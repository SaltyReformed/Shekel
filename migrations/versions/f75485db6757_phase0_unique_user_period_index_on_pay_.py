"""phase0 unique user period_index on pay_periods

Phase 0 of the pay-period CRUD work (see
``docs/plans/implementation_plan_pay_period_crud.md``).  Upgrades the
non-unique ``idx_pay_periods_user_index`` on
``budget.pay_periods (user_id, period_index)`` to a UNIQUE constraint
``uq_pay_periods_user_index``.

Why this change:

  The balance resolver walks a user's pay periods ordered by
  ``period_index`` and trusts that order to be chronological
  (``balance_resolver.balance_as_of_date``): it breaks the walk on the
  first period whose ``start_date`` exceeds the as-of date, so a
  ``period_index`` that is not unique-and-monotonic per user silently
  drops periods from as-of balances.  That invariant -- one row per
  ``(user_id, period_index)`` -- was previously enforced only by
  application convention (``generate_pay_periods`` assigns ``max+1``).
  The pay-period CRUD work adds several period-appending paths (manual
  extend, regenerate, and the continuous rolling top-up); enforcing the
  invariant in the schema makes it impossible for ANY of them -- alone or
  racing concurrently -- to land two periods at the same index for a
  user.  The Phase 2 advisory lock then serves UX (a clean wait-and-noop
  instead of an IntegrityError) rather than being the sole correctness
  guard.

  The old non-unique index existed only for lookup; the UNIQUE
  constraint's backing index covers the same ``(user_id, period_index)``
  columns in the same order, so query plans that used the old index are
  unaffected.  The old index is dropped to avoid two redundant indexes on
  the same columns.

Safety: every shipped path assigns ``period_index = max + 1`` per user,
so production data is expected to be free of duplicate
``(user_id, period_index)`` pairs.  ``upgrade()`` verifies this FIRST and
refuses (``RuntimeError`` with the offending rows and a diagnostic query)
rather than letting ``CREATE UNIQUE`` fail with a less actionable
message -- the database rule that constraint additions on a populated
table verify before they apply.

Review: solo developer, 2026-06-13 (pay-period CRUD Phase 0).  Drops an
index and adds a UNIQUE constraint (constraint add + index drop per the
destructive-migration policy in ``docs/coding-standards.md``).  Downgrade
is symmetric and fully working: it drops the UNIQUE constraint and
recreates the original non-unique ``idx_pay_periods_user_index`` exactly
as ``9dea99d4e33e`` (initial schema) installed it.  No data is touched in
either direction.

Revision ID: f75485db6757
Revises: d410f6b9caa3
Create Date: 2026-06-13 16:30:08.231226
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = 'f75485db6757'
down_revision = 'd410f6b9caa3'
branch_labels = None
depends_on = None


_SCHEMA = "budget"
_TABLE = "pay_periods"
_OLD_INDEX = "idx_pay_periods_user_index"
_NEW_CONSTRAINT = "uq_pay_periods_user_index"
_COLUMNS = ["user_id", "period_index"]


def _constraint_exists(bind, schema: str, name: str) -> bool:
    """Return True iff ``schema.name`` is a constraint in ``pg_constraint``.

    Schema-qualified lookup (``pg_constraint`` is cluster-wide, so a bare
    ``conname`` match could collide across schemas).  Used to make
    ``upgrade``/``downgrade`` idempotent against a partially-applied DB.

    Args:
        bind: SQLAlchemy ``Connection``-like object exposing ``execute``.
        schema: Schema the constraint belongs to.
        name: Constraint name.

    Returns:
        True if a constraint with this name exists in this schema.
    """
    return bool(bind.execute(
        sa.text(
            "SELECT EXISTS ("
            "  SELECT 1 FROM pg_constraint cn "
            "  JOIN pg_class c ON c.oid = cn.conrelid "
            "  JOIN pg_namespace n ON n.oid = c.relnamespace "
            "  WHERE cn.conname = :name AND n.nspname = :schema"
            ")"
        ),
        {"schema": schema, "name": name},
    ).scalar())


def upgrade():
    """Verify no duplicate indices, drop the old index, add the UNIQUE constraint.

    Idempotent: if ``uq_pay_periods_user_index`` already exists the
    migration returns without touching the schema.  Otherwise it asserts
    the data is free of duplicate ``(user_id, period_index)`` pairs,
    drops the legacy non-unique index, and adds the UNIQUE constraint.

    Raises:
        RuntimeError: When the table holds any duplicate
            ``(user_id, period_index)`` pair, which would make the UNIQUE
            constraint unsatisfiable.
    """
    bind = op.get_bind()
    if _constraint_exists(bind, _SCHEMA, _NEW_CONSTRAINT):
        return

    duplicates = bind.execute(sa.text(
        "SELECT user_id, period_index, COUNT(*) AS n "
        "FROM budget.pay_periods "
        "GROUP BY user_id, period_index "
        "HAVING COUNT(*) > 1 "
        "ORDER BY user_id, period_index"
    )).fetchall()
    if duplicates:
        rows = ", ".join(
            f"(user_id={r[0]}, period_index={r[1]}, count={r[2]})"
            for r in duplicates
        )
        raise RuntimeError(
            f"Cannot add UNIQUE({', '.join(_COLUMNS)}) to "
            f"{_SCHEMA}.{_TABLE}: {len(duplicates)} duplicate "
            f"(user_id, period_index) pair(s) exist: {rows}.  Each pay "
            f"period must have a distinct period_index per user.  Resolve "
            f"the duplicates before applying this migration.  Diagnostic: "
            f"SELECT user_id, period_index, COUNT(*) FROM {_SCHEMA}.{_TABLE} "
            f"GROUP BY user_id, period_index HAVING COUNT(*) > 1;"
        )

    op.execute(f"DROP INDEX IF EXISTS {_SCHEMA}.{_OLD_INDEX}")
    op.create_unique_constraint(
        _NEW_CONSTRAINT, _TABLE, _COLUMNS, schema=_SCHEMA,
    )


def downgrade():
    """Drop the UNIQUE constraint and restore the non-unique index.

    Recreates ``idx_pay_periods_user_index`` exactly as the initial
    schema (``9dea99d4e33e``) installed it.  Pure DDL; no data touched.
    """
    bind = op.get_bind()
    if _constraint_exists(bind, _SCHEMA, _NEW_CONSTRAINT):
        op.drop_constraint(
            _NEW_CONSTRAINT, _TABLE, schema=_SCHEMA, type_="unique",
        )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS {_OLD_INDEX} "
        f"ON {_SCHEMA}.{_TABLE} (user_id, period_index)"
    )
