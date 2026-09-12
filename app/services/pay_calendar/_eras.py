"""The grid an era pays on: the displacement producer, its bounds, and the package's refusal.

**Plan step ``pay_calendar:C17-b-2``, in two commits** (ruling **R-PC73**,
developer 2026-09-11).  The first was a PURE move: :class:`PayCalendarError`,
the cadence bounds, :func:`validate_cadence` and :func:`projected_payday`
lived in :mod:`._derive` beside the derivation they serve, and that module
stood at 799 of pylint's 1,000 lines with the money-moving leaf about to add
the ERA producers -- the readers that place a recorded payday on its era's
grid and continue the rhythm from the era's phase rather than from the record
(**R-PC66**, **R-PC72**).  Those producers must sit at or below
:func:`~._derive.derive_periods`, which closes the last saved period through
them, and they need the displacement producer and the error type; so the cut
follows the SUBJECT rather than the line count.  :mod:`._derive` keeps what
derives PERIODS from a RECORD; this module is everything about the grid an
era pays on, which is also where plan step ``C17-d``'s cadence KIND will
branch.  Nothing in any moved definition changed in the move; the second
commit then landed the producers here -- :func:`first_payday_of`,
:func:`era_index_at`, :func:`step_after`, :func:`matched_step`,
:func:`last_step_of`, :func:`matched_planned`, :func:`following_planned`,
:func:`horizon_step`, :func:`payday_after` -- and rewrote the moved
docstrings where the anchor they described had moved.  Rejected, on the fork
presented: moving only the error type and the producer (a weaker subject
line for the module), and trimming :mod:`._derive`'s prose to fit (the shape
**R-PC60** and **R-PC69** refused).

Placed between :mod:`._grid` and :mod:`._derive` in the package's one-way
chain: it imports the nominal grid and nothing above it, and every module
above reaches the producer and the refusal here.  Pure, on the package's
standing terms: no session, no Flask, no clock.  **Every application import
this module takes is a PURE LEAF** -- ``app.exceptions``,
``app.services.pay_rhythm``, ``app.utils.business_days`` -- which is the rule
:mod:`._derive` states and this module inherits.
"""

from datetime import date, timedelta

from app.exceptions import ShekelError
from app.services.pay_rhythm import Era, Rhythm
from app.utils.business_days import shift_to_business_day

from ._grid import cadence_steps_to, nominal_payday

#: The cadence bounds, mirroring ``ck_pay_eras_cadence_range`` on
#: ``budget.pay_eras.cadence_days`` (on ``budget.pay_schedule`` until plan step
#: ``pay_calendar:C17-a`` moved the column).  Named here rather than inlined
#: because :func:`validate_cadence` states them in its refusal message, and a
#: message that quotes a bound the code does not enforce is how the first cut
#: of this module shipped.
#:
#: **A second copy of the pair lives on the model** as
#: :data:`app.models.pay_era.CADENCE_DAYS_MIN` /
#: :data:`~app.models.pay_era.CADENCE_DAYS_MAX`, where plan step X-ad-a
#: collapsed six literals into one name.  This module does not import it, and
#: the reason is this package's purity: importing a model pulls
#: ``app.extensions`` in and closes an import cycle through
#: ``pay_schedule_service``, which would end the "drive the derivation with no
#: database" property C1's harness rests on.  The two are held equal by
#: ``tests/test_models/test_pay_schedule.py::TestTheCadenceBoundHasOneValue``
#: rather than by whoever edits one remembering the other.
MIN_CADENCE_DAYS: int = 1
MAX_CADENCE_DAYS: int = 365


