"""backfill historical settled cash-transaction postings

Revision ID: 7d63529e4300
Revises: bdde62675c9b
Create Date: 2026-06-29 18:00:00.000000

Review: solo developer, 2026-06-29 (Build-Order Step 3, Commit 7; a
production-wide data backfill -- no schema change -- that creates the
per-category Income/Expense chart-of-accounts rows and posts one balanced
journal entry per historical settled non-transfer transaction, so the cash
reconciliation oracle is production-wide.  It writes to the existing
``budget.ledger_accounts`` / ``budget.journal_entries`` /
``budget.account_postings`` tables; no table, trigger, or column is added.)

Build-Order Step 3, Commit 7 (post confirmed cash transactions + cleared
envelope entries; see
``docs/audits/balance_architecture/implementation_plan_posting_ledger_cash_envelopes.md``).

The go-forward poster (Commits 4-6) only writes a journal entry when an
ordinary transaction crosses into a settled status *after* the wiring
shipped.  Every transaction settled before then carries no posting, so the
Commit-8 reconciliation oracle would be blind to historical cash on real
data.  This migration backfills those, mirroring the Step-2 settled-transfer
backfill (``db239773c2fd``).  Nothing reads postings yet; every balance still
flows through the ``balance_at`` seam over ``budget.transactions``.

Two passes, in order:

  1. **Pass A -- create the counter (category / fallback) ledger accounts.**
     One Income or Expense ledger account per ``(owner, category, class)``
     appearing in a settled, non-deleted, non-transfer transaction, plus the
     per-(owner, class) ``Uncategorized`` fallback for a settled transaction
     whose ``category_id`` is NULL.  ``ON CONFLICT ... DO NOTHING`` against the
     matching partial unique index makes this idempotent and -- crucially --
     leaves untouched any account the go-forward resolver already created
     (Commits 4-6 may have posted some settled rows before this runs).  See
     :func:`_create_counter_ledger_accounts`.

  2. **Pass B -- post one balanced entry per settled transaction.**  For every
     settled (``status.is_settled``), non-deleted, ``transfer_id IS NULL``
     transaction with a nonzero confirmed cash effect that has no journal
     entry yet, insert one :class:`budget.journal_entries` header plus two
     :class:`budget.account_postings` legs (the signed cash leg on the linked
     ledger account, its negation on the resolved counter account).  Idempotent
     via ``NOT EXISTS`` on a prior entry for the transaction.  See
     :func:`_post_settled_transactions`.

**The one effect formula -- identical to the go-forward builder and the
oracle.**  A settled transaction's confirmed cash effect is::

    effect = COALESCE(actual_amount, estimated_amount)
             - COALESCE(SUM(credit-entry amounts), 0)

signed ``+`` for income and ``-`` for an expense (by the transaction *type*,
never the account class).  This is byte-for-byte the
``posting_service._signed_cash_leg`` formula
(``effective_amount - _credit_entry_sum``; ``effective_amount`` is
``COALESCE(actual_amount, estimated_amount)`` for a settled, non-deleted row)
and the ``settled_transaction_effect`` oracle reader.  For a plain transaction
the credit sum is zero, so ``effect`` is the effective amount; for an envelope
at settle ``actual_amount`` equals the sum of ALL entries, so
``effect = sum(all) - sum(credit) = sum(debit)`` -- the debit-only checking
outflow (plan Decision D2), with no branch on "is this an envelope".  The
``effect <> 0`` filter skips an all-credit envelope (a zero leg is forbidden
by ``ck_account_postings_amount_nonzero`` and contributes nothing to the
oracle either way), matching the go-forward poster's idempotent no-op.

**Backfill == go-forward (the oracle's backstop check).**  Both producers must
agree exactly, so this migration mirrors the Commit-3 resolver
(``ledger_account_service.get_or_create_category_ledger_account``) and the
Commit-4 builder (``posting_service.sync_transaction_postings``):

  * the fallback row is created with ``is_fallback = TRUE`` (the resolver's
    idempotency lookup keys ``WHERE is_fallback``; an ``is_fallback = FALSE``
    fallback the resolver would never find again, yielding two fallback-shaped
    rows and breaking backfill==go-forward -- plan Section 4.2's H1 fix);
  * the category row's display ``name`` is the snapshotted
    ``LEFT(group_name || ': ' || item_name, 100)`` -- code-point-equal to the
    resolver's ``category.display_name[:100]`` (both truncate to the
    ``ledger_accounts.name`` VARCHAR(100) width); the fallback's name is the
    canonical ``Uncategorized {Income|Expense}`` (the resolver's
    ``_FALLBACK_LEDGER_ACCOUNT_NAMES`` values);
  * the accounting class is derived from the transaction *type* (Income vs
    Expense), identical to the builder's
    ``LedgerAccountClassEnum.INCOME if txn.is_income else ...EXPENSE``;
  * ``entry_date`` is the UTC civil date of ``paid_at``
    (``(paid_at AT TIME ZONE 'UTC')::date``, the storage-timezone date, NOT the
    display timezone), falling back to the pay period's ``start_date`` when a
    historical settle carries a NULL ``paid_at``.  ``entry_date`` is NOT NULL,
    so the fallback is load-bearing.  Mirrors
    ``posting_service._transaction_entry_date``.

**Self-contained dependency policy.**  Unlike the Step-2 migration (which
imported ``app.posting_infrastructure`` for the trigger SQL), this migration
imports nothing from ``app``: both passes are raw SQL against the catalogue,
and there is no trigger work.  The six ``ref`` ids are resolved once by unique
name (``ref.transaction_types`` / ``ref.ledger_account_classes`` /
``ref.posting_kinds`` / ``ref.posting_sources``) -- the documented migration
exception to the IDs-for-logic rule (mirroring ``db239773c2fd``), guarded by
:func:`_require_scalar` so a missing bootstrap row fails loud rather than
binding a NULL into a NOT NULL column.

**No-op on a fresh database (the ordering guard).**  Five of the six ids are
inline-seeded by lower-revision migrations and so are present when this one
runs: the Income/Expense ``ledger_account_classes`` by ``f5037400dc5e``, and the
``income`` / ``expense`` posting kinds + the ``transaction`` posting source by
the Commit-1 migration ``97bc03c2aa4c``.  The sixth -- the Income
``transaction_type`` -- lives in a ref table seeded ONLY by the application's
``seed_reference_data`` pass, NOT by any migration.  During a fresh ``flask db
upgrade base->head`` (a test-template build or a brand-new deploy) that seeding
runs AFTER the migration chain, so that one row is absent while this migration
executes, and resolving it would raise.  A fresh database also has no settled
transactions to post, so there is nothing to do:
:func:`_backfill_settled_transactions` checks
:func:`_has_settled_cash_transactions` FIRST and returns early before resolving
any ref id.  A database that HAS settled transactions has necessarily been
seeded (a transaction cannot exist without its ``transaction_type`` / ``status``
ref rows, and ``seed_reference_data`` seeds every ref table together), so the
resolution always succeeds when there is real work; an existing-database upgrade
(the only place this backfill posts anything) is unaffected.  Go-forward
emission (Commits 4-6) handles every settle that happens after a fresh deploy,
so skipping the empty backfill loses nothing.

**The balanced invariant is checked at COMMIT.**  Each entry is written as a
header plus exactly two legs summing to zero; the deferred
``ck_account_postings_balanced`` trigger validates per-entry
``SUM(amount) = 0`` and ``COUNT(*) >= 2`` at the migration's COMMIT.  A
divergence (an unbalanced pair) would abort the whole migration -- the right
fail-loud signal.

**Downgrade.**  Removes every transaction-sourced journal entry
(``source_kind = transaction`` -- both these backfilled rows and any go-forward
rows emitted after the upgrade; their legs cascade via the CASCADE FK) and
every counter ledger account (``account_id IS NULL`` -- the category, fallback,
and deleted-category orphan rows; the Step-2 *linked* rows keep their
``account_id`` and are untouched).  Entries are deleted before the accounts so
the accounts are posting-free when dropped.  Raw SQL, so the append-only ORM
guards (which catch ORM-mediated deletes only) do not interfere, and the
balanced trigger does not fire (it is INSERT/UPDATE only).  Reversible: a
re-upgrade re-creates the category/fallback accounts and re-posts every settled
transaction identically (the same caveat ``db239773c2fd`` documents -- a
downgrade does not preserve go-forward postings, but the backfill regenerates
them).  Must run *before* the Commit-2 schema downgrade (``bdde62675c9b``),
which drops the ``category_id`` / ``is_fallback`` / ``transaction_id`` columns:
once this has run, no category/fallback rows remain and every
``transaction_id`` is gone, so that column drop is clean.
"""
from collections import namedtuple
from decimal import Decimal

