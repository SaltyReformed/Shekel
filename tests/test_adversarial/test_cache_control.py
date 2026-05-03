"""
Shekel Budget App -- Cache-Control Adversarial Tests

Audit finding F-019 (shared-device threat): a logged-out user steps
away from a shared computer; an attacker presses the browser back
button and reconstructs the dashboard from history.  The defense is
``Cache-Control: no-store, no-cache, must-revalidate`` on every
authenticated response so the browser does not retain a renderable
copy.

These tests assert the header value end-to-end against the actual
after-request hook in ``app/__init__.py``.  Browser back-button
behavior itself cannot be exercised by Flask's test client; that
requires a manual verification pass against a real browser (covered
by the runbook's manual checklist).
"""


def test_logged_out_pages_have_no_store(auth_client, client):
    """After logout, any accidental refetch of an authenticated route
    must not be served from cache.  We assert the no-store header on
    the page that was authenticated -- the same hook applies before
    and after logout, so checking the header on the dashboard while
    logged in is sufficient evidence that the post-logout cache
    semantics are correct."""
    pre_logout = auth_client.get("/dashboard", follow_redirects=True)
    assert pre_logout.status_code == 200
    assert "no-store" in pre_logout.headers.get("Cache-Control", "")

    # Log out, then GET /dashboard directly.  Flask redirects to
    # /login because the session is gone; the redirect response
    # itself must also carry no-store so the browser does not cache
    # the 302.  The login page (after follow_redirects) must too.
    auth_client.post("/logout")
    post_logout = auth_client.get("/dashboard")
    # Status: 302 (redirect to login).  Either way, no-store applies.
    assert "no-store" in post_logout.headers.get("Cache-Control", "")

    # Anonymous client landing on the login page must also be no-store.
    landing = client.get("/login")
    assert "no-store" in landing.headers.get("Cache-Control", "")


def test_static_assets_excluded_from_no_store(client):
    """Static assets must remain cacheable.  In production nginx
    serves /static/ before Flask and sets its own ``Cache-Control:
    public, immutable``; in dev/test Flask's built-in static handler
    is used and the after-request hook explicitly skips
    ``request.endpoint == 'static'``.  This test exercises the dev/
    test path -- a regression here means the nginx no-double-header
    invariant is violated and the production behaviour will diverge
    from dev.

    Vendor assets are content-versioned via VERSIONS.txt; aggressive
    caching is correct and required for performance."""
    resp = client.get("/static/vendor/htmx/htmx.min.js")
    assert resp.status_code == 200
    cache_control = resp.headers.get("Cache-Control", "")
    # The static handler must NOT have applied no-store; Flask's
    # default is empty / no-cache directive on static serving.  The
    # explicit assertion is that ``no-store`` is absent.
    assert "no-store" not in cache_control


def test_static_asset_path_resolves(client):
    """Fail loudly if a vendored file is missing.  The static path
    asserted here MUST point at a file in ``app/static/vendor/``;
    otherwise the test in test_static_assets_excluded_from_no_store
    is meaningless (Flask returns 404, no-store is moot)."""
    # All three core vendored assets must resolve.
    paths = (
        "/static/vendor/bootstrap/bootstrap.min.css",
        "/static/vendor/bootstrap/bootstrap.bundle.min.js",
        "/static/vendor/htmx/htmx.min.js",
        "/static/vendor/chart-js/chart.umd.min.js",
        "/static/vendor/bootstrap-icons/bootstrap-icons.min.css",
        "/static/vendor/bootstrap-icons/fonts/bootstrap-icons.woff2",
        "/static/vendor/fonts/fonts.css",
        "/static/vendor/fonts/inter-latin.woff2",
        "/static/vendor/fonts/jetbrainsmono-latin.woff2",
    )
    for path in paths:
        resp = client.get(path)
        assert resp.status_code == 200, (
            f"Static asset missing or unreachable: {path}"
        )


def test_remember_me_cookie_flags_after_login(client, app, db, seed_user):
    """When a user logs in with ``remember=true``, the
    ``remember_token`` cookie sent in the response must carry
    ``Secure``, ``HttpOnly``, and ``SameSite=Lax`` flags.  See
    audit finding F-017.

    Flask-Login only emits the remember cookie when both (a) the user
    requested ``remember=True`` AND (b) the application config has
    ``REMEMBER_COOKIE_SECURE`` / ``HTTPONLY`` / ``SAMESITE`` set.
    The TestConfig used by the test harness inherits BaseConfig and
    does NOT override these to True, so this test patches the app
    config to ProdConfig's values for the duration of the test --
    the assertion is on the production-class behaviour, not the
    test default."""
    # pylint: disable=unused-argument  # seed_user creates the user
    # we log in as; the fixture itself is what matters.
    with app.app_context():
        # Apply ProdConfig's remember-cookie flags to the test app
        # so Flask-Login emits the hardened cookie.  Restore after
        # the assertion to keep the rest of the suite independent.
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
            # Form posts ``remember=on`` (HTML checkbox standard);
            # the auth route at app/routes/auth.py:87 explicitly
            # checks for the literal string "on".
            resp = client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
                "remember": "on",
            })
            # 302 to dashboard on success.
            assert resp.status_code == 302, (
                f"Login failed with status {resp.status_code}"
            )
            # Inspect every Set-Cookie header on the response for
            # ``remember_token``.  Flask-Login uses that name by
            # default; a future bump that renames it will fail this
            # assertion and force us to update the test deliberately.
            set_cookies = resp.headers.getlist("Set-Cookie")
            remember_cookies = [
                c for c in set_cookies if c.startswith("remember_token=")
            ]
            assert len(remember_cookies) == 1, (
                f"Expected exactly one remember_token cookie, got "
                f"{len(remember_cookies)}: {set_cookies!r}"
            )
            cookie = remember_cookies[0]
            # Header is case-insensitive but the canonical Flask
            # output uses ``Secure``, ``HttpOnly``, ``SameSite=Lax``.
            # Lowercase the cookie string for the substring checks
            # so a future Werkzeug change in casing does not break
            # the test.
            cookie_lower = cookie.lower()
            assert "secure" in cookie_lower, (
                f"remember_token missing Secure: {cookie!r}"
            )
            assert "httponly" in cookie_lower, (
                f"remember_token missing HttpOnly: {cookie!r}"
            )
            assert "samesite=lax" in cookie_lower, (
                f"remember_token missing SameSite=Lax: {cookie!r}"
            )
        finally:
            for key, value in original.items():
                if value is None:
                    app.config.pop(key, None)
                else:
                    app.config[key] = value
