# Implementation plan: the loan balance, designed from scratch

**Status: DESIGN LOCKED. All five blocking rulings answered by the developer (2026-07-14). Nothing
built.** Supersedes the "recommended arc" of `adversarial_review_arc_and_direction_2026-07-14.md`
and the S0-S9 arc of `audit_loan_balance_producers.md` for loans. Prerequisite reading: the
adversarial review's Sections 0, 2 and 4.

**Every fact below was RUN against the dev clone, not read.** Where a number appears, the probe that
produced it is named. No production data was touched; every write probe rolled back and the row
counts were verified identical.

---

## 0. The rulings this plan is built on

| # | Question | RULING |
|---|---|---|
| **D1** | Does an overdue, UNPAID installment reduce the balance? | **No. Payment RECORDS only.** An installment with no record behind it never happened. One with a Projected record behind it is a planned event and projects normally. |
| **D2** | What did a mid-life-imported loan owe before tracking began? | **Contractual back-projection, rendered as an explicit ESTIMATED tier.** Predictions fill gaps in the record -- in both directions -- and never where a record exists or should exist. |
| **D3** | Which cash does a future payment use: PLANNED or CONTRACTUAL? | **PLANNED cash wins.** The same dollar that leaves checking is the dollar that reduces the loan. **Plus** a drift warning when the planned amount stops matching the contract, with a one-click "update the transfer" action. **The drift check treats `extra_principal` as expected** -- a deliberate $100 overpayment must never trip it. |
| **D4** | Should the grid render a loan? | **No.** Refuse an amortizing account; gate the picker AND `PATCH /accounts/<id>/true-up` on account kind. |
| **D5** | When does an event change the balance? | **ONE clock: the real date.** Origination on `origination_date`, an assertion on its `anchor_date`, a payment on its **due date**. No separate "visibility" date. |

**D5 is the deepest of the five and deserves its receipt.** Today a payment is credited on its
**pay-period start**. Your real mortgage:

| pay period starts | payment due | you actually paid |
|---|---|---|
| 2026-05-21 | 2026-06-01 | 2026-06-02 |
| 2026-07-02 | 2026-07-01 | 2026-07-07 |

So the app currently shows your balance dropping on **May 21** for a payment made on **June 2** --
understating your debt for twelve days. That two-date rule (`loan_posting_service/_asof.py`) exists
only because `journal_entries.pay_period_id` is `NOT NULL` (`app/models/journal_entry.py:158-166`)
and an anchor predating every pay period has to be filed *somewhere*. **It is an artifact of the
postings table, not of your finances.** It is also exactly what my own prototype got wrong, at a cost
of $178,103.41 on 22% of days.

**Verified before locking D5:** the one-clock fold reproduces today's balance to the cent --
Mortgage **$177,277.97**, Van Loan **$15,663.59**. **Only the positioning of HISTORY moves**, by up
to 12 days. (`scratchpad/probe_fold.py`.)

---

## 1. The model, in one page

A loan is a stream of **balance events**. There are exactly three kinds, and nothing else moves a
loan's balance.

```
LoanEvent = (effective_date, seq, kind, status, payload)

kind = ORIGINATION   balance := original_principal          # the loan comes into existence
     | ASSERTION     balance := asserted_balance            # a true-up / tracking-start RESETS
     | PAYMENT       balance -= split(cash).principal       # cash is applied

status = ACTUAL      (settled)   -- it happened
       | PLANNED     (projected) -- it is expected
       | ESTIMATED   (synthesized, D2) -- we have no record and are inferring

balance_at(loan, T) = fold(events where effective_date <= T, ordered by (effective_date, seq))
```

**The fold is TOTAL.** It cannot return `None` and cannot raise. Asked about a date before any event
it returns `0.00` -- not as a sentinel, but as the correct fold of an empty prefix. This single
property is what deletes `LoanLedgerNotOpenedError`, `is_originated`, `owed_from`,
`projection_seed`, `loan_ledger_domain`, "the two kinds of zero", and the splice. **Every one of
those exists to manage the partiality of a function that has no business being partial.**

### The rule that encodes D1, with no fork on `today`

A naive reading of D1 needs an `if T > today` branch, which reintroduces a boundary. It does not.
State it as a property of the EVENT instead:

