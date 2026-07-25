# Loan balance producers: audit, root cause, and the one-fold recommendation

**Status: AUDIT COMPLETE, NOTHING BUILT. Three developer rulings are BLOCKING (Section 7).**
Written 2026-07-14, after C3 shipped to `dev`. Every number below was MEASURED against the running
code -- the dev clone of production, or a throwaway clone of it -- never inferred from reading.

**Read this before you touch a loan balance again.** It supersedes the "next up" section of
`implementation_plan_fail_loud_ledger_authority.md`, whose C3b / C3c are absorbed into Section 6
below.

---

## 0. Resuming: the short version

The fail-loud arc (C1 -> C3, all on `dev`, none on `main`) did what it set out to do. **The
`balance_at` seam is now genuinely single-sourced and correct**: driven through 17 loan shapes, every
seam producer agrees with every other, on every shape (Section 8).

**Four surfaces never joined the seam, and each re-derives a loan's balance its own way.** They are
wrong by up to **$299,701.35**. Two are wrong on YOUR REAL DATA today; one is a time bomb that fires
on the clock with no user action.

The root cause is not any of them individually (Section 3). The recommendation is Section 5. The
order of work is Section 6. **Do not start until the Section 7 rulings are answered.**

### The regression baseline (unchanged by anything in this document)

Re-check these against the dev clone before AND after any commit. If one moves, the commit is wrong,
not the number.

| | |
|---|---|
| Mortgage (account 3) balance today | **$177,277.97** |
| Van Loan (account 8) balance today | **$15,663.59** |
| Year-end 2026 principal paid | **+$2,505.02** / **+$4,251.65** |
| Scalar-vs-map divergences, 2 loans x 59 periods | **0** |

Probe: `scratchpad/probe_baseline.py` pattern -- build a `BalanceContext`, call
`balance_at.balance_at` and `balance_at.balance_map`, compare each period at
`p.start_date if p.start_date <= ctx.as_of else p.end_date` (the map is START-keyed for begun periods
and END-keyed for future ones; the naive period-END form is FALSE on a correct loan).

---

## 1. What shipped: C3 (`fe77744e`, on `dev`)

`fix(balance): fence the context's loan handle; a route cannot hold a LoanState`. Closed holes 2b and
2c of the fail-loud arc. Full AS BUILT is in
`implementation_plan_fail_loud_ledger_authority.md`; the two things worth carrying forward:

* **All three fence holes were probe-verified BEFORE any code was written**, and all three held: a
  route reading `ctx.loan_state(account).current_balance`, a new public `loan_balance_right_now()` in
  `loan_resolution`, and a new public METHOD on `BalanceContext` each rated **10.00/10** against the
  running gate. All three are build failures now, each with a negative control that fires.
* **C3's own review found a CRITICAL defect C3 created**, and it is the lesson of this whole document.
  The first cut collapsed the property chart's "is this loan done" test onto the seam's
  `is_paid_off` as a DRY cleanup. But `is_paid_off` also requires a confirmed payment, so a mortgage
  paid off by a LUMP SUM recorded as a balance true-up reads "not done" -- it got charted, and because
  a zero-balance loan has an EMPTY schedule, the back-projection clip handed the chart its entire
  360-row contractual walk:

  | same page | |
  |---|---|
  | equity hero | `total_debt $0.00`, `equity $400,000.00` |
  | equity chart | **`debt $197,049.32`**, `equity $202,950.68` |

  Fixed: `LoanFigures.is_retired` (originated + ledger balance <= 0) is THE rule for "does this loan
  have a debt line"; `is_paid_off` is now **built on it** rather than sitting beside it. Both guards
  negative-controlled, cleanly partitioned.

  **The DRY instinct was right and the predicate was wrong.** When two rules look like duplicates,
  prove they answer the SAME question before merging them; if they do not, make one BUILD ON the
  other rather than sit beside it.

Suite 7387, `pylint app/` 10.00 zero findings, `pylint tools/pylint/shekel_checkers/` 10.00, checker
unit tests 158. Dev clone unmoved to the cent.

---

## 2. Method

C3 passed every gate and still carried a $197,049.32 defect. So the gates prove nothing here, and the
audit was built to not trust them. Four independent reviewers, each with a distinct lens, all
required to PROVE rather than reason:

| lens | method |
|---|---|
| a rule changed, and a consumer spent the new value wrongly | enumerate every consumer of every value C1b/C2/C2b changed the meaning of; probe each against the dev clone in rolled-back transactions |
| which guard is VACUOUS | for every test the arc added, construct the minimal source mutation it should catch, APPLY it, run the test, revert |
| fail-loud completeness | map every producer that can answer "what does loan A owe at T"; delete a loan's genesis ledger in a transaction and see which surfaces answer instead of raising |
| exotic shapes | clone the dev DB, build 17 loan shapes through PRODUCTION's reconcile path, drive every one through all 8 surfaces |

Plus my own probes for anything I report as fact. **Nothing in this document is asserted from
reading the code alone.**

---

## 3. The root cause, in one sentence

> **"What does this loan owe at time T?" has TEN implementations, and the app's answer has been to
> pick one as authoritative and build a pylint fence to stop the others being called.**

The ten, as they exist on `dev` today:

