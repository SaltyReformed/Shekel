"""
Shekel Budget App -- Pay Period Service

Queries an owner's biweekly pay periods.  Each period is defined by a
start_date (payday) and an end_date (the day before the next payday).

**It no longer WRITES them** (plan step C3-b): ``generate_pay_periods``,
``establish_schedule``, the batch bounds and the forward-only guard moved to
:mod:`app.services.pay_period_write`, which is now the one place in ``app/``
that changes ``budget.pay_periods``.  The reason is C3-a's, one level up --
deciding that a schedule should change and changing it are two concerns, and
the invariant that the stored ``end_date`` / ``period_index`` equal the
derivation over the owner's paydays needs exactly one home for plan steps C4,
C6 and C7 to inherit.  What is left here is the read side, which plan step
**C2-f** points at ``pay_calendar.PayCalendar``.
"""

from datetime import date

from sqlalchemy import or_

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.utils.dates import display_today


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
