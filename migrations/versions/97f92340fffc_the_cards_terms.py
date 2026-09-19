"""the card's terms

Revision ID: 97f92340fffc
Revises: 3ef820b7dd52
Create Date: 2026-09-18

Plan step **credit_card:CC-2** of ``docs/design/credit_card_from_scratch.md``
section 3.4 (rulings **R-CC2**, **R-CC4**; the door and its surface ruled
**R-CC24** / **R-CC25**, 2026-09-18)::

    budget.credit_card_params    NEW, one row per revolving account, EMPTY

**ONE TABLE, holding the card's TERMS**: the statement close day, the payment
due day, the minimum-payment percent and floor, the cashback rate, the
auto-redeem threshold and the credit limit.  The ``loan_params`` shape -- a
1:1 satellite keyed on ``account_id`` (NOT NULL, UNIQUE, CASCADE) -- with the
owner key ``pay_calendar:C13-c`` gives the older satellites: ``user_id`` held
equal to the account's by the composite ``fk_credit_card_params_owner`` onto
``uq_accounts_id_user``, the construction ``fk_account_external_identities_owner``
and ``fk_merchant_destinations_owner`` already use.  A row for one owner's
card can never name another owner.

**No figure moves.**  The table is created EMPTY and nothing backfills it:
a card with no row is a dormant plain liability (design 3.4: "no
auto-create"), so every Credit Card account on every database is exactly as
it was before this revision until its owner states its terms through the
one door.  (Production held no Credit Card account when CC-1 shipped --
that leaf's measurement, 2026-09-18 -- so no card exists there to go
dormant; nothing here depends on it.)

**The CHECKs** state each column's domain at the storage tier -- days
``1..31``, both rates as FRACTIONS in ``[0, 1]`` (E-28: the form takes a
percent, the schema divides, the database refuses a writer that forgot),
the floor ``>= 0``, and the two nullable dollar figures ``NULL OR > 0`` (NULL
is their "not set" state, ruling **R-CC4**: "auto-redeem at
``auto_redeem_threshold`` (nullable, e.g. $25)").  Each is stated identically
on the model (``app/models/credit_card_params.py``); autogenerate does not
diff a CHECK, so ``tests/test_models/test_credit_card_params.py`` keeps the
two spellings equal.

**The audit trigger** ``audit_credit_card_params`` is attached here, the
``c8e2f5a94d17`` shape: the table holds user-controlled financial terms and
is EDITED in place (the one door rewrites the row), so the audit trail is the
only record of what the owner said before.  ``AUDITED_TABLES`` in
``app/audit_infrastructure.py`` carries the matching row.

Self-contained dependency policy: this migration imports nothing from
``app``.

**Reversible.**  The downgrade drops the table; its trigger and its own
constraints go with it, and the superkey it targets (``uq_accounts_id_user``)
is older than this revision and stays.  It loses only stated terms.
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = '97f92340fffc'
down_revision = '3ef820b7dd52'
branch_labels = None
depends_on = None


#: The table gaining an audit trigger here (see the docstring).
_AUDITED_NEW_TABLE = "credit_card_params"

# Stated identically on the model (``app/models/credit_card_params.py``),
# by name, so the test that compares the two can read them as a mapping.
_CHECKS = {
    "ck_credit_card_params_statement_close_day":
        "statement_close_day >= 1 AND statement_close_day <= 31",
    "ck_credit_card_params_payment_due_day":
        "payment_due_day >= 1 AND payment_due_day <= 31",
    "ck_credit_card_params_min_payment_percent":
        "min_payment_percent >= 0 AND min_payment_percent <= 1",
    "ck_credit_card_params_cashback_rate":
        "cashback_rate >= 0 AND cashback_rate <= 1",
    "ck_credit_card_params_min_payment_floor":
        "min_payment_floor >= 0",
    "ck_credit_card_params_auto_redeem_threshold":
        "auto_redeem_threshold IS NULL OR auto_redeem_threshold > 0",
    "ck_credit_card_params_credit_limit":
        "credit_limit IS NULL OR credit_limit > 0",
}


def upgrade():
    """Create the card-terms table and attach its audit trigger."""
    op.create_table(
        'credit_card_params',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('statement_close_day', sa.SmallInteger(), nullable=False),
        sa.Column('payment_due_day', sa.SmallInteger(), nullable=False),
        sa.Column('min_payment_percent', sa.Numeric(5, 4), nullable=False),
        sa.Column('min_payment_floor', sa.Numeric(12, 2), nullable=False),
        sa.Column(
            'cashback_rate', sa.Numeric(5, 4), nullable=False,
            server_default=sa.text('0'),
        ),
        sa.Column('auto_redeem_threshold', sa.Numeric(12, 2), nullable=True),
        sa.Column('credit_limit', sa.Numeric(12, 2), nullable=True),
        # The mixin-carried columns render after the model's own; order is
        # load-bearing nowhere (``app/models/mixins.py``).
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
        *[
            sa.CheckConstraint(sqltext, name=name)
            for name, sqltext in _CHECKS.items()
        ],
        # The two single-column keys the mixins declare (convention-named by
        # PostgreSQL, as ``loan_params``' and ``account_external_identities``'
        # are)...
        sa.ForeignKeyConstraint(
            ['account_id'], ['budget.accounts.id'], ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['auth.users.id'], ondelete='CASCADE',
        ),
        # ...and the composite one that makes the owner a guarantee rather
        # than a copy: keyed onto ``uq_accounts_id_user``.
        sa.ForeignKeyConstraint(
            ['account_id', 'user_id'],
            ['budget.accounts.id', 'budget.accounts.user_id'],
            name='fk_credit_card_params_owner',
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
        # One-to-one with the account: the mixin's ``unique=True``.
        sa.UniqueConstraint('account_id'),
        schema='budget',
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
    """Drop the table; its trigger and constraints go with it."""
    op.drop_table('credit_card_params', schema='budget')
