"""Tests for the 7d63529e4300 historical settled cash-transaction backfill (Commit 7).

The Commit-7 migration creates the per-category Income/Expense chart-of-accounts
rows (Pass A) and backfills one balanced journal entry per historical settled,
non-deleted, non-transfer transaction with a nonzero confirmed cash effect
(Pass B).  The migration is already at HEAD when these tests run (the template
builder upgraded base->head against an EMPTY ``budget.transactions`` table, so
the in-chain backfill was a no-op).  Each test therefore engineers settled
transactions directly and invokes the migration's idempotent
:func:`_backfill_settled_transactions` -- the same pattern the
settled-transfer backfill suite uses.

Why direct ORM construction reproduces the "historical" state: the go-forward
poster (Commits 4-6) only writes a journal entry when a transaction crosses
into a settled status THROUGH THE STATUS SEAM (a route / service path).  Tests
build settled rows via the ``add_txn`` constructor helper (status passed as a
constructor kwarg, never a post-hoc ``status_id`` assignment, so the W9907 seam
checker is satisfied and no go-forward post fires).  A settled row built this
way carries no posting -- exactly the pre-ledger state the backfill targets.

The asserted invariants (plan Section 6 / Commit 7):

  * a settled plain expense / income backfills to exactly one balanced entry:
    the signed cash leg on the linked ledger account and its negation on the
    resolved category ledger account, summing to zero, source kind
    ``transaction``, both legs the income/expense posting kind;
  * the confirmed cash effect is ``COALESCE(actual, estimated) - SUM(credit
    entries)`` -- an envelope with credit entries posts the debit-only outflow,
    and a divergent ``actual_amount`` overrides the estimate;
  * the counter account is the per-category row (snapshotted ``"Group: Item"``
    name, class by transaction type) or the per-(owner, class) Uncategorized
    fallback (``is_fallback`` True), reused across rows; a category used for
    both income and expense yields two rows;
  * the entry date is the shadow-less transaction's ``paid_at`` (UTC civil
    date), falling back to the pay-period start when ``paid_at`` is NULL;
  * Projected / Cancelled / Credit / soft-deleted / zero-effect / transfer
    -shadow rows are excluded;
  * the backfill is idempotent.

The executable migration up/down round-trip was verified manually against the
prod-clone dev DB during development (the downgrade removed every
transaction-sourced entry and counter ledger account, leaving the Step-2
transfer entries and linked accounts intact, and a re-upgrade regenerated them
identically reconciling to the settled transactions); the downgrade is checked
at source level here, matching the settled-transfer backfill suite's rationale.
"""
# pylint: disable=redefined-outer-name
# Rationale: ``redefined-outer-name`` is the canonical pytest fixture
# pattern; bodies bind fixtures by name.
from __future__ import annotations

import pathlib
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    LedgerAccountClassEnum,
    PostingKindEnum,
    PostingSourceEnum,
    StatusEnum,
)
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.transaction_entry import TransactionEntry
from tests._test_helpers import (
    add_entry,
    add_txn,
    create_account_of_type,
    create_settled_transfer,
    ledger_accounts_for_account,
    load_migration_module,
)


# ---------------------------------------------------------------------------
# Migration module under test (migrations/versions has no __init__)
# ---------------------------------------------------------------------------


_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)
_MIGRATION_FILENAME = "7d63529e4300_backfill_historical_cash_postings.py"
_MIGRATION = load_migration_module(_MIGRATION_FILENAME)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_backfill():
    """Execute the migration's idempotent backfill on the test session."""
    posted = _MIGRATION._backfill_settled_transactions(_db.session)
    _db.session.commit()
    return posted


def _entry_for_transaction(transaction_id):
    """Return the single journal entry for *transaction_id*, or None."""
    return (
        _db.session.query(JournalEntry)
        .filter_by(transaction_id=transaction_id)
        .one_or_none()
    )


def _legs_by_ledger(entry_id):
    """Return ``{ledger_account_id: amount}`` for an entry's legs."""
    return {
        leg.ledger_account_id: leg.amount
        for leg in _db.session.query(Posting).filter_by(
            journal_entry_id=entry_id,
        ).all()
    }


