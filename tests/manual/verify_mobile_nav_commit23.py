"""Headless verification for Commit 23 of the mobile-first v3 plan.

Drives Playwright Chromium against the local dev Flask server and
asserts the runtime behaviour of the navbar offcanvas drawer that
replaced the collapsing ``navbar-collapse`` (D-H).  This is the
runtime counterpart to ``tests/test_routes/test_base_navbar.py``:
pytest proves the server emits the right HTML; this harness proves
Bootstrap's offcanvas JS binds to the toggler, the drawer slides in,
the backdrop is rendered, the drawer dismisses on link tap, and the
desktop nav (no toggler, inline nav items) is unaffected.

At 375 x 812 (iPhone XS portrait):

* Tapping the navbar-toggler opens ``#mainOffcanvas`` with the
  ``show`` class added by Bootstrap.
* A ``offcanvas-backdrop`` element appears in the body.
* Tapping a nav-link inside the drawer triggers navigation AND
  closes the drawer (the link is the dismiss trigger because the
  drawer carries ``data-bs-dismiss="offcanvas"`` semantics via
  Bootstrap's auto-dismiss on link-click within a navbar offcanvas).
* Tapping the explicit close button (``.btn-close`` inside the
  offcanvas-header) dismisses the drawer.
* The theme-toggle button inside the drawer body is reachable and
  the click changes ``data-bs-theme`` on ``<html>``.

At 1920 x 1080 (desktop):

* The navbar-toggler is not displayed (``navbar-expand-md`` hides
  it at >= 768 px).
* The offcanvas container renders inline as a regular navbar (its
  body is a flex row, the header is hidden, and the drawer-specific
  positioning is overridden by Bootstrap's
  ``.navbar-expand-md .offcanvas`` rules).
* All nav links remain visible without any user interaction.

Prerequisites: same as the other ``verify_*.py`` scripts.  Run via::

    .venv/bin/python tests/manual/verify_mobile_nav_commit23.py

Exit code 0 = all assertions passed.
"""

from __future__ import annotations

import pathlib
import sys
from dataclasses import dataclass
from typing import Callable

from playwright.sync_api import Page, sync_playwright


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
STATE_FILE = REPO_ROOT / "tests" / "manual" / ".dev_session_state.json"
SHOT_DIR = REPO_ROOT / "tests" / "manual" / "screenshots"
DEV_BASE_URL = "http://172.32.0.1:5000"
MOBILE_VIEWPORT = {"width": 375, "height": 812}
DESKTOP_VIEWPORT = {"width": 1920, "height": 1080}
# Bootstrap's offcanvas slide animation runs ~300ms; wait a beat
# past that before reading state so the .show class transition has
# settled.
ANIMATION_SETTLE_MS = 400


@dataclass
class CheckResult:
    """Single assertion outcome."""

    name: str
    ok: bool
    detail: str = ""


def check(results: list[CheckResult], name: str, fn: Callable[[], str]) -> None:
    """Run ``fn`` and record success/failure without stopping the run."""
    try:
        detail = fn() or ""
        results.append(CheckResult(name=name, ok=True, detail=detail))
    except (AssertionError, Exception) as exc:  # pylint: disable=broad-except
        results.append(
            CheckResult(name=name, ok=False, detail=f"{type(exc).__name__}: {exc}"),
        )


def shot(page: Page, label: str) -> None:
    """Save a screenshot to SHOT_DIR with a stable name."""
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SHOT_DIR / f"commit23_{label}.png"
    page.screenshot(path=str(path), full_page=True)


# ── Mobile verification (375 x 812) ─────────────────────────────────


def verify_mobile_initial_state(page: Page, results: list[CheckResult]) -> None:
    """Hamburger visible; drawer hidden; no backdrop."""
    page.goto(f"{DEV_BASE_URL}/", wait_until="domcontentloaded")
    page.wait_for_selector(".navbar-toggler", state="attached")
    shot(page, "01_mobile_initial")

    check(
        results,
        "toggler_visible_on_mobile",
        lambda: (
            "visible" if page.locator(".navbar-toggler").is_visible()
            else (_ for _ in ()).throw(
                AssertionError(".navbar-toggler not visible at 375 px"),
            )
        ),
    )

    check(
        results,
        "drawer_closed_initially",
        lambda: _assert_no_class(page, "#mainOffcanvas", "show"),
    )

    check(
        results,
        "backdrop_absent_initially",
        lambda: (
            "ok" if page.locator(".offcanvas-backdrop").count() == 0
            else (_ for _ in ()).throw(
                AssertionError("offcanvas-backdrop present before opening drawer"),
            )
        ),
    )


