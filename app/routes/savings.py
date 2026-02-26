"""
Shekel Budget App — Savings Routes

Dashboard showing account balances, savings goals with progress tracking,
and emergency fund metrics.
"""

import logging
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models.account import Account
from app.models.savings_goal import SavingsGoal
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.models.ref import AccountType
from app.schemas.validation import SavingsGoalCreateSchema, SavingsGoalUpdateSchema
from app.services import balance_calculator, pay_period_service, savings_goal_service

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

    # Compute projected balances for each account.
    account_data = []
    for acct in accounts:
        period_ids = [p.id for p in all_periods]

        transactions = (
            db.session.query(Transaction)
            .filter(
                Transaction.pay_period_id.in_(period_ids),
                Transaction.scenario_id == scenario.id,
                Transaction.is_deleted.is_(False),
            )
            .all()
        ) if scenario and period_ids else []

        transfers = (
            db.session.query(Transfer)
            .filter(
                Transfer.pay_period_id.in_(period_ids),
                Transfer.scenario_id == scenario.id,
                Transfer.is_deleted.is_(False),
            )
            .all()
        ) if scenario and period_ids else []

        anchor_balance = acct.current_anchor_balance or Decimal("0.00")
        anchor_period_id = acct.current_anchor_period_id or (
            current_period.id if current_period else None
        )

        balances = {}
        if anchor_period_id:
            balances = balance_calculator.calculate_balances(
                anchor_balance=anchor_balance,
                anchor_period_id=anchor_period_id,
                periods=all_periods,
                transactions=transactions,
                transfers=transfers,
                account_id=acct.id,
            )

        # Get projected balance at current period and a few future milestones.
        current_bal = balances.get(current_period.id) if current_period else anchor_balance
        projected = {}
        for offset_label, offset_count in [("3 months", 6), ("6 months", 13), ("1 year", 26)]:
            if current_period:
                target_idx = current_period.period_index + offset_count
                for p in all_periods:
                    if p.period_index == target_idx and p.id in balances:
                        projected[offset_label] = balances[p.id]
                        break

        account_data.append({
            "account": acct,
            "current_balance": current_bal,
            "projected": projected,
        })

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
                if txn.is_expense and txn.status and txn.status.name in ("done", "received", "projected"):
                    total_expenses += Decimal(str(txn.effective_amount))

            # Convert from biweekly to monthly: total / periods * 26/12
            num_periods = len(recent_periods)
            if num_periods > 0:
                per_period = total_expenses / num_periods
                avg_monthly_expenses = per_period * Decimal("26") / Decimal("12")

    # Sum savings balances for emergency fund calculation.
    savings_type = (
        db.session.query(AccountType)
        .filter_by(name="savings")
        .first()
    )
    total_savings = Decimal("0.00")
    for ad in account_data:
        if savings_type and ad["account"].account_type_id == savings_type.id:
            total_savings += ad["current_balance"] or Decimal("0.00")

    emergency_metrics = savings_goal_service.calculate_savings_metrics(
        total_savings, avg_monthly_expenses,
    )

    # Load savings accounts for the goal form dropdown.
    savings_accounts = [
        ad["account"] for ad in account_data
        if savings_type and ad["account"].account_type_id == savings_type.id
    ]

    return render_template(
        "savings/dashboard.html",
        account_data=account_data,
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

    goal = SavingsGoal(user_id=current_user.id, **data)
    db.session.add(goal)
    db.session.commit()

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

    for field, value in data.items():
        if hasattr(goal, field):
            setattr(goal, field, value)

    db.session.commit()
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

    flash(f"Savings goal '{goal.name}' deactivated.", "info")
    return redirect(url_for("savings.dashboard"))
