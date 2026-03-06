"""
Shekel Budget App — Retirement Planning Routes

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


@retirement_bp.route("/retirement")
@login_required
def dashboard():
    """Retirement planning dashboard with gap analysis."""
    user_id = current_user.id
    settings = (
        db.session.query(UserSettings).filter_by(user_id=user_id).first()
    )

    # Load pension profiles.
    pensions = (
        db.session.query(PensionProfile)
        .filter_by(user_id=user_id, is_active=True)
        .all()
    )

    # Load salary profiles for pension calculation.
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

    # Load all periods and find current period.
    all_periods = pay_period_service.get_all_periods(user_id)
    current_period = pay_period_service.get_current_period(user_id)

    # Calculate current net biweekly pay.
    net_biweekly = Decimal("0")
    if salary_profiles:
        profile = salary_profiles[0]
        if current_period:
            from app.routes.salary import _load_tax_configs
            tax_configs = _load_tax_configs(user_id, profile)
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

    retirement_account_projections = []
    planned_retirement_date = (
        settings.planned_retirement_date if settings else None
    )

    # Batch-load paycheck deductions targeting retirement accounts.
    deductions_by_account = {}
    account_ids = [a.id for a in accounts]
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

    # Batch-load transfers targeting retirement accounts.
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

    # Load salary gross biweekly for employer contribution calculation.
    salary_gross_biweekly = Decimal("0")
    if salary_profiles:
        profile = salary_profiles[0]
        salary_gross_biweekly = (
            Decimal(str(profile.annual_salary))
            / (profile.pay_periods_per_year or 26)
        ).quantize(Decimal("0.01"))

    # Generate synthetic future periods from today to retirement.
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
            # Adapt deductions for this account.
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

            # Compute contribution inputs using the shared helper.
            inputs = calculate_investment_inputs(
                account_id=acct.id,
                investment_params=params,
                deductions=adapted_deductions,
                all_transfers=all_acct_transfers,
                all_periods=all_periods,
                current_period=current_period,
                salary_gross_biweekly=salary_gross_biweekly,
            )

            # Project balance forward using synthetic periods to retirement.
            proj = growth_engine.project_balance(
                current_balance=balance,
                assumed_annual_return=params.assumed_annual_return,
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

    # Project salary to retirement for gap analysis comparison.
    # Uses effective take-home rate from current paycheck applied to
    # projected final salary, giving a better comparison than current income.
    # Reuses salary_by_year from the pension loop when available.
    gap_net_biweekly = net_biweekly
    if salary_profiles and planned_retirement_date and net_biweekly > 0:
        profile = salary_profiles[0]
        current_gross_biweekly = (
            Decimal(str(profile.annual_salary))
            / (profile.pay_periods_per_year or 26)
        ).quantize(Decimal("0.01"))
        if current_gross_biweekly > 0:
            effective_take_home_rate = net_biweekly / current_gross_biweekly
            # Compute salary projection only if pension loop didn't already.
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

    # Calculate gap analysis.
    swr = Decimal(str(settings.safe_withdrawal_rate or "0.04")) if settings else Decimal("0.04")
    tax_rate = (
        Decimal(str(settings.estimated_retirement_tax_rate))
        if settings and settings.estimated_retirement_tax_rate
        else None
    )

    gap_analysis = retirement_gap_calculator.calculate_gap(
        net_biweekly_pay=gap_net_biweekly,
        monthly_pension_income=monthly_pension_income,
        retirement_account_projections=retirement_account_projections,
        safe_withdrawal_rate=swr,
        planned_retirement_date=planned_retirement_date,
        estimated_tax_rate=tax_rate,
    )

    # Chart data for gap visualization.
    chart_data = {
        "pension": str(monthly_pension_income),
        "investment_income": str(
            (gap_analysis.projected_total_savings * swr / 12).quantize(Decimal("0.01"))
        ) if gap_analysis.projected_total_savings > 0 else "0",
        "gap": str(gap_analysis.monthly_income_gap),
        "pre_retirement": str(gap_analysis.pre_retirement_net_monthly),
    }

    return render_template(
        "retirement/dashboard.html",
        settings=settings,
        pensions=pensions,
        pension_benefit=pension_benefit,
        salary_profiles=salary_profiles,
        gap_analysis=gap_analysis,
        retirement_account_projections=retirement_account_projections,
        chart_data=chart_data,
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
        flash(f"Validation error: {errors}", "danger")
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
        flash(f"Validation error: {errors}", "danger")
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
    """HTMX fragment: recalculate and return gap analysis results."""
    # Reuse the dashboard logic but return only the fragment.
    return redirect(url_for("retirement.dashboard"))


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
        flash(f"Validation error: {errors}", "danger")
        return redirect(url_for("retirement.dashboard"))

    data = _settings_schema.load(form_data)

    settings = (
        db.session.query(UserSettings)
        .filter_by(user_id=current_user.id)
        .first()
    )
    if not settings:
        flash("Settings not found.", "danger")
        return redirect(url_for("retirement.dashboard"))

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
    return redirect(url_for("retirement.dashboard"))
