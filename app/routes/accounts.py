"""
Shekel Budget App — Account Routes

Handles anchor balance true-up (the most frequent account operation).
Returns HTMX fragments for inline editing at the top of the grid.
"""

import logging
from decimal import Decimal

from flask import Blueprint, render_template, request, jsonify
from flask_login import current_user, login_required

from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.models.pay_period import PayPeriod
from app.schemas.validation import AnchorUpdateSchema
from app.services import pay_period_service

logger = logging.getLogger(__name__)

accounts_bp = Blueprint("accounts", __name__)

_anchor_schema = AnchorUpdateSchema()


@accounts_bp.route("/accounts/<int:account_id>/true-up", methods=["PATCH"])
@login_required
def true_up(account_id):
    """Update the anchor balance for an account (inline edit from grid).

    Records the true-up in anchor_history for audit trail, then
    triggers a balance recalculation via HX-Trigger.
    """
    account = db.session.get(Account, account_id)
    if account is None or account.user_id != current_user.id:
        return "Account not found", 404

    errors = _anchor_schema.validate(request.form)
    if errors:
        return jsonify(errors=errors), 400

    data = _anchor_schema.load(request.form)
    new_balance = Decimal(str(data["anchor_balance"]))

    # Find the current pay period and set it as the anchor period.
    current_period = pay_period_service.get_current_period(current_user.id)
    if current_period is None:
        return "No current pay period found", 400

    # Update the account.
    account.current_anchor_balance = new_balance
    account.current_anchor_period_id = current_period.id

    # Record in history.
    history = AccountAnchorHistory(
        account_id=account.id,
        pay_period_id=current_period.id,
        anchor_balance=new_balance,
    )
    db.session.add(history)
    db.session.commit()

    logger.info(
        "True-up: account %d set to $%s at period %d",
        account.id, new_balance, current_period.id,
    )

    return render_template(
        "grid/_anchor_edit.html",
        account=account,
        editing=False,
    ), 200, {"HX-Trigger": "balanceChanged"}


@accounts_bp.route("/accounts/<int:account_id>/anchor-form", methods=["GET"])
@login_required
def anchor_form(account_id):
    """HTMX partial: return the inline edit form for the anchor balance."""
    account = db.session.get(Account, account_id)
    if account is None or account.user_id != current_user.id:
        return "Not found", 404

    return render_template(
        "grid/_anchor_edit.html",
        account=account,
        editing=True,
    )


@accounts_bp.route("/accounts/<int:account_id>/anchor-display", methods=["GET"])
@login_required
def anchor_display(account_id):
    """HTMX partial: return the anchor balance display (non-editing)."""
    account = db.session.get(Account, account_id)
    if account is None or account.user_id != current_user.id:
        return "Not found", 404

    return render_template(
        "grid/_anchor_edit.html",
        account=account,
        editing=False,
    )
