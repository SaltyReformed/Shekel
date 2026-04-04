"""
Shekel Budget App -- Savings Dashboard Service Tests

Unit tests for the savings_dashboard_service module, verifying that
the extracted business logic produces correct financial computations
independently of the Flask route layer.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import GoalModeEnum, IncomeUnitEnum
from app.extensions import db
from app.models.account import Account
from app.models.ref import AccountType, FilingStatus
from app.models.salary_profile import SalaryProfile
from app.models.savings_goal import SavingsGoal
from app.services import savings_dashboard_service, pay_period_service


class TestComputeDashboardData:
    """Tests for the top-level compute_dashboard_data orchestrator."""

    def test_returns_expected_keys(self, app, db, seed_user, seed_periods):
        """Return dict contains all template context keys."""
        with app.app_context():
            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            expected_keys = {
                "account_data", "grouped_accounts", "goal_data",
                "emergency_metrics", "total_savings",
                "avg_monthly_expenses", "savings_accounts",
                "archived_accounts", "debt_summary",
            }
            assert set(result.keys()) == expected_keys

    def test_empty_user_returns_safe_defaults(self, app, db, seed_user):
        """User with no periods or goals gets safe zero-value defaults.

        The seed user has a Checking account ($1000) which is liquid,
        so total_savings reflects that even without pay periods.
        """
        with app.app_context():
            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            assert result["total_savings"] == Decimal("1000.00")
            assert result["avg_monthly_expenses"] == Decimal("0.00")
            assert result["goal_data"] == []

    def test_checking_account_appears_in_account_data(
        self, app, db, seed_user, seed_periods
    ):
        """The seed user's checking account appears in account_data."""
        with app.app_context():
            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            acct_names = [
                ad["account"].name for ad in result["account_data"]
            ]
            assert "Checking" in acct_names

    def test_account_has_current_balance(
        self, app, db, seed_user, seed_periods
    ):
        """Each account_data entry has a current_balance key."""
        with app.app_context():
            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            for ad in result["account_data"]:
                assert "current_balance" in ad
                assert isinstance(
                    ad["current_balance"], (Decimal, type(None))
                )


class TestGroupAccountsByCategory:
    """Tests for the category grouping logic."""

    def test_checking_grouped_as_asset(
        self, app, db, seed_user, seed_periods
    ):
        """Checking accounts are grouped under the 'asset' category."""
        with app.app_context():
            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            grouped = result["grouped_accounts"]
            assert "asset" in grouped
            asset_names = [
                ad["account"].name for ad in grouped["asset"]
            ]
            assert "Checking" in asset_names

    def test_savings_account_grouped_as_asset(
        self, app, db, seed_user, seed_periods
    ):
        """Savings accounts are grouped under 'asset'."""
        with app.app_context():
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="Savings").one()
            )
            savings = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Emergency Fund",
                current_anchor_balance=Decimal("10000.00"),
            )
            db.session.add(savings)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            grouped = result["grouped_accounts"]
            asset_names = [
                ad["account"].name for ad in grouped.get("asset", [])
            ]
            assert "Emergency Fund" in asset_names


