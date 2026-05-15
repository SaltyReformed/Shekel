# Phase 1: Calculation Surface Inventory

Layer-by-layer enumeration. Each Phase 1 session appends one layer.
Reader should be able to grep this file by concept token and find every
location that produces or consumes that concept.

Audit plan: @docs/financial_calculation_audit_plan.md
Priors: @docs/audits/financial_calculations/00_priors.md
Open questions: @docs/audits/financial_calculations/09_open_questions.md
(Q-01 through Q-07 carry developer answers A-01 through A-07 dated
2026-05-13; cited inline below where a column or property maps to a
resolved cross-plan answer.)

Session ledger:
- 1.5 Models and 1.6 DB aggregates: P1-a, 2026-05-15.
- 1.1 Service layer: P1-b, 2026-05-15.
- 1.2 Route layer: P1-c, 2026-05-15.
- 1.3 Template layer + 1.4 Static/JS: P1-d (pending).
- 1.7 Wrap-up: P1-e (pending).

## Controlled vocabulary

Tokens from Appendix A (section 12) of the audit plan. Each addition
beyond the starter list cites the file:line where the concept appears.

```text
checking_balance
account_balance
projected_end_balance
period_subtotal
loan_principal_real
loan_principal_stored
loan_principal_displayed
monthly_payment
principal_paid_per_period
interest_paid_per_period
escrow_per_period
payoff_date
months_saved
total_interest
interest_saved
apy_interest
growth
employer_contribution
contribution_limit_remaining
ytd_contributions
paycheck_gross
paycheck_net
taxable_income
federal_tax
state_tax
fica
pre_tax_deduction
post_tax_deduction
transfer_amount
effective_amount
goal_progress
emergency_fund_coverage_months
dti_ratio
net_worth
savings_total
debt_total
chart_balance_series
year_summary_jan1_balance
year_summary_dec31_balance
year_summary_principal_paid
year_summary_growth
year_summary_employer_total
```

Additions during 1.5 (none). The starter set covers every numeric column
in `app/models/`. Some columns (rate inputs, inflation, calibration
effective rates) map onto a downstream concept token rather than a
column-level token; this is noted in the per-column rows.

Additions during 1.1 (P1-b, 2026-05-15):

- `pension_benefit_annual` -- annual defined-benefit pension amount
  produced by `pension_calculator.calculate_benefit` at
  `app/services/pension_calculator.py:31-66` (`PensionBenefit.annual_benefit`
  field on the returned dataclass). The starter vocabulary lacked a
  pension-specific token; required for Phase 2 catalog entry.
- `pension_benefit_monthly` -- monthly defined-benefit pension amount
  produced at `app/services/pension_calculator.py:65-66` and consumed by
  `retirement_dashboard_service.compute_gap_data` and
  `retirement_gap_calculator.calculate_gap` as the
  `monthly_pension_income` argument
  (`app/services/retirement_gap_calculator.py:39`).
- `loan_remaining_months` -- count of unfulfilled loan months returned
  by `amortization_engine.calculate_remaining_months` at
  `app/services/amortization_engine.py:128-176`. Distinct from
  `payoff_date` (the calendar date) because consumers display the
  integer count separately. Used as an input to
  `calculate_monthly_payment` at the same module.
- `cash_runway_days` -- integer days of runway computed by
  `dashboard_service._compute_cash_runway` at
  `app/services/dashboard_service.py:375` (current_balance divided by
  daily-average paid expenses from a 30-day window). Distinct from
  `emergency_fund_coverage_months` because the inputs and time unit
  differ. Phase 2 must decide whether to fold this into
  `emergency_fund_coverage_months` or keep them separate.

Additions during 1.2 (P1-c, 2026-05-15):

- `entry_sum_total` -- per-transaction sum of cleared+uncleared entries
  produced for tracked transactions by `entry_service.compute_entry_sums`
  (returned as a dict per `entries.py:_render_entry_list`,
  `companion.py:_build_entry_data` at lines 50-57, and consumed in the grid
  via `build_entry_sums_dict` at `transactions.py:88`). Distinct from
  `effective_amount` because entries are the user-facing inputs that feed
  the `effective_amount` computation on the Transaction model.
- `entry_remaining` -- per-transaction remaining budget computed by
  `entry_service.compute_remaining(estimated_amount, entries)` (cited in
  `entries.py:104-106` and `companion.py:52`). Anchors the
  "remaining budget" display on cells; Phase 3 will compare against
  `effective_amount` semantics.
- `paycheck_breakdown` -- `PaycheckBreakdown` dataclass produced by
  `paycheck_calculator.calculate_paycheck` (see section 1.1) and rendered
  on the salary breakdown / projection / list pages. Single-token shorthand
  for the bundle `paycheck_gross + paycheck_net + federal_tax + state_tax
  + fica + pre_tax_deduction + post_tax_deduction + employer_contribution`;
  the route layer treats the breakdown as one rendered unit, so the audit
  needs a single token to track it across pages.
- `chart_date_labels` -- string-formatted date labels (e.g. "May 2026")
  emitted by `investment.dashboard` (`app/routes/investment.py:242-246`),
  `investment.growth_chart` (`:534`), and the loan chart helper at
  `loan.py:460`. Display-only formatting of period start_date but is
  rendered next to `chart_balance_series`, so the audit tracks it
  separately to flag any divergence between the two parallel arrays.
- `transfer_amount_computed` -- the route-derived transfer amount for new
  recurring payment transfers (loan and investment routes). Distinct from
  `transfer_amount` (which describes Transfer.amount as stored): the
  computed flavor is the route's pre-fill value that defaults to a
  derived monthly payment when the user does not override; cited at
  `loan.py:1213-1241` and `investment.py:668-670`. Phase 3 compares the
  computed flavor against the stored amount.

## 1.5 Models and computed properties

24 model files read in full (P1-a, Explore subagent, very thorough).
40 classes inventoried, 113 numeric columns, 6 `@property` accessors.
NO `@hybrid_property` or `@cached_property` in scope.

For each row, the "Concept token" column gives the financial concept the
column or property feeds. Non-financial columns (sort orders, version
counters, period indexes, dependent counts, FICA day-counts) are recorded
as `-` so the inventory is exhaustive across numeric columns; Phase 6's
SOLID audit needs the full surface.

CHECK constraint citations are line numbers in the same model file
where `db.CheckConstraint(...)` appears inside `__table_args__`. When a
constraint exists in a migration but not in the model file, the cell
reads `MIGRATION (not in model)`. The rebuild migration
(`migrations/versions/a5be2a99ea14_rebuild_audit_infrastructure.py`) is
the canonical source for audit-trigger attachment but is not a CHECK
source for these columns.

### `app/models/account.py`

161 lines. Classes: `Account`, `AccountAnchorHistory`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| Account.current_anchor_balance | account.py:51 | Numeric(12, 2) | True | - | MIGRATION (not in model) | account_balance |
| Account.sort_order | account.py:55 | Integer | False | db.text("0") | - | - |
| Account.version_id | account.py:66 | Integer | False | "1" | account.py:35 | - |
| AccountAnchorHistory.anchor_balance | account.py:152 | Numeric(12, 2) | False | - | MIGRATION (not in model) | account_balance |

Computed properties: none.

### `app/models/calibration_override.py`

177 lines. Classes: `CalibrationOverride`, `CalibrationDeductionOverride`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| CalibrationOverride.actual_gross_pay | calibration_override.py:80 | Numeric(10, 2) | False | - | calibration_override.py:28 | paycheck_gross |
| CalibrationOverride.actual_federal_tax | calibration_override.py:81 | Numeric(10, 2) | False | - | calibration_override.py:32 | federal_tax |
| CalibrationOverride.actual_state_tax | calibration_override.py:82 | Numeric(10, 2) | False | - | calibration_override.py:36 | state_tax |
| CalibrationOverride.actual_social_security | calibration_override.py:83 | Numeric(10, 2) | False | - | calibration_override.py:40 | fica |
| CalibrationOverride.actual_medicare | calibration_override.py:84 | Numeric(10, 2) | False | - | calibration_override.py:44 | fica |
| CalibrationOverride.effective_federal_rate | calibration_override.py:89 | Numeric(12, 10) | False | - | calibration_override.py:54 | federal_tax (input) |
| CalibrationOverride.effective_state_rate | calibration_override.py:90 | Numeric(12, 10) | False | - | calibration_override.py:58 | state_tax (input) |
| CalibrationOverride.effective_ss_rate | calibration_override.py:91 | Numeric(12, 10) | False | - | calibration_override.py:62 | fica (input) |
| CalibrationOverride.effective_medicare_rate | calibration_override.py:92 | Numeric(12, 10) | False | - | calibration_override.py:66 | fica (input) |
| CalibrationDeductionOverride.actual_amount | calibration_override.py:164 | Numeric(10, 2) | False | - | calibration_override.py:136 | pre_tax_deduction |

The four Numeric(10, 2) money columns deviate from the `Numeric(12, 2)`
project standard (E-14). Flag for Phase 6 DRY/SOLID: precision drift on
calibration tables, which were added later than the canonical money
columns. Whether 10,2 is correct (calibration values are bounded by a
single paycheck) or whether the standard should be enforced uniformly
is a Phase 6 question, not a 1.5 finding.

Computed properties: none.

### `app/models/category.py`

47 lines. Classes: `Category`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| Category.sort_order | category.py:32 | Integer | False | db.text("0") | - | - |

Computed properties:

| Class.property | file:line | Decorator | Returns | Formula (one-line) | Reads from | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| Category.display_name | category.py:40 | @property | str (inferred) | f"{self.group_name}: {self.item_name}" | group_name, item_name | - |

### `app/models/interest_params.py`

73 lines. Classes: `InterestParams`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| InterestParams.apy | interest_params.py:60 | Numeric(7, 5) | False | "0.04500" | interest_params.py:34 | apy_interest |

Computed properties: none.

### `app/models/investment_params.py`

99 lines. Classes: `InvestmentParams`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| InvestmentParams.assumed_annual_return | investment_params.py:80 | Numeric(7, 5) | False | db.text("0.07000") | investment_params.py:22 | growth |
| InvestmentParams.annual_contribution_limit | investment_params.py:84 | Numeric(12, 2) | True | - | investment_params.py:32 | contribution_limit_remaining (input) |
| InvestmentParams.contribution_limit_year | investment_params.py:85 | Integer | True | - | - | - |
| InvestmentParams.employer_flat_percentage | investment_params.py:90 | Numeric(5, 4) | True | - | investment_params.py:40 | employer_contribution (input) |
| InvestmentParams.employer_match_percentage | investment_params.py:91 | Numeric(5, 4) | True | - | investment_params.py:53 | employer_contribution (input) |
| InvestmentParams.employer_match_cap_percentage | investment_params.py:92 | Numeric(5, 4) | True | - | investment_params.py:64 | employer_contribution (input) |

Computed properties: none.

### `app/models/loan_features.py`

144 lines. Classes: `RateHistory`, `EscrowComponent`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| RateHistory.interest_rate | loan_features.py:75 | Numeric(7, 5) | False | - | loan_features.py:44 | monthly_payment (input) |
| EscrowComponent.annual_amount | loan_features.py:126 | Numeric(12, 2) | False | - | loan_features.py:104 | escrow_per_period (annual input) |
| EscrowComponent.inflation_rate | loan_features.py:127 | Numeric(5, 4) | True | - | loan_features.py:111 | growth (escrow inflation input) |

`RateHistory.interest_rate` is the ARM-anchor source flagged by C-04 in
the priors; Phase 3 must compare which entry points consume RateHistory
versus the static `LoanParams.interest_rate`.

Computed properties: none.

### `app/models/loan_params.py`

74 lines. Classes: `LoanParams`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| LoanParams.payment_day | loan_params.py:58 | Integer | False | - | loan_params.py:24 | - |
| LoanParams.original_principal | loan_params.py:53 | Numeric(12, 2) | False | - | loan_params.py:28 | loan_principal_stored |
| LoanParams.current_principal | loan_params.py:54 | Numeric(12, 2) | False | - | loan_params.py:32 | loan_principal_stored |
| LoanParams.interest_rate | loan_params.py:55 | Numeric(7, 5) | False | - | loan_params.py:36 | monthly_payment (input) |
| LoanParams.term_months | loan_params.py:56 | Integer | False | - | loan_params.py:40 | - |
| LoanParams.arm_first_adjustment_months | loan_params.py:60 | Integer | True | - | - | - |
| LoanParams.arm_adjustment_interval_months | loan_params.py:61 | Integer | True | - | - | - |

`LoanParams.current_principal` is the source-of-truth column flagged by
C-03/C-04 in the priors and by E-03 in the developer expectations. Phase
4 (source-of-truth audit) is the dedicated spot for this column. A-04
(09_open_questions.md:93-103) resolves the C-03/C-04 ARM-vs-fixed-rate
split: ARM loans use stored `current_principal` directly
(`amortization_engine.py:977-985`,
`savings_dashboard_service.py:373`,
`year_end_summary_service.py:1465-1469`); fixed-rate loans walk the
schedule from origination using confirmed `PaymentRecord` rows. The
column is therefore AUTHORITATIVE for ARM and CACHED-for-display for
fixed-rate; Phase 4 records the dual classification.

`LoanParams.interest_rate` (line 55) is the static rate; ARM loans
override it via `RateHistory.interest_rate`
(`loan_features.py:75`). A-05 (09_open_questions.md:113-125) confirms
the eight call sites that compute ARM monthly_payment from
`(current_principal, current_rate, remaining_months)`; Phase 3 must
verify all eight sites resolve `current_rate` against the same
authority for the same loan-on-date.

Computed properties: none.

### `app/models/mixins.py`

103 lines. Classes: `TimestampMixin`, `CreatedAtMixin`,
`SoftDeleteOverridableMixin`. No numeric columns or computed numeric
properties of audit interest.

### `app/models/pay_period.py`

50 lines. Classes: `PayPeriod`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| PayPeriod.period_index | pay_period.py:31 | Integer | False | - | pay_period.py:20 | - |

Computed properties:

| Class.property | file:line | Decorator | Returns | Formula (one-line) | Reads from | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| PayPeriod.label | pay_period.py:38 | @property | str (inferred) | year-aware formatted date range (`pay_period.py:41-46`) | start_date, end_date | - |

### `app/models/paycheck_deduction.py`

154 lines. Classes: `PaycheckDeduction`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| PaycheckDeduction.amount | paycheck_deduction.py:113 | Numeric(12, 4) | False | - | paycheck_deduction.py:40 | pre_tax_deduction OR post_tax_deduction (timing-dependent) |
| PaycheckDeduction.deductions_per_year | paycheck_deduction.py:114 | Integer | False | db.text("26") | paycheck_deduction.py:41 | - |
| PaycheckDeduction.annual_cap | paycheck_deduction.py:118 | Numeric(12, 2) | True | - | paycheck_deduction.py:43 | contribution_limit_remaining (input) |
| PaycheckDeduction.inflation_rate | paycheck_deduction.py:123 | Numeric(5, 4) | True | - | paycheck_deduction.py:51 | growth (deduction inflation input) |
| PaycheckDeduction.inflation_effective_month | paycheck_deduction.py:124 | Integer | True | - | paycheck_deduction.py:59 | - |
| PaycheckDeduction.sort_order | paycheck_deduction.py:130 | Integer | False | db.text("0") | - | - |
| PaycheckDeduction.version_id | paycheck_deduction.py:139 | Integer | False | "1" | paycheck_deduction.py:66 | - |

`PaycheckDeduction.amount` is `Numeric(12, 4)` (sub-cent precision),
unlike the canonical `Numeric(12, 2)`. The wider precision lets the
paycheck calculator carry intermediate rounding before quantizing the
displayed paycheck. Phase 3 confirms: every consumer reads through the
calculator and quantizes at the boundary, not at the column.

Computed properties: none.

### `app/models/pension_profile.py`

95 lines. Classes: `PensionProfile`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| PensionProfile.benefit_multiplier | pension_profile.py:78 | Numeric(7, 5) | False | - | pension_profile.py:32 | - |
| PensionProfile.consecutive_high_years | pension_profile.py:79 | Integer | False | db.text("4") | pension_profile.py:37 | - |

Computed properties: none. Pension calculation logic lives in
`app/services/pension_calculator.py`; this model only persists the input
parameters.

### `app/models/recurrence_rule.py`

78 lines. Classes: `RecurrenceRule`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| RecurrenceRule.interval_n | recurrence_rule.py:32 | Integer | True | - | recurrence_rule.py:17 | - |
| RecurrenceRule.offset_periods | recurrence_rule.py:34 | Integer | True | - | recurrence_rule.py:18 | - |
| RecurrenceRule.day_of_month | recurrence_rule.py:36 | Integer | True | - | recurrence_rule.py:38 | - |
| RecurrenceRule.due_day_of_month | recurrence_rule.py:46 | Integer | True | - | recurrence_rule.py:48 | - |
| RecurrenceRule.month_of_year | recurrence_rule.py:55 | Integer | True | - | recurrence_rule.py:57 | - |

Computed properties: none. Recurrence logic lives in
`app/services/recurrence_engine.py`.

### `app/models/ref.py`

356 lines. Classes: `AccountTypeCategory`, `AccountType`,
`TransactionType`, `Status`, `RecurrencePattern`, `FilingStatus`,
`DeductionTiming`, `CalcMethod`, `TaxType`, `RaiseType`, `GoalMode`,
`IncomeUnit`, `UserRole`. Reference/lookup tables only. No numeric
columns or computed numeric properties of audit interest. The boolean
columns on `Status` (e.g., `excludes_from_balance`, `is_settled`) are
referenced by `Transaction.effective_amount` and the balance calculator;
consumers are catalogued in section 1.1 (P1-b).

### `app/models/salary_profile.py`

134 lines. Classes: `SalaryProfile`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| SalaryProfile.annual_salary | salary_profile.py:72 | Numeric(12, 2) | False | - | salary_profile.py:28 | paycheck_gross (input) |
| SalaryProfile.pay_periods_per_year | salary_profile.py:77 | Integer | False | db.text("26") | salary_profile.py:29 | - |
| SalaryProfile.qualifying_children | salary_profile.py:82 | Integer | False | db.text("0") | salary_profile.py:30 | - |
| SalaryProfile.other_dependents | salary_profile.py:85 | Integer | False | db.text("0") | salary_profile.py:31 | - |
| SalaryProfile.additional_income | salary_profile.py:88 | Numeric(12, 2) | False | db.text("0") | salary_profile.py:32 | taxable_income (input) |
| SalaryProfile.additional_deductions | salary_profile.py:92 | Numeric(12, 2) | False | db.text("0") | salary_profile.py:33 | federal_tax (W-4 step-4 input) |
| SalaryProfile.extra_withholding | salary_profile.py:96 | Numeric(12, 2) | False | db.text("0") | salary_profile.py:34 | federal_tax (W-4 step-4 input) |
| SalaryProfile.sort_order | salary_profile.py:105 | Integer | False | db.text("0") | - | - |
| SalaryProfile.version_id | salary_profile.py:110 | Integer | False | "1" | salary_profile.py:36 | - |

Computed properties: none.

### `app/models/salary_raise.py`

133 lines. Classes: `SalaryRaise`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| SalaryRaise.effective_month | salary_raise.py:108 | Integer | False | - | salary_raise.py:53 | - |
| SalaryRaise.effective_year | salary_raise.py:109 | Integer | True | - | salary_raise.py:61 | - |
| SalaryRaise.percentage | salary_raise.py:110 | Numeric(5, 4) | True | - | salary_raise.py:66 | growth (raise input) |
| SalaryRaise.flat_amount | salary_raise.py:111 | Numeric(12, 2) | True | - | salary_raise.py:67 | paycheck_gross (raise input) |
| SalaryRaise.version_id | salary_raise.py:119 | Integer | False | "1" | salary_raise.py:69 | - |

Computed properties: none.

### `app/models/savings_goal.py`

135 lines. Classes: `SavingsGoal`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| SavingsGoal.target_amount | savings_goal.py:75 | Numeric(12, 2) | True | - | savings_goal.py:41 | goal_progress (target input) |
| SavingsGoal.contribution_per_period | savings_goal.py:77 | Numeric(12, 2) | True | - | savings_goal.py:46 | goal_progress (rate input) |
| SavingsGoal.income_multiplier | savings_goal.py:115 | Numeric(8, 2) | True | - | savings_goal.py:50 | goal_progress (income-multiplier input) |
| SavingsGoal.version_id | savings_goal.py:121 | Integer | False | "1" | savings_goal.py:54 | - |

`income_multiplier` uses `Numeric(8, 2)` (max 999,999.99); the project
standard is `Numeric(12, 2)` for money but this column is a multiplier
not a money value. Phase 6 confirms this is intentional.

Computed properties: none.

### `app/models/scenario.py`

63 lines. Classes: `Scenario`. No numeric columns or computed numeric
properties of audit interest. `is_baseline` is Boolean; `cloned_from_id`
is FK.

### `app/models/tax_config.py`

240 lines. Classes: `TaxBracketSet`, `TaxBracket`, `StateTaxConfig`,
`FicaConfig`.

`TaxBracketSet` numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| TaxBracketSet.tax_year | tax_config.py:51 | Integer | False | - | tax_config.py:27 | - |
| TaxBracketSet.standard_deduction | tax_config.py:52 | Numeric(12, 2) | False | - | tax_config.py:21 | federal_tax (deduction input) |
| TaxBracketSet.child_credit_amount | tax_config.py:59 | Numeric(12, 2) | False | "0" | tax_config.py:22 | federal_tax (credit input) |
| TaxBracketSet.other_dependent_credit_amount | tax_config.py:63 | Numeric(12, 2) | False | "0" | tax_config.py:23 | federal_tax (credit input) |

`TaxBracket` numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| TaxBracket.min_income | tax_config.py:113 | Numeric(12, 2) | False | - | tax_config.py:86 | taxable_income (bracket boundary) |
| TaxBracket.max_income | tax_config.py:114 | Numeric(12, 2) | True | - | tax_config.py:88 | taxable_income (bracket boundary) |
| TaxBracket.rate | tax_config.py:115 | Numeric(5, 4) | False | - | tax_config.py:91 | federal_tax (rate input) |
| TaxBracket.sort_order | tax_config.py:116 | Integer | False | db.text("0") | - | - |

`StateTaxConfig` numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| StateTaxConfig.tax_year | tax_config.py:174 | Integer | False | - | tax_config.py:149 | - |
| StateTaxConfig.flat_rate | tax_config.py:175 | Numeric(5, 4) | True | - | tax_config.py:137 | state_tax (rate input) |
| StateTaxConfig.standard_deduction | tax_config.py:176 | Numeric(12, 2) | True | - | tax_config.py:143 | state_tax (deduction input) |

`FicaConfig` numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| FicaConfig.tax_year | tax_config.py:216 | Integer | False | - | tax_config.py:204 | - |
| FicaConfig.ss_rate | tax_config.py:217 | Numeric(5, 4) | False | db.text("0.0620") | tax_config.py:194 | fica (SS rate input) |
| FicaConfig.ss_wage_base | tax_config.py:221 | Numeric(12, 2) | False | db.text("176100") | tax_config.py:195 | fica (SS cap input) |
| FicaConfig.medicare_rate | tax_config.py:225 | Numeric(5, 4) | False | db.text("0.0145") | tax_config.py:196 | fica (Medicare rate input) |
| FicaConfig.medicare_surtax_rate | tax_config.py:229 | Numeric(5, 4) | False | db.text("0.0090") | tax_config.py:198 | fica (surtax rate input) |
| FicaConfig.medicare_surtax_threshold | tax_config.py:233 | Numeric(12, 2) | False | db.text("200000") | tax_config.py:201 | fica (surtax threshold input) |

Computed properties: none on any class in this file.

### `app/models/transaction.py`

284 lines. Classes: `Transaction`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| Transaction.estimated_amount | transaction.py:158 | Numeric(12, 2) | False | - | transaction.py:113 | effective_amount (estimate source) |
| Transaction.actual_amount | transaction.py:159 | Numeric(12, 2) | True | - | transaction.py:117 | effective_amount (actual source, may be NULL) |
| Transaction.version_id | transaction.py:186 | Integer | False | "1" | transaction.py:121 | - |

Computed properties:

| Class.property | file:line | Decorator | Returns | Formula (one-line) | Reads from | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| Transaction.effective_amount | transaction.py:221-245 | @property | Decimal (inferred) | `is_deleted ? Decimal("0") : (status.excludes_from_balance ? Decimal("0") : (actual_amount if actual_amount is not None else estimated_amount))` | is_deleted, status.excludes_from_balance, actual_amount, estimated_amount | effective_amount |
| Transaction.is_income | transaction.py:247-250 | @property | bool (inferred) | `transaction_type_id == ref_cache.txn_type_id(TxnTypeEnum.INCOME)` | transaction_type_id (via ref_cache) | - |
| Transaction.is_expense | transaction.py:252-255 | @property | bool (inferred) | `transaction_type_id == ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)` | transaction_type_id (via ref_cache) | - |
| Transaction.days_until_due | transaction.py:257-269 | @property | int \| None (inferred) | `None if due_date is None or status.is_settled else (due_date - date.today()).days` | due_date, status.is_settled | - |
| Transaction.days_paid_before_due | transaction.py:271-283 | @property | int \| None (inferred) | `None if due_date is None or paid_at is None else (due_date - paid_at.date()).days` | due_date, paid_at | - |

`Transaction.effective_amount` is the load-bearing entry point for
balance computation. The 4-tier branching means a Phase 3 consistency
audit must compare (a) every site that reads `effective_amount`, (b)
every site that reads `actual_amount` directly, (c) every site that
reads `estimated_amount` directly, and (d) every site that filters by
status before summing. Direct reads of `actual_amount` or
`estimated_amount` bypass tier 1 (is_deleted) and tier 2 (status
exclusion); sites that do this on purpose must be intentional, and
sites that do this by accident are findings.

