"""
Shekel Budget App -- Auth Routes

Handles login, registration, and logout with Flask-Login session management.
"""

import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session as flask_session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from cryptography.fernet import InvalidToken

from app import ref_cache
from app.enums import RoleEnum
from app.extensions import db, limiter
from app.models.user import MfaConfig, User
from app.services import auth_service, mfa_service
from app.exceptions import AuthError, ConflictError, ValidationError
from app.utils.log_events import log_event, AUTH

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)

# Maximum time between scanning the QR/typing the manual key on /mfa/setup
# and submitting the verification code on /mfa/confirm.  Bounds the
# server-side window in which an unconfirmed TOTP secret remains
# promotable to an active credential.  Long enough to accommodate users
# who fumble with an authenticator app and short enough that an attacker
# who briefly compromised the account cannot revisit /mfa/confirm later
# to silently enrol their own device.  See audit finding F-031 / commit
# C-05 of the 2026-04-15 security remediation plan.
MFA_SETUP_PENDING_TTL = timedelta(minutes=15)


def _is_safe_redirect(target):
    """Check that a redirect target is a safe, relative URL.

    Prevents open redirect attacks by rejecting any URL that contains
    a scheme (http, https, javascript, data, etc.) or a network
    location (netloc).  Also rejects protocol-relative URLs (//evil.com),
    backslash-prefixed URLs (\\\\evil.com -- some browsers normalize \\\\ to //),
    and targets with embedded newlines or whitespace that could bypass
    parsing.

    Args:
        target: The redirect URL string from request.args or session.

    Returns:
        True if the target is a safe relative path (e.g. /templates,
        /settings?section=security).  False for None, empty strings,
        absolute URLs, protocol-relative URLs, and malformed inputs.
    """
    if not target:
        return False

    # Strip leading/trailing whitespace -- browsers may normalize this.
    stripped = target.strip()
    if not stripped:
        return False

    # Reject targets containing newlines, carriage returns, or tabs
    # (header injection / parser confusion), and backslash-prefixed paths
    # (some browsers normalize \\ to //, making \\evil.com a protocol-
    # relative URL).
    if any(c in stripped for c in ("\n", "\r", "\t")) or stripped.startswith("\\"):
        return False

    parsed = urlparse(stripped)

    # Reject any URL with a scheme (http, https, javascript, data, ftp,
    # etc.) or a network location (//evil.com parses with netloc="evil.com"
    # and no scheme).
    if parsed.scheme or parsed.netloc:
        return False

    return True


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per 15 minutes", methods=["POST"])
def login():
    """Display the login form and handle authentication."""
    # Already logged in -- redirect to the appropriate landing page.
    if current_user.is_authenticated:
        companion_id = ref_cache.role_id(RoleEnum.COMPANION)
        if current_user.role_id == companion_id:
            return redirect(url_for("companion.index"))
        return redirect(url_for("dashboard.page"))

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
                # Validate the next parameter at storage time (defense in depth).
                pending_next = request.args.get("next")
                flask_session["_mfa_pending_next"] = (
                    pending_next if _is_safe_redirect(pending_next) else None
                )
                return redirect(url_for("auth.mfa_verify"))

            # No MFA -- complete login immediately.
            login_user(user, remember=remember)
            flask_session["_session_created_at"] = datetime.now(timezone.utc).isoformat()
            log_event(logger, logging.INFO, "login_success", AUTH,
                      "User logged in", user_id=user.id, email=email)

            # Companions always go to companion.index (ignore next param).
            companion_id = ref_cache.role_id(RoleEnum.COMPANION)
            if user.role_id == companion_id:
                return redirect(url_for("companion.index"))

            # Redirect to the page they originally wanted, or the dashboard.
            # Validate the next parameter to prevent open redirect attacks.
            next_page = request.args.get("next")
            if not _is_safe_redirect(next_page):
                next_page = None
            return redirect(next_page or url_for("dashboard.page"))

        except AuthError:
            log_event(logger, logging.WARNING, "login_failed", AUTH,
                      "Login failed", email=email, ip=request.remote_addr)
            flash("Invalid email or password.", "danger")

    return render_template("auth/login.html")


