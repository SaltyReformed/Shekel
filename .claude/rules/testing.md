---
paths:
  - "tests/**/*"
---

# Testing rules

Must-knows for the test suite. Full standards: `docs/testing-standards.md`
(infrastructure, run guidelines, problem reporting). The `code-reviewer` agent
restates the test-quality bar deliberately, so a review carries it even where
this file has not loaded; a change here updates that mirror in the same commit.

## Running

- Invoke via `./scripts/test.sh`, never bare `pytest`. **It gives the run a POSTGRES CLUSTER
  OF ITS OWN** (`balance:X-br-4`) -- a container started from the image
  `scripts/build_test_db_image.py` bakes, on a rootless docker daemon, reached over a unix
  socket in a per-run directory and removed on every exit path including Ctrl-C. It exports
  the test DSNs at that socket and defaults the marker expression. Two runs therefore never
  meet, in one worktree or in many, which is why the restart flag, the live-backend probe,
  `TEST_DB_PREFIX` and `TEST_TEMPLATE_DATABASE` are all gone rather than merely quieter.
- **A migration needs no manual template rebuild.** The template is baked into the image and
  the wrapper re-verifies it on EVERY invocation, rebuilding when the cache key moved. Only
  CI and the image builder run `scripts/build_test_template.py` directly.
- **It REFUSES a daemon that is not rootless**, rather than falling back. A container per run
  on the system daemon is exactly the churn `docs/test-harness-isolation.md` exists to stop:
  that daemon runs the production database and the homelab wud/cadvisor/alloy stack watches
  every container on it. Start the isolated one with
  `systemctl --user start docker.service`; `SHEKEL_ALLOW_HOST_DOCKER=1` accepts the churn
  deliberately, and CI sanctions its own throwaway daemon.
- **It also defaults to `-m "not docker"`, which DESELECTS 28 container-spawning
  `tests/test_deploy` tests** -- deselected, not skipped, so they leave NO line in the
  report and a green run says nothing about them; CI runs bare `pytest` and executes all
  28. Locally the opt-in is now just `PYTEST_MARKER_EXPR=docker ./scripts/test.sh
  tests/test_deploy`, because the wrapper already exports an isolated `DOCKER_HOST` and the
  conftest guard sees it. Measured 2026-09-05: 25 passed, 3 skipped on the rootless daemon
  against 28 skipped on the system one; the 3 are a published-port collision in the nginx
  fixtures, which SKIP rather than fail, so that defect thins a green suite silently.
- Single file/test for fast feedback:
  `./scripts/test.sh tests/path/test_file.py::test_name -v`.
- **Concurrent worktrees: nothing to take, and nothing to release.** `scripts/suite_slot.sh`
  is deleted (`balance:X-br-4`). Correctness coupling is structural now: no run can restart
  another's postmaster and none can collide on a database name, because there is no shared
  postmaster. **What survives is CONTENTION, and it is a resource fact rather than a
  defect** -- the cores do not multiply. Measured 2026-09-05 on this 24-core host: one suite
  alone finished in 349 s, while with THREE running two of them reached ~38% in 13 minutes,
  at a run-queue of 32 and 28% iowait. **No test failed in either**; the slowest single test
  is 2.58 s against `pytest.ini`'s 30 s per-test timeout, so the headroom is roughly 11x and
  that measurement sat on it. So the wrapper REPORTS rather than serialises: it prints any
  other live pytest with its worktree and proceeds. **Read the cwd, never the argv** -- every
  worktree here shares one venv, so a peer's command line names the main checkout whatever
  tree it is testing. Waiting is a courtesy you owe a peer's gating run, not a protocol.
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
