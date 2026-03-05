"""Add HYSA account type and account categories

Adds category column to ref.account_types for dashboard grouping,
inserts HYSA account type, and creates budget.hysa_params table
for HYSA-specific interest configuration.

Revision ID: f1a2b3c4d5e6
Revises: e5f6a7b8c9d0
Create Date: 2026-03-04

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f1a2b3c4d5e6"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade():
    # 1. Add category column to ref.account_types.
    op.add_column(
        "account_types",
        sa.Column("category", sa.String(20)),
        schema="ref",
    )

    # 2. Backfill existing types.
    op.execute("UPDATE ref.account_types SET category = 'asset' WHERE name = 'checking'")
    op.execute("UPDATE ref.account_types SET category = 'asset' WHERE name = 'savings'")

    # 3. Insert HYSA account type.
    op.execute("INSERT INTO ref.account_types (name, category) VALUES ('hysa', 'asset')")

    # 4. Create budget.hysa_params table.
    op.create_table(
        "hysa_params",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer,
            sa.ForeignKey("budget.accounts.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "apy",
            sa.Numeric(7, 5),
            nullable=False,
            server_default="0.04500",
        ),
        sa.Column(
            "compounding_frequency",
            sa.String(10),
            nullable=False,
            server_default="daily",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "compounding_frequency IN ('daily', 'monthly', 'quarterly')",
            name="ck_hysa_params_frequency",
        ),
        schema="budget",
    )
    op.create_index(
        "idx_hysa_params_account",
        "hysa_params",
        ["account_id"],
        schema="budget",
    )


def downgrade():
    op.drop_index("idx_hysa_params_account", table_name="hysa_params", schema="budget")
    op.drop_table("hysa_params", schema="budget")
    op.execute("DELETE FROM ref.account_types WHERE name = 'hysa'")
    op.drop_column("account_types", "category", schema="ref")
