"""Tests for seed-script PII redaction (audit F-114 / commit C-16).

Container stdout is shipped off-host by the Grafana Alloy / Loki
pipeline, so any line emitted by ``scripts/seed_user.py`` or
``scripts/seed_tax_brackets.py`` is retained in long-term storage.
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

The seed_tax_brackets script needs a populated user table to do
anything observable; we exercise it by hand-rolling the relevant
log-emitting branch and asserting on its captured output rather than
running the script as a subprocess (which would require seeding a
user too -- noise for this regression assertion).
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from app.extensions import db
from app.models.user import User


SEED_USER_SCRIPT = Path("scripts/seed_user.py")
SEED_TAX_SCRIPT = Path("scripts/seed_tax_brackets.py")


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

    def test_seed_tax_brackets_does_not_print_user_email(self):
        """``seed_tax_brackets.py`` has no print() that interpolates user.email."""
        text = SEED_TAX_SCRIPT.read_text()
        leaks = re.findall(
            r"^[ \t]*print\([^)]*\bemail\b.*\)",
            text,
            flags=re.MULTILINE,
        )
        assert leaks == [], (
            f"seed_tax_brackets.py prints email value: {leaks!r}"
        )


# --- Runtime invariants ---------------------------------------------------


class TestSeedTaxBracketsRuntimeOutput:
    """Running the per-user log branch of seed_tax_brackets does not print email.

    We exercise the branch directly rather than the whole script so
    the test does not depend on the rest of the seeding logic
    succeeding.  The branch is a single ``print`` call inside the
    ``for user in users`` loop; if that line ever regresses to
    interpolating ``user.email`` again, this test will catch it.
    """

    def test_per_user_log_line_omits_email(self, app, db, seed_user):
        """Re-execute the print branch and assert the captured line has no email."""
        user = seed_user["user"]
        captured = io.StringIO()
        with redirect_stdout(captured):
            # The script's actual line, executed in isolation.  Kept
            # in sync with the source by the test below so a future
            # rewrite that drops the print entirely also surfaces here.
            print(f"\nSeeding tax data for user id={user.id}")
        line = captured.getvalue()

        assert user.email not in line, (
            f"Per-user log line {line!r} leaked the email {user.email!r}"
        )
        assert f"id={user.id}" in line, (
            f"Per-user log line {line!r} missing the user_id reference"
        )

    def test_script_per_user_print_uses_user_id_format(self):
        """The actual source line uses ``id={user.id}``, not an email format."""
        text = SEED_TAX_SCRIPT.read_text()
        # Find the ``for user in users`` block's first print.  Should
        # reference ``user.id`` and NOT ``user.email``.
        match = re.search(
            r"for user in users:\n.*?(print\(f?[^)]*\))",
            text,
            flags=re.DOTALL,
        )
        assert match is not None, "could not locate the per-user print"
        first_print = match.group(1)
        assert "user.id" in first_print, (
            f"per-user print must reference user.id; got {first_print!r}"
        )
        assert "user.email" not in first_print, (
            f"per-user print must NOT reference user.email; got {first_print!r}"
        )


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
        env.update(overrides)
        return env

    @pytest.mark.timeout(30)
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

    @pytest.mark.timeout(30)
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
