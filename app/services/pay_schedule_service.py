"""
Shekel Budget App -- Pay Schedule Service

Reads and writes the per-user ``budget.pay_schedule`` row: the
persisted pay-period cadence that the extend / regenerate paths
continue an existing schedule from, plus the rolling-window
configuration the continuous top-up consumes.  Also owns the per-user
advisory lock that serializes the structural pay-period mutations.

Flask-isolated -- takes and returns plain data, never imports
``request`` / ``session``.  Flushes so callers see assigned ids, but
never commits: the route layer owns the transaction.
"""

import logging

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.exceptions import ValidationError
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.pay_schedule import PaySchedule

logger = logging.getLogger(__name__)

# Advisory-lock namespace for per-user pay-schedule serialization.  The
# two-argument ``pg_advisory_xact_lock(namespace, user_id)`` form keys
# every structural pay-period mutation (top-up / extend / truncate) on
# ``(this constant, user_id)``, so the feature can never collide with
# some other advisory lock that happens to use the same ``user_id`` as a
# single key.  The value is arbitrary but FIXED -- "SHKL" in ASCII --
# and fits a signed int4 (< 2**31 - 1).
_PAY_SCHEDULE_LOCK_NAMESPACE = 0x53484B4C  # 1397705036


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


def lock_schedule(user_id: int) -> None:
    """Take the per-user pay-schedule advisory lock for this transaction.

    Serializes the structural pay-period mutations (top-up / extend /
    truncate, and so regenerate, which truncates then extends) for one
    user within the current transaction, so two concurrent requests
    cannot interleave their count-then-append or classify-then-delete
    windows.  Transaction-scoped: PostgreSQL releases it automatically
    at COMMIT or ROLLBACK.  Re-entrant within a transaction, so a nested
    caller (top-up calling extend) takes it harmlessly more than once.

    The lock is the UX layer, not the correctness guard: the
    ``UNIQUE(user_id, period_index)`` constraint is what actually
    forbids a duplicate index on any append path.  The lock turns a
    would-be ``IntegrityError`` 500 (the racing loser) into a clean
    serialize-and-proceed (the loser re-reads a full window and no-ops).

    Args:
        user_id: The owning user's id, used as the lock's second key.
    """
    db.session.execute(
        select(func.pg_advisory_xact_lock(_PAY_SCHEDULE_LOCK_NAMESPACE, user_id))
    )


def upsert_schedule(user_id: int, cadence_days: int) -> PaySchedule:
    """Create or update the user's persisted cadence, race-safe.

    Called when a schedule's cadence is established (first generation)
    or changed (regenerate).  Uses a single PostgreSQL
    ``INSERT ... ON CONFLICT (uq_pay_schedule_user) DO UPDATE`` so a
    concurrent first-generation double-submit can never raise an
    ``IntegrityError`` 500: whichever request inserts second cleanly
    updates the existing row instead of colliding on the unique
    constraint.  Only ``cadence_days`` is in the conflict-update set, so
    capturing a new cadence never disturbs an existing row's
    rolling-window configuration (or its ``created_at``).

    Args:
        user_id: The owning user's id.
        cadence_days: Days between paydays to persist.  Bounded to
            1..365 by ``ck_pay_schedule_cadence_range``; the caller's
            Marshmallow schema validates the same range before this
            runs.

    Returns:
        The created or updated :class:`PaySchedule` row.
    """
    insert_stmt = pg_insert(PaySchedule.__table__).values(
        user_id=user_id, cadence_days=cadence_days,
    )
    upsert_stmt = insert_stmt.on_conflict_do_update(
        constraint="uq_pay_schedule_user",
        set_={"cadence_days": cadence_days},
    )
    db.session.execute(upsert_stmt)
    # Reload through the ORM with populate_existing so any instance the
    # session already holds for this user is refreshed to the values the
    # core upsert just wrote -- the identity map would otherwise keep a
    # stale copy.
    return (
        db.session.query(PaySchedule)
        .filter_by(user_id=user_id)
        .populate_existing()
        .one()
    )


def set_rolling(user_id: int, enabled: bool, target_periods: int) -> PaySchedule:
    """Update the user's continuous-rolling-window configuration.

    The settings-page setter for the rolling window: it flips
    ``rolling_enabled`` and stores the target period count on the user's
    existing schedule row.  Cadence is deliberately NOT touched here --
    it is owned by generate / regenerate.

    A schedule row must already exist.  The rolling window keeps a count
    of periods generated ahead, and growing the schedule needs a stored
    cadence to extend at; a user with no row has never generated a
    schedule, so there is nothing to roll forward.  Every user who has
    generated periods has a row (the first generation upserts one, and
    the Phase-1 backfill created one for every pre-existing user), so
    this guard only rejects the genuinely-not-set-up case.

    Args:
        user_id: The owning user's id.
        enabled: Whether continuous top-up is on.
        target_periods: How many current-and-future periods to keep
            generated ahead (>= 1; the count INCLUDES the current
            period).  Bounded to 1..260 by the caller's schema and to
            > 0 by ``ck_pay_schedule_positive_target``.

    Returns:
        The updated :class:`PaySchedule` row, flushed.

    Raises:
        ValidationError: The user has no schedule row (they must generate
            a schedule first).
    """
    schedule = get_schedule(user_id)
    if schedule is None:
        raise ValidationError(
            "Generate a pay-period schedule before configuring the "
            "rolling window."
        )
    schedule.rolling_enabled = enabled
    schedule.rolling_target_periods = target_periods
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