class PayCalendarError(ShekelError, ValueError):
    """No pay calendar can be derived: there is no schedule, or it is unusable.

    **TWO states reach it and ``app/error_handlers.py`` answers both with one
    page**, deliberately (plan step C4-b-2, the handler ledger row **P35**
    deferred).  One is ORDINARY and repairable: the owner holds no
    ``budget.pay_schedule`` row, so there is no rhythm to derive anything from
    -- :func:`~._loader.calendar_for` and :func:`~._loader.cadence_for` refuse
    them from one place since plan step C4-d (ruling **R-PC45**), where the
    cadence door used to refuse and the calendar door used to answer an empty
    calendar carrying no cadence.  The other is a BROKEN INVARIANT no write
    door produces: a payday set or a cadence that cannot define a calendar.  The
    owner can act on the first and can do nothing about the second, and both
    leave every per-period figure unanswerable, so the page offers the one
    repair that exists and the LOG carries which state it was.

    **Neither state is user INPUT**, which is why no form field is named in
    the message: one is a setup state the owner repairs elsewhere and the other
    is a broken invariant.  *An earlier form of this paragraph also said "no
    route catches it", which was false when written --
    ``routes/accounts/statements`` catches it beside ``BaselineMissingError``
    and degrades -- and would have read as licence to leave the application
    without an answer.  ``app/error_handlers.py`` registers one (plan step
    C4-b-2, ruling R-PC42); a route catching it to DEGRADE a fragment is
    ordinary beside that.*  ``budget.pay_periods`` already enforces the payday
    model's key (``uq_pay_periods_user_start``), so a duplicate payday cannot
    come out of the table; reaching the second state from the application would
    mean a caller assembled a payday set by hand and got it wrong.  Failing
    loud is the only safe disposition for it -- every alternative
    (de-duplicating, clamping a bad cadence) silently produces a calendar whose
    periods do not tile the days the owner's money lives on.

    **A caller's own ``Raises:`` names the cause that matters at its site**
    rather than re-enumerating both; this class is where the pair is stated.

    Also a ``ValueError`` because it is raised for rejected function arguments,
    where that is Python's own contract; a caller catching either name gets it.

    It is NOT the successor of ``recurrence._calendar.RecurrenceScheduleError``,
    and saying so is a correction the review of C1 made.  That class refused an
    overlapping or reversed SCHEDULE at the value boundary, and the plan retired
    it rather than relocating it: plan step **C2-b2** DELETED the class that
    held its only two raise sites, because the states they policed stopped being
    expressible once the periods are DERIVED.  (An earlier draft of this
    paragraph credited that deletion to C5a, which had it on its list until the
    C2-b decomposition measured that the class dies three leaves earlier.)
    What this class refuses is different -- a payday SET or a cadence that
    cannot define a calendar in the first place.

    **One of those refusals WAS reachable from a page, and plan step C4-b-2
    closed that route rather than handling it** (ledger row **P35**).
    :func:`~._loader.calendar_for` resolves the cadence through
    ``pay_schedule_service.resolve_schedule``, which until that step fell back
    to inferring it from the last period's stored length -- bounded below by
    ``ck_pay_periods_date_order`` and NOT bounded above -- so a hand-written
    period spanning more than a year refused the calendar, and since C2-c that
    meant a 500 on every balance page.  ``fk_pay_periods_schedule`` makes the
    schedule-row-less owner unstorable, so the only source of a cadence is the
    column, bounded to 1..365 by ``ck_pay_schedule_cadence_range`` -- the same
    range this class enforces, which is why the two cannot now disagree.  What
    the refusal still covers is a CALLER, not a page.  Failing loud remains
    right (the alternative is projecting a horizon off a value no write
    door could have produced).  *The paragraph this replaces said C4 would
    remove the fallback "with the column it reads"; the key removed it one leaf
    earlier, and ``end_date`` is still C4-c's to drop.*
    """


