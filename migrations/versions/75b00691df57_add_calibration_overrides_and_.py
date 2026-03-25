"""add calibration_overrides and calibration_deduction_overrides tables

Stores effective tax/deduction rates derived from a real pay stub so
the paycheck calculator can use actual withholding data instead of
bracket-based estimates.

Revision ID: 75b00691df57
Revises: f8f8173ff361
Create Date: 2026-03-24 22:31:55.834221
"""
from alembic import op
import sqlalchemy as sa

# Revision identifiers, used by Alembic.
revision = '75b00691df57'
down_revision = 'f8f8173ff361'
branch_labels = None
depends_on = None


def upgrade():
    """Create calibration override tables in the salary schema."""
    op.create_table('calibration_overrides',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('salary_profile_id', sa.Integer(), nullable=False),
        sa.Column('actual_gross_pay', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('actual_federal_tax', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('actual_state_tax', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('actual_social_security', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('actual_medicare', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('effective_federal_rate', sa.Numeric(precision=7, scale=5), nullable=False),
        sa.Column('effective_state_rate', sa.Numeric(precision=7, scale=5), nullable=False),
        sa.Column('effective_ss_rate', sa.Numeric(precision=7, scale=5), nullable=False),
        sa.Column('effective_medicare_rate', sa.Numeric(precision=7, scale=5), nullable=False),
        sa.Column('pay_stub_date', sa.Date(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.CheckConstraint('actual_federal_tax >= 0', name='ck_calibration_overrides_nonneg_federal'),
        sa.CheckConstraint('actual_gross_pay > 0', name='ck_calibration_overrides_positive_gross'),
        sa.CheckConstraint('actual_medicare >= 0', name='ck_calibration_overrides_nonneg_medicare'),
        sa.CheckConstraint('actual_social_security >= 0', name='ck_calibration_overrides_nonneg_ss'),
        sa.CheckConstraint('actual_state_tax >= 0', name='ck_calibration_overrides_nonneg_state'),
        sa.ForeignKeyConstraint(['salary_profile_id'], ['salary.salary_profiles.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('salary_profile_id', name='uq_calibration_overrides_profile'),
        schema='salary'
    )
    op.create_table('calibration_deduction_overrides',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('calibration_id', sa.Integer(), nullable=False),
        sa.Column('deduction_id', sa.Integer(), nullable=False),
        sa.Column('actual_amount', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.CheckConstraint('actual_amount >= 0', name='ck_calibration_ded_overrides_nonneg_amount'),
        sa.ForeignKeyConstraint(['calibration_id'], ['salary.calibration_overrides.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['deduction_id'], ['salary.paycheck_deductions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('calibration_id', 'deduction_id', name='uq_calibration_ded_overrides_cal_ded'),
        schema='salary'
    )


def downgrade():
    """Drop calibration override tables."""
    op.drop_table('calibration_deduction_overrides', schema='salary')
    op.drop_table('calibration_overrides', schema='salary')
