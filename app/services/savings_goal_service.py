"""
Shekel Budget App -- Savings Goal Service

Pure functions for savings goal calculations. No database writes, no
Flask imports -- called by the savings route to compute metrics.
"""

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP

from app import ref_cache
from app.enums import GoalModeEnum, IncomeUnitEnum, RecurrencePatternEnum
from app.utils.dates import add_months, months_between
from app.utils.money import (
    MONTHS_PER_YEAR,
    PAY_PERIODS_PER_YEAR,
    round_money,
    round_money_ceiling,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GoalTrajectory:
    """When a savings goal lands at the current rate, and how that reads.

    What :func:`calculate_trajectory` returns.  A frozen value object since plan
    step X-aa (ruling R-CO); it was a four-key dict nested INSIDE the
    :class:`~app.services.savings_dashboard_service.GoalProgress` record
    plan step X-w4 created -- a typed outer with a dict inner, which is the
    inconsistency ruling R-CI exists to remove, sitting in R-CI's own container.

    **It is never absent.**  Its producer has three ``return`` statements and
    every one of them fills all four fields, so a consumer asks about the
    FIELDS, never about the record.  X-w4 typed the field that holds it as
    ``dict | None`` and ``savings/dashboard.html`` guarded it with a truthiness
    test; both were unreachable, and both went with this type -- a nullable that
    cannot be null is ruling R-CA's defect, and a guard that cannot be false is
    not a guard.

    **The four fields ARE nullable, and each nullable means one thing**, which
    is why they are read individually:

    Attributes:
        months_to_goal: Whole months until the balance reaches the target.
            ``0`` when the goal is already met -- that branch is tested FIRST,
            so a funded goal whose recurring transfer was deleted still reports
            ``0`` rather than ``None`` -- and ``None`` when the goal is NOT met
            and there is no positive monthly contribution, the state the card
            renders as "No recurring contribution".  ``0`` and ``None`` are
            DIFFERENT answers and the template branches on both.  (The
            precedence was unstated until plan step X-aa's adversarial review
            reached the overlap by execution.)
        projected_completion_date: The date the goal is projected to be met,
            or ``None`` exactly when :attr:`months_to_goal` is.
        pace: ``'ahead'`` / ``'on_track'`` / ``'behind'`` against the goal's
            target date, or ``None`` when there is no ACTIONABLE target date
            (none set, or one already past).  Compared against literals by the
            template's ``pace_pill`` macro, which ``_money_macros.html`` records
            as the sanctioned pattern here: these are this service's own
            vocabulary, not a reference-table ``.name`` column.
        required_monthly: The monthly contribution needed to hit the target
            date.  ``None`` when there is no ACTIONABLE target date (none set,
            or one already past) -- **and also when an actionable target date
            falls inside the current calendar month**, which leaves zero whole
            months to spread the gap over
            (:func:`_compute_required_monthly` divides by
            :func:`app.utils.dates.months_between`).  That second case is
            reachable and was missing from this list until plan step X-aa's
            adversarial review measured it: a goal targeted later THIS month
            renders "behind" with no "Increase to $X/mo" line beside it.  The
            goal-met branch is the exception that proves the rule -- it returns
            ``0.00`` for the same date, because nothing more is required.
    """

    months_to_goal: int | None
    projected_completion_date: date | None
    pace: str | None
    required_monthly: Decimal | None


@dataclass(frozen=True)
class SavingsCoverage:
    """How long the user's liquid savings would cover their expenses.

    What :func:`calculate_savings_metrics` returns, and what the cockpit's
    emergency-fund footer renders.  A frozen value object since plan step X-aa
    (ruling R-CO); it was a three-key dict.

    All three fields are one figure expressed in three units, so they are a
    record rather than three returns -- and they are all present always: the
    zero-expenses branch returns three zeros rather than omitting anything.

    **The two derived units are converted from the ROUNDED months, not from the
    raw ratio**, so they agree with the months figure beside them at the
    displayed grain and can differ from the raw answer by one tenth.  Measured
    on the prod-shape clone: `$4,076.92` over `$5,667.63` is `0.7193` raw
    months, rendered `0.7 months` / `1.5 paychecks`, where the raw ratio gives
    `1.6`.  Which of the two is right is a real fork -- internal consistency
    against accuracy -- and it is finding N-120, not something this record
    settles.  This paragraph said "the same span in biweekly pay periods" until
    plan step X-aa's adversarial review measured that it is not.

    Attributes:
        months_covered: Savings divided by average monthly expenses, to one
            decimal place.  ``0`` when there are no expenses to cover.
        paychecks_covered: :attr:`months_covered` expressed in biweekly pay
            periods (see above -- converted from the rounded value).
        years_covered: :attr:`months_covered` expressed in years, likewise.
    """

    months_covered: Decimal
    paychecks_covered: Decimal
    years_covered: Decimal


def resolve_goal_target(
    goal_mode_id: int,
    target_amount: Decimal | None,
    income_unit_id: int | None,
    income_multiplier: Decimal | None,
    net_biweekly_pay: Decimal,
) -> Decimal:
    """Resolve the dollar target for a savings goal.

    For fixed-mode goals, returns target_amount directly.
    For income-relative goals, computes the target from the income
    multiplier and the user's current net biweekly pay.

    This is a pure function -- it does not query the database.

    Conversion factors:
        Paychecks: target = multiplier * net_biweekly_pay
        Months:    target = multiplier * (net_biweekly_pay * 26 / 12)

    Intermediate results are NOT quantized -- only the final result
    is rounded to 2 decimal places to avoid penny-level rounding
    drift (e.g. 3 months at $2,000/paycheck = exactly $13,000.00,
    not $12,999.99).

    Args:
        goal_mode_id: The goal's mode ID (from ref.goal_modes).
        target_amount: The stored target amount (used for fixed goals;
            may be None for income-relative goals).
        income_unit_id: The income unit ID (from ref.income_units).
            Required when mode is income-relative.
        income_multiplier: The multiplier value.  Required when mode
            is income-relative.
        net_biweekly_pay: Current projected net biweekly pay from
            the paycheck calculator.  Used only for income-relative
            goals.

    Returns:
        The resolved dollar target as a Decimal, quantized to 2
        decimal places.

    Raises:
        ValueError: If the goal is income-relative but income_unit_id
            or income_multiplier is None.
    """

    fixed_id = ref_cache.goal_mode_id(GoalModeEnum.FIXED)

    if goal_mode_id == fixed_id:
        if target_amount is None:
            return Decimal("0.00")
        return target_amount

    # Income-relative mode -- validate required fields.
    if income_unit_id is None or income_multiplier is None:
        raise ValueError(
            "Income-relative goal requires income_unit_id and "
            "income_multiplier."
        )

    multiplier = (
        income_multiplier if isinstance(income_multiplier, Decimal)
        else Decimal(str(income_multiplier))
    )

    paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
    months_id = ref_cache.income_unit_id(IncomeUnitEnum.MONTHS)

    if income_unit_id == paychecks_id:
        result = multiplier * net_biweekly_pay
    elif income_unit_id == months_id:
        # Convert biweekly to monthly: 26 pay periods / 12 months.
        # Quantize only the final result, not the intermediate.
        monthly_net = net_biweekly_pay * PAY_PERIODS_PER_YEAR / MONTHS_PER_YEAR
        result = multiplier * monthly_net
    else:
        # Unknown unit -- defensive fallback with warning.
        logger.warning(
            "Unknown income_unit_id=%d for income-relative goal; "
            "falling back to target_amount.",
            income_unit_id,
        )
        return target_amount if target_amount is not None else Decimal("0.00")

    return round_money(result)


def calculate_required_contribution(current_balance, target_amount, remaining_periods):
    """Calculate the required contribution per period to reach a savings goal.

    Args:
        current_balance:   Decimal -- current account balance.
        target_amount:     Decimal -- the goal target.
        remaining_periods: int -- number of pay periods until the target date.

    Returns:
        Decimal -- required contribution per period, or Decimal("0.00") if
        already met, or None if past due (no remaining periods).
    """
    if current_balance is None:
        current_balance = Decimal("0.00")
    else:
        current_balance = Decimal(str(current_balance))
    target_amount = Decimal(str(target_amount))

    gap = target_amount - current_balance
    if gap <= 0:
        return Decimal("0.00")

    if remaining_periods is None or remaining_periods <= 0:
        return None

    return round_money(gap / remaining_periods)


def calculate_savings_metrics(
    savings_balance: Decimal | None,
    average_monthly_expenses: Decimal | None,
) -> SavingsCoverage:
    """Calculate how long savings would cover expenses.

    Args:
        savings_balance:          Decimal -- total savings balance.
        average_monthly_expenses: Decimal -- average monthly expense total.

    Returns:
        The :class:`SavingsCoverage` (a three-key dict until plan step
        X-aa, ruling R-CO).  Three zeros when there are no expenses to
        cover -- a real answer, not an absent one.
    """
    if savings_balance is None:
        savings_balance = Decimal("0.00")
    else:
        savings_balance = Decimal(str(savings_balance))

    if average_monthly_expenses is None or Decimal(str(average_monthly_expenses)) <= 0:
        return SavingsCoverage(
            months_covered=Decimal("0"),
            paychecks_covered=Decimal("0"),
            years_covered=Decimal("0"),
        )

    avg_expenses = Decimal(str(average_monthly_expenses))
    months = (savings_balance / avg_expenses).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )

    return SavingsCoverage(
        months_covered=months,
        paychecks_covered=(
            months * PAY_PERIODS_PER_YEAR / MONTHS_PER_YEAR
        ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP),
        years_covered=(months / MONTHS_PER_YEAR).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP,
        ),
    )


