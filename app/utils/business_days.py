"""
Shekel Budget App -- Business-Day Calendar

The weekend rule, the computed US federal holiday set, and the ONE
displacement that moves a date off a day no money moves on.  Pure date
arithmetic: no Flask, no SQLAlchemy, no clock, so it imports cleanly into
the pay-calendar package (which is pure by design) and into the recurrence
engine alike.

**One module, two consumers, ruled that way** (``pay_calendar:R-PC47``).
The pay calendar's projection asks it where a payday really lands when the
rhythm names a holiday; ``recurrence:R8-d`` asks it the same question for a
bill's CASH date.  Two copies of "which days is a bank open" would be two
places for a holiday rule to drift, and the drift would be invisible: both
copies answer the same thing for most of any year and part company on the
handful of days the whole feature exists for.

**The holiday set is what carries this module, and a weekend rule alone
would be theatre.**  Measured 2026-09-04 against ``shekel-prod-db``, whose
owner holds 63 recorded paydays from 2026-03-26 to 2028-08-10: every one of
them is a Thursday, so no payday of his can ever fall on a Saturday or a
Sunday, and none of the 63 falls on a federal holiday either.  The rhythm
day ledger row **N-398** is about -- the 2026-01-01 paycheck payroll really
paid on 2025-12-31 -- is New Year's Day, and it is a **Thursday**.  A
weekend-only rule sees nothing there at all.

The scale, stated over the WHOLE calendar this application admits rather
than over a sampled window, because a window's edge is a number nobody
re-derives: continuing that owner's cadence forward to
:data:`~app.utils.dates.CALENDAR_DATE_MAX` gives 1,888 projected paydays of
which **64** fall on a federal holiday and none on a weekend; backward to
:data:`~app.utils.dates.CALENDAR_DATE_MIN`, 684 rhythm days of which **22**
do.  How many of the 22 an owner is exposed to depends on how far back
``budget.pay_schedule.history_opens_on`` lets the rhythm run, so no single
number states it: N-398's own measurement is against a stated opening of
2026-01-05, where the 2026-01-01 day falls below the floor and the
year-to-date correctly reads nine; state an earlier opening and it reads
ten.

**Which calendar this is, said precisely, because two defensible ones
differ.**  These are the ELEVEN federal holidays of ``5 U.S.C. 6103(a)``.
The Saturday-to-preceding-Friday rule is ``5 U.S.C. 6103(b)``; the
Sunday-to-following-Monday one is E.O. 11582 -- two sources, not one, and
:func:`_observed` applies both.  Together they are the calendar
``pay_calendar:R-PC47`` names in the words "computed federal holiday set".

The FEDERAL RESERVE's calendar is not identical to it: the Fed does not
observe a Saturday holiday on the preceding Friday, so banks settle ACH on
a day federal offices are closed.  Since a direct deposit rides ACH, that
is arguably the more accurate calendar for a payday, and it is an open
question for a later leaf rather than a settled one.  Measured 2026-09-04
over 2000-2100: the two calendars differ on **71** days, every one of them
a Friday, and **no** Thursday payday lands differently under either
convention.  That second figure is an ENUMERATION and not a deduction -- a
Friday divergence CAN in principle reach a Thursday payday, since ``NEXT``
from a Thursday holiday lands on the Friday; it happens not to, and an
earlier draft of this paragraph argued from the weekday alone and was a non
sequitur.  Changing calendars is a change to :func:`_observed` and to
nothing else, which is the whole argument for one module.

Inauguration Day is deliberately absent.  It is a federal holiday for
federal employees in the District of Columbia area only, banks do not
close, and payroll does not move for it.
"""
import calendar
from datetime import date, timedelta
from functools import cache

from app.enums import BusinessDayShiftEnum

# ``datetime.date.weekday()`` indices, named because the arithmetic below
# reads as nonsense against bare integers: a holiday that is "the fourth
# ``3`` of November" is a line no reviewer can grade.
MONDAY = 0
THURSDAY = 3
SATURDAY = 5
SUNDAY = 6

