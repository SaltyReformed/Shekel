"""
Shekel Budget App — End-to-End Workflow Tests

Tests multi-step workflows that span services and routes:
  - Salary profile → income transactions in grid
  - Template + recurrence → transactions in correct periods
  - Transfer template → balance calculator includes effects
  - Credit workflow → payback in next period → balance unaffected
  - Anchor balance change → downstream balances recalculate
  - Carry forward → projected items moved to target period
"""

from collections import OrderedDict
from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import (
    AccountType, RecurrencePattern, Status, TransactionType,
)
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import (
    balance_calculator,
    carry_forward_service,
    credit_workflow,
    recurrence_engine,
    transfer_recurrence,
)


class TestSalaryToGrid:
    """Salary profile creation → income transactions appear in grid periods."""

    def test_salary_generates_income_per_period(self, app, db, seed_user, seed_periods):
        """Creating a salary template with every_period recurrence populates all periods."""
        with app.app_context():
            income_type = db.session.query(TransactionType).filter_by(name="income").one()
            every_period = db.session.query(RecurrencePattern).filter_by(name="every_period").one()

            # Create recurrence rule and template (mimics salary profile creation).
            rule = RecurrenceRule(user_id=seed_user["user"].id, pattern_id=every_period.id)
            db.session.add(rule)
            db.session.flush()

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                name="Paycheck",
                default_amount=Decimal("2884.62"),
                recurrence_rule_id=rule.id,
            )
            db.session.add(template)
            db.session.flush()

            # Generate income transactions across all 10 periods.
            txns = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.commit()

            # One income transaction per period.
            assert len(txns) == 10
            for txn in txns:
                assert txn.transaction_type.name == "income"
                assert txn.estimated_amount == Decimal("2884.62")
                assert txn.status.name == "projected"


