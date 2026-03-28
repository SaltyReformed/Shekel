"""
Shekel Budget App -- Savings Routes

Dashboard showing account balances, savings goals with progress tracking,
and emergency fund metrics.
"""

import logging
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from collections import OrderedDict

from app import ref_cache
from app.enums import AcctTypeEnum, AcctCategoryEnum, TxnTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.auto_loan_params import AutoLoanParams
from app.models.hysa_params import HysaParams
from app.models.investment_params import InvestmentParams
from app.models.mortgage_params import MortgageParams
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.salary_profile import SalaryProfile
from app.models.savings_goal import SavingsGoal
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.schemas.validation import SavingsGoalCreateSchema, SavingsGoalUpdateSchema
from app.services import amortization_engine, balance_calculator, growth_engine, pay_period_service, savings_goal_service
from app.services.investment_projection import calculate_investment_inputs

logger = logging.getLogger(__name__)

savings_bp = Blueprint("savings", __name__)

_create_schema = SavingsGoalCreateSchema()
_update_schema = SavingsGoalUpdateSchema()


@savings_bp.route("/savings")
@login_required
def dashboard():
    """Savings dashboard: account balances, goals, and emergency fund metrics."""
    user_id = current_user.id

    # Load all accounts.
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

    # Load all transactions once, then filter per-account using the
    # account_id column.  This includes shadow transactions (transfer_id
    # IS NOT NULL) which represent deposits/withdrawals from transfers.
    # Without these, non-checking account balances silently exclude all
    # transfer effects -- a $500 HYSA deposit would be invisible.
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

    # Load shadow income transactions for investment contribution
    # calculations.  Queried once and filtered per-account in the loop
    # below (matching investment.py's per-account scoping pattern).
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

    # Load HYSA params for all HYSA accounts in one query.
    hysa_type_id = ref_cache.acct_type_id(AcctTypeEnum.HYSA)
    hysa_params_map = {}
    hysa_account_ids = [a.id for a in accounts if a.account_type_id == hysa_type_id]
    if hysa_account_ids:
        for hp in db.session.query(HysaParams).filter(
            HysaParams.account_id.in_(hysa_account_ids)
        ).all():
            hysa_params_map[hp.account_id] = hp

    # Load loan params for mortgage and auto loan accounts.
    mortgage_type_id = ref_cache.acct_type_id(AcctTypeEnum.MORTGAGE)
    auto_loan_type_id = ref_cache.acct_type_id(AcctTypeEnum.AUTO_LOAN)

    # Load investment params for retirement/investment accounts.
    investment_params_map = {}
    retirement_type_ids = {
        ref_cache.acct_type_id(AcctTypeEnum.K401),
        ref_cache.acct_type_id(AcctTypeEnum.ROTH_401K),
        ref_cache.acct_type_id(AcctTypeEnum.TRADITIONAL_IRA),
        ref_cache.acct_type_id(AcctTypeEnum.ROTH_IRA),
        ref_cache.acct_type_id(AcctTypeEnum.BROKERAGE),
    }
    if retirement_type_ids:
        inv_account_ids = [
            a.id for a in accounts if a.account_type_id in retirement_type_ids
        ]
        if inv_account_ids:
            for ip in db.session.query(InvestmentParams).filter(
                InvestmentParams.account_id.in_(inv_account_ids)
            ).all():
                investment_params_map[ip.account_id] = ip

    # Batch-load paycheck deductions targeting investment accounts.
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

    # Load active salary profile for employer contribution gross calculation.
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
    mortgage_ids = [a.id for a in accounts if a.account_type_id == mortgage_type_id]
    if mortgage_ids:
        for mp in db.session.query(MortgageParams).filter(
            MortgageParams.account_id.in_(mortgage_ids)
        ).all():
            loan_params_map[mp.account_id] = mp
    auto_loan_ids = [a.id for a in accounts if a.account_type_id == auto_loan_type_id]
    if auto_loan_ids:
        for alp in db.session.query(AutoLoanParams).filter(
            AutoLoanParams.account_id.in_(auto_loan_ids)
        ).all():
            loan_params_map[alp.account_id] = alp

    # Compute projected balances for each account.
    account_data = []
    for acct in accounts:
        # Filter transactions by the account_id column (added in Task 1
        # of the transfer rework).  This includes shadow transactions
        # from transfers, ad-hoc transactions, and template-generated
        # transactions -- all of which have account_id set at creation.
        acct_transactions = [
            txn for txn in all_transactions
            if txn.account_id == acct.id
        ]

        anchor_balance = acct.current_anchor_balance or Decimal("0.00")
        anchor_period_id = acct.current_anchor_period_id or (
            current_period.id if current_period else None
        )

        balances = {}
        interest_by_period = {}
        acct_hysa_params = hysa_params_map.get(acct.id)

        if anchor_period_id:
            if acct_hysa_params:
                balances, interest_by_period = balance_calculator.calculate_balances_with_interest(
                    anchor_balance=anchor_balance,
                    anchor_period_id=anchor_period_id,
                    periods=all_periods,
                    transactions=acct_transactions,
                    hysa_params=acct_hysa_params,
                )
            else:
                balances, _ = balance_calculator.calculate_balances(
                    anchor_balance=anchor_balance,
                    anchor_period_id=anchor_period_id,
                    periods=all_periods,
                    transactions=acct_transactions,
                )

        # Get projected balance at current period and a few future milestones.
        acct_loan_params = loan_params_map.get(acct.id)
        acct_investment_params = investment_params_map.get(acct.id)
        current_bal = balances.get(current_period.id) if current_period else anchor_balance
        projected = {}

        if acct_loan_params:
            # Debt accounts: use amortization schedule for projections instead
            # of the generic balance calculator (which treats payments as deposits).
            from datetime import date as _date
            from decimal import Decimal as D
            remaining = amortization_engine.calculate_remaining_months(
                acct_loan_params.origination_date,
                acct_loan_params.term_months,
            )
            principal = D(str(acct_loan_params.current_principal))
            rate = D(str(acct_loan_params.interest_rate))
            current_bal = principal
            monthly = amortization_engine.calculate_monthly_payment(
                principal, rate, remaining,
            )
            schedule = amortization_engine.generate_schedule(
                principal, rate, remaining,
                payment_day=acct_loan_params.payment_day,
            )
            for label, month_offset in [("3 months", 3), ("6 months", 6), ("1 year", 12)]:
                if month_offset <= len(schedule):
                    projected[label] = schedule[month_offset - 1].remaining_balance
            summary = amortization_engine.calculate_summary(
                principal, rate, remaining,
                _date.today().replace(day=1),
                acct_loan_params.payment_day,
                acct_loan_params.term_months,
            )
        elif acct_investment_params and current_period:
            # Investment/retirement: full growth projection with contributions.
            acct_deductions = deductions_by_account.get(acct.id, [])
            adapted_deductions = []
            for ded in acct_deductions:
                profile = ded.salary_profile
                adapted_deductions.append(type("D", (), {
                    "amount": ded.amount,
                    "calc_method_name": ded.calc_method.name if ded.calc_method else "flat",
                    "annual_salary": profile.annual_salary,
                    "pay_periods_per_year": profile.pay_periods_per_year or 26,
                })())

            # Filter contributions to this specific account (matching
            # investment.py's per-account scoping pattern).  Without
            # this, contributions from ALL investment accounts would be
            # mixed together, overstating each account's contribution.
            acct_contributions = [
                t for t in all_shadow_income
                if t.account_id == acct.id
            ]

            inputs = calculate_investment_inputs(
                account_id=acct.id,
                investment_params=acct_investment_params,
                deductions=adapted_deductions,
                all_contributions=acct_contributions,
                all_periods=all_periods,
                current_period=current_period,
                salary_gross_biweekly=salary_gross_biweekly,
            )

            future_periods = [
                p for p in all_periods
                if p.period_index >= current_period.period_index
            ]
            if future_periods:
                # Use the balance-calculator-computed current_bal
                # (which includes shadow transactions from transfers),
                # not the raw anchor_balance.
                projection = growth_engine.project_balance(
                    current_balance=current_bal,
                    assumed_annual_return=acct_investment_params.assumed_annual_return,
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
        else:
            for offset_label, offset_count in [("3 months", 6), ("6 months", 13), ("1 year", 26)]:
                if current_period:
                    target_idx = current_period.period_index + offset_count
                    for p in all_periods:
                        if p.period_index == target_idx and p.id in balances:
                            projected[offset_label] = balances[p.id]
                            break

        ad = {
            "account": acct,
            "current_balance": current_bal,
            "projected": projected,
        }
        if acct_hysa_params:
            ad["hysa_params"] = acct_hysa_params

        if acct_investment_params:
            ad["investment_params"] = acct_investment_params

        if acct_loan_params:
            ad["loan_params"] = acct_loan_params
            ad["monthly_payment"] = monthly
            ad["payoff_date"] = summary.payoff_date

        account_data.append(ad)

    # Load savings goals.
    goals = (
        db.session.query(SavingsGoal)
        .filter_by(user_id=user_id, is_active=True)
        .all()
    )

    # Compute goal progress and required contributions.
    goal_data = []
    for goal in goals:
        # Find the account balance for this goal's account.
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

    # Emergency fund metrics.
    # Average monthly expenses from last 6 periods of actual data.
    avg_monthly_expenses = Decimal("0.00")
    if current_period and scenario:
        # Get the 6 most recent periods before or at current.
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

            # Convert from biweekly to monthly: total / periods * 26/12
            num_periods = len(recent_periods)
            if num_periods > 0:
                per_period = total_expenses / num_periods
                avg_monthly_expenses = per_period * Decimal("26") / Decimal("12")

    # Sum savings + HYSA balances for emergency fund calculation.
    savings_type_ids = {
        ref_cache.acct_type_id(AcctTypeEnum.SAVINGS),
        hysa_type_id,
    }

    total_savings = Decimal("0.00")
    for ad in account_data:
        if ad["account"].account_type_id in savings_type_ids:
            total_savings += ad["current_balance"] or Decimal("0.00")

    emergency_metrics = savings_goal_service.calculate_savings_metrics(
        total_savings, avg_monthly_expenses,
    )

    # Load savings/HYSA accounts for the goal form dropdown.
    savings_accounts = [
        ad["account"] for ad in account_data
        if ad["account"].account_type_id in savings_type_ids
    ]

    # Group accounts by category for the dashboard layout.
    # Desired order: Asset, Liability, Retirement, Investment.
    category_order = [
        ("asset", ref_cache.acct_category_id(AcctCategoryEnum.ASSET)),
        ("liability", ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY)),
        ("retirement", ref_cache.acct_category_id(AcctCategoryEnum.RETIREMENT)),
        ("investment", ref_cache.acct_category_id(AcctCategoryEnum.INVESTMENT)),
    ]
    grouped_accounts = OrderedDict()
    for cat_label, cat_id in category_order:
        cat_accounts = [
            ad for ad in account_data
            if ad["account"].account_type
            and ad["account"].account_type.category_id == cat_id
        ]
        if cat_accounts:
            grouped_accounts[cat_label] = cat_accounts
    # Catch any accounts without a category.
    uncategorized = [
        ad for ad in account_data
        if not ad["account"].account_type
        or not ad["account"].account_type.category_id
    ]
    if uncategorized:
        grouped_accounts["other"] = uncategorized

    return render_template(
        "savings/dashboard.html",
        account_data=account_data,
        grouped_accounts=grouped_accounts,
        goal_data=goal_data,
        emergency_metrics=emergency_metrics,
        total_savings=total_savings,
        avg_monthly_expenses=avg_monthly_expenses,
        savings_accounts=savings_accounts,
    )


