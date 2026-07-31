"""Balance-at-T seam -- the CASH-FLOW view (no per-kind dispatch).

The single-account cash-flow surfaces -- the budget grid, the dashboard pulse,
the analytics calendar, the cash detail page -- read an account's pure
transaction running-balance, NOT its kind-correct balance (see the package
docstring's "Two views, one seam").  These three entries are the seam's only
way to obtain that view.

**All three are one fold read at three grains** (plan step X-c2b2).  A period
map, a scalar at a date, and a day-by-day series are the SAME running total
(:mod:`app.services.balance_at._cash_fold`) sampled at period ends, at one
date, and at every date -- so they cannot disagree.  Before the cutover they
were three producers: the map carried an anchor forward over still-Projected
rows only, the scalar re-walked to a date with its own entry-date window, and
the daily series distributed the same rows over days -- and on the real
Checking account the scalar and the series stood ``$15.96`` apart on the day
before this commit (``$246.36`` at the worst day of the current period, finding
cash D2), while both dropped every row settled after the last balance assertion
(``$2,108.15`` invisible at that instant, finding cash D1) and answered a
pre-anchor date by fabricating today's balance or omitting the period entirely
(finding cash D3 / B-18).  One total fold subsumes all three.
"""

from collections import OrderedDict
from datetime import date, datetime, timedelta
from decimal import Decimal

from app.models.account import Account
from ._context import BalanceContext

from . import _cash_fold
from ._inputs import _require_scenario

_ONE_DAY = timedelta(days=1)


def _require_civil_date(entry: str, **dates: object) -> None:
    """Refuse anything that is not exactly a civil :class:`datetime.date`.

    **A ``datetime`` is refused, and that is the whole reason this is a
    function.**  ``datetime`` SUBCLASSES ``date``, so the obvious
    ``isinstance(value, date)`` accepts one -- the guard reads like a type
    check and is not one.  The fold's step boundaries are civil dates, so a
    ``datetime`` reaching them dies inside ``bisect_right`` with
    ``'<' not supported between instances of 'datetime.datetime' and
    'datetime.date'``: a real failure, but one whose traceback names a
    bisect rather than the caller's argument, three layers from the mistake.

    It is expressed as "a date that is NOT a datetime" rather than an exact
    type test, because the suite's ``freeze_today`` clock hands the producers
    its own ``date`` SUBCLASS -- a legitimate civil date that an exact test
    would reject, turning a guard against one wrong type into a guard against
    every subclass.

    Saying the type this precisely also states the contract these entries
    hold: a balance is asked for on a DAY.  An instant is the walk's concern
    (the assertion partition), never a valuation date's.

    Args:
        entry: The seam entry's name, for the message.
        **dates: The argument name -> value pairs to check, in order.

    Raises:
        TypeError: On the first value that is not exactly a ``date``.
    """
    for label, value in dates.items():
        if not isinstance(value, date) or isinstance(value, datetime):
            raise TypeError(
                f"{entry} expects a civil datetime.date for {label}, "
                f"got {type(value).__name__} {value!r}"
            )


def cash_balance_map(
    account: Account, ctx: BalanceContext, periods: list,
) -> "OrderedDict[int, Decimal]":
    """Return one account's cash-flow running balance across *periods*.

    The cash-flow view: the account's projected end balance per period as a
    pure transaction running-balance, with NO per-kind dispatch.  This is what
    the single-account cash-flow surfaces show -- the dashboard pulse chart and
    the cash detail page -- where the balance row must reconcile with the
    account's own transaction rows and subtotal row on the same screen.

    **The budget grid was on this list until plan step X-g3b and is not any
    more** (ruling R-W): it reads :func:`~app.services.balance_at.grid_balance_view`,
    which answers a modelled account its MODELLED balance.  A reader that wants
    "what does the grid show" must call that entry -- for a modelled kind the
    two now answer differently by design, and this one would look right while
    proving nothing.

    Contrast with :func:`~app.services.balance_at.balance_map`, the
    KIND-CORRECT view: for an interest-bearing (HYSA), loan, investment, or
    property account ``balance_map`` dispatches to that kind's engine (accruing
    interest, walking an amortization schedule, compounding growth /
    appreciation) -- which is what the net-worth surfaces want, and what a
    cash-flow surface can only carry if the modelled movement is EXPLAINED on
    screen beside it.  The reason this entry once gave -- "accruing interest
    into the grid's balance row while its subtotal row stays transaction-based
    would leave a balance change the rows on screen cannot explain" -- was
    answered rather than overruled: ruling R-K put the explaining rows there
    (the accrual and the contribution), so the grid moved to the modelled
    balance at plan step X-g3b.  The surfaces still on THIS entry have no such
    rows, so they ask for the cash-flow balance of whatever account they are
    pointed at, regardless of its kind.

    **The one kind they are never pointed at is AMORTIZING, and that is a
    gate rather than a coincidence.**  A loan's balance is not a
    transaction sum (finding B-3), so every resolver feeding these entries
    refuses one at the source: ``resolve_grid_account`` since ruling D4 /
    plan step A1 (grid, dashboard, pulse), ``resolve_analytics_account``
    since plan step X-a1 (the calendar -- finding N-38), and the cash
    detail page's own ``_cash_detail_wrong_type`` 404.  These producers
    therefore stay TOTAL and kind-blind by design, and no screen can ask
    them a question only ``balance_at.balance_at`` can answer.

    **EVERY requested period is in the result** (plan step X-c2b2).  The
    retired producer projected forward from the anchor and omitted every
    pre-anchor period, so a caller had to treat a missing key as "no balance";
    the fold replays every assertion, so a past period answers with the balance
    in force THEN.  Callers that skipped missing keys are unaffected -- there
    are none left to skip.

    Args:
        account: The account whose cash-flow balance to project.  Its
            ``user_id`` scopes the live salary override; its kind is NOT
            consulted (ruling R-J).
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
            Its ``as_of`` is the reader's NOW -- what decides a still-projected
            row cannot already have happened (ruling R-G) -- NOT a valuation
            date; each period is valued at its own ``end_date``.
        periods: The pay periods to project over, in display order.  They need
            not be contiguous and need not start at the account's anchor.

    Returns:
        ``OrderedDict`` period_id -> cent-quantized ``Decimal``, in the order
        *periods* was given.

    Raises:
        BaselineMissingError: When ``scenario`` is None.  A ``ValueError``
            subclass; ONE application-level handler answers it (plan step
            X-v2, ruling R-BW), so no caller pre-checks.
    """
    _require_scenario(ctx)
    return _cash_fold.cash_period_balances(
        account, ctx.scenario_id, ctx.as_of, periods,
    )


