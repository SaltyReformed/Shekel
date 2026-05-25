"""
Shekel Budget App -- Dashboard Routes

Summary dashboard displaying upcoming bills, alerts, balance,
payday info, savings goals, debt summary, and spending comparison.
Mark-paid actions all flow through ``transactions.mark_done``
(the canonical settlement endpoint); the dashboard is read-only
for transaction status.
"""

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.services import dashboard_service
from app.utils.auth_helpers import require_owner

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
@dashboard_bp.route("/dashboard")
@login_required
@require_owner
def page():
    """Render the summary dashboard with all 7 sections.

    Calls dashboard_service.compute_dashboard_data() and passes the
    result as template variables.
    """
    data = dashboard_service.compute_dashboard_data(current_user.id)
    return render_template("dashboard/dashboard.html", **data)


@dashboard_bp.route("/dashboard/bills")
@login_required
@require_owner
def bills_section():
    """HTMX partial: refresh the upcoming bills section.

    Non-HTMX requests redirect to the dashboard page.
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("dashboard.page"))

    data = dashboard_service.compute_dashboard_data(current_user.id)
    return render_template(
        "dashboard/_upcoming_bills.html",
        upcoming_bills=data["upcoming_bills"],
        current_period=data["current_period"],
    )


@dashboard_bp.route("/dashboard/balance")
@login_required
@require_owner
def balance_section():
    """HTMX partial: refresh the balance and runway section.

    Non-HTMX requests redirect to the dashboard page.
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("dashboard.page"))

    data = dashboard_service.compute_dashboard_data(current_user.id)
    return render_template(
        "dashboard/_balance_runway.html",
        balance_info=data["balance_info"],
    )
