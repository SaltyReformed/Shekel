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
from app.exceptions import BaselineMissingError
from app.enums import StatusEnum, TxnTypeEnum
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from unittest.mock import patch

import pytest

from app.services import (
    balance_at,
    calendar_infrequency,
    calendar_service,
    pay_period_write,
    pay_schedule_service,
)
from tests._test_helpers import settlement_columns
from tests._test_helpers import default_settle_day, make_cadence_rule
from tests.oracles.recurrence_baseline import (
    EVERY_PERIOD,
    EVERY_N_PERIODS,
    MONTHLY,
    QUARTERLY,
    SEMI_ANNUAL,
    ANNUAL,
)
from app.services.balance_at import BalanceContext
from app.services.balance_at import _context as resolution_context
from app.services.calendar_infrequency import is_infrequent as _is_infrequent
from app.services.calendar_service import (
    CalendarAccountNotResolvableError,
    DailyView,
    _detect_third_paycheck_months,
)
from app.services.pay_calendar import PayCadence, PeriodWindow, calendar_for

#: The cadence ``seed_periods`` builds: 14 days between paydays, 26 a year.
#: An explicit input to the infrequent badge since plan step R7a-2b, where the
#: predicate was an enumerated set of pattern names that could not vary by
#: owner at all.
_BIWEEKLY = PayCadence(cadence_days=14)

#: A monthly-paid owner: 30 days between paydays, 12 a year.  The cadence that
#: makes "every 2 paychecks" a DIFFERENT answer from the biweekly one.
_MONTHLY_PAID = PayCadence(cadence_days=30)


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
    is_deleted=False, status=StatusEnum.PROJECTED, settled_amount=None,
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
    status_id = ref_cache.status_id(status)
    txn = Transaction(
        account_id=seed_user["account"].id,
        template_id=template.id if template else None,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        status_id=status_id,
        # A settled row must carry the day its money moved, and the rule for a
        # BARE-built fixture row is shared with ``_test_helpers.add_txn`` rather
        # than restated (plan step X-f1).
        settled_on=default_settle_day(period, status_id),
        name=name,
        category_id=None,
        transaction_type_id=type_id,
        estimated_amount=Decimal(str(amount)),
        **settlement_columns(
            default_settle_day(period, status_id), amount, settled_amount,
        ),
        due_date=due_date,
        is_deleted=is_deleted,
    )
    db_session.add(txn)
    db_session.flush()
    return txn


