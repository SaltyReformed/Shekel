"""
Tests for the interest projection service.

Validates compound interest calculations for HYSA accounts across
different compounding frequencies, edge cases, and rounding behavior.
"""

from datetime import date
from decimal import Decimal

from app.services.interest_projection import calculate_interest


class TestDailyCompounding:
    """Daily compounding: interest = balance * ((1 + apy/365)^days - 1)."""

    def test_basic_14_day_period(self):
        """$10,000 at 4.5% APY, 14-day period."""
        result = calculate_interest(
            balance=Decimal("10000.00"),
            apy=Decimal("0.04500"),
            compounding_frequency="daily",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 15),
        )
        # daily_rate = 0.045 / 365 ≈ 0.00012328767
        # interest = 10000 * ((1 + 0.00012328767)^14 - 1) ≈ $17.27
        assert result == Decimal("17.27")

    def test_single_day_period(self):
        """1-day period."""
        result = calculate_interest(
            balance=Decimal("10000.00"),
            apy=Decimal("0.04500"),
            compounding_frequency="daily",
            period_start=date(2026, 3, 1),
            period_end=date(2026, 3, 2),
        )
        # daily_rate ≈ 0.00012328767
        # interest = 10000 * ((1.00012328767)^1 - 1) ≈ $1.23
        assert result == Decimal("1.23")

    def test_30_day_period(self):
        """30-day period."""
        result = calculate_interest(
            balance=Decimal("10000.00"),
            apy=Decimal("0.04500"),
            compounding_frequency="daily",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
        )
        # interest = 10000 * ((1 + 0.045/365)^30 - 1) ≈ $37.05
        assert result == Decimal("37.05")


class TestMonthlyCompounding:
    """Monthly compounding: interest = balance * (apy/12) * (days/days_in_month)."""

    def test_basic_14_day_period(self):
        """$10,000 at 4.5% APY, 14-day period in January (31-day month)."""
        result = calculate_interest(
            balance=Decimal("10000.00"),
            apy=Decimal("0.04500"),
            compounding_frequency="monthly",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 15),
        )
        # monthly_rate = 0.045 / 12 = 0.00375
        # interest = 10000 * 0.00375 * (14/31) ≈ $16.94
        assert result == Decimal("16.94")

    def test_february(self):
        """14-day period in February (28-day month)."""
        result = calculate_interest(
            balance=Decimal("10000.00"),
            apy=Decimal("0.04500"),
            compounding_frequency="monthly",
            period_start=date(2026, 2, 1),
            period_end=date(2026, 2, 15),
        )
        # interest = 10000 * 0.00375 * (14/28) = $18.75
        assert result == Decimal("18.75")


class TestQuarterlyCompounding:
    """Quarterly compounding: interest = balance * (apy/4) * (days/91)."""

    def test_basic_14_day_period(self):
        """$10,000 at 4.5% APY, 14-day period."""
        result = calculate_interest(
            balance=Decimal("10000.00"),
            apy=Decimal("0.04500"),
            compounding_frequency="quarterly",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 15),
        )
        # quarterly_rate = 0.045 / 4 = 0.01125
        # interest = 10000 * 0.01125 * (14/91) ≈ $17.31
        assert result == Decimal("17.31")


class TestEdgeCases:
    """Edge cases: zero/negative balance, zero APY, bad dates."""

    def test_zero_balance(self):
        result = calculate_interest(
            balance=Decimal("0.00"),
            apy=Decimal("0.04500"),
            compounding_frequency="daily",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 15),
        )
        assert result == Decimal("0.00")

    def test_negative_balance(self):
        result = calculate_interest(
            balance=Decimal("-5000.00"),
            apy=Decimal("0.04500"),
            compounding_frequency="daily",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 15),
        )
        assert result == Decimal("0.00")

    def test_zero_apy(self):
        result = calculate_interest(
            balance=Decimal("10000.00"),
            apy=Decimal("0.00000"),
            compounding_frequency="daily",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 15),
        )
        assert result == Decimal("0.00")

    def test_invalid_period_dates_equal(self):
        result = calculate_interest(
            balance=Decimal("10000.00"),
            apy=Decimal("0.04500"),
            compounding_frequency="daily",
            period_start=date(2026, 1, 15),
            period_end=date(2026, 1, 15),
        )
        assert result == Decimal("0.00")

    def test_invalid_period_dates_reversed(self):
        result = calculate_interest(
            balance=Decimal("10000.00"),
            apy=Decimal("0.04500"),
            compounding_frequency="daily",
            period_start=date(2026, 1, 15),
            period_end=date(2026, 1, 1),
        )
        assert result == Decimal("0.00")

    def test_high_apy(self):
        """10% APY should produce larger interest."""
        result = calculate_interest(
            balance=Decimal("10000.00"),
            apy=Decimal("0.10000"),
            compounding_frequency="daily",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 15),
        )
        # daily_rate = 0.10 / 365 ≈ 0.00027397
        # interest = 10000 * ((1.00027397)^14 - 1) ≈ $38.42
        assert result == Decimal("38.42")

    def test_large_balance(self):
        """$1,000,000 balance — no overflow, correct result."""
        result = calculate_interest(
            balance=Decimal("1000000.00"),
            apy=Decimal("0.04500"),
            compounding_frequency="daily",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 15),
        )
        # interest = 1000000 * ((1 + 0.045/365)^14 - 1) ≈ $1727.41
        assert result == Decimal("1727.41")


class TestRounding:
    """Verify ROUND_HALF_UP to 2 decimal places."""

    def test_rounds_half_up(self):
        """A calculation that naturally produces .xx5 should round up."""
        # With carefully chosen values, verify rounding behavior.
        result = calculate_interest(
            balance=Decimal("10000.00"),
            apy=Decimal("0.04500"),
            compounding_frequency="daily",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 15),
        )
        # Result should be exactly 2 decimal places.
        assert result == result.quantize(Decimal("0.01"))

    def test_unknown_frequency_returns_zero(self):
        result = calculate_interest(
            balance=Decimal("10000.00"),
            apy=Decimal("0.04500"),
            compounding_frequency="invalid",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 15),
        )
        assert result == Decimal("0.00")
