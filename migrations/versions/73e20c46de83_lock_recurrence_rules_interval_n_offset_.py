"""lock recurrence_rules interval_n offset_periods not null F-068

Closes the deep-quality-hunt #65 gap: ``budget.recurrence_rules.interval_n``
and ``offset_periods`` are logic-bearing integer divisors (interval_n is
the modulus in ``recurrence_engine.match_periods``; offset_periods shifts
the cycle) that carried a Python-side ``default=`` only -- the ORM fills
them on INSERT, but a raw-SQL INSERT or a ``pg_dump`` reload that omits
the column would land NULL, and the table's CHECK constraints
(``interval_n > 0`` / ``offset_periods >= 0``) accept NULL because a NULL
operand makes a CHECK predicate UNKNOWN, which PostgreSQL treats as
satisfied.  A NULL in either column has no defined meaning for the
recurrence engine.

This is the same F-068 class the 2026-04-15 C-25 sweep
(``c5d20b701a4e``) closed for the boolean / sort_order columns; these two
recurrence integers were outside that sweep's boolean/sort_order scope
and were left nullable.  This migration brings them in line: backfill any
existing NULL to the column's logical default, then ALTER each column to
``NOT NULL`` with a matching ``server_default`` so PostgreSQL enforces the
invariant regardless of who issued the INSERT.

Pre-flight semantics
--------------------

A NOT NULL constraint cannot be added when existing rows hold NULL, so an
idempotent ``UPDATE ... SET col = <default> WHERE col IS NULL`` runs for
each column inside the same transaction as the ALTER.  The backfill values
(``interval_n`` -> 1, ``offset_periods`` -> 0) are the model's Python-side
``default=`` -- the value the ORM has written for every row created via
the application since the column was introduced, and the value every
consumer already coalesces a NULL to (``rule.interval_n or 1`` /
``rule.offset_periods or 0``) -- so a backfilled row carries the same
value it would have had if the column were always NOT NULL.  The migration
is fully idempotent on a clean database: the UPDATEs are no-ops when no
NULLs exist, and ``op.alter_column`` accepts a column already NOT NULL
with the same ``server_default``.

Audit reference: docs/audits/pylint-cleanup/deep-quality-hunt.md #65
(F-068 class).

Revision ID: 73e20c46de83
Revises: aeb04f13caff
Create Date: 2026-06-08 11:32:35.386919
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = '73e20c46de83'
down_revision = 'aeb04f13caff'
branch_labels = None
depends_on = None


# Each entry: (column, server_default_text, backfill_literal_sql).
# server_default_text is the unquoted integer literal passed to
# sa.text(); backfill_literal_sql is substituted into the
# UPDATE ... WHERE col IS NULL guard and matches the model's default=.
_LOCK_SPECS: list[tuple[str, str, str]] = [
    ("interval_n", "1", "1"),
    ("offset_periods", "0", "0"),
]


def upgrade():
    """Backfill NULLs, then lock both columns NOT NULL with server_default.

    Backfill runs first so the subsequent ALTER cannot fail on a
    pre-existing NULL row.  Alembic wraps the whole upgrade in one
    transaction, so a failure in either phase rolls back atomically.
    """
    bind = op.get_bind()

    # 1. Backfill any pre-existing NULL to the logical default.
    for column, _server_default, backfill in _LOCK_SPECS:
        bind.execute(
            sa.text(
                f"UPDATE budget.recurrence_rules "
                f"SET {column} = {backfill} "
                f"WHERE {column} IS NULL"
            )
        )

    # 2. Lock NOT NULL + server_default.
    for column, server_default, _backfill in _LOCK_SPECS:
        op.alter_column(
            "recurrence_rules", column,
            existing_type=sa.Integer(),
            nullable=False,
            server_default=sa.text(server_default),
            schema="budget",
        )


def downgrade():
    """Relax both columns back to nullable and drop the server_default.

    Backfilled values remain in place -- the downgrade does not re-NULL
    any row.  Rolling forward again is a no-op on the backfill phase and
    idempotent on the ALTERs.
    """
    for column, _server_default, _backfill in reversed(_LOCK_SPECS):
        op.alter_column(
            "recurrence_rules", column,
            existing_type=sa.Integer(),
            nullable=True,
            server_default=None,
            schema="budget",
        )