`is_income` / `is_expense` use `ref_cache.txn_type_id(...)` which is
the ID-based lookup pattern required by E-15 (no string `name`
comparisons). This pair satisfies the standard.

NO `is_settled`, `is_done`, `is_received`, `is_credit`, `is_cancelled`,
or `is_projected` properties exist on `Transaction`. Status checks must
go through `status.is_settled` / `status.excludes_from_balance` (boolean
columns on the `Status` ref row). This means consumers either read the
whole `Status` object or call into a service helper; section 1.1
inventories which path each site uses.

### `app/models/transaction_entry.py`

118 lines. Classes: `TransactionEntry`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| TransactionEntry.amount | transaction_entry.py:73 | Numeric(12, 2) | False | - | transaction_entry.py:51 | effective_amount (envelope entry source) |
| TransactionEntry.version_id | transaction_entry.py:95 | Integer | False | "1" | transaction_entry.py:56 | - |

Computed properties: none.

`TransactionEntry.amount` is the column aggregated by the only two
money SQL aggregates (`year_end_summary_service.py:519, 520-528`); see
section 1.6.

### `app/models/transaction_template.py`

93 lines. Classes: `TransactionTemplate`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| TransactionTemplate.default_amount | transaction_template.py:59 | Numeric(12, 2) | False | - | transaction_template.py:30 | effective_amount (template seed) |
| TransactionTemplate.sort_order | transaction_template.py:64 | Integer | False | db.text("0") | - | - |
| TransactionTemplate.version_id | transaction_template.py:75 | Integer | False | "1" | transaction_template.py:32 | - |

Computed properties: none.

### `app/models/transfer.py`

186 lines. Classes: `Transfer`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| Transfer.amount | transfer.py:142 | Numeric(12, 2) | False | - | transfer.py:37 | transfer_amount |
| Transfer.version_id | transfer.py:152 | Integer | False | "1" | transfer.py:39 | - |

Computed properties:

| Class.property | file:line | Decorator | Returns | Formula (one-line) | Reads from | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| Transfer.effective_amount | transfer.py:174-182 | @property | Decimal (inferred) | `Decimal("0") if status.excludes_from_balance else amount` | status.excludes_from_balance, amount | transfer_amount |

`Transfer.effective_amount` simpler than `Transaction.effective_amount`:
no actual/estimated split, no soft-delete branch (transfers route
soft-deletion through cascade to shadow transactions per E-08, so
queries excluded soft-deleted parents should not reach this property in
balance contexts). E-09 forbids the balance calculator from querying
the `transfers` table at all; this property is reserved for the
transfer service and CRUD/display sites.

### `app/models/transfer_template.py`

95 lines. Classes: `TransferTemplate`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| TransferTemplate.default_amount | transfer_template.py:60 | Numeric(12, 2) | False | - | transfer_template.py:32 | transfer_amount (template seed) |
| TransferTemplate.sort_order | transfer_template.py:65 | Integer | False | db.text("0") | - | - |
| TransferTemplate.version_id | transfer_template.py:73 | Integer | False | "1" | transfer_template.py:36 | - |

Computed properties: none.

### `app/models/user.py`

343 lines. Classes: `User`, `UserSettings`, `MfaConfig`.

`User` numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| User.failed_login_count | user.py:110 | Integer | False | "0" | user.py:49 | - |

`UserSettings` numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| UserSettings.default_inflation_rate | user.py:234 | Numeric(5, 4) | True | - | user.py:187 | growth (default inflation input) |
| UserSettings.grid_default_periods | user.py:235 | Integer | True | - | user.py:191 | - |
| UserSettings.low_balance_threshold | user.py:236 | Integer | True | - | user.py:192 | account_balance (alert threshold) |
| UserSettings.safe_withdrawal_rate | user.py:237 | Numeric(5, 4) | True | db.text("0.0400") | user.py:207 | growth (SWR input) |
| UserSettings.estimated_retirement_tax_rate | user.py:242 | Numeric(5, 4) | True | - | user.py:216 | federal_tax (retirement projection input) |
| UserSettings.large_transaction_threshold | user.py:243 | Integer | False | "500" | user.py:194 | effective_amount (alert threshold) |
| UserSettings.trend_alert_threshold | user.py:246 | Numeric(5, 4) | False | "0.1000" | user.py:198 | growth (trend alert threshold) |
| UserSettings.anchor_staleness_days | user.py:249 | Integer | False | "14" | user.py:223 | - |

`UserSettings.trend_alert_threshold` is PA-01's open finding (Marshmallow
`Range(min=1, max=100)` percentage vs DB CHECK 0..1 decimal).
`UserSettings.safe_withdrawal_rate` is the column behind PA-04's
float-cast violation in `compute_slider_defaults`.
`UserSettings.estimated_retirement_tax_rate` is one of the rate fields
inspected by PA-02. These three columns are inputs to financial
calculations; the prior-audit findings live in section 0.6 of
`00_priors.md`.

`MfaConfig` numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| MfaConfig.last_totp_timestep | user.py:342 | BigInteger | True | - | - | - |

Not financial.

Computed properties on `User`, `UserSettings`, `MfaConfig`: none.

### Section 1.5 cross-cutting summary

- All money columns are `Numeric(12, 2)` except: calibration money
  (`Numeric(10, 2)`, four columns in `calibration_override.py`),
  paycheck deduction amount (`Numeric(12, 4)`, sub-cent precision for
  intermediate calculation), and savings-goal income multiplier
  (`Numeric(8, 2)`, multiplier not money). No `Float` money columns.
- All rate columns use `Numeric(7, 5)` (interest, APY, benefit
  multiplier) or `Numeric(5, 4)` (percentages, inflation, employer
  match caps, SWR, trend threshold). Calibration effective rates use
  `Numeric(12, 10)` for downstream-computation precision.
- Every money column has an in-model `CheckConstraint` EXCEPT the two
  `Account` balance columns (`Account.current_anchor_balance` line 51
  and `AccountAnchorHistory.anchor_balance` line 152), which carry no
  in-model CheckConstraint. Phase 4 confirms whether the migration
  attaches a CHECK or whether anchor balances are intentionally
  unbounded (mortgages can be negative balances, etc.).
- Six `@property` accessors total in `app/models/`. Five live on
  `Transaction`, one on `Transfer`, one on `Category` (display_name,
  not financial), one on `PayPeriod` (label, not financial). The
  financial properties are `Transaction.effective_amount`,
  `Transaction.is_income`, `Transaction.is_expense`, and
  `Transfer.effective_amount`; section 1.1 catalogues their consumers.
- No `@hybrid_property` or `@cached_property`. Stored-vs-computed
  drift detection (Phase 4) does not have a hybrid-property surface to
  worry about; the only stored numeric the audit must reconcile is
  `LoanParams.current_principal` against the amortization-engine
  replay (only for fixed-rate per A-04; AUTHORITATIVE for ARM), plus
  `Account.current_anchor_balance` against the balance calculator.
- A-01 (09_open_questions.md:29-35) establishes the canonical rounding
  rule `Decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)` for
  any monetary calculation that produces a stored or displayed value.
  No model-layer column performs quantization (storage precision is
  fixed by the column type); the rounding rule applies to the service-
  layer code paths that compute values from these columns. Sections
  1.1 (services) through 1.4 (JS) inventory those quantization sites
  and Phase 3 verifies the rule is applied uniformly. The two sub-
  cent precision exceptions (`PaycheckDeduction.amount` Numeric(12, 4),
  `CalibrationOverride.effective_*_rate` Numeric(12, 10)) carry their
  precision through the calculator and quantize at the boundary; Phase
  3 confirms that boundary exists for every consumer.

## 1.6 Database queries that aggregate money

The grep
`func\.sum|func\.avg|func\.min|func\.max|func\.count` over `app/`
returns FIVE matches (P1-a, 2026-05-15). The raw-SQL grep (`SUM(`,
`AVG(`, `MIN(`, `MAX(`, `COUNT(` in .py files) returns only the same
five hits via `db.func.*`; no raw SQL strings with aggregate keywords
exist outside the SQLAlchemy `func` accessor. `db.text(...)` calls
elsewhere are all column server defaults or audit-infrastructure DDL,
not aggregate execution paths. Python-builtin `sum()`, `min()`, `max()`
calls operate over already-fetched in-memory collections; they belong
in section 1.1 (services) and are out of scope for 1.6.

| File:line | SQL aggregate | Aggregated column | Aggregated column type | Money? | Joins | Filters | Layer |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `app/services/year_end_summary_service.py:518` | `db.func.count(TransactionEntry.id)` | TransactionEntry.id | Integer (PK) | No (row count) | TransactionEntry -> Transaction -> TransactionTemplate, Transaction -> PayPeriod, Transaction -> Account, outer Transaction -> Category (lines 530-540) | user_id=user_id, scenario_id=scenario_id, pay_period_id IN period_ids, is_deleted=False, transaction_type_id=expense_type_id, status_id IN settled_status_ids, TransactionTemplate.is_envelope=True (lines 541-549); GROUP BY group_name, item_name, transaction_id, due_date, pp_start_date (lines 550-557) | service |
| `app/services/year_end_summary_service.py:519` | `db.func.sum(TransactionEntry.amount)` | TransactionEntry.amount | Numeric(12, 2) | YES | same as above | same as above | service |
| `app/services/year_end_summary_service.py:520-528` | `db.func.sum(case((TransactionEntry.is_credit.is_(True), TransactionEntry.amount), else_=Decimal("0")))` | TransactionEntry.amount conditional on is_credit=True | Numeric(12, 2) | YES (conditional sum -- credit entries only) | same as above | same as above | service |
| `app/services/pay_period_service.py:49` | `db.func.max(PayPeriod.period_index)` | PayPeriod.period_index | Integer | No (assigning next index for new periods) | none | filter_by(user_id=user_id) (line 50) | service |
| `app/services/transfer_service.py:669` | `Transaction.query.filter_by(transfer_id=transfer_id).count()` | row count | n/a | No (orphan-detection guard after CASCADE delete of parent transfer; counts shadow transactions that should already be gone) | none | filter_by(transfer_id=transfer_id) (line 668) | service |

### Money aggregates: deeper notes

Both money aggregates live inside one function:
`_compute_envelope_breakdowns_aware()` in
`app/services/year_end_summary_service.py`, called by the year-end
summary service to break down envelope-tracked spending by category
group/item for the configured calendar year. The function:

- joins TransactionEntry to Transaction to TransactionTemplate to
  PayPeriod to Account, outer-joining Category (lines 530-540);
- restricts to user-owned, baseline-scenario, in-window, non-deleted,
  expense-typed, settled-status, envelope-template rows (lines 541-549);
- groups by category and individual transaction, retaining due_date and
  pay-period start_date for client-side year attribution
  (`row.due_date.year if row.due_date is not None else
  row.pp_start_date.year`, lines 562-565).

Neither aggregate filters on `entry_credit_workflow` flags directly; the
credit-vs-debit distinction is encoded entirely by the `case` in the
second `func.sum`. Phase 3 must verify (a) that the credit-entry
exclusion is consistent with the entry-aware checking-impact formula in
the balance calculator
(`app/services/balance_calculator.py:298-331`, per the docstring) and
(b) that the same envelope-spending concept computed elsewhere
(spending trends, budget variance, savings dashboard) uses the same
filter set or documents the divergence.

### Aggregates over money outside services or in raw SQL

NONE. All five SQL aggregates are inside service modules. There are no
SQL aggregates in routes, templates, JS, raw SQL strings, or
non-service code paths. This satisfies the audit-plan rule that
aggregates over money outside services are suspect.

### Filter-set checklist for the two money aggregates

For Phase 3 cross-comparison:

| Dimension | year_end_summary_service.py:519/520 |
| --- | --- |
| User scope | `Account.user_id == user_id` (line 542) |
| Scenario scope | `Transaction.scenario_id == scenario_id` (line 543) |
| Period scope | `Transaction.pay_period_id.in_(period_ids)` (line 544) |
| Soft delete | `Transaction.is_deleted.is_(False)` (line 545) |
| Type filter | `Transaction.transaction_type_id == expense_type_id` (line 546) |
| Status filter | `Transaction.status_id.in_(settled_status_ids)` (line 547) |
| Envelope filter | `TransactionTemplate.is_envelope.is_(True)` (line 548) |
| Effective-amount logic | Reads `TransactionEntry.amount` directly, not the entry-aware checking-impact formula. Justified because envelope entries are the source for envelope spending, but Phase 3 must confirm |
| Anchor handling | Not anchor-aware. Sums all entries in scope regardless of anchor period |
| `is_deleted` on TransactionEntry | NOT filtered. TransactionEntry has no soft-delete column per the model (`transaction_entry.py`); CASCADE from Transaction handles deletion |
| Quantization | None at the SQL layer. Caller is responsible for any rounding |
| Source-of-truth column | `TransactionEntry.amount` (line 73, Numeric(12, 2)) |

## Open questions raised in 1.5/1.6

None during this session. All numeric columns and properties mapped
cleanly to existing controlled-vocabulary tokens with the noted "input"
qualifications. The classification ambiguities flagged in this section
(calibration money precision, savings goal multiplier precision, anchor-
balance lack of CHECK constraint, paycheck deduction Numeric(12, 4)) are
recorded in the per-model rows for Phase 6 (DRY/SOLID) to evaluate
rather than as questions for the developer; they do not block any
calculation reading in subsequent Phase 1 sessions.

## 1.1 Service layer

40 files under `app/services/`. 18,022 LOC total. Three Explore subagents
ran in parallel, very thorough, each reading every file in scope IN FULL
before producing the structured inventory. Files were partitioned by
domain (calculation engines / aggregation / transactional+workflow) to
keep each subagent's context bounded.

Out of scope per audit plan section 0.6 (auth and non-financial services
excluded): `auth_service.py` (805 lines), `mfa_service.py` (413 lines),
`exceptions.py` (44 lines), `__init__.py` (0 lines). Recorded for
exhaustiveness but no functions inventoried.

In scope: 36 files, 16,760 LOC, 67+ public functions/methods.

For every public function in scope:

- one-sentence description of what the body actually does (not the
  docstring paraphrase);
- financial concept tokens produced (using the vocabulary at the top of
  this file);
- status filter, transaction-type filter, period scope, with file:line
  citations;
- amount column read (`effective_amount`, `actual_amount`,
  `estimated_amount`, `amount`) with file:line and a bypass flag;
- quantization site (precision, rounding mode, line) or "MISSING" per
  A-01;
- enumerated calls to other service functions or model methods.

The cross-cutting summary tables at the end of section 1.1 collect every
`effective_amount` bypass, every missing quantization, every Flask
boundary import, every transfer-model read, and every shadow-mutation
site, so Phase 3 has a single grep target.

A-05 cross-reference: A-05 lists eight `calculate_monthly_payment` call
sites; the grep finds fourteen
(`amortization_engine.py:436, 440, 491, 512, 693, 697, 952, 957`;
`balance_calculator.py:225, 231`;
`loan_payment_service.py:251, 256`;
`app/routes/loan.py:1102, 1225, 1231`). The developer's list captures
only the primary branch of each call pair; Phase 3 must verify that
adjacent fallback branches (lines 436, 693, 957 in the engine; line 231
in the balance calculator; line 256 in loan_payment_service; lines 1102,
1231 in the loan route) receive the same triple
`(current_principal, current_rate, remaining_months)` for the same
loan-on-date as their primary siblings, per the A-05 invariant.

### Group A: Calculation engines

11 files inventoried in scope (`tax_config_service.py` is included for
completeness despite producing no money directly). Total 3,608 LOC.
Zero Flask imports. Zero shadow mutations. Quantization to `Decimal('0.01')`
ROUND_HALF_UP is uniform per A-01 except where noted.

#### `app/services/amortization_engine.py` (991 lines)

Imports flagged: none.

Public functions (sorted by line):

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 128 | `calculate_remaining_months(origination_date, term_months, as_of=None)` | int | Returns term_months minus elapsed months from origination to as_of. | loan_remaining_months | n/a | n/a | n/a | n/a | no | n/a | none |
| 178 | `calculate_monthly_payment(principal, annual_rate, remaining_months)` | Decimal | Applies the standard amortization formula `P * (r(1+r)^n) / ((1+r)^n - 1)` and quantizes. | monthly_payment | n/a | n/a | n/a | n/a | no | Decimal('0.01'), ROUND_HALF_UP, lines 192, 197 | none |
| 200 | `_advance_month(year, month, day)` | date | Increments the month and clamps the day to the month's last valid day. | - | n/a | n/a | n/a | n/a | no | n/a | none |
| 210 | `_build_payment_lookups(payments, origination_date)` | tuple[dict, dict] | Builds (year, month) -> (amount_sum, is_confirmed_any) lookup, dropping pre-origination rows. | - | n/a | n/a | n/a | PaymentRecord.amount fields | no | n/a | none |
| 255 | `_build_rate_change_list(rate_changes, origination_date)` | list[tuple] | Sorts, deduplicates, drops pre-origination rate changes. | - | n/a | n/a | n/a | n/a | no | n/a | none |
| 298 | `_find_applicable_rate(payment_date, rate_schedule, base_rate)` | Decimal | Returns the most recent rate effective on or before payment_date, else base_rate. | - | n/a | n/a | n/a | n/a | no | n/a | none |
| 326 | `generate_schedule(current_principal, annual_rate, remaining_months, extra_monthly=ZERO, origination_date=None, payment_day=1, original_principal=None, term_months=None, payments=None, rate_changes=None, anchor_balance=None, anchor_date=None)` | list[AmortizationRow] | Builds a month-by-month schedule with optional payment-record replay, ARM rate changes, and balance anchoring; each row quantized at allocation time. | principal_paid_per_period, interest_paid_per_period, monthly_payment, loan_principal_real, months_saved | n/a | n/a | forward range from origination/anchor through term (lines 474-617) | n/a | no | Decimal('0.01'), ROUND_HALF_UP, lines 517, 551, 586, 601-604 | calculate_monthly_payment at 436, 440, 491, 512 |
| 622 | `_derive_summary_metrics(schedule, fallback_date)` | tuple[Decimal, date] | Extracts total_interest and payoff_date from a finished schedule. | total_interest, payoff_date | n/a | n/a | n/a | row.interest, row.payment_date | no | n/a | none |
| 649 | `calculate_summary(current_principal, annual_rate, remaining_months, origination_date, payment_day, term_months, extra_monthly=ZERO, original_principal=None, payments=None, rate_changes=None, anchor_balance=None, anchor_date=None)` | AmortizationSummary | Generates standard and accelerated schedules, then derives summary deltas (payoff, interest_saved, months_saved). | total_interest, interest_saved, months_saved, payoff_date, monthly_payment | n/a | n/a | forward range (lines 702-731) | n/a | no | Decimal('0.01'), ROUND_HALF_UP, line 749 | calculate_monthly_payment at 693, 697; generate_schedule at 702, 720 |
| 753 | `calculate_payoff_by_date(current_principal, annual_rate, remaining_months, target_date, origination_date, payment_day, original_principal=None, term_months=None, rate_changes=None)` | Decimal \| None | Binary-searches the extra_monthly value required to hit a target payoff_date; returns None if unreachable. | monthly_payment | n/a | n/a | forward range (lines 824-832) | n/a | no | Decimal('0.01'), ROUND_HALF_UP, line 823 | generate_schedule at 779, 824 |
| 864 | `get_loan_projection(params, schedule_start=None, payments=None, rate_changes=None)` | LoanProjection | Generates the full schedule once, derives summary, and returns `current_balance` from the stored `current_principal` for ARM or the last confirmed schedule row for fixed-rate (A-04 dual policy at lines 977-985). | monthly_payment, total_interest, payoff_date, loan_principal_real, loan_principal_stored, loan_remaining_months | n/a | n/a | full loan term (lines 932-942) | LoanParams.current_principal at 977, 980 | no | n/a (delegates) | calculate_remaining_months at 908; generate_schedule at 932; _derive_summary_metrics at 945; calculate_monthly_payment at 952, 957 |

A-04 anchor: lines 977-985 implement the dual policy. `is_arm=True` -> `cur_balance = current_principal` (line 977-978). `is_arm=False` -> walks the schedule backward from end, taking `row.remaining_balance` from the last `is_confirmed=True` row (lines 980-984). The `LoanProjection` docstring at lines 848-861 documents this asymmetry.

#### `app/services/growth_engine.py` (419 lines)

Imports flagged: none.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 91 | `calculate_employer_contribution(employer_params, employee_contribution)` | Decimal | Computes employer match: flat_percentage of gross, or `match_percentage * employee_contribution` capped by `match_cap_percentage * gross`. | employer_contribution | n/a | n/a | per period | n/a | no | Decimal('0.01'), ROUND_HALF_UP, lines 114, 120, 124 | none |
| 130 | `_build_contribution_lookup(contributions)` | dict \| None | Sums same-date `ContributionRecord` entries into a (date -> (amount_sum, is_confirmed_any)) dict. | - | n/a | n/a | n/a | ContributionRecord.amount | no | n/a | none |
| 164 | `project_balance(current_balance, assumed_annual_return, periods, periodic_contribution=ZERO, employer_params=None, annual_contribution_limit=None, ytd_contributions_start=ZERO, contributions=None)` | list[ProjectedBalance] | Walks forward period-by-period applying growth THEN contribution (order matters; see docstring); resets YTD at year boundary; enforces annual_contribution_limit. | growth, employer_contribution, contribution_limit_remaining, ytd_contributions | n/a | n/a | list of periods (line 222) | n/a | no | Decimal('0.01'), ROUND_HALF_UP, lines 243-244 | calculate_employer_contribution at 265 |
| 297 | `reverse_project_balance(anchor_balance, assumed_annual_return, periods, periodic_contribution=ZERO, employer_params=None)` | list[ProjectedBalance] | Walks backward from a last-period anchor, inverting growth and contributions to derive prior-period balances. | growth, employer_contribution | n/a | n/a | list of periods reversed (line 348) | n/a | no | Decimal('0.01'), ROUND_HALF_UP, lines 363, 368 | calculate_employer_contribution at 339 |
| 393 | `generate_projection_periods(start_date, end_date, cadence_days=14)` | list[SyntheticPeriod] | Emits biweekly `SyntheticPeriod` namedtuples between two dates. | - | n/a | n/a | n/a | n/a | no | n/a | none |

#### `app/services/interest_projection.py` (114 lines)

Imports flagged: none.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 49 | `calculate_interest(balance, apy, compounding_frequency, period_start, period_end)` | Decimal | Computes interest accrued during one pay period under daily/monthly/quarterly compounding using actual/365 convention. Returns ZERO for non-positive balance/APY or inverted period. | apy_interest | n/a | n/a | period span (lines 86-110) | n/a | no | Decimal('0.01'), ROUND_HALF_UP, line 114 | none |

Quarterly compounding (lines 99-110) uses the actual quarter-length from period start rather than a hardcoded 91 days per the L-05 fix comment.

#### `app/services/tax_calculator.py` (321 lines)

Imports flagged: none.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 35 | `calculate_federal_withholding(gross_pay, pay_periods, bracket_set, *, additional_income=ZERO, pre_tax_deductions=ZERO, additional_deductions=ZERO, qualifying_children=0, other_dependents=0, extra_withholding=ZERO)` | Decimal | IRS Pub 15-T Percentage Method: annualize, apply standard deduction and W-4 step-4 deductions, run marginal brackets, apply W-4 child/dependent credits, divide by pay_periods, add extra_withholding. | federal_tax, taxable_income | n/a | n/a | current period (annualize/de-annualize at 102-160) | n/a | no | Decimal('0.01'), ROUND_HALF_UP, lines 162-164 | _apply_marginal_brackets at 127 |
| 173 | `_apply_marginal_brackets(taxable_income, brackets)` | Decimal | Walks sorted brackets and sums `(top - bottom) * rate` for each tier within the income. | federal_tax (intermediate) | n/a | n/a | annual | n/a | no | Decimal('0.01'), ROUND_HALF_UP, line 209 | none |
| 215 | `calculate_federal_tax(annual_gross, bracket_set)` | Decimal | Legacy single-call wrapper: standard deduction subtraction then `_apply_marginal_brackets`; returns ANNUAL not per-period. | federal_tax | n/a | n/a | annual | n/a | no | inherits from helper | _apply_marginal_brackets at 234 |
| 240 | `calculate_state_tax(annual_gross, state_config)` | Decimal | Subtracts standard_deduction, applies flat_rate; returns ZERO if state's tax_type_id is 'none' (line 257 uses ref_cache, not name string). | state_tax | n/a | n/a | annual | n/a | no | Decimal('0.01'), ROUND_HALF_UP, line 266 | ref_cache.tax_type_id at 257 |
| 274 | `calculate_fica(annual_gross, fica_config, cumulative_wages=ZERO)` | dict[str, Decimal] | Applies Social Security rate up to wage base cap (cumulative_wages enforces cap across periods), Medicare rate over full gross, and Medicare surtax above threshold. | fica | n/a | n/a | current period (cumulative wages tracked) | n/a | no | Decimal('0.01'), ROUND_HALF_UP, lines 304, 306, 309, 316-318 | none |

#### `app/services/paycheck_calculator.py` (504 lines)

