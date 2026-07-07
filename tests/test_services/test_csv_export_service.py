"""
Tests for csv_export_service.py -- calendar export (Commit 17).

Verifies CSV generation for the calendar month/year export -- the only
surviving analytics export after the Slice-4 shell collapse (the year-end,
variance, trends, income-statement, and balance-sheet exports were retired
with their tabs).  Tests cover header correctness, data formatting, edge
cases (None, special characters, decimal precision), CSV parseability, and
formula-injection (CWE-1236) neutralization.
"""

import csv
import io
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.services.csv_export_service import export_calendar_csv


# ── Fake Data Structures ─────────────────────────────────────────


@dataclass(frozen=True)
class FakeDayEntry:
    """Minimal DayEntry stand-in for CSV tests."""
    transaction_id: int = 1
    name: str = "Test Txn"
    amount: Decimal = Decimal("100.00")
    is_income: bool = False
    is_paid: bool = True
    is_large: bool = False
    is_infrequent: bool = False
    category_group: str | None = "Home"
    category_item: str | None = "Rent"
    due_date: date | None = date(2026, 1, 15)


@dataclass(frozen=True)
class FakeMonthSummary:
    """Minimal MonthSummary for CSV tests."""
    year: int = 2026
    month: int = 1
    total_income: Decimal = Decimal("3000.00")
    total_expenses: Decimal = Decimal("2000.00")
    net: Decimal = Decimal("1000.00")
    projected_end_balance: Decimal = Decimal("5000.00")
    is_third_paycheck_month: bool = False
    day_entries: dict = field(default_factory=dict)
    day_totals: dict = field(default_factory=dict)
    day_overflow: dict = field(default_factory=dict)
    paycheck_days: list = field(default_factory=list)
    # Daily running-balance view; None here means the CSV's end-of-day
    # balance column is blank (the route supplies it in production).
    daily: object = None


@dataclass(frozen=True)
class FakeYearOverview:
    """Minimal YearOverview for CSV tests."""
    year: int = 2026
    months: list = field(default_factory=list)
    annual_income: Decimal = Decimal("36000.00")
    annual_expenses: Decimal = Decimal("24000.00")
    annual_net: Decimal = Decimal("12000.00")


def _parse_csv(csv_str):
    """Parse a CSV string back into a list of rows."""
    return list(csv.reader(io.StringIO(csv_str)))


# ── Calendar Tests ────────────────────────────────────────────────


class TestCalendarExport:
    """Tests for export_calendar_csv()."""

    def test_export_calendar_month_headers(self, app):
        """C17-svc1: Month CSV first row contains expected headers."""
        data = FakeMonthSummary(day_entries={
            5: [FakeDayEntry()],
        })
        result = export_calendar_csv(data, "month")
        rows = _parse_csv(result)
        assert rows[0][0] == "Due Date"
        assert "Amount ($)" in rows[0]
        assert "Income/Expense" in rows[0]
        assert "End-of-Day Balance ($)" in rows[0]

    def test_export_calendar_month_eod_balance_column(self, app):
        """The end-of-day balance column carries the day's running balance."""
        from types import SimpleNamespace
        data = FakeMonthSummary(
            day_entries={5: [FakeDayEntry(name="Rent")]},
            daily=SimpleNamespace(daily_balances={5: Decimal("800.00")}),
        )
        result = export_calendar_csv(data, "month")
        rows = _parse_csv(result)
        eod_col = rows[0].index("End-of-Day Balance ($)")
        # The Rent row (day 5) carries that day's projected EOD balance.
        assert rows[1][eod_col] == "800.00"

    def test_export_calendar_month_data(self, app):
        """C17-svc2: Month CSV has correct number of data rows."""
        data = FakeMonthSummary(day_entries={
            5: [FakeDayEntry(name="A"), FakeDayEntry(name="B")],
            10: [FakeDayEntry(name="C")],
        })
        result = export_calendar_csv(data, "month")
        rows = _parse_csv(result)
        assert len(rows) == 4  # header + 3 data rows

    def test_export_calendar_year_12_months(self, app):
        """C17-svc3: Year CSV has 12 data rows."""
        months = [
            FakeMonthSummary(month=m) for m in range(1, 13)
        ]
        data = FakeYearOverview(months=months)
        result = export_calendar_csv(data, "year")
        rows = _parse_csv(result)
        assert len(rows) == 13  # header + 12

    def test_export_calendar_empty(self, app):
        """C17-extra1: Empty month produces headers only."""
        data = FakeMonthSummary(day_entries={})
        result = export_calendar_csv(data, "month")
        rows = _parse_csv(result)
        assert len(rows) == 1  # headers only


# ── Cross-Cutting Tests ──────────────────────────────────────────


