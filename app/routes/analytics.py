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

from flask import (
    Blueprint, abort, make_response, redirect, render_template, request,
    url_for,
)
from flask_login import current_user, login_required

from app.utils.auth_helpers import get_or_404, require_owner

from app.extensions import db
from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.models.user import UserSettings
from app.services import (
    budget_variance_service,
    calendar_service,
    csv_export_service,
    pay_period_service,
    spending_trend_service,
    year_end_summary_service,
)

analytics_bp = Blueprint("analytics", __name__)


def _validate_owned_or_abort(model, pk):
    """Validate that ``pk`` references a record owned by ``current_user``.

    Used at the top of analytics route handlers to enforce the
    project security response rule: "404 for both 'not found' and
    'not yours.'"  Without this guard the underlying services
    silently fall back to default data on a cross-user
    ``account_id`` (calendar) or read victim metadata into the
    response label and CSV filename on a cross-user ``period_id``
    (variance), bypassing the access boundary documented in the
    project's auth-helper contract.

    Delegates the existence + ownership check to
    :func:`app.utils.auth_helpers.get_or_404`, which already emits
    the structured ``resource_not_found`` (INFO) and
    ``access_denied_cross_user`` (WARNING) audit events the SOC
    dashboards rely on.  This wrapper adds the abort so the route
    body can stay flat (``_validate_owned_or_abort(...)`` as a
    one-liner instead of an explicit ``if record is None: abort``
    branch in every handler).

    A ``pk`` of ``None`` means "query argument absent" -- bypass
    validation and let the caller's downstream logic supply a
    user-scoped default (e.g. the user's first active checking
    account, or the user's current pay period).  The caller MUST
    NOT attempt to use the return value when passing ``None``.

    Audit reference: F-039 + F-098 / commit C-30 of the
    2026-04-15 security remediation plan.

    Args:
        model: The SQLAlchemy model class to look up.  Must expose
            a ``user_id`` column (Pattern A in
            :mod:`app.utils.auth_helpers`).
        pk: The primary key value parsed from a query argument, or
            ``None`` when the argument was not supplied.

    Returns:
        The loaded record on a successful ownership check, or
        ``None`` when ``pk`` was ``None`` (no validation performed).

    Raises:
        werkzeug.exceptions.NotFound: When ``pk`` references a
            non-existent row OR a row owned by a different user.
            Both branches produce the same 404 so the client cannot
            distinguish "no such row" from "not yours" by response
            shape.
    """
    if pk is None:
        return None
    record = get_or_404(model, pk)
    if record is None:
        abort(404)
    return record


@analytics_bp.route("/analytics")
@login_required
@require_owner
def page():
    """Render the main analytics page with four lazy-loaded tab pills.

    The page contains a nav-pills bar with Calendar, Year-End,
    Variance, and Trends tabs.  The Calendar tab auto-loads on page
    visit via HTMX.  Other tabs load on click.
    """
    return render_template("analytics/analytics.html")


@analytics_bp.route("/analytics/calendar")
@login_required
@require_owner
def calendar_tab():
    """HTMX partial or CSV: calendar tab with month detail or year overview.

    Query parameters:
        view: 'month' (default) or 'year'.
        year: Calendar year (default: current year).
        month: Calendar month 1-12 (default: current month).
        account_id: Optional account filter.
        format: 'csv' for CSV download.

    Non-HTMX requests redirect to the main analytics page unless
    format=csv (CSV downloads are regular browser navigations).
    """
    today = date.today()
    view = request.args.get("view", "month")
    year = request.args.get("year", today.year, type=int)
    month = request.args.get("month", today.month, type=int)
    account_id = request.args.get("account_id", None, type=int)

    # F-039 / commit C-30: a cross-user or non-existent account_id
    # must 404 before any service call.  The underlying
    # ``calendar_service._resolve_account`` falls back to the user's
    # default checking account when ownership fails, which would
    # otherwise mask the IDOR probe behind a normal-looking 200
    # rendered against the requester's own data.
    _validate_owned_or_abort(Account, account_id)

    year = max(2000, min(2100, year))
    month = max(1, min(12, month))

    settings = db.session.query(UserSettings).filter_by(
        user_id=current_user.id,
    ).first()
    threshold = settings.large_transaction_threshold if settings else 500

    # CSV export -- before HTMX guard.
    if request.args.get("format") == "csv":
        if view == "year":
            data = calendar_service.get_year_overview(
                user_id=current_user.id, year=year,
                account_id=account_id, large_threshold=threshold,
            )
            csv_str = csv_export_service.export_calendar_csv(data, "year")
            fname = f"calendar_{year}_year.csv"
        else:
            data = calendar_service.get_month_detail(
                user_id=current_user.id, year=year, month=month,
                account_id=account_id, large_threshold=threshold,
            )
            csv_str = csv_export_service.export_calendar_csv(data, "month")
            fname = f"calendar_{year}_{month:02d}.csv"
        return _csv_response(csv_str, fname)

    if not request.headers.get("HX-Request"):
        return redirect(url_for("analytics.page"))

    if view == "year":
        return _render_year_view(year, account_id, threshold)
    return _render_month_view(year, month, account_id, threshold, today)


