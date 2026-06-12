"""
Shekel Budget App -- Dashboard Pulse / Tracks Producer Tests (Loop B B-1)

Tests for ``dashboard_pulse_service`` -- the additive producers behind the
Terminal Road dashboard rebuild:

  * ``compute_pulse_section`` and its helpers: the as-of-today hero
    (balance / staleness / next-paycheck date), the 13-period projected
    end-balance chart + threshold, the full-horizon trough, the still-due
    totals on the locked B4 bases, and the current period's due-soon rows.
  * ``compute_tracks_section`` and ``_track_goal_datum``: the savings-goal
    metro tracks (trajectory passthrough) and the debt track (debt summary
    + honest principal-paid fraction).

The module-level autouse fixture (``test_services/conftest.py``) freezes
``date.today()`` to 2026-03-20, which falls in ``seed_periods[5]`` (the
period 2026-03-13 .. 2026-03-26).  So with ``seed_periods``:

  * the current period is index 5,
  * the forward horizon is periods 5..9 (5 periods),
  * the next paycheck date is ``seed_periods[6].start_date`` (2026-03-27).

Every dollar assertion shows its arithmetic; Decimals are constructed
from strings per the testing standards.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import GoalModeEnum, IncomeUnitEnum, StatusEnum, TxnTypeEnum
from app.models.account import AccountAnchorHistory
from app.models.ref import AccountType
from app.models.savings_goal import SavingsGoal
from app.models.transaction import Transaction
from app.services import account_service, dashboard_pulse_service, transfer_service
from app.services import pay_period_service
from tests._test_helpers import (
    add_entry,
    create_envelope_txn,
    create_loan_account,
    create_savings_account,
    make_salary_profile,
)


_CURRENT_IDX = 5  # seed_periods index that contains the frozen today.
_TODAY = date(2026, 3, 20)


# ── Helpers ──────────────────────────────────────────────────────────


def _add_expense(
    db_session, seed_user, period, name, amount,
    status_enum=StatusEnum.PROJECTED, due_date=None, is_deleted=False,
):
    """Create a non-tracked projected expense transaction for testing.

    Returns the created Transaction (flushed).
    """
    txn = Transaction(
        account_id=seed_user["account"].id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        status_id=ref_cache.status_id(status_enum),
        name=name,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        estimated_amount=Decimal(str(amount)),
        due_date=due_date,
        is_deleted=is_deleted,
    )
    db_session.add(txn)
    db_session.flush()
    return txn


def _add_tracked_expense(db_session, seed_user, period, name, estimated):
    """Create an entry-tracked (is_envelope) projected expense.

    Thin wrapper over the shared ``create_envelope_txn`` builder that
    accepts a string ``estimated`` for call-site brevity.
    """
    return create_envelope_txn(
        seed_user, db_session, period, name, Decimal(str(estimated)),
    )


def _add_entry(db_session, seed_user, txn, amount, entry_date):
    """Attach one debit entry of ``amount`` to ``txn`` (string-amount wrapper)."""
    add_entry(db_session, seed_user, txn, Decimal(str(amount)), entry_date)


def _add_anchor_history(db_session, account, period, balance, days_ago=0):
    """Append an anchor-history row ``days_ago`` before the frozen today."""
    created = datetime.now(timezone.utc) - timedelta(days=days_ago)
    entry = AccountAnchorHistory(
        account_id=account.id,
        pay_period_id=period.id,
        anchor_balance=Decimal(str(balance)),
        created_at=created,
    )
    db_session.add(entry)
    db_session.flush()
    return entry


# ── Hero: balance, captions, staleness, next paycheck ───────────────


class TestPulseHero:
    """The pulse hero block (as-of-today balance + captions + flags)."""

    def test_hero_carries_account_and_period(self, app, seed_user, seed_periods, db):
        """Hero carries the account name/id and the current period end date.

        The current period is seed_periods[5] (contains the frozen
        2026-03-20); its end_date is 2026-03-26.
        """
        with app.app_context():
            result = dashboard_pulse_service.compute_pulse_section(
                seed_user["user"].id,
            )
            hero = result["hero"]
            assert hero["account_id"] == seed_user["account"].id
            assert hero["account_name"] == seed_user["account"].name
            assert hero["period_end_date"] == seed_periods[_CURRENT_IDX].end_date
            assert hero["period_end_date"] == date(2026, 3, 26)

    def test_hero_balance_is_as_of_today(self, app, seed_user, seed_periods, db):
        """Hero balance equals balance_resolver.balance_as_of_date(today).

        With the seed account anchored at $1,000.00 and no transactions,
        the as-of-today projected balance is exactly the anchor: $1,000.00.
        """
        with app.app_context():
            result = dashboard_pulse_service.compute_pulse_section(
                seed_user["user"].id,
            )
            assert result["hero"]["balance"] == Decimal("1000.00")

    def test_hero_next_paycheck_date_normal(self, app, seed_user, seed_periods, db):
        """next_paycheck_date is the first period starting after today.

        Today is 2026-03-20 (in period 5).  The first period whose
        start_date > today is period 6 (starts 2026-03-27).
        """
        with app.app_context():
            result = dashboard_pulse_service.compute_pulse_section(
                seed_user["user"].id,
            )
            assert (
                result["hero"]["next_paycheck_date"]
                == seed_periods[_CURRENT_IDX + 1].start_date
            )
            assert result["hero"]["next_paycheck_date"] == date(2026, 3, 27)

    def test_next_paycheck_date_none_when_no_future_period(
        self, app, seed_user, db,
    ):
        """No period starts after today -> next_paycheck_date is None.

        Generate periods entirely in the past (starting 2024-02-02,
        forward of the 2024 bootstrap), so none begins after the frozen
        2026-03-20.  ``_next_paycheck_date`` returns None.
        """
        with app.app_context():
            pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date(2024, 2, 2),
                num_periods=5,
                cadence_days=14,
            )
            db.session.commit()

            result = dashboard_pulse_service._next_paycheck_date(
                seed_user["user"].id,
            )
            assert result is None

    def test_hero_staleness_fresh(self, app, seed_user, seed_periods, db):
        """A recently-updated anchor is NOT stale.

        Default staleness threshold is 14 days; an anchor updated 5 days
        ago is fresh (5 <= 14), so is_stale is False.
        """
        with app.app_context():
            _add_anchor_history(
                db.session, seed_user["account"], seed_periods[0],
                "1000.00", days_ago=5,
            )
            db.session.commit()

            result = dashboard_pulse_service.compute_pulse_section(
                seed_user["user"].id,
            )
            assert result["hero"]["is_stale"] is False
            assert result["hero"]["last_updated_date"] is not None

    def test_hero_staleness_stale(self, app, seed_user, seed_periods, db):
        """An anchor older than the threshold IS stale.

        Clear the factory origination row first so the 20-days-ago row is
        the latest.  20 > 14, so is_stale is True.
        """
        with app.app_context():
            account = seed_user["account"]
            db.session.query(AccountAnchorHistory).filter_by(
                account_id=account.id,
            ).delete()
            _add_anchor_history(
                db.session, account, seed_periods[0], "1000.00", days_ago=20,
            )
            db.session.commit()

            result = dashboard_pulse_service.compute_pulse_section(
                seed_user["user"].id,
            )
            assert result["hero"]["is_stale"] is True

    def test_hero_staleness_never_set(self, app, seed_user, seed_periods, db):
        """A never-set anchor IS stale, with a None last_updated_date.

        The Commit-3 invariant guarantees every account has an origination
        anchor row, so the truly-empty-history state hard-raises in the
        resolver and cannot reach the hero's balance call in production.
        The never-set staleness branch is therefore exercised at the
        helper level: with the origination row deleted,
        ``_last_anchor_update_date`` returns None, and ``_anchor_is_stale``
        treats None as stale (the user must set the balance).
        """
        with app.app_context():
            db.session.query(AccountAnchorHistory).filter_by(
                account_id=seed_user["account"].id,
            ).delete()
            db.session.commit()

            last_updated = dashboard_pulse_service._last_anchor_update_date(
                seed_user["account"].id,
            )
            assert last_updated is None
            assert dashboard_pulse_service._anchor_is_stale(
                last_updated, seed_user["settings"],
            ) is True


# ── Chart: 13-point shape, degradation, threshold ──────────────────


class TestPulseChart:
    """The projected end-balance chart series and threshold passthrough."""

    def _periods_and_balances(self, periods, per_period_balance="100.00"):
        """Build a forward-period list and a flat end-balance map for it."""
        end_balances = {p.id: Decimal(per_period_balance) for p in periods}
        return periods, end_balances

    def test_chart_caps_at_13_points(self, app, seed_user, db):
        """The chart slices to at most 13 points even with more periods.

        Generate 20 forward periods and assert the chart returns exactly
        13 points (the current period plus the next 12).
        """
        with app.app_context():
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date(2026, 1, 2),
                num_periods=20,
                cadence_days=14,
            )
            db.session.commit()
            forward, balances = self._periods_and_balances(periods)

            chart = dashboard_pulse_service._pulse_chart(forward, balances, None)
            assert len(chart["points"]) == 13
            # The points are the first 13 periods, in order.
            assert [pt["end_date"] for pt in chart["points"]] == [
                p.end_date for p in periods[:13]
            ]

    def test_chart_fewer_periods_degrades(self, app, seed_user, seed_periods, db):
        """With only 5 forward periods the chart returns 5 points, not 13.

        seed_periods[5..9] is 5 periods; the chart returns one point each
        (fewer-than-13 degradation), each carrying that period's end
        balance.
        """
        with app.app_context():
            forward = seed_periods[_CURRENT_IDX:]
            assert len(forward) == 5
            balances = {p.id: Decimal("250.00") for p in forward}

            chart = dashboard_pulse_service._pulse_chart(forward, balances, None)
            assert len(chart["points"]) == 5
            assert all(
                pt["balance"] == Decimal("250.00") for pt in chart["points"]
            )

    def test_chart_threshold_passthrough(self, app, seed_user, seed_periods, db):
        """The user's low_balance_threshold flows through as a Decimal.

        seed_user's settings default low_balance_threshold to 500 (a
        whole-dollar integer column); the chart surfaces it as
        Decimal("500").
        """
        with app.app_context():
            forward = seed_periods[_CURRENT_IDX:]
            balances = {p.id: Decimal("100.00") for p in forward}
            settings = seed_user["settings"]

            chart = dashboard_pulse_service._pulse_chart(
                forward, balances, settings,
            )
            assert chart["low_balance_threshold"] == Decimal("500")

    def test_chart_threshold_none_without_settings(
        self, app, seed_user, seed_periods, db,
    ):
        """With no settings the threshold is None (no dashed line drawn)."""
        with app.app_context():
            forward = seed_periods[_CURRENT_IDX:]
            balances = {p.id: Decimal("100.00") for p in forward}

            chart = dashboard_pulse_service._pulse_chart(forward, balances, None)
            assert chart["low_balance_threshold"] is None


# ── Trough: full-horizon minimum, beyond-chart dip, offset ──────────


class TestPulseTrough:
    """The lowest projected end balance over the full forward horizon."""

    def test_trough_all_positive(self, app, seed_user, seed_periods, db):
        """All-positive horizon -> the smallest positive end balance.

        Five forward periods (5..9) with balances 500, 400, 300, 350, 450.
        The minimum is 300 at period 7, offset 7 - 5 = 2.
        """
        with app.app_context():
            forward = seed_periods[_CURRENT_IDX:]
            values = ["500.00", "400.00", "300.00", "350.00", "450.00"]
            balances = {p.id: Decimal(v) for p, v in zip(forward, values)}

            trough = dashboard_pulse_service._pulse_trough(
                forward, balances, seed_periods[_CURRENT_IDX],
            )
            assert trough["balance"] == Decimal("300.00")
            assert trough["end_date"] == seed_periods[_CURRENT_IDX + 2].end_date
            # offset = period_index(7) - current period_index(5) = 2.
            assert trough["offset"] == 2

    def test_trough_negative_beyond_chart_window(self, app, seed_user, db):
        """A negative dip BEYOND the 13-chart window is still found.

        Build a 15-period forward horizon, all balances positive EXCEPT
        the 15th (index 14 in the forward list), which is negative.  The
        chart caps at 13 points (all positive), so a chart-only scan would
        miss the dip; the trough scans the FULL horizon and catches it.
        The trough's offset is the dip period's index minus the current
        (forward[0]) period's index.
        """
        with app.app_context():
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date(2026, 1, 2),
                num_periods=15,
                cadence_days=14,
            )
            db.session.commit()

            # Treat the whole generated run as the forward horizon and its
            # first period as the current (offset origin) -- this is a pure
            # _pulse_trough / _pulse_chart test, independent of today.
            forward = periods
            current = forward[0]
            assert len(forward) == 15  # 15 forward periods; chart caps at 13.

            balances = {p.id: Decimal("500.00") for p in forward}
            dip_period = forward[14]  # the 15th forward period (beyond chart)
            balances[dip_period.id] = Decimal("-250.00")

            # The chart sees only the first 13 -- all positive.
            chart = dashboard_pulse_service._pulse_chart(forward, balances, None)
            assert len(chart["points"]) == 13
            assert all(pt["balance"] > Decimal("0") for pt in chart["points"])

            # The trough scans all 15 and finds the dip beyond the chart.
            trough = dashboard_pulse_service._pulse_trough(
                forward, balances, current,
            )
            assert trough["balance"] == Decimal("-250.00")
            assert trough["end_date"] == dip_period.end_date
            # offset = dip period_index - current period_index = 14.
            assert trough["offset"] == dip_period.period_index - current.period_index
            assert trough["offset"] == 14

    def test_trough_none_when_no_balances(self, app, seed_user, seed_periods, db):
        """An empty end-balance map -> trough is None (no projection)."""
        with app.app_context():
            forward = seed_periods[_CURRENT_IDX:]
            trough = dashboard_pulse_service._pulse_trough(
                forward, {}, seed_periods[_CURRENT_IDX],
            )
            assert trough is None


# ── Still due: locked B4 bases, current vs next separation ──────────


class TestPulseStillDue:
    """Still-due totals on the locked B4 bases (data-value pass)."""

    def test_untracked_row_contributes_effective_amount(
        self, app, seed_user, seed_periods, db,
    ):
        """An untracked projected expense contributes its effective amount.

        One $300.00 projected expense in the current period; still-due for
        the current period is $300.00, next period $0.00.
        """
        with app.app_context():
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Rent", "300.00", due_date=date(2026, 3, 22),
            )
            db.session.commit()

            result = dashboard_pulse_service.compute_pulse_section(
                seed_user["user"].id,
            )
            still = result["still_due"]
            assert still["current_period"] == Decimal("300.00")
            assert still["next_period"] == Decimal("0.00")

    def test_tracked_partial_entries_contributes_remaining(
        self, app, seed_user, seed_periods, db,
    ):
        """A tracked row with partial entries contributes remaining-after-entries.

        Envelope estimated $200.00, entries summing $80.00:
            remaining = 200.00 - 80.00 = 120.00 (floored at 0 -> 120.00).
        Still-due for the current period is $120.00.
        """
        with app.app_context():
            txn = _add_tracked_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Groceries", "200.00",
            )
            _add_entry(db.session, seed_user, txn, "50.00", date(2026, 3, 18))
            _add_entry(db.session, seed_user, txn, "30.00", date(2026, 3, 19))
            db.session.commit()

            result = dashboard_pulse_service.compute_pulse_section(
                seed_user["user"].id,
            )
            assert result["still_due"]["current_period"] == Decimal("120.00")

    def test_tracked_over_budget_floors_at_zero(
        self, app, seed_user, seed_periods, db,
    ):
        """An over-budget envelope contributes 0, never a negative.

        Envelope estimated $100.00, entries summing $130.00:
            remaining = 100.00 - 130.00 = -30.00 -> floored to 0.00.
        Still-due for the current period is $0.00 (not -$30.00).
        """
        with app.app_context():
            txn = _add_tracked_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Groceries", "100.00",
            )
            _add_entry(db.session, seed_user, txn, "70.00", date(2026, 3, 18))
            _add_entry(db.session, seed_user, txn, "60.00", date(2026, 3, 19))
            db.session.commit()

            result = dashboard_pulse_service.compute_pulse_section(
                seed_user["user"].id,
            )
            assert result["still_due"]["current_period"] == Decimal("0.00")

    def test_tracked_no_entries_contributes_full_estimate(
        self, app, seed_user, seed_periods, db,
    ):
        """A tracked row with no entries contributes its full estimate.

        Envelope estimated $150.00, no entries:
            remaining = 150.00 - 0 = 150.00.
        Still-due for the current period is $150.00.
        """
        with app.app_context():
            _add_tracked_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Groceries", "150.00",
            )
            db.session.commit()

            result = dashboard_pulse_service.compute_pulse_section(
                seed_user["user"].id,
            )
            assert result["still_due"]["current_period"] == Decimal("150.00")

    def test_transfer_out_shadow_included(
        self, app, seed_user, seed_periods, db,
    ):
        """A projected transfer-out shadow IS counted in still-due (B4b).

        Build a REAL projected transfer (so the expense/income shadow pair
        is genuine) from checking to savings for $400.00 in the current
        period.  The expense shadow lands on checking as a projected
        expense and contributes its $400.00 to still-due.
        """
        with app.app_context():
            savings_type = (
                db.session.query(AccountType).filter_by(name="Savings").one()
            )
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Sweep Target",
                    anchor_balance=Decimal("0.00"),
                    anchor_period_id=seed_periods[0].id,
                ),
            )
            db.session.add(savings)
            db.session.flush()

            transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=savings.id,
                    pay_period_id=seed_periods[_CURRENT_IDX].id,
                    scenario_id=seed_user["scenario"].id,
                    amount=Decimal("400.00"),
                    status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                    category_id=None,
                    due_date=date(2026, 3, 24),
                ),
            )
            db.session.commit()

            result = dashboard_pulse_service.compute_pulse_section(
                seed_user["user"].id,
            )
            # The transfer-out shadow is the only obligation: $400.00.
            assert result["still_due"]["current_period"] == Decimal("400.00")

    def test_current_and_next_period_separated(
        self, app, seed_user, seed_periods, db,
    ):
        """Still-due totals split correctly between current and next period.

        $300.00 in the current period (5) and $175.00 in the next period
        (6).  current_period total = 300.00, next_period total = 175.00.
        """
        with app.app_context():
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "This period bill", "300.00", due_date=date(2026, 3, 22),
            )
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX + 1],
                "Next period bill", "175.00", due_date=date(2026, 3, 30),
            )
            db.session.commit()

            result = dashboard_pulse_service.compute_pulse_section(
                seed_user["user"].id,
            )
            still = result["still_due"]
            assert still["current_period"] == Decimal("300.00")
            assert still["next_period"] == Decimal("175.00")

    def test_settled_row_excluded_from_still_due(
        self, app, seed_user, seed_periods, db,
    ):
        """A settled (done) expense is not still due -- it is already paid.

        One $500.00 DONE expense in the current period contributes nothing
        (the still-due query is Projected-only).
        """
        with app.app_context():
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Already paid", "500.00",
                status_enum=StatusEnum.DONE, due_date=date(2026, 3, 15),
            )
            db.session.commit()

            result = dashboard_pulse_service.compute_pulse_section(
                seed_user["user"].id,
            )
            assert result["still_due"]["current_period"] == Decimal("0.00")


# ── Due soon: current-period rows with day offset + undated flag ────


class TestPulseDueSoon:
    """The current period's due-soon rows for the street / mobile list."""

    def test_due_soon_only_current_period(
        self, app, seed_user, seed_periods, db,
    ):
        """Only the current period's unpaid rows appear (next period excluded).

        A bill in period 5 (current) and a bill in period 6 (next): only
        the current-period bill is in due_soon.
        """
        with app.app_context():
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Current bill", "100.00", due_date=date(2026, 3, 22),
            )
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX + 1],
                "Next bill", "200.00", due_date=date(2026, 3, 30),
            )
            db.session.commit()

            result = dashboard_pulse_service.compute_pulse_section(
                seed_user["user"].id,
            )
            names = [b["name"] for b in result["due_soon"]]
            assert names == ["Current bill"]

    def test_due_soon_day_offset_and_undated(
        self, app, seed_user, seed_periods, db,
    ):
        """Dated rows carry day_offset from period start; undated flagged.

        Current period 5 starts 2026-03-13.  A bill due 2026-03-22 has
        day_offset = (03-22 - 03-13).days = 9.  An undated bill carries
        day_offset None and undated True, and sorts AFTER dated rows.
        """
        with app.app_context():
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Dated bill", "100.00", due_date=date(2026, 3, 22),
            )
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Undated bill", "50.00", due_date=None,
            )
            db.session.commit()

            result = dashboard_pulse_service.compute_pulse_section(
                seed_user["user"].id,
            )
            due_soon = result["due_soon"]
            # Dated first (chronological), undated last.
            assert [b["name"] for b in due_soon] == ["Dated bill", "Undated bill"]

            dated = due_soon[0]
            assert dated["undated"] is False
            # (2026-03-22 - 2026-03-13).days = 9.
            assert dated["day_offset"] == 9

            undated = due_soon[1]
            assert undated["undated"] is True
            assert undated["day_offset"] is None

    def test_due_soon_sorted_by_due_date(
        self, app, seed_user, seed_periods, db,
    ):
        """Dated due-soon rows sort by due_date ascending."""
        with app.app_context():
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Late", "100.00", due_date=date(2026, 3, 25),
            )
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Early", "100.00", due_date=date(2026, 3, 15),
            )
            db.session.commit()

            result = dashboard_pulse_service.compute_pulse_section(
                seed_user["user"].id,
            )
            assert [b["name"] for b in result["due_soon"]] == ["Early", "Late"]


