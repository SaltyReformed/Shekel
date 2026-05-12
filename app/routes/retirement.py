"""
Shekel Budget App -- Retirement Planning Routes

Retirement dashboard with pension management, income gap analysis,
and retirement planning settings.
"""

import logging
from datetime import date
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from app.utils.auth_helpers import get_or_404, require_owner
from app.utils.db_errors import is_unique_violation

from app.extensions import db
from app.models.pension_profile import PensionProfile
from app.models.salary_profile import SalaryProfile
from app.models.user import UserSettings
from app.schemas.validation import (
    PensionProfileCreateSchema,
    PensionProfileUpdateSchema,
    RetirementSettingsSchema,
)
from app.services import retirement_dashboard_service

logger = logging.getLogger(__name__)

# Name of the composite unique constraint that backstops the
# pension-profile double-submit fix (F-105 / C-22).  Mirrors the
# literal in ``app/models/pension_profile.py:PensionProfile.__table_args__``
# and ``migrations/versions/<C-22 revision>.py``; renaming the
# constraint requires a coordinated edit across all three sites.
_PENSION_PROFILE_UNIQUE_CONSTRAINT = "uq_pension_profiles_user_name"

retirement_bp = Blueprint("retirement", __name__)

_pension_create_schema = PensionProfileCreateSchema()
_pension_update_schema = PensionProfileUpdateSchema()
_settings_schema = RetirementSettingsSchema()


@retirement_bp.route("/retirement")
@login_required
@require_owner
def dashboard():
    """Retirement planning dashboard with gap analysis."""
    data = retirement_dashboard_service.compute_gap_data(current_user.id)
    slider = retirement_dashboard_service.compute_slider_defaults(data)

    return render_template(
        "retirement/dashboard.html",
        current_swr=slider["current_swr"],
        current_return=slider["current_return"],
        **data,
    )


# ── Pension CRUD ─────────────────────────────────────────────────


@retirement_bp.route("/retirement/pension")
@login_required
@require_owner
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
@require_owner
def create_pension():
    """Create a new pension profile."""
    errors = _pension_create_schema.validate(request.form)
    if errors:
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
            form_data=dict(request.form),
            errors=errors,
        ), 422

    data = _pension_create_schema.load(request.form)

    # Convert percentage input (e.g. 1.85 → 0.0185).
    if data.get("benefit_multiplier"):
        data["benefit_multiplier"] = Decimal(str(data["benefit_multiplier"])) / Decimal("100")

    pension = PensionProfile(user_id=current_user.id, **data)
    db.session.add(pension)
    try:
        db.session.commit()
    except IntegrityError as exc:
        # Duplicate-name double-submit (F-105 / C-22): the composite
        # unique ``uq_pension_profiles_user_name`` rejects the second
        # INSERT when the user clicks Save twice in a row.  Roll back
        # and treat as idempotent success: re-fetch the winning row
        # so the user lands on the retirement dashboard with the
        # pension they intended to create, regardless of which
        # request reached the database first.
        db.session.rollback()
        if not is_unique_violation(exc, _PENSION_PROFILE_UNIQUE_CONSTRAINT):
            raise
        existing = (
            db.session.query(PensionProfile)
            .filter_by(user_id=current_user.id, name=data["name"])
            .first()
        )
        if existing is None:
            # The winning row was deleted between the IntegrityError
            # and this lookup -- vanishingly unlikely.  Surface as a
            # warning and let the user retry.
            flash(
                "A pension profile with that name already exists.",
                "warning",
            )
            return redirect(url_for("retirement.dashboard"))
        logger.info(
            "Duplicate pension profile prevented; existing id=%d "
            "(idempotent success)", existing.id,
        )
        flash(f"Pension profile '{existing.name}' already exists.", "info")
        return redirect(url_for("retirement.dashboard"))

    logger.info("user_id=%d created pension profile %d", current_user.id, pension.id)
    flash(f"Pension profile '{pension.name}' created.", "success")
    return redirect(url_for("retirement.dashboard"))


