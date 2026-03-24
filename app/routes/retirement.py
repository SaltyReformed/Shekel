"""
Shekel Budget App -- Retirement Planning Routes

Retirement dashboard with pension management, income gap analysis,
and retirement planning settings.
"""

import logging
from datetime import date
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.pension_profile import PensionProfile
from app.models.salary_profile import SalaryProfile
from app.models.transfer import Transfer
from app.models.user import UserSettings
from app.models.ref import AccountType
from app.services.investment_projection import calculate_investment_inputs
from app.schemas.validation import (
    PensionProfileCreateSchema,
    PensionProfileUpdateSchema,
    RetirementSettingsSchema,
)
from app.services import (
    growth_engine,
    pay_period_service,
    paycheck_calculator,
    pension_calculator,
    retirement_gap_calculator,
)

logger = logging.getLogger(__name__)

retirement_bp = Blueprint("retirement", __name__)

_pension_create_schema = PensionProfileCreateSchema()
_pension_update_schema = PensionProfileUpdateSchema()
_settings_schema = RetirementSettingsSchema()

# Account types considered "traditional" (pre-tax contributions).
TRADITIONAL_TYPES = frozenset({"401k", "traditional_ira"})


def _compute_gap_data(user_id, swr_override=None, return_rate_override=None):
    """Compute gap analysis data for the retirement dashboard or HTMX fragment.

    Args:
        user_id: The user's ID.
        swr_override: Optional Decimal safe withdrawal rate from slider.
        return_rate_override: Optional Decimal annual return rate from slider.

    Returns:
        dict with keys: gap_analysis, chart_data, pension_benefit,
                        retirement_account_projections, settings,
                        salary_profiles, pensions.
    """
    settings = (
        db.session.query(UserSettings).filter_by(user_id=user_id).first()
    )

    pensions = (
        db.session.query(PensionProfile)
        .filter_by(user_id=user_id, is_active=True)
        .all()
    )
    salary_profiles = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=user_id, is_active=True)
        .all()
    )

    # Calculate pension benefit.
    pension_benefit = None
    monthly_pension_income = Decimal("0")
    salary_by_year = None
    for pension in pensions:
        if pension.planned_retirement_date and pension.salary_profile:
            profile = pension.salary_profile
            start_year = date.today().year
            end_year = pension.planned_retirement_date.year
            salary_by_year = pension_calculator.project_salaries_by_year(
                Decimal(str(profile.annual_salary)),
                profile.raises,
                start_year,
                end_year,
            )
            benefit = pension_calculator.calculate_benefit(
                benefit_multiplier=pension.benefit_multiplier,
                consecutive_high_years=pension.consecutive_high_years,
                hire_date=pension.hire_date,
                planned_retirement_date=pension.planned_retirement_date,
                salary_by_year=salary_by_year,
            )
            pension_benefit = benefit
            monthly_pension_income += benefit.monthly_benefit

    # Calculate net biweekly pay.
    all_periods = pay_period_service.get_all_periods(user_id)
    current_period = pay_period_service.get_current_period(user_id)
    net_biweekly = Decimal("0")
    if salary_profiles:
        profile = salary_profiles[0]
        if current_period:
            from app.services.tax_config_service import load_tax_configs  # pylint: disable=import-outside-toplevel
            tax_configs = load_tax_configs(user_id, profile)
            breakdown = paycheck_calculator.calculate_paycheck(
                profile, current_period, all_periods, tax_configs,
            )
            net_biweekly = breakdown.net_pay

    # Load retirement/investment accounts and project balances.
    retirement_types = (
        db.session.query(AccountType)
        .filter(AccountType.category.in_(["retirement", "investment"]))
        .all()
    )
    retirement_type_ids = {rt.id for rt in retirement_types}
    type_name_map = {rt.id: rt.name for rt in retirement_types}

    accounts = (
        db.session.query(Account)
        .filter(
            Account.user_id == user_id,
            Account.account_type_id.in_(retirement_type_ids),
            Account.is_active.is_(True),
        )
        .all()
    )

    planned_retirement_date = (
        settings.planned_retirement_date if settings else None
    )

    retirement_account_projections = []
    account_ids = [a.id for a in accounts]

    deductions_by_account = {}
    if account_ids:
        inv_deductions = (
            db.session.query(PaycheckDeduction)
            .join(SalaryProfile)
            .filter(
                SalaryProfile.user_id == user_id,
                SalaryProfile.is_active.is_(True),
                PaycheckDeduction.target_account_id.in_(account_ids),
                PaycheckDeduction.is_active.is_(True),
            )
            .all()
        )
        for ded in inv_deductions:
            deductions_by_account.setdefault(ded.target_account_id, []).append(ded)

    period_ids = [p.id for p in all_periods]
    all_acct_transfers = []
    if account_ids and period_ids:
        all_acct_transfers = (
            db.session.query(Transfer)
            .filter(
                Transfer.to_account_id.in_(account_ids),
                Transfer.pay_period_id.in_(period_ids),
                Transfer.is_deleted.is_(False),
            )
            .all()
        )

    salary_gross_biweekly = Decimal("0")
    if salary_profiles:
        profile = salary_profiles[0]
        salary_gross_biweekly = (
            Decimal(str(profile.annual_salary))
            / (profile.pay_periods_per_year or 26)
        ).quantize(Decimal("0.01"))

    synthetic_periods = []
    if planned_retirement_date:
        synthetic_periods = growth_engine.generate_projection_periods(
            start_date=date.today(),
            end_date=planned_retirement_date,
        )

    for acct in accounts:
        params = (
            db.session.query(InvestmentParams)
            .filter_by(account_id=acct.id)
            .first()
        )
        balance = acct.current_anchor_balance or Decimal("0")
        projected_balance = balance

        if params and synthetic_periods:
            acct_deductions = deductions_by_account.get(acct.id, [])
            adapted_deductions = []
            for ded in acct_deductions:
                ded_profile = ded.salary_profile
                adapted_deductions.append(type("D", (), {
                    "amount": ded.amount,
                    "calc_method_name": ded.calc_method.name if ded.calc_method else "flat",
                    "annual_salary": ded_profile.annual_salary,
                    "pay_periods_per_year": ded_profile.pay_periods_per_year or 26,
                })())

            inputs = calculate_investment_inputs(
                account_id=acct.id,
                investment_params=params,
                deductions=adapted_deductions,
                all_transfers=all_acct_transfers,
                all_periods=all_periods,
                current_period=current_period,
                salary_gross_biweekly=salary_gross_biweekly,
            )

            # Use override return rate if provided, else per-account rate.
            annual_return = (
                return_rate_override
                if return_rate_override is not None
                else params.assumed_annual_return
            )

            proj = growth_engine.project_balance(
                current_balance=balance,
                assumed_annual_return=annual_return,
                periods=synthetic_periods,
                periodic_contribution=inputs.periodic_contribution,
                employer_params=inputs.employer_params,
                annual_contribution_limit=inputs.annual_contribution_limit,
                ytd_contributions_start=inputs.ytd_contributions,
            )
            if proj:
                projected_balance = proj[-1].end_balance

        type_name = type_name_map.get(acct.account_type_id, "")
        retirement_account_projections.append({
            "account": acct,
            "projected_balance": projected_balance,
            "is_traditional": type_name in TRADITIONAL_TYPES,
        })

    # Projected salary for gap comparison.
    gap_net_biweekly = net_biweekly
    if salary_profiles and planned_retirement_date and net_biweekly > 0:
        profile = salary_profiles[0]
        current_gross_biweekly = (
            Decimal(str(profile.annual_salary))
            / (profile.pay_periods_per_year or 26)
        ).quantize(Decimal("0.01"))
        if current_gross_biweekly > 0:
            effective_take_home_rate = net_biweekly / current_gross_biweekly
            if salary_by_year is None:
                salary_by_year = pension_calculator.project_salaries_by_year(
                    Decimal(str(profile.annual_salary)),
                    profile.raises,
                    date.today().year,
                    planned_retirement_date.year,
                )
            if salary_by_year:
                final_salary = salary_by_year[-1][1]
                final_gross_biweekly = (
                    final_salary / (profile.pay_periods_per_year or 26)
                ).quantize(Decimal("0.01"))
                gap_net_biweekly = (
                    final_gross_biweekly * effective_take_home_rate
                ).quantize(Decimal("0.01"))

    # Use override SWR if provided, else from settings.
    swr = (
        swr_override
        if swr_override is not None
        else Decimal(str(settings.safe_withdrawal_rate or "0.04")) if settings else Decimal("0.04")
    )
    tax_rate = (
        Decimal(str(settings.estimated_retirement_tax_rate))
        if settings and settings.estimated_retirement_tax_rate
        else None
    )

    gap_result = retirement_gap_calculator.calculate_gap(
        net_biweekly_pay=gap_net_biweekly,
        monthly_pension_income=monthly_pension_income,
        retirement_account_projections=retirement_account_projections,
        safe_withdrawal_rate=swr,
        planned_retirement_date=planned_retirement_date,
        estimated_tax_rate=tax_rate,
    )

    chart_data = {
        "pension": str(monthly_pension_income),
        "investment_income": str(
            (gap_result.projected_total_savings * swr / 12).quantize(Decimal("0.01"))
        ) if gap_result.projected_total_savings > 0 else "0",
        "gap": str(gap_result.monthly_income_gap),
        "pre_retirement": str(gap_result.pre_retirement_net_monthly),
    }

    return {
        "gap_analysis": gap_result,
        "chart_data": chart_data,
        "pension_benefit": pension_benefit,
        "retirement_account_projections": retirement_account_projections,
        "settings": settings,
        "salary_profiles": salary_profiles,
        "pensions": pensions,
    }


