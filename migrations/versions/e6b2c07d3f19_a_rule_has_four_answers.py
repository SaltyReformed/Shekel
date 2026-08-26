"""a rule has four answers, and an act records whether a rule performed it

Revision ID: e6b2c07d3f19
Revises: d4a1f8b0c25e
Create Date: 2026-08-26

Plan step **bank_import:X-gd-2** of
``docs/plans/implementation_plan_bank_import.md``, "The steps", second commit.
Rulings **R-GI**, **R-GS** and **R-GT** (developer, 2026-08-24 / 2026-08-25).

**Two columns, and they are the whole of this revision.**

``budget.merchant_rules.never_a_purchase`` turns THREE stored answers into
FOUR.  The fourth is *ask me every time*, which replaces the withdrawal: a rule
row is never un-stated, so an owner who wants no standing answer for a merchant
states that instead of deleting the row.  The two answers that name no
container -- *never a purchase* and *ask me every time* -- carried identical
columns before this revision, so a boolean is exactly what has to be added to
tell them apart (**R-GS**; a ref-keyed discriminator was refused because a
CHECK cannot reference a ``ref`` row's id, and because it would need a data
migration when ``bank_import:X-f6c`` collapses the two container columns).

``budget.statement_matches.applied_by_rule`` records whether a standing rule
performed an act or a person ticked it (**R-GT**).  WHICH rule stays derivable
-- the matched line carries the account and the merchant, which is the rule's
key -- so nothing points at the rule row.

**THE BACKFILL IS THE MONEY HALF OF THIS REVISION, and it is one statement.**
Before it, a rule with all three container columns NULL means *never a
purchase* -- the answer that bars a bank line from ever becoming a purchase.
After it, that same shape means *ask me every time* unless the flag says
otherwise.  So every existing container-less row must be set ``TRUE``, and a
run that skipped it would silently lift a bar the owner had set.  Measured on a
clone of the developer's own database, 2026-08-26: **29 rules, of which 16 name
a template, 12 name a new envelope, and exactly ONE is container-less** --
``Capital One Credit Card``, which is 9 of the 91 unexplained outflows on the
developer's own statement and `-$7,412.94` of the `-$11,336.36` in that list.
Production carries neither this table nor a single bank line (checked
2026-08-26: ``budget.merchant_rules`` and ``budget.merchants`` both absent, prod
at ``a4c6f1d92b73``), so there the backfill selects nothing.

``statement_matches`` needs no equivalent judgement: no rule can have performed
an act before the door that applies one exists, so ``FALSE`` is not a default
standing in for the truth -- it IS the truth for all 221 acts on the
developer's dev database.

**Both columns end NOT NULL with NO default**, and that is the shape rather
than an oversight.  Each is filled by a transient ``server_default`` that is
dropped in the same revision, so existing rows get a value and future INSERTs
must state one.  A surviving default on either would answer a question the
writer failed to ask: for the first, which answer the owner gave; for the
second, whether the owner consented at all.

**Reversible, and the downgrade is value-lossy in exactly one way, stated.**
Dropping ``never_a_purchase`` would turn every *ask me every time* row back
into *never a purchase*, which is the same silent bar this revision's backfill
exists to prevent -- in the more dangerous direction, because it INVENTS a
money decision.  So the downgrade DELETES those rows first: the older schema
has no representation for *ask me every time*, and the nearest true statement
in it is the absence of a row, which is also what the older screen would have
written for the same click.  The rows that survive are the three answers the
old schema can hold, unchanged.
"""
from alembic import op
import sqlalchemy as sa


revision = 'e6b2c07d3f19'
down_revision = 'd4a1f8b0c25e'
branch_labels = None
depends_on = None


#: Every rule that named no container MEANT *never a purchase*, so say so.
#:
#: **Held as a module constant so a test can execute the string this migration
#: executes**, which is the convention ``efffcf647644``'s ``BACKFILL_SQL``
#: established here: a test that re-typed the predicate would agree with a
#: mistake as readily as with the truth.
#:
#: The predicate is the OLD schema's own reading of "answer (3)" -- all three
#: container columns NULL -- and not a list of ids, because what has to be
#: carried across is the meaning of a shape rather than the identity of the one
#: row that happens to have it today.
CLAIM_NEVER_SQL = """
    UPDATE budget.merchant_rules
       SET never_a_purchase = TRUE
     WHERE template_id IS NULL
       AND envelope_name IS NULL
       AND category_id IS NULL
"""