| # | producer | file |
|---|---|---|
| 1 | `confirmed_loan_balance_at` -- the ledger walk | `loan_posting_service/_reader.py` |
| 2 | `confirmed_loan_balance_map` -- the ledger, batched | `loan_posting_service/_reader.py` |
| 3 | `_replay_from_anchor` / `rate_period_engine.replay_schedule` -- the money-blind schedule replay | `loan_resolver/_periods.py`, `rate_period_engine.py:658` |
| 4 | `forward_balance_at_date` -- the forward projection | `account_projection.py` |
| 5 | `compute_forward_loan_period_balance_map` -- the projection, batched | `account_projection.py` |
| 6 | `loan_owed_at_dates` -- the projection, multi-date | `net_worth_kernel.py` |
| 7 | `AmortizationRow.remaining_balance` -- EVERY schedule row is a balance | `amortization_engine/_projection.py:220` |
| 8 | `accounts.current_anchor_balance` + transaction sums -- the CASH producer | `balance_resolver.py` |
| 9 | `_cumulative_nets_through` -- raw posting sums | `ledger_report_service/_balance_sheet.py:76` |
| 10 | `LoanState.current_balance` -- the resolver's headline | `loan_resolver/_state.py` |

**A fence is a policy, and policies leak.** Every finding in Section 4 is a leak. The seam
(`balance_at`) is correct; the problem is that four surfaces never call it, and the fence cannot see
them because it binds on function NAMES and cannot see an attribute read, a template variable, or a
producer one tier below the one it guards.

---

## 4. Findings register

Severity is by MONEY MOVED on a reachable path. "Live" = wrong on real data today with no
provocation. "Reachable" = a user can produce it through the UI. "Latent" = real, but currently
unreachable.

| id | finding | worst measured | reach |
|---|---|---|---|
| **B-1** | Upcoming mortgage 500s the app on the day it closes | total outage | **clock, no user action** |
| **B-2** | Property equity chart is wrong on 8 of 13 shapes | **$299,701.35** | reachable |
| **B-3** | Grid renders a loan's balance RISING as you pay it | $825.44 today, +$1,910.95/mo | **LIVE** |
| **B-4** | `_forward_rows`'s `is_confirmed` filter has ZERO tests | $47,120.00 | latent (guard gap) |
| **B-5** | Balance sheet renders a NEGATIVE liability, HTTP 200 | -$7,643.80 | latent |
| **B-6** | Taxes tab prints mortgage interest for a loan the seam refuses to value | $4,156.61 | latent |
| **B-7** | Year-end omits a loan paid off by a true-up entirely | the whole event | reachable |
| **B-8** | Fail-loud does not cover a FUTURE valuation | unbounded | latent |
| **B-9** | FU-7: projection pays down overdue installments nobody paid | -$15,755.38 / period | reachable |
| **B-10** | Year-end net worth spends a fabricated `jan1 = 0` | $255,300.26 | **LIVE** |
| **B-11** | FU-4: a period before the ledger's opening renders the loan debt-free | $17,134.85 | **LIVE** |
| **B-12** | The fence has an entire unfenced tier BELOW the one it guards | -- | guard gap |
| **B-13** | Loan detail route answers a broken loan from the money-blind replay | $199,600.80 | latent |
| **B-14** | `loan_recurrence_sync` PERSISTS a payoff date off the blind walk | -- | reachable |
| **B-15** | Kind-blind anchor true-up writes `current_anchor_balance` on a LOAN | -- | reachable |

### B-1 -- CRITICAL. An upcoming mortgage 500s the app on the day it closes.

**No user action. The clock alone fires it.** C2 blessed "a mortgage closing next month" as a
supported state (the developer ruled it must be). C2b made the fail-loud raise unconditional for an
ORIGINATED loan. **Nothing bridges the two.**

The chain, each link verified in source:

* `app/schemas/validation/loans.py:62` -- `origination_date` has NO not-future validator. A future
  origination is legitimate and reachable.
* `app/routes/loan/params.py:125` -- params-create calls `sync_loan_postings_all_scenarios`. It is
  the ONLY writer.
* `app/services/loan_posting_service/_sync.py` -- that sync runs `as_of = date.today()`, **at WRITE
  time**.
* `app/services/loan_posting_service/_walk.py:356` --
  `(anchor for anchor in anchor_facts if anchor.anchor_date <= as_of)`. The future origination anchor
  is **DROPPED**. No OPENING posting is ever written.
* Every `sync_*` call site is a WRITE path (params create/edit, true-up, rate/escrow change, transfer
  settle, `reset_pay_periods`). **There is no clock-driven writer and no read-time heal.**
* `net_worth_kernel.py:509-528` -- the moment `origination_date <= ctx.as_of`, `owed_from > ctx.as_of`
  goes False, the ledger answers `None`, and the seam raises `LoanLedgerNotOpenedError`.

Measured (a $200,000 / 5% / 360mo mortgage, real user, real scenario):

```
STATE A -- configured today, closes in 20 days      (C2's feature, working)
  /savings   OK        year-end   OK

STATE B -- the SAME loan 30 days later; closed 10 days ago; no write in between
  opening posted at config time?  ->  False
  /savings   !! LoanLedgerNotOpenedError
  year-end   !! LoanLedgerNotOpenedError
```

Blast radius: `/savings`, year-end, `/debt-strategy`, the property page, and the grid all 500. The
loan detail page does NOT -- it renders `$200,000.00` out of the money-blind anchor replay (B-13). So
the one page that still works is the one showing a number nobody should trust. The dead window runs
from the origination date until the user happens to trigger any write.

**Why the suite is blind:** `TestLoanNotYetOriginated` makes EVERY assertion with the loan still
un-originated. No test advances the clock past origination for a loan whose ledger was synced before
it. And `tests/_test_helpers.py:424` states the missing invariant as though it were implemented:
*"it simply owes nothing and posts no opening until its origination date arrives."* **Nothing makes
the opening arrive.**

