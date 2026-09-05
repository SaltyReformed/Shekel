"""
Shekel Budget App -- Pay Schedule Service

Reads and writes the per-user ``budget.pay_schedule`` row: the
persisted pay-period cadence that the extend / regenerate paths
continue an existing schedule from, the day the owner's paychecks
began, and the rolling-window configuration the continuous top-up
consumes.

**The row holds three facts about the RHYTHM and two about a WRITE**, and
the doors here are split on that line.  ``cadence_days``,
``history_opens_on`` and ``shift_id`` are what a pay CALENDAR is derived
from; :func:`resolve_schedule` answers the first two in one read as
:class:`ScheduleFacts`, and the third has its own reader
(:func:`resolve_shift`) until plan step ``C14-e`` gives the calendar a
reason to carry it.  ``rolling_enabled`` and ``rolling_target_periods``
configure the on-request top-up and are read off the row itself by the
caller that is about to write.

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
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app import ref_cache
from app.enums import BusinessDayShiftEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.pay_schedule import (
    CADENCE_DAYS_MAX,
    CADENCE_DAYS_MIN,
    PaySchedule,
)
from app.utils.business_days import shortest_collision_free_cadence
from app.utils.dates import CALENDAR_DATE_MAX, CALENDAR_DATE_MIN

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Rhythm:
    """How often an owner is paid, and what payroll does on a closed day.

    Plan step **C14-b**.  The pair ``budget.pay_schedule`` stores as
    ``cadence_days`` and ``shift_id``, and a TYPE rather than two arguments
    because they carry a JOINT rule: a convention that displaces a payday is
    legal only on a cadence longer than the longest run of consecutive closed
    days (:func:`reject_shift_on_short_cadence`).  Written through two
    statements the row passes through a state neither statement means, and
    either order refuses a legal request -- so :func:`upsert_schedule` takes
    the pair, judges the pair, and writes the pair, and no caller is able to
    hand it half of one.

    **It is not :class:`ScheduleFacts` under another name.**  That value is
    what a pay CALENDAR is DERIVED from and pairs the cadence with
    ``history_opens_on``; this is what a payday batch WRITES.  The two overlap
    on the cadence because one column serves both questions, which is the
    row's shape rather than a second home for the value: neither type stores
    anything, and both read the same column.

    Attributes:
        cadence_days: Days between consecutive paydays.  Bounded by
            :func:`reject_out_of_range_cadence`, not by this type -- a range
            is the column's own rule and belongs where its one writer asks it.
        shift: What payroll does when a payday lands on a day no money moves
            on, as the :class:`~app.enums.BusinessDayShiftEnum` member rather
            than the ``ref.business_day_shifts`` id that spells it on the
            wire.  The id is what crosses a form and what the column holds, so
            :class:`~app.schemas.validation.pay_periods.BusinessDayShiftField`
            converts on the way in and :func:`upsert_schedule` converts on the
            way out; between them the value is a member, which is what
            :func:`~app.utils.business_days.shift_to_business_day` requires --
            it REFUSES an integer rather than defaulting, so a rhythm carrying
            an id would hand plan step ``C14-e`` a raise instead of a payday.
            This is IDs-for-logic as the project means it: no ``name`` string
            is ever compared.
    """

    cadence_days: int
    shift: BusinessDayShiftEnum


@dataclass(frozen=True)
class ScheduleFacts:
    """The two ``budget.pay_schedule`` facts a pay CALENDAR is built from.

    Plan step **balance:X-bh-2**.  One value rather than two return types
    because they arrive from one row and are read by one consumer -- a
    :class:`~app.services.pay_calendar.PayCalendar` needs both, and resolving
    them separately would be two queries of the same row per calendar load,
    which is exactly the redundant-schedule-read defect ledger rows **P68** and
    **P69** record.

    **It is the facts OF A ROW, and since plan step C4-d it cannot say
    otherwise** (ruling **R-PC45**).  ``cadence_days`` was typed ``int | None``
    beside a nullable ``history_opens_on``, which made four pairs constructible
    where a row can produce two: absence of a cadence beside a STATED opening
    said "I do not know how often this owner is paid, and I do know their
    paychecks reach back to June 2020", and ``ck_pay_schedule_cadence_range``
    sits on a ``NOT NULL`` column, so no row can say it.  That is the defect
    plan step C4-b-2 removed from the SCHEMA surviving one tier up in the TYPE.
    "This owner has no schedule at all" is now :func:`resolve_schedule`
    answering ``None``, which is one optional rather than two independent ones,
    and the ``int | None`` that used to travel from here into
    :class:`~app.services.pay_calendar.PayCalendar`,
    :func:`~app.services.pay_calendar.derive_periods` and three projection
    producers -- policed at each by prose and by two runtime raises -- has no
    subject at any of them.

    **It carries the calendar facts and NOT the rolling ones.**
    ``rolling_enabled`` and ``rolling_target_periods`` configure a WRITE
    (the on-request top-up); these two describe the owner's rhythm, which is
    what a calendar derives from.  A caller that needs the rolling half wants
    the row itself (:func:`get_schedule`), because it is about to write.

    Attributes:
        cadence_days: Days between paydays.  The STORED value and never an
            inferred one; the arm that inferred it closed findings **P8** and
            **P35** on its way out (see :func:`resolve_schedule`).  Not
            optional, because the column is ``NOT NULL``: a row states its
            cadence or it is not a row.
        history_opens_on: How far back this owner's paychecks reach, or
            ``None`` for NOT STATED (ruling **balance:R-IA**, amended
            2026-08-31) -- an absence rather than a claim, and one the
            backward rhythm answers by counting only the record.  It stays
            optional where the cadence above did not, and the asymmetry is the
            COLUMN's: this one is nullable and that one is not.  There is NO
            fallback for it: an owner with no schedule row has stated nothing,
            and the first recorded payday is a record boundary rather than an
            answer.
    """

    cadence_days: int
    history_opens_on: date | None

    @classmethod
    def of(cls, schedule: PaySchedule) -> "ScheduleFacts":
        """Return the calendar facts carried by an existing schedule *row*.

        For a caller that already holds the row -- the rolling top-up, which
        reads it to decide whether to write at all and must not pay for a
        second read (finding **P70**).  A classmethod rather than two attribute
        reads at that caller so WHICH columns are the calendar facts is stated
        once: a third fact added to the table joins the value here, and the
        top-up inherits it without its author remembering.

        Args:
            schedule: The owner's ``budget.pay_schedule`` row.

        Returns:
            Its :class:`ScheduleFacts`.
        """
        return cls(
            cadence_days=schedule.cadence_days,
            history_opens_on=schedule.history_opens_on,
        )


def get_schedule(user_id: int) -> PaySchedule | None:
    """Return the user's pay-schedule row, or ``None`` when absent.

    **Absent means one thing, since plan step C4-b-2**: this user has never
    recorded a payday.  ``fk_pay_periods_schedule`` holds a pay period's owner
    to having a row here, so no schedule row IMPLIES no pay periods.  *Not the
    converse, and an adversarial review caught a draft of this paragraph
    asserting the equivalence: an owner may hold this row and zero periods,
    which is the state ``pay_period_admin.reset_pay_periods`` passes through.*
    Absence used to mean a second thing as well -- a legacy user with periods
    that predated this table -- and carrying an answer for that state is what
    findings **P8** and **P35** cost.

    Callers that want the CALENDAR facts rather than the row (because they are
    about to derive, not to write) use :func:`resolve_schedule`.

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
            invariant rather than a state to branch on.  **Since plan step
            C4-b-2 the database narrows it**: ``fk_pay_periods_schedule`` is
            ``ON DELETE RESTRICT``, so the row cannot be removed while the
            owner holds a payday.

            **It does NOT make the refusal unreachable, and a first draft of
            this paragraph claimed it did** -- on the reasoning that a
            constraint "cannot speak for" a row removed outside the
            application, which is backwards: a foreign key is enforced by
            PostgreSQL and an out-of-application delete is exactly what it
            does speak for.  What the key is silent about is an owner holding
            this row and ZERO pay periods, which is ordinary --
            ``pay_period_admin.reset_pay_periods`` passes through it and
            ``pay_period_rolling`` reads such an owner.  Their row is
            deletable, so the refusal names a state that is still reachable.
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


