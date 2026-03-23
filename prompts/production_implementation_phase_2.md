# Claude Code Prompt: Shekel Phase 2 -- Security and Data Isolation

You are implementing Phase 2 of the Shekel production readiness plan. Phase 1 is complete. This is a personal budgeting application where errors have real financial consequences. There is no QA team. You are the only safeguard between this code and production. Every change must be correct, tested, and verifiable. Do not guess. Do not assume. Do not take shortcuts.

Phase 2 is about preventing information disclosure to unauthorized users. Every fix in this phase closes a path where one user could see another user's financial data: category names, pay period dates, or period structures. These are IDOR (Insecure Direct Object Reference) vulnerabilities. A user who knows (or guesses) the database ID of another user's resource can currently retrieve information about it through form-rendering endpoints. The data modification endpoints already have ownership checks, so no data can be CHANGED, but it can be SEEN. That is unacceptable for a financial application.

---

## Ground Rules (Read These First -- They Are Non-Negotiable)

1. **Read before you write.** Before changing ANY file, read the ENTIRE file first. Do not rely on line number references from this prompt or any planning document. Line numbers shift between commits. Phase 1 changed files. Find the actual code by reading the file.

2. **Verify before you fix.** Before implementing each fix, confirm the problem still exists in the current code. If a fix has already been applied (possibly during Phase 1 or Phase 8E work), skip it and document that you skipped it and why.

3. **One fix at a time.** Implement one item, write its tests, run the tests, confirm green, then commit. Do not batch fixes. Each commit must be atomic and individually revertable. The one exception: P2-1, P2-2, and P2-3 are the same fix applied to three endpoints in the same file. You MAY commit those together as a single atomic change since they share a file and a root cause, but only if you write separate tests for each endpoint.

4. **Run pylint after every change.** The baseline is 9.32/10 or higher with zero real errors. Do not decrease this score. All new code must have docstrings and inline comments explaining non-obvious logic. Use: `pylint app/ --fail-on=E,F`

5. **Match the existing code style exactly.** Study the patterns already in the codebase before writing new code. The project has established patterns for ownership checks, test fixtures, IDOR testing, and configuration. Use them. Do not invent new patterns.

6. **Commit messages must be precise.** Format: `fix(<scope>): <what changed> (<audit ID>)`. Example: `fix(transactions): add ownership checks to form-rendering GET endpoints (H1)`.

7. **All work happens on the `dev` branch.** Confirm you are on `dev` before starting. Confirm the working tree is clean before starting.

---

## Pre-Flight Checklist (Do This Before Any Code Changes)

Run these steps in order. Do not skip any. Document the output of each.

```
# 1. Confirm branch and clean working tree
git branch --show-current
git status

# 2. Record the current commit hash (this is your rollback point)
git log --oneline -1

# 3. Confirm all six Phase 2 findings still exist (read each file)
```

For step 3, you must read and confirm each of the following. Do not proceed to fixes until all six are confirmed or marked as already-resolved:

- **P2-1/P2-2/P2-3:** Open `app/routes/transactions.py`. Find the three functions: `get_quick_create`, `get_full_create`, and `get_empty_cell`. For EACH function, read the code where `Category` and `PayPeriod` are loaded by ID from request parameters. Confirm that the existence check does NOT verify `user_id` against `current_user.id`. Also read the data modification endpoints in the same file (`create_inline`, `create_transaction`) and confirm they DO have ownership checks, as the audit states. This tells you the pattern to follow.

- **P2-4:** Open `app/routes/templates.py`. Find the `preview_recurrence` function. Read the code where `start_period` is loaded by ID. Confirm it checks `if start_period:` (existence only) rather than `if start_period and start_period.user_id == current_user.id:` (existence + ownership). Also read the fallback behavior when `effective_from is None` to understand what happens when the ownership check fails gracefully.

- **P2-5:** Open `app/routes/auth.py`. Find the `register` function (the POST handler). Confirm there is no `@limiter.limit(...)` decorator on it. Also find the `login` function and note the exact rate limit string it uses (e.g., `"5 per 15 minutes"`) so you can use a consistent style.

