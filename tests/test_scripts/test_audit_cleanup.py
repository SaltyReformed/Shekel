"""Tests for scripts/audit_cleanup.py (Phase 8B WU-5)."""
from app.extensions import db
from scripts.audit_cleanup import execute_cleanup


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

    def test_cleanup_with_zero_days_deletes_all(self, app, db):
        """execute_cleanup(days=0) deletes all audit_log rows."""
        _insert_audit_row(days_ago=0)
        _insert_audit_row(days_ago=1)
        db.session.commit()

        deleted = execute_cleanup(db.session, days=0, dry_run=False)
        assert deleted == 2
        assert _audit_count() == 0

    def test_cleanup_with_empty_table(self, app, db):
        """execute_cleanup() on an empty audit_log table returns 0."""
        deleted = execute_cleanup(db.session, days=365, dry_run=False)
        assert deleted == 0
