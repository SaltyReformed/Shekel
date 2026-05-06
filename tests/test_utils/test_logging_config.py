"""Tests for logging configuration enhancements (Phase 8B WU-4 + C-15).

Verifies X-Request-Id response header, conditional log levels based
on request duration, standard fields in request summary logs, and
the C-15 RFC3339JsonFormatter wire format that the off-host Loki
pipeline depends on (audit findings F-082, F-150).
"""
import io
import json
import logging
import logging.config
import re
import time
import uuid

from app.extensions import db
from app.utils.logging_config import RFC3339JsonFormatter


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


class TestRFC3339JsonFormatter:
    """Tests for the C-15 ``RFC3339JsonFormatter`` wire format.

    These tests pin the JSON shape that Grafana Alloy's
    ``loki.process.shekel`` config depends on.  A regression in the
    field names or in the timestamp format would break log ingestion
    silently -- Alloy would discard the records as malformed and the
    operator would notice only when a Loki query stops returning
    rows.
    """

    # RFC3339Nano with Z suffix and microsecond precision, e.g.
    # "2026-05-05T19:36:45.139287Z".  The pattern is intentionally
    # strict: the alternative formats datetime.isoformat() can emit
    # (no fractional seconds, +00:00 instead of Z) would be parseable
    # by Loki but break the stable-sort guarantee dashboards depend
    # on, so we forbid them here.
    _RFC3339_NANO_Z_PATTERN = re.compile(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$"
    )

    @staticmethod
    def _emit_one(message="hello", **extra):
        """Return the parsed JSON dict for one log record.

        Builds a fresh dictConfig identical in shape to
        ``setup_logging`` (without the Flask request hooks, so the
        formatter can be exercised in isolation), captures the
        single emitted line on a buffer, parses it, and returns the
        dict.  The helper exists so each test reads as one
        setup-act-assert block instead of repeating the boilerplate.
        """
        buf = io.StringIO()

        # Use a fresh logger name per call so prior handlers from
        # other tests do not duplicate-emit on the buffer.
        logger_name = f"test.rfc3339.{uuid.uuid4().hex}"
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.DEBUG)
        # Strip any inherited handlers to keep the buffer
        # deterministic.
        for old in list(logger.handlers):
            logger.removeHandler(old)
        # Disable propagation so the captured record does not bubble
        # up to a root handler that some other test wired to
        # sys.stdout (which would not break the assertions but would
        # spam test output).
        logger.propagate = False

        handler = logging.StreamHandler(buf)
        handler.setFormatter(
            RFC3339JsonFormatter(
                fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
                rename_fields={
                    "asctime": "timestamp",
                    "levelname": "level",
                    "name": "logger",
                },
            )
        )
        logger.addHandler(handler)

        logger.info(message, extra=extra)

        line = buf.getvalue().strip()
        return json.loads(line)

    def test_timestamp_field_is_present(self):
        """Output JSON contains a ``timestamp`` key (renamed from asctime)."""
        record = self._emit_one()
        assert "timestamp" in record, (
            f"Expected ``timestamp`` field, got keys: {sorted(record.keys())}"
        )

    def test_timestamp_format_is_rfc3339_nano_with_z(self):
        """``timestamp`` is RFC3339Nano with microsecond precision and a Z suffix.

        Loki's ``stage.timestamp { format = "RFC3339Nano" }`` parser
        accepts the broader ``Z07:00`` layout (Z or +HH:MM offset),
        but Alloy displays the original string in Grafana's log line
        view, so the canonical ``Z`` form is the one operators see.
        Pinning it here prevents a future formatter swap from
        silently switching to ``+00:00``.
        """
        record = self._emit_one()
        ts = record["timestamp"]
        assert self._RFC3339_NANO_Z_PATTERN.match(ts), (
            f"Timestamp {ts!r} does not match RFC3339Nano-with-Z pattern. "
            "Expected e.g. '2026-05-05T19:36:45.139287Z'."
        )

    def test_timestamp_reflects_record_creation_time(self):
        """The emitted ``timestamp`` is within ~5 seconds of now.

        Sanity check that the formatter did not freeze on a constant
        or an epoch-zero default; the off-host pipeline relies on
        the timestamp being the actual record-creation moment so
        log replay ordering is meaningful.
        """
        before = time.time()
        record = self._emit_one()
        after = time.time()

        # ISO-8601 with the trailing Z is not directly parseable by
        # ``datetime.fromisoformat`` on Python 3.10; replace Z with
        # +00:00 so we can reuse the stdlib parser.
        from datetime import datetime as _dt
        parsed = _dt.fromisoformat(record["timestamp"].replace("Z", "+00:00"))
        epoch = parsed.timestamp()
        assert before - 5 <= epoch <= after + 5, (
            f"Timestamp {record['timestamp']!r} ({epoch}) is more than 5 "
            f"seconds outside the test window [{before}, {after}]."
        )

    def test_level_field_is_present_and_renamed(self):
        """Output uses ``level`` (renamed from ``levelname``)."""
        record = self._emit_one()
        assert record.get("level") == "INFO", (
            f"Expected level='INFO', got {record!r}"
        )
        assert "levelname" not in record, (
            "Raw ``levelname`` key leaked into the rendered record. "
            "The Alloy parser keys off ``level``; the unrenamed field "
            "must be absent."
        )

    def test_logger_field_is_present_and_renamed(self):
        """Output uses ``logger`` (renamed from ``name``)."""
        record = self._emit_one()
        assert "logger" in record, (
            f"Expected ``logger`` key in output, got: {sorted(record.keys())}"
        )
        assert record["logger"].startswith("test.rfc3339."), (
            f"Logger field {record['logger']!r} does not match the "
            "fresh-name convention used in this test fixture."
        )
        assert "name" not in record, (
            "Raw ``name`` key leaked into the rendered record."
        )

    def test_message_field_is_human_readable(self):
        """``message`` carries the rendered log message."""
        record = self._emit_one(message="user logged in")
        assert record["message"] == "user logged in"

    def test_structured_extras_are_propagated(self):
        """``extra={...}`` kwargs appear as top-level keys in the JSON."""
        record = self._emit_one(
            event="login_success", category="auth", user_id=42,
        )
        assert record["event"] == "login_success"
        assert record["category"] == "auth"
        assert record["user_id"] == 42

    def test_emits_a_single_json_object_per_line(self):
        """Each record serialises to exactly one JSON object on one line.

        Alloy's docker-source step assumes one log line == one record;
        a multi-line emit would split the structured fields across
        two ingestion records and stop label extraction working.
        """
        # Re-do the emit so we can inspect the raw buffer (the
        # ``_emit_one`` helper already parses).
        buf = io.StringIO()
        logger = logging.getLogger(f"test.line.{uuid.uuid4().hex}")
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        handler = logging.StreamHandler(buf)
        handler.setFormatter(RFC3339JsonFormatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            rename_fields={
                "asctime": "timestamp",
                "levelname": "level",
                "name": "logger",
            },
        ))
        logger.addHandler(handler)

        logger.info("inline message")

        text = buf.getvalue()
        # logging adds exactly one trailing newline per record; strip
        # it before counting.
        assert text.endswith("\n"), (
            "logging.StreamHandler should append a trailing newline."
        )
        body = text.rstrip("\n")
        assert "\n" not in body, (
            "Record body contains an embedded newline -- multi-line "
            f"JSON breaks log ingestion: {body!r}"
        )
        # Round-trips as JSON.
        parsed = json.loads(body)
        assert parsed["message"] == "inline message"


