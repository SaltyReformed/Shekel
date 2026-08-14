"""
Shekel Budget App -- Grid route package: the full-page render.

``GET /grid`` -- the primary view, a spreadsheet-like grid whose columns are
pay periods and whose rows are income / expense line items -- plus the
``POST /create-baseline`` repair door the no-baseline card posts to.

The three HTMX fragments this page then re-renders on its own events live in
:mod:`~app.routes.grid.partials`; the producers both halves read are in
:mod:`~app.routes.grid._shared`.
"""

import logging
from decimal import Decimal
from typing import NamedTuple

from flask import redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.ref import Status, TransactionType
from app.models.transaction import Transaction
from app.services import (
    baseline_service,
    grid_view_service,
    pay_period_admin,
    pay_period_service,
)
from app.services.account_resolver import resolve_grid_account
from app.services.balance_at import BalanceContext
from app.services.entry_service import build_entry_lists_dict, build_entry_sums_dict
from app.services.grid_view_service import RowKey
from app.utils.auth_helpers import require_owner
from app.utils.dates import display_today

from app.routes.grid._bp import grid_bp
from app.routes.grid._shared import (
    _accrual_row_label,
    _build_grid_view,
    _resolve_low_balance_threshold,
)

logger = logging.getLogger(__name__)


# Forward-looking window for the mobile "Plan" tab.  13 biweekly pay
# periods ~= 6 months, matching the desktop selector's `6M` option
# (`grid/grid.html:34`).  Fixed for phase 1; configurability is a
# follow-up.  Decoupled from the URL's `periods` / `offset` so Plan
# always answers "what does the next half-year look like from today?"
# regardless of how the user is navigating in This Period.
PLAN_WINDOW_PERIODS = 13


class _GridContext(NamedTuple):
    """Request-derived context for the grid view.

    Produced by :func:`_resolve_grid_context`.  Carrying this as a
    :class:`typing.NamedTuple` lets :func:`index` access fields via
    attribute (``ctx.balance_ctx``) without binding a separate local per
    field from a tuple unpack -- the same pattern keeps the
    orchestrator's pylint ``R0914`` count below the project threshold
    after the mobile-follow-up Commit 8 / F-6 decomposition.

    Attributes:
        user_id: ID of the requesting user.  Drives the user-scoped
            pay-period queries -- notably the forward Plan-tab window
            rebuilt by :func:`_build_plan_view`.
        balance_ctx: The read pass's ``BalanceContext`` (scenario + as-of).
        account: The grid account (checking by default, or the user's
            preferred grid account), or ``None`` when the user has no
            account rows at all (the post-Commit-3 user-with-zero-
            accounts edge case).
        num_periods: Count of visible pay-period columns.
        start_offset: Offset added to the current period's
            ``period_index`` for the leftmost visible column.
        current_period: The user's current pay period (the natural
            leftmost column when ``start_offset == 0``).
        periods: The visible period slice (length ``num_periods``).
        all_periods: ALL of the user's pay periods; the balance-at seam's
            grid view folds this list into one column per period.  Not
            anchor-forward: the fold answers a pre-anchor period with the
            balance in force then (plan step X-c2b2), and each period is
            valued off its own span.
    """

    user_id: int
    balance_ctx: BalanceContext
    account: Account | None
    num_periods: int
    start_offset: int
    current_period: PayPeriod
    periods: list[PayPeriod]
    all_periods: list[PayPeriod]


