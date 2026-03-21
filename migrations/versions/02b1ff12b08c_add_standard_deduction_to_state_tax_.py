"""add standard_deduction to state_tax_configs

Revision ID: 02b1ff12b08c
Revises: a8b1c2d3e4f5
Create Date: 2026-03-20 23:33:34.974325
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = '02b1ff12b08c'
down_revision = 'a8b1c2d3e4f5'
branch_labels = None
depends_on = None


def upgrade():
    """Apply forward migration."""
    op.add_column('state_tax_configs', sa.Column('standard_deduction', sa.Numeric(precision=12, scale=2), nullable=True), schema='salary')


def downgrade():
    """Revert migration."""
    op.drop_column('state_tax_configs', 'standard_deduction', schema='salary')
