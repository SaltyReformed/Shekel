"""
Shekel Budget App -- Salary Management Routes

CRUD for salary profiles, raises, deductions, tax config,
paycheck breakdown, and salary projection views.
"""

import logging
from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.utils.auth_helpers import fresh_login_required, require_owner
from markupsafe import Markup

from app.extensions import db
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.calibration_override import (
    CalibrationOverride,
    CalibrationDeductionOverride,
)
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
    TaxType,
)
from app import ref_cache
from app.enums import (
    AcctCategoryEnum, CalcMethodEnum, RecurrencePatternEnum,
    TaxTypeEnum, TxnTypeEnum,
)
from app.schemas.validation import (
    CalibrationConfirmSchema,
    CalibrationSchema,
    DeductionCreateSchema,
    DeductionUpdateSchema,
    FicaConfigSchema,
    RaiseCreateSchema,
    RaiseUpdateSchema,
    SalaryProfileCreateSchema,
    SalaryProfileUpdateSchema,
    StateTaxConfigSchema,
)
from app.exceptions import RecurrenceConflict, ValidationError
from app.services import paycheck_calculator, pay_period_service, recurrence_engine
from app.services.calibration_service import derive_effective_rates
from app.services.tax_config_service import load_tax_configs
from app.utils.db_errors import is_unique_violation

logger = logging.getLogger(__name__)

# Names of the composite unique constraints that backstop the
# raise / deduction double-submit fixes (F-051 + F-052 / C-23).
# Each literal mirrors the model declaration in
# ``app/models/salary_raise.py`` and
# ``app/models/paycheck_deduction.py`` and the migration revision
# ``a3b9c2d40e15``; renaming a constraint requires a coordinated
# edit across all three sites.
_SALARY_RAISES_UNIQUE_CONSTRAINT = "uq_salary_raises_profile_type_year_month"
_PAYCHECK_DEDUCTIONS_UNIQUE_CONSTRAINT = "uq_paycheck_deductions_profile_name"

salary_bp = Blueprint("salary", __name__)

_create_schema = SalaryProfileCreateSchema()
_update_schema = SalaryProfileUpdateSchema()
_raise_schema = RaiseCreateSchema()
_raise_update_schema = RaiseUpdateSchema()
_deduction_schema = DeductionCreateSchema()
_deduction_update_schema = DeductionUpdateSchema()
_fica_schema = FicaConfigSchema()
_calibration_schema = CalibrationSchema()
_calibration_confirm_schema = CalibrationConfirmSchema()
_state_tax_schema = StateTaxConfigSchema()



# ── Profile CRUD ───────────────────────────────────────────────────


@salary_bp.route("/salary")
@login_required
@require_owner
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
                profile, current_period, periods, tax_configs,
                calibration=profile.calibration,
            )
            net_pay = breakdown.net_pay
        profile_data.append({"profile": profile, "net_pay": net_pay})

    return render_template("salary/list.html", profile_data=profile_data)


