"""
Shekel Budget App -- Savings Dashboard Service Tests

Unit tests for the savings_dashboard_service module, verifying that
the extracted business logic produces correct financial computations
independently of the Flask route layer.
"""

from collections import OrderedDict
from dataclasses import replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import pytest

from app.exceptions import BaselineMissingError

from app import ref_cache
from app.enums import (
    AcctTypeEnum,
    CompoundingFrequencyEnum,
    GoalModeEnum,
    IncomeUnitEnum,
)
from app.extensions import db
from app.models.account import Account
from app.models.ref import AccountType, FilingStatus
from app.models.salary_profile import SalaryProfile
from app.models.savings_goal import SavingsGoal
from app.models.scenario import Scenario
from app.services import balance_at, savings_dashboard_service, pay_period_service
from app.services import account_service
from app.services.balance_at import BalanceContext
from app.services.savings_dashboard_service._types import AccountProjection


def _projection(account, current_balance, balances=None):
    """Build an :class:`AccountProjection` for the pure reducer unit tests.

    The reducers below read an account, a balance, and -- for the per-period
    ones -- the dense period map, so their fixtures are a stand-in account plus
    those figures.  Constructed through the REAL frozen type rather than a dict
    arranged to look like it (plan step X-t1, finding N-111): a test that builds
    its own shape stays green when production changes the one it builds, which
    is finding B-17's lesson paid on this very package.

    Args:
        account: The stand-in account (its ``account_type.category_id`` drives
            the liability classification the reducer reads).
        current_balance: The account's balance today.  Non-nullable since
            plan step X-v2 (ruling R-CA).
        balances: The dense ``period_id -> Decimal`` map (plan step X-w, ruling
            R-CG).  Defaults to EMPTY, which is honest for the today-only
            reducers -- the net-worth hero and the group subtotals read
            ``current_balance`` and never a period -- and is supplied for real
            by the per-period reducers' own tests.

    Returns:
        The :class:`AccountProjection` the reducers consume.
    """
    return AccountProjection(
        account=account,
        current_balance=current_balance,
        balances=OrderedDict() if balances is None else OrderedDict(balances),
        projected={},
        needs_setup=False,
    )


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
                # Loop B P3 slice 3c: the per-account card sparklines.
                # (The diverging allocation bar split retired with P-AC1:
                # the net-worth stream reads net_worth.series.composition
                # instead.)
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
                ad.account.name for ad in result["account_data"]
            ]
            assert "Checking" in acct_names

    def test_account_has_current_balance(
        self, app, db, seed_user, seed_periods
    ):
        """Every projection carries a current_balance of the declared type.

        The membership half of this assertion is gone with the dict (plan step
        X-t1): every field of an ``AccountProjection`` exists by construction,
        so "does the key exist" is no longer a question a test can ask or a
        consumer can get wrong.  What remains -- and what the key check never
        covered -- is the TYPE: a ``Decimal`` or the deliberate ``None``.
        """
        with app.app_context():
            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )
            for ad in result["account_data"]:
                assert isinstance(
                    ad.current_balance, (Decimal, type(None))
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
                ad.account.name for ad in grouped["asset"]
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
                ad.account.name for ad in grouped.get("asset", [])
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
            assert gd.progress_pct == Decimal("50.00")
            assert gd.current_balance == Decimal("5000.00")

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
            assert gd.current_balance == Decimal("4980.00")
            # 4980 / 5000 * 100 = 99.60, ROUND_HALF_UP via percent_complete
            # (NOT the old int()-truncated 99).
            assert gd.progress_pct == Decimal("99.60")

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
            assert gd.current_balance == Decimal("6000.00")
            # 6000 / 5000 * 100 = 120, clamped to 100.00 by percent_complete.
            assert gd.progress_pct == Decimal("100.00")

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
            assert gd.current_balance == Decimal("-500.00")
            # -500 / 5000 * 100 = -10%, floored to 0.00 by percent_complete.
            assert gd.progress_pct == Decimal("0")

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
            assert gd.resolved_target == Decimal("5000.00")
            assert gd.income_descriptor is None

    def test_dashboard_goal_data_includes_new_keys(
        self, app, db, seed_user, seed_periods
    ):
        """The goal record's field set is exactly what its consumers read.

        One of the dict's eleven keys did not become a field: ``goal_mode_id``,
        a straight copy of ``goal.goal_mode_id`` on a record that already
        carries the goal.  (This said "a twelfth key" until plan step X-w6
        recounted it -- eleven keys in, ten fields out.)  An AST
        census over ``app/`` and ``tests/`` found the copy had ZERO readers, so
        plan step X-w4 dropped it when the dict became a
        :class:`~...._goals.GoalProgress` -- finding N-100's
        published-key-with-no-consumer, in the container being typed.  This
        pins the whole set, and its ABSENCE with it, so a future step that
        re-adds a field without a reader fails here.
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
            # pylint: disable=import-outside-toplevel
            from dataclasses import fields
            assert {f.name for f in fields(gd)} == {
                "goal", "current_balance", "progress_pct", "remaining_periods",
                "required_contribution", "resolved_target",
                "income_descriptor", "has_salary_data", "trajectory",
                "monthly_contribution",
            }
            assert not hasattr(gd, "goal_mode_id"), (
                "the goal's mode is read through ``gd.goal``; a copy of it here "
                "had no reader anywhere in app/ or tests/"
            )

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
            assert gd.resolved_target > Decimal("0.00")
            assert gd.has_salary_data is True
            assert isinstance(gd.resolved_target, Decimal)

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
            assert gd.resolved_target == Decimal("0.00")
            assert gd.has_salary_data is False
            assert gd.progress_pct == 0

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
            assert gd.income_descriptor == "3.00 months of salary"

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
            assert gd.progress_pct > 0
            assert gd.resolved_target > Decimal("0.00")

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
            assert gd.progress_pct == 0
            assert gd.required_contribution is None


class TestGoalTrajectoryDashboard:
    """Integration tests for trajectory calculation in the dashboard.

    Verifies that the dashboard service correctly discovers monthly
    contributions from transfer templates and includes trajectory
    data in goal_data dicts.
    """

    def test_goal_row_carries_a_whole_trajectory(
        self, app, db, seed_user, seed_periods
    ):
        """The goal record carries a GoalTrajectory, and it is never absent.

        The non-null arm is the point (plan step X-aa, ruling R-CO).
        ``calculate_trajectory`` has three returns and every one fills all four
        fields, so ``GoalProgress.trajectory`` stopped being ``dict | None`` and
        the goal card's ``{% if gd.trajectory %}`` guard -- a truthiness test on
        an always-four-key value -- went with it.
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
            # pylint: disable=import-outside-toplevel
            from app.services.savings_goal_service import GoalTrajectory
            assert isinstance(gd.trajectory, GoalTrajectory)
            assert isinstance(gd.monthly_contribution, Decimal)

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
            assert gd.monthly_contribution == Decimal("0.00")
            assert gd.trajectory.months_to_goal is None

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
            assert gd.monthly_contribution == Decimal("500.00")
            # remaining = 6000 - 3000 = 3000, months = ceil(3000/500) = 6
            assert gd.trajectory.months_to_goal == 6


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
                if ad.account.id == acct.id
            )
            assert loan_ad.loan.figures.is_paid_off is True

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
                if ad.account.id == acct.id
            )
            assert loan_ad.loan.figures.is_paid_off is False

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
                if ad.account.id == acct.id
            )
            assert loan_ad.loan.figures.is_paid_off is False

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
                if ad.account.id == acct.id
            )
            assert loan_ad.loan.figures.is_paid_off is False

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
                if ad.account.name == "Checking"
            )
            assert checking_ad.loan is None

    def test_paid_off_false_no_loan_params(
        self, app, db, seed_user, seed_periods,
    ):
        """A loan-TYPE account with no LoanParams carries no figures, no crash.

        Since plan step X-r the projection dict carries the seam's
        ``LoanFigures`` whole rather than five flattened copies of its fields,
        so "not a configured loan" is the bundle's ABSENCE -- which is what
        the tile, the debt summary and the debt-line selection all gate on.
        """
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
                if ad.account.id == acct.id
            )
            assert loan_ad.loan is None


