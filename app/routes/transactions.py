"""
Shekel Budget App — Transaction Routes

CRUD operations and status workflow for individual transactions.
Returns HTMX fragments for inline editing in the grid.
"""

import logging
from decimal import Decimal

from flask import Blueprint, render_template, request, jsonify
from flask_login import current_user, login_required

from app.extensions import db
from app.models.transaction import Transaction
from app.models.pay_period import PayPeriod
from app.models.scenario import Scenario
from app.models.account import Account
from app.models.category import Category
from app.models.ref import Status, TransactionType
from app.schemas.validation import (
    TransactionUpdateSchema,
    TransactionCreateSchema,
    InlineTransactionCreateSchema,
)
from app.services import credit_workflow, carry_forward_service, pay_period_service
from app.exceptions import NotFoundError, ValidationError

logger = logging.getLogger(__name__)

transactions_bp = Blueprint("transactions", __name__)

# Marshmallow schema instances.
_update_schema = TransactionUpdateSchema()
_create_schema = TransactionCreateSchema()
_inline_create_schema = InlineTransactionCreateSchema()


@transactions_bp.route("/transactions/<int:txn_id>", methods=["GET"])
@login_required
def get_edit_form(txn_id):
    """HTMX partial: return the inline edit form for a transaction cell."""
    txn = db.session.get(Transaction, txn_id)
    if txn is None:
        return "Not found", 404

    statuses = db.session.query(Status).all()
    periods = pay_period_service.get_all_periods(current_user.id)

    return render_template(
        "grid/_transaction_edit.html",
        txn=txn,
        statuses=statuses,
        periods=periods,
    )


@transactions_bp.route("/transactions/<int:txn_id>/cell", methods=["GET"])
@login_required
def get_cell(txn_id):
    """HTMX partial: return the display-mode cell content for a transaction."""
    txn = db.session.get(Transaction, txn_id)
    if txn is None:
        return "Not found", 404
    return render_template("grid/_transaction_cell.html", txn=txn)


@transactions_bp.route("/transactions/<int:txn_id>/quick-edit", methods=["GET"])
@login_required
def get_quick_edit(txn_id):
    """HTMX partial: return the minimal inline amount input."""
    txn = db.session.get(Transaction, txn_id)
    if txn is None:
        return "Not found", 404
    return render_template("grid/_transaction_quick_edit.html", txn=txn)


@transactions_bp.route("/transactions/<int:txn_id>/full-edit", methods=["GET"])
@login_required
def get_full_edit(txn_id):
    """HTMX partial: return the full edit popover form."""
    txn = db.session.get(Transaction, txn_id)
    if txn is None:
        return "Not found", 404
    statuses = db.session.query(Status).all()
    return render_template("grid/_transaction_full_edit.html", txn=txn, statuses=statuses)


@transactions_bp.route("/transactions/<int:txn_id>", methods=["PATCH"])
@login_required
def update_transaction(txn_id):
    """Update a transaction's fields (inline edit save).

    Returns the updated cell fragment.  Sends an HX-Trigger header
    to refresh the balance row.
    """
    txn = db.session.get(Transaction, txn_id)
    if txn is None:
        return "Not found", 404

    # Parse and validate input.
    errors = _update_schema.validate(request.form)
    if errors:
        return jsonify(errors=errors), 400

    data = _update_schema.load(request.form)

    # Apply updates.
    for field, value in data.items():
        setattr(txn, field, value)

    # If the user changed amount or period on a template-generated item,
    # flag as override.
    if txn.template_id and ("estimated_amount" in data or "pay_period_id" in data):
        txn.is_override = True

    db.session.commit()
    logger.info("Updated transaction %d", txn_id)

    # Return the updated cell with a trigger to refresh balances.
    response = render_template("grid/_transaction_cell.html", txn=txn)
    return response, 200, {"HX-Trigger": "balanceChanged"}


@transactions_bp.route("/transactions/<int:txn_id>/mark-done", methods=["POST"])
@login_required
def mark_done(txn_id):
    """Set a transaction's status to 'done' (expenses) or 'received' (income).

    Automatically picks the correct status based on transaction type.
    Accepts an optional actual_amount from the form.
    """
    txn = db.session.get(Transaction, txn_id)
    if txn is None:
        return "Not found", 404

    # Income uses 'received', expenses use 'done'.
    if txn.is_income:
        status = db.session.query(Status).filter_by(name="received").one()
    else:
        status = db.session.query(Status).filter_by(name="done").one()
    txn.status_id = status.id

    # Accept an actual amount from the form.
    actual = request.form.get("actual_amount")
    if actual:
        txn.actual_amount = Decimal(actual)

    db.session.commit()
    logger.info("Marked transaction %d as %s", txn_id, status.name)

    response = render_template("grid/_transaction_cell.html", txn=txn)
    return response, 200, {"HX-Trigger": "gridRefresh"}


@transactions_bp.route("/transactions/<int:txn_id>/mark-credit", methods=["POST"])
@login_required
def mark_credit(txn_id):
    """Mark a transaction as 'credit' and auto-generate a payback expense."""
    try:
        payback = credit_workflow.mark_as_credit(txn_id)
        db.session.commit()
    except (NotFoundError, ValidationError) as exc:
        return str(exc), 400

    txn = db.session.get(Transaction, txn_id)
    response = render_template("grid/_transaction_cell.html", txn=txn)
    return response, 200, {"HX-Trigger": "gridRefresh"}


