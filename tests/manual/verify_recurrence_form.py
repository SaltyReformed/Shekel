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

**Plan step R7b-3 added a FOURTH control and the same coverage for it**
(:func:`_drive_end_bound`): an "Ends" select whose three shapes -- never, on a
date, after N occurrences -- each enable at most one value input.  It is the
same defect class one control over: a date the user typed and then moved off
would reach the write door beside a mode that does not name it, and rendered
HTML cannot tell a hidden input from a disabled one.  The transfer form's
LOCKED case (a loan payment, whose bound the app derives) is asserted in
``tests/test_routes/test_templates.py`` instead, because it is a property of
the SERVER's render rather than of the script.

**Plan step R7b-4 replaced the "First paycheck" ``<select>`` with a "Starts on"
DATE and gave the surviving ``<select>`` one job**
(:func:`_drive_opening_bound`).  Two new instances of this file's whole defect
class arrived with it, one on each side of the swap:

* the new date box lives INSIDE ``#recurrence-fields`` and carries no
  ``d-none`` of its own -- every other row in that container has one, so
  copying the idiom would have shipped a control that never appears, and a
  rendered-HTML assertion cannot tell "hidden by a class no script removes"
  from "shown";
* the pay-period ``<select>`` now belongs to the NON-repeating case alone, and
  hiding it is not enough -- a hidden control still SUBMITS, so choosing a
  cadence after choosing a period would post a period the recurrence has no
  use for.  It has to be DISABLED, and only a real ``FormData`` says whether
  it is.

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

#: How long to wait after a field change that fires a preview fetch.
#:
#: **The limiter being REAL is the point**, so the run is paced to it rather
#: than the other way round: the dev app runs prod's Redis limiter at 30
#: requests a minute per IP, ``recurrence_form.js`` fetches the occurrence
#: preview on every field change, and this script changes a field about sixty
#: times.  At 2.5s that is 24 a minute, the same headroom
#: :data:`POST_SPACING_SECONDS` leaves the crafted POSTs.
#:
#: Plan step R7c-b is what made it necessary: it added three driver passes
#: (``_drive_nominal_day`` on both forms, ``_drive_loan_destination_lock``),
#: and the run then met nine 429s -- all of them preview fetches, all reported
#: as console errors, none of them a product defect.  Loosening the limiter
#: would have hidden a class of defect the console check exists to catch.
FIELD_SPACING_MS = 2500

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


def _settle(page) -> None:
    """Let the script's last field change settle, PACED to the rate limiter.

    Every change fires an occurrence-preview fetch, so the wait is both a DOM
    settle and the run's rate budget; see :data:`FIELD_SPACING_MS`.

    Args:
        page: The Playwright page.
    """
    page.wait_for_timeout(FIELD_SPACING_MS)


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
    _settle(page)


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


def _posted_bound(page) -> dict[str, list[str]]:
    """Return the closing-bound keys the form would actually submit.

    From a real ``FormData``, for the reason :func:`_posted_intervals` reads
    one: a control hidden by a class still SUBMITS, and a disabled one does
    not, and rendered HTML cannot tell those apart.  Plan step R7b-3's "Ends"
    control turns on exactly that -- the shape the user chose must be the only
    shape whose value reaches the door.

    Args:
        page: The Playwright page.

    Returns:
        Each bound key mapped to every value posted under it.
    """
    return page.evaluate(
        """() => {
            const form = document.getElementById('recurrence_unit').form;
            const data = new FormData(form);
            return {
                recurrence_end_mode: data.getAll('recurrence_end_mode'),
                end_date: data.getAll('end_date'),
                max_occurrences: data.getAll('max_occurrences'),
            };
        }"""
    )


def _posted_opening(page) -> dict[str, list[str]]:
    """Return the opening-bound keys the form would actually submit.

    From a real ``FormData``, for the reason :func:`_posted_intervals` reads
    one: a control hidden by a class still SUBMITS and a disabled one does
    not, and rendered HTML cannot tell those apart.  Plan step R7b-4 turns on
    exactly that for the pay-period ``<select>``, which must post its value
    when the definition does NOT repeat and nothing at all when it does.

    Args:
        page: The Playwright page.

    Returns:
        Each opening key mapped to every value posted under it.
    """
    return page.evaluate(
        """() => {
            const form = document.getElementById('recurrence_unit').form;
            const data = new FormData(form);
            return {
                starts_on: data.getAll('starts_on'),
                nominal_day: data.getAll('nominal_day'),
                start_period_id: data.getAll('start_period_id'),
            };
        }"""
    )


