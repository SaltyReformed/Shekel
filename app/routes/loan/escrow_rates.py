"""
Shekel Budget App -- Loan route package: escrow + rate-history management.

The HTMX partial routes that add a rate-history entry and add / remove escrow
components.  The escrow routes share the out-of-band payment-summary tail
(recomputing monthly escrow + total payment for the OOB swap); co-locating
them keeps that parallel code intra-file (R0801 is cross-file only).
"""

import logging
from datetime import date

from flask import flash, render_template, request
from flask_login import login_required

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
    _resolve_loan_state,
)
from app.services import (
    escrow_calculator,
    loan_payment_service,
    loan_posting_service,
)
from app.utils.auth_helpers import require_owner

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
    # DH-#56: the OOB swap shows the resolver-derived current rate (the
    # rate in effect today after the just-committed rate history), not the
    # retired ``LoanParams.interest_rate`` column.  Resolve once here so
    # the swapped Overview "Interest Rate" reflects the new change.
    state = _resolve_loan_state(account, params)
    return render_template(
        "loan/_rate_history.html",
        account=account,
        params=params,
        rate_history=rate_history,
        current_rate=state.current_rate,
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

    # A rate change cannot predate origination: period 0 starts at
    # ``origination_date`` and the origination RateHistory row (DH-#56)
    # is the loan's base / period-0 rate.  A pre-origination row would
    # become the earliest entry, displacing the true origination row in
    # the dashboard's ``origination_rate`` derivation
    # (``rate_history[-1]``) and in ``_origination_rate``'s ``min()``.
    # Enforced in the route (not the schema) because the schema has no
    # access to the loan's origination date -- mirroring the
    # ``anchor_date >= origination_date`` guard in
    # :func:`true_up_balance`.  ``effective_date == origination_date``
    # itself collides with the seeded origination row and is rejected by
    # the existing same-date unique-constraint path below.
    if data["effective_date"] < params.origination_date:
        return (
            "Rate change effective date cannot be before the loan's "
            f"origination date ({params.origination_date.isoformat()}).",
            400,
        )

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

    # DH-#56: the prior ``params.interest_rate = data["interest_rate"]``
    # mirror-write is gone.  The retired column drifted to the latest
    # rate on every change (corrupting the resolver's period-0/base rate
    # for a backdated or out-of-order change); RateHistory is now the
    # sole source of truth and the resolver derives the current rate from
    # it, so no scalar needs maintaining here.
    # Build-Order Step 4: a rate change moves the interest split of every
    # confirmed post-anchor payment, in every scenario.  The shared helper
    # re-syncs those corrections in the same transaction as the new rate row and
    # translates a same-effective-date duplicate (which its flush surfaces) into
    # the idempotent re-render below; a non-rate IntegrityError propagates from
    # the helper (the correct 500 disposition).
    if not loan_posting_service.sync_all_scenarios_or_duplicate(
        account.id, _RATE_HISTORY_UNIQUE_CONSTRAINT,
    ):
        # Same-effective-date double-submit (F-104 / C-22): the composite
        # unique ``uq_rate_history_account_effective_date`` rejected the second
        # INSERT when the user clicked Save twice in a row.  Flash a clear
        # message and re-render the rate history without the proposed
        # duplicate.  A legitimate same-day correction is expressed by editing
        # the existing row, not by appending another.
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

    db.session.commit()
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

    # Check for a duplicate name among the CURRENTLY-ACTIVE components; a
    # component removed earlier (``end_date`` set) may be re-added under the
    # same name, so a historical version must not block the add.  The partial
    # unique ``uq_escrow_components_account_name_active`` is the DB backstop.
    existing = (
        db.session.query(EscrowComponent)
        .filter(
            EscrowComponent.account_id == account.id,
            EscrowComponent.name == data["name"],
            EscrowComponent.end_date.is_(None),
        )
        .first()
    )
    if existing:
        return "An escrow component with that name already exists.", 400

    # ``effective_date`` is omitted -- the column's CURRENT_DATE server default
    # takes effect today, opening the component's active range.
    comp = EscrowComponent(account_id=account.id, **data)
    db.session.add(comp)
    db.session.commit()

    logger.info("Added escrow component '%s' to loan %d", data["name"], account.id)

    escrow_components = loan_payment_service.load_active_escrow_components(
        account.id,
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

    # Close the component's active range as of today (replaces the old
    # ``is_active = False``); the row survives as history.  Guarded so a repeat
    # delete does not move an already-set ``end_date`` (idempotent).
    if comp.end_date is None:
        comp.end_date = date.today()
    db.session.commit()
    logger.info("Deactivated escrow component %d from loan %d", component_id, account.id)

    escrow_components = loan_payment_service.load_active_escrow_components(
        account.id,
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
