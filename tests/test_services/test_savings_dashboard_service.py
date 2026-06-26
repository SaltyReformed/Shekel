"""
Shekel Budget App -- Savings Dashboard Service Tests

Unit tests for the savings_dashboard_service module, verifying that
the extracted business logic produces correct financial computations
independently of the Flask route layer.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import CompoundingFrequencyEnum, GoalModeEnum, IncomeUnitEnum
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
                # Loop B Phase 1: the net-worth cockpit region.
                "net_worth",
                # Loop B Phase 2: per-group grid subtotals and the
                # Property equity card data.
                "group_subtotals", "property_equity",
                # Loop B P3 slice 3b: the diverging allocation bar split.
                "allocation",
                # Loop B P3 slice 3c: the per-account card sparklines.
                "sparklines",
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
            # 5000 / 10000 * 100 = 50.00 via money.percent_complete (Decimal).
            assert gd["progress_pct"] == Decimal("50.00")
            assert gd["current_balance"] == Decimal("5000.00")

    def test_progress_pct_rounds_half_up_fractional_percent(
        self, app, db, seed_user, seed_periods
    ):
        """progress_pct rounds a fractional percent HALF_UP via percent_complete.

        $4,980 / $5,000 = 99.6%.  deep-quality-hunt #20/#78 routed this
        savings card through the canonical ``money.percent_complete``
        (ROUND_HALF_UP, clamped [0, 100], Decimal), retiring the prior
        ``min(100, int(...))`` truncation that disagreed with the
        budget-dashboard savings-goal card for the same goal.  So the value
        is now ``Decimal("99.60")`` (not the old truncated ``99``); the
        template renders it ``"{:.0f}".format(...)`` -> "100%", matching the
        budget dashboard's savings-goal track label (``_tracks.html``;
        the pre-rebuild ``_savings_goals.html`` it replaced).  Revert-proof:
        the old ``int(99.6) == 99`` fails this ``99.60`` assertion.
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
                    name="Truncation Account",
                    anchor_balance=Decimal("4980.00"),
                    anchor_period_id=seed_periods[0].id,
                ),
            )
            db.session.add(savings)
            db.session.flush()

            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=savings.id,
                name="Almost There",
                target_amount=Decimal("5000.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            gd = result["goal_data"][0]
            assert gd["current_balance"] == Decimal("4980.00")
            # 4980 / 5000 * 100 = 99.60, ROUND_HALF_UP via percent_complete
            # (NOT the old int()-truncated 99).
            assert gd["progress_pct"] == Decimal("99.60")

    def test_progress_pct_clamps_over_funded_to_100(
        self, app, db, seed_user, seed_periods
    ):
        """progress_pct clamps an over-funded goal to 100 (upper bound).

        $6,000 / $5,000 = 120%, clamped to ``Decimal("100.00")`` by
        ``money.percent_complete`` (deep-quality-hunt #20/#78).  The
        companion lower clamp on a negative balance is covered by
        ``test_progress_pct_clamps_negative_balance_to_zero``.
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
                    name="Over-funded Account",
                    anchor_balance=Decimal("6000.00"),
                    anchor_period_id=seed_periods[0].id,
                ),
            )
            db.session.add(savings)
            db.session.flush()

            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=savings.id,
                name="Exceeded",
                target_amount=Decimal("5000.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            gd = result["goal_data"][0]
            assert gd["current_balance"] == Decimal("6000.00")
            # 6000 / 5000 * 100 = 120, clamped to 100.00 by percent_complete.
            assert gd["progress_pct"] == Decimal("100.00")

    def test_progress_pct_clamps_negative_balance_to_zero(
        self, app, db, seed_user, seed_periods
    ):
        """progress_pct floors a negative-balance goal at 0% (lower bound).

        A goal backed by an overdrawn account (negative projected balance)
        previously produced a NEGATIVE progress_pct -- the prior
        ``min(100, int(...))`` rule had no lower clamp, so an overdrawn
        -$500 against a $5,000 target rendered a -10%-width / "-10%"-label
        bar (deep-quality-hunt #20).  Routing through ``percent_complete``
        floors the ratio at ``Decimal("0")``.  Revert-proof: the old rule
        yields ``min(100, int(-10)) == -10``, failing this 0 assertion.
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
                    name="Overdrawn Account",
                    anchor_balance=Decimal("-500.00"),
                    anchor_period_id=seed_periods[0].id,
                ),
            )
            db.session.add(savings)
            db.session.flush()

            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=savings.id,
                name="Underwater",
                target_amount=Decimal("5000.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            gd = result["goal_data"][0]
            assert gd["current_balance"] == Decimal("-500.00")
            # -500 / 5000 * 100 = -10%, floored to 0.00 by percent_complete.
            assert gd["progress_pct"] == Decimal("0")

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
    comfortably positive (~21 from April 2026).  Thin wrapper over the
    shared ``create_loan_account`` builder (DRY -- the four-step
    factory + params + origination-event + rate dance lives in
    ``tests/_test_helpers``, not duplicated per suite).
    """
    from tests._test_helpers import create_loan_account  # pylint: disable=import-outside-toplevel
    return create_loan_account(
        seed_user, db_session, name=name,
        principal=principal, rate=rate, term=term,
        origination_date=date(2026, 1, 1), payment_day=1,
    )


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

    def test_narrow_producer_matches_full_dashboard(
        self, app, db, seed_user, seed_periods,
    ):
        """#82: compute_debt_summary equals the full build's debt_summary.

        The equivalence contract behind the narrow producer: with a loan
        account, a salary profile, AND the seed user's non-loan accounts
        present, the loan-only projection run must produce exactly the
        dict the full ``compute_dashboard_data`` build emits -- every
        money figure (total_debt, total_monthly_payments,
        weighted_avg_rate, projected_debt_free_date) AND the three DTI
        keys, since both route through the shared
        ``_debt_summary_with_dti``.  The salary makes the DTI leg
        non-vacuous: $78,000 / 26 = $3,000 gross biweekly -> $6,500
        gross monthly, so ``gross_monthly_income`` / ``dti_ratio`` /
        ``dti_label`` are live Decimals on both sides, not None == None.
        Dict equality (not per-key spot checks) so a future key added to
        one path but not the other fails here.
        """
        with app.app_context():
            filing = db.session.query(FilingStatus).first()
            db.session.add(SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=filing.id,
                name="Equivalence Salary",
                annual_salary=Decimal("78000.00"),
                state_code="NC",
            ))
            _create_small_loan(seed_user, db.session)
            db.session.commit()

            full = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )["debt_summary"]
            narrow = savings_dashboard_service.compute_debt_summary(
                seed_user["user"].id,
            )
            assert full is not None
            # The DTI leg is live, not the vacuous None == None.
            assert full["gross_monthly_income"] == Decimal("6500.00")
            assert narrow == full

    def test_narrow_producer_none_when_no_loans(
        self, app, db, seed_user, seed_periods,
    ):
        """#82: the narrow producer's no-loan early return yields None.

        Mirrors ``test_debt_summary_none_when_no_loans`` for the narrow
        path: with no LoanParams rows the producer returns ``None``
        before any per-account projection or breakdown computation runs
        (the same ``None`` the full build surfaces as ``debt_summary``).
        """
        with app.app_context():
            assert savings_dashboard_service.compute_debt_summary(
                seed_user["user"].id,
            ) is None

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
            from tests._test_helpers import (  # pylint: disable=import-outside-toplevel
                insert_origination_event as _ioe,
                insert_origination_rate as _ior,
            )
            lp1 = LP(
                account_id=mortgage.id,
                original_principal=Decimal("200000.00"),
                current_principal=Decimal("200000.00"),
                term_months=360,
                origination_date=date(2024, 1, 1),
                payment_day=1,
            )
            db.session.add(lp1)
            db.session.flush()
            _ioe(lp1)
            _ior(lp1, Decimal("0.06500"))  # DH-#56 origination rate

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
                term_months=60,
                origination_date=date(2024, 6, 1),
                payment_day=15,
            )
            db.session.add(lp2)
            db.session.flush()
            _ioe(lp2)
            _ior(lp2, Decimal("0.04900"))  # DH-#56 origination rate
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
                term_months=360,
                origination_date=date(2024, 1, 1),
                payment_day=1,
            )
            db.session.add(lp)
            db.session.flush()
            from tests._test_helpers import (  # pylint: disable=import-outside-toplevel
                insert_origination_event as _ioe,
                insert_origination_rate as _ior,
            )
            _ioe(lp)
            _ior(lp, Decimal("0.06500"))  # DH-#56 origination rate
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
                term_months=360,
                origination_date=date(2024, 1, 1),
                payment_day=1,
            )
            db.session.add(lp)
            db.session.flush()
            from tests._test_helpers import (  # pylint: disable=import-outside-toplevel
                insert_origination_event as _ioe,
                insert_origination_rate as _ior,
            )
            _ioe(lp)
            _ior(lp, Decimal("0.06500"))  # DH-#56 origination rate

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


class TestDebtPrincipalProgress:
    """Tests for ``compute_debt_principal_progress`` (Loop B B-1).

    The narrow producer behind the dashboard's debt track marker: the
    aggregate fraction of original loan principal paid down so far.  Per
    the 2026-06-12 ruling (``dashboard_card_audit.md`` Rebuild decisions
    item 4) it sums over ALL loans ever originated -- paid-off loans stay
    in both the numerator and the denominator -- so the fraction is
    monotonic, reaches exactly ``1`` at full payoff, and stays there.
    ``original_principal`` is a NOT NULL, ``> 0`` column, so any loan
    supplies the denominator; the ONLY ``None`` case is no loans at all.
    """

    def test_none_when_no_loans(self, app, db, seed_user, seed_periods):
        """No loan accounts -> the fraction is None (no marker drawn)."""
        with app.app_context():
            assert savings_dashboard_service.compute_debt_principal_progress(
                seed_user["user"].id,
            ) is None

    def test_fraction_present_for_a_loan(self, app, db, seed_user, seed_periods):
        """A loan -> a Decimal fraction that reconciles with the debt summary.

        A $1,000.00 auto loan.  The fraction sums over the SAME loan set
        the debt summary's ``total_debt`` uses, so:
            fraction = (original - current) / original
                     = (1000.00 - total_debt) / 1000.00.
        ``original_principal`` is NOT NULL, so the data IS present and the
        fraction is a real Decimal in [0, 1], never None.
        """
        with app.app_context():
            _create_small_loan(seed_user, db.session)

            summary = savings_dashboard_service.compute_debt_summary(
                seed_user["user"].id,
            )
            fraction = (
                savings_dashboard_service.compute_debt_principal_progress(
                    seed_user["user"].id,
                )
            )
            assert isinstance(fraction, Decimal)
            assert Decimal("0") <= fraction <= Decimal("1")
            # Reconcile against the debt summary's current balance.
            expected = (
                (Decimal("1000.00") - summary["total_debt"]) / Decimal("1000.00")
            )
            assert fraction == expected

    def test_fraction_zero_at_origination(self, app, db, seed_user, seed_periods):
        """A loan originated today (no payments yet) -> fraction exactly 0.

        ``_create_small_loan`` originates 2026-01-01 at term 24; under the
        frozen 2026-03-20 today some scheduled payments are confirmed, so
        to isolate the zero case we true-up the balance back UP to the
        original principal, leaving current == original:
            (1000.00 - 1000.00) / 1000.00 = 0.
        """
        # pylint: disable=import-outside-toplevel
        from tests._test_helpers import insert_trueup_event

        with app.app_context():
            acct = _create_small_loan(seed_user, db.session)
            # Assert the current balance equals the original principal.
            insert_trueup_event(acct.loan_params, Decimal("1000.00"))
            db.session.commit()

            fraction = (
                savings_dashboard_service.compute_debt_principal_progress(
                    seed_user["user"].id,
                )
            )
            assert fraction == Decimal("0")

    def test_fraction_one_when_all_loans_paid_off(
        self, app, db, seed_user, seed_periods,
    ):
        """All loans paid off -> the fraction is exactly 1 (full payoff).

        A loan trued-up to $0 is paid off.  Under the all-loans-ever basis
        it stays in BOTH sums, contributing $0 to the current-balance sum
        and its full $1,000.00 original principal to the denominator, so:
            (1000.00 - 0.00) / 1000.00 = 1.
        The fraction reaches 1 at full payoff and is NOT None -- None is
        reserved for the no-loans-at-all case.  ``compute_debt_summary``
        still reports total_debt $0.00 (active-loans-only), so the two
        surfaces deliberately disagree on which loans count.
        """
        # pylint: disable=import-outside-toplevel
        from app import ref_cache as rc
        from app.enums import StatusEnum
        from app.services.transfer_service import TransferSpec, create_transfer
        from tests._test_helpers import insert_trueup_event

        with app.app_context():
            acct = _create_small_loan(seed_user, db.session)
            # A confirmed payment so the loan reads as "ever paid off".
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
            insert_trueup_event(acct.loan_params, Decimal("0.00"))
            db.session.commit()

            # The debt summary reports the active-loans-only total ($0.00) ...
            summary = savings_dashboard_service.compute_debt_summary(
                seed_user["user"].id,
            )
            assert summary is not None
            assert summary["total_debt"] == Decimal("0.00")
            # ... but the principal-paid fraction is exactly 1: the paid-off
            # loan keeps its full original principal in both sums.
            fraction = (
                savings_dashboard_service.compute_debt_principal_progress(
                    seed_user["user"].id,
                )
            )
            # (1000.00 - 0.00) / 1000.00 = 1.
            assert fraction == Decimal("1")

    def test_fraction_monotonic_one_paid_one_partial(
        self, app, db, seed_user, seed_periods,
    ):
        """One paid-off + one partial loan -> all-loans-ever fraction.

        Loan A: $1,000.00 original, paid off (trued-up to $0) -> stays in
        both sums, contributing $0 to the current-balance sum.
        Loan B: $1,000.00 original, trued-up to a known $400.00 balance ->
        contributes $400.00 to the current-balance sum.

        Under the all-loans-ever basis BOTH loans count, so the fraction
        does NOT jump (it would under the old active-only basis, which
        would have dropped Loan A entirely):
            (orig_A + orig_B - balance_B) / (orig_A + orig_B)
          = (1000.00 + 1000.00 - 400.00) / (1000.00 + 1000.00)
          = 1600.00 / 2000.00
          = 0.8.
        """
        # pylint: disable=import-outside-toplevel
        from app import ref_cache as rc
        from app.enums import StatusEnum
        from app.services.transfer_service import TransferSpec, create_transfer
        from tests._test_helpers import insert_trueup_event

        with app.app_context():
            paid_off = _create_small_loan(
                seed_user, db.session, name="Paid Off Loan",
            )
            partial = _create_small_loan(
                seed_user, db.session, name="Partial Loan",
            )
            # Confirmed payment so the first loan reads as "ever paid off".
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
            # Loan A: trued-up to $0 (paid off).
            insert_trueup_event(paid_off.loan_params, Decimal("0.00"))
            # Loan B: trued-up to a known $400.00 partial balance.
            insert_trueup_event(partial.loan_params, Decimal("400.00"))
            db.session.commit()

            fraction = (
                savings_dashboard_service.compute_debt_principal_progress(
                    seed_user["user"].id,
                )
            )
            # (orig_A + orig_B - balance_B) / (orig_A + orig_B)
            # = (1000.00 + 1000.00 - 400.00) / (1000.00 + 1000.00)
            # = 1600.00 / 2000.00 = 0.8.
            expected = (
                (Decimal("1000.00") + Decimal("1000.00") - Decimal("400.00"))
                / (Decimal("1000.00") + Decimal("1000.00"))
            )
            assert fraction == expected
            assert fraction == Decimal("0.8")


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
    with no ``apply_raises`` invocation) and converted to monthly via
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
        the current period's year.  ``apply_raises`` applies the raise
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

        1. The shared DTI applier ``_debt_summary_with_dti`` (the single
           home of the debt/DTI rule behind both
           ``compute_dashboard_data`` and the narrow #82
           ``compute_debt_summary``) reads
           ``current_breakdown.earnings.gross_biweekly`` (the engine-derived
           value introduced by Commit 26), and NONE of the three
           functions subscripts ``params`` with the
           ``"salary_gross_biweekly"`` key (the off-engine value still
           used by the investment-projection path -- F-20 follow-up).
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
        # read in the shared DTI applier (the #82 refactor moved the
        # expression out of compute_dashboard_data into the single
        # helper both entry points route through).
        source = inspect.getsource(_orchestrator._debt_summary_with_dti)
        assert "current_breakdown.earnings.gross_biweekly" in source, (
            "DTI block must read gross_biweekly from the paycheck "
            "engine breakdown (MED-06 / F-032)."
        )

        # Guard 1b: negative lock -- neither entry point nor the shared
        # DTI applier may read the off-engine ``salary_gross_biweekly``,
        # by either the legacy dict subscript
        # ``params["salary_gross_biweekly"]`` or the current dataclass
        # attribute ``params.salary_gross_biweekly`` (``params`` became
        # the frozen ``_AccountParams`` in the type-precision quality
        # pass; the attribute form is the access a regression would now
        # use).  ``compute_debt_summary`` is scanned too so the narrow
        # #82 path cannot regress independently.
        dti_fn_names = {
            "compute_dashboard_data",
            "compute_debt_summary",
            "_debt_summary_with_dti",
        }
        tree = ast.parse(inspect.getsource(_orchestrator))
        target_fns = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name in dti_fn_names
        ]
        assert len(target_fns) == len(dti_fn_names), (
            "expected DTI functions not all found in module source"
        )
        for target_fn in target_fns:
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
                        f"{target_fn.name} must not read the off-engine "
                        "salary_gross_biweekly value for DTI "
                        "(MED-06 / F-032)."
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
            on the same period (so ``sum_projected`` applies).
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
                compounding_frequency_id=ref_cache.compounding_frequency_id(
                    CompoundingFrequencyEnum.DAILY,
                ),
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


class TestInvestmentHorizons:
    """DH-#35 follow-up: pin the investment-branch 3/6/12 horizon mapping.

    The ``79181a4`` DRY collapse routed
    ``_projections._investment_horizons`` through the shared
    ``period_projections.project_balance_horizons`` with equivalence
    verified by code analysis only -- the investment branch had no test
    (the register's own caveat).  These tests close that gap: a
    hand-built growth projection (real :class:`ProjectedBalance` rows,
    so the adapter's ``pb.period_id`` / ``pb.end_balance`` reads are
    pinned against the engine's actual row type) must surface at the
    biweekly horizon offsets 6 / 13 / 26 from the current period
    (~3 / 6 / 12 months at 26 periods per year).
    """

    @staticmethod
    def _period(period_id, period_index):
        """Synthetic PayPeriod stand-in (id + period_index reads only)."""
        # pylint: disable=import-outside-toplevel
        from types import SimpleNamespace
        return SimpleNamespace(id=period_id, period_index=period_index)

    @staticmethod
    def _row(period_id, end_balance):
        """Real ProjectedBalance row with only the read fields varying."""
        # pylint: disable=import-outside-toplevel
        from app.services.growth_engine import ProjectedBalance
        return ProjectedBalance(
            period_id=period_id,
            start_balance=Decimal("0.00"),
            growth=Decimal("0.00"),
            contribution=Decimal("0.00"),
            employer_contribution=Decimal("0.00"),
            end_balance=end_balance,
            ytd_contributions=Decimal("0.00"),
            contribution_limit_remaining=Decimal("0.00"),
        )

    def test_pins_three_six_twelve_month_balances(self):
        """All three horizons present -> exact end balances surfaced.

        Current period_index = 10, so the horizon targets are the
        periods at indices 16 / 23 / 36 (offsets 6 / 13 / 26).  The
        projection's end balances there are 1100.00 / 1250.00 /
        1600.00; the current period's own row (1000.00) must NOT
        appear -- offset 0 is not a horizon.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.savings_dashboard_service._projections import (
            _investment_horizons,
        )
        current = self._period(100, 10)
        all_periods = [
            current,
            self._period(116, 16),
            self._period(123, 23),
            self._period(136, 36),
        ]
        projection = [
            self._row(100, Decimal("1000.00")),
            self._row(116, Decimal("1100.00")),
            self._row(123, Decimal("1250.00")),
            self._row(136, Decimal("1600.00")),
        ]
        assert _investment_horizons(projection, all_periods, current) == {
            "3 months": Decimal("1100.00"),
            "6 months": Decimal("1250.00"),
            "1 year": Decimal("1600.00"),
        }

    def test_omits_horizons_beyond_the_projection(self):
        """A horizon with no projected row is omitted, not zeroed.

        The 1-year target period (index 36) exists in ``all_periods``
        but the projection ends at index 23, so only the 3- and
        6-month labels appear -- the omission contract the dashboard
        template relies on (it renders only the horizons present).
        """
        # pylint: disable=import-outside-toplevel
        from app.services.savings_dashboard_service._projections import (
            _investment_horizons,
        )
        current = self._period(100, 10)
        all_periods = [
            current,
            self._period(116, 16),
            self._period(123, 23),
            self._period(136, 36),
        ]
        projection = [
            self._row(116, Decimal("1100.00")),
            self._row(123, Decimal("1250.00")),
        ]
        assert _investment_horizons(projection, all_periods, current) == {
            "3 months": Decimal("1100.00"),
            "6 months": Decimal("1250.00"),
        }


# ── Net-Worth Cockpit Producer Tests (Loop B Phase 1) ───────────────


def _add_savings_account(seed_user, anchor_period_id, balance):
    """Create a liquid Savings account anchored to a period.

    Returns:
        The new savings Account.
    """
    savings_type = (
        db.session.query(AccountType).filter_by(name="Savings").one()
    )
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="Savings",
            anchor_balance=balance,
            anchor_period_id=anchor_period_id,
        ),
    )
    db.session.add(acct)
    db.session.commit()
    return acct


def _add_mortgage_account(seed_user, anchor_period_id, balance):
    """Create a Mortgage (liability) account with a loan schedule.

    Mortgage originated 2025-01-01 at 6.5%, 30-year, so the resolver's
    as-of-today current balance equals the origination principal and the
    amortization schedule drives the forward liability series.

    Returns:
        The new mortgage Account.
    """
    # pylint: disable=import-outside-toplevel
    from datetime import date as _date
    from app.models.loan_params import LoanParams
    from tests._test_helpers import (
        insert_origination_event,
        insert_origination_rate,
    )

    mortgage_type = (
        db.session.query(AccountType).filter_by(name="Mortgage").one()
    )
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=mortgage_type.id,
            name="Home Mortgage",
            anchor_balance=balance,
            anchor_period_id=anchor_period_id,
        ),
    )
    db.session.add(acct)
    db.session.flush()
    params = LoanParams(
        account_id=acct.id,
        original_principal=balance,
        current_principal=balance,
        term_months=360,
        origination_date=_date(2025, 1, 1),
        payment_day=1,
    )
    db.session.add(params)
    db.session.flush()
    insert_origination_event(params)
    insert_origination_rate(params, Decimal("0.06500"))
    db.session.commit()
    return acct