- **P2-6:** Open `app/config.py`. Confirm there is NO `REGISTRATION_ENABLED` config variable in any config class. Open `app/routes/auth.py`. Confirm the register GET and POST routes do NOT check any toggle or feature flag.

---

## Critical: Understand the Existing Test Infrastructure

Before writing ANY tests, you MUST read these files in full:

1. **`tests/conftest.py`** -- Read the entire file. The project has established two-user test fixtures. Look for fixtures named `second_user`, `seed_second_user`, `seed_second_periods`, and `second_auth_client`. Understand their structure, what they return (a dict with keys like `user`, `account`, `scenario`, `categories`), and how they mirror the `seed_user` fixture. YOU MUST USE THESE EXISTING FIXTURES. Do not create your own second user setup in test methods.

2. **`tests/test_integration/test_fixture_validation.py`** -- This file validates the two-user fixtures. Read it to understand the exact shape of the fixture dicts.

3. **Existing IDOR tests** -- Search for existing IDOR test patterns:
   ```
   grep -rn "IDOR\|idor\|other_user\|second_user\|seed_second" tests/
   ```
   Multiple test files already test IDOR scenarios. Study their patterns: how they create two users, log in as one, attempt to access the other's resources, and assert 404. Your tests must follow the same patterns exactly.

4. **`tests/test_routes/test_transaction_auth.py`** -- If this file exists, read it. The implementation plan suggests adding IDOR tests here. If IDOR tests for transaction endpoints already exist (possibly from Phase 8E work), you do not need to duplicate them. Confirm what is already tested and what is missing.

5. **`tests/test_routes/test_templates.py`** -- Read this file. Look for existing IDOR tests on `preview_recurrence`. If they exist, skip writing new ones.

---

## P2-1, P2-2, P2-3: Add Ownership Checks to Transaction Form-Rendering Endpoints

### Context

Three GET endpoints in `app/routes/transactions.py` load `Category` and `PayPeriod` objects by ID from query/form parameters and render form fragments. They check that the objects exist but do not verify the requesting user owns them. This lets an attacker enumerate IDs to discover another user's category names (which may contain financial details like "Auto: Car Payment $450") and pay period dates.

The data modification endpoints in the same file (`create_inline`, `create_transaction`) already have correct ownership checks. Study those implementations to understand the established pattern, then apply the same pattern to the form-rendering endpoints.

### Implementation

1. Read `app/routes/transactions.py` in full. Identify:
   - `get_quick_create` -- loads Category and PayPeriod by ID
   - `get_full_create` -- same pattern
   - `get_empty_cell` -- same pattern
   - The existing ownership check pattern used in mutation endpoints

