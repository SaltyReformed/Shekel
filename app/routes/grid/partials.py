"""
Shekel Budget App -- Grid route package: the three self-refreshing fragments.

The HTMX partials the grid page re-renders in place rather than reloading:
the sticky ``<tfoot>`` balance row and the two summary subtotal ``<tbody>``
sections (both on ``balanceChanged``), and the mobile "This Period" money
summary (on ``mobileCardSettled`` after a mobile / companion Mark Paid).

**Each one answers for the SAME window the page it is patching is showing**,
which is why they share :func:`_resolve_partial_window` rather than parsing
``periods`` / ``offset`` three times, and why every figure comes off the one
:func:`~app.routes.grid._shared._build_grid_view` call the full render also
reads (ruling R-K): a fragment computed by a second producer could disagree
with the row above it and the user would see the contradiction, not the cause.

All three answer ``204 No Content`` rather than ``404`` on a transient miss --
an idempotent GET refresh must leave a live DOM alone.
"""

from typing import NamedTuple

from flask import render_template, request
from flask_login import current_user, login_required

from app.models.account import Account
from app.services.account_resolver import resolve_grid_account
from app.services.balance_at import BalanceContext
from app.services.pay_calendar import PeriodWindow
from app.utils.auth_helpers import require_owner

from app.routes.grid._bp import grid_bp
from app.routes.grid._shared import (
    _accrual_row_label,
    _build_grid_view,
    _resolve_low_balance_threshold,
    _resolve_visible_window,
)


class _PartialBase(NamedTuple):
    """Scenario + grid account shared by every self-refresh grid partial.

    Produced by :func:`_resolve_partial_base`, the account-resolve prefix
    that all three self-refresh endpoints -- :func:`balance_row`,
    :func:`subtotal_rows`, and :func:`mobile_this_period_summary` -- share.
    The resolver's no-baseline ``None`` (and each route's 204 on it) moved to
    the application handler at plan step X-v2, ruling R-BW.

    Attributes:
        balance_ctx: The read pass's ``BalanceContext`` (scenario + as-of).
        account: The grid account (checking by default, or the user's
            preferred grid account), or ``None`` for the
            user-with-zero-accounts edge case.
    """

    balance_ctx: BalanceContext
    account: Account | None


def _resolve_partial_base(user_id):
    """Resolve the read pass + grid account shared by the grid partials.

    The account-resolve prefix every self-refresh grid partial performs
    identically.

    **The no-baseline guard this opened with is GONE, and its answer is now the
    whole app's** (plan step X-v2, ruling R-BW).  Returning ``None`` here so
    each caller could answer ``204`` was the right answer to the wrong-sized
    question: the seam raises :class:`~app.exceptions.BaselineMissingError` and
    one application-level handler returns ``204 No Content`` for ANY HTMX
    request, which is precisely this idempotent leave-the-DOM-alone refresh.
    These three endpoints behave identically; fifteen other surfaces stopped
    inventing their own answer.

    Args:
        user_id: ID of the requesting user.

    Returns:
        A :class:`_PartialBase`.  Never ``None``: the one state it used to
        report is answered above this route now.
    """
    balance_ctx = BalanceContext.build(user_id)
    account = resolve_grid_account(
        user_id, current_user.settings,
        request.args.get("account_id", type=int),
    )
    return _PartialBase(balance_ctx=balance_ctx, account=account)


class _PartialWindow(NamedTuple):
    """Resolved request context shared by the windowed grid partials.

    Produced by :func:`_resolve_partial_window` for the two desktop
    self-refresh endpoints -- :func:`balance_row` and
    :func:`subtotal_rows` -- which both answer "recompute the summary for
    the same ``periods`` / ``offset`` window the page is showing".
    Carrying it as a :class:`typing.NamedTuple` lets each route read
    fields by attribute (``window.periods``) without a parallel local per
    field, the same convention :class:`_GridContext` uses for
    :func:`index`.

    Attributes:
        balance_ctx: The read pass's ``BalanceContext`` (scenario + as-of).
        account: The grid account (checking by default, or the user's
            preferred grid account), or ``None`` for the
            user-with-zero-accounts edge case.
        num_periods: Count of visible pay-period columns (the ``periods``
            query param, default 6).
        start_offset: Offset added to the current period's
            ``period_index`` for the leftmost visible column (the
            ``offset`` query param, default 0).
        periods: The visible period slice, as a
            :class:`~app.services.pay_calendar.PeriodWindow` of length up to
            ``num_periods``.
    """

    balance_ctx: BalanceContext
    account: Account | None
    num_periods: int
    start_offset: int
    periods: PeriodWindow


