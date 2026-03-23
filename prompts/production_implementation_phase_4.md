# Claude Code Prompt: Shekel Phase 4 -- Code Quality and Architecture

You are implementing Phase 4 of the Shekel production readiness plan. Phases 1 through 3 are complete. This is a personal budgeting application where errors have real financial consequences. There is no QA team. You are the only safeguard between this code and production. Every change must be correct, tested, and verifiable. Do not guess. Do not assume. Do not take shortcuts.

Phase 4 is about code quality and architecture: eliminating a layered architecture violation (route-to-route import), adding defense-in-depth ownership checks to functions that are currently unreachable but could become IDOR vulnerabilities if wired to routes, and making database connection pool settings explicit. These changes do not fix user-visible bugs but they prevent future developers (including you six months from now) from introducing security vulnerabilities or experiencing mysterious failures.

---

## Ground Rules (Read These First -- They Are Non-Negotiable)

1. **Read before you write.** Before changing ANY file, read the ENTIRE file first. Do not rely on line number references from this prompt or any planning document. Line numbers shift between commits. Phases 1 through 3 changed files. Find the actual code by reading the file.

2. **Verify before you fix.** Before implementing each fix, confirm the problem still exists in the current code. If a fix has already been applied, skip it and document that you skipped it and why.

3. **One fix at a time.** Implement one item, write its tests, run the full test suite, confirm green, then commit. Do not batch fixes. Each commit must be atomic and individually revertable.

4. **Run pylint after every change.** The baseline is 9.32/10 or higher with zero real errors. Do not decrease this score. All new code must have docstrings and inline comments explaining non-obvious logic. Use: `pylint app/ --fail-on=E,F`

5. **Match the existing code style exactly.** Study the patterns already in the codebase before writing new code. Do not invent new patterns.

6. **Commit messages must be precise.** Format: `refactor(<scope>): <what changed> (<audit ID>)`. Note: Phase 4 uses `refactor` not `fix` since these are architectural improvements, not bug fixes. Exception: P4-2 and P4-3 use `fix` since they add security checks.

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

