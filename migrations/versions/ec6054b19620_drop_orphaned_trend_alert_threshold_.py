"""drop orphaned trend_alert_threshold setting

The spending trend engine's alert threshold lost its last reader when the
Trends tab retired (analytics Slice 4, 2026-07-05); the engine itself was
deleted with the S14/D7 spending rebuild (2026-07-10).  The settings form
kept saving the value, but nothing consumed it -- a write-only column.
This drops the column and its CHECK constraint root-and-branch alongside
the form field, schema field, and route plumbing.

Destructive: the drop discards any user-customized threshold.  Accepted --
the value has had no behavioral effect since 2026-07-05, and the downgrade
restores the column at its historical default (a per-user custom value is
not recoverable, which is why this ships only after the engine's removal).

Review: Josh (directed the removal of all three S14 follow-ups), 2026-07-10

Revision ID: ec6054b19620
Revises: c1a4f7b9e2d3
Create Date: 2026-07-10 19:06:11.542679
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = 'ec6054b19620'
down_revision = 'c1a4f7b9e2d3'
branch_labels = None
depends_on = None


def upgrade():
    """Drop the orphaned column and its CHECK constraint.

    The constraint references only this column, so PostgreSQL would drop
    it implicitly with the column; dropping it explicitly documents the
    intent and keeps the downgrade a byte-exact mirror.
    """
    op.drop_constraint(
        'ck_user_settings_valid_trend_threshold',
        'user_settings',
        schema='auth',
        type_='check',
    )
    op.drop_column('user_settings', 'trend_alert_threshold', schema='auth')


def downgrade():
    """Restore the column and constraint as f06bcc98bc3a created them.

    Existing rows come back at the historical server default (0.1000 =
    10%); a previously customized per-user value is not recoverable from
    a dropped column.
    """
    op.add_column(
        'user_settings',
        sa.Column(
            'trend_alert_threshold', sa.Numeric(precision=5, scale=4),
            server_default='0.1000', nullable=False,
        ),
        schema='auth',
    )
    op.create_check_constraint(
        'ck_user_settings_valid_trend_threshold',
        'user_settings',
        'trend_alert_threshold >= 0 AND trend_alert_threshold <= 1',
        schema='auth',
    )