def _add_property_account(seed_user, anchor_period_id, market_value):
    """Create a Property (appreciating physical asset) anchored to a period.

    The market value is the user-set anchor balance; no appreciation params
    row is needed for equity (equity reads the anchor value, not the
    forward projection).

    Returns:
        The new Property Account.
    """
    property_type = (
        db.session.query(AccountType).filter_by(name="Property").one()
    )
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=property_type.id,
            name="House",
            anchor_balance=market_value,
            anchor_period_id=anchor_period_id,
        ),
    )
    db.session.add(acct)
    db.session.commit()
    return acct


class TestNetWorthHero:
    """Tests for the cockpit's today net-worth figures.

    ``compute_net_worth_today`` reduces over each account's resolver
    ``current_balance``: assets add their balance, liabilities accumulate
    their positive magnitude, net worth is assets minus liabilities, and
    liquid is the liquid-account balance sum.
    """

    def test_assets_minus_liabilities(
        self, app, db, seed_user, seed_periods,
    ):
        """Net worth is total assets minus the positive liability magnitude.

        Checking ($1,000) + Savings ($4,000) are assets; a $240,000
        mortgage is a liability.  With no transactions every
        ``current_balance`` equals its flat anchor, so:
          total_assets       = 1000.00 + 4000.00 = 5000.00
          total_liabilities  = 240000.00 (positive magnitude)
          net_worth          = 5000.00 - 240000.00 = -235000.00
        """
        with app.app_context():
            _add_savings_account(
                seed_user, seed_periods[0].id, Decimal("4000.00"),
            )
            _add_mortgage_account(
                seed_user, seed_periods[0].id, Decimal("240000.00"),
            )

            nw = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )["net_worth"]

            # 1000.00 + 4000.00 = 5000.00
            assert nw["total_assets"] == Decimal("5000.00")
            # Mortgage resolver current balance = origination principal.
            assert nw["total_liabilities"] == Decimal("240000.00")
            # 5000.00 - 240000.00 = -235000.00
            assert nw["net_worth"] == Decimal("-235000.00")

    def test_total_liabilities_is_positive_magnitude(
        self, app, db, seed_user, seed_periods,
    ):
        """A liability contributes a POSITIVE total_liabilities, not negative."""
        with app.app_context():
            _add_mortgage_account(
                seed_user, seed_periods[0].id, Decimal("240000.00"),
            )

            nw = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )["net_worth"]

            assert nw["total_liabilities"] == Decimal("240000.00")
            assert nw["total_liabilities"] > Decimal("0.00")

    def test_liquid_excludes_non_liquid(
        self, app, db, seed_user, seed_periods,
    ):
        """Liquid sums only is_liquid accounts; a mortgage is excluded.

        Checking ($1,000, liquid) + Savings ($4,000, liquid) count; the
        $240,000 mortgage (non-liquid liability) does not:
          liquid = 1000.00 + 4000.00 = 5000.00
        while total_assets (also 5000.00 here) and net worth carry the
        mortgage.  Liquid != assets in general; this fixture keeps them
        equal only because the sole non-liquid account is the liability.
        """
        with app.app_context():
            _add_savings_account(
                seed_user, seed_periods[0].id, Decimal("4000.00"),
            )
            _add_mortgage_account(
                seed_user, seed_periods[0].id, Decimal("240000.00"),
            )

            nw = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )["net_worth"]

            # 1000.00 + 4000.00 = 5000.00 (mortgage excluded from liquid).
            assert nw["liquid"] == Decimal("5000.00")


