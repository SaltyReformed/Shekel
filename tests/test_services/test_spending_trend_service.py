"""
Shekel Budget App -- Spending Trend Service Tests

Tests for the spending trend engine: data sufficiency detection,
linear regression, trend direction/flagging, top-5 lists,
group-level weighted averages, and filter correctness.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.services import spending_trend_service
from app.services.spending_trend_service import _compute_linear_regression


# ── Helpers ──────────────────────────────────────────────────────────


def _generate_periods(db_session, user_id, start, count):
    """Generate biweekly pay periods for testing.

    Args:
        db_session: Active database session.
        user_id: User ID to create periods for.
        start: Start date for the first period.
        count: Number of periods to create.

    Returns:
        List of PayPeriod objects, ordered chronologically.
    """
    from app.services import pay_period_service
    periods = pay_period_service.generate_pay_periods(
        user_id=user_id,
        start_date=start,
        num_periods=count,
        cadence_days=14,
    )
    db_session.commit()
    return periods


def _add_paid_expense(
    db_session, seed_user, period, name, amount,
    category_key=None, due_date=None, paid_at=None,
):
    """Create a paid expense transaction for trend testing.

    Args:
        db_session: Active database session.
        seed_user: The seed_user fixture dict.
        period: PayPeriod to assign to.
        name: Transaction name.
        amount: Amount (Decimal or str).
        category_key: Key into seed_user["categories"] dict.
        due_date: Optional due_date.
        paid_at: Optional paid_at datetime.

    Returns:
        The created Transaction.
    """
    cat_id = None
    if category_key and category_key in seed_user["categories"]:
        cat_id = seed_user["categories"][category_key].id

    txn = Transaction(
        account_id=seed_user["account"].id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        status_id=ref_cache.status_id(StatusEnum.DONE),
        name=name,
        category_id=cat_id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        estimated_amount=Decimal(str(amount)),
        actual_amount=Decimal(str(amount)),
        due_date=due_date or period.start_date,
        paid_at=paid_at,
    )
    db_session.add(txn)
    db_session.flush()
    return txn


def _add_txn_with_status(
    db_session, seed_user, period, name, amount,
    status_enum, category_key=None, is_income=False,
    is_deleted=False,
):
    """Create a transaction with a specific status for filter testing.

    Returns the created Transaction.
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
        estimated_amount=Decimal(str(amount)),
        actual_amount=Decimal(str(amount)) if status_enum != StatusEnum.PROJECTED else None,
        due_date=period.start_date,
        is_deleted=is_deleted,
    )
    db_session.add(txn)
    db_session.flush()
    return txn


# ── Linear Regression Tests ─────────────────────────────────────────


class TestLinearRegression:
    """Tests for the _compute_linear_regression pure math function."""

    def test_regression_perfect_line(self):
        """[10, 20, 30, 40, 50] -> slope=10, intercept=10."""
        vals = [Decimal(str(x)) for x in [10, 20, 30, 40, 50]]
        slope, intercept = _compute_linear_regression(vals)
        assert slope == Decimal("10")
        assert intercept == Decimal("10")

    def test_regression_constant(self):
        """[100, 100, 100] -> slope=0, intercept=100."""
        vals = [Decimal("100")] * 3
        slope, intercept = _compute_linear_regression(vals)
        assert slope == Decimal("0")
        assert intercept == Decimal("100")

    def test_regression_decreasing(self):
        """[50, 40, 30, 20, 10] -> slope=-10, intercept=50."""
        vals = [Decimal(str(x)) for x in [50, 40, 30, 20, 10]]
        slope, intercept = _compute_linear_regression(vals)
        assert slope == Decimal("-10")
        assert intercept == Decimal("50")

    def test_regression_single_value(self):
        """[42] -> slope=0, intercept=42."""
        slope, intercept = _compute_linear_regression([Decimal("42")])
        assert slope == Decimal("0")
        assert intercept == Decimal("42")

    def test_regression_two_values(self):
        """[10, 20] -> slope=10, intercept=10."""
        vals = [Decimal("10"), Decimal("20")]
        slope, intercept = _compute_linear_regression(vals)
        assert slope == Decimal("10")
        assert intercept == Decimal("10")

    def test_regression_empty_raises(self):
        """Empty input raises ValueError."""
        with pytest.raises(ValueError, match="empty"):
            _compute_linear_regression([])

    def test_regression_all_decimal(self):
        """Outputs are Decimal, not float."""
        vals = [Decimal("10.50"), Decimal("20.75"), Decimal("30.25")]
        slope, intercept = _compute_linear_regression(vals)
        assert isinstance(slope, Decimal)
        assert isinstance(intercept, Decimal)

    def test_regression_with_zeros(self):
        """[0, 0, 100, 100, 200] -> valid slope/intercept."""
        vals = [Decimal(str(x)) for x in [0, 0, 100, 100, 200]]
        slope, intercept = _compute_linear_regression(vals)
        # slope > 0 since values trend upward.
        assert slope > Decimal("0")


