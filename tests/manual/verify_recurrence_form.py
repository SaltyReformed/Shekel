"""Drive the recurrence form in a real browser, on the real app (R7b-2).

Plan step R7b-2 rewrote the recurrence picker into three LINKED controls -- a
unit ``<select>``, an interval control, and a placement ``<select>`` -- driven
by ``app/static/js/recurrence_form.js``.  What that script decides is which
controls are VISIBLE and which of the two ``interval_n`` inputs is ENABLED, and
pytest can see neither: the suite asserts over rendered HTML, where a control
hidden by a class, an option hidden by a script, and a style the browser
REFUSED to apply all look identical to one that is fine.

**Both defects this script exists for were found by running it, after the
suite was green and two adversarial reviews had passed the code.**

1. The fixed-interval ``<select>`` was synchronised BY VALUE, and interval
   values are not unique across units -- "1" is offered by paychecks, months
   AND years.  Choosing "months" left the selection on the hidden, disabled
   "1 paycheck" option, so the control rendered BLANK and, because a disabled
   option submits nothing, the form posted no ``interval_n`` at all.  A
   validity test comparing values agreed the selection was fine.
2. ``style="max-width: 6rem"`` on the free-interval box violated
   ``style-src 'self'``.  The width never applied and every render of both
   forms logged a blocked-inline-style error.

Neither is visible to a rendered-HTML assertion, which is why this file is
here and not in ``tests/``.

**It writes nothing.**  Every check reads the form's own DOM; the crafted POSTs
in the refusal pass are all expected to be REFUSED, and the pass asserts that
no template and no recurrence rule was persisted by any of them.  Run it
against a clone all the same.

Preconditions:
  * the containerized dev app is up and answering on ``DEV_BASE_URL``
    (``docker compose -f docker-compose.dev.yml up -d`` from THIS checkout --
    the app bind-mounts the directory compose runs from, so a dev app started
    elsewhere serves that code and this proves nothing about yours);
  * ``tests/manual/.dev_session_state.json`` exists and is unexpired
    (``python tests/manual/save_dev_session.py``).

Usage:
    python tests/manual/verify_recurrence_form.py

Exit code 0 when every check passes, 1 on a failure, 2 when the preconditions
are not met.  It is paced: the dev app runs the real Redis limiter (30/minute
per IP, prod behaviour), and a burst meets 429s.
"""
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

#: Where the dev app answers.  The bridge-gateway bind, matching
#: ``save_dev_session.py`` -- the cookie is host-scoped THERE, so shooting
#: 127.0.0.1 silently returns the login page.
DEV_BASE_URL = "http://172.32.0.1:5000"

#: The storage state ``save_dev_session.py`` writes.
SESSION_STATE = Path(__file__).resolve().parent / ".dev_session_state.json"

#: Marks every row this script creates, so the cleanup pass can find them all
#: even after an aborted run.
MARK = "ZZVERIFY-RECURRENCE"

#: How long to wait between crafted POSTs.  See the module docstring.
POST_SPACING_SECONDS = 2.5

_failures: list[str] = []


def _sql(query: str) -> list[str]:
    """Return the rows of one query against the dev database.

    Args:
        query: SQL to run as ``shekel_user`` against the ``shekel`` database
            in the ``shekel-dev-db`` container.

    Returns:
        The non-empty output lines, pipe-separated.
    """
    completed = subprocess.run(
        ["docker", "exec", "shekel-dev-db", "psql", "-U", "shekel_user",
         "-d", "shekel", "-tAF|", "-c", query],
        capture_output=True, text=True, check=True,
    )
    return [line for line in completed.stdout.strip().split("\n") if line]


def _check(label: str, passed: bool, detail: str = "") -> None:
    """Record one assertion and print it.

    Args:
        label: What was checked.
        passed: Whether it holds.
        detail: What was seen instead, printed only on failure.
    """
    print(f"   {'PASS' if passed else 'FAIL'}  {label}"
          + ("" if passed else f" -- {detail}"))
    if not passed:
        _failures.append(f"{label}: {detail}")


