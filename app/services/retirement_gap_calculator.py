"""
Shekel Budget App -- Retirement Income Gap Calculator Service

Orchestrates pension calculator, growth engine, and paycheck data to
produce a retirement income gap analysis.

All functions are pure (no DB access) -- data is passed in as arguments.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal

from app.utils.money import MONTHS_PER_YEAR, PAY_PERIODS_PER_YEAR, round_money

logger = logging.getLogger(__name__)

ZERO = Decimal("0")


@dataclass
class RetirementGapAnalysis:  # pylint: disable=too-many-instance-attributes
    """Result of a retirement income gap calculation.

    Pylint: ``too-many-instance-attributes`` (10/7) -- suppressed
    because this is a cohesive single-return result aggregate -- every
    field is one figure the ``retirement/_gap_analysis.html`` template
    renders as a flat row-per-field table -- mirroring
    ``amortization_engine.AmortizationRow`` /
    ``growth_engine.ProjectedBalance``. The pre-tax fields and their
    after-tax counterparts (``after_tax_monthly_pension``,
    ``after_tax_projected_savings``, ``after_tax_surplus_or_shortfall``)
    are read side-by-side by that one consumer; nesting them would
    fragment one domain concept and force template churn for no design
    gain.
    """
    pre_retirement_net_monthly: Decimal
    monthly_pension_income: Decimal
    after_tax_monthly_pension: Decimal  # None when no tax rate
    monthly_income_gap: Decimal
    required_retirement_savings: Decimal
    projected_total_savings: Decimal
    savings_surplus_or_shortfall: Decimal
    safe_withdrawal_rate: Decimal
    after_tax_projected_savings: Decimal = None
    after_tax_surplus_or_shortfall: Decimal = None


def _sum_projected_balances(projections: list[dict]) -> Decimal:
    """Sum the projected balances across all retirement-account projections.

    Args:
        projections: list of projection dicts, each carrying a Decimal under
            the ``projected_balance`` key.

    Returns:
        The total projected balance (ZERO when the list is empty).
    """
    total = ZERO
    for proj in projections:
        total += Decimal(str(proj.get("projected_balance", 0)))
    return total


def _after_tax_projected_savings(
    projections: list[dict], estimated_tax_rate: Decimal
) -> Decimal:
    """Compute the after-tax projected savings total.

    Traditional balances (401k, Trad IRA) are taxed on withdrawal, so the
    estimated tax rate is applied to their sum; Roth / brokerage balances
    are assumed already-taxed and pass through untouched.

    Args:
        projections: list of projection dicts, each with ``projected_balance``
            (Decimal) and ``is_traditional`` (bool) keys.
        estimated_tax_rate: Decimal fractional tax rate applied to the
            traditional balances.

    Returns:
        The after-tax projected total, quantized to cents.
    """
    traditional_total = ZERO
    roth_total = ZERO
    for proj in projections:
        bal = Decimal(str(proj.get("projected_balance", 0)))
        if proj.get("is_traditional", False):
            traditional_total += bal
        else:
            roth_total += bal
    return round_money(traditional_total * (1 - estimated_tax_rate) + roth_total)


def calculate_gap(
    net_biweekly_pay,
    monthly_pension_income=ZERO,
    retirement_account_projections=None,
    safe_withdrawal_rate=Decimal("0.04"),
    estimated_tax_rate=None,
):
    """Calculate the retirement income gap analysis.

    Args:
        net_biweekly_pay:              Decimal current net biweekly paycheck.
        monthly_pension_income:        Decimal monthly pension benefit.
        retirement_account_projections: list of dicts with keys:
            - projected_balance: Decimal
            - is_traditional: bool (True for 401k, Trad IRA; False for Roth, brokerage)
        safe_withdrawal_rate:          Decimal (default 0.04 = 4% rule).
        estimated_tax_rate:            Decimal or None. If set, applied to traditional balances.

    Returns:
        RetirementGapAnalysis dataclass.
    """
    net_biweekly_pay = Decimal(str(net_biweekly_pay))
    monthly_pension_income = Decimal(str(monthly_pension_income))
    safe_withdrawal_rate = Decimal(str(safe_withdrawal_rate))

    if retirement_account_projections is None:
        retirement_account_projections = []

    # Step 1: Pre-retirement net monthly income. Biweekly-to-monthly
    # uses the canonical factors from app.utils.money so this site
    # cannot drift from /obligations and /savings (E-24, HIGH-05).
    pre_retirement_net_monthly = round_money(
        net_biweekly_pay * PAY_PERIODS_PER_YEAR / MONTHS_PER_YEAR
    )

    # Step 2: Monthly pension income (passed in directly).

    # Step 2b: After-tax pension income (when tax rate provided).
    after_tax_monthly_pension = None
    if estimated_tax_rate is not None:
        estimated_tax_rate = Decimal(str(estimated_tax_rate))
        after_tax_monthly_pension = round_money(
            monthly_pension_income * (1 - estimated_tax_rate)
        )

    # Step 3: Monthly income gap.
    # Use after-tax pension when available for apples-to-apples comparison
    # with net (post-tax) current income.
    effective_pension = (
        after_tax_monthly_pension
        if after_tax_monthly_pension is not None
        else monthly_pension_income
    )
    monthly_income_gap = max(
        pre_retirement_net_monthly - effective_pension,
        ZERO,
    )

    # Step 4: Required retirement savings (4% rule or custom SWR).
    # ``MONTHS_PER_YEAR`` annualizes the monthly gap so the SWR (an
    # annual rate) divides into an apples-to-apples figure.
    if safe_withdrawal_rate > 0:
        required_retirement_savings = round_money(
            monthly_income_gap * MONTHS_PER_YEAR / safe_withdrawal_rate
        )
    else:
        required_retirement_savings = ZERO

    # Step 5: Projected total savings at retirement.
    projected_total_savings = _sum_projected_balances(retirement_account_projections)

    # Step 6: Surplus or shortfall.
    savings_surplus_or_shortfall = projected_total_savings - required_retirement_savings

    # After-tax view.
    after_tax_projected = None
    after_tax_surplus = None
    if estimated_tax_rate is not None:
        after_tax_projected = _after_tax_projected_savings(
            retirement_account_projections, estimated_tax_rate
        )
        after_tax_surplus = after_tax_projected - required_retirement_savings

    return RetirementGapAnalysis(
        pre_retirement_net_monthly=pre_retirement_net_monthly,
        monthly_pension_income=monthly_pension_income,
        after_tax_monthly_pension=after_tax_monthly_pension,
        monthly_income_gap=monthly_income_gap,
        required_retirement_savings=required_retirement_savings,
        projected_total_savings=projected_total_savings,
        savings_surplus_or_shortfall=savings_surplus_or_shortfall,
        safe_withdrawal_rate=safe_withdrawal_rate,
        after_tax_projected_savings=after_tax_projected,
        after_tax_surplus_or_shortfall=after_tax_surplus,
    )
