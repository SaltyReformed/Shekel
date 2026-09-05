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
that a schedule should change from *changing* it.  The rule below has one home,
so plan steps C6 (a payday inserted mid-schedule) and C7 each inherit it by
reading one file.

*No pylint checker enforces the boundary, deliberately.* Finding
``balance:N-147`` already records that two custom checkers police their rule
with a hand-maintained list of module names, and a third would widen that
finding rather than close it.  The boundary is held by there being exactly one
``PayPeriod(...)`` construction and exactly one ``DELETE`` in ``app/`` -- both
in this module -- which one ``grep`` answers and
``TestThereIsOneWriter`` asserts.  (``tests/`` builds and deletes them freely,
as it must: several suites exist to hand this writer a state no door can
produce.)

The rule, and it is the whole point
===================================

**A row here is ONE FACT -- the payday -- so this module writes one column and
computes nothing** (plan step C4-c).  A period's ordinal is its position in the
owner's payday order and its last covered day is the day before the next
payday; both are answered by ``pay_calendar.derive_periods`` on every read, and
neither is stored.  So, of ``budget.pay_periods``:

    recording a payday INSERTS one row and touches no other; retiring one
    DELETES it and touches no other.  This module issues no ``UPDATE`` against
    that table at all, which ``TestAWriteTouchesNoRowItDidNotName`` grades as a
    statement census rather than as a claim.

(The cadence rule below writes ``budget.pay_schedule``, which is a different
table and a different fact.)

Until C4-c the table carried both derived values as columns, and this module
had to hold them equal to the derivation -- re-materialising the owner's WHOLE
calendar on every write, logging a repair at WARNING where a stored value had
drifted, and refusing outright where the drift ran the other way
(``PayPeriodOverlapStored``).  All of that was the cost of the second source of
truth, and it went with the columns: there is nothing left for a write to
invalidate, which is also what leaves C6's mid-schedule insert nothing to
repair.  **The DROP was provably free**: on production, 63 paydays with 0 index
mismatches, 0 end mismatches, 0 gaps and 0 overlaps against the derivation
(measured 2026-09-01).

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

**Ledger row P28 -- "the horizon the app projects" disagreeing with "the end
stored on the last row" -- has no subject at all since C4-c**: there is one
value, read from ``budget.pay_schedule`` by the derivation, and no column left
for it to come apart from.  The extend door has no
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
from datetime import date, datetime

