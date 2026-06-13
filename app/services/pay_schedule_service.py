"""
Shekel Budget App -- Pay Schedule Service

Reads and writes the per-user ``budget.pay_schedule`` row: the
persisted pay-period cadence that the extend / regenerate paths
continue an existing schedule from, plus the rolling-window
configuration a later phase consumes.

Flask-isolated -- takes and returns plain data, never imports
``request`` / ``session``.  Flushes so callers see assigned ids, but
never commits: the route layer owns the transaction.
"""

import logging

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.pay_schedule import PaySchedule

logger = logging.getLogger(__name__)


def get_schedule(user_id: int) -> PaySchedule | None:
    """Return the user's pay-schedule row, or ``None`` when absent.

    Absent means one of two things: a legacy user who generated pay
    periods before this table existed (no backfill row because they
    had no periods at migration time, or a brand-new schedule), or a
    user who has never generated a schedule at all.  Callers that need
    a cadence regardless of whether a row exists use
    :func:`resolve_cadence`.

    Args:
        user_id: The owning user's id.

    Returns:
        The user's :class:`PaySchedule`, or ``None``.
    """
    return (
        db.session.query(PaySchedule)
        .filter_by(user_id=user_id)
        .first()
    )


def upsert_schedule(user_id: int, cadence_days: int) -> PaySchedule:
    """Create or update the user's persisted cadence.

    Called when a schedule's cadence is established (first generation)
    or changed (regenerate).  The rolling-window configuration on an
    existing row is deliberately left untouched -- only ``cadence_days``
    is authoritative here, so capturing a new cadence never silently
    resets a user's rolling settings.

    Args:
        user_id: The owning user's id.
        cadence_days: Days between paydays to persist.  Bounded to
            1..365 by ``ck_pay_schedule_cadence_range``; the caller's
            Marshmallow schema validates the same range before this
            runs.

    Returns:
        The created or updated :class:`PaySchedule` row, flushed so it
        carries an id.
    """
    schedule = get_schedule(user_id)
    if schedule is None:
        schedule = PaySchedule(user_id=user_id, cadence_days=cadence_days)
        db.session.add(schedule)
    else:
        schedule.cadence_days = cadence_days
    db.session.flush()
    return schedule


def resolve_cadence(user_id: int) -> int | None:
    """Resolve the cadence to continue the user's schedule with.

    Prefers the persisted ``pay_schedule.cadence_days``.  A legacy user
    who has periods but no schedule row (they generated before this
    table existed) falls back to inferring the cadence from the last
    period's length: :func:`pay_period_service.generate_pay_periods`
    sets ``end_date = start_date + (cadence_days - 1)``, so the cadence
    is ``(end_date - start_date).days + 1``.  The last period is the
    highest ``period_index`` -- the one a forward extend continues
    from -- so its length is the right cadence to continue with.

    Args:
        user_id: The owning user's id.

    Returns:
        The cadence in days, or ``None`` when the user has neither a
        schedule row nor any pay period to infer from.  The extend
        path treats ``None`` as "generate your first schedule first".
    """
    schedule = get_schedule(user_id)
    if schedule is not None:
        return schedule.cadence_days

    last = (
        db.session.query(PayPeriod)
        .filter_by(user_id=user_id)
        .order_by(PayPeriod.period_index.desc())
        .first()
    )
    if last is None:
        return None
    return (last.end_date - last.start_date).days + 1
