"""
Shekel Budget App -- Salary Management Routes

CRUD for salary profiles, raises, deductions, tax config,
paycheck breakdown, and salary projection views.
"""

import logging
from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from markupsafe import Markup

from app.extensions import db
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.tax_config import (
    FicaConfig,
    StateTaxConfig,
)
from app.models.transaction_template import TransactionTemplate
from app.models.recurrence_rule import RecurrenceRule
from app.models.pay_period import PayPeriod
from app.models.category import Category
from app.models.account import Account
from app.models.scenario import Scenario
from app.models.ref import (
    CalcMethod,
    DeductionTiming,
    FilingStatus,
    RaiseType,
    RecurrencePattern,
    TaxType,
    TransactionType,
)
from app.schemas.validation import (
    DeductionCreateSchema,
    FicaConfigSchema,
    RaiseCreateSchema,
    SalaryProfileCreateSchema,
    SalaryProfileUpdateSchema,
)
from app.exceptions import RecurrenceConflict
from app.services import paycheck_calculator, pay_period_service, recurrence_engine
from app.services.tax_config_service import load_tax_configs

logger = logging.getLogger(__name__)

salary_bp = Blueprint("salary", __name__)

_create_schema = SalaryProfileCreateSchema()
_update_schema = SalaryProfileUpdateSchema()
_raise_schema = RaiseCreateSchema()
_deduction_schema = DeductionCreateSchema()
_fica_schema = FicaConfigSchema()



# ── Profile CRUD ───────────────────────────────────────────────────


@salary_bp.route("/salary")
@login_required
def list_profiles():
    """List all salary profiles."""
    profiles = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=current_user.id)
        .order_by(SalaryProfile.sort_order, SalaryProfile.name)
        .all()
    )

    # Calculate estimated net pay for each profile
    periods = pay_period_service.get_all_periods(current_user.id)
    current_period = pay_period_service.get_current_period(current_user.id)
    profile_data = []
    for profile in profiles:
        net_pay = None
        if current_period and profile.is_active:
            tax_configs = load_tax_configs(current_user.id, profile)
            breakdown = paycheck_calculator.calculate_paycheck(
                profile, current_period, periods, tax_configs
            )
            net_pay = breakdown.net_pay
        profile_data.append({"profile": profile, "net_pay": net_pay})

    return render_template("salary/list.html", profile_data=profile_data)


@salary_bp.route("/salary/new")
@login_required
def new_profile():
    """Display the salary profile creation form."""
    filing_statuses = db.session.query(FilingStatus).all()
    return render_template(
        "salary/form.html",
        profile=None,
        filing_statuses=filing_statuses,
        raise_types=[],
        deduction_timings=[],
        calc_methods=[],
        now_year=date.today().year,
    )


