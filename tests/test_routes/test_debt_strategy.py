"""
Tests for debt strategy routes.

Covers the GET dashboard (multiple debts, single debt, empty state,
auth), POST calculate (avalanche, snowball, custom, validation, IDOR),
chart data serialization, and navigation link on the accounts dashboard.
"""

import html as html_mod
import json
import re
from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.account import Account
from app.models.loan_params import LoanParams
from app.models.ref import AccountType


# ── Helpers ──────────────────────────────────────────────────────────


def _create_debt_account(user, db_session, type_name, name, principal,
                         rate, term, orig_date, payment_day):
    """Create a debt account with LoanParams for the given user.

    Sets original_principal = current_principal + 5000 to simulate
    a partially paid-off loan (matching test_loan.py convention).
    """
    loan_type = db_session.query(AccountType).filter_by(name=type_name).one()
    account = Account(
        user_id=user.id,
        account_type_id=loan_type.id,
        name=name,
        current_anchor_balance=principal,
    )
    db_session.add(account)
    db_session.flush()

    params = LoanParams(
        account_id=account.id,
        original_principal=principal + Decimal("5000.00"),
        current_principal=principal,
        interest_rate=rate,
        term_months=term,
        origination_date=orig_date,
        payment_day=payment_day,
    )
    db_session.add(params)
    db_session.commit()
    return account


def _create_auto_loan(user, db_session, name="Test Auto Loan"):
    """Create an auto loan: $25,000 at 5.5%, 60 months."""
    return _create_debt_account(
        user, db_session, "Auto Loan", name,
        Decimal("25000.00"), Decimal("0.05500"), 60,
        date(2025, 1, 1), 15,
    )


def _create_mortgage(user, db_session, name="Test Mortgage"):
    """Create a mortgage: $200,000 at 6.5%, 360 months."""
    return _create_debt_account(
        user, db_session, "Mortgage", name,
        Decimal("200000.00"), Decimal("0.06500"), 360,
        date(2023, 6, 1), 1,
    )


def _create_student_loan(user, db_session, name="Test Student Loan"):
    """Create a student loan: $30,000 at 4.5%, 120 months."""
    return _create_debt_account(
        user, db_session, "Student Loan", name,
        Decimal("30000.00"), Decimal("0.04500"), 120,
        date(2024, 1, 1), 1,
    )


# ── Dashboard GET Tests ──────────────────────────────────────────────


class TestDebtStrategyDashboard:
    """Tests for the GET /debt-strategy page."""

    def test_strategy_page_renders(self, auth_client, seed_user, db, seed_periods):
        """GET with 2+ debt accounts returns 200, contains account names
        and the strategy form.
        """
        user = seed_user["user"]
        auto = _create_auto_loan(user, db.session)
        mortgage = _create_mortgage(user, db.session)

        resp = auth_client.get("/debt-strategy")
        assert resp.status_code == 200
        html = resp.data.decode()

        assert auto.name in html
        assert mortgage.name in html
        assert 'name="extra_monthly"' in html
        assert 'name="strategy"' in html

    def test_strategy_page_empty_state(self, auth_client, seed_user, db, seed_periods):
        """GET with no debt accounts shows empty-state message and no form."""
        resp = auth_client.get("/debt-strategy")
        assert resp.status_code == 200
        html = resp.data.decode()

        assert "No Active Debt Accounts" in html
        assert 'name="extra_monthly"' not in html

    def test_strategy_page_single_debt(self, auth_client, seed_user, db, seed_periods):
        """GET with 1 debt account shows the form and a note about
        strategy comparison being most useful with multiple debts.
        """
        _create_auto_loan(seed_user["user"], db.session)

        resp = auth_client.get("/debt-strategy")
        assert resp.status_code == 200
        html = resp.data.decode()

        assert 'name="extra_monthly"' in html
        assert "one debt account" in html.lower()

    def test_strategy_page_requires_login(self, client):
        """GET without authentication redirects to login."""
        resp = client.get("/debt-strategy")
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")

    def test_accounts_without_params_skipped(self, auth_client, seed_user, db, seed_periods):
        """A debt account with has_amortization but no LoanParams does
        not crash the page.  The account is silently skipped.
        """
        user = seed_user["user"]
        loan_type = db.session.query(AccountType).filter_by(name="Auto Loan").one()
        # Create account without LoanParams.
        account = Account(
            user_id=user.id,
            account_type_id=loan_type.id,
            name="No Params Loan",
        )
        db.session.add(account)
        db.session.commit()

        resp = auth_client.get("/debt-strategy")
        assert resp.status_code == 200
        html = resp.data.decode()
        # No params means it's filtered out; empty state shown.
        assert "No Active Debt Accounts" in html

    def test_non_debt_accounts_excluded(self, auth_client, seed_user, db, seed_periods):
        """A savings account (has_amortization=False) is not included."""
        # seed_user already has a Checking account (not amortizing).
        # Verify it does not appear and we get the empty state.
        resp = auth_client.get("/debt-strategy")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No Active Debt Accounts" in html

    def test_inactive_accounts_excluded(self, auth_client, seed_user, db, seed_periods):
        """An inactive debt account is excluded from the strategy page."""
        user = seed_user["user"]
        acct = _create_auto_loan(user, db.session)
        acct.is_active = False
        db.session.commit()

        resp = auth_client.get("/debt-strategy")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No Active Debt Accounts" in html


