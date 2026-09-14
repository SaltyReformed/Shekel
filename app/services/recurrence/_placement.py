"""The PLACEMENT half of the occurrence model: an occurrence DATE onto a pay PERIOD.

**Split out of** :mod:`._occurrence` **at plan step salary:R15-a** as a PURE
MOVE, when the per-month ceiling took that module past pylint's 1000-line
ceiling; every definition below is byte-for-byte what it held, graded by AST
in the same commit.  That module's docstring still carries the whole model --
why generation is stated forward, what an occurrence IS per unit, why
placement is inert under the ``PERIOD`` unit, the one answer ``period=None``
gives, and why several occurrences in one paycheck are reported rather than
collapsed -- and it is not restated here.  ``_occurrence`` keeps the WALK
(:func:`~._occurrence.occurrences`); this leaf keeps what carries each date
it yields onto the period a row lives in: the two searches a placement names
(:func:`_searches`), :func:`place`, and the two compositions every reader
takes (:func:`occurrence_placements` over the SAVED schedule,
:func:`projected_occurrence_placements` over the owner's projected rhythm).

**The dependency runs ONE way**: this module imports the walk and nothing in
:mod:`._occurrence` imports this.

Pure: no Flask, no ORM, no clock, no database.
"""
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from app.enums import PeriodPlacementEnum
from app.services.pay_calendar import (
    DerivedPeriod,
    PayCalendar,
    span_starting_on_or_after,
)
from app.services.recurrence._occurrence import (
    RecurrenceGenerationError,
    _require_generable,
    occurrences,
)
from app.services.recurrence._resolution import ResolvedRecurrence


@dataclass(frozen=True)
class OccurrencePlacement:
    """One occurrence of a rule, and the pay period it lands in.

    **Two fields, because there is one fact to state.**  This carried a third
    until plan step C2-b2 -- a ``PlacementOutcome`` naming WHICH of two "no
    period" answers a ``None`` was, with a ``__post_init__`` refusing a value
    whose two fields disagreed.  The derived calendar tiles its covered span,
    so the SCHEDULE GAP half of that distinction stopped being constructible
    and the remaining member said only what :attr:`period` already said.  A
    fact stated twice needs a reconciler; a fact stated once does not, so the
    check went with the field rather than being kept passing.

    Attributes:
        occurrence: The date the cadence names.  For the ``PERIOD`` unit this
            is the paycheck's own payday; see the module docstring.
        period: The pay period the row lives in, or ``None`` when the SAVED
            schedule does not reach this occurrence.  ``None`` is a real
            answer, not an error, and it is ORDINARY -- see the module
            docstring for why it is no longer an operator signal.
    """

    occurrence: date
    period: DerivedPeriod | None


def _searches(
    calendar: PayCalendar, placement: PeriodPlacementEnum,
) -> "tuple[Callable[[date], DerivedPeriod | None], Callable[[date], DerivedPeriod | None]]":
    """Return the ``(saved, projecting)`` searches *placement* names, refusing an unknown one.

    ONE table from placement to search, since plan step R16-b-2's adversarial
    review found two -- a saved one and a projecting one -- that a placement
    added to one and not the other would have split.  Each rule names its
    saved search (the schedule's own, answering ``None`` past the horizon,
    which is what generation writes against) beside its TOTAL twin (the
    calendar's span search, projecting at the owner's cadence, which the
    balance seam's estimate places on).

    Resolved ONCE per composition rather than per occurrence, which is also
    what makes an unrecognised placement an eager refusal instead of one that
    waits for an occurrence to exist.

    Args:
        calendar: The owner's pay-period schedule, which owns both searches.
        placement: Which placement rule the recurrence uses.

    Returns:
        The bound saved search and the bound projecting search.

    Raises:
        RecurrenceGenerationError: When *placement* is not a member this
            engine has a rule for.
    """
    if placement is PeriodPlacementEnum.CONTAINING_DATE:
        return calendar.period_containing, calendar.span_containing
    if placement is PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER:
        return (
            calendar.period_starting_on_or_after,
            lambda day: span_starting_on_or_after(calendar, day),
        )
    raise RecurrenceGenerationError(
        f"period placement {placement!r} has no rule.  Every member of "
        f"PeriodPlacementEnum must map an occurrence onto a period; "
        f"answering None instead would read as a schedule that cannot host "
        f"the row."
    )


