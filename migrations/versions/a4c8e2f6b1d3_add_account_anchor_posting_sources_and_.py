"""add account anchor posting sources and anchor-equity ledger kind

Revision ID: a4c8e2f6b1d3
Revises: f7a1c2d3e4b5
Create Date: 2026-07-03 12:00:00.000000

Actuals reporting, Commit C2 (Build-Order Step 5; see
``docs/audits/balance_architecture/implementation_plan_actuals_reporting.md``).

Adds the reference values the NON-loan anchor-correction postings need to two
``ref`` lookup tables Steps 2-4 already created.  No table is created or
altered -- new reference values are data, never schema (the
``ref.posting_sources`` / ``ref.ledger_account_kinds`` model docstrings record
this contract):

  * **ref.posting_sources** gains ``account_opening`` and ``account_trueup``
    -- the source-event kinds for a non-loan account's anchor corrections.
    ``account_opening`` tags the once-per-account entry booking the account's
    earliest ``AccountAnchorHistory`` assertion; ``account_trueup`` tags the
    balanced correction appended for each later anchor true-up.  The leg KINDS
    are the existing ``opening`` / ``trueup`` rows the loan read switch added
    (``d1b22f59ba5b``) -- the journal SOURCE is what distinguishes an account
    correction from a loan one, exactly as ``loan_payment`` vs ``transaction``
    already disambiguate entries that share leg kinds.  Neither entry links a
    transfer nor a transaction (both source FKs are nullable).
  * **ref.ledger_account_kinds** gains ``anchor_equity`` -- the row-kind
    discriminator for the per-NON-loan-account Equity account that holds the
    counter-leg of those corrections.  Unlike the per-loan ``equity_opening``
    kind it carries ``account_id`` (not ``loan_account_id``), coexisting with
    the account's ``linked`` row under the ``(account_id, kind_id)`` partial
    unique that Commit C3 re-keys ``uq_ledger_accounts_account`` into.

**Inline seed rationale.**  Every value is seeded in this same migration (not
deferred to the entrypoint's ``seed_reference_data`` pass) so that
``ref_cache.init()`` resolves the new ``PostingSourceEnum`` /
``LedgerAccountKindEnum`` members immediately after a bare ``flask db
upgrade`` -- an enum member with no matching row is a fatal ``RuntimeError``
at app start, and a freshly-upgraded-but-not-yet-seeded database would
otherwise trip it.  ``ON CONFLICT (name) DO NOTHING`` keeps the seed
idempotent against a re-run and against the entrypoint's later idempotent
reseed (which carries the identical rows via ``app/ref_seeds.py``).  This
duplication between the migration and ``ref_seeds`` is the established
project pattern (``f5037400dc5e`` / ``97bc03c2aa4c`` / ``f8e025a8be41`` /
``d1b22f59ba5b``): migrations run below the app layer and must not import
``app`` code, so the bootstrap values live here in raw SQL and the ongoing
idempotent reseed lives in ``ref_seeds``.

**Not audited.**  Both tables are read-only seed catalogues, deliberately
excluded from ``AUDITED_TABLES`` (only the multi-tenant ``ref.account_types``
is audited).  Adding rows to a seed catalogue does not change its audited
status, so no audit trigger is involved.

**Self-contained dependency policy.**  This migration imports nothing from
``app`` -- not models, not enums, not ``ref_cache``.  All values are inline
raw SQL because migrations run at fragile bootstrap moments (the ref-cache
layer is itself initialising) and must survive aggressive refactors in app
code.

**Downgrade.**  Deletes exactly the three rows this migration adds, by name,
so a re-upgrade reproduces them identically from the inline seed.  Reversible:
no table is created here, so there is nothing to drop.  Safe at this revision
because nothing references the new rows yet -- the FKs that point at these
tables (``budget.journal_entries.source_kind_id``,
``budget.ledger_accounts.kind_id``) are both ``ondelete=RESTRICT``, so once
the higher Step-5 commits begin booking account corrections and
``anchor_equity`` ledger accounts, those RESTRICT constraints would correctly
block this DELETE until the higher revisions (the Step-5 data boundary) are
themselves downgraded first.
"""
from alembic import op


# Revision identifiers, used by Alembic.
revision = 'a4c8e2f6b1d3'
down_revision = 'f7a1c2d3e4b5'
branch_labels = None
depends_on = None


# Inline seed SQL.  The ``name`` values MUST match the enum ``.value`` strings
# in ``app/enums.py`` exactly (``PostingSourceEnum`` ``ACCOUNT_OPENING`` /
# ``ACCOUNT_TRUEUP``, ``LedgerAccountKindEnum.ANCHOR_EQUITY``) or
# ``ref_cache.init()`` raises at app start; they MUST also match the lists in
# ``app/ref_seeds.py``.  ``ON CONFLICT (name) DO NOTHING`` makes each
# statement idempotent against a partial re-run and against the entrypoint's
# later idempotent reseed.
_SEED_ACCOUNT_ANCHOR_POSTING_SOURCES_SQL = (
    "INSERT INTO ref.posting_sources (name) VALUES "
    "('account_opening'), "
    "('account_trueup') "
    "ON CONFLICT (name) DO NOTHING"
)

_SEED_ANCHOR_EQUITY_LEDGER_KIND_SQL = (
    "INSERT INTO ref.ledger_account_kinds (name) VALUES "
    "('anchor_equity') "
    "ON CONFLICT (name) DO NOTHING"
)

# Downgrade SQL.  Deletes exactly the rows the upgrade adds, by name, so a
# re-upgrade reproduces them identically from the inline seed above.
_DROP_ACCOUNT_ANCHOR_POSTING_SOURCES_SQL = (
    "DELETE FROM ref.posting_sources "
    "WHERE name IN ('account_opening', 'account_trueup')"
)

_DROP_ANCHOR_EQUITY_LEDGER_KIND_SQL = (
    "DELETE FROM ref.ledger_account_kinds WHERE name = 'anchor_equity'"
)


def upgrade():
    """Inline-seed the account anchor sources and the anchor-equity kind."""
    op.execute(_SEED_ACCOUNT_ANCHOR_POSTING_SOURCES_SQL)
    op.execute(_SEED_ANCHOR_EQUITY_LEDGER_KIND_SQL)


def downgrade():
    """Delete the three Step-5 reference rows this migration added."""
    op.execute(_DROP_ACCOUNT_ANCHOR_POSTING_SOURCES_SQL)
    op.execute(_DROP_ANCHOR_EQUITY_LEDGER_KIND_SQL)
