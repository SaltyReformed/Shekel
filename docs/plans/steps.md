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

*A table in this file is found by its own HEADER, so one added for any other purpose joins no
registry: `tools/plan_gate` separated them by COLUMN COUNT until 2026-08-14, and a three-column
reference table read as four unruled forks. That was finding **N-234**, closed by `d8aed644`.*

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

**The rank is a DECISION, not a derivation.** 48 of these steps are legal to start right now, so the
dependency graph alone cannot say which comes next; the sequence below follows each arc's own stated
sequencing -- the balance README's ten blocks, and each plan's section 0.
**The `starts` column is DERIVED from the blocker keys beside it and the gate reconciles the two**,
so a rank can never contradict a real dependency and a stale `NOW` cannot survive a commit.

**146 steps, 102 open.** The dependency graph holds 81 edges over 53 rows.

## The order

| arc | id | also | what this step does | order | commit | starts |
|---|---|---|---|---|---|---|
| balance | X-f3c | -- | THE CUTOVER: the assertion stops resetting the ledger, `balance(T)` becomes opening equity plus the sum of postings, and an unexplained difference becomes a recorded uncategorized transaction the user accepts. **MOVES MONEY, OWN PR, NO BACKLOG.** Closes **N-171**, **N-172**, **N-174**, **N-275**, **N-276**. | #1 | -- | NOW / balance:X-f3b (shipped) |
| balance | X-f3a-2 | -- | Record that a STATEMENT was walked line by line, so a line it did not show is NOT CLEARED rather than unknown, and widen the offer set to every uncleared line including the already-settled ones, which **MOVES MONEY**; it closes **N-273** and **N-289**. | #2 | -- | after #1 / balance:X-f3c |
| balance | X-f4 | -- | Delete what the cutover orphans: `ReconciledThrough`'s coverage rule, `_anchors.py`, the correction machinery and the R-I seed compensator, a set X-f3a has already narrowed. Closes **N-176**, **N-218**, **N-161**, **N-170**. | #3 | -- | after #1 / balance:X-f3c |
| bank_import | X-f6b | -- | Add the automated SOURCE ADAPTER -- SimpleFIN recommended on the security ground ruling R-FP states -- so a statement arrives without a manual download, deciding first where a scheduled fetch runs in an app with no scheduler. | #4 | -- | NOW |
| balance | X-au-d | -- | Cut SALARY rows over: generation stops pricing them, the 51 live rows go NULL, and `income_service.live_projected_net`, `_freshest_amount` and `_reconcile_cached_amount` are deleted. Closes **N-224**, **N-228**. | #5 | -- | NOW / balance:X-au-c3 |
| balance | X-au-e | -- | Cut TEMPLATE rows over to their template's amount series: generation stops pricing them, the 511 non-override rows go NULL, and regeneration's amount arm with the conflict chooser's keep-vs-use decision are both deleted. | #6 | -- | NOW / balance:X-au-c3 / balance:X-au-a (shipped) |
| bank_import | X-f6c | -- | Let a NEW-ENVELOPE merchant answer create a recurring TEMPLATE once and name that template thereafter, so the container a policy files into carries an identity across pay periods instead of a NAME the app has to converge on -- which waits on `X-au-e` because a row that has a template has a derivation to read, and that is the constraint `X-au-e` rebuilds. Closes **N-328**. | #7 | -- | after #6 / balance:X-au-e |
| balance | X-au-g | -- | Cut LOAN-PAYMENT shadows over, which must first RULE that a shadow's P&I resolves on its own due date as ruling D5 already put its escrow, because a resolver may not read the wall clock. Closes **N-40**. | #8 | -- | NOW / balance:X-au-c3 |
| balance | X-au-f | -- | Cut TRANSFER rows and their shadows over, deleting the amount copy in `update_transfer` and the drift corrector beside it, which makes Transfer Invariant 3 structural rather than maintained. | #9 | -- | NOW / balance:X-au-c3 |
| balance | X-au-h | -- | Split the FOUR facts `is_override` carries, of which two were found by adversarial review: re-priced, moved, survives the regeneration sweep, and exempt from the partial unique index. Closes **N-238**. | #10 | -- | after #9 / balance:X-au-d / balance:X-au-e / balance:X-au-f / balance:X-au-g |
| pay_calendar | C4 | -- | Drop `pay_periods.end_date` and `period_index` with their three constraints, once the ORM readers take their bounds from the calendar. Closes **P1**, **P4**, **P5**, **P8**, **P9**. | #11 | -- | NOW / pay_calendar:C2 (shipped) / pay_calendar:C3 (shipped) |
| pay_calendar | C7 | -- | Rule and then fix `journal_entries.pay_period_id`, a NOT NULL FK stored beside the `entry_date` it derives from, which is P1's defect on the ledger's header table. Closes **P18**. | #12 | -- | after #11 / pay_calendar:C4 |
| pay_calendar | C8 | -- | Give the forward forecast cadence its own control, separating it from the payday forms it is currently welded onto. Closes **P30**. | #13 | -- | after #11 / pay_calendar:C4 |
| pay_calendar | C9 | -- | Project the modelled fold's CONTRIBUTION tier past the pay-period horizon on the same axis the accrual tier already runs on, superseding balance ruling **R-AG**, which let the half model stand before a total calendar existed. **MOVES MONEY.** Closes **P7**; carries **P42**, **P44** and **P50**. | #14 | -- | NOW / pay_calendar:C2-e (shipped) |
| pay_calendar | C6 | -- | Let a payday be inserted mid-schedule, refusing only where `classify_period_lock` says the split period is locked. Starts with the two rulings section 3 names. Closes **P10**. | #15 | -- | after #11 / pay_calendar:C4 / recurrence:R7c (shipped) |
| recurrence | R5 | -- | Split a generated row's dates into three -- `occurs_on` (the cadence), `pay_period_id` (the funding) and `due_on` (the installment) -- and delete `compute_due_date`. A value-splitting migration; own PR. | #16 | -- | after #3 / balance:X-f4 (R5 edits `cash_ledger/_events.py`, which is inside that step's deletion set) |
| recurrence | R6 | -- | Delete `loan_params.payment_day` and collapse eight producers of "when is this installment due" into one `loan_installment_date` accessor. Kills **D4**; needs its own review pass. | #17 | -- | after #16 / recurrence:R5 (it READS `due_on`; "ships WITH balance:X-an" was unsatisfiable -- see section 0) |
| recurrence | R8-b | -- | Make the WEEK unit authorable -- the cadence the closed pattern set could never express -- by deleting `has_row_date_coordinate`, which R5's `occurs_on` makes unnecessary rather than merely satisfied. | #18 | -- | after #16 / recurrence:R5 |
| recurrence | R8-c | -- | Give a rule an nth-weekday coordinate ("the third Tuesday") as an EXCLUSIVE ARC of columns on `budget.recurrence_rules` under one CHECK, dropping the unwritten `budget.recurrence_weekday_anchors` table, and make `describe`'s coordinate dispatch on the coordinate KIND rather than on "WEEK or else". Closes **D29**. | #19 | -- | after #16 / recurrence:R5 |
| recurrence | R8-d | -- | Add the business-day shift over weekends plus a COMPUTED US federal holiday set, applied to the cash date alone, which needs `occurs_on` to exist because today's generated row is dated from its pay period and never from the occurrence. | #20 | -- | after #16 / recurrence:R5 |
| pay_calendar | C5b | -- | Make `should_skip_period` occurrence-aware so a paycheck may owe one template more than once, retiring `refuse_unstorable_repeats`. Needs a migration re-keying two partial unique indexes. Closes **P16**. | #21 | -- | after #16 / recurrence:R5 (it consumes the `occurs_on` column R5 creates) |
| balance | X-k | -- | Reconcile `RecurrenceRule.end_date` against what was actually generated, and give the write door a consistent batch contract. Closes **N-18**, **N-19**, **N-23**, **N-24**. | #22 | -- | after #16 / recurrence:R5 (R5 deletes `compute_due_date` and re-keys the generation index; rebasing this over that is the cheaper direction) |
| balance | X-ad-b | -- | Stop the rolling top-up manufacturing history: an automatic writer creates nothing on a lapsed schedule, and the lapse is surfaced instead. Closes **N-124**. | #23 | -- | NOW |
| balance | X-x1 | -- | Build the ONE answer to "no pay period covers this date": `PayCalendarGapError`, `require_current_period` / `covers`, one handler and its repair page, taking the grid's two pre-checks as its first callers. | #24 | -- | NOW |
| balance | X-x2 | -- | Move the branches that publish a figure the app did not compute onto the raising accessor: the fabricated $0.00 in four producers, and `build_trend_periods`' `current_index = 0` into an empty list. | #25 | -- | after #24 / balance:X-x1 |
| balance | X-x3 | -- | Make `onboarding.has_periods` ask "does a period cover today" rather than "do any periods exist", so the checklist and the page it renders on cannot disagree. | #26 | -- | NOW |
| balance | X-x4 | -- | Stop answering an empty requested WINDOW with the absence card, and stop the card's copy naming two states. | #27 | -- | NOW |
| balance | X-x5 | -- | Delete `verify_savings_producers.py`'s dict-or-attribute `_get` reader and the two-spelling readers its own docstring says this step deletes. | #28 | -- | NOW |
| balance | X-y | -- | Move the fifteen surfaces that resolve the baseline scenario directly onto the seam, deciding what a WRITE may do without one. Closes **N-117**. | #29 | -- | after #28 / balance:X-x (the container ticks at #30) |
| balance | X-am | -- | Decide whether the `Settled` status carries a meaning worth keeping -- 0 rows on both production tables, no writer, one line of the transition map -- and delete it if not. Closes **N-177**. | #30 | -- | NOW |
| balance | X-aj2 | -- | Make the status write door STRUCTURAL and DELETE the W9907 checker, ruling what a row may be BORN as. Closes **N-149**, **N-151**, **N-185**, **N-188**. | #31 | -- | NOW |
| balance | X-ak | -- | Rule the stored transfer-to-shadow copy first, then unify the THREE mirror implementations that already disagree. Closes **N-148**, **N-150**, **N-152**, **N-156**, **N-159**, **N-170**. | #32 | -- | after #31 / balance:X-aj (the container ticks at #33) |
| balance | X-ai-a | -- | Build the cash re-derive VERB from R-DV's sentence, with the union source set, the returned touched pairs and the per-account advisory lock in it from the start. Carries **N-162**, **N-165**, **N-166**. | #33 | -- | NOW |
| balance | X-ai-b | -- | Build the trigger: the commit-boundary grader, drained from the registry the writers populate. | #34 | -- | NOW |
| balance | X-ai-c | -- | Move the loan side onto the same re-derive verb. | #35 | -- | NOW |
| balance | X-ai-g | -- | Classify each of the 20 bulk-statement sites as unable to touch a posted row, routed through a writer, or named in the one docstring that states what the grader cannot see. Closes **N-163**. | #36 | -- | NOW |
| balance | X-ai-s | -- | Migrate `journal_entries` to an EXCLUSIVE ARC of typed FKs with an AT-MOST-ONE check, plus the reversal linkage. Held until the cutover, which deletes the correction family it would buy attribution for. Closes **N-167**. | #37 | -- | after #1 / balance:X-f3c |
| balance | X-d | -- | Make the posted account ledger a CHECKED PROJECTION: the posting writer consumes the walk instead of its own, with a per-visible-date assert. Carries **N-135**. | #38 | -- | after #37 / balance:X-ai (the container ticks at #39; N-155 parked this step and the assert's placement is the restructure's) |
| balance | X-i1 | -- | Give `BalanceContext` the FOUR remaining inputs of the tier the loan derivations already have -- the contribution feed, the override map, the standing extra, the contractual schedule -- since `pay_calendar:C2-c` took the calendar early, as a method beside `loan_walk` rather than through `_memoize_once`. Additive and byte-identical. | #39 | -- | NOW |
| balance | X-i2 | -- | Hand each memoized loader `ctx.as_of` and `ctx.scenario`, so one read pass has one clock. **MOVES MONEY** ($3,631.74 today against $3,722.53 at a 2027 read). Closes **FU-3**, **N-14**, **N-56**, **N-72**, **N-89**, **N-91**, **N-92**, **N-93**, **N-115**. | #40 | -- | after #39 / balance:X-i1 |
| balance | X-j | -- | Rule which producer answers "what is this account worth" for a given surface, or render the row that explains the difference. Closes **N-87**, **N-90**, and **N-83**'s display half. | #41 | -- | after #40 / balance:X-i2 (X-j moves three surfaces onto the modelled view whose contribution load X-i2 fixes, so the other order ships a regression and then removes it) |
| balance | X-m | -- | Make `growth_engine.project_balance` take its AXIS rather than boundaries its caller must compute to match; a caller that gets it wrong costs $1,000.00 of annual-limit room per period. Closes **N-86**. | #42 | -- | NOW |
| balance | X-n | -- | Stop `_redistribute_to_distinct_months` OVERWRITING the real installment a payment satisfies when it shifts a colliding due date. Closes **N-36**. | #43 | -- | NOW |
| balance | X-e | -- | Re-take the census the column deletions made historical, then resolve what remains: two callerless public seam entries and a falsified de-duplication rationale. Carries **N-96**, **N-85**, **N-180**. | #44 | -- | NOW |
| balance | X-ab | -- | Give the posting path ONE asset-vs-liability rule instead of asking X-z's question a second time, and decide what a re-class does to accounts already carrying postings. Closes **N-122**. | #45 | -- | NOW |
| balance | X-ac | -- | Stop the cockpit reducing `_sum_liquid_balances` twice per render and publishing the answer under two context keys. Closes **N-121**. | #46 | -- | NOW |
| balance | X-ao | -- | Grade the arc documents' RULINGS tables, which no gate parses today, so a ruling id resolves to exactly one ruling and that ruling states a rule. Closes **N-217**, **N-220**. | #47 | -- | NOW |
| balance | X-ag | -- | Build the instrument that refuses lax digit acceptance, shown FIRING on a planted defect; both earlier method-name designs were refuted by measurement. Closes **N-139**. | #48 | -- | NOW |
| balance | X-ah | -- | Rule each of the 34 `request.args.get(..., type=int)` sites and parse them like every other id, with a second rule that admits a meaningful zero. Closes **N-142**. | #49 | -- | NOW |
| balance | X-al | -- | Census the fifteen live `duplicate-code` disables, none of them re-measured, and build the arm that catches a stale one -- `useless-suppression` is blind to them. Closes **N-154**. | #50 | -- | NOW |
| credit_card | CC0a | -- | Add `has_revolving_credit` to account types with the `REVOLVING` projection kind and its classifier branch, behind behavior-preserving shims. | #51 | -- | after #30 / balance:X-f4 / balance:X-am |
| credit_card | CC0b | -- | Add the `budget.credit_card_params` satellite model and its migration, inert by design. | #52 | -- | after #30 / balance:X-f4 / balance:X-am |
| credit_card | CC0c | -- | Add the card params setup flow: create and update routes, the Marshmallow schema, and the REVOLVING setup redirect. | #53 | -- | after #30 / balance:X-f4 / balance:X-am |
| credit_card | CC1a | -- | Make the card consume the shared instant-partition fold core rather than growing a second copy of it. | #54 | -- | after #30 / balance:X-f4 / balance:X-am |
| credit_card | CC1b | -- | Build `balance_at/_revolving.py`: a card is an event stream of anchor facts, settled rows and projected rows, unwired and additive. | #55 | -- | after #30 / balance:X-f3c / balance:X-f4 / balance:X-am |
| credit_card | CC1c | -- | Dispatch REVOLVING to the fold at four seam surfaces, deleting the CC0a shim and explaining every moved number. | #56 | -- | after #30 / balance:X-f4 / balance:X-am |
| credit_card | CC2a | -- | Derive the statement cycle purely: `cycle_window`, `statement_sequence`, `due_date_for`, `statement_balance`, `grace_kept` and `minimum_payment`. | #57 | -- | after #30 / balance:X-f4 / balance:X-am |
| credit_card | CC2b | -- | Fold the daily balance into a finance charge over effective-dated APR segments, with purchases joining the average daily balance on grace loss. | #58 | -- | after #30 / balance:X-f4 / balance:X-am |
| credit_card | CC2c | -- | Ride card APR history on `rate_history` through a card-gated write route, pinning that the loan loaders never see the card. | #59 | -- | after #30 / balance:X-f4 / balance:X-am |
| credit_card | CC3a | -- | Add `charge_to_card` and its undo as an additive action with row locking, guards and provenance, beside the still-live mark-credit. | #60 | -- | after #30 / balance:X-f4 / balance:X-am |
| credit_card | CC3b | -- | Cut mark-credit over to charge-to-card at the transaction level, with an in-migration backfill of every live pair and a provenance-restoring downgrade. | #61 | -- | after #30 / balance:X-f1 (shipped; absorbed the X-f1b leaf this once named) / balance:X-f4 / balance:X-am |
| credit_card | CC3c | -- | Rewrite `entry_credit_workflow.py` as `entry_card_charge.py` for split tender, rename `is_credit` to `is_card_tender`, and delete `credit_workflow.py` whole. | #62 | -- | after #30 / balance:X-f4 / balance:X-am |
| credit_card | CC3d | -- | Refuse what the card cannot model -- transfers OUT of the card -- and give `active_accounts_query` an orthogonal `revolving` filter. | #63 | -- | after #30 / balance:X-f4 / balance:X-am |
| credit_card | CC4a | -- | Add `card_payment_settings` with its payment-mode ref table and the creation flow mirroring the loan payment transfer. | #64 | -- | after #30 / balance:X-f4 / balance:X-am |
| credit_card | CC4b | -- | Derive the projected card payment from the statement balance at last close minus redemptions since, as a CARD rule behind the amount resolver rather than a sixth entry in an override map. | #65 | -- | after #30 / balance:X-f4 / balance:X-am |
| credit_card | CC4c | -- | Warn on underpayment with a one-click "pay statement balance", and maintain ONE projected finance-charge expense when grace fails. | #66 | -- | after #30 / balance:X-f4 / balance:X-am |
| credit_card | CC5a | -- | Accrue rewards as a derived figure over settled purchases minus redemptions, carrying the `system_origin_id` migration. | #67 | -- | after #30 / balance:X-f4 / balance:X-am |
| credit_card | CC5b | -- | Add manual redemptions and the auto-redeem threshold, holding the one-live-row invariant under the concurrency shape. | #68 | -- | after #30 / balance:X-f4 / balance:X-am |
| balance | X-p | -- | Put the analytics calendar's day chips and its balance line on one clock, or render the row that explains the gap. Closes **N-58**, **N-97**. | #69 | -- | after #3 / balance:X-f (the container ticks at #4; the import shrinks the date noise at its source, so ruling before it decides against numbers that then change) |
| balance | E2-0 | -- | Trace the super-package membership from the code: which modules are members, what the public re-export surface is, and whether any member imports a non-member. Expect it to DECOMPOSE. Carries **N-33**, **N-35**. | #70 | -- | NOW |
| balance | E2-n | -- | Make the move and delete the registry, with `_FENCED_MODULE_RULINGS` as the LAST commit rather than the first. Its decomposition is decided from `balance:E2-0`'s trace. | #71 | -- | NOW |
| balance | G1 | -- | Trace each allowlist entry to its real cause, then stop the ledger-model and balance-seam fences carrying name lists. Closes **N-147**. | #72 | -- | NOW |
| balance | G2 | -- | Build the `Money` and `DisplayLabel` value types that retire W9901, W9904 and W9902, taking the small label half first. Phase G runs INSIDE E2 by ruling R-DQ. | #73 | -- | NOW |
| balance | X-at | -- | Surface which tax year a figure was actually computed against, and give a new year's bracket set a write door, so the resolver's substitution stops being invisible and the settings screen can finish the year it starts. Closes **N-235**, **N-236**. | #74 | -- | NOW |
| balance | X-av | -- | Give the base annual salary an effective date so the app can tell a raise from a correction, which `apply_raises` already does for every raise but cannot do for the figure they are applied to. Closes **N-237**. | #75 | -- | NOW |
| balance | X-aw | -- | Count a calendar year's paydays from the stored cadence rather than from whichever periods happen to have been generated, so a period's gross stops moving by a cent when the schedule extends. Closes **N-239**. | #76 | -- | NOW |
| balance | X-ax | -- | Reconcile a carry-forward rollover against its source: when a settled source's spend changes, the target row's top-up moves by the same amount so the two always sum to the budget the rollover divided. **MOVES MONEY.** Closes **N-249**. | #77 | -- | NOW |
| balance | X-ay | -- | Post a modelled account's own CONTRIBUTIONS and ACCRUALS, so its balance-assertion residual is what the account earned rather than what it earned plus what was paid into it. Closes **N-277**. | #78 | -- | NOW |
| recurrence | R7d | -- | Stop PERSISTING the CLOSING half of a loan payment's validity window -- `loan_recurrence_sync` becomes a resolver that generation applies over the rule's own bound, deleting nine of its ten write chokepoints -- so `ck_recurrence_rules_valid_window` lands true by construction while `starts_on` stays as the cadence anchor. Closes **D35**. | #79 | -- | NOW |
| recurrence | R7e | -- | Make the recurrence form's THREE-state fields one typed submission the schema emits, so `stated` / `cleared` / `not mentioned` stops being re-derived by a `KEY in data` read at each route site. Closes **D36**. | #80 | -- | NOW |
| recurrence | R7f | -- | Decide, then apply, what a PROGRAMMATICALLY created recurrence starts on: the contribution and salary-profile routes seat theirs at the schedule's OPENING and fan rows into closed pay periods, where the form defaults to today. **MOVES MONEY.** Closes **D34**. | #81 | -- | NOW |
| recurrence | R11 | -- | Rule and then add the LEAD placement -- funding an occurrence from a paycheck EARLIER than the one containing it -- which is what survived **D20** when measurement showed `CONTAINING_DATE` already funds on or before every occurrence. **MOVES MONEY.** Closes **D40**. | #82 | -- | NOW |
| recurrence | R12 | -- | Give `deploy/shekel-deploy.sh` an executable test for the two predicates a destructive release now rests on, `preflight_migrations` and `repin_is_safe`, deciding first where a shell harness runs in a repository whose gates scope to `app/`, `scripts/` and `tools/`. Closes **D41**. | #83 | -- | NOW |
| pay_calendar | C10 | -- | Take the FIVE `app/routes/salary/` reads of "which paycheck am I in" off the process clock and onto the owner's civil day, which is the half of that row `C2-f3a` did not reach. Closes **P49**. | #84 | -- | NOW |
| pay_calendar | C11 | -- | Close the last FIVE service doors that open their own read pass and assert the LAYER predicate in their place -- no module under `app/services/**` calls `BalanceContext.build` -- carving out `loan_recurrence_sync`, which is a writer and takes one by design. Closes **P56**, **P69**. | #85 | -- | NOW |
| pay_calendar | C12 | -- | Rule and then collapse the THREE implementations of "the owner's current paycheck breakdown" into one producer, which changes what two other pages publish and is what gives `income_service`'s amount basis a threaded calendar; it also owes the paycheck engine's own 1000-line ceiling, which no other step shrinks. Closes **P62**, **P63**, **P64**'s engine half. | #86 | -- | NOW |
| recurrence | R13 | -- | Give `budget.pay_schedule` a cadence KIND so a semi-monthly or monthly owner's paydays land on the days of the month they are actually paid, rather than on a fixed-length walk that drifts through it, branching the batch writer, the derivation and the paycheck count on the kind. Ruling **R-R28**. | #87 | -- | NOW |
| recurrence | R14 | -- | Rule, then apply, what a payroll deduction's gross is priced from -- whose salary, which period's figure, and against which clock -- so the employee contribution and the employer-match basis stop being blind to every raise the owner has taken without collapsing a multi-profile owner onto one arbitrary gross. **MOVES MONEY.** Closes **D45**. | #88 | -- | NOW |
| recurrence | R15 | -- | Rule, then apply, what a payroll deduction's own FREQUENCY means: `deductions_per_year` stores 26 / 24 / 12 as a MODE the engine only ever compares against, so a weekly owner's every-paycheck deduction is stored and shown as 26 while it is taken 52 times, and "skip the 3rd paycheck" names nothing at their cadence. Closes **F-21**. | #89 | -- | after #88 / recurrence:R14 |

