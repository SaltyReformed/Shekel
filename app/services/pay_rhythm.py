"""
Shekel Budget App -- An owner's pay rhythm, and the ERA it ran in, as values.

How often somebody is paid, and what payroll does when a payday lands on a day
no money moves on.  ``budget.pay_eras`` stores the pair as ``cadence_days``
and ``shift_id`` on each era row; :class:`Rhythm` is that pair in the
application, and it is a TYPE rather than two arguments because the halves
carry a JOINT rule -- a convention that displaces a payday is legal only on a
cadence longer than the longest run of consecutive closed days
(:func:`~app.utils.business_days.shortest_collision_free_cadence`).
:class:`Era` is a rhythm together with the day it took effect -- one row of
that table as a value (plan step ``pay_calendar:C17-a``, ruling **R-PC58**).

**The cadence is a VALUE OF ITS OWN KIND, and the kind is its type** (plan
step ``pay_calendar:C17-d-1``, rulings **R-PC80** and **R-PC81**).  Three
kinds: :class:`FixedDays` -- every ``days`` days from the era's first payday;
:class:`Monthly` -- one owner-chosen day of the month; :class:`SemiMonthly`
-- two of them (plan step ``C17-d-2``, ruling **R-PC79**).  A kind is a
class rather than an enum member beside an ``int`` because each kind's
parameters have a shape of their own (a day count; a day of the month; a
pair of them), so an ``int`` field that meant "days apart" under one kind
and something else under another would be a conditionally meaning-shifting
column of the value.  Which arithmetic a kind pays on is
:mod:`app.services.pay_calendar._grid`'s, dispatched on the value's type;
this module holds the values and no DATE arithmetic.  **Two facts a kind
states about its own parameters live on the value**, because the one
module that asks them (``pay_schedule_service``, the write door) can import
this leaf and nothing of the pay-calendar package: the SHORTEST GAP between
two of its grid days, which the collision floor is asked of (**R-PC79**),
and the PHRASE a refusal names it by.  Both read the parameters and no
calendar.

**Why it is a module of its own, which is plan step ``C14-e-1``'s one
structural decision.**  The pair was declared in
:mod:`app.services.pay_schedule_service`, and until ``C14-e`` that was the
right place: the pay calendar read only ``cadence_days``, so the one consumer
of the pair was the write door beside it.  ``C14-e-3`` made
:func:`~app.services.pay_calendar.projected_payday` the nominal grid day
DISPLACED under the convention, and ``C14-e-1`` threaded the pair to every
producer ahead of it so that money-moving diff was one expression -- which gives
the PURE half of :mod:`app.services.pay_calendar` the convention to carry.
That half may not import a module holding a database session, nor could it,
because ``pay_calendar._loader`` imports ``pay_schedule_service`` and the edge
back would be a cycle (pylint ``R0401``, measured 2026-09-05).

Three placements were possible and two are worse.  A second, structurally
identical pair inside the pay-calendar package is one value with two homes,
which is the defect rule 14 exists to refuse.
:mod:`app.utils.business_days` is the one module already below both, and it
owns the convention and the collision floor that constrains this pair -- but a
pay CADENCE is not a business-day fact, and at plan step ``C17`` this value
grows an ``effective_from`` and a cadence KIND (**R-PC58**), which would
stretch that module's subject further with every step.  So the value gets the
module its own name describes, below both consumers and above nothing:
**this module imports** :mod:`app.enums`, **one constant of**
:mod:`app.utils.dates` **and nothing else** -- the first imports only the
standard library's ``enum``, the second is the pure date leaf below every
package.  ``C17-a`` grew the value here exactly as this paragraph predicted,
and the import set did not move; ``C17-d-2`` added the one calendar constant
the month kinds' shortest gaps are measured against
(:data:`~app.utils.dates.SHORTEST_MONTH_DAYS`).

*The ROOT CAUSE this placement works around, stated rather than claimed away:*
:mod:`app.services.pay_calendar` *is one package holding a PURE derivation and
the IMPURE loader that feeds it, and its* ``__init__`` *re-exports both -- so
importing the package pulls the schedule service in behind it.  From scratch
the reader would live inside the pay-calendar package, which would then own
this value outright and* ``pay_schedule_service`` *would be the row WRITER
importing it.  That is a package split rather than a placement, and it is not
this step's.*
"""

from dataclasses import dataclass
from datetime import date

from app.enums import BusinessDayShiftEnum
from app.utils.dates import SHORTEST_MONTH_DAYS