def _visible(page, element_id: str) -> bool:
    """Return whether an element is actually rendered to the user.

    Asked of the layout rather than of the class list: ``d-none`` is how this
    form hides a row today, and a check keyed on the class name would pass a
    row hidden some other way and fail a row shown some other way.

    Args:
        page: The Playwright page.
        element_id: The element's ``id``.

    Returns:
        ``True`` when the element occupies space.
    """
    return page.evaluate(
        """(id) => {
            const el = document.getElementById(id);
            if (!el) return null;
            return !!(el.offsetWidth || el.offsetHeight ||
                      el.getClientRects().length);
        }""",
        element_id,
    )


def _posted_intervals(page) -> list[str]:
    """Return the ``interval_n`` values the form would actually submit.

    Read from a real ``FormData``, which is the only thing that accounts for
    BOTH halves of the rule: a disabled control does not submit, and neither
    does a select whose selected option is disabled.

    Args:
        page: The Playwright page.

    Returns:
        Every value the form would post under that name.
    """
    return page.evaluate(
        """() => {
            const form = document.getElementById('recurrence_unit').form;
            return new FormData(form).getAll('interval_n');
        }"""
    )


def _selected_interval_owner(page) -> str | None:
    """Return the ``data-unit`` of the fixed select's chosen option.

    The fact defect 1 turned on: the chosen option must belong to the chosen
    UNIT, which its value alone cannot say.

    Args:
        page: The Playwright page.

    Returns:
        The owning unit id, or ``None`` when nothing is selected.
    """
    return page.evaluate(
        """() => {
            const sel = document.getElementById('interval_n_fixed');
            const opt = sel.options[sel.selectedIndex];
            return opt ? opt.getAttribute('data-unit') : null;
        }"""
    )


def _select_interval(page, unit_id: str, interval_n: int) -> None:
    """Choose the fixed-interval option for one ``(unit, interval)`` PAIR.

    By INDEX, never by value, for the reason defect 1 records: three options
    carry ``value="1"``.

    Args:
        page: The Playwright page.
        unit_id: The ``ref.recurrence_units`` id.
        interval_n: The interval to choose.
    """
    index = page.evaluate(
        """([unitId, n]) => {
            const sel = document.getElementById('interval_n_fixed');
            return Array.from(sel.options).findIndex(
                o => o.getAttribute('data-unit') === String(unitId) &&
                     o.value === String(n));
        }""",
        [unit_id, interval_n],
    )
    if index < 0:
        raise AssertionError(f"no option for unit {unit_id} interval {interval_n}")
    page.evaluate(
        """(i) => {
            const sel = document.getElementById('interval_n_fixed');
            sel.selectedIndex = i;
            sel.dispatchEvent(new Event('change', {bubbles: true}));
        }""",
        index,
    )
    page.wait_for_timeout(200)


def _unit_ids(page) -> dict[str, str]:
    """Return ``{label: ref id}`` for the offered units, read off the form.

    Args:
        page: The Playwright page.

    Returns:
        The mapping, excluding the empty "Does not repeat" entry.
    """
    return {
        label.lower(): value
        for value, label in page.evaluate(
            """() => Array.from(
                   document.getElementById('recurrence_unit').options)
                 .map(o => [o.value, o.textContent.trim()])"""
        )
        if value
    }


