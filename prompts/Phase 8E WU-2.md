# Phase 8E WU-2: Ownership Helpers and Service Defense-in-Depth

## Context

This is a personal finance application (Shekel) that manages real money
through pay-period budgeting. Bugs in this code have real financial
consequences. There is no QA team. Every line you write must be correct
the first time. Do not take shortcuts. Do not leave edge cases
untested. Do not write placeholder or stub implementations. Every test
must assert something meaningful and must actually exercise the code
path it claims to test.

Read CLAUDE.md before doing anything. Read tests/TEST_PLAN.md before
writing any tests. Follow every convention described in both files.

WU-1 (registration) is complete. WU-2 is independent of WU-1 but
builds on the existing codebase.

## What You Are Building

Three things:

1. **Reusable ownership verification helpers** (`app/utils/auth_helpers.py`).
   Two functions that consolidate the "load by PK, check user_id"
   pattern used across 60+ routes. These are defense-in-depth utilities
   for routes to adopt incrementally.

2. **Defense-in-depth on `credit_workflow.py`**. The `mark_as_credit()`
   and `unmark_credit()` functions currently accept only a
   `transaction_id` and trust the caller to have verified ownership.
   You are adding a `user_id` parameter so the service independently
   verifies ownership via the transaction's pay period.

3. **Defense-in-depth on `carry_forward_service.py`**. The
   `carry_forward_unpaid()` function currently accepts period IDs
   without verifying they belong to the caller's user. You are adding
   a `user_id` parameter with ownership checks.

## Why This Matters for Financial Safety

Without defense-in-depth, a single missed ownership check in a route
handler could let User B manipulate User A's credit card payback
transactions or carry forward User A's unpaid bills into a different
period. The route-level checks exist today, but if a future route
forgets the check, the service would blindly operate on another user's
data. This is the last line of defense before money-tracking data is
corrupted.

## Pre-Existing Infrastructure You Must Read First

Before writing any code, read these files IN FULL. Do not skim.

1. `app/services/credit_workflow.py` -- Read every function:
   `mark_as_credit()`, `unmark_credit()`, `_get_or_create_cc_category()`.
   Study how `mark_as_credit()` currently resolves `user_id` by loading
   the PayPeriod (around line 75-76). Your change adds an explicit
   `user_id` parameter and a guard clause at the top.

2. `app/services/carry_forward_service.py` -- Read the entire
   `carry_forward_unpaid()` function. Study how it currently loads
   `source` and `target` periods by PK. Your change adds a `user_id`
   parameter and checks `source.user_id != user_id` after each load.

3. `app/routes/transactions.py` -- Study every call site for
   `credit_workflow.mark_as_credit()`, `credit_workflow.unmark_credit()`,
   and `carry_forward_service.carry_forward_unpaid()`. You must update
   EVERY call site to pass `current_user.id`. Search the file
   thoroughly. Missing a call site will break the app.

4. `tests/test_services/test_credit_workflow.py` -- Read the ENTIRE
   file. This file contains both `TestCreditWorkflow` and
   `TestCarryForward` classes. Every existing call to
   `mark_as_credit(txn.id)`, `unmark_credit(txn.id)`, and
   `carry_forward_unpaid(period_a, period_b)` must be updated to pass
   `user_id`. Count the exact number of call sites. Do not miss any.

5. `tests/test_adversarial/test_hostile_qa.py` -- Search this file for
   calls to `mark_as_credit`, `unmark_credit`, and
   `carry_forward_unpaid`. Update every one.

6. `app/routes/transactions.py` lines 39-53 -- Study the existing
   `_get_owned_transaction()` helper. Your `get_owned_via_parent()` in
   auth_helpers.py generalizes this pattern.

7. `app/exceptions.py` -- Note that `NotFoundError` is the exception
   used when an ownership check fails. This matches the "return 404,
   do not confirm resource exists" design decision.

8. `app/models/` -- Study the relationships on Transaction (has
   `pay_period` relationship), Account (has direct `user_id`), and
   PayPeriod (has direct `user_id`). You need to know which models
   use direct user_id and which use parent-chain user_id.

## Implementation Specification

### 1. New File: `app/utils/auth_helpers.py`

Create the `app/utils/` directory if it does not exist (check first).
Create `app/utils/__init__.py` as an empty file if it does not exist.

**File: `app/utils/auth_helpers.py`**