## Containers

**A container is a DECOMPOSED parent: a name for a group of steps, never a thing you do.** It ticks
when the last of its leaves ships. It is listed here rather than in the order so that every row of
the order is workable.

| arc | id | also | what this step does | order | commit | starts |
|---|---|---|---|---|---|---|
| balance | X-f | -- | The DECOMPOSED parent of the whole "the app records when money moved" cluster, carrying **N-42**. | container | -- | ticks with #3 |
| balance | X-au | -- | The DECOMPOSED parent of the amount model (**R-FI**): a row's amount is either its OWN or DERIVED, and a derived amount is not stored at all. Supersedes **X-ar**; **X-au-i is WITHDRAWN** because the card arc's locked rulings delete the CC payback it would have cut over (`CC3b`, `CC3c`). Carries **N-40**, **N-224**, **N-228**, **N-238**. | container | -- | ticks with #10 |
| balance | X-au-c | -- | The DECOMPOSED parent of the amount model's SEAM, split into three leaves 2026-08-12: the schema and the declaration, the readers, then the freeze and its inverse. | container | -- | ticks with #4 |
| balance | X-ai | -- | The DECOMPOSED parent of the posted-ledger restructure: one verb and one trigger on both ledgers (**R-DU**, **R-DV**). Carries **N-144**, **N-153**, **N-155**, **N-157**, **N-158**, **N-160**, **N-164**. | container | -- | ticks with #37 |
| balance | X-aj | -- | The DECOMPOSED parent of the one-status-seam work; its merge half shipped as X-aj1. Carries **N-145**. | container | -- | ticks with #31 |
| balance | X-x | -- | The DECOMPOSED parent of the one-pay-calendar-precondition work. Closes **N-116**, **N-125**, **N-126**, **N-129**. | container | -- | ticks with #28 |
| balance | X-ad | -- | The DECOMPOSED parent of the pay calendar a new user can actually enter, split 2026-08-10 (**R-EZ**) into the door that CREATES a calendar and the door that GROWS it. | container | -- | ticks with #23 |
| balance | X-i | -- | The DECOMPOSED parent of the one-read-pass work: nine ledger rows with one root cause. | container | -- | ticks with #40 |
| balance | X-l | pay_calendar:C2 / recurrence:R-F12 | The DECOMPOSED parent under its BALANCE name of "the pay calendar answers any date". Closed **N-128** (at `C2-c`). It claimed **N-82** and **N-79**'s far half and delivered NEITHER, so both were re-pointed at the tick rather than closed. | SHIPPED | `4f134bf4` | -- |
| pay_calendar | C2 | balance:X-l / recurrence:R-F12 | The DECOMPOSED parent under its PAY-CALENDAR name: one calendar value answers every "which period" question, RULED on three forks 2026-08-10. | SHIPPED | `4f134bf4` | -- |
| recurrence | R-F12 | pay_calendar:C2 / balance:X-l | The DECOMPOSED parent under its RECURRENCE name: one `PeriodCalendar`, not three period-containing searches. Closes **F-12**. | SHIPPED | `4f134bf4` | -- |
| pay_calendar | C2-f | -- | The DECOMPOSED parent of `pay_period_service`'s six `get_*` readers over their 60 `app/` call sites, split by READER 2026-08-14 because 11 functions pair `get_current_period` with `get_all_periods` and separating those two leaves a context object holding one of each. | SHIPPED | `4f134bf4` | -- |
| pay_calendar | C2-f3 | -- | The DECOMPOSED parent of the rest, split into FIVE leaves 2026-08-18 when the step's own census came back at 12 `app/` call sites, and NARROWED on 2026-08-19 when two of them were promoted out to `C11` and `C12` for gating `C4` behind work that removes no reader of a derived column. | SHIPPED | `4f134bf4` | -- |
| pay_calendar | C2-f2 | -- | The DECOMPOSED parent of the readers at a surface that already holds a read pass, ticked with C2-f2e, its last leaf. | SHIPPED | `531c1402` | -- |
| pay_calendar | C5 | -- | The DECOMPOSED parent of "the gap machinery goes, and a paycheck may owe one template twice", split 2026-08-09. | container | -- | ticks with #21 |
| balance | X-f3 | -- | The DECOMPOSED parent of THE CUTOVER, re-decomposed 2026-08-13 when measurement refuted two of ruling R-EB's premises. Carries **N-171**, **N-172**, **N-174**. | container | -- | ticks with #2 |
| balance | X-f3a | -- | The DECOMPOSED parent of "clearing is a recorded fact", split 2026-08-14 (**R-FQ**) into the RECORDING half, which moves no money, and the half that makes a walked statement's SILENCE mean something, which cannot precede the cutover. Carries **N-273**. | container | -- | ticks with #2 |
| bank_import | X-f6 | -- | The DECOMPOSED parent of the statement importer, whose first leaf moved AHEAD of the cutover because what the cutover needed was clearing facts rather than an import surface. Carries **N-173**. | container | -- | ticks with #7 |
| bank_import | X-f6a | -- | The DECOMPOSED parent of the importer's CORE, split into three leaves 2026-08-16 when the match predicate came back many-to-many in BOTH directions and into FOUR on 2026-08-18 when the third leaf's own premise was measured false. Carried **N-173**. | SHIPPED | `9439a5ad` | -- |
| recurrence | R8 | -- | The DECOMPOSED parent of the ruled add-ons, split 2026-08-16 when measurement showed three of the four need a generated row's own `occurs_on`: `recurrence_engine.compute_due_date` dates a row from its PAY PERIOD and never from the occurrence, so a weekly row, an nth-weekday row and a shifted row would each carry a date the cadence never named. | container | -- | ticks with #20 |

