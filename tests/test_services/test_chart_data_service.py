"""
Shekel Budget App — Chart Data Service Tests

Tests for the chart data orchestration service:
  - Each method returns valid empty structure when no data exists
  - Correct data reshaping for chart consumption
  - Edge cases (no periods, no accounts, no transactions)
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.auto_loan_params import AutoLoanParams
from app.models.category import Category
from app.models.mortgage_params import MortgageParams
from app.models.pay_period import PayPeriod
from app.models.ref import AccountType, Status, TransactionType
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.services import chart_data_service


# ── Empty Data Tests ────────────────────────────────────────────────


class TestEmptyDataReturnsEmptyStructure:
    """Every service method returns a valid empty structure with no data."""

    def test_balance_over_time_empty(self, app, seed_user):
        """No crash on zero periods."""
        with app.app_context():
            result = chart_data_service.get_balance_over_time(
                user_id=seed_user["user"].id,
            )
            assert result["labels"] == []
            assert result["datasets"] == []

    def test_spending_by_category_empty(self, app, seed_user):
        """No crash on zero transactions."""
        with app.app_context():
            result = chart_data_service.get_spending_by_category(
                user_id=seed_user["user"].id,
            )
            assert result["labels"] == []
            assert result["data"] == []

    def test_budget_vs_actuals_empty(self, app, seed_user):
        """No crash on zero transactions."""
        with app.app_context():
            result = chart_data_service.get_budget_vs_actuals(
                user_id=seed_user["user"].id,
            )
            assert result["labels"] == []
            assert result["estimated"] == []
            assert result["actual"] == []

    def test_amortization_breakdown_empty(self, app, seed_user):
        """No crash on zero loan accounts."""
        with app.app_context():
            result = chart_data_service.get_amortization_breakdown(
                user_id=seed_user["user"].id,
            )
            assert result["labels"] == []
            assert result["principal"] == []
            assert result["interest"] == []

    def test_net_worth_over_time_empty(self, app, seed_user):
        """No crash on zero accounts."""
        with app.app_context():
            result = chart_data_service.get_net_worth_over_time(
                user_id=seed_user["user"].id,
            )
            assert result["labels"] == []
            assert result["data"] == []

    def test_net_pay_trajectory_empty(self, app, seed_user):
        """No crash on zero salary profiles."""
        with app.app_context():
            result = chart_data_service.get_net_pay_trajectory(
                user_id=seed_user["user"].id,
            )
            assert result["labels"] == []
            assert result["data"] == []


# ── Balance Over Time Tests ─────────────────────────────────────────


class TestBalanceOverTime:
    """Tests for get_balance_over_time."""

    def test_single_checking_account(self, app, seed_user, seed_periods):
        """Single checking account produces one dataset."""
        with app.app_context():
            result = chart_data_service.get_balance_over_time(
                user_id=seed_user["user"].id,
            )
            # 10 seed_periods → 10 labels; 1 checking account → 1 dataset
            assert len(result["labels"]) == 10
            assert len(result["datasets"]) == 1
            # Checking account should be on left axis.
            checking_ds = next(
                (ds for ds in result["datasets"]
                 if ds["label"] == "Checking"),
                None,
            )
            assert checking_ds is not None
            assert checking_ds["axis"] == "y"

    def test_multi_account(self, app, seed_user, seed_periods):
        """Multiple account types produce separate datasets."""
        with app.app_context():
            # Add a savings account.
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="savings")
                .one()
            )
            savings = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Savings",
                current_anchor_balance=Decimal("5000.00"),
                current_anchor_period_id=seed_periods[0].id,
            )
            db.session.add(savings)
            db.session.commit()

            result = chart_data_service.get_balance_over_time(
                user_id=seed_user["user"].id,
            )
            account_names = [ds["label"] for ds in result["datasets"]]
            assert "Checking" in account_names
            assert "Savings" in account_names

    def test_account_id_filter(self, app, seed_user, seed_periods):
        """account_ids parameter filters to specific accounts."""
        with app.app_context():
            result = chart_data_service.get_balance_over_time(
                user_id=seed_user["user"].id,
                account_ids=[seed_user["account"].id],
            )
            assert len(result["datasets"]) == 1
            assert result["datasets"][0]["label"] == "Checking"

    def test_date_range_filter(self, app, seed_user, seed_periods):
        """Start/end params correctly bound the data."""
        with app.app_context():
            start = seed_periods[2].start_date.isoformat()
            end = seed_periods[5].end_date.isoformat()
            result = chart_data_service.get_balance_over_time(
                user_id=seed_user["user"].id,
                start=start,
                end=end,
            )
            # Periods 2-5 satisfy start_date >= start AND end_date <= end → 4 labels
            assert len(result["labels"]) == 4


# ── Spending by Category Tests ──────────────────────────────────────


class TestSpendingByCategory:
    """Tests for get_spending_by_category."""

    def test_groups_correctly(self, app, seed_user, seed_periods):
        """Expense transactions summed by category group."""
        with app.app_context():
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="expense")
                .one()
            )
            done_status = (
                db.session.query(Status).filter_by(name="done").one()
            )

            # Create two expenses in the same group.
            for name, amount in [("Rent", "1200.00"), ("Groceries", "200.00")]:
                cat = seed_user["categories"].get(name)
                if cat:
                    txn = Transaction(
                        template_id=None,
                        pay_period_id=seed_periods[0].id,
                        scenario_id=seed_user["scenario"].id,
                        category_id=cat.id,
                        transaction_type_id=expense_type.id,
                        name=name,
                        estimated_amount=Decimal(amount),
                        actual_amount=Decimal(amount),
                        status_id=done_status.id,
                    )
                    db.session.add(txn)
            db.session.commit()

            result = chart_data_service.get_spending_by_category(
                user_id=seed_user["user"].id,
                period_range="last_12",
            )
            # 2 expenses in distinct category groups: "Home" (Rent) and
            # "Family" (Groceries) → 2 labels
            assert len(result["labels"]) == 2
            assert all(isinstance(v, float) for v in result["data"])

    def test_only_done_and_projected(self, app, seed_user, seed_periods):
        """Only done and projected transactions included."""
        with app.app_context():
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="expense")
                .one()
            )
            cancelled_status = (
                db.session.query(Status).filter_by(name="cancelled").one()
            )

            txn = Transaction(
                template_id=None,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                name="Cancelled Rent",
                estimated_amount=Decimal("1200.00"),
                status_id=cancelled_status.id,
            )
            db.session.add(txn)
            db.session.commit()

            result = chart_data_service.get_spending_by_category(
                user_id=seed_user["user"].id,
                period_range="last_3",
            )
            # Cancelled transaction should not appear.
            assert result["labels"] == []


# ── Budget vs. Actuals Tests ────────────────────────────────────────


class TestBudgetVsActuals:
    """Tests for get_budget_vs_actuals."""

    def test_estimated_and_actual(self, app, seed_user, seed_periods):
        """Both estimated and actual amounts returned per category."""
        with app.app_context():
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="expense")
                .one()
            )
            done_status = (
                db.session.query(Status).filter_by(name="done").one()
            )

            txn = Transaction(
                template_id=None,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                name="Rent",
                estimated_amount=Decimal("1200.00"),
                actual_amount=Decimal("1250.00"),
                status_id=done_status.id,
            )
            db.session.add(txn)
            db.session.commit()

            result = chart_data_service.get_budget_vs_actuals(
                user_id=seed_user["user"].id,
                period_range="last_12",
            )
            assert "Home" in result["labels"]
            idx = result["labels"].index("Home")
            assert result["estimated"][idx] == 1200.0
            assert result["actual"][idx] == 1250.0


# ── Amortization Breakdown Tests ────────────────────────────────────


class TestAmortizationBreakdown:
    """Tests for get_amortization_breakdown."""

    def test_matches_engine_output(self, app, seed_user):
        """Output matches amortization_engine.generate_schedule()."""
        with app.app_context():
            mortgage_type = (
                db.session.query(AccountType)
                .filter_by(name="mortgage")
                .one()
            )
            account = Account(
                user_id=seed_user["user"].id,
                account_type_id=mortgage_type.id,
                name="Test Mortgage",
                current_anchor_balance=Decimal("200000.00"),
            )
            db.session.add(account)
            db.session.flush()

            params = MortgageParams(
                account_id=account.id,
                original_principal=Decimal("250000.00"),
                current_principal=Decimal("200000.00"),
                interest_rate=Decimal("0.06000"),
                term_months=360,
                origination_date=date(2022, 6, 1),
                payment_day=1,
            )
            db.session.add(params)
            db.session.commit()

            result = chart_data_service.get_amortization_breakdown(
                user_id=seed_user["user"].id,
                account_id=account.id,
            )
            # Remaining months = term_months - elapsed months since origination.
            # origination_date=2022-06-01, term=360. Labels = remaining months.
            from app.services import amortization_engine
            expected_months = amortization_engine.calculate_remaining_months(
                date(2022, 6, 1), 360,
            )
            assert len(result["labels"]) == expected_months
            assert len(result["principal"]) == len(result["labels"])
            assert len(result["interest"]) == len(result["labels"])
            assert result["account_name"] == "Test Mortgage"

            # Principal should increase over time (more goes to principal).
            assert result["principal"][-1] > result["principal"][0]


# ── Net Worth Tests ─────────────────────────────────────────────────


class TestNetWorthOverTime:
    """Tests for get_net_worth_over_time."""

    def test_assets_minus_liabilities(self, app, seed_user, seed_periods):
        """Correct aggregation across account types."""
        with app.app_context():
            result = chart_data_service.get_net_worth_over_time(
                user_id=seed_user["user"].id,
            )
            # 10 seed_periods → 10 labels; single checking account
            assert len(result["labels"]) == 10
            assert len(result["data"]) == 10
            # Net worth at period 0 = checking anchor balance = $1,000.00
            # (no transactions, no liabilities)
            assert result["data"][0] == 1000.0


# ── Helpers Tests ───────────────────────────────────────────────────


class TestHelpers:
    """Tests for helper functions."""

    def test_get_loan_accounts(self, app, seed_user):
        """Returns mortgage and auto loan accounts."""
        with app.app_context():
            # Initially empty.
            result = chart_data_service.get_loan_accounts(
                user_id=seed_user["user"].id,
            )
            assert result == []

            # Add a mortgage account.
            mortgage_type = (
                db.session.query(AccountType)
                .filter_by(name="mortgage")
                .one()
            )
            account = Account(
                user_id=seed_user["user"].id,
                account_type_id=mortgage_type.id,
                name="Home Loan",
                current_anchor_balance=Decimal("200000.00"),
            )
            db.session.add(account)
            db.session.commit()

            result = chart_data_service.get_loan_accounts(
                user_id=seed_user["user"].id,
            )
            assert len(result) == 1
            assert result[0]["name"] == "Home Loan"
            assert result[0]["type"] == "mortgage"

    def test_get_salary_profiles(self, app, seed_user):
        """Returns empty list when no profiles exist."""
        with app.app_context():
            result = chart_data_service.get_salary_profiles(
                user_id=seed_user["user"].id,
            )
            assert result == []