def _resolve_grid_context(user_id, request_args, settings):
    """Resolve scenario, account, and period range from the request.

    Args:
        user_id: ID of the requesting user.
        request_args: Flask ``request.args`` (or any compatible
            multidict).  Parsed for ``account_id``, ``periods``, and
            ``offset``.
        settings: ``current_user.settings`` (a ``UserSettings`` row)
            or ``None``.  Source of the default ``grid_default_periods``
            when the request omits ``periods``.

    A user with no baseline scenario is NOT handled here (plan step X-v2,
    ruling R-BW).  This route used to render ``errors/no_baseline.html`` itself
    and was the only surface in the app that did, while `/savings` fabricated a
    ``$0.00`` hero and three other pages returned a 500.  The raise comes from
    the caller's :func:`_load_grid_transactions`, which asks the context for
    the scenario id it scopes its query with; ONE application-level handler
    renders that same card, so every surface answers the state identically and
    this route states no policy about it.

    **Its ORDER against the no-periods answer changed with it**: a user missing
    both now sees ``no_periods.html``, where the deleted guard put the baseline
    card first.  Neither is a dead end (the card is one click from any balance
    page), and a user with no periods and no baseline needs both repairs.

    Returns:
        A :class:`_GridContext` on success, OR a rendered HTML string (the
        ``no_periods.html`` early-return page) when the user has no current pay
        period.  The caller distinguishes via ``isinstance(result, str)``.
    """
    balance_ctx = BalanceContext.build(user_id)

    # Get the grid account (checking by default, or user preference).
    account = resolve_grid_account(
        user_id, settings, request_args.get("account_id", type=int),
    )

    # Determine the visible period range.
    num_periods = request_args.get(
        "periods",
        default=(settings.grid_default_periods if settings else 6),
        type=int,
    )
    start_offset = request_args.get("offset", default=0, type=int)

    # Find the current period as the baseline starting point.
    current_period = pay_period_service.get_current_period(user_id)
    if current_period is None:
        return render_template("grid/no_periods.html")

    # Load the visible period slice (offset applied to current).
    periods = pay_period_service.get_periods_in_range(
        user_id, current_period.period_index + start_offset, num_periods,
    )
    if not periods:
        return render_template("grid/no_periods.html")

    return _GridContext(
        user_id=user_id,
        balance_ctx=balance_ctx,
        account=account,
        num_periods=num_periods,
        start_offset=start_offset,
        current_period=current_period,
        periods=periods,
        # ALL of the user's periods -- the fold answers every one of them,
        # and each render window is a slice of this domain.
        all_periods=pay_period_service.get_all_periods(user_id),
    )


def _load_grid_transactions(account, balance_ctx, all_periods):
    """Load all transactions for the visible account and scenario.

    Every transaction has ``account_id`` NOT NULL, so filtering by
    ``account_id`` ensures the grid only shows income/expenses
    belonging to the selected account.  Without this filter, checking
    transactions would appear on the savings grid and corrupt the
    projected balance.  ``account=None`` (the user-with-zero-accounts
    edge case) omits the account filter so the resulting list is
    naturally empty.

    Eager-loads ``entries`` (for entry-sum rendering) and ``template``
    (for row-key generation) -- these are read in the row-data helper
    and the cell template, so the eager-load avoids per-row N+1
    queries in the grid render loop.

    Returns the list of matching :class:`Transaction` rows.
    """
    period_ids = [p.id for p in all_periods]
    txn_filters = [
        Transaction.pay_period_id.in_(period_ids),
        Transaction.scenario_id == balance_ctx.scenario_id,
        Transaction.is_deleted.is_(False),
    ]
    if account:
        txn_filters.append(Transaction.account_id == account.id)
    return (
        db.session.query(Transaction)
        .options(
            selectinload(Transaction.entries),
            selectinload(Transaction.template),
        )
        .filter(*txn_filters)
        .all()
    )


class _GridRowData(NamedTuple):
    """Row-render values produced by :func:`_build_grid_row_data`.

    The six fields are the grid's per-render "row contract": they are
    produced together and spliced together into the ``grid/grid.html``
    render context, so carrying them as a :class:`typing.NamedTuple`
    (rather than a six-tuple unpacked into six parallel locals) keeps
    both :func:`index` and :func:`_build_plan_view` under pylint's
    ``R0914`` local-count threshold and names each value at the call
    site.

    Attributes:
        income_row_keys: Ordered income-section row keys for the row
            window (the visible window, or the full projection when
            ``show_all``).
        expense_row_keys: Ordered expense-section row keys.
        matched_by_row_period: ``(category_id, template_id, txn_name,
            period_id) -> matched transactions`` index read by the cell
            template.
        entry_sums: Pre-computed tracked-progress map (``{txn_id ->
            sums}``) for the cell template's "spent / budget" display.
        entry_lists: Pre-rendered inline mobile entries list per
            envelope card (``{txn_id -> list data}``), computed
            server-side to avoid per-card HTMX fan-out.
    """

    income_row_keys: list[RowKey]
    expense_row_keys: list[RowKey]
    matched_by_row_period: dict[tuple[int, int | None, str, int], list[Transaction]]
    entry_sums: dict[int, dict]
    entry_lists: dict[int, dict]