# ── The hero == first chart point identity ──────────────────────────


class TestHeroChartIdentity:
    """The hero balance coincides with the chart's first point.

    With NO entries dated after today, the as-of-today balance (hero)
    equals the current period's projected end balance (chart[0]) by
    reservation semantics -- the data-value pass's locked identity that
    Loop A's mockup data violated.
    """

    def test_hero_equals_first_chart_point_no_post_dated_entries(
        self, app, seed_user, seed_periods, db,
    ):
        """Hero balance == chart points[0].balance with no post-dated entries.

        Seed account anchored $1,000.00.  Add a $300.00 projected expense
        and a $1,200.00 projected income in the current period, plus a
        tracked envelope with an entry dated BEFORE today.  No entry is
        dated after today, so the as-of-today reservation reduction sees
        the whole current period and the two figures coincide.
        """
        with app.app_context():
            current = seed_periods[_CURRENT_IDX]
            _add_expense(
                db.session, seed_user, current, "Rent", "300.00",
                due_date=date(2026, 3, 18),
            )
            income = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="Paycheck",
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
                estimated_amount=Decimal("1200.00"),
            )
            db.session.add(income)
            tracked = _add_tracked_expense(
                db.session, seed_user, current, "Groceries", "150.00",
            )
            _add_entry(
                db.session, seed_user, tracked, "40.00", date(2026, 3, 17),
            )
            db.session.commit()

            result = dashboard_pulse_service.compute_pulse_section(
                seed_user["user"].id,
            )
            hero_balance = result["hero"]["balance"]
            first_point = result["chart"]["points"][0]["balance"]
            assert hero_balance == first_point
            # And the first chart point is the current period's end date.
            assert (
                result["chart"]["points"][0]["end_date"]
                == current.end_date
            )