> **A plan cannot have already happened.**
> A PLANNED event's effective date is `max(due_date, ctx.as_of + 1 day)`.

That is the whole of D1, and the fold stays a single unbranched expression:

| shape | events | `balance_at(today)` | `balance_at(today + 30d)` |
|---|---|---|---|
| never-paid loan, 14 installments overdue, **no records** | origination only | full principal -- **delinquent** | full principal (no plan exists) |
| your mortgage: due Jul 1, still Projected, today Jul 14 | a PLANNED payment, clamped to Jul 15 | **excludes it** (you have not paid) | **includes it** (you will) |
| a normal future installment, due Aug 1 | PLANNED at Aug 1 | excludes | includes |

The $10,365.63-in-six-days cliff and its past-facing twin (defect 2a) become **inexpressible**. Not
guarded against -- inexpressible.

### The one clock (D5)

| event | effective_date | seq tie-break |
|---|---|---|
| ORIGINATION | `loan_params.origination_date` | first (it is the earliest anchor by construction) |
| ASSERTION | `loan_anchor_events.anchor_date` | anchors by `created_at`, **after** payments on the same date |
| PAYMENT (actual) | `loan_loaders.loan_payment_due_date(shadow, payment_day)` (`loan_loaders.py:485-547`) | payments by `(pay_period.start_date, id)`, **before** anchors on the same date |
| PAYMENT (planned) | `max(due_date, ctx.as_of + 1d)` | as above |

The payment-before-anchor tie-break is **preserved verbatim** from `_walk.py:360-362`: a payment due
exactly on an anchor's date is walked and then overwritten by that anchor's reset. It is the same
strict `anchor_date < due_date` boundary the resolver already uses, and changing it would move money.

**The rate and the escrow for a payment resolve at that payment's `effective_date`** -- one clock,
everywhere. (Today they resolve at `pay_period.start_date`: `_walk.py:203`, `_walk.py:408-410`.
Verified on real data that this moves nothing today, because no rate or escrow version changes
between any real payment's period start and its due date -- but it must be re-verified as a gate,
not assumed.)

---

## 2. The split -- one function, actual and planned

`_split_one_payment` (`loan_posting_service/_walk.py:150-221`) is already correct and is **reused
verbatim**:

```python
if balance <= 0:                       # loan closed: the whole payment is a refund
    return Split(0, 0, 0, excess=cash)
interest  = round_money(balance * rate_at_effective_date / 12)
escrow    = escrow_at_effective_date
principal = cash - interest - escrow
if principal > balance:                # payoff overpayment -> refund, never phantom principal
    excess, principal = principal - balance, balance
```

**The same function splits an ACTUAL payment and a PLANNED one.** That is the DRY core of this
design, and it is what makes D3 true by construction: the cash the grid shows leaving checking is the
cash the fold splits, so the checking balance and the loan balance can no longer be computed from
different numbers.

Today they are. `loan_payment_service.py:870-873` says it outright:

> *"The checking expense leg moves the checking balance; **the loan income leg does not affect the
> loan balance (that is resolver-derived)**."*

---

## 3. Where the events come from

Every input already exists. **Nothing new is stored.**

| event | source | scenario-scoped? |
|---|---|---|
| ORIGINATION | `budget.loan_params.origination_date` + `original_principal` (`app/models/loan_params.py:77-99`; both are **immutable** -- no update path exists, `schemas/validation/loans.py:69-71`) | **No** -- account-global |
| ASSERTION | `budget.loan_anchor_events` (`user_trueup`, `tracking_start`). **Structurally append-only** -- the ORM raises on update/delete (`app/models/loan_anchor_event.py:167-194`) | **No** |
| PAYMENT (actual) | settled loan-side income shadow `Transaction`s, via `loan_loaders.query_shadow_income`. Cash = `effective_amount` (frozen at settle, `transactions/mutations.py:610-616`, and it **already includes `extra_principal`**) | **Yes** |
| PAYMENT (planned) | Projected loan-side shadows. Cash = **`loan_payment_service.live_loan_transfer_amounts`** (`:840`) -- the app's existing single source for a projected loan payment's cash (P&I + escrow-at-that-date + `extra_principal`) | **Yes** |
| PAYMENT (estimated, beyond the record horizon) | synthesized contractual installments from the rate schedule, until the balance reaches zero | n/a |
| rate at a date | `budget.rate_history` -> `loan_resolver.resolve_periods` | **No** |
| escrow at a date | `budget.escrow_lines` + `escrow_component_versions` -> `escrow_calculator.escrow_monthly_as_of` | **No** |

