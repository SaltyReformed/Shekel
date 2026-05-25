"""Save a logged-in Playwright session for the dev Flask server.

Run this once before invoking ``verify_mobile_grid_commit6.py`` (or any
future mobile verification script).  The script prompts for the dev
user's password via ``getpass`` -- the value is read directly from the
TTY, never echoed, never stored in shell history or this script's
output.  It drives a headless Chromium against the local dev Flask
server (``http://172.32.0.1:5000``, the shekel-frontend bridge
gateway) to perform the login, then writes Playwright's
``storage_state`` -- cookies + localStorage -- to
``tests/manual/.dev_session_state.json``.

Subsequent verification scripts launch Playwright with
``storage_state=<that file>`` and skip the login flow entirely.  The
file is gitignored (see .gitignore "Manual verification harness").

Usage::

    .venv/bin/python tests/manual/save_dev_session.py
    # Email [josh@saltyreformed.com]: <enter or override>
    # Password: <typed silently>
    # Saved session to tests/manual/.dev_session_state.json

Re-run whenever the saved cookie expires (Flask-Login's session
default is ~31 days; whichever Shekel uses) or after any logout.
"""

from __future__ import annotations

import getpass
import pathlib
import sys

from playwright.sync_api import sync_playwright


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
STATE_FILE = REPO_ROOT / "tests" / "manual" / ".dev_session_state.json"
DEV_BASE_URL = "http://172.32.0.1:5000"
DEFAULT_EMAIL = "josh@saltyreformed.com"


def main() -> int:
    """Drive headless Chromium through the login form and persist state."""
    email_prompt = f"Email [{DEFAULT_EMAIL}]: "
    email = input(email_prompt).strip() or DEFAULT_EMAIL
    password = getpass.getpass("Password: ")
    if not password:
        print("Password is empty; aborting.", file=sys.stderr)
        return 1

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.goto(f"{DEV_BASE_URL}/login", wait_until="domcontentloaded")
        page.fill('input[name="email"]', email)
        page.fill('input[name="password"]', password)
        page.click('button[type="submit"]')
        # Successful login redirects to /grid (or / -> /grid).  Wait
        # until the response settles to a non-login URL.
        try:
            page.wait_for_url(
                lambda url: "/login" not in url,
                timeout=10000,
            )
        except Exception as e:  # pylint: disable=broad-except
            # Re-read the page to see what came back.  Capture the
            # body so the operator can diagnose (often an MFA prompt
            # or a validation error).
            print(
                "Login did not redirect away from /login.  "
                "Inspect the response below:",
                file=sys.stderr,
            )
            print(page.content()[:2000], file=sys.stderr)
            browser.close()
            raise

        # Hit /grid once so any post-login redirect chain settles
        # and the storage_state captures everything the verification
        # script will need.
        page.goto(f"{DEV_BASE_URL}/grid", wait_until="domcontentloaded")
        assert page.url.endswith("/grid"), (
            f"Expected to land on /grid, got {page.url}"
        )

        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(STATE_FILE))
        browser.close()

    print(f"Saved session to {STATE_FILE}")
    print(
        "Next: .venv/bin/python tests/manual/verify_mobile_grid_commit6.py",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
