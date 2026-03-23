# Claude Code Prompt: Shekel Phase 3 -- Operational Hardening

You are implementing Phase 3 of the Shekel production readiness plan. Phases 1 and 2 are complete. This is a personal budgeting application where errors have real financial consequences. There is no QA team. You are the only safeguard between this code and production. Every change must be correct, tested, and verifiable. Do not guess. Do not assume. Do not take shortcuts.

Phase 3 is about operational hardening: preventing information leaks from error responses, fixing incorrect scoping in database queries, locking down proxy trust, pinning build versions for reproducibility, and adding a user-facing warning for a known financial accuracy edge case. These are not flashy changes, but each one removes a way the application could misbehave, leak data, or produce incorrect financial projections.

---

## Ground Rules (Read These First -- They Are Non-Negotiable)

1. **Read before you write.** Before changing ANY file, read the ENTIRE file first. Do not rely on line number references from this prompt or any planning document. Line numbers shift between commits. Phases 1 and 2 changed files. Find the actual code by reading the file.

2. **Verify before you fix.** Before implementing each fix, confirm the problem still exists in the current code. If a fix has already been applied, skip it and document that you skipped it and why.

3. **One fix at a time.** Implement one item, write its tests, run the tests, confirm green, then commit. Do not batch fixes. Each commit must be atomic and individually revertable.

4. **Run pylint after every change.** The baseline is 9.32/10 or higher with zero real errors. Do not decrease this score. All new code must have docstrings and inline comments explaining non-obvious logic. Use: `pylint app/ --fail-on=E,F`

5. **Match the existing code style exactly.** Study the patterns already in the codebase before writing new code. Do not invent new patterns.

6. **Commit messages must be precise.** Format: `fix(<scope>): <what changed> (<audit ID>)`. Example: `fix(health): remove error details from unhealthy response to prevent info disclosure (M5)`.

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

# 3. Run pylint and confirm the baseline
pylint app/ --fail-on=E,F --output-format=text 2>&1 | tail -5

# 4. Confirm all five Phase 3 findings still exist (read each file)
```

For step 4, you must read and confirm each of the following:

- **P3-1:** Open `app/routes/health.py`. Read the entire file (it is short). Find the `except` block. Confirm the JSON response includes a `"detail": str(exc)` field that exposes exception text to unauthenticated callers. Also confirm that the exception IS already logged via `logger.error` before the return, so removing the detail field does not lose observability.

- **P3-2:** Open `app/routes/categories.py`. Find the delete endpoint's in-use check. Confirm it queries `TransactionTemplate` by `category_id` WITHOUT filtering by `user_id`. Also confirm it queries `Transaction` by `category_id` WITHOUT user scoping. Read the `Transaction` model to confirm it has NO direct `user_id` column (ownership is transitive through `pay_period`).

- **P3-3:** Open `gunicorn.conf.py`. Read the entire file. Find the `forwarded_allow_ips` setting. Confirm it is set to `"*"`. Also note whether `import os` already exists at the top of the file.

- **P3-4:** Open `Dockerfile`. Find both `FROM` lines. Confirm they use `python:3.14-slim` without a patch version. Then open `.github/workflows/ci.yml`. Find the `allow-prereleases: true` line. Confirm it exists.

- **P3-5:** Open `app/services/balance_calculator.py`. Read the entire file. Find the `_sum_all()` function. Confirm it excludes done/received transactions from post-anchor period calculations. Then read `calculate_balances()` and note its return type: it returns an `OrderedDict` mapping `period_id` to `Decimal`. Find ALL callers of `calculate_balances()` in the codebase: `grep -rn "calculate_balances(" app/`. You will need to know every caller to ensure your return value change is backward-compatible.

---

## P3-1: Sanitize Health Check Error Response

### Context

The `/health` endpoint is unauthenticated (by design -- external monitors need to reach it). When the database check fails, the response includes `"detail": str(exc)`, which could contain the database host, port, username, or connection string. This is an information disclosure vulnerability. The exception is already logged server-side, so operators can still diagnose issues via logs.

### Implementation

1. Read `app/routes/health.py` in full.

2. Find the `except` block. Remove the `"detail": str(exc)` line from the JSON response. The response should contain ONLY `"status"` and `"database"` keys.

3. **Also update the docstring.** The function docstring currently documents the 500 response as including `"detail"`. Update it to reflect the new response shape. The docstring is part of the contract -- it must be accurate.

4. Verify the `logger.error` call is BEFORE the return statement and includes the exception. If it does not, add it. The full exception detail must be preserved in logs -- you are removing it from the HTTP response, not from observability.

5. While you are in this file, check whether the healthy response (200) includes any fields that could leak information. It should only contain `"status"` and `"database"`. If it contains anything else, evaluate whether it is safe.

### Test Requirements

Read `tests/test_routes/test_health.py` in full. There is an existing test `test_health_returns_500_on_db_failure` that asserts `"connection refused" in data["detail"]`. This test will BREAK after your change. That is correct -- you must update it.

Update the existing test to assert:

1. Response status is 500.
2. Response JSON has `"status": "unhealthy"`.
3. Response JSON has `"database": "error"`.
4. Response JSON does NOT contain a `"detail"` key.
5. The response body does NOT contain the exception message text (e.g., "connection refused", "password", or any substring of the mocked exception). This is the critical security assertion.

Also add a NEW test that specifically verifies sensitive information is not leaked:

```python
def test_unhealthy_response_does_not_leak_credentials(self, ...):
    """Health check error response must not expose database credentials."""
