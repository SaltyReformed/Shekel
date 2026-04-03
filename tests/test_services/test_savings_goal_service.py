"""
Shekel Budget App -- Unit Tests for Savings Goal Service

Tests the pure calculation functions in savings_goal_service.py:
calculate_required_contribution, calculate_savings_metrics,
count_periods_until, resolve_goal_target, and calculate_trajectory.
"""

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app import ref_cache
from app.enums import GoalModeEnum, IncomeUnitEnum
from app.services.savings_goal_service import (
    calculate_required_contribution,
    calculate_savings_metrics,
    calculate_trajectory,
    count_periods_until,
    resolve_goal_target,
)


# ── TestCalculateRequiredContribution ────────────────────────────


class TestCalculateRequiredContribution:
    """Per-period contribution needed to reach a savings goal."""

    def test_gap_exists_returns_per_period_amount(self):
        """Standard case: divide remaining gap by periods."""
        result = calculate_required_contribution(
            current_balance=Decimal("1000"),
            target_amount=Decimal("2000"),
            remaining_periods=5,
        )
        assert result == Decimal("200.00")

    def test_already_met_returns_zero(self):
        """Balance already exceeds target -- no contribution needed."""
        result = calculate_required_contribution(
            current_balance=Decimal("5000"),
            target_amount=Decimal("3000"),
            remaining_periods=5,
        )
        assert result == Decimal("0.00")

    def test_remaining_periods_zero_returns_none(self):
        """Zero remaining periods -- past due, return None."""
        result = calculate_required_contribution(
            current_balance=Decimal("1000"),
            target_amount=Decimal("2000"),
            remaining_periods=0,
        )
        assert result is None

    def test_remaining_periods_negative_returns_none(self):
        """Negative remaining periods -- past due, return None."""
        result = calculate_required_contribution(
            current_balance=Decimal("1000"),
            target_amount=Decimal("2000"),
            remaining_periods=-1,
        )
        assert result is None

    def test_decimal_precision_round_half_up(self):
        """Repeating decimal is quantized to 2 places with ROUND_HALF_UP."""
        # 100 / 3 = 33.333... → 33.33
        result = calculate_required_contribution(
            current_balance=Decimal("0"),
            target_amount=Decimal("100"),
            remaining_periods=3,
        )
        assert result == Decimal("33.33")


# ── TestCalculateSavingsMetrics ──────────────────────────────────


class TestCalculateSavingsMetrics:
    """How long savings would cover monthly expenses."""

    def test_returns_months_paychecks_years(self):
        """Standard case: $12k balance / $2k expenses."""
        result = calculate_savings_metrics(
            savings_balance=Decimal("12000"),
            average_monthly_expenses=Decimal("2000"),
        )
        assert result["months_covered"] == Decimal("6.0")
        assert result["paychecks_covered"] == Decimal("13.0")
        assert result["years_covered"] == Decimal("0.5")

    def test_paychecks_formula(self):
        """Paychecks = months * 26 / 12."""
        result = calculate_savings_metrics(
            savings_balance=Decimal("24000"),
            average_monthly_expenses=Decimal("3000"),
        )
        # months = 8.0, paychecks = 8.0 * 26 / 12 = 17.333... → 17.3
        assert result["months_covered"] == Decimal("8.0")
        assert result["paychecks_covered"] == Decimal("17.3")

    def test_years_formula(self):
        """Years = months / 12."""
        result = calculate_savings_metrics(
            savings_balance=Decimal("36000"),
            average_monthly_expenses=Decimal("1000"),
        )
        # months = 36.0, years = 36 / 12 = 3.0
        assert result["months_covered"] == Decimal("36.0")
        assert result["years_covered"] == Decimal("3.0")

    def test_expenses_zero_returns_all_zeros(self):
        """Zero expenses -- can't divide, return zeros."""
        result = calculate_savings_metrics(
            savings_balance=Decimal("10000"),
            average_monthly_expenses=Decimal("0"),
        )
        assert result["months_covered"] == Decimal("0")
        assert result["paychecks_covered"] == Decimal("0")
        assert result["years_covered"] == Decimal("0")

    def test_expenses_none_returns_all_zeros(self):
        """None expenses -- return zeros."""
        result = calculate_savings_metrics(
            savings_balance=Decimal("10000"),
            average_monthly_expenses=None,
        )
        assert result["months_covered"] == Decimal("0")
        assert result["paychecks_covered"] == Decimal("0")
        assert result["years_covered"] == Decimal("0")

    def test_balance_zero_returns_all_zeros(self):
        """Zero balance -- nothing to cover expenses with."""
        result = calculate_savings_metrics(
            savings_balance=Decimal("0"),
            average_monthly_expenses=Decimal("2000"),
        )
        assert result["months_covered"] == Decimal("0.0")
        assert result["paychecks_covered"] == Decimal("0.0")
        assert result["years_covered"] == Decimal("0.0")


