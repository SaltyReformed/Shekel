"""Tests for the Phase 0 unique-index migration ``f75485db6757``.

The migration upgrades the non-unique ``idx_pay_periods_user_index`` to a
UNIQUE constraint ``uq_pay_periods_user_index`` on
``budget.pay_periods (user_id, period_index)`` (pay-period CRUD Phase 0;
see ``docs/plans/implementation_plan_pay_period_crud.md``).  This puts the
balance resolver's index-is-unique-per-user invariant in the schema so
every period-appending path is protected.  Four invariants:

  1. **Post-upgrade catalog shape.**  ``uq_pay_periods_user_index`` is a
     UNIQUE constraint; the legacy ``idx_pay_periods_user_index`` index is
     gone.
  2. **Model contract.**  ``PayPeriod`` declares the ``UniqueConstraint``
     on ``(user_id, period_index)`` so ``create_all`` / autogenerate
     match the migration.
  3. **Behavioural rejection.**  A second period with an existing
     ``(user_id, period_index)`` (and a fresh ``start_date`` so the
     unrelated ``uq_pay_periods_user_start`` does not fire) raises
     ``IntegrityError`` naming ``uq_pay_periods_user_index``.
  4. **Per-user scope.**  The same ``period_index`` is allowed for a
     different user -- the uniqueness is scoped to ``user_id``.
"""
# Pylint: unused-argument -- fixture-only requests (``seed_user``,
# ``bare_periods``) are needed for their DB-population side effect, not their
# return value, so the parameter is intentionally unreferenced in the body.
# pylint: disable=unused-argument
from __future__ import annotations

import importlib.util
import pathlib
from datetime import date
from unittest.mock import patch

import pytest
from sqlalchemy import UniqueConstraint, text
from sqlalchemy.exc import IntegrityError

from app.models.pay_period import PayPeriod


_NEW_CONSTRAINT = "uq_pay_periods_user_index"
_OLD_INDEX = "idx_pay_periods_user_index"
_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)
_MIGRATION_FILE = "f75485db6757_phase0_unique_user_period_index_on_pay_.py"


def _load_migration(filename: str):
    """Load an Alembic migration file as a module via importlib.

    ``migrations/versions`` has no ``__init__.py`` so a normal import
    fails; alembic loads scripts this way at runtime.  Used to call the
    migration's ``upgrade`` directly so the pre-flight guard can be
    exercised.
    """
    path = _MIGRATIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_M = _load_migration(_MIGRATION_FILE)


def _constraint_present(session, name: str) -> bool:
    """Return True iff the named constraint exists in the budget schema."""
    return bool(session.execute(text(
        "SELECT EXISTS ("
        "  SELECT 1 FROM pg_constraint cn "
        "  JOIN pg_class c ON c.oid = cn.conrelid "
        "  JOIN pg_namespace n ON n.oid = c.relnamespace "
        "  WHERE cn.conname = :name AND n.nspname = 'budget'"
        ")"
    ), {"name": name}).scalar())


def _run_migration_callable(db, func) -> None:
    """Run a migration callable against the test session's connection.

    Binds an Alembic Operations context and patches ``op.get_bind`` so
    the migration's catalog reads resolve to the test connection.  Does
    NOT commit -- the caller decides, since the pre-flight path raises
    before any DDL.
    """
    # Pylint: import-outside-toplevel -- the alembic runtime is imported
    # lazily so it loads only when a test actually drives a migration
    # callable, mirroring the C-43 migration-test pattern.
    # pylint: disable=import-outside-toplevel
    from alembic import op
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    bind = db.session.connection()
    ctx = MigrationContext.configure(connection=bind)
    with Operations.context(ctx):
        with patch.object(op, "get_bind", return_value=bind):
            func()


class TestPostUpgradeCatalogShape:
    """The per-worker template carries the UNIQUE constraint, not the old index."""

    def test_unique_constraint_present_and_old_index_absent(
        self, app, db, seed_user,
    ):
        """uq_pay_periods_user_index is UNIQUE; idx_pay_periods_user_index is gone.

        If this fails the template predates the Phase 0 migration; rebuild
        it via ``python scripts/build_test_template.py``.
        """
        with app.app_context():
            contype = db.session.execute(text(
                "SELECT cn.contype FROM pg_constraint cn "
                "JOIN pg_class c ON c.oid = cn.conrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE cn.conname = :name AND n.nspname = 'budget' "
                "AND c.relname = 'pay_periods'"
            ), {"name": _NEW_CONSTRAINT}).scalar()
            assert contype == "u", (
                f"{_NEW_CONSTRAINT} contype={contype!r}, expected 'u' "
                f"(UNIQUE).  Rebuild the template via "
                f"`python scripts/build_test_template.py`."
            )

            old_index = db.session.execute(text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = 'budget' AND tablename = 'pay_periods' "
                "AND indexname = :name"
            ), {"name": _OLD_INDEX}).scalar()
            assert old_index is None, (
                f"legacy index {_OLD_INDEX} still present alongside the "
                f"UNIQUE constraint -- the migration should have dropped it."
            )