def reject_shift_on_short_cadence(rhythm: Rhythm) -> None:
    """Refuse a displacing convention a cadence is too short to carry.

    **The pairing is what is refused, and it is refused HERE rather than by a
    CHECK constraint** (plan step **C14-b**, ruling **R-PC59**,
    developer 2026-09-05).
    A convention that moves a payday off a closed day is not injective: two
    nominal paydays closer together than the longest run of consecutive closed
    days displace onto ONE day, and
    ``pay_calendar._derive.derive_periods`` refuses a repeated payday outright,
    so the owner's whole calendar would raise rather than render.

    **Two reasons, and an earlier draft of this docstring claimed three.**
    An adversarial review of 2026-09-05 struck the third and corrected the
    premise of the second; the count is restated here rather than left to
    decay.

    1. **The floor is DERIVED.**  It is the longest closed run plus one, proved
       and computed by
       :func:`~app.utils.business_days.shortest_collision_free_cadence`, and it
       moves with the holiday set -- which is not fixed
       (``business_days.JUNETEENTH_FIRST_YEAR`` records the set changing once
       inside the window this application admits).  A CHECK expression must be
       IMMUTABLE, so a constraint could only freeze a copy where nothing can
       recompute it.
    2. **A constraint cannot name a FIELD.**  It arrives as an
       ``IntegrityError`` carrying a constraint name, where a form needs the
       message on the control the owner chose -- which is what
       :func:`~app.schemas.validation.pay_periods.validate_derivable_rhythm`
       supplies, and the same reason plan step X-ad-a moved the cadence bound
       out from behind ``ck_pay_schedule_cadence_range``.

    *The struck reason was "which id means ``none`` is seed data, so a CHECK
    would hard-code a ``ref`` id".  True of the naive spelling and defeasible:
    a pinned id verified by a migration-time assertion, or NULL standing for
    "no convention", both dodge it.  Each has its own cost -- and the first two
    reasons stand on their own -- but a defeasible reason is not a structural
    one and stating it as such overclaimed.*

    **What this placement does NOT buy, and a CHECK would not have bought
    either.**  The refusal is asked on WRITE, so neither it nor a frozen
    constraint sees a STORED row that a later holiday change made illegal --
    ``ADD CONSTRAINT`` does scan existing rows, but only when some migration
    re-adds it.  Nothing reconciles ``budget.pay_schedule`` today.

    **And a floor RISE would surface badly, which is worth knowing before
    anyone raises one.**  The continue path re-judges the STORED pair --
    ``pay_period_admin.extend_pay_periods`` hands it back through
    :func:`upsert_schedule` -- and the rolling top-up reaches that path from
    ``routes/grid`` and ``routes/dashboard`` with no ``except
    ValidationError`` and no error handler registered for this exception.  So
    an owner whose stored pair became illegal would meet a 500 on the two
    screens they use most, with no door offering the repair.  Unreachable
    today: every stored pair was written through this refusal and the
    backfill seeds ``none``, which is legal at every cadence.  Both gaps are
    findings the 2026-09-05 review raised and this step REPORTS rather than
    claims away; neither is C14-b's to fix.

    ``none`` displaces nothing, so it is legal at every cadence and returns
    before the floor is computed -- which is also why the walk behind that
    floor is paid only by an owner who actually chooses a convention.

    Args:
        rhythm: The :class:`Rhythm` the operation will LEAVE BEHIND, which is
            not necessarily the stored one -- a request that shortens the
            cadence and switches the convention off in one submission leaves a
            legal pair, and judging either half against the stored other half
            would refuse it.

    Raises:
        ValidationError: The cadence is below the floor and the convention is
            not ``none``.  The message names the floor, the offending cadence
            and the consequence, so a surface can render it verbatim.
    """
    if rhythm.shift is BusinessDayShiftEnum.NONE:
        return
    floor = shortest_collision_free_cadence()
    if rhythm.cadence_days < floor:
        raise ValidationError(
            f"Days between paydays must be at least {floor} when payroll "
            f"moves a payday off a weekend or holiday; got "
            f"{rhythm.cadence_days}.  A shorter cadence would land two "
            f"paychecks on one day."
        )


