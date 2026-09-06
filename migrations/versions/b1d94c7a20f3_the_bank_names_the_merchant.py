"""the bank names the merchant

Plan step **bank_import:X-f6a-3d** of
``docs/plans/implementation_plan_bank_import.md``, "The steps".

Review: Josh, 2026-08-19 -- APPROVED: the adapter RECORDS the merchant as a
column, over keying a destination policy on a string parsed at render time.

**What this adds is one nullable column, and what it removes is a parse.**
SECU appends its own normalized merchant in parentheses at the end of every
description cell -- ``... BJS FUEL #9151 25GARNER     NC (BJ's Fuel)``, on
**361 of 361** of the developer's recorded lines.  Until now the app pulled
that out at RENDER time (``statement_match._offers.merchant_of``) and used it
for one thing: prefilling the name box on the create-a-purchase form.  For that
job a reader was right, and being TOTAL was right with it -- a description with
no such token fell back to the whole description, so the box was never empty
and a wrong parse cost a badly-named row.

Plan step X-f6a-3d makes that string the KEY a merchant DESTINATION POLICY is
stated against (*lines from this merchant go in this budget line*), and a rule
that MATCHES on a value is a stronger claim than a default that displays it.
The reader could not carry it, for a measured reason: SECU's own OFX truncates
**326 of those same 361 descriptions to exactly 32 characters**, so dozens of
distinct merchants arrive as the identical string
``POINT OF SALE DEBIT L340 DATE 12``.  A total reader keys one policy on that
and fires it on all of them.  A NULLABLE column whose NULL means *this source
names no merchant* keys nothing instead, which is the direction a missing fact
has to fail in on a money path -- the same shape ruling **R-FW** already gave
``bank_statement_lines.transaction_on``.

**No figure moves.**  Nothing reads this column yet: the policy table and its
readers arrive in the next revision.  The two existing consumers of the parse
(the form's name box, and the description a recorded purchase is given) now
read ``merchant or description``, which is the old reader's own answer -- see
below for the measurement that says so on this data, row for row.

**THE BACKFILL, and why it is safe to run the parse once here.**  The adapter
reads the merchant from the source's DESCRIPTION CELL; a recorded row holds the
``Description | Memo`` JOIN, which is a different string.  So the backfill
cannot reproduce the adapter exactly in general, and it does not pretend to:

  * rows whose stored description carries the ``' | '`` memo join are left
    NULL, because from here there is no way to tell whose parentheses those
    are -- the honest answer, and the one a NULL is for.  Re-importing the
    file fills them in through ``_record._absorb_gained_facts``;
  * every other row gets the same anchored trailing token the adapter reads.

Measured on a 2026-08-18 production clone before writing this: **0 of 361
recorded lines carry a memo**, and the token resolves on **361 of 361** -- to
**59 distinct merchants**, the longest 28 characters
(``Department of motor vehicles``).  So on today's data the backfill is
complete and byte-identical to what a re-import would write.

**Reversible.**  The downgrade drops the column, the index and the CHECK.  It
loses only a value that is re-derivable from ``description`` by the same
expression, on every row this migration could fill.
"""
from alembic import op
import sqlalchemy as sa


revision = 'b1d94c7a20f3'
down_revision = 'e7a2c4f18d05'
branch_labels = None
depends_on = None


#: The trailing parenthesised token, anchored at the end -- the SAME rule
#: ``_secu_csv._MERCHANT`` reads, spelled in POSIX so this migration owns no
#: import of application code that will move underneath it.  ``NULLIF(BTRIM(
#: ...), '')`` is the adapter's "an all-whitespace token is not a name".
_BACKFILL_SQL = r"""
UPDATE budget.bank_statement_lines
   SET merchant = NULLIF(
           BTRIM(
               substring(description from '\(([^()]{1,100})\)[[:space:]]*$')
           ),
           ''
       )
 WHERE position(' | ' in description) = 0
"""


def upgrade():
    """Add the merchant column, backfill it, and constrain it."""
    op.add_column(
        'bank_statement_lines',
        sa.Column('merchant', sa.String(length=100), nullable=True),
        schema='budget',
    )
    op.execute(_BACKFILL_SQL)
    # A merchant is a NAME or it is nothing.  Stricter than the provenance
    # columns beside it because this one is a KEY: a blank merchant is a policy
    # the owner could neither read on the screen nor restate.  Added AFTER the
    # backfill, which cannot produce one -- ``NULLIF(BTRIM(...), '')`` -- so
    # this validates rather than blocks.
    op.create_check_constraint(
        'ck_bank_statement_lines_merchant_not_blank',
        'bank_statement_lines',
        "merchant IS NULL OR btrim(merchant) <> ''",
        schema='budget',
    )
    # The review screen groups an account's unexplained lines BY MERCHANT.
    # Partial, because a NULL merchant joins no policy and is never looked up.
    op.create_index(
        'idx_bank_statement_lines_account_merchant',
        'bank_statement_lines',
        ['account_id', 'merchant'],
        unique=False,
        schema='budget',
        postgresql_where=sa.text('merchant IS NOT NULL'),
    )


def downgrade():
    """Drop the merchant column with its index and CHECK."""
    op.drop_index(
        'idx_bank_statement_lines_account_merchant',
        table_name='bank_statement_lines',
        schema='budget',
        postgresql_where=sa.text('merchant IS NOT NULL'),
    )
    op.drop_constraint(
        'ck_bank_statement_lines_merchant_not_blank',
        'bank_statement_lines',
        schema='budget',
        type_='check',
    )
    op.drop_column('bank_statement_lines', 'merchant', schema='budget')
