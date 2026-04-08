"""
Shekel Budget App -- Transaction Routes

CRUD operations and status workflow for individual transactions.
Returns HTMX fragments for inline editing in the grid.
"""

import logging
from decimal import Decimal, InvalidOperation

from flask import Blueprint, render_template, request, jsonify
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.models.pay_period import PayPeriod
from app.models.scenario import Scenario
from app.models.account import Account
from app.models.category import Category
from app.models.ref import Status
from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.schemas.validation import (
    TransactionUpdateSchema,
    TransactionCreateSchema,
    InlineTransactionCreateSchema,
)
from app.services import credit_workflow, carry_forward_service, pay_period_service
from app.services import transfer_service
from app.exceptions import NotFoundError, ValidationError

logger = logging.getLogger(__name__)

transactions_bp = Blueprint("transactions", __name__)

# Marshmallow schema instances.
_update_schema = TransactionUpdateSchema()
_create_schema = TransactionCreateSchema()
_inline_create_schema = InlineTransactionCreateSchema()


def _get_owned_transaction(txn_id):
    """Fetch a transaction and verify it belongs to the current user.

    Ownership is determined via the pay_period's user_id since
    transactions don't have a direct user_id column.

    Returns:
        Transaction if found and owned by current_user, else None.
    """
    txn = db.session.get(Transaction, txn_id)
    if txn is None:
        return None
    if txn.pay_period.user_id != current_user.id:
        return None
    return txn


@transactions_bp.route("/transactions/<int:txn_id>/cell", methods=["GET"])
@login_required
def get_cell(txn_id):
    """HTMX partial: return the display-mode cell content for a transaction."""
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404
    return render_template("grid/_transaction_cell.html", txn=txn)


@transactions_bp.route("/transactions/<int:txn_id>/quick-edit", methods=["GET"])
@login_required
def get_quick_edit(txn_id):
    """HTMX partial: return the minimal inline amount input."""
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404
    return render_template("grid/_transaction_quick_edit.html", txn=txn)


@transactions_bp.route("/transactions/<int:txn_id>/full-edit", methods=["GET"])
@login_required
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
        return render_template(
            "transfers/_transfer_full_edit.html",
            xfer=xfer,
            statuses=statuses,
            categories=categories,
            source_txn_id=txn.id,
        )

    statuses = db.session.query(Status).all()
    return render_template("grid/_transaction_full_edit.html", txn=txn, statuses=statuses)


@transactions_bp.route("/transactions/<int:txn_id>", methods=["PATCH"])
@login_required
def update_transaction(txn_id):
    """Update a transaction's fields (inline edit save).

    Shadow transactions (transfer_id IS NOT NULL) are routed through
    the transfer service so both shadows and the parent transfer stay
    in sync (design doc invariants 3-5).

    Returns the updated cell fragment.  Sends an HX-Trigger header
    to refresh the balance row.
    """
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    # Parse and validate input.
    errors = _update_schema.validate(request.form)
    if errors:
        return jsonify(errors=errors), 400

    data = _update_schema.load(request.form)

    # --- Transfer detection guard ---
    if txn.transfer_id is not None:
        # Map transaction field names to transfer service kwargs.
        svc_kwargs = {}
        if "estimated_amount" in data:
            svc_kwargs["amount"] = data["estimated_amount"]
        if "actual_amount" in data:
            svc_kwargs["actual_amount"] = data["actual_amount"]
        if "status_id" in data:
            svc_kwargs["status_id"] = data["status_id"]
            # Null paid_at when reverting to a non-settled status.
            new_status = db.session.get(Status, data["status_id"])
            if new_status and not new_status.is_settled:
                svc_kwargs["paid_at"] = None
        if "notes" in data:
            svc_kwargs["notes"] = data["notes"]
        if "category_id" in data:
            svc_kwargs["category_id"] = data["category_id"]

        try:
            transfer_service.update_transfer(
                txn.transfer_id, current_user.id, **svc_kwargs
            )
        except (NotFoundError, ValidationError) as exc:
            return str(exc), 400

        db.session.commit()
        db.session.refresh(txn)
        logger.info(
            "user_id=%d updated shadow transaction %d (transfer %d)",
            current_user.id, txn_id, txn.transfer_id,
        )
        response = render_template("grid/_transaction_cell.html", txn=txn)
        return response, 200, {"HX-Trigger": "balanceChanged"}
    # --- End guard ---

    # Look up new status BEFORE applying setattr to avoid autoflush
    # triggering an FK violation when the session is dirtied.
    revert_paid_at = False
    if "status_id" in data:
        new_status = db.session.get(Status, data["status_id"])
        if new_status and not new_status.is_settled and txn.paid_at is not None:
            revert_paid_at = True

    # Apply updates (regular transactions only).
    for field, value in data.items():
        setattr(txn, field, value)

    if revert_paid_at:
        txn.paid_at = None

    # If the user changed amount or period on a template-generated item,
    # flag as override.
    if txn.template_id and ("estimated_amount" in data or "pay_period_id" in data):
        txn.is_override = True

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return "Invalid reference. Check that all referenced records exist.", 400
    logger.info("user_id=%d updated transaction %d", current_user.id, txn_id)

    # Return the updated cell with a trigger to refresh balances.
    response = render_template("grid/_transaction_cell.html", txn=txn)
    return response, 200, {"HX-Trigger": "balanceChanged"}


