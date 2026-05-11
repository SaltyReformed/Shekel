# Testing Standards and Problem Reporting

These standards apply to all testing activities in the Shekel project. They are referenced from
CLAUDE.md and are loaded when working on tests or when test-related decisions arise.

---

## Test Infrastructure

- Tests use a real PostgreSQL database (`TEST_DATABASE_URL` or TestConfig defaults).
- `conftest.py` uses session-scoped app/db setup, truncates tables between tests.
- Test categories: `test_routes/`, `test_services/`, `test_models/`, `test_integration/`,
  `test_adversarial/`, `test_scripts/`.
- **Use existing fixtures** from `conftest.py` (`seed_user`, `seed_second_user`, `auth_client`,
  `second_auth_client`, etc.). Do not create ad-hoc user setup in test methods.
- **Check for existing coverage** before writing a new test. Duplicate tests waste time and
  create maintenance burden.

## Test Run Guidelines

- **Full suite:** ~4 min wall-clock, ~5,148 tests at the default
  `-n 12` parallelism (set in `pytest.ini` `addopts`).  A single
  `pytest` invocation completes well under the 10-min timeout
  that previously forced per-directory batching.
- **Concurrent invocations are safe.**  `tests/conftest.py`'s
  `_bootstrap_worker_database` clones `shekel_test_template` into
  a uniquely-named per-session DB for every pytest session (and
  every pytest-xdist worker within a session), rewriting
  `TEST_DATABASE_URL` before the first `import app`.  Two
  terminals running `pytest` against the same PostgreSQL cluster
  get independent databases; the per-test `TRUNCATE ... CASCADE`
  no longer deadlocks across invocations as it did pre-Phase-3.
- **First-time setup:** build the template once with
  `python scripts/build_test_template.py`; see "Building the test
  template" below for when to rebuild.
- **Before reporting done:** every batch (or the single full-
  suite invocation) must end in `<N> passed`; any `failed`,
  `errors`, or `xfailed` lines block the "done" report.
- **During development:** run only relevant test files; targeted
  runs typically finish in seconds.
- **Override parallelism:** `-n 0` for single-process debugging,
  `-n auto` to match the host CPU count, or any specific number.
  The CLI flag overrides `pytest.ini`'s default.  Past `-n 12`
  the marginal speedup falls off because PostgreSQL's cluster-wide
  WAL/fsync pipeline (not CPU) is the serialised resource; see
  `docs/audits/security-2026-04-15/test-performance-research.md`
  for the full profile.
- **Test timeout:** 30s per test, configured in `pytest.ini`.
  Slowest known test is ~3s (bcrypt-bound MFA/auth tests; ~1-3s
  each is expected).  Anything past 30s raises a timeout error
  rather than hanging the suite.

### Optional per-directory batching

The 8-batch split below was required when the suite was ~28 min
sequentially and the 10-min CI timeout forced sub-batches.  At
the new `-n 12` default it is no longer required, but the table
remains documented for three scenarios:

1. **Slow-PG environments** where `-n 12` saturates the WAL queue
   and batches at `-n 4` are more reliable than the full suite at
   `-n 4`.
2. **Sequential debugging (`-n 0`):** the full suite is ~28 min
   sequentially; batched output makes failures easier to triage
   and lets you re-run a single batch after a fix.
3. **Bisecting a regression** -- one batch finishes faster than
   the whole suite.

| Batch | Tests | `-n 12` | `-n 0` |
|---|---|---|---|
| `tests/test_config.py tests/test_models/ tests/test_services/` | ~1,740 | ~1:20 | ~9:21 |
| `tests/test_routes/test_a* tests/test_routes/test_c*` (includes `test_auth.py`, slowest single file) | ~860 | ~0:45 | ~4:40 |
| `tests/test_routes/test_d* test_e* test_g* test_h* test_i*` | ~390 | ~0:25 | ~2:17 |
| `tests/test_routes/test_l* test_m* test_o* test_p*` | ~290 | ~0:20 | ~1:39 |
| `tests/test_routes/test_r* test_s* test_t* test_x*` | ~690 | ~0:40 | ~4:01 |
| `tests/test_integration/` | ~220 | ~0:15 | ~1:17 |
| `tests/test_adversarial/ tests/test_scripts/ tests/test_deploy/` | ~545 | ~0:30 | ~2:59 |
| `tests/test_audit_fixes.py test_ref_cache.py test_schemas/ test_utils/ test_concurrent/` | ~400 | ~0:25 | ~2:02 |

