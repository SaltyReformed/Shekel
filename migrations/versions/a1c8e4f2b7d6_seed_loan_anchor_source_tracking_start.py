"""Seed the ``tracking_start`` loan anchor source (mid-life-import opening)

Revision ID: a1c8e4f2b7d6
Revises: f2a7c1e9b4d3
Create Date: 2026-07-06 12:30:00.000000

Adds the ``tracking_start`` provenance to ``ref.loan_anchor_sources`` so a
mid-life-imported loan can record its confirmed-ledger opening (a real balance
as of a date at/before the first recorded payment) instead of the fictional
origination.  When such an event exists,
``app.services.loan_loaders.load_loan_anchor_facts`` synthesizes the loan's
``is_opening`` anchor from it in place of the origination
(``LoanParams.origination_date`` / ``original_principal`` stay for the
amortization schedule / projection only), so the genesis ledger opens at the
real recent balance -- no fictional origination-to-tracking-start plateau, and
recorded payments accrue interest on the correct balance.

Dual-seed pattern (mirrors ``d3d25212504b`` for the two existing sources and the
posting-ref migrations): this migration inline-seeds the row with
``ON CONFLICT (name) DO NOTHING`` so a freshly upgraded database resolves the new
``LoanAnchorSourceEnum.TRACKING_START`` member before the idempotent
``app.ref_seeds.seed_reference_data`` reseed runs; the seed list in
``app/ref_seeds.py`` carries the same name for the ``create_all`` fresh-init path
and the ongoing reseed.  Idempotent, so a re-run (or a reseed) is a no-op.

Self-contained: raw SQL, imports nothing from ``app``.
"""
from alembic import op


# Revision identifiers, used by Alembic.
revision = 'a1c8e4f2b7d6'
down_revision = 'f2a7c1e9b4d3'
branch_labels = None
depends_on = None


# Inline seed (idempotent).  ``ref.loan_anchor_sources`` has a unique ``name``,
# so ``ON CONFLICT (name) DO NOTHING`` makes a re-run / reseed a no-op.
_SEED_TRACKING_START_SQL = (
    "INSERT INTO ref.loan_anchor_sources (name) VALUES ('tracking_start') "
    "ON CONFLICT (name) DO NOTHING"
)

# Downgrade removal.  ``loan_anchor_events.source_id`` is RESTRICT-on-delete, so
# this DELETE succeeds only when no event references the source -- exactly the
# desired safety (a downgrade cannot silently orphan tracking-start events).  A
# downgrade with tracking-start events present raises the FK violation, naming
# the constraint, which is the correct signal to first remove those events.
_UNSEED_TRACKING_START_SQL = (
    "DELETE FROM ref.loan_anchor_sources WHERE name = 'tracking_start'"
)


def upgrade():
    """Insert the ``tracking_start`` loan anchor source (idempotent)."""
    op.execute(_SEED_TRACKING_START_SQL)


def downgrade():
    """Remove the ``tracking_start`` loan anchor source.

    Deletes the ref row.  Because ``fk_loan_anchor_events_source_id`` is
    RESTRICT-on-delete, this raises if any ``tracking_start``
    :class:`~app.models.loan_anchor_event.LoanAnchorEvent` still references it --
    the correct guard: remove those events first.  On a clean chain (no
    tracking-start events, e.g. a template rebuild) it succeeds.
    """
    op.execute(_UNSEED_TRACKING_START_SQL)
