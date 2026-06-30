"""add cash posting kinds and transaction source

Revision ID: 97bc03c2aa4c
Revises: db239773c2fd
Create Date: 2026-06-29 08:22:52.378400

Build-Order Step 3, Commit 1 (post confirmed cash transactions + cleared
envelope entries; see
``docs/audits/balance_architecture/implementation_plan_posting_ledger_cash_envelopes.md``).

Adds the reference values the cash-posting leg of Step 3 needs to the two
``ref`` lookup tables Step 2 already created (``f5037400dc5e``).  No table
is created or altered -- new reference values are data, never schema (the
``ref.posting_kinds`` / ``ref.posting_sources`` model docstrings record
this contract):

  * **ref.posting_kinds** gains ``income`` and ``expense`` -- the economic
    nature of an ordinary settled transaction's two balanced legs (the
    cash-account leg and its per-category counter-leg).
  * **ref.posting_sources** gains ``transaction`` -- the source-event kind
    for a journal entry produced by an ordinary (non-transfer) settled
    transaction, distinct from Step 2's ``transfer`` source.

**Inline seed rationale.**  The new rows are seeded in this migration (not
deferred to the entrypoint's ``seed_reference_data`` pass) so that
``ref_cache.init()`` resolves the new ``PostingKindEnum.INCOME`` /
``PostingKindEnum.EXPENSE`` / ``PostingSourceEnum.TRANSACTION`` members
immediately after a bare ``flask db upgrade`` -- an enum member with no
matching row is a fatal ``RuntimeError`` at app start, and a
freshly-upgraded-but-not-yet-seeded database would otherwise trip it.
``ON CONFLICT (name) DO NOTHING`` keeps the seed idempotent against a
re-run and against the entrypoint's later idempotent reseed (which carries
the identical rows via ``app/ref_seeds.py``).  This duplication between the
migration and ``ref_seeds`` is the established project pattern (see
``f5037400dc5e`` / ``07198f0d6716``): migrations run below the app layer
and must not import ``app`` code, so the bootstrap values live here in raw
SQL and the ongoing idempotent reseed lives in ``ref_seeds``.

**Not audited.**  Both tables are read-only seed catalogues, deliberately
excluded from ``AUDITED_TABLES`` (the same criterion that keeps the other
``ref`` lookup tables out); no audit trigger is involved, and adding rows
does not change a table's audited status.

**Self-contained dependency policy.**  This migration imports nothing from
``app`` -- not models, not enums, not ``ref_cache``.  All values are inline
raw SQL because migrations run at fragile bootstrap moments (the ref-cache
layer is itself initialising) and must survive aggressive refactors in app
code.

**Downgrade.**  Deletes exactly the three rows this migration adds, by
name.  Reversible: the next upgrade re-inserts them identically from the
inline seed.  Safe at this revision because nothing references the new
rows yet -- the FKs that point at these tables
(``budget.account_postings.posting_kind_id`` and
``budget.journal_entries.source_kind_id``) are ``ondelete=RESTRICT``, so
once Step 3 begins posting transaction-sourced entries, those RESTRICT
constraints would correctly block this DELETE until the higher Step-3
revisions (which create and later remove those entries) are themselves
downgraded first.
"""
from alembic import op


# Revision identifiers, used by Alembic.
revision = '97bc03c2aa4c'
down_revision = 'db239773c2fd'
branch_labels = None
depends_on = None


# Inline seed SQL.  The ``name`` values MUST match the enum ``.value``
# strings in ``app/enums.py`` exactly (``PostingKindEnum.INCOME`` /
# ``EXPENSE``, ``PostingSourceEnum.TRANSACTION``) or ``ref_cache.init()``
# raises at app start; they MUST also match the lists in
# ``app/ref_seeds.py``.  ``ON CONFLICT (name) DO NOTHING`` makes each
# statement idempotent against a partial re-run and against the
# entrypoint's later idempotent reseed.
_SEED_CASH_POSTING_KINDS_SQL = (
    "INSERT INTO ref.posting_kinds (name) VALUES "
    "('income'), "
    "('expense') "
    "ON CONFLICT (name) DO NOTHING"
)

_SEED_TRANSACTION_POSTING_SOURCE_SQL = (
    "INSERT INTO ref.posting_sources (name) VALUES "
    "('transaction') "
    "ON CONFLICT (name) DO NOTHING"
)

# Downgrade SQL.  Deletes exactly the rows the upgrade adds, by name, so a
# re-upgrade reproduces them identically from the inline seed above.
_DROP_CASH_POSTING_KINDS_SQL = (
    "DELETE FROM ref.posting_kinds WHERE name IN ('income', 'expense')"
)

_DROP_TRANSACTION_POSTING_SOURCE_SQL = (
    "DELETE FROM ref.posting_sources WHERE name = 'transaction'"
)


def upgrade():
    """Inline-seed the ``income`` / ``expense`` kinds and ``transaction`` source."""
    op.execute(_SEED_CASH_POSTING_KINDS_SQL)
    op.execute(_SEED_TRANSACTION_POSTING_SOURCE_SQL)


def downgrade():
    """Delete the three Step-3 reference rows this migration added."""
    op.execute(_DROP_CASH_POSTING_KINDS_SQL)
    op.execute(_DROP_TRANSACTION_POSTING_SOURCE_SQL)
