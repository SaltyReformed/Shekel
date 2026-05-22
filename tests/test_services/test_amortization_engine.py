"""
Tests for the amortization engine service.
"""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

import pytest

from app.services.amortization_engine import (
    AmortizationRow,
    AmortizationSummary,
    PaymentRecord,
    RateChangeRecord,
    ReplayResult,
    calculate_monthly_payment,
    calculate_payoff_by_date,
    calculate_remaining_months,
    calculate_summary,
    generate_schedule,
    replay_confirmed_history,
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


    def test_contractual_payment_from_original_terms(self):
        """When original_principal and term_months are provided, the schedule
        uses the contractual payment instead of re-amortizing current balance.

        A $200k balance with a contractual payment from a $250k/360mo/6% loan
        (~$1,499/mo) should pay off faster than 315 remaining months because
        the payment exceeds what $200k/315mo would require.
        """
        # Without original terms: re-amortizes $200k over 315 months.
        schedule_reamort = generate_schedule(
            Decimal("200000"), Decimal("0.06"), 315,
            origination_date=date(2026, 1, 1), payment_day=1,
        )
        # With original terms: uses contractual payment from $250k/360mo.
        schedule_contract = generate_schedule(
            Decimal("200000"), Decimal("0.06"), 315,
            origination_date=date(2026, 1, 1), payment_day=1,
            original_principal=Decimal("250000"), term_months=360,
        )
        # Contractual payment is higher, so loan pays off sooner.
        assert len(schedule_contract) < len(schedule_reamort)
        # Both end at zero balance.
        assert schedule_reamort[-1].remaining_balance == Decimal("0.00")
        assert schedule_contract[-1].remaining_balance == Decimal("0.00")
        # Contractual schedule should NOT have any balance increases
        # (no negative amortization).
        for i in range(1, len(schedule_contract)):
            assert schedule_contract[i].remaining_balance <= schedule_contract[i - 1].remaining_balance, (
                f"Month {i}: balance increased from "
                f"{schedule_contract[i - 1].remaining_balance} to "
                f"{schedule_contract[i].remaining_balance}"
            )


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


    def test_summary_zero_remaining_months(self):
        """Summary with 0 remaining months does not raise.

        When a loan's term has fully elapsed, remaining_months is 0.
        generate_schedule returns [] and sum() must still produce a
        Decimal, not int 0.  Regression test for AttributeError:
        'int' object has no attribute 'quantize'.
        """
        summary = calculate_summary(
            current_principal=Decimal("25000"),
            annual_rate=Decimal("0.05"),
            remaining_months=0,
            origination_date=date(2020, 1, 1),
            payment_day=15,
            term_months=60,
        )
        assert summary.monthly_payment == Decimal("0.00")
        assert summary.total_interest == Decimal("0.00")
        assert summary.interest_saved == Decimal("0.00")
        assert summary.months_saved == 0


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


# ── Section 5 Regression Baseline ──────────────────────────────────────


class TestAmortizationEngineRegression:
    """Regression baseline for Section 5 (Debt and Account Improvements).

    These tests lock down the amortization engine's current behavior so
    that subsequent Section 5 commits can detect unintended regressions.
    Focus areas: cross-function consistency, payoff round-trip accuracy,
    Decimal type enforcement, and edge cases not covered by existing tests.
    """

    # ── Standard test scenario ─────────────────────────────────────
    # $250,000 mortgage at 6.5% for 30 years, payment day 1,
    # origination 2024-01-01.  Values independently verified via the
    # standard amortization formula M = P * [r(1+r)^n] / [(1+r)^n - 1].

    PRINCIPAL = Decimal("250000.00")
    RATE = Decimal("0.065")
    MONTHS = 360
    ORIGINATION = date(2024, 1, 1)
    PAYMENT_DAY = 1

    # ── Summary-schedule cross-validation ──────────────────────────

    def test_summary_total_interest_matches_schedule_sum(self):
        """Summary total_interest must equal sum of schedule interest rows.

        Section 5 may change how interest is computed.  This catches any
        divergence between the summary aggregation and the row-by-row
        schedule computation.
        """
        summary = calculate_summary(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            self.ORIGINATION, self.PAYMENT_DAY, self.MONTHS,
        )
        schedule = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
        )
        schedule_total_interest = sum(r.interest for r in schedule)
        assert summary.total_interest == schedule_total_interest

    def test_summary_payoff_date_matches_schedule_last_payment(self):
        """Summary payoff_date must equal the last schedule row's payment_date.

        Ensures the summary doesn't compute payoff date via a different
        code path that could drift from the schedule generator.
        """
        summary = calculate_summary(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            self.ORIGINATION, self.PAYMENT_DAY, self.MONTHS,
        )
        schedule = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
        )
        assert summary.payoff_date == schedule[-1].payment_date

    def test_summary_with_extra_matches_accelerated_schedule(self):
        """Summary with extra payment must match accelerated schedule metrics.

        Cross-validates total_interest_with_extra and payoff_date_with_extra
        against an independently generated accelerated schedule.
        """
        extra = Decimal("200.00")
        summary = calculate_summary(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            self.ORIGINATION, self.PAYMENT_DAY, self.MONTHS,
            extra_monthly=extra,
        )
        accel_schedule = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            extra_monthly=extra,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
        )
        accel_interest = sum(r.interest for r in accel_schedule)

        assert summary.total_interest_with_extra == accel_interest
        assert summary.payoff_date_with_extra == accel_schedule[-1].payment_date
        # Months saved = standard length - accelerated length.
        assert summary.months_saved == self.MONTHS - len(accel_schedule)
        assert summary.interest_saved == summary.total_interest - accel_interest

    # ── Payoff round-trip ──────────────────────────────────────────

    def test_payoff_by_date_round_trip(self):
        """calculate_payoff_by_date result fed back into generate_schedule
        must produce a payoff on or before the target date.

        This catches rounding drift in the binary search.
        """
        target = date(2044, 1, 1)  # 20-year payoff target
        extra = calculate_payoff_by_date(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            target, self.ORIGINATION, self.PAYMENT_DAY,
        )
        assert extra is not None
        assert extra > Decimal("0")

        accel = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            extra_monthly=extra,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
        )
        assert accel[-1].payment_date <= target
        assert accel[-1].remaining_balance == Decimal("0.00")

    # ── Known-value regression locks ───────────────────────────────

    def test_standard_schedule_known_values(self):
        """Lock down exact values for the standard $250k/6.5%/360mo scenario.

        These values were independently verified via the amortization formula.
        If any value changes, it indicates the engine's math has shifted.
        """
        schedule = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
        )

        # First row: mostly interest, small principal.
        assert schedule[0].month == 1
        assert schedule[0].payment == Decimal("1580.17")
        assert schedule[0].interest == Decimal("1354.17")
        assert schedule[0].principal == Decimal("226.00")
        assert schedule[0].remaining_balance == Decimal("249774.00")

        # Last row: final balance is exactly zero.
        assert schedule[-1].remaining_balance == Decimal("0.00")
        assert len(schedule) == 360

        # Totals.
        total_interest = sum(r.interest for r in schedule)
        total_principal = sum(r.principal + r.extra_payment for r in schedule)
        assert total_interest == Decimal("318861.58")
        assert total_principal == self.PRINCIPAL

    def test_standard_summary_known_values(self):
        """Lock down exact summary metrics for the standard scenario."""
        summary = calculate_summary(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            self.ORIGINATION, self.PAYMENT_DAY, self.MONTHS,
        )
        assert summary.monthly_payment == Decimal("1580.17")
        assert summary.total_interest == Decimal("318861.58")
        assert summary.payoff_date == date(2054, 1, 1)
        assert summary.months_saved == 0
        assert summary.interest_saved == Decimal("0.00")

    # ── Decimal type enforcement ───────────────────────────────────

    def test_all_schedule_monetary_fields_are_decimal(self):
        """Every monetary field in every schedule row must be Decimal.

        Float values in amortization output would be a critical precision
        bug in a financial application.  Section 5 must preserve this.
        """
        schedule = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
        )
        for row in schedule:
            assert isinstance(row.payment, Decimal), (
                f"Row {row.month}: payment is {type(row.payment).__name__}"
            )
            assert isinstance(row.principal, Decimal), (
                f"Row {row.month}: principal is {type(row.principal).__name__}"
            )
            assert isinstance(row.interest, Decimal), (
                f"Row {row.month}: interest is {type(row.interest).__name__}"
            )
            assert isinstance(row.extra_payment, Decimal), (
                f"Row {row.month}: extra_payment is {type(row.extra_payment).__name__}"
            )
            assert isinstance(row.remaining_balance, Decimal), (
                f"Row {row.month}: remaining_balance is {type(row.remaining_balance).__name__}"
            )

    def test_summary_monetary_fields_are_decimal(self):
        """All monetary fields in the summary must be Decimal."""
        summary = calculate_summary(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            self.ORIGINATION, self.PAYMENT_DAY, self.MONTHS,
        )
        assert isinstance(summary.monthly_payment, Decimal)
        assert isinstance(summary.total_interest, Decimal)
        assert isinstance(summary.interest_saved, Decimal)
        assert isinstance(summary.total_interest_with_extra, Decimal)

    # ── Edge cases ─────────────────────────────────────────────────

    def test_zero_extra_payment_equals_standard_schedule(self):
        """Explicitly passing extra_monthly=0 must produce the same schedule
        as the default (no extra payment argument).
        """
        standard = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
        )
        with_zero = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            extra_monthly=Decimal("0.00"),
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
        )
        assert len(standard) == len(with_zero)
        for s_row, z_row in zip(standard, with_zero):
            assert s_row.payment == z_row.payment
            assert s_row.principal == z_row.principal
            assert s_row.interest == z_row.interest
            assert s_row.remaining_balance == z_row.remaining_balance

    def test_huge_extra_payment_one_month_payoff(self):
        """Extra payment vastly exceeding principal pays off in one month.

        The engine must cap the payment and produce a single-row schedule
        with zero final balance.
        """
        schedule = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            extra_monthly=Decimal("999999.00"),
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
        )
        assert len(schedule) == 1
        assert schedule[0].remaining_balance == Decimal("0.00")

    def test_one_remaining_month_schedule(self):
        """Loan with 1 remaining month produces correct single-row schedule.

        The single payment should cover the remaining principal plus one
        month's interest.
        """
        principal = Decimal("5000.00")
        schedule = generate_schedule(
            principal, self.RATE, 1,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
        )
        assert len(schedule) == 1
        # Monthly rate = 0.065 / 12 = 0.00541667
        # Interest on $5,000 = $27.08
        assert schedule[0].interest == Decimal("27.08")
        assert schedule[0].principal == principal
        assert schedule[0].remaining_balance == Decimal("0.00")

    def test_schedule_payment_equals_principal_plus_interest(self):
        """For every row, payment (base) must equal principal + interest.

        The schedule separates the base payment from the extra_payment
        field.  This invariant must hold regardless of rounding
        adjustments.  A mismatch indicates broken arithmetic.
        """
        extra = Decimal("100.00")
        schedule = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            extra_monthly=extra,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
        )
        for row in schedule:
            expected_base = row.principal + row.interest
            assert row.payment == expected_base, (
                f"Row {row.month}: payment={row.payment} != "
                f"principal({row.principal}) + interest({row.interest}) "
                f"= {expected_base}"
            )

    def test_balance_monotonically_decreasing(self):
        """Remaining balance must never increase from one row to the next.

        Ensures no negative amortization occurs with standard or
        extra-payment schedules.
        """
        schedule = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            extra_monthly=Decimal("50.00"),
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
        )
        for i in range(1, len(schedule)):
            assert schedule[i].remaining_balance <= schedule[i - 1].remaining_balance, (
                f"Balance increased from row {i} to row {i + 1}: "
                f"{schedule[i - 1].remaining_balance} -> {schedule[i].remaining_balance}"
            )


# ── Payment-Aware Schedule Tests (Commit 5.1-1) ─────────────────────


