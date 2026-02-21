"""
Shekel Budget App — Settings Routes

User preferences management (grid defaults, inflation rate, etc.).
"""

import logging

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models.user import UserSettings

logger = logging.getLogger(__name__)

settings_bp = Blueprint("settings", __name__)


@settings_bp.route("/settings", methods=["GET"])
@login_required
def show():
    """Display the user settings page."""
    settings = current_user.settings
    if settings is None:
        # Create default settings if they don't exist.
        settings = UserSettings(user_id=current_user.id)
        db.session.add(settings)
        db.session.commit()

    return render_template("settings/settings.html", settings=settings)


@settings_bp.route("/settings", methods=["POST"])
@login_required
def update():
    """Update user settings."""
    settings = current_user.settings
    if settings is None:
        settings = UserSettings(user_id=current_user.id)
        db.session.add(settings)

    # Update grid default periods.
    grid_periods = request.form.get("grid_default_periods")
    if grid_periods:
        settings.grid_default_periods = int(grid_periods)

    # Update default inflation rate.
    inflation = request.form.get("default_inflation_rate")
    if inflation:
        from decimal import Decimal
        settings.default_inflation_rate = Decimal(inflation)

    db.session.commit()
    flash("Settings updated.", "success")
    return redirect(url_for("settings.show"))