class TestPaidOffReadsTheLedgerNotTheReplay:
    """``is_paid_off`` is a LEDGER read, not a schedule replay.

    The flag used to be answered by ``resolve_loan(inputs, date.max)`` with no
    ``confirmed_view`` -- a producer that structurally cannot consult the genesis
    ledger (the confirmed view returns ``None`` for any ``as_of`` after
    today) and that is BLIND TO MONEY: the replay advances one SCHEDULED step per
    confirmed payment and discards the cash actually paid.

    Each test here pins the fix by ALSO evaluating that retired producer, so the
    assertion is non-vacuous in both directions: the replay genuinely disagrees
    with the ledger on this data, and the app now follows the ledger.
    """

    def _replay_says_paid_off(self, acct, scenario_id):
        """Return what the RETIRED date.max replay probe would have answered.

        The exact call the old ``_loan_ever_paid_off`` made: no ``confirmed_view``
        (so no ledger), no ``extra_principal``, ``as_of=date.max``.
        """
        # Pylint: import-outside-toplevel -- the file-wide deferred-import
        # convention for test-local symbols.
        # pylint: disable=import-outside-toplevel
        from app.services import loan_resolver
        from app.services.loan_loaders import (
            load_loan_anchor_facts, load_loan_params,
        )
        from app.services.loan_payment_service import load_loan_context
        from app.services.loan_resolver._periods import _replay_from_anchor
        from app.utils.money import round_money

        params = load_loan_params(acct.id)
        loan_ctx = load_loan_context(acct.id, scenario_id, params)
        inputs = loan_resolver.LoanInputs(
            params, load_loan_anchor_facts(params),
            loan_ctx.payments, loan_ctx.rate_changes,
        )
        # The replay derivation directly (``LoanState.current_balance`` carried
        # it until plan step D2a deleted the field): the same money-blind
        # schedule-step probe the retired ``_loan_ever_paid_off`` ran.
        replayed = round_money(_replay_from_anchor(
            inputs,
            loan_resolver.resolve_periods(params, inputs.rate_changes),
            date.max,
        ).balance_as_of)
        return replayed == Decimal("0.00")

    def test_off_schedule_payoff_needs_no_trueup_band_aid(
        self, app, db, seed_user, seed_periods_today,
    ):
        """One lump-sum settled payment retires the loan -- no true-up required.

        A $1,000 / 24-month loan whose scheduled principal is ~$40/mo, retired by
        a SINGLE settled $1,100 payment.  The genesis ledger books the real
        principal, so it reads $0.00 owed and the tile's balance is $0.

        The retired replay takes ONE ~$40 scheduled step and still owes ~$960, so
        it answered "not paid off" -- which is why the app previously required the
        operator to record a manual balance true-up to $0 after any lump-sum
        payoff (a band-aid for a producer that could not see the cash).  Reading
        the ledger removes that requirement: the payment alone is enough.
        """
        # Pylint: import-outside-toplevel -- the file-wide deferred-import
        # convention for test-local symbols.
        # pylint: disable=import-outside-toplevel
        from tests._test_helpers import create_settled_transfer, settle_instant_on

        with app.app_context():
            acct = _create_small_loan(seed_user, db.session)
            # The production settle chokepoint, so the payment posts its REAL
            # split to the genesis ledger exactly as a live settle does.  Settled
            # on the current period's start (a past date), so it is visible today
            # under C2's settled-date clock regardless of the UTC/display offset.
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], acct,
                seed_periods_today[4], amount=Decimal("1100.00"),
                paid_at=settle_instant_on(seed_periods_today[4].start_date),
            )
            db.session.commit()

            # NON-VACUITY: the retired producer disagrees on exactly this data.
            assert self._replay_says_paid_off(
                acct, seed_user["scenario"].id,
            ) is False, (
                "fixture regressed: the replay must still owe here, or this "
                "test no longer pins the ledger-vs-replay divergence"
            )

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            loan_ad = next(
                ad for ad in result["account_data"]
                if ad.account.id == acct.id
            )
            # The ledger booked the real principal: nothing is owed.
            assert loan_ad.current_balance == Decimal("0.00")
            assert loan_ad.loan.figures.is_paid_off is True

    def test_short_paid_loan_never_vanishes_from_total_debt(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A loan paid SHORT stays in total_debt, even when the replay hits zero.

        The dangerous inverse.  A $1,000 / 2-month loan's scheduled payment is
        ~$502, so TWO confirmed payments exhaust the replay's term and drive its
        balance to $0.00 -- regardless of how little cash was actually paid.  Here
        each payment is only $100, so the ledger (which books the REAL principal)
        still owes several hundred dollars.

        Under the retired probe that made ``is_paid_off`` True, and
        ``_metrics._loan_current_balance`` drops a paid-off loan from
        ``total_debt`` -- so real, still-owed debt silently vanished from the debt
        card and its full original principal counted as paid.  The ledger read
        keeps the loan on the books.
        """
        # Pylint: import-outside-toplevel -- the file-wide deferred-import
        # convention for test-local symbols.
        # pylint: disable=import-outside-toplevel
        from tests._test_helpers import create_settled_transfer

        with app.app_context():
            acct = _create_small_loan(seed_user, db.session, term=2)
            for idx in (3, 4):
                create_settled_transfer(
                    seed_user, db.session, seed_user["account"], acct,
                    seed_periods_today[idx], amount=Decimal("100.00"),
                )
            db.session.commit()

            # NON-VACUITY: the retired producer really did call this paid off.
            assert self._replay_says_paid_off(
                acct, seed_user["scenario"].id,
            ) is True, (
                "fixture regressed: the replay must reach zero here, or this "
                "test no longer pins the debt-vanishing regression"
            )

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            loan_ad = next(
                ad for ad in result["account_data"]
                if ad.account.id == acct.id
            )
            # The ledger still owes, so the loan is NOT paid off ...
            assert loan_ad.current_balance > Decimal("0.00")
            assert loan_ad.loan.figures.is_paid_off is False
            # ... and it therefore still counts toward the debt card's total.
            assert result["debt_summary"] is not None
            assert (
                result["debt_summary"].total_debt
                >= loan_ad.current_balance
            )


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
            assert result["archived_accounts"][0].account.name == "Old Savings"

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
                ad.account.name for ad in result["account_data"]
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
        """An archived row carries the last anchor balance and nothing else.

        The field is ``last_anchor_balance`` since plan step X-w2 (ruling
        R-CH): it is the ``current_anchor_balance`` COLUMN, not the
        seam-derived balance the live tiles call ``current_balance``, and an
        archived account gets no seam read at all.  The negative arms pin BOTH
        -- the old name must not come back, and no projection field may appear
        on a shape that is deliberately not an ``AccountProjection``.
        """
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
            archived_row = result["archived_accounts"][0]
            assert archived_row.last_anchor_balance == Decimal("3000.00")
            assert not hasattr(archived_row, "current_balance"), (
                "the archived drawer's figure is the anchor COLUMN, not the "
                "seam-derived balance the live tiles publish under that name"
            )
            assert not hasattr(archived_row, "projected")


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
        :class:`~..._metrics.DebtSummary` the full ``compute_dashboard_data``
        build emits -- every money figure, the payoff outlook, the revolving
        caveat and the DTI block, since both route through the shared
        ``_debt_summary_with_dti``.  The salary makes the DTI leg
        non-vacuous: $78,000 / 26 = $3,000 gross biweekly -> $6,500
        gross monthly, so the block is a live value on both sides rather than
        None == None.  WHOLE-VALUE equality (not per-field spot checks) so a
        future field populated on one path but not the other fails here -- a
        frozen dataclass compares on every field, which is the property that
        made this assertion survive the dict's retirement at plan step X-s3.
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
            # The DTI leg is live, not the vacuous None == None.  $78,000 / 26
            # = $3,000 biweekly -> $3,000 * 26 / 12 = $6,500 monthly, so the
            # ratio is the PITI total over $6,500 -- which pins the engine
            # denominator without the summary storing it (plan step X-s3).
            assert full.dti is not None
            assert full.dti.ratio == (
                full.total_monthly_payments / Decimal("6500.00")
                * Decimal("100")
            ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
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
            assert ds.total_debt == Decimal("1000.00")
            # weighted_avg_rate = single loan's rate = 0.05000
            assert ds.weighted_avg_rate == Decimal("0.05000")
            assert ds.total_monthly_payments > Decimal("0.00")
            assert ds.payoff_outlook.all_clear_on is not None

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
        from tests._test_helpers import create_loan_account  # pylint: disable=import-outside-toplevel

        with app.app_context():
            create_loan_account(
                seed_user, db.session, name="Mortgage",
                principal=Decimal("200000.00"),
                rate=Decimal("0.06500"),  # DH-#56 origination rate
                term=360,
                origination_date=date(2024, 1, 1),
                payment_day=1,
                account_type=AcctTypeEnum.MORTGAGE,
            )
            create_loan_account(
                seed_user, db.session, name="Auto",
                principal=Decimal("25000.00"),
                rate=Decimal("0.04900"),  # DH-#56 origination rate
                term=60,
                origination_date=date(2024, 6, 1),
                payment_day=15,
                account_type=AcctTypeEnum.AUTO_LOAN,
            )

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            ds = result["debt_summary"]
            assert ds.total_debt == Decimal("225000.00")
            # Hand-calc: (200000*0.065 + 25000*0.049) / 225000
            #          = 14225 / 225000 = 0.063222...
            assert ds.weighted_avg_rate == Decimal("0.06322")

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
            assert ds.total_debt == Decimal("2000.00")

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
            assert ds.total_debt == Decimal("0.00")
            assert ds.total_monthly_payments == Decimal("0.00")
            assert ds.weighted_avg_rate == Decimal("0.00000")
            assert ds.payoff_outlook.all_clear_on is None

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
            assert ds.total_debt == Decimal("1000.00")

    def test_debt_free_date_is_latest(
        self, app, db, seed_user, seed_periods,
    ):
        """C-5.12-12: Debt-free date is the latest payoff across loans.

        A short-term loan (24 months) and a long-term mortgage (360
        months).  The debt-free date should match the mortgage's payoff.
        """
        from tests._test_helpers import create_loan_account  # pylint: disable=import-outside-toplevel

        with app.app_context():
            # Short-term loan
            _create_small_loan(
                seed_user, db.session, name="Short Loan", term=24,
            )

            # Long-term mortgage
            create_loan_account(
                seed_user, db.session, name="Long Mortgage",
                principal=Decimal("200000.00"),
                rate=Decimal("0.06500"),  # DH-#56 origination rate
                term=360,
                origination_date=date(2024, 1, 1),
                payment_day=1,
                account_type=AcctTypeEnum.MORTGAGE,
                anchor_balance=Decimal("0"),
            )

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            ds = result["debt_summary"]
            # The mortgage payoff is decades away; auto loan is < 2 years.
            # Debt-free date should be the mortgage's later payoff.
            assert ds.payoff_outlook.all_clear_on is not None
            # The auto loan payoff is within ~21 months of origination
            # (Jan 2026 + 21 months ~ Oct 2027).  The mortgage payoff is
            # 360 months from Jan 2024 ~ Jan 2054.  Debt-free = mortgage.
            assert ds.payoff_outlook.all_clear_on.year > 2030

    def test_debt_summary_includes_escrow(
        self, app, db, seed_user, seed_periods,
    ):
        """C-5.12-9: Escrow components are included in monthly total.

        A mortgage with $7,200/year escrow ($600/month).  The monthly
        total must include P&I + escrow.
        """
        from tests._test_helpers import (  # pylint: disable=import-outside-toplevel
            add_escrow_line,
            create_loan_account,
            loan_params_for,
        )

        with app.app_context():
            mortgage = create_loan_account(
                seed_user, db.session, name="Escrow Mortgage",
                principal=Decimal("200000.00"),
                rate=Decimal("0.06500"),  # DH-#56 origination rate
                term=360,
                origination_date=date(2024, 1, 1),
                payment_day=1,
                account_type=AcctTypeEnum.MORTGAGE,
                anchor_balance=Decimal("0"),
            )
            params = loan_params_for(db.session, mortgage.id)

            add_escrow_line(
                db.session, mortgage.id, "Property Tax", Decimal("7200.00"),
                effective_date=params.origination_date,
            )
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            ds = result["debt_summary"]
            # P&I for a $200K, 6.5%, 360-month loan is ~$1,264.
            # With $600/month escrow, total should exceed $1,800.
            assert ds.total_monthly_payments > Decimal("1800.00")
            # Verify escrow is included: total > P&I alone.
            from app.services import amortization_engine as ae
            pi_only = ae.calculate_monthly_payment(
                Decimal("200000.00"), Decimal("0.06500"),
                ae.calculate_remaining_months(date(2024, 1, 1), 360),
            )
            assert ds.total_monthly_payments > pi_only


class TestPrincipalPaidFraction:
    """Tests for ``DebtSummary.principal_paid_fraction`` (Loop B B-1, X-u).

    The debt track marker's figure: the aggregate fraction of original loan
    principal paid down so far.  Per
    the 2026-06-12 ruling (``dashboard_card_audit.md`` Rebuild decisions
    item 4) it sums over ALL loans ever originated -- paid-off loans stay
    in both the numerator and the denominator -- so the fraction is
    monotonic, reaches exactly ``1`` at full payoff, and stays there.

    It was its own narrow producer (``compute_debt_principal_progress``) until
    plan step X-u, which folded it into the summary that the same consumer
    already reads: one dashboard render was running the whole load -> params ->
    project pipeline TWICE, once per debt producer (finding N-109).  These
    tests read the summary field, which is what the dashboard reads.

    ``original_principal`` is a NOT NULL, ``> 0`` column, so any ORIGINATED
    loan supplies the denominator.  "No loans at all" is not a case here --
    there is no summary to carry the field then, which
    ``TestDebtSummary::test_narrow_producer_none_when_no_loans`` already pins.
    """

    def test_fraction_none_when_every_loan_is_unborrowed(
        self, app, db, seed_user, seed_periods,
    ):
        """A configured-but-unclosed loan -> a summary whose fraction is None.

        The REACHABLE state in which the summary exists and the fraction does
        not, which the dashboard rail renders bare.  A mortgage originating
        2026-04-15 under this module's frozen 2026-03-20 today has not been
        borrowed: it owes ``$0.00``, its whole debt line is ahead of it, and
        ``_compute_principal_paid_fraction`` puts it in NEITHER sum -- so the
        denominator is zero and the honest answer is "no progress to report",
        not ``0%`` (which would say the borrower has repaid none of a debt they
        do not yet have) and not ``100%``.

        ``dashboard/_tracks.html`` claimed until plan step X-u that no-loans was
        the only reachable case for a bare rail; this is the case that claim
        missed, and the summary is non-``None`` here precisely because the loan
        DOES have a payoff date to caption.
        """
        from app.enums import AcctTypeEnum  # pylint: disable=import-outside-toplevel
        from tests._test_helpers import create_loan_account  # pylint: disable=import-outside-toplevel

        with app.app_context():
            create_loan_account(
                seed_user, db.session, name="Closing In April",
                principal=Decimal("200000.00"), rate=Decimal("0.05000"),
                term=360, origination_date=date(2026, 4, 15), payment_day=1,
                account_type=AcctTypeEnum.MORTGAGE,
                anchor_period=seed_periods[0],
            )

            summary = savings_dashboard_service.compute_debt_summary(
                seed_user["user"].id,
            )
            # The fixture really is in that state: a loan with a debt line
            # ahead of it that owes nothing today.
            assert summary is not None
            assert summary.total_debt == Decimal("0.00")
            assert summary.payoff_outlook.all_clear_on is not None
            assert summary.principal_paid_fraction is None

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
            fraction = summary.principal_paid_fraction
            assert isinstance(fraction, Decimal)
            assert Decimal("0") <= fraction <= Decimal("1")
            # Reconcile against the debt summary's current balance.
            expected = (
                (Decimal("1000.00") - summary.total_debt) / Decimal("1000.00")
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

            summary = savings_dashboard_service.compute_debt_summary(
                seed_user["user"].id,
            )
            assert summary.principal_paid_fraction == Decimal("0")

    def test_fraction_one_when_all_loans_paid_off(
        self, app, db, seed_user, seed_periods,
    ):
        """All loans paid off -> the fraction is exactly 1 (full payoff).

        A loan trued-up to $0 is paid off.  Under the all-loans-ever basis
        it stays in BOTH sums, contributing $0 to the current-balance sum
        and its full $1,000.00 original principal to the denominator, so:
            (1000.00 - 0.00) / 1000.00 = 1.
        The fraction reaches 1 at full payoff and is NOT None -- None is
        reserved for the no-loan-has-originated case.  The SAME summary
        still reports total_debt $0.00 (active-loans-only), so the two
        figures on ONE value object deliberately disagree on which loans count:
        that is the property plan step X-u had to preserve when it merged the
        two producers, and it is asserted on one object here rather than across
        two calls.
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
            assert summary.total_debt == Decimal("0.00")
            # ... but the principal-paid fraction on the SAME summary is
            # exactly 1: the paid-off loan keeps its full original principal in
            # both sums.  (1000.00 - 0.00) / 1000.00 = 1.
            assert summary.principal_paid_fraction == Decimal("1")

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

            fraction = savings_dashboard_service.compute_debt_summary(
                seed_user["user"].id,
            ).principal_paid_fraction
            # (orig_A + orig_B - balance_B) / (orig_A + orig_B)
            # = (1000.00 + 1000.00 - 400.00) / (1000.00 + 1000.00)
            # = 1600.00 / 2000.00 = 0.8.
            expected = (
                (Decimal("1000.00") + Decimal("1000.00") - Decimal("400.00"))
                / (Decimal("1000.00") + Decimal("1000.00"))
            )
            assert fraction == expected
            assert fraction == Decimal("0.8")


class TestDebtSummaryMembershipRules:
    """ONE summary, THREE loan sets -- the control plan step X-u needed.

    Ruling R-BI declined to merge the principal-paid producer into the debt
    summary inside plan step X-s3 on one ground: the two answer over DIFFERENT
    membership rules, and merging them re-opens the question ruling X-q settled
    at a measured cost of 19 years (finding N-98, where a debt-free caption
    derived over the owed-today set reported the date the OTHER loans finish).
    X-u merged them anyway, having first measured that both rules are REDUCERS
    over one projection rather than two loan sets -- and this is the fixture
    that would catch a merge that re-decided either one.

    Three loans, chosen so the three sets are pairwise DIFFERENT:

    * **A, owing** -- $1,000.00 originated 2026-01-01, trued up to a known
      $400.00.  In owed-today AND in all-loans-ever AND on the debt line.
    * **B, retired** -- $1,000.00 originated 2026-01-01, trued up to $0.00.
      OUT of owed-today (it owes nothing) and OFF the debt line (retired), but
      IN all-loans-ever, contributing its full principal as repaid.
    * **C, unborrowed** -- a $200,000.00 mortgage originating 2026-04-15,
      after this module's frozen 2026-03-20 today.  OUT of owed-today and OUT
      of all-loans-ever (nothing of it has been repaid because none of it has
      been borrowed), but ON the debt line, whose whole 30 years are ahead.

    So owed-today is ``{A}``, all-loans-ever is ``{A, B}``, and the debt line
    is ``{A, C}``.  No two rules can be swapped for each other without a
    figure below moving.
    """

    AUTO = Decimal("1000.00")
    MORTGAGE = Decimal("200000.00")
    OWING_BALANCE = Decimal("400.00")

    def _three_loans(self, seed_user, db_session, periods):
        """Build the owing / retired / unborrowed triple.  Returns all three."""
        # pylint: disable=import-outside-toplevel
        from app.enums import AcctTypeEnum
        from tests._test_helpers import create_loan_account, insert_trueup_event

        owing = create_loan_account(
            seed_user, db_session, name="Owing Auto",
            principal=self.AUTO, rate=Decimal("0.05000"),
            term=24, origination_date=date(2026, 1, 1), payment_day=1,
            account_type=AcctTypeEnum.AUTO_LOAN, anchor_period=periods[0],
        )
        retired = create_loan_account(
            seed_user, db_session, name="Retired Auto",
            principal=self.AUTO, rate=Decimal("0.05000"),
            term=24, origination_date=date(2026, 1, 1), payment_day=1,
            account_type=AcctTypeEnum.AUTO_LOAN, anchor_period=periods[0],
        )
        unborrowed = create_loan_account(
            seed_user, db_session, name="Closing In April",
            principal=self.MORTGAGE, rate=Decimal("0.05000"),
            term=360, origination_date=date(2026, 4, 15), payment_day=1,
            account_type=AcctTypeEnum.MORTGAGE, anchor_period=periods[0],
        )
        insert_trueup_event(owing.loan_params, self.OWING_BALANCE)
        insert_trueup_event(retired.loan_params, Decimal("0.00"))
        db_session.commit()
        return owing, retired, unborrowed

    def test_one_summary_answers_three_membership_rules(
        self, app, db, seed_user, seed_periods,
    ):
        """Each figure counts its own loans, and no two count the same ones.

        The arithmetic, all three rules on one :class:`DebtSummary`:

        * ``total_debt`` is owed-today ``{A}``: $400.00.  B owes nothing and
          C has not been borrowed, so both are skipped by
          ``_loan_ad_current_principal``.
        * ``total_monthly_payments`` is the SAME owed-today set, which is what
          makes it the discriminating figure here: A's payment on $1,000.00 is
          under $50/mo, while C's alone on $200,000.00 at 5% for 360 months is
          about $1,073/mo.  A rule that counted C would be off by an order of
          magnitude.
        * ``principal_paid_fraction`` is all-loans-ever ``{A, B}``:
          (1000.00 + 1000.00 - 400.00) / (1000.00 + 1000.00) = 0.8.  Counting C
          would read (202000.00 - 400.00) / 202000.00 = 0.998; counting only
          the owed-today set would read (1000.00 - 400.00) / 1000.00 = 0.6.
        * ``payoff_outlook`` is the debt line ``{A, C}``: the LATEST payoff, so
          past 2028 -- A's 24-month term from 2026-01-01 ends in 2027 even
          before its true-up shortens it, so only C can put the date there.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.savings_dashboard_service._debt_line import (
            debt_line_loans,
        )

        with app.app_context():
            owing, retired, unborrowed = self._three_loans(
                seed_user, db.session, seed_periods,
            )
            bctx = BalanceContext.build(seed_user["user"].id)

            # Premises about the FIXTURE, read from the seam directly and for
            # ALL THREE loans: a fixture that silently built three identical
            # loans would pass every assertion below for the wrong reason (the
            # X-t5 lesson -- a new fixture is a new control and it can be born
            # dead; this block claimed three loans and read one until X-u's own
            # adversarial review counted them).
            owing_figures = balance_at.loan_figures(owing, bctx)
            assert owing_figures.terms.is_originated is True
            assert owing_figures.is_retired is False
            assert balance_at.balance_at(
                owing, bctx, bctx.as_of,
            ) == self.OWING_BALANCE

            # B: originated and retired -- owes nothing, off the debt line.
            retired_figures = balance_at.loan_figures(retired, bctx)
            assert retired_figures.terms.is_originated is True
            assert retired_figures.is_retired is True
            assert balance_at.balance_at(
                retired, bctx, bctx.as_of,
            ) == Decimal("0.00")

            # C: not borrowed -- owes nothing for the OTHER reason, and its
            # whole debt line is ahead of it.
            unborrowed_figures = balance_at.loan_figures(unborrowed, bctx)
            assert unborrowed_figures.terms.is_originated is False
            assert unborrowed_figures.is_retired is False
            assert balance_at.balance_at(
                unborrowed, bctx, bctx.as_of,
            ) == Decimal("0.00")
            # The two zero balances are the same number for different reasons,
            # which is the whole hazard the three rules exist to separate.

            summary = savings_dashboard_service.compute_debt_summary(
                seed_user["user"].id,
            )
            assert summary is not None

            # Owed-today {A}.
            assert summary.total_debt == self.OWING_BALANCE
            # Owed-today again, and the figure that separates it from the debt
            # line: only A's payment is in here.
            assert summary.total_monthly_payments == (
                owing_figures.terms.monthly_payment
            )
            assert summary.total_monthly_payments < Decimal("50.00")

            # All-loans-ever {A, B}: B's full principal counts as repaid, C is
            # in neither sum.
            expected_fraction = (
                (self.AUTO + self.AUTO - self.OWING_BALANCE)
                / (self.AUTO + self.AUTO)
            )
            assert summary.principal_paid_fraction == expected_fraction
            assert summary.principal_paid_fraction == Decimal("0.8")

            # The debt line {A, C}: two loans, and the date is C's.
            assert summary.payoff_outlook.never_clears is False
            assert summary.payoff_outlook.all_clear_on > date(2028, 1, 1)

            # And the set itself, so the assertion above cannot pass because
            # some OTHER loan happens to date that late.
            full = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )["account_data"]
            assert {
                ad.account.name for ad in debt_line_loans(full)
            } == {"Owing Auto", "Closing In April"}


class TestDTI:
    """Tests for debt-to-income ratio computation.

    DTI = total_monthly_payments / gross_monthly * 100.
    Gross monthly = gross_biweekly * 26 / 12 (biweekly, not semi-monthly).
    """

    def test_dti_no_salary(
        self, app, db, seed_user, seed_periods,
    ):
        """C-5.12-6: Loans exist but no salary profile yields no DTI block.

        The whole block is absent, not three fields set to ``None`` (plan step
        X-s3): "no income source" is ONE state and is now spelled once.
        """
        with app.app_context():
            _create_small_loan(seed_user, db.session)

            result = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            ds = result["debt_summary"]
            assert ds is not None
            assert ds.dti is None

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
            assert ds.dti is not None
            assert isinstance(ds.dti.ratio, Decimal)
            assert ds.dti.label == "healthy"
            # The $6,500 denominator, pinned through the ratio: $78,000 / 26 =
            # $3,000 biweekly -> $3,000 * 26 / 12 = $6,500 monthly.  The gross
            # is not stored on the value object (plan step X-s3 -- an input
            # already spent, with no app/ reader), so the assertion divides by
            # it instead of reading it back.
            assert ds.dti.ratio == (
                ds.total_monthly_payments / Decimal("6500.00")
                * Decimal("100")
            ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)

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
            assert ds.dti is not None
            assert ds.dti.ratio == Decimal("0.0")
            assert ds.dti.label == "healthy"

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
            # Off-engine pre-fix value was $8,666.67 (raise dropped); see the
            # class docstring for the arithmetic.  The denominator is pinned by
            # the ratio identity below rather than read off the value object,
            # which no longer stores it (plan step X-s3) -- and the identity is
            # the stronger pin, since the off-engine $8,666.67 would fail it.
            # total_monthly_payments is the engine-derived monthly P&I
            # from _create_small_loan ($1,000 @ 5% for 24mo); we
            # consume it as an input here so the test pins behaviour
            # without re-deriving the amortization engine's output.
            expected_dti = (
                ds.total_monthly_payments / Decimal("8926.67")
                * Decimal("100")
            ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            assert ds.dti.ratio == expected_dti

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
            # DTI ratio matches the pre-fix calculation (no regression), and
            # dividing by $6,500 is what pins the denominator now that the
            # value object does not store it (plan step X-s3).
            expected_dti = (
                ds.total_monthly_payments / Decimal("6500.00")
                * Decimal("100")
            ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            assert ds.dti.ratio == expected_dti

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
        # ``params["salary_gross_biweekly"]`` or the dataclass attribute
        # ``params.salary_gross_biweekly``.  That field was REMOVED from
        # ``_AccountParams`` in the balance-seam cleanup (the seam assembles
        # the gross itself, so carrying it here was dead state), so a
        # regression would have to re-add the field AND read it here; this
        # AST guard catches that second step.  ``compute_debt_summary`` is
        # scanned too so the narrow #82 path cannot regress independently.
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
            # Engine-derived gross_monthly is $4,291.67 (see class + test
            # docstring); pinned through the ratio, since plan step X-s3
            # stopped storing the denominator on the value object.
            assert ds.dti.ratio == (
                ds.total_monthly_payments / Decimal("4291.67")
                * Decimal("100")
            ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            # Small loan P&I is well under 36% of $4,291.67, so the
            # band is 'healthy' under the engine denominator.
            assert ds.dti.label == "healthy"
            # And the ratio is strictly less than 36 (boundary check).
            assert ds.dti.ratio < Decimal("36.0")


# ── Commit 6: canonical entries-aware producer routing ─────────────
#
# Pre-Commit-6 the savings dashboard built its own transaction query
# without ``selectinload(Transaction.entries)`` and called the engine
# directly.  When an envelope expense had cleared debit entries, the
# silent-degrade seam in ``cash_ledger._amounts._entry_aware_amount``
# (removed at the math layer by Commit 5) returned
# ``effective_amount`` unchanged.  Result: the same data shipped
# $160.00 on the grid and $114.29 on /savings -- symptom #1.  Commit 6
# routes the savings dashboard through
# ``balance_resolver.balances_for``, which owns the query and eager-
# loads entries, so the two surfaces produce byte-identical values
# by construction.


def _override_anchor(db_session, account, pay_period, anchor_balance):
    """Replace ``account``'s current anchor with the given balance + period.

    Thin wrapper over the shared :func:`tests._test_helpers.override_anchor`
    (which stamps the assertion inside its own period -- see its docstring for
    why that instant is load-bearing) plus this suite's commit boundary.
    Required because the ``seed_user`` factory writes an origination anchor of
    $1,000 against the seed_periods anchor period; tests reproducing symptom #1
    need $614.29 on a chosen period.

    Args:
        db_session: SQLAlchemy session bound to the test database.
        account: The :class:`~app.models.account.Account` whose anchor
            should be overridden.
        pay_period: The :class:`~app.models.pay_period.PayPeriod` the
            new anchor is anchored against.
        anchor_balance: The new anchor balance as a Decimal.
    """
    from tests._test_helpers import override_anchor  # pylint: disable=import-outside-toplevel

    override_anchor(
        db_session, account, pay_period, anchor_balance,
        notes="C6 symptom-#1 test: anchor override",
    )
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

        Both the grid and the savings dashboard MUST return
        Decimal("160.00") -- one seam entry, ``balance_at.cash_balance_map``,
        read twice.  Pre-Commit-6, /savings returned Decimal("114.29") via the
        silent-degrade seam.

        The $160.00 survives the basis change at plan step X-c2b2 for a reason
        this fixture makes plain: the account holds ONE asserted balance and the
        only row is still PROJECTED, so the fold has the same assertion to
        replay and the same entries-aware reservation to hold back.  The two
        bases diverge only where money has SETTLED, and nothing here has.
        """
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

            # Grid value: ``balance_at.grid_balance_view`` is the seam entry the
            # grid's balance row reads, so replaying it here is "what does the
            # grid show" without a route round-trip.  It has been repointed
            # TWICE for the same reason -- a guard that had stopped guarding
            # (the N-63 shape): from ``_cash_engine.balances_for`` at plan step
            # X-c2b2, and from ``cash_balance_map`` at X-g3b, where the grid
            # stopped reading the cash view for a modelled account (ruling
            # R-W).  Corrected here rather than left as prose, because the whole
            # point of the test is that the two surfaces read ONE producer --
            # and this account is PLAIN, where they agree by construction.
            grid_current_balance = balance_at.grid_balance_view(
                seed_user["account"],
                BalanceContext.build(seed_user["user"].id),
                seed_periods_today,
            ).columns[current_period.id].balance

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
                if ad.account.id == seed_user["account"].id
            )
            assert checking_ad.current_balance == Decimal("160.00")
            assert checking_ad.current_balance == grid_current_balance

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
                if ad.account.id == hysa.id
            )

            # base = 614.29 - max(500 - 45.71 - 0, 0)
            #     = 614.29 - 454.29 = 160.00 (entry-aware reduction)
            # plus a small positive interest accrual at 4.5%% APY for
            # the anchor period.  Pre-Commit-5 the entries were
            # silently unloaded and the base would have been
            # 614.29 - 500.00 = 114.29.  We require strictly greater
            # than 114.29 + interest noise (a 100x margin from the
            # 45.71 gap) to lock the entry-aware semantics:
            assert hysa_ad.current_balance > Decimal("159.00")
            assert hysa_ad.current_balance < Decimal("161.00")

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
                if ad.account.id == seed_user["account"].id
            )
            # 614.29 - max(500 - 0 - 0, 0) = 614.29 - 500.00 = 114.29.
            # Identical to the pre-Commit-6 value for this no-entries
            # case; the formula reduces to ``effective_amount`` when
            # the entry buckets are all zero.
            assert checking_ad.current_balance == Decimal("114.29")


