# Testing Standards and Problem Reporting

These standards apply to all testing activities in the Shekel project. They are referenced from
CLAUDE.md and are loaded when working on tests or when test-related decisions arise.

**This file is the rationale tier and the one home for the suite's dated measurements.** CLAUDE.md
states each rule in one line and points here; `.claude/rules/testing.md` carries the path-scoped
must-knows; a fact lives in one tier and the other tiers point at it.

---

## Test Infrastructure

- Tests use a real PostgreSQL database (`TEST_DATABASE_URL` or TestConfig defaults).
- `conftest.py` uses session-scoped app/db setup, truncates tables between tests.
- Test categories: `test_routes/`, `test_services/`, `test_models/`, `test_integration/`,
  `test_adversarial/`, `test_scripts/`.
- **Use existing fixtures** from `conftest.py` (`seed_user`, `seed_second_user`, `auth_client`,
  `second_auth_client`, etc.). Do not create ad-hoc user setup in test methods.
- **Check for existing coverage** before writing a new test. Duplicate tests waste time and create
  maintenance burden.

## Test Run Guidelines

- **Invoke via `./scripts/test.sh`, not bare `pytest`.** The wrapper resolves the test DSNs out of
  `.env`, defaults the marker expression, and forwards all arguments verbatim.
  **It restarts `shekel-dev-test-db` only when `RESTART_TEST_DB` is set, to any non-empty value**
  (see "Catalog fragmentation and the test-runner wrapper" below for what the restart buys and why
  it is opt-in), and falls through to plain pytest when the container is absent (CI, fresh
  checkout).
- **Container-spawning deploy tests are excluded by default.** The `tests/test_deploy` integration
  tests that drive a real `docker` daemon are marked `@pytest.mark.docker`, and `./scripts/test.sh`
  defaults to `-m "not docker"` so a routine local run never spawns containers on the host's
  production Docker daemon (which the homelab `wud`/`cadvisor`/`alloy` stack watches). CI runs bare
  `pytest`, so it still executes them. A `tests/test_deploy/conftest.py` guard also skips them if a
  bare `pytest` reaches the system daemon outside CI. Opt in locally with
  `SHEKEL_ALLOW_HOST_DOCKER=1 PYTEST_MARKER_EXPR=docker ./scripts/test.sh tests/test_deploy/...`.
  Full rationale and the daemon-isolation plan: `docs/test-harness-isolation.md`.
- **Full suite:** ~11,800 tests, ~4.5-5 min at the default `-n 12` parallelism (set in `pytest.ini`
  `addopts`). Measured 2026-08-30: 11,788 passed in 278-296 s over four runs, ~18 s run-to-run
  variance. Do not quote these figures without their date; the wrapper's own output is the current
  measurement.
- **Concurrent invocations are serialized by the suite slot** (`scripts/suite_slot.sh`, PR #199,
  2026-09-02): `acquire <name>` before a gating run, `release <name>` after, `status` to inspect.
  The postmaster is SHARED. A `RESTART_TEST_DB=1` run attempts a hygiene restart first, and its
  live-backend probe skips the restart when another run's connections are visible -- but the probe
  is a race (probe, then restart), it is blind to a run whose only connections sit on the excluded
  admin database (observed 2026-09-04: it read ZERO backends while a 756 s full-suite run was live,
  and the restart that followed voided that run with 155 setup errors), and even a correctly skipped
  restart leaves two suites contending (the slot script's header carries the measurement: 859 s
  against 304 s alone, both results void). A probe is not a lock, which is why the slot is mandatory
  rather than advisory. Semantics, exemptions and the staleness rules live in
  `.claude/rules/testing.md` and the script's own header. What the slot does not cover, the worker
  databases do: the per-worker DB name is the stable form `shekel_test_{worker_id}` (no PID suffix),
  so two unslotted invocations against one cluster collide with a clear "database already exists"
  failure rather than silent corruption -- isolate a second checkout with `TEST_DB_PREFIX` and
  `TEST_TEMPLATE_DATABASE` ("Two checkouts against one cluster" below). Orphan cleanup at session
  start drops any leftover DB from a previous crashed run.
