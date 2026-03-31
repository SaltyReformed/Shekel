"""
Shekel Budget App -- Savings Routes

Dashboard showing account balances, savings goals with progress tracking,
and emergency fund metrics.  Goal CRUD endpoints for creating, editing,
and deleting savings goals.
"""

import logging

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.account import Account
from app.models.savings_goal import SavingsGoal
from app.schemas.validation import SavingsGoalCreateSchema, SavingsGoalUpdateSchema
from app.services import savings_dashboard_service

logger = logging.getLogger(__name__)

savings_bp = Blueprint("savings", __name__)

_create_schema = SavingsGoalCreateSchema()
_update_schema = SavingsGoalUpdateSchema()


@savings_bp.route("/savings")
@login_required
def dashboard():
    """Savings dashboard: account balances, goals, and emergency fund metrics."""
    ctx = savings_dashboard_service.compute_dashboard_data(current_user.id)
    return render_template("savings/dashboard.html", **ctx)


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
