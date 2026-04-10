"""
Shekel Budget App -- Budget Variance Service Tests

Tests for the budget variance engine: variance computation for
single transactions, category grouping, time window filtering
(pay period, month, year), sorting, and edge cases.
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.extensions import db as _db
from app.models.transaction import Transaction
from app.services import budget_variance_service


# ── Helpers ──────────────────────────────────────────────────────────


def _add_txn(
    db_session, seed_user, period, name, estimated,
    actual=None, is_income=False, due_date=None,
    status_enum=StatusEnum.PROJECTED, category_key=None,
    is_deleted=False,
):
    """Create a transaction for testing.

    Args:
        db_session: Active database session.
        seed_user: The seed_user fixture dict.
        period: PayPeriod to assign to.
        name: Transaction name.
        estimated: Estimated amount (Decimal or str).
        actual: Actual amount (Decimal, str, or None).
        is_income: Whether this is income (default expense).
        due_date: Optional due_date.
        status_enum: StatusEnum member for the transaction status.
        category_key: Key into seed_user["categories"] dict.
        is_deleted: Soft-delete flag.

    Returns:
        The created Transaction.
    """
    type_id = (
        ref_cache.txn_type_id(TxnTypeEnum.INCOME)
        if is_income
        else ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    )
    cat_id = None
    if category_key and category_key in seed_user["categories"]:
        cat_id = seed_user["categories"][category_key].id

    txn = Transaction(
        account_id=seed_user["account"].id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        status_id=ref_cache.status_id(status_enum),
        name=name,
        category_id=cat_id,
        transaction_type_id=type_id,
        estimated_amount=Decimal(str(estimated)),
        actual_amount=Decimal(str(actual)) if actual is not None else None,
        due_date=due_date,
        is_deleted=is_deleted,
    )
    db_session.add(txn)
    db_session.flush()
    return txn


# ── Core Variance Calculation ────────────────────────────────────────


class TestVarianceEmpty:
    """Tests for empty variance reports."""

    def test_variance_empty_period(self, app, seed_user, seed_periods):
        """VarianceReport with zero totals when no transactions exist."""
        with app.app_context():
            result = budget_variance_service.compute_variance(
                user_id=seed_user["user"].id,
                window_type="pay_period",
                period_id=seed_periods[0].id,
            )
            assert result.total_estimated == Decimal("0")
            assert result.total_actual == Decimal("0")
            assert result.total_variance == Decimal("0")
            assert result.groups == []
            assert result.transaction_count == 0


class TestVarianceExact:
    """Tests for exact budget match."""

    def test_variance_exact_match(self, app, seed_user, seed_periods, db):
        """$500 est, $500 actual, paid -> variance=0, pct=0."""
        with app.app_context():
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Rent", "500.00", actual="500.00",
                status_enum=StatusEnum.DONE,
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            result = budget_variance_service.compute_variance(
                user_id=seed_user["user"].id,
                window_type="pay_period",
                period_id=seed_periods[0].id,
            )
            assert result.total_variance == Decimal("0")
            txn_var = result.groups[0].items[0].transactions[0]
            assert txn_var.variance == Decimal("0")
            assert txn_var.variance_pct == Decimal("0.00")


class TestVarianceOverUnder:
    """Tests for over and under budget scenarios."""

    def test_variance_over_budget(self, app, seed_user, seed_periods, db):
        """$500 est, $550 actual -> variance=50, pct=10.00."""
        with app.app_context():
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Groceries", "500.00", actual="550.00",
                status_enum=StatusEnum.DONE,
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            result = budget_variance_service.compute_variance(
                user_id=seed_user["user"].id,
                window_type="pay_period",
                period_id=seed_periods[0].id,
            )
            txn_var = result.groups[0].items[0].transactions[0]
            assert txn_var.variance == Decimal("50.00")
            assert txn_var.variance_pct == Decimal("10.00")

    def test_variance_under_budget(self, app, seed_user, seed_periods, db):
        """$500 est, $450 actual -> variance=-50, pct=-10.00."""
        with app.app_context():
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Groceries", "500.00", actual="450.00",
                status_enum=StatusEnum.DONE,
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            result = budget_variance_service.compute_variance(
                user_id=seed_user["user"].id,
                window_type="pay_period",
                period_id=seed_periods[0].id,
            )
            txn_var = result.groups[0].items[0].transactions[0]
            assert txn_var.variance == Decimal("-50.00")
            assert txn_var.variance_pct == Decimal("-10.00")


class TestVarianceProjected:
    """Tests for projected (unpaid) transactions."""

    def test_variance_projected_zero(self, app, seed_user, seed_periods, db):
        """Projected txn: actual=estimated, variance=0."""
        with app.app_context():
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Future", "500.00",
                status_enum=StatusEnum.PROJECTED,
                due_date=date(2026, 1, 10),
            )
            db.session.commit()

            result = budget_variance_service.compute_variance(
                user_id=seed_user["user"].id,
                window_type="pay_period",
                period_id=seed_periods[0].id,
            )
            txn_var = result.groups[0].items[0].transactions[0]
            assert txn_var.variance == Decimal("0")
            assert txn_var.is_paid is False


class TestVarianceEdgeCases:
    """Tests for variance edge cases."""

    def test_variance_zero_estimated(self, app, seed_user, seed_periods, db):
        """$0 est, $50 actual -> variance=50, pct=None (div by zero)."""
        with app.app_context():
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Surprise", "0.00", actual="50.00",
                status_enum=StatusEnum.DONE,
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            result = budget_variance_service.compute_variance(
                user_id=seed_user["user"].id,
                window_type="pay_period",
                period_id=seed_periods[0].id,
            )
            txn_var = result.groups[0].items[0].transactions[0]
            assert txn_var.variance == Decimal("50.00")
            assert txn_var.variance_pct is None

    def test_variance_paid_no_actual_amount(self, app, seed_user, seed_periods, db):
        """Done status but actual_amount=NULL -> falls back to estimated."""
        with app.app_context():
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "NoActual", "500.00", actual=None,
                status_enum=StatusEnum.DONE,
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            result = budget_variance_service.compute_variance(
                user_id=seed_user["user"].id,
                window_type="pay_period",
                period_id=seed_periods[0].id,
            )
            txn_var = result.groups[0].items[0].transactions[0]
            assert txn_var.actual == Decimal("500.00")
            assert txn_var.variance == Decimal("0")
            assert txn_var.is_paid is True

    def test_variance_income_transaction(self, app, seed_user, seed_periods, db):
        """Income $2000 est, $2100 actual -> positive variance (received more)."""
        with app.app_context():
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Salary", "2000.00", actual="2100.00",
                is_income=True, status_enum=StatusEnum.RECEIVED,
                due_date=date(2026, 1, 2),
            )
            db.session.commit()

            result = budget_variance_service.compute_variance(
                user_id=seed_user["user"].id,
                window_type="pay_period",
                period_id=seed_periods[0].id,
            )
            txn_var = result.groups[0].items[0].transactions[0]
            # Positive variance = received more than estimated.
            assert txn_var.variance == Decimal("100.00")

    def test_variance_pct_decimal_precision(self, app, seed_user, seed_periods, db):
        """$300 est, $310 actual -> pct = 3.33 (rounded, not float noise)."""
        with app.app_context():
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Precise", "300.00", actual="310.00",
                status_enum=StatusEnum.DONE,
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            result = budget_variance_service.compute_variance(
                user_id=seed_user["user"].id,
                window_type="pay_period",
                period_id=seed_periods[0].id,
            )
            txn_var = result.groups[0].items[0].transactions[0]
            # 10 / 300 * 100 = 3.333... -> 3.33
            assert txn_var.variance_pct == Decimal("3.33")


# ── Time Window Tests ────────────────────────────────────────────────


class TestPayPeriodWindow:
    """Tests for pay period window type."""

    def test_pay_period_window(self, app, seed_user, seed_periods, db):
        """Only the specified period's transactions are included."""
        with app.app_context():
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "P0 Item", "100.00",
                status_enum=StatusEnum.PROJECTED,
                due_date=date(2026, 1, 5),
            )
            _add_txn(
                db.session, seed_user, seed_periods[1],
                "P1 Item", "200.00",
                status_enum=StatusEnum.PROJECTED,
                due_date=date(2026, 1, 20),
            )
            db.session.commit()

            result = budget_variance_service.compute_variance(
                user_id=seed_user["user"].id,
                window_type="pay_period",
                period_id=seed_periods[0].id,
            )
            assert result.transaction_count == 1
            assert result.total_estimated == Decimal("100.00")


