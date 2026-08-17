"""
Shekel Budget App -- Savings Dashboard: savings-goal progress.

Computes per-goal progress, the committed monthly contribution from
recurring transfer templates, and the projected completion trajectory.
No Flask imports.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import GoalModeEnum
from app.extensions import db
from app.models.savings_goal import SavingsGoal
from app.models.transfer_template import TransferTemplate
from app.services import obligations_aggregator, savings_goal_service
from app.services.pay_calendar import PayCalendar, PeriodWindow
from app.services.savings_goal_service import GoalTargetSpec, GoalTrajectory
from app.utils.money import percent_complete


@dataclass(frozen=True)
class _GoalInputs:
    """The read-pass facts every goal datum in one build shares.

    A parameter object introduced at plan step R7a-2a, and forced by that
    step's third field: :func:`_compute_goal_progress` and
    :func:`_build_goal_datum` each took five positional arguments, and the
    owner's pay cadence -- which the contribution floor and the income-relative
    target both need -- would have been a sixth on both.  A raised ``max-args``
    is not the project's answer; grouping is, and these three ARE one thing:
    the per-owner facts resolved once for the whole build that no individual
    goal changes.

    Scalars and loaded rows rather than the whole
    :class:`~.._types._DashboardCoreData`, so a helper here stays constructible
    in a test without a ``BalanceContext`` and an account list it never reads.

    Attributes:
        all_periods: The owner's saved schedule as a
            :class:`~app.services.pay_calendar.PeriodWindow`, for the
            periods-until-target count.  Off the read pass since pay-calendar
            plan step C2-f2d-3, so it is the same window the account balances
            beside it were reported over.
        net_biweekly_pay: Current projected net pay for one paycheck, from the
            canonical paycheck engine.  ``Decimal("0.00")`` when the owner has
            no salary configured, which is what
            :attr:`GoalProgress.has_salary_data` reports.
        calendar: The owner's whole pay-period schedule.  ``calendar.cadence``
            turns ``net_biweekly_pay`` into a monthly figure for a "months of
            salary" goal; the WHOLE schedule is what
            ``obligations_aggregator`` needs to tell whether a contribution
            template bounded "after N occurrences" has spent its count (plan
            step R7b-3).  It was the bare
            :class:`~app.services.pay_calendar.PayCadence` until then, which
            could answer the first and not the second.
        as_of: The read pass's day -- ``balance_ctx.as_of``.  The build's ONE
            clock (pay-calendar plan step C2-f2d-3, ledger row **P55**): the
            committed-contribution filter and the periods-until-target count
            both resolve against a day, and reading it twice let one goal card
            answer from two.
    """

    all_periods: PeriodWindow
    net_biweekly_pay: Decimal
    calendar: PayCalendar
    as_of: date


@dataclass(frozen=True)
class GoalProgress:  # pylint: disable=too-many-instance-attributes
    """One savings goal's progress: where it stands and when it lands.

    The per-goal record the ``/savings`` goal cards and the budget dashboard's
    savings track both reduce over -- two templates, through two packages.  A
    frozen value object since plan step X-w4 (ruling R-CI); it was an ELEVEN-key
    untyped dict, the largest record container on this read path, and its
    contract was stated only in two ``Returns:`` blocks that had to be kept in
    step with each other by hand.

    **One of those eleven keys was read by NOTHING, and it did not become a
    field** -- eleven keys in, ten fields out.  (This said "a TWELFTH key" until
    plan step X-w6's adversarial review recounted the dict it describes, which
    is the "a count in a docstring is a claim" class inside the paragraph that
    deletes a field for being one.)
    ``goal_mode_id`` was a straight copy of ``goal.goal_mode_id`` -- one fact
    under two keys, on a record that already carries the goal -- and an AST
    census over ``app/`` and ``tests/`` found ZERO readers of the copy: every
    ``goal_mode_id`` site in the tree is either the ref-cache accessor
    (``ref_cache.goal_mode_id``), the ORM column, the goal FORM's own field, or
    the create/update schema's payload.  That is finding N-100's defect
    (a published key with no consumer) in a container being typed, so it went
    with the dict rather than being carried into the record.

    Pylint: ``too-many-instance-attributes`` (10/7) -- suppressed because this
    is a cohesive per-goal display record read flat by two templates, not an
    object accumulating state.  Every field has a named reader (each was
    counted before the record was written), and there is no cohesive sub-group
    to nest: the goal's own columns are read THROUGH ``goal`` rather than
    copied, and the trajectory is already one nested value.  Grouping any of
    the rest would invent a concept to satisfy a count.  Mirrors
    :class:`~.._types.AccountProjection`, whose 8/7 carries the same rationale.

    Attributes:
        goal: The :class:`~app.models.savings_goal.SavingsGoal` row.  Every
            field the cards read off the goal itself -- its name, account,
            target date, manual per-period contribution, and its mode -- is
            read HERE rather than copied onto this record.
        current_balance: The backing account's balance today, taken from that
            account's :class:`~.._types.AccountProjection` so the goal card and
            the account tile cannot report different balances.
        progress_pct: Completion percent through the canonical
            :func:`app.utils.money.percent_complete` contract (ROUND_HALF_UP,
            clamped ``[0, 100]``), so this card, the budget-dashboard card and
            the companion entry view report one number for one goal.
        remaining_periods: Pay periods from today to the target date, or
            ``None`` when the goal has no target date.
        required_contribution: The per-period contribution needed to land on
            the target date, or ``None`` when there is no actionable target or
            the date has passed (the card renders "Past target date" for the
            second).
        resolved_target: The dollar target -- ``target_amount`` for a FIXED
            goal, the income-relative computation for the other mode.
        income_descriptor: The income-relative caption ("3 months of salary"),
            or ``None`` for a fixed goal.  Presentation microcopy composed in
            the service because templates display and never compute.
        has_salary_data: Whether the engine produced a positive net biweekly
            pay.  STORED rather than derived: the pay figure itself is not
            carried here, and an income-relative goal with no salary profile is
            exactly the state the card warns about.
        trajectory: The goal's
            :class:`~app.services.savings_goal_service.GoalTrajectory` -- when
            it lands at the current rate, and how that reads against its target
            date.  **NEVER absent**: its producer has three returns and every
            one fills all four fields.  Plan step X-w4 typed this as
            ``dict | None`` and the goal card guarded it with a truthiness
            test; both were unreachable, and plan step X-aa deleted them with
            the dict (ruling R-CO).  A nullable that cannot be null is ruling
            R-CA's defect; a guard that cannot be false is not a guard.
        monthly_contribution: The committed monthly inflow discovered from the
            recurring transfer templates targeting the goal's account, through
            the one canonical obligations aggregator.
    """

    goal: SavingsGoal
    current_balance: Decimal
    progress_pct: Decimal
    remaining_periods: int | None
    required_contribution: Decimal | None
    resolved_target: Decimal
    income_descriptor: str | None
    has_salary_data: bool
    trajectory: GoalTrajectory
    monthly_contribution: Decimal


def _load_active_goals(user_id):
    """Load the user's active savings goals.

    The single active-goal loader shared by both savings-dashboard entry
    points: the narrow ``compute_goal_progress`` (which also needs the
    goals up front to restrict the projected accounts to those backing a
    goal) and the full ``compute_dashboard_data``.  Centralizing the
    query here means :func:`_compute_goal_progress` no longer re-runs the
    identical ``is_active`` query its caller already issued.

    Args:
        user_id: Integer ID of the current user.

    Returns:
        List of active :class:`SavingsGoal` instances.
    """
    return (
        db.session.query(SavingsGoal)
        .filter_by(user_id=user_id, is_active=True)
        .all()
    )


def _load_goal_templates(user_id, goals):
    """Batch-load active recurring transfer templates targeting goal accounts.

    Avoids an N+1 query in the per-goal loop.  The aggregator that
    consumes the result (``obligations_aggregator.committed_monthly``)
    handles per-pattern normalization to monthly equivalents and the
    shared skip-non-repeating / skip-expired filter.

    Args:
        user_id: Integer ID of the current user.
        goals: List of active SavingsGoal instances.

    Returns:
        Dict mapping account_id to a list of TransferTemplate targeting
        that account.
    """
    goal_account_ids = [goal.account_id for goal in goals]
    if goal_account_ids:
        to_account_templates = (
            db.session.query(TransferTemplate)
            .filter(
                TransferTemplate.user_id == user_id,
                TransferTemplate.to_account_id.in_(goal_account_ids),
                TransferTemplate.is_active.is_(True),
            )
            .all()
        )
    else:
        to_account_templates = []

    templates_by_account = {}
    for tmpl in to_account_templates:
        templates_by_account.setdefault(tmpl.to_account_id, []).append(tmpl)
    return templates_by_account


def _goal_account_balance(account_data, account_id):
    """Return the current balance of the account backing a savings goal.

    Args:
        account_data: The per-account
            :class:`~.._types.AccountProjection` values from
            ``_compute_account_projections``.
        account_id: The goal's backing account id.

    Returns:
        The account's current balance as a Decimal, or ``Decimal("0.00")``
        when no matching account is present (e.g. the goal's account is
        archived and excluded from projections).
    """
    for ad in account_data:
        if ad.account.id == account_id:
            return ad.current_balance
    return Decimal("0.00")


def _build_goal_datum(
    goal, acct_balance, monthly_contribution, inputs: _GoalInputs,
) -> GoalProgress:
    """Build the per-goal progress record for one savings goal.

    For income-relative goals the resolved target is calculated from the
    user's net pay, their pay cadence and the goal's multiplier/unit; for fixed
    goals the stored target_amount is used directly.  Computes progress
    percent, required contribution, a human-readable income descriptor,
    and the projected trajectory.

    Args:
        goal: The SavingsGoal instance.
        acct_balance: Current balance of the goal's backing account.
        monthly_contribution: Committed monthly contribution into the
            account, from the canonical obligations aggregator.
        inputs: The build's shared per-owner facts (:class:`_GoalInputs`) --
            the pay periods, the net pay for one paycheck, and the pay
            cadence.

    Returns:
        The goal's :class:`GoalProgress` (an eleven-key dict until plan step
        X-w4, ruling R-CI).
    """
    fixed_id = ref_cache.goal_mode_id(GoalModeEnum.FIXED)
    has_salary = inputs.net_biweekly_pay > Decimal("0.00")

    resolved_target = savings_goal_service.resolve_goal_target(
        GoalTargetSpec(
            goal_mode_id=goal.goal_mode_id,
            target_amount=goal.target_amount,
            income_unit_id=goal.income_unit_id,
            income_multiplier=goal.income_multiplier,
        ),
        inputs.net_biweekly_pay,
        inputs.calendar.cadence,
    )

    remaining_periods = savings_goal_service.count_periods_until(
        goal.target_date, inputs.all_periods, inputs.as_of,
    )
    required = savings_goal_service.calculate_required_contribution(
        acct_balance, resolved_target, remaining_periods,
    ) if resolved_target and resolved_target > 0 else None

    # Progress percent via the canonical money.percent_complete contract
    # (ROUND_HALF_UP, clamped [0, 100], Decimal) so this savings card, the
    # budget-dashboard savings-goal card (dashboard_service), and the companion
    # entry view (entry_service) all report the same number for the same goal,
    # and a negative projected balance renders 0%, not a negative-width bar
    # (deep-quality-hunt #20).
    progress_pct = Decimal("0")
    if resolved_target and resolved_target > Decimal("0.00"):
        progress_pct = percent_complete(acct_balance, resolved_target)

    # Build human-readable descriptor for income-relative goals.
    if goal.goal_mode_id != fixed_id:
        unit_name = (
            goal.income_unit.name.lower()
            if goal.income_unit else "units"
        )
        income_descriptor = f"{goal.income_multiplier} {unit_name} of salary"
    else:
        income_descriptor = None

    # Trajectory: projected completion date and pace indicator.
    trajectory = savings_goal_service.calculate_trajectory(
        current_balance=acct_balance,
        target_amount=resolved_target,
        monthly_contribution=monthly_contribution,
        target_date=goal.target_date,
    )

    # ``goal_mode_id`` is NOT carried (plan step X-w4).  It was a copy of
    # ``goal.goal_mode_id`` on a record that carries the goal, and an AST
    # census found the copy had zero readers anywhere -- finding N-100's
    # published-key-with-no-consumer, inside the container this step typed.
    return GoalProgress(
        goal=goal,
        current_balance=acct_balance,
        progress_pct=progress_pct,
        remaining_periods=remaining_periods,
        required_contribution=required,
        resolved_target=resolved_target,
        income_descriptor=income_descriptor,
        has_salary_data=has_salary,
        trajectory=trajectory,
        monthly_contribution=monthly_contribution,
    )


def _compute_goal_progress(
    user_id, account_data, inputs: _GoalInputs, goals,
) -> list[GoalProgress]:
    """Compute savings goal progress, contributions, and trajectory.

    For income-relative goals, the resolved target is calculated from
    the user's net pay, their pay cadence and the goal's multiplier/unit.  For
    fixed goals, the stored target_amount is used directly.

    Trajectory is computed for each goal by discovering the monthly
    contribution from recurring transfer templates targeting the goal's
    account, then projecting the completion date and pace.

    Args:
        user_id: Integer ID of the current user.
        account_data: The per-account projections from
            _compute_account_projections.
        inputs: The build's shared per-owner facts (:class:`_GoalInputs`) --
            the pay periods, the net pay for one paycheck, and the pay
            cadence every monthly equivalent here is measured against.
        goals: The user's active :class:`SavingsGoal` instances, already
            loaded by the caller via :func:`_load_active_goals`.  Passed
            in rather than re-queried so the active-goal lookup runs once
            per request, not twice (both entry points already load it).

    Returns:
        One :class:`GoalProgress` per active goal, in *goals* order.
    """
    templates_by_account = _load_goal_templates(user_id, goals)

    goal_data = []
    for goal in goals:
        acct_balance = _goal_account_balance(account_data, goal.account_id)

        # Monthly contribution from recurring transfers into this account.
        # Routed through the one canonical aggregator (E-24 / HIGH-05) so
        # the same skip-non-repeating / skip-expired filter applies that the
        # /obligations page applies; pre-Commit-23 this loop omitted the
        # expired-rule guard and inflated per-goal floors indefinitely.
        acct_templates = templates_by_account.get(goal.account_id, [])
        # The PASS's day, not a bare clock read (pay-calendar plan step
        # C2-f2d-3, ledger row **P55**).  ``committed_monthly`` decides whether
        # a bounded template still commits anything AS OF the day it is given,
        # so reading ``date.today()`` here put this figure on a different day
        # from the balances beside it on the same card across a midnight
        # render -- and from the emergency-fund floor, which asks the same
        # producer the same question.
        monthly_contribution = obligations_aggregator.committed_monthly(
            acct_templates, inputs.as_of, inputs.calendar,
        )

        goal_data.append(_build_goal_datum(
            goal, acct_balance, monthly_contribution, inputs,
        ))

    return goal_data
