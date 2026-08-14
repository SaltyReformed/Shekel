"""add the true-up counter-leg reference rows (Unrealized class, two kinds)

Revision ID: e6b4a2d8c713
Revises: c4e1a8b70f36
Create Date: 2026-08-14 10:15:00.000000

Balance arc, plan step **X-f3d** (ruling **R-FO**; see
``docs/audits/balance_architecture/README.md`` section 4).

A non-loan account's balance assertion books a balanced two-leg correction:
one leg moves the account's own linked ledger, and the COUNTER leg has always
landed in equity.  For a Roth IRA, a 401(k) or a Property that is wrong -- the
difference is investment return, and booking it to equity is why ``$10,653.91``
of return earned over 4.5 months is invisible on the income statement
(measured on a production clone 2026-08-13).  R-FO dispatches the counter leg
over ``classify_account``: ``INTEREST`` books per-account **Interest Income**,
``INVESTMENT`` / ``APPRECIATING`` book per-account **Change in Value**,
``PLAIN`` stays on ``anchor_equity`` until the cutover (X-f3c) makes its
residual a recorded transaction, and ``AMORTIZING`` is untouched.

This migration adds ONLY the reference values that dispatch needs.  No table is
created or altered -- new reference values are data, never schema (the
``ref.ledger_account_classes`` / ``ref.ledger_account_kinds`` model docstrings
record this contract):

  * **ref.ledger_account_classes** gains ``Unrealized`` with
    ``is_debit_normal = FALSE``.  A gain is a credit, so it is credit-normal
    like Income -- and it is a class of its OWN because
    ``net_income = income - expense``, so a ``$40,000`` house revaluation
    booked as Income would read as ``$40,000`` earned.  The income statement
    reports it BELOW the net-income line; the balance sheet folds it into
    Equity as one derived accumulated line, exactly as Income and Expense are
    folded into Retained Earnings, so the trial balance closes with it.
  * **ref.ledger_account_kinds** gains ``interest_income`` and
    ``unrealized_change`` -- the per-account chart rows those counter legs land
    in.  Both share the ``anchor_equity`` column shape (``account_id`` set,
    everything else NULL/false) and its ``uq_ledger_accounts_account_kind``
    partial unique, so NO new index is needed.

**Why the UPGRADE moves no posted leg.**  It cannot be done in raw SQL without
duplicating app logic: which counter kind an account's true-up takes is
``classify_account``, and which correction is the OPENING is
``cash_anchor_facts``' ``(observed_on, created_at, id)`` ordering.  It does not
need to be.  The account anchor reconcile is reconcile-to-target and reads
EVERY posted leg of a correction key
(``_posting_reconcile.posted_correction_legs``), so the next per-account sync
emits one balanced delta per key -- reversing the old ``anchor_equity`` leg and
posting the new one -- with no backfill.  ``scripts/init_database.py`` runs
``backfill_all_account_anchor_postings_after_migration()`` on EVERY deploy of
an existing database, immediately after this chain reaches head, so the move
happens in the same deploy.  A developer running a bare ``flask db upgrade``
gets the ref rows now and the re-pointing at the next sync of each account.

**Why the DOWNGRADE moves them anyway.**  The reverse direction needs no app
logic at all -- "every leg on a row of these two kinds belongs on that
account's ``anchor_equity`` row" -- and it MUST happen here: both FKs into
these ref tables are ``ON DELETE RESTRICT``, so deleting the rows while any
chart entry still carries them would abort.  The move is leg-for-leg within
each journal entry (only ``ledger_account_id`` changes), so every entry stays
balanced and the deferred ``ck_account_postings_balanced`` trigger passes; an
entry that ends up with two ``anchor_equity`` legs (the original correction's
and the reversal the forward re-point wrote) nets to exactly what the old code
posted, which is what makes the old image's next reconcile a no-op rather than
a re-derivation.

**Inline seed rationale.**  Every value is seeded in this same migration (not
deferred to the entrypoint's ``seed_reference_data`` pass) so that
``ref_cache.init()`` resolves the new ``LedgerAccountClassEnum`` /
``LedgerAccountKindEnum`` members immediately after a bare ``flask db
upgrade`` -- an enum member with no matching row is a fatal ``RuntimeError``
at app start, and a freshly-upgraded-but-not-yet-seeded database would
otherwise trip it.  ``ON CONFLICT (name) DO NOTHING`` keeps the seed
idempotent against a re-run and against the entrypoint's later idempotent
reseed (which carries the identical rows via ``app/ref_seeds.py``).  This
duplication between the migration and ``ref_seeds`` is the established project
pattern (``f5037400dc5e`` / ``d1b22f59ba5b`` / ``b3f7c2a9d514``): migrations
run below the app layer and must not import ``app`` code, so the bootstrap
values live here in raw SQL and the ongoing idempotent reseed lives in
``ref_seeds``.

**Not audited.**  Both ``ref`` tables are read-only seed catalogues,
deliberately excluded from ``AUDITED_TABLES`` (only the multi-tenant
``ref.account_types`` is audited).  ``budget.ledger_accounts`` and
``budget.account_postings`` ARE audited, so the downgrade's moves and deletes
are captured in ``system.audit_log`` by their triggers.

**Self-contained dependency policy.**  This migration imports nothing from
``app`` -- not models, not enums, not ``ref_cache``.  Every value is inline
raw SQL because migrations run at fragile bootstrap moments (the ref-cache
layer is itself initialising) and must survive aggressive refactors in app
code.
"""
from alembic import op