class TestSetupLoggingDictConfig:
    """End-to-end: ``setup_logging`` wires the right formatter on the right handler."""

    def test_root_handler_uses_rfc3339_formatter(self, app):
        """The root logger's handler is configured with our formatter.

        Guards against a future refactor that switches back to the
        ``class:`` form in dictConfig, which would silently downgrade
        to a plain ``logging.Formatter`` with no JSON output.
        """
        root = logging.getLogger()
        # Find the StreamHandler the configurator installed.
        sh = next(
            (h for h in root.handlers if isinstance(h, logging.StreamHandler)),
            None,
        )
        assert sh is not None, (
            "setup_logging did not install a StreamHandler on the root logger."
        )
        assert isinstance(sh.formatter, RFC3339JsonFormatter), (
            "StreamHandler formatter is "
            f"{type(sh.formatter).__name__}, not RFC3339JsonFormatter -- "
            "did dictConfig regress to ``class:`` form?  See C-15."
        )

    def test_request_id_filter_attached(self, app):
        """The console handler carries the request_id filter."""
        from app.utils.logging_config import RequestIdFilter
        root = logging.getLogger()
        sh = next(
            (h for h in root.handlers if isinstance(h, logging.StreamHandler)),
            None,
        )
        assert sh is not None
        assert any(
            isinstance(f, RequestIdFilter) for f in sh.filters
        ), "RequestIdFilter is missing from the console handler."

    def test_no_local_file_handler_attached(self, app):
        """No FileHandler is wired up under C-15.

        F-150 tagged the rotating-file-on-volume handler as tamper-
        prone; C-15 removed it in favour of stdout-only emission
        with off-host shipping via Alloy/Loki.  This test pins the
        removal so a future debugging session cannot accidentally
        re-introduce a tamper-prone local sink.
        """
        from logging.handlers import (  # pylint: disable=import-outside-toplevel
            BaseRotatingHandler,
        )
        root = logging.getLogger()
        for handler in root.handlers:
            assert not isinstance(handler, logging.FileHandler), (
                f"Found a FileHandler ({handler!r}) on the root logger -- "
                "C-15 mandates stdout-only logging.  See "
                "app/utils/logging_config.py module docstring."
            )
            assert not isinstance(handler, BaseRotatingHandler), (
                f"Found a rotating file handler ({handler!r}) -- forbidden "
                "by C-15."
            )


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
