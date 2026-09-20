"""Browser drive for a vendored-asset refresh (runbook 4.10, step 3).

Runbook 4.10 asks the operator to open every dashboard plus the grid with
DevTools and confirm each asset loads from ``/static/vendor/...`` with no
CSP violation in the console.  This script is that check, mechanised and
widened to the surfaces the 2026-09-20 UI leaf touched (djlint 1.46.2's
fifteen ``type="button"`` additions, htmx 2.0.4 -> 2.0.10, Bootstrap Icons
1.11.3 -> 1.13.1, Chart.js 4.4.7 -> 4.5.1):

* every page below is loaded with a logged-in session and, per page, the
  script records console errors and warnings, uncaught page errors,
  ``securitypolicyviolation`` events (a CSP refusal), every request under
  ``/static/vendor/`` with its status, ``htmx.version``, ``Chart.version``,
  the number of live ``Chart`` instances against the number of ``<canvas>``
  elements (every canvas must have been drawn), and whether the
  ``bootstrap-icons`` font face is loaded AND at least one ``i.bi`` glyph
  has rendered content (``document.fonts.check`` alone answers true for an
  undeclared face);
* the buttons the leaf gave ``type="button"`` are CLICKED and the swap or
  toggle they drive is asserted, so a button that a form now submits, or
  an htmx trigger that stopped firing, fails here rather than in prod.
  The listeners stay attached for the whole session, so a console error,
  CSP refusal or non-200 vendor request raised by a swapped-in fragment
  is recorded against the click that caused it; a selector that matches
  nothing is a finding, never a skip.
* the debt-strategy calculation is submitted (a compute-only POST, no
  commit) so that dashboard's chart, which only exists after a
  calculation, is drawn under the refreshed Chart.js.

WHAT IT WRITES: the Recurring page's Monthly / Per-paycheck toggle commits
a ``user_settings`` row per click; the drive reads which unit is active,
clicks the other, then the original, so the stored unit ends where it
began.  The theme toggle is flipped back the same way.  Run it against a
CLONE all the same; nothing else it clicks writes.

Usage::

    .venv/bin/python tests/manual/verify_vendor_refresh.py OUT.json \\
        [--base-url http://172.32.0.1:5000] \\
        [--storage-state tests/manual/.dev_session_state.json]

Run ``save_dev_session.py`` first (it prompts for the dev user's password).
Exits 1 on any CSP violation, console error, uncaught page error, non-200
vendor request, version mismatch or failed interaction; the JSON holds the
full record either way.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout, sync_playwright

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_STATE = REPO_ROOT / "tests" / "manual" / ".dev_session_state.json"
DEFAULT_BASE_URL = "http://172.32.0.1:5000"

# The versions the manifest pins after the refresh; the drive fails if the
# served files report anything else, which is how a stale browser cache or
# a container serving another checkout shows itself.
EXPECTED_HTMX = "2.0.10"
EXPECTED_CHARTJS = "4.5.1"

# Pages whose templates load Chart.js (runbook 4.10 names the dashboards),
# the grid, and the surfaces whose buttons the leaf edited.  Account ids
# are the 2026-09-20 production dump's (6 = the 401(k), 8 = the van loan,
# 11 = the house); pass ``--skip-accounts`` on a clone with other ids.
PAGES: tuple[tuple[str, bool], ...] = (
    ("/dashboard", True),
    ("/grid", False),
    ("/analytics", True),
    ("/debt-strategy", True),
    ("/retirement", True),
    ("/savings", True),
    ("/templates", False),
    ("/settings", False),
    ("/accounts/6/investment", True),
    ("/accounts/8/loan", True),
    ("/accounts/11/property", True),
)

CSP_HOOK = """
window.__cspViolations = [];
document.addEventListener('securitypolicyviolation', function (e) {
  window.__cspViolations.push({
    directive: e.violatedDirective,
    blocked: e.blockedURI,
    source: e.sourceFile,
    line: e.lineNumber,
  });
});
"""

PAGE_FACTS = """
() => ({
  htmx: window.htmx ? window.htmx.version : null,
  chart: window.Chart ? window.Chart.version : null,
  canvases: document.querySelectorAll('canvas').length,
  chartInstances: window.Chart
    ? Object.keys(window.Chart.instances || {}).length : 0,
  iconsFontLoaded: document.fonts.check('1em "bootstrap-icons"'),
  iconGlyphs: Array.from(document.querySelectorAll('i.bi')).slice(0, 5)
    .map(el => getComputedStyle(el, '::before').content)
    .filter(c => c && c !== 'none' && c !== '""').length,
  cspViolations: window.__cspViolations || [],
})
"""


class Recorder:
    """Session-long listeners whose bucket is swapped per page load or click.

    Attached once to the page; ``start(label)`` opens a fresh bucket that
    every console error, uncaught page error and vendor response lands in
    until the next ``start``.  A CSP violation is read off the page
    (``window.__cspViolations``) by the caller, since it accrues in the
    document rather than in an event the driver sees.
    """

    def __init__(self, page: Page) -> None:
        """Attach the three listeners to ``page`` for the session's life."""
        self.bucket: dict = self._empty()
        page.on("console", self._on_console)
        page.on("pageerror", lambda err: self.bucket["page_errors"].append(str(err)))
        page.on("response", self._on_response)

    @staticmethod
    def _empty() -> dict:
        """A fresh bucket: nothing seen yet."""
        return {"console": [], "page_errors": [], "vendor_requests": []}

    def start(self) -> None:
        """Open a fresh bucket for the next page load or click."""
        self.bucket = self._empty()

    def _on_console(self, msg) -> None:
        """Keep errors and warnings; the rest is noise."""
        if msg.type in ("error", "warning"):
            self.bucket["console"].append({"type": msg.type, "text": msg.text})

    def _on_response(self, resp) -> None:
        """Keep every vendored-asset response with its status."""
        if "/static/vendor/" in resp.url:
            self.bucket["vendor_requests"].append({"url": resp.url, "status": resp.status})


