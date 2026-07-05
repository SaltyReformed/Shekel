"""
Shekel Budget App -- Analytics Routes

Analytics dashboard whose nav pills lazy-load one HTMX partial each:
Calendar, Spending, Taxes, Income Statement, and Balance Sheet.  The
Spending pill (Slice 3) replaced the retired Variance and Trends pills,
and the Taxes pill (Slice 2) replaced Year-End; the underlying
``/analytics/variance``, ``/analytics/trends``, and ``/analytics/year-end``
ROUTES stay reachable until the Slice-4 shell collapse retires them with
redirects.  Each tab endpoint returns an HTML partial loaded into the main
page via nav-pills navigation.  The Income Statement and Balance Sheet tabs
(Build-Order Step 5) present the confirmed posting ledger via
``ledger_report_service``.
"""

import calendar as cal_mod
from datetime import date, datetime, timezone
from decimal import Decimal

from flask import (
    Blueprint, abort, make_response, redirect, render_template, request,
    url_for,
)
from flask_login import current_user, login_required

from app.routes import analytics_view
from app.utils.auth_helpers import get_or_404, require_owner
from app.utils.dates import to_display_date

from app.extensions import db
from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.models.salary_profile import SalaryProfile
from app.models.user import UserSettings
from app.services import (
    budget_variance_service,
    calendar_service,
    csv_export_service,
    ledger_report_service,
    pay_period_service,
    spending_report_service,
    spending_trend_service,
    tax_report_service,
    tax_withholding_service,
    year_end_summary_service,
)
from app.services.calendar_service import CalendarAccountNotResolvableError

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
    """Render the main analytics page with its lazy-loaded tab pills.

    The page contains a nav-pills bar with Calendar, Spending, Taxes,
    Income Statement, and Balance Sheet tabs (the Spending pill replaced
    the retired Variance and Trends pills in Slice 3; the Taxes pill
    replaced Year-End in Slice 2).  The Calendar tab auto-loads on page
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
    # ``today`` is the user's wall-clock day (display timezone), not the
    # server's UTC day: the month default, the today marker, and the
    # elapsed-vs-remaining split all follow the user's calendar per the
    # timezone display policy.
    today = to_display_date(datetime.now(timezone.utc))
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
    low_balance = settings.low_balance_threshold if settings else 500

    # CSV export -- before HTMX guard.
    if request.args.get("format") == "csv":
        try:
            if view == "year":
                data = calendar_service.get_year_overview(
                    user_id=current_user.id, year=year,
                    account_id=account_id, large_threshold=threshold,
                )
                csv_str = csv_export_service.export_calendar_csv(data, "year")
                fname = f"calendar_{year}_year.csv"
            else:
                # Pass ``today`` so the daily running balance (and thus the
                # CSV's end-of-day balance column) is computed.
                data = calendar_service.get_month_detail(
                    user_id=current_user.id, year=year, month=month,
                    account_id=account_id, large_threshold=threshold,
                    today=today,
                )
                csv_str = csv_export_service.export_calendar_csv(data, "month")
                fname = f"calendar_{year}_{month:02d}.csv"
        except CalendarAccountNotResolvableError:
            # F-2: missing analytics account / baseline scenario is an
            # upstream defect after the Commit 3-8 anchor remediation;
            # surface a 404 instead of masking it behind a zeroed CSV.
            abort(404)
        return _csv_response(csv_str, fname)

    if not request.headers.get("HX-Request"):
        return redirect(url_for("analytics.page"))

    try:
        if view == "year":
            return _render_year_view(year, account_id, threshold)
        data = calendar_service.get_month_detail(
            user_id=current_user.id, year=year, month=month,
            account_id=account_id, large_threshold=threshold, today=today,
        )
        return _render_month_view(data, year, month, low_balance, today)
    except CalendarAccountNotResolvableError:
        # F-2: same contract as the CSV branch above -- 404 matches the
        # project security rule ("404 for both 'not found' and 'not
        # yours'", app/utils/auth_helpers.py).
        abort(404)


@analytics_bp.route("/analytics/taxes")
@login_required
@require_owner
def taxes_tab():
    """HTMX partial: the Taxes tab (refund hero, W-2 preview, Schedule A).

    Renders :func:`app.services.tax_report_service.compute_tax_report` for
    one tax year: the refund hero band with rate/timing chips, the YTD
    checkpoint card (the T-P2 partial, wired to the primary profile), the
    assumptions card, the derivation ledger, the hybrid W-2 preview, and
    the Schedule A check.  A user with no baseline scenario or no active
    salary profile gets the empty state.

    Query parameters:
        year: Tax year (default: the display-timezone current year),
            clamped to [2000, 2100] like the sibling tabs.

    Non-HTMX requests redirect to the main analytics page (no CSV export
    for this tab; the year-end CSV's fate is T-P5's explicit decision).
    """
    today = to_display_date(datetime.now(timezone.utc))
    year = request.args.get("year", today.year, type=int)
    year = max(2000, min(2100, year))

    if not request.headers.get("HX-Request"):
        return redirect(url_for("analytics.page"))

    report = tax_report_service.compute_tax_report(current_user.id, year, today)
    available_years = _get_available_years(current_user.id, today.year)

    if report is None:
        return render_template(
            "analytics/_taxes.html",
            report=None,
            year=year,
            available_years=available_years,
        )

    # The checkpoint card partial's contract (T-P2): the owned profile for
    # its form action plus the year's latest checkpoint.  The report's
    # primary_profile_id carries the service-resolved primary so the
    # primary-profile rule is not re-derived here.
    profile = db.session.get(SalaryProfile, report.primary_profile_id)
    checkpoint = tax_withholding_service.latest_checkpoint(
        report.primary_profile_id, year,
    )
    return render_template(
        "analytics/_taxes.html",
        report=report,
        year=year,
        available_years=available_years,
        display=analytics_view.build_taxes_display(report),
        profile=profile,
        checkpoint=checkpoint,
        errors={},
        save_error=None,
        form_values={},
    )


@analytics_bp.route("/analytics/spending")
@login_required
@require_owner
def spending_tab():
    """HTMX partial: the Spending tab (Slice 3, S-P2).

    Renders :func:`app.services.spending_report_service.compute_spending_report`
    for one calendar month: the spent hero band with vs-prior / vs-average /
    payment-timing chips, the "Where It Went" category breakdown (share bars,
    per-period trend sparklines, and delta chips), and the Top Movers /
    Estimate Surprises rail.  The surface is MEASURED (settled expenses on the
    user's active checking account); the account scope and settled basis are
    labeled on screen.  A user with no active checking account or no baseline
    scenario gets the empty state.

    The producer accepts pay-period / month / year windows, but S-P2 exposes
    only the calendar-month picker (the S-P1 gate ruling).  The default is the
    most recent COMPLETED month (the prior calendar month); a partial current
    month would mislabel an incomplete total.  Forward navigation is capped at
    the current month, since a measured surface has no settled spend in a
    future month.

    Query parameters:
        month: Calendar month 1-12 (default: the prior month), clamped to
            [1, 12] like the sibling tabs.
        year: Calendar year (default: the prior month's year), clamped to
            [2000, 2100].

    Non-HTMX requests redirect to the main analytics page (no CSV export for
    this tab, per the 2026-07-05 CSV ruling).
    """
    today = to_display_date(datetime.now(timezone.utc))
    default_year, default_month = analytics_view.prev_month(
        today.year, today.month,
    )
    month = request.args.get("month", default_month, type=int)
    year = request.args.get("year", default_year, type=int)
    month = max(1, min(12, month))
    year = max(2000, min(2100, year))

    if not request.headers.get("HX-Request"):
        return redirect(url_for("analytics.page"))

    window = spending_report_service.SpendingWindow(
        window_type="month", month=month, year=year,
    )
    report = spending_report_service.compute_spending_report(
        current_user.id, window,
    )

    return render_template(
        "analytics/_spending.html",
        report=report,
        year=year,
        month=month,
        display=(
            analytics_view.build_spending_display(report)
            if report is not None else None
        ),
        sparklines=(
            analytics_view.spending_sparklines(report)
            if report is not None else {}
        ),
        **analytics_view.build_spending_nav(today, year, month),
    )


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
    # ``_window_csv_filename`` both read ``PayPeriod.start_date``
    # without re-checking ownership, exposing the victim's pay-
    # period start date in the response and CSV filename.  Validate
    # before ``_resolve_window_params`` runs because that helper
    # also reads ``period_id`` from query args, and we want the 404
    # to fire before any system-default fallback masks the probe.
    _validate_owned_or_abort(
        PayPeriod, request.args.get("period_id", type=int),
    )

    window_type, period_id, month, year = _resolve_window_params(today)
    window = budget_variance_service.VarianceWindow(
        window_type=window_type, period_id=period_id, month=month, year=year,
    )

    report = budget_variance_service.compute_variance(current_user.id, window)

    if request.args.get("format") == "csv":
        fname = _window_csv_filename(
            "variance", window_type, period_id, month, year,
        )
        csv_str = csv_export_service.export_variance_csv(report)
        return _csv_response(csv_str, fname)

    if not request.headers.get("HX-Request"):
        return redirect(url_for("analytics.page"))

    chart_data = analytics_view.build_variance_chart_data(report)
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


@analytics_bp.route("/analytics/income-statement")
@login_required
@require_owner
def income_statement_tab():
    """HTMX partial or CSV: confirmed-ledger income statement.

    Reads the append-only posting ledger's Income and Expense accounts
    into a revenue / cost statement over one window
    (:func:`app.services.ledger_report_service.compute_income_statement`),
    for the baseline scenario only (the deferred multi-scenario policy is
    R8's).

    Query parameters:
        window: 'pay_period' (default), 'month', or 'year'.
        period_id: Pay period ID (for the pay_period window).
        month: Month number 1-12 (for the month window).
        year: Calendar year (for the month/year windows).
        format: 'csv' for CSV download.

    Non-HTMX requests redirect to the main analytics page unless
    format=csv (CSV downloads are regular browser navigations).
    """
    today = date.today()

    # IDOR (mirrors ``variance_tab``): validate a user-supplied
    # ``period_id`` at the boundary before ``_resolve_window_params``
    # reads it.  The statement's money queries are user-scoped (a foreign
    # period yields an empty report), but the service reads the period for
    # its window LABEL un-scoped, so a foreign ``period_id`` would
    # otherwise leak the victim's period dates into the response.
    _validate_owned_or_abort(
        PayPeriod, request.args.get("period_id", type=int),
    )

    window_type, period_id, month, year = _resolve_window_params(today)
    window = ledger_report_service.StatementWindow(
        window_type=window_type, period_id=period_id, month=month, year=year,
    )
    report = ledger_report_service.compute_income_statement(
        current_user.id, window,
    )

    if request.args.get("format") == "csv":
        fname = _window_csv_filename(
            "income_statement", window_type, period_id, month, year,
        )
        csv_str = csv_export_service.export_income_statement_csv(report)
        return _csv_response(csv_str, fname)

    if not request.headers.get("HX-Request"):
        return redirect(url_for("analytics.page"))

    periods = pay_period_service.get_all_periods(current_user.id)
    available_years = _get_available_years(current_user.id, today.year)
    return render_template(
        "analytics/_income_statement.html",
        report=report,
        window_type=window_type,
        period_id=period_id,
        month=month,
        year=year,
        periods=periods,
        available_years=available_years,
    )


@analytics_bp.route("/analytics/balance-sheet")
@login_required
@require_owner
def balance_sheet_tab():
    """HTMX partial or CSV: confirmed-ledger balance sheet as of a date.

    Reads the posted ledger's position as of ``as_of``
    (:func:`app.services.ledger_report_service.compute_balance_sheet`):
    Assets, Liabilities, and Equity sections with a derived
    retained-earnings line and a two-part trial-balance tie-out, for the
    baseline scenario only.

    Query parameters:
        as_of: ISO date (YYYY-MM-DD); defaults to today and is clamped to
            [2000-01-01, today].
        format: 'csv' for CSV download.

    Non-HTMX requests redirect to the main analytics page unless
    format=csv (CSV downloads are regular browser navigations).
    """
    today = date.today()
    as_of = _resolve_as_of_param(request.args.get("as_of"), today)
    report = ledger_report_service.compute_balance_sheet(
        current_user.id, as_of,
    )

    if request.args.get("format") == "csv":
        csv_str = csv_export_service.export_balance_sheet_csv(report)
        return _csv_response(
            csv_str, f"balance_sheet_{as_of.isoformat()}.csv",
        )

    if not request.headers.get("HX-Request"):
        return redirect(url_for("analytics.page"))

    return render_template(
        "analytics/_balance_sheet.html",
        report=report,
        as_of=as_of,
        today=today,
    )


# ── Calendar helpers ────────────────────────────────────────────────


def _render_month_view(data, year, month, low_balance, today):
    """Render the month detail calendar view from resolved MonthSummary data.

    Builds a 7-column Sun-Sat calendar grid (each day carrying its projected
    end-of-day balance) and passes the low-balance threshold through for the
    flow strip line and the below-threshold cell coloring.  The caller
    resolves ``data`` via :func:`calendar_service.get_month_detail` (with
    ``today``) so the fetch and its 404 handling stay in the route.

    ``month_end_balance`` is the RUNNING end-of-day balance on the month's
    last calendar day (``daily.daily_balances``), not the period-flat
    ``MonthSummary.projected_end_balance`` scalar -- the P1 as-built ruling:
    the month-end tile must agree with the flow strip's right end (the two
    differ when the month ends mid-period).
    """
    # Build calendar grid (Sunday-start weeks).
    weeks = analytics_view.build_calendar_weeks(year, month, data, today)

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

    # Direct indexing, not .get(): the daily-series producer guarantees a
    # key for every day of the range, so a missing last day is a producer
    # defect that must fail loud (KeyError), not silently hide the chip --
    # the same contract the flow-strip serializer relies on.
    days_in_month = cal_mod.monthrange(year, month)[1]
    month_end_balance = (
        data.daily.daily_balances[days_in_month]
        if data.daily is not None else None
    )

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
        low_balance_threshold=low_balance,
        today=today,
        month_end_balance=month_end_balance,
        flow_strip_json=analytics_view.serialize_flow_strip(
            data, low_balance, today, year, month,
        ),
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


# ── Window / variance helpers ──────────────────────────────────────
# ``_resolve_window_params`` is shared by the Variance and Income Statement
# tabs; the remaining helpers below it are variance-specific.


def _resolve_window_params(today):
    """Parse and apply defaults for the shared window query parameters.

    The ``window`` / ``period_id`` / ``month`` / ``year`` parsing shared by
    the Budget Variance and Income Statement tabs (both discriminate a
    pay-period / month / year window the same way, though each builds its own
    window value object over the result).  Applies the same defaults for both:
    a bare ``pay_period`` window resolves the user's current period (falling
    back to their most recent, then to a month window when the user has no
    periods at all); a bare ``month`` / ``year`` window fills *today*'s
    fields.  A calendar ``month`` / ``year`` is range-clamped here (month to
    1-12, year to 2000-2100, the ``calendar_tab`` / ``year_end_tab``
    convention) so a hand-crafted out-of-range value cannot reach ``date()``
    / ``calendar.monthrange()`` in either tab's service and raise; the clamp
    is a no-op for the in-range defaults and for the ``pay_period`` window
    (whose ``month`` / ``year`` stay ``None``).

    Args:
        today: The current date.

    Returns:
        Tuple of (window_type, period_id, month, year); ``month`` and
        ``year`` are either ``None`` or in the clamped ranges above.
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

    # Range-clamp the calendar fields (a no-op for defaults and for a
    # pay_period window's None fields) so neither tab's service constructs
    # an out-of-range date from a hand-crafted query string.
    if month is not None:
        month = max(1, min(12, month))
    if year is not None:
        year = max(2000, min(2100, year))

    return window_type, period_id, month, year


def _window_csv_filename(prefix, window_type, period_id, month, year):
    """Build a descriptive CSV filename for a windowed-report export.

    Shared by the Budget Variance and Income Statement tabs, which export
    over the same ``pay_period`` / ``month`` / ``year`` window shape;
    *prefix* names the report (``"variance"`` / ``"income_statement"``).
    A ``pay_period`` window reads the period's ``start_date`` (the same
    read the F-098 route guard protects -- every caller IDOR-validates
    ``period_id`` before reaching here); a ``month`` / ``year`` window
    uses the calendar fields.

    Args:
        prefix: The report-name filename prefix (e.g. ``"variance"``).
        window_type: 'pay_period', 'month', or 'year'.
        period_id: Pay period ID (if pay_period window).
        month: Month number (if month window).
        year: Year (if month or year window).

    Returns:
        Filename string like 'variance_2026_01.csv' or
        'income_statement_period_2026-01-02.csv'.
    """
    if window_type == "pay_period" and period_id is not None:
        period = db.session.get(PayPeriod, period_id)
        if period:
            return f"{prefix}_period_{period.start_date.isoformat()}.csv"
        return f"{prefix}_period.csv"
    if window_type == "month" and month and year:
        return f"{prefix}_{year}_{month:02d}.csv"
    if window_type == "year" and year:
        return f"{prefix}_{year}.csv"
    return f"{prefix}.csv"




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


# ── Balance sheet helpers ──────────────────────────────────────────


def _resolve_as_of_param(raw, today):
    """Parse the balance sheet ``as_of`` query arg to a clamped date.

    Args:
        raw: The raw ``as_of`` query value (an ISO ``YYYY-MM-DD`` string),
            or ``None`` when the argument was absent.
        today: The current date -- both the default and the upper clamp
            bound.

    Returns:
        The parsed date clamped to ``[2000-01-01, today]``; ``today`` when
        *raw* is absent or is not a valid ISO date (a future or pre-2000
        as-of is meaningless for the posted ledger, and a garbage value
        must degrade to today rather than raise).
    """
    floor = date(2000, 1, 1)
    if not raw:
        return today
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        return today
    return max(floor, min(today, parsed))
