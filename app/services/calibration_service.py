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
from app.services.tax_calculator import capped_social_security

logger = logging.getLogger(__name__)

ZERO = Decimal("0")
TWO_PLACES = Decimal("0.01")
# 10 decimal places for effective rates to avoid penny rounding errors
# when the rate is multiplied back against the taxable/gross base.
RATE_PLACES = Decimal("0.0000000001")


@dataclass
class DerivedRates:
    """Effective rates computed from actual pay stub values."""
    effective_federal_rate: Decimal
    effective_state_rate: Decimal
    effective_ss_rate: Decimal
    effective_medicare_rate: Decimal


@dataclass(frozen=True)
class PayStubActuals:
    """Actual withholding amounts transcribed from one real pay stub.

    The immutable input bundle for ``derive_effective_rates``: the five
    amounts a user copies from a single paycheck, plus the taxable-income
    base computed from their profile's current pre-tax deductions.  All
    fields are monetary Decimals.

    Attributes:
        actual_gross_pay:       Gross pay from the pay stub (> 0).
        actual_federal_tax:     Federal tax withheld (>= 0).
        actual_state_tax:       State tax withheld (>= 0).
        actual_social_security: Social Security withheld (>= 0).
        actual_medicare:        Medicare withheld (>= 0).
        taxable_income:         Gross minus pre-tax deductions (> 0); the
                                divisor for the federal/state effective
                                rates (the FICA rates divide by gross).
    """
    actual_gross_pay: Decimal
    actual_federal_tax: Decimal
    actual_state_tax: Decimal
    actual_social_security: Decimal
    actual_medicare: Decimal
    taxable_income: Decimal


def derive_effective_rates(actuals: PayStubActuals) -> DerivedRates:
    """Derive effective tax rates from a real pay stub.

    Federal and state effective rates are computed against taxable income
    (gross minus pre-tax deductions) because that is the base the
    paycheck calculator uses for income taxes.

    FICA rates (SS and Medicare) are computed against gross pay because
    FICA is assessed on gross wages per IRS rules.

    Args:
        actuals: The pay-stub snapshot to derive rates from -- the five
            actual withholding amounts plus the taxable-income base.  See
            ``PayStubActuals`` for the per-field contract.

    Returns:
        DerivedRates dataclass with four effective rates.

    Raises:
        ValidationError: If gross_pay <= 0 or taxable_income <= 0.
    """
    gross = Decimal(str(actuals.actual_gross_pay))
    federal = Decimal(str(actuals.actual_federal_tax))
    state = Decimal(str(actuals.actual_state_tax))
    ss = Decimal(str(actuals.actual_social_security))
    medicare = Decimal(str(actuals.actual_medicare))
    taxable = Decimal(str(actuals.taxable_income))

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


def apply_calibration(
    gross_biweekly,
    taxable_biweekly,
    calibration,
    *,
    cumulative_wages,
    fica_config,
):
    """Compute tax amounts using calibrated effective rates.

    Called by the paycheck calculator when a calibration override is
    active.  Returns the four tax amounts that replace the bracket-based
    calculations.

    Federal, state, and Medicare lines use the calibration's effective
    rates derived from the user's real pay stub (calibration is the user's
    "this is how my employer actually withholds" snapshot).

    The Social Security line is delegated to
    `tax_calculator.capped_social_security`, passing the calibration's
    `effective_ss_rate` as the per-period rate so SS reproduces the user's
    real withholding -- which their employer assesses on a Section 125
    cafeteria-reduced base, typically below 6.2% of gross -- exactly the way
    Medicare already uses `effective_medicare_rate`.  The helper still
    enforces the statutory wage-base cap on the annual total, so this neither
    over-charges a cafeteria filer (the prior statutory-rate-on-full-gross
    behaviour did, by ~$24/biweekly here) nor reintroduces the F-037 / CRIT-03
    bug (uncapped SS for a high earner): the cap ceiling
    `ss_wage_base * statutory_ss_rate` is independent of the effective rate.

    Args:
        gross_biweekly:    Decimal -- gross pay for the period.
        taxable_biweekly:  Decimal -- gross minus pre-tax deductions.
        calibration:       Object with effective_federal_rate,
                           effective_state_rate, effective_ss_rate,
                           effective_medicare_rate attributes.  All four are
                           consumed.
        cumulative_wages:  Decimal -- year-to-date gross wages BEFORE this
                           period.  Required; the SS cap cannot be evaluated
                           without it.
        fica_config:       FicaConfig with `ss_rate` and `ss_wage_base`.
                           Required; carries the statutory SS rate (the cap
                           ceiling) and the wage-base cap the helper enforces.

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
        "ss": capped_social_security(
            gross, cumulative_wages, fica_config, ss_rate=ss_rate
        ),
        "medicare": (gross * medicare_rate).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        ),
    }
