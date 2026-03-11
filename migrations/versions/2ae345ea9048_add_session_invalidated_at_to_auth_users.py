"""add session_invalidated_at to auth.users

Revision ID: 2ae345ea9048
Revises: d5e6f7a8b9c0
Create Date: 2026-03-10 21:52:30.674620
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = '2ae345ea9048'
down_revision = 'd5e6f7a8b9c0'
branch_labels = None
depends_on = None


def upgrade():
    """Apply forward migration."""
    op.add_column(
        'users',
        sa.Column('session_invalidated_at', sa.DateTime(timezone=True), nullable=True),
        schema='auth',
    )


def downgrade():
    """Revert migration."""
    op.drop_column('users', 'session_invalidated_at', schema='auth')
