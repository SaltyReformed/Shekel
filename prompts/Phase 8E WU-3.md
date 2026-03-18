# Phase 8E WU-3: Data Isolation Test Fixtures

## Context

This is a personal finance application (Shekel) that manages real money
through pay-period budgeting. Bugs in this code have real financial
consequences. There is no QA team. Every line you write must be correct
the first time. Do not take shortcuts. Do not write placeholder or
stub implementations.

Read CLAUDE.md before doing anything. Read tests/TEST_PLAN.md before
writing any tests. Follow every convention described in both files.

WU-1 (registration) and WU-2 (ownership helpers + defense-in-depth) are
complete.

## What You Are Building

Test infrastructure for two-user data isolation testing. This is WU-3
of Phase 8E. You are creating pytest fixtures in `tests/conftest.py`
that produce a second test user with a complete, independent dataset,
plus a validation test file that proves every fixture works correctly
before WU-4 and WU-5 consume them.

The plan says "No new test file for this WU -- the fixtures are tested
implicitly by WU-4 and WU-5." We are going beyond the plan. A broken
fixture that surfaces as 20 cascading failures in WU-4 is a debugging
nightmare. Instead, you will create an explicit fixture validation test
file that catches problems immediately.

## Why This Matters for Financial Safety

WU-4 and WU-5 depend entirely on these fixtures to verify that User A's
money data is invisible to User B. If a fixture creates data with wrong
foreign keys, or if two users accidentally share a scenario or account,
the isolation tests will produce false positives ("passes" but is not
actually testing what it claims). That means a data leak between users
could go undetected, and User B might see User A's bank balance, salary,
or mortgage details.

## Pre-Existing Infrastructure You Must Read First

Before writing any code, read these files IN FULL. Do not skim.

1. `tests/conftest.py` -- Read the ENTIRE file. Study every existing
   fixture. Pay special attention to:
   - The import block at the top (you will add imports here).
   - `seed_user` fixture: the structure of the returned dict, the exact
     categories created, the account type lookup pattern, the commit
     at the end.
   - `seed_periods` fixture: how it generates periods, sets the anchor
     period, and commits.
   - `auth_client` fixture: how it creates a test client and logs in.
   - The `db` fixture: how it truncates tables between tests. This
     means every fixture must recreate all its data each time.
   - `_seed_ref_tables()`: what reference data exists (AccountType
     names, Status names, TransactionType names, RecurrencePattern
     names, FilingStatus names, etc.).

2. `app/models/transfer_template.py` -- Study the model. Note the
   check constraint: `from_account_id != to_account_id`. This means
   creating a transfer template requires TWO different accounts.

3. `app/models/transfer.py` -- Study the model. Same constraint:
   `from_account_id != to_account_id`. Also needs `user_id`,
   `pay_period_id`, `scenario_id`, `status_id`.

4. `app/models/salary_profile.py` -- Study the model. Note required
   fields: `user_id`, `scenario_id`, `filing_status_id`, `name`,
   `annual_salary`, `state_code`. Also note the unique constraint on
   `(user_id, scenario_id, name)`.

5. `app/models/savings_goal.py` -- Study the model. Note required
   fields: `user_id`, `account_id`, `name`, `target_amount`.

6. `app/models/transaction.py` -- Study the model. Note required
   fields: `pay_period_id`, `scenario_id`, `status_id`, `name`,
   `category_id`, `transaction_type_id`, `estimated_amount`.
   Transaction does NOT have a direct `user_id` column. It is scoped
   indirectly via `pay_period.user_id`.

## Critical Design Requirement: Distinguishable Data

WU-4 isolation tests work by checking that User A's page contains
User A's data and does NOT contain User B's data. This only works if
the two users' data has distinguishable names and amounts. Every object
created for User B must have a name and amount that is DIFFERENT from
User A's corresponding object.

User A (seed_user / seed_full_user_data):

- Account: "Checking" with anchor balance $1,000.00 (already exists)
- Template: "Rent Payment" at $1,200.00
- Transaction: "Rent Payment" at $1,200.00
- Savings Goal: "Emergency Fund" at $10,000.00

User B (seed_second_user / seed_full_second_user_data):

