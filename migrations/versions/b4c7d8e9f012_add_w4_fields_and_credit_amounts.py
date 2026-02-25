"""Add W-4 fields to salary_profiles and credit amounts to tax_bracket_sets

Revision ID: b4c7d8e9f012
Revises: 22b3dd9d9ed3
Create Date: 2026-02-25 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# Revision identifiers, used by Alembic.
revision = 'b4c7d8e9f012'
down_revision = '22b3dd9d9ed3'
branch_labels = None
depends_on = None


def upgrade():
    """Add W-4 withholding fields and per-year credit amounts."""

    # W-4 fields on salary_profiles (IRS Pub 15-T inputs)
    op.add_column(
        'salary_profiles',
        sa.Column('qualifying_children', sa.Integer(),
                  nullable=False, server_default='0'),
        schema='salary',
    )
    op.add_column(
        'salary_profiles',
        sa.Column('other_dependents', sa.Integer(),
                  nullable=False, server_default='0'),
        schema='salary',
    )
    op.add_column(
        'salary_profiles',
        sa.Column('additional_income', sa.Numeric(12, 2),
                  nullable=False, server_default='0'),
        schema='salary',
    )
    op.add_column(
        'salary_profiles',
        sa.Column('additional_deductions', sa.Numeric(12, 2),
                  nullable=False, server_default='0'),
        schema='salary',
    )
    op.add_column(
        'salary_profiles',
        sa.Column('extra_withholding', sa.Numeric(12, 2),
                  nullable=False, server_default='0'),
        schema='salary',
    )

    # Credit amounts on tax_bracket_sets (versioned per year / filing status)
    op.add_column(
        'tax_bracket_sets',
        sa.Column('child_credit_amount', sa.Numeric(12, 2),
                  nullable=False, server_default='0'),
        schema='salary',
    )
    op.add_column(
        'tax_bracket_sets',
        sa.Column('other_dependent_credit_amount', sa.Numeric(12, 2),
                  nullable=False, server_default='0'),
        schema='salary',
    )


def downgrade():
    """Remove W-4 and credit amount columns."""
    op.drop_column('tax_bracket_sets', 'other_dependent_credit_amount',
                   schema='salary')
    op.drop_column('tax_bracket_sets', 'child_credit_amount',
                   schema='salary')
    op.drop_column('salary_profiles', 'extra_withholding', schema='salary')
    op.drop_column('salary_profiles', 'additional_deductions', schema='salary')
    op.drop_column('salary_profiles', 'additional_income', schema='salary')
    op.drop_column('salary_profiles', 'other_dependents', schema='salary')
    op.drop_column('salary_profiles', 'qualifying_children', schema='salary')