from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = '7d63529e4300'
down_revision = 'bdde62675c9b'
branch_labels = None
depends_on = None


# The six ``ref`` ids the backfill binds, resolved once by unique name (the
# documented migration exception to IDs-for-logic).  Bundled so both passes
# read the same resolved set and the resolution lives in one place.
_RefIds = namedtuple("_RefIds", [
    "income_type_id",    # ref.transaction_types 'Income' -- the type discriminator
    "income_class_id",   # ref.ledger_account_classes 'Income'
    "expense_class_id",  # ref.ledger_account_classes 'Expense'
    "income_kind_id",    # ref.posting_kinds 'income'  -- the income entry's legs
    "expense_kind_id",   # ref.posting_kinds 'expense' -- the expense entry's legs
    "source_kind_id",    # ref.posting_sources 'transaction' -- the entry source
])


# ── Pass A: create the counter (category / fallback) ledger accounts ──
#
# One Income/Expense category ledger account per (owner, category, class)
# appearing in a settled non-deleted non-transfer transaction.  ``account_id``
# defaults NULL (a counter account, not a real-account mirror); ``is_fallback``
# FALSE; ``name`` snapshots ``LEFT(group_name || ': ' || item_name, 100)`` --
# code-point-equal to the resolver's ``category.display_name[:100]`` against the
# ``ledger_accounts.name`` VARCHAR(100) width, so backfill==go-forward.  The
# class follows the transaction *type* (Income vs Expense), so a type-agnostic
# category used for both correctly yields two rows.  The categories join filters
# by owner (``c.user_id = pp.user_id``) per the every-user-data-query-filters-by
# -user_id rule and to match the resolver's user-scoped category load.  ``ON
# CONFLICT ... DO NOTHING`` against ``uq_ledger_accounts_category`` makes this
# idempotent and skips any account the go-forward resolver already created.
_CREATE_CATEGORY_LEDGER_ACCOUNTS_SQL = (
    "INSERT INTO budget.ledger_accounts "
    "    (user_id, class_id, category_id, is_fallback, name) "
    "SELECT DISTINCT "
    "       pp.user_id, "
    "       CASE WHEN t.transaction_type_id = :income_type_id "
    "            THEN :income_class_id ELSE :expense_class_id END, "
    "       t.category_id, "
    "       FALSE, "
    "       LEFT(c.group_name || ': ' || c.item_name, 100) "
    "  FROM budget.transactions t "
    "  JOIN ref.statuses sref ON sref.id = t.status_id "
    "  JOIN budget.pay_periods pp ON pp.id = t.pay_period_id "
    "  JOIN budget.categories c "
    "    ON c.id = t.category_id AND c.user_id = pp.user_id "
    " WHERE sref.is_settled = TRUE "
    "   AND t.transfer_id IS NULL "
    "   AND t.is_deleted = FALSE "
    "   AND t.category_id IS NOT NULL "
    "ON CONFLICT (user_id, category_id, class_id) "
    "   WHERE category_id IS NOT NULL AND account_id IS NULL "
    "   DO NOTHING"
)