def projected_payday(anchor: date, rhythm: Rhythm, steps: int) -> date:
    """Return the payday *steps* whole cadences after *anchor*.

    **The projection's ONE producer, and since plan step
    ``pay_calendar:C14-e-3`` it is the nominal grid day DISPLACED onto a
    business day under the owner's convention** (**R-PC54**: applied at the
    PRODUCER, because the payday a COUNT uses and the payday a PERIOD opens on
    are one value).  Every consumer inherits the shift from this one
    expression: :func:`~._derive.derive_periods` closes the last saved period with it
    (through :func:`payday_after`, as ``pay_period_write``'s floor does),
    :func:`~._projection.project_period_after` opens and closes a projected one,
    :func:`~._rhythm._backdated_paydays` walks the rhythm below the record,
    ``pay_period_write._requested_paydays`` records the days a batch asks for,
    and ``auth_service`` bounds the payday a sign-up may state.

    **The GRID is a module below this one** (``C14-d``, **R-PC60**), and the
    split is the point rather than a place to put a function:
    :func:`~._grid.nominal_payday` answers what a WRITER continues a rhythm on,
    this answers what a calendar SHOWS and what money is filed against, and
    from this step the two differ on roughly 3% of paydays -- **64** of the
    production owner's 1,888 PROJECTED paydays out to ``CALENDAR_DATE_MAX``,
    and **22** of the 684 below their record, under either displacing
    convention, re-derived 2026-09-06.

    **TWO spellings are left outside this function, NAMED rather than
    counted** -- an adversarial review of ``C14-e-3`` found this census
    claiming one and missing the other.  ``scripts/integrity_check.py``'s
    ``BA-06`` horizon in SQL cannot call this (ledger row **PC-501**, whose
    remedy is that check's DELETION), and
    ``migrations/versions/b7a41e2c9d63``'s ``_REBUILD_DERIVED_COLUMNS_SQL``
    restates the same end for a DOWNGRADE (**PC-506**).  ``C14-e-3`` deleted
    the two it could reach by routing both HERE:
    :func:`~._rhythm._backdated_paydays` open-coded the walk twice, and the
    registration window restated the first paycheck's span as
    ``first_payday + cadence_days - 1``.

    **The BOUND is the CALLER's** -- ``C14-a``'s stated obligation on the
    displacement:
    :func:`~app.utils.business_days.shift_to_business_day` may answer outside
    :data:`~app.utils.dates.CALENDAR_DATE_MIN` ..
    :data:`~app.utils.dates.CALENDAR_DATE_MAX`, because both ends of that range
    are themselves closed days.

    **What it does NOT compound, and since plan step ``C17-b-2`` what it no
    longer has to tolerate.**  *steps* is counted from one fixed *anchor*
    rather than accumulated, so a displaced answer cannot move a later payday
    in the same call -- the hazard
    :func:`~app.utils.business_days.shift_to_business_day` hands explicitly to
    its caller.  What it could not do was make the ANCHOR nominal: until
    ``C17-b-2`` :func:`~._projection.project_period_after` anchored on the
    last RECORDED payday and :func:`~._rhythm._backdated_paydays` on the
    first, while ``C14-e-3``'s writer records DISPLACED days -- so an owner
    whose boundary payday payroll moved was projected a rhythm off by that
    displacement until a real payday was recorded again (ledger rows
    **N-495**, **PC-502**).  Every reader now anchors on the phase of the ERA
    covering its own day, ``budget.pay_eras.effective_from``, a day the grid
    passes through by definition (**R-PC66**); the record's last payday is
    MATCHED to the grid day it stands for rather than stepped from
    (:func:`matched_step`).  That could not land while the phase was one
    stored ``nominal_anchor`` on the schedule row, because one anchor cannot
    describe a PIECEWISE owner (**N-492**).

    Args:
        anchor: A day the owner's NOMINAL grid passes through: an era's
            ``effective_from`` at every reader, and the first nominal payday
            of the batch at the writer.
        rhythm: The owner's cadence and payday convention
            (:class:`~app.services.pay_rhythm.Rhythm`).  The cadence is a
            positive ``int`` already validated by :func:`validate_cadence` at
            the caller; re-validating per call would put the bound in a second
            place.  **The convention is read here and nowhere else in this
            package**, which is what makes the money-moving diff one
            expression.
        steps: How many whole cadences after *anchor*.  ``1`` is the next
            payday, ``0`` is *anchor* -- which is NOT the identity once a
            convention displaces, and callers rely on that: it is how a nominal
            grid day becomes the day money moves.  NEGATIVE is reachable and
            not a misuse: :meth:`~._calendar.PayCalendar.span_containing` asks
            :func:`~._projection.project_period_after` about days BELOW its anchor, and
            :func:`~._rhythm._backdated_paydays` walks the rhythm below the
            record.

    Returns:
        The payday -- the grid day *steps* cadences after *anchor*, displaced
        onto a business day under *rhythm*'s convention.  Under
        :attr:`~app.enums.BusinessDayShiftEnum.NONE` that is the grid day
        itself, which is why this step moves ``$0.00`` for an owner who has not
        answered the question.
    """
    return shift_to_business_day(
        nominal_payday(anchor, rhythm.cadence_days, steps), rhythm.shift,
    )


