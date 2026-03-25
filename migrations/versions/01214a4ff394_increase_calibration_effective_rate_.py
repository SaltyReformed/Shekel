"""increase calibration effective rate precision to 10 decimal places

5 decimal places caused penny rounding errors when the derived rate was
multiplied back against the taxable/gross base.  10 decimal places
eliminates this for all practical pay stub amounts.

Revision ID: 01214a4ff394
Revises: 75b00691df57
Create Date: 2026-03-24 23:17:20.955090
"""
from alembic import op
import sqlalchemy as sa

# Revision identifiers, used by Alembic.
revision = '01214a4ff394'
down_revision = '75b00691df57'
branch_labels = None
depends_on = None


def upgrade():
    """Widen effective rate columns from Numeric(7,5) to Numeric(12,10)."""
    for col in ('effective_federal_rate', 'effective_state_rate',
                'effective_ss_rate', 'effective_medicare_rate'):
        op.alter_column(
            'calibration_overrides', col,
            existing_type=sa.NUMERIC(precision=7, scale=5),
            type_=sa.Numeric(precision=12, scale=10),
            existing_nullable=False,
            schema='salary',
        )


def downgrade():
    """Revert effective rate columns to Numeric(7,5)."""
    for col in ('effective_federal_rate', 'effective_state_rate',
                'effective_ss_rate', 'effective_medicare_rate'):
        op.alter_column(
            'calibration_overrides', col,
            existing_type=sa.Numeric(precision=12, scale=10),
            type_=sa.NUMERIC(precision=7, scale=5),
            existing_nullable=False,
            schema='salary',
        )
