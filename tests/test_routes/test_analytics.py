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
        """C15-5: Response contains canvas with chart data attributes."""
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

            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=seed_periods[0].id,
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
