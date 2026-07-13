"""
Shekel Budget App -- Daily end-of-day cash-flow balance series tests.

Pins the calendar's flagship running-balance producer
(:mod:`app.services.daily_balance_series`, exposed via the seam as
``balance_at.cash_daily_balance_series``).  The load-bearing property is the
reconciliation invariant: the day-textured running balance STEPS on each
day's projected flow yet lands exactly on the period-flat seam scalar at
every pay-period end, so the calendar's day cells and flow-strip line agree
with each other AND with the grid.

Scenario (``seed_periods``: biweekly from 2026-01-02, anchor = period 0 at
$1000.00):

* Period 0 (2026-01-02..01-15): Rent -500 due 01-05, Salary +2000 due 01-09.
  Net +1500 -> end 2500.
* Period 1 (2026-01-16..01-29): Car -800 due 01-20, Salary +2000 due 01-23.
  Net +1200 -> end 3700.
* Period 2 (2026-01-30..02-12): Groceries -300 due 01-30 (only 01-30..01-31
  fall in January).

Hand-computed January running balance (Option B, re-anchored per period):
01-01 seed 1000; 01-05 500; 01-09 2500; 01-15 2500; 01-20 1700; 01-23 3700;
01-29 3700; 01-30 3400; 01-31 3400.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import balance_at, daily_balance_series
from app.services.scenario_resolver import get_baseline_scenario
from app.services.resolution_context import BalanceContext

_JAN_FIRST = date(2026, 1, 1)
_JAN_LAST = date(2026, 1, 31)


def _add_txn(
    db, seed_user, period, name, amount, *,
    is_income=False, due_date=None,
    status=StatusEnum.PROJECTED, actual_amount=None,
):
    """Insert one transaction on ``period`` for the seed user's account."""
    type_id = ref_cache.txn_type_id(
        TxnTypeEnum.INCOME if is_income else TxnTypeEnum.EXPENSE,
    )
    txn = Transaction(
        account_id=seed_user["account"].id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        status_id=ref_cache.status_id(status),
        name=name,
        transaction_type_id=type_id,
        estimated_amount=Decimal(str(amount)),
        actual_amount=(
            Decimal(str(actual_amount)) if actual_amount is not None else None
        ),
        due_date=due_date,
    )
    db.session.add(txn)
    db.session.flush()
    return txn


def _seed_january(db, seed_user, seed_periods):
    """Seed the January scenario described in the module docstring."""
    p0, p1, p2 = seed_periods[0], seed_periods[1], seed_periods[2]
    _add_txn(db, seed_user, p0, "Rent", "500.00", due_date=date(2026, 1, 5))
    _add_txn(
        db, seed_user, p0, "Salary", "2000.00",
        is_income=True, due_date=date(2026, 1, 9),
    )
    _add_txn(db, seed_user, p1, "Car", "800.00", due_date=date(2026, 1, 20))
    _add_txn(
        db, seed_user, p1, "Salary", "2000.00",
        is_income=True, due_date=date(2026, 1, 23),
    )
    _add_txn(
        db, seed_user, p2, "Groceries", "300.00", due_date=date(2026, 1, 30),
    )
    db.session.commit()


def _january_series(seed_user):
    """Return the seam's January daily series for the seed user's account."""
    scenario = get_baseline_scenario(seed_user["user"].id)
    bctx = BalanceContext.build(seed_user["user"].id)
    return balance_at.cash_daily_balance_series(
        seed_user["account"], bctx, _JAN_FIRST, _JAN_LAST,
    )


