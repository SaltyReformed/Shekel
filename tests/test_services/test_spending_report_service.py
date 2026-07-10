"""
Shekel Budget App -- Unified Spending Report Service Tests (S-P1, D7 rebuild)

Hand-confirmed tests for :mod:`app.services.spending_report_service`: the
category breakdown and its shares, window filtering, the estimate-surprises
kernel (capped list + net), the hero band (vs-prior / vs-average with
None-safety and per-window-type prior arithmetic), the trailing window
series (the chart / hero one-source identity), the window-over-window
deltas (items, groups, and the By-change rows with their zero-current
rider), and the empty / no-account contracts.  Every value assertion
carries the arithmetic that produces it.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.models.category import Category
from app.models.transaction import Transaction
from app.services import spending_report_service
from app.services.spending_report_service import (
    Comparison,
    SpendingWindow,
    compute_spending_report,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _txn(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    db, seed_user, period, name, category_key, estimated,
    *, actual=None, status_enum=StatusEnum.DONE, is_income=False,
    is_deleted=False, due_date=None, paid_at=None,
):
    """Create one transaction for report testing (settled expense by default)."""
    cat_id = (
        seed_user["categories"][category_key].id if category_key else None
    )
    type_enum = TxnTypeEnum.INCOME if is_income else TxnTypeEnum.EXPENSE
    txn = Transaction(
        account_id=seed_user["account"].id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        status_id=ref_cache.status_id(status_enum),
        name=name,
        category_id=cat_id,
        transaction_type_id=ref_cache.txn_type_id(type_enum),
        estimated_amount=Decimal(str(estimated)),
        actual_amount=Decimal(str(actual)) if actual is not None else None,
        due_date=due_date or period.start_date,
        is_deleted=is_deleted,
        paid_at=paid_at,
    )
    db.session.add(txn)
    db.session.flush()
    return txn


def _pp_window(period):
    """Return a pay-period SpendingWindow for a period."""
    return SpendingWindow(window_type="pay_period", period_id=period.id)


def _group(report, group_name):
    """Return the named breakdown group row, or None."""
    return next(
        (g for g in report.breakdown if g.group_name == group_name), None,
    )


# ── Breakdown ────────────────────────────────────────────────────────


class TestBreakdown:
    """Category breakdown: amounts, shares, ordering, window filtering."""

    def test_shares_sum_to_one(self, app, seed_user, seed_periods, db):
        """Group/item shares are amount/total and sum to 1.0000.

        Window = period[0] with Rent 1200 (Home), Groceries 500 (Family),
        Car Payment 300 (Auto): total 2000.  Shares 0.60 / 0.25 / 0.15.
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "Rent", "Rent", "1200.00")
            _txn(db, seed_user, seed_periods[0], "Food", "Groceries", "500.00")
            _txn(db, seed_user, seed_periods[0], "Car", "Car Payment", "300.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
            )
            assert report.hero.spent_total == Decimal("2000.00")
            # Ordered by amount descending.
            assert [g.group_name for g in report.breakdown] == [
                "Home", "Family", "Auto",
            ]
            home = _group(report, "Home")
            assert home.amount == Decimal("1200.00")
            assert home.share == Decimal("0.6")  # 1200/2000
            assert _group(report, "Family").share == Decimal("0.25")
            assert _group(report, "Auto").share == Decimal("0.15")
            # All group shares sum to exactly 1 (to the documented precision).
            share_sum = sum((g.share for g in report.breakdown), Decimal("0"))
            assert share_sum.quantize(Decimal("0.0001")) == Decimal("1.0000")
            # The single item under a group carries the same share.
            assert home.items[0].item_name == "Rent"
            assert home.items[0].share == Decimal("0.6")

    def test_multi_item_group_ordered(self, app, seed_user, seed_periods, db):
        """A group's items are amount-descending; the group sums them.

        Auto = Car Payment 300 + Gas 100 = 400; items ordered Car, Gas.
        """
        with app.app_context():
            gas = Category(
                user_id=seed_user["user"].id, group_name="Auto", item_name="Gas",
            )
            db.session.add(gas)
            db.session.flush()
            seed_user["categories"]["Gas"] = gas

            _txn(db, seed_user, seed_periods[0], "Car", "Car Payment", "300.00")
            _txn(db, seed_user, seed_periods[0], "Fuel", "Gas", "100.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
            )
            auto = _group(report, "Auto")
            assert auto.amount == Decimal("400.00")
            assert [i.item_name for i in auto.items] == ["Car Payment", "Gas"]
            assert auto.items[0].amount == Decimal("300.00")
            assert auto.items[1].amount == Decimal("100.00")

    def test_window_filters_non_measured_rows(self, app, seed_user, seed_periods, db):
        """Only settled expenses in the window count.

        Seeds one settled expense (included) plus a projected, a credit, a
        cancelled, a settled income, a deleted expense, and a settled expense
        in a DIFFERENT period -- all excluded.  Spent total is the one row.
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "Kept", "Rent", "500.00")
            _txn(db, seed_user, seed_periods[0], "Proj", "Rent", "999.00",
                 status_enum=StatusEnum.PROJECTED)
            _txn(db, seed_user, seed_periods[0], "Cred", "Rent", "999.00",
                 actual="999.00", status_enum=StatusEnum.CREDIT)
            _txn(db, seed_user, seed_periods[0], "Cxl", "Rent", "999.00",
                 status_enum=StatusEnum.CANCELLED)
            _txn(db, seed_user, seed_periods[0], "Inc", "Salary", "999.00",
                 actual="999.00", is_income=True, status_enum=StatusEnum.RECEIVED)
            _txn(db, seed_user, seed_periods[0], "Del", "Rent", "999.00",
                 actual="999.00", is_deleted=True)
            _txn(db, seed_user, seed_periods[1], "Other", "Rent", "999.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
            )
            assert report.hero.spent_total == Decimal("500.00")
            assert len(report.breakdown) == 1
            assert report.breakdown[0].items[0].item_name == "Rent"

    def test_month_window_attribution(self, app, seed_user, seed_periods, db):
        """A month window keeps rows whose COALESCE(due_date, start) is in-month.

        seed_periods span Jan-May 2026.  A month=1 window keeps only the
        January-attributed settled expenses.
        """
        with app.app_context():
            # period[0] starts 2026-01-02 (January); period[4] starts in Feb+.
            _txn(db, seed_user, seed_periods[0], "JanRent", "Rent", "1200.00",
                 due_date=date(2026, 1, 10))
            _txn(db, seed_user, seed_periods[4], "FebRent", "Rent", "800.00",
                 due_date=date(2026, 2, 28))
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id,
                SpendingWindow(window_type="month", month=1, year=2026),
            )
            assert report.hero.spent_total == Decimal("1200.00")
            assert report.scope.window_label == "January 2026"


# ── Surprises ────────────────────────────────────────────────────────


class TestSurprises:
    """Estimate surprises: differs vs equals vs no-actual, cap + net."""

    def test_surprises_signed_delta_and_net(self, app, seed_user, seed_periods, db):
        """Only rows whose actual differs surface; net sums their deltas.

        Rent est 1200 actual 1250 (+50); Car est 300 actual 280 (-20);
        Groceries est 400 actual 400 (0, not a surprise); Utility est 100
        actual None (resolves to estimate, delta 0, not a surprise).
        Sorted by |delta| desc: Rent(+50) then Car(-20).  Net = +30.
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "Rent", "Rent", "1200.00",
                 actual="1250.00")
            _txn(db, seed_user, seed_periods[0], "Car", "Car Payment", "300.00",
                 actual="280.00")
            _txn(db, seed_user, seed_periods[0], "Food", "Groceries", "400.00",
                 actual="400.00")
            _txn(db, seed_user, seed_periods[0], "Util", "Rent", "100.00",
                 actual=None)
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
            )
            rows = report.surprises.rows
            assert [r.name for r in rows] == ["Rent", "Car"]
            assert rows[0].delta == Decimal("50.00")
            assert rows[0].estimated == Decimal("1200.00")
            assert rows[0].actual == Decimal("1250.00")
            assert rows[1].delta == Decimal("-20.00")
            # Net = +50 + (-20) = +30.
            assert report.surprises.net == Decimal("30.00")

    def test_surprises_capped_but_net_is_full(self, app, seed_user, seed_periods, db):
        """The list caps at _MAX_SURPRISES; the net sums ALL surprises.

        Six surprises of +10..+60; the list keeps the 5 largest (+60..+20)
        but the net is +10+20+30+40+50+60 = +210.
        """
        with app.app_context():
            for i in range(1, 7):
                est = Decimal("100.00")
                _txn(db, seed_user, seed_periods[0], f"S{i}", "Rent",
                     est, actual=est + Decimal(str(i * 10)))
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
            )
            assert len(report.surprises.rows) == spending_report_service._MAX_SURPRISES
            assert report.surprises.rows[0].delta == Decimal("60.00")
            # Net includes the capped-off +10 surprise too.
            assert report.surprises.net == Decimal("210.00")


