"""
Shekel Budget App -- Savings Routes

Dashboard showing account balances, savings goals with progress tracking,
and emergency fund metrics.  Goal CRUD endpoints for creating, editing,
and deleting savings goals.
"""

import json
import logging
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from app.utils.auth_helpers import get_or_404, require_owner
from app import ref_cache
from app.enums import GoalModeEnum
from app.extensions import db
from app.models.account import Account
from app.models.ref import GoalMode, IncomeUnit
from app.models.savings_goal import SavingsGoal
from app.routes._commit_helpers import (
    StaleConflictContext,
    commit_or_handle_stale,
)
from app.routes._recurrence_form_helpers import (
    STALE_EDITING_MESSAGE,
    handle_stale_form_conflict,
)
from app.routes._redirect_target import RedirectTarget
from app.schemas.validation import SavingsGoalCreateSchema, SavingsGoalUpdateSchema
from app.services import account_service, savings_dashboard_service

logger = logging.getLogger(__name__)

savings_bp = Blueprint("savings", __name__)

# Chart.js x-axis label format for the net-worth trend: month abbreviation
# plus un-padded day (e.g. "Jun 5"), matching the dashboard pulse chart.
_NET_WORTH_LABEL_FORMAT = "%b %-d"

_create_schema = SavingsGoalCreateSchema()
_update_schema = SavingsGoalUpdateSchema()


def _serialize_net_worth_chart(net_worth_series: dict) -> str:
    """Serialize the net-worth trend series to a Chart.js JSON string.

    The single Chart.js serialization boundary for the cockpit's
    net-worth region (coding-standards: ``float`` lives only here, never
    in a calculation).  Maps the producer's parallel ``Decimal`` lists
    (``net`` / ``assets`` / ``liabilities``, computed money-precise in
    :mod:`app.services.savings_dashboard_service._net_worth`) to ``float``
    arrays and the period descriptors' ``end_date`` to ``%b %-d`` labels.

    ``actual_count`` is the number of leading points whose period has
    already ended (``end_date <= today``): the template uses it to render
    those points as realized history and the remainder as projection,
    the same actual-vs-projected split the dashboard pulse chart draws.

    Args:
        net_worth_series: The ``net_worth["series"]`` dict, with keys
            ``periods`` (list of ``{end_date, period_index}``), ``net``,
            ``assets``, and ``liabilities``.

    Returns:
        A JSON string ``{"labels": [str], "net": [float], "assets":
        [float], "liabilities": [float], "actual_count": int}`` for the
        ``data-chart`` attribute.
    """
    today = date.today()
    periods = net_worth_series["periods"]
    actual_count = sum(
        1 for point in periods if point["end_date"] <= today
    )
    return json.dumps({
        "labels": [
            point["end_date"].strftime(_NET_WORTH_LABEL_FORMAT)
            for point in periods
        ],
        "net": [float(value) for value in net_worth_series["net"]],
        "assets": [float(value) for value in net_worth_series["assets"]],
        "liabilities": [
            float(value) for value in net_worth_series["liabilities"]
        ],
        "actual_count": actual_count,
    })

# Fields allowed in goal updates.  Income-relative fields are included
# so mode changes propagate correctly.
_GOAL_UPDATE_FIELDS = frozenset({
    "name", "target_amount", "target_date", "contribution_per_period",
    "account_id", "is_active", "goal_mode_id", "income_unit_id",
    "income_multiplier",
})


def _goal_form_context(goal=None):
    """Build common template context for the goal create/edit form.

    Loads the account list, goal mode ref table, and income unit ref
    table that the form dropdowns need.

    Args:
        goal: An existing SavingsGoal for edit mode, or None for create.

    Returns:
        dict with keys: goal, accounts, goal_modes, income_units.
    """
    accounts = account_service.list_active_accounts(current_user.id)
    goal_modes = GoalMode.query.order_by(GoalMode.id).all()
    income_units = IncomeUnit.query.order_by(IncomeUnit.id).all()
    return {
        "goal": goal,
        "accounts": accounts,
        "goal_modes": goal_modes,
        "income_units": income_units,
    }