**The fix (recommended, and it is a down payment on Section 5):** the sync writes what is **KNOWN**;
the readers decide what has **HAPPENED**. Post the opening at params-create dated at
`origination_date`, whether or not that date has arrived. The readers are ALREADY as-of bounded
(`_asof.effective_date() <= as_of` in `_reader.py:161`; `bisect_right(boundaries, period.start_date)`
at `:243`), so a future-dated opening is invisible until its date arrives and then appears with NO
further write. That is exactly Section 3a.3's "one fact, two owners, split on the boundary the
architecture already turns on" -- the walk's blanket anchor drop is the only thing preventing it.

It also **DELETES** three special cases that exist only to paper over this: `DebtSchedule.owed_from`,
`account_projection._projected_owed_at`'s guard, and the seam's not-yet-originated fork in
`amortizing_balance_at`.

**Before shipping it:** re-verify the walk's `owed_before` correction arithmetic for a future-dated
opening. That is the one thing that could bite.

### B-2 -- HIGH. The property equity chart is wrong on 8 of 13 loan shapes.

Driven through 17 shapes on a cloned DB. The chart is the ONLY producer that disagrees with the seam,
and it disagrees three different ways -- all one root cause: **it derives its debt line from SCHEDULE
ROWS, not from the ledger.**

| shape | hero (correct, ledger) | chart | gap |
|---|---|---|---|
| mortgage closing in 45 days | $0.00 | $299,701.35 | **$299,701.35** |
| balloon, past maturity, still owing | $80,000.00 | $0.00 | **$80,000.00** |
| true-up after regular payments | $185,000.00 | $179,098.20 | $5,901.80 |
| never-paid, 30 missed installments | $200,000.00 | $193,573.05 | $6,426.95 |
| mid-life import | $180,000.00 | $177,616.96 | $2,383.04 |
| mortgage + HELOC | $250,000.00 | $240,221.41 | $9,778.59 |
| ARM across an adjustment | $170,000.00 | $168,842.47 | $1,157.53 |
| delinquent | $179,700.90 | $178,794.59 | $906.31 |

Three mechanisms:

1. **Empty schedule + positive balance draws the WHOLE contractual walk.** (This is FU-8, which I
   filed during C3 and said needed measuring first. It is measured now: **$80,000**.) The balloon
   shape is not retired (balance > 0) so the chart keeps it -- but `schedule == []` makes
   `tracking_start = None` at `balance_at/_secured_debt.py:164`, and the clip at `:172`
   (`if tracking_start is None or ...`) admits every contractual row as the "estimated" tier.
2. **The axis clamps "Today" onto the wrong month** when the loan's rows do not span today.
   `property_equity_chart.py:401-408` spans only the loan's ROW months and clamps `today_index` into
   that span; its docstring calls the clamp "defensive", and the assumption is false.
3. **A true-up has NO schedule row, so the chart cannot see it** -- and unpaid overdue installments
   are drawn as PAID. This is FU-2. It is the exact failure the genesis-ledger read switch was built
   to kill, still live on this one surface.

**One fix kills all three:** the chart's debt line for any month at or before today must come from
the LEDGER (`confirmed_loan_balance_map`, keyed by `account_id` -- which is why C3 put `account_id`
on `SecuredLoanSeries`), and its axis must span `min(origination, today) .. max(payoff, today)`, not
the row span. That makes "the chart and the hero agree" structural rather than coincidental.

### B-3 -- HIGH, LIVE ON REAL DATA. The grid renders a loan's balance RISING as you pay it.

Measured on the dev clone, no provocation:

| period start | GRID renders | seam / every other surface |
|---|---|---|
| 2026-07-02 | $178,103.41 | $177,277.97 |
| 2026-07-16 | $178,103.41 | $177,277.97 |
| 2026-07-30 | $180,014.36 | $176,999.67 |
| 2026-08-13 | $180,014.36 | $176,999.67 |
| 2026-08-27 | **$181,925.31** | **$176,719.77** |

Rising by the full $1,910.95 PITI every month, diverging without bound.

**Producer:** `balance_at.grid_balance_view` -> `cash_balance_map` -> `balance_resolver` -- the
`accounts.current_anchor_balance` COLUMN plus `budget.transactions` sums. Never the ledger, never the
schedule. `budget.accounts.current_anchor_balance` for account 3 is literally `178103.41`. Payment
transfers INTO the loan account read as inflows, so they ADD.

**Call sites:** `app/routes/grid.py:295,846,980`, `app/services/dashboard_service.py:93,119`,
`app/services/calendar_service.py:689`.

**Reachable:** Settings -> "Default Grid Account" lists EVERY account with no kind filter
(`app/templates/settings/_general.html:26-32`); `resolve_grid_account` honours `?account_id=` for any
owned active account (`app/services/account_resolver.py:78-81`). Setting the Mortgage as the default
grid account also re-points the DASHBOARD HERO at it.

**It goes THROUGH the seam, so W9906 is silent by design.** This is a RULING problem, not a fence
bypass: the seam's cash-flow entry is documented for INTEREST accounts, and for a loan it produces a
number that grows as you pay the loan down. **See ruling R3 (Section 7).**

