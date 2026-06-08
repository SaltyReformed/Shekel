"""
Shekel Budget App -- Transaction route package: read-only HTMX partials.

The GET routes that return display / edit / create form fragments for the
grid: the display cell, the quick-edit and full-edit popovers, and the
quick-create / full-create / empty-cell placeholders.  None of these
mutate state.
"""

from flask import render_template, request
from flask_login import current_user, login_required

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.transfer import Transfer
from app.models.ref import Status
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.account import Account
from app.services import pay_period_service
from app.services.scenario_resolver import get_baseline_scenario
from app.utils.auth_helpers import require_owner
from app.routes.transactions._bp import transactions_bp
from app.routes.transactions._helpers import (
    _get_owned_transaction,
    _render_cell,
    _resolve_owned_fks,
)


@transactions_bp.route("/transactions/<int:txn_id>/cell", methods=["GET"])
@login_required
@require_owner
def get_cell(txn_id):
    """HTMX partial: return the display-mode cell content for a transaction."""
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404
    return _render_cell(txn)


@transactions_bp.route("/transactions/<int:txn_id>/quick-edit", methods=["GET"])
@login_required
@require_owner
def get_quick_edit(txn_id):
    """HTMX partial: return the minimal inline amount input."""
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404
    return render_template("grid/_transaction_quick_edit.html", txn=txn)


@transactions_bp.route("/transactions/<int:txn_id>/full-edit", methods=["GET"])
@login_required
@require_owner
def get_full_edit(txn_id):
    """HTMX partial: return the full edit popover form.

    For shadow transactions (transfer_id IS NOT NULL), returns the
    transfer edit form instead of the transaction edit form so the
    user edits the parent transfer and both shadows stay in sync.
    """
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    # --- Transfer detection: return transfer edit form for shadows ---
    if txn.transfer_id is not None:
        xfer = db.session.get(Transfer, txn.transfer_id)
        if xfer is None:
            return "Not found", 404
        statuses = db.session.query(Status).all()
        categories = (
            db.session.query(Category)
            .filter_by(user_id=current_user.id)
            .order_by(Category.group_name, Category.item_name)
            .all()
        )
        # Current + future periods (plus the transfer's own) power the
        # period-move selector when a transfer is edited from a grid
        # shadow cell -- same set the transfers blueprint supplies.
        periods = pay_period_service.get_current_and_future_periods(
            current_user.id, include_period_id=xfer.pay_period_id,
        )
        return render_template(
            "transfers/_transfer_full_edit.html",
            xfer=xfer,
            statuses=statuses,
            categories=categories,
            source_txn_id=txn.id,
            periods=periods,
        )

    statuses = db.session.query(Status).all()
    # Pay periods power the in-popover period-move selector.  Only the
    # current and future periods are offered -- moving an expense into an
    # already-closed period is not a supported workflow -- but the row's
    # own period is always included so a transaction that currently sits
    # in a past period stays selected (and is not silently re-pointed at
    # the first current period on save).  Periods are per-user; the PATCH
    # handler re-checks ownership of the submitted id (F-029).
    periods = pay_period_service.get_current_and_future_periods(
        current_user.id, include_period_id=txn.pay_period_id,
    )
    return render_template(
        "grid/_transaction_full_edit.html",
        txn=txn,
        statuses=statuses,
        periods=periods,
    )


@transactions_bp.route("/transactions/new/quick", methods=["GET"])
@login_required
@require_owner
def get_quick_create():
    """HTMX partial: return a quick-create input for an empty cell.

    Query params: category_id, period_id, transaction_type_id.
    """
    category_id = request.args.get("category_id", type=int)
    period_id = request.args.get("period_id", type=int)
    account_id = request.args.get("account_id", type=int)
    transaction_type_id = request.args.get(
        "transaction_type_id", type=int,
        default=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
    )

    # Ownership check: prevent IDOR -- return identical 404 for "does
    # not exist" and "belongs to another user" so attackers cannot
    # distinguish the two cases.  See audit finding H1.
    objs, err = _resolve_owned_fks([
        (Category, category_id, "Not found"),
        (PayPeriod, period_id, "Not found"),
        (Account, account_id, "Not found"),
    ])
    if err is not None:
        return err
    category = objs[Category]
    period = objs[PayPeriod]
    acct = objs[Account]

    # Look up the baseline scenario for hidden fields.
    scenario = get_baseline_scenario(current_user.id)
    if not scenario:
        return "No baseline scenario", 400

    return render_template(
        "grid/_transaction_quick_create.html",
        category=category,
        period=period,
        account_id=acct.id,
        scenario_id=scenario.id,
        transaction_type_id=transaction_type_id,
        txn_type_id=transaction_type_id,
    )


@transactions_bp.route("/transactions/new/full", methods=["GET"])
@login_required
@require_owner
def get_full_create():
    """HTMX partial: return the full create popover form.

    Query params: category_id, period_id, account_id, transaction_type_id.
    """
    category_id = request.args.get("category_id", type=int)
    period_id = request.args.get("period_id", type=int)
    account_id = request.args.get("account_id", type=int)
    transaction_type_id = request.args.get(
        "transaction_type_id", type=int,
        default=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
    )

    # Ownership check: same IDOR fix as get_quick_create (H1).
    objs, err = _resolve_owned_fks([
        (Category, category_id, "Not found"),
        (PayPeriod, period_id, "Not found"),
        (Account, account_id, "Not found"),
    ])
    if err is not None:
        return err
    category = objs[Category]
    period = objs[PayPeriod]
    acct = objs[Account]

    scenario = get_baseline_scenario(current_user.id)
    if not scenario:
        return "No baseline scenario", 400

    statuses = db.session.query(Status).all()

    return render_template(
        "grid/_transaction_full_create.html",
        category=category,
        period=period,
        account_id=acct.id,
        scenario_id=scenario.id,
        transaction_type_id=transaction_type_id,
        statuses=statuses,
    )


@transactions_bp.route("/transactions/empty-cell", methods=["GET"])
@login_required
@require_owner
def get_empty_cell():
    """HTMX partial: return the empty cell placeholder.

    Used by Escape key to revert a quick-create form back to the dash.
    Query params: category_id, period_id, transaction_type_id.
    """
    category_id = request.args.get("category_id", type=int)
    period_id = request.args.get("period_id", type=int)
    account_id = request.args.get("account_id", type=int)
    transaction_type_id = request.args.get(
        "transaction_type_id", type=int,
        default=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
    )

    # Ownership check: same IDOR fix as get_quick_create (H1).
    objs, err = _resolve_owned_fks([
        (Category, category_id, "Not found"),
        (PayPeriod, period_id, "Not found"),
        (Account, account_id, "Not found"),
    ])
    if err is not None:
        return err
    category = objs[Category]
    period = objs[PayPeriod]
    account = objs[Account]

    return render_template(
        "grid/_transaction_empty_cell.html",
        category=category,
        period=period,
        account=account,
        txn_type_id=transaction_type_id,
    )
