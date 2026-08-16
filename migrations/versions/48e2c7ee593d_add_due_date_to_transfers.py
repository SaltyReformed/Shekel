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
     uses ``recurrence_engine.compute_due_date``, and this step brings
     already-generated rows into line so monthly transfers (including
     derive-from-loan mortgage payments, whose rule carries
     ``day_of_month = LoanParams.payment_day``) land on their true monthly due
     date across the calendar/dashboard/year-end/spending-trend surfaces,
     matching the loan card.  Every-paycheck/every-N rules (no
     ``day_of_month``) resolve to the period start inside the helper, so the
     ``IS DISTINCT FROM`` guard makes those rows a no-op.

     The inputs are read via raw SQL (NOT ORM models) and fed to
     :func:`_due_date_at_this_revision`, this module's FROZEN copy of the
     arithmetic.  The raw UPDATEs fire the audit trigger on each changed row
     (system backfill, NULL ``current_user_id``), matching the prior
     ``budget.transactions`` account_id backfill (``efffcf647644``).

     **The copy replaced an import of the live
     ``recurrence_engine.compute_due_date`` at plan step R7c-c, and the import
     was a replay-breaking defect.**  Reading the inputs with raw SQL made this
     step drift-safe against a later migration ADDING columns; it did nothing
     about the shared function's own signature moving underneath it, which is
     what happened.  R7c-c dropped ``budget.recurrence_rules.day_of_month`` and
     pointed that function at a derivation over ``unit_id`` / ``placement_id``
     / ``starts_on`` / ``nominal_day``, none of which the namespaces here carry
     and none of which EXIST at this revision -- so every replay over a
     non-empty database raised ``AttributeError`` mid-chain and aborted
     ``flask db upgrade``.  Invisible to CI and to
     ``scripts/build_test_template.py``, which replay this chain against an
     EMPTY database where the loop never runs; it fires only on the replay that
     matters, a restored dump stamped at or before this revision.

     Widening the SELECT could not have fixed it -- the two-axis columns do not
     exist here -- so the mapping is stated AS IT WAS AT THIS REVISION and the
     import is deleted.  That is the same rule ``d9f5c1a48b73`` states for its
     own pattern table: a migration states the mapping as it was at its
     revision, and an import makes a shipped migration change meaning when a
     later step edits the code.  Graded by
     ``tests/test_models/test_transfer_due_date_migration.py``, which drives
     every branch of the frozen arithmetic.

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
import calendar as cal
from datetime import date

from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = '48e2c7ee593d'
down_revision = 'c2a2c508e103'
branch_labels = None
depends_on = None


# Eligible-row selector for the step-2 recompute: projected (non-immutable),
# non-override, template-linked, non-deleted transfers, joined to the inputs
# compute_due_date needs.  The INNER JOIN to recurrence_rules naturally
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


def _due_date_at_this_revision(
    day_of_month, due_day_of_month, start_date, end_date,
):
    """Return the due date this revision's step-2 recompute writes.

    ``recurrence_engine.compute_due_date`` as it stood when this revision
    shipped, frozen here so a replay computes what the revision MEANT rather
    than whatever that function grew into -- see the module docstring for the
    replay this froze after breaking.

    Source priority, unchanged from the original:

      1. *due_day_of_month*, when it is set and differs from the scheduling day;
      2. the scheduling day *day_of_month*, placed in the period's month;
      3. *start_date*, for a rule that names no day of the month.

    Next-month convention: a due day BELOW the scheduling day falls in the
    following calendar month -- scheduled on the 22nd and due on the 1st means
    the 1st of the month after.  Day values above a month's last day are
    clamped (31 in April becomes 30; 30 in February becomes 28).

    Takes four plain values rather than a rule and a period: the caller reads
    them with raw SQL, and column names at THIS revision are what the frozen
    mapping is entitled to name.

    Args:
        day_of_month: ``budget.recurrence_rules.day_of_month``, or ``None`` for
            a cadence that names no day (every-paycheck, every-N).
        due_day_of_month: ``budget.recurrence_rules.due_day_of_month``, or
            ``None`` when the bill is due on the day it is scheduled.
        start_date: The assigned pay period's first day.
        end_date: The assigned pay period's last day.

    Returns:
        date: The calendar date the transfer is due.
    """
    if day_of_month is None:
        return start_date

    # Which month within the period holds the scheduling-day target.  A pay
    # period can straddle two months, so both endpoints are tried in order.
    base_year = start_date.year
    base_month = start_date.month

    for dt in (start_date, end_date):
        last_day = cal.monthrange(dt.year, dt.month)[1]
        target = date(dt.year, dt.month, min(day_of_month, last_day))
        if start_date <= target <= end_date:
            base_year = dt.year
            base_month = dt.month
            break

    if due_day_of_month is None or due_day_of_month == day_of_month:
        # No separate due date -- the scheduling day in the base month.
        last_day = cal.monthrange(base_year, base_month)[1]
        return date(base_year, base_month, min(day_of_month, last_day))

    if due_day_of_month < day_of_month:
        if base_month == 12:
            due_year = base_year + 1
            due_month = 1
        else:
            due_year = base_year
            due_month = base_month + 1
    else:
        due_year = base_year
        due_month = base_month

    last_day = cal.monthrange(due_year, due_month)[1]
    return date(due_year, due_month, min(due_day_of_month, last_day))


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

    # Step 2: recompute eligible transfers from the recurrence rule, through
    # this module's frozen copy of the arithmetic (see
    # :func:`_due_date_at_this_revision`).  It imports no app code: the shared
    # function it used to call is free to move, and did.
    rows = bind.execute(_RECOMPUTE_SELECT).mappings().all()
    for row in rows:
        due = _due_date_at_this_revision(
            row["day_of_month"],
            row["due_day_of_month"],
            row["start_date"],
            row["end_date"],
        )
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
