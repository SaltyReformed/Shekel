"""
Shekel Budget App -- Auth Route Tests

Tests login, logout, route protection, disabled accounts, rate limiting,
password change, session management, and open redirect prevention.
"""

import re
from datetime import datetime, timedelta, timezone

from app import create_app
from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.user import MfaConfig, User, UserSettings
from app.models.scenario import Scenario
from app.routes.auth import _is_safe_redirect
from app.services import mfa_service
from app.services.auth_service import hash_password


class TestIsSafeRedirect:
    """Unit tests for the _is_safe_redirect() helper function.

    This function is the sole defense against open redirect attacks in
    the login and MFA flows.  Every known bypass technique must be
    covered here.
    """

    def test_none_returns_false(self):
        """None input must be rejected -- no redirect target was provided."""
        assert _is_safe_redirect(None) is False

    def test_empty_string_returns_false(self):
        """Empty string must be rejected -- equivalent to no target."""
        assert _is_safe_redirect("") is False

    def test_whitespace_only_returns_false(self):
        """Whitespace-only strings must be rejected after stripping."""
        assert _is_safe_redirect("   ") is False

    def test_relative_path_returns_true(self):
        """A simple relative path is the expected legitimate input."""
        assert _is_safe_redirect("/templates") is True

    def test_relative_path_with_query_returns_true(self):
        """Relative paths with query strings are legitimate."""
        assert _is_safe_redirect("/settings?section=security") is True

    def test_absolute_http_url_returns_false(self):
        """Absolute https:// URLs are the primary open redirect vector."""
        assert _is_safe_redirect("https://evil.com") is False

    def test_absolute_http_url_with_path_returns_false(self):
        """Absolute URLs with paths are used for phishing pages."""
        assert _is_safe_redirect("https://evil.com/phishing") is False

    def test_protocol_relative_url_returns_false(self):
        """Protocol-relative URLs (//host) bypass scheme-only checks."""
        assert _is_safe_redirect("//evil.com") is False

    def test_backslash_url_returns_false(self):
        """Backslash-prefixed URLs are normalized to // by some browsers."""
        assert _is_safe_redirect("\\evil.com") is False

    def test_javascript_scheme_returns_false(self):
        """javascript: scheme can execute arbitrary code in the browser."""
        assert _is_safe_redirect("javascript:alert(1)") is False

    def test_data_scheme_returns_false(self):
        """data: scheme can render attacker-controlled HTML content."""
        assert _is_safe_redirect("data:text/html,<h1>phish</h1>") is False

    def test_newline_injection_returns_false(self):
        """Embedded newlines can confuse URL parsers and inject headers."""
        assert _is_safe_redirect("/safe\nevil.com") is False

    def test_tab_injection_returns_false(self):
        """Embedded tabs can confuse URL parsers."""
        assert _is_safe_redirect("/safe\tevil.com") is False

    def test_carriage_return_injection_returns_false(self):
        """Embedded carriage returns can inject HTTP headers."""
        assert _is_safe_redirect("/safe\revil.com") is False

    def test_leading_whitespace_with_scheme_returns_false(self):
        """Leading whitespace before a scheme must still be caught."""
        assert _is_safe_redirect("  https://evil.com") is False

    def test_mixed_case_scheme_returns_false(self):
        """urlparse handles mixed-case schemes (HTTP://) correctly."""
        assert _is_safe_redirect("HTTP://evil.com") is False

    def test_ftp_scheme_returns_false(self):
        """Non-HTTP schemes like ftp:// must also be rejected."""
        assert _is_safe_redirect("ftp://evil.com/file") is False


