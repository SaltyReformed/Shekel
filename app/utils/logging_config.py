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
import re
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


# Sensitive-token regex catalogue used by ``SensitiveFieldScrubber``.
#
# Each pattern matches a key=value or key: value or key:"value" form
# where ``key`` is one of the well-known sensitive identifiers we
# never want to see in logs.  The substitution preserves the key (so
# the surrounding message is still readable) and replaces the value
# with the literal token ``[REDACTED]``.
#
# Defense-in-depth, not the primary control.  The codebase already
# routes secret-bearing values exclusively through encrypted DB
# columns or ``flask.session`` (which is signed and never logged),
# and ``log_event`` call sites pass user_id rather than raw secrets.
# The scrubber catches the residual cases: a future caller who
# logs ``request.form`` verbatim, a third-party library that emits
# its own debug line, or an exception traceback that surfaces a
# repr() containing one of these patterns.
#
# The pattern set is deliberately narrow.  Each regex was chosen to
# match real-world serialisation forms (JSON, Python repr,
# URL-encoded form data, HTTP header lines) without trapping benign
# tokens like ``password_hash_column_name``.  Audit reference:
# F-160 / C-16 of the 2026-04-15 security remediation plan.
#
# The KEY group must allow ``[a-z_-]`` so compound keys like
# ``new_password``, ``current-password``, ``totp_code``, and
# ``backup_code_csv`` are caught alongside their bare-form
# counterparts.  An anchor at the start (``\b``) prevents a
# substring match inside an unrelated identifier
# (e.g. ``compassword`` would not match).
#
# Two value forms are alternated:
#   1. Quoted string (single or double).  Greedy through the closing
#      quote so a trailing comma/brace doesn't truncate the redaction.
#   2. Bare run of non-whitespace, non-comma, non-brace, non-quote
#      characters.  Stops at the first delimiter so we don't redact
#      the rest of the line by accident.
_SENSITIVE_KEY_NAMES = (
    # Authentication credentials.  Match both bare ``password`` and
    # the project's ``current_password`` / ``new_password`` /
    # ``confirm_password`` / ``password_hash`` form-field names.
    r"(?:current[_-]|new[_-]|confirm[_-]|old[_-])?password(?:[_-]hash)?",
    # MFA / TOTP secrets and codes.  ``totp_secret`` is the encrypted
    # column name, ``totp_code`` / ``totp`` is the form-field, and
    # ``pending_secret`` is the in-flight enrolment ciphertext key.
    r"totp(?:[_-](?:secret|code|key))?",
    r"pending[_-]secret(?:[_-]encrypted)?",
    # Application crypto material.  ``secret_key`` covers Flask's
    # SECRET_KEY and the explicit ``SECRET_KEY=`` env-style line.
    # ``totp_encryption_key`` and the rotation-key sibling are also
    # covered so an accidental ``app.config`` dump cannot leak them.
    r"secret[_-]key",
    r"totp[_-]encryption[_-]key(?:[_-]old)?",
    # MFA backup codes.  ``backup_code`` (singular) is the form-field
    # accepted at /mfa/verify; ``backup_codes`` (plural) is the JSON
    # array column on auth.mfa_configs.
    r"backup[_-]codes?",
    # Generic credential carriers.  ``cookie`` covers the HTTP
    # ``Set-Cookie`` / ``Cookie`` header forms (with the upper-case
    # ``Cookie`` matching via the ``re.IGNORECASE`` flag).
    # ``authorization`` matches both bare ``Bearer ...`` and
    # ``Basic ...`` header values.  ``api[_-]key`` and ``token``
    # catch the third-party SDK debug-line shape.
    r"cookie",
    r"authorization",
    r"api[_-]key",
    r"access[_-]token",
    r"refresh[_-]token",
    # Persistence URLs that embed credentials in the userinfo segment
    # (postgres://user:pass@host/db).  Catches the common dev-mode
    # leak where an exception traceback shows the connection string.
    r"database[_-]url",
    r"sentry[_-]dsn",
)
_KEY_GROUP = "(?:" + "|".join(_SENSITIVE_KEY_NAMES) + ")"

# A separator between the key and the value: ``=``, ``:``, or
# ``=>`` (Ruby-style hash literal seen in some third-party
# libraries' debug output).  Surrounding whitespace is optional.
_SEP = r"\s*[:=]=?\s*"

_SENSITIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Quoted-value form.  Either single or double quotes; greedy
    # match to the matching closing quote so embedded delimiter
    # chars don't truncate the redaction.  The leading character
    # class on the value rejects ``%`` so a printf-style format
    # string (``password="%s"``) is not redacted -- the format
    # placeholder is preserved and the real value, which lives in
    # ``record.args``, is handled by ``_scrub_args``.
    re.compile(
        r'(?P<key>(?<![A-Za-z0-9_])' + _KEY_GROUP +
        r'\s*["\']?)' + _SEP +
        r'(?P<value>"[^"%][^"]*"|\'[^\'%][^\']*\'|""|\'\')',
        re.IGNORECASE,
    ),
    # Bare-value form.  Stops at whitespace, comma, semicolon, brace,
    # bracket, ampersand, pipe, or quote so we don't swallow the rest
    # of the structured line.  The leading character class rejects
    # ``%`` (preserves printf-style format placeholders) and ``[``
    # (idempotence: the literal ``[REDACTED]`` we substitute in must
    # not itself match the next time the record passes through the
    # filter -- without the ``[`` exclusion, ``password=[REDACTED]``
    # would re-match ``[REDACTED`` and emit ``password=[REDACTED]]``).
    re.compile(
        r'(?P<key>(?<![A-Za-z0-9_])' + _KEY_GROUP +
        r'\s*["\']?)' + _SEP +
        r'(?P<value>[^%\[\s,;}\]&|"\'][^\s,;}\]&|"\']*)',
        re.IGNORECASE,
    ),
)

# Field names whose values should be replaced wholesale when they
# appear as ``extra={...}`` keys on a structured log record.  Unlike
# ``_SENSITIVE_PATTERNS`` (which scrubs string forms inside ``msg``
# and ``args``), this set targets the discrete keys passed via
# ``log_event(..., key=value)`` -- if a future call site accidentally
# passes ``password=raw`` we redact the value before it reaches the
# JSON serialiser.  Match is exact and case-insensitive on the bare
# field name.
_SENSITIVE_EXTRA_FIELDS = frozenset({
    "password",
    "current_password",
    "new_password",
    "confirm_password",
    "old_password",
    "password_hash",
    "totp",
    "totp_code",
    "totp_secret",
    "totp_secret_encrypted",
    "totp_encryption_key",
    "totp_encryption_key_old",
    "pending_secret",
    "pending_secret_encrypted",
    "secret_key",
    "backup_code",
    "backup_codes",
    "cookie",
    "set_cookie",
    "authorization",
    "api_key",
    "access_token",
    "refresh_token",
    "database_url",
    "sentry_dsn",
})

_REDACTED = "[REDACTED]"


def _redact_value(match: "re.Match[str]") -> str:
    """Rewrite a sensitive-token match: keep key+separator, replace value.

    ``match.group(0)`` is the full ``key=value`` substring as it
    appeared in the source text; ``match.start('value')`` is the
    absolute offset where the value begins.  Subtracting
    ``match.start()`` converts that to an offset within the matched
    substring, so slicing up to it preserves whatever the source
    used between the key and the value (``=``, ``:``, ``: ``, an
    opening quote, etc.) without us having to reconstruct it.
    """
    full = match.group(0)
    value_offset = match.start("value") - match.start()
    return full[:value_offset] + _REDACTED


def _scrub_text(text: str) -> str:
    """Return ``text`` with every sensitive key=value pair redacted.

    Iterates the regex catalogue and rewrites each match's value
    segment to the ``[REDACTED]`` literal while preserving the key
    and the separator so a downstream log reader can still see WHICH
    secret was suppressed.  Pure function -- safe to call from the
    logging filter where the calling thread cannot afford an
    exception.
    """
    for pattern in _SENSITIVE_PATTERNS:
        text = pattern.sub(_redact_value, text)
    return text


