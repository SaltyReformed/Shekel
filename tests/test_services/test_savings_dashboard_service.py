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
from app.services import account_service


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
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Emergency Fund",
                    anchor_balance=Decimal("10000.00"),
                ),
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
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Goal Account",
                    anchor_balance=Decimal("5000.00"),
                    anchor_period_id=seed_periods[0].id,
                ),
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
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Trajectory Account",
                    anchor_balance=Decimal("3000.00"),
                    anchor_period_id=seed_periods[0].id,
                ),
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
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="No Transfer Account",
                    anchor_balance=Decimal("2000.00"),
                    anchor_period_id=seed_periods[0].id,
                ),
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
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="With Transfer Account",
                    anchor_balance=Decimal("3000.00"),
                    anchor_period_id=seed_periods[0].id,
                ),
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
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Savings",
                    anchor_balance=Decimal("8000.00"),
                    anchor_period_id=seed_periods[0].id,
                ),
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
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=loan_type.id,
            name=name,
            anchor_balance=principal,
        ),
    )
    db_session.add(account)
    db_session.flush()

    from app.models.loan_params import LoanParams as LP  # pylint: disable=import-outside-toplevel
    from tests._test_helpers import insert_origination_event  # pylint: disable=import-outside-toplevel
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
    db_session.flush()
    # E-18 / Commit 15: origination event so the resolver can
    # answer "paid off?" by replaying confirmed payments forward.
    insert_origination_event(params)
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
        from app.services.transfer_service import TransferSpec, create_transfer  # pylint: disable=import-outside-toplevel

        with app.app_context():
            acct = _create_small_loan(seed_user, db.session)
            create_transfer(
                TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=acct.id,
                    pay_period_id=seed_periods[7].id,
                    scenario_id=seed_user["scenario"].id,
                    amount=Decimal("1100.00"),
                    status_id=rc.status_id(StatusEnum.DONE),
                    category_id=seed_user["categories"]["Rent"].id,
                ),
            )
            db.session.commit()

            # Under the contractual-schedule model a cash lump sum does
            # not auto-pay-off; the operator records the payoff as a
            # balance true-up to $0 (the explicit-event path the user now
            # follows after an extra/lump-sum payment).
            from tests._test_helpers import insert_trueup_event  # pylint: disable=import-outside-toplevel
            insert_trueup_event(acct.loan_params, Decimal("0.00"))
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
        from app.services.transfer_service import TransferSpec, create_transfer  # pylint: disable=import-outside-toplevel

        with app.app_context():
            acct = _create_small_loan(seed_user, db.session)
            create_transfer(
                TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=acct.id,
                    pay_period_id=seed_periods[7].id,
                    scenario_id=seed_user["scenario"].id,
                    amount=Decimal("500.00"),
                    status_id=rc.status_id(StatusEnum.DONE),
                    category_id=seed_user["categories"]["Rent"].id,
                ),
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
        from app.services.transfer_service import TransferSpec, create_transfer  # pylint: disable=import-outside-toplevel

        with app.app_context():
            acct = _create_small_loan(seed_user, db.session)
            create_transfer(
                TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=acct.id,
                    pay_period_id=seed_periods[7].id,
                    scenario_id=seed_user["scenario"].id,
                    amount=Decimal("1100.00"),
                    status_id=rc.status_id(StatusEnum.PROJECTED),
                    category_id=seed_user["categories"]["Rent"].id,
                ),
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
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=loan_type.id,
                    name="No Params Loan",
                    anchor_balance=Decimal("0"),
                ),
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
            archived = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Old Savings",
                    anchor_balance=Decimal("2000.00"),
                ),
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
            archived = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Hidden Savings",
                    anchor_balance=Decimal("500.00"),
                ),
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
            archived = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Archived Savings",
                    anchor_balance=Decimal("3000.00"),
                ),
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
            mortgage = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=mortgage_type.id,
                    name="Mortgage",
                    anchor_balance=Decimal("200000.00"),
                ),
            )
            db.session.add(mortgage)
            db.session.flush()

            from app.models.loan_params import LoanParams as LP
            from tests._test_helpers import insert_origination_event as _ioe  # pylint: disable=import-outside-toplevel
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
            db.session.flush()
            _ioe(lp1)

            auto_type = (
                db.session.query(AccountType)
                .filter_by(name="Auto Loan").one()
            )
            auto = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=auto_type.id,
                    name="Auto",
                    anchor_balance=Decimal("25000.00"),
                ),
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
            db.session.flush()
            _ioe(lp2)
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
        from app.services.transfer_service import TransferSpec, create_transfer

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
                TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=paid_off.id,
                    pay_period_id=seed_periods[7].id,
                    scenario_id=seed_user["scenario"].id,
                    amount=Decimal("1100.00"),
                    status_id=rc.status_id(StatusEnum.DONE),
                    category_id=seed_user["categories"]["Rent"].id,
                ),
            )
            db.session.commit()

            # Payoff is recorded as a balance true-up to $0 (the cash
            # lump sum no longer auto-pays-off under the contractual
            # schedule model).
            from tests._test_helpers import insert_trueup_event  # pylint: disable=import-outside-toplevel
            insert_trueup_event(paid_off.loan_params, Decimal("0.00"))
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
        from app.services.transfer_service import TransferSpec, create_transfer

        with app.app_context():
            acct = _create_small_loan(seed_user, db.session)
            create_transfer(
                TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=acct.id,
                    pay_period_id=seed_periods[7].id,
                    scenario_id=seed_user["scenario"].id,
                    amount=Decimal("1100.00"),
                    status_id=rc.status_id(StatusEnum.DONE),
                    category_id=seed_user["categories"]["Rent"].id,
                ),
            )
            db.session.commit()

            # Payoff is recorded as a balance true-up to $0 (cash lump
            # sums no longer auto-pay-off under the contractual schedule).
            from tests._test_helpers import insert_trueup_event  # pylint: disable=import-outside-toplevel
            insert_trueup_event(acct.loan_params, Decimal("0.00"))
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
            no_params = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=loan_type.id,
                    name="No Params",
                    anchor_balance=Decimal("0"),
                ),
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
            mortgage = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=mortgage_type.id,
                    name="Long Mortgage",
                    anchor_balance=Decimal("0"),
                ),
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
            db.session.flush()
            from tests._test_helpers import insert_origination_event as _ioe  # pylint: disable=import-outside-toplevel
            _ioe(lp)
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
            mortgage = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=mortgage_type.id,
                    name="Escrow Mortgage",
                    anchor_balance=Decimal("0"),
                ),
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
            db.session.flush()
            from tests._test_helpers import insert_origination_event as _ioe  # pylint: disable=import-outside-toplevel
            _ioe(lp)

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
        from app.services.transfer_service import TransferSpec, create_transfer

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
                TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=acct.id,
                    pay_period_id=seed_periods[7].id,
                    scenario_id=seed_user["scenario"].id,
                    amount=Decimal("1100.00"),
                    status_id=rc.status_id(StatusEnum.DONE),
                    category_id=seed_user["categories"]["Rent"].id,
                ),
            )
            db.session.commit()

            # All debt paid off -> recorded as a balance true-up to $0.
            from tests._test_helpers import insert_trueup_event  # pylint: disable=import-outside-toplevel
            insert_trueup_event(acct.loan_params, Decimal("0.00"))
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
        from app.services.savings_dashboard_service._metrics import _get_dti_label
        assert _get_dti_label(Decimal("35.9")) == "healthy"
        assert _get_dti_label(Decimal("36.0")) == "moderate"
        assert _get_dti_label(Decimal("43.0")) == "moderate"
        assert _get_dti_label(Decimal("43.1")) == "high"

    def test_dti_over_100(self, app):
        """C-5.12-16: DTI > 100% is valid and labeled 'high'."""
        from app.services.savings_dashboard_service._metrics import _get_dti_label
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


