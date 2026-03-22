# Pytest Freeze/Error Diagnostic -- Findings Report

**Date:** 2026-03-19
**Test suite:** 1258 tests (1257 passed, 1 xfailed)
**Suite runtime:** ~526 seconds (8 min 46 sec)

---

## Executive Summary

**The pytest "freezes" are not caused by database connection issues, lock contention,
or resource leaks.** The root cause is a **timeout mismatch**: the full test suite
takes ~526 seconds to complete, but the Bash tool default timeout is 120 seconds.
When pytest is killed at 120s, it appears to have "frozen" at ~15% completion.

The test infrastructure is well-designed:

- `NullPool` eliminates connection pooling entirely -- each operation opens/closes
  its own connection immediately
- `session.remove()` in fixture teardown properly cleans up
- Kill-and-restart leaves no zombie connections or held locks
- No individual test hangs (slowest: 3.02s for bcrypt-based MFA test)

---

## Phase 1: Environment Baseline

| Check                               | Result                   | Assessment |
| ----------------------------------- | ------------------------ | ---------- |
| Docker shekel-test-db               | Healthy, up ~1 hour      | OK         |
| Zombie pytest processes             | None                     | OK         |
| Active DB connections (idle)        | 0                        | OK         |
| PostgreSQL max_connections          | 100                      | OK         |
| idle_in_transaction_session_timeout | 0 (disabled)             | Low risk\* |
| tcp_keepalives_idle                 | 0 (disabled)             | Low risk\* |
| lock_timeout                        | 0 (disabled)             | Low risk\* |
| statement_timeout                   | 0 (disabled)             | Low risk\* |
| File descriptor limit               | 524,288                  | OK         |
| TEST_DATABASE_URL                   | Correct (localhost:5433) | OK         |
| SQLAlchemy pool class               | **NullPool**             | Excellent  |

\*Low risk because NullPool makes these largely irrelevant -- connections are
opened and closed per-operation, never held idle.

## Phase 2: Connection Monitoring

During the full 526-second test run, the connection monitor observed:

- **Maximum concurrent connections: 2**
- **No idle-in-transaction warnings**
- **No lock wait warnings**
- Most 2-second polling intervals showed 0 connections (too transient to catch)

This confirms NullPool is working as intended.

## Phase 3: Reproduction Attempts

### Scenario 1: Clean baseline run

- **Result:** PASSED (exit code 0, 526s)
- All 1257 tests passed

### Scenario 2: Rapid back-to-back runs (120s timeout)

- **Result:** Exit code 124 (timeout killed it at ~15%)
- **Root cause:** 120s timeout < 526s suite duration
- This is NOT a freeze -- the suite simply hadn't finished

### Scenario 3: Kill-and-restart

- **Killed run:** Exit code 124 (killed at 30s)
- **Remaining connections after kill:** 0 (NullPool cleaned up immediately)
- **Post-kill run:** PASSED (exit code 0)
- **Conclusion:** Kill-and-restart does not cause hangs

### Scenario 5: Multi-app tests (test_errors.py)

- **Result:** PASSED (exit code 0, 4.53s)
- No connection conflicts from extra Flask apps

### Slowest tests (durations analysis)

| Duration | Test                                    | Cause               |
| -------- | --------------------------------------- | ------------------- |
| 3.02s    | test_verify_wrong_code_returns_negative | bcrypt work factor  |
| 1.98s    | test_verify_returns_correct_index       | bcrypt work factor  |
| 1.82s    | test_hash_and_verify_round_trip         | bcrypt work factor  |
| 1.71s    | test_regenerate_backup_codes            | bcrypt work factor  |
| 1.55s    | test_mfa_confirm_valid_code             | bcrypt + TOTP       |
| ~0.9s    | Various setup fixtures                  | DB seeding overhead |

No test exceeds 3.1 seconds. The cumulative time is spread across 1258 tests
averaging ~0.42s each.

## Phase 4: Analysis

### What causes the perceived "freeze"

1. **Timeout mismatch.** The Bash tool's default timeout (120s / 2 min) is far
   shorter than the suite runtime (526s / 8.75 min). Running `pytest` without
   an explicit timeout > 600s will always be killed at ~15% completion.

2. **No progress output.** pytest buffers output by default (`-v` mode prints
   each test as it passes, but if not using `-v`, there's no output until
   completion). Without output, a long-running process appears frozen.

3. **Cumulative fixture overhead.** The 552 `with app.app_context()` wrappers
   in test files are redundant (the `db` fixture already pushes one). While
   they don't cause hangs, they add ~0.1-0.2s overhead per test, contributing
   to the total runtime.

### What does NOT cause the "freeze"

- Connection pool exhaustion -- NullPool has no pool to exhaust
- Stale/leaked connections -- NullPool closes immediately after use
- Lock contention from prior tests -- NullPool + session.remove() prevents this
- Killed processes leaving zombie connections -- verified: 0 connections after kill
- Multiple Flask app instances -- test_errors.py runs cleanly
- File descriptor exhaustion -- max 2 concurrent connections vs. 524K limit
- Docker container health -- both containers healthy

---

## Recommendations (Prioritized)

### P0: Fix the timeout (resolves the "freeze")

When running pytest, always use a timeout of at least 600 seconds:

```bash
timeout 600 pytest -v --tb=short
```

Or for the Bash tool, specify `timeout: 600000` (milliseconds).

For subset runs during development, run specific files:

```bash
pytest tests/test_routes/test_grid.py -v  # ~20s
pytest tests/test_services/ -v            # ~120s
```

### P1: Reduce suite runtime (optional, improves DX)

1. **Lower bcrypt work factor for tests.** The MFA tests spend 10+ seconds
   total on bcrypt hashing. Add to TestConfig:

   ```python
   BCRYPT_LOG_ROUNDS = 4  # minimum, vs. default 12
   ```

2. **Remove redundant `with app.app_context()` wrappers.** There are 552
   occurrences across test files. The `db` fixture (autouse) already provides
   an app context. Removing them eliminates nested context overhead.

3. **Add `pool_pre_ping: True` and `connect_timeout: 5` to TestConfig** as
   a safety net, even though NullPool makes leaks unlikely:
   ```python
   SQLALCHEMY_ENGINE_OPTIONS = {
       "poolclass": NullPool,
       "connect_args": {"connect_timeout": 5},
   }
   ```

### P2: Harden PostgreSQL timeouts (defense in depth)

Set these in the test-db container for safety:

```sql
ALTER SYSTEM SET idle_in_transaction_session_timeout = '30s';
ALTER SYSTEM SET lock_timeout = '10s';
ALTER SYSTEM SET statement_timeout = '30s';
SELECT pg_reload_conf();
```

These won't affect normal operation (NullPool connections are too brief) but
would prevent infinite waits if something unusual happens.

### P3: Add a rollback before TRUNCATE (belt-and-suspenders)

In the `db` fixture, add `_db.session.rollback()` before the TRUNCATE:

```python
@pytest.fixture(autouse=True)
def db(app, setup_database):
    with app.app_context():
        _db.session.rollback()  # Clear any stale transaction state
        _db.session.execute(_db.text("TRUNCATE TABLE ..."))
        ...
```

This is currently unnecessary with NullPool but protects against future
config changes.

---

## Conclusion

The test infrastructure is solid. The "freeze" is a timeout configuration
issue, not a database or application bug. Increasing the pytest timeout to
600+ seconds resolves the symptom entirely.
