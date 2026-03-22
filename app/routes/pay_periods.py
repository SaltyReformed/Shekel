"""
Shekel Budget App -- Pay Period Routes

Provides the pay period generation form and handles creating
the biweekly schedule.
"""

import logging

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.schemas.validation import PayPeriodGenerateSchema
from app.services import pay_period_service

logger = logging.getLogger(__name__)

pay_periods_bp = Blueprint("pay_periods", __name__)

_generate_schema = PayPeriodGenerateSchema()


@pay_periods_bp.route("/pay-periods/generate", methods=["GET"])
@login_required
def generate_form():
    """Redirect to settings dashboard pay periods section."""
    return redirect(url_for("settings.show", section="pay-periods"))


@pay_periods_bp.route("/pay-periods/generate", methods=["POST"])
@login_required
def generate():
    """Generate pay periods from the submitted form data."""
    errors = _generate_schema.validate(request.form)
    if errors:
        return render_template("pay_periods/generate.html", errors=errors), 422

    data = _generate_schema.load(request.form)

    periods = pay_period_service.generate_pay_periods(
        user_id=current_user.id,
        start_date=data["start_date"],
        num_periods=data["num_periods"],
        cadence_days=data["cadence_days"],
    )
    db.session.commit()

    flash(f"Generated {len(periods)} pay periods.", "success")
    return redirect(url_for("grid.index"))