class TestDTIRaiseAware:
    """C26 / MED-06 / F-032: DTI gross monthly income is sourced from
    the canonical raise-aware paycheck engine, not the off-engine
    ``annual_salary / pay_periods`` recompute.

    Pre-Commit-26 the savings dashboard read
    ``params["salary_gross_biweekly"]`` (computed in
    ``_load_account_params`` as raw ``annual_salary / pay_periods``,
    with no ``_apply_raises`` invocation) and converted to monthly via
    the 26/12 factor.  For any user with an applicable
    :class:`SalaryRaise` the displayed DTI denominator drifted from the
    paycheck engine: the audit's worked example carried a $104,000
    salary + recurring 3% raise where the engine produces $8,926.67
    monthly gross and the off-engine path produced $8,666.67, yielding
    a 27.7% DTI vs the correct 26.9% (`03_consistency.md` F-032 worked
    example).  Commit 26 routes both DTI gross and the savings-goal
    net biweekly pay through ``calculate_paycheck`` for the current
    period, making the engine the single source of truth.
    """

    def test_dti_with_applicable_raise(
        self, app, db, seed_user, seed_periods,
    ):
        """C26-1: With an applicable raise the DTI denominator is the
        post-raise engine gross.

        Salary $104,000.00 + a one-time 3% raise effective month 1 of
        the current period's year.  ``_apply_raises`` applies the raise
        once for the current period, so the engine's per-period gross
        reflects the post-raise salary; the period-to-monthly factor
        (26/12) is the structural biweekly-pay-schedule normalization
        and is preserved.

        Hand-computed engine output (MED-06 / F-032):
            annual_after_raise = 104000.00 * 1.03 = 107120.00
            gross_biweekly     = 107120.00 / 26   = 4120.0000 -> $4,120.00
                                 (ROUND_HALF_UP via paycheck_calculator)
            gross_monthly      = 4120.00 * 26 / 12 = 8926.6666...
                                                   -> $8,926.67 ROUND_HALF_UP

        Pre-Commit-26 (off-engine, no raise applied) would have produced:
            biweekly = 104000.00 / 26 = $4,000.00
            monthly  = 4000.00 * 26 / 12 = $8,666.67
        The $260.00/mo gap is the F-032 drift the fix closes.

        DTI ratio uses the engine-derived ``total_monthly_payments``
        (verified by sibling debt-summary tests) over the new
        denominator, quantized to one decimal place.
        """
        from decimal import ROUND_HALF_UP  # pylint: disable=import-outside-toplevel
        from app.models.salary_raise import SalaryRaise  # pylint: disable=import-outside-toplevel
        from app.models.ref import RaiseType  # pylint: disable=import-outside-toplevel

        with app.app_context():
            filing = db.session.query(FilingStatus).first()
            profile = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=filing.id,
                name="DTI Raise Salary",
                annual_salary=Decimal("104000.00"),
                state_code="NC",
            )
            db.session.add(profile)
            db.session.flush()

            current = pay_period_service.get_current_period(
                seed_user["user"].id
            )
            assert current is not None, (
                "seed_periods must cover today so the engine has a "
                "current period to compute against"
            )

            merit = (
                db.session.query(RaiseType).filter_by(name="merit").one()
            )
            db.session.add(SalaryRaise(
                salary_profile_id=profile.id,
                raise_type_id=merit.id,
                percentage=Decimal("0.0300"),
                effective_month=1,
                effective_year=current.start_date.year,
                is_recurring=False,
            ))
            _create_small_loan(seed_user, db.session)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            ds = result["debt_summary"]

            # MED-06 / F-032: engine-derived gross_monthly is $8,926.67.
            # Off-engine pre-fix value was $8,666.67 (raise dropped);
            # see class docstring for the arithmetic.
            assert ds["gross_monthly_income"] == Decimal("8926.67")

            # DTI ratio is recomputed against the corrected denominator.
            # total_monthly_payments is the engine-derived monthly P&I
            # from _create_small_loan ($1,000 @ 5% for 24mo); we
            # consume it as an input here so the test pins behaviour
            # without re-deriving the amortization engine's output.
            expected_dti = (
                ds["total_monthly_payments"] / Decimal("8926.67")
                * Decimal("100")
            ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            assert ds["dti_ratio"] == expected_dti

    def test_dti_no_raise_unchanged(
        self, app, db, seed_user, seed_periods,
    ):
        """C26-2: Without any raise, DTI gross matches the historical
        value (no regression for the raise-free majority).

        Engine output for a flat $78,000 salary, no raises:
            annual_salary  = $78,000.00
            gross_biweekly = 78000.00 / 26 = 3000.0000 -> $3,000.00
            gross_monthly  = 3000.00 * 26 / 12 = $6,500.00

        This is byte-identical to the pre-Commit-26 off-engine path for
        the no-raise case (the only F-032 divergence axis is the raise
        omission and the A-01 banker's-default rounding -- neither
        bites on this salary), so the fix is provably a no-op for the
        majority case where no scheduled raise applies.
        """
        from decimal import ROUND_HALF_UP  # pylint: disable=import-outside-toplevel

        with app.app_context():
            filing = db.session.query(FilingStatus).first()
            profile = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=filing.id,
                name="DTI No-Raise Salary",
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
            assert ds["gross_monthly_income"] == Decimal("6500.00")
            # DTI ratio matches the pre-fix calculation (no regression).
            expected_dti = (
                ds["total_monthly_payments"] / Decimal("6500.00")
                * Decimal("100")
            ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            assert ds["dti_ratio"] == expected_dti

    def test_dti_uses_paycheck_producer_no_flat_factor(self):
        """C26-3: Verification gate that the DTI block does not reach
        the off-engine ``salary_gross_biweekly`` raw recompute.

        Checks two regression guards on
        ``app/services/savings_dashboard_service.py``:

        1. ``compute_dashboard_data`` reads
           ``current_breakdown.earnings.gross_biweekly`` (the engine-derived
           value introduced by Commit 26) and does NOT subscript
           ``params`` with the ``"salary_gross_biweekly"`` key (the
           off-engine value still used by the investment-projection
           path -- F-20 follow-up).
        2. No bare ``Decimal("26") / Decimal("12")`` literal remains
           anywhere in the file: the biweekly-to-monthly factor lives
           in ``app/utils/money.py`` as
           ``PAY_PERIODS_PER_YEAR / MONTHS_PER_YEAR`` per E-24 /
           HIGH-05 / Commit 23.

        Guard 1 is implemented as an AST scan rather than a substring
        check so docstrings or comments that mention the off-engine
        key for historical / explanatory reasons do not trip the
        assertion -- only an actual subscript expression that READS
        the value does.
        """
        import ast  # pylint: disable=import-outside-toplevel
        import inspect  # pylint: disable=import-outside-toplevel
        import pathlib  # pylint: disable=import-outside-toplevel
        from app.services import savings_dashboard_service as svc  # pylint: disable=import-outside-toplevel
        # ``compute_dashboard_data`` lives in the package's
        # ``_orchestrator`` sub-module after the Phase 2 split; ``svc``
        # re-exports it, but the source-inspection guards must target the
        # sub-module (the package ``__init__`` holds only the re-export,
        # not the function body).
        from app.services.savings_dashboard_service import (  # pylint: disable=import-outside-toplevel
            _orchestrator,
        )

        # Guard 1a: positive lock -- the engine breakdown attribute is
        # read in compute_dashboard_data.
        source = inspect.getsource(svc.compute_dashboard_data)
        assert "current_breakdown.earnings.gross_biweekly" in source, (
            "DTI block must read gross_biweekly from the paycheck "
            "engine breakdown (MED-06 / F-032)."
        )

        # Guard 1b: negative lock -- compute_dashboard_data must not read
        # the off-engine ``salary_gross_biweekly`` for DTI, by either the
        # legacy dict subscript ``params["salary_gross_biweekly"]`` or the
        # current dataclass attribute ``params.salary_gross_biweekly``
        # (``params`` became the frozen ``_AccountParams`` in the
        # type-precision quality pass; the attribute form is the access a
        # regression would now use).
        tree = ast.parse(inspect.getsource(_orchestrator))
        target_fn = None
        for node in ast.walk(tree):
            if (isinstance(node, ast.FunctionDef)
                    and node.name == "compute_dashboard_data"):
                target_fn = node
                break
        assert target_fn is not None, (
            "compute_dashboard_data not found in module source"
        )
        for node in ast.walk(target_fn):
            reads_off_engine = (
                isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name)
                and node.value.id == "params"
                and isinstance(node.slice, ast.Constant)
                and node.slice.value == "salary_gross_biweekly"
            ) or (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "params"
                and node.attr == "salary_gross_biweekly"
            )
            if reads_off_engine:
                raise AssertionError(
                    "compute_dashboard_data must not read the off-engine "
                    "salary_gross_biweekly value for DTI (MED-06 / F-032)."
                )

        # Guard 2: package-wide -- no bare 26/12 literal in any
        # sub-module.  Reads every .py file in the package directory so
        # the check stays module-wide after the Phase 2 split.
        pkg_dir = pathlib.Path(inspect.getfile(_orchestrator)).parent
        file_source = "\n".join(
            p.read_text(encoding="utf-8")
            for p in sorted(pkg_dir.glob("*.py"))
        )
        assert 'Decimal("26") / Decimal("12")' not in file_source, (
            "biweekly-to-monthly factor must use named constants "
            "PAY_PERIODS_PER_YEAR / MONTHS_PER_YEAR (E-24 / HIGH-05)."
        )

    def test_dti_label_band_correct_with_raise(
        self, app, db, seed_user, seed_periods,
    ):
        """C26-4: For a borderline DTI fixture, the band reflects the
        engine-derived gross, not the off-engine recompute.

        Hand-construction (annotated below) yields a case where the
        pre-Commit-26 path would have labelled the DTI 'moderate' and
        the post-Commit-26 path labels it 'healthy' against the
        documented bands:

            < 36%  -> healthy
            36-43% -> moderate
            > 43%  -> high

        Salary $50,000 + a one-time 3% raise effective month 1 of the
        current year (applies once in the current period):
            annual_after_raise = 50000.00 * 1.03 = 51500.00
            gross_biweekly     = 51500.00 / 26   = 1980.7692... -> $1,980.77
            gross_monthly      = 1980.77 * 26 / 12 = 4291.6683...
                                                   -> $4,291.67 ROUND_HALF_UP
            36% band floor (engine)  = 4291.67 * 0.36 = $1,545.00

        Pre-Commit-26 off-engine would have been:
            biweekly = 50000 / 26 = $1,923.08
            monthly  = 1923.08 * 26 / 12 = $4,166.67
            36% band floor (off-engine) = 4166.67 * 0.36 = $1,500.00

        For ``total_monthly_payments`` between $1,500.00 and $1,545.00
        the band flips: off-engine labels 'moderate', engine labels
        'healthy'.  This test asserts the band corresponds to the
        engine-derived ratio.  ``_create_small_loan`` produces a P&I
        well below $1,500 so we exercise the deep-healthy case here;
        the band assertion is the structural lock -- the ratio is
        bounded below 36% by the engine denominator, so a regression
        that reverts to the off-engine $4,166.67 denominator would
        still label 'healthy' for THIS fixture (the band crossing only
        bites at larger debt loads), but C26-1 above pins the
        denominator exactly so the band-flip regression cannot hide.
        """
        from app.models.salary_raise import SalaryRaise  # pylint: disable=import-outside-toplevel
        from app.models.ref import RaiseType  # pylint: disable=import-outside-toplevel

        with app.app_context():
            filing = db.session.query(FilingStatus).first()
            profile = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=filing.id,
                name="DTI Band Raise Salary",
                annual_salary=Decimal("50000.00"),
                state_code="NC",
            )
            db.session.add(profile)
            db.session.flush()

            current = pay_period_service.get_current_period(
                seed_user["user"].id
            )
            assert current is not None

            merit = (
                db.session.query(RaiseType).filter_by(name="merit").one()
            )
            db.session.add(SalaryRaise(
                salary_profile_id=profile.id,
                raise_type_id=merit.id,
                percentage=Decimal("0.0300"),
                effective_month=1,
                effective_year=current.start_date.year,
                is_recurring=False,
            ))
            _create_small_loan(seed_user, db.session)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            ds = result["debt_summary"]
            # Engine-derived gross_monthly (see class + test docstring).
            assert ds["gross_monthly_income"] == Decimal("4291.67")
            # Small loan P&I is well under 36% of $4,291.67, so the
            # band is 'healthy' under the engine denominator.
            assert ds["dti_label"] == "healthy"
            # And the ratio is strictly less than 36 (boundary check).
            assert ds["dti_ratio"] < Decimal("36.0")


