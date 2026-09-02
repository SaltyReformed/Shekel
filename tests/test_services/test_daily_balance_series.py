"""
Shekel Budget App -- Daily end-of-day cash-flow balance series tests.

Pins the calendar's flagship running-balance figure, the seam entry
``balance_at.cash_daily_balance_series``.  The load-bearing property is the
reconciliation invariant: the day-textured running balance STEPS on each
day's projected flow yet lands exactly on the seam scalar at every
pay-period end, so the calendar's day cells and flow-strip line agree with
each other AND with the grid.

**That invariant is now STRUCTURAL, and this suite is what remains of the
producer that had to keep it true.**  Until plan step X-c2b2 the series was its
own producer (``balance_at._daily_series``, distributing a period's
still-Projected rows over their attribution days) beside a period-FLAT scalar,
and the two measured ``$15.96`` apart on the real Checking account (finding cash
D2).  All three grains are now ONE fold sampled at a day, at a date, and at
every date, so the identity is a property of reading one running total rather
than a claim two producers maintain -- and plan step X-c2b3 deleted the producer
this file was named for.  What these tests still buy is the SAMPLING: that every
day of the range is a key, in order, with the right value on it.

Scenario (``seed_periods``: biweekly from 2026-01-02, anchor = period 0 at
$1000.00):

* Period 6 (2026-03-27..04-09): Rent -500 due 04-05, Salary +2000 due 04-09.
  Net +1500 -> end 2500.
* Period 7 (2026-04-10..04-23): Car -800 due 04-20, Salary +2000 due 04-23.
  Net +1200 -> end 3700.
* Period 8 (2026-04-24..05-07): Groceries -300 due 04-24 (only 04-24..04-30
  fall in April).

Hand-computed April running balance: 04-01 seed 1000; 04-05 500; 04-09 2500;
04-15 2500; 04-20 1700; 04-23 3700; 04-24 3400; 04-30 3400.

**The window is APRIL, forward of the suite's frozen today (2026-03-20), and
that is ruling R-G rather than convenience** (wired at plan step X-c2b2): a
still-Projected row whose date has passed lands at ``max(its date, as_of + 1
day)`` -- a plan cannot have already happened -- so January-dated projected
rows read in March draw a FLAT line at the anchor.  That is the honest answer
(none of them was ever recorded as paid), but it pins no ramp, so the fixture
states the flows where the projection actually places them.  Every
hand-computed figure above is the one this suite has always pinned.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import balance_at
from app.services.scenario_resolver import get_baseline_scenario
from app.services.balance_at import BalanceContext
from tests._test_helpers import (
    append_balance_assertion,
    default_settle_day,
    last_covered_day,
    mark_purchase_settled,
    settle_day_columns,
    settle_instant_on,
    settlement_columns,
)
from app.models.amount_ownership import AmountOwnership

_APR_FIRST = date(2026, 4, 1)
_APR_LAST = date(2026, 4, 30)


def _add_txn(
    db, seed_user, period, name, amount, *,
    is_income=False, due_date=None,
    status=StatusEnum.PROJECTED, settled_amount=None,
):
    """Insert one transaction on ``period`` for the seed user's account."""
    type_id = ref_cache.txn_type_id(
        TxnTypeEnum.INCOME if is_income else TxnTypeEnum.EXPENSE,
    )
    status_id = ref_cache.status_id(status)
    txn = Transaction(
        account_id=seed_user["account"].id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        status_id=status_id,
        name=name,
        transaction_type_id=type_id,
        amount_ownership=AmountOwnership.own(Decimal(str(amount))),
        **settlement_columns(
            default_settle_day(period, status_id), amount, settled_amount,
        ),
        due_date=due_date,
        # A settled row must carry the day its money moved; the rule for a
        # BARE-built fixture row is shared rather than restated (X-f1).
        **settle_day_columns(default_settle_day(period, status_id)),
    )
    db.session.add(txn)
    db.session.flush()
    return txn


