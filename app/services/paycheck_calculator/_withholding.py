"""
Shekel Budget App -- Paycheck engine: the four WITHHOLDING lines.

The per-paycheck wage figures both tax paths read (:class:`_WageBasis`) and
the two paths themselves: :func:`_calibrated_tax_lines`, effective rates from
one real pay stub, and :func:`_bracket_tax_lines`, IRS Pub 15-T federal
withholding (:func:`_bracket_federal`) plus the annualised state tax
(:func:`_bracket_state`) plus FICA.  Which path a paycheck takes is
:func:`~._pricing.calculate_paycheck`'s decision; both enforce the Social
Security wage-base cap from the same year-to-date cumulative
(CRIT-03 / F-037), which is why the cumulative arrives on the value rather
than being computed inside either path.

Split out of the one-module engine at plan step **salary:C12** (ledger row
**P64**).  Imports :mod:`._breakdown` and the tax services below the engine,
and nothing else of the package.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.services import tax_calculator
from app.services.calibration_service import apply_calibration
from app.utils.money import ZERO, round_money

from ._breakdown import TaxLines


@dataclass(frozen=True)
class _WageBasis:
    """The per-paycheck wage figures withholding is computed from.

    The three figures travel together through both tax paths (calibrated
    and bracket-based): the period gross, the period taxable amount (gross
    less pre-tax deductions, floored at zero), and the year-to-date
    cumulative gross that drives the FICA Social Security wage-base cap.
    """
    gross_biweekly: Decimal
    taxable_biweekly: Decimal
    cumulative_wages: Decimal


def _calibrated_tax_lines(wages, calibration, fica_config):
    """Compute the four withholding lines from effective calibrated rates.

    The Social Security line inside :func:`apply_calibration` delegates to
    ``capped_social_security`` so the wage-base cap is enforced identically
    to the bracket path (CRIT-03 / F-037).

    Args:
        wages: The per-paycheck :class:`_WageBasis` (gross, taxable, and the
            cumulative YTD gross that drives the SS wage-base cap).
        calibration: An active CalibrationOverride with effective rates.
        fica_config: The FicaConfig (or None) for the SS wage-base cap.

    Returns:
        TaxLines with the federal, state, social_security, and medicare
        withholding amounts.
    """
    cal_taxes = apply_calibration(
        wages.gross_biweekly,
        wages.taxable_biweekly,
        calibration,
        cumulative_wages=wages.cumulative_wages,
        fica_config=fica_config,
    )
    return TaxLines(
        federal=cal_taxes["federal"],
        state=cal_taxes["state"],
        social_security=cal_taxes["ss"],
        medicare=cal_taxes["medicare"],
    )


def _bracket_tax_lines(basis, wages, total_pre_tax, tax_configs):
    """Compute the four withholding lines from IRS Pub 15-T brackets plus FICA.

    The cumulative YTD gross on ``wages`` feeds the FICA SS wage-base cap so
    it is enforced identically to the calibration path (CRIT-03 / F-037).

    Args:
        basis: The :class:`~app.services.payroll_basis.PayrollBasis` -- read
            for the W-4 federal inputs and for the denominator Pub 15-T
            annualises a period's wages by.
        wages: The per-paycheck :class:`_WageBasis` (gross, taxable, and the
            cumulative YTD gross that drives the SS wage-base cap).
        total_pre_tax: Per-period pre-tax deduction total (annualised for
            the bracket federal calculation).
        tax_configs: dict with bracket_set, state_config, fica_config.

    Returns:
        TaxLines with the federal, state, social_security, and medicare
        withholding amounts.
    """
    pay_periods_per_year = basis.periods_per_year
    bracket_set = tax_configs.get("bracket_set")
    federal = (
        _bracket_federal(
            basis.profile, wages.gross_biweekly, pay_periods_per_year,
            bracket_set, total_pre_tax * pay_periods_per_year,
        )
        if bracket_set
        else ZERO
    )
    state = _bracket_state(
        wages.taxable_biweekly, pay_periods_per_year, tax_configs.get("state_config")
    )
    fica = tax_calculator.calculate_fica(
        wages.gross_biweekly, tax_configs.get("fica_config"), wages.cumulative_wages
    )
    return TaxLines(
        federal=federal,
        state=state,
        social_security=fica["ss"],
        medicare=fica["medicare"],
    )


def _bracket_federal(profile, gross_biweekly, pay_periods_per_year, bracket_set,
                     annual_pre_tax):
    """Return the bracket-based biweekly federal withholding (IRS Pub 15-T).

    Reads the W-4 inputs off ``profile`` and delegates to
    :func:`tax_calculator.calculate_federal_withholding`.

    Args:
        profile: The SalaryProfile (read for the W-4 inputs).
        gross_biweekly: The period gross to withhold against.
        pay_periods_per_year: The full-year denominator IRS Pub 15-T
            annualises against, off
            :attr:`~app.services.payroll_basis.PayrollBasis.periods_per_year`.
        bracket_set: The TaxBracketSet to withhold against.
        annual_pre_tax: Annualised pre-tax deduction total.

    Returns:
        Decimal biweekly federal withholding.
    """
    w4 = tax_calculator.W4Inputs(
        additional_income=getattr(profile, "additional_income", 0) or 0,
        pre_tax_deductions=annual_pre_tax,
        additional_deductions=getattr(profile, "additional_deductions", 0) or 0,
        qualifying_children=getattr(profile, "qualifying_children", 0) or 0,
        other_dependents=getattr(profile, "other_dependents", 0) or 0,
        extra_withholding=getattr(profile, "extra_withholding", 0) or 0,
    )
    return tax_calculator.calculate_federal_withholding(
        gross_biweekly, pay_periods_per_year, bracket_set, w4,
    )


def _bracket_state(taxable_biweekly, pay_periods_per_year, state_config):
    """Return the biweekly state withholding from annualised taxable income.

    Args:
        taxable_biweekly: Gross less pre-tax deductions, floored at zero.
        pay_periods_per_year: The full-year denominator the annual state tax
            is computed over and divided back by, off
            :attr:`~app.services.payroll_basis.PayrollBasis.periods_per_year`.
        state_config: The StateTaxConfig (or None).

    Returns:
        Decimal biweekly state withholding.
    """
    state_annual = tax_calculator.calculate_state_tax(
        taxable_biweekly * pay_periods_per_year, state_config
    )
    return round_money(state_annual / pay_periods_per_year)
