"""
Shekel Budget App -- Interest Projection Service

Pure function that calculates projected interest earned on a HYSA
account during a pay period.  No database access (the compounding
frequency is resolved against the in-memory ``ref_cache``), no side
effects.

Day-count convention -- actual/actual for leap-day-crossing windows
-------------------------------------------------------------------

The daily compounding formula divides the APY by the actual number of
days in the projection year.  For a projection window that contains a
Feb 29 (a leap day) the divisor is 366; otherwise the divisor is 365.
The window-aware switch closes the residual error documented under the
prior accepted-simplification path (audit MED-05 / PA-06): a 14-day
window crossing the leap day used to overstate daily interest by
approximately 1/365 (~0.27%, ~$0.47 per $100,000 at 4.5% APY for the
14-day window, ~$0.25 per $100,000 across a full leap year) because
actual 366 days were divided by a fixed 365-day denominator.

The leap-day check uses the half-open interval ``[period_start,
period_end)`` so a window that ends exactly on Feb 29 does not count as
crossing it (period_end is the open boundary the rest of this module
already treats as exclusive in its ``(period_end - period_start).days``
computation).  Windows entirely within a leap year that do not cross
Feb 29 (e.g. a Feb 15-Feb 28 window in 2028) keep the 365-day divisor:
the daily-rate error vanishes when the window does not include the
extra calendar day, so the actual/actual switch is calibrated to the
window's content rather than its enclosing year.

Monthly and quarterly compounding are unaffected.  Both already use
calendar-correct day counts for the period numerator (``calendar.
monthrange`` for monthly, computed quarter length for quarterly);
neither passes through the 365-day divisor that this fix replaces.
"""

import calendar
from datetime import date as date_cls
from decimal import Decimal

from app import ref_cache
from app.enums import CompoundingFrequencyEnum
from app.utils.money import MONTHS_PER_YEAR, round_money

ZERO = Decimal("0.00")
# Actual/actual day count, evaluated per projection window.  See module
# docstring "Day-count convention -- actual/actual for leap-day-crossing
# windows" for the full rationale (MED-05 / PA-06).
DAYS_IN_YEAR_NON_LEAP = Decimal("365")
DAYS_IN_YEAR_LEAP = Decimal("366")
QUARTERS_IN_YEAR = Decimal("4")


def _days_in_year_for_window(period_start, period_end):
    """Return 366 if ``[period_start, period_end)`` contains a Feb 29, else 365.

    Iterates years touched by the half-open window and asks
    ``calendar.isleap`` for each.  When the window straddles a year
    boundary (e.g. Dec 25 -> Jan 8) and only one of the two years has a
    Feb 29 inside the window, that single calendar year's leap day is
    enough to trigger 366; this matches the actual day count of the
    window itself, which is what the daily-rate divisor needs.

    Args:
        period_start: inclusive start date of the projection window.
        period_end: exclusive end date of the projection window
            (matches the rest of this module's ``(period_end -
            period_start).days`` convention).

    Returns:
        :data:`DAYS_IN_YEAR_LEAP` (366) when at least one Feb 29 falls
        within ``[period_start, period_end)``, otherwise
        :data:`DAYS_IN_YEAR_NON_LEAP` (365).
    """
    for year in range(period_start.year, period_end.year + 1):
        if not calendar.isleap(year):
            continue
        leap_day = date_cls(year, 2, 29)
        if period_start <= leap_day < period_end:
            return DAYS_IN_YEAR_LEAP
    return DAYS_IN_YEAR_NON_LEAP


def _days_in_quarter(period_start):
    """Return the day-count of the calendar quarter containing ``period_start``.

    Uses the actual quarter length (90-92 days) derived from the quarter's
    start/end boundary dates rather than a hardcoded 91-day approximation
    (L-05).  Parallels :func:`_days_in_year_for_window` -- both compute the
    actual-period divisor for their compounding frequency.
    """
    q_start_month = ((period_start.month - 1) // 3) * 3 + 1
    q_start = date_cls(period_start.year, q_start_month, 1)
    next_q_month = q_start_month + 3
    if next_q_month > 12:
        q_end = date_cls(period_start.year + 1, next_q_month - 12, 1)
    else:
        q_end = date_cls(period_start.year, next_q_month, 1)
    return Decimal(str((q_end - q_start).days))


def calculate_interest(
    balance,
    apy,
    compounding_frequency_id,
    period_start,
    period_end,
):
    """Calculate projected interest earned during a pay period.

    Daily compounding uses an actual/actual day count: the divisor is
    366 when the projection window contains Feb 29 and 365 otherwise.
    See the module docstring ("Day-count convention -- actual/actual
    for leap-day-crossing windows") for the rationale and the residual
    error this closes (MED-05 / PA-06).  Monthly and quarterly
    compounding are unaffected.

    Args:
        balance: Account balance after all transactions/transfers for the period.
        apy: Annual percentage yield (e.g., Decimal("0.04500") for 4.5%).
        compounding_frequency_id: ``ref.compounding_frequencies.id`` of
            the account's compounding frequency (resolved against
            ``ref_cache``; #38).
        period_start: Start date of the pay period.
        period_end: End date of the pay period.

    Returns:
        Decimal interest earned, rounded to 2 decimal places via
        :func:`app.utils.money.round_money` (``ROUND_HALF_UP``).
        Returns :data:`ZERO` for non-positive balances, non-positive
        APY, inverted ``period_start`` / ``period_end`` ordering, or an
        unrecognised ``compounding_frequency_id``.
    """
    balance = Decimal(str(balance))
    apy = Decimal(str(apy))

    if balance <= 0 or apy <= 0 or period_start >= period_end:
        return ZERO

    period_days = Decimal(str((period_end - period_start).days))

    if compounding_frequency_id == ref_cache.compounding_frequency_id(
        CompoundingFrequencyEnum.DAILY
    ):
        days_in_year = _days_in_year_for_window(period_start, period_end)
        daily_rate = apy / days_in_year
        interest = balance * ((1 + daily_rate) ** period_days - 1)
    elif compounding_frequency_id == ref_cache.compounding_frequency_id(
        CompoundingFrequencyEnum.MONTHLY
    ):
        monthly_rate = apy / MONTHS_PER_YEAR
        days_in_month = Decimal(
            str(calendar.monthrange(period_start.year, period_start.month)[1])
        )
        interest = balance * monthly_rate * (period_days / days_in_month)
    elif compounding_frequency_id == ref_cache.compounding_frequency_id(
        CompoundingFrequencyEnum.QUARTERLY
    ):
        quarterly_rate = apy / QUARTERS_IN_YEAR
        days_in_quarter = _days_in_quarter(period_start)
        interest = balance * quarterly_rate * (period_days / days_in_quarter)
    else:
        return ZERO

    return round_money(interest)