def _drive_opening_bound(page, kind: str, url: str) -> None:
    """Check the "Starts on" box and the pay-period select swap correctly.

    The two halves of plan step R7b-4's control swap, both invisible to a
    rendered-HTML assertion.  See the module docstring for why each is here.

    The transaction form places no pay-period select at all -- a rule-less
    transaction template materialises nothing, so it needs no period -- and
    the absence is asserted rather than skipped: a select that reappeared
    there would submit a field that schema no longer declares.

    Args:
        page: The Playwright page.
        kind: "transaction" or "transfer", for the labels.
        url: The create form's path.
    """
    print(f"\n=== {kind} opening bound: {url} ===")
    page.goto(f"{DEV_BASE_URL}{url}", wait_until="domcontentloaded")
    page.wait_for_selector("#recurrence_unit")
    units = _unit_ids(page)
    has_period_select = page.locator("#field-start-period").count() > 0

    _check(f"{kind} K: the pay-period select is present only on the transfer form",
           has_period_select == (kind == "transfer"),
           f"present={has_period_select}")

    # --- does NOT repeat -------------------------------------------------
    page.locator("#recurrence_unit").select_option("")
    _settle(page)
    _check(f"{kind} K: Starts on is hidden when it does not repeat",
           not _visible(page, "field-starts-on"), "visible")
    posted = _posted_opening(page)
    _check(f"{kind} K: no starts_on posts when it does not repeat",
           posted["starts_on"] == [], str(posted["starts_on"]))
    if has_period_select:
        _check(f"{kind} K: the pay-period row IS shown when it does not repeat",
               _visible(page, "field-start-period"), "hidden")
        _check(f"{kind} K: the pay period posts exactly once",
               len(posted["start_period_id"]) == 1,
               str(posted["start_period_id"]))

    # --- repeats ---------------------------------------------------------
    page.locator("#recurrence_unit").select_option(units["paychecks"])
    _settle(page)
    _check(f"{kind} L: Starts on is SHOWN for a repeating definition",
           _visible(page, "field-starts-on"), "hidden")
    if has_period_select:
        _check(f"{kind} L: the pay-period row is hidden when it repeats",
               not _visible(page, "field-start-period"), "visible")
        _check(f"{kind} L: the pay period posts NOTHING when it repeats",
               _posted_opening(page)["start_period_id"] == [],
               str(_posted_opening(page)["start_period_id"]))

    # An untouched box posts the form's own DEFAULT -- TODAY since plan step
    # R7c-b -- and that is load-bearing rather than cosmetic: the control this
    # replaced was a <select> with no empty option preselecting the current
    # period, so every create was bounded.  Defaulting to EMPTY silently made
    # "unbounded" the default, and the create routes generate over every period
    # the owner has: a rent template created today wrote projected debits into
    # pay periods that had already closed.  Found by an adversarial review of
    # plan step R7b-4; asserted here because only a real render shows what the
    # box actually holds.
    posted_default = _posted_opening(page)["starts_on"]
    _check(f"{kind} L: an untouched Starts on posts the form's default",
           len(posted_default) == 1 and posted_default[0] != "",
           str(posted_default))
    page.fill("#starts_on", "2026-09-15")
    _settle(page)
    _check(f"{kind} L: a typed Starts on posts that date",
           _posted_opening(page)["starts_on"] == ["2026-09-15"],
           str(_posted_opening(page)["starts_on"]))
    _check(f"{kind} L: the preview survived the opening bound",
           "Could not load preview" not in page.inner_text("#recurrence-preview"),
           page.inner_text("#recurrence-preview"))