- Account: "Checking" with anchor balance $2,000.00
- Template: "Second User Rent" at $900.00
- Transaction: "Second User Rent" at $900.00
- Savings Goal: "Vacation Fund" at $5,000.00

## Gap in the Plan: Missing Transfer and Salary Data

The plan's `seed_full_user_data` fixture creates templates, transactions,
and a savings goal, but does NOT create transfer templates, transfer
instances, or salary profiles. However, WU-4 includes
`TestTransfersIsolation` and `TestSalaryIsolation` which check that
each user sees only their own transfers and salary profiles.

If the fixtures contain no transfer or salary data, those isolation tests
become meaningless (both users see empty pages, which trivially
"passes"). You must add transfer and salary data to the full data
fixtures.

**Transfer template requirement:** `TransferTemplate` has a
`CheckConstraint("from_account_id != to_account_id")`, which means
each user needs at least two accounts. Add a "Savings" account
(account_type "savings") to each user's full data fixture.

**Salary profile requirement:** `SalaryProfile` needs `scenario_id`,
`filing_status_id`, `name`, and `annual_salary`. Use the baseline
scenario and the "single" filing status.

## Implementation Specification

### 1. Add Imports to `tests/conftest.py`

Read the existing import block at the top of conftest.py. Add ONLY
the imports that are missing. Do not duplicate any existing import.

Expected new imports (verify each is not already present):

```python
from app.models.transfer_template import TransferTemplate
from app.models.transfer import Transfer
from app.models.salary_profile import SalaryProfile
from app.models.savings_goal import SavingsGoal
```

Check whether `TransactionTemplate`, `RecurrenceRule`,
`RecurrencePattern`, `TransactionType`, `Status`, etc. are already
imported. They likely are. Do not re-import them.

### 2. Fixture: `seed_second_user`

Add after the `auth_client` fixture. This mirrors `seed_user` exactly
in structure but creates a completely independent user.

```python
@pytest.fixture()
def seed_second_user(app, db):
```

Create:

- User: email="second@shekel.local", password="secondpass12"
  (note: exactly 12 chars, the minimum),
  display_name="Second User"
- UserSettings: user_id=user.id (model defaults)
- Account: user_id=user.id, account_type="checking", name="Checking",
  current_anchor_balance=Decimal("2000.00")
- Scenario: user_id=user.id, name="Baseline", is_baseline=True
- Categories: same 5 as seed_user:
  ("Income", "Salary"), ("Home", "Rent"), ("Auto", "Car Payment"),
  ("Family", "Groceries"), ("Credit Card", "Payback")

Return dict with keys: user, settings, account, scenario, categories.
The `categories` value must be a dict keyed by item_name (e.g.,
`{c.item_name: c for c in categories}`), matching `seed_user`'s
return structure.

IMPORTANT: Call `db.session.commit()` at the end, matching `seed_user`'s
pattern. The `db` fixture truncates between tests, so every fixture
must commit its data to make it visible to other fixtures and to the
test client's requests.

### 3. Fixture: `seed_second_periods`

```python
@pytest.fixture()
def seed_second_periods(app, db, seed_second_user):
```

Generate 10 pay periods for the second user starting 2026-01-02 with
14-day cadence. Set the anchor period to periods[0]. Commit.

Return the list of PayPeriod objects.

This mirrors `seed_periods` exactly. Use the same
`pay_period_service.generate_pay_periods()` call.

### 4. Fixture: `second_auth_client`

```python
@pytest.fixture()
def second_auth_client(app, db, seed_second_user):
```

CRITICAL: Must create a NEW `app.test_client()` instance. Do NOT
reuse the `client` fixture. Flask test clients carry session state,
and sharing a client between two users would cause session conflicts
(User A's session bleeds into User B's requests).

```python
second_client = app.test_client()
second_client.post("/login", data={
    "email": "second@shekel.local",
    "password": "secondpass12",
})
return second_client
```

Note: The plan's fixture depends on `client` as a parameter. Remove
that dependency. The fixture should depend only on `app`, `db`, and
`seed_second_user`. Creating `app.test_client()` directly is cleaner
and avoids pulling in the `client` fixture's session state.

### 5. Fixture: `seed_full_user_data`

```python
@pytest.fixture()
def seed_full_user_data(app, db, seed_user, seed_periods):
```

Creates a rich dataset for User A. This is the data WU-4 will search
for in page responses to verify isolation.

