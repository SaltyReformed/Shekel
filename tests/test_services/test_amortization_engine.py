"""
Tests for the amortization engine service.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.services.amortization_engine import (
    AmortizationRow,
    AmortizationSummary,
    calculate_monthly_payment,
    calculate_payoff_by_date,
    calculate_remaining_months,
    calculate_summary,
    generate_schedule,
)


class TestCalculateMonthlyPayment:
    """Tests for the monthly payment formula."""

    def test_basic_mortgage(self):
        """$200,000 at 6.5%, 30 years → ~$1,264.14."""
        payment = calculate_monthly_payment(
            Decimal("200000"), Decimal("0.065"), 360,
        )
        assert payment == Decimal("1264.14")

    def test_zero_rate(self):
        """0% rate → principal / months."""
        payment = calculate_monthly_payment(
            Decimal("12000"), Decimal("0"), 12,
        )
        assert payment == Decimal("1000.00")

    def test_zero_principal(self):
        """$0 principal → $0 payment."""
        payment = calculate_monthly_payment(
            Decimal("0"), Decimal("0.05"), 360,
        )
        assert payment == Decimal("0.00")

    def test_negative_principal(self):
        """Negative principal → $0 payment."""
        payment = calculate_monthly_payment(
            Decimal("-1000"), Decimal("0.05"), 360,
        )
        assert payment == Decimal("0.00")

    def test_zero_remaining_months(self):
        """0 months → $0 payment."""
        payment = calculate_monthly_payment(
            Decimal("100000"), Decimal("0.05"), 0,
        )
        assert payment == Decimal("0.00")

    def test_short_term_auto_loan(self):
        """5-year auto loan at 5%."""
        payment = calculate_monthly_payment(
            Decimal("25000"), Decimal("0.05"), 60,
        )
        assert payment == Decimal("471.78")

    def test_one_month(self):
        """1 month → principal + one month interest."""
        payment = calculate_monthly_payment(
            Decimal("10000"), Decimal("0.06"), 1,
        )
        # Should be ~$10,050 (principal + 0.5% interest)
        assert payment == Decimal("10050.00")


class TestGenerateSchedule:
    """Tests for schedule generation."""

    def test_basic_schedule(self):
        """Full schedule with rows summing correctly."""
        schedule = generate_schedule(
            Decimal("100000"), Decimal("0.06"), 360,
            origination_date=date(2026, 1, 1), payment_day=1,
        )
        assert len(schedule) == 360
        assert schedule[0].month == 1
        assert schedule[-1].remaining_balance == Decimal("0.00")

    def test_extra_payment_shortens_schedule(self):
        """Extra $200/mo → shorter schedule."""
        standard = generate_schedule(
            Decimal("100000"), Decimal("0.06"), 360,
            origination_date=date(2026, 1, 1), payment_day=1,
        )
        accelerated = generate_schedule(
            Decimal("100000"), Decimal("0.06"), 360,
            extra_monthly=Decimal("200"),
            origination_date=date(2026, 1, 1), payment_day=1,
        )
        assert len(accelerated) < len(standard)

    def test_extra_exceeds_balance(self):
        """Extra payment capped at remaining balance."""
        schedule = generate_schedule(
            Decimal("1000"), Decimal("0.06"), 360,
            extra_monthly=Decimal("5000"),
            origination_date=date(2026, 1, 1), payment_day=1,
        )
        # Should pay off in 1 month.
        assert len(schedule) == 1
        assert schedule[0].remaining_balance == Decimal("0.00")

    def test_zero_rate_schedule(self):
        """Zero rate → equal principal portions."""
        schedule = generate_schedule(
            Decimal("12000"), Decimal("0"), 12,
            origination_date=date(2026, 1, 1), payment_day=15,
        )
        assert len(schedule) == 12
        for row in schedule:
            assert row.interest == Decimal("0.00")
            assert row.principal == Decimal("1000.00")
        assert schedule[-1].remaining_balance == Decimal("0.00")

    def test_final_balance_zero(self):
        """Last row has remaining_balance = 0."""
        schedule = generate_schedule(
            Decimal("50000"), Decimal("0.05"), 60,
            origination_date=date(2026, 1, 1), payment_day=1,
        )
        assert schedule[-1].remaining_balance == Decimal("0.00")

    def test_total_principal_equals_original(self):
        """Sum of principal portions = original principal."""
        principal = Decimal("50000")
        schedule = generate_schedule(
            principal, Decimal("0.05"), 60,
            origination_date=date(2026, 1, 1), payment_day=1,
        )
        total_principal = sum(r.principal + r.extra_payment for r in schedule)
        assert total_principal == principal

    def test_empty_schedule_zero_principal(self):
        """Zero principal → empty schedule."""
        schedule = generate_schedule(
            Decimal("0"), Decimal("0.05"), 360,
        )
        assert schedule == []

    def test_empty_schedule_zero_months(self):
        """Zero months → empty schedule."""
        schedule = generate_schedule(
            Decimal("100000"), Decimal("0.05"), 0,
        )
        assert schedule == []

    def test_payment_dates_correct(self):
        """Payment dates advance monthly with correct day."""
        schedule = generate_schedule(
            Decimal("10000"), Decimal("0.06"), 6,
            origination_date=date(2026, 1, 1), payment_day=15,
        )
        expected_dates = [
            date(2026, 2, 15),
            date(2026, 3, 15),
            date(2026, 4, 15),
            date(2026, 5, 15),
            date(2026, 6, 15),
            date(2026, 7, 15),
        ]
        actual_dates = [r.payment_date for r in schedule]
        assert actual_dates == expected_dates

    def test_rounding_consistency(self):
        """All amounts rounded to 2 decimal places."""
        schedule = generate_schedule(
            Decimal("200000"), Decimal("0.065"), 360,
            origination_date=date(2026, 1, 1), payment_day=1,
        )
        for row in schedule:
            assert row.payment == row.payment.quantize(Decimal("0.01"))
            assert row.principal == row.principal.quantize(Decimal("0.01"))
            assert row.interest == row.interest.quantize(Decimal("0.01"))
            assert row.extra_payment == row.extra_payment.quantize(Decimal("0.01"))
            assert row.remaining_balance == row.remaining_balance.quantize(Decimal("0.01"))

    def test_single_month_remaining(self):
        """1 month left → final payment = principal + interest."""
        schedule = generate_schedule(
            Decimal("5000"), Decimal("0.06"), 1,
            origination_date=date(2026, 1, 1), payment_day=1,
        )
        assert len(schedule) == 1
        assert schedule[0].remaining_balance == Decimal("0.00")
        assert schedule[0].principal == Decimal("5000.00")
        assert schedule[0].interest == Decimal("25.00")  # 5000 * 0.06/12

    def test_auto_loan_schedule(self):
        """5-year $25,000 at 5%."""
        schedule = generate_schedule(
            Decimal("25000"), Decimal("0.05"), 60,
            origination_date=date(2026, 1, 1), payment_day=1,
        )
        assert len(schedule) == 60
        assert schedule[-1].remaining_balance == Decimal("0.00")

    def test_large_extra_payment(self):
        """Extra > monthly payment → verify early payoff."""
        schedule = generate_schedule(
            Decimal("100000"), Decimal("0.06"), 360,
            extra_monthly=Decimal("2000"),
            origination_date=date(2026, 1, 1), payment_day=1,
        )
        # With ~$600 standard + $2000 extra, should pay off much sooner.
        assert len(schedule) < 50


class TestCalculateSummary:
    """Tests for the summary calculation."""

    def test_basic_summary(self):
        """Summary metrics match schedule."""
        summary = calculate_summary(
            current_principal=Decimal("200000"),
            annual_rate=Decimal("0.065"),
            remaining_months=360,
            origination_date=date(2026, 1, 1),
            payment_day=1,
            term_months=360,
        )
        assert summary.monthly_payment == Decimal("1264.14")
        assert summary.months_saved == 0
        assert summary.interest_saved == Decimal("0.00")

    def test_summary_with_extra(self):
        """Extra $200/mo on $200k at 6.5% over 30 years.

        REGRESSION LOCK: months_saved and interest_saved values
        were derived from running generate_schedule (360-month
        independent computation is impractical to hand-verify).
        The spot-checks below independently verify the first 3
        months of the accelerated schedule to anchor trust in
        the regression values.

        months_saved = 360 - 250 = 110
        interest_saved = 255085.82 - 165011.16 = 90074.66
        """
        summary = calculate_summary(
            current_principal=Decimal("200000"),
            annual_rate=Decimal("0.065"),
            remaining_months=360,
            origination_date=date(2026, 1, 1),
            payment_day=1,
            term_months=360,
            extra_monthly=Decimal("200"),
        )
        assert summary.monthly_payment == Decimal("1264.14"), (
            f"Expected payment 1264.14, got {summary.monthly_payment}"
        )
        assert summary.months_saved == 110, (
            f"Expected 110 months saved, got {summary.months_saved}"
        )
        assert summary.interest_saved == Decimal("90074.66"), (
            f"Expected interest saved 90074.66, "
            f"got {summary.interest_saved}"
        )
        assert summary.payoff_date_with_extra < summary.payoff_date

        # --- Independent spot-checks of accelerated schedule ---
        accel = generate_schedule(
            Decimal("200000"), Decimal("0.065"), 360,
            extra_monthly=Decimal("200"),
            origination_date=date(2026, 1, 1), payment_day=1,
        )

        # Month 1: interest = (200000 * 0.065/12).Q = 1083.33
        # principal = 1264.14 - 1083.33 = 180.81
        # extra = 200, balance = 200000 - 180.81 - 200 = 199619.19
        assert accel[0].interest == Decimal("1083.33"), (
            f"Month 1 interest: expected 1083.33, "
            f"got {accel[0].interest}"
        )
        assert accel[0].principal == Decimal("180.81"), (
            f"Month 1 principal: expected 180.81, "
            f"got {accel[0].principal}"
        )
        assert accel[0].remaining_balance == Decimal("199619.19"), (
            f"Month 1 balance: expected 199619.19, "
            f"got {accel[0].remaining_balance}"
        )

        # Month 2: interest = (199619.19 * 0.065/12).Q = 1081.27
        # principal = 1264.14 - 1081.27 = 182.87
        # balance = 199619.19 - 182.87 - 200 = 199236.32
        assert accel[1].interest == Decimal("1081.27"), (
            f"Month 2 interest: expected 1081.27, "
            f"got {accel[1].interest}"
        )
        assert accel[1].remaining_balance == Decimal("199236.32"), (
            f"Month 2 balance: expected 199236.32, "
            f"got {accel[1].remaining_balance}"
        )

        # Month 3: interest = (199236.32 * 0.065/12).Q = 1079.20
        # principal = 1264.14 - 1079.20 = 184.94
        # balance = 199236.32 - 184.94 - 200 = 198851.38
        assert accel[2].interest == Decimal("1079.20"), (
            f"Month 3 interest: expected 1079.20, "
            f"got {accel[2].interest}"
        )
        assert accel[2].remaining_balance == Decimal("198851.38"), (
            f"Month 3 balance: expected 198851.38, "
            f"got {accel[2].remaining_balance}"
        )

    def test_summary_no_extra(self):
        """No extra → months_saved = 0, interest_saved = 0."""
        summary = calculate_summary(
            current_principal=Decimal("100000"),
            annual_rate=Decimal("0.05"),
            remaining_months=360,
            origination_date=date(2026, 1, 1),
            payment_day=1,
            term_months=360,
            extra_monthly=Decimal("0"),
        )
        assert summary.months_saved == 0
        assert summary.interest_saved == Decimal("0.00")


class TestPayoffByDate:
    """Tests for payoff-by-date calculations."""

    def test_achievable_target(self):
        """Target 15 years on $200k at 6.5% → $478.08 extra/mo.

        REGRESSION LOCK: Value determined by running the
        deterministic binary search to convergence
        (hi - lo <= 0.01). Verified below by proving $478.08
        achieves the target and $478.07 does not.
        """
        result = calculate_payoff_by_date(
            current_principal=Decimal("200000"),
            annual_rate=Decimal("0.065"),
            remaining_months=360,
            target_date=date(2041, 1, 1),
            origination_date=date(2026, 1, 1),
            payment_day=1,
        )
        assert result == Decimal("478.08"), (
            f"Expected extra payment 478.08, got {result}"
        )

        # Verify $478.08 actually achieves the 15-year target.
        schedule_at = generate_schedule(
            Decimal("200000"), Decimal("0.065"), 360,
            extra_monthly=Decimal("478.08"),
            origination_date=date(2026, 1, 1), payment_day=1,
        )
        assert schedule_at[-1].payment_date <= date(2041, 1, 1), (
            f"$478.08 extra should pay off by 2041-01-01, "
            f"but last payment is {schedule_at[-1].payment_date}"
        )

        # Verify $478.07 (one penny less) does NOT achieve it.
        schedule_under = generate_schedule(
            Decimal("200000"), Decimal("0.065"), 360,
            extra_monthly=Decimal("478.07"),
            origination_date=date(2026, 1, 1), payment_day=1,
        )
        assert schedule_under[-1].payment_date > date(2041, 1, 1), (
            f"$478.07 extra should NOT pay off by 2041-01-01, "
            f"but last payment is {schedule_under[-1].payment_date}"
        )

    def test_target_too_soon(self):
        """Target in past → returns None."""
        result = calculate_payoff_by_date(
            current_principal=Decimal("200000"),
            annual_rate=Decimal("0.065"),
            remaining_months=360,
            target_date=date(2025, 1, 1),
            origination_date=date(2026, 1, 1),
            payment_day=1,
        )
        assert result is None

    def test_target_after_standard_payoff(self):
        """Target after standard payoff → returns 0."""
        result = calculate_payoff_by_date(
            current_principal=Decimal("200000"),
            annual_rate=Decimal("0.065"),
            remaining_months=360,
            target_date=date(2060, 1, 1),
            origination_date=date(2026, 1, 1),
            payment_day=1,
        )
        assert result == Decimal("0.00")

    def test_zero_principal(self):
        """Zero principal → returns $0."""
        result = calculate_payoff_by_date(
            current_principal=Decimal("0"),
            annual_rate=Decimal("0.065"),
            remaining_months=360,
            target_date=date(2040, 1, 1),
            origination_date=date(2026, 1, 1),
            payment_day=1,
        )
        assert result == Decimal("0.00")


class TestCalculateRemainingMonths:
    """Tests for calculate_remaining_months date arithmetic."""

    def test_remaining_months_basic(self):
        """360-month loan, 60 months elapsed = 300 remaining.

        months_elapsed = (2025-2020)*12 + (1-1) = 60
        remaining = max(0, 360-60) = 300
        """
        result = calculate_remaining_months(
            date(2020, 1, 1), 360, as_of=date(2025, 1, 1),
        )
        assert result == 300, (
            f"Expected 300 remaining, got {result}"
        )

    def test_remaining_months_past_term(self):
        """12-month loan, 60 months elapsed = 0 remaining.

        months_elapsed = (2025-2020)*12 + (1-1) = 60
        remaining = max(0, 12-60) = 0
        """
        result = calculate_remaining_months(
            date(2020, 1, 1), 12, as_of=date(2025, 1, 1),
        )
        assert result == 0, (
            f"Expected 0 remaining, got {result}"
        )

    def test_remaining_months_same_month(self):
        """as_of same month as origination = full term remaining.

        months_elapsed = (2025-2025)*12 + (1-1) = 0
        remaining = max(0, 360-0) = 360
        """
        result = calculate_remaining_months(
            date(2025, 1, 1), 360, as_of=date(2025, 1, 15),
        )
        assert result == 360, (
            f"Expected 360 remaining, got {result}"
        )

    def test_remaining_months_none_as_of(self):
        """Default as_of=today returns a non-negative int."""
        result = calculate_remaining_months(
            date(2020, 1, 1), 360,
        )
        assert isinstance(result, int), (
            f"Expected int, got {type(result)}"
        )
        assert result >= 0, (
            f"Expected >= 0, got {result}"
        )