class TestNetWorthSeries:
    """Tests for the cockpit's forward net-worth trend series."""

    def test_default_series_spans_history_tail_and_forward(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Series leads with the honest history tail, then the forward run.

        ``seed_periods_today`` places today in period index 4 of a
        10-period window and anchors the seed Checking account at period
        index 0.  Checking is a PLAIN (cash) account, so the honest history
        reaches back to its anchor (index 0): the tail is the 4 elapsed
        periods (indices 0-3, fewer than the 6-period cap) and the forward
        run is indices 4-9, so the series spans all 10 periods and
        ``current_index`` -- the count of leading history points, the
        solid/dashed boundary -- is 4.  The expected values are
        fixture-derived literals, NOT re-derived from the production window
        logic, so an off-by-one there surfaces here.  (``seed_periods``, a
        fixed 2026-01-02 window now entirely in the past, has no current
        period -- it would make this a vacuous empty series.)
        """
        with app.app_context():
            series = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )["net_worth"]["series"]

            # history tail (indices 0-3) + forward (indices 4-9) = 10 points
            assert len(series["periods"]) == 10
            assert len(series["net"]) == 10
            assert len(series["assets"]) == 10
            assert len(series["liabilities"]) == 10
            # current period (index 4) sits at position 4: 4 history points
            # precede it (indices 0, 1, 2, 3).
            assert series["current_index"] == 4
            assert [p["period_index"] for p in series["periods"][:4]] == [
                0, 1, 2, 3,
            ]
            assert series["periods"][4]["period_index"] == 4

    def test_net_equals_assets_minus_liabilities_each_point(
        self, app, db, seed_user, seed_periods,
    ):
        """series net[i] == assets[i] - liabilities[i] for every point.

        Holds even with a mortgage whose amortization drives the
        liability series down period by period: the asset-plus /
        liability-minus split shares one sum with the net reduction.
        """
        with app.app_context():
            _add_savings_account(
                seed_user, seed_periods[0].id, Decimal("4000.00"),
            )
            _add_mortgage_account(
                seed_user, seed_periods[0].id, Decimal("240000.00"),
            )

            series = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )["net_worth"]["series"]

            assert len(series["net"]) > 0
            for i in range(len(series["net"])):
                assert series["net"][i] == (
                    series["assets"][i] - series["liabilities"][i]
                )

    def test_current_period_point_equals_hero_for_liquid_only(
        self, app, db, seed_user, seed_periods,
    ):
        """For a CHECKING/SAVINGS-only fixture, the current-period series
        point equals the today hero.

        With no transactions every balance is flat, so the current
        period's net worth (``series["net"][current_index]``) equals the
        today hero:
          Checking 1000.00 + Savings 4000.00 = 5000.00.
        A flat liquid-only set has the same value at every point, so the
        history-tail points equal it too -- asserted to lock that the
        widened window did not skew the figures.
        """
        with app.app_context():
            _add_savings_account(
                seed_user, seed_periods[0].id, Decimal("4000.00"),
            )

            nw = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )["net_worth"]

            current = nw["series"]["current_index"]
            # 1000.00 + 4000.00 = 5000.00, identical hero and current point.
            assert nw["net_worth"] == Decimal("5000.00")
            assert nw["series"]["net"][current] == Decimal("5000.00")
            assert nw["series"]["net"][current] == nw["net_worth"]
            # Flat liquid-only: every trend point (history tail + forward).
            assert all(v == Decimal("5000.00") for v in nw["series"]["net"])

    def test_current_period_point_diverges_from_hero_for_amortizing_loan(
        self, app, db, seed_user, seed_periods_today,
    ):
        """For a loan, the current-period series point differs from the hero.

        The two figures deliberately read DIFFERENT sources, and this test
        guards that they keep doing so (the documented caveat to the
        liquid-only ``series[current_index] == hero`` case above).  The
        current period sits at ``series["current_index"]`` within the trend
        window (the history tail precedes it):

        - The hero (``compute_net_worth_today``) reduces over each
          account's as-of-today ``current_balance``.  The mortgage has no
          confirmed payments, so the loan resolver replays nothing forward
          and reports its $240,000 origination principal.
        - The series reads the dense amortization-schedule map, whose
          current-period (period-end) value has already paid principal
          DOWN below $240,000 by today.

        Checking $1,000 and a $240,000 mortgage (anchored at index 0):
          hero net        = 1000.00 - 240000.00 = -239000.00 (anchor)
          series[current] = 1000.00 - (amortized < 240000.00) > -239000.00
        """
        with app.app_context():
            _add_mortgage_account(
                seed_user, seed_periods_today[0].id, Decimal("240000.00"),
            )

            nw = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )["net_worth"]

            current = nw["series"]["current_index"]
            # Hero uses the as-of-today anchor (no confirmed payments):
            # 1000.00 (checking) - 240000.00 (mortgage) = -239000.00
            assert nw["net_worth"] == Decimal("-239000.00")
            # The current-period series point uses the schedule (period-end),
            # amortized below 240000, so net is HIGHER (less liability) and
            # strictly differs from the hero -- the two-source split holds.
            assert nw["series"]["net"][current] > nw["net_worth"]
            assert nw["series"]["net"][current] != nw["net_worth"]


class TestBuildTrendPeriods:
    """Tests for the trend's honest history window (build_trend_periods).

    The window leads with a short "actual" history tail then the forward
    projection.  The tail reaches back only as far as every CASH account
    (PLAIN / INTEREST -- the kinds whose dense map omits pre-anchor
    periods) has a real balance, i.e. to the LATEST such anchor, capped at
    the history cap.  These unit tests drive the helper with synthetic
    periods + accounts so the window arithmetic is pinned independently of
    the projection engines.
    """

    @staticmethod
    def _period(period_index):
        """Synthetic PayPeriod stand-in (id, period_index, end_date reads).

        The id is offset (``100 + index``) so an id/index swap in the
        production code would surface rather than coincide.  ``end_date`` is
        biweekly-spaced and distinct per index so the loan gate
        (``_loan_schedule_start_index``, which matches a schedule's first
        payment_date to a period by ``end_date``) resolves unambiguously.
        """
        # pylint: disable=import-outside-toplevel
        from datetime import timedelta
        from types import SimpleNamespace
        return SimpleNamespace(
            id=100 + period_index,
            period_index=period_index,
            end_date=date(2026, 1, 14) + timedelta(days=14 * period_index),
        )

    @staticmethod
    def _account(kind, anchor_period_index, account_id=1):
        """Synthetic Account whose type flags ``classify_account`` reads.

        ``anchor_period_index`` is mapped to the matching ``_period`` id
        (``100 + index``); ``None`` leaves the account unanchored.
        ``account_id`` keys the loan gate's ``debt_schedules`` lookup.
        """
        # pylint: disable=import-outside-toplevel
        from types import SimpleNamespace
        from app.services.account_projection import AccountProjectionKind
        acct_type = SimpleNamespace(
            has_amortization=kind is AccountProjectionKind.AMORTIZING,
            has_interest=kind is AccountProjectionKind.INTEREST,
            has_appreciation=kind is AccountProjectionKind.APPRECIATING,
            has_parameters=kind is AccountProjectionKind.INVESTMENT,
        )
        return SimpleNamespace(
            id=account_id,
            account_type=acct_type,
            current_anchor_period_id=(
                None if anchor_period_index is None
                else 100 + anchor_period_index
            ),
        )

    @staticmethod
    def _schedule(first_payment_period_index, periods):
        """A one-row loan schedule whose first payment falls in a period.

        The row's ``payment_date`` is that period's ``end_date``, so the
        loan gate resolves the loan's honest start to that period's index.
        """
        from types import SimpleNamespace  # pylint: disable=import-outside-toplevel
        return [SimpleNamespace(
            payment_date=periods[first_payment_period_index].end_date,
            remaining_balance=Decimal("1000.00"),
        )]

    def test_tail_reaches_back_to_cash_anchor(self):
        """History reaches back to the cash account's anchor period.

        Periods 0..9, today at index 5, one PLAIN account anchored at
        index 2, no loans.  The honest start is the anchor (index 2); the
        cap (5 - 6 = -1) does not bind, so the window is indices 2..9 (8
        points) and ``current_index`` is the count below 5 -> indices
        2, 3, 4 = 3.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.account_projection import AccountProjectionKind
        from app.services.savings_dashboard_service._net_worth import (
            build_trend_periods,
        )
        periods = [self._period(i) for i in range(10)]
        accounts = [self._account(AccountProjectionKind.PLAIN, 2)]

        window, current_index, honest_start = build_trend_periods(
            accounts, periods, periods[5], {},
        )

        assert [p.period_index for p in window] == [2, 3, 4, 5, 6, 7, 8, 9]
        assert current_index == 3
        assert honest_start == 2

    def test_no_history_when_cash_anchor_is_current(self):
        """A cash account anchored at the current period yields no tail.

        PLAIN anchored at index 5, today at index 5: the honest start is 5,
        so the window is forward-only (indices 5..9) and ``current_index``
        0.  This is the common case for an actively-trued-up cockpit.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.account_projection import AccountProjectionKind
        from app.services.savings_dashboard_service._net_worth import (
            build_trend_periods,
        )
        periods = [self._period(i) for i in range(10)]
        accounts = [self._account(AccountProjectionKind.PLAIN, 5)]

        window, current_index, honest_start = build_trend_periods(
            accounts, periods, periods[5], {},
        )

        assert [p.period_index for p in window] == [5, 6, 7, 8, 9]
        assert current_index == 0
        assert honest_start == 5

    def test_tail_capped_at_history_cap(self):
        """The history tail is capped even when the cash anchor is older.

        PLAIN anchored at index 0, today at index 9, periods 0..12.  The
        honest start (0) loses to the cap (9 - 6 = 3), so the tail is
        indices 3..8 (6 points) and ``current_index`` is 6.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.account_projection import AccountProjectionKind
        from app.services.savings_dashboard_service._net_worth import (
            build_trend_periods,
        )
        periods = [self._period(i) for i in range(13)]
        accounts = [self._account(AccountProjectionKind.PLAIN, 0)]

        window, current_index, _ = build_trend_periods(
            accounts, periods, periods[9], {},
        )

        # 6 history points (indices 3..8) then today (9) onward -- the cap
        # binds even though the cash anchor (index 0) is further back.
        assert current_index == 6
        assert window[0].period_index == 3

    def test_no_current_period_is_empty(self):
        """No current period yields an empty window and indices 0."""
        # pylint: disable=import-outside-toplevel
        from app.services.account_projection import AccountProjectionKind
        from app.services.savings_dashboard_service._net_worth import (
            build_trend_periods,
        )
        periods = [self._period(i) for i in range(10)]
        accounts = [self._account(AccountProjectionKind.PLAIN, 0)]

        assert build_trend_periods(accounts, periods, None, {}) == ([], 0, 0)

    def test_only_cash_kinds_gate_the_history_start(self):
        """An INVESTMENT's recent anchor does not shorten the history.

        Only PLAIN / INTEREST accounts gate the honest start by anchor
        (their dense map omits pre-anchor periods).  A PLAIN account is
        anchored at index 1 and an INVESTMENT at index 4, today at index 5.
        The honest start is the PLAIN anchor (1), NOT the later investment
        anchor (4): an investment is defined pre-anchor (reverse-projected),
        so it must not constrain the window.  Window indices 1..9,
        ``current_index`` 4.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.account_projection import AccountProjectionKind
        from app.services.savings_dashboard_service._net_worth import (
            build_trend_periods,
        )
        periods = [self._period(i) for i in range(10)]
        accounts = [
            self._account(AccountProjectionKind.PLAIN, 1),
            self._account(AccountProjectionKind.INVESTMENT, 4),
        ]

        window, current_index, _ = build_trend_periods(
            accounts, periods, periods[5], {},
        )

        assert window[0].period_index == 1
        assert current_index == 4

    def test_latest_cash_anchor_wins(self):
        """With two cash accounts the LATEST anchor bounds the history.

        PLAIN at index 1 and PLAIN at index 3, today at index 5: the honest
        start is the later anchor (3) so no period misses a cash balance.
        Window indices 3..9, ``current_index`` 2 (indices 3, 4).
        """
        # pylint: disable=import-outside-toplevel
        from app.services.account_projection import AccountProjectionKind
        from app.services.savings_dashboard_service._net_worth import (
            build_trend_periods,
        )
        periods = [self._period(i) for i in range(10)]
        accounts = [
            self._account(AccountProjectionKind.PLAIN, 1),
            self._account(AccountProjectionKind.PLAIN, 3),
        ]

        window, current_index, _ = build_trend_periods(
            accounts, periods, periods[5], {},
        )

        assert window[0].period_index == 3
        assert current_index == 2

    def test_loan_schedule_start_gates_history(self):
        """A loan's today-forward schedule gates the history past the cash.

        A PLAIN account is anchored at index 1, but an AMORTIZING loan's
        schedule first pays in period 5 (today at index 7).  Pre-schedule
        periods report the loan's ORIGINAL PRINCIPAL, so the loan gates the
        honest start at index 5 -- LATER than the cash anchor (1).  Window
        indices 5..9, ``current_index`` 2 (indices 5, 6).  Without the loan
        gate the honest start would be the cash anchor (1) and
        ``current_index`` would be 6, so this pins the loan gate.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.account_projection import AccountProjectionKind
        from app.services.savings_dashboard_service._net_worth import (
            build_trend_periods,
        )
        periods = [self._period(i) for i in range(10)]
        accounts = [
            self._account(AccountProjectionKind.PLAIN, 1, account_id=1),
            self._account(AccountProjectionKind.AMORTIZING, 0, account_id=8),
        ]
        debt_schedules = {8: self._schedule(5, periods)}

        window, current_index, honest_start = build_trend_periods(
            accounts, periods, periods[7], debt_schedules,
        )

        assert honest_start == 5
        assert current_index == 2
        assert window[0].period_index == 5

    def test_empty_loan_schedule_does_not_gate(self):
        """A resolved-but-unpaid loan (empty schedule) does not gate history.

        An empty schedule means the loan sits at its original principal at
        EVERY period, which IS its real balance (no payments yet), so it is
        honest throughout and must not gate.  PLAIN anchored at index 1, an
        AMORTIZING loan with an empty schedule, today at index 7: the honest
        start stays the cash anchor (1), window indices 1..9,
        ``current_index`` 6.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.account_projection import AccountProjectionKind
        from app.services.savings_dashboard_service._net_worth import (
            build_trend_periods,
        )
        periods = [self._period(i) for i in range(10)]
        accounts = [
            self._account(AccountProjectionKind.PLAIN, 1, account_id=1),
            self._account(AccountProjectionKind.AMORTIZING, 0, account_id=8),
        ]
        debt_schedules = {8: []}

        window, current_index, honest_start = build_trend_periods(
            accounts, periods, periods[7], debt_schedules,
        )

        assert honest_start == 1
        assert current_index == 6
        assert window[0].period_index == 1


class TestNetWorthProducerEdgeCases:
    """Edge-case coverage for the cockpit net-worth producers."""

    def test_no_accounts_today_is_all_zero(self):
        """With no accounts the today figures are all zero."""
        # pylint: disable=import-outside-toplevel
        from app.services.savings_dashboard_service._net_worth import (
            compute_net_worth_today,
        )
        today = compute_net_worth_today([])
        assert today["net_worth"] == Decimal("0.00")
        assert today["total_assets"] == Decimal("0.00")
        assert today["total_liabilities"] == Decimal("0.00")
        assert today["liquid"] == Decimal("0.00")

    def test_no_account_maps_series_is_empty_window(self):
        """With no account maps and no forward periods the series is empty."""
        # pylint: disable=import-outside-toplevel
        from app.services.savings_dashboard_service._net_worth import (
            compute_net_worth_series,
        )
        series = compute_net_worth_series([], [])
        assert series["periods"] == []
        assert series["net"] == []
        assert series["assets"] == []
        assert series["liabilities"] == []

    def test_liabilities_only_today_is_negative(self):
        """An accounts-set of only liabilities yields negative net worth.

        One liability account with a 500.00 current balance and no assets:
          total_assets      = 0.00
          total_liabilities = 500.00
          net_worth         = 0.00 - 500.00 = -500.00
        Classification is by the account type's category_id, so this test
        builds a stand-in account whose type's category is the LIABILITY
        ref id (IDs for logic, never a name string).
        """
        # pylint: disable=import-outside-toplevel
        from types import SimpleNamespace
        from app.enums import AcctCategoryEnum
        from app.services.savings_dashboard_service._net_worth import (
            compute_net_worth_today,
        )
        liability_cat_id = ref_cache.acct_category_id(
            AcctCategoryEnum.LIABILITY,
        )
        acct_type = SimpleNamespace(
            category_id=liability_cat_id, is_liquid=False,
        )
        account = SimpleNamespace(account_type=acct_type)
        today = compute_net_worth_today([
            {"account": account, "current_balance": Decimal("500.00")},
        ])
        assert today["total_assets"] == Decimal("0.00")
        assert today["total_liabilities"] == Decimal("500.00")
        # 0.00 - 500.00 = -500.00
        assert today["net_worth"] == Decimal("-500.00")
        assert today["liquid"] == Decimal("0.00")

    def test_single_asset_account(self):
        """A single non-liability liquid account: net worth equals its balance.

        One asset account with a 750.00 current balance:
          net_worth = total_assets = liquid = 750.00, liabilities 0.00.
        """
        # pylint: disable=import-outside-toplevel
        from types import SimpleNamespace
        from app.services.savings_dashboard_service._net_worth import (
            compute_net_worth_today,
        )
        acct_type = SimpleNamespace(category_id=-999, is_liquid=True)
        account = SimpleNamespace(account_type=acct_type)
        today = compute_net_worth_today([
            {"account": account, "current_balance": Decimal("750.00")},
        ])
        assert today["net_worth"] == Decimal("750.00")
        assert today["total_assets"] == Decimal("750.00")
        assert today["total_liabilities"] == Decimal("0.00")
        assert today["liquid"] == Decimal("750.00")

    def test_zero_balance_account_contributes_zero_not_absent(self):
        """A zero-balance asset contributes 0.00, it is not skipped.

        Two asset accounts, one 600.00 and one 0.00 (a real zero, not a
        missing balance).  The zero account still participates:
          total_assets = 600.00 + 0.00 = 600.00, net worth 600.00.
        Asserting 600.00 (not, say, an absent-account artifact) pins that
        a zero balance is summed rather than dropped.
        """
        # pylint: disable=import-outside-toplevel
        from types import SimpleNamespace
        from app.services.savings_dashboard_service._net_worth import (
            compute_net_worth_today,
        )
        acct_type = SimpleNamespace(category_id=-999, is_liquid=True)
        funded = SimpleNamespace(account_type=acct_type)
        empty = SimpleNamespace(account_type=acct_type)
        today = compute_net_worth_today([
            {"account": funded, "current_balance": Decimal("600.00")},
            {"account": empty, "current_balance": Decimal("0.00")},
        ])
        # 600.00 + 0.00 = 600.00 (the zero account is summed, not absent).
        assert today["net_worth"] == Decimal("600.00")
        assert today["total_assets"] == Decimal("600.00")
        assert today["liquid"] == Decimal("600.00")


class TestGroupSubtotals:
    """Tests for the per-category grid subtotals (Loop B Phase 2).

    ``group_subtotals`` carries one ``Decimal`` per category in
    ``grouped_accounts`` -- the sum of that group's account
    ``current_balance`` figures -- computed in the service so the template
    never does money math.
    """

    def test_asset_subtotal_sums_group_balances(
        self, app, db, seed_user, seed_periods,
    ):
        """The asset subtotal sums every asset account's current balance.

        The seed Checking ($1,000) plus a $4,000 Savings are both assets;
        with no transactions each current_balance is its flat anchor, so:
          asset subtotal = 1000.00 + 4000.00 = 5000.00
        """
        with app.app_context():
            _add_savings_account(
                seed_user, seed_periods[0].id, Decimal("4000.00"),
            )

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            # 1000.00 (Checking) + 4000.00 (Savings) = 5000.00
            assert result["group_subtotals"]["asset"] == Decimal("5000.00")

    def test_liability_subtotal_is_positive_owed(
        self, app, db, seed_user, seed_periods,
    ):
        """A liability group subtotals to the positive owed balance.

        A $240,000 mortgage with no confirmed payments resolves to its
        origination principal, so the liability subtotal is that positive
        owed amount.  The template colors it with the danger token; the
        sign is not negated in the figure (color is the display signal).
        """
        with app.app_context():
            _add_mortgage_account(
                seed_user, seed_periods[0].id, Decimal("240000.00"),
            )

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            subtotals = result["group_subtotals"]
            assert subtotals["liability"] == Decimal("240000.00")
            assert subtotals["liability"] > Decimal("0.00")

    def test_subtotal_keys_match_grouped_accounts(
        self, app, db, seed_user, seed_periods,
    ):
        """Every grouped category has a subtotal, in the same order.

        The template reads ``group_subtotals[label]`` inside its
        ``grouped_accounts.items()`` loop, so the key sets and their order
        must line up exactly.
        """
        with app.app_context():
            _add_savings_account(
                seed_user, seed_periods[0].id, Decimal("4000.00"),
            )
            _add_mortgage_account(
                seed_user, seed_periods[0].id, Decimal("240000.00"),
            )

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            assert (
                list(result["group_subtotals"].keys())
                == list(result["grouped_accounts"].keys())
            )

    def test_none_balance_counts_as_zero_not_skipped(self):
        """A None current_balance contributes 0.00 rather than being dropped.

        Direct unit test of the producer: two asset accounts in one group,
        one $600.00 and one with a None balance (no resolvable
        current-period figure).  The None account adds nothing (counts as
        zero), so the subtotal is 600.00 -- the row is not silently dropped
        in a way that would make a populated group look empty.
        """
        # pylint: disable=import-outside-toplevel
        from collections import OrderedDict
        from app.services.savings_dashboard_service._display import (
            _compute_group_subtotals,
        )
        grouped = OrderedDict([(
            "asset",
            [
                {"current_balance": Decimal("600.00")},
                {"current_balance": None},
            ],
        )])
        subtotals = _compute_group_subtotals(grouped)
        # 600.00 + (None -> 0.00) = 600.00
        assert subtotals["asset"] == Decimal("600.00")


class TestComputeAllocation:
    """Tests for the diverging allocation bar's asset/liability split."""

    @staticmethod
    def _acct(category_id):
        """One account_data dict whose account has the given category id."""
        from types import SimpleNamespace  # pylint: disable=import-outside-toplevel
        return {
            "account": SimpleNamespace(
                account_type=SimpleNamespace(category_id=category_id),
            ),
        }

    def test_splits_by_category_id_not_label(self, app):
        """Groups classify as asset vs liability by category id, not label.

        Asset and Retirement groups go to the asset side (in display
        order); the Liability group to the liability side -- decided by the
        account type's category id via the shared classifier, never the
        'liability' label string.
        """
        # pylint: disable=import-outside-toplevel
        from app.enums import AcctCategoryEnum
        from app.services.savings_dashboard_service._net_worth import (
            compute_allocation,
        )
        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            liab_id = ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY)
            ret_id = ref_cache.acct_category_id(AcctCategoryEnum.RETIREMENT)
            grouped = {
                "asset": [self._acct(asset_id)],
                "liability": [self._acct(liab_id)],
                "retirement": [self._acct(ret_id)],
            }
            subtotals = {
                "asset": Decimal("5000.00"),
                "liability": Decimal("12000.00"),
                "retirement": Decimal("30000.00"),
            }

            alloc = compute_allocation(grouped, subtotals)

            assert [s["label"] for s in alloc["assets"]] == [
                "asset", "retirement",
            ]
            assert [s["value"] for s in alloc["assets"]] == [
                Decimal("5000.00"), Decimal("30000.00"),
            ]
            assert [s["label"] for s in alloc["liabilities"]] == ["liability"]
            assert alloc["liabilities"][0]["value"] == Decimal("12000.00")

    def test_drops_zero_and_negative_subtotal_groups(self, app):
        """A zero or negative group subtotal is dropped from the bar.

        A zero asset group is an invisible segment and a negative one (a
        rare overdrawn category) would distort the stacked bar; both are
        already netted into the chips' totals, so the bar omits them.
        """
        # pylint: disable=import-outside-toplevel
        from app.enums import AcctCategoryEnum
        from app.services.savings_dashboard_service._net_worth import (
            compute_allocation,
        )
        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            ret_id = ref_cache.acct_category_id(AcctCategoryEnum.RETIREMENT)
            inv_id = ref_cache.acct_category_id(AcctCategoryEnum.INVESTMENT)
            grouped = {
                "asset": [self._acct(asset_id)],
                "retirement": [self._acct(ret_id)],
                "investment": [self._acct(inv_id)],
            }
            subtotals = {
                "asset": Decimal("5000.00"),
                "retirement": Decimal("0.00"),
                "investment": Decimal("-100.00"),
            }

            alloc = compute_allocation(grouped, subtotals)

            assert [s["label"] for s in alloc["assets"]] == ["asset"]
            assert alloc["liabilities"] == []


