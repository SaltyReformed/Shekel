"""How a pay calendar is WALKED: a paycheck sequence, unbounded by the schedule.

The package's THIRD shape, and plan step **R16-b-1** is what made it one.
:mod:`._searches` answers "which period is X", :mod:`._views` answers "which
SLICE of this schedule does a surface report over" -- both bounded by what has
been SAVED, both returning a period or a
:class:`~._window.PeriodWindow`.  A WALK is neither: it answers "keep giving me
paychecks from here", and it does not stop where the saved schedule stops,
because an owner goes on being paid after the last payday anyone has recorded.
The schedule's horizon is a MATERIALISATION boundary, not a fact about how
often the money arrives.

**It is its own module rather than a method on
:class:`~._calendar.PayCalendar`, and the reason is a measurement rather than
taste.**  That class is the single public door to this package and it carries
one method per question with the caller-level argument for each, so both of
pylint's ceilings on it -- 1,000 lines and 20 public methods -- are properties
of the number of questions rather than of any one of them.  Adding this walk as
a 21st method took the module to 1,070 lines and broke both at once, on a merge
where neither branch was over alone (``pay_calendar:C4-a-1`` had taken it to 992
and 20/20).  Ledger row **P64** records the same ceiling firing twice before.
**This module does NOT create headroom** -- it returns the file to 992 and
20/20, which is where dev already was -- and the finding that does is filed
against the pay_calendar arc: the door wants splitting by SHAPE, so that each
argument lives in the namespace that owns it.

**It sits LAST in the package's chain**, after :mod:`._calendar`, which is why
it can take a whole :class:`~._calendar.PayCalendar` where a view producer
takes a period tuple.  ``_derive`` -> ``_searches`` -> ``_window`` -> ``_views``
-> ``_calendar`` -> this.  Nothing here is imported by anything above it, so the
one-way arrow the package rests on is unchanged; the continuation this composes
(:func:`~._views.projected_paychecks`) stays in ``_views`` because
:func:`~._views.axis_window` consumes it too, and a producer two modules read
belongs where both can reach it.

Pure: no session, no clock, no Flask.  Every answer is a function of the
paydays and the cadence the calendar carries.
"""
from collections.abc import Iterator
from datetime import date

from ._calendar import PayCalendar
from ._derive import DerivedPeriod
from ._views import current_and_future_window, projected_paychecks


def paychecks_from(
    calendar: PayCalendar, day: date,
) -> Iterator[DerivedPeriod]:
    """Yield every paycheck that has not ENDED before *day*, saved then projected.

    :meth:`~._calendar.PayCalendar.current_and_future`'s TOTAL companion -- it
    yields that window and then keeps going -- and the pairing is the one
    :meth:`~._calendar.PayCalendar.span_containing` already makes against
    :meth:`~._calendar.PayCalendar.period_containing`: the saved producer
    answers where the schedule reaches, this one keeps answering past it at the
    owner's own cadence.  A caller walking a CADENCE rather than reading a
    window needs that.

    **Plan step R16-b-1 added it because its absence was a silent wrong
    answer**, not a missing convenience.  ``recurrence.occurrences(...,
    through=X)`` walked the saved periods for the ``PERIOD`` unit, so it
    returned fewer dates than *X* asked for and raised nothing: measured on a
    production clone (2026-08-27), an every-paycheck rule asked through
    ``2036-01-01`` answered 62 dates ending ``2028-07-27`` against the 255 that
    owner is actually paid in the window, the last ``2035-12-20``.  A consumer
    folding those occurrences into money -- the balance seam's ESTIMATED loan
    tier, plan step R16-b-2 -- would under-generate by seven years and report a
    payoff that never comes.

    **FINITE**, because :func:`~._views.projected_paychecks` stops at
    :data:`~app.utils.dates.CALENDAR_DATE_MAX` -- so a consumer that forgets to
    stop pulling terminates instead of hanging, and the ordinary stop is its own
    window (``recurrence._occurrence._bounded``, the one place a bound is
    applied).  Finite is not SMALL: the length is
    ``(CALENDAR_DATE_MAX - horizon) / cadence_days``, about 1,950 paychecks at a
    fourteen-day cadence and about 27,300 at the one-day cadence
    ``budget.pay_schedule`` legally admits.

    **It COMPOSES the two producers and steps nothing itself.**  The saved half
    is :func:`~._views.current_and_future_window` and the continuation is
    :func:`~._views.projected_paychecks`, where the argument for each lives.
    That second one is a function because it was two loops --
    :func:`~._views.axis_window` walked the same recurrence to a requested day
    -- and every projected paycheck it yields carries ``period_id = None`` with
    a ``period_index`` continuing the saved sequence, so a phase test spans the
    seam and a caller needing a foreign key target still cannot mistake one for
    a saved row.

    Args:
        calendar: The owner's schedule.  Taken whole rather than as
            ``(periods, cadence_days)`` because this module sits after
            :mod:`._calendar` in the chain and can: a caller holding the value
            object should not have to open it to ask a question of it.
        day: The first day the sequence covers.  A paycheck qualifies when it
            has not ENDED before it, so the one *day* falls IN is the first
            yielded -- the same admission test
            :meth:`~._calendar.PayCalendar.current_and_future` applies.  A
            *day* below the schedule's opening yields the whole schedule rather
            than projecting backwards (the 2026-08-10 ruling: before the first
            payday there is no paycheck).

    Yields:
        :class:`~._derive.DerivedPeriod` values, ``start_date`` ascending,
        saved where the schedule reaches and projected beyond it, up to the last
        paycheck OPENING within the application's calendar.  Nothing at all for
        a calendar with no payday, which is the same answer
        :meth:`~._calendar.PayCalendar.current_and_future` gives them.
    """
    # The saved half is ``current_and_future`` itself, not a second statement
    # of its admission test: "has not ENDED before this day" is written once,
    # in :func:`~._views.current_and_future_window`, and both this and that
    # method ask it there.  Two copies of one predicate is the shape ledger row
    # **P6** counted seven of, and it is what makes the equality between the
    # two producers structural rather than a coincidence two comprehensions
    # happen to share.
    yield from current_and_future_window(calendar.periods, day)
    yield from (
        period
        for period in projected_paychecks(calendar.periods, calendar.cadence_days)
        if period.end_date >= day
    )


__all__ = ["paychecks_from"]