@auth_bp.route("/register", methods=["GET"])
@limiter.limit("10 per hour")
def register_form():
    """Display the registration form."""
    # Registration toggle: return 404 (not 403) to avoid confirming
    # the endpoint exists when disabled.  See audit finding H6.
    if not current_app.config["REGISTRATION_ENABLED"]:
        abort(404)
    if current_user.is_authenticated:
        companion_id = ref_cache.role_id(RoleEnum.COMPANION)
        if current_user.role_id == companion_id:
            return redirect(url_for("companion.index"))
        return redirect(url_for("dashboard.page"))
    return render_template("auth/register.html")


@auth_bp.route("/register", methods=["POST"])
@limiter.limit("3 per hour")
def register():
    """Process a new user registration.

    Rate-limited to 3 per hour to prevent automated mass account
    creation.  See audit finding H5.
    """
    if not current_app.config["REGISTRATION_ENABLED"]:
        abort(404)
    if current_user.is_authenticated:
        companion_id = ref_cache.role_id(RoleEnum.COMPANION)
        if current_user.role_id == companion_id:
            return redirect(url_for("companion.index"))
        return redirect(url_for("dashboard.page"))

    email = request.form.get("email", "")
    display_name = request.form.get("display_name", "")
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")

    if password != confirm_password:
        flash("Password and confirmation do not match.", "danger")
        return render_template("auth/register.html")

    try:
        auth_service.register_user(email, password, display_name)
        db.session.commit()
        logger.info("action=user_registered email=%s", email)
        flash("Account created. Please sign in.", "success")
        return redirect(url_for("auth.login"))
    except ConflictError as e:
        flash(str(e), "danger")
        return render_template("auth/register.html")
    except ValidationError as e:
        flash(str(e), "danger")
        return render_template("auth/register.html")


@auth_bp.route("/logout", methods=["POST"])
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

    # POST -- verify the submitted code.
    totp_code = request.form.get("totp_code", "").strip()
    backup_code = request.form.get("backup_code", "").strip()

    user = db.session.get(User, pending_user_id)
    if not user:
        # User was deleted between login steps -- clear pending state.
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
        # MFA was disabled between login steps -- clear pending state.
        flask_session.pop("_mfa_pending_user_id", None)
        flask_session.pop("_mfa_pending_remember", None)
        flask_session.pop("_mfa_pending_next", None)
        return redirect(url_for("auth.login"))

    try:
        secret = mfa_service.decrypt_secret(mfa_config.totp_secret_encrypted)
    except (RuntimeError, InvalidToken):
        # Key is missing or has changed since MFA was enabled.
        # Clear pending MFA state so the user is not stuck in a loop.
        flask_session.pop("_mfa_pending_user_id", None)
        flask_session.pop("_mfa_pending_remember", None)
        flask_session.pop("_mfa_pending_next", None)
        flash(
            "MFA verification failed. The encryption key may have been "
            "changed or removed. Contact your administrator.",
            "danger",
        )
        return redirect(url_for("auth.login"))

    valid = False

    if totp_code:
        # Verify the 6-digit TOTP code from an authenticator app.
        valid = mfa_service.verify_totp_code(secret, totp_code)
    elif backup_code:
        # Verify the backup code against stored hashes. Codes generated
        # before the C-03 entropy upgrade are 8 hex chars; codes generated
        # after are 28 hex chars. Both lengths verify identically because
        # bcrypt is length-agnostic.
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

    # Login completion -- both password and TOTP/backup code are verified.
    remember = flask_session.pop("_mfa_pending_remember", False)
    # Validate again at redirect time (defense in depth -- the value was
    # also validated at storage time in the login route).
    next_page = flask_session.pop("_mfa_pending_next", None)
    if not _is_safe_redirect(next_page):
        next_page = None
    flask_session.pop("_mfa_pending_user_id", None)

    login_user(user, remember=remember)
    flask_session["_session_created_at"] = datetime.now(timezone.utc).isoformat()
    log_event(logger, logging.INFO, "mfa_login_success", AUTH,
              "MFA login succeeded", user_id=user.id)

    # Companions always go to companion.index (ignore next_page).
    companion_id = ref_cache.role_id(RoleEnum.COMPANION)
    if user.role_id == companion_id:
        return redirect(url_for("companion.index"))

    return redirect(next_page or url_for("dashboard.page"))