@salary_bp.route("/salary/new")
@login_required
@require_owner
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
@require_owner
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

    # Get income transaction type ID from ref_cache.
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)

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
        rule = RecurrenceRule(
            user_id=current_user.id,
            pattern_id=ref_cache.recurrence_pattern_id(
                RecurrencePatternEnum.EVERY_PERIOD
            ),
        )
        db.session.add(rule)
        db.session.flush()

        # Create linked transaction template
        template = TransactionTemplate(
            user_id=current_user.id,
            account_id=account.id,
            category_id=salary_category.id,
            recurrence_rule_id=rule.id,
            transaction_type_id=income_type_id,
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
@require_owner
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
@require_owner
def update_profile(profile_id):
    """Update a salary profile and recalculate linked transactions.

    Optimistic locking (commit C-18 / F-010): the edit form ships
    ``version_id`` as a hidden input.  When the submitted value
    differs from the row's current counter, the handler short-
    circuits with a flash + redirect so the audit trail records
    only the winner.  ``StaleDataError`` raised at flush time --
    e.g. by a concurrent edit that races past the form-side check
    -- is caught and converted to the same flash + redirect.
    """
    profile = db.session.get(SalaryProfile, profile_id)
    if profile is None or profile.user_id != current_user.id:
        flash("Salary profile not found.", "danger")
        return redirect(url_for("salary.list_profiles"))

    errors = _update_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("salary.edit_profile", profile_id=profile_id))

    data = _update_schema.load(request.form)

    # Stale-form check (commit C-18 / F-010).
    submitted_version = data.pop("version_id", None)
    if submitted_version is not None and submitted_version != profile.version_id:
        logger.info(
            "Stale-form conflict on update_profile id=%d "
            "(submitted=%d, current=%d)",
            profile_id, submitted_version, profile.version_id,
        )
        flash(
            "This salary profile was changed by another action while you "
            "were editing.  Please reload and try again.",
            "warning",
        )
        return redirect(url_for("salary.edit_profile", profile_id=profile_id))

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
    except StaleDataError:
        db.session.rollback()
        logger.info(
            "Stale-data conflict on update_profile id=%d", profile_id,
        )
        flash(
            "This salary profile was changed by another action while you "
            "were editing.  Please reload and try again.",
            "warning",
        )
        return redirect(url_for("salary.edit_profile", profile_id=profile_id))
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
@require_owner
def delete_profile(profile_id):
    """Soft-delete a salary profile and deactivate its template.

    Optimistic locking (commit C-18 / F-010): the
    ``is_active = False`` flush is version-pinned by SQLAlchemy.
    A concurrent edit raises ``StaleDataError`` which the handler
    converts into a flash + redirect.
    """
    profile = db.session.get(SalaryProfile, profile_id)
    if profile is None or profile.user_id != current_user.id:
        flash("Salary profile not found.", "danger")
        return redirect(url_for("salary.list_profiles"))

    profile.is_active = False
    if profile.template:
        profile.template.is_active = False

    try:
        db.session.commit()
    except StaleDataError:
        db.session.rollback()
        logger.info(
            "Stale-data conflict on delete_profile id=%d", profile_id,
        )
        flash(
            "This salary profile was changed by another action.  "
            "Please reload and try again.",
            "warning",
        )
        return redirect(url_for("salary.list_profiles"))
    logger.info("user_id=%d deactivated salary profile %d", current_user.id, profile_id)
    flash(f"Salary profile '{profile.name}' deactivated.", "info")
    return redirect(url_for("salary.list_profiles"))


# ── Raises ─────────────────────────────────────────────────────────


@salary_bp.route("/salary/<int:profile_id>/raises", methods=["POST"])
@login_required
@require_owner
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
    except IntegrityError as exc:
        # Duplicate-raise double-submit (F-051 / C-23): the composite
        # unique ``uq_salary_raises_profile_type_year_month`` rejects
        # the second INSERT when the user clicks Save twice in a row,
        # the browser retries on a flaky network, or the back button
        # is used to re-submit the form.  Roll back and treat as
        # idempotent success: the user lands on the edit page with
        # the raise they intended to create regardless of which
        # request reached the database first, so neither path
        # surfaces the constraint name as a 500.
        db.session.rollback()
        if not is_unique_violation(exc, _SALARY_RAISES_UNIQUE_CONSTRAINT):
            logger.exception(
                "user_id=%d failed to add raise to profile %d "
                "(unexpected IntegrityError)",
                current_user.id, profile_id,
            )
            flash("Failed to add raise. Please try again.", "danger")
            return redirect(url_for("salary.edit_profile", profile_id=profile_id))
        logger.info(
            "Duplicate salary raise prevented on profile %d "
            "(idempotent success)", profile_id,
        )
        flash(
            "A raise with that type and effective date already "
            "exists on this profile.",
            "info",
        )
        if request.headers.get("HX-Request"):
            return _render_raises_partial(profile)
        return redirect(url_for("salary.edit_profile", profile_id=profile_id))
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
@require_owner
def delete_raise(raise_id):
    """Remove a raise from a salary profile.

    Optimistic locking (commit C-18 / F-010): the DELETE statement
    is version-pinned by SQLAlchemy; a concurrent edit raises
    ``StaleDataError`` which the handler converts into a flash +
    redirect.
    """
    salary_raise = db.session.get(SalaryRaise, raise_id)
    if salary_raise is None or salary_raise.salary_profile.user_id != current_user.id:
        flash("Raise not found.", "danger")
        return redirect(url_for("salary.list_profiles"))

    profile = salary_raise.salary_profile

    try:
        db.session.delete(salary_raise)
        _regenerate_salary_transactions(profile)
        db.session.commit()
    except StaleDataError:
        db.session.rollback()
        logger.info(
            "Stale-data conflict on delete_raise id=%d", raise_id,
        )
        flash(
            "This raise was changed by another action.  "
            "Please reload and try again.",
            "warning",
        )
        return redirect(url_for("salary.edit_profile", profile_id=profile.id))
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


