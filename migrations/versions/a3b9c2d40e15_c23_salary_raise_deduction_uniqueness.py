"""C-23 salary raise + paycheck deduction uniqueness constraints

Adds the two composite uniqueness backstops described in commit C-23
of the 2026-04-15 security remediation plan:

  1. ``uq_salary_raises_profile_type_year_month`` -- composite unique
     constraint on ``salary.salary_raises (salary_profile_id,
     raise_type_id, effective_year, effective_month)`` declared with
     PostgreSQL ``NULLS NOT DISTINCT`` semantics.  Closes F-051
     (Medium): a double-submit of the raise form would otherwise
     create two raise rows with identical effective dates, and the
     paycheck calculator would compound the raise twice
     (``salary * 1.03 * 1.03`` instead of ``salary * 1.03``),
     silently overstating projected gross pay.  ``effective_year``
     is nullable for recurring raises that fire each year on a
     given month with no anchored start year, so the SQL-standard
     "every NULL is distinct" default would let two recurring
     duplicates slip through; ``NULLS NOT DISTINCT`` forces them
     to collide, which is the intended semantics for this domain.

  2. ``uq_paycheck_deductions_profile_name`` -- composite unique
     constraint on ``salary.paycheck_deductions (salary_profile_id,
     name)``.  Closes F-052 (Medium): a double-submit of the
     deduction form would otherwise create two deductions with the
     same name and amount, and the paycheck calculator would
     subtract the deduction twice per paycheck, silently
     understating projected net pay.  Each deduction has exactly
     one canonical name per profile, so the constraint matches the
     domain.

Pre-flight checks: PostgreSQL refuses to create unique constraints
when existing rows violate the predicate, but the resulting error
points at the constraint name rather than the offending data.  Each
step here runs an explicit detection query first so the operator
receives the offending tuples and can plan a single cleanup pass.
The migration aborts with ``RuntimeError`` before any DDL runs when
violations are present -- per the ``docs/coding-standards.md`` rule
that destructive migrations require explicit approval, no row is
ever deleted automatically.  This mirrors the pre-flight pattern
established in commit C-22 (revision e8b14f3a7c22).

Audit reference: F-051 + F-052 / commit C-23 of the 2026-04-15
security remediation plan.

Revision ID: a3b9c2d40e15
Revises: e8b14f3a7c22
Create Date: 2026-05-07 21:30:00.000000
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = "a3b9c2d40e15"
down_revision = "e8b14f3a7c22"
branch_labels = None
depends_on = None


# ── Constraint names ──────────────────────────────────────────────
#
# Each literal must stay in sync with the corresponding model
# declaration in ``app/models/salary_raise.py`` and
# ``app/models/paycheck_deduction.py`` and with the route-layer
# ``is_unique_violation`` helper invocations in
# ``app/routes/salary.py``.

SALARY_RAISES_UNIQUE = "uq_salary_raises_profile_type_year_month"
PAYCHECK_DEDUCTIONS_UNIQUE = "uq_paycheck_deductions_profile_name"


def _refuse(label: str, rows, render):
    """Abort the migration with a structured error message.

    Used by every pre-flight to surface the offending tuples so the
    operator can resolve duplicates manually before retrying.

    Args:
        label: Short human label for the constraint being added.
        rows: Sequence of database rows from the detection query.
        render: Callable that turns one row into a one-line string.
    """
    details = "; ".join(render(r) for r in rows)
    raise RuntimeError(
        f"Refusing to add {label}: {len(rows)} pre-existing duplicate "
        f"group(s) violate the constraint.  Resolve them manually "
        f"(typically by deleting all but one of each set after "
        f"confirming with the user which row to keep) and rerun the "
        f"migration.  Offending rows: {details}."
    )


def upgrade():
    """Run both pre-flight detection queries, then create the constraints.

    Each pre-flight refuses the migration cleanly when violators are
    found; combined with the all-or-nothing transaction Alembic wraps
    around the upgrade, no partial state can be left in the database.
    """
    bind = op.get_bind()

    # ── 1. Salary-raise duplicates ────────────────────────────────
    # PostgreSQL's ``GROUP BY`` already treats NULLs as equal for
    # grouping purposes, which mirrors the post-migration constraint's
    # ``NULLS NOT DISTINCT`` semantics: a recurring raise with
    # ``effective_year IS NULL`` will group with another recurring
    # raise on the same ``(profile, type, month)`` and surface as a
    # duplicate exactly when the constraint would later reject the
    # second row.
    raise_dupes = bind.execute(
        sa.text(
            "SELECT salary_profile_id, raise_type_id, "
            "       effective_year, effective_month, COUNT(*) AS cnt "
            "FROM salary.salary_raises "
            "GROUP BY salary_profile_id, raise_type_id, "
            "         effective_year, effective_month "
            "HAVING COUNT(*) > 1 "
            "ORDER BY salary_profile_id, raise_type_id, "
            "         effective_year, effective_month"
        )
    ).fetchall()
    if raise_dupes:
        _refuse(
            SALARY_RAISES_UNIQUE,
            raise_dupes,
            lambda r: (
                f"profile={r[0]} type={r[1]} year={r[2]} "
                f"month={r[3]} count={r[4]}"
            ),
        )

    # ── 2. Paycheck-deduction duplicates ──────────────────────────
    deduction_dupes = bind.execute(
        sa.text(
            "SELECT salary_profile_id, name, COUNT(*) AS cnt "
            "FROM salary.paycheck_deductions "
            "GROUP BY salary_profile_id, name "
            "HAVING COUNT(*) > 1 "
            "ORDER BY salary_profile_id, name"
        )
    ).fetchall()
    if deduction_dupes:
        _refuse(
            PAYCHECK_DEDUCTIONS_UNIQUE,
            deduction_dupes,
            lambda r: (
                f"profile={r[0]} name={r[1]!r} count={r[2]}"
            ),
        )

    # ── Apply DDL ─────────────────────────────────────────────────

    # 1. Salary-raise composite unique with NULLS NOT DISTINCT.
    # Alembic's ``op.create_unique_constraint`` forwards the
    # ``postgresql_nulls_not_distinct`` flag to the dialect's DDL
    # compiler since 1.11; SQLAlchemy 2.0.13+ emits the
    # ``NULLS NOT DISTINCT`` clause for it.  Both versions are
    # already pinned in ``requirements.txt`` (alembic==1.18.4,
    # SQLAlchemy==2.0.49), so the keyword is honoured.
    op.create_unique_constraint(
        SALARY_RAISES_UNIQUE,
        "salary_raises",
        ["salary_profile_id", "raise_type_id",
         "effective_year", "effective_month"],
        schema="salary",
        postgresql_nulls_not_distinct=True,
    )

    # 2. Paycheck-deduction composite unique.
    op.create_unique_constraint(
        PAYCHECK_DEDUCTIONS_UNIQUE,
        "paycheck_deductions",
        ["salary_profile_id", "name"],
        schema="salary",
    )


def downgrade():
    """Drop both constraints this migration created.

    Drops are emitted in reverse order of creation so a partial
    failure produces a recognisable post-state for forensic review.
    """
    op.drop_constraint(
        PAYCHECK_DEDUCTIONS_UNIQUE,
        "paycheck_deductions",
        schema="salary",
        type_="unique",
    )
    op.drop_constraint(
        SALARY_RAISES_UNIQUE,
        "salary_raises",
        schema="salary",
        type_="unique",
    )
