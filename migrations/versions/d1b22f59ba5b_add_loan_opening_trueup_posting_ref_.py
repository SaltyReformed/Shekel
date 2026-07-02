"""add loan opening/true-up posting kinds, sources, and equity-opening kind

Revision ID: d1b22f59ba5b
Revises: e2a9f1c7b4d6
Create Date: 2026-07-01 09:30:00.000000

Loan read switch, Commit 1 (the deferred second half of Build-Order Step 4;
see ``docs/audits/balance_architecture/implementation_plan_loan_read_switch.md``).

Adds the reference values the genesis (opening-equity) posting ledger needs
to three ``ref`` lookup tables Steps 2-4 already created.  No table is
created or altered -- new reference values are data, never schema (the
``ref.posting_kinds`` / ``ref.posting_sources`` / ``ref.ledger_account_kinds``
model docstrings record this contract):

  * **ref.posting_kinds** gains ``opening`` and ``trueup`` -- the two-leg
    kinds of the loan-genesis postings.  ``opening`` tags both legs of the
    once-per-loan opening-equity entry (the loan-liability leg and its
    equity counter-leg) that books the origination balance; ``trueup`` tags
    both legs of an append-only balance-correction entry that drives the
    ledger balance to a user-verified value without rewriting the prior
    payment postings.
  * **ref.posting_sources** gains ``loan_opening`` and ``loan_trueup`` -- the
    source-event kinds for those two journal entries, distinct from Step 4's
    ``loan_payment`` correction source.  Neither entry links a transfer nor a
    transaction (both source FKs are nullable); the source kind disambiguates
    them.
  * **ref.ledger_account_kinds** gains ``equity_opening`` -- the row-kind
    discriminator for the per-loan Equity account that holds the credit
    counter-leg of the opening-equity entry.  It joins the three per-loan
    accounts Step 4 added (``loan_interest`` / ``loan_escrow`` /
    ``loan_refund``); Commit 2 extends the loan chart resolver to create it.

**Inline seed rationale.**  Every value is seeded in this same migration
(not deferred to the entrypoint's ``seed_reference_data`` pass) so that
``ref_cache.init()`` resolves the new ``PostingKindEnum`` /
``PostingSourceEnum`` / ``LedgerAccountKindEnum`` members immediately after a
bare ``flask db upgrade`` -- an enum member with no matching row is a fatal
``RuntimeError`` at app start, and a freshly-upgraded-but-not-yet-seeded
database would otherwise trip it.  ``ON CONFLICT (name) DO NOTHING`` keeps
the seed idempotent against a re-run and against the entrypoint's later
idempotent reseed (which carries the identical rows via ``app/ref_seeds.py``).
This duplication between the migration and ``ref_seeds`` is the established
project pattern (see ``f5037400dc5e`` / ``97bc03c2aa4c`` / ``f8e025a8be41``):
migrations run below the app layer and must not import ``app`` code, so the
bootstrap values live here in raw SQL and the ongoing idempotent reseed lives
in ``ref_seeds``.

**Not audited.**  All three tables are read-only seed catalogues, deliberately
excluded from ``AUDITED_TABLES`` (the same inclusion criteria that keep the
other ``ref`` lookup tables out -- only the multi-tenant ``ref.account_types``
is audited).  Adding rows to a seed catalogue does not change its audited
status, so no audit trigger is involved.

**Self-contained dependency policy.**  This migration imports nothing from
``app`` -- not models, not enums, not ``ref_cache``.  All values are inline
raw SQL because migrations run at fragile bootstrap moments (the ref-cache
layer is itself initialising) and must survive aggressive refactors in app
code.

**Downgrade.**  Deletes exactly the five rows this migration adds, by name,
across the three tables, so a re-upgrade reproduces them identically from the
inline seed.  Reversible: no table is created here, so there is nothing to
drop.  Safe at this revision because nothing references the new rows yet --
the FKs that point at these tables (``budget.account_postings.posting_kind_id``,
``budget.journal_entries.source_kind_id``, ``budget.ledger_accounts.kind_id``)
are all ``ondelete=RESTRICT``, so once the higher read-switch commits begin
booking opening / true-up entries and the ``equity_opening`` ledger account,
those RESTRICT constraints would correctly block this DELETE until the higher
revisions are themselves downgraded first.
"""
from alembic import op


# Revision identifiers, used by Alembic.
revision = 'd1b22f59ba5b'
down_revision = 'e2a9f1c7b4d6'
branch_labels = None
depends_on = None


# Inline seed SQL.  The ``name`` values MUST match the enum ``.value`` strings
# in ``app/enums.py`` exactly (``PostingKindEnum`` ``OPENING`` / ``TRUEUP``,
# ``PostingSourceEnum`` ``LOAN_OPENING`` / ``LOAN_TRUEUP``,
# ``LedgerAccountKindEnum.EQUITY_OPENING``) or ``ref_cache.init()`` raises at
# app start; they MUST also match the lists in ``app/ref_seeds.py``.  ``ON
# CONFLICT (name) DO NOTHING`` makes each statement idempotent against a
# partial re-run and against the entrypoint's later idempotent reseed.
_SEED_LOAN_GENESIS_POSTING_KINDS_SQL = (
    "INSERT INTO ref.posting_kinds (name) VALUES "
    "('opening'), "
    "('trueup') "
    "ON CONFLICT (name) DO NOTHING"
)

_SEED_LOAN_GENESIS_POSTING_SOURCES_SQL = (
    "INSERT INTO ref.posting_sources (name) VALUES "
    "('loan_opening'), "
    "('loan_trueup') "
    "ON CONFLICT (name) DO NOTHING"
)

_SEED_EQUITY_OPENING_LEDGER_KIND_SQL = (
    "INSERT INTO ref.ledger_account_kinds (name) VALUES "
    "('equity_opening') "
    "ON CONFLICT (name) DO NOTHING"
)

# Downgrade SQL.  Deletes exactly the rows the upgrade adds, by name, so a
# re-upgrade reproduces them identically from the inline seed above.
_DROP_LOAN_GENESIS_POSTING_KINDS_SQL = (
    "DELETE FROM ref.posting_kinds WHERE name IN ('opening', 'trueup')"
)

_DROP_LOAN_GENESIS_POSTING_SOURCES_SQL = (
    "DELETE FROM ref.posting_sources "
    "WHERE name IN ('loan_opening', 'loan_trueup')"
)

_DROP_EQUITY_OPENING_LEDGER_KIND_SQL = (
    "DELETE FROM ref.ledger_account_kinds WHERE name = 'equity_opening'"
)


def upgrade():
    """Inline-seed the loan-genesis kinds/sources and the equity-opening kind."""
    op.execute(_SEED_LOAN_GENESIS_POSTING_KINDS_SQL)
    op.execute(_SEED_LOAN_GENESIS_POSTING_SOURCES_SQL)
    op.execute(_SEED_EQUITY_OPENING_LEDGER_KIND_SQL)


def downgrade():
    """Delete the five loan read-switch reference rows this migration added."""
    op.execute(_DROP_LOAN_GENESIS_POSTING_KINDS_SQL)
    op.execute(_DROP_LOAN_GENESIS_POSTING_SOURCES_SQL)
    op.execute(_DROP_EQUITY_OPENING_LEDGER_KIND_SQL)