def _placement_search(
    calendar: PayCalendar, placement: PeriodPlacementEnum,
) -> "Callable[[date], DerivedPeriod | None]":
    """Return the SAVED search *placement* names (:func:`_searches`' first half)."""
    return _searches(calendar, placement)[0]


def _span_search(
    calendar: PayCalendar, placement: PeriodPlacementEnum,
) -> "Callable[[date], DerivedPeriod | None]":
    """Return the PROJECTING search *placement* names (:func:`_searches`' second half)."""
    return _searches(calendar, placement)[1]


def place(
    occurrence: date,
    calendar: PayCalendar,
    placement: PeriodPlacementEnum,
) -> DerivedPeriod | None:
    """Return the pay period *occurrence* belongs in under *placement*.

    The placement half of the model: an occurrence is a calendar DATE and a
    Shekel row lives in a pay PERIOD, and this is the rule that carries one to
    the other.  Both branches bisect the schedule
    (:class:`~app.services.pay_calendar.PayCalendar`), which owns the search
    because "which period covers this day" is a question about the schedule.

    Args:
        occurrence: The date to place.
        calendar: The owner's pay-period schedule.
        placement: Which placement rule the recurrence uses.

    Returns:
        The :class:`~app.services.pay_calendar.DerivedPeriod` the row lives in,
        or ``None`` when the SAVED schedule holds no such period -- a date
        before it opens, or past its horizon.  Since plan step C2-b2 a date in
        a HOLE is not a third case: derived periods tile their covered span.

    Raises:
        RecurrenceGenerationError: When *placement* is a value this engine has
            no rule for.
    """
    return _placement_search(calendar, placement)(occurrence)


def occurrence_placements(
    resolved: ResolvedRecurrence,
    calendar: PayCalendar,
    *,
    through: date | None = None,
) -> tuple[OccurrencePlacement, ...]:
    """Return every occurrence in the window, paired with its pay period.

    The composition every reader of the table answers from, through
    :func:`~app.services.recurrence.rule_occurrences`: one forward walk, one
    placement per occurrence, and the pairs reported as generated.  Plan step
    R4a routed period selection here; plan step R4b-2 moved generation itself
    onto the pairs.  Materialised rather than lazy because every caller reads
    the result more than once.

    **Duplicated periods are reported, not collapsed.**  At a cadence longer
    than a month several occurrences legitimately land in one paycheck, and
    which row the user then owes is a generation decision -- see the module
    docstring.

    **An unplaced occurrence needs no reason field, since plan step C2-b2.**
    This paired every placement with a ``PlacementOutcome`` while ``None`` was
    two answers -- a schedule HOLE against "the schedule has not got there yet"
    -- and derived periods tile their covered span, so the first is
    unconstructible and the second is what ``period is None`` means.  The
    branch that told them apart, and the enum it wrote into, went with the
    state they described.

    Args:
        resolved: The recurrence's two-axis meaning.
        calendar: The owner's pay-period schedule.
        through: The last day to generate through.  ``None`` (the default)
            means the schedule's horizon, which is the last day a placement
            can succeed at all; pass a later date to see the occurrences
            beyond it, each carrying ``period=None``.

    Returns:
        One :class:`OccurrencePlacement` per occurrence, ascending by date.
        Empty for a schedule with no periods, where nothing can be placed and
        no window can be stated.

    Raises:
        RecurrenceGenerationError: See :func:`_require_generable` and
            :func:`_placement_search`.  Both run BEFORE the empty-schedule
            short-circuit, so this function refuses exactly what
            :func:`occurrences` and :func:`place` refuse rather than answering
            ``()`` over a value they would reject.
    """
    return _placements(
        resolved, calendar, _placement_search(calendar, resolved.placement),
        through=through,
    )


