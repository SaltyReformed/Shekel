"""
Shekel Budget App -- Loan Routes

Unified dashboard, parameter updates, escrow management, rate history,
and payoff calculator for all installment loan account types.
"""

import logging
from datetime import date
from decimal import Decimal, ROUND_DOWN

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import AcctTypeEnum, RecurrencePatternEnum
from app.extensions import db
from app.models.account import Account
from app.models.loan_params import LoanParams
from app.models.loan_features import RateHistory, EscrowComponent
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import AccountType
from app.models.scenario import Scenario
from app.models.transfer_template import TransferTemplate
from app.schemas.validation import (
    EscrowComponentSchema,
    LoanParamsCreateSchema,
    LoanParamsUpdateSchema,
    LoanPaymentTransferSchema,
    PayoffCalculatorSchema,
    RateChangeSchema,
)
from app.services import (
    amortization_engine,
    escrow_calculator,
    pay_period_service,
    transfer_recurrence,
)
from app.services.amortization_engine import RateChangeRecord
from app.services.loan_payment_service import get_payment_history
from app.utils.formatting import pct_to_decimal

logger = logging.getLogger(__name__)

loan_bp = Blueprint("loan", __name__)

_create_schema = LoanParamsCreateSchema()
_update_schema = LoanParamsUpdateSchema()
_rate_schema = RateChangeSchema()
_escrow_schema = EscrowComponentSchema()
_payoff_schema = PayoffCalculatorSchema()
_transfer_schema = LoanPaymentTransferSchema()


def _load_loan_account(account_id):
    """Load and validate a loan account for the current user.

    Verifies ownership and that the account type has has_amortization=True.

    Returns:
        (account, params, account_type) or (None, None, None) if invalid.
    """
    account = db.session.get(Account, account_id)
    if account is None or account.user_id != current_user.id:
        return None, None, None

    account_type = db.session.get(AccountType, account.account_type_id)
    if account_type is None or not account_type.has_amortization:
        return None, None, None

    params = (
        db.session.query(LoanParams)
        .filter_by(account_id=account.id)
        .first()
    )
    return account, params, account_type


def _build_chart_data(schedule):
    """Build chart data from an amortization schedule."""
    labels = []
    balances = []
    for row in schedule:
        labels.append(row.payment_date.strftime("%b %Y"))
        # Presentation boundary: float() for Chart.js JSON serialization.
        balances.append(float(row.remaining_balance))
    return labels, balances


def _find_current_period_row(schedule):
    """Find the schedule row for the current or next upcoming payment.

    Returns the first projected (non-confirmed) row if one exists,
    otherwise the last confirmed row.  Returns None for an empty
    schedule.

    This approach is more robust than date-based lookup because
    shadow transaction dates (biweekly) and schedule payment dates
    (monthly) use different calendars.  The confirmed/projected
    boundary is the cleanest split.

    Args:
        schedule: List of AmortizationRow objects.

    Returns:
        AmortizationRow or None.
    """
    if not schedule:
        return None
    for row in schedule:
        if not row.is_confirmed:
            return row
    # All rows confirmed -- use the last one.
    return schedule[-1]


