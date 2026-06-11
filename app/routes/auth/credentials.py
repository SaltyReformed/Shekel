"""
Shekel Budget App -- Auth route package: primary credentials.

The password-credential lifecycle: login (including the handoff into
the MFA pending state that :mod:`~app.routes.auth.mfa` consumes),
registration, and logout.  Each route registers against the shared
``auth_bp`` from :mod:`~app.routes.auth._bp`; the open-redirect guard
and form-error flattener come from
:mod:`~app.routes.auth._helpers`.
"""

import logging
from datetime import datetime, timezone

from flask import (
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session as flask_session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from marshmallow import ValidationError as MarshmallowValidationError

from app import ref_cache
from app.enums import RoleEnum
from app.extensions import db, limiter
from app.models.user import MfaConfig
from app.schemas.validation import LoginSchema, RegisterSchema
from app.services import auth_service
from app.exceptions import AuthError, ConflictError, ValidationError
from app.routes.auth._bp import auth_bp
from app.routes.auth._helpers import (
    _first_validation_message,
    _is_safe_redirect,
)
from app.utils.log_events import (
    AUTH,
    EVT_LOGIN_FAILED,
    EVT_LOGIN_SUCCESS,
    EVT_LOGOUT,
    EVT_USER_REGISTERED,
    log_event,
)
from app.utils.session_helpers import stamp_login_session

logger = logging.getLogger(__name__)


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per 15 minutes", methods=["POST"])
def login():
    """Display the login form and handle authentication.

    Design note: each early return is a distinct semantic exit
    (companion-already-logged-in, owner-already-logged-in, MFA-pending
    redirect, companion-success redirect, owner-success redirect,
    generic failure render).  Consolidating these into one return path
    would force a state-machine in the function body that hides
    per-mode behaviour; the explicit returns are the readable form.
    """
    # Already logged in -- redirect to the appropriate landing page.
    if current_user.is_authenticated:
        companion_id = ref_cache.role_id(RoleEnum.COMPANION)
        if current_user.role_id == companion_id:
            return redirect(url_for("companion.index"))
        return redirect(url_for("dashboard.page"))

    if request.method == "POST":
        # Schema-level validation runs before bcrypt is invoked so a
        # malformed or DoS-sized payload (F-163: megabyte-sized
        # password) is rejected at the route boundary.
        # ``MarshmallowValidationError`` and ``AuthError`` collapse to
        # the same generic "Invalid email or password." flash so an
        # attacker cannot distinguish schema-rejection from auth-
        # rejection by response wording.  See commit C-26.
        email = ""
        try:
            login_data = LoginSchema().load(request.form)
            email = login_data["email"]
            password = login_data["password"]
            remember = login_data["remember"]
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
                # Stamp the pending state with a timestamp so /mfa/verify
                # can reject cookies older than _MFA_PENDING_MAX_AGE.
                # Without this, a captured session cookie remains a valid
                # MFA pending state for the entire 31-day default Flask
                # session lifetime -- audit finding F-002.
                flask_session["_mfa_pending_at"] = (
                    datetime.now(timezone.utc).isoformat()
                )
                return redirect(url_for("auth.mfa_verify"))

            # No MFA -- complete login immediately.  Stamp all three
            # session lifecycle timestamps in one call so the
            # idle-timeout check (commit C-10 / F-006) and the
            # fresh-login check (commit C-10 / F-045) start from the
            # same instant the session was created.
            login_user(user, remember=remember)
            stamp_login_session(datetime.now(timezone.utc))
            log_event(logger, logging.INFO, EVT_LOGIN_SUCCESS, AUTH,
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

        except MarshmallowValidationError:
            # F-163 / F-041: schema layer rejected payload shape or
            # length; treated as a failed login attempt for forensic
            # logging purposes (rate-limited like any other failure).
            log_event(
                logger, logging.WARNING, EVT_LOGIN_FAILED, AUTH,
                "Login failed: schema validation",
                ip=request.remote_addr,
            )
            flash("Invalid email or password.", "danger")
        except AuthError:
            log_event(logger, logging.WARNING, EVT_LOGIN_FAILED, AUTH,
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

    # Schema-level validation enforces email shape, display-name
    # presence, password length (12-72 chars), bcrypt's 72-byte UTF-8
    # cap, and the password/confirm-match rule.  ``auth_service.
    # register_user`` re-validates email/length/byte rules so the
    # service remains correct even when called outside the route
    # layer (e.g. companion creation).  See commit C-26.
    try:
        register_data = RegisterSchema().load(request.form)
    except MarshmallowValidationError as exc:
        # Surface the first message for the first error field so the
        # existing user-facing error strings ("Invalid email format.",
        # "Display name is required.", "Password must be at least 12
        # characters.", "Password and confirmation do not match.")
        # remain stable.  ``_first_validation_message`` flattens
        # Marshmallow's nested dict to a single human-readable line.
        flash(_first_validation_message(exc), "danger")
        return render_template("auth/register.html")

    email = register_data["email"]
    display_name = register_data["display_name"]
    password = register_data["password"]

    try:
        # Capture the user so the structured ``user_registered`` event
        # can include the assigned user_id.  ``register_user`` already
        # returned the user (audit-finding F-085 / commit C-14
        # required no signature change here -- the route was simply
        # discarding the value).
        user = auth_service.register_user(email, password, display_name)
        db.session.commit()
        log_event(
            logger, logging.INFO, EVT_USER_REGISTERED, AUTH,
            "User registered",
            user_id=user.id, email=email,
        )
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
    log_event(logger, logging.INFO, EVT_LOGOUT, AUTH,
              "User logged out", user_id=current_user.id)
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))
