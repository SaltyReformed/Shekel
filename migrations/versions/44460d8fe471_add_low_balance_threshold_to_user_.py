"""Add low_balance_threshold to user_settings

Revision ID: 44460d8fe471
Revises: a3b1c2d4e5f6
Create Date: 2026-02-23 22:12:28.491323
"""
from alembic import op
import sqlalchemy as sa

# Revision identifiers, used by Alembic.
revision = '44460d8fe471'
down_revision = 'a3b1c2d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    """Apply forward migration."""
    op.add_column('user_settings', sa.Column('low_balance_threshold', sa.Integer(), nullable=True), schema='auth')


def downgrade():
    """Revert migration."""
    op.drop_column('user_settings', 'low_balance_threshold', schema='auth')
