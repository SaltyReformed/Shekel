"""
Shekel Budget App -- Auth Routes

Handles login, registration, logout, MFA setup/verify/disable, password
change, session invalidation, and step-up re-authentication
(``/reauth``).  All these routes share a single blueprint because they
all mutate the same authenticated-session state and the helper
constants (``_MFA_PENDING_KEYS``, ``_MFA_PENDING_MAX_AGE``,
``MFA_SETUP_PENDING_TTL``) are co-owned by multiple routes; splitting
the file along route lines would force cross-module imports of those
constants and lose the local audit-rationale comments.
"""
# Pylint module-size waiver: the auth blueprint legitimately spans more
# than 1000 lines because of the dense per-route audit rationale
# (every branch carries a F-NNN finding reference and a security-
# relevant explanation, all of which would lose context if split into
# separate modules).  Splitting /reauth into its own module would also
# lose the shared imports of stamp_login_session / stamp_reauth_session
# / stamp_session_refresh and require duplicating the
# _verify_totp_with_replay_logging helper (which is the single point
# of truth for replay-rejection logging across login, MFA verify, MFA
# disable, AND reauth).  See commit C-10.
# pylint: disable=too-many-lines

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
from app.utils.session_helpers import (
    invalidate_other_sessions,
    stamp_login_session,
    stamp_reauth_session,
    stamp_session_refresh,
)

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

# Maximum time between the password POST that establishes pending MFA
# state on /login and the verification POST on /mfa/verify that
# consumes it.  Bounds the cookie-replay window for an attacker who
# captured a session cookie immediately after the victim's password
# step but before the MFA step.  Five minutes is long enough for a
# real user to reach for their phone and type a code, short enough
# that a stolen cookie is uninteresting -- the TOTP code itself
# rotates every 30 seconds, so the only useful attack window is the
# one this constant closes.  See audit finding F-002 / commit C-08.
_MFA_PENDING_MAX_AGE = timedelta(minutes=5)

# Session-cookie keys used to carry MFA pending state across the two
# halves of the login flow (password POST -> /mfa/verify POST).
# Centralised here so the login route, the verify route, and the
# pending-state helpers below stay in sync; a typo on any one key
# would silently break the flow.
_MFA_PENDING_KEYS = (
    "_mfa_pending_user_id",
    "_mfa_pending_remember",
    "_mfa_pending_next",
    "_mfa_pending_at",
)


def _clear_mfa_pending_state():
    """Pop every MFA pending key from the request session.

    Used by every exit branch in :func:`mfa_verify` -- the timeout
    rejection, the missing-user branch, the disabled-MFA branch, the
    encryption-key-failure branch, and the successful-verification
    branch.  Centralising the cleanup means a future addition to
    ``_MFA_PENDING_KEYS`` is automatically picked up by every exit
    path; an inline ``flask_session.pop(...)`` block at each site
    would have to be updated in five places, with the usual
    miss-one-and-leak-state bug.

    Idempotent: safe to call when no pending state is present.
    """
    for key in _MFA_PENDING_KEYS:
        flask_session.pop(key, None)


def _verify_totp_with_replay_logging(mfa_config, code, user_id):
    """Verify a TOTP code, log replays, and commit on success.

    Wraps :func:`mfa_service.verify_totp_code` to keep the
    replay-rejection logging and the post-success commit out of the
    route's main flow -- :func:`mfa_verify` was over its
    ``too-many-branches`` budget after the C-09 changes added the
    REPLAY/ACCEPTED branches inline.  Pulling the three-way enum
    handling into a helper restores the route to a flat sequence of
    business steps and keeps each function within Pylint's limits.

    On REPLAY, emits the structured ``totp_replay_rejected`` event
    that F-142 requires.  The event includes ``user_id`` and
    ``ip`` so SOC tooling can correlate replay attempts to a
    specific account and source address.

    On ACCEPTED, commits the SQLAlchemy session so
    ``mfa_config.last_totp_timestep`` -- which the verifier mutated
    in place -- persists across requests.  Without this commit, a
    crash later in the route would leave the matched step
    un-recorded and replayable on a retry.

    Args:
        mfa_config: The user's :class:`~app.models.user.MfaConfig`
            row.  Mutated in place on ACCEPTED.
        code: The 6-digit TOTP code submitted by the user.
        user_id: The :attr:`User.id` whose pending login is being
            verified.  Carried into the replay-rejected log event.

    Returns:
        bool: True if the code was ACCEPTED.  False if it was
            REPLAY or INVALID.

    Raises:
        cryptography.fernet.InvalidToken: If the encrypted secret
            cannot be decrypted under any current Fernet key.  Caller
            handles via the encryption-key-failure redirect.
        RuntimeError: If ``TOTP_ENCRYPTION_KEY`` is unset.  Same
            handling.
    """
    result = mfa_service.verify_totp_code(mfa_config, code)
    if result is mfa_service.TotpVerificationResult.REPLAY:
        log_event(
            logger, logging.WARNING, "totp_replay_rejected", AUTH,
            "TOTP replay attempt rejected",
            user_id=user_id, ip=request.remote_addr,
        )
    if result is mfa_service.TotpVerificationResult.ACCEPTED:
        db.session.commit()
        return True
    return False


