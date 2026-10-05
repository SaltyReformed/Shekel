"""a job says what its pay stub's gross includes

Revision ID: 1f431fec6547
Revises: d3b8f5a1c7e2
Create Date: 2026-10-04

Plan step **salary:S11-c-2b**, ruling **R-SAL102** ("A yes/no on each job"),
closing finding **SAL-592**::

    salary.salary_profiles               + stub_gross_includes_after_tax
                                           (boolean, NOT NULL, default false)

**One stored yes/no per salary job: whether its pay stub's printed gross also
holds the after-tax earnings.**  The stub door's printed-gross check (ruling
**R-SAL99**) reads a stub's gross as base pay plus the taxable earnings, which
is how the developer's employer prints it; a stub whose gross also holds a
non-taxable earning (a reimbursement) was refused with every figure right, and
the refusal's advice steered the owner to re-enter that earning as taxable.
"Yes" makes the check add the after-tax earnings too.  The column prices
nothing: its readers are the check, the entry form's line stating it and
the profile form that sets it, and the printed gross it checks is never
stored, so no saved stub and no paycheck moves either way.

**No backfill, and none is owed.**  ``false`` is the ruling's own starting
answer ("It starts at 'no', which is your employer"), and it keeps every
existing job's check exactly as it is: R-SAL99's check, the only thing that
asks the question, already reads every job as "no".  Whether another job's
employer prints its gross that way is that job's owner's answer to give on
the profile form; the server default changes no check anyone gets today, so
it is a static default that fits every existing row and the column is NOT
NULL from the start rather than added nullable and tightened.

The downgrade drops the column, losing any "yes" a job has recorded; the
older schema has no place for it and the older check reads every job as
"no" again.  Additive DDL with a plain reversal: no ``Review:`` line is owed.
"""

from alembic import op
import sqlalchemy as sa


revision = "1f431fec6547"
down_revision = "d3b8f5a1c7e2"
branch_labels = None
depends_on = None


def upgrade():
    """Add the yes/no, every existing job answering "no"."""
    op.add_column(
        "salary_profiles",
        sa.Column(
            "stub_gross_includes_after_tax", sa.Boolean(),
            nullable=False, server_default=sa.text("false"),
        ),
        schema="salary",
    )


def downgrade():
    """Drop the yes/no; the older check reads every job as "no"."""
    op.drop_column(
        "salary_profiles", "stub_gross_includes_after_tax", schema="salary",
    )