# 4. Confirm all four Phase 4 findings still exist (read each file)
```

For step 4, you must read and confirm each of the following:

- **P4-1:** Run `grep -rn "_load_tax_configs" app/`. Confirm the function is defined in `app/routes/salary.py`, imported by `app/routes/retirement.py` (a route-to-route import), and that a duplicate copy exists in `app/services/chart_data_service.py`. Read all three copies/imports to confirm they are identical in behavior. Also check if any OTHER files import or call this function: `grep -rn "_load_tax_configs\|load_tax_configs" app/ tests/`.

- **P4-2:** Open `app/services/recurrence_engine.py`. Find `resolve_conflicts()`. Read the entire function. Confirm it takes `transaction_ids`, `action`, and optionally `new_amount`, but does NOT take `user_id`. Confirm it loads transactions by ID via `db.session.get(Transaction, txn_id)` without checking ownership. Note the exact current function signature.

- **P4-3:** Open `app/services/transfer_recurrence.py`. Find `resolve_conflicts()`. Read the entire function. Confirm the same pattern as P4-2: loads transfers by ID without ownership checks. Note: the `Transfer` model has a direct `user_id` column (unlike `Transaction` which requires a join through `PayPeriod`). Confirm this by reading the Transfer model.

- **P4-4:** Open `app/config.py`. Find the `ProdConfig` class. Confirm it does NOT set `SQLALCHEMY_ENGINE_OPTIONS`. Also read `TestConfig` to see what `SQLALCHEMY_ENGINE_OPTIONS` it uses (it should use `NullPool` -- understand why: tests need deterministic connection lifecycle, not pooling). Understand the difference between test and production pool requirements before configuring production.

---

## P4-1: Extract `_load_tax_configs` to a Shared Service Module

### Context

The `_load_tax_configs` function is defined in `app/routes/salary.py` and imported from there by `app/routes/retirement.py`. Routes should never import from other routes. The layered architecture is: routes depend on services, services depend on models. Route-to-route imports create hidden coupling and circular dependency risks. Additionally, an identical copy of the function exists in `app/services/chart_data_service.py`, which means a bug fix to one copy would have to be manually applied to the other.

This is a pure refactor. The function's behavior must not change. No new features, no parameter changes, no logic changes. Move it, update all imports, delete the duplicates, verify everything still works.

### Implementation

1. **Catalog every occurrence.** Before writing any code, build a complete inventory:

   ```
   grep -rn "_load_tax_configs\|load_tax_configs" app/ tests/
   ```

   You need to find:
   - The original definition (expected in `app/routes/salary.py`)
   - The duplicate definition (expected in `app/services/chart_data_service.py`)
   - Every import statement that references it
   - Every call site in route and service files
   - Every reference in test files (tests may import the function or mock it)

   Write down every file and line. This is your checklist. Every entry must be updated or verified.

2. **Compare the two implementations.** Read the definition in `salary.py` and the duplicate in `chart_data_service.py` side by side. Confirm they are functionally identical. If they differ in ANY way (different query logic, different return format, different parameter handling), document the differences. The new shared version must satisfy all callers.

3. **Create the new service module.** Create `app/services/tax_config_service.py`. The file must:

   a. Have a module-level docstring explaining what it does and why it exists.

   b. Import the necessary models (the same ones the original function imports: `TaxBracketSet`, `StateTaxConfig`, `FicaConfig`, and any other models it queries).

   c. Import `db` from `app.extensions` (or from wherever the original function accesses the database session).

   d. Define the function as `load_tax_configs(user_id, profile)` -- note: DROP the leading underscore. The underscore convention means "private to this module." Since this is now a public service function, the underscore is incorrect. The function is part of the module's public API.

   e. Copy the function body exactly from the original. Do not refactor the internals. This is a move, not a rewrite.

   f. Add a docstring to the function that documents its parameters, return value, and what it queries. The original may or may not have a good docstring -- write a proper one regardless.

4. **Update all call sites.** For each call site found in step 1:

   a. Replace the import. Change `from app.routes.salary import _load_tax_configs` to `from app.services.tax_config_service import load_tax_configs`. Note the name change (no underscore).

   b. Replace the function call. Change `_load_tax_configs(...)` to `load_tax_configs(...)` at every call site.

   c. If a call site uses a deferred import inside a function body (common in Flask to avoid circular imports), keep the deferred pattern but change the import target. Do not move deferred imports to the module level unless you have verified there is no circular dependency risk.

5. **Delete the original definitions.** Remove `_load_tax_configs` from `salary.py`. Remove the duplicate from `chart_data_service.py`. Also remove any now-unused model imports that were only needed for the deleted function.

6. **Update test files.** Search for references in tests:

   ```
   grep -rn "_load_tax_configs\|load_tax_configs" tests/
   ```

   If any test imports or mocks `_load_tax_configs` from `salary.py` or `chart_data_service.py`, update the import path to `app.services.tax_config_service.load_tax_configs`. If tests mock the function, the mock target path must also change.

7. **Final verification -- no stale references remain:**
   ```
   grep -rn "_load_tax_configs" app/ tests/
   ```
   This should return ZERO results. The only references to `load_tax_configs` (without underscore) should be:
   - The definition in `app/services/tax_config_service.py`
   - Import statements at call sites
   - Any test mocks/imports

### Test Requirements

This is a pure refactor. No new tests should be needed IF the existing test suite covers the salary, retirement, and chart routes adequately. Run the relevant test files explicitly to confirm:

```
pytest tests/test_routes/test_salary.py -v
pytest tests/test_routes/test_retirement.py -v
pytest tests/test_services/test_chart_data_service.py -v
```

If any of these fail, the refactor introduced a regression. Fix it before proceeding.

Additionally, write ONE new test for the service module itself to verify the function works in isolation:

```python
# tests/test_services/test_tax_config_service.py

def test_load_tax_configs_returns_expected_keys(self, app, db, seed_user, ...):
    """load_tax_configs returns a dict with bracket_set, state_config, and fica_config keys."""
```

This test should:

1. Set up a salary profile with a filing status and state code (use existing fixtures or create minimal ones).
2. Call `load_tax_configs(user_id, profile)`.
3. Assert the return value is a dict with exactly three keys: `"bracket_set"`, `"state_config"`, `"fica_config"`.
4. Assert each value is either `None` (if no config exists) or the correct model type.

This test ensures the function works correctly after the move and provides regression protection for future changes.

### Verification

```
# Confirm no stale references
grep -rn "_load_tax_configs" app/ tests/
# Should return ZERO results

# Run affected test suites
pytest tests/test_routes/test_salary.py tests/test_routes/test_retirement.py tests/test_services/test_chart_data_service.py -v

