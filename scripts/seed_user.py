"""
Shekel Budget App -- Seed User & Default Data

Creates the single Phase 1 user by delegating to
``auth_service.register_user`` -- the same provisioning path the
/register route uses -- so the seeded user is identical in shape to a
self-registered one: user, settings, bootstrap pay period, checking
account (with origination anchor history), baseline scenario, default
categories, and default tax configuration.  This script owns only the
operator-facing policy around that call: env parsing, production
password guards, the idempotent already-exists skip, redacted
logging, and the credential scrub.  (Historically it hand-copied the
provisioning sequence and had already drifted from the service --
e.g. it never seeded tax data.)

Validates that the password is at least 12 characters, matching the
minimum enforced by the application's change_password() and
register_user() functions.  Exits with code 1 if the password is
too short.

After seeding completes (or returns early on an existing user), the
SEED_USER_PASSWORD and SEED_USER_EMAIL values are scrubbed from
``os.environ`` and the C-level environment.  This is defense-in-depth
against a future caller (or a child process spawned during seeding)
reading the credential back out of the process environment after it
has served its one-shot purpose.  The matching scrub in
``entrypoint.sh`` removes the same variables from the parent shell
before exec'ing Gunicorn -- closing the ``cat /proc/<gunicorn>/environ``
exposure called out in audit finding F-022.  See audit finding F-022
and remediation Commit C-34.

Usage:
    python scripts/seed_user.py

Environment variables (or .env file):
    SEED_USER_EMAIL        -- default: admin@shekel.local
    SEED_USER_PASSWORD     -- default: ChangeMe!2026
    SEED_USER_DISPLAY_NAME -- default: Budget Admin
"""

import os
import sys


# Names of the seed-only env vars that must be scrubbed from
# ``os.environ`` after the seed step completes.  SEED_USER_DISPLAY_NAME
# is intentionally omitted -- the display name is not a secret, never
# leaves the user record, and may be useful for ops queries that want
# to identify the seeded account by name without paying the audit-log
# cost of resolving by id.
_SEED_SECRET_ENV_VARS: tuple[str, ...] = (
    "SEED_USER_PASSWORD",
    "SEED_USER_EMAIL",
)

# Add project root to path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Pylint: wrong-import-position -- the sys.path bootstrap above must run
# before these imports so ``app`` resolves when invoked as
# ``python scripts/seed_user.py`` (sys.path[0] is scripts/, not the repo
# root, in that mode).
# pylint: disable=wrong-import-position
from app import create_app
from app.exceptions import ConflictError, ValidationError
from app.extensions import db
from app.models.user import User
from app.services import auth_service
# pylint: enable=wrong-import-position


def seed_user():
    """Create the seeded user via ``auth_service.register_user``.

    Applies the script-side guards (production password policy, the
    12-character minimum with operator guidance), then delegates the
    provisioning itself to the registration service so the seeded
    user's shape is identical to a /register user's.  Idempotent: an
    existing user takes the already-exists skip and is returned
    unchanged.  Exits with code 1 on a password or input problem.

    Returns:
        The created (or pre-existing) User.
    """
    email = os.getenv("SEED_USER_EMAIL", "admin@shekel.local")
    password = os.getenv("SEED_USER_PASSWORD", "ChangeMe!2026")
    display_name = os.getenv("SEED_USER_DISPLAY_NAME", "Budget Admin")

    # Production safety: reject the publicly documented default password
    # and empty/whitespace-only passwords.  This guard lives inside
    # seed_user() (not just __main__) so it protects all callers.
    flask_env = os.getenv("FLASK_ENV", "development")
    if flask_env == "production":
        if not password or not password.strip():
            print(
                "Error: SEED_USER_PASSWORD is empty or whitespace-only. "
                "Set a strong password in .env or environment.",
                file=sys.stderr,
            )
            sys.exit(1)
        if password == "ChangeMe!2026":
            print(
                "Error: SEED_USER_PASSWORD is still the default "
                "'ChangeMe!2026'. Set a unique password for production "
                "in .env or environment.",
                file=sys.stderr,
            )
            sys.exit(1)
        if len(password) < 12:
            print(
                f"Warning: SEED_USER_PASSWORD is only {len(password)} "
                f"characters. The application requires at least 12.",
                file=sys.stderr,
            )

    # Enforce the same 12-character minimum as the app's change_password()
    # and register_user() functions.  Prevents deploying with a weak
    # default that cannot be changed through the UI.
    if len(password) < 12:
        print(
            f"Error: SEED_USER_PASSWORD must be at least 12 characters "
            f"(got {len(password)}).  Set SEED_USER_PASSWORD in .env or "
            f"environment."
        )
        sys.exit(1)

    # Provision via the canonical registration service -- the same
    # path /register uses -- so the seeded user's shape (settings,
    # bootstrap period, checking account, baseline scenario,
    # categories, tax configuration) cannot drift from a
    # self-registered user's.  ``register_user`` checks email
    # uniqueness itself and raises BEFORE creating anything, so the
    # ConflictError branch is the idempotent already-exists skip
    # (container restarts re-run this script).  Audit finding F-114 /
    # C-16: stdout is captured by the container log driver and shipped
    # off-host, so log lines carry the synthetic primary key, never
    # the email.
    try:
        user = auth_service.register_user(email, password, display_name)
    except ConflictError:
        existing = (
            db.session.query(User)
            .filter_by(email=email.strip().lower())
            .one()
        )
        print(
            f"User id={existing.id} already exists (email redacted).  "
            "Skipping."
        )
        return existing
    except ValidationError as exc:
        # Operator input problem (e.g. a malformed SEED_USER_EMAIL).
        # The message is safe to print: it describes the rule, not the
        # value.
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    db.session.commit()
    print(
        f"Created user id={user.id} (email redacted from log) with "
        "settings, bootstrap pay period, checking account, baseline "
        "scenario, default categories, and default tax configuration."
    )
    # Final summary stays on user_id only -- the email is the same
    # value the operator passed in via SEED_USER_EMAIL (or the
    # documented default), so re-emitting it here would only add a
    # PII surface to the captured container log.
    print("\nSeed complete.  You can now log in with:")
    print(f"  User ID:  {user.id} (email passed via SEED_USER_EMAIL)")
    print("  Password: [set via SEED_USER_PASSWORD env var or default]")
    return user


