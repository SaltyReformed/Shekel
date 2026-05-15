# Answer Verification Report

Verification of the developer-supplied answers A-01 through A-07 in `09_open_questions.md` and
proposed answers for the open questions Q-08 through Q-14. Every claim cites the file and line
where the code was read.

Conducted: 2026-05-15. Read-only against the working tree on branch
`claude/review-audit-docs-8JdQe`. No source files, tests, or migrations were modified by this
verification.

## 0. Verdict summary

| Item | Verdict | Headline |
| ---- | ------- | -------- |
| A-01 | PARTIALLY ACCURATE | The rule IS the established convention, but 24 monetary `.quantize()` calls plus 1 intentional `ROUND_CEILING`, 3 Jinja arithmetic sites, and 1 JS arithmetic site already violate it. |
| A-02 | ACCURATE | Two minor: `settle_from_entries` ends at line 168 not 169; the `entry_sum / estimated_amount` cell render is gated on `status_id == STATUS_PROJECTED`. |
| A-03 | ACCURATE | No caveats; `is_override` is a single uniform boolean and both engines skip on it unconditionally. |
| A-04 | ACCURATE | Minor docstring drift at `amortization_engine.py:855`; the code walks for the last `is_confirmed` row, not "to today's date". |
| A-05 | PARTIAL | The actual count is 16, not 8. The 8 fixed-rate ELSE branches use a different formula. `app/routes/debt_strategy.py:127` is a 16th unconditional site missed by both A-05 and Q-09. |
| A-06 | ACCURATE | Minor: the escrow loop spans 305-319 with the reassignment at 319; the pipeline is fully connected end to end. |
| A-07 | ACCURATE | The discrete branch further sub-splits: template-linked discrete sets `is_override = True`; ad-hoc discrete (`template_id IS NULL`) does NOT. |
| Q-08 | PROPOSED | Current code is interpretation (1) "Budget = what you allocated"; developer must confirm intent. No test exercises (done, actual_amount set, entries present). |
| Q-09 | PROPOSED | 16 call sites total, not 14. Phase 3 should verify all 16, not just the 8 ARM branches. |
| Q-10 | PROPOSED | Subtotal is computed in 4 places with divergent semantics. Even grid vs balance_calculator differ on expense amount (`effective_amount` vs `_entry_aware_amount`). |
| Q-11 | PROPOSED | Real divergence. The `/accounts/<id>/loan` dashboard renders `params.current_principal` directly; refinance uses `proj.current_balance`. They diverge for fixed-rate loans with confirmed payments. |
| Q-12 | PROPOSED | Three paths answer different questions. Two real bugs surfaced: expired-template inclusion in `compute_committed_monthly`, mortgage double-counting risk. |
| Q-13 | PROPOSED | Two formulas; diverge by design when stub-gross differs from profile-gross. One real bug: percentage-based pre-tax deductions use the wrong base when calibration is invoked. |
| Q-14 | PROPOSED | NOT path-equivalent. `mark_done` auto-settles entries via `settle_from_entries`; `mark_paid` does not. Envelope-tracked transactions with entries produce different `actual_amount` and `effective_amount` across the two endpoints. |

## 1. Verification of A-01 through A-07

### A-01: Rounding rule, PARTIALLY ACCURATE

The convention IS established. `ROUND_HALF_UP` appears 109 times across 19 files; every service
that explicitly names a rounding mode uses it. The developer's secondary claim that the rule is
undocumented is verified: zero matches for `ROUND_HALF_UP`, `quantize`, or `cent` in
`CLAUDE.md`, `docs/coding-standards.md`, or `docs/testing-standards.md`.

The "every monetary boundary" claim is not literally true. Concrete violations:

