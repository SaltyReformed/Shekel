"""rename hysa_params to interest_params

Revision ID: b4a6bb55f78b
Revises: 047bfed04987
Create Date: 2026-03-30 20:40:32.283738
"""
from alembic import op

# Revision identifiers, used by Alembic.
revision = 'b4a6bb55f78b'
down_revision = '047bfed04987'
branch_labels = None
depends_on = None


def upgrade():
    """Rename budget.hysa_params table and its indexes."""
    op.rename_table("hysa_params", "interest_params", schema="budget")
    # Rename the unique index on account_id (auto-generated name).
    op.execute(
        "ALTER INDEX IF EXISTS budget.hysa_params_account_id_key "
        "RENAME TO interest_params_account_id_key"
    )
    # Rename the check constraint on compounding_frequency.
    op.execute(
        "ALTER TABLE budget.interest_params "
        "RENAME CONSTRAINT ck_hysa_params_frequency "
        "TO ck_interest_params_frequency"
    )


def downgrade():
    """Revert table and index names."""
    op.execute(
        "ALTER TABLE budget.interest_params "
        "RENAME CONSTRAINT ck_interest_params_frequency "
        "TO ck_hysa_params_frequency"
    )
    op.execute(
        "ALTER INDEX IF EXISTS budget.interest_params_account_id_key "
        "RENAME TO hysa_params_account_id_key"
    )
    op.rename_table("interest_params", "hysa_params", schema="budget")