def first_payday_of(era: Era) -> date:
    """Return the day *era*'s first paycheck is PAID: its phase, displaced.

    An era's ``effective_from`` is its first NOMINAL payday (**R-PC66**); the
    day money moves on is that day under the era's own convention, and it is
    the day the era's paydays start from in every reader here.  One
    expression, because the seam between two eras is drawn on it twice --
    where the earlier era's paydays stop and where the later era's start --
    and two spellings of a boundary are how a day comes to fall in both.

    Args:
        era: The era.

    Returns:
        ``projected_payday(era.effective_from, era.rhythm, 0)``.
    """
    return projected_payday(era.effective_from, era.rhythm, 0)


def era_index_at(eras: "tuple[Era, ...]", day: date) -> int:
    """Return the index of the era whose PAYDAYS cover *day*.

    **The rule an era's span is derived by, read in CASH days** (plan step
    ``C17-b-2``): an era pays from its first payday
    (:func:`first_payday_of`) up to the next era's, so the era covering a day
    is the latest whose first payday falls on or before it -- and a day
    before every era's is the EARLIEST era's, because that era alone runs
    backward below the record (**R-PC66**).
    :func:`~app.services.pay_rhythm.era_covering` is the same rule read in
    NOMINAL days, which is the writer's coordinate: a batch states its first
    payday on the grid, so the era-mint decision asks which era's
    ``effective_from`` bounds that day.  The two agree wherever an era's first
    payday is not displaced, and part by the displacement where it is -- an
    era minted on a nominal holiday under ``prior`` pays its first paycheck
    BEFORE its own ``effective_from``, and that paycheck is the new era's, not
    the old one's.

    A linear scan rather than a bisection, for
    :func:`~app.services.pay_rhythm.era_covering`'s reason: an owner holds a
    handful of eras, and this is asked once per answer and never per row.

    Args:
        eras: The owner's eras, validated (:func:`~._derive.validate_eras`).
        day: The day to place -- a recorded payday, a day to project for, or
            a day below the record.

    Returns:
        The index into *eras*.
    """
    covering = 0
    for index, era in enumerate(eras):
        if first_payday_of(era) > day:
            break
        covering = index
    return covering


