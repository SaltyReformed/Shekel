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
| `archive/phase_x_as_built_2026-08-04.md` | Phase X from **X-c2c4** to **X-f1e1**, one line per step with its verified commit hash and what it closed, plus the 6 findings closed outside a step and the 79 rulings whose work has shipped. **Section 1a (added 2026-08-05) holds the X-f1 cluster's eleven shipped leaves**, including two hashes this document never recorded |
| `archive/anchor_settle_partition.md` | The anchor/settle partition arc: R-DH's six parts, steps 1-4 and S1-c as built (PRs #67 / #75 / #76), the F1-F12 review register, and the from-scratch redesign that was measured and REJECTED. **Superseded 2026-08-04**; its three surviving obligations were carried into X-f1c4 and X-d before it moved |

Everything else that ever governed this work is in `archive/`, indexed by `archive/README.md`.

## Where the arc stands

**A signpost, not a log.** It orients a reader arriving cold -- what just landed, what is in
flight, what is next -- and POINTS at the detail rather than carrying it. It is REPLACED each
session, never appended to, and the gate caps it; rule 8 says where each kind of content goes
instead.

**As of 2026-08-05.** Re-measure production from `docker inspect shekel-prod-app` and branch state
from `git status -sb` when you edit this.

| | | detail |
|---|---|---|
| **just landed** | **X-f1e1** (`677bb397`): the account EDIT page stops asserting balances, so a cash balance now has exactly ONE write door. No migration, no figure moved. Production still runs merge `e5f27154`, migration head `d7c1f4a9e603` -- **none of the X-f1 cluster is deployed** | its Section 5 entry, and the commit |
| **how much of X-f1 is left** | **two leaves: X-f1e2 and X-f1e3.** Every other leaf shipped and was CONDENSED into the archive on 2026-08-05, which took this file 1,195 -> ~1,020 of its 1,200 cap. The ledger stands at **88**, down from 104 on 07-27 | Section 5's X-f1 entry, then `archive/…2026-08-04.md` Section 1a |
| **in flight** | nothing. `feat/xf1-settle-day` carries the whole cluster, is PUSHED, and has **no PR** -- re-measure `git status -sb` rather than trusting a hash here | -- |
| **blocked on you** | **X-f1e2 needs a developer ruling before any code**: `AccountAnchorHistory` has TWO writers (the stager, and `create_account` building the row directly) and NOTHING in `app/` reads its `notes` column. Delete the parameter, delete the column, or route the origination through the stager | X-f1e2's Section 5 entry |
| **next** | **X-f1e2** (needs the ruling above) -> X-f1e3 -> X-f1 ticks -> X-an -> X-f2 -> **X-f3** (moves money, own PR) -> X-f4 -> X-f5 -> X-f6 | Section 5, in execution order |
| **why this shape** | the anchor half was redesigned from scratch by ruling **R-EB**; R-EQ then designed the duplicate rule that had only ever been re-keyed, R-ER put the day rule in the module that owns what an assertion is, and X-f1e1 finished the job by deleting the second door rather than aligning it | Section 3.3, and R-EB / R-EQ / R-ER in Section 4 |
| **the live lesson** | two adversarial reviews recommended ALIGNING the second balance door on ruling R-EQ; measured, that made a RENAME absorb two months of purchases. The surface went, not the gate | Section 8's "two doors" bullet |
| **resuming cold** | branch `feat/xf1-settle-day` is pushed and CLEAN; the test template is at `a3f6c1d84b90` and **now matches it** (the earlier "no longer matches `origin`" warning inverted when X-f1c4b was pushed), so no rebuild is needed unless you add a migration. Baseline: **7,853** green under both clock zones. The old advice to override `IDLE_TIMEOUT_MINUTES` is DEAD -- `TestingConfig` pins 720 itself. A production clone at the branch's head is left on `shekel-dev-db` as **`shekel_xf1e`** for the next baseline diff | `app/config.py:447`; Section 7.2 for the harnesses |

Section 5 is the work that remains, Section 6 every open defect with an owner, Section 4 the
rulings that govern them, and `archive/` what already shipped.

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

## 5. The steps

### 5.0 The order of execution

**A SCHEDULE, and every position is forced by a stated fact rather than a preference.** A block
starts when its gate clears, not when the block above finishes. The phase headings below group the
same steps by SUBJECT; this groups them by TIME. **The blocks PARTITION Section 6**: all 88 open
findings are owned by a step in exactly ONE block, save the three the closed owner vocabulary sends
outside the schedule (one `operator`, two `developer-decision`). Measured, not asserted.

| # | block | the fact that forces its position | cost |
|---|---|---|---|
| 1 | **the anchor half** -- X-f1e2, X-f1e3, X-f1 ticks, X-an, X-f2, **X-f3**, X-f4, X-f5 | The only remaining work that moves a figure the developer reads: **N-171**'s `$15,065.08` gross / `-$1,495.10` net stays invisible on the income statement until X-f3, which is its OWN PR and MOVES MONEY | 28-35 h |
| 2 | **X-ad then X-x, shipped as ONE PR** | Gated on `feat/xf1-settle-day` MERGING, not on block 1 finishing: X-ad's trace must replace the guarantee `accounts.current_anchor_period_id` gave, and X-f1c3c deletes that column. X-x is HELD behind X-ad (**R-DE**), so shipping the pair DISSOLVES the hold instead of maintaining a branch | 12-16 h |
| 3 | **the posting restructure** -- X-ai-a/b/c/g, X-d re-land, X-ai-s, X-aj2, X-am, X-ak | **X-ai-s is HELD pending X-f3**; X-d is PARKED on **N-155**, whose fix is X-ai's placement; X-ak carries **N-193**, a reproducible unhandled 500 on a money route. **29 of Section 6's 88 rows live here** -- a third of the whole ledger (X-ai 13, X-ak 9, X-aj2 4, X-aj 1, X-d 1, X-am 1) | 31-42 h |
| 4 | **the credit-card arc** (own document) | `CC1b`'s fold is specified against the reset semantics **R-EB deletes at X-f3**, and `CC3b` derives a settle from `paid_at`, **deleted at X-f1b**. Earliest correct start is after X-f4 and X-am | 74-104 h |
| 5 | **X-f6, the bank import** (own document) | Consumes X-f2's outstanding set and X-f3's residual path (**R-EB**). After block 4 so ONE matching rule covers checking and card rows rather than being widened into them later | 22-31 h |
| 6 | **the read-path residue** -- X-y, X-i1, X-i2, X-j, X-k, X-l, X-m, X-n, X-e, X-p, X-ab, X-ac | Nothing blocks on it and its footprint is disjoint from the write path (tag `xd-attempt-1-parked-n155`'s 30 `app/` files against `wip/x-x-held`'s 26: **zero overlap**, measured) | 50-70 h |
| 7 | **the gate and vocabulary residue** -- X-ag, X-ah, X-al | Shares files with nothing; interleaves anywhere | 11-15 h |
| 8 | **E2 and G** | Runs LAST by ruling; G2 must not begin before the boundary it rests on is proven | 28-44 h |