Create ALL of the following (order matters for FK dependencies):

**a) Transaction template + transaction (from the plan):**

- RecurrenceRule: user_id, pattern_id=every_period
- TransactionTemplate: user_id, account_id=checking, category_id=Rent,
  recurrence_rule_id, transaction_type_id=expense, name="Rent Payment",
  default_amount=Decimal("1200.00")
- Transaction: template_id, pay_period_id=periods[0], scenario_id,
  status_id=projected, name="Rent Payment", category_id=Rent,
  transaction_type_id=expense, estimated_amount=Decimal("1200.00")

**b) Savings goal (from the plan):**

- SavingsGoal: user_id, account_id=checking, name="Emergency Fund",
  target_amount=Decimal("10000.00")

**c) Savings account + transfer template (NEW, not in the plan):**

- Account: user_id, account_type="savings", name="Savings",
  current_anchor_balance=Decimal("500.00")
- Set savings_account.current_anchor_period_id = periods[0].id
- TransferTemplate: user_id, from_account_id=checking.id,
  to_account_id=savings.id, name="Monthly Savings",
  default_amount=Decimal("200.00")
  (no recurrence_rule_id needed -- can be None for a one-off template)

**d) Salary profile (NEW, not in the plan):**

- Look up FilingStatus with name="single"
- SalaryProfile: user_id, scenario_id=baseline, filing_status_id,
  name="Day Job", annual_salary=Decimal("75000.00"), state_code="NC"

Return a dict that merges `seed_user` plus all new objects:

```python
return {
    **seed_user,
    "periods": periods,
    "template": template,
    "transaction": txn,
    "savings_goal": goal,
    "recurrence_rule": rule,
    "savings_account": savings_account,
    "transfer_template": transfer_tpl,
    "salary_profile": salary_profile,
}
```

### 6. Fixture: `seed_full_second_user_data`

```python
@pytest.fixture()
def seed_full_second_user_data(app, db, seed_second_user, seed_second_periods):
```

Mirrors `seed_full_user_data` but for User B with DIFFERENT names and
amounts:

- TransactionTemplate: name="Second User Rent",
  default_amount=Decimal("900.00")
- Transaction: name="Second User Rent",
  estimated_amount=Decimal("900.00")
- SavingsGoal: name="Vacation Fund",
  target_amount=Decimal("5000.00")
- Account (savings): name="Savings",
  current_anchor_balance=Decimal("300.00")
- TransferTemplate: name="Bi-Weekly Savings",
  default_amount=Decimal("150.00")
- SalaryProfile: name="Second Job",
  annual_salary=Decimal("60000.00"), state_code="NC"

Return the same dict structure as `seed_full_user_data`.

## Fixture Validation Tests

### 7. New File: `tests/test_integration/test_fixture_validation.py`

Create the `tests/test_integration/` directory if it does not exist.
Create `tests/test_integration/__init__.py` if it does not exist.

This file validates that every fixture creates the expected data
correctly. Each test catches a different category of fixture bug.

**Required tests:**

```
class TestSeedSecondUser:
```

```
test_creates_independent_user
```

Use `seed_user` and `seed_second_user`. Assert they are different
User objects (different `id`, different `email`). Assert
seed_second_user email is "second@shekel.local". Assert display_name
is "Second User".

```
test_has_own_settings
```

Use `seed_second_user`. Query UserSettings for user_id. Assert exactly
one row exists. Assert it is the same object as
`seed_second_user["settings"]`.

```
test_has_own_account
```

Use `seed_user` and `seed_second_user`. Assert
`seed_user["account"].id != seed_second_user["account"].id`. Assert
both are checking accounts. Assert second user's anchor balance is
Decimal("2000.00").

```
test_has_own_scenario
```

Use `seed_user` and `seed_second_user`. Assert scenarios have
different IDs. Assert both are baselines (`is_baseline=True`). Assert
they belong to different user_ids.

```
test_has_own_categories
```

Use `seed_user` and `seed_second_user`. Assert both have 5 categories.
Assert no category ID appears in both sets. Query the database to
verify User B's categories have user_id=second_user.id.

```
test_no_shared_foreign_keys
```

Use `seed_user` and `seed_second_user`. Assert:

