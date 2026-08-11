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
an arc document STATES it: the graph holds 93 edges over 58 rows, the gaps are real, and a guess
where a citation belongs is worse than a blank. *That count read "71 over 47" until 2026-08-10 and
had been stale since `C2` decomposed into six leaves carrying three blockers each -- a prose count
beside no reconciler, in the registry whose own rule 3 grades exactly that in `ledger.md`.*

**`aliases` is what this table exists for.** `C2`, `X-l` and `R-F12` are ONE step under three names,
scheduled as one commit by all three documents. Before this table that identity was prose in three
files with nothing reconciling it, and the `P3` / `N-123` collision went unnoticed from April to
2026-08-09.

**112 steps, 96 open.**

| arc | id | aliases | step | state | commit | blocked by |
|---|---|---|---|---|---|---|
| balance | X-f | -- | **X-f** `feat(transactions): the app records when money moved` -- the DECOMPOSED parent, | open | -- | -- |
| balance | X-f1 | -- | **X-f1** `feat(transactions): a settle carries the day the money moved` -- absorbs **S2-b**. | SHIPPED | `8d812662` | -- |
| balance | X-an | -- | **X-an** `fix(loan): a payment is history from the day its money moved` -- the DECOMPOSED parent, COMPLETE at two leaves, condensed into `archive/…2026-08-04.md` Section 1b | SHIPPED | `549015c0` | -- |
| balance | X-f2 | -- | **X-f2** `feat(accounts): the true-up is a reconciliation` -- R-DH (f)'s second half, the DECOMPOSED parent | open | -- | -- |
| balance | X-f2-c | -- | **X-f2-c** the OUTSTANDING SET, widened to transactions, envelopes and transfers, grouped by envelope (**R-EW**) -- the DECOMPOSED parent, three leaves | open | -- | -- |
| balance | X-f2-c1 | -- | **X-f2-c1** the reconcile reader and writer get their own module home, all three panel doors get the kind gate, and purchases NEST under their parent. No new row kind, no money moves. Closed **N-216**; opened **N-217**, **N-218** | SHIPPED | `24701c1d` | -- |
| balance | X-aq | -- | **X-aq** `fix(transactions): a settle books the freshest figure for the row` (**R-FE**) -- the settle verb resolves the amount when the caller supplies none, so every settle door books one figure. MOVES MONEY at the grid | SHIPPED | `9cabc206` | -- |
| balance | X-ar | -- | **X-ar** `refactor(cash): a projected row's amount has ONE answer` (**R-FE**) -- the stored amount becomes authoritative and the read-time override thread is deleted. Closes **N-40** and **N-224** | open | -- | balance:X-aq |
| balance | X-f2-c2 | -- | **X-f2-c2** the TRANSACTION twin: the envelope's own close tick and bills, settled on the STATEMENT date through the service-tier settle verb (**R-FA**). MOVES MONEY | SHIPPED | `d23b55fd` | balance:X-f2-c1 / balance:X-aq (the panel must not display a figure the grid contradicts; R-FE) |
| balance | X-f2-c3 | -- | **X-f2-c3** transfer shadows in their own group, settled through the transfer service with the loan-payment freeze (**R-FA**). MOVES MONEY | open | -- | balance:X-f2-c2 / balance:X-ap (the third settle door is fixed while the verb is fresh; developer 2026-08-10) |
| balance | X-ap | -- | **X-ap** `fix(transactions): the full-edit Status dropdown settles like every other door` -- the THIRD settle door R-FA's text missed. An envelope-tracked row flipped to Paid via the popover never consults its entries, so $25 of purchases against a $400 estimate books $400. MOVES MONEY | open | -- | balance:X-f2-c2 |
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
| balance | X-x | -- | **X-x** `refactor(balance): one pay-calendar precondition, one answer` -- the DECOMPOSED parent; closes **N-116**, | open | -- | balance:X-ad-a (the bootstrap payday, the state its refusals were producing) / pay_calendar:C3 (the WORKING repair its refusals point at -- ruling R-EY 2026-08-10 moved N-127 there, which ended the "X-ad then X-x, ONE PR" pairing) |
| balance | X-x1 | -- | **X-x1 THE ONE ANSWER** (R-CY) -- `PayCalendarGapError`, | open | -- | -- |
| balance | X-x2 | -- | **X-x2 THE FABRICATIONS** (R-CY) -- the branches that publish a figure the app did not | open | -- | balance:X-x1 |
| balance | X-x3 | -- | **X-x3 THE ONE PREDICATE** (R-DA) -- `onboarding.has_periods` asks Q2 rather than Q1, so the | open | -- | -- |
| balance | X-x4 | -- | **X-x4 THE STATES SPLIT** (R-CZ) -- an empty requested window stops answering with the | open | -- | -- |
| balance | X-x5 | -- | **X-x5 THE HARNESS** -- delete `verify_savings_producers.py`'s dict-or-attribute `_get` | open | -- | -- |
| balance | X-ad | -- | **X-ad** `feat(periods): the pay calendar a new user can actually enter` -- the DECOMPOSED parent, split 2026-08-10 (**R-EZ**); ticks with the last of its leaves | open | -- | -- |
| balance | X-ad-a | -- | **X-ad-a -- registration asks.** The bootstrap payday DELETES; the form takes the most recent payday, the cadence and the horizon; `establish_schedule` writes the periods and the `pay_schedule` row in one call. Closes **N-123** (= pay_calendar:P3) | SHIPPED | `2a4eb477` | -- |
| balance | X-ad-b | -- | **X-ad-b -- the automatic writer stops writing history.** The rolling top-up creates nothing on a lapsed schedule (**R-EZ**). Closes **N-124** | open | -- | -- |
| balance | X-y | -- | **X-y** `refactor(balance): the baseline decision that is not the balance seam's` -- closes | open | -- | balance:X-x |
| balance | X-i | -- | **X-i** `refactor(balance): one read pass, one derivation, one clock` -- the DECOMPOSED parent; closes **FU-3**, | open | -- | -- |
| balance | X-i1 | -- | **X-i1 THE MEMO** -- additive, byte-identical on both databases. The context gains the input | open | -- | -- |
| balance | X-i2 | -- | **X-i2 THE CLOCK** -- the cutover. Each memoized loader takes `ctx.as_of` and `ctx.scenario`, | open | -- | balance:X-i1 |
| balance | X-j | -- | **X-j** `feat(balance): one account, one answer -- or a row that explains the difference` -- | open | -- | balance:X-i2 |
| balance | X-k | -- | **X-k** `fix(recurring): the recurrence bound is reconciled, not stored and forgotten` -- | open | -- | recurrence:R5 |
| balance | X-l | pay_calendar:C2 / recurrence:R-F12 | **X-l** `feat(periods): the pay calendar answers any date` -- closes **N-82**, **N-128**, and. The DECOMPOSED parent under its balance name: the leaves are `pay_calendar:C2-a`..`C2-f`, so what may start here is `C2-a` | open | -- | -- |
| balance | X-m | -- | **X-m** `refactor(growth): the projection engine takes its axis, not its boundaries` -- closes | open | -- | -- |
| balance | X-n | -- | **X-n** `fix(loan): a redistributed payment carries its REAL installment` -- closes **N-36**. | open | -- | -- |
| balance | X-e | -- | **X-e** `refactor(accounts): current_anchor_balance is a reconciled cache or it is nothing` -- | open | -- | -- |
| balance | X-p | -- | **X-p** `fix(analytics): the calendar's chips and its balance line are on one clock` -- closes | open | -- | balance:X-f |
| balance | X-ab | -- | **X-ab** `refactor(ledger): one asset-vs-liability rule, read and write` -- closes **N-122** | open | -- | -- |
| balance | X-ac | -- | **X-ac** `refactor(savings): one liquid-savings reduction` -- closes **N-121**. The cockpit | open | -- | -- |
| balance | X-ag | -- | **X-ag** `feat(pylint): lax digit acceptance is refused, not remembered` -- closes **N-139**. | open | -- | -- |
| balance | X-ah | -- | **X-ah** `fix(routes): a query-string id is parsed like every other id` -- closes **N-142**. | open | -- | -- |
| balance | X-ao | -- | **X-ao** `feat(plan-gate): a ruling id resolves to one ruling` -- closes **N-217**. The registries are graded on findings, steps and their graph; the RULINGS tables are not graded at all, and the corpus carried a live collision. One arm per arc document, on the same parser the other arms use | open | -- | -- |
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
| recurrence | R-F12 | pay_calendar:C2 / balance:X-l | **R-F12 -- One `PeriodCalendar`, not three period-containing searches** (finding F-12). The DECOMPOSED parent under its recurrence name; `pay_calendar:C2-b` is the leaf that retires `PeriodCalendar` | open | -- | -- |
| recurrence | R-F13 | -- | **R-F13 -- Close the three holes in this arc's own gate** (finding F-13). | open | -- | -- |
| recurrence | R-F7 | -- | **R-F7 -- Delete two unreachable branches in `_first_of_month_anchor`** (finding D11). | open | -- | -- |
| pay_calendar | C1 | -- | **C1 -- the derivation exists and is proven equal to what is stored.** `f9d148fe`, | SHIPPED | `f9d148fe` | -- |
| pay_calendar | C2 | balance:X-l / recurrence:R-F12 | **C2 -- one calendar value answers every "which period" question.** The DECOMPOSED parent, RULED on three forks 2026-08-10; ticks with the last of its leaves, and that tick is also `balance:X-l` and `recurrence:R-F12` | open | -- | -- |
| pay_calendar | C2-a | -- | **C2-a -- the one calendar VALUE, and nothing calls it.** `PayCalendar`, three named questions, a window that is a VIEW, and the recurrence calendar DELEGATING three of its own methods to the shared primitives. Opened **P21**-**P25** | SHIPPED | `3cb3082f` | -- |
| pay_calendar | C2-b | -- | **C2-b -- the recurrence cutover.** The DECOMPOSED parent, split 2026-08-10 on an instrumented full-suite measurement; ticks with the last of its leaves | open | -- | pay_calendar:C2-a |
| pay_calendar | C2-b1 | -- | **C2-b1 -- the calendar's last two questions, the cadence rule, and the one DB door.** `period_by_id` / `earliest_start_in_month` move; `cadence_days` becomes `int \| None`, refused beside a payday; `pay_calendar.calendar_for` loads. Nothing calls it. Corrected **P25**'s two expired docstrings; opened **P28** | SHIPPED | `90f2fbb7` | pay_calendar:C2-a |
| pay_calendar | C2-b2 | -- | **C2-b2 -- the cutover.** `PeriodCalendar` / `SchedulePeriod` / `RecurrenceScheduleError` DELETE; 10 `calendar_for` sites and 8 modules repoint; the 5 tests a simulated cutover fails are rewritten. Owns **P26**, **P27**, **P28** | open | -- | pay_calendar:C2-b1 / balance:X-ad-a (deletes the registration door that opens a hole) / pay_calendar:C3 (deletes the free-date door; developer ruling 2026-08-10 -- the derived calendar ABSORBS a hole instead of reporting it, row **P27**, so the generators close BEFORE the absorber lands) |
| pay_calendar | C2-c | -- | **C2-c -- the cash-view cutover.** `balance_at/_cash_periods._PeriodSpans` retires (**P14**) | open | -- | pay_calendar:C2-a / balance:X-ad-a (deletes the registration door that opens a hole) / pay_calendar:C3 (deletes the free-date door; developer ruling 2026-08-10 -- the derived calendar ABSORBS a hole instead of reporting it, row **P27**, so the generators close BEFORE the absorber lands) |
| pay_calendar | C2-d | -- | **C2-d -- the filing cutover.** `find_period_containing_date`, `resolve_anchor_pay_period` and `owner_pay_periods` DELETE; both posting writers call the filing rule through one door. Closed **N-169**. **NOT blocked by the P27 pair**: `filing_period` bisects on `start_date` and never reads an end, proven over GAPPED shapes and on a production snapshot | SHIPPED | `3e6cd4ec` | pay_calendar:C2-a |
| pay_calendar | C2-e | -- | **C2-e -- the projection axis.** `generate_projection_periods` and `SyntheticPeriod` DELETE. Closes **P7**, **P17**, **P20** | open | -- | pay_calendar:C2-a / balance:X-ad-a (deletes the registration door that opens a hole) / pay_calendar:C3 (deletes the free-date door; developer ruling 2026-08-10 -- the derived calendar ABSORBS a hole instead of reporting it, row **P27**, so the generators close BEFORE the absorber lands) |
| pay_calendar | C2-f | -- | **C2-f -- the readers answer from the calendar.** `pay_period_service`'s six `get_*` across 66 call sites. Closes **P19** | open | -- | pay_calendar:C2-a / balance:X-ad-a (deletes the registration door that opens a hole) / pay_calendar:C3 (deletes the free-date door; developer ruling 2026-08-10 -- the derived calendar ABSORBS a hole instead of reporting it, row **P27**, so the generators close BEFORE the absorber lands) |
| pay_calendar | C3 | -- | **C3 -- the writer writes paydays, forward-only.** The DECOMPOSED parent, split 2026-08-10 (developer) because the ordinal wire key and the writer are two commits: only one of them takes user input, and only one can renumber. Also owns **N-127**, the interior hole's repair, moved here 2026-08-10 by ruling `balance:R-EY`. Ticks with the last of its leaves | open | -- | balance:X-ad-a |
| pay_calendar | C3-a | -- | **C3-a -- the destructive form stops keying on an ordinal.** `keep_through_index` becomes `keep_through_period_id`, a `RowId`; the truncate service resolves it against the owner's own periods and refuses anything else as `PayPeriodUnresolved`, logging both F-144 branches; the tail is selected by PAYDAY, so nothing in the operation reads a column C4 drops. The lock classifier moves to `pay_period_locks` (developer ruling; `pay_period_admin` hit the 1000-line ceiling and the two concerns are read-predicate vs destructive writer). Closes **P13**; opened **P29**, **P30** | SHIPPED | `5f1e2bd6` | balance:X-ad-a |
| pay_calendar | C3-b | -- | **C3-b -- the writer materialises the derivation.** `generate_pay_periods` stops authoring `end_date` / `period_index`; `_reject_overlapping_batch` is REPLACED by the forward rule of ruling **R-PC1**, not deleted -- deleting it would open the mid-schedule insert C6 defers. Truncate re-materialises the new last period's projected end. Closes **P2**, **P12**, **N-127**, and P27's second end; half-closes **P15** | open | -- | pay_calendar:C3-a |
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

**`pay_calendar:P3` = `balance:N-123` LEFT this table on 2026-08-10**, and how it left is the point:
it was ruled to `balance:X-ad` on 2026-08-09, that remedy SHIPPED as `X-ad-a` (`2a4eb477`), and the
defect row closed with it. A fork whose defect no longer exists binds nothing -- the gate says so,
and a line kept "for the record" would be a fork about nothing. The record lives where the work
does: that step's entry, the `P3` section of the pay-calendar plan, and the commit.

| defect | competing remedies | ruled |
|---|---|---|
| pay_calendar:P16 | pay_calendar:C5b (make should_skip_period occurrence-aware) **vs** pay_calendar:C3 (refuse an over-long period at the writer) | **pay_calendar:C5b**, 2026-08-09 -- occurrence-aware; the writer option would refuse legitimate monthly schedules. Named `C5` when ruled; C5 DECOMPOSED the same day and the winning remedy is its `C5b` leaf, so both cells follow the work |
