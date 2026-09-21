"""Tests for the c7d1e9a4b2f8 shape-C data-boundary migration.

Plan step ``balance:X-bi-6-3`` (rulings **R-BAL45**, **R-BAL98**, **R-BAL99**,
**R-BAL101**).  The migration is the step's DATA BOUNDARY: it generalises the
chart's bucket flag (``is_fallback`` -> ``is_owner_bucket``), re-keys the
bucket singleton onto ``(user_id, class_id, kind_id)``, renames the shape
CHECK with the column, and seeds the ``transit`` chart kind and the
``transfer_movement`` posting source.  It re-books NO posting: the deploy's
cash resync does that through the go-forward writer (R-BAL98), graded in
``test_posting_service.py::TestTheDeployResyncReBooksTheLegacyShape``.

The migration is already at HEAD when these tests run (the template builder
upgraded it base->head), so the per-worker DB shows the post-migration
schema.  These tests assert, without re-executing DDL in the worker:

  * the migration is correctly chained (revision / down_revision -- off
    ``credit_card:CC-5-1``'s ``9900b309f0b0``, the head production carried
    when this step's release was cut);
  * the two reference rows exist and resolve through ``ref_cache`` to the
    enum members the writer and the chart resolver name;
  * the ``downgrade`` tears down in the only order the balanced-entry
    invariant and the RESTRICT keys permit -- the ``transfer_movement``
    entries WHOLE before the ``transit`` chart rows, the chart rows before
    the reference rows -- and renames every object back to its
    ``45f10b870c8b``-era name and key.

The renamed column, index and CHECK are asserted at HEAD by
``test_posting_cash_schema_migration.py`` (the migration that added them,
re-expressed for the rename).  The executable upgrade -> downgrade -> upgrade
round-trip was run during development against the prod-clone
``shekel_xbi63`` (2026-09-21): the ledger's 1,277 entries and 0.00 trial
balance untouched in both directions, the two reference rows seeded and
removed, the index re-keyed and restored.
"""
from __future__ import annotations

import pathlib
from decimal import Decimal

from sqlalchemy import text

from app import ref_cache
from app.enums import LedgerAccountKindEnum, PostingSourceEnum
from app.extensions import db as _db
from tests._test_helpers import (
    create_account_of_type,
    create_settled_transfer,
    load_migration_module,
)


_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)
_MIGRATION_FILENAME = "c7d1e9a4b2f8_the_ledger_takes_shape_c.py"
_MIGRATION = load_migration_module(_MIGRATION_FILENAME)


class TestMigrationRevisionPair:
    """The migration chains off CC-5-1's head."""

    def test_revision_pair(self):
        """revision / down_revision pin the migration into the chain."""
        assert _MIGRATION.revision == "c7d1e9a4b2f8"
        assert _MIGRATION.down_revision == "9900b309f0b0"


class TestSeededReferenceRows:
    """The two reference rows exist at HEAD and resolve to their enum members."""

    def test_transit_kind_is_seeded_and_resolves(self, app, db):
        """``ref.ledger_account_kinds`` holds ``transit`` and the cache resolves it."""
        with app.app_context():
            row_id = db.session.execute(text(
                "SELECT id FROM ref.ledger_account_kinds WHERE name = 'transit'"
            )).scalar()
            assert row_id is not None, "transit kind missing at HEAD"
            assert ref_cache.ledger_account_kind_id(
                LedgerAccountKindEnum.TRANSIT,
            ) == row_id

    def test_transfer_movement_source_is_seeded_and_resolves(self, app, db):
        """``ref.posting_sources`` holds ``transfer_movement`` and the cache resolves it."""
        with app.app_context():
            row_id = db.session.execute(text(
                "SELECT id FROM ref.posting_sources "
                "WHERE name = 'transfer_movement'"
            )).scalar()
            assert row_id is not None, "transfer_movement source missing at HEAD"
            assert ref_cache.posting_source_id(
                PostingSourceEnum.TRANSFER_MOVEMENT,
            ) == row_id

    def test_the_seed_sql_names_the_enum_values(self):
        """The inline seed SQL spells exactly the enum ``.value`` strings."""
        assert f"('{LedgerAccountKindEnum.TRANSIT.value}')" in (
            _MIGRATION._SEED_TRANSIT_KIND_SQL
        )
        assert f"('{PostingSourceEnum.TRANSFER_MOVEMENT.value}')" in (
            _MIGRATION._SEED_TRANSFER_MOVEMENT_SOURCE_SQL
        )


