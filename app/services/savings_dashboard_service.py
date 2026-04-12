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
from app.models.loan_features import EscrowComponent
from app.models.loan_params import LoanParams
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.ref import AccountType, Status
from app.models.salary_profile import SalaryProfile
from app.models.savings_goal import SavingsGoal
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import (
    amortization_engine,
    balance_calculator,
    escrow_calculator,
    growth_engine,
    pay_period_service,
    savings_goal_service,
)
from app.services.investment_projection import (
    adapt_deductions,
    calculate_investment_inputs,
)
from app.services.loan_payment_service import (
    get_payment_history,
    load_loan_context,
)

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

    scenario = (
        db.session.query(Scenario)
        .filter_by(user_id=user_id, is_baseline=True)
        .first()
    )

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

    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    all_shadow_income = (
        db.session.query(Transaction)
        .join(Transaction.status)
        .options(joinedload(Transaction.status))
        .filter(
            Transaction.transfer_id.isnot(None),
            Transaction.transaction_type_id == income_type_id,
            Transaction.pay_period_id.in_(period_ids),
            Transaction.scenario_id == scenario.id,
            Transaction.is_deleted.is_(False),
            Status.excludes_from_balance.is_(False),
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

    # ── Net biweekly pay (for income-relative goals) ─────────
    net_biweekly_pay = _get_net_biweekly_pay(user_id, all_periods, current_period)

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
        gross_biweekly = params["salary_gross_biweekly"]
        if gross_biweekly > Decimal("0.00"):
            gross_monthly = (
                gross_biweekly * Decimal("26") / Decimal("12")
            ).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
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

    deductions_by_account = {}
    if investment_params_map:
        inv_account_ids = list(investment_params_map.keys())
        inv_deductions = (
            db.session.query(PaycheckDeduction)
            .join(SalaryProfile)
            .filter(
                SalaryProfile.user_id == user_id,
                SalaryProfile.is_active.is_(True),
                PaycheckDeduction.target_account_id.in_(inv_account_ids),
                PaycheckDeduction.is_active.is_(True),
            )
            .all()
        )
        for ded in inv_deductions:
            deductions_by_account.setdefault(ded.target_account_id, []).append(ded)

    salary_gross_biweekly = Decimal("0")
    active_profile = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=user_id, is_active=True)
        .first()
    )
    if active_profile:
        salary_gross_biweekly = (
            Decimal(str(active_profile.annual_salary))
            / (active_profile.pay_periods_per_year or 26)
        ).quantize(Decimal("0.01"))

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

        if anchor_period_id:
            if acct_interest_params:
                balances, _ = balance_calculator.calculate_balances_with_interest(
                    anchor_balance=anchor_balance,
                    anchor_period_id=anchor_period_id,
                    periods=all_periods,
                    transactions=acct_transactions,
                    interest_params=acct_interest_params,
                )
            else:
                balances, _ = balance_calculator.calculate_balances(
                    anchor_balance=anchor_balance,
                    anchor_period_id=anchor_period_id,
                    periods=all_periods,
                    transactions=acct_transactions,
                )

        acct_loan_params = params["loan_params_map"].get(acct.id)
        acct_investment_params = params["investment_params_map"].get(acct.id)
        current_bal = balances.get(current_period.id) if current_period else anchor_balance
        projected = {}

        if acct_loan_params:
            # Load all context data via the shared loader.
            scenario_id = params.get("scenario_id")
            loan_ctx = load_loan_context(
                acct.id, scenario_id, acct_loan_params,
            )

            proj = amortization_engine.get_loan_projection(
                acct_loan_params,
                payments=loan_ctx.payments,
                rate_changes=loan_ctx.rate_changes,
            )
            monthly = proj.summary.monthly_payment
            summary = proj.summary

            # Current balance from the projection.  For ARM loans
            # this is the user-verified anchor; for fixed-rate it is
            # derived from the schedule.
            current_bal = proj.current_balance

            # Projected balances: find the schedule row at each
            # target date.  Walk backward to find the last row on
            # or before the target month.
            today = date.today()
            for label, month_offset in [("3 months", 3), ("6 months", 6), ("1 year", 12)]:
                target_m = today.month + month_offset
                target_y = today.year + (target_m - 1) // 12
                target_m = (target_m - 1) % 12 + 1
                target_dt = date(target_y, target_m, 1)
                for row in reversed(proj.schedule):
                    if row.payment_date <= target_dt:
                        projected[label] = row.remaining_balance
                        break
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

        needs_setup = False
        if acct.account_type and acct.account_type.has_parameters:
            if acct.account_type.has_interest:
                needs_setup = acct_interest_params is None
            elif acct.account_type.has_amortization:
                needs_setup = acct_loan_params is None
            else:
                needs_setup = acct_investment_params is None

        # Paid-off determination: replay confirmed-only payments
        # through the amortization engine to check if the remaining
        # balance reaches exactly zero.
        is_paid_off = False
        if acct_loan_params:
            is_paid_off = _check_loan_paid_off(
                acct_loan_params, acct.id, params["scenario_id"],
            )

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
            ad["payoff_date"] = summary.payoff_date

        account_data.append(ad)

    return account_data


