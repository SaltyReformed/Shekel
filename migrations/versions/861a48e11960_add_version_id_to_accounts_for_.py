"""add version_id to accounts for optimistic locking

Adds the optimistic-locking version counter that backs SQLAlchemy's
``__mapper_args__ = {"version_id_col": version_id}`` declaration on
``app.models.account.Account``.  With this column in place every
ORM-emitted UPDATE or DELETE on ``budget.accounts`` is automatically
narrowed to ``WHERE id = ? AND version_id = ?`` and atomically
increments the stored counter; concurrent requests that both load the
same row at version N race for the bump, the loser's WHERE matches
zero rows, SQLAlchemy raises ``StaleDataError``, and the calling
route returns HTTP 409 Conflict.

Three properties of this column are load-bearing for the
optimistic-lock invariant and must not change without coordinated
edits to ``app/models/account.py`` and the routes in
``app/routes/accounts.py``:

  * ``NOT NULL`` -- a NULL counter would silently disable the
    version check on the row (``WHERE version_id IS NULL`` does not
    match the SQLAlchemy-emitted comparison).  ``server_default='1'``
    fills the column at ALTER TABLE time so existing production rows
    pass the NOT NULL check immediately.
  * ``server_default='1'`` -- chosen rather than ``0`` so a
    casual reader who sees ``version_id = 1`` on a freshly-created
    row does not mistake the counter for "no updates yet" (which
    would invite a future caller to subtract from it).  SQLAlchemy
    increments by one per UPDATE; ``CHECK(version_id > 0)`` is
    therefore a true invariant of the table and is asserted below.
  * ``Integer`` (signed 32-bit) -- the realistic upper bound on a
    counter that increments per anchor-balance edit is in the low
    thousands per account per year; a 32-bit counter does not
    overflow within any plausible deployment lifetime, and the
    ``Numeric``/``BigInteger`` alternatives would impose a wider
    column on every audit-log JSONB snapshot for no benefit.

Backfill: nothing.  PostgreSQL applies ``server_default`` to every
existing row at ``ALTER TABLE ... ADD COLUMN`` time, so the NOT
NULL constraint is satisfied for any deployment that already
carries account rows without a separate UPDATE step.

Audit reference: F-009 (High) / commit C-17 of the 2026-04-15
security remediation plan.

Revision ID: 861a48e11960
Revises: f96f26f326e4
Create Date: 2026-05-06 16:41:33.147252
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = "861a48e11960"
down_revision = "f96f26f326e4"
branch_labels = None
depends_on = None


def upgrade():
    """Add the ``version_id`` column and its positivity CHECK.

    Order matters: the column must exist (and be filled by
    ``server_default``) before the CHECK runs, because PostgreSQL
    validates a CHECK against the existing row set the moment the
    constraint is created.  ``ALTER TABLE ... ADD COLUMN ... DEFAULT
    '1'`` writes 1 to every existing row in the same statement, so
    the subsequent CHECK is satisfied for both pre-existing rows
    (default-filled to 1) and rows yet to come (SQLAlchemy
    increments from 1 upward).
    """
    op.add_column(
        "accounts",
        sa.Column(
            "version_id", sa.Integer(),
            nullable=False, server_default="1",
        ),
        schema="budget",
    )
    op.create_check_constraint(
        "ck_accounts_version_id_positive",
        "accounts",
        "version_id > 0",
        schema="budget",
    )


def downgrade():
    """Drop the CHECK and then the column, in reverse order.

    The CHECK is removed first because dropping the column while a
    constraint references it would error in some PostgreSQL
    versions.  After the downgrade the table reverts to its
    pre-C-17 shape; the optimistic-lock contract on
    ``Account.__mapper_args__`` is then unmet, so callers must
    revert the model edit alongside any downgrade or accept that
    every UPDATE will fail to find the column at flush time.
    """
    op.drop_constraint(
        "ck_accounts_version_id_positive",
        "accounts",
        schema="budget",
    )
    op.drop_column("accounts", "version_id", schema="budget")