```

This test should:

1. Mock `db.session.execute` to raise an exception whose message contains realistic sensitive data: `"could not connect to server: password authentication failed for user \"shekel_user\" at host \"192.168.1.50\" port 5432"`.
2. GET `/health`.
3. Assert the response body (as a string) does NOT contain "shekel_user", "192.168.1.50", "5432", or "password". Check each individually.
4. Assert the response JSON keys are exactly `{"status", "database"}` -- no extra fields.

### Verification

```
pytest tests/test_routes/test_health.py -v          # All health tests pass (including updated ones)
pylint app/routes/health.py                         # No new warnings
```

### Commit

```
git add app/routes/health.py tests/test_routes/test_health.py
git commit -m "fix(health): remove error details from unhealthy response to prevent info disclosure (M5)"
```

---

## P3-2: Scope Category Delete In-Use Check by User

### Context

The category delete endpoint checks whether a category is "in use" by querying for `TransactionTemplate` and `Transaction` records that reference it. Neither query filters by `user_id`. This means if User B has a template referencing ANY category (even their own), and that template's `category_id` happens to match User A's category's `id`, User A is incorrectly blocked from deleting their own category. This is not a data leak, but it is a functional bug that worsens with multiple users.

The `Transaction` model does NOT have a direct `user_id` column. Ownership is transitive through `pay_period.user_id`. The implementation plan suggests only fixing the `TransactionTemplate` query (which does have `user_id`) and leaving the `Transaction` query unscoped as "lower priority." We are going to do BOTH fixes because this is a financial application and correctness matters.

### Implementation

1. Read `app/routes/categories.py` in full. Find the delete endpoint. Find the in-use check.

2. Add `user_id=current_user.id` to the `TransactionTemplate` query:

   ```python
   db.session.query(TransactionTemplate)
       .filter_by(category_id=category_id, user_id=current_user.id)
       .first()
   ```

3. **Also scope the `Transaction` query by user.** The `Transaction` model has no `user_id` column, so you must join through `PayPeriod`. Read the `Transaction` model and `PayPeriod` model to confirm the FK relationship:

   ```python
   db.session.query(Transaction)
       .join(PayPeriod, Transaction.pay_period_id == PayPeriod.id)
       .filter(
           PayPeriod.user_id == current_user.id,
           Transaction.category_id == category_id,
           Transaction.is_deleted.is_(False),
       )
       .first()
   ```

   IMPORTANT: Also add `Transaction.is_deleted.is_(False)` to the filter. Soft-deleted transactions should not prevent category deletion. Check whether the existing query already filters by `is_deleted`. If it does not, add it. If it does, keep it.

4. Verify that the `PayPeriod` import is available at the top of the file. If not, add it. Check existing imports in the file to see the import pattern (e.g., `from app.models.pay_period import PayPeriod` or `from app.models import PayPeriod`).

5. Add inline comments explaining both scoping decisions:

   ```python
   # Scope by user_id to prevent other users' templates from
   # blocking deletion. See audit finding M6.
   ```

   and:

   ```python
   # Transaction has no direct user_id -- join through PayPeriod
   # for correct ownership scoping. Exclude soft-deleted records.
   ```

6. While reading the file, check whether there are any OTHER queries in the categories route that lack user scoping. If you find any, document them but fix only if they are trivially within scope.

### Test Requirements

Use the existing two-user fixtures (`seed_user`, `seed_second_user`, `auth_client`). Read `tests/test_routes/test_categories.py` to understand existing test patterns.

Write tests:

1. **User A can delete a category that only User B's template references:**
   - Create User A with a category that has NO templates or transactions referencing it (for User A).
   - Create User B with a template that references User B's own category (a different category object with a different ID).
   - Log in as User A. Delete User A's category.
   - Assert deletion succeeds (check for redirect or 200, whatever the existing delete pattern uses).
   - Verify the category is actually gone from the database.

2. **User A CANNOT delete a category that User A's own template references:**
   - Create User A with a category AND a template referencing that category.
   - Log in as User A. Attempt to delete the category.
   - Assert deletion is blocked (check the existing error response pattern -- flash message, 400, 409, or whatever the endpoint returns for "in use").

3. **User A can delete a category that only has soft-deleted transactions referencing it:**
   - Create User A with a category and a transaction referencing it.
   - Soft-delete the transaction (`is_deleted = True`).
   - Log in as User A. Delete the category.
   - Assert deletion succeeds. Soft-deleted records should not block deletion.

4. **User A CANNOT delete a category that has active (non-deleted) transactions referencing it:**
   - Create User A with a category and an active transaction referencing it.
   - Log in as User A. Attempt to delete the category.
   - Assert deletion is blocked.

### Verification

```
pytest tests/test_routes/test_categories.py -v          # All category tests pass
pylint app/routes/categories.py                         # No new warnings
```

### Commit

```
git add app/routes/categories.py tests/test_routes/test_categories.py
git commit -m "fix(categories): scope delete in-use check by user_id with PayPeriod join (M6)"
```

---

## P3-3: Restrict `forwarded_allow_ips` to Docker Subnet

### Context

Gunicorn's `forwarded_allow_ips = "*"` trusts `X-Forwarded-For` and `X-Forwarded-Proto` headers from any source IP. In the current Docker architecture (Nginx is the sole client on an internal bridge network), this is safe. But if the architecture changes (Gunicorn exposed directly, or a different reverse proxy added), an attacker could spoof `X-Forwarded-For` to bypass IP-based rate limiting or forge HTTPS status.

### Implementation

1. Read `gunicorn.conf.py` in full. Note the existing comment above `forwarded_allow_ips` -- it already acknowledges the risk. Your fix should replace the comment as well, not just the value.

2. Check whether `import os` exists at the top of the file. If not, add it. Check the existing import style (some gunicorn configs use no imports; others import os for env vars).

3. Replace the `forwarded_allow_ips` line with:

   ```python
   forwarded_allow_ips = os.getenv(
       "FORWARDED_ALLOW_IPS",
       "172.16.0.0/12,192.168.0.0/16,10.0.0.0/8",
   )
   ```

4. Update the comment above it:

   ```python
   # Trust X-Forwarded-* headers only from RFC 1918 private subnets,
   # which covers all Docker bridge networks. Override via the
   # FORWARDED_ALLOW_IPS environment variable if the architecture
   # changes (e.g., non-RFC1918 reverse proxy).
   #
   # IMPORTANT: Do NOT set to "*" unless Gunicorn is behind a trusted
   # reverse proxy on a private network with no external access.
   ```

5. **Verify the Docker Compose networking.** Open `docker-compose.yml`. Find the backend network definition. Confirm it uses `internal: true`. Check the Nginx service's network membership. The Nginx container must be on both the frontend (external) and backend (internal) networks, and Gunicorn must be on backend only. If this is already the case, the RFC 1918 default will work. If the Docker network uses a custom subnet outside RFC 1918 (unlikely but possible), document that.

6. **Add `FORWARDED_ALLOW_IPS` to `.env.example`:**
   ```
   # Gunicorn trusted proxy IPs. Default covers all RFC 1918 private subnets.
   # Only change if your reverse proxy is on a non-standard network.
   # FORWARDED_ALLOW_IPS=172.16.0.0/12,192.168.0.0/16,10.0.0.0/8
   ```
   Comment it out (prefixed with `#`) since the default is correct for virtually all Docker setups. This documents the variable's existence without requiring action.