class TestMonthlyWindow:
    """Tests for monthly window type."""

    def test_monthly_window(self, app, seed_user, seed_periods, db):
        """Monthly window includes transactions from all periods in that month."""
        with app.app_context():
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Item 1", "100.00",
                due_date=date(2026, 1, 5),
            )
            _add_txn(
                db.session, seed_user, seed_periods[1],
                "Item 2", "200.00",
                due_date=date(2026, 1, 20),
            )
            db.session.commit()

            result = budget_variance_service.compute_variance(
                user_id=seed_user["user"].id,
                window_type="month",
                month=1,
                year=2026,
            )
            assert result.transaction_count == 2
            assert result.total_estimated == Decimal("300.00")

    def test_monthly_attribution_uses_due_date(self, app, seed_user, seed_periods, db):
        """Txn with due_date in Feb, period in Jan -> attributed to February."""
        with app.app_context():
            _add_txn(
                db.session, seed_user, seed_periods[1],
                "Feb Bill", "300.00",
                due_date=date(2026, 2, 1),
            )
            db.session.commit()

            jan = budget_variance_service.compute_variance(
                user_id=seed_user["user"].id,
                window_type="month",
                month=1,
                year=2026,
            )
            feb = budget_variance_service.compute_variance(
                user_id=seed_user["user"].id,
                window_type="month",
                month=2,
                year=2026,
            )
            assert jan.transaction_count == 0
            assert feb.transaction_count == 1
            assert feb.total_estimated == Decimal("300.00")

    def test_monthly_attribution_fallback(self, app, seed_user, seed_periods, db):
        """Txn with due_date=None uses period start_date month."""
        with app.app_context():
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "NoDue", "100.00",
                due_date=None,
            )
            db.session.commit()

            # Period 0 starts Jan 2 -> attributed to January.
            result = budget_variance_service.compute_variance(
                user_id=seed_user["user"].id,
                window_type="month",
                month=1,
                year=2026,
            )
            assert result.transaction_count == 1

    def test_monthly_no_cross_month_leakage(self, app, seed_user, seed_periods, db):
        """Txn due_date in Feb not included in January window."""
        with app.app_context():
            _add_txn(
                db.session, seed_user, seed_periods[1],
                "Feb Only", "400.00",
                due_date=date(2026, 2, 5),
            )
            db.session.commit()

            jan = budget_variance_service.compute_variance(
                user_id=seed_user["user"].id,
                window_type="month",
                month=1,
                year=2026,
            )
            assert jan.transaction_count == 0