**The scenario is ONLY a different payment stream over the same loan.** Confirmed: none of
`loan_params`, `loan_anchor_events`, `rate_history`, `escrow_lines` carries a `scenario_id`, and the
dev DB has exactly one scenario. No consumer ever reads a loan balance in a non-baseline scenario
(`resolution_context.py:123-127`). So the signature is `(account_id, scenario_id, as_of)` and the
scenario touches only the payment set.

### The one thing that must be FIXED at the source

**The origination event is in your database and is excluded from the event stream.**

```
Mortgage: params.origination_date = 2018-12-01, original_principal = 202,000.00

budget.loan_anchor_events                    the stream the app actually builds
  2018-12-01  202,000.00  origination        (absent -- dropped)
  2026-03-31  178,375.43  tracking_start     2026-03-31  178,375.43  is_opening=True
  2026-05-22  177,829.83  user_trueup        2026-05-22  177,829.83
```

`loan_loaders._opening_anchor_fact` (`:108-157`) returns *tracking-start, else origination*, and
`load_loan_anchor_facts` (`:187-196`) filters to `user_trueup` + `tracking_start` only. So for a
mid-life import the origination is **never an event**, and every producer that walks the stream
reports your mortgage as **debt-free for the whole of 2018-2026**. That is the true root of finding
B-11, one level below where the audit placed it.

**Fix: ORIGINATION is always an event.** A `tracking_start` is then just an ASSERTION like any other
-- it resets a balance the loan already had -- rather than a magic "opening" that replaces the
origination.

---

## 4. Prediction (D1, D2, D3)

A prediction is **an event we have no record of**. Three regions, one rule:

> **Predictions fill the gaps in the record -- in both directions -- and never where a record exists
> or should exist.**

| region | source | status |
|---|---|---|
| **Before the first record** (mid-life import: 2018-2026) | contractual installments from origination | `ESTIMATED` (D2) |
| **Between records** (a missed payment: no record) | **nothing.** The debt stands. | -- (D1) |
| **Forward, within the record horizon** (to ~2028) | the Projected payment RECORDS, cash from `live_loan_transfer_amounts` | `PLANNED` (D3) |
| **Forward, beyond the record horizon** (2028-2048) | synthesized contractual installments | `ESTIMATED` |

Every `LoanPosition` the fold returns carries the **status of the last event that produced it**, so a
chart can render the estimated tier distinctly (D2) without any consumer re-deriving provenance from
row flags. That kills the property chart's `debt_tier` guesswork and its three B-2 mechanisms
($299,701.35) in one move.

**Sub-decision, made here, flagged for your correction.** For the pre-tracking window the estimate
runs **forward contractually from origination**, and the `tracking_start` ASSERTION then resets to
the truth. Both endpoints are facts ($202,000 at origination; $178,375.43 at tracking start); the
path between is not. So there will be a small visible **step** at the tracking-start date -- the gap
between what the contract predicted and what you actually owed. I consider that step **honest and
worth showing** (it is the accumulated difference between the contract and reality), and the
alternative -- back-projecting from the assertion so the curve meets it smoothly -- silently discards
the origination fact. Say so if you would rather have the smooth curve.

---

## 5. The public API -- three entries

Every one of the ~40 consumers surveyed collapses onto these. (Full census in the working notes; the
shapes are: scalar-at-a-date, per-pay-period map, N future dates, per-month series, schedule rows,
interest-in-year, payoff date, payment/rate/escrow, principal-over-a-window, retired/paid-off.)

