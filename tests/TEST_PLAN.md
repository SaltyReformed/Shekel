# Shekel Budget App — Comprehensive Test Plan

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

| File                                        | Tests   | Notes                            |
| ------------------------------------------- | ------- | -------------------------------- |
| `test_routes/test_auth.py`                  | 5       | Login/logout; good               |
| `test_routes/test_grid.py`                  | 8       | Grid view + txn CRUD             |
| `test_routes/test_transaction_auth.py`      | 15      | IDOR on transactions; thorough   |
| `test_services/test_balance_calculator.py`  | 4       | Basic cases only                 |
| `test_routes/test_accounts.py`              | 29      | CRUD, anchor, types; complete    |
| `test_routes/test_salary.py`                | 36      | Profiles, raises, deductions, tax|
| `test_services/test_auth_service.py`        | 7       | Hash, verify, authenticate       |
| `test_services/test_credit_workflow.py`     | 15      | Credit + carry-forward; complete |
| `test_services/test_recurrence_engine.py`   | 6       | 2 of 8 patterns                  |
| `test_services/test_paycheck_calculator.py` | 10      | Raises only; no deductions       |
| `test_services/test_tax_calculator.py`      | 36      | Excellent coverage               |
| `test_audit_fixes.py`                       | 15      | Decimal, IDOR, constraints       |
| **Total**                                   | **183** |                                  |

---

## Priority Matrix

Tests are prioritized by risk severity from the adversarial audit.

| Priority          | Criteria                                    | Modules                                                                                          |
| ----------------- | ------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| **P0 — Critical** | Financial correctness bugs, data corruption | Paycheck calculator pipeline, balance calculator edge cases, recurrence engine advanced patterns |
| **P1 — High**     | Security gaps, IDOR, mass assignment        | Account routes, salary routes, transfer/savings happy paths, schema validation                   |
| **P2 — Medium**   | Functional gaps in core workflows           | Template CRUD, category/pay-period routes, settings, carry-forward                               |
| **P3 — Low**      | Model properties, idempotency, edge polish  | Computed properties, double-submit, rate limiting                                                |

---

## Module Inventory and Test Categories

### Legend

Each subsection marks the test categories needed:

- **HP** — Happy path (expected inputs produce expected outputs)
- **SP** — Sad path (malformed input, missing fields, wrong types)
- **IDOR** — Authorization (User B cannot access User A's resources)
- **BE** — Boundary/edge cases (zero amounts, max values, empty sets)
- **SM** — State machine transitions (status workflows, recurrence states)
- **IDEM** — Idempotency (double-submit on every POST route)
- **FIN** — Financial calculation correctness

Existing coverage is marked with a checkmark. Gaps are flagged with **[AUDIT GAP]** when identified
in the adversarial audit.

---

## 1. Services

### 1.1 `services/tax_calculator.py` — Priority P0

**Status: Well covered (36 tests).** No new tests needed.

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

### 1.2 `services/paycheck_calculator.py` — Priority P0

**Status: Partially covered (10 tests — raises only).** **[AUDIT GAP]** The full
`calculate_paycheck()` pipeline, deduction logic, 3rd-paycheck detection, inflation, cumulative
wages, and `project_salary()` are untested.

#### `calculate_paycheck()` — Full Pipeline

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

#### `_calculate_deductions()` — Deduction Logic

| Category | Tests Needed                                                   |
| -------- | -------------------------------------------------------------- |
| HP       | Flat pre-tax deduction applied correctly                       |
| HP       | Flat post-tax deduction applied correctly                      |
| HP       | Percentage-based deduction (calc_method="percentage")          |
| SM       | 24-per-year deduction skipped on 3rd paycheck                  |
| SM       | 12-per-year deduction applied only on first paycheck of month  |
| SM       | 12-per-year deduction skipped on non-first paycheck            |
| BE       | Inactive deduction skipped                                     |
| BE       | Deduction with annual_cap (not yet enforced — verify behavior) |

#### `_is_third_paycheck()` — 3rd Paycheck Detection

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

#### `project_salary()` — Multi-Period Projection

| Category | Tests Needed                                         |
| -------- | ---------------------------------------------------- |
| HP       | Returns one breakdown per period                     |
| HP       | Raise events appear in correct period                |
| BE       | Empty periods list → empty result                    |
| FIN      | Cumulative wages accumulate correctly across periods |

**Estimated new tests: 30**

---

### 1.3 `services/recurrence_engine.py` — Priority P0

**Status: Partially covered (6 tests — `every_period` and `every_n_periods` only).** **[AUDIT GAP]**
Six of eight recurrence patterns are untested. `regenerate_for_template()`, `resolve_conflicts()`,
and salary-linked amounts are untested.

#### Pattern Matching — `_match_periods()`

| Category | Tests Needed                                                      |
| -------- | ----------------------------------------------------------------- |
| HP       | `monthly` — generates in correct period for day 15                |
| BE       | `monthly` — day 31 clamped to 28 in February                      |
| BE       | `monthly` — day 30 clamped to 28 in February                      |
| HP       | `monthly_first` — picks first period starting in each month       |
| HP       | `quarterly` — generates in 4 correct months                       |
| BE       | `quarterly` — start_month=11 wraps correctly (Nov, Feb, May, Aug) |
| HP       | `semi_annual` — generates in 2 correct months                     |
| BE       | `semi_annual` — start_month=8 wraps correctly (Aug, Feb)          |
| HP       | `annual` — one match per calendar year                            |
| BE       | `annual` — Feb 29 target in non-leap year                         |
| HP       | `once` — returns empty list                                       |
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
| HP       | `action='keep'` — no changes made                            |
| HP       | `action='update'` — clears flags, applies new_amount         |
| BE       | `action='update'` with `new_amount=None` — clears flags only |

**Estimated new tests: 22**

---

### 1.4 `services/transfer_recurrence.py` — Priority P0

**Status: Zero dedicated tests.** **[AUDIT GAP]** Entire module untested. Parallel to
`recurrence_engine` but for Transfer objects.

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

### 1.5 `services/balance_calculator.py` — Priority P0

**Status: Partially covered (4 basic + 3 transfer tests).** **[AUDIT GAP]** Missing edge cases for
pre-anchor periods, None anchor_balance, and mixed transactions + transfers.

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

### 1.6 `services/pay_period_service.py` — Priority P1

**Status: Zero tests.** **[AUDIT GAP]** Foundation service with no direct test coverage.

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

### 1.7 `services/savings_goal_service.py` — Priority P1

**Status: Zero tests.** **[AUDIT GAP]** Pure functions with no test coverage.

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

### 1.8 `services/credit_workflow.py` — Priority P2 ✅

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

### 1.9 `services/carry_forward_service.py` — Priority P2 ✅

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

### 1.10 `services/auth_service.py` — Priority P2 ✅

**Status: Complete (7 tests in `test_auth_service.py`).**

| Category | Tests Needed                                          | Status |
| -------- | ----------------------------------------------------- | ------ |
| HP       | `hash_password()` returns bcrypt hash                 | ✅ `test_hash_password_returns_bcrypt_hash` |
| HP       | `verify_password()` returns True for matching pair    | ✅ `test_verify_password_returns_true_for_correct_password` |
| SP       | `verify_password()` returns False for wrong password  | ✅ `test_verify_password_returns_false_for_wrong_password` |
| HP       | `authenticate()` returns User on valid credentials    | ✅ `test_authenticate_returns_user_on_valid_credentials` |
| SP       | `authenticate()` raises AuthError on wrong email      | ✅ `test_authenticate_raises_auth_error_on_wrong_email` |
| SP       | `authenticate()` raises AuthError on wrong password   | ✅ `test_authenticate_raises_auth_error_on_wrong_password` |
| SP       | `authenticate()` raises AuthError on disabled account | ✅ `test_authenticate_raises_auth_error_on_disabled_account` |

**Estimated new tests: ~~7~~ Done**

---

## 2. Routes

### 2.1 `routes/accounts.py` — Priority P1 ✅

**Status: Complete (29 tests in `test_routes/test_accounts.py`).**

#### Account CRUD

| Category | Tests Needed                                                                                 | Status |
| -------- | -------------------------------------------------------------------------------------------- | ------ |
| HP       | GET `/accounts` — renders list with user's accounts                                          | ✅ `test_list_accounts_renders` |
| HP       | GET `/accounts/new` — renders create form                                                    | ✅ `test_new_account_form_renders` |
| HP       | POST `/accounts` — creates account, redirects to list                                        | ✅ `test_create_account` |
| HP       | GET `/accounts/<id>/edit` — renders edit form                                                | ✅ `test_edit_account_form_renders` |
| HP       | POST `/accounts/<id>` — updates account fields                                               | ✅ `test_update_account` |
| HP       | POST `/accounts/<id>/delete` — soft-deactivates account                                      | ✅ `test_deactivate_account` |
| HP       | POST `/accounts/<id>/reactivate` — reactivates account                                       | ✅ `test_reactivate_account` |
| SP       | POST `/accounts` — validation error (missing name)                                           | ✅ `test_create_account_validation_error` |
| SP       | POST `/accounts` — duplicate name → flash warning                                            | ✅ `test_create_account_duplicate_name` |
| SP       | POST `/accounts/<id>` — duplicate name → flash warning                                       | ✅ `test_update_account_duplicate_name` |
| IDOR     | GET `/accounts/<id>/edit` — other user's account → redirect                                  | ✅ `test_edit_other_users_account_redirects` |
| IDOR     | POST `/accounts/<id>` — other user's account → redirect                                      | ✅ `test_update_other_users_account_redirects` |
| IDOR     | POST `/accounts/<id>/delete` — other user's account → redirect                               | ✅ `test_deactivate_other_users_account_redirects` |
| SM       | POST `/accounts/<id>/delete` — account in use by active transfers → flash warning, no delete | ✅ `test_deactivate_account_with_active_transfers` |
| IDEM     | POST `/accounts` — double-submit same name → duplicate flash on 2nd                          | ✅ `test_create_account_double_submit` |

#### Anchor Balance (Grid Integration)

| Category | Tests Needed                                                            | Status |
| -------- | ----------------------------------------------------------------------- | ------ |
| HP       | PATCH `/accounts/<id>/inline-anchor` — updates balance, returns partial | ✅ `test_inline_anchor_update` |
| HP       | GET `/accounts/<id>/inline-anchor-form` — returns edit partial          | ✅ `test_inline_anchor_form_returns_partial` |
| HP       | GET `/accounts/<id>/inline-anchor-display` — returns display partial    | ✅ `test_inline_anchor_display_returns_partial` |
| HP       | PATCH `/accounts/<id>/true-up` — updates balance, creates history entry | ✅ `test_true_up_updates_balance` |
| SP       | PATCH `/accounts/<id>/true-up` — no current period → 400                | ✅ `test_true_up_no_current_period` |
| SP       | PATCH `/accounts/<id>/inline-anchor` — invalid amount → 400             | ✅ `test_inline_anchor_invalid_amount` |
| IDOR     | PATCH `/accounts/<id>/inline-anchor` — other user's account → 404       | ✅ `test_inline_anchor_other_users_account` |
| IDOR     | PATCH `/accounts/<id>/true-up` — other user's account → 404             | ✅ `test_true_up_other_users_account` |
| FIN      | True-up creates `AccountAnchorHistory` audit record                     | ✅ `test_true_up_updates_balance` (combined) |

#### Account Type Management

| Category | Tests Needed                                                     | Status |
| -------- | ---------------------------------------------------------------- | ------ |
| HP       | POST `/accounts/types` — creates new account type                | ✅ `test_create_account_type` |
| HP       | POST `/accounts/types/<id>` — renames account type               | ✅ `test_rename_account_type` |
| HP       | POST `/accounts/types/<id>/delete` — deletes unused type         | ✅ `test_delete_unused_account_type` |
| SP       | POST `/accounts/types` — duplicate name → flash warning          | ✅ `test_create_duplicate_account_type` |
| SP       | POST `/accounts/types/<id>/delete` — type in use → flash warning | ✅ `test_delete_account_type_in_use` |

**Estimated new tests: ~~30~~ 29 Done (FIN merged into HP true-up test)**

---

### 2.2 `routes/salary.py` — Priority P1 ✅

**Status: Complete (36 tests in `test_routes/test_salary.py`).**

#### Profile CRUD

| Category | Tests Needed                                                                  | Status |
| -------- | ----------------------------------------------------------------------------- | ------ |
| HP       | GET `/salary` — lists profiles with estimated net pay                         | ✅ `test_list_profiles` |
| HP       | GET `/salary/new` — renders create form                                       | ✅ `test_new_profile_form` |
| HP       | POST `/salary` — creates profile with linked template + recurrence + category | ✅ `test_create_profile` |
| HP       | GET `/salary/<id>/edit` — renders edit form                                   | ✅ `test_edit_profile_form` |
| HP       | POST `/salary/<id>` — updates profile, regenerates transactions               | ✅ `test_update_profile` |
| HP       | POST `/salary/<id>/delete` — deactivates profile + template                   | ✅ `test_delete_profile` |
| SP       | POST `/salary` — validation error → flash danger                              | ✅ `test_create_profile_validation_error` |
| SP       | POST `/salary` — no baseline scenario → flash danger                          | ✅ `test_create_profile_no_baseline_scenario` |
| SP       | POST `/salary` — no active account → flash danger                             | ✅ `test_create_profile_no_active_account` |
| IDOR     | GET `/salary/<id>/edit` — other user's profile → redirect                     | ✅ `test_edit_other_users_profile_redirects` |
| IDOR     | POST `/salary/<id>` — other user's profile → redirect                         | ✅ `test_update_other_users_profile_redirects` |
| IDOR     | POST `/salary/<id>/delete` — other user's profile → redirect                  | ✅ `test_delete_other_users_profile_redirects` |
| FIN      | Created template amount = `annual_salary / pay_periods_per_year`              | ✅ `test_create_profile_template_amount` |
| IDEM     | POST `/salary` — double-submit → 2nd attempt duplicate name or re-create      | ✅ `test_create_profile_double_submit` |

#### Raises

| Category | Tests Needed                                                                       | Status |
| -------- | ---------------------------------------------------------------------------------- | ------ |
| HP       | POST `/salary/<id>/raises` — adds raise, regenerates transactions                  | ✅ `test_add_raise` |
| HP       | POST `/salary/raises/<id>/delete` — removes raise, regenerates                     | ✅ `test_delete_raise` |
| SP       | POST `/salary/<id>/raises` — validation error (missing percentage and flat_amount) | ✅ `test_add_raise_validation_error` |
| SP       | POST `/salary/<id>/raises` — profile not found → flash danger                      | ✅ `test_add_raise_profile_not_found` |
| IDOR     | POST `/salary/raises/<id>/delete` — other user's raise → "Not authorized"          | ✅ `test_delete_other_users_raise_redirects` |
| HP       | HTMX response returns `_raises_section.html` partial                               | ✅ `test_add_raise_htmx_returns_partial` |

#### Deductions

| Category | Tests Needed                                                           | Status |
| -------- | ---------------------------------------------------------------------- | ------ |
| HP       | POST `/salary/<id>/deductions` — adds deduction, regenerates           | ✅ `test_add_deduction` |
| HP       | POST `/salary/deductions/<id>/delete` — removes deduction, regenerates | ✅ `test_delete_deduction` |
| SP       | POST `/salary/<id>/deductions` — validation error                      | ✅ `test_add_deduction_validation_error` |
| IDOR     | POST `/salary/deductions/<id>/delete` — other user's deduction         | ✅ `test_delete_other_users_deduction_redirects` |
| HP       | HTMX response returns `_deductions_section.html` partial               | ✅ `test_add_deduction_htmx_returns_partial` |
| BE       | Percentage input converted correctly (6 → 0.06)                        | ✅ `test_add_percentage_deduction_converts_input` |

#### Breakdown & Projection

| Category | Tests Needed                                                               | Status |
| -------- | -------------------------------------------------------------------------- | ------ |
| HP       | GET `/salary/<id>/breakdown/<period_id>` — renders breakdown               | ✅ `test_breakdown_renders` |
| HP       | GET `/salary/<id>/breakdown` — redirects to current period                 | ✅ `test_breakdown_current_redirects` |
| HP       | GET `/salary/<id>/projection` — renders multi-period projection            | ✅ `test_projection_renders` |
| SP       | GET `/salary/<id>/breakdown` — no current period → flash warning           | ✅ `test_breakdown_no_current_period` |
| IDOR     | GET `/salary/<id>/breakdown/<period_id>` — other user's profile → redirect | ✅ `test_breakdown_other_users_profile_redirects` |

#### Tax Config

| Category | Tests Needed                                                  | Status |
| -------- | ------------------------------------------------------------- | ------ |
| HP       | GET `/salary/tax-config` — renders tax config page            | ✅ `test_tax_config_page_renders` |
| HP       | POST `/salary/tax-config` — creates/updates state config      | ✅ `test_update_state_tax_config` |
| HP       | POST `/salary/fica-config` — creates/updates FICA config      | ✅ `test_update_fica_config` |
| SP       | POST `/salary/tax-config` — invalid state code → flash danger | ✅ `test_update_state_tax_invalid_code` |
| SP       | POST `/salary/fica-config` — validation error → flash danger  | ✅ `test_update_fica_validation_error` |

**Estimated new tests: ~~35~~ 36 Done**

---

### 2.3 `routes/transfers.py` — Priority P1

**Status: 3 IDOR tests only. Zero happy-path tests.** **[AUDIT GAP]** Happy paths, status
transitions, and HTMX partials untested for all 19 endpoints.

#### Template Management

| Category | Tests Needed                                                                 |
| -------- | ---------------------------------------------------------------------------- |
| HP       | GET `/transfers` — lists user's transfer templates                           |
| HP       | GET `/transfers/new` — renders create form with accounts                     |
| HP       | POST `/transfers` — creates template with recurrence, generates transfers    |
| HP       | GET `/transfers/<id>/edit` — renders edit form                               |
| HP       | POST `/transfers/<id>` — updates template, regenerates transfers             |
| HP       | POST `/transfers/<id>/delete` — deactivates template, soft-deletes transfers |
| HP       | POST `/transfers/<id>/reactivate` — reactivates template, restores transfers |
| SP       | POST `/transfers` — validation error → flash danger                          |
| SP       | POST `/transfers` — from_account == to_account → validation error            |
| IDOR     | POST `/transfers/<id>` — other user's template → redirect                    |
| IDOR     | POST `/transfers/<id>/delete` — other user's template → redirect             |
| IDEM     | POST `/transfers` — double-submit same name → unique constraint              |

#### Grid Cell Routes

| Category | Tests Needed                                               |
| -------- | ---------------------------------------------------------- |
| HP       | GET `/transfers/cell/<id>` — returns cell partial          |
| HP       | GET `/transfers/quick-edit/<id>` — returns quick-edit form |
| HP       | GET `/transfers/<id>/full-edit` — returns full-edit form   |
| IDOR     | GET `/transfers/cell/<id>` — other user's transfer → 404   |

#### Transfer Instance Operations

| Category | Tests Needed                                                                       |
| -------- | ---------------------------------------------------------------------------------- |
| HP       | PATCH `/transfers/instance/<id>` — updates amount                                  |
| HP       | POST `/transfers/instance/<id>/mark-done` — sets status to done                    |
| HP       | POST `/transfers/instance/<id>/cancel` — sets status to cancelled                  |
| HP       | DELETE `/transfers/instance/<id>` — soft-delete (template) or hard-delete (ad-hoc) |
| SM       | Template transfer → `is_override=True` on amount change                            |
| SM       | Cancel → `effective_amount` returns Decimal("0")                                   |
| IDOR     | PATCH `/transfers/instance/<id>` — other user's transfer → 404                     |

#### Ad-Hoc Creation

| Category | Tests Needed                                                                                |
| -------- | ------------------------------------------------------------------------------------------- |
| HP       | POST `/transfers/ad-hoc` — creates transfer, returns 201                                    |
| SP       | POST `/transfers/ad-hoc` — validation error → 400                                           |
| SP       | POST `/transfers/ad-hoc` — period not owned → 404                                           |
| IDEM     | POST `/transfers/ad-hoc` — double-submit → second succeeds (no unique constraint on ad-hoc) |

**Estimated new tests: 28**

---

### 2.4 `routes/savings.py` — Priority P1

**Status: 1 IDOR test only. Zero happy-path tests.** **[AUDIT GAP]** Dashboard calculation logic and
goal CRUD untested.

#### Dashboard

| Category | Tests Needed                                                  |
| -------- | ------------------------------------------------------------- |
| HP       | GET `/savings` — renders dashboard with goals and projections |
| BE       | No savings accounts → empty dashboard                         |
| BE       | No goals → dashboard still renders account projections        |
| FIN      | Balance projections (3mo, 6mo, 1yr) calculated correctly      |
| FIN      | Emergency fund metrics: avg expenses → months covered         |

#### Goal CRUD

| Category | Tests Needed                                                       |
| -------- | ------------------------------------------------------------------ |
| HP       | GET `/savings/goals/new` — renders form with accounts              |
| HP       | POST `/savings/goals` — creates goal, redirects to dashboard       |
| HP       | GET `/savings/goals/<id>/edit` — renders edit form                 |
| HP       | POST `/savings/goals/<id>` — updates goal fields                   |
| HP       | POST `/savings/goals/<id>/delete` — soft-deactivates goal          |
| SP       | POST `/savings/goals` — validation error                           |
| SP       | POST `/savings/goals` — target_amount = 0 → validation error       |
| IDOR     | GET `/savings/goals/<id>/edit` — other user's goal → redirect      |
| IDOR     | POST `/savings/goals/<id>` — other user's goal → redirect          |
| IDOR     | POST `/savings/goals/<id>/delete` — other user's goal → redirect   |
| IDEM     | POST `/savings/goals` — duplicate name+account → unique constraint |

**Estimated new tests: 16**

---

### 2.5 `routes/templates.py` — Priority P2

**Status: 2 IDOR tests only. Zero happy-path tests.**

#### Template CRUD

| Category | Tests Needed                                                                                |
| -------- | ------------------------------------------------------------------------------------------- |
| HP       | GET `/templates` — lists user's templates                                                   |
| HP       | GET `/templates/new` — renders form with categories, accounts, patterns                     |
| HP       | POST `/templates` — creates template with recurrence, generates transactions                |
| HP       | GET `/templates/<id>/edit` — renders edit form                                              |
| HP       | POST `/templates/<id>` — updates template, regenerates                                      |
| HP       | POST `/templates/<id>/delete` — deactivates, soft-deletes transactions                      |
| HP       | POST `/templates/<id>/reactivate` — reactivates, restores transactions                      |
| SP       | POST `/templates` — validation error (missing name)                                         |
| SP       | POST `/templates/<id>` — validation error                                                   |
| IDOR     | POST `/templates/<id>` — other user's template → redirect                                   |
| IDOR     | POST `/templates/<id>/delete` — other user's template → redirect                            |
| SM       | Delete → deactivate + soft-delete projected txns                                            |
| SM       | Reactivate → restore soft-deleted + regenerate from rule                                    |
| SM       | Update triggers `RecurrenceConflict` → flash warning                                        |
| IDEM     | POST `/templates` — double-submit creates duplicate (no unique constraint on template name) |

#### Preview Recurrence

| Category | Tests Needed                                                                                       |
| -------- | -------------------------------------------------------------------------------------------------- |
| HP       | GET `/templates/preview-recurrence?recurrence_pattern=monthly&day_of_month=15` → returns HTML list |
| BE       | Pattern = "once" → "No preview" message                                                            |
| BE       | Unknown pattern → "Unknown pattern" message                                                        |
| BE       | No periods → "No pay periods" message                                                              |
| BE       | No matching periods → "No matching periods" message                                                |

**Estimated new tests: 20**

---

### 2.6 `routes/categories.py` — Priority P2

**Status: Zero tests.**

| Category | Tests Needed                                                           |
| -------- | ---------------------------------------------------------------------- |
| HP       | GET `/categories` — renders list grouped by group_name                 |
| HP       | POST `/categories` — creates category, redirects                       |
| HP       | POST `/categories` — HTMX request → returns partial HTML               |
| HP       | POST `/categories/<id>/delete` — deletes unused category               |
| SP       | POST `/categories` — validation error                                  |
| SP       | POST `/categories` — duplicate group+item → flash warning              |
| SP       | POST `/categories/<id>/delete` — in use by template → flash warning    |
| SP       | POST `/categories/<id>/delete` — in use by transaction → flash warning |
| IDOR     | POST `/categories/<id>/delete` — other user's category → flash danger  |
| IDEM     | POST `/categories` — double-submit same name → duplicate warning       |

**Estimated new tests: 10**

---

### 2.7 `routes/pay_periods.py` — Priority P2

**Status: Zero tests.**

| Category | Tests Needed                                                        |
| -------- | ------------------------------------------------------------------- |
| HP       | GET `/pay-periods/generate` — renders form                          |
| HP       | POST `/pay-periods/generate` — creates periods, redirects to grid   |
| SP       | POST `/pay-periods/generate` — invalid start_date → 422 with errors |
| SP       | POST `/pay-periods/generate` — cadence_days=0 → validation error    |
| BE       | `num_periods=1` → creates single period                             |
| IDEM     | POST `/pay-periods/generate` — double-submit → duplicates skipped   |

**Estimated new tests: 6**

---

### 2.8 `routes/settings.py` — Priority P2

**Status: Zero tests.**

| Category | Tests Needed                                                    |
| -------- | --------------------------------------------------------------- |
| HP       | GET `/settings` — renders settings page                         |
| HP       | GET `/settings` — auto-creates UserSettings if missing          |
| HP       | POST `/settings` — updates all three fields                     |
| SP       | POST `/settings` — non-numeric grid_periods → flash danger      |
| SP       | POST `/settings` — invalid Decimal for inflation → flash danger |
| SP       | POST `/settings` — non-numeric threshold → flash danger         |
| BE       | Blank fields skipped (partial update)                           |

**Estimated new tests: 7**

---

### 2.9 `routes/grid.py` — Priority P2

**Status: Partially covered (8 tests for `index` + transaction CRUD).** Missing `balance_row`
endpoint.

| Category | Tests Needed                                                   |
| -------- | -------------------------------------------------------------- |
| HP       | GET `/grid/balance-row` — returns recalculated balance partial |
| BE       | GET `/grid/balance-row` — no current period → 204 empty        |
| BE       | GET `/grid/balance-row` — no scenario/account → empty balances |
| BE       | GET `/` — `periods` query param out of range → clipped         |

**Estimated new tests: 4**

---

### 2.10 `routes/transactions.py` — Priority P2

**Status: Well covered for IDOR (15 tests) and basic CRUD (5 tests).** Some state transitions and
edge cases remain.

| Category | Tests Needed                                                         |
| -------- | -------------------------------------------------------------------- |
| SM       | `mark_done` with `actual_amount` provided → sets actual, status=done |
| SM       | `mark_done` without `actual_amount` → status only                    |
| SM       | `cancel_transaction` → status=cancelled, `effective_amount`=0        |
| SM       | `mark_credit` → creates payback in next period                       |
| SM       | `unmark_credit` → reverts to projected, deletes payback              |
| HP       | `create_transaction` (full form) → creates with all fields           |
| BE       | `create_inline` when no baseline scenario → 400                      |
| SM       | Delete template-linked txn → soft-delete (`is_deleted=True`)         |
| SM       | Delete ad-hoc txn → hard-delete                                      |

**Estimated new tests: 9**

---

### 2.11 `routes/auth.py` — Priority P3

**Status: Good coverage (5 tests).** Minor edge cases.

| Category | Tests Needed                                             |
| -------- | -------------------------------------------------------- |
| BE       | Login with disabled account → error message              |
| BE       | Rate limiting after 5 failed attempts (may need mocking) |

**Estimated new tests: 2**

---

## 3. Models

### 3.1 `models/transaction.py` — Priority P0

**Status: `effective_amount` Decimal return covered (2 tests).**

| Category | Tests Needed                                                               |
| -------- | -------------------------------------------------------------------------- |
| HP       | `effective_amount` returns `estimated_amount` when projected               |
| HP       | `effective_amount` returns `actual_amount` when done and actual is set     |
| HP       | `effective_amount` returns `estimated_amount` when done and actual is None |
| HP       | `is_income` returns True for income type                                   |
| HP       | `is_expense` returns True for expense type                                 |

**Estimated new tests: 5**

---

### 3.2 `models/transfer.py` — Priority P0

**Status: `effective_amount` Decimal return covered (2 tests).**

| Category | Tests Needed                                       |
| -------- | -------------------------------------------------- |
| HP       | `effective_amount` returns `amount` when projected |
| HP       | `effective_amount` returns `amount` when done      |

**Estimated new tests: 2**

---

### 3.3 `models/category.py` — Priority P3

| Category | Tests Needed                                |
| -------- | ------------------------------------------- |
| HP       | `display_name` returns "group: item" format |

**Estimated new tests: 1**

---

### 3.4 `models/pay_period.py` — Priority P3

| Category | Tests Needed                                     |
| -------- | ------------------------------------------------ |
| HP       | `label` returns formatted "MM/DD – MM/DD" string |

**Estimated new tests: 1**

---

### 3.5 `models/paycheck_deduction.py` (PaycheckBreakdown dataclass) — Priority P2

| Category | Tests Needed                                     |
| -------- | ------------------------------------------------ |
| HP       | `total_pre_tax` sums pre-tax deduction amounts   |
| HP       | `total_post_tax` sums post-tax deduction amounts |
| HP       | `total_taxes` = federal + state + ss + medicare  |
| BE       | Empty deduction lists → totals are Decimal("0")  |

**Estimated new tests: 4**

---

## 4. Schemas

### 4.1 `schemas/validation.py` — Priority P1

**Status: Zero dedicated tests.** **[AUDIT GAP]** Schema validation is the first line of defense
against malformed input. No schema is tested in isolation.

#### Strategy

Test each schema's `load()` method directly for:

- Required field enforcement (missing → ValidationError)
- Type coercion (string "100.00" → Decimal)
- Range validation (amount >= 0, month 1-12)
- `@pre_load` empty-string stripping
- `@validates_schema` cross-field rules

#### Schemas to Test

| Schema                          | Key Validations to Test                                  |
| ------------------------------- | -------------------------------------------------------- |
| `TransactionCreateSchema`       | Required fields; `estimated_amount >= 0`                 |
| `TransactionUpdateSchema`       | All optional; `@pre_load` strips empty strings           |
| `InlineTransactionCreateSchema` | Required fields; `@pre_load`                             |
| `TemplateCreateSchema`          | Required fields; recurrence fields optional              |
| `TemplateUpdateSchema`          | All optional; `effective_from` Date parsing              |
| `TransferTemplateCreateSchema`  | `from != to` validator; `default_amount > 0`             |
| `TransferCreateSchema`          | `from != to` validator; `amount > 0`                     |
| `TransferUpdateSchema`          | `amount > 0`; `@pre_load`                                |
| `SavingsGoalCreateSchema`       | `target_amount > 0`; `@pre_load`                         |
| `SalaryProfileCreateSchema`     | Required fields; `pay_periods_per_year` in {12,24,26,52} |
| `RaiseCreateSchema`             | Exactly one of percentage/flat_amount; month 1-12        |
| `DeductionCreateSchema`         | Required fields; `deductions_per_year` in {12,24,26}     |
| `FicaConfigSchema`              | All required; `@pre_load`                                |
| `AccountCreateSchema`           | Required `name`, `account_type_id`; `@pre_load`          |
| `PayPeriodGenerateSchema`       | `num_periods` 1-260; `cadence_days` 1-365                |
| `CategoryCreateSchema`          | Required `group_name`, `item_name`                       |

**Estimated new tests: 40**

---

## 5. Integration / Cross-Cutting Tests

### 5.1 End-to-End Workflows — Priority P1

These tests verify multi-step workflows that span services and routes.

| Test                         | Description                                                                                |
| ---------------------------- | ------------------------------------------------------------------------------------------ |
| Salary → Grid                | Create profile → verify income transactions appear in grid periods                         |
| Template → Recurrence → Grid | Create template with monthly recurrence → verify transactions generated in correct periods |
| Transfer → Balance           | Create transfer template → verify balance calculator includes transfer effects             |
| Credit → Payback → Balance   | Mark expense as credit → verify payback in next period → verify balance unaffected         |
| Anchor True-Up → Balance     | Change anchor balance → verify all downstream period balances recalculate                  |
| Carry Forward                | Create projected txns → carry forward → verify moved to target period                      |

**Estimated new tests: 6**

---

### 5.2 Idempotency — Priority P2

**[AUDIT GAP]** Double-submit on POST routes untested.

Every POST endpoint should be tested for double-submission behavior:

| Route                          | Expected Behavior                                          |
| ------------------------------ | ---------------------------------------------------------- |
| POST `/login`                  | Second login succeeds (session refresh)                    |
| POST `/accounts`               | Duplicate name → flash warning, no second account          |
| POST `/salary`                 | Duplicate profile name → unique constraint error           |
| POST `/templates`              | Creates duplicate (no unique constraint — verify behavior) |
| POST `/transfers`              | Duplicate name → unique constraint error                   |
| POST `/savings/goals`          | Duplicate name+account → unique constraint error           |
| POST `/categories`             | Duplicate group+item → flash warning                       |
| POST `/pay-periods/generate`   | Duplicate dates silently skipped                           |
| POST `/salary/<id>/raises`     | Creates duplicate raise (verify this is acceptable)        |
| POST `/salary/<id>/deductions` | Creates duplicate deduction (verify this is acceptable)    |

**Estimated new tests: 10**

---

## 6. Test Count Summary

| Module                                      | Priority | Estimated Tests |
| ------------------------------------------- | -------- | --------------- |
| **Services**                                |          |                 |
| paycheck_calculator (pipeline + deductions) | P0       | 30              |
| recurrence_engine (patterns + regen)        | P0       | 22              |
| balance_calculator (edge cases)             | P0       | 10              |
| transfer_recurrence                         | P0       | 8               |
| pay_period_service                          | P1       | 16              |
| savings_goal_service                        | P1       | 14              |
| auth_service                                | P2       | ~~7~~ ✅ Done   |
| carry_forward_service                       | P2       | ~~9~~ ✅ Done   |
| credit_workflow (gaps)                      | P2       | ~~3~~ ✅ Done   |
| **Routes**                                  |          |                 |
| salary.py                                   | P1       | ~~35~~ 36 ✅ Done |
| accounts.py                                 | P1       | ~~30~~ 29 ✅ Done |
| transfers.py                                | P1       | 28              |
| templates.py                                | P2       | 20              |
| savings.py                                  | P1       | 16              |
| categories.py                               | P2       | 10              |
| settings.py                                 | P2       | 7               |
| pay_periods.py                              | P2       | 6               |
| grid.py (gaps)                              | P2       | 4               |
| transactions.py (gaps)                      | P2       | 9               |
| auth.py (gaps)                              | P3       | 2               |
| **Models**                                  |          |                 |
| transaction.py                              | P0       | 5               |
| transfer.py                                 | P0       | 2               |
| PaycheckBreakdown                           | P2       | 4               |
| category.py, pay_period.py                  | P3       | 2               |
| **Schemas**                                 |          |                 |
| validation.py (all schemas)                 | P1       | 40              |
| **Integration**                             |          |                 |
| End-to-end workflows                        | P1       | 6               |
| Idempotency                                 | P2       | 10              |
|                                             |          |                 |
| **Total new tests**                         |          | **~355**        |
| **Existing tests**                          |          | **105**         |
| **Grand total**                             |          | **~460**        |

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

1. **P0 services** — paycheck_calculator pipeline, recurrence engine patterns, balance_calculator
   edges, transfer_recurrence
2. **P0 models** — transaction/transfer effective_amount full coverage
3. **P1 schemas** — all Marshmallow schemas in isolation
4. **P1 routes** — ~~salary~~ ✅, ~~accounts~~ ✅, transfers, savings (happy + IDOR)
5. **P1 services** — pay_period_service, savings_goal_service
6. **P1 integration** — end-to-end workflows
7. **P2 routes** — templates, categories, pay_periods, settings, grid gaps
8. **P2 services** — ~~auth_service~~ ✅, ~~carry_forward~~ ✅, ~~credit_workflow gaps~~ ✅
9. **P2 idempotency** — double-submit tests
10. **P3 models + routes** — computed properties, rate limiting