def _build_grid_row_data(transactions, periods, show_all, all_categories):
    """Build row keys, the (row_key, period) match index, and entry sums.

    Row keys + the (row_key, period) -> matched-transactions dict are
    produced by the pure :mod:`app.services.grid_view_service`.  The
    service is also called from :func:`app.routes.companion.index` so
    the owner mobile grid and the companion view share one definition
    of the row-key dedup, sort order, and cell-matching predicate
    (mobile-first v3 plan Commit 13 / D-B).

    Row generation is scoped to the visible window by default so the
    grid stays uncluttered when planning far in advance.  ``show_all``
    opts back in to the full forward projection for full-picture
    review.  Balance math, cell matching, and subtotals work off the
    un-filtered ``transactions`` list -- any txn hidden from row-key
    generation has ``pay_period_id`` outside the visible window, so
    it contributes $0 to every visible-period subtotal and its cells
    were never going to render.

    Returns a :class:`_GridRowData` carrying ``income_row_keys``,
    ``expense_row_keys``, ``matched_by_row_period``, ``entry_sums``, and
    ``entry_lists``.  ``entry_sums`` is the pre-computed tracked-progress
    map for the cell template's "spent / budget" display.
    """
    if show_all:
        row_source_txns = transactions
    else:
        visible_period_ids = {p.id for p in periods}
        row_source_txns = [
            t for t in transactions
            if t.pay_period_id in visible_period_ids
        ]

    income_row_keys = grid_view_service.build_row_keys(
        row_source_txns, all_categories, is_income_section=True,
    )
    expense_row_keys = grid_view_service.build_row_keys(
        row_source_txns, all_categories, is_income_section=False,
    )
    matched_by_row_period = grid_view_service.build_matched_by_row_period(
        income_row_keys, expense_row_keys, periods, transactions,
    )

    entry_sums = build_entry_sums_dict(transactions)
    # Pre-render context for the inline mobile entries list on envelope
    # cards.  Computed here (server-side) rather than via per-card HTMX
    # ``hx-trigger="load"`` fan-out to keep one grid page load from
    # blowing past the ``RATELIMIT_DEFAULT`` ceiling of "30 per minute"
    # on the entries endpoint -- with 6 visible periods and ~10 envelope
    # templates each, the lazy-load shape generated ~60 parallel GETs
    # and the over-limit cards stuck on the loading spinner forever.
    entry_lists = build_entry_lists_dict(transactions)

    return _GridRowData(
        income_row_keys=income_row_keys,
        expense_row_keys=expense_row_keys,
        matched_by_row_period=matched_by_row_period,
        entry_sums=entry_sums,
        entry_lists=entry_lists,
    )