### Test Requirements

This is a configuration file change. There is no automated test for Gunicorn config. Verify manually:

- Read the modified `gunicorn.conf.py` and confirm the value is correct.
- Confirm `import os` is present.
- Run `python -c "import gunicorn.conf"` or similar syntax check if available.


### Verification

```
# Syntax check the config file
python -c "exec(open('gunicorn.conf.py').read())"

# Confirm the value is set
grep -A2 "forwarded_allow_ips" gunicorn.conf.py

```

### Commit

```
git add gunicorn.conf.py .env.example
git commit -m "fix(gunicorn): restrict forwarded_allow_ips to RFC 1918 subnets (M3)"
```

---

## P3-4: Pin Python Patch Version in Dockerfile and Clean Up CI

### Context

The Dockerfile uses `python:3.14-slim` without a patch version. This means `docker compose build` run at different times can pull different patch releases (3.14.1 vs 3.14.3), potentially introducing inconsistencies between dev and production, or between builds. Python 3.14 reached GA on 2025-10-07, so this is not about stability -- it is about reproducibility.

The CI workflow (`.github/workflows/ci.yml`) contains `allow-prereleases: true`, which was needed when Python 3.14 was in beta. It is now unnecessary and confusing.

### Implementation

1. **Determine the correct patch version to pin.** The implementation plan says `3.14.3`. Before blindly using that, verify it is actually the latest stable 3.14.x release. Check what version the current Docker image uses:

   ```
   grep "FROM python" Dockerfile
   ```

   The `python:3.14-slim` tag currently resolves to a specific patch version. If you can determine the actual version (e.g., from a previous Docker build log or by reading the image metadata), use that. If not, `3.14.3` is the most recent stable release as of the implementation plan date (2026-02-03). Use it.

