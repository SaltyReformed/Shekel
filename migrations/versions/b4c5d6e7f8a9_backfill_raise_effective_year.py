"""backfill NULL effective_year on recurring salary raises

Recurring raises require effective_year to compound correctly.
Sets it to the year the raise was created for any existing rows
where it was left NULL.

Revision ID: b4c5d6e7f8a9
Revises: 7abcbf372fff
Create Date: 2026-03-21 12:00:00.000000

Review: solo developer, 2026-05-11 (audit 2026-04-15, C-40 downgrade hardening)

C-40 / F-131 downgrade fix (2026-05-11): the previous downgrade was
a bare ``pass``, which violates the project's "downgrade must revert
exactly or raise NotImplementedError" rule.  A bare ``pass`` silently
returns success and lets ``flask db downgrade`` chain past this
migration, which would mislead an operator into believing the
``effective_year`` column had been rolled back when in reality it
still carries the backfilled values.  The replacement raises
:class:`NotImplementedError` with the manual recovery SQL and the
provenance hint (system.audit_log) the operator needs to identify
which rows to revert.
"""
from alembic import op


# Revision identifiers, used by Alembic.
revision = 'b4c5d6e7f8a9'
down_revision = '7abcbf372fff'
branch_labels = None
depends_on = None


def upgrade():
    """Backfill effective_year from created_at for recurring raises."""
    op.execute(
        """
        UPDATE salary.salary_raises
        SET effective_year = EXTRACT(YEAR FROM created_at)::INT
        WHERE is_recurring = TRUE AND effective_year IS NULL
        """
    )


def downgrade():
    """Refuse to auto-revert.  A blind revert would corrupt legitimate data.

    The upgrade replaces NULL ``effective_year`` with
    ``EXTRACT(YEAR FROM created_at)`` for every recurring raise.  An
    automatic downgrade cannot distinguish a row that was *backfilled*
    here from a row that was *inserted later* with an explicit
    ``effective_year`` that happens to equal its ``created_at`` year.
    Setting every recurring raise's ``effective_year`` back to NULL
    would therefore wipe legitimate post-migration values, breaking
    compound-raise projections for users whose raises were entered
    after this migration ran.

    To revert manually, identify the affected row ids from either a
    pre-migration database snapshot or the system.audit_log rows
    captured by the audit trigger:

        SELECT (new_data->>'id')::INT AS raise_id
        FROM system.audit_log
        WHERE table_name = 'salary_raises'
          AND operation = 'UPDATE'
          AND old_data->>'effective_year' IS NULL
          AND new_data->>'effective_year' IS NOT NULL;

    Then revert only those rows:

        UPDATE salary.salary_raises
        SET effective_year = NULL
        WHERE id IN (<list of row ids from the SELECT above>);

    Rows inserted after this migration are untouched.
    """
    raise NotImplementedError(
        "Migration b4c5d6e7f8a9 has no safe automatic downgrade.  The "
        "upgrade backfilled NULL effective_year values on recurring "
        "salary_raises rows; reverting blindly would NULL out "
        "legitimate values inserted after the migration whose "
        "effective_year happens to equal EXTRACT(YEAR FROM created_at).  "
        "To revert manually, identify the affected row ids from "
        "system.audit_log "
        "(table_name='salary_raises', operation='UPDATE', "
        "old_data->>'effective_year' IS NULL and "
        "new_data->>'effective_year' IS NOT NULL) and run:\n"
        "    UPDATE salary.salary_raises SET effective_year = NULL\n"
        "    WHERE id IN (<list of affected row ids>);"
    )