def reject_out_of_range_history_opening(history_opens_on: date | None) -> None:
    """Refuse an opening ``ck_pay_schedule_history_opens_range`` would refuse.

    :func:`reject_out_of_range_cadence`'s sibling, written for the same reason
    and asked by the same two kinds of caller: :func:`set_history_opening`, the
    column's one writer, asks it immediately before writing, and
    ``auth_service.register_user`` asks it in its up-front validation block,
    before the ``User`` row exists.  A value outside the CHECK reaches the
    database as an ``IntegrityError`` 500 rather than as something a form can
    render, and an HTML date input accepts a five-digit-year typo, so this is
    the ordinary path rather than a defensive one.

    ``None`` passes, and it is the column's ordinary value: it means the
    owner has not stated a history, which the rhythm answers by counting only
    the recorded paydays (ruling **balance:R-IA**, amended 2026-08-31).

    Args:
        history_opens_on: The candidate opening day, or ``None``.

    Raises:
        ValidationError: The day falls outside
            :data:`~app.utils.dates.CALENDAR_DATE_MIN` ..
            :data:`~app.utils.dates.CALENDAR_DATE_MAX`.  The message
            names the offending day and both bounds so a surface can render it
            verbatim.
    """
    if history_opens_on is None:
        return
    if not CALENDAR_DATE_MIN <= history_opens_on <= CALENDAR_DATE_MAX:
        raise ValidationError(
            f"The day your paychecks started must fall between "
            f"{CALENDAR_DATE_MIN.isoformat()} and "
            f"{CALENDAR_DATE_MAX.isoformat()}; got "
            f"{history_opens_on.isoformat()}."
        )