def _make_template_with_cadence(
    db_session, seed_user, cadence, interval_n=1,
):
    """Create a template with a recurrence rule of the given cadence.

    Args:
        db_session: Active database session.
        seed_user: The seed_user fixture dict.
        cadence: A ``tests.oracles.recurrence_baseline`` cadence
            constant, or ``None`` for a template that does not repeat --
            which since plan step R2e-3 means it names NO rule at all.
        interval_n: The authored interval, read only by the cadence that
            fixes none of its own.

    Returns:
        The created TransactionTemplate.
    """
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=list(seed_user["categories"].values())[0].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name="Template",
        default_amount=Decimal("100.00"),
    )
    db_session.add(template)
    db_session.flush()
    if cadence is not None:
        # The definition first, then the cadence onto it (plan step R-F6).
        make_cadence_rule(template, cadence, interval_n=interval_n)
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

    def test_out_of_period_due_date_clamps_into_its_period_month(
        self, app, seed_user, seed_periods, db,
    ):
        """A due_date past its period end is counted once, in its period.

        The transaction belongs to period 1 (Jan 16 - Jan 29) but carries a
        stray Feb 1 due_date (outside its own period).  The clamped
        attribution rule pulls it to the period end (Jan 29), so it is
        counted ONCE, in January (its period's month) -- never in February,
        and never in both.  This is the locked clamp behavior that keeps the
        daily balance reconciling with the grid: a period's flow always
        closes by its own end_date.
        """
        with app.app_context():
            p1 = seed_periods[1]  # Jan 16 - Jan 29
            _add_transaction(
                db.session, seed_user, p1, "Stray Bill", "300.00",
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
            # Clamped to period 1's end (Jan 29): counted in January...
            assert jan.total_expenses == Decimal("300.00")
            assert 29 in jan.day_entries
            # ...and NOT in February (its period does not reach February).
            assert feb.total_expenses == Decimal("0")

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

    def test_day_totals_fold_per_day(self, app, seed_user, seed_periods, db):
        """day_totals holds the per-day (income, expense) fold; month derives from it.

        Day 2 carries a $3,000 income and a $400 expense; day 5 carries a
        $1,200 expense only.  Per-day folds: day 2 = (3000.00, 400.00),
        day 5 = (0, 1200.00).  The expense-only day's income leg must be a
        Decimal, not an int 0 (money is always Decimal).  The month
        headline totals are the sum of the per-day folds: income 3000.00,
        expenses 400.00 + 1200.00 = 1600.00 -- so the per-day cells the
        route renders and the month total cannot diverge.
        """
        with app.app_context():
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Salary", "3000.00",
                is_income=True, due_date=date(2026, 1, 2),
            )
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Groceries", "400.00",
                due_date=date(2026, 1, 2),
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
            assert result.day_totals[2] == (Decimal("3000.00"), Decimal("400.00"))
            assert result.day_totals[5] == (Decimal("0"), Decimal("1200.00"))
            # The expense-only day's income leg is a Decimal zero, not int 0.
            income_leg, _expense_leg = result.day_totals[5]
            assert isinstance(income_leg, Decimal)
            # Month headline totals are the sum of the per-day folds.
            assert result.total_income == Decimal("3000.00")
            assert result.total_expenses == Decimal("1600.00")


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
          Period 6: Mar 27 -- Apr 9
          Period 7: Apr 10 -- Apr 23
          Period 8: Apr 24 -- May 7  (contains Apr 30)
        The month-end balance is the seam's cash-flow scalar at
        ``2026-04-30``, which projects forward through the period
        CONTAINING Apr 30 (period 8), not the "last period whose end_date <=
        Apr 30" (period 7).  Period 8 has no transactions here so the
        projected balance carries forward unchanged from period 7's
        4000.00, which keeps this assertion valid; the next test
        proves the producer steps into period 8 when it has data.

        Period 6: 1000 + 2000 - 500 = 2500.
        Period 7: 2500 + 2000 - 500 = 4000.
        Period 8: 4000 + 0 - 0      = 4000  (no txns)
        Month-end (Apr 30, falls in period 8): 4000.00.

        **The flows are seeded AFTER the suite's frozen today (2026-03-20)
        deliberately** (plan step X-c2b2, ruling R-G).  A still-Projected row
        whose date has passed lands at ``max(its date, as_of + 1 day)`` -- a
        plan cannot have already happened -- so a January-dated projected bill
        read in March contributes to March, not to January, and a January
        month-end would correctly read the anchor flat.  Dating the fixture
        forward is what makes each row land on its own day and keeps the
        hand arithmetic above the arithmetic the producer actually performs.
        """
        with app.app_context():
            p6 = seed_periods[6]
            p7 = seed_periods[7]

            _add_transaction(
                db.session, seed_user, p6, "Pay 1", "2000.00",
                is_income=True, due_date=date(2026, 4, 1),
            )
            _add_transaction(
                db.session, seed_user, p6, "Rent", "500.00",
                due_date=date(2026, 4, 5),
            )
            _add_transaction(
                db.session, seed_user, p7, "Pay 2", "2000.00",
                is_income=True, due_date=date(2026, 4, 13),
            )
            _add_transaction(
                db.session, seed_user, p7, "Util", "500.00",
                due_date=date(2026, 4, 20),
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=4,
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
        Post-Commit-9 the month-end balance flows through the seam's
        ``balance_at.cash_balance_at``, which values the target DATE itself
        (the cash fold sampled there) rather than a period boundary near it.

        seed_periods:
          Period 7: Apr 10 -- Apr 23
          Period 8: Apr 24 -- May 7  (contains Apr 30)

        Setup loads income/expense in BOTH periods so the
        pre-Commit-9 path (which would stop after period 7) and the
        post-Commit-9 path (which includes period 8) produce
        distinct values; the assertion locks the correct one.  The month is
        forward of the suite's frozen today for the reason the previous test
        documents (ruling R-G).

        Hand arithmetic (no entries, formula collapses to
        effective_amount; statuses are Projected so the
        balance-contributing predicate includes them):
          Anchor (1000.00):  1000
          Period 7:          1000 + 1500 - 200 = 2300
          Period 8:          2300 + 1500 - 200 = 3600

        Pre-Commit-9 would have returned 2300.00 (period 7 end);
        post-Commit-9 must return 3600.00.  Re-pinned per
        HIGH-02 / W-277.
        """
        with app.app_context():
            p7 = seed_periods[7]
            p8 = seed_periods[8]
            assert p7.end_date == date(2026, 4, 23)
            assert p8.start_date == date(2026, 4, 24)
            assert p8.end_date == date(2026, 5, 7)

            _add_transaction(
                db.session, seed_user, p7, "Mid-Apr Pay", "1500.00",
                is_income=True, due_date=date(2026, 4, 13),
            )
            _add_transaction(
                db.session, seed_user, p7, "Mid-Apr Bill", "200.00",
                due_date=date(2026, 4, 17),
            )
            _add_transaction(
                db.session, seed_user, p8, "Late-Apr Pay", "1500.00",
                is_income=True, due_date=date(2026, 4, 24),
            )
            _add_transaction(
                db.session, seed_user, p8, "Late-Apr Bill", "200.00",
                due_date=date(2026, 4, 24),
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=4,
            )
            # 1000 + (1500-200) + (1500-200) = 3600.00.
            # Pre-Commit-9 returned 2300.00 -- HIGH-02 / W-277.
            assert result.projected_end_balance == Decimal("3600.00")


