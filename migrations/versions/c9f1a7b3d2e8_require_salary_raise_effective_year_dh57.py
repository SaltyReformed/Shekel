"""require salary_raises.effective_year (DH-#57): retire the NULL-year machinery

deep-quality-hunt #57.  ``salary.salary_raises.effective_year`` was
nullable to support recurring raises with no anchored start year, but
that capability was already dead:

  * the create/update Marshmallow schemas have required ``effective_year``
    since the C-24 security sweep -- the only write path, and the form
    input is ``required`` and defaults to the current year;
  * the C-24 backfill ``b4c5d6e7f8a9`` already eliminated every NULL
    recurring raise from the data ("Recurring raises require
    effective_year to compound correctly"); and
  * the calculator's NULL-year branch was degenerate -- a NULL-year
    recurring raise applied exactly once (``apply_raises``) while the
    salary forecast badged it as recurring every year
    (``_get_raise_event``), an internal contradiction.

So the column nullability, the ``IS NULL OR`` disjunct in
``ck_salary_raises_valid_effective_year``, and the ``NULLS NOT DISTINCT``
modifier on ``uq_salary_raises_profile_type_year_month`` were vestigial.
This migration finishes the C-24 decision: backfill any residual NULL to
the row's creation year (the same derivation ``b4c5d6e7f8a9`` used), make
the column NOT NULL, tighten the CHECK, and recreate the unique without
the now-moot NULLS NOT DISTINCT.

Review: solo developer, 2026-06-10 (deep-quality-hunt #57)

Revision ID: c9f1a7b3d2e8
Revises: b7d2f4a619c5
Create Date: 2026-06-10 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = 'c9f1a7b3d2e8'
down_revision = 'b7d2f4a619c5'
branch_labels = None
depends_on = None

CK_YEAR = "ck_salary_raises_valid_effective_year"
UQ_RAISE = "uq_salary_raises_profile_type_year_month"
UQ_COLS = [
    "salary_profile_id", "raise_type_id",
    "effective_year", "effective_month",
]


def upgrade():
    """Backfill residual NULLs, then require effective_year (NOT NULL).

    The C-24 backfill ``b4c5d6e7f8a9`` already set every recurring
    NULL-year row; this re-runs the same ``EXTRACT(YEAR FROM created_at)``
    derivation for ANY residual NULL (including a pre-C-24 one-time raise
    the earlier backfill's ``is_recurring = TRUE`` filter skipped -- such
    a row was dead data the calculator's now-removed ``eff_year is None:
    continue`` guard never applied), so the column can be made NOT NULL.
    A post-backfill guard fails loud if any NULL survives.
    """
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE salary.salary_raises "
            "SET effective_year = EXTRACT(YEAR FROM created_at)::INT "
            "WHERE effective_year IS NULL"
        )
    )
    remaining = bind.execute(
        sa.text(
            "SELECT count(*) FROM salary.salary_raises "
            "WHERE effective_year IS NULL"
        )
    ).scalar()
    if remaining:
        raise RuntimeError(
            f"{remaining} salary_raises row(s) still carry a NULL "
            "effective_year after backfill; cannot enforce NOT NULL.  "
            "Inspect: SELECT id, salary_profile_id, created_at FROM "
            "salary.salary_raises WHERE effective_year IS NULL;"
        )

    op.alter_column(
        "salary_raises", "effective_year",
        existing_type=sa.Integer(),
        nullable=False,
        schema="salary",
    )

    # Tighten the CHECK: the column can no longer be NULL, so drop the
    # ``IS NULL OR`` disjunct, leaving the plain 2000-2100 bound the
    # schema's Range mirrors.
    op.drop_constraint(CK_YEAR, "salary_raises", schema="salary", type_="check")
    op.create_check_constraint(
        CK_YEAR, "salary_raises",
        "effective_year >= 2000 AND effective_year <= 2100",
        schema="salary",
    )

    # Recreate the composite unique without NULLS NOT DISTINCT: with
    # effective_year (and every other key column) NOT NULL the modifier
    # is a no-op, so drop it for an honest constraint.
    op.drop_constraint(UQ_RAISE, "salary_raises", schema="salary", type_="unique")
    op.create_unique_constraint(
        UQ_RAISE, "salary_raises", UQ_COLS, schema="salary",
    )


def downgrade():
    """Restore the nullable column, NULL-admitting CHECK, and NN-distinct unique.

    Fully reversible at the schema tier: re-widening the CHECK to admit
    NULL and re-declaring ``NULLS NOT DISTINCT`` cannot violate any
    existing row (every row carries a concrete 2000-2100 year), and
    ``DROP NOT NULL`` is always safe.  The upgrade's backfilled
    ``effective_year`` values are deliberately NOT reverted to NULL --
    the same provenance problem ``b4c5d6e7f8a9`` documents (a
    post-migration row cannot be distinguished from a backfilled one),
    and the restored schema does not require it.
    """
    op.drop_constraint(UQ_RAISE, "salary_raises", schema="salary", type_="unique")
    op.create_unique_constraint(
        UQ_RAISE, "salary_raises", UQ_COLS, schema="salary",
        postgresql_nulls_not_distinct=True,
    )

    op.drop_constraint(CK_YEAR, "salary_raises", schema="salary", type_="check")
    op.create_check_constraint(
        CK_YEAR, "salary_raises",
        "effective_year IS NULL OR "
        "(effective_year >= 2000 AND effective_year <= 2100)",
        schema="salary",
    )

    op.alter_column(
        "salary_raises", "effective_year",
        existing_type=sa.Integer(),
        nullable=True,
        schema="salary",
    )
