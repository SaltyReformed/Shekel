"""the bank says what happened

Plan step **bank_import:X-f6a-1** of
``docs/plans/implementation_plan_bank_import.md`` -- the RECORDING half of
ruling **R-FP**: *a statement importer is a SOURCE ADAPTER over one normalized
line shape*, and before anything can be matched, what the bank said has to exist
in the database as a fact.

Review: Josh, 2026-08-16 -- APPROVED: the full normalized line table over
storing nothing or storing external ids alone, the source-independent
positional identity key, and the SECU CSV as the single file per account.

**No figure moves.**  Nothing here is read by the balance walk, the posted
ledger, the grid or any producer: four tables land, one ref row is seeded, and
the only code touching them is the import door.  Deciding which of the app's own
rows a recorded line explains -- and correcting a ``settled_on`` from it -- is
the NEXT leaf (``X-f6a-2``).  That separation is what lets this commit be graded
by a property rather than by inspection.

Four tables:

  1. **``ref.statement_sources``** -- the adapter catalogue, seeded with the one
     adapter that exists (``secu_checking_csv``).  A new source is an INSERT,
     never a migration, matching every other ``ref`` catalogue here.
  2. **``budget.account_external_identities``** -- which account at a source is
     which account here.  Ruling R-FP calls this mapping "a fact, not a guess",
     and two UNIQUE constraints are what make it one: an external account maps
     to at most one of THAT OWNER'S accounts, and an account has at most one
     identity per source.  The first import records it and every import after
     it is checked, so importing the card's export into Checking is refused by
     the DATABASE rather than by a reviewer noticing.  **The owner scope is
     load-bearing**: this adapter's identifier is SECU's mask (``******3820``),
     a 10,000-value space, so a global key would let two owners at one credit
     union collide -- permanently locking one out of importing their own
     statements, and disclosing through the refusal that another account in the
     system held their number.  ``uq_accounts_id_user`` and
     ``fk_account_external_identities_owner`` are what make that scope
     structural rather than a column a writer keeps in step.
  3. **``budget.statement_imports``** -- one row per import act, carrying who,
     when, which file, and how much of it was new.
     ``uq_statement_imports_id_account`` is the superkey the line table's
     composite key needs; it constrains nothing on its own.
  4. **``budget.bank_statement_lines``** -- the lines themselves.

**The identity key is ``(account_id, posted_on, amount, sequence_in_group)``,
deliberately NOT the bank's own id.**  R-FP names ``FITID`` as the idempotency
key and measurement 2026-08-16 refined that: only some sources carry one.  SECU's
OFX has ``FITID`` and truncates 326 of 361 descriptions to 32 characters; its
CSV carries the merchant, the bank's category and a per-line running balance and
has no id at all.  Keying on the id would make identity depend on the format --
one rule per adapter -- and it buys nothing: compared across two SECU exports
twelve days apart, the positional key reproduced the ``FITID`` key EXACTLY over
their 342 shared lines (0 keys present in only one export, 0 disagreeing ids).
``external_id`` is therefore stored as CORROBORATION under a partial unique
index, so a source that HAS an id still cannot claim one twice.

**The ordinal is what makes that key total.**  Two genuinely distinct charges can
share a day and an amount, and a key without it would reject the second as a
duplicate -- silent money loss on exactly the shape a duplicate guard exists to
protect.  On today's data no group needs it: ``(day, amount)`` alone was unique
across all 361 lines.

**There is deliberately no ``transaction_on <= posted_on`` CHECK.**  The obvious
constraint is FALSE on real data: 2 of 361 lines in the developer's own SECU
export carry an OFX ``DTUSER`` one day AFTER their ``DTPOSTED``, both ACH
deposits (2026-02-24 and 2026-03-18).  A constraint a real statement violates
makes the truth unimportable.

**What an adversarial review changed before this shipped**, recorded because
each was reachable rather than theoretical: the owner scope above; the
``amount <> 0`` CHECK, which also makes a ``NaN`` amount unstorable (PostgreSQL
``numeric`` accepts NaN, and a NaN amount matches nothing and makes the page
unrenderable); and the removal of a speculative ``external_institution_id``
column no adapter wrote and nothing read.

**Audit.**  All three ``budget`` tables join
``app.audit_infrastructure.AUDITED_TABLES`` and get their triggers here, so
``EXPECTED_TRIGGER_COUNT`` moves 43 -> 46 automatically
(``= len(AUDITED_TABLES)``).  They are audited because they are the evidentiary
basis for the money corrections the NEXT leaf writes: if a recorded line's day
or amount were ever altered, every correction derived from it would be wrong and
nothing else in the system would record that it had changed.
``ref.statement_sources`` is NOT audited -- it is a read-only seed catalogue,
the same exemption every other ``ref`` table takes.

**Downgrade** drops exactly what upgrade added, in reverse dependency order, and
loses only recorded statement lines.  No app figure depends on them at this
leaf, so a downgraded database renders every number it rendered before.

Revision ID: 3f408018a71c
Revises: b2e9a47c3f18
Create Date: 2026-08-16
"""
from alembic import op
import sqlalchemy as sa

