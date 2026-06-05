"""
Shekel Budget App -- Salary route package: profile CRUD.

Create, list, edit, update, and soft-delete salary profiles, including
the auto-linked income transaction template created with each profile.
"""

import logging
from datetime import date

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from markupsafe import Markup
from sqlalchemy.exc import SQLAlchemyError

from app.utils.auth_helpers import get_or_404, require_owner
from app.extensions import db
from app.models.salary_profile import SalaryProfile
from app.models.transaction_template import TransactionTemplate
from app.models.recurrence_rule import RecurrenceRule
from app.models.category import Category
from app.models.account import Account
from app.models.ref import (
    CalcMethod,
    DeductionTiming,
    FilingStatus,
    RaiseType,
)
from app import ref_cache
from app.enums import RecurrencePatternEnum, TxnTypeEnum
from app.services import (
    paycheck_calculator,
    pay_period_service,
    recurrence_engine,
)
from app.services.scenario_resolver import get_baseline_scenario
from app.services.tax_config_service import load_tax_configs
from app.routes._commit_helpers import (
    commit_or_handle_stale,
    regenerate_and_commit_or_stale,
)
from app.routes.salary._bp import salary_bp
from app.routes.salary._helpers import (
    _PROFILE_UPDATE_FIELDS,
    _create_schema,
    _get_investment_accounts,
    _regenerate_salary_transactions,
    _update_schema,
)

logger = logging.getLogger(__name__)


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
            pay_breakdown = paycheck_calculator.calculate_paycheck(
                profile, current_period, periods, tax_configs,
                calibration=profile.calibration,
            )
            net_pay = pay_breakdown.net_pay
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
    scenario = get_baseline_scenario(current_user.id)
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
    except SQLAlchemyError:
        # Narrow catch (C-46 / F-145): DB-tier failures (FK, CHECK,
        # NUMERIC range, OperationalError, etc.) produce the user-
        # facing flash + redirect.  Non-SQLAlchemy exceptions
        # (TypeError, AttributeError, decimal arithmetic) propagate
        # to the Flask 500 handler so they surface as bugs rather
        # than being silently swallowed.
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
    profile = get_or_404(SalaryProfile, profile_id)
    if profile is None:
        abort(404)

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
    profile = get_or_404(SalaryProfile, profile_id)
    if profile is None:
        abort(404)

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
        for field_name, value in data.items():
            if field_name in _PROFILE_UPDATE_FIELDS:
                setattr(profile, field_name, value)

        # Update linked template amount
        if profile.template and "annual_salary" in data:
            pay_periods = profile.pay_periods_per_year or 26
            profile.template.default_amount = data["annual_salary"] / pay_periods
            if "name" in data:
                profile.template.name = data["name"]

        # Regenerate transactions and commit under the canonical
        # optimistic-lock guard (C-18 / F-010): the regeneration flushes,
        # so it must run inside the same stale-race guard as the commit.
        conflict = regenerate_and_commit_or_stale(
            lambda: _regenerate_salary_transactions(profile),
            logger=logger,
            log_label="update_profile",
            log_id=profile_id,
            flash_message=(
                "This salary profile was changed by another action while you "
                "were editing.  Please reload and try again."
            ),
            redirect_endpoint="salary.edit_profile",
            redirect_endpoint_kwargs={"profile_id": profile_id},
        )
        if conflict is not None:
            return conflict
    except SQLAlchemyError:
        # Narrow catch (C-46 / F-145): see ``create_profile`` for the
        # rationale.  ``StaleDataError`` is caught first, inside
        # ``regenerate_and_commit_or_stale`` above, so optimistic-locking
        # conflicts never reach this broader branch.
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
    profile = get_or_404(SalaryProfile, profile_id)
    if profile is None:
        abort(404)

    profile.is_active = False
    if profile.template:
        profile.template.is_active = False

    conflict = commit_or_handle_stale(
        logger=logger,
        log_label="delete_profile",
        log_id=profile_id,
        flash_message=(
            "This salary profile was changed by another action.  "
            "Please reload and try again."
        ),
        redirect_endpoint="salary.list_profiles",
    )
    if conflict is not None:
        return conflict
    logger.info("user_id=%d deactivated salary profile %d", current_user.id, profile_id)
    flash(f"Salary profile '{profile.name}' deactivated.", "info")
    return redirect(url_for("salary.list_profiles"))
