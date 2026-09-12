"""The grid an era pays on: the displacement producer, its bounds, and the package's refusal.

**Plan step ``pay_calendar:C17-b-2``, a pure move** (ruling **R-PC73**,
developer 2026-09-11).  :class:`PayCalendarError`, the cadence bounds,
:func:`validate_cadence` and :func:`projected_payday` lived in :mod:`._derive`
beside the derivation they serve, and that module stood at 799 of pylint's
1,000 lines with the money-moving leaf about to add the ERA producers -- the
readers that place a recorded payday on its era's grid and continue the rhythm
from the era's phase rather than from the record (**R-PC66**, **R-PC72**).
Those producers must sit at or below :func:`~._derive.derive_periods`, which
closes the last saved period through them, and they need the displacement
producer and the error type; so the cut follows the SUBJECT rather than the
line count.  :mod:`._derive` keeps what derives PERIODS from a RECORD; this
module is everything about the grid an era pays on, which is also where plan
step ``C17-d``'s cadence KIND will branch.  Nothing in any moved definition
changed in the move; the docstrings carry their history.  Rejected, on the
fork presented: moving only the error type and the producer (a weaker subject
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

from datetime import date

from app.exceptions import ShekelError
from app.services.pay_rhythm import Rhythm
from app.utils.business_days import shift_to_business_day

from ._grid import nominal_payday

#: The cadence bounds, mirroring ``ck_pay_schedule_cadence_range`` on
#: ``budget.pay_schedule.cadence_days``.  Named here rather than inlined
#: because :func:`validate_cadence` states them in its refusal message, and a
#: message that quotes a bound the code does not enforce is how the first cut
#: of this module shipped.
#:
#: **A second copy of the pair lives on the model** as
#: :data:`app.models.pay_schedule.CADENCE_DAYS_MIN` /
#: :data:`~app.models.pay_schedule.CADENCE_DAYS_MAX`, where plan step X-ad-a
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
    expression: :func:`derive_periods` closes the last saved period with it,
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

    **What it does NOT compound, and what it cannot guarantee.**  *steps* is
    counted from one fixed *anchor* rather than accumulated, so a displaced
    answer cannot move a later payday in the same call -- the hazard
    :func:`~app.utils.business_days.shift_to_business_day` hands explicitly to
    its caller.  What it cannot do is make the ANCHOR nominal:
    :func:`~._projection.project_period_after` anchors on the last RECORDED payday and
    :func:`~._rhythm._backdated_paydays` on the first, and ``C14-e-3``'s writer
    records DISPLACED days -- so an owner whose boundary payday payroll moved
    is projected a rhythm off by that displacement until a real payday is
    recorded again.  That is ledger rows **N-495** and **PC-502**, both owned
    by ``C17``, and the reason neither is repaired here is STRUCTURAL rather
    than sequencing: a phase is an ERA's fact, and one stored
    ``budget.pay_schedule.nominal_anchor`` cannot describe a PIECEWISE owner
    (**N-492** -- ``record_paydays`` permits *correct my cadence going
    forward*), so anchoring on it would let :func:`derive_periods` compute a
    last end BELOW its own start and refuse that owner's whole calendar.

    Args:
        anchor: A payday the owner's rhythm passes through.
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


def validate_cadence(cadence_days: int) -> None:
    """Refuse a cadence that is not an in-range plain integer.

    Held to the same standard as :func:`_validated` holds a payday, and for the
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
    whether they had paydays -- a question only :func:`derive_periods` could
    answer, so it guarded this call and owned a refusal of its own.  A calendar
    now requires a cadence outright: an owner with no ``budget.pay_schedule``
    row has no calendar rather than a cadence-less one, so the pair that needed
    a second opinion cannot be built.  The refusal below is the whole of it, and
    it is the check already written for ``bool`` and ``float`` doing one more
    type's work rather than a new fence.

    The upper bound is the stored column's own
    (``ck_pay_schedule_cadence_range``, 1..365).  Enforcing only the lower half
    while the error message quoted both was the gap; a cadence above 365 cannot
    come from a schedule row, so accepting one would mean projecting a horizon
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
            f"ceiling the value cannot have come from a stored schedule -- "
            f"budget.pay_schedule.cadence_days is constrained to "
            f"{MIN_CADENCE_DAYS}..{MAX_CADENCE_DAYS} by "
            f"ck_pay_schedule_cadence_range."
        )
