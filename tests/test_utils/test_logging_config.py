"""Tests for logging configuration enhancements (Phase 8B WU-4).

Verifies X-Request-Id response header, conditional log levels based
on request duration, and standard fields in request summary logs.
"""
import logging
import uuid

from app.extensions import db


class TestRequestIdHeader:
    """Tests for the X-Request-Id response header."""

    def test_response_includes_x_request_id(self, app, client):
        """Every response includes an X-Request-Id header."""
        resp = client.get("/login")
        assert "X-Request-Id" in resp.headers

    def test_x_request_id_is_uuid4(self, app, client):
        """The X-Request-Id header value is a valid UUID4 string."""
        resp = client.get("/login")
        header_val = resp.headers["X-Request-Id"]
        # uuid.UUID() raises ValueError for invalid UUIDs.
        parsed = uuid.UUID(header_val)
        assert parsed.version == 4

    def test_x_request_id_differs_per_request(self, app, client):
        """Each request gets a unique X-Request-Id."""
        resp1 = client.get("/login")
        resp2 = client.get("/login")
        assert resp1.headers["X-Request-Id"] != resp2.headers["X-Request-Id"]


class TestRequestDurationLogLevel:
    """Tests for conditional log levels based on request duration."""

    def test_fast_request_logs_at_debug(self, app, client):
        """Requests under the threshold are logged at DEBUG level."""
        with _capture_log("app.utils.logging_config") as records:
            client.get("/login")
        summaries = [r for r in records if hasattr(r, "event")]
        assert len(summaries) >= 1
        # A simple GET /login should be fast (well under 500ms).
        assert summaries[-1].levelno == logging.DEBUG
        assert summaries[-1].event == "request_complete"

    def test_slow_request_logs_at_warning(self, app, client, monkeypatch):
        """Requests exceeding SLOW_REQUEST_THRESHOLD_MS log at WARNING level.

        The after_request hook in logging_config reads the threshold at
        setup_logging() time.  To test the WARNING path, we create a fresh
        app with the threshold set to 0ms so every request qualifies.
        """
        from app import create_app  # pylint: disable=import-outside-toplevel

        monkeypatch.setenv("SLOW_REQUEST_THRESHOLD_MS", "0")
        slow_app = create_app("testing")
        slow_client = slow_app.test_client()

        with _capture_log("app.utils.logging_config") as records:
            slow_client.get("/login")

        summaries = [r for r in records if hasattr(r, "event")]
        assert len(summaries) >= 1
        assert summaries[-1].levelno == logging.WARNING
        assert summaries[-1].event == "slow_request"

        # Release connections from the secondary app.
        with slow_app.app_context():
            db.engine.dispose()


class TestRequestLogFields:
    """Tests for standard fields in request summary logs."""

    def test_log_includes_remote_addr(self, app, client):
        """Request summary log includes remote_addr field."""
        with _capture_log("app.utils.logging_config") as records:
            client.get("/login")
        summaries = [r for r in records if hasattr(r, "event")]
        assert len(summaries) >= 1
        # Test client always connects from localhost.
        assert summaries[-1].remote_addr == "127.0.0.1"

    def test_log_includes_user_id_when_authenticated(
        self, app, auth_client, seed_user
    ):
        """Request summary log includes user_id for authenticated requests."""
        with _capture_log("app.utils.logging_config") as records:
            auth_client.get("/")
        summaries = [r for r in records if hasattr(r, "event")]
        assert len(summaries) >= 1
        last = summaries[-1]
        assert hasattr(last, "user_id")
        assert last.user_id == seed_user["user"].id

    def test_log_excludes_user_id_when_anonymous(self, app, client):
        """Request summary log does not include user_id for anonymous requests."""
        with _capture_log("app.utils.logging_config") as records:
            client.get("/login")
        summaries = [r for r in records if hasattr(r, "event")]
        assert len(summaries) >= 1
        assert not hasattr(summaries[-1], "user_id")

    def test_log_includes_category_performance(self, app, client):
        """Request summary log includes category='performance'."""
        with _capture_log("app.utils.logging_config") as records:
            client.get("/login")
        summaries = [r for r in records if hasattr(r, "event")]
        assert len(summaries) >= 1
        assert summaries[-1].category == "performance"


# ── Helper ────────────────────────────────────────────────────────────────


class _capture_log:
    """Context manager that captures log records on a named logger."""

    def __init__(self, logger_name):
        self._logger = logging.getLogger(logger_name)
        self.records = []
        self._handler = logging.Handler()
        self._handler.emit = lambda record: self.records.append(record)

    def __enter__(self):
        self._logger.addHandler(self._handler)
        self._orig_level = self._logger.level
        self._logger.setLevel(logging.DEBUG)
        return self.records

    def __exit__(self, *exc):
        self._logger.removeHandler(self._handler)
        self._logger.setLevel(self._orig_level)
