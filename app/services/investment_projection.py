"""
Shekel Budget App -- Investment Projection Input Calculator

Pure function that computes all inputs needed for growth_engine.project_balance()
from raw deduction, contribution, and investment params data.

Used by both the investment detail route and the savings dashboard to avoid
duplicating contribution/employer/YTD calculation logic.

Contributions are derived from shadow income transactions (transfer_id IS NOT
NULL) in the investment/retirement account.  The caller queries these
transactions and passes them in; this module has no database access.
"""

from dataclasses import dataclass
from datetime import date
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


def _compute_deduction_per_period(deduction, pct_id):
    """Compute the per-period contribution amount from a single deduction.

    Handles flat-dollar and percentage-of-salary calculation methods.
    Shared by calculate_investment_inputs() and build_contribution_timeline()
    to keep the deduction amount logic in one place (DRY).

    Args:
        deduction:  Object with .amount, .calc_method_id, .annual_salary,
                    .pay_periods_per_year.
        pct_id:     The ref ID for the PERCENTAGE calculation method.

    Returns:
        Tuple of (contribution_amount: Decimal, gross_biweekly: Decimal).
        contribution_amount is the per-period dollar amount.
        gross_biweekly is the derived gross pay per period (used by
        calculate_investment_inputs for employer params).
    """
    salary = Decimal(str(deduction.annual_salary))
    pay_per_year = deduction.pay_periods_per_year or 26
    gross = (salary / pay_per_year).quantize(TWO_PLACES)
    amt = Decimal(str(deduction.amount))
    if deduction.calc_method_id == pct_id:
        amt = (gross * amt).quantize(TWO_PLACES)
    return amt, gross


def calculate_investment_inputs(
    account_id,
    investment_params,
    deductions,
    all_contributions,
    all_periods,
    current_period,
    salary_gross_biweekly=None,
):
    """Compute projection inputs for an investment account.

    Args:
        account_id:         int -- the investment account ID.
        investment_params:  Object with employer fields and annual_contribution_limit.
        deductions:         List of deduction-like objects with:
                            .amount, .calc_method_id, .annual_salary, .pay_periods_per_year
        all_contributions:  List of shadow income transactions (transfer_id IS NOT NULL)
                            in this account.  Each has .estimated_amount and .pay_period_id.
        all_periods:        List of period objects with .id, .start_date, .period_index
        current_period:     The current period object.

    Returns:
        InvestmentInputs dataclass.
    """
    # Step 1: Periodic contribution from paycheck deductions.
    periodic_contribution = ZERO
    gross_biweekly = ZERO

    from app import ref_cache  # pylint: disable=import-outside-toplevel
    from app.enums import CalcMethodEnum  # pylint: disable=import-outside-toplevel

    pct_id = ref_cache.calc_method_id(CalcMethodEnum.PERCENTAGE)

    for ded in deductions:
        amt, gross = _compute_deduction_per_period(ded, pct_id)
        gross_biweekly = gross
        periodic_contribution += amt

    # Use salary profile gross as fallback when no deductions provided one.
    if gross_biweekly == ZERO and salary_gross_biweekly is not None:
        gross_biweekly = Decimal(str(salary_gross_biweekly))

    # Step 2: Transfer-based contributions (average per period).
    # all_contributions are shadow income transactions already filtered
    # to this account by the caller.
    if all_contributions:
        total_contrib = sum(
            Decimal(str(t.estimated_amount)) for t in all_contributions
        )
        num_periods_with_contrib = len(
            set(t.pay_period_id for t in all_contributions)
        )
        if num_periods_with_contrib > 0:
            periodic_contribution += (total_contrib / num_periods_with_contrib).quantize(
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

    # Step 4: YTD contributions from shadow transactions.
    ytd_contributions = ZERO
    if current_period:
        current_year = current_period.start_date.year
        ytd_period_ids = {
            p.id for p in all_periods
            if p.start_date.year == current_year
            and p.start_date <= current_period.start_date
        }
        for t in all_contributions:
            if t.pay_period_id in ytd_period_ids:
                ytd_contributions += Decimal(str(t.estimated_amount))

    # Step 5: Annual contribution limit.
    annual_limit = getattr(investment_params, "annual_contribution_limit", None)

    return InvestmentInputs(
        periodic_contribution=periodic_contribution,
        employer_params=employer_params,
        annual_contribution_limit=annual_limit,
        ytd_contributions=ytd_contributions,
        gross_biweekly=gross_biweekly,
    )


def build_contribution_timeline(
    deductions,
    contribution_transactions,
    periods,
):
    """Build ContributionRecords from deductions and shadow transactions.

    Combines two contribution paths into a unified per-period timeline
    for the growth engine:

    Path 1 -- Paycheck deductions: The same dollar amount every period.
    Confirmation is date-based (past period = confirmed) because there
    is no per-period transaction record for deductions.

    Path 2 -- Transfer-based contributions: Per-transaction amounts from
    shadow income transactions.  Confirmation is status-based
    (txn.status.is_settled) -- factual from the transaction workflow.

    The growth engine handles same-date aggregation (summing amounts,
    conservative is_confirmed rule) via its lookup dict.

    Args:
        deductions:                 List of deduction-like objects with
                                    .amount, .calc_method_id,
                                    .annual_salary, .pay_periods_per_year.
        contribution_transactions:  List of shadow income Transaction
                                    objects (transfer_id IS NOT NULL)
                                    with .effective_amount, .pay_period_id,
                                    .status (.is_settled, .excludes_from_balance).
                                    Status must be eager-loaded by caller.
        periods:                    List of period objects with .id,
                                    .start_date.

    Returns:
        list[ContributionRecord] sorted by contribution_date.  Empty
        list if no deductions and no qualifying transactions exist.
    """
    from app import ref_cache  # pylint: disable=import-outside-toplevel
    from app.enums import CalcMethodEnum  # pylint: disable=import-outside-toplevel
    from app.services.growth_engine import ContributionRecord  # pylint: disable=import-outside-toplevel

    records = []
    today = date.today()
    pct_id = ref_cache.calc_method_id(CalcMethodEnum.PERCENTAGE)

    # Path 1: Paycheck deductions -- same amount every period.
    total_deduction_per_period = sum(
        (_compute_deduction_per_period(d, pct_id)[0] for d in deductions),
        ZERO,
    )

    if total_deduction_per_period > ZERO:
        for period in periods:
            records.append(ContributionRecord(
                contribution_date=period.start_date,
                amount=total_deduction_per_period,
                # Deduction-based: past periods are confirmed (the
                # deduction was taken from the paycheck); future
                # periods are projected.
                is_confirmed=period.start_date < today,
            ))

    # Path 2: Transfer-based contributions -- per-transaction amounts.
    period_by_id = {p.id: p for p in periods}
    for txn in contribution_transactions:
        # Skip cancelled/credit transactions that do not represent
        # actual contributions (same filter as loan_payment_service).
        if txn.status.excludes_from_balance:
            continue
        period = period_by_id.get(txn.pay_period_id)
        if period is None:
            # Transaction in a period outside the projection range.
            continue
        amount = txn.effective_amount
        # Defensive: ensure Decimal even if effective_amount returns
        # a non-Decimal from a DB column.
        if not isinstance(amount, Decimal):
            amount = Decimal(str(amount))
        records.append(ContributionRecord(
            contribution_date=period.start_date,
            amount=amount,
            # Transfer-based: determined by the transaction's
            # settlement status (Paid/Settled=True, Projected=False).
            is_confirmed=txn.status.is_settled,
        ))

    records.sort(key=lambda r: r.contribution_date)
    return records