- seed_user["account"].user_id != seed_second_user["user"].id
- seed_second_user["account"].user_id != seed_user["user"].id
- seed_user["scenario"].user_id == seed_user["user"].id
- seed_second_user["scenario"].user_id == seed_second_user["user"].id
  This catches the bug where a fixture accidentally assigns User A's ID
  to User B's object.

```
class TestSeedSecondPeriods:
```

```
test_creates_10_periods
```

Use `seed_second_periods`. Assert len is 10.

```
test_periods_belong_to_second_user
```

Use `seed_second_user` and `seed_second_periods`. Assert every period's
user_id equals seed_second_user["user"].id.

```
test_periods_independent_from_first_user
```

Use `seed_user`, `seed_periods`, `seed_second_user`, `seed_second_periods`.
Collect all period IDs from both users. Assert no overlap (the
intersection of the two ID sets is empty).

```
test_anchor_period_set
```

Use `seed_second_user` and `seed_second_periods`. Reload the account
from the database. Assert `account.current_anchor_period_id` equals
`seed_second_periods[0].id`.

```
class TestSecondAuthClient:
```

```
test_second_client_is_authenticated
```

Use `seed_second_user` and `second_auth_client`. GET a protected page
(e.g., `/settings`). Assert 200 (not 302 redirect to login).

```
test_second_client_is_different_user
```

Use `seed_user`, `auth_client`, `seed_second_user`, `second_auth_client`.
GET `/settings` with both clients. Assert both return 200. This
verifies both sessions are active simultaneously and do not interfere.

```
test_second_client_independent_session
```

