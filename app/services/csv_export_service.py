"""
Shekel Budget App -- CSV Export Service

Pure-function service that converts analytics data structures into
downloadable CSV strings.  Each export function takes the same data
that its corresponding template receives and returns a UTF-8 CSV
string suitable for a Flask response body.

Rules:
  - Monetary amounts as plain numbers with 2 decimal places (no $).
  - Dates as YYYY-MM-DD (ISO 8601).
  - None/null rendered as empty strings.
  - Uses csv.writer for proper quoting and escaping.
"""

import csv
import io
from decimal import Decimal

from app.utils.money import round_money

# Spreadsheet formula-injection (CWE-1236) lead characters.  A CSV cell
# whose first character is one of these is evaluated as a formula by
# Excel / Google Sheets when the downloaded export is opened, so a
# user-typed name like ``=HYPERLINK(...)`` would execute against whoever
# opens the file.  ``_safe`` prefixes a single quote to force the cell
# to render as literal text.  TAB (0x09) and CR (0x0D) are included per
# the OWASP CSV-injection guidance.  Note this neutralizes only
# user-controlled free text routed through ``_safe``; system-formatted
# numerics from ``_dec`` (which may legitimately lead with ``-``) are
# intentionally left numeric so spreadsheets can still aggregate them.
_FORMULA_LEAD_CHARS = ("=", "+", "-", "@", "\t", "\r")


# ── Formatting helpers ────────────────────────────────────────────


def _dec(value: Decimal | int | float | None) -> str:
    """Format a numeric value to 2 decimal places without currency symbol.

    Args:
        value: Decimal, int, float, or None.

    Returns:
        String like '1500.00', or '' for None.
    """
    if value is None:
        return ""
    return str(round_money(Decimal(str(value))))


def _date(value) -> str:
    """Format a date as ISO 8601 YYYY-MM-DD.

    Args:
        value: date, datetime, or None.

    Returns:
        String like '2026-01-15', or '' for None.
    """
    if value is None:
        return ""
    if hasattr(value, "date"):
        return value.date().isoformat()
    return value.isoformat()


def _safe(value) -> str:
    """Convert a user-controlled value to a formula-safe CSV cell string.

    Treats None as empty, then neutralizes spreadsheet formula
    injection (CWE-1236): a cell whose first character is a formula
    trigger (``= + - @``, TAB, or CR) is prefixed with a single quote
    so Excel / Google Sheets render it as literal text instead of
    executing it on open.  Every free-text column sourced from user
    input -- transaction, category, account, and deduction names --
    must route through this helper rather than being written raw.

    Args:
        value: Any value; the case that matters for neutralization is
            user-controlled free text.

    Returns:
        String representation, '' for None, formula-neutralized.
    """
    if value is None:
        return ""
    text = str(value)
    if text.startswith(_FORMULA_LEAD_CHARS):
        return "'" + text
    return text


def _bool_yn(value: bool) -> str:
    """Convert a boolean to 'Yes' or 'No'.

    Args:
        value: Boolean.

    Returns:
        'Yes' or 'No'.
    """
    return "Yes" if value else "No"


def _write_csv(rows: list[list]) -> str:
    """Write rows to a CSV string using csv.writer.

    Args:
        rows: List of lists, where each inner list is a row.

    Returns:
        CSV formatted string.
    """
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerows(rows)
    return output.getvalue()


# ── Calendar Export ───────────────────────────────────────────────


def export_calendar_csv(data, view_type: str) -> str:
    """Export calendar data as CSV.

    For month view: one row per transaction from day_entries.
    For year view: one row per month summary.

    Args:
        data: MonthSummary (month view) or YearOverview (year view).
        view_type: 'month' or 'year'.

    Returns:
        CSV string.
    """
    if view_type == "year":
        return _export_calendar_year(data)
    return _export_calendar_month(data)


def _export_calendar_month(data) -> str:
    """Export month calendar as one row per transaction.

    The trailing "End-of-Day Balance ($)" column carries the day's projected
    end-of-day running balance (repeated on every transaction row for that
    day), sourced from the same :class:`DailyView` the on-screen flow strip
    uses.  It is blank when no daily view was computed (the export route
    always supplies ``today`` so it is populated in practice).

    Args:
        data: MonthSummary with day_entries dict and optional ``daily`` view.

    Returns:
        CSV string with transaction rows.
    """
    headers = [
        "Due Date", "Name", "Category Group", "Category Item",
        "Amount ($)", "Income/Expense", "Status", "Large", "Infrequent",
        "End-of-Day Balance ($)",
    ]
    rows = [headers]
    daily_balances = data.daily.daily_balances if data.daily else {}

    for day in sorted(data.day_entries.keys()):
        for entry in data.day_entries[day]:
            rows.append([
                _date(entry.due_date),
                _safe(entry.name),
                _safe(entry.category_group),
                _safe(entry.category_item),
                _dec(entry.amount),
                "Income" if entry.is_income else "Expense",
                "Paid" if entry.is_paid else "Projected",
                _bool_yn(entry.is_large),
                _bool_yn(entry.is_infrequent),
                _dec(daily_balances.get(day)),
            ])

    return _write_csv(rows)


def _export_calendar_year(data) -> str:
    """Export year overview as one row per month.

    Args:
        data: YearOverview with months list.

    Returns:
        CSV string with 12 month rows.
    """
    headers = [
        "Month", "Total Income ($)", "Total Expenses ($)",
        "Net ($)", "Projected End Balance ($)",
        "Third Paycheck Month",
    ]
    rows = [headers]

    month_names = [
        "", "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ]

    for ms in data.months:
        rows.append([
            month_names[ms.month],
            _dec(ms.total_income),
            _dec(ms.total_expenses),
            _dec(ms.net),
            _dec(ms.projected_end_balance),
            _bool_yn(ms.is_third_paycheck_month),
        ])

    return _write_csv(rows)