Totals: ~5,148 tests / ~4 min at `-n 12` / ~28 min at `-n 0`.
`tests/test_performance/` is excluded from the default `addopts`
and must be invoked explicitly: `pytest tests/test_performance
-v -s`.

The `-n 12` column is derived from the Phase 4.5 profile of
Batch 1 (measured: 79s at `-n 12`, 561s sequential, 7.07x
speedup) applied to the sequential numbers, with a small-batch
floor for pytest startup + 12-worker bootstrap overhead.  Treat
the figures as ballpark, not measured per-batch.

## Building the test template

`shekel_test_template` is the PostgreSQL template database that
`tests/conftest.py::_bootstrap_worker_database` clones into a
uniquely-named per-session DB at the start of every pytest
invocation (and every pytest-xdist worker within a session).
Cloning a populated template is roughly two orders of magnitude
faster than running migrations + audit infrastructure + reference
seed per session, which is what unlocks the parallel and
concurrent-safe test runs documented above.

**First-time build:**

```bash
python scripts/build_test_template.py
```

The script is idempotent: it drops and recreates the template on
every run, so re-running is the recovery path for any template-
corruption symptom.  Three steps print progress: drop+create,
populate (Alembic chain to `head` + audit infrastructure +
reference seed + `TRUNCATE system.audit_log`), verify (account-
type count, audit trigger count, `system.audit_log` row count).

**When to rebuild:**

- **After a migration** (`flask db migrate` + `flask db upgrade`).
  The template runs `alembic.command.upgrade(..., 'head')` at
  build time; per-test clones do not pick up new migrations
  without a template rebuild.
- **After editing `app/ref_seeds.py`.**  Reference data lives in
  the template; per-test fixtures re-seed against the existing
  schema but do not pick up new ref tables or changed seed
  contents without a rebuild.
- **After editing `app/audit_infrastructure.py`,** particularly
  additions to `AUDITED_TABLES`.  The template carries the audit
  triggers; new triggers attach only after a rebuild.
- **If the bootstrap raises `RuntimeError`** complaining the
  template is missing or has the wrong row/trigger count.  The
  error message names the offending count and the most likely
  root cause.

**Environment:**

The script reads `TEST_ADMIN_DATABASE_URL` for the admin DSN
(default `postgresql:///postgres`).  Local development
convention is
`postgresql://shekel_user:shekel_pass@localhost:5433/postgres`
(matching the local PG container); CI uses
`postgresql://shekel_test:shekel_test@localhost:5432/postgres`.
`SECRET_KEY` is defaulted by the script -- the template DB is
never reachable through Gunicorn so the value is purely
scaffolding for app construction.

## Cluster-state tests and `xdist_group`

PostgreSQL has two kinds of state.  Per-database state (rows,
indexes, triggers, schemas) is isolated by the per-session DB
clone: two xdist workers writing to `budget.transactions` cannot
collide because each writes to its own database.  Cluster-scoped
state (`CREATE ROLE`, replication slots, `pg_advisory_lock`) is
shared across all databases in the cluster; two workers racing on
the same cluster-level operation will collide.

The only test file in the current suite that mutates cluster
state is `tests/test_models/test_audit_migration.py`: the
`shekel_app_role` fixture executes `CREATE ROLE shekel_app` and
`DROP ROLE shekel_app`, and `apply_audit_infrastructure` (called
from sibling test classes in the same file) conditionally
`GRANT`s to the role when it exists.  Both touch the cluster.

