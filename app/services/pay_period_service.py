"""
Shekel Budget App -- Pay Period Service

Generates, extends, and queries biweekly pay periods.  Each period
is defined by a start_date (payday) and end_date (day before next
payday).
"""

import logging
from datetime import date, timedelta

from sqlalchemy import or_

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.exceptions import ValidationError
from app.utils.dates import display_today
from app.utils.log_events import (
    BUSINESS,
    EVT_PAY_PERIODS_GENERATED,
    log_event,
)

logger = logging.getLogger(__name__)



def earliest_recordable_day(user_id: int) -> date:
    """Return the earliest civil day this user's app can honestly date money at.

    ``min(the user's earliest pay period start, today)``.  Taking the EARLIER of
    the two is what keeps the bound from refusing a legitimate entry: a user
    whose periods are all still in the future must be able to record what
    happened today, while nobody may back-date into a past the app has no
    schedule for.

    **It has TWO SERVICE consumers, and it lives here so they cannot drift.**

    * ``anchor_service.resolve_observation_day`` -- an anchor's ``observed_on``,
      for BOTH writers of ``AccountAnchorHistory`` (the account factory's
      origination assertion and the true-up door's).  An unbounded day opens the
      modelled-return window (``balance_at._asset_fold._AccrualWindow``
      materialises EVERY calendar day from it) and fabricates contribution
      history back to it (finding **N-133**).
    * ``status_seam.reject_settle_day_before_the_schedule`` -- a settle day
      (plan step X-f1c, ruling **R-EL**).  An unbounded day is absorbed into the
      opening assertion by ``cash_ledger._walk``, which then resets the running
      total to the asserted balance -- so the row's money silently leaves the
      projection while the row still reads Paid.

    **Four ROUTE consumers read it too, and they are a different use**: the two
    settle-day inputs (``routes/transactions/forms``, ``routes/transfers/forms``)
    and the two anchor date inputs (``routes/accounts/crud.new_account``,
    ``routes/accounts/anchor._anchor_day_bounds``) set an input's ``min`` from
    it so the browser refuses what the service would refuse.  That is a
    convenience and never the guard -- an input bound is captured at RENDER time
    and this floor moves whenever pay periods are generated or truncated.

    It was ``account_service.earliest_observable_day`` until X-f1c needed the
    same bound one module lower.  This module is the right home: the rule is a
    PAY-PERIOD SCHEDULE question with no account in it, and living here keeps it
    reachable from ``status_seam``, which must stay below the services that call
    it.  **The first bullet named ``account_service._reject_undatable_observation``
    and this paragraph named ``account_service.earliest_observable_day`` until
    plan step X-f1c4c deleted both** (ruling R-ER moved the rule to the module
    that owns what an assertion is); three independent reviews of that step
    found this docstring still naming them.

    Args:
        user_id: The owner whose schedule sets the floor.

    Returns:
        The earliest recordable civil day.  Today when the user has no pay
        periods at all -- every caller's own operation then fails on the missing
        schedule, which is a clearer error than a date bound.
    """
    today = display_today()
    earliest = (
        db.session.query(db.func.min(PayPeriod.start_date))
        .filter(PayPeriod.user_id == user_id)
        .scalar()
    )
    if earliest is None:
        return today
    return min(earliest, today)