def _kinds_for_entry(entry_id):
    """Return the set of posting-kind ids across an entry's legs."""
    return {
        leg.posting_kind_id
        for leg in _db.session.query(Posting).filter_by(
            journal_entry_id=entry_id,
        ).all()
    }


def _cash_ledger_id(account):
    """Return the linked (cash) ledger account id for *account*."""
    return ledger_accounts_for_account(_db.session, account.id)[0].id


def _counter_ledger(user_id, ledger_class, category_id=None):
    """Return the category / fallback ledger account, mirroring the resolver.

    Keys on ``(user_id, class_id)`` plus either ``category_id`` (a category
    row) or ``is_fallback`` (the Uncategorized fallback), exactly as
    ``ledger_account_service._find_existing_category_ledger_account`` does.
    """
    class_id = ref_cache.ledger_account_class_id(ledger_class)
    query = _db.session.query(LedgerAccount).filter_by(
        user_id=user_id, class_id=class_id, account_id=None,
    )
    if category_id is None:
        return query.filter_by(is_fallback=True).one_or_none()
    return query.filter_by(category_id=category_id).one_or_none()


@pytest.fixture()
def savings(app, db, seed_user):  # pylint: disable=unused-argument
    """A second (Savings) account so a transfer-shadow exclusion test has a target.

    Created in the ``db`` fixture's app context (no nested context) so the
    returned :class:`Account` stays bound to the live session the test runs in.
    """
    acct = create_account_of_type(
        seed_user, _db.session, "Savings", "Backfill Savings",
    )
    _db.session.commit()
    return acct


# ---------------------------------------------------------------------------
# Plain expense / income: one balanced entry, correct signs and accounts
# ---------------------------------------------------------------------------


class TestBackfillPlainExpense:
    """A settled plain expense backfills to one balanced two-leg entry."""

    def test_expense_signs_balance_source_and_kind(self, app, db, seed_user):
        """A Paid $50 Groceries expense backfills to -50 / +50, summing to zero.

        Arithmetic (plan Section 1): effect = COALESCE(actual, estimated) -
        credit_sum = 50.00 - 0 = 50.00.  An expense signs the cash leg
        negative (money leaving Checking) and the category leg positive (the
        expense lands in Family: Groceries -- Expense): -50.00 + 50.00 = 0.00.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "50.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            _db.session.commit()
            checking_ledger = _cash_ledger_id(seed_user["account"])

            assert _run_backfill() == [txn.id]

            entry = _entry_for_transaction(txn.id)
            assert entry is not None
            assert entry.source_kind_id == ref_cache.posting_source_id(
                PostingSourceEnum.TRANSACTION,
            )
            assert entry.description == "Groceries"

            groceries = seed_user["categories"]["Groceries"]
            counter = _counter_ledger(
                seed_user["user"].id, LedgerAccountClassEnum.EXPENSE,
                category_id=groceries.id,
            )
            assert counter is not None
            assert counter.is_fallback is False
            assert counter.name == "Family: Groceries"

            legs = _legs_by_ledger(entry.id)
            assert legs[checking_ledger] == Decimal("-50.00")
            assert legs[counter.id] == Decimal("50.00")
            assert sum(legs.values()) == Decimal("0.00")
            assert _kinds_for_entry(entry.id) == {
                ref_cache.posting_kind_id(PostingKindEnum.EXPENSE),
            }

    def test_expense_uses_actual_not_estimated(self, app, db, seed_user):
        """A divergent settled ``actual_amount`` overrides the estimate.

        The estimate is $100 but the settled actual is $80, so the effect is
        the effective amount COALESCE(actual, estimated) = 80.00 -- the value
        the balance calculator and the oracle use.  The backfill posts
        -80.00 / +80.00, not -100 / +100.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "100.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
                actual_amount="80.00",
            )
            _db.session.commit()
            checking_ledger = _cash_ledger_id(seed_user["account"])
            _run_backfill()

            groceries = seed_user["categories"]["Groceries"]
            counter = _counter_ledger(
                seed_user["user"].id, LedgerAccountClassEnum.EXPENSE,
                category_id=groceries.id,
            )
            legs = _legs_by_ledger(_entry_for_transaction(txn.id).id)
            assert legs[checking_ledger] == Decimal("-80.00")
            assert legs[counter.id] == Decimal("80.00")


