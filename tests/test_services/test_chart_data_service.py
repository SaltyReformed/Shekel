"""
Shekel Budget App -- Chart Data Service Tests

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
from app.models.transfer import Transfer
from app.services import amortization_engine, balance_calculator, chart_data_service


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

    def test_spending_with_multiple_categories_exact_sums(self, app, seed_user, seed_periods):
        """Expense transactions summed by category group with exact Decimal values.

        Seeds 10 transactions across 4 categories. Verifies exact per-group
        sums, cancelled/credit exclusions, and multi-transaction aggregation.
        Expected: Home=2400, Family=650, Auto=300. Cancelled/credit excluded.
        """
        with app.app_context():
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="expense")
                .one()
            )
            done = db.session.query(Status).filter_by(name="done").one()
            projected = db.session.query(Status).filter_by(name="projected").one()
            cancelled = db.session.query(Status).filter_by(name="cancelled").one()
            credit = db.session.query(Status).filter_by(name="credit").one()

            # Use a period we know is in "last_12" range (period 0).
            period = seed_periods[0]

            txn_data = [
                # Home group (Rent category): two done txns
                ("Rent", "Rent Jan", "1200.00", "1200.00", done),
                ("Rent", "Rent Feb", "1200.00", "1200.00", done),
                # Family group (Groceries category): three txns
                ("Groceries", "Groceries Wk1", "200.00", "200.00", done),
                ("Groceries", "Groceries Wk2", "250.00", "250.00", done),
                ("Groceries", "Groceries Wk3", "200.00", None, projected),
                # Auto group (Car Payment): one done txn
                ("Car Payment", "Car Pmt", "300.00", "300.00", done),
                # Cancelled -- must NOT appear
                ("Rent", "Cancelled Rent", "1200.00", None, cancelled),
                # Credit -- must NOT appear (status filter excludes it)
                ("Groceries", "Credit Groceries", "100.00", None, credit),
            ]

            for cat_name, name, est, act, status in txn_data:
                cat = seed_user["categories"][cat_name]
                txn = Transaction(
                    template_id=None,
                    pay_period_id=period.id,
                    scenario_id=seed_user["scenario"].id,
                    category_id=cat.id,
                    transaction_type_id=expense_type.id,
                    name=name,
                    estimated_amount=Decimal(est),
                    actual_amount=Decimal(act) if act else None,
                    status_id=status.id,
                )
                db.session.add(txn)
            db.session.commit()

            result = chart_data_service.get_spending_by_category(
                user_id=seed_user["user"].id,
                period_range="last_12",
            )

            # Build a label→amount mapping from the result.
            spending = dict(zip(result["labels"], result["data"]))

            # Home: 1200 + 1200 = 2400 (cancelled Rent excluded)
            assert spending["Home"] == 2400.0
            # Family: 200 + 250 + 200(estimated, projected) = 650
            # (credit Groceries excluded by status filter)
            assert spending["Family"] == 650.0
            # Auto: 300
            assert spending["Auto"] == 300.0
            # Only 3 groups should appear (cancelled/credit excluded)
            assert len(result["labels"]) == 3

    def test_spending_by_category_invalid_user(self, app, seed_user, seed_periods):
        """Nonexistent user_id returns empty result without crashing.

        The function queries for periods and scenario by user_id. A
        nonexistent user produces no periods → returns empty dict.
        Expected: empty labels and data lists.
        """
        with app.app_context():
            result = chart_data_service.get_spending_by_category(
                user_id=999999,
                period_range="last_12",
            )
            assert result["labels"] == []
            assert result["data"] == []


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

    def test_net_worth_with_liability_exact(self, app, seed_user, seed_periods):
        """Net worth = assets - liabilities with exact values.

        Adds a mortgage (liability) account alongside the existing checking
        (asset) account. Net worth = checking - mortgage for each period.
        Expected: net_worth[0] = 1000.00 - 200000.00 = -199000.00.
        """
        with app.app_context():
            mortgage_type = (
                db.session.query(AccountType)
                .filter_by(name="mortgage")
                .one()
            )
            mortgage = Account(
                user_id=seed_user["user"].id,
                account_type_id=mortgage_type.id,
                name="Home Loan",
                current_anchor_balance=Decimal("200000.00"),
                current_anchor_period_id=seed_periods[0].id,
            )
            db.session.add(mortgage)
            db.session.commit()

            result = chart_data_service.get_net_worth_over_time(
                user_id=seed_user["user"].id,
            )
            assert len(result["data"]) == 10
            # Checking ($1,000 asset) - Mortgage ($200,000 liability)
            assert result["data"][0] == -199000.0

    def test_net_worth_invalid_user(self, app, seed_user):
        """Nonexistent user_id returns empty net worth without crashing.

        Expected: empty labels and data lists.
        """
        with app.app_context():
            result = chart_data_service.get_net_worth_over_time(
                user_id=999999,
            )
            assert result["labels"] == []
            assert result["data"] == []


# ── Balance Over Time Consistency Tests ────────────────────────────

class TestBalanceConsistency:
    """Cross-service consistency between chart data and balance calculator."""

    def test_balance_over_time_matches_balance_calculator(
        self, app, seed_user, seed_periods,
    ):
        """Chart data balances match balance_calculator.calculate_balances().

        Both services must produce identical balance values for the same
        account, periods, and transactions. If they disagree, the user
        sees contradictory numbers on the dashboard vs. the chart.
        Expected: every chart data point equals the corresponding balance
        from calculate_balances().
        """
        from app.services import balance_calculator

        with app.app_context():
            account = seed_user["account"]
            scenario = seed_user["scenario"]

            # Get chart data.
            chart_result = chart_data_service.get_balance_over_time(
                user_id=seed_user["user"].id,
            )
            assert len(chart_result["datasets"]) == 1
            chart_balances = chart_result["datasets"][0]["data"]

            # Get balance calculator results directly.
            periods = seed_periods
            calc_balances = balance_calculator.calculate_balances(
                anchor_balance=account.current_anchor_balance,
                anchor_period_id=account.current_anchor_period_id,
                periods=periods,
                transactions=[],
                transfers=[],
                account_id=account.id,
            )

            # Compare each period.
            for i, period in enumerate(periods):
                calc_val = float(calc_balances.get(period.id, Decimal("0")))
                assert chart_balances[i] == calc_val, (
                    f"Period {i} mismatch: chart={chart_balances[i]}, "
                    f"calc={calc_val}"
                )


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


# ── Realistic Data Scale Tests (WU 8.1) ──────────────────────────────────


class TestSpendingChartRealisticData:
    """Verify get_spending_by_category with realistic data volumes.

    Seeds 60+ transactions across 6 categories and 10 periods, proving
    the service correctly sums, groups, and filters at production-like
    scale.
    """

    def test_spending_chart_many_categories_many_periods(
        self, app, seed_user, seed_periods,
    ):
        """60 expense transactions across 6 categories summed exactly.

        Seeds 6 categories x 10 periods = 60 transactions plus 5 noise
        transactions (cancelled, income, deleted).  Verifies exact
        per-group sums for the 6 periods within the 'last_12' range
        window, and confirms noise transactions have zero effect.

        Amount formula: category_index c (0-5), period_index p (0-9):
            amount = (c + 1) * 100 + p * 10

        With today=2026-03-20, 'last_12' includes periods 0-5 (6 periods).
        Expected sums per category group (p=0..5):
            c=0 (Home):          6*100 + 10*(0+1+2+3+4+5) = 750
            c=1 (Auto):          6*200 + 150 = 1350
            c=2 (Family):        6*300 + 150 = 1950
            c=3 (Credit Card):   6*400 + 150 = 2550
            c=4 (Utilities):     6*500 + 150 = 3150
            c=5 (Entertainment): 6*600 + 150 = 3750
        """
        with app.app_context():
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="expense").one()
            )
            income_type = (
                db.session.query(TransactionType)
                .filter_by(name="income").one()
            )
            done_status = (
                db.session.query(Status).filter_by(name="done").one()
            )
            cancelled_status = (
                db.session.query(Status).filter_by(name="cancelled").one()
            )

            # seed_user provides 5 categories with groups:
            #   Income/Salary, Home/Rent, Auto/Car Payment,
            #   Family/Groceries, Credit Card/Payback
            # Create 2 more expense-suitable categories with unique groups.
            cat_utilities = Category(
                user_id=seed_user["user"].id,
                group_name="Utilities",
                item_name="Electric",
            )
            cat_entertainment = Category(
                user_id=seed_user["user"].id,
                group_name="Entertainment",
                item_name="Netflix",
            )
            db.session.add_all([cat_utilities, cat_entertainment])
            db.session.flush()

            # 6 categories ordered by index for the amount formula.
            categories_ordered = [
                seed_user["categories"]["Rent"],         # c=0, group=Home
                seed_user["categories"]["Car Payment"],  # c=1, group=Auto
                seed_user["categories"]["Groceries"],    # c=2, group=Family
                seed_user["categories"]["Payback"],      # c=3, group=Credit Card
                cat_utilities,                            # c=4, group=Utilities
                cat_entertainment,                        # c=5, group=Entertainment
            ]
            group_names = [c.group_name for c in categories_ordered]

            # Create 60 expense transactions: 6 categories x 10 periods.
            for c_idx, cat in enumerate(categories_ordered):
                for p_idx in range(10):
                    amount = Decimal(str((c_idx + 1) * 100 + p_idx * 10))
                    txn = Transaction(
                        template_id=None,
                        pay_period_id=seed_periods[p_idx].id,
                        scenario_id=seed_user["scenario"].id,
                        category_id=cat.id,
                        transaction_type_id=expense_type.id,
                        name=f"{cat.group_name} P{p_idx}",
                        estimated_amount=amount,
                        actual_amount=amount,
                        status_id=done_status.id,
                    )
                    db.session.add(txn)

            # 5 noise transactions -- must NOT appear in results.
            # 2 cancelled expenses (in Home and Auto groups).
            for cat in [categories_ordered[0], categories_ordered[1]]:
                noise = Transaction(
                    template_id=None,
                    pay_period_id=seed_periods[0].id,
                    scenario_id=seed_user["scenario"].id,
                    category_id=cat.id,
                    transaction_type_id=expense_type.id,
                    name=f"Cancelled {cat.group_name}",
                    estimated_amount=Decimal("999.99"),
                    actual_amount=Decimal("999.99"),
                    status_id=cancelled_status.id,
                )
                db.session.add(noise)

            # 2 income transactions (excluded by expense-type filter).
            salary_cat = seed_user["categories"]["Salary"]
            for i in range(2):
                noise = Transaction(
                    template_id=None,
                    pay_period_id=seed_periods[i].id,
                    scenario_id=seed_user["scenario"].id,
                    category_id=salary_cat.id,
                    transaction_type_id=income_type.id,
                    name=f"Income Noise {i}",
                    estimated_amount=Decimal("5000.00"),
                    actual_amount=Decimal("5000.00"),
                    status_id=done_status.id,
                )
                db.session.add(noise)

            # 1 deleted expense (excluded by is_deleted filter).
            noise = Transaction(
                template_id=None,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                category_id=categories_ordered[0].id,
                transaction_type_id=expense_type.id,
                name="Deleted Noise",
                estimated_amount=Decimal("888.88"),
                actual_amount=Decimal("888.88"),
                status_id=done_status.id,
                is_deleted=True,
            )
            db.session.add(noise)
            db.session.commit()

            result = chart_data_service.get_spending_by_category(
                user_id=seed_user["user"].id,
                period_range="last_12",
            )

            # With today=2026-03-20, last_12 covers periods 0-5 (6 periods).
            # Expected sums per group for p in range(6):
            #   sum = (c+1)*100*6 + 10*(0+1+2+3+4+5) = (c+1)*600 + 150
            expected_sums = {}
            for c_idx, gname in enumerate(group_names):
                total = sum((c_idx + 1) * 100 + p * 10 for p in range(6))
                expected_sums[gname] = float(total)

            assert len(result["labels"]) == 6  # Exactly 6 category groups.
            spending = dict(zip(result["labels"], result["data"]))

            for gname, expected in expected_sums.items():
                assert spending[gname] == expected, (
                    f"Group '{gname}': got {spending.get(gname)}, "
                    f"expected {expected}"
                )
            # Cancelled, income, and deleted transactions are verified
            # excluded by the exact sum assertions -- any leakage would
            # inflate the sums beyond the expected values.

    def test_spending_chart_period_range_filtering(
        self, app, seed_user, seed_periods,
    ):
        """Period ranges 'current' and 'last_3' correctly restrict data.

        Seeds 3 categories across 6 periods (0-5).  Verifies 'current'
        returns spending from only the current period, and 'last_3'
        returns spending from exactly 3 periods.

        With today=2026-03-20, current period index = 5.
          current:  period 5 only
          last_3:   periods 3, 4, 5

        Amount formula: (c + 1) * 50 + p * 5
        """
        with app.app_context():
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="expense").one()
            )
            done_status = (
                db.session.query(Status).filter_by(name="done").one()
            )

            categories_ordered = [
                seed_user["categories"]["Rent"],         # c=0, Home
                seed_user["categories"]["Car Payment"],  # c=1, Auto
                seed_user["categories"]["Groceries"],    # c=2, Family
            ]

            # Seed transactions across periods 0-5.
            for c_idx, cat in enumerate(categories_ordered):
                for p_idx in range(6):
                    amount = Decimal(str((c_idx + 1) * 50 + p_idx * 5))
                    txn = Transaction(
                        template_id=None,
                        pay_period_id=seed_periods[p_idx].id,
                        scenario_id=seed_user["scenario"].id,
                        category_id=cat.id,
                        transaction_type_id=expense_type.id,
                        name=f"Range {cat.group_name} P{p_idx}",
                        estimated_amount=amount,
                        actual_amount=amount,
                        status_id=done_status.id,
                    )
                    db.session.add(txn)
            db.session.commit()

            # 'current' -- period 5 only.
            result_current = chart_data_service.get_spending_by_category(
                user_id=seed_user["user"].id,
                period_range="current",
            )
            assert len(result_current["labels"]) == 3
            spending_current = dict(
                zip(result_current["labels"], result_current["data"])
            )
            for c_idx, cat in enumerate(categories_ordered):
                # Period 5 only: amount = (c+1)*50 + 5*5 = (c+1)*50 + 25
                expected = float((c_idx + 1) * 50 + 25)
                assert spending_current[cat.group_name] == expected, (
                    f"current range, {cat.group_name}: "
                    f"got {spending_current.get(cat.group_name)}, "
                    f"expected {expected}"
                )

            # 'last_3' -- periods 3, 4, 5.
            result_last3 = chart_data_service.get_spending_by_category(
                user_id=seed_user["user"].id,
                period_range="last_3",
            )
            assert len(result_last3["labels"]) == 3
            spending_last3 = dict(
                zip(result_last3["labels"], result_last3["data"])
            )
            for c_idx, cat in enumerate(categories_ordered):
                # sum((c+1)*50 + p*5 for p in [3,4,5])
                # = 3*(c+1)*50 + 5*(3+4+5) = (c+1)*150 + 60
                expected = float(
                    sum((c_idx + 1) * 50 + p * 5 for p in range(3, 6))
                )
                assert spending_last3[cat.group_name] == expected, (
                    f"last_3 range, {cat.group_name}: "
                    f"got {spending_last3.get(cat.group_name)}, "
                    f"expected {expected}"
                )

    def test_spending_chart_mixed_done_and_projected_included(
        self, app, seed_user, seed_periods,
    ):
        """Both done and projected transactions contribute to spending sums.

        Seeds 4 expenses in the same category: 2 done (actual_amount set),
        2 projected (actual_amount=None).  The service uses actual_amount
        when present, estimated_amount otherwise.

        Expected sum: 100 + 200 + 300 + 400 = 1000.0
        """
        with app.app_context():
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="expense").one()
            )
            done_status = (
                db.session.query(Status).filter_by(name="done").one()
            )
            projected_status = (
                db.session.query(Status).filter_by(name="projected").one()
            )

            cat = seed_user["categories"]["Rent"]  # group_name="Home"
            # Period 5 is the current period (2026-03-13 to 2026-03-26).
            period = seed_periods[5]

            txn_specs = [
                # (name, estimated, actual, status)
                ("Done 1", Decimal("100.00"), Decimal("100.00"), done_status),
                ("Done 2", Decimal("200.00"), Decimal("200.00"), done_status),
                ("Proj 1", Decimal("300.00"), None, projected_status),
                ("Proj 2", Decimal("400.00"), None, projected_status),
            ]
            for name, est, act, status in txn_specs:
                txn = Transaction(
                    template_id=None,
                    pay_period_id=period.id,
                    scenario_id=seed_user["scenario"].id,
                    category_id=cat.id,
                    transaction_type_id=expense_type.id,
                    name=name,
                    estimated_amount=est,
                    actual_amount=act,
                    status_id=status.id,
                )
                db.session.add(txn)
            db.session.commit()

            result = chart_data_service.get_spending_by_category(
                user_id=seed_user["user"].id,
                period_range="current",
            )

            assert len(result["labels"]) == 1
            assert result["labels"][0] == "Home"
            # Done txns use actual_amount: 100 + 200 = 300.
            # Projected txns use estimated_amount: 300 + 400 = 700.
            # Total: 1000.0.
            assert result["data"][0] == 1000.0


class TestBalanceChart52Periods:
    """Verify get_balance_over_time across 52 periods.

    Cross-verifies chart data against balance_calculator output to prove
    the chart layer does not silently diverge from the financial engine.
    """

    def test_balance_chart_52_periods_matches_calculator(
        self, app, seed_user, seed_periods_52,
    ):
        """Every chart data point matches balance_calculator across 52 periods.

        Seeds deterministic transactions per period:
            income:  +2500.00 (projected)
            expense: -850.00  (projected)
            expense: -125.50  (projected)
            Net per period: +1524.50

        Period 5: income is 'done' (actual_amount=2600.00) -- excluded from
        balance calc because done transactions are treated as settled.
        Period 10: extra cancelled expense of 500.00 -- excluded, no effect.

        This loop constitutes 52 individual value assertions.  If even one
        diverges, the chart is showing the user incorrect balance projections.
        """
        with app.app_context():
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="expense").one()
            )
            income_type = (
                db.session.query(TransactionType)
                .filter_by(name="income").one()
            )
            projected_status = (
                db.session.query(Status).filter_by(name="projected").one()
            )
            done_status = (
                db.session.query(Status).filter_by(name="done").one()
            )
            cancelled_status = (
                db.session.query(Status).filter_by(name="cancelled").one()
            )

            account = seed_user["account"]
            scenario = seed_user["scenario"]
            salary_cat = seed_user["categories"]["Salary"]
            rent_cat = seed_user["categories"]["Rent"]
            car_cat = seed_user["categories"]["Car Payment"]

            for p_idx, period in enumerate(seed_periods_52):
                # Income: projected everywhere except period 5 (done).
                if p_idx == 5:
                    inc_status = done_status
                    inc_actual = Decimal("2600.00")
                else:
                    inc_status = projected_status
                    inc_actual = None

                db.session.add(Transaction(
                    template_id=None,
                    pay_period_id=period.id,
                    scenario_id=scenario.id,
                    category_id=salary_cat.id,
                    transaction_type_id=income_type.id,
                    name=f"Paycheck P{p_idx}",
                    estimated_amount=Decimal("2500.00"),
                    actual_amount=inc_actual,
                    status_id=inc_status.id,
                    is_override=True,
                ))

                # Expense 1: rent (projected).
                db.session.add(Transaction(
                    template_id=None,
                    pay_period_id=period.id,
                    scenario_id=scenario.id,
                    category_id=rent_cat.id,
                    transaction_type_id=expense_type.id,
                    name=f"Rent P{p_idx}",
                    estimated_amount=Decimal("850.00"),
                    status_id=projected_status.id,
                    is_override=True,
                ))

                # Expense 2: car payment (projected).
                db.session.add(Transaction(
                    template_id=None,
                    pay_period_id=period.id,
                    scenario_id=scenario.id,
                    category_id=car_cat.id,
                    transaction_type_id=expense_type.id,
                    name=f"Car P{p_idx}",
                    estimated_amount=Decimal("125.50"),
                    status_id=projected_status.id,
                    is_override=True,
                ))

                # Period 10: cancelled expense (must have zero effect).
                if p_idx == 10:
                    db.session.add(Transaction(
                        template_id=None,
                        pay_period_id=period.id,
                        scenario_id=scenario.id,
                        category_id=rent_cat.id,
                        transaction_type_id=expense_type.id,
                        name="Cancelled Expense P10",
                        estimated_amount=Decimal("500.00"),
                        status_id=cancelled_status.id,
                        is_override=True,
                    ))

            db.session.commit()

            # Cross-verification: query transactions the same way the
            # chart service does internally (_calculate_account_balances).
            period_ids = [p.id for p in seed_periods_52]
            transactions = (
                db.session.query(Transaction)
                .filter_by(scenario_id=scenario.id)
                .filter(Transaction.pay_period_id.in_(period_ids))
                .filter(
                    db.or_(
                        Transaction.template.has(account_id=account.id),
                        Transaction.is_override.is_(True),
                    )
                )
                .all()
            )
            transfers = (
                db.session.query(Transfer)
                .filter_by(scenario_id=scenario.id)
                .filter(Transfer.pay_period_id.in_(period_ids))
                .all()
            )

            calc_balances = balance_calculator.calculate_balances(
                anchor_balance=account.current_anchor_balance,
                anchor_period_id=seed_periods_52[0].id,
                periods=seed_periods_52,
                transactions=transactions,
                transfers=transfers,
                account_id=account.id,
            )

            # Call chart data service.
            chart_result = chart_data_service.get_balance_over_time(
                user_id=seed_user["user"].id,
                account_ids=[account.id],
            )

            assert len(chart_result["labels"]) == 52
            checking_ds = next(
                ds for ds in chart_result["datasets"]
                if ds["account_id"] == account.id
            )
            assert len(checking_ds["data"]) == 52
            assert checking_ds["axis"] == "y"
            assert checking_ds["account_id"] == account.id

            # 52 individual value assertions -- chart must match calculator.
            for i, period in enumerate(seed_periods_52):
                expected = float(calc_balances[period.id])
                actual = checking_ds["data"][i]
                assert actual == expected, (
                    f"Period {i} ({period.start_date}): "
                    f"chart={actual}, calculator={expected}"
                )

    def test_balance_chart_52_periods_label_format(
        self, app, seed_user, seed_periods_52,
    ):
        """Labels match '%b %d' format and are in chronological order.

        Each label should be the period's start_date.strftime('%b %d').
        Verifies first, last, and every label against the period fixture.
        Also verifies chronological order by period start_dates -- not by
        alphabetical sort, since alphabetical != chronological for month
        names (e.g. 'Apr' < 'Aug' < 'Dec' < 'Jan').
        """
        with app.app_context():
            result = chart_data_service.get_balance_over_time(
                user_id=seed_user["user"].id,
            )

            assert len(result["labels"]) == 52

            # First label: 2026-01-02 -> "Jan 02".
            assert result["labels"][0] == "Jan 02"

            # Last label: 52nd period's start date.
            expected_last = seed_periods_52[51].start_date.strftime("%b %d")
            assert result["labels"][51] == expected_last

            # Every label matches its period's formatted start_date.
            for i, period in enumerate(seed_periods_52):
                expected_label = period.start_date.strftime("%b %d")
                assert result["labels"][i] == expected_label, (
                    f"Label {i}: got '{result['labels'][i]}', "
                    f"expected '{expected_label}'"
                )

            # Verify chronological order via period start_dates.
            for i in range(51):
                assert (
                    seed_periods_52[i].start_date
                    < seed_periods_52[i + 1].start_date
                ), (
                    f"Period {i} ({seed_periods_52[i].start_date}) "
                    f"should precede period {i+1} "
                    f"({seed_periods_52[i+1].start_date})"
                )

    def test_balance_chart_date_range_filter_exact_count(
        self, app, seed_user, seed_periods_52,
    ):
        """Start/end date params restrict chart to exactly 10 periods.

        Filters to periods 10-19 (inclusive) and verifies exact count
        and boundary labels.
        """
        with app.app_context():
            start = seed_periods_52[10].start_date.isoformat()
            end = seed_periods_52[19].end_date.isoformat()

            result = chart_data_service.get_balance_over_time(
                user_id=seed_user["user"].id,
                start=start,
                end=end,
            )

            assert len(result["labels"]) == 10

            # First label corresponds to period index 10.
            expected_first = seed_periods_52[10].start_date.strftime("%b %d")
            assert result["labels"][0] == expected_first

            # Last label corresponds to period index 19.
            expected_last = seed_periods_52[19].start_date.strftime("%b %d")
            assert result["labels"][9] == expected_last


class TestNetWorthRealisticData:
    """Verify get_net_worth_over_time with multiple account types.

    Cross-verifies net worth = assets - liabilities by independently
    computing from get_balance_over_time datasets.
    """

    def test_net_worth_asset_minus_liability_exact(
        self, app, seed_user, seed_periods,
    ):
        """Net worth equals checking (asset) minus mortgage (liability).

        Creates a mortgage account ($200k) with MortgageParams alongside
        the checking account ($1k).  Verifies every period's net worth
        by cross-referencing balance_over_time datasets.

        With no transactions or transfers, both balances are flat:
            Checking:  1000.00 (all periods)
            Mortgage: 200000.00 (all periods)
            Net worth: 1000.00 - 200000.00 = -199000.00 (all periods)
        """
        with app.app_context():
            mortgage_type = (
                db.session.query(AccountType)
                .filter_by(name="mortgage").one()
            )
            mortgage = Account(
                user_id=seed_user["user"].id,
                account_type_id=mortgage_type.id,
                name="Test Mortgage",
                current_anchor_balance=Decimal("200000.00"),
                current_anchor_period_id=seed_periods[0].id,
            )
            db.session.add(mortgage)
            db.session.flush()

            params = MortgageParams(
                account_id=mortgage.id,
                original_principal=Decimal("250000.00"),
                current_principal=Decimal("200000.00"),
                interest_rate=Decimal("0.06000"),
                term_months=360,
                origination_date=date(2022, 6, 1),
                payment_day=1,
            )
            db.session.add(params)
            db.session.commit()

            # Get net worth and raw balance datasets.
            nw_result = chart_data_service.get_net_worth_over_time(
                user_id=seed_user["user"].id,
            )
            bal_result = chart_data_service.get_balance_over_time(
                user_id=seed_user["user"].id,
            )

            assert len(nw_result["data"]) == 10
            assert len(nw_result["labels"]) == 10

            # Extract per-account datasets.
            checking_data = None
            mortgage_data = None
            for ds in bal_result["datasets"]:
                if ds["account_id"] == seed_user["account"].id:
                    checking_data = ds["data"]
                elif ds["account_id"] == mortgage.id:
                    mortgage_data = ds["data"]

            assert checking_data is not None, "Checking dataset missing"
            assert mortgage_data is not None, "Mortgage dataset missing"

            # Cross-verify every period:
            # net_worth[i] = checking[i] (asset, +1) + mortgage[i] (liability, -1)
            for i in range(10):
                expected = round(
                    checking_data[i] + (-1) * mortgage_data[i], 2
                )
                assert nw_result["data"][i] == expected, (
                    f"Period {i}: net_worth={nw_result['data'][i]}, "
                    f"expected={expected} "
                    f"(checking={checking_data[i]}, "
                    f"mortgage={mortgage_data[i]})"
                )

            # First period: no transactions, flat balances.
            assert nw_result["data"][0] == -199000.0

    def test_net_worth_with_transactions_cross_verified(
        self, app, seed_user, seed_periods,
    ):
        """Net worth varies as checking balance changes per period.

        Seeds projected income of 1500 per period in checking.  The
        checking balance increases each period while the mortgage stays
        constant (no transfers).

        Checking balances (projected income only, no expenses):
            P0 (anchor): 1000 + 1500 = 2500
            P1: 2500 + 1500 = 4000
            P2: 4000 + 1500 = 5500
            ...
            P9: 14500 + 1500 = 16000

        Mortgage: 200000.00 (constant, no transfers).
        Net worth per period = checking[i] - mortgage[i].
        """
        with app.app_context():
            income_type = (
                db.session.query(TransactionType)
                .filter_by(name="income").one()
            )
            projected_status = (
                db.session.query(Status).filter_by(name="projected").one()
            )
            mortgage_type = (
                db.session.query(AccountType)
                .filter_by(name="mortgage").one()
            )

            # Create mortgage account with params.
            mortgage = Account(
                user_id=seed_user["user"].id,
                account_type_id=mortgage_type.id,
                name="Test Mortgage",
                current_anchor_balance=Decimal("200000.00"),
                current_anchor_period_id=seed_periods[0].id,
            )
            db.session.add(mortgage)
            db.session.flush()

            params = MortgageParams(
                account_id=mortgage.id,
                original_principal=Decimal("250000.00"),
                current_principal=Decimal("200000.00"),
                interest_rate=Decimal("0.06000"),
                term_months=360,
                origination_date=date(2022, 6, 1),
                payment_day=1,
            )
            db.session.add(params)

            # Add projected income to checking for each period.
            salary_cat = seed_user["categories"]["Salary"]
            for p_idx, period in enumerate(seed_periods):
                db.session.add(Transaction(
                    template_id=None,
                    pay_period_id=period.id,
                    scenario_id=seed_user["scenario"].id,
                    category_id=salary_cat.id,
                    transaction_type_id=income_type.id,
                    name=f"Income P{p_idx}",
                    estimated_amount=Decimal("1500.00"),
                    status_id=projected_status.id,
                    is_override=True,
                ))
            db.session.commit()

            nw_result = chart_data_service.get_net_worth_over_time(
                user_id=seed_user["user"].id,
            )
            bal_result = chart_data_service.get_balance_over_time(
                user_id=seed_user["user"].id,
            )

            assert len(nw_result["data"]) == 10

            checking_data = None
            mortgage_data = None
            for ds in bal_result["datasets"]:
                if ds["account_id"] == seed_user["account"].id:
                    checking_data = ds["data"]
                elif ds["account_id"] == mortgage.id:
                    mortgage_data = ds["data"]

            assert checking_data is not None, "Checking dataset missing"
            assert mortgage_data is not None, "Mortgage dataset missing"

            # Cross-verify every period.
            for i in range(10):
                expected = round(
                    checking_data[i] + (-1) * mortgage_data[i], 2
                )
                assert nw_result["data"][i] == expected, (
                    f"Period {i}: net_worth={nw_result['data'][i]}, "
                    f"expected={expected}"
                )

            # Verify net worth is NOT constant -- checking grows each period.
            assert nw_result["data"][0] != nw_result["data"][9]

            # First period: checking = 1000 + 1500 = 2500, mortgage = 200000.
            assert nw_result["data"][0] == -197500.0


class TestBudgetVsActualsRealisticData:
    """Verify budget vs. actuals with realistic multi-category data."""

    def test_budget_vs_actuals_many_categories_exact_sums(
        self, app, seed_user, seed_periods,
    ):
        """Exact estimated and actual sums across 4 category groups.

        Seeds 3 transactions per group across periods 3-5 (within last_12).
        Each transaction has distinct estimated and actual amounts.

        Expected per-group sums:
            Home:      est = 1200*3 = 3600,  act = 1250+1200+1180 = 3630
            Family:    est = 400*3  = 1200,  act = 380+400+420    = 1200
            Auto:      est = 300*3  = 900,   act = 320+300+280    = 900
            Utilities: est = 150*3  = 450,   act = 145+150+155    = 450
        """
        with app.app_context():
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="expense").one()
            )
            done_status = (
                db.session.query(Status).filter_by(name="done").one()
            )

            cat_utilities = Category(
                user_id=seed_user["user"].id,
                group_name="Utilities",
                item_name="Electric",
            )
            db.session.add(cat_utilities)
            db.session.flush()

            # (category, period_idx, estimated, actual)
            txn_specs = [
                # Home (Rent): periods 3, 4, 5
                (seed_user["categories"]["Rent"], 3, "1200.00", "1250.00"),
                (seed_user["categories"]["Rent"], 4, "1200.00", "1200.00"),
                (seed_user["categories"]["Rent"], 5, "1200.00", "1180.00"),
                # Family (Groceries)
                (seed_user["categories"]["Groceries"], 3, "400.00", "380.00"),
                (seed_user["categories"]["Groceries"], 4, "400.00", "400.00"),
                (seed_user["categories"]["Groceries"], 5, "400.00", "420.00"),
                # Auto (Car Payment)
                (seed_user["categories"]["Car Payment"], 3, "300.00", "320.00"),
                (seed_user["categories"]["Car Payment"], 4, "300.00", "300.00"),
                (seed_user["categories"]["Car Payment"], 5, "300.00", "280.00"),
                # Utilities (Electric)
                (cat_utilities, 3, "150.00", "145.00"),
                (cat_utilities, 4, "150.00", "150.00"),
                (cat_utilities, 5, "150.00", "155.00"),
            ]

            for cat, p_idx, est, act in txn_specs:
                txn = Transaction(
                    template_id=None,
                    pay_period_id=seed_periods[p_idx].id,
                    scenario_id=seed_user["scenario"].id,
                    category_id=cat.id,
                    transaction_type_id=expense_type.id,
                    name=f"BvA {cat.group_name} P{p_idx}",
                    estimated_amount=Decimal(est),
                    actual_amount=Decimal(act),
                    status_id=done_status.id,
                )
                db.session.add(txn)
            db.session.commit()

            result = chart_data_service.get_budget_vs_actuals(
                user_id=seed_user["user"].id,
                period_range="last_12",
            )

            assert len(result["labels"]) == 4

            result_map = {}
            for i, label in enumerate(result["labels"]):
                result_map[label] = {
                    "estimated": result["estimated"][i],
                    "actual": result["actual"][i],
                }

            # Home: est=3*1200=3600, act=1250+1200+1180=3630
            assert result_map["Home"]["estimated"] == 3600.0
            assert result_map["Home"]["actual"] == 3630.0
            # Family: est=3*400=1200, act=380+400+420=1200
            assert result_map["Family"]["estimated"] == 1200.0
            assert result_map["Family"]["actual"] == 1200.0
            # Auto: est=3*300=900, act=320+300+280=900
            assert result_map["Auto"]["estimated"] == 900.0
            assert result_map["Auto"]["actual"] == 900.0
            # Utilities: est=3*150=450, act=145+150+155=450
            assert result_map["Utilities"]["estimated"] == 450.0
            assert result_map["Utilities"]["actual"] == 450.0

    def test_budget_vs_actuals_projected_uses_estimated_for_both(
        self, app, seed_user, seed_periods,
    ):
        """Projected transactions: estimated in estimated column, 0 in actual.

        The service sums estimated_amount for the estimated column and
        actual_amount for the actual column.  Since projected transactions
        have actual_amount=None, the actual column receives 0.

        Seed: 1 projected expense, estimated=500, actual=None.
        Expected: estimated=[500.0], actual=[0.0].
        """
        with app.app_context():
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="expense").one()
            )
            projected_status = (
                db.session.query(Status).filter_by(name="projected").one()
            )

            cat = seed_user["categories"]["Rent"]  # group_name="Home"
            txn = Transaction(
                template_id=None,
                pay_period_id=seed_periods[5].id,  # Current period.
                scenario_id=seed_user["scenario"].id,
                category_id=cat.id,
                transaction_type_id=expense_type.id,
                name="Projected Rent",
                estimated_amount=Decimal("500.00"),
                actual_amount=None,
                status_id=projected_status.id,
            )
            db.session.add(txn)
            db.session.commit()

            result = chart_data_service.get_budget_vs_actuals(
                user_id=seed_user["user"].id,
                period_range="current",
            )

            assert len(result["labels"]) == 1
            assert result["labels"][0] == "Home"
            assert result["estimated"][0] == 500.0
            # actual_amount is None -> service sums Decimal("0").
            assert result["actual"][0] == 0.0


class TestAmortizationBreakdownExact:
    """Verify amortization chart data matches engine output exactly.

    Replaces shallow assertions with exact cross-verification against
    the amortization_engine for both mortgage and auto loan account types.
    """

    def test_amortization_chart_matches_engine_exactly(
        self, app, seed_user,
    ):
        """Mortgage chart data matches amortization_engine.generate_schedule.

        Seeds $200k at 6%, 360 months, originated 2022-06-01, payment_day=1.
        Cross-verifies labels, principal, and interest for the first 3 rows
        and the last row.

        With today=2026-03-20:
            months_elapsed = (2026-2022)*12 + (3-6) = 45
            remaining_months = 360 - 45 = 315
        """
        with app.app_context():
            mortgage_type = (
                db.session.query(AccountType)
                .filter_by(name="mortgage").one()
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

            # Chart service result.
            result = chart_data_service.get_amortization_breakdown(
                user_id=seed_user["user"].id,
                account_id=account.id,
            )

            # Direct engine result for cross-verification.
            remaining_months = amortization_engine.calculate_remaining_months(
                date(2022, 6, 1), 360,
            )
            schedule = amortization_engine.generate_schedule(
                Decimal("200000.00"),
                Decimal("0.06000"),
                remaining_months,
                payment_day=1,
            )

            # Exact count assertions.
            assert len(result["labels"]) == len(schedule)
            assert len(result["principal"]) == len(schedule)
            assert len(result["interest"]) == len(schedule)
            assert result["account_name"] == "Test Mortgage"

            # Cross-verify first 3 rows and last row.
            check_indices = [0, 1, 2, len(schedule) - 1]
            for i in check_indices:
                assert result["principal"][i] == float(schedule[i].principal), (
                    f"Row {i}: principal chart={result['principal'][i]}, "
                    f"engine={float(schedule[i].principal)}"
                )
                assert result["interest"][i] == float(schedule[i].interest), (
                    f"Row {i}: interest chart={result['interest'][i]}, "
                    f"engine={float(schedule[i].interest)}"
                )
                expected_label = schedule[i].payment_date.strftime("%b %Y")
                assert result["labels"][i] == expected_label, (
                    f"Row {i}: label='{result['labels'][i]}', "
                    f"expected='{expected_label}'"
                )

            # Principal increases over time (amortization property:
            # as balance decreases, less goes to interest, more to principal).
            assert result["principal"][-1] > result["principal"][0]
            # Interest decreases over time (complement of above).
            assert result["interest"][-1] < result["interest"][0]

    def test_amortization_auto_loan_matches_engine(
        self, app, seed_user,
    ):
        """Auto loan chart data matches engine output exactly.

        Seeds $25k at 5%, 60 months, originated 2024-01-15, payment_day=15.
        Cross-verifies first 3 rows and last row.

        With today=2026-03-20:
            months_elapsed = (2026-2024)*12 + (3-1) = 26
            remaining_months = 60 - 26 = 34
        """
        with app.app_context():
            auto_loan_type = (
                db.session.query(AccountType)
                .filter_by(name="auto_loan").one()
            )
            account = Account(
                user_id=seed_user["user"].id,
                account_type_id=auto_loan_type.id,
                name="Test Auto Loan",
                current_anchor_balance=Decimal("25000.00"),
            )
            db.session.add(account)
            db.session.flush()

            params = AutoLoanParams(
                account_id=account.id,
                original_principal=Decimal("30000.00"),
                current_principal=Decimal("25000.00"),
                interest_rate=Decimal("0.05000"),
                term_months=60,
                origination_date=date(2024, 1, 15),
                payment_day=15,
            )
            db.session.add(params)
            db.session.commit()

            # Chart service result.
            result = chart_data_service.get_amortization_breakdown(
                user_id=seed_user["user"].id,
                account_id=account.id,
            )

            # Direct engine result for cross-verification.
            remaining_months = amortization_engine.calculate_remaining_months(
                date(2024, 1, 15), 60,
            )
            schedule = amortization_engine.generate_schedule(
                Decimal("25000.00"),
                Decimal("0.05000"),
                remaining_months,
                payment_day=15,
            )

            # Exact count assertions.
            assert len(result["labels"]) == len(schedule)
            assert len(result["principal"]) == len(schedule)
            assert len(result["interest"]) == len(schedule)
            assert result["account_name"] == "Test Auto Loan"

            # Cross-verify first 3 rows and last row.
            check_indices = [0, 1, 2, len(schedule) - 1]
            for i in check_indices:
                assert result["principal"][i] == float(schedule[i].principal), (
                    f"Row {i}: principal chart={result['principal'][i]}, "
                    f"engine={float(schedule[i].principal)}"
                )
                assert result["interest"][i] == float(schedule[i].interest), (
                    f"Row {i}: interest chart={result['interest'][i]}, "
                    f"engine={float(schedule[i].interest)}"
                )
                expected_label = schedule[i].payment_date.strftime("%b %Y")
                assert result["labels"][i] == expected_label, (
                    f"Row {i}: label='{result['labels'][i]}', "
                    f"expected='{expected_label}'"
                )

            # Amortization properties hold for auto loans too.
            assert result["principal"][-1] > result["principal"][0]
            assert result["interest"][-1] < result["interest"][0]