def _clean_goal_form_data(form_data):
    """Strip stale hidden-field values from goal form submissions.

    When the user toggles between Fixed and Income-Relative mode, the
    hidden field group still submits its old values.  This function
    returns a cleaned dict suitable for schema validation -- removing
    income fields for Fixed mode and target_amount for Income-Relative.

    Must run BEFORE schema validation so the cross-field validator does
    not reject the stale combination.

    Args:
        form_data: The ImmutableMultiDict from request.form.

    Returns:
        dict with stale fields removed.
    """
    data = dict(form_data)
    fixed_id = ref_cache.goal_mode_id(GoalModeEnum.FIXED)

    # Default to Fixed when omitted (backward compatibility).
    mode_str = data.get("goal_mode_id", str(fixed_id))
    try:
        mode = int(mode_str)
    except (ValueError, TypeError):
        return data

    if mode == fixed_id:
        data.pop("income_unit_id", None)
        data.pop("income_multiplier", None)
    else:
        data.pop("target_amount", None)

    return data


def _cockpit_context(user_id: int) -> dict:
    """Build the cockpit render context: dashboard data + the chart JSON.

    The single producer + serialization prologue shared by the full-page
    ``dashboard`` render and the ``cockpit_section`` partial re-render, so
    both feed the template the identical contract (the money-precise
    ``net_worth`` figures plus the ``net_worth_chart_json`` the trend
    canvas reads).  ``float`` is applied only in
    :func:`_serialize_net_worth_chart`, the Chart.js boundary; every other
    figure stays ``Decimal``.

    Args:
        user_id: Integer ID of the current user.

    Returns:
        The ``compute_dashboard_data`` dict with ``net_worth_chart_json``
        added.
    """
    ctx = savings_dashboard_service.compute_dashboard_data(user_id)
    ctx["net_worth_chart_json"] = _serialize_net_worth_chart(
        ctx["net_worth"]["series"]
    )
    return ctx


@savings_bp.route("/savings")
@login_required
@require_owner
def dashboard():
    """Savings dashboard: the Net Worth Cockpit, goals, and emergency fund.

    Renders the full page.  The cockpit region (net-worth hero, the
    account grid, and the home-equity cards) is wrapped in
    ``#cockpit-section`` and re-renders on ``balanceChanged`` via
    :func:`cockpit_section`; the savings goals, emergency-fund coverage,
    and archived list below it are page-load-only.  The shared context
    (including the serialized ``net_worth_chart_json``) comes from
    :func:`_cockpit_context`.
    """
    return render_template(
        "savings/dashboard.html", **_cockpit_context(current_user.id),
    )


@savings_bp.route("/savings/cockpit")
@login_required
@require_owner
def cockpit_section():
    """HTMX partial: re-render the Net Worth Cockpit region on balanceChanged.

    The single ``balanceChanged from:body`` swap target for the cockpit's
    ``#cockpit-section`` (the net-worth hero + chips + trend, the account
    grid with its group subtotals and the debt summary, and the
    home-equity cards), so an inline balance edit re-syncs every
    balance-derived figure in that region at once.  Re-renders
    ``savings/_cockpit.html`` with the same :func:`_cockpit_context` the
    page uses, so the swapped-in markup reads the identical contract.

    Non-HTMX requests redirect to the dashboard page (the section is a
    fragment, not a standalone page), matching ``dashboard.pulse_section``.
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("savings.dashboard"))

    return render_template(
        "savings/_cockpit.html", **_cockpit_context(current_user.id),
    )


@savings_bp.route("/savings/cockpit/<int:account_id>/balance")
@login_required
@require_owner
def cockpit_balance(account_id):
    """HTMX partial: re-render one account's cockpit balance cell.

    The Cancel / Escape (and 409-conflict retry) revert target for the
    cockpit's per-card inline anchor editor: ``accounts._anchor_revert_url``
    maps the editor's ``revert=accounts`` token here, mirroring how
    ``revert=dashboard`` maps to ``dashboard.balance_section``.  Renders
    ``savings/_cockpit_balance.html`` -- the ``#acct-balance-<id>`` cell the
    editor replaced -- with the resolver ``current_balance`` from the
    narrow :func:`~app.services.savings_dashboard_service.compute_account_balance_cell`
    producer, so the reverted cell shows the exact figure the grid showed.

    The producer is the IDOR + active gate (as ``balance_section``'s
    producer is for the dashboard): it returns ``None`` -- a 404 -- for an
    account that is not among the user's active accounts (not found, not
    owned, or archived between page load and the revert), satisfying the
    404-for-both security rule.  Non-HTMX requests redirect to the
    dashboard page.
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("savings.dashboard"))

    cell = savings_dashboard_service.compute_account_balance_cell(
        current_user.id, account_id,
    )
    if cell is None:
        abort(404)

    return render_template("savings/_cockpit_balance.html", **cell)