@savings_bp.route("/savings/goals/new", methods=["GET"])
@login_required
def new_goal():
    """Display the savings goal creation form."""
    accounts = (
        db.session.query(Account)
        .filter_by(user_id=current_user.id, is_active=True)
        .order_by(Account.sort_order, Account.name)
        .all()
    )
    return render_template("savings/goal_form.html", goal=None, accounts=accounts)


@savings_bp.route("/savings/goals", methods=["POST"])
@login_required
def create_goal():
    """Create a new savings goal."""
    errors = _create_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("savings.new_goal"))

    data = _create_schema.load(request.form)

    # Validate account ownership and active status.
    acct = db.session.get(Account, data.get("account_id"))
    if not acct or acct.user_id != current_user.id or not acct.is_active:
        flash("Invalid account.", "danger")
        return redirect(url_for("savings.new_goal"))

    goal = SavingsGoal(user_id=current_user.id, **data)
    db.session.add(goal)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash(
            "A savings goal with that name already exists for this account.",
            "warning",
        )
        return redirect(url_for("savings.dashboard"))

    logger.info("user_id=%d created savings goal (id=%d)", current_user.id, goal.id)

    flash(f"Savings goal '{goal.name}' created.", "success")
    return redirect(url_for("savings.dashboard"))


@savings_bp.route("/savings/goals/<int:goal_id>/edit", methods=["GET"])
@login_required
def edit_goal(goal_id):
    """Display the savings goal edit form."""
    goal = db.session.get(SavingsGoal, goal_id)
    if goal is None or goal.user_id != current_user.id:
        flash("Goal not found.", "danger")
        return redirect(url_for("savings.dashboard"))

    accounts = (
        db.session.query(Account)
        .filter_by(user_id=current_user.id, is_active=True)
        .order_by(Account.sort_order, Account.name)
        .all()
    )
    return render_template("savings/goal_form.html", goal=goal, accounts=accounts)