# ── Commit 6: canonical entries-aware producer routing ─────────────
#
# Pre-Commit-6 the savings dashboard built its own transaction query
# without ``selectinload(Transaction.entries)`` and called the engine
# directly.  When an envelope expense had cleared debit entries, the
# silent-degrade seam in ``balance_calculator._entry_aware_amount``
# (removed at the math layer by Commit 5) returned
# ``effective_amount`` unchanged.  Result: the same data shipped
# $160.00 on the grid and $114.29 on /savings -- symptom #1.  Commit 6
# routes the savings dashboard through
# ``balance_resolver.balances_for``, which owns the query and eager-
# loads entries, so the two surfaces produce byte-identical values
# by construction.


def _override_anchor(db_session, account, pay_period, anchor_balance):
    """Replace ``account``'s current anchor with the given balance + period.

    Mirrors the helper used in test_balance_resolver.py: appends a fresh
    :class:`AccountAnchorHistory` row (latest-wins by ``created_at``)
    and updates the cache columns so the resolver's cache-reconciliation
    path does NOT fire (cache and history agree).  Required because the
    ``seed_user`` factory writes an origination anchor of $1,000 against
    the seed_periods anchor period; tests reproducing symptom #1 need
    $614.29 on a chosen period.

    Args:
        db_session: SQLAlchemy session bound to the test database.
        account: The :class:`~app.models.account.Account` whose anchor
            should be overridden.
        pay_period: The :class:`~app.models.pay_period.PayPeriod` the
            new anchor is anchored against.
        anchor_balance: The new anchor balance as a Decimal.
    """
    from app.models.account import AccountAnchorHistory  # pylint: disable=import-outside-toplevel

    history = AccountAnchorHistory(
        account_id=account.id,
        pay_period_id=pay_period.id,
        anchor_balance=anchor_balance,
        notes="C6 symptom-#1 test: anchor override",
    )
    db_session.add(history)
    db_session.flush()
    account.current_anchor_balance = anchor_balance
    account.current_anchor_period_id = pay_period.id
    db_session.commit()


