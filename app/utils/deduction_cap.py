"""
Shekel Budget App -- Per-deduction annual-cap clamp (salary domain)

A payroll deduction may carry an ``annual_cap`` (``PaycheckDeduction.annual_cap``):
a user-set dollar ceiling on how much of that deduction is taken across a single
calendar year.  Once the year-to-date total reaches the cap, the deduction stops
for the remainder of the year and resumes the following January.

This module owns the single definition of "how much of a deduction applies this
period given the cap and the year-to-date total so far."  Both the net-pay path
(``paycheck_calculator._calculate_deductions``) and the investment-contribution
timeline (``investment_projection.build_contribution_timeline``) clamp through
this one function so the two surfaces can never disagree on a capped deduction.

Pure function, no ORM access -- mirrors ``app/utils/money.py`` /
``app/utils/balance_predicates.py``.  The "annual" window is the calendar year,
matching the FICA wage-base cap (``paycheck_calculator._get_cumulative_wages``)
and the growth engine's year-boundary reset (``growth_engine._project_one_period``);
there is no per-deduction ``cap_year`` column, so the year is taken from the
period date by the caller.
"""

from decimal import Decimal

ZERO = Decimal("0")


def cap_period_amount(raw_amount, cumulative_before, annual_cap):
    """Clamp a period's deduction so the calendar-year cumulative never exceeds the cap.

    The returned amount is the largest part of ``raw_amount`` that fits under
    ``annual_cap`` given ``cumulative_before`` already taken this year: it lands
    the year-to-date total exactly on the cap in the binding period and returns
    ``ZERO`` for every period after the cap is exhausted.

    ``cumulative_before`` is summed from the *raw* (pre-cap) per-period amounts of
    the prior same-year periods.  That is mathematically identical to summing the
    capped amounts -- once the cap binds, ``cap - cumulative_raw`` is already
    <= 0, so both yield ZERO -- and avoids threading capped running state through
    callers that compute each period independently.

    Args:
        raw_amount:        Decimal uncapped deduction amount for this period
                           (>= 0).  Returned unchanged when no cap applies.
        cumulative_before: Decimal sum of this deduction's raw amounts for the
                           prior periods in the same calendar year (>= 0).
        annual_cap:        Decimal calendar-year ceiling for this deduction, or
                           ``None`` for an uncapped deduction.  ``None`` means
                           "no ceiling"; a stored ``Decimal`` is the ceiling
                           (the column ``CHECK`` forbids a stored 0, so a present
                           cap is always strictly positive).

    Returns:
        Decimal capped amount: ``raw_amount`` when ``annual_cap`` is ``None``;
        otherwise ``max(0, min(raw_amount, annual_cap - cumulative_before))``.
    """
    if annual_cap is None:
        return raw_amount
    raw_amount = Decimal(str(raw_amount))
    annual_cap = Decimal(str(annual_cap))
    cumulative_before = Decimal(str(cumulative_before))
    remaining = annual_cap - cumulative_before
    if remaining <= ZERO:
        return ZERO
    return min(raw_amount, remaining)
