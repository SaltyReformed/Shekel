"""How a pay calendar is COUNTED: how many paydays a month or a year holds.

The package's FOURTH shape.  :mod:`._searches` answers "which period is X",
:mod:`._views` answers "which SLICE of this schedule does a surface report
over", :mod:`._walks` answers "keep giving me paychecks from here".  This one
answers **"how many times was this owner paid between these two days"** -- a
COUNT of paydays rather than a period, a window or a stream, and the two
questions below are the only two shapes of it anything asks.

**Why it is a count and not a window, which is the whole point of the module.**
Its consumers do not want a pay period.  A 24-per-year deduction skips the
THIRD payday of a month and a 12-per-year one is taken on the FIRST, so the
paycheck engine wants a position; the analytics year overview flags a month
holding three paydays, so it wants a size.  Both are answered by counting
paydays, and a payday is a ``date``.  **Returning dates rather than
:class:`~._derive.DerivedPeriod` values is a structural guard rather than a
convenience**: a projected payday is not a period a foreign key can name, and a
value that never becomes one cannot be filed into, cannot be handed to
:meth:`~._calendar.PayCalendar.require_period`, and cannot be mistaken for a
saved row by a consumer that assumes ``period_id is None`` means "in the
future".

*That guard is NOT on its own the answer to ``pay_calendar:R-PC14``, and an
adversarial review of plan step balance:X-bh-1 corrected a draft of this
paragraph that said it was.*  R-PC14 rejects projecting backwards because it
"would attribute money to paychecks that never happened", and plan step
**balance:X-bh-2** does exactly that -- a year-to-date gross IS money attributed
to those paydays, whatever type carries them.  What answers R-PC14 is ruling
**balance:R-IA**: the app stops GUESSING that the earlier paychecks happened and
stores the fact, ``budget.pay_schedule.history_opens_on``, so the rhythm runs
back to a bound the owner stated rather than to one the app inferred.  The
date-return is the narrower guarantee it sounds like -- nothing fabricated can
be FILED into -- which is what keeps `filing_period` and the balance axis
untouched while the rhythm widens.

**One primitive, two questions.**  :func:`paydays_in_month_through` and
:func:`paydays_in_year_before` differ only in the span they ask for, so the
span search is written once (:func:`_paydays_between`) and neither caller
states a bound the other could state differently.  That is the same argument
:mod:`._searches` makes for its bisects: six copies of "which period contains
this day" already disagreed at the edges that matter (ledger row **P6**).

**It sits LAST in the package's chain**, beside :mod:`._walks` and for the same
reason that module gives: it takes a whole :class:`~._calendar.PayCalendar`
because it needs both the payday set and the cadence, and a caller holding the
value object should not have to open it to ask a question of it -- nor be able
to pair one owner's paydays with another owner's cadence.  ``_derive`` ->
``_searches`` -> ``_window`` -> ``_views`` -> ``_calendar`` -> this.  **It is
not a method on that class**, and that is a measured constraint rather than
taste: ``_calendar.py`` stands at 995 of pylint's 1,000-line ceiling and 20 of
its 20 public methods, and ledger row **P77** records that the next step
touching it breaks both -- which is exactly what happened at
``recurrence:R16-b-1``, resolved by putting that step's one method here in the
package instead.

**What it does NOT yet answer, stated because the gap is a live finding.**  The
forward continuation past the schedule's horizon is here (ruling
``pay_calendar:R-PC9``); the BACKWARD one below the opening payday is not, so a
month or a year that the recorded schedule opens inside is counted from the
first recorded payday rather than from the owner's first real one.  That is the
remaining half of ledger row **N-390** and it is plan step **balance:X-bh-2**'s
subject, which also stores the fact the projection needs -- when the owner's
pay history begins, which no derivation can supply.

Pure: no session, no clock, no Flask.  Every answer is a function of the
paydays and the cadence the calendar carries.
"""