@salary_bp.route("/salary/raises/<int:raise_id>/edit", methods=["POST"])
@login_required
@require_owner
def update_raise(raise_id):
    """Update an existing raise on a salary profile.

    Optimistic locking (commit C-18 / F-010): the edit form ships
    ``version_id`` as a hidden input populated by app.js.  A stale
    submission is rejected with a flash + redirect; the
    SQLAlchemy-tier check catches the truly-concurrent case at
    flush time and produces the same response.
    """
    salary_raise = db.session.get(SalaryRaise, raise_id)
    if salary_raise is None or salary_raise.salary_profile.user_id != current_user.id:
        flash("Raise not found.", "danger")
        return redirect(url_for("salary.list_profiles"))

    profile = salary_raise.salary_profile

    errors = _raise_update_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("salary.edit_profile", profile_id=profile.id))

    data = _raise_update_schema.load(request.form)
    data["is_recurring"] = request.form.get("is_recurring") == "on"

    # Stale-form check (commit C-18 / F-010).
    submitted_version = data.pop("version_id", None)
    if submitted_version is not None and submitted_version != salary_raise.version_id:
        logger.info(
            "Stale-form conflict on update_raise id=%d "
            "(submitted=%d, current=%d)",
            raise_id, submitted_version, salary_raise.version_id,
        )
        flash(
            "This raise was changed by another action while you were "
            "editing.  Please reload and try again.",
            "warning",
        )
        return redirect(url_for("salary.edit_profile", profile_id=profile.id))

    # Convert percentage input (e.g. 3 → 0.03) for storage.
    if data.get("percentage") is not None:
        from decimal import Decimal as D
        data["percentage"] = D(str(data["percentage"])) / D("100")

    _RAISE_UPDATE_FIELDS = {
        "raise_type_id", "effective_month", "effective_year",
        "percentage", "flat_amount", "is_recurring", "notes",
    }
    for field_name, value in data.items():
        if field_name in _RAISE_UPDATE_FIELDS:
            setattr(salary_raise, field_name, value)

    try:
        _regenerate_salary_transactions(profile)
        db.session.commit()
    except StaleDataError:
        db.session.rollback()
        logger.info(
            "Stale-data conflict on update_raise id=%d", raise_id,
        )
        flash(
            "This raise was changed by another action while you were "
            "editing.  Please reload and try again.",
            "warning",
        )
        return redirect(url_for("salary.edit_profile", profile_id=profile.id))
    except IntegrityError as exc:
        # Duplicate-key collision on update (F-051 / C-23): the user
        # edited this raise's (type, year, month) tuple to one
        # another active raise on the same salary profile already
        # holds.  Roll back the stale session, surface the
        # field-level error as a flash, and redirect to the edit
        # page so the user can revise their input -- crashing the
        # request with a 500 would expose the constraint name and
        # leave the user without context to recover.
        db.session.rollback()
        if not is_unique_violation(exc, _SALARY_RAISES_UNIQUE_CONSTRAINT):
            logger.exception(
                "user_id=%d failed to update raise %d on profile %d "
                "(unexpected IntegrityError)",
                current_user.id, raise_id, profile.id,
            )
            flash("Failed to update raise. Please try again.", "danger")
            return redirect(url_for("salary.edit_profile", profile_id=profile.id))
        logger.info(
            "Duplicate-key conflict on update_raise id=%d "
            "(another raise already covers this profile/type/date)",
            raise_id,
        )
        flash(
            "Another raise on this profile already covers that "
            "type and effective date.  Edit or remove it before "
            "applying these changes.",
            "warning",
        )
        return redirect(url_for("salary.edit_profile", profile_id=profile.id))
    except Exception:
        db.session.rollback()
        logger.exception(
            "user_id=%d failed to update raise %d on profile %d",
            current_user.id, raise_id, profile.id,
        )
        flash("Failed to update raise. Please try again.", "danger")
        return redirect(url_for("salary.edit_profile", profile_id=profile.id))

    logger.info("user_id=%d updated raise %d on profile %d", current_user.id, raise_id, profile.id)
    flash("Raise updated.", "success")

    if request.headers.get("HX-Request"):
        return _render_raises_partial(profile)
    return redirect(url_for("salary.edit_profile", profile_id=profile.id))