# ── Infrequency Tests ────────────────────────────────────────────────


class TestIsInfrequent:
    """Tests for the _is_infrequent helper.

    **The predicate is DERIVED since plan step R7a-2b** -- "fires fewer than
    twelve times a year" -- where it was a hand-enumerated set of three
    pattern members.  Every assertion below is unchanged: the derivation has
    to reproduce all six answers before it is allowed to extend past them,
    and ``TestInfrequencyIsDerivedNotEnumerated`` is where it does.
    """

    def test_infrequent_annual(self, app, seed_user, seed_periods, db):
        """Template with Annual pattern is infrequent."""
        with app.app_context():
            template = _make_template_with_cadence(
                db.session, seed_user, ANNUAL,
            )
            txn = _add_transaction(
                db.session, seed_user, seed_periods[0], "Annual",
                "1000.00", template=template, due_date=date(2026, 1, 5),
            )
            db.session.commit()
            assert _is_infrequent(txn, _BIWEEKLY) is True

    def test_infrequent_quarterly(self, app, seed_user, seed_periods, db):
        """Template with Quarterly pattern is infrequent."""
        with app.app_context():
            template = _make_template_with_cadence(
                db.session, seed_user, QUARTERLY,
            )
            txn = _add_transaction(
                db.session, seed_user, seed_periods[0], "Quarterly",
                "500.00", template=template, due_date=date(2026, 1, 5),
            )
            db.session.commit()
            assert _is_infrequent(txn, _BIWEEKLY) is True

    def test_infrequent_semi_annual(self, app, seed_user, seed_periods, db):
        """Template with Semi-Annual pattern is infrequent."""
        with app.app_context():
            template = _make_template_with_cadence(
                db.session, seed_user, SEMI_ANNUAL,
            )
            txn = _add_transaction(
                db.session, seed_user, seed_periods[0], "Semi",
                "600.00", template=template, due_date=date(2026, 1, 5),
            )
            db.session.commit()
            assert _is_infrequent(txn, _BIWEEKLY) is True

    def test_non_repeating_not_infrequent(self, app, seed_user, seed_periods, db):
        """A template that does not repeat is NOT infrequent.

        "Infrequent" means "less frequent than monthly", which is a statement
        about a CADENCE; a definition that does not repeat has none, so the
        badge does not apply.  Until plan step R2e-3 the two template kinds
        disagreed about this: a one-time TRANSACTION was already rule-less and
        answered False here, while a one-time TRANSFER carried a ``Once``
        rule, which was a member of ``_INFREQUENT_PATTERNS`` and answered
        True.  Retiring the pattern is what made them agree.
        """
        with app.app_context():
            template = _make_template_with_cadence(
                db.session, seed_user, None,
            )
            assert template.recurrence_rule is None
            txn = _add_transaction(
                db.session, seed_user, seed_periods[0], "One-time",
                "200.00", template=template, due_date=date(2026, 1, 5),
            )
            db.session.commit()
            assert _is_infrequent(txn, _BIWEEKLY) is False

    def test_monthly_not_infrequent(self, app, seed_user, seed_periods, db):
        """Template with Monthly pattern is NOT infrequent."""
        with app.app_context():
            template = _make_template_with_cadence(
                db.session, seed_user, MONTHLY,
            )
            txn = _add_transaction(
                db.session, seed_user, seed_periods[0], "Monthly",
                "100.00", template=template, due_date=date(2026, 1, 5),
            )
            db.session.commit()
            assert _is_infrequent(txn, _BIWEEKLY) is False

    def test_every_period_not_infrequent(self, app, seed_user, seed_periods, db):
        """Template with Every Period pattern is NOT infrequent."""
        with app.app_context():
            template = _make_template_with_cadence(
                db.session, seed_user, EVERY_PERIOD,
            )
            txn = _add_transaction(
                db.session, seed_user, seed_periods[0], "Each Period",
                "100.00", template=template, due_date=date(2026, 1, 5),
            )
            db.session.commit()
            assert _is_infrequent(txn, _BIWEEKLY) is False

    def test_no_template_not_infrequent(self, app, seed_user, seed_periods, db):
        """Manual transaction (template=None) is NOT infrequent."""
        with app.app_context():
            txn = _add_transaction(
                db.session, seed_user, seed_periods[0], "Manual",
                "100.00", due_date=date(2026, 1, 5),
            )
            db.session.commit()
            assert _is_infrequent(txn, _BIWEEKLY) is False


