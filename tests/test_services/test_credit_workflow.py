"""
Shekel Budget App -- Credit Workflow & Carry Forward Tests

Tests the credit card workflow (§4.5) and carry forward (§4.6)
services that are central to the payday workflow.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.ref import AccountType, Status, TransactionType
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import credit_workflow, carry_forward_service
from app.exceptions import NotFoundError, ValidationError


class TestCreditWorkflow:
    """Tests for the credit card status + auto-payback mechanism."""

    def _create_expense(self, seed_user, seed_periods, amount="100.00"):
        """Helper: create a projected expense in the first period."""
        projected = db.session.query(Status).filter_by(name="Projected").one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

        txn = Transaction(
            pay_period_id=seed_periods[0].id,
            scenario_id=seed_user["scenario"].id,
            account_id=seed_user["account"].id,
            status_id=projected.id,
            name="Test Expense",
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=expense_type.id,
            estimated_amount=Decimal(amount),
        )
        db.session.add(txn)
        db.session.flush()
        return txn

    def test_mark_as_credit_creates_payback(self, app, db, seed_user, seed_periods):
        """Marking an expense as credit creates a payback in the next period."""
        with app.app_context():
            txn = self._create_expense(seed_user, seed_periods)

            payback = credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)
            db.session.flush()

            # Original is now 'credit' status.
            assert txn.status.name == "Credit"

            # Payback exists in the next period.
            assert payback.pay_period_id == seed_periods[1].id
            assert payback.estimated_amount == Decimal("100.00")
            assert payback.name == "CC Payback: Test Expense"
            assert payback.credit_payback_for_id == txn.id

    def test_unmark_credit_deletes_payback(self, app, db, seed_user, seed_periods):
        """Reverting credit status deletes the auto-generated payback."""
        with app.app_context():
            txn = self._create_expense(seed_user, seed_periods)
            payback = credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)
            db.session.flush()
            payback_id = payback.id

            credit_workflow.unmark_credit(txn.id, seed_user["user"].id)
            db.session.flush()

            # Original reverted to projected.
            assert txn.status.name == "Projected"

            # Payback is deleted.
            assert db.session.get(Transaction, payback_id) is None

    def test_cannot_credit_income(self, app, db, seed_user, seed_periods):
        """Marking income as credit raises ValidationError."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()

            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Paycheck",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=Decimal("2000.00"),
            )
            db.session.add(txn)
            db.session.flush()

            with pytest.raises(ValidationError):
                credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)

    def test_payback_uses_actual_amount_when_set(
        self, app, db, seed_user, seed_periods
    ):
        """Payback amount uses actual_amount when it is set on the original."""
        with app.app_context():
            txn = self._create_expense(seed_user, seed_periods, amount="100.00")
            txn.actual_amount = Decimal("75.00")
            db.session.flush()

            payback = credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)
            db.session.flush()

            assert payback.estimated_amount == Decimal("75.00")

    def test_auto_creates_cc_category_if_missing(
        self, app, db, seed_user, seed_periods
    ):
        """mark_as_credit auto-creates the CC Payback category if missing."""
        with app.app_context():
            # Delete the pre-seeded CC Payback category by ID (re-fetch to
            # avoid cross-session issues).
            cc_cat = db.session.get(
                Category, seed_user["categories"]["Payback"].id
            )
            db.session.delete(cc_cat)
            db.session.flush()

            txn = self._create_expense(seed_user, seed_periods)
            payback = credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)
            db.session.flush()

            # A new category should have been created.
            new_cat = (
                db.session.query(Category)
                .filter_by(
                    user_id=seed_user["user"].id,
                    group_name="Credit Card",
                    item_name="Payback",
                )
                .one()
            )
            # .one() above already raises if not found; accessing .id validates non-None
            assert payback.category_id == new_cat.id

    def test_no_next_period_raises_validation_error(
        self, app, db, seed_user, seed_periods
    ):
        """mark_as_credit raises ValidationError when no next period exists."""
        with app.app_context():
            # Create expense in the last period (no period follows it).
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            txn = Transaction(
                pay_period_id=seed_periods[-1].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Last Period Expense",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("50.00"),
            )
            db.session.add(txn)
            db.session.flush()

            with pytest.raises(ValidationError):
                credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)

    def test_mark_as_credit_wrong_user_raises_not_found(
        self, app, db, seed_user, seed_periods
    ):
        """Defense-in-depth: mark_as_credit with wrong user_id raises NotFoundError."""
        with app.app_context():
            txn = self._create_expense(seed_user, seed_periods)
            db.session.flush()

            with pytest.raises(NotFoundError):
                credit_workflow.mark_as_credit(txn.id, user_id=999999)

            # Transaction status must be unchanged -- no partial modification.
            db.session.refresh(txn)
            assert txn.status.name == "Projected"

    def test_unmark_credit_wrong_user_raises_not_found(
        self, app, db, seed_user, seed_periods
    ):
        """Defense-in-depth: unmark_credit with wrong user_id raises NotFoundError."""
        with app.app_context():
            txn = self._create_expense(seed_user, seed_periods)
            payback = credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)
            db.session.flush()
            payback_id = payback.id

            with pytest.raises(NotFoundError):
                credit_workflow.unmark_credit(txn.id, user_id=999999)

            # Transaction status must be unchanged -- still 'credit'.
            db.session.refresh(txn)
            assert txn.status.name == "Credit"

            # Payback transaction must still exist.
            assert db.session.get(Transaction, payback_id) is not None

    def test_mark_as_credit_nonexistent_txn_raises_not_found(
        self, app, db, seed_user, seed_periods
    ):
        """mark_as_credit on a nonexistent transaction raises NotFoundError."""
        with app.app_context():
            with pytest.raises(NotFoundError):
                credit_workflow.mark_as_credit(999999, seed_user["user"].id)

    def test_unmark_credit_nonexistent_txn_raises_not_found(
        self, app, db, seed_user, seed_periods
    ):
        """unmark_credit on a nonexistent transaction raises NotFoundError."""
        with app.app_context():
            with pytest.raises(NotFoundError):
                credit_workflow.unmark_credit(999999, seed_user["user"].id)


