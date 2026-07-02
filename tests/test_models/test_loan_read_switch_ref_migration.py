"""Tests for the d1b22f59ba5b loan read-switch ref-values migration.

Loan read switch, Commit 1 (the deferred second half of Build-Order Step 4;
``docs/audits/balance_architecture/implementation_plan_loan_read_switch.md``).
The migration adds reference VALUES (not tables) to three ``ref`` lookup
tables Steps 2-4 created: the ``opening`` / ``trueup`` kinds to
``ref.posting_kinds``, the ``loan_opening`` / ``loan_trueup`` sources to
``ref.posting_sources``, and the ``equity_opening`` kind to
``ref.ledger_account_kinds``.

The migration is already at HEAD when these tests run (the template builder
upgraded it base->head), so the per-worker DB shows the post-migration
state.  These tests assert, without re-executing DML in the worker:

  * the migration is correctly chained (revision / down_revision);
  * this migration's own contribution is present in the live tables
    (``opening`` / ``trueup`` kinds, ``loan_opening`` / ``loan_trueup``
    sources, ``equity_opening`` kind) -- membership, not the exact row set,
    since Steps 2-4 also populate these tables.  The cumulative inline-seed
    coverage for every enum member lives in ``test_posting_ref_seed_parity.py``
    and the exact enum<->DB-row parity in ``tests/test_ref_cache.py``;
  * the ``downgrade`` is not a bare pass -- it deletes exactly the five rows
    the ``upgrade`` adds across the three tables.

A full executable upgrade -> downgrade -> upgrade round-trip belongs in the
Alembic-driven environment, not an in-test xdist worker.  Even though this
migration's downgrade is a row ``DELETE`` (not a ``DROP TABLE``, so it needs
no ACCESS EXCLUSIVE lock), mutating these ``ref`` rows inside a worker would
desync the framework's session-scoped ``ref_cache`` -- it is initialised once
per session from these exact rows, and every other test in the worker reads
the cached IDs -- so the source-level downgrade check is the safe analogue of
the one used for f5037400dc5e, 97bc03c2aa4c, and the loan-payment migration
(f8e025a8be41).  The executable round-trip was run manually against the
prod-clone dev DB during development (downgrade removed the five rows;
re-upgrade restored them identically).
"""
from __future__ import annotations

import importlib.util
import pathlib

from sqlalchemy import text


# ---------------------------------------------------------------------------
# Migration module loader -- importlib pattern from the 97bc03c2aa4c /
# f8e025a8be41 migration tests (migrations/versions has no __init__.py).
# ---------------------------------------------------------------------------


_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)

_MIGRATION_FILENAME = (
    "d1b22f59ba5b_add_loan_opening_trueup_posting_ref_.py"
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
    """The migration chains off the Step-4 backfill head."""

    def test_revision_pair(self):
        """revision / down_revision pin the migration into the chain."""
        assert _MIGRATION.revision == "d1b22f59ba5b"
        assert _MIGRATION.down_revision == "e2a9f1c7b4d6"


class TestMigratedState:
    """The HEAD per-worker DB carries this migration's new reference rows.

    Reads the live migrated state the template build produced -- the upgrade
    contract proven against the real migrated schema rather than a
    re-execution.  Asserts membership (this migration's own contribution),
    not the exact row set, since Steps 2-4 also populate these tables.
    """

    def test_loan_genesis_posting_kinds_present(self, app, db):
        """``opening`` and ``trueup`` kinds are seeded at HEAD."""
        with app.app_context():
            kinds = {
                row[0] for row in db.session.execute(text(
                    "SELECT name FROM ref.posting_kinds"
                )).fetchall()
            }
            assert {"opening", "trueup"} <= kinds, kinds

    def test_loan_genesis_posting_sources_present(self, app, db):
        """``loan_opening`` and ``loan_trueup`` sources are seeded at HEAD."""
        with app.app_context():
            sources = {
                row[0] for row in db.session.execute(text(
                    "SELECT name FROM ref.posting_sources"
                )).fetchall()
            }
            assert {"loan_opening", "loan_trueup"} <= sources, sources

    def test_equity_opening_ledger_kind_present(self, app, db):
        """The ``equity_opening`` ledger-account kind is seeded at HEAD."""
        with app.app_context():
            kinds = {
                row[0] for row in db.session.execute(text(
                    "SELECT name FROM ref.ledger_account_kinds"
                )).fetchall()
            }
            assert "equity_opening" in kinds, kinds


class TestDowngradeReversible:
    """downgrade() is a real revert, not a bare pass.

    A source-level check (the executable round-trip is out of scope for the
    xdist worker -- see the module docstring) guards against a future edit
    silently dropping one of the deletes the upgrade's five inserts require,
    which would leave orphaned reference rows on a downgrade -- the bare-pass
    downgrade failure mode the coding standard forbids.
    """

    def test_downgrade_deletes_every_added_row(self):
        """downgrade() names AND executes a delete for every added row.

        Two layers, so the check fails if EITHER the delete SQL names the
        wrong rows OR ``downgrade()`` stops executing it:

          * **value-level.**  The ``DELETE FROM`` constants name exactly the
            five rows the upgrade adds.  Scoped to the text from the first
            ``DELETE FROM`` onward (the ``_DROP_*`` constants follow the
            upgrade's ``_SEED_*`` INSERT constants in the module) so a value is
            credited only if it is named in a DELETE, not merely echoed by an
            earlier INSERT.
          * **execution-anchored.**  ``downgrade()``'s own body calls
            ``op.execute`` on all three ``_DROP_*`` constants.  Without this, a
            future edit that kept the constants but deleted the
            ``op.execute(...)`` lines from ``downgrade()`` would leave the five
            rows un-deleted yet still satisfy the value-level check (the
            constants would still carry the literals).  ``op.execute(_DROP_...)``
            appears only in the ``downgrade()`` body, never in the constant
            definitions, so this is a precise anchor.
        """
        source = (_MIGRATIONS_DIR / _MIGRATION_FILENAME).read_text()

        # Value-level: the delete constants name every added row.
        assert "DELETE FROM ref.posting_kinds" in source, (
            "downgrade() never deletes from ref.posting_kinds"
        )
        assert "DELETE FROM ref.posting_sources" in source, (
            "downgrade() never deletes from ref.posting_sources"
        )
        assert "DELETE FROM ref.ledger_account_kinds" in source, (
            "downgrade() never deletes from ref.ledger_account_kinds"
        )
        delete_section = source[source.find("DELETE FROM"):]
        for value in ("opening", "trueup", "loan_opening", "loan_trueup",
                      "equity_opening"):
            assert f"'{value}'" in delete_section, (
                f"downgrade() never names the '{value}' row to delete"
            )

        # Execution-anchored: downgrade() actually runs all three deletes -- a
        # bare-pass or a constants-kept-but-unexecuted edit both fail here.
        downgrade_body = source[source.find("def downgrade"):]
        for constant in (
            "op.execute(_DROP_LOAN_GENESIS_POSTING_KINDS_SQL)",
            "op.execute(_DROP_LOAN_GENESIS_POSTING_SOURCES_SQL)",
            "op.execute(_DROP_EQUITY_OPENING_LEDGER_KIND_SQL)",
        ):
            assert constant in downgrade_body, (
                f"downgrade() never executes {constant}"
            )