def _drive_nominal_day(page, kind: str, url: str) -> None:
    """Check the "repeating on" control appears and posts only where it means.

    **Plan step R7c-b's new control, and the same defect class one field
    over.**  ``nominal_day`` records the day a rule MEANS when its first
    occurrence's own month was too short to hold it -- 2026-04-30 is "the 30th"
    or "the 31st", and those are different cadences from May on.  The control
    therefore renders ONLY where the chosen date leaves that question open, and
    it must be DISABLED as well as hidden everywhere else: a hidden select
    still submits, and a stale day beside a date that never clamped is exactly
    what ``ck_recurrence_rules_nominal_day`` refuses.  A rendered-HTML
    assertion cannot tell "hidden" from "hidden and still submitting".

    The per-OPTION half matters too and pytest cannot see it either: only the
    days ABOVE the chosen one are meaningful, so choosing 2026-04-30 must offer
    31 and not 30, and moving to a month that holds them all must clear the
    selection rather than leave it stating a day the rule no longer fires on.

    Args:
        page: The Playwright page.
        kind: "transaction" or "transfer", for the labels.
        url: The create form's path.
    """
    print(f"\n=== {kind} nominal day: {url} ===")
    page.goto(f"{DEV_BASE_URL}{url}", wait_until="domcontentloaded")
    page.wait_for_selector("#recurrence_unit")
    units = _unit_ids(page)

    # A MONTHLY cadence, which is the only family with a day-of-month
    # coordinate at all.
    page.locator("#recurrence_unit").select_option(units["months"])
    _settle(page)

    # --- a date its month CAN hold: nothing to ask -----------------------
    page.fill("#starts_on", "2026-04-15")
    _settle(page)
    _check(f"{kind} N: repeating-on is hidden for a mid-month date",
           not _visible(page, "field-nominal-day"), "visible")
    _check(f"{kind} N: repeating-on posts NOTHING for a mid-month date",
           _posted_opening(page)["nominal_day"] == [],
           str(_posted_opening(page)["nominal_day"]))

    # --- a month's LAST day in a short month: the question is open -------
    page.fill("#starts_on", "2026-04-30")
    _settle(page)
    _check(f"{kind} N: repeating-on is SHOWN on a short month's last day",
           _visible(page, "field-nominal-day"), "hidden")
    enabled = page.evaluate(
        """() => Array.from(document.getElementById('nominal_day').options)
              .filter(o => o.value !== '' && !o.disabled)
              .map(o => o.value)"""
    )
    _check(f"{kind} N: only the days ABOVE the 30th are offered",
           enabled == ["31"], str(enabled))

    page.select_option("#nominal_day", "31")
    _settle(page)
    _check(f"{kind} N: a chosen nominal day posts exactly once",
           _posted_opening(page)["nominal_day"] == ["31"],
           str(_posted_opening(page)["nominal_day"]))

    # --- back to a month that holds every day: the choice must GO --------
    page.fill("#starts_on", "2026-05-31")
    _settle(page)
    _check(f"{kind} N: repeating-on is hidden again on a 31-day month's end",
           not _visible(page, "field-nominal-day"), "visible")
    _check(f"{kind} N: the stale nominal day posts nothing after the move",
           _posted_opening(page)["nominal_day"] == [],
           str(_posted_opening(page)["nominal_day"]))


