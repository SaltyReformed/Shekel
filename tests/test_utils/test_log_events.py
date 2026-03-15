"""Tests for app.utils.log_events (Phase 8B WU-3)."""
import logging

from app.utils.log_events import AUTH, BUSINESS, ERROR, PERFORMANCE, log_event


class TestLogEvent:
    """Tests for the log_event() helper."""

    def test_log_event_emits_at_correct_level(self):
        """log_event() calls logger.log() with the specified level."""
        test_logger = logging.getLogger("test.log_events")
        with _LogCapture(test_logger) as cap:
            log_event(test_logger, logging.WARNING, "test_evt", AUTH, "msg")
        assert cap.records[0].levelno == logging.WARNING

    def test_log_event_includes_event_field(self):
        """log_event() includes 'event' in the extra dict."""
        test_logger = logging.getLogger("test.log_events.event")
        with _LogCapture(test_logger) as cap:
            log_event(test_logger, logging.INFO, "my_event", AUTH, "msg")
        assert cap.records[0].event == "my_event"

    def test_log_event_includes_category_field(self):
        """log_event() includes 'category' in the extra dict."""
        test_logger = logging.getLogger("test.log_events.category")
        with _LogCapture(test_logger) as cap:
            log_event(test_logger, logging.INFO, "evt", BUSINESS, "msg")
        assert cap.records[0].category == "business"

    def test_log_event_includes_extra_kwargs(self):
        """log_event() passes additional kwargs into the extra dict."""
        test_logger = logging.getLogger("test.log_events.extra")
        with _LogCapture(test_logger) as cap:
            log_event(test_logger, logging.INFO, "evt", AUTH, "msg",
                      user_id=42, ip="1.2.3.4")
        assert cap.records[0].user_id == 42
        assert cap.records[0].ip == "1.2.3.4"

    def test_log_event_message_is_human_readable(self):
        """log_event() passes the message string to the logger."""
        test_logger = logging.getLogger("test.log_events.msg")
        with _LogCapture(test_logger) as cap:
            log_event(test_logger, logging.INFO, "evt", AUTH,
                      "User logged in")
        assert cap.records[0].getMessage() == "User logged in"


class TestEventCategories:
    """Tests for event category constants."""

    def test_auth_category_is_string(self):
        """AUTH constant is a string."""
        assert isinstance(AUTH, str)
        assert AUTH == "auth"

    def test_business_category_is_string(self):
        """BUSINESS constant is a string."""
        assert isinstance(BUSINESS, str)
        assert BUSINESS == "business"

    def test_error_category_is_string(self):
        """ERROR constant is a string."""
        assert isinstance(ERROR, str)
        assert ERROR == "error"

    def test_performance_category_is_string(self):
        """PERFORMANCE constant is a string."""
        assert isinstance(PERFORMANCE, str)
        assert PERFORMANCE == "performance"


# ── Helper ────────────────────────────────────────────────────────────────


class _LogCapture:
    """Context manager that captures log records on a logger."""

    def __init__(self, logger):
        self._logger = logger
        self.records = []
        self._handler = logging.Handler()
        self._handler.emit = lambda record: self.records.append(record)

    def __enter__(self):
        self._logger.addHandler(self._handler)
        self._logger.setLevel(logging.DEBUG)
        return self

    def __exit__(self, *exc):
        self._logger.removeHandler(self._handler)
