"""How a pay calendar is CUT into views: the six producers of a window.

Plan step **C2-f3b**, and it left :mod:`._calendar` for the reason
:mod:`._searches` did at plan step C2-c -- that module had ONE line of headroom
under pylint's 1,000-line ceiling (ledger row **P64**), and a gate about to bind
is a signal rather than a nuisance.  *It never fired: measured at every commit
of this branch, ``_calendar.py`` was 999 lines.  The 1002 an earlier draft of
this paragraph named was a transient state inside C2-f3a's build, which that
step resolved before committing by deleting a duplicated ``Args:`` block*
(adversarial review, 2026-08-19).  The seam the ceiling was measuring is a real
one:
:class:`~._calendar.PayCalendar` is the owner's whole schedule as a VALUE --
the paydays, the derivation over them, and the questions asked OF one period --
while everything here answers a different shape of question, "which SLICE of
this schedule does a surface report over", and every one of them returns a
:class:`~._window.PeriodWindow`.

**The dependency runs one way and that is what the split buys.**  These are
free functions over a period tuple, so a view producer reads only what a caller
hands it -- the periods, the cadence, the bounds, and, for the two that refuse a
range, the ``user_id`` their message names.  It cannot reach the calendar
object, its memo, or any field a later edit adds to it.  A view of a calendar
that consulted the calendar to decide what to show would be the second producer
of an answer the calendar already has, which is the class of duplicate this
whole arc exists to remove.

**What the split COST, said rather than left for the next reader to find**
(adversarial review, 2026-08-19): a pairing :class:`~._calendar.PayCalendar`
enforces -- ``cadence_days`` is ``None`` only beside an empty payday set, which
:func:`~._derive.derive_periods` refuses to break -- is a documented
PRECONDITION here, because a free function cannot see the constructor that
holds it.  :func:`axis_window` states it where it reads the value.

**Five of the six cannot produce a gapped window and the sixth can**, which is
:class:`~._window.PeriodWindow`'s own contiguity refusal restated from the
producing end: :func:`index_window`, :func:`overlapping_window`,
:func:`current_and_future_window`, :func:`axis_window` and
:func:`projection_axis_window` SLICE a tiling, and a slice of a tiling tiles;
:func:`saved_window` FILTERS it, and a filter does not.
*The count read "four of the five" until C4's first commit added the sixth, which
is the shape a stated size has: it is a derived value stored in prose, so it is
restated rather than left to a reader to recount.*

Boundary discipline (``CLAUDE.md``): no Flask symbol, no database session, no
clock.  Every answer is a pure function of the arguments a caller supplies.
"""

from datetime import date, timedelta

from ._derive import DerivedPeriod, PayCalendarError, project_period_after
from ._searches import final_covered_day, materialised_periods, opening_payday
from ._window import PeriodWindow


def saved_window(
    periods: "tuple[DerivedPeriod, ...]",
) -> PeriodWindow:
    """Return every MATERIALISED period of *periods* as one window.

    **The balance seam's whole reporting domain** (plan step C2-c).  Every
    per-period entry the seam publishes -- the grid's column set, the cash map,
    the kind-correct balance maps, the loan map -- answers over the owner's
    entire saved schedule, and each of them used to TAKE that set as an
    argument every one of its eight callers filled with the same value.  An
    argument a caller can get wrong is a defect rather than a contract, so the
    argument is gone and this is what replaced it; the seam reads it once per
    read pass through
    :meth:`~app.services.balance_at.BalanceContext.reported_periods`.

    MATERIALISED, and that filter is load-bearing rather than defensive: the
    seam's maps are keyed by ``budget.pay_periods.id``, so an unmaterialised
    period would key every one of them under ``None`` and collapse them onto
    each other (ledger row **P21**'s shape).  The two ways a period can be
    unmaterialised are named at :func:`~._searches.materialised_periods`;
    neither reaches a calendar built by :func:`~._loader.calendar_for`, which
    reads saved rows only.

    Projections are NOT here, and that is the same distinction
    :meth:`~._calendar.PayCalendar.period_containing` draws against
    :meth:`~._calendar.PayCalendar.span_containing`: a balance column needs a
    row a ``transactions.pay_period_id`` can point at, and the forward
    projection past the horizon is :func:`axis_window`'s answer to a different
    question.

    Args:
        periods: The calendar's periods, ``start_date`` ascending.

    Returns:
        The :class:`~._window.PeriodWindow` over every saved period,
        ``start_date`` ascending.  Empty for a calendar with no saved period --
        an owner who has never generated a schedule, and the companion role.

    Raises:
        PayCalendarError: The saved periods do not cover an unbroken span,
            which means an UNSAVED candidate sits between two saved ones.
            Unreachable through :func:`~._loader.calendar_for` (it reads only
            saved rows) and through ``pay_period_write`` (it appends candidates
            after the last saved payday); it is refused rather than reported
            over, because a hole in the reported set is a balance column that
            does not add up.
    """
    return PeriodWindow(periods=materialised_periods(periods))


