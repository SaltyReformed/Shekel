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

- **Full suite:** ~12 minutes (3100+ tests). Always use `timeout 720`.
- **During development:** Run only relevant test files. Typically under 30 seconds.
- **Before reporting done:** Full suite once: `timeout 720 pytest -v --tb=short`.
- **If tests appear stuck:** Wait for the full timeout. The slowest test is ~3 seconds.
- **MFA/auth tests are slow** (~1-3s each) due to bcrypt hashing. This is expected.

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
