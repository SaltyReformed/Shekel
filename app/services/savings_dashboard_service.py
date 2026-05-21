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
from app.models.ref import AccountType
from app.models.salary_profile import SalaryProfile
from app.models.savings_goal import SavingsGoal
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
from app.utils.balance_predicates import balance_excluded_status_ids
from app.utils.money import MONTHS_PER_YEAR, PAY_PERIODS_PER_YEAR, round_money

logger = logging.getLogger(__name__)

_TWO_PLACES = Decimal("0.01")
_RATE_PLACES = Decimal("0.00001")
_DTI_HEALTHY_THRESHOLD = Decimal("36")
_DTI_HIGH_THRESHOLD = Decimal("43")


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
    # ── Load core data ──────────────────────────────────────────
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
    # ``txn.status.excludes_from_balance`` /
    # ``txn.status.is_settled`` without an N+1; the explicit INNER
    # JOIN is dropped because the ``Transaction.status_id`` filter no
    # longer needs it.
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

    # ── Load account-type-specific parameters ───────────────────
    params = _load_account_params(user_id, accounts)

    # ── Compute per-account projections ─────────────────────────
    params["scenario_id"] = scenario.id if scenario else None
    account_data = _compute_account_projections(
        accounts, all_transactions, all_shadow_income, all_periods,
        current_period, params,
    )

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
        user_id, all_periods, current_period,
    )
    net_biweekly_pay = (
        current_breakdown.net_pay if current_breakdown is not None
        else Decimal("0.00")
    )

    # ── Savings goals ───────────────────────────────────────────
    goal_data = _compute_goal_progress(
        user_id, account_data, all_periods, net_biweekly_pay,
    )

    # ── Emergency fund metrics ──────────────────────────────────
    avg_monthly_expenses = _compute_avg_monthly_expenses(
        user_id, accounts, all_periods, current_period, scenario,
    )

    # Sum liquid account balances for emergency fund calculation.
    total_savings = Decimal("0.00")
    for ad in account_data:
        if ad["account"].account_type and ad["account"].account_type.is_liquid:
            total_savings += ad["current_balance"] or Decimal("0.00")

    emergency_metrics = savings_goal_service.calculate_savings_metrics(
        total_savings, avg_monthly_expenses,
    )

    # ── Template helpers ────────────────────────────────────────
    # Liquid accounts appear in the savings goal form dropdown.
    savings_accounts = [
        ad["account"] for ad in account_data
        if ad["account"].account_type and ad["account"].account_type.is_liquid
    ]

    grouped_accounts = _group_accounts_by_category(account_data)

    # ── Archived accounts (minimal data, no projections) ───────
    archived_accounts = _load_archived_accounts(user_id)

    # ── Debt summary and DTI ───────────────────────────────────
    debt_summary = _compute_debt_summary(
        account_data, params["escrow_map"],
    )
    if debt_summary is not None:
        # MED-06 / F-032: ``gross_biweekly`` is the raise-aware engine
        # output for the current period (``calculate_paycheck`` ->
        # ``PaycheckBreakdown.gross_biweekly``), NOT the off-engine
        # ``annual_salary / pay_periods`` recompute the DTI block read
        # pre-Commit-26.  The biweekly -> monthly conversion factor
        # (``PAY_PERIODS_PER_YEAR / MONTHS_PER_YEAR``) is preserved
        # because once the per-period gross is engine-correct, the
        # period-to-monthly normalization is a structural property of
        # the 26-period pay schedule (Shekel is a biweekly app), not a
        # raise-dropping shortcut -- this is a "genuine flat conversion"
        # in the sense Commit 26 calls out, applied to an engine-derived
        # input.  See ``app/utils/money.py`` for the constants.
        gross_biweekly = (
            current_breakdown.gross_biweekly if current_breakdown is not None
            else Decimal("0.00")
        )
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

    return {
        "account_data": account_data,
        "grouped_accounts": grouped_accounts,
        "goal_data": goal_data,
        "emergency_metrics": emergency_metrics,
        "total_savings": total_savings,
        "avg_monthly_expenses": avg_monthly_expenses,
        "savings_accounts": savings_accounts,
        "archived_accounts": archived_accounts,
        "debt_summary": debt_summary,
    }


