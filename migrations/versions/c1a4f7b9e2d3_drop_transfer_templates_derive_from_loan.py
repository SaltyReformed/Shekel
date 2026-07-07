"""drop budget.transfer_templates.derive_from_loan (contract phase)

Revision ID: c1a4f7b9e2d3
Revises: b3e9d1f6a2c4
Create Date: 2026-07-07 09:30:00.000000

Review: SaltyReformed, 2026-07-07

The CONTRACT phase of the loan-payment-settings move (step 5,
``docs/design/escrow_line_identity_refactor.md`` Sec. 6.3, decision B -- the
operator pre-approved this destructive column drop in the plan).  The EXPAND
migration ``b3e9d1f6a2c4`` created ``budget.loan_payment_settings`` and
backfilled it from this column, and the reader cutover (same commit as the
expand) repointed every ``derive_from_loan`` consumer onto the settings table.
This migration DROPS the now-unused ``budget.transfer_templates.derive_from_loan``
column, retiring the loan-only flag from the generic transfer template.

**Destructive** (a column drop), so it carries this ``Review:`` line per
``.claude/rules/database.md``.  It is safe because no code reads or writes the
column any more (proven by the expand commit's reader cutover): the authoritative
source is ``loan_payment_settings.derive_from_loan``.

**Downgrade** re-adds the column (NOT NULL, ``server_default false`` so every
existing row is valid at add time) and reconstructs its values from the settings
table -- ``derive_from_loan = true`` exactly for the templates whose settings row
carries ``derive_from_loan = true``, matching the pre-drop state.  Lossless for
the flag (the settings table is the superset source); note that if a downgrade
follows a later overpayment edit, only the boolean flag is reconstructed here (the
column never carried ``extra_principal``), which is the intended scope.
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = "c1a4f7b9e2d3"
down_revision = "b3e9d1f6a2c4"
branch_labels = None
depends_on = None


# Reconstruct the dropped column's values from the settings table: TRUE exactly
# where the template's settings row is derive_from_loan, else the re-added
# column's FALSE server default.  Mirrors the expand backfill in reverse.
_DOWNGRADE_BACKFILL_SQL = (
    "UPDATE budget.transfer_templates tt "
    "SET derive_from_loan = true "
    "FROM budget.loan_payment_settings lps "
    "WHERE lps.transfer_template_id = tt.id "
    "  AND lps.derive_from_loan = true"
)


def upgrade():
    """Drop the retired ``derive_from_loan`` column (authoritative source is settings)."""
    op.drop_column("transfer_templates", "derive_from_loan", schema="budget")


def downgrade():
    """Re-add the column (NOT NULL / default false) and rebuild it from settings."""
    op.add_column(
        "transfer_templates",
        sa.Column(
            "derive_from_loan", sa.Boolean(), nullable=False,
            server_default=sa.text("false"),
        ),
        schema="budget",
    )
    op.execute(sa.text(_DOWNGRADE_BACKFILL_SQL))
