"""Tests for ``SensitiveFieldScrubber`` (audit F-160 / commit C-16).

The scrubber is defense-in-depth: in normal operation no log message
should embed a secret in the first place (``log_event`` call sites
pass user_id rather than raw passwords / TOTP codes).  These tests
exercise the surface that catches the residual cases -- a future call
site that logs ``request.form`` verbatim, a third-party library that
emits its own debug line, or an exception traceback that surfaces a
``repr()`` containing one of the watched key=value forms.

Each test exercises a single redaction surface (``record.msg``,
``record.args``, ``record.__dict__`` for structured extras) so a
regression is locatable to a specific code path.  The end-to-end test
at the bottom asserts that ``setup_logging`` actually wires the filter
on the production handler, guarding against a future refactor that
defines the filter but forgets to register it.
"""
import io
import json
import logging
import uuid

from app.utils.logging_config import (
    RFC3339JsonFormatter,
    SensitiveFieldScrubber,
    _redact_value,
    _scrub_text,
)


def _make_record(msg, args=None, **extras):
    """Build a ``LogRecord`` matching what ``logger.log`` would create.

    The scrubber operates on records, not on the logger itself, so
    constructing records directly keeps each test focused on the
    filter's behaviour without coupling to the logging-config wiring.

    ``args`` is set AFTER construction (rather than via the kwarg)
    because ``LogRecord.__init__`` collapses a one-element tuple
    containing a Mapping into ``args = mapping_member`` and indexes
    ``args[0]`` to verify the shape.  Passing ``args=somedict``
    directly trips that path with a ``KeyError(0)`` on Python 3.12+
    where the check became strict.  Setting the attribute after
    construction bypasses the collapse and matches what
    ``Logger.log`` does internally for the dict-args case.
    """
    record = logging.LogRecord(
        name="test.scrubber",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg=msg,
        args=None,
        exc_info=None,
    )
    if args is not None:
        record.args = args
    for key, value in extras.items():
        setattr(record, key, value)
    return record