Imports flagged: none.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 92 | `calculate_paycheck(profile, period, all_periods, tax_configs, *, calibration=None)` | PaycheckBreakdown | Full per-period breakdown: raises -> gross -> pre-tax deductions -> taxable -> federal/state/FICA (bracket-based or calibrated) -> post-tax deductions -> net. | paycheck_gross, paycheck_net, taxable_income, federal_tax, state_tax, fica, pre_tax_deduction, post_tax_deduction, employer_contribution | n/a | n/a | current period + all_periods (for 3rd-paycheck and cumulative wages) | n/a | no | Decimal('0.01'), ROUND_HALF_UP, lines 133-135, 202-204, 231 | _apply_raises at 125; _get_raise_event at 126; _calculate_deductions at 149, 217; calibration_service.apply_calibration at 167; tax_calculator.calculate_federal_withholding at 185; tax_calculator.calculate_state_tax at 199; tax_calculator.calculate_fica at 210 |
| 250 | `project_salary(profile, periods, tax_configs, *, calibration=None)` | list[PaycheckBreakdown] | Calls `calculate_paycheck` once per period in `periods`. | paycheck_gross, paycheck_net, federal_tax, state_tax, fica, pre_tax_deduction, post_tax_deduction, employer_contribution | n/a | n/a | list of periods (line 267) | n/a | no | n/a (delegates) | calculate_paycheck at 263 |
| 274 | `_apply_raises(profile, period)` | Decimal | Applies all qualifying raises (recurring + one-time) in `salary_raise.sort_order`, returning the post-raise annual salary for `period`. | paycheck_gross | n/a | n/a | current period (291-324) | n/a | no | Decimal('0.01'), ROUND_HALF_UP, line 326 | _apply_single_raise at 299 |
| 329 | `_apply_single_raise(salary, raise_obj)` | Decimal | Applies one raise: `salary * (1 + percentage)` or `salary + flat_amount`. | paycheck_gross | n/a | n/a | n/a | n/a | no | n/a (caller quantizes) | none |
| 339 | `_get_raise_event(profile, period)` | str | Returns a comma-separated label describing raise events active in `period`. | - | n/a | n/a | current period (344-365) | n/a | no | n/a | none |
| 369 | `_is_third_paycheck(period, all_periods)` | bool | True if `period` is the third pay period whose start_date falls in its calendar month. | - | n/a | n/a | current month (375-385) | n/a | no | n/a | none |
| 389 | `_is_first_paycheck_of_month(period, all_periods)` | bool | True if `period` is the earliest in its calendar month. | - | n/a | n/a | current month (393-400) | n/a | no | n/a | none |
| 403 | `_calculate_deductions(profile, period, all_periods, gross_biweekly, timing_id, calc_method_pct_id, is_third_paycheck)` | list[DeductionLine] | Filters profile.deductions by timing_id; skips 24-per-year on 3rd-paycheck and 12-per-year unless first-of-month; applies flat/percentage and per-year inflation. | pre_tax_deduction, post_tax_deduction | n/a | n/a | current period (433-453) | PaycheckDeduction.amount, percentage | no | Decimal('0.01'), ROUND_HALF_UP, lines 440-441, 451-452 | _inflation_years at 449 |
| 463 | `_inflation_years(period, profile, effective_month)` | int | Counts full inflation years from profile creation to `period`'s month-anniversary. | - | n/a | n/a | profile -> period (469-475) | n/a | no | n/a | none |
| 480 | `_get_cumulative_wages(profile, period, all_periods)` | Decimal | Sums year-to-date gross wages (post-raises) for FICA SS wage-base cap tracking. | fica | n/a | n/a | YTD (488-504) | n/a | no | Decimal('0.01'), ROUND_HALF_UP, line 499-501 | _apply_raises at 498 |

Calibration override branches at lines 160-173: when `calibration.is_active` is True, FICA/federal/state are computed via `calibration_service.apply_calibration` instead of the bracket path; both paths exit through the same quantization at line 231.

#### `app/services/loan_payment_service.py` (353 lines)

Imports flagged: none.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 78 | `load_loan_context(account_id, scenario_id, loan_params)` | LoanContext | Unified loader: pulls EscrowComponents, derives monthly_escrow, gets payment history, computes contractual_pi, builds rate_changes list for ARM, runs `prepare_payments_for_engine`. Returns a LoanContext dataclass ready for the engine. | monthly_payment, escrow_per_period, loan_principal_stored | status.excludes_from_balance=False at 210 (via get_payment_history) | income at 190 | scenario period at 116 | effective_amount at 218 (in get_payment_history) | no | n/a | EscrowComponent query at 104; escrow_calculator.calculate_monthly_escrow at 110; get_payment_history at 116; prepare_payments_for_engine at 122; RateHistory query at 131 |
| 156 | `get_payment_history(account_id, scenario_id)` | list[PaymentRecord] | Queries shadow income on the loan account (transfer_id IS NOT NULL AND income type), excludes status.excludes_from_balance, returns PaymentRecord dataclasses using `effective_amount` and `status.is_settled`. | monthly_payment | status.excludes_from_balance=False at 210 | income at 190 | scenario period at 212 | effective_amount at 218 | no | n/a | none |
| 233 | `compute_contractual_pi(params)` | Decimal | ARM (line 250): re-amortizes from `current_principal + interest_rate` at the current remaining_months. Fixed-rate: standard `original_principal + interest_rate + term_months`. | monthly_payment | n/a | n/a | n/a | LoanParams.current_principal, LoanParams.interest_rate | no | inherits from `calculate_monthly_payment` | calculate_remaining_months at 247; calculate_monthly_payment at 251, 256 |
| 263 | `prepare_payments_for_engine(payments, payment_day, monthly_escrow, contractual_pi)` | list[PaymentRecord] | A-06 preprocessing: subtracts `monthly_escrow` from any payment >= contractual_pi + threshold (lines 305-319), then redistributes biweekly month-collisions so one payment per month aligns with `payment_day` (lines 321-351). | monthly_payment, escrow_per_period | n/a | n/a | sorted by payment_date at 300 | PaymentRecord.amount | no | n/a (inputs already quantized) | none |

#### `app/services/retirement_gap_calculator.py` (136 lines)

Imports flagged: none.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 37 | `calculate_gap(net_biweekly_pay, monthly_pension_income=ZERO, retirement_account_projections=None, safe_withdrawal_rate=Decimal("0.04"), planned_retirement_date=None, estimated_tax_rate=None)` | RetirementGapAnalysis | Converts net biweekly -> monthly, applies pension (optionally tax-adjusted), computes gap, derives required_savings via 4% SWR rule, compares to projected balances (traditional vs Roth split via `estimated_tax_rate`). | pension_benefit_monthly, federal_tax (retirement tax input), paycheck_net | n/a | n/a | future (planned_retirement_date) | n/a | no | Decimal('0.01'), ROUND_HALF_UP, lines 70, 80, 95, 121 | none |

#### `app/services/pension_calculator.py` (153 lines)

Imports flagged: none.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 31 | `calculate_benefit(benefit_multiplier, consecutive_high_years, hire_date, planned_retirement_date, salary_by_year)` | PensionBenefit | Formula: `multiplier * years_of_service * high_salary_average`. Returns `PensionBenefit(annual_benefit, monthly_benefit, ...)`. | pension_benefit_annual, pension_benefit_monthly | n/a | n/a | hire_date -> planned_retirement_date (47) | salary_by_year (dict input) | no | Decimal('0.01'), ROUND_HALF_UP, lines 63, 65-66 | _calculate_years_of_service at 47; _compute_high_salary_average at 57 |
| 78 | `project_salaries_by_year(annual_salary, raises, start_year, end_year)` | list[tuple] | Builds year-by-year salary projection by reusing `paycheck_calculator._apply_raises` with synthetic period objects. | paycheck_gross (raise-applied) | n/a | n/a | year range (107-110) | n/a | no | n/a | paycheck_calculator._apply_raises at 109 |
| 114 | `_calculate_years_of_service(hire_date, retirement_date)` | Decimal | `(retirement_date - hire_date).days / 365.25`, quantized. | - | n/a | n/a | n/a | n/a | no | Decimal('0.01'), ROUND_HALF_UP, line 121 | none |
| 126 | `_compute_high_salary_average(salary_by_year, consecutive_high_years)` | tuple[Decimal, list] | Sliding-window: returns the highest `consecutive_high_years`-year average and the (year, salary) tuples in that window. | - | n/a | n/a | n/a | salary values | no | Decimal('0.01'), ROUND_HALF_UP, line 148 | none |

#### `app/services/investment_projection.py` (288 lines)

Imports flagged: none.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 42 | `adapt_deductions(raw_deductions)` | list[AdaptedDeduction] | Converts ORM `PaycheckDeduction` rows to lightweight namedtuples for DB-decoupling. | - | n/a | n/a | n/a | n/a | no | n/a | none |
| 73 | `_compute_deduction_per_period(deduction, pct_id)` | tuple[Decimal, Decimal] | Returns (contribution, gross_biweekly) for flat or percentage-of-salary deductions. | - | n/a | n/a | n/a | n/a | no | Decimal('0.01') quantize at 93, 96 (caller-supplied precision) | none |
| 100 | `calculate_investment_inputs(account_id, investment_params, deductions, all_contributions, all_periods, current_period, salary_gross_biweekly=None)` | InvestmentInputs | Computes (periodic_contribution, employer_params, ytd_contributions_start, annual_contribution_limit) from deductions plus shadow income on the investment account. | growth (input), employer_contribution (input), contribution_limit_remaining, ytd_contributions | status.excludes_from_balance at 150 | income (shadow) at 147 | current period + YTD (178-187) | **estimated_amount at 153 and 187** | YES (lines 153, 187) | Decimal('0.01') quantize via .quantize() at 159-160, 186-187 | none |
| 201 | `build_contribution_timeline(deductions, contribution_transactions, periods)` | list[ContributionRecord] | Builds per-period ContributionRecord stream: same deduction amount each period (past = confirmed); per-transaction shadow contributions (status-confirmed). | growth (input), employer_contribution (input) | status.excludes_from_balance=False at 268; status.is_settled at 284 | income (shadow) at 265 | period list (253, 270) | effective_amount at 274 | no | inherits from `_compute_deduction_per_period` | _compute_deduction_per_period at 248 |

The bypass at `calculate_investment_inputs` lines 153 and 187 reads `t.estimated_amount` directly, but the caller pre-filters by `status.excludes_from_balance` (line 150), so cancelled/credit contributions never reach the sum. Phase 3 must verify that all callers honor this contract.

#### `app/services/escrow_calculator.py` (115 lines)

Imports flagged: none.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 14 | `calculate_monthly_escrow(components, as_of_date=None)` | Decimal | Sums active components' `annual_amount / 12` with optional compound inflation; month-aware elapsed (M-05) prevents full-year inflation on late-created components. | escrow_per_period | n/a | n/a | as_of_date (44-50) | EscrowComponent.annual_amount, inflation_rate | no | Decimal('0.01'), ROUND_HALF_UP, line 57 | none |
| 60 | `calculate_total_payment(monthly_pi, components, as_of_date=None)` | Decimal | `monthly_pi + calculate_monthly_escrow(...)`. | monthly_payment, escrow_per_period | n/a | n/a | n/a | n/a | no | Decimal('0.01'), ROUND_HALF_UP, line 76 | calculate_monthly_escrow at 75 |
| 79 | `project_annual_escrow(components, years_forward, base_year)` | list[tuple] | Per-year totals with per-component inflation applied independently. | escrow_per_period | n/a | n/a | forward range (97-113) | n/a | no | Decimal('0.01'), ROUND_HALF_UP, lines 111, 113 | none |

#### `app/services/tax_config_service.py` (69 lines)

Imports flagged: none.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 16 | `load_tax_configs(user_id, profile, tax_year=None)` | dict | Loads TaxBracketSet/StateTaxConfig/FicaConfig for user, filing_status, state, year. | federal_tax (input), state_tax (input), fica (input) | n/a | n/a | tax_year (line 37) | n/a | no | n/a | none |

Phase 3 finding (no F-id yet): docstring at `tax_config_service.py:7` says it was "extracted from the salary route to eliminate a route-to-route import and a duplicate copy in `chart_data_service.py`". That file does not exist anywhere in `app/` (verified by grep); the audit plan's required-grep list (Appendix B) includes `chart_data_service` and the only references in the codebase are this stale docstring and a comment in `app/static/js/chart_theme.js:222`. Phase 3 should determine whether `chart_data_service` was renamed or never implemented.

#### `app/services/calibration_service.py` (145 lines)

Imports flagged: none.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 34 | `derive_effective_rates(actual_gross_pay, actual_federal_tax, actual_state_tax, actual_social_security, actual_medicare, taxable_income)` | DerivedRates | Computes effective rates from pay stub: federal/state divide by taxable_income, FICA divides by gross. Stores at `Decimal('0.0000000001')` precision (`Numeric(12, 10)` column). | federal_tax (calibration input), state_tax (calibration input), fica (calibration input) | n/a | n/a | n/a | n/a | no | Decimal('0.0000000001'), ROUND_HALF_UP, lines 83-96 | none |
| 106 | `apply_calibration(gross_biweekly, taxable_biweekly, calibration)` | dict[str, Decimal] | Applies derived effective rates: federal/state * taxable_biweekly; SS/medicare * gross_biweekly. | federal_tax (calibrated), state_tax (calibrated), fica (calibrated) | n/a | n/a | current period | CalibrationOverride.effective_*_rate | no | Decimal('0.01'), ROUND_HALF_UP, lines 133-144 | none |

### Group B: Aggregation and dashboard services

11 files inventoried. Total 7,726 LOC. The `_sum_remaining` vs `_sum_all` split in `balance_calculator.py` and the `_compute_mortgage_interest` in `year_end_summary_service.py` (A-06) and the ARM `proj.current_balance` read in `savings_dashboard_service.py:373` (A-04) are the headline cross-page concept-comparison targets for Phase 3.

#### `app/services/balance_calculator.py` (451 lines)

Imports flagged: none. Reads `budget.transfers`? **NO** (Transfer Invariant 5 satisfied; verified by grep -- no `Transfer.query`, no `from app.models.transfer`, no `db.session.query(Transfer)`).

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 35 | `calculate_balances(anchor_balance, anchor_period_id, periods, transactions)` | (OrderedDict[period_id, Decimal], bool) | Walks `periods` from anchor forward; first period uses `_sum_remaining` (anchor semantics), later periods use `_sum_all`. Returns running balances and a `stale_anchor_warning` flag. | checking_balance, projected_end_balance, period_subtotal | via `_sum_remaining`/`_sum_all` (lines 411, 439) | via `_sum_remaining`/`_sum_all` (lines 414-449) | anchor + forward periods | n/a (delegates) | no | Decimal('0.00') initialization (line 47) | _sum_remaining at 76; _sum_all at 84 |
| 112 | `calculate_balances_with_interest(anchor_balance, anchor_period_id, periods, transactions, interest_params=None)` | (OrderedDict, dict[period_id, Decimal]) | Wraps `calculate_balances` and layers `interest_projection.calculate_interest` per period. | checking_balance, apy_interest | n/a (delegates) | n/a (delegates) | anchor + forward | n/a (delegates) | no | n/a; interest at column precision | calculate_balances at 135; interest_projection.calculate_interest at 161 |
| 176 | `calculate_balances_with_amortization(anchor_balance, anchor_period_id, periods, transactions, account_id=None, loan_params=None)` | (OrderedDict, dict[period_id, Decimal]) | For debt accounts: detects shadow income on the loan as principal payments, splits into principal/interest using current rate, returns running principal and per-period interest portions. | loan_principal_real, principal_paid_per_period, interest_paid_per_period, monthly_payment | status.excludes_from_balance at 264 | shadow income at 268 (`transfer_id is not None` AND `is_income`) | anchor + forward | effective_amount at 270; LoanParams.current_principal, interest_rate at 221-235 | no | Decimal('0.01'), ROUND_HALF_UP, line 275; principal snap at 278-282 | calculate_balances at 207; amortization_engine.calculate_monthly_payment at 225, 231; amortization_engine.calculate_remaining_months at 203 |
| 292 | `_entry_aware_amount(txn)` | Decimal | For a PROJECTED expense with eagerly-loaded `entries`: returns `max(estimated, sum_cleared_debit + sum_credit)` so cleared entries reduce projection without double-counting (see docstring lines 298-331). For all other shapes returns `txn.effective_amount`. | effective_amount | status_id == projected_id at 365 | n/a (per-transaction) | single transaction | entry.amount directly at 374-378; estimated_amount at 384-385 | YES (374-378, 384-385) | n/a (max formula preserves precision) | ref_cache.status_id at 364 |
| 389 | `_sum_remaining(transactions)` | (Decimal, Decimal) | Anchor-period semantics: sum only PROJECTED transactions (skip status_id != projected at 411); income uses `effective_amount`, expenses use `_entry_aware_amount`. | period_subtotal | status_id != projected_id at 411 | income/expense split at 414-417 | anchor period | effective_amount at 415; `_entry_aware_amount` at 417 | no (uses effective_amount) | Decimal('0.00') initialization (403-404) | ref_cache.status_id at 406; _entry_aware_amount at 417 |
| 422 | `_sum_all(transactions)` | (Decimal, Decimal) | Non-anchor semantics: same filter set and amount logic as `_sum_remaining` but applied to all periods after the anchor. | period_subtotal | status_id != projected_id at 443 | income/expense split at 446-449 | non-anchor period | effective_amount at 447; `_entry_aware_amount` at 449 | no (uses effective_amount) | Decimal('0.00') initialization (436-437) | ref_cache.status_id at 439; _entry_aware_amount at 449 |

`_sum_remaining` vs `_sum_all` differ only in name and which period(s) they receive; the body filters, type splits, amount sources, and lack of quantization are identical. Phase 3 must compare the two functions line-by-line to confirm no behavioral divergence has sneaked in (audit plan section 3.1 calls this out explicitly).

#### `app/services/dashboard_service.py` (731 lines)

Imports flagged: none. Reads `budget.transfers`? NO.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 40 | `compute_dashboard_data(user_id)` | dict | Assembles all main-dashboard sections (balance, payday, upcoming bills, alerts, goals, debt, spending). | checking_balance, account_balance, projected_end_balance, savings_total, debt_total, dti_ratio, cash_runway_days | n/a (delegates) | n/a (delegates) | current + next period | n/a (delegates) | no | n/a (delegates) | pay_period_service.get_all_periods, get_current_period, get_next_period at 65-72; _compute_balances at 73; multiple section helpers at 78-92 |
| 99 | `_get_upcoming_bills(account_id, scenario_id, current_period, next_period)` | list[dict] | Queries PROJECTED expense transactions in current + next period with `selectinload(entries)`; converts each via `txn_to_bill_dict`. | period_subtotal | status_id == projected_id at 145 | expense at 146 | current + next periods (122-124) | effective_amount at 191 (via helper) | no | n/a (caller quantizes for display) | selectinload at 138; txn_to_bill_dict at 155 |
| 167 | `txn_to_bill_dict(txn, today)` | dict | Converts a Transaction to a display dict for templates: amount, due-date countdown, entry progress. | effective_amount | n/a | n/a | n/a | effective_amount at 191 | no | n/a | days_until_due property at 187; _entry_progress_fields at 199 |
| 203 | `_entry_progress_fields(txn)` | dict | For entry-tracked transactions, computes (is_tracked, entry_total, remaining, over_budget) by summing entries and comparing to estimated_amount. | goal_progress, effective_amount | n/a | n/a | n/a | estimated_amount at 239, 245 | YES (estimated_amount direct at 239 via `compute_remaining`, and at 245 for over-budget comparison) | n/a | entry_service.compute_entry_sums at 237; entry_service.compute_remaining at 239 |
| 252 | `_compute_alerts(account, settings, balance_results, current_period, all_periods)` | list[dict] | Detects stale anchor, negative balance, low-balance threshold alerts and sorts by severity. | account_balance, checking_balance | n/a | n/a | current + future (293-308) | balance_results dict (no direct column read) | no | threshold compared as Decimal at 314 | _get_last_anchor_date at 272 |
| 334 | `_get_balance_info(account, current_period, balance_results)` | dict | Returns current balance and cash runway; flags stale anchor. | checking_balance, cash_runway_days | n/a | n/a | current period | **Account.current_anchor_balance at 350** | YES (stored column read) | n/a | _get_last_anchor_date at 355; _compute_cash_runway at 363 |
| 375 | `_compute_cash_runway(account_id, current_balance)` | int \| None | Daily-average paid expenses over the past 30 days by due_date; runway = `current_balance / daily_avg` (int days). | cash_runway_days | DONE/RECEIVED/SETTLED at 391-395 | expense at 396 | last 30 calendar days by due_date (390) | effective_amount via Transaction property | no | int truncation; returns 0 for non-positive balance (line 388) | none (raw query) |

#### `app/services/savings_dashboard_service.py` (956 lines)

Imports flagged: none. Reads `budget.transfers`? NO.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 61 | `compute_dashboard_data(user_id)` | dict | Orchestrates the /savings page: account projections, goals, emergency fund, DTI. | savings_total, debt_total, dti_ratio, goal_progress, emergency_fund_coverage_months | n/a (delegates) | n/a (delegates) | all periods | n/a (delegates) | no | DTI quantized via `Decimal("0.1"), ROUND_HALF_UP` at 172, 176 | _load_account_params at 79; _compute_account_projections at 90; _compute_emergency_fund at 132; _compute_debt_summary at 156; multiple helpers through 195 |
| 201 | `_load_account_params(user_id, accounts)` | dict | Batch-loads InterestParams, InvestmentParams, LoanParams, deductions grouped by account.id. | - | n/a | n/a | n/a | n/a | no | salary_gross_biweekly quantize at 266 | DB queries at 213-291 |
| 294 | `_compute_account_projections(accounts, all_transactions, all_shadow_income, all_periods, current_period, params)` | list[dict] | Dispatches by account type: interest -> `calculate_balances_with_interest`, no-params -> `calculate_balances`, loan -> `amortization_engine.get_loan_projection`, investment -> `_project_investment`. | account_balance, projected_end_balance, monthly_payment, loan_principal_real, growth | n/a (delegates) | filtered upstream by caller | all periods | **proj.current_balance at 373 (A-04: ARM = stored current_principal, fixed-rate = engine-computed)** | YES (for ARM at 373) | n/a (delegates) | balance_calculator.calculate_balances_with_interest at 335; balance_calculator.calculate_balances at 343; amortization_engine.get_loan_projection at 362; _project_investment at 389 |
| 802 | `_compute_debt_summary(account_data, escrow_map)` | dict \| None | Aggregates monthly P&I+escrow across loan accounts for DTI. | monthly_payment, debt_total, dti_ratio | n/a | n/a | n/a | `ad["monthly_payment"]` at 846 | no | Decimal('0.01') quantize at 851, 873 | aggregation only |

Numerous private helpers in this file produce balance/goal/projection data; the four entries above are the load-bearing public-facing functions. Phase 3 must specifically compare `_compute_account_projections` line 373 against `balance_calculator.calculate_balances` results for the same account on the same period, as A-04 and the developer's reported symptom #5 (`/accounts` vs `/savings` divergence) make this the central question.

#### `app/services/retirement_dashboard_service.py` (500 lines)

Imports flagged: none. Reads `budget.transfers`? NO.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 79 | `compute_gap_data(user_id, swr_override=None, return_rate_override=None)` | dict | /retirement page: pension benefit, account projections, income gap, chart data. | paycheck_gross, paycheck_net, savings_total, growth, employer_contribution, pension_benefit_monthly | n/a (delegates) | n/a (delegates) | current period -> retirement date | n/a (delegates) | no | salary conversion quantize at 197; take-home rate at 214; SWR income at 240-241 | pension_calculator.calculate_benefit; paycheck_calculator.project_salary; retirement_gap_calculator.calculate_gap; growth_engine.project_balance |
| 257 | `compute_slider_defaults(data)` | dict | Computes balance-weighted average return rate and converts stored SWR (Decimal(0.04)) to display percentage (Decimal(4.00)). | growth, apy_interest | n/a | n/a | n/a | InvestmentParams.assumed_annual_return; UserSettings.safe_withdrawal_rate | no | SWR percentage conversion at 307-308 (PA-04 float-cast finding lives in this helper per priors) | balance-weighted loop at 318-324 |
| 338 | `_project_retirement_accounts(user_id, accounts, all_periods, current_period, planned_retirement_date, salary_profiles, traditional_type_ids, return_rate_override)` | list[dict] | Projects each retirement account forward via `growth_engine.project_balance` using shadow income contributions. | account_balance, projected_end_balance, growth, employer_contribution | n/a | shadow income at 376 (`transfer_id IS NOT NULL AND income type`) | through retirement date | **acct.current_anchor_balance at 405, 441-442** | YES (stored column at 405, 442) | salary_gross_biweekly quantize at 390 | balance_calculator.calculate_balances at 420; growth_engine.project_balance at 480 |

#### `app/services/year_end_summary_service.py` (2248 lines)

