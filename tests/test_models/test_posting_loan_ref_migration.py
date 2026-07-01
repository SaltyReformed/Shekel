"""Tests for the f8e025a8be41 loan-payment ref migration.

Build-Order Step 4, Commit 1
(``docs/audits/balance_architecture/implementation_plan_posting_ledger_loan_payments.md``).
The migration adds reference VALUES to two ``ref`` lookup tables Step 2/3
created -- the ``principal`` / ``interest`` / ``escrow`` / ``refund`` kinds to
``ref.posting_kinds`` and the ``loan_payment`` source to
``ref.posting_sources`` -- and CREATES one new lookup table,
``ref.ledger_account_kinds`` (the explicit row-kind discriminator for
``budget.ledger_accounts``), seeding its seven kinds.

The migration is already at HEAD when these tests run (the template builder
upgraded it base->head), so the per-worker DB shows the post-migration
state.  These tests assert, without re-executing DDL/DML in the worker:

  * the migration is correctly chained (revision / down_revision);
  * ``ref.ledger_account_kinds`` exists and carries EXACTLY its seven kinds
    (this migration is the sole, permanent producer of that table, so the
    exact row set is pinned -- unlike the shared ``posting_kinds`` /
    ``posting_sources`` tables);
  * this migration's own contributions to the shared tables are present
    (the four loan kinds, the ``loan_payment`` source) -- membership, not
    the exact row set, since Steps 2/3 also populate them and later steps
    add more.  The cumulative inline-seed coverage for every enum member
    lives in ``test_posting_ref_seed_parity.py``;
  * the ``downgrade`` is not a bare pass -- it drops the new table and
    deletes exactly the five rows the ``upgrade`` adds to the shared tables.

A full executable upgrade -> downgrade -> upgrade round-trip belongs in the
Alembic-driven environment, not an in-test xdist worker: the ``DROP TABLE``
in the downgrade needs an ACCESS EXCLUSIVE lock that conflicts with the
ACCESS SHARE locks the framework's session-scoped ``ref_cache`` refresh
holds on these ref tables, and mutating the shared ``ref`` rows inside a
worker would desync that cache for every other test in the worker.  This is
the same constraint that drove the f5037400dc5e and 97bc03c2aa4c migration
tests to a source-level downgrade check.  The executable round-trip was run
manually against the prod-clone dev DB during development (downgrade dropped
``ref.ledger_account_kinds`` and removed the five shared rows; re-upgrade
restored all of them identically).
"""
from __future__ import annotations

import importlib.util
import pathlib

from sqlalchemy import text


# ---------------------------------------------------------------------------
# Migration module loader -- importlib pattern from the f5037400dc5e /
# 97bc03c2aa4c migration tests (migrations/versions has no __init__.py).
# ---------------------------------------------------------------------------


_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)

