"""
Shekel Budget App -- Pay Era Writer

**The ONE place in ``app/`` that changes ``budget.pay_eras``** (plan step
``pay_calendar:C17-a``, ruling **R-PC58**).  An era is one row per *how I have
been paid since* -- the rhythm a span of paydays runs on, kept when the next
span starts on a different one -- and every batch that records a payday
reaches this table through :func:`mint_era` and :func:`retire_eras`, called
from ``pay_period_write._apply`` and from nothing else.  The ERA RULE that
decides what a batch does here is stated in the same module, in two halves:
:func:`eras_describing` says which eras a batch leaves standing, and
:func:`era_to_mint` says whether it states a new one.

**Why a module of its own.**  :mod:`app.services.pay_schedule_service` reads
the eras (as :class:`~app.services.pay_schedule_service.ScheduleFacts`) and
writes the schedule row; this module writes the eras.  That is the reader /
writer split ``pay_period_service`` / ``pay_period_write`` already draws for
the paydays (plan step C3-b), and it was made when the era doors took that
service past pylint's 1,000-line ceiling -- a split rather than a trim, on
the terms ledger row **PC-498** records.  The two refusals both writers ask
(the cadence bound, the cadence-convention pairing) stay in the service beside
the column bounds they state, and this module imports them.

**A caller holding a loaded ``PaySchedule`` must re-read it after either
door**: ``PaySchedule.eras`` is view-only, so an insert reaches no loaded
collection, the bulk delete synchronises nothing, and a joined load does not
replace a collection the identity map already holds (measured 2026-09-11).
``pay_period_write._apply`` expires the session after it;
``pay_schedule_service.reread_schedule`` is the door for anything else.

Flask-isolated -- takes and returns plain data, never imports ``request`` /
``session``.  Flushes so callers see assigned ids, but never commits: the
route layer owns the transaction.
"""

from datetime import date

from app import ref_cache
from app.enums import PayCadenceKindEnum
from app.extensions import db
from app.models.pay_era import PayEra
from app.services import pay_schedule_service
from app.services.pay_rhythm import Era, Rhythm, era_covering


def mint_era(user_id: int, era: Era) -> PayEra:
    """Record that the owner has been paid on *era*'s rhythm since its day.

    **The ONE writer of ``budget.pay_eras``** (plan step ``C17-a``, ruling
    **R-PC58**).  Called by ``pay_period_write.record_paydays`` when a batch
    states a rhythm the era covering its first payday does not already hold
    -- a first schedule, a cadence or convention changed going forward, or a
    phase off the covering grid -- and by nothing else: a batch that continues
    an era mints nothing, which is what closed the read-path re-judging
    ledger row **N-494** recorded.

    **The refusals live HERE, at the column's writer** (plan step X-ad-a's
    placement, carried over).  The cadence bound and the cadence-convention
    pairing are asked immediately before the write, so no door can persist
    what ``ck_pay_eras_cadence_range`` or the collision floor refuses; every
    caller that takes the values from a form asks the same functions earlier
    so the refusal lands on the control the owner chose.

    **It writes the era as ONE row, and the pairing is why rather than
    tidiness.**  ``shift_id`` is legal only on a cadence longer than the
    longest run of closed days
    (:func:`~app.services.pay_schedule_service.reject_shift_on_short_cadence`),
    so the two columns carry a joint rule; written by two doors in sequence the
    row would pass through an intermediate state that is not the one the
    request means.  One statement judged against the state the operation
    LEAVES BEHIND has no such hole -- the same principle
    ``pay_period_write._PaydayChange`` exists for one module over.

    **The owner's ``budget.pay_schedule`` row must already exist**
    (``fk_pay_eras_schedule``): ``pay_period_write._apply`` calls
    :func:`~app.services.pay_schedule_service.ensure_schedule_row` first, and
    no other caller exists.

    Args:
        user_id: The owning user's id.
        era: The :class:`~app.services.pay_rhythm.Era` to persist.  Its
            ``effective_from`` must not equal an existing era's
            (``uq_pay_eras_user_effective_from``); the writer retires every
            era taking effect on or after it before calling here, so the key
            is the guard rather than the door.

    Returns:
        The new :class:`~app.models.pay_era.PayEra` row, flushed.

    Raises:
        ValidationError: The cadence falls outside
            :data:`~app.models.pay_era.CADENCE_DAYS_MIN` ..
            :data:`~app.models.pay_era.CADENCE_DAYS_MAX`, or the pair is one
            no calendar can derive
            (:func:`~app.services.pay_schedule_service.reject_shift_on_short_cadence`).
            A 400 rather than a 500: every door in front of this one takes
            the values from a form.
    """
    pay_schedule_service.reject_out_of_range_cadence(era.rhythm.cadence_days)
    pay_schedule_service.reject_shift_on_short_cadence(era.rhythm)
    # The one place a rhythm's convention and kind become ids, which is the
    # storage boundary and nowhere else -- ``recurrence._authoring`` resolves
    # the same vocabulary at the same moment for the same reason.
    row = PayEra(
        user_id=user_id,
        effective_from=era.effective_from,
        kind_id=ref_cache.pay_cadence_kind_id(era.kind),
        cadence_days=era.rhythm.cadence_days,
        shift_id=ref_cache.business_day_shift_id(era.rhythm.shift),
    )
    db.session.add(row)
    db.session.flush()
    return row


