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
from app.exceptions import PayCalendarGapError, ValidationError
from app.utils.log_events import (
    BUSINESS,
    EVT_PAY_PERIODS_GENERATED,
    log_event,
)

logger = logging.getLogger(__name__)


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
    """Return the pay period that contains the given date, or ``None``.

    **A caller that cannot ANSWER without a period takes
    :func:`require_current_period` instead** (plan step X-x, ruling R-CY).  This
    nullable form is for the callers whose rule has a DEFINED answer for absence
    -- a writer that legitimately no-ops, or a check of whether a date is covered
    at all.  It is the same split
    :func:`app.services.scenario_resolver.get_baseline_scenario` and
    ``require_baseline_scenario`` make one precondition over, in the same
    direction: the obvious name fails loud, and reaching for the nullable reads
    as a decision.

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


def covers(user_id, as_of=None) -> bool:
    """Return whether the user's pay calendar covers *as_of*.

    The ONE predicate behind every "does this user have a usable pay calendar"
    question in the app (plan step X-x, ruling R-DA).  Before it, five spellings
    answered that question and two of them answered a DIFFERENT one: the
    onboarding checklist and eleven service guards tested whether ANY pay period
    exists, which is true for every owner from the moment of registration and so
    could never be false, while the surfaces beside them tested whether TODAY is
    covered, which is reachable.  A checklist telling a user their pay periods
    were generated, on a page telling them to generate pay periods, is what two
    spellings of one question bought.

    Deliberately a boolean and not a period: a caller that wants the period wants
    :func:`require_current_period`, and returning the row here would grow a third
    accessor answering the same question a third way.

    Args:
        user_id: The user's ID.
        as_of:   The reference date (default: today).

    Returns:
        ``True`` when a pay period contains *as_of*.
    """
    return get_current_period(user_id, as_of=as_of) is not None


def require_current_period(user_id, as_of=None):
    """Return the pay period containing *as_of*, or raise the named exception.

    The form every caller takes when its answer is UNDEFINED without a period
    (plan step X-x, ruling R-CY): one application-level handler catches
    :class:`~app.exceptions.PayCalendarGapError` and renders the setup-recovery
    page, so the caller neither invents a degraded figure nor 500s.

    **It exists because a dozen surfaces were inventing one.**  Measured
    2026-07-31 on a prod-shape clone with a four-day hole in an otherwise
    complete 61-period schedule: ``/savings`` reported net worth ``$236,325.04``
    against the ``$233,096.49`` the same data gives when today is covered,
    because every per-account tile fell back to
    :attr:`~app.models.account.Account.current_anchor_balance` -- a derived cache
    the app already knows can diverge from the ledger -- while ``/grid`` rendered
    the "generate your pay periods" card at that same instant and the net-worth
    trend collapsed to zero points carrying a ``current_index`` of ``0``.  This
    is finding N-113's class, one precondition over.

    **Unlike the baseline this state is REACHABLE**, so the raise is not a
    belt-and-braces guard over impossible data: a lapsed schedule, a schedule
    opening in the future, and a hole between two periods all produce it, and a
    hole is permanent because ``pay_period_admin.top_up_rolling_window`` counts
    periods ending on or after today and stops once the target is met.

    Args:
        user_id: The user whose pay period is required.
        as_of:   The reference date (default: today).

    Returns:
        The :class:`~app.models.pay_period.PayPeriod` containing *as_of*.

    Raises:
        PayCalendarGapError: When no pay period contains *as_of*.  A
            ``ValueError`` subclass; its message names the repair.
    """
    if as_of is None:
        as_of = date.today()
    period = get_current_period(user_id, as_of=as_of)
    if period is None:
        raise PayCalendarGapError(
            f"user {user_id} has no pay period containing {as_of.isoformat()}, "
            f"so nothing anchored on that date can be answered for them. The "
            f"schedule has lapsed, opens later, or has a hole at that date: "
            f"/pay-periods/generate extends it forward and the settings "
            f"pay-periods section rebuilds it",
            user_id=user_id,
            as_of=as_of,
        )
    return period


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
