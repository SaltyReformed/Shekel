"""
Shekel Budget App -- CSV Export Service

Pure-function service that converts analytics data structures into
downloadable CSV strings.  Each export function takes the same data
that its corresponding template receives and returns a UTF-8 CSV
string suitable for a Flask response body.

Rules:
  - Monetary amounts as plain numbers with 2 decimal places (no $).
  - Percentages as plain numbers (no % suffix).
  - Dates as YYYY-MM-DD (ISO 8601).
  - None/null rendered as empty strings.
  - Uses csv.writer for proper quoting and escaping.
"""

import csv
import io
from decimal import Decimal, ROUND_HALF_UP

TWO_PLACES = Decimal("0.01")
HUNDRED = Decimal("100")


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
    return str(Decimal(str(value)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP))


def _pct(value: Decimal | None) -> str:
    """Format a percentage value to 2 decimal places without % suffix.

    Args:
        value: Decimal percentage or None.

    Returns:
        String like '10.50', or '' for None.
    """
    if value is None:
        return ""
    return str(Decimal(str(value)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP))


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
    """Convert any value to a string, treating None as empty.

    Args:
        value: Any value.

    Returns:
        String representation, '' for None.
    """
    if value is None:
        return ""
    return str(value)


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

    Args:
        data: MonthSummary with day_entries dict.

    Returns:
        CSV string with transaction rows.
    """
    headers = [
        "Due Date", "Name", "Category Group", "Category Item",
        "Amount ($)", "Income/Expense", "Status", "Large", "Infrequent",
    ]
    rows = [headers]

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


# ── Year-End Export ───────────────────────────────────────────────


def export_year_end_csv(data: dict) -> str:
    """Export year-end summary as a multi-section CSV.

    Sections are separated by blank rows and header rows in
    brackets (e.g., [Income and Taxes]).

    Args:
        data: dict from compute_year_end_summary().

    Returns:
        CSV string with labeled sections.
    """
    rows: list[list] = []
    _add_income_section(rows, data["income_tax"])
    _add_spending_section(rows, data["spending_by_category"])
    _add_transfers_section(rows, data["transfers_summary"])
    _add_net_worth_section(rows, data["net_worth"])
    _add_debt_section(rows, data["debt_progress"])
    _add_savings_section(rows, data["savings_progress"])
    _add_timeliness_section(rows, data.get("payment_timeliness"))
    return _write_csv(rows)


def _add_income_section(rows: list, inc: dict) -> None:
    """Append the Income and Taxes section rows."""
    rows.append(["[Income and Taxes]"])
    rows.append(["Item", "Amount ($)", "W-2 Box"])
    rows.append(["Gross Wages", _dec(inc["gross_wages"]), "Box 1"])
    rows.append(["Federal Income Tax", _dec(inc["federal_tax"]), "Box 2"])
    rows.append(["Social Security Tax", _dec(inc["social_security_tax"]), "Box 4"])
    rows.append(["Medicare Tax", _dec(inc["medicare_tax"]), "Box 6"])
    rows.append(["State Income Tax", _dec(inc["state_tax"]), "Box 17"])

    for ded in inc["pretax_deductions"]:
        rows.append([ded["name"], _dec(ded["annual_total"]), ""])
    rows.append(["Total Pre-Tax Deductions", _dec(inc["total_pretax"]), ""])

    for ded in inc["posttax_deductions"]:
        rows.append([ded["name"], _dec(ded["annual_total"]), ""])
    rows.append(["Total Post-Tax Deductions", _dec(inc["total_posttax"]), ""])

    rows.append(["Net Pay", _dec(inc["net_pay_total"]), ""])

    if inc["mortgage_interest_total"] and inc["mortgage_interest_total"] > 0:
        rows.append([
            "Mortgage Interest Paid", _dec(inc["mortgage_interest_total"]),
            "Schedule A",
        ])


def _add_spending_section(rows: list, spending: list) -> None:
    """Append the Spending by Category section rows."""
    rows.append([])
    rows.append(["[Spending by Category]"])
    rows.append(["Category Group", "Category Item", "Amount ($)"])
    for group in spending:
        rows.append([group["group_name"], "", _dec(group["group_total"])])
        for item in group["items"]:
            rows.append([group["group_name"], item["item_name"], _dec(item["item_total"])])


def _add_transfers_section(rows: list, transfers: list) -> None:
    """Append the Transfers section rows."""
    rows.append([])
    rows.append(["[Transfers]"])
    rows.append(["Destination Account", "Amount ($)"])
    for t in transfers:
        rows.append([t["destination_account"], _dec(t["total_amount"])])


def _add_net_worth_section(rows: list, nw: dict) -> None:
    """Append the Net Worth Monthly section rows."""
    rows.append([])
    rows.append(["[Net Worth Monthly]"])
    rows.append(["Month", "Balance ($)"])
    for mv in nw["monthly_values"]:
        rows.append([mv["month_name"], _dec(mv["balance"])])
    rows.append(["Jan 1", _dec(nw["jan1"])])
    rows.append(["Dec 31", _dec(nw["dec31"])])
    rows.append(["Delta", _dec(nw["delta"])])


def _add_debt_section(rows: list, debt: list) -> None:
    """Append the Debt Progress section rows."""
    rows.append([])
    rows.append(["[Debt Progress]"])
    rows.append(["Account", "Jan 1 Balance ($)", "Dec 31 Balance ($)", "Principal Paid ($)"])
    for d in debt:
        rows.append([
            d["account_name"], _dec(d["jan1_balance"]),
            _dec(d["dec31_balance"]), _dec(d["principal_paid"]),
        ])


def _add_savings_section(rows: list, savings: list) -> None:
    """Append the Savings Progress section rows."""
    rows.append([])
    rows.append(["[Savings Progress]"])
    rows.append([
        "Account", "Jan 1 Balance ($)", "Dec 31 Balance ($)",
        "Contributions ($)", "Employer ($)", "Growth ($)",
    ])
    for s in savings:
        rows.append([
            s["account_name"], _dec(s["jan1_balance"]),
            _dec(s["dec31_balance"]), _dec(s["total_contributions"]),
            _dec(s.get("employer_contributions", 0)),
            _dec(s.get("investment_growth", 0)),
        ])


def _add_timeliness_section(rows: list, pt: dict | None) -> None:
    """Append the Payment Timeliness section rows if data exists."""
    if pt is None:
        return
    rows.append([])
    rows.append(["[Payment Timeliness]"])
    rows.append(["Metric", "Value"])
    rows.append(["Total Bills Paid", _safe(pt["total_bills_paid"])])
    rows.append(["Paid On Time", _safe(pt["paid_on_time"])])
    rows.append(["Paid Late", _safe(pt["paid_late"])])
    rows.append(["Avg Days Before Due", _dec(pt["avg_days_before_due"])])


# ── Variance Export ───────────────────────────────────────────────


def export_variance_csv(report) -> str:
    """Export variance report as a hierarchical CSV.

    Three levels: Group, Item, Transaction.  A Level column allows
    spreadsheet filtering by level.

    Args:
        report: VarianceReport from budget_variance_service.

    Returns:
        CSV string with Group/Item/Transaction rows.
    """
    headers = [
        "Level", "Category Group", "Category Item",
        "Transaction Name", "Estimated ($)", "Actual ($)",
        "Variance ($)", "Variance (%)", "Paid",
    ]
    rows = [headers]

    for group in report.groups:
        rows.append([
            "Group", group.group_name, "", "",
            _dec(group.estimated_total), _dec(group.actual_total),
            _dec(group.variance), _pct(group.variance_pct), "",
        ])
        for item in group.items:
            rows.append([
                "Item", item.group_name, item.item_name, "",
                _dec(item.estimated_total), _dec(item.actual_total),
                _dec(item.variance), _pct(item.variance_pct), "",
            ])
            for txn in item.transactions:
                rows.append([
                    "Transaction", item.group_name, item.item_name,
                    _safe(txn.name),
                    _dec(txn.estimated), _dec(txn.actual),
                    _dec(txn.variance), _pct(txn.variance_pct),
                    _bool_yn(txn.is_paid),
                ])

    # Totals row.
    rows.append([
        "Total", "", "", "",
        _dec(report.total_estimated), _dec(report.total_actual),
        _dec(report.total_variance), _pct(report.total_variance_pct), "",
    ])

    return _write_csv(rows)


# ── Trends Export ─────────────────────────────────────────────────


def export_trends_csv(report) -> str:
    """Export spending trends as CSV with metadata header.

    First row is metadata (window months and threshold).
    Second row is column headers.  Remaining rows are all items.

    Args:
        report: TrendReport from spending_trend_service.

    Returns:
        CSV string with metadata header and item rows.
    """
    threshold_pct = str(
        (report.threshold * HUNDRED).quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
    )
    rows: list[list] = [
        ["Window", f"{report.window_months} months", "Threshold", f"{threshold_pct}%"],
    ]

    headers = [
        "Category Group", "Category Item", "Period Average ($)",
        "Direction", "Change (%)", "Change ($/period)",
        "Data Points", "Flagged", "Avg Days Before Due",
    ]
    rows.append(headers)

    for item in report.all_items:
        rows.append([
            item.group_name,
            item.item_name,
            _dec(item.period_average),
            item.trend_direction,
            _pct(item.pct_change),
            _dec(item.absolute_change),
            str(item.data_points),
            _bool_yn(item.is_flagged),
            _dec(item.avg_days_before_due),
        ])

    return _write_csv(rows)
