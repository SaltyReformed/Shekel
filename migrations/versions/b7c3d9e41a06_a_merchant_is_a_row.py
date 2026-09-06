"""a merchant is a row

Revision ID: b7c3d9e41a06
Revises: a1f4c7e0b839
Create Date: 2026-08-25

Plan step **bank_import:X-gd-1** of
``docs/plans/implementation_plan_bank_import.md``, "The steps".  Ruling
**R-GR** (developer, 2026-08-25).

**The merchant stops being a string two tables each keep their own copy of.**
``budget.merchants`` holds one row per merchant per account; a bank line NAMES
one and a stated destination is ABOUT one, both by id.  The model docstring
(``app/models/merchant.py``) carries the argument; the short form is that a
merchant string on a line is provenance, and the moment the owner may say
*lines from this merchant go here* it becomes the SUBJECT of a stored decision
that two tables have to agree about.

**What changes structurally, and it is the reason for the migration:**

  * ``statement_match._policy._refuse_unknown_merchants`` stops being what
    makes a stored rule correct.  A rule now names a ``merchant_id``, and
    ``fk_merchant_destinations_merchant_account`` refuses one that is not this
    account's -- so the check survives as the SENTENCE a stale page gets, in
    the shape ``_checked_template`` already has.
  * *Which merchants may be asked about* was the UNION of a DISTINCT over every
    recorded line and the set already answered for.  A merchant row outlives
    its lines (deleting an import takes the lines, never the merchant), so the
    union is now the table.
  * The two 100-character copies of one string become one.

**No figure moves and no answer changes.**  Every existing destination is
carried onto the merchant row holding its own string, so the three answers, the
policies that resolve and the placements they suggest are identical either side
of this revision.  Measured on the developer's dev database before writing it:
378 recorded lines, all naming a merchant, **62 distinct merchants and 62
distinct case-folded** -- so no two rows collapse into one here.  29 stored
destinations, every one of them naming a merchant its own account's lines also
name.  Production holds ZERO bank lines and has never had the
``merchant_destinations`` table applied at all, so both arms of the backfill
select nothing there and this revision creates the table empty.

**Reversible, and value-losslessly.**  The downgrade puts each string back on
the row that referenced it, from ``budget.merchants.name``, and drops the
table.  A merchant row that no line and no destination references -- one whose
lines were deleted with their import -- has nothing to write back and is
dropped with the table; that is the one fact this revision adds and the
downgrade cannot keep, and it is a fact no reader had before this revision.
"""
from alembic import op
import sqlalchemy as sa


revision = 'b7c3d9e41a06'
down_revision = 'a1f4c7e0b839'
branch_labels = None
depends_on = None


#: Mint one merchant per distinct name per account, from BOTH sources.
#:
#: A destination may name a merchant whose lines have since been deleted with
#: their import -- which is exactly the case ``statable_merchants``' second half
#: existed for.  It is 0 rows on the developer's own database today and the
#: UNION is what makes that a measurement rather than an assumption.
#:
#: **Held as a module constant so a test can execute the string this migration
#: executes**, which is the convention ``efffcf647644``'s ``BACKFILL_SQL``
#: established here: a test that re-typed the join would agree with a mistake
#: as readily as with the truth.
MINT_MERCHANTS_SQL = """
    INSERT INTO budget.merchants (account_id, name)
    SELECT DISTINCT account_id, merchant
      FROM budget.bank_statement_lines
     WHERE merchant IS NOT NULL
    UNION
    SELECT DISTINCT account_id, merchant
      FROM budget.merchant_destinations
"""

#: Point each recorded line at the merchant row holding its own string.
#:
#: **Joined on the ACCOUNT as well as the name**, which is the term that makes
#: it correct rather than merely working: two accounts legitimately hold one
#: name, and a join on the name alone would be ambiguous and would resolve
#: arbitrarily.
POINT_LINES_SQL = """
    UPDATE budget.bank_statement_lines AS l
       SET merchant_id = m.id
      FROM budget.merchants AS m
     WHERE m.account_id = l.account_id
       AND m.name = l.merchant
"""

