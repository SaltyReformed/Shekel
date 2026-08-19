"""a bank line is this row

Plan step **bank_import:X-f6a-2** of
``docs/plans/implementation_plan_bank_import.md`` -- the MATCH half of ruling
**R-FS**: *a match is one line to one row, or a GROUP summing to it in either
direction*.  X-f6a-1 recorded what the bank said; this records which of the
app's own rows each line IS, so the day the app holds can be corrected to the
day the bank posted.

Review: Josh, 2026-08-17 -- APPROVED: a two-table match relation over a column
on either row, refusing a group whose members do not sum to the line rather
than apportioning the difference, and settling a still-Projected row from the
bank's own evidence that the money moved.

**This migration moves no money by itself.**  Two tables land, two superkeys
are added so their composite keys have something to target, and nothing reads
either table until ``app.services.statement_match`` is called.  The money moves
at the ACCEPT door, one reviewed act at a time.

Two tables:

* ``budget.statement_matches`` -- one accepted act of matching.
* ``budget.statement_match_members`` -- the bank lines and app rows it names,
  as an EXCLUSIVE ARC of three typed foreign keys.

Two superkeys, both of the ``uq_transactions_id_account`` shape and both
constraining nothing on their own: ``uq_bank_statement_lines_id_account`` and
``uq_transaction_entries_id_account``.  PostgreSQL requires a UNIQUE over
exactly the referenced columns before a composite foreign key may target them,
and the composite keys are what make a match spanning two accounts
unrepresentable rather than merely untested.

**Nothing is backfilled**, for the reason X-f6a-1 gives for its own columns: a
match is an OBSERVATION a human accepted, and deriving one from the app's own
dates would launder a measured-wrong guess into an observation nobody made.
Measured on the developer's own 2026-08-16 statement against a production
clone: of 58 bank lines an exact-amount predicate pairs uniquely with an app
row, only 23 carry the day the app recorded.

Revision ID: c1e7d4b3a850
Revises: 3f408018a71c
Create Date: 2026-08-17
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c1e7d4b3a850'
down_revision = '3f408018a71c'
branch_labels = None
depends_on = None


#: The tables this migration creates that ``app.audit_infrastructure``
#: also lists.  Stated once here and asserted against that module by
#: ``tests/test_models/test_statement_match_schema.py``, so the two cannot
#: drift.
_AUDITED_NEW_TABLES = ('statement_matches', 'statement_match_members')


def upgrade():
    """Create the match tables and the two superkeys they key onto."""
    # The superkeys ``statement_match_members``' composite foreign keys target.
    # Neither constrains anything on its own -- ``id`` is already the primary
    # key on both tables, so neither can reject a row -- and both exist only
    # because PostgreSQL requires a UNIQUE over exactly the referenced columns.
    op.create_unique_constraint(
        'uq_bank_statement_lines_id_account', 'bank_statement_lines',
        ['id', 'account_id'], schema='budget',
    )
    op.create_unique_constraint(
        'uq_transaction_entries_id_account', 'transaction_entries',
        ['id', 'account_id'], schema='budget',
    )

    op.create_table(
        'statement_matches',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['account_id'], ['budget.accounts.id'], ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['auth.users.id'], ondelete='CASCADE',
        ),
        # This act's owner IS its account's, guaranteed rather than maintained
        # -- keyed onto ``uq_accounts_id_user``, the same construction
        # ``fk_account_external_identities_owner`` uses.
        sa.ForeignKeyConstraint(
            ['account_id', 'user_id'],
            ['budget.accounts.id', 'budget.accounts.user_id'],
            name='fk_statement_matches_owner',
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
        # The superkey the members name so their account is this act's.
        sa.UniqueConstraint(
            'id', 'account_id', name='uq_statement_matches_id_account',
        ),
        schema='budget',
    )
    op.create_index(
        'idx_statement_matches_account', 'statement_matches',
        ['account_id'], unique=False, schema='budget',
    )

    op.create_table(
        'statement_match_members',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('match_id', sa.Integer(), nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('bank_statement_line_id', sa.Integer(), nullable=True),
        sa.Column('transaction_id', sa.Integer(), nullable=True),
        sa.Column('transaction_entry_id', sa.Integer(), nullable=True),
        # THE EXCLUSIVE ARC: exactly one subject.  Summing the NULL tests is
        # the spelling ``ck_transactions_one_pricing_link`` uses for three
        # columns, where ``<>`` only reads as XOR for two.
        sa.CheckConstraint(
            '(bank_statement_line_id IS NOT NULL)::int '
            '+ (transaction_id IS NOT NULL)::int '
            '+ (transaction_entry_id IS NOT NULL)::int = 1',
            name='ck_statement_match_members_one_subject',
        ),
        # This member's account IS its act's...
        sa.ForeignKeyConstraint(
            ['match_id', 'account_id'],
            ['budget.statement_matches.id',
             'budget.statement_matches.account_id'],
            name='fk_statement_match_members_match_account',
            ondelete='CASCADE',
        ),
        # ...and IS its subject's, for whichever of the three it carries.
        # ``MATCH SIMPLE`` (PostgreSQL's default) is what lets the three sit
        # side by side: a member whose column is NULL satisfies that key
        # whatever ``account_id`` says.
        sa.ForeignKeyConstraint(
            ['bank_statement_line_id', 'account_id'],
            ['budget.bank_statement_lines.id',
             'budget.bank_statement_lines.account_id'],
            name='fk_statement_match_members_line_account',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['transaction_id', 'account_id'],
            ['budget.transactions.id', 'budget.transactions.account_id'],
            name='fk_statement_match_members_transaction_account',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['transaction_entry_id', 'account_id'],
            ['budget.transaction_entries.id',
             'budget.transaction_entries.account_id'],
            name='fk_statement_match_members_entry_account',
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
        schema='budget',
    )
    # One subject, at most one match.  PARTIAL, because two of the three
    # columns are NULL on every row and a NULL is not a claim.  Without these
    # a second review pass could explain one bank line twice and both acts
    # would look complete.
    for column, index in (
        ('bank_statement_line_id', 'uq_statement_match_members_line'),
        ('transaction_id', 'uq_statement_match_members_transaction'),
        ('transaction_entry_id', 'uq_statement_match_members_entry'),
    ):
        op.create_index(
            index, 'statement_match_members', [column], unique=True,
            schema='budget',
            postgresql_where=sa.text(f'{column} IS NOT NULL'),
        )
    op.create_index(
        'idx_statement_match_members_match', 'statement_match_members',
        ['match_id'], unique=False, schema='budget',
    )

    # ── Attach the audit triggers ────────────────────────────────────────
    #
    # Trigger name ``audit_<table>`` matches the convention the entrypoint
    # trigger-count health check enumerates (``tgname LIKE 'audit_%'``).  The
    # shared ``system.audit_trigger_func`` already exists from the rebuild
    # migration; DROP IF EXISTS first so a re-run is idempotent.
    for table in _AUDITED_NEW_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS audit_{table} ON budget.{table}")
        op.execute(
            f"CREATE TRIGGER audit_{table} "
            f"AFTER INSERT OR UPDATE OR DELETE ON budget.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
        )


def downgrade():
    """Drop the match tables and the two superkeys added for them."""
    for index in (
        'idx_statement_match_members_match',
        'uq_statement_match_members_entry',
        'uq_statement_match_members_transaction',
        'uq_statement_match_members_line',
    ):
        op.drop_index(
            index, table_name='statement_match_members', schema='budget',
        )
    op.drop_table('statement_match_members', schema='budget')
    op.drop_index(
        'idx_statement_matches_account', table_name='statement_matches',
        schema='budget',
    )
    op.drop_table('statement_matches', schema='budget')
    op.drop_constraint(
        'uq_transaction_entries_id_account', 'transaction_entries',
        schema='budget', type_='unique',
    )
    op.drop_constraint(
        'uq_bank_statement_lines_id_account', 'bank_statement_lines',
        schema='budget', type_='unique',
    )
