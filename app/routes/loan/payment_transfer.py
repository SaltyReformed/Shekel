"""
Shekel Budget App -- Loan route package: recurring payment transfer.

Creates a recurring monthly transfer (RecurrenceRule + TransferTemplate +
generated Transfer records with shadow transactions) from a source account to
the debt account.  The amount defaults to the resolver-derived monthly payment
(P&I + escrow) with live derivation, or a user-supplied override.
"""

import logging

from flask import Response, flash, redirect, url_for
from flask_login import current_user, login_required

from app import ref_cache
from app.enums import RecurrencePatternEnum
from app.extensions import db
from app.models.loan_features import EscrowComponent
from app.models.recurrence_rule import RecurrenceRule
from app.routes._redirect_target import RedirectTarget
from app.routes._transfer_creation_helpers import (
    build_recurring_transfer_template,
    flush_template_or_namedup_redirect,
    generate_transfers_for_all_periods,
    validate_and_resolve_source_account,
)
from app.routes.loan._bp import loan_bp
from app.routes.loan._helpers import (
    _require_configured_loan,
    _resolve_loan_state,
    _transfer_schema,
)
from app.services import escrow_calculator
from app.utils.auth_helpers import require_owner

logger = logging.getLogger(__name__)


def _resolve_transfer_amount(account, params, data):
    """Resolve the loan-payment transfer amount and live-derivation flag.

    A user-supplied amount is respected verbatim (no live derivation);
    otherwise the amount defaults to the full monthly payment (P&I +
    escrow) and opts into live derivation so the projected cash debit
    tracks the loan's monthly payment after an escrow or rate change
    instead of staying frozen at the default.

    Resolver state owns the P&I figure for both ARM (re-amortized from
    the latest anchor's balance over the remaining term) and fixed-rate
    (contractual payment from origination), so the computed default
    matches the dashboard's displayed "Total Monthly (with escrow)"
    exactly (E-18 / Commit 15).

    Args:
        account: ORM :class:`Account` instance for the loan account.
        params: ORM :class:`LoanParams` instance.
        data: Validated transfer form data (mapping).

    Returns:
        Tuple of (Decimal transfer amount, bool derive_from_loan).
    """
    if "amount" in data and data["amount"] is not None:
        return data["amount"], False

    state, _, _ = _resolve_loan_state(account, params)
    escrow_components = (
        db.session.query(EscrowComponent)
        .filter_by(account_id=account.id, is_active=True)
        .all()
    )
    transfer_amount = escrow_calculator.calculate_total_payment(
        state.monthly_payment, escrow_components,
    )
    return transfer_amount, True


@loan_bp.route("/accounts/<int:account_id>/loan/create-transfer", methods=["POST"])
@login_required
@require_owner
def create_payment_transfer(account_id):
    """Create a recurring monthly transfer to a debt account.

    Creates a RecurrenceRule (monthly pattern), a TransferTemplate
    (from the selected source account to the debt account), and
    generates Transfer records (with shadow transactions) for
    existing pay periods.

    The amount defaults to the computed monthly payment (P&I + escrow).
    The user may override with a custom amount.
    """
    account, params, _ = _require_configured_loan(account_id)

    # Validate the form and resolve + ownership-check the source account
    # (shared with investment.create_contribution_transfer).
    result = validate_and_resolve_source_account(
        _transfer_schema,
        dest_account_id=account_id,
        redirect=RedirectTarget("loan.dashboard", {"account_id": account_id}),
    )
    if isinstance(result, Response):
        return result
    source_account, data = result

    # Determine the transfer amount and whether it auto-derives.  A
    # user-supplied amount is respected verbatim; the computed default
    # opts into live derivation so the projected cash debit tracks the
    # loan's monthly payment after an escrow or rate change instead of
    # staying frozen at default_amount.
    transfer_amount, derive_from_loan = _resolve_transfer_amount(
        account, params, data,
    )

    # Create monthly recurrence rule.
    monthly_pattern_id = ref_cache.recurrence_pattern_id(
        RecurrencePatternEnum.MONTHLY,
    )
    rule = RecurrenceRule(
        user_id=current_user.id,
        pattern_id=monthly_pattern_id,
        day_of_month=params.payment_day,
    )
    db.session.add(rule)
    db.session.flush()

    # Create transfer template via the shared builder.  Loan-payment
    # transfers set derive_from_loan so the projected cash debit tracks
    # the live monthly payment after an escrow or rate change.
    template_name = f"{source_account.name} -> {account.name} Payment"
    template = build_recurring_transfer_template(
        source_account=source_account,
        dest_account=account,
        rule=rule,
        name=template_name,
        default_amount=transfer_amount,
        derive_from_loan=derive_from_loan,
    )

    namedup_redirect = flush_template_or_namedup_redirect(
        redirect=RedirectTarget("loan.dashboard", {"account_id": account_id}),
    )
    if namedup_redirect is not None:
        return namedup_redirect

    # Generate transfers for existing pay periods.
    generate_transfers_for_all_periods(template)

    db.session.commit()

    logger.info(
        "Created recurring payment transfer for loan %d: $%s from account %d",
        account.id, transfer_amount, source_account.id,
    )
    flash(
        f"Recurring monthly transfer of ${transfer_amount:,.2f} created "
        f"from {source_account.name} to {account.name}.",
        "success",
    )
    return redirect(url_for("loan.dashboard", account_id=account_id))