class TestBackfillPlainIncome:
    """A settled income transaction backfills with the income sign + class."""

    def test_income_signs_and_income_class_counter(self, app, db, seed_user):
        """A Received $2000 Salary income backfills to +2000 / -2000.

        Arithmetic (plan Section 1): income has no entries, so effect =
        estimated = 2000.00.  Income signs the cash leg positive (money
        entering Checking) and the category leg negative (income earned in
        Income: Salary -- Income class): +2000.00 - 2000.00 = 0.00.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Paycheck", "2000.00",
                status_enum=StatusEnum.RECEIVED, is_income=True,
                category_key="Salary",
            )
            _db.session.commit()
            checking_ledger = _cash_ledger_id(seed_user["account"])
            assert _run_backfill() == [txn.id]

            salary = seed_user["categories"]["Salary"]
            counter = _counter_ledger(
                seed_user["user"].id, LedgerAccountClassEnum.INCOME,
                category_id=salary.id,
            )
            assert counter is not None
            assert counter.name == "Income: Salary"

            entry = _entry_for_transaction(txn.id)
            legs = _legs_by_ledger(entry.id)
            assert legs[checking_ledger] == Decimal("2000.00")
            assert legs[counter.id] == Decimal("-2000.00")
            assert sum(legs.values()) == Decimal("0.00")
            assert _kinds_for_entry(entry.id) == {
                ref_cache.posting_kind_id(PostingKindEnum.INCOME),
            }


# ---------------------------------------------------------------------------
# Envelope: the debit-only effect (effective - sum(credit))
# ---------------------------------------------------------------------------


class TestBackfillEnvelopeDebitOnlyEffect:
    """A settled envelope posts the debit-only outflow (credits excluded)."""

    def test_envelope_debit_only_effect(self, app, db, seed_user):
        """A $150-actual envelope with a $40 credit entry posts -110 / +110.

        For the backfill an "envelope" is simply a settled transaction carrying
        entries -- the SQL reads ``effective - SUM(credit)`` regardless of the
        is_envelope flag.  Entries: 60 debit + 50 debit + 40 credit, with the
        settled ``actual_amount`` = sum(all) = 150.00 (what
        ``compute_actual_from_entries`` sets at settle).  Arithmetic (plan
        Section 1 / Decision D2): effect = 150.00 - 40.00 (the credit entry)
        = 110.00 = the two debit purchases.  The $40 credit posts nothing here;
        its CC Payback posts separately when it settles.  -110.00 + 110.00 = 0.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries Env", "200.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
                actual_amount="150.00",
            )
            add_entry(_db.session, seed_user, txn, Decimal("60.00"),
                      period.start_date)
            add_entry(_db.session, seed_user, txn, Decimal("50.00"),
                      period.start_date)
            _db.session.add(TransactionEntry(
                transaction_id=txn.id, user_id=seed_user["user"].id,
                amount=Decimal("40.00"), description="cc purchase",
                entry_date=period.start_date, is_credit=True,
            ))
            _db.session.flush()
            _db.session.commit()
            checking_ledger = _cash_ledger_id(seed_user["account"])

            assert _run_backfill() == [txn.id]
            groceries = seed_user["categories"]["Groceries"]
            counter = _counter_ledger(
                seed_user["user"].id, LedgerAccountClassEnum.EXPENSE,
                category_id=groceries.id,
            )
            legs = _legs_by_ledger(_entry_for_transaction(txn.id).id)
            assert legs[checking_ledger] == Decimal("-110.00")
            assert legs[counter.id] == Decimal("110.00")
            assert sum(legs.values()) == Decimal("0.00")

    def test_all_credit_envelope_posts_nothing(self, app, db, seed_user):
        """An all-credit envelope (effect 0) backfills no entry.

        Entries: a single $75 credit purchase, settled ``actual_amount`` =
        75.00.  Arithmetic: effect = 75.00 - 75.00 = 0.00; a zero leg is
        forbidden and contributes nothing to the oracle, so the backfill omits
        the entry entirely (matching the go-forward poster's no-op).
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "All Credit Env", "75.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
                actual_amount="75.00",
            )
            _db.session.add(TransactionEntry(
                transaction_id=txn.id, user_id=seed_user["user"].id,
                amount=Decimal("75.00"), description="cc purchase",
                entry_date=period.start_date, is_credit=True,
            ))
            _db.session.flush()
            _db.session.commit()

            assert _run_backfill() == []
            assert _entry_for_transaction(txn.id) is None


# ---------------------------------------------------------------------------
# Uncategorized fallback
# ---------------------------------------------------------------------------


class TestBackfillUncategorizedFallback:
    """A NULL-category settled transaction books into the fallback bucket."""

    def test_null_category_posts_to_fallback(self, app, db, seed_user):
        """A Paid $30 expense with no category posts to Uncategorized Expense.

        Arithmetic: effect = 30.00; the counter leg lands in the per-(owner,
        class) fallback (``is_fallback`` True, name "Uncategorized Expense"),
        not a category row.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Misc", "30.00",
                status_enum=StatusEnum.DONE, category_key=None,
            )
            _db.session.commit()
            checking_ledger = _cash_ledger_id(seed_user["account"])
            assert _run_backfill() == [txn.id]

            fallback = _counter_ledger(
                seed_user["user"].id, LedgerAccountClassEnum.EXPENSE,
            )
            assert fallback is not None
            assert fallback.is_fallback is True
            assert fallback.category_id is None
            assert fallback.name == "Uncategorized Expense"

            legs = _legs_by_ledger(_entry_for_transaction(txn.id).id)
            assert legs[checking_ledger] == Decimal("-30.00")
            assert legs[fallback.id] == Decimal("30.00")


