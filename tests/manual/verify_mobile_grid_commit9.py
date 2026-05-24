"""Headless mobile verification for Commit 9 of the v3 plan.

Drives Playwright Chromium against the local dev Flask server and
asserts the swipe-left-to-reveal-Mark-Paid behaviour introduced in
Commit 9 of the mobile-first v3 implementation:

  - On a mobile viewport (375 x 812, has_touch=True), a horizontal
    left swipe past 50 px on a Projected card adds the ``.swiped``
    class and the ``.swipe-action-mark-paid`` button is rendered.
  - Swipe-left on a second card auto-closes any prior ``.swiped``.
  - Swipe-right past 50 px on a swiped card removes ``.swiped``.
  - A dominantly-vertical swipe (down + left) does NOT register --
    the touchmove ``Math.abs(dy) > Math.abs(dx)`` guard cancels the
    swipe so vertical scroll wins.
  - Tap outside a swiped card un-swipes it; tap on the swiped card
    body also un-swipes (the click handler's "any swiped? close all"
    branch covers both).
  - Tap on the revealed Paid button fires an HTMX POST to
    ``/transactions/<id>/mark-done`` -- the network monitor confirms
    the request landed on that exact URL.
  - On a desktop viewport (1920 x 1080, has_touch=False), no
    ``.mobile-card-wrapper`` is visible (the wrapper sits inside
    ``.d-md-none``), the swipe handlers cannot fire on mouse, and
    cards render normally.

This script complements the static-render coverage in
``tests/test_routes/test_grid.py::TestMobileSwipeAction``: pytest
verifies the server emits the right HTML; this script verifies the
browser-side JS (touch handlers, the synthetic-click suppression,
the HTMX submit) actually fires.

Prerequisites:

- Playwright installed in the venv
  (``.venv/bin/pip install -r requirements-dev.txt``).
- Chromium headless-shell downloaded
  (``.venv/bin/playwright install chromium``).
- The dev Flask server running, bound to ``172.32.0.1:5000``
  (``flask run --host 172.32.0.1``).
- A logged-in session saved to ``tests/manual/.dev_session_state.json``
  via ``save_dev_session.py``.
- At least one Projected expense transaction visible in the current
  period (the "This Period" tab default).

The script is intentionally NOT a pytest test -- it needs an external
process (the dev server) and an external state file (the cookie), and
the OS-level Chromium dependency is not available in CI.

Usage::

    .venv/bin/python tests/manual/verify_mobile_grid_commit9.py

Exit code 0 = all assertions passed; non-zero = something failed.
Screenshots are written to ``tests/manual/screenshots/``.

Side effects: opens and closes per-card action bars / swipe states
several times; the mark-paid request scenario DOES write to the dev
DB (a single Projected -> Paid transition).  If the dev DB is a
disposable scratch state this is fine; otherwise comment out
``verify_swipe_paid_button_fires_mark_done`` before running.
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

# The CSS transition on .mobile-txn-card is 150 ms; pad to 250 ms so
# the swipe-translate has finished before the next assertion reads
# the inline transform.
SWIPE_TRANSITION_MS = 250


@dataclass
class CheckResult:
    """Single assertion outcome.  ``ok`` is the success flag,
    ``detail`` is a short note shown in the summary."""

    name: str
    ok: bool
    detail: str = ""


def check(results: list[CheckResult], name: str, fn: Callable[[], str]) -> None:
    """Run ``fn`` and record success/failure.

    ``fn`` should return a short success detail string on pass and
    raise ``AssertionError`` (or any exception) on fail; the exception
    message is recorded.  Wrapping every check this way lets the
    script run every assertion even when an earlier one failed --
    useful for catching cascading regressions in one pass.
    """
    try:
        detail = fn() or ""
        results.append(CheckResult(name=name, ok=True, detail=detail))
    except (AssertionError, Exception) as exc:  # pylint: disable=broad-except
        results.append(
            CheckResult(name=name, ok=False, detail=f"{type(exc).__name__}: {exc}"),
        )


def shot(page: Page, label: str) -> None:
    """Save a screenshot under SHOT_DIR with a stable per-step name."""
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SHOT_DIR / f"commit9_{label}.png"
    page.screenshot(path=str(path), full_page=True)


# ---- card / wrapper helpers ----------------------------------------------


def _find_swipeable_card_ids(page: Page, pane_selector: str) -> list[str]:
    """Return the ``data-mobile-txn-id`` values of every
    swipe-able card in the given pane.

    A swipe-able card is one whose wrapper contains a
    ``.swipe-action-mark-paid`` button -- i.e. a Projected (or other
    non-settled) row whose ``render_row_card`` emit included the
    button.  Settled rows are excluded by construction.
    """
    ids = page.evaluate(
        """(pane) => {
            const root = document.querySelector(pane);
            if (!root) return [];
            const wrappers = root.querySelectorAll(
                '.mobile-card-wrapper:has(.swipe-action-mark-paid)'
            );
            const out = [];
            wrappers.forEach((w) => {
                const card = w.querySelector(
                    '.mobile-txn-card[data-mobile-txn-id]'
                );
                if (card) out.push(card.getAttribute('data-mobile-txn-id'));
            });
            return out;
        }""",
        pane_selector,
    )
    return list(ids) if ids else []


def _card_has_class(page: Page, txn_id: str, klass: str) -> bool:
    """Return whether the card matching ``data-mobile-txn-id=txn_id``
    carries ``klass`` in its classList."""
    return bool(
        page.evaluate(
            """([id, k]) => {
                const c = document.querySelector(
                    '.mobile-txn-card[data-mobile-txn-id="' + id + '"]'
                );
                return c ? c.classList.contains(k) : false;
            }""",
            [txn_id, klass],
        )
    )


def _card_visible_paid_button(page: Page, txn_id: str) -> bool:
    """Return whether the card's sibling ``.swipe-action-mark-paid``
    button is present in the DOM.

    Visibility (the actual reveal) is gated by the ``.swiped`` class
    on the sibling card; this helper checks DOM presence only so a
    settled card returns False here too."""
    return bool(
        page.evaluate(
            """([id]) => {
                const c = document.querySelector(
                    '.mobile-txn-card[data-mobile-txn-id="' + id + '"]'
                );
                if (!c) return false;
                const wrapper = c.closest('.mobile-card-wrapper');
                if (!wrapper) return false;
                return !!wrapper.querySelector('.swipe-action-mark-paid');
            }""",
            [txn_id],
        )
    )


# ---- touch event synthesis -----------------------------------------------


def _dispatch_touch(
    page: Page, kind: str, txn_id: str, client_x: float, client_y: float,
    *, is_touches: bool = True,
) -> None:
    """Dispatch a TouchEvent of ``kind`` on the card matching
    ``txn_id`` at the given client coordinates.

    ``is_touches=True`` populates ``touches`` and ``targetTouches``
    (used for touchstart / touchmove); ``False`` populates only
    ``changedTouches`` (used for touchend, where the spec says no
    touches remain).
    """
    page.evaluate(
        """([id, kind, x, y, isTouches]) => {
            const c = document.querySelector(
                '.mobile-txn-card[data-mobile-txn-id="' + id + '"]'
            );
            if (!c) throw new Error('no card for txn ' + id);
            const t = new Touch({
                identifier: 0, target: c, clientX: x, clientY: y,
            });
            const init = {
                bubbles: true, cancelable: true,
                touches: isTouches ? [t] : [],
                targetTouches: isTouches ? [t] : [],
                changedTouches: [t],
            };
            c.dispatchEvent(new TouchEvent(kind, init));
        }""",
        [txn_id, kind, client_x, client_y, is_touches],
    )


def _swipe_left(page: Page, txn_id: str, distance: int = 100) -> None:
    """Synthesize a horizontal left swipe on ``txn_id`` past the 50 px
    threshold.

    Default distance 100 px is comfortably past the 50 px threshold.
    Three events fire: touchstart at the card's center, touchmove to
    (center - distance), touchend at the same final point.
    """
    rect = page.evaluate(
        """([id]) => {
            const c = document.querySelector(
                '.mobile-txn-card[data-mobile-txn-id="' + id + '"]'
            );
            const r = c.getBoundingClientRect();
            return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
        }""",
        [txn_id],
    )
    start_x = float(rect["x"])
    start_y = float(rect["y"])
    end_x = start_x - distance

    _dispatch_touch(page, "touchstart", txn_id, start_x, start_y)
    _dispatch_touch(page, "touchmove", txn_id, end_x, start_y)
    _dispatch_touch(
        page, "touchend", txn_id, end_x, start_y, is_touches=False,
    )


def _swipe_right(page: Page, txn_id: str, distance: int = 100) -> None:
    """Synthesize a horizontal right swipe on ``txn_id`` past the
    50 px threshold."""
    rect = page.evaluate(
        """([id]) => {
            const c = document.querySelector(
                '.mobile-txn-card[data-mobile-txn-id="' + id + '"]'
            );
            const r = c.getBoundingClientRect();
            return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
        }""",
        [txn_id],
    )
    start_x = float(rect["x"])
    start_y = float(rect["y"])
    end_x = start_x + distance

    _dispatch_touch(page, "touchstart", txn_id, start_x, start_y)
    _dispatch_touch(page, "touchmove", txn_id, end_x, start_y)
    _dispatch_touch(
        page, "touchend", txn_id, end_x, start_y, is_touches=False,
    )


def _swipe_diagonal(
    page: Page, txn_id: str, dx: int = -100, dy: int = -150,
) -> None:
    """Synthesize a dominantly-vertical diagonal swipe on ``txn_id``.

    Default ``dy=-150`` exceeds ``|dx|=100`` so the touchmove guard
    cancels the swipe-tracking on the card.  Used to assert that
    vertical-dominant motion does NOT register as a horizontal swipe
    (so vertical page scroll still wins under real fingers).
    """
    rect = page.evaluate(
        """([id]) => {
            const c = document.querySelector(
                '.mobile-txn-card[data-mobile-txn-id="' + id + '"]'
            );
            const r = c.getBoundingClientRect();
            return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
        }""",
        [txn_id],
    )
    start_x = float(rect["x"])
    start_y = float(rect["y"])

    _dispatch_touch(page, "touchstart", txn_id, start_x, start_y)
    _dispatch_touch(page, "touchmove", txn_id, start_x + dx, start_y + dy)
    _dispatch_touch(
        page, "touchend", txn_id, start_x + dx, start_y + dy,
        is_touches=False,
    )


def _click_outside_all_cards(page: Page) -> None:
    """Dispatch a click on the page body, outside any
    ``.mobile-txn-card``.

    Used to verify the click handler's "any swiped card? close all
    and return" branch fires on an outside-tap.
    """
    page.evaluate(
        """() => {
            const body = document.body;
            body.dispatchEvent(new MouseEvent('click', {
                bubbles: true, cancelable: true,
                clientX: 5, clientY: 5,
            }));
        }"""
    )


# ---- verification scenarios ----------------------------------------------


def verify_swipe_left_reveals_paid_button(
    page: Page, results: list[CheckResult]
) -> None:
    """A horizontal left swipe past 50 px adds ``.swiped`` to the
    card and the swipe-action button is in the DOM."""
    page.goto(f"{DEV_BASE_URL}/grid", wait_until="domcontentloaded")
    page.wait_for_selector("#mobile-this-period", state="attached")
    shot(page, "01_mobile_initial_load")

    try:
        ids = _find_swipeable_card_ids(page, "#mobile-this-period")
    except Exception as exc:  # pylint: disable=broad-except
        results.append(CheckResult(
            name="mobile_found_swipeable_cards",
            ok=False, detail=f"{type(exc).__name__}: {exc}",
        ))
        return

    if not ids:
        results.append(CheckResult(
            name="mobile_found_swipeable_cards",
            ok=False,
            detail="no swipeable card found inside #mobile-this-period",
        ))
        return

    txn_id = ids[0]
    results.append(CheckResult(
        name="mobile_found_swipeable_cards",
        ok=True, detail=f"txn_id={txn_id} (and {len(ids) - 1} others)",
    ))

    check(
        results,
        "paid_button_in_dom_before_swipe",
        lambda: "ok" if _card_visible_paid_button(page, txn_id)
        else (_ for _ in ()).throw(
            AssertionError(
                "swipe-action-mark-paid button not in DOM "
                "(render_row_card emit may have regressed)"
            ),
        ),
    )

    check(
        results,
        "card_not_swiped_before_swipe",
        lambda: "ok" if not _card_has_class(page, txn_id, "swiped")
        else (_ for _ in ()).throw(
            AssertionError(".swiped class set on card before any swipe"),
        ),
    )

    _swipe_left(page, txn_id)
    page.wait_for_timeout(SWIPE_TRANSITION_MS)
    shot(page, "02_after_swipe_left")

    check(
        results,
        "card_swiped_after_swipe_left",
        lambda: "ok" if _card_has_class(page, txn_id, "swiped")
        else (_ for _ in ()).throw(
            AssertionError(
                ".swiped class missing on card after swipe-left past 50 px"
            ),
        ),
    )


def verify_second_swipe_auto_closes_first(
    page: Page, results: list[CheckResult]
) -> None:
    """Swiping a second card closes the first card's swipe."""
    page.goto(f"{DEV_BASE_URL}/grid", wait_until="domcontentloaded")
    page.wait_for_selector("#mobile-this-period", state="attached")
    ids = _find_swipeable_card_ids(page, "#mobile-this-period")

    if len(ids) < 2:
        results.append(CheckResult(
            name="second_swipe_needs_two_cards",
            ok=False,
            detail=(
                f"need 2+ swipeable cards in This Period, found "
                f"{len(ids)} -- seed another Projected expense"
            ),
        ))
        return

    first, second = ids[0], ids[1]

    _swipe_left(page, first)
    page.wait_for_timeout(SWIPE_TRANSITION_MS)
    if not _card_has_class(page, first, "swiped"):
        results.append(CheckResult(
            name="setup_first_card_swiped",
            ok=False, detail="first card did not gain .swiped",
        ))
        return

    _swipe_left(page, second)
    page.wait_for_timeout(SWIPE_TRANSITION_MS)
    shot(page, "03_second_swipe_closes_first")

    check(
        results,
        "first_card_unswiped_after_second_swipe",
        lambda: "ok" if not _card_has_class(page, first, "swiped")
        else (_ for _ in ()).throw(
            AssertionError(
                "first card still .swiped after second card was swiped -- "
                "the one-open-at-a-time invariant broke"
            ),
        ),
    )

    check(
        results,
        "second_card_swiped",
        lambda: "ok" if _card_has_class(page, second, "swiped")
        else (_ for _ in ()).throw(
            AssertionError(
                "second card not .swiped after its own swipe"
            ),
        ),
    )


