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


# ---------------------------------------------------------------------------
# Fake objects for pure-function edge-case tests (no DB needed)
# ---------------------------------------------------------------------------

class FakeStatus:
    def __init__(self, name):
        self.name = name


class FakeType:
    def __init__(self, name):
        self.name = name


class FakePeriod:
    def __init__(self, id):
        self.id = id


class FakeTxn:
    def __init__(self, pay_period_id, status_name, type_name, estimated_amount):
        self.pay_period_id = pay_period_id
        self.status = FakeStatus(status_name)
        self.transaction_type = FakeType(type_name)
        self.estimated_amount = Decimal(str(estimated_amount))

    @property
    def is_income(self):
        return self.transaction_type and self.transaction_type.name == "income"

    @property
    def is_expense(self):
        return self.transaction_type and self.transaction_type.name == "expense"


class FakeTransfer:
    def __init__(self, pay_period_id, from_account_id, to_account_id, amount,
                 status_name="projected"):
        self.pay_period_id = pay_period_id
        self.from_account_id = from_account_id
        self.to_account_id = to_account_id
        self.amount = Decimal(str(amount))
        self.status = FakeStatus(status_name)


class TestBalanceCalculatorEdgeCases:
    """Pure-function edge-case tests — no DB fixtures needed."""

    def test_anchor_balance_none_defaults_to_zero(self):
        """anchor_balance=None → Decimal('0.00'), projected income still added."""
        periods = [FakePeriod(1)]
        txns = [FakeTxn(1, "projected", "income", "500.00")]

        balances = balance_calculator.calculate_balances(
            anchor_balance=None,
            anchor_period_id=1,
            periods=periods,
            transactions=txns,
        )

        assert balances[1] == Decimal("500.00")

    def test_pre_anchor_periods_excluded(self):
        """Periods before the anchor are not included in output."""
        periods = [FakePeriod(1), FakePeriod(2), FakePeriod(3), FakePeriod(4)]
        txns = [
            FakeTxn(1, "projected", "income", "100.00"),
            FakeTxn(2, "projected", "income", "200.00"),
            FakeTxn(3, "projected", "income", "300.00"),
            FakeTxn(4, "projected", "expense", "50.00"),
        ]

        balances = balance_calculator.calculate_balances(
            anchor_balance=Decimal("1000.00"),
            anchor_period_id=3,
            periods=periods,
            transactions=txns,
        )

        assert 1 not in balances
        assert 2 not in balances
        assert 3 in balances
        assert 4 in balances
        # Period 3 (anchor): 1000 + 300 = 1300
        assert balances[3] == Decimal("1300.00")
        # Period 4: 1300 - 50 = 1250
        assert balances[4] == Decimal("1250.00")

    def test_mixed_income_expense_post_anchor(self):
        """Post-anchor period with both income and expense rolls forward correctly."""
        periods = [FakePeriod(1), FakePeriod(2)]
        txns = [
            FakeTxn(2, "projected", "income", "2000.00"),
            FakeTxn(2, "projected", "expense", "750.00"),
        ]

        balances = balance_calculator.calculate_balances(
            anchor_balance=Decimal("500.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=txns,
        )

        # Period 1 (anchor, no txns): 500
        assert balances[1] == Decimal("500.00")
        # Period 2: 500 + 2000 - 750 = 1750
        assert balances[2] == Decimal("1750.00")

    def test_mixed_transactions_and_transfers(self):
        """Anchor period with a projected expense and outgoing transfer."""
        periods = [FakePeriod(1)]
        txns = [FakeTxn(1, "projected", "expense", "300.00")]
        xfers = [FakeTransfer(1, from_account_id=10, to_account_id=20, amount="200.00")]

        balances = balance_calculator.calculate_balances(
            anchor_balance=Decimal("1000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=txns,
            transfers=xfers,
            account_id=10,
        )

        # 1000 - 300 (expense) - 200 (outgoing transfer) = 500
        assert balances[1] == Decimal("500.00")

    def test_multiple_transfers_same_period(self):
        """Anchor period with 1 incoming + 1 outgoing transfer."""
        periods = [FakePeriod(1)]
        xfers = [
            FakeTransfer(1, from_account_id=20, to_account_id=10, amount="500.00"),
            FakeTransfer(1, from_account_id=10, to_account_id=30, amount="150.00"),
        ]

        balances = balance_calculator.calculate_balances(
            anchor_balance=Decimal("1000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=[],
            transfers=xfers,
            account_id=10,
        )

        # 1000 + 500 (incoming) - 150 (outgoing) = 1350
        assert balances[1] == Decimal("1350.00")

    def test_settled_transactions_excluded_post_anchor(self):
        """Post-anchor period: done/received transactions excluded, only projected counted."""
        periods = [FakePeriod(1), FakePeriod(2)]
        txns = [
            FakeTxn(2, "done", "expense", "999.00"),
            FakeTxn(2, "received", "income", "888.00"),
            FakeTxn(2, "projected", "expense", "100.00"),
        ]

        balances = balance_calculator.calculate_balances(
            anchor_balance=Decimal("1000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=txns,
        )

        # Period 1 (anchor, no txns): 1000
        assert balances[1] == Decimal("1000.00")
        # Period 2: 1000 - 100 (only projected expense counted) = 900
        assert balances[2] == Decimal("900.00")

    def test_cancelled_transfers_excluded(self):
        """Transfer with 'cancelled' status is excluded from balance calculation."""
        periods = [FakePeriod(1)]
        xfers = [
            FakeTransfer(1, from_account_id=10, to_account_id=20, amount="500.00",
                         status_name="cancelled"),
            FakeTransfer(1, from_account_id=10, to_account_id=30, amount="100.00",
                         status_name="projected"),
        ]

        balances = balance_calculator.calculate_balances(
            anchor_balance=Decimal("1000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=[],
            transfers=xfers,
            account_id=10,
        )

        # 1000 - 100 (only projected transfer) = 900; cancelled ignored
        assert balances[1] == Decimal("900.00")

    def test_empty_transactions_and_transfers(self):
        """Empty lists for both → balance equals anchor balance."""
        periods = [FakePeriod(1)]

        balances = balance_calculator.calculate_balances(
            anchor_balance=Decimal("2500.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=[],
            transfers=[],
            account_id=10,
        )

        assert balances[1] == Decimal("2500.00")

    def test_no_matching_anchor_period(self):
        """anchor_period_id doesn't match any period → empty OrderedDict."""
        periods = [FakePeriod(1), FakePeriod(2)]

        balances = balance_calculator.calculate_balances(
            anchor_balance=Decimal("1000.00"),
            anchor_period_id=99,
            periods=periods,
            transactions=[],
        )

        assert len(balances) == 0

    def test_five_period_rollforward_with_transfers(self):
        """5 periods with income, expenses, and transfers. Verify each balance."""
        periods = [FakePeriod(i) for i in range(1, 6)]
        account_id = 10

        txns = [
            # Period 1 (anchor): income 2000, expense 800
            FakeTxn(1, "projected", "income", "2000.00"),
            FakeTxn(1, "projected", "expense", "800.00"),
            # Period 2: income 2000, expense 600
            FakeTxn(2, "projected", "income", "2000.00"),
            FakeTxn(2, "projected", "expense", "600.00"),
            # Period 3: expense 1500 (no income)
            FakeTxn(3, "projected", "expense", "1500.00"),
            # Period 4: income 2000, expense 900
            FakeTxn(4, "projected", "income", "2000.00"),
            FakeTxn(4, "projected", "expense", "900.00"),
            # Period 5: income 2000
            FakeTxn(5, "projected", "income", "2000.00"),
        ]

        xfers = [
            # Period 2: outgoing 300 from account 10
            FakeTransfer(2, from_account_id=10, to_account_id=20, amount="300.00"),
            # Period 3: incoming 500 to account 10
            FakeTransfer(3, from_account_id=20, to_account_id=10, amount="500.00"),
            # Period 5: outgoing 1000 from account 10
            FakeTransfer(5, from_account_id=10, to_account_id=30, amount="1000.00"),
        ]

        balances = balance_calculator.calculate_balances(
            anchor_balance=Decimal("1000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=txns,
            transfers=xfers,
            account_id=account_id,
        )

        # Period 1: 1000 + 2000 - 800 = 2200
        assert balances[1] == Decimal("2200.00")
        # Period 2: 2200 + 2000 - 600 - 300 = 3300
        assert balances[2] == Decimal("3300.00")
        # Period 3: 3300 - 1500 + 500 = 2300
        assert balances[3] == Decimal("2300.00")
        # Period 4: 2300 + 2000 - 900 = 3400
        assert balances[4] == Decimal("3400.00")
        # Period 5: 3400 + 2000 - 1000 = 4400
        assert balances[5] == Decimal("4400.00")
