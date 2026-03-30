"""add has_interest is_pretax is_liquid to account_types

Revision ID: a45b88e8fa2e
Revises: c67773dc7375
Create Date: 2026-03-29 23:14:29.102298
"""
from alembic import op
import sqlalchemy as sa

# Revision identifiers, used by Alembic.
revision = 'a45b88e8fa2e'
down_revision = 'c67773dc7375'
branch_labels = None
depends_on = None


def upgrade():
    """Add has_interest, is_pretax, and is_liquid boolean columns.

    Also sets HSA has_parameters=True (was False) so it participates in
    the parameterised-account dispatch logic alongside its new
    has_interest=True flag.
    """
    # ── Schema changes ───────────────────────────────────────────────
    op.add_column(
        "account_types",
        sa.Column("has_interest", sa.Boolean(), nullable=False, server_default="false"),
        schema="ref",
    )
    op.add_column(
        "account_types",
        sa.Column("is_pretax", sa.Boolean(), nullable=False, server_default="false"),
        schema="ref",
    )
    op.add_column(
        "account_types",
        sa.Column("is_liquid", sa.Boolean(), nullable=False, server_default="false"),
        schema="ref",
    )

    # ── Data migration ───────────────────────────────────────────────
    op.execute(
        "UPDATE ref.account_types SET has_interest = true "
        "WHERE name IN ('HYSA', 'HSA')"
    )
    op.execute(
        "UPDATE ref.account_types SET has_parameters = true "
        "WHERE name = 'HSA'"
    )
    op.execute(
        "UPDATE ref.account_types SET is_pretax = true "
        "WHERE name IN ('401(k)', 'Traditional IRA')"
    )
    op.execute(
        "UPDATE ref.account_types SET is_liquid = true "
        "WHERE name IN ('Checking', 'Savings', 'HYSA', 'Money Market')"
    )


def downgrade():
    """Remove the three boolean columns and revert HSA has_parameters."""
    op.execute(
        "UPDATE ref.account_types SET has_parameters = false "
        "WHERE name = 'HSA'"
    )
    op.drop_column("account_types", "is_liquid", schema="ref")
    op.drop_column("account_types", "is_pretax", schema="ref")
    op.drop_column("account_types", "has_interest", schema="ref")
