"""
Shekel Budget App -- Calendar-month arithmetic, in ONE place

A monthly cadence walks ABSOLUTE month ordinals rather than adding months to a
date, and clamps the day it means to each month's length.  Two callers need
that walk and they must not disagree:

* :func:`app.services.recurrence._resolution._calendar_anchor` takes the first
  occurrence on or after a rule's opening bound;
* :func:`app.services.recurrence._occurrence.occurrences` takes the whole
  sequence from that anchor forward.

They ARE the same walk seeded differently -- the anchor is its first element --
and until plan step R3 they were two copies of the same arithmetic in two
modules, with ``12`` spelled twice and a test standing in for the thing they
share.  A neutral review named it as the one place this arc's own thesis (one
producer, no second copy to disagree with the first) was not being followed.
So the walk lives here and both call it.

Why ordinals and not ``date`` arithmetic
----------------------------------------

Adding a month to a date has to answer "what is 31 January plus one month",
and every answer that returns a DATE loses the day: 28 February plus one month
is 28 March, and the 31st never comes back.  Walking ordinals keeps the
NOMINAL day and re-clamps it per month, so a day-31 rule is 31 Jan, 28 Feb,
31 Mar -- the last day of every month, which is the only reading that matches
what ``recurrence_engine._match_monthly`` does today and the only one that
does not decay (ruling R-R3).

Pure: no Flask, no ORM, no clock, no database.
"""
import calendar as calendar_module
from collections.abc import Iterator
from datetime import date

#: Months in a year.  The one spelling, for both callers.
MONTHS_PER_YEAR = 12


def month_ordinal(day: date) -> int:
    """Return *day*'s absolute month ordinal.

    Months numbered continuously from year 0, so "three months later" is
    ``+ 3`` with no year-boundary special case and a residue class over
    ordinals is the same set as a residue class over month NUMBERS whenever
    the step divides 12 -- which it does for every calendar pattern
    (1, 3, 6, 12).

    Args:
        day: Any date.

    Returns:
        ``year * 12 + (month - 1)``.
    """
    return day.year * MONTHS_PER_YEAR + (day.month - 1)


def clamped_day(ordinal: int, nominal_day: int) -> date:
    """Return the date *nominal_day* names in the month *ordinal* numbers.

    Args:
        ordinal: An absolute month ordinal, from :func:`month_ordinal`.
        nominal_day: The day of the month the rule MEANS, 1-31, before
            clamping.

    Returns:
        That month's *nominal_day*, or its last day when the month is shorter
        -- so a day-31 rule is the 31st in January and the 30th in April,
        rather than decaying to the 30th forever.
    """
    year, month_index = divmod(ordinal, MONTHS_PER_YEAR)
    month = month_index + 1
    last_day = calendar_module.monthrange(year, month)[1]
    return date(year, month, min(nominal_day, last_day))


def walk_months(
    start_ordinal: int, nominal_day: int, month_step: int,
) -> Iterator[date]:
    """Yield *nominal_day* in *start_ordinal*'s month, then every *month_step*.

    Unbounded by design: the anchor derivation takes the first element that
    clears its bound and the occurrence engine applies the rule's own closing
    bounds, so a stopping condition here would be a third opinion about when a
    recurrence ends.

    Args:
        start_ordinal: The absolute month ordinal to start from.
        nominal_day: The day of the month the rule MEANS, 1-31.
        month_step: Months between occurrences.  Must be positive; both
            callers refuse a non-positive interval before walking.

    Yields:
        Occurrence dates, ascending, until the consumer stops pulling.  Walked
        past year 9999 it raises ``ValueError`` from ``date`` rather than
        looping, which no caller's window can reach.
    """
    ordinal = start_ordinal
    while True:
        yield clamped_day(ordinal, nominal_day)
        ordinal += month_step


__all__ = ["MONTHS_PER_YEAR", "clamped_day", "month_ordinal", "walk_months"]