class TestInfrequencyIsDerivedNotEnumerated:
    """What the three-member set could not answer, and now does (R7a-2b).

    The badge asked "is this pattern one of Quarterly / Semi-Annual /
    Annual".  It now asks "does this fire fewer than twelve times a year",
    which is the same answer for those three and a DIFFERENT one for two
    classes the enumeration structurally could not reach.
    """

    def test_every_three_paychecks_is_infrequent(
        self, app, seed_user, seed_periods, db,
    ):
        """Every 3 paychecks fires 8.67 times a year, so it IS infrequent.

        The enumeration said otherwise -- ``EVERY_N_PERIODS`` was not one of
        its three members, so an every-3-paychecks bill rendered as a frequent
        flow whatever its interval.  Hand-computed: 26 / 3 = 8.67 < 12.
        """
        with app.app_context():
            template = _make_template_with_cadence(
                db.session, seed_user,
                EVERY_N_PERIODS, interval_n=3,
            )
            txn = _add_transaction(
                db.session, seed_user, seed_periods[0], "Every 3rd",
                "300.00", template=template, due_date=date(2026, 1, 5),
            )
            db.session.commit()
            assert _is_infrequent(txn, _BIWEEKLY) is True

    def test_every_two_paychecks_is_frequent_at_a_biweekly_cadence(
        self, app, seed_user, seed_periods, db,
    ):
        """Every 2 paychecks is 13 a year biweekly -- just over the line.

        The boundary case, and the control on the one above: 26 / 2 = 13,
        which is NOT fewer than 12, so the badge does not apply.  A rule that
        tested ``<=`` would fail here.
        """
        with app.app_context():
            template = _make_template_with_cadence(
                db.session, seed_user,
                EVERY_N_PERIODS, interval_n=2,
            )
            txn = _add_transaction(
                db.session, seed_user, seed_periods[0], "Every 2nd",
                "300.00", template=template, due_date=date(2026, 1, 5),
            )
            db.session.commit()
            assert _is_infrequent(txn, _BIWEEKLY) is False

    def test_the_same_rule_answers_differently_for_a_monthly_paid_owner(
        self, app, seed_user, seed_periods, db,
    ):
        """Every 2 paychecks is 6 a year when you are paid monthly.

        The badge is per-OWNER now, which an enumeration of pattern names
        could not be: the identical row is frequent for a biweekly owner
        (13 a year) and infrequent for a monthly-paid one (12 / 2 = 6).  The
        PAIR is the assertion -- either alone would pass against a constant.
        """
        with app.app_context():
            template = _make_template_with_cadence(
                db.session, seed_user,
                EVERY_N_PERIODS, interval_n=2,
            )
            txn = _add_transaction(
                db.session, seed_user, seed_periods[0], "Every 2nd",
                "300.00", template=template, due_date=date(2026, 1, 5),
            )
            db.session.commit()
            assert _is_infrequent(txn, _BIWEEKLY) is False
            assert _is_infrequent(txn, _MONTHLY_PAID) is True

    def test_a_monthly_bill_is_never_infrequent_at_any_cadence(
        self, app, seed_user, seed_periods, db,
    ):
        """A MONTH-unit cadence fires 12 times a year whoever owns it.

        The control that the per-owner half is scoped to paycheck space: a
        monthly bill is monthly for a weekly-paid owner too, so no cadence may
        move this answer.
        """
        with app.app_context():
            template = _make_template_with_cadence(
                db.session, seed_user, MONTHLY,
            )
            txn = _add_transaction(
                db.session, seed_user, seed_periods[0], "Monthly",
                "100.00", template=template, due_date=date(2026, 1, 5),
            )
            db.session.commit()
            for cadence in (_BIWEEKLY, _MONTHLY_PAID, PayCadence(cadence_days=7)):
                assert _is_infrequent(txn, cadence) is False