class TestScrubMessage:
    """Patterns are redacted in ``record.msg`` regardless of source form."""

    def test_bare_password_assignment_redacted(self):
        """A ``password=secret`` substring becomes ``password=[REDACTED]``."""
        scrubber = SensitiveFieldScrubber()
        record = _make_record("login attempt password=hunter2 from cli")

        scrubber.filter(record)

        assert record.msg == "login attempt password=[REDACTED] from cli"

    def test_quoted_password_assignment_redacted(self):
        """JSON-style ``"password": "secret"`` is also caught."""
        scrubber = SensitiveFieldScrubber()
        record = _make_record('payload {"password": "hunter2"}')

        scrubber.filter(record)

        assert "hunter2" not in record.msg
        assert "[REDACTED]" in record.msg

    def test_totp_code_redacted(self):
        """``totp_code=123456`` is redacted via the ``totp`` key family."""
        scrubber = SensitiveFieldScrubber()
        record = _make_record("verify request totp_code=123456")

        scrubber.filter(record)

        assert "123456" not in record.msg
        assert "totp_code=[REDACTED]" in record.msg

    def test_secret_key_redacted(self):
        """``secret_key=...`` covers the Flask SECRET_KEY env-line shape."""
        scrubber = SensitiveFieldScrubber()
        record = _make_record(
            "config dump SECRET_KEY=zoo-bar-baz-quux-this-is-the-key"
        )

        scrubber.filter(record)

        assert "zoo-bar-baz-quux-this-is-the-key" not in record.msg
        assert "SECRET_KEY=[REDACTED]" in record.msg

    def test_backup_code_redacted(self):
        """``backup_code=`` and ``backup_codes=`` both match."""
        scrubber = SensitiveFieldScrubber()
        record = _make_record(
            "submitted backup_code=abc123def456 to /mfa/verify"
        )

        scrubber.filter(record)

        assert "abc123def456" not in record.msg
        assert "backup_code=[REDACTED]" in record.msg

    def test_cookie_header_redacted(self):
        """``Cookie: session=...`` is redacted via the ``cookie`` key."""
        scrubber = SensitiveFieldScrubber()
        record = _make_record(
            'header dump cookie="session=abc.signed.value"'
        )

        scrubber.filter(record)

        assert "abc.signed.value" not in record.msg
        assert "[REDACTED]" in record.msg

    def test_authorization_header_redacted(self):
        """``Authorization=Bearer ...`` is redacted (case-insensitive)."""
        scrubber = SensitiveFieldScrubber()
        record = _make_record("Authorization=Bearer-token-value-redacted-please")

        scrubber.filter(record)

        assert "Bearer-token-value-redacted-please" not in record.msg
        assert "Authorization=[REDACTED]" in record.msg

    def test_multiple_secrets_in_one_message_all_redacted(self):
        """Both substrings in the same message are independently redacted."""
        scrubber = SensitiveFieldScrubber()
        record = _make_record(
            "diag password=p1 totp_code=222333"
        )

        scrubber.filter(record)

        assert "p1" not in record.msg
        assert "222333" not in record.msg
        assert record.msg.count("[REDACTED]") == 2

    def test_message_without_sensitive_pattern_unchanged(self):
        """A message with no key=value form passes through unmutated."""
        scrubber = SensitiveFieldScrubber()
        record = _make_record(
            "User logged in successfully (user_id=42, ip=127.0.0.1)"
        )

        scrubber.filter(record)

        assert record.msg == (
            "User logged in successfully (user_id=42, ip=127.0.0.1)"
        )

    def test_word_containing_password_substring_not_falsely_matched(self):
        """``password_hash_column`` is NOT a key=value form and is preserved.

        Without the negative lookbehind the ``password_hash`` prefix
        would match and the suffix ``_column`` would be consumed as
        the ``hash`` family member's value.  This test pins the
        anchoring guard so a future regex tweak cannot regress.
        """
        scrubber = SensitiveFieldScrubber()
        record = _make_record(
            "queried password_hash_column from auth.users"
        )

        scrubber.filter(record)

        # No ``=`` after ``password_hash`` -- pattern requires a
        # separator, so nothing is redacted.
        assert record.msg == "queried password_hash_column from auth.users"

    def test_format_string_placeholders_preserved(self):
        """``password=%s`` is NOT rewritten -- the placeholder must survive.

        If we redacted the ``%s`` itself, the downstream ``%`` formatter
        would raise ``TypeError: not all arguments converted`` and lose
        the entire log line.  See the ``_SENSITIVE_PATTERNS`` docstring
        for the rationale.
        """
        scrubber = SensitiveFieldScrubber()
        record = _make_record("password=%s submitted", args=("hunter2",))

        scrubber.filter(record)

        # The msg keeps its format placeholder; the literal value in
        # args is the one that needs handling (no key=value form, so
        # the args scrubber leaves it alone -- but the placeholder
        # survives, which keeps formatting healthy).
        assert record.msg == "password=%s submitted"

    def test_non_string_msg_passes_through(self):
        """A non-string ``msg`` (rare but legal) is left untouched."""
        scrubber = SensitiveFieldScrubber()
        record = _make_record({"event": "raw_dict_msg"})

        scrubber.filter(record)

        assert record.msg == {"event": "raw_dict_msg"}


class TestScrubArgs:
    """String members of ``record.args`` are scrubbed; non-strings are not."""

    def test_string_arg_with_secret_redacted(self):
        """A ``password=secret`` substring in an arg becomes redacted."""
        scrubber = SensitiveFieldScrubber()
        record = _make_record(
            "diag %s",
            args=("password=hunter2 from form",),
        )

        scrubber.filter(record)

        assert record.args == ("password=[REDACTED] from form",)

    def test_int_arg_not_coerced_to_string(self):
        """A non-string arg is preserved verbatim so ``%d`` formatting works."""
        scrubber = SensitiveFieldScrubber()
        record = _make_record("count=%d", args=(42,))

        scrubber.filter(record)

        assert record.args == (42,)
        # The formatter would render this as "count=42" -- coercing
        # 42 to "42" would also work for %d but would break for %s
        # callers that expect the original type.

    def test_dict_args_with_secret_string_value_redacted(self):
        """``%(name)s`` formatting with a dict args is also handled."""
        scrubber = SensitiveFieldScrubber()
        record = _make_record(
            "%(label)s",
            args={"label": "totp_secret=ABCDEF1234"},
        )

        scrubber.filter(record)

        assert record.args == {"label": "totp_secret=[REDACTED]"}


