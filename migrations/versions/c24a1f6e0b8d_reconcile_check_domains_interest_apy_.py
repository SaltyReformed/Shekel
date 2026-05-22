"""C-24 reconcile_check_domains: drop interest_params.apy silent server_default

HIGH-06 / Commit 24 of the financial-calculations audit remediation plan
(``docs/audits/financial_calculations/remediation_plan.md``, Section 9 /
Commit 24).  The Marshmallow domain reconciliation work for HIGH-06 lives
entirely in ``app/schemas/validation.py`` and ``app/routes/`` (no DDL),
but the ``apy`` "silent first-save 4.5%" defect (Q-24 #2 / E-12 "zero is
a value, not missing") requires the storage-tier server_default to be
removed: any INSERT that omits ``apy`` must now fail loudly with a
``NotNullViolation`` rather than silently materialise a 4.5% rate the
user never configured.  The application-tier counterpart sites in
``app/routes/accounts.py`` were updated in the same commit to write
``apy=Decimal("0")`` explicitly at every auto-create (E-12 "zero is the
'no interest configured' sentinel"); this migration removes the
server_default so the storage tier can no longer paper over a future
write-path regression that omits the field.

Review: solo developer, 2026-05-20 (audit financial_calculations
HIGH-06 / E-28, Commit 24).  Destructive in the sense that a future
``INSERT INTO budget.interest_params (account_id, compounding_frequency)
VALUES (...)`` that omits ``apy`` will now raise ``NotNullViolation``
where the pre-fix server_default would have admitted the row at 4.5%.
The application code does not issue such an INSERT today (every
auto-create path supplies an explicit ``apy``) and the live database
contains no rows that depend on the default value continuing to
materialise; the change is intentional defence in depth against a
future regression that would otherwise be silent.

Downgrade restores the ``server_default="0.04500"`` byte-identically so
the migration is fully reversible at the schema tier.  Downgrade does
*not* re-introduce the audit hazard for existing rows -- those carry
explicit ``apy`` values written by the upgrade-side application code,
which is unaffected by the downgrade direction.

Revision ID: c24a1f6e0b8d
Revises: c4f0a5b71e83
Create Date: 2026-05-20 22:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "c24a1f6e0b8d"
down_revision = "c4f0a5b71e83"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Drop ``server_default`` on ``budget.interest_params.apy``.

    No data migration: the column remains NOT NULL and every existing
    row already holds an explicit ``apy`` value (the application has
    always assigned ``apy`` on every successful update flow; the
    server_default only mattered for first-save INSERTs that omitted
    the column, which the new application code no longer issues).
    """
    op.alter_column(
        "interest_params",
        "apy",
        existing_type=sa.Numeric(7, 5),
        existing_nullable=False,
        server_default=None,
        schema="budget",
    )


def downgrade() -> None:
    """Restore the original ``server_default="0.04500"`` on
    ``budget.interest_params.apy``.

    Reverting reintroduces the HIGH-06 silent-default hazard for any
    future code path that issues an INSERT omitting ``apy``; the
    application code shipped with this commit no longer relies on the
    default and remains safe regardless of downgrade direction.
    """
    op.alter_column(
        "interest_params",
        "apy",
        existing_type=sa.Numeric(7, 5),
        existing_nullable=False,
        server_default=sa.text("0.04500"),
        schema="budget",
    )
