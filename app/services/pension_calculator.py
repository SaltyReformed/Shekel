"""
Shekel Budget App -- Pension Calculator Service

Pure function service that calculates defined-benefit pension income
based on years of service, salary projection, and a benefit multiplier.

All functions are pure (no DB access) -- data is passed in as arguments.
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app.services.salary_raises import apply_raises
from app.utils.money import round_money

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
        consecutive_high_years:  int -- number of consecutive highest salary years to average.
        hire_date:               date -- employment start date.
        planned_retirement_date: date -- planned retirement date.
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

    annual_benefit = round_money(
        benefit_multiplier * years_of_service * high_avg
    )

    monthly_benefit = round_money(annual_benefit / 12)

    return PensionBenefit(
        years_of_service=years_of_service,
        high_salary_average=high_avg,
        annual_benefit=annual_benefit,
        monthly_benefit=monthly_benefit,
        high_salary_years=high_years,
    )


def project_salaries_by_year(annual_salary, raises, start_year, end_year):
    """Project annual salary for each year in a range.

    Delegates each year's salary to the shared
    :func:`app.services.salary_raises.apply_raises` so pension
    projections and the paycheck pipeline apply the identical raise rule
    (sort order, recurring compounding, one-time gating, and the last year
    each raise is believed).

    **They share ONE TERMINATION RULE as of plan step salary:S3-c** (ruling
    **R-SAL11**), which they did not before.  This function used to fabricate
    a terminal year for every raise from
    ``auth.user_settings.merit_raise_horizon_years`` -- one number for the
    whole owner, applied by ``/retirement`` alone, keyed by raise TYPE --
    while the paycheck engine compounded every recurring raise forever off
    the same rows.  The end year is stored per raise now, both engines read
    it off the row, and neither invents one, so there is no cutoff left for
    the two to disagree about.

    **They do NOT share an AS-OF rule, and an adversarial review of that step
    corrected a draft that said "one model" flat.**  The engine prices each
    payday at its own date; this function evaluates each YEAR at December 1.
    So a payday in a year whose raise lands after January is priced here from
    that year's post-raise salary and by the engine from the salary on the
    day.  Unifying that is plan step **salary:S3**'s, which makes the engine
    price the whole projected horizon.

    Each year is evaluated as of December 1, so every raise effective
    during that year -- recurring or one-time -- is applied.

    Args:
        annual_salary:      Decimal base salary.
        raises:             list of raise objects with .percentage,
                            .flat_amount, .effective_month,
                            .effective_year, .is_recurring and
                            .terminal_year.
        start_year:         int first year to project (the current year, by
                            caller convention -- every call site passes the
                            read pass's ``as_of.year``).
        end_year:           int last year to project (inclusive).

    Returns:
        list of (year, Decimal salary) tuples.
    """
    return [
        (year, apply_raises(annual_salary, raises, date(year, 12, 1)))
        for year in range(start_year, end_year + 1)
    ]


def project_profile_salaries(profile, start_year, end_year):
    """Project a salary profile's annual salaries to the retirement horizon.

    Thin convenience over :func:`project_salaries_by_year` that marshals a
    :class:`~app.models.salary_profile.SalaryProfile` (its ``annual_salary``
    and ``raises``) into the plain-input contract.  Shared by the two
    retirement consumers that project the primary profile's salary path --
    the gap-comparison net-biweekly scaling and the P1b employer-base
    resolver -- so the ``Decimal(str(...))`` marshalling and the
    current-year start live in one place.

    Args:
        profile:             A salary profile exposing ``annual_salary`` and
                             ``raises``.
        start_year:          int first year to project (the current year).
        end_year:            int last year to project (inclusive).

    Returns:
        list of (year, Decimal salary) tuples.
    """
    return project_salaries_by_year(
        Decimal(str(profile.annual_salary)),
        profile.raises,
        start_year,
        end_year,
    )


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
        avg = round_money(total / window_size)
        if avg > best_avg:
            best_avg = avg
            best_window = window

    return best_avg, best_window
