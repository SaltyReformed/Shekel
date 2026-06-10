"""
Shekel Budget App -- Grid Routes (Main Budget View)

The primary view: a spreadsheet-like grid where columns are pay periods
and rows are income/expense line items.  Supports HTMX partial swaps
for inline editing, balance refresh, and carry forward.
"""

import logging
from collections import OrderedDict
from datetime import date
from decimal import Decimal
from typing import NamedTuple

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.category import Category
from app.models.ref import Status, TransactionType
from app.services import (
    balance_resolver,
    grid_view_service,
    pay_period_service,
)
from app.services.account_resolver import resolve_grid_account
from app.services.entry_service import build_entry_lists_dict, build_entry_sums_dict
from app.services.grid_view_service import RowKey
from app.services.scenario_resolver import get_baseline_scenario
from app.utils.auth_helpers import require_owner

logger = logging.getLogger(__name__)

grid_bp = Blueprint("grid", __name__)

# ``RowKey`` is re-exported from ``app.services.grid_view_service`` so
# existing test scaffolding that imports it from this module
# (``from app.routes.grid import RowKey`` in
# ``tests/test_routes/test_grid.py``) keeps working without an
# import-path migration.  The canonical definition lives in the
# service module per mobile-first v3 plan Commit 13 / D-B.
__all__ = ["RowKey", "grid_bp"]


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
    attribute (``ctx.scenario``) without binding a separate local per
    field from a tuple unpack -- the same pattern keeps the
    orchestrator's pylint ``R0914`` count below the project threshold
    after the mobile-follow-up Commit 8 / F-6 decomposition.

    Attributes:
        user_id: ID of the requesting user.  Drives the user-scoped
            pay-period queries -- notably the forward Plan-tab window
            rebuilt by :func:`_build_plan_view`.
        scenario: The baseline scenario for the requesting user.
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
        all_periods: All periods from anchor forward; the canonical
            producer :func:`balance_resolver.balances_for` walks this
            list to project balances.
    """

    user_id: int
    scenario: Scenario
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

    Returns:
        A :class:`_GridContext` on success, OR a rendered HTML string
        (the ``no_setup.html`` / ``no_periods.html`` early-return page)
        when the user lacks a baseline scenario or any current pay
        period.  The caller distinguishes via ``isinstance(result, str)``.
    """
    # Get the baseline scenario.
    scenario = get_baseline_scenario(user_id)
    if scenario is None:
        return render_template("grid/no_setup.html")

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
        scenario=scenario,
        account=account,
        num_periods=num_periods,
        start_offset=start_offset,
        current_period=current_period,
        periods=periods,
        # All periods from anchor forward -- the canonical balance
        # producer walks this list to project per-period end balances.
        all_periods=pay_period_service.get_all_periods(user_id),
    )


def _load_grid_transactions(account, scenario, all_periods):
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
        Transaction.scenario_id == scenario.id,
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


def _build_grid_balances(account, scenario, all_periods, amount_overrides=None):
    """Compute the anchor balance and the period-end balance projection.

    Routes through the canonical entries-aware producer
    :func:`balance_resolver.balances_for` (E-25 / Commit 5).  The
    producer owns its own query (it always ``selectinload``s entries
    so the entries-aware reduction in ``_entry_aware_amount`` applies
    unconditionally), which is the structural fix for CRIT-01 /
    F-009.  The grid additionally keeps its own ``all_transactions``
    query (in :func:`_load_grid_transactions`) for display purposes:
    the route needs the ``template`` eager-load for row-key generation
    and the same entries for ``entry_sums`` / cell rendering, neither
    of which is in ``balances_for``'s remit.  The double-query cost
    is a one-extra SELECT trade for the seam-removal guarantee.

    Returns the 3-tuple ``(balances, stale_anchor_warning,
    anchor_balance)``.  No-account state returns an empty balance map
    + ``False`` warning + zero anchor -- the grid template renders
    empty cells cleanly.  Post-Commit-3 every user with an account
    row has a resolvable anchor; the user-with-zero-accounts state
    lands here.
    """
    anchor_balance = (
        account.current_anchor_balance if account else Decimal("0.00")
    )

    if account is not None:
        balance_result = balance_resolver.balances_for(
            account, scenario.id, all_periods,
            amount_overrides=amount_overrides,
        )
        return (
            balance_result.balances,
            balance_result.stale_anchor_warning,
            anchor_balance,
        )

    return OrderedDict(), False, anchor_balance