```python
# E1 -- THE FOLD, sampled.  Serves SIX of the ten shapes.
positions(account, ctx, dates: list[date]) -> dict[date, LoanPosition]

@dataclass(frozen=True)
class LoanPosition:
    balance: Decimal
    cum_principal: Decimal      # -> principal-paid-in-a-window = a subtraction
    cum_interest: Decimal       # -> interest-in-a-year (TAX) = a subtraction
    status: EventStatus         # ACTUAL | PLANNED | ESTIMATED  (drives the chart tier)

# E2 -- THE PLAN.  A prediction.  Carries NO balance.
plan(account, ctx) -> list[PlannedPayment]
    # (date, cash, principal, interest, escrow, rate, is_extra)
    # payoff_date  = plan[-1].date       (derived, never stored)
    # monthly_payment / current_rate = plan[0]

# E3 -- THE EVENT STREAM.  The fold's input, exposed.
events(account, ctx) -> list[LoanEvent]
    # payment-history table, anchor drift scorecard, first-tracked-payment date
```

**Derived on demand, NEVER copied into a value object:**

```python
is_originated = origination_date <= ctx.as_of
is_retired    = is_originated and positions(today).balance <= 0
is_paid_off   = is_retired and any(ACTUAL payment event)      # is_paid_off BUILDS ON is_retired
```

Copying these is exactly how `_LoanAccountResult` went stale and how C3 drew **$197,049.32** of
phantom debt by picking the wrong copy. The rule from that failure holds: *when two predicates look
like duplicates, prove they answer the same question before merging them; if they do not, make one
BUILD ON the other rather than sit beside it.*

**A schedule row's balance is not an attribute.** `AmortizationRow.remaining_balance`
(`amortization_engine/_projection.py:220`) is the single largest leak in the current design -- it is
what makes the seam's own `SecuredLoanSeries` claim "it carries no balance" while carrying one, and
two of its four read sites are Jinja templates no linter can see. The amortization table
(`templates/loan/_schedule.html:71`, the ONLY template rendering it) instead gets rows **plus
`positions(row dates)`** -- so the table's balances and the page's hero balance come from one source
by construction.

**Escrow is not in this API.** It is an `EscrowLine` read, independent of the balance. Keep it that
way.

---

## 6. The posting ledger's new role

**The ledger stays.** Nothing here deletes it. What changes is *who answers "what do I owe."*

```
                   +--> balance_at / positions()      (the FOLD -- authoritative for the balance)
events(loan) ------+
                   +--> postings                       (the persisted PROJECTION -- the GENERAL LEDGER)
                            |
                            +--> balance sheet, income statement, statements, attribution
```

The postings continue to serve everything they serve today except the balance:
`ledger_report_service` (balance sheet + income statement), `_attribution`, the per-loan
`loan_interest` / `loan_escrow` Expense ledgers, `archive_helpers.account_has_ledger_postings`
(Guard 5), and the period-lock predicate.

**The invariant flips from an assumption to a check:**

> **`sum(postings) == fold(ACTUAL events)`, asserted at WRITE time.**