2. **Update both `FROM` lines in the Dockerfile.** There are TWO: one for the builder stage and one for the runtime stage. They MUST use the same version. Read the Dockerfile and find both:

   ```dockerfile
   FROM python:3.14.3-slim AS builder
   ```

   and:

   ```dockerfile
   FROM python:3.14.3-slim
   ```

3. **Add a comment above the first `FROM` line** explaining why the patch version is pinned:

   ```dockerfile
   # Pin to a specific patch version for reproducible builds.
   # Update this version deliberately, not by accident via
   # floating tags. Last updated: YYYY-MM-DD.
   ```

   Replace `YYYY-MM-DD` with today's date.

4. **Clean up the CI workflow.** Open `.github/workflows/ci.yml`. Find the `allow-prereleases: true` line. Remove it. Also check whether the CI's `python-version` matches the Dockerfile's version. If the CI uses `3.14` (floating) and the Dockerfile now uses `3.14.3` (pinned), consider whether to pin the CI version too. The tradeoff: pinning the CI means manually updating both files, but floating the CI means it could test against a different patch version than production uses. For a solo developer, pinning both to the same version is the safer choice. Make a judgment call and document your reasoning in the commit message.

5. **Search for any other references to the Python version** in the repository:
   ```
   grep -rn "3\.14" Dockerfile .github/ docker-compose* README.md
   ```
   Update any that should match the new pinned version. Common locations: README build instructions, docker-compose build args, CI matrix.

### Test Requirements


Verify the Dockerfile syntax:

```
# Check for obvious syntax errors (if hadolint is available)
# hadolint Dockerfile

# At minimum, verify the file parses as valid Dockerfile:
grep "^FROM" Dockerfile    # Should show exactly 2 FROM lines, both pinned
```

Verify the CI workflow syntax:

```
# If actionlint is available:
# actionlint .github/workflows/ci.yml

# At minimum, verify allow-prereleases is gone:
grep -n "allow-prereleases" .github/workflows/ci.yml
# Should return nothing
```

### Commit

```
git add Dockerfile .github/workflows/ci.yml
git commit -m "chore(build): pin Python to 3.14.3 in Dockerfile and remove allow-prereleases from CI (M4)"
```

---

## P3-5: Add Stale Anchor Warning to Balance Calculator

### Context

This is the most complex item in Phase 3. It touches the balance calculator (a core financial service), the grid route, and a template. Read carefully.

The balance calculator's `_sum_all()` function excludes done/received transactions from post-anchor period calculations. This is intentional: the anchor balance is a real bank balance that already reflects settled transactions. Adding them again would double-count. The expected workflow is: mark items done, then update the anchor balance to reflect reality.

The problem is that if a user marks transactions as "done" in future periods WITHOUT updating the anchor, the done transactions become invisible to the projection. Example: anchor at period 5 = $5,000. Period 6 has rent $500 (done) + groceries $200 (projected). Correct balance: $4,300. Calculator shows: $4,800 (rent excluded because "done" but anchor not updated to reflect the payment).

The fix is NOT to change the calculation. That would break the anchor-based model. Instead, we add a warning flag that tells the UI "there are done/received transactions in periods after the anchor -- the user should update their anchor."

### Critical: Understand the Current Return Type

`calculate_balances()` currently returns an `OrderedDict` mapping `period_id` to `Decimal`. This return type is consumed by:

- `app/routes/grid.py` (main grid and balance row endpoints)
- Possibly other callers (you must search)

**You MUST NOT break this return type.** If you change the return to a tuple or a dict with extra keys, every caller will break. You have two options:

**Option A (preferred): Return a named tuple or dataclass containing both the balances dict and the warning flag.** This is cleanest but requires updating ALL callers. Example:

```python
from dataclasses import dataclass

@dataclass
class BalanceResult:
    balances: OrderedDict
    stale_anchor_warning: bool
```

**Option B: Add the warning as metadata on the returned OrderedDict.** Python allows setting attributes on dict subclasses, but this is fragile and unidiomatic.

**Option C: Return a tuple `(balances_dict, stale_anchor_warning)`.** This requires updating all callers but is simple.

Choose whichever option best fits the existing codebase patterns. Read how `calculate_balances_with_interest()` handles its return value (it returns a tuple of `(balances, interest_by_period)`). If the codebase already uses tuples for multi-value returns from this module, follow that pattern.

### Implementation

1. Read `app/services/balance_calculator.py` in full. Understand:
   - `calculate_balances()` -- the main function you are modifying
   - `calculate_balances_with_interest()` -- returns `(balances, interest_by_period)` as a tuple
   - `_sum_remaining()` -- anchor period helper
   - `_sum_all()` -- post-anchor period helper (the function that excludes done/received)
   - `SETTLED_STATUSES` -- the frozenset `{"done", "received"}`

2. Find ALL callers of `calculate_balances()`:

   ```
   grep -rn "calculate_balances(" app/ tests/
   ```

   List every call site. You must update each one.

3. **Add stale anchor detection to `calculate_balances()`.** After the main loop that computes balances, add a scan over post-anchor period transactions. The scan must:

   a. Only examine periods AFTER the anchor period (not the anchor itself -- done/received in the anchor are correctly excluded by `_sum_remaining()`).

   b. Check each transaction's status against `SETTLED_STATUSES`.

   c. If ANY post-anchor transaction has a status in `SETTLED_STATUSES`, set the warning flag to `True`.

   d. Do NOT count soft-deleted transactions (`is_deleted`). But note: the `transactions` parameter is pre-filtered to exclude `is_deleted=True` rows by the grid route before calling `calculate_balances()`. Read the grid route to confirm this. If it does pre-filter, you do not need to check `is_deleted` again inside the calculator. If it does NOT pre-filter, you must check it.

   e. Do NOT count credit-status transactions. They are excluded from balances for a different reason (credit workflow) and are not part of the stale anchor concern.