# ── Private helpers ──────────────────────────────────────────────


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

    # Amortizing loan types: already metadata-driven via has_amortization.
    amort_type_ids = {
        at.id for at in db.session.query(AccountType).filter_by(has_amortization=True).all()
    }

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

    loan_params_map = {}
    loan_account_ids = [a.id for a in accounts if a.account_type_id in amort_type_ids]
    if loan_account_ids:
        for lp in db.session.query(LoanParams).filter(
            LoanParams.account_id.in_(loan_account_ids)
        ).all():
            loan_params_map[lp.account_id] = lp

    # Escrow components for loan accounts (for debt summary PITI).
    escrow_map = {}
    if loan_account_ids:
        for ec in db.session.query(EscrowComponent).filter(
            EscrowComponent.account_id.in_(loan_account_ids),
        ).all():
            escrow_map.setdefault(ec.account_id, []).append(ec)

    return {
        "interest_params_map": interest_params_map,
        "investment_params_map": investment_params_map,
        "deductions_by_account": deductions_by_account,
        "salary_gross_biweekly": salary_gross_biweekly,
        "loan_params_map": loan_params_map,
        "escrow_map": escrow_map,
    }


def _compute_account_projections(
    accounts, all_transactions, all_shadow_income, all_periods,
    current_period, params,
):
    """Compute balance projections for each account.

    Dispatches to the appropriate projection engine based on account
    type: amortization for loans, growth engine for investments,
    balance calculator for everything else.

    Returns a list of dicts, one per account, with keys: account,
    current_balance, projected, needs_setup, is_paid_off, and
    optional type-specific params.

    Args:
        accounts: List of Account model instances.
        all_transactions: Pre-loaded transactions for all accounts.
        all_shadow_income: Pre-loaded shadow income transactions.
        all_periods: All pay periods for the user.
        current_period: The current PayPeriod, or None.
        params: Dict from _load_account_params with type-specific
            parameter maps and scenario_id.
    """
    account_data = []

    for acct in accounts:
        acct_transactions = [
            txn for txn in all_transactions
            if txn.account_id == acct.id
        ]

        anchor_balance = acct.current_anchor_balance or Decimal("0.00")
        anchor_period_id = acct.current_anchor_period_id or (
            current_period.id if current_period else None
        )

        balances = {}
        acct_interest_params = params["interest_params_map"].get(acct.id)
        scenario_id = params.get("scenario_id")

        # MED-01 / S6-03: one flag-driven classifier shared with the
        # year-end summary's ``_get_account_balance_map``.  The
        # ``interest_params`` row presence still guards the interest
        # path so an account that flags ``has_interest=True`` but
        # has no params row falls through to the canonical resolver
        # (the pre-Commit-28 behavior the param-map-presence check
        # delivered as a happy accident; the classifier preserves
        # it explicitly).
        kind = classify_account(acct)

        if kind is AccountProjectionKind.INTEREST and acct_interest_params:
            # HYSA / interest-bearing path.  Continues to layer interest
            # on top of the base balance calculation.  Entry-aware
            # reduction for any envelope expenses on this account is
            # applied unconditionally by ``_entry_aware_amount`` post-
            # Commit-5 (the seam was removed at the math layer: an
            # unloaded ``entries`` relationship now lazy-loads via the
            # SQLAlchemy descriptor rather than silently degrading to
            # ``effective_amount``).
            if anchor_period_id:
                balances, _ = balance_calculator.calculate_balances_with_interest(
                    anchor_balance=anchor_balance,
                    anchor_period_id=anchor_period_id,
                    periods=all_periods,
                    transactions=acct_transactions,
                    interest_params=acct_interest_params,
                )
        elif scenario_id is not None:
            # Non-interest checking / savings / loan / investment
            # accounts route through the canonical entries-aware
            # producer (CRIT-01 / F-009 / E-25).  ``balances_for``
            # owns its own transaction query with
            # ``selectinload(Transaction.entries)`` and resolves the
            # anchor via the dated ``AccountAnchorHistory`` source of
            # truth, so the per-tile checking balance no longer
            # silently disagrees with the grid (symptom #1: $160 on
            # grid vs $114.29 here pre-fix, because /savings did not
            # eager-load entries and ``_entry_aware_amount`` returned
            # ``effective_amount`` unchanged).  Loan and investment
            # accounts still compute a ``balances`` map here, but
            # ``current_bal`` is overridden below from the
            # amortization / growth projection; the resolver call is
            # cheap and uniform.
            result = balance_resolver.balances_for(
                acct, scenario_id, all_periods,
            )
            balances = result.balances

        acct_loan_params = params["loan_params_map"].get(acct.id)
        acct_investment_params = params["investment_params_map"].get(acct.id)
        current_bal = balances.get(current_period.id) if current_period else anchor_balance
        projected = {}

        if acct_loan_params:
            # Load context (payments + escrow + rate changes), then
            # run the loan resolver (E-18 / Commit 13).  The resolver
            # is the source of truth for current_balance,
            # monthly_payment, schedule, payoff_date, and
            # total_interest -- same dollar figures rendered on the
            # loan card and the year-end net-worth liability.
            scenario_id = params.get("scenario_id")
            loan_ctx = load_loan_context(
                acct.id, scenario_id, acct_loan_params,
            )
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
            monthly = state.monthly_payment

            # Current balance from the resolver state.  Replaces the
            # stored ``LoanParams.current_principal`` read that pre-
            # E-18 produced the F-008 stored-vs-engine divergence on
            # this very tile.
            current_bal = state.current_balance

            # Projected balances at 3 / 6 / 12-month horizons.
            # F-21 / Commit 19: route through the shared
            # ``compute_loan_period_balance_map`` so the dashboard's
            # projected balances agree to the cent with the year-end
            # net-worth liability and debt-progress sections (both
            # consumers now read the same period-end-keyed map).
            # Pre-F-21 this site ran a parallel target-month-first
            # walk over ``state.schedule`` that answered a slightly
            # different question and produced cents-precise drift
            # across the two surfaces; see ``F-21`` in
            # ``docs/audits/financial_calculations/remediation_follow_up.md``
            # and ``account_projection.compute_loan_period_balance_map``
            # for the locked semantic.
            balance_map = compute_loan_period_balance_map(
                state.schedule, all_periods,
                acct_loan_params.original_principal,
            )
            for label, month_offset in [
                ("3 months", 3), ("6 months", 6), ("1 year", 12),
            ]:
                target_m = today.month + month_offset
                target_y = today.year + (target_m - 1) // 12
                target_m = (target_m - 1) % 12 + 1
                target_dt = date(target_y, target_m, 1)
                target_period = find_period_containing_date(
                    all_periods, target_dt,
                )
                if target_period is not None and target_period.id in balance_map:
                    projected[label] = balance_map[target_period.id]
        elif acct_investment_params and current_period:
            projected = _project_investment(
                acct, acct_investment_params, params, all_shadow_income,
                all_periods, current_period, current_bal,
            )
        else:
            for offset_label, offset_count in [("3 months", 6), ("6 months", 13), ("1 year", 26)]:
                if current_period:
                    target_idx = current_period.period_index + offset_count
                    for p in all_periods:
                        if p.period_index == target_idx and p.id in balances:
                            projected[offset_label] = balances[p.id]
                            break

        # MED-01 / S6-03: the third partial copy of the per-account-
        # type ladder used to live here.  Routed through the shared
        # classifier so the "needs setup" predicate consults the same
        # one taxonomy the projection dispatcher above does.
        needs_setup = False
        if acct.account_type and acct.account_type.has_parameters:
            if kind is AccountProjectionKind.INTEREST:
                needs_setup = acct_interest_params is None
            elif kind is AccountProjectionKind.AMORTIZING:
                needs_setup = acct_loan_params is None
            elif kind is AccountProjectionKind.INVESTMENT:
                needs_setup = acct_investment_params is None

        # Paid-off determination: ``state.current_balance`` from the
        # resolver above answers "what do I owe AS OF TODAY given
        # confirmed history" -- which correctly excludes settled
        # payments dated in the future from today's balance.  The
        # is_paid_off flag asks a different question: "have my
        # confirmed payments EVER retired this loan?", regardless of
        # when those payments are dated.  A second resolver call
        # with ``as_of=date.max`` replays every confirmed payment
        # forward and answers that question directly.  Additionally
        # require at least one confirmed payment so a brand-new loan
        # with a zero anchor balance (degenerate input) does not
        # render as "paid off" -- preserves the historical
        # _check_loan_paid_off semantic.
        is_paid_off = False
        if acct_loan_params:
            has_confirmed = any(
                p.is_confirmed for p in loan_ctx.payments
            )
            if has_confirmed:
                ever_state = loan_resolver.resolve_loan(
                    acct_loan_params, anchor_events,
                    loan_ctx.payments, loan_ctx.rate_changes,
                    date.max,
                )
                is_paid_off = ever_state.current_balance == Decimal("0.00")

        ad = {
            "account": acct,
            "current_balance": current_bal,
            "projected": projected,
            "needs_setup": needs_setup,
            "is_paid_off": is_paid_off,
        }
        if acct_interest_params:
            ad["interest_params"] = acct_interest_params
        if acct_investment_params:
            ad["investment_params"] = acct_investment_params
        if acct_loan_params:
            ad["loan_params"] = acct_loan_params
            ad["monthly_payment"] = monthly
            ad["payoff_date"] = state.payoff_date

        account_data.append(ad)

    return account_data


