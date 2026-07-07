"""
Shekel Budget App -- Analytics Route Tests

Tests for the analytics page shell and HTMX tab endpoints:
  - Authentication required for all endpoints
  - Main page renders with nav-pills and tab-content div
  - Tab endpoints return placeholders/content with HX-Request header
  - Tab endpoints redirect without HX-Request header
  - Nav bar shows Analytics link with correct active state
  - Statements pill groups the income statement + balance sheet toggle
  - Retired Variance / Trends / Year-End URLs redirect to the page
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.models.transaction import Transaction
from app.services import account_service

from tests._test_helpers import create_settled_cash_transaction, freeze_today


@pytest.fixture(autouse=True)
def _freeze_today_inside_seed_range(monkeypatch):
    """Freeze today to date(2026, 3, 20) so seed_periods tests pass past 2026-05-22.

    This module relies on calendar-anchored seed_periods (Jan-May 2026)
    and asserts on tax_year=2026, calendar 2026 due_dates, and other
    calendar-anchored values.  Auto-discovery patches every loaded
    module's ``date``/``datetime`` symbols so production services
    (e.g. spending_trend_service) consuming ``date.today()`` agree
    with the test's view of "today" regardless of wall-clock date.
    """
    freeze_today(monkeypatch, date(2026, 3, 20))


def _create_paid_expense_for_route_test(db, seed_user, seed_periods,
                                        name, amount, category_key):
    """Create a settled expense for year-end spending tests.

    Args:
        db: Database session fixture.
        seed_user: User fixture dict.
        seed_periods: Pay periods list.
        name: Transaction name.
        amount: Decimal amount.
        category_key: Key into seed_user['categories'] dict.
    """
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    paid_status_id = ref_cache.status_id(StatusEnum.DONE)
    cat = seed_user["categories"].get(category_key)
    txn = Transaction(
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        pay_period_id=seed_periods[0].id,
        status_id=paid_status_id,
        transaction_type_id=expense_type_id,
        name=name,
        estimated_amount=amount,
        actual_amount=amount,
        category_id=cat.id if cat else None,
    )
    db.session.add(txn)
    db.session.commit()


def _seed_long_periods(db, seed_user, count):
    """Generate pay periods starting ~8 months ago for trend tests.

    The spending trend service uses a window relative to today, so
    periods must be recent enough to fall within that window.

    Args:
        db: Database session.
        seed_user: User fixture dict.
        count: Number of biweekly periods to generate.

    Returns:
        List of PayPeriod objects.
    """
    from app.services import pay_period_service
    # Start 8 months before today to ensure 6-month window coverage.
    today = date.today()
    start_month = today.month - 8
    start_year = today.year
    while start_month < 1:
        start_month += 12
        start_year -= 1
    start = date(start_year, start_month, 3)

    periods = pay_period_service.generate_pay_periods(
        user_id=seed_user["user"].id,
        start_date=start,
        num_periods=count,
        cadence_days=14,
    )
    db.session.flush()
    seed_user["account"].current_anchor_period_id = periods[0].id
    db.session.commit()
    return periods


def _seed_increasing_trend(db, seed_user, periods):
    """Create expenses in every period with increasing amounts.

    The trend service runs linear regression on per-period data,
    so we need one expense per period with a clear upward slope.
    """
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    paid_status_id = ref_cache.status_id(StatusEnum.DONE)
    cat = seed_user["categories"]["Rent"]
    amount = Decimal("100.00")
    for p in periods:
        txn = Transaction(
            account_id=seed_user["account"].id,
            scenario_id=seed_user["scenario"].id,
            pay_period_id=p.id,
            status_id=paid_status_id,
            transaction_type_id=expense_type_id,
            name=f"Rent {p.start_date.strftime('%b %d')}",
            estimated_amount=amount,
            actual_amount=amount,
            category_id=cat.id,
            due_date=p.start_date,
        )
        db.session.add(txn)
        amount += Decimal("20.00")
    db.session.commit()


def _seed_decreasing_trend(db, seed_user, periods):
    """Create expenses in every period with decreasing amounts.

    Clear downward slope for the regression.
    """
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    paid_status_id = ref_cache.status_id(StatusEnum.DONE)
    cat = seed_user["categories"]["Rent"]
    amount = Decimal("600.00")
    for p in periods:
        txn = Transaction(
            account_id=seed_user["account"].id,
            scenario_id=seed_user["scenario"].id,
            pay_period_id=p.id,
            status_id=paid_status_id,
            transaction_type_id=expense_type_id,
            name=f"Rent {p.start_date.strftime('%b %d')}",
            estimated_amount=amount,
            actual_amount=amount,
            category_id=cat.id,
            due_date=p.start_date,
        )
        db.session.add(txn)
        amount = max(Decimal("50.00"), amount - Decimal("20.00"))
    db.session.commit()


def _seed_flat_expenses(db, seed_user, periods):
    """Create expenses with consistent spending across 7+ months.

    Creates one expense per period (not per month) with the same
    amount so per-period averages remain stable.
    """
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    paid_status_id = ref_cache.status_id(StatusEnum.DONE)
    cat = seed_user["categories"]["Rent"]
    months_seen = set()
    count = 0
    for p in periods:
        month_key = (p.start_date.year, p.start_date.month)
        months_seen.add(month_key)
        if len(months_seen) > 8:
            break
        txn = Transaction(
            account_id=seed_user["account"].id,
            scenario_id=seed_user["scenario"].id,
            pay_period_id=p.id,
            status_id=paid_status_id,
            transaction_type_id=expense_type_id,
            name=f"Rent P{count}",
            estimated_amount=Decimal("400.00"),
            actual_amount=Decimal("400.00"),
            category_id=cat.id,
            due_date=p.start_date,
        )
        db.session.add(txn)
        count += 1
    db.session.commit()


def _seed_increasing_trend_with_timing(db, seed_user, periods):
    """Create increasing per-period expenses with paid_at for OP-3.

    Payments made 3 days before due date.
    """
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    paid_status_id = ref_cache.status_id(StatusEnum.DONE)
    cat = seed_user["categories"]["Rent"]
    amount = Decimal("100.00")
    for p in periods:
        due = p.start_date
        paid = datetime(due.year, due.month, max(1, due.day - 3),
                        tzinfo=timezone.utc)
        txn = Transaction(
            account_id=seed_user["account"].id,
            scenario_id=seed_user["scenario"].id,
            pay_period_id=p.id,
            status_id=paid_status_id,
            transaction_type_id=expense_type_id,
            name=f"Rent {p.start_date.strftime('%b %d')}",
            estimated_amount=amount,
            actual_amount=amount,
            category_id=cat.id,
            due_date=due,
            paid_at=paid,
        )
        db.session.add(txn)
        amount += Decimal("20.00")
    db.session.commit()


def _seed_increasing_trend_with_late_timing(db, seed_user, periods):
    """Create increasing per-period expenses paid 5 days AFTER due.

    Ensures avg_days_before_due is negative (late payments).
    """
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    paid_status_id = ref_cache.status_id(StatusEnum.DONE)
    cat = seed_user["categories"]["Rent"]
    amount = Decimal("100.00")
    for p in periods:
        due = p.start_date
        paid = datetime(due.year, due.month, min(28, due.day + 5),
                        tzinfo=timezone.utc)
        txn = Transaction(
            account_id=seed_user["account"].id,
            scenario_id=seed_user["scenario"].id,
            pay_period_id=p.id,
            status_id=paid_status_id,
            transaction_type_id=expense_type_id,
            name=f"Rent {p.start_date.strftime('%b %d')}",
            estimated_amount=amount,
            actual_amount=amount,
            category_id=cat.id,
            due_date=due,
            paid_at=paid,
        )
        db.session.add(txn)
        amount += Decimal("20.00")
    db.session.commit()


# ── Auth Tests ──────────────────────────────────────────────────────


class TestAnalyticsAuth:
    """Tests for authentication requirements on analytics endpoints."""

    def test_analytics_requires_auth(self, app, client):
        """GET /analytics redirects unauthenticated users to login."""
        with app.app_context():
            resp = client.get("/analytics")
            assert resp.status_code == 302
            assert "/login" in resp.headers["Location"]

    def test_all_tabs_require_auth(self, app, client):
        """Every analytics tab endpoint requires auth.

        The five current tab endpoints AND the three retired-redirect URLs
        (/variance, /trends, /year-end) all sit behind ``@login_required``,
        so an unauthenticated GET 302s to /login before any content render
        or redirect-to-page fires.
        """
        tab_urls = [
            "/analytics/calendar",
            "/analytics/spending",
            "/analytics/taxes",
            "/analytics/income-statement",
            "/analytics/balance-sheet",
            "/analytics/variance",
            "/analytics/trends",
            "/analytics/year-end",
        ]
        with app.app_context():
            for url in tab_urls:
                resp = client.get(url)
                assert resp.status_code == 302, (
                    f"{url} did not require auth"
                )
                assert "/login" in resp.headers["Location"], (
                    f"{url} did not redirect to login"
                )


# ── Page Rendering Tests ──────────────────────────────────────────


class TestAnalyticsPage:
    """Tests for GET /analytics page structure and content."""

    def test_analytics_page_renders(self, app, auth_client, seed_user):
        """GET /analytics returns 200 with Analytics heading."""
        with app.app_context():
            resp = auth_client.get("/analytics")
            assert resp.status_code == 200
            assert b"Analytics" in resp.data

    def test_analytics_page_has_pills(self, app, auth_client, seed_user):
        """GET /analytics shows the four Slice-4 nav pills.

        The Slice-4 shell collapse reduced the nav to four pills: Calendar,
        Spending, Statements, and Taxes.  The Income Statement and Balance
        Sheet are no longer shell pills -- they load inside the Statements
        pill's internal toggle via HTMX, so their labels do not appear on
        the bare page.  The retired Year-End / Variance / Trends labels are
        gone from the nav (their routes now redirect to the page).
        """
        with app.app_context():
            resp = auth_client.get("/analytics")
            assert resp.status_code == 200
            html = resp.data
            assert b"Calendar" in html
            assert b"Spending" in html
            assert b"Statements" in html
            assert b"Taxes" in html
            assert b"Year-End" not in html
            assert b"Variance" not in html
            assert b"Trends" not in html
            # Income Statement / Balance Sheet load inside the Statements
            # toggle (HTMX), so they are absent from the bare shell.
            assert b"Income Statement" not in html
            assert b"Balance Sheet" not in html

    def test_analytics_page_has_tab_content_div(self, app, auth_client, seed_user):
        """GET /analytics contains the #tab-content target div for HTMX swaps."""
        with app.app_context():
            resp = auth_client.get("/analytics")
            assert resp.status_code == 200
            assert b'id="tab-content"' in resp.data

    def test_calendar_tab_is_default_load(self, app, auth_client, seed_user):
        """Calendar pill has hx-trigger containing 'load' so it auto-loads."""
        with app.app_context():
            resp = auth_client.get("/analytics")
            html = resp.data.decode()
            assert 'hx-trigger="click, load"' in html

    def test_other_tabs_no_auto_load(self, app, auth_client, seed_user):
        """Only the Calendar pill auto-loads; the other pills load on click."""
        with app.app_context():
            resp = auth_client.get("/analytics")
            html = resp.data.decode()
            # Only the Calendar pill should have the 'load' trigger.
            load_triggers = html.count('hx-trigger="click, load"')
            assert load_triggers == 1, (
                f"Expected exactly 1 pill with 'load' trigger, found {load_triggers}"
            )

    def test_tab_content_has_spinner(self, app, auth_client, seed_user):
        """The #tab-content div contains spinner markup as initial content."""
        with app.app_context():
            resp = auth_client.get("/analytics")
            assert resp.status_code == 200
            assert b"spinner-border" in resp.data

    def test_analytics_uses_scroll_pills(self, app, auth_client, seed_user):
        """GET /analytics uses the shekel-scroll-pills class for scroll behavior."""
        with app.app_context():
            resp = auth_client.get("/analytics")
            assert resp.status_code == 200
            assert b"shekel-scroll-pills" in resp.data


