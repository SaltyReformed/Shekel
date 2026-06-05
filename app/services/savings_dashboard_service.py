"""
Shekel Budget App -- Savings Dashboard Service

Orchestrates account balance projections, savings goal progress,
and emergency fund metrics for the savings dashboard.  Extracted
from the route handler (L-06) so the route contains only Flask
request handling and template rendering.

All functions accept plain data (user_id, ORM objects) and return
plain dicts/lists.  No Flask imports.
"""

import logging
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import joinedload

from app import ref_cache
from app.enums import AcctCategoryEnum, AcctTypeEnum, GoalModeEnum, TxnTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.interest_params import InterestParams
from app.models.investment_params import InvestmentParams
from app.models.loan_anchor_event import LoanAnchorEvent
from app.models.loan_features import EscrowComponent
from app.models.loan_params import LoanParams
from app.models.pay_period import PayPeriod
from app.models.ref import AccountType
from app.models.salary_profile import SalaryProfile
from app.models.savings_goal import SavingsGoal
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import (
    balance_calculator,
    balance_resolver,
    escrow_calculator,
    growth_engine,
    income_service,
    loan_resolver,
    obligations_aggregator,
    pay_period_service,
    paycheck_calculator,
    savings_goal_service,
)
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
    compute_loan_period_balance_map,
    find_period_containing_date,
)
from app.services.investment_projection import adapt_deductions
from app.services.loan_payment_service import (
    load_loan_context,
)
from app.services.projection_inputs import (
    build_investment_projection_inputs,
    load_active_deductions_for_accounts,
)
from app.services.scenario_resolver import get_baseline_scenario
from app.services.tax_config_service import load_tax_configs
from app.utils.balance_predicates import balance_excluded_status_ids
from app.utils.money import MONTHS_PER_YEAR, PAY_PERIODS_PER_YEAR, round_money
from app.utils.period_projections import project_balance_horizons

logger = logging.getLogger(__name__)

_TWO_PLACES = Decimal("0.01")
_RATE_PLACES = Decimal("0.00001")
_DTI_HEALTHY_THRESHOLD = Decimal("36")
_DTI_HIGH_THRESHOLD = Decimal("43")


@dataclass(frozen=True)
class _DashboardCoreData:
    """Request-scoped data loaded once at the start of the dashboard build.

    Bundles the accounts, baseline scenario, pay periods, and the
    pre-loaded transaction sets so the orchestrator passes one object
    to the projection step instead of six positional parameters.
    """

    accounts: list[Account]
    scenario: Scenario | None
    all_periods: list[PayPeriod]
    current_period: PayPeriod | None
    all_transactions: list[Transaction]
    all_shadow_income: list[Transaction]


@dataclass(frozen=True)
class _ProjectionContext:
    """Loop-invariant inputs shared across the per-account projection loop.

    Every account in ``_compute_account_projections`` projects against
    the same transactions, periods, current period, and loaded parameter
    maps; bundling them keeps the per-account helpers to a small,
    cohesive argument list.
    """

    all_transactions: list[Transaction]
    all_shadow_income: list[Transaction]
    all_periods: list[PayPeriod]
    current_period: PayPeriod | None
    params: dict


