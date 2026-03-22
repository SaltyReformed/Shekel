"""
Shekel Budget App -- Interest Projection Service

Pure function that calculates projected interest earned on a HYSA
account during a pay period.  No database access, no side effects.
"""

import calendar
from decimal import Decimal, ROUND_HALF_UP

ZERO = Decimal("0.00")
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

    Args:
        balance: Account balance after all transactions/transfers for the period.
        apy: Annual percentage yield (e.g., Decimal("0.04500") for 4.5%).
        compounding_frequency: One of 'daily', 'monthly', 'quarterly'.
        period_start: Start date of the pay period.
        period_end: End date of the pay period.

    Returns:
        Decimal interest earned, rounded to 2 decimal places.
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
        days_in_quarter = Decimal("91")
        interest = balance * quarterly_rate * (period_days / days_in_quarter)
    else:
        return ZERO

    return interest.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
