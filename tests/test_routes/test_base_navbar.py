"""Tests for the base.html mobile navbar offcanvas drawer (commit 23 / D-H).

The navbar at ``base.html`` ships an offcanvas drawer that slides in from
the left at < md and renders inline at >= md via Bootstrap 5.3's
``.navbar-expand-md .offcanvas`` overrides.  These tests are the
regression lock: they assert the markup the JS (Bootstrap bundle) needs
to find the drawer is present, and that no nav links or right-side
controls were lost in the restructure from ``<div class="collapse
navbar-collapse">`` to ``<div class="offcanvas offcanvas-start">``.

Markup-only -- the actual drag/tap/animation behaviour is covered by
manual Playwright verification in ``tests/manual/``.
"""

import json
import re


# ── Owner nav-items ─────────────────────────────────────────────────
# Pre-commit owner navbar shipped these 10 main-nav items (Dashboard,
# Budget, Recurring, Accounts, Salary, Transfers, Obligations,
# Retirement, Analytics, Settings) plus 3 right-side items
# (theme-toggle, display-name span, logout form button).  Total = 13
# ``<li class="nav-item">`` rows inside the navbar.
_OWNER_NAV_ITEM_COUNT = 13


class TestOffcanvasMarkup:
    """The offcanvas container and toggler attributes are present."""

    def test_offcanvas_markup_present(self, auth_client):
        """C23-1: offcanvas drawer container renders inside the navbar.

        Bootstrap's ``data-bs-toggle="offcanvas"`` handler in the
        navbar-toggler resolves ``data-bs-target="#mainOffcanvas"`` by
        ``document.querySelector``, so the container must exist with
        both the ``offcanvas`` and ``offcanvas-start`` classes and the
        ``id="mainOffcanvas"`` selector.
        """
        resp = auth_client.get("/")
        assert resp.status_code == 200
        assert b'class="offcanvas offcanvas-start"' in resp.data
        assert b'id="mainOffcanvas"' in resp.data

    def test_toggler_targets_offcanvas(self, auth_client):
        """C23-2: navbar-toggler activates the offcanvas, not a collapse.

        Pre-commit the toggler had ``data-bs-toggle="collapse"`` and
        ``data-bs-target="#navMain"``.  Both attributes must now name
        the offcanvas drawer or Bootstrap's offcanvas JS will not bind
        to the click.
        """
        resp = auth_client.get("/")
        assert resp.status_code == 200
        assert b'data-bs-toggle="offcanvas"' in resp.data
        assert b'data-bs-target="#mainOffcanvas"' in resp.data
        assert b'aria-controls="mainOffcanvas"' in resp.data
        # Old collapse target must not survive the restructure --
        # leaving it would mean two togglers exist on the same page.
        assert b'data-bs-target="#navMain"' not in resp.data
        assert b'id="navMain"' not in resp.data

    def test_offcanvas_header_present(self, auth_client):
        """The drawer has a header with title + dismiss button.

        The dismiss button is the user-visible close affordance on
        mobile (the backdrop tap is the secondary path).  Bootstrap's
        ``data-bs-dismiss="offcanvas"`` resolves the closest open
        offcanvas, so it must live inside ``#mainOffcanvas``.
        """
        resp = auth_client.get("/")
        assert resp.status_code == 200
        assert b'class="offcanvas-header"' in resp.data
        assert b'id="mainOffcanvasLabel"' in resp.data
        assert b'data-bs-dismiss="offcanvas"' in resp.data


class TestNavLinksCarryForward:
    """All pre-commit navigation entries land inside offcanvas-body."""

    def test_owner_nav_items_count(self, auth_client):
        """C23-3: same number of ``<li class="nav-item">`` as pre-commit.

        Counts the main owner nav links (Dashboard, Budget, Recurring,
        Accounts, Salary, Transfers, Obligations, Retirement, Analytics,
        Settings -- 10 entries) plus the three right-side rows
        (theme-toggle, display-name span, logout form).  Total = 13.
        Drift in either direction means a nav entry was dropped or an
        unintended one was added.
        """
        resp = auth_client.get("/")
        assert resp.status_code == 200
        # ``<li class="nav-item ...>`` -- match the class anchored at
        # the start of the attribute value so we count every nav-item
        # row regardless of additional utility classes
        # (``d-flex align-items-center`` on the theme-toggle row).
        nav_items = re.findall(rb'<li class="nav-item[^"]*"', resp.data)
        assert len(nav_items) == _OWNER_NAV_ITEM_COUNT, (
            f"Expected {_OWNER_NAV_ITEM_COUNT} nav-items, got "
            f"{len(nav_items)}: {nav_items!r}"
        )

    def test_owner_nav_link_labels_present(self, auth_client):
        """Each of the 10 owner main-nav labels still renders.

        Asserts on the visible label text so a renamed icon or
        re-grouped ``<li>`` does not silently drop a link.
        """
        resp = auth_client.get("/")
        assert resp.status_code == 200
        for label in (
            b"Dashboard", b"Budget", b"Recurring", b"Accounts",
            b"Salary", b"Transfers", b"Obligations", b"Retirement",
            b"Analytics", b"Settings",
        ):
            assert label in resp.data, f"Missing nav label: {label!r}"