#: On the way back, the answer the older schema cannot hold leaves.
#:
#: It is *ask me every time*: container-less and not *never*.  Keeping the row
#: would republish it as *never a purchase* under the old CHECK, which is a bar
#: on money the owner never asked for -- so the row goes, which is what *the
#: owner has not said* looks like in the schema this returns to.
FORGET_ALWAYS_ASK_SQL = """
    DELETE FROM budget.merchant_rules
     WHERE never_a_purchase IS FALSE
       AND template_id IS NULL
       AND envelope_name IS NULL
       AND category_id IS NULL
"""

#: The THREE shapes the older CHECK allows, restated for the downgrade.
_THREE_ANSWERS = (
    "(template_id IS NOT NULL AND envelope_name IS NULL "
    "AND category_id IS NULL) "
    "OR (template_id IS NULL AND envelope_name IS NOT NULL "
    "AND category_id IS NOT NULL) "
    "OR (template_id IS NULL AND envelope_name IS NULL "
    "AND category_id IS NULL)"
)

#: The FOUR, which is the three with the flag pinned false on the two that name
#: a container.  The pin is what keeps the answer readable in one order: without
#: it a row could name a template AND claim never-a-purchase, and the reader
#: that looks at the container and the reader that looks at the flag would
#: disagree about whether that line may become a purchase.
_FOUR_ANSWERS = (
    "(template_id IS NOT NULL AND envelope_name IS NULL "
    "AND category_id IS NULL AND NOT never_a_purchase) "
    "OR (template_id IS NULL AND envelope_name IS NOT NULL "
    "AND category_id IS NOT NULL AND NOT never_a_purchase) "
    "OR (template_id IS NULL AND envelope_name IS NULL "
    "AND category_id IS NULL)"
)


def upgrade():
    """Add the fourth answer's discriminator and the act's consent receipt."""
    # ---- the rule store gains a fourth answer ---------------------------
    op.add_column(
        'merchant_rules',
        sa.Column(
            'never_a_purchase', sa.Boolean(), nullable=False,
            server_default=sa.false(),
        ),
        schema='budget',
    )
    # BEFORE the CHECK is replaced, because until this runs the container-less
    # rows carry FALSE and mean something the owner never said.
    op.execute(CLAIM_NEVER_SQL)
    op.drop_constraint(
        'ck_merchant_rules_one_answer', 'merchant_rules',
        schema='budget', type_='check',
    )
    op.create_check_constraint(
        'ck_merchant_rules_one_answer', 'merchant_rules',
        _FOUR_ANSWERS, schema='budget',
    )
    # The default has done its one job.  Leaving it would let a future writer
    # store *ask me every time* for a merchant nobody answered for.
    op.alter_column(
        'merchant_rules', 'never_a_purchase', server_default=None,
        schema='budget',
    )

    # ---- an act records who consented -----------------------------------
    op.add_column(
        'statement_matches',
        sa.Column(
            'applied_by_rule', sa.Boolean(), nullable=False,
            server_default=sa.false(),
        ),
        schema='budget',
    )
    # No backfill statement: every act that exists was ticked by a person,
    # because the door that applies a rule does not exist yet
    # (plan step ``bank_import:X-ge``).  FALSE is what they are, and the
    # transient default is what writes it.
    op.alter_column(
        'statement_matches', 'applied_by_rule', server_default=None,
        schema='budget',
    )


def downgrade():
    """Drop the consent receipt, and the answer the older schema cannot hold."""
    op.drop_column('statement_matches', 'applied_by_rule', schema='budget')

    # FIRST, while the flag still exists to tell the two container-less
    # answers apart.  After the column is dropped they are indistinguishable
    # and every one of them reads as *never a purchase*.
    op.execute(FORGET_ALWAYS_ASK_SQL)
    op.drop_constraint(
        'ck_merchant_rules_one_answer', 'merchant_rules',
        schema='budget', type_='check',
    )
    op.create_check_constraint(
        'ck_merchant_rules_one_answer', 'merchant_rules',
        _THREE_ANSWERS, schema='budget',
    )
    op.drop_column('merchant_rules', 'never_a_purchase', schema='budget')
