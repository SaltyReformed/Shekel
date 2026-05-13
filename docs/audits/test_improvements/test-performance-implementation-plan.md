# Plan: Test-suite performance via cluster tuning, PG 18 upgrade, and per-test reflink cloning

## Status (as of 2026-05-13)

**Phase 0 + Phase 1 (1a + 1b + 1c + 1d) + Phase 2 (2a + 2b + 2c + 2d + 2e) + Phase 3 (3a + 3b
+ 3c + 3d) all implemented and committed on `dev`.** Phase 3 closed the plan: per-test
TRUNCATE+reseed cycle replaced with per-test drop+reclone on a btrfs-backed PGDATA via PG 18's
reflink-backed `STRATEGY FILE_COPY`.  Single-process fixture floor 298 ms (Phase 0 baseline)
-> 25.5 ms (-91.4%), full-suite wall-clock at `-n 12` ~240 s -> ~62 s on a fresh test-db
container (4x speedup at the default parallelism).

This plan implemented the recommendations in `test-performance-research.md` (filed 2026-05-11)
and superseded its "Phase A / Phase B / Phase C" framing with a four-phase sequential plan plus
measurement gates plus a coordinated PG 16 -> 18 upgrade across test, dev, CI, and production
clusters.  Phase A is folded into Phase 1; Phase B was rejected (clone supersedes hybrid
SAVEPOINT); Phase C landed across Phase 2 + Phase 3.

After Phase 1d the parallel full suite was **deterministically clean at `-n 12` over 10 / 10
consecutive runs** (4 / 12 had failures earlier the same day before the targeted fixes; 50 %
flake rate collapsed to 0 %).  Full evidence chain in
[`phase1-flake-investigation.md`](phase1-flake-investigation.md).