class TestAnnualWindow:
    """Tests for annual window type."""

    def test_annual_window(self, app, seed_user, seed_periods, db):
        """Annual window includes all transactions with due_dates in that year."""
        with app.app_context():
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Jan Item", "100.00",
                due_date=date(2026, 1, 5),
            )
            _add_txn(
                db.session, seed_user, seed_periods[2],
                "Feb Item", "200.00",
                due_date=date(2026, 2, 10),
            )
            db.session.commit()

            result = budget_variance_service.compute_variance(
                user_id=seed_user["user"].id,
                window_type="year",
                year=2026,
            )
            assert result.transaction_count == 2
            assert result.total_estimated == Decimal("300.00")


# ── Grouping and Sorting Tests ───────────────────────────────────────


class TestCategoryGrouping:
    """Tests for category group/item hierarchy."""

    def test_category_grouping(self, app, seed_user, seed_periods, db):
        """Transactions grouped correctly by category group and item."""
        with app.app_context():
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Rent", "1200.00", actual="1200.00",
                status_enum=StatusEnum.DONE,
                category_key="Rent",
                due_date=date(2026, 1, 5),
            )
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Car", "350.00", actual="380.00",
                status_enum=StatusEnum.DONE,
                category_key="Car Payment",
                due_date=date(2026, 1, 10),
            )
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Food", "400.00", actual="450.00",
                status_enum=StatusEnum.DONE,
                category_key="Groceries",
                due_date=date(2026, 1, 7),
            )
            db.session.commit()

            result = budget_variance_service.compute_variance(
                user_id=seed_user["user"].id,
                window_type="pay_period",
                period_id=seed_periods[0].id,
            )
            group_names = {g.group_name for g in result.groups}
            assert "Home" in group_names
            assert "Auto" in group_names
            assert "Family" in group_names
            assert result.transaction_count == 3


