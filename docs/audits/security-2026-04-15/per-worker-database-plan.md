# Plan: Per-pytest-worker PostgreSQL database isolation via template cloning + pytest-xdist parallelism

## Context

The Shekel test suite currently shares a single PostgreSQL test database (`shekel_test`) across all pytest invocations. `tests/conftest.py::db` runs `TRUNCATE TABLE ... CASCADE` between every test, which requires `AccessExclusiveLock` on every named table. Two concurrent pytest processes deadlock by design on that lock. Beyond the deadlock, the architecture forbids `pytest-xdist` parallelism for the same reason -- the suite has grown to ~5,131 tests / ~28 min sequential wall-clock, and the user wants the option to run faster batches concurrently or in parallel without losing test isolation.

This plan adopts Pattern B (per-pytest-session database cloned from a PostgreSQL template) plus `pytest-xdist` for parallelism within one invocation. Each pytest invocation -- and each xdist worker within an invocation -- gets a uniquely-named database cloned at session start, runs the existing TRUNCATE-based per-test cleanup inside its own DB, and drops the cloned DB on session teardown. Deadlocks between sessions become structurally impossible. The per-test cleanup pattern (which is correct for this audit-trail-heavy app: SAVEPOINT rollback would break `system.audit_log` assertions) is preserved unchanged.

The user is a solo developer who explicitly wants best practice over speed: every phase below is independently committable with a clear verification gate, so the migration can be paused or rolled back at any phase boundary.

## High-level approach

1. Build a `shekel_test_template` PostgreSQL database once per developer setup (and after any migration). It contains all schemas, all tables (built via `flask db upgrade head` to validate the migration chain), audit infrastructure (triggers + `system.audit_log`), and reference seed data. It is shared across all pytest sessions.
2. On every pytest session start (including each xdist worker), `tests/conftest.py` clones the template into a uniquely-named per-session database (`shekel_test_<worker_id>_<pid>`), overrides `TEST_DATABASE_URL` before the `app` module is imported, then proceeds with the existing fixture stack.
3. On session teardown, the per-session DB is dropped. Crashed-session orphans are cleaned up on the next pytest startup.
4. `pytest-xdist` is added so a single invocation can fan out workers; each worker gets its own DB.
5. Documentation (CLAUDE.md, testing-standards.md) and CI (`.github/workflows/ci.yml`) are updated to match the new flow.

The user has chosen `flask db upgrade head` for the template build (validates the migration chain on every rebuild) and authorised the CI workflow update.

## Architectural decisions and constraints

- **Per-test cleanup stays TRUNCATE-based.** Validated against the audit-trigger constraint: SAVEPOINT-rollback isolation would roll back audit-log rows along with the test data, breaking `test_integration/test_audit_triggers.py` and `test_scripts/test_audit_cleanup.py`. The current per-test cycle (TRUNCATE 28 tables CASCADE → re-seed ref → TRUNCATE `system.audit_log` → refresh ref_cache + Jinja globals) is preserved verbatim inside each per-session DB.
- **Module-level conftest bootstrap.** The per-session DB must exist and `TEST_DATABASE_URL` must point at it BEFORE `app/config.py::TestConfig.SQLALCHEMY_DATABASE_URI` is evaluated (which happens at class-body time during the first `import app`). The conftest already sets `SECRET_KEY` at module load before the app import; the new bootstrap follows the same pattern.
- **Master-vs-worker detection.** When `pytest-xdist` is active, the master process also imports conftest (for test collection) but never runs tests. If the master ran the bootstrap, it would create a DB that nothing uses and is never dropped. The bootstrap skips when both `PYTEST_XDIST_TESTRUNUID` is set AND `PYTEST_XDIST_WORKER` is unset -- the xdist master signature. Single-process pytest (no `-n` flag) has `PYTEST_XDIST_TESTRUNUID` unset and runs the bootstrap. xdist workers have both set and run the bootstrap. Verified against pytest-xdist 3.x behaviour.
- **Cluster-level state.** `tests/test_models/test_audit_migration.py::shekel_app_role` fixture executes `CREATE ROLE shekel_app` / `DROP ROLE shekel_app`. Roles are cluster-scoped, not database-scoped, so two xdist workers would race on this. `@pytest.mark.xdist_group("shekel_app_role")` on the relevant test classes forces those tests onto one worker.
- **Three seed call sites.** The reference-data seed logic exists in three places today: `tests/conftest.py::_seed_ref_tables` (lines 1152-1242), `app/__init__.py::_seed_ref_tables` (line 861, called eagerly inside `create_app()` for dev/test configs), and `scripts/seed_ref_tables.py`. All three must converge on a single function so the template, runtime app, and production seed produce identical state. This is in scope -- leaving the duplication is a known-drift hazard.