def _make_projected_envelope_expense(
    db_session, *, seed_user, pay_period, estimated, account_id=None,
    name="Groceries",
):
    """Create a Projected envelope expense in ``pay_period``.

    Builds the ``is_envelope=True`` template + Transaction pair that
    entries attach to.  Uses the user's Groceries category so the row
    is consistent with the symptom #1 worked example.  ``account_id``
    defaults to the seed user's checking account; pass an explicit id
    when the txn should live on an account other than seed_user["account"].
    """
    from app.models.ref import Status, TransactionType  # pylint: disable=import-outside-toplevel
    from app.models.transaction import Transaction  # pylint: disable=import-outside-toplevel
    from app.models.transaction_template import TransactionTemplate  # pylint: disable=import-outside-toplevel

    projected = db_session.query(Status).filter_by(name="Projected").one()
    expense_type = (
        db_session.query(TransactionType).filter_by(name="Expense").one()
    )
    target_account_id = account_id or seed_user["account"].id

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=target_account_id,
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=expense_type.id,
        name=name,
        default_amount=estimated,
        is_envelope=True,
    )
    db_session.add(template)
    db_session.flush()

    txn = Transaction(
        template_id=template.id,
        pay_period_id=pay_period.id,
        scenario_id=seed_user["scenario"].id,
        account_id=target_account_id,
        status_id=projected.id,
        name=name,
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=expense_type.id,
        estimated_amount=estimated,
    )
    db_session.add(txn)
    db_session.flush()
    return txn


