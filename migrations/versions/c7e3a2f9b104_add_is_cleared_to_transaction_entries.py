"""add is_cleared to transaction_entries

Adds the is_cleared boolean column to budget.transaction_entries so the
balance calculator can distinguish entries that are already reflected in
the current checking anchor balance from entries that have not yet been
reconciled.  See docs/implementation_plan_section9_fix.md for the bug
this addresses (double-counting of debit entries after a checking anchor
true-up).

Backfill policy: for entries whose parent transaction is Projected and
whose entry_date <= CURRENT_DATE, mark is_cleared = TRUE.  This keeps
balances stable on the day the migration runs, matching the assumption
that the current anchor already reflects all past-dated purchases on
in-flight projected transactions.  Future-dated entries and entries on
already-settled transactions remain FALSE.

Revision ID: c7e3a2f9b104
Revises: b961beb0edf6
Create Date: 2026-04-13 00:00:00.000000
"""
from alembic import op


# Revision identifiers, used by Alembic.
revision = 'c7e3a2f9b104'
down_revision = 'b961beb0edf6'
branch_labels = None
depends_on = None


def upgrade():
    """Add is_cleared column with backfill for past-dated projected entries."""
    # Add the column with FALSE default so existing rows get a non-NULL value.
    op.execute("""
        ALTER TABLE budget.transaction_entries
            ADD COLUMN is_cleared BOOLEAN NOT NULL DEFAULT FALSE
    """)

    # Backfill: mark past-dated entries on projected parents as cleared.
    # These are the entries whose debits are assumed already reflected in
    # the user's current anchor balance, so they should not double-count
    # once the new balance calculator formula takes effect.  Entries on
    # non-projected (done/received/cancelled/credit/settled) parents are
    # excluded from the balance calculator formula anyway, so their
    # is_cleared value does not matter -- leave them FALSE.
    op.execute("""
        UPDATE budget.transaction_entries e
        SET is_cleared = TRUE
        FROM budget.transactions t, ref.statuses s
        WHERE e.transaction_id = t.id
          AND t.status_id = s.id
          AND s.name = 'Projected'
          AND e.entry_date <= CURRENT_DATE
    """)


def downgrade():
    """Drop the is_cleared column."""
    op.execute("""
        ALTER TABLE budget.transaction_entries
            DROP COLUMN IF EXISTS is_cleared
    """)