- **First-time setup:** build the template once with `python scripts/build_test_template.py`; see
  "Building the test template" below for when to rebuild.
- **Before reporting done:** every batch (or the single full- suite invocation) must end in
  `<N> passed`; any `failed`, `errors`, or `xfailed` lines block the "done" report.
- **During development:** run only relevant test files; targeted runs typically finish in seconds.
- **Override parallelism:** `-n 0` for single-process debugging, `-n auto` to match the host CPU
  count, or any specific number. The CLI flag overrides `pytest.ini`'s default. Past `-n 12` the
  marginal speedup falls off because PostgreSQL's cluster- wide `pg_database` catalog lock (formerly
  the WAL/fsync pipeline pre-Phase-3) is the serialised resource; see
  `docs/audits/test_improvements/test-performance-research.md` for the full profile.
- **Test timeout:** 30s per test, configured in `pytest.ini`; anything past 30s raises a timeout
  error rather than hanging the suite. The bcrypt-bound MFA/auth tests are the slow tail. (A
  slowest-test figure once quoted here was measured stale and is dropped rather than re-pinned;
  re-measure with `--durations` when the tail matters.)

## Catalog fragmentation and the test-runner wrapper

Phase 3b's per-test drop+reclone gives strict isolation but exposes a PostgreSQL behaviour worth
naming explicitly so future "is the suite slowing down?" investigations land on the answer
immediately.

**Symptom.** Over many back-to-back suite runs on a long-lived test-db container, full-suite
wall-clock drifts linearly. Measured progression starting from a freshly-restarted container:

| Run | Wall (s) | Single CREATE/DROP (ms) |
|---:|---:|---:|
| 1 | 71 | 14.6 |
| 2 | 72 | 15.6 |
| 3 | 76 | 18.3 |
| 4 | 81 | 20.9 |
| ~50 (37 h uptime) | 220 | 128 |

The slowdown is entirely in `DROP DATABASE WITH (FORCE)`; the fixture profile harness
(`SHEKEL_TEST_FIXTURE_PROFILE=1`) shows DROP dominating ~80 % of per-test fixture cost in the
degraded state. `CREATE DATABASE ... TEMPLATE` stays roughly constant, independent of catalog state.

The figures above were measured when the clone used `STRATEGY FILE_COPY`. The clone now uses
`STRATEGY WAL_LOG`; see `tests/conftest.py::_clone_worker_database` for the measurements that forced
the change. `FILE_COPY` forces three cluster-wide checkpoints per drop+create cycle against one for
`WAL_LOG`, which costs nothing on this cluster (`fsync=off`) and 20x on any cluster with durability
on, including CI until 2026-08-18.

**Cause.** Not on-disk bloat. Verified with `VACUUM`, `VACUUM (FULL)` on `pg_database` /
`pg_shdepend` / `pg_shseclabel` / `pg_db_role_setting`, and `CHECKPOINT` -- none of them moved DROP
time at all. The catalog tables are small (1 page, 5 live rows, 0 dead) even in the degraded state.

The accumulation is in PG's in-memory shared state: the shared invalidation (`sinval`) queue,
syscache, and relcache invalidations broadcast by every DDL operation. Long-lived backends (Python
xdist worker pools held by SQLAlchemy) consume these invalidations slowly, and over thousands of
CREATE/DROP DATABASE cycles the postmaster's bookkeeping degrades. Only restarting the postmaster
resets it.

Verified by the negative: 5,000 CREATE/DROP cycles through fresh `psql` connections (each command
exits, no long-lived backend) does **not** fragment -- DROP stays at ~3 ms. Only the workload
pattern of "many long-lived backends + heavy DDL" triggers the drift.

