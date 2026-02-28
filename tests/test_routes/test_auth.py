"""
Shekel Budget App — Auth Route Tests

Tests login, logout, route protection, disabled accounts, and rate limiting.
"""

from app import create_app
from app.extensions import db
from app.models.user import User
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