**Two blocks leave this document and keep only their row here**:
`docs/plans/implementation_plan_bank_import.md` (X-f6, not yet written) and the existing
`docs/plans/implementation_plan_credit_card.md`. Both are FEATURES consuming this arc's output, not
correctness fixes inside it, so rule 1 is not weakened. **The card plan's ratified sequencing is
DISCHARGED, not pending, and its order must never be re-read as a live gate**: every balance-arc
step it names has SHIPPED -- `C8`/`C9`, `D1`-`D3` and old `X1`-`X3` all resolve in archived records
(the last closed by X-c2b2), and old `X4` survives here as X-e. What blocks the card arc is NEW and
post-dates that 2026-07-19 ruling: R-EB. The row above is the gate.

**The costs are measured against this arc's own history, not estimated** -- X-a (`929b3a72`,
07-25) to X-f1c4c (`5fc22bba`, 08-04) is **98 in-session hours over 10 working days** and ~55 steps.
**They assume Section 6 stops growing.** It grew 41 -> 104 between 07-27 and 08-04, then fell to
**88** across the X-f1 tick pass and X-f1e1 -- the first sustained fall of the arc, and the number to watch.

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

* [ ] **X-f1** `feat(transactions): a settle carries the day the money moved` -- absorbs **S2-b**.
  Rulings **R-EC** / **R-ED** / **R-EE**, and the first DELETES a column: `transactions.settled_on`
  REPLACES `paid_at`, so a settle has ONE clock instead of an instant plus 11 derivations of a day
  from it. Closes **N-173**. (**N-175**, the `anchor_settle_partition.md` archive move R-EB had
  bound to this step, was decoupled and DONE at the 2026-08-04 trim -- see X-f1d.)
  **The invariant it establishes: a row is settled if and only if it carries a settle day.**
  Measured on the 2026-08-03 clone it already holds exactly -- 0 of 741 non-settled rows carry a
  `paid_at`, and all 156 settled rows get a day from the backfill. It is enforced STRUCTURALLY, not
  by a fence: `status_seam.apply_status_change` is the single door that writes `status_id` and it
  writes the day in the same call. A `CHECK` cannot express it (the predicate lives in
  `ref.statuses` and a constraint cannot join). A reader that finds a settled row with no day FAILS
  LOUD rather than falling back, because silently dropping such a row from the fold is silent money
  loss.
  **The seam's stamping rule, restated here because X-f1b is archived and live steps read against
  it** (rule 5): it stamps `display_today()` on FIRST entry to the settled band and PRESERVES the
  day on re-entry, which is what stops archiving a payment from re-dating its money. **X-f2** puts
  the STATEMENT date there instead of the stamp, and **X-an** keys the loan resolver's history cut
  on the stored day.
  * [x] **X-f1a** .. **X-f1d**, and **X-f1e1** -- **SHIPPED, and CONDENSED into
    `archive/phase_x_as_built_2026-08-04.md` Section 1a on 2026-08-05** (rule 5), one row per leaf
    with its verified hash and what it closed. Eleven leaves: the rulings, the settle-day column
    and its two edit doors, the anchor's one home (three leaves, two destructive migrations), the
    statement day and the duplicate rule R-EQ redesigned rather than re-keyed a fourth time, the
    archive move, and the second balance door's deletion. **Two hashes were RECOVERED at extraction
    that this document never recorded** -- X-f1c1 `0b04f255` and X-f1c2 `7e9f261a`.
    **What is still LIVE from that span is the invariant above, plus the three findings its last
    two leaves opened** (N-197, N-198, N-199), which are Section 6 rows owned by X-f1e2, X-f1e3 and
    X-ak. Nothing else in it governs work.
  * [ ] **X-f1e** `refactor(accounts): a balance is asserted at ONE door` -- developer ruling
    2026-08-04, taken at X-f1c4's trace. DECOMPOSED once its own trace found three questions under
    one bullet: **X-f1e1 shipped** (the door deletion, archived above) and the two below remain, so
    this parent and X-f1 both stay open. They are the LAST work in the X-f1 cluster.
    * [ ] **X-f1e2** the assertion's ONE WRITER, and the label nothing reads -- owns **N-198**.
      X-f1e1 left `anchor_service.stage_anchor_true_up` with ONE caller (`apply_anchor_true_up`)
      and its `notes` parameter with NONE, which is the N-96 / N-85 shape one tier down. **The
      question is bigger than the parameter and that is why it is its own leaf**: two writers of
      `AccountAnchorHistory` remain -- the stager, and `account_service.create_account`, which
      constructs the row directly with `notes="origination"` -- and **no code in `app/` READS the
      column at all**, so the audit trail's "which door wrote this" is written and never
      consulted. The leaf decides one of: delete the parameter; delete the column with it; or route
      the origination through the stager so the table has one writer and the label earns its place.
      **A developer ruling, not a build decision**: the third moves the origination onto a shared
      write path, and the first two delete an audit field.
    * [ ] **X-f1e3** the back-dated acknowledgement's mount -- owns **N-199**. Reaches ONE of the
      five surfaces the anchor editor opens from. Per-surface rendering decision, so it wants the
      design loop rather than a one-line addition.
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
  `/savings` net worth by **+$3,228.55** and puts `Account.current_anchor_balance` on screen as a
  current balance, while `/grid` renders the repair card at the same instant. **BUILT AND HELD**
  (ruling R-DE) behind X-ad, because N-127 measures the repair its refusals point at as
  non-functional.
  * [ ] **X-x1 THE ONE ANSWER** (R-CY) -- `PayCalendarGapError`,
    `pay_period_service.require_current_period` / `covers`, the application-level handler and its
    repair page, on the `require_baseline_scenario` / `BaselineMissingError` pattern name for name.
    **It takes the GRID's two pre-checks as its first callers** rather than shipping a door nobody
    walks through: an unreachable handler has no negative control.
  * [ ] **X-x2 THE FABRICATIONS** (R-CY) -- the branches that publish a figure the app did not
    compute take the raising accessor: the anchor-cache substitutions, the fabricated `$0.00` in
    four producers, and `build_trend_periods`' `current_index = 0` into an empty list.
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
  reads and deletes. Sequenced immediately after X-x so the read-side guards X-x installs are what
  grade it. **Registration stops creating a bootstrap pay period** (R-DB): measured, `today+1` /
  `+5` / `+13` are REFUSED and `today+20` / `+27` are accepted leaving a permanent hole, so the
  bootstrap either blocks the user's real payday or guarantees the state X-x's readers refuse to
  answer. **Its trace must decide what replaces the FK guarantee** --
  `accounts.current_anchor_period_id` is why the bootstrap exists, and X-f1c3c deletes that column,
  so re-check this fork against the tree rather than against this sentence.
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
  seam, so it shares no file with any other remaining step and must not grow into one. **Root:
  `RecurrenceRule.end_date` is a stored derived value never reconciled against what was actually
  GENERATED**, and the write door's refusals have no consistent batch contract: one refused loan
  payment rolls back an entire carry-forward batch, and three generation call sites have no
  `ValidationError` handler so a refused write 500s on extend and unarchive. The money consequence
  is on a balance screen: a shadow generated past a bound that later moves EARLIER keeps its
  checking-side expense leg.