class TestLogin:
    """Tests for the /login endpoint."""

    def test_login_page_renders(self, app, client):
        """GET /login returns the login form."""
        with app.app_context():
            response = client.get("/login")
            assert response.status_code == 200
            assert b"Sign In" in response.data

    def test_successful_login(self, app, client, seed_user):
        """POST /login with valid credentials redirects to grid."""
        with app.app_context():
            response = client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            }, follow_redirects=False)
            assert response.status_code == 302
            assert "/" in response.headers.get("Location", "")

    def test_failed_login(self, app, client, seed_user):
        """POST /login with wrong password shows error."""
        with app.app_context():
            response = client.post("/login", data={
                "email": "test@shekel.local",
                "password": "wrongpassword",
            }, follow_redirects=True)
            assert response.status_code == 200
            assert b"Invalid email or password" in response.data

    def test_protected_routes_redirect_to_login(self, app, client):
        """Unauthenticated requests to protected routes redirect to /login."""
        with app.app_context():
            response = client.get("/", follow_redirects=False)
            assert response.status_code == 302
            assert "login" in response.headers.get("Location", "")

    def test_login_disabled_account(self, app, client, seed_user):
        """POST /login with disabled account shows generic error message."""
        with app.app_context():
            # Disable the user account.
            user = db.session.get(User, seed_user["user"].id)
            user.is_active = False
            db.session.commit()

            response = client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            }, follow_redirects=True)

            assert response.status_code == 200
            # Route shows generic message (doesn't reveal account status).
            assert b"Invalid email or password" in response.data

    def test_rate_limiting_after_5_attempts(self, app, seed_user):
        """POST /login is rate-limited to 5 attempts per 15 minutes."""
        with app.app_context():
            # Create a fresh app with rate limiting enabled (TestConfig disables it).
            rate_app = create_app("testing")
            rate_app.config["RATELIMIT_ENABLED"] = True

            # Re-initialize limiter with rate limiting enabled.
            from app.extensions import limiter
            limiter.enabled = True
            limiter.init_app(rate_app)

            rate_client = rate_app.test_client()

            with rate_app.app_context():
                # Make 5 failed login attempts (within the limit).
                for _ in range(5):
                    rate_client.post("/login", data={
                        "email": "test@shekel.local",
                        "password": "wrongpassword",
                    })

                # 6th attempt should be rate-limited.
                response = rate_client.post("/login", data={
                    "email": "test@shekel.local",
                    "password": "wrongpassword",
                })
                assert response.status_code == 429

            # Clean up: dispose the secondary app's engine to release
            # connections, and reset limiter for other tests.
            with rate_app.app_context():
                from app.extensions import db as _db
                _db.engine.dispose()
            limiter.enabled = False

    def test_login_nonexistent_email(self, app, client):
        """POST /login with nonexistent email shows the same generic error.

        Anti-enumeration: same message as wrong password to prevent
        email discovery attacks. If the message differs, an attacker can
        determine which emails have accounts.
        """
        with app.app_context():
            response = client.post("/login", data={
                "email": "nobody@shekel.local",
                "password": "anything",
            }, follow_redirects=True)

            assert response.status_code == 200
            # Anti-enumeration: same message as wrong password.
            assert b"Invalid email or password" in response.data

    def test_login_missing_email_field(self, app, client):
        """POST /login with no email field returns generic error, not a crash.

        The route uses request.form.get('email', '') which defaults to
        empty string when the key is absent.
        """
        with app.app_context():
            response = client.post("/login", data={
                "password": "testpass",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Invalid email or password" in response.data
            assert b"Internal Server Error" not in response.data

    def test_login_missing_password_field(self, app, client, seed_user):
        """POST /login with no password field returns generic error, not a crash.

        The route uses request.form.get('password', '') which defaults to
        empty string when the key is absent.
        """
        with app.app_context():
            response = client.post("/login", data={
                "email": "test@shekel.local",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Invalid email or password" in response.data
            assert b"Internal Server Error" not in response.data

    def test_login_empty_both_fields(self, app, client):
        """POST /login with empty email and password returns generic error.

        Edge case: both fields present but empty strings. Catches any
        difference in handling between missing and empty fields.
        """
        with app.app_context():
            response = client.post("/login", data={
                "email": "",
                "password": "",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Invalid email or password" in response.data

    def test_login_xss_in_email_field(self, app, client):
        """POST /login with XSS payload in email does not render unescaped HTML.

        Verifies Jinja2 auto-escaping prevents script injection. The login
        template does not echo the submitted email, so the payload should
        not appear in the response at all.
        """
        with app.app_context():
            response = client.post("/login", data={
                "email": '<script>alert("xss")</script>',
                "password": "anything",
            }, follow_redirects=True)

            assert response.status_code == 200
            # The raw script tag must not appear unescaped in the response.
            assert b'<script>alert("xss")</script>' not in response.data
            assert b"Internal Server Error" not in response.data

    def test_login_xss_in_password_field(self, app, client, seed_user):
        """POST /login with XSS payload in password does not render unescaped HTML.

        Password fields are never echoed, but verify no injection path
        exists via error messages or logging output rendered in HTML.
        """
        with app.app_context():
            response = client.post("/login", data={
                "email": "test@shekel.local",
                "password": '<script>alert("xss")</script>',
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Invalid email or password" in response.data
            assert b'<script>alert("xss")</script>' not in response.data

    def test_open_redirect_absolute_url_blocked(self, app, client, seed_user):
        """An attacker-supplied absolute URL in the next parameter must be rejected.

        The user must land on the grid after login, not on the attacker's
        site.  This is the primary open redirect attack vector: an attacker
        crafts /login?next=https://evil.com/phishing and sends it to a victim.
        """
        with app.app_context():
            response = client.post("/login?next=https://evil.com", data={
                "email": "test@shekel.local",
                "password": "testpass",
            }, follow_redirects=False)

            assert response.status_code == 302
            location = response.headers.get("Location", "")
            # Must NOT redirect to the attacker's site.
            assert "evil.com" not in location
            # Must redirect to the dashboard (home page).
            assert "/dashboard" in location or location.endswith("/")

    def test_open_redirect_protocol_relative_blocked(self, app, client, seed_user):
        """Protocol-relative URLs (//host) are a common bypass for scheme-only checks.

        //evil.com is parsed by browsers as a protocol-relative URL, inheriting
        the current page's scheme.  Must be rejected.
        """
        with app.app_context():
            response = client.post("/login?next=//evil.com", data={
                "email": "test@shekel.local",
                "password": "testpass",
            }, follow_redirects=False)

            assert response.status_code == 302
            location = response.headers.get("Location", "")
            assert "evil.com" not in location
            assert "/dashboard" in location or location.endswith("/")

    def test_open_redirect_backslash_blocked(self, app, client, seed_user):
        """Backslash-prefixed URLs are normalized to protocol-relative by some browsers.

        \\evil.com may be treated as //evil.com by older IE and some WebKit
        builds.  Must be rejected.
        """
        with app.app_context():
            response = client.post("/login?next=\\evil.com", data={
                "email": "test@shekel.local",
                "password": "testpass",
            }, follow_redirects=False)

            assert response.status_code == 302
            location = response.headers.get("Location", "")
            assert "evil.com" not in location

    def test_open_redirect_javascript_scheme_blocked(self, app, client, seed_user):
        """javascript: scheme URLs can execute arbitrary code in the browser.

        Must be rejected to prevent XSS via redirect.
        """
        with app.app_context():
            response = client.post(
                "/login?next=javascript:alert(1)", data={
                    "email": "test@shekel.local",
                    "password": "testpass",
                }, follow_redirects=False)

            assert response.status_code == 302
            location = response.headers.get("Location", "")
            assert "javascript" not in location

    def test_open_redirect_data_scheme_blocked(self, app, client, seed_user):
        """data: scheme URLs can render attacker-controlled HTML content.

        Must be rejected to prevent phishing via redirect.
        """
        with app.app_context():
            response = client.post(
                "/login?next=data:text/html,<h1>phish</h1>", data={
                    "email": "test@shekel.local",
                    "password": "testpass",
                }, follow_redirects=False)

            assert response.status_code == 302
            location = response.headers.get("Location", "")
            assert "data:" not in location

    def test_safe_next_redirect_allowed(self, app, client, seed_user):
        """Legitimate relative paths in the next parameter must still work.

        Users should be redirected to the page they originally requested
        after logging in.
        """
        with app.app_context():
            response = client.post("/login?next=/templates", data={
                "email": "test@shekel.local",
                "password": "testpass",
            }, follow_redirects=False)

            assert response.status_code == 302
            location = response.headers.get("Location", "")
            # Must redirect to the requested page, not the default grid.
            assert "/templates" in location

    def test_safe_next_with_query_string_allowed(self, app, client, seed_user):
        """Relative paths with query strings are legitimate and must be preserved.

        Common case: Flask-Login's unauthorized_handler adds
        ?next=/settings%3Fsection%3Dsecurity to the login URL.
        """
        with app.app_context():
            response = client.post(
                "/login?next=/settings%3Fsection%3Dsecurity", data={
                    "email": "test@shekel.local",
                    "password": "testpass",
                }, follow_redirects=False)

            assert response.status_code == 302
            location = response.headers.get("Location", "")
            assert "/settings" in location

    def test_no_next_redirects_to_dashboard(self, app, client, seed_user):
        """When no next parameter is provided, the default redirect is the dashboard.

        This is the normal login flow -- no next parameter at all.
        """
        with app.app_context():
            response = client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            }, follow_redirects=False)

            assert response.status_code == 302
            location = response.headers.get("Location", "")
            # Default redirect is the dashboard (home page).
            assert "/dashboard" in location or location.endswith("/")

    def test_deactivated_user_cannot_access_protected_routes(
        self, app, auth_client, seed_user
    ):
        """An active session becomes invalid when the user is deactivated.

        Flask-Login's UserMixin.is_authenticated always returns True, so
        the user_loader must explicitly check is_active. Without this
        check, a deactivated user's existing session remains valid for
        the entire cookie lifetime (up to 30 days).
        """
        with app.app_context():
            # Verify the session is currently valid.
            response = auth_client.get("/", follow_redirects=False)
            assert response.status_code == 200

            # Deactivate the user mid-session.
            user = db.session.get(User, seed_user["user"].id)
            user.is_active = False
            db.session.commit()

            # Next request should be rejected -- user_loader returns None.
            response = auth_client.get("/", follow_redirects=False)
            assert response.status_code == 302
            assert "login" in response.headers.get("Location", "")

            # Attempting to log in again should also fail.
            response = auth_client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            }, follow_redirects=True)
            assert response.status_code == 200
            assert b"Invalid email or password" in response.data

    def test_reactivated_user_can_login_again(
        self, app, client, seed_user
    ):
        """Re-activating a deactivated user restores login access.

        After setting is_active = False (blocking login), setting it
        back to True should allow the user to log in with the same
        credentials.  This positive regression test ensures the
        is_active check is not a one-way door.
        """
        with app.app_context():
            # Deactivate the user.
            user = db.session.get(User, seed_user["user"].id)
            user.is_active = False
            db.session.commit()

            # Login must fail while deactivated.
            response = client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            }, follow_redirects=True)
            assert b"Invalid email or password" in response.data

            # Re-activate the user.
            user = db.session.get(User, seed_user["user"].id)
            user.is_active = True
            db.session.commit()

            # Login must succeed again.
            response = client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            }, follow_redirects=False)
            assert response.status_code == 302
            assert "login" not in response.headers.get("Location", "")


class TestLogout:
    """Tests for the /logout endpoint."""

    def test_logout_redirects_to_login(self, app, auth_client):
        """POST /logout ends session and redirects."""
        with app.app_context():
            response = auth_client.post("/logout", follow_redirects=False)
            assert response.status_code == 302
            assert "login" in response.headers.get("Location", "")

    def test_logout_rejects_get(self, app, seed_user, client):
        """GET /logout returns 405 Method Not Allowed for authenticated users."""
        with app.app_context():
            # Log in with a fresh client so the session is still active.
            client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })
            response = client.get("/logout")
            assert response.status_code == 405