# ── F-21 / Commit 19: Loan period-balance dispatcher ──────────────


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


def _add_mortgage_account(
    seed_user, anchor_period_id, balance, origination_date=None,
):
    """Create a Mortgage (liability) account with a loan schedule.

    Mortgage at 6.5%, 30-year, defaulting to a 2025-01-01 origination so the
    resolver's as-of-today current balance equals the origination principal.
    *origination_date* is overridable: since step C6b the forward liability is a
    FOLD of the loan's payment PLAN (not a schedule walk), so a loan originated in
    the past with NO payment records is delinquent -- its unpaid overdue
    installments never pay it down (finding B-9), and only the FUTURE contractual
    installments are synthesized, which cannot make up the gap.  A test that needs
    a loan to amortize cleanly to ZERO must originate it with no overdue gap (pass
    ``date.today()``), the realistic on-schedule case.

    Routed through the shared ``create_loan_account`` factory rather than
    re-rolling the account + ``LoanParams`` + rate block: the hand-rolled copy this
    replaces never opened the loan's genesis posting ledger, so every mortgage in
    this suite ran on the no-ledger fallback -- a path production never takes.

    Returns:
        The new mortgage Account.
    """
    # pylint: disable=import-outside-toplevel
    from datetime import date as _date
    from app.enums import AcctTypeEnum
    from app.models.pay_period import PayPeriod
    from tests._test_helpers import create_loan_account

    return create_loan_account(
        seed_user, db.session, name="Home Mortgage",
        principal=balance, rate=Decimal("0.06500"), term=360,
        origination_date=origination_date or _date(2025, 1, 1), payment_day=1,
        account_type=AcctTypeEnum.MORTGAGE,
        anchor_period=db.session.get(PayPeriod, anchor_period_id),
    )


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
            assert nw.today.total_assets == Decimal("5000.00")
            # Mortgage resolver current balance = origination principal.
            assert nw.today.total_liabilities == Decimal("240000.00")
            # 5000.00 - 240000.00 = -235000.00
            assert nw.today.net_worth == Decimal("-235000.00")

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

            assert nw.today.total_liabilities == Decimal("240000.00")
            assert nw.today.total_liabilities > Decimal("0.00")

    def test_a_negative_balance_liability_still_adds_its_magnitude(
        self, app, db, seed_user, seed_periods,
    ):
        """A liability whose balance is stored NEGATIVE adds its magnitude.

        The reduction is ``total_liabilities += abs(balance)``, so the sign a
        liability happens to be stored with must not change net worth.  A
        Credit Card's cash balance is negative (money owed leaves the
        account), unlike a mortgage's positive owed figure -- so this is the
        shape that actually exercises the ``abs``.  Every other liability in
        this file's fixtures is stored POSITIVE, where ``abs`` is a no-op and
        a regression to a bare ``+= balance`` would pass green.

        Checking ($1,000) is the only asset; the card anchors at -$500.00:
          total_assets       = 1000.00
          total_liabilities  = abs(-500.00) = 500.00
          net_worth          = 1000.00 - 500.00 = 500.00

        Without the ``abs`` the card would ADD to net worth
        (1000.00 - -500.00 = 1500.00), reporting a debt as an asset.
        """
        with app.app_context():
            cc_type = (
                db.session.query(AccountType)
                .filter_by(name="Credit Card", user_id=None).one()
            )
            card = account_service.create_account(account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=cc_type.id,
                name="Visa",
                anchor_balance=Decimal("-500.00"),
                anchor_period_id=seed_periods[0].id,
            ))
            db.session.add(card)
            db.session.commit()

            nw = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )["net_worth"]

            # Seed Checking only.
            assert nw.today.total_assets == Decimal("1000.00")
            # abs(-500.00) = 500.00 -- the magnitude, not the signed balance.
            assert nw.today.total_liabilities == Decimal("500.00")
            # 1000.00 - 500.00 = 500.00 (NOT 1500.00, the no-abs answer).
            assert nw.today.net_worth == Decimal("500.00")

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
            assert nw.today.liquid == Decimal("5000.00")


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
            )["net_worth"].series

            # history tail (indices 0-3) + forward (indices 4-9) = 10 points
            assert len(series.periods) == 10
            assert len(series.net) == 10
            # The parallel ``assets`` / ``liabilities`` totals were deleted at
            # plan step X-s1 (one fact under two keys); the bands they summed
            # carry the same length.
            assert len(series.composition["asset"]) == 10
            assert len(series.composition["liability"]) == 10
            # current period (index 4) sits at position 4: 4 history points
            # precede it (indices 0, 1, 2, 3).
            assert series.current_index == 4
            # The window's IDENTITY, asserted through the field the chart
            # actually reads.  It read ``p.period_index`` until plan step X-w6
            # deleted that field for having no production consumer (ruling
            # R-CL) -- and this test was its only meaningful reader, so the
            # property it pinned moves onto ``end_date`` rather than being lost:
            # the first four points ARE the four elapsed periods, and the fifth
            # IS the current one.
            assert [p.end_date for p in series.periods[:4]] == [
                seed_periods_today[i].end_date for i in range(4)
            ]
            assert series.periods[4].end_date == seed_periods_today[4].end_date

    def test_net_equals_assets_minus_liabilities_each_point(
        self, app, db, seed_user, seed_periods,
    ):
        """series net[i] == sum(asset bands)[i] - liability band[i], every point.

        Holds even with a mortgage whose amortization drives the
        liability band down period by period: the asset-plus /
        liability-minus split shares one sum with the net reduction.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.savings_dashboard_service._net_worth import (
            _ASSET_BANDS,
        )
        with app.app_context():
            _add_savings_account(
                seed_user, seed_periods[0].id, Decimal("4000.00"),
            )
            _add_mortgage_account(
                seed_user, seed_periods[0].id, Decimal("240000.00"),
            )

            series = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )["net_worth"].series

            assert len(series.net) > 0
            for i in range(len(series.net)):
                # ``net`` is the asset bands less the liability band.  It was
                # asserted against the parallel ``assets`` / ``liabilities``
                # totals until plan step X-s1 deleted those -- they were the
                # same sums under a second name, so this reads them from the
                # bands the chart actually draws.
                assert series.net[i] == (
                    sum(
                        (series.composition[b][i] for b in _ASSET_BANDS),
                        Decimal("0.00"),
                    )
                    - series.composition["liability"][i]
                )

    def test_series_liability_band_holds_a_negative_balance_magnitude(
        self, app, db, seed_user, seed_periods,
    ):
        """A negative-balance liability adds its MAGNITUDE to the series band.

        The per-period reduction (``_sum_composition_at_period``) has its own
        ``abs`` -- a SECOND site from the hero's -- and this is what pins it.
        A Credit Card's cash balance is stored negative, and it carries no
        amortization schedule, so it holds flat at its anchor across every
        point:
          liabilities[i]                = abs(-500.00) = 500.00
          composition["liability"][i]   = 500.00
          net[i]                        = 1000.00 - 500.00 = 500.00

        Without the ``abs`` the band would read -500.00 and net[i] would read
        1500.00 -- the card ADDING to net worth -- while the today hero on the
        SAME page still read 500.00.  Two producers contradicting each other
        on one screen is the failure this arc exists to end, so both ``abs``
        sites need their own control; the hero's is
        ``test_a_negative_balance_liability_still_adds_its_magnitude``.
        """
        with app.app_context():
            cc_type = (
                db.session.query(AccountType)
                .filter_by(name="Credit Card", user_id=None).one()
            )
            card = account_service.create_account(account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=cc_type.id,
                name="Visa",
                anchor_balance=Decimal("-500.00"),
                anchor_period_id=seed_periods[0].id,
            ))
            db.session.add(card)
            db.session.commit()

            series = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )["net_worth"].series

            assert len(series.composition["liability"]) > 0
            for i in range(len(series.composition["liability"])):
                # abs(-500.00) = 500.00 at every point (the card holds flat).
                assert series.composition["liability"][i] == Decimal(
                    "500.00",
                )
                # Seed Checking 1000.00 - 500.00 = 500.00 (NOT 1500.00).
                assert series.net[i] == Decimal("500.00")

    def test_current_period_point_equals_hero_for_liquid_only(
        self, app, db, seed_user, seed_periods,
    ):
        """For a CHECKING/SAVINGS-only fixture, the current-period series
        point equals the today hero.

        With no transactions every balance is flat, so the current
        period's net worth (``series.net[current_index]``) equals the
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

            current = nw.series.current_index
            # 1000.00 + 4000.00 = 5000.00, identical hero and current point.
            assert nw.today.net_worth == Decimal("5000.00")
            assert nw.series.net[current] == Decimal("5000.00")
            assert nw.series.net[current] == nw.today.net_worth
            # Flat liquid-only: every trend point (history tail + forward).
            assert all(v == Decimal("5000.00") for v in nw.series.net)

    def test_current_period_point_agrees_with_hero_for_amortizing_loan(
        self, app, db, seed_user, seed_periods_today,
    ):
        """For a loan, the current-period series point EQUALS the hero.

        This test used to assert the opposite -- that the two figures
        deliberately "read DIFFERENT sources" and must keep diverging.  They must
        not.  A page whose loan tile and whose net-worth trend disagree about the
        same loan on the same day is a page contradicting itself, and that
        contradiction was the symptom that opened this whole arc::

            the /savings loan tile and the net-worth trend's own 'today'
            point disagree: tile=240000.00 trend=236544.21

        The divergence had one cause: the hero read the loan's honest balance,
        while the trend's dense map walked the amortization SCHEDULE and let ~14
        unpaid, purely projected installments pay the principal down.  The mortgage
        has NO confirmed payments, so not one dollar of principal was ever paid,
        and $240,000 is the only true answer.  Both producers now read the confirmed
        ledger for a period that has begun, so both give it.

        Checking $1,000 and a $240,000 never-paid mortgage:
          hero net        = 1000.00 - 240000.00 = -239000.00
          series[current] = 1000.00 - 240000.00 = -239000.00  (agrees)
          series[future]  = 1000.00 - (amortized < 240000.00) > -239000.00
        """
        with app.app_context():
            _add_mortgage_account(
                seed_user, seed_periods_today[0].id, Decimal("240000.00"),
            )

            nw = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id
            )["net_worth"]

            current = nw.series.current_index
            # No confirmed payment: 1000.00 (checking) - 240000.00 (mortgage).
            assert nw.today.net_worth == Decimal("-239000.00")
            # The tile and the trend's own 'today' point agree, to the cent.
            # The message reads the SAME attributes the assertion does (plan
            # step X-w6).  It kept the pre-X-w3 subscripts, which raise
            # ``TypeError`` on the frozen region -- and an assert message is
            # lazy, so it would have raised at the one moment it exists for:
            # the moment the tile and the trend disagree.
            assert nw.series.net[current] == nw.today.net_worth, (
                f"the /savings hero ({nw.today.net_worth}) and the net-worth "
                f"trend's current-period point "
                f"({nw.series.net[current]}) disagree; the page is "
                f"contradicting itself about the same loan on the same day"
            )
            # Amortization is real in the FUTURE, where the projection answers:
            # the last trend point sits above the flat-debt line.
            assert nw.series.net[-1] > nw.today.net_worth


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
    def _debt_schedule(first_payment_period_index, periods):
        """A one-row loan schedule first paying in a period.

        The row's ``payment_date`` is that period's ``end_date``, so the
        loan gate resolves the loan's honest start to that period's index.
        The gate reads only the schedule ROWS -- which is why it is now handed
        rows (``debt_schedule_rows``) rather than the balance-bearing
        ``DebtSchedule`` bundle.
        """
        # pylint: disable=import-outside-toplevel
        from types import SimpleNamespace
        return [SimpleNamespace(
            payment_date=periods[first_payment_period_index].end_date,
            remaining_balance=Decimal("1000.00"),
        )]

    @staticmethod
    def _empty_debt_schedule():
        """An EMPTY loan schedule (a paid-off / fully-resolved loan).

        The gate must NOT constrain the window for such a loan -- its flat
        current balance is its real balance at every period.
        """
        return []

    def test_history_reaches_back_past_the_cash_anchor(self):
        """A cash account's anchor no longer bounds the history (N-44).

        Periods 0..9, today at index 5, one PLAIN account anchored at
        index 2, no loans.  Its balance is a fold over its own assertions, so
        it is real at every period and constrains nothing: the honest start is
        0 and only the ``_TREND_HISTORY_PERIODS`` cap (5 - 6 = -1, which does
        not bind here) limits the tail.  Window indices 0..9, ``current_index``
        the count below 5 -> 0, 1, 2, 3, 4 = 5.

        Before plan step X-c2b2 the projection omitted every pre-anchor period,
        so this same shape gated at index 2 and drew 3 history points; the two
        periods it refused are exactly the ones the fold now answers.
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

        assert [p.period_index for p in window] == [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
        assert current_index == 5
        assert honest_start == 0

    def test_a_cash_anchor_in_the_current_period_still_draws_history(self):
        """A cash account trued up TODAY still has a real past (N-44).

        PLAIN anchored at index 5, today at index 5 -- the shape both real
        production cash accounts are in, and the one the retired cash gate hurt
        most: it equalled the current index, so ``/savings`` drew ZERO history
        points on an account with four months of recorded activity.  The fold
        replays every assertion, so the window is the full
        ``_TREND_HISTORY_PERIODS`` tail.
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

        assert [p.period_index for p in window] == [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
        assert current_index == 5
        assert honest_start == 0

    def test_tail_capped_at_history_cap(self):
        """The history tail is capped by ``_TREND_HISTORY_PERIODS``.

        PLAIN anchored at index 0, today at index 9, periods 0..12.  The
        honest start (0) loses to the cap (9 - 6 = 3), so the tail is
        indices 3..8 (6 points) and ``current_index`` is 6.  With cash no
        longer gating, the cap is what bounds the tail for a loan-free user
        -- so this is also the test that would fail if the fold's history
        were allowed to run all the way back.
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

    def test_an_investment_anchor_does_not_gate_the_history_start(self):
        """Neither an INVESTMENT's anchor nor a cash one shortens the history.

        A PLAIN account is anchored at index 1 and an INVESTMENT at index 4,
        today at index 5.  Neither gates: an investment is defined pre-anchor
        (reverse-projected) and cash is a fold over its own assertions, so the
        honest start is 0 and the window runs 0..9.
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

        window, current_index, honest_start = build_trend_periods(
            accounts, periods, periods[5], {},
        )

        assert window[0].period_index == 0
        assert current_index == 5
        assert honest_start == 0

    def test_two_cash_anchors_neither_bounds_the_history(self):
        """Two cash accounts, two different anchors, neither gates (N-44).

        PLAIN at index 1 and PLAIN at index 3, today at index 5.  The retired
        rule took the LATER anchor (3) so that no period could miss a cash
        balance; the fold gives BOTH accounts a real balance at every period,
        so there is nothing to miss and the window runs 0..9.
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

        window, current_index, honest_start = build_trend_periods(
            accounts, periods, periods[5], {},
        )

        assert window[0].period_index == 0
        assert current_index == 5
        assert honest_start == 0

    def test_a_modelled_only_set_still_draws_no_history(self):
        """An investment / property-only set draws NO backward run.

        Nothing in the set has a RECORDED past: an investment's pre-anchor
        values are a reverse growth projection and a property's are a flat
        anchor carry, so a backward run would be modelled figures presented as
        actual history.  The no-history default therefore survives the cash
        arm's removal -- and this is the test that fails if a cash account is
        dropped from the gate loop entirely instead of participating in it
        unconstrained, because a LOAN-FREE cash user would then land here too.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.account_projection import AccountProjectionKind
        from app.services.savings_dashboard_service._net_worth import (
            build_trend_periods,
        )
        periods = [self._period(i) for i in range(10)]
        accounts = [
            self._account(AccountProjectionKind.INVESTMENT, 1),
            self._account(AccountProjectionKind.APPRECIATING, 2),
        ]

        window, current_index, honest_start = build_trend_periods(
            accounts, periods, periods[5], {},
        )

        assert [p.period_index for p in window] == [5, 6, 7, 8, 9]
        assert current_index == 0
        assert honest_start == 5

    def test_loan_schedule_start_gates_history(self):
        """A loan's today-forward schedule gates the history past the cash.

        A PLAIN account is anchored at index 1, but an AMORTIZING loan's
        schedule first pays in period 5 (today at index 7).  Pre-schedule
        periods report the loan's current balance held flat (today's balance,
        not its real past), so the loan gates the honest start at index 5.
        Window indices 5..9, ``current_index`` 2 (indices 5, 6).  Without the
        loan gate the honest start would be 0 (cash constrains nothing since
        plan step X-c2b2), the cap would bind at 7 - 6 = 1 and
        ``current_index`` would be 6 -- so this pins the loan gate, which is
        the ONLY arm left.
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
        debt_schedules = {8: self._debt_schedule(5, periods)}

        window, current_index, honest_start = build_trend_periods(
            accounts, periods, periods[7], debt_schedules,
        )

        assert honest_start == 5
        assert current_index == 2
        assert window[0].period_index == 5

    def test_empty_loan_schedule_does_not_gate(self):
        """A resolved-but-unpaid loan (empty schedule) does not gate history.

        An empty schedule means the loan sits at its current balance at every
        period (a paid-off / fully-resolved loan), which IS its real balance,
        so it is honest throughout and must not gate.  PLAIN anchored at
        index 1, an AMORTIZING loan with an empty schedule, today at index 7:
        nothing gates, so the honest start is 0 and the
        ``_TREND_HISTORY_PERIODS`` cap bounds the tail at 7 - 6 = 1.  Window
        indices 1..9, ``current_index`` 6.
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
        debt_schedules = {8: self._empty_debt_schedule()}

        window, current_index, honest_start = build_trend_periods(
            accounts, periods, periods[7], debt_schedules,
        )

        assert honest_start == 0
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
        assert today.net_worth == Decimal("0.00")
        assert today.total_assets == Decimal("0.00")
        assert today.total_liabilities == Decimal("0.00")
        assert today.liquid == Decimal("0.00")

    def test_no_projections_series_is_empty_window(self):
        """With no projections and no forward periods the series is empty.

        The field set is pinned for the reason the key set was: ``assets`` and
        ``liabilities`` were deleted at plan step X-s1 for being the band sums
        under a second key, and a producer that re-publishes them fails here.
        ``current_index`` is a FIELD rather than a key the caller mutates on
        afterwards since plan step X-w3 (ruling R-CI), so it is in the literal.
        """
        # pylint: disable=import-outside-toplevel
        from dataclasses import fields
        from app.services.savings_dashboard_service._net_worth import (
            _COMPOSITION_BANDS,
            compute_net_worth_series,
        )
        series = compute_net_worth_series([], [], {}, 0)
        assert series.periods == []
        assert series.net == []
        assert series.current_index == 0
        assert {f.name for f in fields(series)} == {
            "periods", "net", "composition", "current_index",
        }
        # Every composition band is present but empty (no periods to sum).
        assert series.composition == {band: [] for band in _COMPOSITION_BANDS}

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
            _projection(account, Decimal("500.00")),
        ])
        assert today.total_assets == Decimal("0.00")
        assert today.total_liabilities == Decimal("500.00")
        # 0.00 - 500.00 = -500.00
        assert today.net_worth == Decimal("-500.00")
        assert today.liquid == Decimal("0.00")

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
            _projection(account, Decimal("750.00")),
        ])
        assert today.net_worth == Decimal("750.00")
        assert today.total_assets == Decimal("750.00")
        assert today.total_liabilities == Decimal("0.00")
        assert today.liquid == Decimal("750.00")

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
            _projection(funded, Decimal("600.00")),
            _projection(empty, Decimal("0.00")),
        ])
        # 600.00 + 0.00 = 600.00 (the zero account is summed, not absent).
        assert today.net_worth == Decimal("600.00")
        assert today.total_assets == Decimal("600.00")
        assert today.liquid == Decimal("600.00")


