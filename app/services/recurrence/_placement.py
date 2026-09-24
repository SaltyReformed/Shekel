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
:func:`projected_occurrence_placements` over the owner's projected rhythm),
beside the saved walk split by the definition's books
(:func:`occurrence_walk`, plan step ``pay_calendar:C18-a``).

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
from app.utils.books_boundary import books_hold


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


@dataclass(frozen=True)
class BooksWalk:
    """One saved walk of a recurrence, split by where its definition's books open.

    **The books decide which occurrences become rows, never when the rule
    ends** (plan step ``pay_calendar:C18-a``, ruling **R-PC94**).  The walk
    spends a count bound on every occurrence it names, one on or before the
    books included, because that occurrence HAPPENED -- its money is inside
    the opening balance.  So a closing judged on :attr:`kept` alone never saw
    an "after N times" rule finish once the books dropped one of its N, and
    the rule stayed in the monthly totals for good.  The closing reads both
    halves (:meth:`~._reading.RuleReading.bound_reading`), which together
    are every occurrence the rule names through the horizon under its
    closing -- for an AUTHORED closing, the set it read before the books
    bound existed.  A loan payment's DERIVED stop is its loan's payoff,
    which the loan estimate computes over the same floored walk (ruling
    **R-PC85**), so where the books drop an occurrence that estimate would
    otherwise count, the stop -- and with it the set -- can differ (reasoned
    by the C18-a round-3 review's L6; not measured).

    Attributes:
        kept: The occurrences whose rows land where the app keeps books,
            what :func:`occurrence_placements` answers.
        below_the_books: The ones the books drop, what
            :func:`placements_below_the_books` answers.  Both halves are
            ascending by date and disjoint.
    """

    kept: tuple[OccurrencePlacement, ...]
    below_the_books: tuple[OccurrencePlacement, ...]


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
        One :class:`OccurrencePlacement` per occurrence, ascending by date --
        less any whose row would land on or before the definition's books
        (:func:`_lands_inside_the_books`, plan step ``pay_calendar:C18-a``).
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
    """Walk *resolved* through *through*, place each occurrence, keep the books' half.

    The one composition behind :func:`occurrence_placements` and
    :func:`projected_occurrence_placements`, which differ only in the search
    they hand in: :func:`_by_the_books`' ``kept`` half of :func:`_walk`, so
    the two walks -- the saved one generation and the screens read, the
    projected one the loan estimate prices -- cannot disagree about which
    occurrences precede the books.

    Args:
        resolved: The recurrence's two-axis meaning.
        calendar: The owner's pay-period schedule.
        search: The placement search, saved or projecting.
        through: The last day to generate through; ``None`` means the saved
            schedule's horizon.

    Returns:
        One :class:`OccurrencePlacement` per occurrence the books admit,
        ascending by date.
    """
    return _by_the_books(
        resolved, _walk(resolved, calendar, search, through=through),
    ).kept


