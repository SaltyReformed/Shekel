"""add income-relative goal columns to savings_goals

Revision ID: 4f2d894216ad
Revises: 1dc0e7a1b9e4
Create Date: 2026-04-03 13:47:42.627695
"""
from alembic import op
import sqlalchemy as sa

# Revision identifiers, used by Alembic.
revision = '4f2d894216ad'
down_revision = '1dc0e7a1b9e4'
branch_labels = None
depends_on = None


def upgrade():
    """Add goal_mode_id, income_unit_id, income_multiplier to savings_goals.

    Also makes target_amount nullable (income-relative goals compute
    their target on read rather than storing it).

    The server_default='1' on goal_mode_id ensures all existing goals
    are assigned to the Fixed mode without a data backfill.  This
    depends on the ref.goal_modes seed assigning ID 1 to 'Fixed'.
    """
    # Verify that ID 1 in ref.goal_modes is "Fixed".  The server_default
    # below relies on this.  If the seed script has not run, this will
    # fail early with a clear message rather than silently assigning
    # the wrong mode to existing goals.
    conn = op.get_bind()
    result = conn.execute(
        sa.text("SELECT name FROM ref.goal_modes WHERE id = 1")
    )
    row = result.fetchone()
    if not row or row[0] != "Fixed":
        actual = repr(row[0]) if row else "no row"
        raise RuntimeError(
            f"Expected ref.goal_modes ID 1 to be 'Fixed', but got {actual}. "
            "Run seed_ref_tables.py before this migration."
        )

    # Add the three new columns.
    op.add_column(
        'savings_goals',
        sa.Column(
            'goal_mode_id', sa.Integer(),
            server_default='1', nullable=False,
        ),
        schema='budget',
    )
    op.add_column(
        'savings_goals',
        sa.Column('income_unit_id', sa.Integer(), nullable=True),
        schema='budget',
    )
    op.add_column(
        'savings_goals',
        sa.Column(
            'income_multiplier',
            sa.Numeric(precision=8, scale=2),
            nullable=True,
        ),
        schema='budget',
    )

    # Make target_amount nullable (income-relative goals have no stored target).
    op.alter_column(
        'savings_goals', 'target_amount',
        existing_type=sa.Numeric(precision=12, scale=2),
        nullable=True,
        schema='budget',
    )

    # FK constraints.
    op.create_foreign_key(
        'fk_savings_goals_goal_mode_id',
        'savings_goals', 'goal_modes',
        ['goal_mode_id'], ['id'],
        source_schema='budget', referent_schema='ref',
    )
    op.create_foreign_key(
        'fk_savings_goals_income_unit_id',
        'savings_goals', 'income_units',
        ['income_unit_id'], ['id'],
        source_schema='budget', referent_schema='ref',
    )

    # CHECK constraint: multiplier must be positive when present.
    op.create_check_constraint(
        'ck_savings_goals_multiplier_positive',
        'savings_goals',
        'income_multiplier IS NULL OR income_multiplier > 0',
        schema='budget',
    )


def downgrade():
    """Drop income-relative goal columns and revert target_amount to NOT NULL."""
    # Drop CHECK constraint first (references income_multiplier column).
    op.drop_constraint(
        'ck_savings_goals_multiplier_positive',
        'savings_goals',
        schema='budget',
        type_='check',
    )

    # Drop FK constraints.
    op.drop_constraint(
        'fk_savings_goals_income_unit_id',
        'savings_goals',
        schema='budget',
        type_='foreignkey',
    )
    op.drop_constraint(
        'fk_savings_goals_goal_mode_id',
        'savings_goals',
        schema='budget',
        type_='foreignkey',
    )

    # Restore target_amount to NOT NULL.  Any income-relative goals
    # with NULL target_amount get a placeholder value of 1 (the
    # minimum allowed by ck_savings_goals_positive_target).
    op.execute(
        "UPDATE budget.savings_goals "
        "SET target_amount = 1 WHERE target_amount IS NULL"
    )
    op.alter_column(
        'savings_goals', 'target_amount',
        existing_type=sa.Numeric(precision=12, scale=2),
        nullable=False,
        schema='budget',
    )

    # Drop the three columns.
    op.drop_column('savings_goals', 'income_multiplier', schema='budget')
    op.drop_column('savings_goals', 'income_unit_id', schema='budget')
    op.drop_column('savings_goals', 'goal_mode_id', schema='budget')