def reject_history_opening_after_payday(
    history_opens_on: date | None, opening_payday: date | None,
) -> None:
    """Refuse an opening later than the first payday it is a floor below.

    **One rule, asked of two different sources**, which is why it is a function
    rather than an inline test at either.  ``auth_service.register_user`` asks
    it of the payday the sign-up form STATES, up front, before the ``User`` row
    is added -- that module's standing property, and the reason its
    pay-calendar checks all sit in one block.  :func:`set_history_opening` asks
    it of the payday the schedule RECORDS, because by then there is a schedule
    to read.  Two spellings of "your paychecks cannot have begun after your
    first one" would be two chances for the two doors to admit different sets.

    Equality passes, and it is the ordinary answer for one whole class of
    owner: a floor ON the opening payday means "count nothing below the
    record", which is what somebody whose first payday has not happened yet
    states (ruling ``pay_calendar:R-PC14`` calls that an ordinary state).

    Args:
        history_opens_on: The candidate opening day, or ``None`` -- which
            passes, being the absence of a claim rather than a claim.
        opening_payday: The first payday to measure against, or ``None`` for an
            owner with no paydays at all -- which also passes, there being no
            rhythm for a floor to contradict.

    Raises:
        ValidationError: *history_opens_on* falls after *opening_payday*.  The
            message names both days, so a surface can render it verbatim.
    """
    if history_opens_on is None or opening_payday is None:
        return
    if history_opens_on > opening_payday:
        raise ValidationError(
            f"Your paychecks cannot have started on "
            f"{history_opens_on.isoformat()}: that is after your first "
            f"payday, {opening_payday.isoformat()}.  Enter that day or an "
            f"earlier one, or leave it blank."
        )


def set_history_opening(
    user_id: int, history_opens_on: date | None,
) -> PaySchedule:
    """Store how far back this owner's paychecks reach.

    Plan step **balance:X-bh-2** (ruling **balance:R-IA**).  The ONE writer of
    ``history_opens_on``, for the two doors that ask the question:
    registration, which asks it beside the payday and cadence it already asks
    for, and the pay-periods settings section, which is where an owner corrects
    it or states it for the first time -- every owner who registered before
    this column existed holds ``NULL``, and ``NULL`` is not a state a sign-up
    form can revisit.

    **It is a door of its own rather than an argument to**
    :func:`upsert_schedule`, and the lifecycles are why.  That function is
    called by ``pay_period_write.record_paydays`` on EVERY batch -- generate,
    extend, regenerate, reset -- because a batch that records a payday
    establishes the cadence it was spaced by (the cadence rule, plan step
    C3-b).  When a job began is not a fact a batch of paydays states, so
    threading it through that door would either overwrite the owner's answer
    on every extend or add a "leave this one alone" argument, which is the
    conditional-write shape ``set_rolling`` already avoids by being separate.

    **``None`` is a real value to write, not a skip.**  Clearing the field is
    how an owner WITHDRAWS a statement -- after which the engine counts only
    their recorded paydays again -- so this door stores what it is given.

    A schedule row must already exist, exactly as :func:`set_rolling` requires:
    the value bounds a rhythm, and an owner with no row has no cadence for a
    rhythm to run at.  Registration satisfies that by writing its paydays --
    and with them the schedule row -- before it calls here.

    Args:
        user_id: The owning user's id.
        history_opens_on: The day the owner's paychecks began, or ``None``
            to state nothing, which counts only the record.

    Returns:
        The updated :class:`PaySchedule` row, flushed.

    Raises:
        ValidationError: The user has no schedule row, the day falls outside
            the window ``ck_pay_schedule_history_opens_range`` admits, or it
            falls after the owner's first recorded payday.
    """
    reject_out_of_range_history_opening(history_opens_on)
    schedule = get_schedule(user_id)
    if schedule is None:
        raise ValidationError(
            "Generate a pay-period schedule before saying when your "
            "paychecks started."
        )
    # The owner's own paydays, not a calendar: this asks for ONE day and the
    # derivation would build every period to answer it.  ``min`` rather than
    # the lowest ``period_index``, because the floor is measured against the
    # earliest payday and the two agree only while the index is in date order
    # -- which is a stored column plan step C4-c dropped.
    reject_history_opening_after_payday(
        history_opens_on,
        db.session.query(func.min(PayPeriod.start_date))
        .filter(PayPeriod.user_id == user_id)
        .scalar(),
    )
    schedule.history_opens_on = history_opens_on
    db.session.flush()
    return schedule


