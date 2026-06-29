"""Tests for the bdde62675c9b cash-posting schema migration.

Build-Order Step 3, Commit 2
(``docs/audits/balance_architecture/implementation_plan_posting_ledger_cash_envelopes.md``).
The migration is purely additive: it adds ``budget.ledger_accounts.category_id``
and ``is_fallback`` (+ two partial unique indexes + two partition CHECKs) and
``budget.journal_entries.transaction_id`` (+ one partial index), plus the two
SET-NULL FKs.  No table, no data, no trigger.

The migration is already at HEAD when these tests run (the template builder
upgraded it base->head), so the per-worker DB shows the post-migration
schema.  These tests assert, without re-executing DDL in the worker:

  * the migration is correctly chained (revision / down_revision);
  * this migration's own objects are present in the live catalog with the
    exact predicates / FK actions / CHECK the model declares -- the proof
    that ``upgrade`` ran and matches the models (the behavioural proof that
    each constraint *enforces* its rule lives in ``test_ledger_account.py``
    / ``test_journal_entry.py``; this file proves the named objects exist so
    the downgrade's drop-by-name will find them);
  * the ``downgrade`` is not a bare pass -- it drops every object the
    ``upgrade`` adds.

A full executable upgrade -> downgrade round-trip belongs in the
Alembic-driven environment, not an in-test xdist worker: executing the
downgrade here would DROP columns the whole session's ORM depends on, breaking
every other test in the worker.  The executable round-trip was instead run
during development against the prod-clone dev DB -- ``flask db upgrade``
created the objects, a re-run of ``flask db migrate`` produced an empty diff
(model == migration), and ``flask db downgrade`` removed them cleanly,
returning the DB to the Commit-1 head -- so the source-level downgrade check
below is the safe in-worker analogue (the same split the ref-values migration
test uses).
"""
from __future__ import annotations

import pathlib

from sqlalchemy import text

from tests._test_helpers import load_migration_module


_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)
_MIGRATION_FILENAME = (
    "bdde62675c9b_add_ledger_accounts_category_id_and_.py"
)
_MIGRATION = load_migration_module(_MIGRATION_FILENAME)


class TestMigrationRevisionPair:
    """The migration chains off the Step-3 Commit-1 head."""

    def test_revision_pair(self):
        """revision / down_revision pin the migration into the chain."""
        assert _MIGRATION.revision == "bdde62675c9b"
        assert _MIGRATION.down_revision == "97bc03c2aa4c"


class TestMigratedColumns:
    """The new nullable source/chart columns exist at HEAD."""

    def test_category_id_column(self, app, db):
        """``ledger_accounts.category_id`` is a nullable integer at HEAD."""
        with app.app_context():
            row = db.session.execute(text(
                "SELECT data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = 'budget' "
                "  AND table_name = 'ledger_accounts' "
                "  AND column_name = 'category_id'"
            )).fetchone()
            assert row is not None, "category_id column missing at HEAD"
            assert row[0] == "integer"
            assert row[1] == "YES"

    def test_is_fallback_column(self, app, db):
        """``ledger_accounts.is_fallback`` is a NOT NULL boolean defaulting false."""
        with app.app_context():
            row = db.session.execute(text(
                "SELECT data_type, is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_schema = 'budget' "
                "  AND table_name = 'ledger_accounts' "
                "  AND column_name = 'is_fallback'"
            )).fetchone()
            assert row is not None, "is_fallback column missing at HEAD"
            assert row[0] == "boolean"
            assert row[1] == "NO"
            assert row[2] is not None and "false" in row[2], (
                f"is_fallback must default false; found default {row[2]!r}"
            )

    def test_transaction_id_column(self, app, db):
        """``journal_entries.transaction_id`` is a nullable integer at HEAD."""
        with app.app_context():
            row = db.session.execute(text(
                "SELECT data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = 'budget' "
                "  AND table_name = 'journal_entries' "
                "  AND column_name = 'transaction_id'"
            )).fetchone()
            assert row is not None, "transaction_id column missing at HEAD"
            assert row[0] == "integer"
            assert row[1] == "YES"


class TestMigratedIndexes:
    """The three new partial indexes exist with the model's exact predicates.

    The ``pg_indexes.indexdef`` carries the full CREATE INDEX text, including
    the partial ``WHERE`` predicate, so a wrong predicate (which would change
    which rows the unique constrains) is caught here.
    """

    def _indexdef(self, db, name):
        """Return the CREATE INDEX text for *name* in the budget schema."""
        return db.session.execute(text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname = 'budget' AND indexname = :n"
        ), {"n": name}).scalar()

    def test_category_unique_index(self, app, db):
        """``uq_ledger_accounts_category`` is unique on the right key + predicate."""
        with app.app_context():
            ddl = self._indexdef(db, "uq_ledger_accounts_category")
            assert ddl is not None, "uq_ledger_accounts_category missing"
            assert "UNIQUE INDEX" in ddl
            assert "user_id, category_id, class_id" in ddl
            assert "category_id IS NOT NULL" in ddl
            assert "account_id IS NULL" in ddl

    def test_uncategorized_unique_index(self, app, db):
        """``uq_ledger_accounts_uncategorized`` is unique on (user, class) WHERE is_fallback.

        Keyed ``WHERE is_fallback`` (NOT ``WHERE category_id IS NULL``) -- the
        H1 fix.  Asserting the NULL/NULL predicate is ABSENT guards against a
        regression back to the colliding design.
        """
        with app.app_context():
            ddl = self._indexdef(db, "uq_ledger_accounts_uncategorized")
            assert ddl is not None, "uq_ledger_accounts_uncategorized missing"
            assert "UNIQUE INDEX" in ddl
            assert "user_id, class_id" in ddl
            assert "is_fallback" in ddl
            assert "category_id IS NULL" not in ddl, (
                "the singleton must key on is_fallback, not the NULL/NULL "
                "shape -- keying on category_id IS NULL re-opens the H1 "
                "category-delete collision"
            )

    def test_transaction_partial_index(self, app, db):
        """``idx_journal_entries_transaction`` is partial on the back-link."""
        with app.app_context():
            ddl = self._indexdef(db, "idx_journal_entries_transaction")
            assert ddl is not None, "idx_journal_entries_transaction missing"
            assert "transaction_id" in ddl
            assert "transaction_id IS NOT NULL" in ddl


