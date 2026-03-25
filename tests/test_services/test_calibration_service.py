"""
Shekel Budget App -- Unit Tests for Calibration Service

Tests derive_effective_rates() and apply_calibration() with known
pay stub values, edge cases, and error conditions.
"""

from decimal import Decimal

import pytest

from app.exceptions import ValidationError
from app.services.calibration_service import (
    DerivedRates,
    apply_calibration,
    derive_effective_rates,
)


# ── Fake Objects ─────────────────────────────────────────────────


class FakeCalibration:
    """Minimal stand-in for a CalibrationOverride with effective rates."""

    def __init__(self, federal_rate, state_rate, ss_rate, medicare_rate):
        self.effective_federal_rate = Decimal(str(federal_rate))
        self.effective_state_rate = Decimal(str(state_rate))
        self.effective_ss_rate = Decimal(str(ss_rate))
        self.effective_medicare_rate = Decimal(str(medicare_rate))


# ── derive_effective_rates Tests ─────────────────────────────────


class TestDeriveEffectiveRates:
    """Tests for derive_effective_rates()."""

    def test_basic_rate_derivation(self):
        """Derive rates from a typical pay stub with known values.

        Hand calculation:
          gross = $2,307.69
          taxable = $2,107.69  (gross - $200 pre-tax 401k)
          federal = $153.08  -> rate = 153.08 / 2107.69 = 0.07261
          state = $94.85     -> rate = 94.85 / 2107.69 = 0.04499
          ss = $143.08       -> rate = 143.08 / 2307.69 = 0.06200
          medicare = $33.46  -> rate = 33.46 / 2307.69 = 0.01450
        """
        result = derive_effective_rates(
            actual_gross_pay=Decimal("2307.69"),
            actual_federal_tax=Decimal("153.08"),
            actual_state_tax=Decimal("94.85"),
            actual_social_security=Decimal("143.08"),
            actual_medicare=Decimal("33.46"),
            taxable_income=Decimal("2107.69"),
        )

        assert isinstance(result, DerivedRates)
        # With 10-decimal precision, rates reproduce exact pennies.
        assert result.effective_federal_rate == Decimal("0.0726292766")
        assert result.effective_state_rate == Decimal("0.0450018741")
        assert result.effective_ss_rate == Decimal("0.0620013953")
        assert result.effective_medicare_rate == Decimal("0.0144993478")

    def test_zero_federal_tax_produces_zero_rate(self):
        """A pay stub with $0 federal tax produces a 0.00000 federal rate.

        This is valid -- e.g. a state with no income tax or low enough
        income that withholding rounds to zero.
        """
        result = derive_effective_rates(
            actual_gross_pay=Decimal("2000.00"),
            actual_federal_tax=Decimal("0.00"),
            actual_state_tax=Decimal("90.00"),
            actual_social_security=Decimal("124.00"),
            actual_medicare=Decimal("29.00"),
            taxable_income=Decimal("1800.00"),
        )

        assert result.effective_federal_rate == Decimal("0.0000000000")

    def test_zero_state_tax_produces_zero_rate(self):
        """A state with no income tax produces a zero state rate."""
        result = derive_effective_rates(
            actual_gross_pay=Decimal("2000.00"),
            actual_federal_tax=Decimal("150.00"),
            actual_state_tax=Decimal("0.00"),
            actual_social_security=Decimal("124.00"),
            actual_medicare=Decimal("29.00"),
            taxable_income=Decimal("1800.00"),
        )

        assert result.effective_state_rate == Decimal("0.0000000000")

    def test_zero_gross_pay_raises_error(self):
        """Gross pay of zero is rejected -- cannot derive FICA rates."""
        with pytest.raises(ValidationError, match="greater than zero"):
            derive_effective_rates(
                actual_gross_pay=Decimal("0"),
                actual_federal_tax=Decimal("0"),
                actual_state_tax=Decimal("0"),
                actual_social_security=Decimal("0"),
                actual_medicare=Decimal("0"),
                taxable_income=Decimal("0"),
            )

    def test_negative_gross_pay_raises_error(self):
        """Negative gross pay is rejected."""
        with pytest.raises(ValidationError, match="greater than zero"):
            derive_effective_rates(
                actual_gross_pay=Decimal("-100"),
                actual_federal_tax=Decimal("0"),
                actual_state_tax=Decimal("0"),
                actual_social_security=Decimal("0"),
                actual_medicare=Decimal("0"),
                taxable_income=Decimal("100"),
            )

    def test_zero_taxable_income_raises_error(self):
        """Zero taxable income is rejected -- cannot derive income tax rates."""
        with pytest.raises(ValidationError, match="Taxable income"):
            derive_effective_rates(
                actual_gross_pay=Decimal("2000.00"),
                actual_federal_tax=Decimal("0"),
                actual_state_tax=Decimal("0"),
                actual_social_security=Decimal("124.00"),
                actual_medicare=Decimal("29.00"),
                taxable_income=Decimal("0"),
            )

    def test_negative_taxable_income_raises_error(self):
        """Negative taxable income (misconfigured deductions) is rejected."""
        with pytest.raises(ValidationError, match="Taxable income"):
            derive_effective_rates(
                actual_gross_pay=Decimal("2000.00"),
                actual_federal_tax=Decimal("0"),
                actual_state_tax=Decimal("0"),
                actual_social_security=Decimal("124.00"),
                actual_medicare=Decimal("29.00"),
                taxable_income=Decimal("-500"),
            )

    def test_string_inputs_coerced_to_decimal(self):
        """String inputs are accepted and coerced to Decimal."""
        result = derive_effective_rates(
            actual_gross_pay="2000.00",
            actual_federal_tax="100.00",
            actual_state_tax="50.00",
            actual_social_security="124.00",
            actual_medicare="29.00",
            taxable_income="1800.00",
        )

        assert result.effective_federal_rate == Decimal("0.0555555556")
        assert result.effective_state_rate == Decimal("0.0277777778")

    def test_high_income_rates(self):
        """Higher income levels produce reasonable effective rates.

        $200k salary, $7692.31/period gross, ~$1800 federal (23.4% eff).
        """
        result = derive_effective_rates(
            actual_gross_pay=Decimal("7692.31"),
            actual_federal_tax=Decimal("1800.00"),
            actual_state_tax=Decimal("300.00"),
            actual_social_security=Decimal("476.92"),
            actual_medicare=Decimal("111.54"),
            taxable_income=Decimal("6942.31"),
        )

        # Federal: 1800 / 6942.31 at 10 places
        assert result.effective_federal_rate == Decimal("0.2592796922")
        # SS: 476.92 / 7692.31 at 10 places
        assert result.effective_ss_rate == Decimal("0.0619995814")