# ---------------------------------------------------------------------------
# entry_date
# ---------------------------------------------------------------------------


class TestBackfillEntryDate:
    """``entry_date`` is the transaction's paid_at (UTC), else the period start."""

    def test_entry_date_from_paid_at_utc(self, app, db, seed_user):
        """A settled paid_at maps to its UTC civil date, not the display tz date.

        A timezone-boundary-crossing instant proves the migration uses
        ``(paid_at AT TIME ZONE 'UTC')::date`` and NOT the America/New_York
        display date: paid_at 2026-05-10 02:00 UTC is 2026-05-09 22:00 Eastern
        (EDT, UTC-4), so a display-tz conversion would yield 2026-05-09.  The
        UTC civil date -- the one stored -- is 2026-05-10.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Dated", "40.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            # add_txn leaves paid_at NULL; set it here.  The W9907 seam checker
            # bans only post-hoc ``status_id`` assignment, not ``paid_at``.
            txn.paid_at = datetime(2026, 5, 10, 2, 0, tzinfo=timezone.utc)
            _db.session.commit()
            _run_backfill()
            assert _entry_for_transaction(txn.id).entry_date == date(2026, 5, 10)

    def test_entry_date_falls_back_to_period_start_when_paid_at_null(
        self, app, db, seed_user,
    ):
        """A settled transaction with NULL paid_at uses the pay-period start.

        ``entry_date`` is NOT NULL, so the backfill falls back to the period's
        ``start_date`` (the historical-settle state add_txn produces).
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Undated", "40.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            _db.session.commit()
            _run_backfill()
            assert _entry_for_transaction(txn.id).entry_date == period.start_date


# ---------------------------------------------------------------------------
# Exclusions
# ---------------------------------------------------------------------------