class TestTheBadgeReadsTheOWNERSStoredCadence:
    """The thread from the stored schedule to the rendered day entry.

    **Every other test in this file hands ``_is_infrequent`` a hand-built
    ``PayCadence``, so all of them would still pass if ``badge_cadence``
    returned a hardcoded biweekly value** -- which is exactly the defect class
    plan step R7a-2a existed to remove, one layer up.  An adversarial review of
    R7a-2b named the hole; this is the test that closes it, by moving the
    STORED ``pay_schedule.cadence_days`` and asserting the rendered badge
    follows.
    """

    def test_the_same_row_badges_differently_when_the_schedule_moves(
        self, app, db, seed_user, seed_periods,
    ):
        """An every-2-paychecks bill: frequent at 14 days, infrequent at 30.

        Hand-computed: 26 / 2 = 13 a year biweekly (not fewer than 12, so no
        badge) against 12 / 2 = 6 a year for a monthly-paid owner (badged).
        Nothing about the transaction changes between the two reads -- only
        the owner's stored cadence -- so a hardcoded count fails here whatever
        value it is hardcoded to.
        """
        with app.app_context():
            template = _make_template_with_cadence(
                db.session, seed_user,
                EVERY_N_PERIODS, interval_n=2,
            )
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Every 2nd",
                "300.00", template=template,
                due_date=seed_periods[0].start_date,
            )
            db.session.commit()
            month = seed_periods[0].start_date

            def _badges():
                summary = calendar_service.get_month_detail(
                    seed_user["user"].id, month.year, month.month,
                )
                return [
                    entry.is_infrequent
                    for entries in summary.day_entries.values()
                    for entry in entries
                    if entry.name == "Every 2nd"
                ]

            pay_schedule_service.upsert_schedule(seed_user["user"].id, 14)
            db.session.commit()
            assert _badges() == [False]

            pay_schedule_service.upsert_schedule(seed_user["user"].id, 30)
            db.session.commit()
            assert _badges() == [True]

    def test_a_month_of_manual_rows_resolves_no_cadence_at_all(
        self, app, db, seed_user, seed_periods,
    ):
        """Nothing repeats, so the build must not read the pay schedule.

        ``badge_cadence``'s contract is "a page must not fail for a fact it
        does not use", and the predicate that enforces it tests the recurrence
        RULE rather than the transaction list -- an earlier draft tested the
        list, which would have resolved a cadence for a month of purely manual
        entries.  Patching the loader to explode is what makes that a control
        rather than a claim.
        """
        with app.app_context():
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Manual",
                "100.00", due_date=seed_periods[0].start_date,
            )
            db.session.commit()
            month = seed_periods[0].start_date

            def _explode(_user_id):
                raise AssertionError(
                    "badge_cadence read the pay schedule for a month with "
                    "nothing to badge",
                )

            with patch.object(
                calendar_infrequency, "cadence_for", _explode,
            ):
                summary = calendar_service.get_month_detail(
                    seed_user["user"].id, month.year, month.month,
                )
            assert [
                entry.is_infrequent
                for entries in summary.day_entries.values()
                for entry in entries
            ] == [False]


