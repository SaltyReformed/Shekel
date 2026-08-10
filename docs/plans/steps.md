# The step index

**Every step in every arc, one line each, grouped by ARC.** This is an INDEX, not a specification: a
step's specification is an argument about one subject and stays in its own arc's document, which the
`arc` column names. The gate checks both directions -- an index row with no specification and a
specification with no index row are both failures.

**The ORDER is `blocked by`, never the row order, and the claim that it was cost a reader real
time.** This paragraph used to open "in execution-relevant order", which is false twice over: the
table sorts by arc, and two rows sit ABOVE their own blockers (`X-x` above `X-ad`, `X-k` above
`R5`). Grouping by arc is how a reader FINDS a step; "what may start now" is a QUERY over the
column. `conventions.md` rule 13 grades it -- referential, acyclic, no shipped step behind an open
one, and one blocker set per identity class. **Two things are deliberately NOT in that column.** A
decomposition is in the ID by rule 2, so `X-f2-a` needs no edge saying it is inside `X-f2` --
recording it would be this registry's own disease. It is graded by its own arm instead: a row that
DECLARES itself "the DECOMPOSED parent" may not ship while a leaf is open.
**The parent set is declared and only the leaf set derived**, because deriving both by id prefix
claims `R-F1` as the parent of `R-F10`, `R-F12` and `R-F13`. Second, an edge is written only where
an arc document STATES it: the graph holds 65 edges over 42 rows, the gaps are real, and a guess
where a citation belongs is worse than a blank.

**`aliases` is what this table exists for.** `C2`, `X-l` and `R-F12` are ONE step under three names,
scheduled as one commit by all three documents. Before this table that identity was prose in three
files with nothing reconciling it, and the `P3` / `N-123` collision went unnoticed from April to
2026-08-09.

**99 steps, 86 open.**

