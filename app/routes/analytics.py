"""
Shekel Budget App -- Analytics Routes

Analytics dashboard with four HTMX lazy-loaded tabs:
Calendar, Year-End Summary, Budget Variance, and Spending Trends.
Each tab endpoint returns an HTML partial loaded into the main page
via nav-pills navigation.
"""

import calendar as cal_mod
from datetime import date

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from markupsafe import Markup, escape

from app.extensions import db
from app.models.user import UserSettings
from app.services import calendar_service

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
    """Build escaped HTML content for a day's popover.

    Shows each transaction's name, amount, and paid/projected status.
    Returns a Markup-safe string for data-bs-content attribute.
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
    return Markup("".join(lines))
