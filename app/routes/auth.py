"""
Shekel Budget App — Auth Routes

Handles login and logout with Flask-Login session management.
Phase 1 uses a single seeded user — no registration route.
"""

import logging

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.extensions import limiter
from app.services import auth_service
from app.exceptions import AuthError

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per 15 minutes", methods=["POST"])
def login():
    """Display the login form and handle authentication."""
    # Already logged in — go to the grid.
    if current_user.is_authenticated:
        return redirect(url_for("grid.index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        remember = request.form.get("remember") == "on"

        try:
            user = auth_service.authenticate(email, password)
            login_user(user, remember=remember)
            logger.info("User %s logged in", email)

            # Redirect to the page they originally wanted, or the grid.
            next_page = request.args.get("next")
            return redirect(next_page or url_for("grid.index"))

        except AuthError:
            logger.warning("action=login_failed email=%s ip=%s", email, request.remote_addr)
            flash("Invalid email or password.", "danger")

    return render_template("auth/login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    """End the user's session and redirect to login."""
    logger.info("User %s logged out", current_user.email)
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))
