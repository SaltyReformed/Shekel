"""Tests for the f5037400dc5e posting-ledger ref-tables migration.

Build-Order Step 2, Commit 1
(``docs/audits/balance_architecture/implementation_plan_posting_ledger_transfers.md``).
The migration creates the three ``ref`` lookup tables
(``ledger_account_classes``, ``posting_kinds``, ``posting_sources``) and
inline-seeds the Step-2 values.

The migration is already at HEAD when these tests run (the template
builder upgraded it base->head), so the per-worker DB shows the
post-migration state.  These tests therefore assert two things without
re-executing DDL in the worker:

  * the migration is correctly chained (revision / down_revision), and
  * the live migrated+seeded state is exactly the Step-2 contract (three
    tables, correct rows, correct ``is_debit_normal`` flags), and
  * the ``downgrade`` is not a bare pass -- it references every artefact
    the ``upgrade`` materialises.

A full executable upgrade -> downgrade -> upgrade round-trip belongs in
the Alembic-driven environment, not an in-test xdist worker: a
``DROP TABLE`` needs an ACCESS EXCLUSIVE lock that conflicts with the
ACCESS SHARE locks the framework's session-scoped ``ref_cache`` refresh
holds on these ref tables, so it dies on ``lock_timeout``.  This is the
same constraint that drove the ``loan_anchor_events`` migration test
(``tests/test_models/test_loan_anchor_backfill.py::TestDowngradeSmoke``)
to a source-level downgrade check.  The executable round-trip was run
manually against the prod-clone dev DB during development (downgrade
dropped all three tables and their owned sequences cleanly; re-upgrade
restored the 5/1/1 seeded rows).
"""
from __future__ import annotations

import importlib.util
import pathlib

from sqlalchemy import text

from app.enums import (
    LedgerAccountClassEnum,
    PostingKindEnum,
    PostingSourceEnum,
)


# ---------------------------------------------------------------------------
# Migration module loader -- importlib pattern from the C-43 / loan-anchor
# migration tests (migrations/versions has no __init__.py).
# ---------------------------------------------------------------------------


_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)

_MIGRATION_FILENAME = (
    "f5037400dc5e_add_posting_ledger_ref_tables_ledger_.py"
)


def _load_migration(filename):
    """Load an Alembic migration module by path via importlib."""
    path = _MIGRATIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MIGRATION = _load_migration(_MIGRATION_FILENAME)


class TestMigrationRevisionPair:
    """The migration chains off the recorded head."""

    def test_revision_pair(self):
        """revision / down_revision pin the migration into the chain."""
        assert _MIGRATION.revision == "f5037400dc5e"
        assert _MIGRATION.down_revision == "b483e2b8a6d2"


class TestMigratedAndSeededState:
    """The HEAD per-worker DB carries the exact Step-2 schema and seed.

    These assertions read the live migrated state the template build
    produced by running ``upgrade`` -- the upgrade+seed contract proven
    against the real migrated schema rather than a re-execution.
    """

    def test_all_three_tables_exist(self, app, db):
        """All three posting-ledger ref tables are present at HEAD."""
        with app.app_context():
            present = {
                row[0] for row in db.session.execute(text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'ref' AND table_name IN "
                    "('ledger_account_classes', 'posting_kinds', "
                    "'posting_sources')"
                )).fetchall()
            }
            assert present == {
                "ledger_account_classes",
                "posting_kinds",
                "posting_sources",
            }, present

    def test_ledger_account_classes_seeded_with_correct_flags(self, app, db):
        """The five classes are seeded with the correct is_debit_normal.

        Asset and Expense are debit-normal; Liability, Income, and Equity
        are credit-normal.  Read straight from the migrated table so the
        migration's inline seed is validated independently of the ORM /
        ref_cache layer.
        """
        with app.app_context():
            rows = dict(db.session.execute(text(
                "SELECT name, is_debit_normal "
                "FROM ref.ledger_account_classes"
            )).fetchall())
            assert rows == {
                "Asset": True,
                "Liability": False,
                "Income": False,
                "Expense": True,
                "Equity": False,
            }, rows

    def test_posting_kinds_and_sources_seeded(self, app, db):
        """Step 2 seeds exactly one ``transfer`` row in each kind/source."""
        with app.app_context():
            kinds = [
                row[0] for row in db.session.execute(text(
                    "SELECT name FROM ref.posting_kinds ORDER BY name"
                )).fetchall()
            ]
            sources = [
                row[0] for row in db.session.execute(text(
                    "SELECT name FROM ref.posting_sources ORDER BY name"
                )).fetchall()
            ]
            assert kinds == ["transfer"], kinds
            assert sources == ["transfer"], sources


class TestInlineSeedParity:
    """The migration's OWN inline seed covers every enum member.

    The inline seed is the load-bearing mechanism of this commit: it lets
    ``ref_cache.init()`` resolve the three new enums after a bare
    ``flask db upgrade``, BEFORE the entrypoint's ``seed_reference_data``
    runs.  ``TestMigratedAndSeededState`` above reads the template-built
    state, which runs the migration AND THEN ``seed_reference_data`` -- so
    a value omitted from the migration's inline seed alone (but still
    listed in ``app/ref_seeds.py``) would be backfilled there and pass
    undetected, silently breaking the bare-upgrade guarantee.  This
    source-level check isolates the migration's own seed so that omission
    cannot hide.  The quoted form (``'Asset'``) appears only in the
    inline-seed SQL, never in the prose docstring (which is unquoted), so
    a whole-file substring check is unambiguous.
    """

    def test_inline_seed_covers_every_enum_value(self):
        """Each enum value is quoted in the migration's own inline seed."""
        source = (_MIGRATIONS_DIR / _MIGRATION_FILENAME).read_text()
        for enum_cls in (
            LedgerAccountClassEnum, PostingKindEnum, PostingSourceEnum,
        ):
            for member in enum_cls:
                assert f"'{member.value}'" in source, (
                    f"{enum_cls.__name__}.{member.name} "
                    f"('{member.value}') is not seeded inline by the "
                    f"migration -- a bare `flask db upgrade` would leave "
                    f"ref_cache.init() unable to resolve it."
                )


class TestDowngradeReversible:
    """downgrade() is a real revert, not a bare pass.

    A source-level check (the executable round-trip is out of scope for
    the xdist worker -- see the module docstring) guards against a future
    edit silently re-routing the downgrade past one of the artefacts the
    upgrade materialises, which would leave a half-reverted schema -- the
    bare-pass downgrade failure mode the coding standard explicitly
    forbids.
    """

    def test_downgrade_drops_all_three_tables(self):
        """The downgrade source drops every table the upgrade creates."""
        source = (_MIGRATIONS_DIR / _MIGRATION_FILENAME).read_text()
        for table in (
            "ledger_account_classes",
            "posting_kinds",
            "posting_sources",
        ):
            assert (
                f'drop_table("{table}"' in source
                or f"drop_table('{table}'" in source
            ), f"downgrade() never drops ref.{table}"