def _resolve_partial_window(user_id):
    """Resolve the shared scenario / account / period-window context.

    Extracts the identical account-resolve,
    ``periods`` / ``offset`` parse, current-period guard, and range query
    that :func:`balance_row` and :func:`subtotal_rows` each performed
    inline (the two had copied the same ~20-line block).  Both endpoints
    recompute their summary for the same visible window, so the
    resolution is one definition shared by both.  Builds on
    :func:`_resolve_partial_base` so the scenario + account prefix is the
    same one :func:`mobile_this_period_summary` uses.

    **The window itself is resolved by
    :func:`~app.routes.grid._shared._resolve_visible_window`**, the same
    function ``page._resolve_grid_context`` calls (plan step C2-f2b).  That
    shared rule is what these endpoints most need: they exist to recompute the
    SAME window the page is showing, so a second copy of the resolution could
    answer for different columns than the render it is replacing -- and both
    halves would still look self-consistent.

    Args:
        user_id: ID of the requesting user.

    Returns:
        A :class:`_PartialWindow` on success, or ``None`` when the user has no
        current pay period -- the transient miss on which both callers return
        their 204 No Content no-op (an idempotent refresh that leaves the
        existing summary DOM untouched, never a 404).  The no-BASELINE half of
        that contract moved to the application handler at plan step X-v2, which
        answers any HTMX request in that state with the same 204.
    """
    base = _resolve_partial_base(user_id)
    num_periods = request.args.get("periods", default=6, type=int)
    start_offset = request.args.get("offset", default=0, type=int)

    resolved = _resolve_visible_window(
        base.balance_ctx, num_periods, start_offset,
    )
    if resolved is None:
        return None
    _current_period, periods = resolved

    return _PartialWindow(
        balance_ctx=base.balance_ctx,
        account=base.account,
        num_periods=num_periods,
        start_offset=start_offset,
        periods=periods,
    )


@grid_bp.route("/grid/balance-row")
@login_required
@require_owner
def balance_row():
    """HTMX partial: recalculate and return the balance summary row.

    Returns 204 No Content when the user has no current pay period: the grid
    index route renders ``no_periods.html`` for that case, so the HTMX partial
    swap on this endpoint has nothing to render, and 204 leaves the existing
    DOM untouched.  A user with no baseline scenario gets the same 204 from the
    application-level ``BaselineMissingError`` handler (plan step X-v2), which
    is where the ``AttributeError`` this route once guarded against (F-099) is
    now made impossible -- ``BalanceContext.scenario_id`` raises rather than
    handing out a ``None`` to dereference.
    """
    window = _resolve_partial_window(current_user.id)
    if window is None:
        return "", 204

    # The whole column set via the balance-at seam's kind-aware grid view,
    # which owns the live override map and the per-kind dispatch.  For
    # a modelled grid account this yields the modelled balance and the accrual /
    # contribution rows that explain it; an account that models nothing gets the
    # folded cash balance with both tiers at zero (plan step X-g3b).  The seam
    # answers over the owner's whole calendar and this reads the window's flags
    # off it, which is what keeps this refresh and the full-page render one
    # projection.
    view, _anchor = _build_grid_view(window.account, window.balance_ctx)

    return render_template(
        "grid/_balance_row.html",
        periods=window.periods,
        columns=view.columns,
        row_flags=view.row_flags(window.periods),
        accrual_label=_accrual_row_label(window.account),
        account=window.account,
        num_periods=window.num_periods,
        start_offset=window.start_offset,
        low_balance_threshold=_resolve_low_balance_threshold(),
    )