def _drive_page(page: Page, rec: Recorder, base_url: str, path: str) -> dict:
    """Load one page and return its record (see the module docstring)."""
    rec.start()
    response = page.goto(f"{base_url}{path}", wait_until="networkidle")
    facts = page.evaluate(PAGE_FACTS)
    return {
        "path": path,
        "status": response.status if response else None,
        "final_url": page.url,
        **rec.bucket,
        **facts,
    }


def _settle(page: Page) -> bool:
    """Wait until every htmx request has finished and the network is idle.

    htmx marks the indicator (and the triggering element) with
    ``htmx-request`` for the request's life; clicking the next control
    while one is in flight lands on a button the swap is about to
    replace, which is the flake this waits out.

    Returns:
        ``False`` when a request was still in flight after 15 s, which
        the grader reports as its own finding.
    """
    try:
        page.wait_for_function(
            "() => document.querySelector('.htmx-request') === null", timeout=15000
        )
    except PlaywrightTimeout:
        return False
    page.wait_for_load_state("networkidle")
    return True


def _click_and_expect_change(page: Page, rec: Recorder, selector: str, watched: str) -> dict:
    """Click ``selector`` and report whether ``watched``'s HTML changed.

    The htmx buttons the leaf edited swap ``#tab-content`` or
    ``#recurring-body``; the theme toggle flips ``data-bs-theme``.  A
    ``type="button"`` that had been a submit button would navigate away
    instead (``navigated: true``), and an htmx trigger that stopped firing
    would leave the target unchanged (``changed: false``).
    """
    before = page.evaluate(f"() => document.querySelector('{watched}')?.outerHTML")
    # A marker on ``window`` survives an htmx swap or a pushState and dies
    # with a full navigation, which is what a submit button would cause;
    # the URL alone cannot tell them apart (the analytics pills push URLs).
    page.evaluate("() => { window.__driveMarker = true; window.__cspViolations = []; }")
    rec.start()
    page.locator(selector).first.click()
    settled = _settle(page)
    after = page.evaluate(f"() => document.querySelector('{watched}')?.outerHTML")
    return {
        "selector": selector,
        "watched": watched,
        "changed": after != before,
        "settled": settled,
        "navigated": not page.evaluate("() => window.__driveMarker === true"),
        "cspViolations": page.evaluate("() => window.__cspViolations || []"),
        **rec.bucket,
    }