# ── Hero band ────────────────────────────────────────────────────────


class TestHero:
    """Hero comparisons: vs-prior, vs-average, None-safety."""

    def test_vs_prior_pct(self, app, seed_user, seed_periods, db):
        """vs-prior compares the immediately preceding same-type window.

        period[0] spends 1000, period[1] spends 1500.  Window = period[1]:
        delta 500, pct 500/1000*100 = 50.00.
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "A", "Rent", "1000.00")
            _txn(db, seed_user, seed_periods[1], "B", "Rent", "1500.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[1]),
            )
            assert report.hero.spent_total == Decimal("1500.00")
            assert report.hero.vs_prior.baseline == Decimal("1000.00")
            assert report.hero.vs_prior.delta == Decimal("500.00")
            assert report.hero.vs_prior.pct == Decimal("50.00")

    def test_vs_prior_none_when_no_prior_period(self, app, seed_user, seed_periods, db):
        """The earliest period has no prior window -> a None comparison."""
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "A", "Rent", "1000.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
            )
            assert report.hero.vs_prior.baseline is None
            assert report.hero.vs_prior.delta is None
            assert report.hero.vs_prior.pct is None

    def test_vs_prior_empty_prior_is_pct_none(self, app, seed_user, seed_periods, db):
        """An empty (zero-spend) prior window gives a real delta but None pct.

        period[0] has no spend; period[1] spends 800.  Prior exists (a lean
        period) so baseline 0, delta 800, but pct is None (no percent of 0).
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[1], "B", "Rent", "800.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[1]),
            )
            assert report.hero.vs_prior.baseline == Decimal("0")
            assert report.hero.vs_prior.delta == Decimal("800.00")
            assert report.hero.vs_prior.pct is None

    def test_vs_average_over_six_windows(self, app, seed_user, seed_periods, db):
        """vs-average uses the trailing six same-type windows.

        period[0..5] spend 100,200,300,400,500,600; window = period[6] spends
        1000.  Trailing average = 2100/6 = 350.00; delta 650; pct
        650/350*100 = 185.71.
        """
        with app.app_context():
            for idx, amt in enumerate([100, 200, 300, 400, 500, 600]):
                _txn(db, seed_user, seed_periods[idx], f"P{idx}", "Rent",
                     f"{amt}.00")
            _txn(db, seed_user, seed_periods[6], "W", "Rent", "1000.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[6]),
            )
            assert report.hero.vs_average.baseline == Decimal("350.00")
            assert report.hero.vs_average.delta == Decimal("650.00")
            assert report.hero.vs_average.pct == Decimal("185.71")

    def test_vs_average_none_when_no_prior(self, app, seed_user, seed_periods, db):
        """No trailing windows -> a None average comparison."""
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "A", "Rent", "500.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
            )
            assert report.hero.vs_average.baseline is None
            assert report.hero.vs_average.pct is None

    def test_payment_timing_scoped_to_window(self, app, seed_user, seed_periods, db):
        """Payment timing reuses the year-end rule over the window's rows.

        Two settled bills in the window, both paid on their due date (0 days):
        on-time 2, late 0, avg 0.00.
        """
        with app.app_context():
            from datetime import datetime, timezone
            due = seed_periods[0].start_date
            # Noon UTC on the due date -> same civil day in the display tz,
            # so days_paid_before_due == 0 (on time).
            paid = datetime(due.year, due.month, due.day, 12, tzinfo=timezone.utc)
            _txn(db, seed_user, seed_periods[0], "R", "Rent", "1200.00",
                 actual="1200.00", due_date=due, paid_at=paid)
            _txn(db, seed_user, seed_periods[0], "G", "Groceries", "400.00",
                 actual="400.00", due_date=due, paid_at=paid)
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
            )
            timing = report.hero.payment_timing
            assert timing["total_bills_paid"] == 2
            assert timing["paid_on_time"] == 2
            assert timing["paid_late"] == 0
            assert timing["avg_days_before_due"] == Decimal("0.00")