2. For each of the three functions, replace the bare existence check with an ownership-verified check. The pattern is:
   ```python
   if not category or category.user_id != current_user.id:
       return "Not found", 404
   if not period or period.user_id != current_user.id:
       return "Not found", 404
   ```

   IMPORTANT: The response for "does not exist" and "belongs to another user" MUST be identical. Both return 404 with the same body. This prevents an attacker from distinguishing between "this ID does not exist" and "this ID exists but belongs to someone else" (which would confirm the existence of another user's resource).

3. Add a brief inline comment on the first endpoint explaining the IDOR fix. The other two endpoints have the same pattern, so a comment on those referencing the first is sufficient:
   ```python
   # Ownership check: prevent IDOR -- see audit finding H1.
   ```

4. Search the ENTIRE transactions route file for any other endpoints that load Category or PayPeriod by ID from user input without ownership checks. The audit identified these three, but verify there are no others. If you find additional unprotected endpoints, fix them in the same commit and document the discovery.

### Test Requirements

Use the existing two-user fixtures (`seed_user`, `seed_second_user`, `seed_second_periods`, `auth_client`). Do NOT create ad-hoc second users in your tests.

Check if `tests/test_routes/test_transaction_auth.py` already has IDOR tests for these endpoints. If it does, verify they cover the scenarios below. If not, add tests.

You need tests for EACH of the three endpoints, and for EACH resource type (Category and PayPeriod). That means at minimum six test cases, but they can be organized however makes sense given the existing test file structure. Each test must:

1. Log in as User A via `auth_client`.
2. Request the endpoint with User B's resource ID(s).
3. Assert a 404 response.
4. Also test the "both resources belong to different users" case: User A's category but User B's period (and vice versa). This catches implementations that only check one of the two resources.

Additionally, write positive tests confirming that accessing your OWN resources still works (200 response). Regression tests are not optional. If the existing test suite already covers the positive cases, note that and skip.

Test naming should be descriptive:
- `test_quick_create_rejects_other_users_category`
- `test_quick_create_rejects_other_users_period`
- `test_quick_create_rejects_mixed_ownership_resources`
- `test_full_create_rejects_other_users_category`
- etc.

### Verification

```
pytest tests/test_routes/test_transaction_auth.py -v          # All IDOR tests pass
pylint app/routes/transactions.py                             # No new warnings
```

### Commit

```
git add app/routes/transactions.py tests/
git commit -m "fix(transactions): add ownership checks to form-rendering GET endpoints (H1)"
```

---

## P2-4: Add Ownership Check to `preview_recurrence`

### Context

The `preview_recurrence` HTMX endpoint in `app/routes/templates.py` accepts a `start_period_id` from query/form parameters, loads the PayPeriod by ID, and uses its `start_date` to determine the effective start date for recurrence pattern matching. It does not verify the requesting user owns the period.

An attacker could pass another user's period ID to trigger pattern matching against that user's pay period schedule, leaking information about their pay period structure (start dates, frequency, etc.).

The fix is subtle: this endpoint has a graceful fallback. If `start_period` is `None` or fails the ownership check, the code falls through to a block that uses the current user's own periods. This means the fix is a single conditional change, but you must understand the fallback behavior to verify it is correct.

### Implementation

1. Read `app/routes/templates.py` in full. Find `preview_recurrence`.

2. Read the entire function to understand the flow:
   - It loads `start_period` by ID.
   - If `start_period` exists, it uses `start_period.start_date` as `effective_from`.
   - If `effective_from` is still `None` (start_period was None or not found), it falls through to a block that queries the current user's pay periods and uses the first one.

3. Change the condition from:
   ```python
   if start_period:
   ```
   to:
   ```python
   if start_period and start_period.user_id == current_user.id:
   ```

   This means if an attacker passes another user's period ID, the code treats it as if no `start_period_id` was provided, and falls through to the user's own period data. The attacker receives a valid response based on THEIR OWN data, not the victim's, and cannot distinguish the fallback from a normal response.

4. Add an inline comment:
   ```python
   # Ownership check: reject other users' periods to prevent
   # pay period structure disclosure -- see audit finding H3.
   # Falls through to the current user's own periods below.
   ```

5. While you are reading `templates.py`, check for any OTHER endpoints that load resources by ID from user input without ownership checks. The audit identified only `preview_recurrence`, but verify. If you find others, document them but do NOT fix them in this commit unless they are trivial and obviously within scope.

### Test Requirements

Check if `tests/test_routes/test_templates.py` already has an IDOR test for `preview_recurrence`. If it does, verify it covers the scenario below. If not, add a test.

The test must:

1. Create User A and User B, each with their own pay periods (use the existing `seed_user`/`seed_periods` and `seed_second_user`/`seed_second_periods` fixtures).

2. Log in as User A.

3. Determine the correct request format for `preview_recurrence`. Read the endpoint to find:
   - What HTTP method it expects (GET or POST)
   - What parameters it expects (likely form data or query params including `start_period_id`, recurrence pattern, interval, etc.)
   - What a valid request looks like (check existing tests for this endpoint)

4. Send a request with `start_period_id` set to one of User B's period IDs, but with valid values for all other parameters (belonging to User A).

5. Assert the response is 200 (NOT 404 -- this endpoint falls through gracefully rather than returning an error).

6. Assert the response content uses dates from User A's period schedule, NOT User B's. This is the critical assertion. You need to know User A's period start dates and User B's period start dates, and verify the response contains User A's dates. Study the endpoint's template rendering to understand what dates appear in the response HTML.

7. As a control, also test with User A's own `start_period_id` and verify the same response format (to confirm the endpoint works correctly for legitimate requests).

### Verification

```
pytest tests/test_routes/test_templates.py -v -k preview      # Preview recurrence test passes
pylint app/routes/templates.py                                 # No new warnings
```

### Commit

```
git add app/routes/templates.py tests/
git commit -m "fix(templates): add ownership check to preview_recurrence start_period_id (H3)"
```

---

## P2-5: Add Rate Limiting to Registration

### Context

The `/register` POST endpoint has no rate limiting. The `/login` endpoint has `@limiter.limit("5 per 15 minutes")`. An attacker could create unlimited accounts via automated requests, filling the database, consuming resources, and potentially enabling further attacks.

### Implementation

1. Read `app/routes/auth.py` in full. Find the `register` function (POST handler). Note the exact decorator order on the login function so you can mirror it for register.

2. Verify that `limiter` is already imported. Look for `from app.extensions import limiter` or similar at the top of the file. If it is not imported, add it.

3. Add `@limiter.limit("3 per hour", methods=["POST"])` to the register POST function. Place the decorator in the same position relative to `@auth_bp.route(...)` as the login function's limiter decorator is to its route decorator. Decorator order matters in Flask.

4. ALSO rate-limit the register GET route (the form page). While less critical than the POST route, an attacker enumerating whether registration is available should still be rate-limited. Use a more generous limit: `@limiter.limit("10 per hour", methods=["GET"])`. If the GET and POST handlers are the same function (a single function handling both methods), use the more restrictive POST limit and add a comment explaining why.

5. Add an inline comment:
   ```python
   # Rate limit registration to prevent automated mass account creation.
   # See audit finding H5.
   ```

### Test Requirements

The test configuration disables rate limiting (`RATELIMIT_ENABLED = False` in `TestConfig`). This makes directly testing rate limit enforcement difficult in the standard test environment. You have two options:

**Option A (preferred): Inspect the decorator programmatically.** Verify that the rate limit decorator is present on the register function. This does not test enforcement but confirms the decorator was applied:
```python
def test_register_post_has_rate_limit_decorator(self, app):
    """The register POST endpoint has a rate limit decorator."""
    with app.app_context():
        # Find the register endpoint and verify it has rate limiting
        # configured. The exact inspection method depends on how
        # flask-limiter attaches metadata. Study how existing rate
        # limit tests (if any) verify this, or inspect the function's
        # __wrapped__ or decorator metadata.
```

**Option B: Temporarily enable rate limiting in a specific test.** Override the config for a single test to enable rate limiting, then verify the 4th POST to `/register` returns 429. This tests actual enforcement but is more fragile:
```python
def test_register_rate_limit_enforced(self, app, client):
    """Registration is rate-limited to 3 per hour."""
    app.config["RATELIMIT_ENABLED"] = True
    # ... make 4 POST requests, assert 4th returns 429
    # Restore config after test
```

Choose whichever option matches the existing test patterns in the project. Search for existing rate limit tests: `grep -rn "rate_limit\|429\|RATELIMIT" tests/`. If existing tests use a particular approach, follow that approach.

### Verification

```
pytest tests/test_routes/test_auth.py -v -k rate_limit   # Rate limit test(s) pass
pylint app/routes/auth.py                     # No new warnings
```

### Commit

```
git add app/routes/auth.py tests/
git commit -m "fix(auth): add rate limiting to registration endpoint (H5)"
```

---

## P2-6: Add Admin-Controlled Registration Toggle

### Context

Anyone who can reach the app can create an account at `/register`. For LAN-only deployment this is acceptable. For external access via Cloudflare Tunnel or similar, this means anyone with the URL can register and create an account. The owner of the application needs the ability to disable public registration.

This is the most complex item in Phase 2. It touches configuration, routes, templates, and environment files. Take extra care.

### Implementation

1. **Read the existing config structure.** Open `app/config.py`. Study how `BaseConfig`, `DevConfig`, `TestConfig`, and `ProdConfig` are organized. Note how existing boolean config values are parsed from environment variables (e.g., look at `DEBUG`, `TESTING`, or any config that reads a boolean from an env var). Follow the same parsing pattern for `REGISTRATION_ENABLED`.

2. **Add the config variable.** In `BaseConfig`, add:
   ```python
   REGISTRATION_ENABLED = os.getenv(
       "REGISTRATION_ENABLED", "true"
   ).lower() in ("true", "1", "yes")
   ```

   Use `in ("true", "1", "yes")` rather than `== "true"` to be tolerant of common boolean representations. Add a docstring or inline comment explaining the variable. If `BaseConfig` does not already import `os`, check where `os` is imported in the file and add it if needed.

   Consider whether `TestConfig` should override this. Tests that exercise registration need it enabled. If `TestConfig` does not explicitly set it, it inherits `true` from `BaseConfig` (since `REGISTRATION_ENABLED` defaults to `"true"` when the env var is unset). This is correct for tests. Confirm this by checking that `TestConfig` does not set `REGISTRATION_ENABLED` to anything unexpected.

3. **Guard both register routes.** Open `app/routes/auth.py`. Find BOTH the register form route (GET handler) and the register submit route (POST handler). They may be one function or two separate functions. At the TOP of each function body (before any other logic), add:
   ```python
   if not current_app.config["REGISTRATION_ENABLED"]:
       abort(404)
   ```

   Use `current_app.config["REGISTRATION_ENABLED"]` (bracket notation), NOT `current_app.config.get("REGISTRATION_ENABLED", True)`. The bracket notation will raise a `KeyError` if the config variable is missing, which is correct behavior: a missing config variable is a deployment error and should fail loudly, not silently default to open registration.

   Verify that `abort` is imported from `flask`. It should be already, but check.

   Use `abort(404)` rather than `abort(403)`. A 404 does not confirm to an attacker that the registration endpoint exists but is disabled. A 403 tells them "this exists, you just cannot use it," which leaks information.

   Add an inline comment:
   ```python
   # Registration toggle: return 404 (not 403) to avoid confirming
   # the endpoint exists when disabled. See audit finding H6.
   ```

4. **Hide the Register link in the login template.** Find the login template (likely `app/templates/auth/login.html`). Look for a link to the register page (text like "Register", "Sign up", "Create an account", or a link to `url_for('auth.register_form')` or similar). Wrap it in a Jinja2 conditional:
   ```html
   {% if config.REGISTRATION_ENABLED %}
   <a href="{{ url_for('auth.register_form') }}">Register</a>
   {% endif %}
   ```

   Make sure you find ALL register links in ALL templates, not just the login page. Search:
   ```
   grep -rn "register" app/templates/
   ```

   Any link that points to the register route should be conditional. Do NOT hide links in the base template's navigation if they do not exist (some apps only show register links on the login page).

5. **Update `.env.example`.** Add the new variable with a comment:
   ```
   # Registration toggle. Set to 'false' to disable public registration.
   # When disabled, /register returns 404 and the registration link is hidden.
   # Default: true
   REGISTRATION_ENABLED=true
   ```

   Place it logically near other auth-related variables (near `SEED_USER_EMAIL`, `SEED_USER_PASSWORD`, or at the end of the auth section).

6. **Check `docker-compose.yml`.** If the docker-compose file has an environment section for the app service, decide whether to add `REGISTRATION_ENABLED` there. Since the default is `true` (open registration), it does NOT need to be in docker-compose.yml. But if docker-compose.yml already lists every config variable with `${VAR:-default}` syntax, follow the pattern and add it for consistency. Read the file and make a judgment call.

### Test Requirements

Write tests covering four scenarios:

1. **Registration enabled (default): GET returns 200.**
   - With the default config (or `REGISTRATION_ENABLED=True` explicitly), GET `/register` returns 200 with the registration form.

2. **Registration enabled: POST creates user.**
   - POST to `/register` with valid data creates a user and redirects to login. This may already be covered by existing registration tests. If so, confirm and skip.

3. **Registration disabled: GET returns 404.**
   - Override the app config to set `REGISTRATION_ENABLED = False`.
   - GET `/register` returns 404.
   - The response body does NOT contain the word "register" (confirming the form is not leaked in a custom 404 page).

4. **Registration disabled: POST returns 404.**
   - Same config override.
   - POST `/register` with valid registration data returns 404.
   - Confirm no user was created in the database. This is the critical test: even if someone bypasses the UI and POSTs directly, the server must reject it.

For config overrides in tests, study how the existing test suite handles per-test config changes. Common patterns:
- `app.config["REGISTRATION_ENABLED"] = False` at the start of the test
- A pytest fixture that creates an app with a custom config
- Monkeypatching

Use whichever pattern the project already uses. Search: `grep -rn "app.config\[" tests/` to find examples.

Also write a test for the template conditional:

5. **Login page hides register link when disabled.**
   - With `REGISTRATION_ENABLED = False`, GET `/login` returns 200.
   - The response body does NOT contain a link to the register page (search for the register URL or the word "Register" as a link).
   - With `REGISTRATION_ENABLED = True`, GET `/login` returns 200 and DOES contain the register link.

### Verification

```
pytest tests/test_routes/test_auth.py -v -k registration      # Registration toggle tests pass
pylint app/config.py app/routes/auth.py                       # No new warnings
```

### Commit

```
git add app/config.py app/routes/auth.py app/templates/ .env.example tests/
# Also add docker-compose.yml if you modified it
git commit -m "feat(auth): add REGISTRATION_ENABLED toggle to disable public registration (H6)"
```

---

## Post-Phase Checklist (Do This After All Six Items Are Complete)

```
# 1. Confirm all tests pass
pytest --tb=short -q
# Expected: post-Phase-1 count + new tests, all passing, zero failures

# 2. Confirm pylint score
pylint app/ --fail-on=E,F --output-format=text 2>&1 | tail -5
# Expected: 9.32/10 or higher, zero E/F messages

# 3. Confirm all commits are present and atomic
git log --oneline -8
# Should show your Phase 2 commits (3-4 depending on whether P2-1/2/3 were combined)

# 4. Confirm no untracked or unstaged files
git status
# Should be clean

# 5. Verify the IDOR fixes are comprehensive -- search for remaining unguarded patterns
grep -n "db.session.get(Category" app/routes/transactions.py
grep -n "db.session.get(PayPeriod" app/routes/transactions.py
grep -n "db.session.get(PayPeriod" app/routes/templates.py
# Every result should be followed by a user_id ownership check within 1-3 lines

# 6. Verify the registration toggle works at the config level
python -c "
import os
os.environ['REGISTRATION_ENABLED'] = 'false'
from app.config import BaseConfig
assert BaseConfig.REGISTRATION_ENABLED == False, 'Toggle did not parse false'
os.environ['REGISTRATION_ENABLED'] = 'true'
# Note: BaseConfig reads env at class definition time, so this
# tests the parsing logic. In practice, the app reads config at startup.
print('Config parsing verified.')
"

# 7. Print a summary of what was done
echo "Phase 2 complete. Changes:"
echo "  P2-1/2/3: ownership checks on transaction form-rendering endpoints (H1)"
echo "  P2-4: ownership check on preview_recurrence start_period_id (H3)"
echo "  P2-5: rate limiting on registration endpoint (H5)"
echo "  P2-6: REGISTRATION_ENABLED toggle for public registration (H6)"
echo ""
echo "Test count:"
pytest --co -q 2>&1 | tail -1
```

---

## What "Done Right" Means for This Phase

- Every IDOR fix returns 404 for both "does not exist" and "belongs to another user." The responses are indistinguishable. An attacker cannot tell whether a guessed ID is valid or not.
- Every IDOR fix has a test that logs in as User A, requests User B's resource, and asserts 404. The test uses the project's existing two-user fixtures, not ad-hoc user creation.
- Every IDOR fix also has a positive regression test confirming access to your OWN resources still works.
- The rate limit decorator follows the same decorator ordering pattern as the existing login rate limit.
- The registration toggle uses 404 (not 403) to avoid confirming the endpoint exists.
- The registration toggle guards BOTH the GET and POST handlers. A direct POST with registration disabled returns 404 and creates no database records.
- The registration link is hidden from ALL templates when registration is disabled, not just the login page.
- The `.env.example` documents the new variable with a clear explanation.
- All new Python code has docstrings and inline comments.
- All new Python code conforms to pylint standards.
- No existing test is broken.
- No pylint score decrease.
- Every commit is atomic and can be individually reverted.
- No assumptions about line numbers, function names, or file locations -- everything is verified by reading the actual code first.