```python
"""
Shekel Budget App - Authorization Helpers

Reusable functions for verifying resource ownership. Used by route
handlers to ensure the current user can only access their own data.

Pattern A (direct user_id): Use get_or_404() for models with a
user_id column (Account, TransactionTemplate, SavingsGoal, etc.).

Pattern B (indirect via parent): Use get_owned_via_parent() for
models scoped through a FK parent (Transaction via PayPeriod,
SalaryRaise via SalaryProfile, etc.).
"""
```

**Function 1: `get_or_404(model, pk, user_id_field="user_id")`**

Implementation:

1. `record = db.session.get(model, pk)`
2. If `record is None`, return `None`.
3. If `getattr(record, user_id_field, None) != current_user.id`,
   return `None`.
4. Return `record`.

Key details:

- Import `current_user` from `flask_login`.
- Import `db` from `app.extensions`.
- The `getattr` with default `None` ensures that if someone passes a
  model that lacks the `user_id_field`, the comparison fails safely
  (returns None) rather than raising AttributeError.
- The function name says "404" because returning None signals the caller
  to return a 404 response. The function itself does NOT raise or abort.
- Docstring must include Args, Returns, and a note that the caller is
  responsible for handling the None return (returning 404 or redirecting).

**Function 2: `get_owned_via_parent(model, pk, parent_attr, parent_user_id_attr="user_id")`**

Implementation:

1. `record = db.session.get(model, pk)`
2. If `record is None`, return `None`.
3. `parent = getattr(record, parent_attr, None)`
4. If `parent is None`, return `None`. This handles both "relationship
   not defined" and "FK is null" (data corruption).
5. If `getattr(parent, parent_user_id_attr, None) != current_user.id`,
   return `None`.
6. Return `record`.

Key details:

- The `parent_attr` is a SQLAlchemy relationship attribute name (e.g.,
  `"pay_period"` on Transaction, `"salary_profile"` on SalaryRaise,
  `"account"` on HysaParams). When accessed, SQLAlchemy lazy-loads the
  parent. This is acceptable for single-record lookups.
- The double-None check (step 3 and 4) is critical: if the FK column
  exists but the parent row was deleted (orphaned record), we must not
  crash -- we return None.
- Docstring must include Args, Returns, and examples of usage.

### 2. Modify: `app/services/credit_workflow.py`

**`mark_as_credit(transaction_id, user_id)`**

Change the signature from `mark_as_credit(transaction_id)` to
`mark_as_credit(transaction_id, user_id)`.

Add an ownership guard IMMEDIATELY after the existing "txn is None"
check. The new code goes between the existing None check and the
`is_income` check:

```python
txn = db.session.get(Transaction, transaction_id)
if txn is None:
    raise NotFoundError(f"Transaction {transaction_id} not found.")
# Defense-in-depth: verify ownership via pay period.
if txn.pay_period.user_id != user_id:
    raise NotFoundError(f"Transaction {transaction_id} not found.")
```

IMPORTANT: Use the SAME error message for "not found" and "not owned".
This is the "return 404, do not confirm resource exists" design
decision. An attacker cannot distinguish "transaction does not exist"
from "transaction belongs to someone else".

Later in the function (around lines 75-76), the existing code loads
the PayPeriod again to get user_id for `_get_or_create_cc_category()`:

```python
from app.models.pay_period import PayPeriod
period = db.session.get(PayPeriod, txn.pay_period_id)
user_id = period.user_id
```

This is now redundant because you already have `user_id` as a
parameter and you already verified it matches the pay period's user_id.
Replace those lines to use the parameter directly:

```python
from app.models.pay_period import PayPeriod  # pylint: disable=import-outside-toplevel
period = db.session.get(PayPeriod, txn.pay_period_id)
```

Keep the PayPeriod import and the period lookup (they are needed for
`pay_period_service.get_next_period(period)` below), but remove the
`user_id = period.user_id` line since `user_id` is now the parameter.

Update the docstring to document the new `user_id` parameter and the
additional `NotFoundError` raise condition for ownership failure.

**`unmark_credit(transaction_id, user_id)`**

Change the signature from `unmark_credit(transaction_id)` to
`unmark_credit(transaction_id, user_id)`.

Add the same ownership guard after the existing None check:

```python
txn = db.session.get(Transaction, transaction_id)
if txn is None:
    raise NotFoundError(f"Transaction {transaction_id} not found.")
# Defense-in-depth: verify ownership via pay period.
if txn.pay_period.user_id != user_id:
    raise NotFoundError(f"Transaction {transaction_id} not found.")
```

Update the docstring to include the `user_id` parameter and ownership
check.

### 3. Modify: `app/services/carry_forward_service.py`