class TestDailySeriesRunningBalance:
    """The day-textured running balance steps on each day's flows."""

    def test_covers_every_day_ascending(self, app, seed_user, seed_periods, db):
        """The map has one ascending key per calendar day in the range."""
        with app.app_context():
            _seed_january(db, seed_user, seed_periods)
            series = _january_series(seed_user)
        expected_days = [
            _JAN_FIRST + timedelta(days=i) for i in range(31)
        ]
        assert list(series.keys()) == expected_days

    def test_hand_computed_daily_balances(
        self, app, seed_user, seed_periods, db,
    ):
        """Each key day equals its hand-computed running balance."""
        with app.app_context():
            _seed_january(db, seed_user, seed_periods)
            series = _january_series(seed_user)
        # Seed (pre-anchor day before period 0) is the $1000 anchor.
        assert series[date(2026, 1, 1)] == Decimal("1000.00")
        assert series[date(2026, 1, 4)] == Decimal("1000.00")  # before Rent
        assert series[date(2026, 1, 5)] == Decimal("500.00")   # Rent -500
        assert series[date(2026, 1, 8)] == Decimal("500.00")   # before Salary
        assert series[date(2026, 1, 9)] == Decimal("2500.00")  # Salary +2000
        assert series[date(2026, 1, 15)] == Decimal("2500.00")  # period 0 end
        assert series[date(2026, 1, 16)] == Decimal("2500.00")  # period 1 start
        assert series[date(2026, 1, 20)] == Decimal("1700.00")  # Car -800
        assert series[date(2026, 1, 23)] == Decimal("3700.00")  # Salary +2000
        assert series[date(2026, 1, 29)] == Decimal("3700.00")  # period 1 end
        assert series[date(2026, 1, 30)] == Decimal("3400.00")  # Groceries -300
        assert series[date(2026, 1, 31)] == Decimal("3400.00")  # carries fwd

    def test_reconciles_with_seam_scalar_at_period_ends(
        self, app, seed_user, seed_periods, db,
    ):
        """series[P.end] == cash_balance_at(P.end) for each period in range.

        THE invariant: the day-textured line lands exactly on the period-flat
        seam scalar at every pay-period boundary, so the calendar reconciles
        with the grid.
        """
        with app.app_context():
            _seed_january(db, seed_user, seed_periods)
            scenario = get_baseline_scenario(seed_user["user"].id)
            bctx = BalanceContext.build(seed_user["user"].id)
            series = balance_at.cash_daily_balance_series(
                seed_user["account"], bctx, _JAN_FIRST, _JAN_LAST,
            )
            for period in seed_periods:
                if _JAN_FIRST <= period.end_date <= _JAN_LAST:
                    scalar = balance_at.cash_balance_at(
                        seed_user["account"], bctx, period.end_date,
                    )
                    assert series[period.end_date] == scalar

    def test_daily_step_equals_that_days_net(
        self, app, seed_user, seed_periods, db,
    ):
        """balance(D) - balance(D-1) equals the projected flow landing on D."""
        with app.app_context():
            _seed_january(db, seed_user, seed_periods)
            series = _january_series(seed_user)
        # Rent day: -500 step.
        assert (
            series[date(2026, 1, 5)] - series[date(2026, 1, 4)]
            == Decimal("-500.00")
        )
        # Salary day: +2000 step.
        assert (
            series[date(2026, 1, 9)] - series[date(2026, 1, 8)]
            == Decimal("2000.00")
        )
        # A no-flow day does not move the line.
        assert (
            series[date(2026, 1, 11)] - series[date(2026, 1, 10)]
            == Decimal("0.00")
        )

    def test_continuous_across_period_boundary(
        self, app, seed_user, seed_periods, db,
    ):
        """The line does not jump between a period's end and the next start."""
        with app.app_context():
            _seed_january(db, seed_user, seed_periods)
            series = _january_series(seed_user)
        assert series[date(2026, 1, 16)] == series[date(2026, 1, 15)]