@savings_bp.route("/savings/goals/new", methods=["GET"])
@login_required
@require_owner
def new_goal():
    """Display the savings goal creation form."""
    return render_template("savings/goal_form.html", **_goal_form_context())


@savings_bp.route("/savings/goals", methods=["POST"])
@login_required
@require_owner
def create_goal():
    """Create a new savings goal."""
    cleaned = _clean_goal_form_data(request.form)
    errors = _create_schema.validate(cleaned)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("savings.new_goal"))

    data = _create_schema.load(cleaned)

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
@require_owner
def edit_goal(goal_id):
    """Display the savings goal edit form."""
    goal = get_or_404(SavingsGoal, goal_id)
    if goal is None:
        abort(404)

    return render_template(
        "savings/goal_form.html", **_goal_form_context(goal),
    )


@savings_bp.route("/savings/goals/<int:goal_id>", methods=["POST"])
@login_required
@require_owner
def update_goal(goal_id):
    """Update a savings goal.

    Optimistic locking (commit C-18 / F-010): the edit form ships
    ``version_id`` as a hidden input.  When the submitted value
    differs from the row's current counter, the handler short-
    circuits with a flash + redirect so the audit trail records
    only the winner.  ``StaleDataError`` raised at flush time --
    e.g. by a concurrent edit that races past the form-side check
    -- is caught and converted to the same flash + redirect.
    """
    goal = get_or_404(SavingsGoal, goal_id)
    if goal is None:
        abort(404)

    cleaned = _clean_goal_form_data(request.form)
    errors = _update_schema.validate(cleaned)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("savings.edit_goal", goal_id=goal_id))

    data = _update_schema.load(cleaned)

    # Stale-form check (commit C-18 / F-010).  One shared context drives
    # both the pre-flush form-side handler and the commit-time handler so
    # the log label, flash wording, and redirect target are defined once.
    submitted_version = data.pop("version_id", None)
    stale_ctx = StaleConflictContext(
        logger=logger,
        log_label="update_goal",
        log_id=goal_id,
        flash_message=STALE_EDITING_MESSAGE.format(noun="savings goal"),
        redirect=RedirectTarget("savings.edit_goal", {"goal_id": goal_id}),
    )
    if submitted_version is not None and submitted_version != goal.version_id:
        return handle_stale_form_conflict(
            stale_ctx,
            submitted=submitted_version,
            current=goal.version_id,
        )

    # Validate account ownership if account is being changed.
    if "account_id" in data:
        acct = db.session.get(Account, data["account_id"])
        if not acct or acct.user_id != current_user.id:
            flash("Invalid account.", "danger")
            return redirect(url_for("savings.edit_goal", goal_id=goal_id))

    # When switching modes, explicitly clear the now-irrelevant fields
    # so the update loop sets them to None on the goal object.
    if "goal_mode_id" in data:
        fixed_id = ref_cache.goal_mode_id(GoalModeEnum.FIXED)
        if data["goal_mode_id"] == fixed_id:
            data.setdefault("income_unit_id", None)
            data.setdefault("income_multiplier", None)
        else:
            data.setdefault("target_amount", None)

    for field, value in data.items():
        if field in _GOAL_UPDATE_FIELDS:
            setattr(goal, field, value)

    conflict = commit_or_handle_stale(stale_ctx)
    if conflict is not None:
        return conflict
    logger.info("user_id=%d updated savings goal %d", current_user.id, goal_id)
    flash(f"Savings goal '{goal.name}' updated.", "success")
    return redirect(url_for("savings.dashboard"))


@savings_bp.route("/savings/goals/<int:goal_id>/delete", methods=["POST"])
@login_required
@require_owner
def delete_goal(goal_id):
    """Deactivate a savings goal.

    Optimistic locking (commit C-18 / F-010): the
    ``is_active = False`` flush is version-pinned by SQLAlchemy.
    A concurrent edit raises ``StaleDataError`` which the handler
    converts into a flash + redirect.
    """
    goal = get_or_404(SavingsGoal, goal_id)
    if goal is None:
        abort(404)

    goal.is_active = False
    conflict = commit_or_handle_stale(StaleConflictContext(
        logger=logger,
        log_label="delete_goal",
        log_id=goal_id,
        flash_message=(
            "This savings goal was changed by another action.  "
            "Please reload and try again."
        ),
        redirect=RedirectTarget("savings.dashboard"),
    ))
    if conflict is not None:
        return conflict
    logger.info("user_id=%d deleted savings goal %d", current_user.id, goal_id)

    flash(f"Savings goal '{goal.name}' deactivated.", "info")
    return redirect(url_for("savings.dashboard"))
