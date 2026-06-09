"""promote employer_contribution_type and compounding_frequency to ref tables (#38)

Revision ID: 91fda897a32d
Revises: 73e20c46de83
Create Date: 2026-06-08 13:51:47.126526

Review: SaltyReformed, 2026-06-08

Destructive: drops the free-string columns
``budget.investment_params.employer_contribution_type`` and
``budget.interest_params.compounding_frequency`` (and their
``IN (...)`` CHECK constraints) after promoting both to ref tables with
integer-FK columns, so the growth/interest engines branch on IDs rather
than string literals (deep-quality-hunt #38 -- the IDs-for-logic /
full-normalization mandate).  The data is preserved by an in-migration
name->id backfill in both directions; the downgrade restores the exact
prior string columns, their server_defaults, and their CHECKs.

Seed rows are inserted here (explicit ids, matching the goal_modes
pattern in 1dc0e7a1b9e4) so the backfill can resolve name->id within the
migration chain itself; ``app.ref_seeds.seed_reference_data`` is
idempotent and skips these rows on its next run.
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = '91fda897a32d'
down_revision = '73e20c46de83'
branch_labels = None
depends_on = None


def _assert_no_nulls(column, table, schema='budget'):
    """Fail loud if any row's backfilled FK / restored string is NULL.

    A surviving NULL means a value did not map across the name<->id
    backfill (e.g. an out-of-vocabulary string the old CHECK should have
    forbidden).  Per the migration rules we surface the count and the
    diagnostic SELECT rather than letting the subsequent NOT NULL alter
    fail with an opaque error.
    """
    bind = op.get_bind()
    remaining = bind.execute(
        sa.text(
            f"SELECT COUNT(*) FROM {schema}.{table} WHERE {column} IS NULL"
        )
    ).scalar()
    if remaining:
        raise RuntimeError(
            f"{remaining} row(s) in {schema}.{table} have a NULL {column} "
            f"after backfill -- an unmapped value exists.  Inspect with: "
            f"SELECT id FROM {schema}.{table} WHERE {column} IS NULL;"
        )


def upgrade():
    """Create the two ref tables and migrate both columns to FK ids."""
    # 1. Reference tables (id + unique name), seeded with explicit ids so
    #    the backfill below can resolve the prior string literals.
    op.create_table(
        'employer_contribution_types',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
        schema='ref',
    )
    op.create_table(
        'compounding_frequencies',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=12), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
        schema='ref',
    )
    op.execute(
        "INSERT INTO ref.employer_contribution_types (id, name) VALUES "
        "(1, 'none'), (2, 'flat_percentage'), (3, 'match')"
    )
    op.execute(
        "INSERT INTO ref.compounding_frequencies (id, name) VALUES "
        "(1, 'daily'), (2, 'monthly'), (3, 'quarterly')"
    )

    # 2. Add the FK columns nullable, backfill name->id, then lock down.
    op.add_column(
        'investment_params',
        sa.Column('employer_contribution_type_id', sa.Integer(), nullable=True),
        schema='budget',
    )
    op.add_column(
        'interest_params',
        sa.Column('compounding_frequency_id', sa.Integer(), nullable=True),
        schema='budget',
    )
    op.execute(
        "UPDATE budget.investment_params AS ip "
        "SET employer_contribution_type_id = ect.id "
        "FROM ref.employer_contribution_types AS ect "
        "WHERE ip.employer_contribution_type = ect.name"
    )
    op.execute(
        "UPDATE budget.interest_params AS ipa "
        "SET compounding_frequency_id = cf.id "
        "FROM ref.compounding_frequencies AS cf "
        "WHERE ipa.compounding_frequency = cf.name"
    )
    _assert_no_nulls('employer_contribution_type_id', 'investment_params')
    _assert_no_nulls('compounding_frequency_id', 'interest_params')

    op.create_foreign_key(
        'fk_investment_params_employer_contribution_type',
        'investment_params', 'employer_contribution_types',
        ['employer_contribution_type_id'], ['id'],
        source_schema='budget', referent_schema='ref', ondelete='RESTRICT',
    )
    op.create_foreign_key(
        'fk_interest_params_compounding_frequency',
        'interest_params', 'compounding_frequencies',
        ['compounding_frequency_id'], ['id'],
        source_schema='budget', referent_schema='ref', ondelete='RESTRICT',
    )
    op.alter_column(
        'investment_params', 'employer_contribution_type_id',
        nullable=False, schema='budget',
    )
    op.alter_column(
        'interest_params', 'compounding_frequency_id',
        nullable=False, schema='budget',
    )

    # 3. Drop the old free-string columns and their IN (...) CHECKs.
    op.drop_constraint(
        'ck_investment_params_employer_type', 'investment_params',
        schema='budget', type_='check',
    )
    op.drop_constraint(
        'ck_interest_params_frequency', 'interest_params',
        schema='budget', type_='check',
    )
    op.drop_column(
        'investment_params', 'employer_contribution_type', schema='budget',
    )
    op.drop_column(
        'interest_params', 'compounding_frequency', schema='budget',
    )


def downgrade():
    """Restore the prior free-string columns from the FK ids, then drop the FKs/tables."""
    # 1. Re-add the string columns (nullable + the original
    #    server_defaults), backfill id->name, then lock down.
    op.add_column(
        'investment_params',
        sa.Column(
            'employer_contribution_type', sa.String(length=20),
            nullable=True, server_default=sa.text("'none'"),
        ),
        schema='budget',
    )
    op.add_column(
        'interest_params',
        sa.Column(
            'compounding_frequency', sa.String(length=10),
            nullable=True, server_default=sa.text("'daily'"),
        ),
        schema='budget',
    )
    op.execute(
        "UPDATE budget.investment_params AS ip "
        "SET employer_contribution_type = ect.name "
        "FROM ref.employer_contribution_types AS ect "
        "WHERE ip.employer_contribution_type_id = ect.id"
    )
    op.execute(
        "UPDATE budget.interest_params AS ipa "
        "SET compounding_frequency = cf.name "
        "FROM ref.compounding_frequencies AS cf "
        "WHERE ipa.compounding_frequency_id = cf.id"
    )
    _assert_no_nulls('employer_contribution_type', 'investment_params')
    _assert_no_nulls('compounding_frequency', 'interest_params')
    op.alter_column(
        'investment_params', 'employer_contribution_type',
        nullable=False, schema='budget',
    )
    op.alter_column(
        'interest_params', 'compounding_frequency',
        nullable=False, schema='budget',
    )

    # 2. Restore the original IN (...) CHECK constraints.
    op.create_check_constraint(
        'ck_investment_params_employer_type', 'investment_params',
        "employer_contribution_type IN ('none', 'flat_percentage', 'match')",
        schema='budget',
    )
    op.create_check_constraint(
        'ck_interest_params_frequency', 'interest_params',
        "compounding_frequency IN ('daily', 'monthly', 'quarterly')",
        schema='budget',
    )

    # 3. Drop the FK columns (and their constraints) and the ref tables.
    op.drop_constraint(
        'fk_investment_params_employer_contribution_type',
        'investment_params', schema='budget', type_='foreignkey',
    )
    op.drop_constraint(
        'fk_interest_params_compounding_frequency',
        'interest_params', schema='budget', type_='foreignkey',
    )
    op.drop_column(
        'investment_params', 'employer_contribution_type_id', schema='budget',
    )
    op.drop_column(
        'interest_params', 'compounding_frequency_id', schema='budget',
    )
    op.drop_table('compounding_frequencies', schema='ref')
    op.drop_table('employer_contribution_types', schema='ref')
