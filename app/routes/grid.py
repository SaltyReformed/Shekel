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

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.utils.auth_helpers import require_owner
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.category import Category
from app.models.ref import Status, TransactionType
from app.services import balance_resolver, grid_view_service, pay_period_service
from app.services.account_resolver import resolve_grid_account
from app.services.entry_service import build_entry_sums_dict
from app.services.grid_view_service import RowKey
from app.services.scenario_resolver import get_baseline_scenario

logger = logging.getLogger(__name__)

grid_bp = Blueprint("grid", __name__)

# ``RowKey`` is re-exported from ``app.services.grid_view_service`` so
# existing test scaffolding that imports it from this module
# (``from app.routes.grid import RowKey`` in
# ``tests/test_routes/test_grid.py``) keeps working without an
# import-path migration.  The canonical definition lives in the
# service module per mobile-first v3 plan Commit 13 / D-B.
__all__ = ["RowKey", "grid_bp"]


@grid_bp.route("/grid")
@login_required
@require_owner
def index():
    """Render the full budget grid page.

    Loads the current period as the leftmost column, with future
    periods extending to the right.  The number of visible periods
    is controlled by query params or user settings.
    """
    user_id = current_user.id

    # Get the baseline scenario.
    scenario = get_baseline_scenario(user_id)
    if scenario is None:
        return render_template("grid/no_setup.html")

    # Get the grid account (checking by default, or user preference).
    account = resolve_grid_account(
        user_id, current_user.settings,
        request.args.get("account_id", type=int),
    )

    # Determine the visible period range.
    num_periods = request.args.get(
        "periods",
        default=(current_user.settings.grid_default_periods
                 if current_user.settings else 6),
        type=int,
    )
    start_offset = request.args.get("offset", default=0, type=int)

    # Find the current period as the baseline starting point.
    current_period = pay_period_service.get_current_period(user_id)
    if current_period is None:
        return render_template("grid/no_periods.html")

    # Calculate the actual starting index with offset.
    start_index = current_period.period_index + start_offset

    # Load the visible periods.
    periods = pay_period_service.get_periods_in_range(user_id, start_index, num_periods)
    if not periods:
        return render_template("grid/no_periods.html")

    # Load all periods from anchor forward for balance calculation.
    all_periods = pay_period_service.get_all_periods(user_id)

    # Load transactions scoped to the viewed account.  Every transaction
    # now has account_id (NOT NULL), so filtering ensures the grid only
    # shows income/expenses belonging to the selected account.  This is
    # critical when the user switches accounts via settings -- without
    # this filter, checking transactions would appear on the savings
    # grid and corrupt the projected balance.
    period_ids = [p.id for p in all_periods]
    txn_filters = [
        Transaction.pay_period_id.in_(period_ids),
        Transaction.scenario_id == scenario.id,
        Transaction.is_deleted.is_(False),
    ]
    if account:
        txn_filters.append(Transaction.account_id == account.id)
    all_transactions = (
        db.session.query(Transaction)
        .options(
            selectinload(Transaction.entries),
            selectinload(Transaction.template),
        )
        .filter(*txn_filters)
        .all()
    )

    # Calculate balances via the canonical entries-aware producer
    # (E-25 / Commit 5).  The producer owns its own query (it always
    # ``selectinload``s entries so the entry-aware reduction applies
    # unconditionally), which is the structural fix for CRIT-01 /
    # F-009.  The grid additionally keeps its own ``all_transactions``
    # query above for display purposes -- it needs the ``template``
    # eager-load for row-key generation and the same entries for
    # ``entry_sums`` / cell rendering, neither of which is in
    # ``balances_for``'s remit.  The double-query cost is a one-extra
    # SELECT trade for the seam-removal guarantee.
    anchor_balance = (
        account.current_anchor_balance if account else Decimal("0.00")
    )

    if account is not None:
        balance_result = balance_resolver.balances_for(
            account, scenario.id, all_periods,
        )
        balances = balance_result.balances
        stale_anchor_warning = balance_result.stale_anchor_warning
    else:
        # No-account edge case: no anchor exists to project from.
        # Post-Commit-3 every user with an account row has a
        # resolvable anchor; the user-with-zero-accounts state lands
        # here and gets an empty balance map (the grid template
        # renders empty cells cleanly).
        balances = OrderedDict()
        stale_anchor_warning = False

    # Group transactions by period for the template's per-period cell
    # iteration.  ``grid_view_service.build_matched_by_row_period``
    # rebuilds this same index internally, so the route's
    # ``txn_by_period`` here only exists for the template context
    # (the templates consume it directly to look up cell contents in
    # legacy / debug paths).
    txn_by_period = {}
    for txn in all_transactions:
        txn_by_period.setdefault(txn.pay_period_id, []).append(txn)

    # Pre-compute entry sums for tracked transactions with entries.
    # The cell template uses this to show "spent / budget" progress
    # instead of the standard single-amount display.
    entry_sums = build_entry_sums_dict(all_transactions)

    # Per-period subtotals via the canonical entries-aware producer
    # (E-25 / Commit 10).  Routing the on-screen subtotal row through
    # ``balance_resolver.period_subtotal`` closes F-002 Pair C / F-004
    # (Q-10): the same Projected-only, entries-aware formula now
    # generates both the subtotal row and the balance row, so
    # ``balances[p] - balances[p-1] == subtotals[p].net`` by
    # construction.  The pre-Commit-10 inline loop above used raw
    # ``txn.effective_amount`` and disagreed with the entries-aware
    # balance row whenever a Projected envelope expense carried
    # cleared/uncleared/credit entries.  The
    # ``PeriodSubtotal`` dataclass exposes ``.income``, ``.expense``,
    # ``.net`` which the grid templates already access by attribute
    # (``subtotals[period.id].income`` etc., dict and dataclass behave
    # identically through Jinja's attribute resolution).
    subtotals = {}
    for period in periods:
        if account is None:
            # No-account state -- the grid still renders period
            # headers but every subtotal cell is zero.  Return a
            # zero-valued ``PeriodSubtotal`` so the template's
            # ``.income`` / ``.expense`` / ``.net`` access does not
            # ``AttributeError`` and the rendered subtotals match the
            # empty-balance projection above.
            subtotals[period.id] = balance_resolver.PeriodSubtotal(
                income=Decimal("0.00"),
                expense=Decimal("0.00"),
                net=Decimal("0.00"),
            )
        else:
            subtotals[period.id] = balance_resolver.period_subtotal(
                account, scenario.id, period,
            )

    # Load ALL categories (including archived) for row key building so
    # transactions with archived categories still render correctly.
    all_categories = (
        db.session.query(Category)
        .filter_by(user_id=user_id)
        .order_by(Category.group_name, Category.item_name)
        .all()
    )
    # Active-only categories for the Add Transaction modal dropdown.
    active_categories = [c for c in all_categories if c.is_active]

    # Scope row generation to the visible window by default so the grid
    # stays uncluttered when planning far in advance.  ``?show_all=1``
    # opts back in to the full forward projection for full-picture
    # review.  Balance math, cell matching, and subtotals all work off
    # ``all_transactions``/``txn_by_period`` and are unaffected by this
    # filter -- any txn hidden from row-key generation has
    # pay_period_id outside the visible window, so it contributes $0
    # to every visible-period subtotal and its cells were never going
    # to render.  See docs/: grid row scoping invariants.
    show_all = request.args.get("show_all", type=int) == 1
    if show_all:
        row_source_txns = all_transactions
    else:
        visible_period_ids = {p.id for p in periods}
        row_source_txns = [
            t for t in all_transactions
            if t.pay_period_id in visible_period_ids
        ]

    # Build row keys + the (row_key, period) -> matched transactions
    # dict via the pure ``grid_view_service`` producer.  The service
    # is also called from ``app/routes/companion.py::index`` so the
    # owner mobile grid and the companion view share one definition of
    # the row-key dedup, sort order, and cell-matching predicate
    # (mobile-first v3 plan Commit 13 / D-B).  The predicate is
    # text-for-text identical to the inline matching loops that
    # ``grid.html`` and ``_mobile_grid.html`` v1 had at
    # ``grid.html`` lines 158-173 (income) / 234-246 (expense) and
    # ``_mobile_grid.html`` lines 65-81 (income) / 152-168 (expense);
    # Commits 3 and 4 collapsed all four onto this dict, this commit
    # only moved its construction out of the route.
    income_row_keys = grid_view_service.build_row_keys(
        row_source_txns, all_categories, is_income_section=True,
    )
    expense_row_keys = grid_view_service.build_row_keys(
        row_source_txns, all_categories, is_income_section=False,
    )
    matched_by_row_period = grid_view_service.build_matched_by_row_period(
        income_row_keys, expense_row_keys, periods, all_transactions,
    )

    # Load statuses for the edit form dropdowns.
    statuses = db.session.query(Status).all()
    transaction_types = db.session.query(TransactionType).all()

    # Determine column sizing class based on visible period count.
    if num_periods <= 6:
        col_size = "wide"
    elif num_periods <= 13:
        col_size = "medium"
    else:
        col_size = "compact"

    low_balance_threshold = (
        current_user.settings.low_balance_threshold
        if current_user.settings and current_user.settings.low_balance_threshold is not None
        else 500
    )

    return render_template(
        "grid/grid.html",
        scenario=scenario,
        account=account,
        periods=periods,
        current_period=current_period,
        balances=balances,
        txn_by_period=txn_by_period,
        subtotals=subtotals,
        categories=active_categories,
        income_row_keys=income_row_keys,
        expense_row_keys=expense_row_keys,
        statuses=statuses,
        transaction_types=transaction_types,
        num_periods=num_periods,
        start_offset=start_offset,
        show_all=show_all,
        col_size=col_size,
        anchor_balance=anchor_balance,
        today=date.today(),
        all_periods=all_periods,
        low_balance_threshold=low_balance_threshold,
        stale_anchor_warning=stale_anchor_warning,
        entry_sums=entry_sums,
        matched_by_row_period=matched_by_row_period,
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
