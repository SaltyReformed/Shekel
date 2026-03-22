"""XSS payload prevention tests for all user-facing text input fields.

Verifies that Jinja2 autoescaping and any server-side sanitization
correctly neutralize XSS payloads across every text input surface
in the application. These tests serve as a regression safety net
against accidental introduction of |safe filters or raw HTML
rendering on user-controlled data.

Addresses: Test Audit Cross-Cutting Issue 8.
"""

import pytest
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.mortgage_params import MortgageParams
from app.models.ref import (
    AccountType, CalcMethod, DeductionTiming, FilingStatus,
    RecurrencePattern, Status, TransactionType,
)
from app.models.salary_profile import SalaryProfile
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services.auth_service import hash_password


# ── XSS Payload Vectors ─────────────────────────────────────────
#
# Each pytest.param provides:
#   payload          -- the raw XSS string submitted in form fields
#   escaped_fragment -- a bytes substring that MUST appear in the
#                      rendered HTML (proves Jinja2 escaped it)
#
# All payloads contain HTML metacharacters (<, >, ", &) that Jinja2
# autoescaping converts to entities.  If the raw payload bytes
# appear in the response, autoescaping has been bypassed.

XSS_PAYLOADS = [
    pytest.param(
        '<script>alert("xss")</script>',
        b"&lt;script&gt;",
        id="script_tag",
    ),
    pytest.param(
        '" onmouseover="alert(1)" data-x="',
        b"&#34; onmouseover=",
        id="attr_injection",
    ),
    pytest.param(
        "<img src=x onerror=alert(1)>",
        b"&lt;img src=",
        id="img_onerror",
    ),
    pytest.param(
        "<svg onload=alert(1)>",
        b"&lt;svg onload=",
        id="svg_onload",
    ),
    pytest.param(
        "&#60;script&#62;alert(1)&#60;/script&#62;",
        b"&amp;#60;script",
        id="entity_bypass",
    ),
    pytest.param(
        "<scr<script>ipt>alert(1)</script>",
        b"&lt;scr&lt;script&gt;",
        id="nested_script",
    ),
]


# ── Helpers ──────────────────────────────────────────────────────


def _create_savings_account(seed_user):
    """Create a savings account for the test user (needed by transfers)."""
    savings_type = (
        db.session.query(AccountType).filter_by(name="savings").one()
    )
    acct = Account(
        user_id=seed_user["user"].id,
        account_type_id=savings_type.id,
        name="XSS Test Savings",
        current_anchor_balance=Decimal("0"),
    )
    db.session.add(acct)
    db.session.commit()
    return acct


def _create_mortgage_account_with_params(seed_user):
    """Create a mortgage account with MortgageParams for escrow tests."""
    mortgage_type = (
        db.session.query(AccountType).filter_by(name="mortgage").one()
    )
    acct = Account(
        user_id=seed_user["user"].id,
        account_type_id=mortgage_type.id,
        name="XSS Test Mortgage",
        current_anchor_balance=Decimal("200000"),
    )
    db.session.add(acct)
    db.session.flush()

    params = MortgageParams(
        account_id=acct.id,
        original_principal=Decimal("200000"),
        current_principal=Decimal("195000"),
        interest_rate=Decimal("0.065"),
        term_months=360,
        origination_date=date(2024, 1, 1),
        payment_day=1,
    )
    db.session.add(params)
    db.session.commit()
    return acct, params


def _create_salary_profile(seed_user, seed_periods):
    """Create a salary profile for deduction tests."""
    filing_single = (
        db.session.query(FilingStatus).filter_by(name="single").one()
    )
    profile = SalaryProfile(
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        filing_status_id=filing_single.id,
        name="XSS Test Salary",
        annual_salary=Decimal("75000"),
        state_code="NC",
    )
    db.session.add(profile)
    db.session.commit()
    return profile


def _create_transaction(seed_user, seed_periods):
    """Create a transaction for update tests."""
    expense_type = (
        db.session.query(TransactionType).filter_by(name="expense").one()
    )
    projected = (
        db.session.query(Status).filter_by(name="projected").one()
    )
    txn = Transaction(
        pay_period_id=seed_periods[0].id,
        scenario_id=seed_user["scenario"].id,
        status_id=projected.id,
        name="Test Transaction",
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=expense_type.id,
        estimated_amount=Decimal("100.00"),
    )
    db.session.add(txn)
    db.session.commit()
    return txn


def _create_transfer(seed_user, seed_periods, savings_acct):
    """Create a transfer instance for update tests."""
    projected = (
        db.session.query(Status).filter_by(name="projected").one()
    )
    xfer = Transfer(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=savings_acct.id,
        pay_period_id=seed_periods[0].id,
        scenario_id=seed_user["scenario"].id,
        status_id=projected.id,
        name="Test Transfer",
        amount=Decimal("100.00"),
    )
    db.session.add(xfer)
    db.session.commit()
    return xfer


