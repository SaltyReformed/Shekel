"""Tests for custom error pages and production configuration."""

import pytest

from app import create_app
from app.config import BaseConfig, ProdConfig


class TestErrorPages:
    """Tests for custom error page rendering and production config."""

    def test_404_renders_custom_page(self, app, auth_client):
        """GET /nonexistent-path returns 404 with custom template."""
        response = auth_client.get("/this-page-does-not-exist")
        assert response.status_code == 404
        html = response.data.decode()
        assert "Page Not Found" in html

    def test_404_contains_navigation(self, app, auth_client):
        """404 page contains a link back to the budget grid."""
        response = auth_client.get("/this-page-does-not-exist")
        html = response.data.decode()
        assert "/grid" in html
        assert "Back to Budget Grid" in html

    def test_429_renders_custom_page(self, app, seed_user):
        """Rate-limited request returns 429 with custom template."""
        with app.app_context():
            # Create a fresh app with rate limiting enabled
            # (TestConfig disables it).
            rate_app = create_app("testing")
            rate_app.config["RATELIMIT_ENABLED"] = True

            from app.extensions import limiter  # pylint: disable=import-outside-toplevel
            limiter.enabled = True
            limiter.init_app(rate_app)

            rate_client = rate_app.test_client()

            with rate_app.app_context():
                # Exceed the 5-per-15-minutes login rate limit.
                for _ in range(6):
                    response = rate_client.post("/login", data={
                        "email": "test@shekel.local",
                        "password": "wrongpassword",
                    })

                # The last response is guaranteed to be rate-limited.
                assert response.status_code == 429
                html = response.data.decode()
                assert "Too Many Requests" in html

            # Clean up: dispose the secondary app's engine to release
            # connections, and reset limiter for other tests.
            with rate_app.app_context():
                from app.extensions import db as _db  # pylint: disable=import-outside-toplevel
                _db.engine.dispose()
            limiter.enabled = False

    def test_429_includes_retry_after_header(self, app, seed_user):
        """429 response includes Retry-After header set to 900."""
        with app.app_context():
            rate_app = create_app("testing")
            rate_app.config["RATELIMIT_ENABLED"] = True

            from app.extensions import limiter  # pylint: disable=import-outside-toplevel
            limiter.enabled = True
            limiter.init_app(rate_app)

            rate_client = rate_app.test_client()

            with rate_app.app_context():
                for _ in range(6):
                    response = rate_client.post("/login", data={
                        "email": "test@shekel.local",
                        "password": "wrongpassword",
                    })

                assert response.status_code == 429
                assert response.headers["Retry-After"] == "900"

            # Clean up: dispose the secondary app's engine to release
            # connections, and reset limiter for other tests.
            with rate_app.app_context():
                from app.extensions import db as _db  # pylint: disable=import-outside-toplevel
                _db.engine.dispose()
            limiter.enabled = False

    def test_500_renders_custom_page(self):
        """500 error returns the custom error template."""
        # Create a fresh app so we can register a test route before any
        # requests are handled (Flask forbids late route registration).
        error_app = create_app("testing")
        error_app.config["PROPAGATE_EXCEPTIONS"] = False

        @error_app.route("/test-500-trigger")
        def trigger_500():
            """Intentional error for testing the 500 handler."""
            raise RuntimeError("Intentional test error")

        error_client = error_app.test_client()

        with error_app.app_context():
            response = error_client.get("/test-500-trigger")
            assert response.status_code == 500
            html = response.data.decode()
            assert "Something Went Wrong" in html

        # Dispose the secondary app's engine to release connections.
        with error_app.app_context():
            from app.extensions import db as _db  # pylint: disable=import-outside-toplevel
            _db.engine.dispose()

    def test_production_debug_false(self, monkeypatch):
        """ProdConfig has DEBUG=False."""
        # Class attributes are set at import time; patch them directly.
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", "secure-production-key-1234")
        monkeypatch.setattr(
            ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql://localhost/shekel"
        )
        config = ProdConfig()
        assert config.DEBUG is False

    def test_production_validates_secret_key(self, monkeypatch):
        """ProdConfig raises ValueError for default secret key."""
        monkeypatch.setattr(
            BaseConfig, "SECRET_KEY", "dev-only-change-me-in-production"
        )
        monkeypatch.setattr(
            ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql://localhost/shekel"
        )
        with pytest.raises(ValueError, match="SECRET_KEY"):
            ProdConfig()
