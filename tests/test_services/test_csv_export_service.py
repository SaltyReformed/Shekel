"""
Tests for csv_export_service.py -- Commit 17.

Verifies CSV generation for all four analytics export functions.
Tests cover header correctness, data formatting, edge cases
(None, special characters, decimal precision), and CSV parseability.
"""

import csv
import io
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import pytest

from app.services.csv_export_service import (
    export_calendar_csv,
    export_trends_csv,
    export_variance_csv,
    export_year_end_csv,
)


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
    large_transactions: list = field(default_factory=list)
    day_entries: dict = field(default_factory=dict)
    paycheck_days: list = field(default_factory=list)


@dataclass(frozen=True)
class FakeYearOverview:
    """Minimal YearOverview for CSV tests."""
    year: int = 2026
    months: list = field(default_factory=list)
    annual_income: Decimal = Decimal("36000.00")
    annual_expenses: Decimal = Decimal("24000.00")
    annual_net: Decimal = Decimal("12000.00")


@dataclass(frozen=True)
class FakeTransactionVariance:
    """Minimal TransactionVariance for CSV tests."""
    transaction_id: int = 1
    name: str = "Rent Payment"
    estimated: Decimal = Decimal("1200.00")
    actual: Decimal = Decimal("1200.00")
    variance: Decimal = Decimal("0.00")
    variance_pct: Decimal | None = Decimal("0.00")
    is_paid: bool = True
    due_date: date | None = None


@dataclass(frozen=True)
class FakeCategoryItemVariance:
    """Minimal CategoryItemVariance for CSV tests."""
    category_id: int = 1
    group_name: str = "Home"
    item_name: str = "Rent"
    estimated_total: Decimal = Decimal("1200.00")
    actual_total: Decimal = Decimal("1200.00")
    variance: Decimal = Decimal("0.00")
    variance_pct: Decimal | None = Decimal("0.00")
    transaction_count: int = 1
    transactions: list = field(default_factory=list)


@dataclass(frozen=True)
class FakeCategoryGroupVariance:
    """Minimal CategoryGroupVariance for CSV tests."""
    group_name: str = "Home"
    estimated_total: Decimal = Decimal("1200.00")
    actual_total: Decimal = Decimal("1200.00")
    variance: Decimal = Decimal("0.00")
    variance_pct: Decimal | None = Decimal("0.00")
    items: list = field(default_factory=list)


@dataclass(frozen=True)
class FakeVarianceReport:
    """Minimal VarianceReport for CSV tests."""
    window_type: str = "pay_period"
    window_label: str = "Jan 02 - Jan 15, 2026"
    groups: list = field(default_factory=list)
    total_estimated: Decimal = Decimal("0.00")
    total_actual: Decimal = Decimal("0.00")
    total_variance: Decimal = Decimal("0.00")
    total_variance_pct: Decimal | None = None
    transaction_count: int = 0


@dataclass(frozen=True)
class FakeItemTrend:
    """Minimal ItemTrend for CSV tests."""
    category_id: int = 1
    group_name: str = "Home"
    item_name: str = "Rent"
    period_average: Decimal = Decimal("500.00")
    trend_direction: str = "up"
    pct_change: Decimal = Decimal("10.50")
    absolute_change: Decimal = Decimal("50.00")
    is_flagged: bool = True
    data_points: int = 10
    total_spending: Decimal = Decimal("5000.00")
    avg_days_before_due: Decimal | None = None


@dataclass(frozen=True)
class FakeTrendReport:
    """Minimal TrendReport for CSV tests."""
    window_months: int = 6
    window_periods: int = 13
    top_increasing: list = field(default_factory=list)
    top_decreasing: list = field(default_factory=list)
    all_items: list = field(default_factory=list)
    group_trends: list = field(default_factory=list)
    data_sufficiency: str = "sufficient"
    threshold: Decimal = Decimal("0.1000")


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


# ── Year-End Tests ────────────────────────────────────────────────


class TestYearEndExport:
    """Tests for export_year_end_csv()."""

    def test_export_year_end_sections(self, app):
        """C17-svc4: Year-end CSV contains all section headers."""
        data = _build_year_end_data()
        result = export_year_end_csv(data)
        assert "[Income and Taxes]" in result
        assert "[Spending by Category]" in result
        assert "[Transfers]" in result
        assert "[Net Worth Monthly]" in result
        assert "[Debt Progress]" in result
        assert "[Savings Progress]" in result

    def test_export_year_end_payment_timeliness(self, app):
        """C17-extra9: Payment Timeliness section present with OP-2 data."""
        data = _build_year_end_data(with_timeliness=True)
        result = export_year_end_csv(data)
        assert "[Payment Timeliness]" in result
        assert "Total Bills Paid" in result

    def test_export_year_end_no_timeliness(self, app):
        """C17-extra10: No crash and no section when OP-2 data is None."""
        data = _build_year_end_data(with_timeliness=False)
        result = export_year_end_csv(data)
        assert "[Payment Timeliness]" not in result


# ── Variance Tests ────────────────────────────────────────────────


