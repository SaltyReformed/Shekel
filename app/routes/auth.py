"""
Shekel Budget App — Auth Routes

Handles login and logout with Flask-Login session management.
Phase 1 uses a single seeded user — no registration route.
"""

import logging
from datetime import datetime, timezone

from flask import Blueprint, flash, redirect, render_template, request, session as flask_session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.extensions import db, limiter
from app.models.user import MfaConfig, User
from app.services import auth_service, mfa_service
from app.exceptions import AuthError, ValidationError
from app.utils.log_events import log_event, AUTH

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

            # Check if MFA is enabled for this user.
            mfa_config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=user.id, is_enabled=True)
                .first()
            )
            if mfa_config:
                # Store pending auth state in session (user is NOT logged in yet).
                flask_session["_mfa_pending_user_id"] = user.id
                flask_session["_mfa_pending_remember"] = remember
                flask_session["_mfa_pending_next"] = request.args.get("next")
                return redirect(url_for("auth.mfa_verify"))

            # No MFA — complete login immediately.
            login_user(user, remember=remember)
            flask_session["_session_created_at"] = datetime.now(timezone.utc).isoformat()
            log_event(logger, logging.INFO, "login_success", AUTH,
                      "User logged in", user_id=user.id, email=email)

            # Redirect to the page they originally wanted, or the grid.
            next_page = request.args.get("next")
            return redirect(next_page or url_for("grid.index"))

        except AuthError:
            log_event(logger, logging.WARNING, "login_failed", AUTH,
                      "Login failed", email=email, ip=request.remote_addr)
            flash("Invalid email or password.", "danger")

    return render_template("auth/login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    """End the user's session and redirect to login."""
    log_event(logger, logging.INFO, "logout", AUTH,
              "User logged out", user_id=current_user.id)
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/change-password", methods=["POST"])
@login_required
def change_password():
    """Process a password change request from the Security settings section."""
    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")

    if new_password != confirm_password:
        flash("New password and confirmation do not match.", "danger")
        return redirect(url_for("settings.show", section="security"))

    try:
        auth_service.change_password(current_user, current_password, new_password)
        db.session.commit()
        # Invalidate all other sessions after password change.
        current_user.session_invalidated_at = datetime.now(timezone.utc)
        db.session.commit()
        flask_session["_session_created_at"] = datetime.now(timezone.utc).isoformat()
        log_event(logger, logging.INFO, "password_changed", AUTH,
                  "Password changed", user_id=current_user.id)
        flash("Password changed successfully.", "success")
    except AuthError as e:
        flash(str(e), "danger")
    except ValidationError as e:
        flash(str(e), "danger")

    return redirect(url_for("settings.show", section="security"))


@auth_bp.route("/invalidate-sessions", methods=["POST"])
@login_required
def invalidate_sessions():
    """Invalidate all sessions for the current user except the current one.

    Sets session_invalidated_at to now, which causes load_user() to
    reject any session created before this timestamp. The current
    session is refreshed with a new creation timestamp.
    """
    current_user.session_invalidated_at = datetime.now(timezone.utc)
    db.session.commit()
    # Refresh the current session so it survives the invalidation.
    flask_session["_session_created_at"] = datetime.now(timezone.utc).isoformat()
    log_event(logger, logging.INFO, "sessions_invalidated", AUTH,
              "All sessions invalidated", user_id=current_user.id)
    flash("All other sessions have been logged out.", "success")
    return redirect(url_for("settings.show", section="security"))


@auth_bp.route("/mfa/verify", methods=["GET", "POST"])
@limiter.limit("5 per 15 minutes", methods=["POST"])
def mfa_verify():
    """Display the MFA verification form and handle code submission.

    Requires a pending MFA user_id in the session (set by the login
    route after successful password verification). Completes the login
    on valid TOTP or backup code.
    """
    pending_user_id = flask_session.get("_mfa_pending_user_id")
    if not pending_user_id:
        return redirect(url_for("auth.login"))

    if request.method == "GET":
        return render_template("auth/mfa_verify.html")

    # POST — verify the submitted code.
    totp_code = request.form.get("totp_code", "").strip()
    backup_code = request.form.get("backup_code", "").strip()

    user = db.session.get(User, pending_user_id)
    if not user:
        # User was deleted between login steps — clear pending state.
        flask_session.pop("_mfa_pending_user_id", None)
        flask_session.pop("_mfa_pending_remember", None)
        flask_session.pop("_mfa_pending_next", None)
        return redirect(url_for("auth.login"))

    mfa_config = (
        db.session.query(MfaConfig)
        .filter_by(user_id=user.id, is_enabled=True)
        .first()
    )
    if not mfa_config:
        # MFA was disabled between login steps — clear pending state.
        flask_session.pop("_mfa_pending_user_id", None)
        flask_session.pop("_mfa_pending_remember", None)
        flask_session.pop("_mfa_pending_next", None)
        return redirect(url_for("auth.login"))

    secret = mfa_service.decrypt_secret(mfa_config.totp_secret_encrypted)
    valid = False

    if totp_code:
        # Verify the 6-digit TOTP code from an authenticator app.
        valid = mfa_service.verify_totp_code(secret, totp_code)
    elif backup_code:
        # Verify the 8-character backup code against stored hashes.
        idx = mfa_service.verify_backup_code(backup_code, mfa_config.backup_codes)
        if idx >= 0:
            # Remove the consumed backup code hash from the list.
            mfa_config.backup_codes = [
                h for i, h in enumerate(mfa_config.backup_codes) if i != idx
            ]
            db.session.commit()
            valid = True

    if not valid:
        flash("Invalid verification code.", "danger")
        return render_template("auth/mfa_verify.html")

    # Login completion — both password and TOTP/backup code are verified.
    remember = flask_session.pop("_mfa_pending_remember", False)
    next_page = flask_session.pop("_mfa_pending_next", None)
    flask_session.pop("_mfa_pending_user_id", None)

    login_user(user, remember=remember)
    flask_session["_session_created_at"] = datetime.now(timezone.utc).isoformat()
    log_event(logger, logging.INFO, "mfa_login_success", AUTH,
              "MFA login succeeded", user_id=user.id)

    return redirect(next_page or url_for("grid.index"))


@auth_bp.route("/mfa/setup", methods=["GET"])
@login_required
def mfa_setup():
    """Display the MFA setup page with QR code and manual key.

    Generates a new TOTP secret, stores it in the Flask session, and
    renders the setup template.  If MFA is already enabled the user is
    redirected back to security settings.
    """
    mfa_config = (
        db.session.query(MfaConfig)
        .filter_by(user_id=current_user.id)
        .first()
    )
    if mfa_config and mfa_config.is_enabled:
        flash("Two-factor authentication is already enabled.", "info")
        return redirect(url_for("settings.show", section="security"))

    secret = mfa_service.generate_totp_secret()
    flask_session["_mfa_setup_secret"] = secret
    qr_data_uri = mfa_service.generate_qr_code_data_uri(
        mfa_service.get_totp_uri(secret, current_user.email)
    )
    return render_template(
        "auth/mfa_setup.html",
        qr_data_uri=qr_data_uri,
        manual_key=secret,
    )


@auth_bp.route("/mfa/confirm", methods=["POST"])
@login_required
def mfa_confirm():
    """Verify a TOTP code and enable MFA for the current user.

    Reads the secret from the Flask session (stored during /mfa/setup),
    verifies the submitted code, then encrypts and persists the secret
    along with freshly-generated backup codes.
    """
    secret = flask_session.pop("_mfa_setup_secret", None)
    if secret is None:
        flash("MFA setup session expired. Please start again.", "danger")
        return redirect(url_for("auth.mfa_setup"))

    totp_code = request.form.get("totp_code", "")
    if not mfa_service.verify_totp_code(secret, totp_code):
        # Re-store the secret so the user can retry without re-scanning.
        flask_session["_mfa_setup_secret"] = secret
        flash("Invalid code. Please try again.", "danger")
        return redirect(url_for("auth.mfa_setup"))

    # Valid code — enable MFA.
    mfa_config = (
        db.session.query(MfaConfig)
        .filter_by(user_id=current_user.id)
        .first()
    )
    if not mfa_config:
        mfa_config = MfaConfig(user_id=current_user.id)
        db.session.add(mfa_config)

    mfa_config.totp_secret_encrypted = mfa_service.encrypt_secret(secret)
    mfa_config.is_enabled = True
    mfa_config.confirmed_at = datetime.now(timezone.utc)

    codes = mfa_service.generate_backup_codes()
    mfa_config.backup_codes = mfa_service.hash_backup_codes(codes)
    db.session.commit()

    log_event(logger, logging.INFO, "mfa_enabled", AUTH,
              "MFA enabled", user_id=current_user.id)
    return render_template("auth/mfa_backup_codes.html", backup_codes=codes)


@auth_bp.route("/mfa/regenerate-backup-codes", methods=["POST"])
@login_required
def regenerate_backup_codes():
    """Generate and display new backup codes, replacing the old ones."""
    mfa_config = (
        db.session.query(MfaConfig)
        .filter_by(user_id=current_user.id)
        .first()
    )
    if not mfa_config or not mfa_config.is_enabled:
        flash("Two-factor authentication is not enabled.", "danger")
        return redirect(url_for("settings.show", section="security"))

    codes = mfa_service.generate_backup_codes()
    mfa_config.backup_codes = mfa_service.hash_backup_codes(codes)
    db.session.commit()

    log_event(logger, logging.INFO, "backup_codes_regenerated", AUTH,
              "Backup codes regenerated", user_id=current_user.id)
    return render_template("auth/mfa_backup_codes.html", backup_codes=codes)


@auth_bp.route("/mfa/disable", methods=["GET"])
@login_required
def mfa_disable():
    """Display the MFA disable confirmation page.

    Requires MFA to be currently enabled. Redirects to security
    settings if MFA is not enabled.
    """
    mfa_config = (
        db.session.query(MfaConfig)
        .filter_by(user_id=current_user.id)
        .first()
    )
    if not mfa_config or not mfa_config.is_enabled:
        flash("Two-factor authentication is not enabled.", "info")
        return redirect(url_for("settings.show", section="security"))

    return render_template("auth/mfa_disable.html")


@auth_bp.route("/mfa/disable", methods=["POST"])
@login_required
def mfa_disable_confirm():
    """Process MFA disable after verifying password and TOTP code.

    Clears the TOTP secret, backup codes, and sets is_enabled to False.
    """
    current_password = request.form.get("current_password", "")
    totp_code = request.form.get("totp_code", "").strip()

    # Verify the user's current password first.
    if not auth_service.verify_password(current_password, current_user.password_hash):
        flash("Invalid password.", "danger")
        return redirect(url_for("auth.mfa_disable"))

    # Load MFA config and verify TOTP code.
    mfa_config = (
        db.session.query(MfaConfig)
        .filter_by(user_id=current_user.id)
        .first()
    )
    if not mfa_config or not mfa_config.is_enabled:
        flash("Two-factor authentication is not enabled.", "danger")
        return redirect(url_for("settings.show", section="security"))

    secret = mfa_service.decrypt_secret(mfa_config.totp_secret_encrypted)
    if not mfa_service.verify_totp_code(secret, totp_code):
        flash("Invalid authentication code.", "danger")
        return redirect(url_for("auth.mfa_disable"))

    # Clear all MFA fields.
    mfa_config.totp_secret_encrypted = None
    mfa_config.is_enabled = False
    mfa_config.backup_codes = None
    mfa_config.confirmed_at = None
    db.session.commit()

    log_event(logger, logging.INFO, "mfa_disabled", AUTH,
              "MFA disabled", user_id=current_user.id)
    flash("Two-factor authentication has been disabled.", "success")
    return redirect(url_for("settings.show", section="security"))
