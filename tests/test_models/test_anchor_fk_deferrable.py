"""Tests for the Phase 0 anchor-FK migration ``d410f6b9caa3``.

The migration retargets ``budget.accounts.current_anchor_period_id``
from ``ON DELETE SET NULL`` to ``ON DELETE NO ACTION DEFERRABLE
INITIALLY IMMEDIATE`` (pay-period CRUD Phase 0; see
``docs/plans/implementation_plan_pay_period_crud.md``).  Four invariants
are exercised:

  1. **Post-upgrade catalog shape.**  The per-worker template runs the
     migration chain to head, so every clone carries the FK with
     ``confdeltype = 'a'`` (NO ACTION), ``condeferrable = true``, and
     ``condeferred = false`` (INITIALLY IMMEDIATE).
  2. **Model contract.**  ``Account.current_anchor_period_id`` declares
     ``ondelete="NO ACTION"``, ``deferrable=True``, ``initially=
     "IMMEDIATE"`` so ``create_all`` / autogenerate render the same FK
     the migration installs.
  3. **Behavioural immediate check.**  Deleting a pay period that an
     account anchors to raises ``IntegrityError`` at statement time --
     the database backstop that the inherited ``SET NULL`` action (on a
     ``NOT NULL`` column) could never have provided.
  4. **Downgrade round-trip.**  ``downgrade()`` restores ``SET NULL``
     (not deferrable); a following ``upgrade()`` restores the NO ACTION
     DEFERRABLE state.  Proves both directions at the unit level,
     complementing the ``flask db downgrade`` integration check.

Tests bootstrap an Alembic ``MigrationContext`` against the test
session's connection so the migration's ``op.execute`` /
``op.create_foreign_key`` resolve to real DDL through the Operations
proxy.  Mirrors the C-43 round-trip pattern
(``tests/test_models/test_c43_ondelete_and_naming_convention.py``).
"""
# Pylint: redefined-outer-name, unused-argument -- redefined-outer-name is
# the canonical pytest fixture pattern (the local ``restore_anchor_fk_state``
# fixture is consumed under the same name); unused-argument covers
# fixture-only requests (``seed_user``, ``restore_anchor_fk_state``) whose
# DB side effect, not return value, the test depends on.
# pylint: disable=redefined-outer-name,unused-argument
from __future__ import annotations

import importlib.util
import pathlib
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.account import Account


_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)
_MIGRATION_FILE = "d410f6b9caa3_phase0_retarget_anchor_fk_to_no_action_.py"
_FK_NAME = "accounts_current_anchor_period_id_fkey"


def _load_migration(filename: str):
    """Load an Alembic migration file as a module via importlib.

    ``migrations/versions`` has no ``__init__.py`` so a normal import
    would fail; alembic loads scripts this way at runtime.  Mirrors the
    C-43 test loader so the test can call ``upgrade`` / ``downgrade``
    directly.
    """
    path = _MIGRATIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_M = _load_migration(_MIGRATION_FILE)


def _anchor_fk_state(session) -> tuple[str | None, bool | None, bool | None]:
    """Return ``(confdeltype, condeferrable, condeferred)`` for the anchor FK.

    Reads ``pg_constraint`` for ``budget.accounts.<_FK_NAME>``.  Returns
    ``(None, None, None)`` when the constraint is absent.
    """
    row = session.execute(text(
        "SELECT cn.confdeltype, cn.condeferrable, cn.condeferred "
        "FROM pg_constraint cn "
        "JOIN pg_class c ON c.oid = cn.conrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE cn.conname = :name AND n.nspname = 'budget' "
        "AND c.relname = 'accounts' AND cn.contype = 'f'"
    ), {"name": _FK_NAME}).first()
    if row is None:
        return (None, None, None)
    return (row[0], row[1], row[2])


def _run_migration_callable(db, func) -> None:
    """Run a migration ``upgrade``/``downgrade`` against the test connection.

    Wraps the call in an Alembic Operations context bound to the test
    session's connection and patches ``op.get_bind`` so the migration's
    catalog reads resolve to the same connection.  Commits afterwards.
    """
    # Pylint: import-outside-toplevel -- the alembic Operations/op runtime is
    # imported lazily so it loads only when a test actually drives a migration
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
    db.session.commit()


@pytest.fixture
def restore_anchor_fk_state(db):
    """Restore the post-Phase-0 anchor FK after a test mutates it.

    The downgrade round-trip test forces the FK back to ``SET NULL`` and
    relies on its own ``upgrade()`` call to restore the head state.  This
    fixture guarantees the NO ACTION DEFERRABLE state is back in place on
    teardown even if the test body raised mid-way -- otherwise every
    later test running in the same per-worker DB would assert against a
    degraded schema.  ``upgrade()`` is safe to re-run: it drops and
    recreates the constraint unconditionally.
    """
    yield
    db.session.rollback()
    _run_migration_callable(db, _M.upgrade)