def verify_swipe_right_unswipes(
    page: Page, results: list[CheckResult]
) -> None:
    """A horizontal right swipe on a swiped card removes
    ``.swiped``."""
    page.goto(f"{DEV_BASE_URL}/grid", wait_until="domcontentloaded")
    page.wait_for_selector("#mobile-this-period", state="attached")
    ids = _find_swipeable_card_ids(page, "#mobile-this-period")

    if not ids:
        results.append(CheckResult(
            name="swipe_right_needs_swipeable_card",
            ok=False, detail="no swipeable card",
        ))
        return

    txn_id = ids[0]
    _swipe_left(page, txn_id)
    page.wait_for_timeout(SWIPE_TRANSITION_MS)
    if not _card_has_class(page, txn_id, "swiped"):
        results.append(CheckResult(
            name="setup_card_swiped_for_unswipe",
            ok=False, detail="card did not enter .swiped state",
        ))
        return

    _swipe_right(page, txn_id)
    page.wait_for_timeout(SWIPE_TRANSITION_MS)
    shot(page, "04_after_swipe_right")

    check(
        results,
        "card_unswiped_after_swipe_right",
        lambda: "ok" if not _card_has_class(page, txn_id, "swiped")
        else (_ for _ in ()).throw(
            AssertionError(
                "card still .swiped after swipe-right past 50 px"
            ),
        ),
    )