@salary_bp.route("/salary", methods=["POST"])
@login_required
def create_profile():
    """Create a new salary profile with auto-linked template."""
    errors = _create_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("salary.new_profile"))

    data = _create_schema.load(request.form)

    # Get baseline scenario
    scenario = (
        db.session.query(Scenario)
        .filter_by(user_id=current_user.id, is_baseline=True)
        .first()
    )
    if not scenario:
        flash(Markup(
            "No baseline scenario found. Please "
            '<a href="/register" class="alert-link">register a new account</a> '
            "to set up your budget."
        ), "danger")
        return redirect(url_for("salary.list_profiles"))

    # Find or create Income: Salary category
    salary_category = (
        db.session.query(Category)
        .filter_by(user_id=current_user.id, group_name="Income", item_name="Salary")
        .first()
    )
    if not salary_category:
        salary_category = Category(
            user_id=current_user.id,
            group_name="Income",
            item_name="Salary",
            sort_order=0,
        )
        db.session.add(salary_category)
        db.session.flush()

    # Get income transaction type
    income_type = db.session.query(TransactionType).filter_by(name="income").one()

    # Get default account
    account = (
        db.session.query(Account)
        .filter_by(user_id=current_user.id, is_active=True)
        .first()
    )
    if not account:
        flash(Markup(
            'You need an active account before creating a salary profile. '
            '<a href="' + url_for("accounts.new_account") + '" class="alert-link">'
            'Create an account</a>.'
        ), "danger")
        return redirect(url_for("salary.list_profiles"))

    try:
        # Create every_period recurrence rule
        every_period_pattern = (
            db.session.query(RecurrencePattern)
            .filter_by(name="every_period")
            .one()
        )
        rule = RecurrenceRule(
            user_id=current_user.id,
            pattern_id=every_period_pattern.id,
        )
        db.session.add(rule)
        db.session.flush()

        # Create linked transaction template
        template = TransactionTemplate(
            user_id=current_user.id,
            account_id=account.id,
            category_id=salary_category.id,
            recurrence_rule_id=rule.id,
            transaction_type_id=income_type.id,
            name=data["name"],
            default_amount=data["annual_salary"] / (data.get("pay_periods_per_year", 26)),
            is_active=True,
        )
        db.session.add(template)
        db.session.flush()

        # Create the salary profile
        profile = SalaryProfile(
            user_id=current_user.id,
            scenario_id=scenario.id,
            template_id=template.id,
            filing_status_id=data["filing_status_id"],
            name=data["name"],
            annual_salary=data["annual_salary"],
            state_code=data["state_code"],
            pay_periods_per_year=data.get("pay_periods_per_year", 26),
            qualifying_children=data.get("qualifying_children", 0),
            other_dependents=data.get("other_dependents", 0),
            additional_income=data.get("additional_income", 0),
            additional_deductions=data.get("additional_deductions", 0),
            extra_withholding=data.get("extra_withholding", 0),
        )
        db.session.add(profile)
        db.session.flush()

        # Generate income transactions via recurrence engine
        periods = pay_period_service.get_all_periods(current_user.id)
        recurrence_engine.generate_for_template(template, periods, scenario.id)

        # Update the template's default_amount from gross to net so that
        # any future fallback (e.g. missing tax configs for a period)
        # uses the net amount rather than the gross.
        ref_period = (
            pay_period_service.get_current_period(current_user.id)
            or (periods[0] if periods else None)
        )
        if ref_period:
            tax_configs = load_tax_configs(current_user.id, profile)
            init_breakdown = paycheck_calculator.calculate_paycheck(
                profile, ref_period, periods, tax_configs
            )
            template.default_amount = init_breakdown.net_pay

        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("user_id=%d failed to create salary profile", current_user.id)
        flash("Failed to create salary profile. Please try again.", "danger")
        return redirect(url_for("salary.new_profile"))

    logger.info("user_id=%d created salary profile %d", current_user.id, profile.id)
    flash(f"Salary profile '{profile.name}' created.", "success")
    return redirect(url_for("salary.edit_profile", profile_id=profile.id))


@salary_bp.route("/salary/<int:profile_id>/edit")
@login_required
def edit_profile(profile_id):
    """Display the salary profile edit form with raises and deductions."""
    profile = db.session.get(SalaryProfile, profile_id)
    if profile is None or profile.user_id != current_user.id:
        flash("Salary profile not found.", "danger")
        return redirect(url_for("salary.list_profiles"))

    filing_statuses = db.session.query(FilingStatus).all()
    raise_types = db.session.query(RaiseType).all()
    deduction_timings = db.session.query(DeductionTiming).all()
    calc_methods = db.session.query(CalcMethod).all()
    investment_accounts = _get_investment_accounts(current_user.id)

    return render_template(
        "salary/form.html",
        profile=profile,
        filing_statuses=filing_statuses,
        raise_types=raise_types,
        deduction_timings=deduction_timings,
        calc_methods=calc_methods,
        investment_accounts=investment_accounts,
        now_year=date.today().year,
    )