# Revision identifiers, used by Alembic.
revision = '3f408018a71c'
down_revision = 'b2e9a47c3f18'
branch_labels = None
depends_on = None


#: The adapter this leaf ships.  A module CONSTANT rather than a literal inside
#: :func:`upgrade`, so ``tests/test_models/test_statement_import_schema.py``
#: asserts against the string this migration actually runs instead of a
#: hand-copied twin that can drift from it -- the pattern migration
#: ``d5b8e2c74a19``'s own backfill constant established.
SEED_SOURCES_SQL = (
    "INSERT INTO ref.statement_sources (name, display_name) VALUES "
    "('secu_checking_csv', 'SECU checking -- CSV with running balance')"
)

#: The tables that gain an audit trigger here.  Written once because the DROP
#: and the CREATE take the same list, and a second spelling is how one table
#: comes to be missing from one of them.
_AUDITED_NEW_TABLES = (
    "account_external_identities",
    "bank_statement_lines",
    "statement_imports",
)


def upgrade():
    """Create the statement-import tables, seed the adapter, attach audit."""
    # The superkey ``fk_account_external_identities_owner`` targets, so a
    # statement identity's owner is its ACCOUNT'S owner by construction rather
    # than by a writer remembering.  It constrains nothing on its own -- ``id``
    # is already the primary key -- exactly as
    # ``uq_transactions_id_account`` does for the clearing links.
    op.create_unique_constraint(
        'uq_accounts_id_user', 'accounts', ['id', 'user_id'], schema='budget',
    )

    op.create_table(
        'statement_sources',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=40), nullable=False),
        sa.Column('display_name', sa.String(length=80), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
        schema='ref',
    )
    op.execute(SEED_SOURCES_SQL)

    op.create_table(
        'account_external_identities',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('source_id', sa.Integer(), nullable=False),
        sa.Column('external_account_id', sa.String(length=64), nullable=False),
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
        # This row's owner IS its account's, guaranteed rather than
        # maintained -- keyed onto ``uq_accounts_id_user`` above.
        sa.ForeignKeyConstraint(
            ['account_id', 'user_id'],
            ['budget.accounts.id', 'budget.accounts.user_id'],
            name='fk_account_external_identities_owner',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['source_id'], ['ref.statement_sources.id'], ondelete='RESTRICT',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'account_id', 'source_id',
            name='uq_account_external_identities_account_source',
        ),
        # Scoped by OWNER, not global.  This adapter's identifier is SECU's
        # MASK (``******3820``), a 10,000-value space: a global key would let
        # one owner's masked number collide with another's, permanently
        # locking the loser out of importing their own statements and
        # disclosing that some other account in the system held that number.
        sa.UniqueConstraint(
            'user_id', 'source_id', 'external_account_id',
            name='uq_account_external_identities_owner_source_account',
        ),
        schema='budget',
    )

    op.create_table(
        'statement_imports',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('source_id', sa.Integer(), nullable=False),
        sa.Column('file_name', sa.String(length=255), nullable=False),
        sa.Column('file_digest', sa.String(length=64), nullable=False),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('line_count', sa.Integer(), nullable=False),
        sa.Column('recorded_count', sa.Integer(), nullable=False),
        sa.Column(
            'opening_balance', sa.Numeric(precision=12, scale=2),
            nullable=True,
        ),
        sa.Column(
            'closing_balance', sa.Numeric(precision=12, scale=2),
            nullable=True,
        ),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.CheckConstraint(
            'line_count > 0',
            name='ck_statement_imports_line_count_positive',
        ),
        sa.CheckConstraint(
            'period_end >= period_start',
            name='ck_statement_imports_period_ordered',
        ),
        sa.CheckConstraint(
            'recorded_count >= 0 AND recorded_count <= line_count',
            name='ck_statement_imports_recorded_within_file',
        ),
        sa.ForeignKeyConstraint(
            ['account_id'], ['budget.accounts.id'], ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['source_id'], ['ref.statement_sources.id'], ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['auth.users.id'], ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
        # The superkey ``fk_bank_statement_lines_import_account`` targets.  It
        # constrains nothing on its own -- ``id`` is already the primary key --
        # exactly as ``uq_transactions_id_account`` does one table over.
        sa.UniqueConstraint(
            'id', 'account_id', name='uq_statement_imports_id_account',
        ),
        schema='budget',
    )
    op.create_index(
        'idx_statement_imports_account', 'statement_imports', ['account_id'],
        unique=False, schema='budget',
    )

    op.create_table(
        'bank_statement_lines',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('import_id', sa.Integer(), nullable=False),
        sa.Column('posted_on', sa.Date(), nullable=False),
        sa.Column('transaction_on', sa.Date(), nullable=False),
        sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('description', sa.String(length=200), nullable=False),
        sa.Column('source_category', sa.String(length=100), nullable=True),
        sa.Column('external_id', sa.String(length=64), nullable=True),
        # NO server default: the table is new and empty, so there is no
        # backfill to serve -- and a default on a component of the IDENTITY key
        # would let a writer that forgets the ordinal write a plausible row
        # instead of failing.
        sa.Column('sequence_in_group', sa.SmallInteger(), nullable=False),
        sa.Column(
            'running_balance', sa.Numeric(precision=12, scale=2),
            nullable=True,
        ),
        sa.CheckConstraint(
            'sequence_in_group >= 0',
            name='ck_bank_statement_lines_sequence_non_negative',
        ),
        # A statement line MOVES money, and its figures are REAL numbers.
        # ``docs/coding-standards.md`` requires a CHECK on every financial
        # column; the adapter's refusal of a line stating no amount is the
        # Python half of the same rule.
        #
        # **The ``< 'NaN'`` term is the part that is not obvious, and a first
        # draft of this constraint got it wrong.**  PostgreSQL's ``numeric``
        # accepts ``NaN`` and orders it ABOVE every real number, so
        # ``NaN <> 0`` is TRUE and ``NaN = NaN`` is TRUE -- a plain non-zero
        # test admits it.  Since NaN sorts greatest, ``x < 'NaN'`` is true for
        # every real value and false for NaN itself, which is what makes a NaN
        # amount unrepresentable rather than merely unreached.  It matters
        # because a NaN amount compares equal to nothing (invisible to every
        # matcher), poisons ``SUM()`` over the account, and raises inside the
        # money display macro -- so the page 500s on every later load.
        sa.CheckConstraint(
            "amount <> 0 AND amount < 'NaN'::numeric AND (running_balance IS NULL OR running_balance < 'NaN'::numeric)",
            name='ck_bank_statement_lines_amount_real_nonzero',
        ),
        # A line's account IS its import's, guaranteed rather than maintained
        # -- the construction ``fk_transaction_entries_parent_account`` uses.
        # There is no direct FK to ``budget.accounts``: this key reaches it
        # through the import, and CASCADE is what lets deleting an account take
        # its imports and their lines with it.
        sa.ForeignKeyConstraint(
            ['import_id', 'account_id'],
            ['budget.statement_imports.id',
             'budget.statement_imports.account_id'],
            name='fk_bank_statement_lines_import_account',
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
        # THE IDENTITY.  Re-importing an overlapping span cannot duplicate a
        # line, structurally rather than by the importer remembering to look.
        sa.UniqueConstraint(
            'account_id', 'posted_on', 'amount', 'sequence_in_group',
            name='uq_bank_statement_lines_identity',
        ),
        schema='budget',
    )
    op.create_index(
        'idx_bank_statement_lines_account_day', 'bank_statement_lines',
        ['account_id', 'posted_on'], unique=False, schema='budget',
    )
    # A source that HAS its own id may not claim one twice.  PARTIAL, because
    # most adapters carry none and a NULL is not a claim.
    op.create_index(
        'uq_bank_statement_lines_external_id', 'bank_statement_lines',
        ['account_id', 'external_id'], unique=True, schema='budget',
        postgresql_where=sa.text('external_id IS NOT NULL'),
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
    """Drop the statement-import tables (their audit triggers go with them)."""
    op.drop_index(
        'uq_bank_statement_lines_external_id',
        table_name='bank_statement_lines', schema='budget',
        postgresql_where=sa.text('external_id IS NOT NULL'),
    )
    op.drop_index(
        'idx_bank_statement_lines_account_day',
        table_name='bank_statement_lines', schema='budget',
    )
    op.drop_table('bank_statement_lines', schema='budget')
    op.drop_index(
        'idx_statement_imports_account', table_name='statement_imports',
        schema='budget',
    )
    op.drop_table('statement_imports', schema='budget')
    op.drop_table('account_external_identities', schema='budget')
    op.drop_table('statement_sources', schema='ref')
    op.drop_constraint(
        'uq_accounts_id_user', 'accounts', schema='budget', type_='unique',
    )
