"""
Shekel Budget App — Auth Route Tests

Tests login, logout, and route protection.
"""


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


class TestLogout:
    """Tests for the /logout endpoint."""

    def test_logout_redirects_to_login(self, app, auth_client):
        """GET /logout ends session and redirects."""
        with app.app_context():
            response = auth_client.get("/logout", follow_redirects=False)
            assert response.status_code == 302
            assert "login" in response.headers.get("Location", "")
