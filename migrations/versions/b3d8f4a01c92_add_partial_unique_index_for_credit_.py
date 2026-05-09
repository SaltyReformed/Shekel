"""add partial unique index for credit_payback_for_id

Adds ``uq_transactions_credit_payback_unique`` -- a partial unique
index on ``budget.transactions.credit_payback_for_id`` whose predicate
matches the lifetime of an active payback row:

    credit_payback_for_id IS NOT NULL AND is_deleted = FALSE

This index is the database-level backstop that makes the duplicate
CC-payback bug (audit finding F-008 / commit C-19) impossible.
``credit_workflow.mark_as_credit`` and
``entry_credit_workflow.sync_entry_payback`` now bracket their
read-then-insert with ``SELECT ... FOR UPDATE`` against the source
transaction row, so two concurrent requests serialise; the partial
index catches the residual case where any future caller reaches the
INSERT without that lock and the resulting ``IntegrityError`` is
funnelled by the route layer back into idempotent success.

Predicate rationale -- the index permits the legitimate states
already exercised by the application:

  * ``credit_payback_for_id IS NULL`` -- regular transactions and
    paybacks pre-link.  These rows are not paybacks and the
    constraint must not apply.
  * ``is_deleted = TRUE`` -- soft-deleted paybacks remain in the
    table indefinitely (the audit trail and balance calculator both
    rely on the row staying queryable).  After a payback is
    soft-deleted, ``mark_as_credit`` may legitimately create a
    fresh active payback for the same source row; excluding deleted
    rows from the index lets that re-mark succeed.
  * Active paybacks (``credit_payback_for_id IS NOT NULL`` AND
    ``is_deleted = FALSE``) -- the index enforces at most one of
    these per source transaction.

Pre-flight check: PostgreSQL refuses to create a unique index when
existing rows violate the predicate, but the resulting error message
points at the index name rather than the offending data.  This
migration runs an explicit detection query first so the operator
receives a list of source-transaction IDs with their duplicate
counts before the failed CREATE INDEX, and can run a one-off
cleanup before retrying the migration.

Audit reference: F-008 (High) / commit C-19 of the 2026-04-15
security remediation plan.

Revision ID: b3d8f4a01c92
Revises: a6c122211261
Create Date: 2026-05-06 19:15:00.000000
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = "b3d8f4a01c92"
down_revision = "a6c122211261"
branch_labels = None
depends_on = None


# Index name -- referenced by both upgrade/downgrade in this file
# and by the route-layer IntegrityError handler that translates a
# violation of this index into "payback already exists" idempotent
# success.  Keep the name identical here, in
# ``app/models/transaction.py:Transaction.__table_args__``, and in
# the routes that catch the IntegrityError.
INDEX_NAME = "uq_transactions_credit_payback_unique"


# Predicate text -- same string in upgrade and downgrade so Alembic's
# autogenerate diff would not flag spurious differences if a future
# regeneration runs against this migration's resulting schema.
INDEX_PREDICATE = (
    "credit_payback_for_id IS NOT NULL "
    "AND is_deleted = FALSE"
)


def upgrade():
    """Create the partial unique index after a duplicate-detection guard.

    The pre-flight query short-circuits the migration with a clear
    error message when active duplicates already exist in production
    data.  Without it, ``CREATE UNIQUE INDEX`` would surface a
    generic ``CardinalityViolation`` and the operator would have to
    write the diagnostic query themselves.
    """
    bind = op.get_bind()

    # Pre-flight: refuse to create the index when duplicates already
    # exist.  We surface every offender (source txn id + count) so a
    # one-off cleanup can be planned in a single pass instead of
    # resolving them iteratively.
    duplicates = bind.execute(
        sa.text(
            "SELECT credit_payback_for_id, COUNT(*) AS cnt "
            "FROM budget.transactions "
            "WHERE credit_payback_for_id IS NOT NULL "
            "  AND is_deleted = FALSE "
            "GROUP BY credit_payback_for_id "
            "HAVING COUNT(*) > 1 "
            "ORDER BY credit_payback_for_id"
        )
    ).fetchall()
    if duplicates:
        details = ", ".join(
            f"source_txn_id={row[0]} count={row[1]}" for row in duplicates
        )
        raise RuntimeError(
            "Refusing to create "
            f"{INDEX_NAME}: "
            f"{len(duplicates)} source transaction(s) already have "
            "more than one active CC Payback row. Resolve the "
            "duplicates (typically by soft-deleting all but one of "
            "each set, after confirming with the user which payback "
            "they intend to keep) and rerun the migration. "
            f"Offending rows: {details}."
        )

    op.create_index(
        INDEX_NAME,
        "transactions",
        ["credit_payback_for_id"],
        unique=True,
        schema="budget",
        postgresql_where=sa.text(INDEX_PREDICATE),
    )


def downgrade():
    """Drop the partial unique index.

    The predicate is restated on the drop so PostgreSQL's named-index
    catalog match succeeds regardless of how the upstream Alembic
    version recorded the index definition.  ``op.drop_index`` ignores
    ``postgresql_where`` for the drop itself, but supplying it keeps
    the schema-level pair with the upgrade symmetric for any future
    autogenerate run.
    """
    op.drop_index(
        INDEX_NAME,
        table_name="transactions",
        schema="budget",
        postgresql_where=sa.text(INDEX_PREDICATE),
    )
