"""add due_date to transfers

Revision ID: 48e2c7ee593d
Revises: c2a2c508e103
Create Date: 2026-06-03 12:46:12.273300

Adds ``budget.transfers.due_date`` -- the calendar date a transfer is due --
and backfills existing rows so the deploy is self-contained (the runtime
applies migrations automatically via ``scripts/init_database.py``; no manual
data step is required).

The column makes the parent transfer the canonical owner of the due date,
mirrored to both shadow transactions by ``transfer_service`` (Transfer
Invariant 3), consistent with how ``amount``/``status_id``/``pay_period_id``
already live on the parent and mirror down.

Backfill, two steps:

  1. Mirror every transfer's parent ``due_date`` from a shadow.  Both shadows
     carry an identical value by construction (``create_transfer`` sets both
     from the same argument; ``update_transfer`` sets both equal), so
     ``MIN(t.due_date)`` collapses the two rows deterministically and is
     NULL-safe.  This seeds ad-hoc, settled, and override transfers, which
     step 2 deliberately leaves alone.

  2. Recompute the canonical due date for PROJECTED (non-immutable),
     non-override, template-linked transfers and write it to the parent and
     both shadows.  Historically the recurrence engine stamped these with the
     pay-period START, discarding the rule's ``day_of_month``; the engine now
     uses ``recurrence_engine._compute_due_date``, and this step brings
     already-generated rows into line so monthly transfers (including
     derive-from-loan mortgage payments, whose rule carries
     ``day_of_month = LoanParams.payment_day``) land on their true monthly due
     date across the calendar/dashboard/year-end/spending-trend surfaces,
     matching the loan card.  Every-paycheck/every-N rules (no
     ``day_of_month``) resolve to the period start inside the helper, so the
     ``IS DISTINCT FROM`` guard makes those rows a no-op.

     The inputs are read via raw SQL (NOT ORM models) and fed to the shared
     pure ``_compute_due_date`` via lightweight namespaces -- this keeps the
     date logic single-sourced (DRY) while staying drift-safe: a later
     migration that adds columns to the involved tables cannot break this
     migration's replay, because no mapped class is queried.  The raw UPDATEs
     fire the audit trigger on each changed row (system backfill, NULL
     ``current_user_id``), matching the prior ``budget.transactions``
     account_id backfill (``efffcf647644``).

Purely additive at the schema level: the column is nullable with no CHECK and
no index (nothing queries transfers by ``due_date`` -- the due-date consumers
read the shadow ``budget.transactions.due_date``, which keeps its
``idx_transactions_due_date``; the asymmetry is deliberate).  No drop, rename,
type change, or constraint removal, so no ``Review:`` line is required.

Downgrade drops the column.  The schema revert is lossless (the canonical
value also lives on the shadow transactions).  The step-2 shadow recompute is
a forward data correction and is intentionally NOT reverted: the pre-migration
pay-period-start dates were the defect this migration fixes, they were not
snapshotted, and the recomputed dates remain valid under the current app code.
"""
from types import SimpleNamespace

from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = '48e2c7ee593d'
down_revision = 'c2a2c508e103'
branch_labels = None
depends_on = None


# Eligible-row selector for the step-2 recompute: projected (non-immutable),
# non-override, template-linked, non-deleted transfers, joined to the inputs
# _compute_due_date needs.  The INNER JOIN to recurrence_rules naturally
# excludes any template without a rule (nothing to compute from).
_RECOMPUTE_SELECT = sa.text(
    """
    SELECT x.id            AS transfer_id,
           r.day_of_month  AS day_of_month,
           r.due_day_of_month AS due_day_of_month,
           p.start_date    AS start_date,
           p.end_date      AS end_date
    FROM budget.transfers x
    JOIN budget.transfer_templates tt ON tt.id = x.transfer_template_id
    JOIN budget.recurrence_rules   r  ON r.id  = tt.recurrence_rule_id
    JOIN budget.pay_periods        p  ON p.id  = x.pay_period_id
    JOIN ref.statuses              s  ON s.id  = x.status_id
    WHERE x.transfer_template_id IS NOT NULL
      AND x.is_deleted = FALSE
      AND x.is_override = FALSE
      AND s.is_immutable = FALSE
    """
)

# Only write when the value actually changes -- keeps every-paycheck rows and
# already-correct rows from generating no-op UPDATEs (and audit rows).
_UPDATE_TRANSFER = sa.text(
    "UPDATE budget.transfers SET due_date = :d "
    "WHERE id = :i AND due_date IS DISTINCT FROM :d"
)
_UPDATE_SHADOWS = sa.text(
    "UPDATE budget.transactions SET due_date = :d "
    "WHERE transfer_id = :i AND due_date IS DISTINCT FROM :d"
)


def upgrade():
    """Add nullable due_date to budget.transfers and backfill it."""
    op.add_column(
        'transfers',
        sa.Column('due_date', sa.Date(), nullable=True),
        schema='budget',
    )

    bind = op.get_bind()

    # Step 1: mirror the parent from a shadow for ALL transfers.
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

    # Step 2: recompute eligible transfers from the recurrence rule, reusing
    # the shared pure helper.  Local imports defer app-code loading to upgrade
    # time.
    from app.services.recurrence_engine import _compute_due_date  # pylint: disable=import-outside-toplevel

    rows = bind.execute(_RECOMPUTE_SELECT).mappings().all()
    for row in rows:
        rule = SimpleNamespace(
            day_of_month=row["day_of_month"],
            due_day_of_month=row["due_day_of_month"],
        )
        period = SimpleNamespace(
            start_date=row["start_date"],
            end_date=row["end_date"],
        )
        due = _compute_due_date(rule, period)
        params = {"d": due, "i": row["transfer_id"]}
        bind.execute(_UPDATE_TRANSFER, params)
        bind.execute(_UPDATE_SHADOWS, params)


def downgrade():
    """Drop budget.transfers.due_date.

    Lossless at the schema level (the canonical value also lives on the shadow
    transactions).  The one-time shadow due-date recompute from upgrade() is a
    forward data correction and is intentionally not reverted -- see the module
    docstring.
    """
    op.drop_column('transfers', 'due_date', schema='budget')
