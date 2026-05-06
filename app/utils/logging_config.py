"""
Centralized structured logging configuration.

Configures JSON-formatted logging with per-request tracking via
``python-json-logger``.  Call ``setup_logging(app)`` once from the
application factory -- every module that uses
``logging.getLogger(__name__)`` automatically inherits the config.

Architecture (audit Commit C-15 / findings F-082, F-150).  Logs are
emitted as JSON to stdout only.  The runtime container's stdout is
captured by Docker's ``json-file`` driver; an off-host Grafana Alloy
collector reads the container log stream via the Docker socket,
parses each JSON record (using the ``timestamp``, ``level``,
``logger``, ``request_id``, and ``event`` fields produced here), and
ships the records to a Loki instance running on a separate Docker
network with its own storage volume.  Tamper-resistance comes from
that network/volume isolation: a runtime-app compromise can write
new log lines but cannot edit lines already shipped to Loki.

There is intentionally NO local file handler.  An earlier revision
of this module wrote a rotating file under ``/home/shekel/app/logs``
(volume ``applogs``); that volume was rewritable by the same
container and so could not satisfy ASVS V7.3.3 / V7.3.4.  Removing
the file handler eliminates the tamper-window without losing
short-term local logs -- Docker's ``json-file`` driver still keeps
one rotation window of stdout on the host's filesystem so an
operator without Loki access can still ``docker logs <container>``.
"""

import datetime as _dt
import logging
import logging.config
import os
import time
import uuid

from flask import Flask, g, request
from sqlalchemy.exc import SQLAlchemyError

from pythonjsonlogger.json import JsonFormatter