# Juneteenth National Independence Day became a federal holiday on
# 2021-06-17, so it is NOT one for any earlier year.  The bound matters
# because this application's calendar opens at
# ``app.utils.dates.CALENDAR_DATE_MIN`` (2000-01-01): 21 of the years a
# user may put on record predate the holiday, and emitting it for them
# would move a payday that payroll never moved.
JUNETEENTH_FIRST_YEAR = 2021


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    """Return the *occurrence*-th *weekday* of a month (e.g. the 3rd Monday).

    Args:
        year: Calendar year.
        month: Calendar month, 1-12.
        weekday: A ``datetime.date.weekday()`` index -- :data:`MONDAY` or
            :data:`THURSDAY` here.
        occurrence: 1-based, so ``1`` is the first such weekday of the month.

    Returns:
        The day itself.
    """
    opens = date(year, month, 1)
    lead = (weekday - opens.weekday()) % 7
    return opens + timedelta(days=lead + 7 * (occurrence - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """Return the LAST *weekday* of a month (Memorial Day's only rule).

    Counted backward from the month's final day rather than forward at a
    fixed occurrence, because "the last Monday of May" is the fourth in
    some years and the fifth in others -- the distinction the fixed-count
    spelling gets wrong roughly one May in three.

    **It must start from the month's REAL last day**, which is what
    ``calendar.monthrange`` supplies.  A first draft of this function
    started at the 28th -- the last day every month is guaranteed to have
    -- and stepped forward a week at a time while the month held: in a
    31-day May that stops ON the 28th, because the 28th plus seven is
    already June, and the last Monday of May 2027 (the 31st) was answered
    as the 24th.  The test that caught it is
    ``test_matches_the_transcribed_calendar_exactly[2027]``.

    Args:
        year: Calendar year.
        month: Calendar month, 1-12.
        weekday: A ``datetime.date.weekday()`` index.

    Returns:
        The day itself.
    """
    closes = date(year, month, calendar.monthrange(year, month)[1])
    return closes - timedelta(days=(closes.weekday() - weekday) % 7)


def _observed(day: date) -> date:
    """Return the day a FIXED-DATE federal holiday is observed on.

    TWO rules from two sources, not one: a holiday falling on a Saturday is
    observed the preceding Friday (``5 U.S.C. 6103(b)``), one falling on a
    Sunday the following Monday (E.O. 11582).  Applied to the five
    fixed-date holidays only -- the six weekday-anchored ones (Martin
    Luther King, Jr. Day and the rest) can never land on a weekend, so
    asking this of them would be a branch no input reaches.

    **This function is the whole of the Federal Reserve divergence** the
    module docstring names: the Fed applies the Sunday rule and not the
    Saturday one, which is why the two calendars differ on 71 days over
    2000-2100 and every one of them is a Friday.

    Args:
        day: The holiday's statutory date.

    Returns:
        The date it is observed on, which is *day* itself from Monday to
        Friday.
    """
    if day.weekday() == SATURDAY:
        return day - timedelta(days=1)
    if day.weekday() == SUNDAY:
        return day + timedelta(days=1)
    return day


@cache
def federal_holidays(year: int) -> "frozenset[date]":
    """Return every federal holiday OBSERVED within a calendar year.

    Observed rather than statutory, and every returned date falls inside
    *year*, which is what makes :func:`is_business_day` able to answer from
    a single year's set.  The two statements are the same rule read from
    both ends: 2028-01-01 is a Saturday, so New Year's Day 2028 is observed
    on **2027**-12-31 and belongs to that year's answer and not to 2028's.

    Cached because it is a pure function of one small integer and the
    projection asks it once per candidate payday, over a horizon far wider
    than any one screen: the production owner's forward walk to
    ``CALENDAR_DATE_MAX`` asks 1,888 times and wants 73 distinct years, and
    the backward one asks 684 more for 27.  The key is bounded by ``date``'s own
    year domain, so the memo cannot grow without bound, and the value is a
    frozenset, so no caller can corrupt another's answer.

    Args:
        year: Calendar year.

    Returns:
        The observed holidays, every one of them dated within *year*.
    """
    statutory = [
        date(year, 1, 1),                          # New Year's Day
        date(year, 7, 4),                          # Independence Day
        date(year, 11, 11),                        # Veterans Day
        date(year, 12, 25),                        # Christmas Day
    ]
    if year >= JUNETEENTH_FIRST_YEAR:
        statutory.append(date(year, 6, 19))        # Juneteenth
    observed = {
        day for day in map(_observed, statutory) if day.year == year
    }
    # New Year's Day of the FOLLOWING year, when the weekend rule pulls its
    # observance back across the boundary into this one.  Without this the
    # last working Friday of such a year reads as an ordinary business day.
    spillover = _observed(date(year + 1, 1, 1))
    if spillover.year == year:
        observed.add(spillover)
    observed.update({
        _nth_weekday(year, 1, MONDAY, 3),          # Martin Luther King, Jr.
        _nth_weekday(year, 2, MONDAY, 3),          # Washington's Birthday
        _last_weekday(year, 5, MONDAY),            # Memorial Day
        _nth_weekday(year, 9, MONDAY, 1),          # Labor Day
        _nth_weekday(year, 10, MONDAY, 2),         # Columbus Day
        _nth_weekday(year, 11, THURSDAY, 4),       # Thanksgiving Day
    })
    return frozenset(observed)


def is_business_day(day: date) -> bool:
    """Return whether money moves on *day*.

    Args:
        day: A calendar day within
            :data:`~app.utils.dates.CALENDAR_DATE_MIN`..:data:`~app.utils.dates.CALENDAR_DATE_MAX`.
            Not "any" day: :func:`federal_holidays` reads the FOLLOWING
            year for the New Year spillover, so ``date.max`` raises out of
            ``datetime`` rather than answering.  No in-app caller can reach
            that, and the bound is stated rather than guarded because a
            guard here would be a fence around a state no door admits.

    Returns:
        ``True`` for a weekday that is not an observed federal holiday.
    """
    return day.weekday() < SATURDAY and day not in federal_holidays(day.year)


def shift_to_business_day(day: date, shift: BusinessDayShiftEnum) -> date:
    """Return where *day* really lands under a shift convention.

    **The ONE displacement**, and the reason this module exists as a shared
    one.  A rhythm names a nominal day; an employer's convention says what
    happens when that day is not a business day, and a bill's cash date
    asks the identical question (``pay_calendar:R-PC47``).

    **It is a DISPLACEMENT of one day and knows nothing about a cadence.**
    The caller keeps its own progression running on NOMINAL days and asks
    this of each one independently.  Feeding the answer back into the
    progression is what would move every later payday by a day,
    permanently, and nothing here can prevent that -- the discriminating
    code is the caller's, so the guarantee belongs to the leaf that adds
    one.  What this function offers toward it is only that the landing is
    always a business day, hence a fixed point: applying it twice is
    applying it once.  *An earlier draft called that fixed point "the
    non-compounding guarantee", which it is not: a caller that re-anchors
    its cadence on the output compounds while every displacement here stays
    perfectly idempotent.*

    Args:
        day: The nominal day, which need not be a business day.
        shift: The owner's convention.  :attr:`BusinessDayShiftEnum.NONE`
            returns *day* untouched.

    Returns:
        *day* when it is already a business day or the convention is
        ``NONE``; otherwise the nearest business day in the convention's
        direction.

        **The answer may fall OUTSIDE
        :data:`~app.utils.dates.CALENDAR_DATE_MIN`..:data:`~app.utils.dates.CALENDAR_DATE_MAX`,
        and bounding it is the caller's.**  Both ends of the application's
        calendar are non-business days -- 2000-01-01 is a Saturday and
        2100-12-31 is the observed New Year's Day 2101 -- so
        ``PRIOR`` from the floor answers 1999-12-30 and ``NEXT`` from the
        ceiling answers 2101-01-03.  This module answers the arithmetic
        question it was asked; the columns that persist a date
        (``ck_pay_schedule_history_opens_range`` and its two siblings)
        state the bound, and the leaf that writes one decides whether an
        escaping date is clamped or refused.

    Raises:
        ValueError: *shift* is not a :class:`BusinessDayShiftEnum` member.
            Dispatching on PRIOR and treating everything else as NEXT
            would give an unrecognised value a money-moving default, which
            is the shape of a set defined by subtraction: the residue
            claims members nobody censused.
    """
    if shift is BusinessDayShiftEnum.NONE or is_business_day(day):
        return day
    if shift is BusinessDayShiftEnum.PRIOR:
        step = timedelta(days=-1)
    elif shift is BusinessDayShiftEnum.NEXT:
        step = timedelta(days=1)
    else:
        raise ValueError(
            f"unhandled business-day shift {shift!r}: this function moves "
            f"money dates, so an unrecognised convention is refused rather "
            f"than given the behaviour of whichever arm happens to be last."
        )
    landing = day + step
    while not is_business_day(landing):
        landing += step
    return landing