@retirement_bp.route("/retirement")
@login_required
def dashboard():
    """Retirement planning dashboard with gap analysis."""
    data = _compute_gap_data(current_user.id)

    # Compute current slider defaults from settings / account data.
    settings = data["settings"]
    # Presentation boundary: float() for template slider defaults.
    current_swr = float(settings.safe_withdrawal_rate or 0.04) * 100 if settings else 4.0

    # Derive default return rate from weighted average of account return rates.
    projections = data.get("retirement_account_projections", [])
    total_balance = Decimal("0")
    weighted_return = Decimal("0")
    for proj in projections:
        acct = proj["account"]
        params = (
            db.session.query(InvestmentParams)
            .filter_by(account_id=acct.id)
            .first()
        )
        if params and params.assumed_annual_return:
            bal = acct.current_anchor_balance or Decimal("0")
            total_balance += bal
            weighted_return += bal * params.assumed_annual_return
    if total_balance > 0:
        # Presentation boundary: float() for template slider default.
        current_return = float(weighted_return / total_balance) * 100
    else:
        current_return = 7.0

    return render_template(
        "retirement/dashboard.html",
        current_swr=current_swr,
        current_return=current_return,
        **data,
    )


# ── Pension CRUD ─────────────────────────────────────────────────


@retirement_bp.route("/retirement/pension")
@login_required
def pension_list():
    """List pension profiles."""
    pensions = (
        db.session.query(PensionProfile)
        .filter_by(user_id=current_user.id, is_active=True)
        .all()
    )
    salary_profiles = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=current_user.id, is_active=True)
        .all()
    )
    return render_template(
        "retirement/pension_form.html",
        pension=None,
        pensions=pensions,
        salary_profiles=salary_profiles,
    )