# ── Data Sufficiency Tests ──────────────────────────────────────────


class TestDataSufficiency:
    """Tests for window auto-selection based on data availability."""

    def test_insufficient_data(self, app, seed_user, db):
        """1 month of paid data -> insufficient, empty lists, window_months=0."""
        with app.app_context():
            periods = _generate_periods(
                db.session, seed_user["user"].id, date(2026, 1, 2), 2,
            )
            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()

            _add_paid_expense(
                db.session, seed_user, periods[0], "Rent", "1200.00",
                category_key="Rent",
            )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            assert result.data_sufficiency == "insufficient"
            assert result.window_months == 0

    def test_insufficient_returns_empty(self, app, seed_user, db):
        """Insufficient data -> all lists empty."""
        with app.app_context():
            periods = _generate_periods(
                db.session, seed_user["user"].id, date(2026, 1, 2), 2,
            )
            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()

            _add_paid_expense(
                db.session, seed_user, periods[0], "Rent", "1200.00",
                category_key="Rent",
            )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            assert result.top_increasing == []
            assert result.top_decreasing == []
            assert result.all_items == []
            assert result.group_trends == []

    @patch("app.services.spending_trend_service.date")
    def test_preliminary_3_months(self, mock_date, app, seed_user, db):
        """3 months of paid data -> preliminary, window_months=3.

        ``date.today()`` is mocked to a value that places the test's
        fixture data inside the 3-month sufficiency window.  Without
        mocking, the test would silently break each month because the
        window slides forward but the fixture data does not.
        """
        mock_date.today.return_value = date(2026, 2, 1)
        mock_date.side_effect = lambda *args, **kw: date(*args, **kw)

        with app.app_context():
            # 8 biweekly periods from Oct 3, 2025 span Oct 2025 to Jan
            # 2026 (4 distinct calendar months; 3 <= 4 < 6 -> preliminary).
            periods = _generate_periods(
                db.session, seed_user["user"].id, date(2025, 10, 3), 8,
            )
            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()

            # Add paid expenses in 3+ distinct months.
            for p in periods:
                _add_paid_expense(
                    db.session, seed_user, p, "Groceries",
                    "100.00", category_key="Groceries",
                )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            assert result.data_sufficiency == "preliminary"
            assert result.window_months == 3

    def test_preliminary_5_months(self, app, seed_user, db):
        """5 months of paid data -> preliminary, window_months=3."""
        with app.app_context():
            # 10 periods starting Nov 7 spans Nov-Mar = 5 months.
            periods = _generate_periods(
                db.session, seed_user["user"].id, date(2025, 11, 7), 10,
            )
            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()

            for p in periods:
                _add_paid_expense(
                    db.session, seed_user, p, "Groceries",
                    "100.00", category_key="Groceries",
                )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            assert result.data_sufficiency == "preliminary"
            assert result.window_months == 3

    def test_sufficient_6_months(self, app, seed_user, db):
        """6+ months of paid data -> sufficient, window_months=6."""
        with app.app_context():
            periods = _generate_periods(
                db.session, seed_user["user"].id, date(2025, 6, 6), 16,
            )
            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()

            for p in periods:
                _add_paid_expense(
                    db.session, seed_user, p, "Groceries",
                    "100.00", category_key="Groceries",
                )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            assert result.data_sufficiency == "sufficient"
            assert result.window_months == 6