**`carry_forward_unpaid(source_period_id, target_period_id, user_id)`**

Change the signature from
`carry_forward_unpaid(source_period_id, target_period_id)` to
`carry_forward_unpaid(source_period_id, target_period_id, user_id)`.

Modify the existing period loading to add ownership checks. The
EXISTING code is:

```python
source = db.session.get(PayPeriod, source_period_id)
if source is None:
    raise NotFoundError(f"Source pay period {source_period_id} not found.")

target = db.session.get(PayPeriod, target_period_id)
if target is None:
    raise NotFoundError(f"Target pay period {target_period_id} not found.")
```

Change it to:

```python
source = db.session.get(PayPeriod, source_period_id)
if source is None or source.user_id != user_id:
    raise NotFoundError(f"Source pay period {source_period_id} not found.")

target = db.session.get(PayPeriod, target_period_id)
if target is None or target.user_id != user_id:
    raise NotFoundError(f"Target pay period {target_period_id} not found.")
```

Same principle: "not found" and "not owned" produce identical error
messages.

Update the docstring to include the `user_id` parameter and the
ownership check description.

### 4. Modify: `app/routes/transactions.py`

Search the ENTIRE file for every call to:

- `credit_workflow.mark_as_credit(`
- `credit_workflow.unmark_credit(`
- `carry_forward_service.carry_forward_unpaid(`

Update each one to pass `current_user.id` as the new argument.

Expected call sites (verify these line numbers by reading the file):

1. `mark_as_credit` call -- likely in the `mark_credit` route handler.
   Change from `credit_workflow.mark_as_credit(txn.id)` to
   `credit_workflow.mark_as_credit(txn.id, current_user.id)`.

2. `unmark_credit` call -- likely in the `unmark_credit` route handler.
   Change from `credit_workflow.unmark_credit(txn.id)` to
   `credit_workflow.unmark_credit(txn.id, current_user.id)`.

3. `carry_forward_unpaid` call -- in the `carry_forward` route handler.
   Change from `carry_forward_service.carry_forward_unpaid(period_id, current_period.id)`
   to `carry_forward_service.carry_forward_unpaid(period_id, current_period.id, current_user.id)`.

Do NOT assume there are exactly 3 call sites. Read the entire file
and grep for every occurrence. There may be more.

### 5. Update ALL Existing Tests

This is the most error-prone part of this work unit. A single missed
call site means a TypeError at runtime. Be methodical.

**File: `tests/test_services/test_credit_workflow.py`**

Read the entire file. Find every call to:

- `credit_workflow.mark_as_credit(` -- add `, seed_user["user"].id)`
  after the transaction_id argument.
- `credit_workflow.unmark_credit(` -- same.
- `carry_forward_service.carry_forward_unpaid(` -- add
  `, seed_user["user"].id)` after the target_period_id argument.

The `_create_expense` helper in `TestCreditWorkflow` does NOT call
these functions, so it does not change. But every test method that
calls them must be updated.

Count the call sites. The test file has approximately:

- 6-8 calls to `mark_as_credit` across TestCreditWorkflow methods
- 2-3 calls to `unmark_credit`
- 4-6 calls to `carry_forward_unpaid` across TestCarryForward methods

After updating, verify the count matches what you found.

**File: `tests/test_adversarial/test_hostile_qa.py`**

Search for calls to `credit_workflow.mark_as_credit(` and
`carry_forward_service.carry_forward_unpaid(`. Update each one.
These tests use `seed_user`, so the user_id is
`seed_user["user"].id`.

**Any other test files that call these functions:**

Search the ENTIRE `tests/` directory:

```
grep -rn "mark_as_credit\|unmark_credit\|carry_forward_unpaid" tests/
```

Update every hit. Do not skip any file.

## New Tests

### 6. New Test File: `tests/test_utils/test_auth_helpers.py`

Create the `tests/test_utils/` directory if it does not exist.
Create `tests/test_utils/__init__.py` as an empty file if it does not
exist.

**CRITICAL: `current_user` requires a request context.**

The `get_or_404()` and `get_owned_via_parent()` functions use
`current_user` from Flask-Login, which is a proxy that only resolves
during an active Flask request. You CANNOT call these functions in bare
test code. You must use one of these patterns:

**Pattern A (preferred for unit tests): test_request_context + login_user**

```python
from flask_login import login_user

def test_example(self, app, db, seed_user):
    """Example showing how to test auth helpers."""
    with app.test_request_context():
        login_user(seed_user["user"])
        result = get_or_404(Account, seed_user["account"].id)
        assert result is not None
```