## Phase-by-phase plan

Each phase ends with a single verification command. Each phase is independently committable. If verification fails at any phase, revert that phase's commit and diagnose before continuing.

### Phase 0 -- Add pytest-xdist dependency (no behaviour change)

**Files:**
- `requirements-dev.txt` -- add `pytest-xdist==3.7.0` (latest stable)

**Verification:**
```bash
pip install -r requirements-dev.txt
pytest tests/test_config.py -v
```
Pass count must be identical to baseline.

**Why first:** dependency exists before any conftest code reads `PYTEST_XDIST_WORKER`. Reversible (single-line revert).

### Phase 1 -- Consolidate the reference-data seed into one function

**Files:**
- `app/ref_seeds.py` -- add `seed_reference_data(session)` function. Pure, idempotent. Body extracted from `tests/conftest.py::_seed_ref_tables` (the canonical version -- mirrors production seed semantics). Takes a SQLAlchemy session, returns None. Uses existing `ACCT_TYPE_SEEDS` constant.
- `tests/conftest.py` -- `_seed_ref_tables` becomes a 2-line wrapper: `from app.ref_seeds import seed_reference_data; seed_reference_data(_db.session)`.
- `app/__init__.py` -- `_seed_ref_tables` (line 861) rerouted to call `seed_reference_data`. Keep the outer `try/except sqlalchemy.exc.ProgrammingError` guard since it handles the "tables don't exist yet" branch on first-boot.
- `scripts/seed_ref_tables.py` -- rerouted to call `seed_reference_data`.

**Verification:**
```bash
pytest tests/test_models/ tests/test_ref_cache.py tests/test_audit_fixes.py -v
pylint app/ scripts/ --fail-on=E,F
```
Pass count must be identical to baseline. Pylint score must not drop.

**Why second:** the deepest "no test-behaviour change" refactor. If anything breaks here, the cause is the seed extraction, not the DB plumbing. Lands on its own commit.

### Phase 2 -- Build the template database script

**Files (new):**
- `scripts/build_test_template.py` -- idempotent template builder. Pseudocode:
  1. Read env vars: `TEST_TEMPLATE_DATABASE` (default `shekel_test_template`), `TEST_ADMIN_DATABASE_URL` (default `postgresql:///postgres`).
  2. Open psycopg2 connection to admin DB with autocommit (required: `CREATE DATABASE` cannot run inside a transaction).
  3. `DROP DATABASE IF EXISTS shekel_test_template WITH (FORCE)` then `CREATE DATABASE shekel_test_template`.
  4. Close admin connection.
  5. Set `TEST_DATABASE_URL` and `SECRET_KEY` env vars; import `create_app`; build app pointed at the new template DB.
  6. In an app context: create the 5 schemas (`ref`, `auth`, `budget`, `salary`, `system`); run `flask db upgrade head` (via `flask_migrate.upgrade()` programmatic call -- ensures the migration chain is validated against an empty DB on every template rebuild); call `app.audit_infrastructure.apply_audit_infrastructure()`; call `app.ref_seeds.seed_reference_data(db.session)`; commit.
  7. Verify expected state: `assert ref.account_types row count == 18`; `assert system.audit_log row count == 0`; `assert pg_trigger count of audit_* triggers == EXPECTED_TRIGGER_COUNT (45)`.
  8. Print one-line summary on success.