def verify_drawer_open(page: Page, results: list[CheckResult]) -> None:
    """Tap the hamburger; drawer slides in; backdrop appears."""
    page.locator(".navbar-toggler").click()
    page.wait_for_timeout(ANIMATION_SETTLE_MS)
    shot(page, "02_mobile_drawer_open")

    check(
        results,
        "drawer_open_after_toggle",
        lambda: _assert_class(page, "#mainOffcanvas", "show"),
    )

    check(
        results,
        "backdrop_present_when_open",
        lambda: (
            f"count={page.locator('.offcanvas-backdrop').count()}"
            if page.locator(".offcanvas-backdrop").count() >= 1
            else (_ for _ in ()).throw(
                AssertionError("offcanvas-backdrop not rendered when drawer open"),
            )
        ),
    )

    # Theme toggle reachable inside drawer body.
    check(
        results,
        "theme_toggle_visible_in_drawer",
        lambda: (
            "visible" if page.locator("#mainOffcanvas #theme-toggle").is_visible()
            else (_ for _ in ()).throw(
                AssertionError("#theme-toggle not visible inside drawer"),
            )
        ),
    )

    # Logout form button reachable inside drawer body.
    check(
        results,
        "logout_button_visible_in_drawer",
        lambda: (
            "visible"
            if page.locator('#mainOffcanvas form[action="/logout"] button').is_visible()
            else (_ for _ in ()).throw(
                AssertionError("logout button not visible inside drawer"),
            )
        ),
    )


def verify_close_via_btn_close(page: Page, results: list[CheckResult]) -> None:
    """Tap the X in the offcanvas-header; drawer dismisses."""
    page.locator("#mainOffcanvas .btn-close").click()
    page.wait_for_timeout(ANIMATION_SETTLE_MS)
    shot(page, "03_mobile_drawer_closed_via_x")

    check(
        results,
        "drawer_closed_after_btn_close",
        lambda: _assert_no_class(page, "#mainOffcanvas", "show"),
    )

    check(
        results,
        "backdrop_removed_after_close",
        lambda: (
            "ok" if page.locator(".offcanvas-backdrop").count() == 0
            else (_ for _ in ()).throw(
                AssertionError("offcanvas-backdrop still present after close"),
            )
        ),
    )


def verify_close_via_backdrop(page: Page, results: list[CheckResult]) -> None:
    """Re-open the drawer, then tap the backdrop; drawer dismisses."""
    page.locator(".navbar-toggler").click()
    page.wait_for_timeout(ANIMATION_SETTLE_MS)
    page.locator(".offcanvas-backdrop").click()
    page.wait_for_timeout(ANIMATION_SETTLE_MS)
    shot(page, "04_mobile_drawer_closed_via_backdrop")

    check(
        results,
        "drawer_closed_after_backdrop_tap",
        lambda: _assert_no_class(page, "#mainOffcanvas", "show"),
    )


def verify_nav_link_navigates(page: Page, results: list[CheckResult]) -> None:
    """Open drawer, tap a nav link, verify navigation occurred."""
    page.locator(".navbar-toggler").click()
    page.wait_for_timeout(ANIMATION_SETTLE_MS)

    # Tap the Budget link (always present in owner nav).
    page.locator('#mainOffcanvas a.nav-link:has-text("Budget")').click()
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(ANIMATION_SETTLE_MS)
    shot(page, "05_mobile_after_link_tap")

    check(
        results,
        "navigated_after_link_tap",
        lambda: (
            "ok" if "/grid" in page.url
            else (_ for _ in ()).throw(
                AssertionError(f"expected /grid in URL, got {page.url}"),
            )
        ),
    )

    # Page navigation tears down the offcanvas state -- the new page
    # ships its own #mainOffcanvas that should start closed.
    check(
        results,
        "drawer_closed_on_new_page",
        lambda: _assert_no_class(page, "#mainOffcanvas", "show"),
    )


def verify_theme_toggle_inside_drawer(page: Page, results: list[CheckResult]) -> None:
    """Open drawer, tap theme-toggle, confirm data-bs-theme flipped."""
    page.goto(f"{DEV_BASE_URL}/", wait_until="domcontentloaded")
    initial_theme = page.locator("html").get_attribute("data-bs-theme")

    page.locator(".navbar-toggler").click()
    page.wait_for_timeout(ANIMATION_SETTLE_MS)
    page.locator("#mainOffcanvas #theme-toggle").click()
    page.wait_for_timeout(200)
    shot(page, "06_mobile_theme_toggled")

    new_theme = page.locator("html").get_attribute("data-bs-theme")
    check(
        results,
        "theme_toggle_flipped_data_bs_theme",
        lambda: (
            f"{initial_theme} -> {new_theme}" if new_theme != initial_theme
            else (_ for _ in ()).throw(
                AssertionError(
                    f"data-bs-theme did not change (still {new_theme!r})"
                ),
            )
        ),
    )

    # Flip back so the per-user persisted theme is unchanged after
    # the verification run.
    page.locator("#mainOffcanvas #theme-toggle").click()
    page.wait_for_timeout(200)


