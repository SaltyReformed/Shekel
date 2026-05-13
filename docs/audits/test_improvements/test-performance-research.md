# Test-suite performance research (2026-05-11)

**Status:** Research complete; recommendation pending decision. No test-code changes have been made
beyond the four committed Phases 0-4 of `per-worker-database-plan.md` plus the `-n 12` default.

**One-line takeaway:** The 230 ms per-test TRUNCATE cost is dominated by per-statement fixed
overhead (lock acquisition + catalog rewrite + file-segment unlink) on mostly-empty tables, not by
fsync or WAL volume. The cheapest evidence-backed win is to replace TRUNCATE CASCADE with DELETE
FROM in FK order; the longest-payoff change is a Django-style hybrid where audit-asserting tests use
TRUNCATE and everything else uses SAVEPOINT rollback.

---

## 1. Background

After Phases 0-4 of the per-pytest-worker database isolation work (commits `e41d136` through
`0937975`) the suite runs concurrently with no deadlocks and the full-suite wall-clock dropped from
~28 min sequential to ~4 min at `pytest-xdist -n 12`. Worker scaling beyond -n 12 keeps adding
speedup at falling efficiency (64 % at -n 4 down to 56 % at -n 16 on a 24-core host with 8 cores
idle at -n 16), which diagnoses the remaining bottleneck as a shared PostgreSQL resource, not CPU.

This document captures the profile that motivated digging further, the community research on what to
do about it, and a tiered recommendation the operator can pick from later.

---

## 2. Methodology

Two measurements were taken on the working tree at commit `0937975` (branch `dev`, PG 16 on
localhost:5433):

1. **Coarse profile via `pytest --durations=0 -vv`** against a
    representative ~180-test single-process slice
    (`tests/test_audit_fixes.py tests/test_ref_cache.py
    tests/test_models/`). Output piped to `/tmp/durations.txt` and
    summarised per phase (setup / call / teardown).

2. **Fine-grained fixture profile** via temporary instrumentation in
    `tests/conftest.py::db` that wrote a CSV row per test naming each
    inner step (rollback / TRUNCATE main 28 tables / seed_ref /
    commit_after_seed / TRUNCATE audit_log / refresh_ref_cache).
    Instrumentation removed after measurement; the committed conftest
    is unchanged.

A separate set of timing runs measured worker-scaling (batch 1 of 1,756 tests at
`-n 1, 4, 8, 12, 16`) to confirm the PG-side serialisation hypothesis.

Raw data is captured inline below. The instrumentation diff is not preserved (no commit), but is
trivial to reconstruct if needed.

---

## 3. Profile findings

### 3.1 Per-test phase breakdown (n=64, single-process)

| Phase | Avg | p50 | p95 | p99 | Max | % of fixture |
|---|---|---|---|---|---|---|
| rollback | 0.0 ms | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 % |
| **TRUNCATE main 28 tables CASCADE** | **230.1 ms** | 229.4 | 234.7 | 267.3 | 267.3 | **81.8 %** |
| seed_ref re-insert | 19.0 ms | 19.2 | 21.3 | 22.4 | 22.4 | 6.8 % |
| commit_after_seed | 4.9 ms | 4.8 | 5.6 | 6.0 | 6.0 | 1.7 % |
| TRUNCATE system.audit_log | 20.1 ms | 20.0 | 21.1 | 25.1 | 25.1 | 7.1 % |
| refresh_ref_cache | 7.2 ms | 7.1 | 8.1 | 8.6 | 8.6 | 2.5 % |
| **Fixture setup total** | **281.2 ms** | | | | | 100 % |
| Test body (call) | 11 ms | 10 | 40 | 60 | 60 | -- |
| Teardown | 0 ms | 0 | 0 | 10 | 10 | -- |

### 3.2 Phase-vs-call ratio

96 % of single-process wall-clock is fixture setup. 4 % is test bodies. Teardown (the
`_db.session.remove() + _db.engine.dispose()` pair) is essentially free.

### 3.3 Worker-scaling profile (batch 1: 1,756 tests)

| Workers | Wall-clock | Speedup | Efficiency | Marginal vs prior |
|---|---|---|---|---|
| -n 1 (seq) | 561 s | 1.00x | 100 % | -- |
| -n 4 | 218 s | 2.57x | 64 % | -343 s vs -n 1 |
| -n 8 | 114 s | 4.93x | 62 % | -104 s vs -n 4 |
| -n 12 | 79 s | 7.07x | 59 % | -35 s vs -n 8 |
| -n 16 | 63 s | 8.95x | 56 % | -16 s vs -n 12 |

