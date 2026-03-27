"""add transfer_id to transactions and category_id to transfers

Revision ID: 772043eee094
Revises: efffcf647644
Create Date: 2026-03-25 22:18:00.000000
"""
from alembic import op
import sqlalchemy as sa

# Revision identifiers, used by Alembic.
revision = '772043eee094'
down_revision = 'efffcf647644'
branch_labels = None
depends_on = None


def upgrade():
    """Add transfer_id (nullable FK) to transactions, category_id to transfers and transfer_templates."""
    # -- budget.transactions: transfer_id --
    op.add_column(
        'transactions',
        sa.Column('transfer_id', sa.Integer(), nullable=True),
        schema='budget',
    )
    op.create_index(
        'idx_transactions_transfer',
        'transactions',
        ['transfer_id'],
        unique=False,
        schema='budget',
        postgresql_where=sa.text('transfer_id IS NOT NULL'),
    )
    op.create_foreign_key(
        'fk_transactions_transfer_id',
        'transactions', 'transfers',
        ['transfer_id'], ['id'],
        source_schema='budget',
        referent_schema='budget',
        ondelete='CASCADE',
    )

    # -- budget.transfers: category_id --
    op.add_column(
        'transfers',
        sa.Column('category_id', sa.Integer(), nullable=True),
        schema='budget',
    )
    op.create_foreign_key(
        'fk_transfers_category_id',
        'transfers', 'categories',
        ['category_id'], ['id'],
        source_schema='budget',
        referent_schema='budget',
    )

    # -- budget.transfer_templates: category_id --
    op.add_column(
        'transfer_templates',
        sa.Column('category_id', sa.Integer(), nullable=True),
        schema='budget',
    )
    op.create_foreign_key(
        'fk_transfer_templates_category_id',
        'transfer_templates', 'categories',
        ['category_id'], ['id'],
        source_schema='budget',
        referent_schema='budget',
    )


def downgrade():
    """Remove transfer_id from transactions, category_id from transfers and transfer_templates."""
    # -- budget.transfer_templates: category_id --
    op.drop_constraint(
        'fk_transfer_templates_category_id',
        'transfer_templates',
        schema='budget',
        type_='foreignkey',
    )
    op.drop_column('transfer_templates', 'category_id', schema='budget')

    # -- budget.transfers: category_id --
    op.drop_constraint(
        'fk_transfers_category_id',
        'transfers',
        schema='budget',
        type_='foreignkey',
    )
    op.drop_column('transfers', 'category_id', schema='budget')

    # -- budget.transactions: transfer_id --
    op.drop_constraint(
        'fk_transactions_transfer_id',
        'transactions',
        schema='budget',
        type_='foreignkey',
    )
    op.drop_index(
        'idx_transactions_transfer',
        table_name='transactions',
        schema='budget',
        postgresql_where=sa.text('transfer_id IS NOT NULL'),
    )
    op.drop_column('transactions', 'transfer_id', schema='budget')