def step_after(anchor: date, rhythm: Rhythm, day: date) -> int:
    """Return the first grid step whose PAYDAY falls strictly after *day*.

    The one loop behind two questions: :func:`~._searches.nominal_payday_after`
    answers the grid day a writer continues from (the day the floor admits),
    and :func:`last_step_of` answers where an era's paydays stop, which is
    the step before the first one paid on or after the next era's first
    payday.  Each asks about a DERIVED day -- a horizon, a seam -- never a
    recorded one; a recorded payday is placed by :func:`matched_step`, whose
    docstring says why the two rules differ.

    **Three candidates are enough, and it is a theorem rather than a
    margin.**  The estimate satisfies ``nominal(estimate) <= day <
    nominal(estimate + 1)`` by :func:`~._grid.cadence_steps_to`'s floor
    division, and a displacement is strictly shorter than a cadence --
    :func:`~app.utils.business_days.shortest_collision_free_cadence` is the
    longest run of closed days PLUS ONE and
    ``pay_schedule_service.reject_shift_on_short_cadence`` holds a displacing
    convention above it -- so ``nominal(estimate + 2)``'s payday clears *day*
    under either convention.

    Args:
        anchor: A day the owner's NOMINAL grid passes through.
        rhythm: The owner's cadence and payday convention.
        day: The day the answer's PAYDAY must fall after.

    Returns:
        The step count from *anchor*, for :func:`projected_payday` or
        :func:`~._grid.nominal_payday`.

    Raises:
        PayCalendarError: No candidate within two cadences clears *day*,
            which needs a displacement at least a cadence long.  The write
            door refuses that pairing; ledger row **N-493** is that a
            write-time refusal cannot see a stored row a later holiday-set
            change made illegal.
    """
    estimate = cadence_steps_to(anchor, rhythm.cadence_days, day)
    for steps in range(estimate, estimate + 3):
        if projected_payday(anchor, rhythm, steps) > day:
            return steps
    raise PayCalendarError(
        f"no payday on the grid anchored {anchor.isoformat()} at a "
        f"{rhythm.cadence_days}-day cadence falls after {day.isoformat()} "
        f"within two cadences.  That needs a displacement at least a cadence "
        f"long, which pay_schedule_service.reject_shift_on_short_cadence "
        f"refuses at the write door -- ledger row N-493 is that a write-time "
        f"refusal cannot see a stored row a later holiday-set change made "
        f"illegal."
    )


def matched_step(anchor: date, rhythm: Rhythm, payday: date) -> int:
    """Return the grid step a RECORDED payday stands for: the nearest one.

    **How a record is placed on the grid it was paid from** (plan step
    ``C17-b-2``).  A recorded payday is what the bank did, and **R-PC47**
    says it may fall off the cadence: payroll moved it onto a business day,
    or moved it for a reason the convention does not model.  The paycheck it
    IS is the planned one nearest to it -- the ruling's own example is the
    2025-12-31 that was the 2026-01-01 paycheck paid early -- so the next
    paycheck is the grid day AFTER that one, whatever the recorded day is.

    **Why not the first grid payday strictly after the record**, which is
    what :func:`step_after` answers and what a first cut of this step would
    have used: on a record paid a day EARLY under a convention that explains
    no displacement (``none``), that rule names the very paycheck the record
    already stands for -- a one-day pay period followed by a phantom paycheck
    in the forecast, income the owner will not receive.  Matching instead
    reads the early payment as the paycheck it was.

    **A tie -- a record exactly half a cadence from two grid paydays -- goes
    to the LATER one.**  The distance is unimodal in the step (paydays
    ascend with it), so the scan stops at the first step that is further
    than the last, and ``<=`` keeps the later of two equidistant steps.  The
    later step puts the next paycheck further out, which is the poorer
    forecast -- an application that budgets guesses poor where it must
    guess.  **It is ONE rule, and the backward half pays for that.**  A
    record is one paycheck, so :func:`~._rhythm._backdated_paydays` reads
    the same answer for the opening payday -- and there "the later one"
    leaves the grid day half a cadence BELOW the opening counted as a
    paycheck of its own, the over-counting direction that half's own
    docstring says to avoid.  Two tie rules would let one record be two
    paychecks; the exact tie no door produces (a record is a displaced grid
    day, and a displacement is shorter than half a cadence at every legal
    pairing) is the price of one.

    Args:
        anchor: A day the owner's NOMINAL grid passes through -- the era's
            ``effective_from``.
        rhythm: The era's cadence and payday convention.
        payday: The recorded payday to place.

    Returns:
        The step count from *anchor* of the planned payday nearest *payday*.
    """
    steps = cadence_steps_to(anchor, rhythm.cadence_days, payday) - 1
    nearest, distance = steps, abs(
        (projected_payday(anchor, rhythm, steps) - payday).days,
    )
    while True:
        steps += 1
        candidate = abs((projected_payday(anchor, rhythm, steps) - payday).days)
        if candidate > distance:
            return nearest
        nearest, distance = steps, candidate