# ── Third Paycheck Tests ─────────────────────────────────────────────


class TestThirdPaycheckDetection:
    """Tests for 3rd paycheck month detection."""

    def test_third_paycheck_detection_26_periods(self, app, seed_user, db):
        """26 biweekly periods in 2026 produce exactly 2 third-paycheck months."""
        with app.app_context():
            from app.services import pay_period_service
            periods = pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=26,
                cadence_days=14,
            )
            db.session.commit()

            # The CALENDAR's window, which is what production passes since
            # plan step C2-f1; an ORM list only worked here by duck typing.
            window = calendar_for(seed_user["user"].id).saved()
            result = _detect_third_paycheck_months(window, 2026)
            assert len(result) == 2

    def test_third_paycheck_empty_periods(self, app):
        """Empty period list produces empty set."""
        with app.app_context():
            result = _detect_third_paycheck_months(PeriodWindow(periods=()), 2026)
            assert result == set()

    def test_third_paycheck_only_target_year(self, app, seed_user, db):
        """Only counts periods with start_date in the target year."""
        with app.app_context():
            from app.services import pay_period_service
            # Generate periods spanning 2025-2026.
            periods = pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=date(2025, 7, 4),
                num_periods=40,
                cadence_days=14,
            )
            db.session.commit()

            result_2026 = _detect_third_paycheck_months(
                calendar_for(seed_user["user"].id).saved(), 2026,
            )
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
            periods = pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=26,
                cadence_days=14,
            )
            db.session.commit()

            result = _detect_third_paycheck_months(
                calendar_for(seed_user["user"].id).saved(), 2026,
            )

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
            # The BINDING went with the ``current_anchor_period_id`` line it
            # fed (ruling R-EH); the CALL is fixture setup and stays -- these
            # 26 periods ARE the third-paycheck year under test.
            pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=26,
                cadence_days=14,
            )
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
            periods = pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=date(2028, 2, 18),
                num_periods=2,
                cadence_days=14,
            )
            db.session.commit()
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
                status=StatusEnum.SETTLED, settled_amount="200.00",
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
                status=StatusEnum.SETTLED, settled_amount="200.00",
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

    def test_c10_3_grid_period_subtotal_excludes_cancelled_and_credit(
        self, app, seed_user, seed_periods, db,
    ):
        """F-3 / W-065 C10-3: Cancelled and Credit never reach the grid column.

        Same fixture as C10-2 (Projected $500 + Settled $200 +
        Cancelled $100 + Credit $50 on Jan 5).

        **Ruling R-K changed what a subtotal COUNTS, and this test's figure
        moved with it** (plan step X-c2b2; the read moved to the shipped
        ``GridColumn`` when plan step X-c2b3 deleted
        ``cash_ledger.period_subtotal``).  The retired subtotal was
        Projected-ONLY -- it gated every row through ``is_projected``, so a row
        that had actually been PAID contributed nothing and a past column read
        ``$0.00`` while thousands of dollars moved through it (finding N-41).
        The grid column now counts every row attributed to the period: a settled
        row at its confirmed cash leg, a projected row at its entries-aware
        reservation.

        Hand arithmetic on the new basis:

            Projected $500, no entries -> reservation      500.00
            Settled $200 (actual 200.00), no credit entries
              -> settled_cash_leg = 200.00 - 0             200.00
            Cancelled $100 -> neither projected nor settled  0.00
            Credit $50     -> neither projected nor settled  0.00
                                                          -------
            expense                                        700.00

        So the exclusion this test locks is the CANCELLED / CREDIT pair, which
        R-K did not touch: they are excluded from the projected half by
        ``is_projected`` and from the settled half by the settled-status
        narrowing in SQL.  The old "locked Choice-2 divergence" between the
        calendar day total (700.00, C10-2) and this column is gone -- both count
        the settled row now.  That agreement is this FIXTURE's, not a general
        identity: every row here sits in one period on one day, while the
        calendar places a chip on its budget attribution date and steps its
        balance on the day the money moved (finding N-58).
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
                status=StatusEnum.SETTLED, settled_amount="200.00",
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

            column = balance_at.grid_balance_view(
                seed_user["account"],
                BalanceContext.build(seed_user["user"].id),
            ).columns[p0.id]
            # 500.00 reservation + 200.00 confirmed cash leg; the Cancelled
            # $100 and the Credit $50 contribute nothing to either half.
            assert column.expense == Decimal("700.00")
            assert column.income == Decimal("0.00")

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
                status=StatusEnum.SETTLED, settled_amount="200.00",
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
    :func:`resolve_analytics_account` returns ``None`` -- the pre-F-2
    behaviour of silently substituting a zeroed
    :class:`MonthSummary` / :class:`YearOverview` masked the
    upstream defect behind a ``$0.00`` calendar.

    **The no-BASELINE half of that contract moved at plan step X-v2** (ruling
    R-BW).  Both conditions used to raise this one exception, and the route
    turned both into a 404; they are two different problems (a deleted
    analytics account, versus a repairable missing baseline) and they get two
    different answers now.  The baseline half raises
    :class:`~app.exceptions.BaselineMissingError` from the seam, which one
    application-level handler answers with the repair card -- asserted below,
    and end-to-end in ``tests/test_routes/test_no_baseline_policy.py``.
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
        """A None baseline raises the SEAM's named exception, not this module's."""
        with app.app_context():
            # The baseline scenario is now resolved inside the balance context,
            # so that is where an unresolvable baseline is simulated.
            monkeypatch.setattr(
                resolution_context, "get_baseline_scenario",
                lambda _user_id: None,
            )
            # The seam's own named exception, NOT the calendar's: a missing
            # baseline is answered by one application-level handler, and a
            # producer that translated it into its own error is what made this
            # state 404 here while it 500'd on the loan page (ruling R-BW).
            with pytest.raises(BaselineMissingError):
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
            # The baseline scenario is now resolved inside the balance context,
            # so that is where an unresolvable baseline is simulated.
            monkeypatch.setattr(
                resolution_context, "get_baseline_scenario",
                lambda _user_id: None,
            )
            # The seam's own named exception, NOT the calendar's: a missing
            # baseline is answered by one application-level handler, and a
            # producer that translated it into its own error is what made this
            # state 404 here while it 500'd on the loan page (ruling R-BW).
            with pytest.raises(BaselineMissingError):
                calendar_service.get_year_overview(
                    user_id=seed_user["user"].id,
                    year=2026,
                )


class TestCalendarDailyView:
    """The month's daily running-balance projection (DailyView).

    Uses the standard ``seed_periods`` scenario (biweekly from 2026-01-02,
    anchor = period 0 at $1000).  April flows: Rent -500 due 04-05, Salary
    +2000 due 04-09 (period 6); Car -800 due 04-20, Salary +2000 due 04-23
    (period 7).  The hand-computed running balance is 04-05 500, 04-09 2500,
    04-15 2500, 04-20 1700, 04-23 3700, 04-30 3700; the month trough is $500
    on the 5th.

    **April, not January, because a plan cannot have already happened**
    (ruling R-G, wired at plan step X-c2b2).  The suite freezes today to
    2026-03-20, and a still-Projected row dated before that lands at
    ``as_of + 1 day`` rather than on its own date -- so a January month would
    draw a FLAT line at the anchor, which is the honest answer (none of those
    bills was ever recorded as paid) but pins no ramp arithmetic.  Dating the
    flows forward of the frozen today keeps every hand-computed figure below
    exactly what it was and makes each land on its own day.
    """

    def _seed_april(self, db, seed_user, seed_periods):
        p6, p7 = seed_periods[6], seed_periods[7]
        _add_transaction(
            db.session, seed_user, p6, "Rent", "500.00",
            due_date=date(2026, 4, 5),
        )
        _add_transaction(
            db.session, seed_user, p6, "Salary", "2000.00",
            is_income=True, due_date=date(2026, 4, 9),
        )
        _add_transaction(
            db.session, seed_user, p7, "Car", "800.00",
            due_date=date(2026, 4, 20),
        )
        _add_transaction(
            db.session, seed_user, p7, "Salary", "2000.00",
            is_income=True, due_date=date(2026, 4, 23),
        )
        db.session.commit()

    def test_daily_is_none_without_today(self, app, seed_user, seed_periods, db):
        """Omitting ``today`` yields no daily view (year-overview parity)."""
        with app.app_context():
            self._seed_april(db, seed_user, seed_periods)
            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id, year=2026, month=4,
            )
        assert result.daily is None

    def test_daily_balances_and_trough(
        self, app, seed_user, seed_periods, db,
    ):
        """The daily view carries per-day balances and the month trough."""
        with app.app_context():
            self._seed_april(db, seed_user, seed_periods)
            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id, year=2026, month=4,
                today=date(2026, 4, 20),
            )
        daily = result.daily
        assert isinstance(daily, DailyView)
        assert daily.daily_balances[5] == Decimal("500.00")
        assert daily.daily_balances[9] == Decimal("2500.00")
        assert daily.daily_balances[30] == Decimal("3700.00")
        # Trough is the earliest day of the month minimum ($500 on the 5th).
        assert daily.trough_day == 5
        assert daily.trough_balance == Decimal("500.00")

    def test_balance_today_and_elapsed_remaining_split(
        self, app, seed_user, seed_periods, db,
    ):
        """balance_today and the elapsed/remaining split key off ``today``."""
        with app.app_context():
            self._seed_april(db, seed_user, seed_periods)
            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id, year=2026, month=4,
                today=date(2026, 4, 20),
            )
        daily = result.daily
        # End-of-day balance on the 20th (after the Car payment): $1700.
        assert daily.balance_today == Decimal("1700.00")
        # Elapsed (days 1-20): Salary 2000 in, Rent 500 + Car 800 = 1300 out.
        assert daily.elapsed_income == Decimal("2000.00")
        assert daily.elapsed_expense == Decimal("1300.00")
        # Remaining (days 21-30): the second Salary; no more expenses.
        assert daily.remaining_income == Decimal("2000.00")
        assert daily.remaining_expense == Decimal("0.00")

    def test_future_month_is_all_remaining(
        self, app, seed_user, seed_periods, db,
    ):
        """A month entirely after today is all remaining, no balance_today."""
        with app.app_context():
            self._seed_april(db, seed_user, seed_periods)
            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id, year=2026, month=4,
                today=date(2026, 3, 15),
            )
        daily = result.daily
        assert daily.balance_today is None
        assert daily.elapsed_income == Decimal("0.00")
        assert daily.elapsed_expense == Decimal("0.00")
        assert daily.remaining_income == Decimal("4000.00")
        assert daily.remaining_expense == Decimal("1300.00")

    def test_income_first_then_expense_by_magnitude(
        self, app, seed_user, seed_periods, db,
    ):
        """Day cells order income first, then expenses by descending size."""
        with app.app_context():
            p0 = seed_periods[0]
            _add_transaction(
                db.session, seed_user, p0, "Small Income", "100.00",
                is_income=True, due_date=date(2026, 1, 6),
            )
            _add_transaction(
                db.session, seed_user, p0, "Big Expense", "500.00",
                due_date=date(2026, 1, 6),
            )
            db.session.commit()
            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id, year=2026, month=1,
                today=date(2026, 1, 6),
            )
        names = [e.name for e in result.day_entries[6]]
        # Income leads even though the expense is larger.
        assert names == ["Small Income", "Big Expense"]

    def test_day_overflow_collapses_beyond_three_flows(
        self, app, seed_user, seed_periods, db,
    ):
        """A day with more than three flows yields a "+N more" residual.

        Day has 1 income (+3000) and 4 expenses (500/400/300/200).  Ordered
        income-first then expense-desc, the visible three are the income and
        the two largest expenses; the residual is the two smallest expenses
        (-300 and -200 = -500).
        """
        with app.app_context():
            p0 = seed_periods[0]
            _add_transaction(
                db.session, seed_user, p0, "Pay", "3000.00",
                is_income=True, due_date=date(2026, 1, 7),
            )
            for name, amt in [
                ("E500", "500.00"), ("E400", "400.00"),
                ("E300", "300.00"), ("E200", "200.00"),
            ]:
                _add_transaction(
                    db.session, seed_user, p0, name, amt,
                    due_date=date(2026, 1, 7),
                )
            db.session.commit()
            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id, year=2026, month=1,
                today=date(2026, 1, 7),
            )
        assert len(result.day_entries[7]) == 5
        overflow = result.day_overflow[7]
        assert overflow.count == 2
        assert overflow.net == Decimal("-500.00")
        # Days at or under the cap carry no overflow entry.
        assert 5 not in result.day_overflow
