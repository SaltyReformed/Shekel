"""The searches every pay-calendar answer is built from, written ONCE.

Plan step **C2-a** built these beside :class:`~._calendar.PayCalendar`; plan
step **C2-c** moved them into their own module when that one passed the
1,000-line ceiling.  Growing past a gate is a signal rather than a nuisance,
and the seam the ceiling was measuring is the one this package exists to draw:
a SEARCH over an ordered period tuple is a primitive, a WINDOW
(:mod:`._window`) is a view, and a CALENDAR (:mod:`._calendar`) is the owner's
whole schedule.  The dependency runs one way -- both of the others import this
and this imports neither -- so no two of them can answer one question
differently.

**Why they are free functions rather than methods.**  Each is shared by the
calendar and by a view over it, and the whole point of plan step C2 is that six
copies of "which pay period contains this date" already disagreed at exactly
the edges that matter (ledger row **P6**).  A primitive defined once, keyed on
one field, is what makes a view and the calendar it came from incapable of
disagreeing.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no database session, no
clock.  Every answer is a pure function of the periods a caller supplies.
"""

from bisect import bisect_left, bisect_right
from datetime import date
from operator import attrgetter

from ._derive import DerivedPeriod

#: The bisect key for every search here: a period's opening payday.  Module
#: level so no two searches can key on different fields -- which is one of the
#: ways the six implementations row P6 counts came to disagree.
_BY_START_DATE = attrgetter("start_date")


def containing_index(
    periods: "tuple[DerivedPeriod, ...]", day: date,
) -> "int | None":
    """Return the POSITION in *periods* of the period covering *day*, else ``None``.

    **The single containment search.**  :func:`containing_period` is this plus
    an index, and both :class:`PayCalendar` and :class:`PeriodWindow` reach one
    of the two -- so the calendar, a view over it, and a consumer that needs to
    know WHERE in a view the answer sits cannot disagree about which period
    covers a day.  That was the whole point of plan step C2: six copies of this
    predicate already did (ledger row **P6**).

    Periods never overlap (they are derived from a set of distinct sorted
    paydays), so the latest period STARTING on or before *day* is the only
    candidate that can contain it and one bisect answers.

    **The POSITION is what plan step C2-f2c needed**, and it is here rather
    than expressed as arithmetic on :attr:`~._derive.DerivedPeriod.period_index`
    at the caller.  ``investment_dashboard_service._chart`` plots one point per
    period of a projection window and marks the one holding the planned
    retirement date, so what it needs is an offset INTO THAT VIEW; deriving it
    as ``found.period_index - window[0].period_index`` would be a second rule
    about how a window's ordinals relate to the calendar's, true today and
    unenforced, where this is the same bisect the containment answer already
    ran.  The scan it replaced was the last HAND-ROLLED member of row P6's
    census; ``pay_period_service.get_current_period`` is still live at every
    surface outside the read passes, and plan step **C2-f3** retires it.

    Args:
        periods: Periods in ``start_date`` ascending order.
        day: The calendar day to place.

    Returns:
        The 0-based position of the containing period, or ``None`` when *day*
        falls in a hole, before the first period, or after the last one's end.
    """
    index = bisect_right(periods, day, key=_BY_START_DATE) - 1
    if index < 0:
        return None
    return index if day <= periods[index].end_date else None


def containing_period(
    periods: "tuple[DerivedPeriod, ...]", day: date,
) -> "DerivedPeriod | None":
    """Return the period of *periods* whose span covers *day*, else ``None``.

    :func:`containing_index` resolved to the period it names, so the two
    answers come from one bisect and one end-date test rather than from two
    copies of them.

    Args:
        periods: Periods in ``start_date`` ascending order.
        day: The calendar day to place.

    Returns:
        The containing :class:`~._derive.DerivedPeriod`, or ``None`` when *day*
        falls in a hole, before the first period, or after the last one's end.
    """
    index = containing_index(periods, day)
    return None if index is None else periods[index]