class TestDailySeriesEdges:
    """Pre-anchor, settled-only, clamping, and empty-range edges."""

    def test_pre_anchor_month_is_flat_anchor(
        self, app, seed_user, seed_periods, db,
    ):
        """A month entirely before the anchor is flat at the anchor balance.

        December 2025 precedes period 0 (the anchor), so the projection never
        walks backward: every day reads the $1000 anchor.
        """
        with app.app_context():
            _seed_january(db, seed_user, seed_periods)
            scenario = get_baseline_scenario(seed_user["user"].id)
            bctx = BalanceContext.build(seed_user["user"].id)
            series = balance_at.cash_daily_balance_series(
                seed_user["account"], bctx,
                date(2025, 12, 1), date(2025, 12, 31),
            )
        assert len(series) == 31
        assert set(series.values()) == {Decimal("1000.00")}

    def test_settled_row_does_not_move_the_line(
        self, app, seed_user, seed_periods, db,
    ):
        """A settled post-anchor row is excluded from the projected line.

        The anchor already reflects settled activity, so a settled expense in
        period 1 must not lower the projected running balance (identical to
        the grid); the period-1 end stays 3700.
        """
        with app.app_context():
            _seed_january(db, seed_user, seed_periods)
            _add_txn(
                db, seed_user, seed_periods[1], "Paid bill", "150.00",
                due_date=date(2026, 1, 21),
                status=StatusEnum.DONE, actual_amount="150.00",
            )
            db.session.commit()
            series = _january_series(seed_user)
        # Unchanged from the projected-only hand computation.
        assert series[date(2026, 1, 21)] == Decimal("1700.00")
        assert series[date(2026, 1, 29)] == Decimal("3700.00")

    def test_reconciliation_holds_with_within_period_entries(
        self, app, seed_user, seed_periods, db,
    ):
        """The entry-aware reservation drives the line and still reconciles.

        Period 0 (anchor $1000): a projected $500 grocery envelope due Jan 5
        with a $300 cleared purchase dated Jan 4 (inside the period).  The
        cleared debit is already in the anchor, so the entry-aware
        reservation is max(500 - 300, 0) = 200: the line steps by -200 (not
        the -500 estimate), and the period end still equals the seam scalar.
        This guards the entry-aware path -- the reconciliation invariant is
        robust to entries dated within their period (the normal case).
        """
        with app.app_context():
            txn = _add_txn(
                db, seed_user, seed_periods[0], "Groceries", "500.00",
                due_date=date(2026, 1, 5),
            )
            db.session.add(TransactionEntry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                amount=Decimal("300.00"),
                description="Cleared purchase",
                entry_date=date(2026, 1, 4),
                is_credit=False,
                is_cleared=True,
            ))
            db.session.commit()
            scenario = get_baseline_scenario(seed_user["user"].id)
            bctx = BalanceContext.build(seed_user["user"].id)
            series = balance_at.cash_daily_balance_series(
                seed_user["account"], bctx, _JAN_FIRST, _JAN_LAST,
            )
            p0_end = seed_periods[0].end_date
            # Entry-aware reservation ($200 held back), not the $500 estimate.
            assert series[date(2026, 1, 5)] == Decimal("800.00")
            assert series[p0_end] == Decimal("800.00")
            # And it equals the seam scalar at the period end (reconciliation).
            assert series[p0_end] == balance_at.cash_balance_at(
                seed_user["account"], bctx, p0_end,
            )

    def test_out_of_period_due_date_clamps_into_its_period(
        self, app, seed_user, seed_periods, db,
    ):
        """A due_date past its period end lands the flow on the period end.

        A period-0 expense dated 2026-01-20 (inside period 1's span) clamps to
        period 0's end 01-15, so period 0 still reconciles: its flow closes by
        01-15 rather than escaping into period 1.
        """
        with app.app_context():
            _add_txn(
                db, seed_user, seed_periods[0], "Stray", "100.00",
                due_date=date(2026, 1, 20),
            )
            db.session.commit()
            scenario = get_baseline_scenario(seed_user["user"].id)
            bctx = BalanceContext.build(seed_user["user"].id)
            series = balance_at.cash_daily_balance_series(
                seed_user["account"], bctx, _JAN_FIRST, _JAN_LAST,
            )
            # Period 0 end reflects the clamped -100 (1000 - 100 = 900).
            p0_end = seed_periods[0].end_date
            assert series[p0_end] == Decimal("900.00")
            # And it equals the seam scalar there (reconciliation holds).
            assert series[p0_end] == balance_at.cash_balance_at(
                seed_user["account"], bctx, p0_end,
            )
            # The flow landed on 01-15, not on its raw 01-20 due date.
            assert series[date(2026, 1, 15)] == Decimal("900.00")
            assert series[date(2026, 1, 16)] == Decimal("900.00")

    def test_inverted_range_returns_empty(
        self, app, seed_user, seed_periods, db,
    ):
        """last_day < first_day yields an empty map, not an error."""
        with app.app_context():
            scenario = get_baseline_scenario(seed_user["user"].id)
            bctx = BalanceContext.build(seed_user["user"].id)
            series = balance_at.cash_daily_balance_series(
                seed_user["account"], bctx, _JAN_LAST, _JAN_FIRST,
            )
        assert series == {}

    def test_non_date_argument_raises_type_error(
        self, app, seed_user, seed_periods, db,
    ):
        """A datetime/None argument fails loudly rather than silently."""
        with app.app_context():
            scenario = get_baseline_scenario(seed_user["user"].id)
            bctx = BalanceContext.build(seed_user["user"].id)
            with pytest.raises(TypeError):
                balance_at.cash_daily_balance_series(
                    seed_user["account"], bctx, "2026-01-01", _JAN_LAST,
                )

    def test_scenario_none_raises_value_error(
        self, app, seed_user, seed_periods, db,
    ):
        """The seam's None-scenario guard fires before any producer work."""
        with app.app_context():
            with pytest.raises(ValueError):
                balance_at.cash_daily_balance_series(
                    seed_user["account"],
                    BalanceContext(
                        user_id=seed_user["user"].id, scenario=None,
                        as_of=date.today(),
                    ),
                    _JAN_FIRST, _JAN_LAST,
                )


class TestDailySeriesProducerDirect:
    """A couple of checks against the producer entry directly (scenario_id)."""

    def test_build_daily_series_matches_seam(
        self, app, seed_user, seed_periods, db,
    ):
        """The producer entry and the seam pass-through return the same map."""
        with app.app_context():
            _seed_january(db, seed_user, seed_periods)
            scenario = get_baseline_scenario(seed_user["user"].id)
            bctx = BalanceContext.build(seed_user["user"].id)
            via_seam = balance_at.cash_daily_balance_series(
                seed_user["account"], bctx, _JAN_FIRST, _JAN_LAST,
            )
            direct = daily_balance_series.build_daily_series(
                seed_user["account"], scenario.id, _JAN_FIRST, _JAN_LAST,
            )
        assert via_seam == direct
