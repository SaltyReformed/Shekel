"""a purchase is a ledger source of its own

Revision ID: b7c3d9e1f204
Revises: f2a94c7e1b60
Create Date: 2026-08-15 00:10:00.000000

Balance arc, plan step **X-f3b** (ruling **R-FM**, refined by **R-FR**; see
``docs/audits/balance_architecture/README.md`` section 4).

A purchase recorded against an envelope (``budget.transaction_entries``) has
never been a cash movement: it only shrank its envelope's reserved budget, and
the money left the book when the WHOLE envelope closed.  Measured on a
production clone 2026-08-14: entry 89 (``$12.79``) was taken by the bank on
08-12 and the owner asserted ``$2,193.69`` for that day, so the money was
already inside the declared balance -- and the envelope's close on 08-13 then
took the same ``$12.79`` out a second time, rendering the whole of 08-13
``$12.79`` low.

**R-FM**: a purchase that has cleared the bank is a cash posting, and its
envelope's close books only what its purchases did not.  This migration adds
the two things the ledger needs to say that:

  * **``budget.journal_entries.transaction_entry_id``** -- the concrete source
    row for a purchase-sourced entry, beside the existing ``transfer_id`` and
    ``transaction_id``.  ``ON DELETE SET NULL`` and nullable, exactly like its
    two siblings: the posted fact is immutable history and must survive a
    source delete with only the back-link cleared.  It is what lets the posted
    walk read each purchase's OWN posting day and its OWN clearing link
    (``transaction_entries.settled_on`` / ``reconciled_by_id``); grouping
    purchase legs under the parent's ``transaction_id`` instead would date them
    at the PARENT's settle day, which a still-projected envelope does not have
    at all.
  * **``ref.posting_sources`` gains ``purchase``** -- new reference values are
    data, never schema (the ``ref.posting_sources`` model docstring records
    this contract).

The partial index mirrors ``idx_journal_entries_transfer`` /
``idx_journal_entries_transaction`` byte-for-byte in shape: a purchase-sourced
entry is looked up by its source row for the per-purchase reconcile-to-target
filter, and entries of every other source kind carry NULL here and fall outside
it.

**Why the UPGRADE moves no posted leg.**  Nothing is backfilled, and it does
not need to be.  Both the parent's leg and each purchase's are
reconcile-to-target: ``posting_service.sync_transaction_postings`` reads back
every ``(pay period, entry date)`` key already in the ledger and emits one
balanced delta per key that differs.  So the first sync of an envelope after
this migration reverses the part of its cash leg its posted purchases now own
and posts those purchases at their own days, in one balanced pass, and a repeat
sync writes nothing.  ``scripts/init_database.py`` runs
``resync_all_cash_postings()`` on EVERY deploy of an existing database
immediately after the chain reaches head, so the move happens in the same
deploy; a developer running a bare ``flask db upgrade`` gets the column now and
the re-pointing at the next sync of each row.

**Why the DOWNGRADE deletes the purchase-sourced entries.**  The
``source_kind_id`` foreign key is ``ON DELETE RESTRICT``, so the ``purchase``
reference row cannot be removed while any entry still names it.  Deleting those
entries is also the CORRECT direction rather than merely the possible one: the
old code books an envelope's whole debit total on its close, so a purchase leg
left behind would be counted twice.  Each purchase entry is deleted whole --
both of its legs at once, through the ``ON DELETE CASCADE`` on
``account_postings.journal_entry_id`` -- so no entry is ever left unbalanced,
and the deferred ``ck_account_postings_balanced`` trigger does not fire on
DELETE.  What is left stale is the PARENT's leg, which is short by exactly what
its purchases had taken; the same deploy-time ``resync_all_cash_postings`` the
old image runs restores it in one pass.  **Measured on a production clone: the
gap is `$640.70` on Checking's linked ledger between the downgrade and that
resync**, so a bare ``flask db downgrade`` run OUTSIDE the deploy pipeline
leaves the ledger wrong until the hook is run by hand -- verified to converge
exactly, with the second pass returning ``(0, 0)``.

**Not audited on the ref side, audited on the ledger side.**
``ref.posting_sources`` is a read-only seed catalogue deliberately excluded
from ``AUDITED_TABLES``.  ``budget.journal_entries`` and
``budget.account_postings`` ARE audited, so the downgrade's deletes are
captured in ``system.audit_log`` by their triggers.

**Self-contained dependency policy.**  This migration imports nothing from
``app`` -- not models, not enums, not ``ref_cache``.  Every value is inline raw
SQL because migrations run at fragile bootstrap moments (the ref-cache layer is
itself initialising) and must survive aggressive refactors in app code.
"""
import sqlalchemy as sa
from alembic import op


# Revision identifiers, used by Alembic.
revision = 'b7c3d9e1f204'
down_revision = 'f2a94c7e1b60'
branch_labels = None
depends_on = None


# Inline seed SQL.  The ``name`` value MUST match ``PostingSourceEnum.PURCHASE``
# in ``app/enums.py`` exactly or ``ref_cache.init()`` raises at app start, and it
# MUST also match the list in ``app/ref_seeds.py``.  ``ON CONFLICT (name) DO
# NOTHING`` keeps it idempotent against a re-run and against the entrypoint's
# later reseed; the duplication between a migration and ``ref_seeds`` is the
# established project pattern (``f5037400dc5e`` / ``d1b22f59ba5b`` /
# ``e6b4a2d8c713``).
_SEED_PURCHASE_POSTING_SOURCE_SQL = (
    "INSERT INTO ref.posting_sources (name) VALUES ('purchase') "
    "ON CONFLICT (name) DO NOTHING"
)

# The downgrade's first act, and the only order the RESTRICT foreign key
# permits.  Whole ENTRIES, never single legs: ``account_postings`` cascades from
# its entry, so both sides of each balanced pair go together.
_DELETE_PURCHASE_SOURCED_ENTRIES_SQL = """
DELETE FROM budget.journal_entries
WHERE source_kind_id = (
    SELECT id FROM ref.posting_sources WHERE name = 'purchase'
)
"""

_DROP_PURCHASE_POSTING_SOURCE_SQL = (
    "DELETE FROM ref.posting_sources WHERE name = 'purchase'"
)


def upgrade():
    """Add the purchase source column, its index, and its reference value."""
    op.add_column(
        "journal_entries",
        sa.Column("transaction_entry_id", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.create_foreign_key(
        "fk_journal_entries_transaction_entry_id",
        "journal_entries",
        "transaction_entries",
        ["transaction_entry_id"],
        ["id"],
        source_schema="budget",
        referent_schema="budget",
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_journal_entries_transaction_entry",
        "journal_entries",
        ["transaction_entry_id"],
        unique=False,
        schema="budget",
        postgresql_where=sa.text("transaction_entry_id IS NOT NULL"),
    )
    op.execute(_SEED_PURCHASE_POSTING_SOURCE_SQL)


def downgrade():
    """Delete every purchase-sourced entry, then the column and the ref value."""
    op.execute(_DELETE_PURCHASE_SOURCED_ENTRIES_SQL)
    op.drop_index(
        "idx_journal_entries_transaction_entry",
        table_name="journal_entries",
        schema="budget",
        postgresql_where=sa.text("transaction_entry_id IS NOT NULL"),
    )
    op.drop_constraint(
        "fk_journal_entries_transaction_entry_id",
        "journal_entries",
        schema="budget",
        type_="foreignkey",
    )
    op.drop_column(
        "journal_entries", "transaction_entry_id", schema="budget",
    )
    op.execute(_DROP_PURCHASE_POSTING_SOURCE_SQL)
