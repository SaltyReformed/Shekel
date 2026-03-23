# Claude Code Prompt: Shekel Phase 5 -- Testing Gaps

You are implementing Phase 5 of the Shekel production readiness plan. Phases 1 through 4 are complete. This is a personal budgeting application where errors have real financial consequences. There is no QA team. You are the only safeguard between this code and production. Every change must be correct, tested, and verifiable. Do not guess. Do not assume. Do not take shortcuts.

Phase 5 is different from the earlier phases. Phases 1 through 4 made code changes and added tests alongside those changes. Phase 5 is a TEST COVERAGE AUDIT: it verifies that every fix from Phases 1 through 4 has adequate regression tests, fills any gaps, and hardens tests that are weak. You are writing tests, not changing application code. If you find yourself wanting to change application code, STOP -- that means a Phase 1-4 fix was incomplete. Document it and flag it as a regression, do not silently fix it.

**CRITICAL CONTEXT:** The Phase 1 through 4 prompts each required tests alongside their fixes. Many of the tests described in Phase 5 of the implementation plan may ALREADY EXIST. Additionally, the project has extensive IDOR test infrastructure from Phase 8E work in `tests/test_integration/test_access_control.py`. Your first job is to AUDIT what exists, not blindly create duplicates. Duplicate tests waste CI time and create maintenance burden.

---

## Ground Rules (Read These First -- They Are Non-Negotiable)

1. **Audit before you write.** For EACH item in this phase, your first step is to determine whether the required test already exists. Search for it. Read it. Evaluate its quality. Only write new tests if the existing coverage is absent or insufficient.

2. **Do not change application code.** Phase 5 is tests only. If a test reveals that a Phase 1-4 fix is missing or broken, document the finding and flag it. Do not silently fix it in this phase.

3. **Use existing fixtures.** The project has established two-user fixtures (`seed_user`, `seed_second_user`, `seed_second_periods`, `second_user`, `auth_client`, `second_auth_client`, `seed_full_user_data`). Study `tests/conftest.py` and `tests/test_integration/test_fixture_validation.py`. Do NOT create ad-hoc user setup in test methods.

4. **Match existing test patterns.** The project has extensive test conventions. Before writing ANY test, read at least 3 existing test files in the same directory to understand the naming conventions, class organization, fixture usage, assertion style, and docstring format. Study `tests/TEST_PLAN.md` if it exists.

5. **Run pylint on test files if the project lints tests.** Check whether `pylint tests/` is part of the CI or baseline. If so, maintain the score.

6. **One commit per test gap filled.** Each P5 item gets its own commit, even though they are all test files. This allows individual revert if a test is flawed.

7. **All work happens on the `dev` branch.**

---

## Pre-Flight: Comprehensive Test Coverage Audit

Before writing a single test, build a complete inventory. This is the most important step in Phase 5. Run ALL of the following and study the output:

```
# 1. Confirm branch and clean working tree
git branch --show-current
git status

# 2. Record the current commit hash
git log --oneline -1

# 3. Search for existing IDOR tests on transaction form-rendering endpoints (P5-1)
grep -rn "quick_create\|full_create\|empty_cell\|get_quick\|get_full\|get_empty" tests/
grep -rn "IDOR.*transaction\|transaction.*IDOR\|transaction.*other.*user\|other.*user.*transaction" tests/

# 4. Search for existing preview_recurrence IDOR tests (P5-2)
grep -rn "preview_recurrence\|preview.*other.*user\|preview.*second.*user\|preview.*period.*idor" tests/

# 5. Search for existing carry-forward scenario isolation tests (P5-3)
grep -rn "carry_forward.*scenario\|scenario.*carry_forward\|carry_forward.*isolation\|scenario_id.*carry" tests/

# 6. Search for existing is_active deactivation tests (P5-4)
grep -rn "is_active.*False\|deactivat\|is_active.*block\|inactive.*session\|active.*logout" tests/

# 7. Search for existing category delete scoping tests (P5-5)
grep -rn "delete.*category.*other\|category.*delete.*user\|category.*in_use.*user\|category.*blocked.*other" tests/

# 8. Search for existing health endpoint sanitization tests (P5-6)
grep -rn "unhealthy.*detail\|detail.*health\|health.*leak\|health.*sanitiz\|health.*credential\|health.*password" tests/

# 9. Catalog the comprehensive access control suite
wc -l tests/test_integration/test_access_control.py
grep -c "def test_" tests/test_integration/test_access_control.py

# 10. List all test files to understand the full test landscape
find tests/ -name "test_*.py" | sort
```

