"""
Shekel Budget App — Pension Calculator Service

Pure function service that calculates defined-benefit pension income
based on years of service, salary projection, and a benefit multiplier.

All functions are pure (no DB access) — data is passed in as arguments.
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

logger = logging.getLogger(__name__)

ZERO = Decimal("0")
TWO_PLACES = Decimal("0.01")


@dataclass
class PensionBenefit:
    """Result of a pension benefit calculation."""
    years_of_service: Decimal
    high_salary_average: Decimal
    annual_benefit: Decimal
    monthly_benefit: Decimal
    high_salary_years: list = field(default_factory=list)  # [(year, salary)]


def calculate_benefit(benefit_multiplier, consecutive_high_years,
                      hire_date, planned_retirement_date,
                      salary_by_year):
    """Calculate the projected pension benefit.

    Args:
        benefit_multiplier:      Decimal per-year multiplier (e.g. 0.0185 for 1.85%).
        consecutive_high_years:  int — number of consecutive highest salary years to average.
        hire_date:               date — employment start date.
        planned_retirement_date: date — planned retirement date.
        salary_by_year:          list of (year, annual_salary) tuples, sorted by year.

    Returns:
        PensionBenefit dataclass.
    """
    benefit_multiplier = Decimal(str(benefit_multiplier))
    years_of_service = _calculate_years_of_service(hire_date, planned_retirement_date)

    if not salary_by_year:
        return PensionBenefit(
            years_of_service=years_of_service,
            high_salary_average=ZERO,
            annual_benefit=ZERO,
            monthly_benefit=ZERO,
        )

    high_avg, high_years = _compute_high_salary_average(
        salary_by_year, consecutive_high_years
    )

    annual_benefit = (
        benefit_multiplier * years_of_service * high_avg
    ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    monthly_benefit = (annual_benefit / 12).quantize(
        TWO_PLACES, rounding=ROUND_HALF_UP
    )

    return PensionBenefit(
        years_of_service=years_of_service,
        high_salary_average=high_avg,
        annual_benefit=annual_benefit,
        monthly_benefit=monthly_benefit,
        high_salary_years=high_years,
    )


def project_salaries_by_year(annual_salary, raises, start_year, end_year):
    """Project annual salary for each year in a range using raise rules.

    This is a simplified projection that applies raises in order.
    For full raise logic, use paycheck_calculator._apply_raises().

    Args:
        annual_salary:  Decimal base salary.
        raises:         list of raise objects with .percentage, .flat_amount,
                        .effective_month, .effective_year, .is_recurring.
        start_year:     int first year to project.
        end_year:       int last year to project (inclusive).

    Returns:
        list of (year, Decimal salary) tuples.
    """
    from app.services.paycheck_calculator import _apply_raises

    class _FakePeriod:
        def __init__(self, year):
            self.start_date = date(year, 12, 1)  # end of year salary

    class _FakeProfile:
        def __init__(self, salary, raise_list):
            self.annual_salary = salary
            self.raises = raise_list

    profile = _FakeProfile(annual_salary, raises or [])
    result = []
    for year in range(start_year, end_year + 1):
        period = _FakePeriod(year)
        salary = _apply_raises(profile, period)
        result.append((year, salary))
    return result


def _calculate_years_of_service(hire_date, retirement_date):
    """Calculate years of service as a Decimal."""
    if not hire_date or not retirement_date:
        return ZERO
    delta_days = (retirement_date - hire_date).days
    if delta_days < 0:
        return ZERO
    return (Decimal(str(delta_days)) / Decimal("365.25")).quantize(
        TWO_PLACES, rounding=ROUND_HALF_UP
    )


def _compute_high_salary_average(salary_by_year, consecutive_high_years):
    """Find the consecutive window with the highest average salary.

    Args:
        salary_by_year:        list of (year, Decimal salary) sorted by year.
        consecutive_high_years: int window size.

    Returns:
        (best_avg, best_window) where best_window is the list of (year, salary) tuples.
    """
    n = len(salary_by_year)
    window_size = min(consecutive_high_years, n)

    if window_size <= 0:
        return ZERO, []

    best_avg = ZERO
    best_window = []

    for i in range(n - window_size + 1):
        window = salary_by_year[i:i + window_size]
        total = sum(Decimal(str(s)) for _, s in window)
        avg = (total / window_size).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        if avg > best_avg:
            best_avg = avg
            best_window = window

    return best_avg, best_window