def _compute_payment_breakdown(schedule, escrow_components):
    """Build payment allocation breakdown for the current period.

    Combines the amortization engine's per-period principal/interest
    split with the escrow calculator's monthly total to show the user
    exactly how their payment is allocated.

    Percentages are computed with a truncate-then-distribute algorithm
    to guarantee they sum to exactly 100.0%.

    Args:
        schedule: List of AmortizationRow objects (committed schedule).
        escrow_components: List of active EscrowComponent objects.

    Returns:
        dict with breakdown data, or None if no schedule data.
    """
    current_row = _find_current_period_row(schedule)
    if current_row is None:
        return None

    principal_portion = current_row.principal + current_row.extra_payment
    interest_portion = current_row.interest
    escrow_portion = escrow_calculator.calculate_monthly_escrow(
        escrow_components,
    )
    total_payment = principal_portion + interest_portion + escrow_portion

    if total_payment <= Decimal("0.00"):
        return None

    # Truncate-then-distribute: guarantees percentages sum to 100.0%.
    one_decimal = Decimal("0.1")
    parts = [
        ("principal", principal_portion),
        ("interest", interest_portion),
        ("escrow", escrow_portion),
    ]
    truncated = {}
    for name, amount in parts:
        raw_pct = amount / total_payment * 100
        truncated[name] = raw_pct.quantize(one_decimal, rounding=ROUND_DOWN)

    residual = Decimal("100.0") - sum(truncated.values())
    # Assign residual to the largest portion.
    largest = max(truncated, key=truncated.get)
    truncated[largest] += residual

    # O-3: Escrow inflation projection.  If any component has a
    # non-null inflation_rate, compute next year's monthly escrow
    # to show the user projected changes.
    next_year_escrow = None
    has_inflation = any(
        getattr(c, "inflation_rate", None)
        for c in escrow_components
    )
    if has_inflation and escrow_portion > Decimal("0.00"):
        next_year_date = date(date.today().year + 1, 1, 1)
        next_year_escrow = escrow_calculator.calculate_monthly_escrow(
            escrow_components, as_of_date=next_year_date,
        )
        # Only show the note if next year differs from current.
        if next_year_escrow == escrow_portion:
            next_year_escrow = None

    return {
        "principal": principal_portion,
        "interest": interest_portion,
        "escrow": escrow_portion,
        "total": total_payment,
        "principal_pct": truncated["principal"],
        "interest_pct": truncated["interest"],
        "escrow_pct": truncated["escrow"],
        "is_confirmed": current_row.is_confirmed,
        "payment_date": current_row.payment_date,
        "next_year_escrow": next_year_escrow,
    }


def _compute_total_payment(params, escrow_components):
    """Compute total monthly payment (P&I + escrow) for OOB updates.

    Returns None if params are missing (no P&I to add to).
    """
    if params is None:
        return None
    proj = amortization_engine.get_loan_projection(params)
    return escrow_calculator.calculate_total_payment(
        proj.summary.monthly_payment, escrow_components,
    )


