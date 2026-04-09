"""
Shekel Budget App -- Analytics Routes

Analytics dashboard with four HTMX lazy-loaded tabs:
Calendar, Year-End Summary, Budget Variance, and Spending Trends.
Each tab endpoint returns an HTML partial loaded into the main page
via nav-pills navigation.
"""

import calendar as cal_mod
from datetime import date
from decimal import Decimal

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from markupsafe import escape

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.user import UserSettings
from app.services import (
    budget_variance_service,
    calendar_service,
    pay_period_service,
    spending_trend_service,
    year_end_summary_service,
)

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
    """HTMX partial: calendar tab with month detail or year overview.

    Query parameters:
        view: 'month' (default) or 'year'.
        year: Calendar year (default: current year).
        month: Calendar month 1-12 (default: current month).
        account_id: Optional account filter.

    Non-HTMX requests redirect to the main analytics page.
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("analytics.page"))

    today = date.today()
    view = request.args.get("view", "month")
    year = request.args.get("year", today.year, type=int)
    month = request.args.get("month", today.month, type=int)
    account_id = request.args.get("account_id", None, type=int)

    # Clamp to valid ranges.
    year = max(2000, min(2100, year))
    month = max(1, min(12, month))

    settings = db.session.query(UserSettings).filter_by(
        user_id=current_user.id,
    ).first()
    threshold = settings.large_transaction_threshold if settings else 500

    if view == "year":
        return _render_year_view(year, account_id, threshold)
    return _render_month_view(year, month, account_id, threshold, today)


@analytics_bp.route("/analytics/year-end")
@login_required
def year_end_tab():
    """HTMX partial: year-end financial summary.

    Renders a structured annual report with income/tax breakdown,
    spending by category, transfers summary, net worth chart,
    debt progress, and savings progress.

    Query parameters:
        year: Calendar year (default: current year).

    Non-HTMX requests redirect to the main analytics page.
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("analytics.page"))

    today = date.today()
    year = request.args.get("year", today.year, type=int)
    year = max(2000, min(2100, year))

    data = year_end_summary_service.compute_year_end_summary(
        current_user.id, year,
    )

    # Build available years for the year selector.
    available_years = _get_available_years(current_user.id, today.year)

    return render_template(
        "analytics/_year_end.html",
        data=data,
        year=year,
        available_years=available_years,
    )


@analytics_bp.route("/analytics/variance")
@login_required
def variance_tab():
    """HTMX partial: budget variance analysis.

    Renders a grouped bar chart and drill-down table comparing
    estimated vs. actual amounts per category.

    Query parameters:
        window: 'pay_period' (default), 'month', or 'year'.
        period_id: Pay period ID (for pay_period window).
        month: Month number 1-12 (for month window).
        year: Calendar year (for month/year windows).

    Non-HTMX requests redirect to the main analytics page.
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("analytics.page"))

    today = date.today()
    window_type = request.args.get("window", "pay_period")
    if window_type not in ("pay_period", "month", "year"):
        window_type = "pay_period"

    period_id = request.args.get("period_id", type=int)
    month = request.args.get("month", type=int)
    year = request.args.get("year", type=int)

    # Apply defaults for missing params.
    if window_type == "pay_period" and period_id is None:
        current = pay_period_service.get_current_period(current_user.id)
        if current is None:
            all_p = pay_period_service.get_all_periods(current_user.id)
            current = all_p[-1] if all_p else None
        if current is not None:
            period_id = current.id
        else:
            # No periods exist -- fall back to month view.
            window_type = "month"
            month = today.month
            year = today.year
    if window_type == "month":
        if month is None:
            month = today.month
        if year is None:
            year = today.year
    if window_type == "year" and year is None:
        year = today.year

    report = budget_variance_service.compute_variance(
        user_id=current_user.id,
        window_type=window_type,
        period_id=period_id,
        month=month,
        year=year,
    )

    chart_data = _build_variance_chart_data(report)
    periods = pay_period_service.get_all_periods(current_user.id)
    available_years = _get_available_years(current_user.id, today.year)

    return render_template(
        "analytics/_variance.html",
        report=report,
        chart_data=chart_data,
        window_type=window_type,
        period_id=period_id,
        month=month,
        year=year,
        periods=periods,
        available_years=available_years,
    )


@analytics_bp.route("/analytics/trends")
@login_required
def trends_tab():
    """HTMX partial: spending trends analysis.

    Renders ranked lists of top-5 trending-up and trending-down
    categories, data sufficiency banners, group drill-down, and
    OP-3 payment timing data.

    Non-HTMX requests redirect to the main analytics page.
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("analytics.page"))

    settings = db.session.query(UserSettings).filter_by(
        user_id=current_user.id,
    ).first()
    threshold = (
        settings.trend_alert_threshold if settings
        else Decimal("0.1000")
    )

    report = spending_trend_service.compute_trends(
        user_id=current_user.id,
        threshold=threshold,
    )

    return render_template(
        "analytics/_trends.html",
        report=report,
    )


# ── Calendar helpers ────────────────────────────────────────────────