class RFC3339JsonFormatter(JsonFormatter):
    """JSON formatter whose ``asctime`` field is RFC3339Nano with Z.

    The structured log pipeline assumes ``timestamp`` values are
    RFC3339Nano (Go's ``2006-01-02T15:04:05.999999999Z07:00``
    layout).  Grafana Alloy's ``stage.timestamp`` step is configured
    with ``format = "RFC3339Nano"`` and refuses records whose
    timestamp it cannot parse, so the format is load-bearing for log
    ingestion -- not cosmetic.

    Two upstream constraints push the implementation here.

    First, ``logging.Formatter.formatTime`` ultimately calls
    ``time.strftime``, which under glibc does not implement ``%f``
    for sub-second precision.  Without an override, the best we get
    is ``%Y-%m-%d %H:%M:%S,%03d`` (millisecond precision, comma
    separator, no timezone) -- not RFC3339 at all.  Overriding
    ``formatTime`` with ``datetime.fromtimestamp(record.created,
    tz=timezone.utc).isoformat(timespec="microseconds")`` produces
    microsecond precision and a real ``+00:00`` offset that the
    rename-to-Z step below can normalise.

    Second, ``datetime.isoformat`` writes ``+00:00`` for UTC, but
    Loki's parser is strict about RFC3339Nano which accepts ``Z`` or
    ``+HH:MM`` interchangeably; the Z form is the canonical one in
    most modern observability tooling and is what the operator sees
    in Grafana's autocomplete.  Replacing ``+00:00`` with ``Z`` once
    here avoids per-query ``date(... fmt=...)`` rewrites everywhere
    the timestamp is referenced.

    The ``rename_fields`` dict in ``setup_logging`` then maps
    ``asctime -> timestamp`` so the rendered key matches what the
    Alloy ``stage.json`` config expects.
    """

    def formatTime(self, record, datefmt=None):
        """Return the record's creation time as an RFC3339Nano string.

        ``datefmt`` is intentionally ignored.  The pipeline depends on a
        single canonical timestamp format; allowing per-formatter
        overrides would create silent drift between handlers.

        Args:
            record: The ``LogRecord`` whose ``created`` attribute (a
                Unix timestamp produced by ``time.time()``) we format.
            datefmt: Accepted for compatibility with the
                ``logging.Formatter`` signature; not consulted.

        Returns:
            ISO-8601 RFC3339Nano string with microsecond precision
            and a ``Z`` suffix for UTC, e.g.
            ``"2026-05-05T19:36:45.139287Z"``.
        """
        del datefmt  # see docstring -- intentionally unused.
        return (
            _dt.datetime.fromtimestamp(record.created, tz=_dt.timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )


class RequestIdFilter(logging.Filter):
    """Inject ``request_id`` into every log record."""

    def filter(self, record):
        try:
            record.request_id = getattr(g, "request_id", "no-request")
        except RuntimeError:
            # Outside application context entirely (CLI, workers).
            record.request_id = "no-request"
        return True


def _resolve_log_level(app: Flask) -> str:
    explicit = os.getenv("LOG_LEVEL")
    if explicit:
        return explicit.upper()
    if os.getenv("FLASK_DEBUG") == "1" or app.debug:
        return "DEBUG"
    return "INFO"


def setup_logging(app: Flask) -> None:
    """Configure structured JSON logging for the application.

    Emits every log record as a single JSON object on stdout with a
    fixed key set: ``timestamp`` (RFC3339Nano UTC), ``level``,
    ``logger``, ``message``, ``request_id``, plus any structured
    ``extra={...}`` fields supplied by the caller (typically
    ``event``, ``category``, ``user_id``, ``path``, etc.).  See the
    module docstring for the off-host shipping architecture and the
    motivation for the stdout-only sink.
    """

    level = _resolve_log_level(app)

    handlers = {
        "console": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "json",
            "filters": ["request_id"],
            "level": level,
        },
    }

    # The dictConfig ``()`` form constructs the formatter via
    # ``RFC3339JsonFormatter(**kwargs)``.  The earlier ``class:`` form
    # routes through Python's logging.Formatter constructor, which
    # silently drops the ``rename_fields`` and ``timestamp`` kwargs --
    # the JSON output then carries ``levelname``/``name`` instead of
    # the renamed ``level``/``logger`` keys the Alloy parser expects.
    # This is the actual bug behind observability.md's "structured
    # fields are missing" symptom.
    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "request_id": {
                "()": RequestIdFilter,
            },
        },
        "formatters": {
            "json": {
                "()": "app.utils.logging_config.RFC3339JsonFormatter",
                # ``%(asctime)s`` makes asctime a "required field"
                # in the formatter, which triggers the
                # ``RFC3339JsonFormatter.formatTime`` override above.
                # The rename below maps it to ``timestamp`` in the
                # serialised JSON so the Alloy parser's
                # ``ts = "timestamp"`` mapping resolves cleanly.
                "format": (
                    "%(asctime)s %(levelname)s %(name)s %(message)s"
                ),
                "rename_fields": {
                    "asctime": "timestamp",
                    "levelname": "level",
                    "name": "logger",
                },
            },
        },
        "handlers": handlers,
        "root": {
            "level": level,
            "handlers": list(handlers.keys()),
        },
    }

    logging.config.dictConfig(config)

    # --- Per-request hooks ------------------------------------------------

    @app.before_request
    def _attach_request_id():
        # Skip request tracking for health checks (avoid log noise
        # from frequent Docker/monitoring polls).
        if request.path == "/health":
            g.skip_request_logging = True
            return
        g.skip_request_logging = False

        g.request_id = str(uuid.uuid4())
        g.request_start = time.perf_counter()

        # Propagate the application user_id into the PostgreSQL session
        # so audit triggers can capture who made the change.
        # Uses SET LOCAL (transaction-scoped, not session-scoped).
        try:
            from flask_login import current_user  # pylint: disable=import-outside-toplevel
            if current_user.is_authenticated:
                from app.extensions import db  # pylint: disable=import-outside-toplevel
                db.session.execute(
                    db.text("SET LOCAL app.current_user_id = :uid"),
                    {"uid": str(current_user.id)},
                )
        except (RuntimeError, AttributeError, SQLAlchemyError):
            # RuntimeError: outside application/request context.
            # AttributeError: anonymous user proxy has no 'id'.
            # SQLAlchemyError: SET LOCAL fails on broken DB session.
            pass

    # Slow request threshold in milliseconds (configurable via env var).
    slow_threshold_ms = float(os.getenv("SLOW_REQUEST_THRESHOLD_MS", "500"))

    @app.after_request
    def _log_request_summary(response):
        # Skip logging for health checks and other excluded paths.
        if getattr(g, "skip_request_logging", False):
            return response

        duration_ms = (time.perf_counter() - g.request_start) * 1000
        lgr = logging.getLogger(__name__)

        # Return request_id to the client for debugging.
        response.headers["X-Request-Id"] = g.request_id

        # Determine log level based on request duration.
        if duration_ms >= slow_threshold_ms:
            level = logging.WARNING
            event = "slow_request"
        else:
            level = logging.DEBUG
            event = "request_complete"

        # Build structured extra fields.
        extra_fields = {
            "event": event,
            "category": "performance",
            "method": request.method,
            "path": request.path,
            "status": response.status_code,
            "request_duration": round(duration_ms, 2),
            "remote_addr": request.remote_addr,
        }

        try:
            from flask_login import current_user  # pylint: disable=import-outside-toplevel
            if current_user.is_authenticated:
                extra_fields["user_id"] = current_user.id
        except (RuntimeError, AttributeError):
            # RuntimeError: outside application/request context.
            # AttributeError: anonymous user proxy has no 'is_authenticated'.
            pass

        lgr.log(
            level,
            "%s %s %s",
            request.method,
            request.path,
            response.status_code,
            extra=extra_fields,
        )
        return response
