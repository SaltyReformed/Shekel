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

from bisect import bisect_right
from datetime import date
from operator import attrgetter

from ._derive import DerivedPeriod

#: The bisect key for every search here: a period's opening payday.  Module
#: level so no two searches can key on different fields -- which is one of the
#: ways the six implementations row P6 counts came to disagree.
_BY_START_DATE = attrgetter("start_date")


def containing_period(
    periods: "tuple[DerivedPeriod, ...]", day: date,
) -> "DerivedPeriod | None":
    """Return the period of *periods* whose span covers *day*, else ``None``.

    **The single containment search**, shared by :class:`PayCalendar` and
    :class:`PeriodWindow` so the calendar and a view over it cannot answer
    differently -- the whole point of plan step C2 being that six copies of
    this predicate already do.

    Periods never overlap (they are derived from a set of distinct sorted
    paydays), so the latest period STARTING on or before *day* is the only
    candidate that can contain it and one bisect answers.

    Args:
        periods: Periods in ``start_date`` ascending order.
        day: The calendar day to place.

    Returns:
        The containing :class:`~._derive.DerivedPeriod`, or ``None`` when *day*
        falls in a hole, before the first period, or after the last one's end.
    """
    index = bisect_right(periods, day, key=_BY_START_DATE) - 1
    if index < 0:
        return None
    period = periods[index]
    return period if day <= period.end_date else None


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
    answers a WRITE question -- which stored row a rule's authored start period
    names -- so a drift places a generated row against the wrong paycheck.

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