# ── apply_calibration Tests ──────────────────────────────────────


class TestApplyCalibration:
    """Tests for apply_calibration()."""

    def test_basic_calibration_application(self):
        """Apply known rates to a gross/taxable amount.

        gross = $2,307.69, taxable = $2,107.69
        With 10-decimal rates, the round-trip reproduces exact pennies.
        """
        cal = FakeCalibration(
            federal_rate="0.0726292766",
            state_rate="0.0450018741",
            ss_rate="0.0620013953",
            medicare_rate="0.0144993478",
        )

        result = apply_calibration(
            gross_biweekly=Decimal("2307.69"),
            taxable_biweekly=Decimal("2107.69"),
            calibration=cal,
        )

        assert result["federal"] == Decimal("153.08")
        assert result["state"] == Decimal("94.85")
        assert result["ss"] == Decimal("143.08")
        assert result["medicare"] == Decimal("33.46")

    def test_zero_rates_produce_zero_taxes(self):
        """All-zero rates produce zero taxes (no-tax state, etc.)."""
        cal = FakeCalibration(
            federal_rate="0",
            state_rate="0",
            ss_rate="0",
            medicare_rate="0",
        )

        result = apply_calibration(
            gross_biweekly=Decimal("3000.00"),
            taxable_biweekly=Decimal("2500.00"),
            calibration=cal,
        )

        assert result["federal"] == Decimal("0.00")
        assert result["state"] == Decimal("0.00")
        assert result["ss"] == Decimal("0.00")
        assert result["medicare"] == Decimal("0.00")

    def test_federal_and_state_use_taxable_not_gross(self):
        """Federal/state taxes use taxable income, FICA uses gross.

        With different gross and taxable values, the distinction matters.
        """
        cal = FakeCalibration(
            federal_rate="0.10000",
            state_rate="0.05000",
            ss_rate="0.06200",
            medicare_rate="0.01450",
        )

        result = apply_calibration(
            gross_biweekly=Decimal("4000.00"),
            taxable_biweekly=Decimal("3000.00"),
            calibration=cal,
        )

        # Federal: 3000 * 0.10 = 300.00 (uses taxable)
        assert result["federal"] == Decimal("300.00")
        # State: 3000 * 0.05 = 150.00 (uses taxable)
        assert result["state"] == Decimal("150.00")
        # SS: 4000 * 0.062 = 248.00 (uses gross)
        assert result["ss"] == Decimal("248.00")
        # Medicare: 4000 * 0.0145 = 58.00 (uses gross)
        assert result["medicare"] == Decimal("58.00")

    def test_rounding_to_two_decimal_places(self):
        """All results are rounded HALF_UP to 2 decimal places."""
        cal = FakeCalibration(
            federal_rate="0.07261",
            state_rate="0.04499",
            ss_rate="0.06200",
            medicare_rate="0.01450",
        )

        result = apply_calibration(
            gross_biweekly=Decimal("1000.00"),
            taxable_biweekly=Decimal("900.00"),
            calibration=cal,
        )

        # federal: 900 * 0.07261 = 65.349 -> 65.35
        assert result["federal"] == Decimal("65.35")
        # state: 900 * 0.04499 = 40.491 -> 40.49
        assert result["state"] == Decimal("40.49")

    def test_string_inputs_accepted(self):
        """String gross/taxable values are accepted and coerced."""
        cal = FakeCalibration(
            federal_rate="0.10000",
            state_rate="0.05000",
            ss_rate="0.06200",
            medicare_rate="0.01450",
        )

        result = apply_calibration(
            gross_biweekly="2000.00",
            taxable_biweekly="1800.00",
            calibration=cal,
        )

        assert result["federal"] == Decimal("180.00")
        assert result["ss"] == Decimal("124.00")