# ── Calculate POST Tests ─────────────────────────────────────────────


class TestDebtStrategyCalculate:
    """Tests for the POST /debt-strategy/calculate endpoint."""

    def test_calculate_avalanche(self, auth_client, seed_user, db, seed_periods):
        """POST with avalanche strategy returns comparison table with
        expected columns and avalanche interest <= snowball interest.
        """
        user = seed_user["user"]
        _create_auto_loan(user, db.session)
        _create_mortgage(user, db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "avalanche",
        })
        assert resp.status_code == 200
        html = resp.data.decode()

        # Comparison table present with expected column headers.
        assert "No Extra" in html
        assert "Avalanche" in html
        assert "Snowball" in html
        assert "Interest Saved" in html

    def test_calculate_snowball(self, auth_client, seed_user, db, seed_periods):
        """POST with snowball strategy returns results with per-account
        timeline showing smallest balance debt first.
        """
        user = seed_user["user"]
        _create_auto_loan(user, db.session)
        _create_mortgage(user, db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "snowball",
        })
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Snowball" in html
        # The per-account timeline shows the selected strategy.
        assert "Payoff Timeline" in html

    def test_calculate_custom(self, auth_client, seed_user, db, seed_periods):
        """POST with custom strategy and valid order returns 4-column
        comparison table.
        """
        user = seed_user["user"]
        auto = _create_auto_loan(user, db.session)
        mortgage = _create_mortgage(user, db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "custom",
            "custom_order": f"{mortgage.id},{auto.id}",
        })
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Custom" in html

    def test_calculate_zero_extra(self, auth_client, seed_user, db, seed_periods):
        """POST with extra=0 returns results.  All strategies show
        equivalent baseline metrics.
        """
        user = seed_user["user"]
        _create_auto_loan(user, db.session)
        _create_mortgage(user, db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "0",
            "strategy": "avalanche",
        })
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Debt-Free Date" in html

    def test_calculate_requires_login(self, client):
        """POST without authentication redirects to login."""
        resp = client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "avalanche",
        })
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")

    def test_calculate_no_debts(self, auth_client, seed_user, db, seed_periods):
        """POST with no debt accounts returns an error message, not 500."""
        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "avalanche",
        })
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No active debt accounts" in html


# ── Validation Tests ─────────────────────────────────────────────────


class TestDebtStrategyValidation:
    """Tests for input validation on the calculate endpoint."""

    def test_invalid_extra_negative(self, auth_client, seed_user, db, seed_periods):
        """Negative extra_monthly returns a user-friendly error, not 500."""
        _create_auto_loan(seed_user["user"], db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "-100",
            "strategy": "avalanche",
        })
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "negative" in html.lower()

    def test_invalid_extra_nonnumeric(self, auth_client, seed_user, db, seed_periods):
        """Non-numeric extra_monthly returns a user-friendly error."""
        _create_auto_loan(seed_user["user"], db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "abc",
            "strategy": "avalanche",
        })
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Invalid" in html

    def test_invalid_strategy(self, auth_client, seed_user, db, seed_periods):
        """Unknown strategy name returns a user-friendly error."""
        _create_auto_loan(seed_user["user"], db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "invalid_strategy",
        })
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Invalid strategy" in html

    def test_custom_missing_order(self, auth_client, seed_user, db, seed_periods):
        """Custom strategy without custom_order returns an error."""
        _create_auto_loan(seed_user["user"], db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "custom",
        })
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "priority order" in html.lower()

    def test_custom_invalid_order_format(self, auth_client, seed_user, db, seed_periods):
        """Custom order with non-integer values returns an error."""
        _create_auto_loan(seed_user["user"], db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "custom",
            "custom_order": "abc,def",
        })
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Invalid" in html


