# The balance architecture: the plan of record

**This is the ONLY live document for the balance arc.** Everything else that ever governed this
work is in `archive/` (read-only history, indexed by `archive/README.md`). The rules for this
document are at the bottom (Section 9); the short version: amendments are edits HERE, a shipped
step gets its checkbox ticked with its commit hash HERE, and no new planning documents get
written for this arc.

**State as of 2026-07-16:** design verified and locked; ALL rulings answered (D1-D5 and
R-A..R-D, Section 4); **nothing in Section 5 is built yet**. Next commit: **A1**.

---

## 1. The problem, in plain words

The app answers "what is this account's balance at time T?" in many places, many ways, and the
ways disagree. On real data, today: the grid renders the Mortgage RISING by the full payment
each month while every other page shows it falling; the checking projection silently drops every
transaction you settle after your last balance assertion (so you re-assert the balance ~3 times
a week to force it back -- 44 times in under four months); and a loan configured before its
closing date will take five pages down with a 500 the day it closes, fired by the clock alone.

Underneath every one of those is the same three-part root cause:

1. **The honest balance function is PARTIAL.** `confirmed_loan_balance_at` answers `None` when
   the posting ledger has no opening and raises for future dates. A partial function cannot be
   the single source, so every caller composes it with something else -- a projection, a seed, a
   flag, a fallback -- and every composition is a new producer that can disagree with the others.
   All the arc's invented machinery (`is_originated`, `owed_from`, `projection_seed`, the
   past/future splice, "the two kinds of zero", `LoanLedgerNotOpenedError`, the 95-entry pylint
   fence) exists to manage that partiality.
2. **A derived cache is treated as the source of truth.** The posting ledger is a projection of
   facts you already store (loan params, anchor events, settled payments), but reads treat it as
   the truth, so a sync that did not run becomes data loss the app must lie about or die over.
   Worse, what the sync persists depends on the wall clock at the moment it ran
   (`_sync.py:139` -> the anchor drop in `_walk.py:356-358`).
3. **"A balance" has no type.** It is a bare `Decimal` anyone can compute, copy into a DTO,
   store in a column, or render in Jinja. The fence can only see calls; most leaks are reads
   (`LoanState.current_balance`, `AmortizationRow.remaining_balance`,
   `accounts.current_anchor_balance` -- the last is read in 15 modules and rendered raw in 3
   templates).

The cash side is the same disease one layer worse: the anchor (a user assertion) is the truth,
the projection adds only Projected items and DROPS settled ones, and the daily-series producer
disagrees with the scalar on most days of every period.

## 2. What is already shipped and correct (the foundation this plan builds on)

All commit references for the arc live in this table so nothing else has to be consulted.

