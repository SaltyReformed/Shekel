"""add account_id column to transactions

Revision ID: efffcf647644
Revises: 01214a4ff394
Create Date: 2026-03-25 20:31:32.786091
"""
from alembic import op
import sqlalchemy as sa

# Revision identifiers, used by Alembic.
revision = 'efffcf647644'
down_revision = '01214a4ff394'
branch_labels = None
depends_on = None


def upgrade():
    """Add account_id NOT NULL FK column and index to budget.transactions."""
    op.add_column(
        'transactions',
        sa.Column('account_id', sa.Integer(), nullable=False),
        schema='budget',
    )
    op.create_index(
        'idx_transactions_account',
        'transactions',
        ['account_id'],
        unique=False,
        schema='budget',
    )
    op.create_foreign_key(
        'fk_transactions_account_id',
        'transactions', 'accounts',
        ['account_id'], ['id'],
        source_schema='budget',
        referent_schema='budget',
    )


def downgrade():
    """Remove account_id column and its index from budget.transactions."""
    op.drop_constraint(
        'fk_transactions_account_id',
        'transactions',
        schema='budget',
        type_='foreignkey',
    )
    op.drop_index(
        'idx_transactions_account',
        table_name='transactions',
        schema='budget',
    )
    op.drop_column('transactions', 'account_id', schema='budget')