def verify_vertical_swipe_does_not_trigger(
    page: Page, results: list[CheckResult]
) -> None:
    """A dominantly-vertical diagonal swipe does NOT add ``.swiped``.

    The touchmove guard ``Math.abs(dy) > Math.abs(dx)`` cancels swipe
    tracking; touchend then short-circuits because the card's
    ``_swipeStartX`` is undefined.
    """
    page.goto(f"{DEV_BASE_URL}/grid", wait_until="domcontentloaded")
    page.wait_for_selector("#mobile-this-period", state="attached")
    ids = _find_swipeable_card_ids(page, "#mobile-this-period")

    if not ids:
        results.append(CheckResult(
            name="vertical_swipe_needs_swipeable_card",
            ok=False, detail="no swipeable card",
        ))
        return

    txn_id = ids[0]
    _swipe_diagonal(page, txn_id, dx=-100, dy=-150)
    page.wait_for_timeout(SWIPE_TRANSITION_MS)
    shot(page, "05_after_vertical_swipe")

    check(
        results,
        "vertical_swipe_did_not_set_swiped",
        lambda: "ok" if not _card_has_class(page, txn_id, "swiped")
        else (_ for _ in ()).throw(
            AssertionError(
                ".swiped class added despite vertical-dominant gesture"
            ),
        ),
    )


