"""Add upper-bound CHECK on loan_params.interest_rate (F-18)

Revision ID: c66a9a7fda5a
Revises: c24a1f6e0b8d
Create Date: 2026-05-21 00:00:00.000000

Review: solo developer, 2026-05-21 (remediation_follow_up_plan.md F-18
destructive migration, plan-time approved)

Closes F-18 / Commit 13 of the financial-calculation audit follow-up
remediation.  ``budget.loan_params.interest_rate`` carried only the
lower-bound CHECK (``interest_rate >= 0``); the three sibling rate
columns (``interest_params.apy``, ``rate_history.interest_rate``,
``escrow_components.inflation_rate``) all pin the closed unit
interval ``[0, 1]``.  The Marshmallow ``LoanParamsCreateSchema``
already enforces ``Range(0, 1)`` on the application tier (HIGH-06 /
Commit 24), so this migration mirrors the same domain at the storage
tier as belt-and-suspenders against raw-SQL writers or future
regressions that bypass the schema.

The new constraint uses ``IS NULL OR interest_rate <= 1`` so the
E-18 / Commit 15 demotion (``interest_rate`` is nullable, non-
authoritative seed) is preserved.  PostgreSQL treats NULL as
"unknown" under boolean predicates and would already admit NULL
without the explicit guard, but writing the predicate out documents
the intent and matches the sibling ``escrow_components.inflation_rate``
shape verbatim.

Destructive in the audit's strict sense (adds a CHECK; pre-existing
rows that violate the bound would block the upgrade).  Plan-time
approval is recorded in ``docs/audits/financial_calculations/
remediation_follow_up_plan.md`` Section 2 ("F-18 destructive
migration -- Included"), Section 7 Commit 13.

The upgrade body pre-counts violating rows and raises ``RuntimeError``
with the diagnostic ``SELECT`` embedded in the message rather than
attempting the ``ALTER TABLE`` and letting PostgreSQL surface the
generic constraint-violation error.  Staging and disaster-recovery
replays that touch real data are the operationally important
beneficiaries -- a clean error pointing at the offending rows beats
chasing an opaque ``CheckViolation`` from a partial migration.

Downgrade simply drops the constraint; no data loss, no
NotImplementedError needed.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c66a9a7fda5a"
down_revision = "c24a1f6e0b8d"
branch_labels = None
depends_on = None


CONSTRAINT_NAME = "ck_loan_params_interest_rate_upper"
TABLE_NAME = "loan_params"
SCHEMA_NAME = "budget"
CHECK_SQL = "interest_rate IS NULL OR interest_rate <= 1"


def upgrade():
    """Add the upper-bound CHECK after verifying no row would violate it.

    The pre-check is a single aggregate SELECT against the
    ``budget.loan_params`` table; if it returns non-zero the migration
    raises ``RuntimeError`` rather than letting PostgreSQL surface a
    less actionable ``CheckViolation``.  The error message embeds the
    diagnostic ``SELECT`` the operator can paste into ``psql`` to
    locate the offending rows.
    """
    bind = op.get_bind()
    violations = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM budget.loan_params "
            "WHERE interest_rate > 1"
        )
    ).scalar()
    if violations:
        raise RuntimeError(
            f"Cannot add CHECK {CONSTRAINT_NAME}: {violations} row(s) "
            f"in budget.loan_params carry interest_rate > 1 and would "
            f"violate the new bound.  Inspect with: "
            f"`SELECT id, account_id, interest_rate FROM "
            f"budget.loan_params WHERE interest_rate > 1` and decide "
            f"whether to clamp or delete before re-running the upgrade."
        )
    op.create_check_constraint(
        CONSTRAINT_NAME,
        TABLE_NAME,
        CHECK_SQL,
        schema=SCHEMA_NAME,
    )


def downgrade():
    """Drop the upper-bound CHECK.  Lossless; no data implications."""
    op.drop_constraint(
        CONSTRAINT_NAME,
        TABLE_NAME,
        type_="check",
        schema=SCHEMA_NAME,
    )
