"""add account lockout columns to auth users

Adds two columns to ``auth.users`` that implement per-account brute-
force throttling, plus a CHECK constraint that pins the counter to a
non-negative range:

  * ``failed_login_count`` (Integer, NOT NULL, server_default=0)
    -- count of consecutive failed login attempts since the most
    recent successful authenticate() or lockout-trip.  Incremented in
    place by ``app/services/auth_service.py::authenticate`` on every
    bad-password failure; reset to 0 on a successful login or when
    the threshold trips and ``locked_until`` is stamped.
  * ``locked_until`` (DateTime(timezone=True), nullable)
    -- exclusive upper bound on the active lockout window, NULL when
    the account is not locked.  ``authenticate`` short-circuits with
    a generic ``AuthError`` (no bcrypt call, no timing-oracle leak)
    while ``locked_until > now``.
  * ``ck_users_failed_login_count_non_negative`` -- defensive CHECK
    that rejects negative values.  The service path only ever
    increments from a non-negative starting value, but a future raw-
    SQL backfill or buggy migration that wrote a negative would
    otherwise silently invert the lockout logic.

The two columns were added together because the lockout state is
meaningless without both: an attempt counter without a lockout
timestamp does nothing, and a lockout timestamp without a counter
cannot be reset to a "no lockout" baseline.

This migration backfills nothing because all existing rows are
brand-new from the developer's perspective.  The ``server_default='0'``
on ``failed_login_count`` covers any deployment that already carries
user rows: PostgreSQL applies the default to every existing row at
``ALTER TABLE ... ADD COLUMN`` time, so the NOT NULL constraint is
satisfied without a separate UPDATE step.

Closes audit finding F-033 / commit C-11 of the 2026-04-15 security
remediation plan.

Revision ID: d2883fc44071
Revises: 8a21d16c9bde
Create Date: 2026-05-04 23:33:25.193984
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = "d2883fc44071"
down_revision = "8a21d16c9bde"
branch_labels = None
depends_on = None


def upgrade():
    """Add ``failed_login_count``, ``locked_until``, and the CHECK constraint.

    PostgreSQL's transactional DDL means the migration either applies
    fully or rolls back entirely if any step fails, so the column
    additions and the CHECK creation are not split.  ``server_default``
    is preserved on ``failed_login_count`` so the application's own
    ``failed_login_count = (existing or 0) + 1`` increment in
    ``authenticate`` continues to start from a known-zero baseline on
    rows whose value was filled by the default.
    """
    op.add_column(
        "users",
        sa.Column(
            "failed_login_count", sa.Integer(),
            nullable=False, server_default="0",
        ),
        schema="auth",
    )
    op.add_column(
        "users",
        sa.Column(
            "locked_until", sa.DateTime(timezone=True),
            nullable=True,
        ),
        schema="auth",
    )
    op.create_check_constraint(
        "ck_users_failed_login_count_non_negative",
        "users",
        "failed_login_count >= 0",
        schema="auth",
    )


def downgrade():
    """Drop the CHECK constraint and both columns in reverse order.

    The CHECK is removed first because dropping ``failed_login_count``
    while a constraint references it would error in some PostgreSQL
    versions.  Both columns then come off; the table reverts to its
    pre-C-11 shape.  The downgrade is non-destructive in the sense
    that any future re-upgrade will re-apply the columns with the
    same defaults -- no lockout history is preserved across the
    downgrade because lockout state is ephemeral by design.
    """
    op.drop_constraint(
        "ck_users_failed_login_count_non_negative",
        "users",
        schema="auth",
    )
    op.drop_column("users", "locked_until", schema="auth")
    op.drop_column("users", "failed_login_count", schema="auth")
