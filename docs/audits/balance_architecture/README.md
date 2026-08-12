# The cash balance architecture: the plan of record

**This is the ONLY live document for the balance arc, and it carries the work that REMAINS.**
Amendments are edits HERE, a shipped step gets its checkbox ticked with its commit hash HERE, and no
new planning documents get written for this arc. **It is line-capped and the cap is a gate**
(`tools/plan_gate/`, where the number lives -- quoting it here would be a second copy, and the one
this sentence used to carry was 200 lines adrift) -- when it binds, archive a completed span rather
than trimming a live one. **The rules are `../../plans/conventions.md`**; read rule 6 before
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
| **just landed** | **X-au-b** (`81ad02d1`) -- the amount model's resolver: a TOTAL dispatch over ruling R-FI's five rules, each delegating to the producer that already answers it, refusing rather than falling back for a row it cannot place. UNWIRED; X-au-c routes the readers. **997 of 997 rows on a 2026-08-12 production clone agree with the answer the app publishes, 0 refusals, `$0.00` drift**, and the SALARY rule's row set is exactly the override map's 51 keys. **TWO adversarial reviews found six defects the tests and the oracle did not**, every one reproduced before it was fixed -- a basis MISS indistinguishable from a producer's deliberate omission, so a manual loan payment resolved outside its own basis silently dropped a standing extra ($1,250.00 against $1,400.00); the manual arm answering from TWO different bases depending on whether an extra existed; a template switched to derive-mode still priced from the series it kept from its manual days; and **an ORACLE that could not see its own subject** -- with the whole resolver replaced by `return txn.estimated_amount` it still reported 997 of 997 correct, because for 946 rows the app's own answer IS that column. Before it **X-au-a** (`81138fb8`) made a recurring definition's amount an effective-dated SERIES | Section 5 |
| **how it was measured** | **The BEFORE and AFTER are one production row, not an estimate.** The reachable set is exact: two Projected envelope-tracked rows carry purchases, so exactly two rows could reach this door -- 2231 *Gas* moves by `$31.02`, and 2281 *Groceries*, whose stored actual already equals its entry sum, does not move at all. The type rule refuses nothing that exists: of 17 production rows carrying an income type in Paid, **all 17 are transfer shadows**, and every one of the 143 non-shadow settled rows agrees with its own type. Ruling **R-FJ** rests on both signatures being present -- 8 entry-less envelopes booked at estimate (`$794.79`) through a door, 1 at `$0.00` through carry-forward. **And the AUDIT LOG refuted the design's own premise**: the one row cited as evidence that a human types into the Actual box (2281, `$533.08` against a `$500.00` budget) was written by `settle_from_entries` in a single statement with `status_id` and `settled_on` (audit_log id 2978), so no envelope row carries a hand-entered actual and the box could be removed outright. Ten controls in a suite named for the door, each shown to FIRE: routing the door back to the seam fails four | Section 5, X-ap |
| **in flight** | nothing OF THIS ARC. **Neither the live branch state nor what production runs is recorded here, and neither may be** -- read them from `git branch -vv` and `docker inspect shekel-prod-app --format '{{.Image}}'`. This row asserted "no feature branches exist" on 2026-08-08 while block 10's `C1` was being built on `feat/pay-calendar`, and it then stored a prod image digest that a deploy falsified on 2026-08-09 -- twice over, a volatile value beside no reconciler, which is this arc's own root cause worn as a signpost. The migration head is **`a9d3c15e7f42`** (X-au-a's, the first since `c7f3a9d1e864`) | -- |
| **blocked on you** | **ONE, and it is not this arc's**: the SEQUENCING ruling the recurrence arc opened at X-an-a, whose two options are stated in that arc's section 0. `steps.md` has since sequenced `R6` behind `R5`; whether that DISCHARGES the ruling or merely records one of its options is the developer's to say. **N-227 is RULED (R-FG)**: the attribution bound stays, two of that finding's own claims are withdrawn as measured wrong, and the real defect underneath it -- an envelope's close booking a purchase the statement never saw -- is fixed structurally by `_wholly_spent_by`. Everything X-f2-c2 was waiting on is ruled: the bill's amount (**R-FB**), the panel's shape (**R-FC**), income rows (**R-FD**), the settle's amount rule and where it lives (**R-FE**, amended by **R-FH** to write the cache rather than the human's column), and what makes a tick correctable (**R-FF**) | `implementation_plan_recurrence_redesign.md` section 0 |
| **next** | **Read it off `../../plans/steps.md`'s order table, never off this row** -- the first row of that table is the next step, and a row whose `starts` reads `NOW` can be picked up today whatever its rank. This row states only what CHANGED the order and is not derivable from the index: **X-au-b shipped (`81ad02d1`), so X-au-c is unblocked and the amount model's six remaining leaves re-ranked; the CC PAYBACK kind became its own leaf X-au-i, which is where N-243 now points**. Before them, X-ar was SUPERSEDED by the amount model (`X-au`, ruling R-FI, developer 2026-08-11) -- a derived amount stops being stored at all instead of being kept true by a reconciler, so block 11 replaced X-ar's slot with eight leaves and two new steps | `../../plans/steps.md`, Section 5 |
| **complementary arcs** | TWO, neither part of this arc and neither pausing it -- the recurrence redesign (block 9) and the pay calendar (block 10, opened 2026-08-08). **The pay calendar's `C2` IS this arc's `X-l`**, and also recurrence `R-F12`: one commit under three names, so whoever builds it must satisfy all three specifications. How either arc's steps interleave with this one's is the index's answer and is not restated here | `implementation_plan_recurrence_redesign.md`, `implementation_plan_pay_calendar.md`, blocks 9 / 10 |
| **why this shape** | the anchor half was redesigned from scratch by ruling **R-EB**; R-EQ designed the duplicate rule that had only ever been re-keyed, R-ER put the day rule in the module that owns what an assertion is, X-f1e1 deleted the second DOOR, X-f1e2 the second WRITER, X-f1e3 the mount that was destroying its own message | Section 3.3, and R-EB / R-EQ / R-ER / R-ES / R-ET in Section 4 |
| **the live lesson** | **a ROUTE that decides what a status change means is holding a money rule.** The seam is the status MECHANICS, and for a cancel the mechanics are the whole act -- which is exactly why reaching for it to settle a row looks right and is not. The census of doors was never the fix; moving the DECISION out of the route was, and the same shape is still open one level up: nothing structurally stops a fifth door reaching for the seam (**N-244**, X-aj2). Where a caller can pick the wrong primitive and the wrong one still compiles, the primitive is the finding | Section 8, N-244 |
| **the ledger** | Counted by `grep -c '^| balance' ../../plans/ledger.md`, never stored here -- the figure this row carried was wrong in both directions on two successive edits, which is rule 6's own prohibition demonstrated. X-f2-c2's design opened **N-224**: `transactions.estimated_amount` is a CACHE of a derivation, `income_service.live_projected_net` repairs it at READ time and writes nothing back, so a reader and a writer looking at one row get two figures -- owned by **X-au-d** since R-FI, with **X-aq** making every settle door agree meanwhile. The same 2026-08-11 trace opened **N-237** (the base annual salary is a scalar with no effective date), **N-238** (`is_override` carries two facts) and **N-239** (a period's gross depends on how far the schedule has been generated). Before it, X-f2-c1 closed **N-216** (the reconcile panel's doors had no kind gate) and opened two. **N-217**: a ruling id can resolve to TWO different rulings and no gate can see it -- two sessions appended a different `R-EX` on one day, both reached `dev`, and X-f2-c2 / X-f2-c3 cited it ambiguously until a reviewer read the table. The instance is fixed (this arc's is **R-FA**, naming its old id); the class is the new step **X-ao**'s. **N-218**: twelve files cite `anchor_settle_partition.md` at a path that does not exist -- it was archived -- and they are the modules ruling R-DH is about; **X-f4**'s. Before them, X-f2-b closed **N-204** / **N-206**, narrowed **N-205** to the 16 modelled-account assertions and re-pointed it to **X-j**, and opened **N-215** | `../../plans/ledger.md` |
| **resuming cold** | Branch from `dev`. Whether it is ahead of `main`, and whether by documents or by code, is a MEASUREMENT (`git log --oneline origin/main..dev`) and this row no longer claims it -- the previous revision said "DOCUMENTS ONLY" and block 10's `C1` shipped `app/services/pay_calendar/` into that gap. The repo head is **`a9d3c15e7f42`** and the test template must be rebuilt for it -- VERIFIED by reading `alembic_version` in `shekel_test_template`, not assumed; rebuild only if you add a migration. Baseline **8,973** green (`./scripts/test.sh`, 185 s at `-n 12`, measured on `feat/x-au-b` at `81ad02d1`, and green under `TZ=Pacific/Kiritimati` too); it read 8,925 before X-au-b's 48, and 8,760 before X-au-a's 69; it read 8,673 at the X-f2-c1 merge `b7c3aba3` before X-as's controls, 8,663 before X-f2-c1's 10, 8,486 before X-ad-a's and X-f2-b's, and 8,258 before R7a-1 and C1. **Pass `TEST_DB_PREFIX=<name>` when another checkout may be running the suite**, and put the venv on `PATH` -- `scripts/test.sh` execs bare `pytest`. The registries have their own gate (`pytest tools/plan_gate -c /dev/null -q`; its green count is a MEASUREMENT and is not stored here), and **what to do next is the FIRST ROW of `steps.md`'s order table**, and a row whose `starts` column reads `NOW` can be picked up today whatever its rank -- see `docs/plans/conventions.md` rule 14. **The venv must be ACTIVE for `git commit`**: pre-commit hooks are `language: system` and the first attempt failed with `Executable pylint not found`. Two REFERENCE tags, neither a rebase candidate: `xd-attempt-1-parked-n155` (X-d) and `xx-attempt-1-held-rde` (X-x). This row used to name one hand-made prod restore point; **the recurrence arc's R-F8 made every deploy dump unconditionally**, so `~/shekel-backups/` now holds one per release and the deploy REFUSES a rollback its image cannot resolve | `../../plans/verification.md` |

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

Section 5 specifies each step and 5.0 says why its block sits where it does; the EXECUTION ORDER is
`../../plans/steps.md` and who owns each open finding is `../../plans/ledger.md`, with no row
unowned.

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

**What "settled transaction rows" MEANS, and it is structural rather than a fence: a row is settled
if and only if it carries a settle day.** `status_seam.apply_status_change` is the single door that
writes `status_id`, and it writes the day in the same call. A `CHECK` cannot express it -- the
predicate lives in `ref.statuses` and a constraint cannot join -- and a reader finding a settled row
with no day FAILS LOUD, because silently dropping it from the fold is silent money loss. Established
at X-f1 and stated HERE rather than in that step's entry, because X-f2 and X-f3 read against it and
rule 5 forbids a live sentence depending on an archived one.

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
| **R-AZ** | 2026-07-27 | A producer publishes only what the presentation boundary reads |
| **R-BU** | 2026-07-28 | A residual double load is SEQUENCED behind the memo that fixes it, not deferred |
| **R-CH** | 2026-07-30 | The archived drawer's figure is NAMED for what it is |
| **R-CU** | 2026-07-30 | The ledger-class rule is finding N-122 with its OWN step (X-ab), and the false claim is corrected NOW |
| **R-CX** | 2026-07-31 | X-x is scoped to what X-l cannot subsume; the degraded-vocabulary question is SEQUENCED into X-l |
| **R-CY** | 2026-07-31 | The no-current-period answer is X-v's rule EXACTLY: raise a named error, handle it once, offer a repair |
| **R-CZ** | 2026-07-31 | A requested WINDOW that is empty is navigation, not absence, and stops answering with the absence card |
| **R-DA** | 2026-07-31 | `onboarding.has_periods` means "a period covers today", the same question every other surface asks |
| **R-DE** | 2026-07-31 | X-x is HELD behind X-ad: a refusal that points at a repair which does not work converts wrong numbers into a dead end |
| **R-DH** | 2026-07-31, amended at S1-c | An assertion is the CLOSING BALANCE for its civil day, and the day is the user's, not UTC. Six parts (a)-(f); (d) restated on an OBSERVED `settled_on`, so a NULL is "not seen on a statement" and is NOT reconciled |
| **R-DK** | 2026-08-02 | The posting self-heal stops being delta-keyed, which dissolves the reason `_reconcile_postings_after_update` gives for existing (N-153) |
| **R-DM** | 2026-08-02 | The checked-projection assert grades a FINISHED operation, never a half-finished one, and the commit boundary is the end state (X-ai) |
| **R-DN** | 2026-08-02 | ONE status seam, and the transition context stops being a caller's opinion |
| **R-DO** | 2026-08-02 | An illegally-drifted shadow status is REFUSED, not silently repaired |
| **R-DP** | 2026-08-02 | W9907 is DELETED, not shrunk -- the write door becomes structural |
| **R-DQ** | 2026-08-02 | The two remaining allowlist-bearing fences become their own phase (G), scheduled rather than remembered |
| **R-DR** | 2026-08-02 | `restore_transfer`'s four preconditions move to `_transfer_validation`, and X-aj1 ships as three commits |
| **R-DT** | 2026-08-03 | Reversing a documented deliberate decision is its own change and is not bundled into a writer swap (N-153 stays out of X-d) |
| **R-DU** | 2026-08-03 | The posted ledger gets ONE VERB and ONE TRIGGER, on BOTH ledgers, and the row-level posting writer stops being the interface |
| **R-DV** | 2026-08-03 | A journal entry is the projection of exactly ONE SOURCE EVENT, and the EVENT owns it. An account is the SCOPE of a re-derive, never an owner |
| **R-DW** | 2026-08-03 | A transfer's entry has ONE valuation; a broken Transfer Invariant 3 becomes an ASSERT failure rather than a write oscillation |
| **R-DY** | 2026-08-03 | The source identity is an EXCLUSIVE ARC of typed FKs with an AT-MOST-ONE check, never an exactly-one check |
| **R-EA** | 2026-08-03 | An anchor correction books in the pay period CONTAINING the day it asserts, DERIVED through the one function both ledgers share -- never read from the source row's stored `pay_period_id` |
| **R-EB** | 2026-08-03 | The anchor half is redesigned from scratch: the cash ledger is SUM-OF-POSTINGS, an assertion is a RECONCILIATION not a reset, the residual posts to Uncategorized Expense / Income, and bank import (X-f6) is the sequenced follow-on rather than an alternative |
| **R-EE** | 2026-08-03 | The settle door stays ONE CLICK, and the true-up form gets its own statement date |
| **R-EH** | 2026-08-03 | `accounts.current_anchor_balance` and `current_anchor_period_id` are DELETED |
| **R-EK** | 2026-08-04 | The resolver's history/projection cut is a PROXY for the day the money moved; replacing it is X-an, not X-f1 |
| **R-EM** | 2026-08-04 | The four "no current period" fallbacks ASK THE SEAM; they do not read a stored balance |
| **R-EN** | 2026-08-04 | The C-17 optimistic lock leaves the cash true-up, because the true-up stops writing the row it locked. **The ruling STANDS and its RATIONALE was corrected (N-190)**: "a second tab overwrites nothing" is true of one table in a transaction that mutates three, and the deleted column had been serialising the reconcile by accident. Replaced by one per-user advisory lock inside the reconcile, both ledgers, all four entry points |
| **R-EO** | 2026-08-04 | `account_anchor_history.pay_period_id` is DELETED. An assertion is (account, day, balance) and nothing else |
| **R-ER** | 2026-08-04 | The rule that a civil day is ASSERTABLE belongs to the module that owns what an assertion IS (`anchor_service`), now that a second writer asks it -- and **"the user has a pay schedule at all" SPLITS OUT of it** as account creation's own precondition, because ruling R-EO falsified the reason it gave and the live one is the opening's posting reconcile (**N-192**) |
| **R-EQ** | 2026-08-04, DESIGNED FROM SCRATCH rather than re-keyed; **horizon SHARPENED at the build** | **An assertion is refused only when it changes nothing.** The duplicate-submit rule is a comparison against the assertion GOVERNING THE DAY THE SUBMISSION ASSERTS, made in the write door under the per-owner lock that door already takes, on BOTH anchor tables; the content-keyed unique indexes are DELETED. Idempotency is a property of a REQUEST, not of the row's contents, so a content key must mis-classify either a retry or a deliberate re-assertion -- and the two errors are not the same size. **The horizon is the rule, not a detail of it**: this ruling first read "the account's CURRENT latest assertion", which has the index's fault mirrored -- a submission for an EARLIER day can never equal the latest, so every back-dated retry appends a permanent row |
| **R-ET** | 2026-08-05 | **Feedback about a WRITE mounts where no refresh region owns it; a caption about STATE stays with its surface.** The two had shared one element, so the transient fact inherited the durable one's per-surface mounting and was destroyed by the durable one's own refresh. Corollary ruled at the same build: **an affordance that cannot succeed is DELETED, not given a nicer refusal** -- a loan's balance cell renders read-only rather than answering a designed error |
| **R-EU** | 2026-08-09 | **The true-up form compares the LEDGER's balance for the day it names against what the user typed, live.** Not the last asserted balance -- that figure is already the box's prefill, and the difference that matters is the reconciliation gap the grid's Period timing / Book vs bank rows diagnose. Rejected: a static book figure with the subtraction left to the reader, and a JS-computed difference (money math in the browser) |
| **R-EV** | 2026-08-09 | **An assertion gets a DURABLE home before its acknowledgement is re-keyed.** The loan page has listed every anchor with its drift since Commit 16 and the cash side lists none, so the fix for "nothing records that this landed" is the missing card, not a longer-lived toast. The 8s autohide STAYS as ruled at X-f1e3; what changes is that the toast stops being the only evidence |
| **R-FA** (recorded as **R-EX** in commit `daa9c402`, which cannot be edited; renamed 2026-08-10 on discovering that X-ad's own ruling had taken that id the same day, from another session. The id namespace is NOT graded -- see **N-217**) | 2026-08-10 | **What settling a row MEANS is a service verb, and the reconcile tick calls the same one the grid's Mark Paid does.** The rule lives today in two route branches -- `mutations.py:552` (envelope-with-entries settles at sum(entries), everything else keeps its stored amount) and `_shadow_mutations.py:209` (a loan-payment shadow freezes the live payment-date amount first) -- and BOTH are load-bearing for the panel: production's `Kayla's Spending Money` carries no entries, so an unconditional `settle_from_entries` books `$0.00` against a `$100.00` estimate, and 2 of the 4 transfer shadows the replay offers are loan payments (`$1,910.95`, `$531.94`) where skipping the freeze restores the creation-time escrow. Rejected: the writer re-stating both branches (this arc's own root cause 1, on a money rule), and the tick calling the mark-done HTTP endpoints (one request per row, no atomicity, and no channel for the statement date) |
| **R-EW** | 2026-08-09 | **The reconcile panel offers EVERYTHING the statement can settle, grouped by the thing it belongs to, and the two acts stay independent.** A purchase nests under its own envelope and ticking it settles that purchase ALONE; closing the envelope is a separate tick in the same block. Rejected: grouping by act-type (which separates a grocery purchase from the grocery envelope), a flat undifferentiated list, and an editable close amount (a second writer of `actual_amount` beside the entries that derive it) |
| **R-EX** | 2026-08-10 | **Registration ASKS rather than inventing**: the most recent payday, the cadence and the horizon, on the sign-up form. The bound is that the paycheck the stated payday opens must still be running, which is what makes sign-up day fall inside period 0 with no second step. The seed script REFUSES to run without a payday rather than defaulting one -- there is no honest default for the day somebody was paid, and defaulting it would put the fabrication back on the one path that provisions the production owner |
| **R-EY** | 2026-08-10 | **The interior hole's repair is pay-calendar `C3`'s DERIVED index, not a relaxed predicate in X-ad, so N-127 MOVES to that arc and `X-x` gains `C3` as a blocker.** R-DE had asked for "an overlap bound that permits a batch which fills a hole"; relaxing that bound alone is unsafe, because `generate_pay_periods` seats new periods at `max_index + 1` and a hole-filling period would take the LAST ordinal while carrying an interior date. `filing_period`'s own docstring names that as its precondition -- the two filing rules part company on **800 of 872 probed days** once `period_index` order stops matching `start_date` order -- and 21 `app/` files read the ordinal as chronological. C3 makes the writer DERIVE both, which fills a hole by construction and deletes the predicate instead of widening it. Rejected: building an interim renumbering pass in X-ad that C3 then deletes, and folding C3 into X-ad (two money-touching changes and a schedule rewrite in one PR) |
| **R-EZ** | 2026-08-10 | **An AUTOMATIC writer never creates a pay period that has already ended** (N-124), and **X-ad DECOMPOSES** into the door that CREATES a calendar and the door that GROWS it. Rejected for N-124: filling the whole lapse in one pass rather than converging over page loads (still manufactures history the owner never saw), and resuming at the next payday on or after today (writes no false history but opens exactly the calendar hole this arc exists to make impossible) |
| **R-FB** | 2026-08-10 | **A BILL's tick MAY correct its amount; an ENVELOPE's close still may not.** The panel renders a bill's figure as a prefilled input: ticking it untouched settles at the stored figure, so the one-click habit **R-EE** protects is unchanged, and a different figure is written through the optional actual R-FA's verb ALREADY carries -- a second DOOR onto one rule, not a second rule. **R-EW's reason for refusing an editable close does not transfer**: an envelope's `actual_amount` is DERIVED from its entries, so a box there would be a second WRITER of a derived value, and a bill has no entries. Measured read-only on production 2026-08-10: **11 of 93 settled bills carry a hand-typed correction, `$252.79` gross** (Electricity `$300.00` -> `$245.32`, T-Mobile Fiber `$70.00` -> `$50.00`, Geico `$178.32` -> `$165.22`), and **4 of the 30 bills the panel would have offered** over Checking's 53 assertion days, `$40.16`. Rejected: settling at the estimate and leaving the difference to X-f3's residual, which books KNOWN money as Uncategorized Income and overstates the real category by the same amount -- feeding the very bucket X-f3 and X-f5 exist to shrink, at the one moment the user is holding the statement that says the true figure; and linking each row out to the full-edit popover, which is a second request and loses the reader's place mid-statement |
| **R-FC** | 2026-08-10 | **The offer set is ONE collection of blocks; a block with no children RENDERS as one row.** The developer chose the flat-bills layout on sight, and measuring why turned the fork into a non-fork: the whole readability difference was RENDERING, so three presentational rules -- a childless block prints inline instead of as a heading above a one-item list, the sort key gains a kind term, and a section label is emitted when the kind changes -- reproduce the preferred panel byte-for-byte over one collection (checked by normalising the two mockups' markup, not by eye). So X-f2-c2 and X-f2-c3 each ADD an arm and the assembler's ordering, heading, count and empty rules are never rewritten inside a money commit (**R-EY**). Rejected: two collections held side by side, which buys an identical screen for two ordering rules, two heading rules and a two-collection empty test. **One argument for this ruling was WITHDRAWN before it was made**: that a shared section label breaks once an offer set spans two pay periods -- true in principle, but no assertion day in production has ever had one, and across 93 settled bills the worst overdue is 12 days against a 14-day period with ZERO past a whole period |
| **R-FD** | 2026-08-11 | **The panel offers INCOME rows too: a deposit you are still waiting on is exactly what a statement settles.** The settle verb already books Received for income (`transaction_service.settled_status_id`), so the cost is the panel's VOICE -- "tick everything your statement shows" rather than "has your bank taken these" -- and a summary that counts deposits and payments separately. Measured read-only on production 2026-08-11, replaying all 53 Checking assertion days: **3 income rows would have been offered, `$2,304.27`**, the largest an FSA reimbursement of `$1,958.87`. The paycheck has never been in the offer set: it is marked Received the day it lands, so no assertion has caught it still Projected. Rejected: expenses only, which keeps one voice and leaves `$2,304.27` of real reconcilable money tickable only from the grid |
| **R-FE** | 2026-08-11 | **A settle books the FRESHEST derivation of the row's own amount, and the rule lives in the VERB so every door books the same figure.** Absent a caller-supplied actual: `sum(entries)` for an envelope carrying entries (unchanged), the projection's own live derivation where one exists, the stored `effective_amount` otherwise. This is its own step **X-aq** and it MOVES MONEY at the grid's Mark Paid, so it runs BEFORE X-f2-c2's money commit -- otherwise the new panel ships displaying a figure the grid contradicts. **The ROOT cause it does not fix is a denormalization**: `transactions.estimated_amount` is a CACHE of a derivation, `income_service.live_projected_net` repairs it at READ time and writes nothing back, so readers and writers hold two answers with no reconciler. Dissolving that -- one stored amount kept true by a reconciler on input change and at deploy, deleting `live_amount_overrides`, `ProjectedBasis.amount_overrides` and the whole override thread through `sum_projected` -- is step **X-ar** and closes **N-40** and **N-224**. Rejected: deciding the question inside the reconcile panel, which makes one row book two figures depending on which control was used -- the exact defect this arc exists to remove |
| **R-FF** | 2026-08-11 | **A tick's amount is CORRECTABLE exactly when the settle verb takes its MANUAL branch**, i.e. when the row is not (envelope-tracked AND carrying entries). One predicate, read off the verb's own branch, rather than a second classification kept in sync by hand. It makes **R-FB** true by construction -- an envelope with entries is the derived case R-EW protects -- and answers the case neither ruling names: production's `Kayla's Spending Money` is envelope-tracked, budgeted `$100.00` and carries ZERO entries, so the verb already treats it as manual and there is no derived value to protect. Rejected: correctable iff not envelope-tracked, which books that `$100.00` blind and sends the only correction path back to the full-edit popover R-FB rejected |
| **R-FG** | 2026-08-11 | **A row is offerable when EVERYTHING it would book had moved by the statement day, and that is the whole bound.** Ruling N-227: the attribution bound STAYS. The bound was challenged because an envelope has no meaningful landing day of its own, so its close is offerable for the whole period it is being spent in; the proposed remedy `period_end <= observed_on` is **REFUTED by the owner's own behaviour** -- measured on production, they close envelopes DURING the period **21 of 33 times (64%)**, as early as 9 days before period end, mean 2.7 days before, so it would have refused two thirds of the closes they really made and empties today's panel. Two of N-227's own claims were also WRONG and are withdrawn: its stated mechanism ("an envelope carries no `due_date` so `attribution_date` falls back to the period start") describes **4 of 231** envelope rows, while **185** carry an explicit `due_date` EQUAL to the period start -- a value the recurrence engine writes, not a fallback; and its hazard ("closing early makes every later purchase on that row inert") is FALSE for the `Paid` status the close actually writes -- `entry_service._update_actual_if_paid` recomputes `actual_amount` and `_resync_postings_if_settled` re-posts, which an existing green test and 24 of 24 production rows both confirm. What the challenge correctly identified is a DIFFERENT defect, and it is fixed structurally rather than by a calendar bound: an envelope's value is an AGGREGATE, so it could book a purchase the statement never saw. `_wholly_spent_by` is that fix. Rejected: narrowing the post-true-up modal to statement-observable rows, measured to move the prompt from 46 of 48 assertion days to 42 -- a UI palliative for a bound that is now correct | 
| **R-FH** | 2026-08-11 | **A settle's refresh of a derived amount writes the CACHE (`estimated_amount`); `actual_amount` receives a HUMAN's figure and nothing else.** Amends **R-FE**, whose first implementation wrote the live derivation to `actual_amount`. That column's NULL-ness is read as "a human entered this fact" by three subsystems -- `income_service` (a settled income row's actual is never a recomputable projection), `spending_analysis` (only an explicitly entered actual is a surprise, so a machine write manufactures one) and the grid cell (which strikes through the estimate whenever the two differ, rendering a figure the owner never saw). The write was also PERMANENT and unlabelled: the row leaves `live_projected_net`'s Projected-only candidate set at that same flip, so the stale estimate could never be repaired afterwards and **X-ar's reconciler could not have told a machine write from a real correction**. Writing the cache instead makes X-aq X-ar's FIRST INCREMENT rather than a compensating layer -- the same column, one trigger instead of several -- and leaves the booked figure, `effective_amount` and R-DH (c)'s invariant unchanged. `is_override` is deliberately not set; the recurrence engine's `resolve_conflicts` rewrites `estimated_amount` with it False for the same reason. Rejected: keeping the single column and teaching the three readers to tolerate it, which spreads one denormalization into three | 
| **R-FI** | 2026-08-11 (amended the same day by the two adversarial reviews it was written against) | **A row's amount is either its OWN -- a human authored the figure, or the money moved -- or it is DERIVED, and a DERIVED AMOUNT IS NOT STORED.** Two rules, the second following the first: every financial quantity that VARIES OVER TIME is a dated series resolved as-of; every quantity DERIVED from those is not stored at all. **The discriminator is an EXPLICIT `amount_source`, and the first draft's link-derived one was REFUTED by tracing**: the rules are not a partition over `template_id` / `transfer_id`, because salary is a SUBSET of template (`income_service.py:217` keys on `SalaryProfile.template_id`), loan payment is a SUBSET of transfer, and a CC payback row carries NEITHER link (`credit_workflow.py:529-539` builds it with `template_id=None` and no transfer) while its amount is derived -- so the proposed `CHECK (estimated_amount IS NOT NULL OR template_id IS NOT NULL OR transfer_id IS NOT NULL)` would have made storing THAT derived value structurally mandatory, which is the ruling inverted. A declared source cannot go stale against the links because the links never decided the rule. The CHECK becomes `(amount_source = OWN) = (estimated_amount IS NOT NULL)`, so a derived row is structurally incapable of holding a figure that can go stale and a reader that skips the resolver gets `None` rather than a plausible wrong number. The kinds this arc moves are salary, template, transfer, loan payment and the card payment `credit_card:CC4b` currently plans as another override-map entry; **they are not the whole census** -- an adversarial review counted about twelve stored-derived monetary values, which is **N-243**, and the arc SEQUENCES the rest rather than claiming to have found them all. **This SUPERSEDES R-FE's remedy and step X-ar**, whose "one stored amount kept true by a reconciler" preserves the denormalization, rests forever on trigger completeness, and moves salary in the OPPOSITE direction from the other four; **X-aq (`9cabc206`) survives as the FREEZE** -- the moment a derived amount becomes a fact. Rejected: reconciler-plus-detector, which needs a detector precisely because staleness stays representable; and deriving a template row from `template.default_amount` directly, a scalar with no time dimension, so a June price change retroactively rewrites March. The evidence is that the codebase already obeys rule 1 for loan rates (`RateHistory`), escrow (`EscrowComponentVersion`), tax years (`ffb9514c`) and raises (`apply_raises`), obeys rule 2 nowhere, and that the template edit form's "Regenerate effective from" field is already an effective date used once to bound a destructive sweep and never stored |
| **R-FJ** | 2026-08-12 (developer) | **An envelope with NO purchases settles at its BUDGET through a door and at `$0.00` through carry-forward, and the discriminator is the CALLER's ACT, not the row.** Finding **N-230**: `transaction_service.settle_transaction` gates its entries branch on `tracks_purchases AND entries`, so an entry-less envelope takes the MANUAL branch and books `effective_amount`; `settle_from_entries`, which `carry_forward_service` calls directly, books `Decimal("0")` for the identical shape. Both are right, because the two callers have done different things to the money. **Carry-forward has already RELOCATED it** -- it rolls `estimated - sum(entries)` into the next period's row and then settles the source at what was spent, so booking the estimate there would count the same dollars twice. **A DOOR has relocated nothing**: pressing Paid says the budget is finished, and booking `$0.00` would record that no money left the account while marking the row Paid. Production carries BOTH signatures, which is how the split was found: of 9 settled entry-less envelopes, **8 were booked at their estimate (`$794.79`) through a door and 1 -- `Kayla's Spending Money`, `$157.60` -- at `$0.00` through carry-forward**. What was not defensible, and is what this ruling fixes, is that the difference was undocumented and fell out of which function a caller happened to reach for; both functions now state it. **AMENDED 2026-08-12, the same day, by the adversarial review it was written against: the rule holds at the SETTLE MOMENT and nothing holds it afterwards.** An entry-less envelope settled either way is left `Paid`, not archived, so a purchase logged later re-derives `actual_amount` from the entries and re-posts -- reversing the door's "the budget is finished" reading, and spending money the carry-forward branch has already relocated. The ruling stands for the act it governs; reconciling the rollover it divided is **N-249** / step **X-ax**. Rejected: an unconditional `sum(entries)` for every caller, which books `$0.00` against `Kayla's Spending Money`'s `$100.00` budget and hands already-spent money back to the projection (the reading **R-FA** already refused on that same row); an unconditional estimate, which makes every rollover count its envelope twice; and refusing an entry-less envelope settle outright, which breaks the Paid button for the **190** entry-less envelope rows sitting Projected on the grid today |
| **R-ES** | 2026-08-05 | **`account_anchor_history.notes` is DELETED and the origination assertion goes through the SAME write door as every later one**, so the table has ONE writer. An assertion is (account, day, balance) and nothing else -- the sentence ruling R-EO already wrote into the model. Measured: no code in `app/` READS the column (AST census), 76 of 78 production rows are NULL, and it labels the origination on 2 of 9 accounts. It is a SECOND answer to a question the app already decides positionally (`CashAnchorFact.is_opening` -> `account_opening` / `account_trueup`), and the forensic trail it was nominally for is `system.audit_log`, which records every INSERT with the full row and the acting user. The loan twin's typed `source_id` STAYS: that one is read |

## 5. The steps

### 5.0 Why each block sits where it does

**The ORDER is `../../plans/steps.md` and only there.** This section is the arc's RATIONALE: the
fact that forces each block's position, in this arc's own terms. **The block numbers are NAMES, not
ranks** -- four documents cite them -- and this section states no sequence, no member steps and no
readiness. Every copy of those ever kept here drifted: it listed a shipped step as pending, and it
carried an ordering claim three other documents called unsatisfiable. **The phase headings below
group the same work by SUBJECT; this groups it by CAUSE.**

| # | block | the fact that forces its position |
|---|---|---|
| 1 | **the anchor half** | The only remaining work that moves a figure the developer reads: **N-171**'s `$15,065.08` gross / `-$1,495.10` net stays invisible on the income statement until the cutover, which is its OWN PR and MOVES MONEY |
| 2 | **the pay-calendar door** | Its gate CLEARED 2026-08-05 when the X-f1 cluster reached production (`8d812662`): `accounts.current_anchor_period_id` is GONE rather than going, which is the fact X-ad's trace turned on. The "ONE PR" pairing with X-x ENDED at **R-EY** -- X-x is held behind the writer (**R-DE**) and behind the pay-calendar arc's `C3`, which owns the repair its refusals point at |
| 3 | **the posting restructure** | `X-ai-s` is held until the cutover, which deletes the correction family it would buy attribution for; `X-d` is PARKED on **N-155**, whose fix is X-ai's own placement; `X-ak` carries **N-193**, a reproducible unhandled 500 on a money route |
| 4 | **the credit-card arc** (own document) | `CC1b`'s fold is specified against the reset semantics **R-EB** deletes at the cutover, and `CC3b` derives a settle from `paid_at`, which X-f1b deleted |
| 5 | **the bank import** (own document) | Consumes the outstanding set and the residual path (**R-EB**). It follows block 4 so ONE matching rule covers checking and card rows rather than being widened into them later |
| 6 | **the read-path residue** | Nothing blocks on it and its footprint is disjoint from the write path: tag `xd-attempt-1-parked-n155`'s 30 `app/` files against tag `xx-attempt-1-held-rde`'s 26, **zero overlap**, measured. UNGATED, which is what lets `X-l` run early -- and it must, because it is block 10's `C2` and recurrence `R-F12` as well |
| 7 | **the gate and vocabulary residue** | Shares files with nothing; interleaves anywhere |
| 8 | **E2 and G** | Runs LAST by ruling. `G2` must not begin before the boundary it rests on is proven |
| 9 | **the recurrence redesign** (own document) -- **NOT part of this arc** | Overlaps every live block of this arc by ONE file (`_recurrence_common.py`, measured against the `xd-attempt-1-parked-n155` tag), so it pauses nothing here. Its own document holds its argument; how its steps interleave with this arc's is `steps.md`'s answer |
| 10 | **the pay calendar** (own document) -- **NOT part of this arc** | Opened 2026-08-08 out of the recurrence arc's F-10: `budget.pay_periods` stores the payday and DERIVES `end_date` / `period_index`, so a gap, an overlap and an index out of date order all stop being expressible. **Its `C2` IS this arc's `X-l`**, and also recurrence `R-F12` -- one commit under three names, and whoever builds it must satisfy all three specifications, including this arc's N-82 / N-128 / N-79-far |
| 11 | **the amount model** (ruling **R-FI**) | It is the ROOT CAUSE of block 1's last open finding and of three mechanisms nothing else will delete, so it precedes every step that reads a projected figure. Its first two leaves touch no file block 1 does, which is why they run beside the anchor half rather than after it |

**Blocks 9 and 10 are SEPARATE ARCS and blocks 4 and 5 are FEATURES consuming this arc's output.**
None is a correctness fix inside this arc, and **none owns a `ledger.md` row whose `arc` reads
`balance`, nor ever will**. Their documents are `implementation_plan_recurrence_redesign.md`,
`implementation_plan_pay_calendar.md`, `implementation_plan_credit_card.md` and
`implementation_plan_bank_import.md` (not yet written), all under `docs/plans/`. **The card plan's
2026-07-19 sequencing was DISCHARGED and is now archived to
`../../plans/historical/credit_card_sequencing_2026-07-19.md`**: every balance-arc step it named has
shipped (old `X4` survives here as X-e). What gates the card arc is R-EB, and block 4 is the reason.

**Step ids are append-only, and a DECOMPOSED parent ticks with the last of its leaves** --
`../../plans/conventions.md` rules 2 and 10, stated once there and graded there.

**Shipped steps are one line each in `archive/phase_x_as_built_2026-08-04.md`** (Section 1 through
X-f1b, Section 1a for the X-f1 cluster) and are not repeated here. X-ae, X-af and X-aj1 left this
section on 2026-08-11 as copies of rows the archive already carried; the one live fact among them,
X-aj1 leaving `transfer_service.py` at 987 of 1000, is **N-152**'s own row.

### Phase X -- the anchor half (ruling R-EB; runs FIRST)

- [ ] **X-f** `feat(transactions): the app records when money moved` -- the DECOMPOSED parent,
  carrying **N-42**. Its original scope (shrink the reconciliation row) was **redesigned from
  scratch 2026-08-03 on ruling R-EB** and now ships as X-f1..X-f5, with X-f6 as the ruled follow-on.
  It ticks with the last of them.

* [x] **X-f1** `8d812662` (PR #83) a settle carries the day the money moved; absorbs **S2-b**.
  Fourteen leaves, CONDENSED into `archive/…2026-08-04.md` 1a. Did NOT close **N-173** (**X-f6**'s).
* [x] **X-an** `3d3f0ef5` / `549015c0` a payment is history from the day its money moved (**R-EK**),
  two leaves, CONDENSED into `archive/…2026-08-04.md` 1b. Closed **N-187**, **N-196**; opened
  **N-207**..**N-211**.
* [ ] **X-f2** `feat(accounts): the true-up is a reconciliation` -- R-DH (f)'s second half, the
  DECOMPOSED parent (**R-EU** / **R-EV** / **R-EW**, 2026-08-09). One session's work is one leaf; it
  ticks with the last of them. **The developer's existing workflow already IS this loop** -- read the
  bank, type the balance, tick what cleared -- and only the RECORDING changes. **X-f2-a**
  (`397ce36e`) and **X-f2-b** (`a41b5ebf`) are in `archive/…2026-08-04.md` 1c; X-f2-c's three
  leaves remain.
* [x] **X-aq** `9cabc206` a settle books the freshest derivation of the row's amount, in the VERB,
  so all its doors agree (**R-FE**). **`c4932746` then made it write `estimated_amount` and not
  `actual_amount` (ruling R-FH)**, which is what makes this step the FREEZE **X-au-c** formalises
  rather than a compensating layer -- the amount model keeps that column, not undoes it. Figure-neutral on
  production (0 rows, `$0.00`); **51 rows / `$4,897.50`** on a clone after one un-regenerated 3%
  raise. Also fixed at `41f678e5`. Opened **N-224**.
* [x] **X-as** `ffb9514c` a tax year resolves to the latest CONFIGURED year's rules at or before it,
  PER CONFIG KIND, never to the clock's year -- which on 2027-01-01 resolved to NOTHING and dropped
  **$8,460.50** of withholding out of the projection (`$10,914.93` with that day's top-up), because
  the engine reads a missing `fica_config` as zero Social Security. **The amount model's PRECONDITION** (**X-au-b**): a
  resolver may not read the wall clock, and "what year is it" was the last clock read in the salary
  derivation. Opened **N-235**, **N-236**.
* [ ] **X-f2-c** the OUTSTANDING SET, widened (**R-EW**) -- the panel offers everything the statement
  can settle, and ticking stamps the STATEMENT date. This is **N-172**'s churn on the transaction
  side. Measured on production over all 53 assertion DAYS (57 assertion ROWS -- the two figures are
  both right about different things and one word was doing both jobs): **34 days would have had
  something to tick, 100 rows worth $23,910.04, OF WHICH 8 rows worth $5,442.89 are transfer shadows** -- so the
  non-transfer half is **92 rows / $18,467.15**, and the replay's row set is "carries no purchase
  entries", i.e. bills AND shadows -- the OF WHICH is load-bearing: reading it as "plus" counted the
  shadows twice at $29,352.93 until it was re-derived from `shekel-prod-db` on 2026-08-10.
  The **DECOMPOSED parent** of three leaves (developer, 2026-08-10), one session and one commit
  each, with the two that move money separated from the one that does not.
  * [x] **X-f2-c1** `24701c1d` the module home and the grouped shape. Closed **N-216**; opened
    **N-217**, **N-218**.
  * [x] **X-f2-c2** `d23b55fd` the TRANSACTION twin (**R-FA** / **R-FB** / **R-FC** / **R-FD** /
    **R-FF**): the envelope's close and bills, income included, settled through the grid's own verb
    on the STATEMENT day. Prepared by `9f255bb4` / `f4512a2f` / `3e2c346e`, residue `41f678e5`.
    **`9325fe6a` is the rule a LATER arm must keep**: a row is offerable only when everything it
    would book moved by the statement day, so an envelope holding a later purchase is not offered.
    Closed **N-222** / **N-223** / **N-227**; re-pointed **N-221**; opened **N-225**..**N-233**.
  * [ ] **X-f2-c3** the TRANSFER shadows (**R-FA**), in their own group, settled through
    `transfer_service.update_transfer` so both legs and the parent move together (Invariant 3) --
    which means a tick on THIS account's panel also settles the leg on the other account, and the
    copy must say so. Carries the loan-payment freeze.
* [x] **X-ap** `616cf157` THE THIRD SETTLE DOOR, which R-FA's own text missed by naming two.
  Measured before and after on production: row 2231 *Gas*, an `$80.00` budget carrying one `$48.98`
  purchase, booked **`$80.00`** through the dropdown and `$48.98` through Mark Paid -- **`$31.02` of
  spend that never happened**. Three commits, the first two zero-money: `8726beca` (a ROUTE stops
  deciding what a status change means), `32db32af` (**N-233**, **N-229**), `616cf157` (the settle,
  the type rule, ruling **R-FF** on the Actual box). Closed **N-219**, **N-230** (**R-FJ**); opened **N-244**-**N-247**.

* [ ] **X-ax** `fix(carry-forward): a rollover is reconciled against the source it divided` --
  closes **N-249**. **MOVES MONEY.** Carry-forward splits one budget across two periods: it settles
  the source at `sum(entries)` and rolls `estimated - sum(entries)` into the next period's row. The
  split is irreversible and the source is left **Paid, not archived**, so `entry_service` still
  accepts purchases against it and `_resync_settled_envelope` re-derives its actual -- at which point
  the two halves no longer sum to the budget they divided. Live shape: row 2391 settled `$0.00` on
  2026-06-03 with `$157.60` folded into row 2392 (`$257.60 = $100 + $157.60`); one `$30.00` purchase
  against 2391 leaves the projection `$30.00` richer than reality. The other direction covers **8
  rows / `$794.79`** settled at their estimate, any of which a single late `$5` purchase drops to
  `$5`. **The developer ruled the remedy 2026-08-12**: reconcile, do not refuse -- when a settled
  source's spend changes, the target's top-up moves by the same amount. **Its first act is a trace,
  not code**: the top-up is not identified on the target row today (it is folded into
  `estimated_amount` with `is_override = True`), so the step must first rule how a rollover is
  RECORDED before it can be reconciled -- and that recording is a derived quantity, which is
  **R-FI**'s subject, so the design belongs beside the amount model rather than beneath it.
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
  **N-161** and **N-170** with it by deleting the family they are properties of. **N-169 is
  already CLOSED** -- the pay-calendar arc's `C2-d` (`3e6cd4ec`) deleted the loan-package chronology
  primitive this package imported, so the half that was waiting here no longer exists.
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

### Phase X -- the amount model (ruling R-FI)

**What this phase is about, in one sentence: the app stores five amounts it also computes, and each
one grew its own private repair mechanism.** Salary income is repaired at READ time by
`income_service.live_projected_net`, which writes nothing back (**N-224**); a loan-payment shadow by
`loan_payment_service.live_loan_transfer_amounts`, which also reads the wall clock (**N-40**); a
transfer shadow by a two-line copy in `transfer_service.update_transfer` beside a drift corrector
that logs `"Correcting shadow %d estimated_amount drift"`; an ordinary template row by
`regenerate_for_template`, which DELETES and recreates every non-override row from a date the form
supplies and never stores. `credit_card:CC4b` plans a fifth. **R-FI deletes the class**: a derived
amount is not stored, one total resolver answers, and the CHECK makes a stale derived figure
unrepresentable rather than merely unlikely.

**Measured on production 2026-08-11**, so the size of each cutover is known rather than estimated:
**997 rows, and the buckets RECONCILE to it** because a set of measurements that does not is a
census nobody can check: 51 projected non-overridden salary rows (`$139,642.27`) + 511 non-override
template rows + 342 transfer shadows + 49 ad-hoc rows + 37 override rows + 7 already-settled salary
rows = 997. **Two of those cells were re-measured at X-au-b and are stated more honestly here.** The
shadow and override cells depend on which bucket claims the 12 rows that are BOTH: counting them as
overrides gives 330 and 49 instead of 342 and 37, and both readings reconcile to 997 -- which is why
the resolver's dispatch ORDER is a rule rather than a convenience. And the 49 ad-hoc rows do NOT all
"own their amount and always will", as the first draft of this sentence said: **21 of them are CC
paybacks**, whose figure is derived from the purchases they repay and stored with no reconciler.
That is the kind carrying NEITHER link, which is why R-FI's discriminator is declared rather than
read off the columns, and it is now step **X-au-i**. `template_id` and `transfer_id` are mutually
exclusive across every row -- by CONVENTION, with no constraint enforcing it -- but the five rules
are NOT a partition over those two columns (ruling R-FI). Stored-vs-derived drift TODAY is **0 rows /
`$0.00`**, re-measured 2026-08-12 by X-au-b's exhaustive oracle -- which
`../../plans/conventions.md` rule 8 says is not resolved, and which the read-time repair is what
hides.

* [ ] **X-au** `feat(cash): a row's amount is either its own or derived` -- the DECOMPOSED parent
  (**R-FI**), carrying **N-40**, **N-224**, **N-228**, **N-238**. It ticks with the last of its
  leaves. **It SUPERSEDES X-ar**, whose two stated premises tracing also refuted: its deletion set was
  unreachable (`live_amount_overrides` merges the salary and loan halves at
  `cash_ledger/_amounts.py:684`, and the loan half feeds BOTH shadow legs, so the loan half being out
  of scope left nothing deletable), and it claimed **N-40**, which `../../plans/ledger.md` owned to
  `X-i2`.
* [x] **X-au-a** `81138fb8` a recurring definition's amount is an effective-dated series: the model,
  the ONE write door, the read-and-correct panel, and the backfill mining what the rows record.
  **44 eligible templates, 47 versions, 625 minable rows reproduced with 0 mismatches** on a
  production clone. **What a LATER leaf must obey**: the mining predicate is best-available, NOT
  airtight, and the series answers by a row's own DUE date while the regeneration sweep still selects
  by pay PERIOD. Opened **N-244**..**N-247**.
* [x] **X-au-b** `81ad02d1` the TOTAL dispatch over the five amount rules, in
  `cash_ledger/_amount_source.py` -- a module INSIDE that package, not more of `_amounts.py`
  (developer, 2026-08-12). **997 of 997 rows on a 2026-08-12 production clone agree with the answer
  the app publishes, 0 refusals, `$0.00` drift.** **What a LATER leaf must obey**: a derived row's
  answer must be INVARIANT under a change to its own amount column -- agreement alone cannot see this
  resolver at all. Opened **N-252**, **N-253**, **N-254**.
* [ ] **X-au-i** `refactor(cards): a payback is worth the purchases it repays` -- the CC PAYBACK
  cutover, closing **N-243** and **N-252**. The kind ruling R-FI names that carries NEITHER link, so
  no link-derived discriminator can reach it and X-au-b's dispatch places it as OWN: 21 rows on the
  production clone, 20 of them settled. Two writers state the same figure and neither reconciles it
  -- `credit_workflow.create_cc_payback_transaction` copies the source row's amount at the moment it
  is marked Credit and repairs it never, and `entry_credit_workflow.sync_entry_payback` re-states it
  as the sum of the source's credit entries on every entry mutation, unconditionally. **It must RULE
  what a hand edit means first**: `routes/transactions/mutations.py:252` sets `is_override` only for
  a template-linked row, so on a payback that flag is unreachable and the next sync silently reverts
  a typed figure -- production payback 2590 is one, hand-edited to `$123.18` on 2026-06-02 against
  credit entries summing to `$181.58`, and settled at the edited figure.
* [ ] **X-au-c** `refactor(transactions): the column says who owns the amount` -- the migration.
  `transactions.estimated_amount` and `transfers.amount` become NULLABLE under
  `ck_transactions_amount_ownership`, every reader of a projected amount routes through X-au-b's
  resolver, and X-aq's settle refresh becomes the FREEZE: at settle the resolved figure is written and
  the row owns it from then on. No row is NULLed in this step, so no figure moves -- but it is NOT
  "byte-identical by construction", and the first draft's claim that it was is withdrawn.
  **`Transaction.effective_amount` and `Transfer.effective_amount` are MODEL PROPERTIES**
  (`models/transaction.py:348`, `models/transfer.py:198`) with no DB access, so neither can resolve a
  derived amount at all; this step must rule what they answer for a row that owns no amount, and 17
  modules read one of them. **The reader census is this step's first act**, and the four the first
  draft named are not the set: `carry_forward_service/_execute.py:336` and `:350` do arithmetic
  directly on the column, `routes/entries.py:141` and `entry_service.py:736` compute an envelope's
  remaining budget from it, `investment_projection.py:560` sums `effective_amount` over PROJECTED
  contribution shadows, and `posting_service._settle_effective:242` reads it in SQL with no status
  predicate (**N-242**).
* [ ] **X-au-d** `refactor(salary): a projected paycheck is not stored` -- the SALARY cutover. The
  recurrence engine stops pricing salary rows, the 51 live rows go NULL, and
  `income_service.live_projected_net`, `transaction_service._freshest_amount` and
  `_reconcile_cached_amount` are deleted. Closes **N-224** and **N-228** (the batch producer called
  with a one-element list, which disappears with the mechanism rather than by threading a map).
* [ ] **X-au-e** `refactor(recurrence): a template row reads its template's series` -- the TEMPLATE
  cutover, onto X-au-a's series. Generation stops pricing, 511 rows go NULL, and regeneration's amount
  arm plus the conflict chooser's keep-vs-use decision are deleted: a hand-edited month owns its
  figure, so the collision the chooser mediates cannot occur (developer, 2026-08-11). **This is where
  the measured `$502.45` defect class dies** -- `_get_transaction_amount` priced a third paycheck from
  a truncated period list, and a generator that prices nothing cannot mis-price.
* [ ] **X-au-g** `refactor(loans): a loan payment resolves on its own due date` -- the LOAN-PAYMENT
  cutover, closing **N-40**. **It must RULE first**, and the ruling is forced rather than optional: a
  resolver may not read the wall clock, and `live_loan_transfer_amounts` resolves its basis through
  `_resolve_loan_basis(..., date.today())`. The rule ruling D5 already applied to escrow -- a shadow's
  figure resolves on the shadow's own DUE date -- is what makes the loan half resolvable. Dormant on
  production (`budget.loan_payment_settings` is EMPTY, both payments predating migration
  `c2a2c508e103`, which deliberately did not backfill), so it moves `$0.00` there and is graded on a
  seeded loan.
* [ ] **X-au-f** `refactor(transfers): a shadow's amount is its parent's` -- the TRANSFER cutover.
  `transfers.amount` resolves from the template series for a generated transfer, a shadow resolves
  from its parent, and the copy at `transfer_service.py:534` with the drift corrector at `:814` both
  go. **Transfer Invariant 3 becomes STRUCTURAL rather than maintained.** `uq_transfers_adhoc_dedupe`
  is unaffected: its predicate is `transfer_template_id IS NULL`, and an ad-hoc transfer owns its
  amount. **It runs AFTER the loan leaf and the first draft had them the other way round**, which an
  adversarial review reproduced: a loan-payment shadow IS a transfer shadow, so this step NULLs it,
  while `loan_payment_service._manual_shadow_amount:660` reads `shadow.estimated_amount` under a
  docstring asserting that column is "NOT NULL, always the generated base" -- manual-mode loan
  payments would be broken for the whole interval between the two leaves.
* [ ] **X-au-h** `refactor(transactions): is_override says one thing` -- closes **N-238**. The flag
  carries FOUR facts, not the two the first draft named, and an adversarial review found the other
  two. (1) the user RE-PRICED the row and (2) the user MOVED it to another period -- both written at
  `routes/transactions/mutations.py:295`; (3) this row SURVIVES the regeneration sweep
  (`_recurrence_common.py:375`, and `pay_period_admin.py:821` escalates it to "deleting this period
  loses data"); and (4) this row is EXEMPT from the partial unique index, whose predicate is literally
  `is_override = FALSE` (`models/transaction.py:199-203`, `models/transfer.py:64`) --
  `carry_forward_service/_execute.py:352` sets the flag purely to stay index-safe. **So the naive
  absorption is unsafe and measured so**: a moved-but-not-repriced row would carry a NULL amount, fall
  back INSIDE `idx_transactions_template_period_scenario`, and collide with the target period's
  canonical generated row. Meanings 3 and 4 need representation before the flag can go.
* [ ] **X-av** `fix(salary): the base annual salary is a dated series` -- closes **N-237**. Rule 1 of
  R-FI applied to the last scalar input: `apply_raises(base_salary, raises, as_of)` resolves RAISES
  as-of, but `profile.annual_salary` is the base for all time, so the app cannot tell "I got a raise"
  from "I typed the wrong salary" and a correction reprices only from today forward -- the same
  half-effective-dating the template amount had.
* [ ] **X-aw** `fix(salary): a period's gross does not depend on the horizon` -- closes **N-239**.
  `paycheck_calculator._gross_biweekly_for_period:543` distributes the annual rounding residue when
  the row's calendar year holds `pay_periods_per_year` periods IN THE LIST IT WAS HANDED and falls
  back to `ROUND_HALF_UP` otherwise, so the answer depends on how far the schedule has been
  generated. Measured: filling 2028 from 14 periods to 26 moves **6 rows by `-$0.06`**; shrinking a
  full 2027 to 25 moves none. Recurrence **D10**'s class, and the fix is to count a year's paydays
  from the stored cadence rather than from whichever rows exist.

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
* [ ] **X-ad** `feat(periods): the pay calendar a new user can actually enter` -- the DECOMPOSED
  parent, split 2026-08-10 (**R-EZ**). Two write doors, two findings, two commits: the one that
  CREATES a calendar and the one that GROWS it share no function and no test. **It RUNS BEFORE
  X-x** -- R-DD sequenced it after, ruling **R-DE** reversed that, and the later ruling governs --
  but **the pair no longer ships as ONE PR**: **R-EY** moved **N-127** to pay-calendar `C3`, so the
  repair X-x's refusals point at is that step's and X-x waits on both.
  *Its opening line claimed this step "re-anchors accounts". That was expired when written: ruling
  R-EH deleted `accounts.current_anchor_period_id`, so there is nothing left to re-anchor.*
  * [x] **X-ad-a** `2a4eb477` -- registration ASKS for the payday, the cadence and the horizon;
    the bootstrap payday is DELETED. Closed **N-123** (= pay-calendar `P3`) and satisfies `C4`'s
    `P8`. **Shipped to `dev`, NOT to production** -- it is not an ancestor of `origin/main`.
  * [ ] **X-ad-b** the rolling top-up stops writing HISTORY (**N-124**, ruling **R-EZ**). With
    rolling enabled, a lapsed schedule's page load appends periods from `last.end_date + 1`
    wherever that is and populates them -- **61 -> 113 -> 132 periods and +991 transactions** over
    two `/grid` loads on a clone whose schedule ended 1,000 days ago, 19 of the new periods
    entirely historical. It self-heals, which is why nothing ever reported it, and what it heals
    with is fabricated history. **An automatic writer may not create a period that has already
    ended**: the top-up creates nothing on a lapsed schedule and the lapse is surfaced instead,
    because the owner's pay may well have changed while they were away and the app must not guess
    that it did not.
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
  **N-14**, **N-56**, **N-72**'s second half, **N-89**, **N-91**, **N-92**, **N-93**,
  **N-115**. **N-40 LEFT this set on 2026-08-11** (ruling R-FI): the clock read is a property of a
  DERIVED amount being stored, so it closes at **X-au-g** and no gate reads a prose closes-set. **Nine rows, one root cause:** `BalanceContext` pins the pass's `as_of` and `scenario`
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
  asks for by DERIVING the calendar from the paydays, and `C2` adds one requirement this row did not
  have -- **row P14: the calendar may not be constructed from a partial window** (the sibling shape
  is measured in-repo at `$150,000.00`, `loan_ledger/_visible.py:78-95`).
  **`C2` was RULED on three forks 2026-08-10 and DECOMPOSED into `C2-a`..`C2-f`, so this box ticks
  with its LAST leaf rather than with one commit.** The census that sized it corrected a claim
  BOTH other documents carried: **SIX** implementations of "which paycheck covers this day", not
  three, across **66** `app/` call sites. The specifications are the pay-calendar document's
  section 4; nothing about them is restated here. **Root, and it is this arc's own disease on the other axis: the pay
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

* [ ] **X-at** `feat(salary): a substituted tax year says so, and a new year can be entered` --
  closes **N-235**, **N-236**. X-as makes an unconfigured year resolve to the latest configured
  year's rules, which is the only available answer and a correct one; what it does not do is SAY so.
  `/analytics/taxes?year=YYYY` accepts any year in `[2000, 2100]`, so a 2019 request renders its
  refund hero, W-2 preview and Schedule A against another year's law with nothing on the page saying
  which, and the only record is a DEBUG line. Carry the resolved year out of the resolver and render
  it. Its second half is the door that is missing entirely: nothing in `app/` creates a
  `TaxBracketSet` outside the signup seed, so the settings screen can write a year's state and FICA
  rows and never its brackets -- it cannot finish the year it starts, and a user following next
  year's IRS release has nowhere to put it.
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
* [ ] **X-ao** `feat(plan-gate): a ruling id resolves to one ruling` -- closes **N-217**. The
  registries are graded on findings, on steps and on the graph between them; the arc documents'
  RULINGS tables are not parsed at all, and the corpus carried a LIVE collision -- two sessions
  appended a different **R-EX** on one day, both reached `dev`, and X-f2-c2 / X-f2-c3 cited it
  ambiguously until a reviewer read the table. One arm per arc document on the parser the other
  arms already use, and the rule it grades is `conventions.md` rule 9's unstated half: a ruling is
  the RULE and its date, under an id that names one rule.
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

## 7. Verification standard

**Moved to `../../plans/verification.md`**, one copy for every arc. It was stated here in full and,
in a shorter and not-quite-equal form, in the credit-card plan; the recurrence and pay-calendar arcs
had none at all. This arc wrote it, and this arc is why several of its clauses exist -- three of the
five defects behind the suite's two clock gates were fixture-clock bugs created here, and this arc
writes more anchors, assertion instants and due dates than anything else in the codebase.

## 8. Process lessons

**Moved to `../../plans/lessons.md`**, one copy for every arc. They were stated here and nowhere
else, so the other three arcs did not have them -- which is what this restructure removes
everywhere else in the corpus.

## 9. Rules for this document

**Moved to `../../plans/conventions.md`**, one copy for every arc. They were near-identical in
three documents and absent from the fourth.

`tools/plan_gate/` grades this document against them through a pre-commit hook
scoped to it and the CI step that runs the custom pylint checkers -- so EDITING
THIS FILE is what runs the gate. This document's own caps live in the gate's
constants beside the other arcs'.
