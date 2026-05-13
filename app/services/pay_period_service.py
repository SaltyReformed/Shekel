"""
Shekel Budget App -- Pay Period Service

Generates, extends, and queries biweekly pay periods.  Each period
is defined by a start_date (payday) and end_date (day before next
payday).
"""

import logging
from datetime import date, timedelta

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.exceptions import ValidationError
from app.utils.log_events import (
    BUSINESS,
    EVT_PAY_PERIODS_GENERATED,
    log_event,
)

logger = logging.getLogger(__name__)


def generate_pay_periods(user_id, start_date, num_periods=52, cadence_days=14):
    """Generate a series of pay periods for a user.

    Existing periods for this user are checked to avoid duplicates.
    New periods are appended starting from the next available index.

    Args:
        user_id:       The owning user's ID.
        start_date:    The first payday (date object).
        num_periods:   How many periods to generate (default 52 = ~2 years).
        cadence_days:  Days between paydays (default 14 = biweekly).

    Returns:
        List of newly created PayPeriod objects.

    Raises:
        ValidationError: If start_date is not a date or cadence is invalid.
    """
    if not isinstance(start_date, date):
        raise ValidationError("start_date must be a date object.")
    if cadence_days < 1:
        raise ValidationError("cadence_days must be at least 1.")

    # Find the highest existing period_index for this user.
    max_index = (
        db.session.query(db.func.max(PayPeriod.period_index))
        .filter_by(user_id=user_id)
        .scalar()
    )
    next_index = 0 if max_index is None else max_index + 1

    # Check if any existing periods overlap with the requested range.
    existing_starts = set(
        row[0]
        for row in db.session.query(PayPeriod.start_date)
        .filter_by(user_id=user_id)
        .all()
    )

    created = []
    current_start = start_date
    assigned_index = next_index  # Track separately to avoid gaps.
    for _ in range(num_periods):
        # Skip if this start_date already exists.
        if current_start in existing_starts:
            current_start += timedelta(days=cadence_days)
            continue

        end = current_start + timedelta(days=cadence_days - 1)
        period = PayPeriod(
            user_id=user_id,
            start_date=current_start,
            end_date=end,
            period_index=assigned_index,
        )
        db.session.add(period)
        created.append(period)
        assigned_index += 1
        current_start += timedelta(days=cadence_days)

    db.session.flush()  # Assign IDs without committing.
    log_event(
        logger, logging.INFO, EVT_PAY_PERIODS_GENERATED, BUSINESS,
        "Pay periods generated",
        user_id=user_id,
        count=len(created),
        start_date=start_date.isoformat(),
        cadence_days=cadence_days,
    )
    return created


def get_current_period(user_id, as_of=None):
    """Return the pay period that contains the given date.

    Args:
        user_id: The user's ID.
        as_of:   The reference date (default: today).

    Returns:
        The matching PayPeriod, or None if no period covers that date.
    """
    if as_of is None:
        as_of = date.today()

    return (
        db.session.query(PayPeriod)
        .filter(
            PayPeriod.user_id == user_id,
            PayPeriod.start_date <= as_of,
            PayPeriod.end_date >= as_of,
        )
        .first()
    )


def get_periods_in_range(user_id, start_index, count):
    """Return a window of pay periods by index.

    Args:
        user_id:     The user's ID.
        start_index: The first period_index to include.
        count:       Number of periods to return.

    Returns:
        List of PayPeriod objects ordered by period_index.
    """
    return (
        db.session.query(PayPeriod)
        .filter(
            PayPeriod.user_id == user_id,
            PayPeriod.period_index >= start_index,
            PayPeriod.period_index < start_index + count,
        )
        .order_by(PayPeriod.period_index)
        .all()
    )


def get_all_periods(user_id):
    """Return all pay periods for a user, ordered by index.

    Args:
        user_id: The user's ID.

    Returns:
        List of PayPeriod objects.
    """
    return (
        db.session.query(PayPeriod)
        .filter_by(user_id=user_id)
        .order_by(PayPeriod.period_index)
        .all()
    )


def get_next_period(period):
    """Return the pay period immediately following the given one.

    Args:
        period: A PayPeriod object.

    Returns:
        The next PayPeriod, or None if it doesn't exist.
    """
    return (
        db.session.query(PayPeriod)
        .filter(
            PayPeriod.user_id == period.user_id,
            PayPeriod.period_index == period.period_index + 1,
        )
        .first()
    )


def get_overlapping_periods(
    user_id: int,
    first_day: date,
    last_day: date,
) -> list[PayPeriod]:
    """Return all pay periods that overlap a calendar date range.

    A period overlaps ``[first_day, last_day]`` when
    ``start_date <= last_day`` AND ``end_date >= first_day``.  The
    range is inclusive on both ends.

    Used by the calendar and budget-variance services to find the
    pay periods that need to be inspected when reporting on a
    calendar month or year window.  Centralised here so a future
    change (e.g. excluding inactive scenarios' periods, or adding a
    second index ordering) is a single edit rather than chasing the
    inline copies the audit's Issue 1 noted.

    Args:
        user_id: The user whose pay periods to consider.
        first_day: Inclusive lower bound of the date range.
        last_day: Inclusive upper bound of the date range.

    Returns:
        List of :class:`PayPeriod` objects, ordered by
        ``period_index`` ascending.  Empty list when no pay period
        overlaps the range.
    """
    return (
        db.session.query(PayPeriod)
        .filter(
            PayPeriod.user_id == user_id,
            PayPeriod.start_date <= last_day,
            PayPeriod.end_date >= first_day,
        )
        .order_by(PayPeriod.period_index)
        .all()
    )
