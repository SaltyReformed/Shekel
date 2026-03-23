"""Tests for the seed user script's production safety checks.

Uses subprocess to test _check_production_password() in isolation.
The subprocess environment is carefully controlled:
  - Inherits PATH and Python-related vars (so imports work)
  - Sets FLASK_ENV and SEED_USER_PASSWORD explicitly
  - Does NOT inherit DATABASE_URL (prevents connecting to real DBs)

For the production cases, the script exits at the password check
before reaching create_app().  For the development case, the script
proceeds past the check and fails at create_app() (no DATABASE_URL),
but the test only verifies the password error is absent.
"""

import os
import subprocess
import sys


def _safe_env(**overrides):
    """Build a minimal subprocess environment for seed_user.py.

    Inherits only what Python needs to run (PATH, PYTHONPATH, etc.)
    but explicitly excludes DATABASE_URL and TEST_DATABASE_URL to
    prevent the subprocess from connecting to any real database.
    """
    env = {}
    # Inherit only the vars Python needs to find modules and run.
    for key in ("PATH", "PYTHONPATH", "PYTHONHOME", "HOME",
                "VIRTUAL_ENV", "LANG", "LC_ALL"):
        if key in os.environ:
            env[key] = os.environ[key]
    env.update(overrides)
    return env


class TestSeedUserProductionGuard:
    """Verify seed_user.py rejects unsafe passwords in production mode."""

    def test_default_password_rejected_in_production(self):
        """The default 'ChangeMe!2026' password must be rejected in production."""
        result = subprocess.run(
            [sys.executable, "scripts/seed_user.py"],
            env=_safe_env(
                FLASK_ENV="production",
                SEED_USER_PASSWORD="ChangeMe!2026",
            ),
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
            env=_safe_env(
                FLASK_ENV="production",
                SEED_USER_PASSWORD="",
            ),
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 1
        assert "empty" in result.stderr.lower()

    def test_default_password_allowed_in_development(self):
        """In development mode the default password is allowed.

        The script proceeds past the password check and fails at
        create_app() (no DATABASE_URL in the subprocess), but the
        test only verifies the password-specific error is absent.
        """
        result = subprocess.run(
            [sys.executable, "scripts/seed_user.py"],
            env=_safe_env(
                FLASK_ENV="development",
                SEED_USER_PASSWORD="ChangeMe!2026",
            ),
            capture_output=True,
            text=True,
            timeout=10,
        )
        # The script fails later (no DB), but NOT at the password check.
        assert "ChangeMe!2026" not in result.stderr
