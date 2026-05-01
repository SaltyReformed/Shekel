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
from app.services import credit_workflow, entry_service
from app.services.entry_credit_workflow import sync_entry_payback


class TestSyncEntryPayback:
    """Tests for sync_entry_payback -- the 2x2 state matrix."""

    def _create_credit_entry(self, txn, user, amount="100.00", desc="Purchase"):
        """Create a credit entry directly, bypassing entry_service hooks.

        Returns the new TransactionEntry (flushed, id available).
        """
        entry = TransactionEntry(
            transaction_id=txn.id,
            user_id=user.id,
            amount=Decimal(amount),
            description=desc,
            entry_date=date(2026, 1, 5),
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
            transaction_id=txn.id,
            user_id=user.id,
            amount=Decimal(amount),
            description=desc,
            entry_date=date(2026, 1, 5),
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
            assert payback.estimated_amount == Decimal("100.00")
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
            assert payback.estimated_amount == Decimal("150.00")

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

            assert payback.estimated_amount == Decimal("75.00")

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
            assert payback.estimated_amount == Decimal("100.00")

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
            assert payback_again.estimated_amount == Decimal("100.00")

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
            assert payback.estimated_amount == Decimal("100.00")

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
            assert payback.estimated_amount == Decimal("150.00")

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
                transaction_id=txn.id,
                user_id=user.id,
                amount=Decimal("100.00"),
                description="Field parity",
                entry_date=date(2026, 1, 5),
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
            assert payback.estimated_amount == Decimal("100.00")
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
                transaction_id=txn.id,
                user_id=user.id,
                amount=Decimal("50.00"),
                description="Period check",
                entry_date=date(2026, 1, 5),
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
                transaction_id=txn.id,
                user_id=user.id,
                amount=Decimal("50.00"),
                description="Name check",
                entry_date=date(2026, 1, 5),
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
                transaction_id=txn1.id, user_id=user.id,
                amount=Decimal("100.00"), description="Txn1",
                entry_date=date(2026, 1, 5), is_credit=True,
            )
            e2 = TransactionEntry(
                transaction_id=txn2.id, user_id=user.id,
                amount=Decimal("200.00"), description="Txn2",
                entry_date=date(2026, 1, 30), is_credit=True,
            )
            db.session.add_all([e1, e2])
            db.session.flush()

            payback1 = sync_entry_payback(txn1.id, user.id)
            payback2 = sync_entry_payback(txn2.id, user.id)

            assert payback1.id != payback2.id
            assert payback1.estimated_amount == Decimal("100.00")
            assert payback2.estimated_amount == Decimal("200.00")
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
                    transaction_id=txn.id,
                    user_id=user.id,
                    amount=Decimal(amt),
                    description="Split",
                    entry_date=date(2026, 1, 5),
                    is_credit=True,
                )
                db.session.add(e)
            db.session.flush()

            payback = sync_entry_payback(txn.id, user.id)

            assert payback.estimated_amount == Decimal("100.00")

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
                transaction_id=txn.id,
                user_id=user.id,
                amount=Decimal("0.01"),
                description="Penny",
                entry_date=date(2026, 1, 5),
                is_credit=True,
            )
            db.session.add(e)
            db.session.flush()

            payback = sync_entry_payback(txn.id, user.id)

            assert payback.estimated_amount == Decimal("0.01")


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
                    transaction_id=txn.id,
                    user_id=user.id,
                    amount=Decimal(amt),
                    description=f"Store {i}",
                    entry_date=date(2026, 1, 5),
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
                transaction_id=txn.id,
                user_id=user.id,
                amount=Decimal("100.00"),
                description="First",
                entry_date=date(2026, 1, 5),
                is_credit=True,
            )
            db.session.add(e1)
            db.session.flush()

            payback = sync_entry_payback(txn.id, user.id)
            assert e1.credit_payback_id == payback.id

            e2 = TransactionEntry(
                transaction_id=txn.id,
                user_id=user.id,
                amount=Decimal("50.00"),
                description="Second",
                entry_date=date(2026, 1, 6),
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
                transaction_id=txn.id,
                user_id=user.id,
                amount=Decimal("100.00"),
                description="Toggle",
                entry_date=date(2026, 1, 5),
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
                transaction_id=txn.id,
                user_id=user.id,
                amount=Decimal("200.00"),
                description="Debit",
                entry_date=date(2026, 1, 5),
                is_credit=False,
            )
            credit = TransactionEntry(
                transaction_id=txn.id,
                user_id=user.id,
                amount=Decimal("100.00"),
                description="Credit",
                entry_date=date(2026, 1, 5),
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
                transaction_id=txn.id, user_id=user.id,
                amount=Decimal("100.00"), description="A",
                entry_date=date(2026, 1, 5), is_credit=True,
            )
            e2 = TransactionEntry(
                transaction_id=txn.id, user_id=user.id,
                amount=Decimal("50.00"), description="B",
                entry_date=date(2026, 1, 5), is_credit=True,
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
            assert payback.estimated_amount == Decimal("50.00")
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
                transaction_id=txn.id, user_id=user.id,
                amount=Decimal("100.00"), description="A",
                entry_date=date(2026, 1, 5), is_credit=True,
            )
            e2 = TransactionEntry(
                transaction_id=txn.id, user_id=user.id,
                amount=Decimal("50.00"), description="B",
                entry_date=date(2026, 1, 6), is_credit=True,
            )
            e3 = TransactionEntry(
                transaction_id=txn.id, user_id=user.id,
                amount=Decimal("75.00"), description="C",
                entry_date=date(2026, 1, 7), is_credit=True,
            )
            db.session.add_all([e1, e2, e3])
            db.session.flush()

            payback = sync_entry_payback(txn.id, user.id)
            assert payback.estimated_amount == Decimal("225.00")

            # Update e1 from $100 to $120.
            e1.amount = Decimal("120.00")
            db.session.flush()
            payback = sync_entry_payback(txn.id, user.id)
            assert payback.estimated_amount == Decimal("245.00")

            # Delete e2 ($50).
            db.session.delete(e2)
            db.session.flush()
            payback = sync_entry_payback(txn.id, user.id)
            assert payback.estimated_amount == Decimal("195.00")

            # Toggle e3 to debit.
            e3.is_credit = False
            db.session.flush()
            payback = sync_entry_payback(txn.id, user.id)
            assert payback.estimated_amount == Decimal("120.00")

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
                transaction_id=txn.id, user_id=user.id,
                amount=Decimal("100.00"), description="Toggle",
                entry_date=date(2026, 1, 5), is_credit=True,
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
                    transaction_id=txn.id, user_id=user.id,
                    amount=Decimal("50.00"), description=f"Debit {i}",
                    entry_date=date(2026, 1, 5), is_credit=False,
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
            assert payback.estimated_amount == Decimal("100.00")

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
                amount=Decimal("100.00"),
                description="Credit purchase",
                entry_date=date(2026, 1, 5),
                is_credit=True,
            )

            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .first()
            )
            assert payback is not None
            assert payback.estimated_amount == Decimal("100.00")

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
                amount=Decimal("100.00"),
                description="Debit purchase",
                entry_date=date(2026, 1, 5),
                is_credit=False,
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
                amount=Decimal("100.00"),
                description="Toggle test",
                entry_date=date(2026, 1, 5),
                is_credit=False,
            )

            entry_service.update_entry(entry.id, user.id, is_credit=True)

            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .first()
            )
            assert payback is not None
            assert payback.estimated_amount == Decimal("100.00")

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
                amount=Decimal("100.00"),
                description="Toggle test",
                entry_date=date(2026, 1, 5),
                is_credit=True,
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
                amount=Decimal("100.00"),
                description="First",
                entry_date=date(2026, 1, 5),
                is_credit=True,
            )
            entry_service.create_entry(
                transaction_id=txn.id,
                user_id=user.id,
                amount=Decimal("50.00"),
                description="Second",
                entry_date=date(2026, 1, 6),
                is_credit=True,
            )

            # Payback should be $150 (100 + 50).
            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .first()
            )
            assert payback.estimated_amount == Decimal("150.00")

            entry_service.delete_entry(e1.id, user.id)

            db.session.expire(payback)
            assert payback.estimated_amount == Decimal("50.00")

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
                amount=Decimal("100.00"),
                description="Only",
                entry_date=date(2026, 1, 5),
                is_credit=True,
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
                amount=Decimal("100.00"),
                description="Amount change",
                entry_date=date(2026, 1, 5),
                is_credit=True,
            )

            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .first()
            )
            assert payback.estimated_amount == Decimal("100.00")

            entry_service.update_entry(entry.id, user.id, amount=Decimal("75.00"))

            db.session.expire(payback)
            assert payback.estimated_amount == Decimal("75.00")

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
                amount=Decimal("100.00"),
                description="Companion purchase",
                entry_date=date(2026, 1, 5),
                is_credit=True,
            )

            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .first()
            )
            assert payback is not None
            assert payback.estimated_amount == Decimal("100.00")

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
                amount=Decimal("100.00"),
                description="Session test",
                entry_date=date(2026, 1, 5),
                is_credit=True,
            )

            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .first()
            )
            assert payback is not None
            assert payback.estimated_amount == Decimal("100.00")

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
                amount=Decimal("100.00"),
                description="First",
                entry_date=date(2026, 1, 5),
                is_credit=True,
            )
            entry_service.create_entry(
                transaction_id=txn.id,
                user_id=user.id,
                amount=Decimal("50.00"),
                description="Second",
                entry_date=date(2026, 1, 6),
                is_credit=True,
            )

            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .first()
            )
            assert payback.estimated_amount == Decimal("150.00")

            # Delete first entry ($100) -- only second ($50) remains.
            entry_service.delete_entry(e1.id, user.id)

            db.session.expire(payback)
            assert payback.estimated_amount == Decimal("50.00")
