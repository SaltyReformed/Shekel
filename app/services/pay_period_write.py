"""
Shekel Budget App -- Pay Period Writer

**The ONE place in ``app/`` that changes ``budget.pay_periods``** (plan step
C3-b, developer ruling 2026-08-10).  Every door that grows, rebuilds or
shortens an owner's schedule -- generate, extend, the rolling top-up,
regenerate, reset, truncate -- reaches the table through
:func:`record_paydays`, :func:`continue_paydays` or :func:`retire_paydays`
and through nothing else.  The first STATES a batch (a first payday and a
rhythm); the second CONTINUES the owner's own plan (plan step
``pay_calendar:C17-c-2b``); the third removes.
``pay_period_service`` keeps only its readers; ``pay_period_admin`` keeps only
its four doors, and the gates they consult live in ``pay_period_gates`` since
plan step ``pay_calendar:C14-f``.

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

The batch's shape is one module over
====================================

What a batch of paydays IS -- its size bound, its first day's type, the days
it lands on, the floor it may not start under and the ceiling it may not
start at -- lives in :mod:`app.services.pay_period_batch` since plan step
``pay_calendar:C17-c-1`` (ruling **R-PC74**, closing ledger row **PC-507**),
with the record of ruling **R-PC1**'s two refusals: the structural floor that
survives and the coverage rule that was DELETED.  The ceiling joined the
floor at plan step ``C17-c-2a`` (rulings **R-PC67**, **R-PC76**): a batch
may not skip a whole paycheck of the owner's plan, through any door, so a
hole in the schedule is unrepresentable rather than confirmed.
:func:`record_paydays` asks every one of them before it issues a statement,
so a refusal leaves nothing behind.

The cadence rule, which became the ERA rule
============================================

A rhythm is an ERA's fact since plan step ``pay_calendar:C17-a`` (ruling
**R-PC58**): ``budget.pay_eras`` holds one row per *how I have been paid
since*, and ``budget.pay_schedule`` carries no cadence.  **A batch that
CREATED at least one payday MINTS an era at its first payday when it states a
rhythm the era covering that day does not hold, and a batch that created none,
or that continues the covering era on its own grid, writes no era**
(``pay_era_write.era_to_mint``).  The first half is the cadence rule
as ruled 2026-08-10 (closing findings **P12** and **P29**), whose rejected
trigger -- "at least two paydays, or the owner's first" -- silently discarded
a REQUIRED form input.  The second half is the era relation's: a CONTINUING
batch writes nothing, so the rolling top-up no longer re-judges a stored pair
on every ``/grid`` render (ledger row **N-494**, closed), and a cadence
changed going forward no longer re-describes every past payday (**N-492**).

A recording batch retires every era whose FIRST PAYDAY falls after the last
payday it leaves standing -- none of them describes a payday that stands
(``pay_era_write.eras_describing``, in cash days since ``C17-b-2``; the
EARLIEST era stands whenever any payday does, since it runs backward below
the record) -- and a batch that leaves no payday standing retires every era.
A batch that records nothing retires none: truncating a tail leaves the
declared rhythm as it was.  **A CONTINUING batch states no rhythm and so
mints and retires nothing** (:func:`continue_paydays`, plan step
``C17-c-2b``): it records the paydays the owner's stored eras already plan,
so the eras are the plan's description before and after it.

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
from datetime import date
from itertools import islice

from app.exceptions import ValidationError
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.services import (
    pay_calendar,
    pay_era_write,
    pay_period_batch,
    pay_rhythm,
    pay_schedule_service,
)
from app.utils.log_events import (
    BUSINESS,
    EVT_PAY_PERIODS_GENERATED,
    log_event,
)

logger = logging.getLogger(__name__)


def record_paydays(
    user_id: int,
    first_payday: date,
    num_periods: int,
    rhythm: pay_rhythm.Rhythm,
    retiring_ids: "frozenset[int] | set[int]" = frozenset(),
) -> "list[PayPeriod]":
    """Record a batch of paydays.

    **The one door that adds to ``budget.pay_periods``.**  It records the days
    the owner's grid names -- ``first_payday``, then every ``cadence_days``
    after it, ``num_periods`` times -- each DISPLACED onto a business day under
    the stored convention since plan step ``pay_calendar:C14-e-3``
    (:func:`~app.services.pay_period_batch.requested_paydays`), and persists
    the rhythm when the batch actually recorded something.  A payday already
    on the table is skipped rather than duplicated, so re-running with the
    same start and a larger count legitimately extends the schedule.

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
    rule working -- the ERA rule since plan step ``C17-a``.  "Create the
    periods" and "record the rhythm they run at" used to be two calls a caller
    composed, which is how one door could do the first without the second
    (finding **P29**) and another could do the second without the first
    (finding **P12**).  With the rule inside, they are one operation and
    neither half has a door of its own: whether the batch MINTS an era is
    :func:`~app.services.pay_era_write.era_to_mint`'s answer, asked of
    the same read the floor uses.

    Args:
        user_id: The owning user's id.
        first_payday: The batch's first payday, read as a day on the owner's
            NOMINAL grid -- never a period boundary computed from one, and
            since ``C14-e-3`` never necessarily a day money moved either: it
            is what the batch is SPACED from and, when the batch mints an
            era, that era's ``effective_from`` and so its phase, while the
            row recorded for it is that day displaced.  Under ``none`` they
            are the same day.  *Every form that feeds this asked for the day
            the owner was PAID until plan step ``C17-c-2a`` (ledger row
            **PC-504**); the four doors ask for the SCHEDULED day now, in
            one shared sentence beside the convention control.*
        num_periods: How many paydays the batch covers, including any that
            already exist.
        rhythm: How often this owner is paid and what payroll does when a
            payday lands on a closed day
            (:class:`~app.services.pay_rhythm.Rhythm`).  The batch's
            paydays are spaced by its cadence, and the whole pair becomes an
            ERA in one statement when the batch records at least one new
            payday on a rhythm the covering era does not hold (the era rule,
            in the module docstring); ignored otherwise.  It arrives as a
            PAIR rather than two arguments because the two carry a joint rule
            -- plan step **C14-b**, rulings **R-PC54** and **R-PC56**.  Every
            caller of this door is a form that states a rhythm, since plan
            step ``C17-c-2b``: the continue path (extend, the rolling top-up)
            states none and goes through :func:`continue_paydays` instead.
        retiring_ids: ``budget.pay_periods.id`` values to DELETE in the same
            operation -- what makes regenerate and reset ONE operation (plan
            step C14-f).  The caller has already run the gates that decide
            they may go; this carries them out, so the refusals see the
            operation's final payday set.  An id that is not the owner's is
            inert.  Empty -- every door but regenerate and reset -- retires
            nothing.  *Until plan step ``C17-c-2a`` this travelled inside a
            ``SpanReplacement`` beside ``gap_confirmed``, the owner's answer
            to the hole a replacement could open; the hole is refused now
            (``pay_period_batch.reject_skipped_paycheck``), so nothing is
            left to group it with.*
    Returns:
        The newly created :class:`~app.models.pay_period.PayPeriod` objects,
        flushed so their ids are assigned, ``start_date`` ascending.  Empty when
        every requested payday was already on the table -- a no-op the caller
        can see, which is what lets the cadence stay untouched.

    Raises:
        ValidationError: *first_payday* is not a plain ``date``
            (:func:`~app.services.pay_period_batch.reject_undatable_payday`);
            *num_periods* is outside
            :data:`~app.services.pay_period_batch.PERIOD_BATCH_MIN` ..
            :data:`~app.services.pay_period_batch.PERIOD_BATCH_MAX`
            (:func:`~app.services.pay_period_batch.reject_out_of_range_batch_size`);
            *rhythm*'s cadence falls outside ``ck_pay_eras_cadence_range``
            (:func:`~app.services.pay_schedule_service.reject_out_of_range_cadence`);
            *rhythm* pairs a displacing convention with a cadence too short
            to carry it
            (:func:`~app.services.pay_schedule_service.reject_shift_on_short_cadence`)
            -- asked only of a batch that STATES an era, since plan step
            ``C17-a``; the batch's earliest new payday falls before the
            forward-only floor
            (:func:`~app.services.pay_period_batch.reject_backward_payday`);
            or it falls at or past the plan's SECOND projected payday after
            the owner's record, skipping a whole paycheck
            (:func:`~app.services.pay_period_batch.reject_skipped_paycheck`,
            rulings **R-PC67** and **R-PC76**).
            **Every one of them is asked before a statement is issued**, which
            is what lets :func:`_apply` promise that a refused batch deletes
            nothing and leaves the stored rhythm alone.
    """
    # The door's preconditions, ahead of every statement -- and the first
    # three ahead of the arithmetic below, which turns *cadence_days* into
    # dates.  The cadence bound is asked through the column's own owner rather
    # than restated here: two copies of a rule are two chances for the schema
    # tier, the service tier and the database to disagree.
    pay_period_batch.reject_undatable_payday(first_payday)
    pay_period_batch.reject_out_of_range_batch_size(num_periods)
    pay_schedule_service.reject_out_of_range_cadence(rhythm.cadence_days)

    current = _owner_paydays(user_id)
    retiring = [i for i, _payday in current if i in retiring_ids]
    surviving_paydays = {
        payday for i, payday in current if i not in retiring_ids
    }

    requested = pay_period_batch.requested_paydays(first_payday, num_periods, rhythm)
    new_paydays = [p for p in requested if p not in surviving_paydays]
    stored = pay_schedule_service.resolve_schedule(user_id)
    # The ERA rule (module docstring): a recording batch supersedes every era
    # past the last surviving payday and is judged against the ones that
    # stand -- keyed on the RECORD, not the mint's day, so a batch continuing
    # an earlier era still retires a later one it left with no payday.
    standing = pay_era_write.eras_describing(stored, surviving_paydays)
    era = None
    if new_paydays:
        era = pay_era_write.era_to_mint(standing, first_payday, rhythm)
    # The floor reads the ERAS stored BEFORE this batch -- the question it asks
    # is how far the owner's last surviving paycheck already reaches on the
    # grid that paid it, not how far the next one will.  An owner moving from
    # fortnightly to weekly is therefore bounded at a fortnight and then
    # continues at a week, which is what "correct my cadence going forward"
    # means and what it cannot mean retroactively.  **A MINTING batch is
    # bounded at the era's FIRST PAYDAY too** (``requested[0]``, a day the
    # record may hold): an era's identity is its first payday, and one below
    # the floor starts paying inside a paycheck the owner already has -- the
    # inverted seam ``validate_eras`` refuses (ruling 2026-09-11, C17-b-2).
    # **The STORED eras and not the batch's rhythm** (C14-e-1 discharging an
    # obligation C14-d wrote down): ``rhythm`` is what this operation LEAVES
    # BEHIND, and a batch that CHANGES the convention would compute its floor
    # under the new one while ``derive_periods`` still closes the existing
    # calendar under the old -- the disagreement between fence and boundary
    # C14-d exists to end, re-entering through the argument list.
    pay_period_batch.reject_backward_payday(
        surviving_paydays,
        new_paydays if era is None else [requested[0], *new_paydays],
        None if stored is None else stored.eras,
    )
    # The floor's MIRROR at the other end (rulings R-PC67, R-PC76): the
    # earliest new payday may not skip a whole paycheck of the owner's plan.
    # Asked of every batch from HERE, whichever door sent it, which is the
    # half ledger row P80 shows you cannot leave to the doors.  **It reads
    # the eras the batch leaves STANDING, where the floor reads the STORED
    # ones** (this step's adversarial review): the floor asks where the
    # paycheck the owner HAS ends, which is the calendar as it derives today;
    # the ceiling asks which planned paycheck would go MISSING from the
    # calendar the batch leaves behind, and an era this batch retires takes
    # its plan with it -- graded against the stored eras, a rebuild retiring
    # a 30-day era wrote a 35-day paycheck on a fortnightly plan.  Where the
    # batch retires no era the two sets are one and the window is one
    # sequence.
    pay_period_batch.reject_skipped_paycheck(
        surviving_paydays, new_paydays, standing,
    )

    # The pairing is judged only where a rhythm is STATED (plan step C17-a,
    # closing ledger row N-494): a continuing batch hands back its era's own
    # pair, and re-judging it was the top-up's read-path 500 from /grid.
    if era is not None:
        pay_schedule_service.reject_shift_on_short_cadence(era.rhythm)
    created = _apply(
        _PaydayChange(
            user_id=user_id,
            retiring=retiring,
            recording=new_paydays,
            era=era,
            eras_standing=tuple(e.effective_from for e in standing),
        ),
    )
    log_event(
        logger, logging.INFO, EVT_PAY_PERIODS_GENERATED, BUSINESS,
        "Pay periods generated",
        user_id=user_id,
        count=len(created),
        retired=len(retiring),
        # The day RECORDED and the grid day it came from, which stopped being
        # one value at ``C14-e-3``.  ``start_date`` named ``first_payday``
        # alone, so under a displacing convention the event named a day no row
        # holds; it is the first row actually created now, and the day an era
        # was minted from rides beside it -- ``None`` when the batch continued
        # the era it found.
        start_date=(
            created[0].start_date.isoformat() if created
            else first_payday.isoformat()
        ),
        era_minted_from=(
            None if era is None else era.effective_from.isoformat()
        ),
        cadence_days=rhythm.cadence_days,
        shift=rhythm.shift.value,
    )
    return created


def continue_paydays(user_id: int, num_periods: int) -> "list[PayPeriod]":
    """Record the next *num_periods* paydays the owner's own plan projects.

    **The CONTINUE door** (plan step ``pay_calendar:C17-c-2b``, rulings
    **R-PC75** and **R-PC78**; closing ledger rows **PC-509** and
    **N-494**).  Extend and the rolling top-up do not STATE a rhythm -- an
    owner who says "give me six more paychecks" has told this app nothing
    new about how they are paid -- so this door takes no first payday and no
    rhythm.  It reads the owner's stored eras and their record, and
    MATERIALISES the plan past the record:
    :func:`~app.services.pay_calendar.planned_paydays_after`, the one
    sequence the floor's day is the first of and the ceiling's the second.
    What it writes is therefore a PREFIX of the sequence both fences read,
    and neither is asked: the earliest day recorded IS the floor, so it
    cannot fall under it, and it falls before the second planned payday, so
    it cannot skip one.  A fence that cannot fire is not asked, which is the
    goal state ``CLAUDE.md``'s design doctrine names for every fence.

    **What it replaced, and why the replacement is the calendar's own
    walk.**  ``pay_period_admin.extend_pay_periods`` used to hand
    :func:`record_paydays` the LATEST era's grid day past the horizon
    (``nominal_payday_after``, deleted with this step) with that era's
    rhythm, as a STATED batch -- one that mints an era when its first day is
    off the covering era's grid.  For an owner whose latest era covers the
    record that is the plan exactly.  For an owner truncated BELOW a later
    era's first payday it is not: the calendar projects the days between
    the record and that payday on the era COVERING them (ruling **R-PC72**),
    while the door restated the latest era from the horizon -- eras 14 days
    from 2030-01-03 and 7 from 02-22 with the record cut to 01-17 showed
    01-31 on the grid and recorded 02-01, minting a third era there and
    retiring the one the owner had stated (ledger row **PC-509**); and once
    the latest era's cadence was LONGER than the kept one's, the restated
    batch skipped a paycheck of the kept rhythm and met
    ``pay_period_batch.reject_skipped_paycheck`` from ``/grid`` with no
    handler (ruling **R-PC76**'s transitional hazard, closed here).  This
    door records what the calendar shows, so the grid and the record are
    one value for every owner, and no era is judged on a continuation
    (**N-494**'s surviving path, closed).

    **The plan it reads is the seam rule's** (**R-PC75**): a later era's
    first payday replaces the old era's last planned payday at or before
    it, which is the schedule the regenerate that minted the later era
    wrote, so after a truncate this door re-records exactly those days --
    01-31, then 02-22 -- rather than a payday the regenerate had deleted.

    **Nothing here is guarded against a hole the plan itself holds**, and
    that is stated rather than absorbed: two nominal paydays a closed run
    displaces onto one cash day (a stored pairing below the collision
    floor, ledger row **N-493**) would yield one day twice and the insert
    would refuse it on ``uq_pay_periods_user_start``, exactly as the
    replaced door's batch did.  ``C17-e`` owns that state at the calendar.

    Args:
        user_id: The owning user's id.
        num_periods: How many paydays to record, bounded by
            :func:`~app.services.pay_period_batch.reject_out_of_range_batch_size`
            like every batch.

    Returns:
        The newly created :class:`~app.models.pay_period.PayPeriod` objects,
        flushed so their ids are assigned, ``start_date`` ascending -- always
        *num_periods* of them, since every planned payday falls after the
        record and none can already exist.

    Raises:
        ValidationError: *num_periods* is outside the batch bound; or the
            owner holds no payday, so there is no record to continue from
            ("generate your first schedule first" -- the extend card's own
            message, and the route flashes it).
        PayCalendarError: The owner holds no ``budget.pay_schedule`` row or
            no era (:func:`~app.services.pay_calendar.schedule_for`, asked
            BEFORE the record so a row-less owner meets the calendar's one
            refusal rather than this door's empty branch), or their eras
            reach no payday past the record within two cadences -- ledger
            row **N-493**'s reported hole.  Neither is the
            ``ValidationError`` the route catches: both reach
            ``app/error_handlers.py``'s recovery page for this exception,
            which is the right surface for "this owner has no derivable
            calendar" and the wrong one for "that date is not allowed".
    """
    pay_period_batch.reject_out_of_range_batch_size(num_periods)
    facts = pay_calendar.schedule_for(user_id)
    current = _owner_paydays(user_id)
    if not current:
        raise ValidationError(
            "Generate your first pay-period schedule before extending it."
        )
    latest = current[-1][1]
    recording = list(islice(
        pay_calendar.planned_paydays_after(facts.eras, latest), num_periods,
    ))
    # Through ``_apply`` like every other change, leaving EVERY era standing
    # and minting none: the eras described the plan before this batch and
    # describe the same plan after it.  That costs the schedule-row upsert
    # and a ``DELETE ... NOT IN (<every era>)`` that removes nothing, two
    # no-op statements per continuation -- the old door paid the same two.
    created = _apply(
        _PaydayChange(
            user_id=user_id,
            retiring=[],
            recording=recording,
            era=None,
            eras_standing=tuple(era.effective_from for era in facts.eras),
        ),
    )
    # The rhythm the event names is the one the first recorded payday was
    # planned on -- the era COVERING it, which for an owner truncated below
    # a later era is not the latest one.
    covering = facts.eras[pay_calendar.era_index_at(facts.eras, recording[0])]
    log_event(
        logger, logging.INFO, EVT_PAY_PERIODS_GENERATED, BUSINESS,
        "Pay periods generated",
        user_id=user_id,
        count=len(created),
        retired=0,
        start_date=created[0].start_date.isoformat(),
        era_minted_from=None,
        cadence_days=covering.rhythm.cadence_days,
        shift=covering.rhythm.shift.value,
    )
    return created


def retire_paydays(user_id: int, doomed_ids: "set[int]") -> int:
    """Delete the pay periods *doomed_ids* names.

    **The one door that removes from ``budget.pay_periods``.**  Truncate,
    regenerate's rebuild step and reset's whole-schedule wipe all reach the
    table here; the LOCK and DISCARD gates that decide WHICH periods may go
    live in ``pay_period_gates`` (split out of ``pay_period_admin`` at plan
    step ``pay_calendar:C14-f``), because deciding is a different concern
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
    it was: ``POST /pay-periods/generate`` and ``registration_service.register_user``
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
            era=None,
            eras_standing=(),
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
        era: The :class:`~app.services.pay_rhythm.Era` this batch MINTS, or
            ``None`` when it records nothing or continues the era covering
            its first payday on that era's own grid
            (:func:`~app.services.pay_era_write.era_to_mint`).  One
            value, because a row written through two statements passes
            through a state neither means.  Its ``effective_from`` is the
            batch's own ``first_payday``, a point on the grid the batch is
            written on -- always STATED by a door since plan step
            ``C17-c-2b``, where the continue path stopped computing one.
        eras_standing: The ``effective_from`` of every era the batch leaves
            standing -- those with a surviving payday, and the earliest
            whenever any payday survives
            (:func:`~app.services.pay_era_write.eras_describing`); every
            other era is retired before the mint, all of them for an empty
            tuple (``reset``'s shape).  Derived by :func:`record_paydays`
            from the payday sets it computed, so no door can claim a wipe it
            did not perform.
    """

    user_id: int
    retiring: "list[int]"
    recording: "list[date]"
    era: "pay_rhythm.Era | None"
    eras_standing: "tuple[date, ...]"


def _apply(change: _PaydayChange) -> "list[PayPeriod]":
    """Carry out one payday change: delete, mint the era, insert.

    **Every refusal a route RENDERS has already happened**, in
    :func:`record_paydays`, which is what lets truncate keep promising it
    deletes nothing on a refusal and what makes the module docstring's "a
    refusal leaves nothing behind" true of this module rather than of its
    callers.  Nothing here refuses anything: the bounds are asked at the door
    and ``mint_era`` re-asks them as the column's own writer.  The order that
    remains is forced only by the keys:

    1. DELETE what is retired -- one bulk statement, scoped by OWNER as well as
       by id, so the scoping is structural rather than a property of the two
       callers that happen to pass owner-scoped lists.  It runs FIRST so a
       payday being retired and re-recorded in the same operation -- which is
       what regenerate and reset do -- cannot collide on
       ``uq_pay_periods_user_start``.
    2. Make sure the owner's ``budget.pay_schedule`` row exists, when the
       batch records anything: both era and payday keys target it.
    3. RETIRE every era the batch does not leave standing (the era rule)
       BEFORE the mint, so ``uq_pay_eras_user_effective_from`` cannot
       collide on a day being restated; then MINT the era, when the batch
       states one.
    4. INSERT one row per recorded payday.

    ``expire_all`` runs LAST, when a row was deleted or an era minted: the
    bulk ``DELETE`` synchronises nothing and ``PaySchedule.eras`` is
    view-only, so a row the wider request already loaded would otherwise name
    a period that is gone or the eras it had BEFORE the mint.

    Args:
        change: The whole change (:class:`_PaydayChange`).

    Returns:
        The newly created rows, flushed, ``start_date`` ascending.

    Raises:
        ValidationError: ``mint_era`` refuses the cadence or the pairing.
            Unreachable from :func:`record_paydays`, which asks the same
            bounds before any statement is issued; kept because ``mint_era``
            is the column's one writer and owns the refusal.
    """
    if change.retiring:
        db.session.query(PayPeriod).filter(
            PayPeriod.user_id == change.user_id,
            PayPeriod.id.in_(change.retiring),
        ).delete(synchronize_session=False)
    retired_eras = 0
    if change.recording:
        pay_schedule_service.ensure_schedule_row(change.user_id)
        retired_eras = pay_era_write.retire_eras(
            change.user_id, change.eras_standing,
        )
        if change.era is not None:
            pay_era_write.mint_era(change.user_id, change.era)
    created = _create_periods(change.user_id, change.recording)
    if change.retiring or retired_eras or change.era is not None:
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
