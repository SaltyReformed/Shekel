"""
Shekel Budget App -- Tax Calculator Service

Pure functions for computing federal, state, and FICA taxes.
No database access -- all data is passed in as arguments.

Federal withholding follows the IRS Publication 15-T Percentage Method:
  Step 1 -- Annualize income
  Step 2 -- Apply pre-tax adjustments
  Step 3 -- Subtract standard deduction
  Step 4 -- Apply marginal tax brackets (data-driven)
  Step 5 -- Apply credits (W-4 Step 3)
  Step 6 -- De-annualize to per-period withholding
"""

import logging
from decimal import Decimal, ROUND_HALF_UP

from app.services.exceptions import (
    InvalidDependentCountError,
    InvalidFilingStatusError,
    InvalidGrossPayError,
    InvalidPayPeriodsError,
)

logger = logging.getLogger(__name__)

ZERO = Decimal("0")
TWO_PLACES = Decimal("0.01")


# ── Federal Withholding (IRS Pub 15-T Percentage Method) ──────────


def calculate_federal_withholding(
    gross_pay,
    pay_periods,
    bracket_set,
    *,
    additional_income=ZERO,
    pre_tax_deductions=ZERO,
    additional_deductions=ZERO,
    qualifying_children=0,
    other_dependents=0,
    extra_withholding=ZERO,
):
    """Calculate per-period federal income tax withholding.

    Implements the IRS Publication 15-T Percentage Method (2026+).
    All monetary values must be Decimal.  Returns a Decimal rounded
    HALF_UP to two decimal places.

    Args:
        gross_pay:              Gross pay for one pay period.
        pay_periods:            Number of pay periods per year (e.g. 26).
        bracket_set:            TaxBracketSet with .standard_deduction,
                                .child_credit_amount, .other_dependent_credit_amount,
                                and .brackets (list of TaxBracket).
        additional_income:      W-4 Step 4(a) -- other annual income (default 0).
        pre_tax_deductions:     Total annual pre-tax deductions (retirement,
                                Section 125, health premiums) already subtracted
                                from gross before this function (default 0).
        additional_deductions:  W-4 Step 4(b) -- additional annual deductions
                                (default 0).
        qualifying_children:    W-4 Step 3 -- number of qualifying children
                                under 17 (default 0).
        other_dependents:       W-4 Step 3 -- number of other dependents
                                (default 0).
        extra_withholding:      W-4 Step 4(c) -- extra withholding per period
                                (default 0).

    Returns:
        Decimal -- per-period federal withholding amount.

    Raises:
        InvalidGrossPayError:       If gross_pay < 0.
        InvalidPayPeriodsError:     If pay_periods <= 0.
        InvalidFilingStatusError:   If bracket_set is None.
        InvalidDependentCountError: If dependent counts are negative.
    """
    # ── Input validation ──────────────────────────────────────────
    gross_pay = Decimal(str(gross_pay))
    pay_periods = int(pay_periods)
    additional_income = Decimal(str(additional_income))
    pre_tax_deductions = Decimal(str(pre_tax_deductions))
    additional_deductions = Decimal(str(additional_deductions))
    extra_withholding = Decimal(str(extra_withholding))
    qualifying_children = int(qualifying_children)
    other_dependents = int(other_dependents)

    if gross_pay < ZERO:
        raise InvalidGrossPayError(gross_pay)
    if pay_periods <= 0:
        raise InvalidPayPeriodsError(pay_periods)
    if bracket_set is None:
        raise InvalidFilingStatusError(None)
    if qualifying_children < 0:
        raise InvalidDependentCountError("qualifying_children", qualifying_children)
    if other_dependents < 0:
        raise InvalidDependentCountError("other_dependents", other_dependents)

    # ── Step 1 -- Annualize income ─────────────────────────────────
    # IRS Pub 15-T: multiply periodic gross pay by the number of
    # pay periods, then add any additional annual income from W-4 4(a).
    annual_income = (gross_pay * pay_periods) + additional_income

    logger.debug("Step 1 -- annual_income: %s", annual_income)

    # ── Step 2 -- Pre-tax adjustments ──────────────────────────────
    # Subtract annualized pre-tax deductions (retirement, Sec 125, etc.)
    # and W-4 Step 4(b) additional deductions.
    adjusted_income = annual_income - pre_tax_deductions - additional_deductions
    if adjusted_income < ZERO:
        adjusted_income = ZERO

    # ── Step 3 -- Subtract standard deduction ──────────────────────
    standard_deduction = Decimal(str(bracket_set.standard_deduction))
    taxable_income = adjusted_income - standard_deduction
    if taxable_income < ZERO:
        taxable_income = ZERO

    logger.debug("Step 3 -- taxable_income: %s", taxable_income)

    # ── Step 4 -- Apply marginal tax brackets ──────────────────────
    # Brackets are data-driven: iterate sorted bracket tiers and apply
    # the marginal rate to the portion of income within each tier.
    annual_tax_before_credits = _apply_marginal_brackets(
        taxable_income, bracket_set.brackets
    )

    logger.debug(
        "Step 4 -- annual_tax_before_credits: %s", annual_tax_before_credits
    )

    # ── Step 5 -- Apply credits (W-4 Step 3) ───────────────────────
    child_credit_amount = Decimal(
        str(getattr(bracket_set, "child_credit_amount", 0) or 0)
    )
    other_credit_amount = Decimal(
        str(getattr(bracket_set, "other_dependent_credit_amount", 0) or 0)
    )

    child_credit_total = qualifying_children * child_credit_amount
    other_credit_total = other_dependents * other_credit_amount
    total_credits = child_credit_total + other_credit_total

    logger.debug("Step 5 -- total_credits: %s", total_credits)

    annual_tax_after_credits = annual_tax_before_credits - total_credits
    if annual_tax_after_credits < ZERO:
        annual_tax_after_credits = ZERO

    logger.debug(
        "Step 5 -- annual_tax_after_credits: %s", annual_tax_after_credits
    )

    # ── Step 6 -- De-annualize ─────────────────────────────────────
    per_period_withholding = (
        annual_tax_after_credits / pay_periods
    ) + extra_withholding

    per_period_withholding = per_period_withholding.quantize(
        TWO_PLACES, rounding=ROUND_HALF_UP
    )

    logger.debug(
        "Step 6 -- per_period_withholding: %s", per_period_withholding
    )

    return per_period_withholding