# ── Test Class ───────────────────────────────────────────────────


class TestXSSPrevention:
    """XSS payload tests for every user-facing text input surface."""

    def _assert_xss_safe(self, resp, payload, escaped_fragment):
        """Assert response escapes the payload and contains the
        escaped form.

        Args:
            resp: Flask test client response.
            payload: Raw XSS string that was submitted.
            escaped_fragment: Bytes that must appear in the response
                (the HTML-escaped form of the payload).
        """
        assert payload.encode() not in resp.data, (
            f"Raw XSS payload found unescaped in response: {payload!r}"
        )
        assert escaped_fragment in resp.data, (
            f"Escaped fragment missing from response: "
            f"{escaped_fragment!r}"
        )

    # ── Template Name ───────────────────────────────────────────

    @pytest.mark.parametrize("payload,escaped_fragment", XSS_PAYLOADS)
    def test_template_name(
        self, app, auth_client, seed_user, seed_periods,
        payload, escaped_fragment,
    ):
        """XSS in template name is escaped on the template list page."""
        with app.app_context():
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="expense").one()
            )
            category = seed_user["categories"]["Rent"]
            every_period = (
                db.session.query(RecurrencePattern)
                .filter_by(name="every_period").one()
            )

            # POST the XSS payload as the template name.
            auth_client.post("/templates", data={
                "name": payload,
                "default_amount": "100.00",
                "category_id": category.id,
                "account_id": seed_user["account"].id,
                "transaction_type_id": expense_type.id,
                "recurrence_pattern": every_period.name,
            })

            # GET the list page where the name renders.
            resp = auth_client.get("/templates")
            assert resp.status_code == 200
            self._assert_xss_safe(resp, payload, escaped_fragment)

    # ── Account Name ────────────────────────────────────────────

    @pytest.mark.parametrize("payload,escaped_fragment", XSS_PAYLOADS)
    def test_account_name(
        self, app, auth_client, seed_user,
        payload, escaped_fragment,
    ):
        """XSS in account name is escaped on the accounts list page."""
        with app.app_context():
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="savings").one()
            )

            resp = auth_client.post("/accounts", data={
                "name": payload,
                "account_type_id": savings_type.id,
                "anchor_balance": "0",
            }, follow_redirects=True)

            assert resp.status_code == 200
            self._assert_xss_safe(resp, payload, escaped_fragment)

    # ── Category Group Name ─────────────────────────────────────

    @pytest.mark.parametrize("payload,escaped_fragment", XSS_PAYLOADS)
    def test_category_group_name(
        self, app, auth_client, seed_user,
        payload, escaped_fragment,
    ):
        """XSS in category group name is escaped on the settings page."""
        with app.app_context():
            # POST creates the category, redirects to settings.
            auth_client.post("/categories", data={
                "group_name": payload,
                "item_name": "SafeItem",
            })

            # GET the settings page where categories render.
            resp = auth_client.get("/settings?section=categories")
            assert resp.status_code == 200
            self._assert_xss_safe(resp, payload, escaped_fragment)

    # ── Category Item Name ──────────────────────────────────────

    @pytest.mark.parametrize("payload,escaped_fragment", XSS_PAYLOADS)
    def test_category_item_name(
        self, app, auth_client, seed_user,
        payload, escaped_fragment,
    ):
        """XSS in category item name is escaped on the settings page."""
        with app.app_context():
            auth_client.post("/categories", data={
                "group_name": "SafeGroup",
                "item_name": payload,
            })

            resp = auth_client.get("/settings?section=categories")
            assert resp.status_code == 200
            self._assert_xss_safe(resp, payload, escaped_fragment)

    # ── Category Item Name (HTMX partial) ───────────────────────

    @pytest.mark.parametrize("payload,escaped_fragment", XSS_PAYLOADS)
    def test_category_item_name_htmx(
        self, app, auth_client, seed_user,
        payload, escaped_fragment,
    ):
        """XSS in category item name is escaped in the HTMX partial."""
        with app.app_context():
            resp = auth_client.post(
                "/categories",
                data={
                    "group_name": "HTMXGroup",
                    "item_name": payload,
                },
                headers={"HX-Request": "true"},
            )

            # HTMX path returns the _category_row.html partial.
            assert resp.status_code == 200
            self._assert_xss_safe(resp, payload, escaped_fragment)

    # ── Salary Profile Name ─────────────────────────────────────

    @pytest.mark.parametrize("payload,escaped_fragment", XSS_PAYLOADS)
    def test_salary_profile_name(
        self, app, auth_client, seed_user, seed_periods,
        payload, escaped_fragment,
    ):
        """XSS in salary profile name is escaped on the salary list."""
        with app.app_context():
            filing_single = (
                db.session.query(FilingStatus)
                .filter_by(name="single").one()
            )

            # POST creates the profile (also creates template + txns).
            auth_client.post("/salary", data={
                "name": payload,
                "annual_salary": "75000",
                "filing_status_id": filing_single.id,
                "state_code": "NC",
            })

            # GET the salary list page where the name renders.
            resp = auth_client.get("/salary")
            assert resp.status_code == 200
            self._assert_xss_safe(resp, payload, escaped_fragment)

    # ── Transfer Template Name ──────────────────────────────────

    @pytest.mark.parametrize("payload,escaped_fragment", XSS_PAYLOADS)
    def test_transfer_template_name(
        self, app, auth_client, seed_user, seed_periods,
        payload, escaped_fragment,
    ):
        """XSS in transfer template name is escaped on the list page."""
        with app.app_context():
            savings_acct = _create_savings_account(seed_user)
            every_period = (
                db.session.query(RecurrencePattern)
                .filter_by(name="every_period").one()
            )

            auth_client.post("/transfers", data={
                "name": payload,
                "default_amount": "100.00",
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings_acct.id,
                "recurrence_pattern": every_period.name,
            })

            resp = auth_client.get("/transfers")
            assert resp.status_code == 200
            self._assert_xss_safe(resp, payload, escaped_fragment)

    # ── Savings Goal Name ───────────────────────────────────────

    @pytest.mark.parametrize("payload,escaped_fragment", XSS_PAYLOADS)
    def test_savings_goal_name(
        self, app, auth_client, seed_user, seed_periods,
        payload, escaped_fragment,
    ):
        """XSS in savings goal name is escaped on the savings dashboard."""
        with app.app_context():
            auth_client.post("/savings/goals", data={
                "name": payload,
                "target_amount": "10000",
                "account_id": seed_user["account"].id,
            })

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            self._assert_xss_safe(resp, payload, escaped_fragment)

    # ── Pension Profile Name ────────────────────────────────────

    @pytest.mark.parametrize("payload,escaped_fragment", XSS_PAYLOADS)
    def test_pension_profile_name(
        self, app, auth_client, seed_user,
        payload, escaped_fragment,
    ):
        """XSS in pension profile name is escaped on the pension page."""
        with app.app_context():
            auth_client.post("/retirement/pension", data={
                "name": payload,
                "benefit_multiplier": "1.85",
                "consecutive_high_years": "4",
                "hire_date": "2020-01-01",
            })

            # GET the pension list page where names render.
            resp = auth_client.get("/retirement/pension")
            assert resp.status_code == 200
            self._assert_xss_safe(resp, payload, escaped_fragment)

    # ── Escrow Component Name ───────────────────────────────────

    @pytest.mark.parametrize("payload,escaped_fragment", XSS_PAYLOADS)
    def test_escrow_component_name(
        self, app, auth_client, seed_user,
        payload, escaped_fragment,
    ):
        """XSS in escrow component name is escaped in HTMX partial."""
        with app.app_context():
            acct, _ = _create_mortgage_account_with_params(seed_user)

            # POST returns the _escrow_list.html partial.
            resp = auth_client.post(
                f"/accounts/{acct.id}/mortgage/escrow",
                data={
                    "name": payload,
                    "annual_amount": "1200.00",
                },
            )

            assert resp.status_code == 200
            self._assert_xss_safe(resp, payload, escaped_fragment)

    # ── Transaction Ad-hoc Create (name) ────────────────────────

    @pytest.mark.parametrize("payload,escaped_fragment", XSS_PAYLOADS)
    def test_transaction_create_name(
        self, app, auth_client, seed_user, seed_periods,
        payload, escaped_fragment,
    ):
        """XSS in ad-hoc transaction name is escaped in cell partial."""
        with app.app_context():
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="expense").one()
            )
            category = seed_user["categories"]["Rent"]

            # POST creates the transaction, returns cell HTML (201).
            resp = auth_client.post("/transactions", data={
                "name": payload,
                "estimated_amount": "50.00",
                "pay_period_id": seed_periods[0].id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": category.id,
                "transaction_type_id": expense_type.id,
            })

            assert resp.status_code == 201
            self._assert_xss_safe(resp, payload, escaped_fragment)

    # ── Transaction Update (notes) ──────────────────────────────

    @pytest.mark.parametrize("payload,escaped_fragment", XSS_PAYLOADS)
    def test_transaction_update_notes(
        self, app, auth_client, seed_user, seed_periods,
        payload, escaped_fragment,
    ):
        """XSS in transaction notes is escaped in the cell partial."""
        with app.app_context():
            txn = _create_transaction(seed_user, seed_periods)

            # PATCH returns updated cell HTML.
            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"notes": payload},
            )

            assert resp.status_code == 200
            # Notes render in the title attribute of the cell.
            self._assert_xss_safe(resp, payload, escaped_fragment)

    # ── Transfer Instance Update (notes) ────────────────────────

    @pytest.mark.parametrize("payload,escaped_fragment", XSS_PAYLOADS)
    def test_transfer_update_notes(
        self, app, auth_client, seed_user, seed_periods,
        payload, escaped_fragment,
    ):
        """XSS in transfer notes is escaped in the cell partial."""
        with app.app_context():
            savings_acct = _create_savings_account(seed_user)
            xfer = _create_transfer(
                seed_user, seed_periods, savings_acct,
            )

            # PATCH returns updated transfer cell HTML.
            resp = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"notes": payload},
            )

            assert resp.status_code == 200
            self._assert_xss_safe(resp, payload, escaped_fragment)

    # ── Deduction Name ──────────────────────────────────────────

    @pytest.mark.parametrize("payload,escaped_fragment", XSS_PAYLOADS)
    def test_deduction_name(
        self, app, auth_client, seed_user, seed_periods,
        payload, escaped_fragment,
    ):
        """XSS in paycheck deduction name is escaped on the edit page."""
        with app.app_context():
            profile = _create_salary_profile(seed_user, seed_periods)
            pre_tax = (
                db.session.query(DeductionTiming)
                .filter_by(name="pre_tax").one()
            )
            flat_method = (
                db.session.query(CalcMethod)
                .filter_by(name="flat").one()
            )

            auth_client.post(
                f"/salary/{profile.id}/deductions",
                data={
                    "name": payload,
                    "amount": "100",
                    "deduction_timing_id": pre_tax.id,
                    "calc_method_id": flat_method.id,
                    "deductions_per_year": "26",
                },
            )

            # GET the profile edit page where deductions render.
            resp = auth_client.get(
                f"/salary/{profile.id}/edit"
            )
            assert resp.status_code == 200
            self._assert_xss_safe(resp, payload, escaped_fragment)

    # ── Registration Display Name ───────────────────────────────

    @pytest.mark.parametrize("payload,escaped_fragment", XSS_PAYLOADS)
    def test_register_display_name(
        self, app, client, seed_user,
        payload, escaped_fragment,
    ):
        """XSS in registration display_name is escaped in the navbar.

        Uses the unauthenticated ``client`` fixture to register a new
        user, then logs in and checks the main page navbar.
        """
        with app.app_context():
            # Register a new user with the XSS payload as display_name.
            email = "xss-test@shekel.local"
            resp = client.post("/register", data={
                "email": email,
                "display_name": payload,
                "password": "TestPass123!",
                "confirm_password": "TestPass123!",
            })
            assert resp.status_code == 302  # Redirect to login

            # Log in as the new user.
            resp = client.post("/login", data={
                "email": email,
                "password": "TestPass123!",
            }, follow_redirects=True)

            assert resp.status_code == 200
            # display_name renders in base.html navbar.
            self._assert_xss_safe(resp, payload, escaped_fragment)

    # ── javascript: URL Protocol Injection ──────────────────────

    def test_javascript_url_not_in_href_account_name(
        self, app, auth_client, seed_user,
    ):
        """javascript: URL payload is rendered as text, not as a link.

        The javascript: protocol is only dangerous when placed inside
        an href attribute.  Verify it does not appear in any href.
        """
        with app.app_context():
            js_payload = "javascript:alert(1)"
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="savings").one()
            )

            auth_client.post("/accounts", data={
                "name": js_payload,
                "account_type_id": savings_type.id,
                "anchor_balance": "0",
            })

            resp = auth_client.get("/accounts")
            assert resp.status_code == 200

            # The text appears (it has no HTML metacharacters to escape).
            assert js_payload.encode() in resp.data
            # But it must NOT appear inside an href attribute.
            assert (
                b'href="javascript:alert(1)"' not in resp.data
            )
            assert (
                b"href='javascript:alert(1)'" not in resp.data
            )

    def test_javascript_url_not_in_href_template_name(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """javascript: URL payload in template name is not in any href."""
        with app.app_context():
            js_payload = "javascript:alert(1)"
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="expense").one()
            )
            category = seed_user["categories"]["Rent"]
            every_period = (
                db.session.query(RecurrencePattern)
                .filter_by(name="every_period").one()
            )

            auth_client.post("/templates", data={
                "name": js_payload,
                "default_amount": "100.00",
                "category_id": category.id,
                "account_id": seed_user["account"].id,
                "transaction_type_id": expense_type.id,
                "recurrence_pattern": every_period.name,
            })

            resp = auth_client.get("/templates")
            assert resp.status_code == 200
            assert js_payload.encode() in resp.data
            assert (
                b'href="javascript:alert(1)"' not in resp.data
            )