def _click_each(
    page: Page, rec: Recorder, label: str, selectors: tuple[str, ...], watched: str
) -> list[dict]:
    """Click every selector in order and record each outcome.

    A selector that matches nothing is recorded as ``absent`` and graded as
    a finding: the click order below is arranged so every control exists
    when its turn comes (the picker's "Next" arrow is clicked after
    "Previous" has stepped back; the statement window buttons after
    "Income Statement" is showing), so an absence means the page lost a
    control or the selector no longer names it.
    """
    results: list[dict] = []
    for selector in selectors:
        if page.locator(selector).count() == 0:
            results.append({"page": label, "selector": selector, "absent": True})
            continue
        results.append({"page": label, **_click_and_expect_change(page, rec, selector, watched)})
    return results


def _drive_theme_toggle(page: Page, rec: Recorder, base_url: str) -> list[dict]:
    """base.html: the theme toggle (app.js) flips ``data-bs-theme``."""
    page.goto(f"{base_url}/dashboard", wait_until="networkidle")
    results = _click_each(page, rec, "/dashboard", ("#theme-toggle",), "html")
    if results[0].get("changed"):
        # Flip it back so the drive leaves the saved preference where it was.
        page.click("#theme-toggle")
    return results


def _drive_analytics(page: Page, rec: Recorder, base_url: str) -> list[dict]:
    """analytics.html, the calendar partials and the statements toggle."""
    page.goto(f"{base_url}/analytics", wait_until="networkidle")
    results = _click_each(
        page,
        rec,
        "/analytics",
        tuple(
            f"button.nav-link:has-text('{label}')" for label in ("Calendar", "Statements", "Taxes")
        ),
        "#tab-content",
    )
    # _calendar_month.html / _calendar_year.html / _picker_macros.html: the
    # Year <-> Month switch and the period picker's arrows.
    page.click("button.nav-link:has-text('Calendar')")
    _settle(page)
    results += _click_each(
        page,
        rec,
        "/analytics calendar",
        (
            "#tab-content button:has-text('Year')",
            "#tab-content button:has-text('Month')",
            "#tab-content button[aria-label^='Previous']",
            "#tab-content button[aria-label^='Next']:not([disabled])",
        ),
        "#tab-content",
    )
    # _statements_toggle.html / _income_statement.html: statement type and
    # window buttons.
    page.click("button.nav-link:has-text('Statements')")
    _settle(page)
    results += _click_each(
        page,
        rec,
        "/analytics statements",
        (
            "#tab-content button:has-text('Balance Sheet')",
            "#tab-content button:has-text('Income Statement')",
            "#tab-content button:has-text('Month')",
            "#tab-content button:has-text('Year')",
            "#tab-content button:has-text('Pay Period')",
        ),
        "#tab-content",
    )
    return results


def _drive_templates(page: Page, rec: Recorder, base_url: str) -> list[dict]:
    """templates/list.html: the Monthly / Per-paycheck toggle.

    hx-post on each button sends that button's own ``name=unit`` (the
    comment the leaf corrected) and swaps ``#recurring-body``; the route
    COMMITS the unit to ``user_settings``, so the drive clicks the inactive
    unit first and the original second, leaving the stored value as found.
    """
    page.goto(f"{base_url}/templates", wait_until="networkidle")
    if page.locator("#recurring-body").count() == 0:
        return [{"page": "/templates", "selector": "#recurring-body", "absent": True}]
    active = page.locator("[data-unit-value].btn-primary").first
    if active.count() == 0:
        return [
            {"page": "/templates", "selector": "[data-unit-value].btn-primary", "absent": True}
        ]
    original = active.get_attribute("data-unit-value")
    other = "per_paycheck" if original == "monthly" else "monthly"
    return _click_each(
        page,
        rec,
        "/templates",
        tuple(f"[data-unit-value='{unit}']" for unit in (other, original)),
        "#recurring-body",
    )


def _drive_debt_strategy(page: Page, rec: Recorder, base_url: str) -> list[dict]:
    """debt_strategy/dashboard.html: the calculation draws the chart.

    ``#strategy-form`` posts to ``debt_strategy.calculate`` (compute only,
    nothing committed) and swaps ``#results``, whose partial renders the
    one ``<canvas>`` this dashboard has; the page-load record above saw
    0/0 because the chart does not exist until then.
    """
    page.goto(f"{base_url}/debt-strategy", wait_until="networkidle")
    results = _click_each(
        page, rec, "/debt-strategy", ("#strategy-form button[type='submit']",), "#results"
    )
    facts = page.evaluate(PAGE_FACTS)
    results[0]["canvases"] = facts["canvases"]
    results[0]["chartInstances"] = facts["chartInstances"]
    results[0]["chart"] = facts["chart"]
    return results


