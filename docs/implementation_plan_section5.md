# Implementation Plan -- Section 5: Debt and Account Improvements

**Version:** 1.0
**Date:** March 28, 2026
**Parent Documents:** project_roadmap_v4-1.md, project_requirements_v3_addendum.md
**Scope:** Tasks 5.1, 5.4, 5.5 from roadmap v4.1, plus prerequisite student/personal loan
infrastructure and category prerequisite verification.

---

## 1. Prerequisites

| Prerequisite | Status | Notes |
| --- | --- | --- |
| Section 3: Critical Bug Fixes | Complete | All 10 items resolved March 2026 |
| Section 3A: Transfer Architecture Rework | Complete | Shadow transactions, transfer service, five invariants |
| Section 4: UX/Grid Overhaul | Complete | ID-based lookups, account parameter UX, emergency fund fix |
| Task 4.4c: Ref table ID-based lookups | Complete | `AcctTypeEnum`, `AcctCategoryEnum`, `RecurrencePatternEnum` all cached |
| `ref.account_types.category_id` FK | Complete | FK to `ref.account_type_categories` (Asset/Liability/Retirement/Investment) |
| `ref.account_types.has_amortization` | Complete | Boolean flag on all debt types (Mortgage, Auto Loan, Student Loan, Personal Loan, HELOC) |
| `ref.account_types.has_parameters` | Complete | Boolean flag on all parameterized types |

---

## 2. Scope

**In scope:**

- **Prerequisite 5.0:** Student loan and personal loan infrastructure (models, routes,
  templates, schemas) -- these account types are seeded with `has_parameters=True` and
  `has_amortization=True` but have no implementation. Tasks 5.1 and 5.5 require all four
  debt types to have parity.
- **Task 5.1:** Debt account payment linkage -- recurring transfer creation prompt after debt
  account parameter setup, and amortization engine enhancement to accept payment history.
- **Task 5.4:** Income-relative savings goals -- new goal mode that calculates target amount
  from net pay.
- **Task 5.5:** Payoff calculator for all debt accounts -- extend the mortgage payoff
  calculator UI to auto loan, student loan, and personal loan.

**Out of scope:** Scenarios (Phase 7), smart features (Phase 9), notifications (Phase 10).

**Removed tasks:** 5.2 (recurrence audit -- confirmed unnecessary in 3.2), 5.3 (actual
paycheck entry -- superseded by calibration feature in 3.10).

---

## 3. Current State Inventory

This inventory is verified against the current codebase as of March 28, 2026. All file paths
and function signatures are confirmed by reading the actual code.

### 3.1 Debt Account Types and Parameter Tables

| Account Type | Enum Member | Seeded | Model File | Params Model | Route File | Templates |
| --- | --- | --- | --- | --- | --- | --- |
| Mortgage | `AcctTypeEnum.MORTGAGE` | Yes | `app/models/mortgage_params.py` | `MortgageParams` | `app/routes/mortgage.py` | `mortgage/dashboard.html`, `mortgage/setup.html`, `mortgage/_payoff_results.html`, `mortgage/_escrow_list.html`, `mortgage/_rate_history.html` |
| Auto Loan | `AcctTypeEnum.AUTO_LOAN` | Yes | `app/models/auto_loan_params.py` | `AutoLoanParams` | `app/routes/auto_loan.py` | `auto_loan/dashboard.html`, `auto_loan/setup.html` |
| Student Loan | `AcctTypeEnum.STUDENT_LOAN` | Yes | **NONE** | **NONE** | **NONE** | **NONE** |
| Personal Loan | `AcctTypeEnum.PERSONAL_LOAN` | Yes | **NONE** | **NONE** | **NONE** | **NONE** |

**Mortgage parameter columns:** `account_id` (FK unique), `original_principal` (Numeric 12,2),
`current_principal` (Numeric 12,2), `interest_rate` (Numeric 7,5), `term_months` (int),
`origination_date` (Date), `payment_day` (int 1-31), `is_arm` (bool),
`arm_first_adjustment_months` (int), `arm_adjustment_interval_months` (int), timestamps.

**Auto loan parameter columns:** `account_id` (FK unique), `original_principal` (Numeric 12,2),
`current_principal` (Numeric 12,2), `interest_rate` (Numeric 7,5), `term_months` (int),
`origination_date` (Date), `payment_day` (int 1-31), timestamps.

**Student loan and personal loan:** Seeded as account types in `ref.account_types` with
`has_parameters=True` and `has_amortization=True`. No model, route, template, or schema
exists. The account creation route (`app/routes/accounts.py:178-186`) logs a warning and
redirects to the accounts list when these types are created.

### 3.2 Amortization Engine API

**File:** `app/services/amortization_engine.py`

**Public functions:**

| Function | Inputs | Output | Notes |
| --- | --- | --- | --- |
| `calculate_remaining_months(origination_date, term_months, as_of=None)` | Date, int, optional Date | `int` (>= 0) | Months elapsed subtracted from term |
| `calculate_monthly_payment(principal, annual_rate, remaining_months)` | Decimal, Decimal, int | `Decimal` | Standard amortization formula; handles zero rate, zero/negative principal |
| `generate_schedule(current_principal, annual_rate, remaining_months, extra_monthly=0, origination_date=None, payment_day=1)` | Decimals, int, optional Decimal/Date/int | `list[AmortizationRow]` | Static extra_monthly only; no payment history input |
| `calculate_summary(current_principal, annual_rate, remaining_months, origination_date, payment_day, term_months, extra_monthly=0)` | Decimals, ints, Date | `AmortizationSummary` | Generates standard + accelerated schedules, computes savings |
| `calculate_payoff_by_date(current_principal, annual_rate, remaining_months, target_date, origination_date, payment_day)` | Decimals, int, Dates | `Decimal \| None` | Binary search for required extra_monthly; None if unachievable |

**Critical observation:** The amortization engine accepts ONLY static parameters. It does NOT
accept payment history. Extra payments are a uniform static amount per month, not actual
payment records. This is the primary gap for task 5.1.

**Dataclasses:**

```python
@dataclass
class AmortizationRow:
    month: int
    payment_date: date
    payment: Decimal
    principal: Decimal
    interest: Decimal
    extra_payment: Decimal
    remaining_balance: Decimal

@dataclass
class AmortizationSummary:
    monthly_payment: Decimal
    total_interest: Decimal
    payoff_date: date
    total_interest_with_extra: Decimal
    payoff_date_with_extra: date
    months_saved: int
    interest_saved: Decimal
```

### 3.3 Balance Calculator -- Debt Account Handling

**File:** `app/services/balance_calculator.py`

**Function:** `calculate_balances_with_amortization(anchor_balance, anchor_period_id, periods,
transactions, account_id=None, loan_params=None)` (line 175)

**Current behavior:**

1. Calls `calculate_balances()` first to get base balances.
2. Detects payments: finds shadow INCOME transactions (`transfer_id IS NOT NULL`,
   `txn.is_income == True`) -- these represent money coming INTO the debt account (loan
   payments from checking).
3. Splits each payment into interest and principal portions using the loan's rate:
   - `interest_portion = running_principal * monthly_rate`
   - `principal_portion = total_payment_in - interest_portion`
   - Clamped to `[0, running_principal]`
4. Tracks `running_principal` which decreases as payments are applied.

**Key design point:** The balance calculator ALREADY incorporates actual transfer history for
debt account balance projection. The gap is that the **amortization engine** (used for the
dashboard summary, payoff calculator, and chart) does NOT -- it only uses static parameters.

### 3.4 Mortgage Payoff Calculator

**Route:** `POST /accounts/<id>/mortgage/payoff` in `app/routes/mortgage.py:378-472`

**Service calls:**
- Extra payment mode: `amortization_engine.calculate_summary()` with `extra_monthly` param,
  plus `generate_schedule()` twice (standard + accelerated) for chart data.
- Target date mode: `amortization_engine.calculate_payoff_by_date()` plus
  `calculate_monthly_payment()` for display.

**Template:** `mortgage/_payoff_results.html` -- HTMX fragment returned by POST.
- Extra payment mode: Shows new payoff date, months saved, interest saved, and a Chart.js
  comparison chart (standard vs. accelerated balance curves).
- Target date mode: Shows required extra monthly payment and new total monthly, or error
  messages for unachievable/unnecessary dates.

**HTMX pattern:** Form in mortgage dashboard's "Payoff Calculator" tab uses
`hx-post` with `hx-target="#payoff-results"` and `hx-swap="innerHTML"`. The extra payment
input has a slider with 250ms debounce that triggers auto-submit via a custom
`slider-changed` event.

**Schema:** `PayoffCalculatorSchema` in `app/schemas/validation.py:605-614` -- validates
`mode` (required, one of `extra_payment`/`target_date`), `extra_monthly` (optional Decimal),
`target_date` (optional Date).

### 3.5 Auto Loan Dashboard -- No Payoff Calculator

**Route:** `GET /accounts/<id>/auto-loan` in `app/routes/auto_loan.py:61-102`

Renders `auto_loan/dashboard.html` with summary metrics and balance chart. No payoff
calculator tab, no payoff calculation route. Uses the same amortization engine functions
as mortgage for summary and chart.

### 3.6 Student Loan and Personal Loan -- No Implementation

No models, routes, templates, or schemas exist. The account types are seeded in
`ref.account_types` with correct `category_id` (Liability), `has_parameters=True`, and
`has_amortization=True`. Creating an account of these types logs a warning and redirects
to the accounts list.

### 3.7 Savings Goal Model

**File:** `app/models/savings_goal.py`

**Current columns:**
- `id` (PK), `user_id` (FK), `account_id` (FK), `name` (String 100),
  `target_amount` (Numeric 12,2), `target_date` (Date, nullable),
  `contribution_per_period` (Numeric 12,2, nullable), `is_active` (Boolean),
  `created_at`, `updated_at`

**Missing for task 5.4:** `goal_mode`, `income_unit`, `income_multiplier` columns do not
exist. The model is fixed-amount only.

**Constraints:** `target_amount > 0`, `contribution_per_period > 0 OR NULL`,
unique `(user_id, account_id, name)`.

### 3.8 Savings Goal Service

**File:** `app/services/savings_goal_service.py`

**Functions:**
- `calculate_required_contribution(current_balance, target_amount, remaining_periods)` --
  returns `Decimal` gap/periods or `None` if past due.
- `calculate_savings_metrics(savings_balance, average_monthly_expenses)` -- returns dict
  with `months_covered`, `paychecks_covered`, `years_covered`.
- `count_periods_until(target_date, periods)` -- counts periods from today to target_date.
- `compute_committed_monthly(expense_templates, transfer_templates)` -- converts biweekly
  amounts to monthly equivalent by recurrence pattern.

**No income-relative logic exists.** All targets are fixed dollar amounts.

### 3.9 Savings Goal Form

**File:** `app/templates/savings/goal_form.html`

Fields: `account_id` (select), `name`, `target_amount`, `target_date`,
`contribution_per_period`. No toggle for goal mode. No income-relative fields.

### 3.10 Savings Goal Schemas

**File:** `app/schemas/validation.py:405-435`

- `SavingsGoalCreateSchema`: `account_id` (required int), `name` (required str),
  `target_amount` (required Decimal > 0), `target_date` (optional Date),
  `contribution_per_period` (optional Decimal).
- `SavingsGoalUpdateSchema`: Same fields, all optional.

### 3.11 Transfer Creation Flow

**Recurring transfers:** Created via `POST /transfers` (full page form at
`app/routes/transfers.py`). User selects from/to accounts, amount, recurrence pattern, and
optional category. The route creates a `TransferTemplate`, `RecurrenceRule`, and calls
`transfer_recurrence.generate_for_template()` to create instances across pay periods. Each
instance creates a `Transfer` + two shadow transactions via `transfer_service.create_transfer()`.

**Ad-hoc transfers:** Created via `POST /transfers/ad-hoc` (HTMX endpoint). Same service
call path but no template/recurrence rule.

**No post-creation prompt exists.** After creating a debt account and saving parameters, the
user is left on the dashboard with no guidance to set up a recurring payment. They must
independently navigate to `/transfers/new` and configure it manually.

### 3.12 Paycheck Calculator API

**File:** `app/services/paycheck_calculator.py`