class SensitiveFieldScrubber(logging.Filter):
    """Redact known sensitive tokens from log messages, args, and extras.

    Three distinct surfaces carry user-supplied or service-internal
    strings into a log record:

      1. ``record.msg`` -- the format string passed to ``logger.log``.
         Almost always a hard-coded literal in this codebase; a
         caller who interpolates a secret directly into the message
         string still gets caught by the pattern scrub.
      2. ``record.args`` -- the positional substitution arguments
         for the format string.  Scrubbed in place but only when
         the arg is itself a string (a non-string arg cannot embed
         a key=value form, and converting ``int`` -> ``str`` would
         break a downstream ``%d`` placeholder).
      3. ``record.__dict__`` -- the structured fields attached via
         ``logger.log(..., extra={...})``.  Two passes:
           * Exact-name match against ``_SENSITIVE_EXTRA_FIELDS``:
             the value is replaced wholesale with ``[REDACTED]``
             regardless of its content.  This is the path that
             catches a future caller who passes ``password=raw``
             via ``log_event``.
           * Pattern scrub against any remaining string-typed
             extras: catches an exception message attached as
             ``error=str(exc)`` that happens to contain one of
             the watched key=value forms.

    The filter MUST mutate in place rather than return a fresh
    record.  ``logging.Filter.filter`` is invoked by every handler
    on the same record instance; returning a new object would only
    apply the redaction to the first handler and let subsequent
    handlers (e.g. a future Sentry handler) see the unscrubbed
    original.

    Designed to be cheap.  No regex compilation per record (patterns
    are module-level), no heap allocation when nothing matches
    (``re.sub`` returns the same string instance on a no-op), and no
    exception path -- a malformed record is logged unchanged rather
    than dropping the line entirely.  Defense-in-depth, not the
    primary control: see the ``_SENSITIVE_PATTERNS`` docstring for
    the threat model.

    Audit reference: F-160 (Info) / C-16 of the 2026-04-15 security
    remediation plan.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Scrub ``record`` in place; always return True."""
        try:
            self._scrub_msg(record)
            self._scrub_args(record)
            self._scrub_extras(record)
        except (TypeError, AttributeError, re.error):
            # A malformed record (msg not a string, args not iterable,
            # regex catastrophic backtracking on adversarial input)
            # is rare enough that swallowing the failure is the right
            # trade-off: the alternative is dropping a line that
            # might be the only signal of an active incident.  The
            # un-scrubbed record still goes out -- an operator
            # auditing logs after a breach would prefer "extra noise"
            # over "missing line".
            pass
        return True

    @staticmethod
    def _scrub_msg(record: logging.LogRecord) -> None:
        """Scrub ``record.msg`` if it's a string."""
        if isinstance(record.msg, str):
            scrubbed = _scrub_text(record.msg)
            if scrubbed is not record.msg:
                record.msg = scrubbed

    @staticmethod
    def _scrub_args(record: logging.LogRecord) -> None:
        """Scrub string members of ``record.args`` in place.

        Tuples and dicts are both valid ``args`` shapes (``%`` vs
        ``%(name)s`` formatting respectively).  Non-string members
        are left untouched: converting an ``int`` to ``str`` here
        would break a ``%d`` placeholder downstream.
        """
        args = record.args
        if not args:
            return
        if isinstance(args, dict):
            for key, value in list(args.items()):
                if isinstance(value, str):
                    args[key] = _scrub_text(value)
            return
        if isinstance(args, tuple):
            new = tuple(
                _scrub_text(a) if isinstance(a, str) else a
                for a in args
            )
            record.args = new

    @staticmethod
    def _scrub_extras(record: logging.LogRecord) -> None:
        """Scrub structured ``extra={...}`` fields on the record.

        ``logging`` deposits every ``extra`` key directly onto
        ``record.__dict__`` without namespacing.  We can't iterate
        the dict and mutate at the same time, so we materialise the
        items first.  Standard ``LogRecord`` attributes (``msg``,
        ``args``, ``levelname``, etc.) are excluded by name so the
        scrubber can't accidentally redact framework metadata.
        """
        for key, value in list(record.__dict__.items()):
            if key in _RESERVED_RECORD_ATTRS:
                continue
            lowered = key.lower()
            if lowered in _SENSITIVE_EXTRA_FIELDS:
                record.__dict__[key] = _REDACTED
                continue
            if isinstance(value, str):
                scrubbed = _scrub_text(value)
                if scrubbed is not value:
                    record.__dict__[key] = scrubbed


# Standard ``LogRecord`` attribute names that the scrubber must NOT
# treat as candidate ``extra`` keys.  Sourced from
# ``logging.LogRecord.__init__`` plus the two attributes injected
# by this module's ``RequestIdFilter`` and the one injected by
# ``logging.Formatter.format`` at render time.  Hard-coded rather
# than introspected so a future Python version that adds attributes
# to ``LogRecord`` cannot silently expand the exclusion set in a
# way that hides newly leaked secret-bearing fields.
_RESERVED_RECORD_ATTRS = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName",
    "taskName",  # Added in Python 3.12
    # Project-specific filter additions:
    "request_id",
})


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

    # Filter order matters.  ``request_id`` runs first to attach the
    # per-request UUID (a non-secret) so that ``sensitive_scrubber``
    # can act on the fully assembled record without later mutating
    # the request_id field by accident.  Both filters are no-ops on
    # records that have no sensitive payload, so the per-record cost
    # of running both is dominated by the dict lookup in
    # ``_scrub_extras``.
    handlers = {
        "console": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "json",
            "filters": ["request_id", "sensitive_scrubber"],
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
            "sensitive_scrubber": {
                "()": SensitiveFieldScrubber,
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
