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

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.models.transaction import Transaction


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
        """All four tab endpoints redirect unauthenticated users to login."""
        tab_urls = [
            "/analytics/calendar",
            "/analytics/year-end",
            "/analytics/variance",
            "/analytics/trends",
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

    def test_analytics_page_has_four_pills(self, app, auth_client, seed_user):
        """GET /analytics includes all four nav-pill button labels."""
        with app.app_context():
            resp = auth_client.get("/analytics")
            assert resp.status_code == 200
            html = resp.data
            assert b"Calendar" in html
            assert b"Year-End" in html
            assert b"Variance" in html
            assert b"Trends" in html

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
        """Year-End, Variance, and Trends pills do not auto-load on page visit."""
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
            acct = Account(
                user_id=seed_user["user"].id,
                account_type_id=mortgage_type.id,
                name="Mortgage",
                current_anchor_balance=Decimal("240000.00"),
                current_anchor_period_id=seed_periods[0].id,
            )
            db.session.add(acct)
            db.session.flush()
            db.session.add(LoanParams(
                account_id=acct.id,
                original_principal=Decimal("240000.00"),
                current_principal=Decimal("240000.00"),
                interest_rate=Decimal("0.06500"),
                term_months=360,
                origination_date=date(2025, 1, 1),
                payment_day=1,
            ))
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
            savings = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Emergency Fund",
                current_anchor_balance=Decimal("0"),
                current_anchor_period_id=seed_periods[0].id,
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
            acct = Account(
                user_id=seed_user["user"].id,
                account_type_id=mortgage_type.id,
                name="My Mortgage",
                current_anchor_balance=Decimal("200000.00"),
                current_anchor_period_id=seed_periods[0].id,
            )
            db.session.add(acct)
            db.session.flush()
            db.session.add(LoanParams(
                account_id=acct.id,
                original_principal=Decimal("200000.00"),
                current_principal=Decimal("200000.00"),
                interest_rate=Decimal("0.05000"),
                term_months=360,
                origination_date=date(2025, 1, 1),
                payment_day=1,
            ))
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

    def test_variance_tab_htmx(self, app, auth_client, seed_user):
        """GET /analytics/variance with HX-Request returns 200."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/variance",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200

    def test_variance_tab_no_htmx_redirects(self, app, auth_client, seed_user):
        """GET /analytics/variance without HX-Request redirects to /analytics."""
        with app.app_context():
            resp = auth_client.get("/analytics/variance")
            assert resp.status_code == 302
            assert "/analytics" in resp.headers["Location"]


class TestTrendsTab:
    """Tests for GET /analytics/trends HTMX partial endpoint."""

    def test_trends_tab_htmx(self, app, auth_client, seed_user):
        """GET /analytics/trends with HX-Request returns 200."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/trends",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200

    def test_trends_tab_no_htmx_redirects(self, app, auth_client, seed_user):
        """GET /analytics/trends without HX-Request redirects to /analytics."""
        with app.app_context():
            resp = auth_client.get("/analytics/trends")
            assert resp.status_code == 302
            assert "/analytics" in resp.headers["Location"]


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

    def test_charts_route_still_works(self, app, auth_client, seed_user):
        """GET /charts still returns 200 -- route is unlinked, not removed."""
        with app.app_context():
            resp = auth_client.get("/charts")
            assert resp.status_code == 200

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

    def test_calendar_month_totals_displayed(self, app, auth_client, seed_user, seed_periods, db):
        """Month view shows income/expense/net totals."""
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
            assert b"Income" in resp.data
            assert b"Expenses" in resp.data
            assert b"Net" in resp.data

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
            from datetime import date
            today = date.today()
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