def _build_plan_view(ctx, all_transactions, grid_view, all_categories):
    """Build the read-only "Plan" tab context window.

    The Plan tab on the mobile grid answers "what does the next half-
    year look like from today?" regardless of how the user is
    navigating in This Period (which can leave the URL at
    ``?periods=1&offset=N``).  This helper computes a parallel data
    slice anchored at ``current_period`` and walking forward
    :data:`PLAN_WINDOW_PERIODS` periods.

    No entry sums or entry lists are computed -- Plan renders future
    periods read-only and envelope entries are by design a current /
    past concept.  The interactive helper :func:`_build_grid_row_data`
    still produces those values for the rest of the page; we discard
    them here.

    Args:
        ctx: The :class:`_GridContext` for this request.  Supplies
            ``user_id`` and ``current_period`` (the plan window's
            anchor and its pay-period query).
        all_transactions: The list already loaded by
            :func:`_load_grid_transactions`.  Re-used here instead of
            re-querying; ``_build_grid_row_data`` filters by visible
            window internally so the same list works for the wider
            Plan window.
        grid_view: The :class:`~app.services.balance_at.GridBalanceView`
            produced by :func:`_build_grid_view`.  Its columns are SLICED to
            the plan periods -- never recomputed, which is what keeps the Plan
            recap and the This Period card two windows onto one projection
            rather than two projections.
        all_categories: User's full category set (active + archived).
            Forwarded to the row-key builder so archived-category
            transactions still render.

    Returns:
        Dict with six ``plan_*`` keys ready to splice into the
        ``render_template`` kwargs of :func:`index`:

          - ``plan_periods``: list[PayPeriod], up to
            :data:`PLAN_WINDOW_PERIODS` long starting at
            ``current_period``.  May be shorter when the user has
            fewer remaining generated periods.
          - ``plan_income_row_keys`` / ``plan_expense_row_keys``:
            row-key lists scoped to the plan window.
          - ``plan_matched_by_row_period``: same shape as the
            interactive ``matched_by_row_period`` -- keys are
            ``(category_id, template_id, txn_name, period_id)``.
          - ``plan_columns``: dict[period_id ->
            :class:`~app.services.balance_at.GridColumn`], the view's own
            columns for the plan window.
          - ``plan_row_flags``: the window's
            :class:`~app.services.balance_at.GridRowFlags` (ruling R-O's
            conditional-row rule, evaluated for THIS window rather than the
            visible one -- the Plan tab reaches periods the grid may not
            show).
    """
    plan_periods = pay_period_service.get_periods_in_range(
        ctx.user_id, ctx.current_period.period_index, PLAN_WINDOW_PERIODS,
    )

    row_data = _build_grid_row_data(
        all_transactions, plan_periods, False, all_categories,
    )

    return {
        "plan_periods": plan_periods,
        "plan_income_row_keys": row_data.income_row_keys,
        "plan_expense_row_keys": row_data.expense_row_keys,
        "plan_matched_by_row_period": row_data.matched_by_row_period,
        "plan_columns": {
            p.id: grid_view.columns[p.id]
            for p in plan_periods if p.id in grid_view.columns
        },
        "plan_row_flags": grid_view.row_flags(plan_periods),
    }


