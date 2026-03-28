"""
Shekel Budget App -- Charts Route Tests

Tests for the Charts dashboard and HTMX fragment endpoints:
  - Authentication required
  - Dashboard page renders with chart cards
  - Fragment endpoints return HTML fragments with HX-Request header
  - Fragment endpoints redirect without HX-Request header
  - Empty data edge cases
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.auto_loan_params import AutoLoanParams
from app.models.category import Category
from app.models.mortgage_params import MortgageParams
from app.models.pay_period import PayPeriod
from app.models.ref import AccountType, Status, TransactionType
from app.models.scenario import Scenario
from app.models.transaction import Transaction


# ── Auth Tests ──────────────────────────────────────────────────────


class TestChartsAuth:
    """Tests for authentication on charts endpoints."""

    def test_charts_page_requires_auth(self, app, client):
        """GET /charts redirects unauthenticated users to login."""
        with app.app_context():
            resp = client.get("/charts")
            assert resp.status_code == 302
            assert "/login" in resp.headers["Location"]

    def test_fragment_requires_auth(self, app, client):
        """Fragment endpoints redirect unauthenticated users to login."""
        with app.app_context():
            resp = client.get(
                "/charts/balance-over-time",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 302
            assert "/login" in resp.headers["Location"]


# ── Dashboard Tests ─────────────────────────────────────────────────


class TestChartsDashboard:
    """Tests for GET /charts dashboard page."""

    def test_charts_page_renders(self, app, auth_client, seed_user):
        """GET /charts returns 200 with expected chart card containers."""
        with app.app_context():
            resp = auth_client.get("/charts")
            assert resp.status_code == 200
            assert b"Charts" in resp.data
            assert b"Balance Over Time" in resp.data
            assert b"Spending by Category" in resp.data
            assert b"Budget vs. Actuals" in resp.data
            assert b"Amortization Breakdown" in resp.data
            assert b"Net Worth Over Time" in resp.data
            assert b"Net Pay Trajectory" in resp.data

    def test_charts_navbar_link(self, app, auth_client, seed_user):
        """Charts link appears in the navbar."""
        with app.app_context():
            resp = auth_client.get("/charts")
            assert resp.status_code == 200
            assert b'href="/charts"' in resp.data


# ── Fragment Behavior Tests ─────────────────────────────────────────


class TestFragmentRedirects:
    """Fragment URLs without HX-Request header redirect to dashboard."""

    def test_balance_fragment_redirects(self, app, auth_client, seed_user):
        """GET /charts/balance-over-time without HTMX redirects."""
        with app.app_context():
            resp = auth_client.get("/charts/balance-over-time")
            assert resp.status_code == 302
            assert "/charts" in resp.headers["Location"]

    def test_spending_fragment_redirects(self, app, auth_client, seed_user):
        """GET /charts/spending-by-category without HTMX redirects."""
        with app.app_context():
            resp = auth_client.get("/charts/spending-by-category")
            assert resp.status_code == 302
            assert "/charts" in resp.headers["Location"]

    def test_budget_fragment_redirects(self, app, auth_client, seed_user):
        """GET /charts/budget-vs-actuals without HTMX redirects."""
        with app.app_context():
            resp = auth_client.get("/charts/budget-vs-actuals")
            assert resp.status_code == 302
            assert "/charts" in resp.headers["Location"]

    def test_amortization_fragment_redirects(self, app, auth_client, seed_user):
        """GET /charts/amortization without HTMX redirects."""
        with app.app_context():
            resp = auth_client.get("/charts/amortization")
            assert resp.status_code == 302
            assert "/charts" in resp.headers["Location"]

    def test_net_worth_fragment_redirects(self, app, auth_client, seed_user):
        """GET /charts/net-worth without HTMX redirects."""
        with app.app_context():
            resp = auth_client.get("/charts/net-worth")
            assert resp.status_code == 302
            assert "/charts" in resp.headers["Location"]

    def test_net_pay_fragment_redirects(self, app, auth_client, seed_user):
        """GET /charts/net-pay without HTMX redirects."""
        with app.app_context():
            resp = auth_client.get("/charts/net-pay")
            assert resp.status_code == 302
            assert "/charts" in resp.headers["Location"]


# ── HTMX Fragment Tests ─────────────────────────────────────────────


class TestBalanceFragment:
    """Tests for GET /charts/balance-over-time with HX-Request."""

    def test_balance_fragment_empty(self, app, auth_client, seed_user):
        """Returns empty state when no periods exist."""
        with app.app_context():
            resp = auth_client.get(
                "/charts/balance-over-time",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"No balance data" in resp.data

    def test_balance_fragment_with_data(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Returns canvas element when balance data exists."""
        with app.app_context():
            resp = auth_client.get(
                "/charts/balance-over-time",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"chart-balance-over-time" in resp.data


class TestSpendingFragment:
    """Tests for GET /charts/spending-by-category with HX-Request."""

    def test_spending_fragment_empty(self, app, auth_client, seed_user):
        """Returns empty state when no expense transactions exist."""
        with app.app_context():
            resp = auth_client.get(
                "/charts/spending-by-category",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"No spending data" in resp.data

    def test_spending_fragment_with_data(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Returns canvas when expense transactions exist."""
        with app.app_context():
            # Create an expense transaction in a recent period.
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="expense")
                .one()
            )
            done_status = (
                db.session.query(Status).filter_by(name="Paid").one()
            )
            # Use a period that falls within the last_12 range.
            txn = Transaction(
                template_id=None,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                name="Rent Payment",
                estimated_amount=Decimal("1200.00"),
                actual_amount=Decimal("1200.00"),
                status_id=done_status.id,
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get(
                "/charts/spending-by-category?range=last_12",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"chart-spending-category" in resp.data

    def test_spending_fragment_period_params(
        self, app, auth_client, seed_user,
    ):
        """Query params filter data correctly."""
        with app.app_context():
            resp = auth_client.get(
                "/charts/spending-by-category?range=ytd",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            # No expense transactions seeded, so empty state is expected
            assert b"No spending data" in resp.data


class TestBudgetFragment:
    """Tests for GET /charts/budget-vs-actuals with HX-Request."""

    def test_budget_fragment_empty(self, app, auth_client, seed_user):
        """Returns empty state when no data exists."""
        with app.app_context():
            resp = auth_client.get(
                "/charts/budget-vs-actuals",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"No budget data" in resp.data

    def test_budget_fragment_with_data(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Returns canvas when transaction data exists."""
        with app.app_context():
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="expense")
                .one()
            )
            projected_status = (
                db.session.query(Status).filter_by(name="Projected").one()
            )
            txn = Transaction(
                template_id=None,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                name="Groceries",
                estimated_amount=Decimal("200.00"),
                status_id=projected_status.id,
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get(
                "/charts/budget-vs-actuals?range=last_12",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"chart-budget-vs-actuals" in resp.data


class TestAmortizationFragment:
    """Tests for GET /charts/amortization with HX-Request."""

    def test_amortization_fragment_empty(self, app, auth_client, seed_user):
        """Returns empty state when no loan accounts exist."""
        with app.app_context():
            resp = auth_client.get(
                "/charts/amortization",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"No loan accounts" in resp.data

    def test_amortization_fragment_with_mortgage(
        self, app, auth_client, seed_user,
    ):
        """Returns canvas when mortgage account is configured."""
        with app.app_context():
            mortgage_type = (
                db.session.query(AccountType)
                .filter_by(name="mortgage")
                .one()
            )
            account = Account(
                user_id=seed_user["user"].id,
                account_type_id=mortgage_type.id,
                name="Home Mortgage",
                current_anchor_balance=Decimal("250000.00"),
            )
            db.session.add(account)
            db.session.flush()

            params = MortgageParams(
                account_id=account.id,
                original_principal=Decimal("300000.00"),
                current_principal=Decimal("250000.00"),
                interest_rate=Decimal("0.06500"),
                term_months=360,
                origination_date=date(2023, 1, 1),
                payment_day=1,
            )
            db.session.add(params)
            db.session.commit()

            resp = auth_client.get(
                "/charts/amortization",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"chart-amortization" in resp.data


class TestNetWorthFragment:
    """Tests for GET /charts/net-worth with HX-Request."""

    def test_net_worth_fragment_empty(self, app, auth_client, seed_user):
        """Returns empty state when no data available."""
        with app.app_context():
            resp = auth_client.get(
                "/charts/net-worth",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"No account data" in resp.data

    def test_net_worth_fragment_with_data(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Returns canvas when balance data exists."""
        with app.app_context():
            resp = auth_client.get(
                "/charts/net-worth",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"chart-net-worth" in resp.data


class TestNetPayFragment:
    """Tests for GET /charts/net-pay with HX-Request."""

    def test_net_pay_fragment_empty(self, app, auth_client, seed_user):
        """Returns empty state when no salary profiles exist."""
        with app.app_context():
            resp = auth_client.get(
                "/charts/net-pay",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"No salary profiles" in resp.data


# ── Negative Path Tests ─────────────────────────────────────────────


class TestChartNegativePaths:
    """Tests for chart edge cases, invalid params, and error handling."""

    def test_spending_fragment_invalid_range_param(self, app, auth_client, seed_user):
        """Invalid range param handled gracefully (no crash) by spending fragment."""
        with app.app_context():
            resp = auth_client.get(
                "/charts/spending-by-category?range=invalid_value",
                headers={"HX-Request": "true"},
            )
            # Route passes unknown range to service; the broad except
            # catches any error and returns _error_fragment(), or
            # the service handles it gracefully and returns empty data.
            assert resp.status_code == 200
            assert (
                b"No spending data" in resp.data
                or b"Failed to load chart data" in resp.data
                or b"chart-spending-category" in resp.data
            )

    def test_balance_fragment_with_nonexistent_account_id(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Nonexistent account_id handled gracefully by balance fragment."""
        with app.app_context():
            resp = auth_client.get(
                "/charts/balance-over-time?account_id=999999",
                headers={"HX-Request": "true"},
            )
            # Service filters by user_id, so nonexistent account
            # returns empty data or is ignored. No crash.
            assert resp.status_code == 200
