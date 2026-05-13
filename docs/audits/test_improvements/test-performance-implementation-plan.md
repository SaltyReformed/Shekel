# Plan: Test-suite performance via cluster tuning, PG 18 upgrade, and per-test reflink cloning

## Status (as of 2026-05-12)

**Phase 0 + Phase 1 (1a + 1b + 1c + 1d) + Phase 2 (2a test-db slice + 2b + 2e) implemented (pending
commit); Phase 2c + Phase 2d PROPOSED awaiting developer go; Phase 3 pending.** This plan
implements the recommendations in `test-performance-research.md` (filed 2026-05-11) and supersedes
its "Phase A / Phase B / Phase C" framing with a four-phase sequential plan plus measurement gates
plus a coordinated PG 16 -> 18 upgrade across test, dev, CI, and production clusters.

The plan document itself landed on `dev` in commit `d781334` ("moved testing documents and added
improvement plan"). Phase 0 modifies four files on `dev` (working tree, uncommitted as of this
update): `tests/conftest.py`, `pytest.ini`, `.gitignore`, and
`docs/audits/test_improvements/test-performance-research.md` (the latter for the fresh baseline
capture). Phase 1d adds three test-only files to the same uncommitted set:
`tests/test_models/test_c41_baseline_unique_migration.py`, `tests/test_routes/test_errors.py`, and
`tests/test_routes/test_auth.py` -- targeted fixes for the two failure shapes Phase 1c surfaced.
After Phase 1d the parallel full suite is **deterministically clean at `-n 12` over 10 / 10
consecutive runs** (4 / 12 had failures earlier the same day before the fixes; 50 % flake rate
collapsed to 0 %). Full evidence chain in
[`phase1-flake-investigation.md`](phase1-flake-investigation.md).