@transactions_bp.route("/transactions/<int:txn_id>/unmark-credit", methods=["DELETE"])
@login_required
def unmark_credit(txn_id):
    """Revert credit status and delete the auto-generated payback."""
    try:
        credit_workflow.unmark_credit(txn_id)
        db.session.commit()
    except NotFoundError as exc:
        return str(exc), 404

    txn = db.session.get(Transaction, txn_id)
    response = render_template("grid/_transaction_cell.html", txn=txn)
    return response, 200, {"HX-Trigger": "gridRefresh"}


@transactions_bp.route("/transactions/<int:txn_id>/cancel", methods=["POST"])
@login_required
def cancel_transaction(txn_id):
    """Set a transaction's status to 'cancelled'."""
    txn = db.session.get(Transaction, txn_id)
    if txn is None:
        return "Not found", 404

    status = db.session.query(Status).filter_by(name="cancelled").one()
    txn.status_id = status.id

    db.session.commit()
    logger.info("Cancelled transaction %d", txn_id)

    response = render_template("grid/_transaction_cell.html", txn=txn)
    return response, 200, {"HX-Trigger": "gridRefresh"}


@transactions_bp.route("/transactions/new/quick", methods=["GET"])
@login_required
def get_quick_create():
    """HTMX partial: return a quick-create input for an empty cell.

    Query params: category_id, period_id, txn_type_name.
    """
    category_id = request.args.get("category_id", type=int)
    period_id = request.args.get("period_id", type=int)
    txn_type_name = request.args.get("txn_type_name", "expense")

    category = db.session.get(Category, category_id)
    period = db.session.get(PayPeriod, period_id)
    if not category or not period:
        return "Not found", 404

    # Look up the transaction type and baseline scenario for hidden fields.
    txn_type = db.session.query(TransactionType).filter_by(name=txn_type_name).one()
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
        scenario_id=scenario.id,
        transaction_type_id=txn_type.id,
        txn_type_name=txn_type_name,
    )


@transactions_bp.route("/transactions/new/full", methods=["GET"])
@login_required
def get_full_create():
    """HTMX partial: return the full create popover form.

    Query params: category_id, period_id, txn_type_name.
    """
    category_id = request.args.get("category_id", type=int)
    period_id = request.args.get("period_id", type=int)
    txn_type_name = request.args.get("txn_type_name", "expense")

    category = db.session.get(Category, category_id)
    period = db.session.get(PayPeriod, period_id)
    if not category or not period:
        return "Not found", 404

    txn_type = db.session.query(TransactionType).filter_by(name=txn_type_name).one()
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
        scenario_id=scenario.id,
        transaction_type_id=txn_type.id,
        statuses=statuses,
    )


@transactions_bp.route("/transactions/empty-cell", methods=["GET"])
@login_required
def get_empty_cell():
    """HTMX partial: return the empty cell placeholder.

    Used by Escape key to revert a quick-create form back to the dash.
    Query params: category_id, period_id, txn_type_name.
    """
    category_id = request.args.get("category_id", type=int)
    period_id = request.args.get("period_id", type=int)
    txn_type_name = request.args.get("txn_type_name", "expense")

    category = db.session.get(Category, category_id)
    period = db.session.get(PayPeriod, period_id)
    if not category or not period:
        return "Not found", 404

    return render_template(
        "grid/_transaction_empty_cell.html",
        category=category,
        period=period,
        txn_type_name=txn_type_name,
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

    # Look up the category to derive the transaction name.
    category = db.session.get(Category, data["category_id"])
    if not category:
        return "Category not found", 404

    # Default to projected status if not specified.
    if "status_id" not in data or data["status_id"] is None:
        projected = db.session.query(Status).filter_by(name="projected").one()
        data["status_id"] = projected.id

    # Set the name from the category display name.
    data["name"] = category.display_name

    txn = Transaction(**data)
    db.session.add(txn)
    db.session.commit()
    logger.info("Created inline transaction: %s (id=%d)", txn.name, txn.id)

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

    # Default to projected status if not specified.
    if "status_id" not in data or data["status_id"] is None:
        projected = db.session.query(Status).filter_by(name="projected").one()
        data["status_id"] = projected.id

    txn = Transaction(**data)
    db.session.add(txn)
    db.session.commit()
    logger.info("Created ad-hoc transaction: %s", txn.name)

    response = render_template("grid/_transaction_cell.html", txn=txn)
    return response, 201, {"HX-Trigger": "balanceChanged"}


@transactions_bp.route("/transactions/<int:txn_id>", methods=["DELETE"])
@login_required
def delete_transaction(txn_id):
    """Soft-delete a transaction (or hard-delete if it's ad-hoc)."""
    txn = db.session.get(Transaction, txn_id)
    if txn is None:
        return "Not found", 404

    if txn.template_id:
        # Template-linked: soft-delete so the recurrence engine knows.
        txn.is_deleted = True
    else:
        # Ad-hoc: hard delete.
        db.session.delete(txn)

    db.session.commit()
    logger.info("Deleted transaction %d", txn_id)
    return "", 200, {"HX-Trigger": "balanceChanged"}


@transactions_bp.route("/pay-periods/<int:period_id>/carry-forward", methods=["POST"])
@login_required
def carry_forward(period_id):
    """Carry forward all unpaid items from a period to the current period."""
    current_period = pay_period_service.get_current_period(current_user.id)
    if current_period is None:
        return "No current period found", 400

    try:
        count = carry_forward_service.carry_forward_unpaid(period_id, current_period.id)
        db.session.commit()
    except NotFoundError as exc:
        return str(exc), 404

    logger.info("Carried forward %d items from period %d", count, period_id)
    # Trigger a full grid refresh.
    return "", 200, {"HX-Trigger": "gridRefresh"}
