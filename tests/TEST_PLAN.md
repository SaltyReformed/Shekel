# Shekel Budget App -- Comprehensive Test Plan

## Testing Standards

- **Framework:** pytest with fixtures from `tests/conftest.py`
- **All tests need docstrings** explaining what is verified and why
- **Inline comments** on non-obvious assertions or setup steps
- **Use `Decimal`, never `float`**, for all monetary amounts
- **Conform to Pylint standards** (snake_case, docstrings, no unused imports)
- **Mirror directory structure:** `tests/test_services/`, `tests/test_routes/`,
  `tests/test_models/`, `tests/test_schemas/`
- **Run pytest after writing tests;** fix all failures before reporting done
- **Fixtures:** Reuse `seed_user`, `seed_periods`, `auth_client` from conftest. Create new fixtures
  in conftest or local helpers for salary, transfers, etc.

---

## Current Coverage Snapshot

| File                                         | Tests   | Notes                                             |
| -------------------------------------------- | ------- | ------------------------------------------------- |
| `test_routes/test_auth.py`                   | 7       | Login/logout, disabled acct, rate limit; complete |
| `test_routes/test_grid.py`                   | 19      | Grid view, balance row, txn CRUD+SM               |
| `test_routes/test_transaction_auth.py`       | 13      | IDOR on transactions; thorough                    |
| `test_routes/test_accounts.py`               | 29      | CRUD, anchor, types; complete                     |
| `test_routes/test_salary.py`                 | 36      | Profiles, raises, deductions, tax                 |
| `test_routes/test_transfers.py`              | 28      | Templates, grid, instances; complete              |
| `test_routes/test_savings.py`                | 19      | Dashboard, goals CRUD; complete                   |
| `test_routes/test_templates.py`              | 24      | CRUD, recurrence preview; complete                |
| `test_routes/test_categories.py`             | 11      | CRUD, HTMX, in-use checks; complete               |
| `test_routes/test_pay_periods.py`            | 6       | Generate form + validation; complete              |
| `test_routes/test_settings.py`               | 7       | Show, update, validation; complete                |
| `test_services/test_auth_service.py`         | 7       | Hash, verify, authenticate; complete              |
| `test_services/test_balance_calculator.py`   | 14      | Edge cases + transfers; complete                  |
| `test_services/test_credit_workflow.py`      | 15      | Credit + carry-forward; complete                  |
| `test_services/test_pay_period_service.py`   | 17      | Generate, current, range, next; complete          |
| `test_services/test_paycheck_calculator.py`  | 47      | Full pipeline + deductions; complete              |
| `test_services/test_recurrence_engine.py`    | 28      | All patterns + regen; complete                    |
| `test_services/test_savings_goal_service.py` | 14      | Contributions, metrics, periods; complete         |
| `test_services/test_tax_calculator.py`       | 30      | Excellent coverage                                |
| `test_services/test_transfer_recurrence.py`  | 10      | Generate, regen, conflicts; complete              |
| `test_models/test_computed_properties.py`    | 13      | effective_amount, is_income/expense, label; complete |
| `test_schemas/test_validation.py`            | 51      | All 16 schemas; complete                          |
| `test_integration/test_workflows.py`         | 6       | End-to-end cross-service workflows; complete      |
| `test_integration/test_idempotency.py`       | 4       | Double-submit: login, templates, raises, deductions |
| `test_audit_fixes.py`                        | 15      | Decimal, IDOR, constraints                        |
| **Total**                                    | **470** |                                                   |

---

## Priority Matrix

Tests are prioritized by risk severity from the adversarial audit.

| Priority          | Criteria                                    | Modules                                                                                          |
| ----------------- | ------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| **P0 -- Critical** | Financial correctness bugs, data corruption | Paycheck calculator pipeline, balance calculator edge cases, recurrence engine advanced patterns |
| **P1 -- High**     | Security gaps, IDOR, mass assignment        | Account routes, salary routes, transfer/savings happy paths, schema validation                   |
| **P2 -- Medium**   | Functional gaps in core workflows           | Template CRUD, category/pay-period routes, settings, carry-forward                               |
| **P3 -- Low**      | Model properties, idempotency, edge polish  | Computed properties, double-submit, rate limiting                                                |

---

## Module Inventory and Test Categories

### Legend

Each subsection marks the test categories needed:

- **HP** -- Happy path (expected inputs produce expected outputs)
- **SP** -- Sad path (malformed input, missing fields, wrong types)
- **IDOR** -- Authorization (User B cannot access User A's resources)
- **BE** -- Boundary/edge cases (zero amounts, max values, empty sets)
- **SM** -- State machine transitions (status workflows, recurrence states)
- **IDEM** -- Idempotency (double-submit on every POST route)
- **FIN** -- Financial calculation correctness

Existing coverage is marked with a checkmark. Gaps are flagged with **[AUDIT GAP]** when identified
in the adversarial audit.

---

## 1. Services

### 1.1 `services/tax_calculator.py` -- Priority P0

**Status: Well covered (30 tests).** No new tests needed.

Existing tests cover: zero-tax scenarios, positive tax scenarios, high income, bracket boundaries,
input validation, dependent credits, legacy wrapper, and marginal bracket application.

| Category | Covered | Notes                              |
| -------- | ------- | ---------------------------------- |
| HP       | Yes     | 7 positive-tax scenarios           |
| SP       | Yes     | 6 input validation tests           |
| BE       | Yes     | 4 bracket boundary tests           |
| FIN      | Yes     | All bracket/credit/deduction paths |

**Estimated new tests: 0**

---

### 1.2 `services/paycheck_calculator.py` -- Priority P0

**Status: Complete (47 tests in `test_services/test_paycheck_calculator.py`).** Full pipeline,
deductions, 3rd-paycheck detection, inflation, cumulative wages, and projections all covered.

#### `calculate_paycheck()` -- Full Pipeline

| Category | Tests Needed                                                                |
| -------- | --------------------------------------------------------------------------- |
| HP       | Basic paycheck: gross → pre-tax → taxes → post-tax → net pay                |
| HP       | Paycheck with no deductions (tax-only)                                      |
| HP       | Paycheck with pre-tax and post-tax deductions                               |
| FIN      | Verify `net_pay = gross - pre_tax - fed - state - ss - medicare - post_tax` |
| FIN      | Verify `taxable_income = gross - pre_tax` (floored at 0)                    |
| FIN      | State tax annualization and de-annualization                                |
| BE       | Zero annual salary                                                          |
| BE       | Missing tax configs (None bracket_set, None state_config, None fica_config) |
| BE       | `taxable_biweekly` floors at Decimal("0") when pre-tax > gross              |

#### `_calculate_deductions()` -- Deduction Logic

| Category | Tests Needed                                                   |
| -------- | -------------------------------------------------------------- |
| HP       | Flat pre-tax deduction applied correctly                       |
| HP       | Flat post-tax deduction applied correctly                      |
| HP       | Percentage-based deduction (calc_method="percentage")          |
| SM       | 24-per-year deduction skipped on 3rd paycheck                  |
| SM       | 12-per-year deduction applied only on first paycheck of month  |
| SM       | 12-per-year deduction skipped on non-first paycheck            |
| BE       | Inactive deduction skipped                                     |
| BE       | Deduction with annual_cap (not yet enforced -- verify behavior) |

#### `_is_third_paycheck()` -- 3rd Paycheck Detection

| Category | Tests Needed                                 |
| -------- | -------------------------------------------- |
| HP       | Month with exactly 2 paychecks → False       |
| HP       | Month with 3 paychecks → True for 3rd period |
| BE       | First period of month → False                |
| BE       | January 1st start date edge case             |

#### `_is_first_paycheck_of_month()`

| Category | Tests Needed                                 |
| -------- | -------------------------------------------- |
| HP       | First period starting in a month → True      |
| HP       | Second period starting in same month → False |

#### Inflation Adjustment

| Category | Tests Needed                                           |
| -------- | ------------------------------------------------------ |
| HP       | 1 year of inflation applied correctly                  |
| HP       | 2 years of inflation compounded                        |
| BE       | Period before inflation effective_month → years - 1    |
| BE       | `profile.created_at` is None → 0 years                 |
| BE       | Same year as creation → 0 years                        |
| FIN      | Compound formula: `amount * (1 + rate)^years` verified |

#### Cumulative Wages (`_get_cumulative_wages`)

| Category | Tests Needed                                      |
| -------- | ------------------------------------------------- |
| HP       | Sums gross pay for all prior periods in same year |
| BE       | First period of year → cumulative = 0             |
| BE       | Period in different year than prior periods       |
| FIN      | Cumulative correctly passed to FICA for SS cap    |

#### `project_salary()` -- Multi-Period Projection

| Category | Tests Needed                                         |
| -------- | ---------------------------------------------------- |
| HP       | Returns one breakdown per period                     |
| HP       | Raise events appear in correct period                |
| BE       | Empty periods list → empty result                    |
| FIN      | Cumulative wages accumulate correctly across periods |

**Estimated new tests: 30**

---

### 1.3 `services/recurrence_engine.py` -- Priority P0

**Status: Complete (28 tests in `test_services/test_recurrence_engine.py`).** All 8 patterns,
`generate_for_template()`, `regenerate_for_template()`, `resolve_conflicts()`, and salary-linked
amounts covered.

#### Pattern Matching -- `match_periods()`

| Category | Tests Needed                                                      |
| -------- | ----------------------------------------------------------------- |
| HP       | `monthly` -- generates in correct period for day 15                |
| BE       | `monthly` -- day 31 clamped to 28 in February                      |
| BE       | `monthly` -- day 30 clamped to 28 in February                      |
| HP       | `monthly_first` -- picks first period starting in each month       |
| HP       | `quarterly` -- generates in 4 correct months                       |
| BE       | `quarterly` -- start_month=11 wraps correctly (Nov, Feb, May, Aug) |
| HP       | `semi_annual` -- generates in 2 correct months                     |
| BE       | `semi_annual` -- start_month=8 wraps correctly (Aug, Feb)          |
| HP       | `annual` -- one match per calendar year                            |
| BE       | `annual` -- Feb 29 target in non-leap year                         |
| HP       | `once` -- returns empty list                                       |
| BE       | No periods match any pattern → empty result                       |

#### `generate_for_template()`

| Category | Tests Needed                                                                         |
| -------- | ------------------------------------------------------------------------------------ |
| SM       | Skips periods with immutable-status transactions (done, received, credit, cancelled) |
| SM       | Skips periods with `is_override=True` transactions                                   |
| SM       | Skips periods with `is_deleted=True` transactions                                    |
| HP       | Uses `effective_from` to skip earlier periods                                        |
| HP       | Salary-linked template uses `calculate_paycheck()` net_pay as amount                 |
| BE       | Salary linkage fallback: paycheck calc fails → uses `template.default_amount`        |

#### `regenerate_for_template()`

| Category | Tests Needed                                                |
| -------- | ----------------------------------------------------------- |
| HP       | Deletes unmodified auto-generated entries and recreates     |
| SM       | Raises `RecurrenceConflict` when overrides or deletes exist |
| SM       | Overridden entries preserved through regeneration           |
| SM       | Deleted entries preserved through regeneration              |

#### `resolve_conflicts()`

| Category | Tests Needed                                                 |
| -------- | ------------------------------------------------------------ |
| HP       | `action='keep'` -- no changes made                            |
| HP       | `action='update'` -- clears flags, applies new_amount         |
| BE       | `action='update'` with `new_amount=None` -- clears flags only |

**Estimated new tests: 22**

---

### 1.4 `services/transfer_recurrence.py` -- Priority P0

**Status: Complete (10 tests in `test_services/test_transfer_recurrence.py`).** Generate,
regenerate, resolve_conflicts, and edge cases covered.

| Category | Tests Needed                                                     |
| -------- | ---------------------------------------------------------------- |
| HP       | `generate_for_template()` creates transfers for matching periods |
| HP       | Amount always = `template.default_amount` (no salary linkage)    |
| SM       | Skips immutable-status transfers                                 |
| SM       | Skips overridden and deleted transfers                           |
| HP       | `regenerate_for_template()` deletes and recreates                |
| SM       | Raises `RecurrenceConflict` with overrides                       |
| HP       | `resolve_conflicts()` keep and update actions                    |
| BE       | Template with no recurrence rule → empty result                  |

**Estimated new tests: 8**

---

### 1.5 `services/balance_calculator.py` -- Priority P0

**Status: Complete (14 tests in `test_services/test_balance_calculator.py`).** Edge cases for
pre-anchor periods, None anchor_balance, mixed transactions + transfers all covered.

| Category | Tests Needed                                                               |
| -------- | -------------------------------------------------------------------------- |
| BE       | `anchor_balance=None` → defaults to Decimal("0.00")                        |
| BE       | Pre-anchor periods → not included in output dict                           |
| FIN      | Mixed income + expense in same period                                      |
| FIN      | Mixed transactions AND transfers in same period                            |
| FIN      | Multiple transfers in same period (1 incoming + 1 outgoing)                |
| SM       | Settled transactions (done/received) excluded from anchor period remaining |
| SM       | Credit and cancelled transfers excluded                                    |
| BE       | Empty transactions list + empty transfers list → anchor balance only       |
| BE       | No periods match anchor_period_id (all pre-anchor)                         |
| FIN      | 5-period rollforward with income, expense, and transfers                   |

**Estimated new tests: 10**

---

### 1.6 `services/pay_period_service.py` -- Priority P1

**Status: Complete (17 tests in `test_services/test_pay_period_service.py`).** Generate,
get_current, get_next, get_all, get_periods_in_range all covered.

#### `generate_pay_periods()`

| Category | Tests Needed                                            |
| -------- | ------------------------------------------------------- |
| HP       | Generates correct number of periods with 14-day cadence |
| HP       | Period indices are sequential                           |
| HP       | `end_date = start_date + cadence_days - 1`              |
| BE       | Duplicate start_date silently skipped                   |
| BE       | Appending to existing periods (max index + 1)           |
| SP       | Invalid `start_date` type → raises error                |
| SP       | `cadence_days < 1` → raises error                       |
| BE       | `num_periods=0` → empty result                          |
| BE       | `num_periods=1` → single period                         |

#### `get_current_period()`

| Category | Tests Needed                            |
| -------- | --------------------------------------- |
| HP       | Returns period containing today         |
| BE       | No period contains today → returns None |
| HP       | Custom `as_of` date parameter           |

#### `get_periods_in_range()`

| Category | Tests Needed                                    |
| -------- | ----------------------------------------------- |
| HP       | Returns correct window by index                 |
| BE       | Range beyond available periods → partial result |

#### `get_next_period()`

| Category | Tests Needed                             |
| -------- | ---------------------------------------- |
| HP       | Returns the immediately following period |
| BE       | Last period → returns None               |

#### `get_all_periods()`

| Category | Tests Needed                         |
| -------- | ------------------------------------ |
| HP       | Returns all periods ordered by index |

**Estimated new tests: 16**

---

### 1.7 `services/savings_goal_service.py` -- Priority P1

**Status: Complete (14 tests in `test_services/test_savings_goal_service.py`).** Required
contribution, savings metrics, and count_periods_until all covered.

#### `calculate_required_contribution()`

| Category | Tests Needed                                              |
| -------- | --------------------------------------------------------- |
| HP       | Gap exists → returns `gap / remaining_periods`            |
| BE       | Already met (balance >= target) → returns Decimal("0.00") |
| BE       | `remaining_periods = 0` → returns None                    |
| BE       | `remaining_periods < 0` → returns None                    |
| FIN      | Decimal precision: ROUND_HALF_UP to 2 places              |

#### `calculate_savings_metrics()`

| Category | Tests Needed                                  |
| -------- | --------------------------------------------- |
| HP       | Returns months/paychecks/years covered        |
| FIN      | `paychecks_covered = months * 26 / 12`        |
| FIN      | `years_covered = months / 12`                 |
| BE       | `average_monthly_expenses = 0` → all zeros    |
| BE       | `average_monthly_expenses = None` → all zeros |
| BE       | `savings_balance = 0` → all zeros             |

#### `count_periods_until()`

| Category | Tests Needed                             |
| -------- | ---------------------------------------- |
| HP       | Counts periods from today to target_date |
| BE       | `target_date = None` → returns None      |
| BE       | Target date in the past → returns 0      |
| BE       | No periods in range → returns 0          |

**Estimated new tests: 14**

---

### 1.8 `services/credit_workflow.py` -- Priority P2 ✅

**Status: Complete (9 tests).** All edge cases covered.

| Category | Tests Needed                                                                       | Status |
| -------- | ---------------------------------------------------------------------------------- | ------ |
| IDEM     | `mark_as_credit()` called twice → returns same payback (existing test covers this) | ✅     |
| BE       | Payback uses `actual_amount` when set, `estimated_amount` when not                 | ✅     |
| BE       | Auto-creates "Credit Card: Payback" category if missing                            | ✅     |
| SM       | `unmark_credit()` reverts status to projected                                      | ✅     |
| BE       | No next period → raises ValidationError                                            | ✅     |

**New tests added: 3** (`test_payback_uses_actual_amount_when_set`,
`test_auto_creates_cc_category_if_missing`, `test_no_next_period_raises_validation_error`)

---

### 1.9 `services/carry_forward_service.py` -- Priority P2 ✅

**Status: Complete (9 tests in `test_credit_workflow.py::TestCarryForward`).**

| Category | Tests Needed                                     | Status                                                                              |
| -------- | ------------------------------------------------ | ----------------------------------------------------------------------------------- |
| HP       | Moves projected transactions to target period    | ✅ `test_carry_forward_moves_projected_items`                                       |
| HP       | Returns correct count of moved items             | ✅ `test_carry_forward_moves_projected_items`                                       |
| SM       | Template-linked items flagged `is_override=True` | ✅ `test_carry_forward_flags_template_items_as_override`                            |
| SM       | Done/received items NOT moved                    | ✅ `test_carry_forward_skips_done_items`, `test_carry_forward_skips_received_items` |
| SM       | Cancelled items NOT moved                        | ✅ `test_carry_forward_skips_cancelled_items`                                       |
| SM       | Soft-deleted items NOT moved                     | ✅ `test_carry_forward_skips_soft_deleted_items`                                    |
| SP       | Source period doesn't exist → NotFoundError      | ✅ `test_carry_forward_source_not_found`                                            |
| SP       | Target period doesn't exist → NotFoundError      | ✅ `test_carry_forward_target_not_found`                                            |
| BE       | No projected items → returns 0                   | ✅ `test_carry_forward_empty_source_returns_zero`                                   |

**Estimated new tests: ~~9~~ Done**

---

### 1.10 `services/auth_service.py` -- Priority P2 ✅

**Status: Complete (7 tests in `test_auth_service.py`).**

| Category | Tests Needed                                          | Status                                                       |
| -------- | ----------------------------------------------------- | ------------------------------------------------------------ |
| HP       | `hash_password()` returns bcrypt hash                 | ✅ `test_hash_password_returns_bcrypt_hash`                  |
| HP       | `verify_password()` returns True for matching pair    | ✅ `test_verify_password_returns_true_for_correct_password`  |
| SP       | `verify_password()` returns False for wrong password  | ✅ `test_verify_password_returns_false_for_wrong_password`   |
| HP       | `authenticate()` returns User on valid credentials    | ✅ `test_authenticate_returns_user_on_valid_credentials`     |
| SP       | `authenticate()` raises AuthError on wrong email      | ✅ `test_authenticate_raises_auth_error_on_wrong_email`      |
| SP       | `authenticate()` raises AuthError on wrong password   | ✅ `test_authenticate_raises_auth_error_on_wrong_password`   |
| SP       | `authenticate()` raises AuthError on disabled account | ✅ `test_authenticate_raises_auth_error_on_disabled_account` |

**Estimated new tests: ~~7~~ Done**

---

## 2. Routes

### 2.1 `routes/accounts.py` -- Priority P1 ✅

**Status: Complete (29 tests in `test_routes/test_accounts.py`).**

#### Account CRUD

| Category | Tests Needed                                                                                 | Status                                             |
| -------- | -------------------------------------------------------------------------------------------- | -------------------------------------------------- |
| HP       | GET `/accounts` -- renders list with user's accounts                                          | ✅ `test_list_accounts_renders`                    |
| HP       | GET `/accounts/new` -- renders create form                                                    | ✅ `test_new_account_form_renders`                 |
| HP       | POST `/accounts` -- creates account, redirects to list                                        | ✅ `test_create_account`                           |
| HP       | GET `/accounts/<id>/edit` -- renders edit form                                                | ✅ `test_edit_account_form_renders`                |
| HP       | POST `/accounts/<id>` -- updates account fields                                               | ✅ `test_update_account`                           |
| HP       | POST `/accounts/<id>/delete` -- soft-deactivates account                                      | ✅ `test_deactivate_account`                       |
| HP       | POST `/accounts/<id>/reactivate` -- reactivates account                                       | ✅ `test_reactivate_account`                       |
| SP       | POST `/accounts` -- validation error (missing name)                                           | ✅ `test_create_account_validation_error`          |
| SP       | POST `/accounts` -- duplicate name → flash warning                                            | ✅ `test_create_account_duplicate_name`            |
| SP       | POST `/accounts/<id>` -- duplicate name → flash warning                                       | ✅ `test_update_account_duplicate_name`            |
| IDOR     | GET `/accounts/<id>/edit` -- other user's account → redirect                                  | ✅ `test_edit_other_users_account_redirects`       |
| IDOR     | POST `/accounts/<id>` -- other user's account → redirect                                      | ✅ `test_update_other_users_account_redirects`     |
| IDOR     | POST `/accounts/<id>/delete` -- other user's account → redirect                               | ✅ `test_deactivate_other_users_account_redirects` |
| SM       | POST `/accounts/<id>/delete` -- account in use by active transfers → flash warning, no delete | ✅ `test_deactivate_account_with_active_transfers` |
| IDEM     | POST `/accounts` -- double-submit same name → duplicate flash on 2nd                          | ✅ `test_create_account_double_submit`             |

#### Anchor Balance (Grid Integration)

| Category | Tests Needed                                                            | Status                                          |
| -------- | ----------------------------------------------------------------------- | ----------------------------------------------- |
| HP       | PATCH `/accounts/<id>/true-up` -- updates balance, creates history entry | ✅ `test_true_up_updates_balance`               |
| SP       | PATCH `/accounts/<id>/true-up` -- no current period → 400                | ✅ `test_true_up_no_current_period`             |
| IDOR     | PATCH `/accounts/<id>/true-up` -- other user's account → 404             | ✅ `test_true_up_other_users_account`           |
| FIN      | True-up creates `AccountAnchorHistory` audit record                     | ✅ `test_true_up_updates_balance` (combined)    |

#### Account Type Management

| Category | Tests Needed                                                     | Status                                  |
| -------- | ---------------------------------------------------------------- | --------------------------------------- |
| HP       | POST `/accounts/types` -- creates new account type                | ✅ `test_create_account_type`           |
| HP       | POST `/accounts/types/<id>` -- renames account type               | ✅ `test_rename_account_type`           |
| HP       | POST `/accounts/types/<id>/delete` -- deletes unused type         | ✅ `test_delete_unused_account_type`    |
| SP       | POST `/accounts/types` -- duplicate name → flash warning          | ✅ `test_create_duplicate_account_type` |
| SP       | POST `/accounts/types/<id>/delete` -- type in use → flash warning | ✅ `test_delete_account_type_in_use`    |

**Estimated new tests: ~~30~~ 29 Done (FIN merged into HP true-up test)**

---

### 2.2 `routes/salary.py` -- Priority P1 ✅

**Status: Complete (36 tests in `test_routes/test_salary.py`).**

#### Profile CRUD

| Category | Tests Needed                                                                  | Status                                         |
| -------- | ----------------------------------------------------------------------------- | ---------------------------------------------- |
| HP       | GET `/salary` -- lists profiles with estimated net pay                         | ✅ `test_list_profiles`                        |
| HP       | GET `/salary/new` -- renders create form                                       | ✅ `test_new_profile_form`                     |
| HP       | POST `/salary` -- creates profile with linked template + recurrence + category | ✅ `test_create_profile`                       |
| HP       | GET `/salary/<id>/edit` -- renders edit form                                   | ✅ `test_edit_profile_form`                    |
| HP       | POST `/salary/<id>` -- updates profile, regenerates transactions               | ✅ `test_update_profile`                       |
| HP       | POST `/salary/<id>/delete` -- deactivates profile + template                   | ✅ `test_delete_profile`                       |
| SP       | POST `/salary` -- validation error → flash danger                              | ✅ `test_create_profile_validation_error`      |
| SP       | POST `/salary` -- no baseline scenario → flash danger                          | ✅ `test_create_profile_no_baseline_scenario`  |
| SP       | POST `/salary` -- no active account → flash danger                             | ✅ `test_create_profile_no_active_account`     |
| IDOR     | GET `/salary/<id>/edit` -- other user's profile → redirect                     | ✅ `test_edit_other_users_profile_redirects`   |
| IDOR     | POST `/salary/<id>` -- other user's profile → redirect                         | ✅ `test_update_other_users_profile_redirects` |
| IDOR     | POST `/salary/<id>/delete` -- other user's profile → redirect                  | ✅ `test_delete_other_users_profile_redirects` |
| FIN      | Created template amount = `annual_salary / pay_periods_per_year`              | ✅ `test_create_profile_template_amount`       |
| IDEM     | POST `/salary` -- double-submit → 2nd attempt duplicate name or re-create      | ✅ `test_create_profile_double_submit`         |

#### Raises

| Category | Tests Needed                                                                       | Status                                       |
| -------- | ---------------------------------------------------------------------------------- | -------------------------------------------- |
| HP       | POST `/salary/<id>/raises` -- adds raise, regenerates transactions                  | ✅ `test_add_raise`                          |
| HP       | POST `/salary/raises/<id>/delete` -- removes raise, regenerates                     | ✅ `test_delete_raise`                       |
| SP       | POST `/salary/<id>/raises` -- validation error (missing percentage and flat_amount) | ✅ `test_add_raise_validation_error`         |
| SP       | POST `/salary/<id>/raises` -- profile not found → flash danger                      | ✅ `test_add_raise_profile_not_found`        |
| IDOR     | POST `/salary/raises/<id>/delete` -- other user's raise → "Not authorized"          | ✅ `test_delete_other_users_raise_redirects` |
| HP       | HTMX response returns `_raises_section.html` partial                               | ✅ `test_add_raise_htmx_returns_partial`     |

#### Deductions

| Category | Tests Needed                                                           | Status                                            |
| -------- | ---------------------------------------------------------------------- | ------------------------------------------------- |
| HP       | POST `/salary/<id>/deductions` -- adds deduction, regenerates           | ✅ `test_add_deduction`                           |
| HP       | POST `/salary/deductions/<id>/delete` -- removes deduction, regenerates | ✅ `test_delete_deduction`                        |
| SP       | POST `/salary/<id>/deductions` -- validation error                      | ✅ `test_add_deduction_validation_error`          |
| IDOR     | POST `/salary/deductions/<id>/delete` -- other user's deduction         | ✅ `test_delete_other_users_deduction_redirects`  |
| HP       | HTMX response returns `_deductions_section.html` partial               | ✅ `test_add_deduction_htmx_returns_partial`      |
| BE       | Percentage input converted correctly (6 → 0.06)                        | ✅ `test_add_percentage_deduction_converts_input` |

#### Breakdown & Projection

| Category | Tests Needed                                                               | Status                                            |
| -------- | -------------------------------------------------------------------------- | ------------------------------------------------- |
| HP       | GET `/salary/<id>/breakdown/<period_id>` -- renders breakdown               | ✅ `test_breakdown_renders`                       |
| HP       | GET `/salary/<id>/breakdown` -- redirects to current period                 | ✅ `test_breakdown_current_redirects`             |
| HP       | GET `/salary/<id>/projection` -- renders multi-period projection            | ✅ `test_projection_renders`                      |
| SP       | GET `/salary/<id>/breakdown` -- no current period → flash warning           | ✅ `test_breakdown_no_current_period`             |
| IDOR     | GET `/salary/<id>/breakdown/<period_id>` -- other user's profile → redirect | ✅ `test_breakdown_other_users_profile_redirects` |

#### Tax Config

| Category | Tests Needed                                                  | Status                                  |
| -------- | ------------------------------------------------------------- | --------------------------------------- |
| HP       | GET `/salary/tax-config` -- renders tax config page            | ✅ `test_tax_config_page_renders`       |
| HP       | POST `/salary/tax-config` -- creates/updates state config      | ✅ `test_update_state_tax_config`       |
| HP       | POST `/salary/fica-config` -- creates/updates FICA config      | ✅ `test_update_fica_config`            |
| SP       | POST `/salary/tax-config` -- invalid state code → flash danger | ✅ `test_update_state_tax_invalid_code` |
| SP       | POST `/salary/fica-config` -- validation error → flash danger  | ✅ `test_update_fica_validation_error`  |

**Estimated new tests: ~~35~~ 36 Done**

---

### 2.3 `routes/transfers.py` -- Priority P1 ✅

**Status: Complete (28 tests in `test_routes/test_transfers.py`).**

#### Template Management

| Category | Tests Needed                                                                 | Status                                          |
| -------- | ---------------------------------------------------------------------------- | ----------------------------------------------- |
| HP       | GET `/transfers` -- lists user's transfer templates                           | ✅ `test_list_templates`                        |
| HP       | GET `/transfers/new` -- renders create form with accounts                     | ✅ `test_new_template_form`                     |
| HP       | POST `/transfers` -- creates template with recurrence, generates transfers    | ✅ `test_create_template`                       |
| HP       | GET `/transfers/<id>/edit` -- renders edit form                               | ✅ `test_edit_template_form`                    |
| HP       | POST `/transfers/<id>` -- updates template, regenerates transfers             | ✅ `test_update_template`                       |
| HP       | POST `/transfers/<id>/delete` -- deactivates template, soft-deletes transfers | ✅ `test_delete_template`                       |
| HP       | POST `/transfers/<id>/reactivate` -- reactivates template, restores transfers | ✅ `test_reactivate_template`                   |
| SP       | POST `/transfers` -- validation error → flash danger                          | ✅ `test_create_template_validation_error`      |
| SP       | POST `/transfers` -- from_account == to_account → validation error            | ✅ `test_create_template_same_accounts`         |
| IDOR     | POST `/transfers/<id>` -- other user's template → redirect                    | ✅ `test_update_other_users_template_redirects` |
| IDOR     | POST `/transfers/<id>/delete` -- other user's template → redirect             | ✅ `test_delete_other_users_template_redirects` |
| IDEM     | POST `/transfers` -- double-submit same name → unique constraint              | ✅ `test_create_template_double_submit`         |

#### Grid Cell Routes

| Category | Tests Needed                                               | Status                                  |
| -------- | ---------------------------------------------------------- | --------------------------------------- |
| HP       | GET `/transfers/cell/<id>` -- returns cell partial          | ✅ `test_get_cell`                      |
| HP       | GET `/transfers/quick-edit/<id>` -- returns quick-edit form | ✅ `test_get_quick_edit`                |
| HP       | GET `/transfers/<id>/full-edit` -- returns full-edit form   | ✅ `test_get_full_edit`                 |
| IDOR     | GET `/transfers/cell/<id>` -- other user's transfer → 404   | ✅ `test_get_cell_other_users_transfer` |

#### Transfer Instance Operations

| Category | Tests Needed                                                                       | Status                                                                         |
| -------- | ---------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| HP       | PATCH `/transfers/instance/<id>` -- updates amount                                  | ✅ `test_update_transfer_amount`                                               |
| HP       | POST `/transfers/instance/<id>/mark-done` -- sets status to done                    | ✅ `test_mark_done`                                                            |
| HP       | POST `/transfers/instance/<id>/cancel` -- sets status to cancelled                  | ✅ `test_cancel_transfer`                                                      |
| HP       | DELETE `/transfers/instance/<id>` -- soft-delete (template) or hard-delete (ad-hoc) | ✅ `test_delete_ad_hoc_transfer`, `test_delete_template_transfer_soft_deletes` |
| SM       | Template transfer → `is_override=True` on amount change                            | ✅ `test_template_transfer_override_on_amount_change`                          |
| SM       | Cancel → `effective_amount` returns Decimal("0")                                   | ✅ `test_cancelled_transfer_effective_amount_zero`                             |
| IDOR     | PATCH `/transfers/instance/<id>` -- other user's transfer → 404                     | ✅ `test_update_other_users_transfer`                                          |

#### Ad-Hoc Creation

| Category | Tests Needed                                                                                | Status                                     |
| -------- | ------------------------------------------------------------------------------------------- | ------------------------------------------ |
| HP       | POST `/transfers/ad-hoc` -- creates transfer, returns 201                                    | ✅ `test_create_ad_hoc_transfer`           |
| SP       | POST `/transfers/ad-hoc` -- validation error → 400                                           | ✅ `test_create_ad_hoc_validation_error`   |
| SP       | POST `/transfers/ad-hoc` -- period not owned → 404                                           | ✅ `test_create_ad_hoc_other_users_period` |
| IDEM     | POST `/transfers/ad-hoc` -- double-submit → second succeeds (no unique constraint on ad-hoc) | ✅ `test_create_ad_hoc_double_submit`      |

**Estimated new tests: ~~28~~ Done**

---

### 2.4 `routes/savings.py` -- Priority P1

**Status: Complete (19 tests in `test_routes/test_savings.py`).**

#### Dashboard

| Category | Tests Needed                                                  | Status                                                   |
| -------- | ------------------------------------------------------------- | -------------------------------------------------------- |
| HP       | GET `/savings` -- renders dashboard with goals and projections | ✅ `test_dashboard_renders`, `test_dashboard_with_goals` |
| BE       | No savings accounts → empty dashboard                         | ✅ `test_dashboard_no_savings_accounts`                  |
| BE       | No goals → dashboard still renders account projections        | ✅ `test_dashboard_no_goals`                             |
| HP       | Unauthenticated request → redirect to login                   | ✅ `test_dashboard_requires_login`                       |

#### Goal CRUD

| Category | Tests Needed                                                       | Status                                        |
| -------- | ------------------------------------------------------------------ | --------------------------------------------- |
| HP       | GET `/savings/goals/new` -- renders form with accounts              | ✅ `test_new_goal_form`                       |
| HP       | POST `/savings/goals` -- creates goal, redirects to dashboard       | ✅ `test_create_goal_success`                 |
| HP       | POST `/savings/goals` -- optional fields omitted                    | ✅ `test_create_goal_without_optional_fields` |
| HP       | GET `/savings/goals/<id>/edit` -- renders edit form                 | ✅ `test_edit_goal_form`                      |
| HP       | POST `/savings/goals/<id>` -- updates goal fields                   | ✅ `test_update_goal_success`                 |
| HP       | POST `/savings/goals/<id>/delete` -- soft-deactivates goal          | ✅ `test_delete_goal_success`                 |
| SP       | POST `/savings/goals` -- validation error (missing fields)          | ✅ `test_create_goal_validation_error`        |
| SP       | POST `/savings/goals/<id>` -- negative target_amount                | ✅ `test_update_goal_validation_error`        |
| SP       | POST `/savings/goals` -- another user's account → invalid           | ✅ `test_create_goal_invalid_account`         |
| IDOR     | GET `/savings/goals/<id>/edit` -- other user's goal → redirect      | ✅ `test_edit_goal_idor`                      |
| IDOR     | POST `/savings/goals/<id>` -- other user's goal → redirect          | ✅ `test_update_goal_idor`                    |
| IDOR     | POST `/savings/goals/<id>/delete` -- other user's goal → redirect   | ✅ `test_delete_goal_idor`                    |
| BE       | POST `/savings/goals/999999/delete` -- nonexistent goal             | ✅ `test_delete_nonexistent_goal`             |
| IDEM     | POST `/savings/goals` -- duplicate name+account → unique constraint | ✅ `test_duplicate_goal_name_same_account`    |

**Tests: 19** (5 dashboard + 5 create + 5 update + 3 delete + 1 idempotency)

---

### 2.5 `routes/templates.py` -- Priority P2

**Status: Complete (24 tests in `test_routes/test_templates.py`).**

#### Template CRUD

| Category | Tests Needed                                                                 | Status                                        |
| -------- | ---------------------------------------------------------------------------- | --------------------------------------------- |
| HP       | GET `/templates` -- lists user's templates                                    | ✅ `test_list_templates`                      |
| HP       | GET `/templates` -- empty list                                                | ✅ `test_list_templates_empty`                |
| HP       | GET `/templates/new` -- renders form with categories, accounts, patterns      | ✅ `test_new_template_form`                   |
| HP       | POST `/templates` -- creates template without recurrence                      | ✅ `test_create_template_no_recurrence`       |
| HP       | POST `/templates` -- creates template with recurrence, generates transactions | ✅ `test_create_template_with_recurrence`     |
| HP       | GET `/templates/<id>/edit` -- renders edit form                               | ✅ `test_edit_template_form`                  |
| HP       | POST `/templates/<id>` -- updates template, regenerates                       | ✅ `test_update_template_success`             |
| HP       | POST `/templates/<id>/delete` -- deactivates, soft-deletes transactions       | ✅ `test_delete_deactivates_and_soft_deletes` |
| HP       | POST `/templates/<id>/reactivate` -- reactivates, restores transactions       | ✅ `test_reactivate_restores_transactions`    |
| SP       | POST `/templates` -- validation error (missing fields)                        | ✅ `test_create_template_validation_error`    |
| SP       | POST `/templates/<id>` -- validation error (invalid day_of_month)             | ✅ `test_update_template_validation_error`    |
| SP       | POST `/templates` -- another user's account → invalid                         | ✅ `test_create_template_invalid_account`     |
| SP       | POST `/templates` -- another user's category → invalid                        | ✅ `test_create_template_invalid_category`    |
| IDOR     | GET `/templates/<id>/edit` -- other user's template → redirect                | ✅ `test_edit_template_idor`                  |
| IDOR     | POST `/templates/<id>` -- other user's template → redirect                    | ✅ `test_update_template_idor`                |
| IDOR     | POST `/templates/<id>/delete` -- other user's template → redirect             | ✅ `test_delete_template_idor`                |
| IDOR     | POST `/templates/<id>/reactivate` -- other user's template → redirect         | ✅ `test_reactivate_template_idor`            |
| BE       | POST `/templates/999999/delete` -- nonexistent template                       | ✅ `test_delete_nonexistent_template`         |
| SM       | Update triggers `RecurrenceConflict` → flash warning                         | ✅ `test_update_triggers_recurrence_conflict` |

#### Preview Recurrence

| Category | Tests Needed                                                                                       | Status                            |
| -------- | -------------------------------------------------------------------------------------------------- | --------------------------------- |
| HP       | GET `/templates/preview-recurrence?recurrence_pattern=monthly&day_of_month=15` → returns HTML list | ✅ `test_preview_monthly`         |
| HP       | GET `/templates/preview-recurrence?recurrence_pattern=every_period` → returns list                 | ✅ `test_preview_every_period`    |
| BE       | Pattern = "once" → "No preview" message                                                            | ✅ `test_preview_once_pattern`    |
| BE       | Unknown pattern → "Unknown pattern" message                                                        | ✅ `test_preview_unknown_pattern` |
| BE       | No pattern parameter → "No preview" message                                                        | ✅ `test_preview_no_pattern`      |

**Tests: 24** (2 list + 6 create + 6 update + 3 delete + 2 reactivate + 5 preview)

---

### 2.6 `routes/categories.py` -- Priority P2

**Status: Complete (11 tests in `test_routes/test_categories.py`).**

| Category | Tests Needed                                                           | Status                                          |
| -------- | ---------------------------------------------------------------------- | ----------------------------------------------- |
| HP       | GET `/categories` -- renders list grouped by group_name                 | ✅ `test_list_categories`                       |
| HP       | POST `/categories` -- creates category, redirects                       | ✅ `test_create_category_success`               |
| HP       | POST `/categories` -- HTMX request → returns partial HTML               | ✅ `test_create_category_htmx`                  |
| HP       | POST `/categories/<id>/delete` -- deletes unused category               | ✅ `test_delete_unused_category`                |
| SP       | POST `/categories` -- validation error                                  | ✅ `test_create_category_validation_error`      |
| SP       | POST `/categories` -- HTMX validation error → 400 JSON                  | ✅ `test_create_category_htmx_validation_error` |
| SP       | POST `/categories` -- duplicate group+item → flash warning              | ✅ `test_create_category_duplicate`             |
| SP       | POST `/categories/<id>/delete` -- in use by template → flash warning    | ✅ `test_delete_category_in_use_by_template`    |
| SP       | POST `/categories/<id>/delete` -- in use by transaction → flash warning | ✅ `test_delete_category_in_use_by_transaction` |
| IDOR     | POST `/categories/<id>/delete` -- other user's category → flash danger  | ✅ `test_delete_category_idor`                  |
| BE       | POST `/categories/999999/delete` -- nonexistent category                | ✅ `test_delete_nonexistent_category`           |

**Tests: 11** (1 list + 5 create + 5 delete)

---

### 2.7 `routes/pay_periods.py` -- Priority P2

**Status: Complete (6 tests in `test_routes/test_pay_periods.py`).**

| Category | Tests Needed                                                        | Status                                            |
| -------- | ------------------------------------------------------------------- | ------------------------------------------------- |
| HP       | GET `/pay-periods/generate` -- renders form                          | ✅ `test_generate_form_renders`                   |
| HP       | POST `/pay-periods/generate` -- creates periods, redirects to grid   | ✅ `test_generate_periods_success`                |
| SP       | POST `/pay-periods/generate` -- invalid start_date → 422 with errors | ✅ `test_generate_missing_start_date`             |
| SP       | POST `/pay-periods/generate` -- cadence_days=0 → validation error    | ✅ `test_generate_cadence_zero`                   |
| BE       | `num_periods=1` → creates single period                             | ✅ `test_generate_single_period`                  |
| IDEM     | POST `/pay-periods/generate` -- double-submit → duplicates skipped   | ✅ `test_generate_double_submit_skips_duplicates` |

**Tests: 6**

---

### 2.8 `routes/settings.py` -- Priority P2

**Status: Complete (7 tests in `test_routes/test_settings.py`).**

| Category | Tests Needed                                                    | Status                                     |
| -------- | --------------------------------------------------------------- | ------------------------------------------ |
| HP       | GET `/settings` -- renders settings page                         | ✅ `test_settings_page_renders`            |
| HP       | GET `/settings` -- auto-creates UserSettings if missing          | ✅ `test_settings_auto_creates_if_missing` |
| HP       | POST `/settings` -- updates all three fields                     | ✅ `test_update_all_fields`                |
| SP       | POST `/settings` -- non-numeric grid_periods → flash danger      | ✅ `test_invalid_grid_periods`             |
| SP       | POST `/settings` -- invalid Decimal for inflation → flash danger | ✅ `test_invalid_inflation_rate`           |
| SP       | POST `/settings` -- non-numeric threshold → flash danger         | ✅ `test_invalid_threshold`                |
| BE       | Blank fields skipped (partial update)                           | ✅ `test_blank_fields_skipped`             |

**Tests: 7** (2 show + 5 update)

---

### 2.9 `routes/grid.py` -- Priority P2

**Status: Complete (12 tests in `test_routes/test_grid.py` -- 8 existing + 4 new).**

| Category | Tests Needed                                                   | Status                                  |
| -------- | -------------------------------------------------------------- | --------------------------------------- |
| HP       | GET `/grid/balance-row` -- returns recalculated balance partial | ✅ `test_balance_row_returns_partial`   |
| BE       | GET `/grid/balance-row` -- no current period → 204 empty        | ✅ `test_balance_row_no_current_period` |
| BE       | GET `/grid/balance-row` -- custom offset shifts window          | ✅ `test_balance_row_custom_offset`     |
| BE       | GET `/` -- `periods` larger than available → renders available  | ✅ `test_grid_periods_large_value`      |

**Tests: 4 new** (added to existing 8)

---

### 2.10 `routes/transactions.py` -- Priority P2

**Status: Complete (19 tests in `test_routes/test_grid.py` + 13 in
`test_routes/test_transaction_auth.py`).**

| Category | Tests Needed                                                         | Status                                                |
| -------- | -------------------------------------------------------------------- | ----------------------------------------------------- |
| SM       | `mark_done` with `actual_amount` provided → sets actual, status=done | ✅ `test_mark_expense_done` (existing)                |
| SM       | `mark_done` without `actual_amount` → status only                    | ✅ `test_mark_done_without_actual_amount`             |
| SM       | `cancel_transaction` → status=cancelled, `effective_amount`=0        | ✅ `test_cancel_transaction`                          |
| SM       | `mark_credit` → creates payback in next period                       | ✅ `test_mark_credit_creates_payback`                 |
| SM       | `unmark_credit` → reverts to projected, deletes payback              | ✅ `test_unmark_credit_reverts_and_deletes_payback`   |
| HP       | `create_transaction` (full form) → creates with all fields           | ✅ `test_create_transaction_full_form`                |
| BE       | `create_inline` when no baseline scenario → 400                      | ✅ `test_create_inline_no_scenario`                   |
| SM       | Delete template-linked txn → soft-delete (`is_deleted=True`)         | ✅ `test_soft_delete_template_transaction` (existing) |
| SM       | Delete ad-hoc txn → hard-delete                                      | ✅ `test_hard_delete_adhoc_transaction`               |

**Tests: 7 new** (added to existing 12 in test_grid.py)

---

### 2.11 `routes/auth/` (pre-split `routes/auth.py`) -- Priority P3 ✅

**Status: Complete (7 tests).** All edge cases covered.

| Status | Test                                  |
| ------ | ------------------------------------- |
| ✅     | `test_login_disabled_account`         |
| ✅     | `test_rate_limiting_after_5_attempts` |

**Added: 2 new tests**

---

## 3. Models

### 3.1 `models/transaction.py` -- Priority P0 ✅

**Status: Complete (5 tests in `test_models/test_computed_properties.py`).**

| Status | Test                                                                       |
| ------ | -------------------------------------------------------------------------- |
| ✅     | `test_projected_returns_estimated`                                         |
| ✅     | `test_done_with_actual_returns_actual`                                     |
| ✅     | `test_done_without_actual_returns_estimated`                               |
| ✅     | `test_is_income`                                                           |
| ✅     | `test_is_expense`                                                          |

**Added: 5 new tests**

---

### 3.2 `models/transfer.py` -- Priority P0 ✅

**Status: Complete (2 tests in `test_models/test_computed_properties.py`).**

| Status | Test                                       |
| ------ | ------------------------------------------ |
| ✅     | `test_projected_returns_amount`            |
| ✅     | `test_done_returns_amount`                 |

**Added: 2 new tests**

---

### 3.3 `models/category.py` -- Priority P3 ✅

| Status | Test                                |
| ------ | ----------------------------------- |
| ✅     | `test_display_name_format`          |

**Added: 1 new test**

---

### 3.4 `models/pay_period.py` -- Priority P3 ✅

| Status | Test                                     |
| ------ | ---------------------------------------- |
| ✅     | `test_label_format`                      |

**Added: 1 new test**

---

### 3.5 `models/paycheck_deduction.py` (PaycheckBreakdown dataclass) -- Priority P2 ✅

| Status | Test                                     |
| ------ | ---------------------------------------- |
| ✅     | `test_total_pre_tax`                     |
| ✅     | `test_total_post_tax`                    |
| ✅     | `test_total_taxes`                       |
| ✅     | `test_empty_deductions_return_zero`      |

**Added: 4 new tests**

---

## 4. Schemas

### 4.1 `schemas/validation.py` -- Priority P1 ✅

**Status: Complete (51 tests in `test_schemas/test_validation.py`).** All 16 schemas tested in
isolation covering required fields, type coercion, range validation, @pre_load stripping,
and @validates_schema cross-field rules.

| Schema (tests)                  | Key Validations Covered                                  |
| ------------------------------- | -------------------------------------------------------- |
| `TransactionCreateSchema` (3)   | Required fields; `estimated_amount >= 0`                 |
| `TransactionUpdateSchema` (3)   | @pre_load strips; partial update; invalid amount         |
| `InlineTransactionCreateSchema` (2) | Required fields; no name required                    |
| `TemplateCreateSchema` (5)      | Required fields; OneOf pattern; day_of_month range; @pre_load |
| `TemplateUpdateSchema` (3)      | All optional; Date parsing; invalid date                 |
| `TransferTemplateCreateSchema` (3) | `from != to` validator; `default_amount > 0`          |
| `TransferCreateSchema` (2)      | `from != to` validator; valid data                       |
| `TransferUpdateSchema` (2)      | `amount > 0`; partial update                             |
| `SavingsGoalCreateSchema` (3)   | `target_amount > 0`; required fields                     |
| `SavingsGoalUpdateSchema` (2)   | @pre_load strips; Boolean coercion                       |
| `SalaryProfileCreateSchema` (4) | Required fields; OneOf pay_periods; state_code length    |
| `RaiseCreateSchema` (5)         | percentage/flat_amount XOR; month range; both/neither    |
| `DeductionCreateSchema` (3)     | Required fields; OneOf deductions_per_year               |
| `FicaConfigSchema` (2)          | All required; Decimal coercion                           |
| `AccountCreateSchema` (3)       | Required fields; @pre_load strips empty optional         |
| `PayPeriodGenerateSchema` (4)   | Defaults; Range num_periods/cadence; missing start_date  |
| `CategoryCreateSchema` (2)      | Required fields; sort_order default                      |

**Added: 51 new tests**

---

## 5. Integration / Cross-Cutting Tests

### 5.1 End-to-End Workflows -- Priority P1 ✅

**Status: Complete (6 tests in `test_integration/test_workflows.py`).**

| Status | Test                         | Description                                                                                |
| ------ | ---------------------------- | ------------------------------------------------------------------------------------------ |
| ✅     | Salary → Grid                | `test_salary_generates_income_per_period`                                                  |
| ✅     | Template → Recurrence → Grid | `test_monthly_recurrence_hits_correct_periods`                                             |
| ✅     | Transfer → Balance           | `test_transfer_reduces_source_balance`                                                     |
| ✅     | Credit → Payback → Balance   | `test_credit_creates_payback_and_zeroes_effective`                                         |
| ✅     | Anchor True-Up → Balance     | `test_anchor_change_shifts_all_balances`                                                   |
| ✅     | Carry Forward                | `test_carry_forward_moves_projected_items`                                                 |

**Added: 6 new tests**

---

### 5.2 Idempotency -- Priority P2 ✅

**Status: Complete.** All POST endpoints tested for double-submission behavior.
6 tests already existed in route test files; 4 new tests added.

| Status | Route                          | Expected Behavior                                          | Test Location                                    |
| ------ | ------------------------------ | ---------------------------------------------------------- | ------------------------------------------------ |
| ✅     | POST `/login`                  | Second login succeeds (session refresh)                    | `test_integration/test_idempotency.py`           |
| ✅     | POST `/accounts`               | Duplicate name → flash warning, no second account          | `test_routes/test_accounts.py` (existing)        |
| ✅     | POST `/salary`                 | Duplicate profile name → unique constraint error           | `test_routes/test_salary.py` (existing)          |
| ✅     | POST `/templates`              | Creates duplicate (no unique constraint)                   | `test_integration/test_idempotency.py`           |
| ✅     | POST `/transfers`              | Duplicate name → unique constraint error                   | `test_routes/test_transfers.py` (existing)       |
| ✅     | POST `/savings/goals`          | Duplicate name+account → unique constraint error           | `test_routes/test_savings.py` (existing)         |
| ✅     | POST `/categories`             | Duplicate group+item → flash warning                       | `test_routes/test_categories.py` (existing)      |
| ✅     | POST `/pay-periods/generate`   | Duplicate dates silently skipped                           | `test_routes/test_pay_periods.py` (existing)     |
| ✅     | POST `/salary/<id>/raises`     | Creates duplicate raise (no unique constraint)             | `test_integration/test_idempotency.py`           |
| ✅     | POST `/salary/<id>/deductions` | Creates duplicate deduction (no unique constraint)         | `test_integration/test_idempotency.py`           |

**Added: 4 new tests**

---

## 6. Test Count Summary

| Module                                      | Priority | Estimated Tests   |
| ------------------------------------------- | -------- | ----------------- |
| **Services**                                |          |                   |
| paycheck_calculator (pipeline + deductions) | P0       | ~~30~~ 47 ✅ Done |
| recurrence_engine (patterns + regen)        | P0       | ~~22~~ 28 ✅ Done |
| balance_calculator (edge cases)             | P0       | ~~10~~ 14 ✅ Done |
| transfer_recurrence                         | P0       | ~~8~~ 10 ✅ Done  |
| pay_period_service                          | P1       | ~~16~~ 17 ✅ Done |
| savings_goal_service                        | P1       | ~~14~~ 14 ✅ Done |
| auth_service                                | P2       | ~~7~~ ✅ Done     |
| carry_forward_service                       | P2       | ~~9~~ ✅ Done     |
| credit_workflow (gaps)                      | P2       | ~~3~~ ✅ Done     |
| **Routes**                                  |          |                   |
| salary.py                                   | P1       | ~~35~~ 36 ✅ Done |
| accounts.py                                 | P1       | ~~30~~ 29 ✅ Done |
| transfers.py                                | P1       | ~~28~~ ✅ Done    |
| templates.py                                | P2       | ~~20~~ 24 ✅ Done |
| savings.py                                  | P1       | ~~16~~ 19 ✅ Done |
| categories.py                               | P2       | ~~10~~ 11 ✅ Done |
| settings.py                                 | P2       | ~~7~~ ✅ Done     |
| pay_periods.py                              | P2       | ~~6~~ ✅ Done     |
| grid.py (gaps)                              | P2       | ~~4~~ ✅ Done     |
| transactions.py (gaps)                      | P2       | ~~9~~ 7 ✅ Done   |
| auth.py (gaps)                              | P3       | ~~2~~ ✅ Done     |
| **Models**                                  |          |                   |
| transaction.py                              | P0       | ~~5~~ ✅ Done     |
| transfer.py                                 | P0       | ~~2~~ ✅ Done     |
| PaycheckBreakdown                           | P2       | ~~4~~ ✅ Done     |
| category.py, pay_period.py                  | P3       | ~~2~~ ✅ Done     |
| **Schemas**                                 |          |                   |
| validation.py (all schemas)                 | P1       | ~~40~~ 51 ✅ Done |
| **Integration**                             |          |                   |
| End-to-end workflows                        | P1       | ~~6~~ ✅ Done     |
| Idempotency                                 | P2       | ~~10~~ 4 ✅ Done  |
|                                             |          |                   |
| **Remaining estimated**                     |          | **0**             |
| **Current total (actual)**                  |          | **470**           |
| **Projected grand total**                   |          | **~467**          |

---

## 7. Suggested File Structure

```
tests/
├── conftest.py                          # Shared fixtures (existing)
├── test_audit_fixes.py                  # Audit-specific tests (existing)
├── TEST_PLAN.md                         # This document
│
├── test_models/
│   ├── test_transaction_model.py        # effective_amount, is_income, is_expense
│   ├── test_transfer_model.py           # effective_amount
│   ├── test_category_model.py           # display_name
│   ├── test_pay_period_model.py         # label
│   └── test_paycheck_breakdown.py       # total_pre_tax, total_post_tax, total_taxes
│
├── test_schemas/
│   ├── test_transaction_schemas.py      # Create, Update, Inline schemas
│   ├── test_template_schemas.py         # Create, Update schemas
│   ├── test_transfer_schemas.py         # Template Create/Update, Transfer Create/Update
│   ├── test_savings_schemas.py          # Goal Create/Update
│   ├── test_salary_schemas.py           # Profile, Raise, Deduction, FICA schemas
│   └── test_account_schemas.py          # Account, AccountType, PayPeriod, Category schemas
│
├── test_services/
│   ├── test_auth_service.py             # hash, verify, authenticate
│   ├── test_balance_calculator.py       # Edge cases (existing + new)
│   ├── test_carry_forward.py            # Direct carry_forward_unpaid tests
│   ├── test_credit_workflow.py          # Edge cases (existing + new)
│   ├── test_pay_period_service.py       # generate, get_current, get_next, etc.
│   ├── test_paycheck_calculator.py      # Full pipeline (existing + new)
│   ├── test_recurrence_engine.py        # All patterns, regen, conflicts (existing + new)
│   ├── test_savings_goal_service.py     # contribution, metrics, count_periods
│   ├── test_tax_calculator.py           # (existing, complete)
│   └── test_transfer_recurrence.py      # generate, regenerate, conflicts
│
├── test_routes/
│   ├── test_auth.py                     # (existing + edge cases)
│   ├── test_grid.py                     # (existing + balance_row)
│   ├── test_accounts.py                 # CRUD, anchor, types, IDOR
│   ├── test_categories.py              # CRUD, HTMX, IDOR
│   ├── test_pay_periods.py              # Generate form + POST
│   ├── test_salary.py                   # Profiles, raises, deductions, breakdown, tax config
│   ├── test_savings.py                  # Dashboard, goal CRUD, IDOR
│   ├── test_settings.py                 # Show, update, validation
│   ├── test_templates.py               # CRUD, preview, IDOR
│   ├── test_transaction_auth.py         # (existing)
│   └── test_transfers.py               # Template CRUD, instances, ad-hoc, IDOR
│
└── test_integration/
    ├── test_salary_grid_workflow.py      # Salary → template → grid transactions
    ├── test_transfer_balance_workflow.py # Transfer → balance calculator
    ├── test_credit_balance_workflow.py   # Credit → payback → balance
    └── test_idempotency.py              # Double-submit on all POST routes
```

---

## 8. Implementation Order

Tests should be written in this order to maximize coverage of high-risk areas first:

1. **P0 services** -- ~~paycheck_calculator~~ ✅, ~~recurrence engine~~ ✅, ~~balance_calculator~~
   ✅, ~~transfer_recurrence~~ ✅
2. **P0 models** -- ~~transaction/transfer effective_amount full coverage~~ ✅
3. **P1 schemas** -- ~~all Marshmallow schemas in isolation~~ ✅
4. **P1 routes** -- ~~salary~~ ✅, ~~accounts~~ ✅, ~~transfers~~ ✅, ~~savings~~ ✅ (happy + IDOR)
5. **P1 services** -- ~~pay_period_service~~ ✅, ~~savings_goal_service~~ ✅
6. **P1 integration** -- ~~end-to-end workflows~~ ✅
7. **P2 routes** -- ~~templates~~ ✅, ~~categories~~ ✅, ~~pay_periods~~ ✅, ~~settings~~ ✅, ~~grid
   gaps~~ ✅
8. **P2 services** -- ~~auth_service~~ ✅, ~~carry_forward~~ ✅, ~~credit_workflow gaps~~ ✅
9. **P2 idempotency** -- ~~double-submit tests~~ ✅
10. **P3 models + routes** -- ~~computed properties~~ ✅, ~~rate limiting~~ ✅
