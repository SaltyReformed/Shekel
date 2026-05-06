"""
Shekel Budget App -- Adversarial Tests for Session Protection (strong)

Audit finding F-038 (CWE-384 Session Fixation): Flask-Login's default
``session_protection = "basic"`` only flips ``session["_fresh"]`` to
False on per-request identifier drift and leaves the rest of the
session in place.  The ``"strong"`` mode pops every Flask-Login
session key on drift and schedules the remember-me cookie for
clearing, forcing a complete re-authentication.  See remediation
Commit C-07 for the rationale.

These tests assert the runtime divergence between basic and strong
end-to-end against the actual ``app/extensions.py:login_manager``
configuration -- a regression that re-introduces ``"basic"`` (or
disables session protection entirely with ``None``) must fail one of
the behavior tests below, not just the static config inspection in
``tests/test_config.py``.

Flask-Login derives the per-request identifier from
``sha512(remote_addr || "|" || user_agent)`` (see
``flask_login/utils.py:_create_identifier``) and where
``remote_addr`` prefers ``request.headers["X-Forwarded-For"]`` over
``request.remote_addr`` (see
``flask_login/utils.py:_get_remote_addr``).  Each test simulates a
single attribute change and verifies the session is no longer
authoritative on the next request.

Test client defaults established once at the top of the module so
every test in the file shares the same baseline:

- ``REMOTE_ADDR`` defaults to ``127.0.0.1`` (Werkzeug
  ``EnvironBuilder``) when no override is supplied.
- ``User-Agent`` defaults to the ``Werkzeug/X.Y`` string emitted by
  the test client.
- No ``X-Forwarded-For`` is sent by default, so
  ``_get_remote_addr()`` falls back to ``REMOTE_ADDR`` unless a test
  explicitly adds the header.
"""

from flask import g

from app.extensions import login_manager


# A second IP address distinct from Werkzeug's test-client default
# (127.0.0.1).  Picked from the TEST-NET-1 documentation range
# (RFC 5737) so it cannot resolve to a real host.
_OTHER_REMOTE_ADDR = "192.0.2.42"

# A second IP address from RFC 5737 TEST-NET-2 to differentiate the
# X-Forwarded-For drift test from the REMOTE_ADDR drift test.  The
# specific value is unimportant; what matters is that it is distinct
# from the value used at login time.
_OTHER_FORWARDED_ADDR = "198.51.100.7"

# A second User-Agent string that sha512-hashes to a different
# identifier than the Werkzeug test-client default.
_OTHER_USER_AGENT = "Mozilla/5.0 (TestProbe/1.0)"


def _reset_login_cache() -> None:
    """Force Flask-Login to re-evaluate ``current_user`` on the next
    request.

    Flask-Login caches the loaded user on ``g._login_user`` once per
    request.  In production each HTTP request gets a fresh
    ``app.app_context()`` (and therefore a fresh ``g``), so the cache
    is effectively per-request.  The autouse ``db`` fixture in
    ``tests/conftest.py`` holds a single app context across every
    ``test_client`` call within a test, which means subsequent
    requests would otherwise see the cached user even after the
    session has been wiped by ``_session_protection_failed``.

    Mirrors the helper in
    ``tests/test_adversarial/test_secret_key_rotation.py`` -- the
    same caching effect breaks tests that probe per-request session
    state.  Kept duplicated rather than imported because the two
    files exercise distinct invariants and a shared helper would tie
    them together unnecessarily.
    """
    g.pop("_login_user", None)


def _extract_remember_token(set_cookies: list[str]) -> str:
    """Return the single ``remember_token=...`` directive from
    ``set_cookies`` and fail loudly if zero or more than one match.

    Set-Cookie headers serialize to a single string per directive
    that begins with ``<name>=<value>;`` and continues with optional
    attributes.  Tests in this file consistently want exactly one
    ``remember_token`` directive (either the freshly-emitted login
    cookie or the post-drift clear), so wrapping the lookup in a
    helper produces a single ``assert`` per call site instead of two
    (presence + uniqueness) and reduces the number of intermediate
    locals each test has to track.
    """
    matches = [c for c in set_cookies if c.startswith("remember_token=")]
    assert len(matches) == 1, (
        "Expected exactly one remember_token Set-Cookie directive; "
        f"got {set_cookies!r}"
    )
    return matches[0]


