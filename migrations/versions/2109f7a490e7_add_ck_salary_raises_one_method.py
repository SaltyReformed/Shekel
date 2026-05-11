"""add ck_salary_raises_one_method CHECK constraint

Closes a model-vs-migration drift surfaced during the L-1
verification sweep (post-F6 comparison script run, 2026-05-10).

The model ``app/models/salary_raise.py`` declares the CHECK
constraint ``ck_salary_raises_one_method`` that enforces a
``SalaryRaise`` row carries exactly one of ``percentage`` or
``flat_amount`` (XOR-style: one non-NULL, the other NULL).  The
constraint is the storage-tier backstop for the paycheck calculator's
"raise applies as a percentage OR a flat dollar amount, never both
and never neither" invariant.  Without it a future caller could
insert a raise with both columns populated; the calculator would
silently apply only one (the percentage path is checked first), and
the user's projected gross pay would either understate or overstate
depending on which value mattered more.

The constraint was declared on the model from the start but never
added to a migration, so production has been accepting violator rows
for the lifetime of the salary_raises table.  The Marshmallow schema
in ``app/schemas/validation.py`` rejects them at the API tier, but
the storage-tier guarantee was missing.

Pre-flight detection refuses the upgrade when any pre-existing row
violates the predicate.  The ``_constraint_exists`` guard makes the
DDL idempotent against the test path
(``db.create_all()`` already materialises this constraint from the
inline model declaration).

Revision ID: 2109f7a490e7
Revises: 44893a9dbcc3
Create Date: 2026-05-10
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = "2109f7a490e7"
down_revision = "44893a9dbcc3"
branch_labels = None
depends_on = None


_PREDICATE = (
    "(percentage IS NOT NULL AND flat_amount IS NULL) OR "
    "(percentage IS NULL AND flat_amount IS NOT NULL)"
)

_DETECTION_SQL = (
    "SELECT id, salary_profile_id, percentage, flat_amount "
    "FROM salary.salary_raises "
    "WHERE NOT ("
    "  (percentage IS NOT NULL AND flat_amount IS NULL) OR "
    "  (percentage IS NULL AND flat_amount IS NOT NULL)"
    ") "
    "ORDER BY id"
)


def _constraint_exists(name: str, schema: str) -> bool:
    """Return True iff a constraint named ``name`` exists in ``schema``.

    Mirrors the helper in
    ``22b3dd9d9ed3_add_salary_schema_tables.py``.  Used to make the
    ADD CONSTRAINT idempotent against a database that already carries
    the constraint via the create_all path
    (``app/models/salary_raise.py`` declares it inline).
    """
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM pg_constraint c "
            "JOIN pg_namespace n ON c.connamespace = n.oid "
            "WHERE c.conname = :name AND n.nspname = :schema"
        ),
        {"name": name, "schema": schema},
    )
    return result.fetchone() is not None


def upgrade():
    """Pre-flight detect violators, then add ck_salary_raises_one_method.

    Pre-flight refuses the upgrade if any pre-existing row violates
    the XOR predicate -- the operator must resolve violators by hand
    (typically by setting one of the two columns to NULL after
    confirming with the user) per docs/coding-standards.md, which
    forbids auto-rewriting data.

    The DDL phase is guarded by ``_constraint_exists`` so the
    migration is a no-op against the test path (db.create_all()
    materialises the constraint from the inline declaration on the
    SalaryRaise model) and a real backfill on a fresh-from-migrations
    DB.
    """
    bind = op.get_bind()

    rows = bind.execute(sa.text(_DETECTION_SQL)).fetchall()
    if rows:
        rendered = "; ".join(
            "(" + ", ".join(repr(v) for v in row) + ")" for row in rows
        )
        raise RuntimeError(
            f"Refusing to add ck_salary_raises_one_method: "
            f"{len(rows)} existing row(s) violate the XOR predicate "
            f"(percentage XOR flat_amount).  Resolve every violator "
            f"by hand (set one column to NULL after confirming the "
            f"intended raise method with the user) and rerun the "
            f"migration.  Per docs/coding-standards.md the migration "
            f"never auto-rewrites data.  Offenders: " + rendered
        )

    if not _constraint_exists("ck_salary_raises_one_method", "salary"):
        op.create_check_constraint(
            "ck_salary_raises_one_method",
            "salary_raises",
            _PREDICATE,
            schema="salary",
        )


def downgrade():
    """Drop the constraint.

    Raw ``ALTER TABLE ... DROP CONSTRAINT IF EXISTS`` so the
    downgrade tolerates the constraint's absence on a database that
    did not carry it before the upgrade (the upgrade's
    ``_constraint_exists`` guard would have skipped the add).
    """
    op.execute(
        "ALTER TABLE salary.salary_raises "
        "DROP CONSTRAINT IF EXISTS ck_salary_raises_one_method"
    )