class TestBackfillExclusions:
    """Projected / Cancelled / Credit / soft-deleted / shadow rows are excluded."""

    @pytest.mark.parametrize("status_enum", [
        StatusEnum.PROJECTED,
        StatusEnum.CANCELLED,
        StatusEnum.CREDIT,
    ])
    def test_non_settled_status_not_backfilled(
        self, app, db, seed_user, status_enum,
    ):
        """A Projected / Cancelled / Credit transaction produces no entry.

        Only ``is_settled`` rows post.  Projected has not happened; Cancelled
        and Credit are is_settled FALSE (Credit's checking effect comes via its
        CC Payback, not the source row).
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "NotSettled", "50.00",
                status_enum=status_enum, category_key="Groceries",
            )
            _db.session.commit()
            assert _run_backfill() == []
            assert _entry_for_transaction(txn.id) is None

    def test_soft_deleted_settled_not_backfilled(self, app, db, seed_user):
        """A settled-but-soft-deleted transaction produces no entry.

        Its effective amount is zero (the balance calculator drops a deleted
        row), and the backfill's ``is_deleted = FALSE`` filter excludes it.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Deleted", "50.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
                is_deleted=True,
            )
            _db.session.commit()
            assert _run_backfill() == []
            assert _entry_for_transaction(txn.id) is None

    def test_transfer_shadow_not_backfilled_as_transaction(
        self, app, db, seed_user, savings,
    ):
        """A settled transfer's shadows are excluded (Step 2 owns them).

        The shadows carry ``transfer_id``, so the ``transfer_id IS NULL`` filter
        excludes them; the transaction backfill posts nothing and writes no
        transaction-sourced entry for either shadow (the transfer-sourced entry
        the transfer service already wrote is untouched).
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            shadow_ids = [s.id for s in transfer.shadow_transactions]
            assert len(shadow_ids) == 2

            assert _run_backfill() == []
            txn_sourced = (
                _db.session.query(JournalEntry)
                .filter(JournalEntry.transaction_id.in_(shadow_ids))
                .count()
            )
            assert txn_sourced == 0


# ---------------------------------------------------------------------------
# Chart-of-accounts creation and reuse
# ---------------------------------------------------------------------------


class TestBackfillAccountCreationAndReuse:
    """Pass A creates the right counter accounts and reuses them across rows."""

    def test_same_category_reuses_one_account(self, app, db, seed_user):
        """Two settled expenses in one category share one category ledger account.

        Both $20 and $35 Groceries expenses book their counter legs into the
        SAME Family: Groceries -- Expense account (Pass A's ON CONFLICT dedups
        on the (user, category, class) key), so exactly one such account exists
        and carries both +20.00 and +35.00 counter legs.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn_a = add_txn(
                _db.session, seed_user, period, "Groceries A", "20.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            txn_b = add_txn(
                _db.session, seed_user, period, "Groceries B", "35.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            _db.session.commit()
            posted = _run_backfill()
            assert set(posted) == {txn_a.id, txn_b.id}

            groceries = seed_user["categories"]["Groceries"]
            class_id = ref_cache.ledger_account_class_id(
                LedgerAccountClassEnum.EXPENSE,
            )
            matching = (
                _db.session.query(LedgerAccount)
                .filter_by(
                    user_id=seed_user["user"].id, class_id=class_id,
                    category_id=groceries.id, account_id=None,
                )
                .all()
            )
            assert len(matching) == 1
            counter_id = matching[0].id
            leg_a = _legs_by_ledger(_entry_for_transaction(txn_a.id).id)
            leg_b = _legs_by_ledger(_entry_for_transaction(txn_b.id).id)
            assert leg_a[counter_id] == Decimal("20.00")
            assert leg_b[counter_id] == Decimal("35.00")

    def test_mixed_category_yields_two_class_accounts(self, app, db, seed_user):
        """A category used for both income and expense yields two ledger accounts.

        A $500 Received income AND a $40 Paid expense, both in the "Salary"
        category, produce two distinct chart rows -- one Income class (counter
        leg -500.00) and one Expense class (counter leg +40.00) -- because the
        natural key includes the class (a Category is type-agnostic).
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            salary = seed_user["categories"]["Salary"]
            income_txn = add_txn(
                _db.session, seed_user, period, "Salary In", "500.00",
                status_enum=StatusEnum.RECEIVED, is_income=True,
                category_key="Salary",
            )
            expense_txn = add_txn(
                _db.session, seed_user, period, "Salary Clawback", "40.00",
                status_enum=StatusEnum.DONE, category_key="Salary",
            )
            _db.session.commit()
            _run_backfill()

            income_counter = _counter_ledger(
                seed_user["user"].id, LedgerAccountClassEnum.INCOME,
                category_id=salary.id,
            )
            expense_counter = _counter_ledger(
                seed_user["user"].id, LedgerAccountClassEnum.EXPENSE,
                category_id=salary.id,
            )
            assert income_counter is not None
            assert expense_counter is not None
            assert income_counter.id != expense_counter.id
            assert _legs_by_ledger(
                _entry_for_transaction(income_txn.id).id
            )[income_counter.id] == Decimal("-500.00")
            assert _legs_by_ledger(
                _entry_for_transaction(expense_txn.id).id
            )[expense_counter.id] == Decimal("40.00")


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestBackfillIdempotency:
    """Re-running the backfill does not double-post."""

    def test_backfill_is_idempotent(self, app, db, seed_user):
        """Two runs leave exactly one entry and two legs for the transaction.

        The enumeration's ``NOT EXISTS`` guard on a prior entry for the
        transaction makes the second run a no-op, and Pass A's ON CONFLICT
        skips the already-created category account.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "50.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            _db.session.commit()

            first = _run_backfill()
            second = _run_backfill()
            assert first == [txn.id]
            assert second == []

            entries = (
                _db.session.query(JournalEntry)
                .filter_by(transaction_id=txn.id)
                .count()
            )
            assert entries == 1
            legs = (
                _db.session.query(Posting)
                .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
                .filter(JournalEntry.transaction_id == txn.id)
                .count()
            )
            assert legs == 2


# ---------------------------------------------------------------------------
# Migration revision pair + downgrade source check
# ---------------------------------------------------------------------------


class TestMigrationRevisionPair:
    """The migration chains off the Commit-2 schema head."""

    def test_revision_pair(self):
        """revision / down_revision pin the migration into the chain."""
        assert _MIGRATION.revision == "7d63529e4300"
        assert _MIGRATION.down_revision == "bdde62675c9b"


class TestDowngradeReversible:
    """downgrade() removes Step-3 entries + counter accounts, keeps Step-2.

    A behavioral check (``_remove_cash_postings`` is DELETE-based, so unlike the
    Step-2 backfill's DROP-TABLE downgrade it runs cleanly on the shared test
    session) plus a source-level guard against a future edit silently re-routing
    the downgrade past one of the two artefacts it must remove.  The executable
    up/down round-trip was also verified manually against the prod-clone dev DB
    (see the module docstring).
    """

    def test_downgrade_removes_step3_artifacts_keeps_step2(
        self, app, db, seed_user, savings,
    ):
        """The downgrade removal deletes the cash entry + counter account only.

        After backfilling a Paid cash expense AND settling a transfer (which the
        transfer service auto-posts as a Step-2 ``transfer`` entry),
        ``_remove_cash_postings`` deletes the transaction-sourced entry and the
        category ledger account it created, while leaving the transfer-sourced
        entry and the linked ledger accounts intact -- the exact reverse of what
        the upgrade added.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "50.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings, period,
                amount=Decimal("100.00"),
            )
            _db.session.commit()
            _run_backfill()

            groceries = seed_user["categories"]["Groceries"]
            # Upgrade added a transaction entry + its category account; the
            # transfer service auto-posted one Step-2 transfer entry.
            assert _entry_for_transaction(txn.id) is not None
            assert _counter_ledger(
                seed_user["user"].id, LedgerAccountClassEnum.EXPENSE,
                category_id=groceries.id,
            ) is not None
            transfer_entries = (
                _db.session.query(JournalEntry)
                .filter_by(transfer_id=transfer.id).count()
            )
            assert transfer_entries == 1
            linked_before = len(
                ledger_accounts_for_account(_db.session, seed_user["account"].id)
            )

            _MIGRATION._remove_cash_postings(_db.session)
            _db.session.commit()

            # Step-3 artifacts removed.
            assert _entry_for_transaction(txn.id) is None
            assert _counter_ledger(
                seed_user["user"].id, LedgerAccountClassEnum.EXPENSE,
                category_id=groceries.id,
            ) is None
            # Step-2 transfer entry + linked ledger accounts survive.
            assert (
                _db.session.query(JournalEntry)
                .filter_by(transfer_id=transfer.id).count()
            ) == 1
            assert len(
                ledger_accounts_for_account(_db.session, seed_user["account"].id)
            ) == linked_before

    def test_downgrade_source_removes_entries_and_counter_accounts(self):
        """The downgrade source deletes transaction entries + counter accounts."""
        source = (_MIGRATIONS_DIR / _MIGRATION_FILENAME).read_text()
        assert "DELETE FROM budget.journal_entries WHERE source_kind_id" in source
        assert (
            "DELETE FROM budget.ledger_accounts WHERE account_id IS NULL"
            in source
        )
