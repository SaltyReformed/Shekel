"""
Shekel Budget App — Model Computed Property Tests

Tests for computed properties on models:
  - Transaction: effective_amount, is_income, is_expense
  - Transfer: effective_amount (projected vs done vs cancelled)
  - Category: display_name
  - PayPeriod: label
  - PaycheckBreakdown: total_pre_tax, total_post_tax, total_taxes
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.ref import Status, TransactionType
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services.paycheck_calculator import DeductionLine, PaycheckBreakdown


# ── Transaction.effective_amount ─────────────────────────────────────


class TestTransactionEffectiveAmount:
    """Tests for Transaction.effective_amount computed property."""

    def _make_txn(self, seed_user, seed_periods, status_name, estimated, actual=None):
        """Helper: create a transaction with given status and amounts."""
        status = db.session.query(Status).filter_by(name=status_name).one()
        expense_type = db.session.query(TransactionType).filter_by(name="expense").one()
        txn = Transaction(
            pay_period_id=seed_periods[0].id,
            scenario_id=seed_user["scenario"].id,
            status_id=status.id,
            name="Test",
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=expense_type.id,
            estimated_amount=estimated,
            actual_amount=actual,
        )
        db.session.add(txn)
        db.session.flush()
        return txn

    def test_projected_returns_estimated(self, app, db, seed_user, seed_periods):
        """Projected transaction returns estimated_amount."""
        with app.app_context():
            txn = self._make_txn(seed_user, seed_periods, "projected", Decimal("150.00"))
            assert txn.effective_amount == Decimal("150.00")

    def test_done_with_actual_returns_actual(self, app, db, seed_user, seed_periods):
        """Done transaction with actual_amount returns actual_amount."""
        with app.app_context():
            txn = self._make_txn(
                seed_user, seed_periods, "done",
                Decimal("150.00"), actual=Decimal("145.00"),
            )
            assert txn.effective_amount == Decimal("145.00")

    def test_done_without_actual_returns_estimated(self, app, db, seed_user, seed_periods):
        """Done transaction without actual_amount falls back to estimated."""
        with app.app_context():
            txn = self._make_txn(seed_user, seed_periods, "done", Decimal("150.00"))
            assert txn.effective_amount == Decimal("150.00")


# ── Transaction.is_income / is_expense ───────────────────────────────


class TestTransactionTypeProperties:
    """Tests for Transaction.is_income and is_expense properties."""

    def test_is_income(self, app, db, seed_user, seed_periods):
        """is_income returns True for income-type transactions."""
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

            assert txn.is_income is True
            assert txn.is_expense is False

    def test_is_expense(self, app, db, seed_user, seed_periods):
        """is_expense returns True for expense-type transactions."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()
            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=projected.id,
                name="Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("85.00"),
            )
            db.session.add(txn)
            db.session.flush()

            assert txn.is_expense is True
            assert txn.is_income is False


# ── Transfer.effective_amount ────────────────────────────────────────


class TestTransferEffectiveAmount:
    """Tests for Transfer.effective_amount computed property."""

    def _make_transfer(self, seed_user, seed_periods, status_name, amount):
        """Helper: create a transfer with given status and amount."""
        from app.models.account import Account
        from app.models.ref import AccountType

        savings_type = db.session.query(AccountType).filter_by(name="savings").one()
        savings = Account(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="Savings",
            current_anchor_balance=Decimal("0"),
        )
        db.session.add(savings)
        db.session.flush()

        status = db.session.query(Status).filter_by(name=status_name).one()
        xfer = Transfer(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            pay_period_id=seed_periods[0].id,
            scenario_id=seed_user["scenario"].id,
            status_id=status.id,
            name="Test Transfer",
            amount=amount,
        )
        db.session.add(xfer)
        db.session.flush()
        return xfer

    def test_projected_returns_amount(self, app, db, seed_user, seed_periods):
        """Projected transfer returns its amount."""
        with app.app_context():
            xfer = self._make_transfer(seed_user, seed_periods, "projected", Decimal("500.00"))
            assert xfer.effective_amount == Decimal("500.00")

    def test_done_returns_amount(self, app, db, seed_user, seed_periods):
        """Done transfer returns its amount."""
        with app.app_context():
            xfer = self._make_transfer(seed_user, seed_periods, "done", Decimal("500.00"))
            assert xfer.effective_amount == Decimal("500.00")


# ── Category.display_name ────────────────────────────────────────────


class TestCategoryDisplayName:
    """Tests for Category.display_name property."""

    def test_display_name_format(self, app, db, seed_user):
        """display_name returns 'group: item' format."""
        with app.app_context():
            cat = seed_user["categories"]["Rent"]
            assert cat.display_name == "Home: Rent"


# ── PayPeriod.label ──────────────────────────────────────────────────


class TestPayPeriodLabel:
    """Tests for PayPeriod.label property."""

    def test_label_format(self, app, db, seed_user, seed_periods):
        """label returns 'MM/DD – MM/DD' formatted string."""
        with app.app_context():
            period = seed_periods[0]
            # seed_periods start 2026-01-02, cadence 14 days → end 2026-01-15.
            assert period.label == "01/02 – 01/15"


# ── PaycheckBreakdown computed totals ────────────────────────────────


class TestPaycheckBreakdownTotals:
    """Tests for PaycheckBreakdown.total_pre_tax, total_post_tax, total_taxes."""

    def test_total_pre_tax(self):
        """total_pre_tax sums pre-tax deduction amounts."""
        breakdown = PaycheckBreakdown(
            period_id=1,
            annual_salary=Decimal("75000"),
            gross_biweekly=Decimal("2884.62"),
            pre_tax_deductions=[
                DeductionLine(name="401k", amount=Decimal("250.00")),
                DeductionLine(name="HSA", amount=Decimal("100.00")),
            ],
        )
        assert breakdown.total_pre_tax == Decimal("350.00")

    def test_total_post_tax(self):
        """total_post_tax sums post-tax deduction amounts."""
        breakdown = PaycheckBreakdown(
            period_id=1,
            annual_salary=Decimal("75000"),
            gross_biweekly=Decimal("2884.62"),
            post_tax_deductions=[
                DeductionLine(name="Roth IRA", amount=Decimal("200.00")),
                DeductionLine(name="Life Insurance", amount=Decimal("25.00")),
            ],
        )
        assert breakdown.total_post_tax == Decimal("225.00")

    def test_total_taxes(self):
        """total_taxes sums federal + state + ss + medicare."""
        breakdown = PaycheckBreakdown(
            period_id=1,
            annual_salary=Decimal("75000"),
            gross_biweekly=Decimal("2884.62"),
            federal_tax=Decimal("300.00"),
            state_tax=Decimal("130.00"),
            social_security=Decimal("178.85"),
            medicare=Decimal("41.83"),
        )
        assert breakdown.total_taxes == Decimal("650.68")

    def test_empty_deductions_return_zero(self):
        """Empty deduction lists produce Decimal('0') totals."""
        breakdown = PaycheckBreakdown(
            period_id=1,
            annual_salary=Decimal("75000"),
            gross_biweekly=Decimal("2884.62"),
        )
        assert breakdown.total_pre_tax == Decimal("0")
        assert breakdown.total_post_tax == Decimal("0")