@retirement_bp.route("/retirement/pension", methods=["POST"])
@login_required
def create_pension():
    """Create a new pension profile."""
    errors = _pension_create_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("retirement.pension_list"))

    data = _pension_create_schema.load(request.form)

    # Convert percentage input (e.g. 1.85 → 0.0185).
    if data.get("benefit_multiplier"):
        data["benefit_multiplier"] = Decimal(str(data["benefit_multiplier"])) / Decimal("100")

    pension = PensionProfile(user_id=current_user.id, **data)
    db.session.add(pension)
    db.session.commit()

    logger.info("user_id=%d created pension profile %d", current_user.id, pension.id)
    flash(f"Pension profile '{pension.name}' created.", "success")
    return redirect(url_for("retirement.dashboard"))


@retirement_bp.route("/retirement/pension/<int:pension_id>/edit")
@login_required
def edit_pension(pension_id):
    """Display pension profile edit form."""
    pension = db.session.get(PensionProfile, pension_id)
    if pension is None or pension.user_id != current_user.id:
        flash("Pension profile not found.", "danger")
        return redirect(url_for("retirement.dashboard"))

    salary_profiles = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=current_user.id, is_active=True)
        .all()
    )
    return render_template(
        "retirement/pension_form.html",
        pension=pension,
        pensions=[],
        salary_profiles=salary_profiles,
    )