def upsert_schedule(user_id: int, rhythm: Rhythm) -> PaySchedule:
    """Create or update the user's persisted RHYTHM, race-safe.

    Called when a schedule's rhythm is established (first generation)
    or changed (regenerate).  Uses a single PostgreSQL
    ``INSERT ... ON CONFLICT (uq_pay_schedule_user) DO UPDATE`` so a
    concurrent first-generation double-submit can never raise an
    ``IntegrityError`` 500: whichever request inserts second cleanly
    updates the existing row instead of colliding on the unique
    constraint.  The conflict-update set is the RHYTHM alone --
    ``cadence_days`` and ``shift_id`` -- so capturing a new one never
    disturbs an existing row's rolling-window configuration, its stated pay
    history, or its ``created_at``.

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

    **It writes the RHYTHM as a PAIR, since plan step C14-b**, and the pairing
    is why rather than tidiness.  ``shift_id`` -- what payroll does when a
    payday lands on a closed day -- is legal only on a cadence longer than the
    longest run of closed days (:func:`reject_shift_on_short_cadence`), so the
    two columns carry a joint rule.  Written by two doors in sequence, the row
    passes through an intermediate state that is not the one the request
    means, and EITHER order refuses a legal request: writing the cadence first
    judges a shortened cadence against a convention the same request is
    turning OFF, and writing the convention first judges it against a cadence
    the same request is lengthening.  One statement judged against the state
    the operation LEAVES BEHIND has neither hole -- the same principle
    ``pay_period_write._PaydayChange`` exists for one module over.

    Args:
        user_id: The owning user's id.
        rhythm: The :class:`Rhythm` to persist.  A caller CONTINUING a
            schedule rather than stating one reads the stored convention
            (:func:`resolve_shift`) and passes it back; there is deliberately
            no "leave this half alone" argument, because a batch that cannot
            say what the rhythm is cannot be judged against it.

    Returns:
        The created or updated :class:`PaySchedule` row.

    Raises:
        ValidationError: *cadence_days* falls outside
            :data:`~app.models.pay_schedule.CADENCE_DAYS_MIN` ..
            :data:`~app.models.pay_schedule.CADENCE_DAYS_MAX`, the bound
            ``ck_pay_schedule_cadence_range`` enforces in the database, or the
            pair is one no calendar can derive
            (:func:`reject_shift_on_short_cadence`).  A 400 rather than a 500:
            every door in front of this one takes the values from a form.
    """
    reject_out_of_range_cadence(rhythm.cadence_days)
    reject_shift_on_short_cadence(rhythm)
    # The one place a rhythm's convention becomes an id, which is the storage
    # boundary and nowhere else -- ``recurrence._authoring`` resolves the same
    # vocabulary at the same moment for the same reason.
    shift_id = ref_cache.business_day_shift_id(rhythm.shift)
    insert_stmt = pg_insert(PaySchedule.__table__).values(
        user_id=user_id,
        cadence_days=rhythm.cadence_days,
        shift_id=shift_id,
    )
    upsert_stmt = insert_stmt.on_conflict_do_update(
        constraint="uq_pay_schedule_user",
        set_={"cadence_days": rhythm.cadence_days, "shift_id": shift_id},
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
    generated periods has a row -- the first generation upserts one, the
    Phase-1 backfill created one for every pre-existing user, and since plan
    step C4-b-2 ``fk_pay_periods_schedule`` makes it structural rather than
    historical -- so this guard only rejects the genuinely-not-set-up case.

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


def resolve_schedule(user_id: int) -> "ScheduleFacts | None":
    """Resolve the two facts a pay calendar is derived from, in ONE read.

    Plan step **balance:X-bh-2**.  :func:`resolve_cadence`'s body, widened to
    the pair -- because :func:`app.services.pay_calendar.calendar_for` needs
    both and asking for them separately would query one row twice per calendar
    load, which is the redundant per-render schedule read ledger rows **P68**
    and **P69** record.

    **It reads the stored row and nothing else, since plan step C4-b-2**, which
    closed findings **P8** and **P35** by making the state the deleted arm
    existed for UNREPRESENTABLE.  That arm inferred a row-less owner's cadence
    from their last period's length -- ``(end_date - start_date).days + 1`` --
    and it was wrong in two ways at once.  It was CIRCULAR: since plan step
    C3-b :func:`app.services.pay_period_write.record_paydays` derives that same
    last end FROM this answer, so it read back the value it had produced and
    could be neither right nor wrong.  And it was unbounded ABOVE, where
    ``budget.pay_schedule.cadence_days`` is bounded to 1..365 by
    ``ck_pay_schedule_cadence_range``: a hand-written period spanning more than
    a year inferred a cadence ``app.services.pay_calendar`` refuses, and since
    plan step C2-c that raise reaches every balance page as a bare 500.

    What makes the arm unreachable is ``fk_pay_periods_schedule``, the key
    :class:`~app.models.pay_period.PayPeriod` carries: a pay period's owner has
    a ``budget.pay_schedule`` row or the row cannot exist.  So the arm is
    DELETED rather than left standing over a state no database can hold -- an
    unreachable branch is a claim nothing grades, and the next reader cannot
    tell it from a live one.

    **``None`` is still a real answer, and since plan step C4-d it is the
    WHOLE answer rather than a pair of them** (ruling **R-PC45**).  Before, it
    meant "no schedule row AND no period to infer from"; then C4-b-2 narrowed
    it to "no schedule row", which the key makes sufficient -- such an owner has
    no pay periods either.  What this step changed is where that absence is
    SPELT.  It used to be a ``ScheduleFacts`` with both fields ``None``, so the
    absence of a row and the absence of a stated history shared one encoding
    and a THIRD pair -- no cadence beside a stated opening -- was constructible
    and unrepresentable.  Now the absence is this function's own return: there
    is no row, so there are no facts, so there is no value.

    That is an ordinary owner: a companion account
    (``routes/settings.companion_create`` writes neither), or any user before
    registration records their first batch.  The extend path reads it as
    "generate your first schedule first".

    **The two doors above the calendar refuse it and this one does not**, which
    is the split C4-d makes explicit rather than leaves to chance.  This is the
    SOFT door: it answers "does this owner have a schedule" and a caller decides
    what that means, which is what ``routes/salary/profiles._paychecks_per_year``
    needs -- a FORM must not 500 on the state the form itself repairs.  The HARD
    doors are :func:`app.services.pay_calendar.calendar_for` and
    :func:`app.services.pay_calendar.cadence_for`, which answer or raise
    ``PayCalendarError``, because every figure they feed is a per-paycheck one.
    Before C4-d the two calendar doors disagreed about this owner -- the cadence
    door refused and the calendar door quietly answered an EMPTY calendar
    carrying no cadence -- and that split is why some screens showed a repair
    page for them and others showed a blank one.

    **``history_opens_on`` never had a fallback and that asymmetry was the
    point.**  Nothing in ``budget.pay_periods`` says when a job began -- the
    first recorded payday is a record boundary, not an answer -- so an owner who
    HAS a row and has stated nothing carries ``None`` there, and it reads as
    exactly that (ruling **balance:R-IA**, amended 2026-08-31).  It is the one
    optional left on :class:`ScheduleFacts`, and it is optional because its
    column is.

    Args:
        user_id: The owning user's id.

    Returns:
        The :class:`ScheduleFacts`, or ``None`` when the user has no
        ``budget.pay_schedule`` row -- which by ``fk_pay_periods_schedule`` is
        an owner with no pay periods either.
    """
    schedule = get_schedule(user_id)
    if schedule is None:
        return None
    return ScheduleFacts.of(schedule)


def resolve_cadence(user_id: int) -> int | None:
    """Resolve the cadence to continue the user's schedule with.

    :func:`resolve_schedule`'s cadence half, for the callers that need only
    that -- the extend and rolling paths, the writer's own re-read, and
    :func:`app.services.pay_calendar.cadence_for`.  It is a forward rather than
    a second implementation: plan step **balance:X-bh-2** widened the read to a
    pair, and leaving this reading the row itself would have been two answers
    to "what cadence does this owner have" the moment one of them changed.

    **It is the SOFT door's cadence half**, and since plan step C4-d that is
    what distinguishes it from :func:`app.services.pay_calendar.cadence_for`
    rather than how much of the schedule each reads.  Both cost one query of
    one row; this one ANSWERS ``None`` for an owner with no schedule row and
    that one REFUSES them.  ``routes/salary/profiles._paychecks_per_year`` is
    why the soft half exists: a form must not 500 on the state it repairs.

    Args:
        user_id: The owning user's id.

    Returns:
        The STORED cadence in days, or ``None`` when the user has no
        ``budget.pay_schedule`` row -- since plan step C4-b-2 the same
        statement as "no pay periods" (``fk_pay_periods_schedule``).  The
        extend path treats ``None`` as "generate your first schedule first".
    """
    facts = resolve_schedule(user_id)
    return None if facts is None else facts.cadence_days


def resolve_shift(user_id: int) -> BusinessDayShiftEnum:
    """Resolve the convention to continue the user's schedule under.

    :func:`resolve_cadence`'s sibling for the second half of the rhythm, and
    it exists for ONE caller: ``pay_period_admin.extend_pay_periods``, the one
    door that records paydays without asking what the rhythm is.  Every other
    caller of :func:`upsert_schedule` is a door that STATES a convention
    (**R-PC56**: the question is asked wherever a cadence is), and hands that
    answer down.

    **It is a KNOWINGLY ACCEPTED second read of a row the same request already
    resolved**, and calling it anything softer would be the overclaim an
    adversarial review of 2026-09-05 struck from this paragraph.  The extend
    path builds a :class:`~app.services.pay_calendar.PayCalendar` first, which
    reads ``budget.pay_schedule`` through :func:`resolve_schedule`; this is a
    second scalar query against the same row, and it is the per-request
    duplicate-read class ledger rows **P68** and **P69** record.

    The alternative -- widening :class:`ScheduleFacts` and threading the value
    through ``PayCalendar`` -- is what plan step ``C14-e`` does, because that
    is the step where the PURE pay-calendar package acquires a reader for it.
    Doing it here would put a field on the derivation that nothing in it uses,
    which is the same trade from the other side.  So the cost is one indexed
    lookup on a form POST until C14-e, when this function and its query are
    deleted rather than kept.

    Args:
        user_id: The owning user's id.

    Returns:
        The stored convention as its
        :class:`~app.enums.BusinessDayShiftEnum` member, ready to travel in a
        :class:`Rhythm`.

    Raises:
        ValidationError: The user has no ``budget.pay_schedule`` row, or the
            row names a convention this application does not model.  Refused
            rather than answered with a fallback, for
            :func:`reread_schedule`'s reason: the one caller has already built
            a :class:`~app.services.pay_calendar.PayCalendar` for this owner,
            which refuses an owner without a schedule, so absence here is a
            row removed outside the application rather than a state to branch
            on.  A fallback would have to INVENT a convention, and inventing
            one is how a payday moves for an owner who was never asked.  The
            second arm is ``fk_pay_schedule_shift_id`` failing at the same
            time as the seed, which no application path reaches; it is refused
            rather than silently read as ``none`` for the same reason.
    """
    shift_id = (
        db.session.query(PaySchedule.shift_id)
        .filter(PaySchedule.user_id == user_id)
        .scalar()
    )
    if shift_id is None:
        raise ValidationError(
            f"user {user_id} has no budget.pay_schedule row, so there is no "
            f"payday convention to continue their schedule under."
        )
    shift = ref_cache.business_day_shift_member(shift_id)
    if shift is None:
        raise ValidationError(
            f"user {user_id}'s pay schedule names business-day shift "
            f"{shift_id}, which this application does not model."
        )
    return shift
