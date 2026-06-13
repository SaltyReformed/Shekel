"""make low_balance_threshold NOT NULL with default 500

Revision ID: 0dfd2537fecb
Revises: c9f1a7b3d2e8
Create Date: 2026-06-12 21:14:01.302599

The ``auth.user_settings.low_balance_threshold`` column was nullable
with only a Python-side default of 500, so historical rows (and any row
inserted by a path that pre-dated the Python default) could carry NULL.
That forced divergent literal-500 fallbacks at every read site -- the
grid routes, the settings form, and the dashboard pulse chart's
threshold line (which drew no line at all when the value was NULL).

The developer ruling (2026-06-12) is that the dashboard chart's
low-balance threshold line must always track the configured setting.
Making the column NOT NULL with a whole-dollar server default of 500
removes the divergence: every settings row carries a concrete value, so
every read site is a plain read.

Forward: backfill any NULL to 500 (the existing Python-side default, so
no row's effective value changes), attach the server default, then set
NOT NULL after confirming zero NULLs survive.  Downgrade: drop the NOT
NULL and the server default but leave the data untouched (the backfilled
500s remain -- there is no information to restore them to NULL, and a
500 is the value those rows would already have had through the ORM).
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = '0dfd2537fecb'
down_revision = 'c9f1a7b3d2e8'
branch_labels = None
depends_on = None


# Whole-dollar fallback for the low-balance threshold, mirroring the
# model's Python-side default.  Backfilling NULL rows to this value
# leaves every existing row's effective threshold unchanged: a NULL
# column was already read as 500 by every consumer's fallback.
_DEFAULT_THRESHOLD = "500"


def upgrade():
    """Backfill NULLs, attach the server default, then set NOT NULL."""
    bind = op.get_bind()

    # Step 1: backfill existing NULLs to the Python-side default so the
    # NOT NULL constraint can be satisfied without changing any row's
    # effective value.
    bind.execute(
        sa.text(
            "UPDATE auth.user_settings "
            "SET low_balance_threshold = :default "
            "WHERE low_balance_threshold IS NULL"
        ),
        {"default": int(_DEFAULT_THRESHOLD)},
    )

    # Step 2 + 3: attach the server default and set NOT NULL.  Verify
    # zero NULLs survive before flipping the constraint -- if the
    # backfill above did not cover every row (an impossible state given
    # the single UPDATE, but the database is the source of truth) the
    # alter_column would raise an opaque IntegrityError; this guard
    # surfaces the diagnostic instead.
    remaining = bind.execute(
        sa.text(
            "SELECT count(*) FROM auth.user_settings "
            "WHERE low_balance_threshold IS NULL"
        )
    ).scalar()
    if remaining:
        raise RuntimeError(
            f"{remaining} auth.user_settings row(s) still have a NULL "
            "low_balance_threshold after the backfill; cannot set NOT "
            "NULL.  Inspect with: SELECT id, user_id FROM "
            "auth.user_settings WHERE low_balance_threshold IS NULL;"
        )

    op.alter_column(
        'user_settings', 'low_balance_threshold',
        existing_type=sa.INTEGER(),
        nullable=False,
        server_default=sa.text(_DEFAULT_THRESHOLD),
        schema='auth',
    )


def downgrade():
    """Drop the NOT NULL and the server default; leave data untouched."""
    op.alter_column(
        'user_settings', 'low_balance_threshold',
        existing_type=sa.INTEGER(),
        nullable=True,
        server_default=None,
        schema='auth',
    )