@dataclass(frozen=True)
class FixedDays:
    """A cadence of every *days* days from the era's first payday.

    The kind every owner held until plan step ``pay_calendar:C17-d`` -- a
    fortnight, a week, thirty days -- and the one whose grid is an arithmetic
    progression: ``budget.pay_eras.cadence_days`` is its one parameter, and
    :mod:`app.services.pay_calendar._grid` steps it by plain day arithmetic.

    Attributes:
        days: Days between consecutive paydays.  Bounded by
            :func:`~app.services.pay_schedule_service.reject_out_of_range_cadence`,
            not by this type -- a range is the column's own rule and belongs
            where its one writer asks it.  Validated again, for a different
            bound and a different reason, by
            :func:`~app.services.pay_calendar.derive_periods`.
    """

    days: int

    @property
    def shortest_gap(self) -> int:
        """Return the fewest days between two of this grid's paydays: *days*.

        Every gap on a fixed-days grid is the day count, so the shortest is
        too.  The one property the collision floor
        (``pay_schedule_service.reject_shift_on_short_cadence``) reads of a
        cadence, and every theorem the calendar package leans on -- three
        candidates are enough in ``_eras.step_after``, a record is matched
        within one gap -- rests on "a displacement is shorter than the gap
        between two grid days", never on the gap being constant (**R-PC79**).

        Returns:
            The day count.
        """
        return self.days

    @property
    def phrase(self) -> str:
        """Return how an owner would say this cadence: ``"every 14 days"``.

        The one spelling a refusal names the rhythm by, for every message
        that said ``"{days}-day cadence"`` until plan step ``C17-d-2`` --
        a month kind has no day count to put there.  The grammar is
        :func:`app.services.recurrence.describe`'s ("day 15", no ordinal
        suffix), so a cadence reads the same wherever the application
        phrases one.

        Returns:
            The phrase.
        """
        return "every day" if self.days == 1 else f"every {self.days} days"


@dataclass(frozen=True)
class Monthly:
    """A cadence of one owner-chosen day of every month (ruling **R-PC79**).

    Paid on *day* of each month, where a day the month is too short to hold
    -- 29, 30 or 31 -- means its last day: a day-31 owner is paid 31 January,
    28 February, 31 March, and the meant day never decays (the shape
    ``recurrence:R-R3`` gives a rule's ``nominal_day``).  The era's
    ``effective_from`` is its first payday; it carries the MONTH, and this
    value carries the day, so an era opening 2026-02-28 on ``Monthly(31)``
    projects 03-31, 04-30, 05-31.  Stored as the ABSENCE of both
    ``cadence_days`` and ``other_day`` on ``budget.pay_eras`` (ruling
    **R-PC80**), with ``nominal_day`` beside it only when the first month
    was too short to carry the day.

    Attributes:
        day: The day of the month, 1..31.  Bounded by
            :func:`~app.services.pay_schedule_service.reject_out_of_range_cadence`
            at the write door and by
            :func:`~app.services.pay_calendar._eras.validate_cadence` where a
            calendar is built, as :class:`FixedDays`' count is.
    """

    day: int

    @property
    def shortest_gap(self) -> int:
        """Return the fewest days between two monthly paydays: one February.

        :data:`SHORTEST_MONTH_DAYS`, whatever the day (**R-PC79**): a day at
        or below 28 is exactly 28 days apart across a non-leap February, a
        day-31 owner's 31 January to 28 February is 28 too, and a day-29 or
        day-30 owner's true floor of 29 is stated as 28 rather than
        re-derived, since every collision floor the holiday set can produce
        sits far below either.

        Returns:
            28.
        """
        return SHORTEST_MONTH_DAYS

    @property
    def phrase(self) -> str:
        """Return how an owner would say this cadence: ``"monthly on day 15"``.

        Returns:
            The phrase, in :class:`FixedDays.phrase`'s grammar.
        """
        return f"monthly on day {self.day}"