from datetime import date, timedelta
from itertools import takewhile

from ._calendar import PayCalendar
from ._searches import paydays_between
from ._views import projected_paychecks


def paydays_in_month_through(
    calendar: PayCalendar, day: date,
) -> "tuple[date, ...]":
    """Return the paydays of *day*'s calendar month falling on or before it.

    **The single "where does this payday sit in its month" rule**, and the two
    consumers that need it want different halves of one answer.  For a day that
    IS a payday the length is that payday's 1-based position in its month, so
    the paycheck engine reads ``>= 3`` for the third paycheck a 24-per-year
    deduction skips and ``== 1`` for the first a 12-per-year one is taken on --
    two predicates that were two independent scans of a caller-supplied period
    list until plan step **balance:X-bh-1**.  Asked at a month's LAST day the
    length is how many paydays that month holds, which is what the analytics
    year overview flags as a three-paycheck month.

    One rule rather than two is load-bearing rather than tidy: the engine and
    the year overview disagreed about the same month before this step, because
    each counted over whatever period set its own caller happened to hold.

    Args:
        calendar: The owner's schedule.
        day: Any calendar day.  Its month is the span, and it is the inclusive
            upper bound within that month.  It need not be a payday -- the
            month-size question asks at a month end -- but when it is one, it
            is counted.

    Returns:
        The paydays, ascending, as plain ``date`` values.  Empty when the month
        holds none at or before *day*, which is a real answer: a cadence longer
        than a month leaves months empty, and the recorded schedule opens
        somewhere.
    """
    return _paydays_between(calendar, _month_opens(day), day)


def saved_paydays_in_month_through(
    calendar: PayCalendar, day: date,
) -> "tuple[date, ...]":
    """Return the RECORDED paydays of *day*'s month falling on or before it.

    :func:`paydays_in_month_through`'s bounded twin, over the same month span
    and the same search -- what differs is the SET, and each name says which
    one it means.  This one counts only paydays the app HOLDS; that one
    continues the owner's rhythm past the schedule's horizon.

    **Two sets rather than two rules, and the distinction is the developer's
    ruling balance:R-IB.**  The paycheck engine needs the total answer: a
    paycheck it cannot place would take ordinal 0, and 0 silently drops a
    12-per-year deduction and takes a 24-per-year one on a third paycheck.
    The analytics month card needs the bounded one, because everything else on
    that card -- its income, its expenses, its net, its month-end balance -- is
    folded from SAVED periods, so a projected payday would appear beside a
    ``$0.00`` net and an unmoved balance.  Measured 2026-08-30 on the
    developer's own data before the bound went in: 29 month cards, 2028-08
    through 2030-12, read "3 paychecks, income ``$0.00``, balance
    ``$9,539.92``".

    **The bound comes OFF when the cash tier projects** -- ledger row
    **N-394**, which is what pairs the two halves of that card rather than
    silencing one of them.  Until then this is where the card says how far its
    own evidence reaches.

    Args:
        calendar: The owner's schedule.
        day: Any calendar day; its month is the span and it is the inclusive
            upper bound within that month.

    Returns:
        The recorded paydays, ascending.  Empty for a month the saved schedule
        opens no period in, which is a real answer.
    """
    return paydays_between(calendar.periods, _month_opens(day), day)


def _month_opens(day: date) -> date:
    """Return the first day of *day*'s calendar month.

    One line, and it is a function because both month questions above take
    this bound: written twice it would be two places for "which month is this"
    to drift, which is the shape ledger row **P6** counts.
    """
    return date(day.year, day.month, 1)