class TestMigratedConstraints:
    """The partition CHECK and the two SET-NULL FKs exist as declared."""

    def test_account_or_category_check(self, app, db):
        """The exclusive-link CHECK is present with the model's predicate."""
        with app.app_context():
            ddl = db.session.execute(text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_ledger_accounts_account_or_category_null'"
            )).scalar()
            assert ddl is not None, "partition CHECK missing at HEAD"
            assert "account_id IS NULL" in ddl
            assert "category_id IS NULL" in ddl

    def test_fallback_shape_check(self, app, db):
        """The fallback-shape CHECK is present with the model's predicate."""
        with app.app_context():
            ddl = db.session.execute(text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_ledger_accounts_fallback_shape'"
            )).scalar()
            assert ddl is not None, "fallback-shape CHECK missing at HEAD"
            assert "is_fallback" in ddl
            assert "account_id IS NULL" in ddl
            assert "category_id IS NULL" in ddl

    def test_category_fk_is_set_null(self, app, db):
        """``fk_ledger_accounts_category_id`` is ON DELETE SET NULL.

        ``pg_constraint.confdeltype = 'n'`` is the SET NULL delete action;
        any other value (``'c'`` CASCADE, ``'r'`` RESTRICT, ``'a'`` NO
        ACTION) would corrupt the immutable-posting disposal contract.
        """
        with app.app_context():
            deltype = db.session.execute(text(
                "SELECT confdeltype FROM pg_constraint "
                "WHERE conname = 'fk_ledger_accounts_category_id'"
            )).scalar()
            assert deltype == "n", (
                "fk_ledger_accounts_category_id must be SET NULL (confdeltype "
                f"'n'); found {deltype!r}"
            )

    def test_transaction_fk_is_set_null(self, app, db):
        """``fk_journal_entries_transaction_id`` is ON DELETE SET NULL."""
        with app.app_context():
            deltype = db.session.execute(text(
                "SELECT confdeltype FROM pg_constraint "
                "WHERE conname = 'fk_journal_entries_transaction_id'"
            )).scalar()
            assert deltype == "n", (
                "fk_journal_entries_transaction_id must be SET NULL "
                f"(confdeltype 'n'); found {deltype!r}"
            )


class TestDowngradeReversible:
    """downgrade() is a real revert, not a bare pass.

    A source-level check (the executable round-trip is out of scope for the
    xdist worker -- see the module docstring) guards against a future edit
    silently dropping one of the reverts the upgrade's additions require,
    which would leave an orphaned column / index / constraint on a downgrade
    -- the bare-pass downgrade failure mode the coding standard forbids.
    """

    def test_downgrade_drops_every_added_object(self):
        """The downgrade source drops each object the upgrade adds.

        The value check is scoped to the text from ``def downgrade`` onward so
        an object is credited only if it is named in a drop, not merely echoed
        by the upgrade's earlier creates.
        """
        source = (_MIGRATIONS_DIR / _MIGRATION_FILENAME).read_text()
        downgrade_section = source[source.find("def downgrade"):]
        assert downgrade_section, "no downgrade() in the migration source"
        # Each column is dropped by name in a drop_column call -- matched
        # precisely (table + column) so 'is_fallback' appearing in the index
        # predicate text is not miscredited as the column drop.
        for table, column in (
            ("ledger_accounts", "category_id"),
            ("ledger_accounts", "is_fallback"),
            ("journal_entries", "transaction_id"),
        ):
            assert f"drop_column('{table}', '{column}'" in downgrade_section, (
                f"downgrade() never drops the {table}.{column} column"
            )
        # Indexes dropped.
        for index in (
            "uq_ledger_accounts_category",
            "uq_ledger_accounts_uncategorized",
            "idx_journal_entries_transaction",
        ):
            assert f"'{index}'" in downgrade_section, (
                f"downgrade() never drops the {index} index"
            )
        # Both CHECKs and the two FKs dropped.
        for constraint in (
            "ck_ledger_accounts_account_or_category_null",
            "ck_ledger_accounts_fallback_shape",
            "fk_ledger_accounts_category_id",
            "fk_journal_entries_transaction_id",
        ):
            assert f"'{constraint}'" in downgrade_section, (
                f"downgrade() never drops the {constraint} constraint"
            )
