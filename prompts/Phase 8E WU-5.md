# Phase 8E WU-5: Direct Object Access Tests (ID Guessing)

## Context

This is a personal finance application (Shekel) that manages real money
through pay-period budgeting. Bugs in this code have real financial
consequences. There is no QA team. Every line you write must be correct
the first time. Do not take shortcuts.

Read CLAUDE.md before doing anything. Read tests/TEST_PLAN.md before
writing any tests. Follow every convention described in both files.

WU-1 through WU-4 are complete.

## What You Are Building

A single new test file:
`tests/test_integration/test_access_control.py`

This file proves that when User B knows the database ID of User A's
resource, User B cannot access, modify, or delete that resource through
any route that accepts an ID parameter. This is the IDOR (Insecure
Direct Object Reference) test suite.

The codebase has 60+ routes that accept ID parameters. Every one of them
verifies ownership. These tests are the proof.

## Why This Matters More Than WU-4

WU-4 tested page-level visibility (does the list page show only your
data?). WU-5 tests direct resource access (can you hit a URL with
someone else's ID and get their data or modify it?). WU-4 catches
missing `user_id` filters in list queries. WU-5 catches missing
ownership checks in load-by-PK handlers. If WU-5 fails, User B can:

- Read User A's bank account details, salary, and mortgage terms
- Modify User A's transactions, changing the budget they rely on
- Delete User A's financial records

These are not theoretical risks. This is a multi-user finance app.

## Pre-Existing Infrastructure You Must Read First

Before writing any code, read these files IN FULL.

1. `tests/conftest.py` -- Understand the WU-3 fixtures:
   - `seed_full_user_data` returns a dict with User A's objects
   - `seed_full_second_user_data` returns a dict with User B's objects
   - `auth_client` is logged in as User A
   - `second_auth_client` is logged in as User B

2. The complete route authorization audit in
   `phase_8E_implementation_plan.md` Section 5 (lines 609-744). This
   lists EVERY route that accepts an ID parameter, its URL pattern,
   HTTP method, and ownership check. Your tests must cover ALL of them.

3. `tests/test_routes/test_transaction_auth.py` -- Study the existing
   IDOR tests for transactions. These tests create a second user
   inline and test cross-user access. Your tests use the WU-3 fixtures
   instead but follow the same assertion pattern.

4. `tests/test_adversarial/test_hostile_qa.py` class
   `TestAuthEdgeCases` -- More existing cross-user tests.

5. Read these route files to understand the exact HTTP methods and
   response patterns for each endpoint:
   - `app/routes/accounts.py`
   - `app/routes/templates.py`
   - `app/routes/transactions.py`
   - `app/routes/transfers.py`
   - `app/routes/salary.py`
   - `app/routes/savings.py`
   - `app/routes/categories.py`
   - `app/routes/retirement.py`
   - `app/routes/mortgage.py`
   - `app/routes/auto_loan.py`
   - `app/routes/investment.py`

## Response Pattern for Unauthorized Access

The codebase uses TWO patterns for unauthorized access. You must handle
both in your assertions:

**Pattern 1: Redirect with flash (most GET routes and form POSTs)**
The route loads the object, checks ownership, flashes "not found"
(danger), and redirects to the list page. The response is:
- `status_code == 302`
- The redirect target is the list page for that resource type
- If you `follow_redirects=True`, you see the flash message

**Pattern 2: Direct 404 (HTMX fragment endpoints)**
The route loads the object, checks ownership, and returns
`("Not found", 404)`. The response is:
- `status_code == 404`

Your tests must accept EITHER pattern. The safest assertion is:

```python
response = client.get(url)
assert response.status_code in (302, 404), (
    f"Expected 302 or 404, got {response.status_code}"
)
```

For routes that redirect, you can optionally follow the redirect and
check for the flash message. But the status code check alone is the
minimum requirement.

IMPORTANT: A `200` response means the ownership check FAILED and User B
got access. This is the condition you are testing against. If any test
gets a 200, something is broken.

## Extracting IDs from Fixture Data

Every test uses `seed_full_user_data` (User A) to get the target IDs,
and `second_auth_client` (User B) to make the requests.

```python
def test_example(
    self, app, second_auth_client,
    seed_full_user_data, seed_full_second_user_data
):
    """User B cannot access User A's resource."""
    with app.app_context():
        target_id = seed_full_user_data["account"].id
        response = second_auth_client.get(
            f"/accounts/{target_id}/edit"
        )
        assert response.status_code in (302, 404)
```

The fixture dict keys you need:

| Key | Object Type | Has direct user_id |
|-----|-------------|-------------------|
| `"account"` | Account (checking) | Yes |
| `"savings_account"` | Account (savings) | Yes |
| `"template"` | TransactionTemplate | Yes |
| `"transaction"` | Transaction | No (via pay_period) |
| `"transfer_template"` | TransferTemplate | Yes |
| `"salary_profile"` | SalaryProfile | Yes |
| `"savings_goal"` | SavingsGoal | Yes |
| `"recurrence_rule"` | RecurrenceRule | Yes |
| `"periods"` | List[PayPeriod] | Yes |
| `"categories"` | Dict[str, Category] | Yes |
| `"user"` | User | IS the user |
| `"scenario"` | Scenario | Yes |

## Implementation Specification

### File: `tests/test_integration/test_access_control.py`

```python
"""
Shekel Budget App - Access Control Tests (IDOR Prevention)

Verifies that users cannot access other users' resources by guessing
database IDs. Every route that accepts an ID parameter is tested.
User B (second_auth_client) attempts to access User A's resources
(seed_full_user_data). Every attempt must return 302 (redirect) or
404 (not found), never 200 (success).

These tests are the safety net against Insecure Direct Object Reference
vulnerabilities. A 200 response on any test means User B can access
User A's financial data.
"""
```

### Helper Function

Create a module-level helper to reduce boilerplate:

```python
def _assert_blocked(response, msg=""):
    """Assert that a response indicates the request was blocked.

    Ownership checks return either 302 (redirect with flash) or
    404 (direct not-found). A 200 means the attacker got access.

    Args:
        response: The Flask test client response.
        msg: Optional context message for the assertion.
    """
    assert response.status_code in (302, 404), (
        f"Expected 302 or 404 but got {response.status_code}. "
        f"User B may have accessed User A's resource. {msg}"
    )
```

Use this helper in every test instead of writing the assertion inline.
It provides a clear error message when a test fails.

### Class: TestAccountAccessControl

Test every route from Section 5.1 of the authorization audit.

Read `app/routes/accounts.py` to confirm exact URL patterns and HTTP
methods before writing tests.

```
test_edit_account_blocked
```
User B GETs `/accounts/{user_a_checking_id}/edit`. Assert blocked.

```
test_update_account_blocked
```
User B POSTs `/accounts/{user_a_checking_id}` with dummy data (e.g.,
`{"name": "Hacked"}`). Assert blocked.

```
test_deactivate_account_blocked
```
User B POSTs `/accounts/{user_a_checking_id}/delete`. Assert blocked.

```
test_reactivate_account_blocked
```
User B POSTs `/accounts/{user_a_checking_id}/reactivate`. Assert
blocked. (The plan's WU-5 omits this route, but the audit lists it.)

```
test_inline_anchor_update_blocked
```
User B POSTs `/accounts/{user_a_checking_id}/inline-anchor` with
dummy data. Assert blocked. Read the route to determine the correct
HTTP method (POST or PATCH).

```
test_inline_anchor_form_blocked
```
User B GETs `/accounts/{user_a_checking_id}/inline-anchor-form`.
Assert blocked.

```
test_inline_anchor_display_blocked
```
User B GETs `/accounts/{user_a_checking_id}/inline-anchor-display`.
Assert blocked.

```
test_true_up_blocked
```
User B POSTs `/accounts/{user_a_checking_id}/true-up`. Assert blocked.

```
test_anchor_form_blocked
```
User B GETs `/accounts/{user_a_checking_id}/anchor-form`. Assert
blocked.

```
test_anchor_display_blocked
```
User B GETs `/accounts/{user_a_checking_id}/anchor-display`. Assert
blocked.

```
test_hysa_detail_blocked
```
User B GETs `/accounts/{user_a_checking_id}/hysa`. Assert blocked.

```
test_update_hysa_params_blocked
```
User B POSTs `/accounts/{user_a_checking_id}/hysa/params` with dummy
data. Assert blocked.

### Class: TestTemplateAccessControl

Test every route from Section 5.2.

```
test_edit_template_blocked
```
User B GETs `/templates/{user_a_template_id}/edit`. Assert blocked.

```
test_update_template_blocked
```
User B POSTs `/templates/{user_a_template_id}` with dummy data.
Assert blocked.

```
test_delete_template_blocked
```
User B POSTs `/templates/{user_a_template_id}/delete`. Assert blocked.

```
test_reactivate_template_blocked
```
User B POSTs `/templates/{user_a_template_id}/reactivate`. Assert
blocked.

### Class: TestTransactionAccessControl

Test every route from Section 5.4. Transaction IDs come from
`seed_full_user_data["transaction"].id`. Period IDs for
carry-forward come from `seed_full_user_data["periods"][0].id`.

IMPORTANT: Read `app/routes/transactions.py` to determine the exact
HTTP method for each endpoint. Some use GET, some POST, some PATCH,
some DELETE. Using the wrong method gives a 405 (Method Not Allowed),
which is NOT a valid ownership check test.

```
test_get_cell_blocked
```
User B GETs `/transactions/{user_a_txn_id}/cell`. Assert blocked.

```
test_get_quick_edit_blocked
```
User B GETs `/transactions/{user_a_txn_id}/quick-edit`. Assert blocked.

```
test_get_full_edit_blocked
```
User B GETs `/transactions/{user_a_txn_id}/full-edit`. Assert blocked.

```
test_update_transaction_blocked
```
User B PATCHes `/transactions/{user_a_txn_id}` with dummy data.
Assert blocked.

```
test_mark_done_blocked
```
User B POSTs `/transactions/{user_a_txn_id}/mark-done`. Assert blocked.

```
test_mark_credit_blocked
```
User B POSTs `/transactions/{user_a_txn_id}/mark-credit`. Assert
blocked.

```
test_unmark_credit_blocked
```
User B POSTs `/transactions/{user_a_txn_id}/unmark-credit`. Assert
blocked. (The plan omits this but the audit includes it.)

```
test_cancel_transaction_blocked
```
User B POSTs `/transactions/{user_a_txn_id}/cancel`. Assert blocked.

```
test_delete_transaction_blocked
```
User B DELETEs `/transactions/{user_a_txn_id}`. Assert blocked.

```
test_carry_forward_blocked
```
User B POSTs `/pay-periods/{user_a_period_id}/carry-forward`. Assert
blocked. Use `seed_full_user_data["periods"][0].id`.

### Class: TestTransferTemplateAccessControl

Test every transfer TEMPLATE route from Section 5.3.

```
test_edit_transfer_template_blocked
```
User B GETs `/transfers/{user_a_transfer_tpl_id}/edit`. Assert blocked.

```
test_update_transfer_template_blocked
```
User B POSTs `/transfers/{user_a_transfer_tpl_id}` with dummy data.
Assert blocked.

```
test_delete_transfer_template_blocked
```
User B POSTs `/transfers/{user_a_transfer_tpl_id}/delete`. Assert
blocked.

```
test_reactivate_transfer_template_blocked
```
User B POSTs `/transfers/{user_a_transfer_tpl_id}/reactivate`. Assert
blocked.

### Class: TestSalaryAccessControl

Test every route from Section 5.5.

```
test_edit_profile_blocked
```
User B GETs `/salary/{user_a_profile_id}/edit`. Assert blocked.

```
test_update_profile_blocked
```
User B POSTs `/salary/{user_a_profile_id}` with dummy data. Assert
blocked.

```
test_delete_profile_blocked
```
User B POSTs `/salary/{user_a_profile_id}/delete`. Assert blocked.

```
test_add_raise_blocked
```
User B POSTs `/salary/{user_a_profile_id}/raises` with dummy data.
Assert blocked.

```
test_add_deduction_blocked
```
User B POSTs `/salary/{user_a_profile_id}/deductions` with dummy data.
Assert blocked.

```
test_breakdown_blocked
```
User B GETs `/salary/{user_a_profile_id}/breakdown`. Assert blocked.
(This is `breakdown_current` which redirects to `breakdown` with the
current period.)

```
test_breakdown_with_period_blocked
```
User B GETs `/salary/{user_a_profile_id}/breakdown/{user_a_period_id}`.
Assert blocked.

```
test_projection_blocked
```
User B GETs `/salary/{user_a_profile_id}/projection`. Assert blocked.

### Class: TestSavingsAccessControl

Test every route from Section 5.7.

```
test_edit_goal_blocked
```
User B GETs `/savings/goals/{user_a_goal_id}/edit`. Assert blocked.

```
test_update_goal_blocked
```
User B POSTs `/savings/goals/{user_a_goal_id}` with dummy data. Assert
blocked.

```
test_delete_goal_blocked
```
User B POSTs `/savings/goals/{user_a_goal_id}/delete`. Assert blocked.

### Class: TestCategoryAccessControl

Test every route from Section 5.8.

```
test_delete_category_blocked
```
User B POSTs `/categories/{user_a_category_id}/delete`. Use any of
User A's category IDs (e.g., the "Rent" category). Assert blocked.

### Class: TestRetirementAccessControl

Test every route from Section 5.6. The fixtures do NOT create
PensionProfile objects. You must create one inline in the test setup.

Create a local fixture or a setup method that creates a PensionProfile
for User A:

```python
@pytest.fixture()
def user_a_pension(self_or_app, db, seed_full_user_data):
    """Create a pension profile for User A for retirement IDOR tests."""
    from app.models.pension_profile import PensionProfile

    pension = PensionProfile(
        user_id=seed_full_user_data["user"].id,
        name="Test Pension",
        employer_name="Test Corp",
        # Add other required fields -- read PensionProfile model first
    )
    db.session.add(pension)
    db.session.commit()
    return pension
```

IMPORTANT: Read `app/models/pension_profile.py` first to determine the
required columns. Do not guess the schema.

If PensionProfile has complex required fields you cannot easily fill,
use a simpler approach: test with a nonexistent ID like 999999.
The route should return 404 for a nonexistent pension regardless of
user. This still validates that the route does not crash on missing
resources, but does not specifically test cross-user ownership. The
inline fixture approach is preferred if the model allows it.

```
test_edit_pension_blocked
```
User B GETs `/retirement/pension/{user_a_pension_id}/edit`. Assert
blocked.

```
test_update_pension_blocked
```
User B POSTs `/retirement/pension/{user_a_pension_id}` with dummy data.
Assert blocked.

```
test_delete_pension_blocked
```
User B POSTs `/retirement/pension/{user_a_pension_id}/delete`. Assert
blocked.

### Class: TestMortgageAccessControl

Test routes from Section 5.9. These use account_id, not a mortgage-
specific ID. Use User A's checking account ID.

```
test_mortgage_dashboard_blocked
```
User B GETs `/accounts/{user_a_account_id}/mortgage`. Assert blocked.

```
test_mortgage_setup_blocked
```
User B POSTs `/accounts/{user_a_account_id}/mortgage/setup` with dummy
data. Assert blocked.

```
test_mortgage_update_params_blocked
```
User B POSTs `/accounts/{user_a_account_id}/mortgage/params` with
dummy data. Assert blocked.

```
test_mortgage_add_rate_blocked
```
User B POSTs `/accounts/{user_a_account_id}/mortgage/rate` with dummy
data. Assert blocked.

```
test_mortgage_add_escrow_blocked
```
User B POSTs `/accounts/{user_a_account_id}/mortgage/escrow` with
dummy data. Assert blocked.

```
test_mortgage_payoff_blocked
```
User B POSTs `/accounts/{user_a_account_id}/mortgage/payoff` with
dummy data. Assert blocked.

### Class: TestAutoLoanAccessControl

Test routes from Section 5.10.

```
test_auto_loan_dashboard_blocked
```
User B GETs `/accounts/{user_a_account_id}/auto-loan`. Assert blocked.

```
test_auto_loan_setup_blocked
```
User B POSTs `/accounts/{user_a_account_id}/auto-loan/setup` with
dummy data. Assert blocked.

```
test_auto_loan_update_params_blocked
```
User B POSTs `/accounts/{user_a_account_id}/auto-loan/params` with
dummy data. Assert blocked.

### Class: TestInvestmentAccessControl

Test routes from Section 5.11.

```
test_investment_dashboard_blocked
```
User B GETs `/accounts/{user_a_account_id}/investment`. Assert blocked.

```
test_investment_growth_chart_blocked
```
User B GETs `/accounts/{user_a_account_id}/investment/growth-chart`.
Assert blocked.

```
test_investment_update_params_blocked
```
User B POSTs `/accounts/{user_a_account_id}/investment/params` with
dummy data. Assert blocked.

### Class: TestNonexistentResourceAccess

These tests verify that accessing a completely nonexistent ID returns
404, not 500. This catches missing None checks.

```
test_nonexistent_account
```
User B GETs `/accounts/999999/edit`. Assert blocked (302 or 404).

```
test_nonexistent_transaction
```
User B GETs `/transactions/999999/cell`. Assert 404.

```
test_nonexistent_template
```
User B GETs `/templates/999999/edit`. Assert blocked.

```
test_nonexistent_salary_profile
```
User B GETs `/salary/999999/edit`. Assert blocked.

```
test_nonexistent_savings_goal
```
User B GETs `/savings/goals/999999/edit`. Assert blocked.

## Additional Test: Data Integrity After Blocked Attempts

The most critical class of bug is a PARTIAL mutation: the route starts
modifying the resource, then hits the ownership check and returns 404,
but the modification is partially committed.

### Class: TestDataIntegrityAfterBlockedAccess

```
test_account_unchanged_after_blocked_update
```
Record User A's account name before the test. User B POSTs to
`/accounts/{user_a_account_id}` with `{"name": "HACKED"}`. Assert
blocked. Reload User A's account from the database. Assert the
name has NOT changed. Assert the anchor balance has NOT changed.

```
test_transaction_unchanged_after_blocked_mark_done
```
Record User A's transaction status before the test. User B POSTs
to `/transactions/{user_a_txn_id}/mark-done`. Assert blocked.
Reload the transaction from the database. Assert the status is
still "projected" (or whatever it was before).

```
test_template_unchanged_after_blocked_delete
```
Record User A's template `is_active` flag. User B POSTs to
`/templates/{user_a_template_id}/delete`. Assert blocked.
Reload the template. Assert `is_active` is still True.

```
test_category_unchanged_after_blocked_delete
```
Count User A's categories before the test. User B POSTs to
`/categories/{user_a_category_id}/delete`. Assert blocked.
Count User A's categories after. Assert the count is unchanged.

## HTTP Method Reference

Before writing each test, READ the route file to confirm the correct
HTTP method. Using the wrong method returns 405, which is NOT a
meaningful test.

Expected methods (VERIFY by reading each route):

| Route Pattern | Expected Method |
|---|---|
| `GET /.../edit` | GET |
| `POST /...` (create/update) | POST |
| `POST /.../delete` | POST |
| `POST /.../reactivate` | POST |
| `POST /.../mark-done` | POST |
| `POST /.../mark-credit` | POST |
| `POST /.../cancel` | POST |
| `POST /.../carry-forward` | POST |
| `PATCH /transactions/<id>` | PATCH |
| `DELETE /transactions/<id>` | DELETE |
| `GET /.../cell` | GET |
| `GET /.../quick-edit` | GET |
| `GET /.../full-edit` | GET |
| `POST /.../inline-anchor` | POST (verify -- might be PATCH) |

Do NOT assume. Read the `@route` decorator and the `methods=` argument
in the route file.

## Execution Checklist

After implementing everything:

1. Count your tests. The authorization audit lists 60+ routes with ID
   parameters. After excluding ref table routes (account types, which
   are shared data), you should have approximately 55-65 tests. If
   you have fewer than 50, you probably missed routes.

2. `pylint tests/test_integration/test_access_control.py` -- fix all
   issues.

3. `pytest tests/test_integration/test_access_control.py -v` -- all
   must pass. If any test gets a 200 response, that is a REAL BUG.
   Report it immediately; do not "fix" the test by weakening the
   assertion.

4. `pytest` -- the FULL suite. All existing tests plus WU-1 through
   WU-4 tests plus your new tests must pass. Zero failures.

If a test gets a 405 (Method Not Allowed), you used the wrong HTTP
method. Read the route file again.

If a test gets a 500 (Internal Server Error), the route crashes when
it cannot find the resource. This is also a bug worth noting, but
change your assertion to accept 500 alongside 302/404 with a comment
explaining the crash.

## Things You Must NOT Do

- Do not modify any existing file.
- Do not create any file other than
  `tests/test_integration/test_access_control.py`.
- Do not create any migration file.
- Do not add any Python packages.
- Do not write `pass` or `TODO` in any test body.
- Do not use `assert True` or any assertion that always passes.
- Do not weaken an assertion that fails. A 200 response means the
  ownership check is broken. Report it.
- Do not mock the database.
- Do not use `float` for any numeric value.
- Do not use em dashes or en dashes anywhere.
- Do not send POST/PATCH data that could actually succeed and modify
  User A's data. Use obviously dummy values (e.g., name="BLOCKED_TEST")
  that are easy to search for if something goes wrong.
- Do not skip routes because they "seem unlikely to be vulnerable."
  Every ID-accepting route gets a test.