class TestCsvFormatting:
    """Tests for CSV formatting rules on the calendar export."""

    def test_export_amounts_no_currency_symbol(self, app):
        """C17-extra2: No $ in CSV data (only in column headers)."""
        data = FakeMonthSummary(day_entries={
            1: [FakeDayEntry(amount=Decimal("1500.00"))],
        })
        result = export_calendar_csv(data, "month")
        rows = _parse_csv(result)
        # Check data rows (skip header) for dollar signs.
        for row in rows[1:]:
            for cell in row:
                assert "$" not in cell, f"Found $ in data cell: {cell}"

    def test_export_dates_iso_format(self, app):
        """C17-extra3: Dates formatted as YYYY-MM-DD."""
        data = FakeMonthSummary(day_entries={
            15: [FakeDayEntry(due_date=date(2026, 1, 15))],
        })
        result = export_calendar_csv(data, "month")
        assert "2026-01-15" in result

    def test_export_none_values_empty_string(self, app):
        """C17-extra4: None does not appear as literal 'None'."""
        data = FakeMonthSummary(day_entries={
            1: [FakeDayEntry(category_group=None, category_item=None,
                             due_date=None)],
        })
        result = export_calendar_csv(data, "month")
        assert "None" not in result

    def test_export_commas_in_names(self, app):
        """C17-extra5: Names with commas are properly quoted."""
        data = FakeMonthSummary(day_entries={
            1: [FakeDayEntry(name="Smith, John")],
        })
        result = export_calendar_csv(data, "month")
        rows = _parse_csv(result)
        names = [r[1] for r in rows[1:]]
        assert "Smith, John" in names

    def test_export_quotes_in_names(self, app):
        """C17-extra6: Names with quotes are properly escaped."""
        data = FakeMonthSummary(day_entries={
            1: [FakeDayEntry(name='He said "hello"')],
        })
        result = export_calendar_csv(data, "month")
        rows = _parse_csv(result)
        names = [r[1] for r in rows[1:]]
        assert 'He said "hello"' in names

    def test_export_decimal_precision(self, app):
        """C17-extra7: Amounts formatted to exactly 2 decimal places."""
        data = FakeMonthSummary(day_entries={
            1: [FakeDayEntry(amount=Decimal("1500.1"))],
        })
        result = export_calendar_csv(data, "month")
        assert "1500.10" in result

    def test_csv_parseable(self, app):
        """C17-extra11: Output is parseable with uniform column counts."""
        data = FakeMonthSummary(day_entries={
            5: [FakeDayEntry(name="A"), FakeDayEntry(name="B")],
            10: [FakeDayEntry(name="C")],
        })
        result = export_calendar_csv(data, "month")
        rows = _parse_csv(result)
        assert len(rows) > 1
        # All rows should have the same number of columns.
        col_count = len(rows[0])
        for row in rows:
            assert len(row) == col_count, (
                f"Row has {len(row)} cols, expected {col_count}: {row}"
            )


# ── Formula-Injection (CWE-1236) Tests ───────────────────────────


class TestFormulaInjection:
    """User-controlled name cells are neutralized against spreadsheet
    formula injection: a value whose first character is a formula
    trigger (= + - @ TAB CR) is prefixed with a single quote so Excel /
    Google Sheets render it as literal text, while system-formatted
    numerics stay numeric so spreadsheets can still aggregate them.
    """

    def test_calendar_transaction_name_neutralized(self, app):
        """A transaction name leading with '=' is quote-prefixed."""
        data = FakeMonthSummary(day_entries={
            1: [FakeDayEntry(name='=HYPERLINK("http://evil","x")')],
        })
        rows = _parse_csv(export_calendar_csv(data, "month"))
        assert rows[1][1] == '\'=HYPERLINK("http://evil","x")'

    def test_calendar_category_names_neutralized(self, app):
        """Category group ('+') and item ('@') names are quote-prefixed."""
        data = FakeMonthSummary(day_entries={
            1: [FakeDayEntry(category_group="+Home", category_item="@Rent")],
        })
        rows = _parse_csv(export_calendar_csv(data, "month"))
        assert rows[1][2] == "'+Home"
        assert rows[1][3] == "'@Rent"

    def test_ordinary_name_not_prefixed(self, app):
        """A name that does not lead with a formula char is unchanged --
        no spurious quote is added (avoids corrupting every benign name)."""
        data = FakeMonthSummary(day_entries={
            1: [FakeDayEntry(
                name="Rent", category_group="Home", category_item="Utilities",
            )],
        })
        rows = _parse_csv(export_calendar_csv(data, "month"))
        assert rows[1][1] == "Rent"
        assert rows[1][2] == "Home"
        assert rows[1][3] == "Utilities"

    def test_negative_numeric_amount_not_neutralized(self, app):
        """System-formatted negatives stay numeric (not quote-prefixed) so
        spreadsheets can aggregate them: neutralization is scoped to
        user text via _safe, never to _dec-formatted numbers."""
        data = FakeMonthSummary(day_entries={
            1: [FakeDayEntry(name="Refund", amount=Decimal("-50.00"))],
        })
        rows = _parse_csv(export_calendar_csv(data, "month"))
        # Column 4 is "Amount ($)" in the month export.
        assert rows[1][4] == "-50.00"
