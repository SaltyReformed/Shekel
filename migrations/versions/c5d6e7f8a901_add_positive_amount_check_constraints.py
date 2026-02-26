"""Add positive amount CHECK constraints and baseline scenario unique index

Revision ID: c5d6e7f8a901
Revises: b4c7d8e9f012
Create Date: 2026-02-25 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# Revision identifiers, used by Alembic.
revision = 'c5d6e7f8a901'
down_revision = 'b4c7d8e9f012'
branch_labels = None
depends_on = None


def upgrade():
    """Add CHECK constraints for non-negative amounts and partial unique index."""

    op.create_check_constraint(
        'ck_transactions_positive_amount',
        'transactions',
        'estimated_amount >= 0',
        schema='budget',
    )

    op.create_check_constraint(
        'ck_transactions_positive_actual',
        'transactions',
        'actual_amount IS NULL OR actual_amount >= 0',
        schema='budget',
    )

    op.create_index(
        'uq_scenarios_one_baseline',
        'scenarios',
        ['user_id'],
        unique=True,
        schema='budget',
        postgresql_where=sa.text('is_baseline = TRUE'),
    )


def downgrade():
    """Remove CHECK constraints and partial unique index."""

    op.drop_index(
        'uq_scenarios_one_baseline',
        table_name='scenarios',
        schema='budget',
    )

    op.drop_constraint(
        'ck_transactions_positive_actual',
        'transactions',
        schema='budget',
        type_='check',
    )

    op.drop_constraint(
        'ck_transactions_positive_amount',
        'transactions',
        schema='budget',
        type_='check',
    )