| shipped | where | reference |
|---|---|---|
| `balance_at` seam (one read surface, kind-correct dispatch) | prod | PR #45, 2026-06-27 |
| INTEREST-kind grid balance | prod | PR #47, 2026-06-28 |
| Double-entry posting ledger: transfers (Step 2), cash/envelopes (Step 3), loan REAL-split postings (Step 4) | prod | PR #48 2026-06-28; 2026-06-29; PR #51 2026-07-01 |
| Loan read switch (past reads ledger-authoritative) | prod | PR #52, 2026-07-02 |
| Actuals reporting (Step 5) | dev->main | PR #58 |
| `BalanceContext` (one resolution per loan per read pass) + `is_paid_off` off the ledger | dev | `b61aee9c`, `866e30b0`, `84c6e066`, `7b7c909b` (2026-07-13) |
| Fail-loud arc C1/C1b/C2/C2b/C3 (fixtures write through production's path; origination modeled; broken loan raises; context handle fenced) | dev | `2a88456c`, `603aea73`, `def3c8ff`, `9ea61f8a`, `fe77744e` |

**Verified, twice, independently:** the seam's loan answers are correct. A read-time fold over
source events (anchors + settled payments, the reader's visibility rule) matches the seam on
**every day of both real loans' history -- 212 days, zero mismatches** -- and reproduces the
developer-confirmed baseline to the cent (2026-07-14 review, re-verified from scratch
2026-07-16). The problem is everything around the seam, and the machinery keeping it correct.

**Baseline (as of 2026-07-16):** Mortgage (account 3) **$177,277.97**, Van Loan (account 8)
**$15,663.59**. Re-derive from the seam before and after every arc commit; if a commit moves
either, the commit is wrong. Do not trust prose figures older than their write date -- pin
oracles in tests.

## 3. The solution

A loan's balance is a fold over its event stream. The fold is TOTAL: it cannot return `None`
and cannot raise; asked about a date before any event it returns `0.00` as the correct fold of
an empty prefix. That single property deletes the partiality and everything built to manage it.

```text
LoanEvent = (effective_date, seq, kind, status, payload)

kind   = ORIGINATION  balance := original_principal     (loan_params -- immutable)
       | ASSERTION    balance := asserted_balance       (loan_anchor_events -- append-only)
       | PAYMENT      balance -= split(cash).principal  (settled/projected transfer shadows)

status = ACTUAL (settled) | PLANNED (projected; effective = max(due, as_of + 1d) --
         "a plan cannot have already happened") | ESTIMATED (synthesized, no record)

balance_at(loan, T) = fold(events where effective_date <= T, ordered by (effective_date, seq))
```

* **One split function** (`_split_one_payment`, reused verbatim) divides ACTUAL and PLANNED cash
  alike; the cash the grid shows leaving checking is the cash the loan folds.
* **Predictions fill the gaps in the record -- in both directions -- and never where a record
  exists**: contractual back-projection before the first record (ESTIMATED tier, visually
  distinct); payment RECORDS within the materialized horizon (PLANNED); contractual synthesis
  beyond it (ESTIMATED). An installment with NO record behind it never happened and never pays
  the debt down (delinquency reads honestly).
* **Public API, three entries**: `positions(account, ctx, dates) -> {date: LoanPosition}`
  (balance + cum_principal + cum_interest + status -- serves the scalar, the map, series,
  interest-in-year, principal-in-window); `plan(account, ctx) -> [PlannedPayment]` (carries NO
  balance; payoff date = `plan[-1].date`, derived, never stored); `events(account, ctx)`.
  `is_originated` / `is_retired` / `is_paid_off` are derived on demand, never copied into DTOs.
* **The posting ledger STAYS** -- as the general ledger (balance sheet, statements,
  attribution), not as the answer to "what do I owe." It becomes a checked projection:
  `sum(postings) == fold(ACTUAL events)`, asserted at WRITE time. A missing posting becomes a
  detectable, repairable cache inconsistency instead of an outage. The write walk loses its
  clock (the rule "posting early changes when the fact is RECORDED, never when it is SHOWN" is
  already written in `_walk.py:243-252` and already implemented on the cash side).
* **Structure replaces the fence**: the engine cluster goes private inside the seam package;
  `AmortizationRow.remaining_balance` and `LoanState.current_balance` are deleted; cash-flow
  and net-worth balances get distinct types; W9905 deletes; W9906 shrinks to a smoke alarm.
* **Cash is the same fold** (assertion events + transaction events), built AFTER the loan proves
  the machinery, because cash is the incomplete-data case: the anchor legitimately survives as a
  periodic reset (a bank-statement assertion). The instant-partition rule it needs (settle
  instant vs assertion instant) already exists in `account_posting_service/_walk.py` -- the
  projection engine is the one holdout that ignores it.

**Why not the minimal alternative** (just fix the write clock and keep the splice): it shares
steps A1-A3/C1 but keeps the partial function, the splice, the flags, and the fence -- the
measured defect generator (42 commits in three weeks, a new defect found in every commit's
review, all gates green throughout). The fold deletes the generator; the minimal fix feeds it.

## 4. Decisions

### Locked (developer rulings, 2026-07-14; re-examined and ratified 2026-07-16)

| # | ruling |
|---|---|
| D1 | An overdue installment with NO payment record pays nothing down. One with a Projected record projects normally, clamped to `max(due, as_of + 1d)`. Accepted simplification: a delinquent balance holds flat (no penalty interest) -- same as today. |
| D2 | Pre-tracking history is contractual back-projection, rendered as an explicit ESTIMATED tier. The visible step where the estimate meets the tracking-start assertion is honest; keep it. |
| D3 | A future payment uses PLANNED cash (the transfer's amount), plus a drift warning vs contractual PITI + `extra_principal` with a one-click "update the transfer" action. A deliberate overpayment never trips it. |
| D4 | The grid refuses amortizing accounts (picker + `?account_id=` + `PATCH /accounts/<id>/true-up` all gated on kind). A loan's balance is not a transaction sum. |
| D5 | ONE clock: origination on `origination_date`, an assertion on `anchor_date`, an ACTUAL payment VISIBLE on its settled date (R-A below). Ties: payments before anchors on the same date; anchors by `created_at`. The split inputs (ordering, rate, escrow) key on the DUE date -- contract time -- so out-of-order or late settlement can never re-split an installment; verified to move nothing on current data (period-start -> due-date windows contain no rate/escrow version change); gate it anyway. |

### Answered (developer ruling, 2026-07-16: all four as recommended)

| # | ruling | consumed by |
|---|---|---|
| **R-A** | An ACTUAL payment's balance event is VISIBLE on its **settled date** (`paid_at` civil date; due date is the fallback when `paid_at` is NULL). The split math stays keyed to the DUE date -- ordering, rate, AND escrow -- so out-of-order or late settlement can never reorder installments or re-split one (concretely: the July payment settled 2026-07-07, one day after the 07-06 escrow change; due-date keying keeps its escrow $616.99, where settled-date keying would move the split by $0.34 and the baseline with it). Rejected: due-date visibility, which double-counts ~$1,911 in net worth for the days between due and settle every month (real case: due 07-01, settled 07-07). The cash ledger already dates cash by `paid_at`, so loan and checking now move together. | C2 |
| **R-B** | The cash projection counts a settled transaction iff `COALESCE(paid_at, period start)` is after the latest anchor's `created_at` -- SHARED with the posting walk's existing rule, never copied. The archived X0 "post-anchor period" rule is dead: it double-counts on 15 measured real-data pairs. | X1 |
| **R-C** | The transfer write boundary REJECTS a loan payment dated before the loan's origination (root-cause fix; the measured case was $1,200 leaving checking with nothing recording it against the loan). Not modeled as a prepayment; not left documented. | C9 |
| **R-D** | The year-end summary service and its tests are DELETED (dead code carrying B-7/B-10; `/analytics/year-end` already 302s). Rebuild on `positions()` later if ever wanted. `_income_tax` survives -- the live Taxes tab uses it. | F2 |

## 5. The steps

Each commit is independently green (full suite + `pylint app/` with the full `--fail-on` set)
and independently revertable. Tick the box with the commit hash when it ships. Detail beyond
what is written here is decided in the commit itself, not in a new document.

### Phase A -- stop the bleeding, build the net (no model change)

- [ ] **A1** `fix(accounts): a loan is not a cash account` -- gate `PATCH /accounts/<id>/true-up`
  on account kind (it set the real Mortgage's stored anchor to $1.00 with HTTP 200; both real
  loans already carry stray cash-anchor rows) and gate the grid picker + `?account_id=` on cash
  kinds. Kills the LIVE grid defect (Mortgage rendered rising to $181,925.31 by 2026-08-27).
- [ ] **A2** `test(loan): the shape matrix must contain a PAID loan, asserted on the forward
  tail` -- add settled-payments-plus-later-true-up to `test_every_loan_shape`, with assertions
  on FUTURE periods (the forward producers). This is what makes the `_forward_rows`
  `is_confirmed` filter's deletion visible ($4,449.72 measured; currently 0 discriminating
  tests). While in the file: fix the class docstring's "every loan shape" overclaim, and add one
  negative control for the `is_confirmed` / `settled_due_months` guard pair in
  `_loan_year_interest` (currently redundant-by-overlap; the tax number itself is guarded).
- [ ] **A3** `fix(loan): the ledger records what is KNOWN; the readers decide what has
  HAPPENED` -- delete `as_of` from the loan write walk; post every anchor; the readers already
  bound by visibility. Kills the clock-fired outage (B-1), the $7,643.80 split corruption, and
  the negative-liability mechanism (B-5). Also close G1 (`grid.create_baseline` resyncs account
  anchors but not loans). NOTE in the commit: a second write-path clock read remains by design
  -- the settle-time freeze resolves P&I as of today (`loan_payment_service.py:762`); D3's drift
  warning is what surfaces it (C7).

### Phase B -- the fold, as an oracle only

- [ ] **B1** `feat(loan): the loan ledger -- one fold over one event stream` -- new package
  `app/services/loan_ledger/` (`_events`, `_split` re-exporting `_split_one_payment`, `_fold`,
  `_plan`), memoized on `BalanceContext`. Not wired into any production path. Two working
  prototypes existed in review scratchpads (2026-07-14 and 2026-07-16, both matched the seam on
  every day); if lost, the assembly recipe is: `load_loan_anchor_facts` + `_settled_income_shadows`
  + `resolve_periods` + `escrow_monthly_as_of` + `_split_one_payment`, visibility per
  `_asof.effective_date()`.
- [ ] **B2** `test(loan): the reference fold is the oracle, and it is exhaustive` --
  parallel-run fold vs seam on **EVERY DAY** of every loan's domain, over generated shapes
  (including A2's paid shapes) plus real data. **Sampling is forbidden**: a 14-day sample once
  scored perfect while wrong by $178,103.41 on 22% of days. Every divergence is explained and
  signed off, never silenced. **B2 gates all of Phase C.**

### Phase C -- the cutover (order is load-bearing)

- [ ] **C1** `fix(loan): a loan's origination is an event, not a footnote` -- ORIGINATION always
  enters the stream (the row is in the database and is excluded today, `loan_loaders.py:187-196`);
  a tracking-start becomes an ordinary ASSERTION. Kills the false pre-opening zero (B-11, live
  via /savings). Cleanup: the dead `insert_origination_event` test helper.
- [ ] **C2** `fix(loan): one clock -- an event happens on the date it happened` -- R-A ruled
  2026-07-16: settled-date visibility, due-date split keying. **MUST land after C1**
  (probe-proven: one-clock without the origination event reads $0 for 6 days x $178k at the
  Mortgage's tracking boundary). Deletes `_asof.py`. History repositions within bounded windows
  (verified: 26 days Mortgage / 13 days Van Loan, today's balance unchanged); every moved
  number is signed off via B2.
- [ ] **C3** `refactor(balance): the seam's AMORTIZING dispatch is the fold` -- deletes the
  splice, `_projection_seed`, `owed_from`, `loan_ledger_domain`, `LoanLedgerNotOpenedError`,
  both forward producers, `generate_debt_schedules`/`DebtSchedule`, and the two-zeros doctrine.
- [ ] **C4** `fix(loan): the loan page reads the seam like everyone else` -- deletes the route's
  private double-compose and `LoanState.current_balance` (7 route reads).
- [ ] **C5** `fix(accounts): the equity chart's debt line is the fold` -- kills B-2
  ($299,701.35), the axis clamp, the empty-schedule clip (FU-8). Axis spans
  `min(origination, today)..max(payoff, today)`.
- [ ] **C6** `feat(loan): a plan is payment RECORDS, not schedule rows` (D1) -- deletes
  `_forward_rows`, `balance_from_schedule_at_date`, `AmortizationRow.remaining_balance`. Kills
  B-9 (overdue installments paying down debt).
- [ ] **C7** `feat(loan): the payment you plan is the payment the loan gets` (D3) -- the drift
  warning + one-click sync (live today: transfer $1,910.95 vs contract $1,911.29 since the
  2026-07-06 escrow change).
- [ ] **C8** `fix(loan): the payoff date is derived, never persisted from a schedule` -- kills
  B-14 (recurrence sync persisting a blind-walk payoff) and B-20.
- [ ] **C9** `fix(transfers): a loan cannot receive a payment before it originates` -- R-C
  ruled 2026-07-16: reject at the transfer write boundary.

### Phase D -- structure replaces policy

- [ ] **D1** engine cluster private inside the seam package (~60 fence entries delete).
- [ ] **D2** distinct types: cash-flow balance vs net-worth balance.
- [ ] **D3** retire W9905; shrink W9906 to a smoke alarm. Until then the fence is FROZEN: no new
  entries, no new lists.

### Phase E -- the ledger becomes a checked projection

- [ ] **E1** postings generated from the event stream; `sum(postings) == fold(ACTUAL)` asserted
  at write time (consider the existing deferred-trigger pattern for a DB-level invariant).
  Bring the escrow write paths into the reconcile (today seven escrow routes write and none
  re-syncs; only a forward-boundary guard protects them).

### Phase X -- cash (after the loan cutover proves the machinery)

- [ ] **X1** `fix(balance): a settled transaction counts from the instant it settled` -- R-B's
  instant partition (ruled 2026-07-16), shared with `account_posting_service/_walk.py`.
  Recovers the dropped settled activity (measured: $9,431.72 uncounted across 17 days until a
  manual re-anchor).
- [ ] **X2** `refactor(balance): a cash account is an event stream` -- the fold; deletes
  `calculate_balances`' Projected-only premise, `_detect_stale_anchor` (nothing left to
  detect), and the scalar/daily-series fork (they disagreed by $999.48 on 2026-07-16).
- [ ] **X3** `fix(balance): the past is the anchor history, not today's anchor carried
  backward` -- 44 real assertions currently discarded; kills the pre-anchor fabrication (B-18).
- [ ] **X4** `refactor(accounts): current_anchor_balance is a reconciled cache or it is
  nothing` -- today divergence from its history table is detected and only logged.
- [ ] **X5 (optional feature)** anchor `effective_date` migration -- only needed for backdated
  statement assertions; NOT a prerequisite for X1-X4.

### Phase F -- closeout

- [ ] **F1** FU-1: the Van Loan's known-wrong history (duplicate same-day anchors, a $452.37
  step with no payment) -- a DATA correction, made only after B2's oracle can validate it.
- [ ] **F2** `refactor(analytics): delete the dead year-end summary service` -- R-D ruled
  2026-07-16: delete now (with its tests); `_income_tax` stays for the live Taxes tab.
- [ ] **F3** prod ship: dev -> main PR for the whole arc per the standard pipeline.

## 6. The findings ledger

Every finding from the archived audits, its status, and the step that closes it. IDs keep their
archive names so old references resolve here.

| id | finding (one line) | worst measured | status | closed by |
|---|---|---|---|---|
| B-1 | Future-origination loan posts no OPENING; seam 500s five pages when the date arrives | outage | open (bounded by container-restart backfill) | A3 |
| B-2 | Property equity chart derives debt from schedule rows; wrong on 8/13 shapes | $299,701.35 | open, reachable | C5 |
| B-3 | Grid renders a loan's balance RISING (cash producer on an amortizing account) | +$1,910.95/mo, unbounded | **LIVE** | A1 (gate), D4 |
| B-4 | `_forward_rows` `is_confirmed` filter has zero discriminating tests | $4,449.72 | guard gap | A2 |
| B-5 | Balance sheet renders a negative liability, HTTP 200 | -$7,643.80 | latent | A3 (mechanism), E1 (invariant) |
| B-6 | Taxes tab prints interest for a loan the seam refuses to value | $4,156.61 | latent, live tab | C3/C6 |
| B-7, B-10 | Year-end omits a true-up payoff; spends a fabricated `jan1=0` | $255,300.26 | dead code; deletion ruled (R-D) | F2 |
| B-8 | Fail-loud misses future valuations (returns before the ledger read) | unbounded | latent | C3 |
| B-9 / FU-7 | Projection pays down overdue installments nobody paid | -$15,755.38/period | open, reachable | D1 -> C6 |
| B-11 / FU-4 | Period before the ledger's opening renders the loan debt-free | $17,134.85 | **LIVE** (/savings map) | C1 |
| B-12 | Unfenced producer tier below the fence; `loan_resolver` package wholly unfenced | -- | guard gap | Phase D |
| B-13 | Loan detail route answers a broken loan from the money-blind replay | $199,600.80 | latent | C4 |
| B-14 | `loan_recurrence_sync` persists a payoff date off the blind walk | -- | reachable | C8 |
| B-15 | Kind-blind true-up writes a cash anchor onto a LOAN (has fired: both real loans carry rows) | -- | **fired on real data** | A1 |
| B-16 | Horizon uses `is_paid_off` where the contract says `is_retired` | -- | latent | collapses at C3/C4 |
| B-17 | Debt-track `is_originated` wiring unguarded (guard tests a hand-built dict) | -- | guard gap | A2-adjacent; flag deleted at C3 |
| B-18 | Cash scalar fabricates pre-anchor balances from today's anchor | -- | live | X3 |
| B-19 | False `DebtSchedule` type hints in `_income_tax` | -- | open | fix on first C-phase touch |
| B-20 | True-up-paid-off loan shows origination as payoff date, no badge | -- | open | C8 |
| B-21 | `TestBrokenLoanFailsLoud` cash fallback asserts `is not None`, not the value | -- | guard gap | A2 |
| B-22 | Dead `insert_origination_event` fixture helper | -- | open | C1 |
| FU-1 | Van Loan history known-wrong (duplicate anchors; $452.37 unexplained step) | $897.16 | data defect | F1 |
| FU-3 | Standing overpayment resolves at today for any as-of | -- | latent | C-phase note |
| FU-5 | Settled payment into an unoriginated loan vanishes | $1,200 test case | ruled: reject at write boundary (R-C) | C9 |
| FU-8 | Empty schedule admits the whole contractual walk as back-projection | $197,049.32 class | latent | C5 |
| cash D1 | Settled post-anchor transactions counted by NO producer | $9,431.72/17 days | **LIVE** (the re-anchor treadmill) | X1 |
| cash D2 | Scalar is period-flat; contradicts the daily series | $999.48 on 07-16 | **LIVE** | X2 |
| cash D3 | Pre-anchor: scalar fabricates, map omits; every re-anchor rewrites the whole past | -- | live | X3 |
| cash D4 | Anchor column vs history table: divergence detected, only logged | latent | latent | X4 |
| N-1 (07-16) | Archived X0 rule would double-count early-settled transactions | 15 real pairs | plan defect, corrected | R-B / X1 |
| N-2 (07-16) | Settle-time freeze reads the clock (`loan_payment_service.py:762`) | -- | recorded risk | D3/C7 surfaces it |
| N-3 (07-16) | Escrow writes never trigger a posting sync (guard-only protection) | -- | latent hazard | E1 |

## 7. Verification standard (what "done" means for every step)

1. **The baseline must not move** (Section 2) unless the step's design says it moves (C2), in
   which case every moved number is individually explained and signed off.
2. **Oracles are exhaustive and independent.** Every day, every shape; never a sample; never two
   producers that share code proving each other. The fold is the reference; optimized readers
   are proven equal to it over GENERATED shapes.
3. **Every guard gets a negative control** that is shown to fire. A guard whose control does not
   fire is not a guard.
4. **The fixture matrix must contain the shape the feature exists for** (a paid loan, an
   off-schedule payment, a delinquent loan).
5. **Green gates are necessary, never sufficient.** A $197,049.32 defect passed pylint 10.00 and
   a 7,387-test suite. Live-render the five loan surfaces against the dev clone per CLAUDE.md
   rule 9.
6. **No uncited claims in this document.** Anything stated here as fact about the code was
   verified on 2026-07-16 or carries its own commit hash; when you edit this file, re-verify
   what you touch.

## 8. Process lessons (paid for repeatedly; do not pay again)

* Probe before you design; the 60-line probe has repeatedly beaten the 1,500-line plan.
* Two wrong implementations agreeing is not a proof.
* A DRY refactor of a PREDICATE can move money -- prove two rules answer the same question
  before merging them; otherwise make one BUILD ON the other.
* A safety that is a predicate is not a safety.
* Boundary predicates standing in for instants or records are this codebase's signature defect
  (the walk clock, the period-start payment date, the archived X0 rule). When a rule says
  "period", ask if it means "instant"; when it says "schedule", ask if it means "record".
* Documents rot in days here. This file is the only one allowed to rot, and every edit re-dates
  it.

## 9. Rules for this document

1. **This is the only live planning document for the balance arc.** The archive is read-only
   history. If a step needs more design than Section 5 carries, the design happens in the
   commit/PR that ships it -- or amends this file. New standalone plans, audits, and follow-up
   documents for this arc are prohibited; findings become rows in Section 6.
2. When a step ships: tick its box, append the commit hash, and move anything it closed in
   Section 6 to status "closed (hash)".
3. When a ruling in Section 4 is answered: record the answer and date in place.
4. Keep this file under ~500 lines. If it grows past that, something is being planned instead
   of built.