def paydays_in_year_before(
    calendar: PayCalendar, day: date,
) -> "tuple[date, ...]":
    """Return the paydays of *day*'s calendar year falling strictly before it.

    **The single year-to-date rule.**  The paycheck engine's two cumulatives
    both walk it -- the gross that drives the FICA Social Security wage-base cap
    and a deduction's own calendar-year total against its ``annual_cap`` -- and
    they walked two separate copies of it over a caller-supplied period list
    until plan step **balance:X-bh-1**.

    The calendar YEAR is the span because that is the window both caps are
    defined over: ``ck_fica_configs`` prices a wage base per tax year, and
    :func:`~app.utils.deduction_cap.cap_period_amount` clamps a calendar year's
    total.  STRICTLY before, because a cumulative is what has already been paid
    when this paycheck is priced.

    Args:
        calendar: The owner's schedule.
        day: The payday whose year-to-date is wanted.  Excluded from the
            answer.

    Returns:
        The paydays, ascending, as plain ``date`` values.  Empty for the year's
        first payday, which is the correct cumulative of zero rather than an
        error -- and empty for 1 January whatever the schedule holds, because
        the span it names ends the day before it opens.
    """
    return _paydays_between(
        calendar, date(day.year, 1, 1), day - timedelta(days=1),
    )


def _paydays_between(
    calendar: PayCalendar, first_day: date, last_day: date,
) -> "tuple[date, ...]":
    """Return every payday in ``[first_day, last_day]``, saved then projected.

    The one span search both public questions above are asked through.  Saved
    where the schedule reaches and projected at the owner's cadence beyond it,
    which is the same pairing :func:`~._views.axis_window` makes and the same
    continuation producer -- :func:`~._views.projected_paychecks`, the ONE loop
    that steps the forward rhythm (ledger row **P6**).

    **It COMPOSES two producers and states only where the span stops.**  The
    saved half is :func:`~._searches.paydays_between`, which owns the bisect
    pair and the empty-span disposition; the continuation is
    :func:`~._views.projected_paychecks`, told where to START so its cost is
    the size of the answer rather than the distance from the horizon -- an
    adversarial review of this step measured **442 ms** on one year-overview
    render before that argument existed, against **0.6 ms** after
    (2026-08-30).

    **It reads every payday the calendar HOLDS, saved or not**, where the
    producers it replaced read :meth:`~._calendar.PayCalendar.saved`.  For a
    calendar built by :func:`~._loader.calendar_for` the two sets are equal --
    it reads saved rows and nothing else -- so nothing moves today.  The
    difference is deliberate rather than incidental: an unsaved candidate
    payday is still a day the owner is paid on, which is what a COUNT asks,
    and :meth:`~._calendar.PayCalendar.saved` additionally REFUSES a gapped
    schedule, so routing a count through it would make "how many paydays does
    this month hold" raise for a state that has an answer.

    **Nothing is projected BACKWARD below the schedule's opening payday yet**,
    so a span opening before it is answered from the first recorded payday
    onward.  That is the live half of ledger row **N-390**; see the module
    docstring.

    Args:
        calendar: The owner's schedule.
        first_day: Inclusive lower bound.
        last_day: Inclusive upper bound.  May lie past the schedule's horizon,
            which is what the projection is for.

    Returns:
        The paydays, ``start_date`` ascending, as plain ``date`` values.
    """
    if last_day < first_day or not calendar.periods:
        return ()
    periods = calendar.periods
    saved = paydays_between(periods, first_day, last_day)
    if last_day <= periods[-1].end_date:
        return saved
    # ``takewhile`` rather than a hand-rolled loop so the stop condition is the
    # whole of what this adds.  The filter keeps the paydays that OPEN in the
    # span rather than the periods that overlap it -- this counts paychecks,
    # not coverage -- and it is what absorbs the one period ``from_day`` may
    # yield from before the span, which that argument documents as deliberate.
    projected = tuple(
        period.start_date
        for period in takewhile(
            lambda paycheck: paycheck.start_date <= last_day,
            projected_paychecks(periods, calendar.cadence_days, first_day),
        )
        if period.start_date >= first_day
    )
    return saved + projected


__all__ = [
    "paydays_in_month_through",
    "paydays_in_year_before",
    "saved_paydays_in_month_through",
]