@transactions_bp.route("/transactions/<int:txn_id>/mark-done", methods=["POST"])
@login_required
def mark_done(txn_id):
    """Set a transaction's status to 'done' (expenses) or 'received' (income).

    Shadow transactions route through the transfer service so both
    shadows and the parent transfer are updated atomically.

    Automatically picks the correct status based on transaction type.
    Accepts an optional actual_amount from the form.
    """
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    # Income uses 'received', expenses use 'done'.
    if txn.is_income:
        status_id = ref_cache.status_id(StatusEnum.RECEIVED)
    else:
        status_id = ref_cache.status_id(StatusEnum.DONE)

    # --- Transfer detection guard ---
    if txn.transfer_id is not None:
        # Use 'done' for the transfer service -- it sets the same status
        # on both shadows.  The 'done'/'received' distinction is a
        # display convention for regular transactions.
        svc_kwargs = {
            "status_id": ref_cache.status_id(StatusEnum.DONE),
            "paid_at": db.func.now(),
        }

        actual = request.form.get("actual_amount")
        if actual:
            try:
                svc_kwargs["actual_amount"] = Decimal(actual)
            except (InvalidOperation, ValueError, ArithmeticError):
                return "Invalid actual amount", 400

        transfer_service.update_transfer(
            txn.transfer_id, current_user.id, **svc_kwargs
        )
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return "Invalid reference. Check that all referenced records exist.", 400
        db.session.refresh(txn)
        response = render_template("grid/_transaction_cell.html", txn=txn)
        return response, 200, {"HX-Trigger": "gridRefresh"}
    # --- End guard ---

    txn.status_id = status_id
    txn.paid_at = db.func.now()

    # Accept an actual amount from the form.
    actual = request.form.get("actual_amount")
    if actual:
        try:
            txn.actual_amount = Decimal(actual)
        except (InvalidOperation, ValueError, ArithmeticError):
            return "Invalid actual amount", 400

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return "Invalid reference. Check that all referenced records exist.", 400
    logger.info("user_id=%d marked transaction %d status_id=%d", current_user.id, txn_id, status_id)

    response = render_template("grid/_transaction_cell.html", txn=txn)
    return response, 200, {"HX-Trigger": "gridRefresh"}


@transactions_bp.route("/transactions/<int:txn_id>/mark-credit", methods=["POST"])
@login_required
def mark_credit(txn_id):
    """Mark a transaction as 'credit' and auto-generate a payback expense."""
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    # --- Transfer detection guard: credit is not applicable to transfers ---
    if txn.transfer_id is not None:
        return "Cannot mark a transfer shadow as credit.", 400

    try:
        payback = credit_workflow.mark_as_credit(txn_id, current_user.id)
        db.session.commit()
    except (NotFoundError, ValidationError) as exc:
        return str(exc), 400
    response = render_template("grid/_transaction_cell.html", txn=txn)
    return response, 200, {"HX-Trigger": "gridRefresh"}


@transactions_bp.route("/transactions/<int:txn_id>/unmark-credit", methods=["DELETE"])
@login_required
def unmark_credit(txn_id):
    """Revert credit status and delete the auto-generated payback."""
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    # --- Transfer detection guard: credit is not applicable to transfers ---
    if txn.transfer_id is not None:
        return "Cannot unmark credit on a transfer shadow.", 400

    try:
        credit_workflow.unmark_credit(txn_id, current_user.id)
        db.session.commit()
    except NotFoundError as exc:
        return str(exc), 404
    response = render_template("grid/_transaction_cell.html", txn=txn)
    return response, 200, {"HX-Trigger": "gridRefresh"}


