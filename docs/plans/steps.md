# The step order

**This is the single source of truth for WHAT TO DO NEXT.** Every step in every arc appears here
exactly once, and the order table below is sorted into EXECUTION ORDER. The next step is its first
row. You do not need to read anything else to find it.

**How to read a row.**

| column | what it holds |
|---|---|
| `arc` + `id` | The step's key. An `id` alone is not unique across the corpus, so cite both |
| `also` | The other names this same step is filed under. `C2`, `X-l` and `R-F12` are ONE commit |
| `what this step does` | One sentence. If you need more, the arc's own document specifies it |
| `order` | `#N` is its place in the sequence; `container` is a grouping, never work; `SHIPPED` is done |
| `commit` | The hash for a shipped step. The code it shipped is the record; prose about it is not |
| `starts` | `NOW` means every blocker has shipped. Otherwise the rank it waits for, then the blockers |

**Where a step's detail lives**, by its `arc`:

| arc | document and section |
|---|---|
| `balance` | `../audits/balance_architecture/README.md`, section 5 |
| `recurrence` | `implementation_plan_recurrence_redesign.md`, section 4 |
| `pay_calendar` | `implementation_plan_pay_calendar.md`, section 4 |
| `credit_card` | `implementation_plan_credit_card.md`, "The steps" |

*Only the forks table at the foot of this file may have THREE columns: `tools/plan_gate` separates
the tables in this document by their column count, so a three-column table anywhere else is silently
read as a fork. That fragility is finding **N-234**, owned by `balance:X-ao`.*

**Anything under an `archive/` or `historical/` directory is a HISTORICAL RECORD and governs
nothing.** It may be cited for how something came to be; it may never be read as a live plan or as a
statement of the current state. The code as committed is the source of truth for what the app does.

## Working two steps at once

**Any row whose `starts` reads `NOW` can be started today, whatever its rank.** The rank is the
recommended sequence; `starts` is the hard constraint. That is how `X-f2-c2` and `C3` ran side by
side: both were unblocked and they shared no file.

**Two `NOW` rows are safe together when they are in different arcs or different phases.** Before
pairing two inside one arc, check that neither names a module the other deletes. A row marked
**MOVES MONEY** takes its own PR either way, so it is never the second lane.

**The rank is a DECISION, not a derivation.** 38 of these steps are legal to start right now, so the
dependency graph alone cannot say which comes next; the sequence below follows each arc's own stated
sequencing -- the balance README's ten blocks, and each plan's section 0.
**The `starts` column is DERIVED from the blocker keys beside it and the gate reconciles the two**,
so a rank can never contradict a real dependency and a stale `NOW` cannot survive a commit.

**113 steps, 95 open.** The dependency graph holds 94 edges over 59 rows.

## The order

