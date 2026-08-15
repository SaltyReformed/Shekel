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

**The rank is a DECISION, not a derivation.** 41 of these steps are legal to start right now, so the
dependency graph alone cannot say which comes next; the sequence below follows each arc's own stated
sequencing -- the balance README's ten blocks, and each plan's section 0.
**The `starts` column is DERIVED from the blocker keys beside it and the gate reconciles the two**,
so a rank can never contradict a real dependency and a stale `NOW` cannot survive a commit.

**149 steps, 107 open.** The dependency graph holds 100 edges over 66 rows.

## The order

| arc | id | also | what this step does | order | commit | starts |
|---|---|---|---|---|---|---|
| balance | X-f3a-2 | -- | Record that a STATEMENT was walked line by line, so a line it did not show is NOT CLEARED rather than unknown, and widen the offer set to every uncleared line including the already-settled ones, which **MOVES MONEY**; it closes **N-273**, **N-288** and **N-289**. | #4 | -- | after #3 / balance:X-f3c |
| balance | X-f3b | -- | Make a cleared purchase a cash posting, so an envelope's close books only the remainder and ruling R-DH (c)'s invariant stops resting on the anchor reset. Closes **N-274**. | #1 | -- | NOW / balance:X-f3a-1 (shipped) |
| bank_import | X-f6a | -- | Build the statement importer's core -- one normalized line shape behind a source adapter, matched against rows AND purchases, reviewed before it commits -- writing the bank's posted day and the clearing link. **MOVES MONEY.** Closes **N-173**. | #2 | -- | NOW / balance:X-f3a-1 (shipped) |
| balance | X-f3c | -- | THE CUTOVER: the assertion stops resetting the ledger, `balance(T)` becomes opening equity plus the sum of postings, and an unexplained difference becomes a recorded uncategorized transaction the user accepts. **MOVES MONEY, OWN PR, NO BACKLOG.** Closes **N-171**, **N-172**, **N-174**, **N-275**, **N-276**. | #3 | -- | after #2 / balance:X-f3b / bank_import:X-f6a |
| balance | X-f4 | -- | Delete what the cutover orphans: `ReconciledThrough`'s coverage rule, `_anchors.py`, the correction machinery and the R-I seed compensator, a set X-f3a has already narrowed. Closes **N-176**, **N-218**, **N-161**, **N-170**. | #5 | -- | after #3 / balance:X-f3c |
| bank_import | X-f6b | -- | Add the automated SOURCE ADAPTER -- SimpleFIN recommended on the security ground ruling R-FP states -- so a statement arrives without a manual download, deciding first where a scheduled fetch runs in an app with no scheduler. | #6 | -- | after #2 / bank_import:X-f6a |
| balance | X-au-c2b | -- | Route the readers that take a row's BUDGET straight off `estimated_amount` -- the envelope remaining, the carry-forward leftover, the payback amount -- through a batch amount resolver, so no reader still reads a column a derived row will not carry. | #7 | -- | NOW / balance:X-au-c2a (shipped) |
| balance | X-au-c3 | -- | Turn X-aq's settle refresh into the FREEZE and the revert into its inverse: a settle writes the resolved figure into the row's own amount column and declares it owned, and leaving the settled band hands the declaration back. Closes **N-241**, **N-242**, **N-245**, **N-259**. | #8 | -- | NOW / balance:X-au-c2a (shipped) |
| balance | X-au-d | -- | Cut SALARY rows over: generation stops pricing them, the 51 live rows go NULL, and `income_service.live_projected_net`, `_freshest_amount` and `_reconcile_cached_amount` are deleted. Closes **N-224**, **N-228**. | #9 | -- | after #8 / balance:X-au-c3 |
| balance | X-au-e | -- | Cut TEMPLATE rows over to their template's amount series: generation stops pricing them, the 511 non-override rows go NULL, and regeneration's amount arm with the conflict chooser's keep-vs-use decision are both deleted. | #10 | -- | after #8 / balance:X-au-c3 / balance:X-au-a (shipped) |
| balance | X-au-g | -- | Cut LOAN-PAYMENT shadows over, which must first RULE that a shadow's P&I resolves on its own due date as ruling D5 already put its escrow, because a resolver may not read the wall clock. Closes **N-40**. | #11 | -- | after #8 / balance:X-au-c3 |
| balance | X-au-f | -- | Cut TRANSFER rows and their shadows over, deleting the amount copy in `update_transfer` and the drift corrector beside it, which makes Transfer Invariant 3 structural rather than maintained. | #12 | -- | after #8 / balance:X-au-c3 |
| balance | X-au-i | -- | Cut the CC PAYBACK kind over, the sixth amount rule: a payback's figure is the credit entries it repays, so `sync_entry_payback`'s unconditional rewrite and `create_cc_payback_transaction`'s unrepaired copy both become one derivation. Closes **N-243**, **N-252**. | #13 | -- | after #8 / balance:X-au-c3 |
| balance | X-au-h | -- | Split the FOUR facts `is_override` carries, of which two were found by adversarial review: re-priced, moved, survives the regeneration sweep, and exempt from the partial unique index. Closes **N-238**. | #14 | -- | after #12 / balance:X-au-d / balance:X-au-e / balance:X-au-f / balance:X-au-g |
| pay_calendar | C2-f2d | -- | Move `/savings` and `/retirement` together, since both run through `retirement_projection.load_projection_batch` -- which stops building a SECOND `BalanceContext` with its own clock read. Closes **P43**. | #15 | -- | NOW |
| pay_calendar | C2-f2e | -- | Move the budget dashboard and `/accounts/<id>` detail, the last two `BalanceContext`-holding surfaces, onto the pass's calendar. | #16 | -- | NOW |
| pay_calendar | C2-f3 | -- | Point the readers at every remaining surface at one `calendar_for` load per producer, move `pay_period_admin`'s three write-path ORM-row reads into `pay_period_write`, and delete `get_current_period` and `get_all_periods`. Closes **P19**, **P45**, **P49**. | #17 | -- | after #16 / pay_calendar:C2-f2 |
| pay_calendar | C4 | -- | Drop `pay_periods.end_date` and `period_index` with their three constraints, once the ORM readers take their bounds from the calendar. Closes **P1**, **P4**, **P5**, **P8**, **P9**. | #18 | -- | after #17 / pay_calendar:C2 (the container ticks at #17) / pay_calendar:C3 (shipped) |
| pay_calendar | C7 | -- | Rule and then fix `journal_entries.pay_period_id`, a NOT NULL FK stored beside the `entry_date` it derives from, which is P1's defect on the ledger's header table. Closes **P18**. | #19 | -- | after #18 / pay_calendar:C4 |
| pay_calendar | C9 | -- | Project the modelled fold's CONTRIBUTION tier past the pay-period horizon on the same axis the accrual tier already runs on, superseding balance ruling **R-AG**, which let the half model stand before a total calendar existed. **MOVES MONEY.** Closes **P7**; carries **P42**, **P44** and **P50**. | #21 | -- | NOW / pay_calendar:C2-e (shipped) |
| pay_calendar | C8 | -- | Give the forward forecast cadence its own control, separating it from the payday forms it is currently welded onto. Closes **P30**. | #20 | -- | after #18 / pay_calendar:C4 |
| recurrence | R7c-b | -- | Move every reader and the recurrence form onto the two-axis columns: `RecurrenceSpec` states one `starts_on`, the form's Day of Month and Month controls are deleted in favour of that one date, the four columns tighten to NOT NULL, and `end_date >= starts_on` lands with the mirror that refuses it at the door. Closes **D10**, **D21**, **D24**, **D28**, **D31**. | #22 | -- | NOW / recurrence:R7c-a (shipped) |
| recurrence | R7c-c | -- | Drop what the cutover orphans -- `pattern_id`, `day_of_month`, `month_of_year`, `start_period_id`, `offset_periods`, `start_date` and the unwritten `budget.recurrence_month_anchors` -- re-point `interval_n` off the encoding, and delete the encode / decode pair with the closed pattern set behind it. Closes **D6**, **D32**. | #23 | -- | after #22 / recurrence:R7c-b |
| recurrence | R8 | -- | Add the four ruled add-ons: the WEEK unit, `recurrence_weekday_anchors`, the business-day shift and the count-bounded end. | #24 | -- | after #23 / recurrence:R7a-2b / recurrence:R7c |
| recurrence | R9 | -- | Drop the `ref.recurrence_patterns` table and `pay_period_admin._repoint_recurrence_rules`, after re-checking the two premises ledger row **D6** names. | #25 | -- | after #23 / recurrence:R7c |
| pay_calendar | C6 | -- | Let a payday be inserted mid-schedule, refusing only where `classify_period_lock` says the split period is locked. Starts with the two rulings section 3 names. Closes **P10**. | #26 | -- | after #23 / pay_calendar:C4 / recurrence:R7c |
| recurrence | R5 | -- | Split a generated row's dates into three -- `occurs_on` (the cadence), `pay_period_id` (the funding) and `due_on` (the installment) -- and delete `compute_due_date`. A value-splitting migration; own PR. | #27 | -- | after #5 / balance:X-f4 (R5 edits `cash_ledger/_events.py`, which is inside that step's deletion set) |
| recurrence | R6 | -- | Delete `loan_params.payment_day` and collapse eight producers of "when is this installment due" into one `loan_installment_date` accessor. Kills **D4**; needs its own review pass. | #28 | -- | after #27 / recurrence:R5 (it READS `due_on`; "ships WITH balance:X-an" was unsatisfiable -- see section 0) |
| pay_calendar | C5b | -- | Make `should_skip_period` occurrence-aware so a paycheck may owe one template more than once, retiring `refuse_unstorable_repeats`. Needs a migration re-keying two partial unique indexes. Closes **P16**. | #29 | -- | after #27 / recurrence:R5 (it consumes the `occurs_on` column R5 creates) |
| balance | X-k | -- | Reconcile `RecurrenceRule.end_date` against what was actually generated, and give the write door a consistent batch contract. Closes **N-18**, **N-19**, **N-23**, **N-24**. | #30 | -- | after #27 / recurrence:R5 (R5 deletes `compute_due_date` and re-keys the generation index; rebasing this over that is the cheaper direction) |
| balance | X-ad-b | -- | Stop the rolling top-up manufacturing history: an automatic writer creates nothing on a lapsed schedule, and the lapse is surfaced instead. Closes **N-124**. | #31 | -- | NOW |
| balance | X-x1 | -- | Build the ONE answer to "no pay period covers this date": `PayCalendarGapError`, `require_current_period` / `covers`, one handler and its repair page, taking the grid's two pre-checks as its first callers. | #32 | -- | NOW |
| balance | X-x2 | -- | Move the branches that publish a figure the app did not compute onto the raising accessor: the fabricated $0.00 in four producers, and `build_trend_periods`' `current_index = 0` into an empty list. | #33 | -- | after #32 / balance:X-x1 |
| balance | X-x3 | -- | Make `onboarding.has_periods` ask "does a period cover today" rather than "do any periods exist", so the checklist and the page it renders on cannot disagree. | #34 | -- | NOW |
| balance | X-x4 | -- | Stop answering an empty requested WINDOW with the absence card, and stop the card's copy naming two states. | #35 | -- | NOW |
| balance | X-x5 | -- | Delete `verify_savings_producers.py`'s dict-or-attribute `_get` reader and the two-spelling readers its own docstring says this step deletes. | #36 | -- | NOW |
| balance | X-y | -- | Move the fifteen surfaces that resolve the baseline scenario directly onto the seam, deciding what a WRITE may do without one. Closes **N-117**. | #37 | -- | after #36 / balance:X-x (the container ticks at #35) |
| balance | X-am | -- | Decide whether the `Settled` status carries a meaning worth keeping -- 0 rows on both production tables, no writer, one line of the transition map -- and delete it if not. Closes **N-177**. | #38 | -- | NOW |
| balance | X-aj2 | -- | Make the status write door STRUCTURAL and DELETE the W9907 checker, ruling what a row may be BORN as. Closes **N-149**, **N-151**, **N-185**, **N-188**. | #39 | -- | NOW |
| balance | X-ak | -- | Rule the stored transfer-to-shadow copy first, then unify the THREE mirror implementations that already disagree. Closes **N-148**, **N-150**, **N-152**, **N-156**, **N-159**, **N-170**. | #40 | -- | after #39 / balance:X-aj (the container ticks at #38) |
| balance | X-ai-a | -- | Build the cash re-derive VERB from R-DV's sentence, with the union source set, the returned touched pairs and the per-account advisory lock in it from the start. Carries **N-162**, **N-165**, **N-166**. | #41 | -- | NOW |
| balance | X-ai-b | -- | Build the trigger: the commit-boundary grader, drained from the registry the writers populate. | #42 | -- | NOW |
| balance | X-ai-c | -- | Move the loan side onto the same re-derive verb. | #43 | -- | NOW |
| balance | X-ai-g | -- | Classify each of the 20 bulk-statement sites as unable to touch a posted row, routed through a writer, or named in the one docstring that states what the grader cannot see. Closes **N-163**. | #44 | -- | NOW |
| balance | X-ai-s | -- | Migrate `journal_entries` to an EXCLUSIVE ARC of typed FKs with an AT-MOST-ONE check, plus the reversal linkage. Held until the cutover, which deletes the correction family it would buy attribution for. Closes **N-167**. | #45 | -- | after #3 / balance:X-f3c |
| balance | X-d | -- | Make the posted account ledger a CHECKED PROJECTION: the posting writer consumes the walk instead of its own, with a per-visible-date assert. Carries **N-135**. | #46 | -- | after #45 / balance:X-ai (the container ticks at #44; N-155 parked this step and the assert's placement is the restructure's) |
| balance | X-i1 | -- | Give `BalanceContext` the FOUR remaining inputs of the tier the loan derivations already have -- the contribution feed, the override map, the standing extra, the contractual schedule -- since `pay_calendar:C2-c` took the calendar early, as a method beside `loan_walk` rather than through `_memoize_once`. Additive and byte-identical. | #47 | -- | NOW |
| balance | X-i2 | -- | Hand each memoized loader `ctx.as_of` and `ctx.scenario`, so one read pass has one clock. **MOVES MONEY** ($3,631.74 today against $3,722.53 at a 2027 read). Closes **FU-3**, **N-14**, **N-56**, **N-72**, **N-89**, **N-91**, **N-92**, **N-93**, **N-115**. | #48 | -- | after #47 / balance:X-i1 |
| balance | X-j | -- | Rule which producer answers "what is this account worth" for a given surface, or render the row that explains the difference. Closes **N-87**, **N-90**, and **N-83**'s display half. | #49 | -- | after #48 / balance:X-i2 (X-j moves three surfaces onto the modelled view whose contribution load X-i2 fixes, so the other order ships a regression and then removes it) |
| balance | X-m | -- | Make `growth_engine.project_balance` take its AXIS rather than boundaries its caller must compute to match; a caller that gets it wrong costs $1,000.00 of annual-limit room per period. Closes **N-86**. | #50 | -- | NOW |
| balance | X-n | -- | Stop `_redistribute_to_distinct_months` OVERWRITING the real installment a payment satisfies when it shifts a colliding due date. Closes **N-36**. | #51 | -- | NOW |
| balance | X-e | -- | Re-take the census the column deletions made historical, then resolve what remains: two callerless public seam entries and a falsified de-duplication rationale. Carries **N-96**, **N-85**, **N-180**. | #52 | -- | NOW |
| balance | X-ab | -- | Give the posting path ONE asset-vs-liability rule instead of asking X-z's question a second time, and decide what a re-class does to accounts already carrying postings. Closes **N-122**. | #53 | -- | NOW |
| balance | X-ac | -- | Stop the cockpit reducing `_sum_liquid_balances` twice per render and publishing the answer under two context keys. Closes **N-121**. | #54 | -- | NOW |
| balance | X-ao | -- | Grade the arc documents' RULINGS tables, which no gate parses today, so a ruling id resolves to exactly one ruling and that ruling states a rule. Closes **N-217**, **N-220**. | #55 | -- | NOW |
| balance | X-ag | -- | Build the instrument that refuses lax digit acceptance, shown FIRING on a planted defect; both earlier method-name designs were refuted by measurement. Closes **N-139**. | #56 | -- | NOW |
| balance | X-ah | -- | Rule each of the 42 `request.args.get(..., type=int)` sites and parse them like every other id, with a second rule that admits a meaningful zero. Closes **N-142**. | #57 | -- | NOW |
| balance | X-al | -- | Census the fifteen live `duplicate-code` disables, none of them re-measured, and build the arm that catches a stale one -- `useless-suppression` is blind to them. Closes **N-154**. | #58 | -- | NOW |
| credit_card | CC0a | -- | Add `has_revolving_credit` to account types with the `REVOLVING` projection kind and its classifier branch, behind behavior-preserving shims. | #59 | -- | after #38 / balance:X-f4 / balance:X-am |
| credit_card | CC0b | -- | Add the `budget.credit_card_params` satellite model and its migration, inert by design. | #60 | -- | after #38 / balance:X-f4 / balance:X-am |
| credit_card | CC0c | -- | Add the card params setup flow: create and update routes, the Marshmallow schema, and the REVOLVING setup redirect. | #61 | -- | after #38 / balance:X-f4 / balance:X-am |
| credit_card | CC1a | -- | Make the card consume the shared instant-partition fold core rather than growing a second copy of it. | #62 | -- | after #38 / balance:X-f4 / balance:X-am |
| credit_card | CC1b | -- | Build `balance_at/_revolving.py`: a card is an event stream of anchor facts, settled rows and projected rows, unwired and additive. | #63 | -- | after #38 / balance:X-f3c / balance:X-f4 / balance:X-am |
| credit_card | CC1c | -- | Dispatch REVOLVING to the fold at four seam surfaces, deleting the CC0a shim and explaining every moved number. | #64 | -- | after #38 / balance:X-f4 / balance:X-am |
| credit_card | CC2a | -- | Derive the statement cycle purely: `cycle_window`, `statement_sequence`, `due_date_for`, `statement_balance`, `grace_kept` and `minimum_payment`. | #65 | -- | after #38 / balance:X-f4 / balance:X-am |
| credit_card | CC2b | -- | Fold the daily balance into a finance charge over effective-dated APR segments, with purchases joining the average daily balance on grace loss. | #66 | -- | after #38 / balance:X-f4 / balance:X-am |
| credit_card | CC2c | -- | Ride card APR history on `rate_history` through a card-gated write route, pinning that the loan loaders never see the card. | #67 | -- | after #38 / balance:X-f4 / balance:X-am |
| credit_card | CC3a | -- | Add `charge_to_card` and its undo as an additive action with row locking, guards and provenance, beside the still-live mark-credit. | #68 | -- | after #38 / balance:X-f4 / balance:X-am |
| credit_card | CC3b | -- | Cut mark-credit over to charge-to-card at the transaction level, with an in-migration backfill of every live pair and a provenance-restoring downgrade. | #69 | -- | after #38 / balance:X-f1 (shipped; absorbed the X-f1b leaf this once named) / balance:X-f4 / balance:X-am |
| credit_card | CC3c | -- | Rewrite `entry_credit_workflow.py` as `entry_card_charge.py` for split tender, rename `is_credit` to `is_card_tender`, and delete `credit_workflow.py` whole. | #70 | -- | after #38 / balance:X-f4 / balance:X-am |
| credit_card | CC3d | -- | Refuse what the card cannot model -- transfers OUT of the card -- and give `active_accounts_query` an orthogonal `revolving` filter. | #71 | -- | after #38 / balance:X-f4 / balance:X-am |
| credit_card | CC4a | -- | Add `card_payment_settings` with its payment-mode ref table and the creation flow mirroring the loan payment transfer. | #72 | -- | after #38 / balance:X-f4 / balance:X-am |
| credit_card | CC4b | -- | Derive the projected card payment from the statement balance at last close minus redemptions since, as a CARD rule behind the amount resolver rather than a sixth entry in an override map. | #73 | -- | after #38 / balance:X-f4 / balance:X-am |
| credit_card | CC4c | -- | Warn on underpayment with a one-click "pay statement balance", and maintain ONE projected finance-charge expense when grace fails. | #74 | -- | after #38 / balance:X-f4 / balance:X-am |
| credit_card | CC5a | -- | Accrue rewards as a derived figure over settled purchases minus redemptions, carrying the `system_origin_id` migration. | #75 | -- | after #38 / balance:X-f4 / balance:X-am |
| credit_card | CC5b | -- | Add manual redemptions and the auto-redeem threshold, holding the one-live-row invariant under the concurrency shape. | #76 | -- | after #38 / balance:X-f4 / balance:X-am |
| balance | X-p | -- | Put the analytics calendar's day chips and its balance line on one clock, or render the row that explains the gap. Closes **N-58**, **N-97**. | #77 | -- | after #5 / balance:X-f (the container ticks at #5; the import shrinks the date noise at its source, so ruling before it decides against numbers that then change) |
| recurrence | R-F6 | -- | Close the recurrence-rule leak, then delete the orphaned rules it made. Closes **F-6**. | #78 | -- | NOW |
| balance | E2-0 | -- | Trace the super-package membership from the code: which modules are members, what the public re-export surface is, and whether any member imports a non-member. Expect it to DECOMPOSE. Carries **N-33**, **N-35**. | #79 | -- | NOW |
| balance | E2-n | -- | Make the move and delete the registry, with `_FENCED_MODULE_RULINGS` as the LAST commit rather than the first. Its decomposition is decided from #78's trace. | #80 | -- | NOW |
| balance | G1 | -- | Trace each allowlist entry to its real cause, then stop the ledger-model and balance-seam fences carrying name lists. Closes **N-147**. | #81 | -- | NOW |
| balance | G2 | -- | Build the `Money` and `DisplayLabel` value types that retire W9901, W9904 and W9902, taking the small label half first. Phase G runs INSIDE E2 by ruling R-DQ. | #82 | -- | NOW |
| balance | X-at | -- | Surface which tax year a figure was actually computed against, and give a new year's bracket set a write door, so the resolver's substitution stops being invisible and the settings screen can finish the year it starts. Closes **N-235**, **N-236**. | #83 | -- | NOW |
| balance | X-av | -- | Give the base annual salary an effective date so the app can tell a raise from a correction, which `apply_raises` already does for every raise but cannot do for the figure they are applied to. Closes **N-237**. | #84 | -- | NOW |
| balance | X-aw | -- | Count a calendar year's paydays from the stored cadence rather than from whichever periods happen to have been generated, so a period's gross stops moving by a cent when the schedule extends. Closes **N-239**. | #85 | -- | NOW |
| balance | X-ax | -- | Reconcile a carry-forward rollover against its source: when a settled source's spend changes, the target row's top-up moves by the same amount so the two always sum to the budget the rollover divided. **MOVES MONEY.** Closes **N-249**. | #86 | -- | NOW |
| balance | X-ay | -- | Post a modelled account's own CONTRIBUTIONS and ACCRUALS, so its balance-assertion residual is what the account earned rather than what it earned plus what was paid into it. Closes **N-277**. | #87 | -- | NOW |
| recurrence | R-F16 | -- | Give the paycheck engine's divisor and the monthly-equivalent conversions ONE producer, deleting `salary_profiles.pay_periods_per_year` -- which must first RULE what semi-monthly pay means when no `cadence_days` can express the 1st and the 15th. **MOVES MONEY**. Closes **F-16**. | #88 | -- | NOW / recurrence:R7a-2a (shipped) |
| recurrence | R-F17 | -- | Derive the two period-INDEX horizon windows from the owner's cadence instead of a hardcoded 26, deciding first what a fractional period offset means. Closes **F-17**. | #89 | -- | NOW / recurrence:R7a-2a (shipped) |