def latest_started_period(
    periods: "tuple[DerivedPeriod, ...]", day: date,
) -> "DerivedPeriod | None":
    """Return the last period of *periods* opening on or before *day*, else ``None``.

    **The single ordering search**, shared by
    :meth:`PayCalendar.period_starting_on_or_before` and by
    :meth:`PayCalendar.filing_period` -- which needs it over the MATERIALISED
    subset rather than over every payday, and a second bisect written for that
    would be the duplication this step exists to remove.

    Args:
        periods: Periods in ``start_date`` ascending order.
        day: The calendar day to place.

    Returns:
        The last period whose ``start_date`` is on or before *day*, or ``None``
        when *day* precedes every one of them.
    """
    index = bisect_right(periods, day, key=_BY_START_DATE) - 1
    if index < 0:
        return None
    return periods[index]


def earliest_started_period(
    periods: "tuple[DerivedPeriod, ...]", day: date,
) -> "DerivedPeriod | None":
    """Return the first period of *periods* opening on or after *day*, else ``None``.

    The exact mirror of :func:`latest_started_period`, and it is HERE rather
    than inline for the reason the module docstring gives: a search over an
    ordered period tuple is a primitive, and this one had been written into
    :meth:`~._calendar.PayCalendar.period_starting_on_or_after` as a bare
    ``bisect_left`` while its backward twin was already shared.  Plan step
    **C2-f1** needed the same search over the MATERIALISED subset -- the
    situation that made :func:`latest_started_period` shared in the first place
    -- and a second bisect written for that would have been the duplication
    this module exists to remove.

    ``bisect_left`` rather than :func:`latest_started_period`'s
    ``bisect_right``: this asks for the first index at or after *day*, so a
    period opening exactly ON *day* must be included rather than stepped past.

    Args:
        periods: Periods in ``start_date`` ascending order.
        day: The calendar day to search forward from, inclusive.

    Returns:
        The first period whose ``start_date`` is on or after *day*, or ``None``
        when every one of them opens earlier -- "the schedule has not reached
        there yet" rather than "never".
    """
    index = bisect_left(periods, day, key=_BY_START_DATE)
    if index >= len(periods):
        return None
    return periods[index]


def opening_payday(periods: "tuple[DerivedPeriod, ...]") -> "date | None":
    """Return the first payday of *periods*, or ``None`` when there are none.

    **The single opening-bound rule.**  Shared with the recurrence arc's
    ``PeriodCalendar``, which held a byte-identical copy until plan step C2-a --
    two implementations of "where does this schedule start", which is the defect
    row P6 counts on the containment question and this one has in miniature.

    Args:
        periods: Periods in ``start_date`` ascending order.

    Returns:
        The earliest ``start_date``, or ``None`` for an empty schedule.
    """
    if not periods:
        return None
    return periods[0].start_date


def period_by_id(
    periods: "tuple[DerivedPeriod, ...]", period_id: "int | None",
) -> "DerivedPeriod | None":
    """Return the period of *periods* carrying *period_id*, else ``None``.

    **The single identity lookup.**  Shared with the recurrence arc's
    ``PeriodCalendar`` at plan step C2-b1 for the reason every other primitive
    here is shared: two implementations of one question drift, and this one
    answered a WRITE question -- which stored row a rule's authored start
    period named -- until plan step R7b-4 folded that FK into a date and left
    this without an ``app/`` caller.  The pay-calendar arc rules on whether it
    survives; see :meth:`~._calendar.PayCalendar.period_by_id`.

    Linear rather than a map built at construction, and deliberately: a
    calendar is built once per request and the lookup runs once per rule, so an
    index would be a second derived value to keep in step with :attr:`periods`
    for no measured gain (61 paydays against 46 live rules on production).

    Args:
        periods: The owner's periods, in any order.  Identity is not a search
            over a sorted key, so unlike the two bisects above this carries no
            ordering precondition.
        period_id: A ``budget.pay_periods.id``, or ``None``.

    Returns:
        The matching :class:`~._derive.DerivedPeriod`, or ``None`` when
        *period_id* is ``None`` or names no period here.  ``None`` in is
        ``None`` out rather than an error: a rule may legitimately name no
        start period, and the foreign key is ``ON DELETE SET NULL`` -- though a
        stale in-memory id can outlive the row it named, which is the second
        way this answers ``None``.  A PROJECTED period can never match, because
        every one of them carries ``period_id = None``.
    """
    if period_id is None:
        return None
    for period in periods:
        if period.period_id == period_id:
            return period
    return None