| arc | id | also | what this step does | order | commit | starts |
|---|---|---|---|---|---|---|
| balance | X-ar | -- | Make the stored projected amount authoritative and delete the read-time override thread, so a row's amount has ONE answer instead of a cache that a reader repairs and never writes back. Closes **N-40**, **N-224**. | #1 | -- | NOW / balance:X-aq (shipped) |
| balance | X-ap | -- | Fix the THIRD settle door: the full-edit Status dropdown flips an envelope to Paid without consulting its entries, booking $400 where Mark Paid books $25. **MOVES MONEY.** Closes **N-219**. | #2 | -- | NOW / balance:X-f2-c2 (shipped) |
| balance | X-f2-c3 | -- | Settle transfer shadows through `transfer_service.update_transfer` so both legs and the parent move together, carrying the loan-payment freeze. **MOVES MONEY**. | #3 | -- | after #2 / balance:X-f2-c2 (shipped) / balance:X-ap (the third settle door is fixed while the verb is fresh; developer 2026-08-10) |
| balance | X-f3 | -- | THE CUTOVER: the assertion stops resetting the ledger, `balance(T)` becomes opening equity plus the sum of postings, and the reconciliation residual posts to Uncategorized rather than to Equity. **MOVES MONEY, OWN PR, NO BACKLOG.** Closes **N-171**, **N-172**, **N-174**. | #4 | -- | after #3 / balance:X-f2 (the container ticks at #3) |
| balance | X-f4 | -- | Delete what the cutover orphans: `ReconciledThrough` and its 78 references across 14 files, `_anchors.py`, the correction machinery and the R-I seed compensator. Closes **N-176**, **N-161**, **N-170**, **N-218**. | #5 | -- | after #4 / balance:X-f3 |
| balance | X-f5 | -- | Post one balanced entry moving $1,495.10 out of Checking Anchor Equity so the opening equity account holds only the opening, which makes the four-month income statement honest. | #6 | -- | after #5 / balance:X-f4 |
| pay_calendar | C2-b2 | -- | Delete `PeriodCalendar`, `SchedulePeriod` and `RecurrenceScheduleError`, repointing 10 `calendar_for` sites and 8 modules at the derived calendar. Owns **P26**, **P27**, **P28**. | #7 | -- | NOW / pay_calendar:C2-b1 (shipped) / balance:X-ad-a (shipped) / pay_calendar:C3 (shipped) |
| pay_calendar | C2-c | -- | Retire `balance_at/_cash_periods._PeriodSpans` so the cash view answers from the one calendar, keeping `None` outside the reported window as a VIEW question. Closes **P14**. | #8 | -- | NOW / pay_calendar:C2-a (shipped) / balance:X-ad-a (shipped) / pay_calendar:C3 (shipped) |
| pay_calendar | C2-e | -- | Delete `growth_engine.generate_projection_periods` and `SyntheticPeriod`, moving their six call sites onto the calendar's own projection axis. Closes **P7**, **P17**, **P20**, **P21**, **P22**, **P23**. | #9 | -- | NOW / pay_calendar:C2-a (shipped) / balance:X-ad-a (shipped) / pay_calendar:C3 (shipped) |
| pay_calendar | C2-f | -- | Resolve `pay_period_service`'s six `get_*` readers against the one calendar value across their 66 call sites. Closes **P19**. | #10 | -- | NOW / pay_calendar:C2-a (shipped) / balance:X-ad-a (shipped) / pay_calendar:C3 (shipped) |
| pay_calendar | C4 | -- | Drop `pay_periods.end_date` and `period_index` with their three constraints, once the ORM readers take their bounds from the calendar. Closes **P1**, **P4**, **P5**, **P8**, **P9**. | #11 | -- | after #10 / pay_calendar:C2 (the container ticks at #10) / pay_calendar:C3 (shipped) |
| pay_calendar | C5a | recurrence:R-F10 (ticked by it) | Delete the gap machinery the normalization makes unconstructible: `GenerationPlan.gaps`, `report_schedule_gaps` and `PlacementOutcome.SCHEDULE_GAP` with its six references. Deletion-only; the 430-shape baseline stays byte-identical. | #12 | -- | after #11 / pay_calendar:C4 |
| recurrence | R-F10 | pay_calendar:C5a (ticks it) | Delete the gap machinery the pay-calendar arc makes unconstructible; the same commit as `pay_calendar:C5a`, under this arc's name. Closes **F-10**. | #12 | -- | after #11 / pay_calendar:C4 |
| pay_calendar | C7 | -- | Rule and then fix `journal_entries.pay_period_id`, a NOT NULL FK stored beside the `entry_date` it derives from, which is P1's defect on the ledger's header table. Closes **P18**. | #13 | -- | after #11 / pay_calendar:C4 |
| pay_calendar | C8 | -- | Give the forward forecast cadence its own control, separating it from the payday forms it is currently welded onto. Closes **P30**. | #14 | -- | after #11 / pay_calendar:C4 |
| recurrence | R7a-2 | -- | Rewrite `savings_goal_service.amount_to_monthly` as four lines over `(interval_n, unit)`, replacing the `PAY_PERIODS_PER_YEAR` constant with a resolved per-user cadence and deriving `_INFREQUENT_PATTERNS`. **MOVES MONEY**. | #15 | -- | NOW |
| recurrence | R7b | -- | Rebuild the recurrence form as interval plus unit plus anchor plus an optional due row, giving `max_occurrences` its first writer and deleting `offset_periods` and the "First paycheck" affordance. Closes **D1**, **D2**. | #16 | -- | NOW |
| recurrence | R7c | -- | THE CUTOVER: one migration adds the two-axis columns, backfills them, and drops `pattern_id`, `day_of_month`, `month_of_year`, `start_period_id` and `offset_periods`. It must RULE row **D28** first. Closes **D10**, **D12**, **D21**, **D24**. | #17 | -- | after #16 / recurrence:R7b |
| recurrence | R8 | -- | Add the four ruled add-ons: the WEEK unit, `recurrence_weekday_anchors`, the business-day shift and the count-bounded end. | #18 | -- | after #17 / recurrence:R7a-2 / recurrence:R7c |
| recurrence | R9 | -- | Drop the `ref.recurrence_patterns` table and `pay_period_admin._repoint_recurrence_rules`, after re-checking the two premises ledger row **D6** names. | #19 | -- | after #17 / recurrence:R7c |
| pay_calendar | C6 | -- | Let a payday be inserted mid-schedule, refusing only where `classify_period_lock` says the split period is locked. Starts with the two rulings section 3 names. Closes **P10**. | #20 | -- | after #17 / pay_calendar:C4 / recurrence:R7c |
| recurrence | R5 | -- | Split a generated row's dates into three -- `occurs_on` (the cadence), `pay_period_id` (the funding) and `due_on` (the installment) -- and delete `compute_due_date`. A value-splitting migration; own PR. | #21 | -- | after #5 / balance:X-f4 (R5 edits `cash_ledger/_events.py`, which is inside that step's deletion set) |
| recurrence | R6 | -- | Delete `loan_params.payment_day` and collapse eight producers of "when is this installment due" into one `loan_installment_date` accessor. Kills **D4**; needs its own review pass. | #22 | -- | after #21 / recurrence:R5 (it READS `due_on`; "ships WITH balance:X-an" was unsatisfiable -- see section 0) |
| pay_calendar | C5b | -- | Make `should_skip_period` occurrence-aware so a paycheck may owe one template more than once, retiring `refuse_unstorable_repeats`. Needs a migration re-keying two partial unique indexes. Closes **P16**. | #23 | -- | after #21 / recurrence:R5 (it consumes the `occurs_on` column R5 creates) |
| balance | X-k | -- | Reconcile `RecurrenceRule.end_date` against what was actually generated, and give the write door a consistent batch contract. Closes **N-18**, **N-19**, **N-23**, **N-24**. | #24 | -- | after #21 / recurrence:R5 (R5 deletes `compute_due_date` and re-keys the generation index; rebasing this over that is the cheaper direction) |
| balance | X-ad-b | -- | Stop the rolling top-up manufacturing history: an automatic writer creates nothing on a lapsed schedule, and the lapse is surfaced instead. Closes **N-124**. | #25 | -- | NOW |
| balance | X-x1 | -- | Build the ONE answer to "no pay period covers this date": `PayCalendarGapError`, `require_current_period` / `covers`, one handler and its repair page, taking the grid's two pre-checks as its first callers. | #26 | -- | NOW |
| balance | X-x2 | -- | Move the branches that publish a figure the app did not compute onto the raising accessor: the fabricated $0.00 in four producers, and `build_trend_periods`' `current_index = 0` into an empty list. | #27 | -- | after #26 / balance:X-x1 |
| balance | X-x3 | -- | Make `onboarding.has_periods` ask "does a period cover today" rather than "do any periods exist", so the checklist and the page it renders on cannot disagree. | #28 | -- | NOW |
| balance | X-x4 | -- | Stop answering an empty requested WINDOW with the absence card, and stop the card's copy naming two states. | #29 | -- | NOW |
| balance | X-x5 | -- | Delete `verify_savings_producers.py`'s dict-or-attribute `_get` reader and the two-spelling readers its own docstring says this step deletes. | #30 | -- | NOW |
| balance | X-y | -- | Move the fifteen surfaces that resolve the baseline scenario directly onto the seam, deciding what a WRITE may do without one. Closes **N-117**. | #31 | -- | after #30 / balance:X-x (the container ticks at #30) |
| balance | X-am | -- | Decide whether the `Settled` status carries a meaning worth keeping -- 0 rows on both production tables, no writer, one line of the transition map -- and delete it if not. Closes **N-177**. | #32 | -- | NOW |
| balance | X-aj2 | -- | Make the status write door STRUCTURAL and DELETE the W9907 checker, ruling what a row may be BORN as. Closes **N-149**, **N-151**, **N-185**, **N-188**. | #33 | -- | NOW |
| balance | X-ak | -- | Rule the stored transfer-to-shadow copy first, then unify the THREE mirror implementations that already disagree. Closes **N-148**, **N-150**, **N-152**, **N-156**, **N-159**, **N-170**. | #34 | -- | after #33 / balance:X-aj (the container ticks at #33) |
| balance | X-ai-a | -- | Build the cash re-derive VERB from R-DV's sentence, with the union source set, the returned touched pairs and the per-account advisory lock in it from the start. Carries **N-162**, **N-165**, **N-166**. | #35 | -- | NOW |
| balance | X-ai-b | -- | Build the trigger: the commit-boundary grader, drained from the registry the writers populate. | #36 | -- | NOW |
| balance | X-ai-c | -- | Move the loan side onto the same re-derive verb. | #37 | -- | NOW |
| balance | X-ai-g | -- | Classify each of the 20 bulk-statement sites as unable to touch a posted row, routed through a writer, or named in the one docstring that states what the grader cannot see. Closes **N-163**. | #38 | -- | NOW |
| balance | X-ai-s | -- | Migrate `journal_entries` to an EXCLUSIVE ARC of typed FKs with an AT-MOST-ONE check, plus the reversal linkage. Held until the cutover, which deletes the correction family it would buy attribution for. Closes **N-167**. | #39 | -- | after #4 / balance:X-f3 |
| balance | X-d | -- | Make the posted account ledger a CHECKED PROJECTION: the posting writer consumes the walk instead of its own, with a per-visible-date assert. Carries **N-135**. | #40 | -- | after #39 / balance:X-ai (the container ticks at #39; N-155 parked this step and the assert's placement is the restructure's) |
| balance | X-i1 | -- | Give `BalanceContext` the input tier the loan derivations already have, through the same memoize mechanism: the calendar, the contribution feed, the override map, the standing extra, the contractual schedule. Additive and byte-identical. | #41 | -- | NOW |
| balance | X-i2 | -- | Hand each memoized loader `ctx.as_of` and `ctx.scenario`, so one read pass has one clock. **MOVES MONEY** ($3,631.74 today against $3,722.53 at a 2027 read). Closes **FU-3**, **N-14**, **N-56**, **N-72**, **N-89**, **N-91**, **N-92**, **N-93**, **N-115**. | #42 | -- | after #41 / balance:X-i1 |
| balance | X-j | -- | Rule which producer answers "what is this account worth" for a given surface, or render the row that explains the difference. Closes **N-87**, **N-90**, and **N-83**'s display half. | #43 | -- | after #42 / balance:X-i2 (X-j moves three surfaces onto the modelled view whose contribution load X-i2 fixes, so the other order ships a regression and then removes it) |
| balance | X-m | -- | Make `growth_engine.project_balance` take its AXIS rather than boundaries its caller must compute to match; a caller that gets it wrong costs $1,000.00 of annual-limit room per period. Closes **N-86**. | #44 | -- | NOW |
| balance | X-n | -- | Stop `_redistribute_to_distinct_months` OVERWRITING the real installment a payment satisfies when it shifts a colliding due date. Closes **N-36**. | #45 | -- | NOW |
| balance | X-e | -- | Re-take the census the column deletions made historical, then resolve what remains: two callerless public seam entries and a falsified de-duplication rationale. Carries **N-96**, **N-85**, **N-180**. | #46 | -- | NOW |
| balance | X-ab | -- | Give the posting path ONE asset-vs-liability rule instead of asking X-z's question a second time, and decide what a re-class does to accounts already carrying postings. Closes **N-122**. | #47 | -- | NOW |
| balance | X-ac | -- | Stop the cockpit reducing `_sum_liquid_balances` twice per render and publishing the answer under two context keys. Closes **N-121**. | #48 | -- | NOW |
| balance | X-ao | -- | Grade the arc documents' RULINGS tables, which no gate parses today, so a ruling id resolves to exactly one ruling and that ruling states a rule. Closes **N-217**, **N-220**. | #49 | -- | NOW |
| balance | X-ag | -- | Build the instrument that refuses lax digit acceptance, shown FIRING on a planted defect; both earlier method-name designs were refuted by measurement. Closes **N-139**. | #50 | -- | NOW |
| balance | X-ah | -- | Rule each of the 42 `request.args.get(..., type=int)` sites and parse them like every other id, with a second rule that admits a meaningful zero. Closes **N-142**. | #51 | -- | NOW |
| balance | X-al | -- | Census the fifteen live `duplicate-code` disables, none of them re-measured, and build the arm that catches a stale one -- `useless-suppression` is blind to them. Closes **N-154**. | #52 | -- | NOW |
| credit_card | CC0a | -- | Add `has_revolving_credit` to account types with the `REVOLVING` projection kind and its classifier branch, behind behavior-preserving shims. | #53 | -- | after #32 / balance:X-f4 / balance:X-am |
| credit_card | CC0b | -- | Add the `budget.credit_card_params` satellite model and its migration, inert by design. | #54 | -- | after #32 / balance:X-f4 / balance:X-am |
| credit_card | CC0c | -- | Add the card params setup flow: create and update routes, the Marshmallow schema, and the REVOLVING setup redirect. | #55 | -- | after #32 / balance:X-f4 / balance:X-am |
| credit_card | CC1a | -- | Make the card consume the shared instant-partition fold core rather than growing a second copy of it. | #56 | -- | after #32 / balance:X-f4 / balance:X-am |
| credit_card | CC1b | -- | Build `balance_at/_revolving.py`: a card is an event stream of anchor facts, settled rows and projected rows, unwired and additive. | #57 | -- | after #32 / balance:X-f3 / balance:X-f4 / balance:X-am |
| credit_card | CC1c | -- | Dispatch REVOLVING to the fold at four seam surfaces, deleting the CC0a shim and explaining every moved number. | #58 | -- | after #32 / balance:X-f4 / balance:X-am |
| credit_card | CC2a | -- | Derive the statement cycle purely: `cycle_window`, `statement_sequence`, `due_date_for`, `statement_balance`, `grace_kept` and `minimum_payment`. | #59 | -- | after #32 / balance:X-f4 / balance:X-am |
| credit_card | CC2b | -- | Fold the daily balance into a finance charge over effective-dated APR segments, with purchases joining the average daily balance on grace loss. | #60 | -- | after #32 / balance:X-f4 / balance:X-am |
| credit_card | CC2c | -- | Ride card APR history on `rate_history` through a card-gated write route, pinning that the loan loaders never see the card. | #61 | -- | after #32 / balance:X-f4 / balance:X-am |
| credit_card | CC3a | -- | Add `charge_to_card` and its undo as an additive action with row locking, guards and provenance, beside the still-live mark-credit. | #62 | -- | after #32 / balance:X-f4 / balance:X-am |
| credit_card | CC3b | -- | Cut mark-credit over to charge-to-card at the transaction level, with an in-migration backfill of every live pair and a provenance-restoring downgrade. | #63 | -- | after #32 / balance:X-f1 (shipped; absorbed the X-f1b leaf this once named) / balance:X-f4 / balance:X-am |
| credit_card | CC3c | -- | Rewrite `entry_credit_workflow.py` as `entry_card_charge.py` for split tender, rename `is_credit` to `is_card_tender`, and delete `credit_workflow.py` whole. | #64 | -- | after #32 / balance:X-f4 / balance:X-am |
| credit_card | CC3d | -- | Refuse what the card cannot model -- transfers OUT of the card -- and give `active_accounts_query` an orthogonal `revolving` filter. | #65 | -- | after #32 / balance:X-f4 / balance:X-am |
| credit_card | CC4a | -- | Add `card_payment_settings` with its payment-mode ref table and the creation flow mirroring the loan payment transfer. | #66 | -- | after #32 / balance:X-f4 / balance:X-am |
| credit_card | CC4b | -- | Derive the projected card payment from the statement balance at last close minus redemptions since, wired into the live override map. | #67 | -- | after #32 / balance:X-f4 / balance:X-am |
| credit_card | CC4c | -- | Warn on underpayment with a one-click "pay statement balance", and maintain ONE projected finance-charge expense when grace fails. | #68 | -- | after #32 / balance:X-f4 / balance:X-am |
| credit_card | CC5a | -- | Accrue rewards as a derived figure over settled purchases minus redemptions, carrying the `system_origin_id` migration. | #69 | -- | after #32 / balance:X-f4 / balance:X-am |
| credit_card | CC5b | -- | Add manual redemptions and the auto-redeem threshold, holding the one-live-row invariant under the concurrency shape. | #70 | -- | after #32 / balance:X-f4 / balance:X-am |
| balance | X-f6 | -- | Replace the date GUESS with the bank's own record via OFX / CSV / Plaid import, consuming the outstanding set and the residual path. Its first act is a trace, not code. Closes **N-173**. | #71 | -- | after #6 / balance:X-f5 (legal from #6; block 5 SCHEDULES it after the card arc so ONE matching rule covers checking and card rows rather than being widened into them later) |
| balance | X-p | -- | Put the analytics calendar's day chips and its balance line on one clock, or render the row that explains the gap. Closes **N-58**, **N-97**. | #72 | -- | after #71 / balance:X-f (the container ticks at #71; the import shrinks the date noise at its source, so ruling before it decides against numbers that then change) |
| recurrence | R-F2 | -- | Tighten the ref-seed parity scan's statement boundary. Closes **F-2**. | #73 | -- | NOW |
| recurrence | R-F3 | -- | Resolve the ref-table constraint-naming disagreement. Closes **F-3**. | #74 | -- | NOW |
| recurrence | R-F6 | -- | Close the recurrence-rule leak, then delete the orphaned rules it made. Closes **F-6**. | #75 | -- | NOW |
| recurrence | R-F7 | -- | Delete two unreachable branches in `_first_of_month_anchor`. Closes **D11**. | #76 | -- | NOW |
| recurrence | R-F13 | -- | Close the three holes in this arc's own gate. Closes **F-13**. | #77 | -- | NOW |
| balance | E2-0 | -- | Trace the super-package membership from the code: which modules are members, what the public re-export surface is, and whether any member imports a non-member. Expect it to DECOMPOSE. Carries **N-33**, **N-35**. | #78 | -- | NOW |
| balance | E2-n | -- | Make the move and delete the registry, with `_FENCED_MODULE_RULINGS` as the LAST commit rather than the first. Its decomposition is decided from #78's trace. | #79 | -- | NOW |
| balance | G1 | -- | Trace each allowlist entry to its real cause, then stop the ledger-model and balance-seam fences carrying name lists. Closes **N-147**. | #80 | -- | NOW |
| balance | G2 | -- | Build the `Money` and `DisplayLabel` value types that retire W9901, W9904 and W9902, taking the small label half first. Phase G runs INSIDE E2 by ruling R-DQ. | #81 | -- | NOW |

