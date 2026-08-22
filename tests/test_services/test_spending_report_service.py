"""
Shekel Budget App -- Unified Spending Report Service Tests (S-P1, D7 rebuild)

Hand-confirmed tests for :mod:`app.services.spending_report_service`: the
category breakdown and its shares, window filtering, the estimate-surprises
kernel (capped list + net), the hero band (vs-prior / vs-average with
None-safety and per-window-type prior arithmetic), the trailing window
series (the chart / hero one-source identity, and the twelve windows it
is derived from), the window-over-window deltas (items, groups, and the
By-change rows with their zero-current rider), and the empty / no-account
contracts.  Every value assertion carries the arithmetic that produces it.

**The chart's windows are DERIVED off the owner's pay calendar** since plan
step C2-f3d; ``TestTheChartReadsTheDerivedOrdinal`` is that step's firing
control, and it fails on the ``period_index`` queries it replaced.
"""

from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import event

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.services import (
    pay_period_write,
    spending_analysis,
    spending_report_service,
)
from app.services.pay_calendar import PayCalendar
from app.services.spending_report_service import (
    Comparison,
    SpendingWindow,
    compute_spending_report,
)
# The privates these tests exercise moved into the package's submodules at plan
# step X-au-c2b, when finding **N-270**'s 1000-line ceiling forced the split;
# they are imported from where they live rather than re-exported, so the test
# names the module that owns each rule.
from app.services.spending_report_service._hero import (
    _TRAILING_WINDOW_COUNT,
)
from app.services.spending_report_service._surprises import _MAX_SURPRISES
from app.services.spending_report_service._types import _ScopeIds
from app.services.spending_report_service._window import (
    _CHART_WINDOW_COUNT,
    _series_windows,
    _shift_month,
)
from tests._test_helpers import (
    default_settle_day,
    pay_periods_hydrated,
    settle_day_columns,
    settlement_columns,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _txn(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    db, seed_user, period, name, category_key, estimated,
    *, actual=None, status_enum=StatusEnum.DONE, is_income=False,
    is_deleted=False, due_date=None, settled_on=None,
):
    """Create one transaction for report testing (settled expense by default).

    A row in a SETTLED status carries the whole record -- the day, the figure
    and how that figure is known -- resolved through the one door a bare-built
    fixture uses (``_test_helpers.settlement_columns``, plan step X-au-c3).
    *actual* is a figure a HUMAN typed, which makes the record ``corrected``;
    with none the record is ``derived`` at the row's own plan, which is what a
    settle with nothing to correct books.  The settle DAY defaults to the
    period's start where the caller names none, because a settled row carries
    one and the pairing CHECK refuses a record without it.
    """
    cat_id = (
        seed_user["categories"][category_key].id if category_key else None
    )
    type_enum = TxnTypeEnum.INCOME if is_income else TxnTypeEnum.EXPENSE
    planned = Decimal(str(estimated))
    settled_day = settled_on or default_settle_day(
        period, ref_cache.status_id(status_enum),
    )
    txn = Transaction(
        account_id=seed_user["account"].id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        status_id=ref_cache.status_id(status_enum),
        name=name,
        category_id=cat_id,
        transaction_type_id=ref_cache.txn_type_id(type_enum),
        estimated_amount=planned,
        due_date=due_date or period.start_date,
        is_deleted=is_deleted,
        **settle_day_columns(settled_day),
        **settlement_columns(
            settled_day, planned,
            submitted=Decimal(str(actual)) if actual is not None else None,
        ),
    )
    db.session.add(txn)
    db.session.flush()
    return txn


def _pp_window(period):
    """Return a pay-period SpendingWindow for a period."""
    return SpendingWindow(window_type="pay_period", period_id=period.id)


@contextmanager
def _pay_period_selects(engine):
    """Capture every statement this block SELECTs FROM ``budget.pay_periods``.

    The engine rather than the session, because what is being counted is
    round trips to the database: a ``db.session.get`` served out of the
    identity map issues none, which is exactly how the retired ordinal
    walk's own row load stayed invisible while it ran eleven queries beside
    it (plan step C2-f3d).

    **It matches the FROM clause and so a JOIN is invisible to it**, which is
    deliberate and was measured at plan step C2-f3e: a query JOINing the table
    is captured as ZERO statements even while it hydrates ten ``PayPeriod``
    entities.  That is the right scope for the question here -- the assertions
    below count reads of the table as a SUBJECT, and
    ``query_settled_expenses_in_span`` KEEPS its join for the COALESCE
    attribution filter -- but it means this probe cannot grade a
    join-filtered read, and :func:`~tests._test_helpers.pay_periods_hydrated`
    beside it is what sees those.

    Args:
        engine: The SQLAlchemy engine to listen on, normally ``db.engine``.

    Yields:
        The list of flattened SQL statements, appended to as the block runs.
    """
    captured = []

    def _record(_conn, _cursor, statement, _params, _context, _executemany):
        flattened = " ".join(statement.split())
        if "FROM budget.pay_periods" in flattened:
            captured.append(flattened)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        yield captured
    finally:
        event.remove(engine, "before_cursor_execute", _record)


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

    def test_cross_boundary_due_date_belongs_to_its_month(self, app, seed_user,
                                                          seed_periods, db):
        """A bill due in month M funded from a period outside M belongs to M.

        period[1] spans 2026-01-16..29 (entirely January) and holds a bill
        with due_date 2026-02-05.  The period never overlaps February, so
        the former period-overlap pre-filter attributed the row to NO month
        window (January excluded it by date; February never loaded its
        period).  The attribution-day fetch puts it where it is due:
        February counts the 150.00; January does not.
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[1], "EarlyFebBill", "Rent",
                 "150.00", due_date=date(2026, 2, 5))
            db.session.commit()

            february = compute_spending_report(
                seed_user["user"].id,
                SpendingWindow(window_type="month", month=2, year=2026),
            )
            assert february.hero.spent_total == Decimal("150.00")
            assert february.breakdown[0].items[0].item_name == "Rent"

            january = compute_spending_report(
                seed_user["user"].id,
                SpendingWindow(window_type="month", month=1, year=2026),
            )
            assert january.hero.spent_total == Decimal("0")


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
            assert len(report.surprises.rows) == _MAX_SURPRISES
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
            due = seed_periods[0].start_date
            # The settle day IS the due date, so days_paid_before_due == 0 (on
            # time).  It was a noon-UTC instant until plan step X-f1, chosen so
            # the display-timezone conversion landed back on the same civil day;
            # the column stores that day directly now, so the arithmetic is
            # exact rather than zone-dependent.
            paid = due
            _txn(db, seed_user, seed_periods[0], "R", "Rent", "1200.00",
                 actual="1200.00", due_date=due, settled_on=paid)
            _txn(db, seed_user, seed_periods[0], "G", "Groceries", "400.00",
                 actual="400.00", due_date=due, settled_on=paid)
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
            )
            timing = report.hero.payment_timing
            assert timing["total_bills_paid"] == 2
            assert timing["paid_on_time"] == 2
            assert timing["paid_late"] == 0
            assert timing["avg_days_before_due"] == Decimal("0.00")


# ── The chart's twelve windows (plan step C2-f3d) ────────────────────


def _calendar_scope(paydays, cadence_days=14, user_id=1):
    """Return a ``_ScopeIds`` whose calendar is *paydays*, with no database.

    ``_series_windows`` reads the scope's CALENDAR and nothing else, so the
    three window arms are exercised over hand-written paydays rather than
    through a fixture -- which is what lets the cases below name an exact
    expected id list per slot.  The account and scenario ids are never read
    on this path.
    """
    return _ScopeIds(
        user_id=user_id, account_id=1, scenario_id=1,
        calendar=PayCalendar.from_paydays(paydays, cadence_days, user_id),
    )


class TestSeriesWindows:
    """The twelve chart windows, derived in one pass off the calendar.

    ``_shift_month`` is exercised here rather than in a class of its own: it
    is the month arm's one primitive and has no other caller since plan step
    C2-f3d deleted ``_shift_window``.
    """

    def test_shift_month_january_rolls_to_prior_december(self):
        """One month before January 2026 is December 2025."""
        assert _shift_month(2026, 1, 1) == (2025, 12)
        # Two months before March 2026 is January 2026.
        assert _shift_month(2026, 3, 2) == (2026, 1)
        # Thirteen months before June 2026 is May 2025.
        assert _shift_month(2026, 6, 13) == (2025, 5)

    def test_year_series_is_twelve_descending_years(self):
        """A year window's series is 2015..2026, the chosen year last."""
        scope = _calendar_scope([(1, date(2026, 1, 2))])
        chosen = SpendingWindow(window_type="year", year=2026)

        windows = _series_windows(scope, chosen)

        assert len(windows) == _CHART_WINDOW_COUNT
        assert [w.year for w in windows] == list(range(2015, 2027))
        assert {w.window_type for w in windows} == {"year"}
        assert windows[-1] == chosen

    def test_month_series_rolls_the_year(self):
        """March 2026 trails back to April 2025, twelve slots inclusive."""
        scope = _calendar_scope([(1, date(2026, 1, 2))])
        chosen = SpendingWindow(window_type="month", month=3, year=2026)

        windows = _series_windows(scope, chosen)

        assert [(w.year, w.month) for w in windows] == [
            (2025, 4), (2025, 5), (2025, 6), (2025, 7), (2025, 8),
            (2025, 9), (2025, 10), (2025, 11), (2025, 12),
            (2026, 1), (2026, 2), (2026, 3),
        ]
        assert windows[-1] == chosen

    def test_pay_period_series_is_the_twelve_preceding_paychecks(self):
        """With 20 paydays, viewing #14 gives ids 3..14 and no blank slot.

        Paydays are ids 1..20 on a 14-day cadence from 2026-01-02, so the
        calendar's derived ordinals are 0..19 in that same order.  Viewing
        the period with ordinal 13 (id 14) fills every slot: ordinals
        2..13, which are ids 3..14.
        """
        paydays = [
            (n + 1, date(2026, 1, 2) + timedelta(days=14 * n))
            for n in range(20)
        ]
        chosen = SpendingWindow(window_type="pay_period", period_id=14)

        windows = _series_windows(_calendar_scope(paydays), chosen)

        assert len(windows) == _CHART_WINDOW_COUNT
        assert [w.period_id for w in windows] == list(range(3, 15))
        assert windows[-1] == chosen

    def test_pay_period_series_pads_before_the_first_payday(self):
        """A schedule shorter than the chart leaves the LEADING slots blank.

        Four paydays, viewing the third (ordinal 2, id 3): the two earlier
        paychecks fill slots 9 and 10, the chosen one slot 11, and the nine
        slots below the owner's first payday are ``None`` rather than
        shortening the series -- which is what keeps ``series[-2]`` the
        prior window for every owner.
        """
        paydays = [
            (n + 1, date(2026, 1, 2) + timedelta(days=14 * n))
            for n in range(4)
        ]
        chosen = SpendingWindow(window_type="pay_period", period_id=3)

        windows = _series_windows(_calendar_scope(paydays), chosen)

        assert [w.period_id if w else None for w in windows] == (
            [None] * 9 + [1, 2, 3]
        )

    def test_pay_period_series_of_the_first_paycheck_has_no_prior(self):
        """The earliest paycheck fills one slot; the other eleven are blank."""
        paydays = [
            (n + 1, date(2026, 1, 2) + timedelta(days=14 * n))
            for n in range(4)
        ]
        chosen = SpendingWindow(window_type="pay_period", period_id=1)

        windows = _series_windows(_calendar_scope(paydays), chosen)

        assert windows[:-1] == [None] * (_CHART_WINDOW_COUNT - 1)
        assert windows[-1] == chosen

    def test_an_unmatched_period_id_leaves_every_earlier_slot_blank(self):
        """An id naming none of this calendar's periods resolves no predecessor.

        The ``None`` branch, stated at the unit level over a hand-written
        calendar; the case that MATTERS -- an id belonging to another owner
        rather than to nobody -- takes the same branch and is asserted
        end to end by
        ``TestTheChartReadsTheDerivedOrdinal.test_another_owners_period_id_buys_no_bar_of_this_owners_money``,
        which is where the money is.
        """
        paydays = [
            (n + 1, date(2026, 1, 2) + timedelta(days=14 * n))
            for n in range(4)
        ]
        chosen = SpendingWindow(window_type="pay_period", period_id=9999)

        windows = _series_windows(_calendar_scope(paydays), chosen)

        assert windows == [None] * (_CHART_WINDOW_COUNT - 1) + [chosen]

    def test_the_series_never_reaches_past_the_chosen_window(self):
        """No slot names a paycheck LATER than the chosen one.

        The chart is retrospective: ``viewed_index`` is the last bar, so a
        later period appearing anywhere in the list would draw future
        spending beside the window the user asked for.
        """
        paydays = [
            (n + 1, date(2026, 1, 2) + timedelta(days=14 * n))
            for n in range(20)
        ]
        scope = _calendar_scope(paydays)

        for period_id in range(1, 21):
            windows = _series_windows(
                scope,
                SpendingWindow(window_type="pay_period", period_id=period_id),
            )
            assert [w.period_id for w in windows if w is not None] == [
                earlier for earlier in range(1, period_id + 1)
            ][-_CHART_WINDOW_COUNT:]


class TestTheChartAndTheHeroCountTheSameWindows:
    """The two window counts are held in step by a test, not by a comment.

    Both modules state the relation and both say it is unenforced.  It was:
    mutation testing of plan step C2-f3d set ``_TRAILING_WINDOW_COUNT`` to 12
    and the whole 10,236-test suite stayed green, while the vs-average chip
    silently began averaging over a window the chart does not draw -- a wrong
    figure given quietly, which is this project's worst failure shape.  The
    precedent is ``test_pay_schedule.TestTheCadenceBoundHasOneValue``, which
    holds the two cadence bounds equal the same way.
    """

    def test_the_average_reads_fewer_windows_than_the_chart_draws(self):
        """vs-average must derive from bars the chart actually draws.

        ``_build_hero`` averages ``series[-(N + 1):-1]``, so the baseline
        stays inside the drawn series only while ``_TRAILING_WINDOW_COUNT``
        is strictly below ``_CHART_WINDOW_COUNT``.
        """
        assert 1 <= _TRAILING_WINDOW_COUNT < _CHART_WINDOW_COUNT

    def test_the_chart_has_room_for_a_chosen_window_and_a_prior(self):
        """``[-1]`` and ``[-2]`` are named positions, so the count is >= 2.

        ``_series_windows``, ``_build_series``, ``_build_hero`` and the
        chart's ``compare_index`` all index the last two slots directly.
        """
        assert _CHART_WINDOW_COUNT >= 2


class TestTheChartReadsTheDerivedOrdinal:
    """The chart's paychecks come from payday order, not a stored column."""

    def test_a_scrambled_stored_period_index_does_not_move_a_bar(
        self, app, seed_user, seed_periods, db,
    ):
        """Swapping two stored ordinals leaves every bar on its own paycheck.

        **The firing control for plan step C2-f3d.**  The retired walk
        selected each bar with ``WHERE period_index = <chosen> - <steps>``,
        so a stored ordinal out of payday order -- the state
        ``uq_pay_periods_user_index`` and three runtime fences exist to
        police, and which ``pay_period_write`` REPAIRS when it sees it --
        put the wrong paycheck in a bar silently.  Here periods 3 and 4
        spend ``300`` and ``400``; their stored ordinals are swapped, and
        the series still reports ``300`` before ``400`` because the
        calendar derives the ordinal from the paydays.  On the walk this
        assertion fails with the two totals transposed.
        """
        with app.app_context():
            for idx, amount in enumerate(
                ["100.00", "200.00", "300.00", "400.00", "500.00"], start=0
            ):
                _txn(db, seed_user, seed_periods[idx], f"P{idx}", "Rent",
                     amount)
            # Swap the two stored ordinals through a parking value, because
            # ``uq_pay_periods_user_index`` is checked per statement.
            third, fourth = seed_periods[2].id, seed_periods[3].id
            for pk, index in ((third, 999), (fourth, 2), (third, 3)):
                db.session.query(PayPeriod).filter_by(id=pk).update(
                    {"period_index": index},
                )
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[4]),
            )

            totals = [point.total for point in report.series[-5:]]
            assert totals == [
                Decimal("100.00"), Decimal("200.00"), Decimal("300.00"),
                Decimal("400.00"), Decimal("500.00"),
            ]
            assert [p.window.period_id for p in report.series[-5:]] == [
                p.id for p in seed_periods[:5]
            ]

    def test_a_gap_in_the_stored_ordinal_does_not_move_the_average(
        self, app, seed_user, seed_periods, db,
    ):
        """A HOLE in the stored ordinal moves a MONEY figure on the walk.

        The second of the three faults ``budget.pay_periods.period_index``
        can express (the arc's taxonomy is in
        :mod:`app.services.pay_calendar`), and it fails DIFFERENTLY from the
        transposition above: the walk matched nothing at the missing ordinal,
        so eleven steps reached only TEN paychecks and the series began one
        paycheck later than it should.

        **Twenty periods, not the fixture's ten, and that is the point.**  On
        a ten-period owner both sides end up holding the same six windows, so
        only the bar POSITIONS move and every figure survives -- a control
        that would pin the defect's shape and not its cost.  Here the twelfth
        slot is occupied, so the lost slot is a paycheck: the vs-average
        baseline reads ``$1,300.00`` on the walk against a true
        ``$1,250.00``.

        Spends run ``$100.00`` x (ordinal + 1), so the trailing six windows
        the hero averages (ordinals 9-14) are ``$1,000.00``..``$1,500.00`` =
        ``$7,500.00`` / 6 = ``$1,250.00``.  The walk drops ordinal 9 and
        averages the remaining five: ``$6,500.00`` / 5 = ``$1,300.00``.
        """
        with app.app_context():
            # ``record_paydays`` returns the rows it RECORDED, so the owner's
            # whole extended schedule is re-read rather than inferred from it.
            pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=seed_periods[0].start_date,
                num_periods=20,
                cadence_days=14,
            )
            db.session.flush()
            periods = (
                db.session.query(PayPeriod)
                .filter_by(user_id=seed_user["user"].id)
                .order_by(PayPeriod.start_date)
                .all()
            )
            assert len(periods) == 20
            for idx, period in enumerate(periods):
                _txn(db, seed_user, period, f"P{idx}", "Rent",
                     f"{(idx + 1) * 100}.00")
            # Open a hole at stored ordinal 10 by pushing 10..19 up one.
            # Highest first, so no statement collides with
            # uq_pay_periods_user_index.
            for period in reversed(periods[10:]):
                db.session.query(PayPeriod).filter_by(id=period.id).update(
                    {"period_index": PayPeriod.period_index + 1},
                )
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(periods[15]),
            )

            # Twelve occupied slots, ordinals 4..15, no blank anywhere.  The
            # ``if`` is what lets this REPORT the walk's blank mid-chart slot
            # rather than dying on it: a control that raises says less than
            # one that prints both lists.
            assert [
                point.window.period_id if point.window else None
                for point in report.series
            ] == [period.id for period in periods[4:16]]
            assert report.hero.spent_total == Decimal("1600.00")
            assert report.hero.vs_prior.baseline == Decimal("1500.00")
            assert report.hero.vs_average.baseline == Decimal("1250.00")
            assert report.hero.vs_average.delta == Decimal("350.00")

    def test_another_owners_period_id_buys_no_bar_of_this_owners_money(
        self, app, seed_user, second_user, seed_periods, db,
    ):
        """A FOREIGN period id resolves nothing, chips included.

        The walk read the chosen id with an unscoped ``db.session.get``, so
        another owner's period supplied an ordinal and THIS owner's paychecks
        were listed beneath it: five bars of real money under a window whose
        own total is ``None``, with both hero chips priced off them
        (measured on the merge base: vs-prior ``$500.00``, vs-average
        ``$300.00``, each at ``-100%``).  The calendar holds one owner's
        periods, so the id now matches none and every slot is blank.

        Unreachable from ``/analytics/spending``, which exposes only month
        and year -- this pins the door rather than closing a live leak, and
        it is the ownership case ``9999`` cannot make because an id belonging
        to NOBODY takes the same branch as one belonging to someone else.
        """
        with app.app_context():
            pay_period_write.record_paydays(
                user_id=second_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=10,
                cadence_days=14,
            )
            db.session.flush()
            foreign = (
                db.session.query(PayPeriod)
                .filter_by(user_id=second_user["user"].id)
                .order_by(PayPeriod.start_date)
                .all()
            )
            for idx in range(5):
                _txn(db, seed_user, seed_periods[idx], f"P{idx}", "Rent",
                     f"{(idx + 1) * 100}.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id,
                SpendingWindow(window_type="pay_period",
                               period_id=foreign[5].id),
            )

            assert [point.window for point in report.series[:-1]] == (
                [None] * (_CHART_WINDOW_COUNT - 1)
            )
            assert report.series[-1].window.period_id == foreign[5].id
            assert report.series[-1].total is None
            assert report.hero.spent_total == Decimal("0")
            assert report.hero.vs_prior.baseline is None
            assert report.hero.vs_average.baseline is None
            assert report.breakdown == [] and report.changes == []

    def test_the_report_reads_pay_periods_exactly_once(
        self, app, seed_user, seed_periods, db,
    ):
        """A report reads one ``budget.pay_periods`` row set and hydrates none.

        **Measured on the merge base at TWELVE statements for this fixture and
        TWENTY-THREE on a clone of production**, and the difference is the
        point: the retired walk's eleven ``db.session.get`` calls issued no
        statement whenever the chosen window's own rows had already hydrated
        that period, and eleven statements when it had no rows to do so (the
        identity map holds weak references).  Either way the eleven
        ``period_index`` queries beside them always ran.

        **The two assertions catch different things and neither is redundant**
        (mutation testing of this step measured the gap).  A statement count
        alone is order-dependent and JOIN-blind: nine per-bar
        ``db.session.get`` calls placed AFTER the row loads emit no statement,
        and an eager load renders as ``LEFT OUTER JOIN budget.pay_periods``
        inside the transactions query, which the capture predicate cannot see.
        The identity map sees both -- a report that resolves a paycheck any
        way at all leaves the ORM entity behind, and this path is meant to
        leave none, because the calendar is loaded as a column tuple.

        ONE statement is the count for an owner with a ``budget.pay_schedule``
        row, which every owner a live door creates has had since plan step
        X-ad-a.  A legacy owner without one (plan finding **P8**) reads TWO,
        because ``pay_schedule_service.resolve_cadence`` infers the cadence
        from a second ``budget.pay_periods`` query -- one that C4 deletes with
        the column it orders by.  ``seed_user`` has the row.
        """
        with app.app_context():
            for idx in range(10):
                _txn(db, seed_user, seed_periods[idx], f"P{idx}", "Rent",
                     "100.00")
            db.session.commit()
            with _pay_period_selects(db.engine) as selects, \
                    pay_periods_hydrated() as hydrated:
                compute_spending_report(
                    seed_user["user"].id, _pp_window(seed_periods[9]),
                )

            assert len(selects) == 1, "\n".join(selects)
            # The one surviving read may not be an ORDINAL search: C4 drops
            # that column, and ``_loader.calendar_for`` selects id + start_date
            # precisely so it already runs against the schema C4 leaves.
            assert "period_index" not in selects[0]
            assert hydrated == [], (
                f"the report hydrated {len(hydrated)} PayPeriod row(s); "
                f"it resolves every paycheck from the calendar it already "
                f"holds, which is loaded as a column tuple"
            )

    def test_no_settled_expense_query_loads_a_pay_period(
        self, app, seed_user, seed_periods, db,
    ):
        """Neither window query hydrates ``Transaction.pay_period``.

        The other half of the guard above, and it needs its own case because
        the statement counter cannot see this one: an eager load rides INSIDE
        the transactions query as a ``JOIN budget.pay_periods``, so re-adding
        one emits no statement of its own and moves no count.  What it does
        move is the identity map, which is what this asserts.

        Nothing on this read path asks a row which paycheck it sits in, so
        both queries were carrying a per-row ``PayPeriod`` for nobody.  The
        span query keeps its JOIN -- the COALESCE attribution filter runs on
        it -- and drops only the hydration.
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "A", "Rent", "100.00")
            _txn(db, seed_user, seed_periods[1], "B", "Rent", "200.00")
            db.session.commit()

            with pay_periods_hydrated() as hydrated:
                by_period = spending_analysis.query_settled_expenses(
                    seed_user["scenario"].id,
                    [seed_periods[0].id, seed_periods[1].id],
                    seed_user["account"].id,
                )
                by_span = spending_analysis.query_settled_expenses_in_span(
                    seed_user["scenario"].id, seed_user["account"].id,
                    seed_user["user"].id,
                    seed_periods[0].start_date, seed_periods[1].end_date,
                )

            # Both queries must actually return rows, or "nothing was
            # hydrated" is true of a query that loaded nothing at all.
            assert len(by_period) == 2 and len(by_span) == 2
            assert hydrated == [], (
                "a settled-expense query hydrated a PayPeriod, and no "
                "consumer of either query reads txn.pay_period"
            )


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
            assert len(series) == _CHART_WINDOW_COUNT
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

    def test_year_window_series_end_to_end(self, app, seed_user,
                                           seed_periods, db):
        """A YEAR window produces a full twelve-point series through the report.

        The arm had no end-to-end case: the only ``window_type="year"`` outside
        ``tests/manual/`` was the unit test on ``_series_windows``.  It is
        unrenderable by the S-P1 deferral (the route exposes month only) and
        computable, which is exactly the pair that goes untested by accident.

        ``seed_periods`` spans 2026-01-02..2026-05-21 with 1200 spent in
        January, so 2026 totals 1200.00, 2025 and earlier overlap no pay
        period and are ``None``.
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "JanRent", "Rent", "1200.00",
                 due_date=date(2026, 1, 10))
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id,
                SpendingWindow(window_type="year", year=2026),
            )

            assert [p.window.year for p in report.series] == list(
                range(2015, 2027)
            )
            assert report.series[-1].total == Decimal("1200.00")
            assert [p.total for p in report.series[:-1]] == [None] * 11
            assert report.hero.spent_total == Decimal("1200.00")
            assert report.hero.vs_prior.baseline is None
            assert report.hero.vs_average.baseline is None

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
                _CHART_WINDOW_COUNT
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