def _check_production_password():
    """Reject unsafe passwords before starting the app.

    Runs before create_app() so the check works even when
    DATABASE_URL is not set (e.g., direct script invocation
    outside Docker).
    """
    password = os.getenv("SEED_USER_PASSWORD", "ChangeMe!2026")
    flask_env = os.getenv("FLASK_ENV", "development")
    if flask_env != "production":
        return
    if not password or not password.strip():
        print(
            "Error: SEED_USER_PASSWORD is empty or whitespace-only. "
            "Set a strong password in .env or environment.",
            file=sys.stderr,
        )
        sys.exit(1)
    if password == "ChangeMe!2026":
        print(
            "Error: SEED_USER_PASSWORD is still the default "
            "'ChangeMe!2026'. Set a unique password for production "
            "in .env or environment.",
            file=sys.stderr,
        )
        sys.exit(1)
    if len(password) < 12:
        print(
            f"Warning: SEED_USER_PASSWORD is only {len(password)} "
            f"characters. The application requires at least 12.",
            file=sys.stderr,
        )


def _scrub_seed_env_vars() -> None:
    """Remove SEED_USER_PASSWORD/SEED_USER_EMAIL from this process env.

    The seed credentials are needed only at one-shot invocation time.
    Once the user row is in the database (whether created by this run
    or already present from a prior run), the values have served their
    purpose and should not linger in ``os.environ`` where any
    subsequent code path -- application logging, debug introspection,
    a child process inherited via ``subprocess.run(env=os.environ)`` --
    could surface them.

    Removal is performed at three layers for defense-in-depth:

    1. ``os.environ.pop`` -- removes the key from Python's environment
       mapping.  In CPython 3.9+ this also calls ``os.unsetenv`` under
       the hood, so the C-level ``environ`` array is updated too.
    2. An explicit ``os.unsetenv`` -- documents intent and protects
       against any future change to ``os.environ.pop``'s implementation
       that decouples it from the C-level environ.
    3. The parent ``entrypoint.sh`` runs ``unset SEED_USER_PASSWORD ...``
       after this script returns, scrubbing the same keys from the
       shell that exec's Gunicorn.  Without that companion change,
       Gunicorn would still inherit the credential in
       ``/proc/<pid>/environ`` -- this Python-side scrub only helps
       within this script's process tree.

    DISPLAY_NAME is intentionally retained.  It is not a secret and
    has operational value (e.g. an operator confirming they seeded
    the right account by name).  See audit finding F-022 and
    remediation Commit C-34.
    """
    for key in _SEED_SECRET_ENV_VARS:
        # ``os.environ.pop`` is a no-op when the key is absent.  No
        # try/except guard is needed because the default argument
        # silences a missing key.
        os.environ.pop(key, None)
        # ``os.unsetenv`` is a no-op when the underlying environ entry
        # is already absent on POSIX (Linux containers, the only
        # production target).  Wrapped in a guard nonetheless because
        # CPython documents the behaviour as platform-dependent and
        # raising here would propagate as a script failure that masks
        # the actual seed result.
        try:
            os.unsetenv(key)
        except OSError:
            pass


if __name__ == "__main__":
    _check_production_password()
    app = create_app()
    try:
        with app.app_context():
            seed_user()
    finally:
        # Scrub credentials regardless of the seed outcome.  A failed
        # seed must not leave the password in os.environ for a future
        # retry to read; the next run sources the value from the
        # docker-compose env or the docker secret afresh.
        _scrub_seed_env_vars()
