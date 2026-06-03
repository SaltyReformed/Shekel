"""add due_date to transfers

Revision ID: 48e2c7ee593d
Revises: c2a2c508e103
Create Date: 2026-06-03 12:46:12.273300

Adds ``budget.transfers.due_date`` -- the calendar date a transfer is due.
The column makes the parent transfer the canonical owner of the due date,
mirrored to both shadow transactions by ``transfer_service`` (Transfer
Invariant 3), consistent with how ``amount``/``status_id``/``pay_period_id``
already live on the parent and mirror down.

Backfill: every pre-existing transfer's two shadow transactions already
carry an identical ``due_date`` (``create_transfer`` sets both from the same
argument and ``update_transfer`` always sets both equal), so the parent is
seeded from that value.  ``MIN(t.due_date)`` collapses the two identical
shadow rows to one deterministically and is NULL-safe: transfers whose
shadows have no due date (older ad-hoc transfers) keep ``due_date IS NULL``,
and a corrupt transfer with no active shadows resolves to NULL rather than
erroring.  The raw UPDATE does not bump ``version_id`` -- migrations run
with no concurrent writers, so the next ORM load reads the current counter
and no stale pin results.  The audit trigger on ``budget.transfers`` fires
on the backfill UPDATE (matching the prior ``budget.transactions``
account_id backfill, ``efffcf647644``).

Purely additive: the column is nullable with no CHECK and no index (nothing
queries transfers by ``due_date`` -- the due-date consumers read the shadow
``budget.transactions.due_date``, which keeps its ``idx_transactions_due_date``;
the asymmetry is deliberate).  No drop, rename, type change, or constraint
removal, so no ``Review:`` line is required.  Downgrade drops the column and
is lossless: the canonical value also lives on the shadow transactions, so
no due-date information is lost when the parent column goes away.
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = '48e2c7ee593d'
down_revision = 'c2a2c508e103'
branch_labels = None
depends_on = None


def upgrade():
    """Add nullable due_date to budget.transfers and backfill from shadows."""
    op.add_column(
        'transfers',
        sa.Column('due_date', sa.Date(), nullable=True),
        schema='budget',
    )
    # Seed the new parent column from the transfer's shadow transactions.
    # Both shadows share one due_date by construction; MIN collapses them to
    # a single deterministic, NULL-safe value.  See module docstring.
    op.execute(
        """
        UPDATE budget.transfers x
        SET due_date = (
            SELECT MIN(t.due_date)
            FROM budget.transactions t
            WHERE t.transfer_id = x.id
        )
        """
    )


def downgrade():
    """Drop budget.transfers.due_date (lossless -- value remains on shadows)."""
    op.drop_column('transfers', 'due_date', schema='budget')