@dataclass(frozen=True)
class _LoanAccountResult:
    """Resolver-derived projection outputs for one loan account.

    Carries the figures the per-account dict needs from the loan
    resolver so ``_compute_loan_account`` can return them as one cohesive
    value instead of a positional tuple.
    """

    current_balance: Decimal
    monthly_payment: Decimal
    payoff_date: date | None
    projected: dict
    is_paid_off: bool


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
    params["scenario_id"] = core.scenario.id if core.scenario else None

    # ── Compute per-account projections ─────────────────────────
    ctx = _ProjectionContext(
        all_transactions=core.all_transactions,
        all_shadow_income=core.all_shadow_income,
        all_periods=core.all_periods,
        current_period=core.current_period,
        params=params,
    )
    account_data = _compute_account_projections(core.accounts, ctx)

    # ── Canonical paycheck breakdown (MED-06 / F-032) ──────────
    # One income producer feeds every income-derived figure on the
    # page: the income-relative-goal trajectory's net biweekly pay AND
    # the DTI denominator's gross monthly income.  Pre-Commit-26 the
    # DTI path took ``params["salary_gross_biweekly"]`` (the off-engine
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
        current_breakdown.net_pay if current_breakdown is not None
        else Decimal("0.00")
    )

    # ── Savings goals ───────────────────────────────────────────
    goal_data = _compute_goal_progress(
        user_id, account_data, core.all_periods, net_biweekly_pay,
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
    debt_summary = _compute_debt_summary(
        account_data, params["escrow_map"],
    )
    if debt_summary is not None:
        # MED-06 / F-032: ``gross_biweekly`` is the raise-aware engine
        # output for the current period (``calculate_paycheck`` ->
        # ``PaycheckBreakdown.gross_biweekly``), NOT the off-engine
        # ``annual_salary / pay_periods`` recompute the DTI block read
        # pre-Commit-26.  ``_apply_dti_metrics`` performs the
        # biweekly -> monthly normalization on this engine-derived input.
        gross_biweekly = (
            current_breakdown.gross_biweekly if current_breakdown is not None
            else Decimal("0.00")
        )
        _apply_dti_metrics(debt_summary, gross_biweekly)

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


# ── Private helpers ──────────────────────────────────────────────


def _load_dashboard_core_data(user_id):
    """Load the accounts, scenario, periods, and transactions for the dashboard.

    Args:
        user_id: Integer ID of the current user.

    Returns:
        A :class:`_DashboardCoreData` with active accounts (ordered for
        display), the baseline scenario, all pay periods, the current
        period, and the pre-loaded transaction / shadow-income sets.
        Transaction sets are empty when there is no scenario or no
        periods.
    """
    accounts = (
        db.session.query(Account)
        .filter_by(user_id=user_id, is_active=True)
        .order_by(Account.sort_order, Account.name)
        .all()
    )

    scenario = get_baseline_scenario(user_id)
    all_periods = pay_period_service.get_all_periods(user_id)
    current_period = pay_period_service.get_current_period(user_id)
    period_ids = [p.id for p in all_periods]

    all_transactions = (
        db.session.query(Transaction)
        .filter(
            Transaction.pay_period_id.in_(period_ids),
            Transaction.scenario_id == scenario.id,
            Transaction.is_deleted.is_(False),
        )
        .all()
    ) if scenario and period_ids else []

    # Status filter routes through the centralized
    # ``balance_excluded_status_ids`` accessor (D6-09 / MED-02) so the
    # Credit / Cancelled exclusion is defined exactly once across the
    # codebase.  ``joinedload(Transaction.status)`` is retained so
    # downstream Python iteration in
    # ``investment_projection.calculate_investment_inputs`` can read
    # ``txn.status.excludes_from_balance`` / ``txn.status.is_settled``
    # without an N+1; the explicit INNER JOIN is dropped because the
    # ``Transaction.status_id`` filter no longer needs it.
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    all_shadow_income = (
        db.session.query(Transaction)
        .options(joinedload(Transaction.status))
        .filter(
            Transaction.transfer_id.isnot(None),
            Transaction.transaction_type_id == income_type_id,
            Transaction.pay_period_id.in_(period_ids),
            Transaction.scenario_id == scenario.id,
            Transaction.is_deleted.is_(False),
            ~Transaction.status_id.in_(balance_excluded_status_ids()),
        )
        .all()
    ) if scenario and period_ids else []

    return _DashboardCoreData(
        accounts=accounts,
        scenario=scenario,
        all_periods=all_periods,
        current_period=current_period,
        all_transactions=all_transactions,
        all_shadow_income=all_shadow_income,
    )


def _sum_liquid_balances(account_data):
    """Sum the current balances of liquid accounts for the emergency fund.

    Args:
        account_data: List of per-account dicts from
            ``_compute_account_projections``.

    Returns:
        The total liquid balance as a Decimal.
    """
    total_savings = Decimal("0.00")
    for ad in account_data:
        if ad["account"].account_type and ad["account"].account_type.is_liquid:
            total_savings += ad["current_balance"] or Decimal("0.00")
    return total_savings


def _apply_dti_metrics(debt_summary, gross_biweekly):
    """Populate the debt summary's DTI fields from gross biweekly pay.

    Mutates ``debt_summary`` in place, adding ``dti_ratio``,
    ``dti_label``, and ``gross_monthly_income``.  The biweekly -> monthly
    conversion factor (``PAY_PERIODS_PER_YEAR / MONTHS_PER_YEAR``) is a
    structural property of the 26-period pay schedule (Shekel is a
    biweekly app), applied to the engine-derived gross (MED-06 / F-032);
    it is a "genuine flat conversion" in the sense Commit 26 calls out,
    not a raise-dropping shortcut.  When ``gross_biweekly`` is zero (no
    income data), all three fields are set to ``None`` so the template
    distinguishes "no income source" from a real zero (E-12).

    Args:
        debt_summary: The debt-summary dict to populate, in place.
        gross_biweekly: Engine-derived gross biweekly pay (Decimal).
    """
    if gross_biweekly > Decimal("0.00"):
        gross_monthly = round_money(
            gross_biweekly * PAY_PERIODS_PER_YEAR / MONTHS_PER_YEAR
        )
        dti_ratio = (
            debt_summary["total_monthly_payments"]
            / gross_monthly * Decimal("100")
        ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        debt_summary["dti_ratio"] = dti_ratio
        debt_summary["dti_label"] = _get_dti_label(dti_ratio)
        debt_summary["gross_monthly_income"] = gross_monthly
    else:
        debt_summary["dti_ratio"] = None
        debt_summary["dti_label"] = None
        debt_summary["gross_monthly_income"] = None


def _load_loan_params_and_escrow(accounts):
    """Batch-load LoanParams and EscrowComponent maps for loan accounts.

    Amortizing loan types are metadata-driven via ``has_amortization``.

    Args:
        accounts: List of Account model instances.

    Returns:
        ``(loan_params_map, escrow_map)`` -- the first maps account_id
        to its :class:`LoanParams`; the second maps account_id to a
        list of :class:`EscrowComponent` (for the debt-summary PITI
        total).  Both are empty when no loan accounts exist.
    """
    amort_type_ids = {
        at.id for at in db.session.query(AccountType).filter_by(has_amortization=True).all()
    }
    loan_account_ids = [a.id for a in accounts if a.account_type_id in amort_type_ids]

    loan_params_map = {}
    escrow_map = {}
    if loan_account_ids:
        for lp in db.session.query(LoanParams).filter(
            LoanParams.account_id.in_(loan_account_ids)
        ).all():
            loan_params_map[lp.account_id] = lp

        # Escrow components for loan accounts (for debt summary PITI).
        for ec in db.session.query(EscrowComponent).filter(
            EscrowComponent.account_id.in_(loan_account_ids),
        ).all():
            escrow_map.setdefault(ec.account_id, []).append(ec)

    return loan_params_map, escrow_map


def _load_account_params(user_id, accounts):
    """Batch-load all account-type-specific parameters.

    Returns a dict with maps for each param type and supporting data
    needed by the projection loop.
    """
    interest_params_map = {}
    interest_account_ids = [
        a.id for a in accounts
        if a.account_type and a.account_type.has_interest
    ]
    if interest_account_ids:
        for hp in db.session.query(InterestParams).filter(
            InterestParams.account_id.in_(interest_account_ids)
        ).all():
            interest_params_map[hp.account_id] = hp

    # Investment/retirement: parameterized types that are not interest-bearing
    # and not amortizing -- by elimination, these use InvestmentParams.
    investment_params_map = {}
    inv_account_ids = [
        a.id for a in accounts
        if a.account_type
        and a.account_type.has_parameters
        and not a.account_type.has_interest
        and not a.account_type.has_amortization
    ]
    if inv_account_ids:
        for ip in db.session.query(InvestmentParams).filter(
            InvestmentParams.account_id.in_(inv_account_ids)
        ).all():
            investment_params_map[ip.account_id] = ip

    # F-22 / Commit 18: shared deduction batch loader; replaces the
    # filter-shape duplicate that previously lived inline here and in
    # retirement_dashboard_service / year_end_summary_service.
    deductions_by_account = load_active_deductions_for_accounts(
        user_id, list(investment_params_map.keys()),
    ) if investment_params_map else {}

    # F-20 / MED-06 / F-032: raise-aware gross-biweekly from the
    # paycheck engine, not the off-engine
    # ``annual_salary / pay_periods_per_year`` recompute which silently
    # dropped any applicable SalaryRaise row.  ``income_service`` wraps
    # ``calculate_paycheck`` so this producer agrees with the engine
    # value the DTI denominator (and every other income-derived
    # surface) consumes downstream.
    salary_gross_biweekly = income_service.get_current_gross_biweekly(
        user_id,
    )

    loan_params_map, escrow_map = _load_loan_params_and_escrow(accounts)

    return {
        "interest_params_map": interest_params_map,
        "investment_params_map": investment_params_map,
        "deductions_by_account": deductions_by_account,
        "salary_gross_biweekly": salary_gross_biweekly,
        "loan_params_map": loan_params_map,
        "escrow_map": escrow_map,
    }


def _compute_base_balances(acct, kind, acct_interest_params, ctx):
    """Compute the base per-period balance map and current balance.

    Interest-bearing accounts (with a params row) layer interest on top
    of the base calculation; every other account routes through the
    canonical entries-aware resolver.  Loan and investment accounts
    still get a balances map here, but their ``current_balance`` is
    overridden later from the amortization / growth projection.

    Args:
        acct: The Account instance.
        kind: The account's :class:`AccountProjectionKind`.
        acct_interest_params: The account's InterestParams row, or None.
        ctx: The shared :class:`_ProjectionContext`.

    Returns:
        ``(balances, current_bal)`` -- the period-id-keyed balance map
        and the current-period balance (falling back to the anchor
        balance when there is no current period).
    """
    anchor_balance = acct.current_anchor_balance or Decimal("0.00")
    anchor_period_id = acct.current_anchor_period_id or (
        ctx.current_period.id if ctx.current_period else None
    )
    scenario_id = ctx.params.get("scenario_id")

    balances = {}
    # MED-01 / S6-03: one flag-driven classifier shared with the
    # year-end summary's ``_get_account_balance_map``.  The
    # ``interest_params`` row presence still guards the interest path so
    # an account that flags ``has_interest=True`` but has no params row
    # falls through to the canonical resolver (the pre-Commit-28 behavior
    # the param-map-presence check delivered as a happy accident; the
    # classifier preserves it explicitly).
    if kind is AccountProjectionKind.INTEREST and acct_interest_params:
        # HYSA / interest-bearing path.  Continues to layer interest on
        # top of the base balance calculation.  Entry-aware reduction
        # for any envelope expenses on this account is applied
        # unconditionally by ``_entry_aware_amount`` post-Commit-5 (the
        # seam was removed at the math layer: an unloaded ``entries``
        # relationship now lazy-loads via the SQLAlchemy descriptor
        # rather than silently degrading to ``effective_amount``).
        if anchor_period_id:
            acct_transactions = [
                txn for txn in ctx.all_transactions
                if txn.account_id == acct.id
            ]
            balances, _ = balance_calculator.calculate_balances_with_interest(
                anchor_balance=anchor_balance,
                anchor_period_id=anchor_period_id,
                periods=ctx.all_periods,
                transactions=acct_transactions,
                interest_params=acct_interest_params,
            )
    elif scenario_id is not None:
        # Non-interest checking / savings / loan / investment accounts
        # route through the canonical entries-aware producer (CRIT-01 /
        # F-009 / E-25).  ``balances_for`` owns its own transaction query
        # with ``selectinload(Transaction.entries)`` and resolves the
        # anchor via the dated ``AccountAnchorHistory`` source of truth,
        # so the per-tile checking balance no longer silently disagrees
        # with the grid (symptom #1: $160 on grid vs $114.29 here
        # pre-fix, because /savings did not eager-load entries and
        # ``_entry_aware_amount`` returned ``effective_amount``
        # unchanged).  Loan and investment accounts still compute a
        # ``balances`` map here, but ``current_bal`` is overridden below
        # from the amortization / growth projection; the resolver call
        # is cheap and uniform.
        result = balance_resolver.balances_for(
            acct, scenario_id, ctx.all_periods,
        )
        balances = result.balances

    current_bal = (
        balances.get(ctx.current_period.id)
        if ctx.current_period else anchor_balance
    )
    return balances, current_bal


def _loan_projected_horizons(schedule, all_periods, original_principal, today):
    """Project a loan's balance at the 3 / 6 / 12-month horizons.

    Routes through the shared ``compute_loan_period_balance_map`` (F-21 /
    Commit 19) so the dashboard's projected balances agree to the cent
    with the year-end net-worth liability and debt-progress sections
    (both consumers read the same period-end-keyed map).  Pre-F-21 this
    site ran a parallel target-month-first walk over the schedule that
    answered a slightly different question and produced cents-precise
    drift across the two surfaces; see ``F-21`` in
    ``docs/audits/financial_calculations/remediation_follow_up.md`` and
    ``account_projection.compute_loan_period_balance_map`` for the
    locked semantic.

    Args:
        schedule: The resolver's amortization schedule.
        all_periods: All pay periods for the user.
        original_principal: The loan's original principal (keys the map).
        today: The reference date the horizon offsets advance from.

    Returns:
        Dict mapping a horizon label ("3 months" / "6 months" /
        "1 year") to the projected period-end balance, omitting horizons
        with no matching period.
    """
    balance_map = compute_loan_period_balance_map(
        schedule, all_periods, original_principal,
    )
    projected = {}
    for label, month_offset in [
        ("3 months", 3), ("6 months", 6), ("1 year", 12),
    ]:
        target_m = today.month + month_offset
        target_y = today.year + (target_m - 1) // 12
        target_m = (target_m - 1) % 12 + 1
        target_dt = date(target_y, target_m, 1)
        target_period = find_period_containing_date(all_periods, target_dt)
        if target_period is not None and target_period.id in balance_map:
            projected[label] = balance_map[target_period.id]
    return projected


def _loan_ever_paid_off(acct_loan_params, anchor_events, loan_ctx):
    """Return whether confirmed payments have EVER retired this loan.

    Distinct from "balance is zero as of today": the per-tile current
    balance correctly excludes settled payments dated in the future,
    whereas this flag asks "have my confirmed payments ever retired this
    loan?", regardless of when those payments are dated.  A resolver call
    with ``as_of=date.max`` replays every confirmed payment forward and
    answers that directly.  Requires at least one confirmed payment so a
    brand-new loan with a zero anchor balance (degenerate input) does not
    render as "paid off" -- preserves the historical ``_check_loan_paid_off``
    semantic.

    Args:
        acct_loan_params: The account's LoanParams.
        anchor_events: The account's LoanAnchorEvent rows.
        loan_ctx: The loaded loan context (payments + rate changes).

    Returns:
        True when confirmed payments have ever retired the loan.
    """
    has_confirmed = any(p.is_confirmed for p in loan_ctx.payments)
    if not has_confirmed:
        return False
    ever_state = loan_resolver.resolve_loan(
        acct_loan_params, anchor_events,
        loan_ctx.payments, loan_ctx.rate_changes,
        date.max,
    )
    return ever_state.current_balance == Decimal("0.00")


def _compute_loan_account(acct, acct_loan_params, scenario_id, all_periods):
    """Resolve current balance, payment, payoff, and projection for a loan.

    Loads the loan context (payments + escrow + rate changes) and runs
    the loan resolver (E-18 / Commit 13), which is the source of truth
    for current_balance, monthly_payment, schedule, and payoff_date --
    the same dollar figures rendered on the loan card and the year-end
    net-worth liability.  The resolver-derived ``current_balance``
    replaces the stored ``LoanParams.current_principal`` read that pre-
    E-18 produced the F-008 stored-vs-engine divergence on this tile.

    Args:
        acct: The loan Account instance.
        acct_loan_params: The account's LoanParams.
        scenario_id: The baseline scenario id (or None).
        all_periods: All pay periods for the user.

    Returns:
        A :class:`_LoanAccountResult` with the resolver-derived figures.
    """
    loan_ctx = load_loan_context(acct.id, scenario_id, acct_loan_params)
    anchor_events = (
        db.session.query(LoanAnchorEvent)
        .filter_by(account_id=acct.id)
        .all()
    )
    today = date.today()
    state = loan_resolver.resolve_loan(
        acct_loan_params, anchor_events, loan_ctx.payments,
        loan_ctx.rate_changes, today,
    )
    projected = _loan_projected_horizons(
        state.schedule, all_periods,
        acct_loan_params.original_principal, today,
    )
    return _LoanAccountResult(
        current_balance=state.current_balance,
        monthly_payment=state.monthly_payment,
        payoff_date=state.payoff_date,
        projected=projected,
        is_paid_off=_loan_ever_paid_off(
            acct_loan_params, anchor_events, loan_ctx,
        ),
    )


def _compute_needs_setup(
    acct, kind, acct_interest_params, acct_loan_params, acct_investment_params,
):
    """Return whether a parameterized account still needs its params row.

    MED-01 / S6-03: consults the same flag-driven classifier the
    projection dispatcher uses, so the "needs setup" predicate and the
    projection path agree on one account-type taxonomy.

    Args:
        acct: The Account instance.
        kind: The account's :class:`AccountProjectionKind`.
        acct_interest_params: The InterestParams row, or None.
        acct_loan_params: The LoanParams row, or None.
        acct_investment_params: The InvestmentParams row, or None.

    Returns:
        True when the account flags ``has_parameters`` but its
        type-specific params row is missing.
    """
    if not (acct.account_type and acct.account_type.has_parameters):
        return False
    if kind is AccountProjectionKind.INTEREST:
        return acct_interest_params is None
    if kind is AccountProjectionKind.AMORTIZING:
        return acct_loan_params is None
    if kind is AccountProjectionKind.INVESTMENT:
        return acct_investment_params is None
    return False


def _investment_horizons(projection, all_periods, current_period):
    """Map a growth projection to the 3 / 6 / 12-month horizon balances.

    Args:
        projection: The growth engine's per-period projection.
        all_periods: All pay periods for the user.
        current_period: The current :class:`PayPeriod`.

    Returns:
        Dict mapping a horizon label to the projected end balance,
        omitting horizons that fall outside the projection.
    """
    proj_by_idx = {
        p.period_index: pb.end_balance
        for pb in projection
        for p in all_periods
        if p.id == pb.period_id
    }
    projected = {}
    for offset_label, offset_count in [
        ("3 months", 6), ("6 months", 13), ("1 year", 26),
    ]:
        target_idx = current_period.period_index + offset_count
        if target_idx in proj_by_idx:
            projected[offset_label] = proj_by_idx[target_idx]
    return projected


def _project_investment(acct, investment_params, current_bal, ctx):
    """Compute growth projections for an investment/retirement account."""
    acct_deductions = ctx.params["deductions_by_account"].get(acct.id, [])
    adapted_deductions = adapt_deductions(acct_deductions)
    acct_contributions = [
        t for t in ctx.all_shadow_income
        if t.account_id == acct.id
    ]

    inputs = build_investment_projection_inputs(
        acct.id, investment_params, adapted_deductions, acct_contributions,
        ctx.all_periods, ctx.current_period,
        ctx.params["salary_gross_biweekly"],
    )

    future_periods = [
        p for p in ctx.all_periods
        if p.period_index >= ctx.current_period.period_index
    ]
    if not future_periods:
        return {}

    projection = growth_engine.project_balance(
        current_balance=current_bal,
        assumed_annual_return=investment_params.assumed_annual_return,
        periods=future_periods,
        periodic_contribution=inputs.periodic_contribution,
        employer_params=inputs.employer_params,
        annual_contribution_limit=inputs.annual_contribution_limit,
        ytd_contributions_start=inputs.ytd_contributions,
    )
    return _investment_horizons(
        projection, ctx.all_periods, ctx.current_period,
    )


def _project_one_account(acct, ctx):
    """Compute the projection dict for a single account.

    Dispatches to the appropriate projection engine based on account
    type: the loan resolver for loans, the growth engine for
    investments, and the canonical balance calculator for everything
    else.

    Args:
        acct: The Account instance.
        ctx: The shared :class:`_ProjectionContext`.

    Returns:
        A dict with keys: account, current_balance, projected,
        needs_setup, is_paid_off, plus optional type-specific params
        (interest_params / investment_params / loan_params +
        monthly_payment + payoff_date).
    """
    kind = classify_account(acct)
    acct_interest_params = ctx.params["interest_params_map"].get(acct.id)
    acct_loan_params = ctx.params["loan_params_map"].get(acct.id)
    acct_investment_params = ctx.params["investment_params_map"].get(acct.id)

    balances, current_bal = _compute_base_balances(
        acct, kind, acct_interest_params, ctx,
    )

    loan_result = None
    if acct_loan_params:
        loan_result = _compute_loan_account(
            acct, acct_loan_params, ctx.params.get("scenario_id"),
            ctx.all_periods,
        )
        current_bal = loan_result.current_balance
        projected = loan_result.projected
    elif acct_investment_params and ctx.current_period:
        projected = _project_investment(
            acct, acct_investment_params, current_bal, ctx,
        )
    else:
        projected = project_balance_horizons(
            ctx.current_period, ctx.all_periods, balances,
        )

    needs_setup = _compute_needs_setup(
        acct, kind, acct_interest_params, acct_loan_params,
        acct_investment_params,
    )

    ad = {
        "account": acct,
        "current_balance": current_bal,
        "projected": projected,
        "needs_setup": needs_setup,
        "is_paid_off": loan_result.is_paid_off if loan_result else False,
    }
    if acct_interest_params:
        ad["interest_params"] = acct_interest_params
    if acct_investment_params:
        ad["investment_params"] = acct_investment_params
    if acct_loan_params:
        ad["loan_params"] = acct_loan_params
        ad["monthly_payment"] = loan_result.monthly_payment
        ad["payoff_date"] = loan_result.payoff_date
    return ad


def _compute_account_projections(accounts, ctx):
    """Compute balance projections for each account.

    Args:
        accounts: List of Account model instances.
        ctx: The shared :class:`_ProjectionContext` bundling the
            pre-loaded transactions, periods, current period, and
            type-specific parameter maps.

    Returns:
        A list of per-account dicts (see :func:`_project_one_account`).
    """
    return [_project_one_account(acct, ctx) for acct in accounts]


def _get_current_paycheck_breakdown(user_id, all_periods, current_period):
    """Compute the canonical paycheck breakdown for the current period.

    The single income producer this module uses for any engine-derived
    income figure (MED-06 / F-032).  Both consumers -- the savings-goal
    trajectory's net biweekly pay and the DTI denominator's gross
    monthly income -- route through this helper so the page cannot
    silently disagree with the paycheck engine on the same period.
    Pre-Commit-26 the DTI denominator read the off-engine
    ``annual_salary / pay_periods`` recompute, which dropped applicable
    ``SalaryRaise`` rows; the engine applies raises period-by-period
    via ``_apply_raises`` and is therefore the only correct source for
    a raise-aware monthly gross.

    Args:
        user_id: Integer ID of the current user.
        all_periods: All pay periods for the user (passed through to
            the paycheck engine for 3rd-paycheck detection and the
            FICA SS wage-base cap's cumulative-wage tracking).
        current_period: The current :class:`PayPeriod`, or ``None``.

    Returns:
        :class:`PaycheckBreakdown` for the current period under the
        user's active salary profile, or ``None`` if ``current_period``
        is ``None`` or no active profile exists.  Callers treat
        ``None`` as "no income data on the page" rather than as a zero
        amount, since absence of an income source is structurally
        different from a real zero (E-12).
    """
    if current_period is None:
        return None

    # Resolve-active-profile -> load-tax-configs -> calculate_paycheck.
    # ``dashboard_service`` runs the same three steps, but the two return
    # different contracts (that one keeps only ``net_pay``; this one
    # returns the full PaycheckBreakdown for the DTI / trajectory math), so
    # they are deliberately separate surfaces over the same calculator
    # rather than a shared helper (coding-standards rule 13).
    # One-sided ``duplicate-code`` disable (see plan.md Phase 2 notes).
    # pylint: disable=duplicate-code
    profile = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=user_id, is_active=True)
        .first()
    )
    if profile is None:
        return None

    tax_configs = load_tax_configs(user_id, profile)
    return paycheck_calculator.calculate_paycheck(
        profile, current_period, all_periods, tax_configs,
    )
    # pylint: enable=duplicate-code


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

    progress_pct = 0
    if resolved_target and resolved_target > Decimal("0.00"):
        progress_pct = min(
            100,
            int(acct_balance / resolved_target * 100),
        )

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


