"""The sighting names the merchant; a line's merchant is a read over its sightings.

Plan step ``bank_import:X-f6b-1b``, rulings **R-BI16** (the key is stored
once, on the sighting; the line's merchant is ONE SQL producer over its
sightings) and **R-BI17** (the sighting's merchant WORD column is deleted; the
word lives once, on the merchant row).  MOVES NO MONEY: no balance input is
written or rewritten, no line changes identity, day or amount, and no rule
changes the merchant it is stated against -- measured on a clone of
production 2026-09-18, the read this revision installs answers the stored key
for 306 of 306 lines.

What changes:

1. ``budget.statement_line_sightings`` gains ``merchant_id`` -- the
   :class:`~app.models.merchant.Merchant` this sighting's word names -- held
   to the sighting's own account by
   ``fk_statement_line_sightings_merchant_account`` (the composite the line's
   key carried since ``bank_import:X-gd-1``, same ``NO ACTION``), indexed by
   ``idx_statement_line_sightings_account_merchant`` for the grouped reads.
   Backfilled from each sighting's word: a merchant row is minted for any
   word that has none (a later sighting's different word for a line the
   first import had already keyed was never resolved to a row), then every
   worded sighting is pointed at its account's row for that word.
2. ``budget.statement_line_sightings`` DROPS ``merchant`` (the word).  The
   word is ``budget.merchants.name``, verbatim, written by one path and never
   edited, so the column was a second home for one fact.
3. ``budget.bank_statement_lines`` DROPS ``merchant_id`` with
   ``fk_bank_statement_lines_merchant_account`` and
   ``idx_bank_statement_lines_account_merchant``.  A derived value stored
   beside its source (finding **BI-504**: a line kept a key a since-deleted
   import had minted, because nothing re-derived it).  The line's merchant is
   now the model's ``column_property`` over the sightings.
4. SWEEPS the merchants no sighting and no standing rule names.  A BI-504
   key was the only thing reaching its merchant row; once the column is gone
   that row is one the new schema never produces (``resolve_merchants``
   writes every row it mints onto a sighting in the same pass) and nothing
   would ever sweep (``orphan_merchants_by_import`` attributes a merchant to
   the import whose sightings name it, so one no sighting names is nobody's).
   The same act the app's delete door performs for the same state
   (``statement_import._undo._forget_merchants``), and the mirror of the
   downgrade's own sweep; 0 such rows on production 2026-09-18.

The DOWNGRADE is exact for every state this revision can produce: it writes
the line's key back as the model reads it -- the merchant named by the
EARLIEST sighting (by act order) that names one -- and each sighting's word
back from its merchant row, then sweeps the merchants no line's key and no
standing rule names, which is the invariant the old schema held ("no merchant
named by no line, only because no door creates one").  A merchant minted at
step 1 for a second word on a known line is such a row after the key moves
back, and so is a merchant only a non-earliest sighting named; the sweep is
what the app's own delete door does for the same state
(``statement_import._undo._forget_merchants``), not a placement withdrawn.
A downgrade that left them would hand the old schema a state it never
produced, exactly as an upgrade without step 4 would hand the new one.

The production measurement behind "the read answers the stored key 306 of
306" is one import, 306 lines, one sighting each, no standing rule: it grades
the backfill (steps 1 and 2) and that the read reaches it, not the
earliest-by-act-order choice, which only a line two imports sighted can
exercise -- ``tests/test_models/test_merchant_key_on_sighting_migration.py``
and ``test_merchant_schema.py`` seed that shape.

Revision ID: 3ef820b7dd52
Revises: af07125d00f1
Create Date: 2026-09-18 18:03:00
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "3ef820b7dd52"
down_revision = "af07125d00f1"
branch_labels = None
depends_on = None


# The order two acts on one account are told apart in: the instant, then the
# id -- ``StatementImport.act_order``, spelled here in SQL because a
# migration reads the tree as it is and not the model as it will be.
_ACT_ORDER = "i.created_at, i.id"

# UP, step 1a: a merchant row for every word no row names yet.  ``ON CONFLICT
# DO NOTHING`` against ``uq_merchants_account_name`` is what
# ``statement_import._merchants.resolve_merchants`` does for the same reason.
_MINT_MISSING_MERCHANTS_SQL = """
INSERT INTO budget.merchants (account_id, name)
SELECT DISTINCT s.account_id, s.merchant
FROM budget.statement_line_sightings AS s
WHERE s.merchant IS NOT NULL
ON CONFLICT (account_id, name) DO NOTHING
"""

# UP, step 1b: each worded sighting points at its account's row for that word.
_POINT_SIGHTINGS_SQL = """
UPDATE budget.statement_line_sightings AS s
SET merchant_id = m.id
FROM budget.merchants AS m
WHERE m.account_id = s.account_id
  AND m.name = s.merchant
  AND s.merchant IS NOT NULL