**Fix.** `RESTART_TEST_DB=1 ./scripts/test.sh` restarts `shekel-dev-test-db`, waits for
`pg_isready`, then execs into pytest with whatever arguments were passed. It was unconditional until
2026-09-04; the "Escape hatches" list below carries why it is now opt-in and what replaced the
always-on reset as the drift signal.

Escape hatches:

- **The restart is OPT-IN: `RESTART_TEST_DB=1 ./scripts/test.sh ...`** (inverted 2026-09-04; the
  previous opt-out spelling `SKIP_DB_RESTART` was deleted rather than kept as a second way to say
  the same thing). Ask for it before a gating full-suite run. Two reasons the default is no-restart,
  and only the second is a shared-cluster artifact: the cost is fixed while the benefit is
  proportional to how much DDL the run does, so a targeted run paid the whole restart for drift it
  did not cause; and the restart terminates every backend on a container every worktree shares,
  which made an ordinary targeted run a hazard to a peer's in-flight suite.
- **What tells you when to ask.** A run that skips the restart reports the container's state,
  because with the restart opt-in there is otherwise no instrument for the drift anywhere in the
  repo. When the container is up that is its uptime, observed:

  ```text
  [test.sh] not restarting shekel-dev-test-db (Up 14 minutes (healthy)) -- set RESTART_TEST_DB=1 to force the hygiene restart
  ```

  It is not printed on every run: with docker absent, or the container absent or stopped, there is
  no uptime to report and the wrapper says which of those it found -- the stopped case loudly,
  because pytest is about to fail to connect and the old opt-out default used to start such a
  container silently.
  **The `~15 ms` CREATE/DROP cutoff this section named as the trigger is WITHDRAWN, not moved**: the
  table above reads 14.6 ms on a FRESHLY restarted container and 15.6 ms after one run, so the
  threshold fired immediately and meant either "restart every time" or nothing -- and those figures
  were taken under `STRATEGY FILE_COPY`, which the clone no longer uses. No replacement threshold is
  offered here, because re-deriving one under `WAL_LOG` belongs to the work that removes the shared
  cluster rather than to this wrapper: until then uptime is the signal and a gating run is the
  occasion.
- `TEST_DB_CONTAINER=other-container-name ./scripts/test.sh` -- point at a different test-db
  container (e.g. when running against a staging cluster on a different port). The wrapper answered
  to the bare `DB_CONTAINER` until 2026-09-04, which is the same environment variable
  `scripts/backup.sh`, `restore.sh` and `verify_backup.sh` read to name the PRODUCTION container --
  so one export aimed a hygiene restart at production, or a `restore.sh` DROP at a test container.
  `deploy/shekel-deploy.sh` had already avoided the clash with `SHEKEL_DB_CONTAINER`; the test
  runner now follows it. `DB_CONTAINER` is no longer read by the test runner at all.
- Wrapper is a no-op when the container does not exist, so CI (which spins up its own postgres
  service) is unaffected.
- **A requested restart is still SKIPPED, loudly, when another run is using the container.** It
  terminates every backend, so performing it while a second checkout's suite is live kills that run
  with `server closed the connection unexpectedly` -- measured 2026-08-08 as 208 setup errors, which
  read exactly like a code regression at the point where they surface. The wrapper first counts
  backends in `pg_stat_activity` on any database other than `postgres` / `template0` / `template1`.
  It does NOT match on the name `%test%`: plan step R7b-2 measured that predicate blind to exactly
  the runs it existed to protect, because the per-worker databases are named from `TEST_DB_PREFIX`
  (values like `r7a2`, `xf2c3`) and not from the word "test". This container is dedicated to the
  suite, so any non-admin database on it belongs to a run. The restart is shared-memory hygiene, not
  a correctness gate, so skipping it costs drift and nothing else.

### Two checkouts against one cluster

A worktree or second clone sharing `shekel-dev-test-db` needs BOTH halves isolated, and each has its
own environment variable (read from the environment, or from `.env` via the wrapper):