def _apply_marginal_brackets(taxable_income, brackets):
    """Apply progressive marginal tax rates from a bracket list.

    Brackets are iterated in sort_order.  Each bracket defines a
    (min_income, max_income, rate) range.  The top bracket has
    max_income = None (open-ended).

    Args:
        taxable_income: Decimal -- income after standard deduction.
        brackets:       Iterable of TaxBracket objects.

    Returns:
        Decimal -- annual tax before credits, rounded to 2 places.
    """
    if taxable_income <= ZERO:
        return ZERO

    total_tax = ZERO
    for bracket in sorted(brackets, key=lambda b: b.sort_order):
        bracket_min = Decimal(str(bracket.min_income))
        bracket_max = (
            Decimal(str(bracket.max_income)) if bracket.max_income else None
        )
        rate = Decimal(str(bracket.rate))

        if taxable_income <= bracket_min:
            break

        if bracket_max is None:
            amount_in_bracket = taxable_income - bracket_min
        else:
            amount_in_bracket = min(taxable_income, bracket_max) - bracket_min

        if amount_in_bracket > ZERO:
            total_tax += amount_in_bracket * rate

    return total_tax.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


# ── State Tax ─────────────────────────────────────────────────────


def calculate_state_tax(annual_gross, state_config):
    """Calculate annual state income tax.

    Args:
        annual_gross:  Total annual gross income (Decimal).
        state_config:  A StateTaxConfig object. If None or tax_type is 'none',
                       returns 0.

    Returns:
        Decimal -- annual state tax owed.
    """
    if state_config is None:
        return ZERO

    from app import ref_cache  # pylint: disable=import-outside-toplevel
    from app.enums import TaxTypeEnum  # pylint: disable=import-outside-toplevel

    if state_config.tax_type_id == ref_cache.tax_type_id(TaxTypeEnum.NONE):
        return ZERO

    if state_config.flat_rate:
        rate = Decimal(str(state_config.flat_rate))
        std_ded = Decimal(str(getattr(state_config, "standard_deduction", None) or 0))
        taxable = annual_gross - std_ded
        if taxable < ZERO:
            taxable = ZERO
        return (taxable * rate).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    return ZERO


# ── FICA ──────────────────────────────────────────────────────────


