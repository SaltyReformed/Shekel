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

import os
import re
from pathlib import Path

from app.routes.static_pass import (
    _VERSION_PLACEHOLDER,
    _static_asset_version,
    static_file_version,
)


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


class TestStaticFileVersion:
    """Tests for the per-file content hash behind static ``?v=`` URLs.

    ``static_file_version`` backs the cache-busting URL parameter that
    ``_register_static_versioning`` (app/__init__.py) appends to every
    ``url_for('static', ...)`` URL; see
    docs/design/css_architecture_audit.md (cache-busting gap).  These
    tests exercise the helper directly against a temp directory so no
    repo static file is ever touched.
    """

    def test_version_is_deterministic_for_unchanged_content(self, tmp_path):
        """Same bytes produce the same 12-hex-char version every call.

        A version that varied between calls would make every page load
        emit different asset URLs and defeat caching entirely.
        """
        asset = tmp_path / "a.css"
        asset.write_text("body { color: red; }", encoding="utf-8")
        first = static_file_version(str(tmp_path), "a.css")
        second = static_file_version(str(tmp_path), "a.css")
        assert first is not None
        assert first == second
        assert re.fullmatch(r"[0-9a-f]{12}", first)

    def test_version_changes_when_content_changes(self, tmp_path):
        """Changed bytes produce a different version (the cache bust).

        The mtime is bumped explicitly because the helper memoizes by
        mtime, and two writes can land within one filesystem timestamp
        granule; the explicit bump models a real edit deterministically.
        """
        asset = tmp_path / "a.css"
        asset.write_text("body { color: red; }", encoding="utf-8")
        before = static_file_version(str(tmp_path), "a.css")
        asset.write_text("body { color: blue; }", encoding="utf-8")
        stat = asset.stat()
        os.utime(asset, (stat.st_atime, stat.st_mtime + 2))
        after = static_file_version(str(tmp_path), "a.css")
        assert before is not None
        assert after is not None
        assert before != after

    def test_missing_file_returns_none(self, tmp_path):
        """A filename that does not exist yields None (unversioned URL).

        The url_defaults hook then emits the plain URL rather than
        failing the page render over a bad asset reference.
        """
        assert static_file_version(str(tmp_path), "missing.css") is None

    def test_path_escape_returns_none(self, tmp_path):
        """A filename escaping the static folder yields None.

        ``safe_join`` rejects traversal, so the helper can never hash
        (and thereby confirm the existence of) a file outside the
        static folder.
        """
        outside = tmp_path / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        static_root = tmp_path / "static"
        static_root.mkdir()
        assert static_file_version(str(static_root), "../outside.txt") is None