class TestTemplateRecurrenceToGrid:
    """Template with monthly recurrence → transactions in correct periods."""

    def test_monthly_recurrence_hits_correct_periods(self, app, db, seed_user, seed_periods):
        """Monthly recurrence on day 15 places transactions in periods containing that day."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()
            monthly = db.session.query(RecurrencePattern).filter_by(name="monthly").one()

            rule = RecurrenceRule(user_id=seed_user["user"].id, pattern_id=monthly.id, day_of_month=15)
            db.session.add(rule)
            db.session.flush()

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                name="Rent",
                default_amount=Decimal("1200.00"),
                recurrence_rule_id=rule.id,
            )
            db.session.add(template)
            db.session.flush()

            txns = recurrence_engine.generate_for_template(
                template, seed_periods, seed_user["scenario"].id,
            )
            db.session.commit()

            # Monthly on day 15 across 10 biweekly periods (~140 days / ~5 months).
            # Exact count depends on which periods contain the 15th.
            assert len(txns) >= 1
            for txn in txns:
                assert txn.name == "Rent"
                assert txn.estimated_amount == Decimal("1200.00")
                # Each transaction's period should contain the 15th of some month.
                assert txn.pay_period.start_date.day <= 15 or txn.pay_period.end_date.day >= 15


class TestTransferToBalance:
    """Transfer template → balance calculator includes transfer effects."""

    def test_transfer_reduces_source_balance(self, app, db, seed_user, seed_periods):
        """Balance calculator subtracts outgoing transfers from the source account."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(name="savings").one()
            savings = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Savings",
                current_anchor_balance=Decimal("0"),
            )
            db.session.add(savings)
            db.session.flush()

            projected = db.session.query(Status).filter_by(name="projected").one()

            # Create a transfer in the first period.
            xfer = Transfer(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=projected.id,
                name="Save $200",
                amount=Decimal("200.00"),
            )
            db.session.add(xfer)
            db.session.commit()

            # Calculate balances without transfers.
            balances_no_xfer = balance_calculator.calculate_balances(
                anchor_balance=Decimal("1000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods,
                transactions=[],
            )

            # Calculate balances with transfers (for checking account).
            balances_with_xfer = balance_calculator.calculate_balances(
                anchor_balance=Decimal("1000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods,
                transactions=[],
                transfers=[xfer],
                account_id=seed_user["account"].id,
            )

            # First period balance should be $200 less with the transfer.
            diff = balances_no_xfer[seed_periods[0].id] - balances_with_xfer[seed_periods[0].id]
            assert diff == Decimal("200.00")


class TestCreditPaybackBalance:
    """Credit workflow → payback in next period → balance unaffected."""

    def test_credit_creates_payback_and_zeroes_effective(self, app, db, seed_user, seed_periods):
        """Marking as credit creates payback and makes original effective_amount 0."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=projected.id,
                name="Dinner Out",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("75.00"),
            )
            db.session.add(txn)
            db.session.commit()

            # Mark as credit — creates payback in next period.
            payback = credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)
            db.session.commit()

            db.session.refresh(txn)

            # Original transaction is credit status → effective_amount is 0.
            assert txn.status.name == "credit"
            assert txn.effective_amount == Decimal("0")

            # Payback exists in next period with matching amount.
            assert payback.pay_period_id == seed_periods[1].id
            assert payback.estimated_amount == Decimal("75.00")
            assert payback.credit_payback_for_id == txn.id

            # Balance calculation: credit txn contributes 0, payback contributes full amount.
            all_txns = db.session.query(Transaction).filter(
                Transaction.is_deleted.is_(False),
            ).all()

            balances = balance_calculator.calculate_balances(
                anchor_balance=Decimal("1000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods[:3],
                transactions=all_txns,
            )
            # Period 0: credit txn → $0 effect, balance stays 1000.
            assert balances[seed_periods[0].id] == Decimal("1000.00")
            # Period 1: payback expense of $75 → balance drops.
            assert balances[seed_periods[1].id] == Decimal("925.00")


class TestAnchorTrueUpBalance:
    """Anchor balance change → all downstream period balances recalculate."""

    def test_anchor_change_shifts_all_balances(self, app, db, seed_user, seed_periods):
        """Changing anchor balance shifts all downstream period balances by the same delta."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

            # Create an expense in period 1.
            txn = Transaction(
                pay_period_id=seed_periods[1].id,
                scenario_id=seed_user["scenario"].id,
                status_id=projected.id,
                name="Rent",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("1200.00"),
            )
            db.session.add(txn)
            db.session.commit()

            # Calculate with anchor = $2000.
            balances_2k = balance_calculator.calculate_balances(
                anchor_balance=Decimal("2000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods[:3],
                transactions=[txn],
            )

            # Calculate with anchor = $3000 (+$1000 difference).
            balances_3k = balance_calculator.calculate_balances(
                anchor_balance=Decimal("3000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods[:3],
                transactions=[txn],
            )

            # Every period's balance should differ by exactly $1000.
            for period in seed_periods[:3]:
                diff = balances_3k[period.id] - balances_2k[period.id]
                assert diff == Decimal("1000.00")


class TestCarryForwardWorkflow:
    """Carry forward → projected items moved to target period."""

    def test_carry_forward_moves_projected_items(self, app, db, seed_user, seed_periods):
        """carry_forward_unpaid moves projected transactions from source to target."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

            # Create 2 projected expenses in period 0.
            for name, amount in [("Groceries", "85.00"), ("Gas", "45.00")]:
                txn = Transaction(
                    pay_period_id=seed_periods[0].id,
                    scenario_id=seed_user["scenario"].id,
                    status_id=projected.id,
                    name=name,
                    category_id=seed_user["categories"]["Groceries"].id,
                    transaction_type_id=expense_type.id,
                    estimated_amount=Decimal(amount),
                )
                db.session.add(txn)
            db.session.commit()

            # Carry forward from period 0 → period 1.
            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["user"].id,
            )
            db.session.commit()

            assert count == 2

            # Period 0 should have no projected transactions.
            remaining = db.session.query(Transaction).filter_by(
                pay_period_id=seed_periods[0].id,
            ).filter(
                Transaction.status.has(name="projected"),
            ).count()
            assert remaining == 0

            # Period 1 should now have the 2 moved transactions.
            moved = db.session.query(Transaction).filter_by(
                pay_period_id=seed_periods[1].id,
            ).all()
            assert len(moved) == 2
            assert {t.name for t in moved} == {"Groceries", "Gas"}