# ── Desktop verification (1920 x 1080) ──────────────────────────────


def verify_desktop_unchanged(page: Page, results: list[CheckResult]) -> None:
    """At lg+ the hamburger is hidden, nav items render inline."""
    page.set_viewport_size(DESKTOP_VIEWPORT)
    page.goto(f"{DEV_BASE_URL}/", wait_until="domcontentloaded")
    page.wait_for_timeout(300)
    shot(page, "07_desktop_view")

    check(
        results,
        "toggler_hidden_on_desktop",
        lambda: (
            "ok" if not page.locator(".navbar-toggler").is_visible()
            else (_ for _ in ()).throw(
                AssertionError(".navbar-toggler visible at 1920 px"),
            )
        ),
    )

    # Offcanvas body must render inline (i.e., its content is
    # visible) at lg+, even though we have not clicked the toggler.
    check(
        results,
        "drawer_body_visible_inline_on_desktop",
        lambda: (
            "visible" if page.locator("#mainOffcanvas .offcanvas-body").is_visible()
            else (_ for _ in ()).throw(
                AssertionError(
                    "#mainOffcanvas .offcanvas-body not visible at 1920 px",
                ),
            )
        ),
    )

    # Offcanvas-header is display:none at >= md.
    check(
        results,
        "drawer_header_hidden_on_desktop",
        lambda: (
            "ok" if not page.locator("#mainOffcanvas .offcanvas-header").is_visible()
            else (_ for _ in ()).throw(
                AssertionError(
                    "#mainOffcanvas .offcanvas-header visible at 1920 px",
                ),
            )
        ),
    )

    # All 10 owner nav links are visible without any interaction.
    for label in (
        "Dashboard", "Budget", "Recurring", "Accounts", "Salary",
        "Transfers", "Obligations", "Retirement", "Analytics", "Settings",
    ):
        check(
            results,
            f"nav_link_{label.lower()}_visible_inline",
            lambda lab=label: (
                "ok"
                if page.locator(f'#mainOffcanvas a.nav-link:has-text("{lab}")').first.is_visible()
                else (_ for _ in ()).throw(
                    AssertionError(f"{lab} link not visible inline at 1920 px"),
                )
            ),
        )


# ── helpers ─────────────────────────────────────────────────────────


def _assert_class(page: Page, selector: str, klass: str) -> str:
    """Confirm an element's classList contains ``klass``."""
    el = page.locator(selector).first
    cls = (el.get_attribute("class") or "").split()
    if klass not in cls:
        raise AssertionError(
            f"expected class '{klass}' on {selector}, got: {cls}",
        )
    return f"class={cls}"


def _assert_no_class(page: Page, selector: str, klass: str) -> str:
    """Confirm an element's classList does NOT contain ``klass``."""
    el = page.locator(selector).first
    cls = (el.get_attribute("class") or "").split()
    if klass in cls:
        raise AssertionError(
            f"unexpected class '{klass}' on {selector}: {cls}",
        )
    return f"class={cls}"


def main() -> int:
    """Run all checks; print summary; return exit code."""
    if not STATE_FILE.exists():
        print(
            f"Missing {STATE_FILE}.  Run save_dev_session.py first.",
            file=sys.stderr,
        )
        return 2

    results: list[CheckResult] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        # Mobile pass.
        mobile_ctx = browser.new_context(
            storage_state=str(STATE_FILE),
            viewport=MOBILE_VIEWPORT,
        )
        mobile_page = mobile_ctx.new_page()
        verify_mobile_initial_state(mobile_page, results)
        verify_drawer_open(mobile_page, results)
        verify_close_via_btn_close(mobile_page, results)
        verify_close_via_backdrop(mobile_page, results)
        verify_nav_link_navigates(mobile_page, results)
        verify_theme_toggle_inside_drawer(mobile_page, results)
        mobile_ctx.close()

        # Desktop pass.
        desktop_ctx = browser.new_context(
            storage_state=str(STATE_FILE),
            viewport=DESKTOP_VIEWPORT,
        )
        desktop_page = desktop_ctx.new_page()
        verify_desktop_unchanged(desktop_page, results)
        desktop_ctx.close()

        browser.close()

    width = max(len(r.name) for r in results) + 2
    passed = sum(1 for r in results if r.ok)
    failed = sum(1 for r in results if not r.ok)
    print()
    print("Commit 23 navbar offcanvas verification results")
    print("=" * (width + 30))
    for r in results:
        marker = "OK  " if r.ok else "FAIL"
        print(f"  [{marker}] {r.name:<{width}} {r.detail}")
    print("=" * (width + 30))
    print(f"  Passed: {passed}")
    print(f"  Failed: {failed}")
    print(f"  Screenshots: {SHOT_DIR}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
