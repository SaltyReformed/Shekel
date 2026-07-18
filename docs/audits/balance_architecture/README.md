# The balance architecture: the plan of record

**This is the ONLY live document for the balance arc.** Everything else that ever governed this
work is in `archive/` (read-only history, indexed by `archive/README.md`). The rules for this
document are at the bottom (Section 9); the short version: amendments are edits HERE, a shipped
step gets its checkbox ticked with its commit hash HERE, and no new planning documents get
written for this arc.

**State as of 2026-07-18:** design verified and locked; ALL rulings answered (D1-D5,
R-A..R-E, Section 4); Phases A and B complete (**A1** `f11382a0`, **A2** `c96c62be`,
**A3** `4e46a0a8`, N-9 `44cbd028`, **B0** `d1586254`, **B1** `e227de08`, **BG**
`dba91dc0`, **B2** `8f070386`). **Phase C: C1** (`18fd3a04`, a loan's origination is its
ledger opening), **C2** (`eb5de4ac`, the ONE CLOCK: an event counts from the day it
happened -- the date its posting already carries in `entry_date`; closed the N-10 leak and
N-12, moved the per-period map to period-END keying), **C3a** (`df775017`, `positions()` -- the
total loan producer), **F2** (`3aecceb0`, the dead year-end service deleted), and **C3c**
(`99cc2816`, interest-in-year is the fold-based `balance_at.loan_interest_in_year`) shipped. **C3 is
DECOMPOSED** (developer ruling 2026-07-18): too large for one revertable commit and reaching into
dead year-end code, so it ships **C3a** -> **F2** (pulled AHEAD) -> **C3c** (interest-in-year is a
DEDICATED producer, NOT `positions().cum_interest` -- the tax figure keys on the display-tz paid
year while the balance keys on UTC) -> **C3b**, each a REFACTOR (baseline unmoved; B-9's
overdue-installment paydown preserved until C6). **C3b is itself DECOMPOSED into C3b1-C3b4**
(developer ruling 2026-07-18, mirroring C3a): the scalar cutover is proven by C3a's oracle but the
per-period MAP has no equivalence proof yet, and `positions()` lives ABOVE `net_worth_kernel` (at its
1000-line cap) so the map branch must MOVE INTO the seam. **C3b1** (`f410afa9`, the scalar + the
liability band read `positions()`; the read pass memoizes the loan walk; scalar now FOLDS a broken
loan instead of raising -- closes B-8 at the scalar) and **C3b2** (`28f8fe51`, the additive
`positions_period_map` producer + its every-period oracle vs the shipping map; current-period clamp
proven load-bearing, incl. the N-10 originate-inside-current-period `0.00`) and **C3b3** (`84e386c6`,
the map dispatch reads `positions_period_map`, MOVED into the seam's `_account_balance_map` since the
kernel cannot import the seam; the map now FOLDS a broken loan -- B-8 closed at the map; dev-clone
live-render UNMOVED to the cent, Mortgage $177,277.97 / Van Loan $15,663.59, map == scalar) shipped.
**C3b4** (`5c62c995`, the dead ledger-domain readers deleted -- `loan_ledger_domain` /
`confirmed_loan_ledger_domain` / `LoanLedgerDomain` gone; `_domain.py` RENAMED to `_linked_ledger.py`
since it also held the two KEPT-reader query helpers `_has_opening_posting` / `_visible_nets`) shipped,
closing the C3b arc.
**`confirmed_loan_balance_map` is KEPT** (C3b3 deletion-list correction, developer ruling 2026-07-18:
it reads the KEPT posting ledger and is the Step-4 reconciliation oracle's independent window; its
fate is decided at E1). The C2 real-clone history-window live-render (~26 days Mortgage / ~13 days
Van Loan, today UNMOVED) is still outstanding before the F3 prod ship. **C4** (`c98ea07b`, the loan
route reads the seam through ONE `BalanceContext` and drops its private `resolve_loan_seeded`;
`LoanState.current_balance` KEPT for its two in-cluster readers, deletion deferred to C6-adjacent;
B-13 closed, B-13-route control pins the fold vs the money-blind replay) shipped. **C5** (`821dd0eb`,
the equity chart's debt line is the fold: the CONFIRMED and PROJECTED tiers read
`balance_at.positions()`, the pre-tracking ESTIMATED back-projection KEPT per D2, the axis spans
`min(origination, today)..max(payoff, today)` so the today-clamp is retired and the empty-schedule
clip is gone; the chart reconciles with the equity hero AT today via a shared `window_sample_date`;
B-2 and FU-8 closed) shipped. Next commit: **C6**.

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
| Phase A (A1/A2/A3 + the N-9 Schedule A fix) | dev | `f11382a0`, `c96c62be`, `4e46a0a8`, `44cbd028` |
| The loan fold: `loan_ledger` leaf (B0) + `fold_loan_balances` (B1) | dev | `d1586254`, `e227de08` |

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

* **One split function** (`loan_ledger.split_one_payment`, reused verbatim -- it lives in the
  fold's leaf since B0, and the posting writer imports it from there) divides ACTUAL and
  PLANNED cash alike; the cash the grid shows leaving checking is the cash the loan folds.
* **Predictions fill the gaps in the record -- in both directions -- and never where a record
  exists**: contractual back-projection before the first record (ESTIMATED tier, visually
  distinct); payment RECORDS within the materialized horizon (PLANNED); contractual synthesis
  beyond it (ESTIMATED). An installment with NO record behind it never happened and never pays
  the debt down (delinquency reads honestly).
* **Public API, three entries**: `positions(account, ctx, dates) -> {date: LoanPosition}`
  (balance + cum_principal + status -- serves the scalar, the map, series, principal-in-window);
  `plan(account, ctx) -> [PlannedPayment]` (carries NO
  balance; payoff date = `plan[-1].date`, derived, never stored); `events(account, ctx)`.
  **Interest-in-year is NOT on `positions()`** (C3c, developer ruling 2026-07-18): it keys on the
  DISPLAY-tz paid year (the tax clock) while every `positions()` figure keys on the UTC visible date
  (the balance clock), so it is a DEDICATED `balance_at.loan_interest_in_year` -- two clocks, two
  functions.
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
| **R-D** | The year-end summary service and its tests are DELETED (dead code carrying B-7/B-10; `/analytics/year-end` already 302s). Rebuild on `positions()` later if ever wanted. ~~`_income_tax` survives -- the live Taxes tab uses it.~~ **Corrected 2026-07-16 (A2):** only TWO functions are live -- `_compute_mortgage_interest` -> `_loan_year_interest` -- reached because `tax_report_service.py:84` imports a PRIVATE name across packages, and their only real coverage sits in the file F2 deletes (`TestMortgageInterestGenesisHybrid`). Both die at **C3**, which deletes their input type; by F2 nothing is stranded, so F2 stays a clean whole-package deletion. If C3 has not landed when F2 runs, F2 must move the two functions to `tax_report_service` (their only caller) rather than leave the private import. | F2 (C3 first) |
| **R-E** (N-11; answered 2026-07-17) | A raw transaction typed onto a loan account (which moves the posted balance but not the fold) is **FORBIDDEN AT THE SOURCE**, not modeled as a third event kind. Every write path that could type one onto an amortizing loan refuses it on the D4/R6 predicate (`classify_account is AMORTIZING` / `has_amortization`): the two transaction-create routes, the recurrence-template form, AND -- found in the guard's own adversarial review, so BROADER than the plan's cited `create.py:78` -- the salary-profile account picker (which copies `template.account_id` into `recurrence_engine`). Rejected: a third event kind, which keeps a cash-basis paydown path the grid refuses to render and contradicts D4. This makes the fold complete BY CONSTRUCTION, so B2's every-day equality needs no N-11 exception. Any pre-guard row is an F1-class data item; the two real loans carry none (B1's 212-day match). | BG (guard), B2 |

