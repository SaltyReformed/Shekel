"""A VIEW over one pay calendar: the periods a surface reports on.

Plan step **C2-a** built this type; plan step **C2-c** gave it its first
consumer, its two invariants and its own module (see :mod:`._searches` for why
the split fell where it did).

**A window is not a calendar, and that distinction is the fix for ledger row
P14.**  A period's end is its successor's payday, so deriving a calendar from a
SLICE gives that slice's last row a cadence-projected end instead of a
fact-dictated one -- the same period reporting two different ends depending on
which window asked.  The sibling shape is measured at ``$150,000.00``
(:func:`~._derive.derive_periods`).  A window carries the ends the WHOLE
calendar computed, and no constructor in this package accepts one.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no database session, no
clock.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta

from ._derive import DerivedPeriod, PayCalendarError
from ._searches import _BY_START_DATE, containing_index, containing_period


@dataclass(frozen=True)
class PeriodWindow:
    """A contiguous SLICE of one calendar, carrying the ends that calendar derived.

    **Not a calendar, and the type distinction is the fix for ledger row P14.**
    ``PeriodCalendar.from_pay_periods`` and ``_cash_periods._PeriodSpans.of``
    both accepted an arbitrary list, so deriving over a six-period grid window
    gave that window's last period a cadence-projected end while the same
    period ended a day earlier everywhere else.  **In ``app/``** a window is
    only ever produced by :meth:`~._calendar.PayCalendar.saved`,
    :meth:`~._calendar.PayCalendar.window`,
    :meth:`~._calendar.PayCalendar.overlapping` or
    :meth:`~._calendar.PayCalendar.axis`, each of which views a calendar that
    was built from a complete payday set.  The scope is stated because the
    TEST suite constructs one directly, which is the only way the refusal
    below can be shown firing at all.

    **What this type guarantees, and the claim it does NOT make** (ledger row
    **P24**).  An earlier draft said a window "cannot be derived FROM", and
    that was false: ``PayCalendar.from_paydays([(p.period_id, p.start_date)
    for p in window], ...)`` is one line over the public :meth:`__iter__`.
    What is true, and is what the type is for, is that no CONSTRUCTOR ACCEPTS a
    window -- so no producer can be handed a slice and mistake it for a
    complete payday set, which is the mechanism P14 names.

    **The two window invariants are enforced HERE, and they are enforced
    differently on purpose** (ledger rows **P24**, **P32**):

    * **ORDER is DERIVED, never asserted.**  :meth:`__post_init__` sorts, so a
      caller cannot state an order at all and there is nothing to get wrong.
      It was a precondition until then, and an unsorted window answered
      :meth:`containing` wrongly and SILENTLY, because that search bisects:
      given two periods supplied newest-first it missed the day the first one
      covers and answered correctly for the second by accident.  A window's
      identity is its period SET, so canonicalising the order loses nothing --
      the same argument :meth:`~._calendar.PayCalendar.__post_init__` makes for rewriting
      its paydays off the derivation.
    * **CONTIGUITY is CHECKED**, because it is a property of the input rather
      than something a constructor can derive.  A gapped window is not merely
      untidy: the balance column a reader scans down does not telescope across
      the hole, and the per-period reconciliation identity that column rests on
      values each period at its OWN boundaries, so nothing on screen explains
      the step.  ``cash_period_view``'s contract used to PERMIT it in as many
      words ("they need not be contiguous"), which is row **P32**.

      **Three of the four views cannot produce one and the fourth can, and an
      earlier draft of this paragraph said all four could not.**
      :meth:`~._calendar.PayCalendar.window`,
      :meth:`~._calendar.PayCalendar.overlapping` and
      :meth:`~._calendar.PayCalendar.axis` SLICE a tiling, and a slice of a
      tiling tiles.  :meth:`~._calendar.PayCalendar.saved` FILTERS it, and a
      filter does not: an UNSAVED candidate payday between two saved ones
      leaves exactly this hole, which is why that method documents the refusal
      as one of its own outcomes.  No such calendar exists today --
      :func:`~._loader.calendar_for` reads saved rows only, and
      ``pay_period_write`` appends its candidates after the last saved payday
      -- but plan step **C6** inserts a payday MID-SCHEDULE by design, so the
      refusal is a live guard on that day rather than a control for a fifth
      producer.

    Attributes:
        periods: The sliced periods, ``start_date`` ascending and covering an
            unbroken span.  May be empty -- a window over a range the calendar
            does not reach is a real answer, not an error.
    """

    periods: "tuple[DerivedPeriod, ...]"

    def __post_init__(self) -> None:
        """Sort the periods, then refuse a slice with a hole in it.

        ``object.__setattr__`` because the dataclass is frozen: the ordering is
        written exactly here, at construction, and nothing can rewrite it
        afterwards.

        Raises:
            PayCalendarError: Two adjacent periods do not meet -- the later one
                opens more than a day after the earlier one ends (a hole), or
                on or before the day it ends (an overlap, which the derivation
                cannot produce and which would double-count a day).
        """
        ordered = tuple(sorted(self.periods, key=_BY_START_DATE))
        for earlier, later in zip(ordered, ordered[1:]):
            if later.start_date != earlier.end_date + timedelta(days=1):
                raise PayCalendarError(
                    f"a pay-period window must cover an unbroken span: the "
                    f"period opening {earlier.start_date.isoformat()} ends "
                    f"{earlier.end_date.isoformat()} and the next one opens "
                    f"{later.start_date.isoformat()}, leaving "
                    f"{(later.start_date - earlier.end_date).days - 1} day(s) "
                    f"in no column.  A window is a VIEW over one derived "
                    f"calendar, whose periods tile, so every slice of one is "
                    f"contiguous by construction -- reaching this means a "
                    f"window was assembled from somewhere else."
                )
        object.__setattr__(self, "periods", ordered)

    def containing(self, day: date) -> "DerivedPeriod | None":
        """Return the period of this window whose span covers *day*, else ``None``.

        Scoped to the window ON PURPOSE, and that is a question in its own
        right rather than a weaker form of the calendar's: the cash period
        view's reconciliation identity reads a period's balance change as the
        steps inside its own span, so a day outside the REPORTED columns
        belongs to no column and must not be pulled into the nearest one.

        Args:
            day: The calendar day to place.

        Returns:
            The containing :class:`~._derive.DerivedPeriod`, or ``None`` when
            *day* falls outside every period in this window.
        """
        return containing_period(self.periods, day)

    def containing_index(self, day: date) -> "int | None":
        """Return WHERE in this window the period covering *day* sits, else ``None``.

        The same question :meth:`containing` answers, asked by a consumer that
        plots one thing per period and needs to mark the right one: the answer
        is an index into THIS view, in the same ordinal
        :meth:`__getitem__` takes, so ``window[window.containing_index(d)]`` is
        ``window.containing(d)`` whenever either answers.

        **Its consumer is the /investment growth chart** (plan step C2-f2c).
        That module scanned the window period by period for the one holding the
        planned retirement date, which is this predicate written a second time
        and the last HAND-ROLLED member of ledger row **P6**'s census.  The
        scan and this agree over a tiling window, so retiring it removed a
        duplicate rather than a divergence.  **The census's LAST survivor of
        any kind, ``pay_period_service.get_current_period``, is DELETED at plan
        step C2-f3a** -- it was SQL rather than a scan, and its ``.first()``
        carried no ``ORDER BY`` (ledger row **P19**), so over two periods
        covering one day it answered whichever row the planner reached first.
        Row **P6**'s CENSUS is empty -- every "which pay period contains this
        date" in ``app/`` is now one of the searches in :mod:`._searches` --
        though the ROW is open until the ``C2`` container it is owned by
        ticks.

        **Not derived from ``period_index``.**  A window is a slice of a tiling
        calendar, so ``found.period_index - self[0].period_index`` gives the
        same number today -- but that is a second rule about how a view's
        ordinals relate to the calendar's, stated at the caller and enforced
        nowhere, where this is the bisect :meth:`containing` already runs.

        Args:
            day: The calendar day to place.

        Returns:
            The 0-based position within this window, or ``None`` when *day*
            falls outside every period in it.
        """
        return containing_index(self.periods, day)

    def __iter__(self) -> "Iterator[DerivedPeriod]":
        """Iterate the window's periods in ``start_date`` order.

        Returns:
            An iterator over the periods.
        """
        return iter(self.periods)

    def __len__(self) -> int:
        """Return how many periods the window holds.

        Returns:
            The period count.
        """
        return len(self.periods)

    def __getitem__(self, index: int) -> DerivedPeriod:
        """Return the period at ordinal *index* within this window.

        Completes the sequence protocol :meth:`__iter__` and :meth:`__len__`
        already half-state, so a consumer that needs this window's first period
        or its i-th plotted point says so directly instead of reaching through
        to :attr:`periods` -- which is the one move that lets a run of periods
        escape the type guaranteeing their order and their tiling.  **The index
        is the window's own ordinal, not the calendar's** ``period_index``: a
        window is a VIEW, and ``[0]`` is where this view starts rather than
        where the schedule does.

        **SLICING is deliberately not supported.**  A first cut returned a
        :class:`PeriodWindow` for a slice and let :meth:`__post_init__` refuse
        the stepped ones; two adversarial reviews of plan step C2-e landed on
        it, and both were right.  Nothing in ``app/`` slices a window
        (``CLAUDE.md`` rule 13), and the branch shipped a claim it did not
        keep: ``window[::-1]`` tiles, so it passed the contiguity check and came
        back silently RE-SORTED into payday order rather than reversed.  A
        caller wanting the whole window backwards writes ``reversed(window)``,
        which this method and :meth:`__len__` already provide.

        Args:
            index: A 0-based position within this window.

        Returns:
            The :class:`~._derive.DerivedPeriod` at that position.

        Raises:
            IndexError: *index* is outside the window.
            TypeError: *index* is a slice -- see above.
        """
        if isinstance(index, slice):
            raise TypeError(
                "a PeriodWindow cannot be sliced: no consumer in app/ needs "
                "one, and a slice that tiles (window[::-1]) would come back "
                "re-sorted into payday order rather than reversed, which is a "
                "wrong answer given silently.  reversed(window) walks it "
                "backwards; PayCalendar.window() takes an ordinal range."
            )
        return self.periods[index]