For EACH of the six P5 items below, document:
- **EXISTS?** Does a test covering this exact scenario already exist?
- **WHERE?** In which file and test class/function?
- **ADEQUATE?** Does the existing test cover all the assertions required below, or is it weak?
- **ACTION:** `skip` (test exists and is adequate), `harden` (test exists but is weak -- add assertions), or `create` (test does not exist).

---

## P5-1: IDOR Tests for Transaction Form-Rendering Endpoints

### What This Tests

Phase 2 (P2-1, P2-2, P2-3) added ownership checks to three GET endpoints in `app/routes/transactions.py`: `get_quick_create`, `get_full_create`, and `get_empty_cell`. These endpoints load Category and PayPeriod by ID from query parameters. Without the ownership check, User B could pass User A's category/period IDs and see User A's category names and period dates.

### Audit Steps

1. Search for existing tests that exercise these three endpoints with cross-user IDs:
   ```
   grep -rn "quick_create\|new/quick\|full_create\|new/full\|empty.cell\|empty-cell" tests/
   ```

2. Check `tests/test_routes/test_transaction_auth.py` for a `TestTransactionIDOR` class or similar.

3. Check `tests/test_integration/test_access_control.py` for transaction form-rendering IDOR tests. The Phase 8E access control suite may already cover these endpoints.

4. Read any matching tests. Evaluate whether they test:
   - User B's category ID with User A's period ID (cross-user category)
   - User A's category ID with User B's period ID (cross-user period)
   - User B's category AND period IDs (both foreign)
   - All THREE endpoints (quick, full, empty_cell), not just one

### Required Coverage

If tests are missing or incomplete, write tests that cover at minimum:

**For EACH of the three endpoints (`/transactions/new/quick`, `/transactions/new/full`, `/transactions/empty-cell`):**

1. Request with User B's category_id and User A's period_id -- assert 404.
2. Request with User A's category_id and User B's period_id -- assert 404.
3. Request with User B's category_id AND User B's period_id -- assert 404.
4. Request with User A's own category_id and period_id -- assert 200 (positive regression test).

That is 12 test cases across 3 endpoints. If some already exist, only fill the gaps.

