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
from datetime import date, timedelta
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
    is_confirmed: bool = False


@dataclass(frozen=True)
class ContributionRecord:
    """A single contribution to an investment account.

    Used to replay actual or committed contributions through the growth
    projection so projections reflect real contribution history rather
    than assuming the same amount every period.

    Attributes:
        contribution_date: The pay period start date this contribution
            maps to.  Matched to periods by exact start_date.
        amount: The contribution amount.  Must be >= 0.  A zero amount
            represents a period where no contribution was made (only
            growth accrues) -- not the same as a missing entry, which
            falls back to periodic_contribution.
        is_confirmed: True if the contribution is Paid/Settled
            (historical fact).  False if Projected (future commitment).
    """

    contribution_date: date
    amount: Decimal
    is_confirmed: bool

    def __post_init__(self):
        """Validate contribution record fields at construction time.

        Catches invalid data immediately rather than producing wrong
        results deep in the projection loop.

        Raises:
            TypeError: If contribution_date is not a date, amount is not
                a Decimal, or is_confirmed is not a bool.
            ValueError: If amount is negative.
        """
        if not isinstance(self.contribution_date, date):
            raise TypeError(
                f"contribution_date must be a date, "
                f"got {type(self.contribution_date).__name__}"
            )
        if not isinstance(self.amount, Decimal):
            raise TypeError(
                f"amount must be a Decimal, got {type(self.amount).__name__}"
            )
        if self.amount < 0:
            raise ValueError(
                f"amount must be >= 0, got {self.amount}"
            )
        if not isinstance(self.is_confirmed, bool):
            raise TypeError(
                f"is_confirmed must be a bool, "
                f"got {type(self.is_confirmed).__name__}"
            )


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


def _build_contribution_lookup(contributions):
    """Build lookup dict mapping contribution_date to (amount, is_confirmed).

    Groups contributions by date, summing amounts.  is_confirmed is True
    only if ALL records on that date are confirmed.

    Args:
        contributions: Optional list of ContributionRecord instances.
            None or empty list returns None.

    Returns:
        dict mapping date to (Decimal amount, bool is_confirmed), or None
        if contributions is None or empty.
    """
    if not contributions:
        return None
    sorted_contribs = sorted(
        contributions, key=lambda c: c.contribution_date
    )
    lookup = {}
    for record in sorted_contribs:
        d = record.contribution_date
        if d in lookup:
            existing_amount, existing_confirmed = lookup[d]
            lookup[d] = (
                existing_amount + record.amount,
                # Confirmed only if ALL records on this date are confirmed.
                existing_confirmed and record.is_confirmed,
            )
        else:
            lookup[d] = (record.amount, record.is_confirmed)
    return lookup


