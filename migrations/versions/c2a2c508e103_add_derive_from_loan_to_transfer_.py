"""add derive_from_loan to transfer_templates

Revision ID: c2a2c508e103
Revises: 3e22f914679b
Create Date: 2026-06-02 10:02:52.970961

Adds ``budget.transfer_templates.derive_from_loan``: when TRUE the
template's recurring transfers are a loan payment whose cash amount is
derived live from the destination loan account (P&I + escrow), so the
projected debit tracks the loan's monthly payment after an escrow or
rate change instead of staying frozen at ``default_amount``.

NOT NULL with ``server_default false`` -- the sanctioned static-default
form: every existing row (and every template that is not a loan payment)
is FALSE, so the live-derive override is dormant unless the loan
dashboard's create-payment flow enables it.  Purely additive; no
backfill (per the developer's "only new transfers auto-derive" choice).
Downgrade drops the column; lossless.
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = 'c2a2c508e103'
down_revision = '3e22f914679b'
branch_labels = None
depends_on = None


def upgrade():
    """Add the NOT NULL derive_from_loan column (defaults FALSE)."""
    op.add_column(
        'transfer_templates',
        sa.Column(
            'derive_from_loan', sa.Boolean(),
            server_default=sa.text('false'), nullable=False,
        ),
        schema='budget',
    )


def downgrade():
    """Drop the derive_from_loan column.  Lossless."""
    op.drop_column('transfer_templates', 'derive_from_loan', schema='budget')