Marginal gain halves with every doubling of workers despite ample idle CPU. Each test holds a worker
for ~230 ms on a PG operation that funnels through cluster-wide resources (WAL writer process, the
disk-level fsync queue, the single bgwriter / checkpointer). Per-DB isolation removes inter-worker
deadlocks (the Phase 3 win) but does not give each worker its own WAL stream.

### 3.4 Floor calculation

5,148 tests * 281 ms fixture floor = 24.1 min sequential. At -n 12 with 59 % efficiency that's ~3.4
min just for fixtures. Observed full suite is ~4 min, matching prediction within noise.

---

## 4. Research findings

### 4.1 Canonical PG documentation

The PostgreSQL manual has a dedicated **Non-Durable Settings** page which is the de facto canonical
reference for throwaway test clusters:

  <https://www.postgresql.org/docs/16/non-durability.html>

Verbatim ordering of impact from the manual:

1. Place the database cluster's data directory in tmpfs (eliminates
    all database disk I/O).
2. `fsync = off`.
3. `synchronous_commit = off`.
4. `full_page_writes = off`.
5. Increase `max_wal_size` and `checkpoint_timeout`.
6. Use `UNLOGGED` tables (skips WAL entirely; requires migration
    changes).

### 4.2 Which knob wins for TRUNCATE-heavy workloads

