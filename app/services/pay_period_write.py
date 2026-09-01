"""
Shekel Budget App -- Pay Period Writer

**The ONE place in ``app/`` that changes ``budget.pay_periods``** (plan step
C3-b, developer ruling 2026-08-10).  Every door that grows, rebuilds or
shortens an owner's schedule -- generate, extend, the rolling top-up,
regenerate, reset, truncate -- reaches the table through
:func:`record_paydays` or :func:`retire_paydays` and through nothing else.
``pay_period_service`` keeps only its readers; ``pay_period_admin`` keeps only
its orchestration and its two gates.

That split is C3-a's, one level up.  C3-a moved the read-only lock classifier
into :mod:`app.services.pay_period_locks` because a read-predicate and four
destructive writers are two concerns; the same argument separates *deciding*
that a schedule should change from *changing* it.  The invariant below has one
home, so plan steps C4 (drop the derived columns), C6 (a payday inserted
mid-schedule) and C7 each inherit it by reading one file.

*No pylint checker enforces the boundary, deliberately.* Finding
``balance:N-147`` already records that two custom checkers police their rule
with a hand-maintained list of module names, and a third would widen that
finding rather than close it.  The boundary is held by there being exactly one
``PayPeriod(...)`` construction and exactly one ``DELETE`` in ``app/`` -- both
in this module -- which one ``grep`` answers and
``TestThereIsOneWriter`` asserts.  (``tests/`` builds and deletes them freely,
as it must: several suites exist to hand this writer a state no door can
produce.)

The invariant, and it is the whole point
========================================

``budget.pay_periods`` stores three values per row and only ``start_date`` is a
fact.  ``end_date`` and ``period_index`` are a CACHE of one function of the
owner's payday set (``pay_calendar.derive_periods``).  **A cache refreshed only
at its edge is not a cache, it is a second source of truth**, so:

    after any write here, every one of the owner's stored rows equals
    ``derive_periods`` over their COMPLETE payday set and stored cadence.

That is what makes plan step C4's ``DROP COLUMN`` provably unable to change a
number, and it is why :func:`_write_derivation` walks the whole calendar rather
than patching the row next to the batch.  It is also less code than the
alternative: there is no concept of "the preceding period" for C6's
mid-schedule insert to invalidate later.

**It writes nothing on a healthy schedule.**  Stored and derived agree except
where a hole exists, and production has none (61 paydays, 0 index mismatches, 0
end mismatches, 0 gaps -- re-measured 2026-08-10, and re-driven through all
three door shapes on a clone by ``tests/manual/verify_pay_period_writer.py``).
SQLAlchemy emits no ``UPDATE`` at all for a reassignment that changes nothing
(measured on the pinned 2.0.49 while plan step C3-b was written, against the
schedule-rebuild re-pointer plan step R7b-4 has since deleted), so the recompute costs one
derivation and no statements.  Where it DOES move a value it is repairing a
hole and says so at WARNING; where the disagreement runs the OTHER way -- an
overlap, which no writer here can produce -- it refuses instead of guessing
(:func:`_write_derivation`).

The one refusal, and why the second was DELETED
==============================================

Plan ruling **R-PC1** stated ONE rule -- "the last paycheck must hold no row
dated on or after the new payday".  Tracing it against ``shekel-prod-db`` found
it wrong in both directions, so the developer ruled it into two (2026-08-10): a
structural floor and a financial coverage rule.  What survives is the floor.

:func:`_reject_backward_payday` -- **structural, and TEMPORARY.**  A new payday
may not land inside a paycheck the owner already has.  Its only job is keeping
plan step **C6**'s mid-schedule insert closed, and **C6 removes it.**

**The coverage rule was DELETED (developer ruling 2026-08-11), and the argument
is recorded because this module could re-derive it.**  It refused any write
that moved a day from COVERED to UNCOVERED underneath a SETTLED row filed in a
surviving period, and it was approved on the claim that stranding such a day
reproduces ``balance:N-128`` -- the two halves of the cash period view
disagreeing.  **That claim was false, and it was the whole of the case for the
rule.**  ``_cash_periods._assemble_figures`` values each column at that
period's OWN ``end_date`` and computes ``period_timing`` as ``moved - net``, so
a settle day past the last reported end is absent from BOTH sides of ruling
R-K's identity and cancels.  The money reports as a timing remainder -- the row
ruling R-DH split out to carry precisely this -- and the balance is right
either way: on that end date the bank had genuinely not taken it.  Pinned by
``test_cash_period_view.py``'s
``test_a_settle_day_past_the_window_keeps_every_column_exact``, and driven on a
production CLONE: retiring 58 of 61 periods strands three real rows totalling
``$177.47`` and every surviving column reconciles to the cent.

**Two things that are NOT evidence for it, stated because the first draft of
this paragraph offered both.**  Production has never been in the refused state
-- **0** settled rows fall outside its schedule's coverage -- so production is
silent on this rule rather than supporting its removal.  What production shows
is the DESIGN it rests on: 21 of 160 settled rows settle outside their OWN
paycheck (measured 2026-08-11), carried by the remainder with nothing refusing.
And the refusal's message offered THREE remedies, not one -- re-date the row,
move it, or choose a schedule that still covers the day.  The first two falsify
when money moved; the third is declining the edit.  **The measured COST is what
decided it**: 5 of the owner's 61 truncation points refused, one over three
rows that cleared the bank ONE day late.

**"Outside the reported window" is not a windowing nicety on the load-bearing
surface**: ``routes/grid/page.py`` passes the owner's COMPLETE period set, so there
the phrase means "outside every paycheck they have".  The identity holds all
the same -- it is a property of where each column is valued, not of how the
window was chosen -- but the reassurance must not be read as "only a partial
view sees this".

The cadence rule
================

``budget.pay_schedule.cadence_days`` is a FORECAST setting: after this arc its
one job is projecting past the last recorded payday.  **A batch that CREATED at
least one payday persists the cadence it created them at; a batch that created
none leaves it alone** (developer ruling 2026-08-10, closing findings **P12**
and **P29**).  The alternative trigger weighed and rejected -- "at least two
paydays, or the owner's first" -- silently discards a REQUIRED form input: a
regenerate at ``num_periods=1, cadence_days=30`` would build a 14-day paycheck
and say nothing.

The derivation and ``budget.pay_schedule`` therefore take the SAME value, so
"the horizon the app projects" and "the end stored on the last row" are one
number by construction -- ledger row **P28** measured them coming apart, and
there is no longer a second value to disagree with.  The extend door has no
cadence input at all any more (finding **P29**, and finding **P30**'s objection
to answering it with a write): it continues an existing schedule, so the
question does not arise there, and the parameter, its Marshmallow field and the
rolling top-up's pass-through were all deleted.

Flask-isolated: takes and returns plain data, never imports ``request`` /
``session``.  Flushes so callers see assigned ids; never commits (the route
owns the transaction), so a refusal raised here leaves nothing durable behind.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.exceptions import PayPeriodOverlapStored, ValidationError
from app.extensions import db
from app.models.pay_period import MIN_MATERIALISABLE_CADENCE_DAYS, PayPeriod
from app.services import pay_schedule_service
from app.services.pay_calendar import DerivedPeriod, derive_periods
from app.utils.log_events import (
    BUSINESS,
    EVT_PAY_PERIODS_GENERATED,
    EVT_PAY_PERIODS_REMATERIALISED,
    log_event,
)

logger = logging.getLogger(__name__)

#: Inclusive bounds on how many pay periods ONE call may create.
#:
#: A generation policy rather than a column constraint, which is why it lives
#: with the writer rather than on a model: 260 is ten years of fortnightly
#: paydays (five of weekly), past any horizon the app renders and well inside
#: what one transaction can generate and populate.  The floor is 1 because a
#: batch that creates nothing is a caller mistake, not a no-op -- at
#: registration it used to surface several statements later as
#: ``create_account`` complaining that the owner had no pay periods.
#:
#: Read by :func:`reject_unmaterialisable_batch` and by the Marshmallow fields
#: in ``app.schemas.validation.pay_periods``, which import them from here: the
#: bound belongs to the writer, and a schema that stated its own copy is how
#: four form fields came to hold four literals.
PERIOD_BATCH_MIN = 1
PERIOD_BATCH_MAX = 260


def reject_unmaterialisable_batch(num_periods: int, cadence_days: int) -> None:
    """Refuse a batch this writer cannot turn into ``budget.pay_periods`` rows.

    **The writer's OWN preconditions, stated once and asked before anything is
    written** (plan step X-ad-a).  Both were held by each caller's Marshmallow
    field until registration became a fifth caller, and a bound held by
    remembering is a bound the next door does without.

    * **The cadence floor is about the REPRESENTATION, not the schedule.**  A
      one-day pay cycle is legal and pay-calendar step C4 legalises it; what
      cannot hold one is a STORED ``end_date``, because
      ``ck_pay_periods_date_order`` requires ``start_date < end_date`` and a
      one-day period's derived end is its own start.  At a cadence of 1 the
      INSERT died as an unhandled ``CheckViolation`` 500 -- measured on both the
      settings form and the registration form.  See
      :data:`~app.models.pay_period.MIN_MATERIALISABLE_CADENCE_DAYS`, which C4
      deletes with the column.
    * **The batch size is a work bound.**  ``num_periods`` was refused by no
      service at all, so a non-form caller could ask for zero periods (which
      then fails several statements later, in ``create_account``, under a
      message about accounts) or for a hundred thousand (383 years of
      fortnights in one transaction).

    The upper cadence bound is NOT asked here: it belongs to
    ``budget.pay_schedule``'s CHECK and is asked by that column's one writer,
    :func:`~app.services.pay_schedule_service.reject_out_of_range_cadence`.
    Two bounds, two owners, because they answer different questions -- what may
    be STORED as a schedule, and what this writer can MATERIALISE from it.

    Args:
        num_periods: How many periods the batch would create.
        cadence_days: Days between the paydays it would create.

    Raises:
        ValidationError: Either value is outside what this writer can produce.
            Each message names the offending value and the bound it broke.
    """
    _reject_unmaterialisable_cadence(cadence_days)
    if not PERIOD_BATCH_MIN <= num_periods <= PERIOD_BATCH_MAX:
        raise ValidationError(
            f"Number of pay periods must be between {PERIOD_BATCH_MIN} and "
            f"{PERIOD_BATCH_MAX}; got {num_periods}."
        )


def _reject_unmaterialisable_cadence(cadence_days: int) -> None:
    """Refuse a cadence no stored ``end_date`` can express.

    Split out of :func:`reject_unmaterialisable_batch` because it is asked
    TWICE per write and about two different values: once about the cadence a
    caller SUBMITTED, and once about the cadence the recompute will actually
    project the horizon with, which since the cadence rule is the value
    ``budget.pay_schedule`` holds rather than the argument.  Those coincide at
    every door today; they would not for a legacy owner whose stored cadence
    predates the form floor, and the difference between a rendered form error
    and a ``CheckViolation`` 500 is the whole reason the check exists.

    **The second value is now the STORED cadence and nothing else** (plan step
    C4-b-2).  ``resolve_cadence`` used to answer an INFERRED one for an owner
    with no ``budget.pay_schedule`` row, which ``ck_pay_schedule_cadence_range``
    never saw and nothing bounded above; ``fk_pay_periods_schedule`` makes that
    owner unstorable, so what reaches here is a column value in 1..365 and the
    only thing left to refuse is the 1 this writer cannot materialise.

    Args:
        cadence_days: Days between paydays -- submitted or stored.

    Raises:
        ValidationError: *cadence_days* is below
            :data:`~app.models.pay_period.MIN_MATERIALISABLE_CADENCE_DAYS`.
    """
    if cadence_days < MIN_MATERIALISABLE_CADENCE_DAYS:
        raise ValidationError(
            f"Days between paydays must be at least "
            f"{MIN_MATERIALISABLE_CADENCE_DAYS}; got {cadence_days}.  A pay "
            "period records the day before the next payday as its end, so a "
            "shorter cycle has no room to record one."
        )


def record_paydays(
    user_id: int,
    first_payday: date,
    num_periods: int,
    cadence_days: int,
    retiring_ids: "set[int] | None" = None,
) -> "list[PayPeriod]":
    """Record a batch of paydays and re-materialise the owner's whole calendar.

    **The one door that adds to ``budget.pay_periods``.**  It records paydays
    (``first_payday``, then every ``cadence_days`` after it, ``num_periods``
    times), persists the cadence when the batch actually recorded something,
    and then rewrites EVERY one of the owner's rows from
    ``pay_calendar.derive_periods``.  A payday already on the table is skipped
    rather than duplicated, so re-running with the same start and a larger
    count legitimately extends the schedule.

    **``retiring_ids`` is what makes regenerate and reset ONE operation**, and
    an adversarial review of this step is why it exists.  Those two doors replace
    a span: they drop periods and record others, and applying the halves through
    two separate calls derived, refused against and MATERIALISED an interval
    that existed for one statement -- the schedule minus its tail, before the
    rebuild that is the whole point of the door.  Handing both halves to one
    call means every refusal, and the derivation itself, see the state the
    operation actually leaves behind.  The rule that measured this (the
    coverage rule, deleted 2026-08-11) is gone; the reason it is ONE call is
    not, because :func:`_write_derivation` would otherwise re-materialise the
    whole calendar twice per rebuild and log the intermediate shape as a repair.

    **It takes IDS, not rows, since plan step C2-f3b.**  It was a
    ``list[PayPeriod]`` that this function read one thing off -- ``.id`` -- so
    every caller had to hold ORM rows for a set of integers, which is what kept
    ``pay_period_admin`` querying ``budget.pay_periods`` for values it decides
    nothing with.  That module now decides in
    :class:`~app.services.pay_calendar.DerivedPeriod` values and the ORM read
    lives HERE, in the one module that writes the table
    (:func:`_owner_periods`), which is where a read whose only purpose is to
    feed a write belongs.  It also makes the OWNER scoping structural rather
    than a property of the callers: an id naming another owner's period is not
    in :func:`_owner_periods`' answer, so it retires nothing and is not counted.

    It replaced ``pay_period_service.generate_pay_periods`` at plan step C3-b,
    and the difference is what the step is about: that function AUTHORED
    ``end_date = start_date + cadence_days - 1`` and
    ``period_index = max_index + 1`` on the new rows and left every existing
    row alone, which is how a schedule came to hold days no paycheck covered.
    This one computes nothing: the derivation is the single definition and this
    writes what it says.

    It also absorbed ``establish_schedule``, and that collapse is the cadence
    rule working.  "Create the periods" and "record the cadence they run at"
    used to be two calls a caller composed, which is how one door could do the
    first without the second (finding **P29**) and another could do the second
    without the first (finding **P12**).  With the rule inside, they are one
    operation and neither half has a door of its own.

    Args:
        user_id: The owning user's id.
        first_payday: The batch's first payday -- the day money arrived, never
            a period boundary computed from one.
        num_periods: How many paydays the batch covers, including any that
            already exist.
        cadence_days: Days between the batch's paydays.  Persisted as the
            owner's forecast cadence when the batch records at least one new
            payday; ignored otherwise.
        retiring_ids: ``budget.pay_periods.id`` values to DELETE as part of the
            same operation, for the two doors that replace a span rather than
            extend one.  The caller has already run whatever gates decide they
            may go; this only carries them out, so that the refusals below see
            the operation's final payday set.  Any id that is not one of
            *user_id*'s own periods is silently inert -- it names no row this
            function can reach.  Defaults to retiring nothing.

    Returns:
        The newly created :class:`~app.models.pay_period.PayPeriod` objects,
        flushed so their ids are assigned, ``start_date`` ascending.  Empty when
        every requested payday was already on the table -- a no-op the caller
        can see, which is what lets the cadence stay untouched.

    Raises:
        ValidationError: *first_payday* is not a plain ``date``; the batch size
            or cadence is one this writer cannot materialise
            (:func:`reject_unmaterialisable_batch`); or the batch's earliest new
            payday falls before the forward-only floor
            (:func:`_reject_backward_payday`).
        ValidationError: Raised by
            :func:`~app.services.pay_schedule_service.upsert_schedule` when
            *cadence_days* falls outside ``ck_pay_schedule_cadence_range``.
            Nothing is written first: the upsert runs before any row is added.
    """
    _reject_undatable_payday(first_payday)
    reject_unmaterialisable_batch(num_periods, cadence_days)

    current = _owner_periods(user_id)
    doomed = retiring_ids or frozenset()
    keep = [period for period in current if period.id not in doomed]
    by_payday = {period.start_date: period for period in keep}

    new_paydays = [
        payday
        for payday in _requested_paydays(first_payday, num_periods, cadence_days)
        if payday not in by_payday
    ]
    # The floor reads the cadence the owner's LAST SURVIVING PAYCHECK currently
    # runs at, which is the one stored BEFORE this batch -- the question it asks
    # is how far that paycheck already reaches, not how far the next one will.
    # An owner moving from fortnightly to weekly is therefore bounded at a
    # fortnight and then continues at a week, which is what "correct my cadence
    # going forward" means and what it cannot mean retroactively.
    _reject_backward_payday(
        by_payday, new_paydays, pay_schedule_service.resolve_cadence(user_id),
    )

    created = _apply(
        _PaydayChange(
            user_id=user_id,
            current=current,
            keep=keep,
            recording=new_paydays,
            cadence_days=cadence_days,
        ),
    )
    log_event(
        logger, logging.INFO, EVT_PAY_PERIODS_GENERATED, BUSINESS,
        "Pay periods generated",
        user_id=user_id,
        count=len(created),
        retired=len(current) - len(keep),
        start_date=first_payday.isoformat(),
        cadence_days=cadence_days,
    )
    return created


def retire_paydays(user_id: int, doomed_ids: "set[int]") -> int:
    """Delete the periods *doomed_ids* names and re-materialise what survives.

    **The one door that removes from ``budget.pay_periods``.**  Truncate,
    regenerate's rebuild step and reset's whole-schedule wipe all reach the
    table here; the LOCK and DISCARD gates that decide WHICH periods may go
    stay with ``pay_period_admin``, because deciding is a different concern
    from doing (``pay_period_locks``' own split, one level up).

    **The survivors are re-materialised, and without that plan step C1's
    equality claim is false.**  A delete re-derives nothing today, so paydays
    ``[Jan 2, Jan 16, Feb 11]`` truncated through Jan 16 leave the January
    paycheck a stored end of Feb 10 where the derivation says Jan 29 -- the
    period's successor is gone, so its end falls back to the cadence
    projection.  An on-cadence fixture cannot see it (``lead(start) - 1`` and
    ``start + cadence - 1`` coincide there), which is why the step owed it
    explicitly.

    One bulk ``DELETE`` so PostgreSQL performs the whole cascade in one pass:
    transactions and transfers (and both shadows, preserving the transfer
    invariant) go and DB-level audit triggers still fire.  **RECURRENCE RULES
    are no longer in that cascade** (plan step R7b-4): a rule's opening bound
    is a DATE rather than a pay-period FK, so retiring a payday cannot reach
    it.  Per-object ``session.delete()`` would
    instead trip SQLAlchemy's nullify-on-disassociate against the NOT NULL
    ``transactions.pay_period_id`` and raise before the DB cascade fires.
    Balance ASSERTIONS do NOT go -- ruling R-EO deleted
    ``account_anchor_history.pay_period_id``, so a schedule operation can no
    longer destroy the record of what the bank said.

    ``expire_all`` runs AFTER the re-materialisation, not before it: the
    survivors' loaded attributes are untouched by the ``DELETE``, so writing
    the derivation onto them costs no reload, where expiring first would make
    the dirty-check re-``SELECT`` one row at a time.

    **It takes IDS and reads the rows itself, since plan step C2-f3b**, for the
    reason :func:`record_paydays` gives at length: a read whose only purpose is
    to feed a write belongs in the module that writes, and the caller that used
    to supply the rows -- ``pay_period_admin`` -- now decides in
    :class:`~app.services.pay_calendar.DerivedPeriod` values and holds none.
    Because the delete set is ``current`` less ``keep``, an id from another
    owner (or a stale one) retires nothing rather than being deleted or counted.

    **What the re-read does and does NOT guarantee**, corrected by an
    adversarial review of this step.  Under every door that takes
    ``user_write_lock.lock_user_writes`` it cannot see FEWER rows than the gate
    did, which is the direction that matters: no period the caller refused to
    delete can be missing here.  It is not the SAME set, and a first draft said
    it was: ``POST /pay-periods/generate`` and ``auth_service.register_user``
    both reach :func:`record_paydays` without taking that lock (finding
    **P71**), so a concurrent generate can commit a payday between the gate's
    read and this one and this read sees a SUPERSET.  That is benign here and
    better than the caller-supplied snapshot it replaced -- the new row is then
    in ``current`` and in ``keep``, so :func:`_write_derivation` materialises
    it, where the old shape left it in neither and gave the newly-last survivor
    a cadence-projected end that could run past it.

    Args:
        user_id: The owning user's id.
        doomed_ids: The ``budget.pay_periods.id`` values to delete.  Empty is a
            legal, idempotent no-op, and so is a set naming nothing of this
            owner's.

    Returns:
        The number of pay periods actually deleted -- the size of the
        intersection of *doomed_ids* with this owner's periods, never the size
        of the argument.

    Raises:
        PayPeriodOverlapStored: A surviving row's stored end runs past its
            successor's payday -- a state no writer here can produce, so
            reaching it means the rows were edited outside this module
            (:func:`_write_derivation`).  This one raises AFTER the ``DELETE``
            has been issued, unlike the refusals in :func:`record_paydays`, and
            no route catches it: it is deliberately a 500, and nothing durable
            follows because this module never commits and the failed request's
            session is discarded without one.
    """
    current = _owner_periods(user_id)
    keep = [period for period in current if period.id not in doomed_ids]
    retired = len(current) - len(keep)
    if not retired:
        return 0
    _apply(
        _PaydayChange(
            user_id=user_id,
            current=current,
            keep=keep,
            recording=[],
            cadence_days=None,
        ),
    )
    return retired


@dataclass(frozen=True)
class _PaydayChange:
    """One change to an owner's payday set, applied as ONE operation.

    **Every door that writes composes into this, and an adversarial review of
    plan step C3-b is why it exists.**  ``retire_paydays`` and
    ``record_paydays`` used to apply their halves separately, so regenerate --
    which retires a tail and records a new one -- derived, judged and
    MATERIALISED an interval that existed for one statement and was then widened
    again by the rebuild that is the whole point of the door.

    A claim about the final state has to be evaluated against the final state.
    So the two halves arrive together, the derivation is taken ONCE over
    ``keep + recording``, and every refusal is asked of that one answer.  The
    rule whose false refusals measured this is gone (the coverage rule, deleted
    2026-08-11); the composition is not, and the remaining reason is
    :func:`_write_derivation`.  Applied separately it runs twice per rebuild,
    and the first pass shortens the newly-last survivor to a cadence projection
    -- a genuine rewrite, logged at WARNING as a schedule that "disagreed with
    the owner's paydays" -- which the second pass immediately undoes.  One call,
    one derivation, no phantom repair in the log.

    Attributes:
        user_id: The owning user.
        current: Every row the owner has NOW, read by the caller under its
            advisory lock.  Two things are read off it and both need the BEFORE
            set: which ids are being retired, and which payday was previously
            last (the one row :func:`_write_derivation` exempts from the overlap
            refusal, because its end was a projection).
        keep: The subset that survives -- ``current`` less whatever is being
            retired.  Whatever is not in it is deleted.
        recording: The paydays to create, already filtered of any that exist.
        cadence_days: The cadence to persist, iff *recording* is non-empty.
            ``None`` when nothing is recorded, where the stored value stands.
    """

    user_id: int
    current: "list[PayPeriod]"
    keep: "list[PayPeriod]"
    recording: "list[date]"
    cadence_days: "int | None"


def _apply(change: _PaydayChange) -> "list[PayPeriod]":
    """Carry out one payday change: refuse, delete, persist, materialise.

    **Every refusal a route RENDERS happens before the ``DELETE``**, which is
    what lets truncate keep promising it deletes nothing on a refusal and what
    makes the module docstring's "a refusal leaves nothing behind" true of this
    module rather than of its callers.  Step 1 carries all of them.  The one
    exception is :class:`PayPeriodOverlapStored`, raised from step 4: no route
    catches it, deliberately -- it means the rows were edited outside this
    module, and it is a 500 rather than a message.  The order is forced:

    1. Bound the cadence, then derive the calendar the operation would leave
       behind.  The cadence is the one this change PERSISTS when it records a
       payday, and the stored one otherwise -- read before the upsert rather
       than after it, because ``upsert_schedule`` stores the argument verbatim,
       so the two are the same value and only one of them is durable.  Both
       cadence refusals and ``derive_periods``' own live here, ahead of every
       statement.
    2. DELETE what is retired -- one bulk statement, scoped by OWNER as well as
       by id, so the scoping is structural rather than a property of the two
       callers that happen to pass owner-scoped lists.
    3. Persist the cadence (the rule: only a batch that RECORDS a payday).
    4. Write the derivation onto every surviving and new row.

    ``expire_all`` runs LAST, and only when something was deleted: the
    survivors' loaded attributes are untouched by the ``DELETE``, so writing the
    derivation onto them costs no reload, where expiring first would make the
    dirty check re-``SELECT`` one row at a time.

    Args:
        change: The whole change (:class:`_PaydayChange`).

    Returns:
        The newly created rows, flushed, in the derivation's order.

    Raises:
        ValidationError: The cadence the horizon would project at is one no
            stored ``end_date`` can express, or ``upsert_schedule`` refuses it.
        PayPeriodOverlapStored: A surviving row's stored end runs past its
            successor's payday, which no writer here can produce.
    """
    by_payday = {period.start_date: period for period in change.keep}
    horizon_cadence = (
        change.cadence_days if change.recording
        else _horizon_cadence(change.user_id)
    )
    if change.recording:
        # The STORAGE bound, asked here rather than left to ``upsert_schedule``
        # at step 4.  Moving the upsert last is what makes a refused batch leave
        # the cadence alone, and it also moved this check BEHIND the derivation
        # -- where an out-of-range value raised ``PayCalendarError``, a 500,
        # instead of the 422 the form renders.  A caller's own test caught it.
        # Asking the column's owner up front keeps the refusal where a form can
        # read it and still keeps the write last.
        pay_schedule_service.reject_out_of_range_cadence(change.cadence_days)
    if horizon_cadence is not None:
        _reject_unmaterialisable_cadence(horizon_cadence)

    derived = derive_periods(
        [(period.id, period.start_date) for period in change.keep]
        + [(None, payday) for payday in change.recording],
        horizon_cadence,
    )
    keep_ids = {period.id for period in change.keep}
    retiring_ids = [
        period.id for period in change.current if period.id not in keep_ids
    ]
    if retiring_ids:
        db.session.query(PayPeriod).filter(
            PayPeriod.user_id == change.user_id,
            PayPeriod.id.in_(retiring_ids),
        ).delete(synchronize_session=False)
    if change.recording:
        pay_schedule_service.upsert_schedule(
            change.user_id, change.cadence_days,
        )
    created = _write_derivation(
        change.user_id, derived, by_payday,
        projected_before=max(
            (period.start_date for period in change.current), default=None,
        ),
    )
    if retiring_ids:
        db.session.expire_all()
    return created


def owner_period_ids(user_id: int) -> "set[int]":
    """Return every ``budget.pay_periods.id`` *user_id* holds.

    **The door for a caller that means "the whole schedule"** -- today
    ``pay_period_admin.reset_pay_periods``, which retires every period and
    rebuilds from a corrected start.  It lives HERE, beside the write it feeds,
    for :func:`record_paydays`' reason: a read whose only purpose is to name
    rows for a write belongs in the module that owns the table.

    **It is deliberately not a calendar read** (adversarial review of plan step
    C2-f3b).  A first cut spelled this ``calendar_for(user_id).saved()``, which
    made the door that REPAIRS a broken schedule depend on the schedule being
    derivable: an owner with no ``budget.pay_schedule`` row whose last period
    spans more than a year resolved a cadence outside 1..365, and
    ``derive_periods`` refuses it -- so reset, which used to succeed there
    (``keep`` is empty, and :func:`_apply` derives at the SUBMITTED cadence),
    became an unhandled 500.  *That particular owner is unstorable since plan
    step C4-b-2 (ledger rows **P8** / **P35**), so the example no longer
    reproduces; the RULE it was an example of is what this door is built on and
    is not weakened by losing it.*  The identity of a row is not a derived value
    and must not be reached through one -- a repair door that asks the
    derivation for the ids it is about to fix can only repair schedules that
    were not broken.

    Args:
        user_id: The owning user's id.

    Returns:
        The ids, empty for an owner who has never generated a schedule.
    """
    return {
        row[0] for row in
        db.session.query(PayPeriod.id)
        .filter(PayPeriod.user_id == user_id)
        .all()
    }


def _owner_periods(user_id: int) -> "list[PayPeriod]":
    """Return every one of *user_id*'s pay periods, payday ascending.

    Ordered by ``start_date`` rather than by ``period_index``, and that is the
    normalization rather than a preference: the payday is the fact and the
    ordinal is one of the two values this module is about to recompute, so
    reading in ordinal order would sort by the answer.  Plan step C4 drops the
    column this query would otherwise have named.

    Args:
        user_id: The owning user's id.

    Returns:
        The owner's :class:`~app.models.pay_period.PayPeriod` rows, ``start_date``
        ascending.  Empty for an owner who has never generated a schedule.
    """
    return (
        db.session.query(PayPeriod)
        .filter(PayPeriod.user_id == user_id)
        .order_by(PayPeriod.start_date)
        .all()
    )


def _horizon_cadence(user_id: int) -> "int | None":
    """Return the cadence the owner's horizon is projected at, refusing a bad one.

    The STORED cadence, never the one a caller submitted, because the last
    period's derived end and every projection past it read
    ``budget.pay_schedule`` -- so taking the argument here is exactly how
    finding **P28** measured the two coming apart.  Under the cadence rule the
    two are equal at every door that records a payday; this function is what
    makes that a property of the code rather than of the callers.

    Args:
        user_id: The owning user's id.

    Returns:
        The days between paydays the horizon projects at, or ``None`` for an
        owner with no ``budget.pay_schedule`` row -- which since plan step
        C4-b-2 is an owner with no pay periods, ``fk_pay_periods_schedule``
        making the two one fact.  ``None`` is legal ONLY beside an empty payday
        set, which is the rule ``derive_periods`` states and enforces; the key
        is now what makes that pairing hold here, where it used to rest on the
        callers -- a batch that records a payday upserts the row first, and one
        that records none is asked about an owner who already has periods.
        Returned rather than refused so that rule has one home.

    Raises:
        ValidationError: The stored cadence is below
            :data:`~app.models.pay_period.MIN_MATERIALISABLE_CADENCE_DAYS`, so
            the last period's derived end would land on its own payday and
            ``ck_pay_periods_date_order`` would refuse the write as a 500.
            Unreachable from any current door -- all four bound the cadence at
            the writer's floor before storing it -- and asked anyway, because
            the value is read from the database rather than from the argument
            those doors checked.
    """
    cadence_days = pay_schedule_service.resolve_cadence(user_id)
    if cadence_days is not None:
        _reject_unmaterialisable_cadence(cadence_days)
    return cadence_days


def _reject_undatable_payday(payday: date) -> None:
    """Refuse a payday that is not a plain ``datetime.date``.

    ``datetime`` is a subclass of ``date``, so the bare ``isinstance`` check
    this replaced accepted one -- and every derived end would then carry a time
    component, comparing unequal to the ``DATE`` column it is stored in and
    placing a day's money by an accident of the process clock.
    ``pay_calendar._validated`` refuses the same value, but it raises
    ``PayCalendarError``, which no route catches; refusing here makes it the
    form error it actually is.

    Args:
        payday: The candidate first payday.

    Raises:
        ValidationError: *payday* is not a ``date``, or is a ``datetime``.
    """
    if not isinstance(payday, date) or isinstance(payday, datetime):
        raise ValidationError(
            f"first_payday must be a date object, got "
            f"{type(payday).__name__}.  budget.pay_periods.start_date is a "
            f"DATE column and the app's civil day is display_today(); a "
            f"datetime here would place a day's money by the process timezone."
        )


def _requested_paydays(
    first_payday: date, num_periods: int, cadence_days: int,
) -> "list[date]":
    """Return the paydays a batch asks for, whether or not they already exist.

    Args:
        first_payday: The batch's first payday.
        num_periods: How many paydays the batch covers.
        cadence_days: Days between them.

    Returns:
        *num_periods* days, ascending, ``cadence_days`` apart.
    """
    return [
        first_payday + timedelta(days=cadence_days * step)
        for step in range(num_periods)
    ]


def _reject_backward_payday(
    existing_by_payday: "dict[date, PayPeriod]",
    new_paydays: "list[date]",
    cadence_days: "int | None",
) -> None:
    """Refuse a batch whose earliest new payday would land inside a paycheck.

    **The forward-only rule, keyed on the PAYDAY** (ruling **R-PC1** as split
    2026-08-10).  It replaces ``pay_period_service._reject_overlapping_batch``,
    which bounded a batch on ``max(end_date)`` -- a derived column plan step C4
    drops, and one that made the guard do a second job nothing credited it
    with.

    That second job is this function's ONLY job: **keeping plan step C6 closed.**
    Under the derivation a gap and an overlap are not expressible -- consecutive
    paydays define adjacent intervals -- so there is nothing left here to refuse
    except a payday landing INSIDE an existing paycheck, which splits it.  C6
    owns that, behind two questions ledger row **P10** records as unruled: what
    happens to a row ``DerivedPeriod.attribution_day`` would now clamp into the
    wrong half,
    and whether the split-off payday is repopulated (a monthly billed twice) or
    left empty (income understated for the whole horizon).  **When C6 answers
    them, this function is what it deletes.**

    **The floor is ONE CADENCE after the latest payday, and an adversarial
    review of this step is why it is not two days.**  The first cut bounded at
    ``latest_payday + MIN_MATERIALISABLE_CADENCE_DAYS``, on the reasoning that
    the only insert worth refusing is one before an existing payday.  That is
    wrong by the length of a paycheck: the LAST period runs to
    ``latest_payday + cadence_days - 1``, so every day in
    ``[latest_payday + 2, latest_payday + cadence_days - 1]`` splits it -- and
    P10's BOTH damage arms are then reachable through a door P10 says is closed.
    Measured on the two-period fortnightly schedule: recording 2026-01-23 shrank
    the 2026-01-16 paycheck from 01-29 to 01-22 and moved a row due 01-25 from
    rendering on 01-25 to rendering on 01-22, while ``/pay-periods/generate``
    left the split-off half EMPTY and ``regenerate`` repopulated it beside the
    row the shrunk half kept -- one monthly billed twice in what had been one
    paycheck.

    **It refuses exactly what the guard it replaces refused**, then, and the
    change is which values it reads: the latest PAYDAY and the stored CADENCE,
    both of which survive plan step C4, rather than the ``end_date`` column C4
    drops.  On any schedule this app can write those two spellings select the
    same set, because the last period's end IS ``payday + cadence - 1``.

    Args:
        existing_by_payday: The owner's periods keyed by payday, empty for a
            first-time schedule.
        new_paydays: The paydays this batch would create -- already filtered of
            any that exist, so a re-run naming existing days is bounded on what
            it would actually add.
        cadence_days: The owner's stored cadence, which sets how far the last
            paycheck reaches.  ``None`` only beside an empty payday set, where
            there is no floor to apply.

    Raises:
        ValidationError: The earliest new payday falls before the floor.
    """
    if not existing_by_payday or not new_paydays:
        return
    latest_payday = max(existing_by_payday)
    floor = latest_payday + timedelta(days=cadence_days)
    earliest_new = min(new_paydays)
    if earliest_new < floor:
        raise ValidationError(
            f"A new payday must fall on or after {floor.isoformat()} -- one "
            f"full pay cycle ({cadence_days} days) after your latest recorded "
            f"payday ({latest_payday.isoformat()}); got "
            f"{earliest_new.isoformat()}.  An earlier date lands inside a "
            f"paycheck you already have and would split it in half, which this "
            f"app cannot yet do safely.  Choose a later date, or rebuild the "
            f"tail from the payday you want."
        )


def _write_derivation(
    user_id: int,
    derived: "tuple[DerivedPeriod, ...]",
    existing_by_payday: "dict[date, PayPeriod]",
    projected_before: "date | None" = None,
) -> "list[PayPeriod]":
    """Write the derivation onto every row, creating the ones that are missing.

    **The whole calendar, every time**, which is the ruling this step turns on
    (developer, 2026-08-10).  The two stored columns are a cache of one
    function, and a cache refreshed only next to the batch is a second source of
    truth: an interior hole -- a period whose stored end falls short of the next
    payday -- would then never be repaired by any forward append, so plan step
    C4's ``DROP COLUMN`` would silently move that owner's figures.

    **It is also the cheaper implementation, not merely the correct one.**
    Assigning a value identical to an instance's committed state emits no
    statement at all: SQLAlchemy compares each attribute before building the
    ``UPDATE`` (measured on the pinned 2.0.49 -- 0 statements for the
    identical-reassign shape, 1 for a control that changes a value).  On a
    healthy schedule every comparison matches, so the pass costs one derivation
    and no SQL.  Production is such a schedule: 61 paydays, 0 index mismatches,
    0 end mismatches, re-verified 2026-08-10.

    **A move is logged at WARNING and that is deliberate.**  Every row it
    rewrites is a row whose stored coverage disagreed with the owner's own
    paydays, which is a state the model says cannot exist -- so each event names
    a schedule that had been quietly wrong, including under settled money.  A
    silent repair would leave no trace that the shape of a settled paycheck
    changed.

    **It repairs a HOLE and REFUSES an OVERLAP, and that asymmetry is the whole
    disposition of a disagreeing row** (found by an adversarial review of this
    step, which asked what "the derived value is correct" means on a period
    holding settled money).  A non-last period's derived end is its successor's
    payday minus a day.  A stored end BELOW that is a hole: days the owner's own
    paydays cover and the column does not, so writing the derivation LENGTHENS
    the period and can only pull a row's money back into a column it belongs
    in.  A stored end ABOVE it is an OVERLAP -- two periods covering one day --
    which no writer in this app's history could produce and which
    ``uq_pay_periods_user_start`` plus the forward-only rule keep out.  Reaching
    it means the rows were edited outside this module, and "repairing" it would
    SHORTEN a period, possibly a settled one, on the strength of an assumption
    about which of two contradictory values is right.  There is no safe silent
    answer to that, so it fails loud.  Production carries neither state (61
    paydays, 0 disagreements of either sign, measured 2026-08-10).

    **No existing row's ORDINAL moves, and that follows from the forward-only
    floor rather than from luck.**  A payday can only be appended after every
    existing one, and a delete takes a payday-ordered suffix, so a surviving row
    keeps its position in payday order.  That is what keeps
    ``uq_pay_periods_user_index`` from seeing a transient collision as the
    per-row ``UPDATE``\\ s go out.  Plan step C6 is the step that lifts the
    floor, and it is sequenced after C4, which drops the column and the
    constraint with it.

    Args:
        user_id: The owning user's id -- stamped on the rows this creates.
        derived: The calendar to write, ``start_date`` ascending.
        existing_by_payday: The owner's current rows keyed by payday.  A derived
            period whose payday is absent is created.
        projected_before: The payday that was LAST before this write, whose
            stored end was therefore a cadence PROJECTION rather than a fact.
            It is exempt from the overlap refusal: appending after it, or
            shortening the cadence it projected at, legitimately pulls that one
            end back, and refusing it would refuse the ordinary append.  Every
            other row's end was dictated by its successor's payday, so a stored
            value above the derivation there is a genuine contradiction.

    Returns:
        The newly created rows, flushed so their ids are assigned, in the
        derivation's order.
    """
    created = []
    for period in derived:
        row = existing_by_payday.get(period.start_date)
        if row is None:
            row = PayPeriod(
                user_id=user_id,
                start_date=period.start_date,
                end_date=period.end_date,
                period_index=period.period_index,
            )
            db.session.add(row)
            created.append(row)
            continue
        if (
            row.end_date == period.end_date
            and row.period_index == period.period_index
        ):
            continue
        if (
            not period.end_is_projected
            and period.start_date != projected_before
            and row.end_date > period.end_date
        ):
            raise PayPeriodOverlapStored(
                row.id, period.start_date, row.end_date, period.end_date,
            )
        log_event(
            logger, logging.WARNING, EVT_PAY_PERIODS_REMATERIALISED, BUSINESS,
            "A stored pay period disagreed with the owner's paydays and was "
            "rewritten to match them",
            user_id=user_id,
            pay_period_id=row.id,
            payday=period.start_date.isoformat(),
            stored_end=row.end_date.isoformat(),
            derived_end=period.end_date.isoformat(),
            stored_index=row.period_index,
            derived_index=period.period_index,
        )
        row.end_date = period.end_date
        row.period_index = period.period_index
    db.session.flush()
    return created
