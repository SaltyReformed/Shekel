"""
Shekel Budget App -- Mortgage Routes

Dashboard, parameter updates, escrow management, rate history,
and payoff calculator for mortgage accounts.
"""

import logging
from datetime import date
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import ref_cache
from app.enums import AcctTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.mortgage_params import MortgageParams, MortgageRateHistory, EscrowComponent
from app.schemas.validation import (
    EscrowComponentSchema,
    MortgageParamsCreateSchema,
    MortgageParamsUpdateSchema,
    MortgageRateChangeSchema,
    PayoffCalculatorSchema,
)
from app.services import amortization_engine, escrow_calculator

logger = logging.getLogger(__name__)

mortgage_bp = Blueprint("mortgage", __name__)

_create_schema = MortgageParamsCreateSchema()
_params_schema = MortgageParamsUpdateSchema()
_rate_schema = MortgageRateChangeSchema()
_escrow_schema = EscrowComponentSchema()
_payoff_schema = PayoffCalculatorSchema()


def _load_mortgage_account(account_id):
    """Load and validate a mortgage account for the current user.

    Returns (account, params) or (None, None) if invalid.
    """
    account = db.session.get(Account, account_id)
    if account is None or account.user_id != current_user.id:
        return None, None

    if not account.account_type or account.account_type_id != ref_cache.acct_type_id(AcctTypeEnum.MORTGAGE):
        return None, None

    params = (
        db.session.query(MortgageParams)
        .filter_by(account_id=account.id)
        .first()
    )
    return account, params


def _build_chart_data(schedule):
    """Build chart data from an amortization schedule."""
    labels = []
    balances = []
    for row in schedule:
        labels.append(row.payment_date.strftime("%b %Y"))
        # Presentation boundary: float() for Chart.js JSON serialization.
        balances.append(float(row.remaining_balance))
    return labels, balances


def _compute_total_payment(params, escrow_components):
    """Compute total monthly payment (P&I + escrow) for OOB updates.

    Returns None if params are missing (no P&I to add to).
    """
    if params is None:
        return None
    remaining_months = amortization_engine.calculate_remaining_months(
        params.origination_date, params.term_months,
    )
    summary = amortization_engine.calculate_summary(
        current_principal=Decimal(str(params.current_principal)),
        annual_rate=Decimal(str(params.interest_rate)),
        remaining_months=remaining_months,
        origination_date=date.today().replace(day=1),
        payment_day=params.payment_day,
        term_months=params.term_months,
    )
    return escrow_calculator.calculate_total_payment(
        summary.monthly_payment, escrow_components,
    )


@mortgage_bp.route("/accounts/<int:account_id>/mortgage")
@login_required
def dashboard(account_id):
    """Mortgage detail page with summary, escrow, and payoff calculator."""
    account, params = _load_mortgage_account(account_id)
    if account is None:
        flash("Mortgage account not found.", "danger")
        return redirect(url_for("savings.dashboard"))

    if params is None:
        return render_template("mortgage/setup.html", account=account)

    # Calculate summary.
    remaining_months = amortization_engine.calculate_remaining_months(
        params.origination_date, params.term_months,
    )
    # Use today as schedule start so payoff dates are forward-looking.
    schedule_start = date.today().replace(day=1)
    summary = amortization_engine.calculate_summary(
        current_principal=Decimal(str(params.current_principal)),
        annual_rate=Decimal(str(params.interest_rate)),
        remaining_months=remaining_months,
        origination_date=schedule_start,
        payment_day=params.payment_day,
        term_months=params.term_months,
    )

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

    # Rate history (for ARM).
    rate_history = []
    if params.is_arm:
        rate_history = (
            db.session.query(MortgageRateHistory)
            .filter_by(account_id=account.id)
            .order_by(MortgageRateHistory.effective_date.desc())
            .all()
        )

    # Chart data -- schedule starts from now, not origination.
    schedule = amortization_engine.generate_schedule(
        Decimal(str(params.current_principal)),
        Decimal(str(params.interest_rate)),
        remaining_months,
        payment_day=params.payment_day,
    )
    chart_labels, chart_standard = _build_chart_data(schedule)

    return render_template(
        "mortgage/dashboard.html",
        account=account,
        params=params,
        summary=summary,
        escrow_components=escrow_components,
        monthly_escrow=monthly_escrow,
        total_payment=total_payment,
        rate_history=rate_history,
        chart_labels=chart_labels,
        chart_standard=chart_standard,
    )


