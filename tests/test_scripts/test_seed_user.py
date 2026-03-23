"""Tests for the seed user script's production safety checks."""

import subprocess
import sys


class TestSeedUserProductionGuard:
    """Verify seed_user.py rejects unsafe passwords in production mode."""

    def test_default_password_rejected_in_production(self):
        """The default 'ChangeMe!2026' password must be rejected in production.

        Direct invocation of the script without overriding SEED_USER_PASSWORD
        would create an account with a publicly documented password.
        """
        result = subprocess.run(
            [sys.executable, "scripts/seed_user.py"],
            env={
                "FLASK_ENV": "production",
                "SEED_USER_PASSWORD": "ChangeMe!2026",
                "SEED_USER_EMAIL": "test@example.com",
                # Minimal env so the script reaches the password check
                # before failing on missing DB config.
                "PATH": "",
            },
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 1
        assert "ChangeMe!2026" in result.stderr

    def test_empty_password_rejected_in_production(self):
        """An empty SEED_USER_PASSWORD must be rejected in production."""
        result = subprocess.run(
            [sys.executable, "scripts/seed_user.py"],
            env={
                "FLASK_ENV": "production",
                "SEED_USER_PASSWORD": "",
                "SEED_USER_EMAIL": "test@example.com",
                "PATH": "",
            },
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 1
        assert "empty" in result.stderr.lower()

    def test_default_password_allowed_in_development(self):
        """In development mode the default password is allowed.

        The script may fail later (no DB), but it must NOT exit 1
        at the password check.  We verify the password-specific
        error message is absent from stderr.
        """
        result = subprocess.run(
            [sys.executable, "scripts/seed_user.py"],
            env={
                "FLASK_ENV": "development",
                "SEED_USER_PASSWORD": "ChangeMe!2026",
                "SEED_USER_EMAIL": "test@example.com",
                "PATH": "",
            },
            capture_output=True,
            text=True,
            timeout=10,
        )
        # It will fail for other reasons (no DB), but not because
        # of the password check.
        assert "ChangeMe!2026" not in result.stderr