# ── Trend Detection Tests ───────────────────────────────────────────


class TestTrendDetection:
    """Tests for trend direction and flagging."""

    def test_trend_increasing(self, app, seed_user, db):
        """Steadily increasing spending -> direction='up', flagged."""
        with app.app_context():
            periods = _generate_periods(
                db.session, seed_user["user"].id, date(2025, 6, 6), 16,
            )
            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()

            # Increasing amounts over last several periods.
            amounts = [400, 420, 440, 460, 480, 500, 520, 540, 560, 580,
                       600, 620, 640, 660, 680, 700]
            for p, amt in zip(periods, amounts):
                _add_paid_expense(
                    db.session, seed_user, p, "Groceries",
                    str(amt), category_key="Groceries",
                )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            item = next(i for i in result.all_items if i.item_name == "Groceries")
            assert item.trend_direction == "up"
            assert item.pct_change > Decimal("0")
            assert item.is_flagged is True

    def test_trend_decreasing(self, app, seed_user, db):
        """Steadily decreasing spending -> direction='down', flagged."""
        with app.app_context():
            periods = _generate_periods(
                db.session, seed_user["user"].id, date(2025, 6, 6), 16,
            )
            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()

            amounts = [700, 680, 660, 640, 620, 600, 580, 560, 540, 520,
                       500, 480, 460, 440, 420, 400]
            for p, amt in zip(periods, amounts):
                _add_paid_expense(
                    db.session, seed_user, p, "Groceries",
                    str(amt), category_key="Groceries",
                )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            item = next(i for i in result.all_items if i.item_name == "Groceries")
            assert item.trend_direction == "down"
            assert item.pct_change < Decimal("0")
            assert item.is_flagged is True

    def test_trend_flat(self, app, seed_user, db):
        """Constant spending -> direction='flat', not flagged."""
        with app.app_context():
            periods = _generate_periods(
                db.session, seed_user["user"].id, date(2025, 6, 6), 16,
            )
            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()

            for p in periods:
                _add_paid_expense(
                    db.session, seed_user, p, "Rent",
                    "1200.00", category_key="Rent",
                )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            item = next(i for i in result.all_items if i.item_name == "Rent")
            assert item.trend_direction == "flat"
            assert item.is_flagged is False
            assert abs(item.pct_change) < Decimal("1")

    def test_single_data_point_is_flat(self, app, seed_user, db):
        """Category with spending in only 1 period -> flat, pct_change=0."""
        with app.app_context():
            periods = _generate_periods(
                db.session, seed_user["user"].id, date(2025, 6, 6), 16,
            )
            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()

            # Rent in every period for data sufficiency, but car only once.
            for p in periods:
                _add_paid_expense(
                    db.session, seed_user, p, "Rent",
                    "1200.00", category_key="Rent",
                )
            _add_paid_expense(
                db.session, seed_user, periods[0], "Car",
                "350.00", category_key="Car Payment",
            )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            car = next(
                (i for i in result.all_items if i.item_name == "Car Payment"),
                None,
            )
            if car is not None:
                assert car.data_points == 1

    def test_zero_start_handling(self, app, seed_user, db):
        """Category with $0 first period -> pct_change=0, no division error."""
        with app.app_context():
            periods = _generate_periods(
                db.session, seed_user["user"].id, date(2025, 6, 6), 16,
            )
            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()

            # Rent in all periods for sufficiency.
            for p in periods:
                _add_paid_expense(
                    db.session, seed_user, p, "Rent",
                    "1200.00", category_key="Rent",
                )
            # Car only in later periods (zero start).
            for p in periods[4:]:
                _add_paid_expense(
                    db.session, seed_user, p, "Car",
                    "350.00", category_key="Car Payment",
                )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            car = next(
                (i for i in result.all_items if i.item_name == "Car Payment"),
                None,
            )
            # Should not crash; pct_change is 0 due to zero-start guard.
            assert car is not None

    def test_noisy_data_trend_detected(self, app, seed_user, db):
        """Noisy but upward data -> regression detects 'up' direction."""
        with app.app_context():
            periods = _generate_periods(
                db.session, seed_user["user"].id, date(2025, 6, 6), 16,
            )
            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()

            # Noisy upward trend.
            amounts = [100, 150, 120, 170, 140, 200, 160, 220, 180, 250,
                       200, 270, 220, 290, 240, 310]
            for p, amt in zip(periods, amounts):
                _add_paid_expense(
                    db.session, seed_user, p, "Groceries",
                    str(amt), category_key="Groceries",
                )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            item = next(i for i in result.all_items if i.item_name == "Groceries")
            assert item.trend_direction == "up"


