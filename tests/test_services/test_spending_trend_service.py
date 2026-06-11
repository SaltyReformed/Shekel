"""
Shekel Budget App -- Spending Trend Service Tests

Tests for the spending trend engine (Design A: recent-vs-prior-half
comparison over completed pay periods).  Covers data sufficiency, the
completed-period window (F1), the half-window change math, the min-active
(F2) and materiality (R1) eligibility gates, emerging "New" spending (R2),
trend direction/flagging, top-5 lists, group-level weighted averages, and
filter correctness.

The window is made deterministic by mocking ``date.today()`` to
``_TODAY`` (2026-07-01) and generating biweekly periods from ``_WINDOW_START``
(2026-01-02).  With those anchors exactly 12 periods have fully elapsed
inside the 6-month window, so ``n == 12`` and each half holds 6 periods --
the arithmetic in every value assertion below is computed against that
fixed shape.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.services import spending_trend_service
from app.services.spending_trend_service import _half_window_change


# ── Deterministic window anchors ─────────────────────────────────────

# Mocked "today".  Chosen as the first of a month so the 6-month window
# lower bound (first of the month 6 months earlier) is 2026-01-01.
_TODAY = date(2026, 7, 1)

# First generated payday.  Biweekly from here, exactly 12 periods have
# fully elapsed before _TODAY and fall inside the window; periods 13-14
# are still in progress / in the future and are excluded by F1.
_WINDOW_START = date(2026, 1, 2)

# Resulting in-window shape, asserted in test_window_excludes_incomplete.
_N_WINDOW = 12
_HALF = 6


# ── Helpers ──────────────────────────────────────────────────────────


def _patch_today(mock_date):
    """Point the service module's ``date`` at the fixed test clock.

    ``date.today()`` returns ``_TODAY``; ``date(y, m, d)`` construction
    still works via the side_effect so window-bound math is unaffected.
    """
    mock_date.today.return_value = _TODAY
    mock_date.side_effect = lambda *args, **kw: date(*args, **kw)


def _generate_periods(db_session, user_id, start, count):
    """Generate biweekly pay periods for testing.

    Returns the list of created PayPeriod objects, ordered chronologically.
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


def _seed_window(db_session, seed_user, count=14):
    """Create periods and return the completed in-window periods.

    Generates ``count`` biweekly periods from ``_WINDOW_START``, anchors the
    account to the first one, and returns
    ``spending_trend_service._get_window_periods(user_id, 6)`` -- the
    completed periods F1 admits.  MUST be called with the test clock already
    patched (the window query reads ``date.today()``).
    """
    periods = _generate_periods(
        db_session, seed_user["user"].id, _WINDOW_START, count,
    )
    seed_user["account"].current_anchor_period_id = periods[0].id
    db_session.commit()
    return spending_trend_service._get_window_periods(seed_user["user"].id, 6)