def earliest_start_in_month(
    periods: "tuple[DerivedPeriod, ...]", year: int, month: int,
) -> "date | None":
    """Return the earliest payday of *periods* falling in *year* / *month*.

    **The single "when does this month's first paycheck land" rule**, shared
    with the recurrence arc's ``PeriodCalendar`` at plan step C2-b1.  It is the
    one question ``Monthly First`` asks: that pattern fires on each month's
    FIRST paycheck, so whether a month can honour a rule depends on when its
    first paycheck arrives.

    A minimum over the periods that exist rather than an index into a walk,
    because months with no payday are legal -- a cadence longer than a month
    leaves some empty, and the schedule ends somewhere.

    Args:
        periods: The owner's periods, in any order.  It takes a minimum rather
            than a first match, so like :func:`period_by_id` and unlike the two
            bisects it carries no ordering precondition.
        year: Calendar year.
        month: Calendar month, 1-12.

    Returns:
        The earliest ``start_date`` in that month, or ``None`` when no period
        opens there.  ``None`` is a real answer, not an error.
    """
    starts = [
        period.start_date for period in periods
        if period.start_date.year == year and period.start_date.month == month
    ]
    if not starts:
        return None
    return min(starts)


def materialised_periods(
    periods: "tuple[DerivedPeriod, ...]",
) -> "tuple[DerivedPeriod, ...]":
    """Return the periods of *periods* a foreign key can point at.

    **The single "is this period SAVED" rule**, shared by
    :meth:`PayCalendar.filing_period` -- which must answer a row
    ``journal_entries.pay_period_id`` can name -- and by
    :meth:`PayCalendar.saved`, whose window keys the balance seam's per-period
    maps by ``budget.pay_periods.id``.  Two implementations of the predicate
    would be two answers to "which of these periods exists in the table", and
    an adversarial review of plan step C2-a already caught the first cut of
    ``filing_period`` skipping it: two lines of input returned a period whose
    id was ``None`` straight into a ``NOT NULL`` column.

    A period is unmaterialised two ways, and both are legitimate: a PROJECTION
    past the owner's horizon (:meth:`PayCalendar.axis`), and a candidate payday
    the writer has not saved yet -- which :func:`~._derive.derive_periods`
    accepts by design and which ``pay_period_write`` builds a calendar out of
    on every write.

    Args:
        periods: The periods to filter, in any order.  Their order is
            preserved, so a sorted input yields a sorted output.

    Returns:
        The subset carrying a ``period_id``; empty when none does.
    """
    return tuple(
        period for period in periods if period.period_id is not None
    )


def final_covered_day(periods: "tuple[DerivedPeriod, ...]") -> "date | None":
    """Return the last day *periods* covers, or ``None`` when there are none.

    The symmetric partner of :func:`opening_payday`, and shared for the same
    reason.  The LAST period's ``end_date`` rather than a maximum over all of
    them, because the periods are ordered and non-overlapping by construction.

    Args:
        periods: Periods in ``start_date`` ascending order.

    Returns:
        The last covered day, or ``None`` for an empty schedule.
    """
    if not periods:
        return None
    return periods[-1].end_date