def _compute_goal_progress(user_id, account_data, all_periods, net_biweekly_pay):
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

    Returns:
        List of dicts with keys: goal, current_balance, progress_pct,
        remaining_periods, required_contribution, resolved_target,
        goal_mode_id, income_descriptor, has_salary_data, trajectory,
        monthly_contribution.
    """
    goals = (
        db.session.query(SavingsGoal)
        .filter_by(user_id=user_id, is_active=True)
        .all()
    )

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


def _recent_settled_expenses_monthly(all_periods, current_period, scenario):
    """Average monthly settled expenses over the last 6 pay periods.

    Sums settled expense transactions across the most recent 6 periods
    (at or before the current period) and converts the per-period
    average to a monthly figure via the biweekly-to-monthly factor.

    Args:
        all_periods: All pay periods for the user.
        current_period: The current :class:`PayPeriod`, or ``None``.
        scenario: The baseline scenario, or ``None``.

    Returns:
        The monthly average as a Decimal.  ``Decimal("0.00")`` when
        there is no current period / scenario or no recent periods.
    """
    if not (current_period and scenario):
        return Decimal("0.00")

    recent_periods = [
        p for p in all_periods
        if p.period_index <= current_period.period_index
    ][-6:]
    if not recent_periods:
        return Decimal("0.00")

    recent_period_ids = [p.id for p in recent_periods]
    recent_txns = (
        db.session.query(Transaction)
        .filter(
            Transaction.pay_period_id.in_(recent_period_ids),
            Transaction.scenario_id == scenario.id,
            Transaction.is_deleted.is_(False),
        )
        .all()
    )

    total_expenses = Decimal("0.00")
    for txn in recent_txns:
        if txn.is_expense and txn.status and txn.status.is_settled:
            total_expenses += Decimal(str(txn.effective_amount))

    per_period = total_expenses / len(recent_periods)
    return per_period * PAY_PERIODS_PER_YEAR / MONTHS_PER_YEAR


def _committed_expense_floor(user_id, accounts):
    """Committed monthly expense floor from active checking templates.

    Sums the monthly-normalized commitment of active expense templates
    and active outgoing transfer templates on the user's checking
    accounts, via the canonical obligations aggregator (E-24 / HIGH-05)
    -- so the same skip-ONCE / skip-expired filter the /obligations
    page applies governs the emergency-fund baseline.

    Args:
        user_id: Integer ID of the current user.
        accounts: List of Account model instances.

    Returns:
        The committed monthly floor as a Decimal.  ``Decimal("0.00")``
        when the user has no checking account.
    """
    checking_type_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)
    checking_ids = [
        acct.id for acct in accounts
        if acct.account_type_id == checking_type_id
    ]
    if not checking_ids:
        return Decimal("0.00")

    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    active_expense_templates = (
        db.session.query(TransactionTemplate)
        .filter(
            TransactionTemplate.user_id == user_id,
            TransactionTemplate.account_id.in_(checking_ids),
            TransactionTemplate.transaction_type_id == expense_type_id,
            TransactionTemplate.is_active.is_(True),
        )
        .all()
    )
    active_transfer_templates = (
        db.session.query(TransferTemplate)
        .filter(
            TransferTemplate.user_id == user_id,
            TransferTemplate.from_account_id.in_(checking_ids),
            TransferTemplate.is_active.is_(True),
        )
        .all()
    )
    return obligations_aggregator.committed_monthly(
        list(active_expense_templates) + list(active_transfer_templates),
        date.today(),
    )


def _compute_avg_monthly_expenses(
    user_id, accounts, all_periods, current_period, scenario,
):
    """Compute average monthly expenses for emergency fund coverage.

    Uses the higher of: historical settled expenses from the last 6
    periods, or the committed monthly baseline from active templates.
    """
    historical = _recent_settled_expenses_monthly(
        all_periods, current_period, scenario,
    )
    floor = _committed_expense_floor(user_id, accounts)
    return max(historical, floor)


def _accumulate_loan_debt(
    loan_ads: list, escrow_map: dict,
) -> tuple[Decimal, Decimal, Decimal, list]:
    """Sum debt metrics across active (non-paid-off) loan accounts.

    Walks the per-account loan dicts, skipping paid-off loans and loans
    whose resolver-derived current balance is zero, and accumulates the
    running totals the debt summary reports.

    Args:
        loan_ads: Per-account dicts that carry a ``loan_params`` key
            (the loan subset of ``_compute_account_projections`` output).
        escrow_map: Dict mapping account_id to list of EscrowComponent.

    Returns:
        ``(total_debt, total_monthly, weighted_rate_sum, payoff_dates)``
        -- the running sums (Decimals) and the list of per-loan payoff
        dates.
    """
    total_debt = Decimal("0.00")
    total_monthly = Decimal("0.00")
    weighted_rate_sum = Decimal("0.00")
    payoff_dates = []

    for ad in loan_ads:
        if ad["is_paid_off"]:
            continue

        lp = ad["loan_params"]
        # Resolver-derived current_balance (E-18 / Commit 15).  Same
        # dollar figure as the loan card; replaces the previous read
        # of the non-authoritative ``LoanParams.current_principal``
        # column that produced F-008's stored-vs-engine divergence.
        principal = ad["current_balance"] or Decimal("0.00")

        if principal <= Decimal("0.00"):
            continue

        # ``LoanParams.interest_rate`` is the BASE rate (the
        # rate-history layered current rate would require the
        # resolver's ``_rate_at_date`` and is HIGH-08 territory).
        # Carried forward so weighted_avg_rate retains its historical
        # meaning across this commit.
        rate = Decimal(str(lp.interest_rate))
        monthly_pi = ad["monthly_payment"]

        # Include escrow (property tax, insurance) for PITI total.
        components = escrow_map.get(ad["account"].id, [])
        monthly_escrow = escrow_calculator.calculate_monthly_escrow(components)
        monthly_total = (monthly_pi + monthly_escrow).quantize(
            _TWO_PLACES, rounding=ROUND_HALF_UP
        )

        total_debt += principal
        total_monthly += monthly_total
        weighted_rate_sum += rate * principal

        if ad.get("payoff_date"):
            payoff_dates.append(ad["payoff_date"])

    return total_debt, total_monthly, weighted_rate_sum, payoff_dates


def _compute_debt_summary(
    account_data: list,
    escrow_map: dict,
) -> dict | None:
    """Compute aggregate debt metrics across active loan accounts.

    Uses per-account data already computed by _compute_account_projections
    (monthly_payment, payoff_date, loan_params, is_paid_off).  Escrow
    components are loaded separately and included in the monthly total
    so DTI reflects PITI (principal, interest, taxes, insurance).

    Paid-off loans are excluded from all aggregate metrics.  Loans with
    missing LoanParams are skipped with a warning.

    Args:
        account_data: List of per-account dicts from
            _compute_account_projections.
        escrow_map: Dict mapping account_id to list of EscrowComponent.

    Returns:
        Dict with keys: total_debt, total_monthly_payments,
        weighted_avg_rate, projected_debt_free_date.
        Returns None if no loan accounts with params exist.
    """
    loan_ads = [ad for ad in account_data if ad.get("loan_params")]
    if not loan_ads:
        return None

    total_debt, total_monthly, weighted_rate_sum, payoff_dates = (
        _accumulate_loan_debt(loan_ads, escrow_map)
    )

    if total_debt > Decimal("0.00"):
        weighted_avg_rate = (weighted_rate_sum / total_debt).quantize(
            _RATE_PLACES, rounding=ROUND_HALF_UP
        )
    else:
        weighted_avg_rate = Decimal("0.00000")

    debt_free_date = max(payoff_dates) if payoff_dates else None

    return {
        "total_debt": total_debt.quantize(_TWO_PLACES),
        "total_monthly_payments": total_monthly.quantize(_TWO_PLACES),
        "weighted_avg_rate": weighted_avg_rate,
        "projected_debt_free_date": debt_free_date,
    }


def _get_dti_label(dti_pct: Decimal) -> str:
    """Return the DTI health label based on conventional thresholds.

    Boundaries: < 36% is healthy, 36%--43% is moderate, > 43% is high.
    36.0% is moderate (not healthy).  43.0% is moderate (not high).

    Args:
        dti_pct: DTI as a percentage (e.g. Decimal("34.2")).

    Returns:
        'healthy', 'moderate', or 'high'.
    """
    if dti_pct < _DTI_HEALTHY_THRESHOLD:
        return "healthy"
    if dti_pct > _DTI_HIGH_THRESHOLD:
        return "high"
    return "moderate"


def _load_archived_accounts(user_id: int) -> list[dict]:
    """Load archived accounts with minimal data for the collapsed section.

    Archived accounts do not receive balance projections, engine calls,
    or goal calculations -- they are historical.  Each dict contains
    the Account ORM object and its last known balance.

    Args:
        user_id: Integer ID of the current user.

    Returns:
        List of dicts with keys: account, current_balance.
    """
    accounts = (
        db.session.query(Account)
        .filter_by(user_id=user_id, is_active=False)
        .order_by(Account.sort_order, Account.name)
        .all()
    )
    result = []
    for acct in accounts:
        result.append({
            "account": acct,
            "current_balance": acct.current_anchor_balance or Decimal("0.00"),
        })
    return result


def _group_accounts_by_category(account_data):
    """Group account data dicts by account type category.

    Returns an OrderedDict with category labels as keys, preserving
    the display order: Asset, Liability, Retirement, Investment, Other.
    """
    category_order = [
        ("asset", ref_cache.acct_category_id(AcctCategoryEnum.ASSET)),
        ("liability", ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY)),
        ("retirement", ref_cache.acct_category_id(AcctCategoryEnum.RETIREMENT)),
        ("investment", ref_cache.acct_category_id(AcctCategoryEnum.INVESTMENT)),
    ]
    grouped = OrderedDict()
    for cat_label, cat_id in category_order:
        cat_accounts = [
            ad for ad in account_data
            if ad["account"].account_type
            and ad["account"].account_type.category_id == cat_id
        ]
        if cat_accounts:
            grouped[cat_label] = cat_accounts

    uncategorized = [
        ad for ad in account_data
        if not ad["account"].account_type
        or not ad["account"].account_type.category_id
    ]
    if uncategorized:
        grouped["other"] = uncategorized

    return grouped