def _walk(
    resolved: ResolvedRecurrence,
    calendar: PayCalendar,
    search: "Callable[[date], DerivedPeriod | None]",
    *,
    through: date | None,
) -> tuple[OccurrencePlacement, ...]:
    """Return every occurrence *resolved* NAMES through *through*, placed with *search*.

    Before the books: the closing and a count bound are applied by
    :func:`occurrences`, the books by :func:`_by_the_books` over this.
    Refuses before the empty-schedule short-circuit, so every caller refuses
    exactly what :func:`occurrences` and :func:`place` refuse.

    Args:
        resolved: The recurrence's two-axis meaning.
        calendar: The owner's pay-period schedule.
        search: The placement search, saved or projecting.
        through: The last day to generate through; ``None`` means the saved
            schedule's horizon.

    Returns:
        One :class:`OccurrencePlacement` per named occurrence, ascending by
        date; empty for a schedule with no periods.
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


def _by_the_books(
    resolved: ResolvedRecurrence, named: tuple[OccurrencePlacement, ...],
) -> BooksWalk:
    """Split *named* by :func:`_lands_inside_the_books`.

    **The books bound is applied HERE and only here** (plan step
    ``pay_calendar:C18-a``, ruling **R-PC85**): every walk that keeps rows
    and every question about what the books drop is one of these two halves.

    Args:
        resolved: The recurrence, carrying its books floor.
        named: Every occurrence it names, placed (:func:`_walk`).

    Returns:
        The :class:`BooksWalk`, each half in *named*'s order.
    """
    kept = []
    below_the_books = []
    for placement in named:
        if _lands_inside_the_books(resolved, placement):
            kept.append(placement)
        else:
            below_the_books.append(placement)
    return BooksWalk(kept=tuple(kept), below_the_books=tuple(below_the_books))


def _lands_inside_the_books(
    resolved: ResolvedRecurrence, placement: OccurrencePlacement,
) -> bool:
    """Return whether *placement*'s row would land where the app keeps books.

    **The books bound, applied by :func:`_by_the_books` alone** (plan step
    ``pay_calendar:C18-a``, rulings **R-PC85** and **R-PC86**).  A rule says
    when it fires; the ACCOUNT says where the app keeps books, and money on
    or before an account's opening day is already inside its opening equity
    (ruling **R-HG**).  So an occurrence whose row would land on or before
    :attr:`~._resolution.ResolvedRecurrence.books_opened_on` -- the latest
    opening across every account the definition moves money in -- is not a
    ROW the app models, and neither walk keeps it: generation cannot write
    it, the loan estimate cannot price it, and a screen cannot list it.  The
    rule's CLOSING still counts it (ruling **R-PC94**, :class:`BooksWalk`):
    it happened.  Until this bound, the owner's first payday stood in for it,
    because an occurrence with no paycheck is skipped; a paycheck recorded
    below the books let the rules fill it, measured on a production clone at
    ``$531.94`` + ``$100.00`` dated before Checking's opening.

    **The day compared is a ROW's, not the occurrence's**, and which of the
    row's days is :meth:`~._resolution.ResolvedRecurrence.books_day`'s one
    answer (:func:`~app.utils.books_boundary.row_books_day`): for a bill its
    cash day (R-PC86), :meth:`~._resolution.ResolvedRecurrence.row_date`, the
    day ``compute_due_date`` stamps on the written row -- so a bill scheduled
    before the books but due after them is kept, and one due ON the opening
    day is not; for an ENVELOPE its paycheck's last day (ruling **R-PC89**),
    because its money is spent across the paycheck, so the envelope of the
    paycheck the books open inside is kept.  Compared through the one strict
    :func:`~app.utils.books_boundary.books_hold`.  The doors that refuse to
    strand a still-projected row below the books
    (``app.services.planned_rows_books``) read the other half of the same
    split, :func:`occurrence_walk`'s ``below_the_books``
    (``definition_unarchive.books_reading``) -- so each refuses exactly the
    occurrences this stops keeping.

    **An UNPLACED occurrence is kept**, because it has no row day to compare
    and no row: ``period`` is ``None`` only below the owner's first payday
    under ``CONTAINING_DATE`` or past the saved horizon, and no reader writes
    or estimates such an occurrence (ruling **R-R64**), though the rule's
    CLOSING counts it, as it counts every occurrence the rule names (ruling
    **R-PC94**) -- keeping it is the walk's answer before this bound,
    unchanged.  A value with no floor
    (``None``: the pure resolver's, or a definition moving money in no
    account) is unbounded, as it always was.

    Args:
        resolved: The recurrence, carrying its books floor.
        placement: One occurrence and the period it lands in.

    Returns:
        ``True`` when the placement stays in the walk.
    """
    floor = resolved.books_opened_on
    if floor is None or placement.period is None:
        return True
    return books_hold(
        floor, resolved.books_day(placement.occurrence, placement.period),
    )


def placements_below_the_books(
    resolved: ResolvedRecurrence,
    calendar: PayCalendar,
) -> tuple[OccurrencePlacement, ...]:
    """Return the occurrences the books drop from *resolved*'s saved walk.

    **What the books bound removes, asked of the walk itself** (plan step
    ``pay_calendar:C18-a``, rulings **R-PC88**, **R-PC90** and **R-PC91**):
    :func:`occurrence_walk`'s ``below_the_books`` half, so these are exactly
    the occurrences the rule still names that the walk stops keeping because
    of the books -- ONE walk, ONE comparison, and no second spelling of
    either.  The maintain pass matches a row to the occurrence it answers
    (``occurs_on``), so a live row answering one of these is a row the next
    pass to reach it retires.  The doors that refuse to strand an unpaid row
    read the same half off :func:`occurrence_walk` itself
    (``definition_unarchive.books_reading``, one walk per check since plan
    step ``pay_calendar:C18-a``'s ruling **R-PC98**) rather than the row's
    stored due day, which the save's regeneration re-dates by the NEW rule (a
    cleared due day moves a bill's cash day onto its scheduled day, inside
    the books); this function states the half on its own.

    The closing is kept as *resolved* carries it, so an occurrence the
    closing stops is named by neither half and never reported here.

    Args:
        resolved: The recurrence, carrying its books floor
            (:attr:`~._resolution.ResolvedRecurrence.books_opened_on`) --
            the floor the save being graded would leave.
        calendar: The owner's pay-period schedule.

    Returns:
        One :class:`OccurrencePlacement` per dropped occurrence, ascending by
        date, each on the saved period its row would live in (an unplaced
        occurrence is never dropped); empty when *resolved* has no floor.

    Raises:
        RecurrenceGenerationError: See :func:`occurrence_placements`.
    """
    if resolved.books_opened_on is None:
        return ()
    return occurrence_walk(resolved, calendar).below_the_books


def occurrence_walk(
    resolved: ResolvedRecurrence, calendar: PayCalendar,
) -> BooksWalk:
    """Return *resolved*'s SAVED walk through the horizon, split by its books.

    One walk for a reader that needs both halves: the read pass's memo
    (``BalanceContext.placements_of``), whose rule reading keeps the rows'
    half and hands the closing both (ruling **R-PC94**, :class:`BooksWalk`).
    Its ``kept`` half is :func:`occurrence_placements`' answer for the same
    inputs, and its ``below_the_books`` half
    :func:`placements_below_the_books`'.

    Args:
        resolved: The recurrence, carrying its books floor.
        calendar: The owner's pay-period schedule.

    Returns:
        The :class:`BooksWalk`; both halves empty for a schedule with no
        periods.

    Raises:
        RecurrenceGenerationError: See :func:`occurrence_placements`.
    """
    return _by_the_books(
        resolved,
        _walk(
            resolved, calendar, _placement_search(calendar, resolved.placement),
            through=None,
        ),
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
    placed occurrence, ruling **R-R69**) -- so the loan's payoff cannot move when
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
        opening bound under ``CONTAINING_DATE``) -- less any whose row would
        land on or before the definition's books, the bound the saved walk
        applies through the same composition (plan step
        ``pay_calendar:C18-a``).  Empty for a schedule with no periods.

    Raises:
        RecurrenceGenerationError: See :func:`occurrence_placements`.
    """
    return _placements(
        resolved, calendar, _span_search(calendar, resolved.placement),
        through=through,
    )


__all__ = [
    "BooksWalk",
    "OccurrencePlacement",
    "occurrence_placements",
    "occurrence_walk",
    "place",
    "placements_below_the_books",
    "projected_occurrence_placements",
]