def verify_tap_outside_unswipes(
    page: Page, results: list[CheckResult]
) -> None:
    """Clicking outside any swiped card un-swipes it."""
    page.goto(f"{DEV_BASE_URL}/grid", wait_until="domcontentloaded")
    page.wait_for_selector("#mobile-this-period", state="attached")
    ids = _find_swipeable_card_ids(page, "#mobile-this-period")

    if not ids:
        results.append(CheckResult(
            name="tap_outside_needs_swipeable_card",
            ok=False, detail="no swipeable card",
        ))
        return

    txn_id = ids[0]
    _swipe_left(page, txn_id)
    page.wait_for_timeout(SWIPE_TRANSITION_MS)
    if not _card_has_class(page, txn_id, "swiped"):
        results.append(CheckResult(
            name="setup_card_swiped_for_tap_outside",
            ok=False, detail="card did not enter .swiped state",
        ))
        return

    _click_outside_all_cards(page)
    page.wait_for_timeout(SWIPE_TRANSITION_MS)
    shot(page, "06_after_tap_outside")

    check(
        results,
        "card_unswiped_after_tap_outside",
        lambda: "ok" if not _card_has_class(page, txn_id, "swiped")
        else (_ for _ in ()).throw(
            AssertionError(
                "card still .swiped after click-outside"
            ),
        ),
    )


