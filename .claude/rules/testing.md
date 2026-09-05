---
paths:
  - "tests/**/*"
---

# Testing rules

Must-knows for the test suite. Full standards: `docs/testing-standards.md`
(infrastructure, run guidelines, problem reporting, the catalog-fragmentation
rationale). The `code-reviewer` agent restates the test-quality bar
deliberately, so a review carries it even where this file has not loaded; a
change here updates that mirror in the same commit.

## Running

- Invoke via `./scripts/test.sh`, never bare `pytest` -- it resolves the test DSNs out of
  `.env` and defaults the marker expression. **It does NOT restart the shared
  `shekel-dev-test-db` container unless `RESTART_TEST_DB` is set to `1`/`true`/`yes`/`on`**
  (inverted 2026-09-04; the old opt-out spelling `SKIP_DB_RESTART` is gone, not merely
  redundant). `0`/`false`/`no`/`off` mean no restart and anything else is REFUSED with exit
  2 -- deliberately not a bare presence flag, because the spelling this replaced was
  opt-OUT, so a careless `=0` used to land on "skip" and must not now land on "restart the
  shared container". The restart is fragmentation hygiene, not a correctness gate: ask for
  it before a gating full-suite run. A run that skips the restart reports the container's
  state, one of five -- its uptime when it is up, and otherwise which of docker-absent,
  container-absent, container-paused or container-not-running it found. Paused splits out
  of UP, not out of not-running: docker reports it as `Up ... (Paused)`, so it would
  otherwise read as healthy while pytest hangs on a SIGSTOPped postmaster. That is the only drift signal there is:
  `docs/testing-standards.md` withdraws the `~15 ms` CREATE/DROP cutoff that once served as
  the trigger, as self-refuting.
- **It also defaults to `-m "not docker"`, which DESELECTS 28 container-spawning
  `tests/test_deploy` tests** -- deselected, not skipped, so they leave NO line in the
  report and a green run says nothing about them; CI runs bare `pytest` and executes all
  28. Run bare `pytest` to see them as skips; to actually run them, point `DOCKER_HOST` at
  an isolated daemon rather than `SHEKEL_ALLOW_HOST_DOCKER=1`, which accepts container
  churn on the production daemon the homelab stack watches.
- Single file/test for fast feedback:
  `./scripts/test.sh tests/path/test_file.py::test_name -v`.
- **Concurrent worktrees: take the slot first.** `./scripts/suite_slot.sh acquire <name>`,
  then `./scripts/test.sh`, then `release <name>`. The template and the worker databases
  are isolated per worktree, but the POSTMASTER is shared, in two separate ways. **The
  restart** kills every backend on the container: `RESTART_TEST_DB=1` attempts one, and the
  live-backend probe skips it when another run's connections are visible -- but the probe is
  a race, it is blind to a run whose only connections sit on the excluded admin database,
  and a probe is not a lock, so a slotless `RESTART_TEST_DB=1` run can still kill an in-flight
  one. **Contention** needs no restart at all to void both runs: measured 859 s against 304
  s alone. So the default (no restart) removes one hazard and leaves the other, and the slot
  stays mandatory whatever `RESTART_TEST_DB` says. `acquire` exits 2 and releases what it took
  when a pytest is already live -- it guards the START of a run, so one in flight can only
  be coordinated, not protected. `release` frees the lock even on a name mismatch, so copy
  your name exactly. **Only the HOLDER releases** -- a lock held 600 s with nothing running
  looks identical to one whose holder is slow to start, so `status` calls a lock
  possibly-stale only past 900 s AND with no pytest anywhere, and says to ask the holder
  even then. `--collect-only` is exempt both directions.
- **Zero tolerance:** every batch must end in `<N> passed`. Any `failed`,
  `errors`, or unexpected `xfailed` blocks a "done" report -- investigate, do not
  dismiss as "pre-existing" (rule 4). Show the actual pass/fail summary as evidence.

## Setup and isolation

- **Use existing fixtures** from `conftest.py` (`seed_user`, `seed_second_user`,
  `auth_client`, `second_auth_client`, ...). No ad-hoc user setup in test methods.
- **Check for existing coverage** before writing a new test.
- Tests are **independent** -- each sets up its own preconditions, no ordering or
  shared mutable state. Tests that mutate cluster state use `@pytest.mark.xdist_group`.

## The ambient clock and calendar (a test that fails on some days is BROKEN, not flaky)

- **Use the app's clock, never the process's.** `date.today()` reads the PROCESS
  timezone; the app's civil day is `app.utils.dates.display_today()`. Prod and dev
  pin `TZ: America/New_York`, CI does NOT -- so anything a test builds that the app
  compares against its own "today" (a posted `entry_date`, a period window that must
  contain the current day, an `observed_on`) must use `display_today()`. Never fix
  this by pinning CI's timezone; that hides the coupling.
- **A fixture must not depend on where in the calendar it runs.** State the property
  the fixture needs and construct it so it holds on every day -- pin the read
  (`BalanceContext.build(user_id, as_of=...)`) to a date the fixture controls and
  derive the rest from that. Findings N-131, N-132, R8 and the 2026-08-01 loan
  failures are all this shape.
- **Check both:** `TZ=Pacific/Kiritimati ./scripts/test.sh` must pass unchanged.
  Full rationale and worked cases: `docs/testing-standards.md`; the gates, the
  `server_clock` marker and the debugging pitfalls: `docs/test-suite-clocks.md`.

## What a test must assert

- **Route tests assert response content, not just the status code:** correct
  records present, financial amounts correct, right template / HTMX fragment.
- **Service tests assert exact computed values** -- include a comment showing the
  arithmetic that produces the expected number, not `result > 0`.
- **Edge-case tests assert the specific edge behavior**, not just "did not crash."
- **Decimals from strings** in assertions -- `Decimal("12.34")` (gate:
  `shekel-decimal-from-float` runs on `tests/`).
- Every test has a docstring; test behavior, not implementation.