def count_periods_until(target_date, periods):
    """Count pay periods between today and the target date.

    Args:
        target_date: date -- the goal's target date.
        periods:     List of PayPeriod objects ordered by index.

    Returns:
        int -- count of periods from today to the target date (inclusive).
    """
    if target_date is None:
        return None

    today = date.today()
    count = 0
    for period in periods:
        if period.start_date >= today and period.start_date <= target_date:
            count += 1
    return count


def amount_to_monthly(
    amount: Decimal,
    pattern_id: int,
    interval_n: int = 1,
) -> Decimal | None:
    """Convert a per-occurrence amount to its monthly equivalent.

    Uses the biweekly pay period convention (26 periods per year) to
    translate recurrence frequencies into monthly values.  Returns
    None for one-time or unknown patterns.

    Conversion factors (biweekly-to-monthly: 26 pay periods / 12 months):

      - every_period:    amount * 26 / 12
      - every_n_periods: amount * (26 / n) / 12
      - monthly:         amount  (already monthly)
      - monthly_first:   amount  (already monthly)
      - quarterly:       amount / 3
      - semi_annual:     amount / 6
      - annual:          amount / 12
      - once:            None (not a recurring commitment)

    The result is NOT quantized -- callers are responsible for rounding
    at their own aggregation boundary.

    Args:
        amount: The per-occurrence Decimal amount.
        pattern_id: The recurrence pattern integer ID (from ref_cache).
        interval_n: The interval for EVERY_N_PERIODS patterns.
            Defaults to 1 (every period).

    Returns:
        Decimal monthly equivalent, or None for non-recurring patterns.
    """

    every_period_id = ref_cache.recurrence_pattern_id(
        RecurrencePatternEnum.EVERY_PERIOD
    )
    every_n_id = ref_cache.recurrence_pattern_id(
        RecurrencePatternEnum.EVERY_N_PERIODS
    )
    monthly_id = ref_cache.recurrence_pattern_id(
        RecurrencePatternEnum.MONTHLY
    )
    monthly_first_id = ref_cache.recurrence_pattern_id(
        RecurrencePatternEnum.MONTHLY_FIRST
    )
    quarterly_id = ref_cache.recurrence_pattern_id(
        RecurrencePatternEnum.QUARTERLY
    )
    semi_annual_id = ref_cache.recurrence_pattern_id(
        RecurrencePatternEnum.SEMI_ANNUAL
    )
    annual_id = ref_cache.recurrence_pattern_id(
        RecurrencePatternEnum.ANNUAL
    )
    once_id = ref_cache.recurrence_pattern_id(
        RecurrencePatternEnum.ONCE
    )

    if pattern_id == once_id:
        # One-time patterns are not a recurring monthly commitment.
        return None

    # Single-return dispatch (one Decimal-or-None per pattern); the per-pattern
    # conversion factors are documented in the module docstring above.
    if pattern_id == every_period_id:
        monthly = amount * PAY_PERIODS_PER_YEAR / MONTHS_PER_YEAR
    elif pattern_id == every_n_id:
        n = Decimal(str(interval_n))
        monthly = amount * PAY_PERIODS_PER_YEAR / n / MONTHS_PER_YEAR
    elif pattern_id in (monthly_id, monthly_first_id):
        monthly = amount
    elif pattern_id == quarterly_id:
        monthly = amount / Decimal("3")
    elif pattern_id == semi_annual_id:
        monthly = amount / Decimal("6")
    elif pattern_id == annual_id:
        monthly = amount / MONTHS_PER_YEAR
    else:
        # Unrecognized pattern id.
        monthly = None
    return monthly


