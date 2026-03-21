"""add end_date to recurrence_rules

Revision ID: f8f8173ff361
Revises: b4c5d6e7f8a9
Create Date: 2026-03-21 01:29:56.151052
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = 'f8f8173ff361'
down_revision = 'b4c5d6e7f8a9'
branch_labels = None
depends_on = None


def upgrade():
    """Apply forward migration."""
    op.add_column('recurrence_rules', sa.Column('end_date', sa.Date(), nullable=True), schema='budget')


def downgrade():
    """Revert migration."""
    op.drop_column('recurrence_rules', 'end_date', schema='budget')