def last_step_of(eras: "tuple[Era, ...]", index: int) -> "int | None":
    """Return the last grid step era *index* PAYS, or ``None`` for the latest.

    An era's paydays run up to the day before the next era's first payday
    (:func:`first_payday_of`), so its last step is the one before the first
    step paid on or after that day.  Measured in CASH days rather than
    nominal ones, for :func:`era_index_at`'s reason: where two conventions
    meet, a nominal bound can put one era's last paycheck on the same day as
    the next era's first, and a day paid twice is a phantom paycheck.

    Args:
        eras: The owner's eras, validated.
        index: Which era.

    Returns:
        The step, or ``None`` when *index* names the latest era, whose
        paydays run to the end of the application's calendar.
    """
    if index + 1 == len(eras):
        return None
    era = eras[index]
    seam = first_payday_of(eras[index + 1]) - timedelta(days=1)
    return step_after(era.effective_from, era.rhythm, seam) - 1


def matched_planned(
    eras: "tuple[Era, ...]", payday: date,
) -> "tuple[int, int]":
    """Return ``(era index, step)`` of the PLANNED payday a record stands for.

    :func:`matched_step` over the PIECEWISE plan rather than one era's
    unbounded grid.  The record is placed in the era that paid it
    (:func:`era_index_at`) and matched within that era's grid; when the
    match lands on or past the era's last planned payday
    (:func:`last_step_of`), the next era's FIRST payday is a candidate too,
    and the nearer of the two wins -- a tie to the later, as within an era.
    *An adversarial review of this step built the case: eras 14 days from
    2026-01-02 and 7 days from 02-02, the record's last payday 02-01 -- the
    02-02 paycheck paid a day early.  Matched inside the first era's grid
    alone it stood for 01-30 (two days off) and the calendar closed it on
    02-01 with a paycheck projected on 02-02, the phantom the matching rule
    exists to prevent; the planned list's nearest is 02-02, one day off.*

    Args:
        eras: The owner's eras, validated.
        payday: The recorded payday to place.

    Returns:
        The era index and the step within that era.
    """
    index = era_index_at(eras, payday)
    era = eras[index]
    steps = matched_step(era.effective_from, era.rhythm, payday)
    last = last_step_of(eras, index)
    if last is None or steps < last:
        return index, steps
    own = abs((projected_payday(era.effective_from, era.rhythm, last) - payday).days)
    following = abs((first_payday_of(eras[index + 1]) - payday).days)
    if following <= own:
        return index + 1, 0
    return index, last


def following_planned(
    eras: "tuple[Era, ...]", index: int, steps: int,
) -> "tuple[int, int]":
    """Return ``(era index, step)`` of the planned payday after era *index*'s step *steps*.

    The next step of the same era, or -- at the era's last planned payday
    (:func:`last_step_of`) -- the next era's first.  The one statement of
    what follows a planned payday across a seam: :func:`horizon_step` reads
    it for the record's continuation and
    :func:`~._projection.project_period_after` for every projected period's
    end.

    Args:
        eras: The owner's eras, validated.
        index: The era.
        steps: The grid step within it, at or below the era's last.

    Returns:
        The era index and the step within that era.
    """
    last = last_step_of(eras, index)
    if last is not None and steps >= last:
        return index + 1, 0
    return index, steps + 1


def horizon_step(
    eras: "tuple[Era, ...]", last_payday: date,
) -> "tuple[int, int]":
    """Return ``(era index, step)`` of the first payday PROJECTED after the record.

    The seam between the record and its continuation, stated once: the
    last recorded payday is matched to the planned payday it stands for
    (:func:`matched_planned`) and the next payday is the one that follows it
    (:func:`following_planned`).  :func:`payday_after` reads this for the
    day itself, which is what :func:`~._derive.derive_periods` closes the
    last saved period before and what ``pay_period_write``'s floor admits;
    :func:`~._projection.project_period_after` reads it for the ordinal
    every projected period's ``period_index`` is counted from.

    Args:
        eras: The owner's eras, validated.
        last_payday: The latest recorded payday.

    Returns:
        The era index and the step within that era.
    """
    return following_planned(eras, *matched_planned(eras, last_payday))