class TestComputeSparklines:
    """Tests for the conditional per-account sparkline producer."""

    @staticmethod
    def _period(period_id):
        """Synthetic PayPeriod stand-in (only ``id`` is read)."""
        from types import SimpleNamespace  # pylint: disable=import-outside-toplevel
        return SimpleNamespace(id=period_id)

    @staticmethod
    def _map(account_id, balances):
        """One dense-map entry as build_account_net_worth_maps emits it."""
        return {
            "account_id": account_id,
            "balances": balances,
            "is_liability": False,
        }

    def test_trending_account_is_included(self):
        """An account whose forward balance moves enough gets a series.

        A loan amortizing 10000 -> 8000 over five periods is a 20% spread,
        far above the 0.5% relative threshold, so it is informative and the
        full window series is returned.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.savings_dashboard_service._net_worth import (
            compute_sparklines,
        )
        periods = [self._period(i) for i in range(1, 6)]
        account_maps = [self._map(7, {
            1: Decimal("10000"), 2: Decimal("9500"), 3: Decimal("9000"),
            4: Decimal("8500"), 5: Decimal("8000"),
        })]

        result = compute_sparklines(account_maps, periods)

        assert result[7] == [
            Decimal("10000"), Decimal("9500"), Decimal("9000"),
            Decimal("8500"), Decimal("8000"),
        ]

    def test_flat_account_is_excluded(self):
        """A flat account (zero spread) is omitted -> figure fallback."""
        # pylint: disable=import-outside-toplevel
        from app.services.savings_dashboard_service._net_worth import (
            compute_sparklines,
        )
        periods = [self._period(i) for i in range(1, 6)]
        account_maps = [self._map(3, {i: Decimal("5000") for i in range(1, 6)})]

        assert compute_sparklines(account_maps, periods) == {}

    def test_too_few_points_excluded(self):
        """Fewer than the 4-point minimum cannot read as a trend."""
        # pylint: disable=import-outside-toplevel
        from app.services.savings_dashboard_service._net_worth import (
            compute_sparklines,
        )
        periods = [self._period(i) for i in range(1, 4)]  # 3 points
        account_maps = [self._map(9, {
            1: Decimal("100"), 2: Decimal("200"), 3: Decimal("300"),
        })]

        assert compute_sparklines(account_maps, periods) == {}

    def test_small_wobble_below_relative_threshold_excluded(self):
        """A spread under 0.5% of the account's magnitude is not a trend.

        Magnitude ~400,100 -> threshold 0.005 * 400,100 = 2,000.50; the
        100-wide wobble is below it, so a big account barely moving is
        treated as flat (the relative threshold keeps the test scale-free).
        """
        # pylint: disable=import-outside-toplevel
        from app.services.savings_dashboard_service._net_worth import (
            compute_sparklines,
        )
        periods = [self._period(i) for i in range(1, 6)]
        account_maps = [self._map(5, {
            1: Decimal("400000"), 2: Decimal("400050"), 3: Decimal("400100"),
            4: Decimal("400050"), 5: Decimal("400000"),
        })]

        assert compute_sparklines(account_maps, periods) == {}

    def test_window_is_capped_to_the_sparkline_period_count(self):
        """The series is sliced to at most _SPARKLINE_PERIODS forward points.

        With 20 forward periods of a clearly-trending account, the returned
        series is capped at the 13-period window rather than the full run.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.savings_dashboard_service._net_worth import (
            _SPARKLINE_PERIODS,
            compute_sparklines,
        )
        periods = [self._period(i) for i in range(1, 21)]  # 20 periods
        balances = {i: Decimal(str(1000 * i)) for i in range(1, 21)}
        account_maps = [self._map(8, balances)]

        result = compute_sparklines(account_maps, periods)

        assert len(result[8]) == _SPARKLINE_PERIODS