Today a missing posting is *data loss* and the app must lie (B-5's negative liability) or die
(B-1's 500). Under this model it is a **cache inconsistency**: detectable (recompute and compare) and
repairable (re-sync). The fail-loud raise then has nothing to fire on, and it moves from the READ
path -- where it 500s your net-worth page -- to the WRITE path, where it is actionable.

### The write path loses its clock

`_sync.py:139` reads `date.today()` and `_walk.py:357` drops anchors after it, so **what is persisted
depends on when the sync ran.** Measured on your real Mortgage (read-only): an early clock
reclassifies **$7,643.80** of real cash as a Refund Receivable asset and erases the entire
Schedule-A interest figure.

The correct rule is **already written in the same file, 130 lines away**, for payments:

> *"posting early changes when the fact is RECORDED, never when it is SHOWN"* (`_walk.py:237-252`)

and `account_posting_service.walk_account_ledger` (`_walk.py:474`) **takes no `as_of` at all**. The
loan anchor path is the only holdout in the entire app. **Delete `as_of` from the loan walk.** The
persisted ledger becomes a pure function of your data.

---

## 7. Structural enforcement -- retiring the fence

The pylint fence is **2,136 lines of apparatus around 53 lines of matching logic**, with 100
allowlist entries and 6 module lists to hand-sync. It has been breached identically three times,
because **a linter can stop a balance being *called*; most of the leaks are *read*** off a DTO field,
an ORM column, or a Jinja variable.

Replace policy with structure, in this order:

1. **The fold is the only public balance producer for a loan.** Everything else becomes private to
   `app/services/loan_ledger/`. Python's import boundary then enforces what the allowlists police.
2. **`AmortizationRow` loses `remaining_balance`.** A prediction is not a balance.
3. **Distinct TYPES for the two legitimate balance notions** -- the *cash-flow* balance (a transaction
   running sum: grid, obligations) and the *kind-correct* balance (net worth). Both are bare `Decimal`
   today, which is precisely why the grid can render a loan's payment-sum as its balance (**D4**,
   live: your mortgage renders **rising** $1,910.95/month, reaching **$222,055.26** by 2028 against a
   true $170,456.89). A type makes that a category error instead of a $51,598.37 bug.
4. Keep a **thin** W9906 afterwards as a smoke alarm. It is not a fire door.

W9905 (`shekel-original-principal-as-balance`) **deletes entirely** -- the seed argument it polices
ceases to exist.

---

## 8. The build order

Each commit is independently green (full suite + `pylint app/` 10.00 with the full `--fail-on` set)
and independently revertable. **The regression baseline must not move:** Mortgage **$177,277.97**,
Van Loan **$15,663.59**. If one moves, the commit is wrong, not the number.

### Phase A -- stop the bleeding, and build the net (NO model change)

| # | commit | why first |
|---|---|---|
| **A1** | `fix(accounts): a loan is not a cash account` -- gate `PATCH /accounts/<id>/true-up` on account kind, and gate the grid picker + `?account_id=` on cash kinds (**D4**). | **LIVE.** I drove `PATCH /accounts/3/true-up` and set your Mortgage's stored anchor to **$1.00** with an HTTP 200. `GET /grid?account_id=3` renders a rising mortgage today. Neither touches the seam. |
| **A2** | `test(loan): the shape matrix must contain a loan that was PAID` | **The suite's only structural blind spot.** No balance fixture has a settled payment, so every `is_confirmed` branch is a no-op in every test. Deleting the forward filter leaves **3,891 tests green** ($4,449.72); double-counting the mortgage-interest **tax deduction** leaves **5,741 green** ($7,181.97, $1,580.03 of phantom tax savings). **One fixture reds both.** Do this BEFORE any balance code moves, so the next commit lands on a net that works. |
| **A3** | `fix(loan): the ledger records what is KNOWN; the readers decide what has HAPPENED` -- delete `as_of` from the loan write walk. Also close **G1** (`grid.create_baseline` resyncs account anchors but not loans, so a loan configured with no baseline posts nothing). | Kills B-1 (the clock-fired outage), the $7,643.80 split corruption, and B-5's mechanism. Not a design decision -- it applies the rule the file already states. |

### Phase B -- the fold, as an ORACLE only

| # | commit | contents |
|---|---|---|
| **B1** | `feat(loan): the loan ledger -- one fold over one event stream` | New package `app/services/loan_ledger/`: `_events.py` (build the stream), `_split.py` (re-export `_split_one_payment`), `_fold.py` (`positions`), `_plan.py` (`plan`). **Not wired into any production path.** Memoized on `BalanceContext` (which already has the memo, `resolution_context.py:176-180`). |
| **B2** | `test(loan): the reference fold is the oracle, and it is exhaustive` | Parallel-run the fold against the current seam **on EVERY DAY** of every loan's domain, over **generated shapes** plus real data. **Sampling is forbidden**: my own prototype passed a 14-day sample with a perfect score while being wrong by **$178,103.41 on 26 of 117 days.** Divergences at this stage are EXPECTED (the clock change, D5) and must each be explained and signed off, not silenced. |

**B2 is the gate for everything after it.** Nothing in Phase C ships until the fold is proven.

### Phase C -- the cutover

| # | commit | deletes |
|---|---|---|
| **C1** | `fix(loan): a loan's origination is an event, not a footnote` -- ORIGINATION always enters the stream (Section 3). | the "opening anchor" concept; B-11's root |
| **C2** | `fix(loan): one clock -- an event happens on the date it happened` (**D5**) | `loan_posting_service/_asof.py` entirely. **History repositions by up to 12 days. Today's balance is unchanged (verified).** Re-run B2 and sign off every moved number. |
| **C3** | `refactor(balance): the seam's AMORTIZING dispatch is the fold` | `_build_amortizing_balance_map`, `amortizing_balance_at`, `loan_owed_at_dates`, `generate_debt_schedules`, `DebtSchedule`, `_projection_seed`, `owed_from`, `_loan_ledger_not_opened`, `LoanLedgerNotOpenedError`, `loan_ledger_domain`, `_domain.py`, `splice_confirmed_and_projected_loan_balances`, and **both** forward producers |
| **C4** | `fix(loan): the loan page reads the seam like everyone else` | the route's private `resolve_loan_seeded` + its double-compose (2 amortization walks, 2 ledger reads per load); **`LoanState.current_balance`** and its 7 route reads |
| **C5** | `fix(accounts): the equity chart's debt line is the fold` | the chart's `row.remaining_balance` reads, the back-projection clip, the mis-clamped axis. Kills **B-2** ($299,701.35). Axis spans `min(origination, today) .. max(payoff, today)`. |
| **C6** | `feat(loan): a plan is payment RECORDS, not schedule rows` (**D1**) | `_forward_rows`, `_projected_owed_at`, `balance_from_schedule_at_date`, `AmortizationRow.remaining_balance` |
| **C7** | `feat(loan): the payment you plan is the payment the loan gets` (**D3**) | the drift warning + the one-click "match the contract" action. **Live today: your transfer says $1,910.95; the loan requires $1,293.96 + $617.33 = $1,911.29.** The escrow changed 2026-07-06 and the transfer never followed. **The drift check compares against `contractual PITI + extra_principal`, so a deliberate overpayment never trips it.** |
| **C8** | `fix(loan): the payoff date is derived, never persisted from a schedule` | `loan_recurrence_sync`'s `schedule[-1].remaining_balance` read (B-14); B-20 (a true-up-paid-off loan showing its origination as its payoff date) |

### Phase D -- structure replaces policy

| # | commit |
|---|---|
| **D1** | `refactor(balance): the engine cluster is private to the seam` -- move `net_worth_kernel` / `balance_resolver` / `account_projection` / `net_worth_investment` / `daily_balance_series` inside the seam package and underscore them. **~60 of the 100 allowlist entries delete themselves.** |
| **D2** | `feat(balance): a cash-flow balance and a net-worth balance are different types` |
| **D3** | `refactor(pylint): retire W9905; shrink the balance fence to a smoke alarm` |

### Phase E -- the ledger becomes a checked projection

| # | commit |
|---|---|
| **E1** | `feat(loan): the postings must AGREE with the fold` -- generate postings from the event stream; assert `sum(postings) == fold(ACTUAL)` at write time; promote the existing reconciliation oracle to a runtime invariant. |

---

## 9. Test strategy -- the part that actually failed

The suite is **stronger than the audit claims**: all five named guards bite under mutation, and seven
other money paths I probed were caught. It has **one** structural hole, and it explains every vacuous
guard in both registers:

> **No balance fixture contains a loan that was ever PAID.**

`test_every_loan_shape` builds six shapes and claims "every loan shape the app can produce"; not one
has a settled payment (even "paid-off" is a $0 true-up). So every `is_confirmed` branch is a no-op in
every test. `_forward_rows` sits at **100% line coverage and is completely untested.**

Four rules for this rebuild:

1. **The fixture matrix must contain the shape the feature exists for.** (A2.)
2. **Exhaustive, not sampled.** Probe **every day** of a loan's domain, not every 14th. This is not
   paranoia: it is the only reason I caught my own $178,103.41 error.
3. **The oracle must be INDEPENDENT and DUMB.** Two optimized producers agreeing is not a proof --
   they share code, so a bug moves both identically. The fold is the reference; the batched sampler is
   the optimization; prove them equal over **generated** shapes.
4. **Negative-control every guard.** A guard whose negative control does not fire is not a guard.

Guards this plan must deliver: the day-by-day fold-vs-seam oracle (B2); the paid-loan shape (A2); a
`positions()`-vs-`postings` reconciliation (E1); the D1 delinquency guard (an overdue installment with
no record does not reduce the balance); the D3 drift guard (including the `extra_principal`
exemption).

---

## 10. What this deletes

The proof that it is simpler, not merely different.

**Concepts:** the past/future splice · the projection seed · `owed_from` · `is_originated` as
threaded state · the ledger "domain" · "the two kinds of zero" · fail-loud-on-read · the two-clock
(sequence vs visibility) rule · "the opening anchor" · a schedule row as a balance.

**Code:** `_build_amortizing_balance_map` · `amortizing_balance_at` · `loan_owed_at_dates` ·
`generate_debt_schedules` · `DebtSchedule` · `_projection_seed` · `_loan_ledger_not_opened` ·
`LoanLedgerNotOpenedError` · `confirmed_loan_ledger_domain` · `_domain.py` · `_asof.py` ·
`splice_confirmed_and_projected_loan_balances` · `forward_balance_at_date` ·
`compute_forward_loan_period_balance_map` · `_forward_rows` · `_projected_owed_at` ·
`balance_from_schedule_at_date` · `LoanState.current_balance` · `AmortizationRow.remaining_balance` ·
the W9905 checker · ~60 fence allowlist entries.

**And it is FASTER.** Measured, same work (2 loans x 59 periods), dev clone, mean of 20 runs:

```
CURRENT seam  build_maps:   41.7 ms
FOLD          walk + answer all 59 periods:    6.3 ms      -- 6.6x faster
```

The complexity was not buying performance either.

---

## 11. Risks, and what I have NOT proven

Stated plainly, because a false claim of certainty is the failure this whole arc is about.

* **The fold is a prototype, not a migration.** It matches the seam on every day of both real loans
  (111 days, 0 mismatches) and reproduces the baseline to the cent. I have **not** driven it through
  the 17 exotic shapes. **B2 exists to do that, exhaustively, before anything switches.**
* **D5 moves historical numbers.** By design, and by up to 12 days of positioning. Today's balance is
  verified unchanged. **Every moved number in C2 must be explained and signed off individually** --
  that is the standard of proof this project already operates under, and it is the one place this plan
  could quietly break something.
* **Rate/escrow resolution moves from period-start to due-date** (Section 1). Verified to move nothing
  on real data today, but that is a property of your current data, not a guarantee. Gate it.
* **Escrow has NO posting reconcile at all** -- seven escrow routes write and none re-syncs
  (`escrow_rates.py`, W7-W13). Correctness rests entirely on a forward-boundary guard computed **in the
  baseline scenario only**. It holds today. It is a latent hazard the fold does not fix, and it should
  be closed in Phase E.
* **`reset_pay_periods` CASCADE-deletes the genesis journal entries** (`journal_entries.pay_period_id`
  is `ON DELETE CASCADE`). Harmless once postings are a derived projection -- they get rebuilt -- but
  it is load-bearing today and must not be forgotten.
* **The Van Loan's history is known-wrong** (FU-1: duplicate same-day anchors, $114.38 and $897.16
  apart). This plan does not fix it and must not silently change it.