## Containers

**A container is a DECOMPOSED parent: a name for a group of steps, never a thing you do.** It ticks
when the last of its leaves ships. It is listed here rather than in the order so that every row of
the order is workable.

| arc | id | also | what this step does | order | commit | starts |
|---|---|---|---|---|---|---|
| balance | X-f | -- | The DECOMPOSED parent of the whole "the app records when money moved" cluster, carrying **N-42**. | container | -- | ticks with #5 |
| balance | X-au | -- | The DECOMPOSED parent of the amount model (**R-FI**): a row's amount is either its OWN or DERIVED, and a derived amount is not stored at all. Supersedes **X-ar**. Carries **N-40**, **N-224**, **N-228**, **N-238**. | container | -- | ticks with #14 |
| balance | X-au-c | -- | The DECOMPOSED parent of the amount model's SEAM, split into three leaves 2026-08-12: the schema and the declaration, the readers, then the freeze and its inverse. | container | -- | ticks with #8 |
| balance | X-ai | -- | The DECOMPOSED parent of the posted-ledger restructure: one verb and one trigger on both ledgers (**R-DU**, **R-DV**). Carries **N-144**, **N-153**, **N-155**, **N-157**, **N-158**, **N-160**, **N-164**. | container | -- | ticks with #45 |
| balance | X-aj | -- | The DECOMPOSED parent of the one-status-seam work; its merge half shipped as X-aj1. Carries **N-145**. | container | -- | ticks with #39 |
| balance | X-x | -- | The DECOMPOSED parent of the one-pay-calendar-precondition work. Closes **N-116**, **N-125**, **N-126**, **N-129**. | container | -- | ticks with #36 / balance:X-ad-a (shipped) / pay_calendar:C3 (shipped; ruling R-EY 2026-08-10 moved N-127 there, which ended the "X-ad then X-x, ONE PR" pairing) |
| balance | X-ad | -- | The DECOMPOSED parent of the pay calendar a new user can actually enter, split 2026-08-10 (**R-EZ**) into the door that CREATES a calendar and the door that GROWS it. | container | -- | ticks with #31 |
| balance | X-i | -- | The DECOMPOSED parent of the one-read-pass work: nine ledger rows with one root cause. | container | -- | ticks with #48 |
| balance | X-l | pay_calendar:C2 / recurrence:R-F12 | The DECOMPOSED parent under its BALANCE name of "the pay calendar answers any date". Closes **N-82**, **N-128**, and **N-79**'s far half. | container | -- | ticks with #17 |
| pay_calendar | C2 | balance:X-l / recurrence:R-F12 | The DECOMPOSED parent under its PAY-CALENDAR name: one calendar value answers every "which period" question, RULED on three forks 2026-08-10. | container | -- | ticks with #17 |
| recurrence | R-F12 | pay_calendar:C2 / balance:X-l | The DECOMPOSED parent under its RECURRENCE name: one `PeriodCalendar`, not three period-containing searches. Closes **F-12**. | container | -- | ticks with #17 |
| pay_calendar | C2-f | -- | The DECOMPOSED parent of `pay_period_service`'s six `get_*` readers over their 60 `app/` call sites, split by READER 2026-08-14 because 11 functions pair `get_current_period` with `get_all_periods` and separating those two leaves a context object holding one of each. | container | -- | ticks with #17 |
| pay_calendar | C2-f2 | -- | The DECOMPOSED parent of the readers at a surface that already holds a read pass, split into five leaves by PACKAGE 2026-08-14 because the one step spans 23 `app/` modules and 12 templates. Closes **P36**. | container | -- | ticks with #16 / pay_calendar:C2-a (shipped) / balance:X-ad-a (shipped) / pay_calendar:C3 (shipped) |
| pay_calendar | C5 | -- | The DECOMPOSED parent of "the gap machinery goes, and a paycheck may owe one template twice", split 2026-08-09. | container | -- | ticks with #29 / pay_calendar:C4 / recurrence:R5 |
| balance | X-f3 | -- | The DECOMPOSED parent of THE CUTOVER, re-decomposed 2026-08-13 when measurement refuted two of ruling R-EB's premises. Carries **N-171**, **N-172**, **N-174**. | container | -- | ticks with #4 |
| balance | X-f3a | -- | The DECOMPOSED parent of "clearing is a recorded fact", split 2026-08-14 (**R-FQ**) into the RECORDING half, which moves no money, and the half that makes a walked statement's SILENCE mean something, which cannot precede the cutover. Carries **N-273**. | container | -- | ticks with #4 |
| bank_import | X-f6 | -- | The DECOMPOSED parent of the statement importer, whose first leaf moved AHEAD of the cutover because what the cutover needed was clearing facts rather than an import surface. Carries **N-173**. | container | -- | ticks with #6 |
| recurrence | R7c | -- | The DECOMPOSED parent of THE CUTOVER, split into three leaves 2026-08-14 (R-R18) as an expand / migrate / contract: the columns land, the readers move, then the closed set dies. | container | -- | ticks with #23 |