# The per-(owner, class) Uncategorized fallback for a settled transaction whose
# ``category_id`` is NULL.  ``is_fallback`` TRUE (the resolver's idempotency key
# is ``WHERE is_fallback``; this MUST agree or a re-resolve makes a second
# fallback-shaped row -- plan 4.2 H1), ``category_id`` NULL, ``name`` the
# canonical ``Uncategorized {Income|Expense}``.  ``ON CONFLICT ... DO NOTHING``
# against ``uq_ledger_accounts_uncategorized`` (keyed ``WHERE is_fallback``).
_CREATE_FALLBACK_LEDGER_ACCOUNTS_SQL = (
    "INSERT INTO budget.ledger_accounts "
    "    (user_id, class_id, category_id, is_fallback, name) "
    "SELECT DISTINCT "
    "       pp.user_id, "
    "       CASE WHEN t.transaction_type_id = :income_type_id "
    "            THEN :income_class_id ELSE :expense_class_id END, "
    "       NULL::integer, "
    "       TRUE, "
    "       CASE WHEN t.transaction_type_id = :income_type_id "
    "            THEN 'Uncategorized Income' ELSE 'Uncategorized Expense' END "
    "  FROM budget.transactions t "
    "  JOIN ref.statuses sref ON sref.id = t.status_id "
    "  JOIN budget.pay_periods pp ON pp.id = t.pay_period_id "
    " WHERE sref.is_settled = TRUE "
    "   AND t.transfer_id IS NULL "
    "   AND t.is_deleted = FALSE "
    "   AND t.category_id IS NULL "
    "ON CONFLICT (user_id, class_id) WHERE is_fallback DO NOTHING"
)