# ── Deductions ─────────────────────────────────────────────────────


@salary_bp.route("/salary/<int:profile_id>/deductions", methods=["POST"])
@login_required
@require_owner
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
    if data["calc_method_id"] == ref_cache.calc_method_id(CalcMethodEnum.PERCENTAGE):
        data["amount"] = D(str(data["amount"])) / D("100")
    if data.get("inflation_rate"):
        data["inflation_rate"] = D(str(data["inflation_rate"])) / D("100")

    deduction = PaycheckDeduction(salary_profile_id=profile.id, **data)
    db.session.add(deduction)

    try:
        _regenerate_salary_transactions(profile)
        db.session.commit()
    except IntegrityError as exc:
        # Duplicate-deduction double-submit (F-052 / C-23): the
        # composite unique ``uq_paycheck_deductions_profile_name``
        # rejects the second INSERT when the user clicks Save
        # twice in a row, the browser retries on a flaky network,
        # or a deactivated deduction with the same name still
        # exists on the profile.  Roll back and treat as
        # idempotent success: the user lands on the edit page with
        # the deduction they intended to create regardless of
        # which request reached the database first.
        db.session.rollback()
        if not is_unique_violation(exc, _PAYCHECK_DEDUCTIONS_UNIQUE_CONSTRAINT):
            logger.exception(
                "user_id=%d failed to add deduction to profile %d "
                "(unexpected IntegrityError)",
                current_user.id, profile_id,
            )
            flash("Failed to add deduction. Please try again.", "danger")
            return redirect(url_for("salary.edit_profile", profile_id=profile_id))
        attempted_name = data.get("name", "")
        logger.info(
            "Duplicate paycheck deduction prevented on profile %d "
            "(name=%r, idempotent success)",
            profile_id, attempted_name,
        )
        flash(
            f"A deduction named '{attempted_name}' already exists "
            f"on this profile.  Edit or reactivate it instead of "
            f"creating a duplicate.",
            "info",
        )
        if request.headers.get("HX-Request"):
            return _render_deductions_partial(profile)
        return redirect(url_for("salary.edit_profile", profile_id=profile_id))
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
@require_owner
def delete_deduction(ded_id):
    """Remove a deduction from a salary profile.

    Optimistic locking (commit C-18 / F-010): the DELETE statement
    is version-pinned by SQLAlchemy; a concurrent edit raises
    ``StaleDataError`` which the handler converts into a flash +
    redirect.
    """
    deduction = db.session.get(PaycheckDeduction, ded_id)
    if deduction is None or deduction.salary_profile.user_id != current_user.id:
        flash("Deduction not found.", "danger")
        return redirect(url_for("salary.list_profiles"))

    profile = deduction.salary_profile

    try:
        db.session.delete(deduction)
        _regenerate_salary_transactions(profile)
        db.session.commit()
    except StaleDataError:
        db.session.rollback()
        logger.info(
            "Stale-data conflict on delete_deduction id=%d", ded_id,
        )
        flash(
            "This deduction was changed by another action.  "
            "Please reload and try again.",
            "warning",
        )
        return redirect(url_for("salary.edit_profile", profile_id=profile.id))
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