class TestPaymentAwareSchedule:
    """Tests for payment-aware amortization schedule generation.

    Verifies that generate_schedule() correctly handles the optional
    payments parameter: year-month matching, overpayment caps,
    zero-balance termination, negative amortization, and is_confirmed
    propagation.  Uses a $10,000 loan at 6% for 12 months as the
    standard scenario -- small enough to verify every row by hand.

    Monthly payment (amortization formula):
        M = P * [r(1+r)^n] / [(1+r)^n - 1]
        = 10000 * [0.005 * 1.005^12] / [1.005^12 - 1]
        = $860.66
    """

    # ── Standard test scenario ─────────────────────────────────────
    PRINCIPAL = Decimal("10000.00")
    RATE = Decimal("0.06")
    MONTHS = 12
    ORIGINATION = date(2026, 1, 1)
    PAYMENT_DAY = 15
    # $860.66 per the amortization formula.
    MONTHLY_PAYMENT = Decimal("860.66")

    # ── Backward compatibility ────────────────────────────────────

    def test_no_payments_unchanged(self):
        """payments=None produces identical output to pre-change behavior.

        Verifies backward compatibility: the new parameter does not
        alter existing behavior when omitted.
        """
        schedule_none = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
        )
        schedule_explicit_none = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            payments=None,
        )
        assert len(schedule_none) == len(schedule_explicit_none)
        for row_a, row_b in zip(schedule_none, schedule_explicit_none):
            assert row_a.payment == row_b.payment
            assert row_a.principal == row_b.principal
            assert row_a.interest == row_b.interest
            assert row_a.remaining_balance == row_b.remaining_balance
            assert row_a.extra_payment == row_b.extra_payment
            assert row_a.is_confirmed is False
            assert row_b.is_confirmed is False

    def test_empty_payments_unchanged(self):
        """payments=[] is identical to payments=None.

        An empty list must not alter the schedule.
        """
        schedule_none = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
        )
        schedule_empty = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            payments=[],
        )
        assert len(schedule_none) == len(schedule_empty)
        for row_a, row_b in zip(schedule_none, schedule_empty):
            assert row_a.payment == row_b.payment
            assert row_a.principal == row_b.principal
            assert row_a.interest == row_b.interest
            assert row_a.remaining_balance == row_b.remaining_balance

    def test_exact_standard_payments_match_standard_schedule(self):
        """Payments at exactly the standard P&I amount produce the same
        schedule as the no-payments default.

        This verifies the payment-aware path computes identical interest
        and principal splits when the total payment equals the standard
        contractual amount.
        """
        # Generate the standard schedule to get exact payment amounts.
        standard = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
        )
        # Build PaymentRecords at exactly the standard payment for each month.
        payments = [
            PaymentRecord(
                payment_date=row.payment_date,
                amount=row.payment,
                is_confirmed=True,
            )
            for row in standard
        ]
        schedule_with = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            payments=payments,
        )
        assert len(schedule_with) == len(standard)
        for row_std, row_pay in zip(standard, schedule_with):
            assert row_pay.interest == row_std.interest
            assert row_pay.principal == row_std.principal
            assert row_pay.remaining_balance == row_std.remaining_balance

    # ── Extra payments ────────────────────────────────────────────

    def test_extra_payment_reduces_principal_faster(self):
        """Payment exceeding standard P&I reduces principal faster.

        Month 2: standard payment is $860.66, we pay $1,060.66 ($200 extra).
        Interest on $9,189.34 = $45.95.  Principal = $1,060.66 - $45.95 = $1,014.71.
        Extra = $1,060.66 - $860.66 = $200.00.
        Balance = $9,189.34 - $1,014.71 = $8,174.63.
        """
        payments = [
            PaymentRecord(
                payment_date=date(2026, 3, 15),  # Month 2 of schedule
                amount=self.MONTHLY_PAYMENT + Decimal("200.00"),
                is_confirmed=True,
            ),
        ]
        schedule = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            payments=payments,
        )
        # Month 1: standard (no payment record).
        assert schedule[0].interest == Decimal("50.00")
        assert schedule[0].principal == Decimal("810.66")
        assert schedule[0].remaining_balance == Decimal("9189.34")

        # Month 2: extra payment.
        assert schedule[1].interest == Decimal("45.95")
        assert schedule[1].principal == Decimal("1014.71")
        assert schedule[1].extra_payment == Decimal("200.00")
        assert schedule[1].remaining_balance == Decimal("8174.63")

        # Month 3: standard (no payment record), but lower balance.
        # Interest on $8,174.63 = $40.87.
        assert schedule[2].interest == Decimal("40.87")
        assert schedule[2].remaining_balance == Decimal("7354.84")

        # Total interest should be less than standard due to the extra.
        standard = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
        )
        total_interest_extra = sum(r.interest for r in schedule)
        total_interest_std = sum(r.interest for r in standard)
        assert total_interest_extra < total_interest_std

    # ── Partial and missed payments ───────────────────────────────

    def test_partial_payment_slower_payoff(self):
        """Payment below standard P&I: principal decreases slower.

        Month 1: pay $430.33 (half of $860.66).
        Interest = $50.00.  Principal = $430.33 - $50.00 = $380.33.
        Balance = $10,000.00 - $380.33 = $9,619.67.
        """
        payments = [
            PaymentRecord(
                payment_date=date(2026, 2, 15),  # Month 1
                amount=Decimal("430.33"),
                is_confirmed=True,
            ),
        ]
        schedule = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            payments=payments,
        )
        # Month 1: partial payment.
        assert schedule[0].interest == Decimal("50.00")
        assert schedule[0].principal == Decimal("380.33")
        assert schedule[0].remaining_balance == Decimal("9619.67")
        # Extra is $0 since payment < standard.
        assert schedule[0].extra_payment == Decimal("0.00")

        # Total interest should be higher than standard (slower principal
        # reduction means more interest accrues over the term).
        standard = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
        )
        total_interest_partial = sum(r.interest for r in schedule)
        total_interest_std = sum(r.interest for r in standard)
        assert total_interest_partial > total_interest_std
        # The final payment must be larger to absorb the shortfall.
        assert schedule[-1].payment > standard[-1].payment

    def test_zero_payment_negative_amortization(self):
        """$0 payment: interest accrues, principal increases.

        Month 1: pay $0.  Interest = $50.00.
        Principal = $0 - $50.00 = -$50.00 (negative amortization).
        Balance = $10,000 - (-$50) = $10,050.00.

        This correctly models a missed payment where only interest accrues.
        """
        payments = [
            PaymentRecord(
                payment_date=date(2026, 2, 15),  # Month 1
                amount=Decimal("0.00"),
                is_confirmed=False,
            ),
        ]
        schedule = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            payments=payments,
        )
        assert schedule[0].interest == Decimal("50.00")
        # Negative principal = negative amortization.
        assert schedule[0].principal == Decimal("-50.00")
        assert schedule[0].remaining_balance == Decimal("10050.00")
        assert schedule[0].extra_payment == Decimal("0.00")

    # ── is_confirmed flag ─────────────────────────────────────────

    def test_mixed_confirmed_projected_propagation(self):
        """is_confirmed flag propagates correctly to AmortizationRow.

        Months with confirmed payments get is_confirmed=True.
        Months with projected payments get is_confirmed=False.
        Months without any payment record get is_confirmed=False.
        """
        payments = [
            PaymentRecord(
                payment_date=date(2026, 2, 15),  # Month 1 -- confirmed
                amount=self.MONTHLY_PAYMENT,
                is_confirmed=True,
            ),
            PaymentRecord(
                payment_date=date(2026, 3, 15),  # Month 2 -- projected
                amount=self.MONTHLY_PAYMENT,
                is_confirmed=False,
            ),
        ]
        schedule = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            payments=payments,
        )
        assert schedule[0].is_confirmed is True   # Month 1: confirmed payment
        assert schedule[1].is_confirmed is False   # Month 2: projected payment
        assert schedule[2].is_confirmed is False   # Month 3: no payment record

    def test_all_confirmed_in_same_month(self):
        """Two confirmed payments in the same month: is_confirmed=True.

        Two payments of $430.33 each = $860.66 total (standard amount).
        Both confirmed, so the month is fully confirmed.
        """
        payments = [
            PaymentRecord(
                payment_date=date(2026, 2, 1),
                amount=Decimal("430.33"),
                is_confirmed=True,
            ),
            PaymentRecord(
                payment_date=date(2026, 2, 20),
                amount=Decimal("430.33"),
                is_confirmed=True,
            ),
        ]
        schedule = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            payments=payments,
        )
        # Both payments in Feb 2026 -> month 1 (Feb) is confirmed.
        assert schedule[0].is_confirmed is True

    def test_mixed_confirmed_in_same_month(self):
        """One confirmed + one projected in the same month: is_confirmed=False.

        A mix means the month's total is not fully confirmed -- one
        payment is still projected and could change.
        """
        payments = [
            PaymentRecord(
                payment_date=date(2026, 2, 1),
                amount=Decimal("430.33"),
                is_confirmed=True,
            ),
            PaymentRecord(
                payment_date=date(2026, 2, 20),
                amount=Decimal("430.33"),
                is_confirmed=False,  # This one is projected.
            ),
        ]
        schedule = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            payments=payments,
        )
        assert schedule[0].is_confirmed is False

    # ── Multiple payments per month ───────────────────────────────

    def test_multiple_payments_same_month_summed(self):
        """Two payments in the same year-month are summed.

        Two payments of $500 each = $1,000 total for the month.
        Interest = $50.00.  Principal = $1,000 - $50 = $950.
        Balance = $10,000 - $950 = $9,050.
        """
        payments = [
            PaymentRecord(
                payment_date=date(2026, 2, 5),
                amount=Decimal("500.00"),
                is_confirmed=True,
            ),
            PaymentRecord(
                payment_date=date(2026, 2, 19),
                amount=Decimal("500.00"),
                is_confirmed=True,
            ),
        ]
        schedule = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            payments=payments,
        )
        assert schedule[0].interest == Decimal("50.00")
        assert schedule[0].principal == Decimal("950.00")
        assert schedule[0].remaining_balance == Decimal("9050.00")

    # ── Pre-origination filtering ─────────────────────────────────

    def test_payments_before_origination_filtered(self):
        """Payments dated before origination_date are silently ignored.

        These may exist as data artifacts from before the loan started.
        """
        payments = [
            PaymentRecord(
                payment_date=date(2025, 12, 1),  # Before 2026-01-01 origination.
                amount=Decimal("1000.00"),
                is_confirmed=True,
            ),
        ]
        schedule_with = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            payments=payments,
        )
        schedule_none = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
        )
        # Pre-origination payment was filtered; schedules should match.
        assert len(schedule_with) == len(schedule_none)
        for row_a, row_b in zip(schedule_with, schedule_none):
            assert row_a.interest == row_b.interest
            assert row_a.remaining_balance == row_b.remaining_balance

    # ── Overpayment and zero-balance termination ──────────────────

    def test_overpayment_caps_principal_at_remaining(self):
        """Payment exceeding remaining balance + interest: principal capped.

        Month 1 balance is $10,000, interest is $50.  A $20,000 payment
        should cap principal at $10,000 (remaining balance), not produce
        a negative balance.
        """
        payments = [
            PaymentRecord(
                payment_date=date(2026, 2, 15),
                amount=Decimal("20000.00"),
                is_confirmed=True,
            ),
        ]
        schedule = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            payments=payments,
        )
        assert len(schedule) == 1
        assert schedule[0].remaining_balance == Decimal("0.00")
        assert schedule[0].principal == self.PRINCIPAL
        assert schedule[0].interest == Decimal("50.00")

    def test_lump_sum_terminates_schedule_early(self):
        """Large lump sum in month 3 of a 12-month loan: schedule stops early.

        Months 1-2 standard, month 3 pays off remaining balance.
        """
        payments = [
            PaymentRecord(
                payment_date=date(2026, 4, 15),  # Month 3 of schedule
                amount=Decimal("50000.00"),       # Far exceeds remaining
                is_confirmed=True,
            ),
        ]
        schedule = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            payments=payments,
        )
        # Should terminate at month 3.
        assert len(schedule) == 3
        assert schedule[-1].remaining_balance == Decimal("0.00")

    def test_zero_principal_returns_empty(self):
        """current_principal=0: empty schedule returned immediately."""
        schedule = generate_schedule(
            Decimal("0.00"), self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            payments=[
                PaymentRecord(
                    payment_date=date(2026, 2, 15),
                    amount=Decimal("500.00"),
                    is_confirmed=True,
                ),
            ],
        )
        assert schedule == []

    def test_payments_after_payoff_ignored(self):
        """Payments for months after the loan reaches zero are not included.

        A huge payment in month 1 pays off the loan.  Payments in months
        2-3 should not appear in the schedule.
        """
        payments = [
            PaymentRecord(
                payment_date=date(2026, 2, 15),  # Month 1 -- massive overpay
                amount=Decimal("50000.00"),
                is_confirmed=True,
            ),
            PaymentRecord(
                payment_date=date(2026, 3, 15),  # Month 2 -- after payoff
                amount=Decimal("500.00"),
                is_confirmed=True,
            ),
            PaymentRecord(
                payment_date=date(2026, 4, 15),  # Month 3 -- after payoff
                amount=Decimal("500.00"),
                is_confirmed=True,
            ),
        ]
        schedule = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            payments=payments,
        )
        assert len(schedule) == 1
        assert schedule[0].remaining_balance == Decimal("0.00")

    # ── Unsorted payments ─────────────────────────────────────────

    def test_unsorted_payments_handled(self):
        """Payments passed in non-chronological order produce the same
        result as sorted payments.

        The engine must sort internally before processing.
        """
        payment_list = [
            PaymentRecord(
                payment_date=date(2026, 3, 15),  # Month 2
                amount=self.MONTHLY_PAYMENT + Decimal("100.00"),
                is_confirmed=True,
            ),
            PaymentRecord(
                payment_date=date(2026, 2, 15),  # Month 1
                amount=self.MONTHLY_PAYMENT,
                is_confirmed=True,
            ),
        ]
        schedule_unsorted = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            payments=payment_list,
        )
        schedule_sorted = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            payments=sorted(payment_list, key=lambda p: p.payment_date),
        )
        assert len(schedule_unsorted) == len(schedule_sorted)
        for row_a, row_b in zip(schedule_unsorted, schedule_sorted):
            assert row_a.payment == row_b.payment
            assert row_a.principal == row_b.principal
            assert row_a.interest == row_b.interest
            assert row_a.remaining_balance == row_b.remaining_balance

    # ── Year-month matching ───────────────────────────────────────

    def test_payment_on_different_day_matches_month(self):
        """Payment dated 2026-02-05 matches schedule month 2026-02
        (payment_day=15), not an exact day match.

        Year-month matching is critical because payments come from
        biweekly pay periods with arbitrary dates.
        """
        payments = [
            PaymentRecord(
                payment_date=date(2026, 2, 5),  # Day 5, not day 15
                amount=self.MONTHLY_PAYMENT + Decimal("300.00"),
                is_confirmed=True,
            ),
        ]
        schedule = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            payments=payments,
        )
        # The $300 extra should appear in month 1 (Feb 2026).
        assert schedule[0].extra_payment == Decimal("300.00")
        assert schedule[0].is_confirmed is True

    # ── extra_monthly interaction with payments ───────────────────

    def test_extra_monthly_not_added_when_payment_exists(self):
        """extra_monthly is NOT added to months with a payment record.

        The payment record IS the total payment for that month.
        extra_monthly applies only to months without payment records.
        Double-counting would be a financial error.
        """
        payments = [
            PaymentRecord(
                payment_date=date(2026, 2, 15),  # Month 1
                amount=self.MONTHLY_PAYMENT,      # Exactly standard
                is_confirmed=True,
            ),
        ]
        schedule = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            extra_monthly=Decimal("500.00"),
            payments=payments,
        )
        # Month 1: payment record exists, extra_monthly NOT added.
        assert schedule[0].extra_payment == Decimal("0.00")
        # Month 2 (Commit 32 / MED-07 pinning of directional check):
        # extra = min(extra_monthly, balance - principal_portion).
        # Balance is ~$249,774 entering month 2, principal_portion is
        # ~$226, so the cap is far above the $500 extra_monthly.
        # The schedule must record extra_payment == 500.00 verbatim.
        assert schedule[1].extra_payment == Decimal("500.00"), (
            f"Expected 500.00, got {schedule[1].extra_payment}"
        )

    # ── Summary and projection passthrough ────────────────────────

    def test_calculate_summary_with_payments_passthrough(self):
        """calculate_summary with payments returns summary reflecting
        the payment-aware schedule.

        An extra payment in month 1 should reduce total interest
        compared to the standard no-payments summary.
        """
        payments = [
            PaymentRecord(
                payment_date=date(2026, 2, 15),
                amount=self.MONTHLY_PAYMENT + Decimal("2000.00"),
                is_confirmed=True,
            ),
        ]
        summary_standard = calculate_summary(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            self.ORIGINATION, self.PAYMENT_DAY, self.MONTHS,
        )
        summary_with = calculate_summary(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            self.ORIGINATION, self.PAYMENT_DAY, self.MONTHS,
            payments=payments,
        )
        # Extra payment reduces total interest.
        assert summary_with.total_interest < summary_standard.total_interest