Per [Mateus Rauli's measured TPS comparison][rauli]: `synchronous_commit = off` captures the
majority of the win (~3.5 % TPS gain on his bench). Adding `fsync = off` brings ~10.7 % TPS gain
total. `full_page_writes = off` matters most for UPDATE-heavy workloads (avoids WAL after a
checkpoint) and is the smallest win for TRUNCATE-dominated commits.

[rauli]: https://dev.to/mateus-rauli/how-fsync-and-synchronouscommit-affect-postgresql-performance-22di

Consensus (PG manual + Percona + CYBERTEC writeups): all three plus tmpfs is the standard recipe.
None are controversial in this context. Disk-corruption risk is irrelevant for a throwaway cluster.

### 4.3 The Carbon Health finding -- TRUNCATE may be the wrong tool

[Carbon Health's engineering blog][ch] documented switching from TRUNCATE-all-tables to a
tracked-dirty-tables DELETE strategy and cut CI 30 %:

[ch]: https://carbonhealth.com/blog-post/cleaning-postgresql-db-between-integration-tests-efficiently

> "TRUNCATE acquires an ACCESS EXCLUSIVE lock on the table even when the table is empty. For small
> tables DELETE is faster than TRUNCATE because TRUNCATE pays for lock acquisition, catalog rewrite,
> and file-segment unlink even when no rows exist."

CYBERTEC confirms the same threshold ([DELETE vs TRUNCATE][cyb]): the crossover where TRUNCATE wins
is roughly ~100 rows per table. Below that, DELETE wins. Most test tables are empty between tests in
this suite.

[cyb]: https://www.cybertec-postgresql.com/en/postgresql-delete-vs-truncate/

This reframes the recommendation: cluster-level fsync tuning does not target the dominant cost. The
dominant cost is **28 sequential ACCESS EXCLUSIVE lock acquisitions plus 28 catalog rewrites plus 28
file-segment unlinks** per test, regardless of fsync setting.

### 4.4 Session replication role trick (Eric Radman, 2022)

[Eric Radman's article on database test isolation][radman] notes that
`SET session_replication_role = 'replica'` during fixture setup suppresses trigger firing. Applied
here it would skip the 18 audit trigger fires on ref-table re-seed; the subsequent
`TRUNCATE system.audit_log` (~20 ms) could then be dropped because the audit log was never written
to.

[radman]: https://eradman.com/posts/database-test-isolation.html

Effect size: ~25 ms per test (the 20 ms TRUNCATE plus the trigger execution cost on 18 INSERTs).
Modest but free.

### 4.5 Industry pattern: hybrid TRUNCATE + SAVEPOINT

Django's `TestCase` (SAVEPOINT, fast) vs `TransactionTestCase` with `serialized_rollback = True`
(TRUNCATE, slow) is the canonical example documented across the Django and Rails ecosystems:

<https://pytest-django.readthedocs.io/en/stable/helpers.html>
<https://jeancochrane.com/blog/django-test-transactions>

SQLAlchemy's recommended pattern is the same shape -- per-test SAVEPOINT rollback as the default,
full TRUNCATE only when the test needs durable trigger output:

  <https://github.com/sqlalchemy/sqlalchemy/discussions/13109>

Marker-driven (`@pytest.mark.audit_assert` or similar). Pro: ~10x on the rollback-safe majority of
the suite. Con: two fixture paths to maintain and a misclassification is a silent test-isolation
bug.

### 4.6 PG 18 file_copy_method = clone (the future)

PG 18 (released 2025) added `file_copy_method = clone` which uses reflink-backed copy on XFS / ZFS /
APFS / btrfs for `CREATE DATABASE ... TEMPLATE`. [boringSQL's benchmark][bsql] shows 6 GB clones in
~212 ms on supported filesystems vs 67 s on PG 17.

[bsql]: https://boringsql.com/posts/instant-database-clones/

This makes **per-test** database cloning feasible. pgtestdb (Go, ~10-30 ms per clone on a tuned
local SSD with PG 16) demonstrates the pattern; pgtestdbpy is the Python port:

  <https://github.com/peterldowns/pgtestdb> <https://pypi.org/project/pgtestdbpy/>

If the entire fixture floor became "clone the template at 30 ms per test" the full suite would be
approximately: 5,148 * 30 ms / 7 effective parallelism = ~22 s.

Cost: PG 18 upgrade plus a reflink-capable filesystem for PGDATA.

### 4.7 Sources cited

- PG 16 Non-Durable Settings (canonical) -- <https://www.postgresql.org/docs/16/non-durability.html>
- PG Populating a Database (wal_level interaction with TRUNCATE) --
  <https://www.postgresql.org/docs/current/populate.html>
- PG WAL Configuration -- <https://www.postgresql.org/docs/current/wal-configuration.html>
- Carbon Health: DB cleanup between tests --
  <https://carbonhealth.com/blog-post/cleaning-postgresql-db-between-integration-tests-efficiently>
- CYBERTEC: DELETE vs TRUNCATE --
  <https://www.cybertec-postgresql.com/en/postgresql-delete-vs-truncate/>
- CYBERTEC: wal_level differences --
  <https://www.cybertec-postgresql.com/en/wal_level-what-is-the-difference/>
- Percona: synchronous_commit options --
  <https://www.percona.com/blog/postgresql-synchronous_commit-options-and-synchronous-standby-replication/>
- Mateus Rauli: fsync vs synchronous_commit TPS --
  <https://dev.to/mateus-rauli/how-fsync-and-synchronouscommit-affect-postgresql-performance-22di>
- Eric Radman: Database Test Isolation (replication_role trick) --
  <https://eradman.com/posts/database-test-isolation.html>
- pytest-django helpers -- <https://pytest-django.readthedocs.io/en/stable/helpers.html>
- Jean Cochrane: Django test transactions -- <https://jeancochrane.com/blog/django-test-transactions>
- SQLAlchemy Discussion #13109 (per-test patterns) --
  <https://github.com/sqlalchemy/sqlalchemy/discussions/13109>
- boringSQL: PG 18 instant clones -- <https://boringsql.com/posts/instant-database-clones/>
- pgtestdb (Go) -- <https://github.com/peterldowns/pgtestdb>
- pgtestdbpy (Python port) -- <https://pypi.org/project/pgtestdbpy/>
- Babak Shandiz: Optimize Postgres Containers for Testing --
  <https://babakks.github.io/article/2024/01/26/re-015-optimize-postgres-containers-for-testing.html>
- deeprave/postgresql-ram (tmpfs example) -- <https://github.com/deeprave/postgresql-ram>
- Fusonic test-with-databases part 3 --
  <https://www.fusonic.net/en/blog/fusonic-test-with-databases-part-3>

---

## 5. Recommendation -- phased

Ordered by ROI per hour of work, with concrete expected outcomes grounded in the profile data above.

### 5.1 Phase A -- quick wins, no test-code rewrite

| Step | Mechanism | Expected per-test saving | Risk |
|---|---|---|---|
| A1.  Swap TRUNCATE CASCADE for `DELETE FROM` in FK order | Avoids 28 ACCESS EXCLUSIVE locks + catalog rewrites on mostly-empty tables | ~230 ms -> ~40-80 ms (per Carbon Health and CYBERTEC) | DELETE leaves sequences unreset; tests do not assert on PK ordering so likely fine.  Verify with the existing 5,148-test suite. |
| A2.  `SET session_replication_role = 'replica'` during `_seed_ref_tables`, RESET after; drop the `TRUNCATE system.audit_log` step | Skip 18 audit trigger fires on seed inserts | ~25 ms (the 20 ms TRUNCATE + ~5 ms trigger execution) | None functional.  Slightly different audit semantics during seed (no log row written) is in fact the *desired* state. |
| A3.  `ALTER SYSTEM SET synchronous_commit = off; SELECT pg_reload_conf();` on the 5433 cluster | Removes the per-commit fsync barrier | ~10-15 ms (the commit fsync) | Last few committed transactions could be lost on a PG crash.  Acceptable on a throwaway cluster used only for tests.  Recovered by re-running the test. |

**Combined expected outcome:** ~281 ms fixture -> ~50-100 ms. Full suite at -n 12 falls from ~4 min
to ~1.5-2 min.

**Engineering effort:** ~2-4 hours including verification.

**Reversibility:** A1 and A2 are conftest-only edits. A3 is a single
`ALTER SYSTEM ... SET synchronous_commit = on` to revert. None touches production code.

### 5.2 Phase B -- hybrid TRUNCATE + SAVEPOINT

Mark the audit-asserting tests with a marker (suggested name: `@pytest.mark.persistent_state` or
`@pytest.mark.audit_assert`); those continue to use the per-test TRUNCATE path. Everything else gets
a SAVEPOINT-rollback path that is roughly 10x faster.

**Mechanism:** Two fixtures. Default `db` becomes SAVEPOINT-rollback; marked tests get the
TRUNCATE-based `db` override.

**Effort:** ~1-3 days including classification of ~5,148 tests. Misclassification is detectable -- a
SAVEPOINT-isolated test that writes through an audit trigger and then asserts on `system.audit_log`
will fail because the audit row is rolled back with the rest.

**Outcome:** Probably another 30-50 % off Phase A's ~1.5-2 min, so ~50-90 s full suite.

**Risk:** Misclassification creates silent test-isolation bugs (a test that "passed" because
rollback hid the bug). Mitigation: the default-deny direction is correct (TRUNCATE is the safe
default; a SAVEPOINT decorator marks a test as not-mutating-durable-state, which the dev opts into
explicitly).

### 5.3 Phase C -- PG 18 + reflink filesystem + per-test TEMPLATE clone

Migrate the test cluster to PG 18 on an XFS / ZFS / btrfs PGDATA so
`CREATE DATABASE ... TEMPLATE shekel_test_template` is reflink-backed (~30 ms per clone instead of
seconds). Replace the per-session clone in `tests/conftest.py::_bootstrap_worker_database` with a
per-test clone.

**Mechanism:** Drop the TRUNCATE+reseed contract entirely. Each test gets a brand-new clone of the
template. Audit-log assertions just work because every test starts from a known-empty log.

**Outcome:** Full suite ~20-40 s at -n 12. Probably the absolute floor without rearchitecting tests.

**Effort:** PG 18 upgrade (the project runs PG 16 in prod and CI; upgrading affects both);
reflink-capable filesystem for the test PGDATA; rewrite the bootstrap + sessionfinish logic to
operate per-test rather than per-session.

**Risk:** PG 18 was released in 2025; not yet the most-tested LTS in many ecosystems. Filesystem
migration is irreversible without a restore. Defer unless Phase A + B do not get you close enough.

---

## 6. What I would not bother with

- **`full_page_writes = off`** -- documented to matter for UPDATE-
  heavy workloads, not for TRUNCATE-of-empty-tables. Cheap to flip
  but the gain is in the noise relative to Phase A's other steps.
- **`fsync = off` (in isolation)** -- on top of `synchronous_commit =
  off` this gains ~7 % per Mateus Rauli's bench. Modest. The TRUNCATE
  problem is not WAL volume, it's per-statement lock + catalog
  overhead.
- **PG data dir on tmpfs (in isolation)** -- meaningful only after
  Phase A has removed the per-statement TRUNCATE overhead. Without
  Phase A, you're moving an I/O ceiling that's already not the
  bottleneck. Worth combining with Phase C but not by itself.
- **Increase `max_wal_size` / `checkpoint_timeout`** -- the test
  cluster's WAL volume is small. Default settings will not trigger
  checkpoint pressure within a 4-minute run. No measurable gain.

---

## 7. Decision

Recommendation **not yet committed**. Operator to choose between Phase A (cheap, fast, low-risk) and
waiting for a larger window for Phase B (architectural, larger payoff).

Phase C is filed away as a future option once PG 18 is more widely deployed and a reflink filesystem
migration is scheduled for other reasons.

### Suggested order of operations if proceeding

1. Implement A1 alone, measure against the same `--durations=0`
    harness. Phase A's first step is the largest single win;
    verifying it independently de-risks the rest.
2. If A1 closes most of the gap, stop and revisit later whether A2/A3
    are worth the complexity.
3. If A1 + A2 + A3 land short of expectations, Phase B becomes the
    natural next investment.

---

## 8. Baseline as of 2026-05-12

Captured after Phase 0 of `test-performance-implementation-plan.md` landed the permanent profile
harness in `tests/conftest.py` (gated on `SHEKEL_TEST_FIXTURE_PROFILE=1`). The 2026-05-11 numbers in
section 3.1 came from throwaway instrumentation that was not preserved; the table below is
reproducible at any time by running:

```bash
SHEKEL_TEST_FIXTURE_PROFILE=1 pytest tests/test_models/ -n 0 -q
```

Same host as the original capture (Arch Linux x86_64, kernel 7.0, btrfs root, PG 16 on
`localhost:5433` in the `shekel-dev-test-db` container). Single-process (`-n 0`) so worker scaling
and the WAL serialisation effects discussed in section 3.3 do not confound the per-step
measurements. 253 tests under `tests/test_models/`.

### 8.1 Per-test phase breakdown (n=253, single-process)

| Step | Avg | p50 | p95 | p99 | Max | % of fixture |
|---|---|---|---|---|---|---|
| rollback | 0.0 ms | 0.0 | 0.0 | 0.0 | 0.1 | 0.0 % |
| **TRUNCATE main 28 tables CASCADE** | **247.1 ms** | 246.2 | 253.3 | 280.7 | 289.5 | **82.9 %** |
| seed_ref re-insert | 19.0 ms | 19.0 | 20.6 | 21.5 | 22.8 | 6.4 % |
| commit_after_seed | 4.8 ms | 4.8 | 5.4 | 6.4 | 7.4 | 1.6 % |
| TRUNCATE system.audit_log | 20.0 ms | 19.9 | 21.2 | 22.2 | 24.2 | 6.7 % |
| refresh_ref_cache | 7.1 ms | 7.1 | 7.9 | 8.3 | 8.6 | 2.4 % |
| **Fixture setup total** | **298.0 ms** | 297.2 | 304.9 | 331.3 | 341.1 | 100 % |
| Test body (call) | 28.2 ms | 22.3 | 80.6 | 91.7 | 110.3 | -- |
| Teardown | 0.1 ms | 0.1 | 0.2 | 0.2 | 0.3 | -- |

### 8.2 Comparison vs. 2026-05-11 capture

The story has not changed. Per-test fixture setup is dominated by `TRUNCATE` of 28 tables (~83 % of
the floor), with `seed_ref` + `TRUNCATE system.audit_log` together a further ~13 %, and the
remaining steps in the noise floor. Numbers are slightly higher than the 2026-05-11 capture (~298 ms
total vs ~281 ms), well within the variation expected between two cold-start measurements on a
shared host -- but reproducible on demand now that the harness is committed.

| Step | 2026-05-11 (n=64) | 2026-05-12 (n=253) | Delta |
|---|---|---|---|
| TRUNCATE main 28 tables | 230.1 ms | 247.1 ms | +17 ms |
| seed_ref | 19.0 ms | 19.0 ms | 0 ms |
| commit_after_seed | 4.9 ms | 4.8 ms | --0.1 ms |
| TRUNCATE audit_log | 20.1 ms | 20.0 ms | --0.1 ms |
| refresh_ref_cache | 7.2 ms | 7.1 ms | --0.1 ms |
| **Fixture total** | **281.2 ms** | **298.0 ms** | **+17 ms** |

The +17 ms drift on the TRUNCATE step is the only line that moved appreciably; PostgreSQL's
per-statement lock + catalog rewrite overhead naturally varies a bit between runs. No drift in any
of the other steps.

### 8.3 Harness reproducibility check (zero-cost when disabled)

The harness's overhead in the default (flag-unset) configuration must remain below the noise floor
of a normal `pytest` run. Re-running the same 253-test slice with `SHEKEL_TEST_FIXTURE_PROFILE`
unset:

| Configuration | Wall-clock | Delta vs. enabled |
|---|---|---|
| `-n 0`, flag set (this capture) | 83.29 s | -- |
| `-n 0`, flag unset | 83.07 s | --0.22 s (--0.26 %) |

Difference is well under the 2 % bound the plan requires. The harness is effectively free when off:
the context-manager-wrapped timer short-circuits on a `None` ``timings`` dict, and the per-test CSV
writer returns immediately on the same check.

### 8.4 Output format

The harness prints a summary table to stdout from ``pytest_sessionfinish``. Under pytest-xdist the
master process aggregates across every worker's CSV; under single-process the single ``main`` worker
is its own aggregator. Raw per-test rows live in ``tests/.fixture-profile/*.csv`` (one file per
worker; gitignored; truncated on every invocation). The format mirrors section 3.1 above so the two
tables can be diffed cell-for-cell.
