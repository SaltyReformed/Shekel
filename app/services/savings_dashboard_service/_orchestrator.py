"""
Shekel Budget App -- Savings Dashboard: orchestrator.

Two public entry points.  ``compute_dashboard_data`` loads the core
data, runs the per-account projections, computes goal progress, the
emergency-fund metrics, and the debt summary / DTI, and assembles the
render-template context dict.  ``compute_debt_summary`` is the narrow
producer behind the budget dashboard's debt card (deep-hunt #82): the
same loaders, projection dispatch, and debt/DTI rule, restricted to
the loan accounts the debt summary reads.  No Flask imports.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from app.services import savings_goal_service
from app.services.savings_dashboard_service._data import (
    _load_account_params,
    _load_archived_accounts,
    _load_dashboard_core_data,
)
from app.services.savings_dashboard_service._display import (
    _group_accounts_by_category,
)
from app.services.savings_dashboard_service._goals import (
    _compute_goal_progress,
    _load_active_goals,
)
from app.services.savings_dashboard_service._metrics import (
    _apply_dti_metrics,
    _compute_avg_monthly_expenses,
    _compute_debt_summary,
    _get_current_paycheck_breakdown,
    _sum_liquid_balances,
)
from app.services.savings_dashboard_service._projections import (
    _compute_account_projections,
)
from app.services.savings_dashboard_service._types import _ProjectionContext

if TYPE_CHECKING:
    from app.services.paycheck_calculator import PaycheckBreakdown
    from app.services.savings_dashboard_service._types import (
        _AccountParams,
        _DashboardCoreData,
    )


def _build_projection_context(
    core: _DashboardCoreData, params: _AccountParams,
) -> _ProjectionContext:
    """Assemble the request-scoped projection context from loaded data.

    One definition of the core-data -> context mapping (notably the
    baseline-scenario ``None`` fallback) shared by both public entry
    points so the full dashboard build and the narrow debt producer
    cannot project against different inputs.

    Args:
        core: The :class:`_DashboardCoreData` from
            :func:`_load_dashboard_core_data`.
        params: The :class:`_AccountParams` from
            :func:`_load_account_params`.

    Returns:
        The :class:`_ProjectionContext` the projection dispatch reads.
    """
    # scenario_id is request-scoped (off the baseline scenario), not an
    # account-type parameter, so it rides on the context, not in params.
    return _ProjectionContext(
        all_transactions=core.all_transactions,
        all_shadow_income=core.all_shadow_income,
        all_periods=core.all_periods,
        current_period=core.current_period,
        params=params,
        scenario_id=core.scenario.id if core.scenario else None,
    )


def _debt_summary_with_dti(
    account_data: list[dict],
    escrow_map: dict[int, list],
    current_breakdown: PaycheckBreakdown | None,
) -> dict | None:
    """Compute the debt summary and apply the DTI metrics to it.

    The single home for the debt-card rule, shared by the full
    dashboard build and the narrow :func:`compute_debt_summary`
    producer so the /savings page and the budget dashboard's debt card
    cannot drift onto different figures.

    Args:
        account_data: Per-account dicts from
            ``_compute_account_projections`` (any mix -- the debt
            summary reads only the entries carrying ``loan_params``).
        escrow_map: account_id -> list of EscrowComponent (PITI).
        current_breakdown: The engine ``PaycheckBreakdown`` for the
            current period, or ``None`` with no salary configured.

    Returns:
        The debt-summary dict with the DTI keys applied, or ``None``
        when no loan accounts with params exist.
    """
    debt_summary = _compute_debt_summary(account_data, escrow_map)
    if debt_summary is not None:
        # MED-06 / F-032: ``gross_biweekly`` is the raise-aware engine
        # output for the current period (``calculate_paycheck`` ->
        # ``PaycheckBreakdown.earnings.gross_biweekly``), NOT the off-engine
        # ``annual_salary / pay_periods`` recompute the DTI block read
        # pre-Commit-26.  ``_apply_dti_metrics`` performs the
        # biweekly -> monthly normalization on this engine-derived input.
        gross_biweekly = (
            current_breakdown.earnings.gross_biweekly if current_breakdown is not None
            else Decimal("0.00")
        )
        _apply_dti_metrics(debt_summary, gross_biweekly)
    return debt_summary


def compute_debt_summary(user_id: int) -> dict | None:
    """Compute only the debt summary + DTI for the budget dashboard card.

    The narrow producer behind ``dashboard_service._get_debt_summary``
    (deep-hunt #82's efficiency/SRP half).  Identical figures to
    ``compute_dashboard_data(user_id)["debt_summary"]`` by construction:
    it runs the same loaders and the same per-account projection
    dispatch -- restricted to the accounts the debt summary reads (those
    with a ``LoanParams`` row; per-account projections are independent,
    so the restriction cannot change any projected figure) -- and routes
    through the shared :func:`_debt_summary_with_dti`.  What it skips is
    the dashboard-only work: every non-loan account's projection, goal
    progress, the emergency-fund metrics, account grouping, and the
    archived-account list.

    Args:
        user_id: Integer ID of the current user.

    Returns:
        The debt-summary dict with the DTI keys applied, or ``None``
        when the user has no loan accounts with params (the early
        return mirrors ``_compute_debt_summary``'s no-loan ``None``
        inside the full build, and additionally skips the per-account
        projections and the breakdown's paycheck-engine call;
        ``_load_account_params``'s gross-biweekly engine call has
        already run by then -- the deliberate price of sharing the
        loaders verbatim).
    """
    core = _load_dashboard_core_data(user_id)
    params = _load_account_params(user_id, core.accounts)
    loan_accounts = [
        acct for acct in core.accounts if acct.id in params.loan_params_map
    ]
    if not loan_accounts:
        return None

    ctx = _build_projection_context(core, params)
    account_data = _compute_account_projections(loan_accounts, ctx)

    current_breakdown = _get_current_paycheck_breakdown(
        user_id, core.all_periods, core.current_period,
    )
    return _debt_summary_with_dti(
        account_data, params.escrow_map, current_breakdown,
    )


def compute_goal_progress(user_id: int) -> list[dict]:
    """Compute only the savings-goal progress for the budget dashboard card.

    The narrow producer behind ``dashboard_service._get_savings_goals``,
    mirroring :func:`compute_debt_summary`'s pattern.  Identical figures
    to ``compute_dashboard_data(user_id)["goal_data"]`` by construction:
    it runs the same loaders, the same per-account projection dispatch
    (restricted to the accounts that back an active goal -- per-account
    projections are independent, so the restriction cannot change any
    projected figure), and the same canonical net-biweekly-pay producer,
    then routes through the shared :func:`_compute_goal_progress`.  What
    it skips is the dashboard-only work: every non-goal account's
    projection, the emergency-fund metrics, the debt summary, account
    grouping, and the archived-account list.

    Closes the budget dashboard's two goal defects (dashboard_card_audit
    Card 5): income-relative goals (``target_amount`` NULL by design) now
    resolve their target via ``resolve_goal_target`` instead of rendering
    ``$0.00 / 0%``, and the balance basis is the entries-aware resolver
    balance (``account_data[...]["current_balance"]``) rather than the
    raw stored ``current_anchor_balance``.  So this card and the /savings
    page report the same numbers for the same goal.

    Args:
        user_id: Integer ID of the current user.

    Returns:
        A list of per-goal progress dicts (see
        :func:`_compute_goal_progress`), one per active goal; empty when
        the user has no active goals.
    """
    core = _load_dashboard_core_data(user_id)

    active_goals = _load_active_goals(user_id)
    if not active_goals:
        return []

    params = _load_account_params(user_id, core.accounts)
    goal_account_ids = {goal.account_id for goal in active_goals}
    goal_accounts = [
        acct for acct in core.accounts if acct.id in goal_account_ids
    ]

    ctx = _build_projection_context(core, params)
    account_data = _compute_account_projections(goal_accounts, ctx)

    current_breakdown = _get_current_paycheck_breakdown(
        user_id, core.all_periods, core.current_period,
    )
    net_biweekly_pay = (
        current_breakdown.earnings.net_pay if current_breakdown is not None
        else Decimal("0.00")
    )

    return _compute_goal_progress(
        user_id, account_data, core.all_periods, net_biweekly_pay,
        active_goals,
    )


def compute_dashboard_data(user_id):
    """Compute all data needed by the savings dashboard template.

    Loads accounts, projects balances per account type, computes
    savings goal progress and emergency fund metrics, and groups
    accounts by category for display.

    Args:
        user_id: Integer ID of the current user.

    Returns:
        dict with keys matching the render_template context:
            account_data, grouped_accounts, goal_data,
            emergency_metrics, total_savings,
            avg_monthly_expenses, savings_accounts.
    """
    core = _load_dashboard_core_data(user_id)

    # ── Load account-type-specific parameters ───────────────────
    params = _load_account_params(user_id, core.accounts)

    # ── Compute per-account projections ─────────────────────────
    ctx = _build_projection_context(core, params)
    account_data = _compute_account_projections(core.accounts, ctx)

    # ── Canonical paycheck breakdown (MED-06 / F-032) ──────────
    # One income producer feeds every income-derived figure on the
    # page: the income-relative-goal trajectory's net biweekly pay AND
    # the DTI denominator's gross monthly income.  Pre-Commit-26 the
    # DTI path took ``params.salary_gross_biweekly`` (the off-engine
    # raw ``annual_salary / pay_periods`` recompute) and silently dropped
    # any applicable ``SalaryRaise`` rows, so a user with a 3% recurring
    # raise saw a DTI computed against a denominator ~$260/mo too low
    # (audit worked example: $8,666.67 vs $8,926.67, 27.7% vs 26.9%).
    # Routing both consumers through ``calculate_paycheck`` for the
    # current period makes the engine the single source of truth.
    current_breakdown = _get_current_paycheck_breakdown(
        user_id, core.all_periods, core.current_period,
    )
    net_biweekly_pay = (
        current_breakdown.earnings.net_pay if current_breakdown is not None
        else Decimal("0.00")
    )

    # ── Savings goals ───────────────────────────────────────────
    goal_data = _compute_goal_progress(
        user_id, account_data, core.all_periods, net_biweekly_pay,
        _load_active_goals(user_id),
    )

    # ── Emergency fund metrics ──────────────────────────────────
    avg_monthly_expenses = _compute_avg_monthly_expenses(
        user_id, core.accounts, core.all_periods, core.current_period,
        core.scenario,
    )
    total_savings = _sum_liquid_balances(account_data)
    emergency_metrics = savings_goal_service.calculate_savings_metrics(
        total_savings, avg_monthly_expenses,
    )

    # ── Template helpers ────────────────────────────────────────
    # Liquid accounts appear in the savings goal form dropdown.
    savings_accounts = [
        ad["account"] for ad in account_data
        if ad["account"].account_type and ad["account"].account_type.is_liquid
    ]

    # ── Debt summary and DTI ───────────────────────────────────
    debt_summary = _debt_summary_with_dti(
        account_data, params.escrow_map, current_breakdown,
    )

    return {
        "account_data": account_data,
        "grouped_accounts": _group_accounts_by_category(account_data),
        "goal_data": goal_data,
        "emergency_metrics": emergency_metrics,
        "total_savings": total_savings,
        "avg_monthly_expenses": avg_monthly_expenses,
        "savings_accounts": savings_accounts,
        "archived_accounts": _load_archived_accounts(user_id),
        "debt_summary": debt_summary,
    }
