"""add cancelled status

Revision ID: 07198f0d6716
Revises: 9dea99d4e33e
Create Date: 2026-02-21
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = '07198f0d6716'
down_revision = '9dea99d4e33e'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("INSERT INTO ref.statuses (name) VALUES ('cancelled')")


def downgrade():
    op.execute("DELETE FROM ref.statuses WHERE name = 'cancelled'")
