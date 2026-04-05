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
    """Create ref.goal_modes and ref.income_units reference tables.

    Seed data is inserted here because the next migration
    (4f2d894216ad) depends on ref.goal_modes ID 1 = 'Fixed' for
    its server_default.  The entrypoint runs all migrations before
    seed_ref_tables.py, so the data must exist within the migration
    chain itself.  seed_ref_tables.py is idempotent and will skip
    these rows on subsequent runs.
    """
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

    # Seed reference data so downstream migrations can reference it.
    op.execute(
        "INSERT INTO ref.goal_modes (id, name) VALUES "
        "(1, 'Fixed'), (2, 'Income-Relative')"
    )
    op.execute(
        "INSERT INTO ref.income_units (id, name) VALUES "
        "(1, 'Paychecks'), (2, 'Months')"
    )


def downgrade():
    """Drop ref.goal_modes and ref.income_units reference tables."""
    op.drop_table('income_units', schema='ref')
    op.drop_table('goal_modes', schema='ref')
