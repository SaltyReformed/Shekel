"""Add default_grid_account_id to user_settings

Revision ID: d5e6f7a8b9c0
Revises: c3d4e5f6g7h8
Create Date: 2026-03-05
"""

from alembic import op
import sqlalchemy as sa

revision = "d5e6f7a8b9c0"
down_revision = "c3d4e5f6g7h8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user_settings",
        sa.Column(
            "default_grid_account_id",
            sa.Integer(),
            sa.ForeignKey("budget.accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        schema="auth",
    )

    # Data migration: set default to first active checking account per user.
    op.execute("""
        UPDATE auth.user_settings us
        SET default_grid_account_id = sub.account_id
        FROM (
            SELECT DISTINCT ON (a.user_id)
                a.user_id,
                a.id AS account_id
            FROM budget.accounts a
            JOIN ref.account_types at ON a.account_type_id = at.id
            WHERE at.name = 'checking'
              AND a.is_active = true
            ORDER BY a.user_id, a.sort_order, a.id
        ) sub
        WHERE us.user_id = sub.user_id
    """)


def downgrade():
    op.drop_column("user_settings", "default_grid_account_id", schema="auth")
