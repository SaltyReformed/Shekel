"""
Shekel Budget App — Credit Workflow & Carry Forward Tests

Tests the credit card workflow (§4.5) and carry forward (§4.6)
services that are central to the payday workflow.
"""

from decimal import Decimal

import pytest

from app.extensions import db
from app.models.transaction import Transaction
from app.models.ref import Status, TransactionType
from app.services import credit_workflow, carry_forward_service
from app.exceptions import NotFoundError, ValidationError


class TestCreditWorkflow:
    """Tests for the credit card status + auto-payback mechanism."""

    def _create_expense(self, seed_user, seed_periods, amount="100.00"):
        """Helper: create a projected expense in the first period."""
        projected = db.session.query(Status).filter_by(name="projected").one()
        expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

        txn = Transaction(
            pay_period_id=seed_periods[0].id,
            scenario_id=seed_user["scenario"].id,
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

            payback = credit_workflow.mark_as_credit(txn.id)
            db.session.flush()

            # Original is now 'credit' status.
            assert txn.status.name == "credit"

            # Payback exists in the next period.
            assert payback.pay_period_id == seed_periods[1].id
            assert payback.estimated_amount == Decimal("100.00")
            assert payback.name == "CC Payback: Test Expense"
            assert payback.credit_payback_for_id == txn.id

    def test_unmark_credit_deletes_payback(self, app, db, seed_user, seed_periods):
        """Reverting credit status deletes the auto-generated payback."""
        with app.app_context():
            txn = self._create_expense(seed_user, seed_periods)
            payback = credit_workflow.mark_as_credit(txn.id)
            db.session.flush()
            payback_id = payback.id

            credit_workflow.unmark_credit(txn.id)
            db.session.flush()

            # Original reverted to projected.
            assert txn.status.name == "projected"

            # Payback is deleted.
            assert db.session.get(Transaction, payback_id) is None

    def test_cannot_credit_income(self, app, db, seed_user, seed_periods):
        """Marking income as credit raises ValidationError."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="projected").one()
            income_type = db.session.query(TransactionType).filter_by(name="income").one()

            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=projected.id,
                name="Paycheck",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=Decimal("2000.00"),
            )
            db.session.add(txn)
            db.session.flush()

            with pytest.raises(ValidationError):
                credit_workflow.mark_as_credit(txn.id)


class TestCarryForward:
    """Tests for the carry-forward-unpaid service."""

    def test_carry_forward_moves_projected_items(self, app, db, seed_user, seed_periods):
        """Carry forward moves projected items to the target period."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

            # Create two projected expenses in the first period.
            for name in ("Expense A", "Expense B"):
                txn = Transaction(
                    pay_period_id=seed_periods[0].id,
                    scenario_id=seed_user["scenario"].id,
                    status_id=projected.id,
                    name=name,
                    category_id=seed_user["categories"]["Groceries"].id,
                    transaction_type_id=expense_type.id,
                    estimated_amount=Decimal("50.00"),
                )
                db.session.add(txn)
            db.session.flush()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
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
            projected = db.session.query(Status).filter_by(name="projected").one()
            done = db.session.query(Status).filter_by(name="done").one()
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

            # One projected, one done.
            t1 = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=projected.id,
                name="Unpaid",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("100.00"),
            )
            t2 = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
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
                seed_periods[0].id, seed_periods[1].id,
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
            projected = db.session.query(Status).filter_by(name="projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

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
                seed_periods[0].id, seed_periods[1].id,
            )
            db.session.flush()

            # Should now be flagged as override.
            assert txn.is_override is True
            assert txn.pay_period_id == seed_periods[1].id


# Import at the bottom to avoid circular issues in the test helpers.
from app.models.transaction_template import TransactionTemplate