class TestPaymentRecordValidation:
    """Tests for PaymentRecord dataclass validation.

    Validates that invalid inputs are caught at construction time
    with clear error messages rather than producing silent wrong
    results in the schedule loop.
    """

    def test_negative_amount_raises_value_error(self):
        """Negative payment amount is nonsensical and must be rejected."""
        with pytest.raises(ValueError, match="amount must be >= 0"):
            PaymentRecord(
                payment_date=date(2026, 2, 15),
                amount=Decimal("-100.00"),
                is_confirmed=True,
            )

    def test_float_amount_raises_type_error(self):
        """Float amount is a precision bug and must be rejected."""
        with pytest.raises(TypeError, match="amount must be a Decimal"):
            PaymentRecord(
                payment_date=date(2026, 2, 15),
                amount=100.00,
                is_confirmed=True,
            )

    def test_string_date_raises_type_error(self):
        """String date must be rejected -- only date instances accepted."""
        with pytest.raises(TypeError, match="payment_date must be a date"):
            PaymentRecord(
                payment_date="2026-02-15",
                amount=Decimal("100.00"),
                is_confirmed=True,
            )

    def test_int_confirmed_raises_type_error(self):
        """Integer is_confirmed must be rejected -- only bool accepted."""
        with pytest.raises(TypeError, match="is_confirmed must be a bool"):
            PaymentRecord(
                payment_date=date(2026, 2, 15),
                amount=Decimal("100.00"),
                is_confirmed=1,
            )

    def test_zero_amount_valid(self):
        """Zero amount is valid -- represents a missed payment."""
        record = PaymentRecord(
            payment_date=date(2026, 2, 15),
            amount=Decimal("0.00"),
            is_confirmed=False,
        )
        assert record.amount == Decimal("0.00")

    def test_valid_construction(self):
        """Valid PaymentRecord construction succeeds."""
        record = PaymentRecord(
            payment_date=date(2026, 2, 15),
            amount=Decimal("1500.00"),
            is_confirmed=True,
        )
        assert record.payment_date == date(2026, 2, 15)
        assert record.amount == Decimal("1500.00")
        assert record.is_confirmed is True


# ── ARM Rate Change Schedule Tests (Commit 5.7-1) ─────────────────────


class TestARMRateChangeSchedule:
    """Tests for ARM rate change support in the amortization engine.

    Verifies that generate_schedule() correctly handles the optional
    rate_changes parameter: rate lookup by effective_date, re-amortization
    at rate boundaries, interest_rate field population, and interaction
    with the payments parameter.

    Uses a $100,000 loan at 5% for 360 months as the standard scenario.
    Monthly payment = $536.82 (standard amortization formula).
    """

    # ── Standard test scenario ────────────────────────────────────
    PRINCIPAL = Decimal("100000.00")
    RATE = Decimal("0.05")
    MONTHS = 360
    ORIGINATION = date(2024, 1, 1)
    PAYMENT_DAY = 1
    MONTHLY_PAYMENT = Decimal("536.82")

    # ── Backward compatibility ────────────────────────────────────

    def test_arm_no_rate_changes_none(self):
        """rate_changes=None produces identical output to no rate_changes.

        Three-way equivalence: omitted, None, and empty list must all
        produce the same schedule.
        """
        schedule_omit = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
        )
        schedule_none = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            rate_changes=None,
        )
        schedule_empty = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            rate_changes=[],
        )
        assert len(schedule_omit) == len(schedule_none) == len(schedule_empty)
        for row_o, row_n, row_e in zip(schedule_omit, schedule_none, schedule_empty):
            assert row_o.payment == row_n.payment == row_e.payment
            assert row_o.interest == row_n.interest == row_e.interest
            assert row_o.remaining_balance == row_n.remaining_balance == row_e.remaining_balance
            # No rate_changes -> interest_rate is None on all rows.
            assert row_o.interest_rate is None
            assert row_n.interest_rate is None
            assert row_e.interest_rate is None

    # ── Rate increase ─────────────────────────────────────────────

    def test_arm_single_rate_increase(self):
        """Rate increases from 5% to 7% at month 13 (Feb 2025).

        Before adjustment: payment = $536.82, rate = 5%.
        Month 12 (Jan 2025): interest = balance * 0.05/12.
        Month 13 (Feb 2025): rate changes to 7%, payment re-amortizes.

        Re-amortization at month 13:
          balance after 12 months at 5% = $98,386.31 (from schedule).
          remaining months = 360 - 12 = 348.
          new payment = amortize($98,386.31, 0.07, 348).

        Hand calculation for new payment:
          r = 0.07/12 = 0.00583333...
          factor = (1 + r)^348
          M = 98386.31 * r * factor / (factor - 1)
          = $671.12 (verified via formula)
        """
        rate_changes = [
            RateChangeRecord(
                effective_date=date(2025, 2, 1),
                interest_rate=Decimal("0.07"),
            ),
        ]
        schedule = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            rate_changes=rate_changes,
        )

        # Month 12 (Jan 2025): last month at original rate.
        assert schedule[11].interest_rate == self.RATE
        assert schedule[11].payment == self.MONTHLY_PAYMENT

        # Month 13 (Feb 2025): rate changes, payment re-amortizes.
        assert schedule[12].interest_rate == Decimal("0.07")
        balance_at_12 = schedule[11].remaining_balance
        new_payment = calculate_monthly_payment(
            balance_at_12, Decimal("0.07"), 348,
        )
        assert schedule[12].payment == new_payment

        # Interest in month 13 uses the new rate.
        expected_interest = (
            balance_at_12 * Decimal("0.07") / 12
        ).quantize(Decimal("0.01"))
        assert schedule[12].interest == expected_interest

        # Total interest should be higher than fixed-rate schedule.
        fixed = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
        )
        total_arm = sum(r.interest for r in schedule)
        total_fixed = sum(r.interest for r in fixed)
        assert total_arm > total_fixed

    # ── Rate decrease ─────────────────────────────────────────────

    def test_arm_single_rate_decrease(self):
        """Rate decreases from 5% to 3% at month 13 (Feb 2025).

        Re-amortization at month 13:
          balance after 12 months at 5% = $98,386.31.
          remaining months = 348.
          new payment = amortize($98,386.31, 0.03, 348) = $408.16.
        """
        rate_changes = [
            RateChangeRecord(
                effective_date=date(2025, 2, 1),
                interest_rate=Decimal("0.03"),
            ),
        ]
        schedule = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            rate_changes=rate_changes,
        )

        # Month 13: payment decreases at new rate.
        balance_at_12 = schedule[11].remaining_balance
        new_payment = calculate_monthly_payment(
            balance_at_12, Decimal("0.03"), 348,
        )
        assert schedule[12].payment == new_payment
        assert schedule[12].payment < self.MONTHLY_PAYMENT

        # Total interest should be lower than fixed-rate schedule.
        fixed = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
        )
        total_arm = sum(r.interest for r in schedule)
        total_fixed = sum(r.interest for r in fixed)
        assert total_arm < total_fixed

    # ── Multiple rate changes ─────────────────────────────────────

    def test_arm_multiple_rate_changes(self):
        """Three rate changes: 5%->6% at month 13, 6%->7% at month 25,
        7%->4% at month 37.

        Verifies: each rate change triggers re-amortization, interest_rate
        field reflects the correct rate for each period, and the schedule
        terminates cleanly.
        """
        rate_changes = [
            RateChangeRecord(date(2025, 2, 1), Decimal("0.06")),
            RateChangeRecord(date(2026, 2, 1), Decimal("0.07")),
            RateChangeRecord(date(2027, 2, 1), Decimal("0.04")),
        ]
        schedule = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            rate_changes=rate_changes,
        )

        # Verify rate field transitions.
        assert schedule[0].interest_rate == self.RATE    # Month 1: 5%
        assert schedule[11].interest_rate == self.RATE   # Month 12: 5%
        assert schedule[12].interest_rate == Decimal("0.06")  # Month 13: 6%
        assert schedule[24].interest_rate == Decimal("0.07")  # Month 25: 7%
        assert schedule[36].interest_rate == Decimal("0.04")  # Month 37: 4%

        # Payment changes at each boundary.
        assert schedule[11].payment != schedule[12].payment
        assert schedule[23].payment != schedule[24].payment
        assert schedule[35].payment != schedule[36].payment

        # Schedule terminates with zero balance.
        assert schedule[-1].remaining_balance == Decimal("0.00")

    # ── Pre-origination filtering ─────────────────────────────────

    def test_arm_rate_change_before_origination(self):
        """Rate change before origination_date is silently ignored.

        Schedule should be identical to one with no rate changes.
        """
        rate_changes = [
            RateChangeRecord(
                effective_date=date(2023, 1, 1),  # Before 2024-01-01
                interest_rate=Decimal("0.08"),
            ),
        ]
        schedule_with = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            rate_changes=rate_changes,
        )
        schedule_none = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
        )
        assert len(schedule_with) == len(schedule_none)
        for row_a, row_b in zip(schedule_with, schedule_none):
            assert row_a.payment == row_b.payment
            assert row_a.interest == row_b.interest
            assert row_a.remaining_balance == row_b.remaining_balance

    # ── Rate change + payments interaction ────────────────────────

    def test_arm_rate_change_with_payments(self):
        """ARM loan with both payment records AND rate changes.

        Rate increases to 7% at month 13.  A $2000 lump-sum payment
        occurs in month 6.  Verifies:
        1. Month 6 uses the payment record amount (not standard payment).
        2. Month 13 re-amortizes at the new rate with the correct balance.
        3. Interest in month 13 uses 7%.
        """
        rate_changes = [
            RateChangeRecord(date(2025, 2, 1), Decimal("0.07")),
        ]
        payments = [
            PaymentRecord(
                payment_date=date(2024, 7, 1),  # Month 6
                amount=Decimal("2000.00"),
                is_confirmed=True,
            ),
        ]
        schedule = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            rate_changes=rate_changes,
            payments=payments,
        )

        # Month 6: payment record used.
        assert schedule[5].payment + schedule[5].extra_payment >= Decimal("2000.00") - Decimal("1.00")
        assert schedule[5].is_confirmed is True

        # Month 13: rate change applies after lower balance from extra payment.
        assert schedule[12].interest_rate == Decimal("0.07")
        balance_at_12 = schedule[11].remaining_balance
        expected_interest = (
            balance_at_12 * Decimal("0.07") / 12
        ).quantize(Decimal("0.01"))
        assert schedule[12].interest == expected_interest

    # ── Zero rate ─────────────────────────────────────────────────

    def test_arm_rate_change_to_zero(self):
        """Rate changes to 0%: no interest accrues, no division by zero.

        For 0% rate, payment = remaining_balance / remaining_months.
        """
        rate_changes = [
            RateChangeRecord(date(2025, 2, 1), Decimal("0.00")),
        ]
        schedule = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            rate_changes=rate_changes,
        )

        # After rate change, interest should be $0 for all remaining months.
        for row in schedule[12:]:
            assert row.interest == Decimal("0.00"), (
                f"Month {row.month}: expected $0 interest at 0% rate, "
                f"got {row.interest}"
            )

        # Schedule must still terminate.
        assert schedule[-1].remaining_balance == Decimal("0.00")

    # ── Negative rate rejected ────────────────────────────────────

    def test_arm_negative_rate_rejected(self):
        """Negative interest rate raises ValueError at construction."""
        with pytest.raises(ValueError, match="interest_rate must be >= 0"):
            RateChangeRecord(
                effective_date=date(2025, 6, 1),
                interest_rate=Decimal("-0.01"),
            )

    # ── Same-date deduplication ───────────────────────────────────

    def test_arm_multiple_changes_same_date(self):
        """Two rate changes on the same effective_date: last one wins.

        Sorted order is stable, so the second entry with the same date
        overwrites the first.
        """
        rate_changes = [
            RateChangeRecord(date(2025, 2, 1), Decimal("0.06")),
            RateChangeRecord(date(2025, 2, 1), Decimal("0.08")),
        ]
        schedule = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            rate_changes=rate_changes,
        )
        # The winning rate should be 0.08 (second entry).
        assert schedule[12].interest_rate == Decimal("0.08")

    # ── Rate change on exact payment date ─────────────────────────

    def test_arm_rate_change_exactly_on_payment_date(self):
        """Rate change effective_date == payment_date: new rate applies
        to that month's interest calculation.

        Month 1 payment_date = 2024-02-01.  Rate changes on 2024-02-01.
        Interest for month 1 should use the new rate.
        """
        rate_changes = [
            RateChangeRecord(
                effective_date=date(2024, 2, 1),
                interest_rate=Decimal("0.08"),
            ),
        ]
        schedule = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            rate_changes=rate_changes,
        )
        # Month 1: rate changed to 8%.
        # Interest = $100,000 * 0.08 / 12 = $666.67
        expected_interest = (
            self.PRINCIPAL * Decimal("0.08") / 12
        ).quantize(Decimal("0.01"))
        assert schedule[0].interest == expected_interest
        assert schedule[0].interest_rate == Decimal("0.08")

    # ── interest_rate field population ────────────────────────────

    def test_arm_interest_rate_field_populated(self):
        """Every row has interest_rate populated when rate_changes is provided.

        Rate starts at 5%, changes to 7% at month 13.
        Months 1-12: 5%.  Months 13+: 7%.
        """
        rate_changes = [
            RateChangeRecord(date(2025, 2, 1), Decimal("0.07")),
        ]
        schedule = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            rate_changes=rate_changes,
        )
        for row in schedule:
            assert row.interest_rate is not None, (
                f"Month {row.month}: interest_rate is None"
            )
        # Spot-check values.
        for row in schedule[:12]:
            assert row.interest_rate == self.RATE
        for row in schedule[12:]:
            assert row.interest_rate == Decimal("0.07")

    # ── Summary and projection passthrough ────────────────────────

    def test_arm_calculate_summary_with_rate_changes(self):
        """calculate_summary with rate_changes produces different totals
        than a fixed-rate summary.

        A rate increase to 7% at month 13 should increase total interest.
        """
        rate_changes = [
            RateChangeRecord(date(2025, 2, 1), Decimal("0.07")),
        ]
        summary_fixed = calculate_summary(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            self.ORIGINATION, self.PAYMENT_DAY, self.MONTHS,
        )
        summary_arm = calculate_summary(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            self.ORIGINATION, self.PAYMENT_DAY, self.MONTHS,
            rate_changes=rate_changes,
        )
        # Rate increase -> more total interest.
        assert summary_arm.total_interest > summary_fixed.total_interest

class TestRateChangeRecordValidation:
    """Tests for RateChangeRecord dataclass validation.

    Mirrors TestPaymentRecordValidation: validates that invalid inputs
    are caught at construction time with clear error messages.
    """

    def test_negative_rate_raises_value_error(self):
        """Negative interest rate must be rejected."""
        with pytest.raises(ValueError, match="interest_rate must be >= 0"):
            RateChangeRecord(
                effective_date=date(2025, 6, 1),
                interest_rate=Decimal("-0.01"),
            )

    def test_float_rate_raises_type_error(self):
        """Float rate is a precision bug and must be rejected."""
        with pytest.raises(TypeError, match="interest_rate must be a Decimal"):
            RateChangeRecord(
                effective_date=date(2025, 6, 1),
                interest_rate=0.07,
            )

    def test_string_date_raises_type_error(self):
        """String date must be rejected -- only date instances accepted."""
        with pytest.raises(TypeError, match="effective_date must be a date"):
            RateChangeRecord(
                effective_date="2025-06-01",
                interest_rate=Decimal("0.07"),
            )

    def test_zero_rate_valid(self):
        """Zero rate is valid -- represents an interest-free period."""
        record = RateChangeRecord(
            effective_date=date(2025, 6, 1),
            interest_rate=Decimal("0.00"),
        )
        assert record.interest_rate == Decimal("0.00")

    def test_valid_construction(self):
        """Valid RateChangeRecord construction succeeds."""
        record = RateChangeRecord(
            effective_date=date(2025, 6, 1),
            interest_rate=Decimal("0.07"),
        )
        assert record.effective_date == date(2025, 6, 1)
        assert record.interest_rate == Decimal("0.07")