def project_balance(
    current_balance,
    assumed_annual_return,
    periods,
    periodic_contribution=ZERO,
    employer_params=None,
    annual_contribution_limit=None,
    ytd_contributions_start=ZERO,
    contributions=None,
):
    """Project investment balance forward across pay periods.

    Growth is applied to the balance BEFORE the period's contribution
    is added, modeling that existing investments grow while new money
    is contributed.

    Args:
        current_balance:          Decimal starting balance.
        assumed_annual_return:    Decimal annual return rate (e.g. 0.07 for 7%).
        periods:                  List of period objects with .id, .start_date, .end_date.
        periodic_contribution:    Decimal employee contribution per period.  Used as the
                                  fallback when contributions is None or a period has no
                                  matching ContributionRecord.
        employer_params:          dict for employer contribution calculation (see above).
        annual_contribution_limit: Decimal annual limit (None for no limit).
        ytd_contributions_start:  Decimal contributions already made this year.
        contributions:            Optional list of ContributionRecord instances providing
                                  per-period contribution amounts.  When provided, each
                                  period looks up its amount by start_date; periods without
                                  a matching record fall back to periodic_contribution.
                                  A record with amount=0 is an explicit "no contribution" --
                                  distinct from a missing entry.  None or [] uses the static
                                  periodic_contribution for all periods.

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

    # Build contribution lookup from contribution records.  When provided,
    # each period looks up its amount from the dict; periods without an
    # entry fall back to periodic_contribution.  A $0 entry is an explicit
    # "no contribution" -- distinct from a missing entry.
    contribution_lookup = _build_contribution_lookup(contributions)

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

        # Determine this period's contribution and confirmed status.
        if (contribution_lookup is not None
                and period.start_date in contribution_lookup):
            period_contrib_amount, period_is_confirmed = (
                contribution_lookup[period.start_date]
            )
        else:
            period_contrib_amount = periodic_contribution
            period_is_confirmed = False

        # Step 2: Cap contribution at remaining limit.
        if remaining_limit is not None:
            contribution = min(period_contrib_amount, remaining_limit)
        else:
            contribution = period_contrib_amount
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
            is_confirmed=period_is_confirmed,
        ))

    return results


def reverse_project_balance(
    anchor_balance,
    assumed_annual_return,
    periods,
    periodic_contribution=ZERO,
    employer_params=None,
):
    """Reverse-project investment balance backward through pay periods.

    Given the balance at the END of the last period in the list,
    derives what the balance must have been at the START of each
    prior period using the exact inverse of the forward growth
    formula from project_balance():

        Forward:  end = start * (1 + rate) + contribution + employer
        Reverse:  start = (end - contribution - employer) / (1 + rate)

    This is used by the year-end summary to infer the Jan 1 balance
    when the account's anchor period is after January 1.

    Annual contribution limits are NOT tracked in reverse because the
    anchor balance already reflects historical limit enforcement.

    Args:
        anchor_balance:       Decimal balance at the end of the last period.
        assumed_annual_return: Decimal annual return rate (e.g. 0.105 for 10.5%).
        periods:              List of period objects in forward chronological
                              order.  The anchor_balance corresponds to the
                              end of the final period.
        periodic_contribution: Decimal employee contribution per period.
        employer_params:      dict for employer contribution calculation.

    Returns:
        List of ProjectedBalance in forward chronological order, one per
        period.  The start_balance of the first entry is the inferred
        balance before the first period (the "Jan 1 balance").
    """
    anchor_balance = Decimal(str(anchor_balance))
    assumed_annual_return = Decimal(str(assumed_annual_return))
    periodic_contribution = Decimal(str(periodic_contribution))

    contribution = max(periodic_contribution, ZERO)
    employer_contribution = calculate_employer_contribution(
        employer_params, contribution,
    )

    # Work backward: end_balance of each period is the start_balance
    # of the next period.  For the last period, end_balance = anchor.
    reversed_results = []
    end_balance = anchor_balance

    for period in reversed(periods):
        period_days = (period.end_date - period.start_date).days
        if period_days <= 0:
            period_days = 14

        period_return_rate = (
            (1 + assumed_annual_return)
            ** (Decimal(str(period_days)) / Decimal("365"))
            - 1
        )

        # Inverse of: end = start * (1 + rate) + contribution + employer
        divisor = 1 + period_return_rate
        start_balance = (
            (end_balance - contribution - employer_contribution) / divisor
        ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        start_balance = max(start_balance, ZERO)

        # Derive growth from the relationship:
        # end = start + growth + contribution + employer
        growth = end_balance - start_balance - contribution - employer_contribution

        reversed_results.append(ProjectedBalance(
            period_id=period.id,
            start_balance=start_balance,
            growth=growth,
            contribution=contribution,
            employer_contribution=employer_contribution,
            end_balance=end_balance,
            ytd_contributions=ZERO,
            contribution_limit_remaining=None,
            is_confirmed=False,
        ))

        # The start of this period is the end of the previous period.
        end_balance = start_balance

    # Return in forward chronological order.
    reversed_results.reverse()
    return reversed_results


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
