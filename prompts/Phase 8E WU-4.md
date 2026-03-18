# Phase 8E WU-4: Data Isolation Integration Tests (Page Visibility)

## Context

This is a personal finance application (Shekel) that manages real money
through pay-period budgeting. Bugs in this code have real financial
consequences. There is no QA team. Every line you write must be correct
the first time. Do not take shortcuts.

Read CLAUDE.md before doing anything. Read tests/TEST_PLAN.md before
writing any tests. Follow every convention described in both files.

WU-1 (registration), WU-2 (ownership helpers), and WU-3 (test fixtures)
are complete.

## What You Are Building

A single new test file:
`tests/test_integration/test_data_isolation.py`

This file proves that when two users exist with separate datasets, each
user sees ONLY their own data on every page in the application. Every
test follows the same two-assertion pattern:

1. **Positive assertion**: The logged-in user's own data IS present in
   the response body.
2. **Negative assertion**: The OTHER user's data IS NOT present in the
   response body.

A test that only checks "page loads with 200" is worthless. A test that
only checks "other user's data is absent" proves nothing if the user's
own data is also absent (empty page). Both assertions are mandatory.

## Pre-Existing Infrastructure You Must Read First

Before writing any code, read these files IN FULL. Do not skim.

1. `tests/conftest.py` -- Read the entire file, especially the WU-3
   fixtures. Understand exactly what data each fixture creates. The
   key fixtures are:
   - `seed_full_user_data` -- User A's complete dataset
   - `seed_full_second_user_data` -- User B's complete dataset
   - `auth_client` -- Logged in as User A
   - `second_auth_client` -- Logged in as User B

2. Study the returned dict keys from the full data fixtures. You must
   know exactly what objects exist and what their names/amounts are.

   **User A data (from seed_full_user_data):**
   - Account (checking): name="Checking", balance=$1,000.00
   - Account (savings): name="Savings", balance=$500.00
   - Template: name="Rent Payment", amount=$1,200.00
   - Transaction: name="Rent Payment", amount=$1,200.00
   - Savings Goal: name="Emergency Fund", target=$10,000.00
   - Transfer Template: name="Monthly Savings", amount=$200.00
   - Salary Profile: name="Day Job", salary=$75,000.00
   - Categories: Salary, Rent, Car Payment, Groceries, Payback (5 total)

   **User B data (from seed_full_second_user_data):**
   - Account (checking): name="Checking", balance=$2,000.00
   - Account (savings): name="Savings", balance=$300.00
   - Template: name="Second User Rent", amount=$900.00
   - Transaction: name="Second User Rent", amount=$900.00
   - Savings Goal: name="Vacation Fund", target=$5,000.00
   - Transfer Template: name="Bi-Weekly Savings", amount=$150.00
   - Salary Profile: name="Second Job", salary=$60,000.00
   - Categories: Salary, Rent, Car Payment, Groceries, Payback (5 total)

   CRITICAL: Both users have identical category names. Category tests
   cannot distinguish users by category name. Use a different strategy
   (see Categories section below).

3. Study these route files to understand what URL each page lives at
   and what data appears in the rendered HTML:
   - `app/routes/grid.py` -- Grid at `/`. Shows transaction names in
     cells.
   - `app/routes/savings.py` -- Savings dashboard at `/savings`. Shows
     account cards with names and balances, savings goal names.
   - `app/routes/templates.py` -- Templates list at `/templates`.
     Shows template names and amounts.
   - `app/routes/transfers.py` -- Transfers list at `/transfers`.
     Shows transfer template names and amounts.
   - `app/routes/salary.py` -- Salary list at `/salary`. Shows salary
     profile names.
   - `app/routes/charts.py` -- Charts dashboard at `/charts`. The
     dashboard itself is a shell; chart data loads via HTMX fragments
     that require an `HX-Request` header.
   - `app/routes/settings.py` -- Settings dashboard at `/settings`.
     Categories section at `/settings?section=categories`.
   - `app/routes/retirement.py` -- Retirement dashboard at
     `/retirement`.
   - `app/routes/accounts.py` -- Manage accounts at `/accounts`. Shows
     account names in the account list table.

4. Read the existing route test files to understand the assertion
   patterns used elsewhere:
   - `tests/test_routes/test_templates.py`
   - `tests/test_routes/test_transfers.py`
   - `tests/test_routes/test_salary.py`
   - `tests/test_routes/test_savings.py`

## Test Structure Rules

Every test method follows this exact structure:

```python
def test_user_X_sees_own_data(
    self, app, CLIENT_FIXTURE, seed_full_user_data, seed_full_second_user_data
):
    """DOCSTRING describing what is verified."""
    with app.app_context():
        response = CLIENT_FIXTURE.get(URL)
        assert response.status_code == 200

        # Positive: own data IS present.
        assert b"OWN_DATA_MARKER" in response.data

        # Negative: other user's data IS NOT present.
        assert b"OTHER_DATA_MARKER" not in response.data
```