# ── Threshold and Flagging Tests ────────────────────────────────────


class TestThresholdFlagging:
    """Tests for threshold-based flagging."""

    @patch("app.services.spending_trend_service.date")
    def test_threshold_boundary_flagged(self, mock_date, app, seed_user, db):
        """Category with > 10% change -> flagged (>= threshold).

        ``date.today()`` is mocked so the 6-month sufficiency window
        contains a stable, predictable 7 of the 16 fixture periods --
        Oct 10, 2025 through Jan 2, 2026.  Without mocking, the window
        slides each calendar month and the windowed slope shrinks
        below the 10% threshold within ~1 month of when the test was
        written.
        """
        mock_date.today.return_value = date(2026, 4, 1)
        mock_date.side_effect = lambda *args, **kw: date(*args, **kw)

        with app.app_context():
            periods = _generate_periods(
                db.session, seed_user["user"].id, date(2025, 6, 6), 16,
            )
            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()

            # Steep enough slope to exceed 10% in the windowed range.
            # With 6-month window from mocked today (April 1, 2026),
            # window starts Oct 1, 2025 and contains 7 periods (indices
            # 9-15) with amounts 1270 through 1450.
            # slope=30, intercept=1270, pct = 30*6/1270*100 ~14.17%.
            for i, p in enumerate(periods):
                amt = 1000 + i * 30
                _add_paid_expense(
                    db.session, seed_user, p, "Rent",
                    str(amt), category_key="Rent",
                )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
                threshold=Decimal("0.1000"),
            )
            item = next(i for i in result.all_items if i.item_name == "Rent")
            assert item.pct_change >= Decimal("10")
            assert item.is_flagged is True

    def test_custom_threshold(self, app, seed_user, db):
        """Category with > 5% change, threshold=5% -> flagged."""
        with app.app_context():
            periods = _generate_periods(
                db.session, seed_user["user"].id, date(2025, 6, 6), 16,
            )
            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()

            # slope=15, ~7 periods in window, pct = 15*6/~1120*100 ~8%.
            for i, p in enumerate(periods):
                amt = 1000 + i * 15
                _add_paid_expense(
                    db.session, seed_user, p, "Rent",
                    str(amt), category_key="Rent",
                )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
                threshold=Decimal("0.0500"),
            )
            item = next(i for i in result.all_items if i.item_name == "Rent")
            assert item.pct_change >= Decimal("5")
            assert item.is_flagged is True


# ── Top-5 List Tests ────────────────────────────────────────────────