@salary_bp.route("/salary/deductions/<int:ded_id>/edit", methods=["POST"])
@login_required
@require_owner
def update_deduction(ded_id):
    """Update an existing deduction on a salary profile.

    Optimistic locking (commit C-18 / F-010): the edit form ships
    ``version_id`` as a hidden input populated by app.js.  A stale
    submission is rejected with a flash + redirect; the
    SQLAlchemy-tier check catches the truly-concurrent case at
    flush time and produces the same response.
    """
    deduction = db.session.get(PaycheckDeduction, ded_id)
    if deduction is None or deduction.salary_profile.user_id != current_user.id:
        flash("Deduction not found.", "danger")
        return redirect(url_for("salary.list_profiles"))

    profile = deduction.salary_profile

    errors = _deduction_update_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("salary.edit_profile", profile_id=profile.id))

    data = _deduction_update_schema.load(request.form)
    data["inflation_enabled"] = request.form.get("inflation_enabled") == "on"

    # Stale-form check (commit C-18 / F-010).
    submitted_version = data.pop("version_id", None)
    if submitted_version is not None and submitted_version != deduction.version_id:
        logger.info(
            "Stale-form conflict on update_deduction id=%d "
            "(submitted=%d, current=%d)",
            ded_id, submitted_version, deduction.version_id,
        )
        flash(
            "This deduction was changed by another action while you "
            "were editing.  Please reload and try again.",
            "warning",
        )
        return redirect(url_for("salary.edit_profile", profile_id=profile.id))

    # Convert percentage inputs (e.g. 6 → 0.06) for storage.
    from decimal import Decimal as D
    if data["calc_method_id"] == ref_cache.calc_method_id(CalcMethodEnum.PERCENTAGE):
        data["amount"] = D(str(data["amount"])) / D("100")
    if data.get("inflation_rate"):
        data["inflation_rate"] = D(str(data["inflation_rate"])) / D("100")

    _DEDUCTION_UPDATE_FIELDS = {
        "name", "deduction_timing_id", "calc_method_id", "amount",
        "deductions_per_year", "annual_cap", "inflation_enabled",
        "inflation_rate", "inflation_effective_month", "target_account_id",
    }
    for field_name, value in data.items():
        if field_name in _DEDUCTION_UPDATE_FIELDS:
            setattr(deduction, field_name, value)

    try:
        _regenerate_salary_transactions(profile)
        db.session.commit()
    except StaleDataError:
        db.session.rollback()
        logger.info(
            "Stale-data conflict on update_deduction id=%d", ded_id,
        )
        flash(
            "This deduction was changed by another action while you "
            "were editing.  Please reload and try again.",
            "warning",
        )
        return redirect(url_for("salary.edit_profile", profile_id=profile.id))
    except IntegrityError as exc:
        # Name-collision rename (F-052 / C-23): the user renamed
        # this deduction to one another active or inactive
        # deduction on the same profile already holds.  Roll back
        # the stale session and surface the field-level error as a
        # flash.  Crashing the request with a 500 would leak the
        # constraint name and leave the user without context to
        # recover.
        db.session.rollback()
        if not is_unique_violation(exc, _PAYCHECK_DEDUCTIONS_UNIQUE_CONSTRAINT):
            logger.exception(
                "user_id=%d failed to update deduction %d on profile %d "
                "(unexpected IntegrityError)",
                current_user.id, ded_id, profile.id,
            )
            flash("Failed to update deduction. Please try again.", "danger")
            return redirect(url_for("salary.edit_profile", profile_id=profile.id))
        logger.info(
            "Duplicate-name conflict on update_deduction id=%d "
            "(another deduction with this name exists on the profile)",
            ded_id,
        )
        flash(
            "Another deduction on this profile already uses that "
            "name.  Choose a different name or remove the existing "
            "deduction first.",
            "warning",
        )
        return redirect(url_for("salary.edit_profile", profile_id=profile.id))
    except Exception:
        db.session.rollback()
        logger.exception(
            "user_id=%d failed to update deduction %d on profile %d",
            current_user.id, ded_id, profile.id,
        )
        flash("Failed to update deduction. Please try again.", "danger")
        return redirect(url_for("salary.edit_profile", profile_id=profile.id))

    logger.info("user_id=%d updated deduction %d on profile %d", current_user.id, ded_id, profile.id)
    flash(f"Deduction '{deduction.name}' updated.", "success")

    if request.headers.get("HX-Request"):
        return _render_deductions_partial(profile)
    return redirect(url_for("salary.edit_profile", profile_id=profile.id))


# ── Views: Breakdown & Projection ──────────────────────────────────


@salary_bp.route("/salary/<int:profile_id>/breakdown/<int:period_id>")
@login_required
@require_owner
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
        profile, period, periods, tax_configs,
        calibration=profile.calibration,
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
@require_owner
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
@require_owner
def projection(profile_id):
    """Show salary projection table for all periods."""
    profile = db.session.get(SalaryProfile, profile_id)
    if profile is None or profile.user_id != current_user.id:
        flash("Salary profile not found.", "danger")
        return redirect(url_for("salary.list_profiles"))

    periods = pay_period_service.get_all_periods(current_user.id)
    tax_configs = load_tax_configs(current_user.id, profile)
    breakdowns = paycheck_calculator.project_salary(
        profile, periods, tax_configs,
        calibration=profile.calibration,
    )

    # Pair periods with breakdowns
    projection_data = list(zip(periods, breakdowns))

    return render_template(
        "salary/projection.html",
        profile=profile,
        projection_data=projection_data,
    )


