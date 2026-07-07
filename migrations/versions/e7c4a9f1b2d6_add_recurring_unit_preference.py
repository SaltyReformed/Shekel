"""Add recurring_show_per_paycheck to user_settings

Recurring cluster overhaul (Loop B, P1): the unified /templates surface
carries a Monthly / Per-paycheck unit toggle whose choice persists per
user.  This adds the boolean preference column; FALSE (the server
default) is the monthly lens that stands in for the retired /obligations
page.  The static default fits every existing row, so the column is
added in a single step.

Revision ID: e7c4a9f1b2d6
Revises: b7f3a2c1d4e5
Create Date: 2026-07-05
"""

from alembic import op
import sqlalchemy as sa

revision = "e7c4a9f1b2d6"
down_revision = "b7f3a2c1d4e5"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user_settings",
        sa.Column(
            "recurring_show_per_paycheck",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema="auth",
    )


def downgrade():
    op.drop_column(
        "user_settings", "recurring_show_per_paycheck", schema="auth",
    )
