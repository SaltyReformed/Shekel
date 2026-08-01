---
paths:
  - "tests/**/*"
---

# Testing rules

Must-knows for the test suite. Full standards: `docs/testing-standards.md`
(infrastructure, run guidelines, problem reporting, the catalog-fragmentation
rationale).

## Running

- Invoke via `./scripts/test.sh`, never bare `pytest` -- the wrapper restarts the
  `shekel-dev-test-db` container first. `SKIP_DB_RESTART=1` on chained follow-ups.
- Single file/test for fast feedback:
  `./scripts/test.sh tests/path/test_file.py::test_name -v`.
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