# ── Calibration ───────────────────────────────────────────────────


@salary_bp.route("/salary/<int:profile_id>/calibrate")
@login_required
@require_owner
def calibrate_form(profile_id):
    """Display the pay stub calibration form."""
    profile = db.session.get(SalaryProfile, profile_id)
    if profile is None or profile.user_id != current_user.id:
        flash("Salary profile not found.", "danger")
        return redirect(url_for("salary.list_profiles"))

    return render_template(
        "salary/calibrate.html",
        profile=profile,
    )


@salary_bp.route("/salary/<int:profile_id>/calibrate", methods=["POST"])
@login_required
@require_owner
def calibrate_preview(profile_id):
    """Validate pay stub data and show derived rates for confirmation."""
    profile = db.session.get(SalaryProfile, profile_id)
    if profile is None or profile.user_id != current_user.id:
        flash("Salary profile not found.", "danger")
        return redirect(url_for("salary.list_profiles"))

    errors = _calibration_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("salary.calibrate_form", profile_id=profile_id))

    data = _calibration_schema.load(request.form)

    # Calculate taxable income from the profile's current pre-tax deductions.
    from decimal import Decimal as D
    gross = D(str(data["actual_gross_pay"]))
    periods = pay_period_service.get_all_periods(current_user.id)
    current_period = pay_period_service.get_current_period(current_user.id)

    if current_period:
        tax_configs = load_tax_configs(current_user.id, profile)
        bk = paycheck_calculator.calculate_paycheck(
            profile, current_period, periods, tax_configs,
        )
        total_pre_tax = bk.total_pre_tax
    else:
        total_pre_tax = D("0")

    taxable = gross - total_pre_tax
    if taxable <= D("0"):
        flash(
            "Taxable income (gross minus pre-tax deductions) is zero or "
            "negative. Cannot derive effective rates.",
            "danger",
        )
        return redirect(url_for("salary.calibrate_form", profile_id=profile_id))

    try:
        rates = derive_effective_rates(
            actual_gross_pay=data["actual_gross_pay"],
            actual_federal_tax=data["actual_federal_tax"],
            actual_state_tax=data["actual_state_tax"],
            actual_social_security=data["actual_social_security"],
            actual_medicare=data["actual_medicare"],
            taxable_income=taxable,
        )
    except ValidationError as e:
        flash(str(e), "danger")
        return redirect(url_for("salary.calibrate_form", profile_id=profile_id))

    return render_template(
        "salary/calibrate_confirm.html",
        profile=profile,
        data=data,
        rates=rates,
        taxable_income=taxable,
        total_pre_tax=total_pre_tax,
    )


@salary_bp.route("/salary/<int:profile_id>/calibrate/confirm", methods=["POST"])
@login_required
@require_owner
def calibrate_confirm(profile_id):
    """Save the calibration override and regenerate transactions."""
    profile = db.session.get(SalaryProfile, profile_id)
    if profile is None or profile.user_id != current_user.id:
        flash("Salary profile not found.", "danger")
        return redirect(url_for("salary.list_profiles"))

    errors = _calibration_confirm_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("salary.calibrate_form", profile_id=profile_id))

    data = _calibration_confirm_schema.load(request.form)

    try:
        # Delete any existing calibration for this profile.
        existing = (
            db.session.query(CalibrationOverride)
            .filter_by(salary_profile_id=profile.id)
            .first()
        )
        if existing:
            db.session.delete(existing)
            db.session.flush()

        cal = CalibrationOverride(
            salary_profile_id=profile.id,
            actual_gross_pay=data["actual_gross_pay"],
            actual_federal_tax=data["actual_federal_tax"],
            actual_state_tax=data["actual_state_tax"],
            actual_social_security=data["actual_social_security"],
            actual_medicare=data["actual_medicare"],
            effective_federal_rate=data["effective_federal_rate"],
            effective_state_rate=data["effective_state_rate"],
            effective_ss_rate=data["effective_ss_rate"],
            effective_medicare_rate=data["effective_medicare_rate"],
            pay_stub_date=data["pay_stub_date"],
            notes=data.get("notes"),
            is_active=True,
        )
        db.session.add(cal)
        db.session.flush()

        # Refresh the relationship so the calculator sees the new calibration.
        db.session.refresh(profile)

        _regenerate_salary_transactions(profile)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception(
            "user_id=%d failed to save calibration for profile %d",
            current_user.id, profile_id,
        )
        flash("Failed to save calibration. Please try again.", "danger")
        return redirect(url_for("salary.calibrate_form", profile_id=profile_id))

    logger.info("user_id=%d calibrated profile %d", current_user.id, profile_id)
    flash("Paycheck calibration saved. Projections updated.", "success")
    return redirect(url_for("salary.edit_profile", profile_id=profile_id))