def _drive_grid_entries(page: Page, rec: Recorder, base_url: str) -> list[dict]:
    """grid/_transaction_entries.html: the pencil (hx-get) opens the edit
    form in place.  The delete button is NOT clicked.

    The inline entries list is server-rendered inside the MOBILE grid's
    collapsed card (``#card-expansion-tp-<txn>``, opened by tapping the
    card's summary row), so this surface is driven at phone width and the
    card is expanded first; at desktop width the same list only exists
    behind a cell's full-edit popover.
    """
    page.set_viewport_size({"width": 390, "height": 844})
    try:
        page.goto(f"{base_url}/grid", wait_until="networkidle")
        pencil = page.locator("button[aria-label='Edit entry']").first
        if pencil.count() == 0:
            selector = "button[aria-label='Edit entry']"
            return [{"page": "/grid entries", "selector": selector, "absent": True}]
        host = pencil.evaluate("el => el.getAttribute('hx-target')")
        expansion = host.replace("#entry-list-", "card-expansion-")
        page.click(f"[aria-controls='{expansion}']")
        page.wait_for_selector("button[aria-label='Edit entry']:visible", timeout=8000)
        outcome = _click_and_expect_change(
            page, rec, "button[aria-label='Edit entry']:visible", host
        )
        return [{"page": "/grid entries (mobile)", **outcome}]
    finally:
        page.set_viewport_size({"width": 1440, "height": 900})


def _drive_interactions(page: Page, rec: Recorder, base_url: str) -> list[dict]:
    """Click the buttons the leaf edited and record each outcome."""
    return (
        _drive_theme_toggle(page, rec, base_url)
        + _drive_analytics(page, rec, base_url)
        + _drive_templates(page, rec, base_url)
        + _drive_debt_strategy(page, rec, base_url)
        + _drive_grid_entries(page, rec, base_url)
    )


def _grade_page(rec: dict) -> list[str]:
    """The findings one page record carries; empty means it passed."""
    p = rec["path"]
    findings: list[str] = []
    if rec["status"] != 200:
        findings.append(f"{p}: HTTP {rec['status']} (final url {rec['final_url']})")
    findings += [f"{p}: CSP violation {v}" for v in rec["cspViolations"]]
    findings += [
        f"{p}: console error: {c['text'][:200]}" for c in rec["console"] if c["type"] == "error"
    ]
    findings += [f"{p}: page error: {e[:200]}" for e in rec["page_errors"]]
    findings += [
        f"{p}: vendor request {v['url']} -> {v['status']}"
        for v in rec["vendor_requests"]
        if v["status"] != 200
    ]
    if rec["htmx"] != EXPECTED_HTMX:
        findings.append(f"{p}: htmx.version {rec['htmx']!r} != {EXPECTED_HTMX}")
    if rec["chart"] is not None and rec["chart"] != EXPECTED_CHARTJS:
        findings.append(f"{p}: Chart.version {rec['chart']!r} != {EXPECTED_CHARTJS}")
    if not rec["iconsFontLoaded"]:
        findings.append(f"{p}: bootstrap-icons font face not loaded")
    if rec["iconGlyphs"] == 0:
        findings.append(f"{p}: no rendered icon glyph among the first five i.bi")
    return findings


def _grade_click(it: dict) -> list[str]:
    """The findings one click record carries; empty means it passed."""
    where = f"{it.get('page')} {it.get('selector')}"
    if it.get("absent"):
        return [f"{where}: control ABSENT (selector matched nothing)"]
    findings: list[str] = []
    if it.get("changed") is False:
        findings.append(f"{where}: target unchanged after the click")
    if it.get("settled") is False:
        findings.append(f"{where}: never settled (an htmx request still in flight)")
    if it.get("navigated"):
        findings.append(f"{where}: NAVIGATED (a submit, not a button?)")
    findings += [f"{where}: CSP violation {v}" for v in it.get("cspViolations", [])]
    findings += [
        f"{where}: console error: {c['text'][:200]}"
        for c in it.get("console", [])
        if c["type"] == "error"
    ]
    findings += [f"{where}: page error: {e[:200]}" for e in it.get("page_errors", [])]
    findings += [
        f"{where}: vendor request {v['url']} -> {v['status']}"
        for v in it.get("vendor_requests", [])
        if v["status"] != 200
    ]
    if "canvases" in it and it["chartInstances"] < it["canvases"]:
        findings.append(
            f"{where}: {it['canvases']} canvas(es) but {it['chartInstances']} Chart instance(s)"
        )
    if "canvases" in it and it["canvases"] == 0:
        findings.append(f"{where}: the calculation rendered no canvas")
    return findings