Phase 2 (2026-05-12 second session) added two file edits to the uncommitted set:
`docker-compose.dev.yml` (test-db only -- the dev `db` service stays on PG 16 until Phase 2c's
dump/restore migration runs; in-place image swap would brick the 813-transaction local pgdata
volume) and `.github/workflows/ci.yml` (postgres service swapped to `postgres:18`). The full suite
on the PG 18.3 test cluster is **deterministically clean at `-n 12` over 3 / 3 consecutive runs**
(5276 passed, 52.75-53.59 s wall-clock, matching the Phase 1d band). Fixture floor 34.5 ms ->
35.7 ms (+3.5 %, well inside the spec's ~5 % parity bound).

| Phase | Status | Commit | Notes |
|---|---|---|---|
| Phase 0: Profile harness (committed permanently) | **Implemented (pending commit)** | -- | `tests/conftest.py` gains a permanent profile harness gated on `SHEKEL_TEST_FIXTURE_PROFILE=1` (module-level constants + six helpers + `_profile_step` context manager + aggregator wired into `pytest_sessionfinish`).  Per-test inner steps of the `db` fixture (rollback / TRUNCATE main / seed_ref / commit_after_seed / TRUNCATE audit_log / refresh_ref_cache / call / teardown) each get a timer that yields immediately as a no-op when the flag is unset.  `pytest.ini` registers `slow_fixture` marker for future per-test exclusion.  `.gitignore` excludes `tests/.fixture-profile/`.  `test-performance-research.md` gains section 8 with the new baseline (~298 ms fixture floor over 253 tests at `-n 0`).  Empirical zero-cost check: 83.07 s flag-unset vs 83.05 s flag-set = 0.02 % delta, well under the 2 % gate the plan requires. |
| Phase 1a: Docker-compose durability knobs + tmpfs | **Implemented (pending commit)** | -- | `docker-compose.dev.yml` `test-db` service gains `-c fsync=off -c synchronous_commit=off -c full_page_writes=off` and a `tmpfs: /var/lib/postgresql/data:rw,uid=70,gid=70,size=2g` block.  Stays on PG 16.  uid 70 verified via `docker run --rm postgres:16-alpine id postgres`. |
| Phase 1b: Conftest replication-role suppression | **Implemented (pending commit)** | -- | Wrap `_seed_ref_tables` in `SET LOCAL session_replication_role='replica'`.  **Deviates from spec:** the spec's claim that `TRUNCATE system.audit_log` becomes a no-op is incorrect (test-body audit writes persist across tests within a worker); instead `system.audit_log` is folded into the existing `TRUNCATE main 28 tables CASCADE` statement (now 29 tables) so cross-test audit isolation is preserved while still saving the second commit roundtrip the spec intended. |
| Phase 1c: Measurement gate | **Implemented (pending commit) -- flake surfaced, resolved in Phase 1d** | -- | Per-test fixture floor 298 ms -> 34.5 ms (-89 %, beat the 100-150 ms projection); full suite at `-n 12` 4 min -> ~52 s.  **Pre-existing test-isolation bug exposed:** the faster runtime triggers an intermittent ~50-70 %-rate failure cluster in `tests/test_routes/test_xss_prevention.py` + scenario-uniqueness tests (rate-limit state leaks from `test_errors.py::test_429_*` into downstream `auth_client` logins; mysterious `uq_scenarios_one_baseline` violation on bulk-insert of three `is_baseline=False` rows).  The bug does NOT reproduce on baseline docker-compose with the same Phase 1b conftest (0 failures / 2 baseline runs).  Phase 1 functional gate passes (single-process and audit-asserting tests are deterministic); the parallel-suite gate is flaky under Phase 1a's tmpfs+durability knobs.  Root cause identified and fixed in Phase 1d below. |
| Phase 1d: Flake resolution | **Implemented (pending commit)** | -- | Both failure shapes root-caused as pre-existing test-isolation bugs that Phase 1a's faster execution exposed by changing the pytest-xdist scheduling timing landscape (`--dist=loadgroup` distributes ungrouped tests INDIVIDUALLY across workers when no `xdist_group` marker is present).  **Shape #1** (HTTP 429 cluster) -- five rate-limit tests in `test_errors.py` and `test_auth.py` had cleanup outside a `try`/`finally`; when one test's assertion failed (specifically `Retry-After == "900"` rounding to 899 under WAL contention), the cleanup was skipped and the Limiter singleton was left `enabled=True` with a populated bucket, breaking every downstream `auth_client.post("/login", ...)` on the same xdist worker.  Fix: wrap cleanup in `try`/`finally`; relax `Retry-After` assertion to `895 <= retry_after <= 900` to absorb the integer-rounding window.  **Shape #2** (`uq_scenarios_one_baseline` UniqueViolation) -- `tests/test_models/test_c41_baseline_unique_migration.py::TestAssertIndexShapeRejectsMalformedIndex::test_assert_index_shape_raises_on_non_partial_index` intentionally created a malformed (non-partial) version of the index inside its body to verify the migration's shape check rejects it; the `restore_baseline_index` cleanup fixture used `CREATE UNIQUE INDEX IF NOT EXISTS` which is a no-op when ANY index of that name exists -- malformed shape leaked across worker tests.  Fix: change cleanup to `DROP INDEX IF EXISTS` + `CREATE UNIQUE INDEX` (drop-then-create rather than `IF NOT EXISTS`) in both the fixture and the `_recreate_canonical_index` helper.  Verification: 10 / 10 consecutive full-suite runs clean at `-n 12 --dist=loadgroup` on the Phase 1a cluster; deterministic two-test reproducer (test 14 + the three vulnerable scenario tests in sequence) passes after fix.  Pylint app/: 9.52/10 unchanged (no app/ changes).  Full investigation evidence in [`phase1-flake-investigation.md`](phase1-flake-investigation.md). |
| Phase 2a: PG 18 upgrade -- test cluster | **Implemented (pending commit) -- test-db slice only; dev `db` deferred to 2c** | -- | `docker-compose.dev.yml` `test-db` image: postgres:16-alpine -> postgres:18-alpine.  **Deviates from spec:** the dev `db` service stays on PG 16 because the populated `shekel-dev_pgdata` volume (auth=3 / budget=17 / ref=13 / salary=10 tables, 813 transactions, 2 users) cannot survive an in-place major-version restart -- PG 18 binaries refuse to read PG 16 files.  The `db` swap moves to Phase 2c's dump/restore.  Second spec deviation: PG 18's docker-library image (PR docker-library/postgres#1259) changed PGDATA from `/var/lib/postgresql/data` to `/var/lib/postgresql/$PG_MAJOR/docker` and refuses to start when a legacy mount sits at the old path; the Phase 1a tmpfs mount target was moved from `/var/lib/postgresql/data` to the parent `/var/lib/postgresql` so PG 18 places its per-major-version subdir inside the tmpfs.  uid 70 verified for postgres:18-alpine (`docker run --rm postgres:18-alpine id postgres` returns `uid=70(postgres) gid=70(postgres)`); existing `uid=70,gid=70` tmpfs entry stays correct.  Template rebuild + 32-test smoke + Phase 2e sanity pass clean. |
| Phase 2b: PG 18 upgrade -- CI | **Implemented (pending commit)** | -- | `.github/workflows/ci.yml`: postgres service `image: postgres:16` -> `image: postgres:18`; header comment refreshed from "PostgreSQL 16" to "PostgreSQL 18" with a back-reference to Phase 2.  No other CI changes -- the runner starts every job with an empty pgdata, so the docker-library/postgres#1259 layout change cannot fire the legacy-mount guard rail.  Not pushed to a feature branch in this session (the developer owns the CI verify loop). |
| Phase 2c: PG 18 upgrade -- dev (pg_dumpall + restore) | **PROPOSED, awaiting "execute Phase 2c"** | -- | Operational migration -- procedure drafted with three corrections vs spec: (a) plan's `python scripts/init_database.py --check` call cited in the verification step does NOT exist (the script accepts no arguments; the read-only substitute `flask db current` + row-count smokes is used instead); (b) plan's `docker volume rm shekel-dev_pgdata` is the correct name as written (verified via `docker volume inspect`); (c) the `db` service's volume mount needs the same path migration as Phase 2a's tmpfs (`/var/lib/postgresql/data` -> `/var/lib/postgresql`) so PG 18 can use its new layout inside the named volume.  Dump captured BEFORE the volume drop is the load-bearing recovery anchor.  Procedure printed in this turn; no edits applied until developer types "execute Phase 2c". |
| Phase 2d: PG 18 upgrade -- production (planned window) | **PROPOSED, awaiting "execute Phase 2d on <date>" -- 25-30 min downtime** | -- | Operational migration -- procedure drafted with four corrections vs spec: (a) volume name is `shekel-prod-pgdata` (declared `external: true` in `docker-compose.yml:499-500`, so the project prefix is stripped), NOT the plan's `shekel_pgdata`; (b) the same PG 18 layout-change YAML edit applies to the `db` volume mount in `docker-compose.yml`; (c) `deploy/docker-compose.prod.yml` requires NO change (image flows through inheritance); (d) btrfs snapshot is conditional on `/var/lib/docker/volumes/` actually living on a btrfs subvolume on the production host -- pg_dumpall is the load-bearing anchor regardless.  `POSTGRES_INITDB_ARGS=--data-checksums` (set in deploy/docker-compose.prod.yml:321) will fire on the fresh PG 18 initdb -- the intended Commit C-37 posture.  No execution until developer types "execute Phase 2d on <date>". |
| Phase 2e: PG 18 sanity measurement | **Implemented (pending commit)** | -- | Phase 0 harness re-run on PG 18.3 test cluster.  **Single-process (`-n 0`, 253 tests):** fixture total 34.5 ms (Phase 1) -> 35.7 ms (+3.5 %, well inside the spec's ~5 % parity bound); wall-clock 13.53 s -> 13.97 s.  **xdist (`-n 12`, 253 tests):** fixture total 53.4 ms -> 53.9 ms (+0.9 %); wall-clock 3.54 s -> 3.57 s.  **Full suite (`-n 12`):** 3 / 3 consecutive runs `5276 passed, 3 warnings in 52.75 / 53.59 / 52.91 s` -- pass count matches Phase 1d, no flake regression, same three pre-existing flask_login DeprecationWarnings.  Pylint app/: 9.52/10 unchanged.  PG 18 image swap delivers parity; reflink wins are Phase 3. |
| Phase 3a: btrfs subvolume + bind mount + `file_copy_method=clone` | **Not started** | -- | `/var/lib/shekel-test-pgdata` btrfs subvolume; bind-mount replaces Phase 1a's tmpfs; PG configured for reflink-backed `STRATEGY FILE_COPY`. |
| Phase 3b: Conftest rewrite -- per-test drop+reclone | **Not started** | -- | Replace per-test TRUNCATE+reseed+audit_log-truncate at `tests/conftest.py:363-413` with `DROP DATABASE ... WITH (FORCE)` + `CREATE DATABASE ... TEMPLATE ... STRATEGY FILE_COPY` using a stable per-worker DB name (avoids Flask-SQLAlchemy engine-rebinding). |
| Phase 3c: Drop redundant cleanup helpers | **Not started** | -- | Remove now-dead `_seed_ref_tables` wrapper at `tests/conftest.py:1300-1324`; keep `_refresh_ref_cache_and_jinja_globals` (still needed for in-process cache). |
| Phase 3d: Final measurement + docs sweep | **Not started** | -- | Re-run Phase 0 harness; update `docs/testing-standards.md`, `CLAUDE.md`, and `test-performance-research.md` with achieved numbers. |

**Branch state:** working tree on `dev` carries the Phase 0 + Phase 1 + Phase 2 (2a test-db slice
+ 2b) implementations across nine files (`tests/conftest.py`, `pytest.ini`, `.gitignore`,
`docs/audits/test_improvements/test-performance-research.md`,
`docs/audits/test_improvements/test-performance-implementation-plan.md`,
`docker-compose.dev.yml`, `.github/workflows/ci.yml`, plus Phase 1d's three test files), not yet
committed. The plan document itself landed earlier on `dev` in commit `d781334`; the next commits
attributable to this work will be the Phase 0 + Phase 1 + Phase 2 (2a + 2b + 2e) implementations
once the developer signs off.

## Context

The Shekel test suite has grown to 5,148 tests at ~4 min wall-clock under the default
`pytest-xdist -n 12` (`pytest.ini:34`). Per the 2026-05-11 profile
(`test-performance-research.md:64-83`), 96 % of single-process wall-clock is fixture setup, and 82 %
of fixture cost is the per-test `TRUNCATE TABLE ... CASCADE` of 28 tables at
`tests/conftest.py:363-396`. As the suite continues to grow during active development the wall-clock
penalty grows linearly: at the current floor a doubling of test count means ~8 min full-suite runs,
and a 4x grows to ~16 min -- at which point the suite stops being a fast feedback loop and starts
being a deferred batch step.

The developer is a solo operator with no QA team or CI gate other than their own runs (`CLAUDE.md`
"YOU ARE THE ONLY SAFEGUARD"). Catching budgeting / audit-log regressions depends on the suite being
run often enough to be a real feedback loop -- which depends on it staying fast as the codebase
grows.

The audit-trigger architecture (28 tables in `app/audit_infrastructure.py:65-106`; trigger function
at lines 173-252 fires AFTER INSERT/UPDATE/DELETE) is the project's only tamper-resistant forensic
record of financial state changes. Any change to the test-isolation contract must preserve audit-log
behavior bit-for-bit; SAVEPOINT-based rollback is explicitly rejected
(`per-worker-database-plan.md:62-63`, verified against
`tests/test_integration/test_audit_triggers.py` and `tests/test_scripts/test_audit_cleanup.py` which
assert on persisted `system.audit_log` rows that the test itself wrote).

The chosen path (driven by user decisions captured during the planning session on 2026-05-12) is a
sequential four-phase rebuild with measurement gates, a permanent profile harness, and a coordinated
PG 16 -> 18 upgrade across test / dev / CI / production to preserve test-prod parity. Per-test
reflink cloning (Phase 3) is the architectural endpoint; it eliminates the TRUNCATE-based cleanup
entirely and lets each test start from a known-good template clone. Phase 1 captures the near-term
win on the existing architecture; Phase 2 unlocks Phase 3.

**Host environment:** Arch Linux x86_64, kernel 7.0, btrfs root. btrfs supports the `FICLONE` ioctl
that PG 18's `file_copy_method = clone` calls into; no filesystem migration is needed. Docker uses
overlay2 storage by default, which does NOT preserve reflink semantics through its copy-on-write
layer -- the PG data directory must be a btrfs bind mount, not a Docker-managed volume, for cloning
to actually use reflink.

**Reflink filesystem requirement applies only to the development host running the test cluster, not
to production.** Production never executes `CREATE DATABASE ... TEMPLATE` -- the only place that
call exists is the per-test `_reset_worker_database` helper introduced in Phase 3b, which runs
inside pytest only. Production can be deployed on any filesystem PG 18 supports (ext4, xfs, btrfs,
zfs,...); `file_copy_method` is irrelevant there because nothing in the app or migrations consumes
it.

## High-level approach

1. **Phase 0** instruments the per-test `db` fixture with profiling
   timers gated by `SHEKEL_TEST_FIXTURE_PROFILE=1`. Committed
   permanently so every subsequent phase has a reproducible
   before/after comparison and any future regression in fixture cost
   is detectable.
2. **Phase 1** applies the cheap durability knobs and tmpfs to the
   PG 16 test cluster, plus suppresses audit triggers during the
   per-test seed. No architectural change; full suite drops from
   ~4 min to ~1.5-2 min at `-n 12`.
3. **Phase 2** upgrades PG 16 -> 18 across all four clusters (test,
   dev, CI, production) to preserve test-prod parity. Production
   upgrade is the only step with downtime; documented backup +
   rollback procedure.
4. **Phase 3** swaps tmpfs for a btrfs bind mount, sets
   `file_copy_method = clone`, and rewrites the per-test cleanup to
   drop-and-reclone from `shekel_test_template`. Each test starts
   from a fresh clone in ~10-30 ms (vs ~281 ms today). Full suite
   drops to ~30-60 sec at `-n 12`.

Each phase is independently committable and revertable. Each phase ends with a measurement against
the Phase 0 baseline; the next phase's start checks the measurement against the projection. Decision
gates between phases let the developer stop early if a phase delivers more (or less) than expected.

## Architectural decisions and constraints

- **Per-test isolation contract preserved bit-for-bit.** Every test
  must start with the same state it does today: empty `system.audit_log`,
  no rows in `auth.*` / `budget.*` / `salary.*` tables, full ref-data
  seed in `ref.*`, in-process `ref_cache` initialised, all Jinja
  globals matching the seeded IDs. Phase 3's per-test clone delivers
  this contract via a different mechanism (template clone vs TRUNCATE
  - reseed) but the contract is identical from the test's perspective.
- **No SAVEPOINT rollback.** The audit trigger architecture writes
  rows during the test body that the test then asserts on
  (`tests/test_integration/test_audit_triggers.py`,
  `tests/test_scripts/test_audit_cleanup.py`); rolling back the
  outer transaction would discard those rows along with the test's
  writes. Rejected in `per-worker-database-plan.md:62-63` and
  affirmed here.
- **Test-prod parity required.** A PG-18-only bug caught in test
  must not coexist with a PG-16-only bug shipping to prod. Phase 2
  upgrades all clusters together. The user's stated posture (see
  the 2026-05-12 planning session) drove this decision.
- **Reflink in production is out of scope.** The new
  `file_copy_method` consumer lives only in the test fixture path.
  Production filesystem choice is unchanged by this work.
- **Stable per-worker DB name in Phase 3.** The bootstrap creates
  one DB per pytest-xdist worker (e.g. `shekel_test_gw0`,
  `shekel_test_main_<pid>`). Per-test cleanup DROPs and re-clones
  this same name in place rather than allocating a new name per
  test. Avoids the Flask-SQLAlchemy 3.x engine-rebinding gotcha:
  the engine's URL never changes, only the underlying database is
  swapped atomically, and the existing `_db.engine.dispose()` call
  at `tests/conftest.py:439` forces the pool to reconnect on the
  next test's first session access.
- **`STRATEGY FILE_COPY` explicitly.** PG 18's `CREATE DATABASE
  ... TEMPLATE` defaults to `WAL_LOG` strategy unless the template
  is large enough to benefit from `FILE_COPY`. For a small
  template (~50-100 MB) the default would NOT engage the reflink
  path even with `file_copy_method = clone` set globally. Explicit
  `STRATEGY FILE_COPY` forces the reflink path regardless of size.
- **Profile harness committed permanently.** The 2026-05-11 capture
  used throwaway instrumentation (`test-performance-research.md:46-58`)
  that wasn't preserved. Phase 0 commits the harness behind an
  env-var flag so future regressions are detectable without rebuilding
  it from memory. Solo-developer safety net.

## Phase-by-phase plan

Each phase ends with a single verification command or measurement step. Each phase is independently
committable. If verification fails at any phase, revert that phase's commit and diagnose before
continuing.

### Phase 0 -- Profile harness (committed permanently) -- **Implemented (pending commit)**

**What landed:** `tests/conftest.py` gained a permanent profile harness gated on
`SHEKEL_TEST_FIXTURE_PROFILE=1`. Six module-level constants (`_FIXTURE_PROFILE_ENABLED`,
`_FIXTURE_PROFILE_DIR`, `_FIXTURE_PROFILE_STEPS`, `_FIXTURE_PROFILE_LABELS`,
`_FIXTURE_PROFILE_WORKER_ID`, `_FIXTURE_PROFILE_CSV`) plus eight new helpers (`_is_xdist_master`,
`_profile_session_init`, `_profile_step` -- a `contextmanager` --, `_profile_new_timings`,
`_profile_write_row`, `_profile_step_stats`, `_profile_load_rows`, `_profile_print_summary`) sit at
module load, before the existing `_bootstrap_worker_database` and before any `app` import. The
existing per-test `db` fixture (now at lines ~540-695) wraps each inner step in a
`with _profile_step(timings, step_name):` block; when the flag is unset, `timings` is `None` and the
context manager short-circuits to a no-op `yield`, leaving the wrapped block to run unchanged. Step
keys (`setup_rollback`, `setup_truncate_main`, `setup_seed_ref`, `setup_commit_after_seed`,
`setup_truncate_audit_log`, `setup_refresh_ref_cache`, `call`, `teardown`) drive both the CSV
columns and the row order in the summary table. The `try`/`finally` around `yield _db` ensures the
teardown timer and the CSV row write both run even when the test raises, so the harness never
silently biases its sample toward the passing path.

`pytest_sessionfinish` gained an aggregator that runs only on the master / single-process (detected
by `PYTEST_XDIST_WORKER` being unset): it reads every per-worker CSV from `tests/.fixture-profile/`,
computes avg / p50 / p95 / p99 / max for each step via
`statistics.quantiles(n=100, method="inclusive")` with empty-list and single-sample special cases,
and prints a Markdown-format table whose shape mirrors `test-performance-research.md` section 3.1
exactly (one row per setup step, then "Fixture setup total", then `call` and `teardown` as
informational rows without a percent column). The per-session DB drop runs BEFORE the aggregator so
a flaky summary path cannot leave per-session databases behind.

`pytest.ini` registers a new `slow_fixture` marker so future tests with abnormal per-fixture cost
can be excluded from per-step averages without raising `PytestUnknownMarkWarning`. `.gitignore`
excludes `tests/.fixture-profile/`. `docs/audits/test_improvements/test-performance-research.md`
gained a new section 8 with the fresh baseline plus a comparison vs. the 2026-05-11 capture (+17 ms
drift concentrated entirely in the TRUNCATE-main step; every other step within 0.1 ms).

**Notable design choices:**

- **Master-vs-worker detection at conftest load.** The existing `_bootstrap_worker_database`
  docstring claims the xdist master sets `PYTEST_XDIST_TESTRUNUID` at conftest load time;
  empirically (pytest-xdist 3.8 / pytest 9.0.2 / Python 3.14) it does NOT. Both the existing
  bootstrap and the new `_profile_session_init` therefore treat the master as
  indistinguishable from a single-process run AT MODULE LOAD: the master creates a per-session
  DB (dropped at sessionfinish) and writes a header-only `main.csv` stub. The aggregator
  handles the stub correctly -- `csv.DictReader` returns zero data rows from a header-only
  file, so the stub contributes nothing to the summary. The `_is_xdist_master()` check is
  kept in `_profile_session_init` Phase 2 as defence-in-depth for a future xdist that sets
  `TESTRUNUID` earlier.
- **Per-worker CSV layout.** The plan specced `_FIXTURE_PROFILE_PATH = pathlib.Path(...)`
  (singular) but did not commit to a file vs. directory. The implementation uses
  `_FIXTURE_PROFILE_DIR` (a directory) with one CSV per worker (`main.csv` for single-process,
  `gw0.csv`..`gwN.csv` for xdist). Per-worker files avoid append-time lock contention across
  workers; the aggregator unions the per-worker CSVs at session end.
- **Worker-aware aggregation via the master.** Only the process whose `PYTEST_XDIST_WORKER` is
  unset prints the summary (the master in xdist mode; the only process in single-process
  mode). This is the only process that sees every worker's CSV after the workers exit.
  pytest-xdist invokes `pytest_sessionfinish` on the master AFTER every worker finishes, so
  the aggregator reads a complete picture.
- **`nodeid` lookup gated.** `request.node.nodeid` is only evaluated when `timings is not
  None`; the disabled-flag path skips the attribute lookup entirely. Combined with the no-op
  context manager, the disabled-flag overhead measured at 0.02 % (83.07 s unset vs 83.05 s
  set), well inside the 2 % bound the plan requires.
- **Setup steps tagged with `setup_` prefix.** Step keys for fixture setup all start with
  `setup_`; the aggregator computes "Fixture setup total" by summing every column whose name
  starts with that prefix, then derives `% of fixture` from that average. `call` and
  `teardown` are reported but excluded from the percent column. This mirrors the published
  baseline's visual layout cell-for-cell.
- **CSV truncated-on-init.** `_profile_session_init` truncates each worker's CSV with a fresh
  header at conftest load, AND (on the master / single-process only) wipes every existing
  `*.csv` in the directory first. Two consecutive pytest runs do not accumulate, and a
  previous run with more workers (e.g. `-n 16`) does not bleed leftover CSVs into a smaller
  re-run (e.g. `-n 12`).

**Notable deviation from spec:** none functional. The plan's `_FIXTURE_PROFILE_PATH` placeholder
became `_FIXTURE_PROFILE_DIR` to accommodate per-worker files; the disambiguation is documented
inline. No other spec deviation.

**Verification (captured evidence):**

```text
SHEKEL_TEST_FIXTURE_PROFILE=1 pytest tests/test_models/ -n 0 -q:
  253 passed in 83.05s
  Fixture profile summary -- 253 tests across 1 worker(s): main
  | Step                            | Avg      | p50    | p95    | p99    | Max    | % of fixture |
  |---------------------------------|----------|--------|--------|--------|--------|--------------|
  | rollback                        | 0.0 ms   | 0.0    | 0.0    | 0.0    | 0.0    | 0.0 %        |
  | TRUNCATE main 28 tables CASCADE | 246.3 ms | 245.3  | 252.4  | 277.6  | 281.6  | 82.9 %       |
  | seed_ref re-insert              | 19.0 ms  | 19.1   | 20.6   | 21.3   | 22.3   | 6.4 %        |
  | commit_after_seed               | 4.8 ms   | 4.7    | 5.3    | 6.3    | 7.5    | 1.6 %        |
  | TRUNCATE system.audit_log       | 19.9 ms  | 19.8   | 20.8   | 22.8   | 23.1   | 6.7 %        |
  | refresh_ref_cache               | 7.1 ms   | 7.1    | 7.9    | 8.4    | 9.1    | 2.4 %        |
  | Fixture setup total             | 297.1 ms | 296.4  | 303.5  | 329.1  | 333.9  | 100.0 %      |
  | Test body (call)                | 28.2 ms  | 22.5   | 81.1   | 88.4   | 106.3  | --           |
  | Teardown                        | 0.1 ms   | 0.1    | 0.2    | 0.2    | 0.5    | --           |

Bare pytest tests/test_models/ -n 0 -q (no env var):
  253 passed in 83.07s -- delta vs flag-set: -0.02 % (well inside 2 % noise gate).

Reproducibility -- three consecutive flag-set runs:
  Run 1: Fixture total 296.2 ms, 253 passed in 82.71s.
  Run 2: Fixture total 298.0 ms, 253 passed in 83.29s.
  Run 3: Fixture total 297.1 ms, 253 passed in 83.05s.
  Per-step variation within ~1 ms across runs.

xdist sanity (pytest tests/test_models/ -q, flag set, default -n 12):
  253 passed in 15.11s.
  Aggregator listed 12 worker(s): gw0..gw11.  All 12 workers
  contributed; master correctly aggregated; no DB leaks.

Lint:
  pylint app/ --fail-on=E,F: 9.52/10 (unchanged).
  pylint tests/conftest.py errors+fatals only: 10.00/10.
  pylint tests/conftest.py full ruleset: 7.91 (up from 7.25
  pre-change; remaining warnings are pre-existing fixture-name
  shadowing and unused imports unrelated to this work).

Cleanup:
  psql -l on test cluster after every run shows only
  `shekel_test` (legacy untouched) and `shekel_test_template`.
  No per-session DB leaked.
```

**Why first:** every subsequent phase needs a reproducible measurement to confirm gain or detect
regression. Without this harness, the next sessions would be guessing. Reversible: a single revert
of the four-file diff removes the harness entirely.

**Original spec (kept for reference -- now historical):**

**Files:**

- `tests/conftest.py` -- new module-level constants
  `_FIXTURE_PROFILE_ENABLED = os.environ.get("SHEKEL_TEST_FIXTURE_PROFILE") == "1"`
  and `_FIXTURE_PROFILE_PATH = pathlib.Path(...)`. Inside the
  existing `db` fixture (lines 335-439), wrap each inner step
  (rollback / TRUNCATE / seed_ref / commit_after_seed / TRUNCATE
  audit_log / refresh_ref_cache / yield / teardown) with
  `time.perf_counter()` and append one CSV row per test when the
  flag is set. When the flag is off, the timer wrappers must be
  cleanly skipped so there is zero perf cost in the default
  configuration.
- `tests/conftest.py::pytest_sessionfinish` -- when the flag is set,
  read the CSV(s), compute per-step avg / p50 / p95 / p99 / max,
  print a summary table to stdout. Worker-aware so xdist runs
  aggregate across workers. When the flag is off, the existing
  per-session DB drop logic at lines 1368-1383 is unaffected.
- `pytest.ini` -- register a `slow_fixture` marker (optional, for
  the future when individual tests need to be excluded from the
  harness' average).
- `docs/audits/test_improvements/test-performance-research.md` --
  append a new section "Baseline as of <date>" with the captured
  numbers from this phase.

**Verification:**

```bash
SHEKEL_TEST_FIXTURE_PROFILE=1 pytest tests/test_models/ -n 0 -q
```

Must print a summary table matching the shape of `test-performance-research.md:64-77` (one row per
fixture inner step plus the total). Numbers may differ from the 2026-05-11 capture; what matters is
that the table is reproducible.

```bash
pytest tests/test_models/ -n 0 -q
```

Without the env var, must produce the same pass count and similar wall-clock as before the harness
landed (within ~2 % noise). Proves the harness is truly zero-cost when disabled.

### Phase 1 -- Cluster durability knobs + tmpfs + replication-role suppression -- **Complete (pending commit), flake resolved in Phase 1d**

Captures the cheap win available on PG 16 *today*, with no architectural change. Establishes
measurement discipline (compare against Phase 0 baseline) and de-risks the bigger Phase 3 rewrite.

The 2026-05-12 session landed Phase 1a + 1b + 1c + 1d. The per-test fixture floor dropped from
298 ms to 34.5 ms (-89 %, beat the projected 100-150 ms) and the full suite at the `-n 12` default
fell from ~4 min to ~52 s. Phase 1c surfaced a pre-existing test-isolation bug whose failure rate
went from low / unmeasured under the slow baseline to ~50 % under Phase 1a; Phase 1d root-caused
both failure shapes (rate-limit singleton leak from cleanup-not-in-try/finally, and a
`CREATE UNIQUE INDEX IF NOT EXISTS` cleanup that did not replace a malformed index left by a
sibling test under `--dist=loadgroup`'s per-test scheduling) and landed targeted fixes that
restored deterministic clean runs (10 / 10 verified post-fix). Decision on Phase 2 returns to the
developer with the flake closed.

#### Phase 1a -- Docker-compose: PG durability knobs + tmpfs -- **Implemented (pending commit)**

**What landed:** `docker-compose.dev.yml`'s `test-db` service gained a `tmpfs:` block mounting
`/var/lib/postgresql/data` in RAM with `rw,uid=70,gid=70,size=2g`, and the existing `command:` list
(six `-c` flags for timeouts) grew three additional `-c` flags: `fsync=off`,
`synchronous_commit=off`, `full_page_writes=off`. A new comment block above the tmpfs entry cites
the PG 16 manual chapter 30.4 "Non-Durable Settings" as the canonical authority, documents the uid
70 verification (`docker run --rm postgres:16-alpine id postgres` returns
`uid=70(postgres) gid=70(postgres)`), warns "DO NOT copy any of these settings to the ``db`` service
above," and explains the 2 GiB size cap as a guardrail not a tuning knob. The `db` service (dev
cluster) is untouched.

**Notable design choices:**

- **`size=2g` framed as a tripwire, not a budget.** A fresh `shekel_test_template` plus 12
  per-worker clones plus in-test audit-log growth currently sums to well under 1 GB; the cap is
  designed to fail loudly if some future test starts allocating unexpected scratch data. The
  inline comment ends "Hitting the cap is a signal of unexpected test-data growth, not a number
  to bump." Mid-suite `df` showed 680 MB / 2 GB used (33 %), of which 640 MB was pg_wal --
  WAL is the actual growth pressure, not the database files (39 MB total under `base/`).
- **Existing `tmpfs` syntax preferred over `mount: type=tmpfs`.** Compose's `tmpfs:` shorthand
  accepts the `uid`/`gid`/`size` options inline; the long-form `mount` syntax would have
  required restructuring the `volumes:` block. Effect on PG is identical (kernel tmpfs both
  ways).
- **Both `command:` flags AND tmpfs together.** The plan's "Why each knob" section recommends
  every cluster-side knob the PG manual lists for throwaway clusters. Empirically, tmpfs alone
  - `fsync=off` alone each contribute meaningfully and the combined effect is the deepest cut.
  The reordering of the existing `tcp_keepalives_*` block was avoided -- the new flags simply follow
  at the end so a future reader can `grep -A 1 "fsync=off"` and find the durability trio adjacent in
  order.

**Notable deviation from spec:** the plan claimed `test-db` "has no `volumes:` block today (data
already lives in the container's ephemeral writable layer at `docker-compose.dev.yml:56-92`)". The
current file does carry a `volumes:` block with the `init_db.sql` bind mount, and PGDATA lived in a
Docker-managed anonymous volume (declared by the postgres image's `VOLUME` directive) rather than
the container's overlay writable layer. The tmpfs change works identically in both framings --
docker prefers a `tmpfs` mount at the same destination over the implicit anonymous volume -- but the
plan's terminology was inaccurate. Verified post-restart with
`docker exec ... df -hT /var/lib/postgresql/data` showing `tmpfs 2.0G 45.6M Used 1.3G Available` and
`stat -f` reporting `fstype:tmpfs blocksize:4096 totalblocks:524288` (= 2 GiB exactly).

**Verification (captured evidence):**

```text
$ docker run --rm postgres:16-alpine id postgres
uid=70(postgres) gid=70(postgres) groups=70(postgres),70(postgres)

$ docker compose -f docker-compose.dev.yml up -d test-db && docker exec shekel-dev-test-db \
    sh -c "df -hT /var/lib/postgresql/data && stat -f -c 'fstype:%T blocksize:%s totalblocks:%b' /var/lib/postgresql/data"
Filesystem  Type   Size  Used Available Use% Mounted on
tmpfs       tmpfs  2.0G  45.6M     2.0G   2% /var/lib/postgresql/data
fstype:tmpfs blocksize:4096 totalblocks:524288        # 524288 * 4096 = 2 GiB exactly

$ psql -h localhost -p 5433 -U shekel_user -d postgres -tA -c \
    "SELECT name || ' = ' || setting FROM pg_settings
     WHERE name IN ('fsync','synchronous_commit','full_page_writes',
                    'idle_in_transaction_session_timeout','lock_timeout','statement_timeout')
     ORDER BY name;"
fsync = off
full_page_writes = off
idle_in_transaction_session_timeout = 30000
lock_timeout = 10000
statement_timeout = 30000
synchronous_commit = off

$ python scripts/build_test_template.py
  Step 1/3: dropped and recreated empty database.
  Step 2/3: migrated to head, applied audit, seeded reference data.
  Step 3/3: verified (18 account types, 31 audit triggers, 0 audit_log rows).
DONE: shekel_test_template ready.

$ pytest tests/test_models/test_computed_properties.py -n 0 -q   # smoke
32 passed in 2.00s
```

**Why first:** every other phase depends on the test cluster being fast enough that the conftest
changes can be measured cleanly. Phase 1a is reversible by deleting the `tmpfs:` block and the three
new `-c` flags; restart the container and rebuild the template.

#### Phase 1b -- Conftest: suppress audit triggers during seed -- **Implemented (pending commit)**

**What landed:** the per-test `db` fixture (still in `tests/conftest.py`, the function around line
557) now does two things differently from Phase 0:

1. Immediately before `_seed_ref_tables()` runs, the fixture executes
    `SET LOCAL session_replication_role = 'replica'` against the open session. Under
    `replica` mode PostgreSQL skips every trigger that does NOT carry the explicit
    `ENABLE REPLICA TRIGGER` clause; the project's audit triggers in
    `app/audit_infrastructure.py` (lines 292-298) use default enablement (verified by
    `grep -rn 'ENABLE REPLICA TRIGGER|ENABLE ALWAYS' app/ scripts/ migrations/ tests/`
    returning zero matches), so all 18 audit-trigger fires on the seed inserts are
    suppressed. `LOCAL` scopes the SET to the current transaction; the
    `setup_commit_after_seed` step's `db.session.commit()` drops the setting so the test
    body runs with the default `origin` role and audit triggers fire normally on every
    test-body write (the contract every audit-asserting test depends on).
2. `system.audit_log` is now part of the existing `TRUNCATE TABLE ... CASCADE` statement
    in `setup_truncate_main` (now 29 tables instead of 28). The separate
    `setup_truncate_audit_log` block -- one TRUNCATE plus its own commit -- is gone.
    The Phase 0 harness's `_FIXTURE_PROFILE_STEPS` / `_FIXTURE_PROFILE_LABELS` were
    updated correspondingly so the summary table no longer reports the removed step.

**Notable design choices:**

- **One TRUNCATE statement, not two.**  Folding `system.audit_log` into the existing main
  TRUNCATE saves the second commit roundtrip the spec's intent called for (the savings the
  plan attributed to "removing the truncate entirely"). Functionally `TRUNCATE` does not
  fire row-level triggers, so adding `system.audit_log` to the list does not recurse and
  does not interact with the audit-trigger machinery.
- **Step retained in the harness as `setup_truncate_main` (now "29 tables").**  The label
  changed from "TRUNCATE main 28 tables CASCADE" to "TRUNCATE main 29 tables CASCADE" to
  keep cell-for-cell comparability with the published Phase 0 baseline. The
  `setup_truncate_audit_log` row is gone from the summary table because the work no longer
  exists as a separate step.
- **`SET LOCAL` not `SET`.**  Session-scoped `SET session_replication_role = 'replica'`
  would persist across commits and silently break any subsequent test that wrote to an
  audited table. `LOCAL` is the only safe form here; the existing transaction boundary
  at `setup_commit_after_seed` already disposes of it correctly.
- **Inline comments document the why, not the what.**  The new prose in `setup_seed_ref`
  and `setup_truncate_main` explains the trigger-suppression mechanism, the ordering
  rationale (under Phase 1b the seed no longer writes audit rows, so truncating audit_log
  BEFORE the seed is correct -- which was NOT true under the old model), and the
  cross-test isolation guarantee.

**Notable deviation from spec:** the spec's Phase 1b instruction (3) called for **removing**
`TRUNCATE system.audit_log` entirely on the rationale that "nothing has written to audit_log between
the cloned-from-template start state and the now-suppressed seed, so the truncate is a no-op." This
rationale is **incorrect** -- it only accounts for the FIRST test in a worker session. After Test
N's body has fired audit triggers, `system.audit_log` carries the rows it wrote; without a TRUNCATE
between tests those rows leak into Test N+1 and break every assertion of the form
`len(_get_audit_rows("table_x", "INSERT")) == 1`. Verified empirically: with the spec's literal
Phase 1b applied,
`pytest tests/test_integration/test_audit_triggers.py tests/test_scripts/test_audit_cleanup.py`
failed 9 of 36 tests with assertions like `assert 393 == 1` (audit_log carrying 393 leaked rows by
mid-suite). The corrected Phase 1b folds the truncate into the existing main statement so the win
the spec was after (-25 ms = the 20 ms standalone TRUNCATE + 5 ms suppressed audit fires during
seed) is captured AND the per-test isolation contract is preserved. Both audit-asserting test files
pass 36/36 after the corrected change.

**Verification (captured evidence):**

```text
$ pytest tests/test_integration/test_audit_triggers.py \
         tests/test_scripts/test_audit_cleanup.py -n 0 -q
36 passed in 1.99s

$ SHEKEL_TEST_FIXTURE_PROFILE=1 pytest tests/test_models/ -n 0 -q
Fixture profile summary -- 253 tests across 1 worker(s): main
| Step                               | Avg      | p50    | p95    | p99    | Max    | % of fixture |
|------------------------------------|----------|--------|--------|--------|--------|--------------|
| rollback                           |  0.0 ms  |  0.0   |  0.0   |  0.0   |  0.0   |  0.0 %       |
| TRUNCATE main 29 tables CASCADE    | 15.2 ms  | 15.1   | 16.0   | 16.2   | 16.7   | 44.0 %       |
| seed_ref re-insert (replica role)  | 13.1 ms  | 12.9   | 14.5   | 15.2   | 15.7   | 37.9 %       |
| commit_after_seed                  |  0.8 ms  |  0.8   |  0.9   |  1.0   |  1.3   |  2.3 %       |
| refresh_ref_cache                  |  5.4 ms  |  5.4   |  6.2   |  6.6   |  7.0   | 15.8 %       |
| Fixture setup total                | 34.5 ms  | 34.4   | 36.7   | 37.4   | 37.9   | 100.0 %      |
| Test body (call)                   | 17.2 ms  | 14.8   | 41.8   | 53.5   | 66.5   | --           |
| Teardown                           |  0.1 ms  |  0.1   |  0.1   |  0.2   |  0.3   | --           |
253 passed in 13.53s

$ grep -rn 'ENABLE REPLICA TRIGGER\|ENABLE ALWAYS\|REPLICA TRIGGER' app/ scripts/ migrations/ tests/
(no matches -- confirms session_replication_role='replica' suppresses every audit trigger)
```

**Why second:** depends on Phase 1a's faster cluster but the conftest change is conceptually
separate. Lands on its own commit; reversibility is the same diff in reverse.

#### Phase 1c -- Measurement gate -- **Implemented (pending commit) -- flake surfaced, resolved in Phase 1d**

**What landed:** the Phase 0 harness was re-run in both single-process and xdist (`-n 12`) modes
against the same 253-test slice (`tests/test_models/`) and the full 5,276-test suite was run at the
default `-n 12` to compare against the Phase 0 baseline. Results are dramatic on both axes but the
parallel full-suite run is intermittently flaky in a way that the slow baseline masked.

**Single-process measurements (`-n 0`, 253 tests, fresh template):**

| Step | Phase 0 baseline | Phase 1 measured | Delta |
|---|---|---|---|
| rollback                          |   0.0 ms |   0.0 ms |    -- |
| TRUNCATE main 28 -> 29 tables CASCADE | 247.1 ms |  15.2 ms | -94 % |
| seed_ref re-insert (replica role) |  19.0 ms |  13.1 ms | -31 % |
| commit_after_seed                 |   4.8 ms |   0.8 ms | -83 % |
| TRUNCATE system.audit_log (removed) | 20.0 ms |     -- | gone |
| refresh_ref_cache                 |   7.1 ms |   5.4 ms | -24 % |
| **Fixture setup total**           | **298.0 ms** | **34.5 ms** | **-89 %** |
| Test body (call)                  |  28.2 ms |  17.2 ms | -39 % |
| Teardown                          |   0.1 ms |   0.1 ms | flat |
| Wall-clock                        |  83.05 s |  13.53 s | -84 % |

The biggest single contributor is tmpfs-backed PGDATA + `fsync=off` eliminating the disk
synchronisation barrier on `TRUNCATE`'s catalog rewrite -- the operation's lock acquisition cost is
unchanged but every write to disk metadata is now in-RAM. Removing the standalone audit_log TRUNCATE
adds another `~20 ms / test * 253 tests = ~5 sec` of wall-clock savings.

**xdist measurements (`-n 12`, 253 tests):**

| Step | Phase 0 baseline (proj.) | Phase 1 measured |
|---|---|---|
| TRUNCATE main 29 tables CASCADE   | ~35 ms (1/7 of `-n 0` w/scale eff.) | 19.6 ms |
| seed_ref re-insert (replica role) | ~3 ms (1/7 of `-n 0`)               | 24.9 ms |
| commit_after_seed                 | ~1 ms                                |  1.1 ms |
| refresh_ref_cache                 | ~1 ms                                |  7.9 ms |
| **Fixture setup total**           | ~40 ms (proj.)                       | **53.4 ms** |
| Wall-clock                        | ~15.11 s (Phase 0 capture)          | 3.54 s  |

The interesting effect under xdist is that `seed_ref` becomes the dominant step (24.9 ms, 46.6 % of
the fixture). This is the cluster-wide WAL/fsync pipeline serialisation effect identified in
`test-performance-research.md` section 3.3: 12 workers all writing to `ref.account_types`
simultaneously contend on the WAL writer. `fsync=off` removes the disk barrier but the in-memory WAL
queue itself is still single-threaded. Phase 3's per-test template clone (which would not call
`seed_ref` at all -- the template already carries the seed) is the architectural answer; Phase 1
cannot reduce this further.

**Full-suite measurement (default `-n 12`):**

- Phase 0 baseline: ~4 min wall-clock, 5,148 passed (Phase 0 capture date).
- Phase 1 measurement: 51-52 s wall-clock typical, 5,276 tests collected.

**Pass-fail status:** the parallel full suite is intermittently flaky under Phase 1a. Captured
across multiple consecutive runs against my fully-applied Phase 1 (Phase 1a + Phase 1b):

| Run | Result |
|---|---|
| 1 | 5275 passed, 1 failed (`test_carry_forward_only_moves_transactions_for_specified_scenario`) |
| 2 | 5133 passed, 1 failed, 142 errors (XSS-prevention + scenario tests; rate-limit cluster) |
| 3 | 5276 passed |
| 4 | 5132 passed, 1 failed, 143 errors (same cluster as run 2) |
| 5 | 5276 passed |
| 6 | 5275 passed, 1 failed (`test_non_baseline_scenarios_allowed_alongside_baseline`) |
| 7 | 5276 passed |
| 8 | 5274 passed, 2 failed (`test_carry_forward_only_moves_*` + `test_filters_by_scenario`) |

~50-70 % of consecutive full-suite runs surface a failure or error cluster. Two failure shapes
recur:

1. **HTTP 429 on `auth_client` logins** -- the `auth_client` fixture's
   `assert resp.status_code == 302` fires `AssertionError: auth_client login failed with
   status 429` for ~140 downstream tests in `test_routes/test_xss_prevention.py`,
   `test_routes/test_grid.py`, `test_routes/test_retirement.py`, etc. Root cause:
   `tests/test_routes/test_errors.py::TestErrorPages::test_429_*` enables rate-limiting on a
   side-app (`limiter.enabled = True; limiter.init_app(rate_app)`), exhausts the per-IP
   5-per-15-minute login quota, then cleans up with `limiter.reset()` plus
   `limiter.enabled = False`. Under the slow baseline the 15-minute window (or the next
   test's 200 ms+ fixture cycle) expires before subsequent tests in the same worker run
   `auth_client.post("/login", ...)`. Under Phase 1a the per-test cycle is 4 x faster and
   successive auth_client logins fall inside the rate-limit window; the
   `limiter.reset()` call appears not to clear the global limiter's storage state for the
   session-scoped `app` fixture (only for the side `rate_app`).
2. **`UniqueViolation` on `uq_scenarios_one_baseline` during bulk INSERT of `is_baseline=False`
   rows** -- `test_non_baseline_scenarios_allowed_alongside_baseline` (and analogous
   carry-forward / loan-payment scenario tests) attempt to bulk-insert 3 rows where every
   row has `is_baseline = False`, and the partial unique index
   `uq_scenarios_one_baseline ON budget.scenarios (user_id) WHERE is_baseline = true` raises
   "Key (user_id)=(N) already exists" anyway. The index definition is correct (verified by
   `\d budget.scenarios`); the only row that should be in the index is the existing
   `seed_user` baseline (is_baseline=true, same user_id). The new rows should be filtered
   out of the index by the `WHERE` clause. Hypothesis: SQLAlchemy 2.0.49's
   `_exec_insertmany_context` plus `INSERT ... SELECT FROM VALUES ... ORDER BY sen_counter
   RETURNING ...` under `fsync=off` + WAL pressure occasionally violates the partial-index
   visibility guarantee. Not reproduced in isolation (single-process and single-file
   xdist both pass); only reproduces in full-suite parallel runs. Cause unconfirmed.

**Both failure modes are absent under baseline `docker-compose.dev.yml` with the same Phase 1b
conftest.** Verified by stashing Phase 1a, restarting test-db, rebuilding the template, and running
the full suite twice -- both runs returned `5276 passed, 3 warnings in ~253 s (4:13)`. The failures
only emerge with Phase 1a's tmpfs + durability knobs in place.

**Recommendation for the developer (at end of 2026-05-12 first session):** Phase 1 delivers the
projected speedup, but the parallel-suite gate is intermittently flaky. Three response options
were presented:

- (a) **Accept the flake, re-run as needed.**  The win (4 min -> 52 s) is large; a failed
  full-suite run costs another 52 s to re-run. Total expected cost across 10 runs:
  ~10 min instead of ~40 min on baseline. Document the rate-limit-isolation bug as a known
  issue with a recommended fix.
- (b) **Phase 1d cleanup before declaring Phase 1 done.**  Add `limiter.reset()` to the
  per-test `db` fixture teardown (clears the rate-limit state-leak from `test_errors`'s
  `test_429_*` tests). Investigate the `uq_scenarios_one_baseline` bulk-insert mystery
  (may be a SQLAlchemy 2.0 + PG fsync=off interaction); if it cannot be root-caused,
  document it and accept the residual.
- (c) **Revert Phase 1a until the rate-limit and scenario isolation bugs are fixed.**  Lose
  the speedup but keep the deterministic suite. Phase 1b alone delivers ~25 ms / test
  (~2 % of the Phase 0 floor) and is keepable in isolation.

**Outcome:** option (b) was selected, but the eventual Phase 1d root cause was different from
what option (b) anticipated -- the rate-limit bug turned out to be a missing `try`/`finally`
combined with a timing-fragile `Retry-After == "900"` assertion, NOT a state-leak that a
defensive `limiter.reset()` in the conftest teardown would clean up.  The scenario-uniqueness
bug turned out to be NOT a SQLAlchemy / PG interaction at all but a test-only fixture cleanup
that used `CREATE UNIQUE INDEX IF NOT EXISTS` against an already-present malformed index.  The
fixes are targeted to three test files and are described in the Phase 1d retrospective below.

**Notable design choices:**

- **Three full-suite re-runs from scratch chosen as the smallest characterisation sample.**
  Single runs would not distinguish a 50 % flake from a deterministic failure; running ten
  times is wasteful when three already show the flake pattern. Eight runs across the
  session establish the ~50-70 % rate adequately.
- **Baseline re-verification via stash + rebuild + run.** The cheapest way to test "is the
  flake Phase 1a-induced vs pre-existing" was to stash docker-compose.dev.yml (keep
  conftest), restart test-db (no tmpfs, no fsync=off), rebuild template, and run the full
  suite twice. Two runs were enough to demonstrate the bug does not reproduce on the slow
  baseline; a third run would have been a 4-minute investment for diminishing certainty.
- **Single-process measurement first, parallel second.** The single-process harness is
  deterministic by construction (no cluster-wide contention); confirming the dominant
  measured wins (-89 % fixture floor, -84 % wall-clock) before opening the parallel
  flake discussion keeps the two stories from blurring.
- **The xdist seed_ref step climbing from 13 ms to 25 ms is intentional.** WAL serialisation
  was the predicted Phase 1 ceiling per the research doc section 3.3; the measurement
  confirms it. Phase 3 is the architectural answer.

**Notable deviation from spec:** the Phase 1c verification step in the plan said "Full suite at the
default `-n 12`. Expected: ~1.5-2 min wall-clock, 5,148 passed, no audit-log regressions." The
wall-clock came in well under the projection (~52 s, not ~90-120 s) and the pass count matches the
current test inventory (5,276 vs the plan's stale 5,148 -- 128 new tests have landed since the plan
was written), AND the parallel suite was not yet deterministic at end of Phase 1c. All three are
now wins: Phase 1d landed targeted test-fixture fixes that restored deterministic clean runs
(10 / 10 verified) without touching the cluster knobs or `app/` code.

**Verification (captured evidence):**

See the tables and per-failure breakdown in the "What landed" section above; the relevant commands
are reproducible at any time by:

```text
$ TEST_ADMIN_DATABASE_URL='postgresql://shekel_user:shekel_pass@localhost:5433/postgres' \
  SHEKEL_TEST_FIXTURE_PROFILE=1 \
  pytest tests/test_models/ -n 0 -q
$ TEST_ADMIN_DATABASE_URL='postgresql://shekel_user:shekel_pass@localhost:5433/postgres' \
  SHEKEL_TEST_FIXTURE_PROFILE=1 \
  pytest tests/test_models/ -q                   # xdist default
$ TEST_ADMIN_DATABASE_URL='postgresql://shekel_user:shekel_pass@localhost:5433/postgres' \
  pytest --tb=line                                # full suite
$ pylint app/ --fail-on=E,F                       # unchanged: 9.52/10
```

**Why third:** measurement-only sub-phase. Does not modify any production-touching code.
Reversibility is trivial: revert Phase 1a and / or Phase 1b independently.

#### Phase 1d -- Flake resolution -- **Implemented (pending commit)**

**What landed:** root-caused and fixed both failure shapes Phase 1c surfaced. Three test-only
files modified; no `app/` changes; pylint score unchanged at 9.52/10.

The flake's full evidence chain is in
[`phase1-flake-investigation.md`](phase1-flake-investigation.md) -- the document records the
investigator's independent hypothesis space, the captured failing-DB snapshots, a deterministic
two-test reproducer for shape #2, the load-bearing `--dist=loadfile` counter-experiment that
ruled out cluster-level causes, side-by-side comparison with the prior session's hypotheses
(several of which were refuted by direct evidence), and the recommended fix path that this
phase implemented.

**Root cause -- shape #1 (HTTP 429 cluster):** five rate-limit tests in
`tests/test_routes/test_errors.py` and `tests/test_routes/test_auth.py` had cleanup blocks
outside any `try`/`finally`:

- `test_routes/test_errors.py:30` `test_429_renders_custom_page`
- `test_routes/test_errors.py:70` `test_429_includes_retry_after_header`
- `test_routes/test_auth.py:155` `test_rate_limiting_after_5_attempts`
- `test_routes/test_auth.py:2028` `test_register_post_rate_limited`
- `test_routes/test_auth.py:2105` `test_mfa_verify_rate_limiting`

When any assertion before the cleanup raised, the Limiter singleton was left with
`limiter.enabled = True` and a populated rate-limit bucket on `127.0.0.1` (the only IP the
Werkzeug test client uses). The session-scoped `app` is bound to the same singleton, so every
subsequent `auth_client.post("/login", ...)` on the same xdist worker returned 429 and the
fixture's `assert resp.status_code == 302` fired. The triggering assertion was
`test_429_includes_retry_after_header`'s `assert response.headers["Retry-After"] == "900"` --
Flask-Limiter computes the header as `int(reset_at - time.time())` and under `-n 12` WAL
contention the per-request latency occasionally stretches enough that the integer rounds to
899.  Confirmed by captured snapshot: `AssertionError: assert '899' == '900'` on gw3,
followed by 306 downstream tests on the same worker observing `limiter.enabled = True` at
fixture entry.

**Root cause -- shape #2 (`uq_scenarios_one_baseline` UniqueViolation):**
`tests/test_models/test_c41_baseline_unique_migration.py::TestAssertIndexShapeRejectsMalformedIndex::test_assert_index_shape_raises_on_non_partial_index`
intentionally creates a malformed (non-partial) version of `uq_scenarios_one_baseline` inside
its body to verify the migration's `_assert_index_shape` helper rejects it.  The
`restore_baseline_index` cleanup fixture used `CREATE UNIQUE INDEX IF NOT EXISTS` -- which is
a no-op when an index with that name already exists, REGARDLESS of whether the existing
index's WHERE clause matches the canonical partial shape.  PostgreSQL does not compare
definitions; it only checks the name.  The malformed full unique index from the test body
persisted across the cleanup.  Under `pytest-xdist`'s `--dist=loadgroup` scheduling
(verified by reading `.venv/lib/python3.14/site-packages/xdist/scheduler/loadgroup.py:7-66`),
tests without an `xdist_group` marker are distributed INDIVIDUALLY across workers, so test 14
(creates malformed) and test 15 (would restore canonical via its `try`/`finally`) can land on
different workers.  The worker that ran test 14 retained the malformed index for the rest of
its session.  Per-test `TRUNCATE` does not drop indexes, only rows -- the malformed shape
survived every subsequent test on that worker.  Confirmed by captured snapshot:
`INDEX DEF: [('CREATE UNIQUE INDEX uq_scenarios_one_baseline ON budget.scenarios USING btree
(user_id)',)]` (no WHERE clause) in three failing per-session DBs.

**Why Phase 1a exposed the bugs:** both are pre-existing test-isolation defects.  Phase 1a's
faster per-test cycle changed the pytest-xdist scheduling timing landscape (workers finish
tests in ~35 ms instead of ~300 ms, so the ungrouped tests spread across more workers and the
malformed-index leak is more likely to land on a worker that also runs a vulnerable scenario
test downstream).  Under baseline the same bugs are present but the shape #2 trigger lands on
fewer workers per run (prior session's 2 baseline runs are consistent with a low but nonzero
baseline rate; not enough samples to characterise definitively).  Phase 1b's audit-log
folding and replication-role suppression are unrelated to either failure shape -- verified by
the loadfile counter-experiment which kept Phase 1b in place and still produced 4 / 4 clean
full-suite runs.

**Fix #1 -- `tests/test_models/test_c41_baseline_unique_migration.py`:** the
`restore_baseline_index` cleanup fixture and the `_recreate_canonical_index` helper both
changed from `CREATE UNIQUE INDEX IF NOT EXISTS` to `DROP INDEX IF EXISTS` followed by
`CREATE UNIQUE INDEX`.  The DROP unconditionally removes any same-named index (malformed or
canonical) before the CREATE rebuilds the canonical partial shape.  Both edits carry an
expanded docstring referencing this investigation so a future reader knows why the
drop-then-create idiom is load-bearing rather than redundant.

**Fix #2 -- `tests/test_routes/test_errors.py`:** the two cleanup-less 429 tests gained a
`try`/`finally` around the cleanup block so the limiter is reset and disabled even when an
assertion fails.  `test_429_includes_retry_after_header` additionally had its `Retry-After`
assertion relaxed from `== "900"` to `895 <= int(retry_after) <= 900` -- a deliberate test
behaviour change confirmed by the developer (per Rule 5 of `CLAUDE.md`).  The 5-second
tolerance absorbs Flask-Limiter's integer-rounding window under WAL contention while still
firing on a real regression (e.g. the limit downgraded to a few seconds).  Both tests carry
an expanded docstring naming the `try`/`finally` guard's purpose.

**Fix #3 -- `tests/test_routes/test_auth.py`:** the three cleanup-less rate-limit tests
(`test_rate_limiting_after_5_attempts`, `test_register_post_rate_limited`,
`test_mfa_verify_rate_limiting`) gained `try`/`finally` blocks mirroring the
`test_routes/test_errors.py` shape.  Each test's docstring now names the guard's purpose.

**Verification:**

- 10 / 10 consecutive full-suite runs at `-n 12 --dist=loadgroup` on the Phase 1a cluster:
  `5276 passed, 3 warnings` each, wall-clock 52-53 s (no perf regression).
- Deterministic two-test reproducer (`test_assert_index_shape_raises_on_non_partial_index`
  immediately followed by `test_carry_forward_only_moves_transactions_for_specified_scenario`
  under `-n 0 -p no:randomly`): 1 fail before Phase 1d, 2 pass after.
- The same chained run extended to all four vulnerable downstream tests
  (`test_carry_forward_only_moves_*`, `test_filters_by_scenario`,
  `test_non_baseline_scenarios_allowed_alongside_baseline`, plus the malformed-index test
  itself): all pass after fix.
- All 21 tests in the three touched files pass when run together at `-n 0 -p no:randomly`.
- `pylint app/ --fail-on=E,F`: 9.52/10, identical to pre-Phase-1d.

**Notable design choices:**

- **Targeted test-fixture fix, not a `app/` defensive add.**  An optional defensive
  `limiter.reset()` + `limiter.enabled = False` in the per-test `db` fixture teardown was
  considered (and outlined in the investigation doc as a belt-and-braces option).  Skipped
  in this phase because the per-test `try`/`finally` already fixes the bug at its source; a
  conftest-level reset would add work to every test in the suite to defend against future
  tests that forget the same pattern.  Can be added later if a pattern of forgotten
  `try`/`finally` blocks emerges.
- **The malformed-index test was NOT relocated to a side-DB.**  A cleaner architectural
  refactor would create a throwaway database for the shape-check test, install the
  malformed index there, and discard the side-DB rather than mutating the per-test DB in
  place.  Out of scope for the targeted fix; recorded as a future improvement in
  `phase1-flake-investigation.md` "Open questions" section.
- **`--dist=loadgroup` retained as the project default.**  The loadfile counter-experiment
  demonstrated 0 % flake at `--dist=loadfile` AND ~12 % faster wall-clock per run (47 s vs
  53 s in my measurement; smaller fixture overhead because each worker reuses ref_cache
  state across adjacent tests in the same file).  Switching the default may be worth a
  separate evaluation but is independent of the Phase 1d fix -- the targeted fix removes
  the bug regardless of scheduler choice.  Switching now would conflate "the flake is
  fixed" with "the scheduler is different" in the post-Phase-1 measurement story.
- **Diagnostic instrumentation reverted before commit.**  The investigation used a
  `SHEKEL_KEEP_FAILED_DB=1`-gated snapshot hook in `conftest.py` to capture failing per-
  session DB state.  All diagnostic code was removed at the end of the investigation
  session; `git diff tests/conftest.py` shows only the pre-existing Phase 1b changes.

**Why fourth:** flake resolution must follow the surfaced report (Phase 1c).  Phase 1d's fix
is small, targeted, and reversible (revert the three test files); the alternative was to
either accept the flake or revert Phase 1a entirely, both of which were worse outcomes
relative to a ~one-session investigation that pinned the cause to test-isolation defects
that pre-date Phase 1.

**Reversibility:** revert the three test-file diffs.  The Phase 1a / Phase 1b changes are
independent and can stay if Phase 1d itself ever needed to be unwound.

**Original spec (kept for reference -- now historical):**

#### Phase 1a -- Docker-compose: PG durability knobs + tmpfs

**Files:**

- `docker-compose.dev.yml:56-92` -- the `test-db` service.

**Changes:**

1. Append the following to the existing `command:` list (the list
   currently ends at line 87 with the tcp_keepalives flags):

   ```yaml
       - -c
       - fsync=off
       - -c
       - synchronous_commit=off
       - -c
       - full_page_writes=off
   ```

2. Add a `tmpfs:` block mounting the PG data directory in RAM:

   ```yaml
       tmpfs:
         - /var/lib/postgresql/data:rw,uid=70,gid=70,size=2g
   ```

   The `test-db` service has no `volumes:` block today (data
   already lives in the container's ephemeral writable layer at
   `docker-compose.dev.yml:56-92`), so this is a strict upgrade --
   the bytes go to RAM instead of overlay-on-SSD. The 2 g size
   cap protects the host from a runaway test creating too much
   data. uid 70 is the postgres user in the alpine image; verify
   with `docker run --rm postgres:16-alpine id postgres`.
3. Add a comment block immediately above the new flags citing PG
   manual ch. 30.4 "Non-Durable Settings"
   (<https://www.postgresql.org/docs/16/non-durability.html>) as the
   canonical authority for throwaway test clusters.

**Why each knob:**

- `fsync = off` -- skips the per-commit disk barrier. Vinta
  Software reports ~10x speedup on Django+Postgres test suites
  with this single flag. Safe because the cluster is rebuilt by
  `scripts/build_test_template.py` on demand.
- `synchronous_commit = off` -- commits return as soon as the WAL
  is in memory. Mateus Rauli's TPS bench reports ~3.5 % gain
  alone, ~10.7 % combined with `fsync = off`.
- `full_page_writes = off` -- skips full-page WAL after each
  checkpoint. Smallest individual win for a TRUNCATE-heavy
  workload but free to combine.
- tmpfs on the data directory -- eliminates all disk I/O. PG
  manual lists this as the *first* non-durability recommendation.

**Verification:**

```bash
docker compose -f docker-compose.dev.yml up -d test-db
python scripts/build_test_template.py
SHEKEL_TEST_FIXTURE_PROFILE=1 pytest tests/test_models/ -n 0 -q
```

Per-test fixture floor must drop materially below the Phase 0 baseline. Projection: ~281 ms ->
~100-150 ms.

**Reversibility:** delete the `tmpfs:` block and the new `-c` flags; restart the test-db container;
rebuild the template.

#### Phase 1b -- Conftest: suppress audit triggers during seed

**Files:**

- `tests/conftest.py:397-414` -- inside the `db` fixture, between
  the TRUNCATE block and the system.audit_log truncate.

**Changes:**

1. Wrap the `_seed_ref_tables()` call at line 402 with
   `SET LOCAL session_replication_role = 'replica'` immediately
   before and rely on the transaction commit at line 403 to drop
   the SETting. The 18 audit-trigger fires from the seed inserts
   are suppressed.
2. Remove the `_db.session.execute(_db.text("TRUNCATE
   system.audit_log"))` call at line 413 plus its surrounding
   comment block at lines 405-412 -- nothing has written to
   audit_log between the cloned-from-template start state and the
   now-suppressed seed, so the truncate is a no-op.

**Why this is safe:**

- The session_replication_role mechanism is documented behavior
  (Eric Radman's
  <https://eradman.com/posts/database-test-isolation.html>
  references it for exactly this scenario). Triggers default to
  firing under "origin" replication role; under "replica" they
  are skipped unless explicitly declared `ENABLE REPLICA TRIGGER`.
  Shekel's audit triggers at `app/audit_infrastructure.py:296`
  use default enablement.
- The audit_log involved is the per-pytest-session DB's local
  `system.audit_log` (cloned from `shekel_test_template` empty,
  guaranteed by `scripts/build_test_template.py:233`).
  Production audit log is unaffected.
- Tests that assert on `system.audit_log` state
  (`tests/test_integration/test_audit_triggers.py`,
  `tests/test_scripts/test_audit_cleanup.py`) all start from an
  empty log and only assert on rows they themselves wrote in the
  test body. Phase 1b preserves that invariant.

**Verification:**

```bash
pytest tests/test_integration/test_audit_triggers.py tests/test_scripts/test_audit_cleanup.py -v
```

All audit-log assertions must continue to pass.

```bash
SHEKEL_TEST_FIXTURE_PROFILE=1 pytest tests/test_models/ -n 0 -q
```

Per-test fixture floor drops further: ~25 ms saved (the 20 ms audit_log TRUNCATE + ~5 ms suppressed
trigger execution during seed).

**Reversibility:** revert the conftest diff.

#### Phase 1c -- Measurement gate

**Goal:** confirm Phase 1 delivered the projection. Decide whether Phase 2/3 are worth pursuing.

**Verification:**

```bash
pytest --tb=short
```

Full suite at the default `-n 12`. Expected: ~1.5-2 min wall-clock, 5,148 passed, no audit-log
regressions.

**Decision gate:** if Phase 1 delivers the projection AND the developer is satisfied with ~2 min at
the current test count (well under 10-min CI timeout, comfortable for an iterative dev loop), Phases
2/3 become optional and can be deferred indefinitely. Update the status table at the top of this
document with the measured numbers and the decision. If the developer wants the suite to stay <1 min
as it grows past 10,000 tests, proceed to Phase 2.

**Why second:** maximises the win available without a PG upgrade. If anything goes wrong, the worst
case is reverting one docker-compose edit and one conftest edit.

### Phase 2 -- PostgreSQL 16 -> 18 upgrade (test + dev + CI + prod) -- **Implemented in part (2a test-db slice + 2b + 2e); 2c + 2d PROPOSED**

Preserves test-prod parity by upgrading all four clusters together. Unlocks Phase 3's
`file_copy_method = clone`.

The 2026-05-12 second session landed Phase 2a's test-db slice + Phase 2b + Phase 2e. The dev `db`
service stays on PG 16 until Phase 2c's dump/restore migration runs; the production cluster waits
for Phase 2d's planned downtime window. Phase 2e's measurement gate confirmed the PG 18 image
delivers parity with PG 16 (fixture total +0.9 % at `-n 12`, full suite 52-53 s deterministically
clean across 3 / 3 consecutive runs) -- as the plan predicted, PG 18 alone does not materially
speed up TRUNCATE; the architectural win is Phase 3's reflink-backed cloning.

**A non-spec gap that surfaced during Phase 2a and propagates through 2c and 2d:** PG 18's
docker-library image (PR docker-library/postgres#1259) switched the default `PGDATA` from the
legacy `/var/lib/postgresql/data` path to the pg_ctlcluster layout
`/var/lib/postgresql/$PG_MAJOR/docker` (verified: `docker run --rm postgres:18-alpine env | grep
PGDATA` returns `PGDATA=/var/lib/postgresql/18/docker`). The image's entrypoint refuses to start
when a mount sits at the legacy path. Phase 2a's tmpfs mount target moved from
`/var/lib/postgresql/data` to the parent `/var/lib/postgresql` so PG 18 places its per-major-version
subdir inside the tmpfs without the guard rail firing; Phase 2c and Phase 2d must apply the same
edit to their respective volume mount targets in `docker-compose.dev.yml` (`db` service) and
`docker-compose.yml` (production `db` service). The plan's original Phase 2a/2c/2d specs do not
call this out -- the plan was written before PG 18.3 / docker-library/postgres#1259 became the
default. Mounting at the parent is forward-compatible across PG 19 / PG 20 image bumps without
another mount-path edit.

> **Phase 1 flake -- resolved in Phase 1d (2026-05-12).**
>
> Phase 1c's full-suite verification at `-n 12` was intermittently flaky at ~50 % per consecutive
> run.  Both failure shapes were root-caused and fixed in Phase 1d:
>
> 1. **HTTP 429 cluster** -- caused by five rate-limit tests in `test_errors.py` and
>    `test_auth.py` whose cleanup ran outside any `try`/`finally`; a timing-fragile
>    `Retry-After == "900"` assertion in `test_429_includes_retry_after_header` failed under
>    `-n 12` WAL contention, skipping the cleanup and leaving the Limiter singleton in
>    `enabled = True` with a populated bucket.  Fix: wrap cleanup in `try`/`finally`; relax
>    the assertion to `895 <= retry_after <= 900`.
> 2. **`uq_scenarios_one_baseline` UniqueViolation** -- caused by
>    `tests/test_models/test_c41_baseline_unique_migration.py::TestAssertIndexShapeRejectsMalformedIndex::test_assert_index_shape_raises_on_non_partial_index`
>    creating a malformed (non-partial) unique index inside its body, combined with a
>    cleanup fixture that used `CREATE UNIQUE INDEX IF NOT EXISTS` (PostgreSQL only
>    checks the name on `IF NOT EXISTS`, not the definition).  Under `--dist=loadgroup`'s
>    per-test scheduling, the malformed shape leaked across workers.  Fix: change the
>    cleanup to `DROP INDEX IF EXISTS` + `CREATE UNIQUE INDEX` in both
>    `restore_baseline_index` and `_recreate_canonical_index`.  Was NOT a SQLAlchemy / PG
>    visibility bug (the prior leading hypothesis was refuted by the loadfile
>    counter-experiment and by single-row reproductions of the same failure).
>
> Verification: 10 / 10 consecutive `-n 12` full-suite runs on the Phase 1a cluster are now
> deterministically clean.  Full investigation chain in
> [`phase1-flake-investigation.md`](phase1-flake-investigation.md); Phase 1d retrospective
> earlier in this file.  The Phase 2a baseline pass-count gate ("full suite passes at the
> same numbers as Phase 1c +/- noise") can now be evaluated against a deterministic baseline.
> If Phase 2 surfaces a new flake, do NOT mistake it for a PG 18 regression without first
> checking the failure shape against the two patterns above -- if either matches, treat as a
> regression of the Phase 1d fix, not a PG 18 issue.

**Pre-flight verification:** PG 18.3 (released 2026-02-26) is the current stable; five minor
releases over seven months. Breaking changes that *could* affect Shekel (data checksums default, MD5
password deprecation, timezone abbreviation lookup, FTS/pg_trgm collation, VACUUM partitioned
children, char signedness) have been audited individually and none apply -- Shekel uses
scram-sha-256, libc collation (default), no partitioned tables, and x86_64 Linux only.

#### Phase 2a -- Test cluster upgrade -- **Implemented (pending commit) -- test-db slice only; dev `db` deferred to 2c**

**What landed:** `docker-compose.dev.yml`'s `test-db` service swapped from `postgres:16-alpine` to
`postgres:18-alpine` at line 57. The Phase 1a tmpfs mount target moved from
`/var/lib/postgresql/data` to `/var/lib/postgresql` (line 97), with an expanded comment block above
the tmpfs entry explaining the docker-library/postgres#1259 PGDATA layout change, citing the PG
manual URL bump from `/docs/16/` to `/docs/18/`, and re-verifying `uid=70` against the new image
tag (`docker run --rm postgres:18-alpine id postgres`). The dev `db` service at line 28 stays on
`postgres:16-alpine` -- swapping it would brick the populated `shekel-dev_pgdata` volume that
carries 813 transactions across 6 schemas (PG 18 binaries refuse to read PG 16 files; the migration
path is Phase 2c's pg_dumpall + restore).

After the swap, the test cluster reports `PostgreSQL 18.3 on x86_64-pc-linux-musl` with PGDATA at
`/var/lib/postgresql/18/docker` (the new pg_ctlcluster default), the Phase 1a durability trio
(`fsync=off`, `synchronous_commit=off`, `full_page_writes=off`) preserved, and the Phase 0 timeout
trio (`idle_in_transaction_session_timeout=30000`, `lock_timeout=10000`, `statement_timeout=30000`)
preserved. The tmpfs mounts at the parent path with 2 GiB total; PG 18 creates the
per-major-version subdir inside it.

**Notable design choices:**

- **Split Phase 2a -- swap only the test-db slice, defer the dev `db` swap to Phase 2c.** The plan
  spec called for both `db` and `test-db` to swap in a single Phase 2a edit. The dev `db` service
  carries real developer data on a populated Docker volume (`shekel-dev_pgdata`: auth=3, budget=17,
  ref=13, salary=10 tables; 2 users; 813 transactions; verified via `docker volume inspect` +
  schema-level `SELECT count(*)`). An in-place PG 16 -> PG 18 image swap on a populated volume
  fails with "incompatible data directory version" because PG 18 binaries cannot read PG 16 data
  files. The brief explicitly anticipated this: "if so, propose splitting Phase 2a's image swap so
  test-db swaps first (no pgdata volume, recreates from scratch on tmpfs) and dev db waits until
  Phase 2c (dump/restore path)." Done.
- **Tmpfs mount target at the parent path, not the legacy data path or the version-specific
  child.** PG 18's docker-library image (PR docker-library/postgres#1259) switched the default
  `PGDATA` to `/var/lib/postgresql/$PG_MAJOR/docker` and refuses to start when a mount sits at the
  legacy `/var/lib/postgresql/data`. Three options considered:
  - **Override PGDATA back to `/var/lib/postgresql/data`** via env var on the test-db service. One
    extra line; smallest YAML diff. Rejected because it would resist an upstream-documented
    behaviour change and might break under PG 19/20 if upstream removes legacy compat.
  - **Mount at the version-specific `/var/lib/postgresql/18/docker`** directly. Works for PG 18
    today but breaks on the PG 19 image swap because `$PG_MAJOR` advances. Future-fragile.
  - **Mount at the parent `/var/lib/postgresql`** so PG places its per-major-version subdir inside
    the tmpfs. Aligned with the upstream recommendation (the entrypoint's own error message says
    "The suggested container configuration for 18+ is to place a single mount at
    /var/lib/postgresql"). Forward-compatible. Chosen.
- **Comment block expanded inline** to explain the layout change and cite the upstream PR. A future
  reader investigating the tmpfs target picks up the why without diffing through git history. The
  comment names the verifier command (`docker run --rm postgres:18-alpine env | grep PGDATA`) so
  the assertion is reproducible.
- **Existing `size=2g` guardrail preserved.** The new layout places PG data at
  `/var/lib/postgresql/18/docker/` inside the tmpfs; the 2 GiB budget covers the per-major-version
  subdir plus any sibling files PG might place. Mid-run `df -hT /var/lib/postgresql` showed 46 MB
  used immediately after init (the freshly-initialized cluster); the cap stays a deliberate
  tripwire, not a budget.
- **No code or test-fixture changes.** The conftest bootstrap clones from `shekel_test_template`
  via `CREATE DATABASE ... TEMPLATE`; the per-test contract is bit-for-bit identical regardless of
  PG version.

**Notable deviation from spec:** TWO material deviations vs the original Phase 2a spec.

1. **Dev `db` service stays on PG 16 in this phase.** The plan called for both `db` and `test-db`
   to swap together in Phase 2a. As described in "Notable design choices" above, the dev `db`
   migration requires Phase 2c's dump/restore. The verification step's
   `psql -h localhost -p 5432 -U shekel_user -c "SELECT version()"` (which the plan listed as part
   of Phase 2a) is deferred to Phase 2c.
2. **Tmpfs mount target moved.** The plan specced "swap both `image: postgres:16-alpine` to
   `image: postgres:18-alpine`" as the sole change. The PG 18 docker-image PGDATA layout change
   forced the parent-path tmpfs migration as a co-edit. The full comment block above the tmpfs
   entry was updated to document this; a single-line image swap without the mount-path edit would
   leave the container in an infinite restart loop with "Error: in 18+, these Docker images are
   configured to store database data in a format ... Counter to that, there appears to be
   PostgreSQL data in: /var/lib/postgresql/data (unused mount/volume)".

**Verification (captured evidence):**

```text
$ docker run --rm postgres:18-alpine id postgres
uid=70(postgres) gid=70(postgres) groups=70(postgres),70(postgres)

$ docker run --rm postgres:18-alpine env | grep -E "(PG|POSTGRES)"
PG_SHA256=d95663fbbf3a80f81a9d98d895266bdcb74ba274bcc04ef6d76630a72dee016f
PG_MAJOR=18
PG_VERSION=18.3
PGDATA=/var/lib/postgresql/18/docker

$ docker compose -f docker-compose.dev.yml up -d test-db
 Container shekel-dev-test-db Recreate
 Container shekel-dev-test-db Recreated
 Container shekel-dev-test-db Starting
 Container shekel-dev-test-db Started

$ docker exec shekel-dev-test-db psql -U shekel_user -d postgres -c "SELECT version()"
 PostgreSQL 18.3 on x86_64-pc-linux-musl, compiled by gcc (Alpine 15.2.0) 15.2.0, 64-bit

$ docker exec shekel-dev-test-db psql -U shekel_user -d postgres -tA -c \
    "SELECT name || ' = ' || setting FROM pg_settings WHERE name IN ( \
     'fsync','synchronous_commit','full_page_writes', \
     'idle_in_transaction_session_timeout','lock_timeout','statement_timeout') \
     ORDER BY name"
fsync = off
full_page_writes = off
idle_in_transaction_session_timeout = 30000
lock_timeout = 10000
statement_timeout = 30000
synchronous_commit = off

$ docker exec shekel-dev-test-db sh -c \
    "echo PGDATA=\$PGDATA; df -hT /var/lib/postgresql"
PGDATA=/var/lib/postgresql/18/docker
Filesystem  Type   Size  Used Available Use% Mounted on
tmpfs       tmpfs  2.0G  46.4M     2.0G   2% /var/lib/postgresql

$ TEST_ADMIN_DATABASE_URL='postgresql://shekel_user:shekel_pass@localhost:5433/postgres' \
    python scripts/build_test_template.py
  Step 1/3: dropped and recreated empty database.
  Step 2/3: migrated to head, applied audit, seeded reference data.
  Step 3/3: verified (18 account types, 31 audit triggers, 0 audit_log rows).
DONE: shekel_test_template ready.

$ pytest tests/test_models/test_computed_properties.py -n 0 -q   # smoke
32 passed in 1.93s
```

**Why first:** Phase 2a unblocks Phase 2e's measurement gate without touching the dev DB (which
needs the Phase 2c migration). Reversibility: revert the tmpfs path change + image-tag swap;
restart the container; rebuild the template. The dev `db` cluster is untouched, so the developer's
local data is unaffected by Phase 2a entirely.

**Original spec (kept for reference -- now historical):**

**Files:**

- `docker-compose.dev.yml:28` (the `db` service `image:` line) and
  `docker-compose.dev.yml:57` (the `test-db` service `image:`
  line).

**Changes:** swap both `image: postgres:16-alpine` to `image: postgres:18-alpine`.

**Verification:**

```bash
docker compose -f docker-compose.dev.yml up -d db test-db
psql -h localhost -p 5433 -U shekel_user -c "SELECT version()"
psql -h localhost -p 5432 -U shekel_user -c "SELECT version()"
```

Both must report `PostgreSQL 18.3` or later.

```bash
python scripts/build_test_template.py
pytest --tb=short
```

Template rebuilds cleanly; full suite passes at the same numbers as Phase 1c +/- noise.

**Reversibility:** swap the image tag back; rebuild template. Local data on the dev `db` survives
via the named pgdata volume ONLY if no schema-incompatible migration runs in the interim. For
safety, also dump the dev DB before this step:
`docker compose -f docker-compose.dev.yml exec db pg_dumpall -U shekel_user > /tmp/pg16-dev-backup.sql`.

#### Phase 2b -- CI upgrade -- **Implemented (pending commit)**

**What landed:** `.github/workflows/ci.yml`'s postgres service `image:` key (line 33) swapped from
`postgres:16` to `postgres:18`. The file's leading comment block was also updated -- the original
read "Uses a PostgreSQL 16 service container to match the production database version", which
becomes incorrect the moment the swap lands; refreshed to "Uses a PostgreSQL 18 service container
to match the production database version (upgraded from PG 16 to PG 18 in Phase 2 of
docs/audits/test_improvements/test-performance-implementation-plan.md to enable Phase 3's
reflink-backed CREATE DATABASE TEMPLATE clones)." No other CI changes.

**Notable design choices:**

- **`postgres:18` debian variant, not `postgres:18-alpine`.** The CI workflow has used the debian
  variant since the file was authored; the postgres:18 image has the same docker-library/postgres
  PR #1259 PGDATA layout change as `postgres:18-alpine`, but the CI runner starts every job with
  an empty pgdata (no volume mount, no persistent state), so the legacy-mount detection cannot
  fire -- the entrypoint's fresh-init branch runs and creates the new
  `/var/lib/postgresql/18/docker` layout from scratch. No mount-path edit needed in the CI
  workflow as a result.
- **Comment refresh in the same edit.** Without it, a future reader greps for "PostgreSQL 16" in
  CI config and finds a now-stale comment that contradicts the actual `image:` key. The bumped
  comment names Phase 2 so the why is discoverable from the file itself.
- **No push to a feature branch in this session.** The brief explicitly forbade autonomous pushes:
  "Do NOT push to a feature branch in this session unless the developer asks -- they own the
  push/CI verify loop." The CI green/red feedback loop runs on the developer's first push that
  carries this file.

**Notable deviation from spec:** none functional. The plan called for "swap `postgres:16` to
`postgres:18`. No other changes needed". The implementation matches; the comment refresh is the
only addition and it's strictly clarifying, not behavioural.

**Verification (captured evidence):**

```text
$ grep -n "postgres:" .github/workflows/ci.yml
32:      postgres:
33:        image: postgres:18

$ grep -n "PostgreSQL" .github/workflows/ci.yml | head -3
4:# to main and on pull requests.  Uses a PostgreSQL 18 service container
5:# to match the production database version (upgraded from PG 16 to
6:# PG 18 in Phase 2 of docs/audits/test_improvements/

# CI green/red verification is the developer's next-push job -- see the
# brief's "Do NOT push to a feature branch in this session unless the
# developer asks" constraint.
```

**Why second:** lowest-risk edit in Phase 2 (no persistent data, no volume mount, no co-edit
needed). Lands as a separate commit before the higher-risk operational migrations (2c, 2d).
Reversibility: single-line revert.

**Original spec (kept for reference -- now historical):**

**Files:**

- `.github/workflows/ci.yml` postgres service `image:` key (line
  ~25-35).

**Changes:** swap `postgres:16` to `postgres:18`. No other changes needed -- the CI cluster has no
persistent data; every run starts from a fresh container.

**Verification:** push the change to a feature branch; confirm the CI workflow goes green.

#### Phase 2c -- Dev cluster pg_dumpall + restore -- **PROPOSED, awaiting "execute Phase 2c"**

**What landed:** nothing on the filesystem yet. The Phase 2c session captured pre-flight facts about
the dev cluster (PG 16.13, populated `shekel-dev_pgdata` volume carrying 813 transactions across
six schemas, two users in `auth.users`) and drafted the migration procedure with three corrections
vs the original spec. The procedure is printed in the 2026-05-12 second session's transcript and
embedded below in the "Proposed procedure" block; no command from that procedure has been executed
in this session and no edits to `docker-compose.dev.yml`'s `db` service have been applied.

**Notable design choices (drafted, not yet executed):**

- **Plan's `python scripts/init_database.py --check` substituted with `flask db current`.** The
  script accepts no arguments (verified: its `__main__` block is
  `if __name__ == "__main__": flask_app = create_app(); with flask_app.app_context(): ...`); the
  read-only Alembic `flask db current` reports the head revision without mutating the database, and
  pairs with row-count smokes (`auth.users == 2`, `budget.transactions == 813`, full schema list
  from `pg_tables`) to confirm the restore captured everything the dump should have. Substituted
  verification is strictly stronger than the plan's `--check` would have been (had it existed):
  Alembic's revision check + table row counts catches dumps that completed partially.
- **`db` service volume mount target moves from `/var/lib/postgresql/data` to
  `/var/lib/postgresql`** in the same Phase 2c YAML edit that swaps the image. Mirrors Phase 2a's
  tmpfs migration; the rationale (docker-library/postgres#1259 layout change) is identical.
- **`docker compose stop db` + `rm -f db` instead of `down db`.** The plan's `down db` syntax mixes
  the project-level `down` command with a service argument -- some Docker Compose versions
  interpret it as `docker compose down` (project-wide) followed by ignoring the `db` argument,
  which would tear down every service in the file. `stop` + `rm` is the surgical equivalent and is
  unambiguous across compose versions.
- **Off-`/tmp` backup of the dump.** The procedure includes `cp /tmp/pg16-dev-backup.sql ~/Shekel-pg16-dev-backup-$(date -I).sql` because some Arch / systemd configurations mount `/tmp` as
  tmpfs (so the dump evaporates on reboot). The home-directory copy is the durable anchor.

**Notable deviation from spec:** the entire phase is a deviation in two senses.

1. **Plan's `python scripts/init_database.py --check` does not exist.** The script accepts no
   arguments. The procedure substitutes `flask db current` + row-count smokes; see "Notable
   design choices" above.
2. **PG 18 docker-image PGDATA layout change** requires a YAML edit in `docker-compose.dev.yml`'s
   `db` service in addition to the image swap. Plan called for image swap only.

**Verification (captured evidence):**

PROPOSED, not yet executed -- awaiting developer "execute Phase 2c". The full procedure (T-0 through
verification) and the rollback path live in the 2026-05-12 second session's transcript; when
executed, this section will be updated with actual `pg_dumpall` byte sizes, `SELECT version()`
outputs, row counts before vs after, and `flask db current` revisions.

```text
Pre-flight evidence (captured 2026-05-12 second session, BEFORE the migration runs):

$ docker volume inspect shekel-dev_pgdata --format '{{.Name}} {{.Mountpoint}}'
shekel-dev_pgdata /var/lib/docker/volumes/shekel-dev_pgdata/_data

$ docker exec shekel-dev-db psql -U shekel_user -d shekel -c "SELECT version()"
 PostgreSQL 16.13 on x86_64-pc-linux-musl, compiled by gcc (Alpine 15.2.0) 15.2.0, 64-bit

$ docker exec shekel-dev-db psql -U shekel_user -d shekel -c \
    "SELECT schemaname, count(*) FROM pg_tables \
     WHERE schemaname NOT IN ('pg_catalog','information_schema') \
     GROUP BY schemaname ORDER BY schemaname"
 schemaname | count
------------+-------
 auth       |     3
 budget     |    17
 public     |     1
 ref        |    13
 salary     |    10
 system     |     1

$ docker exec shekel-dev-db psql -U shekel_user -d shekel \
    -c "SELECT count(*) FROM auth.users" \
    -c "SELECT count(*) FROM budget.transactions"
 count: 2 (auth.users)
 count: 813 (budget.transactions)

$ python -c "import sys; sys.argv = ['init_database.py', '--check']; exec(open('scripts/init_database.py').read())"
# (Confirms the script does not accept --check; argparse not used.)
```

**Why third:** Phase 2c is independently committable but operationally destructive (drops the
`shekel-dev_pgdata` volume between the dump and the restore). The order vs Phase 2a/2b is irrelevant
to the developer's local workflow -- Phase 2a's tmpfs swap already gave the test cluster PG 18,
so the developer can use `pytest` against the new test cluster while the dev `db` stays on PG 16
indefinitely until they choose to run Phase 2c.

**Original spec (kept for reference -- now historical):**

**Files:** none (operational migration).

**Procedure:**

1. Capture:
   `docker compose -f docker-compose.dev.yml exec db pg_dumpall -U shekel_user > /tmp/pg16-dev-backup.sql`
   (already captured in Phase 2a if that step was followed).
2. Stop the db service: `docker compose -f docker-compose.dev.yml down db`.
3. Remove the volume: `docker volume rm shekel-dev_pgdata`.
4. After Phase 2a's image swap, bring the PG 18 db service up with
   an empty volume: `docker compose -f docker-compose.dev.yml up -d db`.
5. Restore:
   `docker compose -f docker-compose.dev.yml exec -T db psql -U shekel_user -d postgres < /tmp/pg16-dev-backup.sql`.
6. Verify role architecture survived:
   `docker compose -f docker-compose.dev.yml run --rm app python scripts/init_database.py --check`.

**Verification:** dev app boots, can log in, all migrations applied, no missing tables.

**Reversibility:** restore the backup against a PG 16 volume by rolling back Phase 2a first.

#### Phase 2d -- Production upgrade window -- **PROPOSED, awaiting "execute Phase 2d on <date>"**

**What landed:** nothing on the filesystem. Phase 2d is the planned ~25-30 min production downtime
window. The 2026-05-12 second session drafted the full procedure with four corrections vs the
original spec; no production command has been run, no production YAML edit applied, no production
container stopped.

**Notable design choices (drafted, not yet executed):**

- **Production volume name is `shekel-prod-pgdata`** (verified via `docker volume ls` and the
  `external: true` declaration in `docker-compose.yml:499-500`), not the plan's `shekel_pgdata`.
  The plan's wrong-name `docker volume rm` would either fail silently (volume does not exist) or,
  worse, delete a co-tenant volume with a matching name. Surfaced and corrected in the proposed
  procedure.
- **`deploy/docker-compose.prod.yml` requires NO edit.** The shared-mode override layers TLS
  flags, cert bind-mounts, and secrets on top of `docker-compose.yml`'s `db` service. The image
  flows through inheritance; the only place to swap the tag is `docker-compose.yml:29`. The plan
  listed `deploy/docker-compose.prod.yml` as a file-to-edit; that's incorrect.
- **PG 18 docker-image layout change** applies to production the same way it applies to dev. The
  `db` service's volume mount target moves from `/var/lib/postgresql/data` to `/var/lib/postgresql`
  in `docker-compose.yml` in the same Phase 2d edit.
- **`pg_dumpall` is the load-bearing backup; btrfs snapshot is conditional.** The plan listed both
  as "dual backup". Reality: `/var/lib/docker/volumes/shekel-prod-pgdata/_data` may or may not live
  on a btrfs subvolume depending on the production host's Docker storage configuration. The
  proposed procedure includes a `sudo btrfs subvolume show /var/lib/docker` pre-flight check; the
  snapshot step runs only if the check returns a real subvolume. `pg_dumpall` (a logical backup,
  format-portable across major versions) is the load-bearing recovery anchor regardless.
- **Estimated window 25-30 min, with the bulk of the time being the `gunzip + psql` restore.** The
  proposed timing block lays out T-0 (pre-flight) through T+30 (manual smoke test) with each step's
  expected duration.

**Notable deviation from spec:** four material deviations vs the original Phase 2d spec.

1. **Volume name correction:** `shekel_pgdata` -> `shekel-prod-pgdata` (verified).
2. **No `deploy/docker-compose.prod.yml` edit:** the image flows through inheritance.
3. **`docker-compose.yml`'s `db` volume mount path** moves from `/var/lib/postgresql/data` to
   `/var/lib/postgresql` (PG 18 layout change).
4. **btrfs snapshot is conditional, not dual-anchor.** pg_dumpall is the load-bearing backup.

**Verification (captured evidence):**

PROPOSED, not yet executed -- awaiting developer "execute Phase 2d on <date>". The full procedure
(T-0 pre-flight through T+30 smoke test) and the three rollback paths (Path A: PG 16 + dump
restore; Path B: btrfs snapshot restore; Path C: fresh PG 16 cluster + dump restore) live in the
2026-05-12 second session's transcript; on execution day this section will be updated with the
actual dump size, the `SHOW data_checksums` output (expected `on` because
`POSTGRES_INITDB_ARGS=--data-checksums` from Commit C-37 fires on the fresh PG 18 initdb), the
`flask db current` revision, post-restore row counts, and the smoke-test outcome.

```text
Pre-flight evidence (captured 2026-05-12 second session, BEFORE the window):

$ docker ps -a --filter "name=shekel-prod" --format "{{.Names}}: {{.Image}}: {{.Status}}"
shekel-prod-app: ghcr.io/saltyreformed/shekel:latest: Up 4 hours (healthy)
shekel-prod-db: postgres:16-alpine: Up 4 hours (healthy)
shekel-prod-redis: redis:7.4-alpine: Up 4 hours (healthy)

$ docker volume ls --filter "name=shekel-prod-pgdata"
DRIVER    VOLUME NAME
local     shekel-prod-pgdata

$ grep -n "shekel-prod-pgdata\|external:" docker-compose.yml | head -5
53:      - shekel-prod-pgdata:/var/lib/postgresql/data
499:  shekel-prod-pgdata:
500:    external: true
```

**Why fourth:** Phase 2d is the only step with downtime and the only step that touches the
production stack. It runs LAST so every dependency (test cluster verified PG 18-compatible in 2a,
CI verified PG 18-compatible in 2b, dev rehearsed end-to-end in 2c) is already exercised. A failure
in Phase 2d's smoke test is recoverable via the three documented rollback paths; without 2a -> 2c
having de-risked the path, Phase 2d would be the only place every PG 18 surface gets exercised at
once.

**Original spec (kept for reference -- now historical):**

**Files:**

- `deploy/docker-compose.prod.yml` (the production cluster's
  postgres service `image:` key).
- `docker-compose.yml` (the prod-like local compose for testing
  the prod path before applying it for real; lines around the
  postgres service definition).

**Procedure (planned window, ~30 min downtime):**

1. Announce planned maintenance to any users (currently solo).
2. Stop the app: `docker compose -f deploy/docker-compose.prod.yml stop app nginx`.
3. Take a hot backup:
   `docker compose -f deploy/docker-compose.prod.yml exec db pg_dumpall -U shekel_user > /tmp/pg16-prod-backup.sql`
   AND a filesystem-level snapshot of the Docker volume's backing directory. On Arch with the
   default Docker storage driver:
   `sudo btrfs subvolume snapshot /var/lib/docker/volumes/shekel_pgdata /var/lib/docker/snapshots/shekel-pgdata-pre18`
   (creates the snapshots/ subvolume first if needed; verify the docker volume location with
   `docker volume inspect shekel_pgdata`).
4. Stop the db service: `docker compose -f deploy/docker-compose.prod.yml stop db`.
5. Swap the image tag in both compose files: `postgres:16-alpine`
   -> `postgres:18-alpine`.
6. Remove and recreate the volume to start from an empty PG 18
   data dir: `docker volume rm shekel_pgdata`.
7. Bring up the new db: `docker compose -f deploy/docker-compose.prod.yml up -d db`.
8. Restore:
   `docker compose -f deploy/docker-compose.prod.yml exec -T db psql -U shekel_user -d postgres < /tmp/pg16-prod-backup.sql`.
9. Verify role architecture and migrations:
   `docker compose -f deploy/docker-compose.prod.yml run --rm app python scripts/init_database.py --check`
   and `flask db current` against the restored cluster.
10. Bring up the app: `docker compose -f deploy/docker-compose.prod.yml up -d`.
11. Smoke-test from a browser: login, dashboard, one transaction
    create.

**Rollback path if anything fails:**

1. Stop db.
2. Swap the image tag back to `postgres:16-alpine` in both compose
   files.
3. `sudo btrfs subvolume delete /var/lib/docker/volumes/shekel_pgdata` then
   `sudo btrfs subvolume snapshot /var/lib/docker/snapshots/shekel-pgdata-pre18 /var/lib/docker/volumes/shekel_pgdata`.
4. Bring up db + app.

#### Phase 2e -- PG 18 sanity measurement -- **Implemented (pending commit)**

**What landed:** the Phase 0 harness was re-run on the PG 18.3 test cluster in both single-process
(`-n 0`) and xdist (`-n 12`) modes against the same 253-test slice (`tests/test_models/`) that
captured the Phase 1 baseline; the full 5,276-test suite was run three times at the default `-n 12`
to confirm pass-count parity with Phase 1d's deterministic baseline. All three pass with no flake.

The PG 18 image swap delivers **parity** with PG 16, not speedup. As the plan predicted, PG 18
alone does not materially change TRUNCATE cost -- the per-statement lock + catalog-rewrite + WAL
overhead is the dominant cost regardless of major version. PG 18's `CREATE DATABASE STRATEGY
FILE_COPY` (the gateway to Phase 3's reflink wins) is not engaged in Phase 2 because the conftest
still uses the per-test TRUNCATE+reseed cycle (architecturally unchanged from Phase 1).

**Single-process measurements (`-n 0`, 253 tests):**

| Step | Phase 1 (PG 16) | Phase 2 (PG 18) | Delta |
|---|---|---|---|
| rollback                                |   0.0 ms |   0.0 ms |    -- |
| TRUNCATE main 29 tables CASCADE         |  15.2 ms |  16.1 ms | +6 %  |
| seed_ref re-insert (replica role)       |  13.1 ms |  13.1 ms | 0 %   |
| commit_after_seed                       |   0.8 ms |   0.8 ms | 0 %   |
| refresh_ref_cache                       |   5.4 ms |   5.6 ms | +4 %  |
| **Fixture setup total**                 | **34.5 ms** | **35.7 ms** | **+3.5 %** |
| Test body (call)                        |  17.2 ms |  17.7 ms | +3 %  |
| Teardown                                |   0.1 ms |   0.1 ms | 0 %   |
| Wall-clock                              |  13.53 s |  13.97 s | +3 %  |

**xdist measurements (`-n 12`, 253 tests):**

| Step | Phase 1 (PG 16) | Phase 2 (PG 18) | Delta |
|---|---|---|---|
| TRUNCATE main 29 tables CASCADE         |  19.6 ms |  20.3 ms | +4 %  |
| seed_ref re-insert (replica role)       |  24.9 ms |  24.5 ms | -2 %  |
| commit_after_seed                       |   1.1 ms |   1.1 ms | 0 %   |
| refresh_ref_cache                       |   7.9 ms |   8.0 ms | +1 %  |
| **Fixture setup total**                 | **53.4 ms** | **53.9 ms** | **+0.9 %** |
| Wall-clock                              |   3.54 s |   3.57 s | +1 %  |

Every step is within the spec's ~5 % parity bound; the only line that moved appreciably is the
single-process TRUNCATE-main step (+6 % = +0.9 ms absolute), which is well inside the noise floor
between two cold-start measurements on the same shared host.

**Full-suite measurement (default `-n 12`, all 5,276 tests):**

| Run | Pass count | Wall-clock | Warnings |
|---|---|---|---|
| 1   | 5276 passed | 52.91 s   | 3 (pre-existing flask_login DeprecationWarnings) |
| 2   | 5276 passed | 53.59 s   | 3 (same)                                          |
| 3   | 5276 passed | 52.75 s   | 3 (same)                                          |

Pass count matches Phase 1d (5276). Wall-clock band matches Phase 1d's 52-53 s. No flake. The three
DeprecationWarnings are the same pre-existing `flask_login` `datetime.utcnow()` warnings that
Phase 1d reported -- they are unrelated to this work and outside the project's `app/` scope (they
come from the `flask_login` package).

**Notable design choices:**

- **Three consecutive full-suite runs as the determinism gate.** Phase 1d landed at 10 / 10
  consecutive clean runs as the post-flake baseline; Phase 2e re-uses the same shape. Three runs
  is enough to detect a regression in the Phase 1d fixes (which would surface a known failure
  shape: HTTP 429 cluster or `uq_scenarios_one_baseline` UniqueViolation); a 10-run rerun would
  spend ~9 min on noise characterization for a measurement-only sub-phase. If a Phase 2 regression
  fires in the field, the response is to characterize at 10 runs against the new state, not to
  pre-emptively burn 9 min here.
- **Phase 0 harness slice (`tests/test_models/`, 253 tests) re-used unchanged.** Same slice that
  captured the Phase 1 numbers, so the cell-for-cell comparison is meaningful. Any change to the
  slice would conflate Phase 2 vs Phase 1 deltas with population shift.
- **Single-process + xdist both measured.** Single-process isolates per-test cost from cluster-
  wide WAL contention; xdist exercises the contention the production-like `-n 12` default uses.
  The xdist numbers are the load-bearing pass/fail gate for "is the full suite still fast in
  practice"; the single-process numbers are the load-bearing gate for "did anything regress in
  fixture cost". Both are needed.

**Notable deviation from spec:** none functional. The plan called for "Re-run the Phase 0 harness
on PG 18 to confirm parity. Expected: within ~5 % of Phase 1 numbers." Both measurements (`-n 0`
fixture floor +3.5 %, `-n 12` fixture floor +0.9 %) are inside the bound. The full suite passed at
the same numbers as Phase 1d with no flake.

**Verification (captured evidence):**

```text
$ TEST_ADMIN_DATABASE_URL='postgresql://shekel_user:shekel_pass@localhost:5433/postgres' \
    SHEKEL_TEST_FIXTURE_PROFILE=1 pytest tests/test_models/ -n 0 -q
Fixture profile summary -- 253 tests across 1 worker(s): main
| Step                               | Avg     | p50  | p95  | p99  | Max  | % of fixture |
|------------------------------------|---------|------|------|------|------|--------------|
| rollback                           |  0.0 ms |  0.0 |  0.0 |  0.0 |  0.0 |  0.0 %       |
| TRUNCATE main 29 tables CASCADE    | 16.1 ms | 16.1 | 16.9 | 17.3 | 17.5 | 45.2 %       |
| seed_ref re-insert (replica role)  | 13.1 ms | 13.0 | 14.5 | 15.2 | 16.0 | 36.8 %       |
| commit_after_seed                  |  0.8 ms |  0.8 |  0.9 |  1.0 |  1.4 |  2.3 %       |
| refresh_ref_cache                  |  5.6 ms |  5.6 |  6.2 |  6.6 |  7.0 | 15.7 %       |
| Fixture setup total                | 35.7 ms | 35.5 | 37.5 | 38.4 | 40.6 | 100.0 %      |
| Test body (call)                   | 17.7 ms | 15.2 | 44.2 | 54.0 | 65.8 | --           |
| Teardown                           |  0.1 ms |  0.1 |  0.1 |  0.2 |  0.2 | --           |
253 passed in 13.97s

$ TEST_ADMIN_DATABASE_URL='postgresql://shekel_user:shekel_pass@localhost:5433/postgres' \
    SHEKEL_TEST_FIXTURE_PROFILE=1 pytest tests/test_models/ -q
Fixture profile summary -- 253 tests across 12 worker(s): gw0..gw11
| Step                               | Avg     | p50  | p95  | p99  | Max  | % of fixture |
|------------------------------------|---------|------|------|------|------|--------------|
| rollback                           |  0.0 ms |  0.0 |  0.0 |  0.0 |  0.0 |  0.0 %       |
| TRUNCATE main 29 tables CASCADE    | 20.3 ms | 20.1 | 22.5 | 23.6 | 24.7 | 37.7 %       |
| seed_ref re-insert (replica role)  | 24.5 ms | 24.6 | 25.9 | 26.5 | 26.9 | 45.4 %       |
| commit_after_seed                  |  1.1 ms |  1.1 |  1.4 |  1.6 |  1.8 |  2.1 %       |
| refresh_ref_cache                  |  8.0 ms |  8.0 |  8.7 |  8.9 |  9.2 | 14.8 %       |
| Fixture setup total                | 53.9 ms | 53.9 | 56.5 | 57.3 | 57.8 | 100.0 %      |
| Test body (call)                   | 25.7 ms | 20.9 | 64.3 | 73.9 | 85.0 | --           |
| Teardown                           |  0.2 ms |  0.2 |  0.2 |  0.3 |  0.6 | --           |
253 passed in 3.57s

$ TEST_ADMIN_DATABASE_URL='postgresql://shekel_user:shekel_pass@localhost:5433/postgres' pytest --tb=line
====================== 5276 passed, 3 warnings in 52.91s =======================
====================== 5276 passed, 3 warnings in 53.59s =======================
====================== 5276 passed, 3 warnings in 52.75s =======================

$ pylint app/ --fail-on=E,F --score=y
Your code has been rated at 9.52/10 (previous run: 9.52/10, +0.00)
```

**Why fifth:** measurement-only sub-phase. Confirms the PG 18 swap delivered the projected parity
and detects no regression. Lands as a separate commit (the implementation has zero file changes;
only the plan retrospective is updated). Reversibility is trivial: revert Phases 2a + 2b to put
the test cluster back on PG 16.

**Original spec (kept for reference -- now historical):**

Re-run the Phase 0 harness on PG 18 to confirm parity. Expected: within ~5 % of Phase 1 numbers. PG
18 alone (without reflink) does not materially speed up TRUNCATE -- the gains all come from Phase 3.

**Verification:**

```bash
SHEKEL_TEST_FIXTURE_PROFILE=1 pytest tests/test_models/ -n 0 -q
pytest --tb=short
```

Full suite still passes; baseline numbers logged in status table.

**Why third:** PG 18 is a prerequisite for Phase 3's reflink cloning. Coordinated upgrade preserves
test-prod parity. Production downtime is the only operational cost; Phase 2 is otherwise reversible
via the documented rollback.

### Phase 3 -- Per-test reflink cloning, drop TRUNCATE+reseed cycle -- **Not started**

Eliminates the per-test TRUNCATE+reseed+audit_log-truncate cycle entirely. Each test gets a
brand-new database cloned from the template at ~10-30 ms per clone, replacing the ~281 ms current
fixture floor.

#### Phase 3a -- btrfs subvolume + bind mount + `file_copy_method=clone`

**Files:**

- New: btrfs subvolume on the host.
- Modified: `docker-compose.dev.yml:56-92` (the `test-db` service).

**Procedure:**

1. On the host: `sudo btrfs subvolume create /var/lib/shekel-test-pgdata`
   then `sudo chown 70:70 /var/lib/shekel-test-pgdata` (uid 70 is
   the postgres user in the alpine image; verify with
   `docker run --rm postgres:18-alpine id postgres`).
2. In `docker-compose.dev.yml`, remove the Phase 1a `tmpfs:` block
   on the `test-db` service and replace with a `volumes:` block:

   ```yaml
       volumes:
         - /var/lib/shekel-test-pgdata:/var/lib/postgresql/data
   ```

   The btrfs subvolume preserves reflink semantics through to the
   container's PG data dir. Trade-off vs Phase 1a's tmpfs: reads
   and writes go to disk again, but reflink-backed CREATE DATABASE
   makes that strictly faster than the TRUNCATE path it replaces
   (constant-time clone vs O(catalog rewrite per table)).
3. Add `-c file_copy_method=clone` to the existing `command:`
   list.

**Verification (manual reflink check):** inside the container:

```bash
docker compose -f docker-compose.dev.yml exec test-db psql -U shekel_user -d postgres -c "CREATE DATABASE clone_test TEMPLATE shekel_test_template STRATEGY FILE_COPY"
```

And time it. Must be <50 ms. Then drop `clone_test`. If the command takes seconds rather than
milliseconds, reflink is not active -- check that the subvolume is actually on btrfs
(`stat -f /var/lib/shekel-test-pgdata` shows `Type: btrfs`) and that PG sees `SHOW file_copy_method`
as `clone`.

**Reversibility:** revert the compose diff (restoring the tmpfs block); leave the btrfs subvolume in
place (it does no harm unmounted).

#### Phase 3b -- Conftest rewrite: per-test drop+reclone

**Files:**

- `tests/conftest.py` -- substantial rewrite of `db` fixture and
  bootstrap.

**Architectural shift:** the current `_bootstrap_worker_database` at lines 66-220 creates ONE
per-session DB and the `db` fixture at lines 335-439 TRUNCATEs it per test. Phase 3b inverts this:
the bootstrap creates a fixed-name per-worker DB ONCE, and the `db` fixture per test drops it and
re-clones it from the template.

The fixed DB name avoids the engine-rebinding problem with Flask-SQLAlchemy 3.x: the engine's URL
never changes, only the underlying database is replaced atomically by drop+clone, and the existing
`_db.engine.dispose()` call at line 439 forces the pool to reconnect on the next test's first
session access -- it sees a "different" database (the freshly cloned one) at the same URL.

**Changes:**

1. **`_bootstrap_worker_database`** (lines 66-220) simplified:
   - Keep the xdist master detection (lines 128-132) and orphan
     cleanup blocks (lines 144-166).
   - Replace `db_name = f"shekel_test_{worker_id}_{os.getpid()}"`
     at line 135 with a worker-stable name like
     `f"shekel_test_{worker_id}"` (drop the PID component so
     per-test drop+reclone doesn't accumulate names).
   - Keep the template-existence check (lines 168-178).
   - Keep the initial clone (lines 180-189) so the first test of
     a session has a ready DB. Change to use `STRATEGY FILE_COPY`
     explicitly.
   - Keep the row-count verification (lines 197-214).
   - Set `TEST_DATABASE_URL` to the per-worker DSN (line 218).
2. **New helper `_reset_worker_database(db_name, admin_url)`:**
   psycopg2 admin connection with autocommit, runs
   `DROP DATABASE IF EXISTS {db_name} WITH (FORCE)` then
   `CREATE DATABASE {db_name} TEMPLATE shekel_test_template STRATEGY FILE_COPY`.
   On PG 18 with `file_copy_method = clone`, this is two ~10 ms
   ops.
3. **`db` fixture (lines 335-439) rewritten:**
   - Remove the TRUNCATE block at lines 363-396.
   - Remove the `_seed_ref_tables()` call at line 402.
   - Remove the `_db.session.commit()` at line 403.
   - Remove the `TRUNCATE system.audit_log` block at lines
     405-413 (with its commit at line 414).
   - Replace the body with:

     ```python
     with app.app_context():
         _db.session.remove()
         _db.engine.dispose()
     _reset_worker_database(_DB_NAME, _ADMIN_URL)
     with app.app_context():
         _refresh_ref_cache_and_jinja_globals(app)
         yield _db
         _db.session.remove()
         _db.engine.dispose()
     ```

   - The `_refresh_ref_cache_and_jinja_globals(app)` call at line
     424 (today's location) survives because ref-cache IDs are
     in-process and need to match the freshly-cloned DB's IDs
     (which DO equal the template's IDs since the template is
     cloned, not re-seeded).
4. **`pytest_sessionfinish`** (lines 1327-1383) essentially
   unchanged. Drops the worker DB on session end.
5. **`setup_database` session-scoped fixture** (lines 311-332)
   essentially unchanged -- still refreshes ref_cache after the
   bootstrap clone.

**Why drop+clone-with-same-name instead of unique-name-per-test:**

- Avoids the Flask-SQLAlchemy engine-rebinding complexity. The
  engine sees a stable URL; only the underlying DB changes.
- The `_db.engine.dispose()` call between tests closes all pooled
  connections, so the next test's session opens a fresh connection
  that hits the new (cloned) DB at the same name.
- Concurrent xdist workers each have their own `worker_id`-named
  DB, so no two workers race on the same name.

**Verification:**

```bash
pytest tests/test_models/ -v
pytest tests/test_integration/test_audit_triggers.py -v
pytest tests/test_scripts/test_audit_cleanup.py -v
```

All must pass. The audit-log assertions are the strictest gate: every test must observe the same
empty-audit_log start state that the TRUNCATE-based path provided.

```bash
SHEKEL_TEST_FIXTURE_PROFILE=1 pytest tests/test_models/ -n 0 -q
```

Per-test fixture floor must drop to ~30-50 ms (vs ~281 ms baseline at Phase 0).

#### Phase 3c -- Drop redundant cleanup helpers

**Files:**

- `tests/conftest.py:1300-1324` -- the `_seed_ref_tables` wrapper.

**Changes:**

- Remove `_seed_ref_tables` -- no longer called by the per-test
  path after Phase 3b. Verify no other call sites exist before
  removal: `grep -rn "_seed_ref_tables" .` should show only this
  definition. The production seed at
  `scripts/seed_ref_tables.py` and the template builder at
  `scripts/build_test_template.py:227` both import
  `seed_reference_data` directly from `app.ref_seeds` and are
  unaffected.

**Keep:**

- `_refresh_ref_cache_and_jinja_globals` at
  `tests/conftest.py:1227-1297` -- still needed for in-process
  cache initialisation even when the DB is freshly cloned.

**Verification:**

```bash
pylint app/ scripts/ tests/ --fail-on=E,F
pytest --tb=short
```

Pylint passes with no new warnings; full suite passes.

#### Phase 3d -- Final measurement + docs sweep

**Files:**

- `docs/audits/test_improvements/test-performance-research.md`
  -- append a "Final result <date>" section with the achieved
  numbers. Close the open recommendation.
- `docs/audits/test_improvements/per-worker-database-plan.md`
  -- update the "Performance research follow-up" row in the
  status table to mark Phase C as complete with this plan's
  commit hashes.
- `docs/testing-standards.md` "Test Run Guidelines" -- update
  full-suite wall-clock to the new ~30-60 sec number. The
  8-batch table at the bottom becomes purely historical.
- `CLAUDE.md` "Tests" block (currently ~lines 70-85) -- update
  the per-test cost ceiling and full-suite wall-clock.

**Verification:**

```bash
pytest --tb=short
```

Full suite at `-n 12`: ~30-60 seconds, 5,148 passed. Numbers recorded in this plan's status table
and in `test-performance-research.md`.

**Why last:** documentation lag is acceptable until behavior is verified. Doc edits are zero-risk
after measurement confirms the architecture works.

## Critical files to be modified

| File | Phase | Change type |
|---|---|---|
| `tests/conftest.py` | 0 | Add `SHEKEL_TEST_FIXTURE_PROFILE` harness behind env-var flag; aggregator in `pytest_sessionfinish` |
| `pytest.ini` | 0 | Register `slow_fixture` marker (optional) |
| `docs/audits/test_improvements/test-performance-research.md` | 0, 3d | Append baseline + final results |
| `docker-compose.dev.yml` | 1a, 2a, 3a | `test-db` `command:` + `tmpfs:` (1a), `image:` swap on `db` and `test-db` (2a), bind-mount `volumes:` + `-c file_copy_method=clone` (3a) |
| `tests/conftest.py` | 1b, 3b, 3c | `session_replication_role` suppression in seed (1b); drop+reclone rewrite of `db` fixture (3b); delete dead `_seed_ref_tables` wrapper (3c) |
| `.github/workflows/ci.yml` | 2b | postgres image swap to `postgres:18` |
| `deploy/docker-compose.prod.yml` | 2d | postgres image swap to `postgres:18-alpine` |
| `docker-compose.yml` | 2d | postgres image swap to `postgres:18-alpine` |
| `scripts/build_test_template.py` | 2 (verify) | No required change; runs the same Alembic + seed path on PG 18 |
| `docs/testing-standards.md` | 3d | Update "Test Run Guidelines" timings; mark 8-batch table as historical |
| `CLAUDE.md` | 3d | Update "Tests" block with new wall-clock |
| `docs/audits/test_improvements/per-worker-database-plan.md` | 3d | Mark Phase C as complete |

## Existing functions / utilities to reuse

- `app/ref_seeds.py::seed_reference_data(session, *, verbose=False)`
  -- unchanged; still used by `scripts/build_test_template.py:227`.
- `app/audit_infrastructure.py::apply_audit_infrastructure(executor)`
  -- unchanged; still used by `scripts/build_test_template.py:225`.
- `app/audit_infrastructure.py::AUDITED_TABLES` and
  `EXPECTED_TRIGGER_COUNT` -- unchanged; the template builder's
  verification step still consumes them.
- `tests/conftest.py::_refresh_ref_cache_and_jinja_globals`
  (lines 1227-1297) -- kept in the new per-test fixture so the
  in-process cache matches the freshly-cloned DB's seeded IDs
  (which equal the template IDs by construction).
- `scripts/build_test_template.py` -- unchanged. Template build
  is the same regardless of PG version; reflink is consumed by
  CREATE DATABASE callers, not by the build path.
- `psycopg2.sql.Identifier` quoting pattern in
  `tests/conftest.py:163-189` -- reused in the new
  `_reset_worker_database` helper.
- `urlparse / urlunparse` URL-rewriting at
  `tests/conftest.py:197-199` -- pattern reused if
  `TEST_DATABASE_URL` format needs updating.

## Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| PG 18 has a regression that affects Shekel | Low (18.3, 7 months stable) | Phase 0 harness catches perf changes; existing 5,148-test suite catches behavior changes |
| pg_upgrade vs dump/restore data loss on prod | Low (dump/restore is well-trodden) | Dual backup in Phase 2d: pg_dumpall + btrfs snapshot before; rollback procedure documented |
| btrfs bind-mount permissions wrong | Medium (uid 70 hardcoded in alpine image) | Verified via `docker run --rm postgres:18-alpine id postgres` before Phase 3a |
| reflink silently falls back to byte-copy | Medium (overlay2 vs bind-mount confusion) | Phase 3a explicit verification step times a manual `CREATE DATABASE ... STRATEGY FILE_COPY`; must be <50 ms |
| `STRATEGY FILE_COPY` not the default on small templates | High (this is the PG 18 behavior) | Explicit `STRATEGY FILE_COPY` in `_reset_worker_database` (Phase 3b change 2) |
| Flask-SQLAlchemy engine pool retains a stale connection across drop+reclone | Medium | `_db.engine.dispose()` is already in the existing fixture; Phase 3b preserves it |
| `CREATE DATABASE TEMPLATE` serialises across xdist workers on the same template | High (PG semantics) | Reflink clones are ~10 ms each, so 12-way serialisation is ~120 ms per test-start barrier -- still cheaper than the current 230 ms TRUNCATE |
| Production app code uses PG-16-only SQL | Low (no such patterns found in audit_infrastructure.py or migrations) | Full suite on PG 18 in Phase 2a catches regressions before prod |
| Profile harness adds overhead when disabled | Low (guard is `if _FIXTURE_PROFILE_ENABLED:`) | Phase 0 verification step explicitly re-runs baseline with flag off |
| Phase 1 changes get reverted by Phase 3 (wasted effort) | Low | The docker-compose change in 1a is replaced cleanly in 3a (tmpfs -> bind mount); the conftest A2 suppression in 1b is also superseded by Phase 3b (no seed runs per test -- the template carries the seed).  Both are clean swaps, not patches on patches. |
| Concurrent xdist workers race on the same per-worker DB name | None (each worker has its own `worker_id`) | Phase 3b stable per-worker DB name is `shekel_test_{worker_id}`; each worker owns exactly one name |

## Out of scope (documented as follow-ups)

- Tuning `app/audit_infrastructure.py` itself. The trigger function
  is gated by the 20 % overhead bound in
  `tests/test_performance/test_trigger_overhead.py:23`; as long as
  that bound holds, no action is needed. Optimising the trigger
  would be a separate "audit trigger perf" project with its own
  benchmarks.
- Adopting `pytest-clean-database` or `pgtestdbpy` as third-party
  dependencies. `pytest-clean-database` would double the per-table
  trigger count (clashes with the 28 audit triggers and the
  perf-test ceiling). `pgtestdbpy` solves the same problem as
  Phase 3 but is designed for raw SQLAlchemy use, not
  Flask-SQLAlchemy globals -- the hand-rolled fixture in Phase 3b
  integrates more cleanly with this codebase.
- Revisiting the `ref.account_types` FK to `auth.users`
  (`app/models/ref.py:160-164`) that today forces the CASCADE
  re-seed. Phase 3 makes the re-seed moot (template clones carry
  the ref data already), so the FK can stay exactly as audit C-28
  intended.
- Revisiting sequence-coupled tests
  (`tests/test_services/test_growth_engine.py:425`,
  `tests/test_models/test_entry_and_companion_schema.py:292,294`).
  Phase 3 starts every test from the template's sequence state,
  which IS the freshly-seeded state -- those tests continue to
  pass without modification. The `docs/coding-standards.md`
  "Test the behavior, not the implementation" concern remains as a
  code-quality observation but is no longer load-bearing for this
  work.
- Production PG 18 minor-release upgrade discipline (18.4, 18.5,
  ...). Operational topic separate from performance optimisation;
  belongs in a `docs/deployment.md` runbook when production goes
  live with real users.
- Reflink-backed features in production (per-tenant database
  isolation, `pg_basebackup --link` for backups). Not in the
  current architecture or roadmap; production filesystem choice
  remains unchanged by this work.

## End-to-end verification (after all phases land)

Run, in order, after Phase 3d:

```bash
# 1. Template rebuild on PG 18.
python scripts/build_test_template.py

# 2. Profile harness final baseline.
SHEKEL_TEST_FIXTURE_PROFILE=1 pytest tests/test_models/ -n 0 -q
# Expected: per-test fixture floor ~30-50 ms (vs ~281 ms pre-Phase-1).

# 3. Full suite at -n 12.
pytest --tb=short
# Expected: ~30-60 sec wall-clock; 5,148 passed; no audit-log
# regressions.

# 4. Concurrent invocation safety check (preserved from
# per-worker-database-plan.md).  Two terminals at once:
pytest tests/test_models/test_audit_migration.py -v   # terminal A
pytest tests/test_models/test_audit_migration.py -v   # terminal B
# Expected: both pass independently, no deadlocks.

# 5. Audit-log invariant check.
pytest tests/test_integration/test_audit_triggers.py tests/test_scripts/test_audit_cleanup.py -v
# Expected: every audit assertion passes -- per-test clone delivers
# the same "empty audit_log at test start" contract that the old
# TRUNCATE+reseed cycle provided.

# 6. Worker-DB cleanup verification.
psql -h localhost -p 5433 -U shekel_user -l | grep '^[[:space:]]*shekel_test_'
# Expected: only shekel_test_template remains; no per-worker
# leftovers.

# 7. Production smoke test (manual, post-Phase 2d).
# Log in, view dashboard, create one transaction.

# 8. Pylint.
pylint app/ scripts/ --fail-on=E,F
# Expected: 9.50/10 or better; unchanged from current.
```

Each step's pass count must equal or exceed the current baseline (5,148 tests). No failed, errored,
xfailed, or unexpected-passed lines are tolerated.

## Time estimate

- Phase 0 (profile harness): ~1-2 hours.
- Phase 1a (durability knobs + tmpfs): ~1 hour including
  measurement.
- Phase 1b (replication-role suppression): ~1 hour including
  measurement.
- Phase 1c (gate + decision): ~30 min (mostly running the suite
  and updating the status table).
- Phase 2a (test cluster upgrade): ~1 hour including template
  rebuild and full-suite verification.
- Phase 2b (CI upgrade): ~30 min including a feature-branch push
  to confirm green.
- Phase 2c (dev cluster pg_dumpall + restore): ~1 hour.
- Phase 2d (production upgrade window): ~30 min operational
  downtime + ~2 hours engineering (compose edits, dry-run on dev,
  backup scripting).
- Phase 2e (PG 18 sanity measurement): ~30 min.
- Phase 3a (btrfs subvolume + bind mount + clone setting): ~1
  hour including reflink verification.
- Phase 3b (conftest rewrite): ~4-6 hours including end-to-end
  full-suite verification and any Flask-SQLAlchemy quirks
  discovered along the way.
- Phase 3c (drop dead helpers): ~30 min.
- Phase 3d (final measurement + docs sweep): ~1-2 hours.

**Total:** ~17-22 hours engineering across roughly 1-2 calendar weeks, plus a ~30 min production
downtime window in Phase 2d.

Each phase is independently committable and revertable. Each phase ends with a measurement gate that
can defer subsequent phases if the user is satisfied with the current state.

## Resuming from a fresh session

The plan is designed so each phase fits in a single Claude Code session with a clean hand-off.
Recommended session prompt template:

```text
Read docs/audits/test_improvements/test-performance-implementation-plan.md
end-to-end.  Also read docs/audits/test_improvements/test-performance-research.md
and docs/audits/test_improvements/per-worker-database-plan.md
for context.

Execute Phase <N> only (the sub-phases listed in the status
table for that phase).  Do not start the next phase.

Stop at the verification step at the bottom of the Phase <N>
section.  Run the verification commands, show me the output, and
report whether the measurement matches the projection.  Do not
commit -- I will review first.
```

After review:

```text
Phase <N> looks good.  Commit each sub-phase as a separate commit
with the format ``<type>(<scope>): <what changed>`` per
CLAUDE.md's "Definition of Done".  Update the plan's status table
to mark each sub-phase ``**Done**`` with the commit hash and a
one-line note matching the per-worker-database-plan.md convention.
Then end the session.
```

This shape was validated by the per-worker-database-plan.md execution which landed six phases plus a
drift fix across three sessions on `dev`.
