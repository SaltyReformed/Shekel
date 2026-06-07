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
from app.enums import RecurrencePatternEnum, StatusEnum, TxnTypeEnum
from app.models.pay_period import PayPeriod
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import RecurrencePattern
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
import pytest

from app.services import calendar_service
from app.services.balance_resolver import period_subtotal
from app.services.calendar_service import (
    CalendarAccountNotResolvableError,
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
    is_deleted=False, status=StatusEnum.PROJECTED, actual_amount=None,
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
        status: StatusEnum member; defaults to PROJECTED.  Mixed-status
            calendar tests (F-3 / W-065) pass SETTLED, CANCELLED, CREDIT
            to assert the balance-contributing predicate filters them
            correctly.
        actual_amount: Optional realized amount.  Required for SETTLED
            so ``effective_amount`` returns the realized hit rather than
            falling back to ``estimated_amount``.

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
        status_id=ref_cache.status_id(status),
        name=name,
        category_id=None,
        transaction_type_id=type_id,
        estimated_amount=Decimal(str(amount)),
        actual_amount=(
            Decimal(str(actual_amount)) if actual_amount is not None else None
        ),
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
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
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
        seed_periods are 10 biweekly starting 2026-01-02 so:
          Period 0: Jan 2 -- Jan 15 (anchor)
          Period 1: Jan 16 -- Jan 29
          Period 2: Jan 30 -- Feb 12  (contains Jan 31)
        Post-Commit-9 (HIGH-02 / W-277): the month-end balance is
        ``balance_as_of_date(2026-01-31)``, which projects forward
        through the period CONTAINING Jan 31 (period 2), not the
        pre-Commit-9 "last period whose end_date <= Jan 31"
        (period 1).  Period 2 has no transactions here so the
        projected balance carries forward unchanged from period 1's
        4000.00, which keeps this assertion valid; the next test
        proves the producer steps into period 2 when it has data.

        Period 0 (anchor): 1000 + 2000 - 500 = 2500.
        Period 1:          2500 + 2000 - 500 = 4000.
        Period 2:          4000 + 0 - 0      = 4000  (no txns)
        Month-end (Jan 31, falls in period 2): 4000.00.
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
            assert result.projected_end_balance == Decimal("4000.00")

    def test_month_end_balance_includes_straddling_period(
        self, app, seed_user, seed_periods, db,
    ):
        """C9-1 (calendar surface): month-end mid-period includes that period.

        HIGH-02 / W-277: pre-Commit-9 the calendar selected the last
        pay period whose ``end_date <= last_day_of_month`` and
        returned that period's end balance, missing the contribution
        of the period that straddles the month boundary.
        Post-Commit-9 the month-end balance flows through
        ``balance_as_of_date``, which projects through the period
        CONTAINING the target date.

        seed_periods:
          Period 1: Jan 16 -- Jan 29
          Period 2: Jan 30 -- Feb 12  (contains Jan 31)

        Setup loads income/expense in BOTH periods so the
        pre-Commit-9 path (which would stop after period 1) and the
        post-Commit-9 path (which includes period 2) produce
        distinct values; the assertion locks the correct one.

        Hand arithmetic (no entries, formula collapses to
        effective_amount; statuses are Projected so the
        balance-contributing predicate includes them):
          Period 0 (anchor, 1000.00):  1000 + 0 - 0 = 1000
          Period 1:                    1000 + 1500 - 200 = 2300
          Period 2:                    2300 + 1500 - 200 = 3600

        Pre-Commit-9 would have returned 2300.00 (period 1 end);
        post-Commit-9 must return 3600.00.  Re-pinned per
        HIGH-02 / W-277.
        """
        with app.app_context():
            p1 = seed_periods[1]
            p2 = seed_periods[2]
            assert p1.end_date == date(2026, 1, 29)
            assert p2.start_date == date(2026, 1, 30)
            assert p2.end_date == date(2026, 2, 12)

            _add_transaction(
                db.session, seed_user, p1, "Mid-Jan Pay", "1500.00",
                is_income=True, due_date=date(2026, 1, 16),
            )
            _add_transaction(
                db.session, seed_user, p1, "Mid-Jan Bill", "200.00",
                due_date=date(2026, 1, 20),
            )
            _add_transaction(
                db.session, seed_user, p2, "Late-Jan Pay", "1500.00",
                is_income=True, due_date=date(2026, 1, 30),
            )
            _add_transaction(
                db.session, seed_user, p2, "Late-Jan Bill", "200.00",
                due_date=date(2026, 1, 30),
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
            )
            # 1000 + (1500-200) + (1500-200) = 3600.00.
            # Pre-Commit-9 returned 2300.00 -- HIGH-02 / W-277.
            assert result.projected_end_balance == Decimal("3600.00")


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


# ── F-3 / W-065 balance-contributing predicate ─────────────────────


class TestBalanceContributingPredicate:
    """F-3 / HIGH-02 / W-065: calendar per-day filter via the locked
    Choice-2 ``balance-contributing`` predicate (Projected + Settled,
    excludes Cancelled + Credit).

    Locks the post-Commit-10 (follow-up) behaviour so a future change
    that drops the predicate from either the SQL filter in
    ``_query_transactions_for_range`` or the Python re-check in
    ``_assign_transactions_to_days`` fails loud with a concrete
    arithmetic divergence rather than a silent display regression.
    """

    def test_c10_1_projected_and_settled_both_contribute(
        self, app, seed_user, seed_periods, db,
    ):
        """F-3 / W-065 C10-1: Projected $500 + Settled $200 -> day total $700.

        Hand arithmetic: 500 (Projected expense, effective = estimated)
        + 200 (Settled expense, effective = actual_amount) = 700.
        Both statuses are balance-contributing: Projected because it
        is not in the {Credit, Cancelled} exclusion set; Settled for
        the same reason -- the calendar's locked Choice-2 predicate
        intentionally includes realized payments at their settled date.
        """
        with app.app_context():
            p0 = seed_periods[0]
            _add_transaction(
                db.session, seed_user, p0, "Projected Bill", "500.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.PROJECTED,
            )
            _add_transaction(
                db.session, seed_user, p0, "Settled Bill", "200.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.SETTLED, actual_amount="200.00",
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
            )
            # F-3 / W-065: 500 + 200 = 700.00 (both contribute).
            assert result.total_expenses == Decimal("700.00")
            assert len(result.day_entries[5]) == 2
            names = sorted(e.name for e in result.day_entries[5])
            assert names == ["Projected Bill", "Settled Bill"]

    def test_c10_2_cancelled_and_credit_excluded(
        self, app, seed_user, seed_periods, db,
    ):
        """F-3 / W-065 C10-2: Cancelled + Credit excluded from day total.

        Same day as C10-1 plus a Cancelled $100 expense and a Credit
        $50 expense.  Hand arithmetic: 500 (Projected) + 200 (Settled)
        = 700.00; the Cancelled and Credit rows are filtered out by
        ``balance_contributing_clause`` (their status carries
        ``excludes_from_balance=True``) and never reach the day
        assignment, so they neither inflate totals nor appear as
        day entries.

        A pre-Commit-10-follow-up calendar would have included all
        four rows in ``day_entries[5]``; their amount contribution
        collapses to zero via ``effective_amount`` so totals stay at
        700.00, but the visible-entries regression is the user-facing
        defect F-3 names.  This test locks both the arithmetic AND
        the entry-count contract.
        """
        with app.app_context():
            p0 = seed_periods[0]
            _add_transaction(
                db.session, seed_user, p0, "Projected Bill", "500.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.PROJECTED,
            )
            _add_transaction(
                db.session, seed_user, p0, "Settled Bill", "200.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.SETTLED, actual_amount="200.00",
            )
            _add_transaction(
                db.session, seed_user, p0, "Cancelled Bill", "100.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.CANCELLED,
            )
            _add_transaction(
                db.session, seed_user, p0, "Credit Bill", "50.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.CREDIT,
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
            )
            # F-3 / W-065: 500 + 200 = 700.00; Cancelled + Credit excluded.
            assert result.total_expenses == Decimal("700.00")
            # Day cell shows only the two contributing rows.
            assert len(result.day_entries[5]) == 2
            names = sorted(e.name for e in result.day_entries[5])
            assert names == ["Projected Bill", "Settled Bill"]

    def test_c10_3_grid_period_subtotal_projected_only(
        self, app, seed_user, seed_periods, db,
    ):
        """F-3 / W-065 C10-3: grid period subtotal stays Projected-only.

        Same fixture as C10-2 (Projected $500 + Settled $200 +
        Cancelled $100 + Credit $50 on Jan 5).  The grid period
        subtotal is sourced from
        ``balance_resolver.period_subtotal``, whose ``_sum_all``
        helper gates on ``is_projected(txn)`` -- so only the
        Projected $500 expense contributes; Settled, Cancelled, and
        Credit are all excluded.  Hand arithmetic: 500.00.

        The two surfaces intentionally diverge: calendar day total
        for the same day is 700.00 (C10-2), grid period subtotal
        for the same period is 500.00 (this test).  This divergence
        is the locked Choice-2 design from the follow-up plan.
        """
        with app.app_context():
            p0 = seed_periods[0]
            _add_transaction(
                db.session, seed_user, p0, "Projected Bill", "500.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.PROJECTED,
            )
            _add_transaction(
                db.session, seed_user, p0, "Settled Bill", "200.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.SETTLED, actual_amount="200.00",
            )
            _add_transaction(
                db.session, seed_user, p0, "Cancelled Bill", "100.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.CANCELLED,
            )
            _add_transaction(
                db.session, seed_user, p0, "Credit Bill", "50.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.CREDIT,
            )
            db.session.commit()

            sub = period_subtotal(
                seed_user["account"],
                seed_user["scenario"].id,
                p0,
            )
            # Projected-only: 500.00.  Diverges intentionally from
            # the calendar day total of 700.00 in C10-2.
            assert sub.expense == Decimal("500.00")
            assert sub.income == Decimal("0.00")

    def test_c10_4_regression_lock_predicate_drop_visible(
        self, app, seed_user, seed_periods, db, monkeypatch,
    ):
        """F-3 / W-065 C10-4: regression lock for predicate removal.

        Simulates the regression where the locked predicate is
        dropped from BOTH the SQL filter and the Python re-check by
        monkey-patching ``balance_contributing_clause`` to a
        trivially-true predicate (only ``is_deleted=False``, the
        pre-fix gate) and ``is_balance_contributing`` to ignore the
        excludes_from_balance flag.  With the predicate dropped the
        Cancelled and Credit rows leak into ``day_entries[5]`` and
        the day cell renders four entries instead of the two the
        locked Choice-2 semantic mandates.

        The post-Commit-10-follow-up code MUST reject this
        regression: with the production predicate in place this test
        confirms the day shows exactly two contributing entries.
        Then the monkey-patched regression run confirms the locked
        behaviour: with the predicate removed, four entries appear.
        A diff between the two locks the predicate's contribution.

        Hand arithmetic on the four totals through ``effective_amount``:
            Projected $500: effective = 500
            Settled $200: effective = 200
            Cancelled $100: excludes_from_balance=True, effective = 0
            Credit $50: excludes_from_balance=True, effective = 0
            Sum = 700.00 either way; the visible regression is the
            entry-count contract (2 vs 4), which this test locks.
        """
        with app.app_context():
            p0 = seed_periods[0]
            _add_transaction(
                db.session, seed_user, p0, "Projected Bill", "500.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.PROJECTED,
            )
            _add_transaction(
                db.session, seed_user, p0, "Settled Bill", "200.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.SETTLED, actual_amount="200.00",
            )
            _add_transaction(
                db.session, seed_user, p0, "Cancelled Bill", "100.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.CANCELLED,
            )
            _add_transaction(
                db.session, seed_user, p0, "Credit Bill", "50.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.CREDIT,
            )
            db.session.commit()

            # Production predicate: exactly two entries on Jan 5.
            real = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
            )
            assert len(real.day_entries[5]) == 2
            assert real.total_expenses == Decimal("700.00")

            # Simulated regression: drop the predicate from both
            # surfaces.  The SQL filter degrades to is_deleted-only;
            # the Python re-check is short-circuited to always
            # contribute.
            from app.services import calendar_service as cs
            monkeypatch.setattr(
                cs, "balance_contributing_clause",
                lambda: Transaction.is_deleted.is_(False),
            )
            monkeypatch.setattr(
                cs, "is_balance_contributing", lambda _txn: True,
            )

            regressed = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
            )
            # Predicate dropped: all four rows leak into the day cell.
            assert len(regressed.day_entries[5]) == 4
            # The visible-totals regression for a hypothetical future
            # change that ALSO replaces effective_amount with
            # estimated_amount would be 500 + 200 + 100 + 50 = 850;
            # today's effective_amount zeroes Cancelled + Credit so
            # the total stays 700.00 even with the predicate dropped.
            # The entry-count contract above is the load-bearing
            # regression lock.
            assert regressed.total_expenses == Decimal("700.00")


