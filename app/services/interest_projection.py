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
from app.utils.money import MONTHS_PER_YEAR

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


def accrued_interest(
    balance,
    apy,
    compounding_frequency_id,
    period_start,
    period_end,
):
    """Return the interest accrued over ``[period_start, period_end)``, UNROUNDED.

    **Daily compounding uses an actual/actual day count: the divisor is 366
    when the projection window contains Feb 29 and 365 otherwise.**  See the
    module docstring ("Day-count convention -- actual/actual for
    leap-day-crossing windows") for the rationale and the residual error this
    closes (MED-05 / PA-06).  Monthly and quarterly compounding are unaffected.
    That paragraph lived on the cent-rounding wrapper ``calculate_interest``
    until plan step X-g4b deleted it; the rule it describes has always been
    THIS function's, and a caller reading only the function docstring -- the
    common case in an editor -- must still learn it here.

    The day-count rule -- the actual/actual daily divisor, the calendar-month
    monthly divisor, the actual-length quarterly divisor -- stated ONCE.

    **There is exactly ONE consumer, and the sibling that made it two was
    deleted at plan step X-g4b.**  The balance seam's modelled asset fold
    (``balance_at._asset_fold``) accrues DAILY (plan ruling R-T) and credits
    whole cents off a FULL-PRECISION running total (ruling R-X), so it needs
    the sub-cent amount this returns rather than a rounded one.  Its sibling
    ``calculate_interest`` was a two-line ``round_money`` wrapper serving the
    per-PERIOD interest layer, and X-g4b deleted that layer -- leaving a public
    production function whose only readers were its own unit tests, which is
    the residue this arc's deletions exist to remove rather than inherit.  The
    rounding it applied is ``round_money``, applied by whoever needs cents.
      Rounding each day independently is what would make a small
      balance accrue nothing at all forever -- 0.45 cents a day on a
      $50 HYSA at 3.29% APY rounds to zero every day.

    Extracting it is what keeps the daily reader from restating a
    financial rule: a second copy of the leap-day switch or the
    calendar-month divisor is exactly where the two would disagree.

    Args:
        balance: Account balance the interest accrues on.
        apy: Annual percentage yield (e.g., Decimal("0.04500") for 4.5%).
        compounding_frequency_id: ``ref.compounding_frequencies.id`` of
            the account's compounding frequency (resolved against
            ``ref_cache``; #38).
        period_start: Inclusive start date of the accrual window.
        period_end: EXCLUSIVE end date of the accrual window (the
            ``(period_end - period_start).days`` convention this module
            uses throughout).

    Returns:
        Decimal interest earned at full precision -- NOT cent-quantized.
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
        return balance * ((1 + daily_rate) ** period_days - 1)
    if compounding_frequency_id == ref_cache.compounding_frequency_id(
        CompoundingFrequencyEnum.MONTHLY
    ):
        monthly_rate = apy / MONTHS_PER_YEAR
        days_in_month = Decimal(
            str(calendar.monthrange(period_start.year, period_start.month)[1])
        )
        return balance * monthly_rate * (period_days / days_in_month)
    if compounding_frequency_id == ref_cache.compounding_frequency_id(
        CompoundingFrequencyEnum.QUARTERLY
    ):
        quarterly_rate = apy / QUARTERS_IN_YEAR
        days_in_quarter = _days_in_quarter(period_start)
        return balance * quarterly_rate * (period_days / days_in_quarter)
    return ZERO
