"""
Shekel Budget App -- Adversarial Tests for SECRET_KEY Rotation

Regression tests that verify the two protections against forged or
replayed session cookies after a SECRET_KEY rotation:

1. **Signature mismatch** (covered by Flask itself): a cookie signed
   with the old SECRET_KEY fails signature verification under the new
   key, so the session is treated as empty and the user is treated as
   anonymous.
2. **Per-user invalidation timestamp** (covered by ``load_user`` in
   ``app/__init__.py``): even if an attacker preserves cookies that
   *would* verify under the same key (for example, a downgrade attack
   that re-uses the old key), bumping
   ``users.session_invalidated_at`` rejects every session whose
   ``_session_created_at`` predates the bump.

If either test starts failing, the post-rotation security posture
described in ``docs/runbook_secrets.md`` is broken.

Related audit findings: F-001, F-016 (and the operational control
shipped in ``scripts/rotate_sessions.py``).
"""

from datetime import datetime, timedelta, timezone

from flask import g

from app.extensions import db
from app.models.user import User


def _set_session_created_at(test_client, when: datetime) -> None:
    """Force ``_session_created_at`` on the test client's session.

    ``session_transaction`` opens the same SecureCookieSession that
    Flask uses to read/write the signed cookie.  Setting the key
    inside the context manager re-signs the cookie when the block
    exits.

    Args:
        test_client: A Flask test client with an existing session.
        when: The timestamp to record as the session's creation time.
    """
    with test_client.session_transaction() as sess:
        sess["_session_created_at"] = when.isoformat()


def _reset_login_cache() -> None:
    """Force Flask-Login to re-evaluate ``current_user`` on the next
    request.

    Flask-Login caches the user lookup on ``g._login_user`` once per
    request.  In production each HTTP request gets a fresh app
    context (and therefore a fresh ``g``), so the cache is effectively
    per-request.  In the test suite, the autouse ``db`` fixture holds
    a single app context across every ``test_client`` call in the
    test, so ``g._login_user`` persists between simulated requests and
    Flask-Login will return the stale user even after the session has
    been invalidated.

    Calling this before each "subsequent request" in a single test
    forces a fresh user lookup, which is what would happen between
    HTTP requests in production.
    """
    g.pop("_login_user", None)


class TestSecretKeyRotation:
    """Cookie/session rejection paths after SECRET_KEY rotation."""

    def test_session_cookie_signed_with_old_key_is_rejected(
        self, app, auth_client, monkeypatch
    ):
        """A cookie signed under the original SECRET_KEY no longer
        opens a session once the key has been rotated.

        Setup: log the user in, capturing a cookie signed under the
        current key K1.  Action: swap ``app.config["SECRET_KEY"]`` to a
        different value K2.  Verification: the next request
        ``GET /dashboard`` with the same cookie redirects to ``/login``,
        because Flask cannot verify the cookie's signature against K2.

        Without this protection, an attacker who captured a cookie
        before the rotation could continue using it (for as long as the
        old key remained accepted) -- which is exactly the threat that
        SECRET_KEY rotation is meant to close.
        """
        # Sanity: the auth_client has an active session pre-rotation.
        pre = auth_client.get("/dashboard")
        assert pre.status_code == 200, (
            "Setup failed: auth_client should be logged in before the "
            f"rotation; got {pre.status_code}"
        )

        # Rotate the key.  monkeypatch.setitem registers a teardown so
        # subsequent tests are not affected.  The new key must be
        # distinct from the conftest test key and must satisfy any
        # length floor that ProdConfig would enforce (here, ProdConfig
        # is not in play, but using a 64-char value matches reality).
        new_key = "post-rotation-key-distinct-from-conftest-test-suite-fixed-key"
        assert new_key != app.config["SECRET_KEY"], (
            "Test setup error: rotation key must differ from the test "
            "SECRET_KEY for the signature mismatch to fire."
        )
        monkeypatch.setitem(app.config, "SECRET_KEY", new_key)

        # Force a fresh user lookup -- Flask-Login's per-request cache
        # is sticky inside the autouse db fixture's app context.
        _reset_login_cache()

        # Same cookie, new key: signature mismatch.  Flask treats the
        # session as empty, current_user is anonymous, and any
        # @login_required route redirects to /login.
        post = auth_client.get("/dashboard", follow_redirects=False)
        assert post.status_code == 302
        assert "/login" in post.headers["Location"]

    def test_session_created_before_invalidation_timestamp_rejected(
        self, app, auth_client, seed_user
    ):
        """A session whose ``_session_created_at`` predates
        ``users.session_invalidated_at`` is rejected by ``load_user``.

        Setup: log the user in.  Action: forcibly set
        ``_session_created_at`` to one hour ago, then bump
        ``users.session_invalidated_at`` to ``now()``.  Verification:
        ``GET /dashboard`` redirects to ``/login`` because ``load_user``
        compares the two timestamps and returns ``None`` when the
        session predates the invalidation.

        This is the *signature-independent* protection -- it works
        even if the attacker still has a valid signing key.  The
        ``rotate_sessions`` script relies on this branch in
        ``load_user`` to actually log everyone out.
        """
        # Sanity: the auth_client has an active session.
        pre = auth_client.get("/dashboard")
        assert pre.status_code == 200, (
            "Setup failed: auth_client should be logged in before "
            f"timestamp manipulation; got {pre.status_code}"
        )

        # Force the session to look 1 hour old.
        synthetic_creation = datetime.now(timezone.utc) - timedelta(hours=1)
        _set_session_created_at(auth_client, synthetic_creation)

        # Bump the user's invalidation timestamp to "now".  The
        # comparison in load_user becomes:
        #     synthetic_creation (-1h) < user.session_invalidated_at (now)
        # which evaluates True and load_user returns None.
        user = db.session.get(User, seed_user["user"].id)
        user.session_invalidated_at = datetime.now(timezone.utc)
        db.session.commit()

        # Force a fresh user lookup so the next request actually
        # exercises load_user (rather than reading a cached value).
        _reset_login_cache()

        post = auth_client.get("/dashboard", follow_redirects=False)
        assert post.status_code == 302
        assert "/login" in post.headers["Location"]

    def test_session_created_after_invalidation_timestamp_accepted(
        self, app, auth_client, seed_user
    ):
        """The complement of the previous test.

        A session created AFTER ``session_invalidated_at`` must still
        be accepted -- otherwise ``load_user`` would lock everyone out
        forever after the first rotation.  Lock down the boundary so
        a buggy refactor of the comparison cannot break login.
        """
        # Bump invalidation to one hour ago.
        user = db.session.get(User, seed_user["user"].id)
        user.session_invalidated_at = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        )
        db.session.commit()

        # Force the session to look brand new.
        _set_session_created_at(auth_client, datetime.now(timezone.utc))

        # Drop the cached user so load_user actually re-runs the
        # timestamp comparison.  Without this the test would pass
        # spuriously by returning the cached pre-test user.
        _reset_login_cache()

        post = auth_client.get("/dashboard")
        assert post.status_code == 200, (
            "Sessions created after invalidation_at must be accepted; "
            f"got {post.status_code}"
        )