def _placements(
    resolved: ResolvedRecurrence,
    calendar: PayCalendar,
    search: "Callable[[date], DerivedPeriod | None]",
    *,
    through: date | None,
) -> tuple[OccurrencePlacement, ...]:
    """Walk *resolved* through *through* and place each occurrence with *search*.

    The one composition behind :func:`occurrence_placements` and
    :func:`projected_occurrence_placements`, which differ only in the search
    they hand in.  Refuses before the empty-schedule short-circuit, so both
    callers refuse exactly what :func:`occurrences` and :func:`place` refuse.

    Args:
        resolved: The recurrence's two-axis meaning.
        calendar: The owner's pay-period schedule.
        search: The placement search, saved or projecting.
        through: The last day to generate through; ``None`` means the saved
            schedule's horizon.

    Returns:
        One :class:`OccurrencePlacement` per occurrence, ascending by date.
    """
    _require_generable(resolved)
    horizon = calendar.horizon()
    if horizon is None:
        return ()
    window_end = horizon if through is None else through
    return tuple(
        OccurrencePlacement(occurrence=occurrence, period=search(occurrence))
        for occurrence in occurrences(resolved, calendar, through=window_end)
    )


def projected_occurrence_placements(
    resolved: ResolvedRecurrence,
    calendar: PayCalendar,
    *,
    through: date,
) -> tuple[OccurrencePlacement, ...]:
    """Return every occurrence through *through*, placed on a SAVED OR PROJECTED paycheck.

    :func:`occurrence_placements` for a reader that needs to know where a row
    WOULD live rather than where one can be written: the balance seam's
    ESTIMATED loan tier (plan step **R16-b-2**), which prices every occurrence
    a definition names that no row answers, and dates it exactly as the row
    would be dated (:func:`~app.services.recurrence.compute_due_date` over the
    placed period, ruling **R-R69**) -- so the loan's payoff cannot move when
    generation later writes that row.  Past the horizon the saved search
    answers ``None`` and generation stops; this keeps placing at the owner's
    cadence (:meth:`~app.services.pay_calendar.PayCalendar.span_containing`,
    :func:`~app.services.pay_calendar.span_starting_on_or_after`), and a
    projected period carries ``period_id = None`` so nothing can write against
    it by mistake.

    ``period`` is ``None`` for exactly one reason here: the occurrence falls
    BEFORE the owner's first payday under ``CONTAINING_DATE``, where nothing is
    projected backwards (the 2026-08-10 ruling).  That is the boundary ruling
    **R-R64** carries: an occurrence the schedule cannot place is neither
    generated nor estimated.  Under ``PERIOD_STARTING_ON_OR_AFTER`` such an
    occurrence places on the FIRST paycheck, which is also what generation
    does with it.

    *through* is required, and deliberately has no default: the saved horizon
    is a materialisation boundary, and a caller projecting past it must say
    how far.

    Args:
        resolved: The recurrence's two-axis meaning.
        calendar: The owner's pay-period schedule.
        through: The last day to generate through.

    Returns:
        One :class:`OccurrencePlacement` per occurrence, ascending by date,
        each placed on a saved or projected paycheck (``None`` only before the
        opening bound under ``CONTAINING_DATE``).  Empty for a schedule with no
        periods.

    Raises:
        RecurrenceGenerationError: See :func:`occurrence_placements`.
    """
    return _placements(
        resolved, calendar, _span_search(calendar, resolved.placement),
        through=through,
    )


__all__ = [
    "OccurrencePlacement",
    "occurrence_placements",
    "place",
    "projected_occurrence_placements",
]
