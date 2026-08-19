"""
Shekel Budget App -- Loan route package: recurring payment transfer.

Creates a recurring monthly transfer (RecurrenceRule + TransferTemplate +
generated Transfer records with shadow transactions) from a source account to
the debt account.  The amount defaults to the resolver-derived monthly payment
(P&I + escrow) with live derivation, or a user-supplied override.
"""

import logging
from datetime import date
from decimal import Decimal

from flask import Response, flash, redirect, request, url_for
from flask_login import current_user, login_required

from app.enums import RecurrenceUnitEnum
from app.exceptions import (
    NotFoundError,
    ValidationError as ShekelValidationError,
)
from app.extensions import db
from app.models.loan_payment_settings import LoanPaymentSettings
from app.routes._redirect_target import RedirectTarget
from app.routes._transfer_creation_helpers import (
    build_recurring_transfer_template,
    flush_template_or_namedup_redirect,
    generate_transfers_for_all_periods,
    validate_and_resolve_source_account,
)
from app.routes.loan._bp import loan_bp
from app.routes.loan._helpers import (
    _payment_extra_schema,
    _require_configured_loan,
    _total_payment_from_seam,
    _transfer_schema,
)
from app.services import (
    escrow_calculator,
    loan_loaders,
    loan_recurrence_sync,
    template_amount_service,
)
from app.services.pay_calendar import calendar_for
from app.services.recurrence import RecurrenceSpec, author_rule
from app.services.recurring_transfer_query import (
    active_recurring_transfer_template,
)
from app.utils.auth_helpers import require_owner
from app.utils.dates import display_today

logger = logging.getLogger(__name__)


def _contractual_monthly_payment(account) -> Decimal:
    """Return the loan's full contractual monthly payment today: P&I + active escrow.

    The "what the loan needs this month" figure for the create default
    (:func:`_resolve_transfer_amount`) and the auto-track switch
    (:func:`track_payment`): today's active escrow
    (:func:`~app.services.escrow_calculator.resolve_active_lines`) plus the seam
    P&I, summed by the shared
    :func:`app.routes.loan._helpers._total_payment_from_seam`.  It equals the loan
    card's displayed "Total Monthly" and the dashboard's drift comparison by
    construction -- they read the same seam ``monthly_payment`` and the same
    ``resolve_active_lines(load_escrow_lines(id), today)`` set
    (:func:`app.services.loan_payment_service.load_loan_context`) through the same
    leaf -- so the drift the dashboard shows and the amount this writes cannot
    disagree.

    Args:
        account: ORM :class:`Account` instance for the loan account (ownership
            already verified by the caller).

    Returns:
        The contractual monthly payment (P&I + escrow) as a ``Decimal``.
    """
    escrow_components = escrow_calculator.resolve_active_lines(
        loan_loaders.load_escrow_lines(account.id), date.today(),
    )
    return _total_payment_from_seam(account, escrow_components)


