"""
Shekel Budget App -- Audit Log Retention Cleanup

Deletes audit_log rows older than the configured retention period.
Intended to run as a daily cron job.

Usage:
    python scripts/audit_cleanup.py [--days N] [--dry-run]

Options:
    --days N     Override retention period (default: AUDIT_RETENTION_DAYS env
                 var, or 365 if not set).
    --dry-run    Print the count of rows that would be deleted without
                 actually deleting them.

Examples:
    python scripts/audit_cleanup.py                 # Delete rows older than 365 days
    python scripts/audit_cleanup.py --days 90       # Delete rows older than 90 days
    python scripts/audit_cleanup.py --dry-run       # Preview without deleting

Cron example (daily at 3:00 AM):
    0 3 * * * cd /home/shekel/app && /opt/venv/bin/python scripts/audit_cleanup.py
"""
import argparse
import logging
import os
import sys

# Ensure the project root is on sys.path so 'app' is importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def parse_args(argv=None):
    """Parse command-line arguments.

    Args:
        argv: Argument list (defaults to sys.argv[1:]).

    Returns:
        argparse.Namespace with ``days`` (int) and ``dry_run`` (bool).
    """
    parser = argparse.ArgumentParser(
        description="Delete audit_log rows older than the retention period."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=int(os.getenv("AUDIT_RETENTION_DAYS", "365")),
        help="Retention period in days (default: AUDIT_RETENTION_DAYS or 365).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print count of rows to delete without actually deleting.",
    )
    args = parser.parse_args(argv)
    if args.days < 1:
        parser.error("--days must be at least 1 to prevent deleting the entire audit log.")
    return args


def execute_cleanup(db_session, days, dry_run=False):
    """Run the audit log cleanup against an existing database session.

    This is the core logic, separated from app creation so it can be
    called from tests with the test database session.

    Args:
        db_session: A SQLAlchemy session (e.g., ``db.session``).
        days: Number of days to retain.  Rows older than this are deleted.
        dry_run: If True, only count rows without deleting.

    Returns:
        The number of rows deleted (or that would be deleted in dry-run mode).
    """
    from sqlalchemy import text  # pylint: disable=import-outside-toplevel

    logger = logging.getLogger(__name__)

    if dry_run:
        result = db_session.execute(
            text("""
                SELECT COUNT(*)
                FROM system.audit_log
                WHERE executed_at < now() - make_interval(days => :days)
            """),
            {"days": days},
        )
        count = result.scalar()
        logger.info(
            "Dry run: %d audit_log rows older than %d days would be deleted.",
            count,
            days,
        )
    else:
        result = db_session.execute(
            text("""
                DELETE FROM system.audit_log
                WHERE executed_at < now() - make_interval(days => :days)
            """),
            {"days": days},
        )
        count = result.rowcount
        db_session.commit()
        logger.info(
            "Deleted %d audit_log rows older than %d days.",
            count,
            days,
        )

    return count


def run_cleanup(days, dry_run=False):
    """Create the app and execute the audit log cleanup.

    Convenience wrapper for CLI use.  Tests should call
    ``execute_cleanup()`` directly with the test db session.

    Args:
        days: Number of days to retain. Rows older than this are deleted.
        dry_run: If True, only count rows without deleting.

    Returns:
        The number of rows deleted (or that would be deleted in dry-run mode).
    """
    from app import create_app  # pylint: disable=import-outside-toplevel
    from app.extensions import db  # pylint: disable=import-outside-toplevel

    app = create_app()
    with app.app_context():
        return execute_cleanup(db.session, days, dry_run=dry_run)


if __name__ == "__main__":
    args = parse_args()
    deleted = run_cleanup(args.days, dry_run=args.dry_run)
    print(f"{'Would delete' if args.dry_run else 'Deleted'}: {deleted} rows")
