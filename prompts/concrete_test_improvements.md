Read docs/test_audit_report.md (the audit from Part 1). For every P0 and
P1 finding, write concrete new tests or strengthen existing ones. Follow
these rules:

REALISTIC DATA VOLUME TESTS:

- For balance_calculator: create a test with 52 pay periods, 15-20
  transactions per period (mix of income, expense, credit, done, projected,
  cancelled), 3-4 transfer templates, and verify every single period's
  balance to the penny using Decimal. This simulates a real user's year.
- For recurrence_engine: test with all 8 recurrence patterns active
  simultaneously across 52 periods, with overrides and deletions scattered
  throughout. Verify exact transaction counts and placement.
- For paycheck_calculator: test a full year (26 paychecks) with mid-year
  raise, changing deductions, and 3rd-paycheck months. Verify cumulative
  wage tracking is correct at every paycheck.

ASSERTION DEPTH:

- Every route test that currently only checks status_code must also verify:
  (a) the response HTML contains expected data values (not just template
  structure), (b) the database row was actually created/modified/deleted
  with correct field values, (c) related records were updated (e.g.,
  creating a transfer also affects both account balances).

FINANCIAL PRECISION:

- Add a dedicated test class TestDecimalPrecision in each P0 service test
  file that verifies no floating point contamination occurs. Use values
  like Decimal("0.01"), Decimal("999999.99"), and Decimal("0.10") +
  Decimal("0.20") == Decimal("0.30") patterns.

STATE MACHINE COMPLETENESS:

- For transaction status workflow (projected -> done|credit|cancelled,
  done|received -> settled), test every valid transition AND every
  invalid transition. Invalid transitions should raise or return an error,
  not silently succeed.

All new tests must have docstrings, inline comments on non-obvious
assertions, use Decimal (never float), and conform to Pylint standards.
Put new tests in the appropriate existing test files, not new files.