## Shipped

**One line each, and the COMMIT is the record.** Read the code it shipped, not a paragraph about it.
The fuller as-built entries are in each arc's archive.

| arc | id | also | what this step does | order | commit | starts |
|---|---|---|---|---|---|---|
| balance | X-au-j | -- | Give BOTH read passes ONE amount basis for the whole pass instead of one per priced row -- the statement-match review pass and the reconcile panel each derive one and thread it, and both `settle_amount` twins take it as a REQUIRED parameter so the cost cannot regrow by a caller saying nothing. Closed **N-295**, **N-309**, **N-252**, **N-323**. | SHIPPED | `cc7679a7` | -- |
| bank_import | X-f6a-4 | -- | An import can be UNDONE -- it releases the matches naming its lines, forgets the source-account pairing with the last import from that source, and the database refuses to orphan a match -- while a same-day group reconciles as a SET so the ordinal this app mints stops deciding whether a line is held. Closed **N-302**, **N-317**, **N-324**, **N-327**; opened **N-328**, **N-330**, `balance:N-331`. | SHIPPED | `9439a5ad` | -- |
| balance | X-au-c3 | -- | A settle RECORDS what moved instead of refreshing an amount: `settled_amount` + `settled_basis_id` + `settled_on` are one record the status seam alone writes, a revert releases the ASSERTION and KEEPS the fact, and both full-edit popovers correct a figure IN PLACE. Closed **N-241**, **N-242**, **N-257**, **N-259**, **N-265**, **N-282**, **N-298**; opened **N-301**-**N-304**. | SHIPPED | `3d1379d1` | -- |
| recurrence | R-F17 | -- | A forward window named in MONTHS is resolved in the OWNER's paychecks: `PayCadence.paychecks_within` replaced the hardcoded 6 / 13 / 26 / 52 in the balance chips, the "Interest, next 12 mo" chip, the grid's 6M / 1Y / 2Y buttons, the mobile Plan window and the dashboard pulse chart, whose canvas announced six months over thirteen weeks. Closed **F-17**; opened **F-21** and **P73**. | SHIPPED | `e2afd21b` | -- |
| recurrence | R10-b | -- | A transfer regeneration MAINTAINS what it generated: an edit stopped destroying 99 transfers and 198 shadows to rewrite values already there, a note and a `$321.45` figure kept through a revert now survive one, and `update_transfer` learned to move a transfer's ACCOUNTS -- two of the six derived columns it could not write. Ruling **R-R32**. | SHIPPED | `ea776528` | -- |
| bank_import | X-f6a-3c-2 | -- | A whole reviewed pass is ONE request against ONE derivation of the account: the 215 acts the review screen offers cost 13.2 minutes of round trips and now take 13.37 s end to end, each item in its own SAVEPOINT so a refusal costs only itself, and the screen answers with its own receipt. | SHIPPED | `46bec314` | -- |
| bank_import | X-f6a-3c | -- | The DECOMPOSED parent of the review screen's THROUGHPUT, ticked with X-f6a-3c-2, its last leaf. | SHIPPED | `46bec314` | -- |
| bank_import | X-f6a-3d | -- | A destination is stated ONCE PER MERCHANT and SUGGESTED thereafter, never selected: the merchant becomes a column the adapter records, a policy names a template or a new envelope or *never a purchase*, and one sweep ticks what it places -- which turns 91 questions into 21 on a statement whose leftover lines are 21 merchants. Closed **N-325**; opened **N-326** and **N-327**. | SHIPPED | `50fed660` | -- |
| bank_import | X-f6a-3c-1 | -- | Every candidate row carries the WINDOW the app believes its money moved in -- a settle day, a purchase day, or a bill's whole pay period -- so no row the matcher OFFERS is unbounded, and the global undated pool that switched group matching off is deleted rather than reported. Closed **N-312**, **N-315**, **N-316**; opened **N-322** and restored **N-317** with its diagnosis corrected. | SHIPPED | `c25edd96` | -- |
| recurrence | R-F16 | -- | "How often am I paid" stopped having two stored answers: `salary_profiles.pay_periods_per_year` dropped and the paycheck engine takes a `PayrollBasis` binding a profile to the cadence its paychecks arrive on, which only 5 of 365 legal cadences could ever have agreed with. Closed **F-16**; opened **D43**, **D44**, **D45**. | SHIPPED | `4258ce28` | -- |
| balance | X-au-c2b | -- | Every reader that took a row's BUDGET off `estimated_amount` now resolves it: one `amounts_by_id` batch and one `owned_amount` accessor across 14 sites, a `budgets` map published to every render path in place of a transient attribute read behind an `is defined` fallback, and a pricing pass pinned to the OWNER rather than to a row set. Closed **N-268**, **N-269**, **N-270**; opened **N-294**. | SHIPPED | `a24f0b80` | -- |
| balance | X-f3b | -- | A purchase carrying a recorded bank posting day is a cash movement of its OWN -- in the walk, in the ledger and as its own posting source -- so an envelope's close books only what its purchases did not. 1 of 215 sampled days moves on a production clone. Closed **N-274**, **N-286**, **N-288**; opened **N-290**, **N-291**. | SHIPPED | `38ffd87b` | -- |
| recurrence | R10-a | -- | A regeneration MAINTAINS the rows it already generated rather than destroying and rebuilding them, so a template edit can no longer take the purchases recorded against a part-spent envelope with it, and a row holding the owner's own records is retained and reported instead of rewritten. Closed **N-292**. | SHIPPED | `5fc13cdb` | -- |
| recurrence | R7c-c | -- | The closed pattern set DIED: six derived columns and the unwritten `budget.recurrence_month_anchors` dropped, `interval_n` re-pointed off the encoding so a Quarterly rule no longer reads as monthly, and the offer set re-based on the producer that refuses. Closed **D6**, **D32**, **D37**, **D38**, `pay_calendar:P11`; opened **D39**. | SHIPPED | `ee35bca7` | -- |
| recurrence | R7c | -- | The DECOMPOSED parent of THE CUTOVER as an expand / migrate / contract, ticked with R7c-c, its last leaf. | SHIPPED | `ee35bca7` | -- |
| pay_calendar | C2-f1 | -- | Three of `pay_period_service`'s six readers are DELETED across 12 call sites -- `get_overlapping_periods`, `get_next_period` and `get_current_and_future_periods` -- with two copies of one of them, `companion_service.get_previous_period` and `dashboard_pulse_service._next_paycheck_date`. Opened **P45**-**P49**. | SHIPPED | `792e3b21` | -- |
| pay_calendar | C2-e | -- | The projection axis is the OWNER'S paychecks: `growth_engine.generate_projection_periods` and `SyntheticPeriod` are DELETED and every forward projection runs on `PayCalendar.projection_axis`, so a monthly-paid owner stops being credited 26 contributions a year. Closed **P17**, **P20**, **P21**, **P22**, **P23**; re-pointed **P7** to **C2-f**; opened **P40**-**P44**. | SHIPPED | `8143c6fe` | -- |
| pay_calendar | C2-c | -- | Retire `balance_at/_cash_periods._PeriodSpans` so the cash view answers from the one calendar, keeping `None` outside the reported window as a VIEW question. Closes **P14**. | SHIPPED | `b8a72f6c` | -- |
| balance | X-au-c2a | -- | Every reader of the two `effective_amount` model properties moved onto the amount model -- the cheap accessor where a loader filters to settled, the batch resolver where it cannot -- and both properties were DELETED with their 104 test reads. `investment_projection` is valued at its boundary. Closed **N-262**; opened **N-266**-**N-272**. | SHIPPED | `d44a4f01` | -- |
| balance | X-au-c1 | -- | Both amount columns became NULLABLE under the CHECK pairing each with an `amount_source_id` that names WHICH RELATION prices the row, and the at-most-one-pricing-link convention became structural. | SHIPPED | `2dbdad1c` | -- |
| balance | X-f1 | -- | A settle carries the day the money moved; absorbed **S2-b**. Fourteen leaves, condensed into `archive/…2026-08-04.md` 1a. | SHIPPED | `8d812662` | -- |
| balance | X-ad-a | -- | Registration ASKS for the most recent payday, the cadence and the horizon; the bootstrap payday is DELETED. Closed **N-123** (= `pay_calendar:P3`). | SHIPPED | `2a4eb477` | -- |
| balance | X-au-a | -- | A recurring definition's amount became an effective-dated version series with one write door, a read-and-correct panel, and a backfill that reconstructed each price history from the rows already generated. Opened **N-244**, **N-245**, **N-246**, **N-247**. | SHIPPED | `81138fb8` | -- |
| balance | X-au-b | -- | The ONE resolver that answers what a projected row is worth became a total dispatch over the five amount rules, each delegating to the producer that already answers it, proven equal to the app's own answer for all 997 rows on a production clone and shown to ignore the column it replaces. Opened **N-252**, **N-253**, **N-254**. | SHIPPED | `81ad02d1` | -- |
| recurrence | R7a-1 | -- | The Recurrence cell became one function over `(interval, unit)`. Closed **D17**. | SHIPPED | `6fed14af` | -- |
| recurrence | R7a-2a | -- | The paycheck count is DERIVED per owner (`PayCadence`), not a `Decimal("26")` constant nine files read; every conversion takes it as an input. Opened **F-16**, **F-17**. | SHIPPED | `003e3657` | -- |
| recurrence | R-F1 | -- | The lagging `ref` identity sequences are back in step. Closed **F-1**. Held in this index for a `tools/plan_gate` control that NAMED it; `pay_calendar:C2-f3e` made that control derive its specimen and closed **D42**, so it may now be archived under rule 5. | SHIPPED | `44b25ad3` | -- |
| pay_calendar | C1 | -- | The derivation exists and is proven equal to what is stored. Opened **P15**, **P16**. | SHIPPED | `f9d148fe` | -- |
| pay_calendar | C2-a | -- | The one calendar VALUE, with nothing calling it: `PayCalendar`, three named questions, and a window that is a VIEW. Opened **P21**-**P25**. | SHIPPED | `3cb3082f` | -- |
| pay_calendar | C2-b1 | -- | The calendar's last two questions, the cadence rule, and the one DB door. Opened **P28**. | SHIPPED | `90f2fbb7` | pay_calendar:C2-a |
| pay_calendar | C2-d | -- | The filing cutover: both posting writers call the filing rule through one door. Closed **N-169**. | SHIPPED | `3e6cd4ec` | pay_calendar:C2-a |
| pay_calendar | C3 | -- | The writer writes paydays, forward-only: `pay_period_write` is the one place in `app/` that constructs or deletes a pay period. Two leaves, condensed into `historical/pay_calendar_as_built_2026-08-16.md`. Closed **P2**'s writer half, **P12**, **P13**, **P29**, **N-127**. | SHIPPED | `7e3fb33b` | balance:X-ad-a |
| pay_calendar | C2-b2 | -- | The recurrence engine answers from the ONE calendar value; `PeriodCalendar`, `SchedulePeriod` and `RecurrenceScheduleError` are DELETED. Closed **P2** (= recurrence **F-10**) and **P25**; re-pointed **P26**, **P27** and **P28** to **C4**; opened **P34**, **P35**. | SHIPPED | `fe365de1` | pay_calendar:C2-b1 / balance:X-ad-a / pay_calendar:C3 |
| pay_calendar | C2-b | -- | The DECOMPOSED parent of the recurrence cutover, ticked with C2-b2, its last leaf. | SHIPPED | `fe365de1` | pay_calendar:C2-a |
| pay_calendar | C5a | recurrence:R-F10 (ticked by it) | The gap machinery the derivation makes unconstructible is DELETED -- ticked at C2-b2 rather than after C4, because that leaf is what made its subject unreachable (developer 2026-08-11). The 430-shape baseline stayed byte-identical. | SHIPPED | `fe365de1` | -- |
| recurrence | R-F10 | pay_calendar:C5a (ticks it) | The gap machinery goes; the same commit as `pay_calendar:C5a`, under this arc's name. Closed **F-10**. | SHIPPED | `fe365de1` | -- |
| recurrence | R8-a | -- | The offer set stopped being gated on a derivation ruling R-R16 had deleted: `anchor_family` and its three families went, `authorable_cadences` derives from whether a row can be DATED and whether the placement can change it, and a year-scale cadence funded from a later paycheck became authorable. Closed **D20**; opened **D40**. | SHIPPED | `87e2c5b9` | -- |

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