def _build_grid_subtotals(account, scenario, periods, amount_overrides=None):
    """Compute per-period subtotals via the canonical entries-aware producer.

    Routing the on-screen subtotal row through
    :func:`balance_resolver.period_subtotals` (E-25 / Commit 10) closes
    F-002 Pair C / F-004 (Q-10): the same Projected-only,
    entries-aware formula now generates both the subtotal row and the
    balance row, so ``balances[p] - balances[p-1] ==
    subtotals[p].net`` by construction.  The pre-Commit-10 inline
    loop used raw ``txn.effective_amount`` and disagreed with the
    entries-aware balance row whenever a Projected envelope expense
    carried cleared/uncleared/credit entries.

    Uses the batch :func:`balance_resolver.period_subtotals` (one
    transaction query for the whole window), NOT a per-period
    :func:`balance_resolver.period_subtotal` loop -- the latter was an
    N+1 (one SELECT per visible column) over a transaction set the page
    had already loaded (DH-#36; ``database.md`` flags grid N+1
    especially).

    The :class:`balance_resolver.PeriodSubtotal` dataclass exposes
    ``.income``, ``.expense``, ``.net`` which the grid templates
    access by attribute (dict and dataclass behave identically
    through Jinja's attribute resolution).  Returns a dict keyed by
    ``period.id``; no-account state returns zero-valued
    ``PeriodSubtotal`` so template ``.income`` / ``.expense`` /
    ``.net`` access does not raise ``AttributeError`` and the
    rendered subtotals match the empty-balance projection.
    """
    if account is None:
        zero = balance_resolver.PeriodSubtotal(
            income=Decimal("0.00"),
            expense=Decimal("0.00"),
            net=Decimal("0.00"),
        )
        return {period.id: zero for period in periods}
    return balance_resolver.period_subtotals(
        account, scenario.id, periods, amount_overrides=amount_overrides,
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


def _build_plan_view(
    ctx, all_transactions, balances, all_categories, amount_overrides=None,
):
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
            anchor and its pay-period query) plus ``account`` and
            ``scenario`` (forwarded to the subtotal builder).
            ``account`` may be ``None`` for the user-with-zero-accounts
            edge case.
        all_transactions: The list already loaded by
            :func:`_load_grid_transactions`.  Re-used here instead of
            re-querying; ``_build_grid_row_data`` filters by visible
            window internally so the same list works for the wider
            Plan window.
        balances: The full anchor-forward balance map produced by
            :func:`_build_grid_balances`.  Sliced to plan periods
            without recomputing.
        all_categories: User's full category set (active + archived).
            Forwarded to the row-key builder so archived-category
            transactions still render.
        amount_overrides: Optional live projected-income overrides,
            forwarded to the subtotal builder so Plan subtotals match
            the rest of the grid's cells and balances.

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
          - ``plan_subtotals``: dict[period_id -> PeriodSubtotal].
          - ``plan_balances``: dict[period_id -> Decimal | None],
            sliced from the global balance map.
    """
    plan_periods = pay_period_service.get_periods_in_range(
        ctx.user_id, ctx.current_period.period_index, PLAN_WINDOW_PERIODS,
    )

    row_data = _build_grid_row_data(
        all_transactions, plan_periods, False, all_categories,
    )

    plan_subtotals = _build_grid_subtotals(
        ctx.account, ctx.scenario, plan_periods, amount_overrides,
    )

    plan_balances = {p.id: balances.get(p.id) for p in plan_periods}

    return {
        "plan_periods": plan_periods,
        "plan_income_row_keys": row_data.income_row_keys,
        "plan_expense_row_keys": row_data.expense_row_keys,
        "plan_matched_by_row_period": row_data.matched_by_row_period,
        "plan_subtotals": plan_subtotals,
        "plan_balances": plan_balances,
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

    ctx = _resolve_grid_context(
        user_id, request.args, current_user.settings,
    )
    if isinstance(ctx, str):
        return ctx

    all_transactions = _load_grid_transactions(
        ctx.account, ctx.scenario, ctx.all_periods,
    )
    # Workstream B: build the live projected-income override once from the
    # loaded transactions, annotate each row with the display amount, and
    # thread it into every balance / subtotal producer call so projected
    # salary income is recomputed live and shown consistently across the
    # grid's cells, subtotals, and balance projection.  ``live_estimated_amount``
    # is a transient (non-mapped) attribute the cell templates read with a
    # safe ``is defined`` fallback, so it never persists and never affects
    # render paths that do not set it.
    amount_overrides = balance_resolver.live_amount_overrides(
        ctx.account, ctx.scenario.id, all_transactions,
    )
    for txn in all_transactions:
        txn.live_estimated_amount = amount_overrides.get(
            txn.id, txn.estimated_amount,
        )
    balances, stale_anchor_warning, anchor_balance = _build_grid_balances(
        ctx.account, ctx.scenario, ctx.all_periods,
        amount_overrides=amount_overrides,
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
        ctx,
        all_transactions,
        balances,
        all_categories,
        amount_overrides,
    )

    return render_template(
        "grid/grid.html",
        scenario=ctx.scenario,
        account=ctx.account,
        periods=ctx.periods,
        current_period=ctx.current_period,
        balances=balances,
        subtotals=_build_grid_subtotals(
            ctx.account, ctx.scenario, ctx.periods,
            amount_overrides=amount_overrides,
        ),
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
        anchor_balance=anchor_balance,
        today=date.today(),
        all_periods=ctx.all_periods,
        low_balance_threshold=(
            current_user.settings.low_balance_threshold
            if current_user.settings
            and current_user.settings.low_balance_threshold is not None
            else 500
        ),
        stale_anchor_warning=stale_anchor_warning,
        entry_sums=row_data.entry_sums,
        entry_lists=row_data.entry_lists,
        matched_by_row_period=row_data.matched_by_row_period,
        **plan_view,
    )


@grid_bp.route("/create-baseline", methods=["POST"])
@login_required
@require_owner
def create_baseline():
    """Create a missing baseline scenario for the current user.

    Idempotent: if a baseline already exists, redirects without
    creating a duplicate.
    """
    existing = get_baseline_scenario(current_user.id)
    if existing:
        return redirect(url_for("grid.index"))

    scenario = Scenario(
        user_id=current_user.id,
        name="Baseline",
        is_baseline=True,
    )
    db.session.add(scenario)
    db.session.commit()

    logger.info(
        "action=create_baseline user_id=%s scenario_id=%s",
        current_user.id, scenario.id,
    )

    return redirect(url_for("grid.index"))


@grid_bp.route("/grid/balance-row")
@login_required
@require_owner
def balance_row():
    """HTMX partial: recalculate and return the balance summary row.

    Returns 204 No Content when the user has no baseline scenario or no
    current pay period.  The grid index route renders ``no_setup.html``
    / ``no_periods.html`` for those cases, so the HTMX partial swap on
    this endpoint has nothing to render -- returning 204 leaves the
    existing DOM untouched and avoids the ``AttributeError`` that
    dereferencing the missing scenario would have raised (F-099).
    """
    user_id = current_user.id

    scenario = get_baseline_scenario(user_id)
    if scenario is None:
        return "", 204

    account = resolve_grid_account(
        user_id, current_user.settings,
        request.args.get("account_id", type=int),
    )

    num_periods = request.args.get("periods", default=6, type=int)
    start_offset = request.args.get("offset", default=0, type=int)

    current_period = pay_period_service.get_current_period(user_id)
    if not current_period:
        return "", 204

    start_index = current_period.period_index + start_offset
    periods = pay_period_service.get_periods_in_range(user_id, start_index, num_periods)
    all_periods = pay_period_service.get_all_periods(user_id)

    # Balances via the canonical entries-aware producer (E-25 / Commit 5).
    # The producer owns the transaction query, so this HTMX partial no
    # longer needs its own ``selectinload(entries)`` query: that
    # responsibility moved into ``balance_resolver.balances_for``.
    if account is not None:
        balance_result = balance_resolver.balances_for(
            account, scenario.id, all_periods,
        )
        balances = balance_result.balances
    else:
        balances = OrderedDict()

    low_balance_threshold = (
        current_user.settings.low_balance_threshold
        if current_user.settings and current_user.settings.low_balance_threshold is not None
        else 500
    )

    return render_template(
        "grid/_balance_row.html",
        periods=periods,
        balances=balances,
        account=account,
        num_periods=num_periods,
        start_offset=start_offset,
        low_balance_threshold=low_balance_threshold,
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

    Returns 204 No Content -- a swap-nothing no-op that leaves the
    existing summary DOM untouched -- when the user has no baseline
    scenario, no ``period_id`` is supplied, or the period does not
    exist or belongs to another user.  204 (rather than 404) keeps an
    idempotent GET refresh from blanking the summary on a transient
    miss, mirroring :func:`balance_row`'s no-op contract.
    """
    user_id = current_user.id

    scenario = get_baseline_scenario(user_id)
    if scenario is None:
        return "", 204

    period_id = request.args.get("period_id", type=int)
    if period_id is None:
        return "", 204
    period = db.session.get(PayPeriod, period_id)
    if period is None or period.user_id != user_id:
        return "", 204

    account = resolve_grid_account(
        user_id, current_user.settings,
        request.args.get("account_id", type=int),
    )

    all_periods = pay_period_service.get_all_periods(user_id)
    if account is not None:
        balances = balance_resolver.balances_for(
            account, scenario.id, all_periods,
        ).balances
    else:
        balances = OrderedDict()

    subtotals = _build_grid_subtotals(account, scenario, [period])

    return render_template(
        "grid/_mobile_tp_summary.html",
        period=period,
        subtotals=subtotals,
        balances=balances,
        account=account,
        oob=True,
    )