def _render_month_view(year, month, account_id, threshold, today):
    """Render the month detail calendar view.

    Builds a 7-column Sun-Sat calendar grid from the service data
    and pre-computes popover HTML for days with transactions.
    """
    data = calendar_service.get_month_detail(
        user_id=current_user.id,
        year=year,
        month=month,
        account_id=account_id,
        large_threshold=threshold,
    )

    # Build calendar grid (Sunday-start weeks).
    weeks = _build_calendar_weeks(year, month, data, today)

    # Compute prev/next month navigation.
    if month == 1:
        prev_month, prev_year = 12, year - 1
    else:
        prev_month, prev_year = month - 1, year

    if month == 12:
        next_month, next_year = 1, year + 1
    else:
        next_month, next_year = month + 1, year

    month_name = cal_mod.month_name[month]

    return render_template(
        "analytics/_calendar_month.html",
        data=data,
        weeks=weeks,
        year=year,
        month=month,
        month_name=month_name,
        prev_month=prev_month,
        prev_year=prev_year,
        next_month=next_month,
        next_year=next_year,
    )


def _render_year_view(year, account_id, threshold):
    """Render the year overview with 12 month cards."""
    data = calendar_service.get_year_overview(
        user_id=current_user.id,
        year=year,
        account_id=account_id,
        large_threshold=threshold,
    )

    # Attach month names to each MonthSummary for template display.
    month_cards = []
    for ms in data.months:
        month_cards.append({
            "summary": ms,
            "name": cal_mod.month_name[ms.month],
        })

    return render_template(
        "analytics/_calendar_year.html",
        data=data,
        month_cards=month_cards,
        year=year,
    )


def _build_calendar_weeks(year, month, data, today):
    """Build a list of week rows for the calendar grid.

    Each week is a list of 7 day dicts with keys: number, entries,
    is_paycheck, is_today, popover_html.  Empty cells have number=0.
    Uses Sunday as the first day of the week.
    """
    # Sunday-start calendar (firstweekday=6 in Python's calendar).
    cal = cal_mod.Calendar(firstweekday=6)
    month_weeks = cal.monthdayscalendar(year, month)

    paycheck_set = set(data.paycheck_days)

    weeks = []
    for week in month_weeks:
        row = []
        for day_num in week:
            if day_num == 0:
                row.append({
                    "number": 0,
                    "entries": [],
                    "is_paycheck": False,
                    "is_today": False,
                    "popover_html": "",
                })
            else:
                entries = data.day_entries.get(day_num, [])
                is_today = (
                    year == today.year
                    and month == today.month
                    and day_num == today.day
                )
                popover = _build_popover_html(entries) if entries else ""
                row.append({
                    "number": day_num,
                    "entries": entries,
                    "is_paycheck": day_num in paycheck_set,
                    "is_today": is_today,
                    "popover_html": popover,
                })
        weeks.append(row)
    return weeks


def _build_popover_html(entries):
    """Build HTML content for a day's Bootstrap popover.

    Returns a plain string (not Markup) so Jinja auto-escapes it when
    placed inside a data-bs-content attribute.  Bootstrap's popover
    with data-bs-html="true" will parse the entity-decoded HTML at
    display time.
    """
    lines = []
    for entry in entries[:5]:
        name = escape(entry.name)
        amount = f"${entry.amount:,.2f}"
        status = "Paid" if entry.is_paid else "Projected"
        marker = "text-success" if entry.is_income else "text-danger"
        lines.append(
            f'<div class="mb-1">'
            f'<span class="{marker}">&#9679;</span> '
            f'{name} <span class="font-mono">{amount}</span> '
            f'<small class="text-muted">-- {status}</small>'
            f'</div>'
        )
    if len(entries) > 5:
        lines.append(
            f'<div class="text-muted"><small>+{len(entries) - 5} more</small></div>'
        )
    return "".join(lines)


# ── Variance helpers ───────────────────────────────────────────────


def _build_variance_chart_data(report):
    """Build chart data dict from a VarianceReport.

    Converts Decimal values to float for JSON serialization in
    template data attributes.

    Args:
        report: VarianceReport from the variance service.

    Returns:
        dict with labels, estimated, and actual lists.
    """
    return {
        "labels": [g.group_name for g in report.groups],
        "estimated": [float(g.estimated_total) for g in report.groups],
        "actual": [float(g.actual_total) for g in report.groups],
    }


# ── Year-end helpers ───────────────────────────────────────────────


def _get_available_years(user_id, current_year):
    """Build the list of years for the year selector dropdown.

    Spans from the user's earliest pay period year through the
    current year, or just the current year if no periods exist.

    Args:
        user_id: The authenticated user's ID.
        current_year: Today's year as an upper bound.

    Returns:
        List of year integers in descending order.
    """
    earliest_period = (
        db.session.query(PayPeriod)
        .filter(PayPeriod.user_id == user_id)
        .order_by(PayPeriod.start_date)
        .first()
    )
    start_year = (
        earliest_period.start_date.year if earliest_period
        else current_year
    )
    return list(range(current_year, start_year - 1, -1))