# ── Pass B: enumerate every settled transaction still needing an entry ──
#
# Resolves in one query everything the per-row inserts below need: the owner
# (``pp.user_id`` -- a Transaction has no ``user_id``), the scenario/period, the
# linked cash ledger account, the resolved counter (category/fallback) ledger
# account, the signed debit-positive cash leg, the entry's civil date, and the
# description.  ``is_income`` is computed once in the CTE and reused for the
# class (counter-leg join), the sign (cash leg), and the posting kind (in
# Python).  ``effect`` is the ``COALESCE(actual, estimated) - SUM(credit)``
# formula; the outer ``effect <> 0`` filter skips all-credit envelopes.
#
# The counter-leg join resolves to exactly one account: for a categorized
# transaction the (owner, category, class) category row
# (``uq_ledger_accounts_category`` keeps it unique; a non-NULL ``category_id``
# is ``is_fallback`` FALSE by ``ck_ledger_accounts_fallback_shape``), for a
# NULL-category transaction the (owner, class) fallback
# (``uq_ledger_accounts_uncategorized``).  Pass A created both first, so the
# inner join always matches; a transaction whose counter account is somehow
# absent is excluded rather than producing a one-legged entry, and the Commit-8
# completeness oracle is the backstop.  Idempotent via ``NOT EXISTS`` on a prior
# entry for the transaction.
_SETTLED_TRANSACTION_BACKFILL_SQL = (
    "WITH settled_txns AS ( "
    "    SELECT t.id AS transaction_id, "
    "           pp.user_id, "
    "           t.scenario_id, "
    "           t.pay_period_id, "
    "           t.account_id, "
    "           t.category_id, "
    "           (t.transaction_type_id = :income_type_id) AS is_income, "
    "           COALESCE(t.actual_amount, t.estimated_amount) "
    "             - COALESCE(( "
    "                 SELECT SUM(e.amount) "
    "                   FROM budget.transaction_entries e "
    "                  WHERE e.transaction_id = t.id "
    "                    AND e.is_credit "
    "               ), 0) AS effect, "
    "           COALESCE((t.paid_at AT TIME ZONE 'UTC')::date, pp.start_date) "
    "               AS entry_date, "
    "           LEFT(t.name, 200) AS description "
    "      FROM budget.transactions t "
    "      JOIN ref.statuses sref ON sref.id = t.status_id "
    "      JOIN budget.pay_periods pp ON pp.id = t.pay_period_id "
    "     WHERE sref.is_settled = TRUE "
    "       AND t.transfer_id IS NULL "
    "       AND t.is_deleted = FALSE "
    "       AND NOT EXISTS ( "
    "           SELECT 1 FROM budget.journal_entries je "
    "            WHERE je.transaction_id = t.id "
    "       ) "
    ") "
    "SELECT s.transaction_id, "
    "       s.user_id, "
    "       s.scenario_id, "
    "       s.pay_period_id, "
    "       s.is_income, "
    "       cash_ledger.id AS cash_ledger_id, "
    "       counter_ledger.id AS counter_ledger_id, "
    "       CASE WHEN s.is_income THEN s.effect ELSE -s.effect END AS cash_leg, "
    "       s.entry_date, "
    "       s.description "
    "  FROM settled_txns s "
    "  JOIN budget.ledger_accounts cash_ledger "
    "    ON cash_ledger.account_id = s.account_id "
    "  JOIN budget.ledger_accounts counter_ledger "
    "    ON counter_ledger.user_id = s.user_id "
    "   AND counter_ledger.account_id IS NULL "
    "   AND counter_ledger.class_id = CASE WHEN s.is_income "
    "                                      THEN :income_class_id "
    "                                      ELSE :expense_class_id END "
    "   AND ( (s.category_id IS NOT NULL "
    "          AND counter_ledger.category_id = s.category_id) "
    "      OR (s.category_id IS NULL "
    "          AND counter_ledger.is_fallback = TRUE) ) "
    " WHERE s.effect <> 0 "
    " ORDER BY s.transaction_id"
)


