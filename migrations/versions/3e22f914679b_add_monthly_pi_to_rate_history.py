"""add monthly_pi to rate_history

Revision ID: 3e22f914679b
Revises: 78782c6ac75e
Create Date: 2026-06-02 08:20:50.255070

Adds ``budget.rate_history.monthly_pi`` -- the recorded recast P&I
(principal + interest, no escrow) that took effect with the rate row's
``effective_date``.  The rate-period loan model
(``app/services/rate_period_engine.py``, introduced in the following
commit) holds the monthly P&I constant within each fixed-rate period;
for a period whose start balance is not derivable from the app's
recorded history (a mid-life ARM adopted after origination), the lender's
recast figure must be recorded so the payment is not re-derived from a
balance that may have drifted.  NULL means "derive" (origination period,
or a loan with full history); a non-NULL value is used verbatim.

Purely additive: the column is nullable and the CHECK
(``monthly_pi IS NULL OR monthly_pi > 0``) permits NULL, so every
pre-existing row (all NULL on creation) trivially satisfies it -- no
pre-count guard or ``Review:`` line is required (no drop, rename, type
change, or constraint removal).  Downgrade drops the CHECK then the
column; lossless.
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = '3e22f914679b'
down_revision = '78782c6ac75e'
branch_labels = None
depends_on = None


CONSTRAINT_NAME = "ck_rate_history_monthly_pi_positive"
TABLE_NAME = "rate_history"
SCHEMA_NAME = "budget"


def upgrade():
    """Add the nullable monthly_pi column and its NULL-permitting CHECK."""
    op.add_column(
        TABLE_NAME,
        sa.Column("monthly_pi", sa.Numeric(precision=12, scale=2), nullable=True),
        schema=SCHEMA_NAME,
    )
    op.create_check_constraint(
        CONSTRAINT_NAME,
        TABLE_NAME,
        "monthly_pi IS NULL OR monthly_pi > 0",
        schema=SCHEMA_NAME,
    )


def downgrade():
    """Drop the CHECK then the column.  Lossless; no data implications."""
    op.drop_constraint(
        CONSTRAINT_NAME,
        TABLE_NAME,
        type_="check",
        schema=SCHEMA_NAME,
    )
    op.drop_column(TABLE_NAME, "monthly_pi", schema=SCHEMA_NAME)