Imports flagged: none. Reads `budget.transfers`? **YES** at `_compute_transfers_summary` line 658 (`db.session.query(Transfer)`). Classification: LEGITIMATE -- this is display aggregation for the year-end transfers tab, NOT a balance computation, so Transfer Invariant 5 (which scopes to balance calculation) is not implicated. Phase 3 should still record this read in the transfer-model audit.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 66 | `compute_year_end_summary(user_id, year)` | dict | Entry point: aggregates the year-end tab (income/tax, spending, transfers, net worth, debt, savings, timeliness). | year_summary_jan1_balance, year_summary_dec31_balance, year_summary_principal_paid, year_summary_growth, year_summary_employer_total | n/a (delegates) | n/a (delegates) | calendar year | n/a (delegates) | no | n/a (delegates) | _load_common_data at 86; _build_summary at 87 |
| 380 | `_compute_mortgage_interest(year, debt_schedules)` | Decimal | A-06 aggregation: sums `row.interest` for amortization schedule rows whose `payment_date.year == year`. The escrow-subtraction and biweekly-redistribution preprocessing happens upstream in `loan_payment_service.prepare_payments_for_engine`. | interest_paid_per_period, total_interest, year_summary_growth | n/a | n/a | calendar year (payment_date.year filter) | row.interest from schedule (line 406) | no | n/a (sum of pre-quantized rows) | schedule traversal at 403-407 |
| 414 | `_compute_spending_by_category(user_id, year, period_ids, scenario_id)` | list[dict] | Groups settled expense transactions by `(group_name, item_name)` using `_attribution_year` (`COALESCE(due_date, pp_start_date)`); sums `abs(txn.effective_amount)`; attaches `entry_breakdown` from `_compute_entry_breakdowns` for envelope-tracked items (OP-3). | period_subtotal, effective_amount | settled via `_query_settled_expenses` (filter at line 1332+) | expense via `_query_settled_expenses` | calendar year via `period_ids` and `_attribution_year` (454) | `txn.effective_amount` at 457 | no | n/a (sum of model property) | `_query_settled_expenses` at 445; `_attribution_year` at 454; `_txn_category_names` at 456; `_build_spending_hierarchy` at 459; `_compute_entry_breakdowns` at 462 |
| 475 | `_compute_entry_breakdowns(user_id, year, period_ids, scenario_id)` | dict | Per-category envelope entry sums with credit/debit partition. | effective_amount (envelope source) | settled at 547 | expense at 546 | calendar year via period_ids (544) | TransactionEntry.amount at 519, 527 (`func.sum`, conditional `case(is_credit)`) | YES (sum entry.amount directly) | n/a (SQL); per-entry average quantized at 626-627 | SQL aggregate query |
| 518-528 | (SQL aggregates) | rows | The two money SQL aggregates per P1-a section 1.6: `func.sum(TransactionEntry.amount)` and conditional credit-entry sum via `case((is_credit, amount), else_=0)`. Filter set (lines 541-549): user, scenario, period_ids, is_deleted=False, expense type, settled status, `template.is_envelope=True`. | effective_amount (envelope entry source) | settled at 547 | expense at 546 | calendar year (544) | TransactionEntry.amount direct | YES (intentional per envelope semantics) | none at SQL layer | embedded in query |
| 636 | `_compute_transfers_summary(user_id, period_ids, scenario_id)` | list[dict] | Queries `Transfer` model directly (NOT shadow transactions); groups transfers by destination account for the year; sums `Transfer.amount`. | transfer_amount | excludes via `_get_excluded_status_ids()` at 655, filter at 665 (excludes CREDIT/CANCELLED) | n/a (queries Transfer, not Transaction) | calendar year via period_ids (663) | `Transfer.amount` (read at 679 via `t.amount`) | n/a (no Transaction.effective_amount involved) | none here; sums column-precision Decimal | db.session.query(Transfer) at 658; joinedload(Transfer.to_account) at 659 |
| 689 | `_compute_net_worth(year, accounts, all_periods, scenario, debt_schedules=None, ctx=None)` | dict | Monthly net worth (12 samples + Jan 1 + Dec 31) via balance projections and debt schedule lookups. Delegates anchor reads to `_get_account_balance_map` (line 2036). | net_worth, account_balance, debt_total, year_summary_jan1_balance, year_summary_dec31_balance | n/a | n/a | Jan 1, monthly, Dec 31 | n/a (delegates) -- the anchor-balance read happens inside `_get_account_balance_map` (line 2036+) | no (at this layer; delegate reads stored column) | n/a (delegates) | _build_account_data at 727; _get_month_end_periods at 726; _find_period_before_date at 731; _compute_monthly_values at 737 |
| 824 | `_compute_debt_progress(user_id, scenario_id, year, debt_schedules, balance_map, debt_accounts)` | dict | Per-debt principal-paid and interest accrued for the year via amortization schedule traversal. | year_summary_principal_paid, year_summary_growth, debt_total | n/a | n/a | calendar year by payment_date | row.principal, row.interest | no | n/a (schedule pre-quantized) | schedule traversal at 850-886 |
| 887 | `_compute_savings_progress(savings_accounts, period_ids, scenario_id, all_periods, year, scenario, ctx)` | list[dict] | Per-savings-account growth, contributions, employer_total. Dispatches to `_project_investment_for_year` (line 1027) for investment accounts, `_compute_interest_for_year` (line 1207) for HYSA, `_get_account_balance_map` (line 2036) for plain savings. | savings_total, year_summary_growth, year_summary_employer_total, goal_progress | n/a | n/a (delegates) | through year | n/a at this layer (delegates) | no (delegate reads stored column; see helpers) | inherits from helpers | _sum_shadow_income at 929; _project_investment_for_year at 938; _compute_interest_for_year (at later branch); _get_account_balance_map at 944, 961 |
| 1207 | `_compute_interest_for_year(account, interest_params, scenario, all_periods, year)` | Decimal | Projects per-year interest on an interest-bearing savings account via the balance calculator's interest path. | apy_interest, year_summary_growth | n/a | n/a | through year | **account.current_anchor_balance at 1244** | YES (stored column) | inherits from balance_calculator | balance_calculator.calculate_balances_with_interest at 1245 |
| 1263 | `_compute_payment_timeliness(user_id, period_ids, scenario_id)` | dict | Average days_paid_before_due across settled expense transactions (paid_on_time vs paid_late counts plus mean days-before-due). | - (non-financial; counts plus an average integer-days figure) | settled (`_get_settled_status_ids` filter applied via `_query_settled_expenses`) | expense (via `_query_settled_expenses` at line 1332) | period_ids (year-scoped at caller) | `txn.days_paid_before_due` model property | no | Decimal('0.01'), ROUND_HALF_UP at 1319 | `_query_settled_expenses` at 1332 |
| 1465-1469 | (ARM schedule anchor in `_balance_from_schedule_at_date`) | row | A-04: anchors the loan schedule at `current_principal` for ARM accounts so the projected schedule starts from the user-verified principal rather than from origination. | loan_principal_stored | n/a | n/a | n/a | LoanParams.current_principal | YES (per A-04 dual policy) | n/a | embedded |

The full file has 30+ private helpers; the above are the public-facing and load-bearing functions Phase 3 will trace through.

#### `app/services/budget_variance_service.py` (431 lines)

Imports flagged: none. Reads `budget.transfers`? NO.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 99 | `compute_variance(user_id, window_type, period_id=None, month=None, year=None, account_id=None)` | VarianceReport | Estimated vs actual variance grouped by category; supports pay period / calendar month / calendar year windows. | effective_amount, period_subtotal | excludes CREDIT and CANCELLED at 207-210 | all types | period/month/year (212-222) | n/a (delegates) | no | variance_pct quantize at 149 | _get_transactions_for_window; _build_group_hierarchy; variance computation 132-151 |
| 358 | `_build_txn_variance(txn)` | TransactionVariance | Per-transaction (actual - estimated, percentage). | effective_amount | n/a | n/a | n/a | actual_amount at 390; estimated_amount at 391-393 | YES (actual_amount directly at 390) | n/a | status.is_settled at 376 |
| 381 | `_compute_actual(txn)` | Decimal | Reads actual if settled and non-null, else estimated; mirrors `effective_amount` logic by hand. | effective_amount | status.is_settled at 389 | n/a | n/a | actual_amount at 390; estimated_amount at 391-393 | YES (direct two-column read) | n/a | status check |
| 396 | `_pct(variance, estimated)` | Decimal \| None | `variance / estimated * 100`, guarding zero. | - | n/a | n/a | n/a | n/a | no | Decimal('0.01'), ROUND_HALF_UP, line 404 | none |

`_build_txn_variance`/`_compute_actual` reimplement the `effective_amount` logic inline. Phase 3 must compare this hand-rolled version against `Transaction.effective_amount` at `transaction.py:221-245` to confirm they agree on every input (especially around `is_deleted` and `status.excludes_from_balance`).

#### `app/services/spending_trend_service.py` (535 lines)

Imports flagged: none. Reads `budget.transfers`? NO.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 97 | `compute_trends(user_id, threshold=Decimal("0.1000"), account_id=None)` | TrendReport | Per-category linear-regression trend over a 3 or 6 month rolling window. | growth, period_subtotal, effective_amount | settled status set at 173 | expense at 172 | rolling window (133-137) | effective_amount at 322 (via abs()) | no | period_average at 328; absolute_change at 332; pct_change at 337 (Decimal('0.01'), ROUND_HALF_UP) | data load and trend computation 129-155 |
| 265 | `_build_item_trends(transactions, periods, threshold)` | list[ItemTrend] | Groups transactions by category; computes per-period totals and regression slope/intercept. | growth, period_subtotal | n/a (pre-filtered) | n/a (pre-filtered) | window | effective_amount at 322 | no | quantize at 328, 332 | _compute_item_trend at 288 |
| 296 | `_compute_item_trend(cat_id, txns, period_index_map, n_periods, threshold)` | ItemTrend | Per-category metrics: per-period totals, regression, pct_change. | growth, period_subtotal | n/a | n/a | window | effective_amount at 322 | no | quantize at 328, 332; pct_change via _safe_pct_change at 482 | _compute_linear_regression at 331 |
| 360 | `_build_group_trends(items, threshold)` | list[GroupTrend] | Aggregates item trends to group level with spending-weighted pct_change. | growth, effective_amount | n/a | n/a | n/a | n/a (item-level) | no | weighted_pct quantize at 385 | group aggregation 374-397 |
| 425 | `_compute_linear_regression(values)` | (Decimal, Decimal) | OLS regression over equally spaced Decimal data points. | - | n/a | n/a | n/a | n/a | no | n/a (Decimal arithmetic; caller quantizes) | none |
| 470 | `_safe_pct_change(first_predicted, last_predicted)` | Decimal | `(last - first) / first * 100`, guarding zero. | growth | n/a | n/a | n/a | n/a | no | Decimal('0.01'), ROUND_HALF_UP, line 482 | none |

#### `app/services/debt_strategy_service.py` (703 lines)

Imports flagged: none. Reads `budget.transfers`? NO.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 521 | `calculate_strategy(debts, extra_monthly, strategy, custom_order=None, start_date=None, max_horizon_months=600)` | StrategyResult | Month-by-month avalanche/snowball/custom simulation with interest accrual, minimum payments, and extra-payment cascade as debts are retired. | debt_total, monthly_payment, payoff_date, total_interest, principal_paid_per_period | n/a (pure simulation) | n/a | simulation horizon (max_horizon_months) | DebtAccount fields: current_principal, interest_rate, minimum_payment | no | interest quantize at 413; balance snap via `_snap_to_zero`; totals quantize at 679-680 | _validate_inputs, _sort_debts at 571; simulation 600-624 |

The file contains 14 private helpers (interest accrual, payment cascade, strategy ordering); they are deterministic given the inputs and Phase 6 should look for DRY opportunities here.

#### `app/services/savings_goal_service.py` (488 lines)

Imports flagged: none. Reads `budget.transfers`? NO.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 21 | `resolve_goal_target(goal_mode_id, target_amount, income_unit_id, income_multiplier, net_biweekly_pay)` | Decimal | Fixed amount or income-multiplier-driven target. | goal_progress, effective_amount (compare) | n/a | n/a | n/a | n/a | no | Decimal('0.01'), ROUND_HALF_UP, line 106 | mode dispatch at 68-106 |
| 109 | `calculate_required_contribution(current_balance, target_amount, remaining_periods)` | Decimal \| None | `gap / remaining_periods`, quantized. | goal_progress, period_subtotal | n/a | n/a | input count | n/a | no | quantize at 134-135 | none |
| 139 | `calculate_savings_metrics(savings_balance, average_monthly_expenses)` | dict | Emergency fund coverage: months, paychecks, years. | emergency_fund_coverage_months, effective_amount | n/a | n/a | n/a | n/a | no | months/paychecks/years quantize to 0.1 at 163-171 | none |
| 178 | `count_periods_until(target_date, periods)` | int | Periods between today and target_date. | - | n/a | n/a | target_date range | n/a | no | n/a | none |
| 199 | `amount_to_monthly(amount, pattern_id, interval_n=1)` | Decimal \| None | Converts per-occurrence amount to monthly equivalent by recurrence pattern. | period_subtotal, effective_amount | n/a | n/a | n/a | n/a | no | none here (caller responsible) | pattern dispatch 236-280 |
| 287 | `compute_committed_monthly(expense_templates, transfer_templates)` | Decimal | Sums monthly-equivalent costs from all active recurrence templates. | period_subtotal, effective_amount | n/a | n/a | n/a | template.default_amount at 311 | no | quantize at 328 | template loop 310-327 |
| 331 | `calculate_trajectory(current_balance, target_amount, monthly_contribution, target_date=None)` | dict | Months-to-goal, ahead/on-track/behind pace, required monthly rate. | goal_progress, period_subtotal | n/a | n/a | today -> target_date | n/a | no | ceiling division at 391-394; required_monthly Decimal('0.01'), ROUND_CEILING at 462-463 | _add_months at 397 |

#### `app/services/calendar_service.py` (516 lines)

Imports flagged: none. Reads `budget.transfers`? NO.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 88 | `get_month_detail(user_id, year, month, account_id=None, large_threshold=500)` | MonthSummary | Per-day calendar view of one month: txns, large/infrequent flags, month-end balance. | period_subtotal, effective_amount, checking_balance, projected_end_balance | all statuses (display) | all types (display) | calendar month | effective_amount at 255 (via helper) | no | n/a (delegates) | _query_transactions_for_range at 126; _build_month_summary at 130 |
| 136 | `get_year_overview(user_id, year, account_id=None, large_threshold=500)` | YearOverview | All-year calendar overview; one query, then partitions per month in Python. | period_subtotal, effective_amount, checking_balance, projected_end_balance, year_summary_jan1_balance, year_summary_dec31_balance | all statuses | all types | calendar year (12 months) | n/a (delegates) | no | n/a | 12-month partition at 166-177 |
| 240 | `_build_day_entry(txn, income_type_id, threshold)` | DayEntry | Converts one transaction to a per-day entry: income/paid/large/infrequent flags. | effective_amount, period_subtotal | n/a | check at 261 | n/a | effective_amount at 255 | no | n/a | _is_infrequent at 263 |
| 270 | `_assign_transactions_to_days(transactions, year, month, large_threshold)` | (dict, Decimal, Decimal) | Assigns txns to days (dedup by id), totals income/expense. | period_subtotal, effective_amount | n/a | n/a | calendar month | via _build_day_entry | no | n/a (sums) | _build_day_entry 293-310 |
| 313 | `_build_month_summary(year, month, account, periods, transactions, large_threshold, user_id, scenario)` | MonthSummary | Assembles month-end balance and totals. | period_subtotal, checking_balance, projected_end_balance | n/a | n/a | month + overlapping periods | n/a | no | n/a | _compute_month_end_balance at 435 |
| 435 | `_compute_month_end_balance(account, year, month, user_id, scenario)` | Decimal | Looks up last period ending on/before month-end and returns its balance from `balance_calculator.calculate_balances`. | checking_balance, projected_end_balance | n/a (delegates) | n/a (delegates) | last period on/before month-end (464) | **account.current_anchor_balance at 483** | YES (stored column) | n/a (delegates) | balance_calculator.calculate_balances 454-489 |

#### `app/services/companion_service.py` (167 lines)

Imports flagged: none. Reads `budget.transfers`? NO.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 92 | `get_visible_transactions(companion_user_id, period_id=None)` | (list[Transaction], PayPeriod) | Returns the linked-owner's transactions whose template has `companion_visible=True` for one period. | effective_amount (deferred) | all (UI filters) | all | single period | ORM passthrough | no | n/a | transaction query 119-145 |
| 150 | `get_companion_periods(companion_user_id)` | list[PayPeriod] | Returns linked-owner's periods for navigation. | - | n/a | n/a | all periods | n/a | no | n/a | pay_period_service.get_all_periods at 164 |

### Group C: Transactional and workflow services

14 files inventoried. Total 5,135 LOC. Includes the only service permitted to mutate transfer shadows (`transfer_service.py`), the carry-forward branch dispatcher (`carry_forward_service.py`), both recurrence engines, the credit and entry-credit workflows, and resolvers/utilities. Zero Flask imports. Zero shadow mutations outside `transfer_service`.

#### `app/services/transaction_service.py` (168 lines)

Imports flagged: none. Mutates shadow transactions outside `transfer_service`? NO (precondition at line 111 refuses `transfer_id IS NOT NULL`).

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 38 | `settle_from_entries(txn, *, paid_at=None)` | None | A-02 envelope settle: validates preconditions (not deleted, not transfer shadow, envelope template, status mutable), computes `actual_amount = sum(entries)` via `entry_service.compute_actual_from_entries`, sets status to DONE (expense) or RECEIVED (income) at lines 144-147, sets `paid_at` (default `db.func.now()`); does NOT change `pay_period_id`. | effective_amount | precondition: status.is_immutable=False at 130-138; outcome: DONE/RECEIVED at 144-147 | DONE for expense, RECEIVED for income (144-147) | unchanged | entries amount sum via helper at 153 | YES (entry.amount sum at 153) | none here (caller owns) | entry_service.compute_actual_from_entries at 153; ref_cache.status_id at 145, 147; log_event at 155 |

#### `app/services/transfer_service.py` (848 lines)

