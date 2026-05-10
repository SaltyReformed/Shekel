"""
Shekel Budget App -- Seed User & Default Data

Creates the single Phase 1 user plus their default checking account,
baseline scenario, user settings, and starter categories.

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

from app import create_app
from app.extensions import db
from app.models.user import User, UserSettings
from app.models.account import Account
from app.models.scenario import Scenario
from app.models.category import Category
from app.models.ref import AccountType
from app.services.auth_service import hash_password, DEFAULT_CATEGORIES


def seed_user():
    """Create the seeded user and all associated default data.

    Validates that the password is at least 12 characters, matching the
    minimum enforced by the application's change_password() and
    register_user() functions.  Exits with code 1 if the password is
    too short.
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

    # Check if user already exists.  Audit finding F-114 / C-16:
    # the script's stdout is captured by the container log driver
    # and shipped off-host for retention.  Logging the seed user's
    # email on every container start would surface a real PII value
    # in the long-term log store with no operational benefit -- the
    # operator already knows which account they seeded.  Use the
    # synthetic primary key instead so the line stays useful for
    # idempotency debugging without being a PII source.
    existing = db.session.query(User).filter_by(email=email).first()
    if existing:
        print(
            f"User id={existing.id} already exists (email redacted).  "
            "Skipping."
        )
        return existing

    # Create the user.
    user = User(
        email=email,
        password_hash=hash_password(password),
        display_name=display_name,
    )
    db.session.add(user)
    db.session.flush()  # Get user.id
    print(f"Created user id={user.id} (email redacted from log).")

    # Create user settings.
    settings = UserSettings(user_id=user.id)
    db.session.add(settings)
    print("  + User settings created.")

    # Create checking account.
    checking_type = db.session.query(AccountType).filter_by(name="Checking").one()
    account = Account(
        user_id=user.id,
        account_type_id=checking_type.id,
        name="Checking",
        current_anchor_balance=0,
    )
    db.session.add(account)
    print("  + Checking account created.")

    # Create baseline scenario.
    scenario = Scenario(
        user_id=user.id,
        name="Baseline",
        is_baseline=True,
    )
    db.session.add(scenario)
    print("  + Baseline scenario created.")

    # Create default categories.
    for sort_idx, (group, item) in enumerate(DEFAULT_CATEGORIES):
        cat = Category(
            user_id=user.id,
            group_name=group,
            item_name=item,
            sort_order=sort_idx,
        )
        db.session.add(cat)
    print(f"  + {len(DEFAULT_CATEGORIES)} default categories created.")

    db.session.commit()
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