def _seed_april(db, seed_user, seed_periods):
    """Seed the April scenario described in the module docstring."""
    p6, p7, p8 = seed_periods[6], seed_periods[7], seed_periods[8]
    _add_txn(db, seed_user, p6, "Rent", "500.00", due_date=date(2026, 4, 5))
    _add_txn(
        db, seed_user, p6, "Salary", "2000.00",
        is_income=True, due_date=date(2026, 4, 9),
    )
    _add_txn(db, seed_user, p7, "Car", "800.00", due_date=date(2026, 4, 20))
    _add_txn(
        db, seed_user, p7, "Salary", "2000.00",
        is_income=True, due_date=date(2026, 4, 23),
    )
    _add_txn(
        db, seed_user, p8, "Groceries", "300.00", due_date=date(2026, 4, 24),
    )
    db.session.commit()


def _april_series(seed_user):
    """Return the seam's April daily series for the seed user's account."""
    scenario = get_baseline_scenario(seed_user["user"].id)
    bctx = BalanceContext.build(seed_user["user"].id)
    return balance_at.cash_daily_balance_series(
        seed_user["account"], bctx, _APR_FIRST, _APR_LAST,
    )


class TestDailySeriesRunningBalance:
    """The day-textured running balance steps on each day's flows."""

    def test_covers_every_day_ascending(self, app, seed_user, seed_periods, db):
        """The map has one ascending key per calendar day in the range."""
        with app.app_context():
            _seed_april(db, seed_user, seed_periods)
            series = _april_series(seed_user)
        expected_days = [
            _APR_FIRST + timedelta(days=i) for i in range(30)
        ]
        assert list(series.keys()) == expected_days

    def test_hand_computed_daily_balances(
        self, app, seed_user, seed_periods, db,
    ):
        """Each key day equals its hand-computed running balance."""
        with app.app_context():
            _seed_april(db, seed_user, seed_periods)
            series = _april_series(seed_user)
        # Nothing lands before the first April flow, so the line opens at the
        # $1000 anchor carried forward from the January assertion.
        assert series[date(2026, 4, 1)] == Decimal("1000.00")
        assert series[date(2026, 4, 4)] == Decimal("1000.00")  # before Rent
        assert series[date(2026, 4, 5)] == Decimal("500.00")   # Rent -500
        assert series[date(2026, 4, 8)] == Decimal("500.00")   # before Salary
        assert series[date(2026, 4, 9)] == Decimal("2500.00")  # Salary +2000
        assert series[date(2026, 4, 9)] == Decimal("2500.00")  # period 6 end
        assert series[date(2026, 4, 10)] == Decimal("2500.00")  # period 7 start
        assert series[date(2026, 4, 20)] == Decimal("1700.00")  # Car -800
        assert series[date(2026, 4, 23)] == Decimal("3700.00")  # Salary +2000
        assert series[date(2026, 4, 23)] == Decimal("3700.00")  # period 7 end
        assert series[date(2026, 4, 24)] == Decimal("3400.00")  # Groceries -300
        assert series[date(2026, 4, 30)] == Decimal("3400.00")  # carries fwd

    def test_reconciles_with_seam_scalar_at_period_ends(
        self, app, seed_user, seed_periods, db,
    ):
        """series[P.end] == cash_balance_at(P.end) for each period in range.

        THE invariant: the day-textured line lands exactly on the period-flat
        seam scalar at every pay-period boundary, so the calendar reconciles
        with the grid.
        """
        with app.app_context():
            _seed_april(db, seed_user, seed_periods)
            scenario = get_baseline_scenario(seed_user["user"].id)
            bctx = BalanceContext.build(seed_user["user"].id)
            series = balance_at.cash_daily_balance_series(
                seed_user["account"], bctx, _APR_FIRST, _APR_LAST,
            )
            for period in seed_periods:
                if _APR_FIRST <= last_covered_day(period) <= _APR_LAST:
                    scalar = balance_at.cash_balance_at(
                        seed_user["account"], bctx, last_covered_day(period),
                    )
                    assert series[last_covered_day(period)] == scalar

    def test_daily_step_equals_that_days_net(
        self, app, seed_user, seed_periods, db,
    ):
        """balance(D) - balance(D-1) equals the projected flow landing on D."""
        with app.app_context():
            _seed_april(db, seed_user, seed_periods)
            series = _april_series(seed_user)
        # Rent day: -500 step.
        assert (
            series[date(2026, 4, 5)] - series[date(2026, 4, 4)]
            == Decimal("-500.00")
        )
        # Salary day: +2000 step.
        assert (
            series[date(2026, 4, 9)] - series[date(2026, 4, 8)]
            == Decimal("2000.00")
        )
        # A no-flow day does not move the line.
        assert (
            series[date(2026, 4, 11)] - series[date(2026, 4, 10)]
            == Decimal("0.00")
        )

    def test_continuous_across_period_boundary(
        self, app, seed_user, seed_periods, db,
    ):
        """The line does not jump between a period's end and the next start."""
        with app.app_context():
            _seed_april(db, seed_user, seed_periods)
            series = _april_series(seed_user)
        assert series[date(2026, 4, 10)] == series[date(2026, 4, 9)]


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
            _seed_april(db, seed_user, seed_periods)
            scenario = get_baseline_scenario(seed_user["user"].id)
            bctx = BalanceContext.build(seed_user["user"].id)
            series = balance_at.cash_daily_balance_series(
                seed_user["account"], bctx,
                date(2025, 12, 1), date(2025, 12, 31),
            )
        assert len(series) == 31
        assert set(series.values()) == {Decimal("1000.00")}

    def test_a_settled_row_moves_the_line_from_the_day_it_moved(
        self, app, seed_user, seed_periods, db,
    ):
        """A settled post-anchor row LOWERS the line (finding cash D1 closed).

        This assertion is the reverse of the one it replaces, deliberately.
        The retired producer excluded every settled row on the reasoning that
        "the anchor already reflects settled activity" -- true only if the user
        re-anchors after every payment, and on production they do not: 130
        settled rows across 45 assertion gaps were counted by NO producer,
        ``$2,108.15`` of them invisible at the instant the finding was measured
        (plan finding cash D1).  The fold counts a settled row from the day its
        money moved.

        The row is dated on its pay period's start -- period 7's 2026-04-10 --
        so the line drops ``$150`` there and stays down.  It reached that day
        through a NULL-``paid_at`` FALLBACK until plan step X-f1, and the day is
        the same one: the bare builder now states it rather than letting each
        reader substitute it.  What the fixture buys is unchanged -- a day that
        is deterministic under a frozen clock -- and what X-f1 changed is that
        the day is a recorded fact rather than a reader's opinion.
        """
        with app.app_context():
            _seed_april(db, seed_user, seed_periods)
            _add_txn(
                db, seed_user, seed_periods[7], "Paid bill", "150.00",
                due_date=date(2026, 4, 21),
                status=StatusEnum.DONE, settled_amount="150.00",
            )
            db.session.commit()
            series = _april_series(seed_user)
        # Before period 7 opens, the projected-only hand computation stands.
        assert series[date(2026, 4, 9)] == Decimal("2500.00")
        # 2500 - 150: the settled row lands on its period's start day.
        assert series[date(2026, 4, 10)] == Decimal("2350.00")
        # And it stays out of the balance for good: 3700 - 150.
        assert series[date(2026, 4, 23)] == Decimal("3550.00")

    def test_reconciliation_holds_with_within_period_entries(
        self, app, seed_user, seed_periods, db,
    ):
        """The entry-aware reservation drives the line and still reconciles.

        Period 6 (carrying the $1000 anchor forward): a projected $500 grocery
        envelope due Apr 5 with a $300 purchase made and posted Mar 19, which
        the user then confirmed against a balance they read that same day.  The
        posted debit is already inside that balance, so the entry-aware
        reservation is max(500 - 300, 0) = 200: the line steps by -200 (not
        the -500 estimate), and the period end still equals the seam scalar.
        This guards the entry-aware path -- the reconciliation invariant is
        robust to the entries-aware reservation, not just the raw estimate.

        **The purchase is dated BEFORE the suite's frozen today (2026-03-20)
        because a purchase that has not happened cannot have reached the bank**
        (ruling R-M / finding N-39): plan step X-c0 refuses a future
        ``purchased_on`` at both write doors, so the state this fixture used to
        seed -- an entry dated ahead of today -- is no longer reachable through
        the app.  The fixture is realistic for that reason and no longer for the
        reader's: plan step X-c2c1 deleted the reservation's entry window once
        the write door made it unable to drop anything, so this row's
        reservation would be the same $200 at any ``as_of``.  Backdating stays
        allowed and is what this fixture uses.

        **The Mar 19 balance assertion is what makes the purchase reconciled,
        and it is not scenery** (plan step S1-c, ruling R-DH (d)).  The retired
        ``is_cleared`` flag claimed the purchase was inside the January opening
        -- an anchor read two months before the money moved, which production
        cannot produce (finding N-132 / R8).  The assertion re-states the same
        $1,000.00 the records already hold at that instant, so its own
        correction is $0.00 and no figure below moves because of it; what it
        changes is which purchases the reservation may subtract.
        """
        with app.app_context():
            account = seed_user["account"]
            txn = _add_txn(
                db, seed_user, seed_periods[6], "Groceries", "500.00",
                due_date=date(2026, 4, 5),
            )
            entry = TransactionEntry(
                transaction_id=txn.id, account_id=txn.account_id,
                user_id=seed_user["user"].id,
                amount=Decimal("300.00"),
                description="Confirmed purchase",
                purchased_on=date(2026, 3, 19),
                is_credit=False,
            )
            db.session.add(entry)
            # The user read their bank balance on Mar 19 and it still showed
            # $1,000.00 -- no settled row has moved the account, so this
            # assertion books nothing.
            append_balance_assertion(
                db.session, account, seed_periods[5], Decimal("1000.00"),
                settle_instant_on(date(2026, 3, 19)),
            )
            mark_purchase_settled(db.session, account, entry)
            db.session.commit()
            scenario = get_baseline_scenario(seed_user["user"].id)
            bctx = BalanceContext.build(seed_user["user"].id)
            series = balance_at.cash_daily_balance_series(
                seed_user["account"], bctx, _APR_FIRST, _APR_LAST,
            )
            p6_end = last_covered_day(seed_periods[6])
            # Entry-aware reservation ($200 held back), not the $500 estimate.
            assert series[date(2026, 4, 5)] == Decimal("800.00")
            assert series[p6_end] == Decimal("800.00")
            # And it equals the seam scalar at the period end (reconciliation).
            assert series[p6_end] == balance_at.cash_balance_at(
                seed_user["account"], bctx, p6_end,
            )

    def test_out_of_period_due_date_clamps_into_its_period(
        self, app, seed_user, seed_periods, db,
    ):
        """A due_date past its period end lands the flow on the period end.

        A period-6 expense dated 2026-04-20 (inside period 7's span) clamps to
        period 6's end 04-09, so period 6 still reconciles: its flow closes by
        04-09 rather than escaping into period 7.
        """
        with app.app_context():
            _add_txn(
                db, seed_user, seed_periods[6], "Stray", "100.00",
                due_date=date(2026, 4, 20),
            )
            db.session.commit()
            scenario = get_baseline_scenario(seed_user["user"].id)
            bctx = BalanceContext.build(seed_user["user"].id)
            series = balance_at.cash_daily_balance_series(
                seed_user["account"], bctx, _APR_FIRST, _APR_LAST,
            )
            # Period 6 end reflects the clamped -100 (1000 - 100 = 900).
            p6_end = last_covered_day(seed_periods[6])
            assert series[p6_end] == Decimal("900.00")
            # And it equals the seam scalar there (reconciliation holds).
            assert series[p6_end] == balance_at.cash_balance_at(
                seed_user["account"], bctx, p6_end,
            )
            # The flow landed on 04-09, not on its raw 04-20 due date.
            assert series[date(2026, 4, 9)] == Decimal("900.00")
            assert series[date(2026, 4, 10)] == Decimal("900.00")

    def test_inverted_range_returns_empty(
        self, app, seed_user, seed_periods, db,
    ):
        """last_day < first_day yields an empty map, not an error."""
        with app.app_context():
            scenario = get_baseline_scenario(seed_user["user"].id)
            bctx = BalanceContext.build(seed_user["user"].id)
            series = balance_at.cash_daily_balance_series(
                seed_user["account"], bctx, _APR_LAST, _APR_FIRST,
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
                    seed_user["account"], bctx, "2026-01-01", _APR_LAST,
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
                    _APR_FIRST, _APR_LAST,
                )