@salary_bp.route("/salary/<int:profile_id>", methods=["POST"])
@login_required
def update_profile(profile_id):
    """Update a salary profile and recalculate linked transactions."""
    profile = db.session.get(SalaryProfile, profile_id)
    if profile is None or profile.user_id != current_user.id:
        flash("Salary profile not found.", "danger")
        return redirect(url_for("salary.list_profiles"))

    errors = _update_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("salary.edit_profile", profile_id=profile_id))

    data = _update_schema.load(request.form)

    try:
        _PROFILE_UPDATE_FIELDS = {
            "name", "annual_salary", "filing_status_id", "state_code",
            "pay_periods_per_year", "qualifying_children", "other_dependents",
            "additional_income", "additional_deductions", "extra_withholding",
        }
        for field_name, value in data.items():
            if field_name in _PROFILE_UPDATE_FIELDS:
                setattr(profile, field_name, value)

        # Update linked template amount
        if profile.template and "annual_salary" in data:
            pay_periods = profile.pay_periods_per_year or 26
            profile.template.default_amount = data["annual_salary"] / pay_periods
            if "name" in data:
                profile.template.name = data["name"]

        # Regenerate transactions with new amount
        _regenerate_salary_transactions(profile)

        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("user_id=%d failed to update salary profile %d", current_user.id, profile_id)
        flash("Failed to update salary profile. Please try again.", "danger")
        return redirect(url_for("salary.edit_profile", profile_id=profile_id))

    logger.info("user_id=%d updated salary profile %d", current_user.id, profile_id)
    flash(f"Salary profile '{profile.name}' updated.", "success")
    return redirect(url_for("salary.edit_profile", profile_id=profile_id))


@salary_bp.route("/salary/<int:profile_id>/delete", methods=["POST"])
@login_required
def delete_profile(profile_id):
    """Soft-delete a salary profile and deactivate its template."""
    profile = db.session.get(SalaryProfile, profile_id)
    if profile is None or profile.user_id != current_user.id:
        flash("Salary profile not found.", "danger")
        return redirect(url_for("salary.list_profiles"))

    profile.is_active = False
    if profile.template:
        profile.template.is_active = False

    db.session.commit()
    logger.info("user_id=%d deactivated salary profile %d", current_user.id, profile_id)
    flash(f"Salary profile '{profile.name}' deactivated.", "info")
    return redirect(url_for("salary.list_profiles"))


# ── Raises ─────────────────────────────────────────────────────────


@salary_bp.route("/salary/<int:profile_id>/raises", methods=["POST"])
@login_required
def add_raise(profile_id):
    """Add a raise to a salary profile."""
    profile = db.session.get(SalaryProfile, profile_id)
    if profile is None or profile.user_id != current_user.id:
        flash("Salary profile not found.", "danger")
        return redirect(url_for("salary.list_profiles"))

    errors = _raise_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("salary.edit_profile", profile_id=profile_id))

    data = _raise_schema.load(request.form)
    # Handle checkbox -- form sends "on" or nothing
    data["is_recurring"] = request.form.get("is_recurring") == "on"

    # Convert percentage input (e.g. 3 → 0.03) for storage.
    if data.get("percentage") is not None:
        from decimal import Decimal as D
        data["percentage"] = D(str(data["percentage"])) / D("100")

    salary_raise = SalaryRaise(salary_profile_id=profile.id, **data)
    db.session.add(salary_raise)

    try:
        _regenerate_salary_transactions(profile)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("user_id=%d failed to add raise to profile %d", current_user.id, profile_id)
        flash("Failed to add raise. Please try again.", "danger")
        return redirect(url_for("salary.edit_profile", profile_id=profile_id))

    logger.info("user_id=%d added raise to profile %d", current_user.id, profile_id)
    flash("Raise added.", "success")

    if request.headers.get("HX-Request"):
        return _render_raises_partial(profile)
    return redirect(url_for("salary.edit_profile", profile_id=profile_id))


@salary_bp.route("/salary/raises/<int:raise_id>/delete", methods=["POST"])
@login_required
def delete_raise(raise_id):
    """Remove a raise from a salary profile."""
    salary_raise = db.session.get(SalaryRaise, raise_id)
    if salary_raise is None:
        flash("Raise not found.", "danger")
        return redirect(url_for("salary.list_profiles"))

    profile = salary_raise.salary_profile
    if profile.user_id != current_user.id:
        flash("Not authorized.", "danger")
        return redirect(url_for("salary.list_profiles"))

    try:
        db.session.delete(salary_raise)
        _regenerate_salary_transactions(profile)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("user_id=%d failed to delete raise %d from profile %d", current_user.id, raise_id, profile.id)
        flash("Failed to remove raise. Please try again.", "danger")
        return redirect(url_for("salary.edit_profile", profile_id=profile.id))

    logger.info("user_id=%d deleted raise %d from profile %d", current_user.id, raise_id, profile.id)
    flash("Raise removed.", "info")

    if request.headers.get("HX-Request"):
        return _render_raises_partial(profile)
    return redirect(url_for("salary.edit_profile", profile_id=profile.id))