For User A tests, use `auth_client`.
For User B tests, use `second_auth_client`.

Use `with app.app_context():` wrapping every test body. This ensures
database queries within the test (if any) work correctly.

When checking for amounts in HTML, remember that template rendering may
format Decimal values. For example, `Decimal("1200.00")` might render
as `1200.00` or `1,200.00` or `$1,200.00`. Use a substring that
appears regardless of formatting. For unique names (like "Rent Payment"
vs "Second User Rent"), the name itself is the best marker.

## Implementation Specification

### File: `tests/test_integration/test_data_isolation.py`

```python
"""
Shekel Budget App - Data Isolation Integration Tests

Verifies that each user sees only their own data on every page and
endpoint. Two users with complete, distinguishable datasets are
created via fixtures, and every user-facing page is tested to confirm
that neither user can see the other's data in the HTTP response body.

These tests catch missing user_id filters in queries, template context
leaks, and any other path that would expose one user's financial data
to another user.
"""
```

### Class: TestGridIsolation

The grid is the primary budget view at `/`. Transaction names appear
in the grid cells.

```
test_user_a_sees_own_transactions
```

User A (`auth_client`) GETs `/`. Assert 200. Assert response contains
`b"Rent Payment"` (User A's transaction name). Assert response does
NOT contain `b"Second User Rent"` (User B's transaction name).

```
test_user_b_sees_own_transactions
```

User B (`second_auth_client`) GETs `/`. Assert 200. Assert response
contains `b"Second User Rent"`. Assert does NOT contain
`b"Rent Payment"`.

NOTE: The grid requires periods to exist and an anchor period to be
set, which the fixtures handle. If the grid renders a "no setup" or
"no periods" page, the test will fail the positive assertion, which is
correct -- it means the fixture is broken.

### Class: TestAccountsIsolation

The accounts page has two relevant views:

- `/savings` (the accounts dashboard) shows account cards with names,
  balances, and savings goals
- `/accounts` (manage accounts) shows the account list table

Test both.

```
test_user_a_savings_dashboard
```

User A GETs `/savings`. Assert 200. Assert response contains
`b"Emergency Fund"` (User A's savings goal). Assert does NOT contain
`b"Vacation Fund"` (User B's savings goal).

```
test_user_b_savings_dashboard
```

User B GETs `/savings`. Assert 200. Assert contains
`b"Vacation Fund"`. Assert does NOT contain `b"Emergency Fund"`.

```
test_user_a_sees_own_anchor_balance
```

User A GETs `/savings`. Assert 200. Assert response contains
`b"1,000"` or `b"1000"` (User A's checking anchor balance).
Assert does NOT contain `b"2,000"` or `b"2000"` (User B's balance).

To handle formatting variations, check for the distinguishing digits.
User A has $1,000 and User B has $2,000. Since "$1,000" and "$2,000"
are the only checking balances, searching for `b"2,000"` not in User
A's response is meaningful. However, be cautious: other numbers on
the page might coincidentally contain "2000". A safer approach: check
that User A's savings goal name appears but User B's does not. The
balance check is supplementary.

```
test_user_b_sees_own_anchor_balance
```

User B GETs `/savings`. Assert 200. Assert `b"Vacation Fund"` present.
Assert `b"Emergency Fund"` NOT present.

```
test_user_a_manage_accounts
```

User A GETs `/accounts`. Assert 200.
Read the response. Since both users have accounts named "Checking"
and "Savings" (identical names), you CANNOT distinguish by name alone.
Instead, verify that the page renders successfully and that User B's
specific data does not leak through. Use the account IDs from the
fixture data: assert that `str(data_b["account"].id).encode()` does
NOT appear in an account edit URL pattern in the response. For example,
check that `/accounts/{user_b_account_id}/edit` does not appear.

Alternatively: since both users have the same account names, this
test should verify the page loads, check for account count (User A has
2 accounts, not 4), and verify User A's specific account IDs appear.

```
test_user_b_manage_accounts
```

Same approach for User B.

### Class: TestTemplatesIsolation

Templates list at `/templates`.

```
test_user_a_sees_own_templates
```

User A GETs `/templates`. Assert 200. Assert `b"Rent Payment"` present.
Assert `b"Second User Rent"` NOT present.

```
test_user_b_sees_own_templates
```

User B GETs `/templates`. Assert 200. Assert `b"Second User Rent"`
present. Assert `b"Rent Payment"` NOT present.

### Class: TestTransfersIsolation

Transfers list at `/transfers`.

```
test_user_a_sees_own_transfers
```

User A GETs `/transfers`. Assert 200. Assert `b"Monthly Savings"`
present (User A's transfer template name). Assert `b"Bi-Weekly Savings"`
NOT present (User B's transfer template name).

```
test_user_b_sees_own_transfers
```

User B GETs `/transfers`. Assert 200. Assert `b"Bi-Weekly Savings"`
present. Assert `b"Monthly Savings"` NOT present.

### Class: TestSalaryIsolation

Salary list at `/salary`.

```
test_user_a_sees_own_profiles
```

User A GETs `/salary`. Assert 200. Assert `b"Day Job"` present (User
A's salary profile name). Assert `b"Second Job"` NOT present.

```
test_user_b_sees_own_profiles
```

User B GETs `/salary`. Assert 200. Assert `b"Second Job"` present.
Assert `b"Day Job"` NOT present.

### Class: TestSavingsGoalsIsolation

Savings goals appear on the savings dashboard at `/savings`.

```
test_user_a_sees_own_goals
```

User A GETs `/savings`. Assert 200. Assert `b"Emergency Fund"` present.
Assert `b"Vacation Fund"` NOT present.

```
test_user_b_sees_own_goals
```

User B GETs `/savings`. Assert 200. Assert `b"Vacation Fund"` present.
Assert `b"Emergency Fund"` NOT present.

### Class: TestCategoriesIsolation

Categories appear at `/settings?section=categories`.

PROBLEM: Both users have identical category names (Salary, Rent, Car
Payment, Groceries, Payback). You cannot distinguish users by category
name.

SOLUTION: Verify isolation by checking that the total number of
categories shown on the page matches what one user should see (5), not
both combined (10). Also verify the page loads for both users, proving
each can access settings independently.

```
test_user_a_sees_own_categories
```

User A GETs `/settings?section=categories`. Assert 200. Count
occurrences of the word "Rent" (or another category item) in the
response. Assert the count matches the expected number for one user
(exactly 1 occurrence of "Rent" as a category item, not 2). Also
check that the page contains "Categories" to confirm the section
loaded.

A more robust approach: count the total number of category delete
buttons or category rows. Each user has 5 categories, so User A
should see 5 category items, not 10.

```
test_user_b_sees_own_categories
```

Same approach for User B.

```
test_categories_not_doubled
```

Use BOTH auth_client and second_auth_client. GET the categories
section for each. Parse the response body for each and count the
number of delete form actions (each category has a delete action).
Assert both counts equal 5.

### Class: TestChartsIsolation

Charts dashboard at `/charts`. The main dashboard page is a shell
that loads chart fragments via HTMX. The chart data endpoints require
the `HX-Request: true` header.

For the balance-over-time chart, account names appear in the chart
data. However, chart data is typically embedded in data attributes or
JSON within HTML, not necessarily as visible text.

```
test_charts_dashboard_loads_for_user_a
```

User A GETs `/charts`. Assert 200. This is a baseline test that the
page loads. The dashboard itself does not contain user-specific data
(it is a shell with HTMX placeholders).

```
test_charts_dashboard_loads_for_user_b
```

User B GETs `/charts`. Assert 200.

```
test_balance_chart_user_a
```

User A GETs `/charts/balance-over-time` with header
`{"HX-Request": "true"}`. Assert 200. The response is an HTML
fragment with chart data. Assert `b"Checking"` is present (the
account name appears in the chart dataset label). Since both users
have accounts named "Checking", this test primarily verifies the
endpoint works. For stronger isolation: check that User A's specific
account ID appears and User B's does not. Use
`str(data_a["account"].id).encode()` in the assertion.

```
test_balance_chart_user_b
```

Same for User B.

### Class: TestSettingsIsolation

Settings dashboard at `/settings`.

The general section shows user-specific settings values. Since the
fixtures use model defaults for UserSettings, both users have the
same setting values. Instead, test the section that shows user-
specific data: the security section shows the user's email.

```
test_user_a_sees_own_email
```

User A GETs `/settings?section=security`. Assert 200.
Assert `b"test@shekel.local"` present (User A's email).
Assert `b"second@shekel.local"` NOT present.

```
test_user_b_sees_own_email
```

User B GETs `/settings?section=security`. Assert 200.
Assert `b"second@shekel.local"` present.
Assert `b"test@shekel.local"` NOT present.

If the security section does not render the user's email in a
discoverable way, fall back to testing the general section. Both
users have the same defaults, so assert the page loads for each
user as a minimum.

IMPORTANT: Before writing the settings tests, read
`app/templates/settings/_security.html` and
`app/templates/settings/_general.html` to see what user-specific
data actually appears. Adjust the assertions to match what the
template renders.

### Class: TestRetirementIsolation

Retirement dashboard at `/retirement`.

The fixtures do NOT create PensionProfile objects for either user.
The retirement dashboard shows pension profiles and retirement
accounts. Since neither user has pension profiles or retirement-type
accounts, both users see essentially empty retirement dashboards.

This means a name-based isolation test is not meaningful here.
Instead:

```
test_user_a_retirement_loads
```

User A GETs `/retirement`. Assert 200. Assert the page renders (check
for a known heading like `b"Retirement"` or `b"retirement"`).

```
test_user_b_retirement_loads
```

User B GETs `/retirement`. Assert 200. Assert the page renders.

```
test_retirement_no_cross_user_data
```

User A GETs `/retirement`. Assert User B's email or display name
does NOT appear. User B GETs `/retirement`. Assert User A's email
or display name does NOT appear. This is a basic leak check even
when the page has no domain data.

NOTE: If you want stronger retirement isolation tests, you could
add PensionProfile objects to the full data fixtures within THIS
test file using a local fixture or inline setup. However, this is
optional and goes significantly beyond the plan's scope. The primary
goal is to verify the page loads and no cross-user data leaks.

## Discovery-First Approach for Tricky Pages

For pages where the content is not obvious from route code alone
(settings, charts, retirement), use this approach:

1. Read the template file to see what variables are rendered.
2. Read the route handler to see what data is passed to the template.
3. Choose assertion targets based on what actually appears in the HTML.

Do NOT guess what appears on a page. Read the template first.

Specifically, before writing tests for each class:

- TestGridIsolation: read `app/templates/grid/grid.html`
- TestAccountsIsolation: read `app/templates/savings/dashboard.html`
  and `app/templates/accounts/list.html`
- TestTemplatesIsolation: read `app/templates/templates/list.html`
- TestTransfersIsolation: read `app/templates/transfers/list.html`
- TestSalaryIsolation: read `app/templates/salary/list.html`
- TestChartsIsolation: read `app/templates/charts/dashboard.html` and
  `app/templates/charts/_balance_over_time.html`
- TestSettingsIsolation: read `app/templates/settings/dashboard.html`
  and the section partials
- TestRetirementIsolation: read
  `app/templates/retirement/dashboard.html`

If a template does not render the data you expect (e.g., the grid
renders transaction amounts but not names), adjust the assertion to
match what actually appears. The goal is testing real isolation, not
matching a preconceived notion of what the page shows.

## Handling Redirects and Edge Cases

Some pages may redirect:

- `/categories` redirects to `/settings?section=categories`. Use the
  settings URL directly, not `/categories`.
- Chart fragment endpoints redirect to the dashboard if the
  `HX-Request` header is missing. Always include the header.

Some pages require specific data to render:

- The grid requires periods and an anchor period. The fixtures set
  these up.
- The salary page might show a "no profiles" state if something went
  wrong with the fixture. The positive assertion ("Day Job" is present)
  catches this -- it will fail if the page is empty.

## Execution Checklist

After implementing everything, run these checks in order:

1. Verify `tests/test_integration/__init__.py` exists (created in
   WU-3).

2. `pylint tests/test_integration/test_data_isolation.py` -- fix all
   issues.

3. `pytest tests/test_integration/test_data_isolation.py -v` -- all
   must pass. If any positive assertion fails ("own data not found"),
   the fixture is likely broken or the page renders data differently
   than expected. Read the template to find the correct assertion
   marker. If a negative assertion fails ("other user's data found"),
   you have found a real isolation bug. Report it.

4. `pytest` -- the FULL suite. All existing tests plus WU-1 through
   WU-3 tests plus your new tests must pass. Zero failures.

If a test fails because the page does not render the expected content:

- Do NOT weaken the assertion. Instead, read the template to find
  what IS rendered and use a correct assertion.
- Do NOT remove the negative assertion. If you cannot find a
  distinguishable marker, add a comment explaining why and use the
  best available marker (e.g., user email, display name, account ID
  in a URL).
- Do NOT use `follow_redirects=True` on the main page requests unless
  the page genuinely redirects (like `/categories`).

## Things You Must NOT Do

- Do not modify any existing file (no routes, no services, no models,
  no conftest.py, no existing test files).
- Do not create any file other than
  `tests/test_integration/test_data_isolation.py`.
- Do not create any migration file.
- Do not add any Python packages.
- Do not write `pass` or `TODO` in any test body.
- Do not use `assert True` or any assertion that always passes.
- Do not write a test that only checks `response.status_code == 200`
  without also checking response content.
- Do not write a test that only checks negative assertions (other
  user's data absent) without also checking positive assertions (own
  data present).
- Do not mock the database.
- Do not use `float` for any numeric value.
- Do not use em dashes or en dashes anywhere (use hyphens instead).
- Do not assume what content appears on a page. Read the template
  first.