def calculate_trajectory(
    current_balance: Decimal,
    target_amount: Decimal,
    monthly_contribution: Decimal,
    target_date: date | None = None,
) -> GoalTrajectory:
    """Calculate savings goal completion trajectory and pace.

    Computes how long it will take to reach the goal at the current
    savings rate, and whether the user is on track relative to their
    target date (if one is set).

    This is a pure function -- it does not query the database.

    Args:
        current_balance: Current savings account balance.
        target_amount: Resolved goal target (from resolve_goal_target()).
        monthly_contribution: Monthly contribution amount toward this
            goal. Zero if no recurring contribution exists.
        target_date: Optional target completion date for pace comparison.

    Returns:
        The :class:`GoalTrajectory` -- see it for what each nullable means.
        NEVER ``None``: all three branches below fill every field, which is
        why plan step X-aa could delete the ``dict | None`` its consumer's
        field carried and the template guard that tested it (ruling R-CO).
    """
    today = date.today()
    remaining = target_amount - current_balance

    # A target date is only actionable if it is strictly in the future.
    actionable_target = target_date is not None and target_date > today

    if remaining <= Decimal("0.00"):
        # Goal already met.
        return GoalTrajectory(
            months_to_goal=0,
            projected_completion_date=today,
            pace=_compute_pace(today, target_date) if actionable_target else None,
            required_monthly=Decimal("0.00") if actionable_target else None,
        )

    if monthly_contribution <= Decimal("0.00"):
        # No contribution -- cannot project a completion date.
        return GoalTrajectory(
            months_to_goal=None,
            projected_completion_date=None,
            pace="behind" if actionable_target else None,
            required_monthly=_compute_required_monthly(remaining, target_date),
        )

    # Ceiling division in Decimal land -- no float conversion.
    months = int(
        (remaining / monthly_contribution).to_integral_value(
            rounding=ROUND_CEILING
        )
    )

    projected = add_months(today, months)
    pace = _compute_pace(projected, target_date) if actionable_target else None

    return GoalTrajectory(
        months_to_goal=months,
        projected_completion_date=projected,
        pace=pace,
        required_monthly=_compute_required_monthly(remaining, target_date),
    )


def _compute_pace(projected_date: date, target_date: date) -> str:
    """Compare projected completion to target date by year-month.

    Returns 'ahead' if projected is before the target month,
    'on_track' if the same month, 'behind' if projected is after.

    Args:
        projected_date: The projected completion date.
        target_date: The user's target completion date.

    Returns:
        One of 'ahead', 'on_track', or 'behind'.
    """
    proj = (projected_date.year, projected_date.month)
    tgt = (target_date.year, target_date.month)

    if proj < tgt:
        return "ahead"
    if proj == tgt:
        return "on_track"
    return "behind"


def _compute_required_monthly(
    remaining: Decimal,
    target_date: date | None,
) -> Decimal | None:
    """Compute the monthly contribution needed to hit target_date.

    Returns None if target_date is None or in the past/present.
    Uses ROUND_CEILING so the user contributes at least enough.

    Args:
        remaining: Dollar amount still needed (target - balance).
        target_date: The user's target date, or None.

    Returns:
        Decimal monthly amount rounded up, or None.
    """
    if target_date is None:
        return None

    today = date.today()
    if target_date <= today:
        return None

    months_available = months_between(today, target_date)

    if months_available <= 0:
        return None

    return round_money_ceiling(remaining / Decimal(str(months_available)))