class TestSorting:
    """Tests for variance-based sorting."""

    def test_sorted_by_variance_magnitude(self, app, seed_user, seed_periods, db):
        """Groups sorted by abs(variance) descending."""
        with app.app_context():
            # Auto group: $100 variance.
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Car", "300.00", actual="400.00",
                status_enum=StatusEnum.DONE,
                category_key="Car Payment",
                due_date=date(2026, 1, 5),
            )
            # Home group: $200 variance.
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Rent", "1000.00", actual="1200.00",
                status_enum=StatusEnum.DONE,
                category_key="Rent",
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            result = budget_variance_service.compute_variance(
                user_id=seed_user["user"].id,
                window_type="pay_period",
                period_id=seed_periods[0].id,
            )
            # Home (200 variance) should come before Auto (100).
            assert result.groups[0].group_name == "Home"
            assert result.groups[1].group_name == "Auto"

    def test_item_level_sorting(self, app, seed_user, seed_periods, db):
        """Items within a group sorted by abs(variance) descending."""
        with app.app_context():
            # Create 2 items in same category group -- but seed_user
            # categories already have distinct groups.  Use transactions
            # in the same group for sorting.
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Rent A", "500.00", actual="510.00",
                status_enum=StatusEnum.DONE,
                category_key="Rent",
                due_date=date(2026, 1, 5),
            )
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Rent B", "500.00", actual="550.00",
                status_enum=StatusEnum.DONE,
                category_key="Rent",
                due_date=date(2026, 1, 6),
            )
            db.session.commit()

            result = budget_variance_service.compute_variance(
                user_id=seed_user["user"].id,
                window_type="pay_period",
                period_id=seed_periods[0].id,
            )
            # Both are in "Home" / "Rent" item -> sorted by abs variance.
            item = result.groups[0].items[0]
            txns = item.transactions
            assert abs(txns[0].variance) >= abs(txns[1].variance)

    def test_group_totals_sum_from_items(self, app, seed_user, seed_periods, db):
        """Group estimated_total equals sum of item estimated_totals."""
        with app.app_context():
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Car", "300.00", actual="320.00",
                status_enum=StatusEnum.DONE,
                category_key="Car Payment",
                due_date=date(2026, 1, 5),
            )
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Rent", "1000.00", actual="1000.00",
                status_enum=StatusEnum.DONE,
                category_key="Rent",
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            result = budget_variance_service.compute_variance(
                user_id=seed_user["user"].id,
                window_type="pay_period",
                period_id=seed_periods[0].id,
            )
            for group in result.groups:
                item_est_sum = sum(i.estimated_total for i in group.items)
                assert group.estimated_total == item_est_sum

    def test_report_totals_sum_from_groups(self, app, seed_user, seed_periods, db):
        """Report total_estimated equals sum of group estimated_totals."""
        with app.app_context():
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Car", "300.00",
                category_key="Car Payment",
                due_date=date(2026, 1, 5),
            )
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Rent", "1000.00",
                category_key="Rent",
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            result = budget_variance_service.compute_variance(
                user_id=seed_user["user"].id,
                window_type="pay_period",
                period_id=seed_periods[0].id,
            )
            group_est_sum = sum(g.estimated_total for g in result.groups)
            assert result.total_estimated == group_est_sum

    def test_report_total_variance_pct(self, app, seed_user, seed_periods, db):
        """total_variance_pct computed from totals, not averaged from groups."""
        with app.app_context():
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "A", "200.00", actual="220.00",
                status_enum=StatusEnum.DONE,
                due_date=date(2026, 1, 5),
            )
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "B", "300.00", actual="330.00",
                status_enum=StatusEnum.DONE,
                due_date=date(2026, 1, 6),
            )
            db.session.commit()

            result = budget_variance_service.compute_variance(
                user_id=seed_user["user"].id,
                window_type="pay_period",
                period_id=seed_periods[0].id,
            )
            # total_est=500, total_act=550, variance=50
            # pct = 50/500 * 100 = 10.00
            assert result.total_variance_pct == Decimal("10.00")


# ── Filter Tests ─────────────────────────────────────────────────────


class TestFilters:
    """Tests for transaction filtering (deleted, cancelled, ownership)."""

    def test_excludes_deleted(self, app, seed_user, seed_periods, db):
        """Soft-deleted transactions excluded from results."""
        with app.app_context():
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Active", "100.00",
                due_date=date(2026, 1, 5),
            )
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Deleted", "200.00",
                is_deleted=True,
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            result = budget_variance_service.compute_variance(
                user_id=seed_user["user"].id,
                window_type="pay_period",
                period_id=seed_periods[0].id,
            )
            assert result.transaction_count == 1
            assert result.total_estimated == Decimal("100.00")

    def test_excludes_cancelled(self, app, seed_user, seed_periods, db):
        """Cancelled transactions excluded from results."""
        with app.app_context():
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Active", "100.00",
                due_date=date(2026, 1, 5),
            )
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Cancelled", "200.00",
                status_enum=StatusEnum.CANCELLED,
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            result = budget_variance_service.compute_variance(
                user_id=seed_user["user"].id,
                window_type="pay_period",
                period_id=seed_periods[0].id,
            )
            assert result.transaction_count == 1
            assert result.total_estimated == Decimal("100.00")

    def test_ownership_filter(self, app, seed_user, second_user, seed_periods, db):
        """Only the queried user's transactions are returned."""
        with app.app_context():
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "My Txn", "100.00",
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            # Second user should see nothing from seed_user's account.
            result = budget_variance_service.compute_variance(
                user_id=second_user["user"].id,
                window_type="pay_period",
                period_id=seed_periods[0].id,
            )
            assert result.transaction_count == 0


