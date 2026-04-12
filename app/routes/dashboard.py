"""
Shekel Budget App -- Dashboard Routes

Summary dashboard displaying upcoming bills, alerts, balance,
payday info, savings goals, debt summary, and spending comparison.
Includes mark-paid interaction that delegates to the same service
logic used by the grid's mark-done endpoint.
"""

import logging
from decimal import Decimal, InvalidOperation

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.utils.auth_helpers import require_owner
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.services import dashboard_service, transfer_service

logger = logging.getLogger(__name__)

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
@dashboard_bp.route("/dashboard")
@login_required
@require_owner
def page():
    """Render the summary dashboard with all 7 sections.

    Calls dashboard_service.compute_dashboard_data() and passes the
    result as template variables.
    """
    data = dashboard_service.compute_dashboard_data(current_user.id)
    return render_template("dashboard/dashboard.html", **data)


@dashboard_bp.route("/dashboard/mark-paid/<int:txn_id>", methods=["POST"])
@login_required
@require_owner
def mark_paid(txn_id):
    """Mark a bill as paid from the dashboard.

    Mirrors the grid's mark-done logic but returns a dashboard bill
    row partial instead of a grid cell.  Delegates transfer shadow
    handling to transfer_service.update_transfer() to maintain
    transfer invariants (CLAUDE.md rule 4).

    Accepts optional actual_amount from form data.
    """
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    # Income uses 'received', expenses use 'done'.
    if txn.is_income:
        status_id = ref_cache.status_id(StatusEnum.RECEIVED)
    else:
        status_id = ref_cache.status_id(StatusEnum.DONE)

    # Parse optional actual amount from form.
    actual_amount = _parse_actual_amount()
    if actual_amount is False:
        return "Invalid actual amount", 400

    # Transfer shadows route through transfer_service to update both
    # shadows and the parent transfer atomically.
    if txn.transfer_id is not None:
        svc_kwargs = {
            "status_id": ref_cache.status_id(StatusEnum.DONE),
            "paid_at": db.func.now(),
        }
        if actual_amount is not None:
            svc_kwargs["actual_amount"] = actual_amount

        transfer_service.update_transfer(
            txn.transfer_id, current_user.id, **svc_kwargs,
        )
    else:
        txn.status_id = status_id
        txn.paid_at = db.func.now()
        if actual_amount is not None:
            txn.actual_amount = actual_amount

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return "Invalid reference.", 400

    db.session.refresh(txn)
    response = render_template("dashboard/_bill_row.html", bill=_txn_to_bill(txn))
    return response, 200, {"HX-Trigger": "dashboardRefresh"}


@dashboard_bp.route("/dashboard/bills")
@login_required
@require_owner
def bills_section():
    """HTMX partial: refresh the upcoming bills section.

    Non-HTMX requests redirect to the dashboard page.
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("dashboard.page"))

    data = dashboard_service.compute_dashboard_data(current_user.id)
    return render_template(
        "dashboard/_upcoming_bills.html",
        upcoming_bills=data["upcoming_bills"],
        current_period=data["current_period"],
    )


@dashboard_bp.route("/dashboard/balance")
@login_required
@require_owner
def balance_section():
    """HTMX partial: refresh the balance and runway section.

    Non-HTMX requests redirect to the dashboard page.
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("dashboard.page"))

    data = dashboard_service.compute_dashboard_data(current_user.id)
    return render_template(
        "dashboard/_balance_runway.html",
        balance_info=data["balance_info"],
    )


# ── Helpers ──────────────────────────────────────────────────────────


def _get_owned_transaction(txn_id):
    """Fetch a transaction and verify it belongs to the current user.

    Returns Transaction if owned, else None.  Follows the same
    ownership check as transactions.py (via pay_period.user_id).
    """
    txn = db.session.get(Transaction, txn_id)
    if txn is None:
        return None
    if txn.pay_period.user_id != current_user.id:
        return None
    return txn


def _parse_actual_amount():
    """Parse optional actual_amount from form data.

    Returns:
        Decimal if provided, None if not provided, False if invalid.
    """
    actual = request.form.get("actual_amount")
    if not actual:
        return None
    try:
        return Decimal(actual)
    except (InvalidOperation, ValueError, ArithmeticError):
        return False


def _txn_to_bill(txn):
    """Convert a Transaction to a bill dict for template rendering.

    Matches the structure returned by dashboard_service._get_upcoming_bills().
    """
    from datetime import date as date_type  # pylint: disable=import-outside-toplevel
    today = date_type.today()
    return {
        "id": txn.id,
        "name": txn.name,
        "amount": txn.effective_amount,
        "due_date": txn.due_date,
        "period_start_date": txn.pay_period.start_date,
        "category_group": txn.category.group_name if txn.category else None,
        "category_item": txn.category.item_name if txn.category else None,
        "is_transfer": txn.transfer_id is not None,
        "days_until_due": (txn.due_date - today).days if txn.due_date else None,
        "is_paid": bool(txn.status and txn.status.is_settled),
    }
