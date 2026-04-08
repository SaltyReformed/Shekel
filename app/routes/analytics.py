"""
Shekel Budget App -- Analytics Routes

Analytics dashboard with four HTMX lazy-loaded tabs:
Calendar, Year-End Summary, Budget Variance, and Spending Trends.
Each tab endpoint returns an HTML partial loaded into the main page
via nav-pills navigation.
"""

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import login_required

analytics_bp = Blueprint("analytics", __name__)


@analytics_bp.route("/analytics")
@login_required
def page():
    """Render the main analytics page with four lazy-loaded tab pills.

    The page contains a nav-pills bar with Calendar, Year-End,
    Variance, and Trends tabs.  The Calendar tab auto-loads on page
    visit via HTMX.  Other tabs load on click.
    """
    return render_template("analytics/analytics.html")


@analytics_bp.route("/analytics/calendar")
@login_required
def calendar_tab():
    """HTMX partial: calendar heatmap tab placeholder.

    Returns a placeholder fragment until the full calendar view is
    implemented.  Non-HTMX requests redirect to the main analytics
    page to prevent users from landing on a raw HTML fragment.
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("analytics.page"))
    return "<p class='text-muted'>Calendar -- coming soon.</p>"


@analytics_bp.route("/analytics/year-end")
@login_required
def year_end_tab():
    """HTMX partial: year-end summary tab placeholder.

    Returns a placeholder fragment until the full year-end summary
    is implemented.  Non-HTMX requests redirect to the main
    analytics page.
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("analytics.page"))
    return "<p class='text-muted'>Year-end summary -- coming soon.</p>"


@analytics_bp.route("/analytics/variance")
@login_required
def variance_tab():
    """HTMX partial: budget variance tab placeholder.

    Returns a placeholder fragment until the full variance analysis
    is implemented.  Non-HTMX requests redirect to the main
    analytics page.
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("analytics.page"))
    return "<p class='text-muted'>Budget variance -- coming soon.</p>"


@analytics_bp.route("/analytics/trends")
@login_required
def trends_tab():
    """HTMX partial: spending trends tab placeholder.

    Returns a placeholder fragment until the full trends view is
    implemented.  Non-HTMX requests redirect to the main analytics
    page.
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("analytics.page"))
    return "<p class='text-muted'>Spending trends -- coming soon.</p>"
