"""
Shekel Budget App -- Dashboard Routes

Summary dashboard displaying upcoming bills, alerts, balance,
payday info, savings goals, debt summary, and spending comparison.
Includes mark-paid interaction that delegates to the same service
logic used by the grid's mark-done endpoint.
"""

import logging

from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from marshmallow import ValidationError as MarshmallowValidationError

from app.utils.auth_helpers import require_owner
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.transaction import Transaction
from app.schemas.validation import MarkDoneSchema
from app.services import dashboard_service, transfer_service
from app.services.state_machine import verify_transition

logger = logging.getLogger(__name__)

dashboard_bp = Blueprint("dashboard", __name__)

# Schema for the optional ``actual_amount`` form field on
# ``mark_paid``.  Single instance per process (Marshmallow contract);
# replaces the inline ``Decimal(...)`` parse the route used before
# commit C-27 / F-042 / F-162 of the 2026-04-15 security
# remediation plan.
_mark_done_schema = MarkDoneSchema()


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

    Accepts optional ``actual_amount`` from form data, validated by
    :class:`MarkDoneSchema` so a malformed numeric value returns a
    clean field-level 400 (Marshmallow message) instead of the
    legacy ``"Invalid actual amount"`` translation, and a negative
    value is rejected at the schema tier (commit C-27 / F-042 /
    F-162 of the 2026-04-15 security remediation plan).
    """
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    # Income uses 'received', expenses use 'done'.
    if txn.is_income:
        status_id = ref_cache.status_id(StatusEnum.RECEIVED)
    else:
        status_id = ref_cache.status_id(StatusEnum.DONE)

    # Validate the optional ``actual_amount`` form field via the
    # shared Marshmallow schema.  ``strip_empty_strings`` (in the
    # schema) removes the empty-input UX so a button-click with no
    # actual_amount yields ``actual_amount`` absent from the loaded
    # dict, branching into "leave the column untouched" below.
    try:
        mark_done_data = _mark_done_schema.load(request.form)
    except MarshmallowValidationError as exc:
        return jsonify(errors=exc.messages), 400
    actual_amount = mark_done_data.get("actual_amount")

    # Transfer shadows route through transfer_service to update both
    # shadows and the parent transfer atomically.
    if txn.transfer_id is not None:
        svc_kwargs = {
            "status_id": ref_cache.status_id(StatusEnum.DONE),
            "paid_at": db.func.now(),
        }
        if actual_amount is not None:
            svc_kwargs["actual_amount"] = actual_amount

        try:
            transfer_service.update_transfer(
                txn.transfer_id, current_user.id, **svc_kwargs,
            )
        except ValidationError as exc:
            # transfer_service.update_transfer runs every status
            # change through ``verify_transition`` (commit C-21).
            # A mark-paid request against a Cancelled or Settled
            # transfer surfaces here as 400 instead of crashing.
            db.session.rollback()
            return str(exc), 400
    else:
        # State-machine guard: only Projected (or the identity edge
        # from Paid/Received) can transition into Paid/Received via
        # mark-paid.  Closes the parity gap with the grid's
        # mark_done endpoint -- the dashboard now enforces the same
        # workflow contract.  Audit reference: F-047 / F-161
        # follow-up to commit C-21.
        try:
            verify_transition(txn.status_id, status_id, context="transaction")
        except ValidationError as exc:
            return str(exc), 400
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


def _txn_to_bill(txn):
    """Convert a Transaction to a bill dict for template rendering.

    Delegates dict construction to dashboard_service.txn_to_bill_dict
    so this partial response stays in sync with the full bills list
    produced by dashboard_service._get_upcoming_bills.  Adds is_paid
    based on the transaction's current settled state -- the mark-paid
    flow always transitions out of PROJECTED, so is_paid is True
    after the commit and the template suppresses the progress span
    for the paid row.
    """
    from datetime import date as date_type  # pylint: disable=import-outside-toplevel
    bill = dashboard_service.txn_to_bill_dict(txn, date_type.today())
    bill["is_paid"] = bool(txn.status and txn.status.is_settled)
    return bill