- `TEST_TEMPLATE_DATABASE=<name>` -- what the run clones FROM. Set it when the checkout's migration
  head differs from the other's, and build it with
  `TEST_TEMPLATE_DATABASE=<name> python scripts/build_test_template.py`.
- `TEST_DB_PREFIX=<name>` -- what the run clones INTO. The per-worker databases are
  `{prefix}_{worker_id}`, default prefix `shekel_test`. Without it
  **both checkouts default to `-n 12` and both claim `shekel_test_gw0..gw11`**; the loser dies with
  `DuplicateDatabase: database "shekel_test_gwN" already exists`, en masse, at fixture setup.

Tell the two failure signatures apart: `DuplicateDatabase` is a worker-name collision
(`TEST_DB_PREFIX`), `server closed the connection unexpectedly` is someone restarting the container
mid-run (the guard above). Setting `PYTEST_XDIST_WORKER` by hand isolates a serial `-n 0` run but
NOT `-n 12`, where xdist overwrites it per worker.

**Why not just VACUUM the shared catalogs from a pytest sessionstart hook?** Tried; does not help.
See "Cause" above. The fragmentation is in PG shared memory, not on-disk pages.

**Why not switch back to TRUNCATE-based reset?** The Phase 3b move to drop+reclone was driven by
audit-trigger and DDL-state isolation requirements (see
`docs/audits/test_improvements/per-worker-database-plan.md`). Reverting would re-introduce the bugs
Phase 3b fixed. Paying for the occasional hygiene restart is a better tradeoff than test isolation
gaps.

### Optional per-directory batching (historical)

The 8-batch split below was required when the suite was ~28 min sequentially and the 10-min CI
timeout forced sub-batches. At the `-n 12` default (the dated full-suite measurement is under Test
Run Guidelines above) it is **purely historical** -- batched invocations no longer offer any
wall-clock benefit and individual batches finish in seconds, so the bisecting-a-regression and
sequential- debugging scenarios are better served by `pytest <specific-file> -v` rather than a whole
batch. The table is preserved so existing references to "Batch N" in old commits or docs remain
decodable; DO NOT cite these timings in new measurements.

| Batch | Tests | Notes |
|---|---|---|
| `tests/test_config.py tests/test_models/ tests/test_services/` | ~1,740 | includes the Phase 0 harness slice (test_models, 253 tests) |
| `tests/test_routes/test_a* tests/test_routes/test_c*` (includes `test_auth.py`, slowest single file) | ~860 | -- |
| `tests/test_routes/test_d* test_e* test_g* test_h* test_i*` | ~390 | -- |
| `tests/test_routes/test_l* test_m* test_o* test_p*` | ~290 | -- |
| `tests/test_routes/test_r* test_s* test_t* test_x*` | ~690 | -- |
| `tests/test_integration/` | ~220 | -- |
| `tests/test_adversarial/ tests/test_scripts/ tests/test_deploy/` | ~545 | -- |
| `tests/test_audit_fixes.py test_ref_cache.py test_schemas/ test_utils/ test_concurrent/` | ~400 | -- |

Total then, in that era's own figures: ~5,504 tests / ~65 s at `-n 12` via `./scripts/test.sh` (full
suite is faster than the sum of batches because pytest startup + 12-worker bootstrap overhead
amortises over the full inventory rather than paying 8x); DO NOT cite these timings in new
measurements either. `tests/test_performance/` is excluded from the default `addopts` and must be
invoked explicitly: `./scripts/test.sh tests/test_performance -v -s`.

## Building the test template

`shekel_test_template` is the PostgreSQL template database that
`tests/conftest.py::_bootstrap_worker_database` clones into a uniquely-named per-session DB at the
start of every pytest invocation (and every pytest-xdist worker within a session). Cloning a
populated template is roughly two orders of magnitude faster than running migrations + audit
infrastructure + reference seed per session, which is what unlocks the parallel and concurrent-safe
test runs documented above.