@analytics_bp.route("/analytics/year-end")
@login_required
@require_owner
def year_end_tab():
    """HTMX partial or CSV: year-end financial summary.

    Query parameters:
        year: Calendar year (default: current year).
        format: 'csv' for CSV download.
    """
    today = date.today()
    year = request.args.get("year", today.year, type=int)
    year = max(2000, min(2100, year))

    data = year_end_summary_service.compute_year_end_summary(
        current_user.id, year,
    )

    if request.args.get("format") == "csv":
        csv_str = csv_export_service.export_year_end_csv(data)
        return _csv_response(csv_str, f"year_end_{year}.csv")

    if not request.headers.get("HX-Request"):
        return redirect(url_for("analytics.page"))

    available_years = _get_available_years(current_user.id, today.year)
    return render_template(
        "analytics/_year_end.html",
        data=data,
        year=year,
        available_years=available_years,
    )


@analytics_bp.route("/analytics/variance")
@login_required
@require_owner
def variance_tab():
    """HTMX partial or CSV: budget variance analysis.

    Query parameters:
        window: 'pay_period' (default), 'month', or 'year'.
        period_id: Pay period ID (for pay_period window).
        month: Month number 1-12 (for month window).
        year: Calendar year (for month/year windows).
        format: 'csv' for CSV download.
    """
    today = date.today()

    # F-098 / commit C-30: validate user-supplied ``period_id`` at
    # the route boundary.  The variance service ignores cross-user
    # period_ids when ``window_type != "pay_period"`` and produces
    # an empty report when it equals "pay_period" (the txn filter
    # joins ``account_id`` -- a user-owned account -- with
    # ``pay_period_id``, so a victim's period yields no rows).  The
    # leak is in the metadata path: ``_build_window_label`` and
    # ``_variance_csv_filename`` both read ``PayPeriod.start_date``
    # without re-checking ownership, exposing the victim's pay-
    # period start date in the response and CSV filename.  Validate
    # before ``_resolve_variance_params`` runs because that helper
    # also reads ``period_id`` from query args, and we want the 404
    # to fire before any system-default fallback masks the probe.
    _validate_owned_or_abort(
        PayPeriod, request.args.get("period_id", type=int),
    )

    window_type, period_id, month, year = _resolve_variance_params(today)

    report = budget_variance_service.compute_variance(
        user_id=current_user.id,
        window_type=window_type,
        period_id=period_id,
        month=month,
        year=year,
    )

    if request.args.get("format") == "csv":
        fname = _variance_csv_filename(window_type, period_id, month, year)
        csv_str = csv_export_service.export_variance_csv(report)
        return _csv_response(csv_str, fname)

    if not request.headers.get("HX-Request"):
        return redirect(url_for("analytics.page"))

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
@require_owner
def trends_tab():
    """HTMX partial or CSV: spending trends analysis.

    Query parameters:
        format: 'csv' for CSV download.
    """
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

    if request.args.get("format") == "csv":
        fname = f"trends_{report.window_months}month.csv"
        csv_str = csv_export_service.export_trends_csv(report)
        return _csv_response(csv_str, fname)

    if not request.headers.get("HX-Request"):
        return redirect(url_for("analytics.page"))

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
    is_paycheck, is_today, income_total, expense_total.  Empty cells
    have number=0.  Uses Sunday as the first day of the week.
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
                    "income_total": Decimal("0"),
                    "expense_total": Decimal("0"),
                })
            else:
                entries = data.day_entries.get(day_num, [])
                is_today = (
                    year == today.year
                    and month == today.month
                    and day_num == today.day
                )
                income_total = sum(
                    e.amount for e in entries if e.is_income
                )
                expense_total = sum(
                    abs(e.amount) for e in entries if not e.is_income
                )
                row.append({
                    "number": day_num,
                    "entries": entries,
                    "is_paycheck": day_num in paycheck_set,
                    "is_today": is_today,
                    "income_total": income_total,
                    "expense_total": expense_total,
                })
        weeks.append(row)
    return weeks


# ── CSV helpers ────────────────────────────────────────────────────


def _csv_response(csv_content: str, filename: str):
    """Build a Flask response for CSV file download.

    Args:
        csv_content: The CSV string body.
        filename: Suggested download filename.

    Returns:
        Flask Response with CSV headers.
    """
    response = make_response(csv_content)
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = (
        f'attachment; filename="{filename}"'
    )
    return response


# ── Variance helpers ───────────────────────────────────────────────


def _resolve_variance_params(today):
    """Parse and apply defaults for variance tab query parameters.

    Args:
        today: The current date.

    Returns:
        Tuple of (window_type, period_id, month, year).
    """
    window_type = request.args.get("window", "pay_period")
    if window_type not in ("pay_period", "month", "year"):
        window_type = "pay_period"

    period_id = request.args.get("period_id", type=int)
    month = request.args.get("month", type=int)
    year = request.args.get("year", type=int)

    if window_type == "pay_period" and period_id is None:
        current = pay_period_service.get_current_period(current_user.id)
        if current is None:
            all_p = pay_period_service.get_all_periods(current_user.id)
            current = all_p[-1] if all_p else None
        if current is not None:
            period_id = current.id
        else:
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

    return window_type, period_id, month, year


def _variance_csv_filename(window_type, period_id, month, year):
    """Build a descriptive CSV filename for variance export.

    Args:
        window_type: 'pay_period', 'month', or 'year'.
        period_id: Pay period ID (if pay_period window).
        month: Month number (if month window).
        year: Year (if month or year window).

    Returns:
        Filename string like 'variance_2026_01.csv'.
    """
    if window_type == "pay_period" and period_id is not None:
        period = db.session.get(PayPeriod, period_id)
        if period:
            return f"variance_period_{period.start_date.isoformat()}.csv"
        return "variance_period.csv"
    if window_type == "month" and month and year:
        return f"variance_{year}_{month:02d}.csv"
    if window_type == "year" and year:
        return f"variance_{year}.csv"
    return "variance.csv"


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