@auth_bp.route("/mfa/setup", methods=["GET"])
@login_required
def mfa_setup():
    """Display the MFA setup page with QR code and manual key.

    Generates a fresh TOTP secret, encrypts it under the application's
    Fernet/MultiFernet key, and persists the ciphertext on the user's
    ``MfaConfig`` row in ``pending_secret_encrypted`` (paired with a
    15-minute expiry in ``pending_secret_expires_at``) instead of in
    the user's signed-but-unencrypted Flask session cookie.  See audit
    finding F-031 / commit C-05.

    Each visit overwrites any previous pending secret -- legitimate
    re-visits after invalid-code retries get a new QR code, and a
    stale pending row from an abandoned setup is replaced rather than
    accumulated.

    If MFA is already enabled the user is redirected back to security
    settings.  If the encryption key is missing the user is redirected
    with a flash explaining the operator-side prerequisite.
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
    # Encrypt before any DB mutation so a missing TOTP_ENCRYPTION_KEY
    # leaves the database state untouched -- no orphan pending row, no
    # half-initialized MfaConfig.  encrypt_secret() raises RuntimeError
    # when the key is unset (see app/services/mfa_service.py:_build_fernet_list).
    try:
        encrypted_pending = mfa_service.encrypt_secret(secret)
    except RuntimeError:
        flash(
            "MFA is not available. The server administrator must set "
            "TOTP_ENCRYPTION_KEY before MFA can be enabled.",
            "danger",
        )
        return redirect(url_for("settings.show", section="security"))

    if mfa_config is None:
        mfa_config = MfaConfig(user_id=current_user.id)
        db.session.add(mfa_config)

    mfa_config.pending_secret_encrypted = encrypted_pending
    mfa_config.pending_secret_expires_at = (
        datetime.now(timezone.utc) + MFA_SETUP_PENDING_TTL
    )
    db.session.commit()

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

    Reads the encrypted pending secret persisted by ``/mfa/setup``
    (commit C-05 moved this off the Flask session cookie), checks that
    it has not expired, decrypts it, and verifies the submitted code.
    On success the pending secret is re-encrypted under the current
    primary key and promoted to ``totp_secret_encrypted``; the pending
    columns are cleared in the same commit so a replay of the
    confirmation form cannot re-enrol a now-stale device.

    Failure modes handled here:

      * No ``MfaConfig`` row, or one without a pending secret -- the
        user has not started setup or the previous setup has been
        consumed/cleared.  Redirect to /mfa/setup with a flash.
      * Pending expiry has elapsed -- reject and clear the pending
        columns so the next /mfa/setup call starts cleanly.
      * Encryption key is missing or the pending ciphertext does not
        decrypt under any configured key -- redirect with a flash that
        names the operator-side fix.
      * Invalid TOTP code -- flash and redirect; pending state is
        retained until expiry so a typo does not force a re-scan.
    """
    mfa_config = (
        db.session.query(MfaConfig)
        .filter_by(user_id=current_user.id)
        .first()
    )
    # Single guard for "no usable pending secret".  Covers three cases
    # that share the same user-facing recovery (start over): no row, a
    # row with no pending columns set, or a row whose expiry has
    # elapsed.  When the row had stale data we clear it in the same
    # commit so a request that races a fresh /mfa/setup GET does not
    # see the same expired ciphertext twice.
    has_pending = (
        mfa_config is not None
        and mfa_config.pending_secret_encrypted is not None
        and mfa_config.pending_secret_expires_at is not None
    )
    if not has_pending or (
            mfa_config.pending_secret_expires_at
            < datetime.now(timezone.utc)
    ):
        if has_pending:
            mfa_config.pending_secret_encrypted = None
            mfa_config.pending_secret_expires_at = None
            db.session.commit()
        flash("MFA setup session expired. Please start again.", "danger")
        return redirect(url_for("auth.mfa_setup"))

    try:
        secret = mfa_service.decrypt_secret(mfa_config.pending_secret_encrypted)
    except RuntimeError:
        # TOTP_ENCRYPTION_KEY is unset.  Sending the user back to
        # /mfa/setup would only loop them through the same failure
        # (encrypt_secret would raise RuntimeError too), so clear the
        # pending state and bounce to the security settings page where
        # the user can see the status and wait for the operator-side
        # fix.  Mirrors the recovery path used in /mfa/disable.
        mfa_config.pending_secret_encrypted = None
        mfa_config.pending_secret_expires_at = None
        db.session.commit()
        flash(
            "MFA is not available. The server administrator must set "
            "TOTP_ENCRYPTION_KEY before MFA can be enabled.",
            "danger",
        )
        return redirect(url_for("settings.show", section="security"))
    except InvalidToken:
        # The pending ciphertext is unreadable under the current Fernet
        # key list -- typically because the key it was written under has
        # been pruned from TOTP_ENCRYPTION_KEY_OLD between /mfa/setup
        # and /mfa/confirm.  /mfa/setup itself still works (the primary
        # key is present), so direct the user there to start over.
        mfa_config.pending_secret_encrypted = None
        mfa_config.pending_secret_expires_at = None
        db.session.commit()
        flash(
            "MFA setup could not be verified. Please start again.",
            "danger",
        )
        return redirect(url_for("auth.mfa_setup"))

    totp_code = request.form.get("totp_code", "")
    if not mfa_service.verify_totp_code(secret, totp_code):
        # Pending state is preserved -- the user may retry until the
        # 15-minute expiry without re-scanning the QR code.
        flash("Invalid code. Please try again.", "danger")
        return redirect(url_for("auth.mfa_setup"))

    # Re-encrypt under the current primary key rather than copying the
    # bytes verbatim from the pending column.  If TOTP_ENCRYPTION_KEY
    # rotated during the setup window the pending ciphertext could
    # decrypt under a retired key only; promoting that ciphertext as-is
    # would leave the active credential dependent on a key the operator
    # is about to remove from TOTP_ENCRYPTION_KEY_OLD.  Re-encrypt
    # binds the active record to the current primary every time.
    try:
        mfa_config.totp_secret_encrypted = mfa_service.encrypt_secret(secret)
    except RuntimeError:
        # decrypt_secret() succeeded above so MultiFernet was usable a
        # moment ago.  This branch is only hit if TOTP_ENCRYPTION_KEY
        # was unset between the two calls -- defensive, not expected.
        flash(
            "MFA is not available. The server administrator must set "
            "TOTP_ENCRYPTION_KEY before MFA can be enabled.",
            "danger",
        )
        return redirect(url_for("settings.show", section="security"))

    mfa_config.pending_secret_encrypted = None
    mfa_config.pending_secret_expires_at = None
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

    try:
        secret = mfa_service.decrypt_secret(mfa_config.totp_secret_encrypted)
    except (RuntimeError, InvalidToken):
        flash(
            "MFA could not be verified because the encryption key has "
            "changed or been removed. Contact your administrator.",
            "danger",
        )
        return redirect(url_for("settings.show", section="security"))

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