class TestCarryForward:
    """Tests for the carry-forward-unpaid service."""

    def test_carry_forward_moves_projected_items(self, app, db, seed_user, seed_periods):
        """Carry forward moves projected items to the target period."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            # Create two projected expenses in the first period.
            for name in ("Expense A", "Expense B"):
                txn = Transaction(
                    pay_period_id=seed_periods[0].id,
                    scenario_id=seed_user["scenario"].id,
                    account_id=seed_user["account"].id,
                    status_id=projected.id,
                    name=name,
                    category_id=seed_user["categories"]["Groceries"].id,
                    transaction_type_id=expense_type.id,
                    estimated_amount=Decimal("50.00"),
                )
                db.session.add(txn)
            db.session.flush()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["user"].id,
                seed_user["scenario"].id,
            )
            db.session.flush()

            assert count == 2

            # Verify they moved.
            remaining = (
                db.session.query(Transaction)
                .filter_by(pay_period_id=seed_periods[0].id)
                .all()
            )
            assert len(remaining) == 0

            moved = (
                db.session.query(Transaction)
                .filter_by(pay_period_id=seed_periods[1].id)
                .all()
            )
            assert len(moved) == 2

    def test_carry_forward_skips_done_items(self, app, db, seed_user, seed_periods):
        """Carry forward does NOT move done/received items."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            done = db.session.query(Status).filter_by(name="Paid").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            # One projected, one done.
            t1 = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Unpaid",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("100.00"),
            )
            t2 = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=done.id,
                name="Already Paid",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
                actual_amount=Decimal("500.00"),
            )
            db.session.add_all([t1, t2])
            db.session.flush()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["user"].id,
                seed_user["scenario"].id,
            )
            db.session.flush()

            # Only the projected item moved.
            assert count == 1

            # Done item stays in original period.
            remaining = (
                db.session.query(Transaction)
                .filter_by(pay_period_id=seed_periods[0].id)
                .all()
            )
            assert len(remaining) == 1
            assert remaining[0].name == "Already Paid"

    def test_carry_forward_flags_template_items_as_override(
        self, app, db, seed_user, seed_periods
    ):
        """Template-linked items are flagged is_override when carried forward."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            # Create a template (without full recurrence for simplicity).
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Car Payment"].id,
                transaction_type_id=expense_type.id,
                name="Car Payment",
                default_amount=Decimal("300.00"),
            )
            db.session.add(template)
            db.session.flush()

            # Create a template-linked transaction.
            txn = Transaction(
                template_id=template.id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Car Payment",
                category_id=seed_user["categories"]["Car Payment"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("300.00"),
                is_override=False,
            )
            db.session.add(txn)
            db.session.flush()

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["user"].id,
                seed_user["scenario"].id,
            )
            db.session.flush()

            # Should now be flagged as override.
            assert txn.is_override is True
            assert txn.pay_period_id == seed_periods[1].id

    def test_carry_forward_skips_cancelled_items(self, app, db, seed_user, seed_periods):
        """Cancelled items stay in the source period and are not carried forward."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            cancelled = db.session.query(Status).filter_by(name="Cancelled").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            # One projected (should move), one cancelled (should stay).
            t1 = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Unpaid Expense",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("80.00"),
            )
            t2 = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=cancelled.id,
                name="Cancelled Expense",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("200.00"),
            )
            db.session.add_all([t1, t2])
            db.session.flush()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["user"].id,
                seed_user["scenario"].id,
            )
            db.session.flush()

            assert count == 1

            # Cancelled item stays in source period.
            remaining = (
                db.session.query(Transaction)
                .filter_by(pay_period_id=seed_periods[0].id)
                .all()
            )
            assert len(remaining) == 1
            assert remaining[0].name == "Cancelled Expense"

    def test_carry_forward_skips_received_items(self, app, db, seed_user, seed_periods):
        """Received income items stay in the source period and are not carried forward."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            received = db.session.query(Status).filter_by(name="Received").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()

            # One projected expense (should move), one received income (should stay).
            t1 = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Unpaid Expense",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("60.00"),
            )
            t2 = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=received.id,
                name="Received Paycheck",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=Decimal("2000.00"),
                actual_amount=Decimal("2000.00"),
            )
            db.session.add_all([t1, t2])
            db.session.flush()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["user"].id,
                seed_user["scenario"].id,
            )
            db.session.flush()

            assert count == 1

            # Received item stays in source period.
            remaining = (
                db.session.query(Transaction)
                .filter_by(pay_period_id=seed_periods[0].id)
                .all()
            )
            assert len(remaining) == 1
            assert remaining[0].name == "Received Paycheck"

    def test_carry_forward_skips_soft_deleted_items(self, app, db, seed_user, seed_periods):
        """Soft-deleted projected items are excluded from carry forward."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            # Soft-deleted projected expense -- should NOT be moved.
            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Deleted Expense",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("40.00"),
                is_deleted=True,
            )
            db.session.add(txn)
            db.session.flush()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["user"].id,
                seed_user["scenario"].id,
            )
            db.session.flush()

            assert count == 0

            # Item stays in source period, untouched.
            assert txn.pay_period_id == seed_periods[0].id

    def test_carry_forward_source_not_found(self, app, db, seed_user, seed_periods):
        """NotFoundError is raised when the source period does not exist."""
        with app.app_context():
            with pytest.raises(NotFoundError):
                carry_forward_service.carry_forward_unpaid(
                    999999, seed_periods[1].id, seed_user["user"].id,
                    seed_user["scenario"].id,
                )

    def test_carry_forward_target_not_found(self, app, db, seed_user, seed_periods):
        """NotFoundError is raised when the target period does not exist."""
        with app.app_context():
            with pytest.raises(NotFoundError):
                carry_forward_service.carry_forward_unpaid(
                    seed_periods[0].id, 999999, seed_user["user"].id,
                    seed_user["scenario"].id,
                )

    def test_carry_forward_empty_source_returns_zero(self, app, db, seed_user, seed_periods):
        """Carry forward returns 0 when the source period has no transactions."""
        with app.app_context():
            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["user"].id,
                seed_user["scenario"].id,
            )

            assert count == 0

    def test_carry_forward_wrong_user_source_raises_not_found(
        self, app, db, seed_user, seed_periods
    ):
        """Defense-in-depth: wrong user_id on source raises NotFoundError."""
        with app.app_context():
            with pytest.raises(NotFoundError):
                carry_forward_service.carry_forward_unpaid(
                    seed_periods[0].id, seed_periods[1].id, user_id=999999,
                    scenario_id=seed_user["scenario"].id,
                )

    def test_carry_forward_wrong_user_target_raises_not_found(
        self, app, db, seed_user, seed_periods
    ):
        """Defense-in-depth: source passes but target belongs to another user."""
        with app.app_context():
            from app.models.user import User, UserSettings
            from app.models.scenario import Scenario
            from app.services.auth_service import hash_password
            from app.services import pay_period_service

            # Create a second user with their own pay period.
            user2 = User(
                email="second@shekel.local",
                password_hash=hash_password("secondpass1234"),
                display_name="Second User",
            )
            db.session.add(user2)
            db.session.flush()

            settings2 = UserSettings(user_id=user2.id)
            scenario2 = Scenario(
                user_id=user2.id, name="Baseline", is_baseline=True,
            )
            db.session.add_all([settings2, scenario2])
            db.session.flush()

            periods2 = pay_period_service.generate_pay_periods(
                user_id=user2.id,
                start_date=date(2026, 6, 1),
                num_periods=2,
                cadence_days=14,
            )
            db.session.flush()

            # Source belongs to seed_user (passes), target belongs to user2 (fails).
            with pytest.raises(NotFoundError):
                carry_forward_service.carry_forward_unpaid(
                    seed_periods[0].id, periods2[0].id, seed_user["user"].id,
                    seed_user["scenario"].id,
                )

    def test_carry_forward_nonexistent_source_raises_not_found(
        self, app, db, seed_user, seed_periods
    ):
        """Nonexistent source period raises NotFoundError."""
        with app.app_context():
            with pytest.raises(NotFoundError):
                carry_forward_service.carry_forward_unpaid(
                    999999, seed_periods[0].id, seed_user["user"].id,
                    seed_user["scenario"].id,
                )

    def test_carry_forward_nonexistent_target_raises_not_found(
        self, app, db, seed_user, seed_periods
    ):
        """Nonexistent target period raises NotFoundError."""
        with app.app_context():
            with pytest.raises(NotFoundError):
                carry_forward_service.carry_forward_unpaid(
                    seed_periods[0].id, 999999, seed_user["user"].id,
                    seed_user["scenario"].id,
                )


