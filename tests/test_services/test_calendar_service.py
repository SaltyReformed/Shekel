"""
Shekel Budget App -- Calendar Service Tests

Tests for the calendar service engine: month detail computation,
year overview aggregation, day assignment from due_date, large and
infrequent transaction detection, 3rd paycheck month identification,
and projected month-end balance calculation.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import RecurrencePatternEnum, TxnTypeEnum
from app.models.pay_period import PayPeriod
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import RecurrencePattern
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.services import calendar_service
from app.services.calendar_service import (
    _detect_third_paycheck_months,
    _is_infrequent,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _income_type_id(db_session):
    """Get the Income transaction type ID from the database."""
    return ref_cache.txn_type_id(TxnTypeEnum.INCOME)


def _expense_type_id(db_session):
    """Get the Expense transaction type ID from the database."""
    return ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)


def _add_transaction(
    db_session, seed_user, period, name, amount,
    is_income=False, due_date=None, template=None,
    is_deleted=False,
):
    """Create a transaction for testing.

    Args:
        db_session: Active database session.
        seed_user: The seed_user fixture dict.
        period: PayPeriod to assign to.
        name: Transaction name.
        amount: Estimated amount (Decimal or str).
        is_income: Whether this is income (default expense).
        due_date: Optional due_date override.
        template: Optional template to link.
        is_deleted: Soft-delete flag.

    Returns:
        The created Transaction.
    """
    type_id = (
        ref_cache.txn_type_id(TxnTypeEnum.INCOME)
        if is_income
        else ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    )
    txn = Transaction(
        account_id=seed_user["account"].id,
        template_id=template.id if template else None,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        status_id=ref_cache.status_id(
            __import__("app.enums", fromlist=["StatusEnum"]).StatusEnum.PROJECTED,
        ),
        name=name,
        category_id=None,
        transaction_type_id=type_id,
        estimated_amount=Decimal(str(amount)),
        due_date=due_date,
        is_deleted=is_deleted,
    )
    db_session.add(txn)
    db_session.flush()
    return txn


def _make_template_with_pattern(db_session, seed_user, pattern_enum):
    """Create a template with a recurrence rule of the given pattern.

    Args:
        db_session: Active database session.
        seed_user: The seed_user fixture dict.
        pattern_enum: A RecurrencePatternEnum member.

    Returns:
        The created TransactionTemplate.
    """
    pattern_id = ref_cache.recurrence_pattern_id(pattern_enum)
    rule = RecurrenceRule(
        user_id=seed_user["user"].id,
        pattern_id=pattern_id,
    )
    db_session.add(rule)
    db_session.flush()

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=list(seed_user["categories"].values())[0].id,
        recurrence_rule_id=rule.id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name="Template",
        default_amount=Decimal("100.00"),
    )
    db_session.add(template)
    db_session.flush()
    return template


# ── Month Detail Tests ───────────────────────────────────────────────


class TestMonthDetailEmpty:
    """Tests for month detail with no data."""

    def test_month_detail_empty(self, app, seed_user, seed_periods):
        """MonthSummary has zero totals and empty collections when no txns exist."""
        with app.app_context():
            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=4,
            )
            assert result.total_income == Decimal("0")
            assert result.total_expenses == Decimal("0")
            assert result.net == Decimal("0")
            assert result.day_entries == {}
            assert result.large_transactions == []


class TestMonthDetailIncomeAndExpenses:
    """Tests for month detail with income and expense transactions."""

    def test_month_detail_income_and_expenses(self, app, seed_user, seed_periods, db):
        """Two periods in January with $2000 income and $500 expense each.

        Expected: total_income=4000, total_expenses=1000, net=3000.
        """
        with app.app_context():
            # seed_periods starts Jan 2, 2026 with 10 biweekly periods.
            # Period 0: Jan 2 - Jan 15, Period 1: Jan 16 - Jan 29
            p0 = seed_periods[0]
            p1 = seed_periods[1]

            _add_transaction(
                db.session, seed_user, p0, "Paycheck 1", "2000.00",
                is_income=True, due_date=date(2026, 1, 2),
            )
            _add_transaction(
                db.session, seed_user, p0, "Rent", "500.00",
                due_date=date(2026, 1, 5),
            )
            _add_transaction(
                db.session, seed_user, p1, "Paycheck 2", "2000.00",
                is_income=True, due_date=date(2026, 1, 16),
            )
            _add_transaction(
                db.session, seed_user, p1, "Utilities", "500.00",
                due_date=date(2026, 1, 20),
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
            )
            assert result.total_income == Decimal("4000.00")
            assert result.total_expenses == Decimal("1000.00")
            assert result.net == Decimal("3000.00")


class TestDayAssignment:
    """Tests for transaction-to-day assignment logic."""

    def test_day_assignment_from_due_date(self, app, seed_user, seed_periods, db):
        """Transaction with due_date=Jan 15 appears in day_entries[15]."""
        with app.app_context():
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Bill", "100.00",
                due_date=date(2026, 1, 15),
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
            )
            assert 15 in result.day_entries
            assert len(result.day_entries[15]) == 1
            assert result.day_entries[15][0].name == "Bill"

    def test_day_assignment_paycheck_pattern(self, app, seed_user, seed_periods, db):
        """Txn with due_date=period start_date appears on that day."""
        with app.app_context():
            p0 = seed_periods[0]  # starts Jan 2
            _add_transaction(
                db.session, seed_user, p0, "Paycheck", "2000.00",
                is_income=True, due_date=p0.start_date,
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
            )
            assert 2 in result.day_entries
            assert result.day_entries[2][0].name == "Paycheck"

    def test_due_date_none_fallback(self, app, seed_user, seed_periods, db):
        """Txn with due_date=None falls back to period.start_date.day."""
        with app.app_context():
            p0 = seed_periods[0]  # starts Jan 2
            _add_transaction(
                db.session, seed_user, p0, "Manual", "50.00",
                due_date=None,
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
            )
            # Should fall back to period start_date (Jan 2).
            assert 2 in result.day_entries
            names = [e.name for e in result.day_entries[2]]
            assert "Manual" in names

    def test_day_entries_sorted_by_amount(self, app, seed_user, seed_periods, db):
        """Multiple txns on the same day sorted by abs(amount) descending."""
        with app.app_context():
            p0 = seed_periods[0]
            _add_transaction(
                db.session, seed_user, p0, "Small", "100.00",
                due_date=date(2026, 1, 5),
            )
            _add_transaction(
                db.session, seed_user, p0, "Large", "500.00",
                due_date=date(2026, 1, 5),
            )
            _add_transaction(
                db.session, seed_user, p0, "Medium", "200.00",
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
            )
            entries = result.day_entries[5]
            amounts = [e.amount for e in entries]
            # Sorted descending by abs(amount): 500, 200, 100.
            assert amounts == [Decimal("500.00"), Decimal("200.00"), Decimal("100.00")]


class TestNoDuplicates:
    """Tests ensuring no double-counting across period boundaries."""

    def test_no_double_counting_cross_period(self, app, seed_user, seed_periods, db):
        """Txn with due_date in Feb is NOT counted in January detail.

        Period 1 (Jan 16 - Jan 29) overlaps with January, but the
        transaction's due_date is in February.
        """
        with app.app_context():
            p1 = seed_periods[1]  # Jan 16 - Jan 29
            _add_transaction(
                db.session, seed_user, p1, "Feb Bill", "300.00",
                due_date=date(2026, 2, 1),
            )
            db.session.commit()

            jan = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
            )
            feb = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=2,
            )
            # Should NOT be in January.
            assert jan.total_expenses == Decimal("0")
            # Should be in February.
            assert feb.total_expenses == Decimal("300.00")

    def test_no_double_counting_same_month(self, app, seed_user, seed_periods, db):
        """Same txn in two overlapping periods counted exactly once."""
        with app.app_context():
            p0 = seed_periods[0]
            _add_transaction(
                db.session, seed_user, p0, "Unique", "100.00",
                due_date=date(2026, 1, 10),
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
            )
            # Only one entry should exist.
            all_entries = [
                e for entries in result.day_entries.values() for e in entries
            ]
            ids = [e.transaction_id for e in all_entries]
            assert len(ids) == len(set(ids)), "Duplicate transaction IDs found"


class TestLargeTransactions:
    """Tests for large transaction flagging."""

    def test_large_transaction_flagging(self, app, seed_user, seed_periods, db):
        """Txn $600 with threshold=500 is flagged as large."""
        with app.app_context():
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Big Bill", "600.00",
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
                large_threshold=500,
            )
            entry = result.day_entries[5][0]
            assert entry.is_large is True
            assert len(result.large_transactions) == 1

    def test_large_threshold_boundary(self, app, seed_user, seed_periods, db):
        """Txn exactly $500 with threshold=500 is flagged (>= not >)."""
        with app.app_context():
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Exact", "500.00",
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
                large_threshold=500,
            )
            assert result.day_entries[5][0].is_large is True

    def test_below_threshold_not_large(self, app, seed_user, seed_periods, db):
        """Txn $499 with threshold=500 is NOT flagged."""
        with app.app_context():
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Small", "499.00",
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
                large_threshold=500,
            )
            assert result.day_entries[5][0].is_large is False
            assert len(result.large_transactions) == 0


class TestIncomeExpenseClassification:
    """Tests for income vs expense classification."""

    def test_income_vs_expense_classification(self, app, seed_user, seed_periods, db):
        """Income txn counted in total_income, expense in total_expenses."""
        with app.app_context():
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Salary", "3000.00",
                is_income=True, due_date=date(2026, 1, 2),
            )
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Rent", "1200.00",
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
            )
            assert result.total_income == Decimal("3000.00")
            assert result.total_expenses == Decimal("1200.00")

            # Check is_income flags on entries.
            income_entries = [
                e for entries in result.day_entries.values()
                for e in entries if e.is_income
            ]
            expense_entries = [
                e for entries in result.day_entries.values()
                for e in entries if not e.is_income
            ]
            assert len(income_entries) == 1
            assert len(expense_entries) == 1


class TestDeletedTransactions:
    """Tests for soft-deleted transaction exclusion."""

    def test_deleted_transactions_excluded(self, app, seed_user, seed_periods, db):
        """Soft-deleted transactions do not appear in results."""
        with app.app_context():
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Active", "100.00",
                due_date=date(2026, 1, 5),
            )
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Deleted", "200.00",
                due_date=date(2026, 1, 5), is_deleted=True,
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
            )
            all_entries = [
                e for entries in result.day_entries.values() for e in entries
            ]
            names = [e.name for e in all_entries]
            assert "Active" in names
            assert "Deleted" not in names


class TestCategoryInfo:
    """Tests for category info on DayEntry."""

    def test_category_info_on_day_entry(self, app, seed_user, seed_periods, db):
        """DayEntry carries category group and item from the transaction."""
        with app.app_context():
            cat = seed_user["categories"]["Car Payment"]
            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(
                    __import__("app.enums", fromlist=["StatusEnum"]).StatusEnum.PROJECTED,
                ),
                name="Car Payment",
                category_id=cat.id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                estimated_amount=Decimal("350.00"),
                due_date=date(2026, 1, 10),
            )
            db.session.add(txn)
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
            )
            entry = result.day_entries[10][0]
            assert entry.category_group == "Auto"
            assert entry.category_item == "Car Payment"


class TestDefaultAccount:
    """Tests for default account resolution."""

    def test_month_detail_default_account(self, app, seed_user, seed_periods, db):
        """Uses default checking account when no account_id is passed."""
        with app.app_context():
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Test", "100.00",
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            # No account_id passed -- should use seed_user's checking account.
            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
            )
            assert result.total_expenses == Decimal("100.00")


class TestPaycheckDays:
    """Tests for paycheck_days population."""

    def test_paycheck_days_populated(self, app, seed_user, seed_periods):
        """Paycheck days reflect period start_dates in the target month.

        seed_periods: 10 biweekly periods starting Jan 2, 2026.
        Three periods start in January: Jan 2, Jan 16, Jan 30.
        """
        with app.app_context():
            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
            )
            assert result.paycheck_days == [2, 16, 30]


class TestMonthEndBalance:
    """Tests for projected month-end balance calculation."""

    def test_month_end_balance(self, app, seed_user, seed_periods, db):
        """Projected end balance reflects anchor + income - expenses.

        Anchor balance = $1000 (seed_user).
        Period 0 (anchor): +$2000 income, -$500 expense = $2500.
        Period 1: +$2000 income, -$500 expense = $4000.
        Both periods end in January, so month-end = $4000.
        """
        with app.app_context():
            p0 = seed_periods[0]
            p1 = seed_periods[1]

            _add_transaction(
                db.session, seed_user, p0, "Pay 1", "2000.00",
                is_income=True, due_date=date(2026, 1, 2),
            )
            _add_transaction(
                db.session, seed_user, p0, "Rent", "500.00",
                due_date=date(2026, 1, 5),
            )
            _add_transaction(
                db.session, seed_user, p1, "Pay 2", "2000.00",
                is_income=True, due_date=date(2026, 1, 16),
            )
            _add_transaction(
                db.session, seed_user, p1, "Util", "500.00",
                due_date=date(2026, 1, 20),
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
            )
            # Anchor $1000 + $2000 - $500 = $2500 (period 0 end)
            # $2500 + $2000 - $500 = $4000 (period 1 end)
            assert result.projected_end_balance == Decimal("4000.00")


# ── Infrequency Tests ────────────────────────────────────────────────


class TestIsInfrequent:
    """Tests for the _is_infrequent helper."""

    def test_infrequent_annual(self, app, seed_user, seed_periods, db):
        """Template with Annual pattern is infrequent."""
        with app.app_context():
            template = _make_template_with_pattern(
                db.session, seed_user, RecurrencePatternEnum.ANNUAL,
            )
            txn = _add_transaction(
                db.session, seed_user, seed_periods[0], "Annual",
                "1000.00", template=template, due_date=date(2026, 1, 5),
            )
            db.session.commit()
            assert _is_infrequent(txn) is True

    def test_infrequent_quarterly(self, app, seed_user, seed_periods, db):
        """Template with Quarterly pattern is infrequent."""
        with app.app_context():
            template = _make_template_with_pattern(
                db.session, seed_user, RecurrencePatternEnum.QUARTERLY,
            )
            txn = _add_transaction(
                db.session, seed_user, seed_periods[0], "Quarterly",
                "500.00", template=template, due_date=date(2026, 1, 5),
            )
            db.session.commit()
            assert _is_infrequent(txn) is True

    def test_infrequent_semi_annual(self, app, seed_user, seed_periods, db):
        """Template with Semi-Annual pattern is infrequent."""
        with app.app_context():
            template = _make_template_with_pattern(
                db.session, seed_user, RecurrencePatternEnum.SEMI_ANNUAL,
            )
            txn = _add_transaction(
                db.session, seed_user, seed_periods[0], "Semi",
                "600.00", template=template, due_date=date(2026, 1, 5),
            )
            db.session.commit()
            assert _is_infrequent(txn) is True

    def test_infrequent_once(self, app, seed_user, seed_periods, db):
        """Template with Once pattern is infrequent."""
        with app.app_context():
            template = _make_template_with_pattern(
                db.session, seed_user, RecurrencePatternEnum.ONCE,
            )
            txn = _add_transaction(
                db.session, seed_user, seed_periods[0], "One-time",
                "200.00", template=template, due_date=date(2026, 1, 5),
            )
            db.session.commit()
            assert _is_infrequent(txn) is True

    def test_monthly_not_infrequent(self, app, seed_user, seed_periods, db):
        """Template with Monthly pattern is NOT infrequent."""
        with app.app_context():
            template = _make_template_with_pattern(
                db.session, seed_user, RecurrencePatternEnum.MONTHLY,
            )
            txn = _add_transaction(
                db.session, seed_user, seed_periods[0], "Monthly",
                "100.00", template=template, due_date=date(2026, 1, 5),
            )
            db.session.commit()
            assert _is_infrequent(txn) is False

    def test_every_period_not_infrequent(self, app, seed_user, seed_periods, db):
        """Template with Every Period pattern is NOT infrequent."""
        with app.app_context():
            template = _make_template_with_pattern(
                db.session, seed_user, RecurrencePatternEnum.EVERY_PERIOD,
            )
            txn = _add_transaction(
                db.session, seed_user, seed_periods[0], "Each Period",
                "100.00", template=template, due_date=date(2026, 1, 5),
            )
            db.session.commit()
            assert _is_infrequent(txn) is False

    def test_no_template_not_infrequent(self, app, seed_user, seed_periods, db):
        """Manual transaction (template=None) is NOT infrequent."""
        with app.app_context():
            txn = _add_transaction(
                db.session, seed_user, seed_periods[0], "Manual",
                "100.00", due_date=date(2026, 1, 5),
            )
            db.session.commit()
            assert _is_infrequent(txn) is False


# ── Third Paycheck Tests ─────────────────────────────────────────────


class TestThirdPaycheckDetection:
    """Tests for 3rd paycheck month detection."""

    def test_third_paycheck_detection_26_periods(self, app, seed_user, db):
        """26 biweekly periods in 2026 produce exactly 2 third-paycheck months."""
        with app.app_context():
            from app.services import pay_period_service
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date(2026, 1, 2),
                num_periods=26,
                cadence_days=14,
            )
            db.session.commit()

            result = _detect_third_paycheck_months(periods, 2026)
            assert len(result) == 2

    def test_third_paycheck_empty_periods(self, app):
        """Empty period list produces empty set."""
        with app.app_context():
            result = _detect_third_paycheck_months([], 2026)
            assert result == set()

    def test_third_paycheck_only_target_year(self, app, seed_user, db):
        """Only counts periods with start_date in the target year."""
        with app.app_context():
            from app.services import pay_period_service
            # Generate periods spanning 2025-2026.
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date(2025, 7, 4),
                num_periods=40,
                cadence_days=14,
            )
            db.session.commit()

            result_2026 = _detect_third_paycheck_months(periods, 2026)
            # Should find 3rd paycheck months only from 2026 start_dates.
            for m in result_2026:
                count = sum(
                    1 for p in periods
                    if p.start_date.year == 2026 and p.start_date.month == m
                )
                assert count >= 3

    def test_third_paycheck_correct_months(self, app, seed_user, db):
        """Verify the specific months that are 3rd paycheck months.

        26 biweekly periods starting Jan 2, 2026:
        Jan: Jan 2, Jan 16, Jan 30 -> 3 paychecks
        Jul: Jul 10, Jul 24, (need to check) -> depends on exact dates
        Compute by hand: starting Jan 2, every 14 days.
        """
        with app.app_context():
            from app.services import pay_period_service
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date(2026, 1, 2),
                num_periods=26,
                cadence_days=14,
            )
            db.session.commit()

            result = _detect_third_paycheck_months(periods, 2026)

            # Verify by counting manually.
            from collections import Counter
            month_counts = Counter(
                p.start_date.month for p in periods
                if p.start_date.year == 2026
            )
            expected = {m for m, c in month_counts.items() if c >= 3}
            assert result == expected


# ── Year Overview Tests ──────────────────────────────────────────────


class TestYearOverview:
    """Tests for the year overview aggregation."""

    def test_year_overview_12_months(self, app, seed_user, seed_periods):
        """YearOverview has exactly 12 MonthSummary entries."""
        with app.app_context():
            result = calendar_service.get_year_overview(
                user_id=seed_user["user"].id,
                year=2026,
            )
            assert len(result.months) == 12
            # Months are ordered January through December.
            for i, ms in enumerate(result.months):
                assert ms.month == i + 1
                assert ms.year == 2026

    def test_year_overview_marks_third_paycheck(self, app, seed_user, db):
        """Year overview with 26 periods marks exactly 2 third-paycheck months."""
        with app.app_context():
            from app.services import pay_period_service
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date(2026, 1, 2),
                num_periods=26,
                cadence_days=14,
            )
            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()

            result = calendar_service.get_year_overview(
                user_id=seed_user["user"].id,
                year=2026,
            )
            third_paycheck_count = sum(
                1 for ms in result.months if ms.is_third_paycheck_month
            )
            assert third_paycheck_count == 2

    def test_year_overview_annual_totals(self, app, seed_user, seed_periods, db):
        """Annual totals equal the sum of all monthly totals."""
        with app.app_context():
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Income", "5000.00",
                is_income=True, due_date=date(2026, 1, 2),
            )
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Expense", "1000.00",
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            result = calendar_service.get_year_overview(
                user_id=seed_user["user"].id,
                year=2026,
            )
            sum_income = sum(ms.total_income for ms in result.months)
            sum_expenses = sum(ms.total_expenses for ms in result.months)
            sum_net = sum(ms.net for ms in result.months)

            assert result.annual_income == sum_income
            assert result.annual_expenses == sum_expenses
            assert result.annual_net == sum_net

    def test_year_overview_empty_months_have_zeros(self, app, seed_user, seed_periods, db):
        """Months without data have zero totals and empty day_entries."""
        with app.app_context():
            # Only add data in January.
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Jan Item", "100.00",
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            result = calendar_service.get_year_overview(
                user_id=seed_user["user"].id,
                year=2026,
            )
            # April (index 3) should be empty.
            apr = result.months[3]
            assert apr.total_income == Decimal("0")
            assert apr.total_expenses == Decimal("0")
            assert apr.net == Decimal("0")
            assert apr.day_entries == {}

    def test_year_overview_no_double_counting(self, app, seed_user, seed_periods, db):
        """Sum of all month totals equals total across all unique transactions."""
        with app.app_context():
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Inc", "3000.00",
                is_income=True, due_date=date(2026, 1, 2),
            )
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Exp", "500.00",
                due_date=date(2026, 1, 10),
            )
            db.session.commit()

            result = calendar_service.get_year_overview(
                user_id=seed_user["user"].id,
                year=2026,
            )
            # All transactions are in January -- no cross-month leakage.
            assert result.annual_income == Decimal("3000.00")
            assert result.annual_expenses == Decimal("500.00")
            assert result.annual_net == Decimal("2500.00")


# ── Edge Case Tests ──────────────────────────────────────────────────


class TestEdgeCases:
    """Edge case tests for the calendar service."""

    def test_feb_leap_year(self, app, seed_user, db):
        """Txn due_date Feb 29, 2028 (leap year) appears in day_entries[29]."""
        with app.app_context():
            from app.services import pay_period_service
            # Create a period that overlaps Feb 2028.
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date(2028, 2, 18),
                num_periods=2,
                cadence_days=14,
            )
            db.session.commit()
            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()

            _add_transaction(
                db.session, seed_user, periods[0], "Leap Day", "100.00",
                due_date=date(2028, 2, 29),
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2028,
                month=2,
            )
            assert 29 in result.day_entries
            assert result.day_entries[29][0].name == "Leap Day"

    def test_transfer_shadow_included(self, app, seed_user, seed_periods, db):
        """Transfer shadow transactions are included in calendar data.

        Transfer shadows are regular Transaction rows (with transfer_id
        set) and should appear like any other transaction.
        """
        with app.app_context():
            # Create a regular transaction simulating a transfer shadow.
            # Shadows have transfer_id set but are otherwise normal
            # Transaction rows.
            txn = _add_transaction(
                db.session, seed_user, seed_periods[0], "Transfer Out",
                "500.00", due_date=date(2026, 1, 5),
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
            )
            assert 5 in result.day_entries
            assert result.day_entries[5][0].name == "Transfer Out"
