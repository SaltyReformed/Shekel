# The cash balance architecture: the plan of record

**This is the ONLY live document for the balance arc, and it carries the work that REMAINS.**
Amendments are edits HERE, a shipped step gets its checkbox ticked with its commit hash HERE, and no
new planning documents get written for this arc. **It is capped at 1,200 lines and the cap is a
gate** (`tools/plan_gate/`, which also grades Section 6's owners) -- when it binds, archive a
completed span rather than trimming a live one. The rules are Section 9; read rule 6 before
recording a finding and rule 4 before adding prose.

**Four archived records hold what is already done. None of them governs anything, and none is
cited by a live sentence here** (rule 5).

| record | what it holds |
|---|---|
| `archive/loan_arc_as_built_2026-07-26.md` | The LOAN half, complete and in production (PR #64, merge `88c79857`): Phases A-F, its own rulings, and the 75 findings it closed |
| `archive/cash_arc_as_built_2026-07-27.md` | Phase X from **X-a** to **X-g3b**, in production since 2026-07-28 (PR #65, merge `69a527cd`), and the 10 findings it closed |
| `archive/phase_x_as_built_2026-08-04.md` | Phase X from **X-c2c4** to **X-f1e3**, one line per step with its verified commit hash and what it closed, plus the 6 findings closed outside a step and the 79 rulings whose work has shipped. **Section 1a holds the X-f1 cluster, COMPLETE at fourteen leaves**, including two hashes this document never recorded |
| `archive/anchor_settle_partition.md` | The anchor/settle partition arc: R-DH's six parts, steps 1-4 and S1-c as built (PRs #67 / #75 / #76), the F1-F12 review register, and the from-scratch redesign that was measured and REJECTED. **Superseded 2026-08-04**; its three surviving obligations were carried into X-f1c4 and X-d before it moved |

Everything else that ever governed this work is in `archive/`, indexed by `archive/README.md`.

## Where the arc stands

**A signpost, not a log.** It orients a reader arriving cold -- what just landed, what is in
flight, what is next -- and POINTS at the detail rather than carrying it. It is REPLACED each
session, never appended to, and the gate caps it; rule 8 says where each kind of content goes
instead.

**As of 2026-08-09, re-measured.** Production from `docker inspect shekel-prod-app`, the stamp from
`alembic_version`, branch state from `git status -sb`, the baseline from a full run. Do the same
when you edit this: the previous revision was 3 days and TWO production deploys stale, and the
staleness was invisible because nothing here is a predicate.

| | | detail |
|---|---|---|
| **just landed** | **The whole X-f1 cluster is IN PRODUCTION** -- PR #82 -> #83, merge `8d812662`, image `2ab54855937b`, migration head **`b5e3d9c1a7f2`**. Fourteen leaves. A settle carries the day its money moved, an assertion is (account, day, balance) written at one door, and the back-dated acknowledgement reaches all five surfaces | `archive/…2026-08-04.md` Section 1a |
| **how it was proven** | Rehearsed on a restored copy of prod BEFORE deploying: migrations clean, the backfill proven row by row (**999/999** accounted -- 150 dated from `paid_at`, 8 from the period-start fallback, 841 left NULL, 0 unexpected), and the seam harness **byte-identical**, positive-controlled at 1,674 lines from one cent. Post-deploy prod re-measured byte-identical against the same baseline | Section 7.2 |
| **in flight** | nothing OF THIS ARC. **Neither the live branch state nor what production runs is recorded here, and neither may be** -- read them from `git branch -vv` and `docker inspect shekel-prod-app --format '{{.Image}}'`. This row asserted "no feature branches exist" on 2026-08-08 while block 10's `C1` was being built on `feat/pay-calendar`, and it then stored a prod image digest that a deploy falsified on 2026-08-09 -- twice over, a volatile value beside no reconciler, which is this arc's own root cause worn as a signpost. The migration head is **`c7f3a9d1e864`**; nothing since has added one | -- |
| **blocked on you** | **two design questions X-f2 owns**: **N-204** (the acknowledgement is keyed on "was the day the boundary", not "did the figure change", so an ordinary re-confirm writes a row and says nothing) and **N-205** (nothing durable records an assertion anywhere in the UI) | Section 6 |
| **next** | **X-an** -> X-f2 -> **X-f3** (moves money, own PR) -> X-f4 -> X-f5 -> X-f6. Block 2 (**X-ad then X-x, ONE PR**) runs in parallel and its fork is RULED (2026-08-09, for **X-ad**): DELETE the registration bootstrap payday, and registration asks for the payday last paid on. `N-123` absorbed pay-calendar `P3`, so X-ad owns one row, not two | Section 5.0 |
| **complementary arcs** | TWO, neither part of this arc and neither pausing it. **RECURRENCE**: Half A is disjoint and starts whenever; **Half B is not one unit** -- `R6` is the same trace as X-an and ships WITH it, `R5` waits on X-f4. **PAY CALENDAR** (opened 2026-08-08, block 10): `budget.pay_periods` stores the payday and derives `end_date` / `period_index`. **Its `C2` IS this arc's `X-l`**, and also recurrence `R-F12` -- one commit under three names | `implementation_plan_recurrence_redesign.md`, `implementation_plan_pay_calendar.md`, and blocks 9 / 10 |
| **why this shape** | the anchor half was redesigned from scratch by ruling **R-EB**; R-EQ designed the duplicate rule that had only ever been re-keyed, R-ER put the day rule in the module that owns what an assertion is, X-f1e1 deleted the second DOOR, X-f1e2 the second WRITER, X-f1e3 the mount that was destroying its own message | Section 3.3, and R-EB / R-EQ / R-ER / R-ES / R-ET in Section 4 |
| **the live lesson** | **a response can destroy its own message**, and the tests could not see it: deleting either of the two attributes the feature hangs on left the whole suite green, measured by an adversarial review | Section 8's first three bullets |
| **the ledger** | **93 rows**. X-f1e3 closed N-199 and opened N-203..N-206; **N-173 re-pointed to X-f6**, because X-f1 shipped the mechanism and its backfill improved no stored date | Section 6 |
| **resuming cold** | Branch from `dev`. Whether it is ahead of `main`, and whether by documents or by code, is a MEASUREMENT (`git log --oneline origin/main..dev`) and this row no longer claims it -- the previous revision said "DOCUMENTS ONLY" and block 10's `C1` shipped `app/services/pay_calendar/` into that gap. The repo head is **`c7f3a9d1e864`** and the test template is already stamped at it -- VERIFIED by reading `alembic_version` in `shekel_test_template`, not assumed; rebuild only if you add a migration. Baseline **8,431** green (`./scripts/test.sh`, 183 s, 2026-08-09, measured on the merged `feat/pay-calendar` state); the 8,258 this row used to claim predated R7a-1 and C1. Two REFERENCE tags, neither a rebase candidate: `xd-attempt-1-parked-n155` (X-d) and `xx-attempt-1-held-rde` (X-x). This row used to name one hand-made prod restore point; **the recurrence arc's R-F8 made every deploy dump unconditionally**, so `~/shekel-backups/` now holds one per release and the deploy REFUSES a rollback its image cannot resolve | Section 7.2 |

Section 5 is the work that remains and Section 4 the rulings that govern it; `archive/` is what
already shipped. **Every open defect is a row in `../../plans/ledger.md` whose `arc` reads
`balance`**, and every step is indexed in `../../plans/steps.md` -- both moved out on 2026-08-09
because a finding is not arc-local.

## 1. The problem, in plain words

The app answered "what is this cash account's balance at time T?" in three places, three ways, and
the ways disagreed. Underneath all three was ONE root cause, the cash form of the loan side's:
**the honest balance function was PARTIAL.** The projection started at the latest anchor and summed
only still-Projected rows forward, so it could not answer a past date, could not see a settled row,
and had to be composed with a seed, a flag or a fallback at every call site -- and every
composition was a new producer that could disagree with the others. Plan step X-c2b2 replaced all
three with ONE total fold and they closed together. The modelled-asset form of the same defect
closed at X-g2b. Both records are in the archive.

**What remains:**

1. **The write side and the read side are still two statements of one rule.** The posted account
   ledger is written by its own walk while the projection folds another; they are proven
   byte-identical, which is a test keeping two implementations in step rather than a structure that
   cannot drift. Plan step **X-d**.
2. **A derived value is read as a source of truth.** **Its named instance is GONE and the SHAPE is
   not** -- the number is kept because `app/` cites it (`cash_ledger/_walk.py`,
   `status_seam.py` x3) and renumbering would break those. `Account.current_anchor_*` was a
   denormalized copy of the latest `AccountAnchorHistory` row that `resolve_anchor` detected
   diverging and only LOGGED; X-f1c3a re-pointed every reader at the resolver and X-f1c3c dropped
   both columns (2026-08-04, archived). What survives is the general rule the code cites at the
   posting walk -- **the posted ledger is a projection of the facts, never a second opinion about
   them**, which is root cause 1's other face -- plus the residue **X-e** owns.
3. **The pay calendar is a PARTIAL function -- the same shape, on the other axis.**
   `pay_period_service.get_all_periods` returns the materialized rows and stops, so past the last
   one every consumer improvises. Plan step **X-l**.
4. **The assertion RESETS the ledger instead of reconciling with it.** A true-up plugs the
   difference to Equity, so real economic activity is structurally invisible on the income
   statement: **$15,065.08 gross / -$1,495.10 net** over four months on Checking, 66% of reported
   expense. Plan steps **X-f1** .. **X-f5** (ruling R-EB).
5. **The app can now record when money moved, and the HISTORICAL dates are still guesses.**
   `paid_at` was `db.func.now()` at the click, so **88 of 135 settled Checking rows (65.2%) share a
   click-minute with another row** -- the generator of (4)'s churn. **The mechanism half SHIPPED**:
   X-f1b replaced the instant with `transactions.settled_on`, and X-f1c1 / X-f1c2 made that day
   correctable on both the transaction and the transfer door (2026-08-04, archived). The backfill
   was the deleted derivation verbatim, so **no stored date improved** -- every historical row still
   carries its click-day. **X-f6** replaces the guess with the bank's own record; that is the half
   that makes the dates true.
6. **A surface still picks which producer answers for its account.** `/dashboard`'s hero, its pulse
   and the analytics calendar read the kind-blind cash view while `/grid` and `/savings` read the
   modelled one, so the same account renders two balances for the same period. Plan step **X-j**.
7. **The read pass pins a clock it does not hand to its loaders.** `BalanceContext` fixes the
   pass's `as_of` and `scenario` and memoizes three loan derivations; every other input is loaded
   ad hoc at the wall clock, which is both a redundancy and an impurity. Plan step **X-i**.

Section 5 has the steps in execution order; Section 6 records who owns every open finding, with no
row unowned.

## 2. What is already shipped and correct (the foundation this plan builds on)

**This is the FOUNDATION the remaining steps stand on, not the log of how it was built** -- that is
the three as-built records.

| shipped | where | reference |
|---|---|---|
| The whole LOAN arc: one total fold, no partial function, no splice, no name fence | prod | PR #64, merge `88c79857` |
| `balance_at` seam (one read surface, kind-correct dispatch) | prod | PR #45 |
| Double-entry posting ledger: transfers, cash/envelopes, loan REAL-split postings | prod | PR #48, PR #51 |
| Actuals reporting (Step 5) | prod | PR #58 |
| The cash fold, total over every date and account, and the cutover onto it | prod | PR #65 |
| A modelled asset is an event stream (X-g's four steps, ten commits) | prod | PR #65 |
| An assertion is the closing balance for its civil day (R-DH (a), (b)) | prod | PR #67 |
| `transaction_entries.entry_date` splits into `purchased_on` + `settled_on` (S1-c) | prod | PR #75, migration `d7c1f4a9e603` |
| `ReconciledThrough`: the partition's fence is a TYPE, not a checker | prod | PR #76 |
| One status seam for transactions and transfers (X-aj1) | prod | PR #80 |
| A settle carries the DAY its money moved, and an assertion is (account, day, balance) written at ONE door (the whole X-f1 cluster) | prod | PR #83, merge `8d812662`, migration `b5e3d9c1a7f2` |
| An anchor correction's period is DERIVED from its day (X-ai-r) | prod | PR #81 |

**The loan baseline is still a LIVE regression gate for CASH commits.** Mortgage (account 3)
**$177,277.97**, Van Loan (account 8) **$15,663.59**. Re-derive both from the seam before and after
every commit in this phase: a cash change that moves a loan figure is wrong.

**Verified for cash, independently of the producers under test:** the fold reproduces the app's own
persisted double-entry ledger to the cent on both real accounts that carry postings (Checking
`$2,824.26` over 177 postings, Money Market `$3,659.51` over 10) -- an oracle no cash producer
participates in. Ruling R-K's grid identity holds on 360 of 360 real (account, period) pairs. Do
not trust prose figures older than their write date; pin oracles in tests.

## 3. The solution

An account's balance is a fold over its event stream. The fold is TOTAL: it cannot return `None`,
cannot raise, and answers any date -- asked about a date before every event it answers the seed,
not an error. That single property deletes the partiality and everything built to manage it.

### 3.1 The cash fold, as shipped

```text
CashEvent = (instant, kind, payload)                    -- cash_ledger._events
kind = ASSERTION  balance := anchor_balance             (AccountAnchorHistory, EVERY row)
     | ACTUAL     balance += settled_cash_leg (signed)  (settled transaction rows)

walk_cash_ledger(account, scenario) = replay(events, seeded 0.00)  -- cash_ledger._walk
dated_deltas(walk) -> [(visible_civil_date, delta)]                -- the ONE clock
```

Three grains read that one fold: a scalar at a date, a per-period map, and a daily series. A
still-Projected item whose date has passed is CLAMPED FORWARD, never absorbed by an assertion
(R-G): its effective instant is `max(its attribution date, as_of + 1 day)`, because a plan cannot
have already happened. Before an account's first assertion the fold BACK-PROJECTS over the records
and holds flat before the earliest one (R-I / R-S).

### 3.2 The modeled asset, as shipped

One sequential replay over four event kinds -- ASSERTION, ACTUAL, CONTRIBUTION, ACCRUAL -- serves
all five account kinds. An ASSERTION always wins (R-S). ACCRUAL is DAILY, computed at full
precision and credited in whole cents carrying the sub-cent remainder (R-T / R-X), and runs from
the latest assertion's own day forward, including inside the period that holds it (R-L / R-Y). A
CONTRIBUTION lands on its pay period's `start_date` and exists only when that payday is strictly
after the latest assertion (R-Z).

### 3.3 The anchor half, as DESIGNED (ruling R-EB, not yet built)

The assertion stops resetting the ledger. `balance(T)` becomes `opening equity + SUM(postings <=
T)`, and the reconciliation residual posts to Uncategorized Expense / Income rather than to
`anchor_equity`. An assertion becomes a RECONCILIATION -- a recorded observation the outstanding
set is measured against -- rather than a reset that discards what the records say. The reset
deletion and the residual classification cannot ship apart: removing the reset without a
classification path lets book and bank diverge permanently. Steps **X-f1** .. **X-f5**, with
**X-f6** (bank import) as the ruled follow-on that consumes them.

## 4. Decisions that govern the remaining work

**One line each: the RULE, not the deliberation.** A ruling whose work has fully shipped is in the
as-built record of the step that shipped it. Where a live step needs more than the rule, its
Section 5 entry restates it inline (rule 5).

| ruling | date | what was ruled |
|---|---|---|
| **R-G** | 2026-07-25 | A still-Projected item whose date has passed is CLAMPED FORWARD, never absorbed by an anchor: its instant is `max(attribution date, as_of + 1 day)` |
| **R-I** | 2026-07-25 | Before an account's first assertion the fold BACK-PROJECTS over the records, holding flat before the earliest one |
| **R-K** | 2026-07-25 | The grid's balance row and its subtotal rows are ONE step list grouped two ways, plus a named remainder |
| **R-L / R-Y** | 2026-07-25/26 | Modeled interest and ACCRUAL begin at the account's LATEST assertion's own day, for all modelled kinds |
| **R-M** | 2026-07-25, amended 2026-08-01 | The column SPLITS rather than the guard bending: `purchased_on` is the day of purchase, `settled_on` the day the bank was seen to take it |
| **R-N** | 2026-07-25 | The cutover ships FIRST; recording when money actually moved is the follow-up (X-f) |
| **R-O** | 2026-07-25 | The Reconciliation row is "Timing & true-ups", it sits in the sticky footer above the balance, and it renders whenever ANY visible column is non-zero |
| **R-S** | 2026-07-26 | An ASSERTION always wins, and before the FIRST one the balance holds FLAT, for all five kinds |
| **R-U** | 2026-07-26 | The replay owns the SEED and the history; the forward WHAT-IF surfaces keep `growth_engine` |
| **R-T / R-X** | 2026-07-26 | ACCRUAL events are DAILY, resolved in ONE sequential pass, computed at full precision and credited in whole cents carrying the remainder |
| **R-Z** | 2026-07-26 | A modelled CONTRIBUTION lands on its pay period's `start_date` and exists only when that payday is STRICTLY AFTER the latest assertion |
| **R-AF** | 2026-07-27 | The `/investment` chart's projection axis starts the day AFTER the history line's last valued date, so the two lines join BY CONSTRUCTION |
| **R-AG** | 2026-07-27 | Past the pay-period horizon, let the fold answer and RECORD the half-model rather than capping it |
| **R-AK** | 2026-07-27 | The dashboard, the pulse and the analytics calendar STAY on the kind-blind cash view; the divergence is RECORDED, and X-j is the step that rules it |
| **R-AO** | 2026-07-27 | The homeless half of the findings ledger becomes FOUR named steps, and two existing steps widen to absorb the rest |
| **R-AP** | 2026-07-27, NOT as recommended | The write-side recurrence cluster STAYS in this arc, as plan step X-k |
| **R-AQ** | 2026-07-27 | There is no DEFERRED category. Every finding is owned by a step, and a wake condition is not an owner |
| **R-AZ** | 2026-07-27 | A producer publishes only what the presentation boundary reads |
| **R-BU** | 2026-07-28 | A residual double load is SEQUENCED behind the memo that fixes it, not deferred |
| **R-CC** | 2026-07-29 | A financial STATEMENT never reports zeros for a ledger it cannot read |
| **R-CH** | 2026-07-30 | The archived drawer's figure is NAMED for what it is |
| **R-CU** | 2026-07-30 | The ledger-class rule is finding N-122 with its OWN step (X-ab), and the false claim is corrected NOW |
| **R-CX** | 2026-07-31 | X-x is scoped to what X-l cannot subsume; the degraded-vocabulary question is SEQUENCED into X-l |
| **R-CY** | 2026-07-31 | The no-current-period answer is X-v's rule EXACTLY: raise a named error, handle it once, offer a repair |
| **R-CZ** | 2026-07-31 | A requested WINDOW that is empty is navigation, not absence, and stops answering with the absence card |
| **R-DA** | 2026-07-31 | `onboarding.has_periods` means "a period covers today", the same question every other surface asks |
| **R-DB** | 2026-07-31, DEEPER than recommended | Registration STOPS creating a bootstrap pay period |
| **R-DC** | 2026-07-31 | A mid-life schedule change FILLS the hole it would leave |
| **R-DD** | 2026-07-31 | Both write-path fixes are their OWN step (X-ad), sequenced immediately after X-x |
| **R-DE** | 2026-07-31 | X-x is HELD behind X-ad: a refusal that points at a repair which does not work converts wrong numbers into a dead end |
| **R-DH** | 2026-07-31, amended at S1-c | An assertion is the CLOSING BALANCE for its civil day, and the day is the user's, not UTC. Six parts (a)-(f); (d) restated on an OBSERVED `settled_on`, so a NULL is "not seen on a statement" and is NOT reconciled |
| **R-DK** | 2026-08-02 | The posting self-heal stops being delta-keyed, which dissolves the reason `_reconcile_postings_after_update` gives for existing (N-153) |
| **R-DM** | 2026-08-02 | The checked-projection assert grades a FINISHED operation, never a half-finished one, and the commit boundary is the end state (X-ai) |
| **R-DN** | 2026-08-02 | ONE status seam, and the transition context stops being a caller's opinion |
| **R-DO** | 2026-08-02 | An illegally-drifted shadow status is REFUSED, not silently repaired |
| **R-DP** | 2026-08-02 | W9907 is DELETED, not shrunk -- the write door becomes structural |
| **R-DQ** | 2026-08-02 | The two remaining allowlist-bearing fences become their own phase (G), scheduled rather than remembered |
| **R-DR** | 2026-08-02 | `restore_transfer`'s four preconditions move to `_transfer_validation`, and X-aj1 ships as three commits |
| **R-DS** | 2026-08-02 | A status repair takes the PAIR's instant, and never invents one |
| **R-DT** | 2026-08-03 | Reversing a documented deliberate decision is its own change and is not bundled into a writer swap (N-153 stays out of X-d) |
| **R-DU** | 2026-08-03 | The posted ledger gets ONE VERB and ONE TRIGGER, on BOTH ledgers, and the row-level posting writer stops being the interface |
| **R-DV** | 2026-08-03 | A journal entry is the projection of exactly ONE SOURCE EVENT, and the EVENT owns it. An account is the SCOPE of a re-derive, never an owner |
| **R-DW** | 2026-08-03 | A transfer's entry has ONE valuation; a broken Transfer Invariant 3 becomes an ASSERT failure rather than a write oscillation |
| **R-DY** | 2026-08-03 | The source identity is an EXCLUSIVE ARC of typed FKs with an AT-MOST-ONE check, never an exactly-one check |
| **R-EA** | 2026-08-03 | An anchor correction books in the pay period CONTAINING the day it asserts, DERIVED through the one function both ledgers share -- never read from the source row's stored `pay_period_id` |
| **R-EB** | 2026-08-03 | The anchor half is redesigned from scratch: the cash ledger is SUM-OF-POSTINGS, an assertion is a RECONCILIATION not a reset, the residual posts to Uncategorized Expense / Income, and bank import (X-f6) is the sequenced follow-on rather than an alternative |
| **R-EC** | 2026-08-03 | `transactions.paid_at` is REPLACED by `transactions.settled_on`, not joined by it: the column stores the CIVIL DAY and the click instant is deleted |
| **R-ED** | 2026-08-03 | The settle day is EDITABLE on a finalised row, and it is POSTING-RELEVANT |
| **R-EE** | 2026-08-03 | The settle door stays ONE CLICK, and the true-up form gets its own statement date |
| **R-EF** | 2026-08-03 | The settle-day door ships on BOTH forms, because the transaction door alone corrects NOTHING |
| **R-EG** | 2026-08-03 | A settle day submitted alongside a REVERT is dropped at the door, not refused |
| **R-EH** | 2026-08-03 | `accounts.current_anchor_balance` and `current_anchor_period_id` are DELETED |
| **R-EI** | 2026-08-03 | The true-up editor's statement date goes on a SECOND LINE |
| **R-EJ** | 2026-08-03 | A settle day in the FUTURE is refused, at the seam |
| **R-EK** | 2026-08-04 | The resolver's history/projection cut is a PROXY for the day the money moved; replacing it is X-an, not X-f1 |
| **R-EL** | 2026-08-04 | A settle day BELOW the schedule is refused, at the same seam and by the same bound an anchor observation already uses |
| **R-EM** | 2026-08-04 | The four "no current period" fallbacks ASK THE SEAM; they do not read a stored balance |
| **R-EN** | 2026-08-04 | The C-17 optimistic lock leaves the cash true-up, because the true-up stops writing the row it locked. **The ruling STANDS and its RATIONALE was corrected (N-190)**: "a second tab overwrites nothing" is true of one table in a transaction that mutates three, and the deleted column had been serialising the reconcile by accident. Replaced by one per-user advisory lock inside the reconcile, both ledgers, all four entry points |
| **R-EO** | 2026-08-04 | `account_anchor_history.pay_period_id` is DELETED. An assertion is (account, day, balance) and nothing else |
| **R-EP** | 2026-08-04 | "As of when was this balance asserted" gets ONE source: the assertion's own `observed_on` |
| **R-ER** | 2026-08-04 | The rule that a civil day is ASSERTABLE belongs to the module that owns what an assertion IS (`anchor_service`), now that a second writer asks it -- and **"the user has a pay schedule at all" SPLITS OUT of it** as account creation's own precondition, because ruling R-EO falsified the reason it gave and the live one is the opening's posting reconcile (**N-192**) |
| **R-EQ** | 2026-08-04, DESIGNED FROM SCRATCH rather than re-keyed; **horizon SHARPENED at the build** | **An assertion is refused only when it changes nothing.** The duplicate-submit rule is a comparison against the assertion GOVERNING THE DAY THE SUBMISSION ASSERTS, made in the write door under the per-owner lock that door already takes, on BOTH anchor tables; the content-keyed unique indexes are DELETED. Idempotency is a property of a REQUEST, not of the row's contents, so a content key must mis-classify either a retry or a deliberate re-assertion -- and the two errors are not the same size. **The horizon is the rule, not a detail of it**: this ruling first read "the account's CURRENT latest assertion", which has the index's fault mirrored -- a submission for an EARLIER day can never equal the latest, so every back-dated retry appends a permanent row |
| **R-ET** | 2026-08-05 | **Feedback about a WRITE mounts where no refresh region owns it; a caption about STATE stays with its surface.** The two had shared one element, so the transient fact inherited the durable one's per-surface mounting and was destroyed by the durable one's own refresh. Corollary ruled at the same build: **an affordance that cannot succeed is DELETED, not given a nicer refusal** -- a loan's balance cell renders read-only rather than answering a designed error |
| **R-ES** | 2026-08-05 | **`account_anchor_history.notes` is DELETED and the origination assertion goes through the SAME write door as every later one**, so the table has ONE writer. An assertion is (account, day, balance) and nothing else -- the sentence ruling R-EO already wrote into the model. Measured: no code in `app/` READS the column (AST census), 76 of 78 production rows are NULL, and it labels the origination on 2 of 9 accounts. It is a SECOND answer to a question the app already decides positionally (`CashAnchorFact.is_opening` -> `account_opening` / `account_trueup`), and the forensic trail it was nominally for is `system.audit_log`, which records every INSERT with the full row and the acting user. The loan twin's typed `source_id` STAYS: that one is read |

## 5. The steps

### 5.0 The order of execution

**A SCHEDULE, and every position is forced by a stated fact rather than a preference.** A block
starts when its gate clears, not when the block above finishes. The phase headings below group the
same steps by SUBJECT; this groups them by TIME. **The blocks PARTITION Section 6**: all 93 open
findings are owned by a step in exactly ONE block, save the three the closed owner vocabulary sends
outside the schedule (one `operator`, two `developer-decision`). Measured, not asserted -- and the
number **read 88 against a 93-row table until 2026-08-08**, drift the gate could not see because it
grades the "stands at N rows" sentence in Section 6 and not a prose count here.

| # | block | the fact that forces its position | cost |
|---|---|---|---|
| 1 | **the anchor half** -- X-an, X-f2, **X-f3**, X-f4, X-f5 (X-f1 COMPLETE, archived) | The only remaining work that moves a figure the developer reads: **N-171**'s `$15,065.08` gross / `-$1,495.10` net stays invisible on the income statement until X-f3, which is its OWN PR and MOVES MONEY | 28-35 h |
| 2 | **X-ad then X-x, shipped as ONE PR** | **Its gate CLEARED 2026-08-05** when the X-f1 cluster reached production (`8d812662`), so this block can start now and runs in parallel with the rest of block 1. `accounts.current_anchor_period_id` is GONE rather than going, which is the fact X-ad's trace turns on. X-x is HELD behind X-ad (**R-DE**) and its 2026-07-31 build is a REFERENCE tag, not a branch | 12-16 h |
| 3 | **the posting restructure** -- X-ai-a/b/c/g, X-d re-land, X-ai-s, X-aj2, X-am, X-ak | **X-ai-s is HELD pending X-f3**; X-d is PARKED on **N-155**, whose fix is X-ai's placement; X-ak carries **N-193**, a reproducible unhandled 500 on a money route. **29 of Section 6's 93 rows live here** -- a third of the whole ledger (X-ai 13, X-ak 9, X-aj2 4, X-aj 1, X-d 1, X-am 1) | 31-42 h |
| 4 | **the credit-card arc** (own document) | `CC1b`'s fold is specified against the reset semantics **R-EB deletes at X-f3**, and `CC3b` derives a settle from `paid_at`, **deleted at X-f1b**. Earliest correct start is after X-f4 and X-am | 74-104 h |
| 5 | **X-f6, the bank import** (own document) | Consumes X-f2's outstanding set and X-f3's residual path (**R-EB**). After block 4 so ONE matching rule covers checking and card rows rather than being widened into them later | 22-31 h |
| 6 | **the read-path residue** -- X-y, X-i1, X-i2, X-j, X-k, **X-l**, X-m, X-n, X-e, X-p, X-ab, X-ac | Nothing blocks on it and its footprint is disjoint from the write path (tag `xd-attempt-1-parked-n155`'s 30 `app/` files against tag `xx-attempt-1-held-rde`'s 26: **zero overlap**, measured). **UNGATED, which is what lets `X-l` run early** -- and it must, because it is block 10's `C2` and recurrence `R-F12` as well. `X-k` is the one member with an order: it follows `R5`, which waits on X-f4 | 50-70 h |
| 7 | **the gate and vocabulary residue** -- X-ag, X-ah, X-al | Shares files with nothing; interleaves anywhere | 11-15 h |
| 8 | **E2 and G** | Runs LAST by ruling; G2 must not begin before the boundary it rests on is proven | 28-44 h |
| 9 | **the recurrence redesign** (own document) -- **NOT part of this arc** | Its **Half A** (`R1`-`R4`, `R7`-`R8`) overlaps every live block by ONE file (`_recurrence_common.py`, against the `xd-attempt-1-parked-n155` tag), so it interleaves anywhere and pauses nothing. Its **Half B is NOT one unit**, corrected 2026-08-08: **`R6` ships WITH X-an** (it overlaps 4 of 4 of X-an's surfaces and is the same loan-date trace), while **`R5` waits on X-f4**, three steps later with X-f3 between them. `R5` also collides with **X-k**; see that step | own document; not measured against this arc's history |
| 10 | **the pay calendar** (own document) -- **NOT part of this arc** | Opened 2026-08-08 out of the recurrence arc's F-10: `budget.pay_periods` stores the payday and DERIVES `end_date` / `period_index`, so a gap, an overlap and an index out of date order all stop being expressible. **Its `C2` IS this arc's `X-l`** (block 6) and also recurrence `R-F12` -- one commit, three names, and whoever builds it must satisfy all three specs, including this arc's N-82 / N-128 / N-79-far and `C2`'s own P14 (a calendar may not be constructed from a partial window; the sibling shape is measured in-repo at `$150,000.00`). `C1` **SHIPPED 2026-08-09** (`f9d148fe`, branch `feat/pay-calendar`, not yet PR'd): the derivation exists, byte-identical on all 61 live rows, and nothing calls it. `C3` shares only a DERIVATION with recurrence `R7c`, no file -- **but it COLLIDES with `X-ad`**: that arc's `P3` and this arc's `N-123` are ONE defect with OPPOSITE remedies, and the two documents did not say so until 2026-08-09 (see `X-ad`). **Owns no Section 6 row and never will** | own document; not measured against this arc's history |

**Four blocks leave this document and keep only their row here**:
`docs/plans/implementation_plan_bank_import.md` (X-f6, not yet written), the existing
`docs/plans/implementation_plan_credit_card.md`,
`docs/plans/implementation_plan_recurrence_redesign.md` (block 9) and
`docs/plans/implementation_plan_pay_calendar.md` (block 10). The first two are FEATURES
consuming this arc's output; the last two are SEPARATE ARCS, one sharing files with two of this
one's steps and one sharing a whole STEP with it (`C2` = `X-l`). None is a correctness fix inside this arc, so rule 1 is not weakened. **Block 9 owns no
Section 6 row and never will** -- a recurrence defect is a row in its own plan, which is what keeps
the partition claim above true. **The card plan's ratified sequencing is
DISCHARGED, not pending, and its order must never be re-read as a live gate**: every balance-arc
step it names has SHIPPED -- `C8`/`C9`, `D1`-`D3` and old `X1`-`X3` all resolve in archived records
(the last closed by X-c2b2), and old `X4` survives here as X-e. What blocks the card arc is NEW and
post-dates that 2026-07-19 ruling: R-EB. The row above is the gate.

**The costs are measured against this arc's own history, not estimated** -- X-a (`929b3a72`,
07-25) to X-f1c4c (`5fc22bba`, 08-04) is **98 in-session hours over 10 working days** and ~55 steps.
**They assume Section 6 stops growing, and it does not.** It grew 41 -> 104 between 07-27 and
08-04, fell to **88** across the X-f1 tick pass and X-f1e1, and is back to **93**: the X-f1
cluster's last three leaves closed 3 findings and opened 9. **The fall was not sustained.** Every
one of the 9 came from an adversarial review rather than from the build, which is the cost of
reviewing properly and is not a reason to review less -- but the cost estimates above assume a
flat ledger and have never once seen one.

**How to read the step IDs.** A suffix is a DECOMPOSITION of the step before it, appended when a
step splits. **IDs are append-only and nothing is renumbered for readability** -- they are cited in
commit messages, in code comments and in Section 6's owner column, and the gate reads this section
to grade those owners. A DECOMPOSED parent ticks with the last of its leaves.

**Shipped steps are one line each in `archive/phase_x_as_built_2026-08-04.md`** (Section 1 through
X-f1b, Section 1a for the X-f1 cluster) and are not repeated here. The three below stay because a
live step or a live finding still reads against them.

* [x] **X-ae** `fix(app): a submitted digit string is parsed, not predicated` -- PR #79, merge
  `a778703f`. Closed N-136, N-140, N-141, N-143; opened **N-139** (X-ag) and **N-142** (X-ah).
* [x] **X-af** `test(periods): the fixtures build their window on the USER's clock` -- PR #77,
  merge `dbee3812`. Test-only; closed the merge-gate half of N-137 and opened **N-138**.
* [x] **X-aj1** the status seam merge, in three commits (`1688f508` R-DR's extraction, `63514efc`
  R-DN's merge, `1e75d0ce` R-DO's refusal + R-DS's restore half) -- PR #80, merge `dde107f6`.
  Closed **N-146**; opened **N-149**..**N-152**. `transfer_service.py` lands at 987/1000, which is
  the headroom X-d needs and which **N-152** says is not a solution.
### Phase X -- the anchor half (ruling R-EB; runs FIRST)

- [ ] **X-f** `feat(transactions): the app records when money moved` -- the DECOMPOSED parent,
  carrying **N-42**. Its original scope (shrink the reconciliation row) was **redesigned from
  scratch 2026-08-03 on ruling R-EB** and now ships as X-f1..X-f5, with X-f6 as the ruled follow-on.
  It ticks with the last of them.

* [x] **X-f1** `feat(transactions): a settle carries the day the money moved` -- absorbs **S2-b**.
  **COMPLETE: all fourteen leaves shipped, and the whole cluster is CONDENSED into
  `archive/phase_x_as_built_2026-08-04.md` Section 1a** (rule 5), one row per leaf with its verified
  hash and what it closed. `transactions.settled_on` REPLACES `paid_at`, the day is editable on both
  doors, an assertion is (account, day, balance) written at ONE door, and the back-dated
  acknowledgement reaches every surface. **It did NOT close N-173** -- the backfill was the deleted
  derivation verbatim, so no stored date improved; that row re-points to **X-f6**, which is what the
  arc's own Section 1 point 5 always said makes the dates true.
  **Two things it established stay LIVE here, because later steps read against them** (rule 5):
  **(a) a row is settled if and only if it carries a settle day** -- enforced STRUCTURALLY, not by a
  fence, because `status_seam.apply_status_change` is the single door that writes `status_id` and it
  writes the day in the same call. A `CHECK` cannot express it (the predicate lives in `ref.statuses`
  and a constraint cannot join). A reader that finds a settled row with no day FAILS LOUD rather than
  falling back, because silently dropping such a row from the fold is silent money loss.
  **(b) the seam's stamping rule**: `display_today()` on FIRST entry to the settled band, PRESERVED
  on re-entry, which is what stops archiving a payment from re-dating its money. **X-f2** puts the
  STATEMENT date there instead of the stamp, and **X-an** keys the loan resolver's history cut on the
  stored day.
* [ ] **X-an** `fix(loan): a payment is history from the day its money moved` -- closes **N-187**.
  Ruling **R-EK**, sequenced immediately behind X-f1 because X-f1 is what gives the app a stored,
  user-correctable day to key on. **The resolver's replay/projection cut is the last place in the
  loan half that PROXIES the day the money moved**: `is_confirmed_payment_eligible` bounds on
  `period_start <= as_of` while the posted ledger dates the same payment from `settled_on`. The step
  moves the CUT onto `settled_on` for a CONFIRMED payment, so both producers share ONE definition of
  "already happened" and the parallel run can assert at every boundary again. A PROJECTED payment
  keeps the pay-period start: its cash has not moved, so the pay period IS the plan. It DELETES a
  proxy rather than adding a guard, and it does not make the resolver read the ledger. Its trace
  owes: the complement at `_build_monthly_override:174` must move with the cut, or replay and
  projection stop being exact complements.
  **THE RECURRENCE REDESIGN'S HALF B SHIPS WITH THIS STEP** -- `R5` / `R6` of
  `docs/plans/implementation_plan_recurrence_redesign.md` (its section 0). They are this step seen
  from the other side: this one asks which date decides a payment ALREADY HAPPENED, `R6` asks which
  date IS the contractual installment, and both rewrite the neighbourhood of
  `loan_loaders.loan_payment_due_date` / `rate_period_engine.monthly_due_date`. `R6` DELETES
  `loan_params.payment_day`, the argument this step's fallback path reads. Measured 2026-08-05:
  **4 of 4** of this step's surfaces (`loan_ledger/_events.py`, `loan_loaders.py`,
  `loan_resolver/_payoff.py`, `rate_period_engine.py`) sit in that plan's `R5`+`R6` file set.
  Tracing them apart means tracing the loan half's date semantics twice. **Its own trace is owed
  BEFORE either lands**: `payment_day` is set to the CASH day on the live Van Loan while every loan
  surface reads it as the INSTALLMENT day, which is why the two dates cannot differ today.
* [ ] **X-f2** `feat(accounts): the true-up is a reconciliation` -- R-DH (f)'s second half. The
  outstanding set covers TRANSACTIONS as well as entries (`_outstanding_scope`'s transaction twin,
  `entry_service.py:819`), ticking stamps the STATEMENT date rather than `now()` on the `settled_on` R-M split out, and the
  form shows
  the difference before it is saved. **No figure moves**: this records facts and changes no
  producer. The developer's existing workflow already IS this loop; only the recording changes.
* [ ] **X-f3** `feat(cash): the ledger is sum-of-postings and the residual is classified` -- **THE
  CUTOVER. MOVES MONEY. OWN PR, NO BACKLOG.** The assertion stops resetting the ledger
  (`cash_ledger/_walk.py:300`), `balance(T)` becomes `opening equity + SUM(postings <= T)`, and the
  reconciliation residual posts to Uncategorized Expense / Income instead of to `anchor_equity`.
  **The reset deletion and the classification cannot ship apart** -- removing the reset without a
  classification path lets book and bank diverge permanently. Ship-gated on the R-DH (c) invariant
  becoming a TEST that passes without a true-up, in both orders. Closes **N-171**, **N-172**,
  **N-174**.
* [ ] **X-f4** `refactor(cash): delete what the cutover orphans` -- `ReconciledThrough` and its 78
  references across 14 files, `account_posting_service/_anchors.py`, the correction machinery, the
  R-I seed compensator. Byte-identical by construction; the baseline harness is the gate. **State
  the deletion of `_posted_only_key_period_id`'s defensive branch explicitly** -- it has fired in
  production (**N-176**) -- rather than letting it go unnoticed. Closes **N-176**, and takes
  **N-161**, **N-169** and **N-170** with it by deleting the family they are properties of.
  **GATES the recurrence redesign's `R5`** (the `transactions.due_date` / `transfers.due_date` ->
  `occurs_on` + new `due_on` split): `R5` edits `cash_ledger/_events.py`, which is inside this step's
  `ReconciledThrough` deletion set, so renaming a column in a file this step is deleting from is
  avoided by ordering rather than by merge.
* [ ] **X-f5** `fix(ledger): the opening equity account holds only the opening` -- one balanced
  entry: debit Uncategorized Expense **$1,495.10**, credit Checking Anchor Equity **$1,495.10**,
  leaving exactly the **-$689.16** opening credit. Verified to the cent against ledger account 30's
  97 posted legs. Developer approved 2026-08-03. This is QuickBooks' documented Opening Balance
  Equity procedure and it makes the four-month income statement honest.
* [ ] **X-f6** `feat(import): the bank says when money moved` -- **RULED as the follow-on, not an
  alternative (R-EB).** A bank import (OFX / CSV / Plaid) is the only thing that removes the date
  guess without asking the user anything. **It CONSUMES X-f1..X-f5 rather than replacing them**: an
  import yields bank-dated facts that must be MATCHED against budgeted rows, and the unmatched
  residue still needs classification -- exactly X-f2's outstanding set and X-f3's residual path, fed
  automatically instead of by hand. **Its first act is a trace, not code**: which import surface,
  what matching rule, and what a match does to `settled_on`. Opens after X-f5 ships.

### Phase X -- the posting restructure

* [ ] **X-ai** `refactor(posting): the posted ledger gets one verb and one trigger` -- the END
  STATE R-DM named and **R-DU** ruled, on BOTH ledgers. **The row-level posting writer stops being
  the interface**: you do not tell the ledger "this row settled", you tell it "account A in
  scenario S changed; re-derive it from its facts". **R-DV** then fixed what OWNS an entry: an
  entry is the projection of exactly ONE source event and the event owns it; the account is the
  scope of the loop, never an owner. That answer makes N-161 and N-162 die by construction and
  removes the write OSCILLATION account-owned entries would have created. Its correction half already books in the period CONTAINING the day it asserts (**R-EA**, shipped
  at X-ai-r). Carries **N-144**,
  **N-153**, **N-155**, **N-157**, **N-158**, **N-160**, **N-161**, **N-162**, **N-163**,
  **N-164**, **N-165**, **N-166**, **N-167**. Requirements its leaves must satisfy, each from a
  review rather than from the design: the verb's source set is the UNION of the walk's facts and
  the ledger's already-posted source links (**N-162**); the verb returns the `(account, scenario)`
  pairs its emitted legs touched and the caller re-enqueues them with a stated termination argument
  (**N-165**); a per-`(account, scenario)` advisory lock is taken inside the verb (**N-166**); and
  the registry-scoped commit-boundary grader is BLIND to bulk `UPDATE` / `DELETE` across 20 call
  sites, which **X-ai-g** classifies rather than forbids (**N-163**).
  * [ ] **X-ai-a** the cash verb, built from R-DV's sentence with N-162's, N-165's and N-166's
    requirements in it from the start.
  * [ ] **X-ai-b** the trigger: the commit-boundary grader, drained from the registry the writers
    populate.
  * [ ] **X-ai-c** the loan side onto the same verb.
  * [ ] **X-ai-g** the bulk-statement census: each of the 20 sites is proven unable to touch a
    posted row, routed through a writer, or named in the one docstring that states what the grader
    cannot see. **The remedy is NOT a checker forbidding bulk statements** -- several are
    legitimate and performance-critical.
  * [ ] **X-ai-s** the `journal_entries` migration -- source identity as an EXCLUSIVE ARC of typed
    FKs with an AT-MOST-ONE check (**R-DY**), plus the reversal linkage **N-167** asks for. **HELD
    pending X-f3**: it buys per-assertion attribution for the correction family X-f3 deletes, and
    running it first would ship a migration and a backfill for something about to be removed.
* [ ] **X-d** `fix(cash): the posted account ledger is a checked projection` -- E1a's shape for
  cash. The posting writer consumes X-a's walk instead of its own, and the per-visible-date assert
  (`sum(postings) == fold(ACTUAL events)`) makes a stale posting a detectable, repairable cache
  inconsistency. **Its ship gate is ALREADY MEASURED AND CLEAN -- do not re-run it as if it were
  open.** Swept on the 2026-08-01 production clone, in both directions and positive-controlled:
  **0** entries with both concrete FKs NULL and a non-correction source kind, and **0** whose
  `transaction_id` resolves to a missing, soft-deleted or non-contributing row -- against 170
  transaction-linked, 19 transfer-linked and 128 both-NULL entries (every one of the 128 a
  correction) proving the joins bite. **So no F1-class human decision waits on today's data**; that
  is a fact about the data, not the mechanism, and the step must still decide whether the residue
  arm's defence moves to the checked-projection assert or is ceded. **It also inherits
  `ledger_report_service/_attribution.py`'s two duplicate date loaders**, whose `duplicate-code`
  disable named the shipped step 3 as its resolver: X-d deletes their twin rather than extracting a
  third shared home, which is why extracting one now would be scaffolding for a caller X-d removes.
  Both carried from `anchor_settle_partition.md` at its archive move. **PARKED at tag
  `xd-attempt-1-parked-n155`** (six commits, one deliberately RED, off PR #80; the branch
  `feat/xd-checked-projection` was deleted 2026-08-04 once the tag was pushed, because X-f4 deletes
  `account_posting_service/_anchors.py` and the correction machinery that branch conflicted in, so
  the code is a REFERENCE for the re-land and never a rebase candidate) **and nothing there is for
  merge.** The park is
  **N-155**, the step's own assert: it compares an account's WHOLE ledger against its WHOLE
  source-row walk but rides on the PER-ROW write path, so any operation settling N rows one at a
  time grades a half-finished state and REFUSES the write -- three confirmed production defects,
  one reproduced twice independently. The placement is X-ai's, not a patch at each loop. Carries
  **N-135** (wrap both bare fact fields in their own types). Re-lands after X-ai.
* [ ] **X-aj** `refactor(status): one status seam, and the fence is structural` -- rulings
  **R-DN**, **R-DO**, **R-DP**; carries **N-145**. Its merge half shipped as X-aj1 above.
  * [ ] **X-aj2** the structural write door and the DELETION of W9907 (**R-DP**), carrying
    **N-149**, **N-151**, **N-185** and **N-188**. Its trace decides the three candidates R-DP
    names and must rule what a row may be BORN as: today `create_transfer` runs no legality check,
    so "every row is born Projected and every other status is a verified transition" is available
    and makes the constructor question disappear -- but it would refuse creations the tests
    currently make, one of which (Received) is not in the transfer map at all, so the refusal would
    be correct and the test wrong. That is a behaviour change on a creation path and gets its own
    worked ruling, not a build decision.
* [ ] **X-ak** `refactor(transfers): a shadow inherits its parent's fields by ONE rule` -- closes
  **N-148**, **N-150**, **N-152**, **N-156**, **N-159**, **N-170**. **Root: the transfer -> shadow
  mirror is written THREE times and the three already disagree** -- at construction, per-field on
  edit, and again as drift repair, with `scenario_id` mirrored at construction and absent from the
  drift-repair list although that function's docstring claims it re-syncs every mirrored field.
  Transfer Invariants 3, 4 and 5 are `CLAUDE.md` CRITICAL invariants and they currently rest on
  three lists staying in step by memory. Not a live money defect on today's data, which is exactly
  why rule 7 applies. **Its scope is REVERSED by N-150**: rule the stored COPY first (remove it,
  make it structural at the database, or keep it with the cost stated), because unifying the
  copiers while the copy stands makes the denormalization cheaper to maintain. Sequenced after
  X-aj. Also owns **N-152**'s and **N-156**'s answer: an over-ceiling module becomes a PACKAGE
  rather than being shaved again.

### Phase X -- the read-path residue

* [ ] **X-x** `refactor(balance): one pay-calendar precondition, one answer` -- closes **N-116**,
  **N-125**, **N-126**, **N-129**. Rulings **R-CX**..**R-DA**. X-v's sibling one axis over, and its
  own trace inverted the row's premise. **The census replaces the row's count**: an AST pass that
  taints period-valued expressions and reports only ABSENCE tests measures **96 branches in 49
  `app/` files** plus 8 in Jinja, resolving to about 50 distinct answers. They are FIVE questions,
  not one: any periods at all (Q1), which period contains today (Q2), which contains date T (Q3), is
  there a next one (Q4, a normal terminal state), is the requested window non-empty (Q5,
  navigation). **Q1 is unreachable for an owner and Q2 corrupts money**: a 5-day calendar hole moves
  `/savings` net worth by **+$3,228.55**, while `/grid` renders the repair card at the same instant.
  (That measurement ALSO put `Account.current_anchor_balance` on screen as a current balance; **that
  half is now impossible** -- ruling R-EH dropped the column at X-f1c3c, in production since
  `8d812662`. The net-worth figure stands; the substitution does not.)
  **HELD behind X-ad** (ruling R-DE), because N-127 measures the repair its refusals point at as
  non-functional.
  **Its 2026-07-31 build is a REFERENCE, not a rebase candidate.** Tag
  `xx-attempt-1-held-rde` (branch `wip/x-x-held` deleted 2026-08-05 once the tag was pushed, the
  `xd-attempt-1-parked-n155` precedent). Measured at tag time: 39 behind `main`, 3 ahead, 38 files,
  and a test merge conflicts in **7 files across 10 hunks**. Three findings force the re-derive
  rather than a rebase: **one third of it already shipped** (X-x2's anchor-cache arm, below); **its
  reasoning is falsified in at least four places** that assert `accounts.current_anchor_period_id is
  NOT NULL` as a load-bearing guarantee, and that column is gone too; and **one conflict is SEMANTIC**
  -- in `savings_dashboard_service/_projections.py` the branch DELETES the no-current-period fallback
  because `ctx.current_period` is no longer nullable, while `main` (ruling **R-EM**, shipped at
  X-f1c3a) KEEPS the branch and asks the seam for the balance at `ctx.balance_ctx.as_of`. Both cannot
  stand, and choosing is a design decision rather than a merge resolution.
  * [ ] **X-x1 THE ONE ANSWER** (R-CY) -- `PayCalendarGapError`,
    `pay_period_service.require_current_period` / `covers`, the application-level handler and its
    repair page, on the `require_baseline_scenario` / `BaselineMissingError` pattern name for name.
    **It takes the GRID's two pre-checks as its first callers** rather than shipping a door nobody
    walks through: an unreachable handler has no negative control.
  * [ ] **X-x2 THE FABRICATIONS** (R-CY) -- the branches that publish a figure the app did not
    compute take the raising accessor. **Its first arm is DONE and must not be rebuilt**: the
    anchor-cache substitutions died with the column at X-f1c3c (ruling R-EH), and an AST pass over
    `main` finds no live read left -- only prose in 5 `app/` files. What REMAINS, re-measured against
    `8d812662`: the fabricated `$0.00` in four producers, and `build_trend_periods`'
    `current_index = 0` into an empty list, still live at `routes/accounts/detail.py:228` and
    `analytics_view.py:485`.
  * [ ] **X-x3 THE ONE PREDICATE** (R-DA) -- `onboarding.has_periods` asks Q2 rather than Q1, so the
    checklist and the page it renders on cannot disagree.
  * [ ] **X-x4 THE STATES SPLIT** (R-CZ) -- an empty requested window stops answering with the
    absence card, and the card's copy stops naming two states.
  * [ ] **X-x5 THE HARNESS** -- delete `verify_savings_producers.py`'s dict-or-attribute `_get`
    reader and the two-spelling readers its own docstring says X-x deletes. Verified dead by RUNNING
    it against the tree.
* [ ] **X-ad** `feat(periods): the pay calendar a new user can actually enter` -- closes **N-123**,
  **N-124**, **N-127** (rulings R-DB, R-DC, R-DD). **The WRITE half of X-x's trace, and its own step
  because it moves money-adjacent state**: it creates periods and re-anchors accounts where X-x only
  reads and deletes. **It RUNS FIRST, and the pair ships as ONE PR** (Section 5.0 block 2). R-DD had
  sequenced it immediately AFTER X-x; ruling **R-DE** then held X-x behind it, which reverses the
  order -- the later ruling governs, and this sentence used to carry the earlier one.
  **Registration stops creating a bootstrap pay period** (R-DB): measured, `today+1` /
  `+5` / `+13` are REFUSED and `today+20` / `+27` are accepted leaving a permanent hole, so the
  bootstrap either blocks the user's real payday or guarantees the state X-x's readers refuse to
  answer. **The FK guarantee it had to replace is GONE, not going** --
  `accounts.current_anchor_period_id` is why the bootstrap exists, and ruling R-EH dropped that
  column at X-f1c3c, in production since `8d812662`. So the fork is no longer "what will replace it"
  but "nothing replaces it, and what does the bootstrap exist for now": answer that from the tree,
  and note that `reconcile_account_anchor_corrections` still raises for an owner with no pay periods
  (**N-192**), which is the live reason the bootstrap has not simply been deleted.
  **This step and the pay-calendar arc's `C3` are TWO REMEDIES FOR ONE DEFECT, and neither document
  said so until 2026-08-09.** N-123 IS that arc's row `P3`. R-DB answers it by DELETING the
  registration bootstrap payday; `C3` answers it by KEEPING the bootstrap and letting the owner's
  real payday sit beside it, which the payday model makes legal with no code at all. Whichever runs
  first decides for both, and it also binds pay-calendar `C4`, whose `P8` write-door invariant is a
  `PaySchedule` write inside `auth_service.register_user` -- the function this step rewrites.
* [ ] **X-y** `refactor(balance): the baseline decision that is not the balance seam's` -- closes
  **N-117**. The fifteen surfaces that resolve the baseline DIRECTLY (`get_baseline_scenario`)
  rather than through a `BalanceContext`, and so answer this state without the seam being asked:
  two grid fragments and the carry-forward preview (400), template and transfer generation (a silent
  commit that generates NOTHING), `salary/profiles` (a flash telling the user to register a new
  account -- a competing repair story, wrong since `/grid/create-baseline` shipped),
  `period_population` (`return 0`), `spending_report_service` (`return None`), both posting syncs (a
  silently narrowed scenario set), and `escrow_rates` / `loan/params` (which hand a NULLABLE id to a
  query). **X-v deliberately did not reach these** (R-CC took only the two that fabricate a
  financial statement); the rest are a different question one tier down -- what may a WRITE do
  without a scenario -- and deserve their own answer. Sequenced after X-x.
* [ ] **X-i** `refactor(balance): one read pass, one derivation, one clock` -- closes **FU-3**,
  **N-14**, **N-40**, **N-56**, **N-72**'s second half, **N-89**, **N-91**, **N-92**, **N-93**,
  **N-115**. **Nine rows, one root cause:** `BalanceContext` pins the pass's `as_of` and `scenario`
  and memoizes three LOAN derivations, and every OTHER input is loaded ad hoc at the wall clock. The
  arc solved this for loans at D-ctx and stopped there. Two symptom families the ledger filed apart
  are one defect. RECOMPUTED: the calendar loaded three times per modelled grid render, the
  contribution feed at ~9.4 ms per investment account with no cache, the modelled base built 14
  times for 4 accounts on one `/savings` render, `contractual_schedule_from_origination` twice on the
  property page. UNPINNED: the employer-match gross resolving at an implicit `date.today()`,
  `live_amount_overrides` calling `date.today()` inside the pinned fold, and the standing overpayment
  read off the CURRENT template row whatever date the pass is pinned at.
  * [ ] **X-i1 THE MEMO** -- additive, byte-identical on both databases. The context gains the input
    tier the loan derivations already have, through the SAME `_memoize_once` mechanism rather than a
    second one: the calendar, the per-account contribution feed, the override map, the standing
    extra, the contractual schedule. Every loader keeps the clock it has today, so no figure can
    move and the harness is the proof. **Its tier is WIDENED by N-115** (ruling **R-BU**, which SEQUENCED the residual double load here
    rather than deferring it): the dashboard tracks
    section pays twice per render for three more loaders, and the expensive one is **two full
    `calculate_paycheck` runs (7.2 ms / 7 SQL for the second alone)**.
  * [ ] **X-i2 THE CLOCK** -- the cutover. Each memoized loader takes `ctx.as_of` and `ctx.scenario`,
    and this **MOVES MONEY**: the gross measured at `$3,631.74` today against `$3,722.53` at a 2027
    read and `$0` before the first pay period, and FU-3 changes a loan's whole forward trajectory on
    a historical read. Own trace, own oracle, own every-figure sign-off. **Does NOT close N-56**:
    the grid's two self-refresh endpoints are two HTTP requests, so a per-pass memo cannot reach
    across them -- its fix is the `hx-swap-oob` topology, riding here as its own commit. **Sequenced
    BEFORE X-j** on a measured ground: X-j moves three more surfaces onto the modelled view whose
    contribution load costs `2.7 -> 14.8 ms` per render entry, so shipping X-j first ships that
    regression and then removes it. Forks for its trace: which salary profile a historical read
    picks when several are active, and whether deductions resolve at the read date.
* [ ] **X-j** `feat(balance): one account, one answer -- or a row that explains the difference` --
  closes **N-87**, **N-90**, and **N-83**'s DISPLAY half. **This step is the OWN RULING R-AK
  deferred, and its title does not presuppose the outcome.** R-AK ruled the dashboard, the pulse and
  the analytics calendar STAY on the kind-blind cash view inside X-g3 and said the surviving argument
  deserves its own ruling with its own measurement. **Root: the seam offers two families that answer
  "what is this account worth" for the SAME account, and the CALLER picks.** X-g3b proved the
  resolution on the grid -- give the surface the rows that explain the modelled tiers, then let it
  render the modelled balance, and R-K's identity holds for all five kinds. The two candidate end
  states are "one producer, with the difference rendered" and "two producers that legitimately answer
  different questions, with the NAVIGATION that equates them fixed instead"; the trace decides.
  Live on the developer's own default screens.
* [ ] **X-k** `fix(recurring): the recurrence bound is reconciled, not stored and forgotten` --
  closes **N-18**, **N-19**, **N-23**, **N-24**. **Ruling R-AP, taken AGAINST the recommendation**:
  they stay in this ledger and get a step. The ground the recommendation rested on SURVIVES as the
  step's scoping rule -- X-k touches the recurrence engine and the transfer write door and NOT the
  seam, so it must not grow into one. **The "shares no file with any other remaining step" half is
  FALSE, corrected 2026-08-08**: the recurrence redesign's `R5` edits `recurrence_engine.py` and
  `transfer_service.py`, both of them X-k's. Block 9's one-file overlap was measured against the two
  parked TAGS and never against this step. **Sequence X-k AFTER `R5`**, which deletes
  `compute_due_date` and re-keys the generation index; rebasing X-k over that is cheaper than the
  reverse, and X-k's own root is untouched by it. **Root:
  `RecurrenceRule.end_date` is a stored derived value never reconciled against what was actually
  GENERATED**, and the write door's refusals have no consistent batch contract: one refused loan
  payment rolls back an entire carry-forward batch, and three generation call sites have no
  `ValidationError` handler so a refused write 500s on extend and unarchive. The money consequence
  is on a balance screen: a shadow generated past a bound that later moves EARLIER keeps its
  checking-side expense leg.
* [ ] **X-l** `feat(periods): the pay calendar answers any date` -- closes **N-82**, **N-128**, and
  **N-79**'s surviving far half. **The value type this needs already EXISTS** (noted 2026-08-08):
  the recurrence arc built `app.services.recurrence.PeriodCalendar`, a frozen schedule whose
  ordering invariant is CHECKED in `__post_init__` and which already answers `opening_bound`,
  `horizon`, `period_containing`, `period_starting_on_or_after` and `earliest_start_in_month`.
  **The app now holds THREE implementations of "which pay period contains this date"** -- that
  one, `balance_at/_cash_periods.py:310` (a byte-similar bisect) and
  `loan_ledger/_visible.py:117` (a linear scan whose fallback the other two deliberately
  refuse). That is the recurrence arc's finding **F-12**, owned there by step **R-F12**, and
  the two steps must be SEQUENCED TOGETHER or the fourth answer gets built here.
  **Since 2026-08-08 it is THREE steps, not two: this step IS the pay-calendar arc's `C2`**
  (`docs/plans/implementation_plan_pay_calendar.md`), which supplies the total function this row
  asks for by DERIVING the calendar from the paydays. Whoever builds it satisfies all three specs,
  and `C2` adds one this row did not have -- **row P14: the calendar may not be constructed from a
  partial window**, or the last period in that window reports a different end than the same period
  reports elsewhere. `PeriodCalendar.from_pay_periods` and `_cash_periods._PeriodSpans.of` both
  accept one today, and the sibling shape is already measured in-repo at `$150,000.00`
  (`loan_ledger/_visible.py:78-95`). **Root, and it is this arc's own disease on the other axis: the pay
  calendar is a PARTIAL function.** `get_all_periods` returns the MATERIALIZED rows and nothing
  else, so past the last row every consumer improvises and the improvisations disagree -- precisely
  the shape Section 1 describes and Section 3 deletes with a total fold. Measured: past the horizon
  the modelled replay's ACCRUAL tier keeps running while its CONTRIBUTION tier stops (Empower
  **+$2,501.92**, Property **+$5,427.07** at six months out) -- a HALF model with nothing on screen
  saying so; and `growth_engine._project_one_period` looks a `ContributionRecord` up by
  `period.start_date`, so past the real calendar every period falls back to the flat
  `periodic_contribution` (ruling **R-AF** closed the NEAR half for free by landing the synthetic
  axis on the real one). **N-128 is in the FOLD, not above it**: `_cash_sums` and `_assertion_sums`
  skip a fact whose day no period can place while `_period_balances` keeps it, so a hole breaks
  R-K's reconciliation identity (`-$140.63` on the gapped clone).
* [ ] **X-m** `refactor(growth): the projection engine takes its axis, not its boundaries` -- closes
  **N-86**. **Root: `growth_engine.project_balance` takes a derived boundary as an ARGUMENT its
  caller must compute to match the window the caller also passes**, and nothing checks that they
  agree. That is Section 8's rule verbatim -- an argument a caller can get wrong is a defect, not a
  contract. **Cost when a caller gets it wrong: `$1,000.00` of annual-limit room per period of
  divergence, compounded over the horizon.** Both live callers are correct today and pinned in both
  directions, but the app carries TWO correct YTD boundaries whose difference is invisible in the
  rendered figures, and a THIRD projection surface would have to KNOW the rule. The fix is a
  signature change: trace every caller before touching it.
* [ ] **X-n** `fix(loan): a redistributed payment carries its REAL installment` -- closes **N-36**.
  **Root: `_redistribute_to_distinct_months` OVERWRITES the fact instead of carrying it.** Its own
  docstring is the evidence: it shifts a colliding payment's DUE date to the next month so the
  monthly engine does not sum two into one, and says only the due date shifts because `payment_date`
  is a FACT carried through untouched. **Both are facts** -- the real installment a payment
  satisfies is as much a fact as the period its cash moved in, and the redistribution destroys it.
  The codebase therefore holds one question with two keys ON PURPOSE (archived ruling D5 keys the
  split inputs on the DUE date; `rate_period_engine._replay_from_anchor` stayed on
  `payment.period_start`), stated at the site so it cannot be rediscovered as an accident. Contained
  today because the replay's rows are DISCARDED whenever a `confirmed_view` is supplied -- and
  containment is not a fix.
* [ ] **X-e** `refactor(accounts): current_anchor_balance is a reconciled cache or it is nothing` --
  carries **N-96**, **N-85**, **N-180** and **X5**. **WIDENED 2026-07-27 (R-AO): the one-liner is
  not the step the code describes.** Most of the census is being taken by X-f1c3a..c, which delete
  the columns outright; what remains here is what those leaves do not reach -- the two callerless
  public seam entries N-96 / N-85 name, the falsified de-duplication rationale in
  `balance_at/_loan_interest.py` whose surviving question is UNVERIFIED (**N-180**), and X5's
  backdated-assertion option, now largely answered by `observed_on`. **Its census is HISTORICAL and must be re-taken before this step is scoped**: it was
  measured when the columns still existed, and `balance_at/_investment.py` was deleted outright at
  `17c57cde` (its modelled-asset work went to `_asset_fold.py`).
* [ ] **X-p** `fix(analytics): the calendar's chips and its balance line are on one clock` -- closes
  **N-58**, **N-97**. **Sequenced AFTER X-f by that finding's own ruling**, and the reason is not
  caution: X-f shrinks the date noise at its SOURCE, so ruling before it would decide the question
  against numbers X-f then changes. It is scheduled, not deferred. **Root: one day cell renders two
  facts on two clocks with no row explaining the gap** -- the chips are placed on the BUDGET
  attribution date, the balance line steps on the day the money MOVED. Measured: `paid_at - due_date`
  is median 2 days, p75 6, max 25 across 130 settled Checking rows. The option space: place the chip
  on the cash clock (which changes which MONTH a row appears in), give the calendar R-O's treatment
  (a reconciling figure), or rule the divergence acceptable and label it.
* [ ] **X-ab** `refactor(ledger): one asset-vs-liability rule, read and write` -- closes **N-122**
  (R-CU). `ledger_account_service.ledger_class_id_for_category` asks X-z's question a second time on
  the POSTING path, and the two agree by reading. **It is not a residue fix**: it decides which
  ledger class a real account's paired posting account carries, so it is the write path, and Section
  8's "a DRY refactor of a PREDICATE can move money" is the case rather than the caveat. Its trace
  must answer what a re-class does to accounts that already carry postings.
* [ ] **X-ac** `refactor(savings): one liquid-savings reduction` -- closes **N-121**. The cockpit
  reduces `_sum_liquid_balances` over one `account_data` TWICE per render and publishes the answer
  under two context keys -- ruling **R-AZ**'s "one fact under two keys" beside a redundant
  computation. Collapsing it changes what the page publishes, so it wants its own commit
  and its own render diff.

### Phase X -- the gate and vocabulary residue

* [ ] **X-ag** `feat(pylint): lax digit acceptance is refused, not remembered` -- closes **N-139**.
  X-ae converted every submitted-id surface it found and the AST now finds exactly one digit-
  predicate call site in `app/` and `scripts/` (the implementation of the replacement). Nothing stops
  the next one. **What this step must NOT do is what its first two drafts specified**, both refuted
  before it was written: matching `isdigit` / `isdecimal` / `isnumeric` by METHOD NAME reports CLEAN
  over the defect it exists for, because a bare `try: int(raw) / except ValueError` passes it -- and
  that is precisely the form the first ruling specified and measurement rejected. The instrument is
  an open question the step's trace decides, and it must be shown FIRING on a planted defect.
* [ ] **X-ah** `fix(routes): a query-string id is parsed like every other id` -- closes **N-142**.
  The one submitted-id surface X-ae did not convert: 42 `request.args.get(..., type=int)` call sites
  where Werkzeug catches the `ValueError` (so no crash) but the coercion is `int()` (so `'١٠٦'` is
  106, `' 2026 '` is 2026, `'1_0'` is 10). **It needs a per-site ruling, which is why it is a step**:
  the path parameters were all row ids and the schema fields all row ids, so each took one blanket
  rule, but these are MIXED -- `account_id` and `period_id` are row ids while `year`, `month`,
  `offset`, `periods` and `show_all` are not, and `offset=0` / `show_all=0` are meaningful, so a
  blanket `parse_row_id` would silently refuse them. Owes a second small rule in `digit_strings`:
  ASCII-strict, canonical, but admitting zero.
* [ ] **X-al** `fix(pylint): a duplicate-code disable that suppresses nothing is a finding` --
  closes **N-154**. `useless-suppression` is enabled precisely so a stale disable is reported, and
  it is BLIND to a `duplicate-code` one -- measured both directions: removing a disable left
  `pylint app/` at 10.00/10, and planting one back left it at 10.00/10 with no `I0021`. **The
  blindness is upstream, not a misconfiguration** (R0801 is a close-time checker over a similarity
  graph, so suppression accounting has no line to credit). The likely shape is a pre-commit arm that
  strips each disable in turn and fails if the tree stays clean without it; the instrument is not
  ruled here and must be shown FIRING on a planted stale disable. **FIFTEEN live `duplicate-code`
  disables remain in `app/` and not one has been re-measured** (4 in `models/`, 5 in `routes/`, 6 in
  `services/`). The step's first deliverable is the census.
* [ ] **X-am** `refactor(status): the settled band has two members, not three` -- closes **N-177**.
  The `Settled` status carries **0 rows on both tables in production** and no writer anywhere assigns
  it; its only door is the status `<select>` in the two full-edit popovers. **It is in scope because
  it is a member of the set the cash walk folds on**: `settled_status_ids()` is `{Paid, Received,
  Settled}` and every reader consumes the SET, never the member, so the balance engine cannot tell
  the three apart. Its whole distinct meaning is one line of the transition map (`settled: {settled}`,
  terminal, no revert). **The step must decide whether that meaning is wanted at all** -- a
  deliberate archive lock is a defensible feature and an unreachable one is dead vocabulary; today it
  is the second wearing the first's clothes. Its trace owes: whether any row anywhere has ever
  carried it, and what a delete does to the state machine's two maps.

### Phase E2 -- the super-package boundary (runs LAST; Phase G runs inside it, ruling **R-DQ**)

* [ ] **E2-0** `the membership trace` -- NO code. Answer from the code: which modules are members,
  what the public re-export surface is, whether `account_projection` is in or out, and whether
  `ledger_report_service` is (**N-35**). Re-run the fence scan and check the arrow risk: whether any
  member imports a NON-member that would then have to move too. **A FIFTH question, N-33's 13
  private-NAME crossings**: routes import 13 private names from `app.utils.account_validation`, and
  the names LIE about their visibility -- routes are their consumers, so they are cross-package API.
  The honest fix is a rename to public, after which extending W9910 to private NAMES is a
  zero-exception tightening. Expect this step to DECOMPOSE from what it finds.
* [ ] **E2-n** the move itself, and the registry deletion. Decide the decomposition from E2-0, not
  here. **The deletion of `_FENCED_MODULE_RULINGS` is the LAST commit, never the first**: prove the
  boundary holds before removing the gate that compensates for its absence (the C3b3
  prove-the-successor-first precedent, applied eight times in this arc).
* [ ] **G1** `refactor(gates): the ledger-model and balance-seam fences stop carrying name lists` --
  closes **N-147**. **Its first action is a trace and no code is written before it**, because the two
  remaining allowlists are not the same problem wearing two hats. `_LEDGER_MODEL_ALLOWLIST` names the
  modules permitted to import the three ledger models; the balance seam checker carries roughly a
  dozen module sets plus a per-module EXPORT map, which encodes what each producer may publish rather
  than merely who may import it. **What the trace must establish, per allowlist**: whether the entry
  exists because a boundary is genuinely absent (the fix is the boundary, as W9910 was), because a
  legitimate member is spelled as an outsider (the fix is the spelling), or because the rule is a
  value-level invariant a TYPE could carry (`ReconciledThrough`'s shape). **Ruling all three the same
  way is the error this step exists to avoid.** Each checker's deletion is the LAST commit of its own
  arm.
* [ ] **G2** `refactor(money): the value types that retire three checkers` -- retires **W9901**,
  **W9904** and **W9902**, the three with no structural replacement scheduled anywhere. **Its first
  action is a trace**, because the two halves are different sizes and only one is obviously worth it.
  **The money half:** `Money`, a value type over `Decimal` that cannot be constructed from a `float`
  and whose rounding is a method carrying the app's rule, retiring W9901 and W9904 together.
  **Measured: 44 `Numeric(12, 2)` money columns and 37 `.quantize(` call sites**, so this is the
  largest refactor in the inventory and its trace must decide whether it lands at the ORM boundary (a
  `TypeDecorator`, so the blast radius is the type rather than the call sites) or as a hand
  conversion. The `TypeDecorator` route is the one that makes the checkers redundant BY CONSTRUCTION.
  **The label half is small and should not wait for it:** `DisplayLabel`, a type returned by a ref
  table's `.name` whose `__eq__` against a `str` RAISES, retiring W9902 and its two module sets. The
  Jinja half needs its own answer.

## 6. The findings ledger

**Moved to `../../plans/ledger.md`**, the one findings table for every arc. This arc's rows
are the ones whose `arc` column reads `balance`; a row's owner names a step in
`../../plans/steps.md`, whose specification is section 5 of this document.

They moved because a finding is not arc-local: `P2` / `F-10`, `P3` / `N-123` and
`P6` / `F-12` were each one defect recorded in two ledgers, kept in step by hand,
and one of those pairs went unnoticed for months. The rules the table is graded
against are `../../plans/conventions.md`.

## 7. Verification standard (what "done" means for every step)

1. **The baseline must not move** (Section 2) unless the step's design says it moves, in which case
   every moved number is individually explained and signed off.
2. **Oracles are exhaustive and independent.** Every day, every shape; never a sample; never two
   producers that share code proving each other. The fold is the reference.
   * **The real-data harness is `tests/manual/verify_balance_baseline.py`**: it dumps every figure
     the seam can answer about every account in a database. Run it before and after and `diff` the
     blobs. **Use `git worktree` for the HEAD side, never `git checkout`.** It is DETERMINISTIC and
     it is a REGRESSION check, never a proof -- two figures identical in it can both be wrong. Every
     figure is read at the seam's default `as_of`, so a step scoped to a pinned historical as-of
     moves nothing in it.
   * **It is BLIND ABOVE THE SEAM, which is why there is a second one**
     (`tests/manual/verify_savings_producers.py`). For a step whose whole surface is a producer
     package, a serializer or a template, the first harness is byte-identical whatever the step did
     -- a free pass that reads as proof.
   * **A THIRD covers the anchor surfaces both are blind to** (`tests/manual/verify_anchor_surfaces.py`,
     added at X-f1c3c): the grid header's figure and "as of" caption, the reconcile panel, the
     dashboard balance section, the pulse hero, the savings dashboard including the ARCHIVED drawer,
     Property market value / home equity, and the retirement seeds. A producer that raises is
     RECORDED rather than fatal -- a probe that dies on account 3 has silently stopped covering 4
     through 9.
   * **Ask of every harness: can it SEE the code under test?**
3. **Every guard gets a negative control that is shown to fire.** A guard whose control does not
   fire is not a guard.
4. **The fixture matrix must contain the shape the feature exists for** (a paid loan, an
   off-schedule payment, a delinquent loan).
5. **The suite has two clock gates, and they matter to this arc specifically** -- three of the five
   defects behind them were fixture-clock bugs this arc created. CI runs `TZ: Pacific/Kiritimati`, so
   a `date.today()` / `display_today()` mix fails there, and a weekly sweep runs the suite at a leap
   day, both sides of a year boundary, a month end and the first of a month. **Read
   `docs/test-suite-clocks.md` before writing a fixture that touches an anchor, an assertion instant
   or a due date** -- this arc writes more of those than anything else in the codebase.
6. **Green gates are necessary, never sufficient.** A $197,049.32 defect passed pylint 10.00 and a
   7,387-test suite. Live-render the affected surfaces against the dev clone.
7. **No uncited claims in this document.** Anything stated here as fact about the code carries its
   own commit hash or was verified on its write date; when you edit this file, re-verify what you
   touch.

## 8. Process lessons (paid for repeatedly; do not pay again)

One line each; the evidence is in the commits of the step that paid for it.

* **An argument a caller can get wrong is a defect, not a contract.**
* **A DRY refactor of a PREDICATE can move money.** Two spellings that agree by reading are two
  answers until one is deleted.
* **When two figures PARTITION a set, write both halves from ONE predicate**, or the boundary drifts
  and both halves look right.
* **When two sides of one problem have different SHAPES, the loose side is where the next hole is.**
* **When a rule is re-keyed, the complement must move with it**, or replay and projection stop being
  exact complements.
* **SCORE THE RULE YOU SHIPPED, NOT THE RULE YOU DESIGNED.** Any change to a rule after it was
  scored re-opens the score; re-run the measuring script as the LAST act of the build.
* **AN APPEND-ONLY TABLE NEVER LICENSES AN UNSERIALISED READ-MODIFY-WRITE IN THE SAME
  TRANSACTION.** Name the tables the transaction WRITES, not the one the ruling is about. Ruling
  R-EN deleted a lock on "a second tab overwrites nothing", true of one table in a transaction that
  mutates three; the deleted column had been serialising the reconcile by accident. The precedent
  it cited carried the identical defect, so the mistake was made twice (N-190).
* **IDEMPOTENCY IS A PROPERTY OF A REQUEST, NOT OF THE ROW IT CARRIES.** A retry and a deliberate
  re-assertion are byte-identical by construction, so a unique index over the row's own values must
  mis-classify one of them. Ask which way it errs and what each error costs: here a false refusal
  rendered a wrong balance while a surplus append-only row posted `$0.00`, and the two are not the
  same size (R-EQ).
* **WHEN TWO DOORS WRITE ONE FACT, ALIGNING THEM CAN BE WORSE THAN DELETING ONE.** Ask what each
  door's SURFACE means before making them agree: two reviews recommended putting the account-edit
  door on ruling R-EQ's rule, and because that form PRE-FILLS the balance, a rename would then have
  asserted today's figure and absorbed two months of unreconciled purchases. A door that is not a
  balance-reading surface should not be taught to read balances better (X-f1e1).
* **A TEST THAT REPLACES A DELETED FEATURE'S TEST MUST BE RUN AGAINST THE REVERT.** Two of X-f1e1's
  four controls passed on the old code -- one submitted the exact input the deleted branch
  short-circuited, the other could not tell "unreachable" from "refused" because both answer 200
  and write nothing, and it never asserted the part of the edit that DOES differ. Deleting a
  behaviour and its test lowers coverage silently unless the replacement is shown to fail without
  the change.
* **WHEN TWO VALUES HAVE ALWAYS BEEN EQUAL BY CONSTRUCTION, THE CODE THAT COUPLES THEM DOES NOT
  EXIST.** Making one of them user-supplied does not break a rule you can go and read -- it breaks
  an assumption nothing ever had to write down. Before changing what a field can hold, ask which
  values it has silently equalled: the reconcile prompt keyed on `MAX(observed_on)` was correct for
  as long as every true-up stamped today, and nothing in it named that dependency (X-f1c4c).
* **A SUCCESS RESPONSE THAT RENDERS THE PRE-SUBMISSION STATE IS INDISTINGUISHABLE FROM A NO-OP.**
  The same defect as an unrenderable refusal, on the other side, and it hides better -- a 200 looks
  like it worked. A write whose whole point is invisible on the surface that made it will be made
  twice.
* **A REFUSAL THE SUBMITTING SURFACE CANNOT RENDER IS NOT A REFUSAL.** Before adding a rule that a
  user can trip, press the button and watch: this app's htmx config leaves 4xx non-swapping, so a
  correct 400 with a correct message was invisible and the form simply sat there. Ask what the
  refusal LOOKS like, not only whether it fires (X-f1c4c).
* **A RULE THAT READS A CLOCK MUST BE APPLIED ONCE, AND "the writer does not
  trust its caller" IS NOT A REASON TO APPLY IT TWICE.** Unifying two writers
  put one bound on both sides of a call; the floor is `min(earliest period
  start, today)`, so a midnight roll between the two applications refused the
  day the first had just produced -- and the second refusal landed after the row
  was flushed. Ask whether a re-check is a pure function before calling it
  defence-in-depth; where it is not, carry "already checked" in a TYPE the
  checker alone can mint (X-f1e2).
* **A FRAGMENT'S MOUNT IS PART OF ITS CORRECTNESS, AND A RESPONSE CAN DESTROY ITS OWN MESSAGE.**
  An out-of-band swap into a region that re-fetches on the event the SAME response fires is a race
  the response always loses. Ask what re-renders the target, not just whether the target exists
  (N-199).
* **A RESPONSE-BODY ASSERTION CANNOT SEE WHAT A SECOND REQUEST DOES TO THE PAGE.** The test that
  missed N-199 asserted the message was in the body -- it was, and it was gone from the DOM a moment
  later. Where a fragment's correctness is its POSITION, grade the position.
* **GRADE THE ONE TOKEN THE FEATURE HANGS ON, OR A MUTATION WILL PROVE YOU DID NOT.** Deleting
  `data-toast-auto-show` left the acknowledgement in the DOM, permanently invisible -- N-199's exact
  symptom -- and the whole 7,867-test suite passed. Deleting `hx-swap-oob="true"` did too. An
  adversarial review MEASURED both. A test that grades a fragment's id, copy, day and amount and
  stops one attribute short of what makes it reach a human is grading everything except the defect.
* **AN AFFORDANCE THAT CANNOT SUCCEED IS DELETED, NOT GIVEN A NICER REFUSAL.** The first fix for an
  invisible kind refusal re-rendered the editor -- a live input and a Save button guaranteed to be
  refused again -- twelve lines below the same module's rule forbidding exactly that. When a refusal
  is reached by an ordinary click, the question is why the click was offered (R-ET).
* **ONE SURFACE GETTING A RULE RIGHT HIDES THAT THE SHARED PARTIAL NEVER GOT IT.** The cockpit had
  rendered loan balances read-only for a year; the partial the other four surfaces include had not,
  so the rule looked shipped and was one-fifth shipped.
* **NAME A WRAPPER'S FIELD SOMETHING THE WRAPPED PRIMITIVE CANNOT ANSWER.**
  `ObservationDay.day` compiled against a raw `date` and returned the day of the
  MONTH -- an integer, silently, into an SQL bound. A value type only fences
  what its accessor cannot be satisfied by accident (X-f1e2).
* **A MECHANISM THAT HAS ONLY EVER BEEN RE-KEYED HAS NEVER BEEN DESIGNED.** Three migrations moved
  this index's columns to follow the schema, and each one read as a decision. Ask when the rule was
  last decided, not when it was last edited.
* **A shared primitive reached through a private import is telling you the package boundary is
  wrong.**
* **A fail-CLOSED gate is scoped by module identity, so creating a module is how you escape it.**
* **A static guard that greps for a NAME cannot tell code from prose.**
* **A GATE MUST BE EXERCISED AGAINST THE ARTIFACT IT GRADES, never read or tested only
  synthetically.** A pattern that matches the real file nowhere passes every synthetic control.
* **Scan with an AST, not a regex -- and an AST census is a grep with better manners unless it
  FOLLOWS THE DATA.** A census and a gate can be blind the same way, and then they confirm each
  other.
* **A CENSUS THAT IS NOT COMMITTED IS AN UNCITED CLAIM.**
* **COUNT THE CALL GRAPH, NOT THE CALL SITES.** One finding said four spellings; the tree held 18.
* **A COUNT IN A DOCSTRING IS A CLAIM, AND THIS ARC KEEPS WRITING IT WRONG.**
* **Every guard gets a negative control shown to fire, and a REPAIR for a dead control is itself a
  control needing the same mutation.** A correction can carry the defect it corrects.
* **A test whose fixture has no data cannot distinguish two producers.**
* **A NEW FIXTURE IS A NEW CONTROL, AND IT CAN BE BORN DEAD.** SQLAlchemy accepts an assignment to a
  field that does not exist.
* **`hasattr` on a dataclass is not a test**, and neither is `is not None` after `isinstance`.
* **A list returned for its COUNT must have its count asserted.**
* **Ask what a test's failure would have COST before deleting it, and write the answer down.**
* **CONVERTING A SURFACE TO "RAISE" BLINDS EVERY TEST WHOSE FIXTURE CANNOT REACH IT.**
* **A SUITE THAT PASSES ON 353 DAYS A YEAR IS NOT A GATE**, and the day it fails it will look like
  your change.
* **AN INSTRUMENT MUST BE SHOWN TO HAVE REACHED ITS SUBJECT.** One that cannot authenticate reports
  no differences, loudly and wrongly; one that silently grades a single subject five times reports
  five results. Assert the identity a result is attributed to, not just that a result came back.
* **A BASELINE IS ONLY A BASELINE AGAINST THE DATABASE IT WAS TAKEN FROM**, and widening an
  instrument is a shape change needing the same normalization the code does.
* **An ORACLE that states a different rule than the engine lets both be wrong together.**
* **When a conversion is mechanical, the DIRECTION of the type change has a mirror, and the mirror
  is where the bug is.**
* **A refusal is only as good as the repair it names, and nobody had pressed the button.**
* **THE STATE A GUARD DEFENDS AND THE STATE THE APP IS IN CAN BE OPPOSITES.**
* **A guard written against the wrong failure mode can still be a good guard** -- write the reason
  beside it.
* **A skip is safer to state than a fire**, when the operation being guarded is the one under test.
* **A RULING ID IS A CITATION, SO THE RULING SHIPS FIRST.**
* **REVIEW A FROZEN TREE.** Applying one review's fixes while another is running invalidates both,
  and a mutation-planting reviewer needs its own worktree.
* **A STEP WITH MULTIPLE LEAVES, MIGRATIONS AND REVIEWS SPANS SESSIONS.** Stop at the first leaf
  boundary and hand off; the alternative is a mechanical cleanup pass applied with too little care
  at the end of a long session (X-f1c3, 2026-08-04).
* **A TABLE THAT NAMES ITS OWN `HEAD` IS FALSE THE MOMENT IT LANDS**, because the commit that
  writes it moves `HEAD` past the hash it just wrote. Record the last CODE commit and the remote,
  which are stable, and tell the reader to re-measure the rest.
* **Documents rot in days here.** This file is the only one allowed to rot, and every edit re-dates
  it.

## 9. Rules for this document

**Moved to `../../plans/conventions.md`**, one copy for every arc. They were near-identical in
three documents and absent from the fourth.

`tools/plan_gate/` grades this document against them through a pre-commit hook
scoped to it and the CI step that runs the custom pylint checkers -- so EDITING
THIS FILE is what runs the gate. This document's own caps live in the gate's
constants beside the other arcs'.
