"""
Shekel Budget App -- Compound Growth Engine Service

Pure function service that projects investment account balances forward
over time, handling compound growth, periodic contributions, employer
contributions, and annual contribution limits.

All functions are pure (no DB access) -- data is passed in as arguments.
"""

import logging
from collections import namedtuple
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

logger = logging.getLogger(__name__)

ZERO = Decimal("0")
TWO_PLACES = Decimal("0.01")


@dataclass
class ProjectedBalance:
    """A single period's projected investment balance."""
    period_id: int
    start_balance: Decimal
    growth: Decimal
    contribution: Decimal
    employer_contribution: Decimal
    end_balance: Decimal
    ytd_contributions: Decimal
    contribution_limit_remaining: Decimal  # None if no limit


def calculate_employer_contribution(employer_params, employee_contribution):
    """Calculate the employer contribution for a single pay period.

    Args:
        employer_params: dict with keys:
            - type: 'none', 'flat_percentage', or 'match'
            - flat_percentage: Decimal (for flat_percentage type)
            - match_percentage: Decimal (for match type)
            - match_cap_percentage: Decimal (for match type)
            - gross_biweekly: Decimal (gross pay per period)
        employee_contribution: Decimal amount the employee contributed.

    Returns:
        Decimal employer contribution amount.
    """
    if not employer_params:
        return ZERO

    emp_type = employer_params.get("type", "none")
    gross = Decimal(str(employer_params.get("gross_biweekly", 0)))

    if emp_type == "flat_percentage":
        pct = Decimal(str(employer_params.get("flat_percentage", 0)))
        return (gross * pct).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    if emp_type == "match":
        match_pct = Decimal(str(employer_params.get("match_percentage", 0)))
        cap_pct = Decimal(str(employer_params.get("match_cap_percentage", 0)))
        matchable_salary = (gross * cap_pct).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )
        matched_amount = min(employee_contribution, matchable_salary)
        return (matched_amount * match_pct).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )

    return ZERO


def project_balance(
    current_balance,
    assumed_annual_return,
    periods,
    periodic_contribution=ZERO,
    employer_params=None,
    annual_contribution_limit=None,
    ytd_contributions_start=ZERO,
):
    """Project investment balance forward across pay periods.

    Growth is applied to the balance BEFORE the period's contribution
    is added, modeling that existing investments grow while new money
    is contributed.

    Args:
        current_balance:          Decimal starting balance.
        assumed_annual_return:    Decimal annual return rate (e.g. 0.07 for 7%).
        periods:                  List of period objects with .id, .start_date, .end_date.
        periodic_contribution:    Decimal employee contribution per period.
        employer_params:          dict for employer contribution calculation (see above).
        annual_contribution_limit: Decimal annual limit (None for no limit).
        ytd_contributions_start:  Decimal contributions already made this year.

    Returns:
        List of ProjectedBalance, one per period.
    """
    current_balance = Decimal(str(current_balance))
    assumed_annual_return = Decimal(str(assumed_annual_return))
    periodic_contribution = Decimal(str(periodic_contribution))
    ytd_contributions = Decimal(str(ytd_contributions_start))

    if annual_contribution_limit is not None:
        annual_contribution_limit = Decimal(str(annual_contribution_limit))
        remaining_limit = annual_contribution_limit - ytd_contributions
        remaining_limit = max(remaining_limit, ZERO)
    else:
        remaining_limit = None

    results = []
    prev_year = None

    for period in periods:
        period_year = period.start_date.year

        # Year boundary reset.
        if prev_year is not None and period_year != prev_year:
            ytd_contributions = ZERO
            if annual_contribution_limit is not None:
                remaining_limit = annual_contribution_limit

        prev_year = period_year

        start_balance = current_balance

        # Step 1: Growth on existing balance.
        period_days = (period.end_date - period.start_date).days
        if period_days <= 0:
            period_days = 14  # fallback for degenerate periods

        period_return_rate = (
            (1 + assumed_annual_return) ** (Decimal(str(period_days)) / Decimal("365")) - 1
        )
        growth = (current_balance * period_return_rate).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )

        # Step 2: Cap contribution at remaining limit.
        if remaining_limit is not None:
            contribution = min(periodic_contribution, remaining_limit)
        else:
            contribution = periodic_contribution
        contribution = max(contribution, ZERO)

        # Step 3: Employer contribution.
        employer_contribution = calculate_employer_contribution(
            employer_params, contribution
        )

        # Step 4: Update balance.  Clamp to zero -- standard investment
        # accounts cannot go negative (M-06).
        current_balance = max(
            start_balance + growth + contribution + employer_contribution,
            ZERO,
        )

        # Step 5: Track limits.
        ytd_contributions += contribution
        if remaining_limit is not None:
            remaining_limit -= contribution
            remaining_limit = max(remaining_limit, ZERO)

        results.append(ProjectedBalance(
            period_id=period.id,
            start_balance=start_balance,
            growth=growth,
            contribution=contribution,
            employer_contribution=employer_contribution,
            end_balance=current_balance,
            ytd_contributions=ytd_contributions,
            contribution_limit_remaining=remaining_limit,
        ))

    return results


SyntheticPeriod = namedtuple("SyntheticPeriod", ["id", "start_date", "end_date"])


def generate_projection_periods(start_date, end_date, cadence_days=14):
    """Generate synthetic biweekly periods for long-term projections.

    Creates lightweight period objects compatible with project_balance().
    No database interaction -- pure function.

    Args:
        start_date:    date -- first period start.
        end_date:      date -- generate periods until start_date would exceed this.
        cadence_days:  int -- days per period (default 14 for biweekly).

    Returns:
        List of SyntheticPeriod namedtuples with .id, .start_date, .end_date.
    """
    periods = []
    current = start_date
    period_id = 1
    while current <= end_date:
        period_end = current + timedelta(days=cadence_days - 1)
        periods.append(SyntheticPeriod(
            id=period_id,
            start_date=current,
            end_date=period_end,
        ))
        current += timedelta(days=cadence_days)
        period_id += 1
    return periods
