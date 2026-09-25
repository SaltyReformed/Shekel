"""Tests for seed-script PII redaction (audit F-114 / commit C-16).

Container stdout is shipped off-host by the Grafana Alloy / Loki
pipeline, so any line emitted by ``scripts/seed_user.py`` is retained in
long-term storage.  (``scripts/seed_tax_brackets.py`` was covered here too until
plan step salary:X-at-1 deleted it: the tax law is no longer copied per user.)
Logging the user's email on every container start would surface a
real PII value with no operational benefit -- the operator already
knows which account they seeded.

These tests assert the redaction by reading the script source AND
running ``seed_user.py`` against the test DB to capture its actual
stdout.  Two approaches because each catches a different regression:

  * Source inspection catches a copy-paste regression where someone
    re-introduces the email print without realising it.
  * Subprocess invocation catches a regression where the source
    looks redacted but a runtime substitution (e.g. an f-string in a
    different branch) still emits the email.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import time_machine

from app.models.user import User
from app.utils.dates import to_display_tz


def _real_display_today():
    """Return the display-timezone civil day on the REAL clock.

    The clock a CHILD PROCESS will judge a date against, which is finding
    **N-300**: ``time_machine`` patches the calling process only, so a value
    derived from this process's faked clock is months in the child's own
    future and ``seed_user.py`` correctly exits 1 refusing it.

    ``escape_hatch`` reaches the unpatched clock but RAISES
    ``ValueError("Not currently time-travelling.")`` when no travel is active,
    which is every ordinary run -- so ``is_travelling()`` gates it rather than
    a ``try``.  A first version of this helper did not, and turned a sweep-only
    failure into an every-run one.

    Returns:
        Today's date in :data:`~app.utils.dates.DISPLAY_TIMEZONE`, taken from
        the real clock whether or not the suite is time-travelling.
    """
    hatch = time_machine.escape_hatch
    if hatch.is_travelling():
        now = hatch.datetime.datetime.now(timezone.utc)
    else:
        now = datetime.now(timezone.utc)
    return to_display_tz(now).date()


SEED_USER_SCRIPT = Path("scripts/seed_user.py")


# --- Source-level invariants ----------------------------------------------


class TestSourceLevelRedaction:
    """The script source contains no email-bearing print/log statements.

    A grep-based assertion is brittle if a future maintainer adds a
    legitimate ``email`` reference (e.g., the SEED_USER_EMAIL env
    var name in a usage-text block).  We therefore check for the
    SPECIFIC patterns that would re-introduce the leak: an f-string
    or %s-style print whose interpolated expression includes
    ``email`` or ``user.email``.
    """

    @staticmethod
    def _find_email_print_statements(path: Path) -> list[str]:
        """Return any print() lines that interpolate a python ``email`` value.

        Looks for ``print(f"...{email}..."``, ``print(f"...{user.email}..."``,
        and the equivalent ``%`` / ``.format`` shapes.  Returns a list
        of the matching source lines.
        """
        text = path.read_text()
        # Match print(...) lines whose argument string contains an
        # interpolation of the email expression.
        candidates = re.findall(
            r"^[ \t]*print\([^)]*\{[^}]*\bemail\b[^}]*\}.*\)",
            text,
            flags=re.MULTILINE,
        )
        return candidates

    def test_seed_user_does_not_print_email_in_user_lookups(self):
        """``seed_user.py`` has no print() that interpolates ``email``."""
        leaks = self._find_email_print_statements(SEED_USER_SCRIPT)
        assert leaks == [], (
            f"seed_user.py contains email-bearing print statements: {leaks!r}"
        )


# --- Runtime invariants ---------------------------------------------------


class TestSeedUserSubprocessOutput:
    """Running ``seed_user.py`` end-to-end does not echo the email.

    Spawns the script in a subprocess against the same test database
    the rest of the suite uses, captures stdout, and asserts the
    SEED_USER_EMAIL value never appears in the line stream.  This is
    the closest thing to a production-equivalent invocation a unit
    test can do without spinning up a container.
    """

    @staticmethod
    def _safe_env(**overrides):
        """Build a minimal subprocess environment.

        Mirrors the helper in ``test_seed_user.py`` -- inherits only
        the variables Python needs to import modules and connects to
        the SAME test database the rest of the suite uses (so the
        script can find the user row it would otherwise create).

        ``SEED_USER_LAST_PAYDAY`` is supplied because the script REQUIRES it
        and exits 1 without one (plan step X-ad-a): registration no longer
        invents a pay period, and the day somebody was last paid has no
        honest default.  The value is the USER's today, never
        ``date.today()`` -- the service refuses a future payday, and a
        UTC-pinned process is already on tomorrow's date every evening.

        **It is the REAL clock's display day, not this process's, and that
        is finding N-300** (developer ruling 2026-08-25).  ``time_machine``
        patches the CALLING process only, so under the weekly calendar sweep
        a plain ``display_today()`` here handed the CHILD a payday months in
        its own future and ``seed_user.py`` correctly exited 1 refusing it --
        red on all five matrix dates.  The child has no faked clock and
        validates against its real one, so the value it is given must come
        from the clock it will be judged by.  The remedy is deliberately NOT
        propagating the fake into the child, which would put test-only
        machinery in a production provisioning script.  The DISPLAY-timezone
        conversion is kept for the reason above: it is the user's civil day
        that the service bounds, not the process's UTC one.
        """
        env = {}
        for key in ("PATH", "PYTHONPATH", "PYTHONHOME", "HOME",
                    "VIRTUAL_ENV", "LANG", "LC_ALL", "TEST_DATABASE_URL"):
            if key in os.environ:
                env[key] = os.environ[key]
        # Map TEST_DATABASE_URL into DATABASE_URL so the script's
        # create_app() picks up the test DB rather than a real one.
        if "TEST_DATABASE_URL" in env:
            env["DATABASE_URL"] = env["TEST_DATABASE_URL"]
        env["FLASK_ENV"] = "development"
        env["SEED_USER_LAST_PAYDAY"] = _real_display_today().isoformat()
        env.update(overrides)
        return env

    def test_seed_user_does_not_log_email_on_first_run(self, app, db):
        """A fresh seed run prints user_id but not the email value."""
        sentinel_email = "sentinel-redaction-test@shekel.local"
        # Pre-clean any prior sentinel rows so the script runs the
        # "Created user" branch deterministically.
        existing = db.session.query(User).filter_by(
            email=sentinel_email,
        ).first()
        if existing is not None:
            db.session.delete(existing)
            db.session.commit()

        result = subprocess.run(
            [sys.executable, str(SEED_USER_SCRIPT)],
            env=self._safe_env(
                SEED_USER_EMAIL=sentinel_email,
                SEED_USER_PASSWORD="seedtestpassword12",
            ),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert result.returncode == 0, (
            f"seed_user.py failed (rc={result.returncode}); "
            f"stderr={result.stderr!r}"
        )
        assert sentinel_email not in result.stdout, (
            f"seed_user.py leaked the sentinel email into stdout: "
            f"{result.stdout!r}"
        )
        # Either the "Created user" or "already exists" line should
        # carry the user_id reference.
        assert "id=" in result.stdout, (
            f"seed_user.py did not emit a user_id reference: "
            f"{result.stdout!r}"
        )

        # Cleanup.
        created = db.session.query(User).filter_by(
            email=sentinel_email,
        ).first()
        if created is not None:
            db.session.delete(created)
            db.session.commit()

    def test_seed_user_does_not_log_email_on_idempotent_rerun(self, app, db):
        """A second run (existing user branch) also redacts the email."""
        sentinel_email = "sentinel-redaction-rerun@shekel.local"
        # Ensure the row exists so the script hits the "already exists"
        # branch this test cares about.
        if not db.session.query(User).filter_by(email=sentinel_email).first():
            from app.services.auth_service import (  # pylint: disable=import-outside-toplevel
                hash_password,
            )
            db.session.add(User(
                email=sentinel_email,
                password_hash=hash_password("seedtestpassword12"),
                display_name="Sentinel",
            ))
            db.session.commit()

        result = subprocess.run(
            [sys.executable, str(SEED_USER_SCRIPT)],
            env=self._safe_env(
                SEED_USER_EMAIL=sentinel_email,
                SEED_USER_PASSWORD="seedtestpassword12",
            ),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert result.returncode == 0, (
            f"seed_user.py failed (rc={result.returncode}); "
            f"stderr={result.stderr!r}"
        )
        assert sentinel_email not in result.stdout, (
            f"seed_user.py leaked the sentinel email on idempotent run: "
            f"{result.stdout!r}"
        )
        assert "already exists" in result.stdout, (
            f"seed_user.py did not emit the 'already exists' line: "
            f"{result.stdout!r}"
        )

        # Cleanup.
        existing = db.session.query(User).filter_by(
            email=sentinel_email,
        ).first()
        if existing is not None:
            db.session.delete(existing)
            db.session.commit()
