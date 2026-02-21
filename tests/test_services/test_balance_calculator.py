"""
Shekel Budget App — Balance Calculator Tests

Tests the pure-function balance calculator against the rules
defined in §4.9 of the requirements:
  - Anchor period uses only remaining (projected) items.
  - Post-anchor periods roll forward using effective amounts.
  - Credit status is excluded from checking balance.
"""

from decimal import Decimal

from app.models.transaction import Transaction
from app.models.ref import Status, TransactionType
from app.services import balance_calculator


class TestCalculateBalances:
    """Tests for the calculate_balances() function."""

    def test_single_period_projected_only(self, app, db, seed_user, seed_periods):
        """Anchor period with only projected items adds to balance."""
        with app.app_context():
            scenario = seed_user["scenario"]
            account = seed_user["account"]
            periods = seed_periods

            projected = db.session.query(Status).filter_by(name="projected").one()
            income_type = db.session.query(TransactionType).filter_by(name="income").one()
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

            # Add one income and one expense, both projected.
            txns = []
            inc = Transaction(
                pay_period_id=periods[0].id,
                scenario_id=scenario.id,
                status_id=projected.id,
                name="Paycheck",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=Decimal("2000.00"),
            )
            db.session.add(inc)
            txns.append(inc)

            exp = Transaction(
                pay_period_id=periods[0].id,
                scenario_id=scenario.id,
                status_id=projected.id,
                name="Rent",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("800.00"),
            )
            db.session.add(exp)
            txns.append(exp)
            db.session.flush()

            balances = balance_calculator.calculate_balances(
                anchor_balance=Decimal("1000.00"),
                anchor_period_id=periods[0].id,
                periods=periods,
                transactions=txns,
            )

            # anchor_balance + income - expenses = 1000 + 2000 - 800 = 2200
            assert balances[periods[0].id] == Decimal("2200.00")

    def test_done_items_excluded_from_anchor(self, app, db, seed_user, seed_periods):
        """Done items in the anchor period are already in the anchor balance."""
        with app.app_context():
            scenario = seed_user["scenario"]
            periods = seed_periods

            done = db.session.query(Status).filter_by(name="done").one()
            projected = db.session.query(Status).filter_by(name="projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

            txns = []
            # A 'done' expense — already reflected in anchor, should be skipped.
            done_exp = Transaction(
                pay_period_id=periods[0].id,
                scenario_id=scenario.id,
                status_id=done.id,
                name="Already Paid",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
                actual_amount=Decimal("480.00"),
            )
            db.session.add(done_exp)
            txns.append(done_exp)

            # A projected expense — NOT yet in anchor, should be subtracted.
            proj_exp = Transaction(
                pay_period_id=periods[0].id,
                scenario_id=scenario.id,
                status_id=projected.id,
                name="Upcoming",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("200.00"),
            )
            db.session.add(proj_exp)
            txns.append(proj_exp)
            db.session.flush()

            balances = balance_calculator.calculate_balances(
                anchor_balance=Decimal("1000.00"),
                anchor_period_id=periods[0].id,
                periods=periods,
                transactions=txns,
            )

            # Only the projected expense is subtracted: 1000 - 200 = 800.
            assert balances[periods[0].id] == Decimal("800.00")

    def test_credit_excluded_from_balance(self, app, db, seed_user, seed_periods):
        """Credit-status transactions do not affect checking balance."""
        with app.app_context():
            scenario = seed_user["scenario"]
            periods = seed_periods

            credit = db.session.query(Status).filter_by(name="credit").one()
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

            txns = []
            credit_exp = Transaction(
                pay_period_id=periods[0].id,
                scenario_id=scenario.id,
                status_id=credit.id,
                name="CC Purchase",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("300.00"),
            )
            db.session.add(credit_exp)
            txns.append(credit_exp)
            db.session.flush()

            balances = balance_calculator.calculate_balances(
                anchor_balance=Decimal("1000.00"),
                anchor_period_id=periods[0].id,
                periods=periods,
                transactions=txns,
            )

            # Credit excluded — balance unchanged.
            assert balances[periods[0].id] == Decimal("1000.00")

    def test_multi_period_roll_forward(self, app, db, seed_user, seed_periods):
        """Balances roll forward correctly across multiple periods."""
        with app.app_context():
            scenario = seed_user["scenario"]
            periods = seed_periods

            projected = db.session.query(Status).filter_by(name="projected").one()
            income_type = db.session.query(TransactionType).filter_by(name="income").one()
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

            txns = []
            # Period 0: income 2000, expense 800.
            for name, type_id, amount, period in [
                ("Pay1", income_type.id, "2000.00", periods[0]),
                ("Rent", expense_type.id, "800.00", periods[0]),
                ("Pay2", income_type.id, "2000.00", periods[1]),
                ("Bills", expense_type.id, "600.00", periods[1]),
            ]:
                cat_id = seed_user["categories"]["Salary"].id if "Pay" in name else seed_user["categories"]["Rent"].id
                t = Transaction(
                    pay_period_id=period.id,
                    scenario_id=scenario.id,
                    status_id=projected.id,
                    name=name,
                    category_id=cat_id,
                    transaction_type_id=type_id,
                    estimated_amount=Decimal(amount),
                )
                db.session.add(t)
                txns.append(t)
            db.session.flush()

            balances = balance_calculator.calculate_balances(
                anchor_balance=Decimal("500.00"),
                anchor_period_id=periods[0].id,
                periods=periods,
                transactions=txns,
            )

            # Period 0: 500 + 2000 - 800 = 1700
            assert balances[periods[0].id] == Decimal("1700.00")
            # Period 1: 1700 + 2000 - 600 = 3100
            assert balances[periods[1].id] == Decimal("3100.00")