**First-time build:**

```bash
python scripts/build_test_template.py
```

The script is idempotent: it drops and recreates the template on every run, so re-running is the
recovery path for any template- corruption symptom. Three steps print progress: drop+create,
populate (Alembic chain to `head` + audit infrastructure + reference seed +
`TRUNCATE system.audit_log`), verify (account- type count, audit trigger count, `system.audit_log`
row count).

**When to rebuild:**

- **After a migration** (`flask db migrate` + `flask db upgrade`). The template runs
  `alembic.command.upgrade(..., 'head')` at build time; per-test clones do not pick up new
  migrations without a template rebuild.
- **After editing `app/ref_seeds.py`.** Reference data lives in the template; per-test fixtures
  re-seed against the existing schema but do not pick up new ref tables or changed seed contents
  without a rebuild.
- **After editing `app/audit_infrastructure.py`,** particularly additions to `AUDITED_TABLES`. The
  template carries the audit triggers; new triggers attach only after a rebuild.
- **If the bootstrap raises `RuntimeError`** complaining the template is missing or has the wrong
  row/trigger count. The error message names the offending count and the most likely root cause.

**Environment:**

The script reads `TEST_ADMIN_DATABASE_URL` for the admin DSN (default `postgresql:///postgres`).
Local development convention is `postgresql://shekel_user:shekel_pass@localhost:5433/postgres`
(matching the local PG container); CI uses
`postgresql://shekel_test:shekel_test@localhost:5432/postgres`. `SECRET_KEY` is defaulted by the
script -- the template DB is never reachable through Gunicorn so the value is purely scaffolding for
app construction.

## Cluster-state tests and `xdist_group`

PostgreSQL has two kinds of state. Per-database state (rows, indexes, triggers, schemas) is isolated
by the per-session DB clone: two xdist workers writing to `budget.transactions` cannot collide
because each writes to its own database. Cluster-scoped state (`CREATE ROLE`, replication slots,
`pg_advisory_lock`) is shared across all databases in the cluster; two workers racing on the same
cluster-level operation will collide.

The only test file in the current suite that mutates cluster state is
`tests/test_models/test_audit_migration.py`: the `shekel_app_role` fixture executes
`CREATE ROLE shekel_app` and `DROP ROLE shekel_app`, and `apply_audit_infrastructure` (called from
sibling test classes in the same file) conditionally `GRANT`s to the role when it exists. Both touch
the cluster.

**Pattern:** pin all tests that touch cluster state to one pytest-xdist worker via
`@pytest.mark.xdist_group("name")`:

```python
import pytest

# Module-level marker pins every test in this file to a single
# pytest-xdist worker.  pytest.ini's --dist=loadgroup is what
# makes the marker actually serialise (under the default
# --dist=load the marker is metadata only).
pytestmark = pytest.mark.xdist_group("shekel_app_role")
```

Use a **module-level** marker when sibling classes share the cluster-state coupling (as in
`test_audit_migration.py`). Use a **class-level** or **test-level** marker when only some tests in
the file are affected.

The marker **name** must be unique per cluster-state resource: tests sharing a name run on the same
worker, so two unrelated cluster-state tests with the same name would unnecessarily serialise.
Prefer distinct names per resource.

If you add a new test that mutates cluster state, add the marker and a comment naming the resource.
Without it the test will race across workers and produce intermittent failures like
`role "X" already exists` or `DependentObjectsStillExist` on `DROP ROLE`.

## Zero Tolerance for Failing Tests

When you run the test suite -- targeted or full -- every test must pass. If any test fails, you must
investigate. Do not report "done" while any test is failing.

If a test you did not write is failing:

1. Determine what it tests.
2. Determine whether your changes caused the failure.
3. If your changes caused it, fix your code (not the test -- see CLAUDE.md rule 5).
4. If your changes did not cause it, report the failure with full details and ask how to proceed.

Never assume a failing test is someone else's problem. There is no one else.

