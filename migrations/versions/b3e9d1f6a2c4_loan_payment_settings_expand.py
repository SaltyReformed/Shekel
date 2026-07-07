"""create budget.loan_payment_settings + backfill derive_from_loan (expand phase)

Revision ID: b3e9d1f6a2c4
Revises: d7b2f9a4c1e6
Create Date: 2026-07-07 09:00:00.000000

The EXPAND phase of the overpayment work (step 5,
``docs/design/escrow_line_identity_refactor.md`` Sec. 6.3, decision B).  ADDITIVE
ONLY -- it creates the new 1:1 ``budget.loan_payment_settings`` table and
backfills it from the existing ``budget.transfer_templates.derive_from_loan``
column, which it LEAVES IN PLACE and unchanged.  The reader cutover (same commit)
repoints every ``derive_from_loan`` reader onto the new table; the CONTRACT
migration that follows drops the old column (that later migration is the
destructive one and carries its own ``Review:`` line).  So this migration is not
destructive and behaviour is unchanged on deploy.

## The new model

``budget.loan_payment_settings`` -- the loan-payment attributes of a recurring
transfer (``id`` PK, ``transfer_template_id`` UNIQUE CASCADE FK,
``derive_from_loan``, ``extra_principal``).  Present only for recurring LOAN
payments; a template with no row is not a loan payment and every reader defaults
``derive_from_loan`` to ``False`` and ``extra_principal`` to ``0.00``.  The DDL
matches the model in ``app/models/loan_payment_settings.py`` (a future
``flask db migrate --autogenerate`` yields an empty diff); the table is added to
``app/audit_infrastructure.py::AUDITED_TABLES`` and gets an audit trigger here,
following the ``c4f8a1b6e9d2`` precedent (a narrow manual DROP+CREATE, NOT
``apply_audit_infrastructure``, so an earlier fresh-DB replay of the rebuild
migration is not asked to trigger a table that does not exist yet).

## The backfill (byte-identical behaviour on current data)

One ``loan_payment_settings`` row per template whose ``derive_from_loan`` is TRUE
(the only non-default value the column carries -- every loan payment created via
``loan.payment_transfer.create_payment_transfer`` set it, and no other template
does), preserving the live-derive flag exactly.  ``extra_principal`` starts at 0
(no existing overpayment).  A manual-mode loan payment (``derive_from_loan``
FALSE) gets no row and resolves to the same defaults it did through the column.
Idempotent (``NOT EXISTS`` guard) and self-contained (raw SQL, no ``app``
import), the discipline the escrow backfills use.

**Downgrade** drops the new table (its audit trigger cascades).  Fully
reversible with no data loss: ``transfer_templates.derive_from_loan`` was never
touched, so the source is intact and a re-upgrade rebuilds the table from it.
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = "b3e9d1f6a2c4"
down_revision = "d7b2f9a4c1e6"
branch_labels = None
depends_on = None


# One loan_payment_settings row per template with derive_from_loan = TRUE,
# carrying the flag forward and starting extra_principal at 0.  ``NOT EXISTS``
# makes it idempotent (a re-run after a partial failure inserts nothing new).
_BACKFILL_SQL = (
    "INSERT INTO budget.loan_payment_settings "
    "  (transfer_template_id, derive_from_loan, extra_principal, "
    "   created_at, updated_at) "
    "SELECT tt.id, true, 0, now(), now() "
    "FROM budget.transfer_templates tt "
    "WHERE tt.derive_from_loan = true "
    "  AND NOT EXISTS ( "
    "    SELECT 1 FROM budget.loan_payment_settings lps "
    "    WHERE lps.transfer_template_id = tt.id "
    "  )"
)


def _attach_audit_trigger(table: str) -> None:
    """Attach the shared audit trigger to a new ``budget`` table (idempotent).

    A narrow manual DROP+CREATE pair (the ``c4f8a1b6e9d2`` precedent) rather than
    ``apply_audit_infrastructure``, so an earlier fresh-DB replay of the rebuild
    migration is never asked to trigger a table that does not exist yet.

    Args:
        table: The ``budget``-schema table name to attach ``audit_<table>`` to.
    """
    op.execute(f"DROP TRIGGER IF EXISTS audit_{table} ON budget.{table}")
    op.execute(
        f"CREATE TRIGGER audit_{table} "
        f"AFTER INSERT OR UPDATE OR DELETE ON budget.{table} "
        "FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
    )


def upgrade():
    """Create ``loan_payment_settings`` + audit trigger, then backfill from the column.

    Additive only -- ``transfer_templates.derive_from_loan`` is untouched.  See
    the module docstring for the model and the backfill derivation.
    """
    op.create_table(
        "loan_payment_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("transfer_template_id", sa.Integer(), nullable=False),
        sa.Column(
            "derive_from_loan", sa.Boolean(), nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "extra_principal", sa.Numeric(precision=12, scale=2),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["transfer_template_id"], ["budget.transfer_templates.id"],
            ondelete="CASCADE",
            name="fk_loan_payment_settings_transfer_template_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "transfer_template_id",
            name="uq_loan_payment_settings_transfer_template_id",
        ),
        sa.CheckConstraint(
            "extra_principal >= 0",
            name="ck_loan_payment_settings_nonneg_extra_principal",
        ),
        schema="budget",
    )

    _attach_audit_trigger("loan_payment_settings")

    op.execute(sa.text(_BACKFILL_SQL))


def downgrade():
    """Drop ``loan_payment_settings`` (its audit trigger cascades with the table).

    Reversible with no data loss -- ``transfer_templates.derive_from_loan`` was
    never modified, so the source is intact and a re-upgrade rebuilds the table.
    """
    op.drop_table("loan_payment_settings", schema="budget")