@loan_bp.route("/accounts/<int:account_id>/loan")
@login_required
def dashboard(account_id):
    """Loan detail page with summary, escrow, rate history, and payoff calculator."""
    account, params, account_type = _load_loan_account(account_id)
    if account is None:
        flash("Loan account not found.", "danger")
        return redirect(url_for("savings.dashboard"))

    if params is None:
        return render_template(
            "loan/setup.html",
            account=account,
            account_type=account_type,
        )

    # Load payment history from shadow income transactions.
    # When no transfers exist, payments is [] and the engine behaves
    # identically to the pre-5.1 no-payments path.
    scenario = (
        db.session.query(Scenario)
        .filter_by(user_id=current_user.id, is_baseline=True)
        .first()
    )
    payments = get_payment_history(account.id, scenario.id) if scenario else []

    # Load rate history for ARM loans and convert to engine-compatible
    # RateChangeRecord instances.  Non-ARM loans pass rate_changes=None.
    rate_history = []
    rate_changes = None
    if params.is_arm:
        rate_history = (
            db.session.query(RateHistory)
            .filter_by(account_id=account.id)
            .order_by(RateHistory.effective_date.desc())
            .all()
        )
        if rate_history:
            rate_changes = [
                RateChangeRecord(
                    effective_date=rh.effective_date,
                    interest_rate=Decimal(str(rh.interest_rate)),
                )
                for rh in rate_history
            ]

    # Calculate projection (summary + schedule) in one call.
    # This is the COMMITTED projection: all payments (confirmed +
    # projected) plus ARM rate changes.
    proj = amortization_engine.get_loan_projection(
        params, payments=payments, rate_changes=rate_changes,
    )
    summary = proj.summary

    # Load escrow components.
    escrow_components = (
        db.session.query(EscrowComponent)
        .filter_by(account_id=account.id, is_active=True)
        .order_by(EscrowComponent.name)
        .all()
    )
    monthly_escrow = escrow_calculator.calculate_monthly_escrow(escrow_components)
    total_payment = escrow_calculator.calculate_total_payment(
        summary.monthly_payment, escrow_components,
    )

    # Payment allocation breakdown for the current period.
    payment_breakdown = _compute_payment_breakdown(
        proj.schedule, escrow_components,
    )

    # --- Multi-scenario chart data ---
    # Original schedule: contractual baseline, no payments, no rate
    # changes.  "What the bank expects."
    principal = Decimal(str(params.current_principal))
    rate = Decimal(str(params.interest_rate))
    remaining = proj.remaining_months

    # For ARM loans, the contractual payment from original terms is
    # meaningless -- params.interest_rate is the current rate, not the
    # origination rate.  Pass None to force re-amortization from
    # current_principal at the current rate.
    original_for_engine = (
        None if params.is_arm
        else Decimal(str(params.original_principal))
    )

    original_schedule = amortization_engine.generate_schedule(
        principal, rate, remaining,
        payment_day=params.payment_day,
        original_principal=original_for_engine,
        term_months=params.term_months,
    )
    chart_labels, chart_original = _build_chart_data(original_schedule)

    # Committed schedule: already computed as proj.schedule.
    has_payments = len(payments) > 0
    if has_payments:
        _, chart_committed = _build_chart_data(proj.schedule)
    else:
        chart_committed = []

    # Floor schedule: confirmed payments only, standard payments
    # forward.  "Where I stand if I cancel all extras today."
    chart_floor = []
    if has_payments:
        confirmed_payments = [p for p in payments if p.is_confirmed]
        floor_schedule = amortization_engine.generate_schedule(
            principal, rate, remaining,
            payment_day=params.payment_day,
            original_principal=original_for_engine,
            term_months=params.term_months,
            payments=confirmed_payments if confirmed_payments else None,
            rate_changes=rate_changes,
        )
        _, chart_floor = _build_chart_data(floor_schedule)

    # Recurring payment transfer prompt: show when LoanParams exist
    # but no active recurring transfer template targets this account.
    has_recurring_transfer = (
        db.session.query(TransferTemplate)
        .filter(
            TransferTemplate.user_id == current_user.id,
            TransferTemplate.to_account_id == account.id,
            TransferTemplate.is_active.is_(True),
            TransferTemplate.recurrence_rule_id.isnot(None),
        )
        .first()
    ) is not None

    show_transfer_prompt = not has_recurring_transfer

    # Source accounts for the transfer prompt dropdown: active accounts
    # excluding the current debt account and other amortizing accounts.
    source_accounts = []
    if show_transfer_prompt:
        all_accounts = (
            db.session.query(Account)
            .join(AccountType)
            .filter(
                Account.user_id == current_user.id,
                Account.is_active.is_(True),
                Account.id != account.id,
                AccountType.has_amortization.is_(False),
            )
            .order_by(Account.sort_order, Account.name)
            .all()
        )
        source_accounts = all_accounts

    # Default to the checking account if one exists.
    checking_type_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)
    default_source_id = None
    for acct in source_accounts:
        if acct.account_type_id == checking_type_id:
            default_source_id = acct.id
            break

    return render_template(
        "loan/dashboard.html",
        account=account,
        account_type=account_type,
        params=params,
        summary=summary,
        escrow_components=escrow_components,
        monthly_escrow=monthly_escrow,
        total_payment=total_payment,
        payment_breakdown=payment_breakdown,
        rate_history=rate_history,
        chart_labels=chart_labels,
        chart_original=chart_original,
        chart_committed=chart_committed,
        chart_floor=chart_floor,
        has_payments=has_payments,
        show_transfer_prompt=show_transfer_prompt,
        source_accounts=source_accounts,
        default_source_id=default_source_id,
    )


@loan_bp.route("/accounts/<int:account_id>/loan/setup", methods=["POST"])
@login_required
def create_params(account_id):
    """Create initial loan parameters."""
    account = db.session.get(Account, account_id)
    if account is None or account.user_id != current_user.id:
        flash("Account not found.", "danger")
        return redirect(url_for("savings.dashboard"))

    account_type = db.session.get(AccountType, account.account_type_id)
    if account_type is None or not account_type.has_amortization:
        flash("This account type does not support loan parameters.", "warning")
        return redirect(url_for("savings.dashboard"))

    # Check if params already exist.
    existing = db.session.query(LoanParams).filter_by(account_id=account.id).first()
    if existing:
        flash("Loan parameters already configured.", "info")
        return redirect(url_for("loan.dashboard", account_id=account_id))

    errors = _create_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return render_template(
            "loan/setup.html", account=account, account_type=account_type,
        )

    data = _create_schema.load(request.form)

    # Type-specific term validation.
    max_term = account_type.max_term_months
    if max_term and data.get("term_months", 0) > max_term:
        flash(
            f"Term cannot exceed {max_term} months for {account_type.name}.",
            "danger",
        )
        return render_template(
            "loan/setup.html", account=account, account_type=account_type,
        )

    # Convert percentage input (e.g. 6.5) to decimal (0.065) for storage.
    if "interest_rate" in data:
        data["interest_rate"] = pct_to_decimal(data["interest_rate"])

    params = LoanParams(account_id=account.id, **data)
    db.session.add(params)
    db.session.commit()

    logger.info("Created loan params for account %d", account.id)
    flash("Loan parameters configured.", "success")
    return redirect(url_for("loan.dashboard", account_id=account_id))