# Insert one journal entry header, returning its generated id so the two legs
# can reference it.  ``source_kind_id`` / ``transaction_id`` are bound from the
# resolved id / enumeration row; ``transfer_id`` defaults NULL (a transaction
# entry carries ``transaction_id``, never ``transfer_id`` -- the source_kind
# disambiguates).
_INSERT_ENTRY_SQL = (
    "INSERT INTO budget.journal_entries "
    "    (user_id, scenario_id, pay_period_id, entry_date, "
    "     source_kind_id, transaction_id, description) "
    "VALUES (:user_id, :scenario_id, :pay_period_id, :entry_date, "
    "        :source_kind_id, :transaction_id, :description) "
    "RETURNING id"
)


# Insert one signed posting leg.  The caller passes the already-signed amount
# (the cash leg, then its negation for the counter leg).
_INSERT_POSTING_SQL = (
    "INSERT INTO budget.account_postings "
    "    (journal_entry_id, ledger_account_id, amount, posting_kind_id) "
    "VALUES (:journal_entry_id, :ledger_account_id, :amount, :posting_kind_id)"
)


# Downgrade SQL.  Resolve the ``transaction`` source id, delete its entries
# (legs cascade), then drop the counter ledger accounts (now posting-free).
_SELECT_TRANSACTION_SOURCE_SQL = (
    "SELECT id FROM ref.posting_sources WHERE name = 'transaction'"
)
_DELETE_TRANSACTION_ENTRIES_SQL = (
    "DELETE FROM budget.journal_entries WHERE source_kind_id = :source_kind_id"
)
# Every ``account_id IS NULL`` ledger account is a Step-3 counter row (category
# / fallback / deleted-category orphan); Step-2 linked rows carry an
# ``account_id`` and are left alone.  Equity rows (a future step) do not exist
# yet, so this is exactly the set this backfill (and the go-forward resolver)
# materialised.
_DELETE_COUNTER_LEDGER_ACCOUNTS_SQL = (
    "DELETE FROM budget.ledger_accounts WHERE account_id IS NULL"
)


# The fresh-database guard (see the module docstring's "No-op on a fresh
# database").  References only ``budget.transactions`` / ``ref.statuses`` (both
# created by earlier migrations); on a fresh DB ``budget.transactions`` is empty
# so this is FALSE regardless of whether ``ref.statuses`` is seeded yet -- it
# never raises, unlike the ref-id resolution it gates.
_HAS_SETTLED_CASH_TRANSACTIONS_SQL = (
    "SELECT EXISTS ( "
    "    SELECT 1 FROM budget.transactions t "
    "      JOIN ref.statuses sref ON sref.id = t.status_id "
    "     WHERE sref.is_settled = TRUE "
    "       AND t.transfer_id IS NULL "
    "       AND t.is_deleted = FALSE "
    ")"
)


def _require_scalar(connection, sql, description):
    """Resolve a single scalar by SQL, failing loud when the row is absent.

    The six ``ref`` ids the backfill binds are resolved by unique name (the
    documented migration exception to IDs-for-logic).  A missing row is a
    broken bootstrap invariant -- the Step-3 Commit-1 reference seed
    (``97bc03c2aa4c``, a lower revision) must already be applied -- so this
    raises with the offending lookup rather than binding a NULL that would
    surface later as an opaque NOT NULL violation.

    Args:
        connection: A SQLAlchemy bind (``op.get_bind()`` in the migration, or
            a session in a test) exposing ``execute``.
        sql: A ``SELECT id ... WHERE name = '<literal>'`` returning one scalar.
        description: Human label for the row, used in the error message.

    Returns:
        int -- the resolved scalar.

    Raises:
        RuntimeError: If the lookup returns no row.
    """
    value = connection.execute(sa.text(sql)).scalar()
    if value is None:
        raise RuntimeError(
            f"cannot backfill cash postings: {description} is missing; the "
            f"Step-3 Commit-1 reference seed (97bc03c2aa4c) must run first"
        )
    return value