class TestModelContract:
    """The model declares the UNIQUE constraint the migration installs."""

    def test_model_declares_user_index_unique_constraint(self):
        """PayPeriod declares UniqueConstraint(user_id, period_index).

        Keeps ``create_all`` / autogenerate in sync with the migration.
        """
        matching = [
            constraint
            for constraint in PayPeriod.__table__.constraints
            if isinstance(constraint, UniqueConstraint)
            and constraint.name == _NEW_CONSTRAINT
        ]
        assert len(matching) == 1, (
            f"PayPeriod declares {len(matching)} UniqueConstraint(s) named "
            f"{_NEW_CONSTRAINT!r}; expected exactly one."
        )
        columns = [column.name for column in matching[0].columns]
        assert columns == ["user_id", "period_index"], (
            f"{_NEW_CONSTRAINT} covers {columns!r}; expected "
            f"['user_id', 'period_index']."
        )


class TestUniquenessBehaviour:
    """The constraint rejects same-user duplicates and allows cross-user repeats."""

    def test_duplicate_period_index_same_user_raises(
        self, app, db, bare_periods,
    ):
        """A second period_index=0 for the same user raises IntegrityError.

        ``bare_periods`` occupies indices 0..9 for ``bare_user``.  The new
        row uses ``period_index=0`` (already taken) with a 2030 start_date
        that no existing period uses, so the only constraint that can fire
        is ``uq_pay_periods_user_index`` -- asserted via the error text so
        a future drift to the ``uq_pay_periods_user_start`` constraint
        would not silently pass this test.
        """
        user_id = bare_periods[0].user_id
        with app.app_context():
            try:
                with pytest.raises(
                    IntegrityError, match=_NEW_CONSTRAINT,
                ):
                    db.session.add(PayPeriod(
                        user_id=user_id,
                        start_date=date(2030, 1, 1),
                        end_date=date(2030, 1, 14),
                        period_index=0,
                    ))
                    db.session.flush()
            finally:
                db.session.rollback()

    def test_same_period_index_different_user_allowed(
        self, app, db, bare_periods, second_user,
    ):
        """The same period_index is allowed for a different user.

        ``bare_periods`` holds index 5 for ``bare_user``; inserting an
        index-5 period for ``second_user`` must succeed because the
        uniqueness is scoped per ``user_id``.
        """
        other_user_id = second_user["user"].id
        with app.app_context():
            period = PayPeriod(
                user_id=other_user_id,
                start_date=date(2030, 2, 1),
                end_date=date(2030, 2, 14),
                period_index=5,
            )
            db.session.add(period)
            db.session.commit()
            assert db.session.get(PayPeriod, period.id) is not None, (
                "index-5 period for second_user did not persist; the "
                "uniqueness is not scoped per user as expected."
            )


class TestMigrationPreFlightGuard:
    """Migration 1b refuses to apply when duplicate indices already exist.

    The pre-flight check is the data-safety net protecting a production
    database during the upgrade: if (despite the app-level ``max+1``
    convention) two periods ever shared a ``(user_id, period_index)``,
    the migration must REFUSE with an actionable error naming the
    offending rows, rather than fail with a raw ``CREATE UNIQUE``
    violation or apply a constraint that silently does not match the
    data.  This proves that safety net works before it is ever needed in
    production.
    """

    def test_upgrade_raises_when_duplicate_indices_present(
        self, app, db, bare_user,
    ):
        """Pre-flight raises RuntimeError and does NOT create the constraint.

        Drops the live constraint (installed by the test-template chain),
        sneaks in a duplicate ``(user_id, period_index)`` pair, then runs
        the migration's ``upgrade()``: the pre-flight must raise a
        ``RuntimeError`` mentioning the duplicate, and the constraint must
        be absent afterwards.  Head state (constraint present, no
        duplicates) is restored in ``finally`` so later tests in this
        worker DB are unaffected.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            # 1. Drop the constraint so duplicate indices can be inserted.
            db.session.execute(text(
                "ALTER TABLE budget.pay_periods "
                "DROP CONSTRAINT IF EXISTS uq_pay_periods_user_index"
            ))
            db.session.commit()
            try:
                # 2. Two periods sharing (user_id, period_index)=(uid, 0).
                db.session.add_all([
                    PayPeriod(
                        user_id=user_id, start_date=date(2026, 1, 2),
                        end_date=date(2026, 1, 15), period_index=0,
                    ),
                    PayPeriod(
                        user_id=user_id, start_date=date(2026, 1, 16),
                        end_date=date(2026, 1, 29), period_index=0,
                    ),
                ])
                db.session.flush()

                # 3. The migration's pre-flight must refuse, naming the
                # duplicate so an operator can act on it.
                with pytest.raises(RuntimeError, match="duplicate"):
                    _run_migration_callable(db, _M.upgrade)

                # 4. It must NOT have created the constraint over bad data.
                assert not _constraint_present(db.session, _NEW_CONSTRAINT), (
                    "upgrade() created uq_pay_periods_user_index despite the "
                    "duplicate (user_id, period_index) pair -- the pre-flight "
                    "guard failed to block it."
                )
            finally:
                # Restore head state regardless of outcome: remove the
                # duplicates, then re-add the constraint.
                db.session.rollback()
                db.session.query(PayPeriod).filter_by(
                    user_id=user_id,
                ).delete()
                db.session.commit()
                if not _constraint_present(db.session, _NEW_CONSTRAINT):
                    db.session.execute(text(
                        "ALTER TABLE budget.pay_periods "
                        "ADD CONSTRAINT uq_pay_periods_user_index "
                        "UNIQUE (user_id, period_index)"
                    ))
                    db.session.commit()