# ── TestCountPeriodsUntil ────────────────────────────────────────


class TestCountPeriodsUntil:
    """Count pay periods between today and a target date."""

    @patch("app.services.savings_goal_service.date")
    def test_counts_periods_from_today_to_target(self, mock_date):
        """Periods with start_date between today and target are counted."""
        mock_date.today.return_value = date(2026, 2, 1)
        mock_date.side_effect = lambda *args, **kw: date(*args, **kw)

        periods = [
            SimpleNamespace(start_date=date(2026, 1, 1)),   # before today
            SimpleNamespace(start_date=date(2026, 1, 15)),  # before today
            SimpleNamespace(start_date=date(2026, 2, 1)),   # >= today, <= target
            SimpleNamespace(start_date=date(2026, 2, 15)),  # >= today, <= target
            SimpleNamespace(start_date=date(2026, 3, 1)),   # >= today, <= target
        ]
        result = count_periods_until(date(2026, 3, 15), periods)
        assert result == 3

    def test_target_date_none_returns_none(self):
        """None target date -- return None."""
        result = count_periods_until(None, [])
        assert result is None

    @patch("app.services.savings_goal_service.date")
    def test_target_date_in_past_returns_zero(self, mock_date):
        """Target date before today -- no periods can qualify."""
        mock_date.today.return_value = date(2026, 2, 1)
        mock_date.side_effect = lambda *args, **kw: date(*args, **kw)

        periods = [
            SimpleNamespace(start_date=date(2026, 1, 1)),
            SimpleNamespace(start_date=date(2026, 1, 15)),
            SimpleNamespace(start_date=date(2026, 2, 1)),
            SimpleNamespace(start_date=date(2026, 2, 15)),
        ]
        result = count_periods_until(date(2026, 1, 15), periods)
        assert result == 0


# ── TestNegativeAndBoundaryPaths ───────────────────────────────