Use `seed_user`, `auth_client`, `seed_second_user`, `second_auth_client`.
POST `/logout` with `second_auth_client`. Then GET `/settings` with
`auth_client`. Assert auth_client still gets 200 (User A's session
survives User B's logout).

```
class TestSeedFullUserData:
```

```
test_contains_all_expected_keys
```

Use `seed_full_user_data`. Assert the returned dict contains ALL of
these keys: "user", "settings", "account", "scenario", "categories",
"periods", "template", "transaction", "savings_goal",
"recurrence_rule", "savings_account", "transfer_template",
"salary_profile". Assert none of the values are None.

```
test_template_belongs_to_user
```

Use `seed_full_user_data`. Assert
`data["template"].user_id == data["user"].id`.

```
test_transaction_in_first_period
```

Use `seed_full_user_data`. Assert
`data["transaction"].pay_period_id == data["periods"][0].id`.

```
test_transaction_linked_to_template
```

Use `seed_full_user_data`. Assert
`data["transaction"].template_id == data["template"].id`.

```
test_savings_goal_belongs_to_user
```

Use `seed_full_user_data`. Assert
`data["savings_goal"].user_id == data["user"].id`.

```
test_transfer_template_accounts_valid
```

Use `seed_full_user_data`. Assert
`data["transfer_template"].from_account_id == data["account"].id`.
Assert `data["transfer_template"].to_account_id == data["savings_account"].id`.
Assert from != to (constraint check).

```
test_salary_profile_belongs_to_user
```

Use `seed_full_user_data`. Assert
`data["salary_profile"].user_id == data["user"].id`.
Assert `data["salary_profile"].scenario_id == data["scenario"].id`.

```
test_all_amounts_are_decimal
```

Use `seed_full_user_data`. Assert each monetary value is a
`Decimal` instance:

- template.default_amount
- transaction.estimated_amount
- savings_goal.target_amount
- transfer_template.default_amount
- salary_profile.annual_salary
- account.current_anchor_balance
  This catches accidental float usage in fixtures.

```
class TestSeedFullSecondUserData:
```

```
test_contains_all_expected_keys
```

Same as above but for `seed_full_second_user_data`.

```
test_no_shared_objects_between_users
```

Use `seed_full_user_data` and `seed_full_second_user_data`. Assert
every object ID is unique across users:

- user IDs differ
- account IDs differ (both checking and savings)
- scenario IDs differ
- template IDs differ
- transaction IDs differ
- savings goal IDs differ
- transfer template IDs differ
- salary profile IDs differ
- No period ID appears in both period lists

```
test_distinguishable_names
```

Use `seed_full_user_data` and `seed_full_second_user_data`. Assert:

- data_a["template"].name != data_b["template"].name
- data_a["transaction"].name != data_b["transaction"].name
- data_a["savings_goal"].name != data_b["savings_goal"].name
- data_a["transfer_template"].name != data_b["transfer_template"].name
- data_a["salary_profile"].name != data_b["salary_profile"].name
  This is critical: if names match, WU-4 isolation tests cannot
  distinguish whose data appears on a page.

```
test_distinguishable_amounts
```

Use `seed_full_user_data` and `seed_full_second_user_data`. Assert:

- data_a["template"].default_amount != data_b["template"].default_amount
- data_a["transaction"].estimated_amount != data_b["transaction"].estimated_amount
- data_a["savings_goal"].target_amount != data_b["savings_goal"].target_amount
- data_a["account"].current_anchor_balance != data_b["account"].current_anchor_balance

```
class TestBothFullFixturesTogether:
```

```
test_both_fixtures_coexist
```

Use `seed_full_user_data` and `seed_full_second_user_data` in the
same test. Query the database for total User count. Assert exactly 2.
Query for total Account count (across both users). Assert at least 4
(2 checking + 2 savings). Query for total TransactionTemplate count.
Assert exactly 2. This verifies both fixtures can be invoked together
without FK conflicts, unique constraint violations, or other database
errors.

```
test_database_isolation_query
```

Use `seed_full_user_data` and `seed_full_second_user_data`. Run the
same query pattern that the grid route uses:

```python
from app.models.transaction import Transaction
from app.models.pay_period import PayPeriod

user_a_id = data_a["user"].id
user_a_period_ids = [
    p.id for p in db.session.query(PayPeriod)
    .filter_by(user_id=user_a_id).all()
]
user_a_txns = (
    db.session.query(Transaction)
    .filter(Transaction.pay_period_id.in_(user_a_period_ids))
    .all()
)
```

Assert all returned transactions have names matching User A's data.
Assert none have User B's transaction name. Repeat for User B.
This is the exact query pattern that WU-4 will rely on.

## Execution Checklist

After implementing everything, run these checks in this exact order:

1. Read `tests/conftest.py` top-to-bottom. Verify:
   - No duplicate imports.
   - All new fixtures are placed after `auth_client`.
   - All new fixtures have docstrings.
   - All monetary values use `Decimal`, never `float`.
   - `seed_second_user` commits at the end.
   - `seed_second_periods` commits at the end.
   - `seed_full_user_data` commits at the end.
   - `seed_full_second_user_data` commits at the end.

2. Verify directory structure exists:
   - `tests/test_integration/__init__.py`

3. `pylint tests/conftest.py` -- fix all issues.

4. `pytest tests/test_integration/test_fixture_validation.py -v` --
   all must pass. If any fail, the fixture is broken. Fix the
   fixture, not the test.

5. `pytest` -- the FULL suite. All existing ~900+ tests plus
   WU-1 and WU-2 tests plus your new validation tests must pass.
   Zero failures. Zero errors.

The most common fixture bugs:

- Missing `db.session.flush()` before accessing `.id` on a new object.
- Missing `db.session.commit()` at the end (data invisible to other
  fixtures that run in the same test).
- Wrong `user_id` on a child object (copy-paste from User A fixture
  without updating to User B).
- Violating a unique constraint (e.g., two SalaryProfiles with the
  same name for the same user+scenario).
- Violating a check constraint (e.g., from_account_id == to_account_id
  on TransferTemplate).

## Things You Must NOT Do

- Do not modify any existing fixture in conftest.py. Existing fixtures
  (`seed_user`, `seed_periods`, `auth_client`) must remain unchanged.
  Add new fixtures AFTER the existing ones.
- Do not create any migration file. No schema changes are needed.
- Do not add any new Python packages to requirements.txt.
- Do not modify any application code (routes, services, models).
  This WU is purely test infrastructure.
- Do not write `pass` or `TODO` in any function or test body.
- Do not use `assert True` or any assertion that always passes.
- Do not mock the database. Tests use the real PostgreSQL test database.
- Do not use `float` for any numeric value. Use `Decimal`.
- Do not use em dashes or en dashes anywhere (use hyphens instead).
- Do not create data for User B that has the same name as User A's
  data. Every name must be distinguishable for WU-4 isolation tests.