# ── Deductions ─────────────────────────────────────────────────────


@salary_bp.route("/salary/<int:profile_id>/deductions", methods=["POST"])
@login_required
def add_deduction(profile_id):
    """Add a deduction to a salary profile."""
    profile = db.session.get(SalaryProfile, profile_id)
    if profile is None or profile.user_id != current_user.id:
        flash("Salary profile not found.", "danger")
        return redirect(url_for("salary.list_profiles"))

    errors = _deduction_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("salary.edit_profile", profile_id=profile_id))

    data = _deduction_schema.load(request.form)
    data["inflation_enabled"] = request.form.get("inflation_enabled") == "on"

    # Convert percentage inputs (e.g. 6 → 0.06) for storage.
    from decimal import Decimal as D
    calc_method = db.session.get(CalcMethod, data["calc_method_id"])
    if calc_method and calc_method.name == "percentage":
        data["amount"] = D(str(data["amount"])) / D("100")
    if data.get("inflation_rate"):
        data["inflation_rate"] = D(str(data["inflation_rate"])) / D("100")

    deduction = PaycheckDeduction(salary_profile_id=profile.id, **data)
    db.session.add(deduction)

    try:
        _regenerate_salary_transactions(profile)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("user_id=%d failed to add deduction to profile %d", current_user.id, profile_id)
        flash("Failed to add deduction. Please try again.", "danger")
        return redirect(url_for("salary.edit_profile", profile_id=profile_id))

    logger.info("user_id=%d added deduction to profile %d", current_user.id, profile_id)
    flash(f"Deduction '{deduction.name}' added.", "success")

    if request.headers.get("HX-Request"):
        return _render_deductions_partial(profile)
    return redirect(url_for("salary.edit_profile", profile_id=profile_id))


@salary_bp.route("/salary/deductions/<int:ded_id>/delete", methods=["POST"])
@login_required
def delete_deduction(ded_id):
    """Remove a deduction from a salary profile."""
    deduction = db.session.get(PaycheckDeduction, ded_id)
    if deduction is None:
        flash("Deduction not found.", "danger")
        return redirect(url_for("salary.list_profiles"))

    profile = deduction.salary_profile
    if profile.user_id != current_user.id:
        flash("Not authorized.", "danger")
        return redirect(url_for("salary.list_profiles"))

    try:
        db.session.delete(deduction)
        _regenerate_salary_transactions(profile)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("user_id=%d failed to delete deduction %d from profile %d", current_user.id, ded_id, profile.id)
        flash("Failed to remove deduction. Please try again.", "danger")
        return redirect(url_for("salary.edit_profile", profile_id=profile.id))

    logger.info("user_id=%d deleted deduction %d from profile %d", current_user.id, ded_id, profile.id)
    flash("Deduction removed.", "info")

    if request.headers.get("HX-Request"):
        return _render_deductions_partial(profile)
    return redirect(url_for("salary.edit_profile", profile_id=profile.id))


# ── Views: Breakdown & Projection ──────────────────────────────────


@salary_bp.route("/salary/<int:profile_id>/breakdown/<int:period_id>")
@login_required
def breakdown(profile_id, period_id):
    """Show paycheck breakdown for a specific period."""
    profile = db.session.get(SalaryProfile, profile_id)
    if profile is None or profile.user_id != current_user.id:
        flash("Salary profile not found.", "danger")
        return redirect(url_for("salary.list_profiles"))

    period = db.session.get(PayPeriod, period_id)
    if period is None or period.user_id != current_user.id:
        flash("Pay period not found.", "danger")
        return redirect(url_for("salary.list_profiles"))

    periods = pay_period_service.get_all_periods(current_user.id)
    tax_configs = load_tax_configs(current_user.id, profile)
    result = paycheck_calculator.calculate_paycheck(
        profile, period, periods, tax_configs
    )

    return render_template(
        "salary/breakdown.html",
        profile=profile,
        period=period,
        breakdown=result,
        periods=periods,
    )


