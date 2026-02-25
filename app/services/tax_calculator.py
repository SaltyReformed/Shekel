"""
Shekel Budget App — Tax Calculator Service

Pure functions for computing federal, state, and FICA taxes.
No database access — all data is passed in as arguments.
"""

from decimal import Decimal, ROUND_HALF_UP

ZERO = Decimal("0")
TWO_PLACES = Decimal("0.01")


def calculate_federal_tax(annual_gross, bracket_set):
    """Calculate annual federal income tax using marginal brackets.

    Args:
        annual_gross:  Total annual gross income (Decimal).
        bracket_set:   A TaxBracketSet with loaded `brackets` and
                       `standard_deduction`.

    Returns:
        Decimal — annual federal tax owed.
    """
    taxable = annual_gross - bracket_set.standard_deduction
    if taxable <= ZERO:
        return ZERO

    total_tax = ZERO
    for bracket in sorted(bracket_set.brackets, key=lambda b: b.sort_order):
        bracket_min = Decimal(str(bracket.min_income))
        bracket_max = (
            Decimal(str(bracket.max_income)) if bracket.max_income else None
        )
        rate = Decimal(str(bracket.rate))

        if taxable <= bracket_min:
            break

        if bracket_max is None:
            # Top bracket — tax everything above min.
            amount_in_bracket = taxable - bracket_min
        else:
            amount_in_bracket = min(taxable, bracket_max) - bracket_min

        if amount_in_bracket > ZERO:
            total_tax += amount_in_bracket * rate

    return total_tax.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def calculate_state_tax(annual_gross, state_config):
    """Calculate annual state income tax.

    Args:
        annual_gross:  Total annual gross income (Decimal).
        state_config:  A StateTaxConfig object. If None or tax_type is 'none',
                       returns 0.

    Returns:
        Decimal — annual state tax owed.
    """
    if state_config is None:
        return ZERO

    if state_config.tax_type and state_config.tax_type.name == "none":
        return ZERO

    if state_config.flat_rate:
        rate = Decimal(str(state_config.flat_rate))
        return (annual_gross * rate).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    return ZERO


def calculate_fica(annual_gross, fica_config, cumulative_wages=ZERO):
    """Calculate FICA taxes (Social Security + Medicare) for a pay period.

    Handles the SS wage base cap and Medicare surtax threshold using
    cumulative wages to track year-to-date totals.

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
    ss_rate = Decimal(str(fica_config.ss_rate))
    ss_wage_base = Decimal(str(fica_config.ss_wage_base))
    medicare_rate = Decimal(str(fica_config.medicare_rate))
    surtax_rate = Decimal(str(fica_config.medicare_surtax_rate))
    surtax_threshold = Decimal(str(fica_config.medicare_surtax_threshold))

    # Social Security — capped at wage base
    if cumulative >= ss_wage_base:
        ss_tax = ZERO
    elif cumulative + gross > ss_wage_base:
        ss_taxable = ss_wage_base - cumulative
        ss_tax = (ss_taxable * ss_rate).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    else:
        ss_tax = (gross * ss_rate).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    # Medicare — base rate on all income + surtax above threshold
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