Imports flagged: none. Mutates shadow transactions outside `transfer_service`? NO (this IS the transfer service; mutations are authorized by Transfer Invariant 4).

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 283 | `create_transfer(user_id, from_account_id, to_account_id, pay_period_id, scenario_id, amount, status_id, category_id, notes=None, transfer_template_id=None, name=None, due_date=None)` | Transfer (+ 2 shadow Transactions, atomically flushed) | Validates ownership of all referenced entities; creates the parent `Transfer` and two shadow `Transaction` rows (expense at from_account, income at to_account); enforces Invariants 1 (two shadows), 2 (atomic creation), 3 (amount/status/period mirror parent). | transfer_amount | shadows initialized to `status_id` arg (typically PROJECTED) at 388, 409 | expense_type_id at 391; income_type_id at 412 | pay_period_id from caller at 387, 407 | amount Decimal-validated at 333; assigned to both shadows' `estimated_amount` at 392, 413 | no | none here (caller validates Decimal) | _validate_positive_amount at 333; _get_owned_account at 340, 343; _get_owned_period at 346; _get_owned_scenario at 347; _get_owned_category at 348; _get_owned_transfer_template at 349; ref_cache.txn_type_id at 352, 353; log_event at 424 |
| 443 | `update_transfer(transfer_id, user_id, **kwargs)` | Transfer (mutated in place) | Applies field updates; for status_id changes runs `state_machine.verify_transition` at 499 BEFORE shadow mutation; auto-syncs `paid_at` on settled transitions at 524-533; propagates amount/period/category to both shadows; rejects illegal transitions before any mutation. | transfer_amount | verify_transition guard at 499; transition rules drive shadow status sync at 500-502; settled-paid_at sync at 524-533 | n/a | new period validated and assigned to both shadows at 536-541 | new amount Decimal-validated at 485; assigned to both shadows at 487-488 | no | none here | _get_transfer_or_raise at 480; _get_shadow_transactions at 481; _validate_positive_amount at 485; state_machine.verify_transition at 499; db.session.get(Status, ...) at 525; _get_owned_period at 538; _get_owned_category at 549; log_event at 603 |
| 616 | `delete_transfer(transfer_id, user_id, soft=False)` | Transfer (if soft) or None (if hard) | Soft-delete: sets `is_deleted=True` on parent and both shadows explicitly (flag changes don't fire CASCADE). Hard-delete: lets `ON DELETE CASCADE` remove shadows, then queries to verify zero orphan shadows remain. | transfer_amount | n/a | n/a | n/a | n/a | no | n/a | _get_transfer_or_raise at 637; log_event at 651, 678 |
| 688 | `restore_transfer(transfer_id, user_id)` | Transfer | Sets `is_deleted=False` on parent and both shadows; verifies one expense + one income shadow exists; refuses restore if either account is archived (F-164); reconciles shadow `amount/status/period` drift that may have accumulated while soft-deleted. | transfer_amount | shadow status_id reconciled to parent at 821-828 | shadow types verified as one expense + one income at 755-769 | shadow pay_period_id reconciled to parent at 831-838 | shadow amounts reconciled to parent.amount at 811-818 | no | n/a | _get_transfer_or_raise at 716; db.session.get(Account, ...) at 780, 781; log_event at 841 |

Invariant enforcement summary:
- Invariant 1 (two shadows): create_transfer at 381-421.
- Invariant 2 (atomic): create_transfer flushes both together at 422.
- Invariant 3 (mirror): create_transfer 392/413; update_transfer 487/488; restore_transfer 811-818, 821-828, 831-838.
- Invariant 4 (only this service): all shadow mutations live here; carry_forward_service delegates to update_transfer at carry_forward_service.py:461-466 per A-07.
- Invariant 5 (balance_calculator does not query transfers): satisfied -- see balance_calculator.py inventory above.

#### `app/services/carry_forward_service.py` (1016 lines)

Imports flagged: none. Mutates shadow transactions outside `transfer_service`? NO (delegates to `transfer_service.update_transfer` for transfer-shadow moves at 461-466 per A-07).

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 210 | `_build_carry_forward_context(source_period_id, target_period_id, user_id, scenario_id)` | _CarryForwardContext | Validates ownership of both periods; queries source-period PROJECTED, non-deleted, scenario-scoped transactions; three-way partitions into transfer shadows (`transfer_id IS NOT NULL`), envelope (`template.is_envelope`), discrete (else). Per A-07, both A-02 envelope and prod_readiness_v1 discrete branches coexist. | - | PROJECTED only at 263 | partition by transfer_id (273) and template.is_envelope (275) | source period | n/a (read-only partition) | n/a | n/a | PayPeriod model at 235; ref_cache.status_id at 256 |
| 291 | `carry_forward_unpaid(source_period_id, target_period_id, user_id, scenario_id)` | int | Atomically applies all three branch mutations: envelope settle-and-roll via `_settle_source_and_roll_leftover` at 446 (per A-02), discrete bulk UPDATE moving rows to target with `is_override=True` at 415-416 (per A-07 W-192 discrete branch), transfer-shadow delegation to `transfer_service.update_transfer(pay_period_id=target, is_override=True)` at 461-466. Returns count of carried items. | effective_amount (envelope only) | source rows are PROJECTED (enforced by context) at 263 | per partition | source -> target | envelope: entries sum via `compute_actual_from_entries` inside the helper; discrete: row-level move; transfer: delegated | YES (envelope reads entry.amount; per A-02 intent) | none at this layer | _build_carry_forward_context at 335; Transaction.query bulk UPDATE at 407-437; transfer_service.update_transfer at 461; _settle_source_and_roll_leftover at 446; db.session.flush at 470; log_event at 471 |
| 482 | `preview_carry_forward(source_period_id, target_period_id, user_id, scenario_id)` | CarryForwardPreview | Read-only plan: emits one CarryForwardPlan per source row with blocked/actionable status; envelope plans compute target estimated before/after; transfer and discrete plans always actionable. | - | n/a (read-only) | n/a | n/a | n/a | n/a | n/a | _build_carry_forward_context at 532; _build_envelope_plan at 543; _build_discrete_plan at 547; _build_transfer_plan at 554 |
| 563 | `_build_envelope_plan(source_txn, target_period, scenario_id)` | CarryForwardPlan | Computes entries_sum, leftover, target estimated before/after; delegates target-row decisions (eight branches) to `_resolve_envelope_target_fields`. | effective_amount | n/a (planning) | n/a | target period | source entries sum via `compute_actual_from_entries` at 586 | YES (entries sum) | n/a | compute_actual_from_entries at 586; _resolve_envelope_target_fields at 590 |
| 602 | `_resolve_envelope_target_fields(source_txn, target_period, scenario_id, leftover)` | dict | Eight-branch decision tree (leftover==0 / duplicate targets / one mutable / one immutable / only soft-deleted / no template / template inactive / no rows + template active) determining whether carry-forward is blocked vs actionable. | effective_amount | target status.is_immutable check at 665, 970 | n/a | target period at 646 | n/a (leftover precomputed) | n/a | n/a | Transaction.query at 642; recurrence_engine.can_generate_in_period at 725 |
| 757 | `_build_discrete_plan(source_txn)` | CarryForwardPlan (blocked=False) | Discrete rows always actionable; whole-row move. | - | n/a | n/a | n/a | n/a | n/a | n/a | none |
| 772 | `_build_transfer_plan(shadow_txn)` | CarryForwardPlan (blocked=False) | Transfer shadows always actionable; delegates to transfer_service for the actual move. | transfer_amount | n/a | n/a | n/a | n/a | n/a | n/a | none |
| 788 | `_settle_source_and_roll_leftover(source_txn, target_period, scenario_id)` | None | A-02 settle-and-roll: computes entries_sum via `compute_actual_from_entries` at 878; calls `_find_or_generate_target_canonical` at 888; if leftover > 0, bumps `target_row.estimated_amount += leftover` and sets `target_row.is_override = True` at 891-893; settles source via `transaction_service.settle_from_entries`. | effective_amount (computed + bumped) | target.status.is_immutable at 970 | n/a | source -> target | entries sum at 878; estimated_amount additive bump at 891-893 | YES (entries) | none (column precision) | compute_actual_from_entries at 878; recurrence_engine deferred import at 870; transaction_service deferred import at 871; _find_or_generate_target_canonical at 888 |
| 899 | `_find_or_generate_target_canonical(source_txn, target_period, scenario_id, recurrence_engine)` | Transaction | Looks up existing non-deleted target row (template_id, target.id, scenario_id); if mutable, returns it; if immutable raises ValidationError; if absent, calls `recurrence_engine.generate_for_template` to materialize the canonical and returns it. | - | target.status.is_immutable at 970 | n/a | target period at 945 | n/a | n/a | n/a | Transaction.query at 941; recurrence_engine.generate_for_template at 999 |

Bulk UPDATE pattern (lines 405-437) uses `WHERE status_id == projected_id` to guard against race with concurrent `mark_done` (F-049 cite at 371-374); `synchronize_session="fetch"` keeps in-memory Transaction instances coherent with the UPDATE. The whole loop runs inside a `no_autoflush` block at line 358 to prevent partial-mutation autoflushes from violating the partial unique index `idx_transactions_template_period_scenario`.

#### `app/services/recurrence_engine.py` (775 lines)

Imports flagged: none. Mutates shadow transactions outside `transfer_service`? NO (refuses transfer-shadow mutation at line 412).

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 54 | `generate_for_template(template, periods, scenario_id, effective_from=None)` | list[Transaction] | Generates Transaction rows per matching period; per A-03, skips existing/overridden/soft-deleted rows. Each row initialized PROJECTED; estimated_amount from `_get_transaction_amount` (paycheck_calculator if salary-linked, else template.default_amount). | effective_amount (estimated source) | PROJECTED at 158; A-03 skip on `existing.is_override` at 128, on `existing.is_deleted` at 133 | from template at 161 | matching_periods via `_match_periods` at 107 | estimated_amount from `_get_transaction_amount` at 145; reads template.default_amount or paycheck breakdown | no | n/a (calculator owns precision) | Scenario.query at 75; _match_periods at 107; _get_existing_map at 110; _get_salary_profile at 113; _get_transaction_amount at 145; _compute_due_date at 150; log_event at 171 |
| 180 | `can_generate_in_period(template, period, scenario_id)` | bool | Predicate mirroring `generate_for_template` gating: no cross-user, rule exists, not ONCE pattern, period matches, no existing row (including soft-deleted). | - | n/a | n/a | single period at 238 | n/a | no | n/a | Scenario.query at 217; _match_periods at 238; _get_existing_map at 244 |
| 251 | `regenerate_for_template(template, periods, scenario_id, effective_from=None)` | list[Transaction] | Deletes auto-generated-unmodified rows on/after `effective_from`, regenerates from rule, raises `RecurrenceConflict` if overridden/deleted entries exist. | effective_amount | by `is_immutable`/`is_override`/`is_deleted` at 308-320 | n/a | PayPeriod.end_date >= effective_from at 298 | n/a (deletion) | n/a | n/a | Transaction.query + PayPeriod join at 292; log_event at 333 |
| 352 | `resolve_conflicts(transaction_ids, action, user_id, new_amount=None)` | None | Resolves override/delete conflicts post-regenerate; verifies ownership per `pay_period.user_id`; refuses transfer-shadow mutation at 412. | effective_amount (new_amount if supplied) | n/a | shadow guard at 412 | n/a | new_amount at 431 if supplied | no | n/a | Transaction.query at 382; log_resource_access_denied at 393; log_event at 434 |
| 447 | `_match_periods(rule, pattern_id, periods, effective_from)` | list[PayPeriod] | Filters periods by rule.end_date and effective_from; delegates to pattern-specific matcher. | - | n/a | n/a | filtered candidates at 466, 470 | n/a | n/a | n/a | _match_monthly at 481; _match_monthly_first at 484; _match_quarterly at 489; _match_semi_annual at 492; _match_annual at 499 |
| 506-607 | `_match_monthly`, `_match_monthly_first`, `_match_quarterly`, `_match_semi_annual`, `_match_specific_months`, `_match_annual` | list[PayPeriod] | Pattern-specific period-selection helpers; clamp day_of_month to month-end where needed. | - | n/a | n/a | per pattern | n/a | n/a | n/a | none |
| 610 | `_compute_due_date(rule, period)` | date | Primary: `rule.day_of_month` in period's month; override: `rule.due_day_of_month` (next-month convention if due_dom < dom). | - | n/a | n/a | within period | n/a | n/a | n/a | none |
| 680 | `_get_existing_map(template_id, scenario_id, periods)` | dict[int, list[Transaction]] | Builds period_id -> [Transaction] for existing template rows including soft-deleted and overridden. | - | n/a | n/a | supplied periods | n/a | n/a | n/a | Transaction.query at 693 |
| 708 | `_get_salary_profile(template_id)` | SalaryProfile \| None | Looks up active SalaryProfile for template. | - | n/a | n/a | n/a | n/a | n/a | n/a | SalaryProfile.query at 713 |
| 720 | `_get_transaction_amount(template, salary_profile, period, all_periods)` | Decimal | If salary-linked, calls `paycheck_calculator.calculate_paycheck` and returns `breakdown.net_pay`; else returns `template.default_amount`. Tax year derived from `period.start_date` with fallback to current year for future periods missing configs. | paycheck_net (if salary), effective_amount (template default) | n/a | n/a | period | template.default_amount at 734 or calculator result at 765 | no | n/a (calculator/column precision) | paycheck_calculator.calculate_paycheck at 761; tax_config_service.load_tax_configs at 741, 754 |

Per A-03: `is_override=True` blocks regeneration. The skip logic (lines 119-139) checks `is_override` first (line 128), then `is_deleted` (line 133), then treats remaining auto-generated-unmodified rows as already-existing.

#### `app/services/transfer_recurrence.py` (320 lines)

Imports flagged: none. Mutates shadow transactions outside `transfer_service`? NO (creates via `transfer_service.create_transfer` at line 114).

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 43 | `generate_for_template(template, periods, scenario_id, effective_from=None)` | list[Transfer] | Generates Transfer rows per matching period, skipping per A-03 (override/deleted/immutable); delegates each creation to `transfer_service.create_transfer` so shadows are created atomically. | transfer_amount | PROJECTED at 121; A-03 skip on `is_override` at 97, `is_deleted` at 100 | created shadows are expense + income (delegated) | matching periods from `_match_periods` at 85 | template.default_amount at 120 | no | n/a | Scenario.query at 59; _match_periods at 85; _get_existing_map at 86; transfer_service.create_transfer at 114; log_event at 130 |
| 141 | `regenerate_for_template(template, periods, scenario_id, effective_from=None)` | list[Transfer] | Deletes auto-generated-unmodified transfers on/after `effective_from`, regenerates, raises `RecurrenceConflict` if conflicts exist. | transfer_amount | classified by is_immutable/is_override/is_deleted at 187-196 | n/a | PayPeriod.end_date >= effective_from at 177 | n/a (deletion) | n/a | n/a | Transfer.query + PayPeriod join at 171; log_event at 206 |
| 224 | `resolve_conflicts(transfer_ids, action, user_id, new_amount=None)` | None | Resolves override/delete conflicts; restores soft-deleted transfers via `transfer_service.restore_transfer` at 278 then applies updates via `transfer_service.update_transfer` at 287. Ownership-checked at 263. | transfer_amount | n/a | n/a | n/a | new_amount at 285 if supplied | no | n/a | Transfer.query at 257; log_resource_access_denied at 264; transfer_service.restore_transfer at 278; transfer_service.update_transfer at 287; log_event at 291 |
| 302 | `_get_existing_map(template_id, scenario_id, periods)` | dict[int, list[Transfer]] | Builds period_id -> [Transfer] including soft-deleted/overridden. | - | n/a | n/a | supplied periods | n/a | n/a | n/a | Transfer.query at 308 |

#### `app/services/credit_workflow.py` (370 lines)

Imports flagged: none. Mutates shadow transactions outside `transfer_service`? NO (refuses transfer shadows at line 169).

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 33 | `lock_source_transaction_for_payback(transaction_id, owner_id)` | Transaction (row-locked) | `SELECT ... FOR NO KEY UPDATE` with `populate_existing()` for concurrent-safe payback creation (F-008); ownership via `pay_period.user_id`. | - | n/a | n/a | n/a | n/a | n/a | n/a | Transaction.query at 95 |
| 112 | `mark_as_credit(transaction_id, user_id)` | Transaction (newly created payback) | Locks source; verifies not income, not transfer shadow, not entry-capable, not already CREDIT; sets source status CREDIT; finds or creates CC Payback category; creates a PROJECTED expense payback in next period with amount = `actual_amount if not None else estimated_amount` at line 229 (mirrors `Transaction.effective_amount` selection logic by hand). | effective_amount (source); creates payback at projected amount | source must be PROJECTED at 192; new status CREDIT at lines 178-179 | source: expense only (guard at 166); payback: EXPENSE at 240 | source -> next period (222) | actual_amount at 229; estimated_amount at 229 | YES (direct two-column read at 229) | none here | lock_source_transaction_for_payback at 165; ref_cache.status_id at 178, 179; db.session.expire at 213; db.session.get(PayPeriod, ...) at 217; get_or_create_cc_category at 219; pay_period_service.get_next_period at 222; ref_cache.txn_type_id at 198; log_event at 247 |
| 259 | `unmark_credit(transaction_id, user_id)` | None | Reverts source from CREDIT to PROJECTED; deletes auto-created payback. Two guards: bespoke state check at 309 (must be CREDIT); `state_machine.verify_transition` at 319 (defense-in-depth). | effective_amount (source) | CREDIT at 309 -> PROJECTED at 322; verify_transition at 319 | n/a | n/a | n/a | no | n/a | Transaction.query at 296, 325; verify_transition at 319; log_event at 335 |
| 344 | `get_or_create_cc_category(user_id)` | Category | Looks up or creates the "Credit Card: Payback" Category. | - | n/a | n/a | n/a | n/a | n/a | n/a | Category.query at 353 |

#### `app/services/entry_credit_workflow.py` (236 lines)

Imports flagged: none. Mutates shadow transactions outside `transfer_service`? NO (entry-only mutations).

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 42 | `sync_entry_payback(transaction_id, owner_id)` | Transaction (payback) \| None | 2x2 state matrix on (total_credit > 0, payback exists): create / update / delete / no-op. Row-locks source via `lock_source_transaction_for_payback` for concurrent safety. | effective_amount (payback amount) | n/a | n/a | source -> next period | `sum(e.amount for e in credit_entries)` at 112-114 | YES (entry.amount direct sum) | none here (column precision) | lock_source_transaction_for_payback at 99; db.session.expire at 108; _create_payback at 126; log_event at 139, 160 |
| 170 | `_create_payback(txn, owner_id, credit_entries, total_credit)` | Transaction (payback) | Creates PROJECTED expense payback in next period with `estimated_amount = total_credit`; links every credit entry via `credit_payback_id`. | effective_amount | PROJECTED at 203 | EXPENSE at 204 | next period at 196 | total_credit (arg) | n/a | none here | pay_period_service.get_next_period at 196; get_or_create_cc_category at 202; ref_cache.status_id at 203; ref_cache.txn_type_id at 204; log_event at 226 |

#### `app/services/entry_service.py` (589 lines)

Imports flagged: none. Mutates shadow transactions outside `transfer_service`? NO (entries have no `transfer_id`).

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 47 | `_update_actual_if_paid(txn)` | None | If transaction status is DONE, re-computes `actual_amount` from entries (late-posting fix-up). | effective_amount | DONE only at 68 | n/a | n/a | entry sum via `compute_actual_from_entries` at 70 | YES (entry.amount sum) | none here | compute_actual_from_entries at 70; ref_cache.status_id at 68 |
| 73 | `resolve_owner_id(user_id)` | int | Owner accounts -> user_id; companions -> linked_owner_id. | - | n/a | n/a | n/a | n/a | n/a | n/a | User.query at 91; ref_cache.role_id at 94 |
| 109 | `create_entry(transaction_id, user_id, amount, description, entry_date, is_credit=False)` | TransactionEntry | Creates a purchase entry: ownership (companion-aware), entry-capable template, transfer-shadow guard, expense-only, status guards (refuses CANCELLED and CREDIT). Flushes, then syncs payback and updates actual_amount if DONE. | effective_amount (entry-aware) | refuses CANCELLED at 173, CREDIT at 177 | expense at 162 | n/a | passed amount (Decimal) | n/a | none here | resolve_owner_id at 140; Transaction.query at 142; ref_cache.status_id at 171, 172; sync_entry_payback at 205; _update_actual_if_paid at 206; log_event at 194 |
| 211 | `update_entry(entry_id, user_id, **kwargs)` | TransactionEntry | Updates entry fields; re-validates ownership; flushes, syncs payback, updates actual_amount if DONE. | effective_amount (entry-aware) | n/a | n/a | n/a | new amount if supplied | n/a | none here | resolve_owner_id at 246; TransactionEntry.query at 241; sync_entry_payback at 266; _update_actual_if_paid at 267; log_event at 254 |
| 272 | `delete_entry(entry_id, user_id)` | int | Hard-deletes entry; re-validates ownership; returns parent transaction_id so caller can sync. | effective_amount (entry-aware) | n/a | n/a | n/a | n/a | n/a | n/a | resolve_owner_id at 294; TransactionEntry.query at 289; sync_entry_payback at 312; _update_actual_if_paid at 313; log_event at 303 |
| 318 | `get_entries_for_transaction(transaction_id, user_id)` | list[TransactionEntry] | Returns entries ordered by entry_date; ownership via `pay_period.user_id`. | - | n/a | n/a | n/a | n/a | n/a | n/a | resolve_owner_id at 335; Transaction.query at 337 |
| 348 | `compute_entry_sums(entries)` | (Decimal, Decimal) | Partitions entries by `is_credit`; returns (debit_sum, credit_sum). | - | n/a | n/a | n/a | entry.amount at 365, 367 | YES (direct sum) | none (column precision) | none |
| 371 | `build_entry_sums_dict(transactions)` | dict[int, dict] | For each transaction, computes entry sums via `compute_entry_sums` and bundles `{debit, credit, total, count}`. | - | n/a | n/a | n/a | entry.amount at 395, 397-399 | YES | none | compute_entry_sums at 395 |
| 405 | `compute_remaining(estimated_amount, entries)` | Decimal | `estimated_amount - sum(all_entries)`; negative = overspent. | - | n/a | n/a | n/a | entry.amount at 424 | YES | none | none |
| 428 | `compute_actual_from_entries(entries)` | Decimal | `sum(all_entries)`; returns `Decimal("0")` for empty list. Source for settle_from_entries. | effective_amount (settle source) | n/a | n/a | n/a | entry.amount at 446 | YES | none | none |
| 449 | `check_entry_date_in_period(entry_date, transaction)` | bool | Informational (OP-4); does NOT block. | - | n/a | n/a | period span | n/a | n/a | n/a | none |
| 471 | `clear_entries_for_anchor_true_up(owner_id)` | int | Bulk-UPDATE: marks past-dated debit entries on PROJECTED parents as `is_cleared=TRUE` so balance_calculator stops double-counting. | - | PROJECTED only at 507 | n/a | n/a | n/a | n/a | n/a | ref_cache.status_id at 507; TransactionEntry.query at 514; Transaction join at 519; log_event at 535 |
| 546 | `toggle_cleared(entry_id, user_id)` | TransactionEntry | Manual override of `is_cleared` for a single entry. | - | n/a | n/a | n/a | n/a | n/a | n/a | resolve_owner_id at 572; TransactionEntry.query at 568; log_event at 579 |

#### `app/services/_recurrence_common.py` (116 lines)

Imports flagged: none. Mutates shadow transactions outside `transfer_service`? NO (logging only).

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 40 | `log_template_cross_user_blocked(logger, *, message, template_id, template_user_id, scenario_id)` | None | Emits `EVT_CROSS_USER_BLOCKED` warning for IDOR defense-in-depth (template and scenario must share user_id). | - | n/a | n/a | n/a | n/a | n/a | n/a | log_event at 74 |
| 83 | `log_resource_access_denied(logger, *, user_id, model, pk, owner_id)` | None | Emits `EVT_ACCESS_DENIED_CROSS_USER` warning for row-level IDOR detection in resolve_conflicts paths (F-144). | - | n/a | n/a | n/a | n/a | n/a | n/a | log_event at 108 |

#### `app/services/state_machine.py` (171 lines)

Imports flagged: none. Mutates shadow transactions outside `transfer_service`? NO (pure predicate).

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 77 | `_build_transitions()` | dict[int, set[int]] | Builds the legal-successor map keyed by status_id integer. Computed lazily on every `verify_transition` so test fixtures that re-seed ref_cache see fresh IDs. | - | n/a | n/a | n/a | n/a | n/a | n/a | ref_cache.status_id at 91-96 |
| 122 | `verify_transition(current_status_id, new_status_id, context="transaction")` | None (raises ValidationError on illegal) | Checks `new_status_id in transitions[current_status_id]`; identity transitions always legal. Workflow: projected -> {projected, done, received, credit, cancelled}; done/received -> {self, projected, settled}; credit -> {credit, projected}; cancelled -> {cancelled, projected}; settled -> {settled}. | - | from transitions dict at 161 | n/a | n/a | n/a | n/a | n/a | _build_transitions at 161 |

#### `app/services/pay_period_service.py` (216 lines)

Imports flagged: none. Mutates shadow transactions outside `transfer_service`? NO (PayPeriod-only operations).

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 24 | `generate_pay_periods(user_id, start_date, num_periods=52, cadence_days=14)` | list[PayPeriod] | Generates biweekly periods from start_date; assigns `period_index` starting at `max(existing) + 1` (uses `db.func.max(PayPeriod.period_index)` per P1-a section 1.6). Skips overlapping start_dates. | - | n/a | n/a | n/a | n/a | n/a | n/a | PayPeriod.query at 48; log_event at 85 |
| 96 | `get_current_period(user_id, as_of=None)` | PayPeriod \| None | Period containing `as_of` (default today). | - | n/a | n/a | single | n/a | n/a | n/a | PayPeriod.query at 109 |
| 120 | `get_periods_in_range(user_id, start_index, count)` | list[PayPeriod] | Returns `count` periods starting at `period_index = start_index`. | - | n/a | n/a | window | n/a | n/a | n/a | PayPeriod.query at 131 |
| 143 | `get_all_periods(user_id)` | list[PayPeriod] | All periods ordered by `period_index`. | - | n/a | n/a | all | n/a | n/a | n/a | PayPeriod.query at 152 |
| 160 | `get_next_period(period)` | PayPeriod \| None | Next sequential period (`period_index + 1`) for same user. | - | n/a | n/a | single | n/a | n/a | n/a | PayPeriod.query at 169 |
| 179 | `get_overlapping_periods(user_id, first_day, last_day)` | list[PayPeriod] | All periods whose [start_date, end_date] overlap [first_day, last_day]. Used by calendar/variance/spending services for month/year windowing. | - | n/a | n/a | overlap | n/a | n/a | n/a | PayPeriod.query at 207 |

#### `app/services/account_resolver.py` (131 lines)

Imports flagged: none. Mutates shadow transactions outside `transfer_service`? NO.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 36 | `resolve_grid_account(user_id, user_settings=None, override_account_id=None)` | Account \| None | 4-step fallback (override / settings default / first active checking / first active any-type). | account_balance | n/a | n/a | n/a | n/a | n/a | n/a | Account.query at 61, 71; ref_cache.acct_type_id at 60 |
| 79 | `resolve_analytics_account(user_id, account_id)` | Account \| None | 2-step fallback (explicit account_id with IDOR check / first active checking). Explicit failure returns None rather than silently picking a different account. | account_balance | n/a | n/a | n/a | n/a | n/a | n/a | Account.query at 122; ref_cache.acct_type_id at 121 |

#### `app/services/scenario_resolver.py` (48 lines)

Imports flagged: none. Mutates shadow transactions outside `transfer_service`? NO.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 22 | `get_baseline_scenario(user_id)` | Scenario \| None | Returns user's baseline scenario (`is_baseline=True`); enforced one-per-user via partial unique index. None when absent (test fixture or edge case). | - | n/a | n/a | n/a | n/a | n/a | n/a | Scenario.query at 44 |

#### `app/services/csv_export_service.py` (422 lines)

Imports flagged: none. Mutates shadow transactions outside `transfer_service`? NO (pure export).

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 28 | `_dec(value)` | str | Formats a numeric value to 2 decimals (ROUND_HALF_UP), '' for None. | - | n/a | n/a | n/a | n/a | n/a | Decimal('0.01'), ROUND_HALF_UP, line 39 | none |
| 42 | `_pct(value)` | str | Formats percentage to 2 decimals (ROUND_HALF_UP), '' for None. | - | n/a | n/a | n/a | n/a | n/a | Decimal('0.01'), ROUND_HALF_UP, line 53 | none |
| 56 | `_date(value)` | str | ISO 8601 date string; '' for None. | - | n/a | n/a | n/a | n/a | n/a | n/a | none |
| 72 | `_safe(value)` | str | str(value); '' for None. | - | n/a | n/a | n/a | n/a | n/a | n/a | none |
| 86 | `_bool_yn(value)` | str | 'Yes' or 'No'. | - | n/a | n/a | n/a | n/a | n/a | n/a | none |
| 98 | `_write_csv(rows)` | str | Uses `csv.writer` for proper quoting/escaping. | - | n/a | n/a | n/a | n/a | n/a | n/a | csv.writer at 108 |
| 116 | `export_calendar_csv(data, view_type)` | str | Dispatches to `_export_calendar_month` or `_export_calendar_year`. | - | n/a | n/a | n/a | n/a | n/a | inherits | _export_calendar_month at 134; _export_calendar_year at 166 |
| 134 | `_export_calendar_month(data)` | str | One row per transaction in day_entries; amounts via `_dec`. | effective_amount | n/a | n/a | month | entry.amount via _dec at 156 | n/a | inherits via _dec | _date at 152; _safe at 153-154; _dec at 156; _write_csv at 163 |
| 166 | `_export_calendar_year(data)` | str | One row per month summary. | effective_amount, period_subtotal | n/a | n/a | year | total_income/expenses/net/projected_end_balance via _dec at 190-193 | n/a | inherits via _dec | _dec at 190-193; _write_csv at 197 |
| 203 | `export_year_end_csv(data)` | str | Multi-section year-end CSV. | (multiple) | n/a | n/a | year | (section helpers) | n/a | inherits via _dec | section helpers at 216-222; _write_csv at 223 |
| 226 | `_add_income_section(rows, inc)` | None | Income/tax/deductions section. | paycheck_gross, federal_tax, state_tax, fica, pre_tax_deduction, post_tax_deduction, paycheck_net, total_interest | n/a | n/a | year | inc/ded keys via _dec at 230-250 | n/a | inherits via _dec | _dec at 230-250 |
| 253 | `_add_spending_section(rows, spending)` | None | Spending by category section. | effective_amount | n/a | n/a | year | group_total, item_total via _dec at 259-261 | n/a | inherits | _dec at 259-261 |
| 264 | `_add_transfers_section(rows, transfers)` | None | Transfers section. | transfer_amount | n/a | n/a | year | total_amount via _dec at 270 | n/a | inherits | _dec at 270 |
| 273 | `_add_net_worth_section(rows, nw)` | None | Net worth monthly + Jan 1 / Dec 31. | net_worth, year_summary_jan1_balance, year_summary_dec31_balance | n/a | n/a | year | balance/jan1/dec31/delta via _dec at 279-282 | n/a | inherits | _dec at 279-282 |
| 285 | `_add_debt_section(rows, debt)` | None | Debt progress section. | loan_principal_real, year_summary_principal_paid | n/a | n/a | year | jan1/dec31/principal_paid via _dec at 291-293 | n/a | inherits | _dec at 291-293 |
| 297 | `_add_savings_section(rows, savings)` | None | Savings progress section. | savings_total, year_summary_growth, year_summary_employer_total | n/a | n/a | year | jan1/dec31/contrib/employer/growth via _dec at 307-310 | n/a | inherits | _dec at 307-310 |
| 314 | `_add_timeliness_section(rows, pt)` | None | Payment timeliness (avg days before due). | - | n/a | n/a | year | avg_days via _dec at 324 | n/a | inherits | _dec at 324 |
| 330 | `export_variance_csv(report)` | str | 3-level CSV (Group/Item/Transaction) with amounts and percentages. | effective_amount | n/a | n/a | report-defined | estimated/actual/variance via _dec, _pct at 352-368 | n/a | inherits via _dec/_pct | _dec, _pct, _safe, _bool_yn, _write_csv |
| 383 | `export_trends_csv(report)` | str | Metadata header + trend rows. | period_subtotal | n/a | n/a | window | period_average/pct_change/absolute_change/avg_days via _dec/_pct at 413-419; threshold quantize at 395-396 | n/a | Decimal('0.01'), ROUND_HALF_UP at 395-396 (threshold) + inherits | _dec, _pct, _write_csv |

`csv_export_service.py` is the audit's cleanest A-01 example: every monetary cell passes through `_dec` (line 39) before stringification, and percentages through `_pct` (line 53), both at `Decimal('0.01')` with ROUND_HALF_UP.

### Cross-cutting: effective_amount bypasses (every site)

| file:line | Function | Context | Phase 3 verdict candidate |
| --- | --- | --- | --- |
| `balance_calculator.py:292` (374-378, 384-385) | `_entry_aware_amount` | Reads entries' `amount` and txn's `estimated_amount` directly to implement the cleared-entry checking-impact formula; intentional per docstring. | AGREE (intentional) -- but Phase 3 must verify the cleared-vs-uncleared partition matches the model's `effective_amount` for non-entry-aware shapes. |
| `dashboard_service.py:239` | `_entry_progress_fields` (via `compute_remaining`) | Reads `estimated_amount` to compute remaining budget against entry sum. | Q-08 (P1-b): should settled transactions use `actual_amount` for over-budget display? |
| `dashboard_service.py:245` | `_entry_progress_fields` | Over-budget compare against `estimated_amount`. | See Q-08. |
| `dashboard_service.py:350` | `_get_balance_info` | Reads `Account.current_anchor_balance` (stored column) for current period. | Phase 4 (source-of-truth audit) covers this column. |
| `savings_dashboard_service.py:373` | `_compute_account_projections` | Reads `proj.current_balance` for ARM (== stored `LoanParams.current_principal` per A-04) or balance-calculator-derived for fixed-rate. | AGREE per A-04 dual policy; Phase 3 must verify all six pages that show principal use the right side for the right loan type. |
| `retirement_dashboard_service.py:405, 441-442` | `_project_retirement_accounts` | Reads `acct.current_anchor_balance` (stored column). | Phase 4 covers. |
| `year_end_summary_service.py:519, 527` | `_compute_entry_breakdowns` SQL aggregates | `func.sum(TransactionEntry.amount)` and conditional credit-entry sum bypass the `Transaction.effective_amount` property. | AGREE (entry-aware envelope sum). |
| `year_end_summary_service.py:1123, 1124, 1244, 1784, 1806, 1861, 2096` | various private helpers (`_project_investment_for_year`, `_compute_interest_for_year`, `_lookup_balance_with_anchor_fallback`, `_compute_pre_anchor_interest`, `_get_account_balance_map`) | Reads `account.current_anchor_balance` (stored column). | Phase 4 covers. |
| `year_end_summary_service.py:1465-1469` | `_balance_from_schedule_at_date` | Anchors loan schedule at `LoanParams.current_principal` for ARM per A-04. | AGREE per A-04. |
| `budget_variance_service.py:390-393` | `_compute_actual` / `_build_txn_variance` | Inlines `actual_amount if status.is_settled and not None else estimated_amount`; hand-rolled `effective_amount` logic. | Phase 3 must compare to `transaction.py:221-245` for exact equivalence under all status/is_deleted/excludes_from_balance combinations. |
| `calendar_service.py:483` | `_compute_month_end_balance` | Reads `account.current_anchor_balance`. | Phase 4 covers. |
| `transaction_service.py:153` | `settle_from_entries` | `compute_actual_from_entries(txn.entries)` sums entries directly. | AGREE (entries are sub-transactional). |
| `carry_forward_service.py:586, 878` | `_build_envelope_plan`, `_settle_source_and_roll_leftover` | `compute_actual_from_entries(source_txn.entries)`. | AGREE per A-02. |
| `credit_workflow.py:229` | `mark_as_credit` | `txn.actual_amount if not None else txn.estimated_amount` to choose payback amount. Hand-rolled mirror of `effective_amount` logic. | Phase 3 must verify the omitted branches (is_deleted, status.excludes_from_balance) cannot apply at this call site. |
| `entry_credit_workflow.py:112-114` | `sync_entry_payback` | `sum((e.amount for e in credit_entries), Decimal("0"))`. | AGREE (entries are sub-transactional). |
| `entry_service.py:70` | `_update_actual_if_paid` | `compute_actual_from_entries(txn.entries)`. | AGREE. |
| `entry_service.py:365, 367, 395, 397-399, 424, 446` | entry-sum helpers | All read `entry.amount` directly. | AGREE (entries are sub-transactional). |
| `investment_projection.py:153, 187` | `calculate_investment_inputs` | Reads `t.estimated_amount` directly for shadow income contributions. Justified by upstream `status.excludes_from_balance` filter at 150. | Phase 3 must verify the upstream-filter contract is always honored by callers. |

Total: 25 effective_amount bypass sites across 12 files. All are intentional reads with documented justifications; none are read paths that compute a checking-account balance from transaction data, which would be a true violation of the `effective_amount` contract.

### Cross-cutting: missing quantization (per A-01)

NO display-or-storage-facing function returns money without quantization to `Decimal('0.01')`, ROUND_HALF_UP. The handful of helpers that return raw Decimal sums (`compute_entry_sums`, `compute_remaining`, `compute_actual_from_entries`, `_entry_aware_amount`'s `max()` result) feed into either (a) a column at `Numeric(12, 2)` precision (so storage rounds), (b) a calculator that quantizes (e.g., `paycheck_calculator.calculate_paycheck`), or (c) `csv_export_service._dec` at the display boundary. The `entry_service` chain is the most-bypassed surface but is feeding column-precision storage at every consumer site reviewed.

### Cross-cutting: Flask boundary violations

NONE. Zero `from flask import`, `current_app`, `request`, or `session` references in any service file in scope across all 36 files. The architecture rule (CLAUDE.md "Services are isolated from Flask") holds for the financial calculation surface.

The earlier grep at the top of this session matched `request`, `session`, or `current_app` as substrings in 25 files, but those matches are all in (a) docstrings, (b) variable names like `previous_period.start_date.year`, or (c) unrelated identifiers. No actual Flask object is imported or accessed by any service.

### Cross-cutting: transfer-model reads

The legitimate consumers of the `Transfer` model in scope:
- `transfer_service.py` (CRUD, the only authorized mutator).
- `transfer_recurrence.py` (template-driven creation and conflict resolution, all delegated to `transfer_service`).
- `carry_forward_service.py` (queries the partition list and delegates move to `transfer_service.update_transfer`).
- `year_end_summary_service.py:658` (`_compute_transfers_summary`: display aggregation for the year-end transfers tab). LEGITIMATE -- not a balance computation; Phase 3 should still record and classify.

Critically: `balance_calculator.py` does NOT query `budget.transfers`; verified by grep -- no `Transfer.query`, no `from app.models.transfer`, no `db.session.query(Transfer)`. Transfer Invariant 5 (which scopes to balance computation) is satisfied.

Phase 3 follow-up: run the explicit grep `grep -rn "Transfer.query\|db.session.query(Transfer)\|from app.models.transfer" app/` to find any additional Transfer reads in routes or other layers; P1-b's grep was confined to `app/services/`.

### Cross-cutting: shadow-transaction mutations outside transfer_service

NONE. All shadow-row writes route through `transfer_service.create_transfer` / `update_transfer` / `delete_transfer` / `restore_transfer`. Refused at:
- `recurrence_engine.py:412` (`resolve_conflicts` rejects `transfer_id IS NOT NULL`).
- `transaction_service.py:111` (`settle_from_entries` rejects transfer shadows).
- `entry_service.py:158` (`create_entry` rejects transfer shadows).
- `credit_workflow.py:169` (`mark_as_credit` rejects transfer shadows).

Transfer Invariant 4 is satisfied across the service surface.

### Citation-quality note (P1-b self-review, 2026-05-15)

The audit plan section 10.8 lists "trust-then-verify gap" as a known
failure pattern; the inventory itself can suffer the same problem the
audit is trying to surface in the codebase. Section 1.1 was produced by
three Explore subagents reading 36 files; after writing, the main
session spot-checked ~15 file:line citations and found multiple
errors in `year_end_summary_service.py` and `savings_dashboard_service.py`
(the two largest files). Citations have been corrected for:

- `savings_dashboard_service.py` `_compute_debt_summary`: was cited at
  line 700; actual line 802 (monthly_payment at 846, quantize at 851
  and 873). Fixed in the Group B table above.
- `year_end_summary_service.py` `_compute_savings_progress`,
  `_compute_net_worth`, `_compute_interest_for_year`: cited
  `current_anchor_balance` reads at 779, 952, 1003, 1220; actual
  reads are inside helpers `_project_investment_for_year` (1123, 1124),
  `_compute_interest_for_year` (1244), `_lookup_balance_with_anchor_fallback`
  (1784, 1806), `_compute_pre_anchor_interest` (1861), and
  `_get_account_balance_map` (2096). Fixed in the Group B table and
  cross-cutting bypass table above.
- `year_end_summary_service.py` `_compute_transfers_summary`: cited as
  reading `effective_amount at 680` with `settled` and `income` filters;
  actual code queries `Transfer` model directly (line 658) and sums
  `Transfer.amount` (line 679); status filter is `excluded_ids` (lines
  655, 665), NO transaction-type filter. The function reads
  `budget.transfers` -- the file's "Reads `budget.transfers`?" header
  has been corrected and the cross-cutting transfer-reads table now
  lists this site. Phase 3 classification: LEGITIMATE display
  aggregation, not balance computation.
- `year_end_summary_service.py` `_compute_spending_by_category`: cited
  status/type/period filters and `TransactionEntry.amount at 519`;
  actual function calls `_query_settled_expenses` at line 445 and sums
  `txn.effective_amount` at line 457. The cited filter set belongs to
  `_compute_entry_breakdowns` (the next function down), not this one.
  Fixed in the Group B table above.
- `year_end_summary_service.py` `_compute_payment_timeliness`: cited
  quantize at line 1330; actual line 1319. Fixed.

**Verified citations** (spot-checked against `grep`/`Read` in P1-b's
self-review):

- `amortization_engine.py:128, 178, 326, 649, 753, 864` (function
  definitions); `:436, 440, 491, 512, 693, 697, 952, 957`
  (`calculate_monthly_payment` call sites); `:977-985` (A-04 dual policy
  branch).
- `balance_calculator.py:35, 112, 176, 292, 389, 422` (function
  definitions); `:225, 231` (engine calls).
- `dashboard_service.py:40, 99, 167, 203, 252, 334, 375` (function
  definitions); `:239, 245` (`estimated_amount` reads); `:350`
  (`current_anchor_balance` read).
- `year_end_summary_service.py:66, 90, 188, 246, 310, 337, 380, 414,
  475, 577, 611, 636, 689, 750, 790, 824, 887, 986, 1027, 1173, 1207,
  1263, 1332, 1368, 1381, 1388, 1421, 1490, 1524, 1567, 1717, 1731,
  1752, 1811, 1876, 1909, 1938, 1986, 2019, 2028, 2036, 2131, 2157,
  2169, 2181, 2206, 2223, 2237` (all 48 function definitions);
  `:380-408` (A-06 `_compute_mortgage_interest`); `:518-528` (the two
  money SQL aggregates per P1-a section 1.6); `:1462-1469` (A-04 ARM
  schedule anchor); `:1244` (`_compute_interest_for_year`
  `current_anchor_balance` read).
- `transfer_service.py:283, 443, 616, 688` (the four public functions
  per Transfer Invariant 4).
- `carry_forward_service.py:210, 273-277 (three-branch partition), 291,
  482, 563, 602, 757, 772, 788, 891-894 (target canonical bump per
  A-02), 899`.
- `recurrence_engine.py:128 (A-03 is_override skip), 412 (transfer
  shadow guard)`.
- `transaction_service.py:38 (settle_from_entries per A-02), 153
  (entries sum)`.
- `credit_workflow.py:229` (`actual_amount`/`estimated_amount` fallback).
- `entry_service.py:47, 73, 109, 211, 272, 318, 348, 371, 405, 428,
  449, 471, 546` (all 13 function definitions).
- `csv_export_service.py:39, 53` (quantize sites).
- `savings_dashboard_service.py:61, 201, 294, 802 (corrected from 700)`
  (function definitions); `:335, 343, 362, 373` (engine calls and ARM
  current_balance per A-04); `:325, 921` (`current_anchor_balance`
  reads).

**Unverified citations** (relied on agent output without independent
spot-check). Phase 3 should re-verify any cell whose verdict turns on
the exact line:

- Most internal call-site lines in `dashboard_service`, `calendar_service`,
  `budget_variance_service`, `spending_trend_service`,
  `debt_strategy_service`, `savings_goal_service`, `companion_service`
  (Group B's smaller files).
- Most internal call-site lines in the calculation-engine files (Group A:
  `growth_engine`, `interest_projection`, `tax_calculator`,
  `paycheck_calculator`, `loan_payment_service`, `escrow_calculator`,
  `pension_calculator`, `retirement_gap_calculator`,
  `investment_projection`, `calibration_service`, `tax_config_service`).
- Most internal call-site lines in the workflow files (Group C:
  `transfer_service` body, `carry_forward_service` body,
  `recurrence_engine` body, `transfer_recurrence`, `credit_workflow`,
  `entry_credit_workflow`, `entry_service`, `_recurrence_common`,
  `state_machine`, `pay_period_service`, `account_resolver`,
  `scenario_resolver`, `csv_export_service` body beyond the two
  formatter functions).

**Reliability rating**: function-definition line numbers (from
`^def `/`^class ` greps) are reliable everywhere. Function-body
citations were spot-verified at the load-bearing sites (A-02 through
A-07 anchor points, the two money SQL aggregates, the transfer-shadow
guards, the quantization helpers, the eight ARM `calculate_monthly_payment`
A-05 sites and the six additional sites the audit found). Body citations
in the largest file (`year_end_summary_service.py`, 2248 lines) had a
~20% error rate against spot-checks and have been corrected here; Phase
3 should treat any year_end_summary_service citation NOT in the
"verified" list as a hypothesis to test, not a fact to rely on.

## 1.2 Route layer

23 route files under `app/routes/` (`__init__.py` plus 22 blueprints),
13,930 LOC total. Three Explore subagents ran in parallel, very thorough,
partitioned by domain (grid/transactional / account-and-loan /
aggregation-and-analytics). Each subagent read every file in scope IN
FULL (segmented for files over 800 lines) and was given verbatim accuracy
clauses that required Read-confirmation of each cited line before
emission. The main session sampled 15 rows per group (8+ from files
over 800 LOC each) and verified each by Read with a +/-5 line window.

### Out of scope per audit plan section 0.6

Files that do not prepare a financial figure for rendering. Listed for
exhaustiveness; their handlers are not inventoried.

| File | Lines | Reason |
| ---- | ----- | ------ |
| `app/routes/__init__.py` | 6 | Package init with module docstring only. |
| `app/routes/auth.py` | 1339 | Authentication, MFA, password reset; no financial figures. |
| `app/routes/categories.py` | 205 | Category CRUD (group / item names); no financial figures. |
| `app/routes/charts.py` | 22 | Permanent 301 redirect stub for /charts -> /analytics (`charts.py:18-22`). |
| `app/routes/health.py` | 54 | Health endpoint for the readiness probe; no financial figures. |
| `app/routes/pay_periods.py` | 54 | Pay-period table generation (date rows); no money rendered. |
| `app/routes/settings.py` | 559 | Settings + tax-config CRUD; renders stored configuration values, not computed money. |

In scope: 16 files, 11,691 LOC, organized into three groups below.

### 1.2.0 Quality control log

Per the verification protocol, each group's Explore return was sampled at
15 rows (8+ from files over 800 LOC). Each sampled row was re-read with
`Read` at the cited line (+/-5 line window) and confirmed against four
failure categories: (i) off-by-N line number outside the +/-2 tolerance,
(ii) hallucinated function or call, (iii) behavior misdescription
(paraphrased docstring), (iv) wrong file path.

| Group | Files in scope | Total rows | Sampled | Failures | Action | Notes |
| ----- | -------------- | ---------- | ------- | -------- | ------ | ----- |
| A     | grid.py, transactions.py, transfers.py, entries.py, companion.py | 37 | 15 (8 from >800 LOC: transactions.py + transfers.py) | 0 within tolerance | accepted | Three rows with `Inline=YES/NO` classification disagreements were corrected inline before append (see below); not citation failures. |
| B     | accounts.py, loan.py, retirement.py, investment.py, debt_strategy.py | 36 | 15 (11 from >800 LOC: accounts.py + loan.py + investment.py) | 0 within tolerance | accepted | One off-by-1 line citation at `retirement.py:50` (claimed call site for `compute_gap_data`; actual at `retirement.py:51`); within +/-2 tolerance. |
| C     | dashboard.py, savings.py, analytics.py, obligations.py, salary.py, templates.py | 41 | 15 (9 from >800 LOC: salary.py) | 0 within tolerance | accepted | Two off-by-1 decorator-line citations at `salary.py:101` (actual 102), `salary.py:148` (actual 149), `templates.py:162` (actual 163); within +/-2 tolerance. |

Inline classification corrections applied during QC (Group A):

- `grid.py:164` (`index`): Group A return marked `Inline=NO`; the QC
  re-read found inline Decimal arithmetic at lines 263-279 (subtotal loop
  `income += txn.effective_amount`, `expense += txn.effective_amount`,
  `net = income - expense`). The Group A return correctly raised the issue
  as ambiguity Q-002 in its own footer but the table cell was inconsistent.
  Corrected to `Inline=YES (subtotals at 263-279)` in the table below; the
  ambiguity is folded into Q-10 in `09_open_questions.md`.
- `transactions.py:909` (`create_inline`) and `transactions.py:987`
  (`create_transaction`): Group A return marked `Inline=YES` citing a
  "Decimal assignment" at lines 972 / 1017. The QC re-read found those
  lines are `txn = Transaction(**data)` where `data` comes from
  `_inline_create_schema.load(request.form)` (Marshmallow handles Decimal
  parsing). No inline arithmetic happens at the construction site;
  corrected to `Inline=NO` in the table below.

Systematic-error check: the off-by-1 citations in Group B (`retirement.py`)
and Group C (`salary.py` decorators, `templates.py` decorator) are all
shifted by +1 line in the same direction (Explore line numbers run 1
short of the actual line). The pattern is small enough (3 of 45 samples)
to remain inside the protocol's +/-2 tolerance, and is consistent with
agents counting from the function body rather than the decorator.
Phase 3 should treat all route-layer citations as ranges `[cited-1,
cited+2]` when grepping rather than absolute line anchors. The same
pattern was previously flagged in `year_end_summary_service.py` (P1-b's
"Citation-quality note"); the route-layer pass shows it concentrates in
files over 800 LOC. P1-b's largest service file (2248 lines) had a ~20%
spot-check error rate; the route layer's 0% within-tolerance rate
indicates the verbatim accuracy clauses and per-file segmentation worked
as intended for P1-c.

Out-of-scope misclassification spot-check (Group C `dashboard.py:54-139`
`mark_paid`): the Group C return marked this handler out-of-scope because
"it updates status/amount in DB and returns a partial row." The QC notes
the response IS an updated bill-row partial that re-renders financial
figures; per the audit-plan rule "in scope only if its response renders
financial figures," this handler IS in scope at the source level.
Added to Group C inventory below under `dashboard.py`; not a citation
failure but a scope misclassification.

### Group A: grid and transactional routes

Five files, 3,544 LOC. Two large files: `transactions.py` (1182) and
`transfers.py` (1320). Cross-references to section 1.1 service entries
are by name only -- the service file:line for each call appears in the
"Service calls" cell.

#### `app/routes/grid.py` (467 lines)

| @route line | HTTP | View fn | DB queries (file:line) | Service calls (fn @ file:line) | Context vars (name: token) | Template | HTMX target | Inline compute? |
| ----------- | ---- | ------- | ---------------------- | ------------------------------ | -------------------------- | -------- | ----------- | --------------- |
| 164 | GET | `index` | Transaction multi-filter at 226-234 (selectinload entries+template); Account / Category / PayPeriod via service helpers | `get_baseline_scenario` @ 177; `resolve_grid_account` @ 182; `pay_period_service.get_current_period` @ 197; `pay_period_service.get_periods_in_range` @ 205; `pay_period_service.get_all_periods` @ 210; `balance_calculator.calculate_balances` @ 243-248; `build_entry_sums_dict` @ 258 | `balances`: `checking_balance`/`projected_end_balance` (per-period dict @ 243); `subtotals`: `period_subtotal` dict income/expense/net @ 264-279; `anchor_balance`: `account_balance` @ 238; `entry_sums`: `entry_sum_total` dict @ 258; `current_period`: PayPeriod; `periods`: PayPeriod list; `account`: Account; `categories`: Category list | `grid/grid.html` | full-page render | YES (subtotal loop at 263-279) |
| 393 | GET | `balance_row` | Transaction multi-filter same as index | `balance_calculator.calculate_balances` @ 446-451; `pay_period_service.get_current_period` @ 420; `pay_period_service.get_periods_in_range` @ 425; `pay_period_service.get_all_periods` @ 426 | `balances`: `chart_balance_series` @ 446; `periods`: PayPeriod list; `account`: Account; `num_periods`, `start_offset`: int; `low_balance_threshold`: int | `grid/_balance_row.html` | `#balanceRow` (HTMX-targeted in caller; partial responds OOB) | NO |

Out-of-scope handlers in `grid.py`:

- `create_baseline` (POST around 364): bootstraps scenario rows then
  redirects; no financial figure in response body.

#### `app/routes/transactions.py` (1182 lines)

| @route line | HTTP | View fn | DB queries (file:line) | Service calls (fn @ file:line) | Context vars (name: token) | Template | HTMX target | Inline compute? |
| ----------- | ---- | ------- | ---------------------- | ------------------------------ | -------------------------- | -------- | ----------- | --------------- |
| 244 | GET | `get_cell` | Transaction via `_get_owned_transaction` (helper) | `build_entry_sums_dict` @ 88 | `txn`: Transaction; `entry_sums`: `entry_sum_total` | `grid/_transaction_cell.html` | `#txn-cell-{id}` | NO |
| 266 | GET | `get_full_edit` | Transaction via helper; Status @ 300; Category @ 286-290; Transfer @ 282 | -- | `txn`: Transaction; `xfer`: Transfer (optional); `statuses`: list; `categories`: list | `grid/_transaction_full_edit.html` OR `transfers/_transfer_full_edit.html` | `#txn-popover` | NO |
| 304 | PATCH | `update_transaction` | Transaction via helper; Status @ 398 | `transfer_service.update_transfer` @ 409-411 (shadow branch); `transaction_service.settle_from_entries` @ 596 (envelope branch); state-machine `verify_transition` @ 441, 607 | `txn`: Transaction (rendered cell) | `grid/_transaction_cell.html` | `#txn-cell-{id}`; trigger `balanceChanged` | NO |
| 491 | POST | `mark_done` | Transaction via helper; Status @ 535, 537 | `transfer_service.update_transfer` @ 553-555 (shadow branch); `transaction_service.settle_from_entries` @ 596 (envelope branch with entries); `verify_transition` @ 607 | `txn`: Transaction; sets `actual_amount` from form `MarkDoneSchema` (`effective_amount`) | `grid/_transaction_cell.html` | `#txn-cell-{id}`; trigger `gridRefresh` | NO |
| 632 | POST | `mark_credit` | Transaction via helper | `credit_workflow.mark_as_credit` @ 658 | `txn`: Transaction | `grid/_transaction_cell.html` | `#txn-cell-{id}`; trigger `gridRefresh` | NO |
| 675 | DELETE | `unmark_credit` | Transaction via helper | `credit_workflow.unmark_credit` @ 692 | `txn`: Transaction | `grid/_transaction_cell.html` | `#txn-cell-{id}`; trigger `gridRefresh` | NO |
| 713 | POST | `cancel_transaction` | Transaction via helper; Status @ 728 | `transfer_service.update_transfer` @ 733-736 (shadow); `verify_transition` @ 764 | `txn`: Transaction | `grid/_transaction_cell.html` | `#txn-cell-{id}`; trigger `gridRefresh` | NO |
| 783 | GET | `get_quick_create` | Category @ 799; PayPeriod @ 800; Account @ 801; Scenario @ 813 | `get_baseline_scenario` @ 813 | `category`: Category; `period`: PayPeriod; `account_id`/`scenario_id`/`transaction_type_id`: int | `grid/_transaction_quick_create.html` | `#txn-form-{cell}` | NO |
| 828 | GET | `get_full_create` | Category @ 844; PayPeriod @ 845; Account @ 846; Status @ 859; Scenario @ 855 | `get_baseline_scenario` @ 855 | `category`, `period`, `account_id`, `scenario_id`, `transaction_type_id`, `statuses` | `grid/_transaction_full_create.html` | `#txn-popover` | NO |
| 909 | POST | `create_inline` | Account @ 946; Category @ 951; PayPeriod @ 956; Scenario @ 961 | -- (validated Decimal from `_inline_create_schema.load` @ 943; model construction at 972) | `txn`: Transaction; HTMX-wrapped cell | `grid/_transaction_cell.html` | `#txn-row-{new-id}`; trigger `balanceChanged` | NO |
| 987 | POST | `create_transaction` | Account @ 999; PayPeriod @ 1004; Scenario @ 1009 | -- (validated Decimal from `_create_schema.load` @ 996; model construction at 1017) | `txn`: Transaction | `grid/_transaction_cell.html` | `#txn-popover`; trigger `balanceChanged` | NO |
| 1030 | DELETE | `delete_transaction` | Transaction via helper; soft-delete vs hard at 1053 | -- | -- (empty response) | -- | -- | NO |
| 1107 | GET | `carry_forward_preview` | PayPeriod / Scenario via `_resolve_carry_forward_context` @ 1134 | `pay_period_service.get_current_period` (in helper); `carry_forward_service.preview_carry_forward` @ 1140-1142 | `preview`: `CarryForwardPreview`; `source_period`/`current_period`: PayPeriod | `grid/_carry_forward_preview_modal.html` | `#carry-forward-modal` | NO |
| 1154 | POST | `carry_forward` | -- (delegates) | `carry_forward_service.carry_forward_unpaid` @ 1165-1167 | -- (empty 200 response) | -- | -- | NO |

Out-of-scope handlers in `transactions.py`:

- `get_quick_edit` (255): form retrieval only, no financial figure
  computed (the inline-amount input renders the existing value).
- `get_empty_cell` (872): placeholder rendering for an empty grid cell.

#### `app/routes/transfers.py` (1320 lines)

| @route line | HTTP | View fn | DB queries (file:line) | Service calls (fn @ file:line) | Context vars (name: token) | Template | HTMX target | Inline compute? |
| ----------- | ---- | ------- | ---------------------- | ------------------------------ | -------------------------- | -------- | ----------- | --------------- |
| 65 | GET | `list_transfer_templates` | TransferTemplate @ 74-79 | -- | `active_templates`, `archived_templates`: TransferTemplate lists | `transfers/list.html` | full-page | NO |
| 89 | GET | `new_transfer_template` | Account @ 94-99; Category @ 100-104; RecurrencePattern @ 106 | `pay_period_service.get_all_periods` @ 107; `pay_period_service.get_current_period` @ 108 | accounts, categories, patterns, periods, current_period, prefill_from / prefill_to | `transfers/form.html` | full-page | NO |
| 127 | POST | `create_transfer_template` | Account / Category / PayPeriod via `_user_owns` helper; RecurrencePattern @ 176 | `transfer_service.create_transfer` @ 239-250 (one-time branch); `transfer_recurrence.generate_for_template` @ 260-262 (recurring branch); `get_baseline_scenario` @ 235, 257; `pay_period_service.get_all_periods` @ 259 | `template`: TransferTemplate (redirect after success; re-renders form with errors otherwise) | `transfers/form.html` on error | -- | NO |
| 269 | GET | `edit_transfer_template` | TransferTemplate @ 274; Account @ 278-283; Category @ 284-289; RecurrencePattern @ 290 | -- | `template`, `accounts`, `categories`, `patterns`, `periods=[]`, `current_period=None` | `transfers/form.html` | full-page | NO |
| 303 | POST | `update_transfer_template` | TransferTemplate @ 318; RecurrencePattern @ 353; RecurrenceRule @ 357-376 | `transfer_recurrence.regenerate_for_template` @ 428-430; `get_baseline_scenario` @ 424; `pay_period_service.get_all_periods` @ 426 | -- (redirect) | -- | -- | NO |
| 466 | POST | `archive_transfer_template` | TransferTemplate @ 482; Transfer @ 490-498 | `transfer_service.delete_transfer` @ 502 (soft=True, per projected transfer) | -- (redirect) | -- | -- | NO |
| 527 | POST | `unarchive_transfer_template` | TransferTemplate @ 537; Transfer @ 545-553 | `transfer_service.restore_transfer` @ 558; `transfer_recurrence.generate_for_template` @ 566-568; `get_baseline_scenario` @ 563; `pay_period_service.get_all_periods` @ 565 | -- (redirect) | -- | -- | NO |
| 592 | POST | `hard_delete_transfer_template` | TransferTemplate @ 620; Transfer @ 667-671 | `transfer_service.delete_transfer` @ 645 (soft=True for projected) and @ 673 (soft=False for the rest) | -- (redirect) | -- | -- | NO |
| 698 | GET | `get_cell` | Transfer via `_get_owned_transfer` @ 703 | `account_resolver.resolve_grid_account` @ 706 | `xfer`: Transfer; `account`: Account | `transfers/_transfer_cell.html` | `#xfer-cell-{id}` | NO |
| 723 | GET | `get_full_edit` | Transfer via helper; Status @ 731; Category @ 732-737 | -- | `xfer`, `statuses`, `categories` | `transfers/_transfer_full_edit.html` | `#xfer-popover` | NO |
| 744 | PATCH | `update_transfer` | Transfer via helper; Category ownership @ 804-806 | `transfer_service.update_transfer` @ 814; `account_resolver.resolve_grid_account` @ 843 (for the response render path) | `xfer`: Transfer (or shadow `Transaction` via `_resolve_shadow_context` @ 833) | `grid/_transaction_cell.html` (shadow) OR `transfers/_transfer_cell.html` | `#txn-cell-{id}` (shadow) or `#xfer-cell-{id}`; trigger `balanceChanged` | NO |
| 850 | POST | `create_ad_hoc` | Account / PayPeriod / Scenario / Category via `_user_owns` (901-908) | `transfer_service.create_transfer` @ 920-931; `account_resolver.resolve_grid_account` @ 951 | `xfer`: Transfer; `account`: Account | `transfers/_transfer_cell.html` | `#xfer-row-{new-id}`; trigger `balanceChanged` | NO |
| 1020 | DELETE | `delete_transfer` | Transfer via helper; soft check @ 1039 | `transfer_service.delete_transfer` @ 1041 | -- (empty response) | -- | -- | NO |
| 1055 | POST | `mark_done` | Transfer via helper; Status @ 1071 | `transfer_service.update_transfer` @ 1084-1087 (sets `status_id=done`, `paid_at=now()`) | `xfer`: Transfer (or shadow `Transaction`); `account` on the non-shadow path | `grid/_transaction_cell.html` (shadow) OR `transfers/_transfer_cell.html` | `#txn-cell-{id}` (shadow) or `#xfer-cell-{id}`; trigger `gridRefresh`/`balanceChanged` | NO |
| 1118 | POST | `cancel_transfer` | Transfer via helper; Status @ 1130 | `transfer_service.update_transfer` @ 1132-1134 | `xfer`: Transfer (or shadow `Transaction`); `account` on the non-shadow path | `grid/_transaction_cell.html` (shadow) OR `transfers/_transfer_cell.html` | `#txn-cell-{id}` (shadow) or `#xfer-cell-{id}`; trigger `gridRefresh` | NO |

Out-of-scope handlers in `transfers.py`:

- `get_quick_edit` (712): form retrieval only.

#### `app/routes/entries.py` (414 lines)

`_render_entry_list` (helper at 83-120) is the shared rendering path
called by every entry route below; the helper's service calls are listed
once here and not repeated per row.

Helper service calls (`_render_entry_list`):

- `entry_service.get_entries_for_transaction` @ 101-103
- `entry_service.compute_remaining` @ 104-106 (produces `entry_remaining`)
- `entry_service.check_entry_date_in_period` @ 107-110

| @route line | HTTP | View fn | DB queries (file:line) | Service calls (fn @ file:line) | Context vars (name: token) | Template | HTMX target | Inline compute? |
| ----------- | ---- | ------- | ---------------------- | ------------------------------ | -------------------------- | -------- | ----------- | --------------- |
| 200 | GET | `list_entries` | Transaction via `_get_accessible_transaction` @ 208 | helper service calls above | `txn`: Transaction; `entries`: list; `remaining`: `entry_remaining`; `today`: ISO date; `editing_id`: int; `out_of_period_ids`: set; `conflict`: bool | `grid/_transaction_entries.html` | `#txn-entries-{id}` | NO |
| 215 | POST | `create_entry` | Transaction via helper @ 225 | `entry_service.create_entry` @ 235-239; helper service calls on render | as `list_entries` | `grid/_transaction_entries.html` | `#txn-entries-{id}`; trigger `balanceChanged` | NO |
| 255 | PATCH | `update_entry` | Transaction via helper; TransactionEntry @ 281; ownership @ 282 | `entry_service.update_entry` @ 302; helper service calls on render | as `list_entries` | `grid/_transaction_entries.html` | `#txn-entries-{id}`; trigger `balanceChanged` | NO |
| 323 | PATCH | `toggle_cleared` | Transaction via helper; TransactionEntry @ 351; ownership @ 352 | `entry_service.toggle_cleared` @ 356; helper service calls | as `list_entries` | `grid/_transaction_entries.html` | `#txn-entries-{id}`; trigger `balanceChanged` | NO |
| 371 | DELETE | `delete_entry` | Transaction via helper; TransactionEntry @ 389; ownership @ 390 | `entry_service.delete_entry` @ 394; helper service calls | as `list_entries` | `grid/_transaction_entries.html` | `#txn-entries-{id}`; trigger `balanceChanged` | NO |

#### `app/routes/companion.py` (161 lines)

`_build_entry_data` (helper at 28-64) is the shared per-transaction
dict assembler used by both companion handlers below; its inline-compute
classification applies to each row.

Helper inline-compute notes (`_build_entry_data`):

- Calls `entry_service.compute_entry_sums` and `entry_service.compute_remaining` for each transaction (lines 50-53).
- Computes `pct = float(total / txn.estimated_amount * Decimal("100"))` inline at lines 53-56; this is `goal_progress` derived in the route from service outputs.

| @route line | HTTP | View fn | DB queries (file:line) | Service calls (fn @ file:line) | Context vars (name: token) | Template | HTMX target | Inline compute? |
| ----------- | ---- | ------- | ---------------------- | ------------------------------ | -------------------------- | -------- | ----------- | --------------- |
| 80 | GET | `index` | -- (service-loaded) | `companion_service.get_visible_transactions` @ 95-97; `_build_entry_data` @ 109; `companion_service.get_previous_period` @ 111; `pay_period_service.get_next_period` @ 112 | `transactions`: Transaction list; `period`, `prev_period`, `next_period`: PayPeriod; `entry_data`: dict mapping `txn.id` -> `{total: entry_sum_total, remaining: entry_remaining, count: int, pct: goal_progress}` (assembled in helper) | `companion/index.html` | full-page | YES (`pct` at lines 53-56 in helper) |
| 124 | GET | `period_view` | -- (service-loaded) | `companion_service.get_visible_transactions` @ 142-145; `_build_entry_data` @ 149; `companion_service.get_previous_period` @ 151; `pay_period_service.get_next_period` @ 152 | as `index` plus `period_id` route arg | `companion/index.html` | full-page | YES (same helper) |

### Group B: account management and loan routes

Five files, 4,492 LOC. Three large files: `accounts.py` (1468),
`loan.py` (1295), `investment.py` (804).

#### `app/routes/accounts.py` (1468 lines)

| @route line | HTTP | View fn | DB queries (file:line) | Service calls (fn @ file:line) | Context vars (name: token) | Template | HTMX target | Inline compute? |
| ----------- | ---- | ------- | ---------------------- | ------------------------------ | -------------------------- | -------- | ----------- | --------------- |
| 229 | GET | `list_accounts` | Account @ 243-260 | -- | `active_accounts`, `archived_accounts`: lists; `account_types`: list; `types_in_use`: set | `accounts/list.html` | full-page | NO |
| 288 | POST | `create_account` | Account name uniqueness @ 313-315; InterestParams @ 339; InvestmentParams @ 349 | `pay_period_service.get_current_period` @ 323 | -- (redirect) | `accounts/form.html` on error | -- | NO |
| 398 | POST | `update_account` | Account @ 431; name uniqueness @ 182-185 | `_validate_update_account` @ 440; `pay_period_service.get_current_period` @ 458; `entry_service.clear_entries_for_anchor_true_up` @ 484 | -- (redirect) | `accounts/form.html` on error | -- | NO |
| 503 | POST | `archive_account` | TransferTemplate @ 522-532 | -- (direct mutation of `is_active`) | -- (redirect) | -- | -- | NO |
| 592 | POST | `hard_delete_account` | TransferTemplate @ 628-637; TransactionTemplate @ 649-651; Transfer @ 690-696; Transaction @ 703-705; LoanParams / InterestParams / InvestmentParams / EscrowComponent / RateHistory / SavingsGoal @ 711-716 | `archive_helpers.account_has_history` @ 663; `transfer_service.delete_transfer` @ 699 per transfer | -- (redirect) | -- | -- | NO |
| 747 | PATCH | `inline_anchor_update` | Account @ 765; AccountAnchorHistory insert | `pay_period_service.get_current_period` @ 791; `entry_service.clear_entries_for_anchor_true_up` @ 811 | `acct`: Account (with updated `current_anchor_balance`) | `accounts/_anchor_cell.html` | partial swap into anchor cell | YES (`new_balance = Decimal(str(data["anchor_balance"]))` @ 774) |
| 860 | GET | `inline_anchor_form` | Account @ 865 | -- | `acct`: Account; `editing=True` | `accounts/_anchor_cell.html` | partial | NO |
| 874 | GET | `inline_anchor_display` | Account @ 879 | -- | `acct`: Account; `editing=False` | `accounts/_anchor_cell.html` | partial | NO |
| 1053 | PATCH | `true_up` | Account @ 1074; AccountAnchorHistory insert @ 1110-1115; PayPeriod via service @ 1101 | `pay_period_service.get_current_period` @ 1101; `entry_service.clear_entries_for_anchor_true_up` @ 1137 | `account`: Account; `editing=False`; OOB `anchor-as-of` HTML built inline @ 1190-1194 | `grid/_anchor_edit.html` | partial; `hx-swap-oob` for `#anchor-as-of`; trigger `balanceChanged` | YES (`new_balance = Decimal(str(data["anchor_balance"]))` @ 1083) |
| 1198 | GET | `anchor_form` | Account @ 1203 | -- | `account`: Account; `editing=True` | `grid/_anchor_edit.html` | partial | NO |
| 1214 | GET | `anchor_display` | Account @ 1219 | -- | `account`: Account; `editing=False` | `grid/_anchor_edit.html` | partial | NO |
| 1233 | GET | `interest_detail` | Account @ 1238; InterestParams @ 1247-1250; Transaction @ 1272-1281 | `pay_period_service.get_all_periods` @ 1259; `pay_period_service.get_current_period` @ 1260; `get_baseline_scenario` @ 1262; `balance_calculator.calculate_balances_with_interest` @ 1291 | `account`: Account; `params`: InterestParams; `current_balance`: `account_balance` @ 1299; `projected`: `projected_end_balance` dict for 3/6/12-month horizons; `period_data`: list of `{period, balance, interest}` (`account_balance` + `apy_interest`) @ 1302-1309; `anchor_period`: PayPeriod | `accounts/interest_detail.html` | full-page | NO |
| 1331 | POST | `update_interest_params` | Account @ 1336; InterestParams @ 1351-1358 | -- | -- (redirect) | -- | -- | NO |
| 1376 | GET | `checking_detail` | Account @ 1387; Transaction @ 1407-1416 | `pay_period_service.get_all_periods` @ 1397; `pay_period_service.get_current_period` @ 1398; `get_baseline_scenario` @ 1400; `balance_calculator.calculate_balances` @ 1425 | `account`, `current_balance`: `account_balance` @ 1432; `projected`: `projected_end_balance` for 3/6/12-month horizons; `period_data`: list of `{period, balance}` | `accounts/checking_detail.html` | full-page | NO |

Out-of-scope handlers in `accounts.py`:

- `new_account` (272), `edit_account` (382): form display only, no
  financial computation.
- `unarchive_account` (561): mutates state then redirects.
- `create_account_type` (891), `update_account_type` (944),
  `delete_account_type` (1003): ref-table CRUD, no money.

#### `app/routes/loan.py` (1295 lines)

| @route line | HTTP | View fn | DB queries (file:line) | Service calls (fn @ file:line) | Context vars (name: token) | Template | HTMX target | Inline compute? |
| ----------- | ---- | ------- | ---------------------- | ------------------------------ | -------------------------- | -------- | ----------- | --------------- |
| 405 | GET | `dashboard` | Account / LoanParams via `_load_loan_account` @ 410; EscrowComponent via `_load_loan_context`; TransferTemplate @ 496-505; Account+AccountType join @ 522-532 | `_load_loan_context` @ 421; `amortization_engine.get_loan_projection` @ 429-431; `escrow_calculator.calculate_total_payment` @ 433-435; `_compute_payment_breakdown` @ 438-440; `amortization_engine.generate_schedule` @ 453-459, @ 480-... (chart); `_update_transfer_end_date` @ 514 | `account`, `params`: LoanParams; `summary`: `AmortizationSummary` (`monthly_payment`, `total_interest`, `payoff_date`); `monthly_escrow`: `escrow_per_period`; `total_payment`: `monthly_payment`; `payment_breakdown`: `{principal, interest, escrow}` (`principal_paid_per_period` + `interest_paid_per_period` + `escrow_per_period`); `chart_labels`/`chart_original`/`chart_committed`/`chart_floor`: `chart_balance_series`; `amortization_schedule`: list of AmortizationRow; `show_transfer_prompt`: bool | `loan/dashboard.html` | full-page | NO |
| 578 | POST | `create_params` | Account @ 583; LoanParams @ 593 | -- | -- (redirect) | `loan/setup.html` on error | -- | NO |
| 631 | POST | `update_params` | Account / LoanParams via `_load_loan_account` @ 636 | -- | -- (redirect) | -- | -- | NO |
| 682 | POST | `add_rate_change` | Account via `_load_loan_account` @ 687; RateHistory insert @ 700-705; LoanParams update @ 709; RateHistory list @ 732-736 | -- | `account`, `params`, `rate_history`: RateHistory list | `loan/_rate_history.html` | `#rate-history-list` partial | NO |
| 761 | POST | `add_escrow` | Account via helper @ 766; EscrowComponent name uniqueness @ 781-785; EscrowComponent active list @ 795-800 | `escrow_calculator.calculate_monthly_escrow` @ 803; `_compute_total_payment` @ 804 | `account`, `escrow_components`: list; `monthly_escrow`: `escrow_per_period`; `total_payment`: `monthly_payment` | `loan/_escrow_list.html` | `#escrow-list` partial | NO |
| 815 | POST | `delete_escrow` | Account via helper @ 823; EscrowComponent @ 827-829; LoanParams @ 843-847; EscrowComponent active list @ 835-840 | `escrow_calculator.calculate_monthly_escrow` @ 848; `_compute_total_payment` @ 849 | as `add_escrow` | `loan/_escrow_list.html` | `#escrow-list` partial | NO |
| 860 | POST | `payoff_calculate` | Account / LoanParams via helper @ 865 | `_load_loan_context` @ 882; `amortization_engine.calculate_summary` @ 900-... (extra_payment branch); `amortization_engine.generate_schedule` @ 917, 925, 937 (chart inputs); `amortization_engine.calculate_payoff_by_date` @ 1000 (target_date branch); `compute_contractual_pi` @ 1012 | `payoff_summary`: AmortizationSummary; `chart_labels`/`chart_original`/`chart_committed`/`chart_accelerated`: `chart_balance_series`; `committed_months_saved`: `months_saved`; `committed_interest_saved`: `interest_saved`; `required_extra`: Decimal (target_date branch) | `loan/_payoff_results.html` | `#payoff-results` partial | YES (anchor_bal at 893-895 sets ARM anchor inline; `extra = Decimal(str(data.get("extra_monthly", "0")))` at 899) |
| 1027 | POST | `refinance_calculate` | Account / LoanParams via helper @ 1047 | `_load_loan_context` @ 1063; `amortization_engine.get_loan_projection` @ 1069; `amortization_engine.calculate_monthly_payment` @ 1102 (refi monthly); `amortization_engine.generate_schedule` @ 1108 (refi schedule) | `account`, `params`, `comparison`: `{current_monthly: monthly_payment, current_total_interest: total_interest, current_payoff: payoff_date, current_remaining_months: loan_remaining_months, refi_monthly: monthly_payment, refi_total_interest: total_interest, refi_payoff: payoff_date, monthly_savings, interest_savings: interest_saved, break_even_months: months_saved}` | `loan/_refinance_results.html` | `#refinance-results` partial | YES (`refi_principal = current_real_principal + closing_costs` at 1095 when user does not override; `current_real_principal = proj.current_balance` at 1087) |
| 1170 | POST | `create_payment_transfer` | Account / LoanParams via helper @ 1184; Account @ 1201; EscrowComponent @ 1237-1239; RecurrenceRule + TransferTemplate insert; Transfer via service | `amortization_engine.calculate_remaining_months` @ 1222 (ARM branch); `amortization_engine.calculate_monthly_payment` @ 1225 (ARM) and @ 1231 (fixed); `escrow_calculator.calculate_total_payment` @ 1241; `transfer_recurrence.generate_for_template` @ 1280 | -- (redirect) | -- | -- | YES (`transfer_amount = data["amount"]` if provided else `transfer_amount = escrow_calculator.calculate_total_payment(monthly_pi, ...)` at 1241; `monthly_pi` derived inline from service calls at 1222-1235) |

Out-of-scope handlers in `loan.py`:

- `create_params` (578), `update_params` (631): loan-parameter setup
  forms; redirect-only response.

#### `app/routes/retirement.py` (410 lines)

| @route line | HTTP | View fn | DB queries (file:line) | Service calls (fn @ file:line) | Context vars (name: token) | Template | HTMX target | Inline compute? |
| ----------- | ---- | ------- | ---------------------- | ------------------------------ | -------------------------- | -------- | ----------- | --------------- |
| 46 | GET | `dashboard` | -- (service-loaded) | `retirement_dashboard_service.compute_gap_data` @ 51; `retirement_dashboard_service.compute_slider_defaults` @ 52 | `current_swr` (Decimal %), `current_return` (Decimal %); plus all keys from `compute_gap_data` (gap_analysis, chart_data, retirement_account_projections, paycheck_breakdown derivatives) | `retirement/dashboard.html` | full-page | NO |
| 301 | GET | `gap_analysis` | -- (service-loaded) | `retirement_dashboard_service.compute_gap_data` @ 320-324 | `gap_analysis`: dict (includes `paycheck_net`, `pension_benefit_monthly`, `savings_total`, `growth`, `goal_progress` keys); `chart_data`: dict; `retirement_account_projections`: list of `account_balance`/`projected_end_balance` rows | `retirement/_gap_analysis.html` | `#gap-analysis` (HTMX-only; redirects non-HX) | YES (`swr_override = Decimal(str(swr_param)) / Decimal("100")` @ 314; `return_rate_override` @ 318) |

Out-of-scope handlers in `retirement.py`:

- `pension_list` (65): list display of pension profiles; no money.
- `create_pension` (88), `edit_pension` (161), `update_pension` (183),
  `delete_pension` (282): pension CRUD; the pension profile stores
  parameters but the route does not compute / render a benefit value
  on the form pages.
- `update_settings` (338): settings POST then redirect.

#### `app/routes/investment.py` (804 lines)

| @route line | HTTP | View fn | DB queries (file:line) | Service calls (fn @ file:line) | Context vars (name: token) | Template | HTMX target | Inline compute? |
| ----------- | ---- | ------- | ---------------------- | ------------------------------ | -------------------------- | -------- | ----------- | --------------- |
| 63 | GET | `dashboard` | Account @ 68; InvestmentParams @ 72-75; Transaction @ 94-102; SalaryProfile @ 122-125; PaycheckDeduction join @ 134-143; Transaction (shadow income) @ 160-170; TransferTemplate @ 273-281 | `pay_period_service.get_all_periods` @ 78; `pay_period_service.get_current_period` @ 79; `get_baseline_scenario` @ 91; `balance_calculator.calculate_balances` @ 107-112; `calculate_investment_inputs` @ 173-181; `growth_engine.calculate_employer_contribution` @ 187-189; `build_contribution_timeline` @ 193-197; `growth_engine.project_balance` @ 209-218 | `account`, `params`, `current_balance`: `account_balance` @ 115; `periodic_contribution`: Decimal; `employer_contribution_per_period`: `employer_contribution`; `employer_params`: dict; `limit_info`: `{limit: contribution_limit_remaining, ytd: ytd_contributions, pct: int %}`; `projection`: list of ProjectionRow; `chart_labels`: `chart_date_labels`; `chart_balances`: `chart_balance_series`; `chart_contributions`: `chart_balance_series` (cumulative); `default_horizon`: int years; `show_contribution_prompt`, `is_deduction_path`: bool; `source_accounts`: list; `default_source_id`: int; `suggested_amount`: `transfer_amount_computed` | `investment/dashboard.html` | full-page | YES (`salary_gross_biweekly = (Decimal(...) / pp_per_year).quantize(...)` @ 128-131; `chart_balances`/`chart_contributions` loop @ 220-227 quantizes inline) |
| 363 | GET | `growth_chart` | Account @ 386; InvestmentParams @ 390-393; Transaction @ 415-422; SalaryProfile @ 449-452; PaycheckDeduction @ 460-469; Transaction (shadow income) @ 484-494 | `pay_period_service.get_all_periods` @ 408; `pay_period_service.get_current_period` @ 409; `get_baseline_scenario` @ 410; `balance_calculator.calculate_balances` @ 425-430; `growth_engine.generate_projection_periods` @ 436-439; `calculate_investment_inputs` @ 497-505; `build_contribution_timeline` @ 508-512; `growth_engine.project_balance` @ 514-523, @ 567 (what-if branch) | `chart_labels`: `chart_date_labels`; `chart_balances`/`chart_contributions`: `chart_balance_series`; `what_if_balances`: `chart_balance_series` (alt scenario); `what_if_amount`: Decimal; `comparison`: `{committed_end, whatif_end, difference, is_positive, is_zero}` (account_balance pair + Decimal diff) | `investment/_growth_chart.html` | `#growth-chart` (HTMX-only; redirects non-HX) | YES (quantize loop @ 531-538; what-if balance comparison Decimal arithmetic) |
| 609 | POST | `create_contribution_transfer` | Account @ 625; Account @ 641; InvestmentParams @ 662-665; RecurrenceRule + TransferTemplate insert; Transfer via service | `pay_period_service.get_all_periods` @ 712; `get_baseline_scenario` @ 710; `transfer_recurrence.generate_for_template` @ 713 | -- (redirect) | -- | -- | YES (`transfer_amount` derived inline from `params.annual_contribution_limit / 26` or `Decimal("500.00")` fallback at 668-670; this is `transfer_amount_computed`) |

Out-of-scope handlers in `investment.py`:

- `update_params` (734): parameter form POST then redirect.

#### `app/routes/debt_strategy.py` (515 lines)

| @route line | HTTP | View fn | DB queries (file:line) | Service calls (fn @ file:line) | Context vars (name: token) | Template | HTMX target | Inline compute? |
| ----------- | ---- | ------- | ---------------------- | ------------------------------ | -------------------------- | -------- | ----------- | --------------- |
| 275 | GET | `dashboard` | Account+AccountType join via `_load_debt_accounts`; LoanParams via helper | `_load_debt_accounts` @ 286 (internally calls `amortization_engine` to derive each debt's current principal and minimum payment) | `debt_accounts`: list of `DebtAccount` namedtuples (each with `current_principal` -> `loan_principal_real`, `minimum_payment` -> `monthly_payment`); `has_arm`: bool | `debt_strategy/dashboard.html` | full-page | NO |
| 295 | POST | `calculate` | Account+AccountType join via `_load_debt_accounts` @ 343; LoanParams via helper | `_load_debt_accounts` @ 343; `calculate_strategy` @ 362, 366, 370, 384 (baseline / avalanche / snowball / custom); `_build_comparison` @ 396; `_prepare_chart_data` @ 407 | `debt_accounts`: list; `comparison`: `{baseline/avalanche/snowball/custom: {debt_free_date: payoff_date, total_interest, total_paid, total_months, interest_saved, months_saved}}`; `avalanche_result`/`snowball_result`/`baseline_result`/`custom_result`: StrategyResult; `selected_result`: StrategyResult; `selected_strategy`: str; `extra_monthly`: Decimal; `chart_data_json`: JSON-serialized `chart_balance_series` per loan | `debt_strategy/_results.html` | `#debt-strategy-results` partial | NO |

### Group C: aggregation and analytics view routes

Six files, 3,655 LOC. One large file: `salary.py` (1466).

#### `app/routes/dashboard.py` (209 lines)

| @route line | HTTP | View fn | DB queries (file:line) | Service calls (fn @ file:line) | Context vars (name: token) | Template | HTMX target | Inline compute? |
| ----------- | ---- | ------- | ---------------------- | ------------------------------ | -------------------------- | -------- | ----------- | --------------- |
| 40 + 41 | GET | `page` | -- (service-loaded) | `dashboard_service.compute_dashboard_data` @ 50 | `**data` unpacked: `current_period` (PayPeriod), `balance_info` (`checking_balance`/`account_balance`), `balance_runway_days` (`cash_runway_days`), `upcoming_bills` (list with `effective_amount`), `alerts` (list), `savings_goals` (list with `goal_progress`), `debt_obligations` (`monthly_payment` + `dti_ratio` summary), `spending_comparison` (`period_subtotal` deltas) | `dashboard/dashboard.html` | full-page | NO |
| 54 | POST | `mark_paid` | Transaction via `_get_accessible_transaction`; Status @ 95-128 (via service or direct) | `transfer_service.update_transfer` @ 104 (shadow branch); direct Transaction mutation @ 125-128 (non-shadow branch); helper `txn_to_bill_dict` (via `dashboard_service`) | rendered bill-row partial (effective_amount + remaining + over_budget per `_entry_progress_fields`) | `dashboard/_bill_row.html` | `#bill-row-{id}` (HTMX-only) | NO |
| 141 | GET | `bills_section` | -- | `dashboard_service.compute_dashboard_data` @ 152 | `upcoming_bills`: list with `effective_amount`; `current_period`: PayPeriod | `dashboard/_upcoming_bills.html` | bills section target | NO |
| 160 | GET | `balance_section` | -- | `dashboard_service.compute_dashboard_data` @ 171 | `balance_info`: `account_balance` object | `dashboard/_balance_runway.html` | balance section target | NO |

#### `app/routes/savings.py` (288 lines)

| @route line | HTTP | View fn | DB queries (file:line) | Service calls (fn @ file:line) | Context vars (name: token) | Template | HTMX target | Inline compute? |
| ----------- | ---- | ------- | ---------------------- | ------------------------------ | -------------------------- | -------- | ----------- | --------------- |
| 107 | GET | `dashboard` | -- (service-loaded) | `savings_dashboard_service.compute_dashboard_data` @ 112 | `**ctx` unpacked: accounts, goals, emergency_fund_metrics, `savings_total`, `debt_total`, `dti_ratio`, per-account `projected_end_balance` lists | `savings/dashboard.html` | full-page | NO |

Out-of-scope handlers in `savings.py`:

- `new_goal` (116), `create_goal` (124), `edit_goal` (162),
  `update_goal` (176), `delete_goal` (256): savings-goal CRUD; form
  display or redirect-only POST.

#### `app/routes/analytics.py` (563 lines)

| @route line | HTTP | View fn | DB queries (file:line) | Service calls (fn @ file:line) | Context vars (name: token) | Template | HTMX target | Inline compute? |
| ----------- | ---- | ------- | ---------------------- | ------------------------------ | -------------------------- | -------- | ----------- | --------------- |
| 94 | GET | `page` | -- | -- | -- | `analytics/analytics.html` | full-page | NO |
| 107 | GET | `calendar_tab` | UserSettings @ 140-142 | `calendar_service.get_month_detail` @ 155-158 OR `calendar_service.get_year_overview` @ 148-151; `csv_export_service.export_calendar_csv` @ 152, 159; `_render_month_view` @ 168 / `_render_year_view` @ 167; `_validate_owned_or_abort` @ 135 | `data`: month_detail/year_overview object with `day_entries` (effective_amount) and paycheck_days | `analytics/_calendar_month.html` OR `analytics/_calendar_year.html` | calendar tab target (HTMX-only; CSV download non-HTMX) | YES (`_build_calendar_weeks` helper at 380-428 computes `income_total` and `expense_total` per day inline at 413-417) |
| 171 | GET | `year_end_tab` | -- | `year_end_summary_service.compute_year_end_summary` @ 185-187; `csv_export_service.export_year_end_csv` @ 190; `_get_available_years` @ 196 | `data`: year_end summary (year_summary_jan1_balance + year_summary_dec31_balance + year_summary_principal_paid + year_summary_growth + year_summary_employer_total + spending_by_category + transfer_amount per destination); `year`, `available_years`: int / list | `analytics/_year_end.html` | year-end tab target | NO |
| 205 | GET | `variance_tab` | PayPeriod via service @ 256 | `budget_variance_service.compute_variance` @ 239; `csv_export_service.export_variance_csv` @ 249; `pay_period_service.get_all_periods` @ 256; `_build_variance_chart_data` @ 255 | `report`: VarianceReport (effective_amount + period_subtotal); `chart_data`: dict; `window_type`, `period_id`, `month`, `year` | `analytics/_variance.html` | variance tab target | NO |
| 272 | GET | `trends_tab` | UserSettings @ 281-283 | `spending_trend_service.compute_trends` @ 289; `csv_export_service.export_trends_csv` @ 296 | `report`: TrendReport (growth + period_subtotal series) | `analytics/_trends.html` | trends tab target | NO |

#### `app/routes/obligations.py` (423 lines)

| @route line | HTTP | View fn | DB queries (file:line) | Service calls (fn @ file:line) | Context vars (name: token) | Template | HTMX target | Inline compute? |
| ----------- | ---- | ------- | ---------------------- | ------------------------------ | -------------------------- | -------- | ----------- | --------------- |
| 259 | GET | `summary` | TransactionTemplate @ 275-290 (expense), @ 293-307 (income); TransferTemplate @ 310-324; PayPeriod via service indirectly | `amount_to_monthly` (from `savings_goal_service`) @ 338, 361, 383; `_next_occurrence` helper @ 349, 371, 394 | `expense_items`/`income_items`/`transfer_items`: lists of dicts with `monthly` (monthly equivalent of recurring `effective_amount`), `next_date`; `total_expense_monthly`, `total_income_monthly`, `total_transfer_monthly`, `total_outflows`, `net_cash_flow`: Decimal aggregates (`period_subtotal` flavor) | `obligations/summary.html` | full-page | YES (per-template loops at 331-395 sum monthly equivalents; quantize at 398-406; `net_cash_flow = total_income_monthly - total_outflows` at 408) |

#### `app/routes/salary.py` (1466 lines)

| @route line | HTTP | View fn | DB queries (file:line) | Service calls (fn @ file:line) | Context vars (name: token) | Template | HTMX target | Inline compute? |
| ----------- | ---- | ------- | ---------------------- | ------------------------------ | -------------------------- | -------- | ----------- | --------------- |
| 102 | GET | `list_profiles` | SalaryProfile @ 107-112 | `pay_period_service.get_all_periods` @ 115; `pay_period_service.get_current_period` @ 116; `load_tax_configs` @ 121 (per profile); `paycheck_calculator.calculate_paycheck` @ 122-125 (per profile, with `calibration=profile.calibration`) | `profile_data`: list of `{profile, net_pay}` where `net_pay` is `paycheck_net` (Decimal from breakdown) | `salary/list.html` | full-page | NO |
| 149 | POST | `create_profile` | Category @ 172-176; Account @ 191-195; SalaryProfile insert @ 230-244 | `get_baseline_scenario` @ 162; `recurrence_engine.generate_for_template` @ 250; `pay_period_service.get_current_period` @ 256; `pay_period_service.get_all_periods` @ 249; `load_tax_configs` @ 260; `paycheck_calculator.calculate_paycheck` @ 261-263 (sets `template.default_amount = init_breakdown.net_pay` at 264) | -- (redirect) | -- on success | -- | YES (`data["annual_salary"] / pay_periods_per_year` at 223 inline computes `template.default_amount`) |
| 284 | GET | `edit_profile` | SalaryProfile via `get_or_404` @ 289; FilingStatus / RaiseType / DeductionTiming / CalcMethod @ 293-296 | `_get_investment_accounts` @ 297 | `profile`, `filing_statuses`, `raise_types`, `deduction_timings`, `calc_methods`, `investment_accounts` | `salary/form.html` | full-page | NO |
| 311 | POST | `update_profile` | SalaryProfile via `get_or_404` @ 325 | `_regenerate_salary_transactions` @ 369 (which internally calls `pay_period_service.get_all_periods`, `load_tax_configs`, `paycheck_calculator.calculate_paycheck`) | -- (redirect) | -- | -- | NO |
| 438 | POST | `add_raise` | SalaryProfile via helper @ 443; SalaryRaise insert @ 461 | `_regenerate_salary_transactions` @ 465 | partial section render via `_render_raises_partial` (HTMX), else redirect | `salary/_raises_section.html` (partial) | raises section target | YES (`data["percentage"] = D(...)/D("100")` at 459 converts form input) |
| 518 | POST | `delete_raise` | SalaryRaise via `get_owned_via_parent` @ 529-531 | `_regenerate_salary_transactions` @ 539 | partial section render or redirect | `salary/_raises_section.html` | raises section target | NO |
| 571 | POST | `update_raise` | SalaryRaise via `get_owned_via_parent` @ 583-585 | `_regenerate_salary_transactions` @ 628 | partial section render or redirect | `salary/_raises_section.html` | raises section target | NO |
| 696 | POST | `add_deduction` | SalaryProfile via helper @ 701; PaycheckDeduction insert @ 720 | `_regenerate_salary_transactions` @ 724 | partial section render or redirect | `salary/_deductions_section.html` | deductions section target | YES (`data["amount"] = D(.../D("100"))` at 716 percentage conversion) |
| 780 | POST | `delete_deduction` | PaycheckDeduction via `get_owned_via_parent` @ 791-793 | `_regenerate_salary_transactions` @ 801 | partial section render or redirect | `salary/_deductions_section.html` | deductions section target | NO |
| 833 | POST | `update_deduction` | PaycheckDeduction via `get_owned_via_parent` @ 845-847 | `_regenerate_salary_transactions` @ 893 | partial section render or redirect | `salary/_deductions_section.html` | deductions section target | NO |
| 960 | GET | `breakdown` | SalaryProfile via `get_or_404` @ 965; PayPeriod via `get_or_404` @ 969 | `pay_period_service.get_all_periods` @ 973; `load_tax_configs` @ 974; `paycheck_calculator.calculate_paycheck` @ 975-978 (with `calibration=profile.calibration`) | `profile`, `period`, `breakdown`: `paycheck_breakdown` (PaycheckBreakdown dataclass: paycheck_gross/paycheck_net/federal_tax/state_tax/fica/pre_tax_deduction/post_tax_deduction/employer_contribution); `periods`: list | `salary/breakdown.html` | full-page | NO |
| 1020 | GET | `projection` | SalaryProfile via `get_or_404` @ 1025 | `pay_period_service.get_all_periods` @ 1029; `load_tax_configs` @ 1030; `paycheck_calculator.project_salary` @ 1031-1034 | `profile`, `projection_data`: list of `(period, paycheck_breakdown)` tuples @ 1037 (zipped inline) | `salary/projection.html` | full-page | YES (inline `zip(periods, breakdowns)` at 1037) |
| 1064 | POST | `calibrate_preview` | SalaryProfile via `get_or_404` @ 1069 | `pay_period_service.get_all_periods` @ 1083; `pay_period_service.get_current_period` @ 1084; `load_tax_configs` @ 1087; `paycheck_calculator.calculate_paycheck` @ 1088-1090; `calibration_service.derive_effective_rates` @ 1105-1112 | `data`: form inputs; `rates`: DerivedRates; `taxable_income`: Decimal (computed inline `taxable = gross - total_pre_tax` at 1095); `total_pre_tax`: Decimal (`pre_tax_deduction` sum from breakdown @ 1091) | `salary/calibrate_confirm.html` | full-page | YES (`taxable = gross - total_pre_tax` at 1095) |
| 1127 | POST | `calibrate_confirm` | SalaryProfile via `get_or_404` @ 1132; CalibrationOverride @ 1145-1149; CalibrationOverride insert @ 1154-1169 | `_regenerate_salary_transactions` @ 1175 | -- (redirect) | -- | -- | NO |
| 1196 | POST | `calibrate_delete` | SalaryProfile via `get_or_404` @ 1201; CalibrationOverride @ 1205-1209 | `_regenerate_salary_transactions` @ 1218 | -- (redirect) | -- | -- | NO |
| 1251 | POST | `update_tax_config` | StateTaxConfig @ 1275-1278; StateTaxConfig insert @ 1289-1297 | `_regenerate_all_salary_transactions` @ 1303 | -- (redirect) | -- | -- | YES (`flat_rate = D(str(data["flat_rate"])) / D("100")` at 1271 percentage-to-decimal conversion) |
| 1310 | POST | `update_fica_config` | FicaConfig @ 1330-1333; FicaConfig insert @ 1341 | `_regenerate_all_salary_transactions` @ 1348 | -- (redirect) | -- | -- | YES (similar `D(...) / D("100")` conversions in the FICA percentage handling) |

Out-of-scope handlers in `salary.py`:

- `new_profile` (132), `tax_config` (1243): form display and redirect
  stub respectively.
- `breakdown_current` (989): wrapper that redirects to `breakdown` with
  the current period_id substituted.
- `delete_profile` (398): redirect-only POST.
- `calibrate_form` (1049): form display only.

#### `app/routes/templates.py` (706 lines)

| @route line | HTTP | View fn | DB queries (file:line) | Service calls (fn @ file:line) | Context vars (name: token) | Template | HTMX target | Inline compute? |
| ----------- | ---- | ------- | ---------------------- | ------------------------------ | -------------------------- | -------- | ----------- | --------------- |
| 105 | GET | `list_templates` | TransactionTemplate @ 114-119 | -- | `active_templates`, `archived_templates`: lists (default_amount field is `effective_amount` template) | `templates/list.html` | full-page | NO |
| 129 | GET | `new_template` | Category @ 134-138; Account @ 140-143; RecurrencePattern @ 145; TransactionType @ 146 | `pay_period_service.get_all_periods` @ 147; `pay_period_service.get_current_period` @ 148 | categories, accounts, patterns, txn_types, periods, current_period | `templates/form.html` | full-page | NO |
| 163 | POST | `create_template` | Account @ 176; Category @ 180; PayPeriod @ 208; RecurrencePattern @ 198 | `get_baseline_scenario` @ 243; `pay_period_service.get_all_periods` @ 245; `recurrence_engine.generate_for_template` @ 246-248 | -- (redirect) | -- | -- | NO |
| 255 | GET | `edit_template` | TransactionTemplate @ 260; Category @ 264-268; Account @ 270-273; RecurrencePattern @ 275; TransactionType @ 276 | -- | template + dropdowns + periods | `templates/form.html` | full-page | NO |
| 290 | POST | `update_template` | TransactionTemplate @ 306; Account @ 375; Category @ 380; Transaction bulk update @ 411-414; RecurrencePattern @ 343 | `get_baseline_scenario` @ 417; `pay_period_service.get_all_periods` @ 419; `recurrence_engine.regenerate_for_template` @ 421-423 | -- (redirect) | -- | -- | NO |
| 456 | POST | `archive_template` | TransactionTemplate @ 468; Transaction soft-delete @ 476-480 | -- | -- (redirect) | -- | -- | NO |
| 504 | POST | `unarchive_template` | TransactionTemplate @ 512; Transaction restore @ 520-524 | `get_baseline_scenario` @ 528; `pay_period_service.get_all_periods` @ 530; `recurrence_engine.generate_for_template` @ 531-533 | -- (redirect) | -- | -- | NO |
| 557 | POST | `hard_delete_template` | TransactionTemplate @ 577; Transaction @ 591-595, 616-618; TransactionTemplate delete @ 620 | `archive_helpers.template_has_paid_history` @ 581 | -- (redirect) | -- | -- | NO |
| 640 | GET | `preview_recurrence` | RecurrencePattern @ 657; PayPeriod @ 679 | `pay_period_service.get_all_periods` @ 672; `pay_period_service.get_current_period` @ 689; `recurrence_engine._match_periods` @ 692 | `preview_periods`: PayPeriod list (first 5 matches at 693) | -- (HTML fragment via Markup) | -- (HTMX text/html) | NO |

`templates.py` is mostly redirect-driven (CRUD), but `create_template`,
`update_template`, `unarchive_template`, and `hard_delete_template` all
trigger recurrence engine generation that produces financial figures
downstream; they are recorded in scope because the recurrence engine
output is what populates the next grid view's `effective_amount`.
Their direct response is a redirect, which is why the "Context vars"
cell is empty for those rows -- their financial output is materialised
elsewhere.

### 1.2.x Cross-page consistency markers

Phase 1 only enumerates; Phase 3 will adjudicate. Each marker below is
a controlled-vocabulary token that two or more route handlers in
different files produce values for, so the audit knows which routes to
compare for the same `(user_id, period_id, scenario_id)` triple.

| Concept token | Producers | Where rendered |
| ------------- | --------- | -------------- |
| `checking_balance` / `account_balance` | `grid.index` @ 243; `accounts.checking_detail` @ 1425; `accounts.interest_detail` @ 1291; `dashboard.page` (via `dashboard_service.compute_dashboard_data`); `savings.dashboard` (via `savings_dashboard_service.compute_dashboard_data`); `investment.dashboard` @ 107; `investment.growth_chart` @ 425 | `/grid`, `/accounts/<id>/checking`, `/accounts/<id>/interest`, `/dashboard`, `/savings`, `/accounts/<id>/investment` |
| `projected_end_balance` | `grid.index` (subtotals + balances); `accounts.checking_detail` (3/6/12-month horizons); `accounts.interest_detail` (3/6/12-month horizons); `savings.dashboard` (per-account projections); `investment.dashboard` (multi-horizon chart) | as above plus `/savings` |
| `monthly_payment` | `loan.dashboard` @ 429-435; `loan.payoff_calculate` @ 900-...; `loan.refinance_calculate` @ 1102; `loan.create_payment_transfer` @ 1225, 1231; `debt_strategy.dashboard` (per-account minimum_payment); `debt_strategy.calculate` (strategy results); `obligations.summary` (monthly-equivalent normalization) | `/accounts/<id>/loan`, `/debt-strategy`, `/obligations` |
| `loan_principal_real` / `loan_principal_stored` | `loan.dashboard` (via `proj.current_balance`); `loan.refinance_calculate` @ 1087 (`current_real_principal = proj.current_balance`); `debt_strategy.dashboard` (per-account `current_principal`); A-04 split | `/accounts/<id>/loan`, `/debt-strategy` |
| `dti_ratio` | `dashboard.page` (via `dashboard_service`); `savings.dashboard` (via `savings_dashboard_service`) | `/dashboard`, `/savings` |
| `paycheck_breakdown` (paycheck_gross/net/federal_tax/state_tax/fica/deduction split) | `salary.list_profiles` @ 122; `salary.breakdown` @ 975; `salary.projection` @ 1031; `salary.calibrate_preview` @ 1088; `retirement.dashboard` (via `compute_gap_data`); `retirement.gap_analysis` (via `compute_gap_data`) | `/salary*`, `/retirement*` |
| `transfer_amount` (stored) vs `transfer_amount_computed` (route default) | stored: every `transfer_service.update_transfer` flow above; computed: `loan.create_payment_transfer` @ 1213-1241; `investment.create_contribution_transfer` @ 668-670 | `/accounts/<id>/loan` (transfer prompt), `/accounts/<id>/investment` (contribution prompt) |
| `effective_amount` | every cell-render route (`transactions.get_cell`, `transfers.get_cell`, `transactions.mark_done`, `transfers.mark_done`, etc.); rendered by every grid/dashboard/savings/analytics partial that displays a transaction amount | `/grid`, `/dashboard`, `/savings`, `/analytics`, `/companion` |
| `entry_sum_total` / `entry_remaining` | `transactions.get_cell` @ 88; `entries.list_entries` (and every entry route via `_render_entry_list`); `dashboard._entry_progress_fields` (via service); `companion.index` (via `_build_entry_data`) | `/grid` entry popovers; `/dashboard` bills; `/companion` |
| `goal_progress` | `savings.dashboard`; `retirement.gap_analysis`; `companion._build_entry_data` (`pct = total / estimated_amount * 100`) | `/savings`, `/retirement`, `/companion` |
| `chart_balance_series` / `chart_date_labels` | `loan.dashboard` (original/committed/floor); `loan.payoff_calculate` (accelerated overlay); `investment.dashboard`; `investment.growth_chart` (what-if overlay); `debt_strategy.calculate` (per-loan timelines) | `/accounts/<id>/loan`, `/accounts/<id>/investment`, `/debt-strategy` |
| `year_summary_*` (jan1_balance, dec31_balance, principal_paid, growth, employer_total) | `analytics.year_end_tab` (via `year_end_summary_service.compute_year_end_summary`) | `/analytics/year-end` |

### 1.2.y Ambiguities raised by Phase 1.2

Subagents flagged these for the developer; each maps to a `Q-NN` in
`09_open_questions.md`.

- Q-10 (P1-c, 2026-05-15): `grid.index` computes period subtotals
  (income, expense, net) inline at `grid.py:263-279` using
  `txn.effective_amount` directly. Is route-layer subtotal computation
  by design, or should the subtotal aggregation move into a service
  function so the grid and any other consumer (dashboard's
  "spending comparison" section) share a single computation path?
  Phase 6 SRP review is contingent on the answer; Phase 3 consistency
  audit needs the answer before comparing `grid.index` subtotals
  against `dashboard_service._compute_spending_comparison`.

- Q-11 (P1-c, 2026-05-15): `loan.refinance_calculate` reads
  `current_real_principal = proj.current_balance` at line 1087, then
  optionally overrides with `refi_principal = current_real_principal +
  closing_costs` at line 1095. `proj.current_balance` comes from
  `amortization_engine.get_loan_projection` (A-04 dual policy: ARM uses
  stored `current_principal`, fixed-rate uses engine-walked balance).
  Is the refinance flow expected to use the ARM-stored value for ARM
  loans (matching A-04) even though the user's "current principal" for
  refinance purposes might be the engine-walked value? Phase 3 must
  compare the two flavors for the same loan on the same date.

- Q-12 (P1-c, 2026-05-15): `obligations.summary` builds monthly
  equivalents for recurring templates inline at
  `obligations.py:331-395` by calling `amount_to_monthly` per template
  in a loop, then aggregates totals with Decimal arithmetic at
  lines 398-408. The `amount_to_monthly` helper lives in
  `savings_goal_service`; the aggregation is route-level. Should the
  aggregation move into a dedicated service function so the
  obligations summary, the dashboard cash-runway estimate, and the
  debt-summary DTI denominator all consume the same monthly aggregator?
  (Phase 6 SRP candidate.)

- Q-13 (P1-c, 2026-05-15): `salary.calibrate_preview` computes
  `taxable = gross - total_pre_tax` inline at `salary.py:1095`. The
  same identity is computed inside `paycheck_calculator.calculate_paycheck`
  (the breakdown's `taxable_income` field), but the route performs its
  own subtraction. Should the route read `bk.taxable_income` directly
  rather than recomputing? If the route value diverges from the
  breakdown's, which is canonical?

- Q-14 (P1-c, 2026-05-15): `dashboard.mark_paid` (`dashboard.py:54-139`)
  was marked out-of-scope by the Group C return because "it updates
  status/amount in DB and returns a partial row." The response IS an
  updated bill-row partial that re-renders `effective_amount`,
  `entry_remaining`, and the `goal_progress` percent. Per the
  audit-plan scope rule ("in scope only if its response renders
  financial figures"), this handler IS in scope. Should Phase 3 treat
  `mark_paid` as the dashboard's path-equivalent of
  `transactions.mark_done` and verify that the two endpoints produce
  the same effective amount and entry-remaining values for the same
  transaction?

### 1.2.z Notes for Phase 3

- Every cell-render path (`get_cell`, `update_transaction`, `mark_done`,
  `mark_credit`, `cancel_transaction`, plus the transfer-side mirror)
  renders `grid/_transaction_cell.html`, which reads `txn.effective_amount`
  directly. Phase 3 must verify that the four mutation paths
  (`update_transaction`, `mark_done`, `mark_credit`, `cancel_transaction`)
  leave `actual_amount` in a state where `effective_amount` returns the
  intended user-facing value -- specifically, that envelope branch's
  `transaction_service.settle_from_entries` at `transactions.py:596`
  matches the manual-update branch's behavior.
- Every dashboard / savings / accounts / loan detail route reads its
  scenario through `get_baseline_scenario`. Phase 3 should verify that
  no route uses a different scenario resolution (a scenario from form
  input, a hard-coded scenario_id, etc.) for the same user.
- Every loan / investment / interest detail page reads
  `account.current_anchor_balance` either directly or via the balance
  calculator. Phase 3 must verify that the value read on
  `/accounts/<id>/checking` (`accounts.py:1418`) equals the value read
  on `/accounts/<id>/interest` (`accounts.py:1283`) for the same
  account; A-04's dual policy for ARM means the loan detail page reads
  through `LoanProjection.current_balance` instead, and Phase 3 must
  confirm these are not silently inconsistent.

## 1.3 Template layer

P1-d will fill in.

## 1.4 Static / JavaScript layer

P1-d will fill in.

## 1.7 Inventory deliverable wrap-up

P1-e will fill in.
