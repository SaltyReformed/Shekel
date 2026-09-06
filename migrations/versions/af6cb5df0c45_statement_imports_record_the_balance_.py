"""statement imports record the balance their file claims

A SECU CSV opens with a ``Balance as of <date>,<amount>`` line stating what the
bank held the account at.  Nothing read it: the importer derived
``opening_balance`` / ``closing_balance`` from the per-line running-balance
chain, and a file exporting no running-balance column -- which is every export
the developer's bank has produced since 2026-07 -- therefore carried no balance
figure at all and no cross-check of any kind.

This records the claim so it can be set beside the owner's own asserted anchor.
It is stored SEPARATELY from the two derived columns rather than folded into
them, because the two are different kinds of fact and the model's own docstring
records the measurement that forbids substituting one for the other: on the
2026-08-16 export the header read ``$4,747.63``, which was 2026-08-13's closing
balance while the same file listed two 2026-08-14 lines worth ``-$1,006.72``.

**Not destructive.**  Two nullable columns and one CHECK; no column is dropped,
renamed or retyped, and no existing row changes.  Existing imports keep NULL in
both -- there is nothing to backfill them from, because the figure lives in the
uploaded file rather than anywhere the database can reach.

Revision ID: af6cb5df0c45
Revises: e4a7c0f13b92
Create Date: 2026-08-22 06:36:59.923241
"""
from alembic import op
import sqlalchemy as sa

# Revision identifiers, used by Alembic.
revision = 'af6cb5df0c45'
down_revision = 'e4a7c0f13b92'
branch_labels = None
depends_on = None


def upgrade():
    """Add the stated-balance pair and the constraint keeping them together."""
    op.add_column(
        'statement_imports',
        sa.Column('stated_balance', sa.Numeric(precision=12, scale=2),
                  nullable=True),
        schema='budget',
    )
    op.add_column(
        'statement_imports',
        sa.Column('stated_balance_on', sa.Date(), nullable=True),
        schema='budget',
    )
    # ONE fact in two columns, so both-or-neither.  A figure without its day
    # asserts nothing about an account, and the reader selects the anchor to
    # compare against BY that day -- a half-written pair would send it looking
    # for an anchor as of NULL.
    op.create_check_constraint(
        'ck_statement_imports_stated_balance_paired',
        'statement_imports',
        '(stated_balance IS NULL) = (stated_balance_on IS NULL)',
        schema='budget',
    )


def downgrade():
    """Drop the pair and its constraint.

    Value-lossless in the direction that matters: the figures came from files
    the owner still holds, and re-importing one restates them.
    """
    op.drop_constraint(
        'ck_statement_imports_stated_balance_paired',
        'statement_imports',
        type_='check',
        schema='budget',
    )
    op.drop_column('statement_imports', 'stated_balance_on', schema='budget')
    op.drop_column('statement_imports', 'stated_balance', schema='budget')
