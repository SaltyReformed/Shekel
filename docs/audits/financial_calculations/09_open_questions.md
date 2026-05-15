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

## Behavioral ambiguities raised by Phase 1

Phase 1 sessions append a `Q-NN` here whenever a function admits two plausible behaviors and the
codebase does not pick between them by reference to a documented intent. The developer answers in
the next session.

Q-08 (P1-b, 2026-05-15): For an entry-tracked transaction whose parent status is `done` (paid),
should the dashboard's "remaining" and "over-budget" display compute against `estimated_amount` or
against `actual_amount`? Today `dashboard_service._entry_progress_fields`
(`app/services/dashboard_service.py:203-249`) reads `txn.estimated_amount` directly at line 239
(via `entry_service.compute_remaining`) and line 245 (for the over-budget comparison), even when
the transaction is settled and `actual_amount` is non-null. Two interpretations are plausible:

- "Budget is what you allocated": the remaining-and-over-budget figures should always anchor on
  `estimated_amount` so the user sees how their original plan held up, regardless of how the
  transaction settled. The current code matches this reading.
- "Budget is what you spent": once the transaction settles, the remaining/over-budget figures
  should re-anchor on `actual_amount` so the dashboard reflects the actual outcome and any drift
  between estimate and actual is visible.

Which reading is intended? Phase 3 will flag the current code as either AGREE (interpretation 1) or
DIVERGE (interpretation 2) based on the answer.

Q-09 (P1-b, 2026-05-15): For an ARM mortgage's `monthly_payment`, A-05 lists eight call sites that
must receive the same `(current_principal, current_rate, remaining_months)` triple for a given
loan-on-date. The grep in P1-b finds fourteen `calculate_monthly_payment` call sites, not eight: an
extra three in `amortization_engine.py` (lines 436, 693, 957), an extra one in
`balance_calculator.py` (line 231), an extra one in `loan_payment_service.py` (line 256), and two
in `app/routes/loan.py` (lines 1102, 1231). The eight in A-05 are the primary branch of each
if/else pair; the additional six are the fallback branches. Should Phase 3's consistency audit
verify all fourteen call sites against the invariant, or only the eight in A-05? If only the
primary branches matter, what guarantees do the fallback branches make about their inputs?

Q-10 (P1-c, 2026-05-15): `grid.index` (`app/routes/grid.py:164`) computes per-period subtotals
(income, expense, net) inline at lines 263-279 by iterating transactions and accumulating
`txn.effective_amount` directly into Decimal aggregates. The same domain concept
(`period_subtotal`) is also produced by `dashboard_service._get_spending_comparison` and is
implicit in `balance_calculator._sum_remaining` / `_sum_all`. Two interpretations are plausible:

- "Subtotal is a display detail of the grid": route-layer aggregation is fine because the only
  consumer is the grid template; service-layer functions handle the balance computation that
  needs to be cross-page consistent. The current code matches this reading.
- "Subtotal is a shared financial concept": route-layer aggregation duplicates logic the service
  layer already owns (the balance calculator iterates the same transactions with the same status
  filter to produce running balances). The subtotal computation should move to a service so
  every consumer reads it from one source.

Which reading is intended? Phase 6 SRP review needs the answer; Phase 3 must compare
`grid.index` subtotals against the dashboard's spending-comparison values for the same period
regardless, because both are user-facing financial figures.

Q-11 (P1-c, 2026-05-15): `loan.refinance_calculate` (`app/routes/loan.py:1027`) derives
`current_real_principal = proj.current_balance` at line 1087 from
`amortization_engine.get_loan_projection`. A-04's dual policy means `proj.current_balance` is
the stored `LoanParams.current_principal` for ARM loans and the engine-walked balance for
fixed-rate loans. The refinance dialog then optionally overrides with
`refi_principal = current_real_principal + closing_costs` at line 1095 (when the user does not
supply an explicit `new_principal` value). Is the refinance flow expected to honor the A-04
dual policy unchanged (ARM uses stored, fixed uses walked), or should the refinance "current
principal" always come from a single canonical source regardless of loan type? Phase 3 must
verify the value the refinance form prefills matches the value rendered on `/accounts/<id>/loan`
for the same loan-on-date.

Q-12 (P1-c, 2026-05-15): `obligations.summary` (`app/routes/obligations.py:259`) builds monthly
equivalents for recurring templates inline at lines 331-395 by calling
`savings_goal_service.amount_to_monthly` per template in a loop and then aggregating the totals
with Decimal arithmetic at lines 398-408. The result feeds the `/obligations` page's
`net_cash_flow` row. The same monthly-equivalent normalization is needed elsewhere -- the
dashboard's cash-runway computation (`dashboard_service._compute_cash_runway` uses paid
expenses over 30 days, NOT recurring-template monthly equivalents, but the conceptual overlap
is real) and the savings-dashboard's DTI denominator
(`savings_dashboard_service._compute_debt_summary`). Should the per-template loop and
aggregation move into a dedicated service so every consumer reads from one canonical monthly-
equivalent aggregator? If not, what is the contract that distinguishes the three call paths so
Phase 3 can verify each produces a consistent number when their inputs overlap?

Q-13 (P1-c, 2026-05-15): `salary.calibrate_preview` (`app/routes/salary.py:1064`) computes the
calibration's taxable-income input inline at line 1095 (`taxable = gross - total_pre_tax`),
even though `paycheck_calculator.calculate_paycheck` produces a `breakdown.taxable_income`
field on its return value. The route uses `bk.total_pre_tax` (line 1091) to compute its own
subtraction but does NOT read `bk.taxable_income`. Should the route read `bk.taxable_income`
directly so the calibration's effective rates are derived against the same taxable-income value
the breakdown reports, or is the route's inline subtraction the intended source (and the
breakdown's field then potentially divergent)? Phase 3 must verify the two values agree for
identical inputs.

Q-14 (P1-c, 2026-05-15): `dashboard.mark_paid` (`app/routes/dashboard.py:54-139`) was initially
classified out-of-scope by the route-inventory subagent because its body "updates status/amount
in DB and returns a partial row." Spot-checking the response path showed it returns
`dashboard/_bill_row.html`, which re-renders `effective_amount`, `entry_remaining` (via
`_entry_progress_fields`), and the `goal_progress` percent for the just-paid bill -- all
controlled-vocabulary financial figures. The handler was therefore re-classified IN scope. The
question for the developer is whether `mark_paid` and `transactions.mark_done`
(`app/routes/transactions.py:491`) are path-equivalent: both transition a transaction from
projected to done/received, both render an updated cell partial showing `effective_amount`. Are
they intended to produce the same `effective_amount` and `entry_remaining` values for the same
transaction, with the only difference being which endpoint the UI calls? If so, Phase 3 must
verify equivalence; if not, the documented difference belongs in section 0.3 as an expected
behavioral nuance.