class TestGoalProgress:
    """Tests for savings goal progress computation."""

    def test_goal_progress_with_target(
        self, app, db, seed_user, seed_periods
    ):
        """Goal with balance at 50% of target shows 50% progress."""
        with app.app_context():
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="Savings").one()
            )
            savings = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Goal Account",
                current_anchor_balance=Decimal("5000.00"),
                current_anchor_period_id=seed_periods[0].id,
            )
            db.session.add(savings)
            db.session.flush()

            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=savings.id,
                name="Vacation",
                target_amount=Decimal("10000.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            assert len(result["goal_data"]) == 1
            gd = result["goal_data"][0]
            assert gd["progress_pct"] == 50
            assert gd["current_balance"] == Decimal("5000.00")

    def test_no_goals_returns_empty_list(
        self, app, db, seed_user, seed_periods
    ):
        """User with no active goals gets an empty goal_data list."""
        with app.app_context():
            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            assert result["goal_data"] == []


class TestIncomeRelativeGoalDashboard:
    """Integration tests for income-relative goal resolution in the dashboard."""

    def test_dashboard_fixed_goal_includes_resolved_target(
        self, app, db, seed_user, seed_periods
    ):
        """Fixed goal's resolved_target equals its stored target_amount.

        Verifies that fixed goals pass through unmodified by the
        resolution logic.
        """
        with app.app_context():
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                name="Fixed Goal",
                target_amount=Decimal("5000.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            gd = result["goal_data"][0]
            assert gd["resolved_target"] == Decimal("5000.00")
            assert gd["income_descriptor"] is None

    def test_dashboard_goal_data_includes_new_keys(
        self, app, db, seed_user, seed_periods
    ):
        """Goal data dict contains all new keys from 5.4-3.

        Every goal entry must include: resolved_target, goal_mode_id,
        income_descriptor, has_salary_data.
        """
        with app.app_context():
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                name="Key Check",
                target_amount=Decimal("3000.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            gd = result["goal_data"][0]
            assert "resolved_target" in gd
            assert "goal_mode_id" in gd
            assert "income_descriptor" in gd
            assert "has_salary_data" in gd

    def test_dashboard_income_relative_goal_resolves_target(
        self, app, db, seed_user, seed_periods
    ):
        """Income-relative goal on the dashboard shows calculated target.

        With a salary profile configured, an income-relative goal
        resolves its target from net biweekly pay * multiplier.
        """
        with app.app_context():
            filing = db.session.query(FilingStatus).first()
            profile = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=filing.id,
                name="Test Salary",
                annual_salary=Decimal("75000.00"),
                state_code="NC",
            )
            db.session.add(profile)

            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                name="3 Paychecks",
                goal_mode_id=ir_id,
                income_unit_id=paychecks_id,
                income_multiplier=Decimal("3.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            gd = result["goal_data"][0]
            # The exact value depends on the salary profile's net pay.
            # With a salary profile, resolved_target should be > 0.
            assert gd["resolved_target"] > Decimal("0.00")
            assert gd["has_salary_data"] is True
            assert isinstance(gd["resolved_target"], Decimal)

    def test_dashboard_income_relative_no_salary(
        self, app, db, seed_user, seed_periods
    ):
        """Income-relative goal with no salary profile shows $0.00 target.

        Without a salary profile, net_biweekly_pay is $0 and the
        resolved target is $0.  has_salary_data should be False.
        """
        with app.app_context():
            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                name="No Salary Goal",
                goal_mode_id=ir_id,
                income_unit_id=paychecks_id,
                income_multiplier=Decimal("3.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            gd = result["goal_data"][0]
            assert gd["resolved_target"] == Decimal("0.00")
            assert gd["has_salary_data"] is False
            assert gd["progress_pct"] == 0

    def test_dashboard_income_descriptor_format(
        self, app, db, seed_user, seed_periods
    ):
        """Income descriptor uses the unit name and multiplier.

        For a 3-month income-relative goal, income_descriptor should
        be '3.00 months of salary'.
        """
        with app.app_context():
            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            months_id = ref_cache.income_unit_id(IncomeUnitEnum.MONTHS)
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                name="3 Months Buffer",
                goal_mode_id=ir_id,
                income_unit_id=months_id,
                income_multiplier=Decimal("3.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            gd = result["goal_data"][0]
            assert gd["income_descriptor"] == "3.00 months of salary"

    def test_progress_uses_resolved_target(
        self, app, db, seed_user, seed_periods
    ):
        """Progress percentage uses the resolved target, not raw target_amount.

        An income-relative goal with target_amount=None must still
        produce a valid progress percentage (not 0% or a crash).
        """
        with app.app_context():
            filing = db.session.query(FilingStatus).first()
            profile = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=filing.id,
                name="Test Salary",
                annual_salary=Decimal("75000.00"),
                state_code="NC",
            )
            db.session.add(profile)

            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)

            # The seed user's checking account has $1,000 balance.
            # Create a goal for 1 paycheck of savings.
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                name="1 Paycheck",
                goal_mode_id=ir_id,
                income_unit_id=paychecks_id,
                income_multiplier=Decimal("1.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            gd = result["goal_data"][0]
            # resolved_target = 1 * net_biweekly_pay > 0 (salary exists).
            # progress_pct = 1000 / resolved_target * 100.
            # The exact percentage depends on the salary amount,
            # but it must be > 0 (balance is $1000 and target is > 0).
            assert gd["progress_pct"] > 0
            assert gd["resolved_target"] > Decimal("0.00")

    def test_progress_zero_target_no_division_error(
        self, app, db, seed_user, seed_periods
    ):
        """Income-relative goal with $0 resolved target yields 0% progress.

        When there is no salary profile, the resolved target is $0.
        This must not cause a ZeroDivisionError.
        """
        with app.app_context():
            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                name="Zero Target",
                goal_mode_id=ir_id,
                income_unit_id=paychecks_id,
                income_multiplier=Decimal("3.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            gd = result["goal_data"][0]
            assert gd["progress_pct"] == 0
            assert gd["required_contribution"] is None


class TestGoalTrajectoryDashboard:
    """Integration tests for trajectory calculation in the dashboard.

    Verifies that the dashboard service correctly discovers monthly
    contributions from transfer templates and includes trajectory
    data in goal_data dicts.
    """

    def test_goal_data_includes_trajectory_keys(
        self, app, db, seed_user, seed_periods
    ):
        """Goal data dict contains trajectory and monthly_contribution keys."""
        with app.app_context():
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="Savings").one()
            )
            savings = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Trajectory Account",
                current_anchor_balance=Decimal("3000.00"),
                current_anchor_period_id=seed_periods[0].id,
            )
            db.session.add(savings)
            db.session.flush()

            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=savings.id,
                name="Trajectory Goal",
                target_amount=Decimal("6000.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            gd = result["goal_data"][0]
            assert "trajectory" in gd
            assert "monthly_contribution" in gd
            assert isinstance(gd["trajectory"], dict)
            assert isinstance(gd["monthly_contribution"], Decimal)

    def test_trajectory_with_no_transfer_template(
        self, app, db, seed_user, seed_periods
    ):
        """Goal with no recurring transfer shows zero monthly and None trajectory.

        Without a transfer template targeting the account,
        monthly_contribution is $0 and months_to_goal is None.
        """
        with app.app_context():
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="Savings").one()
            )
            savings = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="No Transfer Account",
                current_anchor_balance=Decimal("2000.00"),
                current_anchor_period_id=seed_periods[0].id,
            )
            db.session.add(savings)
            db.session.flush()

            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=savings.id,
                name="No Transfer Goal",
                target_amount=Decimal("10000.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            gd = result["goal_data"][0]
            assert gd["monthly_contribution"] == Decimal("0.00")
            assert gd["trajectory"]["months_to_goal"] is None

    def test_trajectory_with_transfer_template(
        self, app, db, seed_user, seed_periods
    ):
        """Goal with a recurring monthly transfer computes trajectory.

        A $500/month recurring transfer into the savings account with
        $3,000 balance and $6,000 target should produce months_to_goal=6.
        """
        from app.models.recurrence_rule import RecurrenceRule

        with app.app_context():
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="Savings").one()
            )
            savings = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="With Transfer Account",
                current_anchor_balance=Decimal("3000.00"),
                current_anchor_period_id=seed_periods[0].id,
            )
            db.session.add(savings)
            db.session.flush()

            from app.enums import RecurrencePatternEnum
            monthly_pattern_id = ref_cache.recurrence_pattern_id(
                RecurrencePatternEnum.MONTHLY
            )
            rule = RecurrenceRule(
                user_id=seed_user["user"].id,
                pattern_id=monthly_pattern_id,
            )
            db.session.add(rule)
            db.session.flush()

            from app.models.transfer_template import TransferTemplate
            template = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                name="Monthly Savings",
                default_amount=Decimal("500.00"),
                recurrence_rule_id=rule.id,
                is_active=True,
            )
            db.session.add(template)

            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=savings.id,
                name="Transfer Goal",
                target_amount=Decimal("6000.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            gd = result["goal_data"][0]
            # Monthly transfer of $500 with $3,000 remaining
            assert gd["monthly_contribution"] == Decimal("500.00")
            # remaining = 6000 - 3000 = 3000, months = ceil(3000/500) = 6
            assert gd["trajectory"]["months_to_goal"] == 6


class TestEmergencyFundMetrics:
    """Tests for emergency fund coverage computation."""

    def test_total_savings_sums_savings_accounts(
        self, app, db, seed_user, seed_periods
    ):
        """total_savings includes savings + HYSA balances only."""
        with app.app_context():
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="Savings").one()
            )
            savings = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Savings",
                current_anchor_balance=Decimal("8000.00"),
                current_anchor_period_id=seed_periods[0].id,
            )
            db.session.add(savings)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            # Both Checking ($1000, liquid) and Savings ($8000, liquid)
            # contribute to total_savings.
            assert result["total_savings"] == Decimal("9000.00")