# ── compute_pulse_section degraded states ───────────────────────────


class TestPulseSectionDegraded:
    """The pulse producer's None contract for the degraded states."""

    def test_no_current_period_returns_none(self, app, seed_user):
        """No period contains today -> None.

        seed_user (no seed_periods) has only the 2024 bootstrap period, so
        get_current_period returns None and the producer short-circuits.
        """
        with app.app_context():
            result = dashboard_pulse_service.compute_pulse_section(
                seed_user["user"].id,
            )
            assert result is None

    def test_has_all_five_keys_when_populated(
        self, app, seed_user, seed_periods, db,
    ):
        """A populated pulse section carries exactly the five region keys."""
        with app.app_context():
            result = dashboard_pulse_service.compute_pulse_section(
                seed_user["user"].id,
            )
            assert set(result.keys()) == {
                "hero", "chart", "trough", "still_due", "due_soon",
            }


# ── Tracks: savings goal trajectory passthrough + debt fraction ─────


class TestTracksGoals:
    """Savings-goal metro tracks reshaped from compute_goal_progress."""

    def test_goal_track_passes_through_trajectory(
        self, app, seed_user, seed_periods, db,
    ):
        """A goal track carries name, percent, balance, target, and trajectory.

        A $2,500.00 savings account against a $10,000.00 fixed target:
            progress = 2500 / 10000 * 100 = 25.00%.
        The track exposes the trajectory keys (pace, projected completion
        date, required monthly) verbatim from calculate_trajectory.
        """
        with app.app_context():
            acct = create_savings_account(
                seed_user, db.session, "Goal Account",
                Decimal("2500.00"), anchor_period_id=seed_periods[0].id,
            )
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=acct.id,
                name="Emergency Fund",
                target_amount=Decimal("10000.00"),
                target_date=date(2027, 3, 1),
            )
            db.session.add(goal)
            db.session.commit()

            tracks = dashboard_pulse_service.compute_tracks_section(
                seed_user["user"].id,
            )
            goal_tracks = [g for g in tracks["goals"] if g["account_id"] == acct.id]
            assert len(goal_tracks) == 1
            track = goal_tracks[0]
            assert track["name"] == "Emergency Fund"
            assert track["account_name"] == "Goal Account"
            assert track["current_balance"] == Decimal("2500.00")
            assert track["target_amount"] == Decimal("10000.00")
            # 2500 / 10000 * 100 = 25.00.
            assert track["progress_pct"] == Decimal("25.00")
            assert track["target_date"] == date(2027, 3, 1)
            # Trajectory keys present (the metro-track contract).
            assert "pace" in track
            assert "projected_completion_date" in track
            assert "required_monthly" in track
            assert "monthly_contribution" in track

    def test_goal_track_matches_savings_trajectory(
        self, app, seed_user, seed_periods, db,
    ):
        """The track's trajectory fields equal the /savings producer's verbatim.

        Proves the reshape passes calculate_trajectory's outputs through
        unchanged rather than recomputing them.
        """
        # pylint: disable=import-outside-toplevel
        from app.services import savings_dashboard_service

        with app.app_context():
            acct = create_savings_account(
                seed_user, db.session, "Trajectory Account",
                Decimal("3000.00"), anchor_period_id=seed_periods[0].id,
            )
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=acct.id,
                name="House",
                target_amount=Decimal("9000.00"),
                target_date=date(2027, 6, 1),
            )
            db.session.add(goal)
            db.session.commit()

            tracks = dashboard_pulse_service.compute_tracks_section(
                seed_user["user"].id,
            )
            track = next(
                g for g in tracks["goals"] if g["account_id"] == acct.id
            )
            sav = next(
                gd for gd in savings_dashboard_service.compute_goal_progress(
                    seed_user["user"].id,
                )
                if gd["goal"].account_id == acct.id
            )
            assert track["pace"] == sav["trajectory"]["pace"]
            assert (
                track["projected_completion_date"]
                == sav["trajectory"]["projected_completion_date"]
            )
            assert track["required_monthly"] == sav["trajectory"]["required_monthly"]
            assert track["progress_pct"] == sav["progress_pct"]

    def test_no_goals_empty_list(self, app, seed_user, seed_periods, db):
        """No active goals -> goals is an empty list."""
        with app.app_context():
            tracks = dashboard_pulse_service.compute_tracks_section(
                seed_user["user"].id,
            )
            assert tracks["goals"] == []