def _project_investment(
    acct, investment_params, params, all_shadow_income,
    all_periods, current_period, current_bal,
):
    """Compute growth projections for an investment/retirement account."""
    acct_deductions = params["deductions_by_account"].get(acct.id, [])
    adapted_deductions = adapt_deductions(acct_deductions)

    acct_contributions = [
        t for t in all_shadow_income
        if t.account_id == acct.id
    ]

    inputs = build_investment_projection_inputs(
        acct.id, investment_params, adapted_deductions, acct_contributions,
        all_periods, current_period, params["salary_gross_biweekly"],
    )

    future_periods = [
        p for p in all_periods
        if p.period_index >= current_period.period_index
    ]

    projected = {}
    if future_periods:
        projection = growth_engine.project_balance(
            current_balance=current_bal,
            assumed_annual_return=investment_params.assumed_annual_return,
            periods=future_periods,
            periodic_contribution=inputs.periodic_contribution,
            employer_params=inputs.employer_params,
            annual_contribution_limit=inputs.annual_contribution_limit,
            ytd_contributions_start=inputs.ytd_contributions,
        )
        proj_by_idx = {
            p.period_index: pb.end_balance
            for pb in projection
            for p in all_periods
            if p.id == pb.period_id
        }
        for offset_label, offset_count in [("3 months", 6), ("6 months", 13), ("1 year", 26)]:
            target_idx = current_period.period_index + offset_count
            if target_idx in proj_by_idx:
                projected[offset_label] = proj_by_idx[target_idx]

    return projected


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

    profile = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=user_id, is_active=True)
        .first()
    )
    if profile is None:
        return None

    from app.services import paycheck_calculator  # pylint: disable=import-outside-toplevel
    from app.services.tax_config_service import load_tax_configs  # pylint: disable=import-outside-toplevel

    tax_configs = load_tax_configs(user_id, profile)
    return paycheck_calculator.calculate_paycheck(
        profile, current_period, all_periods, tax_configs,
    )


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

    fixed_id = ref_cache.goal_mode_id(GoalModeEnum.FIXED)
    has_salary = net_biweekly_pay > Decimal("0.00")

    # Batch-load recurring transfer templates targeting goal accounts
    # to avoid N+1 queries.  obligations_aggregator.committed_monthly
    # handles per-pattern normalization to monthly equivalents and
    # applies the shared skip-ONCE / skip-expired filter.
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

    goal_data = []
    for goal in goals:
        acct_balance = Decimal("0.00")
        for ad in account_data:
            if ad["account"].id == goal.account_id:
                acct_balance = ad["current_balance"] or Decimal("0.00")
                break

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

        # Monthly contribution from recurring transfers into this account.
        # Routed through the one canonical aggregator (E-24 / HIGH-05) so
        # the same skip-ONCE / skip-expired filter applies that the
        # /obligations page applies; pre-Commit-23 this loop omitted the
        # expired-rule guard and inflated per-goal floors indefinitely.
        acct_templates = templates_by_account.get(goal.account_id, [])
        monthly_contribution = obligations_aggregator.committed_monthly(
            acct_templates, date.today(),
        )

        # Trajectory: projected completion date and pace indicator.
        trajectory = savings_goal_service.calculate_trajectory(
            current_balance=acct_balance,
            target_amount=resolved_target,
            monthly_contribution=monthly_contribution,
            target_date=goal.target_date,
        )

        goal_data.append({
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
        })

    return goal_data


