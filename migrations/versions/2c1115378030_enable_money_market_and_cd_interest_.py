"""enable money market and cd interest params

Revision ID: 2c1115378030
Revises: 087bb96db063
Create Date: 2026-04-06 23:25:02.464436

Data-only migration: sets has_parameters and has_interest flags on
Money Market and CD account types, and creates default InterestParams
rows for any existing accounts of those types that lack them.
"""
from alembic import op
import sqlalchemy as sa

# Revision identifiers, used by Alembic.
revision = '2c1115378030'
down_revision = '087bb96db063'
branch_labels = None
depends_on = None

# Default InterestParams values -- match the model server_defaults
# used by create_account() when auto-creating InterestParams.
_DEFAULT_APY = '0.04500'
_DEFAULT_COMPOUNDING = 'daily'


def upgrade():
    """Enable interest support for Money Market and CD account types.

    1. Set has_parameters=true and has_interest=true on both types.
    2. For every existing account of those types that has no
       InterestParams row, insert one with sensible defaults.
    """
    conn = op.get_bind()

    # Step 1: update the account type flags.
    conn.execute(sa.text(
        "UPDATE ref.account_types "
        "SET has_parameters = true, has_interest = true "
        "WHERE name IN ('Money Market', 'CD')"
    ))

    # Step 2: create default InterestParams for existing accounts
    # that lack them.  The LEFT JOIN ... WHERE ip.id IS NULL pattern
    # finds accounts without an InterestParams row.
    conn.execute(sa.text(
        "INSERT INTO budget.interest_params (account_id, apy, compounding_frequency) "
        "SELECT a.id, :apy, :freq "
        "FROM budget.accounts a "
        "JOIN ref.account_types at ON a.account_type_id = at.id "
        "LEFT JOIN budget.interest_params ip ON ip.account_id = a.id "
        "WHERE at.name IN ('Money Market', 'CD') "
        "AND ip.id IS NULL"
    ), {"apy": _DEFAULT_APY, "freq": _DEFAULT_COMPOUNDING})


def downgrade():
    """Reverse: remove auto-created InterestParams and reset flags.

    Only deletes InterestParams rows that were created by this
    migration -- identified as rows for Money Market or CD accounts
    that still have the default APY and compounding frequency.  Rows
    the user has customized are left intact (the flag reset will
    prevent the UI from accessing them, but no user data is lost).
    """
    conn = op.get_bind()

    # Step 1: delete InterestParams rows for Money Market and CD
    # accounts that still have the migration defaults.  This avoids
    # destroying user-customized params on rollback.
    conn.execute(sa.text(
        "DELETE FROM budget.interest_params "
        "WHERE account_id IN ("
        "  SELECT a.id FROM budget.accounts a "
        "  JOIN ref.account_types at ON a.account_type_id = at.id "
        "  WHERE at.name IN ('Money Market', 'CD')"
        ") "
        "AND apy = :apy "
        "AND compounding_frequency = :freq"
    ), {"apy": _DEFAULT_APY, "freq": _DEFAULT_COMPOUNDING})

    # Step 2: reset the account type flags.
    conn.execute(sa.text(
        "UPDATE ref.account_types "
        "SET has_parameters = false, has_interest = false "
        "WHERE name IN ('Money Market', 'CD')"
    ))