def index_window(
    periods: "tuple[DerivedPeriod, ...]", first_index: int, count: int,
) -> PeriodWindow:
    """Return *count* SAVED periods of *periods* from ordinal *first_index* on.

    The grid's six-period window and every other index-keyed slice.  A
    :class:`~._window.PeriodWindow`, not a calendar: the periods carry the ends
    the WHOLE calendar derived from the whole payday set, which is what ledger
    row **P14** needs and what deriving over the slice would destroy.

    Args:
        periods: The calendar's periods, ``start_date`` ascending.
        first_index: The first ``period_index`` to include.
        count: How many periods to take.  A non-positive count yields an empty
            window rather than an error -- "no periods requested" is a legal
            request.

    Returns:
        The :class:`~._window.PeriodWindow`, shorter than *count* when the
        calendar ends first and empty when *first_index* is past the end.
    """
    if count <= 0:
        return PeriodWindow(periods=())
    return PeriodWindow(
        periods=tuple(
            period for period in periods
            if first_index <= period.period_index < first_index + count
        ),
    )


def overlapping_window(
    periods: "tuple[DerivedPeriod, ...]", first_day: date, last_day: date,
) -> PeriodWindow:
    """Return every period of *periods* overlapping ``[first_day, last_day]``.

    A period overlaps when ``start_date <= last_day`` and
    ``end_date >= first_day``; both bounds are inclusive.  The calendar-month
    and calendar-year slices the reporting surfaces ask for.

    Args:
        periods: The calendar's periods, ``start_date`` ascending.
        first_day: Inclusive lower bound of the range.
        last_day: Inclusive upper bound of the range.

    Returns:
        The overlapping periods as a :class:`~._window.PeriodWindow`, empty when
        none overlaps.

    Raises:
        PayCalendarError: *last_day* precedes *first_day*, which is a caller
            that has its bounds crossed rather than a range that happens to be
            empty -- the two are indistinguishable in the result and only one is
            a defect.
    """
    if last_day < first_day:
        raise PayCalendarError(
            f"overlapping() was asked for {first_day.isoformat()}.."
            f"{last_day.isoformat()}, which ends before it starts.  An "
            f"empty range and a crossed one both return no periods, so "
            f"the crossed one is refused rather than answered."
        )
    return PeriodWindow(
        periods=tuple(
            period for period in periods
            if period.start_date <= last_day and period.end_date >= first_day
        ),
    )


def current_and_future_window(
    periods: "tuple[DerivedPeriod, ...]", day: date,
) -> PeriodWindow:
    """Return every period of *periods* that has not ENDED before *day*.

    "The paychecks this owner has left", counting the one they are IN: a period
    qualifies when its ``end_date`` falls on or after *day*.  The rolling
    top-up's target is compared against this count -- "keep N ahead" counts the
    current period as one of the N -- and plan step **C4** moved the question
    here from a ``PayPeriod.end_date >= as_of`` predicate written in SQL
    (finding **P70**), so "a period ends the day before the next payday" is
    stated in :func:`~._derive.derive_periods` and nowhere else.

    **A SLICE of the tiling, so the result tiles**, exactly as
    :func:`index_window` and :func:`overlapping_window` do rather than as
    :func:`saved_window` does: the qualifying periods are a SUFFIX, because
    derived ends ascend with the paydays they are derived from, and a suffix of
    a tiling cannot carry the hole :class:`~._window.PeriodWindow` refuses.

    **It ANSWERS an empty window where :func:`overlapping_window` would
    REFUSE, and that difference is the whole reason it is its own producer.**
    Asking that one for ``[day, horizon()]`` is the same question -- every
    saved period starts on or before the horizon, so overlapping that range is
    exactly "ends on or after *day*" -- but it is the wrong door: a *day* past
    the horizon CROSSES those bounds, and a crossed range is a caller defect
    there because an empty result and a crossed one are indistinguishable in
    ITS answer.  Here they are not.  "Every paycheck has already ended" is an
    ordinary state, and it is precisely the one the rolling top-up exists to
    repair, so it is answered rather than raised.

    Args:
        periods: The calendar's periods, ``start_date`` ascending.
        day: The first day the window covers, inclusive.

    Returns:
        The periods whose ``end_date`` is on or after *day*, as a
        :class:`~._window.PeriodWindow`.  Empty when every period has ended
        before *day*, and for a calendar holding no payday at all.
    """
    return PeriodWindow(
        periods=tuple(period for period in periods if period.end_date >= day),
    )