def _compute_avg_monthly_expenses(
    user_id, accounts, all_periods, current_period, scenario,
):
    """Compute average monthly expenses for emergency fund coverage.

    Uses the higher of: historical settled expenses from the last 6
    periods, or the committed monthly baseline from active templates.
    """
    avg_monthly_expenses = Decimal("0.00")

    if current_period and scenario:
        recent_periods = [
            p for p in all_periods
            if p.period_index <= current_period.period_index
        ][-6:]

        if recent_periods:
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

            num_periods = len(recent_periods)
            if num_periods > 0:
                per_period = total_expenses / num_periods
                avg_monthly_expenses = (
                    per_period * PAY_PERIODS_PER_YEAR / MONTHS_PER_YEAR
                )

    # Committed monthly floor from active templates.
    checking_type_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)
    checking_ids = [
        acct.id for acct in accounts
        if acct.account_type_id == checking_type_id
    ]
    if checking_ids:
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
        # Same canonical aggregator the /obligations page uses
        # (E-24 / HIGH-05). The expired-template filter that was
        # silently missing pre-Commit-23 now applies here too, so the
        # emergency-fund baseline no longer inherits an expired
        # recurring expense or transfer indefinitely.
        committed_monthly = obligations_aggregator.committed_monthly(
            list(active_expense_templates) + list(active_transfer_templates),
            date.today(),
        )
        avg_monthly_expenses = max(avg_monthly_expenses, committed_monthly)

    return avg_monthly_expenses


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
