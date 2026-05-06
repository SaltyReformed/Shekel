"""add mfa_configs.last_totp_timestep for replay prevention

Adds an integer column that records the highest 30-second TOTP time-
step ever accepted from a given ``MfaConfig`` row.  Subsequent
verifications must produce a strictly greater step or are rejected
as replays -- without this state the +-1 drift window built into
``pyotp.TOTP.verify`` leaves any observed code replayable for ~90
seconds.  See ASVS V2.8.4 and audit findings F-005, F-142 / commit
C-09 of the 2026-04-15 security remediation plan.

The column is nullable.  Pre-existing rows have no observed step
yet, and a NULL value means "the very first successful verify is
accepted unconditionally"; the verifier writes the matched step on
that first success and enforces strict-greater on every call after.
On ``/mfa/disable`` the column is reset to NULL so a re-enrollment
under a new secret does not inherit a stale step boundary from the
old secret.

No backfill is needed because no row is currently relying on a
particular last-step value, and CHECK constraints are not added
because the value is a Unix-time-derived integer with no domain
range to assert (the verifier overwrites it monotonically as the
authoritative source).

Revision ID: 6109f30127c3
Revises: 5eadb91ed3fc
Create Date: 2026-05-04 07:24:14.432080
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = "6109f30127c3"
down_revision = "5eadb91ed3fc"
branch_labels = None
depends_on = None


def upgrade():
    """Add the ``last_totp_timestep`` column to ``auth.mfa_configs``.

    The column is nullable so existing rows remain valid without a
    server-side default.  ``BigInteger`` is used in preference to
    ``Integer`` because TOTP steps are derived from the Unix epoch
    (``time.time() // 30``) and a 32-bit signed integer would
    overflow in approximately the year 4040 -- not an immediate
    concern, but ``BigInteger`` costs nothing on PostgreSQL (8 bytes
    vs 4 bytes per row, with rows in the dozens) and avoids ever
    having to widen the column later.
    """
    op.add_column(
        "mfa_configs",
        sa.Column(
            "last_totp_timestep",
            sa.BigInteger(),
            nullable=True,
        ),
        schema="auth",
    )


def downgrade():
    """Remove the ``last_totp_timestep`` column.

    Dropping the column is safe in either direction.  The previous
    code path verifies TOTP codes without consulting the column at
    all, so any in-flight authentication continues to work; the only
    user-visible effect is that observed codes regain their ~90-second
    replay window until the column is re-added.
    """
    op.drop_column(
        "mfa_configs", "last_totp_timestep", schema="auth",
    )