class TestCategoryClassifier:
    """Tests for the shared id-based category classifier (P-AC1 Loop B P1).

    ``account_category_key`` is the ONE per-account classifier both the grid
    grouping and the net-worth composition split read, so a band and the grid
    group cannot disagree.  It classifies by the account type's integer
    ``category_id`` (IDs for logic, never a ``.name`` string).
    """

    def test_key_by_category_id(self, app):
        """Each real category id maps to its display key; else 'other'."""
        with app.app_context():
            # pylint: disable=import-outside-toplevel
            from types import SimpleNamespace
            from app.enums import AcctCategoryEnum
            from app.services.savings_dashboard_service._display import (
                account_category_key,
            )
            cases = [
                (AcctCategoryEnum.ASSET, "asset"),
                (AcctCategoryEnum.LIABILITY, "liability"),
                (AcctCategoryEnum.RETIREMENT, "retirement"),
                (AcctCategoryEnum.INVESTMENT, "investment"),
            ]
            for enum, expected in cases:
                acct = SimpleNamespace(account_type=SimpleNamespace(
                    category_id=ref_cache.acct_category_id(enum),
                ))
                assert account_category_key(acct) == expected
            # A degenerate account (no type, or a type with no category id)
            # is the only path to "other".
            assert account_category_key(
                SimpleNamespace(account_type=None),
            ) == "other"
            assert account_category_key(SimpleNamespace(
                account_type=SimpleNamespace(category_id=None),
            )) == "other"