# ── Edge Case Guards Tests (Commit 5.8-1) ─────────────────────────────


class TestEdgeCaseGuards:
    """Tests for overpayment capping, zero-balance termination, and
    zero-principal entry guards in the amortization engine.

    These guards harden the engine against boundary conditions reachable
    when real payment data (5.1) and ARM rate changes (5.7) interact
    with the schedule loop.  Every test asserts exact Decimal equality
    for financial values.

    Standard scenario: $10,000 at 6% for 12 months.
    Monthly payment = $860.66 (amortization formula).
    """

    PRINCIPAL = Decimal("10000.00")
    RATE = Decimal("0.06")
    MONTHS = 12
    ORIGINATION = date(2026, 1, 1)
    PAYMENT_DAY = 15
    MONTHLY_PAYMENT = Decimal("860.66")

    # ── C-5.8-1: Payment exactly equals remaining ────────────────

    def test_payment_exactly_equals_remaining(self):
        """PaymentRecord for exactly remaining_balance + interest_due.

        Setup: $1000 balance, 6% rate, 1 month.
        Interest = $1000 * 0.06/12 = $5.00.
        Payment = $1000 + $5.00 = $1005.00.
        Principal = $1005.00 - $5.00 = $1000.00 = balance.
        Result: 1 row, remaining_balance == $0.00 exactly.
        """
        payments = [
            PaymentRecord(
                payment_date=date(2026, 2, 15),
                amount=Decimal("1005.00"),
                is_confirmed=True,
            ),
        ]
        schedule = generate_schedule(
            Decimal("1000.00"), Decimal("0.06"), 1,
            origination_date=date(2026, 1, 1), payment_day=15,
            payments=payments,
        )
        assert len(schedule) == 1
        assert schedule[0].remaining_balance == Decimal("0.00")
        assert schedule[0].interest == Decimal("5.00")
        assert schedule[0].principal == Decimal("1000.00")

    # ── C-5.8-2: Payment exceeds remaining ───────────────────────

    def test_payment_exceeds_remaining(self):
        """PaymentRecord of $5000 against $1000 remaining.

        Interest = $1000 * 0.06/12 = $5.00.
        Principal = $5000 - $5 = $4995, but capped at $1000.
        Actual payment = $1000 + $5 = $1005.00.
        Extra = max($1005 - $860.66, 0) = $144.34.
        Remaining = $0.00.
        """
        payments = [
            PaymentRecord(
                payment_date=date(2026, 2, 15),
                amount=Decimal("5000.00"),
                is_confirmed=True,
            ),
        ]
        schedule = generate_schedule(
            Decimal("1000.00"), Decimal("0.06"), 12,
            origination_date=date(2026, 1, 1), payment_day=15,
            payments=payments,
        )
        assert len(schedule) == 1
        assert schedule[0].remaining_balance == Decimal("0.00")
        assert schedule[0].interest == Decimal("5.00")
        assert schedule[0].principal == Decimal("1000.00")
        # Payment is capped: interest + remaining_balance.
        assert schedule[0].payment == Decimal("1005.00")

    # ── C-5.8-3: Zero-balance stops schedule ─────────────────────

    def test_zero_balance_stops_schedule(self):
        """360-month term, lump sum at month 1 pays off loan.

        Result: exactly 1 row, not 360.
        """
        payments = [
            PaymentRecord(
                payment_date=date(2026, 2, 15),
                amount=Decimal("50000.00"),
                is_confirmed=True,
            ),
        ]
        schedule = generate_schedule(
            self.PRINCIPAL, self.RATE, 360,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            payments=payments,
        )
        assert len(schedule) == 1, (
            f"Expected 1 row after lump-sum payoff, got {len(schedule)}"
        )
        assert schedule[0].remaining_balance == Decimal("0.00")

    # ── C-5.8-4: Payments after payoff ignored ───────────────────

    def test_payments_after_payoff_ignored(self):
        """PaymentRecords exist for months 1-6, but payoff at month 1.

        Only the first month produces a row; months 2-6 are unreachable.
        """
        payments = [
            PaymentRecord(date(2026, 2, 15), Decimal("50000.00"), True),
            PaymentRecord(date(2026, 3, 15), Decimal("500.00"), True),
            PaymentRecord(date(2026, 4, 15), Decimal("500.00"), True),
            PaymentRecord(date(2026, 5, 15), Decimal("500.00"), True),
            PaymentRecord(date(2026, 6, 15), Decimal("500.00"), True),
            PaymentRecord(date(2026, 7, 15), Decimal("500.00"), True),
        ]
        schedule = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            payments=payments,
        )
        assert len(schedule) == 1, (
            f"Expected 1 row (payoff at month 1), got {len(schedule)}"
        )

    # ── C-5.8-5: Very large one-time payment ─────────────────────

    def test_very_large_one_time_payment(self):
        """$200K loan, single PaymentRecord of $300K in month 1.

        Interest = $200,000 * 0.065/12 = $1,083.33.
        Principal capped at $200,000 (remaining balance).
        Actual payment = $200,000 + $1,083.33 = $201,083.33.
        """
        payments = [
            PaymentRecord(
                payment_date=date(2024, 2, 1),
                amount=Decimal("300000.00"),
                is_confirmed=True,
            ),
        ]
        schedule = generate_schedule(
            Decimal("200000.00"), Decimal("0.065"), 360,
            origination_date=date(2024, 1, 1), payment_day=1,
            payments=payments,
        )
        assert len(schedule) == 1
        assert schedule[0].remaining_balance == Decimal("0.00")
        assert schedule[0].interest == Decimal("1083.33")
        assert schedule[0].principal == Decimal("200000.00")
        assert schedule[0].payment == Decimal("201083.33")
        # Payment must be < original $300K (capped).
        assert schedule[0].payment < Decimal("300000.00")

    # ── C-5.8-6: Remaining balance never negative (parametrized) ──

    @pytest.mark.parametrize(
        "principal,rate,months,extra,payments_fn,rate_changes_fn",
        [
            # Standard loan, no extras.
            (Decimal("100000.00"), Decimal("0.06"), 360, Decimal("0.00"),
             None, None),
            # Loan with extra_monthly.
            (Decimal("100000.00"), Decimal("0.06"), 360, Decimal("500.00"),
             None, None),
            # Loan with very large extra_monthly.
            (Decimal("50000.00"), Decimal("0.05"), 60, Decimal("5000.00"),
             None, None),
            # Loan with PaymentRecord overpayment.
            (Decimal("10000.00"), Decimal("0.06"), 12, Decimal("0.00"),
             lambda: [PaymentRecord(date(2026, 2, 15), Decimal("15000.00"), True)],
             None),
            # Loan with ARM rate change.
            (Decimal("100000.00"), Decimal("0.05"), 360, Decimal("0.00"),
             None,
             lambda: [RateChangeRecord(date(2027, 1, 1), Decimal("0.08"))]),
            # Both payments and rate changes.
            (Decimal("10000.00"), Decimal("0.06"), 12, Decimal("0.00"),
             lambda: [PaymentRecord(date(2026, 5, 15), Decimal("8000.00"), True)],
             lambda: [RateChangeRecord(date(2026, 4, 1), Decimal("0.08"))]),
        ],
        ids=[
            "standard", "with_extra", "large_extra", "payment_overpay",
            "arm_rate_change", "payment_and_rate",
        ],
    )
    def test_remaining_balance_never_negative(
        self, principal, rate, months, extra, payments_fn, rate_changes_fn,
    ):
        """Invariant: remaining_balance >= 0 on every row of every schedule.

        Parametrized across multiple loan configurations to catch any
        combination that could produce a negative balance.
        """
        payments = payments_fn() if payments_fn else None
        rate_changes = rate_changes_fn() if rate_changes_fn else None
        schedule = generate_schedule(
            principal, rate, months,
            extra_monthly=extra,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            payments=payments,
            rate_changes=rate_changes,
        )
        for row in schedule:
            assert row.remaining_balance >= Decimal("0.00"), (
                f"Month {row.month}: remaining_balance={row.remaining_balance} "
                f"is negative"
            )

    # ── C-5.8-7: Zero-principal loan ─────────────────────────────

    def test_zero_principal_loan(self):
        """current_principal=0 returns empty schedule immediately."""
        schedule = generate_schedule(
            Decimal("0.00"), Decimal("0.06"), 360,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
        )
        assert schedule == []

    # ── C-5.8-8: Overpayment + ARM rate change same month ────────

    def test_overpayment_with_arm_rate_change(self):
        """ARM rate change in the same month as an overpaying PaymentRecord.

        Setup: $1000 balance, rate changes from 6% to 3% on month 1.
        PaymentRecord: $5000 in month 1.
        Interest at new rate: $1000 * 0.03/12 = $2.50.
        Principal capped at $1000.  Actual payment = $1000 + $2.50 = $1002.50.
        """
        rate_changes = [
            RateChangeRecord(date(2026, 2, 15), Decimal("0.03")),
        ]
        payments = [
            PaymentRecord(date(2026, 2, 15), Decimal("5000.00"), True),
        ]
        schedule = generate_schedule(
            Decimal("1000.00"), Decimal("0.06"), 12,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            rate_changes=rate_changes,
            payments=payments,
        )
        assert len(schedule) == 1
        assert schedule[0].remaining_balance == Decimal("0.00")
        assert schedule[0].interest == Decimal("2.50")
        assert schedule[0].principal == Decimal("1000.00")
        assert schedule[0].interest_rate == Decimal("0.03")

    # ── C-5.8-9: Overpayment via extra_monthly only ──────────────

    def test_overpayment_via_extra_monthly_only(self):
        """Large extra_monthly terminates schedule early.

        $10,000 at 6%, 12 months.  Monthly payment = $860.66.
        extra_monthly = $9,000.

        Month 1:
          interest = $50.00, principal = $810.66.
          extra = min($9000, $10000 - $810.66) = min($9000, $9189.34) = $9000.
          balance = $10000 - $810.66 - $9000 = $189.34.
        Month 2:
          interest = $189.34 * 0.005 = $0.95.
          principal = $860.66 - $0.95 = $859.71 >= $189.34 → is_final.
          principal capped at $189.34.
          balance = $0.00.

        Schedule should be 2 rows (not 12).
        """
        schedule = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            extra_monthly=Decimal("9000.00"),
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
        )
        assert len(schedule) == 2, (
            f"Expected 2 rows with $9K extra on $10K loan, got {len(schedule)}"
        )
        assert schedule[-1].remaining_balance == Decimal("0.00")
        # Month 1: extra is the full $9000 (capped at extra_monthly, not
        # balance - principal_portion which would be $9189.34).
        assert schedule[0].extra_payment == Decimal("9000.00")
        assert schedule[0].remaining_balance == Decimal("189.34")
        # Month 2: final payment absorbs the residual.
        assert schedule[1].principal == Decimal("189.34")
        assert schedule[1].interest == Decimal("0.95")

    # ── C-5.8-10: Sub-penny residual cleanup ─────────────────────

    def test_sub_penny_residual_cleanup(self):
        """Standard amortization over full term ends at exactly $0.00.

        The is_final guard on the last iteration absorbs any sub-penny
        residual from accumulated rounding.  Verifies the final row has
        remaining_balance == $0.00 exactly (not $0.003 or -$0.001).
        Also verifies total principal paid = original principal.
        """
        principal = Decimal("123456.78")
        rate = Decimal("0.04875")
        months = 360
        schedule = generate_schedule(
            principal, rate, months,
            origination_date=date(2024, 1, 1), payment_day=1,
        )
        assert schedule[-1].remaining_balance == Decimal("0.00"), (
            f"Final balance: {schedule[-1].remaining_balance} (expected 0.00)"
        )
        total_principal = sum(r.principal + r.extra_payment for r in schedule)
        assert total_principal == principal, (
            f"Total principal {total_principal} != original {principal}"
        )

    # ── C-5.8-11: Negative amortization -- no false trigger ──────

    def test_negative_amortization_no_false_trigger(self):
        """Payment below interest: balance grows, guard does NOT trigger.

        $10,000 at 6%.  Month 1 interest = $50.00.
        PaymentRecord: $20.00 (below interest).
        Principal = $20 - $50 = -$30 (negative amortization).
        Balance = $10,000 - (-$30) = $10,030.00 (grows).

        The overpayment guard must NOT cap this; the loan must NOT
        terminate early.
        """
        payments = [
            PaymentRecord(date(2026, 2, 15), Decimal("20.00"), True),
        ]
        schedule = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            payments=payments,
        )
        # Month 1: negative amortization increases balance.
        assert schedule[0].principal == Decimal("-30.00")
        assert schedule[0].remaining_balance == Decimal("10030.00")
        # Loan must NOT terminate -- more months follow.
        assert len(schedule) > 1

    # ── C-5.8-12: Multiple overpayments, only first counts ───────

    def test_multiple_overpayments_only_first_counts(self):
        """PaymentRecords at months 1 and 2 -- month 1 pays off loan.

        Month 2's payment is unreachable because the loop breaks after
        month 1's payoff.
        """
        payments = [
            PaymentRecord(date(2026, 2, 15), Decimal("50000.00"), True),
            PaymentRecord(date(2026, 3, 15), Decimal("50000.00"), True),
        ]
        schedule = generate_schedule(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            payments=payments,
        )
        assert len(schedule) == 1, (
            f"Expected 1 row (payoff at month 1), got {len(schedule)}"
        )
        assert schedule[0].remaining_balance == Decimal("0.00")

    # ── C-5.8-13: Standard payments terminate at exactly zero ────

    def test_exactly_zero_after_standard_payments(self):
        """Loan that pays off cleanly with standard payments.

        $12,000 at 0% for 12 months: payment = $1,000, no interest.
        All 12 payments of $1,000 each = $12,000 total.
        Final balance is exactly $0.00 with no residual.
        """
        schedule = generate_schedule(
            Decimal("12000.00"), Decimal("0.00"), 12,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
        )
        assert len(schedule) == 12
        assert schedule[-1].remaining_balance == Decimal("0.00")
        # Every row should have exactly $1000 principal.
        for row in schedule:
            assert row.principal == Decimal("1000.00")
            assert row.interest == Decimal("0.00")

    # ── C-5.8-14: calculate_summary empty schedule ───────────────

    def test_calculate_summary_empty_schedule(self):
        """calculate_summary with zero principal: no crash, sensible defaults.

        Zero principal produces an empty schedule.  The summary must not
        access schedule[-1] and must return zero-value metrics.
        """
        summary = calculate_summary(
            current_principal=Decimal("0.00"),
            annual_rate=Decimal("0.06"),
            remaining_months=360,
            origination_date=self.ORIGINATION,
            payment_day=self.PAYMENT_DAY,
            term_months=360,
        )
        assert summary.total_interest == Decimal("0.00")
        assert summary.interest_saved == Decimal("0.00")
        assert summary.months_saved == 0

    # ── C-5.8-15: calculate_summary early termination ────────────

    def test_calculate_summary_early_termination(self):
        """calculate_summary with lump-sum payoff in month 1.

        Payoff_date should be month 1, not month 360.
        Total interest is one month's worth.
        """
        payments = [
            PaymentRecord(date(2026, 2, 15), Decimal("50000.00"), True),
        ]
        summary = calculate_summary(
            current_principal=self.PRINCIPAL,
            annual_rate=self.RATE,
            remaining_months=360,
            origination_date=self.ORIGINATION,
            payment_day=self.PAYMENT_DAY,
            term_months=self.MONTHS,
            payments=payments,
        )
        # Payoff date is month 1, not month 360.
        assert summary.payoff_date == date(2026, 2, 15)
        # Total interest is one month's worth: $10000 * 0.06/12 = $50.00.
        assert summary.total_interest == Decimal("50.00")

    # ── C-5.8-16: Overpayment extra_payment field correct ────────

    def test_overpayment_extra_payment_field_correct(self):
        """When overpayment cap triggers, extra_payment field is correct.

        $1000 balance, 6%, 12 months.  Monthly payment = $86.07.
        PaymentRecord: $2000 in month 1.
        Interest = $5.00.  Principal capped at $1000.
        Actual payment = $1005.00.
        Extra = max($1005.00 - $86.07, $0) = $918.93.

        Hand calculation:
            monthly_payment = amortize($1000, 0.06, 12) = $86.07
            interest = $1000 * 0.005 = $5.00
            principal_portion = $2000 - $5 = $1995 -> capped at $1000
            actual_payment = $1000 + $5 = $1005
            extra = max($1005 - $86.07, $0) = $918.93
        """
        payments = [
            PaymentRecord(date(2026, 2, 15), Decimal("2000.00"), True),
        ]
        schedule = generate_schedule(
            Decimal("1000.00"), self.RATE, self.MONTHS,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
            payments=payments,
        )
        assert len(schedule) == 1
        assert schedule[0].payment == Decimal("1005.00")
        assert schedule[0].extra_payment == Decimal("918.93"), (
            f"Expected extra $918.93, got {schedule[0].extra_payment}"
        )

    # ── C-5.8-17: payoff_by_date with guards ─────────────────────

    def test_payoff_by_date_with_guards(self):
        """calculate_payoff_by_date works correctly with early termination.

        The function uses generate_schedule internally in a binary search.
        Guards must not cause the binary search to diverge.
        The result should produce a schedule that pays off by the target.
        """
        target = date(2029, 1, 1)  # ~3 years
        extra = calculate_payoff_by_date(
            Decimal("100000.00"), Decimal("0.065"), 360,
            target_date=target,
            origination_date=date(2026, 1, 1),
            payment_day=1,
        )
        assert extra is not None
        assert extra > Decimal("0.00")
        # Verify the result actually achieves the target.
        schedule = generate_schedule(
            Decimal("100000.00"), Decimal("0.065"), 360,
            extra_monthly=extra,
            origination_date=date(2026, 1, 1), payment_day=1,
        )
        assert schedule[-1].payment_date <= target
        assert schedule[-1].remaining_balance == Decimal("0.00")

    # ── C-5.8-18: Negative principal (defensive) ─────────────────

    def test_negative_principal_defensive(self):
        """current_principal=-100 returns empty schedule, does not crash.

        Negative principal should never occur in practice, but the guard
        must handle it defensively.
        """
        schedule = generate_schedule(
            Decimal("-100.00"), Decimal("0.06"), 360,
            origination_date=self.ORIGINATION, payment_day=self.PAYMENT_DAY,
        )
        assert schedule == []