## 5. The steps

Each commit is independently green (full suite + `pylint app/` with the full `--fail-on` set)
and independently revertable. Tick the box with the commit hash when it ships. Detail beyond
what is written here is decided in the commit itself, not in a new document.

### Phase A -- stop the bleeding, build the net (no model change)

- [x] **A1** `fix(accounts): a loan is not a cash account` -- **SHIPPED `f11382a0`
  (2026-07-16).** As built, two more doors than scoped: the Net Worth Cockpit's click-to-edit
  cell offered the CASH anchor editor on loan cards (the live UI door -- now read-only for
  amortizing kinds), and the full-form edit wrote anchors kind-blind (now refuses a CHANGED
  loan anchor).  Service guard (`AmortizingAccountAnchorError`) + route 422s + all three
  resolver steps + the settings picker.  Closes B-3 and B-15; residue recorded as N-4/N-5
  (the reset's balance-preserving re-anchor and the create factory's origination anchor).
  Verified live on the dev clone: `?account_id=3` resolves to Checking; the $1.00 Mortgage
  true-up is refused; baseline unmoved.
- [x] **A2** `test(loan): pin the forward walk's value, not two producers agreeing` -- **SHIPPED
  `c96c62be` (2026-07-16).** **As scoped, this step could not do its job, and the correction was
  the step.** Adding a paid shape to `test_every_loan_shape` does NOT make the
  `_forward_rows` `is_confirmed` filter's deletion
  visible: `_assert_agrees` compares the scalar to the map, and on the forward tail both sides
  are the SAME call, `_projected_owed_at(_forward_rows(schedule), p.end_date, projection_seed,
  owed_from)` (`net_worth_kernel.py:510` and `:995`) -- `f(x) == f(x)`, exactly the shape
  Section 7.2 forbids. **Measured 2026-07-16: with the filter deleted, all 7,401 tests pass.**
  Second, `balance_from_schedule_at_date` returns the LAST qualifying row's `remaining_balance`
  rather than subtracting principal, so the filter changes an answer ONLY between `ctx.as_of`
  and the first UNCONFIRMED row's due date; every future period end-date the matrix probes
  (04-09, 04-23, 05-07, 05-21) lands past that window, where it is a measured no-op. So: pin
  the VALUE inside the window on a paid-then-trued-up loan (new
  `TestForwardWalkExcludesLedgerBookedRows`; the fixture measures $48,496.25 -- the delta is
  `last_confirmed_row.remaining_balance - projection_seed`, unbounded in the true-up's size).
  Still add the paid shape to the matrix (Section 7.4; its BEGUN half IS a real two-reader
  check) and fix the "every loan shape" overclaim. Also B-21 (assert the value, not
  `is not None`) and an independent hand-computed oracle for the LIVE Taxes number -- its only
  live-path test spends `_compute_mortgage_interest` as its own oracle, so a double-count ships
  green (shown to fire). NOT done: a negative control for `_loan_year_interest`'s
  `not row.is_confirmed` -- it is unreachable by construction (N-6). Building that oracle
  surfaced a LIVE tax defect off the arc's path, fixed in its own commit (N-9, `44cbd028`).
- [x] **A3** `fix(loan): the ledger records what is KNOWN; the readers decide what has
  HAPPENED` -- **SHIPPED `4e46a0a8` (2026-07-16).** The clock is out of the loan write walk
  and G1 is closed. **"The readers already bound by visibility" was FALSE, and correcting it
  was the step.** Two regressions measured from naively posting every anchor: (1) an anchor's
  read bound is `LEAST(entry_date, pay_period.start)` (`_asof.effective_date`) -- a
  period-START rule, so a FUTURE anchor resolves to a PAST date and a loan originating INSIDE
  the current period read its full principal as owed TODAY ($200,000.00 five days early),
  trend contradicting hero; (2) `confirmed_loan_view` stopped returning `None`, and the
  ledger's honest `0.00` for a loan that does not exist yet seeded `_build_forward_inputs`,
  collapsing a 360-row schedule to **ZERO rows** (payoff = origination date, $200,000 flat
  forever). Root cause of both: the map and the view INFERRED "not originated" from the
  ledger's SILENCE -- an inference that held only because the clock forced it. Fix: both ask
  the FACT (`origination_date`), as the scalar `amortizing_balance_at` already did and
  documented. `confirmed_loan_view` now takes `params` (all 4 callers already held it: no
  re-load, and the origination cannot be mismatched to the account).
  Also: `loan_balance_anchor_history` applies the display bound to the walk's OUTPUT;
  `_test_helpers`' future-anchor guard deleted (its reason is gone); `create_baseline`'s body
  moved to a new `baseline_service` (grid.py sat at exactly 1000/1000 lines and the route was
  orchestrating two posting packages; `scenario_resolver` cannot host it -- both packages
  import it, so pylint reports R0401). Every control shown to fire. Residue: **N-10**.
  **N-8 is NOT closed: it was misattributed** (see its row). NOTE: a second write-path clock
  read remains by design -- the settle-time freeze resolves P&I as of today
  (`loan_payment_service.py:762`); D3's drift warning is what surfaces it (C7).

### Phase B -- the fold, as an oracle only

- [x] **B0** `refactor(loan): the walk is a leaf, not the ledger's private` -- **SHIPPED
  `d1586254` (2026-07-17). Not in the original plan; B1 could not be written honestly
  without it.** The fold ALREADY EXISTED as `loan_posting_service/_walk.py` -- a private of
  the GENERAL ledger, which is backwards: E1 makes the postings a checked projection of the
  fold, which is only expressible if the fold is the leaf they derive FROM. It priced itself
  too: B1's recipe below needed FOUR private names out of that package (the R-D smell), and
  both prototypes reached through them. Now `app/services/loan_ledger/` owns the walk
  (`_split`, `_events`, `_fold`); the posting package imports it; the rewritten probe needs
  zero private imports. Pure move (AST-identical modulo renames), 7,410 green. Also killed a
  live duplicate: `_settled_income_shadows` existed TWICE (`loan_loaders` unordered, `_walk`
  sorted), each claiming to be the single derivation -- now one public
  `loan_loaders.settled_income_shadows`.
- [x] **B1** `feat(loan): the loan ledger answers a date -- one fold over one event stream`
  -- **SHIPPED `e227de08` (2026-07-17).** `fold_loan_balances(loan, scenario, dates)`, folded
  from SOURCE events, reading the postings table never; TOTAL (any date, any account, a
  `Decimal`; `0.00` for the empty prefix; no `None`, no raise). Matches the seam on every day
  of both real loans. **Three amendments to this step as written, all forced by the code:**
  (a) **`_plan` deferred to C3** -- B2's oracle can only target the PAST, since the seam's
  future answer is B-9 (overdue installments paying down debt nobody paid) and proving the
  fold reproduces it would be proving a defect; C3 needs the plan because it deletes both
  forward producers. (b) **No `BalanceContext` memo** -- the memo is a production-path
  optimization and B1 is not on it; C3 adds it when a read pass would otherwise re-walk per
  date. (c) **`_split` is NOT a re-export module** -- it OWNS `split_one_payment` (see B0).
  The recipe above also omitted two inputs the working prototypes needed
  (`merge_anchor_and_payment_events`, and the anchor's pay-period resolution), and named the
  visibility rule without noting it is the WRITER's period assignment reproduced from source.
  **Its own review found the trap that mattered**: the fold TOOK a period list, which was not
  an alignment but its only divergence vector -- a window (the shape the grid passes, and the
  shape C3 hands the seam's AMORTIZING branch) moved the balance $150,000.00. The parameter
  is gone; the fold and the writer now share one `owner_pay_periods` query.
- [x] **BG** `fix(loan): a loan's balance is not a transaction sum` -- **SHIPPED
  `dba91dc0` (2026-07-17).** R-E's forbid-at-source guard, pulled ahead of B2 because B2's
  completeness premise (no raw loan transaction can exist) rests on it. Three sources gated
  on the D4/R6 predicate: `_reject_transaction_on_loan` on both transaction-create routes;
  the `_validate_template_form` kind check on the recurrence-template form; and the
  salary-profile picker, which now deposits via the shared
  `account_service.active_accounts_query(amortizing=False)` composer (no inline copy). **The
  template and salary sources are BROADER than the plan's cited `create.py:78`** -- found
  tracing every `TransactionTemplate` build and `generate_for_template` caller; the salary
  source was caught in adversarial review (a loan as the user's first active account made the
  auto-picker post salary income onto it). Read-inert (no read path, no data touched), so the
  baseline cannot move; every guard's control is shown to fire (the salary control fails when
  the `amortizing` filter is flipped). Closes N-11 at the source.
- [x] **B2** `test(loan): the reference fold is the oracle, and it is exhaustive` --
  **SHIPPED `8f070386` (2026-07-17).**
  parallel-run fold vs seam on **EVERY DAY** of every loan's domain, over generated shapes
  (including A2's paid shapes) plus real data. **Sampling is forbidden**: a 14-day sample once
  scored perfect while wrong by $178,103.41 on 22% of days. Every divergence is explained and
  signed off, never silenced. **B2 gates all of Phase C.**
  **What B2 does and does not prove, stated precisely (B1 made this concrete).** The fold and
  the posting readers SHARE the walk -- by design, since Section 3 reuses one split function
  and E1 makes the postings a projection of the fold. So a fold-vs-seam equality is NOT an
  independent proof of the split's VALUE; it proves the posted cache faithfully projects the
  fold on every day, which is exactly what C3's cutover needs and exactly E1's invariant
  checked at read time. The split's value is pinned elsewhere and stays there: the Step-4
  reconciliation oracle's parallel run against the un-seeded resolver, A2's hand-computed live
  Taxes oracle, and B1's hand-computed fold figures. Do not let B2's equality be mistaken for
  the correctness proof; it is the equivalence proof. **Required shapes** (Section 7.4): the
  A2 paid shapes, a tracking-start import, an ARM step, escrow, a payoff overpayment, a
  pre-anchor payment, a late-settled payment whose period does not contain its due date, and
  **N-11's raw-transaction-on-a-loan shape -- now closed by construction (BG)**: B2 both
  demonstrates the divergence is real (a forced raw transaction moves the reader by its amount
  while the fold holds) and asserts the create route refuses the only user path, so the
  every-day equality needs no N-11 exception. Each shape carries a realization assert (the
  ARM rate, the escrow slice, the payoff reaching zero) so a feature no-oping in BOTH
  producers cannot pass green, and a negative control proves the harness fails on a forced $1
  divergence.

### Phase C -- the cutover (order is load-bearing)

- [x] **C1** `fix(loan): a loan's origination is an event, not a footnote` -- **SHIPPED
  `18fd3a04`.** ORIGINATION is ALWAYS the loan's opening (SYNTHESIZED from params, not "a row
  excluded"); a `tracking_start` is an ordinary `is_opening=False` ASSERTION that RESETS the walk
  and now STACKS like a true-up (the deleted `_opening_anchor_fact` supersession was the B-11
  mechanism). The idempotent deploy backfill re-instates reversed openings -- no migration.
  **Today's balance UNMOVED** (verified to the cent on both real loans, held by B2); the only
  movement is a pre-tracking date reading the origination principal FLAT (the plateau) instead of
  $0 -- aged out of the /savings render window, so B-11 is closed at the producer. Closes B-22.
  Display: "Tracking start" badge + Origination/tracking-start rows on the Balance anchors card.
  B2's tracking-start shape pins the plateau ($250k flat, drift -$150k).
- [x] **C2** `fix(loan): one clock -- an event happens on the date it happened` -- **SHIPPED
  `eb5de4ac`.** R-A (settled-date visibility, due-date split keying; NULL-`paid_at` falls back to
  period-start). **The one clock IS the posting's `entry_date`:** readers bound by
  `entry_date <= as_of`, the fold reproduces it via one shared `to_utc_civil_date` the writer
  also calls -- fold == reader by construction. Deletes `_asof.py`; moves
  `confirmed_loan_balance_map` to period-END keying; the fold is now calendar-INDEPENDENT
  (dropped `owner_pay_periods` and its no-periods raise -- more total). Closes the N-10 leak at
  source and N-12; the four N-10 guards are NOT all retired here (#1/#2 at C3b,
  `confirmed_loan_view`'s stays for B-1). History repositions in bounded windows, today unchanged;
  signed off via B2 + full suite (7,446 green, pylint 10.00) + adversarial review. Recorded, out
  of scope: **N-13**.
- **C3 (DECOMPOSED, 2026-07-18)** `the seam's AMORTIZING dispatch is the fold` -- too large as
  one revertable commit and reached into dead year-end code, so it ships C3a -> F2 -> C3c -> C3b,
  each a REFACTOR (baseline unmoved; B-9 preserved until C6). F2 (Phase F) is pulled AHEAD of C3b
  so the deletions run on a live-only surface.
  - [x] **C3a** `feat(balance): positions() -- one loan producer, fold past and projection future`
    -- **SHIPPED `df775017`.** `balance_at.positions(account, ctx, dates)`: the FOLD
    (`fold_loan_balances`) for a past date on an originated loan, the schedule projection
    (`forward_balance_at_date`, seeded from `generate_debt_schedules`) after -- or ALL dates for a
    loan not yet originated -- split on the shipping scalar's own boundary. **The PAST reads the
    FOLD (source events), not the postings** -- the cutover's heart, B2-proven equal. ADDITIVE and
    unwired (only its oracle calls it); reproduces `amortizing_balance_at` on EVERY day past AND
    future (`test_loan_positions_oracle.py`, 3 tests + teeth), so C3b moves no money by proof. Lives
    in `balance_at`, NOT the `loan_ledger` leaf: the preserve-behaviour forward half needs the
    resolver schedule + seed (W9906-fenced to the seam/kernel, and `net_worth_kernel` is at the
    1000-line cap), so composing fold + resolver is a SEAM job -- it moves to `loan_ledger` at C6
    when fold-native. Reuses `generate_debt_schedules` + `forward_balance_at_date` (DRY), deletes
    nothing.
  - [x] **C3c** `refactor(analytics): interest-in-year is balance_at.loan_interest_in_year` --
    **SHIPPED `99cc2816`.** A DEDICATED seam producer, **NOT `positions().cum_interest`** (developer
    ruling 2026-07-18): the tax figure keys on each payment's DISPLAY-tz civil paid year (the L9
    rule), while the fold/balance keys on the UTC visible date -- a settle 8:05pm EST Dec 31 folds as
    Jan 1 (UTC) yet deducts in the OLD year, so a UTC-keyed `cum_interest` sampled at year-ends
    mis-years it. Two clocks, so interest gets its own function. It folds each settled payment's
    actual interest by its display paid year and adds the schedule's unconfirmed rows for the future.
    Points the live Taxes tab at it; deletes `_compute_mortgage_interest` / `_loan_year_interest` and
    the `is_confirmed` guard (subsumed by reading only unconfirmed rows). **The `settled_due_months`
    de-dup STAYS** -- the plan's "delete both guards" was wrong (corrected 2026-07-18, probe-measured):
    while the future is schedule-row-driven a settled-but-unconfirmed (early-settled) installment is
    in BOTH halves, so dropping the exclusion double-counts it (**+$489.97** measured). It is relocated
    INTO the producer, derived from the SAME fold walk (non-drift); its STRUCTURAL deletion moves to
    C6. Grade on A2's hand-computed live oracle, never the producer as its own oracle (N-7). Closes
    B-6.
  - **C3b (DECOMPOSED, 2026-07-18)** `the seam's AMORTIZING dispatch reads positions()` -- the cutover
    proper, split into FOUR independently-green commits mirroring C3a (developer ruling). Two facts
    forced the split: C3a's oracle proved only the SCALAR equals `positions()` (the per-period MAP has
    NO equivalence proof yet), and `positions()` lives in the seam ABOVE `net_worth_kernel` (at its
    1000-line cap) which cannot import it back, so the map branch must MOVE INTO the seam -- not a
    one-line redirect. **The plan's original single-commit deletion list was the END-of-C3 (post-C6)
    state and is corrected here**: `positions()` (C3a) still consumes `generate_debt_schedules`,
    `DebtSchedule` (`schedule`/`projection_seed`/`owed_from`), `_projection_seed`, and
    `forward_balance_at_date`, and the savings trend history-gate consumes `generate_debt_schedules`
    via `debt_schedule_rows` -- so ALL of those survive to C6, NOT C3b. Of "both forward producers"
    only the MAP's `compute_forward_loan_period_balance_map` dies at C3b; `forward_balance_at_date`
    lives to C6.
    - [x] **C3b1** `refactor(balance): the scalar and the liability band read positions()` --
      **SHIPPED `f410afa9`.** The seam's SCALAR (`balance_at.balance_at` AMORTIZING branch) and
      LIABILITY band (`liability_owed_at_dates`) read `positions()`. A new `BalanceContext.loan_walk`
      memo walks each loan's ledger ONCE per read pass and `fold_from_walk` samples it, so the
      scalar/map/liability folding one loan in a render do not each re-walk it (the redundant-fold DRY
      fix C3a earmarked). `fold_from_walk` is the shared sampling core, and B2's oracle subject
      `fold_loan_balances` DELEGATES to it -- so the every-day oracle grades the exact code production
      runs, not a copy (adversarial-review catch, fixed pre-commit). Deletes `amortizing_balance_at` +
      `loan_owed_at_dates`. The scalar cutover
      is proven by C3a's every-day oracle (RETIRED here, its job done -- ongoing proof is B2 + the
      seam's own tests); the liability forward path is the IDENTICAL `forward_balance_at_date` call, so
      the band cannot move. **Behaviour change (approved 2026-07-18): the SCALAR now FOLDS a broken
      loan (originated, no opening posting) instead of raising `LoanLedgerNotOpenedError`** -- the fold
      reads SOURCE facts, so a cold posting cache is a repairable inconsistency (E1), not a read-time
      outage; the broken-loan test flips from expects-raise to expects-$240,000. The MAP still raises
      until C3b3. Baseline UNMOVED on both real loans (C3a oracle + B2 + full suite 7367). Also fixed
      a PRE-EXISTING dev-only checker failure `test_classification_sets_match_the_real_fenced_modules`:
      C2 (`eb5de4ac`) deleted `_asof.py` but left `effective_date`/`scope_to_linked_ledger` in the
      loan-ledger non-producer set (stale fence entry; uncaught because dev is not CI-gated).
    - [x] **C3b2** `test(balance): the positions-based per-period map is proven equal` -- **SHIPPED
      `28f8fe51`.** `positions_period_map` samples `positions()` -- begun periods at
      `min(period.end, ctx.as_of)`, future periods at `period.end` -- reproducing the splice's
      `period.start <= ctx.as_of` boundary; additive and unwired (only its oracle calls it). The
      current-period clamp is the subtlety B2's scalar proof did not cover: `positions([period.end])`
      would hand the current period to the projection, moving it whenever a payment falls between today
      and period end. **Caller-trace VERIFIED: the per-period map is NEVER read with
      `ctx.as_of != today` in production** (every map caller builds `BalanceContext.build(user_id)` =
      today; the one explicit-`as_of` site is the Taxes tab, which reaches `loan_interest_in_year`, not
      a map), so the clamp reproduces `_build_amortizing_balance_map` exactly -- the historical-`as_of`
      case where the two would diverge is unreachable. The every-period oracle
      (`test_loan_positions_period_map_oracle.py`) parallel-runs vs the shipping `balance_map` over four
      shapes (trued-up + payments, tracking-start plateau, payoff, not-yet-originated) plus a forced-$1
      teeth test, and proves the clamp load-bearing on TWO shapes -- including a loan originating INSIDE
      the current period reading `0.00` not its opening (the N-10 shape, added beyond the plan).
      Adversarial review clean (equivalence correct; the no-posting-after-today invariant the clamp
      rests on confirmed ENFORCED -- server-set `paid_at`, `anchor_date <= today`, the `owed_from`
      gate). Deletes nothing.
    - [x] **C3b3** `refactor(balance): the per-period map reads positions()` -- **SHIPPED
      `84e386c6`.** The map dispatch MOVED into the seam's `_account_balance_map` (the kernel cannot
      import `positions()`), pointed at C3b2's `positions_period_map`. Deletes
      `_build_amortizing_balance_map`, the kernel's AMORTIZING branch + its now-dead `debt_schedule`
      param, `splice_confirmed_and_projected_loan_balances`, `compute_forward_loan_period_balance_map`,
      `_loan_ledger_not_opened`, `LoanLedgerNotOpenedError`, the two-zeros doctrine. **`confirmed_loan_balance_map`
      is KEPT, correcting the plan's deletion list** (developer ruling 2026-07-18): it reads the KEPT
      posting ledger and is the Step-4 reconciliation oracle's independent window, so deleting it would
      gut that oracle; its fate is decided at E1. All four cutover hazards handled: (1) the
      `account.id in inputs.debt_schedules` gate degrades an unconfigured Mortgage to cash, not
      `positions()`'s fail-loud; (2) the map FOLDS a broken loan (E1 decision, mirrors C3b1 -- B-8
      closed at the map); (3) the `_inputs -> _positions` import cycle broken by importing
      `require_scenario` from `resolution_context` in `_positions`; (4) the `current_anchor_period_id is
      None -> None` guard preserved in the moved branch. **Broader than scoped** (traced): the C3b2
      oracle RETIRED (tautology after the cutover, mirroring C3b1 retiring C3a's); `TestScalarAndMapAgree`'s
      docstring corrected (both producers read `positions()` now -- a sampling-consistency check);
      `baseline_service` + the G1 `test_grid` narrative updated (the fold retired the read-outage; G1's
      now-vacuous `balance_at` assertion moved to the posting reader since it folds regardless); the
      W9905/W9906 checker sets + tests repointed off the deleted forward map onto `forward_balance_at_date`;
      a seam-level future-value pin restored the after-payment coverage the retired savings dispatcher
      unit tests carried. Baseline UNMOVED (dev-clone live-render + B2 + full suite 7360, pylint 10.00,
      adversarial review clean).
    - [x] **C3b4** `refactor(balance): delete the dead ledger-domain readers` -- **SHIPPED
      `5c62c995`.** Deleted the seam `loan_ledger_domain`, the reader `confirmed_loan_ledger_domain`,
      `LoanLedgerDomain`, and the private `_confirmed_loan_ledger_start` (0 production callers since F2
      deleted the year-end summary -- whole-repo grep confirmed), plus the fence entry, both packages'
      exports, and their tests. The shared `_is_originated` STAYS (still
      `loan_figures`/`is_retired`/`is_paid_off`). **The plan's "delete `_domain.py`" was too broad, and
      correcting it was the step:** that module ALSO held two load-bearing PRIVATE query helpers the
      KEPT readers build on -- `_has_opening_posting` (the configured-loan sentinel
      `confirmed_loan_balance_at`/`_map` and `_display` guard on) and `_visible_nets` (the grouped
      per-date load `confirmed_loan_balance_map` prefix-sums). So `_domain.py` was RENAMED (`git mv`) to
      `_linked_ledger.py`, stripped to those two helpers (kept VERBATIM) with a rewritten module
      docstring; `_reader.py`'s import repointed, `_display.py` untouched (it takes the helper via
      `_reader`). The deleted seam function's now-orphaned `_require_scenario` import went with it.
      Baseline cannot move (no read path touched); full suite 7357 (= 7360 - 3 deleted domain tests),
      pylint 10.00, the fence classification-completeness guard green, adversarial review clean (its one
      catch -- a gate-invisible orphaned test import, `tests/` being outside the pylint gate -- fixed
      pre-commit). Closes the C3b arc.