def verify_swipe_paid_button_fires_mark_done(
    page: Page, results: list[CheckResult]
) -> None:
    """Tapping the revealed Paid button fires an HTMX POST to
    ``mark_done`` for the same txn id.

    Network-monitors the request before clicking; reports the URL of
    the captured POST so a regression that re-routes the button
    elsewhere fails loudly with the wrong URL in the detail string.

    NOTE: this scenario writes to the dev DB (one mark-done
    transition).  Comment out the call site in ``main()`` if the dev
    DB must stay untouched.
    """
    page.goto(f"{DEV_BASE_URL}/grid", wait_until="domcontentloaded")
    page.wait_for_selector("#mobile-this-period", state="attached")
    ids = _find_swipeable_card_ids(page, "#mobile-this-period")

    if not ids:
        results.append(CheckResult(
            name="paid_button_needs_swipeable_card",
            ok=False, detail="no swipeable card",
        ))
        return

    txn_id = ids[0]
    _swipe_left(page, txn_id)
    page.wait_for_timeout(SWIPE_TRANSITION_MS)

    captured: list[str] = []

    def _handler(req):
        if req.method == "POST" and "/mark-done" in req.url:
            captured.append(req.url)

    page.on("request", _handler)

    page.evaluate(
        """([id]) => {
            const c = document.querySelector(
                '.mobile-txn-card[data-mobile-txn-id="' + id + '"]'
            );
            const wrapper = c.closest('.mobile-card-wrapper');
            const btn = wrapper.querySelector('.swipe-action-mark-paid');
            btn.click();
        }""",
        [txn_id],
    )

    # Wait briefly for the HTMX request to land.  The route returns
    # 200 + HX-Trigger=gridRefresh which triggers a page reload; we
    # do NOT wait for the reload because the captured-request check
    # only needs the network event.
    page.wait_for_timeout(500)
    page.remove_listener("request", _handler)
    shot(page, "07_after_paid_button_click")

    check(
        results,
        "mark_done_request_captured",
        lambda: f"url={captured[0]}" if (
            captured and f"/transactions/{txn_id}/mark-done" in captured[0]
        )
        else (_ for _ in ()).throw(
            AssertionError(
                f"expected POST to /transactions/{txn_id}/mark-done, "
                f"captured: {captured!r}"
            ),
        ),
    )


