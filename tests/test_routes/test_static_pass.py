"""Tests for the /sw.js root-scope service-worker passthrough route.

The service worker must be served at the root URL (``/sw.js``)
rather than under ``/static/`` so the browser scopes the worker
to ``/`` -- see ``app/routes/static_pass.py`` docstring for the
scope rationale.  These tests pin:

  1. The route returns 200 with the on-disk ``app/static/sw.js``
     body, with the ``__ASSET_VERSION__`` placeholder substituted by a
     content hash of the cached static assets (Option A cache-busting).
     A missing route would silently 404 and no worker would install.
  2. The response carries ``Content-Type: application/javascript``.
     Some browsers (notably Firefox) refuse to install a worker served
     with ``text/javascript`` or ``text/plain``; the explicit mimetype
     is load-bearing.
  3. The route is reachable without authentication.  The browser
     fetches ``/sw.js`` on every page load to check for an updated
     worker, including before any user session is established.
  4. The injected cache version is deterministic across requests, so
     the worker only reinstalls when an asset actually changes.
"""

import re
from pathlib import Path

from app.routes.static_pass import _VERSION_PLACEHOLDER, _static_asset_version


class TestServiceWorkerPassthrough:
    """Tests for GET /sw.js."""

    def test_sw_js_returns_200(self, app, client, db):
        """GET /sw.js returns 200 OK."""
        response = client.get("/sw.js")
        assert response.status_code == 200

    def test_sw_js_content_type_is_javascript(self, app, client, db):
        """GET /sw.js responds with application/javascript.

        Firefox refuses to register a service worker served with any
        other Content-Type (the spec requires a JavaScript MIME type;
        ``text/plain`` and ``text/javascript`` are rejected).
        """
        response = client.get("/sw.js")
        assert "application/javascript" in response.content_type

    def test_sw_js_no_authentication_required(self, app, client, db):
        """GET /sw.js is reachable without an authenticated session.

        The browser issues this request on every page load, including
        the login page itself, to check for worker updates.  Any
        authentication gate would make the worker invisible to logged-
        out visitors.  The ``client`` fixture is unauthenticated.
        """
        response = client.get("/sw.js")
        assert response.status_code == 200

    def test_sw_js_body_is_on_disk_file_with_version_substituted(
        self, app, client, db
    ):
        """The /sw.js body is app/static/sw.js with the version filled in.

        The route substitutes the ``__ASSET_VERSION__`` placeholder in
        the on-disk worker with a content hash of the cached static
        assets.  Verifies the passthrough reads the real file and
        changes only the placeholder: the served body equals the on-disk
        source with the placeholder replaced by the computed version,
        and no longer contains the raw placeholder.
        """
        response = client.get("/sw.js")
        on_disk = (Path(app.static_folder) / "sw.js").read_text(
            encoding="utf-8"
        )
        version = _static_asset_version(app.static_folder)
        expected = on_disk.replace(_VERSION_PLACEHOLDER, version)
        body = response.data.decode("utf-8")
        assert body == expected
        assert _VERSION_PLACEHOLDER not in body

    def test_sw_js_body_contains_static_cache_invariant(
        self, app, client, db
    ):
        """The served worker enforces the static-only cache invariant.

        Asserts the response body declares a ``shekel-static-<hash>``
        cache name (the version is a content hash injected by the route,
        not a literal version string) and lists the ``/static/`` prefix
        array.  This is a regression lock against any future edit that
        switches the worker to a stale-while-revalidate strategy for
        HTML or to a broader cache scope, both of which would re-open
        the financial-correctness hole D-I in
        ``docs/implementation_plan_mobile_v3.md`` Section 2 closes.
        """
        body = client.get("/sw.js").data.decode("utf-8")
        assert re.search(r"shekel-static-[0-9a-f]{6,}", body) is not None
        assert _VERSION_PLACEHOLDER not in body
        assert "/static/" in body

    def test_sw_js_cache_version_is_deterministic(self, app, client, db):
        """The injected cache version is identical across requests.

        A service worker only reinstalls when its bytes change, so the
        version must be stable within a deployment (it is a content hash
        memoized per static folder).  A version that varied per request
        would make the browser reinstall the worker on every page load
        and never settle.  Also confirms the served version equals the
        route's computed hash, not the raw placeholder.
        """
        first = client.get("/sw.js").data.decode("utf-8")
        second = client.get("/sw.js").data.decode("utf-8")
        assert first == second
        match = re.search(r"shekel-static-([0-9a-f]{6,})", first)
        assert match is not None
        assert match.group(1) == _static_asset_version(app.static_folder)