@grid_bp.route("/grid/subtotal-rows")
@login_required
@require_owner
def subtotal_rows():
    """HTMX partial: recompute and return both summary subtotal ``<tbody>``.

    The desktop grid's three summary rows (Total Income, Total
    Expenses, Net Cash Flow) live in two self-refreshing ``<tbody>``
    sections.  Only the income ``<tbody>`` fires this endpoint on
    ``balanceChanged from:body`` -- the same event-driven swap pattern
    :func:`balance_row` gives the sticky ``<tfoot>`` balance row.  Before
    this endpoint existed the summary rows only updated on a full page
    reload, so an inline amount edit or a mark-paid (which now triggers
    ``balanceChanged`` instead of ``gridRefresh`` on the regular desktop
    path) left them stale.

    ONE GET refreshes both sections.  The response renders the income
    ``<tbody>`` (which an ``outerHTML`` swap replaces in place) AND the
    expense ``<tbody>`` as an ``hx-swap-oob`` fragment -- a whole
    ``<tbody id=...>`` is a parseable out-of-band target, unlike a bare
    ``<tr>`` -- so the two summary blocks never need two separate GETs
    (which would double the fan-out and risk the ``RATELIMIT_DEFAULT``
    ceiling silently 429-ing the refresh).  Both ``<tbody>`` blocks read
    the same :func:`_build_grid_view` column set the index route and the
    balance row read, so the financial figures match exactly -- and they match
    by being the SAME rows, not by two producers agreeing (ruling R-K).

    Mirrors :func:`balance_row`'s auth, ownership, and param handling:
    ``@login_required`` + ``@require_owner``, the same
    ``account_id`` / ``periods`` / ``offset`` parse, and the same 204
    No Content no-op (rather than 404) when the user has no current pay
    period -- an idempotent GET refresh that leaves the existing summary DOM
    untouched on a transient miss.  A user with no baseline scenario gets the
    same 204 from the application handler (plan step X-v2).
    """
    window = _resolve_partial_window(current_user.id)
    if window is None:
        return "", 204

    # The same column set the grid index route and the balance row read --
    # never a re-derived inline loop -- so ``balance[p] - balance[p-1] ==
    # net[p] + period_timing[p] + book_vs_bank[p] + contribution[p] +
    # accrual[p]`` keeps holding
    # across the live swap because both sides are the same rows (E-25 /
    # Commit 10, rulings R-K / R-AH).  The window is the whole anchor-forward
    # set for the same reason
    # the index route passes it: a projection re-based on the visible slice
    # would answer a different question at the window's left edge, and asking
    # for one this endpoint then does not render would be an argument a caller
    # can get wrong.  This endpoint therefore does MORE work than it did
    # (measured 2026-07-26 on the prod-shape clone: 87.9 ms -> 165.6 ms, the
    # added cost being the balance walk), which is what buys the identity; the
    # pair of self-refresh GETs a ``balanceChanged`` fires still costs less in
    # total than before (360.2 ms -> 331.0 ms) because the balance row stopped
    # building a second override map.  Finding N-56 records the remaining
    # duplication.
    view, _anchor = _build_grid_view(window.account, window.balance_ctx)

    return render_template(
        "grid/_subtotal_rows.html",
        oob=True,
        periods=window.periods,
        columns=view.columns,
        account=window.account,
        num_periods=window.num_periods,
        start_offset=window.start_offset,
    )


@grid_bp.route("/grid/this-period-summary")
@login_required
@require_owner
def mobile_this_period_summary():
    """HTMX partial: the mobile "This Period" money summary for one period.

    Recomputes the period's Net Cash Flow + Projected Balance and the
    Income / Expense section-header totals, then returns
    ``grid/_mobile_tp_summary.html`` with ``oob=True`` so the response
    refreshes all four figures (the balance + net inline, the two
    header totals out-of-band) in a single swap.  The self-refreshing
    ``#mobile-tp-summary-<period_id>`` element on the mobile grid fires
    this on ``mobileCardSettled from:body`` after a mobile Mark Paid,
    which swaps one card in place rather than reloading the page.

    Owner-only (``@require_owner``): the companion view shows no
    subtotal / balance blocks, so it has nothing to refresh.

    Returns 204 No Content -- a swap-nothing no-op that leaves the existing
    summary DOM untouched -- when no ``period_id`` is supplied, or the period
    does not exist or belongs to another user.  204 (rather than 404) keeps an
    idempotent GET refresh from blanking the summary on a transient miss,
    mirroring :func:`balance_row`'s no-op contract.  A user with no baseline
    scenario gets the same 204 from the application-level
    ``BaselineMissingError`` handler (plan step X-v2) rather than from a guard
    here.

    **The OWNERSHIP check is STRUCTURAL since plan step C2-f2b.**  This route
    resolved the submitted id with ``db.session.get(PayPeriod, ...)`` and then
    compared ``period.user_id`` against the requester -- the IDOR guard written
    out by hand, in a place a later edit could drop it and no test would see
    the difference (both states answer 204).  It asks the read pass's own
    calendar now, which holds ONE owner's paydays, so another user's id is
    simply absent and there is no comparison left to forget.  The lookup is
    cheap: the calendar is a per-pass memo, so the column set below reads the
    same derivation rather than a second one.  *On the MISS path it is not
    free* -- an unknown or another owner's id now costs one calendar
    derivation before the 204, where the ORM lookup cost one primary-key
    ``session.get``.  Stated rather than glossed, because this docstring is
    the record of a security decision.
    """
    base = _resolve_partial_base(current_user.id)

    period_id = request.args.get("period_id", type=int)
    if period_id is None:
        return "", 204
    period = base.balance_ctx.calendar().period_by_id(period_id)
    if period is None:
        return "", 204

    # Kind-aware balance, subtotals and accrual from the ONE seam view (the
    # kind-correct-grid feature).  Since plan step X-g3b every kind reads the
    # modelled balance; an account that models no return resolves no tier, so
    # its figure IS the cash-flow running balance.
    view, _anchor = _build_grid_view(base.account, base.balance_ctx)

    return render_template(
        "grid/_mobile_tp_summary.html",
        period=period,
        columns=view.columns,
        period_row_flags=view.row_flags([period]),
        accrual_label=_accrual_row_label(base.account),
        account=base.account,
        oob=True,
    )