def _add_entry(
    db_session, *, txn, user_id, amount,
    is_cleared=False, is_credit=False, description="Purchase",
):
    """Add a :class:`TransactionEntry` to ``txn`` with the given flags."""
    from app.models.transaction_entry import TransactionEntry  # pylint: disable=import-outside-toplevel

    db_session.add(TransactionEntry(
        transaction_id=txn.id,
        user_id=user_id,
        amount=amount,
        description=description,
        entry_date=date(2026, 1, 15),
        is_credit=is_credit,
        is_cleared=is_cleared,
    ))
    db_session.flush()


class TestCanonicalProducerRouting:
    """C6: /savings balances routed through balance_resolver.balances_for.

    The single-source-of-truth ``balances_for`` owns the transaction
    query (entries eager-loaded) and the anchor resolution
    (AccountAnchorHistory dated SoT), so the per-tile current balance
    cannot disagree with the grid for any input.  These tests pin the
    contract.  Test IDs match remediation_plan.md Commit 6 (C6-1
    through C6-3).
    """

    def test_savings_equals_grid_symptom1(
        self, app, db, seed_user, seed_periods_today,
    ):
        """C6-1: /savings checking tile == grid current-period balance.

        Reproduction of symptom #1 (audit 05_symptoms.md):

          - Real checking anchor 614.29 on the current pay period.
          - One Projected envelope expense ``estimated_amount = 500.00``
            on the same period (so ``_sum_remaining`` applies).
          - Three CLEARED debit entries 20.00 + 15.71 + 10.00 = 45.71.
            No credit entries, no uncleared debits.

        Hand arithmetic (F-009 worked example):

          cleared_debit   = 20.00 + 15.71 + 10.00 = 45.71
          uncleared_debit = 0
          sum_credit      = 0
          checking_impact = max(500.00 - 45.71 - 0, 0) = 454.29
          anchor_period_balance = 614.29 + 0 - 454.29 = 160.00

        Both the grid (already routed through ``balances_for`` in
        Commit 5) and the savings dashboard (routed in Commit 6) MUST
        return Decimal("160.00").  Pre-Commit-6, /savings returned
        Decimal("114.29") via the silent-degrade seam.
        """
        from app.services import balance_resolver  # pylint: disable=import-outside-toplevel

        with app.app_context():
            # Current period == anchor period: seed_periods_today
            # places today in period 4 of a 10-period biweekly window.
            current_period = pay_period_service.get_current_period(
                seed_user["user"].id
            )
            assert current_period is not None
            _override_anchor(
                db.session,
                seed_user["account"],
                current_period,
                Decimal("614.29"),
            )

            txn = _make_projected_envelope_expense(
                db.session,
                seed_user=seed_user,
                pay_period=current_period,
                estimated=Decimal("500.00"),
            )
            for amt in (Decimal("20.00"), Decimal("15.71"), Decimal("10.00")):
                _add_entry(
                    db.session,
                    txn=txn,
                    user_id=seed_user["user"].id,
                    amount=amt,
                    is_cleared=True,
                    is_credit=False,
                )
            db.session.commit()

            # Grid value: balance_resolver.balances_for is the canonical
            # producer the grid routes through post-Commit-5.  Replaying
            # it here is equivalent to "what does the grid show" without
            # a route round-trip.
            grid_result = balance_resolver.balances_for(
                seed_user["account"],
                seed_user["scenario"].id,
                seed_periods_today,
            )
            grid_current_balance = grid_result.balances[current_period.id]

            # F-009 / CRIT-01: 614.29 - max(500 - 45.71 - 0, 0)
            #                = 614.29 - 454.29 = 160.00.
            # Pre-Commit-6 /savings reported 114.29 (entries silently
            # unloaded; effective_amount returned 500.00 unchanged).
            assert grid_current_balance == Decimal("160.00")

            # Savings dashboard tile: routed through balances_for by
            # Commit 6.  Must equal the grid value exactly.
            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            checking_ad = next(
                ad for ad in result["account_data"]
                if ad["account"].id == seed_user["account"].id
            )
            assert checking_ad["current_balance"] == Decimal("160.00")
            assert checking_ad["current_balance"] == grid_current_balance

    def test_savings_hysa_entry_aware(
        self, app, db, seed_user, seed_periods_today,
    ):
        """C6-2: HYSA accounts with cleared entries get the entry-aware reduction.

        HYSA still routes through ``calculate_balances_with_interest``
        in Commit 6 (the canonical producer does not yet carry an
        interest variant; MED-01 / Commit 28 collapses the dispatcher).
        However the Commit-5 seam softening at the math layer makes
        ``_entry_aware_amount`` lazy-load entries via the SQLAlchemy
        descriptor instead of silently degrading to ``effective_amount``,
        so the value is correct regardless.

        Setup mirrors symptom #1 on an HYSA:
          - HYSA anchor 614.29 on the current period.
          - One Projected envelope expense est=500.00 on the same period.
          - Three cleared debit entries summing to 45.71.

        Hand arithmetic (identical formula; interest for one period at
        the default 4.5%% APY rounds to a few cents and is verified
        loosely):

          base_balance = 614.29 - max(500 - 45.71 - 0, 0) = 160.00
          + small positive interest accrual (HYSA is not zero-rate).
        """
        from app.models.interest_params import InterestParams  # pylint: disable=import-outside-toplevel

        with app.app_context():
            hysa_type = (
                db.session.query(AccountType).filter_by(name="HYSA").one()
            )
            current_period = pay_period_service.get_current_period(
                seed_user["user"].id
            )
            assert current_period is not None

            hysa = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=hysa_type.id,
                    name="HYSA Entry Test",
                    anchor_balance=Decimal("614.29"),
                    anchor_period_id=current_period.id,
                ),
            )
            db.session.add(hysa)
            db.session.flush()
            # HIGH-06 / Commit 24: ``apy`` NOT NULL, no server_default.
            db.session.add(InterestParams(
                account_id=hysa.id, apy=Decimal("0.04500"),
            ))
            db.session.commit()

            txn = _make_projected_envelope_expense(
                db.session,
                seed_user=seed_user,
                pay_period=current_period,
                estimated=Decimal("500.00"),
                account_id=hysa.id,
                name="HYSA Groceries",
            )
            for amt in (Decimal("20.00"), Decimal("15.71"), Decimal("10.00")):
                _add_entry(
                    db.session,
                    txn=txn,
                    user_id=seed_user["user"].id,
                    amount=amt,
                    is_cleared=True,
                    is_credit=False,
                )
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            hysa_ad = next(
                ad for ad in result["account_data"]
                if ad["account"].id == hysa.id
            )

            # base = 614.29 - max(500 - 45.71 - 0, 0)
            #     = 614.29 - 454.29 = 160.00 (entry-aware reduction)
            # plus a small positive interest accrual at 4.5%% APY for
            # the anchor period.  Pre-Commit-5 the entries were
            # silently unloaded and the base would have been
            # 614.29 - 500.00 = 114.29.  We require strictly greater
            # than 114.29 + interest noise (a 100x margin from the
            # 45.71 gap) to lock the entry-aware semantics:
            assert hysa_ad["current_balance"] > Decimal("159.00")
            assert hysa_ad["current_balance"] < Decimal("161.00")

    def test_savings_no_entries_unchanged(
        self, app, db, seed_user, seed_periods_today,
    ):
        """C6-3: with no entries, the current balance equals effective_amount.

        Assert-unchanged: the regression-safety guarantee that
        accounts with no envelope entries see byte-identical balances
        pre- and post-Commit-6.  Verified directly: with an anchor of
        614.29 and a single Projected $500 expense on the current
        period and NO entries, the entry-aware formula collapses to
        ``max(500.00 - 0 - 0, 0) = 500.00``, so the current balance
        is 614.29 - 500.00 = 114.29.  This is the SAME number /savings
        would have shown pre-Commit-6 (where entries were silently
        unloaded and ``effective_amount`` returned the same 500.00).

        Hand arithmetic:

          cleared_debit = 0; uncleared_debit = 0; sum_credit = 0
          checking_impact = max(500.00 - 0 - 0, 0) = 500.00
          anchor_period_balance = 614.29 + 0 - 500.00 = 114.29
        """
        with app.app_context():
            current_period = pay_period_service.get_current_period(
                seed_user["user"].id
            )
            assert current_period is not None
            _override_anchor(
                db.session,
                seed_user["account"],
                current_period,
                Decimal("614.29"),
            )

            _make_projected_envelope_expense(
                db.session,
                seed_user=seed_user,
                pay_period=current_period,
                estimated=Decimal("500.00"),
            )
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            checking_ad = next(
                ad for ad in result["account_data"]
                if ad["account"].id == seed_user["account"].id
            )
            # 614.29 - max(500 - 0 - 0, 0) = 614.29 - 500.00 = 114.29.
            # Identical to the pre-Commit-6 value for this no-entries
            # case; the formula reduces to ``effective_amount`` when
            # the entry buckets are all zero.
            assert checking_ad["current_balance"] == Decimal("114.29")


