"""add tax_year to state_tax_configs

Revision ID: 7abcbf372fff
Revises: 02b1ff12b08c
Create Date: 2026-03-20 23:38:43.486072
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = '7abcbf372fff'
down_revision = '02b1ff12b08c'
branch_labels = None
depends_on = None


def upgrade():
    """Apply forward migration."""
    # Add tax_year column with a default so existing rows get a value.
    op.add_column(
        'state_tax_configs',
        sa.Column('tax_year', sa.Integer(), nullable=False, server_default='2026'),
        schema='salary',
    )
    # Remove the server default after backfill.
    op.alter_column(
        'state_tax_configs', 'tax_year',
        server_default=None, schema='salary',
    )
    # Replace old unique constraint with one that includes tax_year.
    op.drop_constraint(
        'uq_state_tax_configs_user_state', 'state_tax_configs', schema='salary',
    )
    op.create_unique_constraint(
        'uq_state_tax_configs_user_state_year', 'state_tax_configs',
        ['user_id', 'state_code', 'tax_year'], schema='salary',
    )


def downgrade():
    """Revert migration."""
    op.drop_constraint(
        'uq_state_tax_configs_user_state_year', 'state_tax_configs', schema='salary',
    )
    op.create_unique_constraint(
        'uq_state_tax_configs_user_state', 'state_tax_configs',
        ['user_id', 'state_code'], schema='salary',
    )
    op.drop_column('state_tax_configs', 'tax_year', schema='salary')
