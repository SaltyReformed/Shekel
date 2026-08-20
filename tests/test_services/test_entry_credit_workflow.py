"""
Shekel Budget App -- Entry-Level Credit Card Workflow Tests

Tests the entry-level credit card workflow that manages aggregated CC
Payback transactions from individual credit entries on entry-capable
transactions.  Covers the sync_entry_payback 2x2 state matrix, payback
field parity with the legacy workflow, entry link integrity, Decimal
precision, full lifecycle, the legacy credit guard, integration through
entry_service hooks, and session state correctness.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.category import Category
from app.models.ref import Status, TransactionType
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.exceptions import NotFoundError, ValidationError
from app.services import credit_workflow, entry_service, transaction_service
from app.services.row_valuation import settled_figure
from app.services.entry_credit_workflow import sync_entry_payback
from app.services import cash_ledger


def worth(payback):
    """Return what *payback* is WORTH, asked of the amount model.

    **Plan step X-au-i moved the question, not the answer.**  These assertions
    read ``payback.estimated_amount`` until that step, because the figure was a
    column two writers kept re-stating; a payback now declares the
    ``credit_source`` relation and stores nothing, so the same question is put
    to the resolver that derives it from the card spend the payback repays.

    Going through the resolver rather than re-summing the entries here is the
    point: a helper that recomputed the sum itself would agree with a broken
    derivation, and it is the derivation these cases exist to grade.
    """
    return cash_ledger.resolve_transaction_amount(
        payback,
        cash_ledger.amount_basis(payback.account.user_id, payback.scenario_id),
    )


class TestSyncEntryPayback:
    """Tests for sync_entry_payback -- the 2x2 state matrix."""

    def _create_credit_entry(self, txn, user, amount="100.00", desc="Purchase"):
        """Create a credit entry directly, bypassing entry_service hooks.

        Returns the new TransactionEntry (flushed, id available).
        """
        entry = TransactionEntry(
            transaction_id=txn.id, account_id=txn.account_id,
            user_id=user.id,
            amount=Decimal(amount),
            description=desc,
            purchased_on=date(2026, 1, 5),
            is_credit=True,
        )
        db.session.add(entry)
        db.session.flush()
        return entry

    def _create_debit_entry(self, txn, user, amount="50.00", desc="Purchase"):
        """Create a debit entry directly, bypassing entry_service hooks.

        Returns the new TransactionEntry (flushed, id available).
        """
        entry = TransactionEntry(
            transaction_id=txn.id, account_id=txn.account_id,
            user_id=user.id,
            amount=Decimal(amount),
            description=desc,
            purchased_on=date(2026, 1, 5),
            is_credit=False,
        )
        db.session.add(entry)
        db.session.flush()
        return entry

    # ---- Plan tests 4.1 through 4.8, 4.11, 4.12 ----

    def test_sync_creates_payback_first_credit_entry(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """4.1: First credit entry on a fresh transaction creates a payback.

        Verifies the CREATE cell: total_credit > 0 AND no payback exists.
        The payback should appear in the next period with the correct amount.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            self._create_credit_entry(txn, user, "100.00")

            payback = sync_entry_payback(txn.id, user.id)

            assert payback is not None
            assert worth(payback) == Decimal("100.00")
            assert payback.pay_period_id == seed_periods[1].id
            assert payback.credit_payback_for_id == txn.id
            assert payback.name == f"CC Payback: {txn.name}"

    def test_sync_updates_payback_on_second_credit(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """4.2: Second credit entry updates the existing payback amount.

        Verifies the UPDATE cell: total_credit > 0 AND payback already exists.
        The payback amount should be the sum of both entries.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            self._create_credit_entry(txn, user, "100.00", "First")
            payback = sync_entry_payback(txn.id, user.id)
            payback_id = payback.id

            self._create_credit_entry(txn, user, "50.00", "Second")
            payback = sync_entry_payback(txn.id, user.id)

            # Same payback, updated amount: 100 + 50 = 150.
            assert payback.id == payback_id
            assert worth(payback) == Decimal("150.00")

    def test_sync_deletes_payback_when_last_credit_removed(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """4.3: Deleting the last credit entry removes the payback.

        Verifies the DELETE cell: total_credit == 0 AND payback exists.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = self._create_credit_entry(txn, user, "100.00")
            payback = sync_entry_payback(txn.id, user.id)
            payback_id = payback.id

            db.session.delete(entry)
            db.session.flush()

            result = sync_entry_payback(txn.id, user.id)

            assert result is None
            assert db.session.get(Transaction, payback_id) is None

    def test_sync_updates_on_credit_entry_edit(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """4.4: Editing a credit entry's amount updates the payback.

        Verifies the UPDATE cell after an amount change.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = self._create_credit_entry(txn, user, "100.00")
            sync_entry_payback(txn.id, user.id)

            entry.amount = Decimal("75.00")
            db.session.flush()

            payback = sync_entry_payback(txn.id, user.id)

            assert worth(payback) == Decimal("75.00")

    def test_sync_handles_credit_toggle_debit_to_credit(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """4.5: Toggling a debit entry to credit creates a payback.

        Verifies the transition from no-op to CREATE when an entry's
        is_credit flag is set to True.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = self._create_debit_entry(txn, user, "100.00")
            result = sync_entry_payback(txn.id, user.id)
            assert result is None

            entry.is_credit = True
            db.session.flush()

            payback = sync_entry_payback(txn.id, user.id)

            assert payback is not None
            assert worth(payback) == Decimal("100.00")

    def test_sync_handles_credit_toggle_credit_to_debit(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """4.6: Toggling the only credit entry to debit deletes the payback.

        Verifies the transition from UPDATE/CREATE to DELETE when all
        credit entries are toggled off.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = self._create_credit_entry(txn, user, "100.00")
            payback = sync_entry_payback(txn.id, user.id)
            payback_id = payback.id

            entry.is_credit = False
            db.session.flush()

            result = sync_entry_payback(txn.id, user.id)

            assert result is None
            assert db.session.get(Transaction, payback_id) is None

    def test_sync_idempotent(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """4.7: Calling sync when payback amount already matches is idempotent.

        The payback object and amount should be unchanged.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            self._create_credit_entry(txn, user, "100.00")
            payback = sync_entry_payback(txn.id, user.id)
            payback_id = payback.id

            payback_again = sync_entry_payback(txn.id, user.id)

            assert payback_again.id == payback_id
            assert worth(payback_again) == Decimal("100.00")

    def test_sync_no_next_period_raises(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """4.8: Creating a payback when no next period exists raises ValidationError.

        Transactions in the last generated period cannot produce paybacks
        because there is no subsequent period for the payback to land in.
        """
        with app.app_context():
            user = seed_user["user"]
            template = seed_entry_template["template"]
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = (
                db.session.query(TransactionType).filter_by(name="Expense").one()
            )

            # Transaction in the last period -- no period follows it.
            txn = Transaction(
                template_id=template.id,
                pay_period_id=seed_periods[-1].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Last Period Expense",
                category_id=seed_entry_template["category"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
            )
            db.session.add(txn)
            db.session.flush()

            self._create_credit_entry(txn, user, "100.00")

            with pytest.raises(ValidationError, match="No next pay period"):
                sync_entry_payback(txn.id, user.id)

    def test_payback_links_all_credit_entries(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """4.11: All credit entries share the same credit_payback_id.

        Three credit entries created before the first sync should all be
        linked to the single payback.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            e1 = self._create_credit_entry(txn, user, "50.00", "Store A")
            e2 = self._create_credit_entry(txn, user, "30.00", "Store B")
            e3 = self._create_credit_entry(txn, user, "20.00", "Store C")

            payback = sync_entry_payback(txn.id, user.id)

            assert e1.credit_payback_id == payback.id
            assert e2.credit_payback_id == payback.id
            assert e3.credit_payback_id == payback.id
            # 50 + 30 + 20 = 100
            assert worth(payback) == Decimal("100.00")

    def test_mixed_entries_only_credit_sum_in_payback(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """4.12: Mixed debit/credit entries -- payback reflects only credit sum.

        Debit entries do not contribute to the payback amount.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            self._create_debit_entry(txn, user, "200.00", "Debit 1")
            self._create_debit_entry(txn, user, "200.00", "Debit 2")
            self._create_credit_entry(txn, user, "100.00", "Credit 1")
            self._create_credit_entry(txn, user, "50.00", "Credit 2")

            payback = sync_entry_payback(txn.id, user.id)

            # Only credit entries count: 100 + 50 = 150.
            assert worth(payback) == Decimal("150.00")

    def test_sync_ignores_soft_deleted_payback_and_creates_fresh(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """sync excludes a soft-deleted payback and takes the CREATE branch (#58).

        The existing-payback lookup must filter ``is_deleted == False`` to
        match the partial unique index.  When the prior payback was
        soft-deleted but credit entries still exist, sync must take the
        CREATE branch and build a fresh live payback rather than
        resurrecting and mutating the soft-deleted one.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            self._create_credit_entry(txn, user, "100.00")
            first = sync_entry_payback(txn.id, user.id)
            first_id = first.id

            # Soft-delete the payback but keep the credit entry live.
            first.is_deleted = True
            db.session.flush()

            second = sync_entry_payback(txn.id, user.id)

            # A NEW live payback was created (CREATE branch), not the dead
            # one resurrected: without the filter the lookup returned the
            # soft-deleted row and the UPDATE branch reused its id.
            assert second is not None
            assert second.id != first_id
            assert second.is_deleted is False
            assert worth(second) == Decimal("100.00")

            # The soft-deleted payback is left untouched for the audit trail.
            old = db.session.get(Transaction, first_id)
            assert old.is_deleted is True

    # ---- Defense-in-depth: ownership and not-found guards ----

    def test_sync_nonexistent_transaction_raises(self, app, db, seed_user):
        """sync_entry_payback raises NotFoundError for a nonexistent transaction."""
        with app.app_context():
            with pytest.raises(NotFoundError):
                sync_entry_payback(999999, seed_user["user"].id)

    def test_sync_wrong_owner_raises(
        self, app, db, seed_user, seed_second_user,
        seed_periods, seed_entry_template,
    ):
        """sync_entry_payback raises NotFoundError when owner_id doesn't match.

        Defense-in-depth: even if the caller already checked ownership,
        sync verifies via pay_period.user_id to prevent payback creation
        under the wrong user.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            other_user = seed_second_user["user"]

            self._create_credit_entry(txn, seed_user["user"], "100.00")

            with pytest.raises(NotFoundError):
                sync_entry_payback(txn.id, other_user.id)


class TestPaybackCorrectness:
    """Verify payback field parity, period placement, name, and precision."""

    def test_payback_fields_match_legacy(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Entry-level payback has every field that legacy mark_as_credit sets.

        Compares each of the 10 fields set by credit_workflow.mark_as_credit
        against the payback created by sync_entry_payback.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = TransactionEntry(
                transaction_id=txn.id, account_id=txn.account_id,
                user_id=user.id,
                amount=Decimal("100.00"),
                description="Field parity",
                purchased_on=date(2026, 1, 5),
                is_credit=True,
            )
            db.session.add(entry)
            db.session.flush()

            payback = sync_entry_payback(txn.id, user.id)

            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)

            # All 10 fields from mark_as_credit (lines 104-115).
            assert payback.account_id == txn.account_id
            assert payback.template_id is None
            assert payback.pay_period_id == seed_periods[1].id
            assert payback.scenario_id == txn.scenario_id
            assert payback.status_id == projected_id
            assert payback.name == f"CC Payback: {txn.name}"
            assert payback.transaction_type_id == expense_type_id
            assert worth(payback) == Decimal("100.00")
            assert payback.credit_payback_for_id == txn.id

            # Category must be the CC Payback category for the owner.
            cat = db.session.get(Category, payback.category_id)
            assert cat.group_name == "Credit Card"
            assert cat.item_name == "Payback"
            assert cat.user_id == user.id

    def test_payback_period_is_next_not_same(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Payback lands in the NEXT period, not the parent's period.

        The parent transaction is in seed_periods[0]; the payback must
        be in seed_periods[1].
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]
            assert txn.pay_period_id == seed_periods[0].id

            entry = TransactionEntry(
                transaction_id=txn.id, account_id=txn.account_id,
                user_id=user.id,
                amount=Decimal("50.00"),
                description="Period check",
                purchased_on=date(2026, 1, 5),
                is_credit=True,
            )
            db.session.add(entry)
            db.session.flush()

            payback = sync_entry_payback(txn.id, user.id)

            assert payback.pay_period_id == seed_periods[1].id
            assert payback.pay_period_id != txn.pay_period_id

    def test_payback_name_format(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Payback name follows 'CC Payback: {parent_name}' pattern.

        The seed_entry_template transaction name is 'Weekly Groceries'.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = TransactionEntry(
                transaction_id=txn.id, account_id=txn.account_id,
                user_id=user.id,
                amount=Decimal("50.00"),
                description="Name check",
                purchased_on=date(2026, 1, 5),
                is_credit=True,
            )
            db.session.add(entry)
            db.session.flush()

            payback = sync_entry_payback(txn.id, user.id)

            assert payback.name == "CC Payback: Weekly Groceries"

    def test_independent_paybacks_per_transaction(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Two transactions from the same template have independent paybacks.

        Each transaction's credit entries produce a separate payback with
        the correct amount and credit_payback_for_id.
        """
        with app.app_context():
            txn1 = seed_entry_template["transaction"]
            user = seed_user["user"]
            template = seed_entry_template["template"]
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = (
                db.session.query(TransactionType).filter_by(name="Expense").one()
            )

            txn2 = Transaction(
                template_id=template.id,
                pay_period_id=seed_periods[2].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Weekly Groceries",
                category_id=seed_entry_template["category"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
            )
            db.session.add(txn2)
            db.session.flush()

            e1 = TransactionEntry(
                transaction_id=txn1.id, account_id=txn1.account_id, user_id=user.id,
                amount=Decimal("100.00"), description="Txn1",
                purchased_on=date(2026, 1, 5), is_credit=True,
            )
            e2 = TransactionEntry(
                transaction_id=txn2.id, account_id=txn2.account_id, user_id=user.id,
                amount=Decimal("200.00"), description="Txn2",
                purchased_on=date(2026, 1, 30), is_credit=True,
            )
            db.session.add_all([e1, e2])
            db.session.flush()

            payback1 = sync_entry_payback(txn1.id, user.id)
            payback2 = sync_entry_payback(txn2.id, user.id)

            assert payback1.id != payback2.id
            assert worth(payback1) == Decimal("100.00")
            assert worth(payback2) == Decimal("200.00")
            assert payback1.credit_payback_for_id == txn1.id
            assert payback2.credit_payback_for_id == txn2.id

    def test_three_entries_sum_to_exact_hundred(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """$33.33 + $33.33 + $33.34 sums to exactly $100.00.

        Verifies Decimal arithmetic avoids float rounding errors.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            for amt in ["33.33", "33.33", "33.34"]:
                e = TransactionEntry(
                    transaction_id=txn.id, account_id=txn.account_id,
                    user_id=user.id,
                    amount=Decimal(amt),
                    description="Split",
                    purchased_on=date(2026, 1, 5),
                    is_credit=True,
                )
                db.session.add(e)
            db.session.flush()

            payback = sync_entry_payback(txn.id, user.id)

            assert worth(payback) == Decimal("100.00")

    def test_single_penny_entry(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Single $0.01 credit entry produces a $0.01 payback.

        The smallest possible amount must produce a valid payback.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            e = TransactionEntry(
                transaction_id=txn.id, account_id=txn.account_id,
                user_id=user.id,
                amount=Decimal("0.01"),
                description="Penny",
                purchased_on=date(2026, 1, 5),
                is_credit=True,
            )
            db.session.add(e)
            db.session.flush()

            payback = sync_entry_payback(txn.id, user.id)

            assert worth(payback) == Decimal("0.01")


class TestEntryLinkIntegrity:
    """Verify credit_payback_id links are maintained correctly."""

    def test_create_links_all_credit_entries(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """After CREATE: all credit entries have credit_payback_id set.

        Three entries created before sync should all be linked after sync.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entries = []
            for i, amt in enumerate(["30.00", "40.00", "30.00"]):
                e = TransactionEntry(
                    transaction_id=txn.id, account_id=txn.account_id,
                    user_id=user.id,
                    amount=Decimal(amt),
                    description=f"Store {i}",
                    purchased_on=date(2026, 1, 5),
                    is_credit=True,
                )
                db.session.add(e)
                entries.append(e)
            db.session.flush()

            payback = sync_entry_payback(txn.id, user.id)

            for e in entries:
                assert e.credit_payback_id == payback.id

    def test_update_links_new_credit_entry(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """After UPDATE: a newly added credit entry also gets linked.

        The second credit entry, added after the payback already exists,
        must be linked on the next sync.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            e1 = TransactionEntry(
                transaction_id=txn.id, account_id=txn.account_id,
                user_id=user.id,
                amount=Decimal("100.00"),
                description="First",
                purchased_on=date(2026, 1, 5),
                is_credit=True,
            )
            db.session.add(e1)
            db.session.flush()

            payback = sync_entry_payback(txn.id, user.id)
            assert e1.credit_payback_id == payback.id

            e2 = TransactionEntry(
                transaction_id=txn.id, account_id=txn.account_id,
                user_id=user.id,
                amount=Decimal("50.00"),
                description="Second",
                purchased_on=date(2026, 1, 6),
                is_credit=True,
            )
            db.session.add(e2)
            db.session.flush()

            sync_entry_payback(txn.id, user.id)

            assert e2.credit_payback_id == payback.id

    def test_delete_path_clears_entry_links(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """DELETE path clears credit_payback_id before removing payback.

        When an entry is toggled from credit to debit (making total == 0),
        the DELETE path must clear the stale credit_payback_id and delete
        the payback.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = TransactionEntry(
                transaction_id=txn.id, account_id=txn.account_id,
                user_id=user.id,
                amount=Decimal("100.00"),
                description="Toggle",
                purchased_on=date(2026, 1, 5),
                is_credit=True,
            )
            db.session.add(entry)
            db.session.flush()

            payback = sync_entry_payback(txn.id, user.id)
            assert entry.credit_payback_id == payback.id
            payback_id = payback.id

            # Toggle to debit -- total_credit becomes 0, triggers DELETE.
            entry.is_credit = False
            db.session.flush()

            result = sync_entry_payback(txn.id, user.id)

            assert result is None
            assert db.session.get(Transaction, payback_id) is None
            assert entry.credit_payback_id is None

    def test_debit_entries_never_get_payback_link(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Debit entries NEVER get credit_payback_id set.

        Even when a payback exists for credit entries on the same
        transaction, debit entries must remain unlinked.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            debit = TransactionEntry(
                transaction_id=txn.id, account_id=txn.account_id,
                user_id=user.id,
                amount=Decimal("200.00"),
                description="Debit",
                purchased_on=date(2026, 1, 5),
                is_credit=False,
            )
            credit = TransactionEntry(
                transaction_id=txn.id, account_id=txn.account_id,
                user_id=user.id,
                amount=Decimal("100.00"),
                description="Credit",
                purchased_on=date(2026, 1, 5),
                is_credit=True,
            )
            db.session.add_all([debit, credit])
            db.session.flush()

            sync_entry_payback(txn.id, user.id)

            assert debit.credit_payback_id is None
            assert credit.credit_payback_id is not None

    def test_toggle_to_debit_clears_stale_link(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Toggling one entry to debit clears its stale credit_payback_id.

        When two credit entries exist and one is toggled to debit, the
        UPDATE path must clear the toggled entry's stale link while
        keeping the remaining credit entry linked.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            e1 = TransactionEntry(
                transaction_id=txn.id, account_id=txn.account_id, user_id=user.id,
                amount=Decimal("100.00"), description="A",
                purchased_on=date(2026, 1, 5), is_credit=True,
            )
            e2 = TransactionEntry(
                transaction_id=txn.id, account_id=txn.account_id, user_id=user.id,
                amount=Decimal("50.00"), description="B",
                purchased_on=date(2026, 1, 5), is_credit=True,
            )
            db.session.add_all([e1, e2])
            db.session.flush()

            payback = sync_entry_payback(txn.id, user.id)
            assert e1.credit_payback_id == payback.id
            assert e2.credit_payback_id == payback.id

            e1.is_credit = False
            db.session.flush()

            payback = sync_entry_payback(txn.id, user.id)

            # Payback updated to e2's amount only.
            assert worth(payback) == Decimal("50.00")
            # e1's stale link was cleared.
            assert e1.credit_payback_id is None
            # e2 still linked.
            assert e2.credit_payback_id == payback.id


class TestPaybackLifecycle:
    """Full lifecycle tests through multiple entry mutations."""

    def test_full_lifecycle(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Create 3 credit entries -> update one -> delete one -> toggle one.

        Verifies the payback amount at each step of a realistic sequence
        of entry mutations.

        Step amounts:
          Start: $100 + $50 + $75 = $225
          After update e1 $100 -> $120: $120 + $50 + $75 = $245
          After delete e2: $120 + $75 = $195
          After toggle e3 to debit: $120
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            e1 = TransactionEntry(
                transaction_id=txn.id, account_id=txn.account_id, user_id=user.id,
                amount=Decimal("100.00"), description="A",
                purchased_on=date(2026, 1, 5), is_credit=True,
            )
            e2 = TransactionEntry(
                transaction_id=txn.id, account_id=txn.account_id, user_id=user.id,
                amount=Decimal("50.00"), description="B",
                purchased_on=date(2026, 1, 6), is_credit=True,
            )
            e3 = TransactionEntry(
                transaction_id=txn.id, account_id=txn.account_id, user_id=user.id,
                amount=Decimal("75.00"), description="C",
                purchased_on=date(2026, 1, 7), is_credit=True,
            )
            db.session.add_all([e1, e2, e3])
            db.session.flush()

            payback = sync_entry_payback(txn.id, user.id)
            assert worth(payback) == Decimal("225.00")

            # Update e1 from $100 to $120.
            e1.amount = Decimal("120.00")
            db.session.flush()
            payback = sync_entry_payback(txn.id, user.id)
            assert worth(payback) == Decimal("245.00")

            # Delete e2 ($50).
            db.session.delete(e2)
            db.session.flush()
            payback = sync_entry_payback(txn.id, user.id)
            assert worth(payback) == Decimal("195.00")

            # Toggle e3 to debit.
            e3.is_credit = False
            db.session.flush()
            payback = sync_entry_payback(txn.id, user.id)
            assert worth(payback) == Decimal("120.00")

    def test_credit_then_toggle_to_debit_deletes_payback(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Create a credit entry then toggle it to debit -- payback is deleted.

        After the toggle, no credit entries remain, so the payback must
        be removed entirely.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = TransactionEntry(
                transaction_id=txn.id, account_id=txn.account_id, user_id=user.id,
                amount=Decimal("100.00"), description="Toggle",
                purchased_on=date(2026, 1, 5), is_credit=True,
            )
            db.session.add(entry)
            db.session.flush()

            payback = sync_entry_payback(txn.id, user.id)
            payback_id = payback.id

            entry.is_credit = False
            db.session.flush()

            result = sync_entry_payback(txn.id, user.id)

            assert result is None
            assert db.session.get(Transaction, payback_id) is None

    def test_all_debit_entries_no_payback_ever_created(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """All debit entries from the start -- no payback is ever created.

        Verifies the no-op cell: total_credit == 0 AND no payback exists.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            for i in range(3):
                e = TransactionEntry(
                    transaction_id=txn.id, account_id=txn.account_id, user_id=user.id,
                    amount=Decimal("50.00"), description=f"Debit {i}",
                    purchased_on=date(2026, 1, 5), is_credit=False,
                )
                db.session.add(e)
            db.session.flush()

            result = sync_entry_payback(txn.id, user.id)

            assert result is None
            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .first()
            )
            assert payback is None


class TestLegacyCreditGuard:
    """Guard tests for legacy mark_as_credit on tracked transactions."""

    def test_legacy_credit_blocked_on_tracked(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """4.9: Legacy mark_as_credit raises ValidationError on tracked transactions.

        Entry-capable transactions must use entry-level credit, not the
        legacy Credit status.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            with pytest.raises(ValidationError, match="individual purchase tracking"):
                credit_workflow.mark_as_credit(txn.id, user.id)

    def test_legacy_credit_still_works_non_tracked(
        self, app, db, seed_user, seed_periods,
    ):
        """4.10: Legacy mark_as_credit still works on non-tracked transactions.

        Regression: the guard must not affect transactions whose template
        does not have is_envelope enabled.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = (
                db.session.query(TransactionType).filter_by(name="Expense").one()
            )

            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Non-Tracked Expense",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("100.00"),
            )
            db.session.add(txn)
            db.session.flush()

            payback = credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)
            db.session.flush()

            assert payback is not None
            assert payback.name == "CC Payback: Non-Tracked Expense"
            assert worth(payback) == Decimal("100.00")

    def test_legacy_unmark_credit_still_works(
        self, app, db, seed_user, seed_periods,
    ):
        """Legacy unmark_credit still works (regression).

        Verify that reverting a legacy credit transaction restores
        Projected status and deletes the payback.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = (
                db.session.query(TransactionType).filter_by(name="Expense").one()
            )

            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Legacy Expense",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("100.00"),
            )
            db.session.add(txn)
            db.session.flush()

            payback = credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)
            db.session.flush()
            payback_id = payback.id

            credit_workflow.unmark_credit(txn.id, seed_user["user"].id)
            db.session.flush()

            assert txn.status.name == "Projected"
            assert db.session.get(Transaction, payback_id) is None


class TestEntryServiceHooks:
    """Integration tests through entry_service hooks.

    These tests verify that entry_service.create_entry, update_entry,
    and delete_entry automatically trigger sync_entry_payback, producing
    the correct payback state without explicit sync calls.
    """

    def test_create_credit_entry_creates_payback(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """create_entry with is_credit=True automatically creates a payback."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry_service.create_entry(
                transaction_id=txn.id,
                user_id=user.id,
                details=entry_service.EntryDetails(
                    amount=Decimal("100.00"),
                    description="Credit purchase",
                    purchased_on=date(2026, 1, 5),
                    is_credit=True,
                ),
            )

            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .first()
            )
            assert payback is not None
            assert worth(payback) == Decimal("100.00")

    def test_create_debit_entry_no_payback(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """create_entry with is_credit=False creates no payback."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry_service.create_entry(
                transaction_id=txn.id,
                user_id=user.id,
                details=entry_service.EntryDetails(
                    amount=Decimal("100.00"),
                    description="Debit purchase",
                    purchased_on=date(2026, 1, 5),
                    is_credit=False,
                ),
            )

            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .first()
            )
            assert payback is None

    def test_update_entry_toggle_credit_creates_payback(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """update_entry toggling is_credit to True creates a payback."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=user.id,
                details=entry_service.EntryDetails(
                    amount=Decimal("100.00"),
                    description="Toggle test",
                    purchased_on=date(2026, 1, 5),
                    is_credit=False,
                ),
            )

            entry_service.update_entry(entry.id, user.id, is_credit=True)

            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .first()
            )
            assert payback is not None
            assert worth(payback) == Decimal("100.00")

    def test_update_entry_toggle_credit_deletes_payback(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """update_entry toggling is_credit to False deletes the payback."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=user.id,
                details=entry_service.EntryDetails(
                    amount=Decimal("100.00"),
                    description="Toggle test",
                    purchased_on=date(2026, 1, 5),
                    is_credit=True,
                ),
            )

            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .first()
            )
            assert payback is not None

            entry_service.update_entry(entry.id, user.id, is_credit=False)

            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .first()
            )
            assert payback is None

    def test_delete_credit_entry_updates_payback(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """delete_entry on a credit entry updates the payback amount.

        After deleting one of two credit entries, the payback amount
        should reflect only the remaining entry.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            e1 = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=user.id,
                details=entry_service.EntryDetails(
                    amount=Decimal("100.00"),
                    description="First",
                    purchased_on=date(2026, 1, 5),
                    is_credit=True,
                ),
            )
            entry_service.create_entry(
                transaction_id=txn.id,
                user_id=user.id,
                details=entry_service.EntryDetails(
                    amount=Decimal("50.00"),
                    description="Second",
                    purchased_on=date(2026, 1, 6),
                    is_credit=True,
                ),
            )

            # Payback should be $150 (100 + 50).
            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .first()
            )
            assert worth(payback) == Decimal("150.00")

            entry_service.delete_entry(e1.id, user.id)

            db.session.expire(payback)
            assert worth(payback) == Decimal("50.00")

    def test_delete_last_credit_entry_deletes_payback(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """delete_entry on the last credit entry deletes the payback."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=user.id,
                details=entry_service.EntryDetails(
                    amount=Decimal("100.00"),
                    description="Only",
                    purchased_on=date(2026, 1, 5),
                    is_credit=True,
                ),
            )

            entry_service.delete_entry(entry.id, user.id)

            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .first()
            )
            assert payback is None

    def test_update_entry_amount_updates_payback(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """update_entry changing amount on credit entry updates payback.

        Verifies the hook fires for amount changes, not just is_credit
        toggles.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=user.id,
                details=entry_service.EntryDetails(
                    amount=Decimal("100.00"),
                    description="Amount change",
                    purchased_on=date(2026, 1, 5),
                    is_credit=True,
                ),
            )

            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .first()
            )
            assert worth(payback) == Decimal("100.00")

            entry_service.update_entry(entry.id, user.id, amount=Decimal("75.00"))

            db.session.expire(payback)
            assert worth(payback) == Decimal("75.00")

    def test_companion_credit_entry_creates_owner_payback(
        self, app, db, seed_user, seed_companion,
        seed_periods, seed_entry_template,
    ):
        """Credit entry by companion creates payback under owner's data.

        The companion's user_id is resolved to the owner's user_id, so
        the CC Payback category is created for the owner, not the companion.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            companion = seed_companion["user"]

            entry_service.create_entry(
                transaction_id=txn.id,
                user_id=companion.id,
                details=entry_service.EntryDetails(
                    amount=Decimal("100.00"),
                    description="Companion purchase",
                    purchased_on=date(2026, 1, 5),
                    is_credit=True,
                ),
            )

            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .first()
            )
            assert payback is not None
            assert worth(payback) == Decimal("100.00")

            # Category belongs to the owner, not the companion.
            cat = db.session.get(Category, payback.category_id)
            assert cat.user_id == seed_user["user"].id


class TestSessionState:
    """Verify SQLAlchemy session state between flush and sync."""

    def test_create_entry_flush_visible_to_sync(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """After create_entry flushes, sync sees the new entry.

        Verifies that the SQLAlchemy session state is correct between
        the entry flush in create_entry and the entries read in
        sync_entry_payback.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=user.id,
                details=entry_service.EntryDetails(
                    amount=Decimal("100.00"),
                    description="Session test",
                    purchased_on=date(2026, 1, 5),
                    is_credit=True,
                ),
            )

            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .first()
            )
            assert payback is not None
            assert worth(payback) == Decimal("100.00")

            # Entry should be linked by sync.
            db.session.refresh(entry)
            assert entry.credit_payback_id == payback.id

    def test_delete_entry_flush_invisible_to_sync(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """After delete_entry flushes, sync does not see the deleted entry.

        Verifies that the deleted entry is excluded from the credit sum
        and payback amount.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            e1 = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=user.id,
                details=entry_service.EntryDetails(
                    amount=Decimal("100.00"),
                    description="First",
                    purchased_on=date(2026, 1, 5),
                    is_credit=True,
                ),
            )
            entry_service.create_entry(
                transaction_id=txn.id,
                user_id=user.id,
                details=entry_service.EntryDetails(
                    amount=Decimal("50.00"),
                    description="Second",
                    purchased_on=date(2026, 1, 6),
                    is_credit=True,
                ),
            )

            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .first()
            )
            assert worth(payback) == Decimal("150.00")

            # Delete first entry ($100) -- only second ($50) remains.
            entry_service.delete_entry(e1.id, user.id)

            db.session.expire(payback)
            assert worth(payback) == Decimal("50.00")


class TestASettledPaybackCannotBeReDerived:
    """A settled payback records what moved; a later purchase cannot rewrite it.

    **The control for a regression plan step X-au-c3 introduced and this guard
    closes.**  ``sync_entry_payback`` rewrites the payback's
    ``estimated_amount`` from the source's credit entries.  While a settled
    row's value was ``COALESCE(actual_amount, estimated_amount)`` that write
    moved the money, because an uncorrected settled payback carried a NULL
    actual and fell through to its plan.  A settled row is now worth what it
    RECORDED, so the same write became inert -- and the liability the user had
    just added left the projection with nothing booking it.

    Measured on the shape below before the guard existed: a payback settled at
    ``$100.00``, a later ``$50.00`` card purchase, ``estimated_amount`` moved to
    ``$150.00``, and every balance went on reading ``$100.00``.
    """

    def test_a_later_card_purchase_is_refused_not_silently_dropped(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The $50 that used to disappear is a designed 400 instead."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry_service.create_entry(
                transaction_id=txn.id, user_id=user.id,
                details=entry_service.EntryDetails(
                    amount=Decimal("100.00"), description="Card buy",
                    purchased_on=date(2026, 1, 5), is_credit=True,
                ),
            )
            payback = sync_entry_payback(txn.id, user.id)
            db.session.flush()

            transaction_service.settle_transaction(payback)
            # COMMITTED, so the rollback below undoes only the refused act and
            # not the setup -- otherwise the payback the assertion re-reads was
            # never persisted and the test fails on its own fixture.
            db.session.commit()
            payback_id = payback.id
            assert settled_figure(payback) == Decimal("100.00")

            # The second card purchase would take the payback to $150.00.
            with pytest.raises(ValidationError, match="has settled at"):
                entry_service.create_entry(
                    transaction_id=txn.id, user_id=user.id,
                    details=entry_service.EntryDetails(
                        amount=Decimal("50.00"), description="Later card buy",
                        purchased_on=date(2026, 1, 9), is_credit=True,
                    ),
                )

            # Nothing moved: the record stands and the plan was not rewritten.
            db.session.rollback()
            reloaded = db.session.get(Transaction, payback_id)
            assert settled_figure(reloaded) == Decimal("100.00")
            assert worth(reloaded) == Decimal("100.00")

    def test_a_sync_that_changes_NOTHING_is_still_allowed(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The guard compares against what the payback RECORDED, not its plan.

        Without this case the refusal could be written as "any sync touching a
        settled payback", which would fail every re-sync the source triggers for
        an unrelated reason -- a description edit, a debit entry added beside the
        credit one -- none of which changes what the payback owes.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry_service.create_entry(
                transaction_id=txn.id, user_id=user.id,
                details=entry_service.EntryDetails(
                    amount=Decimal("100.00"), description="Card buy",
                    purchased_on=date(2026, 1, 5), is_credit=True,
                ),
            )
            payback = sync_entry_payback(txn.id, user.id)
            db.session.flush()
            transaction_service.settle_transaction(payback)
            db.session.flush()

            # A DEBIT entry does not change the credit total.
            entry_service.create_entry(
                transaction_id=txn.id, user_id=user.id,
                details=entry_service.EntryDetails(
                    amount=Decimal("25.00"), description="Cash buy",
                    purchased_on=date(2026, 1, 9), is_credit=False,
                ),
            )
            assert settled_figure(payback) == Decimal("100.00")

    def test_removing_the_last_card_purchase_cannot_DELETE_a_settled_payback(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The DELETE arm's twin of the refusal above, and the larger harm.

        **The guard above shipped without this one, and two independent
        adversarial reviews found the asymmetry** (2026-08-17): the same
        function refused to RE-DERIVE a payback whose money had moved while
        going on to DESTROY one outright the moment the last credit purchase
        was removed.  Deleting a settled row erases a record of money that left
        the account -- strictly worse than re-pricing it -- and it happened with
        no refusal and an INFO log line as the only trace.

        The source row stays PROJECTED throughout, which is what makes this
        reachable: ``_reject_settled_parent`` guards the SOURCE's entries and
        has nothing to say about the payback's own status.

        Shown to FIRE: replacing the refusal with ``pass`` deletes the payback
        and this reads ``None``.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = entry_service.create_entry(
                transaction_id=txn.id, user_id=user.id,
                details=entry_service.EntryDetails(
                    amount=Decimal("100.00"), description="Card buy",
                    purchased_on=date(2026, 1, 5), is_credit=True,
                ),
            )
            payback = sync_entry_payback(txn.id, user.id)
            db.session.flush()
            transaction_service.settle_transaction(payback)
            db.session.commit()
            payback_id, entry_id = payback.id, entry.id
            assert settled_figure(payback) == Decimal("100.00")

            with pytest.raises(ValidationError, match="cannot be removed"):
                entry_service.delete_entry(entry_id, user.id)

            db.session.rollback()
            reloaded = db.session.get(Transaction, payback_id)
            assert reloaded is not None, (
                "a SETTLED payback was hard-deleted: $100.00 that had already "
                "left the account, erased with no refusal"
            )
            assert settled_figure(reloaded) == Decimal("100.00")


class TestAPaybackIsWorthTheCardSpendItRepays:
    """Plan step **X-au-i**: the figure is DERIVED, and by which of two arms.

    A payback repays the card spend of ONE source row, and what that source's
    card spend IS depends on how the source holds its money.  The dispatch is
    ``Transaction.tracks_purchases`` -- the app's one published answer to *does
    this row hold its spend, or do its entries?* -- and the two source kinds are
    disjoint by a write-door refusal, not by convention: ``mutations`` refuses
    Credit status on an entry-capable row.

    **Both arms are graded because the step's own one-line specification named
    only one of them.**  It said *"a payback's figure is the credit entries it
    repays"*, and a census of the 2026-08-20 production clone measured 22 live
    paybacks of which **12 have a source with no entries at all** -- their
    source is a whole transaction marked Credit.  Applied literally that
    sentence values those twelve at ``$0.00``.
    """

    def test_an_entry_capable_source_is_worth_its_CREDIT_purchases(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Rule 6: the credit purchases, and not the debit ones beside them."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            for amount, is_credit in (
                (Decimal("40.00"), True),
                (Decimal("35.00"), False),   # a DEBIT, which the card did not pay
                (Decimal("10.00"), True),
            ):
                entry_service.create_entry(
                    transaction_id=txn.id, user_id=user.id,
                    details=entry_service.EntryDetails(
                        amount=amount, description="Buy",
                        purchased_on=date(2026, 1, 5), is_credit=is_credit,
                    ),
                )
            payback = sync_entry_payback(txn.id, user.id)
            db.session.flush()

            assert worth(payback) == Decimal("50.00")
            assert payback.estimated_amount is None, (
                "a derived row stores no figure -- that pairing is "
                "ck_transactions_amount_ownership"
            )

    def test_a_single_spend_source_is_worth_THE_WHOLE_ROW(
        self, app, db, seed_user, seed_periods,
    ):
        """Rule 7, the arm the step's one-line specification would have zeroed.

        The source carries no entries at all, so *the credit entries it repays*
        sums to nothing.  What went on the card is the row.
        """
        with app.app_context():
            user = seed_user["user"]
            source = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="Rowing Machine",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                estimated_amount=Decimal("1958.87"),
            )
            db.session.add(source)
            db.session.flush()
            assert not source.entries

            payback = credit_workflow.mark_as_credit(source.id, user.id)
            db.session.flush()

            assert worth(payback) == Decimal("1958.87")
            assert payback.estimated_amount is None

    def test_the_two_arms_are_chosen_by_the_SOURCE_and_not_by_the_payback(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The dispatch is a fact about the source, read live.

        Stated as its own case because the refinement could have been STAMPED
        onto the payback at creation, and that is precisely what
        :class:`app.enums.AmountSourceEnum` rules against: a relation names the
        row that prices this one, and how that row prices it is the related
        row's own property.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]
            entry_service.create_entry(
                transaction_id=txn.id, user_id=user.id,
                details=entry_service.EntryDetails(
                    amount=Decimal("40.00"), description="Card",
                    purchased_on=date(2026, 1, 5), is_credit=True,
                ),
            )
            payback = sync_entry_payback(txn.id, user.id)
            db.session.flush()

            assert cash_ledger.amount_rule(payback) is (
                cash_ledger.AmountRule.CC_PAYBACK_PURCHASES
            )
            # The payback itself is not entry-capable and never was; if the
            # dispatch read ITS shape rather than its source's, every payback
            # would take the single-spend arm.
            assert not payback.tracks_purchases


class TestAHandEditToAPaybackIsREFUSED:
    """Developer ruling 2026-08-20, the ruling plan step X-au-i owed.

    A payback is worth what went on the card, so you change it by changing the
    purchases -- not by typing over the total.  Before this step the box
    rendered, took a figure, and the next entry mutation on the source silently
    overwrote it, which is finding **N-252**: production payback 2590 was
    hand-edited to ``$123.18`` against credit entries summing to ``$181.58`` and
    settled there, ``$58.40`` that no screen reported, because the
    ``is_override`` flag that would have marked it is unreachable for a row
    carrying neither a template nor a transfer link.
    """

    def test_the_PATCH_door_refuses_a_typed_estimate(
        self, app, db, auth_client, seed_user, seed_periods, seed_entry_template,
    ):
        """The crafted-request backstop behind the withdrawn input."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]
            entry_service.create_entry(
                transaction_id=txn.id, user_id=user.id,
                details=entry_service.EntryDetails(
                    amount=Decimal("40.00"), description="Card",
                    purchased_on=date(2026, 1, 5), is_credit=True,
                ),
            )
            payback = sync_entry_payback(txn.id, user.id)
            db.session.commit()
            payback_id = payback.id

        response = auth_client.patch(
            f"/transactions/{payback_id}",
            data={"estimated_amount": "123.18"},
        )

        assert response.status_code == 400
        assert b"repays what went on the card" in response.data

        with app.app_context():
            reloaded = db.session.get(Transaction, payback_id)
            assert worth(reloaded) == Decimal("40.00"), (
                "the refused figure must not have landed"
            )
            assert reloaded.amount_source_id is not None, (
                "and the relation that prices it must still stand -- a typed "
                "figure clears it, which is how the row would detach"
            )


class TestTheSettledRefusalAsksWhatTHISWriteMoves:
    """Finding **N-323**: the guard was wider than the policy it enforces.

    The policy is right and stays: money that has moved is a RECORD, and a
    record is changed by reverting the row and settling it again, never by
    re-deriving it underneath.  A settled payback whose source then takes
    another card purchase has liability the projection is not booking, and that
    is refused.

    **What was wrong is the PREDICATE.**  It fired whenever the recorded figure
    differed from the credit total AT ALL, so a settled payback carrying
    pre-existing drift refused every later edit on its envelope -- including
    edits that cannot move that total.  Measured on a production clone:
    envelope 2275's payback recorded ``$50.80`` against ``$49.52`` of credit
    entries and envelope 2276's recorded ``$123.18`` against ``$181.58``, and
    **5 of the developer's own 124 statement proposals, worth ``$706.35``,
    could not be accepted at all** -- because accepting one stamps a DEBIT
    purchase's bank posting day, which changes no credit entry.

    The question a write should be asked is whether IT moves the credit total,
    which is what ``moves_credit_total`` carries.
    """

    @staticmethod
    def _drifted_settled_payback(db, seed_user, txn, user):
        """Return ``(payback, debit_entry)`` in production 2276's exact state.

        The drift is reached through the app's own CORRECTION door -- a settle
        that records what the statement said rather than what the row derived --
        so this is a state the app can produce rather than one the fixture typed
        past a guard.
        """
        entry_service.create_entry(
            transaction_id=txn.id, user_id=user.id,
            details=entry_service.EntryDetails(
                amount=Decimal("181.58"), description="Card",
                purchased_on=date(2026, 1, 5), is_credit=True,
            ),
        )
        debit = entry_service.create_entry(
            transaction_id=txn.id, user_id=user.id,
            details=entry_service.EntryDetails(
                amount=Decimal("25.00"), description="Aldi",
                purchased_on=date(2026, 1, 5),
            ),
        )
        payback = sync_entry_payback(txn.id, user.id)
        db.session.flush()
        assert worth(payback) == Decimal("181.58")

        # The correction: the card statement said $123.18.
        transaction_service.settle_transaction(
            payback, submitted=Decimal("123.18"),
        )
        db.session.commit()
        assert settled_figure(payback) == Decimal("123.18")
        return payback, debit

    def test_a_DEBIT_purchase_edit_is_ALLOWED_on_a_drifted_settled_payback(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The $706.35 case: stamping a posting day cannot move a credit total."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]
            payback, debit = self._drifted_settled_payback(
                db, seed_user, txn, user,
            )

            # This is exactly what accepting a statement proposal does.
            entry_service.update_entry(
                debit.id, user.id, settled_on=date(2026, 1, 7),
            )
            db.session.flush()

            assert debit.settled_on == date(2026, 1, 7)
            assert settled_figure(payback) == Decimal("123.18"), (
                "the record is untouched -- the edit was about a debit"
            )

    def test_a_CREDIT_purchase_is_STILL_REFUSED_on_one(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The narrowing did not open the hole the guard exists to close.

        Stated beside its sibling because a predicate loosened one case too far
        looks identical to one loosened correctly until this case is asked.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]
            payback, _ = self._drifted_settled_payback(db, seed_user, txn, user)
            payback_id = payback.id

            with pytest.raises(ValidationError, match="has settled at"):
                entry_service.create_entry(
                    transaction_id=txn.id, user_id=user.id,
                    details=entry_service.EntryDetails(
                        amount=Decimal("50.00"), description="Later card buy",
                        purchased_on=date(2026, 1, 9), is_credit=True,
                    ),
                )

            db.session.rollback()
            reloaded = db.session.get(Transaction, payback_id)
            assert settled_figure(reloaded) == Decimal("123.18")
