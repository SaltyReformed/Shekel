"""Tests for the /health endpoint."""

import logging
from unittest.mock import patch

from app.extensions import db


class TestHealthEndpoint:
    """Tests for GET /health."""

    def test_health_returns_200_when_healthy(self, app, client, db):
        """GET /health returns 200 with healthy status when DB is reachable."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "healthy"
        assert data["database"] == "connected"

    def test_health_returns_json_content_type(self, app, client, db):
        """GET /health returns application/json content type."""
        response = client.get("/health")
        assert "application/json" in response.content_type

    def test_health_requires_no_authentication(self, app, client, db):
        """GET /health is accessible without login (client is not authenticated)."""
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_returns_500_on_db_failure(self, app, client, db):
        """GET /health returns 500 with error details when DB is unreachable."""
        with patch.object(
            db.session, "execute", side_effect=Exception("connection refused")
        ):
            response = client.get("/health")
            assert response.status_code == 500
            data = response.get_json()
            assert data["status"] == "unhealthy"
            assert data["database"] == "error"
            assert "connection refused" in data["detail"]

    def test_health_not_logged_in_request_summary(self, app, client, db, caplog):
        """GET /health does not produce a request_complete log entry."""
        with caplog.at_level(logging.DEBUG):
            client.get("/health")
        # The request logging middleware skips /health.
        request_logs = [
            r for r in caplog.records
            if hasattr(r, "event")
            and r.event in ("request_complete", "slow_request")
            and hasattr(r, "path")
            and r.path == "/health"
        ]
        assert len(request_logs) == 0

    def test_health_no_request_id_header(self, app, client, db):
        """GET /health does not return X-Request-Id header (logging skipped)."""
        response = client.get("/health")
        assert "X-Request-Id" not in response.headers
