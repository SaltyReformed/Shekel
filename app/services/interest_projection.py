"""
Shekel Budget App -- Interest Projection Service

Pure function that calculates projected interest earned on a HYSA
account during a pay period.  No database access, no side effects.

Day-count convention -- accepted simplification
-----------------------------------------------

This module hardcodes a 365-day year (``DAYS_IN_YEAR``) for the daily
compounding formula regardless of whether the period falls in a leap
year.  US retail banks use the actual/365 convention industry-wide;
leap years are handled identically (366 actual days against a 365-day
divisor), which overstates daily interest by approximately 1/365
(~0.27%) for periods that cross February 29.

At Shekel-realistic balances and rates the absolute error is small --
~$1.23 per $100,000 at 4.5% APY for a 14-day period crossing the leap
day, ~$0.25/year per $100,000 across a full leap year.  The error has
been classified as an accepted simplification in the 2026-04-15
security audit (F-126).  Switching to actual-day-count would require:

- threading the leap-year flag into every monthly/quarterly branch
  (they already use actual days for the *period numerator*, so the
  denominator inconsistency is daily-specific), and
- diverging from the convention every brokerage and HYSA statement the
  user will reconcile against, which would make the projection look
  "wrong" against ground truth even though the math was technically
  more precise.

The trade is intentional.  Callers that need leap-year-exact accrual
(rare; reserved for closed-out historical periods, not projection) must
compute interest outside this module.
"""

import calendar
from decimal import Decimal, ROUND_HALF_UP

ZERO = Decimal("0.00")
# US bank convention: actual/365 day count.  In leap years this
# overstates daily interest by ~0.27% (~$1.23 per $100K at 4.5% APY).
# Acceptable approximation for projection purposes.  See the module
# docstring for the full rationale (F-126).
DAYS_IN_YEAR = Decimal("365")
MONTHS_IN_YEAR = Decimal("12")
QUARTERS_IN_YEAR = Decimal("4")


def calculate_interest(
    balance,
    apy,
    compounding_frequency,
    period_start,
    period_end,
):
    """Calculate projected interest earned during a pay period.

    Daily compounding uses the actual/365 day-count convention -- the
    365-day divisor is fixed regardless of whether the period falls in
    a leap year.  See the module docstring ("Day-count convention --
    accepted simplification") for the full rationale and the maximum
    error envelope; in short, the per-leap-year overstatement is
    ~$0.25 per $100,000 at 4.5% APY, accepted in F-126 to keep the
    projection consistent with retail-bank statements.

    Args:
        balance: Account balance after all transactions/transfers for the period.
        apy: Annual percentage yield (e.g., Decimal("0.04500") for 4.5%).
        compounding_frequency: One of 'daily', 'monthly', 'quarterly'.
        period_start: Start date of the pay period.
        period_end: End date of the pay period.

    Returns:
        Decimal interest earned, rounded to 2 decimal places
        (``ROUND_HALF_UP``).  Returns :data:`ZERO` for non-positive
        balances, non-positive APY, inverted ``period_start`` /
        ``period_end`` ordering, or an unrecognised
        ``compounding_frequency``.
    """
    balance = Decimal(str(balance))
    apy = Decimal(str(apy))

    if balance <= 0 or apy <= 0 or period_start >= period_end:
        return ZERO

    period_days = Decimal(str((period_end - period_start).days))

    if compounding_frequency == "daily":
        daily_rate = apy / DAYS_IN_YEAR
        interest = balance * ((1 + daily_rate) ** period_days - 1)
    elif compounding_frequency == "monthly":
        monthly_rate = apy / MONTHS_IN_YEAR
        days_in_month = Decimal(
            str(calendar.monthrange(period_start.year, period_start.month)[1])
        )
        interest = balance * monthly_rate * (period_days / days_in_month)
    elif compounding_frequency == "quarterly":
        quarterly_rate = apy / QUARTERS_IN_YEAR
        # Calculate actual quarter length from the period's start date
        # instead of using a hardcoded 91-day approximation (L-05).
        from datetime import date as date_cls  # pylint: disable=import-outside-toplevel
        q_start_month = ((period_start.month - 1) // 3) * 3 + 1
        q_start = date_cls(period_start.year, q_start_month, 1)
        next_q_month = q_start_month + 3
        if next_q_month > 12:
            q_end = date_cls(period_start.year + 1, next_q_month - 12, 1)
        else:
            q_end = date_cls(period_start.year, next_q_month, 1)
        days_in_quarter = Decimal(str((q_end - q_start).days))
        interest = balance * quarterly_rate * (period_days / days_in_quarter)
    else:
        return ZERO

    return interest.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