@transactions_bp.route("/transactions/<int:txn_id>/cancel", methods=["POST"])
@login_required
def cancel_transaction(txn_id):
    """Set a transaction's status to 'cancelled'.

    Shadow transactions route through the transfer service to cancel
    the parent transfer and both shadows atomically.
    """
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    # --- Transfer detection guard ---
    if txn.transfer_id is not None:
        transfer_service.update_transfer(
            txn.transfer_id, current_user.id,
            status_id=ref_cache.status_id(StatusEnum.CANCELLED),
        )
        db.session.commit()
        db.session.refresh(txn)
        response = render_template("grid/_transaction_cell.html", txn=txn)
        return response, 200, {"HX-Trigger": "gridRefresh"}
    # --- End guard ---

    txn.status_id = ref_cache.status_id(StatusEnum.CANCELLED)

    db.session.commit()
    logger.info("user_id=%d cancelled transaction %d", current_user.id, txn_id)

    response = render_template("grid/_transaction_cell.html", txn=txn)
    return response, 200, {"HX-Trigger": "gridRefresh"}


@transactions_bp.route("/transactions/new/quick", methods=["GET"])
@login_required
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

    category = db.session.get(Category, category_id)
    period = db.session.get(PayPeriod, period_id)
    acct = db.session.get(Account, account_id) if account_id else None
    # Ownership check: prevent IDOR -- return identical 404 for
    # "does not exist" and "belongs to another user" so attackers
    # cannot distinguish the two cases.  See audit finding H1.
    if not category or category.user_id != current_user.id:
        return "Not found", 404
    if not period or period.user_id != current_user.id:
        return "Not found", 404
    if not acct or acct.user_id != current_user.id:
        return "Not found", 404

    # Look up the baseline scenario for hidden fields.
    scenario = (
        db.session.query(Scenario)
        .filter_by(user_id=current_user.id, is_baseline=True)
        .first()
    )
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

    category = db.session.get(Category, category_id)
    period = db.session.get(PayPeriod, period_id)
    acct = db.session.get(Account, account_id) if account_id else None
    # Ownership check: same IDOR fix as get_quick_create (H1).
    if not category or category.user_id != current_user.id:
        return "Not found", 404
    if not period or period.user_id != current_user.id:
        return "Not found", 404
    if not acct or acct.user_id != current_user.id:
        return "Not found", 404

    scenario = (
        db.session.query(Scenario)
        .filter_by(user_id=current_user.id, is_baseline=True)
        .first()
    )
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

    category = db.session.get(Category, category_id)
    period = db.session.get(PayPeriod, period_id)
    account = db.session.get(Account, account_id) if account_id else None
    # Ownership check: same IDOR fix as get_quick_create (H1).
    if not category or category.user_id != current_user.id:
        return "Not found", 404
    if not period or period.user_id != current_user.id:
        return "Not found", 404
    if not account or account.user_id != current_user.id:
        return "Not found", 404

    return render_template(
        "grid/_transaction_empty_cell.html",
        category=category,
        period=period,
        account=account,
        txn_type_id=transaction_type_id,
    )


@transactions_bp.route("/transactions/inline", methods=["POST"])
@login_required
def create_inline():
    """Create a transaction from inline grid interaction.

    Auto-derives the name from the category.  Returns the new
    transaction cell wrapped in a div with a unique ID for HTMX
    targeting.
    """
    errors = _inline_create_schema.validate(request.form)
    if errors:
        return jsonify(errors=errors), 400

    data = _inline_create_schema.load(request.form)

    # Verify the account belongs to the current user.
    acct = db.session.get(Account, data["account_id"])
    if not acct or acct.user_id != current_user.id:
        return "Not found", 404

    # Look up the category to derive the transaction name.
    category = db.session.get(Category, data["category_id"])
    if not category or category.user_id != current_user.id:
        return "Category not found", 404

    # Verify the pay period belongs to the current user.
    period = db.session.get(PayPeriod, data["pay_period_id"])
    if not period or period.user_id != current_user.id:
        return "Pay period not found", 404

    # Verify the scenario belongs to the current user.
    scenario = db.session.get(Scenario, data["scenario_id"])
    if not scenario or scenario.user_id != current_user.id:
        return "Not found", 404

    # Default to projected status if not specified.
    if "status_id" not in data or data["status_id"] is None:
        data["status_id"] = ref_cache.status_id(StatusEnum.PROJECTED)

    # Set the name from the category display name.
    data["name"] = category.display_name

    txn = Transaction(**data)
    db.session.add(txn)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return "Invalid reference. Check that all referenced records exist.", 400
    logger.info("user_id=%d created inline transaction: %s (id=%d)", current_user.id, txn.name, txn.id)

    # Return the cell wrapped in a div with a unique ID, matching
    # the pattern used in grid.html for existing transactions.
    response = render_template(
        "grid/_transaction_cell.html",
        txn=txn,
        wrap_div=True,
    )
    return response, 201, {"HX-Trigger": "balanceChanged"}