**Pre-flight drift check (phase 2a):** before running the template build for real, run `flask db upgrade head` against a temporary empty DB to detect any model-vs-migration drift in the current codebase. If the migration produces a schema that differs from `db.create_all()`'s output (which today's conftest uses), the test suite has been masking the drift. Surface this as a known issue BEFORE switching the template to migrations. (If drift is found, stop and surface to user; the drift fix is out of scope for this work.)

**Verification:**
```bash
# Pre-flight drift check (one-off, manual)
createdb shekel_drift_check
flask db upgrade head  # against a clean shekel_drift_check
# Compare schema with `db.create_all()` output

# Template build itself
python scripts/build_test_template.py
psql shekel_test_template -c "SELECT count(*) FROM ref.account_types;"  # must return 18
psql shekel_test_template -c "SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'audit_%' AND NOT tgisinternal;"  # must return 45
psql shekel_test_template -c "SELECT count(*) FROM system.audit_log;"  # must return 0

# Idempotency
python scripts/build_test_template.py  # re-run, must succeed
psql shekel_test_template -c "SELECT count(*) FROM ref.account_types;"  # still 18
```

**Why third:** new script, no existing test paths touched. Run it manually before touching conftest. Lands on its own commit.

### Phase 3 -- Wire conftest to per-worker DB cloning

**Files:**
- `tests/conftest.py` -- module-level bootstrap and `pytest_sessionfinish` hook. Reorganised as:
  1. **Top of file (before any `app` import):** new `_bootstrap_worker_database()` function that:
     - Detects xdist master: `if os.environ.get("PYTEST_XDIST_TESTRUNUID") and not os.environ.get("PYTEST_XDIST_WORKER"): return None`. Master skips bootstrap entirely.
     - Computes `worker_id = os.environ.get("PYTEST_XDIST_WORKER", "main")`, `db_name = f"shekel_test_{worker_id}_{os.getpid()}"`.
     - Reads admin URL from env (`TEST_ADMIN_DATABASE_URL`, default `postgresql:///postgres`).
     - Opens psycopg2 admin connection with autocommit.
     - Cleans up orphans: `SELECT datname FROM pg_database WHERE datname LIKE 'shekel_test_{worker_id}_%'` minus any DBs with active connections in `pg_stat_activity`. Drops the rest. (Avoids the PID-reuse trap by checking `pg_stat_activity` not just name patterns.)
     - Verifies the template exists: `SELECT 1 FROM pg_database WHERE datname = 'shekel_test_template'`. If missing, raise with `RuntimeError("Test template database 'shekel_test_template' not found. Run: python scripts/build_test_template.py")`.
     - Issues `CREATE DATABASE "{db_name}" TEMPLATE "shekel_test_template"`.
     - Closes admin connection.
     - Verifies the new DB is correct: open a brief psycopg2 connection to `{db_name}`, count `ref.account_types` rows. If != 18, raise with "template appears corrupted, rebuild with `python scripts/build_test_template.py`."
     - Returns `(db_name, admin_url)`.
  2. **Just after the bootstrap call:** if a bootstrap happened, `os.environ["TEST_DATABASE_URL"]` is set to the per-session URL. Existing `os.environ.setdefault("SECRET_KEY", ...)` line stays where it is.
  3. **Existing `from app import create_app` and `from app.extensions import db as _db` imports stay where they are** -- they now read the per-session URL.
  4. **Simplify `setup_database` fixture** (currently lines 118-166): remove `_db.create_all()`, `_create_audit_infrastructure()`, `_seed_ref_tables()`, and the teardown block (`_db.drop_all()` + `DROP SCHEMA CASCADE`). Keep ONLY the `_refresh_ref_cache_and_jinja_globals(app)` call and the `yield`. The cloned template already has schemas + tables + audit triggers + ref data; the only Python-side initialisation needed is the in-process ref_cache.
  5. **Add `pytest_sessionfinish(session, exitstatus)` at module level** that:
     - Returns early if bootstrap was skipped (master).
     - Calls `_db.session.remove()` and `_db.engine.dispose()` to release all SQLAlchemy connections to the per-session DB.
     - Opens psycopg2 admin connection.
     - Issues `DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)` (the WITH FORCE clause handles any lingering backend that escaped the engine.dispose; requires PG 13+, which the project's PG 16 covers).
     - Closes admin connection.
  6. **Per-test `db` fixture (lines 168-266) stays exactly as-is.** TRUNCATE + reseed + audit_log truncate + ref_cache refresh -- the existing per-test cleanup remains the contract every test depends on.
  7. **Add `_db.engine.dispose()` to the `db` fixture teardown** (after `_db.session.remove()`) to defensively release any leaked NullPool connection that a previous test might have held -- belt-and-braces protection so per-test isolation cannot be subtly compromised by connection-pool state.

**Verification:**
```bash
# Single-process sanity check
pytest tests/test_config.py -v
pytest tests/test_models/ -v
pytest tests/test_ref_cache.py tests/test_audit_fixes.py -v

# Per-session DB is dropped after pytest exits
psql -l | grep shekel_test_main_  # must be empty

# Two concurrent invocations no longer deadlock
# (run these in two terminals at the same time)
pytest tests/test_models/test_audit_migration.py -v   # terminal 1
pytest tests/test_models/test_audit_migration.py -v   # terminal 2
# Both must pass independently; neither shows "OperationalError: deadlock detected"
```
All pass counts must equal baseline.

**Why fourth:** substantive change. Lands on its own commit. If verification fails at any sub-step, revert and diagnose.

### Phase 4 -- Enable pytest-xdist parallelism and mark cluster-state tests

**Files:**
- `tests/test_models/test_audit_migration.py` -- add `@pytest.mark.xdist_group("shekel_app_role")` to the `TestLeastPrivilegeRole` class (line 389) and any sibling class that uses the `shekel_app_role` fixture. Cluster-level roles cannot be created/dropped concurrently across workers.
- `pytest.ini` -- register the `xdist_group` marker in a `markers =` section to silence pytest's "unknown marker" warning under strict-markers (defensive; the project doesn't currently use strict markers but the registration is no-cost).

