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
