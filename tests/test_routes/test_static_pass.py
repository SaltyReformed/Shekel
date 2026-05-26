"""Tests for the /sw.js root-scope service-worker passthrough route.

The service worker must be served at the root URL (``/sw.js``)
rather than under ``/static/`` so the browser scopes the worker
to ``/`` -- see ``app/routes/static_pass.py`` docstring for the
scope rationale.  These tests pin three properties:

  1. The route returns 200 with the on-disk ``app/static/sw.js``
     body.  A missing route would silently fall back to a 404 and
     no worker would ever install.
  2. The response carries ``Content-Type: application/javascript``.
     Some browsers (notably Firefox, per Web/API/ServiceWorker) refuse
     to install a worker served with ``text/javascript`` or
     ``text/plain``; the explicit mimetype on
     :func:`send_from_directory` is load-bearing.
  3. The route is reachable without authentication.  The browser
     fetches ``/sw.js`` on every page load to check for an updated
     worker (per the HTTP cache rules SW uses internally), including
     before any user session is established.
"""

from pathlib import Path


class TestServiceWorkerPassthrough:
    """Tests for GET /sw.js."""

    def test_sw_js_returns_200(self, app, client, db):
        """GET /sw.js returns 200 OK."""
        response = client.get("/sw.js")
        assert response.status_code == 200

    def test_sw_js_content_type_is_javascript(self, app, client, db):
        """GET /sw.js responds with application/javascript.

        Firefox refuses to register a service worker served with
        any other Content-Type (the spec requires a JavaScript MIME
        type; ``text/plain`` and ``text/javascript`` are rejected).
        """
        response = client.get("/sw.js")
        assert "application/javascript" in response.content_type

    def test_sw_js_no_authentication_required(self, app, client, db):
        """GET /sw.js is reachable without an authenticated session.

        The browser issues this request on every page load -- including
        the login page itself -- to check for worker updates.  Any
        authentication gate would make the worker invisible to logged-
        out visitors.  The ``client`` fixture is unauthenticated.
        """
        response = client.get("/sw.js")
        assert response.status_code == 200

    def test_sw_js_body_matches_on_disk_file(self, app, client, db):
        """The /sw.js response body matches app/static/sw.js verbatim.

        Verifies the passthrough is reading the real file and not
        rendering a template or serving stale content.
        """
        response = client.get("/sw.js")
        on_disk = Path(app.static_folder) / "sw.js"
        assert response.data == on_disk.read_bytes()

    def test_sw_js_body_contains_static_cache_invariant(
        self, app, client, db
    ):
        """The served worker enforces the static-only cache invariant.

        Asserts the response body declares a versioned
        ``shekel-static-v*`` cache name and lists the ``/static/``
        prefix array.  Regression lock against any future edit that
        switches the worker to a stale-while-revalidate strategy for
        HTML or to a broader cache scope -- both of which would
        re-open the financial-correctness hole D-I in
        ``docs/implementation_plan_mobile_v3.md`` Section 2 closes.

        Matches the cache name on a prefix so a future bump
        (``shekel-static-v3``, etc.) does not require a paired
        test edit; the name is allowed to evolve so long as the
        ``shekel-static-v`` prefix stays and the activate handler
        evicts every previous version.
        """
        import re  # pylint: disable=import-outside-toplevel
        body = client.get("/sw.js").data.decode("utf-8")
        assert re.search(r"shekel-static-v\d+", body) is not None
        assert "/static/" in body