def _consume_backup_code(mfa_config, plaintext):
    """Verify a backup code against stored hashes and consume on match.

    Single-use backup codes work as a one-time bypass for the TOTP
    requirement.  This helper handles the verify-and-remove pair as
    one operation so :func:`mfa_verify` does not have to inline the
    list-rebuild and commit (one less branch on its R0912 budget,
    and the consume step lives next to the verify step that
    motivates it).

    Args:
        mfa_config: The user's :class:`~app.models.user.MfaConfig`
            row; ``backup_codes`` is mutated in place on a match
            and the change is committed.
        plaintext: User-submitted backup code in plaintext.  Length-
            agnostic: pre-C-03 codes (8 hex chars) and post-C-03
            codes (28 hex chars) verify identically because bcrypt
            ignores length below the 72-byte input cap.

    Returns:
        True if ``plaintext`` matched a stored hash and was consumed.
        False if no hash matched (no DB change in that case).
    """
    idx = mfa_service.verify_backup_code(plaintext, mfa_config.backup_codes)
    if idx < 0:
        return False
    mfa_config.backup_codes = [
        h for i, h in enumerate(mfa_config.backup_codes) if i != idx
    ]
    db.session.commit()
    return True


def _mfa_pending_is_fresh():
    """Return True iff the session's ``_mfa_pending_at`` is recent.

    Reads ``flask_session["_mfa_pending_at"]`` (an ISO-8601 string
    written by /login when MFA is required) and compares it against
    ``datetime.now(timezone.utc)``.  Returns False, NEVER raising,
    for any of the following adversarial conditions:

      * No ``_mfa_pending_at`` key in the session -- pending state
        was constructed without a timestamp (pre-C-08 cookie still
        in flight, or a tampered/forged cookie missing the field).
      * The value is not parseable as ISO-8601 -- malformed cookie.
      * The parsed datetime is naive (no timezone) -- ill-formed
        cookie that would otherwise raise on the comparison.
      * The pending timestamp is in the future -- clock skew or
        tampered cookie; treat as invalid rather than honouring an
        impossible state.
      * The age exceeds ``_MFA_PENDING_MAX_AGE`` -- the legitimate
        case the constant exists to enforce.

    Fail-closed across the board.  The cost of a false rejection is
    one extra password retry by the user; the cost of a false
    acceptance is the entire F-002 finding (a stolen cookie can
    complete login weeks after the password step).
    """
    raw = flask_session.get("_mfa_pending_at")
    if not raw:
        return False
    try:
        pending_at = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return False
    if pending_at.tzinfo is None:
        # A naive datetime would raise on the timezone-aware
        # subtraction below.  Reject explicitly so the failure mode
        # is "expired pending state" (UX redirect to /login) rather
        # than "500 from the auth route".
        return False
    elapsed = datetime.now(timezone.utc) - pending_at
    if elapsed < timedelta(0):
        # Future-dated pending state -- only reachable via a tampered
        # cookie (the SECRET_KEY signature would have to be forged)
        # or a backwards clock jump on the server.  Either way the
        # value cannot be trusted.
        return False
    return elapsed <= _MFA_PENDING_MAX_AGE


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
        # Invalidate all other sessions after password change.  A
        # single ``now`` value flows into both the DB column and
        # every cookie key so the strict-less-than comparison in
        # ``load_user`` accepts this session and rejects every
        # parallel one.  ``stamp_login_session`` ALSO refreshes
        # ``_fresh_login_at``: the user just typed their current
        # password, so this counts as a step-up re-auth and the
        # five-minute fresh-login window restarts here.
        now = datetime.now(timezone.utc)
        current_user.session_invalidated_at = now
        db.session.commit()
        stamp_login_session(now)
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
    reject any session created before this timestamp.  The current
    session is refreshed via :func:`stamp_session_refresh` so that
    ``_session_created_at`` advances past the new invalidation point
    (and survives the loader's strict-less-than check).
    ``_fresh_login_at`` is deliberately left untouched: the user
    pressed a "log everyone else out" button without re-typing a
    password, so the step-up re-auth window must NOT silently
    extend.
    """
    now = datetime.now(timezone.utc)
    current_user.session_invalidated_at = now
    db.session.commit()
    stamp_session_refresh(now)
    log_event(logger, logging.INFO, "sessions_invalidated", AUTH,
              "All sessions invalidated", user_id=current_user.id)
    flash("All other sessions have been logged out.", "success")
    return redirect(url_for("settings.show", section="security"))


@auth_bp.route("/reauth", methods=["GET", "POST"])
@login_required
@limiter.limit("5 per 15 minutes", methods=["POST"])
def reauth():
    """Step-up re-authentication for high-value operations.

    Users land here when ``fresh_login_required`` (commit C-10 /
    F-045) determines that their last password verification is older
    than ``FRESH_LOGIN_MAX_AGE_MINUTES``.  Successful POST stamps
    ``_fresh_login_at`` to ``now`` via
    :func:`~app.utils.session_helpers.stamp_reauth_session` and
    redirects to the validated ``next`` URL.

    Verification mirrors the /login flow:

      * Password is verified through ``auth_service.verify_password``
        -- the same bcrypt path used at primary login.
      * If the user has MFA enabled, a TOTP code is required.  The
        same ``_verify_totp_with_replay_logging`` helper used by
        /mfa/verify and /mfa/disable enforces replay prevention --
        a code captured at primary login cannot be reused here, and
        vice versa, because the helper bumps and persists
        ``mfa_config.last_totp_timestep`` on every ACCEPTED match.
      * Backup codes are intentionally NOT accepted here.  They are
        single-use and reserved for the "I lost my authenticator"
        scenario at primary login.  Spending one on a step-up
        prompt would burn a recovery credential for the wrong
        purpose; the user can /logout and /login with a backup code
        if their device is unavailable.
      * Decryption errors (missing TOTP_ENCRYPTION_KEY, ciphertext
        unreadable under any current Fernet) flash an
        operator-actionable message and re-render the form, mirroring
        /mfa/verify and /mfa/disable.

    The ``next`` parameter is validated through ``_is_safe_redirect``
    on every read (storage time and post-success redirect) so an
    attacker cannot stage an open-redirect via the step-up flow.
    For most decorated routes the original action URL is a POST or
    PATCH endpoint, so the redirect-back GET will return 405 -- the
    user is expected to re-issue their action manually.  The
    fall-through to /dashboard handles the no-next case (direct
    navigation to /reauth).
    """
    next_url = request.args.get("next")
    safe_next = next_url if _is_safe_redirect(next_url) else None

    if request.method == "GET":
        return render_template("auth/reauth.html", next=safe_next)

    password = request.form.get("password", "")
    if not auth_service.verify_password(password, current_user.password_hash):
        log_event(
            logger, logging.WARNING, "reauth_failed", AUTH,
            "Step-up re-auth failed: bad password",
            user_id=current_user.id, ip=request.remote_addr,
        )
        flash("Invalid password.", "danger")
        return render_template("auth/reauth.html", next=safe_next)

    mfa_config = (
        db.session.query(MfaConfig)
        .filter_by(user_id=current_user.id, is_enabled=True)
        .first()
    )
    if mfa_config:
        totp_code = request.form.get("totp_code", "").strip()
        try:
            accepted = _verify_totp_with_replay_logging(
                mfa_config, totp_code, current_user.id,
            )
        except (RuntimeError, InvalidToken):
            flash(
                "MFA could not be verified because the encryption key "
                "has changed or been removed. Contact your "
                "administrator.",
                "danger",
            )
            return render_template("auth/reauth.html", next=safe_next)
        if not accepted:
            log_event(
                logger, logging.WARNING, "reauth_failed", AUTH,
                "Step-up re-auth failed: bad TOTP",
                user_id=current_user.id, ip=request.remote_addr,
            )
            flash("Invalid authentication code.", "danger")
            return render_template("auth/reauth.html", next=safe_next)

    stamp_reauth_session(datetime.now(timezone.utc))
    log_event(
        logger, logging.INFO, "reauth_success", AUTH,
        "Step-up re-auth succeeded", user_id=current_user.id,
    )
    if safe_next:
        return redirect(safe_next)
    return redirect(url_for("dashboard.page"))


@auth_bp.route("/mfa/verify", methods=["GET", "POST"])
@limiter.limit("5 per 15 minutes", methods=["POST"])
def mfa_verify():  # pylint: disable=too-many-return-statements
    """Display the MFA verification form and handle code submission.

    Requires a pending MFA user_id in the session (set by the login
    route after successful password verification). Completes the login
    on valid TOTP or backup code.

    Pending state freshness is enforced on every entry to this view
    (both GET and POST) via :func:`_mfa_pending_is_fresh`.  A pending
    state older than ``_MFA_PENDING_MAX_AGE`` is cleared and the user
    is bounced back to /login with a flash message -- the F-002
    cookie-replay window closes at exactly this gate.

    Pylint note: ``too-many-return-statements`` is suppressed because
    each early return is a distinct semantic exit (no pending state,
    stale state, GET render, missing user-or-config, key failure,
    invalid code, companion success, owner success).  Consolidating
    these into one return path would force a single flash/redirect
    template that hides the per-mode messaging; the explicit returns
    are the readable form.
    """
    pending_user_id = flask_session.get("_mfa_pending_user_id")
    if not pending_user_id:
        # No pending state at all -- silently redirect.  This is the
        # normal case for a user who navigated directly to /mfa/verify
        # without completing the password step; no flash required.
        return redirect(url_for("auth.login"))

    if not _mfa_pending_is_fresh():
        # Pending state exists but is stale, malformed, or future-dated
        # (see _mfa_pending_is_fresh for the full failure list).  Clear
        # every pending key so a fresh /login starts from a clean slate
        # and surface a user-visible reason for the bounce -- without
        # the flash, a user who paused on the verify page for >5 min
        # would see only an unexplained redirect to /login.  F-002.
        _clear_mfa_pending_state()
        flash(
            "Two-factor authentication timed out. Please log in again.",
            "warning",
        )
        return redirect(url_for("auth.login"))

    if request.method == "GET":
        return render_template("auth/mfa_verify.html")

    # POST -- verify the submitted code.
    totp_code = request.form.get("totp_code", "").strip()
    backup_code = request.form.get("backup_code", "").strip()

    user = db.session.get(User, pending_user_id)
    mfa_config = None
    if user:
        mfa_config = (
            db.session.query(MfaConfig)
            .filter_by(user_id=user.id, is_enabled=True)
            .first()
        )
    if not user or not mfa_config:
        # Either the user was deleted between login steps or MFA was
        # disabled out from under the pending session.  Both are race
        # conditions that resolve the same way: clear pending state
        # and silently bounce to /login.  No flash because a fresh
        # password entry will explain itself (invalid credentials if
        # the user is gone, no MFA prompt if MFA is disabled).
        _clear_mfa_pending_state()
        return redirect(url_for("auth.login"))

    valid = False
    used_backup_code = False

    # Single try/except wraps both the up-front decrypt (preserving
    # the pre-C-09 fail-fast behaviour: a missing encryption key
    # disables BOTH the TOTP and backup-code paths) and the TOTP
    # verifier (which decrypts again internally on the TOTP path).
    # Any of three failure modes -- decrypt-now error, verifier
    # decrypt error, or impossible-but-defensive key swap mid-flow --
    # collapses into the same operator-side error message and
    # redirect.
    try:
        mfa_service.decrypt_secret(mfa_config.totp_secret_encrypted)
        if totp_code:
            valid = _verify_totp_with_replay_logging(
                mfa_config, totp_code, user.id,
            )
        elif backup_code:
            # Backup-code path delegates to _consume_backup_code so
            # the verify+remove+commit sequence stays atomic and is
            # not duplicated at any other call site.  Length-
            # agnostic: pre-C-03 (8 hex) and post-C-03 (28 hex) codes
            # verify identically through bcrypt.
            used_backup_code = _consume_backup_code(mfa_config, backup_code)
            valid = used_backup_code
    except (RuntimeError, InvalidToken):
        _clear_mfa_pending_state()
        flash(
            "MFA verification failed. The encryption key may have been "
            "changed or removed. Contact your administrator.",
            "danger",
        )
        return redirect(url_for("auth.login"))

    if not valid:
        flash("Invalid verification code.", "danger")
        return render_template("auth/mfa_verify.html")

    # Login completion -- both password and TOTP/backup code are verified.
    # Read the values we still need from pending state before clearing.
    remember = flask_session.get("_mfa_pending_remember", False)
    # Validate again at redirect time (defense in depth -- the value was
    # also validated at storage time in the login route).
    next_page = flask_session.get("_mfa_pending_next")
    if not _is_safe_redirect(next_page):
        next_page = None

    _clear_mfa_pending_state()

    login_user(user, remember=remember)
    # Stamp every lifecycle key in one call so the idle-timeout check
    # (commit C-10 / F-006) and the fresh-login check (commit C-10 /
    # F-045) start from the same instant the MFA-protected session
    # was completed.  The backup-code branch below ALSO calls
    # ``invalidate_other_sessions``, which writes ``_session_created_at``
    # again with its own internal ``now`` -- the small microsecond
    # drift is harmless because the load_user check is strict less-
    # than, not equality, and the cookie's later value is still > the
    # DB's earlier session_invalidated_at.
    stamp_login_session(datetime.now(timezone.utc))

    if used_backup_code:
        # Backup-code consumption is the canonical "I lost my
        # authenticator" signal -- the credential the user just spent
        # exists precisely because the original device is unavailable
        # or compromised.  Force every other live session for this
        # user to re-authenticate so an attacker who has the same
        # password+device can no longer ride along.  F-003.
        #
        # Called after login_user / _session_created_at so the helper
        # refreshes the cookie (with its single ``now``) AFTER
        # Flask-Login has populated the session, ensuring the
        # current request survives the bump.
        invalidate_other_sessions(user, "backup_code_consumed")

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
    matched_step = mfa_service.verify_totp_setup_code(secret, totp_code)
    if matched_step is None:
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
    # Seed replay-prevention state from the confirming code's step so
    # the same code cannot be replayed at /mfa/verify in the seconds
    # immediately after enrolment.  Without this seed the first
    # ~30 seconds of an MFA-protected account would still be vulnerable
    # to the F-005 attack against an attacker who observed the confirm
    # POST.  See commit C-09.
    mfa_config.last_totp_timestep = matched_step

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

    Clears the TOTP secret, backup codes, the recorded replay-prevention
    step, and sets is_enabled to False.  The TOTP step is verified
    through :func:`mfa_service.verify_totp_code` with replay prevention
    enforced -- an attacker who has captured the user's password and a
    recently-used TOTP code cannot use that code to disable MFA, the
    same defence that ``/mfa/verify`` provides at login.
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

    # Same helper as /mfa/verify: emits ``totp_replay_rejected`` on
    # REPLAY and commits ``last_totp_timestep`` on ACCEPTED so a code
    # captured at /mfa/verify cannot be reused at /mfa/disable (and
    # vice versa).  Re-raises decrypt errors so the operator-side
    # message wins over the generic invalid-code flash.
    try:
        accepted = _verify_totp_with_replay_logging(
            mfa_config, totp_code, current_user.id,
        )
    except (RuntimeError, InvalidToken):
        flash(
            "MFA could not be verified because the encryption key has "
            "changed or been removed. Contact your administrator.",
            "danger",
        )
        return redirect(url_for("settings.show", section="security"))

    if not accepted:
        flash("Invalid authentication code.", "danger")
        return redirect(url_for("auth.mfa_disable"))

    # Clear all MFA fields.  ``last_totp_timestep`` is reset to NULL
    # so that a re-enrollment under a fresh secret does not inherit
    # the step boundary recorded against the now-cleared secret -- the
    # two values are unrelated and a stale boundary on the new secret
    # would be a UX bug (every new code might be rejected as a replay
    # of the old secret's last step).
    mfa_config.totp_secret_encrypted = None
    mfa_config.is_enabled = False
    mfa_config.backup_codes = None
    mfa_config.confirmed_at = None
    mfa_config.last_totp_timestep = None
    db.session.commit()

    # MFA disable is a security-relevant state change: a user who
    # disables MFA because they suspect a session is compromised has
    # done nothing about that session yet.  Force every other live
    # session to re-authenticate so the disable cannot be silently
    # exploited by an attacker who already holds a valid cookie.
    # Called AFTER the MFA-clear commit so a transient DB error in
    # the helper does not leave MFA-disabled rows tied to a
    # non-bumped session_invalidated_at.  F-032.
    invalidate_other_sessions(current_user, "mfa_disabled")

    log_event(logger, logging.INFO, "mfa_disabled", AUTH,
              "MFA disabled", user_id=current_user.id)
    flash("Two-factor authentication has been disabled.", "success")
    return redirect(url_for("settings.show", section="security"))