* [ ] **X-l** `feat(periods): the pay calendar answers any date` -- closes **N-82**, **N-128**, and
  **N-79**'s surviving far half. **Root, and it is this arc's own disease on the other axis: the pay
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

**Only findings whose fix is NOT yet in the code are here, and every one has an OWNER**
(Section 9 rule 6, enforced by `tools/plan_gate/`). A row leaves when its fix ships, not when a
ruling settles it -- a decided-but-unbuilt defect is still ahead of you. The CLOSED registers are in
the three as-built records; IDs keep their names, so a reference to any finding resolves in
whichever file holds it. Unfinished work stays HERE whichever half of the arc it came from.

**One line per row, and the code is the detail.** Figures and citations were true on their write
date; re-verify against the tree before acting on one. **Six rows carry a QUESTION whose ADDRESS is
gone** -- **cash D4**, **N-4**, **N-5**, **N-73**, **N-83** and **N-103**, all inherited from X-e's
census: X-f1c3a re-pointed every rendered read at `cash_ledger.resolve_anchor` and X-f1c3c deleted
both columns, and `balance_at/_investment.py` was deleted outright at `17c57cde`. They are kept
rather than re-pointed line by line, because a census rewritten to today's addresses stops being a
record of what was measured.

**The ledger stands at 88 rows.**