#: The same, for a stated destination.
POINT_DESTINATIONS_SQL = """
    UPDATE budget.merchant_destinations AS d
       SET merchant_id = m.id
      FROM budget.merchants AS m
     WHERE m.account_id = d.account_id
       AND m.name = d.merchant
"""

#: Put each string back on the row that referenced it.
RESTORE_DESTINATION_STRINGS_SQL = """
    UPDATE budget.merchant_destinations AS d
       SET merchant = m.name
      FROM budget.merchants AS m
     WHERE m.id = d.merchant_id
"""

RESTORE_LINE_STRINGS_SQL = """
    UPDATE budget.bank_statement_lines AS l
       SET merchant = m.name
      FROM budget.merchants AS m
     WHERE m.id = l.merchant_id
"""


def upgrade():
    """Promote the merchant string to a row and point both tables at it."""
    op.create_table(
        'merchants',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "btrim(name) <> ''", name='ck_merchants_name_not_blank',
        ),
        sa.ForeignKeyConstraint(
            ['account_id'], ['budget.accounts.id'], ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
        # THE IDENTITY: one row per name per account.
        sa.UniqueConstraint(
            'account_id', 'name', name='uq_merchants_account_name',
        ),
        # The SUPERKEY both referrers target so their own ``account_id`` is
        # this merchant's.  It constrains nothing -- ``id`` is already the
        # primary key -- and exists only because PostgreSQL requires a UNIQUE
        # over exactly the referenced columns before a composite foreign key
        # may target them.
        sa.UniqueConstraint(
            'id', 'account_id', name='uq_merchants_id_account',
        ),
        schema='budget',
    )
    # Trigger name ``audit_<table>`` matches the convention the entrypoint
    # trigger-count health check enumerates (``tgname LIKE 'audit_%'``), and
    # the row in ``app.audit_infrastructure.AUDITED_TABLES`` is what the
    # rebuild migration reads.  The shared ``system.audit_trigger_func``
    # already exists.  A merchant row is not a decision -- it is the source's
    # own word -- but ``bank_statement_lines`` beside it is audited for the
    # same reason: this is the account's record of what a bank said, and the
    # forensic question *when did this merchant first appear* has no other
    # answer once its lines are deleted.
    op.execute(
        "DROP TRIGGER IF EXISTS audit_merchants ON budget.merchants"
    )
    op.execute(
        "CREATE TRIGGER audit_merchants "
        "AFTER INSERT OR UPDATE OR DELETE ON budget.merchants "
        "FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
    )

    # BOTH sources, because a destination may name a merchant whose lines have
    # since been deleted with their import -- which is exactly the case
    # ``statable_merchants``' second half existed for.  It is 0 rows on the
    # developer's own database today and the UNION is what makes that a
    # measurement rather than an assumption.
    op.execute(MINT_MERCHANTS_SQL)

    # ---- the lines NAME a merchant -------------------------------------
    op.add_column(
        'bank_statement_lines',
        sa.Column('merchant_id', sa.Integer(), nullable=True),
        schema='budget',
    )
    op.execute(POINT_LINES_SQL)
    op.create_foreign_key(
        'fk_bank_statement_lines_merchant_account', 'bank_statement_lines',
        'merchants', ['merchant_id', 'account_id'], ['id', 'account_id'],
        source_schema='budget', referent_schema='budget',
    )
    # The review screen groups an account's unexplained lines BY MERCHANT.
    # Partial for the reason the index it replaces was: a line naming no
    # merchant joins no rule and is never looked up by this column.
    op.drop_index(
        'idx_bank_statement_lines_account_merchant',
        table_name='bank_statement_lines', schema='budget',
    )
    op.create_index(
        'idx_bank_statement_lines_account_merchant', 'bank_statement_lines',
        ['account_id', 'merchant_id'], unique=False, schema='budget',
        postgresql_where=sa.text('merchant_id IS NOT NULL'),
    )
    op.drop_constraint(
        'ck_bank_statement_lines_merchant_not_blank', 'bank_statement_lines',
        schema='budget', type_='check',
    )
    op.drop_column('bank_statement_lines', 'merchant', schema='budget')

    # ---- the destinations are ABOUT a merchant -------------------------
    op.add_column(
        'merchant_destinations',
        sa.Column('merchant_id', sa.Integer(), nullable=True),
        schema='budget',
    )
    op.execute(POINT_DESTINATIONS_SQL)
    op.alter_column(
        'merchant_destinations', 'merchant_id', nullable=False,
        schema='budget',
    )
    # ONE answer per merchant per account.  ``user_id`` leaves the key: it is
    # held equal to the account's by ``fk_merchant_destinations_owner``, so it
    # was a functionally dependent third term that narrowed nothing.
    op.drop_constraint(
        'uq_merchant_destinations_owner_account_merchant',
        'merchant_destinations', schema='budget', type_='unique',
    )
    op.create_unique_constraint(
        'uq_merchant_destinations_account_merchant', 'merchant_destinations',
        ['account_id', 'merchant_id'], schema='budget',
    )
    op.create_foreign_key(
        'fk_merchant_destinations_merchant_account', 'merchant_destinations',
        'merchants', ['merchant_id', 'account_id'], ['id', 'account_id'],
        source_schema='budget', referent_schema='budget',
    )
    op.drop_constraint(
        'ck_merchant_destinations_merchant_not_blank', 'merchant_destinations',
        schema='budget', type_='check',
    )
    op.drop_column('merchant_destinations', 'merchant', schema='budget')