def _drive_visibility(page, kind: str, url: str) -> None:
    """Check which controls each cadence shows, on one form kind.

    Args:
        page: The Playwright page.
        kind: "transaction" or "transfer", for the labels.
        url: The create form's path.
    """
    print(f"\n=== {kind}: {url} ===")
    page.goto(f"{DEV_BASE_URL}{url}", wait_until="domcontentloaded")
    page.wait_for_selector("#recurrence_unit")
    units = _unit_ids(page)
    unit = page.locator("#recurrence_unit")

    def one_interval(label: str) -> list[str]:
        """Exactly one interval control submits, in every state."""
        posted = _posted_intervals(page)
        _check(f"{kind} {label}: exactly one interval_n posts",
               len(posted) <= 1, f"posted={posted}")
        return posted

    # Does not repeat: the form's own empty option, not a cadence.
    unit.select_option("")
    page.wait_for_timeout(200)
    _check(f"{kind} A: interval row hidden", not _visible(page, "field-interval"), "shown")
    _check(f"{kind} A: placement row hidden", not _visible(page, "field-placement"), "shown")
    _check(f"{kind} A: day hidden", not _visible(page, "field-dom"), "shown")
    _check(f"{kind} A: month hidden", not _visible(page, "field-moy"), "shown")
    one_interval("A")

    # Paychecks: a free interval, and ONE placement, so that row stays hidden.
    unit.select_option(units["paychecks"])
    page.wait_for_timeout(200)
    _check(f"{kind} B: free box enabled",
           page.evaluate("() => !document.getElementById('interval_n_free').disabled"),
           "disabled")
    _check(f"{kind} B: placement row hidden (one placement offered)",
           not _visible(page, "field-placement"),
           "the Funded-from row is shown with a single usable choice")
    _check(f"{kind} B: day hidden", not _visible(page, "field-dom"), "shown")
    one_interval("B")

    # Months at 1: a day of the month, no month-of-year, both placements.
    unit.select_option(units["months"])
    page.wait_for_timeout(200)
    _check(f"{kind} C: the chosen interval belongs to the chosen unit",
           _selected_interval_owner(page) == units["months"],
           f"owner={_selected_interval_owner(page)} unit={units['months']}")
    _select_interval(page, units["months"], 1)
    _check(f"{kind} C: placement row shown at 1 month",
           _visible(page, "field-placement"), "hidden")
    _check(f"{kind} C: day VISIBLE", _visible(page, "field-dom"), "hidden")
    _check(f"{kind} C: month hidden at 1 month", not _visible(page, "field-moy"), "shown")
    one_interval("C")

    # Months at 1, funded from the month's FIRST paycheck: anchors on a
    # paycheck, so it reads no day of the month.
    page.evaluate(
        """() => { const s = document.getElementById('recurrence_placement');
                   s.selectedIndex = 1;
                   s.dispatchEvent(new Event('change', {bubbles: true})); }""")
    page.wait_for_timeout(200)
    _check(f"{kind} D: day HIDDEN for first-paycheck funding",
           not _visible(page, "field-dom"),
           "a Day of Month input is shown for a cadence that reads no day")

    # Months at 3: no quarterly first-paycheck twin, so the row goes.
    _select_interval(page, units["months"], 3)
    _check(f"{kind} E: placement row hidden at 3 months",
           not _visible(page, "field-placement"), "shown")
    _check(f"{kind} E: day VISIBLE", _visible(page, "field-dom"), "hidden")
    _check(f"{kind} E: month VISIBLE at 3 months", _visible(page, "field-moy"), "hidden")
    one_interval("E")

    # Years: interval 1, cycle twelve months -- the case an "interval > 1"
    # inference got wrong, hiding the Month control on every annual rule.
    unit.select_option(units["years"])
    page.wait_for_timeout(200)
    _check(f"{kind} F: the chosen interval belongs to the chosen unit",
           _selected_interval_owner(page) == units["years"],
           f"owner={_selected_interval_owner(page)}")
    _check(f"{kind} F: day VISIBLE for annual", _visible(page, "field-dom"), "hidden")
    _check(f"{kind} F: month VISIBLE for annual",
           _visible(page, "field-moy"),
           "an annual rule cannot say which month it falls in")
    _check(f"{kind} F: posts interval 1", one_interval("F") == ["1"], "wrong interval")

    preview = page.locator("#recurrence-preview").inner_text().strip()
    _check(f"{kind}: the live preview answered",
           "Could not load preview" not in preview, preview)


