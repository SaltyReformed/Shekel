"""Drive the balance acknowledgement in a real browser and grade what it does.

**This probe exists because the pytest suite structurally cannot see the defect
it grades.**  The acknowledgement is a toast delivered by an out-of-band htmx
swap and made visible by a Bootstrap call in ``app/static/js/app.js``; a route
test can assert the FRAGMENT (that it targets the mount, that it carries
``data-toast-auto-show``) and nothing more.  Whether the browser then shows it
is JavaScript, and the whole suite passes with the toast permanently invisible
-- which is finding **N-199**'s exact symptom, and which plan step X-f2-b
reproduced a second time: changing the swap to ``beforeend:`` moved the toast
outside the settled element the auto-show handler was scoped to, and 8,556
green tests said nothing.

It is in the repository for the reason ``verify_anchor_surfaces.py`` is: every
anchor-touching step re-writes this probe in a scratchpad and the next one
skips it.

**What it grades**, all against a running app:

1. the acknowledgement fires for a write that moves no figure (**N-204**) --
   re-record the balance that already governs, for a later day;
2. the toast is actually SHOWN (``.show`` present, a Bootstrap instance
   attached) rather than sitting in the DOM at ``display: none``;
3. a second save inside the autohide window STACKS rather than destroying the
   first (**N-206**);
4. each toast disposes and removes itself once hidden, so the mount does not
   accumulate detached nodes with armed timers (**N-206**'s other half);
5. the copy distinguishes a write that recorded a row from ruling R-EQ's
   idempotent re-assert, which records nothing.

**Usage.**  Point a dev server at a database you may write to (a clone, never
production -- this probe RECORDS BALANCES), then::

    .venv/bin/python tests/manual/verify_anchor_ack.py \\
        --base-url http://127.0.0.1:5011 \\
        --email you@example.com --password ... --account-id 1

Every check prints PASS or FAIL and the script exits non-zero if any failed, so
it can be read at a glance or run in a pre-ship loop.
"""

from __future__ import annotations

import argparse
import sys

from playwright.sync_api import sync_playwright

#: The mount every anchor acknowledgement lands in (``base.html``).  Spelled
#: once here because three checks below read it.
MOUNT = "#anchor-ack-mount"

#: How long the toast's own ``data-bs-delay`` gives it, plus room for the fade
#: transition, in milliseconds.  Read from the element rather than assumed.
_AUTOHIDE_SLACK_MS = 2500


def _parse_args(argv):
    """Return the parsed command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5011")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument(
        "--account-id", type=int, required=True,
        help="A PLAIN cash account whose detail page carries the hero editor.",
    )
    parser.add_argument(
        "--balance", default=None,
        help="Balance to assert.  Defaults to the account's GOVERNING "
             "assertion, read off the Balance history card, which is what "
             "makes this finding N-204's shape rather than an ordinary write.",
    )
    return parser.parse_args(argv)


def _governing_balance(page, account_id):
    """Return the balance the account's newest assertion declared.

    Read from the Balance history card's first row, NOT from the hero's
    ``data-current-balance`` -- and the difference is the whole point of the
    probe.  The hero displays the resolver's CURRENT-PERIOD balance (on the
    real Checking account, ``$2,077.02`` against a governing assertion of
    ``$2,422.94``), so asserting it would MOVE the governing figure and
    correctly raise no acknowledgement at all.  A first version of this probe
    did exactly that and reported two false failures.

    Args:
        page: The Playwright page, already on the account's detail page.
        account_id: The account whose card to read.

    Returns:
        The balance as a plain digit string the form will accept.
    """
    cell = page.locator(
        f"#balance-history-{account_id} tbody tr td:nth-child(2)",
    ).first.inner_text()
    return cell.strip().lstrip("$").replace(",", "")


class _Report:
    """Collect PASS / FAIL lines and remember whether anything failed."""

    def __init__(self):
        """Start with nothing graded and nothing failed."""
        self.failed = False

    def check(self, ok, label, detail=""):
        """Record one graded expectation."""
        self.failed = self.failed or not ok
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {label}{f'  -- {detail}' if detail else ''}")


def _login(page, args):
    """Sign in, failing loudly rather than proceeding unauthenticated."""
    page.goto(f"{args.base_url}/login")
    page.fill("#email", args.email)
    page.fill("#password", args.password)
    page.click("button[type=submit]")
    page.wait_for_load_state("networkidle")
    if page.url.rstrip("/").endswith("/login"):
        raise SystemExit(
            "login did not take: check the credentials, and note that a user "
            "with MFA enabled cannot be driven by this probe."
        )


def _record_balance(page, balance):
    """Open the cash hero's editor, submit *balance*, wait for the swap."""
    page.click("#cash-balance-hero")
    page.wait_for_selector('input[name="anchor_balance"]')
    page.fill('input[name="anchor_balance"]', balance)
    page.click('form button[type=submit][aria-label="Save balance"]')
    page.wait_for_timeout(1200)


def main(argv=None):
    """Run every check and return a process exit code."""
    args = _parse_args(argv or sys.argv[1:])
    report = _Report()
    errors = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda exc: errors.append(str(exc)))

        _login(page, args)
        detail = f"{args.base_url}/accounts/{args.account_id}/details"
        page.goto(detail)
        page.wait_for_load_state("networkidle")

        governing = args.balance or _governing_balance(page, args.account_id)
        print(f"\nrecording {governing} on account {args.account_id}\n")

        # 1 + 2: a write that moves no figure is acknowledged, VISIBLY.
        _record_balance(page, governing)
        toasts = page.locator(f"{MOUNT} .toast")
        report.check(toasts.count() == 1, "a no-change write is acknowledged",
                     f"{toasts.count()} toast(s)")
        if toasts.count():
            classes = page.evaluate(
                f"document.querySelector('{MOUNT} .toast').className",
            )
            report.check(
                "show" in classes.split(),
                "the toast is SHOWN, not merely in the DOM", classes,
            )
            report.check(
                page.evaluate(
                    "!!bootstrap.Toast.getInstance("
                    f"document.querySelector('{MOUNT} .toast'))",
                ),
                "Bootstrap holds an instance for it",
            )
            text = toasts.first.inner_text()
            report.check(
                "Balance recorded" in text or "Balance confirmed" in text,
                "the copy names what happened", text.split("\n")[0].strip(),
            )

        # 3: a second save STACKS rather than destroying the first.
        _record_balance(page, governing)
        report.check(
            page.locator(f"{MOUNT} .toast").count() == 2,
            "a second save stacks beside the first",
            f"{page.locator(f'{MOUNT} .toast').count()} toast(s)",
        )
        # The second is ruling R-EQ's idempotent re-assert: same balance, same
        # day, nothing written -- so it must not claim to have recorded one.
        if page.locator(f"{MOUNT} .toast").count() == 2:
            second = page.locator(f"{MOUNT} .toast").nth(1).inner_text()
            report.check(
                "Balance confirmed" in second,
                "an idempotent re-assert does not claim to have recorded",
                second.split("\n")[0].strip(),
            )

        # 4: both dispose and remove themselves once hidden.
        delay = int(page.get_attribute(f"{MOUNT} .toast", "data-bs-delay") or 0)
        page.wait_for_timeout(delay + _AUTOHIDE_SLACK_MS)
        report.check(
            page.evaluate(
                f"document.querySelector('{MOUNT}').children.length",
            ) == 0,
            "the mount empties itself after the autohide",
        )

        report.check(not errors, "no page errors", "; ".join(errors))
        browser.close()

    print("\nFAILED\n" if report.failed else "\nOK\n")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
