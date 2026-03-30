"""
Shekel Budget App -- Grid Routes (Main Budget View)

The primary view: a spreadsheet-like grid where columns are pay periods
and rows are income/expense line items.  Supports HTMX partial swaps
for inline editing, balance refresh, and carry forward.
"""

import logging
from collections import namedtuple
from datetime import date
from decimal import Decimal

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.category import Category
from app.models.ref import Status, TransactionType
from app.enums import StatusEnum
from app import ref_cache
from app.services import balance_calculator, pay_period_service
from app.services.account_resolver import resolve_grid_account

logger = logging.getLogger(__name__)

grid_bp = Blueprint("grid", __name__)

# Lightweight struct for a single row in the budget grid.  Each unique
# (category, template, name) combination produces one RowKey.
RowKey = namedtuple("RowKey", [
    "category_id",    # int -- FK to budget.categories
    "template_id",    # int or None -- FK to budget.transaction_templates
    "txn_name",       # str -- the transaction name (display label)
    "group_name",     # str -- category group for section headers
    "item_name",      # str -- category item (used for sort tiebreaker)
    "display_name",   # str -- label shown in the row <th>
    "category",       # Category -- full ORM object for empty-cell rendering
])


def _short_display_name(name):
    """Strip redundant prefixes from transaction names for row headers.

    Transfer shadows are named "Transfer to X" / "Transfer from X" and
    credit paybacks "CC Payback: X".  The grid cell already shows a
    transfer icon or CC badge, so the prefix is visual noise in the
    row label.  Strip it to show only the meaningful part.
    """
    lower = name.lower()
    if lower.startswith("transfer to "):
        return name[len("Transfer to "):]
    if lower.startswith("transfer from "):
        return name[len("Transfer from "):]
    if lower.startswith("cc payback: "):
        return name[len("CC Payback: "):]
    return name


def _build_row_keys(txn_by_period, categories, is_income_section):
    """Build a deterministic, sorted list of RowKeys for the grid.

    Scans every transaction across all visible periods and collects unique
    (category_id, template_id, txn_name) tuples.  Each tuple becomes a
    grid row.  The result is sorted by (group_name, item_name, txn_name)
    so rows appear in stable alphabetical order within each category group.

    Args:
        txn_by_period: dict mapping period_id -> list of Transaction objects.
            This is the full transaction set (all periods), not just the
            visible window.
        categories: list of Category objects, already ordered by
            (group_name, item_name).  Used to map category_id -> Category
            for sort keys and for the empty-cell template.
        is_income_section: bool -- True to collect income transactions,
            False for expense transactions.

    Returns:
        list[RowKey] -- one entry per unique transaction row, sorted by
        (group_name, item_name, txn_name).  Deterministic across calls
        with the same data.
    """
    cancelled_id = ref_cache.status_id(StatusEnum.CANCELLED)

    # Index categories by ID for O(1) lookup.
    cat_by_id = {c.id: c for c in categories}

    # Collect unique row keys across all periods.
    seen = set()       # (category_id, template_id, txn_name) tuples
    row_keys = []

    for txns in txn_by_period.values():
        for txn in txns:
            # Skip deleted and cancelled transactions.
            if txn.is_deleted or txn.status_id == cancelled_id:
                continue

            # Filter by income/expense.
            if is_income_section and not txn.is_income:
                continue
            if not is_income_section and not txn.is_expense:
                continue

            # Skip transactions whose category doesn't belong to this user
            # (should never happen, but defensive).
            cat = cat_by_id.get(txn.category_id)
            if cat is None:
                continue

            key = (txn.category_id, txn.template_id, txn.name)
            if key not in seen:
                seen.add(key)
                row_keys.append(RowKey(
                    category_id=txn.category_id,
                    template_id=txn.template_id,
                    txn_name=txn.name,
                    group_name=cat.group_name,
                    item_name=cat.item_name,
                    display_name=_short_display_name(txn.name),
                    category=cat,
                ))

    # Sort by (group_name, item_name, txn_name) for deterministic ordering.
    row_keys.sort(key=lambda rk: (rk.group_name, rk.item_name, rk.txn_name))

    return row_keys


@grid_bp.route("/")
@login_required
def index():
    """Render the full budget grid page.

    Loads the current period as the leftmost column, with future
    periods extending to the right.  The number of visible periods
    is controlled by query params or user settings.
    """
    user_id = current_user.id

    # Get the baseline scenario.
    scenario = (
        db.session.query(Scenario)
        .filter_by(user_id=user_id, is_baseline=True)
        .first()
    )
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
        .filter(*txn_filters)
        .all()
    )

    # Calculate balances.  Shadow transactions (from transfers) are
    # already in all_transactions -- no separate Transfer query needed.
    anchor_balance = account.current_anchor_balance if account else Decimal("0.00")
    anchor_period_id = account.current_anchor_period_id if account else (
        current_period.id
    )

    balances, stale_anchor_warning = balance_calculator.calculate_balances(
        anchor_balance=anchor_balance,
        anchor_period_id=anchor_period_id,
        periods=all_periods,
        transactions=all_transactions,
    )

    # Group transactions by period and then by category group for display.
    txn_by_period = {}
    for txn in all_transactions:
        txn_by_period.setdefault(txn.pay_period_id, []).append(txn)

    # Load categories for grouping rows and for the Add Transaction modal.
    categories = (
        db.session.query(Category)
        .filter_by(user_id=user_id)
        .order_by(Category.group_name, Category.item_name)
        .all()
    )

    # Build row keys: one row per unique (category, template, name) tuple.
    income_row_keys = _build_row_keys(txn_by_period, categories, is_income_section=True)
    expense_row_keys = _build_row_keys(txn_by_period, categories, is_income_section=False)

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
        categories=categories,
        income_row_keys=income_row_keys,
        expense_row_keys=expense_row_keys,
        statuses=statuses,
        transaction_types=transaction_types,
        num_periods=num_periods,
        start_offset=start_offset,
        col_size=col_size,
        anchor_balance=anchor_balance,
        today=date.today(),
        all_periods=all_periods,
        low_balance_threshold=low_balance_threshold,
        stale_anchor_warning=stale_anchor_warning,
    )


@grid_bp.route("/create-baseline", methods=["POST"])
@login_required
def create_baseline():
    """Create a missing baseline scenario for the current user.

    Idempotent: if a baseline already exists, redirects without
    creating a duplicate.
    """
    existing = (
        db.session.query(Scenario)
        .filter_by(user_id=current_user.id, is_baseline=True)
        .first()
    )
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
def balance_row():
    """HTMX partial: recalculate and return the balance summary row."""
    user_id = current_user.id

    scenario = (
        db.session.query(Scenario)
        .filter_by(user_id=user_id, is_baseline=True)
        .first()
    )
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
        .filter(*txn_filters)
        .all()
    )

    anchor_balance = account.current_anchor_balance if account else Decimal("0.00")
    anchor_period_id = account.current_anchor_period_id if account else current_period.id

    balances, _ = balance_calculator.calculate_balances(
        anchor_balance=anchor_balance,
        anchor_period_id=anchor_period_id,
        periods=all_periods,
        transactions=all_transactions,
    )

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