**Pattern B (if you need full HTTP): use auth_client to make a request**

This is NOT appropriate for auth_helpers unit tests because you need to
call the function directly, not through a route.

Use Pattern A for all auth_helpers tests.

**Creating a second user for cross-user tests:**

The `seed_second_user` conftest fixture does not exist yet (that is
WU-3). Do NOT modify conftest.py. Instead, create a local fixture in
the test file:

```python
@pytest.fixture()
def second_user(app, db):
    """Create a second user for cross-user ownership tests.

    This is a local fixture for auth_helpers tests only. The global
    seed_second_user fixture will be added in WU-3.
    """
    from app.services.auth_service import hash_password
    from app.models.user import User
    from app.models.user_settings import UserSettings
    from app.models.scenario import Scenario

    user = User(
        email="second@shekel.local",
        password_hash=hash_password("secondpass1234"),
        display_name="Second User",
    )
    db.session.add(user)
    db.session.flush()

    settings = UserSettings(user_id=user.id)
    scenario = Scenario(
        user_id=user.id, name="Baseline", is_baseline=True
    )
    db.session.add_all([settings, scenario])
    db.session.flush()

    return user
```

For tests that need a second user's Account (for `get_or_404` cross-user
tests), create the account inside the test or in a second local fixture.

**Required tests for `TestGetOr404`:**

```
test_returns_owned_record
```

Create an Account for `seed_user`. Log in as `seed_user`. Call
`get_or_404(Account, account.id)`. Assert the returned record IS the
account (same `id`). This tests the happy path.

```
test_returns_none_for_nonexistent_pk
```

Log in as `seed_user`. Call `get_or_404(Account, 999999)`. Assert
returns `None`. This tests the "record does not exist" path.

```
test_returns_none_for_other_users_record
```

Create an Account for the `second_user` local fixture. Log in as
`seed_user` (NOT second_user). Call `get_or_404(Account,
second_user_account.id)`. Assert returns `None`. This is the core
security test -- user A cannot load user B's record.

```
test_returns_none_for_pk_zero
```

Log in as `seed_user`. Call `get_or_404(Account, 0)`. Assert returns
`None`. PK=0 does not exist in PostgreSQL autoincrement sequences, but
the function must handle it without crashing.

```
test_custom_user_id_field
```

If you can find a model in the codebase that uses a column name other
than `user_id` for its owner FK, test the `user_id_field` parameter.
If no such model exists (check the codebase), test this by passing a
deliberately wrong field name (e.g., `user_id_field="nonexistent"`) and
verifying it returns `None` (since `getattr` returns `None` for a
nonexistent attr, which will not equal `current_user.id`).

**Required tests for `TestGetOwnedViaParent`:**

```
test_returns_owned_child_record
```

Create a Transaction for `seed_user` in one of their pay periods.
Log in as `seed_user`. Call
`get_owned_via_parent(Transaction, txn.id, "pay_period")`.
Assert the returned record IS the transaction. This tests the happy
path.

```
test_returns_none_for_nonexistent_pk
```

Log in as `seed_user`. Call
`get_owned_via_parent(Transaction, 999999, "pay_period")`.
Assert returns `None`.

```
test_returns_none_for_other_users_child
```

Create a PayPeriod for `second_user`. Create a Transaction in that
period. Log in as `seed_user`. Call
`get_owned_via_parent(Transaction, second_user_txn.id, "pay_period")`.
Assert returns `None`. Core security test.

```
test_returns_none_when_parent_attr_missing
```

Log in as `seed_user`. Create a Transaction for seed_user. Call
`get_owned_via_parent(Transaction, txn.id, "nonexistent_relationship")`.
Assert returns `None`. This tests the safety net when a bad
`parent_attr` is passed -- the function must not crash.

```
test_returns_none_when_parent_user_id_attr_missing
```

Log in as `seed_user`. Create a Transaction for seed_user. Call
`get_owned_via_parent(Transaction, txn.id, "pay_period", parent_user_id_attr="nonexistent")`.
Assert returns `None`. Tests the safety net for bad
`parent_user_id_attr`.

### 7. New Tests in `tests/test_services/test_credit_workflow.py`

Add these to the existing `TestCreditWorkflow` class:

```
test_mark_as_credit_wrong_user_raises_not_found
```

Create an expense for `seed_user`. Call
`credit_workflow.mark_as_credit(txn.id, user_id=999999)`.
Assert raises `NotFoundError`. Assert the transaction's status is
UNCHANGED (still "projected") -- the function must not partially
modify data before failing.