4. **Choose and implement the return type change.** Based on your analysis of the existing codebase patterns:

   If using a tuple (matching `calculate_balances_with_interest` pattern):

   ```python
   return balances, stale_anchor_warning
   ```

   If using a dataclass:

   ```python
   return BalanceResult(balances=balances, stale_anchor_warning=stale_anchor_warning)
   ```

5. **Update ALL callers.** For each call site:
   - If the caller currently does `balances = calculate_balances(...)`, update to unpack: `balances, stale_anchor_warning = calculate_balances(...)` (or `result = calculate_balances(...)` then `result.balances`).
   - If the caller does not need the warning, it still must unpack correctly. Use `balances, _ = calculate_balances(...)` for callers that ignore the warning.

6. **Pass the warning to the grid template.** In the grid route(s), add `stale_anchor_warning` to the template context. Find which template is rendered (likely `grid.html` or `_balance_row.html` or both). Pass the flag as a template variable.

7. **Add the UI warning to the template.** In the appropriate grid template, add a dismissible Bootstrap alert at the top of the grid (above the table, not inside it). The warning should:
   - Only appear when `stale_anchor_warning` is truthy.
   - Use `alert-warning` (yellow) styling.
   - Include an icon (use whatever icon set the project already uses -- likely Bootstrap Icons based on the `bi bi-` classes visible in `_balance_row.html`).
   - Include clear, actionable text: "Some transactions are marked as done in periods after your anchor. Your projected balances may be inaccurate. Update your anchor balance to reflect recent activity."
   - Be dismissible (Bootstrap's `alert-dismissible fade show` with a close button).
   - Use Jinja2 conditional: `{% if stale_anchor_warning %}...{% endif %}`.

8. **Update the docstring** of `calculate_balances()` to document the new return type and the `stale_anchor_warning` field.

### Test Requirements

Read `tests/test_services/test_balance_calculator.py` in full. Study the existing test patterns, especially the `FakePeriod` and `FakeTxn` helper classes.

**Update existing tests.** Every existing test that calls `calculate_balances()` must be updated to handle the new return type. If you chose a tuple return, every existing test that does `result = calculate_balances(...)` must change to `result, _ = calculate_balances(...)` or similar.

**Write new tests:**

1. **Stale anchor warning when done transactions exist in post-anchor periods:**
   - Create 3+ periods. Set anchor at period 0.
   - Add a "done" expense in period 2 (post-anchor).
   - Call `calculate_balances()`.
   - Assert the warning flag is `True`.

2. **No warning when all post-anchor transactions are projected:**
   - Create 3+ periods. Set anchor at period 0.
   - Add only "projected" transactions in all periods.
   - Call `calculate_balances()`.
   - Assert the warning flag is `False`.

3. **No warning when done transactions exist only in the anchor period:**
   - Create 3+ periods. Set anchor at period 0.
   - Add a "done" income in period 0 (the anchor itself).
   - Add only "projected" transactions in post-anchor periods.
   - Call `calculate_balances()`.
   - Assert the warning flag is `False`. Done in the anchor period is expected and does not indicate a stale anchor.

4. **Warning triggered by "received" status (not just "done"):**
   - Create 3+ periods. Set anchor at period 0.
   - Add a "received" income in period 1 (post-anchor).
   - Call `calculate_balances()`.
   - Assert the warning flag is `True`.

5. **No warning when post-anchor transactions are only credit/cancelled:**
   - Create 3+ periods. Set anchor at period 0.
   - Add "credit" and "cancelled" transactions in period 1.
   - Call `calculate_balances()`.
   - Assert the warning flag is `False`. Credit and cancelled are excluded from balances for different reasons and do not indicate a stale anchor.

6. **No warning when there are no transactions at all:**
   - Create 3+ periods. Set anchor at period 0.
   - Pass an empty transaction list.
   - Call `calculate_balances()`.
   - Assert the warning flag is `False`.

7. **Warning does not affect the calculated balances:**
   - Create a scenario with done transactions in post-anchor periods.
   - Call `calculate_balances()`.
   - Assert the BALANCES are identical to what they would be without the warning feature (i.e., done transactions are still excluded from the calculation). The warning is informational only -- it must not change the math.

### Verification

```
pytest tests/test_services/test_balance_calculator.py -v          # All balance calculator tests pass
pylint app/services/balance_calculator.py app/routes/grid.py      # No new warnings
```

### Commit

```
git add app/services/balance_calculator.py app/routes/grid.py app/templates/ tests/
git commit -m "feat(balance): add stale_anchor_warning when done/received exist in post-anchor periods (M1)"
```

---

## Post-Phase Checklist (Do This After All Five Items Are Complete)

```
# 1. Confirm all tests pass
pytest --tb=short -q
# Expected: post-Phase-2 count + new tests, all passing, zero failures

# 2. Confirm pylint score
pylint app/ --fail-on=E,F --output-format=text 2>&1 | tail -5
# Expected: 9.32/10 or higher, zero E/F messages

# 3. Confirm all commits are present and atomic
git log --oneline -8
# Should show your 5 Phase 3 commits

# 4. Confirm no untracked or unstaged files
git status
# Should be clean

# 5. Verify health endpoint sanitization
grep -n "detail" app/routes/health.py
# Should return ZERO lines containing "detail" in the response

# 6. Verify category in-use check is scoped
grep -n "user_id" app/routes/categories.py | head -20
# The in-use check should show user_id filtering

# 7. Verify gunicorn forwarded_allow_ips is restricted
grep "forwarded_allow_ips" gunicorn.conf.py
# Should NOT show "*"

# 8. Verify Dockerfile is pinned
grep "^FROM" Dockerfile
# Both lines should show python:3.14.3-slim (or whatever patch you chose)

# 9. Verify allow-prereleases is gone from CI
grep "allow-prereleases" .github/workflows/ci.yml
# Should return nothing

# 10. Verify balance calculator returns stale_anchor_warning
grep -n "stale_anchor_warning" app/services/balance_calculator.py
# Should show the flag being set and returned

# 11. Print a summary
echo "Phase 3 complete. Changes:"
echo "  P3-1: sanitized health check error response (M5)"
echo "  P3-2: scoped category delete in-use check by user with PayPeriod join (M6)"
echo "  P3-3: restricted forwarded_allow_ips to RFC 1918 subnets (M3)"
echo "  P3-4: pinned Python patch version in Dockerfile, removed allow-prereleases (M4)"
echo "  P3-5: added stale_anchor_warning to balance calculator (M1)"
echo ""
echo "Test count:"
pytest --co -q 2>&1 | tail -1
```

---

## What "Done Right" Means for This Phase

- The health endpoint returns exactly two keys (`"status"`, `"database"`) in error responses. No exception text, no stack traces, no connection strings. An unauthenticated attacker learns nothing beyond "the database is down."
- The category delete in-use check is fully scoped by user on BOTH queries (TransactionTemplate via `user_id`, Transaction via PayPeriod join). Soft-deleted transactions do not block deletion.
- The gunicorn config trusts forwarded headers only from RFC 1918 subnets, with an env var override for nonstandard architectures. The comment explains why and warns against `"*"`.
- The Dockerfile pins both `FROM` lines to the same patch version. The CI no longer uses `allow-prereleases`.
- The balance calculator returns a stale anchor warning when done/received transactions exist in post-anchor periods. The warning is informational only and does NOT change the calculated balances. The warning flag is propagated to the grid template and displayed as a dismissible alert.
- Every existing test that called `calculate_balances()` is updated for the new return type and still passes.
- All new Python code has docstrings and inline comments.
- All new Python code conforms to pylint standards.
- No existing test is broken.
- No pylint score decrease.
- Every commit is atomic and can be individually reverted.
- No assumptions about line numbers, function names, or file locations -- everything is verified by reading the actual code first.