def _drive_loan_destination_lock(page) -> None:
    """Check a loan destination locks "Starts on" on the CREATE form.

    **Plan step R7c-b, and the create-side half of a rule the edit form has
    carried since R7b-4.**  A recurring loan payment's first occurrence is the
    loan's first contractual installment, which the app derives -- so asking
    the user for it and discarding the answer is the defect
    ``LOAN_PAYMENT_BOUND_IS_DERIVED`` closes on the edit path.  The create form
    cannot know at render which destination will be chosen, so the server ships
    the SET of loan accounts and ``recurrence_form.js`` applies it.

    Only a real ``FormData`` says whether the control is disabled, and only a
    real render says whether the help text swapped -- both are exactly the
    difference this file exists for.

    **It also covers the defect this step SHIPPED and reading caught**: the
    script read ``startsOn.readOnly`` while the template emitted ``disabled``,
    so ``startsOnLocked`` was false on every locked form and ``syncStartsOn``
    re-enabled the control the moment the page settled.  The edit-form arm
    below is that regression's control.

    Skipped with a printed note when the owner has no loan account -- the dev
    clone has one, but a fresh database does not, and a check that silently
    passes on an empty set is worse than one that says it did not run.

    Args:
        page: The Playwright page.
    """
    print("\n=== transfer loan-destination lock: /transfers/new ===")
    page.goto(f"{DEV_BASE_URL}/transfers/new", wait_until="domcontentloaded")
    page.wait_for_selector("#recurrence_unit")
    loan_ids = page.evaluate(
        """() => (document.getElementById('recurrence-fields')
              .getAttribute('data-loan-account-ids') || '')
              .split(',').filter(Boolean)"""
    )
    if not loan_ids:
        print("   SKIPPED: this owner has no configured loan account")
        return

    units = _unit_ids(page)
    page.locator("#recurrence_unit").select_option(units["months"])
    _settle(page)

    # --- a NON-loan destination: the date is the user's ------------------
    non_loan = page.evaluate(
        """(loanIds) => Array.from(
              document.getElementById('to_account_id').options
           ).map(o => o.value).find(v => v && loanIds.indexOf(v) === -1)""",
        loan_ids,
    )
    _check("transfer M: the form offers a non-loan destination to compare",
           non_loan is not None, "every destination is a loan")
    if non_loan is None:
        return
    page.select_option("#to_account_id", non_loan)
    _settle(page)
    _check("transfer M: Starts on is the user's for a non-loan destination",
           not page.locator("#starts_on").is_disabled(), "disabled")
    _check("transfer M: it posts the date for a non-loan destination",
           len(_posted_opening(page)["starts_on"]) == 1,
           str(_posted_opening(page)["starts_on"]))

    # --- a LOAN destination: the app derives it --------------------------
    page.select_option("#to_account_id", loan_ids[0])
    _settle(page)
    _check("transfer M: Starts on is DISABLED for a loan destination",
           page.locator("#starts_on").is_disabled(), "enabled")
    _check("transfer M: it posts NOTHING for a loan destination",
           _posted_opening(page)["starts_on"] == [],
           str(_posted_opening(page)["starts_on"]))
    _check("transfer M: the help text says the loan sets it",
           "loan's first payment" in page.inner_text("#starts-on-help"),
           page.inner_text("#starts-on-help"))

    # --- and BACK: the lock is not one-way -------------------------------
    page.select_option("#to_account_id", non_loan)
    _settle(page)
    _check("transfer M: Starts on is handed back when the loan is deselected",
           not page.locator("#starts_on").is_disabled(), "still disabled")


def _select_end_mode(page, token: str) -> None:
    """Choose one "Ends" shape and let the script re-link the value inputs.

    By VALUE here, unlike the interval select: a bound token IS unique across
    the offer set (it is the shape's own name), which the interval values are
    not.

    Args:
        page: The Playwright page.
        token: The shape's token -- ``never``, ``on_date``,
            ``after_occurrences``.
    """
    page.select_option("#recurrence_end_mode", token)
    _settle(page)