@retirement_bp.route("/retirement/pension/<int:pension_id>", methods=["POST"])
@login_required
def update_pension(pension_id):
    """Update a pension profile."""
    pension = db.session.get(PensionProfile, pension_id)
    if pension is None or pension.user_id != current_user.id:
        flash("Pension profile not found.", "danger")
        return redirect(url_for("retirement.dashboard"))

    errors = _pension_update_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("retirement.edit_pension", pension_id=pension_id))

    data = _pension_update_schema.load(request.form)

    # Convert percentage input.
    if data.get("benefit_multiplier"):
        data["benefit_multiplier"] = Decimal(str(data["benefit_multiplier"])) / Decimal("100")

    _PENSION_FIELDS = {
        "salary_profile_id", "name", "benefit_multiplier",
        "consecutive_high_years", "hire_date",
        "earliest_retirement_date", "planned_retirement_date",
    }
    for field_name, value in data.items():
        if field_name in _PENSION_FIELDS:
            setattr(pension, field_name, value)

    db.session.commit()
    logger.info("user_id=%d updated pension profile %d", current_user.id, pension_id)
    flash(f"Pension profile '{pension.name}' updated.", "success")
    return redirect(url_for("retirement.dashboard"))


@retirement_bp.route("/retirement/pension/<int:pension_id>/delete", methods=["POST"])
@login_required
def delete_pension(pension_id):
    """Deactivate a pension profile."""
    pension = db.session.get(PensionProfile, pension_id)
    if pension is None or pension.user_id != current_user.id:
        flash("Pension profile not found.", "danger")
        return redirect(url_for("retirement.dashboard"))

    pension.is_active = False
    db.session.commit()
    logger.info("user_id=%d deactivated pension profile %d", current_user.id, pension_id)
    flash(f"Pension profile '{pension.name}' deactivated.", "info")
    return redirect(url_for("retirement.dashboard"))


# ── Gap Analysis Fragment ────────────────────────────────────────


@retirement_bp.route("/retirement/gap")
@login_required
def gap_analysis():
    """HTMX fragment: recalculate gap analysis with slider overrides."""
    if not request.headers.get("HX-Request"):
        return redirect(url_for("retirement.dashboard"))

    swr_override = None
    return_rate_override = None

    swr_param = request.args.get("swr", type=float)
    if swr_param is not None:
        swr_override = Decimal(str(swr_param)) / Decimal("100")

    return_param = request.args.get("return_rate", type=float)
    if return_param is not None:
        return_rate_override = Decimal(str(return_param)) / Decimal("100")

    data = _compute_gap_data(
        current_user.id,
        swr_override=swr_override,
        return_rate_override=return_rate_override,
    )

    return render_template(
        "retirement/_gap_analysis.html",
        gap_analysis=data["gap_analysis"],
        chart_data=data["chart_data"],
    )


# ── Retirement Settings ──────────────────────────────────────────


@retirement_bp.route("/retirement/settings", methods=["POST"])
@login_required
def update_settings():
    """Update retirement planning settings."""
    # Convert percentage inputs from form.
    form_data = dict(request.form)
    for field in ("safe_withdrawal_rate", "estimated_retirement_tax_rate"):
        if field in form_data and form_data[field]:
            try:
                form_data[field] = str(Decimal(form_data[field]) / Decimal("100"))
            except Exception:
                pass

    errors = _settings_schema.validate(form_data)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("settings.show", section="retirement"))

    data = _settings_schema.load(form_data)

    settings = (
        db.session.query(UserSettings)
        .filter_by(user_id=current_user.id)
        .first()
    )
    if not settings:
        flash("Settings not found.", "danger")
        return redirect(url_for("settings.show", section="retirement"))

    _SETTINGS_FIELDS = {
        "safe_withdrawal_rate", "planned_retirement_date",
        "estimated_retirement_tax_rate",
    }
    for field_name, value in data.items():
        if field_name in _SETTINGS_FIELDS:
            setattr(settings, field_name, value)

    db.session.commit()
    logger.info("user_id=%d updated retirement settings", current_user.id)
    flash("Retirement settings updated.", "success")
    return redirect(url_for("settings.show", section="retirement"))