class TestPasswordChange:
    """Tests for POST /change-password."""

    def test_change_password_success(self, app, auth_client, seed_user):
        """POST /change-password with valid data changes the password."""
        with app.app_context():
            response = auth_client.post("/change-password", data={
                "current_password": "testpass",
                "new_password": "newpassword12",
                "confirm_password": "newpassword12",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Password changed successfully" in response.data

            # Verify the user can log in with the new password.
            auth_client.post("/logout")
            login_resp = auth_client.post("/login", data={
                "email": "test@shekel.local",
                "password": "newpassword12",
            }, follow_redirects=False)
            assert login_resp.status_code == 302

    def test_change_password_wrong_current(self, app, auth_client, seed_user):
        """POST /change-password with wrong current password shows error."""
        with app.app_context():
            response = auth_client.post("/change-password", data={
                "current_password": "wrongpassword",
                "new_password": "newpassword12",
                "confirm_password": "newpassword12",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Current password is incorrect" in response.data

    def test_change_password_mismatch(self, app, auth_client, seed_user):
        """POST /change-password with mismatched new/confirm shows error."""
        with app.app_context():
            response = auth_client.post("/change-password", data={
                "current_password": "testpass",
                "new_password": "newpassword12",
                "confirm_password": "differentpass12",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"New password and confirmation do not match" in response.data

    def test_change_password_too_short(self, app, auth_client, seed_user):
        """POST /change-password with password under 12 chars shows error."""
        with app.app_context():
            response = auth_client.post("/change-password", data={
                "current_password": "testpass",
                "new_password": "short",
                "confirm_password": "short",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"at least 12 characters" in response.data

    def test_change_password_requires_login(self, app, client):
        """POST /change-password without login redirects to login."""
        with app.app_context():
            response = client.post("/change-password", data={
                "current_password": "testpass",
                "new_password": "newpassword12",
                "confirm_password": "newpassword12",
            }, follow_redirects=False)

            assert response.status_code == 302
            assert "login" in response.headers.get("Location", "")


class TestSessionManagement:
    """Tests for session invalidation functionality."""

    def test_invalidate_sessions(self, app, auth_client, seed_user):
        """POST /invalidate-sessions sets session_invalidated_at on user."""
        with app.app_context():
            before = datetime.now(timezone.utc)
            response = auth_client.post(
                "/invalidate-sessions", follow_redirects=True
            )
            after = datetime.now(timezone.utc)

            assert response.status_code == 200
            assert b"All other sessions have been logged out" in response.data

            # Reload user from database.
            user = db.session.get(User, seed_user["user"].id)
            # Verify timestamp is recent, not just non-null (audit section 43 smell fix).
            assert before <= user.session_invalidated_at <= after

    def test_invalidate_sessions_current_session_survives(self, app, auth_client, seed_user):
        """Current session remains valid after invalidation."""
        with app.app_context():
            auth_client.post("/invalidate-sessions")

            # The current session should still work -- not redirected to login.
            response = auth_client.get(
                "/settings?section=security", follow_redirects=False
            )
            assert response.status_code == 200

    def test_stale_session_rejected(self, app, db, seed_user):
        """load_user() returns None for sessions created before invalidation.

        Tests the load_user callback directly with a simulated stale
        session, avoiding g._login_user caching in the test environment.
        """
        user = seed_user["user"]
        user.session_invalidated_at = datetime.now(timezone.utc)
        db.session.flush()

        # Simulate a request with a stale _session_created_at.
        with app.test_request_context():
            from flask import session as flask_session  # pylint: disable=import-outside-toplevel
            flask_session["_session_created_at"] = (
                datetime.now(timezone.utc) - timedelta(hours=1)
            ).isoformat()

            from app.extensions import login_manager  # pylint: disable=import-outside-toplevel
            loaded_user = login_manager._user_callback(str(user.id))
            assert loaded_user is None

    def test_password_change_invalidates_sessions(self, app, auth_client, seed_user):
        """Password change sets session_invalidated_at."""
        with app.app_context():
            before = datetime.now(timezone.utc)
            auth_client.post("/change-password", data={
                "current_password": "testpass",
                "new_password": "newpassword12",
                "confirm_password": "newpassword12",
            })
            after = datetime.now(timezone.utc)

            # Reload user from database.
            user = db.session.get(User, seed_user["user"].id)
            # Verify timestamp is recent, not just non-null (audit section 43 smell fix).
            assert before <= user.session_invalidated_at <= after

    def test_invalidate_sessions_requires_login(self, app, client):
        """POST /invalidate-sessions without login redirects to login."""
        with app.app_context():
            response = client.post(
                "/invalidate-sessions", follow_redirects=False
            )
            assert response.status_code == 302
            assert "login" in response.headers.get("Location", "")


class TestMfaSetup:
    """Tests for the MFA setup flow."""

    def test_mfa_setup_page_renders(self, app, auth_client, seed_user):
        """GET /mfa/setup renders the QR code and manual key."""
        with app.app_context():
            response = auth_client.get("/mfa/setup")
            assert response.status_code == 200
            assert b"Set Up Two-Factor Authentication" in response.data
            assert b"mfa/confirm" in response.data
            # QR code data URI is present.
            assert b"data:image/png;base64," in response.data

    def test_mfa_setup_stores_encrypted_pending_server_side(
        self, app, auth_client, seed_user
    ):
        """GET /mfa/setup persists the pending secret in the DB, not the session.

        Locks down the C-05 contract: the unconfirmed TOTP secret lives
        in ``MfaConfig.pending_secret_encrypted`` (encrypted under the
        Fernet key) rather than in ``flask_session["_mfa_setup_secret"]``,
        because the Flask session cookie is signed but not encrypted.
        Verifies (a) the column is populated, (b) the bytes round-trip
        through ``mfa_service.decrypt_secret`` to a base32 string that
        matches the manual key shown on the page, and (c) the legacy
        session key is never written.
        """
        with app.app_context():
            response = auth_client.get("/mfa/setup")
            assert response.status_code == 200

            # The plaintext secret must NOT appear in the Flask session.
            with auth_client.session_transaction() as sess:
                assert "_mfa_setup_secret" not in sess

            # The encrypted pending secret IS in the database.
            config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .first()
            )
            assert config is not None, "MfaConfig row must exist after setup"
            assert config.pending_secret_encrypted is not None
            assert config.pending_secret_expires_at is not None

            # The ciphertext decrypts to the manual key rendered in the
            # response body.  The base32 secret is wrapped in <code>...</code>.
            decrypted = mfa_service.decrypt_secret(config.pending_secret_encrypted)
            assert (f"<code>{decrypted}</code>").encode("utf-8") in response.data

    def test_mfa_setup_sets_expiry_within_window(
        self, app, auth_client, seed_user
    ):
        """GET /mfa/setup sets pending_secret_expires_at ~15 minutes ahead.

        The TTL constant is ``MFA_SETUP_PENDING_TTL = timedelta(minutes=15)``
        in ``app/routes/auth.py``.  Allow a 60-second slack on either
        side so the test is stable under slow CI without permitting a
        regression that bumps the TTL by hours.
        """
        with app.app_context():
            before = datetime.now(timezone.utc)
            response = auth_client.get("/mfa/setup")
            after = datetime.now(timezone.utc)
            assert response.status_code == 200

            config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .first()
            )
            expires_at = config.pending_secret_expires_at
            assert expires_at is not None
            # 15 minutes minus a small slack and 15 minutes plus a small
            # slack bracket the legitimate window.
            lower = before + timedelta(minutes=15) - timedelta(seconds=60)
            upper = after + timedelta(minutes=15) + timedelta(seconds=60)
            assert lower <= expires_at <= upper, (
                f"pending_secret_expires_at={expires_at!r} not in "
                f"[{lower!r}, {upper!r}] -- the 15-minute TTL has "
                f"changed; update the test only if the change was deliberate."
            )

    def test_mfa_setup_replaces_previous_pending(
        self, app, auth_client, seed_user
    ):
        """A second GET /mfa/setup overwrites the first pending secret.

        Each visit must regenerate the secret so that an abandoned
        setup row cannot persist with stale data, and so a fresh QR
        scan after a typo always shows a usable code.  This also matches
        the documented C-05 contract (each /mfa/setup call rewrites the
        pending columns).
        """
        with app.app_context():
            auth_client.get("/mfa/setup")
            first_config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .first()
            )
            first_ciphertext = first_config.pending_secret_encrypted
            first_expiry = first_config.pending_secret_expires_at
            assert first_ciphertext is not None

            auth_client.get("/mfa/setup")
            db.session.refresh(first_config)
            second_ciphertext = first_config.pending_secret_encrypted
            second_expiry = first_config.pending_secret_expires_at

            assert second_ciphertext is not None
            # Fernet ciphertexts include a fresh IV per encryption, so
            # even encrypting the SAME plaintext under the SAME key
            # produces different bytes.  The stronger property to
            # verify is that the decrypted plaintexts differ -- the
            # secret really was regenerated.
            assert (
                mfa_service.decrypt_secret(first_ciphertext)
                != mfa_service.decrypt_secret(second_ciphertext)
            ), "Second /mfa/setup must regenerate the secret, not reuse it."
            # Expiry was extended to a fresh 15-minute window.
            assert second_expiry >= first_expiry

    def test_mfa_setup_redirects_if_already_enabled(self, app, auth_client, seed_user):
        """GET /mfa/setup redirects if MFA is already enabled."""
        with app.app_context():
            mfa_config = MfaConfig(
                user_id=seed_user["user"].id,
                is_enabled=True,
                totp_secret_encrypted=mfa_service.encrypt_secret("TESTBASE32SECRET"),
                backup_codes=mfa_service.hash_backup_codes(["aaaaaaaa"]),
            )
            db.session.add(mfa_config)
            db.session.commit()

            response = auth_client.get("/mfa/setup", follow_redirects=False)
            assert response.status_code == 302
            assert "security" in response.headers.get("Location", "")

    def test_mfa_confirm_valid_code(self, app, auth_client, seed_user, monkeypatch):
        """POST /mfa/confirm with valid code promotes pending to active.

        End-to-end check of the C-05 confirm path: the pending secret
        is decrypted, the code verifies, the secret is re-encrypted and
        stored as ``totp_secret_encrypted``, the pending columns are
        cleared, and the active credential is the same secret that was
        captured during setup (round-trip through encrypt -> decrypt).
        """
        with app.app_context():
            monkeypatch.setattr(mfa_service, "verify_totp_code", lambda s, c: True)

            # Visit setup to write the pending secret to the DB.
            auth_client.get("/mfa/setup")
            pending_config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .first()
            )
            captured_secret = mfa_service.decrypt_secret(
                pending_config.pending_secret_encrypted
            )

            # Confirm with a mocked-valid code.
            response = auth_client.post("/mfa/confirm", data={
                "totp_code": "123456",
            })
            assert response.status_code == 200
            assert b"Save Your Backup Codes" in response.data

            # Reload to pick up the post-confirm row state.
            config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .first()
            )
            assert config is not None
            assert config.is_enabled is True
            assert config.confirmed_at is not None
            # Pending columns must be cleared so a replay cannot
            # re-enrol against the same setup.
            assert config.pending_secret_encrypted is None
            assert config.pending_secret_expires_at is None
            # Active secret matches the secret captured during setup.
            assert config.totp_secret_encrypted is not None
            assert (
                mfa_service.decrypt_secret(config.totp_secret_encrypted)
                == captured_secret
            )
            assert config.backup_codes is not None

    def test_mfa_confirm_invalid_code_keeps_pending(
        self, app, auth_client, seed_user, monkeypatch
    ):
        """POST /mfa/confirm with invalid code preserves pending state.

        A typo on the verification form must not destroy the pending
        secret -- otherwise the user would have to re-scan the QR for
        every wrong digit.  Asserts (a) the user sees the invalid-code
        flash, (b) MFA is not enabled, and (c) the pending columns are
        unchanged after the POST itself so the user can retry until the
        15-minute expiry.

        ``follow_redirects=False`` is essential here: the redirect
        target is /mfa/setup, which DOES rewrite pending state by
        design.  The contract we are testing is the behavior of the
        confirm route alone, not the side effects of the user's next
        navigation.
        """
        with app.app_context():
            monkeypatch.setattr(mfa_service, "verify_totp_code", lambda s, c: False)

            # Visit setup to write a pending secret.
            auth_client.get("/mfa/setup")
            pending_before = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .first()
            )
            ciphertext_before = pending_before.pending_secret_encrypted
            expires_before = pending_before.pending_secret_expires_at
            assert ciphertext_before is not None

            response = auth_client.post("/mfa/confirm", data={
                "totp_code": "000000",
            }, follow_redirects=False)
            assert response.status_code == 302
            assert "mfa/setup" in response.headers.get("Location", "")

            db.session.refresh(pending_before)
            # Pending columns are unchanged -- the user can retry.
            assert pending_before.pending_secret_encrypted == ciphertext_before
            assert pending_before.pending_secret_expires_at == expires_before
            # MFA was NOT enabled.
            assert pending_before.is_enabled in (False, None)
            assert pending_before.totp_secret_encrypted is None
            assert pending_before.confirmed_at is None

            # Following the redirect manually delivers the flash to the
            # user, since the confirm route itself returned 302.
            follow_up = auth_client.get(
                response.headers["Location"], follow_redirects=False,
            )
            assert follow_up.status_code == 200
            assert b"Invalid code" in follow_up.data

    def test_mfa_confirm_no_pending_state(self, app, auth_client, seed_user):
        """POST /mfa/confirm with no pending secret in DB shows expired flash.

        Covers two paths through the same branch: (a) the user posts
        directly to /mfa/confirm without ever visiting /mfa/setup, so
        no MfaConfig row exists; (b) a hypothetical row with cleared
        pending columns.  Both should redirect to /mfa/setup with the
        "session expired" flash.
        """
        with app.app_context():
            # No prior /mfa/setup visit -- no MfaConfig row.
            response = auth_client.post("/mfa/confirm", data={
                "totp_code": "123456",
            }, follow_redirects=True)
            assert response.status_code == 200
            assert b"MFA setup session expired" in response.data

    def test_mfa_confirm_rejects_expired_pending(
        self, app, auth_client, seed_user, monkeypatch
    ):
        """POST /mfa/confirm rejects pending state past its expiry.

        Sets up a pending secret with an expiry in the past and posts a
        valid code.  The route must (a) reject the submission with the
        "expired" flash, (b) clear the stale pending columns, and
        (c) NOT promote the secret to ``totp_secret_encrypted``.  This
        is the C-05 anti-staleness contract: an attacker who briefly
        compromises an account cannot revisit /mfa/confirm hours later
        to silently enrol their own device.

        ``follow_redirects=False`` is essential: the redirect target
        /mfa/setup writes new pending state by design and would mask
        the cleared columns we are asserting on.
        """
        with app.app_context():
            monkeypatch.setattr(mfa_service, "verify_totp_code", lambda s, c: True)

            # Build a row with pending state already past expiry.
            mfa_config = MfaConfig(
                user_id=seed_user["user"].id,
                pending_secret_encrypted=mfa_service.encrypt_secret(
                    "JBSWY3DPEHPK3PXP"
                ),
                pending_secret_expires_at=(
                    datetime.now(timezone.utc) - timedelta(minutes=1)
                ),
            )
            db.session.add(mfa_config)
            db.session.commit()

            response = auth_client.post("/mfa/confirm", data={
                "totp_code": "123456",
            }, follow_redirects=False)
            assert response.status_code == 302
            assert "mfa/setup" in response.headers.get("Location", "")

            db.session.refresh(mfa_config)
            # Stale pending state cleared.
            assert mfa_config.pending_secret_encrypted is None
            assert mfa_config.pending_secret_expires_at is None
            # MFA was NOT enabled.
            assert mfa_config.is_enabled in (False, None)
            assert mfa_config.totp_secret_encrypted is None
            assert mfa_config.confirmed_at is None

            # The flash arrives on the next page; verify it explicitly
            # so the user-visible message is locked down.  Use
            # follow_redirects=False on the GET so we do not chain
            # through into a fresh /mfa/setup that resets the page.
            follow_up = auth_client.get(
                response.headers["Location"], follow_redirects=False,
            )
            assert follow_up.status_code == 200
            assert b"MFA setup session expired" in follow_up.data

    def test_regenerate_backup_codes(self, app, auth_client, seed_user):
        """POST /mfa/regenerate-backup-codes generates new codes."""
        with app.app_context():
            # Enable MFA for the user first.
            mfa_config = MfaConfig(
                user_id=seed_user["user"].id,
                is_enabled=True,
                totp_secret_encrypted=mfa_service.encrypt_secret("TESTBASE32SECRET"),
                backup_codes=mfa_service.hash_backup_codes(["aaaaaaaa"]),
            )
            db.session.add(mfa_config)
            db.session.commit()

            response = auth_client.post("/mfa/regenerate-backup-codes")
            assert response.status_code == 200
            assert b"Save Your Backup Codes" in response.data

    def test_regenerate_backup_codes_requires_mfa_enabled(self, app, auth_client, seed_user):
        """POST /mfa/regenerate-backup-codes without MFA enabled shows error."""
        with app.app_context():
            response = auth_client.post(
                "/mfa/regenerate-backup-codes", follow_redirects=True
            )
            assert response.status_code == 200
            assert b"Two-factor authentication is not enabled" in response.data

    def test_regenerate_backup_codes_renders_28_char_codes(self, app, auth_client, seed_user):
        """POST /mfa/regenerate-backup-codes renders 10 codes of 28 hex chars.

        Asserts the post-C-03 contract end-to-end: the route generates
        backup codes, hashes them, persists the hashes, and renders the
        plaintext codes once. Each rendered code must be 28 lowercase
        hex characters (112 bits of entropy). Without this end-to-end
        assertion, a regression in either the generator or the template
        could ship without breaking any unit test.
        """
        with app.app_context():
            mfa_config = MfaConfig(
                user_id=seed_user["user"].id,
                is_enabled=True,
                totp_secret_encrypted=mfa_service.encrypt_secret("TESTBASE32SECRET"),
                backup_codes=mfa_service.hash_backup_codes(["legacy01"], rounds=4),
            )
            db.session.add(mfa_config)
            db.session.commit()

            response = auth_client.post("/mfa/regenerate-backup-codes")
            assert response.status_code == 200
            assert b"Save Your Backup Codes" in response.data

            body = response.get_data(as_text=True)
            # Match exactly 28 lowercase hex chars terminated by a non-hex
            # boundary (the surrounding markup) so partial matches inside
            # bcrypt hashes or similar can never fool the count.
            rendered_codes = re.findall(r"(?<![0-9a-f])[0-9a-f]{28}(?![0-9a-f])", body)
            assert len(rendered_codes) == 10, (
                f"Expected 10 28-char codes in response; found "
                f"{len(rendered_codes)}: {rendered_codes!r}"
            )

            stored_hashes = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .first()
                .backup_codes
            )
            assert len(stored_hashes) == 10
            # The stored bcrypt hashes match the freshly rendered plaintext
            # codes -- the route persisted what it displayed and rotated
            # away from the legacy code.
            for plaintext in rendered_codes:
                idx = mfa_service.verify_backup_code(plaintext, stored_hashes)
                assert idx >= 0, (
                    f"Rendered code {plaintext!r} has no matching stored hash"
                )
            # The pre-existing legacy code must no longer verify -- this
            # endpoint regenerates, it does not append.
            assert mfa_service.verify_backup_code("legacy01", stored_hashes) == -1

    def test_mfa_confirm_renders_28_char_codes(self, app, auth_client, seed_user, monkeypatch):
        """POST /mfa/confirm renders 10 freshly generated 28-char backup codes.

        Mirrors test_regenerate_backup_codes_renders_28_char_codes for
        the initial enrollment path. Both routes call
        ``mfa_service.generate_backup_codes()`` and render
        ``auth/mfa_backup_codes.html``; both must show the upgraded
        format.
        """
        with app.app_context():
            monkeypatch.setattr(mfa_service, "verify_totp_code", lambda s, c: True)
            auth_client.get("/mfa/setup")

            response = auth_client.post("/mfa/confirm", data={"totp_code": "123456"})
            assert response.status_code == 200
            assert b"Save Your Backup Codes" in response.data

            body = response.get_data(as_text=True)
            rendered_codes = re.findall(r"(?<![0-9a-f])[0-9a-f]{28}(?![0-9a-f])", body)
            assert len(rendered_codes) == 10, (
                f"Expected 10 28-char codes in response; found "
                f"{len(rendered_codes)}: {rendered_codes!r}"
            )

    def test_mfa_confirm_missing_totp_key(self, app, auth_client, seed_user, monkeypatch):
        """POST /mfa/confirm redirects to security settings when TOTP key is missing.

        Setup writes a pending secret successfully; then the operator
        unsets ``TOTP_ENCRYPTION_KEY`` between request boundaries.  At
        confirm time the route's ``decrypt_secret`` call raises
        RuntimeError.  The route must (a) clear the unrecoverable
        pending state, (b) redirect to ``/settings?section=security``
        rather than looping the user through /mfa/setup (which would
        also fail), and (c) NOT return a 500.
        """
        with app.app_context():
            monkeypatch.setattr(mfa_service, "verify_totp_code", lambda s, c: True)

            # Visit setup to write the pending secret while the key is set.
            auth_client.get("/mfa/setup")

            # Remove the key so the next decrypt_secret raises RuntimeError.
            monkeypatch.delenv("TOTP_ENCRYPTION_KEY", raising=False)

            response = auth_client.post("/mfa/confirm", data={
                "totp_code": "123456",
            }, follow_redirects=False)
            assert response.status_code == 302
            assert "security" in response.headers.get("Location", "")

            # The unrecoverable pending state must have been cleared so
            # the next request does not encounter the same broken row.
            config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .first()
            )
            assert config.pending_secret_encrypted is None
            assert config.pending_secret_expires_at is None
            assert config.is_enabled in (False, None)
            assert config.totp_secret_encrypted is None