def _add_paid_expense(
    db_session, seed_user, period, name, amount,
    category_key=None, due_date=None, paid_at=None,
):
    """Create a settled (Done) paid expense transaction for trend testing.

    Returns the created Transaction.
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


def _fill(db_session, seed_user, window, category_key, amounts):
    """Add a paid expense to ``window[i]`` for each non-None amount.

    ``amounts`` is positional over the window periods.  ``None`` skips the
    period entirely (no transaction -> the service zero-fills it and it is
    NOT counted as a data point), which is how true gaps and ramp-from-zero
    patterns are expressed.
    """
    for period, amount in zip(window, amounts):
        if amount is None:
            continue
        _add_paid_expense(
            db_session, seed_user, period, category_key, str(amount),
            category_key=category_key,
        )


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


def _item(result, item_name):
    """Return the named ItemTrend from a report, or None."""
    return next(
        (i for i in result.all_items if i.item_name == item_name), None,
    )


# ── Half-Window Change Tests (Design A core math) ───────────────────


class TestHalfWindowChange:
    """Tests for the _half_window_change pure math function."""

    def test_even_increasing(self):
        """[10,20,30,40]: prior avg 15, recent avg 35 -> +133.33%, +20.00."""
        abs_change, pct = _half_window_change(
            [Decimal(str(x)) for x in [10, 20, 30, 40]],
        )
        # prior=(10+20)/2=15, recent=(30+40)/2=35; (35-15)/15*100=133.33.
        assert abs_change == Decimal("20.00")
        assert pct == Decimal("133.33")

    def test_odd_drops_middle(self):
        """[10,20,30,40,50]: middle (30) dropped -> prior 15, recent 45."""
        abs_change, pct = _half_window_change(
            [Decimal(str(x)) for x in [10, 20, 30, 40, 50]],
        )
        # half=2: prior=(10+20)/2=15, recent=(40+50)/2=45; (45-15)/15*100=200.
        assert abs_change == Decimal("30.00")
        assert pct == Decimal("200.00")

    def test_decreasing(self):
        """[40,30,20,10]: prior 35, recent 15 -> -57.14%, -20.00."""
        abs_change, pct = _half_window_change(
            [Decimal(str(x)) for x in [40, 30, 20, 10]],
        )
        # (15-35)/35*100 = -57.142857... -> -57.14 (ROUND_HALF_UP).
        assert abs_change == Decimal("-20.00")
        assert pct == Decimal("-57.14")

    def test_flat(self):
        """[100,100,100,100]: no change -> 0.00%, 0.00."""
        abs_change, pct = _half_window_change([Decimal("100")] * 4)
        assert abs_change == Decimal("0.00")
        assert pct == Decimal("0.00")

    def test_new_prior_zero(self):
        """Prior half all zero -> emerging: pct None, positive dollar delta."""
        abs_change, pct = _half_window_change(
            [Decimal("0"), Decimal("0"), Decimal("100"), Decimal("100")],
        )
        # prior avg 0 < 5 floor -> None; recent avg 100, delta 100.00.
        assert pct is None
        assert abs_change == Decimal("100.00")

    def test_new_prior_below_floor(self):
        """Prior half avg below the $5 floor -> emerging (None), not a %."""
        abs_change, pct = _half_window_change(
            [Decimal("2"), Decimal("2"), Decimal("100"), Decimal("100")],
        )
        # prior avg 2 < 5 -> None; recent avg 100, delta 98.00.
        assert pct is None
        assert abs_change == Decimal("98.00")

    def test_baseline_floor_boundary_computes_pct(self):
        """Prior half avg exactly at the floor ($5) still yields a percentage."""
        abs_change, pct = _half_window_change(
            [Decimal("5"), Decimal("5"), Decimal("100"), Decimal("100")],
        )
        # prior avg 5 is NOT < 5 -> compute: (100-5)/5*100 = 1900.00.
        assert abs_change == Decimal("95.00")
        assert pct == Decimal("1900.00")

    def test_single_value(self):
        """One period cannot be split -> (0, None)."""
        abs_change, pct = _half_window_change([Decimal("42")])
        assert abs_change == Decimal("0")
        assert pct is None

    def test_empty(self):
        """Empty input -> (0, None), no exception."""
        abs_change, pct = _half_window_change([])
        assert abs_change == Decimal("0")
        assert pct is None

    def test_outputs_are_decimal(self):
        """Numeric outputs are Decimal, not float."""
        abs_change, pct = _half_window_change(
            [Decimal("10.50"), Decimal("20.75"), Decimal("30.25"), Decimal("40.00")],
        )
        assert isinstance(abs_change, Decimal)
        assert isinstance(pct, Decimal)


# ── Data Sufficiency Tests ──────────────────────────────────────────


class TestDataSufficiency:
    """Tests for window auto-selection based on data availability."""

    def test_insufficient_data(self, app, seed_user, db):
        """Under 3 distinct months of paid data -> insufficient, window 0."""
        with app.app_context():
            periods = _generate_periods(
                db.session, seed_user["user"].id, date(2026, 1, 2), 2,
            )
            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()

            # One paid expense -> a single distinct month (January).
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
    def test_preliminary_three_months(self, mock_date, app, seed_user, db):
        """Exactly 3 distinct months of paid data -> preliminary, window 3."""
        _patch_today(mock_date)
        with app.app_context():
            # Periods from 2026-04-03 cover Apr/May/Jun 2026 = 3 months.
            periods = _generate_periods(
                db.session, seed_user["user"].id, date(2026, 4, 3), 6,
            )
            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()
            for p in periods:
                _add_paid_expense(
                    db.session, seed_user, p, "Groceries", "100.00",
                    category_key="Groceries",
                )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            assert result.data_sufficiency == "preliminary"
            assert result.window_months == 3

    @patch("app.services.spending_trend_service.date")
    def test_preliminary_five_months(self, mock_date, app, seed_user, db):
        """5 distinct months (3 <= 5 < 6) -> preliminary, window 3."""
        _patch_today(mock_date)
        with app.app_context():
            # Periods from 2026-02-06 cover Feb-Jun 2026 = 5 months.
            periods = _generate_periods(
                db.session, seed_user["user"].id, date(2026, 2, 6), 11,
            )
            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()
            for p in periods:
                _add_paid_expense(
                    db.session, seed_user, p, "Groceries", "100.00",
                    category_key="Groceries",
                )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            assert result.data_sufficiency == "preliminary"
            assert result.window_months == 3

    @patch("app.services.spending_trend_service.date")
    def test_sufficient_six_months(self, mock_date, app, seed_user, db):
        """6+ distinct months of paid data -> sufficient, window 6."""
        _patch_today(mock_date)
        with app.app_context():
            window = _seed_window(db.session, seed_user)
            # Rent in all 12 window periods (Jan-Jun) = 6 distinct months.
            _fill(db.session, seed_user, window, "Rent", ["1200.00"] * _N_WINDOW)
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            assert result.data_sufficiency == "sufficient"
            assert result.window_months == 6

    @patch("app.services.spending_trend_service.date")
    def test_window_excludes_incomplete(self, mock_date, app, seed_user, db):
        """F1: only fully-elapsed periods are in-window; in-progress/future out.

        With _TODAY=2026-07-01 and 14 biweekly periods from 2026-01-02, the
        first 12 have end_date < today and are admitted; period 13 is still
        in progress (ends 2026-07-02) and period 14 is in the future, so both
        are excluded.
        """
        _patch_today(mock_date)
        with app.app_context():
            periods = _generate_periods(
                db.session, seed_user["user"].id, _WINDOW_START, 14,
            )
            db.session.commit()

            window = spending_trend_service._get_window_periods(
                seed_user["user"].id, 6,
            )
            assert len(window) == _N_WINDOW
            assert all(p.end_date < _TODAY for p in window)

            in_ids = {p.id for p in window}
            future = [p for p in periods if p.end_date >= _TODAY]
            assert future, "fixture must include >=1 incomplete period"
            assert all(p.id not in in_ids for p in future)


# ── Trend Detection Tests ───────────────────────────────────────────


class TestTrendDetection:
    """Tests for trend direction, magnitude, and flagging (Design A)."""

    @patch("app.services.spending_trend_service.date")
    def test_increasing(self, mock_date, app, seed_user, db):
        """Rising spend -> up, exact +48.00% over the 12-period window."""
        _patch_today(mock_date)
        with app.app_context():
            window = _seed_window(db.session, seed_user)
            amounts = [100 + 10 * i for i in range(_N_WINDOW)]
            _fill(db.session, seed_user, window, "Groceries", amounts)
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            item = _item(result, "Groceries")
            # prior=(100..150)/6=125, recent=(160..210)/6=185.
            # pct=(185-125)/125*100=48.00; delta=185-125=60.00.
            assert item.trend_direction == "up"
            assert item.pct_change == Decimal("48.00")
            assert item.absolute_change == Decimal("60.00")
            assert item.period_average == Decimal("155.00")
            assert item.data_points == _N_WINDOW
            assert item.is_flagged is True

    @patch("app.services.spending_trend_service.date")
    def test_decreasing(self, mock_date, app, seed_user, db):
        """Falling spend -> down, exact -32.43% over the window."""
        _patch_today(mock_date)
        with app.app_context():
            window = _seed_window(db.session, seed_user)
            amounts = [210 - 10 * i for i in range(_N_WINDOW)]
            _fill(db.session, seed_user, window, "Groceries", amounts)
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            item = _item(result, "Groceries")
            # prior=(210..160)/6=185, recent=(150..100)/6=125.
            # pct=(125-185)/185*100=-32.4324... -> -32.43; delta=-60.00.
            assert item.trend_direction == "down"
            assert item.pct_change == Decimal("-32.43")
            assert item.absolute_change == Decimal("-60.00")
            assert item.is_flagged is True

    @patch("app.services.spending_trend_service.date")
    def test_flat(self, mock_date, app, seed_user, db):
        """Constant spend -> flat, 0.00%, not flagged."""
        _patch_today(mock_date)
        with app.app_context():
            window = _seed_window(db.session, seed_user)
            _fill(db.session, seed_user, window, "Rent", ["1200.00"] * _N_WINDOW)
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            item = _item(result, "Rent")
            assert item.trend_direction == "flat"
            assert item.pct_change == Decimal("0.00")
            assert item.is_flagged is False

    @patch("app.services.spending_trend_service.date")
    def test_ramp_from_zero_is_new(self, mock_date, app, seed_user, db):
        """A category with no prior-half spend is reported as emerging 'New'.

        The prior half is all zero, so there is no baseline to divide by:
        pct_change is None (renders 'New'), direction is up, and the dollar
        delta is the recent-half average.  R2's chosen treatment.
        """
        _patch_today(mock_date)
        with app.app_context():
            window = _seed_window(db.session, seed_user)
            # Rent baseline (sufficiency); Car ramps from zero: nothing in
            # the first 6 periods, $350 in the last 6.
            _fill(db.session, seed_user, window, "Rent", ["1200.00"] * _N_WINDOW)
            _fill(
                db.session, seed_user, window, "Car Payment",
                [None] * _HALF + [350] * _HALF,
            )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            car = _item(result, "Car Payment")
            assert car is not None
            assert car.pct_change is None
            assert car.trend_direction == "up"
            # recent avg 350, prior avg 0 -> delta 350.00.
            assert car.absolute_change == Decimal("350.00")
            # period_average = 6*350 / 12 = 175.00.
            assert car.period_average == Decimal("175.00")
            assert car.data_points == _HALF
            assert car.is_flagged is True
            # Emerging spend ranks as an increase, never a decrease.
            assert "Car Payment" in [i.item_name for i in result.top_increasing]
            assert "Car Payment" not in [i.item_name for i in result.top_decreasing]


# ── Eligibility Gate Tests (F2 / R1) ────────────────────────────────


class TestEligibilityGates:
    """Tests for the min-active (F2) and materiality (R1) exclusions."""

    @patch("app.services.spending_trend_service.date")
    def test_below_min_active_periods_excluded(self, mock_date, app, seed_user, db):
        """F2: a category active in < 3 periods is excluded entirely."""
        _patch_today(mock_date)
        with app.app_context():
            window = _seed_window(db.session, seed_user)
            _fill(db.session, seed_user, window, "Rent", ["1200.00"] * _N_WINDOW)
            # Car active in only 2 periods (material $500 each: passes R1,
            # so the exclusion is attributable to F2 alone).
            _fill(
                db.session, seed_user, window, "Car Payment",
                [500, 500] + [None] * (_N_WINDOW - 2),
            )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            assert _item(result, "Car Payment") is None

    @patch("app.services.spending_trend_service.date")
    def test_below_materiality_excluded(self, mock_date, app, seed_user, db):
        """R1: a category averaging < $20/period is excluded entirely."""
        _patch_today(mock_date)
        with app.app_context():
            window = _seed_window(db.session, seed_user)
            _fill(db.session, seed_user, window, "Rent", ["1200.00"] * _N_WINDOW)
            # Car active in 3 periods (passes F2) but only $20 total/3 =
            # $60 over 12 periods -> $5.00/period average < $20 floor.
            _fill(
                db.session, seed_user, window, "Car Payment",
                [20, 20, 20] + [None] * (_N_WINDOW - 3),
            )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            assert _item(result, "Car Payment") is None

    @patch("app.services.spending_trend_service.date")
    def test_materiality_boundary_included(self, mock_date, app, seed_user, db):
        """R1 boundary: an average of exactly $20.00/period is included."""
        _patch_today(mock_date)
        with app.app_context():
            window = _seed_window(db.session, seed_user)
            _fill(db.session, seed_user, window, "Rent", ["1200.00"] * _N_WINDOW)
            # $80 in 3 periods -> $240 / 12 = exactly $20.00/period.
            _fill(
                db.session, seed_user, window, "Car Payment",
                [80, 80, 80] + [None] * (_N_WINDOW - 3),
            )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            car = _item(result, "Car Payment")
            assert car is not None
            assert car.period_average == Decimal("20.00")


# ── Threshold and Flagging Tests ────────────────────────────────────


class TestThresholdFlagging:
    """Tests for threshold-based flagging."""

    @patch("app.services.spending_trend_service.date")
    def test_below_threshold_not_flagged(self, mock_date, app, seed_user, db):
        """A 5% change with a 10% threshold -> up but not flagged."""
        _patch_today(mock_date)
        with app.app_context():
            window = _seed_window(db.session, seed_user)
            # prior avg 100, recent avg 105 -> +5.00%.
            _fill(
                db.session, seed_user, window, "Groceries",
                [100] * _HALF + [105] * _HALF,
            )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id, threshold=Decimal("0.1000"),
            )
            item = _item(result, "Groceries")
            assert item.pct_change == Decimal("5.00")
            assert item.trend_direction == "up"
            assert item.is_flagged is False

    @patch("app.services.spending_trend_service.date")
    def test_custom_threshold_flags(self, mock_date, app, seed_user, db):
        """The same 5% change with a 5% threshold -> flagged (>= threshold)."""
        _patch_today(mock_date)
        with app.app_context():
            window = _seed_window(db.session, seed_user)
            _fill(
                db.session, seed_user, window, "Groceries",
                [100] * _HALF + [105] * _HALF,
            )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id, threshold=Decimal("0.0500"),
            )
            item = _item(result, "Groceries")
            assert item.pct_change == Decimal("5.00")
            assert item.is_flagged is True


# ── Top-5 List Tests ────────────────────────────────────────────────


class TestTopLists:
    """Tests for top-5 increasing/decreasing lists."""

    @patch("app.services.spending_trend_service.date")
    def test_flat_items_not_in_top_lists(self, mock_date, app, seed_user, db):
        """Flat items appear in neither top list."""
        _patch_today(mock_date)
        with app.app_context():
            window = _seed_window(db.session, seed_user)
            _fill(db.session, seed_user, window, "Rent", ["1200.00"] * _N_WINDOW)
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            inc = [i.item_name for i in result.top_increasing]
            dec = [i.item_name for i in result.top_decreasing]
            assert "Rent" not in inc
            assert "Rent" not in dec

    @patch("app.services.spending_trend_service.date")
    def test_real_pct_ranks_above_new(self, mock_date, app, seed_user, db):
        """In top_increasing, a real-percentage rise outranks an emerging row.

        Real-percentage increases are ordered before 'New' rows (which have
        no percentage to compare), so a +48% Groceries rise precedes the
        emerging Car Payment in the increasing list.
        """
        _patch_today(mock_date)
        with app.app_context():
            window = _seed_window(db.session, seed_user)
            _fill(
                db.session, seed_user, window, "Groceries",
                [100 + 10 * i for i in range(_N_WINDOW)],
            )
            _fill(
                db.session, seed_user, window, "Car Payment",
                [None] * _HALF + [350] * _HALF,
            )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            names = [i.item_name for i in result.top_increasing]
            assert names.index("Groceries") < names.index("Car Payment")


# ── Group-Level Trend Tests ─────────────────────────────────────────


class TestGroupTrends:
    """Tests for group-level weighted average trends."""

    @patch("app.services.spending_trend_service.date")
    def test_group_single_item(self, mock_date, app, seed_user, db):
        """A group with one item -> group pct_change equals the item's."""
        _patch_today(mock_date)
        with app.app_context():
            window = _seed_window(db.session, seed_user)
            # Rent is the only item in group "Home".
            _fill(
                db.session, seed_user, window, "Rent",
                [1000 + 20 * i for i in range(_N_WINDOW)],
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
            # prior avg 1050, recent avg 1170 -> +11.43%; single item, so the
            # spending-weighted group average collapses to the item's value.
            assert item.pct_change == Decimal("11.43")
            assert group.pct_change == item.pct_change

    @patch("app.services.spending_trend_service.date")
    def test_group_excludes_new_item_from_weighting(self, mock_date, app, seed_user, db):
        """An emerging ('New') item does not enter the group weighted average.

        Group "Auto" holds a measurable Car Payment (down) and an emerging
        Gas item.  The emerging item has no percentage, so the group's
        weighted average reflects only Car Payment, yet both items are
        still listed under the group.
        """
        _patch_today(mock_date)
        with app.app_context():
            # A second item in the "Auto" group (seed_user ships only one).
            gas = Category(
                user_id=seed_user["user"].id,
                group_name="Auto",
                item_name="Gas",
            )
            db.session.add(gas)
            db.session.flush()
            seed_user["categories"]["Gas"] = gas

            window = _seed_window(db.session, seed_user)
            _fill(db.session, seed_user, window, "Rent", ["1200.00"] * _N_WINDOW)
            # Car Payment: clear decrease (prior 200, recent 100 -> -50.00%).
            _fill(
                db.session, seed_user, window, "Car Payment",
                [200] * _HALF + [100] * _HALF,
            )
            # Gas: ramps from zero -> emerging 'New', no percentage.
            _fill(
                db.session, seed_user, window, "Gas",
                [None] * _HALF + [200] * _HALF,
            )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            group = next(
                (g for g in result.group_trends if g.group_name == "Auto"),
                None,
            )
            assert group is not None
            # Only the measurable Car Payment drives the group: -50.00%.
            assert group.pct_change == Decimal("-50.00")
            assert {i.item_name for i in group.items} == {"Car Payment", "Gas"}


# ── Zero-Period Handling Tests ──────────────────────────────────────


class TestZeroPeriods:
    """Tests for zero-spending period handling."""

    @patch("app.services.spending_trend_service.date")
    def test_gaps_count_as_zero(self, mock_date, app, seed_user, db):
        """A category with gaps has data_points below the window length."""
        _patch_today(mock_date)
        with app.app_context():
            window = _seed_window(db.session, seed_user)
            _fill(db.session, seed_user, window, "Rent", ["1200.00"] * _N_WINDOW)
            # Car only in even-indexed window periods (6 of 12), $100 each.
            car_amounts = [100 if i % 2 == 0 else None for i in range(_N_WINDOW)]
            _fill(db.session, seed_user, window, "Car Payment", car_amounts)
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            car = _item(result, "Car Payment")
            assert car is not None
            # 6 active periods < 12 window periods.
            assert car.data_points == _HALF
            assert car.data_points < result.window_periods


# ── Filter Tests ────────────────────────────────────────────────────


class TestFilters:
    """Tests for transaction filter correctness.

    Each off-status / off-type control row spans 3 material periods, so it
    would be trendable (passes F2 and R1) if it were not filtered -- the
    absence in the result is attributable to the filter, not the gates.
    """

    @patch("app.services.spending_trend_service.date")
    def test_excludes_projected(self, mock_date, app, seed_user, db):
        """Only settled transactions contribute; projected ones do not."""
        _patch_today(mock_date)
        with app.app_context():
            window = _seed_window(db.session, seed_user)
            _fill(db.session, seed_user, window, "Rent", ["1200.00"] * _N_WINDOW)
            for p in window[:3]:
                _add_txn_with_status(
                    db.session, seed_user, p, "Future Car", "500.00",
                    StatusEnum.PROJECTED, category_key="Car Payment",
                )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            assert _item(result, "Car Payment") is None

    @patch("app.services.spending_trend_service.date")
    def test_excludes_income(self, mock_date, app, seed_user, db):
        """Income transactions are not analyzed for spending trends."""
        _patch_today(mock_date)
        with app.app_context():
            window = _seed_window(db.session, seed_user)
            _fill(db.session, seed_user, window, "Rent", ["1200.00"] * _N_WINDOW)
            for p in window[:3]:
                _add_txn_with_status(
                    db.session, seed_user, p, "Salary", "5000.00",
                    StatusEnum.RECEIVED, category_key="Salary", is_income=True,
                )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            assert _item(result, "Salary") is None

    @patch("app.services.spending_trend_service.date")
    def test_excludes_deleted(self, mock_date, app, seed_user, db):
        """Soft-deleted transactions are excluded."""
        _patch_today(mock_date)
        with app.app_context():
            window = _seed_window(db.session, seed_user)
            _fill(db.session, seed_user, window, "Rent", ["1200.00"] * _N_WINDOW)
            for p in window[:3]:
                _add_txn_with_status(
                    db.session, seed_user, p, "Deleted Car", "500.00",
                    StatusEnum.DONE, category_key="Car Payment", is_deleted=True,
                )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            assert _item(result, "Car Payment") is None

    @patch("app.services.spending_trend_service.date")
    def test_excludes_cancelled(self, mock_date, app, seed_user, db):
        """Cancelled transactions are excluded (not a settled status)."""
        _patch_today(mock_date)
        with app.app_context():
            window = _seed_window(db.session, seed_user)
            _fill(db.session, seed_user, window, "Rent", ["1200.00"] * _N_WINDOW)
            for p in window[:3]:
                _add_txn_with_status(
                    db.session, seed_user, p, "Cancelled Car", "500.00",
                    StatusEnum.CANCELLED, category_key="Car Payment",
                )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            assert _item(result, "Car Payment") is None


# ── OP-3: Average Days Before Due ───────────────────────────────────


class TestAvgDaysBeforeDue:
    """Tests for the OP-3 avg_days_before_due metric."""

    @patch("app.services.spending_trend_service.date")
    def test_avg_days_before_due_early(self, mock_date, app, seed_user, db):
        """All txns paid 3 days before due -> avg_days_before_due == 3.00."""
        _patch_today(mock_date)
        with app.app_context():
            window = _seed_window(db.session, seed_user)
            for p in window:
                due = p.start_date
                paid = datetime(
                    due.year, due.month, due.day, tzinfo=timezone.utc,
                ) - timedelta(days=3)
                _add_paid_expense(
                    db.session, seed_user, p, "Rent", "1200.00",
                    category_key="Rent", due_date=due, paid_at=paid,
                )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            item = _item(result, "Rent")
            assert item.avg_days_before_due == Decimal("3.00")

    @patch("app.services.spending_trend_service.date")
    def test_avg_days_before_due_late(self, mock_date, app, seed_user, db):
        """All txns paid 2 days after due -> avg_days_before_due == -2.00."""
        _patch_today(mock_date)
        with app.app_context():
            window = _seed_window(db.session, seed_user)
            for p in window:
                due = p.start_date
                paid = datetime(
                    due.year, due.month, due.day, tzinfo=timezone.utc,
                ) + timedelta(days=2)
                _add_paid_expense(
                    db.session, seed_user, p, "Rent", "1200.00",
                    category_key="Rent", due_date=due, paid_at=paid,
                )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            item = _item(result, "Rent")
            assert item.avg_days_before_due == Decimal("-2.00")

    @patch("app.services.spending_trend_service.date")
    def test_avg_days_before_due_no_data(self, mock_date, app, seed_user, db):
        """Txns without paid_at -> avg_days_before_due is None."""
        _patch_today(mock_date)
        with app.app_context():
            window = _seed_window(db.session, seed_user)
            for p in window:
                _add_paid_expense(
                    db.session, seed_user, p, "Rent", "1200.00",
                    category_key="Rent", paid_at=None,
                )
            db.session.commit()

            result = spending_trend_service.compute_trends(
                user_id=seed_user["user"].id,
            )
            item = _item(result, "Rent")
            assert item.avg_days_before_due is None