**B-15 is its write-side twin:** `PATCH /accounts/<id>/true-up` (`app/routes/accounts/anchor.py:153-220`)
has NO account-kind gate. It calls `anchor_service.apply_anchor_true_up` -> `stage_anchor_true_up`
(`anchor_service.py:210-211`), which writes `account.current_anchor_balance` for an AMORTIZING loan --
a second, stored, never-reconciled loan balance, which is the one the grid then renders. The loan's
REAL true-up path is `apply_loan_anchor_true_up` (a `LoanAnchorEvent` + posting sync).

### B-4 -- CRITICAL (guard gap). The `is_confirmed` filter in the forward walk has ZERO tests.

`app/services/account_projection.py:269` -- `_forward_rows`:

```python
return sorted(
    (row for row in schedule if not row.is_confirmed),
    key=lambda row: row.payment_date,
)
```

C2 EXTRACTED this filter into `_forward_rows` and called it "the structural lesson of 2a, applied
pre-emptively". **It refactored a filter that nothing tests.**

**Negative control: delete `if not row.is_confirmed`. Result: 1,369 tests GREEN.** Not one goes red --
`test_balance_at.py` (including `TestScalarAndMapAgree`), the entire cross-page oracle,
`test_net_worth_kernel.py`, `test_savings_dashboard_service.py`, `test_year_end_summary_service.py`,
`test_loan_posting_service.py`, all of `tests/test_integration/`, and the loan / savings / property /
debt-strategy route suites.

**Measured divergence: $47,120.00** (a paid mortgage with 12 confirmed rows and a $50,000 lump-sum
true-up, valued at a future date before the next installment -- which is what the net-worth trend
reads on every render):

```
correct (filter present) : 146,000.00
mutated (filter deleted) : 193,120.00
```

**Why `TestScalarAndMapAgree` can NEVER catch it.** Both forward producers route through
`_forward_rows`. A defect in the SHARED helper moves the scalar and the map IDENTICALLY, so an
agreement invariant is structurally incapable of seeing it. **Two wrong implementations agreeing is
not a proof.** This is the single most important structural lesson in this document, and it is why
Section 5 recommends an independent reference fold as the oracle.

Related: **B-3/F3 -- `TestScalarAndMapAgree`'s matrix contains no loan that has ever been PAID.** The
plan claims 8 shapes; it has 6, and overpaid and short-paid are absent. Proven: make `_forward_rows`
raise on seeing any `is_confirmed` row and the test PASSES. So the arc's flagship guard never
exercises the off-schedule payment path -- the entire reason the ledger read switch exists.

### B-5 -- HIGH (latent). The balance sheet renders a NEGATIVE liability, HTTP 200.

`app/services/ledger_report_service/_balance_sheet.py:46,76` -- `compute_balance_sheet` ->
`_cumulative_nets_through`: raw posting sums, NOT `balance_at`, NOT `confirmed_loan_balance_at`.
Route: `app/routes/analytics.py:461`.

With the ledger intact it agrees with the seam to the cent ($177,277.97). With the OPENING posting
gone it renders **Mortgage = -$7,643.80** -- a NEGATIVE liability -- HTTP 200, `tie_out.in_balance =
True`, on the same request scope where `balance_at.balance_at` RAISES. It reads the ledger, so it
never touches the schedule, but it carries none of the ledger's invariants: "an originated loan must
have an OPENING" is not enforced on this walk.

### B-6 -- HIGH (latent). The Taxes tab prints mortgage interest for a loan the seam refuses to value.

`net_worth_kernel.debt_schedule_rows` (`:253-296`) reads NO ledger and therefore NEVER raises. The
analytics **Taxes tab** (`app/services/tax_report_service.py:645`) and the year-end interest section
both feed it into `_loan_year_interest` (`year_end_summary_service/_income_tax.py:228-240`), whose
`confirmed is None` branch **sums the full schedule**.

On the same broken loan, same day: `/savings` says *"this loan's history cannot be read"* and the
Taxes tab prints **$4,156.61** of deductible mortgage interest -- **a number a user may put on a tax
return.** C2b's rule is "a broken loan fails loud rather than producing a number"; here it produces
one, on the highest-stakes surface in the app.

### B-7 -- MEDIUM. Year-end omits a loan paid off by a true-up ENTIRELY.

`year_end_summary_service/_net_worth.py:228-230` -- `if not schedule_rows: continue`. A paid-off loan
has no schedule rows.

| the same economic event | year-end reports |
|---|---|
| $197k mortgage paid off by a lump-sum TRUE-UP | **row absent** -- the largest debt event of the year, reported as nothing |
| the same payoff recorded as a settled PAYMENT | `principal_paid = $20,000.00`, reported normally |

The membership gate should be "is this a configured, ORIGINATED loan"
(`loan_figures(...) is not None and is_originated`), not "does it have schedule rows". A balloon still
owing $80,000 is also silently absent.

### B-8 -- HIGH (latent). The fail-loud does not cover a FUTURE valuation, and the docstring says it does.

`net_worth_kernel.py:509`:

```python
if as_of > ctx.as_of or debt_schedule.owed_from > ctx.as_of:
    return forward_balance_at_date(...)      # returns BEFORE the ledger is read
```

The `Raises:` contract at `:490-493` promises `LoanLedgerNotOpenedError` "when an ORIGINATED loan has
no OPENING posting". It does not, for any future date -- and the seed it projects from is
`state.current_balance`, which for an unopened loan IS the money-blind anchor replay. Measured on a
broken loan:

```
PAST     balance_at(yesterday)  !! LoanLedgerNotOpenedError
PRESENT  balance_at(today)      !! LoanLedgerNotOpenedError
FUTURE   balance_at(+1 day)     = 200,000.00      <-- NO RAISE
FUTURE   balance_at(+1 year)    = 197,049.32      <-- NO RAISE
         loan_owed_at_dates     = [195,775.75, 185,780.99]   <-- NO RAISE
```

