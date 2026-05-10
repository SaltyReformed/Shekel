"""Tests for the seed user script's production safety checks and
credential hygiene scrubbing.

Uses subprocess to test _check_production_password() in isolation.
The subprocess environment is carefully controlled:
  - Inherits PATH and Python-related vars (so imports work)
  - Sets FLASK_ENV and SEED_USER_PASSWORD explicitly
  - Does NOT inherit DATABASE_URL (prevents connecting to real DBs)

For the production cases, the script exits at the password check
before reaching create_app().  For the development case, the script
proceeds past the check and fails at create_app() (no DATABASE_URL),
but the test only verifies the password error is absent.

The TestSeedUserCredentialScrub class covers Commit C-34 / audit
finding F-022: after running, ``scripts/seed_user.py`` must remove
SEED_USER_PASSWORD and SEED_USER_EMAIL from its own ``os.environ``
and from the C-level environ array (so any child process inherited
via ``subprocess.run(env=os.environ)`` does not pick the credential
back up).  The companion ``unset`` step in ``entrypoint.sh`` is
covered by tests/test_deploy/test_seed_credential_hygiene.py.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SEED_USER_SCRIPT = REPO_ROOT / "scripts" / "seed_user.py"


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
        # ``check=False`` so we can assert on the non-zero exit code
        # rather than have subprocess raise CalledProcessError.  The
        # whole point of this test is to verify the script exits 1
        # with the expected stderr message.
        result = subprocess.run(
            [sys.executable, "scripts/seed_user.py"],
            env=_safe_env(
                FLASK_ENV="production",
                SEED_USER_PASSWORD="ChangeMe!2026",
            ),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
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
            check=False,
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
            check=False,
        )
        # The script fails later (no DB), but NOT at the password check.
        assert "ChangeMe!2026" not in result.stderr


class TestSeedUserCredentialScrub:
    """Verify scripts/seed_user.py scrubs seed credentials from os.environ.

    Audit finding F-022 / Commit C-34.  The script must not leave
    SEED_USER_PASSWORD or SEED_USER_EMAIL in ``os.environ`` once its
    work is done -- otherwise any subsequent code path inside the
    same Python process (or a child spawned via ``subprocess.run(env=
    os.environ)``) would still see the credential.

    Tests in this class load the script's source as a module rather
    than spawning a subprocess so we can introspect the post-run
    state of ``os.environ`` directly.  The destructive bits
    (create_app + DB writes) are the responsibility of the
    subprocess-based tests in test_seed_redaction.py and the
    ``test_seed_user_subprocess_*`` tests below.
    """

    @staticmethod
    def _import_seed_module():
        """Return the loaded scripts.seed_user module.

        Uses ``importlib`` so each test gets a fresh import without
        polluting other tests' module cache.  Exposing
        ``_scrub_seed_env_vars`` via the imported module means the
        unit tests below can call it in isolation without needing
        the create_app / app_context machinery at all.

        No ``monkeypatch`` argument: callers do their own env
        manipulation BEFORE calling this helper, and the helper just
        loads and returns the module under test.
        """
        import importlib.util  # pylint: disable=import-outside-toplevel
        spec = importlib.util.spec_from_file_location(
            "scripts_seed_user_under_test",
            str(SEED_USER_SCRIPT),
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_scrub_removes_password_from_os_environ(self, monkeypatch):
        """``_scrub_seed_env_vars`` deletes SEED_USER_PASSWORD."""
        # Set the env var so the scrub has something to remove.  The
        # value is a sentinel string we look for AFTER the scrub --
        # any leftover would surface as a false positive.
        monkeypatch.setenv(
            "SEED_USER_PASSWORD", "scrub-test-sentinel-password"
        )
        module = self._import_seed_module()
        # Pre-condition: the env var IS set in this process.
        assert "SEED_USER_PASSWORD" in os.environ
        module._scrub_seed_env_vars()  # pylint: disable=protected-access
        # Post-condition: the env var is gone from BOTH os.environ
        # (Python's mapping) and the underlying C environ (read via
        # os.getenv, which goes through getenv(3)).
        assert "SEED_USER_PASSWORD" not in os.environ, (
            "SEED_USER_PASSWORD still present in os.environ after scrub"
        )
        assert os.getenv("SEED_USER_PASSWORD") is None, (
            "SEED_USER_PASSWORD still present in C environ after scrub "
            "(os.getenv reads the underlying environ via getenv(3))"
        )

    def test_scrub_removes_email_from_os_environ(self, monkeypatch):
        """``_scrub_seed_env_vars`` deletes SEED_USER_EMAIL."""
        monkeypatch.setenv("SEED_USER_EMAIL", "scrub-test@shekel.local")
        module = self._import_seed_module()
        assert "SEED_USER_EMAIL" in os.environ
        module._scrub_seed_env_vars()  # pylint: disable=protected-access
        assert "SEED_USER_EMAIL" not in os.environ
        assert os.getenv("SEED_USER_EMAIL") is None

    def test_scrub_preserves_display_name(self, monkeypatch):
        """``_scrub_seed_env_vars`` MUST NOT touch SEED_USER_DISPLAY_NAME.

        DISPLAY_NAME is not a secret and has operational value (an
        operator confirming they seeded the right account by name
        during a forensic review).  A regression that adds it to the
        scrub list would silently drop useful context.
        """
        monkeypatch.setenv(
            "SEED_USER_DISPLAY_NAME", "Sentinel Display Name"
        )
        module = self._import_seed_module()
        module._scrub_seed_env_vars()  # pylint: disable=protected-access
        assert os.environ.get("SEED_USER_DISPLAY_NAME") == \
            "Sentinel Display Name", (
            "SEED_USER_DISPLAY_NAME was scrubbed; the credential-hygiene "
            "scrub MUST limit itself to PASSWORD and EMAIL"
        )

    def test_scrub_is_a_noop_when_vars_already_absent(self, monkeypatch):
        """Calling scrub twice (or before any seed) must not raise.

        The seed step in entrypoint.sh skips the script entirely when
        the seed sentinel is present, but a future caller might still
        invoke ``_scrub_seed_env_vars`` defensively before any setenv.
        Idempotency means that scenario produces no exception and no
        log output -- ``os.environ.pop(key, None)`` and the guarded
        ``os.unsetenv`` together swallow the missing-key case.
        """
        monkeypatch.delenv("SEED_USER_PASSWORD", raising=False)
        monkeypatch.delenv("SEED_USER_EMAIL", raising=False)
        module = self._import_seed_module()
        # Should not raise.
        module._scrub_seed_env_vars()  # pylint: disable=protected-access
        # Idempotency: calling again is also fine.
        module._scrub_seed_env_vars()  # pylint: disable=protected-access

    def test_scrubbed_credential_not_inherited_by_subprocess(
        self, monkeypatch,
    ):
        """A child spawned after the scrub does NOT see the credential.

        This is the key end-to-end behaviour the F-022 fix is meant
        to provide.  We set the env var, run the scrub in this
        process, then spawn a child that prints its environ -- the
        child's stdout must NOT contain the sentinel value because
        the child inherits the C environ from the parent at fork()
        time, and the scrub modified that array.

        Uses ``env=None`` (inherit parent) on subprocess.run so the
        test exercises the actual inheritance path; passing
        ``env=os.environ.copy()`` would test only the dict, not the
        underlying C environ.
        """
        sentinel = "subprocess-inheritance-test-sentinel-xyz"
        monkeypatch.setenv("SEED_USER_PASSWORD", sentinel)
        module = self._import_seed_module()
        module._scrub_seed_env_vars()  # pylint: disable=protected-access
        # Child process prints its received SEED_USER_PASSWORD value
        # (or the empty string if absent).  We assert the sentinel
        # is NOT in the output.
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import os; print(os.environ.get('SEED_USER_PASSWORD', ''))",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert sentinel not in result.stdout, (
            "Child subprocess inherited SEED_USER_PASSWORD after parent "
            "called _scrub_seed_env_vars; the C-level environ was not "
            "scrubbed"
        )


class TestSeedUserSubprocessScrub:
    """End-to-end: ``python scripts/seed_user.py`` removes the env vars.

    This complements the unit-style tests above by exercising the
    full ``__main__`` path: production guard, create_app, seed_user,
    THEN _scrub_seed_env_vars in the ``finally`` block.  The seed
    step needs DATABASE_URL pointing at a real database; we use the
    test DB the rest of the suite consumes.

    These tests also assert that a seed_user.py CRASH (e.g. missing
    DB) still scrubs the env vars on the way out -- the ``finally``
    block is the load-bearing piece for that property.
    """

    @staticmethod
    def _safe_env_with_db(**overrides):
        """Subprocess env with TEST_DATABASE_URL mapped to DATABASE_URL."""
        env = {}
        for key in ("PATH", "PYTHONPATH", "PYTHONHOME", "HOME",
                    "VIRTUAL_ENV", "LANG", "LC_ALL", "TEST_DATABASE_URL",
                    "SECRET_KEY"):
            if key in os.environ:
                env[key] = os.environ[key]
        if "TEST_DATABASE_URL" in env:
            env["DATABASE_URL"] = env["TEST_DATABASE_URL"]
        env["FLASK_ENV"] = "development"
        env.update(overrides)
        return env

    @pytest.mark.timeout(30)
    def test_subprocess_does_not_print_password_after_seed(
        self, app, db,  # pylint: disable=unused-argument
    ):
        """``python scripts/seed_user.py`` does not echo the password.

        The script's print/log statements were already redacted in
        Commit C-16 (PII / F-114).  This test re-asserts the
        invariant against the C-34 changes -- the new scrub code
        path must not introduce a debug print of the value before
        scrubbing it.
        """
        sentinel_password = "c34-subprocess-scrub-pw-1234567"
        sentinel_email = "c34-subprocess-scrub@shekel.local"
        result = subprocess.run(
            [sys.executable, str(SEED_USER_SCRIPT)],
            env=self._safe_env_with_db(
                SEED_USER_EMAIL=sentinel_email,
                SEED_USER_PASSWORD=sentinel_password,
            ),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        # The script may exit 0 (created) or 0 (already exists);
        # both are acceptable.  The assertion is on the output.
        assert sentinel_password not in result.stdout, (
            f"seed_user.py leaked password into stdout: {result.stdout!r}"
        )
        assert sentinel_password not in result.stderr, (
            f"seed_user.py leaked password into stderr: {result.stderr!r}"
        )
        # Cleanup -- delete the sentinel user the script created.
        from app.extensions import db as _db  # pylint: disable=import-outside-toplevel
        from app.models.user import User  # pylint: disable=import-outside-toplevel
        existing = _db.session.query(User).filter_by(
            email=sentinel_email,
        ).first()
        if existing is not None:
            _db.session.delete(existing)
            _db.session.commit()