class TestPostUpgradeCatalogShape:
    """The per-worker template carries the NO ACTION DEFERRABLE anchor FK."""

    def test_anchor_fk_is_no_action_deferrable_initially_immediate(
        self, app, db, seed_user,
    ):
        """confdeltype='a', condeferrable=true, condeferred=false.

        If this fails the template was built without the Phase 0
        migration; rebuild it via
        ``python scripts/build_test_template.py``.
        """
        with app.app_context():
            confdeltype, condeferrable, condeferred = _anchor_fk_state(
                db.session,
            )
            assert confdeltype == "a", (
                f"anchor FK confdeltype={confdeltype!r}, expected 'a' "
                f"(NO ACTION).  Rebuild the template via "
                f"`python scripts/build_test_template.py`."
            )
            assert condeferrable is True, (
                f"anchor FK condeferrable={condeferrable!r}, expected True "
                f"(DEFERRABLE)."
            )
            assert condeferred is False, (
                f"anchor FK condeferred={condeferred!r}, expected False "
                f"(INITIALLY IMMEDIATE)."
            )


class TestModelContract:
    """The model FK declaration matches the migrated catalog state."""

    def test_model_declares_no_action_deferrable(self):
        """Account.current_anchor_period_id declares the deferrable NO ACTION FK.

        Keeps ``create_all`` / autogenerate in sync with the migration so
        no phantom diff appears on the next ``flask db migrate``.
        """
        column = Account.__table__.columns["current_anchor_period_id"]
        foreign_keys = list(column.foreign_keys)
        assert len(foreign_keys) == 1, (
            f"current_anchor_period_id has {len(foreign_keys)} ForeignKey "
            f"entries; expected exactly one."
        )
        fk = foreign_keys[0]
        assert fk.ondelete == "NO ACTION", (
            f"current_anchor_period_id ForeignKey ondelete={fk.ondelete!r}, "
            f"expected 'NO ACTION'."
        )
        assert fk.deferrable is True, (
            f"current_anchor_period_id ForeignKey deferrable={fk.deferrable!r}, "
            f"expected True."
        )
        assert fk.initially == "IMMEDIATE", (
            f"current_anchor_period_id ForeignKey initially={fk.initially!r}, "
            f"expected 'IMMEDIATE'."
        )


class TestImmediateDeleteCheck:
    """Deleting an anchored period is refused immediately."""

    def test_delete_anchored_period_raises_integrity_error(
        self, app, db, seed_user,
    ):
        """Deleting the period an account anchors to raises IntegrityError.

        ``seed_user``'s Checking account anchors to a pay period
        (``current_anchor_period_id``).  That period is referenced by a
        ``NOT NULL`` FK with ``ON DELETE NO ACTION`` (INITIALLY
        IMMEDIATE), so deleting it must fail at statement time rather
        than NULL the column (which ``SET NULL`` would have attempted
        and which the NOT NULL column would then have rejected at a
        confusing point).
        """
        with app.app_context():
            anchor_period_id = seed_user["account"].current_anchor_period_id
            assert anchor_period_id is not None, (
                "seed_user account has no anchor period; the fixture "
                "precondition for this test is not met."
            )
            try:
                with pytest.raises(IntegrityError):
                    db.session.execute(text(
                        "DELETE FROM budget.pay_periods WHERE id = :pid"
                    ), {"pid": anchor_period_id})
                    db.session.flush()
            finally:
                db.session.rollback()


class TestDowngradeRoundTrip:
    """downgrade() restores SET NULL; upgrade() restores NO ACTION DEFERRABLE."""

    def test_downgrade_then_upgrade_round_trip(
        self, app, db, seed_user, restore_anchor_fk_state,
    ):
        """downgrade -> SET NULL (not deferrable); upgrade -> NO ACTION DEFERRABLE.

        Proves the migration is reversible at the unit level.  The
        ``restore_anchor_fk_state`` fixture guarantees the head state is
        back even if an assertion below fails before the upgrade call.
        """
        with app.app_context():
            _run_migration_callable(db, _M.downgrade)
            confdeltype, condeferrable, _ = _anchor_fk_state(db.session)
            assert confdeltype == "n", (
                f"after downgrade, anchor FK confdeltype={confdeltype!r}, "
                f"expected 'n' (SET NULL)."
            )
            assert condeferrable is False, (
                f"after downgrade, anchor FK condeferrable={condeferrable!r}, "
                f"expected False (SET NULL is not deferrable)."
            )

            _run_migration_callable(db, _M.upgrade)
            confdeltype, condeferrable, condeferred = _anchor_fk_state(
                db.session,
            )
            assert confdeltype == "a" and condeferrable is True, (
                f"after re-upgrade, anchor FK (confdeltype, condeferrable)="
                f"({confdeltype!r}, {condeferrable!r}), expected "
                f"('a', True) for NO ACTION DEFERRABLE."
            )
            assert condeferred is False, (
                f"after re-upgrade, anchor FK condeferred={condeferred!r}, "
                f"expected False (INITIALLY IMMEDIATE)."
            )
