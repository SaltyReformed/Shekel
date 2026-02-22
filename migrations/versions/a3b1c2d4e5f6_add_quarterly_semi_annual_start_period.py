"""add quarterly, semi_annual patterns and start_period_id

Revision ID: a3b1c2d4e5f6
Revises: 07198f0d6716
Create Date: 2026-02-22
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = 'a3b1c2d4e5f6'
down_revision = '07198f0d6716'
branch_labels = None
depends_on = None


def upgrade():
    # Add new recurrence patterns.
    op.execute("INSERT INTO ref.recurrence_patterns (name) VALUES ('quarterly')")
    op.execute("INSERT INTO ref.recurrence_patterns (name) VALUES ('semi_annual')")

    # Add start_period_id to recurrence_rules.
    op.add_column(
        'recurrence_rules',
        sa.Column('start_period_id', sa.Integer(), nullable=True),
        schema='budget',
    )
    op.create_foreign_key(
        'fk_recurrence_rules_start_period',
        'recurrence_rules', 'pay_periods',
        ['start_period_id'], ['id'],
        source_schema='budget', referent_schema='budget',
        ondelete='SET NULL',
    )


def downgrade():
    op.drop_constraint(
        'fk_recurrence_rules_start_period',
        'recurrence_rules',
        schema='budget',
        type_='foreignkey',
    )
    op.drop_column('recurrence_rules', 'start_period_id', schema='budget')

    op.execute("DELETE FROM ref.recurrence_patterns WHERE name = 'quarterly'")
    op.execute("DELETE FROM ref.recurrence_patterns WHERE name = 'semi_annual'")