class TestNetWorthComposition:
    """Tests for the 2-year net-worth series' per-category composition split.

    ``compute_net_worth_series`` now also emits ``composition`` -- one Decimal
    band series per category (asset / retirement / investment / other /
    liability).  The split shares ONE per-period sum with the ``assets`` /
    ``liabilities`` / ``net`` totals, so it reconciles to them by
    construction, and each band reads the same balance the grid renders.
    """

    def test_bands_reconcile_to_totals_each_point(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Composition bands sum to assets / liabilities / net at every point.

        With Checking ($1,000) + Savings ($4,000) as assets, a 401(k) as the
        retirement band, and a $240,000 mortgage as the liability band, the
        asset-side bands must sum to ``assets`` and the liability band must
        equal ``liabilities`` at every trend point -- the split cannot drift
        from the totals it is derived from.
        """
        with app.app_context():
            # pylint: disable=import-outside-toplevel
            from tests._test_helpers import make_investment_account
            periods = seed_periods_today
            _add_savings_account(seed_user, periods[0].id, Decimal("4000.00"))
            make_investment_account(
                seed_user, db.session, periods[0], Decimal("10000.00"),
            )
            _add_mortgage_account(seed_user, periods[0].id, Decimal("240000.00"))

            series = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )["net_worth"].series
            comp = series.composition
            asset_bands = ("asset", "retirement", "investment", "other")

            assert len(series.net) > 0
            for i in range(len(series.net)):
                asset_side = sum(
                    (comp[band][i] for band in asset_bands), Decimal("0"),
                )
                # asset-side bands sum to the asset total; the liability band
                # is the liability total; net is their difference.
                # The bands ARE the totals: the parallel ``assets`` /
                # ``liabilities`` lanes this used to reconcile against were
                # deleted at plan step X-s1 for being that same sum under a
                # second key, so what remains is the identity itself.
                assert series.net[i] == (
                    asset_side - comp["liability"][i]
                )

    def test_bands_place_each_account_in_its_category(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The 401(k) lands in the retirement band, not the asset band.

        A reconciliation test alone cannot catch a mis-banding (a 401(k) in
        the asset band still sums into ``assets``); this pins that the
        retirement band at the current period equals the 401(k)'s own current
        balance, and the empty investment / other bands stay zero.
        """
        with app.app_context():
            # pylint: disable=import-outside-toplevel
            from tests._test_helpers import make_investment_account
            periods = seed_periods_today
            k401 = make_investment_account(
                seed_user, db.session, periods[0], Decimal("10000.00"),
            )

            data = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            series = data["net_worth"].series
            comp = series.composition
            current = series.current_index

            k401_balance = next(
                ad.current_balance for ad in data["account_data"]
                if ad.account.id == k401.id
            )
            # The 401(k) is the only retirement account, so the retirement
            # band at the current period IS its balance -- proving it is not
            # summed into the asset band.
            assert comp["retirement"][current] == k401_balance
            assert comp["retirement"][current] > Decimal("0.00")
            assert comp["investment"][current] == Decimal("0.00")
            assert comp["other"][current] == Decimal("0.00")


class TestNetWorthHorizon:
    """Tests for the long-horizon annual net-worth producer (P-AC1 Loop B P1).

    ``build_horizon`` builds an annual net-worth composition +
    net-trajectory series to the horizon domain (last loan payoff + 1 year, or
    a fixed decade for a loan-free user), reusing the /retirement engine for
    the retirement / investment bands, per-account growth params for the asset
    band, and the loan resolver schedules for the liability band.

    **Read through ``compute_dashboard_data``, the only path production has**
    (plan step X-q2, finding N-100).  These tests called
    ``compute_net_worth_horizon``, a narrow producer with ZERO ``app/`` callers
    -- an AST census found its 10 call sites were all in this file -- so the
    suite was grading a second producer no screen reached while the page read
    the horizon out of the full build.  The narrow producer is deleted; the
    horizon is read where the route reads it.  The one exception is the
    no-pay-periods test below, which calls ``build_horizon`` directly because
    the state it needs -- a user with an empty period list -- is upstream of
    the build rather than inside it.
    """

    def test_none_without_periods(self, app, db, seed_user):
        """No pay periods -> the horizon producer returns None (no axis)."""
        with app.app_context():
            # pylint: disable=import-outside-toplevel
            from app.services.savings_dashboard_service._horizon import (
                build_horizon,
            )
            from app.services.savings_dashboard_service._types import (
                _DashboardCoreData,
            )
            core = _DashboardCoreData(
                accounts=[],
                balance_ctx=BalanceContext.build(seed_user["user"].id),
                all_periods=[], current_period=None,
            )
            assert build_horizon(seed_user["user"].id, core, [], {}) is None

    def test_publishes_only_the_keys_the_page_reads(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Every published key is one the route's serializer consumes (N-100).

        The producer's side of the contract plan step X-q2 established; the
        route's side -- each key removed in turn must break
        ``_serialize_horizon`` -- is
        ``test_savings.TestHorizonSerialization.test_every_published_key_is_read``.
        Pinned as a literal here so a key added without a consumer fails a test
        rather than living on as a producer output no screen reaches, which is
        what ``horizon_end`` and ``is_loan_free`` did until X-q2.
        """
        with app.app_context():
            horizon = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )["net_worth"].horizon

            assert set(horizon) == {
                "dates", "current_index", "composition", "net", "milestones",
            }

    def test_loan_free_uses_fixed_decade_window(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A user with no loans gets the fixed 10-year forward window.

        The domain end is December 31 of ``today.year + 10``; index 0 is today
        (the "Today" marker), and it is the LAST sample -- the domain end is
        ``dates[-1]`` by construction, which is why plan step X-q2 deleted the
        second ``horizon_end`` key that restated it.
        """
        with app.app_context():
            horizon = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )["net_worth"].horizon
            assert horizon is not None
            assert horizon["dates"][0] == date.today()
            assert horizon["dates"][-1] == date(date.today().year + 10, 12, 31)
            assert horizon["current_index"] == 0

    def test_today_point_equals_net_worth_hero(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The horizon's index-0 figures equal the page's net-worth hero.

        Checking ($1,000) + Savings ($4,000) + a 401(k) as assets and a
        $240,000 mortgage: the horizon starts at the same net worth, total
        assets, and total liabilities the cockpit hero shows.  The 401(k)
        pins that the /retirement-engine today balance the horizon reads for
        the retirement band agrees with the account_data balance the hero
        sums (both the model-from-anchor balance_at figure).
        """
        with app.app_context():
            # pylint: disable=import-outside-toplevel
            from tests._test_helpers import make_investment_account
            periods = seed_periods_today
            _add_savings_account(seed_user, periods[0].id, Decimal("4000.00"))
            make_investment_account(
                seed_user, db.session, periods[0], Decimal("10000.00"),
            )
            _add_mortgage_account(seed_user, periods[0].id, Decimal("240000.00"))
            uid = seed_user["user"].id

            hero = savings_dashboard_service.compute_dashboard_data(
                uid,
            )["net_worth"]
            horizon = hero.horizon

            asset_bands = ("asset", "retirement", "investment", "other")
            asset0 = sum(
                (horizon["composition"][band][0] for band in asset_bands),
                Decimal("0"),
            )
            assert horizon["net"][0] == hero.today.net_worth
            assert asset0 == hero.today.total_assets
            assert horizon["composition"]["liability"][0] == (
                hero.today.total_liabilities
            )

    def test_group_subtotal_equals_horizon_band_at_today(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The legend matches its chart band per category on the default view.

        The stream's legend renders ``group_subtotals[band]`` (summed from
        each account's ``current_balance``) directly beneath the chart.  On
        the default ``Horizon`` range, each band's index-0 value is the
        horizon's today point -- built from the SAME ``current_balance`` -- so
        the legend and the chart agree per band even for a loan holder
        (Checking $1,000 + Savings $4,000 asset $5,000, and a $240,000
        mortgage liability), and the legend can never disagree with the chart
        it labels.  (The ``2 years`` range's liability band is the loan's
        contractual schedule per decision 11, which equals ``current_balance``
        on reconciled data but can drift when a loan's anchor is a stale
        true-up -- a pre-existing cockpit-wide seam, not this element's.)
        """
        with app.app_context():
            periods = seed_periods_today
            _add_savings_account(seed_user, periods[0].id, Decimal("4000.00"))
            _add_mortgage_account(
                seed_user, periods[0].id, Decimal("240000.00"),
            )
            uid = seed_user["user"].id

            ctx = savings_dashboard_service.compute_dashboard_data(uid)
            subtotals = ctx["group_subtotals"]
            horizon = ctx["net_worth"].horizon

            # Checking $1,000 + Savings $4,000 = $5,000 asset; $240,000
            # mortgage liability.  Both groups are present.
            assert subtotals["asset"] == Decimal("5000.00")
            assert subtotals["liability"] == Decimal("240000.00")
            for band, subtotal in subtotals.items():
                assert horizon["composition"][band][0] == subtotal, band

    def test_non_amortizing_liability_stays_in_the_band(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A revolving Credit Card debt appears in the liability band, flat.

        A liability with no amortization schedule (Credit Card, no
        ``loan_params``) has no forward model, so it holds flat at its owed
        magnitude -- but it must NOT vanish from the horizon: the today point
        still reconciles to the net-worth hero, and the $3,000 debt does not
        disappear when the range toggles from ``2 years`` to ``Horizon``.
        """
        with app.app_context():
            # pylint: disable=import-outside-toplevel
            from app.models.ref import AccountType
            from app.services import account_service
            periods = seed_periods_today
            _add_savings_account(seed_user, periods[0].id, Decimal("4000.00"))
            cc_type = (
                db.session.query(AccountType)
                .filter_by(name="Credit Card").one()
            )
            card = account_service.create_account(account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=cc_type.id,
                name="Visa",
                anchor_balance=Decimal("3000.00"),
                anchor_period_id=periods[0].id,
            ))
            db.session.add(card)
            db.session.commit()
            uid = seed_user["user"].id

            hero = savings_dashboard_service.compute_dashboard_data(
                uid,
            )["net_worth"]
            horizon = hero.horizon
            liability = horizon["composition"]["liability"]

            # Checking $1,000 + Savings $4,000 assets, $3,000 card liability:
            #   total_liabilities = 3000.00 ; net = 5000 - 3000 = 2000.
            assert hero.today.total_liabilities == Decimal("3000.00")
            assert horizon["net"][0] == hero.today.net_worth
            # The card is in the band at index 0 and holds flat (no schedule).
            assert liability[0] == Decimal("3000.00")
            assert liability[-1] == Decimal("3000.00")
            # A card carries no payoff model, so the domain is the fixed
            # loan-free decade -- the card neither sets nor poisons it.
            assert horizon["dates"][-1] == date(date.today().year + 10, 12, 31)

    def test_composition_reconciles_to_net_each_point(
        self, app, db, seed_user, seed_periods_today,
    ):
        """net[k] == sum(asset bands) - liability band at every horizon point."""
        with app.app_context():
            # pylint: disable=import-outside-toplevel
            from tests._test_helpers import make_investment_account
            periods = seed_periods_today
            _add_savings_account(seed_user, periods[0].id, Decimal("4000.00"))
            make_investment_account(
                seed_user, db.session, periods[0], Decimal("10000.00"),
            )
            _add_mortgage_account(seed_user, periods[0].id, Decimal("240000.00"))

            horizon = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )["net_worth"].horizon
            comp = horizon["composition"]
            asset_bands = ("asset", "retirement", "investment", "other")
            for k in range(len(horizon["net"])):
                asset_side = sum(
                    (comp[band][k] for band in asset_bands), Decimal("0"),
                )
                assert horizon["net"][k] == asset_side - comp["liability"][k]

    def test_retirement_band_equals_retirement_engine_at_horizon(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The final retirement band equals the /retirement projection (oracle).

        The P-AC1 ruling's "the cockpit band equals /retirement by
        construction": re-running the SAME engine
        (``build_projection_context`` + ``project_retirement_accounts``) at
        the horizon end and summing the projected balances must equal the
        horizon retirement band's final sample.
        """
        with app.app_context():
            # pylint: disable=import-outside-toplevel
            from tests._test_helpers import make_investment_account
            from app.services import pay_period_service, retirement_projection
            periods = seed_periods_today
            make_investment_account(
                seed_user, db.session, periods[0], Decimal("50000.00"),
            )
            uid = seed_user["user"].id

            horizon = savings_dashboard_service.compute_dashboard_data(
                uid,
            )["net_worth"].horizon

            all_periods = pay_period_service.get_all_periods(uid)
            current = pay_period_service.get_current_period(uid)
            # The domain end is the last annual sample (plan step X-q2 deleted
            # the second key that restated it).
            ctx = retirement_projection.build_projection_context(
                uid, all_periods, current, horizon["dates"][-1], None, None,
            )
            projections = retirement_projection.project_retirement_accounts(ctx)
            expected = sum(
                (p["projected_balance"] for p in projections), Decimal("0"),
            )
            # 50k at 7% over a decade grows well past 50k.
            assert expected > Decimal("50000.00")
            assert horizon["composition"]["retirement"][-1] == expected

    def test_liability_amortizes_toward_zero_and_sets_domain(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A mortgage sets the domain to payoff + 1 year and amortizes to zero.

        The liability band starts at the loan's current balance ($240,000) and
        the final sample -- past the payoff -- is zero owed; the domain end
        year is the payoff year plus one.

        Originated TODAY (no overdue gap), so the forward fold synthesizes its
        WHOLE contractual schedule from the full balance and amortizes cleanly to
        zero -- the on-schedule case.  A past origination with no payment records
        would be delinquent under the C6b plan fold and never reach zero (B-9).
        """
        with app.app_context():
            periods = seed_periods_today
            _add_mortgage_account(
                seed_user, periods[0].id, Decimal("240000.00"),
                origination_date=date.today(),
            )
            uid = seed_user["user"].id

            data = savings_dashboard_service.compute_dashboard_data(uid)
            payoff = data["debt_summary"].payoff_outlook.all_clear_on
            horizon = data["net_worth"].horizon
            liability = horizon["composition"]["liability"]

            # A payoff-sized domain is the loan-bearing state: the fixed
            # loan-free decade would not land on the payoff year plus one.
            assert horizon["dates"][-1].year == payoff.year + 1
            assert liability[0] == Decimal("240000.00")
            assert liability[-1] == Decimal("0.00")
            assert liability[-1] < liability[0]

    def test_debt_free_milestone_at_payoff(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A loan yields exactly one payoff-complete flag at that date.

        The label reads "All loans paid off" as of plan step X-q3 (developer
        ruling on finding N-99): the date behind it covers amortizing loans,
        the only debts with a payoff model, and a revolving balance on the
        same chart's liability band never reaches zero.

        **The label is now the flag's IDENTITY** (plan step X-s1, ruling
        R-BC): the machine ``kind`` beside it reached no consumer -- the
        serializer copied it into the payload and the client's flag plugin
        never read it -- so it is gone from both ends, and this asserts the
        milestone's whole key set here rather than only at the top level.  The
        expected label is imported from the producer, not re-typed: X-q3
        renamed this very string, and a hand-typed copy would have gone stale
        without failing.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.savings_dashboard_service._horizon import (
            _DEBT_FREE_MILESTONE_LABEL,
        )
        with app.app_context():
            periods = seed_periods_today
            _add_mortgage_account(seed_user, periods[0].id, Decimal("240000.00"))
            uid = seed_user["user"].id

            data = savings_dashboard_service.compute_dashboard_data(uid)
            payoff = data["debt_summary"].payoff_outlook.all_clear_on
            horizon = data["net_worth"].horizon

            # Identified by the (label, date) PAIR, never the label alone
            # (plan step X-t4, finding N-110): a per-loan flag reads
            # "<account name> paid off", which equals this label exactly for an
            # account a user names "All loans".  The pair is unique by
            # construction -- a per-loan flag fires only STRICTLY BEFORE the
            # debt-free date -- and the collision itself is covered by
            # ``TestAMilestoneLabelCanCollide`` below.
            debt_free = [
                m for m in horizon["milestones"]
                if m["label"] == _DEBT_FREE_MILESTONE_LABEL
                and m["date"] == payoff
            ]
            assert len(debt_free) == 1
            # The wording itself, pinned ONCE (this is the only place it is
            # spelled out); every other consumer reads the constant.
            assert _DEBT_FREE_MILESTONE_LABEL == "All loans paid off"
            # The milestone's complete key set -- the producer's half of the
            # nested contract whose route half is
            # ``test_savings.TestHorizonSerialization
            # .test_every_published_key_is_read``.
            for milestone in horizon["milestones"]:
                assert set(milestone) == {"date", "label"}

    def test_net_crossing_milestone(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A net trajectory crossing $500k yields a 'Net $500k' flag.

        A $300,000 401(k) at 7% over the decade window grows the net worth
        (~$301k today) past $500k, so exactly one $500k crossing flag fires at
        the first sample that reaches it.
        """
        with app.app_context():
            # pylint: disable=import-outside-toplevel
            from tests._test_helpers import make_investment_account
            periods = seed_periods_today
            make_investment_account(
                seed_user, db.session, periods[0], Decimal("300000.00"),
            )

            horizon = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )["net_worth"].horizon
            assert horizon["net"][0] < Decimal("500000")
            assert horizon["net"][-1] >= Decimal("500000")
            crossings = [
                m for m in horizon["milestones"] if m["label"] == "Net $500k"
            ]
            assert len(crossings) == 1

    def test_net_milestone_label_formatting(self):
        """The $500k-step crossing labels format without scientific notation.

        A whole-million multiple reads ``$NM`` (int-formatted, so a round
        ten-million is ``$10M`` and never ``Decimal.normalize``'s ``1E+1``);
        a half-million residue keeps its ``.5``.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.savings_dashboard_service._horizon import (
            _format_net_milestone,
        )
        assert _format_net_milestone(Decimal("500000")) == "Net $500k"
        assert _format_net_milestone(Decimal("1000000")) == "Net $1M"
        assert _format_net_milestone(Decimal("1500000")) == "Net $1.5M"
        assert _format_net_milestone(Decimal("10000000")) == "Net $10M"
        assert _format_net_milestone(Decimal("2500000")) == "Net $2.5M"


class TestAMilestoneLabelCanCollide:
    """Two flags may share a label, and the chart draws both (plan step X-t4).

    Finding N-110.  Plan step X-s1 deleted the machine ``kind`` from the
    milestone dicts because nothing in ``app/`` read it -- the serializer copied
    it into the payload and the client's flag plugin never looked at it -- which
    left the LABEL as a flag's only handle.  The debt-free flag reads "All loans
    paid off" and a per-loan flag reads ``f"{account.name} paid off"``, so a
    user who names an account "All loans" makes the two strings equal.

    **Ruled (developer, 2026-07-28): the label IS the identity, and a duplicate
    is a display outcome rather than a defect.**  Two flags at two dates are two
    true statements; the producer must never DROP one to keep labels unique,
    because a silently missing flag is the worse failure.  What the finding
    actually broke was a TEST that counted flags by matching the string, and the
    fix is to identify a flag by the ``(label, date)`` pair -- unique by
    construction, since a per-loan flag fires only strictly before the debt-free
    date.

    This pins the ruling so it is a predicate rather than prose: a future step
    that de-duplicates by label, or drops the colliding per-loan flag, fails
    here.
    """

    def test_an_account_named_all_loans_yields_two_identical_labels(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The colliding account's flag and the debt-free flag BOTH survive.

        A 24-month loan literally named "All loans" pays off years before a
        30-year mortgage, so the producer emits its per-loan flag ("All loans
        paid off") AND the debt-free flag at the mortgage's payoff -- the same
        string, twice, at two dates.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.savings_dashboard_service._horizon import (
            _DEBT_FREE_MILESTONE_LABEL,
        )
        with app.app_context():
            colliding = _create_small_loan(
                seed_user, db.session, name="All loans",
            )
            _add_mortgage_account(
                seed_user, seed_periods_today[0].id, Decimal("240000.00"),
                origination_date=date.today(),
            )
            db.session.commit()

            data = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            milestones = data["net_worth"].horizon["milestones"]
            payoff = data["debt_summary"].payoff_outlook.all_clear_on

            # Precondition: the fixture really does collide -- the per-loan
            # label is built from the account name and equals the constant.
            assert f"{colliding.name} paid off" == _DEBT_FREE_MILESTONE_LABEL

            same_label = [
                m for m in milestones
                if m["label"] == _DEBT_FREE_MILESTONE_LABEL
            ]
            assert len(same_label) == 2, (
                "the producer dropped or merged a flag to keep labels unique; "
                "a user's own payoff flag is not a duplicate to be pruned"
            )
            # Two DISTINCT dates, and the later one is the debt-free flag.
            dates = sorted(m["date"] for m in same_label)
            assert dates[0] < dates[1]
            assert dates[1] == payoff
            # The (label, date) pair is what identifies a flag, and it stays
            # unique across every milestone the chart draws.
            pairs = [(m["label"], m["date"]) for m in milestones]
            assert len(set(pairs)) == len(pairs)


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

    def test_every_account_in_a_group_is_counted(self):
        """A group's subtotal is the sum of ALL its accounts, none dropped.

        Direct unit test of the producer: three asset accounts in one group,
        including a zero-balance one, which must be ADDED rather than skipped
        -- a populated group must never look empty, and a real ``$0.00``
        account is a member like any other.

        **This test used to pin the opposite question** -- that a ``None``
        balance "contributes 0.00 rather than being dropped" -- and plan step
        X-v2 deleted the state it described (ruling R-CA).
        ``AccountProjection.current_balance`` is non-nullable now: the ``None``
        it tested for had exactly one cause (a user with no baseline scenario),
        that user's page never renders, and treating "the app cannot answer
        this balance" as ``$0.00`` was the eighth copy of finding N-113's
        fabrication.  The developer confirmed the behaviour change per
        CLAUDE.md rule 5.
        """
        # pylint: disable=import-outside-toplevel
        from collections import OrderedDict
        from types import SimpleNamespace
        from app.services.savings_dashboard_service._display import (
            _compute_group_subtotals,
        )
        grouped = OrderedDict([(
            "asset",
            [
                _projection(SimpleNamespace(account_type=None),
                            Decimal("600.00")),
                _projection(SimpleNamespace(account_type=None),
                            Decimal("0.00")),
                _projection(SimpleNamespace(account_type=None),
                            Decimal("40.25")),
            ],
        )])
        subtotals = _compute_group_subtotals(grouped)
        # 600.00 + 0.00 + 40.25
        assert subtotals["asset"] == Decimal("640.25")


class TestComputeSparklines:
    """Tests for the conditional per-account sparkline producer."""

    @staticmethod
    def _period(period_id):
        """Synthetic PayPeriod stand-in (only ``id`` is read)."""
        from types import SimpleNamespace  # pylint: disable=import-outside-toplevel
        return SimpleNamespace(id=period_id)

    @staticmethod
    def _map(account_id, balances):
        """One account's projection as ``_project_one_account`` emits it.

        It was the ``{account_id, balances, is_liability}`` dict a second
        producer built beside the projections until plan step X-w (ruling
        R-CG, finding N-114).  ``compute_sparklines`` reads the projection
        itself now, so this fixture builds the REAL frozen type -- the
        stand-in account carries only the ``id`` the producer keys on.
        """
        from types import SimpleNamespace  # pylint: disable=import-outside-toplevel
        return _projection(
            SimpleNamespace(id=account_id, account_type=None),
            Decimal("0.00"),
            balances,
        )

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
            assert entry.account.id == prop.id
            # 400000.00 - 240000.00 = 160000.00; 240000/400000 = 0.6000
            assert entry.equity.market_value == Decimal("400000.00")
            assert entry.equity.total_debt == Decimal("240000.00")
            assert entry.equity.equity == Decimal("160000.00")
            assert entry.equity.ltv == Decimal("0.6000")

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
            assert entry.account.id == prop.id
            assert entry.equity.total_debt == Decimal("0")
            assert entry.equity.equity == Decimal("300000.00")
            assert entry.equity.ltv == Decimal("0.0000")


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
                ad.current_balance for ad in full["account_data"]
                if ad.account.id == acct_id
            )

            cell = savings_dashboard_service.compute_account_balance_cell(
                user_id, acct_id,
            )
            assert cell is not None
            assert cell.account.id == acct_id
            assert cell.current_balance == grid_balance

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

    def test_cell_flags_liability_for_loan(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A loan (LIABILITY category) cell carries is_liability=True.

        The cell contract feeds ``_cockpit_balance.html`` the flag that
        paints the owed balance in the danger token -- the same treatment
        the liabilities chip, group subtotal, and diverging-bar segment
        already use -- keyed on the account's category id, never the
        figure's sign (polish audit P-AC4).  A reverted liability cell
        therefore keeps its danger ink.
        """
        with app.app_context():
            loan = _create_small_loan(seed_user, db.session)

            cell = savings_dashboard_service.compute_account_balance_cell(
                seed_user["user"].id, loan.id,
            )
            assert cell is not None
            assert cell.is_liability is True

    def test_cell_flags_asset_as_non_liability(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A Checking account (non-LIABILITY category) cell is_liability=False.

        The seed_user account is a Checking type (ASSET category), so its
        balance keeps the plain number ink -- the danger token is reserved
        for the liability side (polish audit P-AC4).
        """
        with app.app_context():
            cell = savings_dashboard_service.compute_account_balance_cell(
                seed_user["user"].id, seed_user["account"].id,
            )
            assert cell is not None
            assert cell.is_liability is False


class TestOneResolutionPerLoanPerReadPass:
    """Every producer resolves each loan EXACTLY ONCE per read pass.

    The DRY property made into a deterministic gate rather than a hope.  Before
    the read-pass :class:`~app.services.balance_at.BalanceContext`, ONE
    ``compute_dashboard_data`` ran the loan resolver ELEVEN times for two loans:
    the balance maps, the trend window's honest-history gate, the liability band,
    the loan tile, the property-equity card, and an "ever paid off" probe each
    resolved independently.

    That was filed as waste, and the waste was the least of it: because there was
    no single resolution to compare against, nothing revealed that one of the
    eleven answered from a producer that could not read the genesis ledger
    (``followup_redundant_loan_resolution.md``).  Redundant derivation is where a
    divergence hides, so "resolved once" is pinned HERE, at the count -- a new
    consumer that re-resolves a loan behind the seam's back fails this test
    rather than silently agreeing until the day it does not.

    The spy sits on ``resolve_loan_bundle``: the single db-facing load the memo
    wraps, so it counts every resolution anywhere in the pass regardless of which
    module asked.
    """

    def _count_resolutions(self, monkeypatch):
        """Spy on the db-facing loan resolver; return the per-account call list."""
        # Pylint: import-outside-toplevel -- the file-wide deferred-import
        # convention for test-local symbols.
        from app.services.balance_at import (  # pylint: disable=import-outside-toplevel
            _resolution as resolution_module,
        )

        calls = []
        real = resolution_module.resolve_loan_bundle

        def _spy(account, ctx):
            calls.append(account.id)
            return real(account, ctx)

        monkeypatch.setattr(resolution_module, "resolve_loan_bundle", _spy)
        return calls

    def test_dashboard_data_resolves_each_loan_once(
        self, app, db, seed_user, seed_periods_today, monkeypatch,
    ):
        """/savings resolves each of two loans exactly once (was 11 runs for 2)."""
        with app.app_context():
            first = _create_small_loan(seed_user, db.session, name="Loan A")
            second = _create_small_loan(seed_user, db.session, name="Loan B")
            db.session.commit()
            ids = {first.id, second.id}

            calls = self._count_resolutions(monkeypatch)
            savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )

            assert set(calls) == ids
            for loan_id in ids:
                assert calls.count(loan_id) == 1, (
                    f"loan {loan_id} was resolved {calls.count(loan_id)} times "
                    "in one /savings render; it must be resolved exactly once"
                )

    def test_tracks_section_resolves_each_loan_once(
        self, app, db, seed_user, seed_periods_today, monkeypatch,
    ):
        """The dashboard's tracks section shares ONE pass across its producers.

        ``compute_tracks_section`` runs the goal and debt-summary producers back
        to back -- three producers until plan step X-u deleted the
        principal-progress one (finding N-109).  Each used to start its own read
        pass, so a two-loan user paid for six resolutions; they now share one
        context.

        The CONTEXT is shared; the LOADS behind it are not, which is finding
        N-115 and is what this test does NOT assert -- it counts loan
        resolutions, and a duplicate ``_load_dashboard_core_data`` resolves no
        loan.  ``TestTracksDebt::test_one_render_projects_the_debt_accounts_once``
        is the one that counts pipelines.
        """
        with app.app_context():
            loan = _create_small_loan(seed_user, db.session, name="Tracks Loan")
            db.session.commit()
            loan_id = loan.id

            calls = self._count_resolutions(monkeypatch)
            # Pylint: import-outside-toplevel -- deferred, matching the producer's
            # own lazy import of the savings package.
            from app.services import (  # pylint: disable=import-outside-toplevel
                dashboard_pulse_service,
            )
            dashboard_pulse_service.compute_tracks_section(
                seed_user["user"].id,
            )

            assert calls.count(loan_id) == 1


class TestTypeDriftedLoanParamsRow:
    """An orphan ``LoanParams`` on a non-amortizing type is not treated as a loan.

    The reachable drift state: the account edit form allows changing
    ``account_type_id`` (``accounts/crud.py``), nothing deletes the params row on
    a type change, so a Mortgage re-typed to Credit Card KEEPS its ``LoanParams``.
    ``followup_horizon_loan_predicate_split.md`` proposed that the Horizon's
    domain / milestones (which read ``ad.loan.params``) would then disagree
    with its liability band (which asks the canonical classifier), drawing a
    payoff flag on a debt line that never retires.

    They do NOT disagree, and this test is the evidence the follow-up said did not
    exist.  ``_data._load_loan_params_and_escrow`` builds ``loan_params_map`` from
    the accounts whose TYPE carries ``has_amortization`` -- the SAME flag
    ``classify_account`` branches on -- so a drifted account never enters the map,
    never gets ``loan_params`` in its projection dict, and is therefore skipped by
    the domain and the milestones exactly as the band skips it.  One flag, one
    answer, three consumers.
    """

    def test_drifted_account_is_a_plain_liability_everywhere(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A re-typed loan carries no loan fields and raises no payoff milestone."""
        with app.app_context():
            acct = _create_small_loan(seed_user, db.session, name="Drifted")
            assert acct.loan_params is not None

            # Re-type it to a NON-amortizing liability, exactly as the edit route
            # allows; the LoanParams row deliberately survives.
            card_type = (
                db.session.query(AccountType)
                .filter_by(name="Credit Card").one()
            )
            assert card_type.has_amortization is False
            acct.account_type_id = card_type.id
            db.session.commit()
            acct_id = acct.id

            data = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            entry = next(
                ad for ad in data["account_data"] if ad.account.id == acct_id
            )

            # No loan fields: the tile shows no payment / rate / payoff, so the
            # Horizon's domain and its milestone flags cannot pick it up.
            # ONE field now answers both of the questions the dict spelled
            # as two absent keys (``loan_params`` / ``loan_figures``): the
            # projection either has a loan detail or it does not.
            assert entry.loan is None

            # And it raises no "paid off" milestone on the Horizon.
            horizon = data["net_worth"].horizon
            assert horizon is not None
            assert not [
                m for m in horizon["milestones"] if "Drifted" in m["label"]
            ]


class TestNoBaselineIsAnsweredOnceForEveryKind:
    """A user with no baseline scenario gets ONE answer, not five blank tiles.

    Plan step X-v2 (rulings R-BW / R-BZ / R-CA), REVERSING plan step X-t2's
    ruling that this region should degrade in place.  The developer confirmed
    the expected behaviour changed (CLAUDE.md rule 5); this docstring is where
    that reversal is findable from the tests.

    **What X-t2 shipped and why it was wrong.**  It gave the region one
    no-baseline door returning the "today figures over an empty series".  Those
    today figures were ``compute_net_worth_today`` reducing
    ``current_balance or ZERO`` over balances that were ALL ``None`` -- so a
    user whose every balance the app cannot answer was told their net worth,
    total assets and total liabilities were exactly ``$0.00`` (finding N-113).
    Seven reducers in this package made that same substitution, and four other
    surfaces answered the same state four other ways, two of them by
    fabricating a figure and three of them with a 500.

    **What replaces it.**  The seam raises
    :class:`~app.exceptions.BaselineMissingError` and ONE application-level
    handler answers: the setup-recovery card for a page, ``204`` for an HTMX
    fragment.  ``AccountProjection.current_balance`` stopped being nullable in
    the same commit, so the seven fabrications are gone with the state that
    produced them.

    Unreachable in production -- ``auth_service.register_user`` writes a
    baseline for every owner, nothing deletes or un-baselines one, and no path
    promotes a companion to owner -- which is precisely why it needs tests:
    nothing else would ever execute it.  The end-to-end arms live in
    ``tests/test_routes/test_no_baseline_policy.py``, which sweeps every route
    in ``url_map``.
    """

    def test_every_kind_raises_the_same_named_exception(
        self, app, db, seed_user, seed_periods_today,
    ):
        """No baseline: the projection raises, whatever kinds the user holds.

        The discriminating fixture is a loan BESIDE the seeded cash account.
        Before X-t2 the two arms disagreed -- the non-loan map builder returned
        ``{}`` while the loan arm four lines later reached ``require_scenario``
        and raised -- so ONE account kind 500'd a page the other four rendered.
        Both arms now reach the same named exception, which is what "answered
        once" means here; a fixture with only one kind could not tell.
        """
        with app.app_context():
            _create_small_loan(seed_user, db.session, name="No Baseline")
            scenario = db.session.get(Scenario, seed_user["scenario"].id)
            scenario.is_baseline = False
            db.session.commit()

            with pytest.raises(BaselineMissingError):
                savings_dashboard_service.compute_dashboard_data(
                    seed_user["user"].id,
                )

    def test_the_hero_cannot_report_a_fabricated_zero(
        self, app, db, seed_user, seed_periods_today,
    ):
        """No baseline: no figure is produced at all, fabricated or otherwise.

        The regression this pins is finding N-113 as a POSITIVE claim rather
        than an absence: the page used to reach
        :func:`.._net_worth.compute_net_worth_today` with every
        ``current_balance`` ``None`` and report ``$0.00`` net worth, ``$0.00``
        assets and ``$0.00`` liabilities.  The assertion is that the producer
        never returns, so there is no dict to inspect -- if a future change
        re-introduces a degraded return, this fails whatever figures it carries.

        The account set is deliberately NON-trivial (a loan and a cash account
        with real balances), so a ``$0.00`` result would be visibly wrong
        rather than coincidentally right.
        """
        with app.app_context():
            _create_small_loan(seed_user, db.session, name="No Baseline")
            scenario = db.session.get(Scenario, seed_user["scenario"].id)
            scenario.is_baseline = False
            db.session.commit()

            with pytest.raises(BaselineMissingError) as excinfo:
                savings_dashboard_service.compute_dashboard_data(
                    seed_user["user"].id,
                )
            # The message names the repair, which is what makes the handler's
            # card actionable rather than decorative.
            assert "create-baseline" in str(excinfo.value)

    def test_the_page_answers_with_the_repair(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """/savings renders the setup-recovery card for a baseline-less owner.

        The end-to-end arm: a producer that raises is only correct if the
        application answers the raise, and this is the route where X-t2's
        version returned 200 with a page full of fabricated zeros.

        **The fixture carries a PROPERTY SECURING A MORTGAGE, and that is the
        whole discriminator** (kept from plan step X-t5).  X-t2 shipped its
        version of this test with a loan-only fixture and a docstring claiming
        the page renders -- while a third seam door,
        ``compute_property_equity`` -> ``home_equity_service.resolve_home_equity``
        -> ``balance_at.loan_figures``, raised for exactly this user.  Both of
        X-t's adversarial reviews found it by walking the call graph; the
        control that should have caught it could not fire, because its fixture
        had no Property.  The real link is ``collateral_account_id``:
        ``secured_by_account_id`` is NOT a field, and SQLAlchemy accepts that
        assignment in silence, which is how the first Property fixture was born
        dead.
        """
        with app.app_context():
            _create_small_loan(seed_user, db.session, name="No Baseline")
            mortgage = _add_mortgage_account(
                seed_user, seed_periods_today[0].id, Decimal("240000.00"),
            )
            prop = _add_property_account(
                seed_user, seed_periods_today[0].id, Decimal("400000.00"),
            )
            mortgage.collateral_account_id = prop.id
            scenario = db.session.get(Scenario, seed_user["scenario"].id)
            scenario.is_baseline = False
            db.session.commit()

            resp = auth_client.get("/savings")

            assert resp.status_code == 200
            body = resp.data.decode()
            assert "Setup Incomplete" in body
            assert "/create-baseline" in body
            # And none of the page it replaced: no fabricated hero, no chart.
            assert "Net worth" not in body
            assert 'id="net-worth-chart-canvas"' not in body

class TestUnclearingDebtHasNoDebtFreeDate:
    """A loan that never pays off must not be dropped from the debt-free date.

    Plan C8d made ``payoff_date`` legitimately ``None`` for a loan whose payment
    never clears it.  Both debt-free producers filtered those out and took
    ``max()`` over what remained, so a borrower owing $900,000 on a loan the loan
    page labels "No payoff at current payment" was told they go debt-free when
    their small loan ends -- and with every loan in that state, the Horizon fell
    through to its LOAN-FREE fallback window entirely.  Before C8d this could not
    happen: ``LoanState.payoff_date`` was non-nullable.
    """

    def _never_clearing_loan(self, seed_user, db_session, periods):
        """A loan whose level payment cannot cover its own monthly interest.

        Trued up to $900,000 against a $240,000/30yr contract (a ~$1,439 payment
        versus $4,500 of monthly interest at 6%), so the balance grows and the
        fold never reaches zero.
        """
        # Pylint: ``import-outside-toplevel`` -- test-local helpers, matching
        # this module's convention of importing them where used.
        from tests._test_helpers import (  # pylint: disable=import-outside-toplevel
            create_loan_account, insert_trueup_event, loan_params_for,
        )
        acct = create_loan_account(
            seed_user, db_session, name="Never Clears",
            principal=Decimal("240000.00"), rate=Decimal("0.06000"), term=360,
            origination_date=date(2026, 1, 1), anchor_period=periods[0],
        )
        insert_trueup_event(
            loan_params_for(db_session, acct.id), Decimal("900000.00"),
        )
        db_session.commit()
        return acct

    def test_the_debt_summary_reports_no_debt_free_date(
        self, app, db, seed_user, seed_periods,
    ):
        """The date is ``None`` and the reason is flagged, not silently dropped."""
        with app.app_context():
            from app.services import balance_at  # pylint: disable=import-outside-toplevel
            acct = self._never_clearing_loan(seed_user, db.session, seed_periods)

            ctx = BalanceContext.build(seed_user["user"].id)
            figures = balance_at.loan_figures(acct, ctx)
            assert figures.payoff_date is None, "precondition: it never clears"
            assert figures.is_retired is False

            data = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            summary = data["debt_summary"]
            assert summary is not None
            assert summary.total_debt > Decimal("0.00")
            assert summary.payoff_outlook.all_clear_on is None, (
                "the cockpit reports a debt-free date while a loan on the same "
                "page never pays off"
            )
            assert summary.payoff_outlook.never_clears is True

    def test_a_clearing_loan_beside_it_does_not_supply_the_date(
        self, app, db, seed_user, seed_periods,
    ):
        """The healthy loan's payoff must NOT stand in as the debt-free date.

        The measured shape: a $12,000 loan that clears in 2028 alongside a
        $900,000 loan that never does.  Taking ``max()`` over the loans that DO
        clear yields 2028 -- a date at which the borrower still owes $900,000.
        """
        with app.app_context():
            from tests._test_helpers import (  # pylint: disable=import-outside-toplevel
                create_loan_account,
            )
            self._never_clearing_loan(seed_user, db.session, seed_periods)
            create_loan_account(
                seed_user, db.session, name="Healthy Small",
                principal=Decimal("12000.00"), rate=Decimal("0.05000"),
                term=24, origination_date=date(2026, 1, 1),
                anchor_period=seed_periods[0],
            )
            db.session.commit()

            data = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            assert data["debt_summary"].payoff_outlook.all_clear_on is None

    def test_the_horizon_is_not_loan_free(
        self, app, db, seed_user, seed_periods,
    ):
        """The Horizon plants no "Debt-free" flag and does not go loan-free.

        Two assertions on two producers, because plan step X-q2 put each fact
        where it is derived.  The Horizon plants no flag -- its domain resolver
        has no date to plant one at.  Whether the user is loan-free is the
        OUTLOOK's, and the resolver used to republish it as a third tuple
        element nothing read (finding N-100); a "no date" that means "a loan
        never clears" and a "no date" that means "no loans" are what
        :class:`~..._debt_line.LoanPayoffOutlook` exists to tell apart, and the
        cockpit footer on the same page renders the difference.
        """
        with app.app_context():
            # Pylint: ``import-outside-toplevel`` -- the private producers
            # under test, imported where used.
            from app.services.savings_dashboard_service._debt_line import (  # pylint: disable=import-outside-toplevel
                loan_payoff_outlook,
            )
            from app.services.savings_dashboard_service._horizon import (  # pylint: disable=import-outside-toplevel
                _resolve_horizon_domain,
            )
            self._never_clearing_loan(seed_user, db.session, seed_periods)

            data = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            _end, debt_free = _resolve_horizon_domain(
                data["account_data"], date(2026, 3, 20),
            )
            assert debt_free is None
            assert loan_payoff_outlook(
                data["account_data"],
            ).is_loan_free is False, (
                "a borrower carrying a loan that never pays off was reported "
                "loan-free"
            )


class TestTheProjectionShape:
    """The per-account projection is a value object, and its shape is pinned.

    Plan step X-t1, finding N-111.  ``_project_one_account`` returned an
    untyped dict with five required keys and four optional ones, and KEY
    MEMBERSHIP was the type discriminator: ``"loan_figures" in ad`` meant "this
    account is a loan".  Two measured defects came out of that container --
    B-16 (a retired loan reported as debt still owed, because the dict copied
    the seam's ``LoanFigures`` field by field and dropped the one field the
    question needed) and N-98 (19 years of contradiction between two surfaces
    on one page).

    These pin what the container now guarantees.  A future step that
    re-flattens the loan half, or re-stores a value the projection can derive,
    fails here.
    """

    def test_the_field_set_is_exactly_what_consumers_read(self):
        """The projection's fields are pinned, and the loan half is ONE of them.

        The analogue of the Horizon's published-key pin: a field added without
        a consumer, or the loan detail re-flattened into parallel
        ``loan_figures`` / ``loan_params`` fields (the shape plan step X-s2 had
        to unpick at the seam-batch layer), fails this literal.

        ``balances`` joined the set at plan step X-w (ruling R-CG, finding
        N-114), and its consumers are named so this stays the
        "field added without a consumer" gate it was written to be: the
        per-period net-worth reduction (``_sum_composition_at_period``), the
        card sparklines (``compute_sparklines``), and -- for every kind but a
        loan -- the current balance and the 3 / 6 / 12-month horizons that
        ``_project_one_account`` reads out of it.
        """
        # pylint: disable=import-outside-toplevel
        from dataclasses import fields
        assert {f.name for f in fields(AccountProjection)} == {
            "account", "current_balance", "balances", "projected",
            "needs_setup", "interest_params", "investment_params", "loan",
        }

    def test_it_is_frozen_and_a_mistyped_field_raises(self, app):
        """A projection cannot be mutated, and an unknown field is an error.

        Frozen because every consumer on the page reads the SAME object: the
        grid cell, the hero reduction, the debt summary, the Horizon bands and
        the goal balances.  One of them rewriting a figure the others have
        already read is the class of defect this arc exists to remove, and
        ``dataclasses.FrozenInstanceError`` makes it impossible rather than
        merely uncustomary.

        The second arm is the dict's real cost: ``ad["loan_figures"]`` on a
        cash account raised ``KeyError`` while ``ad.get("loan_figures")``
        answered ``None`` and Jinja's ``ad.loan_figures`` answered
        ``Undefined`` -- three spellings, one of them silent.  There is now one
        answer and it is loud.
        """
        # pylint: disable=import-outside-toplevel
        from dataclasses import FrozenInstanceError
        from types import SimpleNamespace
        with app.app_context():
            projection = _projection(
                SimpleNamespace(account_type=None), Decimal("1.00"),
            )
            with pytest.raises(FrozenInstanceError):
                projection.current_balance = Decimal("2.00")
            with pytest.raises(AttributeError):
                _ = projection.loan_figures

    def test_the_liability_flag_is_derived_not_stored(
        self, app, db, seed_user, seed_periods_today,
    ):
        """``is_liability`` IS the classifier, for a loan and for cash.

        It was a STORED key, and the page asked the same rule two ways over one
        set of balances: the grid cell read the stored flag while
        ``compute_net_worth_today`` -- summing the very balances those cells
        show -- re-derived it from the account.  Derived, they cannot differ.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.account_category import is_liability_account
        with app.app_context():
            loan = _create_small_loan(seed_user, db.session, name="Van")
            db.session.commit()

            data = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            by_id = {ad.account.id: ad for ad in data["account_data"]}
            assert by_id[loan.id].is_liability is True
            assert by_id[seed_user["account"].id].is_liability is False
            # And it is the classifier itself, not a copy that agrees today.
            for ad in data["account_data"]:
                assert ad.is_liability == is_liability_account(ad.account)

    def test_every_kind_carries_its_dense_period_map_including_a_loan(
        self, app, db, seed_user, seed_periods_today,
    ):
        """``balances`` is TOTAL over the pay periods, for cash AND for a loan.

        Plan step X-w, ruling R-CG, finding N-114.  The projection carried no
        dense map at all, and a SECOND per-account container was built beside it
        for the net-worth trend and the card sparklines -- storing the liability
        flag this projection derives.  Folding the map in is what deleted that
        container, and it only works if the map covers EVERY kind: a loan's tile
        reads none of it, but the trend and the liability band do, and the loans
        were exactly what the old batch left out.

        The loan arm is the one worth pinning to the cent.  Its
        ``current_balance`` is still the SCALAR
        (:func:`app.services.balance_at.balance_at`), not a read of this map,
        and the two agree because the seam clamps the current period's column to
        the read pass's ``as_of`` -- a property of the seam's construction, not
        of this module's, so it is asserted rather than assumed.  It was
        measured equal to the cent for both real loans on both databases at the
        step's trace.
        """
        with app.app_context():
            loan = _create_small_loan(seed_user, db.session, name="Van")
            db.session.commit()
            user_id = seed_user["user"].id
            all_period_ids = {
                p.id for p in pay_period_service.get_all_periods(user_id)
            }
            current = pay_period_service.get_current_period(user_id)

            data = savings_dashboard_service.compute_dashboard_data(user_id)
            by_id = {ad.account.id: ad for ad in data["account_data"]}

            for ad in data["account_data"]:
                assert set(ad.balances) == all_period_ids, (
                    f"{ad.account.name}'s dense map does not cover every pay "
                    f"period, so the net-worth trend would read a gap"
                )
            # The loan is in the map (it was excluded until this step) AND its
            # map agrees with the scalar the tile renders.
            assert by_id[loan.id].balances[current.id] == (
                by_id[loan.id].current_balance
            )


class TestTheDenseMapIsTotalAndSaysSo:
    """A missing period column FAILS LOUD at every reader of the dense map.

    Plan step X-w6, ruling R-CK.  ``_net_worth`` ended plan step X-w1 asking
    "is this map total over this window?" THREE ways: the projection's own read
    INDEXED, the per-period composition reduction wrote
    ``ad.balances.get(period_id, ZERO)``, and the sparkline producer wrote
    ``[balances[p.id] for p in window if p.id in balances]``.

    The two defaults are unreachable -- every window on this path is a slice of
    the period list the seam builds each map over -- and they degrade in
    opposite, silent, financially wrong directions if ever reached.  ``ZERO``
    banks a real account's balance at nothing for that band, so the composition
    stops reconciling to the hero the page asserts it equals.  The membership
    filter SHORTENS the series, and ``_serialize_sparklines`` normalizes on
    series LENGTH, so one dropped point moves every remaining point on the card.

    These pin the loud behaviour, which is what the ``Raises: KeyError`` blocks
    on all three readers claim and nothing asserted before this step.
    """

    @staticmethod
    def _account(account_id=1):
        """A stand-in account: only ``id`` and the liability classifier read it."""
        from types import SimpleNamespace  # pylint: disable=import-outside-toplevel
        return SimpleNamespace(id=account_id, account_type=None)

    def test_the_composition_reduction_raises_on_a_missing_column(self, app):
        """A period the map has no column for is a KeyError, not a silent $0."""
        # pylint: disable=import-outside-toplevel
        from app.services.savings_dashboard_service._net_worth import (
            _sum_composition_at_period,
        )
        with app.app_context():
            ad = _projection(
                self._account(7), Decimal("100.00"), {1: Decimal("100.00")},
            )
            # Period 1 is present; period 2 is not.
            assert _sum_composition_at_period(1, [ad], {7: "asset"})["asset"] == (
                Decimal("100.00")
            )
            with pytest.raises(KeyError):
                _sum_composition_at_period(2, [ad], {7: "asset"})

    def test_the_sparkline_producer_raises_on_a_missing_column(self, app):
        """A window point the map lacks is a KeyError, not a shorter series.

        A shorter series is the dangerous answer: the route normalizes each
        sparkline into a fixed viewBox by its own LENGTH, so silently dropping
        one point re-positions every other point on that card.
        """
        # pylint: disable=import-outside-toplevel
        from types import SimpleNamespace
        from app.services.savings_dashboard_service._net_worth import (
            compute_sparklines,
        )
        with app.app_context():
            balances = {i: Decimal(str(1000 * i)) for i in range(1, 6)}
            ad = _projection(self._account(8), Decimal("1000.00"), balances)
            window = [SimpleNamespace(id=i) for i in range(1, 6)]
            assert len(compute_sparklines([ad], window)[8]) == 5
            # One period beyond the map's domain.
            with pytest.raises(KeyError):
                compute_sparklines([ad], window + [SimpleNamespace(id=99)])

    def test_the_projection_read_raises_on_a_missing_current_period(self, app):
        """``_current_balance_from_map`` states ``Raises: KeyError`` -- it does.

        Its contract has said so since plan step X-v2 (ruling R-CA) and nothing
        asserted it; this is the third reader of the same invariant, so it is
        pinned with the other two.
        """
        # pylint: disable=import-outside-toplevel
        from types import SimpleNamespace
        from app.services.savings_dashboard_service._projections import (
            _current_balance_from_map,
        )
        with app.app_context():
            acct = SimpleNamespace(current_anchor_balance=Decimal("5.00"))
            ctx = SimpleNamespace(current_period=SimpleNamespace(id=2))
            assert _current_balance_from_map(
                {2: Decimal("42.00")}, acct, ctx,
            ) == Decimal("42.00")
            with pytest.raises(KeyError):
                _current_balance_from_map({1: Decimal("42.00")}, acct, ctx)


def _with_badging_predicate(account_data):
    """Rebuild *account_data* with ``is_retired`` set to the BADGING predicate.

    The shared firing control for the two tests below: it substitutes
    ``is_paid_off`` for ``is_retired`` on every loan projection, so a producer
    that asks the debt-LINE question with the CONGRATULATION predicate answers
    differently and the control fires.

    Every level is rebuilt through ``dataclasses.replace`` on the real frozen
    types -- the projection, its ``LoanDetail``, and the seam's own
    ``LoanFigures`` -- so the control exercises the production shapes rather
    than dicts arranged to look like them (finding B-17's lesson; the
    projection became a frozen value at plan step X-t1, and rewriting it in
    place is no longer possible even if a test wanted to).

    Args:
        account_data: The per-account projections from
            ``compute_dashboard_data``.

    Returns:
        A new list, same order, with the loan projections substituted.
    """
    return [
        ad if ad.loan is None else replace(
            ad,
            loan=replace(
                ad.loan,
                figures=replace(
                    ad.loan.figures, is_retired=ad.loan.figures.is_paid_off,
                ),
            ),
        )
        for ad in account_data
    ]


class TestARetiredLoanHasNoDebtLine:
    """The Horizon asks the debt-line question with the debt-line predicate.

    Finding B-16, plan step X-o.  ``LoanFigures`` states the split in terms --
    "Use ``is_retired`` to decide whether a loan has a debt line; use this
    [``is_paid_off``] to decide whether to CONGRATULATE the user" -- because
    ``is_paid_off`` adds a confirmed-payment guard that exists for BADGING.  A
    loan paid off by a LUMP SUM recorded as a balance true-up has no payment
    rows, so it owes ``$0.00`` and reads ``is_paid_off=False``.

    The Horizon's domain resolver asked the debt-LINE question with that
    badging predicate, so such a loan stayed in the ACTIVE set -- and, being
    retired, it has no forward payoff to date, which fired the "an active loan
    with no payoff never clears" branch: no debt-free date, every STRUCTURAL
    flag gone (the payoffs and "Debt-free"; the net-worth crossing flags are
    built from the trajectory and survived), and the axis cut back to the
    loan-free fallback window, while the debt-summary caption on the SAME page
    (which selects on the loan's BALANCE) still reported the real date.
    Measured on the developer's own two loans: the axis ended 2036-12-31 where
    the debt line ends 2049-12-31.  The same collapse drew $197,049.32 of
    phantom debt on the property equity chart -- the incident the seam's
    contract was written by.

    **Only the DOMAIN resolver was a defect.**  ``_structural_milestones``
    took the same predicate, and plan step X-o moved it onto the shared
    selection too -- but its own ``payoff is not None`` test already excluded
    every retired loan (a retired loan has no forward crossing to date), so
    that half is behaviour-neutral by construction and has no firing control
    because none can exist.  Said here rather than discovered at a review.
    """

    # today + _LOAN_FREE_HORIZON_YEARS, on this module's frozen 2026-03-20
    # clock: the window the resolver falls back to when it has no debt-free
    # date to size an axis with.
    _FALLBACK_END = date(2036, 12, 31)
    # The clearing loan's DERIVED payoff.  Its 24 installments run 2026-02-01
    # .. 2028-01-01, but the fold pays nothing it has no settled record for,
    # and at the frozen 2026-03-20 as-of TWO installments (2026-02-01 and
    # 2026-03-01) are already due and unpaid -- so the plan's zero crossing
    # lands two installments past the contractual date.
    _CLEARING_PAYOFF = date(2028, 3, 1)
    # The domain end the resolver derives from it: the payoff year plus one,
    # at that year's end.
    _CLEARING_DOMAIN_END = date(2029, 12, 31)

    def _two_loans(self, seed_user, db_session, periods):
        """A loan retired by a lump-sum true-up, beside one that still clears.

        The retired loan has ZERO settled payment rows -- the shape the app's
        own true-up UI produces -- so it is ``is_retired`` without being
        ``is_paid_off``.  Its true-up is dated two months AFTER origination so
        the fixture tells the story it claims (a loan borrowed, then cleared by
        a lump sum) rather than the degenerate ``$0``-opening shape
        ``is_paid_off``'s guard exists to catch.

        The second loan is what makes the debt-free date OBSERVABLE: with only
        the retired one there is no date to report either way, and the test
        could not tell the predicates apart.
        """
        # Pylint: ``import-outside-toplevel`` -- test-local helpers, matching
        # this module's convention of importing them where used.
        from tests._test_helpers import (  # pylint: disable=import-outside-toplevel
            create_loan_account, insert_trueup_event, loan_params_for,
        )
        retired = create_loan_account(
            seed_user, db_session, name="Lump Sum Payoff",
            principal=Decimal("12000.00"), rate=Decimal("0.05000"), term=24,
            origination_date=date(2026, 1, 1), anchor_period=periods[0],
        )
        insert_trueup_event(
            loan_params_for(db_session, retired.id), Decimal("0.00"),
            anchor_date=date(2026, 3, 1),
        )
        clearing = create_loan_account(
            seed_user, db_session, name="Still Clearing",
            principal=Decimal("12000.00"), rate=Decimal("0.05000"), term=24,
            origination_date=date(2026, 1, 1), anchor_period=periods[0],
        )
        db_session.commit()
        return retired, clearing

    def test_the_retired_loan_is_not_a_debt_line(
        self, app, db, seed_user, seed_periods,
    ):
        """The debt-free date is the SURVIVING loan's payoff, and it is flagged.

        End to end through ``compute_dashboard_data``: the domain runs to the
        clearing loan's payoff plus a year and the "Debt-free" flag lands on
        that payoff.  Every expected value is a pinned literal, not a value
        read back off the producer: a regression that moved the clearing
        loan's payoff to some other future date must fail here.  Its firing
        control is :meth:`test_the_badging_predicate_loses_the_debt_free_date`,
        which re-runs the same data through the predicate this step replaced.
        """
        with app.app_context():
            # Pylint: ``import-outside-toplevel`` -- the private producer under
            # test, imported where used.
            from app.services.savings_dashboard_service._horizon import (  # pylint: disable=import-outside-toplevel
                _DEBT_FREE_MILESTONE_LABEL,
                _resolve_horizon_domain,
            )
            retired, clearing = self._two_loans(
                seed_user, db.session, seed_periods,
            )

            ctx = BalanceContext.build(seed_user["user"].id)
            retired_figures = balance_at.loan_figures(retired, ctx)
            clearing_figures = balance_at.loan_figures(clearing, ctx)
            # The precondition that makes this fixture discriminating: the loan
            # owes nothing and is NOT badged, because nothing was ever paid.
            assert balance_at.balance_at(
                retired, ctx, ctx.as_of,
            ) == Decimal("0.00")
            assert retired_figures.is_retired is True
            assert retired_figures.is_paid_off is False
            assert retired_figures.payoff_date is None
            assert clearing_figures.payoff_date == self._CLEARING_PAYOFF

            data = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            assert _resolve_horizon_domain(
                data["account_data"], date(2026, 3, 20),
            ) == (self._CLEARING_DOMAIN_END, self._CLEARING_PAYOFF)

            horizon = data["net_worth"].horizon
            assert {
                (m["label"], m["date"]) for m in horizon["milestones"]
            } >= {(_DEBT_FREE_MILESTONE_LABEL, self._CLEARING_PAYOFF)}

            # And the caption on the same page agrees, which is the point:
            # the debt summary always read the real date here (it selects on
            # the loan's BALANCE) while the chart beside it did not.  Since
            # plan step X-q both fold ONE derivation
            # (``_debt_line.loan_payoff_outlook``), so this asserts an
            # agreement that is now structural; the shape that proves the
            # merge is ``TestTheDebtFreeDateIsOneDerivation``.
            assert data["debt_summary"].payoff_outlook.all_clear_on == (
                self._CLEARING_PAYOFF
            )

    def test_the_badging_predicate_loses_the_debt_free_date(
        self, app, db, seed_user, seed_periods,
    ):
        """FIRING CONTROL: the replaced predicate, on the same real data.

        ``_debt_line.debt_line_loans`` selects on ``is_retired``; before plan
        step X-o it selected on ``is_paid_off`` (and lived in ``_horizon``).  Substituting the old predicate into the
        projection dicts -- exactly what the old line read -- must produce the
        defect: no debt-free date, and the domain cut back to the loan-free
        fallback window.  A control that cannot fail is not a control
        (Section 7.3 of the balance plan of record).
        """
        with app.app_context():
            # Pylint: ``import-outside-toplevel`` -- the private producer under
            # test, imported where used.
            from app.services.savings_dashboard_service._horizon import (  # pylint: disable=import-outside-toplevel
                _resolve_horizon_domain,
            )
            self._two_loans(seed_user, db.session, seed_periods)

            data = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            account_data = data["account_data"]
            # The fixed producer, on unmodified data.
            assert _resolve_horizon_domain(
                account_data, date(2026, 3, 20),
            ) == (self._CLEARING_DOMAIN_END, self._CLEARING_PAYOFF)

            # The same producer, reading the badging predicate instead.  The
            # substitution is made through ``dataclasses.replace`` on the real
            # frozen types at all three levels, so the control exercises the
            # production shapes rather than dicts arranged to look like them.
            assert _resolve_horizon_domain(
                _with_badging_predicate(account_data), date(2026, 3, 20),
            ) == (self._FALLBACK_END, None), (
                "the control does not fire: the badging predicate produced the "
                "same domain as the debt-line predicate, so this fixture "
                "cannot tell them apart"
            )

    def test_a_user_whose_only_loan_is_retired_is_loan_free(
        self, app, db, seed_user, seed_periods,
    ):
        """One loan, retired by a true-up: the user IS loan-free.

        The single-loan half of the same defect.  ``is_loan_free`` separates
        "no debt line at all" from "a debt line that never clears", and the
        badging predicate collapsed a retired loan into the second.

        **This one pins a producer CONTRACT, not a rendered figure, and the
        distinction is stated so the coverage is not over-read.**  With a
        single retired loan the Horizon's domain resolver returns the same
        answer either way -- the fallback window and no date -- so the sample
        dates, both series and the milestone list are identical whichever
        predicate is used, and the assertion below records that rather than
        claiming a chart moved.

        **The discriminating value is the OUTLOOK's ``is_loan_free``, and it is
        asserted where it is derived** (plan step X-q2).  The resolver
        republished it as a third tuple element until then, which is finding
        N-100 -- so this test was asserting the state of a copy nothing read.
        The copy is gone; the fact is not, and a producer that reports "still
        in debt" for a borrower who owes nothing is wrong whether or not a
        screen has asked yet.  The cockpit footer is the screen that asks.
        """
        with app.app_context():
            # Pylint: ``import-outside-toplevel`` -- the private producers
            # under test, imported where used.
            from app.services.savings_dashboard_service._debt_line import (  # pylint: disable=import-outside-toplevel
                loan_payoff_outlook,
            )
            from app.services.savings_dashboard_service._horizon import (  # pylint: disable=import-outside-toplevel
                _resolve_horizon_domain,
            )
            from tests._test_helpers import (  # pylint: disable=import-outside-toplevel
                create_loan_account, insert_trueup_event, loan_params_for,
            )
            retired = create_loan_account(
                seed_user, db.session, name="Only Loan",
                principal=Decimal("12000.00"), rate=Decimal("0.05000"),
                term=24, origination_date=date(2026, 1, 1),
                anchor_period=seed_periods[0],
            )
            insert_trueup_event(
                loan_params_for(db.session, retired.id), Decimal("0.00"),
            )
            db.session.commit()

            data = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            account_data = data["account_data"]
            assert loan_payoff_outlook(account_data).is_loan_free is True
            # The chart is unmoved either way, and that is the point of the
            # note above: the axis has no payoff to size itself on.
            assert _resolve_horizon_domain(
                account_data, date(2026, 3, 20),
            ) == (self._FALLBACK_END, None)

            # FIRING CONTROL: the badging predicate keeps the retired loan in
            # the active set, and a retired loan has no forward payoff to date
            # -- so the user is reported NOT loan-free, on a loan they have
            # already cleared.
            badged = _with_badging_predicate(account_data)
            assert loan_payoff_outlook(badged).is_loan_free is False, (
                "the control does not fire: the badging predicate reported the "
                "same loan-free state as the debt-line predicate, so this "
                "fixture cannot tell them apart"
            )
            assert _resolve_horizon_domain(
                badged, date(2026, 3, 20),
            ) == (self._FALLBACK_END, None)


class TestTheDebtFreeDateIsOneDerivation:
    """The caption and the Horizon flag read ONE debt-free derivation.

    Finding N-98, plan step X-q.  ``/savings`` renders both on one page and
    derived the date twice from the same ``account_data``: the cockpit's
    ``Debt-free <month>`` caption through ``_metrics._compute_debt_summary``,
    which selected loans by their current BALANCE, and the Horizon chart's
    ``Debt-free`` flag through ``_horizon._resolve_horizon_domain``, which
    selected them by the debt-line predicate.

    They part on a loan that has NOT been borrowed yet.  It owes ``$0.00``
    today, so the balance rule dropped a mortgage whose whole 30-year line is
    ahead of it and the caption reported the date the OTHER loans finish --
    measured 19 years early on the developer's own mortgage rewritten into
    that state, and 28 years early on the fixture below.

    Both now fold :func:`.._debt_line.loan_payoff_outlook`.  The MONEY figures
    keep their own membership deliberately (``total_debt`` and the monthly
    payments answer "what do you owe today", where an unclosed mortgage
    contributes nothing and pays nothing), which the last assertion pins so a
    later "simplification" onto one set has to fail here first.
    """

    # A 24-month, $12,000 @ 5% loan originated 2026-01-01 (payment_day 1) has
    # installments due 2026-02-01 .. 2028-01-01, but at the module's frozen
    # 2026-03-20 clock two are already due and unpaid, and the fold pays
    # nothing it has no settled record for -- so its zero crossing lands two
    # installments past the contractual date.
    _CAR_PAYOFF = date(2028, 3, 1)
    # A 360-month mortgage originating 2026-06-01 (payment_day 1) has not been
    # borrowed at the frozen clock, so nothing is overdue and its plan folds
    # from its opening anchor to the contractual last installment.
    _MORTGAGE_PAYOFF = date(2056, 6, 1)

    def _car_now_and_mortgage_later(self, seed_user, db_session, periods):
        """A loan already running, beside a mortgage that closes in June."""
        # Pylint: ``import-outside-toplevel`` -- test-local helpers, matching
        # this module's convention of importing them where used.
        from tests._test_helpers import (  # pylint: disable=import-outside-toplevel
            create_loan_account,
        )
        create_loan_account(
            seed_user, db_session, name="Car Loan",
            principal=Decimal("12000.00"), rate=Decimal("0.05000"), term=24,
            origination_date=date(2026, 1, 1), anchor_period=periods[0],
        )
        create_loan_account(
            seed_user, db_session, name="Future Mortgage",
            principal=Decimal("200000.00"), rate=Decimal("0.06000"), term=360,
            origination_date=date(2026, 6, 1), anchor_period=periods[0],
            account_type=AcctTypeEnum.MORTGAGE,
        )
        db_session.commit()

    def test_an_unclosed_mortgage_sets_the_date_on_both_surfaces(
        self, app, db, seed_user, seed_periods,
    ):
        """Both surfaces report the mortgage's payoff, not the car loan's."""
        with app.app_context():
            # Pylint: ``import-outside-toplevel`` -- the private producers
            # under test, imported where used.
            from app.services.savings_dashboard_service._debt_line import (  # pylint: disable=import-outside-toplevel
                loan_payoff_outlook,
            )
            from app.services.savings_dashboard_service._horizon import (  # pylint: disable=import-outside-toplevel
                _DEBT_FREE_MILESTONE_LABEL,
                _resolve_horizon_domain,
            )
            self._car_now_and_mortgage_later(
                seed_user, db.session, seed_periods,
            )

            data = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            account_data = data["account_data"]
            by_name = {
                ad.account.name: ad for ad in account_data
                if ad.loan is not None
            }
            # The preconditions that make the fixture discriminating: the
            # mortgage owes nothing today and is NOT retired -- its whole line
            # is ahead of it -- and it clears far later than the car loan.
            assert by_name["Future Mortgage"].current_balance == (
                Decimal("0.00")
            )
            assert by_name["Future Mortgage"].loan.figures.terms.is_originated is False
            assert by_name["Future Mortgage"].loan.figures.is_retired is False
            assert by_name["Future Mortgage"].loan.figures.payoff_date == (
                self._MORTGAGE_PAYOFF
            )
            assert by_name["Car Loan"].loan.figures.payoff_date == self._CAR_PAYOFF

            # ONE derivation, and both surfaces read it.
            assert loan_payoff_outlook(account_data).all_clear_on == (
                self._MORTGAGE_PAYOFF
            )
            assert data["debt_summary"].payoff_outlook.all_clear_on == (
                self._MORTGAGE_PAYOFF
            )
            assert _resolve_horizon_domain(
                account_data, date(2026, 3, 20),
            ) == (date(2057, 12, 31), self._MORTGAGE_PAYOFF)
            assert {
                (m["label"], m["date"]) for m in data["net_worth"].horizon[
                    "milestones"
                ]
            } >= {(_DEBT_FREE_MILESTONE_LABEL, self._MORTGAGE_PAYOFF)}

            # The MONEY figures keep the owed-today set: the mortgage is not
            # borrowed, so it owes nothing and pays nothing this month.  Pinned
            # as literals, not read back off the producer: the car loan has no
            # settled payment at the frozen clock, so the fold is still its
            # $12,000.00 opening anchor, and its level payment is
            # 12000 * r(1+r)^24 / ((1+r)^24 - 1) at r = 0.05/12 = $526.46.
            assert data["debt_summary"].total_debt == Decimal("12000.00")
            assert data["debt_summary"].total_monthly_payments == (
                Decimal("526.46")
            )

    def test_the_balance_rule_reports_the_wrong_date(
        self, app, db, seed_user, seed_periods,
    ):
        """FIRING CONTROL: the replaced membership rule, on the same data.

        Before plan step X-q the caption's date was derived inside the
        owed-today loop, so its membership was "current balance > 0".  This
        substitutes that MEMBERSHIP into the new fold rather than resurrecting
        the deleted loop -- the fold itself (latest payoff, poisoned by an
        absent one) is unchanged between them, so the set is the whole
        difference and is what this isolates.  It must produce the CAR LOAN's
        payoff: the borrower told they are done with debt 28 years before a
        mortgage they are about to close is paid off.
        """
        with app.app_context():
            # Pylint: ``import-outside-toplevel`` -- the private producer under
            # test, imported where used.
            from app.services.savings_dashboard_service._debt_line import (  # pylint: disable=import-outside-toplevel
                loan_payoff_outlook,
            )
            self._car_now_and_mortgage_later(
                seed_user, db.session, seed_periods,
            )

            account_data = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )["account_data"]
            owed_today = [
                ad for ad in account_data
                if ad.loan is not None
                and (ad.current_balance or Decimal("0.00")) > Decimal("0.00")
            ]
            assert loan_payoff_outlook(owed_today).all_clear_on == (
                self._CAR_PAYOFF
            ), (
                "the control does not fire: the owed-today set produced the "
                "same date as the debt-line set, so this fixture cannot tell "
                "the two membership rules apart"
            )
            assert loan_payoff_outlook(account_data).all_clear_on == (
                self._MORTGAGE_PAYOFF
            )

    def test_a_past_payoff_is_reported_but_cannot_size_the_axis(
        self, app, db, seed_user, seed_periods,
    ):
        """The developer's ruling on a payoff date that is already behind us.

        ``plan_payoff_date`` returns the DUE date the balance first folds to
        zero, and an overdue-but-still-projected installment that clears the
        loan folds at a date behind today.  The ruling (plan step X-q): the
        outlook REPORTS that date -- it is a fact about the loan's plan -- and
        the Horizon, whose axis is today-forward, falls back to its fixed
        window for AXIS SIZING only, because
        ``savings._milestone_axis_x`` clamps a target at or before
        ``dates[0]`` to index 0.0 and the flag would be planted on "Today".

        The state is exercised by asking the domain resolver about a ``today``
        past every payoff, which is what the producer's own signature allows
        and is far cheaper than manufacturing an overdue installment.  **This
        is the branch that CHANGED**: the rule it replaced dropped past
        payoffs per loan and then read the empty list as "no loans at all",
        so it returned ``is_loan_free=True`` for a borrower whose loan had
        not cleared.

        **Plan step X-q2 made that answer unreachable from here rather than
        merely correct.**  The resolver no longer returns a loan-free flag at
        all, and the outlook that does own one takes no reader ``today`` to
        filter payoffs on -- so a reader's clock can size an axis and can
        decide whether a flag is drawable, and it can no longer decide whether
        the borrower is out of debt.  Both assertions below are therefore about
        different producers on purpose.
        """
        with app.app_context():
            # Pylint: ``import-outside-toplevel`` -- the private producers
            # under test, imported where used.
            from app.services.savings_dashboard_service._debt_line import (  # pylint: disable=import-outside-toplevel
                loan_payoff_outlook,
            )
            from app.services.savings_dashboard_service._horizon import (  # pylint: disable=import-outside-toplevel
                _resolve_horizon_domain,
            )
            self._car_now_and_mortgage_later(
                seed_user, db.session, seed_periods,
            )

            account_data = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )["account_data"]
            # Read at a date past BOTH payoffs.
            after_everything = date(2060, 1, 1)
            assert loan_payoff_outlook(account_data).all_clear_on == (
                self._MORTGAGE_PAYOFF
            ), "the outlook reports the date whatever the reader's clock"
            assert loan_payoff_outlook(account_data).is_loan_free is False, (
                "a borrower whose loans have not cleared was reported loan-free"
            )
            assert _resolve_horizon_domain(
                account_data, after_everything,
            ) == (date(2070, 12, 31), None), (
                "the axis was sized to a past date, or the flag was kept for "
                "``_milestone_axis_x`` to clamp onto Today"
            )

    def test_a_revolving_balance_is_named_beside_the_date(
        self, app, db, seed_user, seed_periods,
    ):
        """A card the payoff date cannot cover is reported, not implied away.

        Developer ruling on finding N-99 (plan step X-q3): the derivation
        stays over the debts that HAVE a payoff model, and the surfaces say
        so.  A revolving Credit Card has no forward model -- the seam holds it
        FLAT at its owed magnitude, so it never reaches zero -- and it is
        invisible to :func:`.._debt_line.loan_payoff_outlook`.  Without the
        caveat a borrower reads a payoff month on a page whose own liability
        band never touches zero.

        The card is anchored OWED-AS-NEGATIVE, which is the app's convention
        (``TestNegativelyAnchoredLiability``), and the caveat reports the
        magnitude -- the same ``abs`` the net-worth liability total takes.
        """
        with app.app_context():
            # Pylint: ``import-outside-toplevel`` -- test-local helper,
            # matching this module's convention of importing where used.
            from tests._test_helpers import (  # pylint: disable=import-outside-toplevel
                create_account_of_type, create_loan_account,
            )
            create_loan_account(
                seed_user, db.session, name="Car Loan",
                principal=Decimal("12000.00"), rate=Decimal("0.05000"),
                term=24, origination_date=date(2026, 1, 1),
                anchor_period=seed_periods[0],
            )
            create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            db.session.commit()

            summary = savings_dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )["debt_summary"]

            # The card is NOT in the payoff derivation and does NOT poison it
            # -- that is the ruling, not an accident.
            assert summary.payoff_outlook.all_clear_on == (
                self._CAR_PAYOFF
            )
            assert summary.payoff_outlook.never_clears is False
            # And it is not in the loan money aggregates either.
            assert summary.total_debt == Decimal("12000.00")
            # It IS named, at its owed magnitude.
            assert summary.revolving_debt == Decimal("500.00")

    def test_the_narrow_producer_reports_the_same_revolving_debt(
        self, app, db, seed_user, seed_periods,
    ):
        """The two paths to the debt summary agree on EVERY key.

        ``compute_debt_summary`` promises "identical figures to
        ``compute_dashboard_data(...)['debt_summary']`` by construction", and
        it kept that promise by projecting the same loans.  Plan step X-q3
        added a key that is NOT about loans -- the revolving debt the payoff
        date cannot speak for -- so a loans-only projection reported
        ``$0.00`` there while the full build reported the real figure.
        Nothing rendered the difference, which is what made it worth fixing:
        two paths to one number that quietly disagree are the shape this arc
        exists to remove.
        """
        with app.app_context():
            # Pylint: ``import-outside-toplevel`` -- test-local helpers,
            # matching this module's convention of importing where used.
            from tests._test_helpers import (  # pylint: disable=import-outside-toplevel
                create_account_of_type, create_loan_account,
            )
            create_loan_account(
                seed_user, db.session, name="Car Loan",
                principal=Decimal("12000.00"), rate=Decimal("0.05000"),
                term=24, origination_date=date(2026, 1, 1),
                anchor_period=seed_periods[0],
            )
            create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            db.session.commit()
            user_id = seed_user["user"].id

            full = savings_dashboard_service.compute_dashboard_data(
                user_id,
            )["debt_summary"]
            narrow = savings_dashboard_service.compute_debt_summary(user_id)

            assert narrow.revolving_debt == Decimal("500.00")
            assert narrow.revolving_debt == full.revolving_debt
            # And every other key still agrees, which is the promise itself.
            assert narrow == full