Masked today only because every live consumer asks a today-or-past question first and dies there.
**The safety is a predicate, not a structure** -- the precise shape that cost $197,049.32 in C3.

### B-9 -- MEDIUM (= FU-7, now QUANTIFIED). The projection pays down overdue installments nobody paid.

`account_projection._projected_owed_at` / `forward_balance_at_date` walk ALL unconfirmed rows,
including past-due ones.

| shape | map cliff in ONE pay period | year-end says "principal paid" | actually paid |
|---|---|---|---|
| 41 installments overdue | **-$15,755.38** | **$17,906.63** | **$0.00** |
| 30 overdue | -$6,426.95 | $7,594.73 | $0.00 |
| mid-life import | -$2,383.04 | $5,418.14 | $0.00 |
| recurring, 3 overdue | -$1,205.41 | $2,746.39 | $0.00 |
| delinquent | -$906.31 | $2,746.39 | $1,199.10 |

Lands on the /savings net-worth trend, the Horizon band, and year-end debt progress simultaneously.
**Needs ruling R2 (Section 7) before any code moves.** The naive fix ("start the projection after
`as_of`") was tried and MEASURED: 26 failures, and it silently DROPS a payment the user has planned
and will make. The fix must turn on *is there a payment RECORD behind this installment*, not on the
date.

### B-10 -- MEDIUM, LIVE. Year-end net worth spends a fabricated `jan1 = 0`.

`year_end_summary_service/_net_worth.py:66-70`. C1b taught `_compute_debt_progress` to clamp its
window to the ledger's domain. Its sibling `_compute_net_worth`, 180 lines above IN THE SAME FILE,
does not -- and reads the same per-period map that returns the FALSE pre-opening `$0.00`.

Live on the clone today: with no pay period before 2026-01-01, `jan1_period` is `None`, so
`jan1_nw = ZERO` and the panel reports

```
year-end 2026 NET WORTH:  jan1 = 0   dec31 = 255,300.26   delta = 255,300.26
```

*"Net worth grew $255,300.26 in 2026"* is a fabrication. The debt-progress section BESIDE IT correctly
says "tracked_from 2026-03-31" because C1b taught it to. This one does not.

### B-11 -- MEDIUM, LIVE (= FU-4). A period before the ledger's opening renders the loan debt-free.

```
Van Loan ledger domain: start = 2026-04-09, opening = $17,134.85

per-period map (the GRID and year-end net worth read this):
  #0  2026-03-26..2026-04-08   Van Loan       0.00   <-- FALSE
  #1  2026-04-09..2026-04-22   Van Loan  17,134.85
```

Net worth overstated by **$17,134.85** in period 0. `_build_amortizing_balance_map`'s OWN docstring
(`net_worth_kernel.py:933-939`) names this exact zero -- *"means 'I have no record' and is FALSE for a
mid-life import"* -- and then hands it out. **C1b built `balance_at.loan_ledger_domain` and did not
wire it into the map**, so the producer that emits the false zero now has the fact that identifies it,
one call away, and still emits it.

### B-12 -- HIGH (guard gap). The fence has an entire UNFENCED TIER below the one it guards.

Proven by running pylint with a positive control in the same invocation (the checkers ARE live -- they
fired on the control). They stayed **completely silent (10.00/10)** on a new
`app/routes/probe_consumer.py` that renders a loan's balance via:

| producer | file | why it is a balance-at-T |
|---|---|---|
| `loan_resolver.resolve_loan(...).current_balance` | `loan_resolver/_state.py:129` | the RAW producer underneath the three fenced wrappers |
| `rate_period_engine.replay_schedule(...).balance_as_of` | `rate_period_engine.py:658` | **THIS IS the blind-to-money balance-at-T walk** |
| `compute_payoff_scenarios(...).history_rows[-1].remaining_balance` | `loan_resolver/_payoff.py:337` | PAST balances; already called from `routes/loan/calculators.py:117,515` |
| `amortization_engine.project_forward(...)[-1].remaining_balance` | `amortization_engine/_projection.py:556` | rows carry balances; called from `routes/loan/calculators.py:315` |
| `posting_reads.account_posting_total` / `confirmed_loan_view().balance` | `posting_reads.py:123`, `loan_payment_service.py:467` | hand a raw balance to any caller |

And **W9909 does not scope them**: a new public `loan_balance_right_now()` added to
`loan_resolver/_state.py` rated **10.00/10**, while the identical shape in `net_worth_kernel` fired
W9909. `_FENCED_MODULE_RULINGS` covers nine modules and NOT `loan_resolver`, `rate_period_engine`,
`amortization_engine`, `loan_payment_service`, `posting_reads`, `ledger_report_service`.

**The 2026-07-02 fix fenced the SEEDING layer and left the PURE RESOLVER it wraps wide open.**

### B-13 -- MEDIUM (latent). The loan detail route answers a broken loan from the money-blind replay.

`app/routes/loan/` never uses `BalanceContext` or the seam. It resolves the loan itself
(`_helpers.py:189`, `resolve_loan_seeded`, on its own `date.today()`) and renders
`state.current_balance` -- **7 live reads** across `dashboard.py:259,482,511` and
`calculators.py:198,405,422,432,495`. When the ledger cannot answer, `resolve_loan` falls back to
`_replay_from_anchor`.

Measured (Mortgage, ledger deleted in a rolled-back transaction): the seam RAISES; the loan page
renders **$177,277.97**. Five surfaces fail loud, the sixth -- the most-used loan surface in the app --
fails silent. The number is right today only because the user's true-ups re-anchor the replay:
agreement by luck.

**Also measured: the page already resolves the loan TWICE** -- 2 amortization walks (273 rows each),
2 ledger reads, 2 scenario lookups, 0 `BalanceContext`. And the two walks are the SAME composition:
`resolve_loan` calls `compute_payoff_scenarios` and discards `original_forward`, which
`build_baseline_scenarios` then recomputes with identical inputs. The standing-overpayment figure
behind them is fetched by THREE different code paths.

Two more route-level past-balance surfaces: `_helpers.py:324` (the chart's balance series, past rows
included) and `templates/loan/_schedule.html:71` (the amortization table renders EVERY past row's
balance).

### B-14 -- MEDIUM. `loan_recurrence_sync` PERSISTS a payoff derived from the blind walk.

`app/services/loan_recurrence_sync.py:101` -> `:67` -- `if schedule[-1].remaining_balance > 0` writes
`recurrence_rule.end_date`. Measured on a broken loan: end_date = `2048-12-01`, replay-seeded, no
fail-loud. Fired from every transfer settle / revert / edit / delete / restore and every params /
rate / true-up edit. **A WRITE path persisting a schedule-derived balance.**

### Lower-severity, recorded so they are decisions rather than oversights

* **B-16.** `savings_dashboard_service/_horizon.py:144,551` use `is_paid_off` for the job the seam's
  own contract assigns to `is_retired` ("use `is_retired` to decide whether a loan has a debt line;
  use `is_paid_off` to decide whether to CONGRATULATE"). Safe today only by an incidental second
  guard (`payoff_date > today`, and an empty schedule gives `payoff_date = origination_date`, a past
  date). A safety that is a predicate, not a structure.
* **B-17.** The debt-track `is_originated` wiring is UNGUARDED: set `"is_originated": True` in
  `savings_dashboard_service/_projections.py:239` and **308 tests stay green**. The regression guard
  builds its own dict and calls the private `_compute_principal_paid_fraction` -- it never runs the
  producer that BUILDS that dict in production. C1's own thesis, reproduced inside the arc's own
  guard.
* **B-18.** Pre-anchor periods: the map OMITS them, the scalar FABRICATES. `balance_at(Checking,
  2026-03-26) = $2,640.16` on real data -- today's balance presented as March's. The seam's own
  docstring says flat-carrying cash backward "would fabricate balances the account never had"; the
  scalar does exactly that. `balance_at/_kind_correct.py:276-279`.
* **B-19.** False type hints of exactly the class C2 fixed in the sibling function:
  `year_end_summary_service/_income_tax.py:154,192,236` say `dict[int, DebtSchedule]` / `DebtSchedule`
  where the callers pass ROWS (`for row in debt` -- a `DebtSchedule` is not iterable).
* **B-20.** A loan paid off by true-up shows its ORIGINATION date as its payoff date (empty schedule ->
  `LoanState.payoff_date = origination_date`, `loan_resolver/_state.py:313-314`), and `is_paid_off`
  is False, so a paid-off loan gets no badge.
* **B-21.** `TestBrokenLoanFailsLoud`'s cash-fallback assertion is `is not None` -- it verifies "did
  not raise", not "answered correctly". The account has a $150,000 anchor; it should assert
  `== Decimal("150000.00")`.
* **B-22.** `tests/_test_helpers.py:758` -- `create_loan_account` calls `insert_origination_event`,
  which production explicitly does NOT (`routes/loan/params.py:109`: "NO origination LoanAnchorEvent
  is written"). Proven inert (removing it leaves 249 tests green), so it is dead weight, not a live
  divergence -- but it sits inside the commit whose thesis is "fixtures must write what production
  writes".

---

## 5. The recommendation: one fold

If this were designed from scratch: **a loan's balance is a fold over its event stream, there is
exactly ONE fold, and nobody else may answer.**

Exactly THREE events move a loan's balance:

* **ORIGINATION** -- `+principal`, dated `origination_date`
* **PAYMENT** -- `-(principal component of the ACTUAL cash)`, dated at settle
* **TRUE-UP** -- reset to an asserted balance, dated `anchor_date`

Nothing else. Not rate changes (they alter future amortization, not the balance). Not escrow (not
principal). **Not the schedule** -- a schedule is a PREDICTION, not an event.

```
balance_at(loan, T) = fold(events(loan) where date <= T)

events(loan, T) = actual_events(loan)                        # what happened
                + predicted_events(loan, from=today, to=T)   # what we expect
```

Five consequences, each of which DELETES a class of bug this project has been paying for:

1. **The past/future "seam" disappears.** It was never a seam -- it was two implementations of one
   fold, hand-synchronised. One fold, called at many dates, cannot diverge from itself.
   `TestScalarAndMapAgree` stops being NECESSARY (though see (5)).

2. **A prediction is only emitted for the FUTURE.** An installment that came due and was not paid did
   not happen; the debt is still owed and the loan correctly reads as DELINQUENT. That single rule
   kills defect 2a (unpaid past rows paying the debt down) AND B-9 / FU-7 (the same bug pointed
   forwards) -- they are the same bug, and this model cannot express either. The mid-period case FU-7
   worries about ("my mortgage was due Jul 1, it is Jul 13, I have not clicked mark-paid") falls out
   naturally: a Projected payment RECORD is a planned event, so the projection walks payment
   RECORDS, not schedule rows.

3. **The flags evaporate AS THREADED STATE.** `owed_from`, `is_originated`, `projection_seed`, the
   ledger's "domain", `is_retired` all exist because today's balance function is PARTIAL -- it cannot
   answer before the ledger opens, so callers need flags to know which zeros are real. A TOTAL fold
   has no such zeros. Predicates that remain (`is_retired`) are derived from the ONE stream on demand,
   never COPIED into six objects -- which is exactly how `_LoanAccountResult` went stale the instant
   `is_originated` was added, and how C3 shipped $197,049.32 of phantom debt by picking the wrong copy.

4. **The postings ledger becomes a DERIVED CACHE, not the source of truth.** This is the deep fix for
   B-1. Today the postings ARE the truth, so a sync that did not run means the truth is gone and the
   app must lie or die. Right model: the truth is the rows the user entered (`LoanParams`, settled
   transfers, `LoanAnchorEvent`); the double-entry ledger is a PROJECTION of them, kept for the
   general ledger and the balance sheet. A missing opening then becomes a CACHE INCONSISTENCY --
   detectable (recompute the fold and compare) and repairable (re-sync) -- not data loss. The
   invariant becomes *"the ledger must AGREE with the fold"*, which is a reconciliation this project
   already has oracles for (the Step-2/3/4 reconciliation tests). **The time bomb cannot exist,
   because the fold reads `origination_date` directly.**

5. **Optimized producers must be proven against a DUMB REFERENCE, not against each other.** This is
   where the suite fails hardest, and B-4 is the proof. `TestScalarAndMapAgree` compares two
   OPTIMIZED producers to each other, so a bug in code they SHARE moves both identically and the test
   is structurally blind -- 1,369 tests stayed green on a $47,120 mutation. **Two wrong
   implementations agreeing is not a proof.** The batched readers
   (`confirmed_loan_balance_map`, `loan_owed_at_dates`) are legitimate OPTIMIZATIONS -- 59 periods x N
   loans must not be 59N queries -- but every one must be proven equal to an independent,
   obviously-correct reference fold, over GENERATED loan shapes rather than hand-picked fixtures.

**What this is NOT.** It is not "rewrite the seam". The seam is CORRECT (Section 8). The fold is the
structure that makes Section 4 unrepeatable; the immediate value is that the seam already works and
four surfaces still do not call it.

---

## 6. The recommended arc

Ordered by (blocking-ness, money moved, independence). Each step is independently green and
independently revertable, per this project's convention.

| step | work | why here |
|---|---|---|
| **S0** | **B-1, the time bomb.** Sync writes what is KNOWN; readers decide what HAPPENED. Post the opening dated at `origination_date` regardless of the clock. Deletes `owed_from`, `_projected_owed_at`'s guard, and the seam's not-yet-originated fork. | BLOCKING. A guaranteed outage on legitimate user data, fired by the clock. Stands alone. It is also the fold model applied to one commit. |
| **S1** | **The reference fold, as an ORACLE only.** A dumb, obviously-correct `loan_balance_at(loan, T)`. Not (yet) in production paths. Every existing producer is proven equal to it over GENERATED shapes. | It would have caught B-4 ($47,120), the C3 phantom ($197,049.32), and B-3. Build the net before walking further out on the wire. |
| **S2** | **B-2, the property chart onto the ledger.** Debt line for months <= today from `confirmed_loan_balance_map`; axis spans `min(origination, today) .. max(payoff, today)`. | Kills three findings at once, worth up to $299,701.35. `SecuredLoanSeries.account_id` (added in C3) is already the key it needs. |
| **S3** | **B-3 + B-15, the grid.** Needs ruling R3 first. | LIVE on real data. |
| **S4** | **B-7, B-10, B-11, B-5, B-6.** The four surfaces that read a loan without the seam's invariants: year-end's membership gate, year-end net worth's domain clamp, the map's false pre-opening zero, the balance sheet's OPENING invariant, the Taxes tab. | All the same shape; batch them. B-10 and B-11 are LIVE. |
| **S5** | **B-12, the unfenced tier.** Add `loan_resolver`, `rate_period_engine`, `amortization_engine`, `loan_payment_service`, `posting_reads`, `ledger_report_service` to `_FENCED_MODULE_RULINGS`; fence `replay_schedule` and `resolve_loan`. | It is what lets S2-S4 RECUR. Do it after them, so the rulings are known. |
| **S6** | **B-8, B-14, B-13.** Fail-loud completeness: the future branch, the recurrence-sync writer, the loan detail route. B-13 is the old C3b/C3c, now trivial once there is one balance function. | Latent, and S0-S5 shrink them. |
| **S7** | **B-9 / FU-7.** Needs ruling R2. | A modelling decision with UI consequences, not a balance-seam change. |
| **S8** | **The fold in production.** Collapse the ten producers onto the reference; the fence becomes a cheap backstop rather than the thing holding the line. | The end state. Only sane once S1's oracle exists and S2-S6 have removed the exceptions. |
| **S9** | Lower-severity register: B-16 .. B-22. | Cleanup, each with its own note above. |

**C3b and C3c (from the fail-loud plan) are ABSORBED into S6/S8.** The three-option fork recorded
there (widen the memo allowlist / grow `LoanFigures` / collapse the double-compose first) is moot once
there is ONE balance function for the loan page to call. Do not build it as scoped.

---

## 7. Open developer rulings -- BLOCKING

Nothing in Section 6 should start until these are answered. Each is a design or product call, not a
seam call, and folding a guess into a commit is exactly the failure this arc exists to stop.

**R1 -- Ship S0 (the time bomb) now, as its own commit?** Recommended: YES. It is a guaranteed outage
on a feature the developer explicitly ruled must be supported, it fires on the clock with no user
action, and its fix is a down payment on the target model rather than a band-aid.

**R2 -- Does an OVERDUE, UNPAID installment pay the loan down?** (B-9 / FU-7.) Today: yes (due-basis
forward, cash-basis past). The naive fix was tried and MEASURED: 26 failures, and it silently DROPS a
payment the user has planned and will make, so the payoff date flickers monthly. The proposal to
measure: **an installment BACKED by a payment record (confirmed OR projected) is projected; one with
NO record behind it never happened and never pays the debt down.** This touches the loan detail page,
the payoff date, and the schedule table -- it is a modelling decision with UI consequences.

**R3 -- Should the grid render a loan AT ALL?** (B-3.) The grid is a cash-flow view over transactions;
a loan's balance is not a transaction sum. Three options: (a) the grid refuses to render an amortizing
account (most honest -- the number it wants does not exist in its model); (b) the grid renders the
loan's LEDGER balance in its balance row, breaking its own
`balances[p] - balances[p-1] == subtotals[p].net` invariant; (c) leave it and gate the account picker.
**Recommended: (a)**, plus gating `PATCH /accounts/<id>/true-up` on account kind (B-15) so a loan can
never grow a second, stored, unreconciled balance.

**R4 -- Is the one-fold model (Section 5) the direction?** If yes, S1 is the next build after S0. If
you want it costed first (files touched, migration risk, how the batched readers survive), say so and
I will cost it before writing anything.

---

## 8. Verified CLEAN -- what the arc actually achieved

Recorded so nobody re-audits it, and because it is the reason Section 6 is smaller than Section 4
looks.

* **The `balance_at` seam is genuinely single-sourced.** Across **17 loan shapes** (never-paid,
  mid-life import, payoff-by-true-up, payoff-by-payment, paid SHORT, overpaid, overpaid past zero,
  balloon past maturity, $0.01 principal, not-yet-originated, originating this period,
  mortgage+HELOC, ARM across an adjustment, origination predating period 0, true-up after payments,
  delinquent, recurring with overdue) -- `balance_at`, `balance_map`, the loan route, the /savings
  tile, the debt card, the equity hero, and the Horizon band **all agree, on every shape.** The only
  producer that disagrees is the property equity chart (B-2).
* **Overpayment never drives the ledger negative.** The split caps principal at the remaining balance
  and books the excess as a refund (`loan_posting_service/_walk.py:205-218`). A $6,000 payment on a
  $5,000 loan gives $0.00, not -$1,000.
* **No surface raises on any LEGITIMATE shape.** The only raise is the deliberate fail-loud on the
  broken-ledger state.
* **All five of the arc's named deliverable guards ARE load-bearing** -- every one fired under a
  targeted source mutation, with a clean partition:
  `TestScalarAndMapAgree`, `TestBrokenLoanFailsLoud`, `TestLoanNotYetOriginated`, the cross-page
  oracle, `TestMultiLoanIsolation`. (The vacuity is in what they do NOT reach: B-4, B-17, and the
  missing paid-loan shapes.)
* **C1's thesis was not reintroduced.** Every number the arc's tests assert is produced through
  `create_loan_account`'s write-through ledger -- production's path. The one exception is B-17.
* **C2's three consumer fixes are correct and load-bearing** (`_compute_principal_paid_fraction`, the
  property chart's `is_retired` drop, the year-end `is_originated` skip). Each refuses to read the
  unoriginated `$0.00` as "repaid"; I could not break any of them.
* **C1b's `LEAST(entry_date, period.start)` claim holds.** An anchor's effective date is always a
  period start or a date strictly before the first period -- never strictly inside one. The
  per-period map is structurally immovable.
* **No float-on-money, no ref-name comparison, no IDOR, no ownership regression** in C1 .. C3.
* `_transfer_loan_posting` reconciles from ACTUAL CASH and derives no balance from a schedule.
* `loan_payment_service._resolve_loan_pi` holds an unfenced `LoanState` but reads only
  `.monthly_payment` (contractual, from the rate periods) -- measured identical on an intact, a
  partial, and a destroyed ledger. It persists no balance.

---

## 9. The process lessons, stated so they are not paid for a fourth time

1. **The gates prove nothing.** C3 passed `pylint app/` 10.00 with zero findings and a 7,387-test
   green suite, and carried a $197,049.32 defect. Every one of the four reviews that found the
   findings above ran against a fully green tree.
2. **Two wrong implementations agreeing is not a proof.** B-4 is the canonical instance: a $47,120
   mutation left 1,369 tests green because the "agreement" invariant compared two producers that
   share the broken code. An oracle must be INDEPENDENT and DUMB.
3. **A DRY refactor of a PREDICATE can move money.** C3 merged two "this loan is done" tests and drew
   $197,049.32 of phantom debt. Prove two rules answer the SAME question before merging them; if they
   do not, make one BUILD ON the other rather than sit beside it.
4. **Probe before you design; negative-control every new guard.** Every finding in this document that
   was found by reading was WRONG or incomplete until it was run. And a guard whose negative control
   does not fire is not a guard.
5. **A safety that is a predicate is not a safety.** B-8, B-16, and FU-8 are all "harmless because
   some OTHER check happens to fire first". C3's $197k defect was exactly this, one predicate
   flipped.
