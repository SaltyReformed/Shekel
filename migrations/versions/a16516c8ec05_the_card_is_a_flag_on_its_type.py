"""the card is a flag on its type, not a projection kind

Revision ID: a16516c8ec05
Revises: ad573b07bede
Create Date: 2026-09-18

Plan step **credit_card:CC-1**, the first leaf of the arc as re-minted from
``docs/design/credit_card_from_scratch.md`` (section 3.1), under developer
ruling **credit_card:R-CC14** (2026-09-18: a credit card needs no balance engine
of its own -- its balance is ``opening + SUM(movements)``, the cash fold every
non-loan account already rides -- so it is a FLAG on the account type and not a
sixth ``AccountProjectionKind``)::

    ref.account_types    +has_revolving_credit   boolean NOT NULL DEFAULT false
                         Credit Card seed row    := true
                         +ck_account_types_revolving_is_plain

**The flag.**  ``has_revolving_credit`` is seed-only, exactly as
``has_appreciation`` is (revision ``b483e2b8a6d2``): the custom-type doors
(``/accounts/types``) expose neither, so a user can never mint a revolving type
of their own, and ``app.services.account_projection.is_revolving`` is the ONE
predicate that reads it.  ``server_default false`` makes the NOT NULL safe on
the populated table in one step; every existing type is non-revolving.

**The seed row.**  The built-in ``Credit Card`` type (``user_id IS NULL``) is
flagged INLINE here, the dual-seed pattern every ref-row migration in this
tree follows, so the flag is true the moment this revision is applied rather
than at the next ``seed_reference_data`` upsert (which writes the same value
from ``app.ref_seeds.ACCT_TYPE_SEEDS`` on every container start and keeps it
there).  Idempotent: the UPDATE sets a value the row either lacks or already
holds.

**The CHECK.**  A revolving type carries NONE of ``has_amortization`` /
``has_interest`` / ``has_appreciation`` / ``has_parameters``, so the "both
flags" type the 2026-07-19 plan resolved by classifier precedence is
UNREPRESENTABLE instead.  ``has_parameters`` is in the clause because of what
that column MEANS (``app/models/ref.py``: a ``*Params`` row that MUST be
created alongside the account): the card's params row of design 3.4 is
optional by design, so the type carries ``has_parameters = FALSE`` and the
CHECK stands.  The clause is stated once here and once on the model
(autogenerate does not diff CHECK constraints on an existing table, so the two
are kept equal by hand and by ``tests/test_models/test_revolving_flag.py``).
It is added AFTER the seed UPDATE: the Credit Card row carries none of the
four flags, so the constraint validates on the populated table, and a row
that did violate it would fail this revision loudly rather than be admitted.

**What the flag changes on the balance seam in the same step** (the one seam
surface design 3.1 names): ``balance_at._liability.liability_owed_at_dates``
stops holding a non-loan liability FLAT forward and reads the kind-correct
fold at each future sample date instead, so the net-worth horizon carries a
card's projected balance.  That is code, not schema; it is recorded here
because the flag and the arm ship as one leaf.

Self-contained dependency policy: this migration imports nothing from
``app``; the seed UPDATE runs as raw SQL against the catalog it is
constructing.

Downgrade reverses in order: the CHECK, then the column.  Nothing else
depends on either -- the flag is derivable from the seed list and is re-set
by the next upgrade -- so the downgrade needs no guard.
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = 'a16516c8ec05'
down_revision = 'ad573b07bede'
branch_labels = None
depends_on = None


# The seeded built-in only (``user_id IS NULL``): a user-owned row that shares
# the name is a different type and stays non-revolving, as the schema and the
# custom-type doors both intend.
_FLAG_CREDIT_CARD_SEED_SQL = (
    "UPDATE ref.account_types "
    "   SET has_revolving_credit = true "
    " WHERE name = 'Credit Card' AND user_id IS NULL"
)

# Stated identically on the model (``app/models/ref.py``, ``AccountType``).
_REVOLVING_IS_PLAIN_SQL = (
    "NOT has_revolving_credit OR NOT ("
    "has_amortization OR has_interest OR has_appreciation OR has_parameters)"
)


def upgrade():
    """Add the flag, flag the Credit Card seed row, add the CHECK."""
    op.add_column(
        'account_types',
        sa.Column(
            'has_revolving_credit', sa.Boolean(),
            server_default=sa.text('false'), nullable=False,
        ),
        schema='ref',
    )
    op.execute(_FLAG_CREDIT_CARD_SEED_SQL)
    op.create_check_constraint(
        'ck_account_types_revolving_is_plain', 'account_types',
        _REVOLVING_IS_PLAIN_SQL,
        schema='ref',
    )


def downgrade():
    """Drop the CHECK, then the flag, in inverse order of :func:`upgrade`."""
    op.drop_constraint(
        'ck_account_types_revolving_is_plain', 'account_types',
        schema='ref', type_='check',
    )
    op.drop_column('account_types', 'has_revolving_credit', schema='ref')
