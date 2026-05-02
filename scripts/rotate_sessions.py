"""
Shekel Budget App -- Force-Invalidate All User Sessions

One-shot operations utility that bumps ``users.session_invalidated_at``
to ``now()`` for every row in ``auth.users``.  Run it after rotating
``SECRET_KEY``, after a git history rewrite that excised a leaked key,
or after any other event that compromises previously-issued session
cookies and remember-me tokens.

The application's ``load_user`` callback (``app/__init__.py``) compares
``users.session_invalidated_at`` against the per-session
``_session_created_at`` timestamp on each request.  When this script
runs, every existing session becomes older than the new
``session_invalidated_at`` and is rejected on the next request,
forcing every active user to log in again.

Usage:
    python scripts/rotate_sessions.py --confirm

The ``--confirm`` flag is mandatory: running without it prints a
short usage hint and exits with code 1, never touching the database.

Test entry point:
    ``execute_rotation(db.session)`` returns the number of rows
    updated.  Tests call this directly with the test session and never
    create a separate Flask app.
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

# Ensure the project root is on sys.path so 'app' is importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def execute_rotation(db_session) -> int:
    """Bump ``session_invalidated_at`` to ``now()`` for every user row.

    The core data operation, separated from app creation so tests can
    call it directly with the test database session.

    Args:
        db_session (sqlalchemy.orm.Session): A SQLAlchemy session
            bound to a database that already has the ``auth.users``
            table.

    Returns:
        The number of rows updated.  Returns ``0`` for an empty
        ``auth.users`` table (also a valid post-condition).

    Side effects:
        - Updates every row in ``auth.users``.
        - Commits the transaction.
        - Emits a structured log event ``sessions_invalidated_global``
          at ``WARNING`` level with the row count.
    """
    # Local imports keep this module importable even when the app
    # package has not yet been initialized (matches the convention used
    # in scripts/audit_cleanup.py).
    from app.models.user import User  # pylint: disable=import-outside-toplevel
    from app.utils.log_events import (  # pylint: disable=import-outside-toplevel
        AUTH,
        log_event,
    )

    logger = logging.getLogger(__name__)

    now = datetime.now(timezone.utc)
    count = db_session.query(User).update(
        {User.session_invalidated_at: now},
        synchronize_session=False,
    )
    db_session.commit()

    log_event(
        logger,
        logging.WARNING,
        "sessions_invalidated_global",
        AUTH,
        "Global session invalidation: bumped session_invalidated_at "
        "for all users.",
        count=count,
    )
    return count


def run_rotation() -> int:
    """Create the Flask app and execute the rotation.

    Convenience wrapper for CLI use.  Tests should call
    ``execute_rotation()`` directly with the test db session.

    Returns:
        The number of rows updated.
    """
    # Local imports so the module is importable even when create_app
    # would fail (e.g. config validation in a test that has not yet
    # patched the environment).
    from app import create_app  # pylint: disable=import-outside-toplevel
    from app.extensions import db  # pylint: disable=import-outside-toplevel

    app = create_app()
    with app.app_context():
        return execute_rotation(db.session)


def parse_args(argv=None):
    """Parse command-line arguments.

    Args:
        argv (list[str] | None): Argument list (defaults to
            ``sys.argv[1:]`` when ``None``).

    Returns:
        argparse.Namespace with ``confirm`` (bool).
    """
    parser = argparse.ArgumentParser(
        description=(
            "Force-invalidate every user session by bumping "
            "session_invalidated_at to now().  Run after rotating "
            "SECRET_KEY or after a git history rewrite that exposed "
            "a previously-leaked key."
        ),
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help=(
            "Acknowledge that every active user will be logged out. "
            "Required: the script refuses to run without this flag."
        ),
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    """CLI entry point.

    Args:
        argv (list[str] | None): Argument list (defaults to
            ``sys.argv[1:]`` when ``None``).

    Returns:
        Process exit code.  ``0`` on success, ``1`` when ``--confirm``
        was not supplied.
    """
    args = parse_args(argv)
    if not args.confirm:
        print(
            "Refusing to run without --confirm.  Re-run as:\n"
            "    python scripts/rotate_sessions.py --confirm",
            file=sys.stderr,
        )
        return 1
    count = run_rotation()
    print(f"Invalidated {count} session(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