def capped_social_security(gross, cumulative_wages, fica_config, *, ss_rate=None):
    """Compute one period's Social Security tax with the wage-base cap enforced.

    Sole source of truth for SS arithmetic.  Both the bracket-based path
    (`calculate_fica`, statutory rate) and the calibrated path
    (`apply_calibration`, the user's pay-stub-derived `effective_ss_rate`)
    delegate here so the IRS invariant -- a worker's yearly SS never exceeds
    `ss_wage_base * statutory_ss_rate` -- cannot drift between the two paths.

    Per-period SS is `ss_rate * gross`, accrued until the cumulative SS
    collected reaches the statutory annual maximum, after which it is zero.
    Expressed as one clamp:

        statutory_max = fica_config.ss_rate * ss_wage_base
        period_ss     = ss_rate * gross
        remaining     = statutory_max - ss_rate * cumulative_wages
        ss            = max(0, min(period_ss, remaining))

    When `ss_rate` is the statutory rate this reduces EXACTLY to the classic
    three-branch cap (cumulative >= base -> 0; crossing -> partial; under ->
    full `gross * ss_rate`): at the statutory rate
    `remaining == ss_rate * (ss_wage_base - cumulative_wages)`, so the bracket
    path is byte-identical to its prior form (verified against the $312k
    worked example: period 16 -> $279.00, period 17 -> $0.00).

    The calibration path passes the stub-derived `effective_ss_rate`, which
    reproduces the user's real per-period SS withholding -- assessed by their
    employer on a Section 125 cafeteria-reduced base, so typically below 6.2%
    of gross -- while the cap still bounds the annual total at the statutory
    maximum.  This restores the pre-CRIT-03 calibration fidelity (which used
    `effective_ss_rate`) WITHOUT reintroducing the F-037 bug (which had no
    cap): the cap is now enforced for both rates by the same arithmetic.  A
    calibrated `effective_ss_rate` of zero (a non-SS-covered employee, e.g.
    some government workers) correctly yields zero SS, which the statutory
    substitution got wrong.

    Args:
        gross:            Gross pay for this pay period (NOT annualized).
        cumulative_wages: Year-to-date gross wages BEFORE this period.
        fica_config:      FicaConfig with `ss_rate` and `ss_wage_base`.  When
                          None, returns ZERO -- mirroring `calculate_fica`'s
                          None-fica handling so paycheck projection on a
                          profile without a seeded FICA config produces a
                          zero SS line on both the bracket and calibration
                          paths (e.g. during early bootstrap or unit tests
                          that omit the FICA seed).
        ss_rate:          Optional per-period SS rate applied to `gross`.
                          Defaults to the statutory `fica_config.ss_rate`
                          (the bracket path).  The calibration path passes
                          the pay-stub-derived `effective_ss_rate`.  The cap
                          ceiling `statutory_max` always uses the statutory
                          `fica_config.ss_rate`, never this override.

    Returns:
        Decimal: SS tax for the period, quantised HALF_UP to two places.
    """
    if fica_config is None:
        return ZERO.quantize(TWO_PLACES)

    gross = Decimal(str(gross))
    cumulative = Decimal(str(cumulative_wages))
    statutory_rate = Decimal(str(fica_config.ss_rate))
    rate = statutory_rate if ss_rate is None else Decimal(str(ss_rate))
    ss_wage_base = Decimal(str(fica_config.ss_wage_base))

    statutory_max = statutory_rate * ss_wage_base
    period_ss = rate * gross
    remaining = statutory_max - rate * cumulative
    capped = max(min(period_ss, remaining), ZERO)
    return capped.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def calculate_fica(annual_gross, fica_config, cumulative_wages=ZERO):
    """Calculate FICA taxes (Social Security + Medicare) for a pay period.

    Handles the SS wage base cap and Medicare surtax threshold using
    cumulative wages to track year-to-date totals.  The SS portion is
    delegated to `capped_social_security` so the bracket and calibration
    paths cannot drift on the cap invariant (F-037 / CRIT-03).

    Args:
        annual_gross:     Gross income for this pay period (NOT annualized).
        fica_config:      A FicaConfig object with rates and thresholds.
        cumulative_wages: Year-to-date gross wages BEFORE this period.

    Returns:
        dict with keys: ss, medicare, total (all Decimal).
    """
    if fica_config is None:
        return {"ss": ZERO, "medicare": ZERO, "total": ZERO}

    gross = Decimal(str(annual_gross))
    cumulative = Decimal(str(cumulative_wages))
    medicare_rate = Decimal(str(fica_config.medicare_rate))
    surtax_rate = Decimal(str(fica_config.medicare_surtax_rate))
    surtax_threshold = Decimal(str(fica_config.medicare_surtax_threshold))

    ss_tax = capped_social_security(gross, cumulative, fica_config)

    # Medicare -- base rate on all income + surtax above threshold
    medicare_tax = (gross * medicare_rate).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    if cumulative + gross > surtax_threshold:
        if cumulative >= surtax_threshold:
            surtax_income = gross
        else:
            surtax_income = (cumulative + gross) - surtax_threshold
        medicare_tax += (surtax_income * surtax_rate).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )

    total = ss_tax + medicare_tax
    return {"ss": ss_tax, "medicare": medicare_tax, "total": total}