from app.exceptions import ValidationError
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.services import pay_calendar, pay_schedule_service
from app.utils.log_events import (
    BUSINESS,
    EVT_PAY_PERIODS_GENERATED,
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
#: Read by :func:`reject_out_of_range_batch_size` and by the Marshmallow fields
#: in ``app.schemas.validation.pay_periods``, which import them from here: the
#: bound belongs to the writer, and a schema that stated its own copy is how
#: four form fields came to hold four literals.
PERIOD_BATCH_MIN = 1
PERIOD_BATCH_MAX = 260


def reject_out_of_range_batch_size(num_periods: int) -> None:
    """Refuse a batch size outside what one call may create.

    **The writer's OWN precondition, stated once and asked before anything is
    written** (plan step X-ad-a).  It was held by each caller's Marshmallow
    field until registration became a fifth caller, and a bound held by
    remembering is a bound the next door does without: ``num_periods`` was
    refused by no service at all, so a non-form caller could ask for zero
    periods (which then failed several statements later, in ``create_account``,
    under a message about accounts) or for a hundred thousand (383 years of
    fortnights in one transaction).

    **It used to carry a cadence FLOOR beside this, and plan step C4-c deleted
    it with its subject.**  That floor refused a cadence of 1 because a STORED
    ``end_date`` cannot express a one-day period -- ``ck_pay_periods_date_order``
    required ``start_date < end_date`` and the writer stored
    ``start_date + (cadence_days - 1)`` -- so the INSERT died as an unhandled
    ``CheckViolation`` 500 on both the settings form and the registration form.
    Nothing is stored now, two paydays a day apart simply define a one-day
    period, and pay-calendar findings **P9** and **P33** close with the column.

    The CADENCE bound that remains is ``budget.pay_schedule``'s CHECK, asked by
    that column's own writer,
    :func:`~app.services.pay_schedule_service.reject_out_of_range_cadence`.
    Two bounds, two owners, because they answer different questions -- what may
    be STORED as a schedule, and how much of one this writer will materialise
    in a single call.

    Args:
        num_periods: How many periods the batch would create.

    Raises:
        ValidationError: *num_periods* is outside
            :data:`PERIOD_BATCH_MIN` .. :data:`PERIOD_BATCH_MAX`.  The message
            names the offending value and both bounds.
    """
    if not PERIOD_BATCH_MIN <= num_periods <= PERIOD_BATCH_MAX:
        raise ValidationError(
            f"Number of pay periods must be between {PERIOD_BATCH_MIN} and "
            f"{PERIOD_BATCH_MAX}; got {num_periods}."
        )


def record_paydays(
    user_id: int,
    first_payday: date,
    num_periods: int,
    rhythm: pay_schedule_service.Rhythm,
    retiring_ids: "set[int] | None" = None,
) -> "list[PayPeriod]":
    """Record a batch of paydays.

    **The one door that adds to ``budget.pay_periods``.**  It records paydays
    (``first_payday``, then every ``cadence_days`` after it, ``num_periods``
    times) and persists the cadence when the batch actually recorded something.
    A payday already on the table is skipped rather than duplicated, so
    re-running with the same start and a larger count legitimately extends the
    schedule.

    **``retiring_ids`` is what makes regenerate and reset ONE operation**, and
    an adversarial review of plan step C3-b is why it exists.  Those two doors
    replace a span: they drop periods and record others, and applying the halves
    through two separate calls judged each refusal against an interval that
    existed for one statement -- the schedule minus its tail, before the rebuild
    that is the whole point of the door.  Handing both halves to one call means
    every refusal sees the payday set the operation actually leaves behind.
    *Its second reason went with the derived columns at plan step C4-c: two
    calls also re-materialised the whole calendar twice per rebuild and logged
    the intermediate shape as a repair.  A write touches one row now, so only
    the refusals argue for the composition -- and they still do.*

    **It takes IDS, not rows, since plan step C2-f3b.**  It was a
    ``list[PayPeriod]`` that this function read one thing off -- ``.id`` -- so
    every caller had to hold ORM rows for a set of integers, which is what kept
    ``pay_period_admin`` querying ``budget.pay_periods`` for values it decides
    nothing with.  That module now decides in
    :class:`~app.services.pay_calendar.DerivedPeriod` values and the table read
    lives HERE, in the one module that writes it (:func:`_owner_paydays`),
    which is where a read whose only purpose is to feed a write belongs.  It
    also makes the OWNER scoping structural rather than a property of the
    callers: an id naming another owner's period is not in
    :func:`_owner_paydays`' answer, so it retires nothing and is not counted.

    It replaced ``pay_period_service.generate_pay_periods`` at plan step C3-b,
    and the difference is what the step was about: that function AUTHORED
    ``end_date = start_date + cadence_days - 1`` and
    ``period_index = max_index + 1`` on the new rows and left every existing
    row alone, which is how a schedule came to hold days no paycheck covered.
    C3-b held those columns equal to the derivation on every write; plan step
    C4-c dropped them, so this one records the payday and there is nothing
    else to get right.

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
        rhythm: How often this owner is paid and what payroll does when a
            payday lands on a closed day
            (:class:`~app.services.pay_schedule_service.Rhythm`).  The batch's
            paydays are spaced by its cadence, and the whole pair is persisted
            in one statement when the batch records at least one new payday;
            ignored otherwise.  It arrives as a PAIR rather than two arguments
            because the two carry a joint rule -- plan step **C14-b**, rulings
            **R-PC54** and **R-PC56**.  Four of this door's five callers are
            forms that state a rhythm; the fifth continues the stored one
            (``pay_schedule_service.resolve_shift``).
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
        ValidationError: *first_payday* is not a plain ``date``; *num_periods*
            is outside :data:`PERIOD_BATCH_MIN` .. :data:`PERIOD_BATCH_MAX`
            (:func:`reject_out_of_range_batch_size`); *rhythm*'s cadence
            falls outside ``ck_pay_schedule_cadence_range``
            (:func:`~app.services.pay_schedule_service.reject_out_of_range_cadence`);
            *rhythm* pairs a displacing convention with a cadence too short
            to carry it
            (:func:`~app.services.pay_schedule_service.reject_shift_on_short_cadence`);
            or the batch's earliest new payday falls before the forward-only
            floor (:func:`_reject_backward_payday`).  **Every one of them is
            asked before a statement is issued**, which is what lets
            :func:`_apply` promise that a refused batch deletes nothing and
            leaves the stored rhythm alone.
    """
    # The door's four preconditions, together and ahead of every statement --
    # including ahead of the arithmetic below, which turns *cadence_days* into
    # dates.  The cadence bound and the cadence-convention pairing are asked
    # through the column's own owner rather than restated here: two copies of a
    # rule are two chances for the schema tier, the service tier and the
    # database to disagree.
    _reject_undatable_payday(first_payday)
    reject_out_of_range_batch_size(num_periods)
    pay_schedule_service.reject_out_of_range_cadence(rhythm.cadence_days)
    pay_schedule_service.reject_shift_on_short_cadence(rhythm)

    current = _owner_paydays(user_id)
    doomed = retiring_ids or frozenset()
    retiring = [period_id for period_id, _payday in current if period_id in doomed]
    surviving_paydays = {
        payday for period_id, payday in current if period_id not in doomed
    }

    new_paydays = [
        payday
        for payday in _requested_paydays(
            first_payday, num_periods, rhythm.cadence_days,
        )
        if payday not in surviving_paydays
    ]
    # The floor reads the cadence the owner's LAST SURVIVING PAYCHECK currently
    # runs at, which is the one stored BEFORE this batch -- the question it asks
    # is how far that paycheck already reaches, not how far the next one will.
    # An owner moving from fortnightly to weekly is therefore bounded at a
    # fortnight and then continues at a week, which is what "correct my cadence
    # going forward" means and what it cannot mean retroactively.
    _reject_backward_payday(
        surviving_paydays, new_paydays,
        pay_schedule_service.resolve_cadence(user_id),
    )

    created = _apply(
        _PaydayChange(
            user_id=user_id,
            retiring=retiring,
            recording=new_paydays,
            rhythm=rhythm,
        ),
    )
    log_event(
        logger, logging.INFO, EVT_PAY_PERIODS_GENERATED, BUSINESS,
        "Pay periods generated",
        user_id=user_id,
        count=len(created),
        retired=len(retiring),
        start_date=first_payday.isoformat(),
        cadence_days=rhythm.cadence_days,
        shift=rhythm.shift.value,
    )
    return created


def retire_paydays(user_id: int, doomed_ids: "set[int]") -> int:
    """Delete the pay periods *doomed_ids* names.

    **The one door that removes from ``budget.pay_periods``.**  Truncate,
    regenerate's rebuild step and reset's whole-schedule wipe all reach the
    table here; the LOCK and DISCARD gates that decide WHICH periods may go
    stay with ``pay_period_admin``, because deciding is a different concern
    from doing (``pay_period_locks``' own split, one level up).

    **The survivors are untouched, and since plan step C4-c that is a property
    of the SCHEMA rather than of this function.**  While the two derived
    columns were stored a delete moved values on rows it did not name: paydays
    ``[Jan 2, Jan 16, Feb 11]`` truncated through Jan 16 left the January
    paycheck a stored end of Feb 10 where the derivation said Jan 29, because
    its successor was gone and its end fell back to the cadence projection.  So
    this function re-materialised what survived, and an on-cadence fixture
    could not see the bug it was fixing (``lead(start) - 1`` and
    ``start + cadence - 1`` coincide there).  Both ends are derived on every
    read now; a delete removes rows and changes no value anywhere.

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

    **It takes IDS and reads the rows itself, since plan step C2-f3b**, for the
    reason :func:`record_paydays` gives at length: a read whose only purpose is
    to feed a write belongs in the module that writes, and the caller that used
    to supply the rows -- ``pay_period_admin`` -- now decides in
    :class:`~app.services.pay_calendar.DerivedPeriod` values and holds none.
    Because the delete set is ``current`` less ``keep``, an id from another
    owner (or a stale one) retires nothing rather than being deleted or counted.

    **What the re-read does and does NOT guarantee**, corrected by an
    adversarial review of plan step C2-f3b.  Under every door that takes
    ``user_write_lock.lock_user_writes`` it cannot see FEWER rows than the gate
    did, which is the direction that matters: no period the caller refused to
    delete can be missing here.  It is not the SAME set, and a first draft said
    it was: ``POST /pay-periods/generate`` and ``auth_service.register_user``
    both reach :func:`record_paydays` without taking that lock (finding
    **P71**), so a concurrent generate can commit a payday between the gate's
    read and this one and this read sees a SUPERSET.  A row this read gained is
    simply one it does not name, so it survives -- where the caller-supplied
    snapshot it replaced left it in neither ``current`` nor ``keep`` and gave
    the newly-last survivor a cadence-projected end that could run past it.

    Args:
        user_id: The owning user's id.
        doomed_ids: The ``budget.pay_periods.id`` values to delete.  Empty is a
            legal, idempotent no-op, and so is a set naming nothing of this
            owner's.

    Returns:
        The number of pay periods actually deleted -- the size of the
        intersection of *doomed_ids* with this owner's periods, never the size
        of the argument.
    """
    retiring = [
        period_id for period_id, _payday in _owner_paydays(user_id)
        if period_id in doomed_ids
    ]
    if not retiring:
        return 0
    _apply(
        _PaydayChange(
            user_id=user_id,
            retiring=retiring,
            recording=[],
            rhythm=None,
        ),
    )
    return len(retiring)


@dataclass(frozen=True)
class _PaydayChange:
    """One change to an owner's payday set, applied as ONE operation.

    **Every door that writes composes into this, and an adversarial review of
    plan step C3-b is why it exists.**  ``retire_paydays`` and
    ``record_paydays`` used to apply their halves separately, so regenerate --
    which retires a tail and records a new one -- judged its refusals against an
    interval that existed for one statement and was then widened again by the
    rebuild that is the whole point of the door.

    A claim about the final state has to be evaluated against the final state,
    so the two halves arrive together and every refusal is asked of the payday
    set the operation actually leaves behind.  *The rule whose false refusals
    measured this is gone (the coverage rule, deleted 2026-08-11), and its
    second reason went at plan step C4-c: while the derived columns were stored,
    two calls also re-materialised the calendar twice per rebuild and the first
    pass logged a phantom repair the second undid.  What is left is the
    refusals, and they are enough.*

    Attributes:
        user_id: The owning user.
        retiring: The ``budget.pay_periods.id`` values to DELETE.  Already
            intersected with what the owner actually holds by the caller's
            :func:`_owner_paydays` read, so the OWNER scoping is structural
            rather than a property of the two callers.
        recording: The paydays to create, already filtered of any that exist.
        rhythm: The cadence and payday convention to persist, iff *recording*
            is non-empty.  ``None`` when nothing is recorded, where the stored
            pair stands.  One value rather than two nullable columns because
            the two carry a joint rule and a row written through two
            statements passes through a state neither statement means (see
            :func:`~app.services.pay_schedule_service.upsert_schedule`).
    """

    user_id: int
    retiring: "list[int]"
    recording: "list[date]"
    rhythm: "pay_schedule_service.Rhythm | None"


def _apply(change: _PaydayChange) -> "list[PayPeriod]":
    """Carry out one payday change: delete, persist the cadence, insert.

    **Every refusal a route RENDERS has already happened**, in
    :func:`record_paydays`, which is what lets truncate keep promising it
    deletes nothing on a refusal and what makes the module docstring's "a
    refusal leaves nothing behind" true of this module rather than of its
    callers.  Nothing here refuses anything: the cadence bound is asked at the
    door, ahead of the arithmetic that turns it into dates, and
    ``upsert_schedule`` re-asks it as the column's own writer.  The order that
    remains is forced only by the unique key:

    1. DELETE what is retired -- one bulk statement, scoped by OWNER as well as
       by id, so the scoping is structural rather than a property of the two
       callers that happen to pass owner-scoped lists.  It runs FIRST so a
       payday being retired and re-recorded in the same operation -- which is
       what regenerate and reset do -- cannot collide on
       ``uq_pay_periods_user_start``.
    2. Persist the rhythm -- the cadence and the payday convention, in one
       statement (the rule: only a batch that RECORDS a payday).
    3. INSERT one row per recorded payday.

    ``expire_all`` runs LAST, and only when something was deleted: the bulk
    ``DELETE`` runs with ``synchronize_session=False``, so any row the wider
    request already loaded would otherwise stay in the identity map naming a
    row that is gone.

    Args:
        change: The whole change (:class:`_PaydayChange`).

    Returns:
        The newly created rows, flushed, ``start_date`` ascending.

    Raises:
        ValidationError: ``upsert_schedule`` refuses the cadence.  Unreachable
            from :func:`record_paydays`, which asks the same bound through the
            same function before any statement is issued; kept because
            ``upsert_schedule`` is the column's one writer and owns the refusal.
    """
    if change.retiring:
        db.session.query(PayPeriod).filter(
            PayPeriod.user_id == change.user_id,
            PayPeriod.id.in_(change.retiring),
        ).delete(synchronize_session=False)
    if change.recording:
        pay_schedule_service.upsert_schedule(change.user_id, change.rhythm)
    created = _create_periods(change.user_id, change.recording)
    if change.retiring:
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
    (it retires everything and records a fresh batch), became an unhandled 500.
    *That particular owner is unstorable since plan
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
    return {period_id for period_id, _payday in _owner_paydays(user_id)}


def _owner_paydays(user_id: int) -> "list[tuple[int, date]]":
    """Return every one of *user_id*'s pay periods as ``(id, payday)``, ascending.

    Two columns rather than the ORM row, because two columns are the whole of
    what a write needs to know about what is already there: which rows a delete
    set actually names, and which paydays a batch would duplicate.  Hydrating a
    row to read an id and a date would put every other column of the table in
    the session for no reader -- the shape ``pay_calendar._loader`` already
    takes for the same reason.

    Ordered by ``start_date``, and that is the normalization rather than a
    preference: the payday is the fact, and until plan step C4-c the ordinal was
    a stored column this module had to recompute, so reading in ordinal order
    would have sorted by the answer.  There is no ordinal to sort by now.

    Args:
        user_id: The owning user's id.

    Returns:
        ``(budget.pay_periods.id, start_date)`` per period, payday ascending.
        Empty for an owner who has never generated a schedule.
    """
    return [
        (row.id, row.start_date)
        for row in db.session.query(PayPeriod.id, PayPeriod.start_date)
        .filter(PayPeriod.user_id == user_id)
        .order_by(PayPeriod.start_date)
        .all()
    ]


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

    **It asks the GRID producer rather than restating its arithmetic** (plan
    step C14-d).  ``first_payday + timedelta(days=cadence_days * step)`` was
    the fourth spelling of the payday rhythm, in the module whose OTHER
    spelling this step deleted, and an adversarial review of ``C14-d`` found it
    missing from the census that step corrected.  The body is
    :func:`~app.services.pay_calendar.nominal_payday` verbatim, so routing it
    moves no date and makes the census true by construction rather than by
    prose.

    **It stays NOMINAL, and that is not this function's decision to revisit.**
    Whether the writer should RECORD each of these days displaced onto a
    business day is ``C14-e``'s question and it moves money -- ledger row
    **PC-497**.  Asking the grid rather than the projection leaves that question
    exactly where it was: the grid producer is the one C14-e does not change.

    Args:
        first_payday: The batch's first payday.
        num_periods: How many paydays the batch covers.
        cadence_days: Days between them.

    Returns:
        *num_periods* days, ascending, ``cadence_days`` apart.
    """
    return [
        pay_calendar.nominal_payday(first_payday, cadence_days, step)
        for step in range(num_periods)
    ]


def _reject_backward_payday(
    surviving_paydays: "set[date]",
    new_paydays: "list[date]",
    cadence_days: "int | None",
) -> None:
    """Refuse a batch whose earliest new payday would land inside a paycheck.

    **The forward-only rule, keyed on the PAYDAY** (ruling **R-PC1** as split
    2026-08-10).  It replaces ``pay_period_service._reject_overlapping_batch``,
    which bounded a batch on ``max(end_date)`` -- a derived column plan step
    C4-c dropped, and one that made the guard do a second job nothing credited
    it with.

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

    **The floor is WHERE THE LAST PAYCHECK ENDS, and since plan step C14-d it
    asks the derivation rather than restating it.**  It is
    ``projected_payday(latest_payday, cadence_days, 1)`` -- the same call
    :func:`~app.services.pay_calendar.derive_periods` makes to close the last
    saved period, whose ``end_date`` is that day minus one.  So the first day
    NOT inside a paycheck the owner already has is the floor by construction,
    and the two cannot come apart.

    *That was a maintained agreement until C14-d, and the docstring said so:
    "on any schedule this app can write those two spellings select the same
    set, because the last period's end IS ``payday + cadence - 1``".  A rule
    that two places must always agree is rule 14's tell -- one value with two
    homes -- and the remedy is to delete a home rather than keep them in step.
    The home deleted here is this function's own ``latest_payday +
    cadence_days``, one of the five spellings*
    :func:`~app.services.pay_calendar.projected_payday` *censuses -- and this
    module held TWO of them:* :func:`_requested_paydays` *routes to the grid
    producer in the same step, so two of the five go and the census names
    three.*

    **Why it moves ``$0.00``, and the reason is STRUCTURAL rather than a fact
    about stored data** -- an adversarial review of this step corrected a first
    draft that rested it on every row holding ``none``, which any owner can
    falsify in one POST through the four doors ``C14-b`` shipped.  The real
    reason is that :func:`~app.services.pay_calendar.projected_payday` takes no
    convention and nothing in the pay-calendar package reads one until
    ``C14-e``, so it IS the grid today and no stored value can move a date
    through it.

    **What the change buys is measured, not asserted** (probe over production's
    own schedule, 1,951 paydays from 2026-03-26 at cadence 14 out to
    ``CALENDAR_DATE_MAX``, 2026-09-05).  Both spellings refuse the same **0**
    paydays with the convention at ``none``.  Under ``prior`` the open-coded
    floor refuses **58** of the owner's own future paydays -- the R-PC47 case
    exactly: a payday nominally
    2026-01-01 is really paid 2025-12-31, and ``latest + cadence`` puts the
    floor on the nominal day and refuses the real one -- and the producer call
    refuses **0**.

    **What ``C14-e`` must not get wrong here** (adversarial review of this
    step).  When the producer starts taking a convention, this floor must read
    the STORED one and not :attr:`rhythm.shift
    <app.services.pay_schedule_service.Rhythm.shift>`.  The rhythm is what the
    operation LEAVES BEHIND, and a batch that changes the convention would
    otherwise compute its floor under the new one while
    :func:`~app.services.pay_calendar.derive_periods` still closes the existing
    calendar under the old -- which is the disagreement between fence and
    boundary this step exists to end, reintroduced through the argument list.

    **Under ``next`` it still refuses those 58, and that is ledger row N-495
    rather than a half-fix.**  Those refusals are the ones whose ANCHOR was
    itself displaced: ``projected_payday`` steps from the last RECORDED payday,
    so a payday payroll moved forward carries its whole projection forward with
    it, and the floor inherits exactly the error the derived end has.  That is
    the point of asking the producer -- the fence can no longer be wrong in a
    way the calendar is not -- and the one home left to repair is the anchor,
    which **N-495** owns and ``C14-e`` may not fix without widening ``C14-c``'s
    probe window.

    **Why it is not two days, and an adversarial review of C3-b is why.**  That
    step's first cut bounded at ``latest_payday +
    MIN_MATERIALISABLE_CADENCE_DAYS``, on the reasoning that the only insert
    worth refusing is one before an existing payday.  That is wrong by the
    length of a paycheck, and P10's BOTH damage arms are then reachable through
    a door P10 says is closed.  Measured on the two-period fortnightly
    schedule: recording 2026-01-23 shrank the 2026-01-16 paycheck from 01-29 to
    01-22 and moved a row due 01-25 from rendering on 01-25 to rendering on
    01-22, while ``/pay-periods/generate`` left the split-off half EMPTY and
    ``regenerate`` repopulated it beside the row the shrunk half kept -- one
    monthly billed twice in what had been one paycheck.

    Args:
        surviving_paydays: The paydays the owner keeps once this operation's
            retirements are applied, empty for a first-time schedule.
        new_paydays: The paydays this batch would create -- already filtered of
            any that exist, so a re-run naming existing days is bounded on what
            it would actually add.
        cadence_days: The owner's stored cadence, which sets how far the last
            paycheck reaches.  ``None`` only beside an empty payday set, where
            there is no floor to apply -- the early return below is what makes
            that safe, and it has to be, because the producer takes an ``int``.

    Raises:
        ValidationError: The earliest new payday falls before the floor.
    """
    if not surviving_paydays or not new_paydays:
        return
    latest_payday = max(surviving_paydays)
    floor = pay_calendar.projected_payday(latest_payday, cadence_days, 1)
    earliest_new = min(new_paydays)
    if earliest_new < floor:
        raise ValidationError(
            f"A new payday must fall on or after {floor.isoformat()} -- the "
            f"day the next paycheck opens after your latest recorded payday "
            f"({latest_payday.isoformat()}, at a {cadence_days}-day cycle); "
            f"got {earliest_new.isoformat()}.  An earlier date lands inside a "
            f"paycheck you already have and would split it in half, which this "
            f"app cannot yet do safely.  Choose a later date, or rebuild the "
            f"tail from the payday you want."
        )


def _create_periods(
    user_id: int, paydays: "list[date]",
) -> "list[PayPeriod]":
    """Insert one pay period per payday.

    **The whole of what a write to this table does since plan step C4-c**, and
    the shrinkage is the normalization rather than a tidy-up.  While
    ``end_date`` and ``period_index`` were stored, this function was
    ``_write_derivation``: it re-materialised the owner's ENTIRE calendar on
    every write, because two derived values cached beside the fact they derive
    from are a second source of truth and a cache refreshed only next to the
    batch leaves an interior hole no forward append ever repairs.  It logged a
    rewrite at WARNING where a stored end had fallen short of the next payday,
    and refused outright (``PayPeriodOverlapStored``) where it ran past it,
    because shortening a period that may hold settled money is not a decision
    code may take silently.  Every one of those behaviours existed to hold a
    cache honest.  There is no cache.

    Args:
        user_id: The owning user's id -- stamped on the rows this creates.
        paydays: The paydays to record, ``start_date`` ascending and already
            filtered of any the owner holds.  A repeat would be refused by
            ``uq_pay_periods_user_start``, which is the key that makes "one
            period per owner per opening day" a property of the table rather
            than of this function.

    Returns:
        The newly created rows, flushed so their ids are assigned, in the order
        given.
    """
    created = [
        PayPeriod(user_id=user_id, start_date=payday) for payday in paydays
    ]
    db.session.add_all(created)
    db.session.flush()
    return created
