"""
Shekel Budget App — Unit Tests for Savings Goal Service

Tests the pure calculation functions in savings_goal_service.py:
calculate_required_contribution, calculate_savings_metrics, and
count_periods_until.
"""

from datetime import date
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.savings_goal_service import (
    calculate_required_contribution,
    calculate_savings_metrics,
    count_periods_until,
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
        """Balance already exceeds target — no contribution needed."""
        result = calculate_required_contribution(
            current_balance=Decimal("5000"),
            target_amount=Decimal("3000"),
            remaining_periods=5,
        )
        assert result == Decimal("0.00")

    def test_remaining_periods_zero_returns_none(self):
        """Zero remaining periods — past due, return None."""
        result = calculate_required_contribution(
            current_balance=Decimal("1000"),
            target_amount=Decimal("2000"),
            remaining_periods=0,
        )
        assert result is None

    def test_remaining_periods_negative_returns_none(self):
        """Negative remaining periods — past due, return None."""
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
        """Zero expenses — can't divide, return zeros."""
        result = calculate_savings_metrics(
            savings_balance=Decimal("10000"),
            average_monthly_expenses=Decimal("0"),
        )
        assert result["months_covered"] == Decimal("0")
        assert result["paychecks_covered"] == Decimal("0")
        assert result["years_covered"] == Decimal("0")

    def test_expenses_none_returns_all_zeros(self):
        """None expenses — return zeros."""
        result = calculate_savings_metrics(
            savings_balance=Decimal("10000"),
            average_monthly_expenses=None,
        )
        assert result["months_covered"] == Decimal("0")
        assert result["paychecks_covered"] == Decimal("0")
        assert result["years_covered"] == Decimal("0")

    def test_balance_zero_returns_all_zeros(self):
        """Zero balance — nothing to cover expenses with."""
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
        """None target date — return None."""
        result = count_periods_until(None, [])
        assert result is None

    @patch("app.services.savings_goal_service.date")
    def test_target_date_in_past_returns_zero(self, mock_date):
        """Target date before today — no periods can qualify."""
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