## Test Output is Evidence

When reporting test results, include the actual output -- pass counts, fail counts, error messages.
Do not summarize "tests passed" without showing it. If output is long, show the final summary lines
at minimum.

## Test Quality Standards

A test that does not verify behavior is worse than no test -- it creates false confidence.

### Route Tests

Route tests must assert **response content, not just status codes.** A 200 means Flask did not
error. It does not mean the response is correct. After the status code, assert: correct records
present, financial amounts correct, right template rendered, expected HTML fragments in HTMX
responses. For JSON, assert structure and values. For form submissions, assert database state
changed correctly.

### Service Tests

Service tests must assert **computed values with exact expectations.** Do not assert `result > 0` or
`result is not None` when you can compute the expected value by hand. For financial calculations,
every test should include a comment showing the arithmetic that produces the expected value.

### Edge Case Tests

Edge case tests must assert the **specific edge behavior**, not just that the function did not
crash. A test for "zero amount" must assert what happens with zero, not just that no exception was
raised.

### General Test Requirements

- **All tests need docstrings** explaining what is verified and why.
- **Tests must be independent.** Each test sets up its own preconditions. No ordering dependencies
  or shared mutable state between tests.
- **Test the behavior, not the implementation.** Assert what the function produces, not how it
  produces it. Implementation-coupled tests break on every refactor.

---

## The ambient clock and the ambient calendar

**Depth, pitfalls and how to read a failure from either gate: `docs/test-suite-clocks.md`.**

**A test that passes on some days and fails on others is not a flaky test. It is a broken test with
a schedule.** Every "flake" this suite has produced has turned out to be one of the two couplings
below, and each was diagnosed only after it blocked a merge gate. Both are cheap to avoid and
expensive to find.

### 1. Use the APPLICATION's clock, never the process's

`date.today()` reads the PROCESS timezone. The application's civil day is
`app.utils.dates.display_today()` (`America/New_York`). Production and `docker-compose.dev.yml` both
pin `TZ: America/New_York`, so the two agree there -- **CI does not pin it and runs UTC**, so for
the four hours a day the calendars disagree, a test that mixes them fails.

> **Whenever a test builds a date that the application will compare against its own "today", the
> test must use `display_today()`.**

That covers an `entry_date` posted to a route (`entry_service._reject_future_entry_date` refuses
anything after `display_today()`, ruling R-M), a pay-period window that must contain the day
`get_current_period` looks for, an anchor's `observed_on`, and any assertion on a date the app
derived. Three live examples, all of which failed CI on 2026-08-01 and none of which was
reproducible in a dev shell:

| site | was | is |
|---|---|---|
| `test_c19_credit_payback_unique.py` -- posted `entry_date` | `date.today()` | `display_today()` |
| `test_c19_credit_payback_unique.py` -- period window | `date.today()` | `display_today()` |
| `test_optimistic_locking_c18.py::_make_entry` | `date.today()` | `display_today()` |
| `test_account_anchor_invariant.py` -- signup period | `date.today()` | `display_today()` |

**Do not "fix" this class by pinning CI's timezone.** That hides the coupling instead of removing
it, and the coupling is what breaks the moment any environment differs.

**How to check your work:** run the suite with the process clock shifted off the display clock. The
whole suite must pass unchanged.

```bash
TZ=Pacific/Kiritimati ./scripts/test.sh     # process date one day AHEAD of the app's
```

**This is a gate, not a suggestion.** `ci.yml`'s `lint-and-test` job sets `TZ: Pacific/Kiritimati`
deliberately, so the process date runs a day ahead of the app's for eighteen hours out of every
twenty-four and the coupling fails on most runs rather than on the four-hour window that let three
of these reach `main`. Never "fix" a failure there by setting CI's zone to `America/New_York`.

### 2. A fixture must not depend on WHERE IN THE CALENDAR it runs