class TestMfaLogin:
    """Tests for the two-step MFA login flow."""

    def _enable_mfa(self, user_id, known_codes=None):
        """Helper to enable MFA for a user with a known secret and backup codes.

        Args:
            user_id: The user's primary key.
            known_codes: Optional list of plaintext backup codes. Defaults to
                         a standard set of 3 codes.

        Returns:
            tuple: (plaintext_secret, plaintext_backup_codes)
        """
        secret = "JBSWY3DPEHPK3PXP"
        if known_codes is None:
            known_codes = ["aaaaaaaa", "bbbbbbbb", "cccccccc"]
        mfa_config = MfaConfig(
            user_id=user_id,
            is_enabled=True,
            totp_secret_encrypted=mfa_service.encrypt_secret(secret),
            backup_codes=mfa_service.hash_backup_codes(known_codes),
        )
        db.session.add(mfa_config)
        db.session.commit()
        return secret, known_codes

    def test_login_with_mfa_redirects_to_verify(self, app, client, seed_user):
        """POST /login with MFA enabled redirects to /mfa/verify instead of grid."""
        with app.app_context():
            self._enable_mfa(seed_user["user"].id)

            response = client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            }, follow_redirects=False)

            assert response.status_code == 302
            assert "mfa/verify" in response.headers.get("Location", "")

            # User should NOT be logged in -- a protected page should redirect.
            grid_resp = client.get("/", follow_redirects=False)
            assert grid_resp.status_code == 302
            assert "login" in grid_resp.headers.get("Location", "")

    def test_mfa_verify_page_renders(self, app, client, seed_user):
        """GET /mfa/verify renders the verification form when pending user exists."""
        with app.app_context():
            self._enable_mfa(seed_user["user"].id)

            # POST to /login to set up pending state.
            client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })

            response = client.get("/mfa/verify")
            assert response.status_code == 200
            assert b"Two-Factor Verification" in response.data

    def test_mfa_verify_no_pending_redirects_to_login(self, app, client):
        """GET /mfa/verify without pending user redirects to login."""
        with app.app_context():
            response = client.get("/mfa/verify", follow_redirects=False)
            assert response.status_code == 302
            assert "login" in response.headers.get("Location", "")

    def test_mfa_verify_valid_totp(self, app, client, seed_user, monkeypatch):
        """POST /mfa/verify with valid TOTP code completes login."""
        with app.app_context():
            self._enable_mfa(seed_user["user"].id)
            monkeypatch.setattr(mfa_service, "verify_totp_code", lambda s, c: True)

            # Step 1: enter pending state.
            client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })

            # Step 2: submit TOTP code.
            response = client.post("/mfa/verify", data={
                "totp_code": "123456",
            }, follow_redirects=False)

            assert response.status_code == 302
            # Should redirect to grid, not back to login or verify.
            location = response.headers.get("Location", "")
            assert "login" not in location
            assert "mfa" not in location

            # User is now logged in -- protected page should return 200.
            grid_resp = client.get("/", follow_redirects=False)
            assert grid_resp.status_code == 200

    def test_mfa_verify_invalid_totp(self, app, client, seed_user, monkeypatch):
        """POST /mfa/verify with invalid TOTP code shows generic error."""
        with app.app_context():
            self._enable_mfa(seed_user["user"].id)
            monkeypatch.setattr(mfa_service, "verify_totp_code", lambda s, c: False)

            client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })

            response = client.post("/mfa/verify", data={
                "totp_code": "000000",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Invalid verification code." in response.data

            # User should NOT be logged in.
            grid_resp = client.get("/", follow_redirects=False)
            assert grid_resp.status_code == 302
            assert "login" in grid_resp.headers.get("Location", "")

    def test_mfa_verify_missing_totp_key(self, app, client, seed_user, monkeypatch):
        """POST /mfa/verify redirects to login when TOTP key is missing.

        When TOTP_ENCRYPTION_KEY is removed after MFA was enabled,
        decrypt_secret() raises RuntimeError.  The route must catch
        this, clear pending session state, and redirect to login
        instead of returning a 500 error.
        """
        with app.app_context():
            self._enable_mfa(seed_user["user"].id)

            # Step 1: enter pending state via login.
            client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })

            # Remove the key so decrypt_secret raises RuntimeError.
            monkeypatch.delenv("TOTP_ENCRYPTION_KEY", raising=False)

            # Step 2: attempt MFA verification.
            response = client.post("/mfa/verify", data={
                "totp_code": "123456",
            }, follow_redirects=False)

            assert response.status_code == 302
            assert "login" in response.headers.get("Location", "")

    def test_mfa_verify_valid_backup_code(self, app, client, seed_user):
        """POST /mfa/verify with valid backup code completes login and consumes the code."""
        with app.app_context():
            _, known_codes = self._enable_mfa(seed_user["user"].id)

            # Step 1: enter pending state.
            client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })

            # Step 2: submit the first backup code.
            response = client.post("/mfa/verify", data={
                "backup_code": known_codes[0],
            }, follow_redirects=False)

            assert response.status_code == 302
            location = response.headers.get("Location", "")
            assert "login" not in location
            assert "mfa" not in location

            # User is logged in.
            grid_resp = client.get("/", follow_redirects=False)
            assert grid_resp.status_code == 200

            # The used code should be removed from the database.
            config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .first()
            )
            assert len(config.backup_codes) == len(known_codes) - 1

    def test_mfa_verify_invalid_backup_code(self, app, client, seed_user):
        """POST /mfa/verify with invalid backup code shows generic error."""
        with app.app_context():
            self._enable_mfa(seed_user["user"].id)

            client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })

            response = client.post("/mfa/verify", data={
                "backup_code": "zzzzzzzz",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Invalid verification code." in response.data

            # User should NOT be logged in.
            grid_resp = client.get("/", follow_redirects=False)
            assert grid_resp.status_code == 302
            assert "login" in grid_resp.headers.get("Location", "")

    def test_mfa_verify_backup_code_consumed(self, app, client, seed_user):
        """A used backup code cannot be reused."""
        with app.app_context():
            _, known_codes = self._enable_mfa(seed_user["user"].id)

            # First login cycle: use the first backup code.
            client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })
            client.post("/mfa/verify", data={
                "backup_code": known_codes[0],
            })

            # Log out.
            client.post("/logout")

            # Second login cycle: try the same backup code again.
            client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })
            response = client.post("/mfa/verify", data={
                "backup_code": known_codes[0],
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Invalid verification code." in response.data

    def test_login_without_mfa_unchanged(self, app, client, seed_user):
        """POST /login without MFA enabled completes in one step (existing behavior)."""
        with app.app_context():
            # No MFA enabled for seed_user.
            response = client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            }, follow_redirects=False)

            assert response.status_code == 302
            location = response.headers.get("Location", "")
            # Should redirect to grid, not to /mfa/verify.
            assert "mfa" not in location

            # User is logged in.
            grid_resp = client.get("/", follow_redirects=False)
            assert grid_resp.status_code == 200

    def test_mfa_open_redirect_absolute_url_blocked(
        self, app, client, seed_user, monkeypatch
    ):
        """Open redirect via the MFA flow must be blocked.

        Attack scenario: attacker puts a malicious next on the login URL.
        The next value is stored in the session during the MFA pending step
        and used after TOTP verification.  It must be validated at storage
        time AND at redirect time (defense in depth).
        """
        with app.app_context():
            self._enable_mfa(seed_user["user"].id)
            monkeypatch.setattr(mfa_service, "verify_totp_code", lambda s, c: True)

            # Step 1: login with malicious next parameter.
            client.post("/login?next=https://evil.com", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })

            # Step 2: complete MFA verification.
            response = client.post("/mfa/verify", data={
                "totp_code": "123456",
            }, follow_redirects=False)

            assert response.status_code == 302
            location = response.headers.get("Location", "")
            # Must NOT redirect to the attacker's site.
            assert "evil.com" not in location
            # Must redirect to the dashboard (home page).
            assert "/dashboard" in location or location.endswith("/")

    def test_mfa_open_redirect_protocol_relative_blocked(
        self, app, client, seed_user, monkeypatch
    ):
        """Protocol-relative URLs must be blocked in the MFA flow.

        //evil.com bypasses scheme-only checks and inherits the current
        page's protocol.  The MFA flow stores next in the session and
        uses it after TOTP verification -- both steps must validate.
        """
        with app.app_context():
            self._enable_mfa(seed_user["user"].id)
            monkeypatch.setattr(mfa_service, "verify_totp_code", lambda s, c: True)

            # Step 1: login with protocol-relative next.
            client.post("/login?next=//evil.com", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })

            # Step 2: complete MFA verification.
            response = client.post("/mfa/verify", data={
                "totp_code": "123456",
            }, follow_redirects=False)

            assert response.status_code == 302
            location = response.headers.get("Location", "")
            assert "evil.com" not in location
            assert "/dashboard" in location or location.endswith("/")

    def test_mfa_safe_next_redirect_allowed(
        self, app, client, seed_user, monkeypatch
    ):
        """Legitimate next values must survive the MFA two-step flow.

        A safe relative path set on the login URL must be preserved
        through the MFA pending session state and used as the redirect
        target after successful TOTP verification.
        """
        with app.app_context():
            self._enable_mfa(seed_user["user"].id)
            monkeypatch.setattr(mfa_service, "verify_totp_code", lambda s, c: True)

            # Step 1: login with a safe next parameter.
            client.post("/login?next=/templates", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })

            # Step 2: complete MFA verification.
            response = client.post("/mfa/verify", data={
                "totp_code": "123456",
            }, follow_redirects=False)

            assert response.status_code == 302
            location = response.headers.get("Location", "")
            # Must redirect to the safe requested page.
            assert "/templates" in location