def _check_loan_paid_off(
    loan_params: LoanParams,
    account_id: int,
    scenario_id: int | None,
) -> bool:
    """Determine if a loan is paid off based on confirmed payments.

    Replays only confirmed (Paid/Settled) payments through the
    amortization engine and checks whether the remaining balance
    reaches exactly zero.  Projected payments are excluded -- a loan
    is "paid off" only when actual payments have retired the balance.

    Rate changes for ARM loans are omitted from the replay.  This is
    a minor simplification: rate changes affect the interest/principal
    split within each payment but do not change whether a fixed-amount
    payment sequence reaches zero balance.

    Args:
        loan_params: LoanParams model instance for the debt account.
        account_id: The debt account ID for the payment query.
        scenario_id: The baseline scenario ID, or None if no scenario
            exists (returns False immediately).

    Returns:
        True if confirmed payments bring the remaining balance to
        exactly Decimal("0.00").
    """
    if scenario_id is None:
        return False

    all_payments = get_payment_history(account_id, scenario_id)
    confirmed = [p for p in all_payments if p.is_confirmed]

    if not confirmed:
        return False

    orig_principal = Decimal(str(loan_params.original_principal))
    rate = Decimal(str(loan_params.interest_rate))

    # For ARM loans, pass original_principal=None to force
    # re-amortization from current state.
    is_arm = getattr(loan_params, "is_arm", False)
    original = None if is_arm else orig_principal

    # Start from origination with full term so past confirmed
    # payments match the schedule's year-month lookup.  Matches
    # the get_loan_projection() pattern from d2455e8.
    schedule = amortization_engine.generate_schedule(
        orig_principal, rate, loan_params.term_months,
        origination_date=loan_params.origination_date,
        payment_day=loan_params.payment_day,
        original_principal=original,
        term_months=loan_params.term_months,
        payments=confirmed,
    )

    if not schedule:
        # Empty schedule means current_principal <= 0 or
        # remaining_months <= 0 -- the engine cannot verify payoff
        # through payment replay.
        return False

    # Check the balance at the last CONFIRMED row, not the last row
    # overall.  The engine fills non-confirmed months with standard
    # contractual payments that would eventually bring any loan to
    # zero.  Only a confirmed payment driving the balance to zero
    # means the user has actually paid off the loan.
    confirmed_rows = [r for r in schedule if r.is_confirmed]
    if not confirmed_rows:
        return False

    return confirmed_rows[-1].remaining_balance == Decimal("0.00")


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

    inputs = calculate_investment_inputs(
        account_id=acct.id,
        investment_params=investment_params,
        deductions=adapted_deductions,
        all_contributions=acct_contributions,
        all_periods=all_periods,
        current_period=current_period,
        salary_gross_biweekly=params["salary_gross_biweekly"],
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


def _get_net_biweekly_pay(user_id, all_periods, current_period):
    """Load the user's current net biweekly pay from the paycheck calculator.

    Returns Decimal("0.00") if no active salary profile exists or if
    there is no current period.

    Args:
        user_id: Integer ID of the current user.
        all_periods: All pay periods for the user.
        current_period: The current PayPeriod, or None.

    Returns:
        Decimal -- net biweekly pay, or Decimal("0.00") if unavailable.
    """
    if current_period is None:
        return Decimal("0.00")

    profile = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=user_id, is_active=True)
        .first()
    )
    if profile is None:
        return Decimal("0.00")

    from app.services import paycheck_calculator  # pylint: disable=import-outside-toplevel
    from app.services.tax_config_service import load_tax_configs  # pylint: disable=import-outside-toplevel

    tax_configs = load_tax_configs(user_id, profile)
    breakdown = paycheck_calculator.calculate_paycheck(
        profile, current_period, all_periods, tax_configs,
    )
    return breakdown.net_pay


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
    # to avoid N+1 queries.  compute_committed_monthly() handles the
    # per-pattern normalization to monthly equivalents.
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
        # Reuses compute_committed_monthly() for pattern normalization.
        acct_templates = templates_by_account.get(goal.account_id, [])
        monthly_contribution = savings_goal_service.compute_committed_monthly(
            [], acct_templates,
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
                avg_monthly_expenses = per_period * Decimal("26") / Decimal("12")

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
        committed_monthly = savings_goal_service.compute_committed_monthly(
            active_expense_templates, active_transfer_templates,
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
        principal = Decimal(str(lp.current_principal))

        if principal <= Decimal("0.00"):
            continue

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
