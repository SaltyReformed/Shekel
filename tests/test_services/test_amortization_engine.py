"""
Tests for the amortization engine service.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.services.amortization_engine import (
    AmortizationRow,
    AmortizationSummary,
    LoanProjection,
    calculate_monthly_payment,
    calculate_payoff_by_date,
    calculate_remaining_months,
    calculate_summary,
    generate_schedule,
    get_loan_projection,
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


class TestGetLoanProjection:
    """Tests for the get_loan_projection convenience function."""

    def test_returns_projection_dataclass(self):
        """get_loan_projection returns a LoanProjection with all fields."""
        params = type("P", (), {
            "origination_date": date(2025, 1, 1),
            "term_months": 60,
            "original_principal": Decimal("25000.00"),
            "current_principal": Decimal("25000.00"),
            "interest_rate": Decimal("0.05000"),
            "payment_day": 15,
        })()

        proj = get_loan_projection(params)
        assert isinstance(proj, LoanProjection)
        assert proj.remaining_months >= 0
        assert isinstance(proj.summary, AmortizationSummary)
        assert isinstance(proj.schedule, list)
        assert proj.summary.monthly_payment > 0

    def test_zero_remaining_months(self):
        """Projection for fully elapsed loan does not raise.

        Regression test for the int-vs-Decimal sum() bug.  The monthly
        payment still reflects the contractual amount (from original
        principal and term), but the schedule is empty and total interest
        is zero since there are no remaining months.
        """
        params = type("P", (), {
            "origination_date": date(2015, 1, 1),
            "term_months": 60,
            "original_principal": Decimal("25000.00"),
            "current_principal": Decimal("25000.00"),
            "interest_rate": Decimal("0.05000"),
            "payment_day": 15,
        })()

        proj = get_loan_projection(params)
        assert proj.remaining_months == 0
        assert proj.schedule == []
        # Contractual payment is still computed from original terms.
        assert proj.summary.monthly_payment > Decimal("0.00")
        assert proj.summary.total_interest == Decimal("0.00")

    def test_contractual_payment_uses_original_principal(self):
        """Monthly payment reflects original loan terms, not current balance.

        Regression test: a $35,000 auto loan at 3.25%/72mo with $18,000
        remaining should show ~$536.87/mo (the contractual payment), not
        ~$1,663 (re-amortizing $18k over 11 remaining months).
        """
        params = type("P", (), {
            "origination_date": date(2021, 2, 1),
            "term_months": 72,
            "original_principal": Decimal("35000.00"),
            "current_principal": Decimal("18000.00"),
            "interest_rate": Decimal("0.03250"),
            "payment_day": 1,
        })()

        # Contractual payment: amortize(35000, 0.0325, 72).
        expected = calculate_monthly_payment(
            Decimal("35000.00"), Decimal("0.03250"), 72,
        )

        proj = get_loan_projection(params)
        assert proj.summary.monthly_payment == expected
        # Must be around $537, definitely not $1,663.
        assert proj.summary.monthly_payment < Decimal("600")
        assert proj.summary.monthly_payment > Decimal("500")


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

    # ── get_loan_projection consistency ────────────────────────────

    def test_get_loan_projection_wraps_individual_functions(self):
        """get_loan_projection must return results consistent with calling
        calculate_summary and generate_schedule individually.

        Section 5 may refactor the projection pipeline.  This ensures the
        wrapper stays in sync with the underlying functions.

        Note: get_loan_projection uses schedule_start (defaulting to
        today's 1st) as the origination_date for summary/schedule, and
        computes remaining_months from today.  We must replicate that
        exact behavior for a valid cross-check.
        """
        schedule_start = date.today().replace(day=1)

        params = type("P", (), {
            "origination_date": self.ORIGINATION,
            "term_months": self.MONTHS,
            "original_principal": self.PRINCIPAL,
            "current_principal": self.PRINCIPAL,
            "interest_rate": self.RATE,
            "payment_day": self.PAYMENT_DAY,
        })()

        projection = get_loan_projection(params, schedule_start=schedule_start)

        # Replicate what get_loan_projection does internally:
        # summary uses schedule_start as origination_date.
        standalone_summary = calculate_summary(
            self.PRINCIPAL, self.RATE, projection.remaining_months,
            schedule_start, self.PAYMENT_DAY, self.MONTHS,
            original_principal=self.PRINCIPAL,
        )

        assert projection.summary.monthly_payment == standalone_summary.monthly_payment
        assert projection.summary.total_interest == standalone_summary.total_interest
        assert projection.summary.payoff_date == standalone_summary.payoff_date

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