Each test must:
- Use `auth_client` (logged in as User A) and resources from `seed_second_user` / `seed_second_periods` (User B's data).
- Determine the correct query parameters by reading the actual endpoint code. Do not guess parameter names. The endpoints may use `category_id`, `period_id`, `pay_period_id`, `txn_type_name`, or other parameters. Read the route code.
- Assert 404 for cross-user requests. Assert the response body does NOT contain User B's category name or period dates.
- Assert 200 for same-user requests.

### Commit

```
git commit -m "test(transactions): add/verify IDOR regression tests for form-rendering endpoints (P5-1)"
```

---

## P5-2: IDOR Test for Template Preview Recurrence

### What This Tests

Phase 2 (P2-4) added an ownership check to the `preview_recurrence` endpoint in `app/routes/templates.py`. This endpoint accepts a `start_period_id` from the user. Without the ownership check, User B's period ID would trigger pattern matching against User B's pay period schedule, leaking period structure.

The fix is a graceful fallback: if the period fails the ownership check, the endpoint falls through to the user's own periods. This means the response is 200 (not 404), but the content comes from User A's data, not User B's.

### Audit Steps

1. Search for existing tests:
   ```
   grep -rn "preview_recurrence.*other\|preview_recurrence.*second\|preview_recurrence.*idor\|preview.*start_period_id.*other" tests/
   ```

2. Check `tests/test_routes/test_templates.py` for IDOR tests in the `TestPreviewRecurrence` class.

3. Check `tests/test_integration/test_access_control.py` for preview_recurrence coverage.

### Required Coverage

If tests are missing or incomplete:

1. **Cross-user period is ignored (graceful fallback):**
   - Create User A with periods starting on known dates (e.g., 2026-01-02).
   - Create User B with periods starting on DIFFERENT known dates (e.g., 2026-02-01).
   - Log in as User A. Request `preview_recurrence` with `start_period_id` set to User B's first period.
   - Assert response is 200.
   - Assert response content contains dates derived from User A's period schedule.
   - Assert response content does NOT contain dates derived from User B's period schedule.
   - This requires knowing both users' period start dates and checking the response HTML for them.

2. **Own period works normally (positive regression):**
   - Log in as User A. Request `preview_recurrence` with `start_period_id` set to User A's own period.
   - Assert 200 and valid recurrence preview content.

3. **Nonexistent period_id falls back gracefully:**
   - Log in as User A. Request with `start_period_id=999999`.
   - Assert 200 (fallback to own periods, not 500 error).

### Commit

```
git commit -m "test(templates): add/verify IDOR regression test for preview_recurrence (P5-2)"
```

---

## P5-3: Carry-Forward Scenario Isolation Test

### What This Tests

Phase 1 (P1-2) added `scenario_id` filtering to the carry-forward service. Without it, carry-forward would move projected transactions from ALL scenarios, corrupting non-baseline data.

### Audit Steps

1. Search for existing tests:
   ```
   grep -rn "scenario.*carry_forward\|carry_forward.*scenario\|carry_forward.*isolation\|only_moves.*scenario\|scenario.*corrupt\|scenario_id.*carry" tests/
   ```

2. Check `tests/test_services/test_carry_forward_service.py` for scenario isolation tests.

3. The Phase 1 prompt required this test. It should already exist. Verify.

### Required Coverage

If the test exists, read it and verify it covers ALL of the following:

1. A single user has TWO scenarios: baseline and alternative.
2. Projected transactions exist in BOTH scenarios for the SAME source pay period.
3. Carry-forward is called with `scenario_id` set to the baseline scenario's ID.
4. ONLY baseline scenario transactions are moved to the target period.
5. Alternative scenario transactions remain UNTOUCHED in the source period. Assert their `pay_period_id` has not changed.
6. The return value (count) matches only the baseline transactions moved.

If the test exists but is weak (e.g., only checks that the function does not crash, but does not verify the alternative scenario's transactions are untouched), HARDEN it by adding the missing assertions.

If the test does not exist, create it using the existing carry-forward test fixtures. Study the existing test file to understand how carry-forward tests set up periods, transactions, and statuses.

### Commit

```
git commit -m "test(carry-forward): add/verify scenario isolation regression test (P5-3)"
```

---

## P5-4: User Deactivation Session Invalidation Test

### What This Tests

Phase 1 (P1-1) added an `is_active` check to the `user_loader` callback. Without it, a deactivated user (`is_active = False`) retains access through existing sessions for up to 30 days.

### Audit Steps

1. Search for existing tests:
   ```
   grep -rn "is_active\|deactivat\|inactive.*session\|active.*False.*login\|active.*block\|active.*logout" tests/
   ```

2. Check `tests/test_routes/test_auth.py` for a deactivation test.

3. The Phase 1 prompt required this test. It should already exist.

### Required Coverage

The test must verify the COMPLETE deactivation flow, not just one step:

1. **Login succeeds initially:** User logs in and accesses a protected page (200).
2. **Deactivation takes effect immediately:** Set `user.is_active = False` on the model and commit. The user does NOT log out -- their session cookie is still valid.
3. **Existing session is invalidated:** Request the same protected page again with the same session. Assert redirect to `/login` (302 with Location containing `/login`).
4. **Re-login is blocked:** Attempt to log in with the same valid credentials. Assert login fails. The `authenticate()` function in `auth_service.py` checks `is_active` on new logins.
5. **Error message is appropriate:** Assert the login failure message does NOT reveal that the account is deactivated (to prevent user enumeration). The message should be the same generic message used for wrong credentials (e.g., "Invalid email or password").

If the existing test only covers steps 1-3 (deactivation blocks session) but not steps 4-5 (re-login blocked with correct message), HARDEN it.

Also verify there is a POSITIVE regression test: re-activating the user (`is_active = True`) should allow login again. If this test does not exist, add it.

### Commit

```
git commit -m "test(auth): add/verify user deactivation session invalidation regression test (P5-4)"
```

---

## P5-5: Category Delete User-Scoping Test

### What This Tests

Phase 3 (P3-2) scoped the category delete in-use check by `user_id`. Without the fix, User B's templates/transactions could block User A from deleting User A's own category.

### Audit Steps

1. Search for existing tests:
   ```
   grep -rn "delete.*category.*other\|category.*delete.*blocked\|category.*in_use.*user\|category.*other.*user.*template\|not_blocked_by_other" tests/
   ```

2. Check `tests/test_routes/test_categories.py` for user-scoped delete tests.

3. The Phase 3 prompt required this test.

### Required Coverage

The complete coverage requires FOUR scenarios. Verify each exists:

1. **Cross-user template does not block deletion:** User A can delete their category even though User B has a template referencing User B's own (different) category. Assert deletion succeeds.

2. **Own template DOES block deletion:** User A cannot delete a category that User A's own template references. Assert deletion is blocked with appropriate error.

3. **Soft-deleted transactions do not block deletion:** User A can delete a category that only has soft-deleted (`is_deleted = True`) transactions referencing it. Assert deletion succeeds.

4. **Active transactions DO block deletion:** User A cannot delete a category that has active (non-deleted) transactions referencing it. Assert deletion is blocked.

If only scenario 1 exists (the basic cross-user test from the plan), add the other three. The soft-deleted transaction test (scenario 3) is particularly important because Phase 3 added `is_deleted.is_(False)` filtering to the in-use query. Without this test, a regression in that filter would go undetected.

### Commit

```
git commit -m "test(categories): add/verify user-scoped delete in-use regression tests (P5-5)"
```

---

## P5-6: Health Endpoint Error Sanitization Test

### What This Tests

Phase 3 (P3-1) removed the `"detail": str(exc)` field from the health endpoint's error response. Without the fix, an unauthenticated caller could trigger a database error and receive connection details (host, port, username) in the response.

### Audit Steps

1. Search for existing tests:
   ```
   grep -rn "detail.*health\|health.*detail\|health.*leak\|health.*sanitiz\|health.*credential\|health.*password\|unhealthy.*expose\|unhealthy.*detail" tests/
   ```

2. Check `tests/test_routes/test_health.py`. The Phase 3 prompt required both updating the existing `test_health_returns_500_on_db_failure` test AND adding a credential-leak test.

3. Read the existing health tests. The original test asserted `"connection refused" in data["detail"]` -- this should have been updated in Phase 3 to assert the detail key is ABSENT.

### Required Coverage

1. **Basic error response shape:** Mock `db.session.execute` to raise an exception. Assert:
   - Response status is 500.
   - Response JSON contains `"status": "unhealthy"`.
   - Response JSON contains `"database": "error"`.
   - Response JSON does NOT contain a `"detail"` key.
   - Response JSON contains EXACTLY two keys: `"status"` and `"database"`.

2. **Credential leak prevention:** Mock `db.session.execute` to raise an exception with a message containing realistic sensitive data: `"could not connect to server: password authentication failed for user \"shekel_user\" at host \"192.168.1.50\" port 5432"`. Assert:
   - Response status is 500.
   - The response body (as a string) does NOT contain `"shekel_user"`.
   - The response body does NOT contain `"192.168.1.50"`.
   - The response body does NOT contain `"5432"`.
   - The response body does NOT contain `"password"`.
   - Each assertion is separate (not combined with `and`) so a failure pinpoints exactly which sensitive string leaked.

3. **Logging preserved:** If possible, assert that the exception detail IS logged (using `caplog` or similar). This verifies that removing the detail from the response did not also remove it from logs. The detail must be available for operator debugging.

If the existing tests already cover all of this, mark P5-6 as `skip` and document why.

### Commit

```
git commit -m "test(health): add/verify error response sanitization regression tests (P5-6)"
```

---

## Post-Audit: Gap Summary Report

After auditing all six items, produce a summary before writing any code:

```
echo "=== Phase 5 Test Coverage Audit ==="
echo ""
echo "P5-1 (Transaction IDOR):    [EXISTS/PARTIAL/MISSING] -- [ACTION: skip/harden/create]"
echo "P5-2 (Preview Recurrence):  [EXISTS/PARTIAL/MISSING] -- [ACTION: skip/harden/create]"
echo "P5-3 (Carry-Forward):       [EXISTS/PARTIAL/MISSING] -- [ACTION: skip/harden/create]"
echo "P5-4 (User Deactivation):   [EXISTS/PARTIAL/MISSING] -- [ACTION: skip/harden/create]"
echo "P5-5 (Category Delete):     [EXISTS/PARTIAL/MISSING] -- [ACTION: skip/harden/create]"
echo "P5-6 (Health Sanitization): [EXISTS/PARTIAL/MISSING] -- [ACTION: skip/harden/create]"
echo ""
echo "Items requiring action: [count]"
```

Only after completing this audit should you begin writing or modifying test code. If ALL six items are already adequately covered, Phase 5 is a documentation exercise: commit the audit summary to `docs/phase_5_test_audit.md` and move on.

---

## Post-Phase Checklist

```
# 1. Confirm all tests pass
pytest --tb=short -q
# Expected: same or higher count than pre-Phase-5 baseline, all passing

# 2. No application code was changed
git diff --name-only HEAD~$(git log --oneline | head -n $(git log --oneline --since="Phase 5 start" | wc -l) | wc -l) | grep -v "^tests/" | grep -v "^docs/"
# Should return nothing -- only test/ and docs/ files should be changed

# 3. Confirm no duplicate tests were created
# Check that no test name appears in multiple files:
grep -rh "def test_" tests/ | sort | uniq -d
# Should be empty or contain only legitimately shared test names

# 4. Confirm every Phase 1-4 fix has at least one regression test
echo "Phase 1 fixes:"
echo "  B1 (is_active):      $(grep -rl 'is_active.*False\|deactivat' tests/ | head -1)"
echo "  B3 (scenario_id):    $(grep -rl 'scenario.*carry_forward\|carry_forward.*scenario' tests/ | head -1)"
echo "  B4 (seed password):  $(grep -rl 'seed.*password\|ChangeMe\|FLASK_ENV.*production.*seed' tests/ | head -1)"
echo "  M2 (pg_isready):     (shell script -- manual verification only)"
echo ""
echo "Phase 2 fixes:"
echo "  H1 (txn IDOR):       $(grep -rl 'quick_create.*other\|full_create.*other\|empty_cell.*other\|IDOR.*transaction' tests/ | head -1)"
echo "  H3 (preview IDOR):   $(grep -rl 'preview_recurrence.*other\|preview.*idor\|preview.*second' tests/ | head -1)"
echo "  H5 (rate limit):     $(grep -rl 'rate.*limit.*register\|register.*rate\|register.*429' tests/ | head -1)"
echo "  H6 (reg toggle):     $(grep -rl 'REGISTRATION_ENABLED\|registration.*disabled\|registration.*toggle' tests/ | head -1)"
echo ""
echo "Phase 3 fixes:"
echo "  M5 (health detail):  $(grep -rl 'detail.*health\|health.*detail\|health.*leak\|health.*sanitiz' tests/ | head -1)"
echo "  M6 (category scope): $(grep -rl 'category.*other.*user\|category.*delete.*scop\|not_blocked_by_other' tests/ | head -1)"
echo "  M3 (forwarded_ips):  (config change -- no app test needed)"
echo "  M4 (Python pin):     (Dockerfile change -- no app test needed)"
echo "  M1 (stale anchor):   $(grep -rl 'stale_anchor\|anchor.*warning' tests/ | head -1)"
echo ""
echo "Phase 4 fixes:"
echo "  L1 (tax config):     $(grep -rl 'tax_config_service\|load_tax_configs' tests/ | head -1)"
echo "  H4 (resolve user):   $(grep -rl 'resolve_conflicts.*user\|resolve.*ownership\|resolve.*cross' tests/ | head -1)"
echo "  L4 (pool options):   $(grep -rl 'SQLALCHEMY_ENGINE_OPTIONS\|pool_size\|pool_pre_ping' tests/ | head -1)"
echo ""
echo "Test count:"
pytest --co -q 2>&1 | tail -1

# 5. Print summary
echo ""
echo "Phase 5 complete."
```

---

## What "Done Right" Means for This Phase

- Every fix from Phases 1 through 4 has at least one regression test that would FAIL if the fix were reverted and PASSES with the fix in place.
- No duplicate tests were created. If a test already exists and is adequate, it was left alone.
- Weak tests were hardened with additional assertions, not replaced. Existing passing tests are never deleted.
- Tests that verify the ABSENCE of information (IDOR tests, health sanitization) check for specific forbidden strings, not just status codes. A 404 status with leaked data in the body is still a vulnerability.
- Cross-user tests use the project's established two-user fixtures, not ad-hoc user creation.
- Every test has a descriptive docstring explaining what it verifies and why it matters.
- No application code was changed. Phase 5 is tests only.
- The audit summary documents exactly what was found, what was skipped, what was hardened, and what was created.
