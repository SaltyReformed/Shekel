"""
Shekel Budget App — Grid Routes (Main Budget View)

The primary view: a spreadsheet-like grid where columns are pay periods
and rows are income/expense line items.  Supports HTMX partial swaps
for inline editing, balance refresh, and carry forward.
"""

import logging
from datetime import date
from decimal import Decimal

from flask import Blueprint, render_template, request
from flask_login import current_user, login_required

from app.extensions import db
from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.models.category import Category
from app.models.ref import Status, TransactionType
from app.services import balance_calculator, pay_period_service

logger = logging.getLogger(__name__)

grid_bp = Blueprint("grid", __name__)


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

    # Get the checking account.
    account = (
        db.session.query(Account)
        .filter_by(user_id=user_id, is_active=True)
        .first()
    )

    # Determine the visible period range.
    num_periods = int(request.args.get(
        "periods",
        current_user.settings.grid_default_periods if current_user.settings else 6,
    ))
    start_offset = int(request.args.get("offset", 0))

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

    # Load transactions for all periods (for balance calc) and visible periods.
    period_ids = [p.id for p in all_periods]
    all_transactions = (
        db.session.query(Transaction)
        .filter(
            Transaction.pay_period_id.in_(period_ids),
            Transaction.scenario_id == scenario.id,
            Transaction.is_deleted.is_(False),
        )
        .all()
    )

    # Load transfers for all periods.
    all_transfers = (
        db.session.query(Transfer)
        .filter(
            Transfer.pay_period_id.in_(period_ids),
            Transfer.scenario_id == scenario.id,
            Transfer.is_deleted.is_(False),
        )
        .all()
    )

    # Calculate balances.
    anchor_balance = account.current_anchor_balance if account else Decimal("0.00")
    anchor_period_id = account.current_anchor_period_id if account else (
        current_period.id
    )

    balances = balance_calculator.calculate_balances(
        anchor_balance=anchor_balance,
        anchor_period_id=anchor_period_id,
        periods=all_periods,
        transactions=all_transactions,
        transfers=all_transfers,
        account_id=account.id if account else None,
    )

    # Group transactions by period and then by category group for display.
    txn_by_period = {}
    for txn in all_transactions:
        txn_by_period.setdefault(txn.pay_period_id, []).append(txn)

    # Group transfers by period for display.
    xfer_by_period = {}
    for xfer in all_transfers:
        xfer_by_period.setdefault(xfer.pay_period_id, []).append(xfer)

    # Load categories for grouping rows.
    categories = (
        db.session.query(Category)
        .filter_by(user_id=user_id)
        .order_by(Category.group_name, Category.item_name)
        .all()
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
        xfer_by_period=xfer_by_period,
        categories=categories,
        statuses=statuses,
        transaction_types=transaction_types,
        num_periods=num_periods,
        start_offset=start_offset,
        col_size=col_size,
        anchor_balance=anchor_balance,
        today=date.today(),
        all_periods=all_periods,
        low_balance_threshold=low_balance_threshold,
    )


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
    account = (
        db.session.query(Account)
        .filter_by(user_id=user_id, is_active=True)
        .first()
    )

    num_periods = int(request.args.get("periods", 6))
    start_offset = int(request.args.get("offset", 0))

    current_period = pay_period_service.get_current_period(user_id)
    if not current_period:
        return "", 204

    start_index = current_period.period_index + start_offset
    periods = pay_period_service.get_periods_in_range(user_id, start_index, num_periods)
    all_periods = pay_period_service.get_all_periods(user_id)

    period_ids = [p.id for p in all_periods]
    all_transactions = (
        db.session.query(Transaction)
        .filter(
            Transaction.pay_period_id.in_(period_ids),
            Transaction.scenario_id == scenario.id,
            Transaction.is_deleted.is_(False),
        )
        .all()
    )

    all_transfers = (
        db.session.query(Transfer)
        .filter(
            Transfer.pay_period_id.in_(period_ids),
            Transfer.scenario_id == scenario.id,
            Transfer.is_deleted.is_(False),
        )
        .all()
    )

    anchor_balance = account.current_anchor_balance if account else Decimal("0.00")
    anchor_period_id = account.current_anchor_period_id if account else current_period.id

    balances = balance_calculator.calculate_balances(
        anchor_balance=anchor_balance,
        anchor_period_id=anchor_period_id,
        periods=all_periods,
        transactions=all_transactions,
        transfers=all_transfers,
        account_id=account.id if account else None,
    )

    # Also compute income/expense totals per visible period.
    txn_by_period = {}
    for txn in all_transactions:
        txn_by_period.setdefault(txn.pay_period_id, []).append(txn)

    xfer_by_period = {}
    for xfer in all_transfers:
        xfer_by_period.setdefault(xfer.pay_period_id, []).append(xfer)

    low_balance_threshold = (
        current_user.settings.low_balance_threshold
        if current_user.settings and current_user.settings.low_balance_threshold is not None
        else 500
    )

    return render_template(
        "grid/_balance_row.html",
        periods=periods,
        balances=balances,
        txn_by_period=txn_by_period,
        xfer_by_period=xfer_by_period,
        account=account,
        num_periods=num_periods,
        start_offset=start_offset,
        low_balance_threshold=low_balance_threshold,
    )