class TestScrubExtras:
    """Exact-name and pattern scrubs apply to ``record.__dict__`` extras."""

    def test_exact_name_password_field_replaced_wholesale(self):
        """A ``password=...`` extra is replaced with ``[REDACTED]``.

        The exact-name path exists so an accidental ``log_event(...,
        password=raw)`` call site is caught even when ``raw`` does
        not embed a key=value form.
        """
        scrubber = SensitiveFieldScrubber()
        record = _make_record("change_password event", password="hunter2")

        scrubber.filter(record)

        assert record.password == "[REDACTED]"

    def test_exact_name_totp_secret_field_replaced(self):
        """``totp_secret`` is in the exact-name set."""
        scrubber = SensitiveFieldScrubber()
        record = _make_record("mfa enroll", totp_secret="JBSWY3DPEHPK3PXP")

        scrubber.filter(record)

        assert record.totp_secret == "[REDACTED]"

    def test_extra_string_with_pattern_redacted(self):
        """A free-form string extra carrying a secret is pattern-scrubbed."""
        scrubber = SensitiveFieldScrubber()
        record = _make_record(
            "request body trace",
            body="action=login&password=hunter2&csrf=xyz",
        )

        scrubber.filter(record)

        assert "hunter2" not in record.body
        assert "[REDACTED]" in record.body
        # Surrounding fields preserved -- only the password value got
        # rewritten.
        assert "action=login" in record.body
        assert "csrf=xyz" in record.body

    def test_non_sensitive_extras_unchanged(self):
        """Routine extras (user_id, event, category) are preserved."""
        scrubber = SensitiveFieldScrubber()
        record = _make_record(
            "login_success",
            event="login_success",
            category="auth",
            user_id=42,
        )

        scrubber.filter(record)

        assert record.event == "login_success"
        assert record.category == "auth"
        assert record.user_id == 42

    def test_reserved_logrecord_attrs_skipped(self):
        """Standard ``LogRecord`` attributes are exempt from redaction.

        ``msg``, ``args``, ``levelname`` etc. are infrastructure --
        the scrubber's exact-name set must not accidentally touch
        them.  This test pins the exclusion via a known-safe
        attribute (``levelname``) that would otherwise be a string
        match for nothing in particular but tests the iteration path.
        """
        scrubber = SensitiveFieldScrubber()
        record = _make_record("normal message")
        # Pre-condition: levelname is the framework string we expect.
        assert record.levelname == "INFO"

        scrubber.filter(record)

        assert record.levelname == "INFO"
        assert record.msg == "normal message"


class TestEndToEndJsonOutput:
    """Filter is wired into ``setup_logging``'s console handler."""

    def test_root_handler_carries_sensitive_scrubber(self, app):
        """The console handler installed by setup_logging includes the filter."""
        root = logging.getLogger()
        sh = next(
            (h for h in root.handlers if isinstance(h, logging.StreamHandler)),
            None,
        )
        assert sh is not None, (
            "setup_logging did not install a StreamHandler on the root logger."
        )
        assert any(
            isinstance(f, SensitiveFieldScrubber) for f in sh.filters
        ), (
            "SensitiveFieldScrubber missing from the console handler -- "
            "C-16 wiring regression."
        )

    def test_emitted_json_redacts_password_in_message(self, app):
        """A real emit through the configured handler scrubs the message.

        Builds a fresh handler chain that mirrors setup_logging (so we
        can inspect a captured buffer instead of stdout), then asserts
        the rendered JSON contains ``[REDACTED]`` and never the raw
        secret string.
        """
        buf = io.StringIO()
        logger = logging.getLogger(f"test.scrubber.endtoend.{uuid.uuid4().hex}")
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
        handler.addFilter(SensitiveFieldScrubber())
        logger.addHandler(handler)

        logger.info("dump password=hunter2 from form")

        line = buf.getvalue().strip()
        # Sanity: emitted exactly one line.
        assert "\n" not in line
        parsed = json.loads(line)
        assert "hunter2" not in parsed["message"]
        assert "[REDACTED]" in parsed["message"]


class TestRedactValueHelper:
    """Direct test of the regex-substitution helper."""

    def test_redact_value_preserves_key_and_separator(self):
        """The helper keeps the key+separator and replaces only the value."""
        from app.utils.logging_config import _SENSITIVE_PATTERNS

        match = _SENSITIVE_PATTERNS[1].search("token password=hunter2 end")
        assert match is not None

        # The bare-value pattern's group(0) is just ``password=hunter2``
        # (no surrounding whitespace), and value is ``hunter2``.
        result = _redact_value(match)
        assert result == "password=[REDACTED]"


class TestScrubTextIdempotence:
    """Running the scrubber twice on the same string is a no-op."""

    def test_already_redacted_string_unchanged_on_second_pass(self):
        """Idempotent: ``[REDACTED]`` does not match the patterns again."""
        once = _scrub_text("password=hunter2")
        twice = _scrub_text(once)
        assert once == twice == "password=[REDACTED]"
