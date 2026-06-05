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

## What a test must assert

- **Route tests assert response content, not just the status code:** correct
  records present, financial amounts correct, right template / HTMX fragment.
- **Service tests assert exact computed values** -- include a comment showing the
  arithmetic that produces the expected number, not `result > 0`.
- **Edge-case tests assert the specific edge behavior**, not just "did not crash."
- **Decimals from strings** in assertions -- `Decimal("12.34")` (gate:
  `shekel-decimal-from-float` runs on `tests/`).
- Every test has a docstring; test behavior, not implementation.
