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
from app.enums import GoalModeEnum, IncomeUnitEnum
from app.services.pay_calendar import PayCadence
from app.utils.dates import add_months, months_between
from app.utils.money import (
    MONTHS_PER_YEAR,
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

    **Each is quantized ONCE from the raw ratio** (plan step X-z5, ruling R-CS,
    closing finding N-120).  The two derived units were converted from the
    ROUNDED months -- rounded twice, so each could differ from the true answer
    by a tenth: `$4,076.92` over `$5,667.63` is `0.719334` raw months, and the
    footer rendered `0.7 months / 1.5 paychecks` where the raw ratio gives
    `1.6`.  The three are therefore no longer exact conversions of each other
    at the displayed grain, which is the price of each being right about its
    own question; :func:`calculate_savings_metrics` carries the measurement.
    (This paragraph said "the same span in biweekly pay periods" until plan
    step X-aa's adversarial review measured that it was not.)

    Attributes:
        months_covered: Savings divided by average monthly expenses, at the
            footer's one-decimal grain.  ``0`` when there are no expenses to
            cover.
        paychecks_covered: How many biweekly pay periods the same savings
            cover -- the raw ratio through
            :meth:`~app.services.pay_calendar.PayCadence.months_to_paychecks`,
            then rounded.  The owner's own cadence since plan step R7a-2a; it
            was a hardcoded ``26/12``.
        years_covered: The same span in years -- the raw ratio over ``12``,
            then rounded.
    """

    months_covered: Decimal
    paychecks_covered: Decimal
    years_covered: Decimal


@dataclass(frozen=True)
class GoalTargetSpec:
    """The four columns that define what a savings goal is AIMING AT.

    :func:`resolve_goal_target`'s input, grouped into a value at plan step
    R7a-2a.  They were four positional parameters beside the owner's pay, and
    adding the fifth input that step gives the resolver -- the owner's pay
    CADENCE, which is what turns a per-paycheck figure into a monthly one --
    would have taken the signature to six.  A parameter object rather than a
    raised ``max-args``: the four are meaningless apart (a multiplier with no
    unit names no target, a unit with no mode is not consulted) and they are
    exactly ``budget.savings_goals``' four target columns, so they are one
    concept rather than a bag assembled to satisfy a count.

    Scalars rather than the ``SavingsGoal`` row, which is what the four
    parameters were: this service is pure and Flask-free by contract, and its
    tests drive the resolver over shapes no row has to exist for.

    Mirrors the ``RecurrenceSpec`` / ``TransferSpec`` / ``RegistrationSpec``
    precedent -- a frozen record of what a caller STATES.

    Attributes:
        goal_mode_id: The goal's mode id (``ref.goal_modes``).  FIXED means the
            target is :attr:`target_amount`; anything else is income-relative
            and reads the two fields below.
        target_amount: The stored dollar target.  Used for a FIXED goal, and
            ``None`` by design for an income-relative one -- where it is still
            the fallback if the unit cannot be read.
        income_unit_id: The unit the multiplier counts (``ref.income_units``:
            paychecks or months).  ``None`` for a FIXED goal.
        income_multiplier: How many of that unit the goal targets.  ``None``
            for a FIXED goal.
    """

    goal_mode_id: int
    target_amount: Decimal | None
    income_unit_id: int | None
    income_multiplier: Decimal | None


def resolve_goal_target(
    spec: GoalTargetSpec,
    net_biweekly_pay: Decimal,
    pay_cadence: PayCadence,
) -> Decimal:
    """Resolve the dollar target for a savings goal.

    For fixed-mode goals, returns the spec's ``target_amount`` directly.
    For income-relative goals, computes the target from the income
    multiplier and the user's current net biweekly pay.

    This is a pure function -- it does not query the database.

    Conversion factors:
        Paychecks: target = multiplier * net_biweekly_pay
        Months:    target = multiplier * pay_cadence.per_paycheck_to_monthly(
                   net_biweekly_pay)

    **The months conversion is per-OWNER since plan step R7a-2a**, where it read
    a hardcoded ``26 / 12``.  A "3 months of salary" goal for a weekly-paid
    owner resolved to ``multiplier * pay * 26 / 12`` -- half the true target,
    because they receive 52 paychecks a year and not 26.

    Intermediate results are NOT quantized -- only the final result
    is rounded to 2 decimal places to avoid penny-level rounding
    drift (e.g. 3 months at $2,000/paycheck = exactly $13,000.00,
    not $12,999.99).

    Args:
        spec: What the goal is aiming at -- see :class:`GoalTargetSpec`.
        net_biweekly_pay: Current projected net pay for one paycheck, from
            the paycheck calculator.  Used only for income-relative
            goals.
        pay_cadence: How often the owner is paid
            (:class:`~app.services.pay_calendar.PayCadence`).  Read only by the
            MONTHS unit, which is the only branch that leaves paycheck space.

    Returns:
        The resolved dollar target as a Decimal, quantized to 2
        decimal places.

    Raises:
        ValueError: If the goal is income-relative but ``income_unit_id``
            or ``income_multiplier`` is None.
    """

    fixed_id = ref_cache.goal_mode_id(GoalModeEnum.FIXED)

    if spec.goal_mode_id == fixed_id:
        if spec.target_amount is None:
            return Decimal("0.00")
        return spec.target_amount

    # Income-relative mode -- validate required fields.
    if spec.income_unit_id is None or spec.income_multiplier is None:
        raise ValueError(
            "Income-relative goal requires income_unit_id and "
            "income_multiplier."
        )

    multiplier = (
        spec.income_multiplier if isinstance(spec.income_multiplier, Decimal)
        else Decimal(str(spec.income_multiplier))
    )

    paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
    months_id = ref_cache.income_unit_id(IncomeUnitEnum.MONTHS)

    if spec.income_unit_id == paychecks_id:
        result = multiplier * net_biweekly_pay
    elif spec.income_unit_id == months_id:
        # Paycheck space to month space, at the OWNER's cadence.
        # Quantize only the final result, not the intermediate.
        result = multiplier * pay_cadence.per_paycheck_to_monthly(
            net_biweekly_pay,
        )
    else:
        # Unknown unit -- defensive fallback with warning.
        logger.warning(
            "Unknown income_unit_id=%d for income-relative goal; "
            "falling back to target_amount.",
            spec.income_unit_id,
        )
        return (
            spec.target_amount if spec.target_amount is not None
            else Decimal("0.00")
        )

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


# The grain the emergency-fund footer renders its three coverage units at: one
# decimal place.  A display grain rather than a money one -- these are spans of
# time, not amounts -- so it is named here and not in ``app.utils.money``, whose
# rounding helpers are all two-place currency.
_COVERAGE_GRAIN = Decimal("0.1")


def _to_coverage_grain(value: Decimal) -> Decimal:
    """Round one coverage figure to the footer's displayed grain.

    The single quantization point for all three units of
    :class:`SavingsCoverage`, so each is rounded exactly ONCE from the raw
    ratio (plan step X-z5, ruling R-CS, finding N-120) rather than two of them
    being converted from an already-rounded third.

    Args:
        value: The unrounded coverage figure, in whichever unit.

    Returns:
        *value* at :data:`_COVERAGE_GRAIN`, half-up.
    """
    return value.quantize(_COVERAGE_GRAIN, rounding=ROUND_HALF_UP)


def calculate_savings_metrics(
    savings_balance: Decimal,
    average_monthly_expenses: Decimal,
    pay_cadence: PayCadence,
) -> SavingsCoverage:
    """Calculate how long savings would cover expenses.

    **Neither input is nullable, and neither was reachable as one** (plan step
    X-z4, ruling R-CS).  Both were ``Decimal | None`` with a ``None`` arm each.
    The ONE production caller
    (:func:`~app.services.savings_dashboard_service._orchestrator._compute_emergency_fund_section`)
    passes ``_sum_liquid_balances(...)`` and ``_compute_avg_monthly_expenses(...)``,
    which return a ``Decimal`` on every path -- and NO test anywhere passed
    ``savings_balance=None``, so that branch had zero exercisers in the whole
    repository.  That is ruling R-CA's "a nullable that cannot be null", one
    function over from where plan step X-aa closed it for
    :class:`GoalTrajectory`.  The four ``Decimal(str(x))`` coercions went with
    them: they defended against types this signature forbids, and without them
    a float caller raises ``TypeError`` rather than silently succeeding.

    The non-positive guard STAYS and is a different thing entirely: a user with
    no recorded expenses is a real state, and three zeros is a real answer to
    "how long would your savings last" when nothing is being spent.

    **Each of the three units is quantized ONCE, from the RAW ratio** (plan step
    X-z5, ruling R-CS, finding N-120).  The months figure was rounded to
    :data:`_COVERAGE_GRAIN` FIRST and the other two derived from that rounded
    value, so both were rounded twice.  That is the drift the two functions
    beside this one forbid in terms -- :func:`resolve_goal_target`
    ("Intermediate results are NOT quantized") and
    ``obligations_aggregator.template_monthly_or_none`` (full precision until
    one ``round_money`` at the boundary).

    What the paychecks figure ANSWERS is "how many pay periods would my savings
    cover", which is ``savings / (monthly expenses / paychecks per month)``.
    Converting the
    already-rounded months answers a different question -- how many pay periods
    the DISPLAYED months figure corresponds to -- which is a fact about the
    display, not about the user's money.  Measured on the prod-shape clone:
    ``$4,076.92`` over ``$5,667.63`` is ``0.719334`` raw months, which rendered
    ``0.7 months / 1.5 paychecks`` where the raw ratio gives ``1.6``.  A sweep
    over 40,817 (savings, expenses) shapes differed on ``paychecks_covered`` in
    53.5% of them and on ``years_covered`` in 4.2%, worst gap ``0.2`` paychecks:
    ``$250`` against ``$1,000``/mo rendered ``0.3 months / 0.7 paychecks``
    against a raw answer of ``0.5``, a 40% error on that figure.

    The cost, stated rather than argued away: the three are no longer exact
    conversions of each other at the displayed grain, so a reader multiplying
    the rendered ``0.7`` by ``26/12`` gets ``1.5`` and the line beside it says
    ``1.6``.  Each figure is the best answer to its own question instead.
    (Every figure in the two paragraphs above was measured at the developer's
    biweekly cadence, which is where ``26/12`` comes from; the RULE is
    *paychecks per month* and is the owner's own since plan step R7a-2a.)

    **The shape that cost can take, measured and ruled acceptable** (plan step
    X-z8, ruling R-CW, out of X-z's adversarial design review, which found the
    ruling had been taken without it): against the developer's own
    ``$5,667.63``/mo baseline, any liquid savings between ``$130.80`` and
    ``$283.38`` renders ``0.0 months / 0.1 paychecks / 0.0 years`` on ONE line,
    where the old rule rendered three zeros.  Both figures are individually
    right -- ``0.1`` paychecks IS the better answer for ``$200`` of savings --
    and the line still reads as self-contradictory.  It stands because the
    alternative is a figure 40% wrong about the money, and because near-zero
    savings is where a reader is least likely to be converting between units.

    Args:
        savings_balance: The user's total liquid savings.
        average_monthly_expenses: The average monthly expense total.
        pay_cadence: How often the owner is paid
            (:class:`~app.services.pay_calendar.PayCadence`), which is what
            :attr:`SavingsCoverage.paychecks_covered` is measured in.  Read as
            the OWNER's cadence since plan step R7a-2a: the span was converted
            at a hardcoded ``26 / 12``, so a weekly-paid owner was told their
            savings covered half as many paychecks as they do.

    Returns:
        The :class:`SavingsCoverage` (a three-key dict until plan step
        X-aa, ruling R-CO).  Three zeros when there are no expenses to
        cover -- a real answer, not an absent one.
    """
    if average_monthly_expenses <= 0:
        return SavingsCoverage(
            months_covered=Decimal("0"),
            paychecks_covered=Decimal("0"),
            years_covered=Decimal("0"),
        )

    # The one unrounded answer all three units are expressed from.
    months = savings_balance / average_monthly_expenses

    return SavingsCoverage(
        months_covered=_to_coverage_grain(months),
        paychecks_covered=_to_coverage_grain(
            pay_cadence.months_to_paychecks(months),
        ),
        years_covered=_to_coverage_grain(months / MONTHS_PER_YEAR),
    )


def count_periods_until(target_date, periods, as_of):
    """Count the paydays between *as_of* and the target date.

    **The day is an ARGUMENT, not a clock read** (pay-calendar plan step
    C2-f2d-3, ledger row **P55**).  It read ``date.today()``, which put this
    count on a different day from the balance and the required-contribution
    figure rendered beside it on the same goal card whenever a render crossed
    midnight -- and from the goal's own committed-contribution filter, which
    asks a different producer the same question.  Its one caller holds a read
    pass whose ``as_of`` is that render's single day.

    Args:
        target_date: date -- the goal's target date, or ``None``.
        periods: The owner's saved schedule -- a
            :class:`~app.services.pay_calendar.PeriodWindow` -- whose paydays
            are counted.
        as_of: The read pass's day.  A payday ON this day counts.

    Returns:
        int -- count of paydays from *as_of* to the target date (inclusive),
        or ``None`` when *target_date* is ``None``.
    """
    if target_date is None:
        return None

    count = 0
    for period in periods:
        if as_of <= period.start_date <= target_date:
            count += 1
    return count


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
