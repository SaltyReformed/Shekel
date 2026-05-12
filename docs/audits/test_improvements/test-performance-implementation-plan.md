# Plan: Test-suite performance via cluster tuning, PG 18 upgrade, and per-test reflink cloning

## Status (as of 2026-05-12)

**All phases pending.** Plan written; no implementation work has landed. This plan implements the
recommendations in `test-performance-research.md` (filed 2026-05-11) and supersedes its "Phase A /
Phase B / Phase C" framing with a four-phase sequential plan plus measurement gates plus a
coordinated PG 16 -> 18 upgrade across test, dev, CI, and production clusters.

The branch `claude/review-security-audit-tests-lvJWE` carries this plan document; no code changes
have been committed against it yet.

| Phase | Status | Commit | Notes |
|---|---|---|---|
| Phase 0: Profile harness (committed permanently) | **Not started** | -- | Wraps the existing `db` fixture inner steps with `time.perf_counter()` gated by `SHEKEL_TEST_FIXTURE_PROFILE=1`.  Captures a fresh baseline on the current host (the 2026-05-11 capture is stale). |
| Phase 1a: Docker-compose durability knobs + tmpfs | **Not started** | -- | `docker-compose.dev.yml` `test-db` service gains `-c fsync=off -c synchronous_commit=off -c full_page_writes=off` and a `tmpfs: /var/lib/postgresql/data` block.  Stays on PG 16. |
| Phase 1b: Conftest replication-role suppression | **Not started** | -- | Wrap `_seed_ref_tables` in `SET LOCAL session_replication_role='replica'`; drop the now-redundant `TRUNCATE system.audit_log` step. |
| Phase 1c: Measurement gate | **Not started** | -- | Re-run Phase 0 harness; capture baseline; decide whether to proceed to Phase 2/3. |
| Phase 2a: PG 18 upgrade -- test cluster | **Not started** | -- | `docker-compose.dev.yml` `db` and `test-db` image: postgres:16-alpine -> postgres:18-alpine.  Rebuild template. |
| Phase 2b: PG 18 upgrade -- CI | **Not started** | -- | `.github/workflows/ci.yml` postgres image swap. |
| Phase 2c: PG 18 upgrade -- dev (pg_dumpall + restore) | **Not started** | -- | Dump local dev DB, drop volume, restore on PG 18. |
| Phase 2d: PG 18 upgrade -- production (planned window) | **Not started** | -- | `deploy/docker-compose.prod.yml` + `docker-compose.yml` image swap; ~30 min downtime; dual backup (pg_dumpall + btrfs snapshot); documented rollback. |
| Phase 2e: PG 18 sanity measurement | **Not started** | -- | Re-run Phase 0 harness on PG 18 (expected: within ~5 % of Phase 1 numbers; reflink not yet engaged). |
| Phase 3a: btrfs subvolume + bind mount + `file_copy_method=clone` | **Not started** | -- | `/var/lib/shekel-test-pgdata` btrfs subvolume; bind-mount replaces Phase 1a's tmpfs; PG configured for reflink-backed `STRATEGY FILE_COPY`. |
| Phase 3b: Conftest rewrite -- per-test drop+reclone | **Not started** | -- | Replace per-test TRUNCATE+reseed+audit_log-truncate at `tests/conftest.py:363-413` with `DROP DATABASE ... WITH (FORCE)` + `CREATE DATABASE ... TEMPLATE ... STRATEGY FILE_COPY` using a stable per-worker DB name (avoids Flask-SQLAlchemy engine-rebinding). |
| Phase 3c: Drop redundant cleanup helpers | **Not started** | -- | Remove now-dead `_seed_ref_tables` wrapper at `tests/conftest.py:1300-1324`; keep `_refresh_ref_cache_and_jinja_globals` (still needed for in-process cache). |
| Phase 3d: Final measurement + docs sweep | **Not started** | -- | Re-run Phase 0 harness; update `docs/testing-standards.md`, `CLAUDE.md`, and `test-performance-research.md` with achieved numbers. |

**Branch state:** clean. No commits on `claude/review-security-audit-tests-lvJWE` ahead of
`origin/dev` relate to this plan yet (the prior commits there cover the C-40 through C-43
retroactive sweep). The plan itself will be the first commit attributable to this work.

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

**Host environment:** Arch Linux x86_64, kernel 6.18, btrfs root. btrfs supports the `FICLONE` ioctl
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

### Phase 0 -- Profile harness (committed permanently) -- **Not started**

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

**Why first:** every subsequent phase needs a reproducible measurement to confirm gain or detect
regression. Without this harness, the next sessions are guessing. Reversible (single file revert).

### Phase 1 -- Cluster durability knobs + tmpfs + replication-role suppression -- **Not started**

Captures the cheap win available on PG 16 *today*, with no architectural change. Establishes
measurement discipline (compare against Phase 0 baseline) and de-risks the bigger Phase 3 rewrite.

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

### Phase 2 -- PostgreSQL 16 -> 18 upgrade (test + dev + CI + prod) -- **Not started**

Preserves test-prod parity by upgrading all four clusters together. Unlocks Phase 3's
`file_copy_method = clone`.

**Pre-flight verification:** PG 18.3 (released 2026-02-26) is the current stable; five minor
releases over seven months. Breaking changes that *could* affect Shekel (data checksums default, MD5
password deprecation, timezone abbreviation lookup, FTS/pg_trgm collation, VACUUM partitioned
children, char signedness) have been audited individually and none apply -- Shekel uses
scram-sha-256, libc collation (default), no partitioned tables, and x86_64 Linux only.

#### Phase 2a -- Test cluster upgrade

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

#### Phase 2b -- CI upgrade

**Files:**

- `.github/workflows/ci.yml` postgres service `image:` key (line
  ~25-35).

**Changes:** swap `postgres:16` to `postgres:18`. No other changes needed -- the CI cluster has no
persistent data; every run starts from a fresh container.

**Verification:** push the change to a feature branch; confirm the CI workflow goes green.

#### Phase 2c -- Dev cluster pg_dumpall + restore

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

#### Phase 2d -- Production upgrade window

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

#### Phase 2e -- PG 18 sanity measurement

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