* **Performance is measured on 2 loans.** The Horizon band needs the fold to be total over ~1985-2100
  and cheap for ~360 monthly samples per loan. The step-function makes sampling O(log n) per date, but
  I have not benchmarked a full `/savings` render.

---

## 12. Correction to the 2026-07-14 adversarial review

**I got a severity wrong, and the record must say so.**

`compute_year_end_summary` has **zero non-test callers**, and `/analytics/year-end` 302s to
`/analytics` (`app/routes/analytics.py:347-362`). The year-end summary service is **dead code on
`dev`** -- only its `_income_tax` module survives, re-used by the live Taxes tab.

Therefore:

* **B-10** ("year-end net worth spends a fabricated `jan1 = 0`", $255,300.26) is **NOT live.** My
  review called it LIVE. It is real, and it is in unreachable code.
* **B-7** (year-end omits a loan paid off by a true-up) -- same.
* **B-11** (the false pre-opening zero, $17,134.85) **IS still live** -- but via `/savings`
  (`build_maps` -> the per-period map), **not** via year-end.
* **B-6** (the Taxes tab printing mortgage interest for a loan the seam refuses to value) **stands** --
  the Taxes tab is live.

This does not change the plan (the fold deletes all four regardless), but it does change the
priority: **do not spend a commit on the year-end surface until you decide whether to revive or
delete it.**

---

## 13. The one sentence

> **A loan's balance is a fold over three kinds of event; the fold is total, it is already written,
> and everything this project has built to work around its absence -- the splice, the seeds, the
> flags, the two zeros, the fail-loud, and the hundred-entry fence -- deletes.**