@savings_bp.route("/savings/goals/<int:goal_id>", methods=["POST"])
@login_required
def update_goal(goal_id):
    """Update a savings goal."""
    goal = db.session.get(SavingsGoal, goal_id)
    if goal is None or goal.user_id != current_user.id:
        flash("Goal not found.", "danger")
        return redirect(url_for("savings.dashboard"))

    errors = _update_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("savings.edit_goal", goal_id=goal_id))

    data = _update_schema.load(request.form)

    # Validate account ownership if account is being changed.
    if "account_id" in data:
        acct = db.session.get(Account, data["account_id"])
        if not acct or acct.user_id != current_user.id:
            flash("Invalid account.", "danger")
            return redirect(url_for("savings.edit_goal", goal_id=goal_id))

    _GOAL_UPDATE_FIELDS = {"name", "target_amount", "target_date", "contribution_per_period", "account_id", "is_active"}
    for field, value in data.items():
        if field in _GOAL_UPDATE_FIELDS:
            setattr(goal, field, value)

    db.session.commit()
    logger.info("user_id=%d updated savings goal %d", current_user.id, goal_id)
    flash(f"Savings goal '{goal.name}' updated.", "success")
    return redirect(url_for("savings.dashboard"))


@savings_bp.route("/savings/goals/<int:goal_id>/delete", methods=["POST"])
@login_required
def delete_goal(goal_id):
    """Deactivate a savings goal."""
    goal = db.session.get(SavingsGoal, goal_id)
    if goal is None or goal.user_id != current_user.id:
        flash("Goal not found.", "danger")
        return redirect(url_for("savings.dashboard"))

    goal.is_active = False
    db.session.commit()
    logger.info("user_id=%d deleted savings goal %d", current_user.id, goal_id)

    flash(f"Savings goal '{goal.name}' deactivated.", "info")
    return redirect(url_for("savings.dashboard"))
