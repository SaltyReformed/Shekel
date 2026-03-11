"""
Shekel Budget App — Auth Route Tests

Tests login, logout, route protection, disabled accounts, rate limiting,
password change, and session management.
"""

from datetime import datetime, timedelta, timezone

from app import create_app
from app.extensions import db
from app.models.user import MfaConfig, User
from app.services import mfa_service
from app.services.auth_service import hash_password


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

            # Reset limiter for other tests.
            limiter.enabled = False


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
            response = auth_client.post(
                "/invalidate-sessions", follow_redirects=True
            )

            assert response.status_code == 200
            assert b"All other sessions have been logged out" in response.data

            # Reload user from database and verify timestamp was set.
            user = db.session.get(User, seed_user["user"].id)
            assert user.session_invalidated_at is not None

    def test_invalidate_sessions_current_session_survives(self, app, auth_client, seed_user):
        """Current session remains valid after invalidation."""
        with app.app_context():
            auth_client.post("/invalidate-sessions")

            # The current session should still work — not redirected to login.
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
            auth_client.post("/change-password", data={
                "current_password": "testpass",
                "new_password": "newpassword12",
                "confirm_password": "newpassword12",
            })

            # Reload user and verify session_invalidated_at was set.
            user = db.session.get(User, seed_user["user"].id)
            assert user.session_invalidated_at is not None

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

            # User should NOT be logged in — a protected page should redirect.
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

            # User is now logged in — protected page should return 200.
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