```
test_unmark_credit_wrong_user_raises_not_found
```

Create an expense for `seed_user`, mark it as credit (with the correct
user_id). Then call
`credit_workflow.unmark_credit(txn.id, user_id=999999)`.
Assert raises `NotFoundError`. Assert the transaction's status is
UNCHANGED (still "credit") -- the unmark must not partially revert.
Assert the payback transaction still exists.

```
test_mark_as_credit_nonexistent_txn_raises_not_found
```

Call `credit_workflow.mark_as_credit(999999, seed_user["user"].id)`.
Assert raises `NotFoundError`. This verifies the existing behavior
is preserved after the signature change.

```
test_unmark_credit_nonexistent_txn_raises_not_found
```

Call `credit_workflow.unmark_credit(999999, seed_user["user"].id)`.
Assert raises `NotFoundError`.

Add these to the existing `TestCarryForward` class:

```
test_carry_forward_wrong_user_source_raises_not_found
```

Call `carry_forward_service.carry_forward_unpaid(
    seed_periods[0].id, seed_periods[1].id, user_id=999999
)`. Assert raises `NotFoundError`. Use `user_id=999999` (a
nonexistent user) to guarantee the source period's user_id will not
match.

```
test_carry_forward_wrong_user_target_raises_not_found
```

Create a second user with their own pay periods (inline, not via
conftest). Call `carry_forward_service.carry_forward_unpaid(
    seed_periods[0].id, second_user_period.id, seed_user["user"].id
)`. The source belongs to seed_user (passes), but the target belongs
to the second user (fails). Assert raises `NotFoundError`. This
tests that BOTH source and target are checked, not just the first one.

```
test_carry_forward_nonexistent_source_raises_not_found
```

Call `carry_forward_service.carry_forward_unpaid(
    999999, seed_periods[0].id, seed_user["user"].id
)`. Assert raises `NotFoundError`. Verifies existing behavior is
preserved.

```
test_carry_forward_nonexistent_target_raises_not_found
```

Call `carry_forward_service.carry_forward_unpaid(
    seed_periods[0].id, 999999, seed_user["user"].id
)`. Assert raises `NotFoundError`.

## Execution Checklist

After implementing everything, run these checks in this exact order:

1. Verify the `app/utils/` directory exists with `__init__.py`.
2. Verify the `tests/test_utils/` directory exists with `__init__.py`.
3. `grep -rn "mark_as_credit\|unmark_credit\|carry_forward_unpaid" app/ tests/`
   -- Inspect EVERY result. Every call must now include the `user_id`
   argument. If any call is missing `user_id`, fix it before running
   tests.
4. `pylint app/utils/auth_helpers.py` -- fix all issues.
5. `pylint app/services/credit_workflow.py` -- fix all issues.
6. `pylint app/services/carry_forward_service.py` -- fix all issues.
7. `pytest tests/test_utils/test_auth_helpers.py -v` -- all must pass.
8. `pytest tests/test_services/test_credit_workflow.py -v` -- all must
   pass (BOTH existing updated tests AND new tests).
9. `pytest tests/test_adversarial/ -v` -- all must pass.
10. `pytest` -- the FULL suite. All ~900+ existing tests plus your new
    tests must pass. Zero failures. Zero errors.

If ANY test fails at step 10, the most likely cause is a missed call
site where the old signature (without `user_id`) is still being used.
The error will be a `TypeError: missing required positional argument`.
Search for the function name in the traceback file and add the missing
argument.

## Things You Must NOT Do

- Do not create any migration file. No schema changes are needed.
- Do not modify `tests/conftest.py` (that is WU-3's job).
- Do not refactor any routes to use the new auth_helpers yet. The
  helpers are created here; routes adopt them incrementally in a
  future work unit. The only route changes in WU-2 are adding
  `current_user.id` to the three service call sites.
- Do not change the behavior of `mark_as_credit`, `unmark_credit`, or
  `carry_forward_unpaid` beyond adding the ownership check. The
  business logic (status changes, payback creation, transaction moves)
  must remain identical.
- Do not add any new Python packages to requirements.txt.
- Do not write `pass` or `TODO` in any function or test body.
- Do not use `assert True` or any assertion that always passes.
- Do not mock the database. Tests use the real PostgreSQL test database.
- Do not mock `current_user` in auth_helpers tests. Use
  `app.test_request_context()` + `login_user()` from Flask-Login
  to set up a real request context with a real user.
- Do not use em dashes or en dashes anywhere (use hyphens instead).
- Do not use `float` for any numeric value. Use `Decimal` when needed.