def downgrade():
    """Put each string back on the row that referenced it, then drop the table."""
    # ---- the destinations carry their own string again -----------------
    op.add_column(
        'merchant_destinations',
        sa.Column('merchant', sa.String(length=100), nullable=True),
        schema='budget',
    )
    op.execute(RESTORE_DESTINATION_STRINGS_SQL)
    op.alter_column(
        'merchant_destinations', 'merchant', nullable=False, schema='budget',
    )
    op.create_check_constraint(
        'ck_merchant_destinations_merchant_not_blank', 'merchant_destinations',
        "btrim(merchant) <> ''", schema='budget',
    )
    op.drop_constraint(
        'fk_merchant_destinations_merchant_account', 'merchant_destinations',
        schema='budget', type_='foreignkey',
    )
    op.drop_constraint(
        'uq_merchant_destinations_account_merchant', 'merchant_destinations',
        schema='budget', type_='unique',
    )
    op.create_unique_constraint(
        'uq_merchant_destinations_owner_account_merchant',
        'merchant_destinations', ['user_id', 'account_id', 'merchant'],
        schema='budget',
    )
    op.drop_column('merchant_destinations', 'merchant_id', schema='budget')

    # ---- the lines carry their own string again ------------------------
    op.add_column(
        'bank_statement_lines',
        sa.Column('merchant', sa.String(length=100), nullable=True),
        schema='budget',
    )
    op.execute(RESTORE_LINE_STRINGS_SQL)
    op.create_check_constraint(
        'ck_bank_statement_lines_merchant_not_blank', 'bank_statement_lines',
        "merchant IS NULL OR btrim(merchant) <> ''", schema='budget',
    )
    op.drop_constraint(
        'fk_bank_statement_lines_merchant_account', 'bank_statement_lines',
        schema='budget', type_='foreignkey',
    )
    op.drop_index(
        'idx_bank_statement_lines_account_merchant',
        table_name='bank_statement_lines', schema='budget',
    )
    op.create_index(
        'idx_bank_statement_lines_account_merchant', 'bank_statement_lines',
        ['account_id', 'merchant'], unique=False, schema='budget',
        postgresql_where=sa.text('merchant IS NOT NULL'),
    )
    op.drop_column('bank_statement_lines', 'merchant_id', schema='budget')

    op.drop_table('merchants', schema='budget')