class TestDowngradeSource:
    """The downgrade tears down in dependency order and restores every name."""

    def _downgrade_section(self):
        source = (_MIGRATIONS_DIR / _MIGRATION_FILENAME).read_text()
        section = source[source.find("def downgrade"):]
        assert section, "no downgrade() in the migration source"
        return section

    def test_entries_go_before_chart_rows_before_reference_rows(self):
        """The statements run in the only order the invariants permit.

        Deleting the transit chart row first would cascade ONE leg of every
        per-movement entry and leave the real-account leg unbalanced; deleting
        a reference row while a chart row or an entry still carries it trips
        the RESTRICT key.  The order is asserted by position in the source.
        """
        section = self._downgrade_section()
        positions = [
            section.find(name) for name in (
                "_DELETE_TRANSFER_MOVEMENT_ENTRIES_SQL",
                "_DELETE_TRANSIT_CHART_ROWS_SQL",
                "_DROP_TRANSFER_MOVEMENT_SOURCE_SQL",
                "_DROP_TRANSIT_KIND_SQL",
            )
        ]
        assert all(p >= 0 for p in positions), positions
        assert positions == sorted(positions), positions
        # The entries go WHOLE: a DELETE on journal_entries, never on legs.
        assert "DELETE FROM budget.journal_entries" in (
            _MIGRATION._DELETE_TRANSFER_MOVEMENT_ENTRIES_SQL
        )
        assert "account_postings" not in (
            _MIGRATION._DELETE_TRANSFER_MOVEMENT_ENTRIES_SQL
        )

    def test_every_renamed_object_is_restored(self):
        """The downgrade names each HEAD object it drops and each old name it restores."""
        section = self._downgrade_section()
        for head_name, old_name in (
            ("uq_ledger_accounts_owner_bucket", "uq_ledger_accounts_uncategorized"),
            ("ck_ledger_accounts_owner_bucket_shape", "ck_ledger_accounts_fallback_shape"),
            ("is_owner_bucket", "is_fallback"),
        ):
            assert head_name in section, f"downgrade() never touches {head_name}"
            assert old_name in section, f"downgrade() never restores {old_name}"
        # The restored index carries the OLD key and predicate exactly.
        assert '["user_id", "class_id"]' in section
        assert 'sa.text("is_fallback")' in section


class TestDowngradeExecutes:
    """The downgrade's DML runs against a posted ledger in ``downgrade()``'s order.

    The pattern ``test_trueup_counter_ref_migration._run_downgrade`` set: the
    migration's data statements executed against the test's OWN database (one
    clone per item, so DML here touches nothing shared) and graded by counts
    -- the ``transfer_movement`` entries gone WHOLE, the ``transit`` chart
    rows gone, the two reference rows gone, every other source's entries
    untouched, no posting left without its entry.  The DDL half (the index
    re-key, the renames) is not executed here: it would alter the schema
    under the ORM for the rest of the item, and its source is graded by
    :class:`TestDowngradeSource`; the full round-trip ran on the clone.
    """

    def test_dml_tears_the_transit_ledger_down_whole(
        self, app, db, seed_user,
    ):
        """One settled transfer: two entries, a transit row, two ref rows -> none.

        Arithmetic: a $100 Checking -> Savings settle posts two
        ``transfer_movement`` entries (four legs) and mints the owner's
        transit row.  After the four DML statements: 0 such entries, 0 such
        legs, 0 transit rows, neither reference row -- and the account
        openings' entries (the ``account_opening`` / ``account_trueup``
        sources the fixture accounts post) exactly as many as before.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, _db.session, "Savings", "Downgrade Savings",
            )
            _db.session.commit()
            create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()

            def _count(sql):
                return db.session.execute(text(sql)).scalar()
            movement_entries = (
                "SELECT count(*) FROM budget.journal_entries je "
                "JOIN ref.posting_sources ps ON ps.id = je.source_kind_id "
                "WHERE ps.name = 'transfer_movement'"
            )
            other_entries = (
                "SELECT count(*) FROM budget.journal_entries je "
                "JOIN ref.posting_sources ps ON ps.id = je.source_kind_id "
                "WHERE ps.name <> 'transfer_movement'"
            )
            transit_rows = (
                "SELECT count(*) FROM budget.ledger_accounts la "
                "JOIN ref.ledger_account_kinds k ON k.id = la.kind_id "
                "WHERE k.name = 'transit'"
            )
            orphan_legs = (
                "SELECT count(*) FROM budget.account_postings p "
                "LEFT JOIN budget.journal_entries je ON je.id = p.journal_entry_id "
                "WHERE je.id IS NULL"
            )
            assert _count(movement_entries) == 2
            assert _count(transit_rows) == 1
            others_before = _count(other_entries)

            for statement in (
                _MIGRATION._DELETE_TRANSFER_MOVEMENT_ENTRIES_SQL,
                _MIGRATION._DELETE_TRANSIT_CHART_ROWS_SQL,
                _MIGRATION._DROP_TRANSFER_MOVEMENT_SOURCE_SQL,
                _MIGRATION._DROP_TRANSIT_KIND_SQL,
            ):
                db.session.execute(text(statement))
            db.session.flush()

            assert _count(movement_entries) == 0
            assert _count(transit_rows) == 0
            assert _count(other_entries) == others_before
            assert _count(orphan_legs) == 0
            assert _count(
                "SELECT count(*) FROM ref.ledger_account_kinds "
                "WHERE name = 'transit'"
            ) == 0
            assert _count(
                "SELECT count(*) FROM ref.posting_sources "
                "WHERE name = 'transfer_movement'"
            ) == 0
            db.session.rollback()