**Verification:**
```bash
# Multi-worker basic
pytest tests/test_models/ -n 4 -v

# Multi-worker with the cluster-role tests
pytest tests/test_models/test_audit_migration.py -n 4 -v

# Confirm the marker serialises correctly
pytest tests/test_models/test_audit_migration.py -n 4 --collect-only -q | head -30

# Multi-worker route batch
pytest tests/test_routes/test_a* tests/test_routes/test_c* -n 4 -v
```
All pass. No "role already exists" or "role does not exist" errors. Per-worker DBs cleaned up on exit.

**Why fifth:** xdist is the user-visible parallelism change. Lands on its own commit.

### Phase 5 -- Update CI workflow and documentation

**Files:**
- `.github/workflows/ci.yml`:
  1. Add `TEST_ADMIN_DATABASE_URL: postgresql://shekel_test:shekel_test@localhost:5432/postgres` to the job's `env` block (admin connection points at the maintenance DB, not the test DB).
  2. Update Postgres service to grant the `shekel_test` role `CREATEDB` privilege (add `POSTGRES_INITDB_ARGS` or a setup step that runs `ALTER ROLE shekel_test CREATEDB`).
  3. Add a new step BEFORE `Run tests`: `Build test template` running `python scripts/build_test_template.py`.
  4. Change the `Run tests` step's command from `pytest --tb=short -q` to `pytest --tb=short -q -n auto`.
- `docs/testing-standards.md`:
  1. Replace the "NEVER run two pytest processes concurrently" warning (was added in this session) with the new guarantee: "Concurrent invocations are safe -- each session gets its own DB cloned from `shekel_test_template`."
  2. Add a "Building the test template" subsection: how to build it (`python scripts/build_test_template.py`), when to rebuild (after migrations, after `app/ref_seeds.py` changes), and where to look if it's missing.
  3. Add a "Cluster-level tests" subsection documenting the `xdist_group` pattern for the `shekel_app_role` tests, so future role-touching tests adopt the same marker.
  4. Update the batch table to reflect xdist-parallel wall-clock estimates (full suite ~6-10 min with `-n auto` vs ~28 min sequential).