# ── Prior-window arithmetic ──────────────────────────────────────────


class TestPriorWindowArithmetic:
    """Month/year prior-window stepping (including the year rollover)."""

    def test_shift_month_january_rolls_to_prior_december(self):
        """One month before January 2026 is December 2025."""
        assert spending_report_service._shift_month(2026, 1, 1) == (2025, 12)
        # Two months before March 2026 is January 2026.
        assert spending_report_service._shift_month(2026, 3, 2) == (2026, 1)
        # Thirteen months before June 2026 is May 2025.
        assert spending_report_service._shift_month(2026, 6, 13) == (2025, 5)

    def test_shift_year(self, app, seed_user, seed_periods, db):
        """A year window steps back to the prior calendar year."""
        with app.app_context():
            prior = spending_report_service._shift_window(
                seed_user["user"].id,
                SpendingWindow(window_type="year", year=2026), 1,
            )
            assert prior.window_type == "year"
            assert prior.year == 2025


# ── Trailing series (the chart / hero one-source identity) ──────────


class TestSeries:
    """The trailing same-type window series the chart and hero share."""

    def test_series_shape_and_pay_period_totals(self, app, seed_user,
                                                seed_periods, db):
        """Twelve points, viewed last; each point totals its own window.

        period[0..2] spend 100/200/300; window = period[2].  The series is
        _CHART_WINDOW_COUNT (12) points ending at the viewed window: the
        last three total 300/200/100 walking back, and every earlier slot
        is an all-None point (no period exists before period[0]).
        """
        with app.app_context():
            for idx, amt in enumerate(["100.00", "200.00", "300.00"]):
                _txn(db, seed_user, seed_periods[idx], f"T{idx}", "Rent", amt)
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[2]),
            )
            series = report.series
            assert len(series) == spending_report_service._CHART_WINDOW_COUNT
            assert series[-1].window.period_id == seed_periods[2].id
            assert series[-1].total == Decimal("300.00")
            assert series[-2].total == Decimal("200.00")
            assert series[-3].total == Decimal("100.00")
            # Slots before the user's first period are all-None points.
            assert all(
                p.window is None and p.total is None for p in series[:-3]
            )

    def test_series_month_zero_vs_none(self, app, seed_user, seed_periods, db):
        """A tracked zero-spend month is 0; a pre-history month is None.

        seed_periods span 2026-01-02 .. 2026-05-21.  Viewing March 2026:
        the series is Apr 2025 .. Mar 2026.  Jan spends 1200, Feb nothing
        (tracked, so Decimal 0), Mar spends 300; Apr-Dec 2025 overlap no
        period, so their totals are None (excluded from averages; drawn as
        baseline ticks).
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "JanRent", "Rent", "1200.00",
                 due_date=date(2026, 1, 10))
            _txn(db, seed_user, seed_periods[5], "MarBill", "Rent", "300.00",
                 due_date=date(2026, 3, 15))
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id,
                SpendingWindow(window_type="month", month=3, year=2026),
            )
            series = report.series
            # Apr 2025 .. Dec 2025: before the user's first period.
            assert [p.total for p in series[:9]] == [None] * 9
            assert (series[9].window.year, series[9].window.month) == (2026, 1)
            assert series[9].total == Decimal("1200.00")
            assert series[10].total == Decimal("0")   # Feb: tracked, no spend
            assert series[11].total == Decimal("300.00")  # March (viewed)

    def test_hero_baselines_read_the_series(self, app, seed_user,
                                            seed_periods, db):
        """The hero's baselines derive from the series (one data source).

        Same data as above, viewing March: vs-prior baseline is the
        series' Feb point (0 -> delta 300.00, pct None); vs-average
        averages the existing points among Sep 2025..Feb 2026 -- Jan 1200
        and Feb 0 (the None 2025 months are skipped) -> (1200 + 0) / 2 =
        600.00; delta 300 - 600 = -300.00; pct -300/600*100 = -50.00.
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "JanRent", "Rent", "1200.00",
                 due_date=date(2026, 1, 10))
            _txn(db, seed_user, seed_periods[5], "MarBill", "Rent", "300.00",
                 due_date=date(2026, 3, 15))
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id,
                SpendingWindow(window_type="month", month=3, year=2026),
            )
            assert report.hero.vs_prior.baseline == Decimal("0")
            assert report.hero.vs_prior.delta == Decimal("300.00")
            assert report.hero.vs_prior.pct is None
            assert report.hero.vs_average.baseline == Decimal("600.00")
            assert report.hero.vs_average.delta == Decimal("-300.00")
            assert report.hero.vs_average.pct == Decimal("-50.00")