| arc | id | aliases | step | state | commit | blocked by |
|---|---|---|---|---|---|---|
| balance | X-ae | -- | **X-ae** `fix(app): a submitted digit string is parsed, not predicated` -- PR #79, merge | SHIPPED | -- | -- |
| balance | X-af | -- | **X-af** `test(periods): the fixtures build their window on the USER's clock` -- PR #77, | SHIPPED | -- | -- |
| balance | X-aj1 | -- | **X-aj1** the status seam merge, in three commits (`1688f508` R-DR's extraction, `63514efc` | SHIPPED | `1688f508` | -- |
| balance | X-f | -- | **X-f** `feat(transactions): the app records when money moved` -- the DECOMPOSED parent, | open | -- | -- |
| balance | X-f1 | -- | **X-f1** `feat(transactions): a settle carries the day the money moved` -- absorbs **S2-b**. | SHIPPED | -- | -- |
| balance | X-an | -- | **X-an** `fix(loan): a payment is history from the day its money moved` -- the DECOMPOSED parent. | SHIPPED | `549015c0` | -- |
| balance | X-an-a | -- | **X-an-a** the CUT: the replay/projection boundary moves onto `settled_on`, through ONE predicate. Closes **N-187** | SHIPPED | `3d3f0ef5` | -- |
| balance | X-an-b | -- | **X-an-b** the anchor TIE-BREAK: ONE chronology key, not `id` bolted onto every ordering. Closes **N-196** | SHIPPED | `549015c0` | -- |
| balance | X-f2 | -- | **X-f2** `feat(accounts): the true-up is a reconciliation` -- R-DH (f)'s second half. The | open | -- | -- |
| balance | X-f3 | -- | **X-f3** `feat(cash): the ledger is sum-of-postings and the residual is classified` -- **THE | open | -- | balance:X-f2 |
| balance | X-f4 | -- | **X-f4** `refactor(cash): delete what the cutover orphans` -- `ReconciledThrough` and its 78 | open | -- | balance:X-f3 |
| balance | X-f5 | -- | **X-f5** `fix(ledger): the opening equity account holds only the opening` -- one balanced | open | -- | balance:X-f4 |
| balance | X-f6 | -- | **X-f6** `feat(import): the bank says when money moved` -- **RULED as the follow-on, not an | open | -- | balance:X-f5 |
| balance | X-ai | -- | **X-ai** `refactor(posting): the posted ledger gets one verb and one trigger` -- the DECOMPOSED parent; the END | open | -- | -- |
| balance | X-ai-a | -- | **X-ai-a** the cash verb, built from R-DV's sentence with N-162's, N-165's and N-166's | open | -- | -- |
| balance | X-ai-b | -- | **X-ai-b** the trigger: the commit-boundary grader, drained from the registry the writers | open | -- | -- |
| balance | X-ai-c | -- | **X-ai-c** the loan side onto the same verb. | open | -- | -- |
| balance | X-ai-g | -- | **X-ai-g** the bulk-statement census: each of the 20 sites is proven unable to touch a | open | -- | -- |
| balance | X-ai-s | -- | **X-ai-s** the `journal_entries` migration -- source identity as an EXCLUSIVE ARC of typed | open | -- | balance:X-f3 |
| balance | X-d | -- | **X-d** `fix(cash): the posted account ledger is a checked projection` -- E1a's shape for | open | -- | balance:X-ai |
| balance | X-aj | -- | **X-aj** `refactor(status): one status seam, and the fence is structural` -- the DECOMPOSED parent; rulings | open | -- | -- |
| balance | X-aj2 | -- | **X-aj2** the structural write door and the DELETION of W9907 (**R-DP**), carrying | open | -- | -- |
| balance | X-ak | -- | **X-ak** `refactor(transfers): a shadow inherits its parent's fields by ONE rule` -- closes | open | -- | balance:X-aj |
| balance | X-x | -- | **X-x** `refactor(balance): one pay-calendar precondition, one answer` -- the DECOMPOSED parent; closes **N-116**, | open | -- | balance:X-ad |
| balance | X-x1 | -- | **X-x1 THE ONE ANSWER** (R-CY) -- `PayCalendarGapError`, | open | -- | -- |
| balance | X-x2 | -- | **X-x2 THE FABRICATIONS** (R-CY) -- the branches that publish a figure the app did not | open | -- | balance:X-x1 |
| balance | X-x3 | -- | **X-x3 THE ONE PREDICATE** (R-DA) -- `onboarding.has_periods` asks Q2 rather than Q1, so the | open | -- | -- |
| balance | X-x4 | -- | **X-x4 THE STATES SPLIT** (R-CZ) -- an empty requested window stops answering with the | open | -- | -- |
| balance | X-x5 | -- | **X-x5 THE HARNESS** -- delete `verify_savings_producers.py`'s dict-or-attribute `_get` | open | -- | -- |
| balance | X-ad | -- | **X-ad** `feat(periods): the pay calendar a new user can actually enter` -- closes **N-123**, | open | -- | -- |
| balance | X-y | -- | **X-y** `refactor(balance): the baseline decision that is not the balance seam's` -- closes | open | -- | balance:X-x |
| balance | X-i | -- | **X-i** `refactor(balance): one read pass, one derivation, one clock` -- the DECOMPOSED parent; closes **FU-3**, | open | -- | -- |
| balance | X-i1 | -- | **X-i1 THE MEMO** -- additive, byte-identical on both databases. The context gains the input | open | -- | -- |
| balance | X-i2 | -- | **X-i2 THE CLOCK** -- the cutover. Each memoized loader takes `ctx.as_of` and `ctx.scenario`, | open | -- | balance:X-i1 |
| balance | X-j | -- | **X-j** `feat(balance): one account, one answer -- or a row that explains the difference` -- | open | -- | balance:X-i2 |
| balance | X-k | -- | **X-k** `fix(recurring): the recurrence bound is reconciled, not stored and forgotten` -- | open | -- | recurrence:R5 |
| balance | X-l | pay_calendar:C2 / recurrence:R-F12 | **X-l** `feat(periods): the pay calendar answers any date` -- closes **N-82**, **N-128**, and | open | -- | -- |
| balance | X-m | -- | **X-m** `refactor(growth): the projection engine takes its axis, not its boundaries` -- closes | open | -- | -- |
| balance | X-n | -- | **X-n** `fix(loan): a redistributed payment carries its REAL installment` -- closes **N-36**. | open | -- | -- |
| balance | X-e | -- | **X-e** `refactor(accounts): current_anchor_balance is a reconciled cache or it is nothing` -- | open | -- | -- |
| balance | X-p | -- | **X-p** `fix(analytics): the calendar's chips and its balance line are on one clock` -- closes | open | -- | balance:X-f |
| balance | X-ab | -- | **X-ab** `refactor(ledger): one asset-vs-liability rule, read and write` -- closes **N-122** | open | -- | -- |
| balance | X-ac | -- | **X-ac** `refactor(savings): one liquid-savings reduction` -- closes **N-121**. The cockpit | open | -- | -- |
| balance | X-ag | -- | **X-ag** `feat(pylint): lax digit acceptance is refused, not remembered` -- closes **N-139**. | open | -- | -- |
| balance | X-ah | -- | **X-ah** `fix(routes): a query-string id is parsed like every other id` -- closes **N-142**. | open | -- | -- |
| balance | X-al | -- | **X-al** `fix(pylint): a duplicate-code disable that suppresses nothing is a finding` -- | open | -- | -- |
| balance | X-am | -- | **X-am** `refactor(status): the settled band has two members, not three` -- closes **N-177**. | open | -- | -- |
| balance | E2-0 | -- | **E2-0** `the membership trace` -- NO code. Answer from the code: which modules are members, | open | -- | -- |
| balance | E2-n | -- | **E2-n** the move itself, and the registry deletion. Decide the decomposition from E2-0, not | open | -- | -- |
| balance | G1 | -- | **G1** `refactor(gates): the ledger-model and balance-seam fences stop carrying name lists` -- | open | -- | -- |
| balance | G2 | -- | **G2** `refactor(money): the value types that retire three checkers` -- retires **W9901**, | open | -- | -- |
| recurrence | R1-R3 | -- | **R1-R3 -- oracle, vocabulary, subtypes, write door, `Once` gone, forward engine.** `4b5c577b` | SHIPPED | `4b5c577b` | -- |
| recurrence | R4a | -- | **R4a, R4b-1, R4b-2 -- the forward cutover.** `1836a928`, `b4538d25`, `75346625`, archived | SHIPPED | `1836a928` | -- |
| recurrence | R5 | -- | **R5 -- a generated row carries THREE dates, in three places.** | open | -- | balance:X-f4 |
| recurrence | R6 | -- | **R6 -- Delete `payment_day`; one installment accessor.** | open | -- | recurrence:R5 (it READS `due_on`; "ships WITH balance:X-an" was unsatisfiable -- see section 0) |
| recurrence | R7a-1 | -- | **R7a-1 -- the Recurrence cell is one function over `(interval, unit)`.** `6fed14af`, on | SHIPPED | `6fed14af` | -- |
| recurrence | R7a-2 | -- | **R7a-2 -- the monthly equivalent becomes one function over the same pair.** | open | -- | -- |
| recurrence | R7b | -- | **R7b -- bounds and the form.** | open | -- | -- |
| recurrence | R7c | -- | **R7c -- the cutover.** | open | -- | recurrence:R7b |
| recurrence | R8 | -- | **R8 -- Add-ons.** | open | -- | recurrence:R7a-2 / recurrence:R7c |
| recurrence | R9 | -- | **R9 -- Drop the old columns.** | open | -- | recurrence:R7c |
| recurrence | R-F1 | -- | **R-F1 -- the lagging `ref` identity sequences are in step (F-1).** `44b25ad3`, migration | SHIPPED | `44b25ad3` | -- |
| recurrence | R-F6 | -- | **R-F6 -- Close the recurrence-rule leak, then delete what leaked** (finding F-6). | open | -- | -- |
| recurrence | R-F8 | -- | **R-F8 -- the deploy's safety net stops lying (F-8, F-14, R-R14).** `2e63e4f9`, `8aeae48e`, | SHIPPED | `2e63e4f9` | -- |
| recurrence | R-F2 | -- | **R-F2 -- Tighten the ref-seed parity scan's statement boundary** (finding F-2). | open | -- | -- |
| recurrence | R-F3 | -- | **R-F3 -- Resolve the ref-table constraint-naming disagreement** (finding F-3). | open | -- | -- |
| recurrence | R-F10 | pay_calendar:C5a (ticks it) | **R-F10 -- Delete the gap machinery the pay-calendar arc makes unconstructible** (finding | open | -- | pay_calendar:C4 |
| recurrence | R-F12 | pay_calendar:C2 / balance:X-l | **R-F12 -- One `PeriodCalendar`, not three period-containing searches** (finding F-12). | open | -- | -- |
| recurrence | R-F13 | -- | **R-F13 -- Close the three holes in this arc's own gate** (finding F-13). | open | -- | -- |
| recurrence | R-F7 | -- | **R-F7 -- Delete two unreachable branches in `_first_of_month_anchor`** (finding D11). | open | -- | -- |
| pay_calendar | C1 | -- | **C1 -- the derivation exists and is proven equal to what is stored.** `f9d148fe`, | SHIPPED | `f9d148fe` | -- |
| pay_calendar | C2 | balance:X-l / recurrence:R-F12 | **C2 -- one calendar value answers every "which period" question.** | open | -- | -- |
| pay_calendar | C3 | -- | **C3 -- the writer writes paydays, forward-only.** | open | -- | balance:X-ad |
| pay_calendar | C4 | -- | **C4 -- drop the derived columns.** | open | -- | pay_calendar:C2 / pay_calendar:C3 |
| pay_calendar | C5 | -- | **C5 -- the gap machinery goes, and a paycheck may owe one template twice.** The DECOMPOSED PARENT, split 2026-08-09; ticks with the last of its leaves | open | -- | pay_calendar:C4 / recurrence:R5 |
| pay_calendar | C5a | recurrence:R-F10 (ticked by it) | **C5a -- delete what is now unconstructible.** Deletion-only; the recurrence arc's 430-shape baseline stays byte-identical. It deletes no VISIBILITY -- see P16 | open | -- | pay_calendar:C4 |
| pay_calendar | C5b | -- | **C5b -- a paycheck may owe one template more than once.** `should_skip_period` becomes occurrence-aware; `refuse_unstorable_repeats` retires. Closes **P16** | open | -- | recurrence:R5 |
| pay_calendar | C6 | -- | **C6 -- a payday may be inserted mid-schedule.** | open | -- | pay_calendar:C4 / recurrence:R7c |
| pay_calendar | C7 | -- | **C7 -- the ledger entry derives its paycheck.** Rules `journal_entries.pay_period_id`, P1's defect on the ledger header. Closes **P18** | open | -- | pay_calendar:C4 |
| credit_card | CC0a | -- | **CC0a** `feat(ref): account types carry a revolving-credit kind` -- migration adds | open | -- | balance:X-f4 / balance:X-am |
| credit_card | CC0b | -- | **CC0b** `feat(cards): budget.credit_card_params satellite` -- model + migration as specced in | open | -- | balance:X-f4 / balance:X-am |
| credit_card | CC0c | -- | **CC0c** `feat(cards): card params setup flow` -- create/update routes + Marshmallow schema | open | -- | balance:X-f4 / balance:X-am |
| credit_card | CC1a | -- | **CC1a** `refactor(balance): the card consumes the shared instant-partition fold core` -- per | open | -- | balance:X-f4 / balance:X-am |
| credit_card | CC1b | -- | **CC1b** `feat(balance): a card is an event stream -- the revolving fold (additive)` -- | open | -- | balance:X-f3 / balance:X-f4 / balance:X-am |
| credit_card | CC1c | -- | **CC1c** `feat(balance): the seam dispatches REVOLVING to the fold (cutover)` -- four | open | -- | balance:X-f4 / balance:X-am |
| credit_card | CC2a | -- | **CC2a** `feat(cards): the statement cycle is a pure derivation` -- | open | -- | balance:X-f4 / balance:X-am |
| credit_card | CC2b | -- | **CC2b** `feat(cards): the finance charge folds the daily balance` -- | open | -- | balance:X-f4 / balance:X-am |
| credit_card | CC2c | -- | **CC2c** `feat(cards): card APR history rides rate_history` -- card-gated write route + schema | open | -- | balance:X-f4 / balance:X-am |
| credit_card | CC3a | -- | **CC3a** `feat(cards): charge-to-card (additive)` -- migration: | open | -- | balance:X-f4 / balance:X-am |
| credit_card | CC3b | -- | **CC3b** | open | -- | balance:X-f1 (shipped; absorbed the X-f1b leaf this once named) / balance:X-f4 / balance:X-am |
| credit_card | CC3c | -- | **CC3c** `feat(cards)!: envelope split tender + renames` -- rewrite `entry_credit_workflow.py` | open | -- | balance:X-f4 / balance:X-am |
| credit_card | CC3d | -- | **CC3d** `feat(cards): the card refuses what it cannot model` -- reject transfers OUT of the | open | -- | balance:X-f4 / balance:X-am |
| credit_card | CC4a | -- | **CC4a** `feat(cards): card_payment_settings` -- 1:1 `transfer_template_id` | open | -- | balance:X-f4 / balance:X-am |
| credit_card | CC4b | -- | **CC4b** `feat(cards): the payment you owe is the payment the card derives` -- | open | -- | balance:X-f4 / balance:X-am |
| credit_card | CC4c | -- | **CC4c** `feat(cards): underpayment warns and projects its finance charge` -- C7-style warning | open | -- | balance:X-f4 / balance:X-am |
| credit_card | CC5a | -- | **CC5a** `feat(cards): rewards accrue as a derived figure` -- the | open | -- | balance:X-f4 / balance:X-am |
| credit_card | CC5b | -- | **CC5b** `feat(cards): redemptions -- manual + auto-redeem threshold` -- manual route (settled | open | -- | balance:X-f4 / balance:X-am |

## Cross-arc forks

**Two steps in different arcs that are competing remedies for ONE defect.** Whichever ships first
decides for both, so the gate REFUSES a tick on either until `ruled` NAMES one of the competing
remedies. This is the predicate that would have caught `P3` / `N-123`, where one arc's ruling
deletes what the other's keeps. A ruled fork's defect row is owned by the remedy that won, and the
gate checks that too: a ruling nobody re-points is a ruling that decided nothing.

| defect | competing remedies | ruled |
|---|---|---|
| pay_calendar:P3 = balance:N-123 | balance:X-ad (R-DB: DELETE the bootstrap payday) **vs** pay_calendar:C3 (KEEP it, beside the real payday) | **balance:X-ad**, 2026-08-09 -- delete it, and registration asks for the LAST payday, not the next |
| pay_calendar:P16 | pay_calendar:C5b (make should_skip_period occurrence-aware) **vs** pay_calendar:C3 (refuse an over-long period at the writer) | **pay_calendar:C5b**, 2026-08-09 -- occurrence-aware; the writer option would refuse legitimate monthly schedules. Named `C5` when ruled; C5 DECOMPOSED the same day and the winning remedy is its `C5b` leaf, so both cells follow the work |
