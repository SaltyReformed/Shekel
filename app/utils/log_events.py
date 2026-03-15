"""
Shekel Budget App -- Structured Log Event Definitions

Defines standardized event categories and a helper for emitting
structured log entries with consistent fields.  All log events
include the event name and category as structured ``extra`` fields,
making them filterable in Grafana/Loki.
"""
import logging


# ---------------------------------------------------------------------------
# Event category constants
# ---------------------------------------------------------------------------

AUTH = "auth"
BUSINESS = "business"
ERROR = "error"
PERFORMANCE = "performance"


# ---------------------------------------------------------------------------
# Structured logging helper
# ---------------------------------------------------------------------------


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    category: str,
    message: str,
    **extra,
):
    """Emit a structured log entry with standardized fields.

    Args:
        logger: The logger instance (typically ``logging.getLogger(__name__)``).
        level: Logging level (e.g., ``logging.INFO``).
        event: Machine-readable event name (e.g., ``"login_success"``).
        category: Event category (``AUTH``, ``BUSINESS``, ``ERROR``,
                  ``PERFORMANCE``).
        message: Human-readable description.
        **extra: Additional key-value pairs included in the JSON output.
    """
    logger.log(
        level,
        message,
        extra={"event": event, "category": category, **extra},
    )
