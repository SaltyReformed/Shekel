"""add tax_year to state_tax_configs

Revision ID: 7abcbf372fff
Revises: 02b1ff12b08c
Create Date: 2026-03-20 23:38:43.486072

Review: solo developer, 2026-05-11 (audit 2026-04-15, C-40 downgrade hardening)

C-40 / F-133 downgrade fix (2026-05-11): the previous downgrade
unconditionally recreated the narrower 2-column unique constraint
``(user_id, state_code)`` after dropping the wider 3-column form
``(user_id, state_code, tax_year)``.  Any user who created
``state_tax_configs`` rows for multiple tax_years after the upgrade
(which is the intended post-upgrade behaviour) would cause
``CREATE UNIQUE CONSTRAINT`` to fail with a UniqueViolation, leaving
the table in a half-migrated state: the new constraint dropped, the
old constraint not created, the ``tax_year`` column still present
but now without a uniqueness backstop.  The replacement raises
:class:`NotImplementedError` with the manual recovery procedure.
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = '7abcbf372fff'
down_revision = '02b1ff12b08c'
branch_labels = None
depends_on = None


def upgrade():
    """Apply forward migration."""
    # Add tax_year column with a default so existing rows get a value.
    op.add_column(
        'state_tax_configs',
        sa.Column('tax_year', sa.Integer(), nullable=False, server_default='2026'),
        schema='salary',
    )
    # Remove the server default after backfill.
    op.alter_column(
        'state_tax_configs', 'tax_year',
        server_default=None, schema='salary',
    )
    # Replace old unique constraint with one that includes tax_year.
    op.drop_constraint(
        'uq_state_tax_configs_user_state', 'state_tax_configs', schema='salary',
    )
    op.create_unique_constraint(
        'uq_state_tax_configs_user_state_year', 'state_tax_configs',
        ['user_id', 'state_code', 'tax_year'], schema='salary',
    )


def downgrade():
    """Refuse to auto-revert.  Downgrade is unsafe against post-upgrade data.

    The upgrade replaces a 2-column unique constraint
    ``(user_id, state_code)`` with a wider 3-column form
    ``(user_id, state_code, tax_year)``.  The intent of the change is
    to let a user configure different tax brackets per tax_year for
    the same state -- which means any user that exercised the feature
    after the upgrade now has multiple ``state_tax_configs`` rows
    with the same ``(user_id, state_code)`` pair but different
    ``tax_year`` values.  Recreating the narrower 2-column constraint
    would fail with a ``UniqueViolation`` and leave the table in a
    half-migrated state:

      * the wider 3-column constraint already dropped,
      * the narrower 2-column constraint refused by PostgreSQL,
      * the ``tax_year`` column still present but unprotected.

    To revert manually:

      1. Decide which row to keep per duplicate ``(user_id,
         state_code)`` pair (typically the row with the most recent
         ``tax_year``).  The duplicates can be found with:

           SELECT user_id, state_code, array_agg(tax_year ORDER BY tax_year DESC)
                  AS years
           FROM salary.state_tax_configs
           GROUP BY user_id, state_code
           HAVING count(*) > 1;

      2. ``DELETE`` the rows that will not be kept.

      3. Drop the wider unique constraint:

           ALTER TABLE salary.state_tax_configs
             DROP CONSTRAINT uq_state_tax_configs_user_state_year;

      4. Recreate the narrower unique constraint:

           ALTER TABLE salary.state_tax_configs
             ADD CONSTRAINT uq_state_tax_configs_user_state
               UNIQUE (user_id, state_code);

      5. Drop the ``tax_year`` column:

           ALTER TABLE salary.state_tax_configs DROP COLUMN tax_year;

    Steps 1-2 are the only ones that touch row data; steps 3-5 are
    pure DDL and are reversible in turn if needed.
    """
    raise NotImplementedError(
        "Migration 7abcbf372fff has no safe automatic downgrade.  The "
        "narrower 2-column unique constraint (user_id, state_code) "
        "cannot be recreated if any user has state_tax_configs rows "
        "for multiple tax_years for the same state -- which is the "
        "intended post-upgrade behaviour of this feature.  To revert "
        "manually:\n"
        "  1. Identify duplicate (user_id, state_code) pairs via "
        "     SELECT user_id, state_code, array_agg(tax_year) FROM "
        "     salary.state_tax_configs GROUP BY user_id, state_code "
        "     HAVING count(*) > 1;\n"
        "  2. DELETE the rows that will not be kept (typically all "
        "     except the most recent tax_year per pair).\n"
        "  3. ALTER TABLE salary.state_tax_configs DROP CONSTRAINT "
        "     uq_state_tax_configs_user_state_year;\n"
        "  4. ALTER TABLE salary.state_tax_configs ADD CONSTRAINT "
        "     uq_state_tax_configs_user_state UNIQUE (user_id, "
        "     state_code);\n"
        "  5. ALTER TABLE salary.state_tax_configs DROP COLUMN tax_year;"
    )