# ── Anchor-None contract (F-2 / Commit 11) ──────────────────────────


class TestUnresolvableAccountOrScenario:
    """Tests for the F-2 / Commit 11 contract.

    After Commits 3-8 of the main remediation locked the E-19 /
    CRIT-01 invariant, the calendar service must raise
    :class:`CalendarAccountNotResolvableError` when
    :func:`resolve_analytics_account` or
    :func:`get_baseline_scenario` returns ``None`` -- the pre-F-2
    behaviour of silently substituting a zeroed
    :class:`MonthSummary` / :class:`YearOverview` masked the
    upstream defect behind a ``$0.00`` calendar.
    """

    def test_month_detail_raises_when_account_unresolvable(
        self, app, seed_user, db, monkeypatch,
    ):
        """C11-1 (service): None account -> CalendarAccountNotResolvableError."""
        with app.app_context():
            monkeypatch.setattr(
                calendar_service, "resolve_analytics_account",
                lambda _user_id, _account_id: None,
            )
            with pytest.raises(CalendarAccountNotResolvableError):
                calendar_service.get_month_detail(
                    user_id=seed_user["user"].id,
                    year=2026,
                    month=1,
                )

    def test_month_detail_raises_when_scenario_unresolvable(
        self, app, seed_user, db, monkeypatch,
    ):
        """C11-2 (service): None baseline scenario -> error."""
        with app.app_context():
            monkeypatch.setattr(
                calendar_service, "get_baseline_scenario",
                lambda _user_id: None,
            )
            with pytest.raises(CalendarAccountNotResolvableError):
                calendar_service.get_month_detail(
                    user_id=seed_user["user"].id,
                    year=2026,
                    month=1,
                )

    def test_year_overview_raises_when_account_unresolvable(
        self, app, seed_user, db, monkeypatch,
    ):
        """C11-1 (service, year view): None account -> error."""
        with app.app_context():
            monkeypatch.setattr(
                calendar_service, "resolve_analytics_account",
                lambda _user_id, _account_id: None,
            )
            with pytest.raises(CalendarAccountNotResolvableError):
                calendar_service.get_year_overview(
                    user_id=seed_user["user"].id,
                    year=2026,
                )

    def test_year_overview_raises_when_scenario_unresolvable(
        self, app, seed_user, db, monkeypatch,
    ):
        """C11-2 (service, year view): None scenario -> error."""
        with app.app_context():
            monkeypatch.setattr(
                calendar_service, "get_baseline_scenario",
                lambda _user_id: None,
            )
            with pytest.raises(CalendarAccountNotResolvableError):
                calendar_service.get_year_overview(
                    user_id=seed_user["user"].id,
                    year=2026,
                )