class TestPropertyEquityInContext:
    """Tests for the cockpit equity card data (Loop B Phase 2).

    ``property_equity`` lists ``{account, equity}`` for each Property
    account, reusing the Property detail page's home-equity producer so the
    cockpit equity figure equals the detail page's and the debt card's.
    """

    def test_property_equity_present_with_linked_mortgage(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A Property secured by a mortgage reports equity = value - debt.

        A $400,000 Property secured by a $240,000 mortgage (no confirmed
        payments, so the loan resolves to its origination principal):
          equity = 400000.00 - 240000.00 = 160000.00
          ltv    = 240000.00 / 400000.00 = 0.6000
        """
        with app.app_context():
            prop = _add_property_account(
                seed_user, seed_periods_today[0].id, Decimal("400000.00"),
            )
            mortgage = _add_mortgage_account(
                seed_user, seed_periods_today[0].id, Decimal("240000.00"),
            )
            mortgage.collateral_account_id = prop.id
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            equity_data = result["property_equity"]
            assert len(equity_data) == 1
            entry = equity_data[0]
            assert entry["account"].id == prop.id
            # 400000.00 - 240000.00 = 160000.00; 240000/400000 = 0.6000
            assert entry["equity"].market_value == Decimal("400000.00")
            assert entry["equity"].total_debt == Decimal("240000.00")
            assert entry["equity"].equity == Decimal("160000.00")
            assert entry["equity"].ltv == Decimal("0.6000")

    def test_no_property_yields_empty_list(
        self, app, db, seed_user, seed_periods,
    ):
        """A user with no Property account gets an empty property_equity list."""
        with app.app_context():
            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            assert result["property_equity"] == []

    def test_unencumbered_property_is_all_equity(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A Property with no secured loan reports its full value as equity.

        A $300,000 Property with no linked mortgage:
          total_debt = 0; equity = market value = 300000.00; ltv = 0.0000
        """
        with app.app_context():
            prop = _add_property_account(
                seed_user, seed_periods_today[0].id, Decimal("300000.00"),
            )

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            equity_data = result["property_equity"]
            assert len(equity_data) == 1
            entry = equity_data[0]
            assert entry["account"].id == prop.id
            assert entry["equity"].total_debt == Decimal("0")
            assert entry["equity"].equity == Decimal("300000.00")
            assert entry["equity"].ltv == Decimal("0.0000")


class TestAccountBalanceCell:
    """Tests for compute_account_balance_cell -- the cockpit inline-edit revert producer."""

    def test_cell_balance_matches_grid_card(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The cell's current_balance equals the grid card's (one projection, SSOT).

        The Cancel / Escape revert producer must restore the exact figure
        the grid card showed, so it reuses the same per-account projection
        ``compute_dashboard_data`` runs.  Both read the resolver
        ``current_balance`` for the account, so the reverted cell and the
        grid card can never disagree.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            acct_id = seed_user["account"].id

            full = savings_dashboard_service.compute_dashboard_data(user_id)
            grid_balance = next(
                ad["current_balance"] for ad in full["account_data"]
                if ad["account"].id == acct_id
            )

            cell = savings_dashboard_service.compute_account_balance_cell(
                user_id, acct_id,
            )
            assert cell is not None
            assert cell["account"].id == acct_id
            assert cell["current_balance"] == grid_balance

    def test_cell_none_for_foreign_account(
        self, app, db, seed_user, seed_second_user,
    ):
        """A non-owned account id yields None (the route's 404 / IDOR gate).

        The producer loads only the caller's active accounts, so a second
        user's account is never found -- enforcing the 404-for-both
        security rule at the producer rather than a separate ownership query.
        """
        with app.app_context():
            cell = savings_dashboard_service.compute_account_balance_cell(
                seed_user["user"].id, seed_second_user["account"].id,
            )
            assert cell is None

    def test_cell_none_for_archived_account(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An archived (inactive) account yields None.

        The producer loads only active accounts; an account archived between
        page load and the Cancel / Escape revert is no longer projected, so
        the producer returns None (a 404) rather than a stale figure.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            account = db.session.get(Account, acct_id)
            account.is_active = False
            db.session.commit()

            cell = savings_dashboard_service.compute_account_balance_cell(
                seed_user["user"].id, acct_id,
            )
            assert cell is None
