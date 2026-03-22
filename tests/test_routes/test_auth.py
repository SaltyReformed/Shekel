"""
Shekel Budget App -- Auth Route Tests

Tests login, logout, route protection, disabled accounts, rate limiting,
password change, session management, and open redirect prevention.
"""

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
            # Must redirect to the grid index.
            assert location.endswith("/")

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
            assert location.endswith("/")

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

    def test_no_next_redirects_to_grid(self, app, client, seed_user):
        """When no next parameter is provided, the default redirect must be the grid index.

        This is the normal login flow -- no next parameter at all.
        """
        with app.app_context():
            response = client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            }, follow_redirects=False)

            assert response.status_code == 302
            location = response.headers.get("Location", "")
            # Default redirect is the grid index (/).
            assert location.endswith("/")


class TestLogout:
    """Tests for the /logout endpoint."""

    def test_logout_redirects_to_login(self, app, auth_client):
        """GET /logout ends session and redirects."""
        with app.app_context():
            response = auth_client.get("/logout", follow_redirects=False)
            assert response.status_code == 302
            assert "login" in response.headers.get("Location", "")


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
            auth_client.get("/logout")
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
        """POST /mfa/confirm with valid TOTP code enables MFA and shows backup codes."""
        with app.app_context():
            monkeypatch.setattr(mfa_service, "verify_totp_code", lambda s, c: True)

            # Visit setup to store the secret in the session.
            auth_client.get("/mfa/setup")

            # Confirm with a mocked-valid code.
            response = auth_client.post("/mfa/confirm", data={
                "totp_code": "123456",
            })
            assert response.status_code == 200
            assert b"Save Your Backup Codes" in response.data

            # Verify MFA is enabled in the database.
            config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .first()
            )
            assert config is not None
            assert config.is_enabled is True
            assert config.totp_secret_encrypted is not None
            assert config.backup_codes is not None

    def test_mfa_confirm_invalid_code(self, app, auth_client, seed_user, monkeypatch):
        """POST /mfa/confirm with invalid code shows error and redirects."""
        with app.app_context():
            monkeypatch.setattr(mfa_service, "verify_totp_code", lambda s, c: False)

            # Visit setup first.
            auth_client.get("/mfa/setup")

            response = auth_client.post("/mfa/confirm", data={
                "totp_code": "000000",
            }, follow_redirects=True)
            assert response.status_code == 200
            assert b"Invalid code" in response.data

    def test_mfa_confirm_no_session_secret(self, app, auth_client, seed_user):
        """POST /mfa/confirm without setup secret in session shows error."""
        with app.app_context():
            # Post directly without visiting /mfa/setup first.
            response = auth_client.post("/mfa/confirm", data={
                "totp_code": "123456",
            }, follow_redirects=True)
            assert response.status_code == 200
            assert b"MFA setup session expired" in response.data

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

    def test_mfa_confirm_missing_totp_key(self, app, auth_client, seed_user, monkeypatch):
        """POST /mfa/confirm redirects with flash when TOTP key is missing.

        When TOTP_ENCRYPTION_KEY is not set, encrypt_secret() raises
        RuntimeError.  The route must catch this and redirect to security
        settings instead of returning a 500 error.
        """
        with app.app_context():
            monkeypatch.setattr(mfa_service, "verify_totp_code", lambda s, c: True)

            # Visit setup to store the secret in the session.
            auth_client.get("/mfa/setup")

            # Remove the key so encrypt_secret raises RuntimeError.
            monkeypatch.delenv("TOTP_ENCRYPTION_KEY", raising=False)

            response = auth_client.post("/mfa/confirm", data={
                "totp_code": "123456",
            }, follow_redirects=False)
            assert response.status_code == 302
            assert "security" in response.headers.get("Location", "")


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
            client.get("/logout")

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
            # Must redirect to the default grid.
            assert location.endswith("/")

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
            assert location.endswith("/")

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
        self, app, client, seed_user, seed_periods
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
            assert len(categories) == 22

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
            auth_client.get("/logout")
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
            auth_client.get("/logout")
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
            auth_client.get("/logout")
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

        The first POST pops the setup secret from the session. The second
        POST finds no secret and redirects to /mfa/setup with an expiry
        message. Only one MfaConfig row should exist.
        """
        with app.app_context():
            monkeypatch.setattr(mfa_service, "verify_totp_code", lambda s, c: True)

            # Visit setup to store the secret in the session.
            auth_client.get("/mfa/setup")

            # First confirm: enables MFA and shows backup codes.
            resp1 = auth_client.post("/mfa/confirm", data={
                "totp_code": "123456",
            })
            assert resp1.status_code == 200
            assert b"Save Your Backup Codes" in resp1.data

            # Second confirm: session secret was consumed; should redirect.
            resp2 = auth_client.post("/mfa/confirm", data={
                "totp_code": "123456",
            }, follow_redirects=False)
            # Session secret was consumed on first submit; second is rejected.
            assert resp2.status_code == 302
            assert "mfa/setup" in resp2.headers.get("Location", "")

            # Verify exactly one MfaConfig exists and it is enabled.
            config_count = db.session.query(MfaConfig).filter_by(
                user_id=seed_user["user"].id
            ).count()
            assert config_count == 1

            config = db.session.query(MfaConfig).filter_by(
                user_id=seed_user["user"].id
            ).first()
            assert config.is_enabled is True