# ── Window-over-window deltas (breakdown + By-change rows) ──────────


class TestDeltas:
    """Item, group, and By-change deltas on the D7 change basis."""

    def test_item_and_group_deltas(self, app, seed_user, seed_periods, db):
        """Items and groups carry current minus prior; new categories flag.

        period[0] (prior): Rent 1000, Groceries 400.
        period[1] (viewed): Rent 900, Car Payment 250.
        Viewed breakdown: Home/Rent 900 (delta 900 - 1000 = -100, not new);
        Auto/Car Payment 250 (delta +250, is_new -- no prior spend).
        Groceries has no viewed row, so Family is absent from the breakdown.
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "Rent0", "Rent", "1000.00")
            _txn(db, seed_user, seed_periods[0], "Food0", "Groceries", "400.00")
            _txn(db, seed_user, seed_periods[1], "Rent1", "Rent", "900.00")
            _txn(db, seed_user, seed_periods[1], "Car1", "Car Payment", "250.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[1]),
            )
            assert [g.group_name for g in report.breakdown] == ["Home", "Auto"]
            home = _group(report, "Home")
            assert home.delta == Decimal("-100.00")   # 900 - 1000
            assert home.is_new is False
            assert home.items[0].delta == Decimal("-100.00")
            assert home.items[0].is_new is False
            auto = _group(report, "Auto")
            assert auto.delta == Decimal("250.00")    # 250 - 0
            assert auto.is_new is True
            assert auto.items[0].is_new is True

    def test_group_delta_includes_stopped_categories(self, app, seed_user,
                                                     seed_periods, db):
        """A group's prior side sums categories with no current spend.

        Home has two categories: Rent and Electricity (created here).
        period[0] (prior): Rent 1000, Electricity 200 -> Home prior 1200.
        period[1] (viewed): Electricity 150 only -> Home current 150.
        Group delta = 150 - 1200 = -1050 (the stopped Rent still counts);
        the Electricity ITEM delta is only 150 - 200 = -50.
        """
        with app.app_context():
            electricity = Category(
                user_id=seed_user["user"].id, group_name="Home",
                item_name="Electricity",
            )
            db.session.add(electricity)
            db.session.flush()
            seed_user["categories"]["Electricity"] = electricity

            _txn(db, seed_user, seed_periods[0], "Rent0", "Rent", "1000.00")
            _txn(db, seed_user, seed_periods[0], "Elec0", "Electricity",
                 "200.00")
            _txn(db, seed_user, seed_periods[1], "Elec1", "Electricity",
                 "150.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[1]),
            )
            home = _group(report, "Home")
            assert home.amount == Decimal("150.00")
            assert home.delta == Decimal("-1050.00")  # 150 - (1000 + 200)
            assert home.items[0].delta == Decimal("-50.00")  # 150 - 200

    def test_changes_include_zero_current_rows_sorted(self, app, seed_user,
                                                      seed_periods, db):
        """Changes span both windows; stopped categories appear at $0.

        period[0] (prior): Rent 1000, Groceries 400.
        period[1] (viewed): Groceries 460, Car Payment 250.
        Rows by |delta| descending: Rent (0 - 1000 = -1000, the
        zero-current rider), Car Payment (+250, is_new), Groceries
        (460 - 400 = +60).
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "Rent0", "Rent", "1000.00")
            _txn(db, seed_user, seed_periods[0], "Food0", "Groceries", "400.00")
            _txn(db, seed_user, seed_periods[1], "Food1", "Groceries", "460.00")
            _txn(db, seed_user, seed_periods[1], "Car1", "Car Payment", "250.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[1]),
            )
            rows = report.changes
            assert [r.item_name for r in rows] == [
                "Rent", "Car Payment", "Groceries",
            ]
            rent, car, groceries = rows
            assert (rent.current, rent.prior) == (Decimal("0"), Decimal("1000.00"))
            assert rent.delta == Decimal("-1000.00")
            assert rent.is_new is False              # nothing new about a stop
            assert rent.group_name == "Home"         # labels from the prior row
            assert car.delta == Decimal("250.00")
            assert car.is_new is True
            assert groceries.delta == Decimal("60.00")
            assert groceries.is_new is False

    def test_changes_tie_breaks_on_current_then_name(self, app, seed_user,
                                                     seed_periods, db):
        """Equal |delta| rows order by current spend, then item name.

        period[0]: Rent 100 (stops -> delta -100).  period[1]: Car Payment
        100 (new -> delta +100).  |delta| ties at 100; Car Payment has the
        higher current spend (100 > 0) so it sorts first.
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "Rent0", "Rent", "100.00")
            _txn(db, seed_user, seed_periods[1], "Car1", "Car Payment", "100.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[1]),
            )
            assert [r.item_name for r in report.changes] == [
                "Car Payment", "Rent",
            ]


# ── Scope facts, empty window, and the None contract ────────────────


class TestScopeAndContracts:
    """Scope labels, the empty-window shape, and the no-account None."""

    def test_scope_facts(self, app, seed_user, seed_periods, db):
        """The report carries the checking account label and settled basis."""
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "R", "Rent", "500.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
            )
            assert report.scope.account_id == seed_user["account"].id
            assert report.scope.account_name == "Checking"
            assert report.scope.settled_only is True
            assert report.scope.window_label  # non-empty pay-period label

    def test_empty_window_shape(self, app, seed_user, seed_periods, db):
        """A resolvable window with no settled spend is a zeroed report.

        Not None: an empty breakdown, empty change rows, a zero spent total,
        an empty surprises list with a zero net, no payment timing, and a
        full-length series whose viewed point is a real zero (the window
        exists; it just has no settled spend).
        """
        with app.app_context():
            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
            )
            assert report is not None
            assert report.breakdown == []
            assert report.changes == []
            assert report.hero.spent_total == Decimal("0")
            assert report.surprises.rows == []
            assert report.surprises.net == Decimal("0")
            assert report.hero.payment_timing is None
            assert len(report.series) == (
                spending_report_service._CHART_WINDOW_COUNT
            )
            assert report.series[-1].total == Decimal("0")

    def test_none_when_no_active_checking(self, app, seed_user, seed_periods):
        """No resolvable checking account -> None (empty state, not a crash)."""
        with app.app_context():
            with patch(
                "app.services.spending_report_service.resolve_analytics_account",
                return_value=None,
            ):
                report = compute_spending_report(
                    seed_user["user"].id, _pp_window(seed_periods[0]),
                )
            assert report is None

    def test_none_when_no_baseline_scenario(self, app, seed_user, seed_periods):
        """No baseline scenario -> None (empty state, not a crash)."""
        with app.app_context():
            with patch(
                "app.services.spending_report_service.get_baseline_scenario",
                return_value=None,
            ):
                report = compute_spending_report(
                    seed_user["user"].id, _pp_window(seed_periods[0]),
                )
            assert report is None

    def test_invalid_window_raises(self, app, seed_user):
        """An unknown window type raises ValueError (shared validator)."""
        import pytest
        with app.app_context():
            with pytest.raises(ValueError, match="Invalid window_type"):
                compute_spending_report(
                    seed_user["user"].id,
                    SpendingWindow(window_type="bogus"),
                )


# ── Comparison value object ──────────────────────────────────────────


class TestComparison:
    """The hero comparison value object's None-safety."""

    def test_of_none_baseline(self):
        """A None baseline yields an all-None comparison."""
        cmp = Comparison.of(Decimal("100.00"), None)
        assert cmp.baseline is None
        assert cmp.delta is None
        assert cmp.pct is None

    def test_of_zero_baseline(self):
        """A zero baseline yields a real delta but a None percent."""
        cmp = Comparison.of(Decimal("100.00"), Decimal("0"))
        assert cmp.baseline == Decimal("0")
        assert cmp.delta == Decimal("100.00")
        assert cmp.pct is None