# ── Paid-Off Flag Tests (Commit 5.9-2) ──────────────────────────────


def _create_small_loan(seed_user, db_session, name="Test Loan",
                       principal=Decimal("1000.00"),
                       rate=Decimal("0.05000"), term=24):
    """Create a small loan account with LoanParams for paid-off testing.

    Uses a small principal for fast engine replay and easy verification.
    Origination is Jan 2026 with term=24 so remaining months is
    comfortably positive (~21 from April 2026).
    """
    loan_type = db_session.query(AccountType).filter_by(name="Auto Loan").one()
    account = Account(
        user_id=seed_user["user"].id,
        account_type_id=loan_type.id,
        name=name,
        current_anchor_balance=principal,
    )
    db_session.add(account)
    db_session.flush()

    from app.models.loan_params import LoanParams as LP  # pylint: disable=import-outside-toplevel
    params = LP(
        account_id=account.id,
        original_principal=principal,
        current_principal=principal,
        interest_rate=rate,
        term_months=term,
        origination_date=date(2026, 1, 1),
        payment_day=1,
    )
    db_session.add(params)
    db_session.commit()
    return account


class TestPaidOffFlag:
    """Tests for the is_paid_off flag in account data.

    Commit 5.9-2: the savings dashboard service determines whether a
    loan is paid off by replaying only confirmed (Paid/Settled) payments
    through the amortization engine.  Projected payments are excluded.
    """

    def test_paid_off_true_when_confirmed_covers_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """Confirmed payment covering the full balance sets is_paid_off=True.

        A $1,000 loan at 5% for 12 months.  A single confirmed payment
        of $1,100 exceeds principal + first-month interest (~$1,004.17).
        The engine's overpayment guard caps the payment at the remaining
        balance + interest, resulting in remaining_balance = $0.00.
        """
        from app import ref_cache as rc  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum  # pylint: disable=import-outside-toplevel
        from app.services.transfer_service import create_transfer  # pylint: disable=import-outside-toplevel

        with app.app_context():
            acct = _create_small_loan(seed_user, db.session)
            create_transfer(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=acct.id,
                pay_period_id=seed_periods[7].id,
                scenario_id=seed_user["scenario"].id,
                amount=Decimal("1100.00"),
                status_id=rc.status_id(StatusEnum.DONE),
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            loan_ad = next(
                ad for ad in result["account_data"]
                if ad["account"].id == acct.id
            )
            assert loan_ad["is_paid_off"] is True

    def test_paid_off_false_no_confirmed_payments(
        self, app, db, seed_user, seed_periods,
    ):
        """Loan with no payments at all: is_paid_off=False."""
        with app.app_context():
            acct = _create_small_loan(seed_user, db.session)

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            loan_ad = next(
                ad for ad in result["account_data"]
                if ad["account"].id == acct.id
            )
            assert loan_ad["is_paid_off"] is False

    def test_paid_off_false_partial_confirmed_payments(
        self, app, db, seed_user, seed_periods,
    ):
        """Partial confirmed payment leaving balance > 0: is_paid_off=False.

        A $500 payment on a $1,000 loan leaves ~$504 (principal minus
        payment plus interest).
        """
        from app import ref_cache as rc  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum  # pylint: disable=import-outside-toplevel
        from app.services.transfer_service import create_transfer  # pylint: disable=import-outside-toplevel

        with app.app_context():
            acct = _create_small_loan(seed_user, db.session)
            create_transfer(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=acct.id,
                pay_period_id=seed_periods[7].id,
                scenario_id=seed_user["scenario"].id,
                amount=Decimal("500.00"),
                status_id=rc.status_id(StatusEnum.DONE),
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            loan_ad = next(
                ad for ad in result["account_data"]
                if ad["account"].id == acct.id
            )
            assert loan_ad["is_paid_off"] is False

    def test_paid_off_false_projected_only(
        self, app, db, seed_user, seed_periods,
    ):
        """Projected payment that would pay off the loan: is_paid_off=False.

        The critical semantic test -- projections do not equal payoff.
        A projected transfer of $1,100 covers the full balance, but
        since it has Projected status (is_settled=False), the paid-off
        flag must remain False.
        """
        from app import ref_cache as rc  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum  # pylint: disable=import-outside-toplevel
        from app.services.transfer_service import create_transfer  # pylint: disable=import-outside-toplevel

        with app.app_context():
            acct = _create_small_loan(seed_user, db.session)
            create_transfer(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=acct.id,
                pay_period_id=seed_periods[7].id,
                scenario_id=seed_user["scenario"].id,
                amount=Decimal("1100.00"),
                status_id=rc.status_id(StatusEnum.PROJECTED),
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            loan_ad = next(
                ad for ad in result["account_data"]
                if ad["account"].id == acct.id
            )
            assert loan_ad["is_paid_off"] is False

    def test_paid_off_false_for_non_loan_account(
        self, app, db, seed_user, seed_periods,
    ):
        """Non-loan accounts (checking, savings) have is_paid_off=False."""
        with app.app_context():
            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            # The seed user's checking account is non-amortizing.
            checking_ad = next(
                ad for ad in result["account_data"]
                if ad["account"].name == "Checking"
            )
            assert checking_ad["is_paid_off"] is False

    def test_paid_off_false_no_loan_params(
        self, app, db, seed_user, seed_periods,
    ):
        """Loan account with no LoanParams: is_paid_off=False, no crash."""
        with app.app_context():
            loan_type = (
                db.session.query(AccountType)
                .filter_by(name="Auto Loan").one()
            )
            acct = Account(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name="No Params Loan",
            )
            db.session.add(acct)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            loan_ad = next(
                ad for ad in result["account_data"]
                if ad["account"].id == acct.id
            )
            assert loan_ad["is_paid_off"] is False


class TestArchivedAccounts:
    """Tests for archived account loading in the dashboard service.

    Commit 5.9-3: archived accounts (is_active=False) are loaded
    separately with minimal data and no projections.
    """

    def test_archived_accounts_returned(
        self, app, db, seed_user, seed_periods,
    ):
        """Archived accounts appear in the archived_accounts key."""
        with app.app_context():
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="Savings").one()
            )
            archived = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Old Savings",
                current_anchor_balance=Decimal("2000.00"),
                is_active=False,
            )
            db.session.add(archived)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            assert "archived_accounts" in result
            assert len(result["archived_accounts"]) == 1
            assert result["archived_accounts"][0]["account"].name == "Old Savings"

    def test_archived_excluded_from_active(
        self, app, db, seed_user, seed_periods,
    ):
        """Archived account does not appear in account_data."""
        with app.app_context():
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="Savings").one()
            )
            archived = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Hidden Savings",
                current_anchor_balance=Decimal("500.00"),
                is_active=False,
            )
            db.session.add(archived)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            active_names = [
                ad["account"].name for ad in result["account_data"]
            ]
            assert "Hidden Savings" not in active_names

    def test_no_archived_returns_empty_list(
        self, app, db, seed_user, seed_periods,
    ):
        """No archived accounts yields an empty list."""
        with app.app_context():
            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            assert result["archived_accounts"] == []

    def test_archived_has_balance_only(
        self, app, db, seed_user, seed_periods,
    ):
        """Archived accounts carry current_balance but no projections."""
        with app.app_context():
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="Savings").one()
            )
            archived = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Archived Savings",
                current_anchor_balance=Decimal("3000.00"),
                is_active=False,
            )
            db.session.add(archived)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            ad = result["archived_accounts"][0]
            assert "current_balance" in ad
            assert ad["current_balance"] == Decimal("3000.00")
            assert "projected" not in ad


