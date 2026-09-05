"""where a merchant goes

Plan step **bank_import:X-f6a-3d** of
``docs/plans/implementation_plan_bank_import.md``, "The steps".

Review: Josh, 2026-08-19 -- APPROVED: a policy the owner STATES over one the
app infers from history, with "never a purchase" as one of its three answers.

**One table, holding a DECISION.**  ``budget.merchant_destinations`` records
where this owner has said one merchant's spending goes on one account, and the
model docstring carries the argument for storing it rather than deriving it
from the match relation the app already keeps.  The short form: where a
merchant's money went last April is evidence, where it should go next is a
decision; a derivation moves when history is edited; and a derivation cannot
express *never a purchase* at all, because a line the owner left alone leaves
no trace.  That last one is the whole game on the developer's own data --
Capital One Credit Card is 9 of the 91 unexplained outflows and **`-$7,412.94`
of the `-$11,336.36`**, and every one of them must never become a purchase
because the app already holds that money as CC Payback rows.

**No figure moves.**  Nothing in this table can write money: a policy is read
to SUGGEST a destination, and the only thing that records a purchase is an
explicit destination submitted for one specific line (developer ruling
2026-08-19, which keeps ruling **R-FZ**'s *the destination select IS the tick*
whole).  The table is created empty.

**TWO SUPERKEYS come with it**, and they constrain nothing on their own --
``id`` is already the primary key on both.  PostgreSQL requires a UNIQUE over
exactly the referenced columns before a composite foreign key may target them,
which is what makes two ownership facts structural rather than checked by a
reader that can be forgotten:

  * ``uq_transaction_templates_id_account`` -- so a policy's template is
    provably on the policy's own ACCOUNT.  A statement is one bank's record of
    one account, so a policy pointing at another account's recurring envelope
    is not a destination at all.
  * ``uq_categories_id_user`` -- so a policy's category is provably its
    OWNER's.  A foreign ``category_id`` satisfies a bare foreign key perfectly
    well, which is the IDOR every create door in this project probes for by
    hand; here it is unwritable.

The same construction, for the same reason, as ``uq_accounts_id_user`` and
``uq_transactions_id_account``.

**Reversible.**  The downgrade drops the table and both superkeys.  It loses
only stated preferences, and the table is empty at this revision.
"""
from alembic import op
import sqlalchemy as sa


revision = 'c8e2f5a94d17'
down_revision = 'b1d94c7a20f3'
branch_labels = None
depends_on = None


#: The table gaining an audit trigger here.  It holds a user-controlled
#: decision that governs where real money is filed, which is
#: ``app.audit_infrastructure.AUDITED_TABLES``' inclusion criterion -- and it is
#: EDITED in place rather than superseded by a new row, so the audit trail is
#: the only record of what the owner said before.
_AUDITED_NEW_TABLE = "merchant_destinations"


def upgrade():
    """Create the merchant-destination table and the keys it targets."""
    # The superkeys the composite foreign keys below need as targets.  Neither
    # constrains anything: ``id`` is already the primary key on both tables, so
    # these can reject no row.
    op.create_unique_constraint(
        'uq_transaction_templates_id_account', 'transaction_templates',
        ['id', 'account_id'], schema='budget',
    )
    op.create_unique_constraint(
        'uq_categories_id_user', 'categories', ['id', 'user_id'],
        schema='budget',
    )

    op.create_table(
        'merchant_destinations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('merchant', sa.String(length=100), nullable=False),
        sa.Column('template_id', sa.Integer(), nullable=True),
        sa.Column('envelope_name', sa.String(length=200), nullable=True),
        sa.Column('category_id', sa.Integer(), nullable=True),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        # THE THREE ANSWERS, spelled as three shapes: a template, a new
        # envelope (name AND category), or never a purchase (nothing set).  A
        # count-the-NULLs form cannot say this -- one answer sets two columns
        # and one sets none -- so what is constrained is which COMBINATIONS are
        # legal rather than how many columns are filled.
        sa.CheckConstraint(
            "(template_id IS NOT NULL AND envelope_name IS NULL "
            "AND category_id IS NULL) "
            "OR (template_id IS NULL AND envelope_name IS NOT NULL "
            "AND category_id IS NOT NULL) "
            "OR (template_id IS NULL AND envelope_name IS NULL "
            "AND category_id IS NULL)",
            name='ck_merchant_destinations_one_answer',
        ),
        sa.CheckConstraint(
            "btrim(merchant) <> ''",
            name='ck_merchant_destinations_merchant_not_blank',
        ),
        sa.CheckConstraint(
            "envelope_name IS NULL OR btrim(envelope_name) <> ''",
            name='ck_merchant_destinations_envelope_name_not_blank',
        ),
        sa.ForeignKeyConstraint(
            ['account_id'], ['budget.accounts.id'], ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['auth.users.id'], ondelete='CASCADE',
        ),
        # This row's owner IS its account's, guaranteed rather than maintained.
        sa.ForeignKeyConstraint(
            ['account_id', 'user_id'],
            ['budget.accounts.id', 'budget.accounts.user_id'],
            name='fk_merchant_destinations_owner',
            ondelete='CASCADE',
        ),
        # ...the template it names is on that same ACCOUNT...
        sa.ForeignKeyConstraint(
            ['template_id', 'account_id'],
            ['budget.transaction_templates.id',
             'budget.transaction_templates.account_id'],
            name='fk_merchant_destinations_template_account',
            ondelete='CASCADE',
        ),
        # ...and the category it names is this OWNER's.
        sa.ForeignKeyConstraint(
            ['category_id', 'user_id'],
            ['budget.categories.id', 'budget.categories.user_id'],
            name='fk_merchant_destinations_category_owner',
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
        # ONE answer per merchant per account: restating is an UPDATE rather
        # than a second row leaving two answers to one question.
        sa.UniqueConstraint(
            'user_id', 'account_id', 'merchant',
            name='uq_merchant_destinations_owner_account_merchant',
        ),
        schema='budget',
    )
    op.create_index(
        'idx_merchant_destinations_account', 'merchant_destinations',
        ['account_id'], unique=False, schema='budget',
    )

    # Trigger name ``audit_<table>`` matches the convention the entrypoint
    # trigger-count health check enumerates (``tgname LIKE 'audit_%'``).  The
    # shared ``system.audit_trigger_func`` already exists.
    op.execute(
        f"DROP TRIGGER IF EXISTS audit_{_AUDITED_NEW_TABLE} "
        f"ON budget.{_AUDITED_NEW_TABLE}"
    )
    op.execute(
        f"CREATE TRIGGER audit_{_AUDITED_NEW_TABLE} "
        f"AFTER INSERT OR UPDATE OR DELETE ON budget.{_AUDITED_NEW_TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
    )


def downgrade():
    """Drop the table (its audit trigger goes with it) and both superkeys."""
    op.drop_index(
        'idx_merchant_destinations_account',
        table_name='merchant_destinations', schema='budget',
    )
    op.drop_table('merchant_destinations', schema='budget')
    op.drop_constraint(
        'uq_categories_id_user', 'categories', schema='budget',
        type_='unique',
    )
    op.drop_constraint(
        'uq_transaction_templates_id_account', 'transaction_templates',
        schema='budget', type_='unique',
    )