@salary_bp.route("/salary/<int:profile_id>/calibrate/delete", methods=["POST"])
@login_required
@require_owner
def calibrate_delete(profile_id):
    """Remove calibration override and revert to bracket-based taxes."""
    profile = db.session.get(SalaryProfile, profile_id)
    if profile is None or profile.user_id != current_user.id:
        flash("Salary profile not found.", "danger")
        return redirect(url_for("salary.list_profiles"))

    existing = (
        db.session.query(CalibrationOverride)
        .filter_by(salary_profile_id=profile.id)
        .first()
    )
    if existing:
        db.session.delete(existing)
        db.session.flush()

        # Refresh so the calculator no longer sees the calibration.
        db.session.refresh(profile)

        try:
            _regenerate_salary_transactions(profile)
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception(
                "user_id=%d failed to remove calibration for profile %d",
                current_user.id, profile_id,
            )
            flash("Failed to remove calibration. Please try again.", "danger")
            return redirect(url_for("salary.edit_profile", profile_id=profile_id))

        logger.info("user_id=%d removed calibration from profile %d", current_user.id, profile_id)
        flash("Calibration removed. Reverted to bracket-based taxes.", "info")
    else:
        flash("No calibration to remove.", "info")

    return redirect(url_for("salary.edit_profile", profile_id=profile_id))


# ── Tax Config ─────────────────────────────────────────────────────


@salary_bp.route("/salary/tax-config")
@login_required
@require_owner
def tax_config():
    """Redirect to settings dashboard tax configuration section."""
    return redirect(url_for("settings.show", section="tax"))


@salary_bp.route("/salary/tax-config", methods=["POST"])
@login_required
@require_owner
@fresh_login_required()
def update_tax_config():
    """Update state tax flat rate."""
    errors = _state_tax_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("settings.show", section="tax"))

    data = _state_tax_schema.load(request.form)

    state_code = data["state_code"].upper()
    tax_year = data["tax_year"]

    # Convert percentage input (e.g. 3.99 → 0.0399) for storage.
    from decimal import Decimal as D
    flat_rate = None
    if data.get("flat_rate") is not None:
        flat_rate = D(str(data["flat_rate"])) / D("100")

    standard_deduction = data.get("standard_deduction")

    state_config = (
        db.session.query(StateTaxConfig)
        .filter_by(user_id=current_user.id, state_code=state_code, tax_year=tax_year)
        .first()
    )

    if state_config:
        if flat_rate is not None:
            state_config.flat_rate = flat_rate
        state_config.standard_deduction = standard_deduction
        flash(f"State tax config for {state_code} {tax_year} updated.", "success")
    else:
        flat_type_id = ref_cache.tax_type_id(TaxTypeEnum.FLAT)
        if flat_rate is not None:
            new_config = StateTaxConfig(
                user_id=current_user.id,
                tax_type_id=flat_type_id,
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
@require_owner
@fresh_login_required()
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
            profile, current_period, periods, tax_configs,
            calibration=profile.calibration,
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
    from app.models.ref import AccountType as AT  # pylint: disable=import-outside-toplevel
    retirement_cat_id = ref_cache.acct_category_id(AcctCategoryEnum.RETIREMENT)
    investment_cat_id = ref_cache.acct_category_id(AcctCategoryEnum.INVESTMENT)
    retirement_types = (
        db.session.query(AT)
        .filter(AT.category_id.in_([retirement_cat_id, investment_cat_id]))
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