**24 monetary `.quantize()` calls without `rounding=ROUND_HALF_UP`** (default is
`ROUND_HALF_EVEN`, banker's rounding):

- `app/services/investment_projection.py:93, 96, 159`
- `app/services/savings_dashboard_service.py:266, 872, 873`
- `app/services/retirement_dashboard_service.py:197, 211, 214, 240, 390`
- `app/routes/investment.py:131, 223, 226, 319, 458, 535, 538, 580, 585, 586, 589, 670`
- `app/routes/loan.py:968` (`committed_interest_saved`)

**1 intentional non-`ROUND_HALF_UP`** for money:

- `app/services/savings_goal_service.py:462-463` uses `ROUND_CEILING` so "the user contributes
  at least enough" (documented in the function's docstring at line 438).

**3 templates doing arithmetic on money** (also violates `docs/coding-standards.md` "Templates
are for display, not computation"):

- `app/templates/loan/_schedule.html:55` (payment + escrow + extra, summed as floats)
- `app/templates/loan/_payoff_results.html:72` (monthly_payment + required_extra in floats)
- `app/templates/loan/_escrow_list.html:37` (annual / 12 in float)

Note also: `app/templates/grid/_transaction_cell.html:21` and `app/templates/grid/_mobile_grid.html:96, 183`
compute `remaining = t.estimated_amount - es.total` in Jinja. Decimal subtraction preserves
precision, so this is not a rounding violation, but it is a template-arithmetic violation per
the coding standard.

**1 JS file doing monetary arithmetic**:

- `app/static/js/retirement_gap_chart.js:24-25` (`var covered = pension + investment; var
  remaining = Math.max(0, preRetirement - covered);`)

**No centralized `round_money()` helper exists** anywhere in the codebase. Every service
redeclares its own `TWO_PLACES = Decimal("0.01")` constant locally. This is exactly the
substrate that produces the drift.

**Recommended action.** Either (a) accept A-01 as the canonical rule and accept the 24+5
violations as Phase 3 / Phase 6 findings, or (b) revise A-01 to acknowledge the violations
explicitly so Phase 3 starts with a documented list rather than discovering them piecemeal.
Either way, the absence of a centralized money-rounding helper is a candidate Phase 6 DRY
finding.

### A-02: Envelope semantics, ACCURATE

Each of the developer's seven evidence bullets verified:

- `app/services/carry_forward_service.py:275` reads `elif txn.template is not None and
  txn.template.is_envelope:` and routes to `envelope_txns`. The handler
  `_settle_source_and_roll_leftover` calls `transaction_service.settle_from_entries(source_txn)`
  at `carry_forward_service.py:896`.
- `app/services/transaction_service.py:38-168` is `settle_from_entries`. Three column writes at
  lines 149-153: `status_id`, `paid_at`, `actual_amount`. No assignment to `pay_period_id`. (The
  developer cited "38-169"; the function actually ends at 168, the file is 168 lines.)
- `app/services/carry_forward_service.py:891-894` bumps `target_row.estimated_amount += leftover`
  and sets `target_row.is_override = True`.
- `app/templates/grid/_transaction_cell.html:42-44` renders `{{ "{:,.0f}".format(es.total) }} /
  {{ "{:,.0f}".format(t.estimated_amount) }}`. The render is gated on `show_progress` at line 19,
  which requires `status_id == STATUS_PROJECTED`. The bumped target canonical satisfies this
  gate in the steady state.
- Zero matches for `carried_from_period_id` in `app/`, `migrations/`, or `tests/`.
- `app/services/grid_aggregation.py` does not exist; nor does any file matching
  `grid_aggregation*` anywhere in the repo.
- Zero matches for `EnvelopeCell` in `app/`, `migrations/`, or `tests/`.

The end-user observation ("source row stays in source period showing $65 with a Done badge") is
consistent with the code: `settle_from_entries` flips status to DONE and never touches
`pay_period_id`; the template at `_transaction_cell.html:59-60` renders the `badge-done`
checkmark when `t.status.is_settled` is True.

**Recommended action.** Add the two minor notes (line range, display gating) to A-02 if precision
matters; otherwise the answer can stand as written.

### A-03: Recurrence skip rule, ACCURATE

- `app/services/recurrence_engine.py:128` reads `if existing_txn.is_override:` and sets
  `should_skip = True; break`. No "carried" sub-condition; the predicate is uniform.
- `app/services/transfer_recurrence.py:97` reads `if xfer.is_override:` and sets `should_skip =
  True; break`. Identical structure.
- The `is_override` column on `SoftDeleteOverridableMixin` (`app/models/mixins.py:65-103`) is a
  single boolean. The schema has no provenance metadata; "carried-only" vs "manual" cannot be
  represented at the row level even if a future code change wanted to differentiate.
- Zero matches for `carried_from_period_id`, `carried_only`, or `carried_override` anywhere in
  `app/` or `migrations/`.
- Option F's "bumps in place" claim verified at `carry_forward_service.py:887-896` and the
  docstring at `:802-810, :825-829`. The bumped canonical IS the canonical; no sibling row is
  created.

**Recommended action.** No changes needed.

### A-04: ARM vs fixed principal source, ACCURATE

All four cited locations verified:

- `app/services/amortization_engine.py:977-984` branches on `is_arm` at line 977. ARM sets
  `cur_balance = current_principal` at line 978. Fixed-rate walks `for row in reversed(schedule):
  if row.is_confirmed: cur_balance = row.remaining_balance; break` at lines 980-984, falling back
  to `current_principal` if no confirmed payments exist.
- `app/services/amortization_engine.py:848-861` is the `LoanProjection` dataclass with a
  docstring documenting the dual policy.
- `app/services/savings_dashboard_service.py:373` reads `current_bal = proj.current_balance`.
- `app/services/year_end_summary_service.py:1465-1469` anchors `anchor_bal = Decimal(str(
  params.current_principal)) if params.is_arm else None`, passed to `generate_schedule` at line
  1481.

**Minor finding.** The `LoanProjection` docstring at line 855 says fixed-rate `current_balance`
is "derived from the schedule by walking to today's date". The actual implementation (lines
980-984) walks the schedule looking for the **last `is_confirmed` row**, not a date walk. The
inline comment at 971-976 acknowledges this is intentional: walking to today's date "would pick
up theoretical contractual rows that may not match reality when the user hasn't recorded
payments." The dataclass docstring is loosely accurate; the inline comment is the precise truth.
Documentation drift, not a logic bug.

**Recommended action.** Update the `LoanProjection` docstring at line 855 in a future maintenance
sweep (out of scope for the audit itself). A-04 can stand as written.

### A-05: `calculate_monthly_payment` call sites, PARTIAL

All 8 enumerated sites exist and use the formula
`calculate_monthly_payment(current_principal_like, current_rate, remaining_months)`. The
W-048 invariant holds for those 8.

The claim "Eight call sites compute it" is wrong about the total. The actual count is 16
call sites in `app/`, plus 1 definition at `amortization_engine.py:178`.

**The full inventory:**

| File:line | Role | Inputs | Class |
| --------- | ---- | ------ | ----- |
| `amortization_engine.py:178` | DEFINITION | (principal, rate, months) | n/a |
| `amortization_engine.py:436` | IF / `using_contractual=True` | `(original_principal, annual_rate, term_months)` | Fixed-rate contractual |
| `amortization_engine.py:440` | ELSE | `(current_principal, annual_rate, remaining_months)` | ARM / re-amortize |
| `amortization_engine.py:491` | In-loop (anchor reset) | `(anchor_balance, current_annual_rate, months_left)` | ARM anchor |
| `amortization_engine.py:512` | In-loop (rate change) | `(balance, current_annual_rate, months_left)` | ARM rate change |
| `amortization_engine.py:693` | IF / `original_principal is not None and not has_rate_changes` | `(original_principal, annual_rate, term_months)` | Fixed-rate contractual |
| `amortization_engine.py:697` | ELSE | `(current_principal, annual_rate, remaining_months)` | ARM / re-amortize |
| `amortization_engine.py:952` | IF / `is_arm and remaining > 0` | `(current_principal, rate, remaining)` | ARM |
| `amortization_engine.py:957` | ELSE | `(orig_principal, rate, params.term_months)` | Fixed-rate contractual |
| `balance_calculator.py:225` | IF / `is_arm` | `(loan_params.current_principal, annual_rate, remaining)` | ARM |
| `balance_calculator.py:231` | ELSE | `(loan_params.original_principal, annual_rate, loan_params.term_months)` | Fixed-rate contractual |
| `loan_payment_service.py:251` | IF / `params.is_arm` | `(current_principal, interest_rate, remaining)` | ARM |
| `loan_payment_service.py:256` | Trailing return (implicit else) | `(original_principal, interest_rate, term_months)` | Fixed-rate contractual |
| `routes/debt_strategy.py:127` | UNCONDITIONAL | `(real_principal, rate, remaining)` | Both / ARM formula on every loan |
| `routes/loan.py:1102` | UNCONDITIONAL (refinance preview) | `(refi_principal, refi_rate, refi_term)` | New-loan terms by design |
| `routes/loan.py:1225` | IF / `params.is_arm` | `(current_principal, interest_rate, remaining)` | ARM |
| `routes/loan.py:1231` | ELSE | `(original_principal, interest_rate, term_months)` | Fixed-rate contractual |

**Three concerns.**

1. **`debt_strategy.py:127` is the 16th site, missed by both A-05 and Q-09.** It applies the
   ARM formula `(current_principal, rate, remaining)` to every loan, fixed-rate or ARM. For a
   partially-paid fixed-rate loan this produces a payment lower than the contractual one. Intent
   unclear; could be deliberate ("minimum to clear current balance") or an oversight.
2. **Pairs 1 and 2 (`amortization_engine.py:436/440` and `:693/697`) discriminate on
   caller-supplied state**, not the `is_arm` column. The predicate is `original_principal is not
   None and term_months is not None and not has_rate_changes` (line 432-434) and `original_principal
   is not None and not has_rate_changes` (line 692). A caller that forgets to pass `original_principal`
   for a fixed-rate loan would silently route through the ARM branch. The other four pairs (`:952/957`,
   `balance_calculator.py:225/231`, `loan_payment_service.py:251/256`, `routes/loan.py:1225/1231`)
   discriminate on the `is_arm` model column.
3. **`amortization_engine.py:952` corner case**: the predicate `if is_arm and remaining > 0`
   routes a fully-paid ARM (`remaining <= 0`) through the fixed-rate ELSE at `:957`, using
   `orig_principal` and `term_months`. For a paid-off ARM this produces a meaningless contractual
   figure.

**Recommended action.** Revise A-05 to acknowledge 16 sites, identify the 8 fixed-rate ELSE
branches as using `(original_principal, rate, term_months)` (a different formula), and flag the
three concerns above for Phase 3.

### A-06: Year-end mortgage interest pipeline, ACCURATE

All cited locations verified:

- `app/services/loan_payment_service.py:263-353` is `prepare_payments_for_engine`. The escrow
  subtraction loop spans lines 305-318; the reassignment `sorted_payments = adjusted` is on line
  319 (the developer cited 305-318, which is the loop body without the reassignment, faithful).
- Biweekly redistribution is at lines 321-351.
- `app/services/year_end_summary_service.py:380-408` is `_compute_mortgage_interest`. The
  aggregation predicate `row.payment_date.year == year` is at line 405.
- `tests/test_services/test_year_end_summary_service.py:1399` asserts `Decimal("15356.80")`.

End-to-end pipeline traced: `Transaction (shadow income)` -> `get_payment_history`
(`loan_payment_service.py:156-230`) -> `prepare_payments_for_engine`
(`loan_payment_service.py:263-353`, called from `load_loan_context` at `:122-125`) ->
`LoanContext.payments` -> `amortization_engine.generate_schedule`
(called from `year_end_summary_service._generate_debt_schedules` at `:1471-1483`) ->
`AmortizationRow[]` -> `_compute_mortgage_interest` -> dollar total.

**Two observations.**

1. The test at `:1399` calls `compute_year_end_summary` end-to-end, but creates zero payments.
   So `prepare_payments_for_engine` is invoked but short-circuits at the
   `if not payments: return payments` guard at `loan_payment_service.py:297-298`. The actual
   escrow-subtraction and biweekly-redistribution reshaping is covered by dedicated unit tests
   in `tests/test_services/test_loan_payment_service.py:488+`.
2. **Two paths call `generate_schedule` directly without going through `prepare_payments_for_engine`:**
   `savings_dashboard_service.py:471, 488` (paid-off check) and `routes/debt_strategy.py:175, 181`
   (debt-strategy current-principal). These bypass preprocessing. They do not affect the year-end
   mortgage-interest pipeline (A-06's scope), but they could produce incorrect schedules in their
   own contexts if escrow-inclusive payments are present.

**Recommended action.** Note the bypasses for a Phase 3 / Phase 6 scan. A-06 can stand as
written.

### A-07: Three-branch carry-forward partition, ACCURATE

The three-way partition at `carry_forward_service.py:272-278` is exactly as the developer
described:

- transfer (`txn.transfer_id is not None`) -> `shadow_txns`
- envelope (`txn.template is not None and txn.template.is_envelope`) -> `envelope_txns`
- discrete (everything else) -> `discrete_txns`

Lines 415-416 set `pay_period_id = target_period_id` and `is_override = True` via a bulk UPDATE
on the discrete branch.

**One nuance the answer did not surface.** The discrete branch further sub-splits at runtime:

- **Template-linked discrete** (`template_id IS NOT NULL`): bulk UPDATE sets `pay_period_id`,
  `is_override = True`, `version_id += 1` at `carry_forward_service.py:405-421`.
- **Ad-hoc discrete** (`template_id IS NULL`): bulk UPDATE sets only `pay_period_id` and
  `version_id += 1` at `carry_forward_service.py:423-438`. It does NOT set `is_override`.

Reason: ad-hoc rows are not constrained by the partial unique index
`idx_transactions_template_period_scenario` (comment at lines 382-384). The ad-hoc sub-branch
intentionally omits the flip.

**Recommended action.** Add the sub-split note to A-07 for completeness.

## 2. Proposed answers for Q-08 through Q-14

These are auditor-proposed answers pending developer confirmation. Each is grounded in what the
code DOES; intent is for the developer to confirm.

### Q-08, proposed answer

The current code implements interpretation (1): "Budget = what you allocated, anchor on
`estimated_amount`".

- `app/services/dashboard_service.py:203-246` is `_entry_progress_fields`.
- Line 239: `remaining = compute_remaining(txn.estimated_amount, txn.entries)`. Unconditional on
  status.
- Line 245: `"entry_over_budget": total > txn.estimated_amount`. Unconditional on status.
- `app/services/entry_service.py:405-425` is `compute_remaining(estimated_amount, entries)`.
  Formula: `estimated_amount - sum(e.amount for e in entries)`. The function does not receive
  the transaction, so it cannot switch behavior on status.

For a done txn with `actual_amount = $100`, `estimated_amount = $120`, entries summing to $80:
`entry_remaining = $40`, `entry_over_budget = False`. The `$100` `actual_amount` is not
consulted. Separately, `bill["amount"] = txn.effective_amount` (`dashboard_service.py:191`)
returns `$100` (the `Transaction.effective_amount` property at `app/models/transaction.py:222-245`
returns `actual_amount` when non-null). The displayed bill row therefore has internally
inconsistent anchors against a single user mental model.

**No test asserts this case.** `tests/test_routes/test_dashboard_entries.py:45-88` hard-codes
`status_id = projected.id` in the helper that builds entry-tracked transactions; no fixture
exercises `(status=done, actual_amount set, entries present)`.

**The audit cannot determine intent from the code alone.** If interpretation (1) is the intent,
Phase 3 records the current behavior as AGREE. If interpretation (2) is the intent, the code is a
DIVERGE finding and `compute_remaining` should accept the transaction and switch the base on
`is_settled`. Note also the cross-anchor inconsistency between `bill.amount` (uses
`actual_amount`) and `bill.entry_remaining` (uses `estimated_amount`) within the same bill row.

### Q-09, proposed answer

The actual call site count is 16 in `app/`, plus 1 definition. A-05 listed 8 (the ARM
branches); Q-09 listed 7 additional (despite saying "six") and missed
`app/routes/debt_strategy.py:127`.

Catalog: see the A-05 table above.

Phase 3 should verify all 16 call sites against the per-loan invariant, not just the 8 ARM
branches. The 8 fixed-rate ELSE branches use a different formula
`(original_principal, rate, term_months)` and would produce a different payment value for
partially-paid fixed-rate loans. Verifying only the ARM branches answers "does every ARM site
use the ARM formula" tautologically and would not catch the fluctuation symptom if it originates
from a fixed-rate branch firing when the ARM branch was expected (or vice versa).

**Fallback branch input guarantees.**

- Pairs 3-6 (`is_arm`-discriminated): the ELSE branches read `LoanParams.original_principal` and
  `params.term_months`, required model columns wrapped in `Decimal(str(...))`. Risk axis is at
  the discriminator (the `is_arm` column being correct), not at the inputs.
- Pairs 1-2 (`amortization_engine.py:436/440` and `:693/697`): discriminator is caller-supplied
  state. A caller that forgets to pass `original_principal` on a fixed-rate loan would silently
  route through the ARM branch.
- Corner case: `amortization_engine.py:952` (`if is_arm and remaining > 0`) routes a fully-paid
  ARM (`remaining <= 0`) through the fixed-rate ELSE at `:957`, using `orig_principal` and
  `term_months`.

### Q-10, proposed answer

The current code computes per-period subtotal **in the route inline** at
`app/routes/grid.py:263-279` (Projected-only, `is_income` + `is_expense` split, `effective_amount`).
Three other services compute superficially-similar "period totals" with **divergent semantics**.
None of them is identical to the grid's inline subtotal.

| Path | File:line | Status filter | Type filter | Expense amount source |
| ---- | --------- | ------------- | ----------- | --------------------- |
| `grid.index` inline | `grid.py:263-279` | Projected only | `is_income` and `is_expense` | `effective_amount` |
| `dashboard._sum_settled_expenses` | `dashboard_service.py:607-633` | DONE, RECEIVED, SETTLED | Expense only | `abs(effective_amount)` |
| `balance_calculator._sum_remaining` / `_sum_all` | `balance_calculator.py:389-451` | Projected only | `is_income` and `is_expense` | `_entry_aware_amount(txn)` |
| `spending_trend_service.period_totals` | `spending_trend_service.py:315-322` | various | Expense only | `abs(effective_amount)` |

`_get_spending_comparison` is **opposite** of the grid on status filter (settled vs Projected),
so it cannot agree with the grid by construction. `_sum_remaining` and `_sum_all` agree with the
grid on filters but **differ on the expense amount**: `_entry_aware_amount` at
`balance_calculator.py:292-386` subtracts cleared entry debits when entries are loaded, so for a
projected envelope expense with cleared entries the two paths produce different numbers.

`_sum_remaining` and `_sum_all` are not exposed as per-period subtotals: they feed the
running-balance recurrence at `balance_calculator.py:74-80`. But the expense computation logic
diverges from the grid's, and `selectinload(Transaction.entries)` is in effect for both
(`grid.py:229` and balance calculator), so the divergence is real for the same input rows.

No service-level `period_subtotal` function exists. Grep for `period_subtotal | period_total |
subtotal` finds only `spending_trend_service.py:315` and the template/route consumers of the
`subtotals` dict the grid hands to its template.

**Phase 3 must record** that the grid's inline subtotal and the balance calculator's expense
computation disagree on `(period, Projected, envelope-with-cleared-entries)` inputs, regardless
of which interpretation the developer chooses. This is a DIVERGE the user would see if they
compared the grid's subtotal row to a running balance derived from the balance calculator.

Interpretation (1) "display detail of grid" matches the current code's locality. Interpretation
(2) "shared concept" would require choosing between `effective_amount` and `_entry_aware_amount`
as the canonical expense formula. That decision is the developer's.

### Q-11, proposed answer

The `/accounts/<id>/loan` dashboard does NOT honor the A-04 dual policy on the display side.
This is a real divergence:

- `app/routes/loan.py:405-575` is the dashboard route. It builds `proj = get_loan_projection(...)`
  at line 429.
- The template at `app/templates/loan/dashboard.html:104` renders
  `${{ "{:,.2f}".format(params.current_principal|float) }}` directly, not `proj.current_balance`.
  The projection's principal is computed but not used for the "Current Principal" card.
- `app/routes/loan.py:1027` is `refinance_calculate`. It uses `current_real_principal =
  proj.current_balance` at line 1087 and `refi_principal = current_real_principal +
  closing_costs` at line 1095 when the user leaves the "New Principal" input blank.

For ARM loans the two values coincide (engine line 978 assigns `cur_balance = current_principal`).
For fixed-rate loans with any confirmed payments, `proj.current_balance` is the last
`is_confirmed` row's `remaining_balance` (`amortization_engine.py:980-984`), which may differ
from the stored `params.current_principal`. Only when no confirmed payments exist (the fallback
at line 980) do the two values match.

**Phase 3 finding:** for a fixed-rate loan that has any confirmed payments, the refinance form
prefill does not match the "Current Principal" card on `/accounts/<id>/loan`. The refinance
prefill is the more accurate number (reflects committed payments); the dashboard's display value
is the stored static.

**Recommendation for developer:** decide on the canonical "current balance" for display. Option
A: dashboard template renders `proj.current_balance` (pass it via the existing engine call at
line 429) so prefill matches the on-screen number. Option B: keep the stored value but rename
the dashboard label ("Stored Principal" or similar) so the divergence from refinance is
intentional and visible.

### Q-12, proposed answer

The three paths answer different questions and the code makes no guarantee they agree:

- `obligations.summary` (`app/routes/obligations.py:259-423`): forward-projected monthly from
  active recurring `TransactionTemplate` + `TransferTemplate`. Excludes `ONCE` patterns
  (lines 333-334, 356-357, 378-379). Excludes templates with `end_date < today`
  (lines 335, 358, 380). Calls `amount_to_monthly(amount, pattern_id, interval_n)` at
  lines 338, 361, 383.
- `dashboard_service._compute_cash_runway` (`app/services/dashboard_service.py:375-417`):
  trailing 30-day average from **settled `Transaction` rows** (status in DONE/RECEIVED/SETTLED,
  expense-only). Does NOT consult templates. Returns days of runway, not a monthly equivalent.
- `savings_dashboard_service._compute_debt_summary`
  (`app/services/savings_dashboard_service.py:802-876`): amortization-engine P+I
  (`ad["monthly_payment"]` from `get_loan_projection` at `:362-367`) plus escrow `annual / 12`
  (`escrow_calculator.calculate_monthly_escrow`). Does NOT call `amount_to_monthly` and does NOT
  consult templates.

Cash-runway and debt-summary are conceptually independent from obligations: realised history,
amortization-derived obligation, and forward template projection are different quantities.

The biweekly-to-monthly conversion factor 26/12 is duplicated in three places:
`savings_goal_service.py:17-18` (`_PAY_PERIODS_PER_YEAR` / `_MONTHS_PER_YEAR`),
`savings_dashboard_service.py:170-172` (inline `Decimal("26") / Decimal("12")`), and
`savings_dashboard_service.py:765` (same inline form). Numerically equivalent; not cross-imported.

**Two real bugs surfaced by this question** (out of Q-12 scope but reported per CLAUDE.md
rule 4):

1. **`compute_committed_monthly` does not skip expired templates.**
   `app/services/savings_goal_service.py:287-328` has no `end_date` check, while
   `obligations.summary` does. Consumers (emergency-fund baseline at
   `savings_dashboard_service.py:794`, per-goal contributions at `:700`) include expired-template
   contributions indefinitely.
2. **Mortgage / loan double-counting risk** between `obligations.summary` and
   `_compute_debt_summary`. A user with both a recurring expense template for the mortgage AND a
   loan account with `loan_params` sees the same payment in `/obligations.total_expense_monthly`
   AND in the savings dashboard DTI numerator. No reconciliation guard.

**Contract for Phase 3 verification.** For the same set of active recurring expense templates
with `pattern_id != ONCE` AND `(end_date IS NULL OR end_date >= today)`,
`obligations.summary.total_expense_monthly` MUST equal the sum of `amount_to_monthly` outputs
for those templates. `cash_runway` has no such relationship; `_compute_debt_summary` is
amortization-derived and unrelated to recurrence templates.

### Q-13, proposed answer

The route's inline `taxable = gross - bk.total_pre_tax` at `app/routes/salary.py:1095` and
`bk.taxable_income` at `app/services/paycheck_calculator.py:155-157` do not measure the same
quantity:

- The route uses the form's `data["actual_gross_pay"]` (the user's actual pay stub gross)
  minus the profile-derived `bk.total_pre_tax`.
- The breakdown's `taxable_income` is `bk.gross_biweekly - bk.total_pre_tax`, where
  `bk.gross_biweekly = (annual_salary / pay_periods_per_year).quantize(...)` at
  `paycheck_calculator.py:133-135` (and is floored at zero at `:156-157`).

The two formulas agree only when `actual_gross_pay == bk.gross_biweekly`, which is the trivial
case calibration is designed to detect deviation from. They are different quantities by design.

**The route's intent is the actual-pay-stub-grounded taxable.** The breakdown's `taxable_income`
is grounded in the profile's simulated gross. Calibration's purpose is to derive effective rates
from the user's actual pay stub, so the route should NOT use `bk.taxable_income`.

**However, a real bug surfaced**: `bk.total_pre_tax` includes percentage-based pre-tax
deductions computed against the **profile's** `gross_biweekly`, not against the form's
`actual_gross_pay`. At `paycheck_calculator.py:439-442`:

```
if ded.calc_method_id == calc_method_pct_id:
    amount = (gross_biweekly * amount).quantize(
        TWO_PLACES, rounding=ROUND_HALF_UP
    )
```

When the form's gross differs from the profile's gross (the calibration use case), the
percentage deduction is computed against the wrong base. Concrete example: profile
`annual_salary = $60,000` so `bk.gross_biweekly = $2,307.69`; pay stub
`actual_gross_pay = $2,400.00`; pre-tax 401k is 5% of gross. Then:

- `bk.total_pre_tax = 2,307.69 * 0.05 = $115.38`
- route's `taxable = 2,400.00 - 115.38 = $2,284.62`
- if the percentage were applied to the actual stub gross: `2,400.00 * 0.05 = $120.00`, so
  taxable would be `2,400.00 - 120.00 = $2,280.00`

The derived effective rates absorb this gap. The fix needs developer intent: option A
(recompute pre-tax deductions inline against `actual_gross_pay`), option B (use
`bk.taxable_income / bk.gross_biweekly` as a ratio to scale), option C (status quo and document
the bias).

### Q-14, proposed answer

`mark_paid` (`app/routes/dashboard.py:54-138`) and `mark_done`
(`app/routes/transactions.py:491-629`) are path-similar but **not path-equivalent**. They share
the status policy (RECEIVED for income, DONE for expense, DONE for transfer shadows) and the
same `MarkDoneSchema`, but they diverge on critical behavior:

1. **Envelope-with-entries auto-settle.** `mark_done` calls
   `transaction_service.settle_from_entries(txn)` at `transactions.py:596`, which auto-writes
   `actual_amount = sum(entries)` at `transaction_service.py:153`. `mark_paid` never calls this;
   it only writes `actual_amount` if the form provided it (`dashboard.py:127-128`). For an
   envelope-tracked txn with entries, the two endpoints produce **different `actual_amount`**
   for the same input request, and therefore different `Transaction.effective_amount` (the
   property at `app/models/transaction.py:222-245` returns `actual_amount` if non-null else
   `estimated_amount`). `mark_done` produces `sum(entries)`; `mark_paid` leaves `actual_amount`
   NULL and `effective_amount` returns `estimated_amount`.
2. **Stale-data handling.** `mark_done` catches `StaleDataError` and returns a 409 conflict cell
   at `transactions.py:618`; `mark_paid` does not (only `IntegrityError` at `dashboard.py:132`).
   Concurrent edits raise 409 in one path and likely 500 in the other.
3. **Ownership scope.** `mark_done` allows companions for templates with `companion_visible=True`
   (`_get_accessible_transaction_for_status` at `transactions.py:210-241`). `mark_paid` is
   owner-only (`_get_owned_transaction` at `dashboard.py:181-192`).
4. **Rendered partial and HX-Trigger.** `mark_paid` returns `dashboard/_bill_row.html` with
   `HX-Trigger: dashboardRefresh`; `mark_done` returns `grid/_transaction_cell.html` (via
   `_render_cell` at `transactions.py:628`) with `HX-Trigger: gridRefresh`. The progress display
   gating differs: the bill row gates on `not bill.is_paid` (`_bill_row.html:2-6, 31`); the grid
   cell gates on `status_id == STATUS_PROJECTED` (`_transaction_cell.html:19`). Semantically
   equivalent for a freshly-settled txn (both suppress the progress block).
5. **Logging.** `mark_done` emits an info log; `mark_paid` does not.

The arithmetic for `entry_remaining` is identical in both paths (`estimated_amount -
sum(entries)`); the displayed value differs only by template gating.

**Phase 3 verdict.** The two endpoints produce the same `effective_amount` for transactions that
are not envelope-tracked or that have no entries. They produce **different** `effective_amount`
for envelope-tracked transactions with entries, the case the developer's concern is most likely
to involve.

**Recommendation.** Either align `mark_paid` to call `settle_from_entries` for envelope-tracked
transactions (so the two endpoints agree on `actual_amount` and therefore `effective_amount`),
or document the difference explicitly so future contributors do not assume equivalence.

## 3. Additional findings surfaced by the verification

These are out of the literal scope of each question but surfaced during the verification. Per
CLAUDE.md rule 4 they are reported here so the developer can route them into the audit (as
Phase 3 findings or separate work items).

1. **`compute_committed_monthly` does not skip expired templates**
   (`app/services/savings_goal_service.py:287-328`). Consumers
   (`savings_dashboard_service.py:700, 794`) over-count contributions from templates with
   `end_date < today`. See Q-12.
2. **Mortgage double-counting risk** between `obligations.summary` and `_compute_debt_summary`.
   No reconciliation guard. See Q-12.
3. **`calibrate_preview` percentage-deduction base mismatch**: pre-tax deductions computed
   against profile gross, not form gross (`app/routes/salary.py:1080-1112` and
   `app/services/paycheck_calculator.py:439-442`). See Q-13.
4. **`mark_paid` does not auto-derive `actual_amount` from entries** while `mark_done` does. For
   envelope-tracked transactions with entries, the two endpoints produce different
   `effective_amount` for the same input request. See Q-14.
5. **`mark_paid` does not catch `StaleDataError`** while `mark_done` does. Inconsistent
   concurrent-edit error handling. See Q-14.
6. **`mark_paid` is owner-only**; `mark_done` allows companions. Inconsistent ownership policy
   for the same operation. See Q-14.
7. **`amortization_engine.py:952` corner case**: fully-paid ARM (`remaining <= 0`) routes
   through the fixed-rate ELSE using `orig_principal/term_months`. See A-05 / Q-09.
8. **`debt_strategy.py:127` uses the ARM formula on every loan unconditionally** (16th
   `calculate_monthly_payment` site, missed by both A-05 and Q-09). Intent unclear.
9. **`amortization_engine.py:436/440` and `:693/697` use caller-state discriminator**
   (`using_contractual` = `original_principal` provided AND `term_months` provided AND no
   `rate_changes`) rather than the model's `is_arm` flag. More fragile than the other four
   `is_arm`-discriminated pairs.
10. **`LoanProjection` docstring drift** at `app/services/amortization_engine.py:855`. Says
    "walk to today's date" but the implementation walks for the last `is_confirmed` row.
11. **`/accounts/<id>/loan` dashboard renders `params.current_principal` directly**, bypassing
    the projection it already computes
    (`app/routes/loan.py:429, :557` and `app/templates/loan/dashboard.html:104`). See Q-11.
12. **Three Jinja templates do monetary arithmetic** (violates `docs/coding-standards.md`):
    `app/templates/loan/_schedule.html:55`, `_payoff_results.html:72`, `_escrow_list.html:37`.
    Also `app/templates/grid/_transaction_cell.html:21` and
    `app/templates/grid/_mobile_grid.html:96, 183` (Decimal subtraction; not a rounding
    violation but still template arithmetic).
13. **`app/static/js/retirement_gap_chart.js:24-25` does monetary arithmetic in JS** (violates
    the JS standard "Monetary values in JS are display-only").
14. **No centralized `round_money()` helper exists.** Every service redeclares
    `TWO_PLACES = Decimal("0.01")` locally. This is the substrate for the A-01 drift.
15. **26/12 biweekly-to-monthly conversion factor is duplicated** in three places without
    cross-import: `savings_goal_service.py:17-18`, `savings_dashboard_service.py:170-172, :765`.
16. **Two `generate_schedule` callers bypass `prepare_payments_for_engine`**:
    `savings_dashboard_service.py:471, 488` (paid-off check) and `routes/debt_strategy.py:175,
    181` (debt-strategy current-principal). Does not affect mortgage-interest aggregation, but
    could produce incorrect schedules in their own contexts if escrow-inclusive payments are
    present.
17. **Discrete carry-forward sub-split**: template-linked sets `is_override = True`; ad-hoc
    (`template_id IS NULL`) does NOT. Intentional but worth recording per A-07.

## 4. Recommended next steps

Before Phase 1 inventory consolidates further or Phase 3 begins, resolve the items below. Each
one is small individually; together they determine whether the audit verifies the right
invariants.

1. Decide whether A-01 stands as the canonical rule (with the 24+5 violations as Phase 3 / 6
   findings) or is amended to acknowledge them explicitly.
2. Revise A-05 to acknowledge 16 sites and identify the 8 fixed-rate ELSE branches as a separate
   formula. Flag `debt_strategy.py:127` for intent confirmation. Flag the
   `amortization_engine.py:952` corner case (fully-paid ARM).
3. Add the discrete sub-split note to A-07 (template-linked vs ad-hoc).
4. Answer Q-08 (which interpretation of the entry-tracked done-state display is intended).
5. Decide on the Q-10 architectural shape (display-only vs shared concept). Either way, Phase 3
   records the grid-vs-balance-calculator expense divergence for envelope-with-cleared-entries.
6. Decide on the Q-11 dashboard label (`proj.current_balance` vs renamed stored value).
7. Triage the 17 additional findings: which become Phase 3 findings, which are deferred to
   separate work items.