def cash_balance_at(
    account: Account, ctx: BalanceContext, as_of: date,
) -> Decimal:
    """Return one account's cash-flow balance as of a calendar date *as_of*.

    The scalar cash-flow view -- the date-precise counterpart of
    :func:`cash_balance_map`, and literally the same fold read at one date, so
    ``cash_balance_at(account, ctx, P.end_date)`` equals
    ``cash_balance_map(account, ctx, [... P ...])[P.id]`` by construction rather
    than by a test.  Used by the calendar's month-end balance, which must
    reconcile with the day cells it renders for the same month.

    Like :func:`cash_balance_map`, this does NOT dispatch by kind: it is
    the cash-flow balance of whatever account the surface points at (the
    calendar's account can be any kind via an explicit ``account_id``).
    The KIND-CORRECT scalar is :func:`~app.services.balance_at.balance_at`.

    **Two dates, deliberately distinct.**  ``ctx.as_of`` is the reader's NOW --
    the floor ruling R-G clamps a still-projected row's landing day up to --
    while *as_of* is the VALUATION date, which may be long past (a historical
    read) or far future (a projection).  A past valuation date now answers with
    the balance the account really held then, replayed from its assertions,
    rather than with today's balance fabricated backwards (finding B-18).

    Args:
        account: The account to value.  Its kind is NOT consulted.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (its scenario scopes the fold; its ``as_of`` is the reader's NOW).
        as_of: The calendar date to value the account at.

    Returns:
        The cent-quantized ``Decimal`` cash-flow balance at *as_of*.

    Raises:
        BaselineMissingError: When ``scenario`` is None.  A ``ValueError``
            subclass; ONE application-level handler answers it (plan step
            X-v2, ruling R-BW), so no caller pre-checks.
        TypeError: When ``as_of`` is not a civil :class:`datetime.date` -- a
            ``datetime`` INCLUDED (see :func:`_require_civil_date`).
    """
    _require_scenario(ctx)
    _require_civil_date("cash_balance_at", as_of=as_of)
    return _cash_fold.fold_cash_balances(
        account, ctx.scenario_id, ctx.as_of, [as_of],
    )[as_of]


def cash_daily_balance_series(
    account: Account,
    ctx: BalanceContext,
    first_day: date,
    last_day: date,
) -> "OrderedDict[date, Decimal]":
    """Return one account's projected end-of-day cash-flow balance per day.

    The daily-granularity cash-flow view -- the same fold as
    :func:`cash_balance_at`, sampled at every day of the range instead of one,
    which is what makes the calendar's running-balance line reconcile with the
    other CASH-basis surfaces at every period end (the grid left that set at
    plan step X-g3b, and the calendar can be pointed at a modelled account by
    explicit ``account_id`` -- finding N-87 records the divergence)::

        series[P.end_date] == cash_balance_at(account, ctx, P.end_date)

    That identity used to be a claim two producers had to keep true (the series
    distributed a period's still-Projected rows over their attribution days
    while the scalar re-walked to the date through a different entry-date
    window, and they measured ``$15.96`` apart on the real Checking account);
    it is now a property of reading one running total twice.

    Like :func:`cash_balance_at` this does NOT dispatch by kind: it is the
    cash-flow balance of whatever account the surface points at (the
    calendar's account can be any kind via an explicit ``account_id``).  Used
    by the analytics calendar's flow strip and day-cell end-of-day balances.

    Args:
        account: The account to project.  Its kind is NOT consulted; must be
            session-attached.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (its scenario scopes the fold; its ``as_of`` is the reader's NOW).
        first_day: Inclusive first calendar day of the range.
        last_day: Inclusive last calendar day of the range.

    Returns:
        An ``OrderedDict`` mapping each calendar ``date`` in the inclusive
        range (ascending) to its projected end-of-day cash-flow balance,
        quantized to cents.  An inverted range yields an empty map.

    Raises:
        BaselineMissingError: When ``scenario`` is None.  A ``ValueError``
            subclass; ONE application-level handler answers it (plan step
            X-v2, ruling R-BW), so no caller pre-checks.
        TypeError: When ``first_day`` / ``last_day`` are not civil
            :class:`datetime.date` values -- a ``datetime`` INCLUDED (see
            :func:`_require_civil_date`).
    """
    _require_scenario(ctx)
    _require_civil_date(
        "cash_daily_balance_series", first_day=first_day, last_day=last_day,
    )
    if last_day < first_day:
        return OrderedDict()

    days: list[date] = []
    day = first_day
    while day <= last_day:
        days.append(day)
        day += _ONE_DAY

    folded = _cash_fold.fold_cash_balances(
        account, ctx.scenario_id, ctx.as_of, days,
    )
    return OrderedDict((day, folded[day]) for day in days)