@mortgage_bp.route("/accounts/<int:account_id>/mortgage/setup", methods=["POST"])
@login_required
def create_params(account_id):
    """Create initial mortgage parameters."""
    account = db.session.get(Account, account_id)
    if account is None or account.user_id != current_user.id:
        flash("Account not found.", "danger")
        return redirect(url_for("savings.dashboard"))
    if not account.account_type or account.account_type_id != ref_cache.acct_type_id(AcctTypeEnum.MORTGAGE):
        flash("This account is not a mortgage.", "warning")
        return redirect(url_for("savings.dashboard"))

    # Check if params already exist.
    existing = db.session.query(MortgageParams).filter_by(account_id=account.id).first()
    if existing:
        flash("Mortgage parameters already configured.", "info")
        return redirect(url_for("mortgage.dashboard", account_id=account_id))

    errors = _create_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return render_template("mortgage/setup.html", account=account)

    data = _create_schema.load(request.form)
    # Convert percentage input (e.g. 6.5) to decimal (0.065) for storage.
    if "interest_rate" in data:
        data["interest_rate"] = Decimal(str(data["interest_rate"])) / 100
    params = MortgageParams(account_id=account.id, **data)
    db.session.add(params)
    db.session.commit()

    logger.info("Created mortgage params for account %d", account.id)
    flash("Mortgage parameters configured.", "success")
    return redirect(url_for("mortgage.dashboard", account_id=account_id))


@mortgage_bp.route("/accounts/<int:account_id>/mortgage/params", methods=["POST"])
@login_required
def update_params(account_id):
    """Update mortgage parameters."""
    account, params = _load_mortgage_account(account_id)
    if account is None or params is None:
        flash("Mortgage account not found.", "danger")
        return redirect(url_for("savings.dashboard"))

    errors = _params_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("mortgage.dashboard", account_id=account_id))

    data = _params_schema.load(request.form)

    # Convert percentage input (e.g. 6.5) to decimal (0.065) for storage.
    if "interest_rate" in data:
        data["interest_rate"] = Decimal(str(data["interest_rate"])) / 100

    _PARAM_FIELDS = {
        "current_principal", "interest_rate", "payment_day",
        "is_arm", "arm_first_adjustment_months", "arm_adjustment_interval_months",
    }
    for field, value in data.items():
        if field in _PARAM_FIELDS:
            setattr(params, field, value)

    db.session.commit()
    logger.info("Updated mortgage params for account %d", account.id)
    flash("Mortgage parameters updated.", "success")
    return redirect(url_for("mortgage.dashboard", account_id=account_id))


@mortgage_bp.route("/accounts/<int:account_id>/mortgage/rate", methods=["POST"])
@login_required
def add_rate_change(account_id):
    """Record an ARM rate change (HTMX)."""
    account, params = _load_mortgage_account(account_id)
    if account is None or params is None:
        return "Account not found", 404

    errors = _rate_schema.validate(request.form)
    if errors:
        return "Please correct the highlighted errors and try again.", 400

    data = _rate_schema.load(request.form)

    # Convert percentage input (e.g. 6.5) to decimal (0.065) for storage.
    data["interest_rate"] = Decimal(str(data["interest_rate"])) / 100

    entry = MortgageRateHistory(
        account_id=account.id,
        effective_date=data["effective_date"],
        interest_rate=data["interest_rate"],
        notes=data.get("notes"),
    )
    db.session.add(entry)

    # Also update the current rate on params.
    params.interest_rate = data["interest_rate"]
    db.session.commit()

    logger.info("Recorded rate change for mortgage %d: %s", account.id, data["interest_rate"])

    rate_history = (
        db.session.query(MortgageRateHistory)
        .filter_by(account_id=account.id)
        .order_by(MortgageRateHistory.effective_date.desc())
        .all()
    )
    return render_template(
        "mortgage/_rate_history.html",
        account=account,
        rate_history=rate_history,
    )


@mortgage_bp.route("/accounts/<int:account_id>/mortgage/escrow", methods=["POST"])
@login_required
def add_escrow(account_id):
    """Add an escrow component (HTMX)."""
    account, params = _load_mortgage_account(account_id)
    if account is None:
        return "Account not found", 404

    errors = _escrow_schema.validate(request.form)
    if errors:
        return "Please correct the highlighted errors and try again.", 400

    data = _escrow_schema.load(request.form)

    # Convert percentage input (e.g. 3 → 0.03) for storage.
    if data.get("inflation_rate") is not None:
        from decimal import Decimal as D
        data["inflation_rate"] = D(str(data["inflation_rate"])) / D("100")

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

    logger.info("Added escrow component '%s' to mortgage %d", data["name"], account.id)

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
        "mortgage/_escrow_list.html",
        account=account,
        escrow_components=escrow_components,
        monthly_escrow=monthly_escrow,
        total_payment=total_payment,
    )