# Pylint the new file and modified files
pylint app/services/tax_config_service.py app/routes/salary.py app/routes/retirement.py app/services/chart_data_service.py
```

### Commit

```
git add app/services/tax_config_service.py app/routes/salary.py app/routes/retirement.py app/services/chart_data_service.py tests/
git commit -m "refactor(tax): extract _load_tax_configs to app/services/tax_config_service.py (L1)"
```

---

## P4-2: Add Ownership Guard to `resolve_conflicts` in recurrence_engine

### Context

`resolve_conflicts()` in `app/services/recurrence_engine.py` loads `Transaction` objects by ID and modifies them (clears override flags, updates amounts, or soft-deletes). It does not verify that the requesting user owns the transactions. This function is NOT currently wired to any route endpoint -- it is called internally by the recurrence engine's regenerate flow with IDs from pre-filtered queries. But if it is ever connected to a route (e.g., an HTMX conflict-resolution endpoint), it would be an IDOR vulnerability.

Defense-in-depth means adding the ownership check now, before anyone forgets and wires it to a route without protection.

### Implementation

1. Read `app/services/recurrence_engine.py` in full. Find `resolve_conflicts()`. Read the ENTIRE function. Understand every code path: `keep`, `update`, and any other action values.

2. **Note the ownership chain.** The `Transaction` model does NOT have a direct `user_id` column. Ownership is determined through `transaction.pay_period.user_id`. This requires either:
   - A lazy-load: `txn.pay_period.user_id` (triggers a query per transaction -- acceptable for conflict resolution which handles small batches)
   - An eager join: load the transaction with `.options(joinedload(Transaction.pay_period))` or do a joined query

   For consistency with the rest of the codebase, check how other ownership checks on Transaction are done. Search:

   ```
   grep -rn "pay_period.user_id" app/
   ```

   Follow the established pattern.

3. **Add a `user_id` parameter.** Add it as the third positional parameter: `resolve_conflicts(transaction_ids, action, user_id, new_amount=None)`. This matches the pattern used in other service functions.

4. **Add the ownership check inside the loop.** After loading each transaction, before ANY modification:

   ```python
   if txn.pay_period.user_id != user_id:
       logger.warning(
           "resolve_conflicts blocked: transaction %d belongs to user %d, "
           "not requesting user %d",
           txn_id, txn.pay_period.user_id, user_id,
       )
       continue
   ```

   Use `continue`, not `raise`. If a list of transaction IDs contains one bad ID, we skip it and process the rest. This is consistent with how `txn is None` is already handled (skip, not abort).

5. **Update the docstring.** Document the new `user_id` parameter and the ownership check behavior.

6. **Find ALL callers of this function:**

   ```
   grep -rn "resolve_conflicts" app/ tests/
   ```

   This will return results from BOTH `recurrence_engine.py` and `transfer_recurrence.py` (which has its own `resolve_conflicts`). Focus only on callers of `recurrence_engine.resolve_conflicts`. Update each caller to pass `user_id`. If the function is called internally within the module (from `regenerate_for_template` or similar), the user_id is available from the template's `user_id` attribute or from the scenario's user.

   If callers are only internal (not from routes), they still need the parameter. Pass the user_id that is available in the calling context.

7. **Check if any route will ever call this.** Search for route files that import from `recurrence_engine`:
   ```
   grep -rn "from app.services.recurrence_engine import\|from app.services import.*recurrence_engine" app/routes/
   ```
   If any route imports `resolve_conflicts` directly, it must pass `current_user.id`.

### Test Requirements

Read `tests/test_services/test_recurrence_engine.py`. Find `TestResolveConflicts`. Read ALL existing tests in that class. Understand the fixture setup, especially how transactions are created.

**Update existing tests.** Every existing call to `resolve_conflicts` must now pass a `user_id`. Use the seed user's ID from the fixture. Existing tests should continue to pass with the correct user_id.

**Write new ownership isolation tests:**

1. **Cross-user update is blocked:**
   - Create a transaction owned by User A (via `seed_user`).
   - Generate it via the recurrence engine so it is properly set up.
   - Override it (`is_override = True`, modified amount).
   - Call `resolve_conflicts([txn.id], "update", user_id=user_b_id, new_amount=Decimal("50.00"))` using User B's ID.
   - Assert the transaction is NOT modified: `is_override` is still `True`, amount is unchanged.
   - Also assert no exception was raised (it should silently skip, not crash).

2. **Cross-user keep is blocked:**
   - Same setup as above.
   - Call `resolve_conflicts([txn.id], "keep", user_id=user_b_id)`.
   - Assert the transaction is unchanged.

3. **Same-user update succeeds:**
   - Same setup, but use User A's own ID.
   - Call `resolve_conflicts([txn.id], "update", user_id=user_a_id, new_amount=Decimal("50.00"))`.
   - Assert the transaction IS modified: `is_override` cleared, amount updated.

4. **Mixed list: valid and invalid IDs:**
   - Create two transactions: one owned by User A, one by User B (requires the `second_user` fixture).
   - Call `resolve_conflicts([txn_a.id, txn_b.id], "update", user_id=user_a_id, new_amount=...)`.
   - Assert User A's transaction IS modified.
   - Assert User B's transaction is NOT modified.
   - This tests that processing continues past blocked items.

Use the existing `seed_user` and `second_user`/`seed_second_user` fixtures. Do NOT create ad-hoc users in test methods.

### Verification

```
pytest tests/test_services/test_recurrence_engine.py -v -k resolve     # All resolve tests pass
pylint app/services/recurrence_engine.py                               # No new warnings
```

### Commit

```
git add app/services/recurrence_engine.py tests/test_services/test_recurrence_engine.py
git commit -m "fix(recurrence): add user_id ownership check to resolve_conflicts (H4)"
```

---

## P4-3: Add Ownership Guard to `resolve_conflicts` in transfer_recurrence

### Context

Same pattern as P4-2, but for transfers. `resolve_conflicts()` in `app/services/transfer_recurrence.py` loads `Transfer` objects by ID and modifies them without verifying ownership. Unlike `Transaction`, the `Transfer` model HAS a direct `user_id` column, so the ownership check is simpler.

### Implementation

1. Read `app/services/transfer_recurrence.py` in full. Find `resolve_conflicts()`. Read the entire function. Compare its structure to the one in `recurrence_engine.py` -- they should be very similar.

2. **Confirm the Transfer model has `user_id`.** Open the Transfer model and verify:

   ```
   grep -n "user_id" app/models/transfer.py
   ```

3. **Add a `user_id` parameter.** Same position as P4-2: `resolve_conflicts(transfer_ids, action, user_id, new_amount=None)`.

4. **Add the ownership check inside the loop.** Since Transfer has a direct `user_id`:

   ```python
   if xfer.user_id != user_id:
       logger.warning(
           "resolve_conflicts blocked: transfer %d belongs to user %d, "
           "not requesting user %d",
           xfer_id, xfer.user_id, user_id,
       )
       continue
   ```

5. **Update the docstring.**

6. **Find ALL callers:**

   ```
   grep -rn "resolve_conflicts" app/services/transfer_recurrence.py
   grep -rn "transfer_recurrence.*resolve_conflicts\|transfer_recurrence import" app/ tests/
   ```

   Update each caller to pass `user_id`.

7. **Ensure consistency with P4-2.** Both `resolve_conflicts` functions (transaction and transfer) should follow the same pattern: same parameter position for `user_id`, same logging format, same `continue` behavior on ownership failure. Read both after your changes and confirm they are symmetrical.

### Test Requirements

Read `tests/test_services/test_transfer_recurrence.py`. Find `TestResolveConflicts` (if it exists) or the relevant test class.

**Update existing tests** to pass `user_id`.

**Write new ownership isolation tests** following the same four-test pattern as P4-2:

1. Cross-user update blocked.
2. Cross-user keep blocked.
3. Same-user update succeeds.
4. Mixed list: only owned transfers modified.

The Transfer model has `user_id` directly, so creating cross-user test data is simpler than for Transaction (no PayPeriod join needed).

### Verification

```
pytest --tb=short -q                                                      # Full suite green
pytest tests/test_services/test_transfer_recurrence.py -v -k resolve      # All resolve tests pass
pylint app/services/transfer_recurrence.py                                # No new warnings
```

### Commit

```
git add app/services/transfer_recurrence.py tests/test_services/test_transfer_recurrence.py
git commit -m "fix(transfers): add user_id ownership check to resolve_conflicts (H4-transfer)"
```

---

## P4-4: Add `SQLALCHEMY_ENGINE_OPTIONS` to ProdConfig

### Context

`ProdConfig` does not set `SQLALCHEMY_ENGINE_OPTIONS`. SQLAlchemy uses defaults: `pool_size=5`, `max_overflow=10`, no `pool_recycle`, no `connect_timeout`. These defaults are adequate for the current 2-worker Gunicorn setup but have risks:

- No `pool_recycle` means connections are held indefinitely. If PostgreSQL restarts or a firewall drops idle connections, the pool holds dead connections that fail on next use.
- No `connect_timeout` means a connection attempt to an unreachable database hangs for the system TCP timeout (often 2+ minutes), causing worker threads to become unresponsive.
- `max_overflow=10` combined with `pool_size=5` allows up to 15 simultaneous connections per worker. With 2 workers, that is 30 connections, which is fine for PostgreSQL's default `max_connections=100` but should be documented.

`TestConfig` already sets `SQLALCHEMY_ENGINE_OPTIONS` using `NullPool` (no pooling -- each operation opens and closes its own connection). This is correct for tests but NOT for production, where connection pooling is essential for performance.

### Implementation

1. Read `app/config.py` in full. Study how `BaseConfig`, `DevConfig`, `TestConfig`, and `ProdConfig` are structured. Note:
   - Does `BaseConfig` set `SQLALCHEMY_ENGINE_OPTIONS`? If so, `ProdConfig` may need to override it, not just add it.
   - Does `DevConfig` set it? Understand the inheritance chain.
   - `TestConfig` sets it with `NullPool`. `ProdConfig` must NOT inherit `NullPool`.

2. **Add `SQLALCHEMY_ENGINE_OPTIONS` to `ProdConfig`.** The values must be justified:

   ```python
   SQLALCHEMY_ENGINE_OPTIONS = {
       # Pool size per Gunicorn worker. With 2 workers and pool_size=5,
       # the app uses up to 10 base connections + overflow.
       "pool_size": 5,
       # Allow 2 overflow connections per worker for burst traffic.
       # Total max per worker: pool_size + max_overflow = 7.
       # Total across 2 workers: 14 (well within PostgreSQL's default 100).
       "max_overflow": 2,
       # Seconds to wait for a connection from the pool before raising.
       # 30s is generous -- if the pool is exhausted for 30s, something
       # is very wrong and failing fast is better than queueing indefinitely.
       "pool_timeout": 30,
       # Recycle connections after 30 minutes (1800s) to avoid using
       # connections that PostgreSQL or a firewall may have closed.
       # This prevents "connection reset by peer" errors after idle periods.
       "pool_recycle": 1800,
       # Enable pre-ping: test each connection before using it.
       # Catches stale connections that pool_recycle missed (e.g., database
       # restart between recycle intervals). Small overhead (~1ms per checkout)
       # but eliminates "server closed the connection unexpectedly" errors.
       "pool_pre_ping": True,
       # TCP-level connect timeout. If the database host is unreachable,
       # fail in 5 seconds instead of waiting for the system TCP timeout.
       "connect_args": {"connect_timeout": 5},
   }
   ```

   IMPORTANT additions beyond the implementation plan:
   - **`pool_pre_ping: True`**: The plan does not include this but it is a best practice for production. It prevents the "stale connection" problem that `pool_recycle` only partially addresses (recycle is time-based; pre-ping is connection-based).
   - **Inline comments explaining every value**: A future developer reading this config must understand WHY each value was chosen, not just what it is.

3. **Verify no conflict with `BaseConfig`.** If `BaseConfig` sets `SQLALCHEMY_ENGINE_OPTIONS`, your `ProdConfig` definition completely overrides it (Python class attribute resolution). This is correct for production. But confirm that `DevConfig` still works -- if `DevConfig` does not set its own `SQLALCHEMY_ENGINE_OPTIONS`, it inherits from `BaseConfig`. Read `DevConfig` and confirm its pool behavior is acceptable.

4. **Add a comment on `TestConfig`'s `SQLALCHEMY_ENGINE_OPTIONS`** if one does not exist, explaining why tests use `NullPool`:
   ```python
   # NullPool: no connection pooling in tests. Each operation opens
   # and closes its own connection for deterministic cleanup and no
   # connection leaks between tests.
   ```

### Test Requirements

This is a configuration change. It only affects production (and possibly development) pool behavior. The test suite uses `TestConfig` with `NullPool`, so it is unaffected.

Write a simple config verification test if one does not already exist:

```python
def test_prod_config_has_pool_settings(self):
    """ProdConfig explicitly sets connection pool options."""
    from app.config import ProdConfig
    opts = ProdConfig.SQLALCHEMY_ENGINE_OPTIONS
    assert "pool_size" in opts
    assert "pool_recycle" in opts
    assert "pool_pre_ping" in opts
    assert opts["pool_pre_ping"] is True
    assert opts["connect_args"]["connect_timeout"] == 5