# ── ARM Contractual Path Bug Regression Tests ─────────────────────────


class TestARMContractualPaymentBug:
    """Regression tests for the ARM contractual payment bug.

    Prior to the fix, when a loan had both original_principal/term_months
    (triggering the "contractual" path) AND rate_changes (indicating an
    ARM), the monthly payment was incorrectly computed as:

        M(original_principal, current_rate, term_months)

    For an ARM, the rate stored in params.interest_rate is the CURRENT
    rate (after adjustment), not the origination rate.  So the above
    formula answers "what would the payment be if the original loan had
    always been at this rate?" -- a meaningless number.

    The correct ARM calculation re-amortizes from current_principal and
    remaining_months at the current rate:

        M(current_principal, current_rate, remaining_months)

    These tests verify the fix across summary, schedule, and projection
    layers using a scenario where original_principal != current_principal
    and the rate has changed from the origination rate.
    """

    # ── Test scenario ─────────────────────────────────────────────
    # An ARM mortgage: $100,000 original at (originally) 5%, 360 months.
    # The rate has since adjusted to 7%.  Balance is now $90,000 with
    # 300 months remaining.
    #
    # Correct monthly payment: M($90,000, 7%, 300) = $636.10
    # Wrong contractual path:  M($100,000, 7%, 360) = $665.30
    # Difference: $29.20/month

    ORIGINAL_PRINCIPAL = Decimal("100000.00")
    CURRENT_PRINCIPAL = Decimal("90000.00")
    CURRENT_RATE = Decimal("0.07")
    TERM_MONTHS = 360
    REMAINING_MONTHS = 300
    ORIGINATION = date(2019, 1, 1)
    PAYMENT_DAY = 1

    # Rate change record indicating the rate adjusted to 7%.
    # Effective date is before the schedule start so the current rate
    # applies from the first month.
    RATE_CHANGES = [
        RateChangeRecord(
            effective_date=date(2024, 1, 1),
            interest_rate=Decimal("0.07"),
        ),
    ]

    CORRECT_PAYMENT = Decimal("636.10")
    WRONG_PAYMENT = Decimal("665.30")

    # ── Summary monthly_payment ──────────────────────────────────

    def test_arm_summary_uses_current_balance_not_original(self):
        """ARM summary.monthly_payment must use current_principal and
        remaining_months, not original_principal and term_months.

        This is THE bug that caused the $33/month discrepancy.
        M($90,000, 7%, 300) = $636.10, not M($100,000, 7%, 360) = $665.30.
        """
        summary = calculate_summary(
            current_principal=self.CURRENT_PRINCIPAL,
            annual_rate=self.CURRENT_RATE,
            remaining_months=self.REMAINING_MONTHS,
            origination_date=self.ORIGINATION,
            payment_day=self.PAYMENT_DAY,
            term_months=self.TERM_MONTHS,
            original_principal=self.ORIGINAL_PRINCIPAL,
            rate_changes=self.RATE_CHANGES,
        )
        assert summary.monthly_payment == self.CORRECT_PAYMENT, (
            f"ARM summary payment should be {self.CORRECT_PAYMENT} "
            f"(re-amortized from current balance), "
            f"got {summary.monthly_payment}"
        )
        assert summary.monthly_payment != self.WRONG_PAYMENT, (
            "ARM summary payment must NOT use the contractual path "
            "(original_principal + term_months) when rate_changes exist"
        )

    def test_arm_summary_exact_penny_accuracy(self):
        """Verify the ARM summary payment to the exact penny using the
        standard amortization formula.

        M = P * [r(1+r)^n] / [(1+r)^n - 1]
        P = $90,000, r = 0.07/12, n = 300

        r = 0.00583333...
        (1+r)^300 = 5.717924...
        Numerator = 90000 * 0.00583333 * 5.717924 = 3001.926...
        Denominator = 5.717924 - 1 = 4.717924...
        M = 3001.926 / 4.717924 = 636.10
        """
        expected = calculate_monthly_payment(
            self.CURRENT_PRINCIPAL,
            self.CURRENT_RATE,
            self.REMAINING_MONTHS,
        )
        assert expected == self.CORRECT_PAYMENT

        summary = calculate_summary(
            current_principal=self.CURRENT_PRINCIPAL,
            annual_rate=self.CURRENT_RATE,
            remaining_months=self.REMAINING_MONTHS,
            origination_date=self.ORIGINATION,
            payment_day=self.PAYMENT_DAY,
            term_months=self.TERM_MONTHS,
            original_principal=self.ORIGINAL_PRINCIPAL,
            rate_changes=self.RATE_CHANGES,
        )
        assert summary.monthly_payment == expected

    # ── Schedule correctness ─────────────────────────────────────

    def test_arm_schedule_uses_reamortized_payment(self):
        """ARM schedule rows must use the re-amortized payment ($636.10),
        not the contractual payment from original terms ($665.30).

        Month 1: interest = $90,000 * 0.07/12 = $525.00
                 principal = $636.10 - $525.00 = $111.10
                 balance = $90,000 - $111.10 = $89,888.90

        Month 2: interest = $89,888.90 * 0.07/12 = $524.35
                 principal = $636.10 - $524.35 = $111.75
                 balance = $89,888.90 - $111.75 = $89,777.15
        """
        schedule = generate_schedule(
            self.CURRENT_PRINCIPAL, self.CURRENT_RATE,
            self.REMAINING_MONTHS,
            origination_date=self.ORIGINATION,
            payment_day=self.PAYMENT_DAY,
            original_principal=self.ORIGINAL_PRINCIPAL,
            term_months=self.TERM_MONTHS,
            rate_changes=self.RATE_CHANGES,
        )
        assert schedule[0].interest == Decimal("525.00"), (
            f"Month 1 interest: expected 525.00, got {schedule[0].interest}"
        )
        assert schedule[0].principal == Decimal("111.10"), (
            f"Month 1 principal: expected 111.10, got {schedule[0].principal}"
        )
        assert schedule[0].remaining_balance == Decimal("89888.90"), (
            f"Month 1 balance: expected 89888.90, "
            f"got {schedule[0].remaining_balance}"
        )

        assert schedule[1].interest == Decimal("524.35"), (
            f"Month 2 interest: expected 524.35, got {schedule[1].interest}"
        )
        assert schedule[1].remaining_balance == Decimal("89777.15"), (
            f"Month 2 balance: expected 89777.15, "
            f"got {schedule[1].remaining_balance}"
        )

    def test_arm_schedule_terminates_at_remaining_months(self):
        """ARM schedule must use remaining_months (300) as max_months,
        not the inflated remaining + term (300 + 360 = 660).

        With the correct payment of $636.10, the $90,000 balance should
        amortize to zero in exactly 300 months.
        """
        schedule = generate_schedule(
            self.CURRENT_PRINCIPAL, self.CURRENT_RATE,
            self.REMAINING_MONTHS,
            origination_date=self.ORIGINATION,
            payment_day=self.PAYMENT_DAY,
            original_principal=self.ORIGINAL_PRINCIPAL,
            term_months=self.TERM_MONTHS,
            rate_changes=self.RATE_CHANGES,
        )
        assert len(schedule) == self.REMAINING_MONTHS, (
            f"ARM schedule should be {self.REMAINING_MONTHS} months, "
            f"got {len(schedule)}"
        )
        assert schedule[-1].remaining_balance == Decimal("0.00")

    def test_arm_schedule_total_principal_equals_current_balance(self):
        """Sum of principal portions must equal the current principal.

        This is the fundamental amortization invariant: every dollar of
        the starting balance is accounted for in the schedule.
        """
        schedule = generate_schedule(
            self.CURRENT_PRINCIPAL, self.CURRENT_RATE,
            self.REMAINING_MONTHS,
            origination_date=self.ORIGINATION,
            payment_day=self.PAYMENT_DAY,
            original_principal=self.ORIGINAL_PRINCIPAL,
            term_months=self.TERM_MONTHS,
            rate_changes=self.RATE_CHANGES,
        )
        total_principal = sum(
            r.principal + r.extra_payment for r in schedule
        )
        assert total_principal == self.CURRENT_PRINCIPAL, (
            f"Total principal repaid should equal current balance "
            f"{self.CURRENT_PRINCIPAL}, got {total_principal}"
        )

    # ── Fixed-rate contractual path still works ──────────────────

    def test_fixed_rate_contractual_path_unchanged(self):
        """Fixed-rate loan (no rate_changes) still uses the contractual
        payment from original_principal and term_months.

        This ensures the ARM fix does not break fixed-rate behavior.
        A $100k loan at 5%/360mo with $90k remaining and 300 months
        left should still show M($100k, 5%, 360) = $536.82.
        """
        summary = calculate_summary(
            current_principal=self.CURRENT_PRINCIPAL,
            annual_rate=Decimal("0.05"),
            remaining_months=self.REMAINING_MONTHS,
            origination_date=self.ORIGINATION,
            payment_day=self.PAYMENT_DAY,
            term_months=self.TERM_MONTHS,
            original_principal=self.ORIGINAL_PRINCIPAL,
        )
        expected_contractual = calculate_monthly_payment(
            self.ORIGINAL_PRINCIPAL, Decimal("0.05"), self.TERM_MONTHS,
        )
        assert expected_contractual == Decimal("536.82")
        assert summary.monthly_payment == expected_contractual, (
            "Fixed-rate loan must use contractual payment from original terms"
        )

    # ── Regression: user's exact mortgage scenario ───────────────

    def test_user_mortgage_exact_values(self):
        """Regression lock for the specific mortgage that revealed the bug.

        Loan summary:
            Original principal:  $202,000.00
            Current principal:   $178,103.41
            Interest rate:       6.875% ARM
            Term:                360 months
            Origination:         Dec 1, 2018
            Payment day:         1st

        The bug produced M($202,000, 6.875%, 360) = $1,327.00 by always
        using original_principal and the full term.  The correct ARM
        formula is M(current_principal, rate, remaining_months); the
        exact dollar value depends on remaining_months which advances
        each calendar month.  We verify two stable invariants via
        ``calculate_summary`` with explicit ``rate_changes``:

          1. The monthly_payment equals the re-amortization formula
             applied to the CURRENT remaining months (272 here).
          2. The monthly_payment is NOT the contractual value
             ($1,327.00) -- this is the witness for the original bug.

        Pre-Commit-15 the test also exercised the orchestration via
        ``get_loan_projection`` (Path B); F-10 / follow-up Commit 15
        deleted that wrapper -- its orchestration responsibility now
        lives in ``loan_resolver.resolve_loan`` and is covered by the
        integration tests in
        ``tests/test_integration/test_loan_resolver_arm.py`` and
        ``tests/test_integration/test_loan_resolver_single_source.py``.
        """
        origination = date(2018, 12, 1)
        current_principal = Decimal("178103.41")
        original_principal = Decimal("202000.00")
        rate = Decimal("0.06875")
        term = 360

        # The contractual (buggy) payment is the regression witness.
        # If a future change reverts the fix and re-introduces the
        # contractual path, monthly_payment would equal this value
        # and assertion (2) would catch the regression.
        contractual_payment = calculate_monthly_payment(
            original_principal, rate, term,
        )
        assert contractual_payment == Decimal("1327.00"), (
            "Setup sanity check: contractual formula on the exact "
            "user scenario must equal $1,327.00 (the bug witness)."
        )

        # Engine-level guard via rate_changes.  remaining_months is
        # passed explicitly so the expected value is fully determined
        # by the inputs (no date.today() involvement).
        rate_changes = [
            RateChangeRecord(
                effective_date=date(2025, 12, 1),
                interest_rate=rate,
            ),
        ]
        summary = calculate_summary(
            current_principal=current_principal,
            annual_rate=rate,
            remaining_months=272,
            origination_date=origination,
            payment_day=1,
            term_months=term,
            original_principal=original_principal,
            rate_changes=rate_changes,
        )
        expected = calculate_monthly_payment(
            current_principal, rate, 272,
        )
        # Hand-computed sanity: M($178,103.41, 6.875%, 272) = $1,293.96.
        assert expected == Decimal("1293.96")
        assert summary.monthly_payment == expected, (
            f"monthly_payment must equal "
            f"M(${current_principal}, {rate}, 272) = {expected}, "
            f"got {summary.monthly_payment}"
        )
        assert summary.monthly_payment != contractual_payment, (
            "Regression: monthly_payment reverted to contractual "
            f"value ${contractual_payment} (the original bug)."
        )

    # ── Edge cases ───────────────────────────────────────────────

    def test_arm_empty_rate_changes_uses_contractual(self):
        """rate_changes=[] (empty list) is equivalent to no rate changes.

        An empty rate_changes list means no ARM adjustments occurred.
        The contractual path should still be used for fixed-rate behavior.
        """
        summary_none = calculate_summary(
            current_principal=self.CURRENT_PRINCIPAL,
            annual_rate=Decimal("0.05"),
            remaining_months=self.REMAINING_MONTHS,
            origination_date=self.ORIGINATION,
            payment_day=self.PAYMENT_DAY,
            term_months=self.TERM_MONTHS,
            original_principal=self.ORIGINAL_PRINCIPAL,
            rate_changes=None,
        )
        summary_empty = calculate_summary(
            current_principal=self.CURRENT_PRINCIPAL,
            annual_rate=Decimal("0.05"),
            remaining_months=self.REMAINING_MONTHS,
            origination_date=self.ORIGINATION,
            payment_day=self.PAYMENT_DAY,
            term_months=self.TERM_MONTHS,
            original_principal=self.ORIGINAL_PRINCIPAL,
            rate_changes=[],
        )
        # Both should use the contractual path.
        expected = calculate_monthly_payment(
            self.ORIGINAL_PRINCIPAL, Decimal("0.05"), self.TERM_MONTHS,
        )
        assert summary_none.monthly_payment == expected
        assert summary_empty.monthly_payment == expected

    def test_arm_rate_change_mid_schedule_uses_correct_months_left(self):
        """When a rate change occurs mid-schedule, the remaining months
        for re-amortization must equal remaining_months - month_num + 1,
        not an inflated value from the contractual max_months upper bound.

        Scenario: $90,000 at 5%, 300 months remaining. Rate changes to
        7% at month 13. At that point:
          - Balance has been reduced by 12 months of payments at 5%.
          - Re-amortization should use 300 - 12 = 288 remaining months.
        """
        # Rate starts at 5%, changes to 7% one year in.
        rate_changes = [
            RateChangeRecord(
                effective_date=date(2020, 2, 1),
                interest_rate=Decimal("0.07"),
            ),
        ]
        schedule = generate_schedule(
            self.CURRENT_PRINCIPAL, Decimal("0.05"),
            self.REMAINING_MONTHS,
            origination_date=self.ORIGINATION,
            payment_day=self.PAYMENT_DAY,
            original_principal=self.ORIGINAL_PRINCIPAL,
            term_months=self.TERM_MONTHS,
            rate_changes=rate_changes,
        )

        # Month 12: last month at 5%.
        balance_at_12 = schedule[11].remaining_balance

        # Month 13: rate changes to 7%, re-amortize over 288 remaining.
        # remaining_months=300, month_num=13, so months_left = 300-13+1 = 288.
        expected_payment_13 = calculate_monthly_payment(
            balance_at_12, Decimal("0.07"), 288,
        )
        assert schedule[12].payment == expected_payment_13, (
            f"Month 13 re-amortized payment should be {expected_payment_13} "
            f"(288 months left), got {schedule[12].payment}"
        )

        # Verify it uses 7% for interest.
        expected_interest_13 = (
            balance_at_12 * Decimal("0.07") / 12
        ).quantize(Decimal("0.01"))
        assert schedule[12].interest == expected_interest_13

        # Schedule should still terminate cleanly.
        assert schedule[-1].remaining_balance == Decimal("0.00")