@salary_bp.route("/salary/<int:profile_id>/breakdown")
@login_required
def breakdown_current(profile_id):
    """Show paycheck breakdown for the current period."""
    current_period = pay_period_service.get_current_period(current_user.id)
    if not current_period:
        flash(Markup(
            'No pay periods found. '
            '<a href="' + url_for("pay_periods.generate_form") + '" class="alert-link">'
            'Generate pay periods</a> first.'
        ), "warning")
        return redirect(url_for("salary.list_profiles"))
    return redirect(url_for(
        "salary.breakdown",
        profile_id=profile_id,
        period_id=current_period.id,
    ))


@salary_bp.route("/salary/<int:profile_id>/projection")
@login_required
def projection(profile_id):
    """Show salary projection table for all periods."""
    profile = db.session.get(SalaryProfile, profile_id)
    if profile is None or profile.user_id != current_user.id:
        flash("Salary profile not found.", "danger")
        return redirect(url_for("salary.list_profiles"))

    periods = pay_period_service.get_all_periods(current_user.id)
    tax_configs = load_tax_configs(current_user.id, profile)
    breakdowns = paycheck_calculator.project_salary(profile, periods, tax_configs)

    # Pair periods with breakdowns
    projection_data = list(zip(periods, breakdowns))

    return render_template(
        "salary/projection.html",
        profile=profile,
        projection_data=projection_data,
    )


# ── Tax Config ─────────────────────────────────────────────────────


@salary_bp.route("/salary/tax-config")
@login_required
def tax_config():
    """Redirect to settings dashboard tax configuration section."""
    return redirect(url_for("settings.show", section="tax"))


@salary_bp.route("/salary/tax-config", methods=["POST"])
@login_required
def update_tax_config():
    """Update state tax flat rate."""
    state_code = request.form.get("state_code", "").strip().upper()
    flat_rate_raw = request.form.get("flat_rate", "").strip()
    standard_deduction = request.form.get("standard_deduction", "").strip() or None
    tax_year = request.form.get("tax_year", "").strip()

    # Convert percentage input (e.g. 3.99 → 0.0399) for storage.
    from decimal import Decimal as D, InvalidOperation
    flat_rate = None
    if flat_rate_raw:
        try:
            flat_rate = D(flat_rate_raw) / D("100")
        except (InvalidOperation, ValueError):
            flash("Invalid flat rate.", "danger")
            return redirect(url_for("settings.show", section="tax"))

    if not state_code or len(state_code) != 2:
        flash("Invalid state code.", "danger")
        return redirect(url_for("settings.show", section="tax"))

    if not tax_year or not tax_year.isdigit():
        tax_year = date.today().year
    else:
        tax_year = int(tax_year)

    state_config = (
        db.session.query(StateTaxConfig)
        .filter_by(user_id=current_user.id, state_code=state_code, tax_year=tax_year)
        .first()
    )

    if state_config:
        if flat_rate:
            state_config.flat_rate = flat_rate
        state_config.standard_deduction = standard_deduction
        flash(f"State tax config for {state_code} {tax_year} updated.", "success")
    else:
        flat_type = db.session.query(TaxType).filter_by(name="flat").first()
        if flat_type and flat_rate:
            new_config = StateTaxConfig(
                user_id=current_user.id,
                tax_type_id=flat_type.id,
                state_code=state_code,
                tax_year=tax_year,
                flat_rate=flat_rate,
                standard_deduction=standard_deduction,
            )
            db.session.add(new_config)
            flash(f"State tax config for {state_code} {tax_year} created.", "success")

    db.session.commit()

    # Regenerate salary transactions so the grid reflects the new rates.
    _regenerate_all_salary_transactions()
    db.session.commit()

    logger.info("user_id=%d updated state tax config for %s", current_user.id, state_code)
    return redirect(url_for("settings.show", section="tax"))


