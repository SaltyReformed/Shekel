"""
Centralized structured logging configuration.

Configures JSON-formatted logging with per-request tracking via
``python-json-logger``.  Call ``setup_logging(app)`` once from the
application factory — every module that uses
``logging.getLogger(__name__)`` automatically inherits the config.
"""

import logging
import logging.config
import os
import time
import uuid

from flask import Flask, g, request

from pythonjsonlogger.json import JsonFormatter  # noqa: F401 — used in dictConfig


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
    """Configure structured JSON logging for the application."""

    level = _resolve_log_level(app)
    testing = app.config.get("TESTING", False)

    handlers = {
        "console": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "json",
            "filters": ["request_id"],
            "level": level,
        },
    }

    if not testing:
        os.makedirs("logs", exist_ok=True)
        handlers["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": "logs/budget_app.log",
            "maxBytes": 10_485_760,  # 10 MB
            "backupCount": 5,
            "formatter": "json",
            "filters": ["request_id"],
            "level": level,
        }

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
                "class": "pythonjsonlogger.json.JsonFormatter",
                "format": "%(levelname)s %(name)s %(message)s",
                "rename_fields": {
                    "levelname": "level",
                    "name": "logger",
                },
                "timestamp": True,
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
        g.request_id = str(uuid.uuid4())
        g.request_start = time.perf_counter()

    @app.after_request
    def _log_request_summary(response):
        duration_ms = (time.perf_counter() - g.request_start) * 1000
        logger = logging.getLogger(__name__)
        logger.info(
            "%s %s %s",
            request.method,
            request.path,
            response.status_code,
            extra={
                "method": request.method,
                "path": request.path,
                "status": response.status_code,
                "request_duration": round(duration_ms, 2),
            },
        )
        return response