class TestVarianceExport:
    """Tests for export_variance_csv()."""

    def test_export_variance_hierarchy(self, app):
        """C17-svc5: CSV has Group, Item, and Transaction level rows."""
        report = _build_variance_report()
        result = export_variance_csv(report)
        rows = _parse_csv(result)
        levels = [r[0] for r in rows[1:]]  # skip header
        assert "Group" in levels
        assert "Item" in levels
        assert "Transaction" in levels

    def test_export_variance_totals_row(self, app):
        """C17-svc6: Last data row is 'Total'."""
        report = _build_variance_report()
        result = export_variance_csv(report)
        rows = _parse_csv(result)
        last = rows[-1]
        assert last[0] == "Total"

    def test_export_variance_pct_none(self, app):
        """C17-extra8: None variance_pct exported as empty string."""
        txn = FakeTransactionVariance(variance_pct=None)
        item = FakeCategoryItemVariance(
            transactions=[txn], variance_pct=None,
        )
        group = FakeCategoryGroupVariance(
            items=[item], variance_pct=None,
        )
        report = FakeVarianceReport(
            groups=[group],
            total_variance_pct=None,
        )
        result = export_variance_csv(report)
        assert "None" not in result


# ── Trends Tests ──────────────────────────────────────────────────


class TestTrendsExport:
    """Tests for export_trends_csv()."""

    def test_export_trends_metadata(self, app):
        """C17-svc7: First row contains window months and threshold."""
        report = FakeTrendReport(window_months=6, threshold=Decimal("0.1000"))
        result = export_trends_csv(report)
        rows = _parse_csv(result)
        assert rows[0][0] == "Window"
        assert "6 months" in rows[0][1]
        assert "10.00%" in rows[0][3]

    def test_export_trends_all_items(self, app):
        """C17-svc8: CSV has correct number of item rows."""
        items = [
            FakeItemTrend(item_name=f"Item {i}") for i in range(5)
        ]
        report = FakeTrendReport(all_items=items)
        result = export_trends_csv(report)
        rows = _parse_csv(result)
        # metadata + header + 5 items
        assert len(rows) == 7


# ── Cross-Cutting Tests ──────────────────────────────────────────


class TestCsvFormatting:
    """Tests for CSV formatting rules across all export functions."""

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
        """C17-extra11: Output is parseable by csv.reader."""
        report = _build_variance_report()
        result = export_variance_csv(report)
        rows = _parse_csv(result)
        assert len(rows) > 1
        # All rows should have the same number of columns.
        col_count = len(rows[0])
        for row in rows:
            assert len(row) == col_count, (
                f"Row has {len(row)} cols, expected {col_count}: {row}"
            )


# ── Helpers ───────────────────────────────────────────────────────


def _build_year_end_data(with_timeliness=False):
    """Build a minimal year-end summary dict for testing."""
    monthly_values = [
        {"month": m, "month_name": f"Month{m}", "balance": Decimal("1000.00")}
        for m in range(1, 13)
    ]
    data = {
        "income_tax": {
            "gross_wages": Decimal("75000.00"),
            "federal_tax": Decimal("6000.00"),
            "state_tax": Decimal("3000.00"),
            "social_security_tax": Decimal("4650.00"),
            "medicare_tax": Decimal("1087.50"),
            "pretax_deductions": [
                {"name": "401k", "annual_total": Decimal("5200.00")},
            ],
            "posttax_deductions": [],
            "total_pretax": Decimal("5200.00"),
            "total_posttax": Decimal("0.00"),
            "net_pay_total": Decimal("55062.50"),
            "mortgage_interest_total": Decimal("15000.00"),
        },
        "spending_by_category": [
            {
                "group_name": "Home",
                "group_total": Decimal("14400.00"),
                "items": [
                    {"item_name": "Rent", "item_total": Decimal("14400.00")},
                ],
            },
        ],
        "transfers_summary": [
            {
                "destination_account": "Savings",
                "destination_account_id": 2,
                "total_amount": Decimal("6000.00"),
            },
        ],
        "net_worth": {
            "monthly_values": monthly_values,
            "jan1": Decimal("10000.00"),
            "dec31": Decimal("22000.00"),
            "delta": Decimal("12000.00"),
        },
        "debt_progress": [
            {
                "account_name": "Mortgage",
                "account_id": 3,
                "jan1_balance": Decimal("240000.00"),
                "dec31_balance": Decimal("235000.00"),
                "principal_paid": Decimal("5000.00"),
            },
        ],
        "savings_progress": [
            {
                "account_name": "Savings",
                "account_id": 2,
                "jan1_balance": Decimal("500.00"),
                "dec31_balance": Decimal("6500.00"),
                "total_contributions": Decimal("6000.00"),
            },
        ],
        "payment_timeliness": None,
    }
    if with_timeliness:
        data["payment_timeliness"] = {
            "total_bills_paid": 24,
            "paid_on_time": 22,
            "paid_late": 2,
            "avg_days_before_due": Decimal("3.50"),
        }
    return data


def _build_variance_report():
    """Build a VarianceReport with one group, one item, one txn."""
    txn = FakeTransactionVariance(
        name="Jan Rent",
        estimated=Decimal("1200.00"),
        actual=Decimal("1250.00"),
        variance=Decimal("50.00"),
        variance_pct=Decimal("4.17"),
    )
    item = FakeCategoryItemVariance(
        estimated_total=Decimal("1200.00"),
        actual_total=Decimal("1250.00"),
        variance=Decimal("50.00"),
        variance_pct=Decimal("4.17"),
        transactions=[txn],
    )
    group = FakeCategoryGroupVariance(
        estimated_total=Decimal("1200.00"),
        actual_total=Decimal("1250.00"),
        variance=Decimal("50.00"),
        variance_pct=Decimal("4.17"),
        items=[item],
    )
    return FakeVarianceReport(
        groups=[group],
        total_estimated=Decimal("1200.00"),
        total_actual=Decimal("1250.00"),
        total_variance=Decimal("50.00"),
        total_variance_pct=Decimal("4.17"),
        transaction_count=1,
    )