@salary_bp.route("/salary/fica-config", methods=["POST"])
@login_required
def update_fica_config():
    """Update FICA configuration."""
    errors = _fica_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("settings.show", section="tax"))

    data = _fica_schema.load(request.form)
    tax_year = data.pop("tax_year")

    # Convert percentage inputs (e.g. 6.2 → 0.062) for storage.
    from decimal import Decimal as D
    for rate_field in ("ss_rate", "medicare_rate", "medicare_surtax_rate"):
        if rate_field in data and data[rate_field] is not None:
            data[rate_field] = D(str(data[rate_field])) / D("100")

    fica = (
        db.session.query(FicaConfig)
        .filter_by(user_id=current_user.id, tax_year=tax_year)
        .first()
    )

    if fica:
        for field_name, value in data.items():
            setattr(fica, field_name, value)
        flash(f"FICA config for {tax_year} updated.", "success")
    else:
        fica = FicaConfig(user_id=current_user.id, tax_year=tax_year, **data)
        db.session.add(fica)
        flash(f"FICA config for {tax_year} created.", "success")

    db.session.commit()

    # Regenerate salary transactions so the grid reflects the new rates.
    _regenerate_all_salary_transactions()
    db.session.commit()

    logger.info("user_id=%d updated FICA config for %d", current_user.id, tax_year)
    return redirect(url_for("settings.show", section="tax"))


# ── Private Helpers ────────────────────────────────────────────────


def _regenerate_salary_transactions(profile):
    """Recalculate and update linked template transactions."""
    if not profile.template:
        return

    scenario = (
        db.session.query(Scenario)
        .filter_by(user_id=current_user.id, is_baseline=True)
        .first()
    )
    if not scenario:
        return

    periods = pay_period_service.get_all_periods(current_user.id)
    tax_configs = load_tax_configs(current_user.id, profile)

    # Update the template's default_amount to the current net pay
    current_period = pay_period_service.get_current_period(current_user.id)
    if current_period:
        breakdown = paycheck_calculator.calculate_paycheck(
            profile, current_period, periods, tax_configs
        )
        profile.template.default_amount = breakdown.net_pay

    # Regenerate transactions
    try:
        recurrence_engine.regenerate_for_template(
            profile.template, periods, scenario.id,
            effective_from=date.today(),
        )
    except RecurrenceConflict as e:
        logger.warning("Recurrence conflict during salary regeneration: %s", e)
    except Exception:
        logger.exception("Failed to regenerate salary transactions for profile %d", profile.id)
        raise


def _regenerate_all_salary_transactions():
    """Regenerate salary transactions for every active profile.

    Called after tax or FICA configuration changes so that projected
    paycheck amounts in the grid stay in sync with the salary profile
    page.  Without this, updating a tax rate would change the salary
    page's displayed net pay but leave stale amounts in the grid.
    """
    profiles = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=current_user.id, is_active=True)
        .all()
    )
    for profile in profiles:
        _regenerate_salary_transactions(profile)


def _render_raises_partial(profile):
    """Return the raises table partial for HTMX updates."""
    # Refresh relationships
    db.session.refresh(profile)
    raise_types = db.session.query(RaiseType).all()
    return render_template(
        "salary/_raises_section.html",
        profile=profile,
        raise_types=raise_types,
        now_year=date.today().year,
    )


def _render_deductions_partial(profile):
    """Return the deductions table partial for HTMX updates."""
    db.session.refresh(profile)
    deduction_timings = db.session.query(DeductionTiming).all()
    calc_methods = db.session.query(CalcMethod).all()
    investment_accounts = _get_investment_accounts(profile.user_id)
    return render_template(
        "salary/_deductions_section.html",
        profile=profile,
        deduction_timings=deduction_timings,
        calc_methods=calc_methods,
        investment_accounts=investment_accounts,
    )


def _get_investment_accounts(user_id):
    """Load retirement/investment accounts for the target account dropdown."""
    from app.models.ref import AccountType as AT
    retirement_types = (
        db.session.query(AT)
        .filter(AT.category.in_(["retirement", "investment"]))
        .all()
    )
    type_ids = {rt.id for rt in retirement_types}
    if not type_ids:
        return []
    return (
        db.session.query(Account)
        .filter(
            Account.user_id == user_id,
            Account.account_type_id.in_(type_ids),
            Account.is_active.is_(True),
        )
        .order_by(Account.name)
        .all()
    )
