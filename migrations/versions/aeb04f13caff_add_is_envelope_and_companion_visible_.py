"""add is_envelope and companion_visible to transactions

Adds two per-transaction boolean flags to budget.transactions so that
ad-hoc (template_id IS NULL) transactions can carry the same purchase-
tracking and companion-visibility semantics that template-generated
transactions inherit from their TransactionTemplate:

  * ``is_envelope``       -- enables individual purchase entries on the row.
  * ``companion_visible`` -- exposes the row in the linked companion's view.

Both columns are added NOT NULL with a static ``false`` server_default,
which backfills every existing row at ALTER TABLE time, so the single
add_column form is safe here (no separate UPDATE backfill step is needed
because ``false`` is the correct value for every pre-existing row -- no
transaction tracked purchases or was companion-visible through these
columns before they existed; template-generated rows continue to resolve
their behaviour from the template via Transaction.tracks_purchases /
Transaction.visible_to_companion).

budget.transactions is already in AUDITED_TABLES, so the existing audit
trigger captures changes to the new columns with no further wiring.

Review: solo developer, 2026-06-03 (F2/F3 ad-hoc flags; additive columns,
downgrade drops the two columns it added).

Revision ID: aeb04f13caff
Revises: 48e2c7ee593d
Create Date: 2026-06-03 18:32:28.157260
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = 'aeb04f13caff'
down_revision = '48e2c7ee593d'
branch_labels = None
depends_on = None


def upgrade():
    """Add the is_envelope and companion_visible columns."""
    op.add_column(
        'transactions',
        sa.Column(
            'is_envelope', sa.Boolean(),
            server_default='false', nullable=False,
        ),
        schema='budget',
    )
    op.add_column(
        'transactions',
        sa.Column(
            'companion_visible', sa.Boolean(),
            server_default='false', nullable=False,
        ),
        schema='budget',
    )


def downgrade():
    """Drop the two columns added by this migration.

    Reverses the additive upgrade exactly.  No data is lost beyond the
    two flags themselves, which existed only from this migration forward.
    """
    op.drop_column('transactions', 'companion_visible', schema='budget')
    op.drop_column('transactions', 'is_envelope', schema='budget')