class TestTracksDebt:
    """The debt track: debt summary + honest principal-paid fraction."""

    def test_no_debt_returns_none(self, app, seed_user, seed_periods, db):
        """No loan accounts -> debt is None (no track rendered)."""
        with app.app_context():
            tracks = dashboard_pulse_service.compute_tracks_section(
                seed_user["user"].id,
            )
            assert tracks["debt"] is None

    def test_debt_track_carries_summary_and_fraction(
        self, app, seed_user, seed_periods, db,
    ):
        """A loan -> debt summary plus a principal_paid_fraction Decimal.

        A $1,000.00 auto loan originated 2026-01-01 at 5% for 24 months.
        By the frozen today (2026-03-20) confirmed payments have reduced
        the balance, so:
            principal_paid_fraction =
                (original - current) / original
              = (1000.00 - total_debt) / 1000.00
        which is the SAME current balance the debt summary's total_debt
        reports (same loan set), so the fraction reconciles with the
        summary to the cent.  The data is present (original_principal is
        NOT NULL), so the fraction is a real Decimal, not None.
        """
        with app.app_context():
            create_loan_account(seed_user, db.session, name="Pulse Loan")

            tracks = dashboard_pulse_service.compute_tracks_section(
                seed_user["user"].id,
            )
            debt = tracks["debt"]
            assert debt is not None
            # The summary fields survive (delegated to compute_debt_summary).
            assert "total_debt" in debt
            assert "total_monthly_payments" in debt
            assert "projected_debt_free_date" in debt
            # The honest fraction is present and is a Decimal in [0, 1].
            fraction = debt["principal_paid_fraction"]
            assert isinstance(fraction, Decimal)
            assert Decimal("0") <= fraction <= Decimal("1")
            # Reconcile with the summary: fraction uses the SAME current
            # balance the summary's total_debt sums (one loan, original
            # $1,000.00), so:
            #   fraction == (1000.00 - total_debt) / 1000.00.
            expected = (
                (Decimal("1000.00") - debt["total_debt"]) / Decimal("1000.00")
            )
            assert fraction == expected

    def test_debt_fraction_zero_before_any_payment(
        self, app, seed_user, seed_periods, db,
    ):
        """A loan whose balance still equals its original -> fraction 0.

        Originate the loan TODAY (frozen 2026-03-20) so no scheduled
        payment has been confirmed yet; the resolver's current balance
        equals the original principal, giving a paid fraction of exactly
        0: (1000.00 - 1000.00) / 1000.00 = 0.
        """
        with app.app_context():
            # Originate TODAY (frozen 2026-03-20) so no scheduled payment
            # is yet confirmed: the resolver's current balance equals the
            # original principal.
            create_loan_account(
                seed_user, db.session, name="Fresh Loan",
                origination_date=_TODAY, payment_day=_TODAY.day,
            )

            tracks = dashboard_pulse_service.compute_tracks_section(
                seed_user["user"].id,
            )
            debt = tracks["debt"]
            assert debt["total_debt"] == Decimal("1000.00")
            # No principal paid yet -> fraction is exactly 0.
            assert debt["principal_paid_fraction"] == Decimal("0")


