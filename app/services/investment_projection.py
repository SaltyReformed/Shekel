"""
Shekel Budget App — Investment Projection Input Calculator

Pure function that computes all inputs needed for growth_engine.project_balance()
from raw deduction, transfer, and investment params data.

Used by both the investment detail route and the savings dashboard to avoid
duplicating contribution/employer/YTD calculation logic.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


ZERO = Decimal("0")
TWO_PLACES = Decimal("0.01")


@dataclass
class InvestmentInputs:
    """All inputs needed for growth_engine.project_balance()."""
    periodic_contribution: Decimal
    employer_params: Optional[dict]
    annual_contribution_limit: Optional[Decimal]
    ytd_contributions: Decimal
    gross_biweekly: Decimal


def calculate_investment_inputs(
    account_id,
    investment_params,
    deductions,
    all_transfers,
    all_periods,
    current_period,
):
    """Compute projection inputs for an investment account.

    Args:
        account_id:        int — the investment account ID.
        investment_params:  Object with employer fields and annual_contribution_limit.
        deductions:         List of deduction-like objects with:
                            .amount, .calc_method_name, .annual_salary, .pay_periods_per_year
        all_transfers:      List of transfer-like objects with:
                            .to_account_id, .amount, .pay_period_id
        all_periods:        List of period objects with .id, .start_date, .period_index
        current_period:     The current period object.

    Returns:
        InvestmentInputs dataclass.
    """
    # Step 1: Periodic contribution from paycheck deductions.
    periodic_contribution = ZERO
    gross_biweekly = ZERO

    for ded in deductions:
        salary = Decimal(str(ded.annual_salary))
        pay_per_year = ded.pay_periods_per_year or 26
        gross = (salary / pay_per_year).quantize(TWO_PLACES)
        gross_biweekly = gross
        amt = Decimal(str(ded.amount))
        if ded.calc_method_name == "percentage":
            amt = (gross * amt).quantize(TWO_PLACES)
        periodic_contribution += amt

    # Step 2: Transfer-based contributions (average per period).
    acct_transfers = [
        t for t in all_transfers
        if t.to_account_id == account_id and not getattr(t, "is_deleted", False)
    ]
    if acct_transfers:
        total_xfer = sum(Decimal(str(t.amount)) for t in acct_transfers)
        num_periods_with_xfer = len(set(t.pay_period_id for t in acct_transfers))
        if num_periods_with_xfer > 0:
            periodic_contribution += (total_xfer / num_periods_with_xfer).quantize(
                TWO_PLACES
            )

    # Step 3: Employer params.
    employer_params = None
    emp_type = getattr(investment_params, "employer_contribution_type", "none")
    if emp_type and emp_type != "none":
        employer_params = {
            "type": emp_type,
            "flat_percentage": getattr(investment_params, "employer_flat_percentage", None) or ZERO,
            "match_percentage": getattr(investment_params, "employer_match_percentage", None) or ZERO,
            "match_cap_percentage": getattr(investment_params, "employer_match_cap_percentage", None) or ZERO,
            "gross_biweekly": gross_biweekly,
        }

    # Step 4: YTD contributions from transfers.
    ytd_contributions = ZERO
    if current_period:
        current_year = current_period.start_date.year
        ytd_period_ids = {
            p.id for p in all_periods
            if p.start_date.year == current_year
            and p.start_date <= current_period.start_date
        }
        for t in acct_transfers:
            if t.pay_period_id in ytd_period_ids:
                ytd_contributions += Decimal(str(t.amount))

    # Step 5: Annual contribution limit.
    annual_limit = getattr(investment_params, "annual_contribution_limit", None)

    return InvestmentInputs(
        periodic_contribution=periodic_contribution,
        employer_params=employer_params,
        annual_contribution_limit=annual_limit,
        ytd_contributions=ytd_contributions,
        gross_biweekly=gross_biweekly,
    )