def axis_window(
    periods: "tuple[DerivedPeriod, ...]",
    cadence_days: "int | None",
    user_id: int,
    first_day: date,
    last_day: date,
) -> PeriodWindow:
    """Return the spans covering ``[first_day, last_day]``, projecting past the horizon.

    **The replacement for ``growth_engine.generate_projection_periods``**,
    DELETED at plan step **C2-e**, which fabricated its own periods with ids
    numbered from 1 in the same integer namespace as real
    ``budget.pay_periods.id`` (ledger row **P17**) and at a hardcoded 14-day
    cadence that no call site overrode -- so an owner paid monthly was credited
    ``365/14`` paycheck contributions a year and shown ``$1,300,344.92`` against
    a true ``$711,385.70`` (row **P20**).  This projects at the OWNER's cadence,
    and a projected period says so with ``period_id = None``.

    **It COVERS the range it is given or it refuses; it never covers part of
    one** (ledger row **P23**, ruled 2026-08-14 by the developer).  A range
    opening below the first payday used to come back silently short --
    ``axis(2025-12-20, 2026-03-01)`` on a calendar opening 2026-01-02 left 13
    days in no period -- and a short axis is indistinguishable from a complete
    one in the result.  That is the argument :func:`overlapping_window` already
    makes for refusing a CROSSED range, applied to the other end.  Nothing is
    projected backwards (ruling 2026-08-10: before an owner's first payday
    there is no paycheck), so covering such a range is not an option and
    refusing is the only honest answer left.

    **The clamp a live consumer needs is its own function**, and deliberately
    not a branch in here: :func:`projection_axis_window` raises *first_day* to
    the opening bound for a pass that opens before the owner's first payday --
    an ordinary state, since the Generate form asks for "your next (or first)
    payday".  The pairing is the one
    :meth:`~._calendar.PayCalendar.filing_period` already makes against
    :meth:`~._calendar.PayCalendar.period_starting_on_or_before`: the strict
    search answers or does not, and the TOTAL companion beside it states its
    clamp in the open where a reader and a test can both see it.  Every
    projecting surface calls the companion, so **no caller in ``app/`` can reach
    the refusal below** -- it guards the value against a caller assembled by
    hand, the same standing :func:`overlapping_window`'s crossed-range refusal
    has.

    An EMPTY calendar is not that case and answers an empty window: an owner
    with no paydays at all has no partial coverage to hide, which is the same
    answer :func:`saved_window` gives them.

    Args:
        periods: The calendar's periods, ``start_date`` ascending.
        cadence_days: Days between paydays.  ``None`` is legal ONLY beside an
            empty *periods*, which is the pairing
            :func:`~._derive.derive_periods` enforces and
            :class:`~._calendar.PayCalendar` therefore cannot break; the guard
            below returns before this is read exactly when *periods* is empty,
            so the two agree.  A hand-assembled call passing ``None`` beside a
            payday reaches ``elapsed // None`` and a ``TypeError``, which is the
            precondition this function cannot check for itself.
        user_id: The owner these periods belong to, named in the refusal below
            so a traceback says whose schedule the range fell outside of.
        first_day: Inclusive lower bound of the range.  Must be at or after the
            first payday unless the calendar is empty.
        last_day: Inclusive upper bound of the range.

    Returns:
        The covering periods as a :class:`~._window.PeriodWindow`, saved where
        the schedule reaches and projected beyond it.  Empty only for an empty
        calendar -- for any other calendar the range is covered in full.

    Raises:
        PayCalendarError: *last_day* precedes *first_day*, or *first_day*
            precedes this calendar's first payday.
    """
    saved = overlapping_window(periods, first_day, last_day)
    opening = opening_payday(periods)
    if opening is not None and first_day < opening:
        raise PayCalendarError(
            f"axis() was asked for {first_day.isoformat()}.."
            f"{last_day.isoformat()}, which opens before user "
            f"{user_id}'s first payday ({opening.isoformat()}).  "
            f"Nothing is projected backwards -- before the first payday "
            f"there is no paycheck -- so the {(opening - first_day).days} "
            f"day(s) below it can only be left out, and an axis that "
            f"silently covers part of its range reads exactly like one "
            f"that covers all of it.  Call projection_axis() if raising "
            f"the range's start to the opening bound is what was meant."
        )
    horizon = final_covered_day(periods)
    if horizon is None or last_day <= horizon:
        return saved
    projected = []
    period = project_period_after(
        periods, cadence_days, horizon + timedelta(days=1),
    )
    while period.start_date <= last_day:
        if period.end_date >= first_day:
            projected.append(period)
        period = project_period_after(
            periods, cadence_days, period.end_date + timedelta(days=1),
        )
    return PeriodWindow(periods=saved.periods + tuple(projected))