def _resolve_ref_ids(connection):
    """Resolve the six ``ref`` ids the backfill binds, by unique name.

    Args:
        connection: A SQLAlchemy bind exposing ``execute``.

    Returns:
        _RefIds: the resolved id bundle.
    """
    return _RefIds(
        income_type_id=_require_scalar(
            connection,
            "SELECT id FROM ref.transaction_types WHERE name = 'Income'",
            "the Income transaction type",
        ),
        income_class_id=_require_scalar(
            connection,
            "SELECT id FROM ref.ledger_account_classes WHERE name = 'Income'",
            "the Income ledger-account class",
        ),
        expense_class_id=_require_scalar(
            connection,
            "SELECT id FROM ref.ledger_account_classes WHERE name = 'Expense'",
            "the Expense ledger-account class",
        ),
        income_kind_id=_require_scalar(
            connection,
            "SELECT id FROM ref.posting_kinds WHERE name = 'income'",
            "the income posting kind",
        ),
        expense_kind_id=_require_scalar(
            connection,
            "SELECT id FROM ref.posting_kinds WHERE name = 'expense'",
            "the expense posting kind",
        ),
        source_kind_id=_require_scalar(
            connection,
            "SELECT id FROM ref.posting_sources WHERE name = 'transaction'",
            "the transaction posting source",
        ),
    )


def _create_counter_ledger_accounts(connection, ref_ids):
    """Pass A: create the category + Uncategorized-fallback ledger accounts.

    Idempotent (``ON CONFLICT ... DO NOTHING`` against the matching partial
    unique index), so it skips any counter account the go-forward resolver
    already created and re-runs cleanly after a partial failure.

    Args:
        connection: A SQLAlchemy bind exposing ``execute``.
        ref_ids: The resolved :class:`_RefIds` bundle.
    """
    params = {
        "income_type_id": ref_ids.income_type_id,
        "income_class_id": ref_ids.income_class_id,
        "expense_class_id": ref_ids.expense_class_id,
    }
    connection.execute(sa.text(_CREATE_CATEGORY_LEDGER_ACCOUNTS_SQL), params)
    connection.execute(sa.text(_CREATE_FALLBACK_LEDGER_ACCOUNTS_SQL), params)


def _post_settled_transactions(connection, ref_ids):
    """Pass B: post one balanced journal entry per historical settled transaction.

    For every settled (``status.is_settled``), non-deleted,
    ``transfer_id IS NULL`` transaction with a nonzero confirmed cash effect
    and no journal entry yet (see :data:`_SETTLED_TRANSACTION_BACKFILL_SQL`),
    insert one :class:`budget.journal_entries` header plus two
    :class:`budget.account_postings` legs:

    * the **cash** leg, on the transaction's linked ledger account, carrying the
      signed debit-positive effect (``+`` income / ``-`` expense);
    * the **counter** leg, on the resolved category/fallback ledger account,
      carrying the negation -- so the entry sums to zero by construction (the
      deferred ``ck_account_postings_balanced`` trigger validates at COMMIT).

    Both legs carry the same posting kind, by the transaction type
    (``income``/``expense``), mirroring the go-forward builder.  Idempotent: the
    enumeration's ``NOT EXISTS`` guard skips any transaction already posted, so
    a re-run inserts nothing new.

    Args:
        connection: A SQLAlchemy bind exposing ``execute``.
        ref_ids: The resolved :class:`_RefIds` bundle.

    Returns:
        list[int]: the ``transaction_id`` of every transaction a new entry was
        posted for (for the migration's logging and test introspection).
    """
    rows = connection.execute(sa.text(_SETTLED_TRANSACTION_BACKFILL_SQL), {
        "income_type_id": ref_ids.income_type_id,
        "income_class_id": ref_ids.income_class_id,
        "expense_class_id": ref_ids.expense_class_id,
    }).fetchall()

    posted = []
    for row in rows:
        entry_id = connection.execute(sa.text(_INSERT_ENTRY_SQL), {
            "user_id": row.user_id,
            "scenario_id": row.scenario_id,
            "pay_period_id": row.pay_period_id,
            "entry_date": row.entry_date,
            "source_kind_id": ref_ids.source_kind_id,
            "transaction_id": row.transaction_id,
            "description": row.description,
        }).scalar()

        # ``cash_leg`` is the signed, debit-positive cash effect from the
        # enumeration; ``Decimal(str(...))`` keeps it exact (no float) and the
        # negation is exact, so the two legs sum to zero.
        cash_leg = Decimal(str(row.cash_leg))
        posting_kind_id = (
            ref_ids.income_kind_id if row.is_income
            else ref_ids.expense_kind_id
        )
        # Cash-account leg: the signed confirmed cash effect.
        connection.execute(sa.text(_INSERT_POSTING_SQL), {
            "journal_entry_id": entry_id,
            "ledger_account_id": row.cash_ledger_id,
            "amount": cash_leg,
            "posting_kind_id": posting_kind_id,
        })
        # Counter (category / fallback) leg: the negation.
        connection.execute(sa.text(_INSERT_POSTING_SQL), {
            "journal_entry_id": entry_id,
            "ledger_account_id": row.counter_ledger_id,
            "amount": -cash_leg,
            "posting_kind_id": posting_kind_id,
        })
        posted.append(row.transaction_id)

    return posted