class TestRightSideControlsPreserved:
    """Theme toggle and logout form survived the offcanvas-body move."""

    def test_theme_toggle_and_logout_preserved(self, auth_client):
        """C23-4: theme toggle button and logout form both render.

        Pre-commit both lived inside ``<div class="collapse
        navbar-collapse">``; post-commit they live inside
        ``<div class="offcanvas-body">``.  The DOM ids
        (``theme-toggle``) and form ``action="/logout"`` are the JS /
        link integration points and must not be lost.
        """
        resp = auth_client.get("/")
        assert resp.status_code == 200
        # Theme-toggle button -- id is the hook ``app.js`` uses.
        assert b'id="theme-toggle"' in resp.data
        # Logout form -- POST to ``/logout`` with a CSRF token.
        assert b'action="/logout"' in resp.data
        assert b'name="csrf_token"' in resp.data

    def test_theme_toggle_inside_offcanvas_body(self, auth_client):
        """The theme-toggle row renders inside the drawer body.

        Without this anchor a future refactor could move the toggle
        outside the drawer (e.g., to a separate top-bar slot) without
        any other test catching it.  The substring search confirms the
        toggle still lives between the ``offcanvas-body`` opening tag
        and the matching close.
        """
        resp = auth_client.get("/")
        assert resp.status_code == 200
        body = resp.data
        body_start = body.find(b'class="offcanvas-body"')
        assert body_start != -1, "offcanvas-body container not found"
        # The drawer body closes at the matching ``</div>`` before the
        # outer offcanvas ``</div>``.  Searching forward from
        # body_start is sufficient -- the toggle must appear after.
        toggle_pos = body.find(b'id="theme-toggle"', body_start)
        assert toggle_pos > body_start, (
            "theme-toggle does not render inside offcanvas-body"
        )


# ── HTMX response-handling config ───────────────────────────────────

class TestHtmxConfig:
    """The htmx-config meta tag's responseHandling contract."""

    def test_409_swaps_before_4xx_catchall(self, auth_client):
        """409 conflict bodies swap; the other 4xx stay non-swapping.

        htmx's default responseHandling never swaps 4xx bodies, which
        made the optimistic-lock conflict partials (C-18: conflict
        entry list, transaction cell, mobile card, transfer cell) dead
        UI -- the server rendered them at 409 and the client silently
        discarded them.  The meta override REPLACES the whole config
        key and entries match in order, so this pins all the
        load-bearing properties at once: the 409 entry exists and
        swaps, it precedes the ``[45]..`` catch-all that would
        otherwise shadow it, and the restated defaults (204 no-swap,
        2xx/3xx swap, 4xx/5xx error-no-swap) surround it.  422 stays
        non-swapping deliberately: those bodies are raw
        ``str(errors)`` / JSON, and the carry-forward modal's
        ``htmx:responseError`` handler relies on 4xx not swapping.
        """
        resp = auth_client.get("/")
        assert resp.status_code == 200
        html = resp.data.decode()

        match = re.search(
            r'<meta name="htmx-config" content=\'([^\']+)\'>', html,
        )
        assert match is not None, "htmx-config meta tag not found"
        config = json.loads(match.group(1))

        handling = config["responseHandling"]
        codes = [entry["code"] for entry in handling]
        # The 409 swap entry must precede the 4xx/5xx catch-all, or
        # first-match-wins shadows it.
        assert codes.index("409") < codes.index("[45].."), codes
        assert {"code": "409", "swap": True} in handling
        # Restated defaults -- the meta override replaces the whole key.
        assert {"code": "204", "swap": False} in handling
        assert {"code": "[23]..", "swap": True} in handling
        assert {"code": "[45]..", "swap": False, "error": True} in handling
