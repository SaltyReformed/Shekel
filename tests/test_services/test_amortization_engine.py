"""
Tests for the amortization engine service.
"""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import pytest

from app.services.amortization_engine import (
    PaymentRecord,
    RateChangeRecord,
    calculate_monthly_payment,
    calculate_payoff_by_date,
    calculate_remaining_months,
    project_forward,
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

        # Project forward at the converged extra and confirm payoff lands
        # at or before the target.  The same engine primitive
        # ``calculate_payoff_by_date`` uses internally, so the cross-
        # check stays consistent with the binary search.
        contractual = calculate_monthly_payment(
            Decimal("200000"), Decimal("0.065"), 360,
        )
        starting_date = date(2026, 2, 1)  # origination + 1 month
        schedule_at = project_forward(
            starting_balance=Decimal("200000"),
            starting_date=starting_date,
            annual_rate=Decimal("0.065"),
            remaining_months=360,
            payment_day=1,
            contractual_payment=contractual,
            extra_monthly=Decimal("478.08"),
        )
        assert schedule_at[-1].payment_date <= date(2041, 1, 1), (
            f"$478.08 extra should pay off by 2041-01-01, "
            f"but last payment is {schedule_at[-1].payment_date}"
        )

        # And $478.07 (one penny less) must NOT achieve it -- pins the
        # binary search's penny-level discrimination.
        schedule_under = project_forward(
            starting_balance=Decimal("200000"),
            starting_date=starting_date,
            annual_rate=Decimal("0.065"),
            remaining_months=360,
            payment_day=1,
            contractual_payment=contractual,
            extra_monthly=Decimal("478.07"),
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


class TestPayoffByDateProjectForward:
    """C7-1..C7-4, C7-6: lock ``calculate_payoff_by_date`` behaviour
    after the migration onto :func:`project_forward`.

    Commit 7 of the amortization-engine split rewrites the function
    so both the standard schedule and the binary-search inner loop
    call ``project_forward`` instead of ``generate_schedule``.  These
    tests pin the externally observable behaviour: API contracts for
    the three early-return cases (C7-1 / C7-2 / C7-3), byte-identical
    output across five representative inputs (C7-4), and the
    structural guarantee that no ``generate_schedule`` reference
    remains inside the function body (C7-6).
    """

    def test_calculate_payoff_by_date_target_in_past(self):
        """C7-1: target_date before the projection start returns None.

        ``starting_date = origination_date + 1 month``; a target_date
        before ``starting_date`` yields ``target_months <= 0`` which
        is the legacy ``None`` short-circuit (no extra payment can
        change history).
        """
        result = calculate_payoff_by_date(
            current_principal=Decimal("200000"),
            annual_rate=Decimal("0.065"),
            remaining_months=360,
            target_date=date(2025, 12, 1),
            origination_date=date(2026, 1, 1),
            payment_day=1,
        )
        assert result is None

    def test_calculate_payoff_by_date_target_already_achieved(self):
        """C7-2: target after the standard payoff returns Decimal('0.00').

        Standard schedule for $200k / 6.5% / 360 months pays off on
        2056-01-01; target 2060-01-01 is later, so no extra is
        required and the function short-circuits before binary search.
        """
        result = calculate_payoff_by_date(
            current_principal=Decimal("200000"),
            annual_rate=Decimal("0.065"),
            remaining_months=360,
            target_date=date(2060, 1, 1),
            origination_date=date(2026, 1, 1),
            payment_day=1,
        )
        assert result == Decimal("0.00")

    def test_calculate_payoff_by_date_converges(self):
        """C7-3: binary search converges to a Decimal within $0.01.

        $200k / 6.5% / 360 months, target 2041-01-01 (~15-year payoff)
        converges deterministically to ``Decimal('478.08')`` -- the
        same value the legacy implementation produced.  Hand-derived
        via the bisection's convergence criterion (hi - lo <= 0.01).
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
            f"Expected $478.08 required extra, got {result}"
        )

    @pytest.mark.parametrize("label,kwargs,expected", [
        # Pre-commit values captured from the legacy generate_schedule
        # implementation on 2026-05-22 before the project_forward
        # migration.  Any drift here is a real regression.
        (
            "basic_30yr",
            dict(
                current_principal=Decimal("200000"),
                annual_rate=Decimal("0.065"),
                remaining_months=360,
                target_date=date(2041, 1, 1),
                origination_date=date(2026, 1, 1),
                payment_day=1,
            ),
            Decimal("478.08"),
        ),
        (
            "partial_paid_with_original_terms",
            dict(
                current_principal=Decimal("150000"),
                annual_rate=Decimal("0.065"),
                remaining_months=240,
                target_date=date(2038, 1, 1),
                origination_date=date(2026, 1, 1),
                payment_day=1,
                original_principal=Decimal("200000"),
                term_months=360,
            ),
            Decimal("238.75"),
        ),
        (
            "15yr_target_5_percent",
            dict(
                current_principal=Decimal("250000"),
                annual_rate=Decimal("0.05"),
                remaining_months=360,
                target_date=date(2035, 6, 1),
                origination_date=date(2026, 1, 1),
                payment_day=1,
            ),
            Decimal("1436.42"),
        ),
        (
            "short_horizon_payment_day_15",
            dict(
                current_principal=Decimal("50000"),
                annual_rate=Decimal("0.04"),
                remaining_months=120,
                target_date=date(2030, 1, 1),
                origination_date=date(2026, 1, 1),
                payment_day=15,
            ),
            Decimal("644.88"),
        ),
        (
            "large_loan_7_percent",
            dict(
                current_principal=Decimal("500000"),
                annual_rate=Decimal("0.07"),
                remaining_months=360,
                target_date=date(2045, 1, 1),
                origination_date=date(2026, 1, 1),
                payment_day=1,
            ),
            Decimal("644.46"),
        ),
    ])
    def test_calculate_payoff_by_date_unchanged_vs_pre_commit(
        self, label, kwargs, expected,
    ):
        """C7-4: byte-identical Decimal output across 5 representative inputs.

        The migration from ``generate_schedule`` to ``project_forward``
        is behavior-preserving (per D-F of the implementation plan).
        These five cases are the assert-unchanged lock that proves it:
        if any value drifts even by one cent, that is a real
        regression caught here.
        """
        result = calculate_payoff_by_date(**kwargs)
        assert result == expected, (
            f"{label}: expected {expected}, got {result}"
        )

    def test_no_generate_schedule_in_calculate_payoff_by_date(self):
        """C7-6: ``calculate_payoff_by_date`` no longer calls generate_schedule.

        Structural guarantee that the migration is complete -- the
        function body slice between its ``def`` and the next top-level
        ``def`` must contain zero CALLS to ``generate_schedule`` (i.e.,
        the ``generate_schedule(`` syntactic form).  Docstring / comment
        mentions are exempt because they retain useful historical
        context after the rewrite.  Catches future merges that
        accidentally reintroduce the old engine call.
        """
        engine_path = (
            Path(__file__).resolve().parent.parent.parent
            / "app" / "services" / "amortization_engine.py"
        )
        source = engine_path.read_text(encoding="utf-8")
        marker = "def calculate_payoff_by_date("
        start = source.index(marker)
        # The function body ends at the next top-level ``def`` /
        # class definition.  Slice from ``start`` to whichever comes
        # first; if neither, slice to end-of-file.
        next_def = source.find("\ndef ", start + len(marker))
        next_class = source.find("\nclass ", start + len(marker))
        candidates = [c for c in (next_def, next_class) if c != -1]
        end = min(candidates) if candidates else len(source)
        body = source[start:end]
        assert "generate_schedule(" not in body, (
            "calculate_payoff_by_date must not call "
            "generate_schedule() after the Commit 7 migration; "
            "found a call site in the function body."
        )


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


# ── Section 5 Regression Baseline (re-purposed for project_forward) ──


class TestAmortizationEngineRegression:
    """Regression baseline pinning ``project_forward``'s behaviour on a
    representative 30-year mortgage scenario.

    Originally written against ``generate_schedule`` / ``calculate_summary``
    during the Section 5 cleanup sprint.  Re-purposed in Commit 9 of the
    amortization-engine split (deletion of the legacy entry points) to
    drive the same scenarios through ``project_forward``.  The lock
    values are unchanged because the new primitive computes the same
    schedule for the no-payments / no-override / no-rate-change inputs
    these tests exercise.

    Focus areas: payoff round-trip accuracy via the binary search in
    ``calculate_payoff_by_date``, known-value Decimal locks for the
    $250k / 6.5% / 360 scenario, schedule invariants
    (payment = principal + interest; balance monotonically decreasing;
    Decimal types).
    """

    # ── Standard test scenario ─────────────────────────────────────
    # $250,000 mortgage at 6.5% for 30 years, payment day 1,
    # origination 2024-01-01.  Values independently verified via the
    # standard amortization formula M = P * [r(1+r)^n] / [(1+r)^n - 1].
    # First payment date = origination + 1 month = 2024-02-01.

    PRINCIPAL = Decimal("250000.00")
    RATE = Decimal("0.065")
    MONTHS = 360
    ORIGINATION = date(2024, 1, 1)
    PAYMENT_DAY = 1
    STARTING_DATE = date(2024, 2, 1)
    CONTRACTUAL_PAYMENT = Decimal("1580.17")

    def _project(
        self,
        principal: Decimal | None = None,
        rate: Decimal | None = None,
        months: int | None = None,
        extra_monthly: Decimal = Decimal("0.00"),
    ):
        """Project forward at the standard contractual payment.

        Helper that captures the boilerplate: derive the contractual
        payment from the inputs (the value a fixed-rate loan keeps for
        its life) and run ``project_forward`` from the standard
        starting state.  Equivalent to the legacy
        ``generate_schedule(principal, rate, months, extra=..., ...)``
        call but expressed in primitive terms.
        """
        principal = self.PRINCIPAL if principal is None else principal
        rate = self.RATE if rate is None else rate
        months = self.MONTHS if months is None else months
        contractual = calculate_monthly_payment(principal, rate, months)
        return project_forward(
            starting_balance=principal,
            starting_date=self.STARTING_DATE,
            annual_rate=rate,
            remaining_months=months,
            payment_day=self.PAYMENT_DAY,
            contractual_payment=contractual,
            extra_monthly=extra_monthly,
        )

    # ── Payoff round-trip ──────────────────────────────────────────

    def test_payoff_by_date_round_trip(self):
        """``calculate_payoff_by_date`` result fed back into
        ``project_forward`` must produce a payoff on or before the
        target date.  Catches rounding drift in the binary search.
        """
        target = date(2044, 1, 1)  # 20-year payoff target
        extra = calculate_payoff_by_date(
            self.PRINCIPAL, self.RATE, self.MONTHS,
            target, self.ORIGINATION, self.PAYMENT_DAY,
        )
        assert extra is not None
        assert extra > Decimal("0")

        accel = self._project(extra_monthly=extra)
        assert accel[-1].payment_date <= target
        assert accel[-1].remaining_balance == Decimal("0.00")

    # ── Known-value regression locks ───────────────────────────────

    def test_standard_schedule_known_values(self):
        """Lock down exact values for the standard $250k / 6.5% / 360
        scenario via ``project_forward``.

        Hand arithmetic at $250,000 / 6.5% / 360, payment_day=1,
        starting 2024-02-01:
          monthly_payment = M(250000, 0.065, 360) = $1,580.17
          row 1: interest = 250000.00 * (0.065 / 12) = $1,354.17;
                 principal = 1580.17 - 1354.17 = $226.00;
                 balance = 250000.00 - 226.00 = $249,774.00.
          row 360: final balance == $0.00 by the ``is_final``
                   absorbing branch.
        """
        schedule = self._project()

        # First row: mostly interest, small principal.
        assert schedule[0].month == 1
        assert schedule[0].payment == self.CONTRACTUAL_PAYMENT
        assert schedule[0].interest == Decimal("1354.17")
        assert schedule[0].principal == Decimal("226.00")
        assert schedule[0].remaining_balance == Decimal("249774.00")

        # Last row: final balance is exactly zero.
        assert schedule[-1].remaining_balance == Decimal("0.00")
        assert len(schedule) == self.MONTHS

        # Totals.
        total_interest = sum(r.interest for r in schedule)
        total_principal = sum(r.principal + r.extra_payment for r in schedule)
        assert total_interest == Decimal("318861.58")
        assert total_principal == self.PRINCIPAL

    def test_contractual_payment_known_value(self):
        """``calculate_monthly_payment`` for the standard scenario is
        $1,580.17 (the contractual P&I the projection uses).
        """
        payment = calculate_monthly_payment(
            self.PRINCIPAL, self.RATE, self.MONTHS,
        )
        assert payment == self.CONTRACTUAL_PAYMENT

    # ── Decimal type enforcement ───────────────────────────────────

    def test_all_schedule_monetary_fields_are_decimal(self):
        """Every monetary field in every projected row must be Decimal.

        Float values in amortization output would be a critical
        precision bug in a financial application.
        """
        schedule = self._project()
        for row in schedule:
            assert isinstance(row.payment, Decimal), (
                f"Row {row.month}: payment is {type(row.payment).__name__}"
            )
            assert isinstance(row.principal, Decimal), (
                f"Row {row.month}: principal is "
                f"{type(row.principal).__name__}"
            )
            assert isinstance(row.interest, Decimal), (
                f"Row {row.month}: interest is "
                f"{type(row.interest).__name__}"
            )
            assert isinstance(row.extra_payment, Decimal), (
                f"Row {row.month}: extra_payment is "
                f"{type(row.extra_payment).__name__}"
            )
            assert isinstance(row.remaining_balance, Decimal), (
                f"Row {row.month}: remaining_balance is "
                f"{type(row.remaining_balance).__name__}"
            )

    # ── Edge cases ─────────────────────────────────────────────────

    def test_zero_extra_payment_equals_default_schedule(self):
        """Passing ``extra_monthly=0`` produces the same schedule as
        omitting the parameter (default ``Decimal("0.00")``).
        """
        standard = self._project()
        with_zero = self._project(extra_monthly=Decimal("0.00"))
        assert len(standard) == len(with_zero)
        for s_row, z_row in zip(standard, with_zero):
            assert s_row.payment == z_row.payment
            assert s_row.principal == z_row.principal
            assert s_row.interest == z_row.interest
            assert s_row.remaining_balance == z_row.remaining_balance

    def test_huge_extra_payment_one_month_payoff(self):
        """An ``extra_monthly`` vastly exceeding principal pays off in
        one month.  The projection caps extra at
        ``balance - principal_portion`` so the row absorbs the residue
        and balance closes at zero.
        """
        schedule = self._project(extra_monthly=Decimal("999999.00"))
        assert len(schedule) == 1
        assert schedule[0].remaining_balance == Decimal("0.00")

    def test_one_remaining_month_schedule(self):
        """Loan with 1 remaining month produces a single-row schedule
        whose payment covers principal plus one month of interest.

        Hand arithmetic at $5,000 / 6.5% / 1 month:
          monthly_rate = 0.065 / 12 = 0.00541667
          interest = 5000.00 * 0.00541667 = $27.08
          principal = 5000.00; balance = $0.00.
        """
        principal = Decimal("5000.00")
        schedule = self._project(principal=principal, months=1)
        assert len(schedule) == 1
        assert schedule[0].interest == Decimal("27.08")
        assert schedule[0].principal == principal
        assert schedule[0].remaining_balance == Decimal("0.00")

    def test_schedule_payment_equals_principal_plus_interest(self):
        """For every row, payment (base) equals principal + interest.

        The schedule separates the base payment from the
        ``extra_payment`` field; this invariant holds regardless of
        rounding adjustments.  A mismatch indicates broken arithmetic.
        """
        schedule = self._project(extra_monthly=Decimal("100.00"))
        for row in schedule:
            expected_base = row.principal + row.interest
            assert row.payment == expected_base, (
                f"Row {row.month}: payment={row.payment} != "
                f"principal({row.principal}) + interest({row.interest}) "
                f"= {expected_base}"
            )

    def test_balance_monotonically_decreasing(self):
        """Remaining balance never increases between consecutive rows.

        With a contractual payment plus extra and no negative
        amortization (override below interest), balance must decrease
        monotonically.
        """
        schedule = self._project(extra_monthly=Decimal("50.00"))
        for i in range(1, len(schedule)):
            assert (
                schedule[i].remaining_balance
                <= schedule[i - 1].remaining_balance
            ), (
                f"Balance increased from row {i} to row {i + 1}: "
                f"{schedule[i - 1].remaining_balance} -> "
                f"{schedule[i].remaining_balance}"
            )


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


class TestProjectForward:
    """Tests for ``project_forward``.

    The second half of the amortization-engine split (Commit 2 of
    ``docs/plans/2026-05-21-amortization-engine-split-implementation.md``;
    architectural plan
    ``docs/plans/2026-05-21-amortization-engine-split-replay-projection.md``).
    Verifies that projection is a pure forward-only function of a
    known starting state:

      - ``extra_monthly`` lives only on this surface;
      - ``monthly_override`` routes the user's planned payments
        through a forward-only channel;
      - override months never receive ``extra_monthly`` (C2-4 -- the
        architectural plan's "critical regression-prevention
        assertion": override + extra cannot interact to silently
        suppress or double-apply acceleration);
      - negative amortization, overpayment cap, and ARM rate-change
        re-amortization all mirror ``generate_schedule``'s existing
        behavior on the projection side;
      - every projected row carries ``is_confirmed=False``.

    Standard scenario: $300,000 / 6% / 360 months / payment_day=1
    with a known starting date of 2026-02-01.  Monthly P&I:
        M = P * [r(1+r)^n] / [(1+r)^n - 1]
        r = 0.06/12 = 0.005, n = 360
        (1.005)^360 ~ 6.022575
        M = 300000 * 0.005 * 6.022575 / 5.022575 = $1,798.65
    """

    PRINCIPAL = Decimal("300000.00")
    RATE = Decimal("0.06")
    TERM_MONTHS = 360
    PAYMENT_DAY = 1
    CONTRACTUAL_PAYMENT = Decimal("1798.65")
    STARTING_DATE = date(2026, 2, 1)

    # ── C2-1: no override, no extra -> contractual schedule ───────

    def test_no_override_no_extra_is_contractual(self):
        """C2-1: with no override and no extra, ``project_forward``
        produces the standard contractual schedule over
        ``remaining_months``.

        Hand arithmetic at $300,000 / 6% / 360, payment_day=1, first
        payment 2026-02-01:
          row 1: interest = 300000.00 * 0.005 = $1,500.00;
                 principal = 1798.65 - 1500.00 = $298.65;
                 balance = $299,701.35
          row 360: schedule terminates with balance == $0.00 by the
                   ``is_final`` absorbing branch (final row rolls up
                   any sub-penny residue).
        ``extra_payment`` is $0.00 on every row.
        """
        rows = project_forward(
            starting_balance=self.PRINCIPAL,
            starting_date=self.STARTING_DATE,
            annual_rate=self.RATE,
            remaining_months=self.TERM_MONTHS,
            payment_day=self.PAYMENT_DAY,
            contractual_payment=self.CONTRACTUAL_PAYMENT,
        )
        assert len(rows) == self.TERM_MONTHS
        # Row 1.
        assert rows[0].payment_date == date(2026, 2, 1)
        assert rows[0].interest == Decimal("1500.00")
        assert rows[0].principal == Decimal("298.65")
        assert rows[0].extra_payment == Decimal("0.00")
        assert rows[0].remaining_balance == Decimal("299701.35")
        assert rows[0].is_confirmed is False
        # Every row uses the contractual payment (sans final-row
        # absorption rounding), no extra.
        for row in rows[:-1]:
            assert row.payment == self.CONTRACTUAL_PAYMENT
            assert row.extra_payment == Decimal("0.00")
            assert row.is_confirmed is False
        # Final row absorbs the residue and closes the balance at 0.
        assert rows[-1].payment_date == date(2056, 1, 1)
        assert rows[-1].remaining_balance == Decimal("0.00")
        assert rows[-1].extra_payment == Decimal("0.00")

    # ── C2-2: monthly_override only (no extra) ────────────────────

    def test_monthly_override_only(self):
        """C2-2: an override entry replaces the contractual payment
        for that month, with ``extra_payment == 0`` on the row.

        Hand arithmetic at the start of month 5 (June 2026):
          balance before June = $298,796.42 (after four contractual
          payments at $1,798.65).
          June interest = 298796.42 * 0.005 = 1493.98210 -> $1,493.98
          override = $2,000.00 -> principal = 2000.00 - 1493.98
                                            = $506.02
          balance after June = 298796.42 - 506.02 = $298,290.40
        Non-override months use the contractual payment.
        """
        rows = project_forward(
            starting_balance=self.PRINCIPAL,
            starting_date=self.STARTING_DATE,
            annual_rate=self.RATE,
            remaining_months=self.TERM_MONTHS,
            payment_day=self.PAYMENT_DAY,
            contractual_payment=self.CONTRACTUAL_PAYMENT,
            monthly_override={(2026, 6): Decimal("2000.00")},
        )
        june = next(r for r in rows if r.payment_date == date(2026, 6, 1))
        assert june.payment == Decimal("2000.00")
        assert june.interest == Decimal("1493.98")
        assert june.principal == Decimal("506.02")
        assert june.extra_payment == Decimal("0.00")
        assert june.remaining_balance == Decimal("298290.40")
        # Adjacent non-override months keep the contractual payment.
        may = next(r for r in rows if r.payment_date == date(2026, 5, 1))
        assert may.payment == self.CONTRACTUAL_PAYMENT
        assert may.extra_payment == Decimal("0.00")
        july = next(r for r in rows if r.payment_date == date(2026, 7, 1))
        assert july.payment == self.CONTRACTUAL_PAYMENT
        assert july.extra_payment == Decimal("0.00")

    # ── C2-3: extra_monthly only, no override ─────────────────────

    def test_extra_monthly_only_no_override(self):
        """C2-3: ``extra_monthly`` applies to every non-final month;
        the schedule shortens to ~17.7 years and the final row
        absorbs the residue with ``extra_payment == 0``.

        Hand arithmetic at row 1:
          interest = 300000.00 * 0.005 = $1,500.00
          principal = 1798.65 - 1500.00 = $298.65
          extra = $500.00 (uncapped: 500.00 < 299701.35)
          balance = 300000.00 - 298.65 - 500.00 = $299,201.35
        At $300,000 / 6% with $500 extra, the loan amortizes in 212
        rows; the final row's ``is_final`` branch absorbs the balance
        and reports ``extra_payment == $0.00``.
        """
        rows = project_forward(
            starting_balance=self.PRINCIPAL,
            starting_date=self.STARTING_DATE,
            annual_rate=self.RATE,
            remaining_months=self.TERM_MONTHS,
            payment_day=self.PAYMENT_DAY,
            contractual_payment=self.CONTRACTUAL_PAYMENT,
            extra_monthly=Decimal("500.00"),
        )
        # Row 1 hand-computed values.
        assert rows[0].payment == self.CONTRACTUAL_PAYMENT
        assert rows[0].extra_payment == Decimal("500.00")
        assert rows[0].principal == Decimal("298.65")
        assert rows[0].interest == Decimal("1500.00")
        assert rows[0].remaining_balance == Decimal("299201.35")
        # Schedule shortens substantially (under the unaccelerated
        # 360 months).
        assert len(rows) == 212
        # Final row absorbs residue.
        assert rows[-1].remaining_balance == Decimal("0.00")
        # Every non-final row has the configured extra applied.
        for row in rows[:-1]:
            assert row.extra_payment == Decimal("500.00")

    # ── C2-4: override + extra -- the regression-prevention lock ──

    def test_override_plus_extra_extra_not_added_to_override_months(self):
        """C2-4: when both an override and ``extra_monthly`` are
        present, the override month uses the override as the total
        payment and ``extra_payment == 0``; non-override months use
        contractual + extra.

        CRITICAL: this is the primitive-level regression lock that
        makes the architectural bug structurally impossible.  In the
        old ``generate_schedule`` flow a projected payment record
        would suppress ``extra_monthly`` silently (the gate was "any
        record present") -- now override and extra are independent
        parameters of ``project_forward``, and the override path
        unconditionally sets ``extra = $0.00``.

        Hand arithmetic for June 2026 (the override month, $2000):
          balance before June = $295,748.32 (after four
          contractual+$500-extra rows preceding); this is mechanical
          to compute and not re-derived in the assertion -- the test
          asserts the SHAPE of the result (payment == override,
          extra == 0).
        Hand arithmetic for July 2026 (no override, extra applies):
          July payment = $1,798.65 contractual
          July extra_payment = $500.00
        """
        rows = project_forward(
            starting_balance=self.PRINCIPAL,
            starting_date=self.STARTING_DATE,
            annual_rate=self.RATE,
            remaining_months=self.TERM_MONTHS,
            payment_day=self.PAYMENT_DAY,
            contractual_payment=self.CONTRACTUAL_PAYMENT,
            monthly_override={(2026, 6): Decimal("2000.00")},
            extra_monthly=Decimal("500.00"),
        )
        june = next(r for r in rows if r.payment_date == date(2026, 6, 1))
        assert june.payment == Decimal("2000.00")
        # The load-bearing assertion: extra is NEVER added to an
        # override month, even when ``extra_monthly`` is set.
        assert june.extra_payment == Decimal("0.00")
        july = next(r for r in rows if r.payment_date == date(2026, 7, 1))
        assert july.payment == self.CONTRACTUAL_PAYMENT
        assert july.extra_payment == Decimal("500.00")
        # Every override-less month past the override carries extra.
        post_override = [
            r for r in rows
            if r.payment_date > date(2026, 6, 1) and r != rows[-1]
        ]
        for row in post_override:
            assert row.extra_payment == Decimal("500.00")

    # ── C2-5: override below interest -> negative amortization ────

    def test_override_below_interest_negative_amortization(self):
        """C2-5: an override below the period's interest produces
        negative ``principal_portion`` and the balance grows.

        Hand arithmetic at row 1 with override $50 and $1500 interest:
          interest = 300000.00 * 0.005 = $1,500.00
          principal = 50.00 - 1500.00 = -$1,450.00 (negative am)
          balance = 300000.00 - (-1450.00) = $301,450.00
        Existing engine behavior on ``generate_schedule``'s
        payment-record branch is preserved.
        """
        rows = project_forward(
            starting_balance=self.PRINCIPAL,
            starting_date=self.STARTING_DATE,
            annual_rate=self.RATE,
            remaining_months=self.TERM_MONTHS,
            payment_day=self.PAYMENT_DAY,
            contractual_payment=self.CONTRACTUAL_PAYMENT,
            monthly_override={(2026, 2): Decimal("50.00")},
        )
        feb = rows[0]
        assert feb.payment_date == date(2026, 2, 1)
        assert feb.payment == Decimal("50.00")
        assert feb.interest == Decimal("1500.00")
        # Negative principal portion is valid -- it represents
        # unpaid interest capitalized into the balance.
        assert feb.principal == Decimal("-1450.00")
        assert feb.extra_payment == Decimal("0.00")
        # 300000.00 - (-1450.00) = 301450.00.
        assert feb.remaining_balance == Decimal("301450.00")

    # ── C2-6: ARM rate change during projection ───────────────────

    def test_arm_rate_change_during_projection(self):
        """C2-6: when ``rate_changes_remaining`` has an entry whose
        ``effective_date`` is reached during the projection, the
        engine re-amortizes the remaining balance over remaining
        months at the new rate.

        Setup: 30 yr / $300,000 / starts at 6%; rate change at
        2027-02-01 to 7.5%.  Hand arithmetic at month 13 (Feb 2027):
          balance entering Feb 2027 (post-Jan 2027) = $296,316.00
          new monthly_rate = 0.075 / 12 = 0.00625
          interest = 296316.00 * 0.00625 = 1851.97500 -> $1,851.98
        Existing engine ARM behavior is preserved by construction
        (project_forward shares the rate-change handler shape with
        generate_schedule).
        """
        contractual = calculate_monthly_payment(
            self.PRINCIPAL, self.RATE, self.TERM_MONTHS,
        )
        rows = project_forward(
            starting_balance=self.PRINCIPAL,
            starting_date=self.STARTING_DATE,
            annual_rate=self.RATE,
            remaining_months=self.TERM_MONTHS,
            payment_day=self.PAYMENT_DAY,
            contractual_payment=contractual,
            rate_changes_remaining=[
                RateChangeRecord(
                    effective_date=date(2027, 2, 1),
                    interest_rate=Decimal("0.075"),
                ),
            ],
        )
        # Row 12 (month 13, Feb 2027) is the first row at the new
        # rate.  Pre-change row 11 (Jan 2027, month 12) keeps the
        # base 6% rate.
        jan_2027 = rows[11]
        feb_2027 = rows[12]
        assert jan_2027.payment_date == date(2027, 1, 1)
        assert jan_2027.interest_rate == Decimal("0.06")
        assert feb_2027.payment_date == date(2027, 2, 1)
        assert feb_2027.interest_rate == Decimal("0.075")
        # interest at the new rate: 296316.00 * 0.075/12.
        assert feb_2027.interest == Decimal("1851.98")
        # Re-amortized payment is strictly greater than the base
        # 6% contractual ($1798.65); the engine recomputes
        # monthly_payment at the rate-change boundary.
        assert feb_2027.payment > self.CONTRACTUAL_PAYMENT

    # ── C2-7: overpayment cap on the final row ────────────────────

    def test_overpayment_cap_final_row(self):
        """C2-7: when extra (or an override) would drive the balance
        below zero, the final row absorbs the remaining balance
        exactly, ``extra_payment`` is capped, and the closing balance
        is $0.00.

        Hand arithmetic at $1,000 balance, 6%, contractual $1,798.65,
        extra $500 over a 360-month projection:
          interest = 1000.00 * 0.005 = $5.00
          principal (contractual - interest) = 1798.65 - 5.00 = $1,793.65
          ``is_final`` triggers because principal >= balance:
            principal := 1000.00
            actual_payment = 1000.00 + 5.00 = $1,005.00
            extra := $0.00 (final-row absorption clears extra)
            balance := $0.00
        The schedule terminates in a single row.
        """
        rows = project_forward(
            starting_balance=Decimal("1000.00"),
            starting_date=self.STARTING_DATE,
            annual_rate=self.RATE,
            remaining_months=self.TERM_MONTHS,
            payment_day=self.PAYMENT_DAY,
            contractual_payment=self.CONTRACTUAL_PAYMENT,
            extra_monthly=Decimal("500.00"),
        )
        assert len(rows) == 1
        final = rows[0]
        assert final.payment == Decimal("1005.00")
        assert final.principal == Decimal("1000.00")
        assert final.interest == Decimal("5.00")
        assert final.extra_payment == Decimal("0.00")
        assert final.remaining_balance == Decimal("0.00")

    # ── C2-8: zero starting balance returns empty ─────────────────

    def test_zero_starting_balance_returns_empty(self):
        """C2-8: ``starting_balance == 0`` returns an empty list
        (the loan is already paid off; nothing to project).
        """
        rows = project_forward(
            starting_balance=Decimal("0.00"),
            starting_date=self.STARTING_DATE,
            annual_rate=self.RATE,
            remaining_months=self.TERM_MONTHS,
            payment_day=self.PAYMENT_DAY,
            contractual_payment=self.CONTRACTUAL_PAYMENT,
        )
        assert rows == []
        # Negative balance is treated the same way (defensive).
        rows = project_forward(
            starting_balance=Decimal("-10.00"),
            starting_date=self.STARTING_DATE,
            annual_rate=self.RATE,
            remaining_months=self.TERM_MONTHS,
            payment_day=self.PAYMENT_DAY,
            contractual_payment=self.CONTRACTUAL_PAYMENT,
        )
        assert rows == []

    # ── C2-9: zero remaining_months returns empty ─────────────────

    def test_zero_remaining_months_returns_empty(self):
        """C2-9: ``remaining_months == 0`` returns an empty list
        (no time horizon for projection).
        """
        rows = project_forward(
            starting_balance=self.PRINCIPAL,
            starting_date=self.STARTING_DATE,
            annual_rate=self.RATE,
            remaining_months=0,
            payment_day=self.PAYMENT_DAY,
            contractual_payment=self.CONTRACTUAL_PAYMENT,
        )
        assert rows == []
        # Negative remaining_months is treated the same way.
        rows = project_forward(
            starting_balance=self.PRINCIPAL,
            starting_date=self.STARTING_DATE,
            annual_rate=self.RATE,
            remaining_months=-3,
            payment_day=self.PAYMENT_DAY,
            contractual_payment=self.CONTRACTUAL_PAYMENT,
        )
        assert rows == []

    # ── C2-10: hand-computed payoff lock ──────────────────────────

    def test_hand_computed_payoff_with_extra(self):
        """C2-10: $279,985 starting balance / 6% / 336 remaining
        months / $200 extra produces the hand-computed payoff baked
        into the architectural plan.

        Hand-arithmetic anchor for the contractual payment used by
        the projection:
          r = 0.005, n = 336
          (1.005)^336 ~ 5.34324
          M = 279985 * 0.005 * 5.34324 / (5.34324 - 1)
            = 7479.4 / 4.34324
            ~ $1,722.25
        Independently-computed payoff: 262 rows; final pay_date
        2048-02-29 (payment_day=30 clamped to 29 in February); final
        ``remaining_balance == $0.00``.

        Pre-Commit-9 this test also cross-checked against
        ``generate_schedule`` for row-by-row identity; Commit 9
        deletes ``generate_schedule`` and the cross-check.  The
        hand-anchored payoff is the lasting regression lock.
        """
        starting = Decimal("279985.00")
        remaining = 336
        extra = Decimal("200.00")
        payment_day = 30
        contractual = calculate_monthly_payment(
            starting, self.RATE, remaining,
        )
        # Hand-anchored contractual value.
        assert contractual == Decimal("1722.25")
        rows = project_forward(
            starting_balance=starting,
            starting_date=date(2026, 5, 30),
            annual_rate=self.RATE,
            remaining_months=remaining,
            payment_day=payment_day,
            contractual_payment=contractual,
            extra_monthly=extra,
        )
        # Hand-anchored independently-computed payoff.
        assert len(rows) == 262
        assert rows[-1].payment_date == date(2048, 2, 29)
        assert rows[-1].remaining_balance == Decimal("0.00")

    # ── C2-11: round_money is the only rounding boundary ──────────

    def test_round_money_is_only_rounding_boundary(self):
        """C2-11: ``project_forward``'s body contains no bare
        ``.quantize(Decimal("0.01"))`` without an explicit
        ``rounding=`` keyword (coding standards: rounding goes
        through ``round_money`` or an explicit ``ROUND_HALF_UP``).

        Source-level guard against silent ``ROUND_HALF_EVEN``
        regressions: a bare ``.quantize(Decimal("0.01"))`` would use
        Python's default banker's rounding and silently drift away
        from the project's hand-computed test assertions.  The
        ``round_money`` helper rejects ``float`` and pins
        ``ROUND_HALF_UP``; this test pins the helper as the only
        rounding surface inside the new primitive.
        """
        engine_source = Path(
            "app/services/amortization_engine.py"
        ).read_text(encoding="utf-8")
        # Slice out the project_forward body for the assertion.
        # ``calculate_payoff_by_date`` is the next top-level def
        # after Commit 9 of the amortization-engine split removed
        # ``calculate_summary``.
        marker = "def project_forward("
        next_def = "\ndef calculate_payoff_by_date("
        start = engine_source.index(marker)
        end = engine_source.index(next_def, start)
        body = engine_source[start:end]
        # No bare cents quantize without explicit rounding= keyword.
        assert '.quantize(Decimal("0.01"))' not in body
        assert '.quantize(TWO_PLACES)' not in body
        # round_money IS used (positive assertion that the helper
        # is the rounding boundary).
        assert "round_money(" in body