| id | finding (one line) | worst measured | status | closed by |
|---|---|---|---|---|
| FU-1 / F1 | The Van Loan's one unexplained true-up STEP -- an operator question, not a code fix. Prod account 8 carries exactly three anchors | `$905.33` against the servicer's statement | OPEN -- awaiting the operator. Blocks nothing; the ledger is self-consistent either way | operator (unchanged by the R-AO triage) |
| FU-3 | Standing overpayment resolves at TODAY for any as-of: `_resolution.py:294` reads the CURRENT template row with no as-of, inside a pass the context pins | latent | OPEN, re-verified 2026-07-27 | X-i2 |
| N-96 | `balance_at.interest_by_period_for_account` is a public seam entry with ZERO `app/` callers, and `__init__.py` states as fact that two screens read it | a seam entry no screen can reach, documented as one two screens read | OPEN, AST-verified 2026-07-27 | X-e |
| N-97 | `app/utils/dates.py:314` cites `balance_resolver.daily_cash_balance_series` as a live consumer of `attribution_date`; that producer was deleted at X-c2b3. The rule it states is still true | a present-tense claim naming a producer deleted a month earlier | OPEN, reported not fixed | X-p |
| N-18 | The recurrence bound and what was GENERATED can disagree, in both directions: `match_periods` neither backfills nor prunes | bound 1 `2026-04-01` vs bound 2 `2026-03-01` | OPEN, ruled to X-k (R-AP) | X-k |
| N-19 | A RETIRED loan's recurrence bound does not exclude the CURRENT pay period (`period.start_date <= end_date` still matches) | -- | OPEN, ruled to X-k | X-k |
| N-23 | A refused loan payment fails an entire carry-forward batch | whole batch lost | OPEN; batch semantics ruled at X-k's trace, not folded into a prune commit | X-k |
| N-24 | Three generation call sites have no `ValidationError` handler, so a refused write 500s | 500 on pay-period extend and on unarchive | OPEN, ruled to X-k | X-k |
| N-25 | A real runtime import cycle was invisible to `cyclic-import`, because a TYPE-ONLY import of the same module excluded the edge for every import of it | a cycle plus an inverted dependency, gate-green | instance closed (`8285fcad`); the CLASS is an upstream limitation with no shared root here | developer-decision (dated 2026-07-27) |
| N-33 | 13 cross-package private-NAME imports: four route modules import 13 private names from `app.utils.account_validation`, so the names lie about their visibility | -- | OPEN, ruled to E2-0, which asks the same question one level down | E2-0 / E2-n (R-AO) |
| N-35 | The statement tier `ledger_report_service` is not W9909-scoped, so a public balance-at-T born there would be unguarded | a balance-at-T outside the seam with every gate green | recorded, NOT fixed; the false absolute claim was corrected in-commit | E2-0 |
| N-36 | The resolver's money-blind replay keys its rate on the PAY-PERIOD START where the genesis walk keys on the DUE date -- one question, two rules, deliberately | none measured; the divergent surface is discarded on every production read | recorded, deliberate, stated at the site so it cannot be rediscovered as an accident | X-n |
| N-42 | Nothing in the app records WHEN money moved, so the balance engine's ACTUAL clock is a data-entry click | `$36,323.99` gross swing against a `-$159.73` true error | RULED 2026-07-25 (R-N); R-EB re-scoped the fix into X-f1..X-f5 | X-f |
| N-40 | `live_amount_overrides` reads the wall clock, so a fold given an explicit `as_of` is not fully as-of-pure | latent; derive-mode loan-payment shadows on a historical read | OPEN, re-verified live 2026-07-27 | X-i2 |
| N-56 | The desktop grid's two self-refresh endpoints compute the SAME per-period view twice per `balanceChanged` | `165.6 ms` of duplicate producer work per refresh | OPEN, and NOT closed by X-i1's memo: two HTTP requests, two contexts | X-i |
| N-58 | The analytics calendar renders a flow on one day and the balance step for it on another, with no row explaining the gap | chip and step up to 25 days apart; median 2 | recorded, NOT fixed; three options open, ruled at its own step | X-p |
| N-14 | `contractual_schedule_from_origination` is computed twice per pass on the property page | -- | OPEN; its condition (prove both call sites' rate inputs identical) is X-i1's work | X-i1 |
| E2 | The super-package boundary: the option that would dissolve the last name-keyed gate. Row kept so the id resolves | -- | RATIFIED 2026-07-26 and promoted back into Section 5 as Phase E2 | E2-0 / E2-n |
| N-72 | A modelled asset's balance was three producers merged by a preference order; the merge is deleted, the `/savings` + `/investment` REDUNDANCY half is not | the modelled base built 14 times for 4 accounts on one render | merge half closed with the module; redundancy half open | X-i1 (the redundancy) |
| X5 | Anchor `effective_date`: a user cannot enter a balance read off last month's statement and have it land on last month | -- | OPEN, optional, NOT committed to; largely answered by `observed_on` -- re-verify before scoping | X-e (widened 2026-07-27) |
| N-79 | The investment chart's projection axis and its contribution timeline are on two different calendars, so the chart answers differently by the day it is opened | not measured in dollars | recorded, NOT fixed; inside the forward WHAT-IF engine ruling **R-U** deliberately keeps on `growth_engine` | X-l |
| N-82 | Past the pay-period horizon the replay's ACCRUAL keeps running while its CONTRIBUTION tier stops -- a HALF model, with nothing on screen saying so | `+$5,427.07` at six months past the horizon | RULED at R-AG: let the fold answer and record it. Not live today | X-l |
| N-83 | A Property's value is answered two ways on adjacent screens -- the modelled map against the account's asserted balance | `$965.03`, growing with time since the last assertion | recorded; X-g2b TRIED the fix and reverted, because pointing the hero at the seam produced two new defects. **The CACHE half shipped at X-f1c3a** (`c16bdb3b`) -- there is no `current_anchor_balance` column to diverge from any more -- and what survives is the DISPLAY question | X-j |
| N-85 | `interest_by_period_for_account` has no production caller and survives on its own tests | -- | recorded; the same defect as N-96, de-duplicated against it | X-e |
| N-87 | The dashboard pulse justifies its cash basis by agreement with a grid that stopped agreeing at PR #47 | `$704.72` live; `$21,856.66` at the last projected column | RULED at R-AK; both false comment clauses are deleted, the divergence itself is X-j's | X-j |
| N-89 | The modelled contribution tier re-queries the whole pay-period calendar its caller has already loaded | 1 redundant calendar query per modelled read; 4 per grid refresh | recorded, NOT fixed | X-i1 |
| N-90 | R-K's identity is a property of the construction only in its BOUNDARY form; the form the screens render needs contiguous periods and is unverifiable at the leftmost column | none measured; every production caller passes the full contiguous set | recorded, NOT fixed; closing it changes what the grid displays | X-j |
| N-91 | The modelled-contribution feed is measured against a clock nobody pinned: the employer-match basis resolves at an implicit `date.today()` and across all scenarios | `$3,631.74` today against `$0` for a pre-horizon read | recorded; threading `ctx` MOVES MONEY and needs its own measurement and ruling | X-i2 |
| N-92 | The contribution feed is the seam's one un-memoized per-pass derivation and the most expensive loader in the set | `~9.4 ms` per investment account per seam read | recorded; shares a fix with N-91 rather than competing with it | X-i1 |
| N-93 | Every grid render entry pays the modelled contribution load, including the entry that reads none of it | `2.7 -> 14.8 ms` per modelled grid render entry | recorded; the load is not waste, the per-pass repetition is | X-i1 |
| N-86 | The `/investment` limit CARD and the projection beside it read two different YTD boundaries, and only one is a function of the window | `$1,000.00` of annual-limit room per period of divergence, compounded | recorded; correct on both surfaces today and pinned in both directions | X-m |
| N-115 | The dashboard tracks section pays TWICE for three loaders, and the expensive one is a full paycheck-engine run | the second breakdown alone: `7.2 ms / 7 SQL` | OPEN; widens X-i1's input tier | X-i1 |
| N-116 | The period preconditions are the baseline precondition's twin: 96 branches in 49 `app/` files, resolving to ~50 answers over FIVE distinct questions | `+$3,228.55` of net worth on a 5-day calendar hole | OPEN | X-x |
| N-117 | Fifteen surfaces decide the no-baseline state WITHOUT the balance seam, and neither instrument X-v built could see one of them | `$0.00` today and unreachable, for the same measured reason as N-112 | OPEN | X-y |
| N-121 | The cockpit reduces its liquid-savings total TWICE per render and publishes it under two context keys | `$0.00` today -- one function, one input, equal by construction | OPEN | X-ac |
| N-122 | The asset-vs-liability rule has a SECOND home on the WRITE path, and X-z's own docstring said it could not | `$0.00` today: both spellings compare against the same cached id | OPEN, found independently by both of X-z's adversarial reviews | X-ab |
| N-123 | The pay-calendar WRITER refuses every payday from `today+1` to `today+13` and leaves a permanent hole on every one after `today+14` | `$0.00` on both databases today; `$3,228.55` the moment a hole exists | OPEN | X-ad |
| N-124 | The forward rolling top-up backfills HISTORY: a lapsed schedule generates past periods and populates them | `$0.00` in balance terms; the generated rows are Projected | OPEN | X-ad |
| N-127 | The interior calendar hole has NO working repair, and X-x as written points every refusal at it | `$0.00` in figures; X-x would convert wrong numbers into a dead end | OPEN | X-ad |
| N-128 | A pay-period hole breaks R-K's reconciliation identity, and it is in the FOLD, not above it | `-$140.63` on the gapped clone | OPEN; X-ad stops new holes reaching it | X-l |
| N-129 | Converting a page to "raise" silently blinds every test whose fixture calendar does not cover today, including security controls | 84 unique node ids fire the precondition; ~80 controls blinded | OPEN, measured with an instrumented full run | X-x |
| N-125 | The salary cockpit is the SEVENTH answer to "no pay period covers today" | `$0.00` -- an empty page rather than a wrong number | OPEN, measured at X-x2 and deliberately not widened into | X-x |
| N-126 | A public contribution producer has no caller in `app/`, and its whole body is a fabrication the trace was about to fix | `$0.00`: no `app/` path reaches it | OPEN, found by the call-graph pass | X-x |
| N-135 | The partition's fence covers the derived boundary and NOT the two bare fact fields, so the line step 3 deleted still compiles | `$0.00` today: every remaining read is a legitimate raw-date use | OPEN, RULED 2026-08-01: wrap both fields, in two distinct types | X-d |
| N-138 | The app has TWO "today"s, no enforced rule about which is which, and NO INSTRUMENT THAT CAN SEE THE DIFFERENCE: 78 `date.today()` calls in `app/`, 191 in `tests/` | `$0.00` today: production pins the zone | OPEN, and a DECISION before it is work -- **the first task is the INSTRUMENT, not the sweep** | developer-decision (2026-08-02) |
| N-142 | `request.args.get(..., type=int)` is the one submitted-id surface X-ae did not convert, and it is lax in the same way | `$0.00` and no crash; `'١٠٦'` returns 106, `' 2026 '` returns 2026 | OPEN; needs a per-site ruling, id against non-id | X-ah |
| N-144 | `settled=` is a caller's OPINION about a row that already knows its own status | `$0.00` today | OPEN; R-DU upgrades the remedy from removable to unrepresentable | X-ai |
| N-145 | `transfer_service.py` sat at 999 of pylint's 1000-line ceiling, blocking X-d | `$0.00` -- a size gate, not a money defect | ANSWERED by R-DN; X-aj1 took it to 987, which N-152 says is not a solution | X-aj |
| N-147 | Two custom checkers still enforce a rule with a list of module names, which is prose plus a detector that must be kept complete | `$0.00` directly; each list is a rule that can rot | OPEN, ruled into Phase G, which runs inside E2 | G1 |
| N-148 | The transfer -> shadow mirror rule is written THREE times and the three already disagree on `scenario_id` | `$0.00` on today's data -- nothing edits a transfer's scenario | OPEN, ruled into its OWN step rather than folded into X-aj | X-ak |
| N-149 | `create_transfer` applies NO transition check, so a transfer can be BORN in a status the state machine exists to exclude | `$0.00` today, a property of the callers rather than of the design | OPEN; carried by X-aj2, because the fix is the born-status RULE | X-aj2 |
| N-150 | A transfer shadow STORES five fields Transfer Invariant 4 says must always equal its parent's, and NOTHING enforces the equality | `$0.00` today | OPEN, and it REVERSES X-ak's scope: rule the copy before the copiers | X-ak |
| N-151 | The two `mark_done` routes pass an explicit `paid_at=now()`, which wins over the seam, so a replayed settle still re-dates a settled transfer | unmeasured, deliberately: the same unbounded gap N-146 carried | OPEN; the fix is a rule about what `mark_done` MEANS on an already-settled row | X-aj2 |
| N-152 | `transfer_service.py` lands at 987 of 1000, so the size gate is answered for X-d and NOT solved | `$0.00` -- a size gate; 13 lines of headroom against X-d's ~9 | OPEN; the structural answer is a PACKAGE, one private leaf per verb | X-ak |
| N-153 | `_reconcile_postings_after_update` re-syncs both endpoints' anchors, and ruling R-DK dissolved the reason its docstring gives for existing | `$0.00`; both calls are idempotent and land on the same state | OPEN, deliberately not taken at X-d (R-DT) | X-ai |
| N-154 | `useless-suppression` does not report a stale `duplicate-code` disable, so one that suppresses nothing survives every gate | `$0.00`: a stale suppression writes no wrong figure | OPEN; the instrument is undecided, which is why it is a step | X-al |
| N-155 | The checked-projection assert grades a HALF-FINISHED operation in every batch loop, not only in the delete window R-DM ordered around | three CONFIRMED production defects and one plausible | OPEN, and it PARKS X-d | X-ai |
| N-156 | The 1000-line ceiling has split a SECOND module, and that split was recorded in an as-built rather than as a finding | `$0.00`: a public module writes no wrong figure | OPEN; same class and same answer as N-152 | X-ak |
| N-157 | `resync_anchor_postings` is a NAME, not a chokepoint, and its docstring claims otherwise: FIVE entry points reach the assert, not three | `$0.00` today | OPEN; R-DU deletes the rule instead of relocating it | X-ai |
| N-158 | The shared checked-projection assert leaves its SIGN convention as an unnamed operator inside each caller's loop | `$0.00` today; a sign flip still balances every entry | OPEN; the option not considered is a per-package `posting_deltas(walk)` accessor | X-ai |
| N-159 | Transfer retirement stays TWO halves a caller must remember to pair, which is the obligation `retire_transaction` was built to make structural | `$0.00` today: `delete_transfer` is the single path and pairs them correctly | OPEN; R-DU may dissolve the question before X-ak reaches it | X-ak |
| N-160 | Two exported loan writers reconcile the ledger without ever grading it | `$0.00`, and unreachable from `app/` -- an unwired writer posts nothing | OPEN; under the one verb a half-ledger writer cannot exist | X-ai |
| N-161 | The anchor-correction reconcile violated the R2 attribution rule the source reconcile obeys, in the one branch that did not run | `$0.00`; no rendered figure moved, tested rather than asserted | RULE half CLOSED at X-ai-r (PR #81, in production); the family it belongs to dies at X-f4 | X-ai |
| N-162 | A walk-driven whole-account re-derive cannot see a source that LEFT the settled set, so X-ai-a's defining sentence has a hole in it | `$0.00` today and unreachable from `app/` | OPEN, and a REQUIREMENT on X-ai-a rather than a defect to fix afterwards | X-ai |
| N-163 | A registry-scoped commit-boundary grader is BLIND to a bulk `UPDATE` or `DELETE` of a source table, and the door is 20 call sites wide | unquantified today, and that is the finding | OPEN, owned by X-ai-g, which classifies all 20. The remedy is NOT a checker forbidding bulk statements | X-ai |
| N-164 | A transfer's posted effect is computed by TWO rules, and account-owned re-derivation would turn their disagreement into a write OSCILLATION | `$0.00` today; Transfer Invariant 3 holds | OPEN, ruled RESOLVED BY DESIGN at X-ai-a (R-DV / R-DW) | X-ai |
| N-165 | A whole-account re-derive writes on OTHER accounts' ledgers, and the design gives no rule for grading them | `$0.00` today and unreachable from `app/` | OPEN, and a REQUIREMENT on X-ai-a: return the pairs touched and re-enqueue | X-ai |
| N-166 | Two concurrent re-derives of one account double-post its correction, and no DB constraint can catch it | `$0.00` today; the per-row writers are narrow enough that `version_id` covers the window | OPEN, and a REQUIREMENT on X-ai-a: a per-`(account, scenario)` advisory lock | X-ai |
| N-167 | The append-only ledger has no reversal linkage, so "which entry undoes which" is recoverable only by re-running the reconcile arithmetic | `$0.00`; the cost is diagnostic and this arc paid it twice in one week | OPEN, owned by X-ai-s, the only cheap moment to add it | X-ai |
| N-169 | A chronology primitive both ledgers now depend on lives in the LOAN package, and the cash fact it replaced is read by nothing | `$0.00`; both halves are structural | OPEN; the cash half SHIPPED at X-f1c3b (`379ed1af`); the rest goes with the correction family | X-f4 |
| N-171 | The true-up residual is booked to EQUITY, so real economic activity is structurally invisible on the income statement | `$15,065.08` gross / `-$1,495.10` net over four months on Checking | OPEN; resolved by R-EB (d), and only correctly AFTER the date work | X-f3 |
| N-172 | The book-vs-bank gap at an assertion IS the rendered balance error immediately before it, and it is not small | `$321.52` average, `$1,853.92` worst, non-zero on 96% of assertion days | OPEN; the churn half is X-f1/X-f2's, the residue half X-f3's | X-f3 |
| N-173 | For two thirds of settled Checking rows the settle date is a bookkeeping-session artifact: 88 of 135 share a click-minute with another row | 65.2% of the account | OPEN; this is the GENERATOR of N-172's churn | X-f1 |
| N-174 | The PROJECTED END BALANCE -- the figure the developer actually budgets against -- inherits the whole gap, and the invariant protecting it is unbuilt | the affordability decision itself | OPEN; X-f3's ship gate is that invariant passing as a TEST without a true-up, in both orders | X-f3 |
| N-176 | Five posted correction days on Checking carry no surviving anchor history row, and all five self-healed to `$0.00` | `$0.00`, by construction and by measurement | OPEN as a RECORD, not a defect: X-f4 deletes a branch that has demonstrably fired in production | X-f4 |
| N-177 | The `Settled` status has no writer, no reader that distinguishes it, and zero rows on both tables | `$0.00`; a THIRD member in the predicate every balance rule in this arc is written against | OPEN; ruled its own step rather than taken inside X-f1 | X-am |
| N-187 | The loan resolver decides "has this payment happened yet?" from the pay period and the posted ledger from the day the money moved, so a payment settled before its period begins counts TWICE | `$1,003.87` in the reproduction; `$0.00` today and one click away | OPEN, RULED (R-EK) | X-an |
| N-188 | The W9907 checker still describes the seam as maintaining `paid_at`, in the message it shows the developer | `$0.00` -- the checker's behaviour is correct | OPEN; not fixed inside X-f1c because X-aj2 DELETES this checker | X-aj2 |
| N-185 | `settled_on` is now exactly as load-bearing as `status_id`, and only one of the two has a fence | `$0.00` today | OPEN; do NOT grow W9907 an arm -- X-aj2 replaces its write door with a read-only attribute | X-aj2 |
| N-180 | A de-duplication rationale in `balance_at/_loan_interest.py` was falsified by R-DH, and whether the two sets can still differ for any other reason is UNVERIFIED | `$0.00` -- the code was never wrong, only the reason beside it | OPEN; the paragraph is replaced in place, the question survives | X-e |
| N-139 | Nothing prevents a submitted digit string being parsed laxly again, and a checker on the method NAME does not prevent it | `$0.00` today; the exposure is the NEXT id parse | OPEN, and the INSTRUMENT is undecided, which is why it is a step | X-ag |
| N-191 (X-f1c3c's residue pass 2026-08-04, found while re-justifying a comment ruling R-EO had falsified; census by grep over `app/`) | **The app's civil day rests on a compose environment variable rather than on a rule, at 113 call sites.** `app/utils/dates.display_today()` is the user's civil day; `date.today()` is the PROCESS-local day, and `app/` calls it **113 times**. Production pins `TZ: America/New_York`, so the two are equal there and none of the 113 is a live defect -- which is exactly the problem: their correctness is a property of the deployment, not of the code, and finding N-138 already records that neither clock gate can detect a process-vs-display SPLIT. Two sites found in this pass decide something against the user's CALENDAR and so look like the wrong side of the line: `pay_period_admin.top_up_rolling_window` (`as_of` for "keep N periods ahead of today") and `classify_periods_bulk` (`as_of` for whether a period is HISTORICAL, which is a truncate LOCK decision). `display_today`'s own docstring calls `date.today()` the UTC day, which is false in production, where the process clock is Eastern | `$0.00` today and on any TZ-pinned deployment. The exposure is a one-day boundary error in whether a period is historical (a truncate refusal) or whether the rolling window is short, for the hours the two clocks disagree -- reachable the moment a container runs unpinned, which CI already does on purpose | **OPEN.** Not fixed here: 113 sites is a systemic surface far outside a leaf that deletes two columns, and each needs a per-site ruling on which day it means -- the same shape N-142 has for query-string ids. Recorded with the census rather than the two anecdotes | X-ak |
| N-192 (X-f1c3c's residue pass 2026-08-04) | **A `PostingError` that was referentially unreachable is now held out of reach by code alone, and its comment still cited the deleted constraint.** `reconcile_account_anchor_corrections` raises when an account has anchor corrections to post while its owner has no pay periods. That was impossible under the FK: an assertion carried a NOT NULL `pay_period_id` pointing at one of those periods. Ruling R-EO deleted the column, so what keeps the state out of reach is now three separate code paths -- registration opens a bootstrap period before creating the default account, `truncate_pay_periods` only deletes indices ABOVE the one kept so index 0 survives, and `reset_pay_periods` regenerates before returning | `$0.00`; unreachable through every UI path traced. A 500 on account create or on any true-up for the affected user if it is ever reached | **OPEN, deliberately as a loud failure.** The comment is corrected to state what actually holds it out of reach; the raise STAYS, because this reconcile derives each correction's period from its day and an empty calendar is the one state that could silently mis-file every correction an account has. Worth a schema-level guarantee if one exists that does not re-file a bank fact under a budgeting artifact -- which is what R-EO deleted | X-ak |
| N-193 (X-f1c3c's concurrency review 2026-08-04; the deadlock REPRODUCED against a real PostgreSQL with its own `DeadlockDetected` DETAIL line, the statement ordering CAPTURED from a real loan-payment settle) | **The per-user write lock closes the reconcile race and opens an advisory-vs-ROW-lock cycle, because it is not the FIRST lock its transaction takes.**  A settle takes row locks first -- `update_transfer` UPDATEs the transfer and both shadows, they flush, and the posting sync reaches `lock_user_writes` only afterwards (measured: statements 2-4 against statement 19) -- while `truncate_pay_periods` / `regenerate` / `reset` take the lock first and then bulk-DELETE pay periods, which CASCADEs to `budget.transactions` and locks exactly the rows the settle may hold.  Same user, opposite orders.  **A first version of this step's docstrings and record called deadlock "structurally impossible on every request path"**, an argument that considered only advisory-vs-advisory ordering | PostgreSQL detects it and aborts one transaction: an unhandled 500 on a money route, **no money corrupted** (the loser rolls back atomically).  Needs a settle and a schedule rebuild for one user to overlap -- two browser tabs.  `$0.00` of ledger divergence, against the silent permanent divergence the lock replaces, which is why the lock ships anyway | **OPEN.**  The fix is the invariant stated in `user_write_lock`'s docstring -- *this lock must be the FIRST lock a transaction takes* -- which means acquiring at the write-SERVICE entry (the status seam, `update_transfer`, the delete/restore paths) rather than inside the reconcile.  Deliberately NOT done in this leaf: it is a different change with its own blast radius and needs its own review, and the decompose-at-the-leaf-boundary lesson is this step's own.  What ships here is the correct claim, not the correct lock placement.  **NARROWED at X-f1c4b**, which moves acquisition to the TOP of both anchor doors so those two satisfy the invariant; the settle paths still do not, and they are what this row is now about | X-ak |
| N-198 (X-f1c4c's test-quality review 2026-08-04, mutant `drop_notes` survived the suite) | **`anchor_service.stage_anchor_true_up`'s `notes` parameter has no production caller.** `routes/accounts/crud.update_account` omits it and `account_service.create_account` writes the origination row's `notes` directly rather than through the stager, so the only caller passing it is one test, which asserts nothing about it. Deleting the parameter left the suite green. It is the shape findings **N-96** / **N-85** record one tier up -- a public surface kept alive by its own tests | `$0.00`: an unused keyword writes no wrong figure. The cost is that the audit trail's "which door wrote this assertion" label is available and unused, so an assertion's provenance is recoverable only from its balance and day | **OPEN.** Not deleted in X-f1c4c: the parameter is one of the two things that would let the remaining write doors be told apart in the audit trail, and X-f1e1 answered how many doors there are (one), so the parameter now has NO caller and the table has TWO writers -- the stager and `account_service.create_account`, which builds the row directly. Nothing in `app/` reads the column | X-f1e2 |
| N-199 (X-f1c4c's design + test reviews 2026-08-04; the dashboard half CORRECTED by the re-review, which read the template rather than the claim) | **The back-dated acknowledgement reaches ONE of the five surfaces the anchor editor opens from.** It rides on the out-of-band `#anchor-as-of` snippet. The cockpit, the investment hero and the cash hero (`revert=accounts` / `investment` / `cash`) carry no such element and are skipped outright. **The DASHBOARD carries one but destroys it**: `dashboard/_pulse.html` puts `#anchor-as-of` inside `#pulse-section`, which is `hx-trigger="balanceChanged from:body"` -- and the true-up's own response fires that trigger, so the pulse re-fetches and wipes the snippet within one round-trip. Only the grid's, which sits outside any refresh target, survives. A first version of this row claimed the dashboard was covered. Separately the amortizing-kind refusal is the one rejection on this door still returning a raw 422 rather than a designed fragment | `$0.00`: no figure is wrong, and the kind refusal is unreachable through the UI because `anchor_form` refuses to OPEN the editor for a loan. The exposure is a user on four of five surfaces believing a back-dated correction did not save and entering it again | **OPEN.** The acknowledgement needs a mount no `balanceChanged` region owns, which is a per-surface rendering decision rather than a one-line addition. The grid -- the developer's own highest-stakes surface -- is the one that works, so this is recorded rather than rushed | X-f1e3 |
| N-197 (X-f1c4c's trace 2026-08-04, read off the two doors) | **The app's two anchor doors bound "not in the future" on two DIFFERENT clocks, and one of them also sets a form's `max`.** The cash door refuses through `display_today()` (the user's civil day); the loan door refuses through `date.today()` in BOTH its schema (`schemas/validation/loans.py:161`) and the input bound its form renders (`routes/loan/dashboard.py:548`), which is the process day. One question -- "has this day happened for this user" -- with two answers, on the two surfaces that ask it about a balance assertion. A specific instance of **N-191**'s 113-site census, kept as its own row because it is a DIVERGENCE between two doors rather than one site's choice, and because X-f1c4c is what puts the cash clock on a user-visible form | `$0.00` on any TZ-pinned deployment, and production pins `TZ: America/New_York`, so the two are equal there. Unpinned (CI does this on purpose), the loan door accepts and pre-fills a day the user has not seen, for the hours the clocks disagree | **OPEN.** Not fixed in X-f1c4c: it is the LOAN door, and moving its refusal boundary is a behaviour change on a write path that needs its own measurement rather than riding a cash-form commit | X-ak |
| N-196 (X-f1c4b's design review 2026-08-04, verified against the tree) | **The loan resolver and the loan write door break an anchor tie two different ways.** `loan_resolver._periods.select_latest_anchor` takes `max(key=(anchor_date, created_at))` -- first-maximal-wins, no `id` term, and `LoanAnchorFact` carries no `id` to add -- while `anchor_service._governing_loan_anchor` orders `(anchor_date, created_at, id)` DESC, last-wins. `CreatedAtMixin.created_at` is `server_default=func.now()`, which PostgreSQL evaluates at TRANSACTION START, so every row written in one transaction shares an instant and a tie between two DIFFERENT balances is producible by any backfill, migration or fixture that writes two anchors at once | `$0.00` on today's data: production carries 6 loan anchor events with no same-date pair. Reachable by a future backfill, and the consequence is that the door compares against a row the dashboard is not showing -- N-194's shape, one table over | **OPEN.** Not fixed in X-f1c4b: adding the `id` tie-break changes which anchor a loan RESOLVES to, which is a read-path behaviour change on the balance the loan pages render, and it needs its own measurement rather than riding a write-door commit. The false claim that the two orderings agree was deleted in the same commit that found it | X-an |

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

1. **This is the only live planning document for the balance arc.** The archive is read-only
   history. If a step needs more design than Section 5 carries, the design happens in the commit or
   PR that ships it, or amends this file. New standalone plans, audits and follow-up documents for
   this arc are prohibited; findings become rows in Section 6.
2. **When a step ships: tick its box, append the commit hash, and re-point every Section 6 row that
   named it.** Rule 6's gate fails the build otherwise, which is the only reason this instruction is
   reliable. **Step IDs are append-only** -- a decomposition appends a suffix, and nothing is
   renumbered for readability.
3. **When a ruling in Section 4 is answered, record the RULE and its date in place** -- one line,
   not the deliberation. The deliberation belongs in the commit that ships it.
4. **This document is capped at 1,200 lines, and the cap is a gate** (`tools/plan_gate/`, which runs
   on every commit that touches this file). **There is no exemption for completed work** -- that
   exemption is how the old ~500-line prose target let the file reach 6,688 lines. **When the cap
   binds, the answer is an ARCHIVE MOVE under rule 5, never a trim of live content**: shrink the
   record of what is DONE, never the specification of what remains. **Do not raise the number.** It
   is the 2026-08-04 live-content floor (976 lines, from 6,688) plus room to work, and that floor
   moves DOWN as steps ship, because a shipped step's specification becomes one line in an as-built
   record. If it is not moving down, the archive move is overdue.
5. **A completed span is archived whole and CONDENSED, not moved verbatim.** An as-built record is
   one line per step: the step ID, its commit hash, what changed, and what it closed. The narrative,
   the measurements and the review residue stay in the commits, which is where the code agrees with
   them -- **this arc has repeatedly carried a claim into an as-built that the code later
   contradicted**, and prose nobody re-verifies is worse than a hash anyone can check. Three
   conditions, all requirements rather than courtesies:
   * **Unfinished work stays here whichever half it came from.** Section 6 is the single home for
     every open question.
   * **No live sentence may depend on an archived one.** Where a surviving ruling cited an archived
     one, restate the cited rule inline at the citation. The archived record may reference this
     document; never the reverse.
   * **Re-verify what you carry.** A row that says "open" because nobody re-read it is worse than no
     row. Rows carried WITHOUT re-verification must say so.
6. **EVERY finding has an OWNER, and the owner vocabulary is closed** (ruling R-AQ). Section 6's
   last column is one of exactly three things:
   * **a live (unticked) Section 5 step ID** -- the normal case;
   * **`operator`** -- a question only the developer can answer from outside the code, with the
     question stated;
   * **`developer-decision`** -- a fork the developer has taken, dated, with the options named.

   **Retired as values: "own commit", "own step", "own arc", "if ever", "recorded, deferred",
   "residue", and any wake condition.** They all mean nobody. **A finding is BORN with an owner**:
   the review or trace that records it assigns one in the SAME commit. **An owner must be
   TICKABLE** -- every step ID cited as an owner is a CHECKBOX in Section 5, never a plain bullet,
   or "did its owner ship?" is unanswerable. **This rule is a GATE, not a discipline**
   (`tools/plan_gate/test_balance_plan_ledger_integrity.py`), which fails on an owner naming a
   ticked step, an owner naming an ID that is not a checkbox, an owner outside the vocabulary, an
   empty owner cell, a row an unescaped `|` has split, a stated row count that does not match the
   table, and a document over the rule 4 cap.
7. **A finding is not deferred for cost** (ruling R-AQ). "Materially larger than this step" is a
   reason to give something its OWN step, never a reason to leave it open. A finding that costs
   `$0.00` on today's data is not resolved; it is a defect waiting for the data to change, and the
   data changes without asking. Where a fix must follow another step to be decided correctly it is
   SEQUENCED behind it with the reason stated -- which is a schedule, and is what a deferral is not.
8. **"Where the arc stands" is a SIGNPOST, replaced each session and never appended to** (developer
   instruction, 2026-08-04: *"a short section to orient a reader to where to find information --
   what just landed, what is in flight, and what is next, with pointers to the more detailed
   information"*, and *"replace, not add on to"*). It names those three things and POINTS at the
   detail; it does not carry the detail. **The gate caps it at 30 lines**, which is too small to
   hold a narrative -- that is the enforcement, because under a cap adding a paragraph forces you to
   say what leaves, and the honest answer is nearly always that it belonged somewhere else. **Where
   it goes instead**, each being where the next reader will actually look:
   * a constraint on a step, or how a step was verified -> **that step's Section 5 entry**;
   * a defect, however it was found -> **a Section 6 row, born with an owner** (rule 6);
   * a rule that will still be true next month -> **Sections 7-9**;
   * a measurement of work that SHIPPED -> the commit, and one line in an as-built record.

   Nothing else. If the section is growing, it is being used as a log again -- which is how it
   reached **1,019 of the file's 6,688 lines**, after an extraction had already emptied it once.
