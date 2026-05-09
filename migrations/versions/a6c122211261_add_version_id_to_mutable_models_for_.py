"""add version_id to mutable models for optimistic locking

Adds the optimistic-locking version counter that backs SQLAlchemy's
``__mapper_args__ = {"version_id_col": version_id}`` declaration on
every mutable model the audit plan requires it for:

  * ``budget.transactions``
  * ``budget.transfers``
  * ``budget.transaction_templates``
  * ``budget.transfer_templates``
  * ``budget.savings_goals``
  * ``budget.transaction_entries``
  * ``salary.salary_profiles``
  * ``salary.salary_raises``
  * ``salary.paycheck_deductions``

With this column in place every ORM-emitted UPDATE or DELETE on
these tables is automatically narrowed to ``WHERE id = ? AND
version_id = ?`` and atomically increments the stored counter;
concurrent requests that both load the same row at version N race
for the bump, the loser's WHERE matches zero rows, SQLAlchemy
raises ``StaleDataError``, and the calling route returns HTTP 409
Conflict (HTMX endpoints) or flash + redirect (full-page forms).

The same three column properties that are load-bearing for the
``Account.version_id`` invariant on commit C-17 apply here:

  * ``NOT NULL`` -- a NULL counter would silently disable the
    version check on the row (``WHERE version_id IS NULL`` does not
    match the SQLAlchemy-emitted comparison).  ``server_default='1'``
    fills the column at ALTER TABLE time so existing production rows
    pass the NOT NULL check immediately.
  * ``server_default='1'`` -- chosen rather than ``0`` so a casual
    reader who sees ``version_id = 1`` on a freshly-created row does
    not mistake the counter for "no updates yet" (which would invite
    a future caller to subtract from it).  SQLAlchemy increments by
    one per UPDATE; ``CHECK(version_id > 0)`` is a true invariant of
    the table and is asserted below.
  * ``Integer`` (signed 32-bit) -- the realistic upper bound on a
    counter that increments per row edit is in the tens of thousands
    per row over the lifetime of the project; a 32-bit counter does
    not overflow within any plausible deployment lifetime, and the
    ``Numeric``/``BigInteger`` alternatives would impose a wider
    column on every audit-log JSONB snapshot for no benefit.

Backfill: nothing.  PostgreSQL applies ``server_default`` to every
existing row at ``ALTER TABLE ... ADD COLUMN`` time, so the NOT
NULL constraint is satisfied for any deployment that already
carries rows in these tables without a separate UPDATE step.

Audit reference: F-010 (High) / commit C-18 of the 2026-04-15
security remediation plan.

Revision ID: a6c122211261
Revises: 861a48e11960
Create Date: 2026-05-06 22:12:30.103162
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = "a6c122211261"
down_revision = "861a48e11960"
branch_labels = None
depends_on = None


# (schema, table) pairs covered by this migration.  Ordered to keep
# the resulting CHECK constraint names predictable in diffs and to
# match the model file order in app/models/.  Adding a new entry
# here without a coordinated edit to the matching SQLAlchemy model
# (the column declaration AND ``__mapper_args__``) would leave the
# optimistic-lock contract half-built and is never the right move.
_VERSIONED_TABLES: tuple[tuple[str, str, str], ...] = (
    # (schema, table, check_constraint_name)
    ("budget", "transactions", "ck_transactions_version_id_positive"),
    ("budget", "transfers", "ck_transfers_version_id_positive"),
    (
        "budget",
        "transaction_templates",
        "ck_transaction_templates_version_id_positive",
    ),
    (
        "budget",
        "transfer_templates",
        "ck_transfer_templates_version_id_positive",
    ),
    ("budget", "savings_goals", "ck_savings_goals_version_id_positive"),
    (
        "budget",
        "transaction_entries",
        "ck_transaction_entries_version_id_positive",
    ),
    ("salary", "salary_profiles", "ck_salary_profiles_version_id_positive"),
    ("salary", "salary_raises", "ck_salary_raises_version_id_positive"),
    (
        "salary",
        "paycheck_deductions",
        "ck_paycheck_deductions_version_id_positive",
    ),
)


def upgrade():
    """Add ``version_id`` and the positivity CHECK to each table.

    Order matters within each table: the column must exist (and be
    filled by ``server_default``) before the CHECK runs, because
    PostgreSQL validates a CHECK against the existing row set the
    moment the constraint is created.  ``ALTER TABLE ... ADD COLUMN
    ... DEFAULT '1'`` writes 1 to every existing row in the same
    statement, so the subsequent CHECK is satisfied for both
    pre-existing rows (default-filled to 1) and rows yet to come
    (SQLAlchemy increments from 1 upward).

    Tables are processed in the order declared in ``_VERSIONED_TABLES``
    so the migration is deterministic across environments.
    """
    for schema, table, check_name in _VERSIONED_TABLES:
        op.add_column(
            table,
            sa.Column(
                "version_id", sa.Integer(),
                nullable=False, server_default="1",
            ),
            schema=schema,
        )
        op.create_check_constraint(
            check_name,
            table,
            "version_id > 0",
            schema=schema,
        )


def downgrade():
    """Drop the CHECK and then the column on each table, in reverse order.

    The CHECK is removed first because dropping the column while a
    constraint references it would error in some PostgreSQL
    versions.  After the downgrade the tables revert to their
    pre-C-18 shape; the optimistic-lock contract on the matching
    ``__mapper_args__`` declarations is then unmet, so callers must
    revert the model edits alongside any downgrade or accept that
    every UPDATE will fail to find the column at flush time.

    Reverse iteration matches the natural rollback ordering: the
    last table touched on upgrade is the first one undone here.
    """
    for schema, table, check_name in reversed(_VERSIONED_TABLES):
        op.drop_constraint(
            check_name,
            table,
            schema=schema,
        )
        op.drop_column(table, "version_id", schema=schema)
