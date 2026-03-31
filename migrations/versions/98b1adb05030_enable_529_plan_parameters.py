"""enable 529 plan parameters

Revision ID: 98b1adb05030
Revises: b4a6bb55f78b
Create Date: 2026-03-30 21:26:16.579030
"""
from alembic import op

# Revision identifiers, used by Alembic.
revision = '98b1adb05030'
down_revision = 'b4a6bb55f78b'
branch_labels = None
depends_on = None


def upgrade():
    """Set has_parameters=True for 529 Plan so it gets InvestmentParams."""
    op.execute(
        "UPDATE ref.account_types SET has_parameters = true "
        "WHERE name = '529 Plan'"
    )


def downgrade():
    """Revert 529 Plan to has_parameters=False."""
    op.execute(
        "UPDATE ref.account_types SET has_parameters = false "
        "WHERE name = '529 Plan'"
    )