class TestNegativeAndBoundaryPaths:
    """Negative-path and boundary-condition tests for savings goal calculations.

    Covers: None inputs, negative expenses, very small expenses, exact-match
    balance/target, empty period lists, and one-penny gaps.
    """

    def test_required_contribution_none_balance(self):
        """None current_balance is treated as Decimal("0.00") by the None guard.

        A new account with no transactions has None balance. The goal
        calculator must not crash.
        """
        result = calculate_required_contribution(
            current_balance=None,
            target_amount=Decimal("5000"),
            remaining_periods=10,
        )
        # gap = 5000 - 0 = 5000, contribution = 5000 / 10 = 500.00
        assert result == Decimal("500.00")

    def test_required_contribution_none_target_amount(self):
        """None target_amount causes Decimal(str(None)) which raises InvalidOperation.

        A goal with no target set (user saved without filling it in) hits an
        unguarded code path: Decimal("None") is not a valid decimal string.
        """
        with pytest.raises(InvalidOperation):
            calculate_required_contribution(
                current_balance=Decimal("1000"),
                target_amount=None,
                remaining_periods=10,
            )

    def test_metrics_negative_expenses(self):
        """Negative expenses trigger the <= 0 guard and return all-zero metrics.

        Negative expenses are logically impossible but could result from a data
        bug. The metrics must not produce negative months or division errors.
        """
        result = calculate_savings_metrics(
            savings_balance=Decimal("10000"),
            average_monthly_expenses=Decimal("-100"),
        )
        assert result["months_covered"] == Decimal("0")
        assert result["paychecks_covered"] == Decimal("0")
        assert result["years_covered"] == Decimal("0")

    def test_metrics_very_small_expenses(self):
        """Very small expenses produce very large coverage numbers without overflow.

        Decimal arithmetic must not produce floating-point artifacts.
        """
        result = calculate_savings_metrics(
            savings_balance=Decimal("100000"),
            average_monthly_expenses=Decimal("0.01"),
        )
        # months = 100000 / 0.01 = 10000000.0
        assert result["months_covered"] == Decimal("10000000.0")
        # paychecks = 10000000.0 * 26 / 12 = 21666666.666... → 21666666.7
        assert result["paychecks_covered"] == Decimal("21666666.7")
        # years = 10000000.0 / 12 = 833333.333... → 833333.3
        assert result["years_covered"] == Decimal("833333.3")

    def test_required_contribution_exact_match(self):
        """When balance exactly equals target, contribution is Decimal("0.00").

        Must not return a tiny positive contribution due to Decimal comparison bugs.
        """
        result = calculate_required_contribution(
            current_balance=Decimal("5000"),
            target_amount=Decimal("5000"),
            remaining_periods=10,
        )
        assert result == Decimal("0.00")

    def test_count_periods_until_no_periods_in_range(self):
        """Empty periods list returns 0, not an exception.

        A user with no pay periods generated should see 0, not a crash.
        """
        result = count_periods_until(
            target_date=date(2030, 1, 1),
            periods=[],
        )
        assert result == 0

    def test_required_contribution_one_penny_gap(self):
        """One-penny gap (smallest possible) still produces correct contribution.

        The smallest possible gap must still produce a correct contribution,
        not be rounded to zero.
        """
        result = calculate_required_contribution(
            current_balance=Decimal("999.99"),
            target_amount=Decimal("1000.00"),
            remaining_periods=1,
        )
        # gap = 0.01, contribution = 0.01 / 1 = 0.01
        assert result == Decimal("0.01")


# ── TestResolveGoalTarget ──────────────────────────────────────