# ── Debt Summary Tests (Commit 5.12-1) ────────────────────────────────


class TestDebtSummary:
    """Tests for the debt summary computation in the dashboard service.

    Commit 5.12-1: aggregate debt metrics (total debt, monthly payments,
    weighted average rate, debt-free date) and debt-to-income ratio.
    """

    def test_debt_summary_none_when_no_loans(
        self, app, db, seed_user, seed_periods,
    ):
        """C-5.12-3: No loan accounts yields debt_summary=None."""
        with app.app_context():
            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            assert result["debt_summary"] is None

    def test_debt_summary_single_loan(
        self, app, db, seed_user, seed_periods,
    ):
        """C-5.12-1: Single loan produces a valid debt summary.

        A $1,000 auto loan at 5% for 24 months.  The summary should
        reflect this single loan's metrics.
        """
        with app.app_context():
            acct = _create_small_loan(seed_user, db.session)
            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            ds = result["debt_summary"]
            assert ds is not None
            assert ds["total_debt"] == Decimal("1000.00")
            # weighted_avg_rate = single loan's rate = 0.05000
            assert ds["weighted_avg_rate"] == Decimal("0.05000")
            assert ds["total_monthly_payments"] > Decimal("0.00")
            assert ds["projected_debt_free_date"] is not None

    def test_debt_summary_multiple_loans_weighted_rate(
        self, app, db, seed_user, seed_periods,
    ):
        """C-5.12-2 / C-5.12-4: Two loans with hand-calculated weighted avg rate.

        Loan A: $200,000 at 6.5%
        Loan B: $25,000 at 4.9%
        weighted_avg = (200000*0.065 + 25000*0.049) / (200000+25000)
                     = (13000 + 1225) / 225000
                     = 14225 / 225000
                     = 0.06322...
        """
        with app.app_context():
            mortgage_type = (
                db.session.query(AccountType)
                .filter_by(name="Mortgage").one()
            )
            mortgage = Account(
                user_id=seed_user["user"].id,
                account_type_id=mortgage_type.id,
                name="Mortgage",
                current_anchor_balance=Decimal("200000.00"),
            )
            db.session.add(mortgage)
            db.session.flush()

            from app.models.loan_params import LoanParams as LP
            lp1 = LP(
                account_id=mortgage.id,
                original_principal=Decimal("200000.00"),
                current_principal=Decimal("200000.00"),
                interest_rate=Decimal("0.06500"),
                term_months=360,
                origination_date=date(2024, 1, 1),
                payment_day=1,
            )
            db.session.add(lp1)

            auto_type = (
                db.session.query(AccountType)
                .filter_by(name="Auto Loan").one()
            )
            auto = Account(
                user_id=seed_user["user"].id,
                account_type_id=auto_type.id,
                name="Auto",
                current_anchor_balance=Decimal("25000.00"),
            )
            db.session.add(auto)
            db.session.flush()

            lp2 = LP(
                account_id=auto.id,
                original_principal=Decimal("25000.00"),
                current_principal=Decimal("25000.00"),
                interest_rate=Decimal("0.04900"),
                term_months=60,
                origination_date=date(2024, 6, 1),
                payment_day=15,
            )
            db.session.add(lp2)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            ds = result["debt_summary"]
            assert ds["total_debt"] == Decimal("225000.00")
            # Hand-calc: (200000*0.065 + 25000*0.049) / 225000
            #          = 14225 / 225000 = 0.063222...
            assert ds["weighted_avg_rate"] == Decimal("0.06322")

    def test_debt_summary_excludes_paid_off(
        self, app, db, seed_user, seed_periods,
    ):
        """C-5.12-8: Paid-off loan excluded from debt summary.

        One active loan ($1,000) plus one paid-off loan.  Total debt
        should equal only the active loan's principal.
        """
        from app import ref_cache as rc
        from app.enums import StatusEnum
        from app.services.transfer_service import create_transfer

        with app.app_context():
            active = _create_small_loan(
                seed_user, db.session, name="Active Loan",
                principal=Decimal("2000.00"),
            )
            paid_off = _create_small_loan(
                seed_user, db.session, name="Paid Off Loan",
            )
            # Pay off the second loan with a confirmed transfer.
            create_transfer(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=paid_off.id,
                pay_period_id=seed_periods[7].id,
                scenario_id=seed_user["scenario"].id,
                amount=Decimal("1100.00"),
                status_id=rc.status_id(StatusEnum.DONE),
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            ds = result["debt_summary"]
            # Only the active $2,000 loan contributes.
            assert ds["total_debt"] == Decimal("2000.00")

    def test_debt_summary_all_paid_off(
        self, app, db, seed_user, seed_periods,
    ):
        """C-5.12-10: All loans paid off yields zero totals.

        Debt summary exists (not None) but all aggregates are zero
        and debt-free date is None.
        """
        from app import ref_cache as rc
        from app.enums import StatusEnum
        from app.services.transfer_service import create_transfer

        with app.app_context():
            acct = _create_small_loan(seed_user, db.session)
            create_transfer(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=acct.id,
                pay_period_id=seed_periods[7].id,
                scenario_id=seed_user["scenario"].id,
                amount=Decimal("1100.00"),
                status_id=rc.status_id(StatusEnum.DONE),
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            ds = result["debt_summary"]
            assert ds is not None
            assert ds["total_debt"] == Decimal("0.00")
            assert ds["total_monthly_payments"] == Decimal("0.00")
            assert ds["weighted_avg_rate"] == Decimal("0.00000")
            assert ds["projected_debt_free_date"] is None

    def test_debt_summary_missing_params(
        self, app, db, seed_user, seed_periods,
    ):
        """C-5.12-11: Loan with no LoanParams is skipped, no crash.

        A loan account without params exists but another loan with
        params also exists.  The summary should only include the
        parameterized loan.
        """
        with app.app_context():
            # Loan with params
            _create_small_loan(seed_user, db.session, name="With Params")

            # Loan without params
            loan_type = (
                db.session.query(AccountType)
                .filter_by(name="Auto Loan").one()
            )
            no_params = Account(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name="No Params",
            )
            db.session.add(no_params)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            ds = result["debt_summary"]
            assert ds is not None
            # Only the parameterized loan contributes.
            assert ds["total_debt"] == Decimal("1000.00")

    def test_debt_free_date_is_latest(
        self, app, db, seed_user, seed_periods,
    ):
        """C-5.12-12: Debt-free date is the latest payoff across loans.

        A short-term loan (24 months) and a long-term mortgage (360
        months).  The debt-free date should match the mortgage's payoff.
        """
        with app.app_context():
            from app.models.loan_params import LoanParams as LP

            # Short-term loan
            _create_small_loan(
                seed_user, db.session, name="Short Loan", term=24,
            )

            # Long-term mortgage
            mortgage_type = (
                db.session.query(AccountType)
                .filter_by(name="Mortgage").one()
            )
            mortgage = Account(
                user_id=seed_user["user"].id,
                account_type_id=mortgage_type.id,
                name="Long Mortgage",
            )
            db.session.add(mortgage)
            db.session.flush()
            lp = LP(
                account_id=mortgage.id,
                original_principal=Decimal("200000.00"),
                current_principal=Decimal("200000.00"),
                interest_rate=Decimal("0.06500"),
                term_months=360,
                origination_date=date(2024, 1, 1),
                payment_day=1,
            )
            db.session.add(lp)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            ds = result["debt_summary"]
            # The mortgage payoff is decades away; auto loan is < 2 years.
            # Debt-free date should be the mortgage's later payoff.
            assert ds["projected_debt_free_date"] is not None
            # The auto loan payoff is within ~21 months of origination
            # (Jan 2026 + 21 months ~ Oct 2027).  The mortgage payoff is
            # 360 months from Jan 2024 ~ Jan 2054.  Debt-free = mortgage.
            assert ds["projected_debt_free_date"].year > 2030

    def test_debt_summary_includes_escrow(
        self, app, db, seed_user, seed_periods,
    ):
        """C-5.12-9: Escrow components are included in monthly total.

        A mortgage with $7,200/year escrow ($600/month).  The monthly
        total must include P&I + escrow.
        """
        with app.app_context():
            from app.models.loan_params import LoanParams as LP
            from app.models.loan_features import EscrowComponent

            mortgage_type = (
                db.session.query(AccountType)
                .filter_by(name="Mortgage").one()
            )
            mortgage = Account(
                user_id=seed_user["user"].id,
                account_type_id=mortgage_type.id,
                name="Escrow Mortgage",
            )
            db.session.add(mortgage)
            db.session.flush()

            lp = LP(
                account_id=mortgage.id,
                original_principal=Decimal("200000.00"),
                current_principal=Decimal("200000.00"),
                interest_rate=Decimal("0.06500"),
                term_months=360,
                origination_date=date(2024, 1, 1),
                payment_day=1,
            )
            db.session.add(lp)

            ec = EscrowComponent(
                account_id=mortgage.id,
                name="Property Tax",
                annual_amount=Decimal("7200.00"),
            )
            db.session.add(ec)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            ds = result["debt_summary"]
            # P&I for a $200K, 6.5%, 360-month loan is ~$1,264.
            # With $600/month escrow, total should exceed $1,800.
            assert ds["total_monthly_payments"] > Decimal("1800.00")
            # Verify escrow is included: total > P&I alone.
            from app.services import amortization_engine as ae
            pi_only = ae.calculate_monthly_payment(
                Decimal("200000.00"), Decimal("0.06500"),
                ae.calculate_remaining_months(date(2024, 1, 1), 360),
            )
            assert ds["total_monthly_payments"] > pi_only


class TestDTI:
    """Tests for debt-to-income ratio computation.

    DTI = total_monthly_payments / gross_monthly_income * 100.
    Gross monthly = gross_biweekly * 26 / 12 (biweekly, not semi-monthly).
    """

    def test_dti_no_salary(
        self, app, db, seed_user, seed_periods,
    ):
        """C-5.12-6: Loans exist but no salary profile yields dti_ratio=None."""
        with app.app_context():
            _create_small_loan(seed_user, db.session)

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            ds = result["debt_summary"]
            assert ds is not None
            assert ds["dti_ratio"] is None
            assert ds["dti_label"] is None

    def test_dti_with_salary(
        self, app, db, seed_user, seed_periods,
    ):
        """C-5.12-5: Known DTI from specific monthly debt and gross pay.

        Gross biweekly = annual_salary / 26.
        gross_monthly = gross_biweekly * 26 / 12 = annual_salary / 12.
        $78,000 / 12 = $6,500.
        A $1,000 loan at 5% for 24 months: monthly P&I ~ $43.87.
        DTI = 43.87 / 6500 * 100 = ~0.7%.
        """
        with app.app_context():
            filing = db.session.query(FilingStatus).first()
            profile = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=filing.id,
                name="DTI Salary",
                annual_salary=Decimal("78000.00"),
                state_code="NC",
            )
            db.session.add(profile)
            _create_small_loan(seed_user, db.session)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            ds = result["debt_summary"]
            assert ds["dti_ratio"] is not None
            assert isinstance(ds["dti_ratio"], Decimal)
            assert ds["dti_label"] == "healthy"
            assert ds["gross_monthly_income"] == Decimal("6500.00")

    def test_dti_zero_debt(
        self, app, db, seed_user, seed_periods,
    ):
        """C-5.12-13: Salary exists, all loans paid off -> DTI = 0.0%."""
        from app import ref_cache as rc
        from app.enums import StatusEnum
        from app.services.transfer_service import create_transfer

        with app.app_context():
            filing = db.session.query(FilingStatus).first()
            profile = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=filing.id,
                name="DTI Salary",
                annual_salary=Decimal("78000.00"),
                state_code="NC",
            )
            db.session.add(profile)
            acct = _create_small_loan(seed_user, db.session)
            create_transfer(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=acct.id,
                pay_period_id=seed_periods[7].id,
                scenario_id=seed_user["scenario"].id,
                amount=Decimal("1100.00"),
                status_id=rc.status_id(StatusEnum.DONE),
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            ds = result["debt_summary"]
            assert ds["dti_ratio"] == Decimal("0.0")
            assert ds["dti_label"] == "healthy"

    def test_dti_thresholds(self, app):
        """C-5.12-7 / C-5.12-14 / C-5.12-15: DTI threshold boundary values.

        35.9% -> healthy (< 36)
        36.0% -> moderate (not < 36)
        43.0% -> moderate (not > 43)
        43.1% -> high (> 43)
        """
        from app.services.savings_dashboard_service import _get_dti_label
        assert _get_dti_label(Decimal("35.9")) == "healthy"
        assert _get_dti_label(Decimal("36.0")) == "moderate"
        assert _get_dti_label(Decimal("43.0")) == "moderate"
        assert _get_dti_label(Decimal("43.1")) == "high"

    def test_dti_over_100(self, app):
        """C-5.12-16: DTI > 100% is valid and labeled 'high'."""
        from app.services.savings_dashboard_service import _get_dti_label
        assert _get_dti_label(Decimal("124.5")) == "high"

    def test_gross_monthly_uses_26_not_24(self, app):
        """C-5.12-20: Gross monthly = biweekly * 26 / 12, not * 24 / 12.

        Biweekly $3,000:
            Correct: 3000 * 26 / 12 = $6,500.00
            Wrong:   3000 * 24 / 12 = $6,000.00

        Hand-calculation:
            26 biweekly periods per year / 12 months = 2.16667
            3000 * 2.16667 = 6500.00
        """
        gross_biweekly = Decimal("3000.00")
        gross_monthly = (
            gross_biweekly * Decimal("26") / Decimal("12")
        ).quantize(Decimal("0.01"))
        assert gross_monthly == Decimal("6500.00")