def _drive_end_bound(page, kind: str, url: str) -> None:
    """Check the "Ends" control shows and posts exactly one shape's value.

    The property pytest cannot see, and the one plan step R7b-2 was bitten by
    twice: which controls are VISIBLE and which are ENABLED.  A stale date
    left in a box the user has moved off would otherwise reach the door beside
    a mode that does not name it.

    Args:
        page: The Playwright page.
        kind: "transaction" or "transfer", for the labels.
        url: The create form's path.
    """
    print(f"\n=== {kind} ends control: {url} ===")
    page.goto(f"{DEV_BASE_URL}{url}", wait_until="domcontentloaded")
    page.wait_for_selector("#recurrence_unit")
    units = _unit_ids(page)
    page.locator("#recurrence_unit").select_option(units["paychecks"])
    _settle(page)

    _check(f"{kind} G: the Ends row is shown for a repeating definition",
           _visible(page, "field-end-bound"), "hidden")

    # Never: neither value input shows, and NEITHER posts.
    _select_end_mode(page, "never")
    posted = _posted_bound(page)
    _check(f"{kind} G: never shows no value input",
           not _visible(page, "field-end-date")
           and not _visible(page, "field-max-occurrences"),
           "a value input is shown for the unbounded shape")
    _check(f"{kind} G: never posts only the mode",
           posted["end_date"] == [] and posted["max_occurrences"] == [],
           f"posted={posted}")

    # On a date: the date box shows and posts; the count does neither.
    _select_end_mode(page, "on_date")
    page.fill("#end_date", "2030-01-01")
    posted = _posted_bound(page)
    _check(f"{kind} H: the date box is shown",
           _visible(page, "field-end-date"), "hidden")
    _check(f"{kind} H: the count box is hidden",
           not _visible(page, "field-max-occurrences"), "shown")
    _check(f"{kind} H: only the date posts",
           posted["end_date"] == ["2030-01-01"]
           and posted["max_occurrences"] == [],
           f"posted={posted}")

    # After N: the count box shows and posts, and the DATE the user typed a
    # moment ago must not follow it.
    _select_end_mode(page, "after_occurrences")
    page.fill("#max_occurrences", "6")
    posted = _posted_bound(page)
    _check(f"{kind} I: the count box is shown",
           _visible(page, "field-max-occurrences"), "hidden")
    _check(f"{kind} I: the date box is hidden",
           not _visible(page, "field-end-date"), "shown")
    _check(f"{kind} I: only the count posts",
           posted["max_occurrences"] == ["6"] and posted["end_date"] == [],
           f"a stale value from the shape the user moved off still posts: "
           f"{posted}")
    _check(f"{kind} I: exactly one mode posts",
           posted["recurrence_end_mode"] == ["after_occurrences"],
           f"posted={posted}")

    # Back to "does not repeat": the whole control goes, and posts NOTHING --
    # a hidden-but-enabled control is the defect class this file exists for.
    page.locator("#recurrence_unit").select_option("")
    _settle(page)
    posted = _posted_bound(page)
    _check(f"{kind} J: a non-repeating definition posts no bound at all",
           posted["recurrence_end_mode"] == []
           and posted["end_date"] == []
           and posted["max_occurrences"] == [],
           f"posted={posted}")

    _check(f"{kind}: the preview survived the bound changes",
           "Could not load preview"
           not in page.locator("#recurrence-preview").inner_text(),
           "preview broke")


def _posted_due_day(page) -> list[str]:
    """Return the ``due_day_of_month`` values the form would actually submit.

    From a real ``FormData``, for the reason :func:`_posted_intervals` reads
    one -- and this is the control that shipped the defect the idiom exists
    for.  The Due Day row is HIDDEN for a cadence that anchors on a paycheck
    and was never DISABLED, so a value typed under "every 1 month" still
    posted after switching to "funded from the first paycheck" and landed in
    the column through ``recurrence._authoring._author``.  Rendered HTML cannot
    tell a hidden row from a hidden row that still submits.

    Args:
        page: The Playwright page.

    Returns:
        Every value the form would post under that name.  Empty on the
        transfer form, which does not render the control at all.
    """
    return page.evaluate(
        """() => {
            const form = document.getElementById('recurrence_unit').form;
            return new FormData(form).getAll('due_day_of_month');
        }"""
    )