class TestMfaDisable:
    """Tests for the MFA disable flow."""

    def _enable_mfa(self, user_id):
        """Helper to enable MFA for a user with a known secret and backup codes.

        Args:
            user_id: The user's primary key.

        Returns:
            tuple: (plaintext_secret, MfaConfig instance)
        """
        secret = "JBSWY3DPEHPK3PXP"
        known_codes = ["aaaaaaaa", "bbbbbbbb", "cccccccc"]
        mfa_config = MfaConfig(
            user_id=user_id,
            is_enabled=True,
            totp_secret_encrypted=mfa_service.encrypt_secret(secret),
            backup_codes=mfa_service.hash_backup_codes(known_codes),
        )
        db.session.add(mfa_config)
        db.session.commit()
        return secret, mfa_config

    def test_mfa_disable_page_renders(self, app, auth_client, seed_user):
        """GET /mfa/disable renders the confirmation form when MFA is enabled."""
        with app.app_context():
            self._enable_mfa(seed_user["user"].id)

            response = auth_client.get("/mfa/disable")
            assert response.status_code == 200
            assert b"Disable Two-Factor Authentication" in response.data
            assert b"current_password" in response.data

    def test_mfa_disable_redirects_if_not_enabled(self, app, auth_client, seed_user):
        """GET /mfa/disable redirects if MFA is not enabled."""
        with app.app_context():
            response = auth_client.get("/mfa/disable", follow_redirects=False)
            assert response.status_code == 302
            assert "security" in response.headers.get("Location", "")

    def test_mfa_disable_success(self, app, auth_client, seed_user, monkeypatch):
        """POST /mfa/disable with valid password + TOTP disables MFA."""
        with app.app_context():
            self._enable_mfa(seed_user["user"].id)
            monkeypatch.setattr(mfa_service, "verify_totp_code", lambda s, c: True)

            response = auth_client.post("/mfa/disable", data={
                "current_password": "testpass",
                "totp_code": "123456",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Two-factor authentication has been disabled" in response.data

            # Verify MFA is fully cleared in the database.
            config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .first()
            )
            assert config.is_enabled is False
            assert config.totp_secret_encrypted is None
            assert config.backup_codes is None
            assert config.confirmed_at is None

    def test_mfa_disable_wrong_password(self, app, auth_client, seed_user):
        """POST /mfa/disable with wrong password shows error."""
        with app.app_context():
            self._enable_mfa(seed_user["user"].id)

            response = auth_client.post("/mfa/disable", data={
                "current_password": "wrongpassword",
                "totp_code": "123456",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Invalid password" in response.data

            # MFA should still be enabled.
            config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .first()
            )
            assert config.is_enabled is True

    def test_mfa_disable_wrong_totp(self, app, auth_client, seed_user, monkeypatch):
        """POST /mfa/disable with wrong TOTP code shows error."""
        with app.app_context():
            self._enable_mfa(seed_user["user"].id)
            monkeypatch.setattr(mfa_service, "verify_totp_code", lambda s, c: False)

            response = auth_client.post("/mfa/disable", data={
                "current_password": "testpass",
                "totp_code": "000000",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Invalid authentication code" in response.data

            # MFA should still be enabled.
            config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .first()
            )
            assert config.is_enabled is True


class TestRegistration:
    """Tests for the /register endpoint (GET and POST)."""

    def test_get_register_renders_form(self, app, client):
        """GET /register returns the registration form with all expected fields."""
        with app.app_context():
            response = client.get("/register")
            assert response.status_code == 200
            assert b"Create Account" in response.data
            assert b'name="email"' in response.data
            assert b'name="display_name"' in response.data
            assert b'name="password"' in response.data
            assert b'name="confirm_password"' in response.data
            assert b"csrf_token" in response.data

    def test_get_register_has_login_link(self, app, client):
        """GET /register includes a link back to the login page."""
        with app.app_context():
            response = client.get("/register")
            assert response.status_code == 200
            assert b"Already have an account?" in response.data
            assert b"/login" in response.data

    def test_get_login_has_register_link(self, app, client):
        """GET /login includes a link to the registration page."""
        with app.app_context():
            response = client.get("/login")
            assert response.status_code == 200
            assert b"Create an account" in response.data
            assert b"/register" in response.data

    def test_register_success_creates_all_records(self, app, client):
        """POST /register with valid data creates User, UserSettings, and Scenario.

        Verifies the redirect to /login, the success flash message, and
        that all three database records exist with correct values.
        """
        with app.app_context():
            response = client.post("/register", data={
                "email": "NewUser@Example.com",
                "display_name": "New User",
                "password": "securepass123",
                "confirm_password": "securepass123",
            }, follow_redirects=False)

            assert response.status_code == 302
            assert "/login" in response.headers.get("Location", "")

            # Follow the redirect and check for flash message.
            follow = client.get(response.headers["Location"])
            assert b"Account created" in follow.data

            # Verify database records (email should be lowercased).
            user = db.session.query(User).filter_by(
                email="newuser@example.com"
            ).first()
            assert user is not None

            settings = db.session.query(UserSettings).filter_by(
                user_id=user.id
            ).first()
            assert settings is not None

            scenario = db.session.query(Scenario).filter_by(
                user_id=user.id, is_baseline=True
            ).first()
            assert scenario is not None

    def test_register_success_user_can_login(self, app, client):
        """A newly registered user can log in with their credentials.

        Verifies the full registration-to-login flow works end to end.
        """
        with app.app_context():
            # Register.
            client.post("/register", data={
                "email": "logintest@example.com",
                "display_name": "Login Test",
                "password": "securepass123",
                "confirm_password": "securepass123",
            })

            # Log in with the same credentials.
            login_resp = client.post("/login", data={
                "email": "logintest@example.com",
                "password": "securepass123",
            }, follow_redirects=False)

            assert login_resp.status_code == 302
            # Should redirect to grid, not back to login.
            location = login_resp.headers.get("Location", "")
            assert "login" not in location

    def test_register_success_new_user_sees_empty_grid(
        self, app, client, seed_user, seed_periods_today
    ):
        """A newly registered user sees an empty grid with no seed user data.

        Verifies complete data isolation: the new user's grid does not
        contain transactions from the seed user.
        """
        with app.app_context():
            # Register a new user.
            client.post("/register", data={
                "email": "isolated@example.com",
                "display_name": "Isolated User",
                "password": "securepass123",
                "confirm_password": "securepass123",
            })

            # Log in as the new user.
            client.post("/login", data={
                "email": "isolated@example.com",
                "password": "securepass123",
            })

            # Access the grid.
            grid_resp = client.get("/")
            assert grid_resp.status_code == 200
            # Should NOT contain seed user data.
            assert b"Rent Payment" not in grid_resp.data

    def test_register_duplicate_email_shows_error(
        self, app, client, seed_user
    ):
        """POST /register with an existing email shows the conflict error.

        Uses the seed_user fixture (test@shekel.local) to trigger a
        duplicate email conflict.
        """
        with app.app_context():
            response = client.post("/register", data={
                "email": "test@shekel.local",
                "display_name": "Dup Test",
                "password": "securepass123",
                "confirm_password": "securepass123",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"already exists" in response.data

    def test_register_duplicate_email_preserves_form_input(
        self, app, client, seed_user
    ):
        """POST /register with a duplicate email preserves the submitted values.

        After a conflict error, the form should be re-rendered with the
        email and display_name fields pre-filled so the user does not
        have to re-type them.
        """
        with app.app_context():
            response = client.post("/register", data={
                "email": "test@shekel.local",
                "display_name": "Keep This Name",
                "password": "securepass123",
                "confirm_password": "securepass123",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"test@shekel.local" in response.data
            assert b"Keep This Name" in response.data

    def test_register_short_password_shows_error(self, app, client):
        """POST /register with a short password shows a validation error."""
        with app.app_context():
            response = client.post("/register", data={
                "email": "short@example.com",
                "display_name": "Short Test",
                "password": "12345678901",
                "confirm_password": "12345678901",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"at least 12 characters" in response.data

    def test_register_password_mismatch_shows_error(self, app, client):
        """POST /register with mismatched passwords shows an error."""
        with app.app_context():
            response = client.post("/register", data={
                "email": "mismatch@example.com",
                "display_name": "Mismatch Test",
                "password": "validpassword1",
                "confirm_password": "validpassword2",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"do not match" in response.data

    def test_register_invalid_email_shows_error(self, app, client):
        """POST /register with an invalid email shows a validation error."""
        with app.app_context():
            response = client.post("/register", data={
                "email": "notvalid",
                "display_name": "Invalid Email Test",
                "password": "securepass123",
                "confirm_password": "securepass123",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Invalid email format" in response.data

    def test_register_empty_display_name_shows_error(self, app, client):
        """POST /register with an empty display name shows a validation error."""
        with app.app_context():
            response = client.post("/register", data={
                "email": "noname@example.com",
                "display_name": "",
                "password": "securepass123",
                "confirm_password": "securepass123",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Display name is required" in response.data

    def test_register_get_redirects_when_authenticated(
        self, app, auth_client
    ):
        """GET /register redirects to the grid when already logged in."""
        with app.app_context():
            response = auth_client.get("/register", follow_redirects=False)
            assert response.status_code == 302
            # Should redirect to grid, not stay on register.
            location = response.headers.get("Location", "")
            assert "register" not in location

    def test_register_post_redirects_when_authenticated(
        self, app, auth_client, seed_user
    ):
        """POST /register redirects when already logged in, creating no new user.

        Verifies that authenticated users cannot create additional
        accounts via POST.
        """
        with app.app_context():
            user_count_before = db.session.query(User).count()

            response = auth_client.post("/register", data={
                "email": "sneaky@example.com",
                "display_name": "Sneaky",
                "password": "securepass123",
                "confirm_password": "securepass123",
            }, follow_redirects=False)

            assert response.status_code == 302

            # No new user should have been created.
            user_count_after = db.session.query(User).count()
            assert user_count_after == user_count_before

    def test_register_success_has_baseline_scenario(self, app, client):
        """POST /register creates exactly one baseline Scenario for the new user.

        Verifies the scenario has the correct name, is_baseline flag,
        and user_id.
        """
        with app.app_context():
            client.post("/register", data={
                "email": "scenario@example.com",
                "display_name": "Scenario Test",
                "password": "securepass123",
                "confirm_password": "securepass123",
            })

            user = db.session.query(User).filter_by(
                email="scenario@example.com"
            ).first()
            assert user is not None

            scenarios = db.session.query(Scenario).filter_by(
                user_id=user.id
            ).all()
            assert len(scenarios) == 1
            assert scenarios[0].is_baseline is True
            assert scenarios[0].name == "Baseline"
            assert scenarios[0].user_id == user.id

    def test_register_success_creates_default_categories(self, app, client):
        """POST /register creates 22 default categories for the new user."""
        with app.app_context():
            client.post("/register", data={
                "email": "categories@example.com",
                "display_name": "Category Test",
                "password": "securepass123",
                "confirm_password": "securepass123",
            })

            user = db.session.query(User).filter_by(
                email="categories@example.com"
            ).first()
            assert user is not None

            categories = db.session.query(Category).filter_by(
                user_id=user.id
            ).all()
            assert len(categories) == 24

    def test_register_success_creates_checking_account(self, app, client):
        """POST /register creates a default checking account for the new user."""
        with app.app_context():
            client.post("/register", data={
                "email": "acct@example.com",
                "display_name": "Account Test",
                "password": "securepass123",
                "confirm_password": "securepass123",
            })

            user = db.session.query(User).filter_by(
                email="acct@example.com"
            ).first()
            assert user is not None

            accounts = db.session.query(Account).filter_by(
                user_id=user.id
            ).all()
            assert len(accounts) == 1
            assert accounts[0].name == "Checking"

    def test_register_disabled_get_returns_404(self, app, client):
        """GET /register returns 404 when REGISTRATION_ENABLED is False."""
        with app.app_context():
            app.config["REGISTRATION_ENABLED"] = False
            response = client.get("/register")
            assert response.status_code == 404
            # The form must not leak in a custom 404 page.
            assert b"Create Account" not in response.data
            app.config["REGISTRATION_ENABLED"] = True

    def test_register_disabled_post_returns_404(self, app, client):
        """POST /register returns 404 and creates no user when disabled."""
        with app.app_context():
            app.config["REGISTRATION_ENABLED"] = False
            response = client.post("/register", data={
                "email": "blocked@example.com",
                "display_name": "Blocked",
                "password": "securepass123",
                "confirm_password": "securepass123",
            })
            assert response.status_code == 404

            # Confirm no user was created.
            user = db.session.query(User).filter_by(
                email="blocked@example.com"
            ).first()
            assert user is None
            app.config["REGISTRATION_ENABLED"] = True

    def test_login_hides_register_link_when_disabled(self, app, client):
        """GET /login omits the register link when registration is disabled."""
        with app.app_context():
            app.config["REGISTRATION_ENABLED"] = False
            response = client.get("/login")
            assert response.status_code == 200
            assert b"Create an account" not in response.data
            app.config["REGISTRATION_ENABLED"] = True

    def test_login_shows_register_link_when_enabled(self, app, client):
        """GET /login shows the register link when registration is enabled."""
        with app.app_context():
            response = client.get("/login")
            assert response.status_code == 200
            assert b"Create an account" in response.data

    def test_register_post_rate_limited(self, app):
        """POST /register is rate-limited to 3 per hour (H5).

        Uses the same pattern as test_rate_limiting_after_5_attempts:
        create a fresh app with RATELIMIT_ENABLED=True, then verify
        the 4th POST triggers a 429.
        """
        with app.app_context():
            rate_app = create_app("testing")
            rate_app.config["RATELIMIT_ENABLED"] = True

            from app.extensions import limiter
            limiter.enabled = True
            limiter.init_app(rate_app)

            rate_client = rate_app.test_client()

            with rate_app.app_context():
                # Make 3 registration attempts (within the limit).
                for i in range(3):
                    rate_client.post("/register", data={
                        "email": f"bot{i}@example.com",
                        "display_name": f"Bot {i}",
                        "password": "securepass123",
                        "confirm_password": "securepass123",
                    })

                # 4th attempt should be rate-limited.
                response = rate_client.post("/register", data={
                    "email": "bot3@example.com",
                    "display_name": "Bot 3",
                    "password": "securepass123",
                    "confirm_password": "securepass123",
                })
                assert response.status_code == 429

            # Clean up.
            with rate_app.app_context():
                from app.extensions import db as _db
                _db.engine.dispose()
            limiter.enabled = False


class TestMfaVerifySecurity:
    """Security tests for the /mfa/verify endpoint.

    Covers rate limiting, session isolation (IDOR prevention), and
    rejection of requests with no pending MFA state.
    """

    def _enable_mfa(self, user_id):
        """Enable MFA for a user with a known secret and backup codes.

        Args:
            user_id: The user's primary key.

        Returns:
            str: The plaintext TOTP secret.
        """
        secret = "JBSWY3DPEHPK3PXP"
        mfa_config = MfaConfig(
            user_id=user_id,
            is_enabled=True,
            totp_secret_encrypted=mfa_service.encrypt_secret(secret),
            backup_codes=mfa_service.hash_backup_codes(["aaaaaaaa"]),
        )
        db.session.add(mfa_config)
        db.session.commit()
        return secret

    def test_mfa_verify_rate_limiting(self, app, seed_user):
        """POST /mfa/verify is rate-limited to 5 attempts per 15 minutes.

        Without rate limiting, a 6-digit TOTP code (1,000,000 possibilities)
        could be brute-forced in hours. This test verifies the limiter blocks
        the 6th failed attempt with a 429 status.
        """
        with app.app_context():
            # Create a fresh app with rate limiting enabled (TestConfig disables it).
            rate_app = create_app("testing")
            rate_app.config["RATELIMIT_ENABLED"] = True

            # Re-initialize limiter with rate limiting enabled.
            from app.extensions import limiter  # pylint: disable=import-outside-toplevel
            limiter.enabled = True
            limiter.init_app(rate_app)

            rate_client = rate_app.test_client()

            with rate_app.app_context():
                # Enable MFA for the seed user.
                self._enable_mfa(seed_user["user"].id)

                # Login to reach MFA pending state.
                login_resp = rate_client.post("/login", data={
                    "email": "test@shekel.local",
                    "password": "testpass",
                }, follow_redirects=False)
                assert login_resp.status_code == 302
                assert "mfa/verify" in login_resp.headers.get("Location", "")

                # Submit 5 wrong TOTP codes (within the limit).
                for i in range(5):
                    resp = rate_client.post("/mfa/verify", data={
                        "totp_code": "000000",
                    })
                    # Each attempt should succeed at the HTTP level (invalid code, not rate-limited).
                    assert resp.status_code == 200, \
                        f"Attempt {i + 1} should return 200, got {resp.status_code}"
                    assert b"Invalid verification code." in resp.data

                # 6th attempt should be rate-limited.
                response = rate_client.post("/mfa/verify", data={
                    "totp_code": "000000",
                })
                assert response.status_code == 429

            # Clean up: dispose the secondary app's engine to release
            # connections, and reset limiter for other tests.
            with rate_app.app_context():
                from app.extensions import db as _db  # pylint: disable=import-outside-toplevel
                _db.engine.dispose()
            limiter.enabled = False

    def test_mfa_verify_idor_other_users_pending_session(self, app, client, seed_user):
        """Session isolation: a second client cannot access another session's MFA state.

        The _mfa_pending_user_id is stored in the Flask session cookie, so a
        separate client (different session) should have no access to it. This
        prevents cross-session MFA completion.
        """
        with app.app_context():
            self._enable_mfa(seed_user["user"].id)

            # Client 1: Login to enter MFA pending state.
            client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })

            # Client 2: A separate test client with its own session.
            client2 = app.test_client()

            # Client 2 has no pending MFA state -- should be redirected to login.
            response = client2.get("/mfa/verify", follow_redirects=False)
            assert response.status_code == 302
            assert "login" in response.headers.get("Location", "")

    def test_mfa_verify_no_pending_user_post_rejected(self, app, client):
        """POST /mfa/verify with no pending MFA user redirects to login.

        Prevents abuse of the verify endpoint when no authentication flow
        is in progress. The route checks for _mfa_pending_user_id before
        processing any submitted code.
        """
        with app.app_context():
            response = client.post("/mfa/verify", data={
                "totp_code": "123456",
            }, follow_redirects=False)

            assert response.status_code == 302
            assert "login" in response.headers.get("Location", "")


class TestPasswordChangeEdgeCases:
    """Edge-case tests for POST /change-password.

    Covers same-as-current password, double submission, and
    whitespace-only passwords to document security-relevant behavior.
    """

    def test_change_password_same_as_current(self, app, auth_client, seed_user):
        """Changing password to the same value succeeds (no reuse check).

        The auth service does not prevent password reuse. This test
        documents the current behavior. Consider rejecting same-as-current
        for improved security posture.
        """
        with app.app_context():
            # First change to a password that meets the 12-char minimum,
            # since the seed user's initial password ("testpass") is only 8 chars.
            resp1 = auth_client.post("/change-password", data={
                "current_password": "testpass",
                "new_password": "testpass1234",
                "confirm_password": "testpass1234",
            }, follow_redirects=True)
            assert resp1.status_code == 200
            assert b"Password changed successfully" in resp1.data

            # Now try to change to the exact same password.
            resp2 = auth_client.post("/change-password", data={
                "current_password": "testpass1234",
                "new_password": "testpass1234",
                "confirm_password": "testpass1234",
            }, follow_redirects=True)

            # App allows reusing current password (consider rejecting for security).
            assert resp2.status_code == 200
            assert b"Password changed successfully" in resp2.data

            # Verify the password still works for login.
            auth_client.post("/logout")
            login_resp = auth_client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass1234",
            }, follow_redirects=False)
            assert login_resp.status_code == 302

    def test_change_password_double_submit(self, app, auth_client, seed_user):
        """Second password change fails because current password already changed.

        Simulates a double-submit scenario: the first change succeeds,
        making the second attempt's 'current_password' incorrect.
        """
        with app.app_context():
            # First change: testpass -> newpassword12.
            resp1 = auth_client.post("/change-password", data={
                "current_password": "testpass",
                "new_password": "newpassword12",
                "confirm_password": "newpassword12",
            }, follow_redirects=True)
            assert resp1.status_code == 200
            assert b"Password changed successfully" in resp1.data

            # Second change: tries to use "testpass" as current, but it's
            # now "newpassword12".
            resp2 = auth_client.post("/change-password", data={
                "current_password": "testpass",
                "new_password": "anotherpass12",
                "confirm_password": "anotherpass12",
            }, follow_redirects=True)
            assert resp2.status_code == 200
            assert b"Current password is incorrect" in resp2.data

            # Verify the password is still "newpassword12" (not "anotherpass12").
            auth_client.post("/logout")
            login_resp = auth_client.post("/login", data={
                "email": "test@shekel.local",
                "password": "newpassword12",
            }, follow_redirects=False)
            assert login_resp.status_code == 302

    def test_change_password_whitespace_only(self, app, auth_client, seed_user):
        """Whitespace-only password of sufficient length is accepted.

        The auth service validates only minimum length (12 chars) and does
        not strip whitespace from passwords. This test documents that an
        all-spaces password is accepted. Consider adding whitespace
        validation for improved security.
        """
        with app.app_context():
            whitespace_password = "            "  # 12 spaces
            response = auth_client.post("/change-password", data={
                "current_password": "testpass",
                "new_password": whitespace_password,
                "confirm_password": whitespace_password,
            }, follow_redirects=True)

            # App accepts whitespace-only passwords (consider rejecting for security).
            assert response.status_code == 200
            assert b"Password changed successfully" in response.data

            # Verify the whitespace password works for login.
            auth_client.post("/logout")
            login_resp = auth_client.post("/login", data={
                "email": "test@shekel.local",
                "password": whitespace_password,
            }, follow_redirects=False)
            assert login_resp.status_code == 302


class TestMfaSetupEdgeCases:
    """Edge-case tests for the MFA setup/confirm flow.

    Verifies that double-submitting the MFA confirmation code does not
    create duplicate MfaConfig rows or cause errors.
    """

    def test_mfa_confirm_double_submit(self, app, auth_client, seed_user, monkeypatch):
        """Submitting MFA confirmation twice does not create a duplicate MfaConfig.

        Under C-05, the first POST decrypts the pending secret, promotes
        it to ``totp_secret_encrypted``, and clears the pending columns.
        A second POST finds no pending secret and redirects to
        /mfa/setup with the "expired" flash.  Only one MfaConfig row
        should exist, and the existing active credential must NOT be
        re-rotated by the no-op second submission.
        """
        with app.app_context():
            monkeypatch.setattr(mfa_service, "verify_totp_code", lambda s, c: True)

            # Visit setup to write the pending secret to the DB.
            auth_client.get("/mfa/setup")

            # First confirm: promotes pending to active and shows backup codes.
            resp1 = auth_client.post("/mfa/confirm", data={
                "totp_code": "123456",
            })
            assert resp1.status_code == 200
            assert b"Save Your Backup Codes" in resp1.data

            # Snapshot the active credential after the successful confirm.
            config_after_first = db.session.query(MfaConfig).filter_by(
                user_id=seed_user["user"].id
            ).first()
            ciphertext_after_first = config_after_first.totp_secret_encrypted
            confirmed_at_after_first = config_after_first.confirmed_at

            # Second confirm: pending state was cleared; should redirect.
            resp2 = auth_client.post("/mfa/confirm", data={
                "totp_code": "123456",
            }, follow_redirects=False)
            assert resp2.status_code == 302
            assert "mfa/setup" in resp2.headers.get("Location", "")

            # Verify exactly one MfaConfig exists.
            config_count = db.session.query(MfaConfig).filter_by(
                user_id=seed_user["user"].id
            ).count()
            assert config_count == 1

            config = db.session.query(MfaConfig).filter_by(
                user_id=seed_user["user"].id
            ).first()
            assert config.is_enabled is True
            # The second submit must not have rotated or cleared the
            # active credential; pending state is still empty.
            assert config.totp_secret_encrypted == ciphertext_after_first
            assert config.confirmed_at == confirmed_at_after_first
            assert config.pending_secret_encrypted is None
            assert config.pending_secret_expires_at is None