- `CLAUDE.md`:
  1. Replace the "NEVER run concurrent pytest processes" line with: "Concurrent invocations are safe (each session gets its own per-pytest DB cloned from `shekel_test_template`). Use `pytest -n auto` to parallelise within one invocation."
  2. Add a one-line first-time setup note: "Build the test template once: `python scripts/build_test_template.py`."

**Verification:**
- Re-run the CI workflow on a feature branch. Confirm green.
- Read each doc top-to-bottom. The new flow should be discoverable without prior context.

**Why last:** documentation updates are riskless once behaviour is verified. CI update can land in the same commit since it's a small, surgical change.

## Critical files to be modified

| File | Phase | Change type |
|---|---|---|
| `requirements-dev.txt` | 0 | Add `pytest-xdist` |
| `app/ref_seeds.py` | 1 | Add `seed_reference_data(session)` function |
| `tests/conftest.py` | 1, 3 | Reroute `_seed_ref_tables` (P1); module-level bootstrap + sessionfinish + simplify `setup_database` + add `engine.dispose` to `db` teardown (P3) |
| `app/__init__.py` | 1 | Reroute `_seed_ref_tables` (line 861) to call `seed_reference_data` |
| `scripts/seed_ref_tables.py` | 1 | Reroute to call `seed_reference_data` |
| `scripts/build_test_template.py` | 2 | NEW file -- template builder using `flask db upgrade head` |
| `tests/test_models/test_audit_migration.py` | 4 | Add `@pytest.mark.xdist_group("shekel_app_role")` on `TestLeastPrivilegeRole` and sibling classes that use `shekel_app_role` fixture |
| `pytest.ini` | 4 | Register `xdist_group` marker |
| `.github/workflows/ci.yml` | 5 | Add CREATEDB grant, add template-build step, change pytest invocation to `-n auto` |
| `docs/testing-standards.md` | 5 | Replace deadlock warning, document template build, document `xdist_group` |
| `CLAUDE.md` | 5 | Replace deadlock warning, document first-time template build |

## Existing functions/utilities to reuse

- `app/audit_infrastructure.py::apply_audit_infrastructure(executor)` -- already idempotent, already exported, already in use by current `tests/conftest.py::_create_audit_infrastructure`. Called from the template builder.
- `app/audit_infrastructure.py::AUDITED_TABLES` and `EXPECTED_TRIGGER_COUNT` -- used by the template builder's verification step.
- `app/ref_seeds.py::ACCT_TYPE_SEEDS` -- the canonical account-type seed list; consumed by the new `seed_reference_data` function.
- `tests/conftest.py::_refresh_ref_cache_and_jinja_globals` -- already in use; the simplified `setup_database` continues to call it.
- `flask_migrate.upgrade(directory, revision)` -- used by `scripts/build_test_template.py` to run migrations against the freshly-created template DB.
- The current `db` fixture's TRUNCATE block (lines 196-229) and the table list it cites are correct and preserved verbatim.

## Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| `flask db upgrade head` exposes existing model-vs-migration drift on first template build | Medium -- the suite has been masking this for some time | Phase 2a pre-flight drift check. If drift detected, surface to user before continuing. Drift fix is out of scope for this work. |
| User runs pytest before building the template | High (first-time setup) | Bootstrap raises with explicit "Run: python scripts/build_test_template.py" |
| Crashed pytest leaks per-session DB | Low | Bootstrap cleans up orphans using `pg_stat_activity` not just name patterns. PID-reuse trap avoided. |
| Two xdist workers race on `shekel_app` role creation | High under parallel runs | `@pytest.mark.xdist_group("shekel_app_role")` forces serialisation onto one worker |
| `pytest_sessionfinish` not called on SIGKILL | Medium | Next session's bootstrap cleanup handles orphans |
| `DROP DATABASE WITH (FORCE)` not supported on PG <13 | None (project uses PG 16 in prod and CI) | Document the version requirement in `scripts/build_test_template.py` |
| CI fails on first push after merge | Low (Phase 5 updates CI in the same change) | CI updates land before merge |
| Per-session DB name length exceeds PostgreSQL 63-char limit | None (longest realistic name is ~30 chars) | No mitigation needed |
| Connection pool in the secondary apps used by fresh-app tests (test_errors, test_auth, test_rate_limiter) holds connections that block DROP DATABASE | Low | Each fresh-app test already calls `_db.engine.dispose()` in teardown; `WITH (FORCE)` is a final safety net |
| `app/__init__.py::_seed_ref_tables` silently swallows `ProgrammingError` and quietly writes a stale schema | Low after Phase 1 | Phase 1 consolidates the three seed call sites; future column-shape changes propagate through `seed_reference_data` consistently |

## Out of scope (documented as follow-ups)

- Refactoring `scripts/seed_user.py` and `scripts/seed_tax_brackets.py` to share more logic with the test paths. The test paths use fixtures (`seed_user`, `seed_full_user_data`) that are intentionally different from production seeds.
- Adding a `scripts/clean_test_databases.py` utility for manual operator cleanup. The conftest's startup orphan-cleanup covers the common case; an explicit utility is a future convenience.
- Renaming `shekel_test` to something more neutral. The test DB name appears in CI hardcodes and `.env.example`; a rename is a separate cleanup pass.
- Switching the per-test cleanup from TRUNCATE to per-test template clone (Pattern C). Pattern C was evaluated and rejected for this codebase -- see the comparison in the planning discussion. The TRUNCATE pattern is the right contract for the audit-trail invariants.

## End-to-end verification (after all phases land)

Run, in order:

```bash
# 1. Single-process suite, every directory
pytest tests/test_config.py tests/test_models/ tests/test_services/ -q
pytest tests/test_routes/test_a* tests/test_routes/test_c* -q
pytest tests/test_routes/test_d* tests/test_routes/test_e* tests/test_routes/test_g* tests/test_routes/test_h* tests/test_routes/test_i* -q
pytest tests/test_routes/test_l* tests/test_routes/test_m* tests/test_routes/test_o* tests/test_routes/test_p* -q
pytest tests/test_routes/test_r* tests/test_routes/test_s* tests/test_routes/test_t* tests/test_routes/test_x* -q
pytest tests/test_integration/ -q
pytest tests/test_adversarial/ tests/test_scripts/ tests/test_deploy/ -q
pytest tests/test_audit_fixes.py tests/test_ref_cache.py tests/test_schemas/ tests/test_utils/ tests/test_concurrent/ -q

# 2. Parallel suite via xdist
pytest -n auto -q

# 3. Concurrent invocations (proves the deadlock is gone)
# Run in two terminals at once:
pytest tests/test_models/test_audit_migration.py -v   # terminal A
pytest tests/test_models/test_audit_migration.py -v   # terminal B

# 4. Pylint
pylint app/ scripts/ --fail-on=E,F

# 5. CI
# Push to a feature branch; confirm the CI workflow goes green.

# 6. Cleanup verification
psql -l | grep '^[[:space:]]*shekel_test_'
# Should show only `shekel_test_template`. No per-session DBs left over.
```

Each step's pass count must equal or exceed the baseline (5,131 tests). No failed, errored, xfailed, or unexpected-passed lines are tolerated.

## Time estimate

- Phase 0: 10 minutes
- Phase 1: 1 hour (extracting + rerouting three call sites; ~90 lines of code)
- Phase 2: 1.5 hours (new script + drift pre-flight; first migration-based template build may surface unknown drift)
- Phase 3: 2 hours (conftest rewrite; the substantive change)
- Phase 4: 30 minutes (markers + verification)
- Phase 5: 1 hour (CI + docs)
- Total: ~6 hours of focused work, plus drift-detection time if Phase 2 surfaces existing migration drift.

Each phase is independently committable and revertable. The user can pause between phases for review or production deploys.