# ── HTMX Tab Tests ────────────────────────────────────────────────


class TestCalendarTab:
    """Tests for GET /analytics/calendar HTMX partial endpoint."""

    def test_calendar_tab_htmx(self, app, auth_client, seed_user, seed_periods):
        """GET /analytics/calendar with HX-Request returns 200 with calendar."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            # Calendar replaced the placeholder; month view renders by default.
            assert b"calendar-grid" in resp.data

    def test_calendar_tab_no_htmx_redirects(self, app, auth_client, seed_user):
        """GET /analytics/calendar without HX-Request redirects to /analytics."""
        with app.app_context():
            resp = auth_client.get("/analytics/calendar")
            assert resp.status_code == 302
            assert "/analytics" in resp.headers["Location"]

    def test_calendar_tab_404_when_account_unresolvable(
        self, app, auth_client, seed_user, monkeypatch,
    ):
        """C11-1 (route): unresolvable analytics account returns 404.

        F-2 / Commit 11: pre-remediation the route silently rendered a
        zeroed calendar.  After the contract change, the route maps
        ``CalendarAccountNotResolvableError`` to a 404 ("404 for both
        'not found' and 'not yours'").  Monkeypatching the resolver
        is the deterministic way to simulate the upstream defect
        without coupling the test to user/account fixture deletion.
        """
        from app.services import calendar_service as cs
        monkeypatch.setattr(
            cs, "resolve_analytics_account",
            lambda _user_id, _account_id: None,
        )
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 404

    def test_calendar_tab_404_when_scenario_unresolvable(
        self, app, auth_client, seed_user, monkeypatch,
    ):
        """C11-2 (route): unresolvable baseline scenario returns 404."""
        from app.services import calendar_service as cs
        monkeypatch.setattr(
            cs, "get_baseline_scenario",
            lambda _user_id: None,
        )
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 404

    def test_calendar_tab_year_view_404_when_account_unresolvable(
        self, app, auth_client, seed_user, monkeypatch,
    ):
        """C11-1 (route, year view): year-view path also 404s."""
        from app.services import calendar_service as cs
        monkeypatch.setattr(
            cs, "resolve_analytics_account",
            lambda _user_id, _account_id: None,
        )
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=year",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 404

    def test_calendar_tab_csv_404_when_scenario_unresolvable(
        self, app, auth_client, seed_user, monkeypatch,
    ):
        """C11-1/C11-2 (route, CSV branch): CSV path also 404s."""
        from app.services import calendar_service as cs
        monkeypatch.setattr(
            cs, "get_baseline_scenario",
            lambda _user_id: None,
        )
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?format=csv&view=month"
                "&year=2026&month=1",
            )
            assert resp.status_code == 404

    def test_calendar_tab_200_when_resolvable(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """C11-3 (route): valid account + scenario renders the calendar.

        Locks the happy path so future regressions of the exception
        handler (e.g. raising too eagerly) fail loud.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"calendar-grid" in resp.data

    def test_calendar_basis_chip_and_scope(self, app, auth_client, seed_user,
                                           seed_periods):
        """The calendar month view labels its MIXED basis and checking scope.

        Slice-4 cross-cutting fix: the calendar states its data basis (a
        projected balance line combined with measured/projected flows) via
        the unified basis chip, and names the checking account it reads so
        the previously-silent scope is on screen.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "analytics-basis-chip" in html
            assert ">mixed<" in html
            assert "Checking account:" in html


# ── Income Statement Tab Tests ────────────────────────────────────


class TestIncomeStatementTab:
    """Tests for GET /analytics/income-statement HTMX partial endpoint.

    Build-Order Step 5: the confirmed-ledger income statement.  The
    money queries read the double-entry posting ledger, so content
    tests settle through ``create_settled_cash_transaction`` (the real
    go-forward posting path) -- a directly-inserted transaction never
    posts and would not appear.
    """

    def test_income_statement_requires_auth(self, app, client):
        """Unauthenticated request redirects to login."""
        with app.app_context():
            resp = client.get("/analytics/income-statement")
            assert resp.status_code == 302
            assert "/login" in resp.headers["Location"]

    def test_income_statement_no_htmx_redirects(self, app, auth_client,
                                                seed_user):
        """Non-HTMX request redirects to /analytics."""
        with app.app_context():
            resp = auth_client.get("/analytics/income-statement")
            assert resp.status_code == 302
            assert "/analytics" in resp.headers["Location"]

    def test_income_statement_htmx_renders(self, app, auth_client, seed_user,
                                           seed_periods):
        """HTMX request renders the statement with its heading and toggle."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/income-statement",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Income Statement" in html
            # The pay-period / month / year window toggle.
            assert "Pay Period" in html
            assert "Month" in html
            assert "Year" in html

    def test_income_statement_shows_statements_toggle(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The shared Statements header renders: the confirmed basis chip,
        the Statements title, and the Balance Sheet toggle target so the two
        confirmed-ledger reports switch inside one pill.  No CSV button (the
        Statements exports were retired at Slice 4).
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/income-statement",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "analytics-basis-chip" in html
            assert ">confirmed<" in html
            assert "Statements" in html
            assert "/analytics/balance-sheet" in html
            assert "Balance Sheet" in html
            # CSV export retired: no download button, no format=csv link.
            assert "format=csv" not in html
            assert "download" not in html

    def test_income_statement_empty_state(self, app, auth_client, seed_user,
                                          seed_periods):
        """A window with no posted income or expense shows the empty state.

        The seed Checking's $1000 opening is an Equity correction, so it
        never reaches the Income/Expense filter -- the current period is
        genuinely empty on the income statement.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/income-statement?window=pay_period"
                f"&period_id={seed_periods[3].id}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"No income or expenses in this window" in resp.data

    def test_income_statement_no_periods_falls_back(self, app, auth_client,
                                                    seed_user):
        """With no pay periods, the default window degrades to a month window."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/income-statement?window=pay_period",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"No income or expenses in this window" in resp.data

    def test_income_statement_pay_period_content(self, app, auth_client,
                                                 seed_user, seed_periods, db):
        """A settled income + expense in a period section and net out.

        Income 2000, expense 300 -> net income 1700.  Both settle through
        the real posting path so they land on the confirmed ledger the
        statement reads.
        """
        with app.app_context():
            create_settled_cash_transaction(
                seed_user, db.session, seed_periods[0], Decimal("2000.00"),
                is_income=True, name="Paycheck",
            )
            create_settled_cash_transaction(
                seed_user, db.session, seed_periods[0], Decimal("300.00"),
                is_income=False, name="Groceries",
            )
            db.session.commit()

            resp = auth_client.get(
                "/analytics/income-statement?window=pay_period"
                f"&period_id={seed_periods[0].id}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Income" in html
            assert "Expenses" in html
            assert "$2,000.00" in html
            assert "$300.00" in html
            assert "Net Income" in html
            assert "$1,700.00" in html

    def test_income_statement_monthly_window(self, app, auth_client, seed_user,
                                             seed_periods):
        """Monthly window label contains the month name and year."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/income-statement?window=month&month=1&year=2026",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"January" in resp.data
            assert b"2026" in resp.data

    def test_income_statement_annual_window(self, app, auth_client, seed_user,
                                            seed_periods):
        """Annual window label contains the year."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/income-statement?window=year&year=2026",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"2026" in resp.data

    def test_income_statement_out_of_range_month_clamped(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A hand-crafted out-of-range month/year clamps instead of 500ing.

        ``calendar_tab`` clamps its calendar fields; the income statement
        does the same so a crafted URL cannot reach ``monthrange()`` /
        ``date()`` in the service and raise.  month=13 clamps to 12
        (December), year=99999 clamps to 2100.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/income-statement?window=month&month=13&year=99999",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"December" in resp.data
            assert b"2100" in resp.data


# ── Balance Sheet Tab Tests ───────────────────────────────────────


class TestBalanceSheetTab:
    """Tests for GET /analytics/balance-sheet HTMX partial endpoint.

    Build-Order Step 5: the confirmed-ledger balance sheet.  The seed
    Checking's $1000 opening is dated by its origination ``created_at``
    (the real DB clock), which the module's frozen 2026-03-20 ``today``
    predates, so the default as-of does NOT fold it.  Content is therefore
    exercised with a settled transaction whose ``paid_at`` is pinned inside
    the frozen range; the opening is excluded but as a WHOLE entry, so the
    tie-out still closes.  A far-future ``today`` refreeze is avoided
    deliberately: Flask-Login would treat the real-clock session as
    idle-expired and redirect the request to login.
    """

    def test_balance_sheet_requires_auth(self, app, client):
        """Unauthenticated request redirects to login."""
        with app.app_context():
            resp = client.get("/analytics/balance-sheet")
            assert resp.status_code == 302
            assert "/login" in resp.headers["Location"]

    def test_balance_sheet_no_htmx_redirects(self, app, auth_client, seed_user):
        """Non-HTMX request redirects to /analytics."""
        with app.app_context():
            resp = auth_client.get("/analytics/balance-sheet")
            assert resp.status_code == 302
            assert "/analytics" in resp.headers["Location"]

    def test_balance_sheet_htmx_renders(self, app, auth_client, seed_user):
        """HTMX request renders the balance sheet heading and as-of input."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/balance-sheet",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Balance Sheet" in html
            assert 'type="date"' in html
            assert 'name="as_of"' in html

    def test_balance_sheet_shows_statements_toggle(self, app, auth_client,
                                                   seed_user):
        """The shared Statements header renders with the confirmed basis chip
        and the Income Statement toggle target; no CSV button (retired)."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/balance-sheet",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "analytics-basis-chip" in html
            assert ">confirmed<" in html
            assert "/analytics/income-statement" in html
            assert "format=csv" not in html
            assert "download" not in html

    def test_balance_sheet_format_csv_no_longer_exports(
        self, app, auth_client, seed_user,
    ):
        """The retired ?format=csv no longer returns a CSV body.

        The balance-sheet CSV export was removed at Slice 4; a stale
        ?format=csv now falls through to the normal render (an HTMX
        partial), never a text/csv attachment.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/balance-sheet?format=csv",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert "text/csv" not in resp.headers.get("Content-Type", "")

    def test_balance_sheet_shows_posted_content_and_ties_out(
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """A settled income posts to the sheet and the tie-out stays green.

        A $500 income settled with a ``paid_at`` inside the frozen range
        (2026-02-15) folds into the default as-of (2026-03-20): Checking
        +500 (Asset) and Retained Earnings +500 (Income closed into
        equity).  The seed opening's entry_date is the real-clock
        origination ``created_at`` (after the frozen today), so it is
        excluded -- but as a WHOLE entry (both its Asset and Equity legs),
        so the tie-out still closes.  A far-future refreeze is avoided
        deliberately: it would make Flask-Login treat the real-clock
        session as idle-expired.
        """
        with app.app_context():
            create_settled_cash_transaction(
                seed_user, db.session, seed_periods[0], Decimal("500.00"),
                is_income=True, name="Paycheck",
                paid_at=datetime(2026, 2, 15, 12, tzinfo=timezone.utc),
            )
            db.session.commit()

            resp = auth_client.get(
                "/analytics/balance-sheet",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Assets" in html
            assert "Liabilities" in html
            assert "Equity" in html
            assert "Checking" in html
            assert "Retained Earnings" in html
            assert "$500.00" in html
            assert "In balance" in html

    def test_balance_sheet_before_opening_is_empty(self, app, auth_client,
                                                   seed_user):
        """An as-of before any posted source shows the empty state.

        The seed opening is dated no earlier than the account's
        origination, so an as-of of 2001-01-01 folds nothing.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/balance-sheet?as_of=2001-01-01",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"No posted balances" in resp.data

    def test_balance_sheet_future_as_of_clamped(self, app, auth_client,
                                                seed_user):
        """A future as-of clamps to today (the date input shows today)."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/balance-sheet?as_of=2099-12-31",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            # Frozen today is 2026-03-20; the future as-of clamps to it.
            assert 'value="2026-03-20"' in resp.data.decode()

    def test_balance_sheet_garbage_as_of_defaults_today(self, app, auth_client,
                                                        seed_user):
        """A non-ISO as-of degrades to today rather than raising."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/balance-sheet?as_of=not-a-date",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert 'value="2026-03-20"' in resp.data.decode()


# ── CSV Export Tests ──────────────────────────────────────────────


class TestCsvExport:
    """Tests for the calendar CSV export (the only surviving export).

    The Slice-4 shell collapse retired the year-end, variance, trends,
    income-statement, and balance-sheet CSV exports with their tabs; only
    the calendar month/year CSV still ships (Calendar decision 9).
    """

    def test_calendar_csv_export(self, app, auth_client, seed_user,
                                  seed_periods):
        """C17-1: Calendar CSV returns 200 with text/csv content type."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?format=csv&view=month&year=2026&month=1",
            )
            assert resp.status_code == 200
            assert "text/csv" in resp.headers["Content-Type"]

    def test_calendar_csv_content(self, app, auth_client, seed_user,
                                   seed_periods, db):
        """C17-2: Calendar CSV body contains transaction names."""
        with app.app_context():
            _create_paid_expense_for_route_test(
                db, seed_user, seed_periods,
                "January Rent", Decimal("1200.00"), "Rent",
            )
            resp = auth_client.get(
                f"/analytics/calendar?format=csv&view=month&year=2026&month=1"
                f"&period_id={seed_periods[0].id}",
            )
            assert resp.status_code == 200
            assert b"January Rent" in resp.data

    def test_calendar_csv_has_eod_balance_column(
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """The month CSV carries the end-of-day running-balance column.

        Anchor $1000 (period 0); a projected -$250 expense due Jan 8 leaves
        the projected end-of-day balance at $750 on that day.
        """
        with app.app_context():
            from app import ref_cache
            from app.enums import StatusEnum, TxnTypeEnum
            from app.models.transaction import Transaction
            from datetime import date
            from decimal import Decimal
            db.session.add(Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="Groceries",
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                estimated_amount=Decimal("250.00"),
                due_date=date(2026, 1, 8),
            ))
            db.session.commit()
            resp = auth_client.get(
                "/analytics/calendar?format=csv&view=month&year=2026&month=1",
            )
            assert resp.status_code == 200
            body = resp.data.decode()
            assert "End-of-Day Balance ($)" in body
            # The Groceries row carries the day's projected EOD balance ($750).
            assert "750.00" in body

    def test_csv_requires_auth(self, app, client):
        """C17-8: CSV export requires authentication."""
        with app.app_context():
            resp = client.get("/analytics/calendar?format=csv")
            assert resp.status_code == 302
            assert "/login" in resp.headers["Location"]

    def test_csv_content_disposition(self, app, auth_client, seed_user,
                                      seed_periods):
        """C17-9: CSV has Content-Disposition with attachment and .csv."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?format=csv&view=month&year=2026&month=1",
            )
            cd = resp.headers.get("Content-Disposition", "")
            assert "attachment" in cd
            assert ".csv" in cd

    def test_csv_does_not_require_htmx(self, app, auth_client, seed_user,
                                        seed_periods):
        """C17-extra12: the calendar CSV works without an HX-Request header."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?format=csv&view=month&year=2026&month=1",
            )
            # Should NOT redirect -- CSV bypasses the HTMX guard.
            assert resp.status_code == 200
            assert "text/csv" in resp.headers["Content-Type"]

    def test_csv_filename_includes_context(self, app, auth_client,
                                            seed_user, seed_periods):
        """C17-extra14: Calendar CSV filename contains year and month."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?format=csv&view=month&year=2026&month=4",
            )
            cd = resp.headers.get("Content-Disposition", "")
            assert "2026_04" in cd

    def test_calendar_year_csv_export(self, app, auth_client, seed_user,
                                       seed_periods):
        """C17-extra15: Calendar year CSV returns year overview data."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?format=csv&view=year&year=2026",
            )
            assert resp.status_code == 200
            assert b"January" in resp.data

    def test_html_still_works_without_format(self, app, auth_client,
                                              seed_user, seed_periods):
        """C17-extra16: Without format=csv, normal HTMX HTML is returned."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"calendar-grid" in resp.data
            assert "text/csv" not in resp.headers.get("Content-Type", "")

    def test_calendar_has_export_button(self, app, auth_client, seed_user,
                                         seed_periods):
        """C17-extra17: Calendar tab contains CSV export link."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "format=csv" in html
            assert "bi-download" in html


# ── Retired Tab Redirect Tests ────────────────────────────────────


class TestRetiredTabRedirects:
    """The retired Variance / Trends / Year-End URLs redirect to /analytics.

    Slice-4 shell collapse: these three tabs were folded into Spending and
    Taxes, so their routes are now a single redirect stub.  Old bookmarks and
    the former ?format=csv exports land on the current page instead of 404ing.
    """

    def test_retired_urls_redirect_to_page(self, app, auth_client, seed_user):
        """Each retired URL 302s to /analytics for an authenticated user."""
        with app.app_context():
            for url in ("/analytics/variance", "/analytics/trends",
                        "/analytics/year-end"):
                resp = auth_client.get(url)
                assert resp.status_code == 302, f"{url} did not redirect"
                assert resp.headers["Location"].endswith("/analytics"), (
                    f"{url} did not redirect to /analytics"
                )

    def test_retired_csv_urls_also_redirect(self, app, auth_client, seed_user):
        """The former ?format=csv exports redirect too (no CSV body)."""
        with app.app_context():
            resp = auth_client.get("/analytics/year-end?format=csv&year=2026")
            assert resp.status_code == 302
            assert "text/csv" not in resp.headers.get("Content-Type", "")
            assert resp.headers["Location"].endswith("/analytics")

    def test_retired_urls_still_require_auth(self, app, client):
        """The redirect stub is behind login_required: unauth -> /login."""
        with app.app_context():
            resp = client.get("/analytics/variance")
            assert resp.status_code == 302
            assert "/login" in resp.headers["Location"]


# ── Nav Bar Tests ─────────────────────────────────────────────────


class TestAnalyticsNav:
    """Tests for nav bar updates after Charts-to-Analytics rename."""

    def test_nav_shows_analytics_link(self, app, auth_client, seed_user):
        """Authenticated pages show Analytics link in the nav bar."""
        with app.app_context():
            resp = auth_client.get("/")
            assert resp.status_code == 200
            assert b"Analytics" in resp.data
            assert b'href="/analytics"' in resp.data

    def test_nav_does_not_show_charts_link(self, app, auth_client, seed_user):
        """Nav bar no longer shows a link pointing to /charts."""
        with app.app_context():
            resp = auth_client.get("/")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert 'href="/charts"' not in html

    def test_charts_route_redirects(self, app, auth_client, seed_user):
        """GET /charts returns 301 redirect to /analytics."""
        with app.app_context():
            resp = auth_client.get("/charts")
            assert resp.status_code == 301
            assert "/analytics" in resp.headers["Location"]

    def test_analytics_active_nav_state(self, app, auth_client, seed_user):
        """GET /analytics shows the Analytics nav item as active."""
        with app.app_context():
            resp = auth_client.get("/analytics")
            assert resp.status_code == 200
            html = resp.data.decode()
            # The nav link for /analytics should have the active class.
            assert 'class="nav-link active" href="/analytics"' in html


# ── Calendar Month View Tests ────────────────────────────────────────


class TestCalendarMonthView:
    """Tests for the calendar month detail view."""

    def test_calendar_month_renders(self, app, auth_client, seed_user, seed_periods):
        """Month view renders with current month name."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"calendar-grid" in resp.data

    def test_calendar_month_navigation(self, app, auth_client, seed_user, seed_periods):
        """Month view for specific month/year contains the correct heading."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=3",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"March" in resp.data
            assert b"2026" in resp.data

    def test_calendar_month_has_day_cells(self, app, auth_client, seed_user, seed_periods):
        """Month view contains calendar-day elements."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"calendar-day" in resp.data

    def test_calendar_paycheck_highlighting(self, app, auth_client, seed_user, seed_periods):
        """Paycheck days have the calendar-paycheck CSS class."""
        with app.app_context():
            # Request a month with known paycheck days (Jan 2026 has
            # periods starting Jan 2, Jan 16, Jan 30).
            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"calendar-paycheck" in resp.data

    def test_calendar_month_empty(self, app, auth_client, seed_user, seed_periods):
        """Month with no transactions renders without crash."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=4",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"calendar-grid" in resp.data

    def test_calendar_month_prev_next(self, app, auth_client, seed_user, seed_periods):
        """Month view has prev/next navigation buttons."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=6",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "month=5" in html  # prev button
            assert "month=7" in html  # next button

    def test_calendar_month_december_next_wraps(self, app, auth_client, seed_user, seed_periods):
        """December next button wraps to January of next year."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=12",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "year=2027" in html
            assert "month=1" in html

    def test_calendar_month_january_prev_wraps(self, app, auth_client, seed_user, seed_periods):
        """January prev button wraps to December of prior year."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "year=2025" in html
            assert "month=12" in html

    def test_calendar_month_year_overview_button(self, app, auth_client, seed_user, seed_periods):
        """Month view has a button to switch to year overview."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "view=year" in html

    def test_calendar_month_summary_strip_displayed(self, app, auth_client, seed_user, seed_periods, db):
        """Month view shows the summary strip per the slice-1 rebuild anatomy.

        Redesign pin (analytics_audit.md, Gate A + "Calendar rebuild
        decisions" 6, developer-locked 2026-07-04): the old
        Income/Expenses/Net totals row was replaced by the summary strip --
        So far / Remaining income+expense pairs plus Month end and Month
        trough chips.  January 2026 is wholly past relative to any display-tz
        today after 2026-01-31, so the So far chip renders (captioned "full
        month") and the future-only Remaining chip does not.
        """
        with app.app_context():
            from app import ref_cache
            from app.enums import StatusEnum, TxnTypeEnum
            from app.models.transaction import Transaction
            from datetime import date
            from decimal import Decimal

            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="Test Income",
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
                estimated_amount=Decimal("3000.00"),
                due_date=date(2026, 1, 5),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "calendar-summary" in html
            assert "So far" in html
            assert "Income" in html
            assert "Expenses" in html
            assert "Month end" in html
            assert "Month trough" in html
            # Wholly past month: the projected-remainder chip is empty by
            # construction and hidden.
            assert "Remaining" not in html
            # So far pair: the seeded $3,000 income is the month's only flow.
            # elapsed_income = 3000.00, elapsed_expense = 0.00.
            assert "$3,000.00" in html
            assert "$0.00" in html

    def test_calendar_default_view_is_month(self, app, auth_client, seed_user, seed_periods):
        """No view param defaults to month view."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"calendar-grid" in resp.data

    def test_calendar_invalid_month_handled(self, app, auth_client, seed_user, seed_periods):
        """Invalid month=13 clamped to valid range, no crash."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month&month=13",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200

    def test_calendar_today_highlighted(self, app, auth_client, seed_user, seed_periods):
        """Current month view contains today indicator class."""
        with app.app_context():
            from datetime import datetime, timezone
            from app.utils.dates import to_display_date
            # The route marks today in the display timezone, so the test must
            # ask for the display-tz month (not the server's UTC day) or it
            # flakes at the midnight-UTC month boundary.
            today = to_display_date(datetime.now(timezone.utc))
            resp = auth_client.get(
                f"/analytics/calendar?view=month&year={today.year}&month={today.month}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"calendar-day--today" in resp.data


# ── Calendar Year View Tests ─────────────────────────────────────────


class TestCalendarYearView:
    """Tests for the calendar year overview."""

    def test_calendar_year_renders(self, app, auth_client, seed_user, seed_periods):
        """Year view renders with all 12 month names."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=year&year=2026",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            for month_name in [b"January", b"February", b"March", b"April",
                               b"May", b"June", b"July", b"August",
                               b"September", b"October", b"November", b"December"]:
                assert month_name in resp.data

    def test_calendar_third_paycheck_badge(self, app, auth_client, seed_user, db):
        """Year with 26 periods shows '3rd check' badge."""
        with app.app_context():
            from app.services import pay_period_service
            from datetime import date
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date(2026, 1, 2),
                num_periods=26,
                cadence_days=14,
            )
            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()

            resp = auth_client.get(
                "/analytics/calendar?view=year&year=2026",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"3rd check" in resp.data

    def test_calendar_year_navigation(self, app, auth_client, seed_user, seed_periods):
        """Year view navigation shows correct year."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=year&year=2025",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"2025" in resp.data

    def test_calendar_year_month_click_links(self, app, auth_client, seed_user, seed_periods):
        """Month cards contain hx-get with view=month params."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=year&year=2026",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "view=month" in html

    def test_calendar_year_annual_totals(self, app, auth_client, seed_user, seed_periods):
        """Year view shows annual total labels."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=year&year=2026",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"Annual Income" in resp.data
            assert b"Annual Expenses" in resp.data
            assert b"Annual Net" in resp.data


# ── Calendar Inline Totals and Day Detail Tests ───────────────────────


class TestCalendarInlineTotals:
    """Tests for inline day totals and day detail section."""

    def test_calendar_day_totals_rendered(self, app, auth_client, seed_user, seed_periods, db):
        """Day with transactions shows inline income/expense totals."""
        with app.app_context():
            from app import ref_cache
            from app.enums import StatusEnum, TxnTypeEnum
            from app.models.transaction import Transaction
            from datetime import date
            from decimal import Decimal

            txn_inc = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="Test Paycheck",
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
                estimated_amount=Decimal("2500.00"),
                due_date=date(2026, 1, 5),
            )
            txn_exp = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="Test Rent",
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                estimated_amount=Decimal("1200.00"),
                due_date=date(2026, 1, 5),
            )
            db.session.add_all([txn_inc, txn_exp])
            db.session.commit()

            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert resp.status_code == 200
            assert "calendar-day-income" in html
            assert "calendar-day-expense" in html

    def test_calendar_day_detail_template(self, app, auth_client, seed_user, seed_periods, db):
        """Day with entries has a template element containing the transaction name."""
        with app.app_context():
            from app import ref_cache
            from app.enums import StatusEnum, TxnTypeEnum
            from app.models.transaction import Transaction
            from datetime import date
            from decimal import Decimal

            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="Electric Bill Detail",
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                estimated_amount=Decimal("150.00"),
                due_date=date(2026, 1, 10),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert resp.status_code == 200
            assert 'data-detail-day="10"' in html
            assert "Electric Bill Detail" in html

    def test_calendar_no_popover_attributes(self, app, auth_client, seed_user, seed_periods, db):
        """Calendar month view does not contain Bootstrap popover attributes."""
        with app.app_context():
            from app import ref_cache
            from app.enums import StatusEnum, TxnTypeEnum
            from app.models.transaction import Transaction
            from datetime import date
            from decimal import Decimal

            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="Popover Check",
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                estimated_amount=Decimal("100.00"),
                due_date=date(2026, 1, 15),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert resp.status_code == 200
            assert 'data-bs-toggle="popover"' not in html

    def test_calendar_day_click_attributes(self, app, auth_client, seed_user, seed_periods, db):
        """Day with entries has data-day and role=button attributes."""
        with app.app_context():
            from app import ref_cache
            from app.enums import StatusEnum, TxnTypeEnum
            from app.models.transaction import Transaction
            from datetime import date
            from decimal import Decimal

            # Period 1 (Jan 16-29) is the period whose span contains the
            # Jan 20 due date, so the clamped attribution lands on the 20th.
            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=seed_periods[1].id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="Click Test",
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                estimated_amount=Decimal("200.00"),
                due_date=date(2026, 1, 20),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert resp.status_code == 200
            assert 'data-day="20"' in html
            assert 'role="button"' in html


# ── Calendar Flow Strip and Day-Cell Hero Tests (slice-1 rebuild) ─────


def _extract_flow_strip_payload(page_html):
    """Parse the flow strip canvas's ``data-chart`` JSON out of a month view.

    The attribute value is HTML-escaped by Jinja autoescaping (quotes as
    ``&#34;``), so it is unescaped before ``json.loads`` -- the same
    decode path the browser's ``getAttribute`` performs.
    """
    import html as html_mod
    import json
    import re

    match = re.search(r"data-chart='([^']*)'", page_html)
    assert match is not None, "flow strip data-chart attribute missing"
    return json.loads(html_mod.unescape(match.group(1)))


class TestCalendarFlowStrip:
    """Pins for the month flow strip payload and the day-cell balance hero.

    Slice-1 rebuild behavior (analytics_audit.md, "Calendar rebuild
    decisions" + "Loop B P1 as-built"): the strip serializes the daily
    running-balance series with measured/projected split indices, the
    user's low-balance threshold, payday dots, and the labeled trough;
    day cells carry the projected end-of-day balance hero with the
    modeled-tilde and below-threshold danger treatments.
    """

    def test_flow_strip_payload_shape(self, app, auth_client, seed_user, seed_periods):
        """January 2026 with no transactions serializes a flat $1,000 series.

        Hand-computed: the seeded account anchors at $1,000.00 with no
        transactions, so every end-of-day balance is 1000.0.  January 2026
        is wholly past (display-tz today is after 2026-01-31), so
        current_index equals the day count (all measured).  Paydays are the
        seeded period starts Jan 2 / 16 / 30 (0-based indices 1, 15, 29).
        Weekly ticks are the 1st plus every Sunday: 2026-01-01 is a
        Thursday, so Sundays fall on Jan 4 / 11 / 18 / 25 (indices 3, 10,
        17, 24).  The default low-balance threshold is 500.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert 'id="calendar-flow-canvas"' in html

            payload = _extract_flow_strip_payload(html)
            assert len(payload["values"]) == 31
            assert all(value == 1000.0 for value in payload["values"])
            assert payload["current_index"] == 31
            assert payload["threshold"] == 500.0
            assert payload["payday_indices"] == [1, 15, 29]
            # Flat series: the trough is the earliest minimum, day 1.
            assert payload["trough_index"] == 0
            assert payload["week_tick_indices"] == [0, 3, 10, 17, 24]
            assert len(payload["labels"]) == 31
            assert payload["labels"][0] == "Jan 1"

    def test_flow_strip_trough_and_danger_cells(self, app, auth_client, seed_user, seed_periods, db):
        """A below-threshold trough renders the danger chip, cells, and index.

        Hand-computed: anchor $1,000.00; one projected $600.00 expense due
        Jan 5 (inside period Jan 2-15).  End-of-day balances: Jan 1-4 =
        $1,000.00; Jan 5-31 = 1000 - 600 = $400.00.  The trough is Jan 5
        (0-based index 4) at $400.00, below the default $500 threshold, so
        the Month trough chip takes the danger variant and the below-
        threshold day cells take the danger hero class.  January 2026 is
        wholly past, so no modeled tilde renders anywhere.
        """
        with app.app_context():
            from app import ref_cache
            from app.enums import StatusEnum, TxnTypeEnum
            from app.models.transaction import Transaction
            from datetime import date
            from decimal import Decimal

            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="Trough Expense",
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                estimated_amount=Decimal("600.00"),
                due_date=date(2026, 1, 5),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            payload = _extract_flow_strip_payload(html)
            assert payload["trough_index"] == 4
            assert payload["values"][3] == 1000.0
            assert payload["values"][4] == 400.0
            assert payload["values"][30] == 400.0

            assert "Month trough" in html
            assert "$400.00" in html
            assert "pulse-chip--danger" in html
            assert "calendar-day-balance--danger" in html
            # Wholly past month: measured treatment only, no modeled tilde.
            assert "~$" not in html

    def test_future_month_modeled_treatment(self, app, auth_client, seed_user, seed_periods):
        """A wholly future month renders all-modeled heroes and no measured chips.

        Uses the display-timezone today (the route's split basis) to pick a
        month two months ahead, so the assert never straddles the boundary.
        With no transactions the series is flat at the $1,000.00 anchor:
        every day is after today, so every balance hero carries the modeled
        tilde + secondary-ink class, current_index is 0 (all projected),
        and the elapsed-side chips (Balance today / So far) do not render
        while Remaining does.
        """
        with app.app_context():
            from datetime import datetime, timezone
            from app.utils.dates import to_display_date

            today = to_display_date(datetime.now(timezone.utc))
            year = today.year + (1 if today.month >= 11 else 0)
            month = today.month - 10 if today.month >= 11 else today.month + 2

            resp = auth_client.get(
                f"/analytics/calendar?view=month&year={year}&month={month}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            payload = _extract_flow_strip_payload(html)
            assert payload["current_index"] == 0

            assert "calendar-day-balance--modeled" in html
            assert "~$1,000" in html
            assert "Balance today" not in html
            assert "So far" not in html
            assert "Remaining" in html

    def test_day_cell_flow_lines_cap_and_overflow(self, app, auth_client, seed_user, seed_periods, db):
        """A five-flow day shows three named lines plus the +N more residual.

        Hand-computed: on Jan 5, one $3,000.00 income and four expenses
        ($1,200 / $300 / $100 / $50).  Cell order is income first then
        expenses by descending magnitude, capped at three named lines
        (Salary Chunk, Rent Big, Utility Mid), so exactly three
        calendar-day-flow lines render.  The hidden tail is Snack Small
        ($100) + Tiny Fee ($50): count 2, residual net -(100 + 50) =
        -$150, rendered whole-dollar as "-$150".
        """
        with app.app_context():
            from app import ref_cache
            from app.enums import StatusEnum, TxnTypeEnum
            from app.models.transaction import Transaction
            from datetime import date
            from decimal import Decimal

            flows = [
                ("Salary Chunk", TxnTypeEnum.INCOME, Decimal("3000.00")),
                ("Rent Big", TxnTypeEnum.EXPENSE, Decimal("1200.00")),
                ("Utility Mid", TxnTypeEnum.EXPENSE, Decimal("300.00")),
                ("Snack Small", TxnTypeEnum.EXPENSE, Decimal("100.00")),
                ("Tiny Fee", TxnTypeEnum.EXPENSE, Decimal("50.00")),
            ]
            for name, txn_type, amount in flows:
                db.session.add(Transaction(
                    account_id=seed_user["account"].id,
                    pay_period_id=seed_periods[0].id,
                    scenario_id=seed_user["scenario"].id,
                    status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                    name=name,
                    transaction_type_id=ref_cache.txn_type_id(txn_type),
                    estimated_amount=amount,
                    due_date=date(2026, 1, 5),
                ))
            db.session.commit()

            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            # Exactly three named flow lines on the one populated day.
            assert html.count('class="calendar-day-flow"') == 3
            assert "+2 more" in html
            assert "-$150" in html

    def test_paycheck_day_pay_tag(self, app, auth_client, seed_user, seed_periods):
        """Payday cells carry the PAY tag marker alongside the period tint."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "calendar-pay-tag" in html
            assert ">PAY</span>" in html


# ── Taxes Tab Tests (slice-2 rebuild, T-P4) ──────────────────────────


def _seed_taxes_profile(seed_user, db):
    """Seed the DEFAULT_* tax configs and a 130k single/NC salary profile.

    The T-P4 route-test fixture: 130,000 / 26 = 5,000.00 gross per period
    exactly (no rounding residue), no deductions, no calibration -- so every
    figure asserted below is hand-computable from the 2026 seeds.
    """
    from app.extensions import db as _db
    from app.models.ref import FilingStatus
    from app.models.salary_profile import SalaryProfile
    from app.services.auth_service import _seed_tax_data_for_user

    _seed_tax_data_for_user(seed_user["user"].id)
    filing_status = (
        _db.session.query(FilingStatus).filter_by(name="single").one()
    )
    profile = SalaryProfile(
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        name="Taxes Tab Profile",
        annual_salary=Decimal("130000.00"),
        pay_periods_per_year=26,
        filing_status_id=filing_status.id,
        state_code="NC",
        is_active=True,
    )
    db.session.add(profile)
    db.session.commit()
    return profile


class TestTaxesTab:
    """Pins for the Taxes tab (T-P4): hero, chips, ledger, W-2, Schedule A.

    Hand-computed baseline (130k single/NC profile, seed_periods = 10
    paydays in 2026, no checkpoint, no calibration, no deductions):

      per-period federal = round(19,934 / 26) = 766.69 -> x10 = 7,666.90
      per-period NC      = round(4,678.28 / 26) = 179.93 -> x10 = 1,799.30
      liability on hybrid gross 50,000:
        federal taxable 33,900 -> 1,240 + 21,500 x 0.12 = 3,820.00
        NC (50,000 - 12,750) x 0.0399 = 1,486.2750 -> 1,486.28
      federal refund 7,666.90 - 3,820.00 = 3,846.90
      NC refund      1,799.30 - 1,486.28 =   313.02
      total refund                       = 4,159.92
      effective (3,820 + 1,486.28) / 50,000 = 0.106126 -> 10.61%
      marginal: 33,900 sits in the 12,400-50,400 band -> 12.00%
    """

    def test_taxes_tab_hand_pinned_figures(self, app, auth_client, seed_user, seed_periods, db):
        """The full-modeled baseline renders every hand-computed figure."""
        with app.app_context():
            _seed_taxes_profile(seed_user, db)
            resp = auth_client.get(
                "/analytics/taxes?year=2026",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            assert "Estimated refund" in html
            assert "$4,159.92" in html   # hero + ledger total
            assert "$3,846.90" in html   # federal refund chip + ledger
            assert "$313.02" in html     # NC refund chip + ledger
            assert "10.61%" in html      # effective rate chip
            assert "12.00%" in html      # marginal rate chip
            assert "modeled estimate" in html
            assert "withholding fully modeled" in html

            # Derivation ledger + W-2 tie-out: Box 2 == federal withheld.
            assert "How the estimate is derived" in html
            assert "$7,666.90" in html
            assert "$3,820.00" in html   # federal tax line
            assert "$1,486.28" in html   # NC tax line
            assert "W-2 preview" in html
            assert "$50,000.00" in html  # Box 1 / Box 3 / Box 5 wages
            assert "$1,799.30" in html   # Box 17 == NC withheld

            # Schedule A: no loans seeded -> itemized = state tax only,
            # standard deduction 16,100 wins by 16,100 - 1,799.30 = 14,300.70.
            assert "Schedule A check" in html
            assert "standard deduction wins by" in html
            assert "$14,300.70" in html

            # The YTD checkpoint card and assumptions card are present.
            assert "ytd-checkpoint-card" in html
            assert "Assumptions" in html
            assert "bracket model" in html   # no calibration seeded

    def test_taxes_tab_checkpoint_reanchors_withholding(self, app, auth_client, seed_user, seed_periods, db):
        """A saved checkpoint re-anchors the measured side of the refund.

        Stub dated 2026-01-16 (the second payday) covers paydays 1-2;
        the remainder is the 8 later paydays.  Entered federal 1,600.00
        (vs 2 x 766.69 = 1,533.38 modeled), so:

          federal withheld = 1,600.00 + 8 x 766.69 = 7,733.52
          federal refund   = 7,733.52 - 3,820.00   = 3,913.52
        """
        with app.app_context():
            from datetime import date as date_cls
            from app.services import tax_withholding_service
            from app.services.tax_withholding_service import CheckpointFigures

            profile = _seed_taxes_profile(seed_user, db)
            tax_withholding_service.save_checkpoint(
                profile.id,
                CheckpointFigures(
                    as_of_date=date_cls(2026, 1, 16),
                    ytd_gross=Decimal("10000.00"),
                    ytd_federal=Decimal("1600.00"),
                    ytd_state=Decimal("360.00"),
                    ytd_social_security=Decimal("620.00"),
                    ytd_medicare=Decimal("145.00"),
                ),
            )
            db.session.commit()

            resp = auth_client.get(
                "/analytics/taxes?year=2026",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "$3,913.52" in html
            assert "measured through Jan 16" in html
            assert "Jan 16, 2026" in html   # assumptions "Measured through"

    def test_taxes_tab_actc_and_nc_child_deduction(self, app, auth_client, seed_user, seed_periods, db):
        """An MFJ 4-child household renders the ACTC row and NC child deduction.

        Hand-computed (130k MFJ, 4 qualifying children, seed_periods = 10
        paydays, no pre-tax, no checkpoint, 2026 seeds w/ OBBBA CTC 2,200 and
        ACTC cap 1,700):

          liability on hybrid gross 50,000:
            fed taxable 50,000 - 32,200 = 17,800 -> 10% band -> 1,780.00
            credits 4 x 2,200 = 8,800 -> liability clamps at 0
            unused 8,800 - 1,780 = 7,020
            ACTC = min(7,020, 4 x 1,700 = 6,800, 15% x 47,500 = 7,125)
                 = 6,800.00 (the CAP leg binds)
          fed withheld: annualized taxable 97,800 -> 11,240 - 8,800 = 2,440
            -> 93.85/period x 10 = 938.50
          fed refund = 938.50 - 0 + 6,800.00 = 7,738.50
          NC: base 50,000 -> tier 40-60k -> 2,500 x 4 = 10,000 child ded;
            taxable 50,000 - 25,500 - 10,000 = 14,500 x 0.0399 = 578.55
          NC withheld: (130,000 - 25,500) x 0.0399 = 4,169.55 -> 160.37 x 10
            = 1,603.70 -> NC refund 1,603.70 - 578.55 = 1,025.15
        """
        with app.app_context():
            from app.extensions import db as _db
            from app.models.ref import FilingStatus
            from app.models.salary_profile import SalaryProfile
            from app.services.auth_service import _seed_tax_data_for_user

            _seed_tax_data_for_user(seed_user["user"].id)
            filing_status = (
                _db.session.query(FilingStatus)
                .filter_by(name="married_jointly").one()
            )
            profile = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                name="MFJ Four Kids",
                annual_salary=Decimal("130000.00"),
                pay_periods_per_year=26,
                filing_status_id=filing_status.id,
                state_code="NC",
                is_active=True,
                qualifying_children=4,
            )
            db.session.add(profile)
            db.session.commit()

            resp = auth_client.get(
                "/analytics/taxes?year=2026",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            assert "Plus refundable child tax credit" in html
            assert "$6,800.00" in html          # the ACTC (cap leg binds)
            assert "$7,738.50" in html          # federal refund
            assert "$1,025.15" in html          # NC refund
            assert "$10,000 child deduction" in html
            assert "ACTC refundable cap" in html
            assert "$1,700 / child" in html
            assert "Refundable child tax credit (ACTC) modeled" in html
            assert "nonrefundable" not in html  # the stale caveat is gone

    def test_taxes_tab_empty_state_without_profile(self, app, auth_client, seed_user):
        """No active salary profile renders the empty state, not a crash."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/taxes",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "No active salary profile" in html
            assert "Set up salary" in html

    def test_taxes_tab_non_htmx_redirects(self, app, auth_client, seed_user):
        """A non-HTMX GET redirects to the analytics page."""
        with app.app_context():
            resp = auth_client.get("/analytics/taxes")
            assert resp.status_code == 302
            assert "/analytics" in resp.headers["Location"]


def _settled_spending_txn(db, seed_user, period, name, category_key,
                          estimated, *, actual=None, due_date=None):
    """Create one settled (DONE) expense for the Spending route tests.

    Args:
        db: Database session fixture.
        seed_user: User fixture dict.
        period: The owning pay period.
        name: Transaction name.
        category_key: Key into ``seed_user['categories']`` (or ``None``).
        estimated: Estimated amount (string, Decimal-safe).
        actual: Entered actual (string) or ``None`` (settled-without-actual).
        due_date: Optional due date; ``None`` attributes by the period start.

    Returns:
        The flushed :class:`Transaction`.
    """
    cat = seed_user["categories"].get(category_key)
    txn = Transaction(
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        pay_period_id=period.id,
        status_id=ref_cache.status_id(StatusEnum.DONE),
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name=name,
        estimated_amount=Decimal(estimated),
        actual_amount=Decimal(actual) if actual is not None else None,
        category_id=cat.id if cat else None,
        due_date=due_date,
    )
    db.session.add(txn)
    db.session.flush()
    return txn


class TestSpendingTab:
    """Pins for the Spending tab (Slice 3, S-P2): hero, breakdown, rail.

    Today is frozen to 2026-03-20 by the module autouse fixture, so the
    ``seed_periods`` months (Jan-May 2026) are completed and the calendar-
    month windows below are deterministic.
    """

    def test_spending_tab_breakdown_and_hero(self, app, auth_client, seed_user,
                                             seed_periods, db):
        """A January window renders the spent hero, group shares, and scope.

        seed_periods[0] (starts 2026-01-02) carries Rent 1200 (Home),
        Groceries 500 (Family), Car Payment 300 (Auto): total 2000, shares
        60% / 25% / 15%.
        """
        with app.app_context():
            _settled_spending_txn(db, seed_user, seed_periods[0], "Rent",
                                  "Rent", "1200.00")
            _settled_spending_txn(db, seed_user, seed_periods[0], "Food",
                                  "Groceries", "500.00")
            _settled_spending_txn(db, seed_user, seed_periods[0], "Car",
                                  "Car Payment", "300.00")
            db.session.commit()

            resp = auth_client.get(
                "/analytics/spending?year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            # Hero: 1200 + 500 + 300 = 2000, measured scope on Checking.
            assert "$2,000.00" in html
            assert "January 2026" in html
            assert "measured" in html
            assert "Checking" in html
            # Where It Went: groups amount-descending with the group amount.
            assert "Where It Went" in html
            assert "Home" in html
            assert "Family" in html
            assert "Auto" in html
            assert "$1,200.00" in html
            # Shares 60 / 25 / 15 percent.
            assert "60%" in html
            assert "25%" in html
            assert "15%" in html
            # Share bars carry the CSP-safe width attribute.
            assert "data-progress-pct" in html

    def test_spending_tab_comparison_chips(self, app, auth_client, seed_user,
                                           seed_periods, db):
        """A February window shows the vs-January comparison chip.

        January (period[0]) spent 1000; February (period[3], starts
        2026-02-13) spent 1500.  vs-prior delta = 1500 - 1000 = +500 (+50%),
        rendered as a spent-more (danger) direction.
        """
        with app.app_context():
            _settled_spending_txn(db, seed_user, seed_periods[0], "JanRent",
                                  "Rent", "1000.00")
            _settled_spending_txn(db, seed_user, seed_periods[3], "FebRent",
                                  "Rent", "1500.00")
            db.session.commit()

            resp = auth_client.get(
                "/analytics/spending?year=2026&month=2",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            assert "$1,500.00" in html          # February spent hero
            assert "vs January" in html          # vs-prior chip label
            assert "$500.00" in html             # |delta| = 1500 - 1000
            assert "+50.0% vs last month" in html
            assert "trend-up" in html            # spent more -> danger dir

    def test_spending_tab_surprises(self, app, auth_client, seed_user,
                                    seed_periods, db):
        """A settled row whose actual differs from estimate is a surprise.

        Electric Bill est 100 actual 145 -> delta +45 (a surprise); Rent
        Exact est 1200 actual 1200 -> delta 0 (not a surprise).  The net over
        ALL surprises is +45.
        """
        with app.app_context():
            _settled_spending_txn(db, seed_user, seed_periods[0], "Electric Bill",
                                  "Rent", "100.00", actual="145.00")
            _settled_spending_txn(db, seed_user, seed_periods[0], "Rent Exact",
                                  "Rent", "1200.00", actual="1200.00")
            db.session.commit()

            resp = auth_client.get(
                "/analytics/spending?year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            assert "Estimate Surprises" in html
            assert "Electric Bill" in html
            assert "+$45.00" in html             # delta 145 - 100
            # Net over ALL surprises = +45 (the est==actual row is excluded).
            assert "net +$45.00" in html

    def test_spending_tab_sparklines_and_movers(self, app, auth_client,
                                                seed_user, db):
        """With 12+ completed periods, item rows carry sparklines and movers.

        ``_seed_long_periods(14)`` starts 2025-07-03; its last period
        (index 13) is 2026-01-01..14 (January 2026).  A rising Rent series
        makes Rent both trendable (sparkline) and a top mover.
        """
        with app.app_context():
            periods = _seed_long_periods(db, seed_user, 14)
            _seed_increasing_trend(db, seed_user, periods)

            resp = auth_client.get(
                "/analytics/spending?year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            # Rent is trendable -> its row carries a sparkline polyline.
            assert "spend-spark-svg" in html
            assert "<polyline points=" in html
            # Rising Rent is a top mover; sufficiency met -> the reliability
            # note is shown, not the insufficient-data note.
            assert "Top Movers" in html
            assert "Rent" in html
            assert "recent per-period trend" in html
            assert "Not enough completed pay periods" not in html

    def test_spending_tab_empty_month(self, app, auth_client, seed_user,
                                      seed_periods):
        """A window with no settled spend renders the zeroed empty breakdown."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/spending?year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "$0.00" in html
            assert "No settled spending in January 2026" in html

    def test_spending_tab_empty_state_no_account(self, app, auth_client,
                                                 seed_user):
        """No active checking account renders the empty state, not a crash."""
        from unittest.mock import patch
        with app.app_context():
            with patch(
                "app.services.spending_report_service.resolve_analytics_account",
                return_value=None,
            ):
                resp = auth_client.get(
                    "/analytics/spending",
                    headers={"HX-Request": "true"},
                )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "No active checking account" in html
            assert "Set up an account" in html

    def test_spending_tab_default_is_prior_month(self, app, auth_client,
                                                 seed_user, seed_periods):
        """With no month param, the default window is the prior completed month.

        Today is frozen 2026-03-20, so the default is February 2026.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/spending",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert "February 2026" in resp.data.decode()

    def test_spending_tab_non_htmx_redirects(self, app, auth_client, seed_user):
        """A non-HTMX GET redirects to the analytics page."""
        with app.app_context():
            resp = auth_client.get("/analytics/spending")
            assert resp.status_code == 302
            assert "/analytics" in resp.headers["Location"]
