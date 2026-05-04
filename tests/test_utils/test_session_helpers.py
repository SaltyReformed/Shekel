"""Shekel Budget App -- Unit tests for app.utils.session_helpers.

Locks down the contract documented in
``app/utils/session_helpers.py``: every auth-factor state change
that needs to invalidate parallel sessions does so by stamping
``users.session_invalidated_at`` and refreshing
``flask.session[SESSION_CREATED_AT_KEY]`` with the SAME timestamp.
The strict less-than comparison in :func:`app.load_user` then rejects
every other live session for that user while letting the request that
issued the helper survive.

These tests exercise the helper in isolation against a real DB and a
real Flask test request context -- no mocks of ``db.session`` or
``flask.session``, because the helper's correctness depends on the
exact serialization round-trip of those two pieces of state.

Audit references: F-002, F-003, F-032 (commit C-08).
"""

import logging
from datetime import datetime, timedelta, timezone

import pytest

from app.models.user import User
from app.utils.log_events import AUTH
from app.utils.session_helpers import (
    SESSION_CREATED_AT_KEY,
    invalidate_other_sessions,
)


class _LogCapture:
    """Capture log records emitted on a specific logger.

    Mirrors ``tests/test_utils/test_log_events._LogCapture`` -- kept
    duplicated rather than imported because the two files exercise
    distinct contracts and a shared helper would create an import-
    order dependency between two unrelated test modules.
    """

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


class TestInvalidateOtherSessionsHappyPath:
    """The helper's documented behaviour on a normal authenticated call."""

    def test_bumps_session_invalidated_at_on_user_row(
        self, app, db, seed_user
    ):
        """``user.session_invalidated_at`` is set to a recent UTC datetime.

        Reload the row from the DB after the call to verify the
        write was committed (not just held in the session unit-of-
        work).  Bound the timestamp between ``before`` and ``after``
        wall-clock samples taken around the helper invocation so a
        regression that wrote a stale or wildly-future value would
        be caught by the bounded-recency assertion.
        """
        user = seed_user["user"]
        assert user.session_invalidated_at is None, (
            "Setup error: seed_user must start with no prior "
            "invalidation timestamp."
        )

        with app.test_request_context("/"):
            before = datetime.now(timezone.utc)
            invalidate_other_sessions(user, "test_reason")
            after = datetime.now(timezone.utc)

        # Reload from DB to assert the write was committed.
        reloaded = db.session.get(User, user.id)
        assert reloaded.session_invalidated_at is not None
        assert before <= reloaded.session_invalidated_at <= after

    def test_refreshes_session_created_at_to_match_invalidation(
        self, app, db, seed_user
    ):
        """The cookie's ``_session_created_at`` equals the DB column.

        The :func:`app.load_user` comparison is strict less-than
        (``cookie < db`` => reject).  Equality is the boundary case
        that lets the current session survive -- a regression that
        used two different ``now`` calls would push the cookie
        microseconds ahead and the test would still pass with == OR
        cookie > db.  Asserting equality (not >=) locks down the
        single-``now`` invariant the helper documents.
        """
        user = seed_user["user"]

        with app.test_request_context("/") as ctx:
            invalidate_other_sessions(user, "single_now_check")
            cookie_value = ctx.session.get(SESSION_CREATED_AT_KEY)

        # Reload from DB.
        reloaded = db.session.get(User, user.id)
        assert cookie_value is not None, (
            "Helper must write SESSION_CREATED_AT_KEY into the session."
        )
        cookie_dt = datetime.fromisoformat(cookie_value)
        assert cookie_dt == reloaded.session_invalidated_at, (
            f"Cookie ({cookie_dt!r}) must equal DB column "
            f"({reloaded.session_invalidated_at!r}); the helper must "
            "use a single now() value for both writes."
        )

    def test_session_created_at_is_iso_8601_string(
        self, app, seed_user,
    ):
        """The cookie value parses with ``datetime.fromisoformat``.

        ``app.load_user`` calls ``fromisoformat`` on the cookie value;
        if the helper wrote a non-ISO format the cookie would round-
        trip-explode and every authenticated request after the
        invalidation would 500.
        """
        user = seed_user["user"]
        with app.test_request_context("/") as ctx:
            invalidate_other_sessions(user, "iso_format_check")
            cookie_value = ctx.session.get(SESSION_CREATED_AT_KEY)

        # Must be a string (not a datetime) and must round-trip.
        assert isinstance(cookie_value, str)
        parsed = datetime.fromisoformat(cookie_value)
        # Must be timezone-aware so the load_user comparison works.
        assert parsed.tzinfo is not None, (
            "The cookie value must be timezone-aware ISO 8601; a naive "
            "datetime would raise on the timezone-aware comparison in "
            "load_user."
        )

    def test_emits_other_sessions_invalidated_event_with_reason(
        self, app, seed_user,
    ):
        """A structured ``other_sessions_invalidated`` event is logged.

        Locks down the audit-trail contract: every call must emit
        exactly one event with the supplied ``reason`` and the
        ``user_id`` of the user being invalidated.  Without this
        check, a future refactor that swallowed the log call would
        leave operators blind to credential-change activity.
        """
        user = seed_user["user"]
        with app.test_request_context("/"):
            with _LogCapture(
                logging.getLogger("app.utils.session_helpers")
            ) as cap:
                invalidate_other_sessions(user, "password_change")

        matching = [
            r for r in cap.records
            if getattr(r, "event", None) == "other_sessions_invalidated"
        ]
        assert len(matching) == 1, (
            "Helper must emit exactly one other_sessions_invalidated "
            f"event per call; got {len(matching)}."
        )
        record = matching[0]
        assert record.category == AUTH
        assert record.reason == "password_change"
        assert record.user_id == user.id
        assert record.levelno == logging.INFO

    def test_idempotent_under_repeated_call(self, app, db, seed_user):
        """Calling the helper twice in a row leaves the user in a
        consistent post-invalidation state.

        Verifies that a second call does not corrupt the timestamp,
        does not raise, and leaves the cookie matching the latest DB
        value.  Important because future call sites might call the
        helper from both the route and a hook that fires for the same
        state change -- a non-idempotent helper would split the two
        timestamps and lock the user out of their current session.
        """
        user = seed_user["user"]
        with app.test_request_context("/") as ctx:
            invalidate_other_sessions(user, "first_call")
            first_db = db.session.get(User, user.id).session_invalidated_at
            first_cookie = ctx.session.get(SESSION_CREATED_AT_KEY)

            invalidate_other_sessions(user, "second_call")
            second_db = db.session.get(User, user.id).session_invalidated_at
            second_cookie = ctx.session.get(SESSION_CREATED_AT_KEY)

        # Second call's timestamps must be >= first (monotonic).
        assert second_db >= first_db
        assert datetime.fromisoformat(second_cookie) >= datetime.fromisoformat(first_cookie)
        # Cookie still matches DB after the second call.
        assert datetime.fromisoformat(second_cookie) == second_db


