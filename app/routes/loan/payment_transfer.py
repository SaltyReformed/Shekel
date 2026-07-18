"""
Shekel Budget App -- Loan route package: recurring payment transfer.

Creates a recurring monthly transfer (RecurrenceRule + TransferTemplate +
generated Transfer records with shadow transactions) from a source account to
the debt account.  The amount defaults to the resolver-derived monthly payment
(P&I + escrow) with live derivation, or a user-supplied override.
"""

import logging
from datetime import date

from flask import Response, flash, redirect, request, url_for
from flask_login import current_user, login_required

from app import ref_cache
from app.enums import RecurrencePatternEnum
from app.extensions import db
from app.models.loan_payment_settings import LoanPaymentSettings
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
    _loan_figures_now,
    _payment_extra_schema,
    _require_configured_loan,
    _transfer_schema,
)
from app.services import escrow_calculator, loan_loaders, loan_recurrence_sync
from app.services.recurring_transfer_query import (
    active_recurring_transfer_template,
)
from app.utils.auth_helpers import require_owner

logger = logging.getLogger(__name__)


def _resolve_transfer_amount(account, data):
    """Resolve the loan-payment transfer amount and live-derivation flag.

    A user-supplied amount is respected verbatim (no live derivation);
    otherwise the amount defaults to the full monthly payment (P&I +
    escrow) and opts into live derivation so the projected cash debit
    tracks the loan's monthly payment after an escrow or rate change
    instead of staying frozen at the default.

    The seam figure owns the P&I for both ARM (re-amortized from the latest
    anchor's balance over the remaining term) and fixed-rate (contractual payment
    from origination), so the computed default matches the dashboard's displayed
    "Total Monthly (with escrow)" exactly (the loan card reads the same figure).

    Args:
        account: ORM :class:`Account` instance for the loan account.
        data: Validated transfer form data (mapping).

    Returns:
        Tuple of (Decimal transfer amount, bool derive_from_loan).
    """
    if "amount" in data and data["amount"] is not None:
        return data["amount"], False

    escrow_lines = loan_loaders.load_escrow_lines(account.id)
    escrow_components = escrow_calculator.resolve_active_lines(
        escrow_lines, date.today(),
    )
    transfer_amount = escrow_calculator.calculate_total_payment(
        _loan_figures_now(account).monthly_payment, escrow_components,
    )
    return transfer_amount, True


def _created_transfer_flash(source_name, dest_name, base_amount, extra_principal):
    """Build the success flash for a created recurring loan payment.

    Names the total monthly cash (base + extra) so the operator sees what will
    actually debit, and appends the base / extra split only when a standing
    overpayment was set (the common no-extra case reads as before).

    Args:
        source_name: The funding account's display name.
        dest_name: The loan account's display name.
        base_amount: The stored base payment (P&I + escrow, or the typed amount).
        extra_principal: The standing extra principal (0.00 when none).

    Returns:
        The flash message string.
    """
    total = base_amount + extra_principal
    extra_note = (
        f" (${base_amount:,.2f} payment + ${extra_principal:,.2f} "
        f"extra principal)" if extra_principal > 0 else ""
    )
    return (
        f"Recurring monthly transfer of ${total:,.2f} created "
        f"from {source_name} to {dest_name}{extra_note}."
    )


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
        account, data,
    )
    # The standing overpayment (spec Sec. 6): stored on the settings row and
    # added live to every payment, in BOTH modes -- NOT baked into
    # ``default_amount``, which stays the base P&I + escrow (derive) or typed
    # base (manual).  Schema default 0.00 when the field is blank.
    extra_principal = data["extra_principal"]

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

    # Create transfer template via the shared builder.
    template_name = f"{source_account.name} -> {account.name} Payment"
    template = build_recurring_transfer_template(
        source_account=source_account,
        dest_account=account,
        rule=rule,
        name=template_name,
        default_amount=transfer_amount,
    )
    # Loan-payment transfers carry their loan-payment settings in a 1:1
    # ``loan_payment_settings`` row (decision B) rather than a column on the
    # generic template: ``derive_from_loan`` opts the projected cash debit into
    # live derivation so it tracks the loan's monthly payment after an escrow or
    # rate change.  Attached via the relationship so it flushes with the template
    # (the shared builder leaves generic / investment transfers with no settings
    # row, which every reader defaults to non-derive).
    template.settings = LoanPaymentSettings(
        derive_from_loan=derive_from_loan, extra_principal=extra_principal,
    )

    namedup_redirect = flush_template_or_namedup_redirect(
        redirect=RedirectTarget("loan.dashboard", {"account_id": account_id}),
    )
    if namedup_redirect is not None:
        return namedup_redirect

    # R-4: bound the new recurrence to the loan's projected payoff BEFORE
    # generating, so no shadow transaction is ever generated past payoff (the
    # template is flushed above, so the sync finds it).
    loan_recurrence_sync.sync_recurring_payment_end_date(account.id)

    # Generate transfers for existing pay periods.
    generate_transfers_for_all_periods(template)

    db.session.commit()

    logger.info(
        "Created recurring payment transfer for loan %d: $%s + $%s extra "
        "from account %d",
        account.id, transfer_amount, extra_principal, source_account.id,
    )
    flash(
        _created_transfer_flash(
            source_account.name, account.name,
            transfer_amount, extra_principal,
        ),
        "success",
    )
    return redirect(url_for("loan.dashboard", account_id=account_id))


@loan_bp.route(
    "/accounts/<int:account_id>/loan/payment-settings", methods=["POST"],
)
@login_required
@require_owner
def update_payment_settings(account_id):
    """Update a loan's recurring-payment standing extra principal.

    The dashboard's extra-principal control posts here.  Updates the active
    recurring payment's ``loan_payment_settings.extra_principal`` (creating the
    settings row when a legacy manual payment has none), then re-syncs the
    recurrence end date, since a changed extra moves the projected payoff (so no
    shadow is generated past the new, earlier payoff).  The extra is a LIVE
    parameter -- applied at display, settle, and projection from this one value
    -- so no shadow regeneration is needed.

    404s a cross-owner / non-loan account (``_require_configured_loan``);
    redirects with a warning when the loan has no recurring payment to edit.
    """
    account, _params, _ = _require_configured_loan(account_id)

    dashboard = RedirectTarget("loan.dashboard", {"account_id": account_id})
    errors = _payment_extra_schema.validate(request.form)
    if errors:
        flash("Please enter a valid extra principal amount.", "danger")
        return dashboard.to_response()

    data = _payment_extra_schema.load(request.form)
    extra_principal = data["extra_principal"]

    template = active_recurring_transfer_template(account.id, current_user.id)
    if template is None:
        flash("This loan has no recurring payment to update.", "warning")
        return dashboard.to_response()

    # Update the extra on the settings row, creating it for a legacy manual
    # payment that never had one (a template with no settings row resolves to
    # non-derive, which is exactly a manual payment).
    if template.settings is None:
        template.settings = LoanPaymentSettings(
            derive_from_loan=False, extra_principal=extra_principal,
        )
    else:
        template.settings.extra_principal = extra_principal

    # A changed extra moves the projected payoff, so re-bound the recurrence
    # (the template already exists, so the sync finds it).
    loan_recurrence_sync.sync_recurring_payment_end_date(account.id)
    db.session.commit()

    logger.info(
        "Updated extra principal for loan %d to $%s",
        account.id, extra_principal,
    )
    flash(
        f"Extra principal set to ${extra_principal:,.2f} per payment.",
        "success",
    )
    return redirect(url_for("loan.dashboard", account_id=account_id))
