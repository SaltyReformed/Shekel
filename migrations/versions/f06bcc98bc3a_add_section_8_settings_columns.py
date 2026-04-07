"""add section 8 settings columns

Revision ID: f06bcc98bc3a
Revises: 2c1115378030
Create Date: 2026-04-07 16:54:07.339603
"""
from alembic import op
import sqlalchemy as sa

# Revision identifiers, used by Alembic.
revision = 'f06bcc98bc3a'
down_revision = '2c1115378030'
branch_labels = None
depends_on = None


def upgrade():
    """Add Section 8 settings columns to auth.user_settings.

    Three user-configurable thresholds consumed by the dashboard,
    financial calendar, and spending trend services.  Uses
    server_default so existing rows get defaults without a data
    migration.
    """
    op.add_column(
        'user_settings',
        sa.Column(
            'large_transaction_threshold', sa.Integer(),
            server_default='500', nullable=False,
        ),
        schema='auth',
    )
    op.add_column(
        'user_settings',
        sa.Column(
            'trend_alert_threshold', sa.Numeric(precision=5, scale=4),
            server_default='0.1000', nullable=False,
        ),
        schema='auth',
    )
    op.add_column(
        'user_settings',
        sa.Column(
            'anchor_staleness_days', sa.Integer(),
            server_default='14', nullable=False,
        ),
        schema='auth',
    )
    op.create_check_constraint(
        'ck_user_settings_large_txn_threshold',
        'user_settings',
        'large_transaction_threshold >= 0',
        schema='auth',
    )
    op.create_check_constraint(
        'ck_user_settings_valid_trend_threshold',
        'user_settings',
        'trend_alert_threshold >= 0 AND trend_alert_threshold <= 1',
        schema='auth',
    )
    op.create_check_constraint(
        'ck_user_settings_positive_staleness_days',
        'user_settings',
        'anchor_staleness_days > 0',
        schema='auth',
    )


def downgrade():
    """Remove Section 8 settings columns from auth.user_settings."""
    op.drop_constraint(
        'ck_user_settings_positive_staleness_days',
        'user_settings',
        schema='auth',
    )
    op.drop_constraint(
        'ck_user_settings_valid_trend_threshold',
        'user_settings',
        schema='auth',
    )
    op.drop_constraint(
        'ck_user_settings_large_txn_threshold',
        'user_settings',
        schema='auth',
    )
    op.drop_column('user_settings', 'anchor_staleness_days', schema='auth')
    op.drop_column('user_settings', 'trend_alert_threshold', schema='auth')
    op.drop_column('user_settings', 'large_transaction_threshold', schema='auth')