def _remember_token_value(set_cookie_header: str) -> str:
    """Return the value portion of a ``remember_token=...`` Set-Cookie
    string.

    A Werkzeug-emitted ``Set-Cookie`` for the remember-me cookie has
    the shape ``remember_token=<value>; Expires=...; Path=/; ...``.
    A clear directive sets ``<value>`` to the empty string and adds
    an ``Expires`` in the past; a fresh login emits a non-empty
    encoded value with a future ``Expires``.  The helper extracts the
    value so tests can assert presence/absence without duplicating
    the parse logic.
    """
    return set_cookie_header.split(";", 1)[0].split("=", 1)[1]


class TestStrongSessionProtection:
    """End-to-end behaviour of ``login_manager.session_protection``.

    The fixture ``auth_client`` logs in once at the Werkzeug default
    fingerprint (``REMOTE_ADDR=127.0.0.1`` / ``User-Agent=Werkzeug/
    *``).  Each test simulates a single attribute drift on the next
    request and asserts the session is no longer authoritative.
    """

    def test_remote_addr_change_invalidates_session(
        self, auth_client
    ):
        """A subsequent request from a different IP must be treated
        as anonymous and redirected to ``/login``.

        Identifier component changed: ``REMOTE_ADDR``.

        ``auth_client`` logs in at ``127.0.0.1``.  We then issue
        ``GET /dashboard`` with ``REMOTE_ADDR`` overridden to a
        TEST-NET-1 address so the per-request identifier hash
        changes.  ``_session_protection_failed`` must fire under
        ``"strong"`` mode, the session must be wiped, and Flask-Login
        must redirect to the login view.

        Pre-strong (basic) regression check: under ``"basic"``, the
        session would remain populated and only ``session["_fresh"]``
        would flip to False; ``GET /dashboard`` would still return
        200.  This test must therefore fail with status 200 if a
        future change accidentally re-introduces ``"basic"``.
        """
        # Sanity: the auth_client is logged in at the default IP.
        pre = auth_client.get("/dashboard")
        assert pre.status_code == 200, (
            "Setup failed: auth_client should be logged in before the "
            f"identifier drift; got {pre.status_code}"
        )

        # Drop the per-request user cache so load_user actually runs
        # against the freshly-wiped session on the next request.
        _reset_login_cache()

        # Request from a different IP.  The X-Forwarded-For header is
        # absent, so _get_remote_addr() falls back to REMOTE_ADDR.
        post = auth_client.get(
            "/dashboard",
            environ_overrides={"REMOTE_ADDR": _OTHER_REMOTE_ADDR},
            follow_redirects=False,
        )
        assert post.status_code == 302, (
            "Strong session protection should redirect to /login on "
            f"REMOTE_ADDR drift; got {post.status_code}"
        )
        assert "/login" in post.headers["Location"], (
            "Redirect target must be the login view; got "
            f"{post.headers['Location']!r}"
        )

    def test_user_agent_change_invalidates_session(self, auth_client):
        """A subsequent request from a different User-Agent must be
        treated as anonymous and redirected to ``/login``.

        Identifier component changed: ``User-Agent``.

        Mirrors ``test_remote_addr_change_invalidates_session`` but
        drifts the second component of the identifier hash.
        Together the two tests prove that BOTH inputs to
        ``_create_identifier`` are wired into the protection -- a
        future bug that hashes only one input would leave the other
        as a session-fixation hole.
        """
        pre = auth_client.get("/dashboard")
        assert pre.status_code == 200, (
            "Setup failed: auth_client should be logged in before the "
            f"identifier drift; got {pre.status_code}"
        )

        _reset_login_cache()

        post = auth_client.get(
            "/dashboard",
            headers={"User-Agent": _OTHER_USER_AGENT},
            follow_redirects=False,
        )
        assert post.status_code == 302, (
            "Strong session protection should redirect to /login on "
            f"User-Agent drift; got {post.status_code}"
        )
        assert "/login" in post.headers["Location"], (
            "Redirect target must be the login view; got "
            f"{post.headers['Location']!r}"
        )

    def test_x_forwarded_for_change_invalidates_session(
        self, client, app, db, seed_user
    ):
        """A subsequent request whose ``X-Forwarded-For`` header
        differs from the value seen at login must be treated as
        anonymous.

        Identifier component changed: ``X-Forwarded-For`` (proxy-
        aware path).  Production traffic arrives via Cloudflare
        Tunnel + nginx, both of which set ``X-Forwarded-For`` to the
        original client IP -- ``_get_remote_addr()`` reads that
        header first and only falls back to ``request.remote_addr``
        when it is absent.  Without this test the IP-drift coverage
        would assert only the development-default code path
        (``REMOTE_ADDR`` direct), not the production code path.

        Uses a fresh client (rather than ``auth_client``) so the
        login can be issued with a deterministic
        ``X-Forwarded-For`` value, avoiding the implicit fallback to
        ``REMOTE_ADDR`` that ``auth_client`` triggers.
        """
        # pylint: disable=unused-argument  # seed_user, db are required
        # by the auth flow; their fixtures arrange the user record.

        # Log in with a deterministic X-Forwarded-For so the
        # session's stored "_id" is the sha512 of that header (not
        # the REMOTE_ADDR fallback).
        login_resp = client.post(
            "/login",
            data={
                "email": "test@shekel.local",
                "password": "testpass",
            },
            headers={"X-Forwarded-For": _OTHER_REMOTE_ADDR},
        )
        assert login_resp.status_code == 302, (
            f"Login failed with status {login_resp.status_code}"
        )

        # Sanity: the same XFF still resolves to the dashboard.
        same = client.get(
            "/dashboard",
            headers={"X-Forwarded-For": _OTHER_REMOTE_ADDR},
        )
        assert same.status_code == 200, (
            "Setup failed: same X-Forwarded-For should keep the "
            f"session valid; got {same.status_code}"
        )

        _reset_login_cache()

        # Drift X-Forwarded-For to a distinct address.  REMOTE_ADDR
        # is unchanged but does not feed the identifier when XFF is
        # present (see flask_login.utils._get_remote_addr).
        drift = client.get(
            "/dashboard",
            headers={"X-Forwarded-For": _OTHER_FORWARDED_ADDR},
            follow_redirects=False,
        )
        assert drift.status_code == 302, (
            "Strong session protection should redirect to /login on "
            f"X-Forwarded-For drift; got {drift.status_code}"
        )
        assert "/login" in drift.headers["Location"], (
            "Redirect target must be the login view; got "
            f"{drift.headers['Location']!r}"
        )

    def test_unchanged_fingerprint_preserves_session(self, auth_client):
        """Control test: when neither identifier input changes, the
        session must remain authoritative.

        Without this test, a buggy implementation that nuked every
        session unconditionally would still pass the drift tests
        (status 302 on the drifted request).  Asserting that the
        unchanged-fingerprint path returns 200 closes that gap and
        proves the drift tests measure drift, not a pathological
        kill-switch.
        """
        # Two consecutive requests with no environ or header
        # overrides.  REMOTE_ADDR and User-Agent stay at the
        # Werkzeug test-client defaults -- identical to the values
        # at login time -- so the per-request identifier hash
        # matches session["_id"] exactly.
        first = auth_client.get("/dashboard")
        assert first.status_code == 200, (
            f"Baseline request failed with status {first.status_code}"
        )

        _reset_login_cache()

        second = auth_client.get("/dashboard")
        assert second.status_code == 200, (
            "Session was invalidated despite identical fingerprint; "
            "got status "
            f"{second.status_code}.  This indicates strong mode is "
            "either misconfigured or hashing inputs that drift "
            "between consecutive identical requests."
        )

    def test_strong_mode_pops_session_keys_not_just_fresh_flag(
        self, auth_client
    ):
        """The strong-mode signature: every Flask-Login session key
        is removed on drift, not just ``_fresh`` flipped to False.

        Distinguishes strong from basic at the session-state level
        rather than the redirect level:

        - Basic: ``session["_fresh"]`` becomes False, but
          ``_user_id``, ``_id``, and other Flask-Login keys persist.
          The user remains logged in for any non-fresh-required
          endpoint.
        - Strong: every key in Flask-Login's ``SESSION_KEYS`` is
          popped (see flask_login.login_manager.
          _session_protection_failed); the after-request remember-me
          cookie clear is the only surviving residue.

        Asserting on ``_user_id`` absence after drift proves we are
        in strong-mode territory -- a regression to basic would leave
        ``_user_id`` populated and fail this assertion.
        """
        # Sanity: post-login the session has _user_id and _id.
        with auth_client.session_transaction() as sess:
            assert "_user_id" in sess, (
                "Setup failed: login_user did not set _user_id"
            )
            assert "_id" in sess, (
                "Setup failed: login_user did not set _id"
            )

        # The auth_client fixture's POST /login populated
        # ``g._login_user``; the test fixture's app context outlives
        # the login request, so without resetting the cache the next
        # request would skip ``_load_user`` (and therefore the
        # session-protection check) entirely.  See
        # ``_reset_login_cache`` for the reasoning.
        _reset_login_cache()

        # Trigger drift on the next request.  We do not care about
        # the response itself here -- the strong-mode wipe happens
        # during request processing, before the route runs.
        auth_client.get(
            "/dashboard",
            environ_overrides={"REMOTE_ADDR": _OTHER_REMOTE_ADDR},
        )

        with auth_client.session_transaction() as sess:
            assert "_user_id" not in sess, (
                "Strong mode must remove _user_id from the session "
                f"on drift; saw {sess.get('_user_id')!r}"
            )
            assert "_id" not in sess, (
                "Strong mode must remove _id from the session "
                f"on drift; saw {sess.get('_id')!r}"
            )

    def test_strong_mode_clears_remember_cookie_on_drift(
        self, app, client, db, seed_user
    ):
        """When drift fires while a remember-me cookie is present,
        the response must instruct the browser to clear it.

        Strong mode sets ``session["_remember"] = "clear"`` on drift
        (see ``_session_protection_failed``).  Flask-Login's
        ``_update_remember_cookie`` after-request hook then issues a
        ``Set-Cookie: remember_token=`` with an expiry in the past,
        deleting the cookie from the browser.

        Without this clear step, the next request -- now without a
        valid session -- would re-authenticate the user from the
        remember-me cookie, completely defeating the
        session-protection intent.  The cookie clear is what makes
        ``"strong"`` actually strong.
        """
        # pylint: disable=unused-argument  # seed_user creates the user.

        # Apply ProdConfig's remember-cookie flags so Flask-Login
        # treats the test client's request as production-equivalent.
        # Without the override, the cookie is still emitted but the
        # absence of Secure/HttpOnly/SameSite could mask future
        # regressions in the production-only code path.
        original = {
            key: app.config.get(key)
            for key in (
                "REMEMBER_COOKIE_SECURE",
                "REMEMBER_COOKIE_HTTPONLY",
                "REMEMBER_COOKIE_SAMESITE",
            )
        }
        try:
            app.config["REMEMBER_COOKIE_SECURE"] = True
            app.config["REMEMBER_COOKIE_HTTPONLY"] = True
            app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"

            login_resp = client.post(
                "/login",
                data={
                    "email": "test@shekel.local",
                    "password": "testpass",
                    "remember": "on",
                },
            )
            assert login_resp.status_code == 302, (
                f"Login failed with status {login_resp.status_code}"
            )

            # Setup sanity: login emitted a non-empty remember_token.
            login_directive = _extract_remember_token(
                login_resp.headers.getlist("Set-Cookie")
            )
            assert _remember_token_value(login_directive), (
                "Setup failed: login emitted an empty remember_token "
                f"value: {login_directive!r}"
            )

            _reset_login_cache()

            # Drift REMOTE_ADDR.  Strong mode should pop the session
            # AND schedule remember_token for clearing, so the
            # response must Set-Cookie remember_token to an empty /
            # past-expiry value.
            drift = client.get(
                "/dashboard",
                environ_overrides={"REMOTE_ADDR": _OTHER_REMOTE_ADDR},
                follow_redirects=False,
            )
            drift_directive = _extract_remember_token(
                drift.headers.getlist("Set-Cookie")
            )
            # The clear directive uses an empty value plus an Expires
            # in the past (Werkzeug's delete_cookie convention).
            assert _remember_token_value(drift_directive) == "", (
                "Strong mode's remember_token clear must send an "
                "empty value to overwrite the cookie; got "
                f"{drift_directive!r}"
            )
            assert "Expires=" in drift_directive, (
                "Strong mode's remember_token clear must include a "
                "past Expires attribute so the browser deletes the "
                f"cookie; got {drift_directive!r}"
            )
        finally:
            for key, value in original.items():
                if value is None:
                    app.config.pop(key, None)
                else:
                    app.config[key] = value


class TestSessionProtectionAttributeIsConfigured:
    """Belt-and-braces: the ``app/extensions.py`` module-level
    assignment is exercised here as well so a refactor that moves
    the assignment elsewhere (or accidentally drops it) is caught
    even if the static inspection in
    ``tests/test_config.py::TestLoginManagerConfig`` is skipped or
    deselected.

    ``test_config.py`` covers the *intent* (the literal value);
    this class covers the *binding* (the attribute is actually set
    on the module-level ``login_manager`` instance the rest of the
    app imports).
    """

    def test_login_manager_attribute_set_to_strong(self):
        """``login_manager.session_protection`` must equal the
        literal string ``"strong"``.

        Flask-Login's ``_session_protection_failed`` short-circuits
        to ``return False`` when ``session_protection`` is not in
        ``["basic", "strong"]`` (see
        flask_login.login_manager.LoginManager._session_protection_failed).
        That makes ``None``, ``""``, or any typo silently disable
        the protection -- so an exact-string check is required.
        """
        assert login_manager.session_protection == "strong", (
            "login_manager.session_protection must be 'strong' to "
            "satisfy ASVS L2 V3.2.1 / audit finding F-038; got "
            f"{login_manager.session_protection!r}"
        )