def eras_describing(
    stored: "pay_schedule_service.ScheduleFacts | None",
    surviving_paydays: "set[date]",
) -> "tuple[Era, ...]":
    """Return the eras a batch leaves standing: those with a surviving payday.

    The era rule's first half, beside its second (:func:`era_to_mint`).  An
    era takes effect on its own day, so one taking effect after the last
    surviving payday describes nothing the batch keeps; the batch's new
    paydays all fall past that day (``pay_period_write``'s floor) and take
    their era from the mint decision.  ``retire_eras`` is asked the same
    boundary, so what is judged against and what survives are one set.

    Args:
        stored: The owner's schedule facts before the batch, or ``None``.
        surviving_paydays: The paydays the batch leaves standing.

    Returns:
        The surviving eras, ``effective_from`` ascending; empty when nothing
        survives or the owner holds no era.
    """
    if stored is None or not surviving_paydays:
        return ()
    latest = max(surviving_paydays)
    return tuple(e for e in stored.eras if e.effective_from <= latest)


def era_to_mint(
    eras: "tuple[Era, ...]",
    first_payday: date,
    rhythm: Rhythm,
) -> "Era | None":
    """Return the era a batch states, or ``None`` when it continues one.

    **The era rule's decision, and the whole of it** (plan step
    ``pay_calendar:C17-a``).  Asked by ``pay_period_write.record_paydays``
    of the facts it read for the floor, and answered HERE because whether a
    rhythm is a new era is the era's own question, beside the door that mints
    one.  A batch mints an era at its first payday when

    1. the owner holds no era -- a first schedule;
    2. the era covering that day runs on a DIFFERENT rhythm -- a cadence or a
       convention corrected going forward, which is the state ledger row
       **N-492** records the old single row could only overwrite; or
    3. the day is OFF the covering era's grid -- a phase corrected going
       forward, which ``regenerate`` expresses by a corrected first payday at
       the same cadence.

    A batch whose first payday sits on the covering era's grid at that era's
    rhythm CONTINUES it and mints nothing.  That is every extend and rolling
    top-up for an owner whose latest era covers the horizon -- every owner
    the migration backfills: their first payday comes from
    :func:`~app.services.pay_calendar.nominal_payday_after`, stepped from the
    latest era's own ``effective_from``, so it is on that grid by
    construction and the rhythm is the one read off the calendar.  *An owner
    who truncated below their latest era's day is the exception, and an
    adversarial review of C17-a named it: that era describes no surviving
    payday, the batch retires it, and the extend restates its rhythm as an
    era from the day it continues -- and is judged like any stated era.*

    **The grid test is arithmetic on the NOMINAL day, which is what
    *first_payday* is** (the writer's own reading of it).  ``C17-d`` branches
    it on the era's kind.

    Args:
        eras: The eras the batch LEAVES STANDING, ``effective_from``
            ascending -- the owner's eras less those the batch supersedes,
            which ``pay_period_write.record_paydays`` computes from the
            surviving paydays.  Empty for a first schedule, and for a batch
            that leaves no payday standing.
        first_payday: The batch's first nominal payday.
        rhythm: The rhythm the batch states.

    Returns:
        The :class:`~app.services.pay_rhythm.Era` to mint, or ``None``.
    """
    if not eras:
        return Era(
            effective_from=first_payday, kind=PayCadenceKindEnum.FIXED_DAYS,
            rhythm=rhythm,
        )
    covering = era_covering(eras, first_payday)
    on_grid = (
        (first_payday - covering.effective_from).days
        % covering.rhythm.cadence_days == 0
    )
    if covering.rhythm == rhythm and on_grid:
        return None
    return Era(
        effective_from=first_payday, kind=PayCadenceKindEnum.FIXED_DAYS,
        rhythm=rhythm,
    )


def retire_eras(user_id: int, effective_after: "date | None") -> int:
    """Delete the owner's eras taking effect after a day, or all of them.

    **The ONE door that removes from ``budget.pay_eras``**, and its caller is
    ``pay_period_write._apply`` alone.  A recording batch SUPERSEDES every
    era taking effect after the last payday it leaves standing -- none of
    them describes a payday that stands, and every payday the batch records
    falls past that day -- and a batch that leaves NO payday standing
    (``reset``) leaves no era either, since an era is *how I have been paid
    since* and nobody is.

    An era is never retired by a batch that records nothing: truncating a
    schedule's tail shortens the record and leaves the owner's declared
    rhythm as it was, so the next extend continues the era they stated --
    restating it from the day it continues, when the truncate cut below
    that era's own day.

    Args:
        user_id: The owning user's id.
        effective_after: Retire every era whose ``effective_from`` is strictly
            after this day; ``None`` retires every era the owner holds.

    Returns:
        How many rows were deleted.
    """
    query = db.session.query(PayEra).filter(PayEra.user_id == user_id)
    if effective_after is not None:
        query = query.filter(PayEra.effective_from > effective_after)
    return query.delete(synchronize_session=False)
