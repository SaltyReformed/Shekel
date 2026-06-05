"""
Shekel Budget App -- Year-End Summary: income/tax and mortgage interest.

Section 1 (W-2-style income and tax totals) and Section 2 (mortgage
interest paid during the year, for Schedule A).
"""

from decimal import Decimal

from app.models.salary_profile import SalaryProfile
from app.services import paycheck_calculator
from app.services.tax_config_service import load_tax_configs

ZERO = Decimal("0")


def _compute_income_tax(
    user_id: int,
    year: int,
    periods: list,
    salary_profiles: list,
) -> dict:
    """Aggregate W-2-style income and tax totals for the year.

    Calls the paycheck calculator for each active salary profile
    across all pay periods in the year, then sums the results.

    Pre-tax and post-tax deductions are grouped by deduction name
    so each deduction type (e.g. 401k, HSA) shows its annual total.

    Args:
        user_id: User ID for loading tax configs.
        year: Calendar year for tax config lookup.
        periods: Pay periods with start_date in the target year.
        salary_profiles: Active SalaryProfile objects with loaded
            raises and deductions.

    Returns:
        dict with gross_wages, federal_tax, state_tax,
        social_security_tax, medicare_tax, pretax_deductions,
        posttax_deductions, total_pretax, total_posttax,
        net_pay_total.  mortgage_interest_total is added by the
        caller after computing Section 2.
    """
    if not periods or not salary_profiles:
        return _empty_income_tax()

    # Accumulate totals across all profiles and periods.
    totals = {k: ZERO for k in (
        "gross", "federal", "state", "ss", "medicare", "net",
    )}
    pretax_by_name: dict[str, Decimal] = {}
    posttax_by_name: dict[str, Decimal] = {}

    for profile in salary_profiles:
        breakdowns = _compute_profile_breakdowns(
            user_id, year, profile, periods,
        )
        for bd in breakdowns:
            totals["gross"] += bd.earnings.gross_biweekly
            totals["federal"] += bd.taxes.federal
            totals["state"] += bd.taxes.state
            totals["ss"] += bd.taxes.social_security
            totals["medicare"] += bd.taxes.medicare
            totals["net"] += bd.earnings.net_pay

            for ded in bd.deductions.pre_tax:
                pretax_by_name[ded.name] = (
                    pretax_by_name.get(ded.name, ZERO) + ded.amount
                )
            for ded in bd.deductions.post_tax:
                posttax_by_name[ded.name] = (
                    posttax_by_name.get(ded.name, ZERO) + ded.amount
                )

    return _assemble_income_result(
        totals, pretax_by_name, posttax_by_name,
    )


def _compute_profile_breakdowns(
    user_id: int, year: int, profile: SalaryProfile, periods: list,
) -> list:
    """Run the paycheck calculator for one profile across all periods.

    Loads tax configs for the target year with a fallback to the
    current year if the target year has no configs (follows the
    recurrence_engine.py pattern).

    Args:
        user_id: User ID for tax config lookup.
        year: Target calendar year.
        profile: SalaryProfile with loaded raises and deductions.
        periods: Pay periods in the target year.

    Returns:
        List of PaycheckBreakdown from project_salary.
    """
    tax_configs = load_tax_configs(user_id, profile, tax_year=year)
    if all(v is None for v in tax_configs.values()):
        tax_configs = load_tax_configs(user_id, profile)

    return paycheck_calculator.project_salary(
        profile, periods, tax_configs,
    )


def _assemble_income_result(
    totals: dict, pretax_by_name: dict, posttax_by_name: dict,
) -> dict:
    """Build the income_tax section dict from accumulated totals.

    Args:
        totals: dict mapping short keys to Decimal sums.
        pretax_by_name: deduction name -> annual total.
        posttax_by_name: deduction name -> annual total.

    Returns:
        Fully structured income_tax section dict.
    """
    pretax_list = [
        {"name": k, "annual_total": v}
        for k, v in sorted(pretax_by_name.items())
    ]
    posttax_list = [
        {"name": k, "annual_total": v}
        for k, v in sorted(posttax_by_name.items())
    ]

    return {
        "gross_wages": totals["gross"],
        "federal_tax": totals["federal"],
        "state_tax": totals["state"],
        "social_security_tax": totals["ss"],
        "medicare_tax": totals["medicare"],
        "pretax_deductions": pretax_list,
        "posttax_deductions": posttax_list,
        "total_pretax": sum(
            (d["annual_total"] for d in pretax_list), ZERO,
        ),
        "total_posttax": sum(
            (d["annual_total"] for d in posttax_list), ZERO,
        ),
        "net_pay_total": totals["net"],
    }


def _compute_mortgage_interest(
    year: int,
    debt_schedules: dict[int, list],
) -> Decimal:
    """Sum mortgage/loan interest paid during the calendar year.

    Uses pre-generated amortization schedules (with properly prepared
    payments) and sums the interest portion of payments whose
    payment_date falls in the target year.

    This number appears on Schedule A (itemized deductions) so
    accuracy is critical.

    Args:
        year: Calendar year to sum interest for.
        debt_schedules: account_id -> list[AmortizationRow] mapping
            from _generate_debt_schedules().

    Returns:
        Total interest paid across all loan accounts in the year.
    """
    total_interest = ZERO

    for schedule in debt_schedules.values():
        for row in schedule:
            if row.payment_date.year == year:
                total_interest += row.interest

    return total_interest


def _empty_income_tax() -> dict:
    """Return an income/tax section with all zeros."""
    return {
        "gross_wages": ZERO,
        "federal_tax": ZERO,
        "state_tax": ZERO,
        "social_security_tax": ZERO,
        "medicare_tax": ZERO,
        "pretax_deductions": [],
        "posttax_deductions": [],
        "total_pretax": ZERO,
        "total_posttax": ZERO,
        "net_pay_total": ZERO,
        "mortgage_interest_total": ZERO,
    }