@loan_bp.route("/accounts/<int:account_id>/loan/params", methods=["POST"])
@login_required
def update_params(account_id):
    """Update loan parameters."""
    account, params, account_type = _load_loan_account(account_id)
    if account is None or params is None:
        flash("Loan account not found.", "danger")
        return redirect(url_for("savings.dashboard"))

    errors = _update_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("loan.dashboard", account_id=account_id))

    data = _update_schema.load(request.form)

    # Type-specific term validation.
    max_term = account_type.max_term_months
    if max_term and data.get("term_months", 0) > max_term:
        flash(
            f"Term cannot exceed {max_term} months for {account_type.name}.",
            "danger",
        )
        return redirect(url_for("loan.dashboard", account_id=account_id))

    # Convert percentage input (e.g. 6.5) to decimal (0.065) for storage.
    if "interest_rate" in data:
        data["interest_rate"] = pct_to_decimal(data["interest_rate"])

    _PARAM_FIELDS = {
        "current_principal", "interest_rate", "payment_day", "term_months",
        "is_arm", "arm_first_adjustment_months", "arm_adjustment_interval_months",
    }
    for field, value in data.items():
        if field in _PARAM_FIELDS:
            setattr(params, field, value)

    db.session.commit()
    logger.info("Updated loan params for account %d", account.id)
    flash("Loan parameters updated.", "success")
    return redirect(url_for("loan.dashboard", account_id=account_id))


@loan_bp.route("/accounts/<int:account_id>/loan/rate", methods=["POST"])
@login_required
def add_rate_change(account_id):
    """Record a variable-rate change (HTMX)."""
    account, params, account_type = _load_loan_account(account_id)
    if account is None or params is None:
        return "Account not found", 404

    errors = _rate_schema.validate(request.form)
    if errors:
        return "Please correct the highlighted errors and try again.", 400

    data = _rate_schema.load(request.form)

    # Convert percentage input (e.g. 6.5) to decimal (0.065) for storage.
    data["interest_rate"] = pct_to_decimal(data["interest_rate"])

    entry = RateHistory(
        account_id=account.id,
        effective_date=data["effective_date"],
        interest_rate=data["interest_rate"],
        notes=data.get("notes"),
    )
    db.session.add(entry)

    # Also update the current rate on params.
    params.interest_rate = data["interest_rate"]
    db.session.commit()

    logger.info("Recorded rate change for loan %d: %s", account.id, data["interest_rate"])

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


@loan_bp.route("/accounts/<int:account_id>/loan/escrow", methods=["POST"])
@login_required
def add_escrow(account_id):
    """Add an escrow component (HTMX)."""
    account, params, account_type = _load_loan_account(account_id)
    if account is None:
        return "Account not found", 404

    errors = _escrow_schema.validate(request.form)
    if errors:
        return "Please correct the highlighted errors and try again.", 400

    data = _escrow_schema.load(request.form)

    # Convert percentage input (e.g. 3 -> 0.03) for storage.
    if data.get("inflation_rate") is not None:
        data["inflation_rate"] = pct_to_decimal(data["inflation_rate"])

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
    total_payment = _compute_total_payment(params, escrow_components)

    return render_template(
        "loan/_escrow_list.html",
        account=account,
        escrow_components=escrow_components,
        monthly_escrow=monthly_escrow,
        total_payment=total_payment,
    )


@loan_bp.route(
    "/accounts/<int:account_id>/loan/escrow/<int:component_id>/delete",
    methods=["POST"],
)
@login_required
def delete_escrow(account_id, component_id):
    """Remove an escrow component (HTMX)."""
    account, _, account_type = _load_loan_account(account_id)
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
    total_payment = _compute_total_payment(params, escrow_components)

    return render_template(
        "loan/_escrow_list.html",
        account=account,
        escrow_components=escrow_components,
        monthly_escrow=monthly_escrow,
        total_payment=total_payment,
    )


