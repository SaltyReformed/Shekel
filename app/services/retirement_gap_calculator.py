"""
Shekel Budget App — Retirement Income Gap Calculator Service

Orchestrates pension calculator, growth engine, and paycheck data to
produce a retirement income gap analysis.

All functions are pure (no DB access) — data is passed in as arguments.
"""

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

logger = logging.getLogger(__name__)

ZERO = Decimal("0")
TWO_PLACES = Decimal("0.01")


@dataclass
class RetirementGapAnalysis:
    """Result of a retirement income gap calculation."""
    pre_retirement_net_monthly: Decimal
    monthly_pension_income: Decimal
    monthly_income_gap: Decimal
    required_retirement_savings: Decimal
    projected_total_savings: Decimal
    savings_surplus_or_shortfall: Decimal
    safe_withdrawal_rate: Decimal
    planned_retirement_date: date
    after_tax_projected_savings: Decimal = None
    after_tax_surplus_or_shortfall: Decimal = None


def calculate_gap(
    net_biweekly_pay,
    monthly_pension_income=ZERO,
    retirement_account_projections=None,
    safe_withdrawal_rate=Decimal("0.04"),
    planned_retirement_date=None,
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
        planned_retirement_date:       date or None.
        estimated_tax_rate:            Decimal or None. If set, applied to traditional balances.

    Returns:
        RetirementGapAnalysis dataclass.
    """
    net_biweekly_pay = Decimal(str(net_biweekly_pay))
    monthly_pension_income = Decimal(str(monthly_pension_income))
    safe_withdrawal_rate = Decimal(str(safe_withdrawal_rate))

    if retirement_account_projections is None:
        retirement_account_projections = []

    # Step 1: Pre-retirement net monthly income.
    pre_retirement_net_monthly = (
        net_biweekly_pay * 26 / 12
    ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    # Step 2: Monthly pension income (passed in directly).

    # Step 3: Monthly income gap.
    monthly_income_gap = max(
        pre_retirement_net_monthly - monthly_pension_income,
        ZERO,
    )

    # Step 4: Required retirement savings (4% rule or custom SWR).
    if safe_withdrawal_rate > 0:
        required_retirement_savings = (
            monthly_income_gap * 12 / safe_withdrawal_rate
        ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    else:
        required_retirement_savings = ZERO

    # Step 5: Projected total savings at retirement.
    projected_total_savings = ZERO
    for proj in retirement_account_projections:
        projected_total_savings += Decimal(str(proj.get("projected_balance", 0)))

    # Step 6: Surplus or shortfall.
    savings_surplus_or_shortfall = projected_total_savings - required_retirement_savings

    # After-tax view.
    after_tax_projected = None
    after_tax_surplus = None
    if estimated_tax_rate is not None:
        estimated_tax_rate = Decimal(str(estimated_tax_rate))
        traditional_total = ZERO
        roth_total = ZERO
        for proj in retirement_account_projections:
            bal = Decimal(str(proj.get("projected_balance", 0)))
            if proj.get("is_traditional", False):
                traditional_total += bal
            else:
                roth_total += bal
        after_tax_projected = (
            traditional_total * (1 - estimated_tax_rate) + roth_total
        ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        after_tax_surplus = after_tax_projected - required_retirement_savings

    return RetirementGapAnalysis(
        pre_retirement_net_monthly=pre_retirement_net_monthly,
        monthly_pension_income=monthly_pension_income,
        monthly_income_gap=monthly_income_gap,
        required_retirement_savings=required_retirement_savings,
        projected_total_savings=projected_total_savings,
        savings_surplus_or_shortfall=savings_surplus_or_shortfall,
        safe_withdrawal_rate=safe_withdrawal_rate,
        planned_retirement_date=planned_retirement_date,
        after_tax_projected_savings=after_tax_projected,
        after_tax_surplus_or_shortfall=after_tax_surplus,
    )