def verify_desktop_no_wrapper_visible(
    page: Page, results: list[CheckResult]
) -> None:
    """At desktop viewport the mobile card wrapper is hidden via
    ``.d-md-none`` on the parent ``#mobile-grid``; the desktop grid
    table is the visible affordance.

    Loose check that the swipe-action machinery only matters on
    mobile; the wrapper IS in the DOM (the partial still renders;
    Bootstrap's ``.d-md-none`` hides it at media query).
    """
    page.goto(f"{DEV_BASE_URL}/grid", wait_until="domcontentloaded")
    page.wait_for_selector(".grid-table", state="attached")
    shot(page, "08_desktop_initial_load")

    check(
        results,
        "mobile_grid_hidden_on_desktop",
        lambda: "ok" if page.evaluate(
            "() => {"
            "  const el = document.getElementById('mobile-grid');"
            "  if (!el) return true;"
            "  return getComputedStyle(el).display === 'none';"
            "}"
        )
        else (_ for _ in ()).throw(
            AssertionError(
                "#mobile-grid is visible on desktop viewport "
                "(d-md-none did not hide it)"
            ),
        ),
    )

    check(
        results,
        "desktop_grid_table_visible",
        lambda: "ok" if page.evaluate(
            "() => !!document.querySelector('.grid-table')"
        )
        else (_ for _ in ()).throw(
            AssertionError(".grid-table missing on desktop /grid"),
        ),
    )


# ---- main -----------------------------------------------------------------


def main() -> int:
    """Run the verification harness; print a summary; return exit
    code."""
    if not STATE_FILE.exists():
        print(
            f"Missing {STATE_FILE}.  Run save_dev_session.py first.",
            file=sys.stderr,
        )
        return 2

    results: list[CheckResult] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        # Mobile context first.  has_touch=True enables the global
        # Touch and TouchEvent constructors that our synthetic-touch
        # helpers rely on; viewport width < 768 makes the mobile-only
        # CSS paths apply.
        mobile_ctx = browser.new_context(
            storage_state=str(STATE_FILE),
            viewport=MOBILE_VIEWPORT,
            has_touch=True,
            is_mobile=True,
        )
        mobile_page = mobile_ctx.new_page()
        verify_swipe_left_reveals_paid_button(mobile_page, results)
        verify_second_swipe_auto_closes_first(mobile_page, results)
        verify_swipe_right_unswipes(mobile_page, results)
        verify_vertical_swipe_does_not_trigger(mobile_page, results)
        verify_tap_outside_unswipes(mobile_page, results)
        # The next call writes to the dev DB (single mark-done
        # transition).  Comment out if the dev DB must stay
        # untouched.
        verify_swipe_paid_button_fires_mark_done(mobile_page, results)
        mobile_ctx.close()

        # Desktop context.  Wide viewport + no touch confirms the
        # mobile partials stay hidden and the desktop table renders.
        desktop_ctx = browser.new_context(
            storage_state=str(STATE_FILE),
            viewport=DESKTOP_VIEWPORT,
            has_touch=False,
            is_mobile=False,
        )
        desktop_page = desktop_ctx.new_page()
        verify_desktop_no_wrapper_visible(desktop_page, results)
        desktop_ctx.close()

        browser.close()

    width = max(len(r.name) for r in results) + 2
    passed = sum(1 for r in results if r.ok)
    failed = sum(1 for r in results if not r.ok)
    print()
    print("Commit 9 mobile verification results")
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