class TestResolveGoalTarget:
    """Tests for resolve_goal_target() -- the income-relative target resolver.

    All tests use ref_cache IDs (not hardcoded integers) for goal modes
    and income units.  Hand-calculated expected values are documented
    in comments.
    """

    def test_resolve_fixed_goal(self):
        """Fixed goal returns target_amount directly, unmodified."""
        fixed_id = ref_cache.goal_mode_id(GoalModeEnum.FIXED)
        result = resolve_goal_target(
            goal_mode_id=fixed_id,
            target_amount=Decimal("5000.00"),
            income_unit_id=None,
            income_multiplier=None,
            net_biweekly_pay=Decimal("2000.00"),
        )
        assert result == Decimal("5000.00")

    def test_resolve_fixed_goal_null_target_defensive(self):
        """Fixed goal with target_amount=None returns Decimal("0.00").

        This is a defensive case -- schema validation should prevent
        None target_amount for fixed goals, but the function must not
        crash if it reaches here.
        """
        fixed_id = ref_cache.goal_mode_id(GoalModeEnum.FIXED)
        result = resolve_goal_target(
            goal_mode_id=fixed_id,
            target_amount=None,
            income_unit_id=None,
            income_multiplier=None,
            net_biweekly_pay=Decimal("2000.00"),
        )
        assert result == Decimal("0.00")

    def test_resolve_income_relative_paychecks(self):
        """3 paychecks at $2,000/paycheck = $6,000.00.

        Hand-calculation: 3 * 2000 = 6000.
        """
        ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
        paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
        result = resolve_goal_target(
            goal_mode_id=ir_id,
            target_amount=None,
            income_unit_id=paychecks_id,
            income_multiplier=Decimal("3.00"),
            net_biweekly_pay=Decimal("2000.00"),
        )
        assert result == Decimal("6000.00")

    def test_resolve_income_relative_months(self):
        """3 months at $2,000/paycheck = exactly $13,000.00.

        Hand-calculation:
            monthly_net = 2000 * 26 / 12 = 4333.333...
            target = 3 * 4333.333... = 13000.00 (exact)

        This MUST be $13,000.00, NOT $12,999.99.  Premature
        quantization of the intermediate monthly_net would yield
        3 * 4333.33 = 12999.99.
        """
        ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
        months_id = ref_cache.income_unit_id(IncomeUnitEnum.MONTHS)
        result = resolve_goal_target(
            goal_mode_id=ir_id,
            target_amount=None,
            income_unit_id=months_id,
            income_multiplier=Decimal("3.00"),
            net_biweekly_pay=Decimal("2000.00"),
        )
        assert result == Decimal("13000.00"), (
            f"Expected exactly $13,000.00 but got {result} -- "
            "check for premature quantization of intermediate results"
        )

    def test_resolve_months_odd_net_pay(self):
        """3 months at $1,234.56/paycheck = $8,024.64.

        Hand-calculation:
            1234.56 * 26 = 32098.56
            32098.56 / 12 = 2674.88
            2674.88 * 3 = 8024.64
        """
        ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
        months_id = ref_cache.income_unit_id(IncomeUnitEnum.MONTHS)
        result = resolve_goal_target(
            goal_mode_id=ir_id,
            target_amount=None,
            income_unit_id=months_id,
            income_multiplier=Decimal("3.00"),
            net_biweekly_pay=Decimal("1234.56"),
        )
        assert result == Decimal("8024.64")

    def test_resolve_fractional_multiplier(self):
        """0.5 paychecks at $2,000/paycheck = $1,000.00.

        Hand-calculation: 0.5 * 2000 = 1000.
        """
        ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
        paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
        result = resolve_goal_target(
            goal_mode_id=ir_id,
            target_amount=None,
            income_unit_id=paychecks_id,
            income_multiplier=Decimal("0.50"),
            net_biweekly_pay=Decimal("2000.00"),
        )
        assert result == Decimal("1000.00")

    def test_resolve_no_salary_returns_zero(self):
        """Income-relative goal with net_biweekly_pay=$0 returns $0.00."""
        ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
        paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
        result = resolve_goal_target(
            goal_mode_id=ir_id,
            target_amount=None,
            income_unit_id=paychecks_id,
            income_multiplier=Decimal("3.00"),
            net_biweekly_pay=Decimal("0.00"),
        )
        assert result == Decimal("0.00")

    def test_resolve_income_relative_missing_fields_raises(self):
        """Income-relative mode with None income_unit_id raises ValueError."""
        ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
        with pytest.raises(ValueError, match="income_unit_id"):
            resolve_goal_target(
                goal_mode_id=ir_id,
                target_amount=None,
                income_unit_id=None,
                income_multiplier=Decimal("3.00"),
                net_biweekly_pay=Decimal("2000.00"),
            )

    def test_resolve_income_relative_missing_multiplier_raises(self):
        """Income-relative mode with None income_multiplier raises ValueError."""
        ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
        paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
        with pytest.raises(ValueError, match="income_multiplier"):
            resolve_goal_target(
                goal_mode_id=ir_id,
                target_amount=None,
                income_unit_id=paychecks_id,
                income_multiplier=None,
                net_biweekly_pay=Decimal("2000.00"),
            )

    def test_resolve_returns_decimal_type(self):
        """Return type is Decimal, never float."""
        ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
        paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
        result = resolve_goal_target(
            goal_mode_id=ir_id,
            target_amount=None,
            income_unit_id=paychecks_id,
            income_multiplier=Decimal("3.00"),
            net_biweekly_pay=Decimal("2000.00"),
        )
        assert isinstance(result, Decimal)

    def test_resolve_six_months_at_3500(self):
        """6 months at $3,500/paycheck = $45,500.00.

        Hand-calculation:
            3500 * 26 = 91000
            91000 / 12 = 7583.333...
            7583.333... * 6 = 45500.00 (exact)
        """
        ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
        months_id = ref_cache.income_unit_id(IncomeUnitEnum.MONTHS)
        result = resolve_goal_target(
            goal_mode_id=ir_id,
            target_amount=None,
            income_unit_id=months_id,
            income_multiplier=Decimal("6.00"),
            net_biweekly_pay=Decimal("3500.00"),
        )
        assert result == Decimal("45500.00")