class TestRoundTrip:
    """Derive-then-apply must reproduce the original pay stub amounts exactly.

    This is the most important property of the calibration system. If
    deriving rates from actual amounts and then applying those rates to
    the same gross/taxable does not reproduce the original amounts to
    the penny, the calibration is broken and every future paycheck will
    be wrong.

    IMPORTANT: Several test cases are specifically chosen because they
    produce WRONG results at 5-decimal precision but CORRECT results at
    10-decimal precision. These tests are the safety net for the
    precision fix -- if someone reverts RATE_PLACES to 0.00001, these
    tests MUST fail. Cases marked [precision-sensitive] were verified
    to produce different (incorrect) values at 5 decimal places.
    """

    def _assert_round_trip(self, gross, federal, state, ss, medicare, taxable):
        """Helper: derive rates from actuals, apply back, verify penny match."""
        rates = derive_effective_rates(
            actual_gross_pay=gross,
            actual_federal_tax=federal,
            actual_state_tax=state,
            actual_social_security=ss,
            actual_medicare=medicare,
            taxable_income=taxable,
        )
        cal = FakeCalibration(
            federal_rate=str(rates.effective_federal_rate),
            state_rate=str(rates.effective_state_rate),
            ss_rate=str(rates.effective_ss_rate),
            medicare_rate=str(rates.effective_medicare_rate),
        )
        result = apply_calibration(
            gross_biweekly=gross,
            taxable_biweekly=taxable,
            calibration=cal,
        )

        assert result["federal"] == Decimal(str(federal)).quantize(Decimal("0.01")), (
            f"Federal round-trip failed: expected {federal}, got {result['federal']} "
            f"(rate={rates.effective_federal_rate})"
        )
        assert result["state"] == Decimal(str(state)).quantize(Decimal("0.01")), (
            f"State round-trip failed: expected {state}, got {result['state']} "
            f"(rate={rates.effective_state_rate})"
        )
        assert result["ss"] == Decimal(str(ss)).quantize(Decimal("0.01")), (
            f"SS round-trip failed: expected {ss}, got {result['ss']} "
            f"(rate={rates.effective_ss_rate})"
        )
        assert result["medicare"] == Decimal(str(medicare)).quantize(Decimal("0.01")), (
            f"Medicare round-trip failed: expected {medicare}, got {result['medicare']} "
            f"(rate={rates.effective_medicare_rate})"
        )

    def test_round_trip_all_four_taxes_precision_sensitive(self):
        """[precision-sensitive] All four taxes fail at 5-decimal places.

        Verified: at 5-decimal precision --
          federal $150.00 -> $149.99 (WRONG)
          state $80.01 -> $80.00 (WRONG)
          ss $150.01 -> $150.00 (WRONG)
          medicare $30.01 -> $30.00 (WRONG)

        These amounts were found by systematic sweep of realistic
        paycheck values. Every tax line produces the wrong penny at
        5-decimal precision. If RATE_PLACES is reverted, all four
        assertions fail.
        """
        self._assert_round_trip(
            gross=Decimal("2884.62"),
            federal=Decimal("150.00"),
            state=Decimal("80.01"),
            ss=Decimal("150.01"),
            medicare=Decimal("30.01"),
            taxable=Decimal("2684.62"),
        )

    def test_round_trip_federal_and_state_precision_sensitive(self):
        """[precision-sensitive] Large deduction gap -- federal/state break at 5 places.

        Verified: at 5-decimal precision --
          federal $250.00 -> $250.01 (WRONG, off by +$0.01)
          state $100.00 -> $100.01 (WRONG, off by +$0.01)
        """
        self._assert_round_trip(
            gross=Decimal("3846.15"),
            federal=Decimal("250.00"),
            state=Decimal("100.00"),
            ss=Decimal("238.46"),
            medicare=Decimal("55.77"),
            taxable=Decimal("3096.15"),
        )

    def test_round_trip_mid_salary_precision_sensitive(self):
        """[precision-sensitive] ~$67k salary -- federal and state break at 5 places.

        Verified: at 5-decimal precision --
          federal $150.00 -> $150.01 (WRONG, off by +$0.01)
          state $70.01 -> $70.00 (WRONG, off by -$0.01)
        """
        self._assert_round_trip(
            gross=Decimal("2576.92"),
            federal=Decimal("150.00"),
            state=Decimal("70.01"),
            ss=Decimal("159.77"),
            medicare=Decimal("37.37"),
            taxable=Decimal("2376.92"),
        )

    def test_round_trip_typical_paycheck(self):
        """$60k salary, $200 pre-tax 401k -- typical mid-range paycheck.

        This case happens to pass at both 5 and 10 decimal places.
        Retained as a basic correctness check.
        """
        self._assert_round_trip(
            gross=Decimal("2307.69"),
            federal=Decimal("153.08"),
            state=Decimal("94.85"),
            ss=Decimal("143.08"),
            medicare=Decimal("33.46"),
            taxable=Decimal("2107.69"),
        )

    def test_round_trip_zero_state_tax(self):
        """No-income-tax state -- state rate is zero, others must still match."""
        self._assert_round_trip(
            gross=Decimal("3461.54"),
            federal=Decimal("412.18"),
            state=Decimal("0.00"),
            ss=Decimal("214.62"),
            medicare=Decimal("50.19"),
            taxable=Decimal("3061.54"),
        )

    def test_round_trip_one_cent_taxes(self):
        """Very small tax amounts -- tests precision at the lowest end."""
        self._assert_round_trip(
            gross=Decimal("500.00"),
            federal=Decimal("0.01"),
            state=Decimal("0.01"),
            ss=Decimal("31.00"),
            medicare=Decimal("7.25"),
            taxable=Decimal("500.00"),
        )

    def test_round_trip_high_income(self):
        """[precision-sensitive] $200k salary -- state tax breaks at 5 places.

        Verified: at 5-decimal precision --
          state $300.00 -> $299.98 (WRONG, off by -$0.02)
        """
        self._assert_round_trip(
            gross=Decimal("7692.31"),
            federal=Decimal("1800.00"),
            state=Decimal("300.00"),
            ss=Decimal("476.92"),
            medicare=Decimal("111.54"),
            taxable=Decimal("6942.31"),
        )