def _drive_refusals(context, page) -> None:
    """POST the payloads the form cannot produce, and read the refusal.

    Read off the POST's OWN response body rather than a later page load: the
    API context follows the redirect, and that hop CONSUMES the flash.

    Args:
        context: The Playwright browser context (for its request API).
        page: A page, for the CSRF token.
    """
    print("\n=== refusals: payloads the form cannot produce ===")
    page.goto(f"{DEV_BASE_URL}/templates/new", wait_until="domcontentloaded")
    token = page.evaluate(
        "() => document.querySelector('input[name=csrf_token]').value")

    ids = {
        "category": _sql("SELECT id FROM budget.categories LIMIT 1")[0],
        # By NAME, not LIMIT 1: an unordered pick lands on the Van Loan, which
        # a transaction template cannot target, and the refusal under test is
        # then indistinguishable from that one.
        "account": _sql(
            "SELECT id FROM budget.accounts WHERE name = 'Checking'")[0],
        "expense": _sql(
            "SELECT id FROM ref.transaction_types WHERE name = 'Expense'")[0],
        "month": _sql(
            "SELECT id FROM ref.recurrence_units WHERE name = 'month'")[0],
        "period": _sql(
            "SELECT id FROM ref.recurrence_units WHERE name = 'period'")[0],
        "covering": _sql("SELECT id FROM ref.period_placements "
                         "WHERE name = 'containing_date'")[0],
        "first_pay": _sql("SELECT id FROM ref.period_placements "
                          "WHERE name = 'period_starting_on_or_after'")[0],
    }
    base_form = {
        "name": f"{MARK}-refused", "default_amount": "10.00",
        "category_id": ids["category"], "account_id": ids["account"],
        "transaction_type_id": ids["expense"],
    }
    rules_before = _sql("SELECT count(*) FROM budget.recurrence_rules")[0]

    cases = [
        ("every other month has no pattern",
         {"recurrence_unit": ids["month"], "interval_n": "2",
          "recurrence_placement": ids["covering"]},
         "That repeat schedule cannot be saved yet"),
        ("quarterly funded from the first paycheck has no twin",
         {"recurrence_unit": ids["month"], "interval_n": "3",
          "recurrence_placement": ids["first_pay"]},
         "That repeat schedule cannot be saved yet"),
        ("a unit with no placement key",
         {"recurrence_unit": ids["period"], "interval_n": "1"},
         "Choose which paycheck funds each occurrence"),
        ("a unit with an EMPTY placement",
         {"recurrence_unit": ids["period"], "interval_n": "1",
          "recurrence_placement": ""},
         "Choose which paycheck funds each occurrence"),
        ("an unmodelled unit id",
         {"recurrence_unit": "999999", "interval_n": "1",
          "recurrence_placement": ids["covering"]},
         "Invalid repeat unit"),
        ("an unmodelled placement id",
         {"recurrence_unit": ids["period"], "interval_n": "1",
          "recurrence_placement": "999999"},
         "Invalid funding choice"),
    ]
    for label, cadence, expected in cases:
        response = context.request.post(
            f"{DEV_BASE_URL}/templates",
            form={**base_form, **cadence, "csrf_token": token},
            headers={"Referer": f"{DEV_BASE_URL}/templates/new"},
        )
        if response.status == 429:
            _check(label, False, "429 rate-limited; raise POST_SPACING_SECONDS")
        else:
            _check(f"{label} -> its own message", expected in response.text(),
                   f"status={response.status}")
        time.sleep(POST_SPACING_SECONDS)

    made = _sql("SELECT count(*) FROM budget.transaction_templates "
                f"WHERE name LIKE '{MARK}%'")[0]
    rules_after = _sql("SELECT count(*) FROM budget.recurrence_rules")[0]
    _check("no template was persisted by any refusal", made == "0", f"{made} rows")
    _check("no recurrence rule was persisted by any refusal",
           rules_before == rules_after, f"{rules_before} -> {rules_after}")


def main() -> int:
    """Drive both forms and report.

    Returns:
        0 when every check passes, 1 on failure, 2 when the preconditions are
        not met.
    """
    if not SESSION_STATE.exists():
        print(f"No saved session at {SESSION_STATE}.\n"
              "Run: python tests/manual/save_dev_session.py", file=sys.stderr)
        return 2

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(storage_state=str(SESSION_STATE))
        page = context.new_page()
        console: list[str] = []
        page.on("pageerror", lambda e: console.append(str(e)))
        page.on("console",
                lambda m: console.append(m.text) if m.type == "error" else None)

        page.goto(f"{DEV_BASE_URL}/templates/new", wait_until="domcontentloaded")
        if "/login" in page.url:
            print("Session expired. Re-run save_dev_session.py", file=sys.stderr)
            browser.close()
            return 2

        for kind, url in (("transaction", "/templates/new"),
                          ("transfer", "/transfers/new")):
            _drive_visibility(page, kind, url)
        _drive_refusals(context, page)

        # A blocked inline style is a console error and nothing else, which is
        # how defect 2 shipped past a green suite.
        print(f"\n=== console errors: {len(console)} ===")
        for message in console:
            print(f"   {message}")
        _check("no console errors", not console, "; ".join(console[:2]))
        browser.close()

    print("\n" + "=" * 62)
    if _failures:
        print(f"FAILURES ({len(_failures)}):")
        for failure in _failures:
            print(f"  - {failure}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