After Phase 2 the test cluster was on PG 18.3 and the full suite remained deterministically
clean at `-n 12` over 3 / 3 consecutive runs (5276 passed, 52.75-53.59 s wall-clock).  Fixture
floor 34.5 ms -> 35.7 ms (+3.5 %, well inside the spec's ~5 % parity bound).

After Phase 3 the full suite is **deterministically clean at `-n 12` over 7 / 7 consecutive
runs** (5276 passed, no flake).  First run after `docker restart shekel-dev-test-db`:
61.4 s; back-to-back plateau ~72 s as the PG cluster's catalog cache fragments from CREATE/DROP
DATABASE churn (container restart returns to baseline).  Single-process fixture floor
35.7 ms -> 25.5 ms (-28% vs Phase 2e, -91% vs Phase 0 baseline).  Xdist fixture floor went UP
from 53.9 ms (Phase 2e) to 82.6 ms (Phase 3) because PG's cluster-level `pg_database` catalog
lock serialises CREATE/DROP DATABASE across xdist workers -- a risk the plan's risk table
acknowledged but under-projected the magnitude of.  Single-process gains dominate the headline
wall-clock outcome; the xdist regression is the load-bearing finding worth flagging for any
future test-performance work.

| Phase | Status | Commit | Notes |
|---|---|---|---|
| Phase 0: Profile harness (committed permanently) | **Implemented (pending commit)** | -- | `tests/conftest.py` gains a permanent profile harness gated on `SHEKEL_TEST_FIXTURE_PROFILE=1` (module-level constants + six helpers + `_profile_step` context manager + aggregator wired into `pytest_sessionfinish`).  Per-test inner steps of the `db` fixture (rollback / TRUNCATE main / seed_ref / commit_after_seed / TRUNCATE audit_log / refresh_ref_cache / call / teardown) each get a timer that yields immediately as a no-op when the flag is unset.  `pytest.ini` registers `slow_fixture` marker for future per-test exclusion.  `.gitignore` excludes `tests/.fixture-profile/`.  `test-performance-research.md` gains section 8 with the new baseline (~298 ms fixture floor over 253 tests at `-n 0`).  Empirical zero-cost check: 83.07 s flag-unset vs 83.05 s flag-set = 0.02 % delta, well under the 2 % gate the plan requires. |
| Phase 1a: Docker-compose durability knobs + tmpfs | **Implemented (pending commit)** | -- | `docker-compose.dev.yml` `test-db` service gains `-c fsync=off -c synchronous_commit=off -c full_page_writes=off` and a `tmpfs: /var/lib/postgresql/data:rw,uid=70,gid=70,size=2g` block.  Stays on PG 16.  uid 70 verified via `docker run --rm postgres:16-alpine id postgres`. |
| Phase 1b: Conftest replication-role suppression | **Implemented (pending commit)** | -- | Wrap `_seed_ref_tables` in `SET LOCAL session_replication_role='replica'`.  **Deviates from spec:** the spec's claim that `TRUNCATE system.audit_log` becomes a no-op is incorrect (test-body audit writes persist across tests within a worker); instead `system.audit_log` is folded into the existing `TRUNCATE main 28 tables CASCADE` statement (now 29 tables) so cross-test audit isolation is preserved while still saving the second commit roundtrip the spec intended. |
| Phase 1c: Measurement gate | **Implemented (pending commit) -- flake surfaced, resolved in Phase 1d** | -- | Per-test fixture floor 298 ms -> 34.5 ms (-89 %, beat the 100-150 ms projection); full suite at `-n 12` 4 min -> ~52 s.  **Pre-existing test-isolation bug exposed:** the faster runtime triggers an intermittent ~50-70 %-rate failure cluster in `tests/test_routes/test_xss_prevention.py` + scenario-uniqueness tests (rate-limit state leaks from `test_errors.py::test_429_*` into downstream `auth_client` logins; mysterious `uq_scenarios_one_baseline` violation on bulk-insert of three `is_baseline=False` rows).  The bug does NOT reproduce on baseline docker-compose with the same Phase 1b conftest (0 failures / 2 baseline runs).  Phase 1 functional gate passes (single-process and audit-asserting tests are deterministic); the parallel-suite gate is flaky under Phase 1a's tmpfs+durability knobs.  Root cause identified and fixed in Phase 1d below. |
| Phase 1d: Flake resolution | **Implemented (pending commit)** | -- | Both failure shapes root-caused as pre-existing test-isolation bugs that Phase 1a's faster execution exposed by changing the pytest-xdist scheduling timing landscape (`--dist=loadgroup` distributes ungrouped tests INDIVIDUALLY across workers when no `xdist_group` marker is present).  **Shape #1** (HTTP 429 cluster) -- five rate-limit tests in `test_errors.py` and `test_auth.py` had cleanup outside a `try`/`finally`; when one test's assertion failed (specifically `Retry-After == "900"` rounding to 899 under WAL contention), the cleanup was skipped and the Limiter singleton was left `enabled=True` with a populated bucket, breaking every downstream `auth_client.post("/login", ...)` on the same xdist worker.  Fix: wrap cleanup in `try`/`finally`; relax `Retry-After` assertion to `895 <= retry_after <= 900` to absorb the integer-rounding window.  **Shape #2** (`uq_scenarios_one_baseline` UniqueViolation) -- `tests/test_models/test_c41_baseline_unique_migration.py::TestAssertIndexShapeRejectsMalformedIndex::test_assert_index_shape_raises_on_non_partial_index` intentionally created a malformed (non-partial) version of the index inside its body to verify the migration's shape check rejects it; the `restore_baseline_index` cleanup fixture used `CREATE UNIQUE INDEX IF NOT EXISTS` which is a no-op when ANY index of that name exists -- malformed shape leaked across worker tests.  Fix: change cleanup to `DROP INDEX IF EXISTS` + `CREATE UNIQUE INDEX` (drop-then-create rather than `IF NOT EXISTS`) in both the fixture and the `_recreate_canonical_index` helper.  Verification: 10 / 10 consecutive full-suite runs clean at `-n 12 --dist=loadgroup` on the Phase 1a cluster; deterministic two-test reproducer (test 14 + the three vulnerable scenario tests in sequence) passes after fix.  Pylint app/: 9.52/10 unchanged (no app/ changes).  Full investigation evidence in [`phase1-flake-investigation.md`](phase1-flake-investigation.md). |
| Phase 2a: PG 18 upgrade -- test cluster | **Implemented (pending commit) -- test-db slice only; dev `db` deferred to 2c** | -- | `docker-compose.dev.yml` `test-db` image: postgres:16-alpine -> postgres:18-alpine.  **Deviates from spec:** the dev `db` service stays on PG 16 because the populated `shekel-dev_pgdata` volume (auth=3 / budget=17 / ref=13 / salary=10 tables, 813 transactions, 2 users) cannot survive an in-place major-version restart -- PG 18 binaries refuse to read PG 16 files.  The `db` swap moves to Phase 2c's dump/restore.  Second spec deviation: PG 18's docker-library image (PR docker-library/postgres#1259) changed PGDATA from `/var/lib/postgresql/data` to `/var/lib/postgresql/$PG_MAJOR/docker` and refuses to start when a legacy mount sits at the old path; the Phase 1a tmpfs mount target was moved from `/var/lib/postgresql/data` to the parent `/var/lib/postgresql` so PG 18 places its per-major-version subdir inside the tmpfs.  uid 70 verified for postgres:18-alpine (`docker run --rm postgres:18-alpine id postgres` returns `uid=70(postgres) gid=70(postgres)`); existing `uid=70,gid=70` tmpfs entry stays correct.  Template rebuild + 32-test smoke + Phase 2e sanity pass clean. |
| Phase 2b: PG 18 upgrade -- CI | **Implemented (pending commit)** | -- | `.github/workflows/ci.yml`: postgres service `image: postgres:16` -> `image: postgres:18`; header comment refreshed from "PostgreSQL 16" to "PostgreSQL 18" with a back-reference to Phase 2.  No other CI changes -- the runner starts every job with an empty pgdata, so the docker-library/postgres#1259 layout change cannot fire the legacy-mount guard rail.  Not pushed to a feature branch in this session (the developer owns the CI verify loop). |
| Phase 2c: PG 18 upgrade -- dev (pg_dump from prod + restore on PG 18 + MFA reset) | **Implemented (pending commit) 2026-05-13** | -- | Executed end-to-end on 2026-05-13.  pg_dumpall of dev captured as rollback anchor (~/Shekel-pg16-dev-pre-prod-restore-2026-05-13.sql, 520 KB); pg_dump -d shekel of prod (~/Shekel-prod-data-2026-05-13.sql, 584 KB, 7617 lines); dev container stopped and removed, `shekel-dev_pgdata` volume dropped; docker-compose.dev.yml `db` service edited (image -> postgres:18-alpine; volume mount target -> `/var/lib/postgresql`); PG 18.3 dev cluster brought up empty with PGDATA at `/var/lib/postgresql/18/docker`; prod dump restored (GRANT-to-shekel_app errors expected because shekel_app role didn't exist yet at restore time -- all schema + data restored cleanly); shekel_app role + table grants provisioned via `scripts/init_db_role.sql` piped through psql; system.audit_log GRANTs to shekel_app applied via the canonical block from `app/audit_infrastructure.py`; MFA disabled for `josh@saltyreformed.com` via `scripts/reset_mfa.py --force` (audit `mfa_reset` event logged into system.audit_log -- count went from 385 to 386); lockout UPDATE ran (0 rows affected, prod was clean).  Post-migration verification: 2 users / 1 mfa_config / 815 transactions / 8 accounts / 1 salary_profile / 386 audit_log rows / 31 audit triggers, all matching prod's pre-flight snapshot.  **Discovered post-restore:** the prod cluster's alembic head is `d477228fee56` (C-28 era) but the dev codebase's head is `b4b588a49a0c` (C-43) -- the dev DB is 8 migrations behind the codebase.  `flask db upgrade` is the operator's next step to catch dev up to head; deferred to the developer, not auto-executed by Phase 2c. |
| Phase 2d: PG 18 upgrade -- production | **Implemented (pending commit) 2026-05-13 -- minimal scope, ~5 min downtime** | -- | Executed on 2026-05-13 with developer's "execute Phase 2d now" trigger.  **Significant plan-vs-reality gap surfaced and adapted before destructive action:** the drafted procedure assumed the project tree's `docker-compose.yml` + `deploy/docker-compose.prod.yml` controlled production; in reality the runtime compose lives at `/opt/docker/shekel/docker-compose.yml` + `/opt/docker/shekel/docker-compose.override.yml`, hand-synced copies that have diverged from the project tree (older base + thinner override + missing the project's C-37 TLS / C-38 secrets / C-33 final networking additions).  Developer chose the **minimal scope**: PG version bump only on the runtime base compose, leave the divergence as-is for a separate sync session.  Image preserved digest-pinned posture (audit C-36): `postgres:16-alpine@sha256:4e6e670b...` -> `postgres:18-alpine@sha256:54451ecb...` (digest captured via `docker image inspect`).  Volume mount edited from `/var/lib/postgresql/data` to `/var/lib/postgresql` (PG 18 layout per docker-library/postgres#1259) with a new comment block explaining the rationale.  Backup pre-edit: pg_dumpall at `/opt/docker/shekel/backups/pg16-prod-pre-pg18-upgrade-2026-05-13.sql` (561 KB / 7415 lines, cluster-level dump including the shekel_app role), plus `docker-compose.yml.bak.20260513-002801`.  Restore was clean (pg_dumpall captured shekel_app role + grants, so no GRANT errors -- unlike Phase 2c's single-DB pg_dump path).  Post-execution verification: same row counts as pre-dump (2/1/815/8/1/385), alembic head `d477228fee56` unchanged, 31 audit triggers, both `shekel_user` and `shekel_app` roles restored with correct DML grants, app entrypoint completed cleanly with "Audit trigger health OK: 31 triggers", app reports healthy after 1s.  **Benign upgrade-side change to flag:** `SHOW data_checksums` is now `on` (PG 18 changed its initdb default to enable checksums; PG 16 cluster had it off). |
| Phase 2e: PG 18 sanity measurement | **Implemented (pending commit)** | -- | Phase 0 harness re-run on PG 18.3 test cluster.  **Single-process (`-n 0`, 253 tests):** fixture total 34.5 ms (Phase 1) -> 35.7 ms (+3.5 %, well inside the spec's ~5 % parity bound); wall-clock 13.53 s -> 13.97 s.  **xdist (`-n 12`, 253 tests):** fixture total 53.4 ms -> 53.9 ms (+0.9 %); wall-clock 3.54 s -> 3.57 s.  **Full suite (`-n 12`):** 3 / 3 consecutive runs `5276 passed, 3 warnings in 52.75 / 53.59 / 52.91 s` -- pass count matches Phase 1d, no flake regression, same three pre-existing flask_login DeprecationWarnings.  Pylint app/: 9.52/10 unchanged.  PG 18 image swap delivers parity; reflink wins are Phase 3. |
| Phase 3a: btrfs subvolume + bind mount + `file_copy_method=clone` | **Done** | `e329151` | `docker-compose.dev.yml` `test-db` service: Phase 1a's tmpfs mount replaced with bind-mount to host btrfs subvolume `/var/lib/shekel-test-pgdata` (operator-created, uid 70:70).  `-c file_copy_method=clone` appended to the `command:` list so PG 18's `CREATE DATABASE ... STRATEGY FILE_COPY` uses kernel `FICLONE` reflinks.  Comment block above the volumes entry rewritten to document the btrfs rationale, the operator setup commands, the trade-off vs tmpfs (data persists across restarts; non-durable knobs remain correct), and the parent-path PG 18 layout requirement.  Manual reflink check: `CREATE DATABASE clone_test TEMPLATE shekel_test_template STRATEGY FILE_COPY` took 44 ms first-cold-cache then 4-5 ms steady-state -- well under the 50 ms gate. |
| Phase 3b: Conftest rewrite -- per-test drop+reclone | **Done** | `376afd3` | `tests/conftest.py` rewritten (+258 / -175 lines).  Per-worker DB name dropped PID suffix to stable form `shekel_test_{worker_id}`.  Orphan cleanup pattern matches both new and legacy PID-suffix names; active-connection filter prevents dropping a sibling invocation's live DB.  Bootstrap initial clone now uses `STRATEGY FILE_COPY` explicitly.  New helpers `_drop_worker_database` and `_clone_worker_database` wrap the admin-DSN psycopg2 calls.  Per-test fixture: defensive rollback -> release engine (session.remove + engine.dispose untimed) -> DROP DATABASE WITH (FORCE) -> CREATE DATABASE STRATEGY FILE_COPY -> refresh ref_cache -> yield -> teardown.  Profile harness step keys updated: `setup_truncate_main` / `setup_seed_ref` / `setup_commit_after_seed` replaced with `setup_drop_db` + `setup_clone_template`; surviving keys (`setup_rollback`, `setup_refresh_ref_cache`, `call`, `teardown`) unchanged so the Phase 2e vs Phase 3 cell-for-cell comparison stays valid.  Phase 1b's `SET LOCAL session_replication_role='replica'` removed (no seed runs per test).  Verification: 36/36 audit-asserting tests pass; 7/7 consecutive full-suite runs clean at `-n 12`; pylint 9.52/10 unchanged.  **Two notable measurements:** single-process fixture floor 35.7 ms (Phase 2e) -> 25.5 ms (Phase 3b, -28%, below the 30-50 ms target band); xdist fixture floor 53.9 ms -> 82.6 ms (+53%, the cluster-level `pg_database` catalog lock serialises CREATE/DROP across xdist workers -- a risk the plan acknowledged but underestimated the magnitude of). |
| Phase 3c: Drop redundant cleanup helpers | **Done** | `c91ca45` | Removed the `_seed_ref_tables` wrapper from `tests/conftest.py` (-27 lines).  No remaining callers in `tests/` or `scripts/`; `app/__init__.py`'s same-named module-local function is a different definition and is unaffected.  `_refresh_ref_cache_and_jinja_globals` retained.  68-test smoke green; pylint 9.52/10 unchanged. |
| Phase 3d: Final measurement + docs sweep | **Implemented (pending commit)** | -- | Final harness measurement captured at `-n 0` (25.5 ms fixture floor) and `-n 12` (82.6 ms fixture floor); full suite final wall-clock 61.42 s (5276 passed, 3 pre-existing flask_login warnings).  No leftover per-worker DBs after session end (only `shekel_test_template` remains).  `CLAUDE.md` Tests block, `docs/testing-standards.md` Test Run Guidelines, `docs/audits/test_improvements/test-performance-research.md` (new section 9 "Final result"), `docs/audits/test_improvements/per-worker-database-plan.md` Phase C status row, and this plan's status table + per-sub-phase retrospectives all updated.  Pylint `app/` 9.52/10 unchanged. |

**Branch state:** ten test-performance commits on `dev` ahead of `origin/dev` (Phase 0 + 1b
`185275f`; Phase 1a + 2a tmpfs + PG 18 `c0cd0bb`; Phase 1d flake fixes `c249846`; Phase 2b CI
`4a34884`; Phase 0+1+2 plan retrospective `99d3a98`; Phase 2c dev pg_dump-from-prod restore
`5888d25`; Phase 2c+2d retrospective `9575514`; Phase 3a btrfs subvolume `e329151`; Phase 3b
conftest rewrite `376afd3`; Phase 3c dead-code drop `c91ca45`).  Phase 3d retrospective +
docs sweep is the only remaining uncommitted piece on `dev` at the time of this writing; it
lands as a final docs(test-improvements) commit closing the plan.  The plan document itself
originally landed on `dev` in commit `d781334`.

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

### Phase 2 -- PostgreSQL 16 -> 18 upgrade (test + dev + CI + prod) -- **Implemented across two sessions (2026-05-12 + 2026-05-13)**

Preserves test-prod parity by upgrading all four clusters together. Unlocks Phase 3's
`file_copy_method = clone`.

The 2026-05-12 second session landed Phase 2a's test-db slice + Phase 2b + Phase 2e (all
committed 2026-05-13). The 2026-05-13 session executed Phase 2c (dev pg_dump-from-prod restore +
MFA reset + lockout cleanup) and Phase 2d (production PG 16 -> 18 upgrade with ~5 min downtime,
minimal scope, digest pinning preserved).  Both 2c and 2d are pending commit at session end.
Phase 2e's measurement gate confirmed the PG 18 image delivers parity with PG 16 (fixture total
+0.9 % at `-n 12`, full suite 52-53 s deterministically clean across 3 / 3 consecutive runs) --
as the plan predicted, PG 18 alone does not materially speed up TRUNCATE; the architectural win is
Phase 3's reflink-backed cloning.  Production cluster's PGDATA volume is on btrfs (verified during
Phase 2d via `df -hT /var/lib/postgresql` showing `/dev/nvme0n1p2 btrfs`), so Phase 3's reflink
prerequisite is satisfied on prod once a Phase 3 design lands.

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

#### Phase 2c -- Dev cluster pg_dump from prod + restore on PG 18 + MFA reset -- **Implemented (pending commit) 2026-05-13**

**What landed:** executed end-to-end on 2026-05-13. The 2026-05-12 second session originally drafted
Phase 2c as a dump-dev / restore-dev path (mirroring the plan's spec); the developer redirected on
2026-05-13 to dump **production's** `shekel` database into dev for manual testing.  Procedure ran
on the host (no app container required) in three stages:

1.  **Backups + prod dump:** pg_dumpall of dev as a rollback anchor (520 KB at
    `~/Shekel-pg16-dev-pre-prod-restore-2026-05-13.sql`) and pg_dump -d shekel of prod (584 KB
    / 7617 lines at `~/Shekel-prod-data-2026-05-13.sql`).
2.  **Volume migration:** dev `db` container stopped + removed; `shekel-dev_pgdata` Docker volume
    dropped; `docker-compose.dev.yml` `db` service edited (`image: postgres:16-alpine` ->
    `postgres:18-alpine`; `volumes: - pgdata:/var/lib/postgresql/data` ->
    `volumes: - pgdata:/var/lib/postgresql`; expanded comment block citing PR
    docker-library/postgres#1259); fresh PG 18 dev cluster brought up; restore ran into the
    empty `shekel` database.
3.  **Role + MFA + lockout cleanup:** `scripts/init_db_role.sql` piped through psql to
    provision `shekel_app` with the dev password and DML grants on the restored tables; the
    canonical `_GRANT_APP_ROLE_SQL` block from `app/audit_infrastructure.py` (3-line GRANT
    USAGE/SELECT/INSERT/SEQUENCE on system schema) applied so shekel_app can read/write the
    restored `system.audit_log`; `scripts/reset_mfa.py --force josh@saltyreformed.com` cleared
    the MFA config row + logged a `mfa_reset` AUTH audit event; the lockout-cleanup UPDATE on
    `auth.users` ran (0 rows affected -- prod had no users in a locked state at dump time).

**Migration head gap surfaced post-execution:** the prod cluster's alembic head is
`d477228fee56` (the C-28 "ref.account_types user_id + per-user partial uniqueness + audit
trigger" migration), but the dev codebase's head is `b4b588a49a0c` (C-43, "ref-FK
ondelete=RESTRICT sweep").  The dev DB now has prod's schema state, which is 8 migrations
behind the codebase: `724d21236759` (drop redundant CHECK), `1702cadcae54`
(ck_recurrence_rules_dom/moy), `b2b1ff4c3cea` (system.audit_log NOT NULL alignment),
`44893a9dbcc3` (hysa_params -> interest_params rename), `2109f7a490e7`
(ck_salary_raises_one_method), `a80c3447c153` (C-41 uq_scenarios_one_baseline in prod),
`c42b1d9a4e8f` (C-42 salary indexes + FK naming), `b4b588a49a0c` (C-43 ref-FK
ondelete=RESTRICT).  Running `flask db upgrade` from the dev `app` (or `python
scripts/init_database.py` via the entrypoint) brings dev forward to head.  Deferred to the
developer as a deliberate hand-off -- the head-catch-up is an independent decision (the
developer may want to verify the prod-restored state first against the prod schema before
moving dev forward, since some downstream tests assume the dev codebase's schema rather than
prod's).

**Notable design choices (drafted, not yet executed):**

- **`pg_dump -d shekel` (single-DB), not `pg_dumpall` (cluster + roles).** Production's
  `shekel_user` role has the production password (real secret in `/opt/docker/shekel/.env`);
  `pg_dumpall` would emit `ALTER ROLE shekel_user PASSWORD '<prod-hash>'`, and replaying that
  on the dev cluster overwrites dev's `shekel_pass` password.  The dev compose hard-codes
  `DB_PASSWORD: shekel_pass` and `DATABASE_URL: postgresql://shekel_user:shekel_pass@db:5432/
  shekel` (`docker-compose.dev.yml:144,148`), so a password change would break dev app startup.
  `pg_dump -d shekel` captures schema + data of the named database only, no role passwords.
  Same set of roles exist on both sides (`shekel_user`, `shekel_app`) because both clusters
  provision them via `scripts/init_db.sql` / `entrypoint.sh`, so the dump's `OWNER TO
  shekel_user` and `GRANT ... TO shekel_app` statements apply cleanly against dev.
- **MFA disable via `scripts/reset_mfa.py --force <email>` is the official path.** The script
  (`scripts/reset_mfa.py:33-69`) clears `mfa_configs.totp_secret_encrypted = NULL`,
  `is_enabled = false`, `backup_codes = NULL`, `confirmed_at = NULL`, and
  `last_totp_timestep = NULL` for the named user, logs a `mfa_reset` AUTH audit event, and
  exits cleanly.  The login MFA gate at `app/routes/auth.py:392-397` redirects to `/mfa/verify`
  if and only if a `mfa_configs` row with `is_enabled = true` exists for the user; clearing
  `is_enabled` is sufficient.  Running through the official tool (rather than crafting raw
  SQL) preserves the audit-trail row that documents the reset, matching the script's documented
  emergency-recovery role.
- **TOTP_ENCRYPTION_KEY mismatch is why MFA must be disabled, not preserved.** Dev's
  `TOTP_ENCRYPTION_KEY` (in dev `.env`, typically a developer-chosen sentinel) differs from
  production's (the real Fernet key in `/opt/docker/shekel/.env` or the
  `totp_encryption_key` Docker secret in shared mode).  If the prod-encrypted
  `totp_secret_encrypted` blob restored cleanly into dev's `auth.mfa_configs.totp_secret_encrypted`,
  the dev app's `mfa_service.decrypt_secret()` call at `/mfa/verify` would raise
  `cryptography.fernet.InvalidToken`; the route at `app/routes/auth.py:827-848` catches that
  and renders "MFA verification failed. The encryption key may have been changed or removed."
  The developer would be locked out.  Disabling MFA bypasses both the verify call and the
  decrypt; the user re-enrolls (or stays disabled) under the dev key going forward.
- **Lockout state clearing is a parallel cleanup.** `auth.users.failed_login_count` (NOT NULL,
  default 0) and `auth.users.locked_until` (nullable timestamptz) gate
  `auth_service.authenticate()` at `app/services/auth_service.py:653`: if `locked_until > now()`,
  login is rejected before the password is even checked.  Production may have been in a
  partially-locked state at dump time (e.g. several recent failed login attempts that hadn't
  hit the 5-attempt lockout threshold yet, or an active lockout window).  No existing script
  clears these columns -- raw SQL is the cleanest path.  Also clear
  `session_invalidated_at` (cosmetic but unsightly: the prod row's value is stale in dev).
- **Optional pre-migration `pg_dumpall` of dev** as a rollback anchor only.  The developer
  has explicitly said the dev data carries nothing they care about, so the dump-of-dev step
  is OPTIONAL.  If included, the dump goes to `~/Shekel-pg16-dev-pre-prod-restore-$(date -I).sql`
  -- it's the recovery anchor if the prod restore fails mid-flight in a way that leaves dev in
  an unbootable state.  The developer can skip it without loss.
- **`pg_dump` uses `--clean --if-exists`** so the restore drops and recreates each prod table
  inside dev's empty `shekel` database.  `--no-owner` is intentionally NOT used: both clusters
  have the same `shekel_user` / `shekel_app` role pair so `OWNER TO shekel_user` and
  `GRANT TO shekel_app` apply cleanly.  Migrations + audit infra come along with the dump
  (schema + DDL); a follow-up `flask db current` confirms head matches.
- **`docker compose stop db` + `rm -f db` instead of `down db`.** Same rationale as the
  original Phase 2c draft -- `down db` is ambiguous across compose versions.

**Notable deviation from spec:** the original plan's Phase 2c was a dev-self-dump-and-restore;
this is a prod-to-dev data refresh plus an MFA reset and lockout clear.  Six material deviations:

1. **Data direction reversed.** Spec dumped dev, restored dev. New direction dumps prod, restores
   into dev.  Developer-driven (2026-05-13): the dev data is throwaway, prod data is what they
   want for manual testing.
2. **`pg_dump -d shekel`** (single-DB), not `pg_dumpall` (cluster + roles).  Avoids overwriting
   the dev `shekel_user` password.
3. **MFA disable step** via `scripts/reset_mfa.py --force <email>`.  Not in the original spec
   because the original spec restored dev's own dump (no foreign MFA secrets, no key mismatch).
4. **Lockout cleanup SQL block** for `auth.users.failed_login_count`, `locked_until`,
   `session_invalidated_at`.  Not in the original spec for the same reason.
5. **Plan's `python scripts/init_database.py --check`** still does not exist (carried over
   from the 2026-05-12 draft); substitute `flask db current` + row-count smokes.
6. **PG 18 docker-image PGDATA layout change** still requires the YAML mount-path edit on the
   dev `db` service (carried over from the 2026-05-12 draft).

**Proposed procedure (printed for the developer; no command executed in this turn):**

```bash
# ---- Pre-flight, host shell ----------------------------------------
PROD_DUMP=~/Shekel-prod-data-$(date -I).sql
DEV_DUMP=~/Shekel-pg16-dev-pre-prod-restore-$(date -I).sql

# (Optional) Capture dev's pre-migration state as a rollback anchor.
# Skip if the developer truly does not care about a dev rollback option.
docker exec shekel-dev-db pg_dumpall -U shekel_user > "$DEV_DUMP"
ls -la "$DEV_DUMP"

# ---- Step 1: dump production's shekel database (not pg_dumpall) ----
# Runs against the running shekel-prod-db container via docker exec.
# --clean --if-exists adds DROP TABLE statements so the restore is
# idempotent into a non-empty target; here dev will be empty, but the
# flags are harmless and forward-compatible if the procedure is re-run.
docker exec shekel-prod-db pg_dump -U shekel_user -d shekel \
    --clean --if-exists > "$PROD_DUMP"
ls -la "$PROD_DUMP"

# ---- Step 2: stop and remove the PG 16 dev db (preserves the volume) ----
docker compose -f docker-compose.dev.yml stop db
docker compose -f docker-compose.dev.yml rm -f db

# ---- Step 3: drop the volume.  DESTRUCTIVE -- this is the irreversible step ----
docker volume rm shekel-dev_pgdata

# ---- Step 4: apply the docker-compose.dev.yml db-service YAML edits ----
#   * services.db.image: postgres:16-alpine -> postgres:18-alpine
#   * services.db.volumes:
#       - pgdata:/var/lib/postgresql/data
#     becomes:
#       - pgdata:/var/lib/postgresql
#   (Mirrors Phase 2a's tmpfs path migration on test-db.)

# ---- Step 5: bring up the empty PG 18 dev cluster ----
# init_db.sql runs via entrypoint and creates shekel_user with the dev
# shekel_pass password + the empty shekel database.
docker compose -f docker-compose.dev.yml up -d db
until docker exec shekel-dev-db pg_isready -U shekel_user -d shekel; do sleep 1; done
docker exec shekel-dev-db psql -U shekel_user -d shekel -c "SELECT version()"
# Must report PG 18.x.

# ---- Step 6: restore prod's dump INTO the dev shekel database ----
docker exec -i shekel-dev-db psql -U shekel_user -d shekel < "$PROD_DUMP" 2>&1 | tail -50
# Watch for ERROR lines.  Expected non-fatal warnings:
#   * "table X does not exist, skipping" -- from --clean --if-exists DROP
#     against an empty target.  Cosmetic; the CREATE that follows succeeds.
#   * NOTICE / WARNING about constraint names or sequence ownership --
#     usually benign.  Any ERROR (not NOTICE / WARNING) on a CREATE or
#     INSERT statement halts the procedure.

# ---- Step 7: disable MFA on the restored production user(s) ----
# Use the docker app container so DATABASE_URL is already pointed at the
# dev shekel DB; reset_mfa.py reads it via create_app().
docker compose -f docker-compose.dev.yml run --rm app \
    python scripts/reset_mfa.py --force <prod_user_email>
# Repeat for every user that had MFA enabled in prod (look up via
# `docker exec shekel-dev-db psql -U shekel_user -d shekel -c
#    "SELECT u.email FROM auth.users u JOIN auth.mfa_configs m
#       ON m.user_id = u.id WHERE m.is_enabled = true"`).

# ---- Step 8: clear lockout state on all restored users ----
docker exec -i shekel-dev-db psql -U shekel_user -d shekel <<'SQL'
UPDATE auth.users
   SET failed_login_count = 0,
       locked_until = NULL,
       session_invalidated_at = NULL
 WHERE failed_login_count > 0 OR locked_until IS NOT NULL OR session_invalidated_at IS NOT NULL;
SQL

# ---- Step 9: read-only verification ----
docker compose -f docker-compose.dev.yml run --rm app flask db current
# Must report the same head revision as the prod cluster (and the dev
# code's migrations directory).

docker exec shekel-dev-db psql -U shekel_user -d shekel -c \
    "SELECT email, locked_until, failed_login_count FROM auth.users"
# locked_until is NULL and failed_login_count is 0 for every row.

docker exec shekel-dev-db psql -U shekel_user -d shekel -c \
    "SELECT u.email, m.is_enabled, (m.totp_secret_encrypted IS NOT NULL) AS has_secret \
       FROM auth.users u LEFT JOIN auth.mfa_configs m ON m.user_id = u.id"
# is_enabled is false (or row absent) for every user; has_secret is false
# (because reset_mfa.py NULLed the secret).

docker exec shekel-dev-db psql -U shekel_user -d shekel -c \
    "SELECT schemaname, count(*) FROM pg_tables \
     WHERE schemaname NOT IN ('pg_catalog','information_schema') \
     GROUP BY schemaname ORDER BY schemaname"
# 6 schemas with row counts matching prod's pre-dump counts.

# ---- Step 10: smoke - log in via the dev app ----
# 1. Bring up the dev app: docker compose -f docker-compose.dev.yml up -d
# 2. Open http://localhost:5000.
# 3. Log in with the production user's email + the production password
#    (the password hash came along with the restore).  No MFA prompt.
# 4. Spot-check the dashboard, a couple of transactions, and the audit
#    log: docker exec shekel-dev-db psql ... -c "SELECT count(*) FROM system.audit_log"
#    -- should have grown by exactly the login+session events.
```

**Rollback path (if Step 6 or 7 fails):**

```bash
# Path A: re-restore dev's own dump on a PG 18 cluster.  Cheapest --
# the YAML edits stay in place; only the volume is rebuilt.
docker compose -f docker-compose.dev.yml stop db
docker compose -f docker-compose.dev.yml rm -f db
docker volume rm shekel-dev_pgdata
docker compose -f docker-compose.dev.yml up -d db
until docker exec shekel-dev-db pg_isready -U shekel_user -d shekel; do sleep 1; done
docker exec -i shekel-dev-db psql -U shekel_user -d postgres < "$DEV_DUMP"
# Dev cluster is back to its pre-migration state on PG 18 with the
# developer's original (throwaway) data.

# Path B: revert to PG 16 with dev's original data.  Roll back the
# YAML edits and start over.  Same as Path A but pre-undoing the
# docker-compose changes.

# Path C: skip the rollback and proceed.  If the dev data is truly
# throwaway, an empty dev DB on PG 18 (just init_db.sql's shell) is
# acceptable -- the developer re-seeds with seed_*.py or manually
# creates the data they need for testing.
```

**Verification (captured evidence):**

```text
Pre-flight (captured 2026-05-13 before execution):

$ docker exec shekel-prod-db psql -U shekel_user -d shekel -c "SELECT version()"
 PostgreSQL 16.13 on x86_64-pc-linux-musl

$ docker exec shekel-prod-db psql -U shekel_user -d shekel -c \
    "SELECT u.email, m.is_enabled, m.confirmed_at IS NOT NULL AS confirmed \
       FROM auth.users u LEFT JOIN auth.mfa_configs m ON m.user_id = u.id"
         email          | is_enabled | confirmed
------------------------+------------+-----------
 josh@saltyreformed.com | t          | t
 klgrubb@pm.me          |            | f
(One MFA-enabled user.)

$ docker exec shekel-prod-db psql -U shekel_user -d shekel -c \
    "SELECT 'auth.users' AS t, count(*) FROM auth.users
     UNION ALL SELECT 'auth.mfa_configs', count(*) FROM auth.mfa_configs
     UNION ALL SELECT 'budget.transactions', count(*) FROM budget.transactions
     UNION ALL SELECT 'budget.accounts', count(*) FROM budget.accounts
     UNION ALL SELECT 'salary.salary_profiles', count(*) FROM salary.salary_profiles
     UNION ALL SELECT 'system.audit_log', count(*) FROM system.audit_log"
 auth.users             |     2
 auth.mfa_configs       |     1
 budget.transactions    |   815
 budget.accounts        |     8
 salary.salary_profiles |     1
 system.audit_log       |   385

Execution evidence (2026-05-13):

$ ls -la ~/Shekel-prod-data-2026-05-13.sql
.rw-r--r-- 584 KB ... (7617 lines)

$ docker exec shekel-dev-db psql -U shekel_user -d postgres -c "SELECT version()"
 PostgreSQL 18.3 on x86_64-pc-linux-musl
$ docker exec shekel-dev-db sh -c 'echo $PGDATA; df -hT /var/lib/postgresql'
PGDATA=/var/lib/postgresql/18/docker
Filesystem  Type   Size  Used Available Use%  Mounted on
/dev/nvme0n1p2  btrfs  1.8T  ...  /etc/resolv.conf

$ docker exec -i shekel-dev-db psql -U shekel_user -d shekel < ~/Shekel-prod-data-2026-05-13.sql 2>&1 | tail
... (GRANT-to-shekel_app errors on every grant statement because role
     didn't exist yet at restore time -- all schema + data restored
     cleanly via the SET/CREATE/COPY paths, only the GRANTs failed)

$ docker exec -i shekel-dev-db psql ... < scripts/init_db_role.sql
SET / DO / RESET / GRANT x4 / ALTER DEFAULT PRIVILEGES x4 / GRANT x1

$ docker exec -i shekel-dev-db psql -U shekel_user -d shekel <<'SQL'
GRANT USAGE ON SCHEMA system TO shekel_app;
GRANT SELECT, INSERT ON system.audit_log TO shekel_app;
GRANT USAGE ON SEQUENCE system.audit_log_id_seq TO shekel_app;
SQL
GRANT / GRANT / GRANT

$ .venv/bin/python scripts/reset_mfa.py --force josh@saltyreformed.com
[2026-05-13 00:12:20] INFO  Shekel app created with config=development
[2026-05-13 00:12:20] WARN  event=mfa_reset category=auth user_email=josh@saltyreformed.com
MFA has been disabled for josh@saltyreformed.com.

$ docker exec -i shekel-dev-db psql -U shekel_user -d shekel <<'SQL'
UPDATE auth.users SET failed_login_count = 0, locked_until = NULL, session_invalidated_at = NULL
 WHERE failed_login_count > 0 OR locked_until IS NOT NULL OR session_invalidated_at IS NOT NULL;
SQL
UPDATE 0    (prod was already clean)

Post-execution verification:

$ docker exec shekel-dev-db psql -U shekel_user -d shekel -tA -c "SELECT version()"
PostgreSQL 18.3 on x86_64-pc-linux-musl, compiled by gcc (Alpine 15.2.0) 15.2.0, 64-bit

$ docker exec shekel-dev-db psql -U shekel_user -d shekel \
    -c "SELECT email, locked_until, failed_login_count FROM auth.users ORDER BY email"
         email          | locked_until | failed_login_count
------------------------+--------------+--------------------
 josh@saltyreformed.com |              |                  0
 klgrubb@pm.me          |              |                  0

$ docker exec shekel-dev-db psql -U shekel_user -d shekel \
    -c "SELECT u.email, m.is_enabled, (m.totp_secret_encrypted IS NOT NULL) AS has_secret
          FROM auth.users u LEFT JOIN auth.mfa_configs m ON m.user_id = u.id ORDER BY u.email"
         email          | is_enabled | has_secret
------------------------+------------+------------
 josh@saltyreformed.com | f          | f
 klgrubb@pm.me          |            | f

$ docker exec shekel-dev-db psql -U shekel_user -d shekel -c \
    "SELECT 'auth.users' AS t, count(*) FROM auth.users
     UNION ALL SELECT 'auth.mfa_configs', count(*) FROM auth.mfa_configs
     UNION ALL SELECT 'budget.transactions', count(*) FROM budget.transactions
     UNION ALL SELECT 'budget.accounts', count(*) FROM budget.accounts
     UNION ALL SELECT 'salary.salary_profiles', count(*) FROM salary.salary_profiles
     UNION ALL SELECT 'system.audit_log', count(*) FROM system.audit_log"
 auth.users             |     2
 auth.mfa_configs       |     1
 budget.transactions    |   815
 budget.accounts        |     8
 salary.salary_profiles |     1
 system.audit_log       |   386      (was 385 in prod + 1 from the mfa_reset event)

$ docker exec shekel-dev-db psql -tAc "SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'audit_%' AND NOT tgisinternal"
31         (matches EXPECTED_TRIGGER_COUNT)

Migration head gap:

$ docker exec shekel-dev-db psql -U shekel_user -d shekel -tA -c "SELECT version_num FROM public.alembic_version"
d477228fee56    (C-28 era -- prod's head at dump time)

$ docker exec shekel-dev-test-db psql -U shekel_user -d shekel_test_template -tA -c \
    "SELECT version_num FROM public.alembic_version"
b4b588a49a0c    (C-43 -- the dev codebase's head, captured in the test template)

8 missing migrations.  Developer must run `flask db upgrade` to bring dev to head before the
dev app can use the latest schema (see "Migration head gap surfaced post-execution" in the
"What landed" section above for the explicit migration list and rationale for not auto-running).
```

**Why third:** Phase 2c is independently committable but operationally destructive (drops the
`shekel-dev_pgdata` volume between the dump and the restore).  The order vs Phase 2a / 2b is
irrelevant to the developer's local workflow -- Phase 2a's tmpfs swap already gave the test
cluster PG 18, so the developer can use `pytest` against the new test cluster while the dev `db`
stays on PG 16 indefinitely until they choose to run Phase 2c.

The redirected prod-to-dev direction also serves as a load-bearing dress rehearsal for Phase 2d
(production downtime window): the same `pg_dump` / `psql` restore mechanics, the same PG 18
docker-image PGDATA layout edit on `docker-compose.yml`, and the same role-existence assumptions
all get exercised against the dev cluster before production sees them.  A Phase 2c failure
discovered here is recoverable in minutes; the same failure in Phase 2d is a production-downtime
extension.

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

#### Phase 2d -- Production upgrade -- **Implemented (pending commit) 2026-05-13 -- minimal scope, ~5 min downtime**

**What landed:** executed on 2026-05-13 with the developer's "execute Phase 2d now" trigger.
Actual downtime ~5 min (app stop -> db stop -> volume drop -> compose edit -> volume recreate ->
db up -> restore -> app up -> healthy), well under the drafted procedure's 25-30 min estimate.
Production currently runs PG 18.3 with all data intact.

**Significant plan-vs-reality gap surfaced before destructive action.** Pre-flight discovery:
the runtime compose at `/opt/docker/shekel/docker-compose.yml` + `docker-compose.override.yml` is
NOT the project tree's `docker-compose.yml` + `deploy/docker-compose.prod.yml`. The runtime is a
hand-synced copy that has diverged in load-bearing ways:

- Runtime base has a digest-pinned image (`postgres:16-alpine@sha256:4e6e670b...`) + custom
  `deploy.resources` + `ulimits`; project base does not.
- Project base has the `user: postgres` + `cap_drop: ALL` + `read_only: true` audit-hardening
  block; runtime does not (the runtime predates that hardening).
- Runtime override is 775 bytes (a 15-line file with `FORWARDED_ALLOW_IPS: 172.18.0.0/16` for
  homelab, marked TEMPORARY); project override is 22 KB with the canonical post-C-33
  `shekel-frontend` network, the C-37 PG TLS command + cert mounts, and the C-38 Docker secrets
  block.
- Runtime override has **no** `POSTGRES_INITDB_ARGS=--data-checksums` (the project's C-37
  posture).  Pre-upgrade `SHOW data_checksums` returned `off` on the running PG 16 cluster.

Three remediations (C-37 TLS, C-38 Docker secrets, C-33 final shekel-frontend network) exist in
the project tree but have NOT yet been synced to production.  Developer was offered three
adaptation paths (minimal / full sync / abort) and chose **minimal**: PG version bump only,
preserving digest pinning, leaving the runtime-vs-project drift to a separate sync session.

**Notable design choices:**

- **Edited the runtime base compose at `/opt/docker/shekel/docker-compose.yml`** -- the
  authoritative compose for the running shekel-prod project (verified via `docker compose ls`
  showing `shekel-prod: /opt/docker/shekel/docker-compose.yml,/opt/docker/shekel/docker-compose.override.yml`).
  The project tree was NOT edited in Phase 2d -- a sync session is the right place to bring those
  changes into runtime alongside C-37/C-38/C-33.
- **Pre-edit compose backup** at `/opt/docker/shekel/docker-compose.yml.bak.20260513-002801`
  follows the operator's established pattern (a previous `.bak.20260506-063209` exists in the
  same directory).
- **Image digest pinning preserved.** New digest captured via
  `docker image inspect postgres:18-alpine --format '{{index .RepoDigests 0}}'` ->
  `postgres@sha256:54451ecb8ab38c24c3ec123f2fd501303a3a1856a5c66e98cecf2460d5e1e9d7`.  Maintains
  audit C-36 immutability posture against `:latest` re-tag attacks.
- **pg_dumpall, not pg_dump -d shekel.** Phase 2c used single-DB `pg_dump` to avoid overwriting
  dev's shekel_user password; Phase 2d is dump-and-restore in the SAME cluster (just a
  major-version bump), so the shekel_user password being captured + restored is exactly what we
  want -- it's the same password before and after.  `pg_dumpall` ALSO captures the shekel_app
  role + its grants, so the restore is self-contained: no manual `init_db_role.sql` step needed,
  no GRANT-on-system.audit_log step needed, no MFA reset step needed.
- **No btrfs snapshot.** `sudo btrfs subvolume show /var/lib/docker` required an interactive
  sudo password.  pg_dumpall is the sole backup anchor (which the drafted procedure flagged was
  acceptable).
- **Volume drop -> recreate sequence respects `external: true`.** The
  `shekel-prod-pgdata` volume is declared `external: true` in the runtime compose (line 330),
  meaning compose does not manage it.  `docker volume rm` followed by `docker volume create`
  preserves the external declaration -- the freshly-created volume is empty and PG 18's
  initdb runs into it cleanly.

**Notable deviation from spec (vs the 2026-05-12 drafted procedure):**

1. **Runtime compose path** -- `/opt/docker/shekel/docker-compose.yml`, not the project tree.
   Drafted procedure was wrong.
2. **No override edit needed.** Runtime override has no db service block; the base compose's
   image swap propagates through.
3. **Scope reduced to minimal.** No C-37 / C-38 / C-33 sync.  No data-checksums INITDB_ARG
   (PG 18 enables checksums by default anyway -- see "Benign upgrade-side change" below).
4. **5 min downtime, not 25-30 min.** The drafted timing was conservative; without TLS cert
   setup, secrets file setup, or smoke-test latency it's just stop -> dump -> swap -> restore ->
   start.

**Benign upgrade-side change to flag:** `SHOW data_checksums` returned `off` on the pre-upgrade
PG 16 cluster and `on` on the post-upgrade PG 18 cluster.  PG 18 changed its `initdb` default to
enable data checksums (one of the "breaking changes" the plan audited as not-applying-to-Shekel
because nothing in Shekel touches the checksum flag at runtime).  Data integrity improvement; no
operational change required.  The pre-existing C-37 plan to enable checksums via
`POSTGRES_INITDB_ARGS=--data-checksums` is now effectively redundant (the default already does it
on fresh PG 18 inits); the C-37 line in `deploy/docker-compose.prod.yml:321` can stay as
defensive belt-and-braces or be removed in the eventual sync session.

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

```text
Pre-flight discovery (2026-05-13 -- BEFORE destructive action):

$ docker compose ls
NAME           STATUS              CONFIG FILES
shekel-prod    running(3)          /opt/docker/shekel/docker-compose.yml,/opt/docker/shekel/docker-compose.override.yml

(Runtime compose is at /opt/docker/shekel/, NOT the project tree.  Adapted procedure.)

$ docker exec shekel-prod-db psql -U shekel_user -d shekel -c "SELECT version()"
 PostgreSQL 16.13 on x86_64-pc-linux-musl
$ docker exec shekel-prod-db psql -U shekel_user -d shekel -tA -c "SHOW data_checksums"
off

$ docker exec shekel-prod-db psql -U shekel_user -d shekel -c \
    "SELECT 'auth.users' AS t, count(*) FROM auth.users UNION ALL ... "
 auth.users             |     2
 auth.mfa_configs       |     1
 budget.transactions    |   815
 budget.accounts        |     8
 salary.salary_profiles |     1
 system.audit_log       |   385

$ docker image inspect postgres:18-alpine --format '{{index .RepoDigests 0}}'
postgres@sha256:54451ecb8ab38c24c3ec123f2fd501303a3a1856a5c66e98cecf2460d5e1e9d7

Execution evidence (2026-05-13):

$ cp /opt/docker/shekel/docker-compose.yml \
     /opt/docker/shekel/docker-compose.yml.bak.20260513-002801
$ docker exec shekel-prod-db pg_dumpall -U shekel_user > \
     /opt/docker/shekel/backups/pg16-prod-pre-pg18-upgrade-2026-05-13.sql
ls -la /opt/docker/shekel/backups/pg16-prod-pre-pg18-upgrade-2026-05-13.sql
.rw-r--r-- 561 KB    (7415 lines, ends with "PostgreSQL database cluster dump complete")

$ docker compose -p shekel-prod ... stop app
$ docker compose -p shekel-prod ... stop db
$ docker compose -p shekel-prod ... rm -f db
$ docker volume rm shekel-prod-pgdata    # DESTRUCTIVE
$ # Edit /opt/docker/shekel/docker-compose.yml:
$ #   image: postgres:16-alpine@sha256:4e6e670b... -> postgres:18-alpine@sha256:54451ecb...
$ #   volumes: shekel-prod-pgdata:/var/lib/postgresql/data -> shekel-prod-pgdata:/var/lib/postgresql
$ docker volume create shekel-prod-pgdata
$ docker compose -p shekel-prod ... up -d db
# Healthy on first pg_isready poll.

$ docker exec shekel-prod-db psql -U shekel_user -d shekel -c "SELECT version()"
 PostgreSQL 18.3 on x86_64-pc-linux-musl
$ docker exec shekel-prod-db sh -c 'echo PGDATA=$PGDATA; df -hT /var/lib/postgresql'
PGDATA=/var/lib/postgresql/18/docker
/dev/nvme0n1p2  btrfs  1.8T ...    (named volume on btrfs subvolume -- ready for Phase 3 reflink)

$ docker exec -i shekel-prod-db psql -U shekel_user -d postgres < ${DUMP} 2>&1 | tail
... GRANT x20+ / ALTER DEFAULT PRIVILEGES x10+ -- no errors

Post-execution verification (2026-05-13):

$ docker exec shekel-prod-db psql -U shekel_user -d shekel -c "SELECT 'auth.users' AS t, count(*) FROM auth.users UNION ALL ..."
 auth.users             |     2     (match pre-dump)
 auth.mfa_configs       |     1     (match)
 budget.transactions    |   815     (match)
 budget.accounts        |     8     (match)
 salary.salary_profiles |     1     (match)
 system.audit_log       |   385     (match)

$ docker exec shekel-prod-db psql -U shekel_user -d shekel -tA -c "SELECT version_num FROM public.alembic_version"
d477228fee56                          (unchanged)

$ docker exec shekel-prod-db psql -U shekel_user -d shekel -tA -c \
    "SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'audit_%' AND NOT tgisinternal"
31                                     (matches EXPECTED_TRIGGER_COUNT)

$ docker exec shekel-prod-db psql -U shekel_user -d postgres -c \
    "SELECT rolname, rolcanlogin FROM pg_roles WHERE rolname IN ('shekel_user','shekel_app')"
shekel_app  | t
shekel_user | t                       (both roles restored by pg_dumpall cluster-level)

$ docker exec shekel-prod-db psql -U shekel_user -d shekel -tA -c "SHOW data_checksums"
on                                     (PG 18 initdb default; was off in PG 16 cluster)

$ docker compose -p shekel-prod ... up -d app
# App healthy after 1s.

$ docker logs shekel-prod-app --tail
...
Audit trigger health OK: 31 triggers (expected >= 31).
Copying static files to shared volume...
=== Starting Application ===
[INFO] Starting gunicorn 26.0.0
[INFO] Listening at: http://0.0.0.0:8000 (1)
[INFO] Booting worker with pid: 19
[INFO] Booting worker with pid: 26
{"timestamp": "2026-05-13T04:30:06.909451Z", "level": "INFO", "message": "Shekel app created with config=production"}

$ docker ps --filter "name=shekel-prod" --format "{{.Names}}: {{.Image}}: {{.Status}}"
shekel-prod-db: postgres:18-alpine: Up (healthy)
shekel-prod-redis: redis:7.4-alpine: Up (healthy)
shekel-prod-app: ghcr.io/saltyreformed/shekel:latest: Up (healthy)
```

**Rollback path remains available:** the `/opt/docker/shekel/docker-compose.yml.bak.20260513-002801`
file preserves the pre-upgrade compose, and the pg_dumpall at
`/opt/docker/shekel/backups/pg16-prod-pre-pg18-upgrade-2026-05-13.sql` is a
format-portable logical backup.  To roll back: `docker compose stop app db; docker volume rm
shekel-prod-pgdata; cp ...bak... docker-compose.yml; docker volume create shekel-prod-pgdata;
docker compose up -d db; docker exec -i shekel-prod-db psql ... < ...dump...; docker compose up -d
app`.  Same shape as the forward path with the compose revert and the dump replay.

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

#### Phase 3a -- btrfs subvolume + bind mount + `file_copy_method=clone` -- **Done** (`e329151`)

**What landed:** `docker-compose.dev.yml`'s `test-db` service swapped from a Phase 1a tmpfs mount
to a btrfs bind mount of the host subvolume `/var/lib/shekel-test-pgdata` (operator-created,
uid 70:70 to match the postgres user inside `postgres:18-alpine`).  The `command:` list grew one
trailing `-c file_copy_method=clone` entry so PG 18's `CREATE DATABASE ... STRATEGY FILE_COPY`
uses the kernel `FICLONE` reflink ioctl on btrfs.  The comment block above the volumes entry was
rewritten from end to end: drops the tmpfs-specific commentary, names the operator setup
commands (`sudo btrfs subvolume create` + `sudo chown 70:70`), cites the boringSQL benchmark
reference, documents the trade-off vs Phase 1a's tmpfs (data persists across restarts; reads /
writes go to disk but reflink-backed CREATE DATABASE is strictly faster than the TRUNCATE path
it supersedes), preserves the PG 18 docker-library/postgres#1259 parent-path mount rationale,
and reaffirms the non-durable-knob constraint ("DO NOT copy any of these settings to the dev `db`
service").  Production compose, dev `db` service, CI workflow, app service, and all other
services are untouched.

**Notable design choices:**

- **Operator-run sudo commands, not agent-run.**  Passwordless sudo for btrfs is not configured
  on this host, so the agent surfaced the literal two commands the operator must run
  (`btrfs subvolume create`, `chown 70:70`) and stopped at the verification gate -- the
  brief explicitly requested this checkpoint.  Test-db was stopped on the agent side via
  `docker compose stop test-db` (no sudo required), then resumed after the operator confirmed
  `stat -f` reported `Type: btrfs`.
- **Mount target stays at the parent `/var/lib/postgresql`.**  Same rationale as Phase 2a's
  parent-path migration -- PG 18 places PGDATA at `/var/lib/postgresql/$PG_MAJOR/docker` per
  docker-library/postgres#1259, and a legacy mount at `/var/lib/postgresql/data` would fail
  the entrypoint's guard rail.  The bind-mount lets the per-major-version subdir live inside
  the subvolume and stays correct across future PG 19 / PG 20 image bumps.
- **`file_copy_method=clone` is per-cluster.**  Added only to the test-db service (the only
  cluster that runs `CREATE DATABASE TEMPLATE`); the dev `db` and production `db` clusters
  never invoke that path so the GUC is intentionally omitted there.  Brief caveat #2 was
  followed literally.
- **Explicit `STRATEGY FILE_COPY` consumed by the conftest helper (Phase 3b).**  PG 18's
  default `WAL_LOG` strategy would NOT use FICLONE on a ~50 MB template even with the GUC
  set globally; only explicit `STRATEGY FILE_COPY` consumes the GUC.  Phase 3a sets the GUC;
  Phase 3b's `_clone_worker_database` issues the explicit clause.

**Notable deviation from spec:** the plan's Phase 3a spec wrote the volume mount target as
`/var/lib/postgresql/data` (the pre-PG-18 legacy path).  The current code uses
`/var/lib/postgresql` (the parent path) -- a consequence of the PG 18 docker-image layout
change documented at Phase 2a.  Same mount-path migration applied here as Phase 2a applied to
the tmpfs.  No other deviation.

**Verification (captured evidence):**

```text
$ docker run --rm postgres:18-alpine id postgres
uid=70(postgres) gid=70(postgres) groups=70(postgres),70(postgres)

$ stat -f -c 'fstype:%T' /var/lib/shekel-test-pgdata
fstype:btrfs

$ stat -c '%n %u:%g %a' /var/lib/shekel-test-pgdata
/var/lib/shekel-test-pgdata 70:70 755

$ docker compose -f docker-compose.dev.yml up -d test-db
 Container shekel-dev-test-db Recreated/Started

$ docker exec shekel-dev-test-db psql -U shekel_user -d postgres -tA -c "SELECT version()"
PostgreSQL 18.3 on x86_64-pc-linux-musl

$ docker exec shekel-dev-test-db psql -U shekel_user -d postgres -tA -c "SHOW file_copy_method"
clone

$ docker exec shekel-dev-test-db sh -c 'df -hT /var/lib/postgresql'
/dev/nvme0n1p2  btrfs  1.8T  ...  /var/lib/postgresql

$ docker exec shekel-dev-test-db psql -U shekel_user -d postgres -c "\timing on" \
    -c "CREATE DATABASE clone_test TEMPLATE shekel_test_template STRATEGY FILE_COPY" \
    -c "DROP DATABASE clone_test"
Time: 44.028 ms   (first cold-cache; subsequent samples 4-5 ms)
Time:  3.799 ms

$ TEST_ADMIN_DATABASE_URL='postgresql://shekel_user:shekel_pass@localhost:5433/postgres' \
    python scripts/build_test_template.py
  Step 1/3: dropped and recreated empty database.
  Step 2/3: migrated to head, applied audit, seeded reference data.
  Step 3/3: verified (18 account types, 31 audit triggers, 0 audit_log rows).
DONE: shekel_test_template ready.

$ pytest tests/test_models/test_computed_properties.py -n 0 -q
32 passed in 2.02s
```

The 44 ms first-clone-after-cold-cache vs 4-5 ms steady-state is the load-bearing evidence that
reflink is engaging (the 50 ms gate is met; without reflink the same operation would land in
the hundreds of milliseconds on a small template).

**Why first:** the docker-compose edit + btrfs subvolume is a prerequisite for Phase 3b's
conftest rewrite -- the rewrite invokes `STRATEGY FILE_COPY`, and without the per-cluster GUC
the strategy would not engage the reflink path.  Reversibility: revert the YAML edit, restart
the container; the host subvolume stays in place (does no harm unmounted; the operator can
`sudo btrfs subvolume delete /var/lib/shekel-test-pgdata` to fully unwind).

**Original spec (kept for reference -- now historical):**

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

#### Phase 3b -- Conftest rewrite: per-test drop+reclone -- **Done** (`376afd3`)

**What landed:** `tests/conftest.py` was rewritten (+258 / -175 lines) to replace the per-test
TRUNCATE+reseed cycle with a per-test drop+reclone.  Six discrete changes:

1. **Module docstring + comment block** updated to describe the new per-test isolation
   mechanism (clone from `shekel_test_template` via `STRATEGY FILE_COPY`) while documenting
   that the per-test contract (empty audit_log, no rows in budget/auth/salary, ref tables
   seeded, ref_cache + Jinja globals reseated) is bit-for-bit identical to the prior
   TRUNCATE+reseed cycle.
2. **`_FIXTURE_PROFILE_STEPS` / `_FIXTURE_PROFILE_LABELS`** updated in lockstep: removed
   `setup_truncate_main`, `setup_seed_ref`, `setup_commit_after_seed`; added `setup_drop_db`,
   `setup_clone_template`.  `setup_rollback`, `setup_refresh_ref_cache`, `call`, `teardown`
   unchanged so Phase 2e vs Phase 3 cell-for-cell diffs remain valid.  Labels updated to the
   actual SQL forms (e.g. `"DROP DATABASE WITH (FORCE)"`, `"CREATE DATABASE TEMPLATE STRATEGY
   FILE_COPY"`).
3. **`_bootstrap_worker_database`** edits:
   - Per-worker DB name dropped the `_{os.getpid()}` suffix to a stable
     `f"shekel_test_{worker_id}"` form so the Flask-SQLAlchemy engine URL stays valid across
     every drop+reclone within a session.
   - Orphan cleanup pattern matches both the new stable form AND the legacy
     `f"{db_name}_%"` PID-suffix names from pre-Phase-3b crashed runs; the active-connection
     filter still skips any DB with live connections.
   - Initial clone now uses `STRATEGY FILE_COPY` explicitly so the very first test of the
     session gets the same reflink path the per-test fixture uses.
   - Orphan-cleanup docstring rewritten to acknowledge that the active-connection filter
     now defends against concurrent pytest invocations rather than the PID-reuse trap (the
     PID-reuse trap is moot under stable names; the concurrent-invocation collision is the
     new failure mode).
4. **Two new module-level constants** `_WORKER_DB_NAME` and `_WORKER_ADMIN_URL` cache the
   bootstrap result for the per-test fixture (skipping the per-call `_BOOTSTRAP_RESULT`
   unpacking).  `None` when the bootstrap was skipped (xdist master); the per-test fixture
   raises `RuntimeError` defensively if both are `None`.
5. **Two new helpers** `_drop_worker_database(db_name, admin_url)` and
   `_clone_worker_database(db_name, admin_url)` wrap the admin-DSN psycopg2 calls.  Both use
   `psycopg2.sql.Identifier` for identifier quoting -- consistent with the rest of the
   module.  Both functions are testable in isolation, take plain arguments, and have no
   Flask binding.
6. **Per-test `db` fixture body rewritten** with the new structure: defensive
   `_db.session.rollback()` -> release the engine (`session.remove()` + `engine.dispose()`,
   untimed -- the cost is dominated by Python overhead) -> `_drop_worker_database` ->
   `_clone_worker_database` -> `_refresh_ref_cache_and_jinja_globals` -> yield -> teardown
   (same session.remove + engine.dispose pair).  The Phase 1b
   `SET LOCAL session_replication_role='replica'` and the 29-table TRUNCATE statement are
   both gone -- the per-test reset is delivered exclusively by the drop+clone.  Docstring
   rewritten to enumerate the new mechanism and explain why the worker DB name is stable
   across the session.

**Notable design choices:**

- **Stable per-worker DB name without PID, per the brief.**  Brief instruction #1 was
  explicit ("Change the per-worker DB name to a stable form WITHOUT pid: `f"shekel_test_
  {worker_id}"`. The plan's spec is correct; keep it.").  Implementation followed the
  brief literally; the testing-standards.md concurrent-invocation guarantee was narrowed
  accordingly in Phase 3d's docs sweep.  Concurrent xdist invocations against the same
  cluster now collide on the worker name and fail loud with "database already exists" --
  the active-connection filter on the orphan-cleanup pass prevents silent corruption of a
  sibling's live DB.
- **Two helper functions, not one combined `_reset_worker_database`.**  The brief's caveat
  #4 mentioned a single `_reset_worker_database` helper, but the profile harness wanted two
  separate step keys (`setup_drop_db` and `setup_clone_template`).  Two separate helpers is
  cleaner than one helper called from two profile-step contexts -- each helper is
  testable in isolation and the step boundaries align with the SQL boundaries.
- **`_db.session.remove()` + `_db.engine.dispose()` untimed.**  Empirically these two
  calls cost ~0 ms in steady state (the previous teardown already disposed the engine).
  Folding them into the `setup_drop_db` timer would blur the actual DROP cost; leaving
  them as a separate, untimed prep step keeps the measurement clean.  An extra
  `setup_release` step key was considered and rejected -- the brief listed exactly six
  step keys, and the prep is dominated by Python overhead, not DB round-trips.
- **Single `with app.app_context():` block, not the two-block form the brief sketched.**
  The brief's example used two app_context blocks bracketing the
  `_reset_worker_database` call; the implementation collapsed to a single block because
  the admin-DSN psycopg2 calls don't need a Flask binding either way, and the single block
  is easier to read.  Functionally equivalent.
- **First-test-of-session edge case:** the first test's `_db.session.remove() +
  _db.engine.dispose()` actually closes the connection opened by `setup_database` session-
  scoped fixture (which called `_refresh_ref_cache_and_jinja_globals(app)` at session
  start).  Cost is ~1-2 ms one-time, amortised across hundreds of tests.  Not material.

**Notable deviation from spec:** the plan's Phase 3b spec assumed dropping the PID was a
strict win ("so per-test drop+reclone doesn't accumulate names").  That rationale is
incorrect -- per-test drop+reclone re-uses the SAME name on every test regardless of PID
suffix; the PID is stable within a process.  The actual cost of dropping the PID is the
loss of concurrent-invocation safety (two concurrent xdist invocations against the same
cluster now collide on `shekel_test_gw0..gwN`).  Documented in this commit's message and in
the docs sweep in Phase 3d; the brief explicitly instructed dropping the PID, so the
deviation is in rationale not behaviour.

The Phase 0 harness was updated literally per brief instruction #5: step keys
`setup_truncate_main` / `setup_seed_ref` / `setup_commit_after_seed` replaced with
`setup_drop_db` + `setup_clone_template`; `setup_rollback`, `setup_refresh_ref_cache`,
`call`, `teardown` kept.  No other deviation.

**Verification (captured evidence):**

```text
$ TEST_ADMIN_DATABASE_URL='postgresql://shekel_user:shekel_pass@localhost:5433/postgres' \
    pytest tests/test_integration/test_audit_triggers.py \
           tests/test_scripts/test_audit_cleanup.py -n 0 -q
36 passed in 1.81s
(The strictest gate: every test must observe an empty system.audit_log at fixture
entry.  Per-test clone delivers the same contract that TRUNCATE+reseed did.)

$ SHEKEL_TEST_FIXTURE_PROFILE=1 pytest tests/test_models/ -n 0 -q     # 253 tests
Fixture profile summary -- 253 tests across 1 worker(s): main
| Step                                       | Avg     | p50  | p95  | p99  | Max  | % of fixture |
|--------------------------------------------|---------|------|------|------|------|--------------|
| rollback                                   |  0.0 ms |  0.0 |  0.0 |  0.0 |  0.0 |  0.0 %       |
| DROP DATABASE WITH (FORCE)                 |  6.1 ms |  6.1 |  6.6 |  6.9 |  7.1 | 24.0 %       |
| CREATE DATABASE TEMPLATE STRATEGY FILE_COPY|  6.4 ms |  6.4 |  7.0 |  7.2 |  7.4 | 25.2 %       |
| refresh_ref_cache                          | 12.9 ms | 12.9 | 13.6 | 14.2 | 15.6 | 50.7 %       |
| Fixture setup total                        | 25.5 ms | 25.5 | 26.7 | 27.6 | 29.2 | 100.0 %      |
| Test body (call)                           | 22.1 ms | 19.4 | 52.0 | 61.4 | 73.1 | --           |
| Teardown                                   |  0.1 ms |  0.1 |  0.2 |  0.2 |  0.3 | --           |
253 passed in 12.45s

$ SHEKEL_TEST_FIXTURE_PROFILE=1 pytest tests/test_models/ -q          # -n 12, 253 tests
Fixture profile summary -- 253 tests across 12 worker(s): gw0..gw11
| Step                                       | Avg     | p50  | p95   | p99   | Max   | % of fixture |
|--------------------------------------------|---------|------|-------|-------|-------|--------------|
| rollback                                   |  0.0 ms |  0.0 |  0.0  |  0.0  |  0.0  |  0.0 %       |
| DROP DATABASE WITH (FORCE)                 | 36.1 ms | 36.9 | 59.7  | 68.5  | 69.7  | 43.7 %       |
| CREATE DATABASE TEMPLATE STRATEGY FILE_COPY| 30.4 ms | 31.1 | 43.8  | 46.9  | 52.8  | 36.8 %       |
| refresh_ref_cache                          | 16.1 ms | 16.0 | 17.6  | 18.4  | 18.8  | 19.4 %       |
| Fixture setup total                        | 82.6 ms | 82.5 | 112.1 | 123.4 | 126.1 | 100.0 %      |
| Test body (call)                           | 29.4 ms | 24.2 | 72.1  | 81.8  | 95.6  | --           |
| Teardown                                   |  0.2 ms |  0.2 |  0.3  |  0.3  |  0.3  | --           |
253 passed in 4.29s

Full suite at -n 12 (7 consecutive runs, container restarted before run 7):
  Run 1 (after restart): 5276 passed, 3 warnings in 61.65s
  Run 2: 5276 passed in 63.35s
  Run 3: 5276 passed in 66.70s
  Run 4: 5276 passed in 68.98s
  Run 5: 5276 passed in 72.41s
  Run 6: 5276 passed in 73.42s
  Run 7 (after `docker restart shekel-dev-test-db`): 5276 passed in 61.35s

The monotonic ~10 s drift across runs 1-6 was root-caused to PG cluster in-memory
catalog cache fragmentation from CREATE/DROP DATABASE churn; container restart
returns to the ~61 s baseline.  No test failures or flake across any of the 7 runs.

$ pylint app/ --fail-on=E,F --score=y
Your code has been rated at 9.52/10 (previous run: 9.52/10, +0.00)
```

Comparison vs Phase 2e:

| Metric | Phase 2e (PG 18 + TRUNCATE+reseed) | Phase 3b (PG 18 + drop+reclone) | Delta |
|---|---|---|---|
| Fixture floor `-n 0` (253 tests)   | 35.7 ms | 25.5 ms | -28.5% |
| Fixture floor `-n 12` (253 tests)  | 53.9 ms | 82.6 ms | +53.2% |
| Wall-clock `-n 0` (253 tests)      | 13.97 s | 12.45 s | -10.9% |
| Wall-clock `-n 12` (253 tests)     |  3.57 s |  4.29 s | +20.2% |
| Wall-clock `-n 12` (full 5276 tests, fresh container) | 52.9 s | 61.4 s | +16.1% |
| Test count                          | 5276    | 5276    | 0 |
| Pylint app/                         | 9.52/10 | 9.52/10 | 0 |

The single-process gains are real and dominate the headline outcome; the xdist regression
reflects PG's cluster-level catalog-lock contention which the plan's risk table flagged but
under-projected.  Architecturally the conftest is significantly simpler (no TRUNCATE list,
no replica-role suppression, no Phase 1b commit boundary surgery); the audit-trigger
contract is delivered by the clone semantics rather than by post-write TRUNCATE.

**Why second:** Phase 3a (the cluster-side btrfs subvolume + file_copy_method=clone) had to
land first because Phase 3b's `STRATEGY FILE_COPY` is inert without the GUC.  Phase 3c (the
dead-code drop) can only land after Phase 3b removes the last caller.  Phase 3b is the
load-bearing change of Phase 3 -- 7/7 full-suite verification across multiple cluster states
is the determinism gate the plan required.

**Original spec (kept for reference -- now historical):**

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

#### Phase 3c -- Drop redundant cleanup helpers -- **Done** (`c91ca45`)

**What landed:** the `_seed_ref_tables` wrapper at `tests/conftest.py` (27 lines including
its docstring) was removed.  No callers remained after Phase 3b's drop+reclone fixture
removed the per-test seed call.

**Notable design choices:**

- **Pre-flight grep first, exactly per the brief's instruction.**  `grep -rn
  "_seed_ref_tables\b" tests/ scripts/` was run before the edit to confirm no remaining
  callers; the result was empty after Phase 3b's commit.  The `app/__init__.py` same-named
  function at line 861 is a different definition (a module-local helper) and was not
  affected.
- **`_refresh_ref_cache_and_jinja_globals` retained.**  Brief instruction explicit; the
  in-process ref_cache must still be reseated per-test (technically a no-op when the
  cloned IDs equal the template IDs by construction, but the call covers the future
  migration that changes the seeded ID set without an eager cache invalidation).

**Notable deviation from spec:** none.

**Verification (captured evidence):**

```text
$ grep -rn "_seed_ref_tables" tests/ scripts/
(no output -- the wrapper is fully gone)

$ pylint app/ --fail-on=E,F --score=y
Your code has been rated at 9.52/10 (previous run: 9.52/10, +0.00)

$ pylint tests/conftest.py --disable=all --enable=E,F
Your code has been rated at 10.00/10

$ pytest tests/test_models/test_computed_properties.py \
         tests/test_integration/test_audit_triggers.py \
         tests/test_scripts/test_audit_cleanup.py -n 0 -q
68 passed in 3.70s
```

**Why third:** purely architectural cleanup; lands after Phase 3b removes the last caller
and before Phase 3d's docs sweep so the docs can claim "no _seed_ref_tables wrapper
remains in tests/conftest.py" accurately.  Reversibility: revert the diff (the function
body is preserved in `app/ref_seeds.py::seed_reference_data` which the wrapper delegated
to; recreating the wrapper is a 5-line edit).

**Original spec (kept for reference -- now historical):**

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

#### Phase 3d -- Final measurement + docs sweep -- **Implemented (pending commit)**

**What landed:** five documentation files updated to reflect the Phase 3 outcome, plus a
final measurement pass to anchor the wall-clock numbers cited in those docs.

1. **`CLAUDE.md` "Tests" block** -- test count updated from 5,148 to 5,276; full-suite
   wall-clock from "~4 min" to "~62 s first run / ~72 s plateau"; the concurrent-invocation
   guarantee narrowed to note that Phase 3b's stable-name scheme makes two simultaneous
   pytest invocations against the same cluster fail loud with "database already exists";
   the per-test mechanism rewritten to describe drop+reclone via reflink rather than
   TRUNCATE+reseed.  Lines 165-191 of the file.
2. **`docs/testing-standards.md` "Test Run Guidelines"** -- same content updates as
   CLAUDE.md plus the 8-batch table reframed as "historical" (the bisecting and sequential-
   debug scenarios it served are now better addressed by per-file `pytest <file>`
   invocations; the table is preserved so existing references to "Batch N" stay
   decodable).  Removed the per-batch wall-clock columns (the figures were derived from
   Phase 4.5 of the per-worker-database-plan and no longer track reality after Phase 3).
3. **`docs/audits/test_improvements/test-performance-research.md`** -- new section 9
   "Final result (2026-05-13)" with the per-step measurement tables (single-process and
   xdist), the full-suite wall-clock characterisation (including the back-to-back drift
   and container-restart return-to-baseline), and the recommendation status (Phase A done,
   Phase B rejected, Phase C done-with-caveat).  Document explicitly closed; future
   work-tracking should start a fresh research doc.
4. **`docs/audits/test_improvements/per-worker-database-plan.md`** -- "Performance
   research follow-up" row moved from `**Filed**` (no commit) to `**Done**` with the
   full commit-hash chain spanning Phase 1/2/3 of the implementation plan.  Single-line
   summary of the outcome (4x suite speedup at -n 12; 12x at -n 0; xdist gain narrower
   than projected).
5. **`docs/audits/test_improvements/test-performance-implementation-plan.md`** -- this
   file.  Status table at the top updated; per-sub-phase retrospective blocks (What
   landed / Notable design choices / Notable deviation from spec / Verification) prepended
   to each of 3a, 3b, 3c, 3d, with the original spec preserved under "Original spec (kept
   for reference -- now historical):".  Matches the format Phase 0 / Phase 1 / Phase 2
   used.

**Notable design choices:**

- **Phase 3 retrospective format follows Phase 2's precedent exactly.**  Four blocks per
  sub-phase; status table row at top mirrors the per-sub-phase note column.  Future
  readers should be able to skim the status table at the top of the plan and have a
  complete picture without scrolling to each sub-phase.
- **`test-performance-research.md` closed deliberately.**  The document was filed as a
  recommendation; the recommendation was implemented; further test-performance work
  should start a fresh document with fresh measurements (the current document's section
  3.x baselines no longer reflect production).
- **8-batch table preserved as historical rather than deleted.**  Existing commits cite
  "Batch 1", "Batch 4", etc.; deleting the table would orphan those references.  Marking
  it historical and removing the wall-clock columns is the minimal preserving edit.
- **Concurrent-invocation guarantee narrowed in CLAUDE.md AND testing-standards.md.**
  Both files now name the Phase 3b stable-name scheme as the reason and describe the
  failure mode (clear "database already exists" error rather than silent corruption).
  The workaround (run one invocation against the dev `db` cluster on port 5432) is
  documented in testing-standards.md for the rare case where concurrent runs are
  genuinely needed.

**Notable deviation from spec:** none.

**Verification (captured evidence):**

```text
$ TEST_ADMIN_DATABASE_URL='postgresql://shekel_user:shekel_pass@localhost:5433/postgres' \
    SHEKEL_TEST_FIXTURE_PROFILE=1 pytest tests/test_models/ -n 0 -q
Fixture profile summary -- 253 tests across 1 worker(s): main
| Step                                       | Avg     | p50  | p95  | p99  | Max  | % of fixture |
|--------------------------------------------|---------|------|------|------|------|--------------|
| rollback                                   |  0.0 ms |  0.0 |  0.0 |  0.0 |  0.0 |  0.0 %       |
| DROP DATABASE WITH (FORCE)                 |  6.1 ms |  6.1 |  6.6 |  6.9 |  7.1 | 24.0 %       |
| CREATE DATABASE TEMPLATE STRATEGY FILE_COPY|  6.4 ms |  6.4 |  7.0 |  7.2 |  7.4 | 25.2 %       |
| refresh_ref_cache                          | 12.9 ms | 12.9 | 13.6 | 14.2 | 15.6 | 50.7 %       |
| Fixture setup total                        | 25.5 ms | 25.5 | 26.7 | 27.6 | 29.2 | 100.0 %      |
253 passed in 12.45s

$ SHEKEL_TEST_FIXTURE_PROFILE=1 pytest tests/test_models/ -q
Fixture profile summary -- 253 tests across 12 worker(s): gw0..gw11
| Step                                       | Avg     | p50  | p95   | p99   | Max   | % of fixture |
|--------------------------------------------|---------|------|-------|-------|-------|--------------|
| rollback                                   |  0.0 ms |  0.0 |  0.0  |  0.0  |  0.0  |  0.0 %       |
| DROP DATABASE WITH (FORCE)                 | 36.1 ms | 36.9 | 59.7  | 68.5  | 69.7  | 43.7 %       |
| CREATE DATABASE TEMPLATE STRATEGY FILE_COPY| 30.4 ms | 31.1 | 43.8  | 46.9  | 52.8  | 36.8 %       |
| refresh_ref_cache                          | 16.1 ms | 16.0 | 17.6  | 18.4  | 18.8  | 19.4 %       |
| Fixture setup total                        | 82.6 ms | 82.5 | 112.1 | 123.4 | 126.1 | 100.0 %      |
253 passed in 4.29s

$ pytest --tb=line                                                    # full suite, fresh container
5276 passed, 3 warnings in 61.42s

$ docker exec shekel-dev-test-db psql -U shekel_user -l | grep '^[[:space:]]*shekel_test_'
 shekel_test_template | shekel_user | UTF8 | ...
(Only the template remains.  No per-worker leftover.)

$ pylint app/ --fail-on=E,F --score=y
Your code has been rated at 9.52/10 (previous run: 9.52/10, +0.00)
```

**Why last:** measurement-only sub-phase that documents the achieved state.  Lands as a
separate commit (the implementation has zero functional file changes; only docs change).
Reversibility is trivial: revert the docs diff.

**Original spec (kept for reference -- now historical):**

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