**Key function:** `calculate_paycheck(profile, period, all_periods, tax_configs, *,
calibration=None)` returns `PaycheckBreakdown` with `net_pay` (Decimal).

**For income-relative goals (task 5.4):** The route will need to get the current net biweekly
pay. The savings route already loads the active salary profile (`app/routes/savings.py:152-162`)
and computes `salary_gross_biweekly`. To get net pay, it would call `calculate_paycheck()`
for the current period.

### 3.13 Account Creation Redirect Flow

**File:** `app/routes/accounts.py:100-188`

After creating an account, the route checks the account type and redirects to the appropriate
config page with `setup=1` query parameter:
- HYSA -> `accounts.hysa_detail`
- Mortgage -> `mortgage.dashboard`
- Auto Loan -> `auto_loan.dashboard`
- Investment types -> `investment.dashboard`
- Student Loan / Personal Loan -> logs warning, redirects to accounts list (no config page)

---

## 4. Task Ordering and Dependencies

```
Commit #1: Student/personal loan param models + migration
     |
Commit #2: Student/personal loan schemas
     |
Commit #3: Student/personal loan routes + templates (setup + dashboard)
     |
Commit #4: Student/personal loan tests
     |
Commit #5: Wire student/personal loan into account creation redirect
     |
     +------+------+
     |             |
Commit #6      Commit #10
(5.1 part 1)   (5.4 part 1)
     |             |
Commit #7      Commit #11
(5.1 part 2)   (5.4 part 2)
     |             |
Commit #8      Commit #12
(5.1 part 3)   (5.4 part 3)
     |
Commit #9
(5.5)
     |
Commit #13: Final verification + cleanup
```

**Dependency rationale:**

- Commits #1-#5 (student/personal loan infrastructure) MUST come first. Tasks 5.1 and 5.5
  require "all debt account types" to have models, routes, and dashboards.
- Commit #6-#8 (task 5.1: payment linkage) depend on #5 because the post-parameter-save
  prompt needs to work for all four debt types.
- Commit #9 (task 5.5: payoff calculator extension) depends on #8 because the payoff
  calculator should incorporate payment history after 5.1 is done.
- Commits #10-#12 (task 5.4: income-relative goals) are independent of 5.1/5.5 and can
  be interleaved after #5, but are ordered after for clean commit history.

---

## 5. Commit Specifications

### Commit #1: Student Loan and Personal Loan Parameter Models

**Commit message:** `feat(models): add StudentLoanParams and PersonalLoanParams models`

**A. Problem statement**

Student loan and personal loan account types are seeded in `ref.account_types` with
`has_parameters=True` and `has_amortization=True`, but no parameter model exists to store
loan details. Without these models, no dashboard, payoff calculator, or payment linkage can
be built for these types.

**B. Files created**

1. `app/models/student_loan_params.py` -- New model file.
2. `app/models/personal_loan_params.py` -- New model file.

**C. Implementation**

Both models follow the exact pattern established by `AutoLoanParams` (the simplest existing
loan model). Mortgage has ARM/escrow complexity that does not apply to these loan types.
Student loans and personal loans are standard fixed-rate installment loans.

**StudentLoanParams** (`budget.student_loan_params`):

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | Integer | PK | |
| `account_id` | Integer | FK `budget.accounts.id`, unique, CASCADE | One-to-one |
| `original_principal` | Numeric(12,2) | NOT NULL | Original loan amount |
| `current_principal` | Numeric(12,2) | NOT NULL | Current remaining principal |
| `interest_rate` | Numeric(7,5) | NOT NULL | Annual rate (e.g. 0.05500) |
| `term_months` | Integer | NOT NULL | Original loan term |
| `origination_date` | Date | NOT NULL | Loan start date |
| `payment_day` | Integer | NOT NULL, CHECK 1-31 | Day of month payment due |
| `created_at` | DateTime(tz) | server_default now() | |
| `updated_at` | DateTime(tz) | server_default now(), onupdate | |

Relationship: `account = db.relationship("Account", backref="student_loan_params", lazy="joined")`

**PersonalLoanParams** (`budget.personal_loan_params`):

Identical column structure to `StudentLoanParams`. Different table name, different backref
name (`personal_loan_params`).

Both models use the same column set as `AutoLoanParams`. The amortization engine is
loan-type-agnostic -- it takes principal, rate, term, and payment_day regardless of loan type.

**D. Model file structure**

Each model file follows the pattern of `app/models/auto_loan_params.py`:
- Module docstring explaining the model
- Import `db` from `app.extensions`
- Class with `__tablename__`, `__table_args__` (schema, check constraints), columns,
  relationship, and `__repr__`
- Check constraint on `payment_day`: `payment_day >= 1 AND payment_day <= 31`

**E. Register in `app/models/__init__.py`**

Add imports for `StudentLoanParams` and `PersonalLoanParams` to the models `__init__.py`
so they are discovered by SQLAlchemy and Alembic.

**F. Migration**

Run `flask db migrate -m "add student_loan_params and personal_loan_params tables"`.

The migration creates two tables in the `budget` schema:
- `budget.student_loan_params` with all columns above
- `budget.personal_loan_params` with all columns above

Both tables have:
- `UNIQUE` constraint on `account_id`
- `FOREIGN KEY` on `account_id` referencing `budget.accounts(id)` with `ON DELETE CASCADE`
- `CHECK` constraint: `payment_day >= 1 AND payment_day <= 31`

No data migration needed (no existing data to backfill).

**G. Tests for this commit**

No behavioral tests needed for a pure model commit. The model is verified by:
1. Migration applies cleanly: `flask db upgrade`
2. Migration downgrades cleanly: `flask db downgrade` (one revision back)
3. Subsequent commits test CRUD operations via routes

**H. Verification**

- `flask db upgrade` succeeds
- `flask db downgrade` (one step) succeeds
- Tables exist: `SELECT * FROM budget.student_loan_params` returns empty result (no error)
- `SELECT * FROM budget.personal_loan_params` returns empty result (no error)
- `pylint app/models/student_loan_params.py` passes
- `pylint app/models/personal_loan_params.py` passes

---

### Commit #2: Student Loan and Personal Loan Validation Schemas

**Commit message:** `feat(schemas): add validation schemas for student and personal loan params`

**A. Problem statement**

Routes need Marshmallow schemas to validate and deserialize form data for student loan and
personal loan parameter creation and updates.

**B. Files modified**

1. `app/schemas/validation.py` -- Add four new schema classes.

**C. Implementation**

Add four schemas following the exact pattern of `AutoLoanParamsCreateSchema` and
`AutoLoanParamsUpdateSchema` (lines 577-602 in `validation.py`):

**StudentLoanParamsCreateSchema:**
- `original_principal`: Decimal, required, places=2, min=0
- `current_principal`: Decimal, required, places=2, min=0
- `interest_rate`: Decimal, required, places=3, min=0, max=100
- `term_months`: Integer, required, min=1, max=600 (student loans can be 25+ years)
- `origination_date`: Date, required
- `payment_day`: Integer, required, min=1, max=31

**StudentLoanParamsUpdateSchema:**
- Same fields, all optional (no `required=True`)

**PersonalLoanParamsCreateSchema:**
- Same structure as student loan create schema, but `term_months` max=120 (personal loans
  typically max at 10 years)

**PersonalLoanParamsUpdateSchema:**
- Same fields, all optional

All schemas extend `BaseSchema` and include the `strip_empty_strings` pre_load method.

**D. Tests for this commit**

Schema validation is implicitly tested by route tests in Commit #4. No standalone schema
tests needed (matching the existing pattern -- no standalone schema test files exist in the
test suite).

**E. Verification**

- `pylint app/schemas/validation.py` passes with no score decrease

---

### Commit #3: Student Loan and Personal Loan Routes and Templates

**Commit message:** `feat(loans): add routes and templates for student and personal loan accounts`

**A. Problem statement**

Student loan and personal loan accounts need dashboard pages, parameter setup pages, and
parameter update routes -- the same infrastructure that mortgage and auto loan already have.

**B. Files created**

1. `app/routes/student_loan.py` -- Blueprint with dashboard, create_params, update_params
2. `app/routes/personal_loan.py` -- Blueprint with dashboard, create_params, update_params
3. `app/templates/student_loan/dashboard.html` -- Dashboard with summary, chart, params form
4. `app/templates/student_loan/setup.html` -- Initial parameter entry form
5. `app/templates/personal_loan/dashboard.html` -- Dashboard with summary, chart, params form
6. `app/templates/personal_loan/setup.html` -- Initial parameter entry form

**C. Files modified**

1. `app/__init__.py` (or wherever blueprints are registered) -- Register `student_loan_bp`
   and `personal_loan_bp`.

**D. Implementation -- Routes**

Both route files follow the exact pattern of `app/routes/auto_loan.py`. Each file contains:

1. **`_load_*_account(account_id)`** -- private helper that loads the account, checks
   `current_user.id` ownership, verifies account type matches (using `ref_cache.acct_type_id()`),
   and loads params. Returns `(account, params)` or `(None, None)`.

2. **`_build_chart_data(schedule)`** -- identical helper to convert AmortizationRow list to
   Chart.js labels and data arrays.

3. **`GET /accounts/<id>/student-loan`** (`dashboard`) -- loads account and params; if params
   is None, renders setup template; otherwise computes `remaining_months`, `summary`, and
   `schedule` via amortization engine, renders dashboard template.

4. **`POST /accounts/<id>/student-loan/setup`** (`create_params`) -- validates with
   `StudentLoanParamsCreateSchema`, converts interest_rate from percentage to decimal (divide
   by 100), creates `StudentLoanParams`, commits, redirects to dashboard.

5. **`POST /accounts/<id>/student-loan/params`** (`update_params`) -- validates with
   `StudentLoanParamsUpdateSchema`, converts interest_rate, updates allowed fields
   (`current_principal`, `interest_rate`, `term_months`, `payment_day`), commits, redirects
   to dashboard.

The personal loan routes are identical except they use `PersonalLoanParams`,
`AcctTypeEnum.PERSONAL_LOAN`, the personal loan schemas, and URL prefix `/personal-loan`.

**Imports for each route file:**
- `logging`, `date` from datetime, `Decimal`
- `Blueprint`, `flash`, `redirect`, `render_template`, `request`, `url_for` from flask
- `current_user`, `login_required` from flask_login
- `ref_cache` from app, `AcctTypeEnum` from app.enums
- `db` from app.extensions
- `Account` from app.models.account
- The appropriate params model
- The appropriate schemas
- `amortization_engine` from app.services

**D2. Implementation -- Templates**

Both dashboard templates follow the exact pattern of `auto_loan/dashboard.html`:

- Extends `base.html`
- Title: `{{ account.name }} -- Student Loan -- Shekel` (or Personal Loan)
- Back button to `savings.dashboard`
- **Loan Summary card:** Original principal, current principal, interest rate (formatted as
  percentage), term, origination date, payment day, monthly payment (from `summary.monthly_payment`),
  total interest remaining (from `summary.total_interest`), projected payoff date
  (from `summary.payoff_date`)
- **Loan Parameters card:** Edit form posting to the update_params route. Fields:
  `current_principal`, `interest_rate` (percentage input with `step="0.001"`), `term_months`,
  `payment_day`. Interest rate converted to display percentage (`params.interest_rate * 100`).
- **Balance Over Time chart:** Chart.js canvas with `chart_labels` and `chart_standard` data
  attributes (same pattern as auto loan).

Both setup templates follow the exact pattern of `auto_loan/setup.html`:
- Form fields: `original_principal`, `current_principal`, `interest_rate` (percentage),
  `term_months`, `origination_date`, `payment_day`
- Student loan: icon `bi-mortarboard`, term_months max=600, placeholder="120" (10 years)
- Personal loan: icon `bi-cash-coin`, term_months max=120, placeholder="60" (5 years)
- Auto loan setup pre-populates `current_principal` from `account.current_anchor_balance`;
  both new setup templates should do the same.

**E. Blueprint registration**

In `app/__init__.py` (or wherever `create_app()` registers blueprints), add:
```python
from app.routes.student_loan import student_loan_bp
from app.routes.personal_loan import personal_loan_bp
app.register_blueprint(student_loan_bp)
app.register_blueprint(personal_loan_bp)
```

**F. Tests for this commit**

Tests are specified in Commit #4 (combined to avoid running tests against unregistered
blueprints).

**G. Verification**