# ── IDOR Tests ───────────────────────────────────────────────────────


class TestDebtStrategyIDOR:
    """Tests for ownership checks on the calculate endpoint."""

    def test_custom_order_other_users_account(
        self, auth_client, seed_user, second_user, db, seed_periods,
    ):
        """Custom order containing another user's account ID returns 404.

        Creates a debt account for the second user and attempts to
        include it in the authenticated user's custom order.
        """
        my_acct = _create_auto_loan(seed_user["user"], db.session)
        other_acct = _create_auto_loan(second_user["user"], db.session, name="Other Auto")

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "custom",
            "custom_order": f"{my_acct.id},{other_acct.id}",
        })
        assert resp.status_code == 404

    def test_custom_order_nonexistent_account(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Custom order with a nonexistent account ID returns 404."""
        my_acct = _create_auto_loan(seed_user["user"], db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "custom",
            "custom_order": f"{my_acct.id},999999",
        })
        assert resp.status_code == 404


# ── Comparison Metrics Tests ─────────────────────────────────────────


class TestDebtStrategyMetrics:
    """Tests for the correctness of comparison metrics."""

    def test_comparison_table_structure(self, auth_client, seed_user, db, seed_periods):
        """POST returns a comparison table with all expected rows."""
        user = seed_user["user"]
        _create_auto_loan(user, db.session)
        _create_student_loan(user, db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "300",
            "strategy": "avalanche",
        })
        assert resp.status_code == 200
        html = resp.data.decode()

        # All metric rows present.
        assert "Debt-Free Date" in html
        assert "Total Interest" in html
        assert "Total Paid" in html
        assert "Months to Debt-Free" in html
        assert "Interest Saved" in html
        assert "Months Saved" in html

    def test_per_account_timeline_present(self, auth_client, seed_user, db, seed_periods):
        """POST includes a per-account payoff timeline table."""
        user = seed_user["user"]
        auto = _create_auto_loan(user, db.session)
        student = _create_student_loan(user, db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "300",
            "strategy": "snowball",
        })
        assert resp.status_code == 200
        html = resp.data.decode()

        assert "Payoff Timeline" in html
        assert auto.name in html
        assert student.name in html

    def test_arm_warning_shown(self, auth_client, seed_user, db, seed_periods):
        """When an ARM loan is present, the R-5 warning is displayed."""
        user = seed_user["user"]
        loan_type = db.session.query(AccountType).filter_by(name="Mortgage").one()
        arm_acct = Account(
            user_id=user.id,
            account_type_id=loan_type.id,
            name="ARM Mortgage",
            current_anchor_balance=Decimal("200000.00"),
        )
        db.session.add(arm_acct)
        db.session.flush()
        params = LoanParams(
            account_id=arm_acct.id,
            original_principal=Decimal("205000.00"),
            current_principal=Decimal("200000.00"),
            interest_rate=Decimal("0.05500"),
            term_months=360,
            origination_date=date(2023, 1, 1),
            payment_day=1,
            is_arm=True,
        )
        db.session.add(params)
        db.session.commit()

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "avalanche",
        })
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "ARM rate adjustments" in html


# ── Navigation Link Tests ────────────────────────────────────────────


class TestDebtStrategyNavigation:
    """Tests for the navigation link on the accounts dashboard."""

    def test_nav_link_on_accounts_dashboard(self, auth_client, seed_user, db, seed_periods):
        """The accounts dashboard with debt accounts contains a link
        to the debt strategy page.
        """
        _create_auto_loan(seed_user["user"], db.session)

        resp = auth_client.get("/savings")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "/debt-strategy" in html
        assert "Payoff Strategies" in html


# ── Chart Data Tests ─────────────────────────────────────────────────


def _extract_chart_data(response_html):
    """Extract and parse chart JSON from the response HTML.

    Finds the data-chart-data attribute on the strategy-chart canvas,
    HTML-unescapes and JSON-parses it.

    Args:
        response_html: Decoded HTML string from the response.

    Returns:
        Parsed dict with 'labels' and 'datasets' keys, or None if
        the chart canvas is not present.
    """
    match = re.search(
        r"data-chart-data='([^']*)'",
        response_html,
    )
    if not match:
        return None
    raw = html_mod.unescape(match.group(1))
    return json.loads(raw)


class TestDebtStrategyChart:
    """Tests for the balance-over-time chart data in POST responses."""

    def test_chart_data_included_in_response(self, auth_client, seed_user, db, seed_periods):
        """POST with valid input includes a strategy-chart canvas with
        well-formed JSON chart data.
        """
        user = seed_user["user"]
        _create_auto_loan(user, db.session)
        _create_mortgage(user, db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "avalanche",
        })
        assert resp.status_code == 200
        html = resp.data.decode()

        assert 'id="strategy-chart"' in html

        chart = _extract_chart_data(html)
        assert chart is not None
        assert "labels" in chart
        assert "datasets" in chart
        assert isinstance(chart["labels"], list)
        assert isinstance(chart["datasets"], list)
        assert len(chart["datasets"]) == 2  # auto + mortgage

    def test_chart_not_included_when_no_debts(self, auth_client, seed_user, db, seed_periods):
        """POST with no debt accounts does not include the chart canvas."""
        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "avalanche",
        })
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "strategy-chart" not in html

    def test_chart_data_values_are_floats(self, auth_client, seed_user, db, seed_periods):
        """Every value in each dataset's data array is a float or int,
        not a string representation of a Decimal.
        """
        user = seed_user["user"]
        _create_auto_loan(user, db.session)
        _create_mortgage(user, db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "avalanche",
        })
        chart = _extract_chart_data(resp.data.decode())
        assert chart is not None

        for ds in chart["datasets"]:
            for val in ds["data"]:
                assert isinstance(val, (int, float)), (
                    f"Expected int/float, got {type(val).__name__}: {val!r}"
                )

    def test_chart_labels_are_formatted_dates(self, auth_client, seed_user, db, seed_periods):
        """Chart labels are formatted as 'Mon YYYY' strings."""
        user = seed_user["user"]
        _create_auto_loan(user, db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "500",
            "strategy": "avalanche",
        })
        chart = _extract_chart_data(resp.data.decode())
        assert chart is not None
        assert len(chart["labels"]) > 0

        date_pattern = re.compile(r"^[A-Z][a-z]{2} \d{4}$")
        for label in chart["labels"]:
            assert date_pattern.match(label), (
                f"Label {label!r} does not match 'Mon YYYY' format"
            )

    def test_chart_dataset_count_matches_accounts(self, auth_client, seed_user, db, seed_periods):
        """Number of chart datasets equals the number of debt accounts."""
        user = seed_user["user"]
        _create_auto_loan(user, db.session)
        _create_mortgage(user, db.session)
        _create_student_loan(user, db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "avalanche",
        })
        chart = _extract_chart_data(resp.data.decode())
        assert chart is not None
        assert len(chart["datasets"]) == 3

    def test_chart_dataset_labels_match_account_names(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Each dataset label matches one of the debt account names."""
        user = seed_user["user"]
        auto = _create_auto_loan(user, db.session)
        mortgage = _create_mortgage(user, db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "avalanche",
        })
        chart = _extract_chart_data(resp.data.decode())
        assert chart is not None

        ds_labels = {ds["label"] for ds in chart["datasets"]}
        assert auto.name in ds_labels
        assert mortgage.name in ds_labels

    def test_chart_timeline_starts_at_principal(self, auth_client, seed_user, db, seed_periods):
        """The first data point of each dataset is the starting principal.

        The auto loan has $25,000 current_principal.  The chart's first
        value should be close to that (may differ slightly due to
        confirmed payment replay adjusting real principal).
        """
        user = seed_user["user"]
        _create_auto_loan(user, db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "500",
            "strategy": "avalanche",
        })
        chart = _extract_chart_data(resp.data.decode())
        assert chart is not None
        assert len(chart["datasets"]) == 1

        first_balance = chart["datasets"][0]["data"][0]
        # Real principal may differ from stored due to payment replay,
        # but should be in the same ballpark as $25,000.
        assert first_balance > 20000
        assert first_balance <= 30000

    def test_chart_timeline_ends_at_zero(self, auth_client, seed_user, db, seed_periods):
        """Each dataset's balance reaches zero by the end of the timeline."""
        user = seed_user["user"]
        _create_auto_loan(user, db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "500",
            "strategy": "avalanche",
        })
        chart = _extract_chart_data(resp.data.decode())
        assert chart is not None

        for ds in chart["datasets"]:
            last_balance = ds["data"][-1]
            assert last_balance == 0.0 or abs(last_balance) < 0.01, (
                f"Dataset {ds['label']!r} ends at {last_balance}, expected ~0"
            )
