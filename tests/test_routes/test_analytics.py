"""
Shekel Budget App -- Analytics Route Tests

Tests for the analytics page shell and HTMX tab endpoints:
  - Authentication required for all endpoints
  - Main page renders with nav-pills and tab-content div
  - Tab endpoints return placeholders/content with HX-Request header
  - Tab endpoints redirect without HX-Request header
  - Nav bar shows Analytics link with correct active state
  - Charts route still functions after nav rename
  - Year-end tab renders income/tax, spending, net worth, debt, savings
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


def _seed_multi_month_expenses(db, seed_user, periods, num_months):
    """Create paid expenses spread across num_months distinct months.

    Distributes one expense per month, attributed by due_date.
    """
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    paid_status_id = ref_cache.status_id(StatusEnum.DONE)
    cat = seed_user["categories"]["Rent"]
    months_seeded = set()
    for p in periods:
        month_key = (p.start_date.year, p.start_date.month)
        if month_key in months_seeded:
            continue
        if len(months_seeded) >= num_months:
            break
        txn = Transaction(
            account_id=seed_user["account"].id,
            scenario_id=seed_user["scenario"].id,
            pay_period_id=p.id,
            status_id=paid_status_id,
            transaction_type_id=expense_type_id,
            name=f"Rent {p.start_date.strftime('%b %Y')}",
            estimated_amount=Decimal("1200.00"),
            actual_amount=Decimal("1200.00"),
            category_id=cat.id,
            due_date=p.start_date,
        )
        db.session.add(txn)
        months_seeded.add(month_key)
    db.session.commit()


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
        """All six tab endpoints redirect unauthenticated users to login."""
        tab_urls = [
            "/analytics/calendar",
            "/analytics/year-end",
            "/analytics/variance",
            "/analytics/trends",
            "/analytics/income-statement",
            "/analytics/balance-sheet",
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

    def test_analytics_page_has_six_pills(self, app, auth_client, seed_user):
        """GET /analytics includes all six nav-pill button labels.

        Build-Order Step 5 added the Income Statement and Balance Sheet
        tabs (the confirmed-ledger statements) alongside the original four.
        Slice 2 of the analytics rebuild (Gate A ruling 4, locked
        2026-07-04) replaced the Year-End pill with Taxes; the
        /analytics/year-end ROUTE stays alive until the slice-4 shell
        collapse retires it with redirects.
        """
        with app.app_context():
            resp = auth_client.get("/analytics")
            assert resp.status_code == 200
            html = resp.data
            assert b"Calendar" in html
            assert b"Taxes" in html
            assert b"Year-End" not in html
            assert b"Variance" in html
            assert b"Trends" in html
            assert b"Income Statement" in html
            assert b"Balance Sheet" in html

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
        """Only the Calendar pill auto-loads; the other five load on click."""
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


class TestYearEndTab:
    """Tests for GET /analytics/year-end HTMX partial endpoint."""

    def test_year_end_tab_renders(self, app, auth_client, seed_user, seed_periods):
        """C14-1: Year-end tab renders with heading."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/year-end",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"Year-End Summary" in resp.data

    def test_year_end_year_parameter(self, app, auth_client, seed_user, seed_periods):
        """C14-2: Year parameter controls which year is displayed."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/year-end?year=2026",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"2026" in resp.data

    def test_year_end_income_section(self, app, auth_client, seed_user,
                                     seed_periods, db, seed_full_user_data):
        """C14-3: Income section shows Gross Wages with dollar amount."""
        with app.app_context():
            from app.models.ref import FilingStatus, TaxType
            from app.models.tax_config import (
                FicaConfig, StateTaxConfig, TaxBracket, TaxBracketSet,
            )
            user = seed_full_user_data["user"]
            profile = seed_full_user_data["salary_profile"]

            # Seed tax configs for 2026.
            bs = TaxBracketSet(
                user_id=user.id,
                filing_status_id=profile.filing_status_id,
                tax_year=2026,
                standard_deduction=Decimal("15000.00"),
                child_credit_amount=Decimal("2000.00"),
                other_dependent_credit_amount=Decimal("500.00"),
            )
            db.session.add(bs)
            db.session.flush()
            db.session.add(TaxBracket(
                bracket_set_id=bs.id,
                min_income=Decimal("0"), max_income=Decimal("50000"),
                rate=Decimal("0.1000"), sort_order=0,
            ))
            db.session.add(TaxBracket(
                bracket_set_id=bs.id,
                min_income=Decimal("50000"), max_income=None,
                rate=Decimal("0.2200"), sort_order=1,
            ))
            flat_type = db.session.query(TaxType).filter_by(name="flat").one()
            db.session.add(StateTaxConfig(
                user_id=user.id, tax_type_id=flat_type.id,
                state_code="NC", tax_year=2026,
                flat_rate=Decimal("0.0450"),
            ))
            db.session.add(FicaConfig(
                user_id=user.id, tax_year=2026,
                ss_rate=Decimal("0.0620"),
                ss_wage_base=Decimal("168600.00"),
                medicare_rate=Decimal("0.0145"),
                medicare_surtax_rate=Decimal("0.0090"),
                medicare_surtax_threshold=Decimal("200000.00"),
            ))
            db.session.commit()

            resp = auth_client.get(
                "/analytics/year-end?year=2026",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Gross Wages" in html
            assert "$" in html

    def test_year_end_spending_section(self, app, auth_client, seed_user,
                                       seed_periods, db):
        """C14-4: Spending section shows category group name."""
        with app.app_context():
            from app import ref_cache
            from app.enums import StatusEnum, TxnTypeEnum
            from app.models.transaction import Transaction

            _create_paid_expense_for_route_test(
                db, seed_user, seed_periods,
                "Rent Payment", Decimal("1200.00"), "Rent",
            )

            resp = auth_client.get(
                "/analytics/year-end?year=2026",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"Home" in resp.data

    def test_year_end_net_worth_chart(self, app, auth_client, seed_user,
                                      seed_periods):
        """C14-5: Net worth section contains canvas with data attributes."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/year-end?year=2026",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "<canvas" in html
            assert "data-labels" in html
            assert "data-data" in html

    def test_year_end_empty_year(self, app, auth_client, seed_user,
                                 seed_periods):
        """C14-6: Year with no data shows empty state message."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/year-end?year=2020",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"No financial data for 2020" in resp.data

    def test_year_end_requires_auth(self, app, client):
        """C14-extra1: Unauthenticated request redirects to login."""
        with app.app_context():
            resp = client.get("/analytics/year-end")
            assert resp.status_code == 302
            assert "/login" in resp.headers["Location"]

    def test_year_end_no_htmx_redirects(self, app, auth_client, seed_user):
        """C14-extra2: Non-HTMX request redirects to /analytics."""
        with app.app_context():
            resp = auth_client.get("/analytics/year-end")
            assert resp.status_code == 302
            assert "/analytics" in resp.headers["Location"]

    def test_year_end_has_year_selector(self, app, auth_client, seed_user,
                                        seed_periods):
        """C14-extra3: Response contains a year selector element."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/year-end",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"<select" in resp.data

    def test_year_end_tax_items_present(self, app, auth_client, seed_user,
                                        seed_periods, db, seed_full_user_data):
        """C14-extra4: Tax items Federal, State, Social Security, Medicare shown."""
        with app.app_context():
            from app.models.ref import TaxType
            from app.models.tax_config import (
                FicaConfig, StateTaxConfig, TaxBracket, TaxBracketSet,
            )
            user = seed_full_user_data["user"]
            profile = seed_full_user_data["salary_profile"]

            bs = TaxBracketSet(
                user_id=user.id,
                filing_status_id=profile.filing_status_id,
                tax_year=2026,
                standard_deduction=Decimal("15000.00"),
                child_credit_amount=Decimal("2000.00"),
                other_dependent_credit_amount=Decimal("500.00"),
            )
            db.session.add(bs)
            db.session.flush()
            db.session.add(TaxBracket(
                bracket_set_id=bs.id,
                min_income=Decimal("0"), max_income=Decimal("50000"),
                rate=Decimal("0.1000"), sort_order=0,
            ))
            flat_type = db.session.query(TaxType).filter_by(name="flat").one()
            db.session.add(StateTaxConfig(
                user_id=user.id, tax_type_id=flat_type.id,
                state_code="NC", tax_year=2026,
                flat_rate=Decimal("0.0450"),
            ))
            db.session.add(FicaConfig(
                user_id=user.id, tax_year=2026,
                ss_rate=Decimal("0.0620"),
                ss_wage_base=Decimal("168600.00"),
                medicare_rate=Decimal("0.0145"),
                medicare_surtax_rate=Decimal("0.0090"),
                medicare_surtax_threshold=Decimal("200000.00"),
            ))
            db.session.commit()

            resp = auth_client.get(
                "/analytics/year-end?year=2026",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "Federal" in html
            assert "State" in html
            assert "Social Security" in html
            assert "Medicare" in html

    def test_year_end_mortgage_interest_shown(self, app, auth_client,
                                              seed_user, seed_periods, db):
        """C14-extra5: Mortgage interest line shown when > 0."""
        with app.app_context():
            from app.models.account import Account
            from app.models.loan_params import LoanParams
            from app.models.ref import AccountType

            mortgage_type = db.session.query(AccountType).filter_by(
                name="Mortgage",
            ).one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=mortgage_type.id,
                    name="Mortgage",
                    anchor_balance=Decimal("240000.00"),
                    anchor_period_id=seed_periods[0].id,
                ),
            )
            db.session.add(acct)
            db.session.flush()
            lp = LoanParams(
                account_id=acct.id,
                original_principal=Decimal("240000.00"),
                current_principal=Decimal("240000.00"),
                term_months=360,
                origination_date=date(2025, 1, 1),
                payment_day=1,
            )
            db.session.add(lp)
            db.session.flush()
            from tests._test_helpers import (  # pylint: disable=import-outside-toplevel
                insert_origination_event,
                insert_origination_rate,
            )
            insert_origination_rate(lp, Decimal("0.06500"))
            insert_origination_event(lp)
            db.session.commit()

            resp = auth_client.get(
                "/analytics/year-end?year=2026",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "Mortgage Interest" in html or "Schedule A" in html

    def test_year_end_mortgage_interest_hidden_when_zero(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """C14-extra6: Mortgage interest line hidden when no mortgage."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/year-end?year=2026",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "Schedule A" not in html

    def test_year_end_spending_accordion(self, app, auth_client, seed_user,
                                         seed_periods, db):
        """C14-extra7: Spending section uses Bootstrap accordion."""
        with app.app_context():
            _create_paid_expense_for_route_test(
                db, seed_user, seed_periods,
                "Groceries", Decimal("150.00"), "Groceries",
            )

            resp = auth_client.get(
                "/analytics/year-end?year=2026",
                headers={"HX-Request": "true"},
            )
            assert b"accordion" in resp.data

    def test_year_end_transfers_section(self, app, auth_client, seed_user,
                                        seed_periods, db):
        """C14-extra8: Transfers section shows destination account name."""
        with app.app_context():
            from app.models.account import Account
            from app.models.ref import AccountType
            from app.models.transfer import Transfer
            from app import ref_cache
            from app.enums import StatusEnum

            savings_type = db.session.query(AccountType).filter_by(
                name="Savings",
            ).one()
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Emergency Fund",
                    anchor_balance=Decimal("0"),
                    anchor_period_id=seed_periods[0].id,
                ),
            )
            db.session.add(savings)
            db.session.flush()

            transfer = Transfer(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                name="Save",
                amount=Decimal("500.00"),
            )
            db.session.add(transfer)
            db.session.commit()

            resp = auth_client.get(
                "/analytics/year-end?year=2026",
                headers={"HX-Request": "true"},
            )
            assert b"Emergency Fund" in resp.data

    def test_year_end_debt_progress_shown(self, app, auth_client, seed_user,
                                          seed_periods, db):
        """C14-extra9: Debt progress section shown with mortgage account."""
        with app.app_context():
            from app.models.account import Account
            from app.models.loan_params import LoanParams
            from app.models.ref import AccountType

            mortgage_type = db.session.query(AccountType).filter_by(
                name="Mortgage",
            ).one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=mortgage_type.id,
                    name="My Mortgage",
                    anchor_balance=Decimal("200000.00"),
                    anchor_period_id=seed_periods[0].id,
                ),
            )
            db.session.add(acct)
            db.session.flush()
            lp = LoanParams(
                account_id=acct.id,
                original_principal=Decimal("200000.00"),
                current_principal=Decimal("200000.00"),
                term_months=360,
                origination_date=date(2025, 1, 1),
                payment_day=1,
            )
            db.session.add(lp)
            db.session.flush()
            from tests._test_helpers import (  # pylint: disable=import-outside-toplevel
                insert_origination_event,
                insert_origination_rate,
            )
            insert_origination_rate(lp, Decimal("0.05000"))
            insert_origination_event(lp)
            db.session.commit()

            resp = auth_client.get(
                "/analytics/year-end?year=2026",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "My Mortgage" in html
            assert "Principal Paid" in html

    def test_year_end_debt_hidden_when_none(self, app, auth_client,
                                            seed_user, seed_periods):
        """C14-extra10: No debt section when no debt accounts."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/year-end?year=2026",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "Debt Progress" not in html

    def test_year_end_savings_progress(self, app, auth_client,
                                       seed_full_user_data, seed_periods):
        """C14-extra11: Savings progress section shows savings account."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/year-end?year=2026",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"Savings" in resp.data

    def test_year_end_net_worth_delta_displayed(self, app, auth_client,
                                                seed_user, seed_periods):
        """C14-extra12: Net worth delta value is displayed."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/year-end?year=2026",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "Change" in html
            assert "Jan 1" in html
            assert "Dec 31" in html

    def test_year_end_amounts_formatted(self, app, auth_client, seed_user,
                                        seed_periods):
        """C14-extra13: Monetary amounts contain dollar sign and comma separators."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/year-end?year=2026",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            # The checking account has $1,000 balance which should appear
            # formatted in the net worth section.
            assert "$" in html
            assert "1,000.00" in html

    def test_year_end_net_pay_displayed(self, app, auth_client,
                                        seed_full_user_data, seed_periods,
                                        db):
        """C14-extra14: Net Pay line shown with salary data."""
        with app.app_context():
            from app.models.ref import TaxType
            from app.models.tax_config import (
                FicaConfig, StateTaxConfig, TaxBracket, TaxBracketSet,
            )
            user = seed_full_user_data["user"]
            profile = seed_full_user_data["salary_profile"]

            bs = TaxBracketSet(
                user_id=user.id,
                filing_status_id=profile.filing_status_id,
                tax_year=2026,
                standard_deduction=Decimal("15000.00"),
                child_credit_amount=Decimal("2000.00"),
                other_dependent_credit_amount=Decimal("500.00"),
            )
            db.session.add(bs)
            db.session.flush()
            db.session.add(TaxBracket(
                bracket_set_id=bs.id,
                min_income=Decimal("0"), max_income=Decimal("50000"),
                rate=Decimal("0.1000"), sort_order=0,
            ))
            flat_type = db.session.query(TaxType).filter_by(name="flat").one()
            db.session.add(StateTaxConfig(
                user_id=user.id, tax_type_id=flat_type.id,
                state_code="NC", tax_year=2026,
                flat_rate=Decimal("0.0450"),
            ))
            db.session.add(FicaConfig(
                user_id=user.id, tax_year=2026,
                ss_rate=Decimal("0.0620"),
                ss_wage_base=Decimal("168600.00"),
                medicare_rate=Decimal("0.0145"),
                medicare_surtax_rate=Decimal("0.0090"),
                medicare_surtax_threshold=Decimal("200000.00"),
            ))
            db.session.commit()

            resp = auth_client.get(
                "/analytics/year-end?year=2026",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "Net Pay" in html

    def test_year_end_payment_timeliness_if_present(
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """C14-extra15: Payment timeliness shown when bills have paid_at and due_date."""
        with app.app_context():
            from app import ref_cache
            from app.enums import StatusEnum, TxnTypeEnum
            from app.models.transaction import Transaction

            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                name="Electric Bill",
                estimated_amount=Decimal("150.00"),
                actual_amount=Decimal("150.00"),
                due_date=date(2026, 1, 15),
                paid_at=datetime(2026, 1, 10, tzinfo=timezone.utc),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get(
                "/analytics/year-end?year=2026",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "On Time" in html or "Bills Paid" in html


class TestVarianceTab:
    """Tests for GET /analytics/variance HTMX partial endpoint."""

    def test_variance_tab_renders(self, app, auth_client, seed_user,
                                   seed_periods):
        """C15-1: Variance tab renders with heading."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/variance",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"Budget Variance" in resp.data

    def test_variance_pay_period_default(self, app, auth_client, seed_user,
                                          seed_periods, db):
        """C15-2: Default pay_period window shows period label."""
        with app.app_context():
            _create_paid_expense_for_route_test(
                db, seed_user, seed_periods,
                "Rent", Decimal("1000.00"), "Rent",
            )
            resp = auth_client.get(
                "/analytics/variance?window=pay_period",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            # Should contain the period date range.
            assert b"Jan" in resp.data

    def test_variance_monthly_window(self, app, auth_client, seed_user,
                                      seed_periods):
        """C15-3: Monthly window contains month name and year."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/variance?window=month&month=1&year=2026",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"January" in resp.data
            assert b"2026" in resp.data

    def test_variance_annual_window(self, app, auth_client, seed_user,
                                     seed_periods):
        """C15-4: Annual window contains the year."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/variance?window=year&year=2026",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"2026" in resp.data

    def test_variance_out_of_range_month_clamped(self, app, auth_client,
                                                 seed_user, seed_periods):
        """A hand-crafted out-of-range month/year clamps instead of 500ing.

        The shared ``_resolve_window_params`` clamps month to [1,12] and
        year to [2000,2100] (the ``calendar_tab`` / ``year_end_tab``
        convention) so a crafted ``?window=month&month=13&year=99999`` URL
        cannot reach ``date()`` / ``calendar.monthrange()`` in the variance
        service and raise.  The 200 proves the month clamp (``monthrange``
        would raise on month=13); the ``2100`` label (not offered by the
        bounded year dropdown, which tops out at the current year) proves
        the year clamp.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/variance?window=month&month=13&year=99999",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"2100" in resp.data

    def test_variance_requires_auth(self, app, client):
        """C15-extra1: Unauthenticated request redirects to login."""
        with app.app_context():
            resp = client.get("/analytics/variance")
            assert resp.status_code == 302
            assert "/login" in resp.headers["Location"]

    def test_variance_no_htmx_redirects(self, app, auth_client, seed_user):
        """C15-extra2: Non-HTMX request redirects to /analytics."""
        with app.app_context():
            resp = auth_client.get("/analytics/variance")
            assert resp.status_code == 302
            assert "/analytics" in resp.headers["Location"]

    def test_variance_chart_present(self, app, auth_client, seed_user,
                                     seed_periods, db):
        """C15-5: Response contains canvas with chart data attributes.

        Includes C31 (JN-03): ``data-variance`` must be emitted so the
        chart tooltip renders the server-computed variance instead of
        recomputing ``actual - estimated`` client-side.
        """
        with app.app_context():
            _create_paid_expense_for_route_test(
                db, seed_user, seed_periods,
                "Groceries", Decimal("200.00"), "Groceries",
            )
            resp = auth_client.get(
                "/analytics/variance?window=pay_period"
                f"&period_id={seed_periods[0].id}",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "<canvas" in html
            assert "data-labels" in html
            assert "data-estimated" in html
            assert "data-actual" in html
            assert "data-variance" in html

    def test_variance_chart_data_matches_report(self, app, auth_client,
                                                 seed_user, seed_periods, db):
        """C15-extra3: Chart data-labels contains expected category names."""
        with app.app_context():
            _create_paid_expense_for_route_test(
                db, seed_user, seed_periods,
                "Groceries", Decimal("150.00"), "Groceries",
            )
            resp = auth_client.get(
                "/analytics/variance?window=pay_period"
                f"&period_id={seed_periods[0].id}",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "Family" in html  # group_name for Groceries

    def test_variance_table_has_categories(self, app, auth_client,
                                            seed_user, seed_periods, db):
        """C15-extra4: Table shows both category group names."""
        with app.app_context():
            _create_paid_expense_for_route_test(
                db, seed_user, seed_periods,
                "Rent", Decimal("1200.00"), "Rent",
            )
            _create_paid_expense_for_route_test(
                db, seed_user, seed_periods,
                "Groceries", Decimal("100.00"), "Groceries",
            )
            resp = auth_client.get(
                "/analytics/variance?window=pay_period"
                f"&period_id={seed_periods[0].id}",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "Home" in html
            assert "Family" in html

    def test_variance_table_amounts_present(self, app, auth_client,
                                             seed_user, seed_periods, db):
        """C15-extra5: Estimated and actual amounts visible in table."""
        with app.app_context():
            _create_paid_expense_for_route_test(
                db, seed_user, seed_periods,
                "Rent", Decimal("1200.00"), "Rent",
            )
            resp = auth_client.get(
                "/analytics/variance?window=pay_period"
                f"&period_id={seed_periods[0].id}",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "1,200.00" in html

    def test_variance_over_budget_colored(self, app, auth_client,
                                           seed_user, seed_periods, db):
        """C15-extra6: Over-budget row has variance-over class."""
        with app.app_context():
            # Create a txn where actual > estimated (over budget).
            expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
            paid_status_id = ref_cache.status_id(StatusEnum.DONE)
            cat = seed_user["categories"]["Rent"]
            txn = Transaction(
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                pay_period_id=seed_periods[0].id,
                status_id=paid_status_id,
                transaction_type_id=expense_type_id,
                name="Rent Over",
                estimated_amount=Decimal("1000.00"),
                actual_amount=Decimal("1200.00"),
                category_id=cat.id,
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get(
                "/analytics/variance?window=pay_period"
                f"&period_id={seed_periods[0].id}",
                headers={"HX-Request": "true"},
            )
            assert b"variance-over" in resp.data

    def test_variance_under_budget_colored(self, app, auth_client,
                                            seed_user, seed_periods, db):
        """C15-extra7: Under-budget row has variance-under class."""
        with app.app_context():
            # Create a txn where actual < estimated (under budget).
            expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
            paid_status_id = ref_cache.status_id(StatusEnum.DONE)
            cat = seed_user["categories"]["Rent"]
            txn = Transaction(
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                pay_period_id=seed_periods[0].id,
                status_id=paid_status_id,
                transaction_type_id=expense_type_id,
                name="Rent Under",
                estimated_amount=Decimal("1200.00"),
                actual_amount=Decimal("1000.00"),
                category_id=cat.id,
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get(
                "/analytics/variance?window=pay_period"
                f"&period_id={seed_periods[0].id}",
                headers={"HX-Request": "true"},
            )
            assert b"variance-under" in resp.data

    def test_variance_totals_row(self, app, auth_client, seed_user,
                                  seed_periods, db):
        """C15-extra8: Total row shows summed estimated and actual."""
        with app.app_context():
            _create_paid_expense_for_route_test(
                db, seed_user, seed_periods,
                "Rent", Decimal("1200.00"), "Rent",
            )
            resp = auth_client.get(
                "/analytics/variance?window=pay_period"
                f"&period_id={seed_periods[0].id}",
                headers={"HX-Request": "true"},
            )
            assert b"Total" in resp.data

    def test_variance_detail_drilldown(self, app, auth_client, seed_user,
                                       seed_periods, db):
        """C15-6: Drill-down shows transaction names (collapse present)."""
        with app.app_context():
            _create_paid_expense_for_route_test(
                db, seed_user, seed_periods,
                "January Rent", Decimal("1200.00"), "Rent",
            )
            resp = auth_client.get(
                "/analytics/variance?window=pay_period"
                f"&period_id={seed_periods[0].id}",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            # Transaction name should be in the collapsed section.
            assert "January Rent" in html

    def test_variance_detail_shows_transactions(self, app, auth_client,
                                                 seed_user, seed_periods, db):
        """C15-extra9: All transaction names visible in drill-down."""
        with app.app_context():
            for name in ["Rent A", "Rent B", "Rent C"]:
                _create_paid_expense_for_route_test(
                    db, seed_user, seed_periods,
                    name, Decimal("400.00"), "Rent",
                )
            resp = auth_client.get(
                "/analytics/variance?window=pay_period"
                f"&period_id={seed_periods[0].id}",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "Rent A" in html
            assert "Rent B" in html
            assert "Rent C" in html

    def test_variance_detail_shows_paid_status(self, app, auth_client,
                                                seed_user, seed_periods, db):
        """C15-extra10: Paid indicator shown on settled transactions."""
        with app.app_context():
            _create_paid_expense_for_route_test(
                db, seed_user, seed_periods,
                "Paid Bill", Decimal("500.00"), "Rent",
            )
            resp = auth_client.get(
                "/analytics/variance?window=pay_period"
                f"&period_id={seed_periods[0].id}",
                headers={"HX-Request": "true"},
            )
            assert b"Paid" in resp.data

    def test_variance_window_toggle_buttons(self, app, auth_client,
                                             seed_user, seed_periods):
        """C15-extra11: Response contains buttons for all three windows."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/variance",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "Pay Period" in html
            assert "Month" in html
            assert "Year" in html

    def test_variance_active_window_highlighted(self, app, auth_client,
                                                 seed_user, seed_periods):
        """C15-extra12: Active window button has primary class."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/variance?window=month&month=1&year=2026",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            # The Month button should have btn-primary class.
            assert 'btn-primary' in html

    def test_variance_period_selector_present(self, app, auth_client,
                                               seed_user, seed_periods):
        """C15-extra13: Period selector with period labels shown."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/variance?window=pay_period",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "<select" in html
            # Period dates should appear in the selector.
            assert "Jan 02" in html

    def test_variance_empty_period(self, app, auth_client, seed_user,
                                    seed_periods):
        """C15-extra14: Period with no transactions shows empty message."""
        with app.app_context():
            # Use a period with no transactions.
            resp = auth_client.get(
                "/analytics/variance?window=pay_period"
                f"&period_id={seed_periods[5].id}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"No transactions in this period" in resp.data

    def test_variance_no_current_period(self, app, auth_client, seed_user):
        """C15-extra15: No periods at all -- graceful handling."""
        with app.app_context():
            # seed_user has no periods (seed_periods not used).
            resp = auth_client.get(
                "/analytics/variance?window=pay_period",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"No transactions in this period" in resp.data

    def test_variance_show_variances_toggle(self, app, auth_client,
                                             seed_user, seed_periods, db):
        """C15-extra16: Toggle element present in response."""
        with app.app_context():
            _create_paid_expense_for_route_test(
                db, seed_user, seed_periods,
                "Rent", Decimal("1200.00"), "Rent",
            )
            resp = auth_client.get(
                "/analytics/variance?window=pay_period"
                f"&period_id={seed_periods[0].id}",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "Show only variances" in html
            assert "variance-filter-toggle" in html


class TestTrendsTab:
    """Tests for GET /analytics/trends HTMX partial endpoint."""

    def test_trends_tab_renders(self, app, auth_client, seed_user,
                                 seed_periods):
        """C16-1: Trends tab renders with heading."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/trends",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"Spending Trends" in resp.data

    def test_trends_requires_auth(self, app, client):
        """C16-extra1: Unauthenticated request redirects to login."""
        with app.app_context():
            resp = client.get("/analytics/trends")
            assert resp.status_code == 302
            assert "/login" in resp.headers["Location"]

    def test_trends_no_htmx_redirects(self, app, auth_client, seed_user):
        """C16-extra2: Non-HTMX request redirects to /analytics."""
        with app.app_context():
            resp = auth_client.get("/analytics/trends")
            assert resp.status_code == 302
            assert "/analytics" in resp.headers["Location"]

    def test_trends_insufficient_banner(self, app, auth_client, seed_user,
                                         seed_periods, db):
        """C16-2: < 3 months of paid data shows insufficient banner."""
        with app.app_context():
            # Create paid expense in only 1 month.
            _create_paid_expense_for_route_test(
                db, seed_user, seed_periods,
                "Single Month Expense", Decimal("100.00"), "Rent",
            )
            resp = auth_client.get(
                "/analytics/trends",
                headers={"HX-Request": "true"},
            )
            assert b"Not enough data" in resp.data

    def test_trends_preliminary_banner(self, app, auth_client, seed_user,
                                       seed_periods, db):
        """C16-3: 3-5 months of data shows preliminary banner."""
        with app.app_context():
            # Create paid expenses in 3 distinct months.
            _seed_multi_month_expenses(db, seed_user, seed_periods, 3)

            resp = auth_client.get(
                "/analytics/trends",
                headers={"HX-Request": "true"},
            )
            assert b"preliminary" in resp.data

    def test_trends_sufficient_no_banner(self, app, auth_client, seed_user,
                                          db):
        """C16-extra3: 6+ months of data shows no banner."""
        with app.app_context():
            periods = _seed_long_periods(db, seed_user, 26)
            _seed_multi_month_expenses(db, seed_user, periods, 6)

            resp = auth_client.get(
                "/analytics/trends",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "preliminary" not in html
            assert "Not enough data" not in html

    def test_trends_insufficient_hides_lists(self, app, auth_client,
                                              seed_user, seed_periods, db):
        """C16-extra4: Insufficient data hides trend lists."""
        with app.app_context():
            _create_paid_expense_for_route_test(
                db, seed_user, seed_periods,
                "One Expense", Decimal("50.00"), "Rent",
            )
            resp = auth_client.get(
                "/analytics/trends",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "Trending Up" not in html
            assert "Trending Down" not in html

    def test_trends_up_list(self, app, auth_client, seed_user, db):
        """C16-4: Trending up list shows red arrow and positive pct."""
        with app.app_context():
            periods = _seed_long_periods(db, seed_user, 26)
            _seed_increasing_trend(db, seed_user, periods)

            resp = auth_client.get(
                "/analytics/trends",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "bi-arrow-up-right" in html
            assert "Trending Up" in html

    def test_trends_down_list(self, app, auth_client, seed_user, db):
        """C16-5: Trending down list shows green arrow and negative pct."""
        with app.app_context():
            periods = _seed_long_periods(db, seed_user, 26)
            _seed_decreasing_trend(db, seed_user, periods)

            resp = auth_client.get(
                "/analytics/trends",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "bi-arrow-down-right" in html
            assert "Trending Down" in html

    def test_trends_up_list_empty(self, app, auth_client, seed_user, db):
        """C16-extra5: No flagged increases shows empty message.

        Uses only decreasing-trend data so top_increasing is empty.
        """
        with app.app_context():
            periods = _seed_long_periods(db, seed_user, 26)
            _seed_decreasing_trend(db, seed_user, periods)

            resp = auth_client.get(
                "/analytics/trends",
                headers={"HX-Request": "true"},
            )
            assert b"No significant spending increases" in resp.data

    def test_trends_down_list_empty(self, app, auth_client, seed_user, db):
        """C16-extra6: No flagged decreases shows empty message.

        Uses only increasing-trend data so top_decreasing is empty.
        """
        with app.app_context():
            periods = _seed_long_periods(db, seed_user, 26)
            _seed_increasing_trend(db, seed_user, periods)

            resp = auth_client.get(
                "/analytics/trends",
                headers={"HX-Request": "true"},
            )
            assert b"No significant spending decreases" in resp.data

    def test_trends_item_shows_category_label(self, app, auth_client,
                                               seed_user, db):
        """C16-extra7: Items show 'Group: Item' format."""
        with app.app_context():
            periods = _seed_long_periods(db, seed_user, 26)
            _seed_increasing_trend(db, seed_user, periods)

            resp = auth_client.get(
                "/analytics/trends",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            # The category is "Home: Rent".
            assert "Home" in html
            assert "Rent" in html

    def test_trends_item_shows_pct_change(self, app, auth_client,
                                           seed_user, db):
        """C16-extra8: Items show percentage with % suffix."""
        with app.app_context():
            periods = _seed_long_periods(db, seed_user, 26)
            _seed_increasing_trend(db, seed_user, periods)

            resp = auth_client.get(
                "/analytics/trends",
                headers={"HX-Request": "true"},
            )
            assert b"%" in resp.data

    def test_trends_item_shows_absolute_change(self, app, auth_client,
                                                seed_user, db):
        """C16-extra9: Items show dollar change per period."""
        with app.app_context():
            periods = _seed_long_periods(db, seed_user, 26)
            _seed_increasing_trend(db, seed_user, periods)

            resp = auth_client.get(
                "/analytics/trends",
                headers={"HX-Request": "true"},
            )
            assert b"/period" in resp.data

    def test_trends_item_shows_period_average(self, app, auth_client,
                                               seed_user, db):
        """C16-extra10: Items show period average value."""
        with app.app_context():
            periods = _seed_long_periods(db, seed_user, 26)
            _seed_increasing_trend(db, seed_user, periods)

            resp = auth_client.get(
                "/analytics/trends",
                headers={"HX-Request": "true"},
            )
            assert b"Avg" in resp.data

    def test_trends_group_drilldown(self, app, auth_client, seed_user, db):
        """C16-6: Group drill-down content present (via collapse)."""
        with app.app_context():
            periods = _seed_long_periods(db, seed_user, 26)
            _seed_increasing_trend(db, seed_user, periods)

            resp = auth_client.get(
                "/analytics/trends",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "Category Groups" in html
            assert "collapse" in html

    def test_trends_group_drilldown_shows_items(self, app, auth_client,
                                                 seed_user, db):
        """C16-extra11: Group shows all items in collapsed section."""
        with app.app_context():
            periods = _seed_long_periods(db, seed_user, 26)
            _seed_increasing_trend(db, seed_user, periods)

            resp = auth_client.get(
                "/analytics/trends",
                headers={"HX-Request": "true"},
            )
            # "Rent" is the item inside "Home" group.
            assert b"Rent" in resp.data

    def test_trends_window_info_displayed(self, app, auth_client,
                                           seed_user, db):
        """C16-extra14: Window info shows months and threshold."""
        with app.app_context():
            periods = _seed_long_periods(db, seed_user, 26)
            _seed_flat_expenses(db, seed_user, periods)

            resp = auth_client.get(
                "/analytics/trends",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "month window" in html or "pay periods" in html
            assert "threshold" in html.lower() or "%" in html

    def test_trends_all_items_section(self, app, auth_client, seed_user, db):
        """C16-extra15: All items collapsible section present."""
        with app.app_context():
            periods = _seed_long_periods(db, seed_user, 26)
            _seed_flat_expenses(db, seed_user, periods)

            resp = auth_client.get(
                "/analytics/trends",
                headers={"HX-Request": "true"},
            )
            assert b"Show all categories" in resp.data

    def test_trends_all_items_flagged_indicator(self, app, auth_client,
                                                 seed_user, db):
        """C16-extra16: Flagged items have warning indicator."""
        with app.app_context():
            periods = _seed_long_periods(db, seed_user, 26)
            _seed_increasing_trend(db, seed_user, periods)

            resp = auth_client.get(
                "/analytics/trends",
                headers={"HX-Request": "true"},
            )
            assert b"bi-exclamation-triangle" in resp.data

    def test_trends_payment_timing_shown(self, app, auth_client,
                                          seed_user, db):
        """C16-op3-1: Items with avg_days_before_due show timing text."""
        with app.app_context():
            periods = _seed_long_periods(db, seed_user, 26)
            _seed_increasing_trend_with_timing(db, seed_user, periods)

            resp = auth_client.get(
                "/analytics/trends",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "days before due" in html or "days late" in html

    def test_trends_late_payment_red(self, app, auth_client, seed_user, db):
        """C16-op3-2: Late payment timing has danger styling."""
        with app.app_context():
            periods = _seed_long_periods(db, seed_user, 26)
            _seed_increasing_trend_with_late_timing(db, seed_user, periods)

            resp = auth_client.get(
                "/analytics/trends",
                headers={"HX-Request": "true"},
            )
            assert b"trend-payment-late" in resp.data


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
            # Window toggle cloned from the variance tab.
            assert "Pay Period" in html
            assert "Month" in html
            assert "Year" in html

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

        ``calendar_tab`` and ``year_end_tab`` clamp their calendar fields;
        the income statement does the same so a crafted URL cannot reach
        ``monthrange()`` / ``date()`` in the service and raise.  month=13
        clamps to 12 (December), year=99999 clamps to 2100.
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
    """Tests for CSV export on all analytics tabs."""

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

    def test_year_end_csv_export(self, app, auth_client, seed_user,
                                  seed_periods):
        """C17-3: Year-end CSV returns 200 with text/csv."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/year-end?format=csv&year=2026",
            )
            assert resp.status_code == 200
            assert "text/csv" in resp.headers["Content-Type"]

    def test_year_end_csv_sections(self, app, auth_client, seed_user,
                                    seed_periods):
        """C17-4: Year-end CSV contains section headers."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/year-end?format=csv&year=2026",
            )
            assert b"[Income and Taxes]" in resp.data

    def test_variance_csv_export(self, app, auth_client, seed_user,
                                  seed_periods, db):
        """C17-5: Variance CSV returns 200 with text/csv."""
        with app.app_context():
            _create_paid_expense_for_route_test(
                db, seed_user, seed_periods,
                "Test Expense", Decimal("500.00"), "Rent",
            )
            resp = auth_client.get(
                f"/analytics/variance?format=csv&window=pay_period"
                f"&period_id={seed_periods[0].id}",
            )
            assert resp.status_code == 200
            assert "text/csv" in resp.headers["Content-Type"]

    def test_variance_csv_hierarchy(self, app, auth_client, seed_user,
                                     seed_periods, db):
        """C17-6: Variance CSV contains Group and Transaction levels."""
        with app.app_context():
            _create_paid_expense_for_route_test(
                db, seed_user, seed_periods,
                "Rent Bill", Decimal("1200.00"), "Rent",
            )
            resp = auth_client.get(
                f"/analytics/variance?format=csv&window=pay_period"
                f"&period_id={seed_periods[0].id}",
            )
            body = resp.data.decode()
            assert "Group" in body
            assert "Transaction" in body

    def test_trends_csv_export(self, app, auth_client, seed_user, db):
        """C17-7: Trends CSV returns 200 with text/csv."""
        with app.app_context():
            periods = _seed_long_periods(db, seed_user, 26)
            _seed_flat_expenses(db, seed_user, periods)
            resp = auth_client.get("/analytics/trends?format=csv")
            assert resp.status_code == 200
            assert "text/csv" in resp.headers["Content-Type"]

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
        """C17-extra12: CSV works without HX-Request header."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/year-end?format=csv&year=2026",
            )
            # Should NOT redirect -- CSV bypasses HTMX guard.
            assert resp.status_code == 200
            assert "text/csv" in resp.headers["Content-Type"]

    def test_csv_preserves_window_params(self, app, auth_client, seed_user,
                                          seed_periods, db):
        """C17-extra13: Variance CSV with month window reflects correct data."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/variance?format=csv&window=month&month=1&year=2026",
            )
            assert resp.status_code == 200
            body = resp.data.decode()
            assert "Total" in body

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

    def test_variance_has_export_button(self, app, auth_client, seed_user,
                                         seed_periods):
        """C17-extra18: Variance tab contains CSV export link."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/variance",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "format=csv" in html

    def test_year_end_has_export_button(self, app, auth_client, seed_user,
                                         seed_periods):
        """C17-extra19: Year-end tab contains CSV export link."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/year-end",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "format=csv" in html

    def test_trends_has_export_button(self, app, auth_client, seed_user, db):
        """C17-extra20: Trends tab contains CSV export link when data sufficient."""
        with app.app_context():
            periods = _seed_long_periods(db, seed_user, 26)
            _seed_flat_expenses(db, seed_user, periods)
            resp = auth_client.get(
                "/analytics/trends",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "format=csv" in html

    # ── Statement CSV (Build-Order Step 5, C11) ──────────────────

    def test_income_statement_csv_export(self, app, auth_client, seed_user,
                                         seed_periods):
        """C11-1: Income statement CSV returns 200 with text/csv."""
        with app.app_context():
            resp = auth_client.get(
                f"/analytics/income-statement?format=csv&window=pay_period"
                f"&period_id={seed_periods[0].id}",
            )
            assert resp.status_code == 200
            assert "text/csv" in resp.headers["Content-Type"]

    def test_income_statement_csv_content(self, app, auth_client, seed_user,
                                          seed_periods, db):
        """C11-2: Income statement CSV carries settled posted content.

        A $2000 income and a $300 expense settle through the real posting
        path (a directly-inserted row never posts), so the confirmed ledger
        the export reads carries both sections and a $1700 net income.  The
        amounts render plain (no ``$`` / thousands comma) per the CSV rules.
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
                f"/analytics/income-statement?format=csv&window=pay_period"
                f"&period_id={seed_periods[0].id}",
            )
            assert resp.status_code == 200
            body = resp.data.decode()
            assert "[Income]" in body
            assert "[Expenses]" in body
            assert "2000.00" in body
            assert "300.00" in body
            assert "Net Income,1700.00" in body

    def test_income_statement_csv_filename(self, app, auth_client, seed_user,
                                           seed_periods):
        """C11-3: Income statement CSV filename reflects the pay-period window."""
        with app.app_context():
            resp = auth_client.get(
                f"/analytics/income-statement?format=csv&window=pay_period"
                f"&period_id={seed_periods[0].id}",
            )
            cd = resp.headers.get("Content-Disposition", "")
            assert "attachment" in cd
            assert "income_statement_period_" in cd
            assert ".csv" in cd

    def test_income_statement_has_export_button(self, app, auth_client,
                                                seed_user, seed_periods):
        """C11-4: Income Statement tab contains a CSV export link."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/income-statement",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "format=csv" in html
            assert "bi-download" in html

    def test_balance_sheet_csv_export(self, app, auth_client, seed_user):
        """C11-5: Balance sheet CSV returns 200 with text/csv (no HTMX)."""
        with app.app_context():
            resp = auth_client.get("/analytics/balance-sheet?format=csv")
            assert resp.status_code == 200
            assert "text/csv" in resp.headers["Content-Type"]

    def test_balance_sheet_csv_content_and_tie_out(
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """C11-6: Balance sheet CSV carries posted lines and a green tie-out.

        A $500 income settled with a ``paid_at`` inside the frozen range
        (2026-02-15) folds into the default as-of (2026-03-20): Checking
        +500 (Asset) and Retained Earnings +500 (Income closed into
        equity).  The seed opening's entry is excluded whole (its date is
        after the frozen today), so the tie-out still closes -> In Balance
        is Yes.
        """
        with app.app_context():
            create_settled_cash_transaction(
                seed_user, db.session, seed_periods[0], Decimal("500.00"),
                is_income=True, name="Paycheck",
                paid_at=datetime(2026, 2, 15, 12, tzinfo=timezone.utc),
            )
            db.session.commit()
            resp = auth_client.get("/analytics/balance-sheet?format=csv")
            assert resp.status_code == 200
            body = resp.data.decode()
            assert "[Assets]" in body
            assert "[Liabilities]" in body
            assert "[Equity]" in body
            assert "[Trial Balance]" in body
            # Pin BOTH placements exactly (not a bare "500.00", which also
            # matches Retained Earnings): the settled income lands +500 on the
            # Checking asset line and closes +500 into Retained Earnings, and
            # the seed opening is excluded whole so Checking is exactly 500.
            assert "Checking,500.00" in body
            assert "Retained Earnings,500.00" in body
            assert "In Balance,Yes" in body

    def test_balance_sheet_csv_filename(self, app, auth_client, seed_user):
        """C11-7: Balance sheet CSV filename carries the as-of date."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/balance-sheet?format=csv&as_of=2026-02-10",
            )
            cd = resp.headers.get("Content-Disposition", "")
            assert "attachment" in cd
            assert "balance_sheet_2026-02-10.csv" in cd

    def test_balance_sheet_has_export_button(self, app, auth_client, seed_user):
        """C11-8: Balance Sheet tab contains a CSV export link."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/balance-sheet",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "format=csv" in html
            assert "bi-download" in html


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
