"""
Shekel Budget App -- Calibration Service

Pure functions for deriving effective tax/deduction rates from actual
pay stub data and applying those rates to paycheck calculations.

No database access -- all data is passed in as arguments.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from app.exceptions import ValidationError

logger = logging.getLogger(__name__)

ZERO = Decimal("0")
TWO_PLACES = Decimal("0.01")
RATE_PLACES = Decimal("0.00001")


@dataclass
class DerivedRates:
    """Effective rates computed from actual pay stub values."""
    effective_federal_rate: Decimal
    effective_state_rate: Decimal
    effective_ss_rate: Decimal
    effective_medicare_rate: Decimal


def derive_effective_rates(
    actual_gross_pay,
    actual_federal_tax,
    actual_state_tax,
    actual_social_security,
    actual_medicare,
    taxable_income,
):
    """Derive effective tax rates from a real pay stub.

    Federal and state effective rates are computed against taxable income
    (gross minus pre-tax deductions) because that is the base the
    paycheck calculator uses for income taxes.

    FICA rates (SS and Medicare) are computed against gross pay because
    FICA is assessed on gross wages per IRS rules.

    Args:
        actual_gross_pay:       Gross pay from the pay stub (Decimal, > 0).
        actual_federal_tax:     Federal tax withheld (Decimal, >= 0).
        actual_state_tax:       State tax withheld (Decimal, >= 0).
        actual_social_security: Social Security withheld (Decimal, >= 0).
        actual_medicare:        Medicare withheld (Decimal, >= 0).
        taxable_income:         Gross minus pre-tax deductions (Decimal).
                                Used as the base for income tax rates.

    Returns:
        DerivedRates dataclass with four effective rates.

    Raises:
        ValidationError: If gross_pay <= 0 or taxable_income <= 0.
    """
    gross = Decimal(str(actual_gross_pay))
    federal = Decimal(str(actual_federal_tax))
    state = Decimal(str(actual_state_tax))
    ss = Decimal(str(actual_social_security))
    medicare = Decimal(str(actual_medicare))
    taxable = Decimal(str(taxable_income))

    if gross <= ZERO:
        raise ValidationError("Actual gross pay must be greater than zero.")

    if taxable <= ZERO:
        raise ValidationError(
            "Taxable income (gross minus pre-tax deductions) must be "
            "greater than zero. Cannot derive income tax rates."
        )

    # Income tax rates use taxable income as the base.
    effective_federal = (federal / taxable).quantize(
        RATE_PLACES, rounding=ROUND_HALF_UP
    )
    effective_state = (state / taxable).quantize(
        RATE_PLACES, rounding=ROUND_HALF_UP
    )

    # FICA rates use gross pay as the base.
    effective_ss = (ss / gross).quantize(
        RATE_PLACES, rounding=ROUND_HALF_UP
    )
    effective_medicare = (medicare / gross).quantize(
        RATE_PLACES, rounding=ROUND_HALF_UP
    )

    return DerivedRates(
        effective_federal_rate=effective_federal,
        effective_state_rate=effective_state,
        effective_ss_rate=effective_ss,
        effective_medicare_rate=effective_medicare,
    )


def apply_calibration(gross_biweekly, taxable_biweekly, calibration):
    """Compute tax amounts using calibrated effective rates.

    Called by the paycheck calculator when a calibration override is
    active.  Returns the four tax amounts that replace the bracket-based
    calculations.

    Args:
        gross_biweekly:    Decimal -- gross pay for the period.
        taxable_biweekly:  Decimal -- gross minus pre-tax deductions.
        calibration:       Object with effective_federal_rate,
                           effective_state_rate, effective_ss_rate,
                           effective_medicare_rate attributes.

    Returns:
        dict with keys: federal, state, ss, medicare (all Decimal,
        rounded to 2 places).
    """
    gross = Decimal(str(gross_biweekly))
    taxable = Decimal(str(taxable_biweekly))

    federal_rate = Decimal(str(calibration.effective_federal_rate))
    state_rate = Decimal(str(calibration.effective_state_rate))
    ss_rate = Decimal(str(calibration.effective_ss_rate))
    medicare_rate = Decimal(str(calibration.effective_medicare_rate))

    return {
        "federal": (taxable * federal_rate).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        ),
        "state": (taxable * state_rate).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        ),
        "ss": (gross * ss_rate).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        ),
        "medicare": (gross * medicare_rate).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        ),
    }