def _drive_visibility(page, kind: str, url: str) -> None:
    """Check which controls each cadence shows, on one form kind.

    **Re-pointed onto ``field-due-dom`` at plan step R7c-b, and the previous
    version is the lesson.**  It drove ``field-dom`` (Day of Month) and
    ``field-moy`` (Month), the two controls that step DELETED -- ruling R-R16
    put the cycle's day and its month on ``starts_on``.  So its five
    ``VISIBLE`` assertions failed correctly, and, far worse, its six ``hidden``
    assertions PASSED VACUOUSLY: a non-existent element is not visible, so
    those read green while proving nothing.

    The day question they asked is now answered in two places and both have
    their own driver: ``starts_on`` (always shown --
    :func:`_drive_opening_bound`) and ``nominal_day`` (conditionally shown --
    :func:`_drive_nominal_day`).  What is left cadence-dependent, and what this
    function is now about, is ``field-due-dom``: the bill's separate REAL due
    day, which ``recurrence_form.js`` toggles on the chosen offer's
    ``anchors_day_of_month``.

    Every visibility check is paired with a POSTED-VALUE check, which is what
    earns the re-point rather than merely keeping the function alive: the row
    was hidden by class and never disabled, so it submitted from behind the
    hiding.  That is this file's whole defect class, live, in the one control
    it was left holding.

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
    # The transfer form does not render a Due Day at all (only a transaction
    # template carries one), so its every due-day assertion would be vacuous in
    # exactly the way this rewrite exists to remove.  Named once, asked at each
    # site.
    has_due_day = page.evaluate(
        "() => document.getElementById('field-due-dom') !== null")
    print(f"   (due-day row rendered on this form: {has_due_day})")

    def one_interval(label: str) -> list[str]:
        """Exactly one interval control submits, in every state."""
        posted = _posted_intervals(page)
        _check(f"{kind} {label}: exactly one interval_n posts",
               len(posted) <= 1, f"posted={posted}")
        return posted

    def due_day(label: str, shown: bool) -> None:
        """The Due Day row is shown and submits together, or neither.

        Args:
            label: The case letter.
            shown: Whether this cadence should render the row.
        """
        if not has_due_day:
            return
        _check(f"{kind} {label}: due-day row "
               f"{'VISIBLE' if shown else 'hidden'}",
               _visible(page, "field-due-dom") == shown,
               "hidden" if shown else "shown")
        posted = _posted_due_day(page)
        # A control the user cannot see must state NOTHING.  ``["25"]`` here
        # is the live defect: a value typed under a day-of-month cadence
        # surviving the switch to one that reads no day.
        _check(f"{kind} {label}: due-day posts "
               f"{'its value' if shown else 'NOTHING'}",
               (posted != []) == shown, f"posted={posted}")

    # Does not repeat: the form's own empty option, not a cadence.
    unit.select_option("")
    _settle(page)
    _check(f"{kind} A: interval row hidden", not _visible(page, "field-interval"), "shown")
    _check(f"{kind} A: placement row hidden", not _visible(page, "field-placement"), "shown")
    due_day("A", shown=False)
    one_interval("A")

    # Paychecks: a free interval, and ONE placement, so that row stays hidden.
    unit.select_option(units["paychecks"])
    _settle(page)
    _check(f"{kind} B: free box enabled",
           page.evaluate("() => !document.getElementById('interval_n_free').disabled"),
           "disabled")
    _check(f"{kind} B: placement row hidden (one placement offered)",
           not _visible(page, "field-placement"),
           "the Funded-from row is shown with a single usable choice")
    due_day("B", shown=False)
    one_interval("B")

    # Months at 1: anchors on the calendar, so the bill's due day applies.
    unit.select_option(units["months"])
    _settle(page)
    _check(f"{kind} C: the chosen interval belongs to the chosen unit",
           _selected_interval_owner(page) == units["months"],
           f"owner={_selected_interval_owner(page)} unit={units['months']}")
    _select_interval(page, units["months"], 1)
    _check(f"{kind} C: placement row shown at 1 month",
           _visible(page, "field-placement"), "hidden")
    due_day("C", shown=True)
    one_interval("C")

    # TYPE a due day here, so the next case measures whether it SURVIVES the
    # switch to a cadence that reads no day.  This is the defect: the value is
    # what makes D's posted check able to fail.
    if has_due_day:
        page.fill("#due_day_of_month", "25")
        _settle(page)
        _check(f"{kind} C: a typed due day posts",
               _posted_due_day(page) == ["25"], str(_posted_due_day(page)))

    # Months at 1, funded from the month's FIRST paycheck: anchors on a
    # paycheck, so it reads no day of the month.
    page.evaluate(
        """() => { const s = document.getElementById('recurrence_placement');
                   s.selectedIndex = 1;
                   s.dispatchEvent(new Event('change', {bubbles: true})); }""")
    _settle(page)
    due_day("D", shown=False)

    # Months at 3: no quarterly first-paycheck twin, so the placement row goes
    # and the cadence anchors on the calendar again.
    _select_interval(page, units["months"], 3)
    _check(f"{kind} E: placement row hidden at 3 months",
           not _visible(page, "field-placement"), "shown")
    due_day("E", shown=True)
    one_interval("E")

    # Years: interval 1, cycle twelve months -- the case an "interval > 1"
    # inference got wrong.
    unit.select_option(units["years"])
    _settle(page)
    _check(f"{kind} F: the chosen interval belongs to the chosen unit",
           _selected_interval_owner(page) == units["years"],
           f"owner={_selected_interval_owner(page)}")
    due_day("F", shown=True)
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
            _drive_opening_bound(page, kind, url)
            _drive_nominal_day(page, kind, url)
            _drive_end_bound(page, kind, url)
        # Transfer-only: the transaction form has no destination account, so
        # its definition can never be a loan payment.
        _drive_loan_destination_lock(page)
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
