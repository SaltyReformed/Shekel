"""
Shekel Budget App -- Salary route package: pay-stub calibration.

Two-step calibration flow (form -> preview -> confirm) plus deletion.
Calibration is an immutable pay-stub-grounded snapshot (HIGH-03 / Q-25 /
E-20): the effective tax rates are always re-derived server-side from the
stored ``actual_*`` amounts and the live taxable base; posted rate values
are never trusted for storage and a mismatch is treated as tampering.
"""

import logging
from decimal import Decimal

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError

from app.utils.auth_helpers import get_or_404, require_owner
from app.extensions import db
from app.models.salary_profile import SalaryProfile
from app.models.calibration_override import CalibrationOverride
from app.exceptions import ValidationError
from app.services.calibration_service import derive_effective_rates
from app.routes.salary._bp import salary_bp
from app.routes.salary._helpers import (
    _calibration_confirm_schema,
    _calibration_schema,
    _compute_total_pre_tax,
    _regenerate_salary_transactions,
    _reject_if_rates_inconsistent,
)

logger = logging.getLogger(__name__)


@salary_bp.route("/salary/<int:profile_id>/calibrate")
@login_required
@require_owner
def calibrate_form(profile_id):
    """Display the pay stub calibration form."""
    profile = get_or_404(SalaryProfile, profile_id)
    if profile is None:
        abort(404)

    return render_template(
        "salary/calibrate.html",
        profile=profile,
    )


@salary_bp.route("/salary/<int:profile_id>/calibrate", methods=["POST"])
@login_required
@require_owner
def calibrate_preview(profile_id):
    """Validate pay stub data and show derived rates for confirmation."""
    profile = get_or_404(SalaryProfile, profile_id)
    if profile is None:
        abort(404)

    errors = _calibration_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("salary.calibrate_form", profile_id=profile_id))

    data = _calibration_schema.load(request.form)

    # Calculate taxable income from the profile's current pre-tax deductions.
    gross = Decimal(str(data["actual_gross_pay"]))
    total_pre_tax = _compute_total_pre_tax(profile)
    taxable = gross - total_pre_tax
    if taxable <= Decimal("0"):
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
    """Save the calibration override and regenerate transactions.

    HIGH-03 / Q-25 / E-20 (audit 2026-05-19): calibration is an
    immutable pay-stub-grounded snapshot.  The four ``effective_*_rate``
    columns are re-derived server-side from the stored ``actual_*``
    plus the freshly-computed taxable base (gross minus current pre-tax
    deductions); the rate values posted via hidden form fields are
    never trusted for storage.  The schema's FICA cross-check
    (``CalibrationConfirmSchema.validate_fica_rate_consistency``) and
    the federal/state cross-check below jointly reject tampered or
    stale two-step submissions as 422 -- a discrepancy between the
    posted ``actual_*`` and ``effective_*_rate`` fields is a signal of
    tampering or stale browser state, not a legitimate user error
    (the confirm form is fully server-generated from the preview).

    The schema's job is to enforce the consistency invariant on the
    POST itself; the route's job is to ignore the posted rates for
    storage and to extend the invariant to the federal/state divisor
    (the profile-derived taxable base, not available at the schema
    layer).
    """
    profile = get_or_404(SalaryProfile, profile_id)
    if profile is None:
        abort(404)

    errors = _calibration_confirm_schema.validate(request.form)
    if errors:
        # HIGH-03 / E-20: the confirm form is fully server-generated
        # from the preview; any validation failure here means tampering
        # or stale browser state, not legitimate user error.  Return
        # 422 rather than silently redirecting -- CLAUDE.md rule 4
        # (never ignore a problem).
        logger.info(
            "Rejected calibration confirm for profile %d (tampering or "
            "stale browser state, errors=%s)",
            profile_id, sorted(errors.keys()),
        )
        abort(422)

    data = _calibration_confirm_schema.load(request.form)

    # Re-compute the taxable base from the profile's current pre-tax
    # deductions, identical to ``calibrate_preview``.  A fresh
    # computation here is the load-bearing piece of the E-20 immutable-
    # snapshot invariant: the stored rate is derived against the
    # snapshot's own ``actual_*`` plus the live taxable base, not
    # against a posted rate the client could have tampered with or
    # whose source taxable base could have shifted between preview and
    # confirm.
    total_pre_tax = _compute_total_pre_tax(profile)
    gross = Decimal(str(data["actual_gross_pay"]))
    taxable = gross - total_pre_tax
    if taxable <= Decimal("0"):
        # Profile-state issue (pre-tax deductions exceed gross), not
        # tampering; redirect to the form so the user can adjust the
        # profile and re-do the preview.  Mirrors ``calibrate_preview``.
        flash(
            "Taxable income (gross minus pre-tax deductions) is zero or "
            "negative. Cannot derive effective rates.",
            "danger",
        )
        return redirect(url_for("salary.calibrate_form", profile_id=profile_id))

    try:
        derived_rates = derive_effective_rates(
            actual_gross_pay=data["actual_gross_pay"],
            actual_federal_tax=data["actual_federal_tax"],
            actual_state_tax=data["actual_state_tax"],
            actual_social_security=data["actual_social_security"],
            actual_medicare=data["actual_medicare"],
            taxable_income=taxable,
        )
    except ValidationError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("salary.calibrate_form", profile_id=profile_id))

    # Federal/state cross-check: the schema covers FICA (divisor = posted
    # ``actual_gross_pay``); federal/state's divisor is the live taxable
    # base, available only here.  Aborts 422 on a mismatch worth more than
    # one cent of withholding (E-20 / C19-2 tampering signal).
    _reject_if_rates_inconsistent(data, derived_rates, taxable, profile_id)

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
            # E-20: store the SERVER-DERIVED rates, not the posted
            # ones.  The schema and federal/state cross-checks above
            # have proven posted == derived within tolerance; storing
            # the derived value pins the snapshot to the canonical
            # arithmetic and removes any residual posted-precision
            # variance from the persisted row.
            effective_federal_rate=derived_rates.effective_federal_rate,
            effective_state_rate=derived_rates.effective_state_rate,
            effective_ss_rate=derived_rates.effective_ss_rate,
            effective_medicare_rate=derived_rates.effective_medicare_rate,
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
    except SQLAlchemyError:
        # Narrow catch (C-46 / F-145): DB-tier failures during the
        # delete-then-insert of the calibration row plus the
        # subsequent transactions regeneration (FK, CHECK, NUMERIC
        # range, OperationalError) produce the user-facing flash.
        # Non-SQLAlchemy exceptions propagate to the 500 handler.
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
    profile = get_or_404(SalaryProfile, profile_id)
    if profile is None:
        abort(404)

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
        except SQLAlchemyError:
            # Narrow catch (C-46 / F-145): DB-tier failures during
            # the post-deletion regeneration land here.  Non-
            # SQLAlchemy exceptions propagate to the 500 handler.
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
