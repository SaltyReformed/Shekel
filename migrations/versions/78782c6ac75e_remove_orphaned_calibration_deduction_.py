"""remove orphaned calibration_deduction_overrides table

The salary.calibration_deduction_overrides table (and its
CalibrationDeductionOverride model) was an unfinished feature: no
application code ever read or wrote it, the calibration confirm flow
never created a row, and the paycheck calculator used the configured
deduction amount rather than any stored override.  It is removed here.

The table was always empty (no writer existed), so the drop loses no
data.  The audit trigger is removed from app.audit_infrastructure
AUDITED_TABLES in the same change, decrementing EXPECTED_TRIGGER_COUNT
from 32 to 31; dropping the table cascades its audit trigger away.

Downgrade recreates the table object (columns, PK, the two CASCADE FKs,
the unique constraint, created_at NOT NULL per 8a21d16c9bde) but does
NOT recreate the deduction_id index and does NOT re-attach an audit
trigger.  The index is omitted because c42b1d9a4e8f's INDEX_SPECS no
longer lists it (it was removed together with this table), so the
forward chain never creates it and the downgrade must match that
state.  The audit trigger is omitted because per-table triggers are
owned by the rebuild migration via AUDITED_TABLES, not by a table's own
create/restore migration (the original 75b00691df57 created the table
without a trigger), so a table absent from AUDITED_TABLES is correctly
untriggered.

Review: solo developer, 2026-06-01 (remove orphaned
CalibrationDeductionOverride; destructive drop of an empty,
never-written table)

Revision ID: 78782c6ac75e
Revises: c66a9a7fda5a
Create Date: 2026-06-01 20:25:31.169000
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = '78782c6ac75e'
down_revision = 'c66a9a7fda5a'
branch_labels = None
depends_on = None


def upgrade():
    """Drop the orphaned calibration_deduction_overrides table."""
    # The audit trigger is dropped automatically with the table, but
    # drop it explicitly first so the intent is visible and the step is
    # idempotent if the trigger was already absent.
    op.execute(
        "DROP TRIGGER IF EXISTS audit_calibration_deduction_overrides "
        "ON salary.calibration_deduction_overrides"
    )
    op.drop_table('calibration_deduction_overrides', schema='salary')


def downgrade():
    """Recreate the (empty) table without the deduction_id index or trigger.

    The deduction_id index is intentionally NOT recreated: c42b1d9a4e8f
    no longer lists it in INDEX_SPECS, so the forward chain never creates
    it and the downgrade matches that state.  No audit trigger is
    attached: the table is absent from AUDITED_TABLES, and per-table
    triggers are governed by the rebuild migration rather than this
    table's lifecycle.
    """
    op.create_table(
        'calibration_deduction_overrides',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('calibration_id', sa.Integer(), nullable=False),
        sa.Column('deduction_id', sa.Integer(), nullable=False),
        sa.Column('actual_amount', sa.Numeric(precision=10, scale=2),
                  nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('actual_amount >= 0',
                           name='ck_calibration_ded_overrides_nonneg_amount'),
        sa.ForeignKeyConstraint(['calibration_id'],
                                ['salary.calibration_overrides.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['deduction_id'],
                                ['salary.paycheck_deductions.id'],
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('calibration_id', 'deduction_id',
                            name='uq_calibration_ded_overrides_cal_ded'),
        schema='salary',
    )