@transactions_bp.route("/transactions", methods=["POST"])
@login_required
def create_transaction():
    """Create an ad-hoc transaction (not from a template)."""
    errors = _create_schema.validate(request.form)
    if errors:
        return jsonify(errors=errors), 400

    data = _create_schema.load(request.form)

    # Verify the account belongs to the current user.
    acct = db.session.get(Account, data["account_id"])
    if not acct or acct.user_id != current_user.id:
        return "Not found", 404

    # Verify the pay period belongs to the current user.
    period = db.session.get(PayPeriod, data["pay_period_id"])
    if not period or period.user_id != current_user.id:
        return "Pay period not found", 404

    # Verify the scenario belongs to the current user.
    scenario = db.session.get(Scenario, data["scenario_id"])
    if not scenario or scenario.user_id != current_user.id:
        return "Not found", 404

    # Default to projected status if not specified.
    if "status_id" not in data or data["status_id"] is None:
        data["status_id"] = ref_cache.status_id(StatusEnum.PROJECTED)

    txn = Transaction(**data)
    db.session.add(txn)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return "Invalid reference. Check that all referenced records exist.", 400
    logger.info("user_id=%d created ad-hoc transaction: %s (id=%d)", current_user.id, txn.name, txn.id)

    response = render_template("grid/_transaction_cell.html", txn=txn)
    return response, 201, {"HX-Trigger": "balanceChanged"}


@transactions_bp.route("/transactions/<int:txn_id>", methods=["DELETE"])
@login_required
def delete_transaction(txn_id):
    """Soft-delete a transaction (or hard-delete if it's ad-hoc).

    Shadow transactions cannot be directly deleted -- the user must
    delete the parent transfer instead.
    """
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    # --- Transfer detection guard: block direct shadow deletion ---
    if txn.transfer_id is not None:
        return "Cannot delete a transfer shadow directly. Delete the parent transfer instead.", 400

    if txn.template_id:
        # Template-linked: soft-delete so the recurrence engine knows.
        txn.is_deleted = True
    else:
        # Ad-hoc: hard delete.
        db.session.delete(txn)

    db.session.commit()
    logger.info("user_id=%d deleted transaction %d", current_user.id, txn_id)
    return "", 200, {"HX-Trigger": "balanceChanged"}


@transactions_bp.route("/pay-periods/<int:period_id>/carry-forward", methods=["POST"])
@login_required
def carry_forward(period_id):
    """Carry forward all unpaid items from a period to the current period."""
    # Verify the source period belongs to the current user.
    source_period = db.session.get(PayPeriod, period_id)
    if source_period is None or source_period.user_id != current_user.id:
        return "Not found", 404

    current_period = pay_period_service.get_current_period(current_user.id)
    if current_period is None:
        return "No current period found", 400

    # Resolve the baseline scenario so carry-forward only moves
    # transactions within that scenario.
    scenario = (
        db.session.query(Scenario)
        .filter_by(user_id=current_user.id, is_baseline=True)
        .first()
    )
    if not scenario:
        return "No baseline scenario", 400

    try:
        count = carry_forward_service.carry_forward_unpaid(
            period_id, current_period.id, current_user.id, scenario.id
        )
        db.session.commit()
    except NotFoundError as exc:
        return str(exc), 404

    logger.info("user_id=%d carried forward %d items from period %d", current_user.id, count, period_id)
    # Trigger a full grid refresh.
    return "", 200, {"HX-Trigger": "gridRefresh"}