- [x] **C4** `fix(loan): the loan page reads the seam like everyone else` -- **SHIPPED
  `c98ea07b`.** The loan ROUTE rendered its balance from the money-blind anchor replay for a broken
  loan (B-13); it reads the seam now. **Scope corrected 2026-07-18 (developer ruling), on two counts
  the one-liner got wrong:**
  (1) **`LoanState.current_balance` was NOT deleted here** -- beyond the route's reads, the field is
  still consumed by TWO in-cluster readers: `net_worth_kernel._projection_seed` (the seed for
  `positions()`'s FORWARD projection -- `positions()` is its only reader) and
  `balance_at._loan_figures._is_retired`. Both equal the fold for an intact loan today, so deleting
  the field means making the seam's forward SEED fold-native, which belongs where `positions()` goes
  fold-native (C6-adjacent). C4 KEPT the field and read the ROUTE off the seam (mirrors C3b3's
  KEEP-correcting-the-deletion-list); the field dies in its own later commit.
  (2) **The migration was the WHOLE loan route package, not "7 reads"** (developer ruling: full, not
  surgical): reading only the balance off a new `BalanceContext` while the route still resolved a
  private `LoanState` for the payment/rate/schedule would resolve each loan TWICE per request -- the
  redundant derivation the arc exists to kill. So the route DROPPED `resolve_loan_seeded` entirely and
  reads through ONE `BalanceContext`: balance from `balance_at.balance_at`, payment/rate/payoff/arm from
  `loan_figures`, schedule from the composer it already runs (`build_baseline_scenarios`'s
  `history_rows + committed_forward` == the dropped `LoanState.schedule`, same `compute_payoff_scenarios`
  call, reviewer-verified). Touches `dashboard`/`calculators`/`schedule`/`escrow_rates`/`payment_transfer`/`_helpers`.
  **Broader than scoped in three places** (found building it + adversarial review): the standalone
  SCHEDULE route is a TABLE, not a balance surface, so it does NOT read the seam -- it composes ONCE via
  a new shared `load_baseline_scenarios` helper and reads its rate off a new cheap
  `loan_resolver.current_rate_baseline` accessor (proven `== resolve_loan(...).current_rate`), not a full
  resolve (else it derived its schedule twice); the refinance paid-off gate now reads the seam balance
  once and drops the redundant `not state.schedule` half (an empty committed schedule implies a zero
  balance; its only divergent case -- a past-term balloon still owing -- is better served by a comparison
  than blocked); the stale `_secured_debt.py` docstring is corrected (the seam FOLDS a broken loan since
  C3b1, no longer raises). A no-baseline user cannot reach a configured loan (baseline created at
  registration), so the seam's `require_scenario` fail-loud is unreachable here -- matched to
  `debt_strategy`, deliberately not guarded. Baseline UNMOVED on real data by proof (intact loans: fold
  == the replay, by B2; `test_cross_page_balance_equality` green); a new route test pins the fix -- a
  broken loan's page renders the fold `$231,200.00`, never the money-blind replay `$239,761.08`. Full
  suite 7359, pylint 10.00, adversarial review clean (its Medium -- the schedule double-compose -- and
  4 Lows all fixed pre-commit).
- [x] **C5** `fix(accounts): the equity chart's debt line is the fold` -- **SHIPPED
  `821dd0eb`.** The chart derived each month's debt from the resolver's CONTRACTUAL schedule rows
  (`remaining_balance`), which advance one installment whether or not the borrower paid it; it
  disagreed with the equity hero (the fold) on eight of thirteen shapes by up to $299,701.35 (B-2).
  It now folds: `SecuredLoanSeries` drops its `back_projection`/`schedule` row lists for a tiered
  `month_balances` map, and the seam samples `balance_at.positions()` once per calendar month -- the
  fold of actual cash at or before today, the same forward projection after. **"The debt line is
  the fold" is the CONFIRMED and PROJECTED tiers; the pre-tracking ESTIMATED contractual
  back-projection STAYS (ruling D2)** -- the fold holds a flat plateau there, not the declining
  curve the loan amortized unseen. Sampling uses the per-period map's begun/future rule, extracted
  here to a SHARED `_positions.window_sample_date` so the map and the chart cannot drift on the
  boundary C3b2 proved load-bearing: a begun month reads `min(month end, as_of)`, so the CURRENT
  month reads today's fold and the chart reconciles with the hero AT today -- closing the M1 gap
  (today's month was `projected` and one payment low; now `confirmed` and equal). The producer
  loses `_loan_month_tiers` (schedule-row derivation) and `_dense_month_balances` (gap-fill,
  unnecessary once the fold samples every month), and `_build_axis` spans `min(origination, today)
  .. max(payoff, today)`, retiring the "defensive" `today_index` clamp (the mechanism that clamped
  a not-yet-originated mortgage's principal onto today, $299,701.35). The empty-schedule clip is
  gone: an empty schedule draws NO back-projection, so FU-8's phantom contractual walk cannot
  recur. **Two sub-points decided this session, mirroring C3b/C4's deletion-list corrections:** a
  RETIRED loan is still DROPPED, not charted with its history (developer ruling: C5 stays a pure
  B-2 fix; retired-history is a later feature); and a mid-life import's ORIGINATION month reads the
  fold's recorded opening principal tagged `confirmed`, NOT `estimated` (developer-ratified: the
  opening is a hard fact, and it renders inside the dotted segment regardless; pinned by a test).
  Dev-clone live-render UNMOVED: the real Mortgage reconciles to the baseline $177,277.97 at today
  (== hero == `positions`), axis Dec 2018 (origination) .. Dec 2048 (payoff), tiers confirmed
  opening $202,000 -> estimated back-projection -> confirmed tracking (Apr 2026) -> projected,
  contiguous, no gaps. Full suite 7359, pylint 10.00, adversarial review clean (no
  Critical/High/Medium; two Low fixed pre-commit -- the shared-helper DRY extraction and the pinned
  origination-month tier).
- [ ] **C6** `feat(loan): a plan is payment RECORDS, not schedule rows` (D1) -- deletes
  `_forward_rows`, `balance_from_schedule_at_date`, `AmortizationRow.remaining_balance`. Kills
  B-9 (overdue installments paying down debt). Also STRUCTURALLY deletes C3c's settled-slot de-dup:
  with the future as payment records there is one record per installment, so no fold/schedule slot
  overlap to exclude. Fold in the second in-year interest producer here too: the loan detail page's
  `interest_paid_ytd` chip still reads `confirmed_loan_interest_in_year` directly
  (`routes/loan/dashboard.py:425`) -- settled-only YTD, so no user-visible disagreement with the
  Taxes tab today, but it should read the one fold-based producer.
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
- [x] **F2** `refactor(analytics): delete the dead year-end summary service` -- **SHIPPED
  `3aecceb0`.** The whole `year_end_summary_service` package + its two test files deleted (the route
  302s; `compute_year_end_summary` had no live caller). R-D's two still-live functions
  (`_compute_mortgage_interest` -> `_loan_year_interest`) RELOCATED to their only caller
  `tax_report_service` (C3c has not landed, so they move rather than die), B-19's false
  `DebtSchedule` hints fixed to the real row-list type, their unique hybrid coverage moved to
  `test_tax_mortgage_interest.py`. **Broader than R-D scoped** (the full suite caught it): four LIVE
  cross-consistency tests reached into the package internals -- repointed to the live producers, or
  the deleted year-end surface dropped from the equality checks. The obsolete pre-fence
  `calculate_balances` git-grep guard went with it (the W9906 fence supersedes it). ~12 stale
  docstring PROVENANCE mentions remain (deferred doc-sweep; not broken code). Net -6.7k lines.
- [ ] **F3** prod ship: dev -> main PR for the whole arc per the standard pipeline.

## 6. The findings ledger

Every finding from the archived audits, its status, and the step that closes it. IDs keep their
archive names so old references resolve here.

| id | finding (one line) | worst measured | status | closed by |
|---|---|---|---|---|
| B-1 | Future-origination loan posts no OPENING; seam 500s five pages when the date arrives | outage | **closed (`4e46a0a8`)** -- reproduced (configure 2026-03-20 / close 04-15 / read 05-07, no re-sync: hero AND map both raised), control fires | A3 |
| G1 | `grid.create_baseline` resynced account anchors but NOT loans, so a loan configured while its owner had no baseline (its opening posts per SCENARIO, and there was none) stayed opening-less through the one recovery path -- every loan surface 500s, with no way back short of re-saving the loan's params | outage | **closed (`4e46a0a8`)** -- reproduced (delete the baseline, POST /create-baseline, read the loan: raised); control fires | A3 |
| B-2 | Property equity chart derives debt from schedule rows; wrong on 8/13 shapes | $299,701.35 | **closed (`821dd0eb`)** -- the debt line reads `balance_at.positions()` (the fold) for the confirmed + projected tiers; the axis clamp and empty-schedule clip are gone; dev-clone live-render reconciles to the hero at today ($177,277.97) | C5 |
| B-3 | Grid renders a loan's balance RISING (cash producer on an amortizing account) | +$1,910.95/mo, unbounded | **closed (`f11382a0`)** | A1 |
| B-4 | `_forward_rows` `is_confirmed` filter had zero discriminating tests -- **measured: the filter could be deleted and all 7,401 tests passed** | $4,449.72 archived; $48,496.25 on A2's fixture; unbounded (= `last_confirmed.remaining_balance - projection_seed`) | **closed (`c96c62be`)** -- value pinned inside the only window where the filter decides anything, control shown to fire | A2 |
| B-5 | Balance sheet renders a negative liability, HTTP 200 | -$7,643.80 | **mechanism closed (`4e46a0a8`)** -- the clock-dropped opening that let a payment split against a ZERO balance is gone; reproduced on the ordinary settle path (payment settled early, due AFTER a future origination: interest $0.00 / principal $0.00 / **excess $1,073.64**, whole payment booked as a Refund Receivable and the Schedule-A interest erased -> $833.33 / $240.31 / $0.00). The linked ledger netted to exactly $0.00 -- the loan reading as owing NOTHING while the borrower's cash sat in a Refund. Invariant still open | A3 (mechanism), E1 (invariant) |
| B-6 | Taxes tab prints interest for a loan the seam refuses to value | $4,156.61 | **closed (`99cc2816`)** -- the Taxes tab reads the fold-based `balance_at.loan_interest_in_year`, which answers from source events for the settled past (no schedule fallback for a loan the posting cache cannot value), so the interest figure and the balance come from the ONE total producer; the no-opening fallback is gone, demonstrated by `test_cleared_ledger_still_answers_from_the_fold` | C3c |
| B-7, B-10 | Year-end omits a true-up payoff; spends a fabricated `jan1=0` | $255,300.26 | dead code; deletion ruled (R-D) | F2 |
| B-8 | Fail-loud misses future valuations (returns before the ledger read) | unbounded | **closed (`f410afa9` scalar; `84e386c6` map)** -- both producers now fold a broken loan from SOURCE facts for past AND future, so no fail-loud path remains at either to be inconsistent about | C3b1 (scalar); C3b3 (map) |
| B-9 / FU-7 | Projection pays down overdue installments nobody paid | -$15,755.38/period | open, reachable | D1 -> C6 |
| B-11 / FU-4 | Period before the ledger's opening renders the loan debt-free | $17,134.85 | **closed (`18fd3a04`)** -- origination is the opening now, so a pre-tracking date reads the origination principal held flat (the plateau), never debt-free; verified $0 -> $202,000/$32,402.45 on the real loans; aged-out of the /savings trend window meanwhile, closed at the producer; B2's tracking-start shape pins the plateau | C1 |
| B-12 | Unfenced producer tier below the fence; `loan_resolver` package wholly unfenced | -- | guard gap | Phase D |
| B-13 | Loan detail route answers a broken loan from the money-blind replay | $199,600.80 | **closed (`c98ea07b`)** -- the route reads `balance_at.balance_at` (the fold) now, not `LoanState.current_balance`; the route test renders a broken loan's page at the fold `$231,200.00`, never the replay `$239,761.08` (control fires) | C4 |
| B-14 | `loan_recurrence_sync` persists a payoff date off the blind walk | -- | reachable | C8 |
| B-15 | Kind-blind true-up writes a cash anchor onto a LOAN (had fired: both real loans carry rows) | -- | **closed (`f11382a0`)**; residue N-4/N-5 | A1 |
| B-16 | Horizon uses `is_paid_off` where the contract says `is_retired` | -- | latent | collapses at C3b/C4 |
| B-17 | Debt-track `is_originated` wiring unguarded (guard tests a hand-built dict) | -- | guard gap | A2-adjacent; flag deleted at C3b |
| B-18 | Cash scalar fabricates pre-anchor balances from today's anchor | -- | live | X3 |
| B-19 | False `DebtSchedule` type hints in `_income_tax` | -- | **closed (`3aecceb0`)** -- hints fixed to the real `dict[int, list]` / `list` row type on the relocation to `tax_report_service` | F2 |
| B-20 | True-up-paid-off loan shows origination as payoff date, no badge | -- | open | C8 |
| B-21 | `TestBrokenLoanFailsLoud` cash fallback asserts `is not None`, not the value | -- | **closed (`c96c62be`)** -- pinned at the $150,000.00 anchor | A2 |
| B-22 | Dead `insert_origination_event` fixture helper | -- | **closed (`18fd3a04`)** -- helper + its no-op seeds deleted; test loans now match production (origination synthesized, no stored row) | C1 |
| N-12 (B1) | **The two ledger readers disagree about when an anchor becomes visible.** `confirmed_loan_balance_at` bounds an anchor by `LEAST(entry_date, period.start)` (`_asof.effective_date`); `confirmed_loan_history_rows` bounds its non-payment events by raw `entry_date` (`_reader.py:_classify_linked_nets`). They therefore diverge for any `as_of` in `[period.start, entry_date)` -- two readers of ONE ledger, contradicting each other about one loan. Measured on the real Mortgage: on 2026-03-26..03-30 the scalar says **$178,103.41** and the history rows' last `remaining_balance` says **-$272.02** -- a NEGATIVE liability, the B-5 shape. Contained today, not by design but by two unrelated gates: a user true-up is schema-bound to `anchor_date <= today` (`routes/loan/params.py:244`), and the future-origination case is stopped by N-10's four `origination_date` predicates -- so no surface passes an `as_of` inside the window. One clock retires both bounds | $178,375.43 (the divergence; the rendered figure is a negative liability) | **closed (`eb5de4ac`)** -- the scalar now bounds anchors by `entry_date` (their own civil date), the same rule the history reader already used, so the two agree | C2 (`remaining_balance` itself dies at C6) |
| FU-1 | Van Loan history known-wrong (duplicate anchors; $452.37 unexplained step) | $897.16 | data defect | F1 |
| FU-3 | Standing overpayment resolves at today for any as-of | -- | latent | C-phase note |
| FU-5 | Settled payment into an unoriginated loan vanishes | $1,200 test case | ruled: reject at write boundary (R-C) | C9 |
| FU-8 | Empty schedule admits the whole contractual walk as back-projection | $197,049.32 class | **closed (`821dd0eb`)** -- an empty schedule now draws NO back-projection (`_back_projection_by_month` returns `{}`); the loan's real balance comes from the fold, which answers $0.00 after payoff | C5 |
| cash D1 | Settled post-anchor transactions counted by NO producer | $9,431.72/17 days | **LIVE** (the re-anchor treadmill) | X1 |
| cash D2 | Scalar is period-flat; contradicts the daily series | $999.48 on 07-16 | **LIVE** | X2 |
| cash D3 | Pre-anchor: scalar fabricates, map omits; every re-anchor rewrites the whole past | -- | live | X3 |
| cash D4 | Anchor column vs history table: divergence detected, only logged | latent | latent | X4 |
| N-1 (07-16) | Archived X0 rule would double-count early-settled transactions | 15 real pairs | plan defect, corrected | R-B / X1 |
| N-2 (07-16) | Settle-time freeze reads the clock (`loan_payment_service.py:762`) | -- | recorded risk | D3/C7 surfaces it |
| N-3 (07-16) | Escrow writes never trigger a posting sync (guard-only protection) | -- | latent hazard | E1 |
| N-4 (A1) | Pay-period reset re-anchors EVERY kind, refreshing loan cash-anchor rows (balance-preserving `stage_anchor_true_up` inside the reset's deferred-FK transaction; same-value, not user-supplied) | -- | B-15 residue | C-phase, when loan reads of the column die |
| N-5 (A1) | Account-create factory writes an origination cash anchor for every kind -- a loan created with a balance seeds the column at birth (entangled with loan onboarding) | -- | B-15 residue | C-phase |
| N-6 (A2) | `_loan_year_interest`'s `not row.is_confirmed` guard fires on NO live path today: it would only matter when the ledger answers for interest but not for the schedule, and both gate on the same `_has_opening_posting` (`_reader.py:310` vs `:152`), so `_payoff.py:285` has already swapped the replay's redistributed rows for raw-due-dated ledger rows that `settled_due_months` alone excludes. **That unreachability is a cross-module coincidence, not a structural guarantee** -- `confirmed_loan_view` ALSO returns `None` for `as_of > today` (`loan_payment_service.py:529`) where the interest reader has no `as_of` at all, so a caller passing a year-end `as_of` makes this guard the only thing between the Taxes tab and a double-count. Kept, untested: the state is unreachable, so a control would have to hand-build the rows (the anti-pattern B-17 names) | -- | **closed (`99cc2816`)** -- `_loan_year_interest` deleted whole at C3c; the fold-based producer's `not row.is_confirmed` is the always-reachable projected-rows filter (source it only from unconfirmed rows), not a dead guard, so nothing unreachable is kept | C3c |
| N-9 (A2) | **Schedule A counted a CAR LOAN's interest as home-mortgage interest.** The pre-fix `_load_debt_accounts` selected on `has_amortization` alone -- set on AUTO_LOAN, STUDENT_LOAN, PERSONAL_LOAN and HELOC as well as MORTGAGE -- so every amortizing account fed `_compute_mortgage_interest`. Personal interest is not deductible at all; student-loan interest is above-the-line, never Schedule A. Root cause is its own docstring: "Mirrors `_load_common_data`'s `debt_accounts` selection" -- one predicate answering two questions (Section 8's lesson, live) | **$5,221.16** measured on the suite's split-loan fixture; inflates itemize-vs-standard, so it can advise itemizing when the standard deduction wins | **closed (`44cbd028`)** -- `_load_debt_accounts` -> `_load_mortgage_accounts`, selected by account_type ID; HELOC deliberately excluded (use-of-proceeds unknown, documented like property tax); negative control shown to fire | own commit, found building A2's oracle |
| N-7 (A2) | The live Taxes number's only test used `_compute_mortgage_interest` as its own oracle -- a double-count inside it moved both sides and shipped green (demonstrated) | interest deduction, unbounded | **closed (`c96c62be`)** -- hand-computed live-path oracle ($500.00, paid-date basis) | A2; C3 must grade its rebuild on it |
| N-8 (A2) | ~~The loan write walk stamps postings with the WALL CLOCK, visible in test logs as `Posted anchor correction (source 6 as of 2026-07-16)`~~ **WITHDRAWN 2026-07-16 (A3): misattributed, on two counts.** (a) **Source 6 is `account_opening`** (`PostingSourceEnum` order: transfer 1, transaction 2, loan_payment 3, loan_opening 4, loan_trueup 5, account_opening 6) -- that log line is the ACCOUNT anchor path, not the loan walk. (b) That path reads **no clock at all** (`grep date.today() app/services/account_posting_service/` is empty); its `entry_date` is the assertion's own instant. The 2026-07-16 under a frozen-to-2026-03-20 suite is fixture ORDERING: `seed_user` creates Checking before `freeze_today` applies. The LOAN's anchor corrections stamp the **anchor's own date** (observed: `source 4 as of 2026-04-15` for an origination dated 2026-04-15), never the clock -- the walk's clock read was in the FILTER (which anchors were admitted), never in the stamp, and the filter is what A3 deleted. No defect here | -- | **withdrawn, no code change** | -- |
| N-11 (B1) | **A raw settled transaction typed onto a loan account moves the POSTED balance but not the fold.** Its cash leg books onto the loan's linked ledger and `confirmed_loan_balance_at` sums every linked posting with no kind filter (`_reader.py:167-176`); the reader's own classifier names the case ("a raw settled transaction typed onto the loan account", `_reader.py:623-633`). The fold cannot see it: its payment set is transfer-linked shadows only (`settled_income_shadows`). **This is the one shape where the ledger is RIGHT and the fold is incomplete**, which inverts the "postings are a stale cache" framing: someone acting on it would "repair" a genuine event away. Ruled R-E (forbid at the source). **Reachability proved BROADER than first recorded:** beyond the create routes (`create.py:78` accepted any owned `account_id`), a recurrence TEMPLATE targeting a loan (the engine copies `template.account_id`) and the SALARY-PROFILE auto-picker (found in adversarial review) each generate raw transactions onto the loan -- all three now refuse an amortizing account. B2 demonstrates the divergence is real ($300 forced) and asserts the sources refuse it. | **$300.00** measured on a probe; unbounded (any typed amount) | **closed (`dba91dc0`)** -- all three sources gated; control shown to fire | BG |
| N-10 (A3) | An anchor's read bound is `LEAST(entry_date, pay_period.start)` (`_asof.effective_date`), a period-START rule, so a FUTURE-dated origination is visible from its containing period's START. Measured: origination 2026-03-25 read on 2026-03-20 -> **$200,000.00** from `confirmed_loan_balance_at`, and the same from `confirmed_loan_balance_map` for the current period. No surface renders it: **FOUR** consumers each ask `origination_date` first -- `amortizing_balance_at`, `_build_amortizing_balance_map`, `confirmed_loan_view`, and `balance_at.loan_ledger_domain` (the 4th found by A3's adversarial review; before its guard, `confirmed_loan_ledger_domain` flipped `None` -> a real `opening_balance=$200,000.00` for an unclosed mortgage, and the year-end clamp's not-borrowed guard was left load-bearing on statement ORDER). Four predicates standing where one honest rule belongs ("a safety that is a predicate is not a safety", Section 8). Pinned in the suite (`test_seed_is_none_before_the_loan_originates` asserts the $200,000.00 leak, so C2 has a test to flip). The honest bound is the anchor's own civil date (D5/R-A), which moves history and is therefore gated on C1 (probe-proven: one-clock without the origination event reads $0 for 6 days x $178k at the Mortgage's tracking boundary) | $200,000.00 contained | **leak closed (`eb5de4ac`)** -- the reader bounds the opening by its `entry_date` (the origination), so a future origination is not yet visible and reads the honest `0.00`; the pin flipped. The four guards become redundant: #1 `amortizing_balance_at` deleted at C3b1, #2 `_build_amortizing_balance_map` deleted at C3b3, `confirmed_loan_view`'s STAYS (B-1, clock-independent), `loan_ledger_domain`'s guard SITE deleted at C3b4 (the reader is gone); the shared `_is_originated` fn STAYS (`loan_figures`/`is_retired`/`is_paid_off`) | C2 (leak); C3b1/C3b3 (guards #1/#2); C3b4 (guard #4 site) |
| N-13 (C2) | **Editing a settled payment's `paid_at` does not re-date its postings**, so since C2's settled-date clock the balance's visibility date does not follow an edited settle date. `paid_at` is not in `transfer_service._POSTING_RELEVANT_FIELDS` (it changes no leg), and the loan reconcile is leg-delta based, so a `paid_at`-only edit resyncs the account anchors but leaves the loan correction at its original `entry_date`. Harmless pre-C2 (visibility was period-based); now latent. Not a live defect on current data (paid_at edits are rare and the app has no such edit flow surfaced) | -- | recorded, out of scope | write-side (E1 / C9) |

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
* **A shared primitive reached through a private import is telling you the package boundary is
  wrong.** B1's own recipe needed four private names out of `loan_posting_service`; the fix was
  not to import them but to notice the walk was owned by the wrong package (B0).
* **An argument a caller can get wrong is a defect, not a contract.** The fold TOOK the pay
  periods its visibility rule needs, documented as "so a caller cannot fold against one period
  set and read against another" -- which was exactly backwards: nothing else took that
  argument, so it was the only way to disagree, and the grid passes a WINDOW ($150,000.00,
  measured). Load it, do not take it.
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
4. Keep the PLANNING surface small; ~500 lines is the target for it. Growth from marking work
   COMPLETED -- ticking boxes with hashes, "as built" step detail, moving findings to closed -- is
   fine and may push the file past ~500; that is the ledger doing its job, do not trim it for length.
   The limit exists to catch NEW planning/design prose accumulating (the "documents rot" lesson), not
   to cap the record of what shipped.