# ── Parameter Validation Tests ───────────────────────────────────────


class TestParameterValidation:
    """Tests for parameter validation."""

    def test_invalid_window_type(self, app, seed_user):
        """ValueError raised for invalid window_type."""
        with app.app_context():
            with pytest.raises(ValueError, match="Invalid window_type"):
                budget_variance_service.compute_variance(
                    user_id=seed_user["user"].id,
                    window_type="invalid",
                )

    def test_pay_period_requires_period_id(self, app, seed_user):
        """ValueError raised when pay_period window lacks period_id."""
        with app.app_context():
            with pytest.raises(ValueError, match="period_id is required"):
                budget_variance_service.compute_variance(
                    user_id=seed_user["user"].id,
                    window_type="pay_period",
                )

    def test_month_requires_month_and_year(self, app, seed_user):
        """ValueError raised when month window lacks month or year."""
        with app.app_context():
            with pytest.raises(ValueError, match="month and year are required"):
                budget_variance_service.compute_variance(
                    user_id=seed_user["user"].id,
                    window_type="month",
                    month=1,
                )

    def test_year_requires_year(self, app, seed_user):
        """ValueError raised when year window lacks year."""
        with app.app_context():
            with pytest.raises(ValueError, match="year is required"):
                budget_variance_service.compute_variance(
                    user_id=seed_user["user"].id,
                    window_type="year",
                )


# ── Window Label Tests ───────────────────────────────────────────────


class TestWindowLabels:
    """Tests for window label formatting."""

    def test_window_label_pay_period(self, app, seed_user, seed_periods):
        """Pay period label uses start - end, year format."""
        with app.app_context():
            result = budget_variance_service.compute_variance(
                user_id=seed_user["user"].id,
                window_type="pay_period",
                period_id=seed_periods[0].id,
            )
            # Period 0: Jan 02 - Jan 15, 2026.
            assert "Jan 02" in result.window_label
            assert "Jan 15" in result.window_label
            assert "2026" in result.window_label

    def test_window_label_month(self, app, seed_user, seed_periods):
        """Month label uses full month name and year."""
        with app.app_context():
            result = budget_variance_service.compute_variance(
                user_id=seed_user["user"].id,
                window_type="month",
                month=1,
                year=2026,
            )
            assert result.window_label == "January 2026"

    def test_window_label_year(self, app, seed_user, seed_periods):
        """Year label is just the year string."""
        with app.app_context():
            result = budget_variance_service.compute_variance(
                user_id=seed_user["user"].id,
                window_type="year",
                year=2026,
            )
            assert result.window_label == "2026"


# ── Transaction Count Tests ──────────────────────────────────────────


class TestTransactionCounts:
    """Tests for transaction_count at item and report level."""

    def test_transaction_count_on_item(self, app, seed_user, seed_periods, db):
        """Item transaction_count reflects the number of transactions."""
        with app.app_context():
            for i in range(3):
                _add_txn(
                    db.session, seed_user, seed_periods[0],
                    f"Item {i}", "100.00",
                    category_key="Rent",
                    due_date=date(2026, 1, 5 + i),
                )
            db.session.commit()

            result = budget_variance_service.compute_variance(
                user_id=seed_user["user"].id,
                window_type="pay_period",
                period_id=seed_periods[0].id,
            )
            item = result.groups[0].items[0]
            assert item.transaction_count == 3

    def test_transaction_count_on_report(self, app, seed_user, seed_periods, db):
        """Report transaction_count equals total transactions across all items."""
        with app.app_context():
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Rent", "1000.00", category_key="Rent",
                due_date=date(2026, 1, 5),
            )
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Car", "300.00", category_key="Car Payment",
                due_date=date(2026, 1, 6),
            )
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Food", "400.00", category_key="Groceries",
                due_date=date(2026, 1, 7),
            )
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Food 2", "200.00", category_key="Groceries",
                due_date=date(2026, 1, 8),
            )
            _add_txn(
                db.session, seed_user, seed_periods[0],
                "Salary", "5000.00", is_income=True,
                category_key="Salary",
                due_date=date(2026, 1, 2),
            )
            db.session.commit()

            result = budget_variance_service.compute_variance(
                user_id=seed_user["user"].id,
                window_type="pay_period",
                period_id=seed_periods[0].id,
            )
            assert result.transaction_count == 5