## Containers

**A container is a DECOMPOSED parent: a name for a group of steps, never a thing you do.** It ticks
when the last of its leaves ships. It is listed here rather than in the order so that every row of
the order is workable.

| arc | id | also | what this step does | order | commit | starts |
|---|---|---|---|---|---|---|
| balance | X-f | -- | The DECOMPOSED parent of the whole "the app records when money moved" cluster, carrying **N-42**. | container | -- | ticks with #71 |
| balance | X-f2 | -- | The DECOMPOSED parent of the true-up-is-a-reconciliation cluster, which is R-DH (f)'s second half. | container | -- | ticks with #3 |
| balance | X-f2-c | -- | The DECOMPOSED parent of the OUTSTANDING SET, widened to transactions, envelopes and transfers and grouped by envelope (**R-EW**). | container | -- | ticks with #3 |
| balance | X-ai | -- | The DECOMPOSED parent of the posted-ledger restructure: one verb and one trigger on both ledgers (**R-DU**, **R-DV**). Carries **N-144**, **N-153**, **N-155**, **N-157**, **N-158**, **N-160**, **N-164**. | container | -- | ticks with #39 |
| balance | X-aj | -- | The DECOMPOSED parent of the one-status-seam work; its merge half shipped as X-aj1. Carries **N-145**. | container | -- | ticks with #33 |
| balance | X-x | -- | The DECOMPOSED parent of the one-pay-calendar-precondition work. Closes **N-116**, **N-125**, **N-126**, **N-129**. | container | -- | ticks with #30 / balance:X-ad-a (shipped) / pay_calendar:C3 (shipped; ruling R-EY 2026-08-10 moved N-127 there, which ended the "X-ad then X-x, ONE PR" pairing) |
| balance | X-ad | -- | The DECOMPOSED parent of the pay calendar a new user can actually enter, split 2026-08-10 (**R-EZ**) into the door that CREATES a calendar and the door that GROWS it. | container | -- | ticks with #25 |
| balance | X-i | -- | The DECOMPOSED parent of the one-read-pass work: nine ledger rows with one root cause. | container | -- | ticks with #42 |
| balance | X-l | pay_calendar:C2 / recurrence:R-F12 | The DECOMPOSED parent under its BALANCE name of "the pay calendar answers any date". Closes **N-82**, **N-128**, and **N-79**'s far half. | container | -- | ticks with #10 |
| pay_calendar | C2 | balance:X-l / recurrence:R-F12 | The DECOMPOSED parent under its PAY-CALENDAR name: one calendar value answers every "which period" question, RULED on three forks 2026-08-10. | container | -- | ticks with #10 |
| recurrence | R-F12 | pay_calendar:C2 / balance:X-l | The DECOMPOSED parent under its RECURRENCE name: one `PeriodCalendar`, not three period-containing searches. Closes **F-12**. | container | -- | ticks with #10 |
| pay_calendar | C2-b | -- | The DECOMPOSED parent of the recurrence cutover, split 2026-08-10 on an instrumented full-suite measurement. | container | -- | ticks with #7 / pay_calendar:C2-a (shipped) |
| pay_calendar | C5 | -- | The DECOMPOSED parent of "the gap machinery goes, and a paycheck may owe one template twice", split 2026-08-09. | container | -- | ticks with #23 / pay_calendar:C4 / recurrence:R5 |

