"""add goal_modes and income_units reference tables

Revision ID: 1dc0e7a1b9e4
Revises: 98b1adb05030
Create Date: 2026-04-03 13:23:19.251358
"""
from alembic import op
import sqlalchemy as sa

# Revision identifiers, used by Alembic.
revision = '1dc0e7a1b9e4'
down_revision = '98b1adb05030'
branch_labels = None
depends_on = None


def upgrade():
    """Create ref.goal_modes and ref.income_units reference tables."""
    op.create_table('goal_modes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
        schema='ref'
    )
    op.create_table('income_units',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
        schema='ref'
    )


def downgrade():
    """Drop ref.goal_modes and ref.income_units reference tables."""
    op.drop_table('income_units', schema='ref')
    op.drop_table('goal_modes', schema='ref')