@loan_bp.route("/accounts/<int:account_id>/loan/payoff", methods=["POST"])
@login_required
def payoff_calculate(account_id):
    """Calculate payoff scenario (HTMX)."""
    account, params, account_type = _load_loan_account(account_id)
    if account is None or params is None:
        return "Account not found", 404

    errors = _payoff_schema.validate(request.form)
    if errors:
        return render_template(
            "loan/_payoff_results.html",
            error="Please correct the highlighted errors and try again.",
        )

    data = _payoff_schema.load(request.form)
    mode = data["mode"]
    remaining_months = amortization_engine.calculate_remaining_months(
        params.origination_date, params.term_months,
    )
    schedule_start = date.today().replace(day=1)

    principal = Decimal(str(params.current_principal))
    rate = Decimal(str(params.interest_rate))

    # For ARM loans, the contractual payment from original terms is
    # meaningless -- params.interest_rate is the current rate, not the
    # origination rate.  Pass None to force re-amortization from
    # current_principal at the current rate.
    original = (
        None if params.is_arm
        else Decimal(str(params.original_principal))
    )

    # Load payment history for payment-aware projections.
    scenario = (
        db.session.query(Scenario)
        .filter_by(user_id=current_user.id, is_baseline=True)
        .first()
    )
    payments = get_payment_history(account.id, scenario.id) if scenario else []

    # Load rate changes for ARM loans (same pattern as dashboard).
    rate_changes = None
    if params.is_arm:
        rate_history = (
            db.session.query(RateHistory)
            .filter_by(account_id=account.id)
            .order_by(RateHistory.effective_date)
            .all()
        )
        if rate_history:
            rate_changes = [
                RateChangeRecord(
                    effective_date=rh.effective_date,
                    interest_rate=Decimal(str(rh.interest_rate)),
                )
                for rh in rate_history
            ]

    if mode == "extra_payment":
        extra = Decimal(str(data.get("extra_monthly", "0")))
        payoff_summary = amortization_engine.calculate_summary(
            current_principal=principal,
            annual_rate=rate,
            remaining_months=remaining_months,
            origination_date=schedule_start,
            payment_day=params.payment_day,
            term_months=params.term_months,
            extra_monthly=extra,
            original_principal=original,
            payments=payments,
            rate_changes=rate_changes,
        )

        # --- Multi-scenario chart data for payoff calculator ---
        # Original: contractual baseline, no payments, no rate changes.
        original_schedule = amortization_engine.generate_schedule(
            principal, rate, remaining_months,
            payment_day=params.payment_day,
            original_principal=original,
            term_months=params.term_months,
        )
        # Committed: all payments (confirmed + projected), no extra.
        committed_schedule = amortization_engine.generate_schedule(
            principal, rate, remaining_months,
            payment_day=params.payment_day,
            original_principal=original,
            term_months=params.term_months,
            payments=payments,
            rate_changes=rate_changes,
        )
        # Accelerated: committed payments + extra_monthly.
        accelerated_schedule = amortization_engine.generate_schedule(
            principal, rate, remaining_months,
            extra_monthly=extra,
            payment_day=params.payment_day,
            original_principal=original,
            term_months=params.term_months,
            payments=payments,
            rate_changes=rate_changes,
        )

        chart_labels, chart_original = _build_chart_data(original_schedule)
        _, chart_committed = _build_chart_data(committed_schedule)
        _, chart_accelerated = _build_chart_data(accelerated_schedule)

        has_payments = len(payments) > 0

        # Comparison metrics: committed vs. original.
        committed_months_saved = (
            len(original_schedule) - len(committed_schedule)
        )
        original_interest = sum(
            (r.interest for r in original_schedule), Decimal("0.00"),
        )
        committed_interest = sum(
            (r.interest for r in committed_schedule), Decimal("0.00"),
        )
        committed_interest_saved = (
            original_interest - committed_interest
        ).quantize(Decimal("0.01"))

        return render_template(
            "loan/_payoff_results.html",
            mode=mode,
            payoff_summary=payoff_summary,
            chart_labels=chart_labels,
            chart_original=chart_original,
            chart_committed=chart_committed if has_payments else [],
            chart_accelerated=chart_accelerated,
            has_payments=has_payments,
            committed_months_saved=committed_months_saved,
            committed_interest_saved=committed_interest_saved,
        )

    elif mode == "target_date":
        target_date = data.get("target_date")
        if not target_date:
            return render_template(
                "loan/_payoff_results.html",
                error="Target date is required.",
            )

        required_extra = amortization_engine.calculate_payoff_by_date(
            current_principal=principal,
            annual_rate=rate,
            remaining_months=remaining_months,
            target_date=target_date,
            origination_date=schedule_start,
            payment_day=params.payment_day,
            original_principal=original,
            term_months=params.term_months,
            rate_changes=rate_changes,
        )

        # For ARM loans, the displayed monthly payment is the re-amortized
        # payment from current balance and remaining term.  For fixed-rate
        # loans, use the contractual payment from original terms.
        if params.is_arm:
            monthly_payment = amortization_engine.calculate_monthly_payment(
                principal, rate, remaining_months,
            )
        else:
            monthly_payment = amortization_engine.calculate_monthly_payment(
                Decimal(str(params.original_principal)), rate,
                params.term_months,
            )

        return render_template(
            "loan/_payoff_results.html",
            mode=mode,
            required_extra=required_extra,
            monthly_payment=monthly_payment,
        )

    return render_template(
        "loan/_payoff_results.html",
        error="Invalid mode.",
    )


