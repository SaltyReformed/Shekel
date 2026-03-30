"""
Shekel Budget App -- Charts Routes

Centralized Charts dashboard with HTMX-loaded chart fragments.
Each fragment endpoint returns an HTML partial with chart data
encoded in data-* attributes for CSP-compliant Chart.js rendering.
"""

import logging

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError

from app.services import chart_data_service

logger = logging.getLogger(__name__)

charts_bp = Blueprint("charts", __name__)


def _error_fragment(retry_url=None):
    """Render the error fragment template.

    Args:
        retry_url: Optional URL for the retry button.

    Returns:
        Rendered HTML error fragment.
    """
    return render_template("charts/_error.html", retry_url=retry_url)


@charts_bp.route("/charts")
@login_required
def dashboard():
    """Charts dashboard page with progressively loaded chart cards."""
    return render_template("charts/dashboard.html")


@charts_bp.route("/charts/balance-over-time")
@login_required
def balance_over_time():
    """HTMX fragment: balance over time chart with data."""
    if not request.headers.get("HX-Request"):
        return redirect(url_for("charts.dashboard"))

    try:
        account_ids = request.args.getlist("account_id", type=int)
        start = request.args.get("start")
        end = request.args.get("end")

        chart_data = chart_data_service.get_balance_over_time(
            user_id=current_user.id,
            account_ids=account_ids,
            start=start,
            end=end,
        )

        return render_template(
            "charts/_balance_over_time.html",
            chart_data=chart_data,
        )
    except (ValueError, KeyError, SQLAlchemyError):
        logger.exception("Error loading balance over time chart")
        return _error_fragment(url_for("charts.balance_over_time"))


@charts_bp.route("/charts/spending-by-category")
@login_required
def spending_by_category():
    """HTMX fragment: spending by category horizontal bar chart."""
    if not request.headers.get("HX-Request"):
        return redirect(url_for("charts.dashboard"))

    try:
        period_range = request.args.get("range", "current")

        chart_data = chart_data_service.get_spending_by_category(
            user_id=current_user.id,
            period_range=period_range,
        )

        return render_template(
            "charts/_spending_category.html",
            chart_data=chart_data,
            selected_range=period_range,
        )
    except (ValueError, KeyError, SQLAlchemyError):
        logger.exception("Error loading spending by category chart")
        return _error_fragment(url_for("charts.spending_by_category"))


@charts_bp.route("/charts/budget-vs-actuals")
@login_required
def budget_vs_actuals():
    """HTMX fragment: budget vs. actuals grouped bar chart."""
    if not request.headers.get("HX-Request"):
        return redirect(url_for("charts.dashboard"))

    try:
        period_range = request.args.get("range", "current")

        chart_data = chart_data_service.get_budget_vs_actuals(
            user_id=current_user.id,
            period_range=period_range,
        )

        return render_template(
            "charts/_budget_vs_actuals.html",
            chart_data=chart_data,
            selected_range=period_range,
        )
    except (ValueError, KeyError, SQLAlchemyError):
        logger.exception("Error loading budget vs actuals chart")
        return _error_fragment(url_for("charts.budget_vs_actuals"))


@charts_bp.route("/charts/amortization")
@login_required
def amortization():
    """HTMX fragment: amortization breakdown stacked area chart."""
    if not request.headers.get("HX-Request"):
        return redirect(url_for("charts.dashboard"))

    try:
        account_id = request.args.get("account_id", type=int)

        chart_data = chart_data_service.get_amortization_breakdown(
            user_id=current_user.id,
            account_id=account_id,
        )

        loan_accounts = chart_data_service.get_loan_accounts(
            user_id=current_user.id,
        )

        return render_template(
            "charts/_amortization.html",
            chart_data=chart_data,
            loan_accounts=loan_accounts,
            selected_account_id=account_id,
        )
    except (ValueError, KeyError, SQLAlchemyError):
        logger.exception("Error loading amortization chart")
        return _error_fragment(url_for("charts.amortization"))


@charts_bp.route("/charts/net-worth")
@login_required
def net_worth():
    """HTMX fragment: net worth over time line chart."""
    if not request.headers.get("HX-Request"):
        return redirect(url_for("charts.dashboard"))

    try:
        start = request.args.get("start")
        end = request.args.get("end")

        chart_data = chart_data_service.get_net_worth_over_time(
            user_id=current_user.id,
            start=start,
            end=end,
        )

        return render_template(
            "charts/_net_worth.html",
            chart_data=chart_data,
        )
    except (ValueError, KeyError, SQLAlchemyError):
        logger.exception("Error loading net worth chart")
        return _error_fragment(url_for("charts.net_worth"))


@charts_bp.route("/charts/net-pay")
@login_required
def net_pay():
    """HTMX fragment: net pay trajectory step line chart."""
    if not request.headers.get("HX-Request"):
        return redirect(url_for("charts.dashboard"))

    try:
        profile_id = request.args.get("profile_id", type=int)

        chart_data = chart_data_service.get_net_pay_trajectory(
            user_id=current_user.id,
            profile_id=profile_id,
        )

        salary_profiles = chart_data_service.get_salary_profiles(
            user_id=current_user.id,
        )

        return render_template(
            "charts/_net_pay.html",
            chart_data=chart_data,
            salary_profiles=salary_profiles,
            selected_profile_id=profile_id,
        )
    except (ValueError, KeyError, SQLAlchemyError):
        logger.exception("Error loading net pay chart")
        return _error_fragment(url_for("charts.net_pay"))