def _has_settled_cash_transactions(connection):
    """Return whether any settled non-transfer transaction exists to backfill.

    The fresh-database ordering guard (see the module docstring's "No-op on a
    fresh database").  Returns False on a fresh ``flask db upgrade base->head``
    (an empty ``budget.transactions``), so :func:`_backfill_settled_transactions`
    returns early BEFORE resolving the Income ``transaction_type`` ref id that
    ``seed_reference_data`` has not yet seeded at that point (the ledger classes
    and posting kinds/sources are migration-seeded; see the module docstring).
    The query itself never raises (an empty ``budget.transactions`` yields False
    whatever the seed state of ``ref.statuses``).

    Args:
        connection: A SQLAlchemy bind exposing ``execute``.

    Returns:
        bool: True if at least one settled, non-deleted, non-transfer
        transaction exists.
    """
    return bool(
        connection.execute(
            sa.text(_HAS_SETTLED_CASH_TRANSACTIONS_SQL)
        ).scalar()
    )


def _backfill_settled_transactions(connection):
    """Backfill cash postings: create chart accounts (Pass A), then post (Pass B).

    The migration's idempotent entry point, also invoked directly by the
    Commit-7 backfill suite and the Commit-8 reconciliation oracle (the
    ``backfill == go-forward`` check).

    Returns early (an empty list) when no settled cash transaction exists -- a
    fresh database, where the Income ``transaction_type`` ref row the resolution
    needs is not seeded until after the migration chain and there is nothing to
    post anyway (see :func:`_has_settled_cash_transactions`).

    Args:
        connection: A SQLAlchemy bind (``op.get_bind()`` in the migration, or a
            test session) exposing ``execute``.

    Returns:
        list[int]: the ``transaction_id`` of every transaction posted (Pass B),
        or an empty list when there was nothing to backfill.
    """
    if not _has_settled_cash_transactions(connection):
        return []
    ref_ids = _resolve_ref_ids(connection)
    _create_counter_ledger_accounts(connection, ref_ids)
    return _post_settled_transactions(connection, ref_ids)


def upgrade():
    """Create the category/fallback chart accounts, then post settled transactions."""
    _backfill_settled_transactions(op.get_bind())


def _remove_cash_postings(connection):
    """Remove every transaction-sourced entry and counter ledger account.

    The downgrade's reversible removal, factored out so it runs with either an
    Alembic bind (``op.get_bind()``) or a test session.  Deletes the
    ``source_kind = transaction`` journal entries FIRST (their legs cascade via
    ``fk_account_postings_journal_entry_id``), then the ``account_id IS NULL``
    counter ledger accounts -- which are posting-free by then, since only
    transaction-sourced legs ever land on a category/fallback account.  The
    Step-2 ``transfer`` entries and the linked (``account_id`` set) ledger
    accounts are untouched.

    Args:
        connection: A SQLAlchemy bind (``op.get_bind()`` in the migration, or a
            test session) exposing ``execute``.
    """
    source_kind_id = _require_scalar(
        connection, _SELECT_TRANSACTION_SOURCE_SQL,
        "the transaction posting source",
    )
    connection.execute(
        sa.text(_DELETE_TRANSACTION_ENTRIES_SQL),
        {"source_kind_id": source_kind_id},
    )
    connection.execute(sa.text(_DELETE_COUNTER_LEDGER_ACCOUNTS_SQL))


def downgrade():
    """Remove every transaction-sourced entry and counter ledger account.

    Reversible (see the module docstring): a re-upgrade re-creates the
    category/fallback accounts and re-posts every settled transaction
    identically.  Entries are deleted before the accounts so the accounts are
    posting-free; the legs cascade with their entries via the CASCADE FK.  Must
    run before the Commit-2 schema downgrade (``bdde62675c9b``).
    """
    _remove_cash_postings(op.get_bind())
