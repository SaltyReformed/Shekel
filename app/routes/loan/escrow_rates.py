"""
Shekel Budget App -- Loan route package: escrow + rate-history management.

The HTMX partial routes that add a rate-history entry and add / remove escrow
components.  The escrow routes share the out-of-band payment-summary tail
(recomputing monthly escrow + total payment for the OOB swap); co-locating
them keeps that parallel code intra-file (R0801 is cross-file only).
"""

import logging

from flask import flash, render_template, request
from flask_login import login_required
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.loan_features import EscrowComponent, RateHistory
from app.models.loan_params import LoanParams
from app.routes.loan._bp import loan_bp
from app.routes.loan._helpers import (
    _RATE_HISTORY_UNIQUE_CONSTRAINT,
    _compute_total_payment,
    _escrow_schema,
    _load_loan_account,
    _rate_schema,
)
from app.services import escrow_calculator
from app.utils.auth_helpers import require_owner
from app.utils.db_errors import is_unique_violation

logger = logging.getLogger(__name__)


def _render_rate_history(account, params):
    """Re-query and render the rate-history partial for a loan account.

    The shared reload + render used by both the duplicate-submit
    (IntegrityError) and the success paths of :func:`add_rate_change`, so
    the descending-effective-date ordering and the template kwargs live in
    exactly one place.

    Args:
        account: ORM :class:`Account` instance for the loan.
        params: ORM :class:`LoanParams` instance.

    Returns:
        The rendered ``loan/_rate_history.html`` partial.
    """
    rate_history = (
        db.session.query(RateHistory)
        .filter_by(account_id=account.id)
        .order_by(RateHistory.effective_date.desc())
        .all()
    )
    return render_template(
        "loan/_rate_history.html",
        account=account,
        params=params,
        rate_history=rate_history,
    )


@loan_bp.route("/accounts/<int:account_id>/loan/rate", methods=["POST"])
@login_required
@require_owner
def add_rate_change(account_id):
    """Record a variable-rate change (HTMX)."""
    account, params, _account_type = _load_loan_account(account_id)
    if account is None or params is None:
        return "Account not found", 404

    errors = _rate_schema.validate(request.form)
    if errors:
        return "Please correct the highlighted errors and try again.", 400

    data = _rate_schema.load(request.form)

    # E-28 / HIGH-06 (Commit 24): the schema's ``@pre_load`` already
    # converted the form percent to the storage-domain fraction.

    entry = RateHistory(
        account_id=account.id,
        effective_date=data["effective_date"],
        interest_rate=data["interest_rate"],
        monthly_pi=data.get("monthly_pi"),
        notes=data.get("notes"),
    )
    db.session.add(entry)

    # Also update the current rate on params.
    params.interest_rate = data["interest_rate"]
    try:
        db.session.commit()
    except IntegrityError as exc:
        # Same-effective-date double-submit (F-104 / C-22): the
        # composite unique ``uq_rate_history_account_effective_date``
        # rejects the second INSERT when the user clicks Save twice
        # in a row.  Roll back, flash a clear message, and re-render
        # the rate history without the proposed duplicate.  A
        # legitimate same-day correction is expressed by editing the
        # existing row, not by appending another.
        db.session.rollback()
        if not is_unique_violation(exc, _RATE_HISTORY_UNIQUE_CONSTRAINT):
            raise
        logger.info(
            "Duplicate rate-history entry prevented for account %d on %s",
            account.id, data["effective_date"],
        )
        flash(
            "A rate change with that effective date already exists. "
            "Edit the existing entry to correct it.",
            "warning",
        )
        return _render_rate_history(account, params)

    logger.info("Recorded rate change for loan %d: %s", account.id, data["interest_rate"])

    return _render_rate_history(account, params)


@loan_bp.route("/accounts/<int:account_id>/loan/escrow", methods=["POST"])
@login_required
@require_owner
def add_escrow(account_id):
    """Add an escrow component (HTMX)."""
    account, params, _account_type = _load_loan_account(account_id)
    if account is None:
        return "Account not found", 404

    errors = _escrow_schema.validate(request.form)
    if errors:
        return "Please correct the highlighted errors and try again.", 400

    data = _escrow_schema.load(request.form)

    # E-28 / HIGH-06 (Commit 24): the schema's ``@pre_load``
    # converted the form percent to the storage-domain fraction
    # before validation, so ``data["inflation_rate"]`` is stored
    # verbatim.

    # Check for duplicate name.
    existing = (
        db.session.query(EscrowComponent)
        .filter_by(account_id=account.id, name=data["name"])
        .first()
    )
    if existing:
        return "An escrow component with that name already exists.", 400

    comp = EscrowComponent(account_id=account.id, **data)
    db.session.add(comp)
    db.session.commit()

    logger.info("Added escrow component '%s' to loan %d", data["name"], account.id)

    escrow_components = (
        db.session.query(EscrowComponent)
        .filter_by(account_id=account.id, is_active=True)
        .order_by(EscrowComponent.name)
        .all()
    )

    # Compute updated payment summary for OOB swap.
    monthly_escrow = escrow_calculator.calculate_monthly_escrow(escrow_components)
    total_payment = _compute_total_payment(account, params, escrow_components)

    return render_template(
        "loan/_escrow_list.html",
        account=account,
        escrow_components=escrow_calculator.build_escrow_display(
            escrow_components,
        ),
        monthly_escrow=monthly_escrow,
        total_payment=total_payment,
    )


@loan_bp.route(
    "/accounts/<int:account_id>/loan/escrow/<int:component_id>/delete",
    methods=["POST"],
)
@login_required
@require_owner
def delete_escrow(account_id, component_id):
    """Remove an escrow component (HTMX)."""
    account, _, _account_type = _load_loan_account(account_id)
    if account is None:
        return "Account not found", 404

    comp = db.session.get(EscrowComponent, component_id)
    if comp is None or comp.account_id != account.id:
        return "Component not found", 404

    comp.is_active = False
    db.session.commit()
    logger.info("Deactivated escrow component %d from loan %d", component_id, account.id)

    escrow_components = (
        db.session.query(EscrowComponent)
        .filter_by(account_id=account.id, is_active=True)
        .order_by(EscrowComponent.name)
        .all()
    )

    # Compute updated payment summary for OOB swap.
    params = (
        db.session.query(LoanParams)
        .filter_by(account_id=account.id)
        .first()
    )
    monthly_escrow = escrow_calculator.calculate_monthly_escrow(escrow_components)
    total_payment = _compute_total_payment(account, params, escrow_components)

    return render_template(
        "loan/_escrow_list.html",
        account=account,
        escrow_components=escrow_calculator.build_escrow_display(
            escrow_components,
        ),
        monthly_escrow=monthly_escrow,
        total_payment=total_payment,
    )
