"""add posting-ledger ref tables (ledger classes, posting kinds, posting sources)

Revision ID: f5037400dc5e
Revises: b483e2b8a6d2
Create Date: 2026-06-28 14:28:42.469527

Build-Order Step 2, Commit 1 (posting ledger + chart of accounts, piloted
on transfers; see
``docs/audits/balance_architecture/implementation_plan_posting_ledger_transfers.md``).

Creates the three ``ref`` lookup tables the later commits depend on:

  1. **ref.ledger_account_classes** -- the five fundamental accounting
     classes (Asset, Liability, Income, Expense, Equity).  Carries the
     ``is_debit_normal`` boolean (TRUE for Asset/Expense, FALSE for the
     credit-normal classes) so readers branch on a stored flag, never on
     the class name.  ``budget.ledger_accounts.class_id`` (Commit 2) FKs
     here.
  2. **ref.posting_kinds** -- the nature of a single posting leg.  Step 2
     seeds only ``transfer``; later steps INSERT ``income``, ``expense``,
     ``principal``, ``interest`` and similar via their own migrations
     (new values are data, never schema).
  3. **ref.posting_sources** -- the kind of source event that produced a
     journal entry.  Step 2 seeds only ``transfer``; later steps add
     ``transaction``, ``loan_payment``, ``paycheck``, ``credit_payback``.

**Inline seed rationale.**  Each table is seeded in this same migration
(not deferred to the entrypoint's ``seed_reference_data`` pass) so that
``ref_cache.init()`` resolves the new ``LedgerAccountClassEnum`` /
``PostingKindEnum`` / ``PostingSourceEnum`` members immediately after a
bare ``flask db upgrade`` -- an enum member with no matching row is a
fatal ``RuntimeError`` at app start, and a freshly-upgraded-but-not-yet-
seeded database would otherwise trip it.  ``ON CONFLICT (name) DO
NOTHING`` keeps the seed idempotent against a re-run and against the
entrypoint's later idempotent reseed (which carries the identical rows
via ``app/ref_seeds.py``).  The duplication between the two seed sites is
the established project pattern (see ``d3d25212504b`` /
``ref.loan_anchor_sources``): migrations run below the app layer and must
not import ``app`` code, so the bootstrap values live here in raw SQL and
the ongoing idempotent reseed lives in ``ref_seeds``.

**Not audited.**  All three are read-only seed catalogues, so they are
deliberately excluded from ``AUDITED_TABLES`` (the same inclusion
criteria that keep ``ref.statuses`` and ``ref.transaction_types`` out --
only the multi-tenant ``ref.account_types`` is audited).  No audit
trigger is attached here.

**Self-contained dependency policy.**  This migration imports nothing
from ``app`` -- not models, not enums, not ``ref_cache``.  All values are
inline raw SQL because migrations run at fragile bootstrap moments (the
ref-cache layer is itself initialising) and must survive aggressive
refactors in app code.

**Downgrade.**  Drops all three tables (their unique/PK constraints drop
with them).  Reversible: nothing references these tables at this point in
the chain (the ``budget.ledger_accounts`` /
``budget.account_postings`` / ``budget.journal_entries`` FKs that target
them arrive in the Commit 2 and Commit 3 migrations, which downgrade
first), and the seed rows are fully reproduced from the inline seed on
the next upgrade.
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = 'f5037400dc5e'
down_revision = 'b483e2b8a6d2'
branch_labels = None
depends_on = None


# Inline seed SQL.  The ``name`` values MUST match the enum ``.value``
# strings in ``app/enums.py`` exactly (``LedgerAccountClassEnum``,
# ``PostingKindEnum``, ``PostingSourceEnum``) or ``ref_cache.init()``
# raises at app start; the ``is_debit_normal`` flags MUST match
# ``app/ref_seeds.py``.  ``ON CONFLICT (name) DO NOTHING`` makes each
# statement idempotent against a partial re-run and against the
# entrypoint's later idempotent reseed.  Asset and Expense are
# debit-normal; Liability, Income, and Equity are credit-normal.
_SEED_LEDGER_ACCOUNT_CLASSES_SQL = (
    "INSERT INTO ref.ledger_account_classes (name, is_debit_normal) VALUES "
    "('Asset', TRUE), "
    "('Liability', FALSE), "
    "('Income', FALSE), "
    "('Expense', TRUE), "
    "('Equity', FALSE) "
    "ON CONFLICT (name) DO NOTHING"
)

_SEED_POSTING_KINDS_SQL = (
    "INSERT INTO ref.posting_kinds (name) VALUES "
    "('transfer') "
    "ON CONFLICT (name) DO NOTHING"
)

_SEED_POSTING_SOURCES_SQL = (
    "INSERT INTO ref.posting_sources (name) VALUES "
    "('transfer') "
    "ON CONFLICT (name) DO NOTHING"
)


def upgrade():
    """Create and inline-seed the three posting-ledger ref tables."""
    op.create_table(
        "ledger_account_classes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=12), nullable=False),
        sa.Column("is_debit_normal", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        schema="ref",
    )
    op.execute(_SEED_LEDGER_ACCOUNT_CLASSES_SQL)

    op.create_table(
        "posting_kinds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        schema="ref",
    )
    op.execute(_SEED_POSTING_KINDS_SQL)

    op.create_table(
        "posting_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        schema="ref",
    )
    op.execute(_SEED_POSTING_SOURCES_SQL)


def downgrade():
    """Drop the three posting-ledger ref tables (reverse create order)."""
    op.drop_table("posting_sources", schema="ref")
    op.drop_table("posting_kinds", schema="ref")
    op.drop_table("ledger_account_classes", schema="ref")
