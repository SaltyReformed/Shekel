"""Add merit_raise_horizon_years to auth.user_settings

Gate A ruling 3 / fork F4: the retirement salary projection's user-facing
merit horizon.  Merit-type and custom-type raises apply only through
``current year + N`` (default 5); cola-type recurring raises still
extrapolate to the retirement date.

NOT NULL with a server default of 5 -- the static default fits every
existing row (no user has a horizon preference yet), so the column is
added in a single step, not the nullable-backfill-tighten sequence the
database rules require only when no static default fits.  The named CHECK
constraint pins the value to a 0-50 year range.

Revision ID: f7a1c2d3e4b5
Revises: e3c23fadb21d
Create Date: 2026-07-03
"""

from alembic import op
import sqlalchemy as sa

revision = "f7a1c2d3e4b5"
down_revision = "e3c23fadb21d"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user_settings",
        sa.Column(
            "merit_raise_horizon_years",
            sa.Integer(),
            nullable=False,
            server_default="5",
        ),
        schema="auth",
    )
    op.create_check_constraint(
        "ck_user_settings_valid_merit_horizon",
        "user_settings",
        "merit_raise_horizon_years >= 0 AND merit_raise_horizon_years <= 50",
        schema="auth",
    )


def downgrade():
    # Drop the CHECK first, then the column.  PostgreSQL would cascade the
    # single-column CHECK on the column drop, but dropping it explicitly
    # keeps the downgrade self-documenting and independent of that cascade
    # behaviour.
    op.drop_constraint(
        "ck_user_settings_valid_merit_horizon",
        "user_settings",
        schema="auth",
        type_="check",
    )
    op.drop_column(
        "user_settings", "merit_raise_horizon_years", schema="auth",
    )