## Shipped

**One line each, and the COMMIT is the record.** Read the code it shipped, not a paragraph about it.
The fuller as-built entries are in each arc's archive.

| arc | id | also | what this step does | order | commit | starts |
|---|---|---|---|---|---|---|
| balance | X-f1 | -- | A settle carries the day the money moved; absorbed **S2-b**. Fourteen leaves, condensed into `archive/…2026-08-04.md` 1a. | SHIPPED | `8d812662` | -- |
| balance | X-an | -- | A loan payment is history from the day its money moved (**R-EK**) -- the DECOMPOSED parent, complete at two leaves, condensed into `archive/…2026-08-04.md` 1b. Closed **N-187**, **N-196**; opened **N-207**-**N-211**. | SHIPPED | `549015c0` | -- |
| balance | X-f2-c1 | -- | The reconcile reader and writer got their own module home, all three panel doors got the kind gate, and purchases NEST under their parent. Closed **N-216**; opened **N-217**, **N-218**. | SHIPPED | `24701c1d` | -- |
| balance | X-aq | -- | A settle books the freshest figure for the row, resolved in the VERB so every settle door agrees (**R-FE**, amended by **R-FH** to write the cache). Opened **N-224**. | SHIPPED | `9cabc206` | -- |
| balance | X-f2-c2 | -- | The TRANSACTION twin: the envelope's close, bills and income settled on the STATEMENT date through the service-tier verb. Closed **N-222**, **N-223**, **N-227**; opened **N-225**-**N-233**. | SHIPPED | `d23b55fd` | balance:X-f2-c1 / balance:X-aq (the panel must not display a figure the grid contradicts; R-FE) |
| balance | X-ad-a | -- | Registration ASKS for the most recent payday, the cadence and the horizon; the bootstrap payday is DELETED. Closed **N-123** (= `pay_calendar:P3`). | SHIPPED | `2a4eb477` | -- |
| recurrence | R1-R3 | -- | Oracle, vocabulary, subtypes, write door, `Once` retired, forward engine. Archived to `historical/recurrence_as_built_2026-08-05.md`. | SHIPPED | `4b5c577b` | -- |
| recurrence | R4a | -- | The forward cutover, three commits, archived to `historical/recurrence_as_built_2026-08-08.md`. Closed **D3**, **D5**, **D22**, **D25**, **D7**. | SHIPPED | `1836a928` | -- |
| recurrence | R7a-1 | -- | The Recurrence cell became one function over `(interval, unit)`. Closed **D17**. | SHIPPED | `6fed14af` | -- |
| recurrence | R-F1 | -- | The lagging `ref` identity sequences are back in step. Closed **F-1**. | SHIPPED | `44b25ad3` | -- |
| recurrence | R-F8 | -- | The deploy's safety net stops lying: back up unconditionally, pre-flight the rollback, and refuse the one that cannot work. Closed **F-8**, **F-14**. | SHIPPED | `2e63e4f9` | -- |
| pay_calendar | C1 | -- | The derivation exists and is proven equal to what is stored. Opened **P15**, **P16**. | SHIPPED | `f9d148fe` | -- |
| pay_calendar | C2-a | -- | The one calendar VALUE, with nothing calling it: `PayCalendar`, three named questions, and a window that is a VIEW. Opened **P21**-**P25**. | SHIPPED | `3cb3082f` | -- |
| pay_calendar | C2-b1 | -- | The calendar's last two questions, the cadence rule, and the one DB door. Opened **P28**. | SHIPPED | `90f2fbb7` | pay_calendar:C2-a |
| pay_calendar | C2-d | -- | The filing cutover: both posting writers call the filing rule through one door. Closed **N-169**. | SHIPPED | `3e6cd4ec` | pay_calendar:C2-a |
| pay_calendar | C3 | -- | The writer writes paydays, forward-only. The DECOMPOSED parent, ticked with C3-b. | SHIPPED | `7e3fb33b` | balance:X-ad-a |
| pay_calendar | C3-a | -- | The destructive form stops keying on an ordinal; the tail is selected by PAYDAY. Closed **P13**; opened **P29**, **P30**. | SHIPPED | `5f1e2bd6` | balance:X-ad-a |
| pay_calendar | C3-b | -- | `pay_period_write` is the one place in `app/` that constructs or deletes a pay period, materialising `derive_periods` on every write. Closed **P2**'s writer half, **P12**, **P29**, **N-127**; opened **P31**, **P32**; found **P33**. | SHIPPED | `7e3fb33b` | pay_calendar:C3-a |

## Cross-arc forks

**Two steps in different arcs that are competing remedies for ONE defect.** Whichever ships first
decides for both, so the gate REFUSES a tick on either until `ruled` NAMES one of the competing
remedies. A ruled fork's defect row is owned by the remedy that won, and the gate checks that too: a
ruling nobody re-points is a ruling that decided nothing.

**`pay_calendar:P3` = `balance:N-123` LEFT this table on 2026-08-10**, and how it left is the point:
it was ruled to `balance:X-ad` on 2026-08-09, that remedy SHIPPED as `X-ad-a` (`2a4eb477`), and the
defect row closed with it. A fork whose defect no longer exists binds nothing.

| defect | competing remedies | ruled |
|---|---|---|
| pay_calendar:P16 | pay_calendar:C5b (make should_skip_period occurrence-aware) **vs** pay_calendar:C3 (refuse an over-long period at the writer) | **pay_calendar:C5b**, 2026-08-09 -- occurrence-aware; the writer option would refuse legitimate monthly schedules. Named `C5` when ruled; C5 DECOMPOSED the same day and the winning remedy is its `C5b` leaf, so both cells follow the work |
