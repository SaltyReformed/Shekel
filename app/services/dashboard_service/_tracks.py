"""
Shekel Budget App -- Dashboard: the position tracks tier.

:func:`compute_tracks_section` is the page-load-only position tier of the
Terminal Road rebuild (Loop B B-1; deliberately NOT on the
``balanceChanged`` refresh path, per the Gate B6 rationale): the
savings-goal metro tracks and the debt track.  Both come from the
``/savings`` producers verbatim, so the two screens cannot disagree.

Pure aggregation -- no Flask imports, no database writes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.balance_at import BalanceContext

if TYPE_CHECKING:
    # Type-only, and that is load-bearing here: the runtime import of
    # ``savings_dashboard_service`` inside ``compute_tracks_section`` is
    # DEFERRED on purpose (it pulls the heaviest service chain, +27 modules
    # measured), so annotating at module scope would undo a measured decision.
    # ``GoalProgress`` is the package's PUBLIC re-export as of plan step X-w6
    # (ruling R-CN), not a private module name, so this is the facade the
    # W9910 package-privacy checker asks for -- the type hint the coding
    # standard asks for, at no import cost.
    from app.services.savings_dashboard_service import GoalProgress


# ── Tracks producer (savings goals + debt position) ────────────────


def compute_tracks_section(user_id: int) -> dict:
    """Compute the position tier: savings-goal tracks and the debt track.

    The page-load-only position tier of the Terminal Road rebuild
    (Loop B B-1; deliberately not on the ``balanceChanged`` refresh path,
    per the Gate B6 rationale).  Reuses the /savings producers so both
    screens agree on the same figures:

      * ``goals`` -- one dict per active goal, reshaped from
        ``savings_dashboard_service.compute_goal_progress`` into the metro
        track contract (see :func:`_track_goal_datum`).
      * ``debt`` -- the
        ``savings_dashboard_service.compute_debt_summary`` value, passed
        through WHOLE: the same ``DebtSummary`` ``/savings`` renders, carrying
        both the money figures and ``principal_paid_fraction`` (the honest
        all-loans-ever rail position, or ``None`` when no loan has originated).
        ``None`` when the user has no loan accounts.

    **This tier carried a ``DebtTrack`` wrapper until plan step X-u** (ruling
    R-BS, finding N-109), because the fraction came from a SECOND narrow
    producer that re-ran the whole debt pipeline to get it -- measured at two
    debt projections and three seam-batch builds per render.  With the fraction
    a field of the summary, the wrapper's only job was to pair two values one
    object already carries, so it is gone and this tier adds nothing to what the
    producer answered.  The route still maps the fraction to a rail percent;
    that is presentation and belongs there.

    No exception is caught here: the producers this delegates to are the
    same code the /savings route runs without a guard, so a
    ``ValueError`` / ``KeyError`` / ``AttributeError`` from that
    computation is a programming bug that must fail loud, not be masked as
    an empty tracks tier (CLAUDE.md rule 4); letting it propagate fails
    loud and identically on the dashboard and /savings pages.

    Args:
        user_id: Integer ID of the current user.

    Returns:
        A dict with keys ``goals`` (a list, possibly empty) and ``debt``
        (a ``savings_dashboard_service.DebtSummary`` or ``None``).
    """
    # Pylint: ``import-outside-toplevel`` -- Deferred: savings_dashboard_service
    # pulls the heaviest service import chain (+27 modules, measured); loaded only
    # when this path runs, not on every ``dashboard_service`` import.
    from app.services import savings_dashboard_service  # pylint: disable=import-outside-toplevel

    # ONE read pass for both producers: each loan is resolved once for the
    # whole section rather than once per producer (they used to start
    # independent passes, so a two-loan user paid for four resolutions here).
    # Both producers REQUIRE it since pay-calendar plan step C2-f2d-3 (ledger
    # row **P58**), so sharing is structural rather than a courtesy this
    # section extends -- there is no owner id left for either of them to open a
    # second pass from.  **This module still opens its own pass**, which is
    # ledger row **P56**'s door for the budget dashboard and closes at
    # ``C2-f2e``.
    # The pass is shared; the LOADS behind it are not -- each producer still
    # runs its own ``_load_dashboard_core_data``, which is the input-tier memo
    # plan step X-i1 owns (finding N-72), not something this section can fix
    # without a second sharing channel beside the context.
    balance_ctx = BalanceContext.build(user_id)

    goal_data = savings_dashboard_service.compute_goal_progress(balance_ctx)
    goals = [_track_goal_datum(gd) for gd in goal_data]

    return {
        "goals": goals,
        "debt": savings_dashboard_service.compute_debt_summary(balance_ctx),
    }


def _track_goal_datum(goal_datum: GoalProgress) -> dict:
    """Reshape one ``compute_goal_progress`` entry into the metro-track contract.

    Pulls only the fields the savings track renders -- the goal's name and
    account name, the progress percent and balance/target, and the
    ``calculate_trajectory`` outputs (pace, projected completion date,
    required monthly) -- so the template reads a flat dict rather than
    reaching into the nested ``goal`` ORM object and its
    :class:`~app.services.savings_goal_service.GoalTrajectory`.

    Args:
        goal_datum: One
            :class:`~app.services.savings_dashboard_service._goals.GoalProgress`
            from ``savings_dashboard_service.compute_goal_progress`` (an untyped
            eleven-key dict until plan step X-w4, ruling R-CI).  Read through
            ATTRIBUTES now, so a field this producer renames fails here rather
            than resolving to a ``KeyError`` that reads like missing data.

    Returns:
        A dict with keys ``name``, ``account_name``, ``account_id``,
        ``progress_pct``, ``current_balance``, ``target_amount``,
        ``target_date``, ``pace``, ``projected_completion_date``,
        ``required_monthly``, ``monthly_contribution``.
    """
    goal = goal_datum.goal
    trajectory = goal_datum.trajectory
    return {
        "name": goal.name,
        "account_name": goal.account.name,
        "account_id": goal.account_id,
        "progress_pct": goal_datum.progress_pct,
        "current_balance": goal_datum.current_balance,
        "target_amount": goal_datum.resolved_target,
        "target_date": goal.target_date,
        "pace": trajectory.pace,
        "projected_completion_date": trajectory.projected_completion_date,
        "required_monthly": trajectory.required_monthly,
        "monthly_contribution": goal_datum.monthly_contribution,
    }
