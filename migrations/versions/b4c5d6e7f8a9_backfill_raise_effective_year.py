"""backfill NULL effective_year on recurring salary raises

Recurring raises require effective_year to compound correctly.
Sets it to the year the raise was created for any existing rows
where it was left NULL.

Revision ID: b4c5d6e7f8a9
Revises: 7abcbf372fff
Create Date: 2026-03-21 12:00:00.000000
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
    """No safe automatic downgrade — NULL effective_year was a bug."""
    pass
