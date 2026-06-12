"""Tests for scripts/audit_cleanup.py (Phase 8B WU-5)."""
import pytest
from app.extensions import db
from scripts.audit_cleanup import execute_cleanup, parse_args


def _insert_audit_row(days_ago=0):
    """Insert a test audit_log row with executed_at offset by days_ago."""
    db.session.execute(
        db.text("""
            INSERT INTO system.audit_log
                (table_schema, table_name, operation, row_id, executed_at)
            VALUES
                ('budget', 'transactions', 'INSERT', 1,
                 now() - make_interval(days => :days))
        """),
        {"days": days_ago},
    )
    db.session.flush()


def _audit_count():
    """Return the current row count in system.audit_log."""
    result = db.session.execute(
        db.text("SELECT COUNT(*) FROM system.audit_log")
    )
    return result.scalar()


class TestAuditCleanup:
    """Tests for the audit_cleanup execute_cleanup() function."""

    def test_cleanup_deletes_old_rows(self, app, db):
        """execute_cleanup() deletes rows older than the retention period."""
        _insert_audit_row(days_ago=400)  # older than 365
        _insert_audit_row(days_ago=10)   # recent
        db.session.commit()

        deleted = execute_cleanup(db.session, days=365, dry_run=False)
        assert deleted == 1
        assert _audit_count() == 1

    def test_cleanup_preserves_recent_rows(self, app, db):
        """execute_cleanup() does not delete rows within the retention period."""
        _insert_audit_row(days_ago=5)
        _insert_audit_row(days_ago=30)
        db.session.commit()

        deleted = execute_cleanup(db.session, days=365, dry_run=False)
        assert deleted == 0
        assert _audit_count() == 2

    def test_cleanup_dry_run_does_not_delete(self, app, db):
        """execute_cleanup(dry_run=True) counts but does not delete rows."""
        _insert_audit_row(days_ago=400)
        db.session.commit()

        count = execute_cleanup(db.session, days=365, dry_run=True)
        assert count == 1
        # Row should still be there.
        assert _audit_count() == 1

    def test_cleanup_returns_correct_count(self, app, db):
        """execute_cleanup() returns the number of deleted rows."""
        for days in (400, 500, 600):
            _insert_audit_row(days_ago=days)
        _insert_audit_row(days_ago=10)
        db.session.commit()

        deleted = execute_cleanup(db.session, days=365, dry_run=False)
        assert deleted == 3

    def test_cleanup_with_zero_days_rejected(self, app, db):
        """execute_cleanup(days=0) raises ValueError and deletes nothing.

        days=0 makes the cutoff ``now()``, which matches every existing
        row -- the destroy-the-entire-log hazard parse_args already
        blocks at the CLI.  The programmatic path enforces the identical
        ``days >= 1`` bound before any DB work.
        """
        _insert_audit_row(days_ago=0)
        _insert_audit_row(days_ago=1)
        db.session.commit()

        with pytest.raises(ValueError, match="at least 1"):
            execute_cleanup(db.session, days=0, dry_run=False)
        # Nothing was deleted -- the guard fired before any DB work.
        assert _audit_count() == 2

    def test_cleanup_with_empty_table(self, app, db):
        """execute_cleanup() on an empty audit_log table returns 0."""
        deleted = execute_cleanup(db.session, days=365, dry_run=False)
        assert deleted == 0

    def test_cleanup_negative_days_rejected(self, app, db):
        """execute_cleanup(days=-1) raises ValueError before any DB work.

        A negative days value creates a future cutoff in PostgreSQL:
        ``now() - make_interval(days => -1)`` equals ``now() + 1 day``,
        so ``executed_at < cutoff`` matches every existing row and
        silently destroys the entire audit log.  The guard rejects
        ``days < 1``, matching the parse_args CLI bound.
        """
        _insert_audit_row(days_ago=30)
        _insert_audit_row(days_ago=0)
        _insert_audit_row(days_ago=1)
        db.session.commit()

        with pytest.raises(ValueError, match="at least 1"):
            execute_cleanup(db.session, days=-1, dry_run=False)
        # All three rows survive -- the guard fired before any DB work.
        assert _audit_count() == 3

    def test_cleanup_does_not_affect_recent_records(self, app, db):
        """execute_cleanup(days=30) preserves records newer than 30 days.

        Seeds 4 rows: 2 old (60 days), 1 recent (15 days), 1 today.
        Only the 2 old rows should be deleted.
        """
        _insert_audit_row(days_ago=60)
        _insert_audit_row(days_ago=60)
        _insert_audit_row(days_ago=15)
        _insert_audit_row(days_ago=0)
        db.session.commit()

        deleted = execute_cleanup(db.session, days=30, dry_run=False)
        assert deleted == 2
        assert _audit_count() == 2


class TestParseArgs:
    """Tests for parse_args CLI argument validation."""

    def test_parse_args_rejects_zero_days(self):
        """parse_args rejects --days 0 to prevent deleting entire audit log."""
        with pytest.raises(SystemExit):
            parse_args(["--days", "0"])

    def test_parse_args_rejects_negative_days(self):
        """parse_args rejects --days -1 to prevent future-cutoff deletion."""
        with pytest.raises(SystemExit):
            parse_args(["--days", "-1"])

    def test_parse_args_accepts_one_day(self):
        """parse_args accepts --days 1 as the minimum retention period."""
        args = parse_args(["--days", "1"])
        assert args.days == 1

    def test_parse_args_accepts_default(self):
        """parse_args defaults to 365 days when --days is not specified."""
        args = parse_args([])
        assert args.days == 365