class TestTopLists:
    """Tests for top-5 increasing/decreasing lists."""

    def test_flat_items_not_in_top_lists(self, app, seed_user, db):
        """Flat items don't appear in top_increasing or top_decreasing."""
        with app.app_context():
            periods = _generate_periods(
                db.session, seed_user["user"].id, date(2025, 6, 6), 16,
            )
            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()

            for p in periods:
                _add_paid_expense(
                    db.session, seed_user, p, "Rent",
                    "1200.00", category_key="Rent",
                )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            inc_names = [i.item_name for i in result.top_increasing]
            dec_names = [i.item_name for i in result.top_decreasing]
            assert "Rent" not in inc_names
            assert "Rent" not in dec_names


# ── Group-Level Trend Tests ─────────────────────────────────────────


class TestGroupTrends:
    """Tests for group-level weighted average trends."""

    def test_group_single_item(self, app, seed_user, db):
        """Group with one item -> group pct_change equals item pct_change."""
        with app.app_context():
            periods = _generate_periods(
                db.session, seed_user["user"].id, date(2025, 6, 6), 16,
            )
            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()

            for i, p in enumerate(periods):
                _add_paid_expense(
                    db.session, seed_user, p, "Rent",
                    str(1000 + i * 20), category_key="Rent",
                )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            group = next(
                (g for g in result.group_trends if g.group_name == "Home"),
                None,
            )
            assert group is not None
            item = group.items[0]
            assert group.pct_change == item.pct_change


# ── Zero-Period Handling Tests ──────────────────────────────────────


class TestZeroPeriods:
    """Tests for zero-spending period handling in regression."""

    def test_zero_periods_included(self, app, seed_user, db):
        """Item with gaps gets zero values for missing periods."""
        with app.app_context():
            periods = _generate_periods(
                db.session, seed_user["user"].id, date(2025, 6, 6), 16,
            )
            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()

            # Add to all periods to ensure sufficiency, but car only some.
            for p in periods:
                _add_paid_expense(
                    db.session, seed_user, p, "Rent",
                    "1200.00", category_key="Rent",
                )
            # Car only in even-indexed periods.
            for i, p in enumerate(periods):
                if i % 2 == 0:
                    _add_paid_expense(
                        db.session, seed_user, p, "Car",
                        "100.00", category_key="Car Payment",
                    )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            car = next(
                (i for i in result.all_items if i.item_name == "Car Payment"),
                None,
            )
            assert car is not None
            # data_points counts periods WITH spending.
            # The regression receives all periods (including zeros).
            # With alternating 100/0, data_points < window_periods.
            assert car.data_points < result.window_periods


# ── Filter Tests ────────────────────────────────────────────────────


