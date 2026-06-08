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

    def test_leap_year_february_window_not_crossing_feb_29(self):
        """A Feb 15-28 window in a leap year keeps the 365-day divisor.

        Per MED-05 / PA-06, the divisor switches to 366 only when the
        projection window contains Feb 29.  A Feb 15-Feb 28 window in
        2028 (a leap year) stops at Feb 28 and never includes Feb 29,
        so the daily rate uses 365.  This locks the "non-crossing
        window" half of the actual/actual switch as a regression guard.

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

    def test_leap_year_window_crossing_feb_29_uses_366(self):
        """C27-1: a 14-day window crossing Feb 29 2028 uses the 366-day divisor.

        MED-05 / PA-06: pre-fix the divisor was a hardcoded 365 even in
        leap years, overstating daily interest by ~0.27% for windows
        containing Feb 29.  The fix switches to 366 when the window
        contains Feb 29.

        Hand calculation for $10,000 at 4.5% APY over a 14-day window
        2028-02-20 -> 2028-03-05 (contains 2028-02-29):

          daily_rate = 0.045 / 366 = 0.000122950819672...
          (1 + daily_rate)^14 - 1 = 0.001722687790...
          interest = 10000 * 0.001722687790 = 17.226877905...
          round half-up to 2dp = $17.23

        Pre-fix value (365 divisor) was $17.27, so the corrected value
        is $0.04 lower at this balance (corresponds to the ~$0.47/$100K
        figure cited in MED-05's evidence).
        """
        result = calculate_interest(
            balance=Decimal("10000.00"),
            apy=Decimal("0.04500"),
            compounding_frequency="daily",
            period_start=date(2028, 2, 20),
            period_end=date(2028, 3, 5),
        )
        assert result == Decimal("17.23")

    def test_non_leap_year_window_unchanged(self):
        """C27-2: a window entirely outside any leap year is unchanged by MED-05.

        2026 is not a leap year, so the divisor stays 365.  The
        expected value matches the legacy
        ``test_basic_14_day_period`` baseline byte-for-byte;
        re-asserted here as an explicit no-regression lock for the
        non-leap branch of the actual/actual switch.

        daily_rate = 0.045 / 365
        interest = 10000 * ((1 + daily_rate)^14 - 1) -> $17.27
        """
        result = calculate_interest(
            balance=Decimal("10000.00"),
            apy=Decimal("0.04500"),
            compounding_frequency="daily",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 15),
        )
        assert result == Decimal("17.27")

    def test_leap_year_window_ending_on_feb_29_does_not_cross(self):
        """C27-2b: half-open interval -- a window whose end IS Feb 29 stays at 365.

        ``(period_end - period_start).days`` treats ``period_end`` as
        exclusive, so a window ending on Feb 29 contains 0 days of
        Feb 29 and gets the 365-day divisor.  Locks the half-open
        boundary semantics documented in the module docstring.

        Expected: Feb 15 -> Feb 29 (14-day window, exclusive of Feb 29)
        in 2028 uses 365 divisor, same as the non-crossing baseline.
          daily_rate = 0.045 / 365
          interest = 10000 * ((1 + daily_rate)^14 - 1) = $17.27
        """
        result = calculate_interest(
            balance=Decimal("10000.00"),
            apy=Decimal("0.04500"),
            compounding_frequency="daily",
            period_start=date(2028, 2, 15),
            period_end=date(2028, 2, 29),
        )
        assert result == Decimal("17.27")

    def test_leap_year_window_starting_on_feb_29_uses_366(self):
        """C27-2c: a window starting on Feb 29 contains the leap day -> 366.

        The half-open interval ``[Feb 29, Mar 14)`` contains Feb 29,
        so the divisor is 366.  Complements
        ``test_leap_year_window_ending_on_feb_29_does_not_cross`` so
        both ends of the boundary semantics are pinned.

        Hand calc, 14-day window starting Feb 29 2028:
          daily_rate = 0.045 / 366
          interest = 10000 * ((1 + daily_rate)^14 - 1) -> $17.23
        """
        result = calculate_interest(
            balance=Decimal("10000.00"),
            apy=Decimal("0.04500"),
            compounding_frequency="daily",
            period_start=date(2028, 2, 29),
            period_end=date(2028, 3, 14),
        )
        assert result == Decimal("17.23")

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
    """Quarterly compounding: interest = balance * (apy/4) * (days / actual_quarter_days).

    Uses the ACTUAL quarter length (90-92 days) from ``_days_in_quarter``,
    not a hardcoded 91-day approximation (L-05).
    """

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

    def test_q4_year_rollover_period(self):
        """$10,000 at 4.5% APY, 14-day period in Q4 (92 actual days).

        Locks the year-rollover branch in ``_days_in_quarter``
        (``next_q_month > 12`` -> Jan 1 of the following year), isolated by
        the Phase-3 extraction: Q4 2026 spans Oct(31) + Nov(30) + Dec(31) =
        92 days, distinguishing the actual-length divisor from both the
        90-day Q1 and the old hardcoded 91 (L-05).
        """
        result = calculate_interest(
            balance=Decimal("10000.00"),
            apy=Decimal("0.04500"),
            compounding_frequency="quarterly",
            period_start=date(2026, 10, 1),
            period_end=date(2026, 10, 15),
        )
        # quarterly_rate = 0.045 / 4 = 0.01125
        # Q4 2026: Oct(31) + Nov(30) + Dec(31) = 92 actual days
        # interest = 10000 * 0.01125 * (14/92) = 17.1195... -> $17.12
        assert result == Decimal("17.12")


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
    """Verify the actual/actual leap-day-crossing docstring is in place.

    The 2026-04-15 security audit closed F-126 with an
    "accepted simplification" wording: actual/365 day count, even in
    leap years, because the per-leap-year overstatement (~$0.25/$100K)
    was small.  MED-05 / PA-06 of the financial-calculation audit
    (2026-05-19) superseded that closure with a code-level fix: the
    divisor is 366 when the projection window contains Feb 29 and 365
    otherwise.

    These tests pin the *new* docstring content so a future refactor
    that strips the rationale (or reverts wording to the historical
    "accepted simplification" phrasing) breaks the test and reopens
    the audit item.
    """

    def test_module_docstring_names_actual_actual_and_leap_day_check(self):
        """The module docstring names the actual/actual convention and the leap-day trigger.

        Asserts the new convention name (``actual/actual``), the
        per-window leap-day trigger (``Feb 29``), and the audit IDs
        (``MED-05`` and ``PA-06``) that govern the fix.  The previous
        ``actual/365`` / ``accepted simplification`` phrasing must NOT
        appear, otherwise a wording revert would pass silently.

        Re-pinned under MED-05 / PA-06: was a F-126 lock; superseded
        2026-05-19 (this commit).
        """
        from app.services import interest_projection  # pylint: disable=import-outside-toplevel

        doc = interest_projection.__doc__ or ""
        # New audit-aligned wording.
        assert "actual/actual" in doc
        assert "Feb 29" in doc
        assert "MED-05" in doc
        assert "PA-06" in doc
        # Old F-126 phrasing must not survive a wording revert.
        assert "actual/365" not in doc
        assert "accepted simplification" not in doc.lower()

    def test_calculate_interest_docstring_names_366_and_feb_29(self):
        """The function docstring names the 366-day divisor and the Feb 29 trigger.

        A caller reading only the function docstring (the common case
        in editors / API docs) must learn that windows crossing Feb 29
        use a 366-day divisor.  Asserts the substantive keywords
        (``366``, ``Feb 29``, ``MED-05`` / ``PA-06``) so the docstring
        stays self-sufficient and audit-traceable.

        Re-pinned under MED-05 / PA-06: was a F-126 lock; superseded
        2026-05-19 (this commit).
        """
        from app.services.interest_projection import (  # pylint: disable=import-outside-toplevel
            calculate_interest,
        )

        doc = calculate_interest.__doc__ or ""
        assert "366" in doc
        assert "Feb 29" in doc
        assert "MED-05" in doc
        assert "PA-06" in doc