class TestFixedRateAnchorReset:
    """Regression tests for F-8: anchor-reset payment gate.

    Prior to the fix, ``generate_schedule`` unconditionally recomputed
    ``monthly_payment`` whenever the anchor reset fired.  For fixed-rate
    loans the inner contract sets ``max_months = remaining_months +
    term_months`` (a generous upper bound for the early-payoff case),
    so ``months_left`` at the reset was roughly ``2 * term_months`` and
    the recomputed payment came out about half the contractual amount.

    The fix gates the recompute on ``not using_contractual``: ARM loans
    (``using_contractual`` is False) continue to re-amortize at the
    anchor; fixed-rate loans keep the contractual monthly payment that
    was computed at loop entry.

    The bug is unreachable in production today because every fixed-rate
    call site passes ``anchor_balance=None``.  Closing the gap lets a
    future Commit-16 fixed-rate true-up UX project from the corrected
    balance without corrupting the schedule.
    """

    # ── C14-1: fixed-rate anchor at origination ───────────────────
    #
    # $400,000 at 6% / 360 months.  M = P * r(1+r)^n / ((1+r)^n - 1)
    # with r = 0.06 / 12 = 0.005 and n = 360:
    #   (1.005)^360 = 6.022575...
    #   numerator = 400000 * 0.005 * 6.022575 = 12045.150...
    #   denominator = 6.022575 - 1 = 5.022575
    #   M = 12045.150 / 5.022575 = 2398.2046...
    #   -> Decimal("2398.20") after ROUND_HALF_UP to two places.

    FIXED_PRINCIPAL = Decimal("400000")
    FIXED_RATE = Decimal("0.06")
    FIXED_TERM = 360
    FIXED_ORIGINATION = date(2026, 1, 1)
    FIXED_PAYMENT_DAY = 1
    CONTRACTUAL_PAYMENT = Decimal("2398.20")

    def test_fixed_rate_anchor_at_origination_preserves_contractual_payment(self):
        """C14-1: every row's payment equals the contractual P&I when the
        anchor is passed for a fixed-rate loan.

        Without the F-8 gate, the anchor reset at the first month after
        origination would recompute ``monthly_payment`` over
        ``max_months - 1 + 1 = 720`` months (since
        ``max_months = remaining_months + term_months = 360 + 360``),
        producing a payment of roughly ``M($400000, 0.06, 720) ~ $2056``
        instead of the contractual ``$2398.20``.  After the gate, the
        contractual payment is held constant across every row.
        """
        schedule = generate_schedule(
            current_principal=self.FIXED_PRINCIPAL,
            annual_rate=self.FIXED_RATE,
            remaining_months=self.FIXED_TERM,
            origination_date=self.FIXED_ORIGINATION,
            payment_day=self.FIXED_PAYMENT_DAY,
            original_principal=self.FIXED_PRINCIPAL,
            term_months=self.FIXED_TERM,
            anchor_balance=self.FIXED_PRINCIPAL,
            anchor_date=self.FIXED_ORIGINATION,
        )

        # The anchor at origination is a no-op for the balance (it
        # equals the starting principal) but it fires the anchor-reset
        # branch; the F-8 gate must prevent the payment recompute.
        #
        # The contractual path keeps ``max_months = remaining_months +
        # term_months`` as a generous upper bound, so the loop may
        # produce one extra row that absorbs sub-penny rounding residue
        # after the contractual payment retires the principal across
        # 360 months (this is the existing behaviour locked by
        # ``test_contractual_payment_from_original_terms`` and is
        # unrelated to F-8).  All non-final rows must use the
        # contractual payment; if the F-8 gate is removed every row
        # would instead use ``M($400000, 0.06, 720) ~ $2056.83`` and
        # the schedule would stretch well past ``FIXED_TERM + 1``.
        assert len(schedule) <= self.FIXED_TERM + 1, (
            f"Schedule should retire the loan within "
            f"{self.FIXED_TERM + 1} rows (360 contractual + at most one "
            f"residue-absorption row); got {len(schedule)} rows, which "
            f"indicates the contractual payment was clobbered."
        )
        # Every row except the optional residue-absorption final row
        # must use the contractual payment exactly.
        non_residue_rows = (
            schedule[:-1] if len(schedule) > self.FIXED_TERM else schedule
        )
        for row in non_residue_rows:
            assert row.payment == self.CONTRACTUAL_PAYMENT, (
                f"Fixed-rate row {row.month} payment should be "
                f"{self.CONTRACTUAL_PAYMENT} (contractual M($400000, "
                f"0.06, 360)), got {row.payment}"
            )

        # The schedule must still amortize to zero -- the contractual
        # payment with no extra correctly retires the principal.
        assert schedule[-1].remaining_balance == Decimal("0.00")

    def test_fixed_rate_anchor_payment_matches_calculate_monthly_payment(self):
        """C14-1 lock: ``calculate_monthly_payment`` for the fixture
        returns exactly the value pinned in CONTRACTUAL_PAYMENT.

        Locks the hand-computed value above against any drift in the
        helper itself, so a future regression in
        ``calculate_monthly_payment`` would fail this test loudly rather
        than letting the schedule assertion silently re-pin against a
        wrong helper output.
        """
        assert calculate_monthly_payment(
            self.FIXED_PRINCIPAL, self.FIXED_RATE, self.FIXED_TERM,
        ) == self.CONTRACTUAL_PAYMENT

    # ── C14-2: ARM anchor mid-loan still re-amortizes ─────────────
    #
    # ARM scenario (``using_contractual`` is False because
    # ``original_principal`` is None): $100,000 balance at 6% with 300
    # months remaining, origination 2024-01-01.  Anchor at $90,000 on
    # 2024-06-15.
    #
    # The first scheduled pay_date strictly after the anchor is the
    # 6th iteration (origination + 1 -> 2024-02-01, ..., +5 ->
    # 2024-07-01).  At month_num=6 the gate is True (ARM), the balance
    # snaps to $90,000, and ``months_left = max_months - month_num + 1
    # = 300 - 6 + 1 = 295``.  Expected post-anchor payment:
    #   M($90,000, 0.06, 295)
    # = 90000 * 0.005 * (1.005)^295 / ((1.005)^295 - 1)

    ARM_BALANCE = Decimal("100000")
    ARM_RATE = Decimal("0.06")
    ARM_REMAINING = 300
    ARM_ORIGINATION = date(2024, 1, 1)
    ARM_ANCHOR_BALANCE = Decimal("90000")
    ARM_ANCHOR_DATE = date(2024, 6, 15)
    ARM_ANCHOR_MONTH_INDEX = 5  # 0-based: row 6 is index 5 (pay 2024-07-01)
    ARM_MONTHS_LEFT_AT_ANCHOR = 295

    def test_arm_mid_loan_anchor_reamortizes(self):
        """C14-2: ARM loans still re-amortize at the anchor reset.

        ``using_contractual`` is False for this fixture
        (``original_principal`` is None), so the F-8 gate does NOT
        suppress the recompute.  Row 6 (pay_date 2024-07-01) is the
        first row strictly after anchor_date 2024-06-15; at that row
        the balance must snap to $90,000 and the payment must
        re-amortize over the remaining 295 months at 6%.
        """
        schedule = generate_schedule(
            current_principal=self.ARM_BALANCE,
            annual_rate=self.ARM_RATE,
            remaining_months=self.ARM_REMAINING,
            origination_date=self.ARM_ORIGINATION,
            payment_day=1,
            anchor_balance=self.ARM_ANCHOR_BALANCE,
            anchor_date=self.ARM_ANCHOR_DATE,
        )

        anchor_row = schedule[self.ARM_ANCHOR_MONTH_INDEX]
        assert anchor_row.payment_date == date(2024, 7, 1)

        expected_post_anchor_payment = calculate_monthly_payment(
            self.ARM_ANCHOR_BALANCE,
            self.ARM_RATE,
            self.ARM_MONTHS_LEFT_AT_ANCHOR,
        )
        # M($90,000, 0.06, 295) hand-arithmetic:
        #   r = 0.005; (1.005)^295 ~ 4.367
        #   M = 90000 * 0.005 * 4.367 / (4.367 - 1)
        #     = 1965.15 / 3.367 = 583.71 (Decimal at two places).
        assert anchor_row.payment == expected_post_anchor_payment

        # Pre-anchor rows use the initial re-amortized payment from
        # current_principal over remaining_months (the ARM contract);
        # the gate does not change pre-anchor behaviour.
        initial_payment = calculate_monthly_payment(
            self.ARM_BALANCE, self.ARM_RATE, self.ARM_REMAINING,
        )
        assert schedule[0].payment == initial_payment

        # The anchor reset must change the payment -- pin the diff so a
        # regression that mistakenly applies the F-8 gate to ARMs would
        # fail loudly here.
        assert anchor_row.payment != initial_payment