def payday_after(eras: "tuple[Era, ...]", last_payday: date) -> date:
    """Return the first payday the owner's eras PROJECT after *last_payday*.

    :func:`horizon_step` as a day.  It is strictly after *last_payday* by
    construction -- the matched step's next payday is later than the record
    it follows, and a next era's first payday is later than any day its
    predecessor covers -- so the period it closes can never end before it
    opens (ledger row **PC-505**: the reversed period the recorded anchor
    admitted below the collision floor is unrepresentable here).

    Args:
        eras: The owner's eras, validated.
        last_payday: The latest recorded payday.

    Returns:
        The next projected payday, displaced under its era's convention.
    """
    index, steps = horizon_step(eras, last_payday)
    era = eras[index]
    return projected_payday(era.effective_from, era.rhythm, steps)


def validate_cadence(cadence_days: int) -> None:
    """Refuse a cadence that is not an in-range plain integer.

    Held to the same standard as :func:`~._derive._validated` holds a payday, and for the
    same reason -- the review of C1 measured what the looser check let through.
    ``bool`` is an ``int`` subclass, so ``True`` was accepted as a one-day
    cadence; and a ``float`` was accepted and silently TRUNCATED, because
    ``date.__add__`` reads only ``timedelta.days``, so ``14.9`` produced the
    same calendar as ``14``.

    **Package-internal rather than underscore-private, and plan step R7a-2a is
    why**: :class:`~._cadence.PayCadence` validates through this same function,
    so the bound has one implementation across the two values that hold a
    cadence.  It stays out of the package's public surface -- this module is
    private and ``__init__`` does not re-export it -- so the name is visible to
    siblings and to nothing else.

    **``None`` IS this function's subject since plan step C4-d** (ruling
    **R-PC45**), and that is a reversal of the rule C2-b1 wrote here.  Absence
    used to mean "this owner has no schedule at all", whose legality depended on
    whether they had paydays -- a question only :func:`~._derive.derive_periods` could
    answer, so it guarded this call and owned a refusal of its own.  A calendar
    now requires a cadence outright: an owner with no ``budget.pay_schedule``
    row has no calendar rather than a cadence-less one, so the pair that needed
    a second opinion cannot be built.  The refusal below is the whole of it, and
    it is the check already written for ``bool`` and ``float`` doing one more
    type's work rather than a new fence.

    The upper bound is the stored column's own
    (``ck_pay_eras_cadence_range``, 1..365).  Enforcing only the lower half
    while the error message quoted both was the gap; a cadence above 365 cannot
    come from an era row, so accepting one would mean projecting a horizon
    off a value no write door could have produced.

    Args:
        cadence_days: The candidate cadence.

    Raises:
        PayCalendarError: The value is not an ``int`` (a ``bool`` and ``None``
            included), or falls outside 1..365.
    """
    if not isinstance(cadence_days, int) or isinstance(cadence_days, bool):
        raise PayCalendarError(
            f"cadence_days must be a plain int, got "
            f"{type(cadence_days).__name__} {cadence_days!r}.  A bool is an "
            f"int subclass and would pass as a one-day cadence, and a float is "
            f"truncated by date arithmetic, which moves a horizon silently.  "
            f"None reaches here as a caller that built a calendar without a "
            f"cadence: since plan step C4-d there is no such calendar, because "
            f"an owner with no budget.pay_schedule row has no calendar at all "
            f"(pay_calendar._loader.calendar_for refuses them)."
        )
    if not MIN_CADENCE_DAYS <= cadence_days <= MAX_CADENCE_DAYS:
        raise PayCalendarError(
            f"cadence_days must be at least {MIN_CADENCE_DAYS} day and at "
            f"most {MAX_CADENCE_DAYS}, got {cadence_days}.  Below the floor "
            f"the last pay period would end before its own payday; above the "
            f"ceiling the value cannot have come from a stored era -- "
            f"budget.pay_eras.cadence_days is constrained to "
            f"{MIN_CADENCE_DAYS}..{MAX_CADENCE_DAYS} by "
            f"ck_pay_eras_cadence_range."
        )