def _resolve_transfer_amount(account, data):
    """Resolve the loan-payment transfer amount and live-derivation flag.

    A user-supplied amount is respected verbatim (no live derivation);
    otherwise the amount defaults to the full monthly payment (P&I +
    escrow, :func:`_contractual_monthly_payment`) and opts into live derivation so
    the projected cash debit tracks the loan's monthly payment after an escrow or
    rate change instead of staying frozen at the default.

    Args:
        account: ORM :class:`Account` instance for the loan account.
        data: Validated transfer form data (mapping).

    Returns:
        Tuple of (Decimal transfer amount, bool derive_from_loan).
    """
    if "amount" in data and data["amount"] is not None:
        return data["amount"], False
    return _contractual_monthly_payment(account), True


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

    # Create the monthly recurrence rule, ALREADY bounded by the loan's own
    # contract (developer ruling 2026-08-15, plan step R7c-b).  Its first
    # occurrence is the first contractual installment, and ``nominal_day``
    # carries a servicer's day-31 payment through a 30-day origination month --
    # both from ``loan_recurrence_sync.loan_cadence_start``, the ONE producer of
    # that answer.  It used to be typed here as ``day_of_month=payment_day`` and
    # then overwritten by ``bind_rule_to_loan`` a few lines below, which is the
    # shape that let the GENERIC transfer form discard a user's typed date
    # without saying so.
    cadence_start = loan_recurrence_sync.loan_cadence_start(
        RecurrenceUnitEnum.MONTH, params,
    )
    # Create transfer template via the shared builder.
    template_name = f"{source_account.name} -> {account.name} Payment"
    template = build_recurring_transfer_template(
        source_account=source_account,
        dest_account=account,
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

    # Open the amount's dated series, AFTER the settings row is attached and for
    # exactly that reason (plan step X-au-a): a DERIVE-mode payment's
    # ``default_amount`` is a P&I + escrow snapshot, so it owns no stated amount
    # and must get no version -- and the write door reads the mode off the
    # settings row this line has just set.  A MANUAL payment (the operator typed
    # the base) does open a series here.
    template_amount_service.set_amount(
        template, transfer_amount, effective_on=display_today(),
    )

    namedup_redirect = flush_template_or_namedup_redirect(
        redirect=RedirectTarget("loan.dashboard", {"account_id": account_id}),
    )
    if namedup_redirect is not None:
        return namedup_redirect

    # **The cadence is authored ONTO the template** (plan step R-F6): the rule
    # carries its owner's FK, so the definition has to exist first.  After the
    # name-collision flush rather than before it, because ``author_rule``
    # flushes and an earlier one would surface a duplicate name as an unhandled
    # ``IntegrityError`` instead of that helper's redirect.
    rule = author_rule(
        RecurrenceSpec(
            user_id=current_user.id,
            unit=RecurrenceUnitEnum.MONTH,
            starts_on=cadence_start.starts_on,
            nominal_day=cadence_start.nominal_day,
        ),
        calendar_for(current_user.id),
        template,
    )

    # Bound the new recurrence at BOTH ends BEFORE generating, so no shadow
    # transaction is ever generated outside the loan's life: past the projected
    # payoff (R-4, the account-keyed sync below), or -- the reason this is
    # load-bearing rather than merely tidy -- BEFORE the loan's first
    # contractual installment (C9a).  Without the start bound this route
    # generated a payment into every materialized pay period, including those
    # preceding origination.
    #
    # The START bound is applied to THIS rule directly rather than through the
    # account-keyed sync, which resolves the loan's FIRST active recurring
    # template: on a loan that already has one, that would re-bound the OLD rule
    # and leave the new one unbounded, generating the pre-origination payments
    # this step exists to stop (and, since C9b, failing the write outright when
    # they are refused).
    loan_recurrence_sync.bind_rule_to_loan(rule, account.id)
    loan_recurrence_sync.sync_recurring_payment_bounds(account.id)

    # Generate transfers for existing pay periods.  ``create_transfer`` refuses
    # a payment dated before the loan originates (R-C) and a transfer OUT of a
    # loan, and the recurrence engine fans out through it -- so an unbounded or
    # mis-set rule surfaces here as a rejection rather than a 500 on a clean
    # user action.  Roll back the flushed rule / template and flash, mirroring
    # the generic transfer-template path.
    try:
        generate_transfers_for_all_periods(template)
    except (NotFoundError, ShekelValidationError) as exc:
        db.session.rollback()
        flash(f"Could not create the recurring payment: {exc}", "danger")
        return redirect(url_for("loan.dashboard", account_id=account_id))

    # ...and re-derive it AFTER, because since plan C8d the payoff is a fold over
    # the loan's forward PLAN -- and the payments just generated are part of that
    # plan.  The first call cannot see them (they do not exist yet), so on a loan
    # with overdue installments it bounds against a plan with no records at all
    # and lands months late; the next payoff-affecting mutation would then
    # silently correct it, which is a stored value that disagrees with every
    # screen until something unrelated happens to fix it.  Both calls are needed
    # and neither is redundant: the first BOUNDS generation, this one RECORDS the
    # payoff the generated plan actually implies.  Idempotent, so it is a no-op
    # write whenever the two agree (a healthy loan: the generated payments match
    # the contractual synthesis the first call folded).
    loan_recurrence_sync.sync_recurring_payment_bounds(account.id)


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
    loan_recurrence_sync.sync_recurring_payment_bounds(account.id)
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


@loan_bp.route(
    "/accounts/<int:account_id>/loan/track-payment", methods=["POST"],
)
@login_required
@require_owner
def track_payment(account_id):
    """Switch a loan's recurring payment to auto-track the contractual amount (D3 / C7).

    The one-click resolution for the loan detail page's payment-drift warning:
    when a MANUAL recurring payment has fallen short of the contractual monthly
    payment (P&I + today's escrow) after an escrow or rate change, this flips it to
    ``derive_from_loan`` so its projected cash always equals the contract, and
    resets the stored base (``default_amount``) to today's contract so every
    surface that reads it shows the current figure.  No shadow regeneration is
    needed -- a derive payment's projected cash is recomputed LIVE at read time
    (:meth:`~app.services.loan_payment_service.LoanPricing.live_cash`), the
    same mechanism a freshly-created derive transfer relies on -- and the
    recurrence end date is re-synced since a higher tracked payment can move the
    projected payoff.

    404s a cross-owner / non-loan account (``_require_configured_loan``); redirects
    with a warning when the loan has no recurring payment to switch.
    """
    account, _params, _ = _require_configured_loan(account_id)
    dashboard = RedirectTarget("loan.dashboard", {"account_id": account_id})

    template = active_recurring_transfer_template(account.id, current_user.id)
    if template is None:
        flash("This loan has no recurring payment to update.", "warning")
        return dashboard.to_response()

    contract = _contractual_monthly_payment(account)
    # Flip to derive FIRST, creating the settings row for a legacy manual payment
    # that never had one (a missing settings row IS manual mode); the standing
    # extra is preserved, added live on top of the tracked base exactly as
    # before.  **The order is load-bearing since plan step X-au-a**: the stored
    # base written below is a DERIVED figure (P&I + today's escrow), so it must
    # not be recorded as a stated price in the amount series -- and the write
    # door reads the mode off the settings row, so flipping after the write
    # would stamp exactly the fake history ruling R-FI refuses.  Versions the
    # template recorded while it was manual stay as the record of what was
    # stated then.
    if template.settings is None:
        template.settings = LoanPaymentSettings(
            derive_from_loan=True, extra_principal=Decimal("0.00"),
        )
    else:
        template.settings.derive_from_loan = True
    template_amount_service.set_amount(
        template, contract, effective_on=display_today(),
    )

    # Re-sync the recurrence end date.  **Load-bearing since plan C8d, where it
    # used to be defensive:** the payoff is now a fold over the forward PLAN, and
    # the PLANNED tier folds each projected shadow's cash as
    # ``live_cash.get(shadow.id, <the shadow's own contribution>)``.  Flipping
    # manual->derive is exactly what moves that value -- a manual payment folds
    # its typed figure, a derive one the live contractual cash --
    # so this switch MOVES the projected payoff whenever the two differ, which is
    # the drift C7 flagged to get the user here in the first place.
    loan_recurrence_sync.sync_recurring_payment_bounds(account.id)
    db.session.commit()

    logger.info(
        "Switched loan %d recurring payment to auto-track ($%s)",
        account.id, contract,
    )
    flash(
        f"Recurring payment now tracks the loan automatically "
        f"(${contract:,.2f} this month).",
        "success",
    )
    return redirect(url_for("loan.dashboard", account_id=account_id))