- `flask run` starts without errors
- Navigate to `/accounts/new`, select "Student Loan", create account -- should redirect to
  accounts list (redirect wiring not done until Commit #5)
- `pylint app/routes/student_loan.py` passes
- `pylint app/routes/personal_loan.py` passes

---

### Commit #4: Student Loan and Personal Loan Route Tests

**Commit message:** `test(loans): add route tests for student and personal loan accounts`

**A. Problem statement**

New routes need test coverage for CRUD operations, ownership checks, and parameter validation.

**B. Files created**

1. `tests/test_routes/test_student_loan.py`
2. `tests/test_routes/test_personal_loan.py`

**C. Test specifications**

Each test file follows the pattern of existing route tests (e.g., `test_accounts.py`). Both
files have the same test structure, differing only in the model, URL prefix, enum member,
and schema-specific validation ranges.

**Fixtures:** Use `seed_user`, `auth_client` from conftest.py. Each test creates an account
of the appropriate type using the seeded `ref.account_types` data.

**Test cases for `test_student_loan.py`:**

| Test Name | Scenario | Expected |
| --- | --- | --- |
| `test_dashboard_no_params_shows_setup` | GET dashboard for account with no params | 200, renders setup.html |
| `test_dashboard_with_params_shows_summary` | GET dashboard after params created | 200, contains monthly payment amount |
| `test_create_params_valid` | POST setup with valid data | 302 redirect to dashboard, params row created |
| `test_create_params_duplicate` | POST setup when params already exist | Flash "already configured", redirect |
| `test_create_params_invalid_rate` | POST setup with interest_rate > 100 | Flash error, re-render setup |
| `test_update_params_valid` | POST params update with new principal | 302 redirect, principal updated in DB |
| `test_update_params_converts_rate` | POST with rate=5.5 | Stored as 0.05500 |
| `test_dashboard_wrong_user` | GET dashboard with second user's auth_client | 302 redirect, flash "not found" |
| `test_dashboard_wrong_account_type` | GET student-loan dashboard for a checking account | 302 redirect |
| `test_create_params_wrong_user` | POST setup with second user's client | 302 redirect |
| `test_zero_rate_loan` | Create params with rate=0, GET dashboard | 200, summary shows monthly payment = principal/months |
| `test_zero_remaining_months` | Create params with past origination + short term | 200, summary shows $0 payment |

**Test cases for `test_personal_loan.py`:** Same structure, same test names, different model
and URLs. Additional test:

| Test Name | Scenario | Expected |
| --- | --- | --- |
| `test_create_params_term_over_120` | POST setup with term_months=121 | Validation error (max 120 for personal loans) |

**D. Verification**

- `pytest tests/test_routes/test_student_loan.py -v` -- all pass
- `pytest tests/test_routes/test_personal_loan.py -v` -- all pass

---

### Commit #5: Wire Student and Personal Loan into Account Creation Flow

**Commit message:** `feat(accounts): wire student/personal loan into creation redirect and dashboard`

**A. Problem statement**

Creating a student loan or personal loan account logs a warning and redirects to the accounts
list. These types need to follow the established pattern: auto-create params record is NOT
done (unlike HYSA/investment which have sensible defaults -- loans require user input), but
redirect to the setup page so the user can configure parameters immediately.

**B. Files modified**

1. `app/routes/accounts.py` -- Add redirect cases for student loan and personal loan after
   account creation.
2. `app/routes/savings.py` -- Add student loan and personal loan params to `loan_params_map`
   loading so the savings dashboard shows their balances and projections correctly.

**C. Implementation -- accounts.py**

In `create_account()`, after the auto loan redirect block (line 169-172), add:

```python
if acct_type_id == ref_cache.acct_type_id(AcctTypeEnum.STUDENT_LOAN):
    return redirect(url_for(
        "student_loan.dashboard", account_id=account.id, setup=1,
    ))
if acct_type_id == ref_cache.acct_type_id(AcctTypeEnum.PERSONAL_LOAN):
    return redirect(url_for(
        "personal_loan.dashboard", account_id=account.id, setup=1,
    ))
```

Remove the warning log block at lines 178-186 that handles the fallthrough for these types.

Add imports for the student/personal loan models at the top of the file.

**C2. Implementation -- savings.py**

In the dashboard route, extend the loan params loading section (currently lines 165-176)
to also load `StudentLoanParams` and `PersonalLoanParams`:

```python
student_loan_type_id = ref_cache.acct_type_id(AcctTypeEnum.STUDENT_LOAN)
personal_loan_type_id = ref_cache.acct_type_id(AcctTypeEnum.PERSONAL_LOAN)

student_loan_ids = [a.id for a in accounts if a.account_type_id == student_loan_type_id]
if student_loan_ids:
    from app.models.student_loan_params import StudentLoanParams
    for slp in db.session.query(StudentLoanParams).filter(
        StudentLoanParams.account_id.in_(student_loan_ids)
    ).all():
        loan_params_map[slp.account_id] = slp

personal_loan_ids = [a.id for a in accounts if a.account_type_id == personal_loan_type_id]
if personal_loan_ids:
    from app.models.personal_loan_params import PersonalLoanParams
    for plp in db.session.query(PersonalLoanParams).filter(
        PersonalLoanParams.account_id.in_(personal_loan_ids)
    ).all():
        loan_params_map[plp.account_id] = plp
```

Also extend the `needs_setup` detection block (line 327) to include student loan and
personal loan type IDs:

```python
elif acct.account_type_id in (mortgage_type_id, auto_loan_type_id,
                               student_loan_type_id, personal_loan_type_id):
    needs_setup = acct_loan_params is None
```

**D. Tests**

| Test Name | File | Scenario | Expected |
| --- | --- | --- | --- |
| `test_create_student_loan_redirects_to_setup` | `test_accounts.py` | POST create with student loan type | 302 to student_loan.dashboard with setup=1 |
| `test_create_personal_loan_redirects_to_setup` | `test_accounts.py` | POST create with personal loan type | 302 to personal_loan.dashboard with setup=1 |
| `test_dashboard_shows_student_loan_balance` | `test_savings.py` | Create student loan with params, GET savings dashboard | Account appears in Liability section with correct balance |
| `test_dashboard_shows_personal_loan_balance` | `test_savings.py` | Create personal loan with params, GET savings dashboard | Account appears in Liability section |

**E. Verification**

- Create a student loan account via the UI -- redirects to student loan setup page
- Create a personal loan account via the UI -- redirects to personal loan setup page
- After saving params, the savings dashboard shows the loan with monthly payment and payoff date
- `pytest tests/test_routes/test_accounts.py -v` -- passes (including new tests)
- `pytest tests/test_routes/test_savings.py -v` -- passes (including new tests)

---

### Commit #6: Debt Payment Linkage -- Post-Parameter-Save Prompt (Task 5.1, Part 1)

**Commit message:** `feat(transfers): add recurring transfer prompt after debt account parameter setup`

**A. Problem statement**

After creating a debt account and saving parameters, the user has no guidance to set up a
recurring payment. They must independently navigate to the transfer template page and
configure everything manually. The app should prompt the user to create a recurring monthly
transfer from their checking account to the debt account.

**B. Files modified**

1. `app/routes/mortgage.py` -- Add payment prompt redirect after `create_params`.
2. `app/routes/auto_loan.py` -- Add payment prompt redirect after `create_params`.
3. `app/routes/student_loan.py` -- Add payment prompt redirect after `create_params`.
4. `app/routes/personal_loan.py` -- Add payment prompt redirect after `create_params`.
5. `app/routes/transfers.py` -- Add prefill support to the transfer template creation form.
6. `app/templates/transfers/form.html` -- Display pre-populated values and explanatory banner.

**C. Implementation approach**

Rather than building a custom inline form on each debt dashboard, leverage the existing
transfer template creation page with **prefill query parameters**. This reuses the existing,
tested transfer creation flow rather than duplicating it.

**Flow:**

1. User creates a debt account and saves parameters (via `create_params` route).
2. The route calculates the monthly payment from the amortization engine.
3. The route redirects to `/transfers/new?prefill_to=<account_id>&prefill_amount=<monthly_payment>&prefill_from=<checking_id>&setup_prompt=1`
   instead of redirecting directly to the dashboard.
4. The transfer template form detects `setup_prompt=1` and shows a banner:
   "Set up a recurring monthly payment for [account name]? The calculated monthly payment
   is $X,XXX.XX. You can adjust the amount, source account, and schedule below."
5. The banner includes a "Skip -- go to dashboard" link.
6. The form pre-populates: `to_account` = debt account, `from_account` = user's first
   active checking account, `default_amount` = calculated monthly payment, recurrence
   pattern = Monthly, `day_of_month` = params.payment_day.

**C2. Route changes (all four debt route files)**

In each `create_params()` function, after the `db.session.commit()` and flash message,
replace the redirect to dashboard with:

```python
# Calculate monthly payment for the prompt.
remaining = amortization_engine.calculate_remaining_months(
    params.origination_date, params.term_months,
)
monthly = amortization_engine.calculate_monthly_payment(
    Decimal(str(params.current_principal)),
    Decimal(str(params.interest_rate)),
    remaining,
)

# Find user's first active checking account for the prefill.
checking_type_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)
checking = (
    db.session.query(Account)
    .filter_by(user_id=current_user.id, account_type_id=checking_type_id, is_active=True)
    .order_by(Account.sort_order, Account.id)
    .first()
)

return redirect(url_for(
    "transfers.new_transfer_template",
    prefill_to=account.id,
    prefill_from=checking.id if checking else "",
    prefill_amount=str(monthly),
    prefill_day=params.payment_day,
    setup_prompt=1,
    return_to=url_for("<type>.dashboard", account_id=account.id),
))
```

Where `<type>` is `mortgage`, `auto_loan`, `student_loan`, or `personal_loan` respectively.

Import `Account` model if not already imported. Import `AcctTypeEnum.CHECKING`.

**C3. Transfer route changes**

In `new_transfer_template()` in `app/routes/transfers.py`, read query parameters:

```python
prefill_to = request.args.get("prefill_to", type=int)
prefill_from = request.args.get("prefill_from", type=int)
prefill_amount = request.args.get("prefill_amount", "")
prefill_day = request.args.get("prefill_day", type=int)
setup_prompt = request.args.get("setup_prompt", type=int)
return_to = request.args.get("return_to", "")
```

Pass these to the template context. Verify the `prefill_to` and `prefill_from` accounts
exist and belong to the current user before passing them to the template (ownership check).

**C4. Template changes**

In `app/templates/transfers/form.html`:

1. If `setup_prompt` is truthy, render an alert banner at the top:
   ```html
   {% if setup_prompt %}
   <div class="alert alert-info">
     <strong>Set up a recurring payment?</strong>
     The calculated monthly payment is ${{ prefill_amount }}.
     Adjust the details below and save, or
     <a href="{{ return_to or url_for('savings.dashboard') }}">skip this step</a>.
   </div>
   {% endif %}
   ```

2. Pre-populate form fields from prefill values:
   - `default_amount` input: `value="{{ prefill_amount }}"` when set
   - `to_account_id` select: mark option as `selected` when matching `prefill_to`
   - `from_account_id` select: mark option as `selected` when matching `prefill_from`
   - Recurrence pattern: pre-select "Monthly" when `setup_prompt`
   - `day_of_month` input: `value="{{ prefill_day }}"` when set

The transfer form template already accepts `prefill_from` and `prefill_to` in its context
(noted in the routes/templates exploration). Extend this to also accept `prefill_amount`,
`prefill_day`, and `setup_prompt`.

**D. Edge cases**

- **No checking account:** If the user has no active checking account, `prefill_from` is
  empty. The form still renders but without a pre-selected source. The user must select one.
- **Already has a recurring transfer:** The prompt always appears after initial parameter
  setup. If the user already set up a transfer (unlikely on first setup), they can click
  "Skip." No guard against duplicate transfers is needed -- the user controls this.
- **Parameter update (not initial setup):** The prompt only fires on `create_params()`, NOT
  on `update_params()`. Updating existing parameters does not re-prompt.
- **Monthly payment is $0:** If the loan is paid off (remaining_months=0 or principal=0),
  do not redirect to the transfer prompt. Check `monthly > 0` before redirecting; if zero,
  redirect to dashboard directly.

**E. Tests**

| Test Name | File | Scenario | Expected |
| --- | --- | --- | --- |
| `test_create_mortgage_params_redirects_to_transfer_prompt` | `test_routes/test_mortgage.py` | POST create_params with valid data | 302 to transfers/new with prefill params |
| `test_create_auto_loan_params_redirects_to_transfer_prompt` | `test_routes/test_auto_loan.py` | POST create_params with valid data | 302 to transfers/new with prefill params |
| `test_create_student_loan_params_redirects_to_transfer_prompt` | `test_routes/test_student_loan.py` | POST create_params with valid data | 302 to transfers/new with prefill params |
| `test_create_personal_loan_params_redirects_to_transfer_prompt` | `test_routes/test_personal_loan.py` | POST create_params with valid data | 302 to transfers/new with prefill params |
| `test_transfer_form_shows_setup_banner` | `test_routes/test_transfers.py` | GET /transfers/new?setup_prompt=1&prefill_to=X | 200, contains "Set up a recurring payment" |
| `test_transfer_form_prefills_amount` | `test_routes/test_transfers.py` | GET with prefill_amount=599.55 | 200, input value contains 599.55 |
| `test_paid_off_loan_skips_prompt` | `test_routes/test_mortgage.py` | POST create_params with zero principal | 302 to mortgage dashboard (not transfers) |
| `test_update_params_does_not_prompt` | `test_routes/test_mortgage.py` | POST update_params | 302 to mortgage dashboard (not transfers) |

**F. Verification**

1. Create a new mortgage account, fill in setup params, submit.
2. Verify redirect to `/transfers/new` with pre-populated fields and setup banner.
3. Verify the banner shows the correct monthly payment amount.
4. Verify "Skip" link goes to the mortgage dashboard.
5. Submit the transfer form -- verify transfer template created, recurrence generates instances.
6. Repeat for auto loan, student loan, and personal loan.
7. Update existing mortgage params -- verify redirect goes to dashboard (no prompt).

**G. Downstream effects**

- Existing mortgage `create_params` tests that expect a redirect to `mortgage.dashboard`
  will need to be updated to expect a redirect to `transfers.new_transfer_template`.
- Same for auto loan tests.

---

### Commit #7: Amortization Engine -- Accept Payment History (Task 5.1, Part 2)

**Commit message:** `feat(amortization): add payment history support to schedule generation`

**A. Problem statement**

The amortization engine generates schedules based on static parameters only. It does not
accept actual payment history. When a user makes an extra payment (or any payment that
differs from the standard amount), the projected schedule does not reflect reality. The
dashboard shows a payoff date and interest total that ignore actual payments.

**B. Files modified**

1. `app/services/amortization_engine.py` -- Add `payment_history` parameter to
   `generate_schedule`, `calculate_summary`, and `calculate_payoff_by_date`.

**C. Implementation**

**New parameter on `generate_schedule`:**

```python
def generate_schedule(
    current_principal: Decimal,
    annual_rate: Decimal,
    remaining_months: int,
    extra_monthly: Decimal = Decimal("0.00"),
    origination_date: date | None = None,
    payment_day: int = 1,
    payment_history: list[tuple[date, Decimal]] | None = None,
) -> list[AmortizationRow]:
```

`payment_history` is an optional list of `(payment_date, total_amount)` tuples representing
actual payments made. Each entry is the total payment amount (principal + interest + any
extra). The list must be sorted by date ascending.

**Modified logic:**

When `payment_history` is provided:

1. Build a dict mapping `(year, month)` to total payment amount from the history. If
   multiple payments fall in the same month, sum them.
2. During the month-by-month loop, for each month, check if a historical payment exists:
   - If yes: use the historical payment amount instead of `monthly_payment + extra_monthly`.
     Calculate interest as before (`balance * monthly_rate`), subtract interest from the
     historical amount to get the actual principal portion, reduce the balance.
   - If no: use the standard `monthly_payment + extra_monthly` (existing behavior).
3. After all historical months are consumed, continue with projected payments using
   `monthly_payment + extra_monthly` for the remaining months.

This preserves the pure function pattern -- no database access, no side effects. The caller
provides the payment history; the engine just computes.

**Edge cases in the modified logic:**

- **Historical payment less than interest:** If the actual payment was less than the monthly
  interest (negative amortization), `principal_portion` would be negative. Clamp
  `principal_portion` to `Decimal("0.00")` -- the unpaid interest is absorbed (the user's
  anchor balance true-up will correct the principal). Log nothing (pure function).

- **Historical payment exceeds remaining balance + interest:** The payment fully pays off the
  loan. Set `balance = 0`, record the overpayment in the `extra_payment` field of the
  `AmortizationRow`, terminate the schedule.

- **Empty payment history:** `payment_history=[]` or `None` behaves exactly as the current
  implementation (backward compatible).

- **Payment in a month that doesn't match the schedule:** If the payment date doesn't align
  with the payment_day, map it to the nearest schedule month using `(year, month)` matching.
  This handles cases where the user pays on the 3rd but payment_day is the 15th.

**Modified `calculate_summary`:**

Add the same `payment_history` parameter. Pass it through to both `generate_schedule` calls
(standard and accelerated). The "standard" schedule uses `payment_history` with
`extra_monthly=0`, and the "accelerated" schedule uses `payment_history` with the provided
`extra_monthly`.

**Modified `calculate_payoff_by_date`:**

Add the same `payment_history` parameter. Pass it through to the standard schedule generation
and to each binary search iteration's `generate_schedule` call.

**D. Backward compatibility**

All existing callers pass no `payment_history` argument. The default `None` preserves existing
behavior exactly. No caller changes are needed for this commit.

**E. Tests**

**File:** `tests/test_services/test_amortization_engine.py` -- add new test class
`TestPaymentHistorySchedule`.

| Test Name | Scenario | Expected |
| --- | --- | --- |
| `test_schedule_with_no_history_unchanged` | `payment_history=None` | Identical to existing behavior (regression) |
| `test_schedule_with_empty_history_unchanged` | `payment_history=[]` | Identical to existing behavior |
| `test_schedule_with_standard_payments` | History matches standard payment amounts | Schedule identical to no-history schedule |
| `test_schedule_with_extra_payment_in_history` | One month has $1000 extra payment | Balance drops faster, schedule shorter |
| `test_schedule_with_underpayment` | One month has $0 payment (skipped payment) | Balance increases by interest, schedule extends |
| `test_history_payment_less_than_interest` | Payment of $100 on a $200 interest month | Principal portion clamped to $0, balance unchanged |
| `test_history_payment_exceeds_balance` | $50,000 payment on $10,000 balance | Balance goes to $0, schedule terminates |
| `test_history_then_projection` | 3 months history, then 57 months projection | First 3 months match history, remaining match standard amortization from new balance |
| `test_multiple_payments_same_month` | Two payments in same month | Summed and treated as single payment |
| `test_summary_with_payment_history` | Summary with 6 months of history | Interest saved and months saved reflect actual payments |
| `test_payoff_by_date_with_history` | Payoff by date with history showing extra payments already made | Required extra monthly is lower because balance is already reduced |

**Verification of Decimal precision:** All test values use `Decimal` inputs. Verify all
outputs are `Decimal` with 2 decimal places. No `float` anywhere in the engine.

**F. Verification**

- `pytest tests/test_services/test_amortization_engine.py -v` -- all pass (old + new)
- `pylint app/services/amortization_engine.py` -- passes

---

### Commit #8: Wire Payment History into Debt Dashboards (Task 5.1, Part 3)

**Commit message:** `feat(loans): incorporate actual payment history into dashboard projections`

**A. Problem statement**

The amortization engine now accepts payment history, but the debt account dashboards still
pass only static parameters. The dashboard summary, chart, and payoff calculator should
reflect actual payments made (transfers to the debt account).

**B. Files modified**

1. `app/routes/mortgage.py` -- Query payment history from shadow transactions, pass to
   amortization engine.
2. `app/routes/auto_loan.py` -- Same.
3. `app/routes/student_loan.py` -- Same.
4. `app/routes/personal_loan.py` -- Same.
5. `app/routes/savings.py` -- Pass payment history to amortization calls in the savings
   dashboard.

**C. Implementation -- Shared helper**

To avoid duplicating the payment history query in four route files, create a shared helper.

**Option:** Add a function to `app/services/amortization_engine.py` would violate the pure
function constraint (no DB access). Instead, add a helper in a new utility module or in
each route file. Since all four routes need the same query, place it in a small utility:

**New file:** `app/utils/loan_helpers.py`

```python
"""Helpers for debt account routes. Thin DB wrappers -- not service-layer logic."""

from decimal import Decimal
from app.extensions import db
from app.models.transaction import Transaction


def get_payment_history(account_id, scenario_id):
    """Query actual payment history for a debt account.

    Returns a list of (payment_date, total_amount) tuples sorted by date,
    suitable for passing to amortization_engine.generate_schedule().

    Payments are shadow income transactions (transfer_id IS NOT NULL,
    is_income=True) in the debt account -- money transferred into the
    loan to pay it down.
    """
    from app import ref_cache
    from app.enums import TxnTypeEnum

    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)

    shadow_payments = (
        db.session.query(Transaction)
        .join(Transaction.pay_period)
        .filter(
            Transaction.account_id == account_id,
            Transaction.transfer_id.isnot(None),
            Transaction.transaction_type_id == income_type_id,
            Transaction.is_deleted.is_(False),
            Transaction.scenario_id == scenario_id,
        )
        .order_by(Transaction.pay_period.property.mapper.class_.start_date)
        .all()
    )

    history = []
    for txn in shadow_payments:
        # Use actual_amount if settled, estimated_amount otherwise.
        amount = Decimal(str(txn.effective_amount))
        if amount > 0:
            payment_date = txn.pay_period.start_date
            history.append((payment_date, amount))

    return history
```

**Note on status filtering:** Include ALL non-deleted, non-cancelled shadow income
transactions. Projected payments represent future scheduled transfers and should be included
in the schedule so the projection shows the effect of planned payments. Cancelled payments
(`excludes_from_balance=True`) are already excluded by `effective_amount` returning 0.

**C2. Route changes -- mortgage.py `dashboard()`**

After loading params, query payment history and pass to amortization calls:

```python
from app.utils.loan_helpers import get_payment_history

# In dashboard():
scenario = db.session.query(Scenario).filter_by(
    user_id=current_user.id, is_baseline=True
).first()
payment_history = get_payment_history(account.id, scenario.id) if scenario else []

# Pass to calculate_summary:
summary = amortization_engine.calculate_summary(
    ...,
    payment_history=payment_history,
)

# Pass to generate_schedule (for chart):
schedule = amortization_engine.generate_schedule(
    ...,
    payment_history=payment_history,
)
```

Same change in `payoff_calculate()` -- pass `payment_history` to `calculate_summary`,
`generate_schedule` (both standard and accelerated), and `calculate_payoff_by_date`.

**C3. Same pattern for auto_loan, student_loan, personal_loan routes**

Each dashboard function queries payment history and passes it to amortization calls.

**C4. savings.py changes**

In the savings dashboard, the loan projection block (lines 222-249) uses the amortization
engine but does NOT pass payment history. Update it to query and pass history:

```python
if acct_loan_params:
    scenario_id = scenario.id if scenario else None
    acct_payment_history = []
    if scenario_id:
        from app.utils.loan_helpers import get_payment_history
        acct_payment_history = get_payment_history(acct.id, scenario_id)

    # Pass to generate_schedule and calculate_summary:
    schedule = amortization_engine.generate_schedule(
        principal, rate, remaining,
        payment_day=acct_loan_params.payment_day,
        payment_history=acct_payment_history,
    )
    summary = amortization_engine.calculate_summary(
        principal, rate, remaining,
        _date.today().replace(day=1),
        acct_loan_params.payment_day,
        acct_loan_params.term_months,
        payment_history=acct_payment_history,
    )
```

**D. Edge cases**

- **No transfers to debt account yet:** `payment_history` is an empty list. Behavior is
  identical to current (static projection).
- **Debt account with no scenario:** `payment_history` is an empty list. Defensive fallback.
- **Future scheduled transfers included:** Projected transfers (status=Projected) are included
  in the history. Their `effective_amount` uses `estimated_amount`. This means the dashboard
  shows the projected payoff incorporating future scheduled payments -- which is the desired
  behavior (the user sees what happens if they keep making planned payments).

**E. Tests**

| Test Name | File | Scenario | Expected |
| --- | --- | --- | --- |
| `test_mortgage_dashboard_with_payment_history` | `test_routes/test_mortgage.py` | Create mortgage, set up recurring transfer, GET dashboard | Summary shows shorter payoff date if extra payments made |
| `test_mortgage_payoff_with_history` | `test_routes/test_mortgage.py` | POST payoff calculate with existing payments | Results reflect actual balance after payments |
| `test_auto_loan_dashboard_with_payments` | `test_routes/test_auto_loan.py` | Create auto loan with transfer, GET dashboard | Summary reflects payment history |
| `test_loan_dashboard_no_payments` | `test_routes/test_student_loan.py` | Dashboard with no transfers | Same as static projection |
| `test_get_payment_history_empty` | `test_services/test_loan_helpers.py` | Account with no shadow transactions | Returns empty list |
| `test_get_payment_history_filters_cancelled` | `test_services/test_loan_helpers.py` | Account with cancelled transfer | Cancelled payment excluded |
| `test_get_payment_history_includes_projected` | `test_services/test_loan_helpers.py` | Account with projected future transfer | Projected payment included in history |
| `test_get_payment_history_sorted_by_date` | `test_services/test_loan_helpers.py` | Multiple payments across periods | Returned sorted by pay period start_date |

**F. Verification**

1. Create a mortgage account with params.
2. Set up a recurring monthly transfer from checking to mortgage.
3. Mark 2-3 transfer instances as "Paid" (done).
4. Navigate to mortgage dashboard -- verify summary metrics (payoff date, total interest)
   reflect the actual payments.
5. Open payoff calculator, enter extra payment -- verify results account for history.
6. Navigate to savings dashboard -- verify mortgage balance projection reflects payments.
7. Repeat for auto loan.

**G. Downstream effects**

- Balance over time chart on each debt dashboard now shows the actual payment trajectory
  (payments already made) transitioning into projected trajectory (future payments).
- Savings dashboard loan projections now incorporate payment history.

---

### Commit #9: Payoff Calculator for All Debt Accounts (Task 5.5)

**Commit message:** `feat(loans): add payoff calculator to auto loan, student loan, and personal loan dashboards`

**A. Problem statement**

The payoff calculator (extra payment mode and target date mode) exists only on the mortgage
dashboard. Auto loan, student loan, and personal loan dashboards do not have this feature
despite sharing the same amortization engine.

**B. Files modified**

1. `app/routes/auto_loan.py` -- Add `payoff_calculate` route.
2. `app/routes/student_loan.py` -- Add `payoff_calculate` route.
3. `app/routes/personal_loan.py` -- Add `payoff_calculate` route.
4. `app/routes/mortgage.py` -- Update `payoff_calculate` to render shared template.
5. `app/templates/auto_loan/dashboard.html` -- Add payoff calculator tab.
6. `app/templates/student_loan/dashboard.html` -- Add payoff calculator tab.
7. `app/templates/personal_loan/dashboard.html` -- Add payoff calculator tab.
8. `app/templates/mortgage/dashboard.html` -- Replace inline payoff form/results with shared includes.
9. `app/templates/loans/_payoff_results.html` -- New shared template for payoff results.
10. `app/templates/loans/_payoff_form.html` -- New shared template for payoff form.

**C. Implementation -- Shared templates**

The payoff calculator UI is identical across all debt types. Rather than duplicating the
mortgage's payoff results template four times, create shared partials:

**`app/templates/loans/_payoff_form.html`** -- The form with two sub-tabs (Extra Payment,
Target Date). Parameterized with `payoff_url` and `slider_max` variables so each debt type
can point to its own route and the slider auto-scales to a meaningful range:

```html
{# Shared payoff calculator form.
   Include with:
     payoff_url  = url_for(...)
     slider_max  = auto-scaled integer (see route logic below)
#}
{% set smax = slider_max|default(2000) %}
<ul class="nav nav-pills mb-3" role="tablist">
  <li class="nav-item">
    <button class="nav-link active" data-bs-toggle="pill"
            data-bs-target="#extra-tab">Extra Payment</button>
  </li>
  <li class="nav-item">
    <button class="nav-link" data-bs-toggle="pill"
            data-bs-target="#target-tab">Target Date</button>
  </li>
</ul>
<div class="tab-content">
  <div class="tab-pane fade show active" id="extra-tab">
    <form hx-post="{{ payoff_url }}" hx-target="#payoff-results" hx-swap="innerHTML"
          hx-trigger="submit, slider-changed">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <input type="hidden" name="mode" value="extra_payment">
      <div class="mb-3">
        <label class="form-label">Extra Monthly Payment</label>
        <input type="range" class="form-range" name="extra_monthly"
               min="0" max="{{ smax }}" step="25" value="0"
               id="extra-slider">
        <div class="d-flex justify-content-between">
          <span class="small text-muted">$0</span>
          <span class="small fw-bold font-mono" id="extra-display">$0</span>
          <span class="small text-muted">${{ "{:,.0f}".format(smax) }}</span>
        </div>
      </div>
      <button type="submit" class="btn btn-sm btn-primary">Calculate</button>
    </form>
  </div>
  <div class="tab-pane fade" id="target-tab">
    <form hx-post="{{ payoff_url }}" hx-target="#payoff-results" hx-swap="innerHTML">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <input type="hidden" name="mode" value="target_date">
      <div class="mb-3">
        <label class="form-label">Target Payoff Date</label>
        <input type="date" class="form-control" name="target_date" required>
      </div>
      <button type="submit" class="btn btn-sm btn-primary">Calculate</button>
    </form>
  </div>
</div>
<div id="payoff-results" class="mt-3"></div>
```

**Slider auto-scaling logic (in each route's `dashboard()`):**

The slider max is set to `2 * monthly_payment`, rounded up to the nearest $25, with a
floor of $100. This ensures the range is always meaningful regardless of loan size -- a
$200/month personal loan gets a slider from $0-$400, while a $1,500/month mortgage gets
$0-$3,000.

```python
import math

def _slider_max(monthly_payment):
    """Calculate auto-scaled slider max: 2x monthly payment, rounded up to nearest $25."""
    raw = float(monthly_payment) * 2
    rounded = int(math.ceil(raw / 25) * 25)
    return max(rounded, 100)
```

Each route passes `slider_max` to the template context alongside `payoff_url`. For the
mortgage route, the existing hardcoded $2,000 max is replaced with this calculation.

**`app/templates/loans/_payoff_results.html`** -- Identical to `mortgage/_payoff_results.html`.
Copy the content from the existing mortgage template.

**C2. Implementation -- Routes**

Add `payoff_calculate` to each of the three non-mortgage debt route files. The function
is identical to `mortgage.payoff_calculate()` except:
- Uses the type-specific `_load_*_account()` helper
- Uses the type-specific params model
- Does not include escrow in the payment (escrow is mortgage-only)

```python
@<blueprint>.route("/accounts/<int:account_id>/<url-prefix>/payoff", methods=["POST"])
@login_required
def payoff_calculate(account_id):
    """Calculate payoff scenario (HTMX)."""
    account, params = _load_*_account(account_id)
    if account is None or params is None:
        return "Account not found", 404

    errors = _payoff_schema.validate(request.form)
    if errors:
        return render_template("loans/_payoff_results.html", error="...")

    data = _payoff_schema.load(request.form)
    mode = data["mode"]
    remaining_months = amortization_engine.calculate_remaining_months(
        params.origination_date, params.term_months,
    )
    schedule_start = date.today().replace(day=1)

    # Get payment history for accurate projections.
    scenario = db.session.query(Scenario).filter_by(
        user_id=current_user.id, is_baseline=True
    ).first()
    payment_history = get_payment_history(account.id, scenario.id) if scenario else []

    if mode == "extra_payment":
        extra = Decimal(str(data.get("extra_monthly", "0")))
        payoff_summary = amortization_engine.calculate_summary(
            current_principal=Decimal(str(params.current_principal)),
            annual_rate=Decimal(str(params.interest_rate)),
            remaining_months=remaining_months,
            origination_date=schedule_start,
            payment_day=params.payment_day,
            term_months=params.term_months,
            extra_monthly=extra,
            payment_history=payment_history,
        )
        # Chart data...
        standard_schedule = amortization_engine.generate_schedule(
            ..., payment_history=payment_history,
        )
        accelerated_schedule = amortization_engine.generate_schedule(
            ..., extra_monthly=extra, payment_history=payment_history,
        )
        chart_labels, chart_standard = _build_chart_data(standard_schedule)
        _, chart_accelerated = _build_chart_data(accelerated_schedule)

        return render_template(
            "loans/_payoff_results.html",
            mode=mode,
            payoff_summary=payoff_summary,
            chart_labels=chart_labels,
            chart_standard=chart_standard,
            chart_accelerated=chart_accelerated,
        )

    elif mode == "target_date":
        target_date = data.get("target_date")
        if not target_date:
            return render_template("loans/_payoff_results.html", error="...")

        required_extra = amortization_engine.calculate_payoff_by_date(
            ..., payment_history=payment_history,
        )
        monthly_payment = amortization_engine.calculate_monthly_payment(...)

        return render_template(
            "loans/_payoff_results.html",
            mode=mode,
            required_extra=required_extra,
            monthly_payment=monthly_payment,
        )

    return render_template("loans/_payoff_results.html", error="Invalid mode.")
```

Import `PayoffCalculatorSchema` and `Scenario` in each route file. Add `_payoff_schema`
module-level instance.

**C3. Implementation -- Dashboard templates**

Add a "Payoff Calculator" tab to each dashboard template. The structure follows the mortgage
dashboard's tab pattern:

```html
{# In each dashboard.html, add after the Balance Over Time chart card: #}
<div class="card mt-3">
  <div class="card-header">
    <h6 class="mb-0"><i class="bi bi-calculator"></i> Payoff Calculator</h6>
  </div>
  <div class="card-body">
    {% include "loans/_payoff_form.html" %}
  </div>
</div>
```

Each dashboard template passes `payoff_url` and `slider_max` to the include:

```html
{% set payoff_url = url_for('auto_loan.payoff_calculate', account_id=account.id) %}
{% set slider_max = slider_max %}
{% include "loans/_payoff_form.html" %}
```

The `slider_max` value is computed in the route and passed to the dashboard template context.

**C4. Mortgage refactor to shared templates**

Update `mortgage/dashboard.html` to replace its inline payoff calculator form and results
with the shared `loans/_payoff_form.html` and `loans/_payoff_results.html` includes. This
eliminates the duplication between the mortgage-specific and shared templates.

Changes to `app/routes/mortgage.py`:
- In `dashboard()`: compute `slider_max` from `summary.monthly_payment` using
  `_slider_max()` and pass it to the template context.
- In `payoff_calculate()`: render `loans/_payoff_results.html` instead of
  `mortgage/_payoff_results.html`.

Changes to `app/templates/mortgage/dashboard.html`:
- Replace the inline payoff calculator tab content with:
  ```html
  {% set payoff_url = url_for('mortgage.payoff_calculate', account_id=account.id) %}
  {% set slider_max = slider_max %}
  {% include "loans/_payoff_form.html" %}
  ```
- Remove any inline payoff form HTML that the shared partial now provides.

Do NOT delete `mortgage/_payoff_results.html` in this commit. It is removed in Commit #13
(cleanup) after the shared template is verified working across all four debt types.

**D. Edge cases**

- **Loan with zero remaining months:** Payoff calculator should show "Loan is already paid
  off" or equivalent. The amortization engine returns empty schedule for zero remaining
  months, and `calculate_payoff_by_date` returns `Decimal("0.00")`.
- **Target date in the past:** `calculate_payoff_by_date` returns `None`. Template shows
  warning message.
- **Extra payment exceeds remaining balance:** Schedule terminates early. Results show
  correct (short) payoff date.
- **No params configured:** `params is None` returns 404 before reaching payoff logic.

**E. Tests**

| Test Name | File | Scenario | Expected |
| --- | --- | --- | --- |
| `test_auto_loan_payoff_extra_payment` | `test_routes/test_auto_loan.py` | POST payoff with mode=extra_payment, extra=200 | 200, contains months saved |
| `test_auto_loan_payoff_target_date` | `test_routes/test_auto_loan.py` | POST payoff with mode=target_date | 200, contains required extra monthly |
| `test_auto_loan_payoff_target_past` | `test_routes/test_auto_loan.py` | POST with past target_date | 200, warning about unachievable |
| `test_student_loan_payoff_extra_payment` | `test_routes/test_student_loan.py` | POST payoff with extra_monthly=100 | 200, contains interest saved |
| `test_student_loan_payoff_target_date` | `test_routes/test_student_loan.py` | POST with future target_date | 200, contains required extra |
| `test_personal_loan_payoff_extra_payment` | `test_routes/test_personal_loan.py` | POST payoff with extra_monthly=50 | 200, shows results |
| `test_personal_loan_payoff_wrong_user` | `test_routes/test_personal_loan.py` | POST with second user | 404 |
| `test_payoff_no_params` | `test_routes/test_auto_loan.py` | POST payoff for account without params | 404 |
| `test_mortgage_payoff_uses_shared_template` | `test_routes/test_mortgage.py` | POST payoff after refactor to shared template | 200, same results as before refactor |
| `test_slider_max_auto_scales` | `test_routes/test_auto_loan.py` | GET dashboard for $300/mo loan | Response contains max="600" (2x, rounded to nearest 25) |
| `test_slider_max_floor` | `test_routes/test_auto_loan.py` | GET dashboard for nearly paid-off loan ($10/mo) | Response contains max="100" (floor) |

**F. Verification**

1. Navigate to auto loan dashboard -- verify "Payoff Calculator" section visible.
2. Verify the slider max auto-scales based on the monthly payment (e.g., $300/mo loan
   should show slider from $0-$600; $1,500/mo mortgage should show $0-$3,000).
3. Slide the extra payment slider -- verify results update (HTMX fragment).
4. Enter a target date -- verify required extra payment shown.
5. Repeat for student loan and personal loan dashboards.
6. Navigate to mortgage dashboard -- verify payoff calculator still works identically after
   refactor to shared templates. Compare results for the same inputs as before the refactor.
7. Verify the slider max on the mortgage dashboard reflects `2 * monthly_payment` instead
   of the previous hardcoded $2,000.

**G. Downstream effects**

- All four debt account types now have identical payoff calculator functionality.
- The payoff calculator incorporates actual payment history (from Commit #8), so results
  reflect real payments, not just static parameters.
- Mortgage payoff calculator now uses shared templates (`loans/_payoff_form.html` and
  `loans/_payoff_results.html`) instead of mortgage-specific copies. The old
  `mortgage/_payoff_results.html` is retained until Commit #13 cleanup.
- Mortgage payoff slider max changes from hardcoded $2,000 to auto-scaled
  `2 * monthly_payment`.

---

### Commit #10: Income-Relative Savings Goals -- Schema and Model Changes (Task 5.4, Part 1)

**Commit message:** `feat(savings): add income-relative goal mode columns to savings_goals`

**A. Problem statement**

Savings goals currently accept only a fixed dollar amount. For goals like "3 months of
salary in the emergency fund," the user must manually calculate the target and re-calculate
it after every raise. An income-relative mode should auto-calculate the target from net pay.

**B. Files modified**

1. `app/models/savings_goal.py` -- Add `goal_mode`, `income_unit`, `income_multiplier` columns.
2. `app/schemas/validation.py` -- Update savings goal schemas with new fields.

**C. Migration**

Run `flask db migrate -m "add income-relative goal mode columns to savings_goals"`.

New columns on `budget.savings_goals`:

| Column | Type | Default | Nullable | Constraints | Notes |
| --- | --- | --- | --- | --- | --- |
| `goal_mode` | String(20) | `'fixed'` | NOT NULL | CHECK `goal_mode IN ('fixed', 'income_relative')` | Determines how target_amount is used |
| `income_unit` | String(20) | NULL | Yes | CHECK `income_unit IS NULL OR income_unit IN ('paychecks', 'months')` | Unit for income-relative mode |
| `income_multiplier` | Numeric(6,2) | NULL | Yes | CHECK `income_multiplier IS NULL OR income_multiplier > 0` | How many units |

Data migration: All existing rows get `goal_mode='fixed'`, `income_unit=NULL`,
`income_multiplier=NULL`. This is handled by the column defaults.

**Existing constraint change:** The `target_amount` column currently has `NOT NULL` and
`CHECK target_amount > 0`. For income-relative goals, `target_amount` will be calculated
on read and stored as a cached value. The column remains NOT NULL (the calculated value is
written back on each read/display). No constraint change needed -- the service layer always
computes a positive target_amount before writing it.

**D. Model changes**

In `app/models/savings_goal.py`, add after existing columns:

```python
goal_mode = db.Column(db.String(20), nullable=False, server_default="fixed")
income_unit = db.Column(db.String(20))
income_multiplier = db.Column(db.Numeric(6, 2))
```

Add check constraints to `__table_args__`:

```python
db.CheckConstraint(
    "goal_mode IN ('fixed', 'income_relative')",
    name="ck_savings_goals_valid_mode",
),
db.CheckConstraint(
    "income_unit IS NULL OR income_unit IN ('paychecks', 'months')",
    name="ck_savings_goals_valid_unit",
),
db.CheckConstraint(
    "income_multiplier IS NULL OR income_multiplier > 0",
    name="ck_savings_goals_positive_multiplier",
),
```

**E. Schema changes**

In `app/schemas/validation.py`, update both savings goal schemas:

**SavingsGoalCreateSchema** -- add fields:
```python
goal_mode = fields.String(
    load_default="fixed",
    validate=validate.OneOf(["fixed", "income_relative"]),
)
income_unit = fields.String(
    allow_none=True,
    validate=validate.OneOf(["paychecks", "months"]),
)
income_multiplier = fields.Decimal(
    places=2, as_string=True, allow_none=True,
    validate=validate.Range(min=0, min_inclusive=False),
)
```

Add cross-field validation:

```python
@validates_schema
def validate_income_relative(self, data, **kwargs):
    """Ensure income_unit and multiplier are set when mode is income_relative."""
    if data.get("goal_mode") == "income_relative":
        if not data.get("income_unit"):
            raise ValidationError(
                "Income unit is required for income-relative goals.",
                field_name="income_unit",
            )
        if not data.get("income_multiplier"):
            raise ValidationError(
                "Multiplier is required for income-relative goals.",
                field_name="income_multiplier",
            )
```

When `goal_mode='fixed'`, `target_amount` remains required. When `goal_mode='income_relative'`,
`target_amount` is still required (the route calculates and provides it before saving).

**SavingsGoalUpdateSchema** -- add same fields (all optional).

**F. Tests**

| Test Name | File | Scenario | Expected |
| --- | --- | --- | --- |
| `test_create_fixed_goal_default_mode` | `test_routes/test_savings.py` | POST create without goal_mode | Goal created with goal_mode='fixed' |
| `test_create_income_relative_without_unit` | `test_routes/test_savings.py` | POST with goal_mode=income_relative, no unit | Validation error |
| `test_create_income_relative_without_multiplier` | `test_routes/test_savings.py` | POST with mode+unit but no multiplier | Validation error |
| `test_model_check_constraint_invalid_mode` | `test_models/test_savings_goal.py` | Insert with goal_mode='invalid' | IntegrityError |
| `test_model_check_constraint_invalid_unit` | `test_models/test_savings_goal.py` | Insert with income_unit='weekly' | IntegrityError |
| `test_model_negative_multiplier` | `test_models/test_savings_goal.py` | Insert with income_multiplier=-1 | IntegrityError |

**G. Verification**

- `flask db upgrade` succeeds
- Existing savings goals still load on the dashboard (backward compatible)
- `pylint app/models/savings_goal.py` passes
- `pylint app/schemas/validation.py` passes

---

### Commit #11: Income-Relative Savings Goals -- Service and Route Logic (Task 5.4, Part 2)

**Commit message:** `feat(savings): calculate income-relative goal targets from net pay`

**A. Problem statement**

The savings goal service and route need to resolve income-relative goals to a dollar amount
based on the user's net pay from the paycheck calculator.

**B. Files modified**

1. `app/services/savings_goal_service.py` -- Add `resolve_income_relative_target()`.
2. `app/routes/savings.py` -- Call the resolver on create, update, and dashboard display.

**C. Implementation -- Service**

Add a new pure function to `savings_goal_service.py`:

```python
def resolve_income_relative_target(net_biweekly_pay, income_unit, income_multiplier):
    """Calculate the dollar target for an income-relative savings goal.

    Args:
        net_biweekly_pay:  Decimal -- projected net pay per biweekly paycheck.
        income_unit:       str -- 'paychecks' or 'months'.
        income_multiplier: Decimal -- how many units (e.g. 3 for 3 months).

    Returns:
        Decimal -- resolved dollar target, or Decimal("0.00") if inputs are
        invalid (zero/negative net pay, unknown unit).
    """
    if net_biweekly_pay is None or Decimal(str(net_biweekly_pay)) <= 0:
        return Decimal("0.00")
    if income_multiplier is None or Decimal(str(income_multiplier)) <= 0:
        return Decimal("0.00")

    net = Decimal(str(net_biweekly_pay))
    multiplier = Decimal(str(income_multiplier))

    if income_unit == "paychecks":
        # target = multiplier * net_biweekly_pay
        return (multiplier * net).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    elif income_unit == "months":
        # target = multiplier * (net_biweekly * 26 / 12)
        monthly_net = net * Decimal("26") / Decimal("12")
        return (multiplier * monthly_net).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        return Decimal("0.00")
```

**C2. Implementation -- Route changes for create_goal and update_goal**

In `create_goal()`:

After schema validation, if `goal_mode == 'income_relative'`:
1. Load the active salary profile.
2. Compute current net biweekly pay by calling `paycheck_calculator.calculate_paycheck()`
   for the current period.
3. Call `resolve_income_relative_target()` to get the dollar amount.
4. Set `data['target_amount']` to the resolved value.
5. Proceed with goal creation as before.

```python
if data.get("goal_mode") == "income_relative":
    profile = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=current_user.id, is_active=True)
        .first()
    )
    if not profile:
        flash("No active salary profile found. Income-relative goals "
              "require a salary profile.", "danger")
        return redirect(url_for("savings.new_goal"))

    current_period = pay_period_service.get_current_period(current_user.id)
    all_periods = pay_period_service.get_all_periods(current_user.id)
    tax_configs = _load_tax_configs(current_user.id, current_period)
    calibration = _load_calibration(profile)

    breakdown = paycheck_calculator.calculate_paycheck(
        profile, current_period, all_periods, tax_configs,
        calibration=calibration,
    )
    net_biweekly = breakdown.net_pay

    resolved_target = savings_goal_service.resolve_income_relative_target(
        net_biweekly,
        data["income_unit"],
        Decimal(str(data["income_multiplier"])),
    )
    data["target_amount"] = resolved_target
```

The `_load_tax_configs` and `_load_calibration` helpers are local functions in the route
file that query the tax configuration and calibration override for the paycheck calculator.
These follow the same pattern used in the recurrence engine (query tax configs by year,
load calibration if active).

In `update_goal()`:

If the goal's `goal_mode` is being changed to `income_relative`, or if it's already
`income_relative` and `income_unit` or `income_multiplier` is being updated, recompute
the target amount using the same logic.

**C3. Implementation -- Dashboard display**

In the savings dashboard, when rendering income-relative goals, the resolved target should
be recalculated on every page load to reflect current net pay (which changes when raises
take effect or calibration is updated):

```python
# In the goal_data loop:
for goal in goals:
    if goal.goal_mode == "income_relative" and net_biweekly_pay:
        resolved_target = savings_goal_service.resolve_income_relative_target(
            net_biweekly_pay,
            goal.income_unit,
            goal.income_multiplier,
        )
        # Update the stored target if it has changed.
        if resolved_target != goal.target_amount and resolved_target > 0:
            goal.target_amount = resolved_target
            db.session.commit()
    ...
```

To get `net_biweekly_pay` for the dashboard, compute it once at the top of the dashboard
function using the active salary profile and current period (similar to how
`salary_gross_biweekly` is already computed at line 152-162).

**D. Edge cases**

- **No active salary profile:** Income-relative goals cannot be resolved. Display the last
  stored `target_amount` with a warning badge: "No salary profile -- target may be outdated."
- **Net pay is zero or negative:** `resolve_income_relative_target()` returns `Decimal("0.00")`.
  Do not update `target_amount` to zero -- keep the last valid value. The service returns
  `0.00` as a signal; the caller checks `> 0` before updating.
- **Raise takes effect mid-projection:** The paycheck calculator already handles raises
  chronologically. `calculate_paycheck()` for the current period returns the current net pay
  INCLUDING any raises that have taken effect. Future raises that haven't taken effect yet
  are NOT included -- the goal reflects current income, not future. This is the correct
  behavior per the roadmap: "Use the current projected net pay from the paycheck calculator
  (what the next paycheck would be)."
- **Multiple salary profiles:** Only the active profile (`is_active=True`) is used. If
  multiple active profiles exist, use the first one (ordered by sort_order). This matches
  existing behavior throughout the codebase.
- **Goal multiplier of 0.5:** Valid -- e.g., "half a month's salary." The check constraint
  allows any positive value.
- **Decimal precision:** The `income_multiplier` column is `Numeric(6,2)`, supporting values
  up to 9999.99. The resolved target is quantized to 2 decimal places.

**E. Tests**

| Test Name | File | Scenario | Expected |
| --- | --- | --- | --- |
| `test_resolve_target_paychecks` | `test_services/test_savings_goal_service.py` | net=$2000, unit=paychecks, mult=3 | $6,000.00 |
| `test_resolve_target_months` | `test_services/test_savings_goal_service.py` | net=$2000, unit=months, mult=3 | $13,000.00 (2000*26/12*3) |
| `test_resolve_target_zero_pay` | `test_services/test_savings_goal_service.py` | net=$0, unit=months, mult=3 | $0.00 |
| `test_resolve_target_none_pay` | `test_services/test_savings_goal_service.py` | net=None, unit=months, mult=3 | $0.00 |
| `test_resolve_target_invalid_unit` | `test_services/test_savings_goal_service.py` | net=$2000, unit=invalid, mult=3 | $0.00 |
| `test_resolve_target_fractional_multiplier` | `test_services/test_savings_goal_service.py` | net=$2000, unit=paychecks, mult=0.5 | $1,000.00 |
| `test_create_income_relative_goal` | `test_routes/test_savings.py` | POST with mode=income_relative, unit=months, mult=3 | Goal created with calculated target_amount |
| `test_create_income_relative_no_profile` | `test_routes/test_savings.py` | POST income_relative without salary profile | Flash error, redirect |
| `test_dashboard_recalculates_target` | `test_routes/test_savings.py` | Change salary, GET dashboard | target_amount updated to reflect new pay |
| `test_dashboard_no_profile_shows_warning` | `test_routes/test_savings.py` | Income-relative goal, no salary profile | Dashboard renders with stale target, warning badge |

**F. Verification**

1. Create an income-relative savings goal (mode=income_relative, unit=months, multiplier=3).
2. Verify target amount is calculated correctly based on current net pay.
3. Add a raise to the salary profile that has already taken effect.
4. Refresh the savings dashboard -- verify the goal's target amount updates.
5. Delete the salary profile -- verify the goal still displays (with stale target + warning).

---

### Commit #12: Income-Relative Savings Goals -- Form UI (Task 5.4, Part 3)

**Commit message:** `feat(savings): add income-relative mode toggle to savings goal form`

**A. Problem statement**

The savings goal form needs UI controls for selecting the goal mode and entering
income-relative parameters.

**B. Files modified**

1. `app/templates/savings/goal_form.html` -- Add mode toggle, income fields.
2. `app/templates/savings/dashboard.html` -- Display income-relative goal details.

**C. Implementation -- Goal form**

Add a mode toggle to the form. When "Based on income" is selected, show income unit and
multiplier fields; hide the manual target amount field (it will be auto-calculated).

```html
{# Goal Mode Toggle #}
<div class="mb-3">
  <label class="form-label">Goal Type</label>
  <div class="btn-group w-100" role="group">
    <input type="radio" class="btn-check" name="goal_mode" id="mode-fixed"
           value="fixed" {{ 'checked' if not goal or goal.goal_mode == 'fixed' }}
           onchange="toggleGoalMode()">
    <label class="btn btn-outline-primary" for="mode-fixed">Fixed Amount</label>

    <input type="radio" class="btn-check" name="goal_mode" id="mode-income"
           value="income_relative" {{ 'checked' if goal and goal.goal_mode == 'income_relative' }}
           onchange="toggleGoalMode()">
    <label class="btn btn-outline-primary" for="mode-income">Based on Income</label>
  </div>
</div>

{# Fixed Amount Fields (shown when mode=fixed) #}
<div id="fixed-fields">
  <div class="mb-3">
    <label for="target_amount" class="form-label">Target Amount</label>
    <div class="input-group">
      <span class="input-group-text">$</span>
      <input type="number" class="form-control" id="target_amount"
             name="target_amount" step="0.01" min="0.01"
             value="{{ goal.target_amount if goal else '' }}"
             {{ 'required' if not goal or goal.goal_mode == 'fixed' }}>
    </div>
  </div>
</div>

{# Income-Relative Fields (shown when mode=income_relative) #}
<div id="income-fields" style="display: none;">
  <div class="row mb-3">
    <div class="col-6">
      <label for="income_unit" class="form-label">Unit</label>
      <select class="form-select" id="income_unit" name="income_unit">
        <option value="">Select...</option>
        <option value="paychecks" {{ 'selected' if goal and goal.income_unit == 'paychecks' }}>
          Paychecks
        </option>
        <option value="months" {{ 'selected' if goal and goal.income_unit == 'months' }}>
          Months
        </option>
      </select>
    </div>
    <div class="col-6">
      <label for="income_multiplier" class="form-label">How Many</label>
      <input type="number" class="form-control" id="income_multiplier"
             name="income_multiplier" step="0.01" min="0.01"
             value="{{ goal.income_multiplier if goal and goal.income_multiplier else '' }}">
    </div>
  </div>
  {% if resolved_target %}
  <div class="alert alert-info small">
    <strong>Calculated target:</strong>
    ${{ "{:,.2f}".format(resolved_target|float) }}
    <span class="text-muted">(based on current net pay)</span>
  </div>
  {% endif %}
</div>
```

JavaScript toggle function (inline or in a script block):

```javascript
function toggleGoalMode() {
  const isIncome = document.getElementById('mode-income').checked;
  document.getElementById('fixed-fields').style.display = isIncome ? 'none' : '';
  document.getElementById('income-fields').style.display = isIncome ? '' : 'none';

  // Toggle required attributes.
  const targetInput = document.getElementById('target_amount');
  targetInput.required = !isIncome;
}
// Initialize on page load.
document.addEventListener('DOMContentLoaded', toggleGoalMode);
```

When `mode=income_relative`, the `target_amount` field is hidden but the route still
calculates and submits it as a hidden field (or the route computes it server-side from the
unit and multiplier). Since the route already handles the calculation in Commit #11, the
form simply omits `target_amount` for income-relative mode and the route fills it in.

**C2. Implementation -- Dashboard display**

In `savings/dashboard.html`, where each goal is displayed, add income-relative context:

```html
{% if goal.goal.goal_mode == 'income_relative' %}
<div class="small text-muted mt-1">
  <i class="bi bi-arrow-repeat"></i>
  {{ goal.goal.income_multiplier }}
  {{ 'paycheck' if goal.goal.income_unit == 'paychecks' else 'month' }}{{ 's' if goal.goal.income_multiplier != 1 }}
  of net pay
  {% if not has_salary_profile %}
  <span class="badge bg-warning text-dark">No salary profile</span>
  {% endif %}
</div>
{% endif %}
```

Pass `has_salary_profile` (boolean) to the template from the dashboard route.

**C3. Route changes for edit_goal**

In `edit_goal()`, if the goal is income-relative, compute and pass `resolved_target` to the
template so it can display the current calculated value:

```python
resolved_target = None
if goal.goal_mode == "income_relative" and net_biweekly_pay:
    resolved_target = savings_goal_service.resolve_income_relative_target(
        net_biweekly_pay, goal.income_unit, goal.income_multiplier,
    )
return render_template(
    "savings/goal_form.html",
    goal=goal, accounts=accounts, resolved_target=resolved_target,
)
```

**D. Edge cases**

- **Switching from income-relative to fixed:** When the user switches the toggle to "Fixed,"
  the target_amount field becomes visible and required. The last calculated target is pre-
  populated (from `goal.target_amount`), so the user doesn't lose their reference point.
- **Switching from fixed to income-relative:** The target_amount field is hidden. The route
  calculates the new target on submit.
- **JavaScript disabled:** Without JS, both field sets are visible. The server-side
  validation enforces correctness regardless of client-side toggle state.

**E. Tests**

| Test Name | File | Scenario | Expected |
| --- | --- | --- | --- |
| `test_goal_form_shows_mode_toggle` | `test_routes/test_savings.py` | GET new goal form | Contains "Fixed Amount" and "Based on Income" |
| `test_edit_form_shows_income_fields` | `test_routes/test_savings.py` | GET edit for income-relative goal | Contains income_unit select, income_multiplier input |
| `test_edit_form_shows_resolved_target` | `test_routes/test_savings.py` | GET edit for income-relative goal with salary | Contains "Calculated target" |
| `test_update_fixed_to_income_relative` | `test_routes/test_savings.py` | POST update changing mode to income_relative | target_amount recalculated |
| `test_update_income_relative_to_fixed` | `test_routes/test_savings.py` | POST update changing mode to fixed with target | target_amount from form used |
| `test_dashboard_shows_income_relative_label` | `test_routes/test_savings.py` | GET dashboard with income-relative goal | Contains "months of net pay" or "paychecks of net pay" |

**F. Verification**

1. Navigate to savings goal form.
2. Toggle to "Based on Income" -- verify fixed amount field hides, income fields appear.
3. Select "Months," enter "3," submit -- verify goal created with correct calculated target.
4. View savings dashboard -- verify goal shows "3 months of net pay" label.
5. Edit the goal, change multiplier to "6," submit -- verify target updates.
6. Toggle back to "Fixed Amount" -- verify target amount field appears with last calculated value.

---

### Commit #13: Final Verification and Cleanup

**Commit message:** `chore(section5): final cleanup and verification pass`

**A. Description**

Final commit after all Section 5 work is complete. Runs full test suite, fixes any pylint
issues, verifies all features end-to-end.

**B. Files modified**

1. `app/templates/mortgage/_payoff_results.html` -- **Delete.** The mortgage payoff route
   was updated in Commit #9 to render `loans/_payoff_results.html`. This file is now dead
   code. Verify no other template includes it before deletion.

Any other files with pylint issues or minor cleanup needs discovered during final
verification.

**C. Verification checklist**

- [ ] Full test suite passes: `timeout 660 pytest -v --tb=short`
- [ ] All four debt account types have dashboards with summary, chart, and payoff calculators
- [ ] All four payoff calculators use the shared `loans/_payoff_form.html` and `loans/_payoff_results.html`
- [ ] `mortgage/_payoff_results.html` has been deleted (dead code after shared template refactor)
- [ ] Payoff slider auto-scales to `2 * monthly_payment` on all four debt dashboards
- [ ] Student loan and personal loan accounts can be created, configured, and viewed
- [ ] Post-parameter-save prompt offers to create a recurring transfer for all four debt types
- [ ] Extra payments to any debt account update the amortization schedule on the dashboard
- [ ] Payoff calculator on all four types incorporates actual payment history
- [ ] Income-relative savings goals calculate correctly (paychecks and months modes)
- [ ] Income-relative goals update when salary changes (raise takes effect)
- [ ] Income-relative goals display correctly on the dashboard with mode label
- [ ] No regressions in the grid, balance calculator, charts, or accounts dashboard
- [ ] Savings dashboard shows all debt account balances and projections correctly
- [ ] pylint passes with no score decrease: `pylint app/ --fail-on=E,F`
- [ ] All new code has docstrings and inline comments on non-obvious logic
- [ ] All new models, routes, and services use Decimal (never float) for money
- [ ] All new routes have ownership checks (user_id verification)
- [ ] All new database queries are user-scoped

---

## 6. Edge Case Analysis

### 6.1 Amortization Edge Cases

| Scenario | Handling | Commit |
| --- | --- | --- |
| Zero remaining principal | Engine returns empty schedule, $0 payment. Dashboard shows "Paid off." | #7 |
| Zero interest rate | Engine uses simple division (principal/months). No division by zero. | #7, existing |
| Extra payment exceeds remaining principal | Schedule terminates; final month absorbs remainder. | #7, existing |
| No transfers to debt account (no payment history) | `payment_history=[]`, engine uses static projection. | #8 |
| Historical payment less than monthly interest | Principal portion clamped to $0. Balance unchanged for that month. | #7 |
| Historical payment exactly equals standard amount | Schedule identical to no-history schedule. | #7 |
| Loan term is zero months | Engine returns empty schedule. | Existing |
| Original principal is zero | Engine returns $0 payment, empty schedule. | Existing |
| Target payoff date in the past | `calculate_payoff_by_date` returns None. UI shows warning. | #9 |
| Target payoff date requires unaffordable payment | Engine returns the amount anyway. No affordability check. | #9 |
| Multiple transfers to debt account in same pay period | `get_payment_history` returns each separately; engine sums by (year, month). | #7, #8 |
| Cancelled transfer to debt account | Excluded: `effective_amount` returns 0 for `excludes_from_balance=True` statuses. | #8 |
| Rounding residue at end of schedule | Guard: `if balance < 0: extra += balance; balance = 0`. | Existing |

### 6.2 Income-Relative Goal Edge Cases

| Scenario | Handling | Commit |
| --- | --- | --- |
| No active salary profile | Goal creation blocked with flash error. Dashboard shows stale target + warning badge. | #11, #12 |
| Net pay is zero (very high deductions) | `resolve_income_relative_target` returns $0. Route does NOT update target_amount to zero. | #11 |
| Net pay is negative | Treated same as zero. Return $0, do not update. | #11 |
| Raise scheduled but not yet effective | Paycheck calculator for current period does NOT apply future raises. Goal reflects current income. | #11 |
| Multiple active salary profiles | First by sort_order is used. Matches existing convention. | #11 |
| Goal multiplier of 0.5 (half a paycheck) | Valid. Numeric(6,2) allows it. Result: 0.5 * net_biweekly. | #11 |
| Goal multiplier of 100 (100 months) | Valid. Result: 100 * monthly_net. Unusual but not invalid. | #11 |
| Switching from income-relative to fixed | Last calculated target pre-populates the fixed amount field. | #12 |
| Switching from fixed to income-relative | Route calculates new target from current net pay. | #12 |
| Currency displayed incorrectly on dashboard | Template uses `"{:,.2f}".format()` pattern. Same as all other monetary displays. | #12 |

### 6.3 Student/Personal Loan Edge Cases

| Scenario | Handling | Commit |
| --- | --- | --- |
| Account created but params not yet saved | Dashboard redirects to setup page. `needs_setup` badge on savings dashboard. | #3, #5 |
| Params saved then account deactivated | Params remain in DB (CASCADE only fires on account delete, not deactivate). No special handling needed. | N/A |
| Student loan with very long term (300 months) | Schema allows up to 600 months. Amortization engine handles any positive term. | #2 |
| Personal loan term over 120 months rejected | Schema validation: max=120 for PersonalLoanParamsCreateSchema. | #2 |

---

## 7. Downstream Impact Analysis

### 7.1 Amortization Engine Signature Change (Commit #7)

**Callers of `generate_schedule()`:**
- `app/routes/mortgage.py:dashboard()` (line 144) -- updated in Commit #8
- `app/routes/mortgage.py:payoff_calculate()` (lines 413, 419) -- updated in Commit #8
- `app/routes/auto_loan.py:dashboard()` (line 87) -- updated in Commit #8
- `app/services/balance_calculator.py` -- does NOT call `generate_schedule()` (it does its
  own principal tracking). No change needed.
- `tests/test_services/test_amortization_engine.py` -- existing tests unaffected (default
  `payment_history=None`). New tests added in Commit #7.

**Callers of `calculate_summary()`:**
- `app/routes/mortgage.py:dashboard()` (line 112) -- updated in Commit #8
- `app/routes/mortgage.py:payoff_calculate()` (line 402) -- updated in Commit #8
- `app/routes/mortgage.py:_compute_total_payment()` (line 81) -- NOT updated (escrow
  recalculation doesn't need payment history; uses current params only)
- `app/routes/auto_loan.py:dashboard()` (line 77) -- updated in Commit #8
- `app/routes/savings.py:dashboard()` (line 244) -- updated in Commit #8
- Tests -- existing tests unaffected (default parameter).

**Callers of `calculate_payoff_by_date()`:**
- `app/routes/mortgage.py:payoff_calculate()` (line 447) -- updated in Commit #8

All callers are backward compatible because `payment_history` defaults to `None`.

### 7.2 Savings Goal Model Changes (Commit #10)

**Downstream of adding columns to `budget.savings_goals`:**
- `app/routes/savings.py:create_goal()` -- updated in Commit #11
- `app/routes/savings.py:update_goal()` -- updated in Commit #11
- `app/routes/savings.py:dashboard()` -- updated in Commit #11 (recalculation on read)
- `app/templates/savings/goal_form.html` -- updated in Commit #12
- `app/templates/savings/dashboard.html` -- updated in Commit #12
- `app/schemas/validation.py` -- updated in Commit #10
- `tests/test_routes/test_savings.py` -- new tests in Commits #10-#12
- `tests/test_services/test_savings_goal_service.py` -- new tests in Commit #11

No other files reference the `SavingsGoal` model.

### 7.3 New Blueprint Registration (Commit #3)

**Impact:** Two new blueprints (`student_loan_bp`, `personal_loan_bp`) are registered in
`create_app()`. No URL conflicts -- new routes use `/accounts/<id>/student-loan/...` and
`/accounts/<id>/personal-loan/...` prefixes that don't overlap with existing routes.

### 7.4 Transfer Form Prefill (Commit #6)

**Impact on existing transfer creation flow:** The transfer form template gains conditional
rendering based on `setup_prompt` and `prefill_*` query parameters. When these are absent
(normal transfer creation), behavior is unchanged. The only change is additional Jinja
conditionals that evaluate to false.

### 7.5 Savings Dashboard Loan Loading (Commit #5)

**Impact:** The savings dashboard loads student loan and personal loan params alongside
mortgage and auto loan params. This adds two additional queries (conditional on accounts of
those types existing). No performance concern -- queries are filtered by account ID with
`IN` clauses.

### 7.6 Balance Calculator -- No Changes Needed

The balance calculator's `calculate_balances_with_amortization()` already handles any debt
account with a `loan_params` object that has `.interest_rate`, `.term_months`,
`.origination_date`, and `.payment_day`. Both `StudentLoanParams` and `PersonalLoanParams`
have these fields. No code changes needed in the balance calculator.

### 7.7 Charts -- No Changes Needed

The balance over time chart on each debt dashboard is rendered using Chart.js data attributes
generated by `_build_chart_data()` in the route file. New dashboards (student loan, personal
loan) include their own chart rendering following the same pattern. The main Charts page
(`app/routes/charts.py`) renders balance history from `calculate_balances()` /
`calculate_balances_with_amortization()` -- it will automatically include student/personal
loan accounts once the balance calculator receives their loan_params via the savings
dashboard loading.

### 7.8 Grid -- No Changes Needed

The budget grid operates on transactions, not on accounts or loan params. Shadow transactions
from transfers to debt accounts already appear in the grid. No grid changes are needed for
any Section 5 work.

---

## 8. Migration Specifications

### 8.1 Migration: Student Loan and Personal Loan Params Tables (Commit #1)

**Command:** `flask db migrate -m "add student_loan_params and personal_loan_params tables"`

**Table 1: `budget.student_loan_params`**

| Column | Type | Nullable | Default | FK | Constraint |
| --- | --- | --- | --- | --- | --- |
| `id` | INTEGER | NOT NULL | autoincrement | | PK |
| `account_id` | INTEGER | NOT NULL | | `budget.accounts.id` CASCADE | UNIQUE |
| `original_principal` | NUMERIC(12,2) | NOT NULL | | | |
| `current_principal` | NUMERIC(12,2) | NOT NULL | | | |
| `interest_rate` | NUMERIC(7,5) | NOT NULL | | | |
| `term_months` | INTEGER | NOT NULL | | | |
| `origination_date` | DATE | NOT NULL | | | |
| `payment_day` | INTEGER | NOT NULL | | | CHECK (1 <= val <= 31) |
| `created_at` | TIMESTAMPTZ | | `now()` | | |
| `updated_at` | TIMESTAMPTZ | | `now()` | | |

**Table 2: `budget.personal_loan_params`** -- Identical structure.

**No data migration.** Tables are created empty.

### 8.2 Migration: Income-Relative Goal Columns (Commit #10)

**Command:** `flask db migrate -m "add income-relative goal mode columns to savings_goals"`

**Table: `budget.savings_goals` -- add columns:**

| Column | Type | Nullable | Default | Constraint |
| --- | --- | --- | --- | --- |
| `goal_mode` | VARCHAR(20) | NOT NULL | `'fixed'` | CHECK `goal_mode IN ('fixed', 'income_relative')` |
| `income_unit` | VARCHAR(20) | YES | NULL | CHECK `income_unit IS NULL OR income_unit IN ('paychecks', 'months')` |
| `income_multiplier` | NUMERIC(6,2) | YES | NULL | CHECK `income_multiplier IS NULL OR income_multiplier > 0` |

**Data migration:** All existing rows default to `goal_mode='fixed'` via the server_default.
No explicit UPDATE needed.

---

## 9. Test Specifications Summary

### 9.1 New Test Files

| File | Commit | Test Count | Focus |
| --- | --- | --- | --- |
| `tests/test_routes/test_student_loan.py` | #4, #6, #8, #9 | ~16 | Route CRUD, ownership, payoff |
| `tests/test_routes/test_personal_loan.py` | #4, #6, #8, #9 | ~16 | Route CRUD, ownership, payoff |
| `tests/test_services/test_loan_helpers.py` | #8 | 4 | Payment history query |

### 9.2 Modified Test Files

| File | Commit | New Tests | Focus |
| --- | --- | --- | --- |
| `tests/test_services/test_amortization_engine.py` | #7 | 11 | Payment history schedule generation |
| `tests/test_routes/test_mortgage.py` | #6, #8 | 4 | Payment prompt redirect, history integration |
| `tests/test_routes/test_auto_loan.py` | #6, #8, #9 | 6 | Payment prompt, history, payoff calculator |
| `tests/test_routes/test_savings.py` | #5, #10, #11, #12 | ~14 | Loan dashboard integration, income-relative goals |
| `tests/test_routes/test_transfers.py` | #6 | 2 | Prefill parameters, setup banner |
| `tests/test_routes/test_accounts.py` | #5 | 2 | Creation redirect for new loan types |
| `tests/test_services/test_savings_goal_service.py` | #11 | 6 | `resolve_income_relative_target` |
| `tests/test_models/test_savings_goal.py` | #10 | 3 | Check constraints for new columns |

### 9.3 Fixtures

All tests use existing fixtures from `conftest.py`:
- `seed_user`, `seed_second_user` -- user setup
- `auth_client`, `second_auth_client` -- authenticated test clients
- `seed_full_user_data` -- when pay periods, scenarios, and accounts are needed

No new fixtures are required. Tests create loan accounts and params inline using the seeded
reference data.

---

## 10. Resolved Design Decisions

These questions were evaluated during planning. Decisions are reflected in the commit
specifications above.

1. **Student loan model:** Basic installment loan model (matching auto loan). No
   `is_subsidized` column. The amortization engine already handles zero-rate loans, which
   covers the subsidized case during deferment (user would temporarily set rate to 0).
   Subsidized/unsubsidized can be added as a future enhancement if needed.

2. **Payoff calculator slider max:** Auto-scale to `2 * monthly_payment`, rounded up to
   nearest $25, with a floor of $100. Applied to all four debt types including mortgage
   (replacing its previous hardcoded $2,000 max). See `_slider_max()` helper in Commit #9.

3. **Mortgage payoff template refactor:** Refactor in Commit #9. The mortgage dashboard and
   payoff route are updated to use the shared `loans/_payoff_form.html` and
   `loans/_payoff_results.html` templates. The old `mortgage/_payoff_results.html` is
   retained as a safety net during Commit #9 and deleted in Commit #13 after verification.