def projection_axis_window(
    periods: "tuple[DerivedPeriod, ...]",
    cadence_days: "int | None",
    user_id: int,
    first_day: date,
    last_day: date,
) -> PeriodWindow:
    """Return the paychecks a FORWARD projection over ``[first_day, last_day]`` runs on.

    :func:`axis_window` with ONE clamp, and the same pairing
    :meth:`~._calendar.PayCalendar.filing_period` makes against
    :meth:`~._calendar.PayCalendar.period_starting_on_or_before` (plan step
    C2-e).  The strict producer refuses a range it can only half-cover; this is
    the TOTAL companion every projecting surface actually calls, and it states
    its clamp in the open rather than absorbing it.

    **The clamp exists for the owner whose first payday has not happened yet**,
    which is an ordinary state rather than a broken one: the Generate form asks
    for "your next (or first) payday", so a read pass whose ``as_of`` precedes
    the whole schedule is what a new owner looks like on the day they set it up.
    Three surfaces resolve a projection axis -- /retirement with its two lever
    solvers, /savings Horizon, and the /investment growth chart -- and each
    raising *first_day* itself would be three copies of one rule, the fourth of
    which a later consumer forgets.

    **Nothing is clamped at the other end**, and the asymmetry is the point.  A
    *last_day* past the schedule's horizon is exactly what :func:`axis_window`
    projects for, at the owner's own cadence; a *first_day* below the opening
    bound is a span no paycheck ever covered.

    **A CROSSED range is still refused**, and telling it apart from an emptied
    one is why the two tests below are separate (adversarial code review,
    2026-08-14).  A caller whose bounds are the wrong way round is a defect, and
    folding it into the empty answer is the hole :func:`overlapping_window`
    refuses to leave open one level down.  A range the CLAMP empties is a
    different thing entirely -- a horizon already behind the owner's first
    payday -- and is a real answer: it is the /retirement lever page's
    ``past_horizon`` state, where a shortfall exists but no paycheck remains for
    new money to land in.

    Args:
        periods: The calendar's periods, ``start_date`` ascending.
        cadence_days: Days between paydays, forwarded to :func:`axis_window`.
        user_id: The owner these periods belong to, forwarded for the crossed-
            range refusal's message.
        first_day: The day the projection window opens -- the day AFTER the
            balance it seeds from is valued.  Raised to the first payday when it
            precedes it.
        last_day: The last day the projection reaches.  May lie past the
            schedule's horizon, which is what the projection is for.

    Returns:
        The :class:`~._window.PeriodWindow` covering the (possibly raised)
        range, saved where the schedule reaches and projected at the owner's
        cadence beyond it.  **Empty** when the calendar holds no payday, and
        when the raised range would end before it starts.

    Raises:
        PayCalendarError: *last_day* precedes *first_day* as the caller supplied
            them.
    """
    if last_day < first_day:
        return axis_window(periods, cadence_days, user_id, first_day, last_day)
    opening = opening_payday(periods)
    if opening is None:
        return PeriodWindow(periods=())
    window_opens = max(first_day, opening)
    if last_day < window_opens:
        return PeriodWindow(periods=())
    return axis_window(periods, cadence_days, user_id, window_opens, last_day)