# ── Tracks: income-relative goal pace passthrough ───────────────────


class TestTracksIncomeRelativeGoal:
    """An income-relative goal resolves a real target on the track."""

    def test_income_relative_goal_track_has_positive_target(
        self, app, seed_user, seed_periods, db,
    ):
        """An income-relative goal's resolved target is positive on the track.

        With a salary profile, the NULL stored target resolves to a real
        dollar figure (multiplier * net biweekly pay) -- the track must
        carry the resolved (positive) target, not 0, and a pace from the
        trajectory.
        """
        with app.app_context():
            make_salary_profile(seed_user, db.session, name="Track Salary")
            acct = create_savings_account(
                seed_user, db.session, "IR Goal Account",
                Decimal("4000.00"), anchor_period_id=seed_periods[0].id,
            )

            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=acct.id,
                name="3 Paychecks",
                goal_mode_id=ir_id,
                income_unit_id=paychecks_id,
                income_multiplier=Decimal("3.00"),
                target_amount=None,
            )
            db.session.add(goal)
            db.session.commit()

            tracks = dashboard_pulse_service.compute_tracks_section(
                seed_user["user"].id,
            )
            track = next(
                g for g in tracks["goals"] if g["account_id"] == acct.id
            )
            assert track["target_amount"] > Decimal("0.00")
            # The pace key is present (None when no contribution / target
            # date, a string otherwise) -- the track exposes it either way.
            assert "pace" in track
