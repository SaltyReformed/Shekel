"""add security event columns to auth users

Adds three columns and two CHECK constraints to ``auth.users`` that
back the in-app "was this you?" banner shown after a security-relevant
credential change:

  * ``last_security_event_at`` (DateTime(timezone=True), nullable)
    -- moment of the most recent password change, MFA enrol/disable,
    or backup-code regeneration.  NULL means no security event has
    ever been recorded for the row, which is the common case for
    fresh accounts.

  * ``last_security_event_kind`` (VARCHAR(50), nullable)
    -- short machine code naming the kind of change.  Constrained at
    the database tier to one of four whitelisted values via
    ``ck_users_security_event_kind`` so a future caller that bypasses
    ``app.utils.security_events.record_security_event`` cannot persist
    a kind the banner template cannot render.

  * ``last_security_event_acknowledged_at`` (DateTime(timezone=True),
    nullable) -- moment the user dismissed the banner.  NULL means
    "never dismissed".  Banner visibility compares this against
    ``last_security_event_at`` (strict less-than), so a fresh event
    re-shows the banner even after a prior dismissal.

Plus two CHECK constraints:

  * ``ck_users_security_event_kind`` -- whitelist of allowed kind
    values.  Conditional on the column being non-NULL so existing
    rows continue to satisfy the constraint.

  * ``ck_users_security_event_at_kind_paired`` -- pair invariant:
    ``last_security_event_at`` and ``last_security_event_kind`` are
    NULL together or non-NULL together.  Prevents a future caller
    from writing one without the other.

The acknowledged-at column does NOT participate in the pair CHECK
because "event recorded but not yet dismissed" is the state the
banner exists to surface; a NULL acknowledged-at against a non-NULL
event timestamp is the explicit signal that the banner should render.

Backfill: nothing.  All three columns default to NULL on existing
rows, which means no banner renders for users who have never
triggered a recorded event -- the correct UX (we did not capture
the timestamps before this commit, so we cannot honestly claim a
historical event).

Audit reference: F-091 (Low) / commit C-16 of the 2026-04-15
security remediation plan.

Revision ID: f96f26f326e4
Revises: a5be2a99ea14
Create Date: 2026-05-05 22:36:00.000000
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = "f96f26f326e4"
down_revision = "a5be2a99ea14"
branch_labels = None
depends_on = None


# Whitelist mirrored from app.utils.security_events.SecurityEventKind.
# Duplicated here (rather than imported) so the migration is hermetic
# and can run against a future codebase where the enum module has
# moved or grown.  Adding a kind requires both updating the enum
# (and the code paths that emit it) AND issuing a follow-up
# migration that drops and recreates this CHECK with the new value.
_KIND_WHITELIST = (
    "password_changed",
    "mfa_enabled",
    "mfa_disabled",
    "backup_codes_regenerated",
)


def _kind_check_sql() -> str:
    """Render the CHECK clause restricting last_security_event_kind.

    Builds a parenthesised IN-list from ``_KIND_WHITELIST`` so a
    future maintainer can extend the whitelist by adding a member
    rather than editing a string literal.
    """
    quoted = ", ".join(f"'{value}'" for value in _KIND_WHITELIST)
    return (
        "last_security_event_kind IS NULL OR "
        f"last_security_event_kind IN ({quoted})"
    )


def upgrade():
    """Add the three columns and the two CHECK constraints.

    Order matters: columns first, constraints second.  PostgreSQL's
    CHECK validation runs against the existing row set the moment
    the constraint is created, so adding the columns (which start
    out NULL on every row) must precede the constraint creation or
    the validation would error against rows that have not yet been
    touched.  All-NULL rows trivially satisfy both constraints
    because the kind whitelist is conditional on non-NULL and the
    pair invariant is "both NULL" on existing rows.
    """
    op.add_column(
        "users",
        sa.Column(
            "last_security_event_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        schema="auth",
    )
    op.add_column(
        "users",
        sa.Column(
            "last_security_event_kind",
            sa.String(length=50),
            nullable=True,
        ),
        schema="auth",
    )
    op.add_column(
        "users",
        sa.Column(
            "last_security_event_acknowledged_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        schema="auth",
    )
    op.create_check_constraint(
        "ck_users_security_event_kind",
        "users",
        _kind_check_sql(),
        schema="auth",
    )
    op.create_check_constraint(
        "ck_users_security_event_at_kind_paired",
        "users",
        "(last_security_event_at IS NULL) = "
        "(last_security_event_kind IS NULL)",
        schema="auth",
    )


def downgrade():
    """Drop both CHECK constraints, then both columns, in reverse order.

    The CHECKs are dropped first so column removal cannot trip the
    constraint validator on an intermediate row state.  Both columns
    then come off; any acknowledged-banner state is lost (the column
    holds no auditable financial data, so the loss is acceptable for
    a downgrade -- the rebuild migration will repopulate the columns
    as NULL on the next upgrade).
    """
    op.drop_constraint(
        "ck_users_security_event_at_kind_paired",
        "users",
        schema="auth",
    )
    op.drop_constraint(
        "ck_users_security_event_kind",
        "users",
        schema="auth",
    )
    op.drop_column(
        "users", "last_security_event_acknowledged_at", schema="auth",
    )
    op.drop_column(
        "users", "last_security_event_kind", schema="auth",
    )
    op.drop_column(
        "users", "last_security_event_at", schema="auth",
    )