# Revision identifiers, used by Alembic.
revision = 'e6b4a2d8c713'
down_revision = 'c4e1a8b70f36'
branch_labels = None
depends_on = None


# Inline seed SQL.  The ``name`` values MUST match the enum ``.value`` strings
# in ``app/enums.py`` exactly (``LedgerAccountClassEnum.UNREALIZED``,
# ``LedgerAccountKindEnum.INTEREST_INCOME`` / ``UNREALIZED_CHANGE``) or
# ``ref_cache.init()`` raises at app start; they MUST also match the lists in
# ``app/ref_seeds.py``.  ``is_debit_normal`` has no server default -- it is an
# intrinsic property of the class, so it is stated explicitly here exactly as
# the seed states it.
_SEED_UNREALIZED_LEDGER_CLASS_SQL = (
    "INSERT INTO ref.ledger_account_classes (name, is_debit_normal) VALUES "
    "('Unrealized', FALSE) "
    "ON CONFLICT (name) DO NOTHING"
)

_SEED_TRUEUP_COUNTER_LEDGER_KINDS_SQL = (
    "INSERT INTO ref.ledger_account_kinds (name) VALUES "
    "('interest_income'), "
    "('unrealized_change') "
    "ON CONFLICT (name) DO NOTHING"
)

# Downgrade SQL, in the only order the RESTRICT FKs permit: mint any missing
# ``anchor_equity`` row, move the posted legs onto it, drop the emptied chart
# rows, then delete the reference values.

# An account whose OPENING correction booked nothing (a $0 opening) has no
# ``anchor_equity`` row, so a true-up that re-pointed to one of the new kinds
# can be the account's only counter row.  Mint the twin the old code would
# have made, with the same class, kind, shape and ``"<account> -- Opening"``
# label its resolver snapshots (clipped to the ``name`` column's 100 chars).
# ``DISTINCT`` collapses an account that somehow carries both new kinds.
_RESTORE_MISSING_ANCHOR_EQUITY_SQL = """
INSERT INTO budget.ledger_accounts
    (user_id, class_id, kind_id, account_id, is_fallback, name)
SELECT DISTINCT
    src.user_id,
    (SELECT id FROM ref.ledger_account_classes WHERE name = 'Equity'),
    (SELECT id FROM ref.ledger_account_kinds WHERE name = 'anchor_equity'),
    src.account_id,
    FALSE,
    LEFT(a.name || ' -- Opening', 100)
FROM budget.ledger_accounts src
JOIN budget.accounts a ON a.id = src.account_id
WHERE src.kind_id IN (
        SELECT id FROM ref.ledger_account_kinds
        WHERE name IN ('interest_income', 'unrealized_change')
      )
  AND NOT EXISTS (
        SELECT 1 FROM budget.ledger_accounts eq
        WHERE eq.account_id = src.account_id
          AND eq.kind_id = (
              SELECT id FROM ref.ledger_account_kinds
              WHERE name = 'anchor_equity'
          )
      )
"""

# Leg-for-leg: only ``ledger_account_id`` changes, so every journal entry keeps
# summing to zero and the deferred balanced-entry trigger passes.
_MOVE_COUNTER_LEGS_TO_ANCHOR_EQUITY_SQL = """
UPDATE budget.account_postings p
SET ledger_account_id = eq.id
FROM budget.ledger_accounts src
JOIN budget.ledger_accounts eq
  ON eq.account_id = src.account_id
 AND eq.kind_id = (
       SELECT id FROM ref.ledger_account_kinds WHERE name = 'anchor_equity'
     )
WHERE p.ledger_account_id = src.id
  AND src.kind_id IN (
        SELECT id FROM ref.ledger_account_kinds
        WHERE name IN ('interest_income', 'unrealized_change')
      )
"""

_DROP_TRUEUP_COUNTER_CHART_ROWS_SQL = """
DELETE FROM budget.ledger_accounts
WHERE kind_id IN (
    SELECT id FROM ref.ledger_account_kinds
    WHERE name IN ('interest_income', 'unrealized_change')
)
"""

_DROP_TRUEUP_COUNTER_LEDGER_KINDS_SQL = (
    "DELETE FROM ref.ledger_account_kinds "
    "WHERE name IN ('interest_income', 'unrealized_change')"
)

_DROP_UNREALIZED_LEDGER_CLASS_SQL = (
    "DELETE FROM ref.ledger_account_classes WHERE name = 'Unrealized'"
)


def upgrade():
    """Inline-seed the Unrealized class and the two true-up counter kinds."""
    op.execute(_SEED_UNREALIZED_LEDGER_CLASS_SQL)
    op.execute(_SEED_TRUEUP_COUNTER_LEDGER_KINDS_SQL)


def downgrade():
    """Return every counter leg to anchor equity, then delete the three rows."""
    op.execute(_RESTORE_MISSING_ANCHOR_EQUITY_SQL)
    op.execute(_MOVE_COUNTER_LEGS_TO_ANCHOR_EQUITY_SQL)
    op.execute(_DROP_TRUEUP_COUNTER_CHART_ROWS_SQL)
    op.execute(_DROP_TRUEUP_COUNTER_LEDGER_KINDS_SQL)
    op.execute(_DROP_UNREALIZED_LEDGER_CLASS_SQL)
