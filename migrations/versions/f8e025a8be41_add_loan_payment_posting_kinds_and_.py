"""add loan-payment posting kinds/source and the ledger-account-kind ref table

Revision ID: f8e025a8be41
Revises: 7d63529e4300
Create Date: 2026-06-30 00:00:00.000000

Build-Order Step 4, Commit 1 (post confirmed loan payments with their real
principal / interest / escrow split; see
``docs/audits/balance_architecture/implementation_plan_posting_ledger_loan_payments.md``).

Adds the reference values and the one new ``ref`` lookup table the
loan-payment correction needs.  Two existing tables gain rows; one new
table is created and seeded:

  * **ref.posting_kinds** gains ``principal`` / ``interest`` / ``escrow`` /
    ``refund`` -- the four legs of a confirmed loan payment's real-split
    correction (the loan principal adjustment, the accrued interest expense,
    the configured escrow expense, and the payoff-overpayment refund
    receivable).  New reference VALUES are data, never schema (the
    ``ref.posting_kinds`` model docstring records this contract).
  * **ref.posting_sources** gains ``loan_payment`` -- the source-event kind
    for the correction journal entry appended to a confirmed loan-payment
    transfer, distinct from Step 2's ``transfer`` and Step 3's
    ``transaction`` sources.
  * **ref.ledger_account_kinds** (NEW) -- the explicit, positive row-kind
    discriminator for ``budget.ledger_accounts`` that replaces inferring a
    row's kind from the NULL-pattern of its ``account_id`` / ``category_id``
    / ``is_fallback`` columns.  Seven kinds are seeded: the four the chart
    already uses (``linked``, ``category``, ``fallback``, ``orphan``) plus the
    three per-loan accounts the correction books into (``loan_interest`` and
    ``loan_escrow`` Expense; ``loan_refund`` Asset).  Commit 2 adds the
    ``budget.ledger_accounts.kind_id`` FK that targets this table.

**Inline seed rationale.**  Every value is seeded in this same migration
(not deferred to the entrypoint's ``seed_reference_data`` pass) so that
``ref_cache.init()`` resolves the new ``PostingKindEnum`` /
``PostingSourceEnum`` / ``LedgerAccountKindEnum`` members immediately after a
bare ``flask db upgrade`` -- an enum member with no matching row is a fatal
``RuntimeError`` at app start, and a freshly-upgraded-but-not-yet-seeded
database would otherwise trip it.  ``ON CONFLICT (name) DO NOTHING`` keeps the
seed idempotent against a re-run and against the entrypoint's later
idempotent reseed (which carries the identical rows via ``app/ref_seeds.py``).
This duplication between the migration and ``ref_seeds`` is the established
project pattern (see ``f5037400dc5e`` / ``97bc03c2aa4c``): migrations run below
the app layer and must not import ``app`` code, so the bootstrap values live
here in raw SQL and the ongoing idempotent reseed lives in ``ref_seeds``.

**Not audited.**  All three tables are read-only seed catalogues, deliberately
excluded from ``AUDITED_TABLES`` (the same inclusion criteria that keep
``ref.statuses`` and the other lookup tables out -- only the multi-tenant
``ref.account_types`` is audited).  Creating a seed catalogue or adding rows
to one does not change a table's audited status, so no audit trigger is
attached.

**Self-contained dependency policy.**  This migration imports nothing from
``app`` -- not models, not enums, not ``ref_cache``.  All values are inline
raw SQL because migrations run at fragile bootstrap moments (the ref-cache
layer is itself initialising) and must survive aggressive refactors in app
code.

**Downgrade.**  Drops ``ref.ledger_account_kinds`` (its unique/PK constraints
and owned sequence drop with it) and deletes exactly the five rows the
upgrade adds to ``ref.posting_kinds`` / ``ref.posting_sources``, by name, so a
re-upgrade reproduces them identically from the inline seed.  Safe at this
revision because nothing references the new artefacts yet -- the
``budget.ledger_accounts.kind_id`` FK (Commit 2) and the first
``loan_payment`` journal entries / ``principal``-``refund`` postings (Commits
4-6) arrive in higher revisions, which downgrade first.  Once those exist,
their ``ondelete=RESTRICT`` FKs would correctly block this DELETE / DROP until
the higher revisions are themselves downgraded.
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = 'f8e025a8be41'
down_revision = '7d63529e4300'
branch_labels = None
depends_on = None


# Inline seed SQL.  The ``name`` values MUST match the enum ``.value`` strings
# in ``app/enums.py`` exactly (``PostingKindEnum`` ``PRINCIPAL`` / ``INTEREST``
# / ``ESCROW`` / ``REFUND``, ``PostingSourceEnum.LOAN_PAYMENT``,
# ``LedgerAccountKindEnum``) or ``ref_cache.init()`` raises at app start; they
# MUST also match the lists in ``app/ref_seeds.py``.  ``ON CONFLICT (name) DO
# NOTHING`` makes each statement idempotent against a partial re-run and
# against the entrypoint's later idempotent reseed.
_SEED_LOAN_POSTING_KINDS_SQL = (
    "INSERT INTO ref.posting_kinds (name) VALUES "
    "('principal'), "
    "('interest'), "
    "('escrow'), "
    "('refund') "
    "ON CONFLICT (name) DO NOTHING"
)

_SEED_LOAN_PAYMENT_POSTING_SOURCE_SQL = (
    "INSERT INTO ref.posting_sources (name) VALUES "
    "('loan_payment') "
    "ON CONFLICT (name) DO NOTHING"
)

_SEED_LEDGER_ACCOUNT_KINDS_SQL = (
    "INSERT INTO ref.ledger_account_kinds (name) VALUES "
    "('linked'), "
    "('category'), "
    "('fallback'), "
    "('orphan'), "
    "('loan_interest'), "
    "('loan_escrow'), "
    "('loan_refund') "
    "ON CONFLICT (name) DO NOTHING"
)

# Downgrade SQL.  Deletes exactly the rows the upgrade adds to the two
# pre-existing tables, by name, so a re-upgrade reproduces them identically
# from the inline seed above.  ``ref.ledger_account_kinds`` needs no row
# DELETE -- the table itself is dropped.
_DROP_LOAN_POSTING_KINDS_SQL = (
    "DELETE FROM ref.posting_kinds "
    "WHERE name IN ('principal', 'interest', 'escrow', 'refund')"
)

_DROP_LOAN_PAYMENT_POSTING_SOURCE_SQL = (
    "DELETE FROM ref.posting_sources WHERE name = 'loan_payment'"
)


def upgrade():
    """Create + seed ``ref.ledger_account_kinds`` and add the loan-payment rows."""
    op.create_table(
        "ledger_account_kinds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        schema="ref",
    )
    op.execute(_SEED_LEDGER_ACCOUNT_KINDS_SQL)

    op.execute(_SEED_LOAN_POSTING_KINDS_SQL)
    op.execute(_SEED_LOAN_PAYMENT_POSTING_SOURCE_SQL)


def downgrade():
    """Delete the loan-payment kinds/source and drop ``ref.ledger_account_kinds``."""
    op.execute(_DROP_LOAN_POSTING_KINDS_SQL)
    op.execute(_DROP_LOAN_PAYMENT_POSTING_SOURCE_SQL)
    op.drop_table("ledger_account_kinds", schema="ref")