@mortgage_bp.route(
    "/accounts/<int:account_id>/mortgage/escrow/<int:component_id>/delete",
    methods=["POST"],
)
@login_required
def delete_escrow(account_id, component_id):
    """Remove an escrow component (HTMX)."""
    account, _ = _load_mortgage_account(account_id)
    if account is None:
        return "Account not found", 404

    comp = db.session.get(EscrowComponent, component_id)
    if comp is None or comp.account_id != account.id:
        return "Component not found", 404

    comp.is_active = False
    db.session.commit()
    logger.info("Deactivated escrow component %d from mortgage %d", component_id, account.id)

    escrow_components = (
        db.session.query(EscrowComponent)
        .filter_by(account_id=account.id, is_active=True)
        .order_by(EscrowComponent.name)
        .all()
    )

    # Compute updated payment summary for OOB swap.
    params = (
        db.session.query(MortgageParams)
        .filter_by(account_id=account.id)
        .first()
    )
    monthly_escrow = escrow_calculator.calculate_monthly_escrow(escrow_components)
    total_payment = _compute_total_payment(params, escrow_components)

    return render_template(
        "mortgage/_escrow_list.html",
        account=account,
        escrow_components=escrow_components,
        monthly_escrow=monthly_escrow,
        total_payment=total_payment,
    )


@mortgage_bp.route("/accounts/<int:account_id>/mortgage/payoff", methods=["POST"])
@login_required
def payoff_calculate(account_id):
    """Calculate payoff scenario (HTMX)."""
    account, params = _load_mortgage_account(account_id)
    if account is None or params is None:
        return "Account not found", 404

    errors = _payoff_schema.validate(request.form)
    if errors:
        return render_template(
            "mortgage/_payoff_results.html",
            error="Please correct the highlighted errors and try again.",
        )

    data = _payoff_schema.load(request.form)
    mode = data["mode"]
    remaining_months = amortization_engine.calculate_remaining_months(
        params.origination_date, params.term_months,
    )
    schedule_start = date.today().replace(day=1)

    if mode == "extra_payment":
        extra = Decimal(str(data.get("extra_monthly", "0")))
        payoff_summary = amortization_engine.calculate_summary(
            current_principal=Decimal(str(params.current_principal)),
            annual_rate=Decimal(str(params.interest_rate)),
            remaining_months=remaining_months,
            origination_date=schedule_start,
            payment_day=params.payment_day,
            term_months=params.term_months,
            extra_monthly=extra,
        )

        # Generate chart data for comparison.
        standard_schedule = amortization_engine.generate_schedule(
            Decimal(str(params.current_principal)),
            Decimal(str(params.interest_rate)),
            remaining_months,
            payment_day=params.payment_day,
        )
        accelerated_schedule = amortization_engine.generate_schedule(
            Decimal(str(params.current_principal)),
            Decimal(str(params.interest_rate)),
            remaining_months,
            extra_monthly=extra,
            payment_day=params.payment_day,
        )

        chart_labels, chart_standard = _build_chart_data(standard_schedule)
        _, chart_accelerated = _build_chart_data(accelerated_schedule)

        return render_template(
            "mortgage/_payoff_results.html",
            mode=mode,
            payoff_summary=payoff_summary,
            chart_labels=chart_labels,
            chart_standard=chart_standard,
            chart_accelerated=chart_accelerated,
        )

    elif mode == "target_date":
        target_date = data.get("target_date")
        if not target_date:
            return render_template(
                "mortgage/_payoff_results.html",
                error="Target date is required.",
            )

        required_extra = amortization_engine.calculate_payoff_by_date(
            current_principal=Decimal(str(params.current_principal)),
            annual_rate=Decimal(str(params.interest_rate)),
            remaining_months=remaining_months,
            target_date=target_date,
            origination_date=schedule_start,
            payment_day=params.payment_day,
        )

        monthly_payment = amortization_engine.calculate_monthly_payment(
            Decimal(str(params.current_principal)),
            Decimal(str(params.interest_rate)),
            remaining_months,
        )

        return render_template(
            "mortgage/_payoff_results.html",
            mode=mode,
            required_extra=required_extra,
            monthly_payment=monthly_payment,
        )

    return render_template(
        "mortgage/_payoff_results.html",
        error="Invalid mode.",
    )
