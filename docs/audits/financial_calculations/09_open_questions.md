# Open Questions

Questions surfaced during the audit that the developer must answer before the corresponding finding
can be classified. Phases write into this file as questions arise; later sessions consult it.

## Candidate behavioral expectations needing developer confirmation

(P0-b populates this section.)

Q-01: Is the canonical rounding rule for monetary calculations
`Decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)`?

Why it seemed natural to assume: the audit plan's inventory step (section 1.1) and required-grep
list (section 13) both name `ROUND_HALF_UP` and `quantize` as expected patterns to find in the code,
and the schema rule `NUMERIC(12,2)` fixes storage precision at two decimal places, so a single
rounding mode is strongly implied for any computation that produces a stored or displayed money
value.

Why no explicit source could be found:
`grep -inE "round_half|quantize|two decimal|2 decimal|cent" CLAUDE.md docs/coding-standards.md docs/testing-standards.md`
returns no matches; the standards documents fix the storage type but not the rounding mode used by
calculation code.

Question for the developer: should every monetary calculation that produces a stored or displayed
value quantize to two decimal places using `ROUND_HALF_UP`, and if so, do you want this added to
section 0.3 as a behavioral expectation (with citation to your answer) before P0-c starts the
plan-vs-code watchlist?

A-01 (developer, 2026-05-13): Yes. The canonical rounding rule for every monetary calculation that
produces a stored or displayed value is `Decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)`.
This rule is not currently documented in `CLAUDE.md`, `docs/coding-standards.md`, or
`docs/testing-standards.md`, but it is the established convention and the audit should treat it as a
behavioral expectation (Phase 0.3 addition). Any computation that produces a money value at a system
boundary (storage, display) without quantizing to two decimal places using ROUND_HALF_UP is a
finding.

A-01 verification (auditor, 2026-05-15): PARTIALLY ACCURATE. The rule IS the established convention
(109 `ROUND_HALF_UP` occurrences across 19 files; every service that names a rounding mode uses it;
rule absent from `CLAUDE.md` and the two standards documents as claimed). The "every monetary
boundary" framing is not literally true. Concrete violations:

- 24 monetary `.quantize()` calls omit `rounding=ROUND_HALF_UP` (defaults to `ROUND_HALF_EVEN`,
  banker's rounding):
  `app/services/investment_projection.py:93, 96, 159`;
  `app/services/savings_dashboard_service.py:266, 872, 873`;
  `app/services/retirement_dashboard_service.py:197, 211, 214, 240, 390`;
  `app/routes/investment.py:131, 223, 226, 319, 458, 535, 538, 580, 585, 586, 589, 670`;
  `app/routes/loan.py:968`.
- 1 intentional `ROUND_CEILING` on a money value: `app/services/savings_goal_service.py:462-463`
  in `_compute_required_monthly` so the user contributes "at least enough" (documented in the
  function's docstring at line 438).
- 3 Jinja templates do arithmetic on money (also violates `docs/coding-standards.md` "Templates
  are for display, not computation"): `app/templates/loan/_schedule.html:55`,
  `app/templates/loan/_payoff_results.html:72`, `app/templates/loan/_escrow_list.html:37`.
- 1 JS file does monetary arithmetic: `app/static/js/retirement_gap_chart.js:24-25`.
- No centralized `round_money()` helper exists; every service redeclares `TWO_PLACES =
  Decimal("0.01")` locally. This is the substrate for the drift.

Recommendation: either accept A-01 as the canonical rule (with the 24+5 violations as Phase 3
findings) or amend A-01 to acknowledge them explicitly so Phase 3 starts with a documented list. The
absence of a centralized money-rounding helper is a candidate Phase 6 DRY finding. See
`answer_verification.md` Section 1 (A-01) for full evidence.

## Cross-plan contradictions to adjudicate

Questions surfaced by P0-c when comparing watchlist entries across plans. Each maps to a `C-NN`
entry in section 0.5 of `00_priors.md`. The developer answers; Phase 3 then has a single source of
truth to compare the code against.

Q-02 (maps to C-01): Carry-forward envelope semantics. The plans `carry_fwd_design` (Option F) and
`carry_fwd_impl` (its execution) settle the envelope source row in place (status DONE/RECEIVED,
`actual_amount = entries_sum`, `pay_period_id` unchanged) and bump the target canonical's estimate;
`envelope_view` keeps the source row moving (post-33cd21e behavior) and groups canonical plus
carried members at display time via a new `carried_from_period_id` column. Which architectural shape
is the current direction for envelope items: data-layer settle (Option F), display-layer envelope
view, or both layered together?

A-02 (developer, 2026-05-13): The data-layer settle (Option F / `carry_fwd_impl`) is current.
`envelope_view` is superseded: its display goal (a combined envelope cell, e.g. `18/135`) was
reached by Option F's bumped-canonical mechanism, so `envelope_view`'s data model and aggregation
helper were never built. Evidence:

- `app/services/carry_forward_service.py:275` branches on `template.is_envelope`; the envelope
  branch calls `transaction_service.settle_from_entries(source)`
  (`app/services/transaction_service.py:38-169`), which mutates status/paid_at/actual_amount only;
  `pay_period_id` is unchanged.
- Target canonical's `estimated_amount` is bumped by leftover and `is_override=True` is set
  (`app/services/carry_forward_service.py:891-894`).
- Combined display arises naturally: `app/templates/grid/_transaction_cell.html:43` renders
  `entry_sum / estimated_amount` for the single bumped canonical row.
- `envelope_view`'s additions are absent: zero `carried_from_period_id` matches across `app/` and
  `migrations/`; no `app/services/grid_aggregation.py`; no `EnvelopeCell` anywhere in the codebase.
- End-user observation (developer): the source row stays in the source period showing $65 with a
  Done badge, consistent with Option F and inconsistent with `envelope_view`.

A-02 verification (auditor, 2026-05-15): ACCURATE. All seven evidence bullets verified at the cited
locations. Two minor notes: `settle_from_entries` ends at `app/services/transaction_service.py:168`,
not 169 (the file is 168 lines); the render at `app/templates/grid/_transaction_cell.html:42-44` is
gated on `show_progress` at line 19, which requires `status_id == STATUS_PROJECTED` (true for the
bumped target canonical in the steady state, which is the case the developer is describing). Zero
matches for `carried_from_period_id`, `EnvelopeCell`, or `grid_aggregation*` anywhere in code or
migrations. See `answer_verification.md` Section 1 (A-02).

Q-03 (maps to C-02): Recurrence skip rule for `is_override` rows. `carry_fwd_impl` leans on the
existing rule that any `is_override=True` row blocks regeneration of its canonical; `envelope_view`
(sections 4.4 and 12) narrows the rule so carried-only overrides do NOT block generation, only
non-carried (manually-edited) overrides do. Should the recurrence and transfer-recurrence engines
treat carried overrides as non-blocking (envelope_view's narrowing) or continue blocking on any
override (carry_fwd_impl's assumption)?

A-03 (developer, 2026-05-13): Continue blocking on any `is_override=True` row. The recurrence and
transfer-recurrence engines still treat any `is_override=True` row as a skip signal
(`app/services/recurrence_engine.py:128`, `app/services/transfer_recurrence.py:97`).
`envelope_view`'s narrowing of the rule (carried-only overrides should not block) was never
implemented because `envelope_view`'s data model was never built; see A-02. Option F does not need
the narrowing -- under settle-and-bump the target canonical IS the canonical (bumped in place), not
a separate carried sibling, so a single override flag is sufficient.

A-03 verification (auditor, 2026-05-15): ACCURATE. Skip predicates verified at
`app/services/recurrence_engine.py:128` (`if existing_txn.is_override:`) and
`app/services/transfer_recurrence.py:97` (`if xfer.is_override:`). The `is_override` column on
`SoftDeleteOverridableMixin` (`app/models/mixins.py:65-103`) is a single uniform boolean; the schema
has no provenance metadata, so a "carried-only" sub-rule could not be represented even if a future
code change wanted to differentiate. Option F's "bumps in place" verified at
`app/services/carry_forward_service.py:887-896` (no sibling row is created; the existing target
canonical is mutated). See `answer_verification.md` Section 1 (A-03).

Q-04 (maps to C-03): ARM `current_principal` source for projection. `section5` (5.1-2) says current
principal must be derived from confirmed payments via engine replay and replace the stored
`LoanParams.current_principal` for projection purposes; `arm_anchor` (3F) says for ARM loans
`_compute_real_principal()` must return `current_principal` directly without replaying payments
because forward-from-origination is mathematically wrong without complete rate history. For ARM
loans specifically, should the audit verify that the code uses the stored `current_principal` (per
`arm_anchor`) or the engine-replayed value (per `section5`), and is the answer different for
fixed-rate loans?

A-04 (developer, 2026-05-13): The two plans apply to different loan types by design; they are not
contradictory. For ARM loans the code uses the stored `current_principal` directly (per `arm_anchor`
3F): `app/services/amortization_engine.py:977-985` sets
`LoanProjection.current_balance = current_principal`;
`app/services/savings_dashboard_service.py:373` reads it through `proj.current_balance`;
`app/services/year_end_summary_service.py:1465-1469` anchors the schedule at `current_principal` for
ARM. For fixed-rate loans the code walks the schedule forward from origination using confirmed
`PaymentRecord`s (per `section5`'s replay approach), same lines 977-985. The `LoanProjection`
docstring at `app/services/amortization_engine.py:848-861` documents this dual policy. Phase 3
should verify the split is consistent across every page that displays principal, not whether one
plan replaces the other.

A-04 verification (auditor, 2026-05-15): ACCURATE. All four cited locations verified.
`app/services/amortization_engine.py:977-984` branches on `is_arm` (line 977); ARM sets
`cur_balance = current_principal` (line 978); fixed-rate walks
`for row in reversed(schedule): if row.is_confirmed: cur_balance = row.remaining_balance; break`
(lines 980-984) with fallback to `current_principal`. Minor: the `LoanProjection` docstring at line
855 says fixed-rate `current_balance` is "derived from the schedule by walking to today's date", but
the implementation actually walks for the last `is_confirmed` row. The inline comment at lines
971-976 acknowledges this is deliberate ("walking to today's date would pick up theoretical
contractual rows that may not match reality"). Documentation drift, not a logic bug. See
`answer_verification.md` Section 1 (A-04).

Q-05 (maps to C-04): ARM monthly payment computation. `section5` describes the engine replaying
actual payments from origination and re-amortizing at every rate change. `arm_anchor` (1A, 1D)
introduces an anchor reset at today using `current_principal` and `current_rate`, and recomputes the
monthly payment from the anchor forward; pre-anchor rows are approximate. Inside an ARM's fixed-rate
window, these two methods can produce different monthly payment values when rate history is
incomplete. Which method is current, and is the developer's reported "fluctuating monthly payment"
symptom a manifestation of the methods being mixed across entry points?

A-05 (developer, 2026-05-13): `arm_anchor`'s anchor-reset method is current -- every code site that
computes ARM monthly payment uses the formula
`calculate_monthly_payment(current_principal, current_rate, remaining_months)` per W-048. Eight call
sites compute it: `app/services/amortization_engine.py:440`, `:491`, `:512`, `:697`, `:952`;
`app/services/balance_calculator.py:225`; `app/services/loan_payment_service.py:251`;
`app/routes/loan.py:1225`. The fluctuation symptom is not a "mixed methods" issue but an
"inconsistent inputs" issue: each call site computes `current_principal`, `current_rate`, and
`remaining_months` from its own context (`LoanParams.interest_rate` vs `rate_history` resolution;
different `remaining_months` derivations). `section5`'s engine-replay description is also still in
the code (the engine replays confirmed payments from origination forward); inside an ARM's
fixed-rate window, replay and anchor-reset must produce the same number, and any divergence is a
finding. Phase 3 must verify all eight call sites receive the same triple for the same loan on the
same date; Phase 6 should record the DRY violation across the eight sites.

A-05 verification (auditor, 2026-05-15): PARTIAL. All 8 enumerated sites exist and use the ARM
formula. But "Eight call sites compute it" understates the total: there are 16 call sites in `app/`,
plus 1 definition at `app/services/amortization_engine.py:178`. The 8 fixed-rate ELSE branches use a
different formula `(original_principal, rate, term_months)` and produce a different value for
partially-paid fixed-rate loans. Full inventory:

- ARM-formula branches (the 8 in A-05): `amortization_engine.py:440, :491, :512, :697, :952`;
  `balance_calculator.py:225`; `loan_payment_service.py:251`; `routes/loan.py:1225`.
- Fixed-rate ELSE branches (8 sites, missing from A-05):
  `amortization_engine.py:436, :693,
  :957`;`balance_calculator.py:231`;`loan_payment_service.py:256`;`routes/loan.py:1231`. These
  use`(original_principal, rate, term_months)`. Plus 2 in-loop state transitions
  inside`generate_schedule`(`:491`,`:512`, listed above with the ARM branches because they fire only
  on ARM schedules in practice) and 2 unconditional sites:`routes/loan.py:1102` (refinance preview,
  new-loan terms by design) and `app/routes/debt_strategy.py:127` (uses ARM formula on EVERY loan,
  ARM or fixed; intent unclear).

`app/routes/debt_strategy.py:127` is the 16th site, missed by both A-05 and Q-09. Q-09's "14" is
also short (its prose enumerates 7 fallback sites despite saying "six"). Three concerns Phase 3 must
verify:

1. Pairs 1-2 in `amortization_engine.py` (`:436/440`, `:693/697`) use caller-state discriminator
   (`using_contractual` = `original_principal` provided AND `term_months` provided AND no
   `rate_changes`), not the `is_arm` column. A caller that forgets `original_principal` on a
   fixed-rate loan silently routes through the ARM branch.
2. The `:952` predicate is `if is_arm and remaining > 0`. A fully-paid ARM (`remaining <= 0`)
   routes through the fixed-rate ELSE at `:957` using `orig_principal/term_months`, which
   produces a meaningless contractual figure for a paid-off loan.
3. `debt_strategy.py:127` needs developer clarification on intent.

Phase 3 must verify all 16 call sites against the per-loan invariant, not just the 8 ARM branches;
verifying only the ARM branches answers the W-048 invariant tautologically. See
`answer_verification.md` Section 1 (A-05) for the full call-site table.

Q-06 (maps to C-05): Year-end mortgage interest source. `section8` (13.D) defines mortgage interest
total as the sum of interest portions from amortization schedule rows whose `payment_date` falls in
the calendar year. `year_end_fixes` (1) requires escrow subtraction from shadow transaction amounts
and biweekly-month redistribution before amortization. Should the `section8` definition stand on its
own, or is it superseded by `year_end_fixes`'s preprocessing requirements such that `section8`'s
rule is incomplete on its own?

A-06 (developer, 2026-05-13): The plans describe different layers of the same pipeline and both
apply. `year_end_fixes` is preprocessing: `app/services/loan_payment_service.py:263-353`
(`prepare_payments_for_engine`) subtracts escrow excess at lines 305-318 and redistributes biweekly
month collisions at lines 321-351 before payments reach the amortization engine. `section8` (13.D)
is aggregation: `app/services/year_end_summary_service.py:380-408` (`_compute_mortgage_interest`)
sums `row.interest` for schedule rows where `payment_date.year == year`. The exact-value test at
`tests/test_services/test_year_end_summary_service.py:1399` (`Decimal("15356.80")`) verifies the
full pipeline. `section8`'s rule is correct only when applied to a schedule generated from
`year_end_fixes`-preprocessed payments; both plans are honored simultaneously.

A-06 verification (auditor, 2026-05-15): ACCURATE. All cited locations and the end-to-end pipeline
verified: `Transaction (shadow income)` -> `get_payment_history` (`loan_payment_service.py:156-230`)
-> `prepare_payments_for_engine` (`loan_payment_service.py:263-353`, called from `load_loan_context`
at `:122-125`) -> `LoanContext.payments` -> `amortization_engine.generate_schedule` (from
`year_end_summary_service._generate_debt_schedules` at `:1471-1483`) -> `_compute_mortgage_interest`
at `year_end_summary_service.py:380-408`. Minor: the escrow subtraction loop spans
`loan_payment_service.py:305-318` with the reassignment `sorted_payments = adjusted` at line 319.
Two observations: the test at `tests/test_services/test_year_end_summary_service.py:1399` invokes
`compute_year_end_summary` end-to-end but creates zero payments, so `prepare_payments_for_engine`
short-circuits at the empty-list guard (`:297-298`); the actual reshaping is covered by dedicated
unit tests in `tests/test_services/test_loan_payment_service.py:488+`. Two paths call
`generate_schedule` WITHOUT preprocessing: `savings_dashboard_service.py:471, 488` (paid-off check)
and `routes/debt_strategy.py:175, 181` (debt-strategy current-principal). They do not affect
mortgage interest but could produce wrong schedules in their own contexts when escrow-inclusive
payments are present. See `answer_verification.md` Section 1 (A-06).

Q-07 (maps to C-06): Envelope source row `pay_period_id` after carry-forward. `carry_fwd_impl`
(Phase 4 step 7) says the envelope source row stays in its original period as a settled record;
`prod_readiness_v1`'s description of `carry_forward_unpaid` (WU-10) implies template-linked
transactions move to the target period and are flagged as overrides. These describe different
generations of carry-forward behavior. Which behavior does the current code embody for envelope vs
non-envelope rows, and is `prod_readiness_v1`'s description superseded by `carry_fwd_impl`?

A-07 (developer, 2026-05-13): The plans describe different branches of the same partition; both
behaviors coexist for different transaction shapes. `app/services/carry_forward_service.py`
partitions sources into three branches (lines 273-277): transfer (`transfer_id IS NOT NULL`),
envelope (`template.is_envelope`), and discrete (everything else). For envelope rows
(`carry_fwd_impl` W-118), source `pay_period_id` is unchanged -- `settle_from_entries` only mutates
status/paid_at/actual_amount; target canonical is bumped at lines 891-894. For discrete
template-linked rows (`prod_readiness_v1` W-192), source is moved with `pay_period_id = target` and
`is_override = True` at lines 415-416. `prod_readiness_v1`'s WU-10 description is the
discrete-branch behavior; `carry_fwd_impl`'s Phase 4 step 7 is the envelope-branch behavior. Neither
plan supersedes the other.

A-07 verification (auditor, 2026-05-15): ACCURATE. The three-way partition at
`carry_forward_service.py:272-278` verified with the exact predicates the developer described; lines
415-416 (discrete) and 891-894 (envelope target bump) verified. One nuance the answer did not
surface: the discrete branch further sub-splits at runtime.

- Template-linked discrete (`template_id IS NOT NULL`, `carry_forward_service.py:405-421`): bulk
  UPDATE sets `pay_period_id`, `is_override = True`, `version_id += 1`.
- Ad-hoc discrete (`template_id IS NULL`, `carry_forward_service.py:423-438`): bulk UPDATE sets
  only `pay_period_id` and `version_id += 1`. It does NOT set `is_override`.

Reason for the ad-hoc carve-out: ad-hoc rows are not constrained by the partial unique index
`idx_transactions_template_period_scenario` (comment at lines 382-384), so they have no canonical to
override. The sub-split is intentional. Worth adding to A-07 for completeness. See
`answer_verification.md` Section 1 (A-07).

## Behavioral ambiguities raised by Phase 1

Phase 1 sessions append a `Q-NN` here whenever a function admits two plausible behaviors and the
codebase does not pick between them by reference to a documented intent. The developer answers in
the next session.

Q-08 (P1-b, 2026-05-15): For an entry-tracked transaction whose parent status is `done` (paid),
should the dashboard's "remaining" and "over-budget" display compute against `estimated_amount` or
against `actual_amount`? Today `dashboard_service._entry_progress_fields`
(`app/services/dashboard_service.py:203-249`) reads `txn.estimated_amount` directly at line 239 (via
`entry_service.compute_remaining`) and line 245 (for the over-budget comparison), even when the
transaction is settled and `actual_amount` is non-null. Two interpretations are plausible:

- "Budget is what you allocated": the remaining-and-over-budget figures should always anchor on
  `estimated_amount` so the user sees how their original plan held up, regardless of how the
  transaction settled. The current code matches this reading.
- "Budget is what you spent": once the transaction settles, the remaining/over-budget figures
  should re-anchor on `actual_amount` so the dashboard reflects the actual outcome and any drift
  between estimate and actual is visible.

Which reading is intended? Phase 3 will flag the current code as either AGREE (interpretation 1) or
DIVERGE (interpretation 2) based on the answer.

A-08 proposed (auditor, 2026-05-15, pending developer confirmation): The current code implements
interpretation (1) "Budget = what you allocated". `_entry_progress_fields`
(`app/services/dashboard_service.py:203-246`) anchors `entry_remaining` (line 239) and
`entry_over_budget` (line 245) on `txn.estimated_amount` unconditionally. `compute_remaining` at
`app/services/entry_service.py:405-425` is `estimated_amount - sum(e.amount for e in entries)`; the
function does not receive the transaction and cannot switch on status. For a done txn with
`actual_amount = $100`, `estimated_amount = $120`, entries summing to
$80: returns `entry_remaining = $40`, `entry_over_budget =
False`; the `$100` `actual_amount` is not consulted. Separately, `bill["amount"] =
txn.effective_amount` (`dashboard_service.py:191`) returns `actual_amount` when non-null, so the
displayed bill row has internally inconsistent anchors against a single user mental model.

No test exercises (done, actual_amount set, entries present):
`tests/test_routes/test_dashboard_entries.py:45-88` hard-codes `status_id = projected.id`. The audit
cannot determine intent from the code alone. If interpretation (1) is the intent, Phase 3 records
AGREE; if interpretation (2), the code is a DIVERGE finding and `compute_remaining` needs to accept
the transaction and switch base on `is_settled`. The cross-anchor inconsistency (amount uses
`actual_amount`, remaining uses `estimated_amount`) is a separate concern worth labeling regardless
of which interpretation is chosen. See `answer_verification.md` Section 2 (Q-08).

Q-09 (P1-b, 2026-05-15): For an ARM mortgage's `monthly_payment`, A-05 lists eight call sites that
must receive the same `(current_principal, current_rate, remaining_months)` triple for a given
loan-on-date. The grep in P1-b finds fourteen `calculate_monthly_payment` call sites, not eight: an
extra three in `amortization_engine.py` (lines 436, 693, 957), an extra one in
`balance_calculator.py` (line 231), an extra one in `loan_payment_service.py` (line 256), and two in
`app/routes/loan.py` (lines 1102, 1231). The eight in A-05 are the primary branch of each if/else
pair; the additional six are the fallback branches. Should Phase 3's consistency audit verify all
fourteen call sites against the invariant, or only the eight in A-05? If only the primary branches
matter, what guarantees do the fallback branches make about their inputs?

A-09 proposed (auditor, 2026-05-15, pending developer confirmation): The actual count is 16 call
sites in `app/`, not 14. A-05 listed 8 (the ARM branches); Q-09 listed 7 additional (despite saying
"six") and missed `app/routes/debt_strategy.py:127`. Full inventory in `answer_verification.md`
Section 1 (A-05) and Section 2 (Q-09).

Phase 3 should verify all 16 call sites, not just the 8 ARM branches. Reasons:

- The 8 fixed-rate ELSE branches (`amortization_engine.py:436, :693, :957`;
  `balance_calculator.py:231`; `loan_payment_service.py:256`; `routes/loan.py:1231`) use the
  formula `(original_principal, rate, term_months)`, NOT the ARM formula. For partially-paid
  fixed-rate loans these produce a different value than the ARM branches.
- Verifying only the 8 ARM branches answers the W-048 invariant tautologically (every ARM
  branch uses the ARM formula, by construction).
- The fluctuation symptom may originate from a fixed-rate branch firing when the ARM branch was
  expected, or from the caller-state discriminator (`using_contractual`) misclassifying a
  fixed-rate loan when `original_principal` is not passed.

Fallback branch input guarantees:

- Pairs 3-6 (`is_arm`-discriminated, sites at `:952/957`, `balance_calculator.py:225/231`,
  `loan_payment_service.py:251/256`, `routes/loan.py:1225/1231`): the ELSE branches read
  `LoanParams.original_principal` and `params.term_months`, required model columns wrapped in
  `Decimal(str(...))`. Risk axis is the discriminator (`is_arm` column correctness), not the
  inputs.
- Pairs 1-2 (`amortization_engine.py:436/440`, `:693/697`): discriminator is caller-supplied
  state (`using_contractual` = `original_principal` provided AND `term_months` provided AND no
  `rate_changes`). A caller that omits `original_principal` for a fixed-rate loan silently
  routes through the ARM branch. Phase 3 should audit every direct caller of `generate_schedule`
  and `calculate_summary` for this pattern.
- Corner case at `:952`: `if is_arm and remaining > 0` routes a fully-paid ARM (`remaining <=
  0`) through the fixed-rate ELSE at `:957`, using `orig_principal/term_months`. Likely a
  meaningless figure for a paid-off ARM.
- `routes/debt_strategy.py:127`: unconditional ARM-formula call on every loan, fixed-rate or
  ARM. Intent unclear; needs developer confirmation. For a partially-paid fixed-rate loan, this
  produces a value lower than the contractual payment.

See `answer_verification.md` Section 2 (Q-09) for the full per-site analysis.

Q-10 (P1-c, 2026-05-15): `grid.index` (`app/routes/grid.py:164`) computes per-period subtotals
(income, expense, net) inline at lines 263-279 by iterating transactions and accumulating
`txn.effective_amount` directly into Decimal aggregates. The same domain concept (`period_subtotal`)
is also produced by `dashboard_service._get_spending_comparison` and is implicit in
`balance_calculator._sum_remaining` / `_sum_all`. Two interpretations are plausible:

- "Subtotal is a display detail of the grid": route-layer aggregation is fine because the only
  consumer is the grid template; service-layer functions handle the balance computation that
  needs to be cross-page consistent. The current code matches this reading.
- "Subtotal is a shared financial concept": route-layer aggregation duplicates logic the service
  layer already owns (the balance calculator iterates the same transactions with the same status
  filter to produce running balances). The subtotal computation should move to a service so
  every consumer reads it from one source.

Which reading is intended? Phase 6 SRP review needs the answer; Phase 3 must compare `grid.index`
subtotals against the dashboard's spending-comparison values for the same period regardless, because
both are user-facing financial figures.

A-10 proposed (auditor, 2026-05-15, pending developer confirmation): The current code computes
per-period subtotal inline in the route at `app/routes/grid.py:263-279` (Projected-only, `is_income`

- `is_expense` split, `txn.effective_amount`). Three other services compute superficially-similar
"period totals" with divergent semantics:
- `dashboard._sum_settled_expenses` (`app/services/dashboard_service.py:607-633`): settled-only
  (status in DONE/RECEIVED/SETTLED), expense-only, `abs(effective_amount)`. OPPOSITE status
  filter from grid; cannot agree by construction.
- `balance_calculator._sum_remaining` and `_sum_all`
  (`app/services/balance_calculator.py:389-451`): same filters as grid (Projected-only,
  is_income + is_expense), BUT expense uses `_entry_aware_amount(txn)`
  (`balance_calculator.py:292-386`), which subtracts cleared entry debits when entries are
  loaded. For a projected envelope expense with cleared entries, the two paths produce different
  numbers.
- `spending_trend_service.period_totals` (`app/services/spending_trend_service.py:315-322`):
  expense-only, abs of effective_amount.

No service-level `period_subtotal` function exists. Grep for
`period_subtotal | period_total | subtotal` finds only `spending_trend_service.py:315` and the
template/route consumers of the `subtotals` dict the grid hands to its template.

Phase 3 must record that the grid's inline subtotal and the balance calculator's expense disagree on
`(period, Projected, envelope-with-cleared-entries)` inputs, regardless of which interpretation the
developer chooses. This is a DIVERGE the user would see if they compared the grid's subtotal row to
a running balance derived from the balance calculator (both have `selectinload(Transaction.entries)`
in effect; `grid.py:229`).

Choosing interpretation (1) "display detail of grid" matches current locality; the divergence above
still requires a Phase 3 finding. Choosing interpretation (2) "shared concept" requires deciding
between `effective_amount` and `_entry_aware_amount` as the canonical expense formula. See
`answer_verification.md` Section 2 (Q-10).

Q-11 (P1-c, 2026-05-15): `loan.refinance_calculate` (`app/routes/loan.py:1027`) derives
`current_real_principal = proj.current_balance` at line 1087 from
`amortization_engine.get_loan_projection`. A-04's dual policy means `proj.current_balance` is the
stored `LoanParams.current_principal` for ARM loans and the engine-walked balance for fixed-rate
loans. The refinance dialog then optionally overrides with
`refi_principal = current_real_principal + closing_costs` at line 1095 (when the user does not
supply an explicit `new_principal` value). Is the refinance flow expected to honor the A-04 dual
policy unchanged (ARM uses stored, fixed uses walked), or should the refinance "current principal"
always come from a single canonical source regardless of loan type? Phase 3 must verify the value
the refinance form prefills matches the value rendered on `/accounts/<id>/loan` for the same
loan-on-date.

A-11 proposed (auditor, 2026-05-15, pending developer confirmation): The `/accounts/<id>/loan`
dashboard does NOT honor the A-04 dual policy on the display side. This is a real divergence.

The dashboard route at `app/routes/loan.py:405-575` constructs `proj = get_loan_projection(...)`
(line 429) but the template at `app/templates/loan/dashboard.html:104` renders
`${{ "{:,.2f}".format(params.current_principal|float) }}` directly, bypassing the projection
entirely. The refinance route at `app/routes/loan.py:1087` uses `proj.current_balance`.

For ARM loans the two values coincide because `app/services/amortization_engine.py:978` assigns
`cur_balance = current_principal`. For fixed-rate loans with any confirmed payments,
`proj.current_balance` is the last `is_confirmed` row's `remaining_balance`
(`amortization_engine.py:980-984`), which may differ from the stored `params.current_principal`.
Only when no confirmed payments exist (the fallback at line 980) do the two values match.

Phase 3 finding: for a fixed-rate loan with any confirmed payments, the refinance form prefill (when
"New Principal" is blank, the auto-calc at `loan.py:1095`) does NOT match the "Current Principal"
card on `/accounts/<id>/loan`. The refinance prefill is the more accurate number (reflects committed
payments); the dashboard's display value is the stored static.

Developer must decide: either render `proj.current_balance` on the dashboard (pass it via the
existing engine call at line 429) so prefill matches the on-screen number, or rename the dashboard
label to make the divergence intentional and visible ("Stored Principal" vs "Current Balance" or
similar). See `answer_verification.md` Section 2 (Q-11).

Q-12 (P1-c, 2026-05-15): `obligations.summary` (`app/routes/obligations.py:259`) builds monthly
equivalents for recurring templates inline at lines 331-395 by calling
`savings_goal_service.amount_to_monthly` per template in a loop and then aggregating the totals with
Decimal arithmetic at lines 398-408. The result feeds the `/obligations` page's `net_cash_flow` row.
The same monthly-equivalent normalization is needed elsewhere -- the dashboard's cash-runway
computation (`dashboard_service._compute_cash_runway` uses paid expenses over 30 days, NOT
recurring-template monthly equivalents, but the conceptual overlap is real) and the
savings-dashboard's DTI denominator (`savings_dashboard_service._compute_debt_summary`). Should the
per-template loop and aggregation move into a dedicated service so every consumer reads from one
canonical monthly- equivalent aggregator? If not, what is the contract that distinguishes the three
call paths so Phase 3 can verify each produces a consistent number when their inputs overlap?

A-12 proposed (auditor, 2026-05-15, pending developer confirmation): The three paths answer
different questions; the code makes no guarantee they agree.

- `obligations.summary` (`app/routes/obligations.py:259-423`): forward-projected monthly from
  active recurring `TransactionTemplate` + `TransferTemplate`. Excludes `ONCE` patterns (lines
  333-334, 356-357, 378-379). Excludes templates with `end_date < today` (lines 335, 358, 380).
  Calls `amount_to_monthly(amount, pattern_id, interval_n)` at lines 338, 361, 383.
- `dashboard._compute_cash_runway` (`app/services/dashboard_service.py:375-417`): trailing
  30-day average from SETTLED `Transaction` rows (status in DONE/RECEIVED/SETTLED, expense-only).
  Does NOT consult templates. Returns days of runway, not monthly equivalent.
- `savings_dashboard._compute_debt_summary`
  (`app/services/savings_dashboard_service.py:802-876`): amortization-engine P+I
  (`ad["monthly_payment"]` from `get_loan_projection` at `:362-367`) plus escrow `annual / 12`
  (`escrow_calculator.calculate_monthly_escrow`). Does NOT call `amount_to_monthly` and does
  NOT consult templates.

Cash-runway and debt-summary are conceptually independent from obligations: realised history,
amortization-derived obligation, and forward template projection are different quantities. A single
aggregator only makes sense within the obligations path itself (three near-identical inline loops at
obligations.py:331-395 could be extracted to a helper).

The 26/12 biweekly-to-monthly conversion factor is duplicated in three places:
`savings_goal_service.py:17-18` (`_PAY_PERIODS_PER_YEAR` / `_MONTHS_PER_YEAR`),
`savings_dashboard_service.py:170-172` (inline `Decimal("26") / Decimal("12")`), and
`savings_dashboard_service.py:765` (same inline form). Numerically equivalent; not cross-imported.

Two real defects surfaced by this question (out of Q-12 scope but reported per CLAUDE.md rule 4):

1. `compute_committed_monthly` at `app/services/savings_goal_service.py:287-328` does NOT skip
   templates with `end_date < today`, while `obligations.summary` does. Consumers (emergency-fund
   baseline at `savings_dashboard_service.py:794`, per-goal contributions at `:700`) include
   expired-template contributions indefinitely.
2. Mortgage / loan double-counting risk: a user with both (a) a recurring expense template for
   the mortgage AND (b) a loan account with `loan_params` sees the same payment in
   `/obligations.total_expense_monthly` AND in the savings dashboard DTI numerator. No
   reconciliation guard.

Phase 3 contract for verification: for the same set of active recurring expense templates with
`pattern_id != ONCE` AND `(end_date IS NULL OR end_date >= today)`,
`obligations.summary.total_expense_monthly` MUST equal the sum of `amount_to_monthly` outputs.
`cash_runway` and `_compute_debt_summary` have no such relationship with obligations. See
`answer_verification.md` Section 2 (Q-12).

Q-13 (P1-c, 2026-05-15): `salary.calibrate_preview` (`app/routes/salary.py:1064`) computes the
calibration's taxable-income input inline at line 1095 (`taxable = gross - total_pre_tax`), even
though `paycheck_calculator.calculate_paycheck` produces a `breakdown.taxable_income` field on its
return value. The route uses `bk.total_pre_tax` (line 1091) to compute its own subtraction but does
NOT read `bk.taxable_income`. Should the route read `bk.taxable_income` directly so the
calibration's effective rates are derived against the same taxable-income value the breakdown
reports, or is the route's inline subtraction the intended source (and the breakdown's field then
potentially divergent)? Phase 3 must verify the two values agree for identical inputs.

A-13 proposed (auditor, 2026-05-15, pending developer confirmation): The route's inline
`taxable = gross - bk.total_pre_tax` at `app/routes/salary.py:1095` and `bk.taxable_income` at
`app/services/paycheck_calculator.py:155-157` measure different quantities by design. They agree
only when `actual_gross_pay == bk.gross_biweekly`, the trivial case calibration is designed to
detect deviation from.

- Route: `taxable = data["actual_gross_pay"] - bk.total_pre_tax` (pay-stub-grounded).
- Breakdown: `bk.taxable_income = max(bk.gross_biweekly - total_pre_tax, 0)` where
  `bk.gross_biweekly = (annual_salary / pay_periods_per_year).quantize(...)`
  (`paycheck_calculator.py:133-135`) (profile-grounded, floored at zero at lines 156-157).

The route's intent is pay-stub-grounded taxable so calibration's effective rates derive from the
user's actual paycheck. Using `bk.taxable_income` directly would defeat the calibration use case.

However, a real bug surfaced: `bk.total_pre_tax` includes percentage-based pre-tax deductions
computed against the PROFILE's `gross_biweekly`, not against the form's `actual_gross_pay`. At
`paycheck_calculator.py:439-442`:

    if ded.calc_method_id == calc_method_pct_id:
        amount = (gross_biweekly * amount).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )

When the form's gross differs from the profile's gross (the calibration use case), the percentage
deduction is applied to the wrong base, and the derived effective rates absorb the gap. Concrete
example: profile `annual_salary = $60,000` so `bk.gross_biweekly = $2,307.69`; pay stub
`actual_gross_pay = $2,400.00`; pre-tax 401k is 5% of gross. Then:

- `bk.total_pre_tax = 2,307.69 * 0.05 = $115.38`
- Route's `taxable = 2,400.00 - 115.38 = $2,284.62`
- If the 5% were applied to the actual stub gross: `2,400.00 * 0.05 = $120.00`, so taxable
  would be `$2,280.00`.

Fix needs developer intent: option A (recompute pre-tax deductions inline against `actual_gross_pay`
via a refactored helper), option B (use `bk.taxable_income / bk.gross_biweekly` as a ratio to
scale), option C (status quo, document the bias). Option A is what calibration semantically wants.
See `answer_verification.md` Section 2 (Q-13).

Q-14 (P1-c, 2026-05-15): `dashboard.mark_paid` (`app/routes/dashboard.py:54-139`) was initially
classified out-of-scope by the route-inventory subagent because its body "updates status/amount in
DB and returns a partial row." Spot-checking the response path showed it returns
`dashboard/_bill_row.html`, which re-renders `effective_amount`, `entry_remaining` (via
`_entry_progress_fields`), and the `goal_progress` percent for the just-paid bill -- all
controlled-vocabulary financial figures. The handler was therefore re-classified IN scope. The
question for the developer is whether `mark_paid` and `transactions.mark_done`
(`app/routes/transactions.py:491`) are path-equivalent: both transition a transaction from projected
to done/received, both render an updated cell partial showing `effective_amount`. Are they intended
to produce the same `effective_amount` and `entry_remaining` values for the same transaction, with
the only difference being which endpoint the UI calls? If so, Phase 3 must verify equivalence; if
not, the documented difference belongs in section 0.3 as an expected behavioral nuance.

A-14 proposed (auditor, 2026-05-15, pending developer confirmation): `mark_paid`
(`app/routes/dashboard.py:54-138`) and `mark_done` (`app/routes/transactions.py:491-629`) are
path-similar but NOT path-equivalent. They share the status policy (RECEIVED for income, DONE for
expense, DONE for transfer shadows) and the same `MarkDoneSchema` for form validation, but they
diverge on five behaviors:

1. Envelope-with-entries auto-settle. `mark_done` calls
   `transaction_service.settle_from_entries(txn)` at `transactions.py:596`, which auto-writes
   `actual_amount = sum(entries)` at `transaction_service.py:153`. `mark_paid` never calls this;
   it writes `actual_amount` only when the form provided it (`dashboard.py:127-128`). For an
   envelope-tracked txn with entries, the two endpoints produce DIFFERENT `actual_amount` for
   the same input request, and therefore different `Transaction.effective_amount` (the property
   at `app/models/transaction.py:222-245` returns `actual_amount` when non-null else
   `estimated_amount`). `mark_done` produces `sum(entries)`; `mark_paid` leaves `actual_amount`
   NULL and `effective_amount` returns `estimated_amount`. This is the most important divergence.
2. Stale-data handling. `mark_done` catches `StaleDataError` and returns a 409 conflict cell
   (`transactions.py:618`); `mark_paid` only catches `IntegrityError` (`dashboard.py:132`).
   Concurrent edits raise 409 in one path and likely 500 in the other.
3. Ownership scope. `mark_done` allows companions for templates with `companion_visible=True`
   (`_get_accessible_transaction_for_status` at `transactions.py:210-241`). `mark_paid` is
   owner-only (`_get_owned_transaction` at `dashboard.py:181-192`).
4. Rendered partial and HX-Trigger. `mark_paid` returns `dashboard/_bill_row.html` with
   `HX-Trigger: dashboardRefresh`; `mark_done` returns `grid/_transaction_cell.html` (via
   `_render_cell` at `transactions.py:628`) with `gridRefresh`. The progress display gating
   differs (bill row: `not bill.is_paid`; grid cell: `status_id == STATUS_PROJECTED`),
   semantically equivalent for a just-settled txn.
5. Logging. `mark_done` emits an info log; `mark_paid` does not.

The arithmetic for `entry_remaining` is identical in both paths (`estimated_amount - sum(entries)`);
the displayed value differs only by template gating.

Phase 3 verdict: the two endpoints produce the same `effective_amount` for transactions that are NOT
envelope-tracked or that have no entries. They produce DIFFERENT `effective_amount` for
envelope-tracked transactions with entries, the case the developer's concern is most likely to
involve.

Recommendation: either align `mark_paid` to call `settle_from_entries` for envelope-tracked
transactions (so the two endpoints agree on `actual_amount` and therefore `effective_amount`), or
document the difference explicitly so future contributors do not assume equivalence. See
`answer_verification.md` Section 2 (Q-14).

## Concept-catalog primary-path questions raised by Phase 2

Phase 2 sessions append a `Q-NN` here whenever a concept has more than one producer and the codebase
does not designate which is the canonical source of truth. The developer answers in the next
session; Phase 2's primary-path entry cross-links the answer.

Q-15 (P2-a, 2026-05-15): There is no single canonical producer for a multi-account aggregate balance
figure, and two independent per-account dispatch implementations exist. Concretely:

- Aggregate `debt_total`: `savings_dashboard_service._compute_debt_summary`
  (`app/services/savings_dashboard_service.py:802`, `total_debt` at line 855) sums the stored
  `LoanParams.current_principal` (`Decimal(str(lp.current_principal))` at line 840), while the
  liability contribution to net worth `year_end_summary_service._compute_net_worth`
  (`app/services/year_end_summary_service.py:689`) derives each loan's balance from the
  amortization schedule via `_build_account_data` / `_get_account_balance_map`
  (`app/services/year_end_summary_service.py:750`). Per A-04 the stored column and the
  engine-walked balance differ for a fixed-rate loan with confirmed payments, so the dashboard /
  `/savings` debt card and the net-worth liability figure can disagree for the same loan.
- Aggregate `account_balance` / `savings_total`: the per-account dispatch (which engine per
  account type) is implemented twice -- `_compute_account_projections`
  (`app/services/savings_dashboard_service.py:294`, drives `/savings` and the dashboard) and
  `_build_account_data` / `_get_account_balance_map`
  (`app/services/year_end_summary_service.py:750`, drives net worth and the year-end savings
  progress). Roadmap v5 Section 2 Stage A names "a single canonical balance computation" as
  PENDING (`docs/audits/financial_calculations/00_priors.md:152`); net_worth_amort W-152
  ("Net worth section and savings progress section must use identical calculation paths for all
  account types") is `planned-per-plan`, i.e. not yet implemented.

Question for the developer: which producer is the canonical source of truth that the displayed
aggregate `debt_total` (and, by extension, `account_balance` / `savings_total`) must derive from --
the savings-dashboard dispatch (`_compute_account_projections` / `_compute_debt_summary`, stored
`current_principal` base) or the year-end dispatch (`_get_account_balance_map`, amortization-real
base) -- so Phase 3 knows which side is the reference when E-04 (same number on every page) is
violated?

Why it matters: determines the primary path for `account_balance`, `savings_total`, and `debt_total`
in `02_concepts.md` (P2-a); governs the symptom #5 (`/accounts` vs other pages divergence)
hypothesis tree in Phase 5; the answer also tells Phase 3 whether the net_worth_amort W-152
plan-drift is a "code must catch up to plan" finding or a "plan superseded" finding.

Where it came up: `docs/audits/financial_calculations/02_concepts.md` (P2-a), `account_balance` /
`savings_total` / `debt_total` primary-path entries;
`app/services/savings_dashboard_service.py:294,802` and
`app/services/year_end_summary_service.py:689,750`.

## Behavioral divergences raised by Phase 3

Phase 3 sessions append a `Q-NN` here whenever the consistency audit finds the code does the same
thing more than one way and no documented intent picks the canonical behavior. The auditor records
the divergence as a finding (provable from code) and STOPS on the "which behavior is intended"
question for the developer (audit-plan section 9; hard rule 5 -- no guessed verdict).

Q-16 (P3-a, 2026-05-15): When a checking/savings account has `current_anchor_period_id IS NULL`
(no anchor period set), the five balance producers do three different things for the same account:

- Grid (`app/routes/grid.py:239-241`): passes `None` as `anchor_period_id`; the engine
  (`app/services/balance_calculator.py:69-86`) matches no anchor period, so `running_balance`
  stays `None` and every period hits `continue` (`:82-84`) -- `balances` is empty and the grid
  renders a blank balance row.
- `/accounts` checking detail (`app/routes/accounts.py:1419-1421`) and `/savings`
  (`app/services/savings_dashboard_service.py:326-328`): `... or (current_period.id if ...)`
  falls back to the current period as the anchor with a `$0.00` anchor balance, producing a
  populated (but anchored-at-zero) projection.
- Net worth (`app/services/year_end_summary_service.py:2065-2066`) and the dashboard
  (`app/services/dashboard_service.py:683-684`): `return None`, omitting the account from the
  net-worth total and the dashboard balance card entirely.

Question for the developer: what is the intended behavior when an account has no anchor period --
blank (grid), a `$0`-anchored projection (`/accounts`, `/savings`), or omit the account (net
worth, dashboard)? Phase 3 records the divergence as SCOPE_DRIFT in F-001/F-003/F-006/F-007
regardless; the answer tells Phase 8 which of the three is the bug and which is the target.

Why it matters: governs the SCOPE_DRIFT axis in F-001 (`account_balance`), F-003
(`projected_end_balance`), F-006 (`net_worth`), and F-007 (`savings_total`); a user who has
created an account but not yet set its anchor sees it as blank on the grid, as `$0` on
`/accounts` and `/savings`, and as absent from net worth and the dashboard -- four different
representations of one account state, an unlabeled difference under developer expectation E-04
(`00_priors.md:178-182`).

Where it came up: `docs/audits/financial_calculations/03_consistency.md` (P3-a), F-001 / F-003 /
F-006 / F-007 anchor-handling dimension; `app/routes/grid.py:239-241`,
`app/routes/accounts.py:1419-1421`, `app/services/savings_dashboard_service.py:326-328`,
`app/services/year_end_summary_service.py:2065-2066`, `app/services/dashboard_service.py:683-684`.

Q-17 (P3-b, 2026-05-15): For a 5/5 ARM inside its fixed-rate window (e.g. the first 60 months,
`LoanParams.arm_first_adjustment_months = 60`), the displayed "Monthly P&I" is computed by
`get_loan_projection` at `app/services/amortization_engine.py:952-954` as
`calculate_monthly_payment(current_principal, rate, remaining)` where
`current_principal = Decimal(str(params.current_principal))` (`:913`, the STORED column) and
`remaining = calculate_remaining_months(params.origination_date, params.term_months)`
(`:908-910`, a calendar formula that decreases by 1 every month). The stored
`current_principal` is not reduced as transfers settle (Q-3/A-3 / F-014: the only writer is the
manual `update_params` form at `loan.py:672-674`). Re-amortizing a non-decreasing principal
over a strictly decreasing `remaining` makes the payment drift upward a few dollars every month
even with no rate change and no manual edit -- the developer's symptom #4. The
`arm_first_adjustment_months` / `arm_adjustment_interval_months` columns
(`app/models/loan_params.py:60-61`) are stored and form-bound but consumed by zero calculation
sites (grep-verified), so the engine has no representation of the fixed-rate window. Two
plausible intended behaviors:

- "The stored column is the anchor and must be maintained": per A-04 the ARM
  `current_principal` is authoritative; the intended fix is that confirmed transfers reduce the
  stored column (closing the symptom-#3 gap), after which re-amortizing the *true* remaining
  balance over the shrinking `remaining` reproduces the constant payment via the amortization
  identity. Site 7 is then correct and the bug is purely the missing settle-driven update
  (F-014).
- "The engine must hold the payment constant in the fixed window": site 7 should re-amortize
  `proj.current_balance` (engine-real) over `remaining`, or reset the payment only at
  `arm_first_adjustment_months` / `arm_adjustment_interval_months` boundaries, so the displayed
  payment is invariant for the whole window regardless of whether the stored column is
  maintained.

Which behavior is intended? E-02 (`00_priors.md:166-170`) requires the payment be one value
for the whole fixed window; the audit records the current code as a DIVERGE (F-026) regardless,
but the answer determines whether the remediation target is F-014 (maintain the stored column
on settle) or a change at `amortization_engine.py:952-954` (re-amortize the engine-real
balance / honor the ARM-adjustment columns).

Why it matters: governs the remediation direction for F-013 (`monthly_payment` symptom #2) and
F-026 (5/5 ARM symptom #4), and ties them to F-014 (symptom #3, the un-maintained stored
`current_principal`). The verdict (DIVERGE) is not blocked; only the fix shape is.

Where it came up: `docs/audits/financial_calculations/03_consistency.md` (P3-b), F-013 and
F-026; `app/services/amortization_engine.py:908-959`, `app/models/loan_params.py:60-61`,
`app/routes/loan.py:672-674`. Cross-link Q-09, Q-11, Q-15, A-04/A-05 verification (this file),
E-02 (`00_priors.md:166-170`).

Q-18 (P3-cmp-3, 2026-05-16): The calendar's "month-end balance" (the "End Balance" figure on
`/analytics` calendar month view) is computed by `_compute_month_end_balance`
(`app/services/calendar_service.py:435-489`). It selects the target period by
`for p in all_periods: if p.end_date <= last_day: target_period = p`
(`app/services/calendar_service.py:461-466`, `all_periods` ordered by `period_index`,
`last_day` = the calendar month's last day) -- i.e. the LAST pay period whose `end_date` is
on or before the month's last day, then returns `balances.get(target_period.id)`. The
section8 watchlist claim W-273-cluster (`00_priors.md:524`, plan section 5.D) states the
month-end balance must be "the projected end balance of the last pay period ending in or
after the month." When a pay period straddles the month boundary (starts in-month, ends in
the following month), the code excludes it (its `end_date > last_day`) and instead reports
the projected end balance of the PRIOR period, which can be up to ~13 days before the actual
calendar month-end. Two plausible intended behaviors: (1) "balance as of the last completed
pay period within the month" -- the current code; the displayed figure is a real period-end
projection but not the literal calendar month-end. (2) "balance covering the whole month" --
the period that contains (or first ends on/after) the month-end day, so the figure reflects
every transaction through month-end. Which is intended?

Why it matters: governs the W-277 verdict in P3-cmp-3 (the period-selection axis is recorded
UNKNOWN-Q-18). The displayed "End Balance" is a user-facing money figure on the calendar; an
off-by-one-period selection silently shows a different dollar amount than the user expects
for "month-end," and it interacts with the F-003/F-009 entries-load SILENT_DRIFT (the
calendar path does NOT `selectinload(Transaction.entries)`, `calendar_service.py:471-480`)
and the Q-16 anchor-None axis (`calendar_service.py:449-450` returns `Decimal("0")` when
`current_anchor_period_id is None`).

Where it came up: `docs/audits/financial_calculations/03_consistency.md` (P3-cmp-3), W-277;
`app/services/calendar_service.py:435-489` (esp. `:461-466` period selection, `:471-480`
no-entries-load, `:449-450` anchor-None). Cross-link F-003, F-009, Q-16,
W-273 (`00_priors.md:524`).

Q-19 (P3-cmp-3, 2026-05-16): `archive_helpers.template_has_paid_history`
(`app/utils/archive_helpers.py:17-38`) decides whether a transaction template may be
permanently hard-deleted (`app/routes/templates.py:581-637`). It blocks deletion only when a
linked, non-soft-deleted transaction has `status_id in [DONE, SETTLED]`
(`app/utils/archive_helpers.py:29-37`). RECEIVED is not in that set, yet RECEIVED is a
settled-equivalent state (`Status.is_settled = True` for Received,
`app/ref_seeds.py:79-84`) and is exactly the status `mark_done` assigns to income
transactions (`app/routes/transactions.py:534-535`: `if txn.is_income: status_id =
RECEIVED`). A transaction template whose generated income transactions were all marked
RECEIVED (and never further transitioned to SETTLED) therefore returns
`template_has_paid_history = False`, so `hard_delete_template` proceeds to
`db.session.query(Transaction).filter(Transaction.template_id == template.id).delete()`
(`app/routes/templates.py:616-618`) and permanently destroys the RECEIVED income history.
The section5a watchlist claim W-262 (`00_priors.md:509`, plan section 5A.5-2) is phrased
"templates with Paid or Settled history cannot be permanently deleted" -- the code matches
that literal wording ([DONE = Paid, SETTLED]), so the question is whether the plan's
"Paid or Settled" definition is itself too narrow for income templates: should the guard
also treat RECEIVED (and any `is_settled = True` status) as blocking history, or is
hard-deleting a RECEIVED-only income template's history intended? (The transfer analogue
`transfer_template_has_paid_history`, `app/utils/archive_helpers.py:41-62`, has no such gap:
transfers only ever use DONE/SETTLED -- the transfer service sets DONE on both shadows,
`app/routes/transactions.py:541-545` -- so [DONE, SETTLED] is complete there.)

Why it matters: governs the W-262 escalation in P3-cmp-3. This is a silent data-loss path on
an explicit user delete action (no wrong number on a page, but irreversible loss of settled
income transaction history). The fix shape depends on intent: widen the predicate to
`Status.is_settled = True` (catches RECEIVED + any future settled-equivalent), enumerate
`[DONE, RECEIVED, SETTLED]`, or document that RECEIVED-only income history is intentionally
hard-deletable. The auditor does not choose (audit-plan section 9).

Where it came up: `docs/audits/financial_calculations/03_consistency.md` (P3-cmp-3), W-262;
`app/utils/archive_helpers.py:17-38` (predicate), `app/routes/templates.py:581-637`
(hard-delete path, esp. `:616-618` the unconditional delete), `app/ref_seeds.py:79-84`
(Received `is_settled = True`), `app/routes/transactions.py:534-535` (income -> RECEIVED).
Cross-link W-262, W-264 (the transfer analogue that does NOT have the gap), F-031.
