"""
Shekel Budget App -- Pay Period Routes

Generates the biweekly schedule and manages its lifecycle: extend the
schedule forward, truncate the tail, and regenerate a wrong future tail.
All management actions are full-page POST + redirect (or a 422 re-render
of the settings dashboard when a discard needs confirming); they live on
the settings "pay-periods" section.
"""

import logging

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.utils.auth_helpers import require_owner

from app.extensions import db
from app.exceptions import (
    PayPeriodDiscardRequired,
    PayPeriodLocked,
    ValidationError,
)
from app.routes.settings import render_settings_dashboard
from app.schemas.validation import (
    PayPeriodExtendSchema,
    PayPeriodGenerateSchema,
    PayPeriodRegenerateSchema,
    PayPeriodTruncateSchema,
    PayScheduleSchema,
)
from app.services import pay_period_admin, pay_period_service, pay_schedule_service

logger = logging.getLogger(__name__)

pay_periods_bp = Blueprint("pay_periods", __name__)

_generate_schema = PayPeriodGenerateSchema()
_extend_schema = PayPeriodExtendSchema()
_truncate_schema = PayPeriodTruncateSchema()
_regenerate_schema = PayPeriodRegenerateSchema()
_schedule_schema = PayScheduleSchema()


def _pay_periods_redirect():
    """Redirect back to the settings pay-periods section."""
    return redirect(url_for("settings.show", section="pay-periods"))


def _summarize_errors(errors):
    """Flatten a Marshmallow error dict into one flash-able sentence."""
    parts = [
        f"{field}: {'; '.join(str(m) for m in messages)}"
        for field, messages in errors.items()
    ]
    return "Please correct the form: " + " | ".join(parts)


@pay_periods_bp.route("/pay-periods/generate", methods=["GET"])
@login_required
@require_owner
def generate_form():
    """Redirect to settings dashboard pay periods section."""
    return redirect(url_for("settings.show", section="pay-periods"))


@pay_periods_bp.route("/pay-periods/generate", methods=["POST"])
@login_required
@require_owner
def generate():
    """Generate pay periods from the submitted form data."""
    errors = _generate_schema.validate(request.form)
    if errors:
        return render_template("pay_periods/generate.html", errors=errors), 422

    data = _generate_schema.load(request.form)

    try:
        periods = pay_period_service.generate_pay_periods(
            user_id=current_user.id,
            start_date=data["start_date"],
            num_periods=data["num_periods"],
            cadence_days=data["cadence_days"],
        )
    except ValidationError as exc:
        # Forward-only invariant (DH-#39): a start date that would
        # interleave or overlap the existing schedule is rejected.
        # Surface it on the start_date field, mirroring the schema 422.
        return render_template(
            "pay_periods/generate.html",
            errors={"start_date": [str(exc)]},
        ), 422
    # Capture the cadence authoritatively at first generation so extend /
    # rolling top-up have a persisted cadence to continue from.
    pay_schedule_service.upsert_schedule(current_user.id, data["cadence_days"])
    db.session.commit()

    flash(f"Generated {len(periods)} pay periods.", "success")
    return redirect(url_for("grid.index"))


@pay_periods_bp.route("/pay-periods/extend", methods=["POST"])
@login_required
@require_owner
def extend():
    """Append pay periods to the end of the schedule."""
    errors = _extend_schema.validate(request.form)
    if errors:
        flash(_summarize_errors(errors), "danger")
        return _pay_periods_redirect()

    data = _extend_schema.load(request.form)
    try:
        new_periods = pay_period_admin.extend_pay_periods(
            current_user.id, data["num_periods"], data.get("cadence_days"),
        )
    except ValidationError as exc:
        flash(str(exc), "danger")
        return _pay_periods_redirect()

    db.session.commit()
    flash(f"Added {len(new_periods)} pay periods.", "success")
    return _pay_periods_redirect()


@pay_periods_bp.route("/pay-periods/truncate", methods=["POST"])
@login_required
@require_owner
def truncate():
    """Delete the schedule tail beyond the chosen period."""
    errors = _truncate_schema.validate(request.form)
    if errors:
        flash(_summarize_errors(errors), "danger")
        return _pay_periods_redirect()

    data = _truncate_schema.load(request.form)
    try:
        deleted = pay_period_admin.truncate_pay_periods(
            current_user.id, data["keep_through_index"],
            confirm_discard=data["confirm_discard"],
        )
    except PayPeriodLocked as exc:
        flash(str(exc), "danger")
        return _pay_periods_redirect()
    except PayPeriodDiscardRequired as exc:
        return render_settings_dashboard("pay-periods", extra={"pp_confirm": {
            "op": "truncate",
            "count": exc.count,
            "params": {"keep_through_index": data["keep_through_index"]},
        }}, status=422)

    db.session.commit()
    flash(f"Removed {deleted} pay period(s).", "success")
    return _pay_periods_redirect()


@pay_periods_bp.route("/pay-periods/regenerate", methods=["POST"])
@login_required
@require_owner
def regenerate():
    """Rebuild the not-yet-started future tail from a corrected start."""
    errors = _regenerate_schema.validate(request.form)
    if errors:
        flash(_summarize_errors(errors), "danger")
        return _pay_periods_redirect()

    data = _regenerate_schema.load(request.form)
    try:
        new_periods = pay_period_admin.regenerate_pay_periods(
            current_user.id, data["new_start_date"], data["num_periods"],
            data["cadence_days"], confirm_discard=data["confirm_discard"],
        )
    except (PayPeriodLocked, ValidationError) as exc:
        flash(str(exc), "danger")
        return _pay_periods_redirect()
    except PayPeriodDiscardRequired as exc:
        return render_settings_dashboard("pay-periods", extra={"pp_confirm": {
            "op": "regenerate",
            "count": exc.count,
            "params": {
                "new_start_date": data["new_start_date"].isoformat(),
                "num_periods": data["num_periods"],
                "cadence_days": data["cadence_days"],
            },
        }}, status=422)

    db.session.commit()
    flash(f"Rebuilt the schedule: {len(new_periods)} new period(s).", "success")
    return _pay_periods_redirect()


@pay_periods_bp.route("/pay-periods/schedule", methods=["POST"])
@login_required
@require_owner
def schedule():
    """Save the continuous-rolling-window configuration."""
    errors = _schedule_schema.validate(request.form)
    if errors:
        flash(_summarize_errors(errors), "danger")
        return _pay_periods_redirect()

    data = _schedule_schema.load(request.form)
    try:
        pay_schedule_service.set_rolling(
            current_user.id,
            enabled=data["rolling_enabled"],
            target_periods=data["rolling_target_periods"],
        )
    except ValidationError as exc:
        flash(str(exc), "danger")
        return _pay_periods_redirect()

    db.session.commit()
    flash("Rolling-window settings saved.", "success")
    return _pay_periods_redirect()
