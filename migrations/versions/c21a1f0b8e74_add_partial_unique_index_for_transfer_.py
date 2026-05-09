"""add partial unique index for transfer shadow transactions

Adds ``uq_transactions_transfer_type_active`` -- a partial unique
index on ``budget.transactions (transfer_id, transaction_type_id)``
whose predicate matches the lifetime of an active shadow row::

    transfer_id IS NOT NULL AND is_deleted = FALSE

This index is the database-level backstop for the service-layer
invariant (CLAUDE.md "Transfer Invariants" #1) that every transfer
has exactly two linked shadow transactions -- one expense and one
income.  Without it a defective caller (or a hypothetical script
that bypasses ``transfer_service``) could insert a third active
shadow row of either type and silently double-count the transfer in
balance projections.  ``transfer_service.update_transfer`` already
verifies the (= 2) shadow count via ``_get_shadow_transactions``,
but the moment a row reaches the database the constraint must hold
regardless of caller path.

Predicate rationale -- the index permits the legitimate states
already exercised by the application:

  * ``transfer_id IS NULL`` -- regular (non-transfer) transactions.
    These rows are not shadows and the constraint must not apply.
  * ``is_deleted = TRUE`` -- soft-deleted shadows remain in the table
    indefinitely (the audit trail and ``restore_transfer`` workflow
    both rely on the row staying queryable).  After the parent
    transfer is soft-deleted, ``create_transfer`` may legitimately
    create a fresh transfer (with two fresh active shadows) on the
    same ``from_account_id``/``to_account_id`` pairing; excluding
    deleted rows from the index lets that succeed.
  * Active shadows (``transfer_id IS NOT NULL`` AND
    ``is_deleted = FALSE``) -- the index enforces at most one active
    expense shadow and one active income shadow per parent transfer.

Pre-flight check: PostgreSQL refuses to create a unique index when
existing rows violate the predicate, but the resulting error message
points at the index name rather than the offending data.  This
migration runs an explicit detection query first so the operator
receives the (transfer_id, transaction_type_id, count) tuples for
each violation before the failed CREATE INDEX, and can run a one-off
cleanup before retrying.

Audit reference: F-046 (Medium) / commit C-21 of the 2026-04-15
security remediation plan.

Revision ID: c21a1f0b8e74
Revises: b3d8f4a01c92
Create Date: 2026-05-06 22:18:00.000000
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = "c21a1f0b8e74"
down_revision = "b3d8f4a01c92"
branch_labels = None
depends_on = None


# Index name -- must stay in sync with the literal in
# ``app/models/transaction.py:Transaction.__table_args__``.
INDEX_NAME = "uq_transactions_transfer_type_active"


# Predicate text -- identical string in upgrade and downgrade so
# Alembic's autogenerate diff would not flag spurious differences if
# a future regeneration runs against this migration's resulting
# schema.
INDEX_PREDICATE = (
    "transfer_id IS NOT NULL "
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
    # exist.  Surfacing every offender in one query lets the operator
    # plan a single cleanup pass instead of resolving them
    # iteratively.
    duplicates = bind.execute(
        sa.text(
            "SELECT transfer_id, transaction_type_id, COUNT(*) AS cnt "
            "FROM budget.transactions "
            "WHERE transfer_id IS NOT NULL "
            "  AND is_deleted = FALSE "
            "GROUP BY transfer_id, transaction_type_id "
            "HAVING COUNT(*) > 1 "
            "ORDER BY transfer_id, transaction_type_id"
        )
    ).fetchall()
    if duplicates:
        details = ", ".join(
            f"transfer_id={row[0]} "
            f"transaction_type_id={row[1]} "
            f"count={row[2]}"
            for row in duplicates
        )
        raise RuntimeError(
            "Refusing to create "
            f"{INDEX_NAME}: "
            f"{len(duplicates)} (transfer_id, transaction_type_id) "
            "tuple(s) already have more than one active shadow row.  "
            "Resolve the duplicates (typically by soft-deleting all "
            "but one of each set, after confirming with the user "
            "which shadow they intend to keep) and rerun the "
            f"migration.  Offending rows: {details}."
        )

    op.create_index(
        INDEX_NAME,
        "transactions",
        ["transfer_id", "transaction_type_id"],
        unique=True,
        schema="budget",
        postgresql_where=sa.text(INDEX_PREDICATE),
    )


def downgrade():
    """Drop the partial unique index.

    The predicate is restated on the drop so PostgreSQL's
    named-index catalog match succeeds regardless of how the
    upstream Alembic version recorded the index definition.
    ``op.drop_index`` ignores ``postgresql_where`` for the drop
    itself, but supplying it keeps the schema-level pair with the
    upgrade symmetric for any future autogenerate run -- mirroring
    the pattern established by ``b3d8f4a01c92``.
    """
    op.drop_index(
        INDEX_NAME,
        table_name="transactions",
        schema="budget",
        postgresql_where=sa.text(INDEX_PREDICATE),
    )
