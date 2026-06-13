"""
Shekel Budget App -- Savings Dashboard: savings-goal progress.

Computes per-goal progress, the committed monthly contribution from
recurring transfer templates, and the projected completion trajectory.
No Flask imports.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import GoalModeEnum
from app.extensions import db
from app.models.savings_goal import SavingsGoal
from app.models.transfer_template import TransferTemplate
from app.services import obligations_aggregator, savings_goal_service
from app.utils.money import percent_complete


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
    shared skip-ONCE / skip-expired filter.

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
        account_data: List of per-account dicts from
            ``_compute_account_projections``.
        account_id: The goal's backing account id.

    Returns:
        The account's current balance as a Decimal, or ``Decimal("0.00")``
        when no matching account is present (e.g. the goal's account is
        archived and excluded from projections).
    """
    for ad in account_data:
        if ad["account"].id == account_id:
            return ad["current_balance"] or Decimal("0.00")
    return Decimal("0.00")


def _build_goal_datum(
    goal, acct_balance, monthly_contribution, all_periods, net_biweekly_pay,
):
    """Build the per-goal progress dict for one savings goal.

    For income-relative goals the resolved target is calculated from the
    user's net biweekly pay and the goal's multiplier/unit; for fixed
    goals the stored target_amount is used directly.  Computes progress
    percent, required contribution, a human-readable income descriptor,
    and the projected trajectory.

    Args:
        goal: The SavingsGoal instance.
        acct_balance: Current balance of the goal's backing account.
        monthly_contribution: Committed monthly contribution into the
            account, from the canonical obligations aggregator.
        all_periods: All pay periods for the user.
        net_biweekly_pay: Current net biweekly pay (Decimal), used to
            resolve income-relative goal targets.

    Returns:
        Dict with keys: goal, current_balance, progress_pct,
        remaining_periods, required_contribution, resolved_target,
        goal_mode_id, income_descriptor, has_salary_data, trajectory,
        monthly_contribution.
    """
    fixed_id = ref_cache.goal_mode_id(GoalModeEnum.FIXED)
    has_salary = net_biweekly_pay > Decimal("0.00")

    resolved_target = savings_goal_service.resolve_goal_target(
        goal.goal_mode_id,
        goal.target_amount,
        goal.income_unit_id,
        goal.income_multiplier,
        net_biweekly_pay,
    )

    remaining_periods = savings_goal_service.count_periods_until(
        goal.target_date, all_periods
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

    return {
        "goal": goal,
        "current_balance": acct_balance,
        "progress_pct": progress_pct,
        "remaining_periods": remaining_periods,
        "required_contribution": required,
        "resolved_target": resolved_target,
        "goal_mode_id": goal.goal_mode_id,
        "income_descriptor": income_descriptor,
        "has_salary_data": has_salary,
        "trajectory": trajectory,
        "monthly_contribution": monthly_contribution,
    }


def _compute_goal_progress(user_id, account_data, all_periods, net_biweekly_pay, goals):
    """Compute savings goal progress, contributions, and trajectory.

    For income-relative goals, the resolved target is calculated from
    the user's net biweekly pay and the goal's multiplier/unit.  For
    fixed goals, the stored target_amount is used directly.

    Trajectory is computed for each goal by discovering the monthly
    contribution from recurring transfer templates targeting the goal's
    account, then projecting the completion date and pace.

    Args:
        user_id: Integer ID of the current user.
        account_data: List of per-account dicts from _compute_account_projections.
        all_periods: All pay periods for the user.
        net_biweekly_pay: Current net biweekly pay (Decimal).  Used to
            resolve income-relative goal targets.
        goals: The user's active :class:`SavingsGoal` instances, already
            loaded by the caller via :func:`_load_active_goals`.  Passed
            in rather than re-queried so the active-goal lookup runs once
            per request, not twice (both entry points already load it).

    Returns:
        List of dicts with keys: goal, current_balance, progress_pct,
        remaining_periods, required_contribution, resolved_target,
        goal_mode_id, income_descriptor, has_salary_data, trajectory,
        monthly_contribution.
    """
    templates_by_account = _load_goal_templates(user_id, goals)

    goal_data = []
    for goal in goals:
        acct_balance = _goal_account_balance(account_data, goal.account_id)

        # Monthly contribution from recurring transfers into this account.
        # Routed through the one canonical aggregator (E-24 / HIGH-05) so
        # the same skip-ONCE / skip-expired filter applies that the
        # /obligations page applies; pre-Commit-23 this loop omitted the
        # expired-rule guard and inflated per-goal floors indefinitely.
        acct_templates = templates_by_account.get(goal.account_id, [])
        monthly_contribution = obligations_aggregator.committed_monthly(
            acct_templates, date.today(),
        )

        goal_data.append(_build_goal_datum(
            goal, acct_balance, monthly_contribution, all_periods,
            net_biweekly_pay,
        ))

    return goal_data
