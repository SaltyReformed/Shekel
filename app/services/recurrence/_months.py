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
31 Mar -- the last day of every month, which is the only reading that
reproduces the reverse matcher plan step R4a replaced (it clamped with
``min(day, monthrange(...))`` per period) and the only one that does not decay
(ruling R-R3).

Pure: no Flask, no ORM, no clock, no database.
"""
import calendar as calendar_module
from collections.abc import Iterator
from datetime import date

from app.enums import RecurrenceUnitEnum
from app.exceptions import ShekelError

#: Months in a year.  The one spelling, for both callers.
MONTHS_PER_YEAR = 12

#: How many MONTHS one of each calendar unit spans.
#:
#: ``PERIOD`` and ``WEEK`` are absent because they are not month-based at all --
#: :func:`months_per_step` refuses them rather than answering a plausible
#: number, which is the disposition every other partial function in this
#: package takes over an enum.
_MONTHS_PER_UNIT: dict[RecurrenceUnitEnum, int] = {
    RecurrenceUnitEnum.MONTH: 1,
    RecurrenceUnitEnum.YEAR: MONTHS_PER_YEAR,
}

#: The units measured in whole months, and therefore the only ones that fire on
#: a DAY of the month.
#:
#: **The one statement of that class**, and an adversarial review of plan step
#: R7b-1 is why it is here rather than beside its reader.  ``_resolution``
#: carried its own ``(MONTH, YEAR)`` tuple; the two were extensionally equal,
#: nothing made them so, and the ONLY way to reach
#: :class:`MonthStepError` from either caller was to drive them apart -- which
#: is what a test had to monkeypatch to exercise the refusal at all.  A guard
#: whose entire reachability condition is "two hand-written sets disagree" is
#: the fence this project removes rather than tests.
#:
#: Firing on a day of the month is not a second fact about a unit: a cadence
#: measured in months has a day-of-month coordinate and one measured in
#: paychecks or weeks does not.
MONTH_SPANNING_UNITS: tuple[RecurrenceUnitEnum, ...] = tuple(_MONTHS_PER_UNIT)


class MonthStepError(ShekelError):
    """A cadence unit has no reading in months.

    A broken invariant rather than user input: :func:`months_per_step` is asked
    only for a cadence its caller has already routed to the calendar family, so
    a unit arriving here without a month span means the router and this table
    disagree about which units are calendar units.

    **A :class:`~app.exceptions.ShekelError`, and an adversarial review of plan
    step R7b-1 is why.**  It was a bare ``ValueError``, which put it outside the
    hierarchy every other refusal in this package raises into
    (``RecurrenceResolutionError``, ``RecurrenceGenerationError``,
    ``RecurrenceFrequencyError``) -- so a handler written against that hierarchy
    would not have caught it.  Both callers also CONVERT it to their own
    module's error, because each states a different contract about what could
    not be answered; the base class is what makes a third caller that forgets
    to convert still fail inside the hierarchy rather than outside it.
    """


def months_per_step(unit: RecurrenceUnitEnum, interval_n: int) -> int:
    """Return the month stride a ``(interval_n, unit)`` cadence walks in.

    **The ONE producer of that stride, and it had two.**  The anchor derivation
    (``_resolution._calendar_anchor``) took a ``month_step`` off the pattern
    table while the occurrence walk (``_occurrence._unbounded``) computed
    ``interval_n * MONTHS_PER_YEAR`` for the YEAR unit itself -- two spellings
    of one fact, in the two places whose own docstrings claim to be "the SAME
    walk seeded differently".  They agreed; nothing made them.

    Both of those callers route to it on a calendar unit and CONVERT the
    refusal below into their own module's error; see :class:`MonthStepError`.

    Args:
        unit: The cadence unit.  Must be a calendar unit.
        interval_n: How many *unit*\\ s pass between occurrences.

    Returns:
        Months between occurrences -- 3 for a quarterly cadence, 24 for every
        two years.

    Raises:
        MonthStepError: When *unit* is not measured in months.
    """
    months = _MONTHS_PER_UNIT.get(unit)
    if months is None:
        raise MonthStepError(
            f"recurrence unit {unit!r} has no reading in months, so it has no "
            f"month stride.  Only the calendar units walk months; a "
            f"pay-period or weekly cadence reaching this call means the anchor "
            f"family and the occurrence walk disagree about which units are "
            f"calendar units."
        )
    return months * interval_n


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


__all__ = [
    "MONTHS_PER_YEAR",
    "MONTH_SPANNING_UNITS",
    "MonthStepError",
    "clamped_day",
    "month_ordinal",
    "months_per_step",
    "walk_months",
]
