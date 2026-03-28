"""add boolean columns to ref.statuses and rename display names

Revision ID: e138e6f55bf0
Revises: 772043eee094
Create Date: 2026-03-27 22:25:18.865781

Adds three boolean columns to ref.statuses that capture the logical
groupings previously expressed via frozensets in Python code:

    is_settled          -- done/received/settled: amount already reflected
    is_immutable        -- done/received/credit/cancelled/settled: recurrence
                           engine must not overwrite
    excludes_from_balance -- credit/cancelled: contributes zero to balance

Then renames status display names to capitalized forms (e.g. "done" -> "Paid").

IMPORTANT: The boolean UPDATEs execute BEFORE the name RENAMEs because the
WHERE clauses match on the original lowercase names.

TransactionType names ("income", "expense") are NOT renamed here -- that
belongs to Commit #2 alongside the code changes that replace filter_by calls.
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = 'e138e6f55bf0'
down_revision = '772043eee094'
branch_labels = None
depends_on = None


def upgrade():
    """Add boolean columns, set values, then rename display names."""
    # --- Step 1: Add boolean columns with server_default=false -----------
    op.add_column(
        "statuses",
        sa.Column("is_settled", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        schema="ref",
    )
    op.add_column(
        "statuses",
        sa.Column("is_immutable", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        schema="ref",
    )
    op.add_column(
        "statuses",
        sa.Column("excludes_from_balance", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        schema="ref",
    )

    # --- Step 2: Set boolean values (BEFORE renaming names) --------------
    # is_settled: done, received, settled
    op.execute(
        "UPDATE ref.statuses SET is_settled = true "
        "WHERE name IN ('done', 'received', 'settled')"
    )
    # is_immutable: done, received, credit, cancelled, settled
    op.execute(
        "UPDATE ref.statuses SET is_immutable = true "
        "WHERE name IN ('done', 'received', 'credit', 'cancelled', 'settled')"
    )
    # excludes_from_balance: credit, cancelled
    op.execute(
        "UPDATE ref.statuses SET excludes_from_balance = true "
        "WHERE name IN ('credit', 'cancelled')"
    )

    # --- Step 3: Rename status display names (AFTER boolean updates) -----
    renames = [
        ("projected", "Projected"),
        ("done", "Paid"),
        ("received", "Received"),
        ("credit", "Credit"),
        ("cancelled", "Cancelled"),
        ("settled", "Settled"),
    ]
    for old_name, new_name in renames:
        op.execute(
            f"UPDATE ref.statuses SET name = '{new_name}' "
            f"WHERE name = '{old_name}'"
        )


def downgrade():
    """Revert display names then drop boolean columns."""
    # --- Step 1: Revert status display names -----------------------------
    renames = [
        ("Projected", "projected"),
        ("Paid", "done"),
        ("Received", "received"),
        ("Credit", "credit"),
        ("Cancelled", "cancelled"),
        ("Settled", "settled"),
    ]
    for old_name, new_name in renames:
        op.execute(
            f"UPDATE ref.statuses SET name = '{new_name}' "
            f"WHERE name = '{old_name}'"
        )

    # --- Step 2: Drop boolean columns -----------------------------------
    op.drop_column("statuses", "excludes_from_balance", schema="ref")
    op.drop_column("statuses", "is_immutable", schema="ref")
    op.drop_column("statuses", "is_settled", schema="ref")