```

Check if there is an existing config test file (`tests/test_config.py` or similar). If so, add the test there. If not, create one.

### Verification

```
pylint app/config.py                    # No new warnings
```

### Commit

```
git add app/config.py tests/
git commit -m "refactor(config): add explicit SQLALCHEMY_ENGINE_OPTIONS to ProdConfig (L4)"
```

---

## Post-Phase Checklist (Do This After All Four Items Are Complete)

```
# 1. Confirm all tests pass
pytest --tb=short -q
# Expected: post-Phase-3 count + new tests, all passing, zero failures

# 2. Confirm pylint score
pylint app/ --fail-on=E,F --output-format=text 2>&1 | tail -5
# Expected: 9.32/10 or higher, zero E/F messages

# 3. Confirm all commits are present and atomic
git log --oneline -6
# Should show your 4 Phase 4 commits

# 4. Confirm no untracked or unstaged files
git status
# Should be clean

# 5. Verify the architecture violation is resolved
grep -rn "_load_tax_configs" app/ tests/
# Should return ZERO results (underscore-prefixed version is gone)

grep -rn "from app.routes.salary import" app/routes/retirement.py
# Should return ZERO results (route-to-route import is gone)

grep -rn "from app.services.tax_config_service import" app/
# Should show imports in salary.py, retirement.py, and chart_data_service.py

