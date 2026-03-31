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
from decimal import Decimal

from app import ref_cache
from app.enums import AcctCategoryEnum, AcctTypeEnum, TxnTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.interest_params import InterestParams
from app.models.investment_params import InvestmentParams
from app.models.loan_params import LoanParams
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.ref import AccountType
from app.models.salary_profile import SalaryProfile
from app.models.savings_goal import SavingsGoal
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import (
    amortization_engine,
    balance_calculator,
    growth_engine,
    pay_period_service,
    savings_goal_service,
)
from app.services.investment_projection import calculate_investment_inputs

logger = logging.getLogger(__name__)


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
        .filter(
            Transaction.transfer_id.isnot(None),
            Transaction.transaction_type_id == income_type_id,
            Transaction.pay_period_id.in_(period_ids),
            Transaction.scenario_id == scenario.id,
            Transaction.is_deleted.is_(False),
        )
        .all()
    ) if scenario and period_ids else []

    # ── Load account-type-specific parameters ───────────────────
    params = _load_account_params(user_id, accounts)

    # ── Compute per-account projections ─────────────────────────
    account_data = _compute_account_projections(
        accounts, all_transactions, all_shadow_income, all_periods,
        current_period, params,
    )

    # ── Savings goals ───────────────────────────────────────────
    goal_data = _compute_goal_progress(user_id, account_data, all_periods)

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

    return {
        "account_data": account_data,
        "grouped_accounts": grouped_accounts,
        "goal_data": goal_data,
        "emergency_metrics": emergency_metrics,
        "total_savings": total_savings,
        "avg_monthly_expenses": avg_monthly_expenses,
        "savings_accounts": savings_accounts,
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

    return {
        "interest_params_map": interest_params_map,
        "investment_params_map": investment_params_map,
        "deductions_by_account": deductions_by_account,
        "salary_gross_biweekly": salary_gross_biweekly,
        "loan_params_map": loan_params_map,
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
    current_balance, projected, needs_setup, and optional type-specific
    params.
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
            proj = amortization_engine.get_loan_projection(acct_loan_params)
            current_bal = Decimal(str(acct_loan_params.current_principal))
            monthly = proj.summary.monthly_payment
            summary = proj.summary
            for label, month_offset in [("3 months", 3), ("6 months", 6), ("1 year", 12)]:
                if month_offset <= len(proj.schedule):
                    projected[label] = proj.schedule[month_offset - 1].remaining_balance
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

        ad = {
            "account": acct,
            "current_balance": current_bal,
            "projected": projected,
            "needs_setup": needs_setup,
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


def _project_investment(
    acct, investment_params, params, all_shadow_income,
    all_periods, current_period, current_bal,
):
    """Compute growth projections for an investment/retirement account."""
    acct_deductions = params["deductions_by_account"].get(acct.id, [])
    adapted_deductions = []
    for ded in acct_deductions:
        profile = ded.salary_profile
        adapted_deductions.append(type("D", (), {
            "amount": ded.amount,
            "calc_method_id": ded.calc_method_id,
            "annual_salary": profile.annual_salary,
            "pay_periods_per_year": profile.pay_periods_per_year or 26,
        })())

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


def _compute_goal_progress(user_id, account_data, all_periods):
    """Compute savings goal progress and required contributions.

    Returns a list of dicts with goal, current_balance, progress_pct,
    remaining_periods, and required_contribution.
    """
    goals = (
        db.session.query(SavingsGoal)
        .filter_by(user_id=user_id, is_active=True)
        .all()
    )

    goal_data = []
    for goal in goals:
        acct_balance = Decimal("0.00")
        for ad in account_data:
            if ad["account"].id == goal.account_id:
                acct_balance = ad["current_balance"] or Decimal("0.00")
                break

        remaining_periods = savings_goal_service.count_periods_until(
            goal.target_date, all_periods
        )
        required = savings_goal_service.calculate_required_contribution(
            acct_balance, goal.target_amount, remaining_periods,
        )

        progress_pct = 0
        if goal.target_amount and goal.target_amount > 0:
            progress_pct = min(
                100,
                int(acct_balance / goal.target_amount * 100),
            )

        goal_data.append({
            "goal": goal,
            "current_balance": acct_balance,
            "progress_pct": progress_pct,
            "remaining_periods": remaining_periods,
            "required_contribution": required,
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