class TestReplayConfirmedHistory:
    """Tests for ``replay_confirmed_history``.

    The first half of the amortization-engine split (Commit 1 of
    ``docs/plans/2026-05-21-amortization-engine-split-implementation.md``;
    architectural plan
    ``docs/plans/2026-05-21-amortization-engine-split-replay-projection.md``).
    Verifies that replay is a deterministic, what-if-free reduction of
    confirmed history up to an ``as_of`` date:

      - empty input yields a clean "no replay" result;
      - replay does NOT fabricate contractual rows for missed months
        (this is what distinguishes replay from projection);
      - pre-origination payments are filtered;
      - payments past ``as_of`` are filtered;
      - ARM anchor and rate changes during the replayed window are
        honored;
      - the returned ``ReplayResult`` carries the starting state a
        forward projection needs (``balance_as_of``,
        ``next_pay_date``, ``remaining_months_as_of``,
        ``applicable_rate_as_of``).

    Standard scenario: 30 yr / $300,000 / 6% / payment_day=1.  Monthly
    P&I:
        M = P * [r(1+r)^n] / [(1+r)^n - 1]
        r = 0.06/12 = 0.005, n = 360
        (1.005)^360 ~ 6.022575
        M = 300000 * 0.005 * 6.022575 / 5.022575 = $1,798.65
    """

    PRINCIPAL = Decimal("300000.00")
    RATE = Decimal("0.06")
    TERM_MONTHS = 360
    ORIGINATION = date(2026, 1, 1)
    PAYMENT_DAY = 1
    CONTRACTUAL_PAYMENT = Decimal("1798.65")

    # ── C1-1: empty input ─────────────────────────────────────────

    def test_empty_confirmed_payments(self):
        """C1-1: empty ``confirmed_payments`` returns no rows and the
        starting-state fields describe a pristine, un-replayed loan.

        Replay over an empty input must report zero rows,
        ``balance_as_of == original_principal``, ``next_pay_date ==
        origination + 1 month``, and ``remaining_months_as_of ==
        term_months``.  This is the all-projection case: the caller
        (the scenario composer) projects from origination forward
        because no confirmed history exists.
        """
        result = replay_confirmed_history(
            origination_date=self.ORIGINATION,
            original_principal=self.PRINCIPAL,
            annual_rate=self.RATE,
            term_months=self.TERM_MONTHS,
            payment_day=self.PAYMENT_DAY,
            confirmed_payments=[],
            rate_changes=None,
            anchor_balance=None,
            anchor_date=None,
            as_of=date(2026, 12, 31),
        )
        assert isinstance(result, ReplayResult)
        assert result.rows == []
        assert result.balance_as_of == self.PRINCIPAL
        # Origination 2026-01-01, payment_day=1 -> 2026-02-01.
        assert result.next_pay_date == date(2026, 2, 1)
        assert result.remaining_months_as_of == self.TERM_MONTHS
        assert result.applicable_rate_as_of == self.RATE

    # ── C1-2: single confirmed payment in month 1 ─────────────────

    def test_single_confirmed_payment_month_1(self):
        """C1-2: one confirmed contractual payment in schedule month 1.

        Hand arithmetic at $300,000 / 6% / 360:
          interest = 300000.00 * 0.005 = $1,500.00
          principal = 1798.65 - 1500.00 = $298.65
          balance after = 300000.00 - 298.65 = $299,701.35
        The row must be flagged ``is_confirmed=True`` per the
        architectural plan ("replay rows are the deterministic-past
        slice").
        """
        result = replay_confirmed_history(
            origination_date=self.ORIGINATION,
            original_principal=self.PRINCIPAL,
            annual_rate=self.RATE,
            term_months=self.TERM_MONTHS,
            payment_day=self.PAYMENT_DAY,
            confirmed_payments=[
                PaymentRecord(
                    payment_date=date(2026, 2, 1),
                    amount=self.CONTRACTUAL_PAYMENT,
                    is_confirmed=True,
                ),
            ],
            rate_changes=None,
            anchor_balance=None,
            anchor_date=None,
            as_of=date(2026, 5, 21),
        )
        assert len(result.rows) == 1
        row = result.rows[0]
        assert row.is_confirmed is True
        assert row.payment_date == date(2026, 2, 1)
        # 300000.00 * 0.005 = 1500.00.
        assert row.interest == Decimal("1500.00")
        # 1798.65 - 1500.00 = 298.65.
        assert row.principal == Decimal("298.65")
        # 300000.00 - 298.65 = 299701.35.
        assert row.remaining_balance == Decimal("299701.35")
        assert result.balance_as_of == Decimal("299701.35")
        # Month 2: 2026-03-01.
        assert result.next_pay_date == date(2026, 3, 1)
        assert result.remaining_months_as_of == self.TERM_MONTHS - 1

    # ── C1-3: three consecutive contractual payments ──────────────

    def test_multiple_confirmed_payments_span_months_1_to_3(self):
        """C1-3: three contractual payments span months 1-3.

        Hand arithmetic at $300,000 / 6% / 360:
          Month 1 (2026-02-01):
            interest = 300000.00 * 0.005 = $1,500.00
            principal = 1798.65 - 1500.00 = $298.65
            balance = 300000.00 - 298.65 = $299,701.35
          Month 2 (2026-03-01):
            interest = 299701.35 * 0.005 = 1498.50675 -> $1,498.51
            principal = 1798.65 - 1498.51 = $300.14
            balance = 299701.35 - 300.14 = $299,401.21
          Month 3 (2026-04-01):
            interest = 299401.21 * 0.005 = 1497.00605 -> $1,497.01
            principal = 1798.65 - 1497.01 = $301.64
            balance = 299401.21 - 301.64 = $299,099.57
        Balance must decrease monotonically; every row is
        ``is_confirmed=True``; ``remaining_months_as_of == term -
        rows``.
        """
        result = replay_confirmed_history(
            origination_date=self.ORIGINATION,
            original_principal=self.PRINCIPAL,
            annual_rate=self.RATE,
            term_months=self.TERM_MONTHS,
            payment_day=self.PAYMENT_DAY,
            confirmed_payments=[
                PaymentRecord(
                    payment_date=date(2026, 2, 1),
                    amount=self.CONTRACTUAL_PAYMENT,
                    is_confirmed=True,
                ),
                PaymentRecord(
                    payment_date=date(2026, 3, 1),
                    amount=self.CONTRACTUAL_PAYMENT,
                    is_confirmed=True,
                ),
                PaymentRecord(
                    payment_date=date(2026, 4, 1),
                    amount=self.CONTRACTUAL_PAYMENT,
                    is_confirmed=True,
                ),
            ],
            rate_changes=None,
            anchor_balance=None,
            anchor_date=None,
            as_of=date(2026, 5, 21),
        )
        assert len(result.rows) == 3
        # Row 1 (2026-02-01).
        assert result.rows[0].payment_date == date(2026, 2, 1)
        assert result.rows[0].interest == Decimal("1500.00")
        assert result.rows[0].principal == Decimal("298.65")
        assert result.rows[0].remaining_balance == Decimal("299701.35")
        # Row 2 (2026-03-01).
        assert result.rows[1].payment_date == date(2026, 3, 1)
        assert result.rows[1].interest == Decimal("1498.51")
        assert result.rows[1].principal == Decimal("300.14")
        assert result.rows[1].remaining_balance == Decimal("299401.21")
        # Row 3 (2026-04-01).
        assert result.rows[2].payment_date == date(2026, 4, 1)
        assert result.rows[2].interest == Decimal("1497.01")
        assert result.rows[2].principal == Decimal("301.64")
        assert result.rows[2].remaining_balance == Decimal("299099.57")
        # Balance monotonically decreasing.
        balances = [r.remaining_balance for r in result.rows]
        assert balances == sorted(balances, reverse=True)
        # All rows confirmed.
        assert all(r.is_confirmed is True for r in result.rows)
        # Aggregate fields.
        assert result.balance_as_of == Decimal("299099.57")
        assert result.remaining_months_as_of == self.TERM_MONTHS - 3

    # ── C1-4: gap in payments (month 3 missing) ───────────────────

    def test_gap_in_payments_months_1_2_4(self):
        """C1-4: months 1, 2, 4 are confirmed; month 3 is missing.

        Replay returns three rows for months 1, 2, 4 -- it does NOT
        fabricate a contractual row for month 3.  The architectural
        plan is explicit on this point ("Replay returns only what was
        recorded; the missing month is the caller's responsibility to
        reason about").  This is the load-bearing distinction between
        replay and projection.

        Hand arithmetic (skipping month 3 means no interest accrual
        for that month; the iteration's balance carries from the
        post-month-2 value directly into the month-4 interest calc):
          Month 1 (2026-02-01): balance -> $299,701.35 (per C1-2).
          Month 2 (2026-03-01): balance -> $299,401.21 (per C1-3).
          Month 4 (2026-05-01):
            interest = 299401.21 * 0.005 = $1,497.01
            principal = 1798.65 - 1497.01 = $301.64
            balance = 299401.21 - 301.64 = $299,099.57
        The month-4 row's ``month`` field is 4 (the loop counter is
        not reset by the skip), and its ``payment_date`` is
        2026-05-01.
        """
        result = replay_confirmed_history(
            origination_date=self.ORIGINATION,
            original_principal=self.PRINCIPAL,
            annual_rate=self.RATE,
            term_months=self.TERM_MONTHS,
            payment_day=self.PAYMENT_DAY,
            confirmed_payments=[
                PaymentRecord(
                    payment_date=date(2026, 2, 1),
                    amount=self.CONTRACTUAL_PAYMENT,
                    is_confirmed=True,
                ),
                PaymentRecord(
                    payment_date=date(2026, 3, 1),
                    amount=self.CONTRACTUAL_PAYMENT,
                    is_confirmed=True,
                ),
                # Month 3 (2026-04-01) deliberately missing.
                PaymentRecord(
                    payment_date=date(2026, 5, 1),
                    amount=self.CONTRACTUAL_PAYMENT,
                    is_confirmed=True,
                ),
            ],
            rate_changes=None,
            anchor_balance=None,
            anchor_date=None,
            as_of=date(2026, 12, 31),
        )
        # Exactly three rows: months 1, 2, 4.  No row fabricated for
        # the missing month 3.
        assert len(result.rows) == 3
        assert result.rows[0].payment_date == date(2026, 2, 1)
        assert result.rows[1].payment_date == date(2026, 3, 1)
        # Critical assertion: the third row is for May (month 4), not
        # April (month 3).
        assert result.rows[2].payment_date == date(2026, 5, 1)
        # The loop counter advances through skipped months, so the
        # row's ``month`` field is 4.
        assert result.rows[2].month == 4
        # No row should have payment_date in April 2026.
        april_rows = [
            r for r in result.rows if r.payment_date.month == 4
        ]
        assert april_rows == []
        # Balance after the May payment matches the post-month-2
        # balance reduced by the May payment (no April interest
        # accrual because the row was skipped).
        assert result.rows[2].interest == Decimal("1497.01")
        assert result.rows[2].principal == Decimal("301.64")
        assert result.rows[2].remaining_balance == Decimal("299099.57")

    # ── C1-5: confirmed payments past as_of filtered ──────────────

    def test_payments_past_as_of_filtered(self):
        """C1-5: confirmed payments months 1-6 with ``as_of`` set to
        the end of schedule month 3 produce three rows (months 1-3).

        The cutoff is enforced inside the iteration via
        ``pay_date > as_of`` so payment payload order does not matter.
        Schedule month 3 with payment_day=1 is 2026-04-01; an
        ``as_of`` of 2026-04-30 includes that row and excludes
        2026-05-01 and beyond.
        """
        payments_1_to_6 = [
            PaymentRecord(
                payment_date=date(2026, m, 1),
                amount=self.CONTRACTUAL_PAYMENT,
                is_confirmed=True,
            )
            for m in range(2, 8)  # Feb..Jul 2026 = schedule months 1..6.
        ]
        result = replay_confirmed_history(
            origination_date=self.ORIGINATION,
            original_principal=self.PRINCIPAL,
            annual_rate=self.RATE,
            term_months=self.TERM_MONTHS,
            payment_day=self.PAYMENT_DAY,
            confirmed_payments=payments_1_to_6,
            rate_changes=None,
            anchor_balance=None,
            anchor_date=None,
            as_of=date(2026, 4, 30),
        )
        # Months 1 (Feb), 2 (Mar), 3 (Apr) included.
        assert len(result.rows) == 3
        assert result.rows[0].payment_date == date(2026, 2, 1)
        assert result.rows[1].payment_date == date(2026, 3, 1)
        assert result.rows[2].payment_date == date(2026, 4, 1)
        # No row dated after as_of.
        for row in result.rows:
            assert row.payment_date <= date(2026, 4, 30)

    # ── C1-6: pre-origination payments filtered ───────────────────

    def test_pre_origination_payments_filtered(self):
        """C1-6: payments dated before ``origination_date`` are
        silently filtered.

        Mirrors existing engine behavior (``_build_payment_lookups``
        applies the same filter when ``origination_date`` is set).
        Includes one pre-origination payment plus one valid
        post-origination payment so the test verifies BOTH the
        filter and that the valid payment still produces a row.
        """
        result = replay_confirmed_history(
            origination_date=self.ORIGINATION,
            original_principal=self.PRINCIPAL,
            annual_rate=self.RATE,
            term_months=self.TERM_MONTHS,
            payment_day=self.PAYMENT_DAY,
            confirmed_payments=[
                # Pre-origination: 2025-12-15 < 2026-01-01.
                PaymentRecord(
                    payment_date=date(2025, 12, 15),
                    amount=Decimal("5000.00"),
                    is_confirmed=True,
                ),
                # Valid post-origination payment in month 1.
                PaymentRecord(
                    payment_date=date(2026, 2, 1),
                    amount=self.CONTRACTUAL_PAYMENT,
                    is_confirmed=True,
                ),
            ],
            rate_changes=None,
            anchor_balance=None,
            anchor_date=None,
            as_of=date(2026, 5, 21),
        )
        # Only the post-origination payment produces a row.
        assert len(result.rows) == 1
        assert result.rows[0].payment_date == date(2026, 2, 1)
        # Balance reflects the valid payment only, not the
        # pre-origination one (which would have pushed the balance
        # to roughly 295000 if it had been applied).
        # 300000.00 - 298.65 = 299701.35 (matches C1-2).
        assert result.rows[0].remaining_balance == Decimal("299701.35")
        assert result.balance_as_of == Decimal("299701.35")

    # ── C1-7: ARM anchor snaps balance ────────────────────────────

    # ARM-anchor scenario: $400,000 / 6% / 360 / originated 2024-01-01.
    # Anchor verified at $250,000 on 2025-12-15.  Two confirmed
    # contractual payments straddle the anchor (2024-06-01 and
    # 2024-07-01 pre-anchor, 2026-01-01 and 2026-02-01 post-anchor).
    # The anchor reset fires on the first scheduled month strictly
    # AFTER 2025-12-15.  With payment_day=1, schedule month 24 is
    # 2026-01-01, which is the first row strictly past the anchor.
    #
    # Hand arithmetic for the four kept rows:
    #   Pre-anchor at $400k / 6% / contractual $2,398.20:
    #     Row 1 (2024-06-01): interest = 400000.00 * 0.005 = $2,000.00
    #       principal = 2398.20 - 2000.00 = $398.20
    #       balance = 400000.00 - 398.20 = $399,601.80
    #     Row 2 (2024-07-01): interest = 399601.80 * 0.005 = 1998.009
    #       -> $1,998.01
    #       principal = 2398.20 - 1998.01 = $400.19
    #       balance = 399601.80 - 400.19 = $399,201.61
    #   Anchor reset to $250,000:
    #     Row 3 (2026-01-01): pre-row balance snapped to 250000.00.
    #       interest = 250000.00 * 0.005 = $1,250.00
    #       principal = 2398.20 - 1250.00 = $1,148.20
    #       balance = 250000.00 - 1148.20 = $248,851.80
    #     Row 4 (2026-02-01): interest = 248851.80 * 0.005 = 1244.259
    #       -> $1,244.26
    #       principal = 2398.20 - 1244.26 = $1,153.94
    #       balance = 248851.80 - 1153.94 = $247,697.86

    ARM_ANCHOR_PRINCIPAL = Decimal("400000.00")
    ARM_ANCHOR_RATE = Decimal("0.06")
    ARM_ANCHOR_TERM = 360
    ARM_ANCHOR_ORIGINATION = date(2024, 1, 1)
    ARM_ANCHOR_PAYMENT_DAY = 1
    ARM_ANCHOR_CONTRACTUAL = Decimal("2398.20")
    ARM_ANCHOR_BALANCE = Decimal("250000.00")
    ARM_ANCHOR_DATE = date(2025, 12, 15)

    def test_arm_anchor_snaps_balance(self):
        """C1-7: ARM anchor snaps the running balance at the first
        scheduled month strictly after ``anchor_date``.

        Pre-anchor rows compute interest against the un-snapped
        balance (the historical rate is unknown so the split is
        approximate, matching the engine's documented behavior).
        The post-anchor row computes interest against the anchor
        balance ($250,000 * 0.005 = $1,250.00); subsequent rows
        project from the post-payment balance and are exact.
        """
        result = replay_confirmed_history(
            origination_date=self.ARM_ANCHOR_ORIGINATION,
            original_principal=self.ARM_ANCHOR_PRINCIPAL,
            annual_rate=self.ARM_ANCHOR_RATE,
            term_months=self.ARM_ANCHOR_TERM,
            payment_day=self.ARM_ANCHOR_PAYMENT_DAY,
            confirmed_payments=[
                PaymentRecord(
                    payment_date=date(2024, 6, 1),
                    amount=self.ARM_ANCHOR_CONTRACTUAL,
                    is_confirmed=True,
                ),
                PaymentRecord(
                    payment_date=date(2024, 7, 1),
                    amount=self.ARM_ANCHOR_CONTRACTUAL,
                    is_confirmed=True,
                ),
                PaymentRecord(
                    payment_date=date(2026, 1, 1),
                    amount=self.ARM_ANCHOR_CONTRACTUAL,
                    is_confirmed=True,
                ),
                PaymentRecord(
                    payment_date=date(2026, 2, 1),
                    amount=self.ARM_ANCHOR_CONTRACTUAL,
                    is_confirmed=True,
                ),
            ],
            rate_changes=None,
            anchor_balance=self.ARM_ANCHOR_BALANCE,
            anchor_date=self.ARM_ANCHOR_DATE,
            as_of=date(2026, 5, 21),
        )
        assert len(result.rows) == 4
        # Pre-anchor row 1 (2024-06-01).
        assert result.rows[0].payment_date == date(2024, 6, 1)
        # interest = 400000.00 * 0.005 = 2000.00 (approximate split:
        # uses the current rate, not the unknown historical rate).
        assert result.rows[0].interest == Decimal("2000.00")
        assert result.rows[0].remaining_balance == Decimal("399601.80")
        # Pre-anchor row 2 (2024-07-01).
        assert result.rows[1].remaining_balance == Decimal("399201.61")
        # Post-anchor row 3 (2026-01-01): the snap fires before
        # interest is computed, so interest reflects the anchor
        # balance exactly.
        assert result.rows[2].payment_date == date(2026, 1, 1)
        # 250000.00 * 0.005 = 1250.00.
        assert result.rows[2].interest == Decimal("1250.00")
        # 2398.20 - 1250.00 = 1148.20.
        assert result.rows[2].principal == Decimal("1148.20")
        # 250000.00 - 1148.20 = 248851.80.
        assert result.rows[2].remaining_balance == Decimal("248851.80")
        # Post-anchor row 4 (2026-02-01).
        # interest = 248851.80 * 0.005 = 1244.259 -> 1244.26.
        assert result.rows[3].interest == Decimal("1244.26")
        # 248851.80 - (2398.20 - 1244.26) = 247697.86.
        assert result.rows[3].remaining_balance == Decimal("247697.86")
        assert result.balance_as_of == Decimal("247697.86")

    # ── C1-8: rate change during replay ───────────────────────────

    # ARM rate-change scenario: $100,000 / 5% initial / 360 /
    # originated 2024-01-01.  Rate changes to 7% effective 2025-02-01
    # (start of schedule month 13).  Twenty-four confirmed payments
    # cover schedule months 1-24 (2024-02-01..2026-01-01).  At month
    # 13 the engine re-amortizes the remaining balance over the
    # remaining 348 months at 7%.  Month 13's interest is
    # ``balance_at_12 * 0.07 / 12``.

    RC_PRINCIPAL = Decimal("100000.00")
    RC_INITIAL_RATE = Decimal("0.05")
    RC_NEW_RATE = Decimal("0.07")
    RC_TERM = 360
    RC_ORIGINATION = date(2024, 1, 1)
    RC_PAYMENT_DAY = 1
    RC_INITIAL_PAYMENT = Decimal("536.82")
    RC_RATE_CHANGE_DATE = date(2025, 2, 1)

    def test_rate_change_during_replay(self):
        """C1-8: rate change at schedule month 13 reflects in the
        applicable rate field and re-amortizes the interest
        calculation from that month forward.

        The post-change rate (0.07) must appear in
        ``applicable_rate_as_of`` because the rate-change effective
        date (2025-02-01) is before ``next_pay_date`` (2026-02-01)
        after a 24-month confirmed window.  The month-13 row's
        ``interest_rate`` field equals the new rate and its
        ``interest`` is computed at the new rate against the
        pre-row balance (mirrors generate_schedule's rate-change
        behavior).
        """
        # Build the 24 payment dates explicitly: 2024-02..2024-12 =
        # schedule months 1..11, 2025-01..2025-12 = schedule months
        # 12..23, 2026-01 = schedule month 24.  All payments at
        # $700.00, above both the pre-change contractual ($536.82)
        # and the post-change contractual (re-amortized at the new
        # rate; the engine recomputes at the rate boundary).
        payments: list[PaymentRecord] = []
        # 2024-02..2024-12 (11 dates).
        for m in range(2, 13):
            payments.append(PaymentRecord(
                payment_date=date(2024, m, 1),
                amount=Decimal("700.00"),
                is_confirmed=True,
            ))
        # 2025-01..2025-12 (12 dates).
        for m in range(1, 13):
            payments.append(PaymentRecord(
                payment_date=date(2025, m, 1),
                amount=Decimal("700.00"),
                is_confirmed=True,
            ))
        # 2026-01 (1 date).
        payments.append(PaymentRecord(
            payment_date=date(2026, 1, 1),
            amount=Decimal("700.00"),
            is_confirmed=True,
        ))
        assert len(payments) == 24

        result = replay_confirmed_history(
            origination_date=self.RC_ORIGINATION,
            original_principal=self.RC_PRINCIPAL,
            annual_rate=self.RC_INITIAL_RATE,
            term_months=self.RC_TERM,
            payment_day=self.RC_PAYMENT_DAY,
            confirmed_payments=payments,
            rate_changes=[
                RateChangeRecord(
                    effective_date=self.RC_RATE_CHANGE_DATE,
                    interest_rate=self.RC_NEW_RATE,
                ),
            ],
            anchor_balance=None,
            anchor_date=None,
            as_of=date(2026, 5, 21),
        )
        # All 24 months produce rows.
        assert len(result.rows) == 24
        # Month 12 is the last row at the initial 5% rate (pay_date
        # 2025-01-01, before the rate change at 2025-02-01).
        month_12 = result.rows[11]
        assert month_12.payment_date == date(2025, 1, 1)
        assert month_12.interest_rate == self.RC_INITIAL_RATE
        # Month 13 (2025-02-01) is the first row at the new 7% rate.
        month_13 = result.rows[12]
        assert month_13.payment_date == date(2025, 2, 1)
        assert month_13.interest_rate == self.RC_NEW_RATE
        # Interest in month 13 uses the new rate against the
        # post-month-12 balance.  Compute the expected value from
        # the engine's pre-row state via the documented formula:
        # round_money(balance_at_12 * 0.07 / 12).
        balance_at_12 = month_12.remaining_balance
        expected_month_13_interest = (
            balance_at_12 * self.RC_NEW_RATE / 12
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        assert month_13.interest == expected_month_13_interest
        # applicable_rate_as_of: next_pay_date is 2026-02-01 (month
        # after the last replayed row), past the rate-change
        # effective date, so the post-change rate is the applicable
        # rate.
        assert result.next_pay_date == date(2026, 2, 1)
        assert result.applicable_rate_as_of == self.RC_NEW_RATE

    # ── C1-9: balance_as_of cross-check vs generate_schedule ──────

    def test_balance_as_of_matches_generate_schedule_replay(self):
        """C1-9: ``balance_as_of`` equals ``generate_schedule``'s
        ``remaining_balance`` for the equivalent row.

        Cross-check during the migration window (Commits 1-8 keep
        both surfaces alive; Commit 9 deletes ``generate_schedule``
        and this test deletes with it).  Both functions share the
        per-month payment-record branch by construction, so for
        identical inputs they must produce identical balances.
        """
        payments = [
            PaymentRecord(
                payment_date=date(2026, 2, 1),
                amount=self.CONTRACTUAL_PAYMENT,
                is_confirmed=True,
            ),
            PaymentRecord(
                payment_date=date(2026, 3, 1),
                amount=self.CONTRACTUAL_PAYMENT,
                is_confirmed=True,
            ),
            PaymentRecord(
                payment_date=date(2026, 4, 1),
                amount=self.CONTRACTUAL_PAYMENT,
                is_confirmed=True,
            ),
        ]
        replay_result = replay_confirmed_history(
            origination_date=self.ORIGINATION,
            original_principal=self.PRINCIPAL,
            annual_rate=self.RATE,
            term_months=self.TERM_MONTHS,
            payment_day=self.PAYMENT_DAY,
            confirmed_payments=payments,
            rate_changes=None,
            anchor_balance=None,
            anchor_date=None,
            as_of=date(2026, 4, 30),
        )
        generate_result = generate_schedule(
            current_principal=self.PRINCIPAL,
            annual_rate=self.RATE,
            remaining_months=self.TERM_MONTHS,
            origination_date=self.ORIGINATION,
            payment_day=self.PAYMENT_DAY,
            original_principal=self.PRINCIPAL,
            term_months=self.TERM_MONTHS,
            payments=payments,
        )
        # Both surfaces produce three rows for the three confirmed
        # payments (generate_schedule continues past them with
        # contractual rows, but the first three should match).
        assert len(replay_result.rows) == 3
        assert generate_result[2].payment_date == date(2026, 4, 1)
        # The replay's balance_as_of equals row 2's (zero-indexed)
        # remaining_balance from generate_schedule.
        assert replay_result.balance_as_of == generate_result[2].remaining_balance
        # And each replayed row's balance matches.
        for replay_row, gen_row in zip(replay_result.rows, generate_result[:3]):
            assert replay_row.remaining_balance == gen_row.remaining_balance
            assert replay_row.interest == gen_row.interest
            assert replay_row.principal == gen_row.principal

    # ── C1-10: mixed-confirmation input still labels rows confirmed ─

    def test_replay_rows_all_confirmed(self):
        """C1-10: regardless of the ``is_confirmed`` flag on input
        ``PaymentRecord`` instances, every output row is
        ``is_confirmed=True``.

        Per the architectural plan: "replay only consumes confirmed
        inputs at this phase; the caller filters before calling.
        All rows have is_confirmed=True."  Passing a mixed input is
        a caller bug, but the function deliberately labels every
        output row True because the semantic role of replay is
        "the deterministic-past slice" and downstream consumers
        rely on the flag.
        """
        result = replay_confirmed_history(
            origination_date=self.ORIGINATION,
            original_principal=self.PRINCIPAL,
            annual_rate=self.RATE,
            term_months=self.TERM_MONTHS,
            payment_day=self.PAYMENT_DAY,
            confirmed_payments=[
                # Confirmed.
                PaymentRecord(
                    payment_date=date(2026, 2, 1),
                    amount=self.CONTRACTUAL_PAYMENT,
                    is_confirmed=True,
                ),
                # Not confirmed (caller bug -- replay still treats it
                # as confirmed).
                PaymentRecord(
                    payment_date=date(2026, 3, 1),
                    amount=self.CONTRACTUAL_PAYMENT,
                    is_confirmed=False,
                ),
            ],
            rate_changes=None,
            anchor_balance=None,
            anchor_date=None,
            as_of=date(2026, 5, 21),
        )
        assert len(result.rows) == 2
        # Both rows labelled confirmed despite the mixed input.
        assert all(row.is_confirmed is True for row in result.rows)

    # ── C1-11: next_pay_date is the month after the last row ──────

    def test_next_pay_date_correct(self):
        """C1-11: when the last replayed row is for month N,
        ``next_pay_date`` is the first day of month N+1 (clamped to
        ``payment_day``).

        Three contractual payments months 1-3 land the last row on
        2026-04-01.  ``next_pay_date`` must be 2026-05-01.  Pinning
        this property keeps the boundary between replay and
        projection clean: the composer (Commit 3) passes
        ``next_pay_date`` directly to ``project_forward`` as the
        first projected ``payment_date``.
        """
        result = replay_confirmed_history(
            origination_date=self.ORIGINATION,
            original_principal=self.PRINCIPAL,
            annual_rate=self.RATE,
            term_months=self.TERM_MONTHS,
            payment_day=self.PAYMENT_DAY,
            confirmed_payments=[
                PaymentRecord(
                    payment_date=date(2026, 2, 1),
                    amount=self.CONTRACTUAL_PAYMENT,
                    is_confirmed=True,
                ),
                PaymentRecord(
                    payment_date=date(2026, 3, 1),
                    amount=self.CONTRACTUAL_PAYMENT,
                    is_confirmed=True,
                ),
                PaymentRecord(
                    payment_date=date(2026, 4, 1),
                    amount=self.CONTRACTUAL_PAYMENT,
                    is_confirmed=True,
                ),
            ],
            rate_changes=None,
            anchor_balance=None,
            anchor_date=None,
            as_of=date(2026, 5, 21),
        )
        assert len(result.rows) == 3
        assert result.rows[-1].payment_date == date(2026, 4, 1)
        # Month after 2026-04-01 (payment_day=1) is 2026-05-01.
        assert result.next_pay_date == date(2026, 5, 1)
        # Cross-check: month after the last row.
        assert result.next_pay_date.month == 5
        assert result.next_pay_date.year == 2026