@grid_bp.route("/grid")
@login_required
@require_owner
def index():
    """Render the full budget grid page.

    Loads the current period as the leftmost column with future
    periods extending to the right.  The number of visible periods is
    controlled by query params or user settings.  Orchestrates
    :func:`_resolve_grid_context` (period range + early returns),
    :func:`_load_grid_transactions`, :func:`_build_grid_balances`,
    :func:`_build_grid_subtotals`, and :func:`_build_grid_row_data`,
    then dispatches to ``grid/grid.html``.
    """
    user_id = current_user.id

    # Continuous rolling window: top up before resolving the grid so any
    # newly generated periods are visible this request.  A no-op (one
    # count, no lock) when rolling is disabled; commits only when periods
    # were actually created.
    if pay_period_admin.top_up_rolling_window(user_id):
        db.session.commit()

    ctx = _resolve_grid_context(
        user_id, request.args, current_user.settings,
    )
    if isinstance(ctx, str):
        return ctx

    all_transactions = _load_grid_transactions(
        ctx.account, ctx.balance_ctx, ctx.all_periods,
    )
    grid_view, anchor = _build_grid_view(ctx.account, ctx.balance_ctx)
    # Workstream B: annotate each row with the live display amount, read back
    # off the map the SEAM built for its own projection (ruling R-Q) rather
    # than a second one built here.  The two were "provably identical" by
    # argument -- both filter the same account/scenario rows through
    # ``is_projected`` -- and now they are the same object, so a cell and the
    # balance row cannot price one row differently.  ``live_estimated_amount``
    # is a transient (non-mapped) attribute the cell templates read with a
    # safe ``is defined`` fallback, so it never persists and never affects
    # render paths that do not set it.
    for txn in all_transactions:
        txn.live_estimated_amount = grid_view.amount_overrides.get(
            txn.id, txn.estimated_amount,
        )

    # Load ALL categories (including archived) for row-key building so
    # transactions with archived categories still render correctly;
    # the Add Transaction modal dropdown filters to active only.
    all_categories = (
        db.session.query(Category)
        .filter_by(user_id=user_id)
        .order_by(Category.group_name, Category.item_name)
        .all()
    )
    show_all = request.args.get("show_all", type=int) == 1

    row_data = _build_grid_row_data(
        all_transactions, ctx.periods, show_all, all_categories,
    )

    # Build the parallel context for the mobile "Plan" tab.  Decoupled
    # from ctx.periods so a `?periods=1&offset=N` URL (driven by the
    # This Period arrow nav) does not starve Plan of forward visibility.
    plan_view = _build_plan_view(
        ctx, all_transactions, grid_view, all_categories,
    )

    return render_template(
        "grid/grid.html",
        # The ID, not the Scenario ROW (plan step X-v2): the template needs
        # exactly the id for its hidden create-form field, and the two sibling
        # create fragments already take ``scenario_id``.  Passing the nullable
        # model handed a template an object it dereferenced as
        # ``{{ scenario.id }}`` -- a Jinja ``UndefinedError`` waiting on a state
        # the seam now answers before the render, and one more reader of the
        # nullable this step exists to stop handing out.
        scenario_id=ctx.balance_ctx.scenario_id,
        account=ctx.account,
        periods=ctx.periods,
        current_period=ctx.current_period,
        columns=grid_view.columns,
        row_flags=grid_view.row_flags(ctx.periods),
        # The mobile "This Period" card is a ONE-period surface -- it renders
        # ``periods[0]`` -- so ruling R-O's "at least one visible column" is
        # that column alone.  Scoping it here rather than reusing the desktop
        # window's flags is what keeps the initial include and the
        # ``mobileCardSettled`` refresh (which sees one period and no window)
        # from disagreeing about whether a row is on screen.
        period_row_flags=grid_view.row_flags(ctx.periods[:1]),
        # ONE label for the three surfaces this render feeds -- the desktop
        # <tfoot>, the mobile This Period card and the Plan recap all read it
        # from this context (ruling R-AI / R-P), so the form factors cannot name
        # the same row differently.
        accrual_label=_accrual_row_label(ctx.account),
        categories=[c for c in all_categories if c.is_active],
        income_row_keys=row_data.income_row_keys,
        expense_row_keys=row_data.expense_row_keys,
        statuses=db.session.query(Status).all(),
        transaction_types=db.session.query(TransactionType).all(),
        num_periods=ctx.num_periods,
        start_offset=ctx.start_offset,
        show_all=show_all,
        col_size=(
            "wide" if ctx.num_periods <= 6
            else "medium" if ctx.num_periods <= 13
            else "compact"
        ),
        anchor_balance=anchor.balance if anchor is not None else Decimal("0.00"),
        anchor_as_of=anchor.observed_on if anchor is not None else None,
        # The USER's today, not the server's UTC one: it drives the
        # past-period column styling AND the add-entry form's hidden
        # ``entry_date`` default, which the entry service refuses on that same
        # display clock (ruling R-M).  Two clocks here would let a UTC-running
        # process stamp tomorrow's date for the evening hours the frames
        # disagree, and the app's own form would post a value its own guard
        # rejects.
        today=display_today(),
        all_periods=ctx.all_periods,
        low_balance_threshold=_resolve_low_balance_threshold(),
        entry_sums=row_data.entry_sums,
        entry_lists=row_data.entry_lists,
        matched_by_row_period=row_data.matched_by_row_period,
        **plan_view,
    )


@grid_bp.route("/create-baseline", methods=["POST"])
@login_required
@require_owner
def create_baseline():
    """Create a missing baseline scenario, idempotently.

    Thin wrapper over
    :func:`app.services.baseline_service.create_baseline_scenario`, which owns
    the create-or-noop AND the recovery of both posting ledgers that had no
    scenario to post into while the baseline was missing.  ``None`` means the
    user already had one, so nothing was created and nothing is logged.
    """
    scenario = baseline_service.create_baseline_scenario(current_user.id)
    if scenario is not None:
        logger.info(
            "action=create_baseline user_id=%s scenario_id=%s",
            current_user.id, scenario.id,
        )
    return redirect(url_for("grid.index"))
