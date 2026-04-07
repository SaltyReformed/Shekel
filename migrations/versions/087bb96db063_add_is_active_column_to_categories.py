"""add is_active column to categories

Revision ID: 087bb96db063
Revises: 4f2d894216ad
Create Date: 2026-04-06 21:16:21.539118
"""
from alembic import op
import sqlalchemy as sa

# Revision identifiers, used by Alembic.
revision = '087bb96db063'
down_revision = '4f2d894216ad'
branch_labels = None
depends_on = None


def upgrade():
    """Apply forward migration."""
    op.add_column(
        'categories',
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        schema='budget',
    )


def downgrade():
    """Revert migration."""
    op.drop_column('categories', 'is_active', schema='budget')