Deriving fixture dates from "today" makes the SHAPE of the fixture depend on the date the suite
runs. Findings N-131 (six cross-page tests failing on the last days of a month), N-132 (fixtures
separating events by hours against a rule that reads civil days), R8 (an offset that silently became
a duplicate of its sibling) and the 2026-08-01 loan failures are all this.

The 2026-08-01 case is the clearest: a fixture originated a loan at the current period's start with
`payment_day=1`, and its own docstring promised *"clean past: no overdue installment"*. Whether that
was true depended on the day of the month --

- on the **1st** the first installment fell on today, where `balance_at` reads the ledger
  (`balance_at/_positions.py`) and the committed schedule shows the post-payment projection;
- on the days **between the 1st and the month's first Monday** it was overdue and unpaid, so the
  fold pays nothing for it (D1 / B-9) while the schedule still lists it -- and every later row
  diverges.

**State the property the fixture needs, then construct it so the property holds on every calendar
day**, rather than deriving from today and hoping. Pin the read
(`BalanceContext.build(user_id, as_of=...)`) to a date the fixture itself controls, and derive the
other dates from THAT.

Two shapes worth naming because both shipped:

- **Never `date.replace(year=...)`.** On 29 February it raises
  `ValueError: day 29 must be in range 1..28`. Use `app.utils.dates.add_months(d, 12)`, which clamps
  to 2029-02-28.
- **Never hard-code a date that is "in the future".** `target_date=date(2027, 6, 1)` stops being in
  the future on 2027-06-01, and the test that depends on it starts failing that morning. Derive it:
  `add_months(display_today(), 10)`.

**The gate:** `.github/workflows/calendar-sweep.yml` runs the whole suite weekly as if today were a
leap day, both sides of a year boundary, a month end, and the first of a month. Run one locally with

```bash
SHEKEL_FAKE_TODAY=2028-02-29 ./scripts/test.sh
```

### What the sweep cannot see, and the marker that says so

`time-machine` moves Python's clock. It cannot move POSTGRES: `created_at` / `updated_at` are
`server_default=db.func.now()` and `paid_at` is `db.func.now()`. So under a faked date a
server-stamped row carries the REAL instant, and any test comparing it against a Python-derived date
fails by the offset -- an artifact of the instrument, not a defect.

Those tests carry `@pytest.mark.server_clock`, and the sweep deselects them. Two of them assert the
database's clock on purpose (the `CURRENT_DATE` server default; the audit trigger's `executed_at`).

**The marker is a statement about the instrument, never a way to quiet a failure.** Every marked
test still runs in ordinary CI. A test earns the marker only after its failure has been traced to
the two clocks -- and **"it fails at some dates but not others" is not sufficient evidence**:
`test_two_same_day_trueups_reconcile` varies by date and is still an artifact, because its two
clocks only diverge once the faked date moves far enough. Trace to the clocks; do not infer from the
pattern.

---

## Problem Reporting Protocol

You are the only automated safeguard this project has. If you see a problem and say nothing, that
problem ships to production.

### What Counts as a Problem

A failing test. A linter warning. A logic error noticed while reading code. A function that does not
handle an edge case. A query missing a `user_id` filter. A Decimal compared to a float. A TODO that
has been there for months. An unused import. A migration that does not match the model. Any
discrepancy between what the code does and what it should do.

### Response Protocol

1. **Within scope of the current task:** Fix it. Test the fix. Include it in the commit.
2. **Outside scope but quick and safe:** Report it to the developer. Fix in a separate commit only
   if the developer approves.
3. **Outside scope and risky or complex:** Report it immediately. State: what the problem is, where
   it is (file and function), what the impact could be, and your recommended next step. Lead with
   it -- do not bury it at the end of a long message.

### What You Must Never Do

- Say "this test was already failing" and move on.
- Say "this is unrelated to my changes" without investigating and reporting.
- Say "tests pass" when any test failed.
- Treat a pre-existing bug as acceptable because it predates your work.
- Assume the developer knows about a problem. If you are not certain, tell them.
