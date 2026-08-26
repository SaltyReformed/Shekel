"""
Shekel Budget App -- Pay Schedule Service

Reads and writes the per-user ``budget.pay_schedule`` row: the
persisted pay-period cadence that the extend / regenerate paths
continue an existing schedule from, plus the rolling-window
configuration the continuous top-up consumes.

**It no longer owns the advisory lock that serializes the structural
pay-period mutations** (plan step X-f1c3c).  That lock moved, unchanged
in key and namespace value, to
:mod:`app.services.user_write_lock` -- because the posting-ledger
reconciles need the SAME lock, not a second one: a reconcile derives each
correction's pay period from the owner's calendar, so a concurrent
truncate can delete the period it is filing under.  Two locks would also
be the only way this app could deadlock.  See that module for the whole
argument.

Flask-isolated -- takes and returns plain data, never imports
``request`` / ``session``.  Flushes so callers see assigned ids, but
never commits: the route layer owns the transaction.
"""

import logging

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.exceptions import ValidationError
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.pay_schedule import (
    CADENCE_DAYS_MAX,
    CADENCE_DAYS_MIN,
    PaySchedule,
)

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


def reread_schedule(user_id: int) -> PaySchedule:
    """Return the user's schedule row, RE-READ rather than remembered.

    **For a caller that has taken the per-user advisory lock after loading the
    row and must not trust what it loaded** (plan step **C4**).  Every writer
    of ``cadence_days`` takes that lock, so a batch committing between a
    caller's first read and its lock acquisition leaves the caller's instance
    stale by exactly one write -- which matters wherever the cadence decides a
    figure, because it dictates the LAST pay period's derived end.

    **A second :func:`get_schedule` would NOT fix that, and would read as
    though it had.**  The query runs, but SQLAlchemy returns the
    identity-mapped instance with its ORIGINAL attribute values; that is the
    same trap :func:`upsert_schedule` spells ``populate_existing`` for after
    its Core upsert, and taking an advisory lock through the session expires
    nothing either.  Naming the re-read is what keeps the next caller from
    writing the version that silently does nothing.

    Args:
        user_id: The owning user's id.

    Returns:
        The user's :class:`PaySchedule`, with every attribute re-read.

    Raises:
        ValidationError: The user has no schedule row.  Refused rather than
            answered ``None`` because this door's callers have ALREADY
            established that a row exists and hold the lock that protects it;
            no ``app/`` door deletes one, so absence here is a broken
            invariant rather than a state to branch on.
    """
    schedule = (
        db.session.query(PaySchedule)
        .filter_by(user_id=user_id)
        .populate_existing()
        .one_or_none()
    )
    if schedule is None:
        raise ValidationError(
            f"user {user_id} has no budget.pay_schedule row to re-read.  This "
            f"door is called under the per-user write lock by a caller that "
            f"has already read one, and no door in app/ deletes a schedule "
            f"row, so reaching this means the row was removed outside the "
            f"application."
        )
    return schedule


def reject_out_of_range_cadence(cadence_days: int) -> None:
    """Refuse a cadence ``ck_pay_schedule_cadence_range`` would refuse.

    **One implementation of the bound, two callers, and the second is why it
    is a function** (plan step X-ad-a).  :func:`upsert_schedule` is the one
    writer of the column and asks this immediately before writing, so no door
    can persist a value the CHECK refuses.  ``auth_service.register_user`` asks
    it EARLIER -- in its up-front validation block, before the ``User`` row is
    added to the session -- because a registration that refuses halfway leaves
    a partly-built owner in a session whose only protection is that nobody
    commits it.  Neither caller may hold its own copy of the bound: two copies
    of a range are two chances for the schema tier, the service tier and the
    column to disagree.

    Args:
        cadence_days: The candidate days-between-paydays value.

    Raises:
        ValidationError: *cadence_days* falls outside
            :data:`~app.models.pay_schedule.CADENCE_DAYS_MIN` ..
            :data:`~app.models.pay_schedule.CADENCE_DAYS_MAX`.  The message
            names the offending value and both bounds, so a surface can render
            it verbatim.
    """
    if not CADENCE_DAYS_MIN <= cadence_days <= CADENCE_DAYS_MAX:
        raise ValidationError(
            f"Days between paydays must be between {CADENCE_DAYS_MIN} and "
            f"{CADENCE_DAYS_MAX}; got {cadence_days}."
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

    **The cadence bound is checked HERE, and that placement is plan step
    X-ad-a's** (finding **N-123**'s neighbourhood, not the finding itself).
    This docstring used to say the bound was
    ``ck_pay_schedule_cadence_range``'s and that "the caller's Marshmallow
    schema validates the same range before this runs" -- true of the four
    callers that existed, and a rule held by remembering rather than by
    structure.  Four doors write a cadence (generate, regenerate, reset, and
    now registration), the CHECK turns an out-of-range value into an
    ``IntegrityError`` 500 rather than something a form can render, and this
    function is the ONE writer of the column.  So the refusal lives at the
    write door, where a fifth caller inherits it without its author
    remembering.

    Args:
        user_id: The owning user's id.
        cadence_days: Days between paydays to persist.

    Returns:
        The created or updated :class:`PaySchedule` row.

    Raises:
        ValidationError: *cadence_days* falls outside
            :data:`~app.models.pay_schedule.CADENCE_DAYS_MIN` ..
            :data:`~app.models.pay_schedule.CADENCE_DAYS_MAX`, the bound
            ``ck_pay_schedule_cadence_range`` enforces in the database.  A
            400 rather than a 500: every door in front of this one takes the
            value from a form.
    """
    reject_out_of_range_cadence(cadence_days)
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
    period's length: the LAST period's end is
    ``start_date + (cadence_days - 1)``, so the cadence is
    ``(end_date - start_date).days + 1``.  The last period is the
    highest ``period_index`` -- the one a forward extend continues
    from -- so its length is the right cadence to continue with.

    **The fallback is CIRCULAR and pay-calendar finding P8 owns that**: since
    plan step C3-b :func:`app.services.pay_period_write.record_paydays` derives
    that same last end FROM this answer, so for a schedule-row-less owner this
    reads back the value it produced.  It is a fixed point rather than a drift,
    and no door can create such an owner any more -- every batch that records a
    payday upserts the row (the cadence rule) -- so it names legacy data only.
    Plan step C4 removes the fallback with the column it reads.

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