"""

# DOWN, step 3: the line's key as the model reads it -- the EARLIEST
# sighting naming a merchant, by act order.  ``DISTINCT ON`` takes the first
# row per line in the given order.
_WRITE_LINE_KEYS_BACK_SQL = f"""
UPDATE budget.bank_statement_lines AS l
SET merchant_id = named.merchant_id
FROM (
    SELECT DISTINCT ON (s.line_id) s.line_id, s.merchant_id
    FROM budget.statement_line_sightings AS s
    JOIN budget.statement_imports AS i ON i.id = s.import_id
    WHERE s.merchant_id IS NOT NULL
    ORDER BY s.line_id, {_ACT_ORDER}
) AS named
WHERE named.line_id = l.id
"""

# DOWN, step 2: each sighting's word from its merchant row.
_WRITE_WORDS_BACK_SQL = """
UPDATE budget.statement_line_sightings AS s
SET merchant = m.name
FROM budget.merchants AS m
WHERE m.id = s.merchant_id
"""

# UP, step 4: the merchants nothing at this revision can reach -- named by no
# sighting and by no standing rule.  Run AFTER the line's key is gone, because
# a line named its merchant under a ``NO ACTION`` key and would have refused
# the delete while it stood.
_SWEEP_UNSIGHTED_MERCHANTS_SQL = """
DELETE FROM budget.merchants AS m
WHERE NOT EXISTS (
    SELECT 1 FROM budget.statement_line_sightings AS s
    WHERE s.merchant_id = m.id
)
AND NOT EXISTS (
    SELECT 1 FROM budget.merchant_rules AS r
    WHERE r.merchant_id = m.id
)
"""

# DOWN, step 4: the merchants nothing below this revision can reach -- named
# by no line's key and by no standing rule.  Run AFTER the sightings' keys are
# gone, because a sighting names its merchant under a ``NO ACTION`` key and
# would refuse the delete while it stood.
_SWEEP_UNNAMED_MERCHANTS_SQL = """
DELETE FROM budget.merchants AS m
WHERE NOT EXISTS (
    SELECT 1 FROM budget.bank_statement_lines AS l
    WHERE l.merchant_id = m.id
)
AND NOT EXISTS (
    SELECT 1 FROM budget.merchant_rules AS r
    WHERE r.merchant_id = m.id
)
"""


def upgrade():
    """Move the merchant key onto the sightings; drop the word and the line's key."""
    # 1. The sighting's key, backfilled from its word.
    op.add_column(
        "statement_line_sightings",
        sa.Column("merchant_id", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.execute(_MINT_MISSING_MERCHANTS_SQL)
    op.execute(_POINT_SIGHTINGS_SQL)
    op.create_foreign_key(
        "fk_statement_line_sightings_merchant_account",
        "statement_line_sightings", "merchants",
        ["merchant_id", "account_id"], ["id", "account_id"],
        source_schema="budget", referent_schema="budget",
    )
    op.create_index(
        "idx_statement_line_sightings_account_merchant",
        "statement_line_sightings", ["account_id", "merchant_id"],
        schema="budget",
        postgresql_where=sa.text("merchant_id IS NOT NULL"),
    )

    # 2. The word lives once, on the merchant row.
    op.drop_column("statement_line_sightings", "merchant", schema="budget")

    # 3. The line's stored copy of a derived value goes.
    op.drop_index(
        "idx_bank_statement_lines_account_merchant",
        table_name="bank_statement_lines", schema="budget",
    )
    op.drop_constraint(
        "fk_bank_statement_lines_merchant_account", "bank_statement_lines",
        schema="budget", type_="foreignkey",
    )
    op.drop_column("bank_statement_lines", "merchant_id", schema="budget")

    # 4. What nothing at this revision can reach.
    op.execute(_SWEEP_UNSIGHTED_MERCHANTS_SQL)


def downgrade():
    """Write the line's key and the sighting's word back; sweep what nothing names."""
    # 3. The line's key, from its earliest naming sighting.
    op.add_column(
        "bank_statement_lines",
        sa.Column("merchant_id", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.execute(_WRITE_LINE_KEYS_BACK_SQL)
    op.create_foreign_key(
        "fk_bank_statement_lines_merchant_account", "bank_statement_lines",
        "merchants", ["merchant_id", "account_id"], ["id", "account_id"],
        source_schema="budget", referent_schema="budget",
    )
    op.create_index(
        "idx_bank_statement_lines_account_merchant",
        "bank_statement_lines", ["account_id", "merchant_id"],
        schema="budget",
        postgresql_where=sa.text("merchant_id IS NOT NULL"),
    )

    # 2. The word, from the merchant row.
    op.add_column(
        "statement_line_sightings",
        sa.Column("merchant", sa.String(length=100), nullable=True),
        schema="budget",
    )
    op.execute(_WRITE_WORDS_BACK_SQL)

    # 1. The sighting's key goes.
    op.drop_index(
        "idx_statement_line_sightings_account_merchant",
        table_name="statement_line_sightings", schema="budget",
    )
    op.drop_constraint(
        "fk_statement_line_sightings_merchant_account",
        "statement_line_sightings", schema="budget", type_="foreignkey",
    )
    op.drop_column("statement_line_sightings", "merchant_id", schema="budget")

    # 4. What nothing below this revision can reach.
    op.execute(_SWEEP_UNNAMED_MERCHANTS_SQL)