# Import at the bottom to avoid circular issues in the test helpers.
from app.models.transaction_template import TransactionTemplate


class TestNegativePaths:
    """Negative-path and edge-case tests for credit workflow and carry forward.

    Covers: unmark on projected transactions, double-mark idempotency,
    carry-forward source==target, and marking done transactions as credit.
    """

    def _create_expense(self, seed_user, seed_periods, amount="100.00",
                        status_name="Projected"):
        """Helper: create an expense in the first period with the given status."""
        status = db.session.query(Status).filter_by(name=status_name).one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

        txn = Transaction(
            pay_period_id=seed_periods[0].id,
            scenario_id=seed_user["scenario"].id,
            account_id=seed_user["account"].id,
            status_id=status.id,
            name="Test Expense",
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=expense_type.id,
            estimated_amount=Decimal(amount),
        )
        db.session.add(txn)
        db.session.flush()
        return txn

    def test_unmark_credit_on_projected_transaction(self, app, db, seed_user, seed_periods):
        """unmark_credit on a non-Credit row raises ValidationError.

        After the C-21 follow-up the helper guards against being
        called on a transaction that is not currently in Credit
        status -- the previous implementation silently rewrote the
        status to Projected on any caller-supplied row, which
        would (for a Paid row, for example) drop a settled
        transaction back to Projected and erase its place in the
        audit trail.

        The original intent of this test ("A user double-clicking
        'unmark credit' or calling the endpoint on the wrong
        transaction must not corrupt data") is now satisfied by a
        loud rejection rather than a silent no-op: the row stays
        unchanged AND the caller learns the request was rejected.
        """
        with app.app_context():
            txn = self._create_expense(seed_user, seed_periods)
            original_status_id = txn.status_id

            with pytest.raises(ValidationError) as excinfo:
                credit_workflow.unmark_credit(txn.id, seed_user["user"].id)
            msg = str(excinfo.value)
            assert "Projected" in msg
            assert "Only Credit" in msg

            # Row unchanged -- the bespoke guard fires before any write.
            db.session.refresh(txn)
            assert txn.status.name == "Projected"
            assert txn.status_id == original_status_id

            # No payback transaction exists or was created.
            payback_count = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .count()
            )
            assert payback_count == 0

    def test_double_mark_as_credit_returns_existing_payback(
        self, app, db, seed_user, seed_periods
    ):
        """Double-marking a transaction as credit returns the existing payback idempotently.

        The function checks if the transaction's status is already 'credit'
        and, if so, returns the existing payback rather than creating a duplicate.
        This is critical: a duplicate payback would mean the user sees double
        the debt repayment on their grid.
        """
        with app.app_context():
            txn = self._create_expense(seed_user, seed_periods)

            # First mark: creates the payback.
            first_payback = credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)
            db.session.flush()
            first_payback_id = first_payback.id

            # Second mark: should return the existing payback, not create a new one.
            second_payback = credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)
            db.session.flush()

            # Same payback returned.
            assert second_payback.id == first_payback_id

            # Only one payback exists for this transaction.
            payback_count = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .count()
            )
            assert payback_count == 1

    def test_carry_forward_same_period_behavior(self, app, db, seed_user, seed_periods):
        """carry_forward with source == target returns 0 without modifying transactions.

        The guard ``if source_period_id == target_period_id: return 0``
        prevents the function from touching any transactions, which avoids
        the previous bug of setting is_override=True on items that were not
        actually carried forward.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            # Create a template for a template-linked transaction.
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                name="Template Expense",
                default_amount=Decimal("50.00"),
            )
            db.session.add(template)
            db.session.flush()

            # Create a template-linked transaction (is_override=False).
            txn_with_template = Transaction(
                template_id=template.id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Template Expense",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("50.00"),
                is_override=False,
            )
            # Create an ad-hoc transaction (no template).
            txn_adhoc = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Ad-hoc Expense",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("30.00"),
            )
            db.session.add_all([txn_with_template, txn_adhoc])
            db.session.flush()

            # Carry forward with source == target -- early return.
            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[0].id, seed_user["user"].id,
                seed_user["scenario"].id,
            )
            db.session.flush()

            # Guard returns 0 -- no items processed.
            assert count == 0

            # Template-linked item is untouched (is_override stays False).
            db.session.refresh(txn_with_template)
            assert txn_with_template.is_override is False
            assert txn_with_template.pay_period_id == seed_periods[0].id

            # Ad-hoc item is also untouched.
            db.session.refresh(txn_adhoc)
            assert txn_adhoc.pay_period_id == seed_periods[0].id

    def test_mark_credit_on_done_transaction(self, app, db, seed_user, seed_periods):
        """Marking a 'done' transaction as credit raises ValidationError.

        Only projected transactions can be marked as credit. A done
        transaction (already paid) must not be retroactively put on credit,
        which would create a phantom payback for money already spent.
        """
        with app.app_context():
            txn = self._create_expense(
                seed_user, seed_periods, status_name="Paid",
            )

            with pytest.raises(ValidationError, match="Only projected transactions"):
                credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)

            # Status unchanged -- still 'done'.
            db.session.refresh(txn)
            assert txn.status.name == "Paid"

            # No payback was created.
            payback_count = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .count()
            )
            assert payback_count == 0

    def test_mark_credit_on_cancelled_transaction(self, app, db, seed_user, seed_periods):
        """Marking a 'cancelled' transaction as credit raises ValidationError.

        Cancelled transactions must also be rejected by the status guard,
        not just done transactions.
        """
        with app.app_context():
            txn = self._create_expense(
                seed_user, seed_periods, status_name="Cancelled",
            )

            with pytest.raises(ValidationError, match="Only projected transactions"):
                credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)

            # Status unchanged -- still 'cancelled'.
            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"

    def test_mark_credit_rejects_shadow_transaction(
        self, app, db, seed_user, seed_periods
    ):
        """mark_as_credit raises ValidationError for shadow (transfer) transactions.

        Defense-in-depth: the route layer already blocks this, but the
        service must enforce its own invariants independently (M-03).
        """
        with app.app_context():
            # Create a second account so we can build a real Transfer.
            savings_type = (
                db.session.query(AccountType).filter_by(name="Savings").one()
            )
            savings = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Test Savings",
                current_anchor_balance=Decimal("0"),
            )
            db.session.add(savings)
            db.session.flush()

            projected = (
                db.session.query(Status).filter_by(name="Projected").one()
            )
            transfer = Transfer(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=projected.id,
                amount=Decimal("200.00"),
                name="Test Transfer",
            )
            db.session.add(transfer)
            db.session.flush()

            # Create a shadow transaction linked to the transfer.
            txn = self._create_expense(seed_user, seed_periods)
            txn.transfer_id = transfer.id
            db.session.flush()

            with pytest.raises(ValidationError, match="transfer"):
                credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)

            # Status unchanged -- still projected.
            db.session.refresh(txn)
            assert txn.status.name == "Projected"