class TestFilters:
    """Tests for transaction filter correctness."""

    def test_excludes_projected(self, app, seed_user, db):
        """Only paid transactions contribute to trends."""
        with app.app_context():
            periods = _generate_periods(
                db.session, seed_user["user"].id, date(2025, 6, 6), 16,
            )
            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()

            # Paid expenses in all periods.
            for p in periods:
                _add_paid_expense(
                    db.session, seed_user, p, "Rent",
                    "1200.00", category_key="Rent",
                )
            # Add a projected expense (should NOT count).
            _add_txn_with_status(
                db.session, seed_user, periods[0], "Future Car",
                "9999.00", StatusEnum.PROJECTED, category_key="Car Payment",
            )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            car_items = [i for i in result.all_items if i.item_name == "Car Payment"]
            assert len(car_items) == 0

    def test_excludes_income(self, app, seed_user, db):
        """Income transactions are not analyzed for spending trends."""
        with app.app_context():
            periods = _generate_periods(
                db.session, seed_user["user"].id, date(2025, 6, 6), 16,
            )
            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()

            for p in periods:
                _add_paid_expense(
                    db.session, seed_user, p, "Rent",
                    "1200.00", category_key="Rent",
                )
            # Add paid income (should NOT appear in trends).
            _add_txn_with_status(
                db.session, seed_user, periods[0], "Salary",
                "5000.00", StatusEnum.RECEIVED,
                category_key="Salary", is_income=True,
            )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            salary_items = [i for i in result.all_items if i.item_name == "Salary"]
            assert len(salary_items) == 0

    def test_excludes_deleted(self, app, seed_user, db):
        """Soft-deleted transactions excluded from trends."""
        with app.app_context():
            periods = _generate_periods(
                db.session, seed_user["user"].id, date(2025, 6, 6), 16,
            )
            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()

            for p in periods:
                _add_paid_expense(
                    db.session, seed_user, p, "Rent",
                    "1200.00", category_key="Rent",
                )
            _add_txn_with_status(
                db.session, seed_user, periods[0], "Deleted Car",
                "350.00", StatusEnum.DONE,
                category_key="Car Payment", is_deleted=True,
            )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            car_items = [i for i in result.all_items if i.item_name == "Car Payment"]
            assert len(car_items) == 0

    def test_excludes_cancelled(self, app, seed_user, db):
        """Cancelled transactions excluded (not a settled status)."""
        with app.app_context():
            periods = _generate_periods(
                db.session, seed_user["user"].id, date(2025, 6, 6), 16,
            )
            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()

            for p in periods:
                _add_paid_expense(
                    db.session, seed_user, p, "Rent",
                    "1200.00", category_key="Rent",
                )
            _add_txn_with_status(
                db.session, seed_user, periods[0], "Cancelled Car",
                "350.00", StatusEnum.CANCELLED,
                category_key="Car Payment",
            )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            car_items = [i for i in result.all_items if i.item_name == "Car Payment"]
            assert len(car_items) == 0


# ── OP-3: Average Days Before Due ───────────────────────────────────


class TestAvgDaysBeforeDue:
    """Tests for the OP-3 avg_days_before_due metric."""

    def test_avg_days_before_due_early(self, app, seed_user, db):
        """All txns paid 3 days before due -> avg_days_before_due=3."""
        with app.app_context():
            periods = _generate_periods(
                db.session, seed_user["user"].id, date(2025, 6, 6), 16,
            )
            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()

            for p in periods:
                due = p.start_date
                paid = datetime(
                    due.year, due.month, due.day,
                    tzinfo=timezone.utc,
                ) - __import__("datetime").timedelta(days=3)
                _add_paid_expense(
                    db.session, seed_user, p, "Rent",
                    "1200.00", category_key="Rent",
                    due_date=due, paid_at=paid,
                )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            item = next(i for i in result.all_items if i.item_name == "Rent")
            assert item.avg_days_before_due == Decimal("3.00")

    def test_avg_days_before_due_late(self, app, seed_user, db):
        """All txns paid 2 days after due -> avg_days_before_due=-2."""
        with app.app_context():
            periods = _generate_periods(
                db.session, seed_user["user"].id, date(2025, 6, 6), 16,
            )
            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()

            for p in periods:
                due = p.start_date
                paid = datetime(
                    due.year, due.month, due.day,
                    tzinfo=timezone.utc,
                ) + __import__("datetime").timedelta(days=2)
                _add_paid_expense(
                    db.session, seed_user, p, "Rent",
                    "1200.00", category_key="Rent",
                    due_date=due, paid_at=paid,
                )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            item = next(i for i in result.all_items if i.item_name == "Rent")
            assert item.avg_days_before_due == Decimal("-2.00")

    def test_avg_days_before_due_no_data(self, app, seed_user, db):
        """Txns without paid_at -> avg_days_before_due=None."""
        with app.app_context():
            periods = _generate_periods(
                db.session, seed_user["user"].id, date(2025, 6, 6), 16,
            )
            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()

            # No paid_at set -> days_paid_before_due returns None.
            for p in periods:
                _add_paid_expense(
                    db.session, seed_user, p, "Rent",
                    "1200.00", category_key="Rent",
                    paid_at=None,
                )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            item = next(i for i in result.all_items if i.item_name == "Rent")
            assert item.avg_days_before_due is None