# 6. Verify both resolve_conflicts functions have user_id
grep -n "def resolve_conflicts" app/services/recurrence_engine.py app/services/transfer_recurrence.py
# Both should show user_id in the parameter list

# 7. Verify ProdConfig has pool settings
python -c "from app.config import ProdConfig; print(ProdConfig.SQLALCHEMY_ENGINE_OPTIONS)"
# Should print the pool config dict

# 8. Print a summary
echo "Phase 4 complete. Changes:"
echo "  P4-1: extracted _load_tax_configs to app/services/tax_config_service.py (L1)"
echo "  P4-2: added user_id ownership check to recurrence_engine.resolve_conflicts (H4)"
echo "  P4-3: added user_id ownership check to transfer_recurrence.resolve_conflicts (H4)"
echo "  P4-4: added explicit SQLALCHEMY_ENGINE_OPTIONS to ProdConfig (L4)"
echo ""
echo "Test count:"
pytest --co -q 2>&1 | tail -1
```

---

## What "Done Right" Means for This Phase

- The `_load_tax_configs` function exists in exactly ONE place: `app/services/tax_config_service.py`. No route file defines it. No service file has a duplicate. All callers import from the single canonical location. `grep -rn "_load_tax_configs" app/ tests/` returns zero results.
- The function is renamed from `_load_tax_configs` (private) to `load_tax_configs` (public) since it is now a public service API.
- Both `resolve_conflicts` functions (transaction and transfer) require a `user_id` parameter and silently skip any records not owned by that user. The ownership check pattern is consistent between the two modules: same parameter position, same logging, same skip behavior.
- The ownership check for Transaction uses the `pay_period.user_id` chain (because Transaction has no direct `user_id`). The ownership check for Transfer uses the direct `user_id` column.
- Each ownership guard has tests that verify cross-user access is blocked AND same-user access succeeds AND mixed-ownership lists are processed correctly (owned items modified, unowned items skipped).
- `ProdConfig.SQLALCHEMY_ENGINE_OPTIONS` is explicit, documented with inline comments explaining every value, and includes `pool_pre_ping: True` for stale connection detection.
- `TestConfig.SQLALCHEMY_ENGINE_OPTIONS` has a comment explaining why it uses `NullPool`.
- All new Python code has docstrings and inline comments.
- All new Python code conforms to pylint standards.
- No existing test is broken.
- No pylint score decrease.
- Every commit is atomic and can be individually reverted.
- No assumptions about line numbers, function names, or file locations -- everything is verified by reading the actual code first.