_MIGRATION_FILENAME = (
    "f8e025a8be41_add_loan_payment_posting_kinds_and_.py"
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
    """The migration chains off the Step-3 backfill head."""

    def test_revision_pair(self):
        """revision / down_revision pin the migration into the chain."""
        assert _MIGRATION.revision == "f8e025a8be41"
        assert _MIGRATION.down_revision == "7d63529e4300"


class TestMigratedState:
    """The HEAD per-worker DB carries this migration's new table and rows.

    Reads the live migrated state the template build produced -- the upgrade
    contract proven against the real migrated schema rather than a
    re-execution.
    """

    def test_ledger_account_kinds_seeded_exactly(self, app, db):
        """``ref.ledger_account_kinds`` carries exactly its seven kinds.

        f8e025a8be41 is the sole, permanent producer of this table (the four
        kinds the chart already uses plus the three per-loan accounts), so
        the exact row set is pinned -- a future stray INSERT or a dropped
        seed value surfaces here.
        """
        with app.app_context():
            kinds = {
                row[0] for row in db.session.execute(text(
                    "SELECT name FROM ref.ledger_account_kinds"
                )).fetchall()
            }
            assert kinds == {
                "linked",
                "category",
                "fallback",
                "orphan",
                "loan_interest",
                "loan_escrow",
                "loan_refund",
            }, kinds

    def test_loan_posting_kinds_present(self, app, db):
        """The four loan-correction kinds are seeded at HEAD."""
        with app.app_context():
            kinds = {
                row[0] for row in db.session.execute(text(
                    "SELECT name FROM ref.posting_kinds"
                )).fetchall()
            }
            assert {"principal", "interest", "escrow", "refund"} <= kinds, kinds

    def test_loan_payment_posting_source_present(self, app, db):
        """The ``loan_payment`` source is seeded at HEAD."""
        with app.app_context():
            sources = {
                row[0] for row in db.session.execute(text(
                    "SELECT name FROM ref.posting_sources"
                )).fetchall()
            }
            assert "loan_payment" in sources, sources


class TestDowngradeReversible:
    """downgrade() is a real revert, not a bare pass.

    A source-level check (the executable round-trip is out of scope for the
    xdist worker -- see the module docstring) guards against a future edit
    silently dropping the table-drop or one of the row-deletes the upgrade's
    inserts require, which would leave orphaned reference rows or a stranded
    table on a downgrade -- the bare-pass downgrade failure mode the coding
    standard forbids.
    """

    def test_downgrade_drops_table_and_deletes_added_rows(self):
        """downgrade() drops the new table AND executes both row-delete constants.

        Two layers, so the check fails if EITHER the delete SQL names the
        wrong rows OR ``downgrade()`` stops executing it:

          * **value-level.**  The ``DELETE FROM`` constants name exactly the
            five shared rows the upgrade adds.  Scoped to the text from the
            first ``DELETE FROM`` onward (the ``_DROP_*`` constants follow the
            upgrade's ``_SEED_*`` INSERT constants in the module) so a value is
            credited only if it is named in a DELETE, not merely echoed by an
            earlier INSERT.
          * **execution-anchored.**  ``downgrade()``'s own body drops
            ``ref.ledger_account_kinds`` and calls ``op.execute`` on BOTH
            ``_DROP_*`` constants.  Without this, a future edit that kept the
            constants but deleted the ``op.execute(...)`` lines from
            ``downgrade()`` would leave the five rows un-deleted yet still
            satisfy the value-level check (the constants would still carry the
            literals).  ``op.execute(_DROP_...)`` appears only in the
            ``downgrade()`` body, never in the constant definitions, so this is
            a precise anchor.
        """
        source = (_MIGRATIONS_DIR / _MIGRATION_FILENAME).read_text()

        # Value-level: the delete constants name every added shared row.
        delete_section = source[source.find("DELETE FROM"):]
        assert "DELETE FROM ref.posting_kinds" in delete_section, (
            "the downgrade never deletes from ref.posting_kinds"
        )
        assert "DELETE FROM ref.posting_sources" in delete_section, (
            "the downgrade never deletes from ref.posting_sources"
        )
        for value in ("principal", "interest", "escrow", "refund",
                      "loan_payment"):
            assert f"'{value}'" in delete_section, (
                f"the downgrade never names the '{value}' row to delete"
            )

        # Execution-anchored: downgrade() actually runs both deletes and the
        # table drop -- a bare-pass or a constants-kept-but-unexecuted edit
        # both fail here.
        downgrade_body = source[source.find("def downgrade"):]
        assert (
            'drop_table("ledger_account_kinds"' in downgrade_body
            or "drop_table('ledger_account_kinds'" in downgrade_body
        ), "downgrade() never drops ref.ledger_account_kinds"
        assert "op.execute(_DROP_LOAN_POSTING_KINDS_SQL)" in downgrade_body, (
            "downgrade() never executes _DROP_LOAN_POSTING_KINDS_SQL"
        )
        assert (
            "op.execute(_DROP_LOAN_PAYMENT_POSTING_SOURCE_SQL)"
            in downgrade_body
        ), "downgrade() never executes _DROP_LOAN_PAYMENT_POSTING_SOURCE_SQL"
