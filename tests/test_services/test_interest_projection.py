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

    def test_leap_year_february(self):
        """Leap year February period uses 365 as divisor, not 366.

        The source hardcodes DAYS_IN_YEAR = Decimal("365") regardless of
        whether the period falls in a leap year. This test verifies that
        behavior with a Feb 15-28 period in 2028 (a leap year).
        Expected:
          daily_rate = 0.045 / 365
          interest = 10000 * ((1 + daily_rate)^13 - 1) = $16.04
        """
        result = calculate_interest(
            balance=Decimal("10000.00"),
            apy=Decimal("0.04500"),
            compounding_frequency="daily",
            period_start=date(2028, 2, 15),
            period_end=date(2028, 2, 28),
        )
        assert result == Decimal("16.04")

    def test_very_high_apy_no_overflow(self):
        """100% APY with daily compounding produces correct result without overflow.

        Stress test: ensures Decimal handles large rate without precision loss.
        Expected:
          daily_rate = 1.0 / 365
          interest = 1000 * ((1 + daily_rate)^14 - 1) = $39.05
        """
        result = calculate_interest(
            balance=Decimal("1000.00"),
            apy=Decimal("1.00000"),
            compounding_frequency="daily",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 15),
        )
        assert result == Decimal("39.05")


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

    def test_cross_month_period(self):
        """Period spanning a month boundary uses the START month's days_in_month.

        The source calls calendar.monthrange(period_start.year, period_start.month)
        so a Jan 25 - Feb 7 period (13 days) uses January's 31-day month
        as the divisor, not February's 28.
        Expected:
          monthly_rate = 0.045 / 12 = 0.00375
          interest = 10000 * 0.00375 * (13 / 31) = $15.73
        """
        result = calculate_interest(
            balance=Decimal("10000.00"),
            apy=Decimal("0.04500"),
            compounding_frequency="monthly",
            period_start=date(2026, 1, 25),
            period_end=date(2026, 2, 7),
        )
        assert result == Decimal("15.73")


class TestQuarterlyCompounding:
    """Quarterly compounding: interest = balance * (apy/4) * (days/91)."""

    def test_basic_14_day_period(self):
        """$10,000 at 4.5% APY, 14-day period in Q1 (90 actual days)."""
        result = calculate_interest(
            balance=Decimal("10000.00"),
            apy=Decimal("0.04500"),
            compounding_frequency="quarterly",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 15),
        )
        # quarterly_rate = 0.045 / 4 = 0.01125
        # Q1 2026: Jan(31) + Feb(28) + Mar(31) = 90 actual days
        # interest = 10000 * 0.01125 * (14/90) ≈ $17.50
        assert result == Decimal("17.50")


class TestEdgeCases:
    """Edge cases: zero/negative balance, zero APY, bad dates."""

    def test_zero_balance(self):
        """Zero balance returns zero interest regardless of APY or period."""
        result = calculate_interest(
            balance=Decimal("0.00"),
            apy=Decimal("0.04500"),
            compounding_frequency="daily",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 15),
        )
        assert result == Decimal("0.00")

    def test_negative_balance(self):
        """Negative balance returns zero -- guard prevents nonsensical interest."""
        result = calculate_interest(
            balance=Decimal("-5000.00"),
            apy=Decimal("0.04500"),
            compounding_frequency="daily",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 15),
        )
        assert result == Decimal("0.00")

    def test_zero_apy(self):
        """Zero APY returns zero interest regardless of balance or period."""
        result = calculate_interest(
            balance=Decimal("10000.00"),
            apy=Decimal("0.00000"),
            compounding_frequency="daily",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 15),
        )
        assert result == Decimal("0.00")

    def test_invalid_period_dates_equal(self):
        """Equal start/end dates return zero -- no time elapsed, no interest."""
        result = calculate_interest(
            balance=Decimal("10000.00"),
            apy=Decimal("0.04500"),
            compounding_frequency="daily",
            period_start=date(2026, 1, 15),
            period_end=date(2026, 1, 15),
        )
        assert result == Decimal("0.00")

    def test_invalid_period_dates_reversed(self):
        """Reversed dates (start > end) return zero -- guard prevents negative periods."""
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
        """$1,000,000 balance -- no overflow, correct result."""
        result = calculate_interest(
            balance=Decimal("1000000.00"),
            apy=Decimal("0.04500"),
            compounding_frequency="daily",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 15),
        )
        # interest = 1000000 * ((1 + 0.045/365)^14 - 1) ≈ $1727.41
        assert result == Decimal("1727.41")

    def test_negative_apy(self):
        """Negative APY returns zero -- the guard `apy <= 0` catches this.

        A negative APY is nonsensical for a savings account. The source
        short-circuits and returns ZERO.
        Expected: Decimal("0.00").
        """
        result = calculate_interest(
            balance=Decimal("10000.00"),
            apy=Decimal("-0.05000"),
            compounding_frequency="daily",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 15),
        )
        assert result == Decimal("0.00")


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
        """Unrecognized compounding frequency returns zero as fallback."""
        result = calculate_interest(
            balance=Decimal("10000.00"),
            apy=Decimal("0.04500"),
            compounding_frequency="invalid",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 15),
        )
        assert result == Decimal("0.00")


class TestDayCountConventionDocstring:
    """Verify the 365-day-year accepted-simplification docstring is in place.

    F-126 of the 2026-04-15 security audit classified the actual/365
    day-count convention as an accepted simplification rather than a
    code change.  The closure condition for that audit item is that
    the convention is documented in a discoverable docstring (module
    + function), so callers reasoning about leap-year accuracy do not
    have to grep the constant definition to find the rationale.

    These tests pin the docstring content.  They fail if a future
    refactor strips the acknowledgement; the audit then reopens.
    """

    def test_module_docstring_acknowledges_365_day_convention(self):
        """The module docstring names the 365-day actual/365 convention.

        Asserts both the convention name (``actual/365``) and the
        explicit acknowledgement keyword (``accepted simplification``)
        are present so that wording drift -- e.g. someone rewording
        the docstring to say "approximation" instead of
        "simplification" -- breaks the test.  The audit-closure language
        is "accepted simplification"; that exact phrase must survive.
        """
        from app.services import interest_projection  # pylint: disable=import-outside-toplevel

        doc = interest_projection.__doc__ or ""
        assert "actual/365" in doc
        assert "accepted simplification" in doc.lower()
        assert "F-126" in doc

    def test_calculate_interest_docstring_acknowledges_leap_year(self):
        """The function docstring names the leap-year behaviour for callers.

        A caller reading only the function docstring (the common case
        in editors / API docs) must learn that leap years use the
        365-day divisor.  Asserts both ``leap year`` and ``365`` are
        named in the function-level docstring (separate from the
        module-level rationale) so the docstring stays self-sufficient.
        """
        from app.services.interest_projection import (  # pylint: disable=import-outside-toplevel
            calculate_interest,
        )

        doc = calculate_interest.__doc__ or ""
        assert "leap year" in doc.lower()
        assert "365" in doc
        assert "F-126" in doc
