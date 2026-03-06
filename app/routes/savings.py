"""
Shekel Budget App — Savings Routes

Dashboard showing account balances, savings goals with progress tracking,
and emergency fund metrics.
"""

import logging
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from collections import OrderedDict

from app.extensions import db
from app.models.account import Account
from app.models.auto_loan_params import AutoLoanParams
from app.models.hysa_params import HysaParams
from app.models.investment_params import InvestmentParams
from app.models.mortgage_params import MortgageParams
from app.models.savings_goal import SavingsGoal
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.ref import AccountType
from app.schemas.validation import SavingsGoalCreateSchema, SavingsGoalUpdateSchema
from app.services import amortization_engine, balance_calculator, pay_period_service, savings_goal_service
from app.services.account_resolver import resolve_grid_account

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

    # Load all transactions and transfers once, then filter per-account.
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

    all_transfers = (
        db.session.query(Transfer)
        .filter(
            Transfer.pay_period_id.in_(period_ids),
            Transfer.scenario_id == scenario.id,
            Transfer.is_deleted.is_(False),
        )
        .all()
    ) if scenario and period_ids else []

    # Map template_id → account_id so we can filter transactions per-account
    # without N+1 queries through the template relationship.
    template_account_map = dict(
        db.session.query(TransactionTemplate.id, TransactionTemplate.account_id)
        .filter_by(user_id=user_id)
        .all()
    ) if scenario else {}

    # The grid uses a resolved account for its balance view.  Ad-hoc
    # transactions (no template) are created in that context, so
    # attribute them to the same account.
    grid_account = resolve_grid_account(user_id, current_user.settings)
    grid_account_id = grid_account.id if grid_account else None

    # Load HYSA params for all HYSA accounts in one query.
    hysa_type = (
        db.session.query(AccountType).filter_by(name="hysa").first()
    )
    hysa_params_map = {}
    if hysa_type:
        hysa_account_ids = [a.id for a in accounts if a.account_type_id == hysa_type.id]
        if hysa_account_ids:
            for hp in db.session.query(HysaParams).filter(
                HysaParams.account_id.in_(hysa_account_ids)
            ).all():
                hysa_params_map[hp.account_id] = hp

    # Load loan params for mortgage and auto loan accounts.
    mortgage_type = db.session.query(AccountType).filter_by(name="mortgage").first()
    auto_loan_type = db.session.query(AccountType).filter_by(name="auto_loan").first()

    # Load investment params for retirement/investment accounts.
    investment_params_map = {}
    retirement_types = [
        db.session.query(AccountType).filter_by(name=n).first()
        for n in ("401k", "roth_401k", "traditional_ira", "roth_ira", "brokerage")
    ]
    retirement_type_ids = {rt.id for rt in retirement_types if rt}
    if retirement_type_ids:
        inv_account_ids = [
            a.id for a in accounts if a.account_type_id in retirement_type_ids
        ]
        if inv_account_ids:
            for ip in db.session.query(InvestmentParams).filter(
                InvestmentParams.account_id.in_(inv_account_ids)
            ).all():
                investment_params_map[ip.account_id] = ip

    loan_params_map = {}
    if mortgage_type:
        mortgage_ids = [a.id for a in accounts if a.account_type_id == mortgage_type.id]
        if mortgage_ids:
            for mp in db.session.query(MortgageParams).filter(
                MortgageParams.account_id.in_(mortgage_ids)
            ).all():
                loan_params_map[mp.account_id] = mp
    if auto_loan_type:
        auto_loan_ids = [a.id for a in accounts if a.account_type_id == auto_loan_type.id]
        if auto_loan_ids:
            for alp in db.session.query(AutoLoanParams).filter(
                AutoLoanParams.account_id.in_(auto_loan_ids)
            ).all():
                loan_params_map[alp.account_id] = alp

    # Compute projected balances for each account.
    account_data = []
    for acct in accounts:
        # Include transactions belonging to this account (via template).
        # Ad-hoc transactions (no template_id) are attributed to the grid
        # account since they were created in that context.
        acct_transactions = [
            txn for txn in all_transactions
            if (txn.template_id
                and template_account_map.get(txn.template_id) == acct.id)
            or (not txn.template_id and acct.id == grid_account_id)
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
                    transfers=all_transfers,
                    account_id=acct.id,
                    hysa_params=acct_hysa_params,
                )
            else:
                balances = balance_calculator.calculate_balances(
                    anchor_balance=anchor_balance,
                    anchor_period_id=anchor_period_id,
                    periods=all_periods,
                    transactions=acct_transactions,
                    transfers=all_transfers,
                    account_id=acct.id,
                )

        # Get projected balance at current period and a few future milestones.
        acct_loan_params = loan_params_map.get(acct.id)
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

        acct_investment_params = investment_params_map.get(acct.id)
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
                if txn.is_expense and txn.status and txn.status.name in ("done", "received"):
                    total_expenses += Decimal(str(txn.effective_amount))

            # Convert from biweekly to monthly: total / periods * 26/12
            num_periods = len(recent_periods)
            if num_periods > 0:
                per_period = total_expenses / num_periods
                avg_monthly_expenses = per_period * Decimal("26") / Decimal("12")

    # Sum savings + HYSA balances for emergency fund calculation.
    savings_type = (
        db.session.query(AccountType)
        .filter_by(name="savings")
        .first()
    )
    savings_type_ids = set()
    if savings_type:
        savings_type_ids.add(savings_type.id)
    if hysa_type:
        savings_type_ids.add(hysa_type.id)

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
    category_order = ["asset", "liability", "retirement", "investment"]
    grouped_accounts = OrderedDict()
    for cat in category_order:
        cat_accounts = [
            ad for ad in account_data
            if ad["account"].account_type
            and (ad["account"].account_type.category or "").lower() == cat
        ]
        if cat_accounts:
            grouped_accounts[cat] = cat_accounts
    # Catch any accounts without a category.
    uncategorized = [
        ad for ad in account_data
        if not ad["account"].account_type
        or not ad["account"].account_type.category
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
        flash(f"Validation error: {errors}", "danger")
        return redirect(url_for("savings.new_goal"))

    data = _create_schema.load(request.form)

    # Validate account ownership.
    acct = db.session.get(Account, data.get("account_id"))
    if not acct or acct.user_id != current_user.id:
        flash("Invalid account.", "danger")
        return redirect(url_for("savings.new_goal"))

    goal = SavingsGoal(user_id=current_user.id, **data)
    db.session.add(goal)
    db.session.commit()
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
        flash(f"Validation error: {errors}", "danger")
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