@retirement_bp.route("/retirement/pension/<int:pension_id>/edit")
@login_required
@require_owner
def edit_pension(pension_id):
    """Display pension profile edit form."""
    pension = get_or_404(PensionProfile, pension_id)
    if pension is None:
        abort(404)

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
@require_owner
def update_pension(pension_id):
    """Update a pension profile."""
    pension = get_or_404(PensionProfile, pension_id)
    if pension is None:
        abort(404)

    # Context needed for error re-render (same as edit_pension GET).
    salary_profiles = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=current_user.id, is_active=True)
        .all()
    )

    errors = _pension_update_schema.validate(request.form)
    if errors:
        return render_template(
            "retirement/pension_form.html",
            pension=pension,
            pensions=[],
            salary_profiles=salary_profiles,
            form_data=dict(request.form),
            errors=errors,
        ), 422

    data = _pension_update_schema.load(request.form)

    # Convert percentage input.
    if data.get("benefit_multiplier"):
        data["benefit_multiplier"] = Decimal(str(data["benefit_multiplier"])) / Decimal("100")

    # Cross-field date validation: merge submitted values with existing
    # pension data so partial updates are validated against the full state.
    eff_hire = data.get("hire_date", pension.hire_date)
    eff_earliest = data.get("earliest_retirement_date", pension.earliest_retirement_date)
    eff_planned = data.get("planned_retirement_date", pension.planned_retirement_date)

    date_errors = {}
    if eff_earliest and eff_hire and eff_earliest <= eff_hire:
        date_errors.setdefault("earliest_retirement_date", []).append(
            "Must be after hire date."
        )
    if eff_planned and eff_hire and eff_planned <= eff_hire:
        date_errors.setdefault("planned_retirement_date", []).append(
            "Must be after hire date."
        )
    if eff_planned and eff_planned <= date.today():
        date_errors.setdefault("planned_retirement_date", []).append(
            "Must be in the future."
        )
    if eff_planned and eff_earliest and eff_planned < eff_earliest:
        date_errors.setdefault("planned_retirement_date", []).append(
            "Must be on or after earliest retirement date."
        )
    if date_errors:
        return render_template(
            "retirement/pension_form.html",
            pension=pension,
            pensions=[],
            salary_profiles=salary_profiles,
            form_data=dict(request.form),
            errors=date_errors,
        ), 422

    _PENSION_FIELDS = {
        "salary_profile_id", "name", "benefit_multiplier",
        "consecutive_high_years", "hire_date",
        "earliest_retirement_date", "planned_retirement_date",
    }
    for field_name, value in data.items():
        if field_name in _PENSION_FIELDS:
            setattr(pension, field_name, value)

    try:
        db.session.commit()
    except IntegrityError as exc:
        # Name-collision rename (F-105 / C-22): renaming this profile
        # to a name another active pension already holds violates
        # ``uq_pension_profiles_user_name``.  Surface as a 422 with
        # the field-level error rather than crashing the request --
        # the user expects a form-level message, not a 500.
        db.session.rollback()
        if not is_unique_violation(exc, _PENSION_PROFILE_UNIQUE_CONSTRAINT):
            raise
        return render_template(
            "retirement/pension_form.html",
            pension=pension,
            pensions=[],
            salary_profiles=salary_profiles,
            form_data=dict(request.form),
            errors={"name": ["You already have a pension profile with this name."]},
        ), 422
    logger.info("user_id=%d updated pension profile %d", current_user.id, pension_id)
    flash(f"Pension profile '{pension.name}' updated.", "success")
    return redirect(url_for("retirement.dashboard"))


@retirement_bp.route("/retirement/pension/<int:pension_id>/delete", methods=["POST"])
@login_required
@require_owner
def delete_pension(pension_id):
    """Deactivate a pension profile."""
    pension = get_or_404(PensionProfile, pension_id)
    if pension is None:
        abort(404)

    pension.is_active = False
    db.session.commit()
    logger.info("user_id=%d deactivated pension profile %d", current_user.id, pension_id)
    flash(f"Pension profile '{pension.name}' deactivated.", "info")
    return redirect(url_for("retirement.dashboard"))


# ── Gap Analysis Fragment ────────────────────────────────────────


@retirement_bp.route("/retirement/gap")
@login_required
@require_owner
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

    data = retirement_dashboard_service.compute_gap_data(
        current_user.id,
        swr_override=swr_override,
        return_rate_override=return_rate_override,
    )

    return render_template(
        "retirement/_gap_analysis.html",
        gap_analysis=data["gap_analysis"],
        chart_data=data["chart_data"],
        retirement_account_projections=data["retirement_account_projections"],
        htmx_response=True,
    )


# ── Retirement Settings ──────────────────────────────────────────


@retirement_bp.route("/retirement/settings", methods=["POST"])
@login_required
@require_owner
def update_settings():
    """Update retirement planning settings."""
    # Preserve original user input for form re-display on error.
    raw_form_data = dict(request.form)

    # Convert percentage inputs from form.
    form_data = dict(request.form)
    for field in ("safe_withdrawal_rate", "estimated_retirement_tax_rate"):
        if field in form_data and form_data[field]:
            try:
                form_data[field] = str(Decimal(form_data[field]) / Decimal("100"))
            except InvalidOperation:
                # Narrow catch (C-46 / F-145): a non-numeric string
                # (e.g. "abc") raises ``decimal.InvalidOperation``.
                # Leave the raw value in place so the Marshmallow
                # schema rejects it with a field-level "Not a valid
                # number." message and the user sees the 422 form
                # re-render rather than a silent normalisation.
                pass

    errors = _settings_schema.validate(form_data)
    if errors:
        settings = (
            db.session.query(UserSettings)
            .filter_by(user_id=current_user.id)
            .first()
        )
        if not settings:
            settings = UserSettings(user_id=current_user.id)
        return render_template(
            "settings/dashboard.html",
            active_section="retirement",
            settings=settings,
            form_data=raw_form_data,
            errors=errors,
            accounts=[],
            grouped={},
            filing_statuses=[],
            tax_types=[],
            bracket_sets=[],
            fica_configs=[],
            state_configs=[],
            account_types=[],
            types_in_use=set(),
            mfa_enabled=False,
        ), 422

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
