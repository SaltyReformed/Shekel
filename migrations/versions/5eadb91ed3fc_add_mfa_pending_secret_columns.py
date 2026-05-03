"""add mfa_configs.pending_secret_encrypted and pending_secret_expires_at

Adds server-side storage for the in-progress TOTP secret captured
during ``/mfa/setup`` but not yet confirmed.  Before this migration the
plaintext secret was stored in ``flask_session["_mfa_setup_secret"]``,
which Flask only signs and does not encrypt -- the secret therefore sat
base64-decodable in the user's browser cookie for the duration of the
setup flow.  See audit finding F-031 (Medium) and commit C-05 of the
2026-04-15 security remediation plan.

Both columns are nullable.  Most rows in ``auth.mfa_configs`` represent
either a fully-enrolled MFA configuration or a never-started setup, and
neither state needs pending fields populated.  No backfill is required
because in-progress setups complete (or expire) within 15 minutes of
the migration; on the next request the user starts a fresh setup that
populates the new columns naturally.

Revision ID: 5eadb91ed3fc
Revises: cea9b9e31e88
Create Date: 2026-05-03 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = "5eadb91ed3fc"
down_revision = "cea9b9e31e88"
branch_labels = None
depends_on = None


def upgrade():
    """Add pending-secret columns to ``auth.mfa_configs``.

    Both columns are nullable so existing rows remain valid without a
    server-side default.  No CHECK constraint or index is added: the
    presence/absence of a pending secret is a business-logic state
    (not a domain invariant), and lookups always go through the
    ``user_id`` index that already exists on the table.
    """
    op.add_column(
        "mfa_configs",
        sa.Column(
            "pending_secret_encrypted",
            sa.LargeBinary(),
            nullable=True,
        ),
        schema="auth",
    )
    op.add_column(
        "mfa_configs",
        sa.Column(
            "pending_secret_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        schema="auth",
    )


def downgrade():
    """Remove the pending-secret columns.

    Dropping these columns is safe in either direction: the application
    code paired with this migration treats both columns as optional and
    the previous code path used a Flask session entry that does not
    depend on these columns at all.  Any in-progress setup is
    interrupted; affected users restart the /mfa/setup flow.
    """
    op.drop_column(
        "mfa_configs", "pending_secret_expires_at", schema="auth",
    )
    op.drop_column(
        "mfa_configs", "pending_secret_encrypted", schema="auth",
    )