@loan_bp.route("/accounts/<int:account_id>/loan/create-transfer", methods=["POST"])
@login_required
def create_payment_transfer(account_id):
    """Create a recurring monthly transfer to a debt account.

    Creates a RecurrenceRule (monthly pattern), a TransferTemplate
    (from the selected source account to the debt account), and
    generates Transfer records (with shadow transactions) for
    existing pay periods.

    The amount defaults to the computed monthly payment (P&I + escrow).
    The user may override with a custom amount.
    """
    account, params, _ = _load_loan_account(account_id)
    if account is None or params is None:
        flash("Loan account not found.", "danger")
        return redirect(url_for("savings.dashboard"))

    errors = _transfer_schema.validate(request.form)
    if errors:
        flash("Please correct the errors and try again.", "danger")
        return redirect(url_for("loan.dashboard", account_id=account_id))

    data = _transfer_schema.load(request.form)
    source_account_id = data["source_account_id"]

    # Verify source account ownership (404 for both "not found" and
    # "not yours" per the security response rule).
    source_account = db.session.get(Account, source_account_id)
    if source_account is None or source_account.user_id != current_user.id:
        flash("Loan account not found.", "danger")
        return redirect(url_for("savings.dashboard"))

    if not source_account.is_active:
        flash("Source account is inactive.", "danger")
        return redirect(url_for("loan.dashboard", account_id=account_id))

    if source_account_id == account_id:
        flash("Source and destination accounts must be different.", "danger")
        return redirect(url_for("loan.dashboard", account_id=account_id))

    # Determine the transfer amount: user override or computed default.
    if "amount" in data and data["amount"] is not None:
        transfer_amount = data["amount"]
    else:
        # Compute P&I + escrow as the full monthly payment.
        # For ARM loans, use re-amortized payment from current balance
        # and remaining term.  For fixed-rate, use contractual payment
        # from original terms.
        if params.is_arm:
            remaining = amortization_engine.calculate_remaining_months(
                params.origination_date, params.term_months,
            )
            monthly_pi = amortization_engine.calculate_monthly_payment(
                Decimal(str(params.current_principal)),
                Decimal(str(params.interest_rate)),
                remaining,
            )
        else:
            monthly_pi = amortization_engine.calculate_monthly_payment(
                Decimal(str(params.original_principal)),
                Decimal(str(params.interest_rate)),
                params.term_months,
            )
        escrow_components = (
            db.session.query(EscrowComponent)
            .filter_by(account_id=account.id, is_active=True)
            .all()
        )
        transfer_amount = escrow_calculator.calculate_total_payment(
            monthly_pi, escrow_components,
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

    # Create transfer template.
    template_name = f"{source_account.name} -> {account.name} Payment"
    template = TransferTemplate(
        user_id=current_user.id,
        from_account_id=source_account.id,
        to_account_id=account.id,
        recurrence_rule_id=rule.id,
        name=template_name,
        default_amount=transfer_amount,
    )
    db.session.add(template)

    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        flash("A recurring transfer with that name already exists.", "warning")
        return redirect(url_for("loan.dashboard", account_id=account_id))

    # Generate transfers for existing pay periods.
    scenario = (
        db.session.query(Scenario)
        .filter_by(user_id=current_user.id, is_baseline=True)
        .first()
    )
    if scenario:
        periods = pay_period_service.get_all_periods(current_user.id)
        transfer_recurrence.generate_for_template(
            template, periods, scenario.id,
        )

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