# ── TestCalculateTrajectory ──────────────────────────────────────


class TestCalculateTrajectory:
    """Tests for calculate_trajectory() -- completion projection and pace.

    All tests mock date.today() to ensure deterministic results.
    Hand-calculated expected values are documented in comments.
    """

    @patch("app.services.savings_goal_service.date")
    def test_trajectory_on_track(self, mock_date):
        """C-5.15-1: On track when projected month matches target month.

        Setup: balance=$3,000, target=$6,000, $500/mo, target 6 months.
        Hand-calculation:
            remaining = 6000 - 3000 = 3000
            months = ceil(3000 / 500) = 6
            projected = 2026-04 + 6 months = 2026-10
            target = 2026-10-03
            pace = same month -> on_track
            required = ceil(3000 / 6) = 500.00
        """
        mock_date.today.return_value = date(2026, 4, 3)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        target_date = date(2026, 10, 3)
        result = calculate_trajectory(
            current_balance=Decimal("3000.00"),
            target_amount=Decimal("6000.00"),
            monthly_contribution=Decimal("500.00"),
            target_date=target_date,
        )
        assert result["months_to_goal"] == 6
        assert result["projected_completion_date"] == date(2026, 10, 3)
        assert result["pace"] == "on_track"
        assert result["required_monthly"] == Decimal("500.00")

    @patch("app.services.savings_goal_service.date")
    def test_trajectory_behind(self, mock_date):
        """C-5.15-2: Behind when projected completion is after target.

        Setup: balance=$1,000, target=$6,000, $500/mo, target 3 months.
        Hand-calculation:
            remaining = 6000 - 1000 = 5000
            months = ceil(5000 / 500) = 10
            projected = 2026-04 + 10 months = 2027-02
            target = 2026-07-03
            pace = 2027-02 > 2026-07 -> behind
            required = ceil(5000 / 3) = 1666.67
        """
        mock_date.today.return_value = date(2026, 4, 3)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        target_date = date(2026, 7, 3)
        result = calculate_trajectory(
            current_balance=Decimal("1000.00"),
            target_amount=Decimal("6000.00"),
            monthly_contribution=Decimal("500.00"),
            target_date=target_date,
        )
        assert result["months_to_goal"] == 10
        assert result["projected_completion_date"] == date(2027, 2, 3)
        assert result["pace"] == "behind"
        # required = ceil(5000 / 3) = 1666.666... -> 1666.67
        assert result["required_monthly"] == Decimal("1666.67")

    @patch("app.services.savings_goal_service.date")
    def test_trajectory_ahead(self, mock_date):
        """C-5.15-3: Ahead when projected completion is before target.

        Setup: balance=$5,000, target=$6,000, $500/mo, target 12 months.
        Hand-calculation:
            remaining = 6000 - 5000 = 1000
            months = ceil(1000 / 500) = 2
            projected = 2026-04 + 2 months = 2026-06
            target = 2027-04-03
            pace = 2026-06 < 2027-04 -> ahead
            required = ceil(1000 / 12) = 83.34
        """
        mock_date.today.return_value = date(2026, 4, 3)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        target_date = date(2027, 4, 3)
        result = calculate_trajectory(
            current_balance=Decimal("5000.00"),
            target_amount=Decimal("6000.00"),
            monthly_contribution=Decimal("500.00"),
            target_date=target_date,
        )
        assert result["months_to_goal"] == 2
        assert result["projected_completion_date"] == date(2026, 6, 3)
        assert result["pace"] == "ahead"
        # required = ceil(1000 / 12) = 83.333... -> 83.34
        assert result["required_monthly"] == Decimal("83.34")

    @patch("app.services.savings_goal_service.date")
    def test_trajectory_no_contribution(self, mock_date):
        """C-5.15-4: No contribution yields None trajectory values.

        With $0 monthly, no projected date or months can be computed.
        """
        mock_date.today.return_value = date(2026, 4, 3)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        result = calculate_trajectory(
            current_balance=Decimal("3000.00"),
            target_amount=Decimal("6000.00"),
            monthly_contribution=Decimal("0.00"),
            target_date=date(2027, 4, 3),
        )
        assert result["months_to_goal"] is None
        assert result["projected_completion_date"] is None
        assert result["pace"] == "behind"
        # required = ceil(3000 / 12) = 250.00
        assert result["required_monthly"] == Decimal("250.00")

    @patch("app.services.savings_goal_service.date")
    def test_trajectory_goal_already_met(self, mock_date):
        """C-5.15-5: Balance exceeds target -- goal already met.

        months_to_goal=0, projected_completion_date=today.
        """
        mock_date.today.return_value = date(2026, 4, 3)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        result = calculate_trajectory(
            current_balance=Decimal("7000.00"),
            target_amount=Decimal("6000.00"),
            monthly_contribution=Decimal("500.00"),
            target_date=date(2027, 4, 3),
        )
        assert result["months_to_goal"] == 0
        assert result["projected_completion_date"] == date(2026, 4, 3)
        assert result["pace"] == "ahead"
        assert result["required_monthly"] == Decimal("0.00")

    @patch("app.services.savings_goal_service.date")
    def test_trajectory_no_target_date(self, mock_date):
        """C-5.15-6: No target_date -- pace and required_monthly are None.

        months_to_goal and projected_completion_date are still computed.
        """
        mock_date.today.return_value = date(2026, 4, 3)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        # remaining = 6000 - 3000 = 3000, months = ceil(3000/500) = 6
        result = calculate_trajectory(
            current_balance=Decimal("3000.00"),
            target_amount=Decimal("6000.00"),
            monthly_contribution=Decimal("500.00"),
            target_date=None,
        )
        assert result["months_to_goal"] == 6
        assert result["projected_completion_date"] == date(2026, 10, 3)
        assert result["pace"] is None
        assert result["required_monthly"] is None

    @patch("app.services.savings_goal_service.date")
    def test_trajectory_exact_boundary(self, mock_date):
        """C-5.15-7: Exact division should not round up.

        remaining=$500, $500/mo -> exactly 1 month, not 0.something.
        """
        mock_date.today.return_value = date(2026, 4, 3)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        # remaining = 6000 - 5500 = 500, months = ceil(500/500) = 1
        result = calculate_trajectory(
            current_balance=Decimal("5500.00"),
            target_amount=Decimal("6000.00"),
            monthly_contribution=Decimal("500.00"),
        )
        assert result["months_to_goal"] == 1

    @patch("app.services.savings_goal_service.date")
    def test_trajectory_fractional_months_rounds_up(self, mock_date):
        """C-5.15-8: Fractional months round up via ROUND_CEILING.

        remaining=$3,000, $700/mo -> 3000/700 = 4.2857 -> ceil = 5.
        """
        mock_date.today.return_value = date(2026, 4, 3)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        # remaining = 6000 - 3000 = 3000, months = ceil(3000/700) = 5
        result = calculate_trajectory(
            current_balance=Decimal("3000.00"),
            target_amount=Decimal("6000.00"),
            monthly_contribution=Decimal("700.00"),
        )
        assert result["months_to_goal"] == 5
        assert result["projected_completion_date"] == date(2026, 9, 3)

    @patch("app.services.savings_goal_service.date")
    def test_required_monthly_rounds_up(self, mock_date):
        """C-5.15-9: required_monthly uses ROUND_CEILING.

        remaining=$5,000, 3 months -> 5000/3 = 1666.666... -> 1666.67.
        NOT 1666.66.
        """
        mock_date.today.return_value = date(2026, 4, 3)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        result = calculate_trajectory(
            current_balance=Decimal("1000.00"),
            target_amount=Decimal("6000.00"),
            monthly_contribution=Decimal("500.00"),
            target_date=date(2026, 7, 3),
        )
        # remaining = 5000, months_available = 3 (Jul - Apr = 3)
        # required = ceil(5000/3) = 1666.67
        assert result["required_monthly"] == Decimal("1666.67")

    @patch("app.services.savings_goal_service.date")
    def test_trajectory_target_date_past(self, mock_date):
        """C-5.15-10: Target date in the past yields no pace or required.

        A stale target date is not actionable -- pace and required_monthly
        are None.  months_to_goal is still computed normally.
        """
        mock_date.today.return_value = date(2026, 4, 3)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        # remaining = 6000 - 3000 = 3000, months = ceil(3000/500) = 6
        result = calculate_trajectory(
            current_balance=Decimal("3000.00"),
            target_amount=Decimal("6000.00"),
            monthly_contribution=Decimal("500.00"),
            target_date=date(2026, 4, 2),  # yesterday
        )
        assert result["months_to_goal"] == 6
        assert result["pace"] is None
        assert result["required_monthly"] is None

    @patch("app.services.savings_goal_service.date")
    def test_trajectory_zero_target(self, mock_date):
        """C-5.15-11: Zero target amount means goal is met immediately.

        remaining = 0 - balance = negative -> goal met.
        """
        mock_date.today.return_value = date(2026, 4, 3)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        result = calculate_trajectory(
            current_balance=Decimal("3000.00"),
            target_amount=Decimal("0.00"),
            monthly_contribution=Decimal("500.00"),
        )
        assert result["months_to_goal"] == 0
        assert result["projected_completion_date"] == date(2026, 4, 3)

    @patch("app.services.savings_goal_service.date")
    def test_trajectory_negative_contribution(self, mock_date):
        """C-5.15-12: Negative contribution treated as no contribution.

        A withdrawal (negative amount) cannot drive progress toward the goal.
        """
        mock_date.today.return_value = date(2026, 4, 3)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        result = calculate_trajectory(
            current_balance=Decimal("3000.00"),
            target_amount=Decimal("6000.00"),
            monthly_contribution=Decimal("-50.00"),
        )
        assert result["months_to_goal"] is None
        assert result["projected_completion_date"] is None

    @patch("app.services.savings_goal_service.date")
    def test_trajectory_very_small_contribution(self, mock_date):
        """C-5.15-13: Very small contribution produces large months without overflow.

        $1/mo toward $100,000 -> 100,000 months. Decimal must handle this
        without precision loss or performance issues.
        """
        mock_date.today.return_value = date(2026, 4, 3)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        # remaining = 100000 - 0 = 100000, months = ceil(100000/1) = 100000
        result = calculate_trajectory(
            current_balance=Decimal("0.00"),
            target_amount=Decimal("100000.00"),
            monthly_contribution=Decimal("1.00"),
        )
        assert result["months_to_goal"] == 100000
        assert result["projected_completion_date"] is not None

    @patch("app.services.savings_goal_service.date")
    def test_pace_same_month_is_on_track(self, mock_date):
        """C-5.15-18: Same year-month comparison yields on_track.

        Projected on the 3rd, target on the 28th of the same month.
        Same month = on_track, regardless of day.
        """
        mock_date.today.return_value = date(2026, 4, 3)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        # remaining = 6000 - 3000 = 3000, months = ceil(3000/500) = 6
        # projected = 2026-10-03
        result = calculate_trajectory(
            current_balance=Decimal("3000.00"),
            target_amount=Decimal("6000.00"),
            monthly_contribution=Decimal("500.00"),
            target_date=date(2026, 10, 28),
        )
        assert result["pace"] == "on_track"

    @patch("app.services.savings_goal_service.date")
    def test_trajectory_no_contribution_no_target_date(self, mock_date):
        """No contribution and no target date -- fully None trajectory.

        pace and required_monthly are None (no target date).
        months_to_goal and projected_completion_date are None (no contribution).
        """
        mock_date.today.return_value = date(2026, 4, 3)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        result = calculate_trajectory(
            current_balance=Decimal("3000.00"),
            target_amount=Decimal("6000.00"),
            monthly_contribution=Decimal("0.00"),
            target_date=None,
        )
        assert result["months_to_goal"] is None
        assert result["projected_completion_date"] is None
        assert result["pace"] is None
        assert result["required_monthly"] is None