def _grade(records: list[dict], interactions: list[dict]) -> list[str]:
    """Return the findings a reviewer must read; empty means the drive passed."""
    return [f for rec in records for f in _grade_page(rec)] + [
        f for it in interactions for f in _grade_click(it)
    ]


def _print_report(records: list[dict], interactions: list[dict]) -> None:
    """One line per page and per click, for the operator's eye."""
    for rec in records:
        errors = sum(c["type"] == "error" for c in rec["console"])
        icons = "ok" if rec["iconsFontLoaded"] and rec["iconGlyphs"] else "MISSING"
        print(
            f"{rec['path']:28s} {rec['status']}  htmx {rec['htmx']}  "
            f"chart {rec['chart'] or '-':6s}  "
            f"charts {rec['chartInstances']}/{rec['canvases']}  "
            f"vendor {len(rec['vendor_requests'])}  csp {len(rec['cspViolations'])}  "
            f"console-err {errors}  icons {icons}"
        )
    for it in interactions:
        if it.get("absent"):
            outcome = "ABSENT"
        else:
            outcome = "changed" if it.get("changed") else "NO CHANGE"
            outcome += " NAVIGATED" if it.get("navigated") else ""
            errors = sum(c["type"] == "error" for c in it.get("console", []))
            outcome += (
                f"  csp {len(it.get('cspViolations', []))}  console-err {errors}  "
                f"vendor {len(it.get('vendor_requests', []))}"
            )
            if "canvases" in it:
                outcome += f"  charts {it['chartInstances']}/{it['canvases']}"
        print(f"  click {it.get('page', '?'):22s} {it.get('selector')}: {outcome}")


def main(argv: list[str] | None = None) -> int:
    """Drive the pages, write the JSON record, print the grade."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    parser.add_argument("out", type=pathlib.Path, help="where to write the JSON record")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--storage-state", type=pathlib.Path, default=DEFAULT_STATE)
    parser.add_argument(
        "--skip-accounts", action="store_true", help="skip the /accounts/<id>/... pages"
    )
    args = parser.parse_args(argv)
    if not args.storage_state.exists():
        print(
            f"no saved session at {args.storage_state}; run save_dev_session.py first",
            file=sys.stderr,
        )
        return 2

    pages = [
        (p, c) for p, c in PAGES if not (args.skip_accounts and p.startswith("/accounts/"))
    ]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(
            storage_state=str(args.storage_state), viewport={"width": 1440, "height": 900}
        )
        context.add_init_script(CSP_HOOK)
        page = context.new_page()
        rec = Recorder(page)
        page.goto(f"{args.base_url}/dashboard", wait_until="networkidle")
        if "/login" in page.url:
            print(
                f"the saved session at {args.storage_state} is not logged in "
                f"(/dashboard -> {page.url}); re-run save_dev_session.py",
                file=sys.stderr,
            )
            browser.close()
            return 2
        records = [_drive_page(page, rec, args.base_url, path) for path, _ in pages]
        for record, (_, charts_expected) in zip(records, pages):
            if charts_expected and record["chartInstances"] < record["canvases"]:
                record["console"].append(
                    {
                        "type": "error",
                        "text": f"{record['canvases']} canvas(es) but only "
                        f"{record['chartInstances']} Chart instance(s): a chart did not draw",
                    }
                )
        interactions = _drive_interactions(page, rec, args.base_url)
        browser.close()

    findings = _grade(records, interactions)
    args.out.write_text(
        json.dumps(
            {"pages": records, "interactions": interactions, "findings": findings}, indent=2
        ),
        encoding="utf-8",
    )
    _print_report(records, interactions)
    if findings:
        print(f"\n{len(findings)} finding(s):")
        for f in findings:
            print("  -", f)
        return 1
    print("\nVENDOR REFRESH DRIVE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