**Pattern:** pin all tests that touch cluster state to one
pytest-xdist worker via `@pytest.mark.xdist_group("name")`:

```python
import pytest

# Module-level marker pins every test in this file to a single
# pytest-xdist worker.  pytest.ini's --dist=loadgroup is what
# makes the marker actually serialise (under the default
# --dist=load the marker is metadata only).
pytestmark = pytest.mark.xdist_group("shekel_app_role")
```

Use a **module-level** marker when sibling classes share the
cluster-state coupling (as in `test_audit_migration.py`).  Use a
**class-level** or **test-level** marker when only some tests in
the file are affected.

The marker **name** must be unique per cluster-state resource:
tests sharing a name run on the same worker, so two unrelated
cluster-state tests with the same name would unnecessarily
serialise.  Prefer distinct names per resource.

If you add a new test that mutates cluster state, add the marker
and a comment naming the resource.  Without it the test will
race across workers and produce intermittent failures like
`role "X" already exists` or `DependentObjectsStillExist` on
`DROP ROLE`.

## Zero Tolerance for Failing Tests

When you run the test suite -- targeted or full -- every test must pass. If any test fails, you
must investigate. Do not report "done" while any test is failing.

If a test you did not write is failing:

1. Determine what it tests.
2. Determine whether your changes caused the failure.
3. If your changes caused it, fix your code (not the test -- see CLAUDE.md rule 5).
4. If your changes did not cause it, report the failure with full details and ask how to proceed.

Never assume a failing test is someone else's problem. There is no one else.

## Test Output is Evidence

When reporting test results, include the actual output -- pass counts, fail counts, error
messages. Do not summarize "tests passed" without showing it. If output is long, show the
final summary lines at minimum.

## Test Quality Standards

A test that does not verify behavior is worse than no test -- it creates false confidence.

### Route Tests

Route tests must assert **response content, not just status codes.** A 200 means Flask did not
error. It does not mean the response is correct. After the status code, assert: correct records
present, financial amounts correct, right template rendered, expected HTML fragments in HTMX
responses. For JSON, assert structure and values. For form submissions, assert database state
changed correctly.

### Service Tests

Service tests must assert **computed values with exact expectations.** Do not assert
`result > 0` or `result is not None` when you can compute the expected value by hand. For
financial calculations, every test should include a comment showing the arithmetic that
produces the expected value.

### Edge Case Tests

Edge case tests must assert the **specific edge behavior**, not just that the function did not
crash. A test for "zero amount" must assert what happens with zero, not just that no exception
was raised.

### General Test Requirements

- **All tests need docstrings** explaining what is verified and why.
- **Tests must be independent.** Each test sets up its own preconditions. No ordering
  dependencies or shared mutable state between tests.
- **Test the behavior, not the implementation.** Assert what the function produces, not how it
  produces it. Implementation-coupled tests break on every refactor.

---

## Problem Reporting Protocol

You are the only automated safeguard this project has. If you see a problem and say nothing,
that problem ships to production.

### What Counts as a Problem

A failing test. A linter warning. A logic error noticed while reading code. A function that
does not handle an edge case. A query missing a `user_id` filter. A Decimal compared to a
float. A TODO that has been there for months. An unused import. A migration that does not match
the model. Any discrepancy between what the code does and what it should do.

### Response Protocol

1. **Within scope of the current task:** Fix it. Test the fix. Include it in the commit.
2. **Outside scope but quick and safe:** Report it to the developer. Fix in a separate commit
   only if the developer approves.
3. **Outside scope and risky or complex:** Report it immediately. State: what the problem is,
   where it is (file and function), what the impact could be, and your recommended next step.
   Lead with it -- do not bury it at the end of a long message.

### What You Must Never Do

- Say "this test was already failing" and move on.
- Say "this is unrelated to my changes" without investigating and reporting.
- Say "tests pass" when any test failed.
- Treat a pre-existing bug as acceptable because it predates your work.
- Assume the developer knows about a problem. If you are not certain, tell them.