class TestInvalidateOtherSessionsValidation:
    """Defensive validation on the helper's inputs."""

    def test_rejects_none_user(self, app):
        """Passing ``user=None`` raises before any state mutation.

        A None user would silently no-op or raise an obscure
        AttributeError mid-helper.  The early-validation contract
        guarantees the caller's session state is unchanged on misuse.
        """
        with app.test_request_context("/") as ctx:
            with pytest.raises(ValueError, match="None"):
                invalidate_other_sessions(None, "any_reason")
            # Cookie was not touched.
            assert SESSION_CREATED_AT_KEY not in ctx.session

    def test_rejects_empty_reason(self, app, db, seed_user):
        """Empty/None reason raises before any state mutation.

        The audit log relies on ``reason`` to distinguish password-
        change events from MFA-disable events from backup-code
        consumption events.  An empty value defeats the audit trail
        the helper exists to produce.  Verify both the user row and
        the cookie are untouched on misuse.
        """
        user = seed_user["user"]
        with app.test_request_context("/") as ctx:
            with pytest.raises(ValueError, match="reason"):
                invalidate_other_sessions(user, "")
            assert SESSION_CREATED_AT_KEY not in ctx.session

        # User row still has its initial session_invalidated_at value.
        reloaded = db.session.get(User, user.id)
        assert reloaded.session_invalidated_at is None

    def test_rejects_none_reason(self, app, seed_user):
        """``reason=None`` is also rejected (not just empty string).

        Separate from the empty-string test because the implementation
        could conceivably check truthiness for one and not the other.
        Both must fail.
        """
        user = seed_user["user"]
        with app.test_request_context("/"):
            with pytest.raises(ValueError, match="reason"):
                invalidate_other_sessions(user, None)


class TestInvalidateOtherSessionsLoadUserIntegration:
    """The helper's writes correctly drive ``app.load_user``.

    The helper's contract only matters if ``load_user`` actually
    accepts the current cookie and rejects older ones.  These tests
    exercise the integration without going through a full route, so
    a regression in either the helper or the loader is caught here.
    """

    def test_current_cookie_value_passes_load_user_after_call(
        self, app, seed_user,
    ):
        """Cookie ``_session_created_at`` written by the helper makes
        ``load_user`` return the user (current session survives).

        Drives the loader directly via ``login_manager._user_callback``
        with the cookie value the helper just wrote, so the test
        bypasses Flask-Login's per-request cache and exercises the
        actual reload path.
        """
        # pylint: disable=import-outside-toplevel,protected-access
        # Local import keeps the LoginManager dependency next to the
        # test that needs it.  protected-access is intentional: we
        # are exercising the loader callback directly rather than
        # through current_user, because going through current_user
        # would hit the g._login_user cache and skip load_user
        # entirely.
        from app.extensions import login_manager

        user = seed_user["user"]

        with app.test_request_context("/"):
            invalidate_other_sessions(user, "current_passes_check")
            # The cookie the helper just wrote is still on the
            # request context's session.  Drive the loader.
            loaded = login_manager._user_callback(str(user.id))

            assert loaded is not None, (
                "Current session must survive the helper's invalidation "
                "bump; load_user returned None instead."
            )
            assert loaded.id == user.id

    def test_older_cookie_value_fails_load_user_after_call(
        self, app, seed_user,
    ):
        """A cookie with a pre-call ``_session_created_at`` is rejected.

        Sets the cookie's timestamp to one hour BEFORE the helper
        runs, then invokes the helper, then drives the loader.  The
        loader must return None for the stale cookie -- the entire
        point of the helper.
        """
        # pylint: disable=import-outside-toplevel,protected-access
        # See test_current_cookie_value_passes_load_user_after_call
        # above for the rationale on the protected-access waiver.
        from app.extensions import login_manager

        user = seed_user["user"]

        with app.test_request_context("/") as ctx:
            stale_value = (
                datetime.now(timezone.utc) - timedelta(hours=1)
            ).isoformat()
            ctx.session[SESSION_CREATED_AT_KEY] = stale_value

            invalidate_other_sessions(user, "older_fails_check")

            # Replace the helper-written cookie with the stale value
            # before calling load_user, simulating a parallel session
            # that has not yet refreshed its cookie.
            ctx.session[SESSION_CREATED_AT_KEY] = stale_value

            loaded = login_manager._user_callback(str(user.id))
            assert loaded is None, (
                "A session whose _session_created_at predates the "
                "invalidation bump must be rejected by load_user; "
                "got a non-None user instead."
            )