@dataclass(frozen=True)
class SemiMonthly:
    """A cadence of two owner-chosen days of every month (ruling **R-PC79**).

    Paid on each of two days of the month -- 1st and 15th, 15th and last,
    5th and 20th -- each read as :class:`Monthly` reads its one day (29..31
    meaning "or the last day").  **The pair is SORTED, and that is what
    makes it a value**: an era stating "the 1st and the 15th" and one
    stating "the 15th and the 1st" are one rhythm, so
    ``pay_era_write.era_to_mint``'s equality must say so whichever day the
    era started on.  Which of the two days the era's ``effective_from``
    stands for is read off that date by the grid, not carried here.

    **A pair whose lower day is 28 or more is REFUSED** (at the write door
    and where a calendar is built): both days clamp onto 28 February and the
    owner would be paid once that month, which is not twice a month.

    Stored on ``budget.pay_eras`` as ``other_day`` -- the member the era's
    ``effective_from`` does NOT stand for -- with ``cadence_days`` absent
    (**R-PC80**), and ``nominal_day`` beside it only when the first month
    could not carry the day the anchor means.

    Attributes:
        days: The two days of the month, ascending, each 1..31 and distinct,
            the lower at most 27.  Bounded at the same two doors as
            :class:`Monthly.day`.  Normalised to ascending order at
            construction, so a caller may state the pair in either order;
            a pair that cannot be ordered at all (a string beside an int) is
            Python's own ``TypeError`` at construction, which is the
            contract :mod:`app.services.pay_calendar._grid` gives a value of
            no kind.
    """

    days: tuple[int, int]

    def __post_init__(self) -> None:
        """Order the pair ascending, so equal pairs compare equal."""
        object.__setattr__(self, "days", tuple(sorted(self.days)))

    def member_of(self, anchor: date) -> int:
        """Return which of the pair *anchor* stands for: ``0`` or ``1``.

        **The one reading of an anchor's POSITION in the pair**, asked by the
        grid (which numbers half-months from it) and by the era writer
        (which stores the OTHER member as ``other_day``): the lower day when
        the anchor falls on it -- the lower day is at most 27, so it is never
        clamped and the comparison is exact -- and the upper otherwise.  An
        anchor on neither reads as the upper; that state is refused before
        any grid function is asked (``pay_era_write.reject_phase_off_grid``,
        ``pay_calendar._derive.validate_eras``), and reading it as the upper
        is what makes ``nominal_payday(anchor, cadence, 0) != anchor`` there,
        which is exactly the predicate those refusals ask.  A parameter fact
        rather than date arithmetic: it compares one day number.

        Args:
            anchor: A day the grid passes through.

        Returns:
            The index into :attr:`days`.
        """
        return 0 if anchor.day == self.days[0] else 1

    @property
    def shortest_gap(self) -> int:
        """Return the fewest days between two semi-monthly paydays.

        The closed form ruling **R-PC79** states and checked against a
        brute-force walk over 2024-2027 with 0 disagreements: with the
        upper day clamped into February, the shorter of the gap from the
        lower day up to it and the gap from it round to the next month's
        lower day.  1st/15th is 14, 15th/last is 13 (15 February to 28
        February), 1st/last is 1 (28 February to 1 March).

        Returns:
            The day count.
        """
        lower, upper = self.days
        upper_in_february = min(upper, SHORTEST_MONTH_DAYS)
        return min(
            upper_in_february - lower,
            SHORTEST_MONTH_DAYS - upper_in_february + lower,
        )

    @property
    def phrase(self) -> str:
        """Return how an owner would say this cadence.

        Returns:
            ``"twice a month on days 1 and 15"``, in
            :class:`FixedDays.phrase`'s grammar.
        """
        lower, upper = self.days
        return f"twice a month on days {lower} and {upper}"


@dataclass(frozen=True)
class Rhythm:
    """How often an owner is paid, and what payroll does on a closed day.

    Plan step **C14-b**.  The pair ``budget.pay_eras`` stores as
    ``cadence_days`` and ``shift_id`` (``budget.pay_schedule`` did, until
    ``C17-a``).  Written through two statements the row passes through a
    state neither statement means, and either order refuses a legal request
    -- so :func:`~app.services.pay_era_write.mint_era` takes the pair,
    judges the pair, and writes the pair, and no caller is able to hand it half
    of one.

    **The halves acquired a SHARED consumer at plan step ``C14-e``, which is
    why this stopped being a writer's value.**  A
    :class:`~app.services.pay_calendar.PayCalendar` is derived from the cadence
    AND the convention -- from ``C14-e-3`` the projection is the nominal grid
    day displaced under it -- so a calendar carrying half the pair could be
    handed the other half from another owner's schedule, a mismatch that
    produces a plausible wrong payday rather than an error.  *An adversarial
    review struck a sentence claiming each half has ONE consumer and the same
    one: the cadence has ten and the convention two.  What is true is the
    narrower thing the argument needs -- they now share one.*  That is the same argument
    :class:`~app.services.pay_schedule_service.ScheduleFacts` makes for
    pairing the cadence with ``history_opens_on`` rather than resolving them
    separately.

    Attributes:
        cadence: How often, as a value of its own KIND (:class:`FixedDays`,
            :class:`Monthly` or :class:`SemiMonthly`).  Its parameters are
            bounded by the write door and the derivation, not by this type
            -- see the kind's own docstring.
        shift: What payroll does when a payday lands on a day no money moves
            on, as the :class:`~app.enums.BusinessDayShiftEnum` member rather
            than the ``ref.business_day_shifts`` id that spells it on the
            wire.  The id is what crosses a form and what the column holds, so
            :class:`~app.schemas.validation._pay_rhythm.BusinessDayShiftField`
            converts on the way in and
            :func:`~app.services.pay_era_write.mint_era`
            converts on the way out; between them the value is a member, which
            is what :func:`~app.utils.business_days.shift_to_business_day`
            requires -- it REFUSES an integer rather than defaulting, so a
            rhythm carrying an id would hand
            :func:`~app.services.pay_calendar.projected_payday` a raise
            instead of a payday.  This is IDs-for-logic as the project means
            it: no ``name`` string is ever compared.
    """

    cadence: "FixedDays | Monthly | SemiMonthly"
    shift: BusinessDayShiftEnum


