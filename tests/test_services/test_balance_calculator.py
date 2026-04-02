"""
Shekel Budget App -- Balance Calculator Tests

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
from app import ref_cache
from app.enums import StatusEnum


class TestCalculateBalances:
    """Tests for the calculate_balances() function."""

    def test_single_period_projected_only(self, app, db, seed_user, seed_periods):
        """Anchor period with only projected items adds to balance."""
        with app.app_context():
            scenario = seed_user["scenario"]
            account = seed_user["account"]
            periods = seed_periods

            projected = db.session.query(Status).filter_by(name="Projected").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            # Add one income and one expense, both projected.
            txns = []
            inc = Transaction(
                pay_period_id=periods[0].id,
                scenario_id=scenario.id,
                account_id=account.id,
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
                account_id=account.id,
                status_id=projected.id,
                name="Rent",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("800.00"),
            )
            db.session.add(exp)
            txns.append(exp)
            db.session.flush()

            balances, _ = balance_calculator.calculate_balances(
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
            account = seed_user["account"]
            periods = seed_periods

            done = db.session.query(Status).filter_by(name="Paid").one()
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            txns = []
            # A 'done' expense -- already reflected in anchor, should be skipped.
            done_exp = Transaction(
                pay_period_id=periods[0].id,
                scenario_id=scenario.id,
                account_id=account.id,
                status_id=done.id,
                name="Already Paid",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
                actual_amount=Decimal("480.00"),
            )
            db.session.add(done_exp)
            txns.append(done_exp)

            # A projected expense -- NOT yet in anchor, should be subtracted.
            proj_exp = Transaction(
                pay_period_id=periods[0].id,
                scenario_id=scenario.id,
                account_id=account.id,
                status_id=projected.id,
                name="Upcoming",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("200.00"),
            )
            db.session.add(proj_exp)
            txns.append(proj_exp)
            db.session.flush()

            balances, _ = balance_calculator.calculate_balances(
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
            account = seed_user["account"]
            periods = seed_periods

            credit = db.session.query(Status).filter_by(name="Credit").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            txns = []
            credit_exp = Transaction(
                pay_period_id=periods[0].id,
                scenario_id=scenario.id,
                account_id=account.id,
                status_id=credit.id,
                name="CC Purchase",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("300.00"),
            )
            db.session.add(credit_exp)
            txns.append(credit_exp)
            db.session.flush()

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("1000.00"),
                anchor_period_id=periods[0].id,
                periods=periods,
                transactions=txns,
            )

            # Credit excluded -- balance unchanged.
            assert balances[periods[0].id] == Decimal("1000.00")

    def test_multi_period_roll_forward(self, app, db, seed_user, seed_periods):
        """Balances roll forward correctly across multiple periods."""
        with app.app_context():
            scenario = seed_user["scenario"]
            account = seed_user["account"]
            periods = seed_periods

            projected = db.session.query(Status).filter_by(name="Projected").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

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
                    account_id=account.id,
                    status_id=projected.id,
                    name=name,
                    category_id=cat_id,
                    transaction_type_id=type_id,
                    estimated_amount=Decimal(amount),
                )
                db.session.add(t)
                txns.append(t)
            db.session.flush()

            balances, _ = balance_calculator.calculate_balances(
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
    """Fake status object mimicking the ref.Status model.

    Boolean attributes mirror the database columns used by the balance
    calculator for status-based filtering.
    """
    def __init__(self, name, is_settled=False, is_immutable=False,
                 excludes_from_balance=False):
        self.name = name
        self.is_settled = is_settled
        self.is_immutable = is_immutable
        self.excludes_from_balance = excludes_from_balance


class FakeType:
    def __init__(self, name):
        self.name = name


class FakePeriod:
    def __init__(self, id):
        self.id = id


# Status configuration lookup: maps status display names to their boolean
# attributes (is_settled, is_immutable, excludes_from_balance).  Mirrors
# the seed data in scripts/seed_ref_tables.py.
_STATUS_ATTRS = {
    "Projected":  {"is_settled": False, "is_immutable": False, "excludes_from_balance": False},
    "Paid":       {"is_settled": True,  "is_immutable": True,  "excludes_from_balance": False},
    "Received":   {"is_settled": True,  "is_immutable": True,  "excludes_from_balance": False},
    "Credit":     {"is_settled": False, "is_immutable": True,  "excludes_from_balance": True},
    "Cancelled":  {"is_settled": False, "is_immutable": True,  "excludes_from_balance": True},
    "Settled":    {"is_settled": True,  "is_immutable": True,  "excludes_from_balance": False},
}


class FakeTxn:
    """Fake transaction object for pure-function tests (no DB needed).

    Provides status_id (integer) and status (FakeStatus with boolean columns)
    matching the interface expected by the balance calculator, including the
    effective_amount property.
    """
    def __init__(self, pay_period_id, status_name, type_name, estimated_amount,
                 transfer_id=None, actual_amount=None, is_deleted=False):
        self.pay_period_id = pay_period_id
        attrs = _STATUS_ATTRS[status_name]
        self.status = FakeStatus(status_name, **attrs)
        self.transaction_type = FakeType(type_name)
        self.estimated_amount = Decimal(str(estimated_amount))
        self.actual_amount = (
            Decimal(str(actual_amount)) if actual_amount is not None else None
        )
        self.transfer_id = transfer_id
        self.is_deleted = is_deleted

        # Resolve the integer status_id from ref_cache.  The cache is
        # initialized by the session-scoped db fixture in conftest.py.
        _name_to_enum = {e.value: e for e in StatusEnum}
        self.status_id = ref_cache.status_id(_name_to_enum[status_name])

    @property
    def effective_amount(self):
        """Mirror Transaction.effective_amount for fake objects."""
        if self.is_deleted:
            return Decimal("0")
        if self.status and self.status.excludes_from_balance:
            return Decimal("0")
        # Use `is not None` -- zero is a valid actual amount.
        return self.actual_amount if self.actual_amount is not None else self.estimated_amount

    @property
    def is_income(self):
        """True if this transaction represents income."""
        return self.transaction_type and self.transaction_type.name == "Income"

    @property
    def is_expense(self):
        """True if this transaction represents an expense."""
        return self.transaction_type and self.transaction_type.name == "Expense"


class TestBalanceCalculatorEdgeCases:
    """Pure-function edge-case tests -- no DB fixtures needed."""

    def test_anchor_balance_none_defaults_to_zero(self):
        """anchor_balance=None → Decimal('0.00'), projected income still added."""
        periods = [FakePeriod(1)]
        txns = [FakeTxn(1, "Projected", "Income", "500.00")]

        balances, _ = balance_calculator.calculate_balances(
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
            FakeTxn(1, "Projected", "Income", "100.00"),
            FakeTxn(2, "Projected", "Income", "200.00"),
            FakeTxn(3, "Projected", "Income", "300.00"),
            FakeTxn(4, "Projected", "Expense", "50.00"),
        ]

        balances, _ = balance_calculator.calculate_balances(
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
            FakeTxn(2, "Projected", "Income", "2000.00"),
            FakeTxn(2, "Projected", "Expense", "750.00"),
        ]

        balances, _ = balance_calculator.calculate_balances(
            anchor_balance=Decimal("500.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=txns,
        )

        # Period 1 (anchor, no txns): 500
        assert balances[1] == Decimal("500.00")
        # Period 2: 500 + 2000 - 750 = 1750
        assert balances[2] == Decimal("1750.00")

    def test_mixed_transactions_and_shadow_expense(self):
        """Anchor period with a regular expense and a shadow expense (transfer out)."""
        periods = [FakePeriod(1)]
        txns = [
            FakeTxn(1, "Projected", "Expense", "300.00"),
            # Shadow expense from a transfer (outgoing $200).
            FakeTxn(1, "Projected", "Expense", "200.00", transfer_id=1),
        ]

        balances, _ = balance_calculator.calculate_balances(
            anchor_balance=Decimal("1000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=txns,
        )

        # 1000 - 300 (expense) - 200 (shadow expense) = 500
        assert balances[1] == Decimal("500.00")

    def test_shadow_income_and_expense_same_period(self):
        """Anchor period with shadow income + shadow expense (transfer in + out)."""
        periods = [FakePeriod(1)]
        txns = [
            # Shadow income: $500 transfer into this account.
            FakeTxn(1, "Projected", "Income", "500.00", transfer_id=1),
            # Shadow expense: $150 transfer out of this account.
            FakeTxn(1, "Projected", "Expense", "150.00", transfer_id=2),
        ]

        balances, _ = balance_calculator.calculate_balances(
            anchor_balance=Decimal("1000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=txns,
        )

        # 1000 + 500 (shadow income) - 150 (shadow expense) = 1350
        assert balances[1] == Decimal("1350.00")

    def test_settled_transactions_excluded_post_anchor(self):
        """Post-anchor period: done/received transactions excluded, only projected counted."""
        periods = [FakePeriod(1), FakePeriod(2)]
        txns = [
            FakeTxn(2, "Paid", "Expense", "999.00"),
            FakeTxn(2, "Received", "Income", "888.00"),
            FakeTxn(2, "Projected", "Expense", "100.00"),
        ]

        balances, _ = balance_calculator.calculate_balances(
            anchor_balance=Decimal("1000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=txns,
        )

        # Period 1 (anchor, no txns): 1000
        assert balances[1] == Decimal("1000.00")
        # Period 2: 1000 - 100 (only projected expense counted) = 900
        assert balances[2] == Decimal("900.00")

    def test_cancelled_shadow_excluded(self):
        """Cancelled shadow transaction is excluded from balance calculation."""
        periods = [FakePeriod(1)]
        txns = [
            # Cancelled shadow expense -- should be ignored.
            FakeTxn(1, "Cancelled", "Expense", "500.00", transfer_id=1),
            # Projected shadow expense -- should be counted.
            FakeTxn(1, "Projected", "Expense", "100.00", transfer_id=2),
        ]

        balances, _ = balance_calculator.calculate_balances(
            anchor_balance=Decimal("1000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=txns,
        )

        # 1000 - 100 (only projected shadow) = 900; cancelled ignored
        assert balances[1] == Decimal("900.00")

    def test_empty_transactions(self):
        """Empty transaction list -> balance equals anchor balance."""
        periods = [FakePeriod(1)]

        balances, _ = balance_calculator.calculate_balances(
            anchor_balance=Decimal("2500.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=[],
        )

        assert balances[1] == Decimal("2500.00")

    def test_no_matching_anchor_period(self):
        """anchor_period_id doesn't match any period → empty OrderedDict."""
        periods = [FakePeriod(1), FakePeriod(2)]

        balances, _ = balance_calculator.calculate_balances(
            anchor_balance=Decimal("1000.00"),
            anchor_period_id=99,
            periods=periods,
            transactions=[],
        )

        assert len(balances) == 0

    def test_five_period_rollforward_with_shadows(self):
        """5 periods with income, expenses, and shadow transactions. Verify each balance."""
        periods = [FakePeriod(i) for i in range(1, 6)]

        txns = [
            # Period 1 (anchor): income 2000, expense 800
            FakeTxn(1, "Projected", "Income", "2000.00"),
            FakeTxn(1, "Projected", "Expense", "800.00"),
            # Period 2: income 2000, expense 600, shadow expense 300 (transfer out)
            FakeTxn(2, "Projected", "Income", "2000.00"),
            FakeTxn(2, "Projected", "Expense", "600.00"),
            FakeTxn(2, "Projected", "Expense", "300.00", transfer_id=1),
            # Period 3: expense 1500, shadow income 500 (transfer in)
            FakeTxn(3, "Projected", "Expense", "1500.00"),
            FakeTxn(3, "Projected", "Income", "500.00", transfer_id=2),
            # Period 4: income 2000, expense 900
            FakeTxn(4, "Projected", "Income", "2000.00"),
            FakeTxn(4, "Projected", "Expense", "900.00"),
            # Period 5: income 2000, shadow expense 1000 (transfer out)
            FakeTxn(5, "Projected", "Income", "2000.00"),
            FakeTxn(5, "Projected", "Expense", "1000.00", transfer_id=3),
        ]

        balances, _ = balance_calculator.calculate_balances(
            anchor_balance=Decimal("1000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=txns,
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

    def test_no_transfers_parameter_accepted(self):
        """calculate_balances no longer accepts a transfers keyword argument."""
        import pytest
        periods = [FakePeriod(1)]
        with pytest.raises(TypeError):
            balance_calculator.calculate_balances(
                anchor_balance=Decimal("1000.00"),
                anchor_period_id=1,
                periods=periods,
                transactions=[],
                transfers=[],
            )


# -------------------------------------------------------------------
# FIN tests -- Financial accuracy (penny-level precision)
# -------------------------------------------------------------------


class TestBalanceCalculatorFIN:
    """Financial accuracy tests for calculate_balances.

    Verify penny-level accuracy over production-scale projection
    windows using independent Decimal oracles that never call
    any function from balance_calculator.py.
    """

    def test_52_period_penny_accuracy(  # pylint: disable=too-many-statements
        self,
    ):
        """Verify calculate_balances across 52 periods.

        Uses a deterministic dataset covering scenarios S1-S10
        with mixed statuses (projected, done, cancelled, credit,
        received) and an independent Decimal oracle. Proves
        cumulative accuracy does not drift over a full 2-year
        projection window.
        """
        # 52 synthetic periods with integer IDs 0-51.
        periods = [FakePeriod(i) for i in range(52)]
        # Non-round anchor to expose truncation bugs.
        anchor_balance = Decimal("3245.67")

        txns = []

        # --- Period 0 (S10 + S1): anchor with standard mix ---
        # Projected paycheck.
        txns.append(
            FakeTxn(0, "Projected", "Income", "2500.00")
        )
        # Projected rent.
        txns.append(
            FakeTxn(0, "Projected", "Expense", "850.00")
        )
        # Projected utilities.
        txns.append(
            FakeTxn(0, "Projected", "Expense", "125.50")
        )
        # Projected groceries.
        txns.append(
            FakeTxn(0, "Projected", "Expense", "200.00")
        )

        # --- Period 1 (S2): done expense -- excluded ---
        # Projected paycheck.
        txns.append(
            FakeTxn(1, "Projected", "Income", "2500.00")
        )
        # Projected rent.
        txns.append(
            FakeTxn(1, "Projected", "Expense", "850.00")
        )
        # Done expense (actual would be 275.50); service
        # excludes done entirely -- never reads actual_amount.
        txns.append(
            FakeTxn(1, "Paid", "Expense", "300.00")
        )

        # --- Period 2 (S3): cancelled expense -- excluded ---
        # Projected paycheck.
        txns.append(
            FakeTxn(2, "Projected", "Income", "2500.00")
        )
        # Projected rent.
        txns.append(
            FakeTxn(2, "Projected", "Expense", "850.00")
        )
        # Cancelled: would subtract 500 if counted.
        txns.append(
            FakeTxn(2, "Cancelled", "Expense", "500.00")
        )

        # --- Period 3 (S4): credit expense -- excluded ---
        # Projected paycheck.
        txns.append(
            FakeTxn(3, "Projected", "Income", "2500.00")
        )
        # Projected rent.
        txns.append(
            FakeTxn(3, "Projected", "Expense", "850.00")
        )
        # Credit: on credit card, not checking -- excluded.
        txns.append(
            FakeTxn(3, "Credit", "Expense", "450.00")
        )

        # --- Period 4 (S5): only cancelled + credit ---
        # No projected items; balance carries forward unchanged.
        txns.append(
            FakeTxn(4, "Cancelled", "Expense", "600.00")
        )
        txns.append(
            FakeTxn(4, "Credit", "Expense", "350.00")
        )

        # --- Period 5 (S6): done income + done expense ---
        # Both excluded; only the projected expense counts.
        txns.append(
            FakeTxn(5, "Paid", "Income", "2500.00")
        )
        txns.append(
            FakeTxn(5, "Paid", "Expense", "850.00")
        )
        # Only projected item in this period.
        txns.append(
            FakeTxn(5, "Projected", "Expense", "150.00")
        )

        # --- Period 6 (S7): received income -- excluded ---
        # Projected paycheck.
        txns.append(
            FakeTxn(6, "Projected", "Income", "2500.00")
        )
        # Projected rent.
        txns.append(
            FakeTxn(6, "Projected", "Expense", "850.00")
        )
        # Received: already settled -- excluded.
        txns.append(
            FakeTxn(6, "Received", "Income", "100.00")
        )

        # --- Period 7 (S8): zero-amount projected expense ---
        # Projected paycheck.
        txns.append(
            FakeTxn(7, "Projected", "Income", "2500.00")
        )
        # Projected rent.
        txns.append(
            FakeTxn(7, "Projected", "Expense", "850.00")
        )
        # Zero expense: included in sum but adds nothing.
        txns.append(
            FakeTxn(7, "Projected", "Expense", "0.00")
        )

        # --- Period 8 (S9): fractional-cent expenses ---
        # Projected paycheck.
        txns.append(
            FakeTxn(8, "Projected", "Income", "2500.00")
        )
        # Three 33.33 expenses; sum = 99.99, NOT 100.00.
        txns.append(
            FakeTxn(8, "Projected", "Expense", "33.33")
        )
        txns.append(
            FakeTxn(8, "Projected", "Expense", "33.33")
        )
        txns.append(
            FakeTxn(8, "Projected", "Expense", "33.33")
        )

        # --- Period 9 (S2 repeat): done income -- excluded ---
        # Projected paycheck.
        txns.append(
            FakeTxn(9, "Projected", "Income", "2500.00")
        )
        # Projected rent.
        txns.append(
            FakeTxn(9, "Projected", "Expense", "850.00")
        )
        # Done income (actual would be 520.00) -- excluded.
        txns.append(
            FakeTxn(9, "Paid", "Income", "500.00")
        )

        # --- Period 10 (S3 repeat): cancelled expense ---
        # Projected paycheck.
        txns.append(
            FakeTxn(10, "Projected", "Income", "2500.00")
        )
        # Projected rent.
        txns.append(
            FakeTxn(10, "Projected", "Expense", "850.00")
        )
        # Cancelled expense -- excluded.
        txns.append(
            FakeTxn(10, "Cancelled", "Expense", "200.00")
        )

        # --- Period 11 (S4 repeat): credit expense ---
        # Projected paycheck.
        txns.append(
            FakeTxn(11, "Projected", "Income", "2500.00")
        )
        # Projected rent.
        txns.append(
            FakeTxn(11, "Projected", "Expense", "850.00")
        )
        # Credit expense -- excluded.
        txns.append(
            FakeTxn(11, "Credit", "Expense", "175.00")
        )

        # --- Periods 12-51 (S1): standard mix each period ---
        # Each: income 2500 - rent 850 - utilities 125.50
        #   - groceries 200 = net +1324.50.
        for p in range(12, 52):
            # Paycheck.
            txns.append(
                FakeTxn(p, "Projected", "Income", "2500.00")
            )
            # Rent.
            txns.append(
                FakeTxn(p, "Projected", "Expense", "850.00")
            )
            # Utilities.
            txns.append(
                FakeTxn(p, "Projected", "Expense", "125.50")
            )
            # Groceries.
            txns.append(
                FakeTxn(p, "Projected", "Expense", "200.00")
            )

        # -------------------------------------------------------
        # Oracle: independent balance computation.
        # Only "Projected" status contributes. Income added,
        # expense subtracted. No rounding applied (service does
        # not quantize). Does NOT call any balance_calculator
        # function.
        # -------------------------------------------------------
        oracle_expected = {}
        running = anchor_balance
        for i in range(52):
            period_inc = Decimal("0.00")
            period_exp = Decimal("0.00")
            for txn in txns:
                if txn.pay_period_id != i:
                    continue
                # Exclude all non-projected statuses.
                if txn.status.name != "Projected":
                    continue
                # Classify by raw attribute, not service prop.
                if txn.transaction_type.name == "Income":
                    period_inc += txn.estimated_amount
                elif txn.transaction_type.name == "Expense":
                    period_exp += txn.estimated_amount
            running = running + period_inc - period_exp
            oracle_expected[i] = running

        # --- Call the service under test ---
        result, _ = balance_calculator.calculate_balances(
            anchor_balance=anchor_balance,
            anchor_period_id=0,
            periods=periods,
            transactions=txns,
        )

        # Verify all 52 periods returned.
        assert len(result) == 52, (
            f"Expected 52 balances, got {len(result)}"
        )

        # Assert each period individually.
        for i, period in enumerate(periods):
            assert result[period.id] == oracle_expected[period.id], (
                f"Period {i} (id={period.id}): "
                f"expected {oracle_expected[period.id]}, "
                f"got {result[period.id]}, "
                f"diff="
                f"{result[period.id] - oracle_expected[period.id]}"
            )

        # Cumulative cross-check: anchor + total projected net
        # must equal the final period balance. Uses a separate
        # summation path to catch accumulation drift.
        total_net = Decimal("0.00")
        for txn in txns:
            if txn.status.name != "Projected":
                continue
            if txn.transaction_type.name == "Income":
                total_net += txn.estimated_amount
            elif txn.transaction_type.name == "Expense":
                total_net -= txn.estimated_amount
        cumulative_expected = anchor_balance + total_net
        assert result[periods[51].id] == cumulative_expected, (
            f"Cumulative check: "
            f"expected {cumulative_expected}, "
            f"got {result[periods[51].id]}, "
            f"diff="
            f"{result[periods[51].id] - cumulative_expected}"
        )

    def test_negative_anchor_balance_overdraft(self):
        """Verify calculate_balances handles negative anchor.

        Uses anchor_balance=-500.00 with 3 periods. Each period
        has income of 2500.00 and two expenses of 850.00. Proves
        income covers the overdraft and balances accumulate
        correctly from a negative starting point.
        """
        periods = [FakePeriod(i) for i in range(3)]
        anchor_balance = Decimal("-500.00")

        txns = []
        for p in range(3):
            # Income: 2500.00 each period.
            txns.append(
                FakeTxn(p, "Projected", "Income", "2500.00")
            )
            # Expense 1: 850.00 each period.
            txns.append(
                FakeTxn(p, "Projected", "Expense", "850.00")
            )
            # Expense 2: 850.00 each period.
            txns.append(
                FakeTxn(p, "Projected", "Expense", "850.00")
            )

        result, _ = balance_calculator.calculate_balances(
            anchor_balance=anchor_balance,
            anchor_period_id=0,
            periods=periods,
            transactions=txns,
        )

        # Period 0: -500 + 2500 - 850 - 850 = 300.00
        # (positive -- overdraft covered by income)
        assert result[0] == Decimal("300.00"), (
            f"Period 0: expected 300.00, got {result[0]}, "
            f"diff={result[0] - Decimal('300.00')}"
        )
        # Period 1: 300 + 2500 - 850 - 850 = 1100.00
        assert result[1] == Decimal("1100.00"), (
            f"Period 1: expected 1100.00, got {result[1]}, "
            f"diff={result[1] - Decimal('1100.00')}"
        )
        # Period 2: 1100 + 2500 - 850 - 850 = 1900.00
        assert result[2] == Decimal("1900.00"), (
            f"Period 2: expected 1900.00, got {result[2]}, "
            f"diff={result[2] - Decimal('1900.00')}"
        )

    def test_large_values_no_overflow(self):
        """Verify calculate_balances with large values near DB limits.

        Uses anchor_balance=999999.99 with 3 periods. Income of
        50000.00 and expense of 49999.99 per period (net +0.01).
        Tests precision near Numeric(12,2) boundary without
        overflow.
        """
        periods = [FakePeriod(i) for i in range(3)]
        anchor_balance = Decimal("999999.99")

        txns = []
        for p in range(3):
            # Large income: 50000.00.
            txns.append(
                FakeTxn(p, "Projected", "Income", "50000.00")
            )
            # Large expense: 49999.99 (net +0.01 per period).
            txns.append(
                FakeTxn(p, "Projected", "Expense", "49999.99")
            )

        result, _ = balance_calculator.calculate_balances(
            anchor_balance=anchor_balance,
            anchor_period_id=0,
            periods=periods,
            transactions=txns,
        )

        # Period 0: 999999.99 + 50000.00 - 49999.99 = 1000000.00
        assert result[0] == Decimal("1000000.00"), (
            f"Period 0: expected 1000000.00, "
            f"got {result[0]}, "
            f"diff={result[0] - Decimal('1000000.00')}"
        )
        # Period 1: 1000000.00 + 0.01 = 1000000.01
        assert result[1] == Decimal("1000000.01"), (
            f"Period 1: expected 1000000.01, "
            f"got {result[1]}, "
            f"diff={result[1] - Decimal('1000000.01')}"
        )
        # Period 2: 1000000.01 + 0.01 = 1000000.02
        assert result[2] == Decimal("1000000.02"), (
            f"Period 2: expected 1000000.02, "
            f"got {result[2]}, "
            f"diff={result[2] - Decimal('1000000.02')}"
        )

    def test_idempotent_same_inputs_same_outputs(self):
        """Verify calculate_balances is idempotent and correct.

        Calls the function twice with identical inputs (5 periods,
        standard transactions). Proves:
        1. Each period's balance matches an independent oracle.
        2. Repeated calls produce exactly the same Decimal results
           with no hidden state mutation.
        """
        periods = [FakePeriod(i) for i in range(5)]
        anchor_balance = Decimal("1000.00")

        txns = []
        for p in range(5):
            # Standard mix: income 2500, expense 850.
            txns.append(
                FakeTxn(p, "Projected", "Income", "2500.00")
            )
            txns.append(
                FakeTxn(p, "Projected", "Expense", "850.00")
            )

        # Independent expected values.
        # anchor=1000, net per period = 2500 - 850 = +1650
        # P0: 1000+1650=2650, P1: 4300, P2: 5950,
        # P3: 7600, P4: 9250
        oracle = {
            0: Decimal("2650.00"),
            1: Decimal("4300.00"),
            2: Decimal("5950.00"),
            3: Decimal("7600.00"),
            4: Decimal("9250.00"),
        }

        # First call.
        result_1, _ = balance_calculator.calculate_balances(
            anchor_balance=anchor_balance,
            anchor_period_id=0,
            periods=periods,
            transactions=txns,
        )

        # Correctness: verify against independent oracle.
        for i, period in enumerate(periods):
            assert result_1[period.id] == oracle[period.id], (
                f"Period {i} (id={period.id}): "
                f"expected {oracle[period.id]}, "
                f"got {result_1[period.id]}"
            )

        # Second call with identical inputs.
        result_2, _ = balance_calculator.calculate_balances(
            anchor_balance=anchor_balance,
            anchor_period_id=0,
            periods=periods,
            transactions=txns,
        )

        # Idempotency: full dict equality.
        assert result_1 == result_2, (
            "Idempotency violated: result_1 != result_2"
        )
        # Per-period equality with descriptive messages.
        for i, period in enumerate(periods):
            assert result_1[period.id] == result_2[period.id], (
                f"Period {i} (id={period.id}): "
                f"call 1={result_1[period.id]}, "
                f"call 2={result_2[period.id]}"
            )

    def test_zero_estimated_amount_does_not_affect_balance(self):
        """Verify a zero-amount expense does not alter balance.

        Uses 3 periods; period 1 has an extra expense with
        estimated_amount=0.00. Proves the zero amount is processed
        (not skipped) and produces the same net effect as a period
        without it.
        """
        periods = [FakePeriod(i) for i in range(3)]
        anchor_balance = Decimal("1000.00")

        txns = [
            # --- Period 0: income 2500, expense 850 ---
            FakeTxn(0, "Projected", "Income", "2500.00"),
            FakeTxn(0, "Projected", "Expense", "850.00"),
            # --- Period 1: same + zero-amount expense ---
            FakeTxn(1, "Projected", "Income", "2500.00"),
            FakeTxn(1, "Projected", "Expense", "850.00"),
            # Zero expense: included but contributes nothing.
            FakeTxn(1, "Projected", "Expense", "0.00"),
            # --- Period 2: same as period 0 (no zero exp) ---
            FakeTxn(2, "Projected", "Income", "2500.00"),
            FakeTxn(2, "Projected", "Expense", "850.00"),
        ]

        result, _ = balance_calculator.calculate_balances(
            anchor_balance=anchor_balance,
            anchor_period_id=0,
            periods=periods,
            transactions=txns,
        )

        # Period 0: 1000 + 2500 - 850 = 2650.00
        assert result[0] == Decimal("2650.00"), (
            f"Period 0: expected 2650.00, got {result[0]}, "
            f"diff={result[0] - Decimal('2650.00')}"
        )
        # Period 1: 2650 + 2500 - 850 - 0.00 = 4300.00
        # Net is +1650 -- same as without the zero expense.
        assert result[1] == Decimal("4300.00"), (
            f"Period 1: expected 4300.00, got {result[1]}, "
            f"diff={result[1] - Decimal('4300.00')}"
        )
        # Period 2: 4300 + 2500 - 850 = 5950.00
        # Confirms period 2 net (+1650) matches period 1 net.
        assert result[2] == Decimal("5950.00"), (
            f"Period 2: expected 5950.00, got {result[2]}, "
            f"diff={result[2] - Decimal('5950.00')}"
        )

    def test_received_status_handling(self):
        """Verify received-status transactions are excluded.

        Uses 3 periods, each with one received transaction.
        Received has is_settled=True and must not affect
        the projected balance. Proves zero net effect across
        all periods.
        """
        periods = [FakePeriod(i) for i in range(3)]
        anchor_balance = Decimal("1000.00")

        txns = [
            # Period 0: received income -- excluded from anchor.
            FakeTxn(0, "Received", "Income", "5000.00"),
            # Period 1: received expense -- excluded post-anchor.
            FakeTxn(1, "Received", "Expense", "500.00"),
            # Period 2: received income -- excluded post-anchor.
            FakeTxn(2, "Received", "Income", "3000.00"),
        ]

        result, _ = balance_calculator.calculate_balances(
            anchor_balance=anchor_balance,
            anchor_period_id=0,
            periods=periods,
            transactions=txns,
        )

        # All transactions received -- every period equals anchor.
        # Period 0: 1000 + 0 - 0 = 1000.00
        assert result[0] == Decimal("1000.00"), (
            f"Period 0: expected 1000.00, got {result[0]}, "
            f"diff={result[0] - Decimal('1000.00')}"
        )
        # Period 1: 1000 + 0 - 0 = 1000.00
        assert result[1] == Decimal("1000.00"), (
            f"Period 1: expected 1000.00, got {result[1]}, "
            f"diff={result[1] - Decimal('1000.00')}"
        )
        # Period 2: 1000 + 0 - 0 = 1000.00
        assert result[2] == Decimal("1000.00"), (
            f"Period 2: expected 1000.00, got {result[2]}, "
            f"diff={result[2] - Decimal('1000.00')}"
        )


# -------------------------------------------------------------------
# Negative-path and boundary-condition tests
# -------------------------------------------------------------------


class TestNegativePaths:
    """Negative-path and boundary-condition tests for calculate_balances.

    Verifies correct status filtering, zero-amount handling, and
    comprehensive status interaction in the balance calculator.
    """

    def test_zero_estimated_amount_no_balance_effect(self):
        """Zero-amount projected transactions must not affect the balance.

        Input: 3 periods, anchor=1500.00. Each period has one real transaction
        and one zero-amount transaction.
        Expected: Zero-amount transactions are processed but contribute nothing.
        Why: A bug treating zero as None or skipping it differently could silently
        corrupt balances.
        """
        periods = [FakePeriod(1), FakePeriod(2), FakePeriod(3)]
        txns = [
            # Period 1 (anchor): income 2000, zero expense
            FakeTxn(1, "Projected", "Income", "2000.00"),
            FakeTxn(1, "Projected", "Expense", "0.00"),  # zero -- must not affect balance
            # Period 2: expense 500, zero expense
            FakeTxn(2, "Projected", "Expense", "500.00"),
            FakeTxn(2, "Projected", "Expense", "0.00"),  # zero -- must not affect balance
            # Period 3: expense 300 only
            FakeTxn(3, "Projected", "Expense", "300.00"),
        ]

        balances, _ = balance_calculator.calculate_balances(
            anchor_balance=Decimal("1500.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=txns,
        )

        # Period 1 (anchor): 1500 + 2000 - 0 = 3500.00
        assert balances[1] == Decimal("3500.00")
        # Period 2: 3500 - 500 - 0 = 3000.00 (zero expense has no effect)
        assert balances[2] == Decimal("3000.00")
        # Period 3: 3000 - 300 = 2700.00
        assert balances[3] == Decimal("2700.00")

    def test_received_status_excluded_from_remaining(self):
        """Received income must NOT be added to the anchor balance.

        Input: 2 periods, anchor=2000.00. Anchor has a received income (already
        settled in the anchor balance) and a projected expense.
        Expected: Only projected items affect the balance. The received income
        is already reflected in the anchor balance.
        Why: If received income is double-counted, every downstream period shows
        an inflated balance, and the user budgets against money they already spent.
        """
        periods = [FakePeriod(1), FakePeriod(2)]
        txns = [
            # Anchor: received income -- already settled, must be excluded.
            # actual_amount would be on the real ORM object but the balance
            # calculator never reads it; it skips the entire txn (status_id != projected_id).
            FakeTxn(1, "Received", "Income", "2500.00"),
            # Anchor: projected expense -- included in remaining calculation.
            FakeTxn(1, "Projected", "Expense", "800.00"),
            # Period 2: projected expense.
            FakeTxn(2, "Projected", "Expense", "600.00"),
        ]

        balances, _ = balance_calculator.calculate_balances(
            anchor_balance=Decimal("2000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=txns,
        )

        # Anchor: 2000 + 0(received excluded) - 800 = 1200.00
        assert balances[1] == Decimal("1200.00")
        # Period 2: 1200 - 600 = 600.00
        assert balances[2] == Decimal("600.00")

    def test_all_cancelled_period_passes_balance_through(self):
        """A period with only cancelled transactions passes balance through unchanged.

        Input: 3 periods, anchor=1000.00. Period 2 has two cancelled expenses.
        Expected: Cancelled transactions excluded; period 2 balance = period 1 balance.
        Why: If cancelled items are accidentally included or the period is skipped
        entirely, downstream balances break.
        """
        periods = [FakePeriod(1), FakePeriod(2), FakePeriod(3)]
        txns = [
            # Period 1 (anchor): projected income
            FakeTxn(1, "Projected", "Income", "2000.00"),
            # Period 2: two cancelled expenses -- must be excluded
            FakeTxn(2, "Cancelled", "Expense", "500.00"),
            FakeTxn(2, "Cancelled", "Expense", "300.00"),
            # Period 3: projected expense
            FakeTxn(3, "Projected", "Expense", "400.00"),
        ]

        balances, _ = balance_calculator.calculate_balances(
            anchor_balance=Decimal("1000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=txns,
        )

        # Period 1 (anchor): 1000 + 2000 = 3000.00
        assert balances[1] == Decimal("3000.00")
        # Period 2: 3000 + 0 - 0 = 3000.00 (cancelled excluded, balance passes through)
        assert balances[2] == Decimal("3000.00")
        # Period 3: 3000 - 400 = 2600.00
        assert balances[3] == Decimal("2600.00")

    def test_done_transaction_excluded_from_anchor_remaining(self):
        """Done transactions in the anchor period are excluded (already settled).

        Input: 2 periods, anchor=3000.00. Anchor has a done income
        (estimated_amount=2500, actual_amount=2487.33 on the ORM object).
        No projected transactions in anchor period.
        Expected: The done income is already reflected in the anchor balance and
        must NOT be added again. Balance = 3000 unchanged.
        Why: Mixing up estimated vs. actual or double-counting done items is the
        most dangerous financial bug possible. This test locks the behavior.
        """
        periods = [FakePeriod(1), FakePeriod(2)]
        txns = [
            # Anchor: done income -- already settled. The balance calculator skips
            # it entirely (status_id != projected_id), so estimated_amount is never read.
            FakeTxn(1, "Paid", "Income", "2500.00"),
            # Period 2: projected expense
            FakeTxn(2, "Projected", "Expense", "1000.00"),
        ]

        balances, _ = balance_calculator.calculate_balances(
            anchor_balance=Decimal("3000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=txns,
        )

        # Anchor: 3000 + 0(done excluded) = 3000.00
        assert balances[1] == Decimal("3000.00")
        # Period 2: 3000 - 1000 = 2000.00
        assert balances[2] == Decimal("2000.00")

    def test_mixed_statuses_single_period_comprehensive(self):
        """Single-period integration test of the entire status filtering logic.

        Input: 1 period (anchor), anchor=5000.00. Six transactions, one per status:
        projected income, done expense, received income, credit expense, cancelled
        expense, projected expense.
        Expected: Only projected items counted. End balance = 5000 + 1500 - 750 = 5750.
        Why: If any status is misclassified, this catches it.
        """
        periods = [FakePeriod(1)]
        txns = [
            # projected income -- INCLUDED (only projected items count in _sum_remaining)
            FakeTxn(1, "Projected", "Income", "1500.00"),
            # done expense -- EXCLUDED (status_id != projected_id: already in anchor)
            FakeTxn(1, "Paid", "Expense", "999.00"),
            # received income -- EXCLUDED (status_id != projected_id: already in anchor)
            FakeTxn(1, "Received", "Income", "888.00"),
            # credit expense -- EXCLUDED (credit card, not checking balance)
            FakeTxn(1, "Credit", "Expense", "777.00"),
            # cancelled expense -- EXCLUDED (user cancelled it)
            FakeTxn(1, "Cancelled", "Expense", "666.00"),
            # projected expense -- INCLUDED
            FakeTxn(1, "Projected", "Expense", "750.00"),
        ]

        balances, _ = balance_calculator.calculate_balances(
            anchor_balance=Decimal("5000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=txns,
        )

        # Only projected items: +1500 income, -750 expense
        # 5000 + 1500 - 750 = 5750.00
        assert balances[1] == Decimal("5750.00")


class TestStaleAnchorWarning:
    """Tests for the stale_anchor_warning flag returned by calculate_balances()."""

    def test_warning_when_done_in_post_anchor(self):
        """Warning is True when a done transaction exists after the anchor."""
        periods = [FakePeriod(1), FakePeriod(2), FakePeriod(3)]
        txns = [
            FakeTxn(1, "Projected", "Income", "1000.00"),
            FakeTxn(2, "Paid", "Expense", "500.00"),
        ]
        _, warning = balance_calculator.calculate_balances(
            anchor_balance=Decimal("5000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=txns,
        )
        assert warning is True

    def test_no_warning_when_all_projected(self):
        """Warning is False when all post-anchor transactions are projected."""
        periods = [FakePeriod(1), FakePeriod(2), FakePeriod(3)]
        txns = [
            FakeTxn(1, "Projected", "Income", "1000.00"),
            FakeTxn(2, "Projected", "Expense", "500.00"),
            FakeTxn(3, "Projected", "Expense", "200.00"),
        ]
        _, warning = balance_calculator.calculate_balances(
            anchor_balance=Decimal("5000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=txns,
        )
        assert warning is False

    def test_no_warning_when_done_only_in_anchor(self):
        """Done transactions in the anchor period do not trigger the warning."""
        periods = [FakePeriod(1), FakePeriod(2), FakePeriod(3)]
        txns = [
            FakeTxn(1, "Paid", "Income", "1000.00"),
            FakeTxn(2, "Projected", "Expense", "200.00"),
        ]
        _, warning = balance_calculator.calculate_balances(
            anchor_balance=Decimal("5000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=txns,
        )
        assert warning is False

    def test_warning_triggered_by_received_status(self):
        """Warning is True for received (income) status in post-anchor."""
        periods = [FakePeriod(1), FakePeriod(2)]
        txns = [
            FakeTxn(2, "Received", "Income", "3000.00"),
        ]
        _, warning = balance_calculator.calculate_balances(
            anchor_balance=Decimal("1000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=txns,
        )
        assert warning is True

    def test_no_warning_for_credit_or_cancelled(self):
        """Credit and cancelled statuses do not trigger the warning."""
        periods = [FakePeriod(1), FakePeriod(2)]
        txns = [
            FakeTxn(2, "Credit", "Expense", "100.00"),
            FakeTxn(2, "Cancelled", "Expense", "200.00"),
        ]
        _, warning = balance_calculator.calculate_balances(
            anchor_balance=Decimal("1000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=txns,
        )
        assert warning is False

    def test_no_warning_with_no_transactions(self):
        """Warning is False when there are no transactions at all."""
        periods = [FakePeriod(1), FakePeriod(2), FakePeriod(3)]
        _, warning = balance_calculator.calculate_balances(
            anchor_balance=Decimal("1000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=[],
        )
        assert warning is False

    def test_warning_does_not_change_balances(self):
        """The warning flag is informational only -- balances are unchanged."""
        periods = [FakePeriod(1), FakePeriod(2), FakePeriod(3)]
        txns_projected = [
            FakeTxn(2, "Projected", "Expense", "500.00"),
            FakeTxn(3, "Projected", "Expense", "200.00"),
        ]
        txns_with_done = [
            FakeTxn(2, "Paid", "Expense", "500.00"),
            FakeTxn(3, "Projected", "Expense", "200.00"),
        ]

        balances_projected, warn_projected = balance_calculator.calculate_balances(
            anchor_balance=Decimal("5000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=txns_projected,
        )
        balances_done, warn_done = balance_calculator.calculate_balances(
            anchor_balance=Decimal("5000.00"),
            anchor_period_id=1,
            periods=periods,
            transactions=txns_with_done,
        )

        assert warn_projected is False
        assert warn_done is True
        # Period 2: projected gives 5000-500=4500; done gives 5000 (excluded).
        # They differ because done is excluded -- that IS the correct behavior.
        # Period 3: both subtract 200 from their respective period 2 balance.
        # The warning does not change the calculation logic.
        assert balances_projected[2] == Decimal("4500.00")
        assert balances_done[2] == Decimal("5000.00")  # done excluded
        assert balances_projected[3] == Decimal("4300.00")
        assert balances_done[3] == Decimal("4800.00")  # done excluded


class TestBalanceCalculatorRegressionBaseline:
    """Regression baseline for Section 5A.

    Locks down current balance calculator behavior BEFORE 5A.1 changes
    how effective_amount and the calculator handle actual vs. estimated.
    Tests that intentionally assert current (buggy) behavior are marked
    with comments indicating the expected post-5A.1 change.
    """

    def test_balance_uses_actual_for_projected_when_populated(
        self, app, db, seed_user, seed_periods,
    ):
        """Projected transaction with actual_amount populated: balance
        calculator uses actual_amount via effective_amount.

        Originally a Commit #0 regression baseline asserting the D-1 bug
        (estimated_amount was used, ignoring actual).  Updated in Commit
        5A.1 to assert the corrected behavior: the balance calculator now
        uses effective_amount, which prefers actual_amount when populated.
        """
        with app.app_context():
            scenario = seed_user["scenario"]
            account = seed_user["account"]
            periods = seed_periods

            projected = db.session.query(Status).filter_by(
                name="Projected",
            ).one()
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()

            # Expense with both amounts populated -- actual differs from
            # estimated.  After 5A.1, calculator uses actual via
            # effective_amount.
            txn = Transaction(
                pay_period_id=periods[0].id,
                scenario_id=scenario.id,
                account_id=account.id,
                status_id=projected.id,
                name="Regression: est vs actual",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("100.00"),
                actual_amount=Decimal("150.00"),
            )
            db.session.add(txn)
            db.session.flush()

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("1000.00"),
                anchor_period_id=periods[0].id,
                periods=periods[:2],
                transactions=[txn],
            )

            # 5A.1 fix: 1000 - 150 (actual) = 850.
            # Before fix: was 1000 - 100 (estimated) = 900.
            assert balances[periods[0].id] == Decimal("850.00"), (
                "Balance calculator should use actual_amount (150) "
                "for Projected transactions when populated"
            )

    def test_balance_excludes_settled_from_projection(
        self, app, db, seed_user, seed_periods,
    ):
        """Settled (Paid) transaction in anchor period is excluded from
        the remaining-items calculation.

        Both _sum_remaining and _sum_all filter by status_id ==
        projected_id, so any non-Projected transaction contributes zero
        regardless of its estimated or actual amounts.  This test locks
        down that behavior with a Paid expense that has both amounts
        populated, ensuring the exclusion persists after 5A.1 changes
        the amount source.
        """
        with app.app_context():
            scenario = seed_user["scenario"]
            account = seed_user["account"]
            periods = seed_periods

            paid = db.session.query(Status).filter_by(name="Paid").one()
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()

            txn = Transaction(
                pay_period_id=periods[0].id,
                scenario_id=scenario.id,
                account_id=account.id,
                status_id=paid.id,
                name="Regression: settled exclusion",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("100.00"),
                actual_amount=Decimal("150.00"),
            )
            db.session.add(txn)
            db.session.flush()

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("1000.00"),
                anchor_period_id=periods[0].id,
                periods=periods[:2],
                transactions=[txn],
            )

            # Paid transaction excluded entirely: balance = anchor unchanged.
            assert balances[periods[0].id] == Decimal("1000.00"), (
                "Settled transactions must be excluded from balance "
                "projection -- they are already reflected in the anchor"
            )

    def test_effective_amount_property_behavior(
        self, app, db, seed_user, seed_periods,
    ):
        """Exhaustive verification of Transaction.effective_amount for
        every status and edge case.

        Originally a Commit #0 regression baseline.  Updated in Commit
        5A.1 to assert the corrected property logic: all active statuses
        now prefer actual_amount when populated.

        Property logic (after 5A.1):
          - is_deleted=True             -> Decimal("0")
          - status.excludes_from_balance -> Decimal("0")
          - actual_amount is not None   -> actual_amount
          - otherwise                   -> estimated_amount
        """
        with app.app_context():
            scenario = seed_user["scenario"]
            account = seed_user["account"]
            periods = seed_periods

            # Look up all statuses and types.
            projected = db.session.query(Status).filter_by(
                name="Projected",
            ).one()
            paid = db.session.query(Status).filter_by(name="Paid").one()
            received = db.session.query(Status).filter_by(
                name="Received",
            ).one()
            credit = db.session.query(Status).filter_by(name="Credit").one()
            cancelled = db.session.query(Status).filter_by(
                name="Cancelled",
            ).one()
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()

            def make_txn(name, status, estimated, actual=None,
                         is_deleted=False):
                """Create a transaction with the given parameters."""
                txn = Transaction(
                    pay_period_id=periods[0].id,
                    scenario_id=scenario.id,
                    account_id=account.id,
                    status_id=status.id,
                    name=name,
                    category_id=seed_user["categories"]["Rent"].id,
                    transaction_type_id=expense_type.id,
                    estimated_amount=estimated,
                    actual_amount=actual,
                    is_deleted=is_deleted,
                )
                db.session.add(txn)
                return txn

            # --- Projected status cases ---

            # Projected with actual populated: returns actual (5A.1 fix).
            t1 = make_txn(
                "Proj+actual", projected,
                Decimal("100.00"), Decimal("150.00"),
            )

            # Projected with actual=None: returns estimated (fallback).
            t2 = make_txn(
                "Proj+no_actual", projected,
                Decimal("100.00"), None,
            )

            # Projected with actual=0: returns 0 (5A.1 fix -- zero is
            # a valid actual, e.g. a waived fee).
            t3 = make_txn(
                "Proj+zero_actual", projected,
                Decimal("100.00"), Decimal("0"),
            )

            # --- Settled status cases ---

            # Paid with actual populated: returns actual.
            t4 = make_txn(
                "Paid+actual", paid,
                Decimal("100.00"), Decimal("80.00"),
            )

            # Paid with actual=None: returns estimated (fallback).
            t5 = make_txn(
                "Paid+no_actual", paid,
                Decimal("100.00"), None,
            )

            # Received with actual populated: returns actual.
            t6 = make_txn(
                "Received+actual", received,
                Decimal("100.00"), Decimal("120.00"),
            )

            # --- Excluded status cases ---

            # Credit: returns 0 regardless of amounts.
            t7 = make_txn(
                "Credit", credit,
                Decimal("100.00"), Decimal("150.00"),
            )

            # Cancelled: returns 0 regardless of amounts.
            t8 = make_txn(
                "Cancelled", cancelled,
                Decimal("100.00"), Decimal("150.00"),
            )

            # --- Deleted case ---

            # Soft-deleted: returns 0 regardless of status or amounts.
            t9 = make_txn(
                "Deleted", projected,
                Decimal("100.00"), Decimal("150.00"),
                is_deleted=True,
            )

            db.session.flush()

            # Projected: prefers actual when populated (5A.1 fix).
            assert t1.effective_amount == Decimal("150.00"), (
                "Projected with actual: should return actual_amount"
            )
            assert t2.effective_amount == Decimal("100.00"), (
                "Projected without actual: should fall back to estimated"
            )
            # Projected with actual=0: returns 0 (zero is a valid actual).
            assert t3.effective_amount == Decimal("0"), (
                "Projected with zero actual: should return 0 (waived fee)"
            )

            # Settled: returns actual if present, else estimated.
            assert t4.effective_amount == Decimal("80.00"), (
                "Paid with actual: should return actual_amount"
            )
            assert t5.effective_amount == Decimal("100.00"), (
                "Paid without actual: should fall back to estimated"
            )
            assert t6.effective_amount == Decimal("120.00"), (
                "Received with actual: should return actual_amount"
            )

            # Excluded statuses: always 0.
            assert t7.effective_amount == Decimal("0"), (
                "Credit: should return 0 (excludes_from_balance)"
            )
            assert t8.effective_amount == Decimal("0"), (
                "Cancelled: should return 0 (excludes_from_balance)"
            )

            # Deleted: always 0.
            assert t9.effective_amount == Decimal("0"), (
                "Deleted: should return 0 regardless of status"
            )


class TestEffectiveAmountFix:
    """Tests for the 5A.1 effective_amount and balance calculator fix.

    Verifies that the balance calculator correctly uses actual_amount
    (via effective_amount) when populated, falling back to estimated_amount
    when actual is null.
    """

    def test_balance_uses_estimated_when_actual_is_null(
        self, app, db, seed_user, seed_periods,
    ):
        """Projected transaction with actual=None: balance reflects
        estimated_amount as the fallback.

        Confirms the null path: when no actual has been entered, the
        calculator correctly uses the estimate.
        """
        with app.app_context():
            scenario = seed_user["scenario"]
            account = seed_user["account"]
            periods = seed_periods

            projected = db.session.query(Status).filter_by(
                name="Projected",
            ).one()
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()

            txn = Transaction(
                pay_period_id=periods[0].id,
                scenario_id=scenario.id,
                account_id=account.id,
                status_id=projected.id,
                name="No actual entered",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("100.00"),
                actual_amount=None,
            )
            db.session.add(txn)
            db.session.flush()

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("1000.00"),
                anchor_period_id=periods[0].id,
                periods=periods[:2],
                transactions=[txn],
            )

            # actual=None -> fallback to estimated: 1000 - 100 = 900.
            assert balances[periods[0].id] == Decimal("900.00")

    def test_balance_uses_zero_actual_not_estimated(
        self, app, db, seed_user, seed_periods,
    ):
        """Projected transaction with actual=0.00: balance reflects zero
        deduction, NOT the estimated amount.

        This is the most critical edge case.  A waived fee (actual=$0)
        must not fall back to the $100 estimate.  If the is-not-None
        check were a truthiness check, Decimal("0") would be falsy and
        the calculator would incorrectly deduct $100.
        """
        with app.app_context():
            scenario = seed_user["scenario"]
            account = seed_user["account"]
            periods = seed_periods

            projected = db.session.query(Status).filter_by(
                name="Projected",
            ).one()
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()

            txn = Transaction(
                pay_period_id=periods[0].id,
                scenario_id=scenario.id,
                account_id=account.id,
                status_id=projected.id,
                name="Waived fee",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("100.00"),
                actual_amount=Decimal("0.00"),
            )
            db.session.add(txn)
            db.session.flush()

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("1000.00"),
                anchor_period_id=periods[0].id,
                periods=periods[:2],
                transactions=[txn],
            )

            # actual=0 -> deduction is 0, not 100: 1000 - 0 = 1000.
            assert balances[periods[0].id] == Decimal("1000.00"), (
                "Zero actual must be used, not the estimated amount"
            )

    def test_mixed_actual_and_estimated_in_same_period(
        self, app, db, seed_user, seed_periods,
    ):
        """Two expenses in the same period: one with actual, one without.

        Verifies the calculator handles heterogeneous transactions within
        a single period correctly -- using actual for one and estimated
        for the other.
        """
        with app.app_context():
            scenario = seed_user["scenario"]
            account = seed_user["account"]
            periods = seed_periods

            projected = db.session.query(Status).filter_by(
                name="Projected",
            ).one()
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()

            # Expense 1: actual populated.
            txn1 = Transaction(
                pay_period_id=periods[0].id,
                scenario_id=scenario.id,
                account_id=account.id,
                status_id=projected.id,
                name="Rent (actual known)",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("100.00"),
                actual_amount=Decimal("200.00"),
            )
            # Expense 2: no actual.
            txn2 = Transaction(
                pay_period_id=periods[0].id,
                scenario_id=scenario.id,
                account_id=account.id,
                status_id=projected.id,
                name="Groceries (estimate only)",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("50.00"),
                actual_amount=None,
            )
            db.session.add_all([txn1, txn2])
            db.session.flush()

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("1000.00"),
                anchor_period_id=periods[0].id,
                periods=periods[:2],
                transactions=[txn1, txn2],
            )

            # 1000 - 200 (actual) - 50 (estimated fallback) = 750.
            assert balances[periods[0].id] == Decimal("750.00")

    def test_balance_decimal_precision(
        self, app, db, seed_user, seed_periods,
    ):
        """Verify balance is correct to exactly 2 decimal places after
        the Decimal wrapping removal (O-1).

        Guards against precision loss from switching to txn.effective_amount
        (already Decimal from SQLAlchemy Numeric) instead of
        Decimal(str(txn.estimated_amount)).
        """
        with app.app_context():
            scenario = seed_user["scenario"]
            account = seed_user["account"]
            periods = seed_periods

            projected = db.session.query(Status).filter_by(
                name="Projected",
            ).one()
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()

            txn = Transaction(
                pay_period_id=periods[0].id,
                scenario_id=scenario.id,
                account_id=account.id,
                status_id=projected.id,
                name="Precise amount",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("100.50"),
                actual_amount=Decimal("99.99"),
            )
            db.session.add(txn)
            db.session.flush()

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("1000.00"),
                anchor_period_id=periods[0].id,
                periods=periods[:2],
                transactions=[txn],
            )

            # 1000.00 - 99.99 = 900.01 (exact Decimal arithmetic).
            assert balances[periods[0].id] == Decimal("900.01")

    def test_effective_amount_projected_actual_zero(
        self, app, db, seed_user, seed_periods,
    ):
        """Projected transaction with actual=Decimal("0.00"): property
        returns Decimal("0.00"), not the estimated amount.

        Specifically tests the zero-actual path for Projected status.
        If someone uses truthiness instead of `is not None`, this fails.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(
                name="Projected",
            ).one()
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()

            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Zero actual",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("250.00"),
                actual_amount=Decimal("0.00"),
            )
            db.session.add(txn)
            db.session.flush()

            assert txn.effective_amount == Decimal("0"), (
                "Zero actual must return 0, not fall back to estimated"
            )

    def test_balance_multiple_periods_with_mixed_actuals(
        self, app, db, seed_user, seed_periods,
    ):
        """Running balance across two periods with varying actual/null
        combinations.

        Period 0 (anchor): expense actual=200, income estimated=500.
        Period 1 (post-anchor): expense estimated=300.
        Verifies accumulation across periods is correct.
        """
        with app.app_context():
            scenario = seed_user["scenario"]
            account = seed_user["account"]
            periods = seed_periods

            projected = db.session.query(Status).filter_by(
                name="Projected",
            ).one()
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()
            income_type = db.session.query(TransactionType).filter_by(
                name="Income",
            ).one()

            txns = [
                # Period 0: expense with actual.
                Transaction(
                    pay_period_id=periods[0].id,
                    scenario_id=scenario.id,
                    account_id=account.id,
                    status_id=projected.id,
                    name="Rent",
                    category_id=seed_user["categories"]["Rent"].id,
                    transaction_type_id=expense_type.id,
                    estimated_amount=Decimal("150.00"),
                    actual_amount=Decimal("200.00"),
                ),
                # Period 0: income without actual.
                Transaction(
                    pay_period_id=periods[0].id,
                    scenario_id=scenario.id,
                    account_id=account.id,
                    status_id=projected.id,
                    name="Paycheck",
                    category_id=seed_user["categories"]["Salary"].id,
                    transaction_type_id=income_type.id,
                    estimated_amount=Decimal("500.00"),
                    actual_amount=None,
                ),
                # Period 1: expense without actual.
                Transaction(
                    pay_period_id=periods[1].id,
                    scenario_id=scenario.id,
                    account_id=account.id,
                    status_id=projected.id,
                    name="Groceries",
                    category_id=seed_user["categories"]["Groceries"].id,
                    transaction_type_id=expense_type.id,
                    estimated_amount=Decimal("300.00"),
                    actual_amount=None,
                ),
            ]
            for t in txns:
                db.session.add(t)
            db.session.flush()

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("1000.00"),
                anchor_period_id=periods[0].id,
                periods=periods[:3],
                transactions=txns,
            )

            # Period 0: 1000 + 500 (est) - 200 (actual) = 1300.
            assert balances[periods[0].id] == Decimal("1300.00")
            # Period 1: 1300 - 300 (est) = 1000.
            assert balances[periods[1].id] == Decimal("1000.00")

    def test_balance_all_transactions_have_actuals(
        self, app, db, seed_user, seed_periods,
    ):
        """Period where every transaction has actual_amount populated.

        Verifies no estimated amounts leak into the balance when all
        actuals are known.
        """
        with app.app_context():
            scenario = seed_user["scenario"]
            account = seed_user["account"]
            periods = seed_periods

            projected = db.session.query(Status).filter_by(
                name="Projected",
            ).one()
            income_type = db.session.query(TransactionType).filter_by(
                name="Income",
            ).one()
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()

            txns = [
                Transaction(
                    pay_period_id=periods[0].id,
                    scenario_id=scenario.id,
                    account_id=account.id,
                    status_id=projected.id,
                    name="Pay",
                    category_id=seed_user["categories"]["Salary"].id,
                    transaction_type_id=income_type.id,
                    estimated_amount=Decimal("2000.00"),
                    actual_amount=Decimal("1950.00"),
                ),
                Transaction(
                    pay_period_id=periods[0].id,
                    scenario_id=scenario.id,
                    account_id=account.id,
                    status_id=projected.id,
                    name="Rent",
                    category_id=seed_user["categories"]["Rent"].id,
                    transaction_type_id=expense_type.id,
                    estimated_amount=Decimal("1200.00"),
                    actual_amount=Decimal("1200.00"),
                ),
            ]
            for t in txns:
                db.session.add(t)
            db.session.flush()

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("500.00"),
                anchor_period_id=periods[0].id,
                periods=periods[:2],
                transactions=txns,
            )

            # 500 + 1950 (actual) - 1200 (actual) = 1250.
            # NOT 500 + 2000 - 1200 = 1300 (estimated).
            assert balances[periods[0].id] == Decimal("1250.00")

    def test_balance_no_transactions_have_actuals(
        self, app, db, seed_user, seed_periods,
    ):
        """Period where no transactions have actual_amount populated.

        Verifies the calculator falls back to estimated for all of them.
        """
        with app.app_context():
            scenario = seed_user["scenario"]
            account = seed_user["account"]
            periods = seed_periods

            projected = db.session.query(Status).filter_by(
                name="Projected",
            ).one()
            income_type = db.session.query(TransactionType).filter_by(
                name="Income",
            ).one()
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()

            txns = [
                Transaction(
                    pay_period_id=periods[0].id,
                    scenario_id=scenario.id,
                    account_id=account.id,
                    status_id=projected.id,
                    name="Pay",
                    category_id=seed_user["categories"]["Salary"].id,
                    transaction_type_id=income_type.id,
                    estimated_amount=Decimal("2000.00"),
                    actual_amount=None,
                ),
                Transaction(
                    pay_period_id=periods[0].id,
                    scenario_id=scenario.id,
                    account_id=account.id,
                    status_id=projected.id,
                    name="Rent",
                    category_id=seed_user["categories"]["Rent"].id,
                    transaction_type_id=expense_type.id,
                    estimated_amount=Decimal("1200.00"),
                    actual_amount=None,
                ),
            ]
            for t in txns:
                db.session.add(t)
            db.session.flush()

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("500.00"),
                anchor_period_id=periods[0].id,
                periods=periods[:2],
                transactions=txns,
            )

            # 500 + 2000 (est) - 1200 (est) = 1300.
            assert balances[periods[0].id] == Decimal("1300.00")


# ── Section 5 Regression Baseline ──────────────────────────────────────


class TestTransferInvariantsBalanceRegression:
    """Regression tests verifying the five transfer invariants hold when
    transfers are used with the balance calculator.

    These tests create real transfers via transfer_service and then run
    the balance calculator to verify shadow transactions are correctly
    reflected.  This is a cross-cutting concern that Section 5 must
    preserve.
    """

    def test_transfer_creates_exactly_two_shadows(  # pylint: disable=too-many-locals
        self, app, db, seed_user, seed_periods,
    ):
        """Invariant 1: Every transfer has exactly two linked shadow
        transactions (one expense, one income).

        Verifies the count and types after creating a transfer between
        checking and a savings account.
        """
        from app.models.account import Account  # pylint: disable=import-outside-toplevel
        from app.models.ref import AccountType  # pylint: disable=import-outside-toplevel
        from app.services.transfer_service import create_transfer  # pylint: disable=import-outside-toplevel

        with app.app_context():
            user = seed_user["user"]
            account = seed_user["account"]
            scenario = seed_user["scenario"]
            periods = seed_periods

            savings_type = db.session.query(AccountType).filter_by(
                name="Savings"
            ).one()
            savings_acct = Account(
                user_id=user.id,
                account_type_id=savings_type.id,
                name="Invariant Savings",
                current_anchor_balance=Decimal("0.00"),
            )
            db.session.add(savings_acct)
            db.session.flush()
            savings_acct.current_anchor_period_id = periods[0].id
            db.session.commit()

            transfer = create_transfer(
                user_id=user.id,
                from_account_id=account.id,
                to_account_id=savings_acct.id,
                pay_period_id=periods[1].id,
                scenario_id=scenario.id,
                amount=Decimal("200.00"),
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            # Query shadow transactions.
            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=transfer.id, is_deleted=False)
                .all()
            )
            assert len(shadows) == 2

            types = {s.transaction_type.name for s in shadows}
            assert types == {"Expense", "Income"}

    def test_shadow_amounts_match_transfer(  # pylint: disable=too-many-locals
        self, app, db, seed_user, seed_periods,
    ):
        """Invariant 3: Shadow transaction amounts equal the parent
        transfer's amount.
        """
        from app.models.account import Account  # pylint: disable=import-outside-toplevel
        from app.models.ref import AccountType  # pylint: disable=import-outside-toplevel
        from app.services.transfer_service import create_transfer  # pylint: disable=import-outside-toplevel

        with app.app_context():
            user = seed_user["user"]
            account = seed_user["account"]
            scenario = seed_user["scenario"]
            periods = seed_periods

            savings_type = db.session.query(AccountType).filter_by(
                name="Savings"
            ).one()
            savings_acct = Account(
                user_id=user.id,
                account_type_id=savings_type.id,
                name="Amount Test Savings",
                current_anchor_balance=Decimal("0.00"),
            )
            db.session.add(savings_acct)
            db.session.flush()
            savings_acct.current_anchor_period_id = periods[0].id
            db.session.commit()

            transfer_amount = Decimal("350.00")
            transfer = create_transfer(
                user_id=user.id,
                from_account_id=account.id,
                to_account_id=savings_acct.id,
                pay_period_id=periods[1].id,
                scenario_id=scenario.id,
                amount=transfer_amount,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=transfer.id, is_deleted=False)
                .all()
            )
            for shadow in shadows:
                assert shadow.estimated_amount == transfer_amount, (
                    f"Shadow {shadow.id} estimated_amount "
                    f"{shadow.estimated_amount} != transfer {transfer_amount}"
                )

    def test_shadow_statuses_and_periods_match_transfer(  # pylint: disable=too-many-locals
        self, app, db, seed_user, seed_periods,
    ):
        """Invariants 4 & 5: Shadow statuses and periods always equal
        the parent transfer's.
        """
        from app.models.account import Account  # pylint: disable=import-outside-toplevel
        from app.models.ref import AccountType  # pylint: disable=import-outside-toplevel
        from app.services.transfer_service import create_transfer  # pylint: disable=import-outside-toplevel

        with app.app_context():
            user = seed_user["user"]
            account = seed_user["account"]
            scenario = seed_user["scenario"]
            periods = seed_periods

            savings_type = db.session.query(AccountType).filter_by(
                name="Savings"
            ).one()
            savings_acct = Account(
                user_id=user.id,
                account_type_id=savings_type.id,
                name="Status Period Savings",
                current_anchor_balance=Decimal("0.00"),
            )
            db.session.add(savings_acct)
            db.session.flush()
            savings_acct.current_anchor_period_id = periods[0].id
            db.session.commit()

            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            transfer = create_transfer(
                user_id=user.id,
                from_account_id=account.id,
                to_account_id=savings_acct.id,
                pay_period_id=periods[2].id,
                scenario_id=scenario.id,
                amount=Decimal("100.00"),
                status_id=projected_id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=transfer.id, is_deleted=False)
                .all()
            )
            for shadow in shadows:
                assert shadow.status_id == transfer.status_id, (
                    f"Shadow {shadow.id} status_id {shadow.status_id} "
                    f"!= transfer status_id {transfer.status_id}"
                )
                assert shadow.pay_period_id == transfer.pay_period_id, (
                    f"Shadow {shadow.id} pay_period_id "
                    f"{shadow.pay_period_id} != transfer "
                    f"pay_period_id {transfer.pay_period_id}"
                )

    def test_balance_calculator_reflects_transfer_shadows(  # pylint: disable=too-many-locals
        self, app, db, seed_user, seed_periods,
    ):
        """Balance calculator correctly reflects transfer shadow transactions.

        Creates a transfer between checking and savings, then verifies
        the balance calculator shows the checking balance decreased and
        the savings balance increased by the transfer amount.  This
        confirms no double-counting occurs.
        """
        from app.models.account import Account  # pylint: disable=import-outside-toplevel
        from app.models.ref import AccountType  # pylint: disable=import-outside-toplevel
        from app.services.transfer_service import create_transfer  # pylint: disable=import-outside-toplevel

        with app.app_context():
            user = seed_user["user"]
            checking = seed_user["account"]
            scenario = seed_user["scenario"]
            periods = seed_periods

            savings_type = db.session.query(AccountType).filter_by(
                name="Savings"
            ).one()
            savings_acct = Account(
                user_id=user.id,
                account_type_id=savings_type.id,
                name="Balance Check Savings",
                current_anchor_balance=Decimal("500.00"),
            )
            db.session.add(savings_acct)
            db.session.flush()
            savings_acct.current_anchor_period_id = periods[0].id
            db.session.commit()

            transfer_amount = Decimal("250.00")
            transfer = create_transfer(
                user_id=user.id,
                from_account_id=checking.id,
                to_account_id=savings_acct.id,
                pay_period_id=periods[1].id,
                scenario_id=scenario.id,
                amount=transfer_amount,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            # Get all shadow transactions.
            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=transfer.id, is_deleted=False)
                .all()
            )

            # ── Checking account balance ──
            checking_shadows = [
                s for s in shadows if s.account_id == checking.id
            ]
            checking_balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("1000.00"),
                anchor_period_id=periods[0].id,
                periods=periods[:3],
                transactions=checking_shadows,
            )
            # Checking decreased by transfer amount in period 2.
            assert checking_balances[periods[1].id] == (
                Decimal("1000.00") - transfer_amount
            )

            # ── Savings account balance ──
            savings_shadows = [
                s for s in shadows if s.account_id == savings_acct.id
            ]
            savings_balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("500.00"),
                anchor_period_id=periods[0].id,
                periods=periods[:3],
                transactions=savings_shadows,
            )
            # Savings increased by transfer amount in period 2.
            assert savings_balances[periods[1].id] == (
                Decimal("500.00") + transfer_amount
            )