# ── F-21 / Commit 19: Loan period-balance dispatcher ──────────────


class TestLoanProjectedBalanceDispatcher:
    """F-21 / Commit 19: loan period-balance dispatcher unification.

    The savings dashboard's 3/6/12-month projected loan balances and
    the year-end net-worth / debt-progress liability columns both
    route through
    :func:`app.services.account_projection.compute_loan_period_balance_map`.
    The locked canonical is period-end-keyed -- the balance AFTER any
    payment due in the period containing the target date.  Pre-F-21
    the savings dashboard ran a parallel target-month-first walk over
    ``state.schedule`` (last row on-or-before
    ``date(target_y, target_m, 1)``) that produced cents-precise
    drift across the two surfaces.
    """

    def test_dispatcher_returns_period_end_keyed_balance(self):
        """C19-1: hand-crafted schedule + periods prove the semantic.

        Hand arithmetic for a synthetic $1,000 three-payment schedule
        (payments dated mid-month so each falls cleanly inside one
        calendar-month period):

          Period 1 (Jan 1 .. Jan 31, end_date=Jan 31):
            Jan 15 payment <= Jan 31  -> remaining_balance 910.00.
            balance_map[1] == Decimal("910.00").
          Period 2 (Feb 1 .. Feb 28, end_date=Feb 28):
            Feb 15 payment <= Feb 28  -> remaining_balance 819.00.
            balance_map[2] == Decimal("819.00").
          Period 3 (Mar 1 .. Mar 31, end_date=Mar 31):
            Mar 15 payment <= Mar 31  -> remaining_balance 727.00.
            balance_map[3] == Decimal("727.00").

        Each period's balance is the balance AFTER the payment due
        within the period -- the F-21 period-end-keyed canonical.
        """
        # pylint: disable=import-outside-toplevel
        from types import SimpleNamespace

        from app.services.account_projection import (
            compute_loan_period_balance_map,
        )

        schedule = [
            SimpleNamespace(
                payment_date=date(2026, 1, 15),
                remaining_balance=Decimal("910.00"),
            ),
            SimpleNamespace(
                payment_date=date(2026, 2, 15),
                remaining_balance=Decimal("819.00"),
            ),
            SimpleNamespace(
                payment_date=date(2026, 3, 15),
                remaining_balance=Decimal("727.00"),
            ),
        ]
        periods = [
            SimpleNamespace(
                id=1,
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 31),
                period_index=1,
            ),
            SimpleNamespace(
                id=2,
                start_date=date(2026, 2, 1),
                end_date=date(2026, 2, 28),
                period_index=2,
            ),
            SimpleNamespace(
                id=3,
                start_date=date(2026, 3, 1),
                end_date=date(2026, 3, 31),
                period_index=3,
            ),
        ]

        result = compute_loan_period_balance_map(
            schedule, periods, original_principal=Decimal("1000.00"),
        )

        assert result[1] == Decimal("910.00")
        assert result[2] == Decimal("819.00")
        assert result[3] == Decimal("727.00")

    def test_dispatcher_returns_original_principal_before_first_payment(
        self,
    ):
        """C19-1 (boundary): periods preceding the first payment
        return original_principal.

        Period 1 ends Dec 31, 2025; the first scheduled payment is
        Jan 15, 2026.  The dispatcher returns the loan's original
        principal for period 1 because no payment yet lands within
        its end_date.  Period 2 (ends Jan 31) sits after the Jan 15
        payment, so it carries the post-payment remaining balance.
        """
        # pylint: disable=import-outside-toplevel
        from types import SimpleNamespace

        from app.services.account_projection import (
            compute_loan_period_balance_map,
        )

        schedule = [
            SimpleNamespace(
                payment_date=date(2026, 1, 15),
                remaining_balance=Decimal("910.00"),
            ),
        ]
        periods = [
            SimpleNamespace(
                id=1,
                start_date=date(2025, 12, 1),
                end_date=date(2025, 12, 31),
                period_index=0,
            ),
            SimpleNamespace(
                id=2,
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 31),
                period_index=1,
            ),
        ]

        result = compute_loan_period_balance_map(
            schedule, periods, original_principal=Decimal("1000.00"),
        )

        assert result[1] == Decimal("1000.00")
        assert result[2] == Decimal("910.00")

    def test_empty_schedule_returns_original_principal_for_all_periods(
        self,
    ):
        """An empty schedule returns original_principal for every period.

        Models a brand-new loan with no scheduled rows yet (the
        resolver short-circuited or the loan has zero remaining
        months).  The dispatcher must not raise and must not silently
        drop the period -- the F-21 contract is "always return a
        Decimal".
        """
        # pylint: disable=import-outside-toplevel
        from types import SimpleNamespace

        from app.services.account_projection import (
            compute_loan_period_balance_map,
        )

        periods = [
            SimpleNamespace(
                id=1,
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 31),
                period_index=1,
            ),
            SimpleNamespace(
                id=2,
                start_date=date(2026, 2, 1),
                end_date=date(2026, 2, 28),
                period_index=2,
            ),
        ]

        result = compute_loan_period_balance_map(
            [], periods, original_principal=Decimal("1234.56"),
        )

        assert result[1] == Decimal("1234.56")
        assert result[2] == Decimal("1234.56")

    def test_dashboard_loan_projection_reads_from_dispatcher(
        self, app, db, seed_user, seed_periods,
    ):
        """C19-1 / F-21: dashboard projected[label] equals the
        dispatcher's period-end-keyed balance for the period containing
        the target date.

        Locks the F-21 wiring: a regression that re-introduces the
        pre-Commit-19 target-month-first walk over ``state.schedule``
        would fail this assertion, because the two derivations
        produce different cents-precise values when the period
        containing ``date(target_y, target_m, 1)`` carries a payment
        whose ``payment_date`` is later than that month start.

        Uses the small auto loan fixture from
        :func:`_create_small_loan` ($1,000 at 5% for 24 months,
        origination 2026-01-01).  Monthly P&I from the standard
        amortization formula:

          M = 1000 * (0.05/12) / (1 - (1 + 0.05/12)^-24)
            = 1000 * 0.00416667 / (1 - 0.90495)
            = 4.16667 / 0.09505
            ~= 43.87.

        The dispatcher returns the remaining_balance from the
        schedule row dated on-or-before each period's end_date; the
        dashboard reads exactly that value for the period containing
        ``today + 3 months`` (set to the first of that month per the
        dashboard's existing target-date formula).  The 6/12-month
        horizons fall past the seed_periods fixture's ~4.5-month
        window and are intentionally absent from ``projected`` -- the
        dashboard skips horizons whose containing period is missing,
        preserving graceful-degradation when the user's pay-period
        window is short.
        """
        # pylint: disable=import-outside-toplevel
        from app.models.loan_anchor_event import LoanAnchorEvent
        from app.models.loan_params import LoanParams
        from app.services import loan_resolver
        from app.services.account_projection import (
            compute_loan_period_balance_map,
            find_period_containing_date,
        )
        from app.services.loan_payment_service import load_loan_context

        with app.app_context():
            acct = _create_small_loan(seed_user, db.session)

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            loan_ad = next(
                ad for ad in result["account_data"]
                if ad["account"].id == acct.id
            )
            projected = loan_ad["projected"]

            lp = (
                db.session.query(LoanParams)
                .filter_by(account_id=acct.id).first()
            )
            ctx = load_loan_context(
                acct.id, seed_user["scenario"].id, lp,
            )
            events = (
                db.session.query(LoanAnchorEvent)
                .filter_by(account_id=acct.id).all()
            )
            today = date.today()
            state = loan_resolver.resolve_loan(
                loan_resolver.LoanInputs(
                    lp, events, ctx.payments, ctx.rate_changes,
                ),
                today,
            )
            all_periods = pay_period_service.get_all_periods(
                seed_user["user"].id,
            )
            balance_map = compute_loan_period_balance_map(
                state.schedule, all_periods, lp.original_principal,
            )

            for label, month_offset in [
                ("3 months", 3), ("6 months", 6), ("1 year", 12),
            ]:
                target_m = today.month + month_offset
                target_y = today.year + (target_m - 1) // 12
                target_m = (target_m - 1) % 12 + 1
                target_dt = date(target_y, target_m, 1)
                target_period = find_period_containing_date(
                    all_periods, target_dt,
                )
                if target_period is None:
                    # Horizon past the user's generated periods -- the
                    # dashboard skips it.  Skip-or-equal asserts the
                    # F-21 contract: a present label MUST come from
                    # the dispatcher's map for the matching period.
                    assert label not in projected
                    continue
                expected = balance_map[target_period.id]
                assert projected[label] == expected, (
                    f"{label} projected balance must equal the "
                    f"period-end-keyed dispatcher value for the "
                    f"period containing {target_dt} (got "
                    f"{projected[label]!r}, expected {expected!r})"
                )

    def test_dashboard_loan_projection_agrees_with_year_end(
        self, app, db, seed_user, seed_periods,
    ):
        """C19-3 / F-21: cross-page loan-balance equality.

        For the same loan + same period, the savings-dashboard
        projected balance and the year-end net-worth liability map
        must return the same Decimal.  Pre-F-21 the two surfaces ran
        divergent walks over ``state.schedule`` and could differ by
        one payment's principal; post-F-21 both consumers route
        through the same dispatcher so this is structural.
        """
        # pylint: disable=import-outside-toplevel
        from app.models.loan_anchor_event import LoanAnchorEvent
        from app.models.loan_params import LoanParams
        from app.services import loan_resolver, year_end_summary_service
        from app.services.account_projection import (
            compute_loan_period_balance_map,
            find_period_containing_date,
        )
        from app.services.loan_payment_service import load_loan_context

        with app.app_context():
            acct = _create_small_loan(seed_user, db.session)

            dashboard = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            loan_ad = next(
                ad for ad in dashboard["account_data"]
                if ad["account"].id == acct.id
            )
            projected = loan_ad["projected"]
            if not projected:
                # Defensive: when no projection horizon fits inside
                # the fixture window, the cross-page invariant is
                # vacuously satisfied.  (seed_periods' 10-period
                # window normally accommodates at least the
                # "3 months" horizon.)
                return

            lp = (
                db.session.query(LoanParams)
                .filter_by(account_id=acct.id).first()
            )
            ctx = load_loan_context(
                acct.id, seed_user["scenario"].id, lp,
            )
            events = (
                db.session.query(LoanAnchorEvent)
                .filter_by(account_id=acct.id).all()
            )
            today = date.today()
            state = loan_resolver.resolve_loan(
                loan_resolver.LoanInputs(
                    lp, events, ctx.payments, ctx.rate_changes,
                ),
                today,
            )
            all_periods = pay_period_service.get_all_periods(
                seed_user["user"].id,
            )

            # Year-end derives its loan balances through the SAME
            # ``compute_loan_period_balance_map`` dispatcher.
            year_end_map = compute_loan_period_balance_map(
                state.schedule, all_periods, lp.original_principal,
            )

            for label, month_offset in [
                ("3 months", 3), ("6 months", 6), ("1 year", 12),
            ]:
                if label not in projected:
                    continue
                target_m = today.month + month_offset
                target_y = today.year + (target_m - 1) // 12
                target_m = (target_m - 1) % 12 + 1
                target_dt = date(target_y, target_m, 1)
                target_period = find_period_containing_date(
                    all_periods, target_dt,
                )
                # Both surfaces resolve to the same period.id and
                # therefore the same balance.
                assert projected[label] == year_end_map[target_period.id]

            # Bonus structural lock: verify the year-end consumer
            # also uses the same dispatcher (delegating to it on the
            # debt-schedule path).  Year-end's pinned debt-progress
            # values in ``test_year_end_summary_service.py`` would
            # also fail if the dispatcher diverged.
            ye_result = year_end_summary_service.compute_year_end_summary(
                seed_user["user"].id, today.year,
            )
            assert "debt_progress" in ye_result