## Shipped

**One line each, and the COMMIT is the record.** Read the code it shipped, not a paragraph about it.
The fuller as-built entries are in each arc's archive.

| arc | id | also | what this step does | order | commit | starts |
|---|---|---|---|---|---|---|
| recurrence | R7c-a | -- | The two-axis columns land NULLABLE and backfilled -- `unit_id`, `placement_id`, `shift_id`, `starts_on` and `nominal_day` -- with the write door keeping them in step from the same `resolve` call, and nothing reading them yet. Closed **D12**. | SHIPPED | `370a30cc` | -- |
| recurrence | R-F2 | -- | The ref-seed parity scan bounds a statement where the SQL does -- at the Python string literal carrying it, with the keyword list as the inner rule -- and four negative controls were shown to fire against the reader it replaces. Closed **F-2**. | SHIPPED | `672c18b1` | -- |
| recurrence | R-F3 | -- | A `ref` lookup table's single-column PK and UNIQUE take PostgreSQL's generated names, stated in both places the constraint-naming rule lives; measured 24 of 24 live `ref` tables. Closed **F-3**. | SHIPPED | `e37b736c` | -- |
| recurrence | R-F7 | -- | `_first_of_month_anchor` loses two provably dead guards, one of which commented a case that cannot execute; the 430-shape baseline stayed byte-identical. Closed **D11**. | SHIPPED | `5ac7ab4d` | -- |
| recurrence | R-F13 | -- | A baseline REGENERATION run can no longer report success, so a skip cannot read as a pass. Its other two holes no longer exist: `PlacementOutcome` and the `OccurrencePlacement` invariant died at `pay_calendar:C2-b2`. Closed **F-13**. | SHIPPED | `b97ec1c3` | -- |
| balance | X-f3a-1 | -- | Clearing became a RECORDED FACT: a transaction and a purchase each name the `account_anchor_history` row whose statement showed them, under composite keys over the account, and one rule (`cash_ledger.StatementCoverage`) replaced `ReconciledThrough.covers` for every cash consumer. Balance-neutral with nothing backfilled. Opened **N-285**-**N-289**. | SHIPPED | `d6d9692c` | -- |
| balance | X-f3d | -- | A balance assertion's counter leg NAMES what the difference was: a total dispatch over `classify_account` sends an `INTEREST` true-up to per-account Interest Income and an `INVESTMENT` / `APPRECIATING` one to per-account Change in Value in a sixth reporting class, while every OPENING stays on `anchor_equity`. Closed **N-276**; opened **N-277**, **N-278**. | SHIPPED | `98ea657d` | -- |
| pay_calendar | C2-f2c | -- | No module under `app/services/investment_dashboard_service/` imports `pay_period_service`: all five reads answer from the read pass's own calendar and clock, and `_build_chart_markers`' scan retired into `PeriodWindow.containing_index`. A contribution carries the PAYDAY its boundary resolves, which let the period list leave three SHARED signatures. Closed **P48**; opened **P52**-**P54**. | SHIPPED | `d4621147` | -- |
| pay_calendar | C2-f2b | -- | The GRID answers every "which paycheck" question from the read pass's own calendar, so `get_periods_in_range` was DELETED whole -- all three of its `app/` call sites were this route's -- and the COMPANION moved with it, the two sharing one partial. Carried **P36**'s grid half. | SHIPPED | `f4d4abe6` | -- |
| pay_calendar | C2-f2a | -- | No module under `app/services/balance_at/` IMPORTS `pay_period_service` any more: the modelled CONTRIBUTION tier takes the read pass's own `PayCalendar` through a required `_asset_fold.resolve` parameter and derives its axis from it, and the sort at its door became the derivation's own ordering. Closed **P37**. | SHIPPED | `dd5c48a5` | -- |
| pay_calendar | C2-f1 | -- | Three of `pay_period_service`'s six readers are DELETED across 12 call sites -- `get_overlapping_periods`, `get_next_period` and `get_current_and_future_periods` -- with two copies of one of them, `companion_service.get_previous_period` and `dashboard_pulse_service._next_paycheck_date`. Opened **P45**-**P49**. | SHIPPED | `792e3b21` | -- |
| pay_calendar | C2-e | -- | The projection axis is the OWNER'S paychecks: `growth_engine.generate_projection_periods` and `SyntheticPeriod` are DELETED and every forward projection runs on `PayCalendar.projection_axis`, so a monthly-paid owner stops being credited 26 contributions a year. Closed **P17**, **P20**, **P21**, **P22**, **P23**; re-pointed **P7** to **C2-f**; opened **P40**-**P44**. | SHIPPED | `8143c6fe` | -- |
| pay_calendar | C2-c | -- | Retire `balance_at/_cash_periods._PeriodSpans` so the cash view answers from the one calendar, keeping `None` outside the reported window as a VIEW question. Closes **P14**. | SHIPPED | `b8a72f6c` | -- |
| balance | X-au-c2a | -- | Every reader of the two `effective_amount` model properties moved onto the amount model -- the cheap accessor where a loader filters to settled, the batch resolver where it cannot -- and both properties were DELETED with their 104 test reads. `investment_projection` is valued at its boundary. Closed **N-262**; opened **N-266**-**N-272**. | SHIPPED | `d44a4f01` | -- |
| balance | X-au-c1 | -- | Both amount columns became NULLABLE under the CHECK pairing each with an `amount_source_id` that names WHICH RELATION prices the row, and the at-most-one-pricing-link convention became structural. | SHIPPED | `2dbdad1c` | -- |
| balance | X-f1 | -- | A settle carries the day the money moved; absorbed **S2-b**. Fourteen leaves, condensed into `archive/…2026-08-04.md` 1a. | SHIPPED | `8d812662` | -- |
| balance | X-ad-a | -- | Registration ASKS for the most recent payday, the cadence and the horizon; the bootstrap payday is DELETED. Closed **N-123** (= `pay_calendar:P3`). | SHIPPED | `2a4eb477` | -- |
| balance | X-au-a | -- | A recurring definition's amount became an effective-dated version series with one write door, a read-and-correct panel, and a backfill that reconstructed each price history from the rows already generated. Opened **N-244**, **N-245**, **N-246**, **N-247**. | SHIPPED | `81138fb8` | -- |
| balance | X-au-b | -- | The ONE resolver that answers what a projected row is worth became a total dispatch over the five amount rules, each delegating to the producer that already answers it, proven equal to the app's own answer for all 997 rows on a production clone and shown to ignore the column it replaces. Opened **N-252**, **N-253**, **N-254**. | SHIPPED | `81ad02d1` | -- |
| recurrence | R1-R3 | -- | Oracle, vocabulary, subtypes, write door, `Once` retired, forward engine. Archived to `historical/recurrence_as_built_2026-08-05.md`. | SHIPPED | `4b5c577b` | -- |
| recurrence | R4a | -- | The forward cutover, three commits, archived to `historical/recurrence_as_built_2026-08-08.md`. Closed **D3**, **D5**, **D22**, **D25**, **D7**. | SHIPPED | `1836a928` | -- |
| recurrence | R7a-1 | -- | The Recurrence cell became one function over `(interval, unit)`. Closed **D17**. | SHIPPED | `6fed14af` | -- |
| recurrence | R7a-2a | -- | The paycheck count is DERIVED per owner (`PayCadence`), not a `Decimal("26")` constant nine files read; every conversion takes it as an input. Opened **F-16**, **F-17**. | SHIPPED | `003e3657` | -- |
| recurrence | R7a-2b | -- | The monthly equivalent became ONE expression over `(interval_n, unit)` and the infrequent badge derives from the same pair; `amount_to_monthly` and the unmodelled-pattern `None` arm are deleted. | SHIPPED | `7c417b90` | -- |
| recurrence | R7b-1 | -- | The AUTHORED vocabulary became the two axes and the closed pattern set became a storage encoding with one inverted table behind it. | SHIPPED | `e7eb3b1a` | -- |
| recurrence | R7b-2 | -- | The form AUTHORS the two axes and the offer set is the encoder's own table inverted, so an unstorable cadence is unofferable rather than fenced. Closed **D8**; opened **D31**, **D32**. | SHIPPED | `ecc4d01b` | -- |
| recurrence | R7b-3 | -- | The closing bound became ONE value with THREE shapes above the columns, so a rule cannot state two; `max_occurrences` gained its first writer as one "Ends" control. Closed **D23**; opened **D33** (closed at R-D33) and three findings carried on R7b-4 and R7c. | SHIPPED | `c8655584` | -- |
| recurrence | R7b-4 | -- | The opening bound became a DATE: "First paycheck" is "Starts on", `start_period_id` folded into `start_date` under a MAXIMUM that writes only the term deciding it, and the `Every N Periods` PHASE became a derivation of that bound rather than a stored value. Closed **D2**, **D30**. | SHIPPED | `67f013c8` | -- |
| recurrence | R-D33 | -- | Both closing bounds answer "is this still a commitment" from whether the rule still OWES an occurrence, so a DATE bound stops counting at its last occurrence rather than at its bound date. Closes **D33**. | SHIPPED | `dd2a5a34` | -- |
| recurrence | R-F1 | -- | The lagging `ref` identity sequences are back in step. Closed **F-1**. | SHIPPED | `44b25ad3` | -- |
| recurrence | R-F8 | -- | The deploy's safety net stops lying: back up unconditionally, pre-flight the rollback, and refuse the one that cannot work. Closed **F-8**, **F-14**. | SHIPPED | `2e63e4f9` | -- |
| pay_calendar | C1 | -- | The derivation exists and is proven equal to what is stored. Opened **P15**, **P16**. | SHIPPED | `f9d148fe` | -- |
| pay_calendar | C2-a | -- | The one calendar VALUE, with nothing calling it: `PayCalendar`, three named questions, and a window that is a VIEW. Opened **P21**-**P25**. | SHIPPED | `3cb3082f` | -- |
| pay_calendar | C2-b1 | -- | The calendar's last two questions, the cadence rule, and the one DB door. Opened **P28**. | SHIPPED | `90f2fbb7` | pay_calendar:C2-a |
| pay_calendar | C2-d | -- | The filing cutover: both posting writers call the filing rule through one door. Closed **N-169**. | SHIPPED | `3e6cd4ec` | pay_calendar:C2-a |
| pay_calendar | C3 | -- | The writer writes paydays, forward-only. The DECOMPOSED parent, ticked with C3-b. | SHIPPED | `7e3fb33b` | balance:X-ad-a |
| pay_calendar | C3-a | -- | The destructive form stops keying on an ordinal; the tail is selected by PAYDAY. Closed **P13**; opened **P29**, **P30**. | SHIPPED | `5f1e2bd6` | balance:X-ad-a |
| pay_calendar | C3-b | -- | `pay_period_write` is the one place in `app/` that constructs or deletes a pay period, materialising `derive_periods` on every write. Closed **P2**'s writer half, **P12**, **P29**, **N-127**; opened **P31**, **P32**; found **P33**. | SHIPPED | `7e3fb33b` | pay_calendar:C3-a |
| pay_calendar | C2-b2 | -- | The recurrence engine answers from the ONE calendar value; `PeriodCalendar`, `SchedulePeriod` and `RecurrenceScheduleError` are DELETED. Closed **P2** (= recurrence **F-10**) and **P25**; re-pointed **P26**, **P27** and **P28** to **C4**; opened **P34**, **P35**. | SHIPPED | `fe365de1` | pay_calendar:C2-b1 / balance:X-ad-a / pay_calendar:C3 |
| pay_calendar | C2-b | -- | The DECOMPOSED parent of the recurrence cutover, ticked with C2-b2, its last leaf. | SHIPPED | `fe365de1` | pay_calendar:C2-a |
| pay_calendar | C5a | recurrence:R-F10 (ticked by it) | The gap machinery the derivation makes unconstructible is DELETED -- ticked at C2-b2 rather than after C4, because that leaf is what made its subject unreachable (developer 2026-08-11). The 430-shape baseline stayed byte-identical. | SHIPPED | `fe365de1` | -- |
| recurrence | R-F10 | pay_calendar:C5a (ticks it) | The gap machinery goes; the same commit as `pay_calendar:C5a`, under this arc's name. Closed **F-10**. | SHIPPED | `fe365de1` | -- |

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