@dataclass(frozen=True)
class Era:
    """One span of an owner's pay history: the rhythm, and since when.

    Plan step **pay_calendar:C17-a** (ruling **R-PC58**).  One row of
    ``budget.pay_eras`` as a value: the day the rhythm took effect and the
    rhythm itself.  Which days an era governs is not
    a field, because it is not a fact of the row -- an era governs from its
    ``effective_from`` up to the next era's, and the earliest one also runs
    backward below the record, bounded by the owner's stated history.

    **``effective_from`` is the grid's phase.**  It is the era's first NOMINAL
    payday, so by construction a day the grid passes through, and every
    producer that continues the rhythm steps from it.  ``budget.pay_schedule``
    used to carry that phase as ``nominal_anchor`` beside a cadence the same
    batch wrote; a second field here would have to agree with this one modulo
    the cadence, which is the maintenance contract rule 14 exists to delete.

    **The KIND is not a field, since plan step ``C17-d-1``.**  ``C17-a``
    carried ``kind: PayCadenceKindEnum`` here so that the day-of-month leaf
    would add members rather than a field; that leaf's design found the kind
    to be the cadence's TYPE (``rhythm.cadence`` is a :class:`FixedDays`, a
    :class:`Monthly` or a :class:`SemiMonthly`), so a field naming it beside
    the value was one fact in two homes.  ``budget.pay_eras`` stored it as
    ``kind_id`` until ``C17-d-2`` dropped the column with the vocabulary
    (ruling **R-PC80**): a stored era's kind is which parameter columns it
    carries, and ``pay_schedule_service._era_of`` reads the type off that.

    **``effective_from`` is a day the cadence's grid passes through, for
    every kind.**  A fixed-days grid passes through any anchor by
    construction; a day-of-month grid does not, so a :class:`Monthly` or
    :class:`SemiMonthly` era's phase must fall on its own stated day --
    ``pay_era_write.reject_phase_off_grid`` refuses one that does not at the
    write door, ``pay_calendar._derive.validate_eras`` refuses it where a
    calendar is built, and the storage cannot hold it at all (the anchor's
    own day IS the meant day unless ``nominal_day`` records the clamp).

    Attributes:
        effective_from: The era's first nominal payday.
        rhythm: The cadence and the payday convention (:class:`Rhythm`).
    """

    effective_from: date
    rhythm: Rhythm


def era_covering(eras: "tuple[Era, ...]", day: date) -> Era:
    """Return the era that governs *day*.

    **The rule an era's span is DERIVED by**, written once: an era governs
    from its ``effective_from`` up to the next era's, so the era covering a
    day is the LATEST one taking effect on or before it -- and a day before
    every era is the EARLIEST era's, because that era alone runs backward
    below the record (bounded there by the owner's stated history, which is
    the calendar's question and not this function's).

    **Read in NOMINAL days, which is the writer's coordinate**: a batch states
    its first payday on the grid, and the era-mint decision asks which era's
    ``effective_from`` bounds that day.  The calendar's readers ask the same
    rule of a CASH day -- a recorded payday, a day to project for -- through
    ``pay_calendar.era_index_at``, whose bound is each era's first payday
    DISPLACED; the two part only where an era's first payday moved.

    A linear scan rather than a bisection: an owner holds a handful of eras,
    and this is asked once per batch and never per row.

    Args:
        eras: The owner's eras, ``effective_from`` ascending and NON-EMPTY --
            a caller holding a :class:`~app.services.pay_schedule_service.ScheduleFacts`
            holds at least one.
        day: The day to place.

    Returns:
        The governing :class:`Era`.
    """
    covering = eras[0]
    for era in eras:
        if era.effective_from > day:
            break
        covering = era
    return covering