def _reject_overlapping_batch(existing_periods, new_starts):
    """Reject a batch whose earliest new payday overlaps existing coverage.

    Forward-only invariant (DH-#39): new periods are appended with the
    highest ``period_index`` values, so their start dates MUST fall after
    every existing period's COVERAGE, not merely after the latest existing
    start.  Otherwise ``period_index`` order stops matching calendar
    order.  The cash fold indexes a day's column by DATE
    (``balance_at._cash_fold._PeriodSpans``, a bisect over sorted
    ``start_date``) so it would place a flow in the wrong column rather than
    skip it.  The index-ordered anchor-forward walks that used to SKIP such a
    period outright -- silently dropping its transactions -- were deleted at
    plan step X-g4b, so the date-keyed misplacement is now the only failure
    mode, and it is the reason this batch is rejected rather than reshuffled.  A start date that
    lands ON or WITHIN any existing period's ``[start_date, end_date]`` span
    also produces overlapping date ranges (two periods covering one day) and a
    nondeterministic ``get_current_period``.

    The bound is therefore the latest existing ``end_date``: the new batch
    must start strictly after the day the current schedule's coverage
    ends.  This is a user mistake or a schedule change that needs a
    dedicated realign flow, not a silent reshuffle, so reject the whole
    batch loudly before writing anything.

    Args:
        existing_periods: List of ``(start_date, end_date)`` rows for the
            user's existing periods (empty for a first-time schedule).
        new_starts: The de-duplicated start dates this batch would create.

    Raises:
        ValidationError: When the earliest new start falls on or before
            the latest existing ``end_date``.
    """
    if not (existing_periods and new_starts):
        return
    latest_end = max(row[1] for row in existing_periods)
    if min(new_starts) <= latest_end:
        raise ValidationError(
            "New pay periods must start after your latest existing "
            f"period ends ({latest_end.isoformat()}). The requested "
            "start date would create periods that overlap or predate "
            "your current schedule; choose a later start date to "
            "extend your schedule forward."
        )


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
        ValidationError: If start_date is not a date, cadence is invalid,
            or the batch would create periods that overlap or predate the
            user's existing schedule (the forward-only invariant that keeps
            ``period_index`` order chronological -- see DH-#39).
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

    existing_periods = (
        db.session.query(PayPeriod.start_date, PayPeriod.end_date)
        .filter_by(user_id=user_id)
        .all()
    )
    existing_starts = {row[0] for row in existing_periods}

    # Determine which paydays this batch would create -- every requested
    # start that is not already an existing period.  An exact-match re-run
    # is skipped (not duplicated), so re-running with the same start and a
    # larger count legitimately extends the schedule.
    new_starts = []
    current_start = start_date
    for _ in range(num_periods):
        if current_start not in existing_starts:
            new_starts.append(current_start)
        current_start += timedelta(days=cadence_days)

    _reject_overlapping_batch(existing_periods, new_starts)

    created = []
    assigned_index = next_index  # Highest existing index + 1; gap-free.
    for new_start in new_starts:
        end = new_start + timedelta(days=cadence_days - 1)
        period = PayPeriod(
            user_id=user_id,
            start_date=new_start,
            end_date=end,
            period_index=assigned_index,
        )
        db.session.add(period)
        created.append(period)
        assigned_index += 1

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


def get_current_and_future_periods(user_id, as_of=None, include_period_id=None):
    """Return pay periods that have not yet ended, plus an optional extra.

    "Current and future" means every period whose ``end_date`` is on or
    after ``as_of`` (defaults to today): the period containing today and
    every later one.  Periods that have already ended are excluded.

    ``include_period_id`` forces one specific period into the result even
    if it has already ended.  The transaction-move UI passes the moved
    row's current ``pay_period_id`` so a row that currently sits in a
    past period stays selectable -- and stays the selected option --
    instead of the dropdown silently defaulting to the first current
    period and re-pointing the row on save.

    Args:
        user_id: The user's ID.
        as_of: Reference date for the "has ended" test (default: today).
        include_period_id: Optional pay_period id to always include,
            even when it has ended.

    Returns:
        List of PayPeriod objects ordered by period_index.
    """
    if as_of is None:
        as_of = date.today()

    not_ended = PayPeriod.end_date >= as_of
    if include_period_id is not None:
        clause = or_(not_ended, PayPeriod.id == include_period_id)
    else:
        clause = not_ended

    return (
        db.session.query(PayPeriod)
        .filter(PayPeriod.user_id == user_id, clause)
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

    Used by the calendar, daily-balance series, and spending-report
    services to find the pay periods that need to be inspected when
    reporting on a calendar month or year window.  Centralised here so a future
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
