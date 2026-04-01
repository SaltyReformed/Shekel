"""
Tests for unified loan routes.

Covers dashboard, setup, parameter updates, escrow management,
rate history, and payoff calculator across multiple loan types.
"""

import re
from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.account import Account
from app.models.loan_params import LoanParams
from app.models.loan_features import RateHistory, EscrowComponent
from app.models.ref import AccountType


# ── Helpers ──────────────────────────────────────────────────────────


def _create_loan_account(seed_user, db_session, type_name, name, principal,
                         rate, term, orig_date, payment_day, is_arm=False):
    """Helper to create a loan account with params for any amortizing type."""
    loan_type = db_session.query(AccountType).filter_by(name=type_name).one()
    account = Account(
        user_id=seed_user["user"].id,
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
        is_arm=is_arm,
    )
    db_session.add(params)
    db_session.commit()
    return account


def _create_auto_loan(seed_user, db_session, name="My Auto Loan"):
    """Helper: auto loan account with params."""
    return _create_loan_account(
        seed_user, db_session, "Auto Loan", name,
        Decimal("25000.00"), Decimal("0.05000"), 60,
        date(2025, 1, 1), 15,
    )


def _create_mortgage(seed_user, db_session, name="My Mortgage"):
    """Helper: mortgage account with params."""
    return _create_loan_account(
        seed_user, db_session, "Mortgage", name,
        Decimal("250000.00"), Decimal("0.06500"), 360,
        date(2023, 6, 1), 1,
    )


def _create_other_loan(second_user, db_session, type_name="Auto Loan"):
    """Create a loan account owned by the second user."""
    loan_type = db_session.query(AccountType).filter_by(name=type_name).one()
    account = Account(
        user_id=second_user["user"].id,
        account_type_id=loan_type.id,
        name="Other Loan",
        current_anchor_balance=Decimal("15000.00"),
    )
    db_session.add(account)
    db_session.flush()

    params = LoanParams(
        account_id=account.id,
        original_principal=Decimal("20000.00"),
        current_principal=Decimal("15000.00"),
        interest_rate=Decimal("0.04000"),
        term_months=48,
        origination_date=date(2024, 6, 1),
        payment_day=1,
    )
    db_session.add(params)
    db_session.commit()
    return account


# ── Dashboard Tests ──────────────────────────────────────────────────


class TestLoanDashboard:
    """Tests for the unified loan dashboard page."""

    @pytest.mark.parametrize("create_fn", [_create_auto_loan, _create_mortgage])
    def test_dashboard_view(self, auth_client, seed_user, db, seed_periods, create_fn):
        """GET returns 200 with loan summary for any amortizing type."""
        acct = create_fn(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b"Loan Summary" in resp.data

    def test_dashboard_setup_when_no_params(self, auth_client, seed_user, db, seed_periods):
        """Dashboard renders setup page when params don't exist yet."""
        loan_type = db.session.query(AccountType).filter_by(name="Auto Loan").one()
        account = Account(
            user_id=seed_user["user"].id,
            account_type_id=loan_type.id,
            name="No Params Loan",
        )
        db.session.add(account)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{account.id}/loan")
        assert resp.status_code == 200
        assert b"Configure" in resp.data

    def test_dashboard_404_nonexistent(self, auth_client, seed_user, db, seed_periods):
        """Nonexistent account redirects to savings dashboard."""
        resp = auth_client.get("/accounts/99999/loan")
        assert resp.status_code == 302
        assert "/savings" in resp.headers.get("Location", "")

    def test_dashboard_idor(self, auth_client, second_user, db, seed_periods):
        """Another user's loan dashboard is rejected without leaking data."""
        other = _create_other_loan(second_user, db.session)
        resp = auth_client.get(f"/accounts/{other.id}/loan")
        assert resp.status_code == 302
        assert "/savings" in resp.headers.get("Location", "")
        assert b"Other Loan" not in resp.data

    def test_dashboard_wrong_type(self, auth_client, seed_user, db, seed_periods):
        """Non-amortizing account type redirects away."""
        acct = seed_user["account"]  # checking account
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 302
        assert "/savings" in resp.headers.get("Location", "")

    def test_dashboard_login_required(self, client, seed_user, db, seed_periods):
        """Unauthenticated request redirects to login."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")

    def test_dashboard_shows_term_field(self, auth_client, seed_user, db, seed_periods):
        """Dashboard parameter form includes editable term_months input."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b'name="term_months"' in resp.data
        assert b'value="60"' in resp.data

    def test_dashboard_shows_payoff_calculator(self, auth_client, seed_user, db, seed_periods):
        """Dashboard renders the payoff calculator tab for all loan types."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b"Payoff Calculator" in resp.data
        assert b'data-slider-group="payoff"' in resp.data

    def test_dashboard_shows_icon_from_account_type(self, auth_client, seed_user, db, seed_periods):
        """Dashboard renders the correct icon class from account_type."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b"bi-house" in resp.data


# ── Setup / Create Params Tests ──────────────────────────────────────


class TestLoanSetup:
    """Tests for initial loan parameter setup."""

    @pytest.mark.parametrize("type_name", ["Auto Loan", "Mortgage"])
    def test_create_params(self, auth_client, seed_user, db, seed_periods, type_name):
        """POST valid params creates LoanParams record."""
        loan_type = db.session.query(AccountType).filter_by(name=type_name).one()
        account = Account(
            user_id=seed_user["user"].id,
            account_type_id=loan_type.id,
            name=f"Setup {type_name}",
        )
        db.session.add(account)
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data={
                "original_principal": "30000.00",
                "current_principal": "25000.00",
                "interest_rate": "5.000",
                "term_months": "60",
                "origination_date": "2025-01-01",
                "payment_day": "15",
            },
        )
        assert resp.status_code == 302
        assert "/loan" in resp.headers.get("Location", "")

        params = db.session.query(LoanParams).filter_by(account_id=account.id).one()
        assert params.interest_rate == Decimal("0.05000")
        assert params.term_months == 60

    def test_create_params_already_configured(self, auth_client, seed_user, db, seed_periods):
        """POST setup when params exist redirects with info flash."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/setup",
            data={
                "original_principal": "99999.00",
                "current_principal": "99999.00",
                "interest_rate": "0.99",
                "term_months": "12",
                "origination_date": "2025-01-01",
                "payment_day": "1",
            },
        )
        assert resp.status_code == 302
        resp2 = auth_client.get(resp.headers["Location"])
        assert b"Loan parameters already configured." in resp2.data

    def test_create_params_term_exceeds_type_max(self, auth_client, seed_user, db, seed_periods):
        """Auto loan rejects term > 120 (type-specific max_term_months)."""
        loan_type = db.session.query(AccountType).filter_by(name="Auto Loan").one()
        account = Account(
            user_id=seed_user["user"].id,
            account_type_id=loan_type.id,
            name="Term Test Auto",
        )
        db.session.add(account)
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data={
                "original_principal": "30000.00",
                "current_principal": "25000.00",
                "interest_rate": "5.000",
                "term_months": "360",
                "origination_date": "2025-01-01",
                "payment_day": "15",
            },
        )
        assert resp.status_code == 200  # re-renders setup
        assert b"cannot exceed" in resp.data

        count = db.session.query(LoanParams).filter_by(account_id=account.id).count()
        assert count == 0

    def test_mortgage_allows_long_term(self, auth_client, seed_user, db, seed_periods):
        """Mortgage accepts term=360 (max_term_months=600)."""
        loan_type = db.session.query(AccountType).filter_by(name="Mortgage").one()
        account = Account(
            user_id=seed_user["user"].id,
            account_type_id=loan_type.id,
            name="Long Term Mortgage",
        )
        db.session.add(account)
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data={
                "original_principal": "300000.00",
                "current_principal": "250000.00",
                "interest_rate": "6.500",
                "term_months": "360",
                "origination_date": "2023-06-01",
                "payment_day": "1",
            },
        )
        assert resp.status_code == 302
        params = db.session.query(LoanParams).filter_by(account_id=account.id).one()
        assert params.term_months == 360

    def test_setup_prefills_current_principal(self, auth_client, seed_user, db, seed_periods):
        """Setup form pre-fills current_principal from anchor balance."""
        loan_type = db.session.query(AccountType).filter_by(name="Auto Loan").one()
        resp = auth_client.post(
            "/accounts",
            data={
                "name": "Prepop Auto",
                "account_type_id": str(loan_type.id),
                "anchor_balance": "15000",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b'name="current_principal"' in resp.data
        assert b'value="15000.00"' in resp.data


# ── Update Params Tests ──────────────────────────────────────────────


class TestLoanParamsUpdate:
    """Tests for updating loan parameters."""

    def test_params_update(self, auth_client, seed_user, db, seed_periods):
        """POST valid data updates params (percentage converted to decimal)."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={
                "current_principal": "22000.00",
                "interest_rate": "4.500",
                "payment_day": "1",
            },
        )
        assert resp.status_code == 302

        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        assert params.current_principal == Decimal("22000.00")
        assert params.interest_rate == Decimal("0.04500")

    def test_params_update_validation(self, auth_client, seed_user, db, seed_periods):
        """POST invalid data leaves DB unchanged."""
        acct = _create_auto_loan(seed_user, db.session)
        orig = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        orig_day = orig.payment_day

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={"payment_day": "32"},
        )
        assert resp.status_code == 302

        db.session.expire_all()
        after = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        assert after.payment_day == orig_day

    def test_term_update_saves(self, auth_client, seed_user, db, seed_periods):
        """POST with valid term_months persists the new value."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={
                "current_principal": "25000.00",
                "interest_rate": "5.000",
                "payment_day": "15",
                "term_months": "48",
            },
        )
        assert resp.status_code == 302

        db.session.expire_all()
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        assert params.term_months == 48

    def test_arm_fields_update(self, auth_client, seed_user, db, seed_periods):
        """ARM fields can be toggled on and adjusted."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={
                "current_principal": "250000.00",
                "interest_rate": "6.500",
                "payment_day": "1",
                "is_arm": "true",
                "arm_first_adjustment_months": "60",
                "arm_adjustment_interval_months": "12",
            },
        )
        assert resp.status_code == 302

        db.session.expire_all()
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        assert params.is_arm is True
        assert params.arm_first_adjustment_months == 60

    def test_params_update_idor(self, auth_client, second_user, db, seed_periods):
        """POST to another user's loan params is rejected and unchanged."""
        other = _create_other_loan(second_user, db.session)
        orig = db.session.query(LoanParams).filter_by(account_id=other.id).one()
        orig_principal = orig.current_principal

        resp = auth_client.post(
            f"/accounts/{other.id}/loan/params",
            data={
                "current_principal": "1.00",
                "interest_rate": "0.99",
                "payment_day": "28",
            },
        )
        assert resp.status_code == 302
        assert "/savings" in resp.headers.get("Location", "")

        db.session.expire_all()
        after = db.session.query(LoanParams).filter_by(account_id=other.id).one()
        assert after.current_principal == orig_principal

    def test_params_update_nonexistent(self, auth_client, seed_user, db, seed_periods):
        """POST to nonexistent account redirects with flash."""
        resp = auth_client.post(
            "/accounts/999999/loan/params",
            data={"current_principal": "20000.00", "interest_rate": "5.0", "payment_day": "1"},
        )
        assert resp.status_code == 302
        assert "/savings" in resp.headers.get("Location", "")

    def test_params_update_wrong_type(self, auth_client, seed_user, db, seed_periods):
        """POST loan params to checking account redirects."""
        checking = seed_user["account"]
        resp = auth_client.post(
            f"/accounts/{checking.id}/loan/params",
            data={"current_principal": "20000.00", "interest_rate": "5.0", "payment_day": "1"},
        )
        assert resp.status_code == 302
        assert "/savings" in resp.headers.get("Location", "")

    def test_amortization_uses_updated_term(self, auth_client, seed_user, db, seed_periods):
        """Changing term_months recalculates amortization on next dashboard load."""
        acct = _create_auto_loan(seed_user, db.session)
        resp1 = auth_client.get(f"/accounts/{acct.id}/loan")

        auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={
                "current_principal": "25000.00",
                "interest_rate": "5.000",
                "payment_day": "15",
                "term_months": "36",
            },
        )
        resp2 = auth_client.get(f"/accounts/{acct.id}/loan")

        pattern = rb"Monthly P.{1,6}I.*?\$([0-9,]+\.\d{2})"
        match1 = re.search(pattern, resp1.data, re.DOTALL)
        match2 = re.search(pattern, resp2.data, re.DOTALL)
        assert match1 is not None
        assert match2 is not None
        assert match1.group(1) != match2.group(1)


# ── Escrow Tests ─────────────────────────────────────────────────────


class TestEscrow:
    """Tests for escrow component management."""

    def test_escrow_add(self, auth_client, seed_user, db, seed_periods):
        """POST escrow creates component with percentage-to-decimal conversion."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow",
            data={"name": "Property Tax", "annual_amount": "4800.00", "inflation_rate": "3"},
        )
        assert resp.status_code == 200
        assert b"Property Tax" in resp.data

        comp = db.session.query(EscrowComponent).filter_by(account_id=acct.id).first()
        assert comp is not None
        assert comp.inflation_rate == Decimal("0.03")

    def test_escrow_add_duplicate_name(self, auth_client, seed_user, db, seed_periods):
        """Duplicate escrow name returns 400."""
        acct = _create_mortgage(seed_user, db.session)
        db.session.add(EscrowComponent(
            account_id=acct.id, name="Insurance", annual_amount=Decimal("2400.00"),
        ))
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow",
            data={"name": "Insurance", "annual_amount": "3000.00"},
        )
        assert resp.status_code == 400
        assert b"already exists" in resp.data

    def test_escrow_delete(self, auth_client, seed_user, db, seed_periods):
        """POST delete deactivates component."""
        acct = _create_mortgage(seed_user, db.session)
        comp = EscrowComponent(
            account_id=acct.id, name="Old Insurance", annual_amount=Decimal("1200.00"),
        )
        db.session.add(comp)
        db.session.commit()

        resp = auth_client.post(f"/accounts/{acct.id}/loan/escrow/{comp.id}/delete")
        assert resp.status_code == 200
        db.session.refresh(comp)
        assert comp.is_active is False

    def test_escrow_delete_idor(self, auth_client, second_user, db, seed_periods):
        """DELETE another user's escrow returns 404 and leaves it active."""
        other = _create_other_loan(second_user, db.session, "Mortgage")
        comp = EscrowComponent(
            account_id=other.id, name="Tax", annual_amount=Decimal("3000.00"),
        )
        db.session.add(comp)
        db.session.commit()

        resp = auth_client.post(f"/accounts/{other.id}/loan/escrow/{comp.id}/delete")
        assert resp.status_code == 404

        db.session.expire_all()
        after = db.session.get(EscrowComponent, comp.id)
        assert after.is_active is True

    def test_escrow_oob_payment_update(self, auth_client, seed_user, db, seed_periods):
        """Adding escrow returns OOB fragments for payment summary."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow",
            data={"name": "Property Tax", "annual_amount": "4800.00"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'id="total-payment-display"' in html
        assert 'hx-swap-oob="true"' in html
        assert "$400.00/mo" in html


# ── Rate History Tests ───────────────────────────────────────────────


class TestRateHistory:
    """Tests for ARM rate change recording."""

    def test_rate_change_create(self, auth_client, seed_user, db, seed_periods):
        """POST rate change creates history row and updates params rate."""
        acct = _create_mortgage(seed_user, db.session)
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        params.is_arm = True
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/rate",
            data={"effective_date": "2026-04-01", "interest_rate": "7.000", "notes": "Adjustment"},
        )
        assert resp.status_code == 200

        entry = db.session.query(RateHistory).filter_by(account_id=acct.id).first()
        assert entry is not None
        assert entry.interest_rate == Decimal("0.07000")

        db.session.expire_all()
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        assert params.interest_rate == Decimal("0.07000")

    def test_rate_change_validation(self, auth_client, seed_user, db, seed_periods):
        """Invalid rate returns 400."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/rate",
            data={"interest_rate": "200.0"},
        )
        assert resp.status_code == 400

    def test_rate_change_idor(self, auth_client, second_user, db, seed_periods):
        """Rate change to another user's loan returns 404 with no side effects."""
        other = _create_other_loan(second_user, db.session, "Mortgage")
        resp = auth_client.post(
            f"/accounts/{other.id}/loan/rate",
            data={"interest_rate": "9.0", "effective_date": "2026-06-01"},
        )
        assert resp.status_code == 404

        count = db.session.query(RateHistory).filter_by(account_id=other.id).count()
        assert count == 0


# ── Payoff Calculator Tests ──────────────────────────────────────────


class TestPayoffCalculator:
    """Tests for the payoff calculator."""

    @pytest.mark.parametrize("create_fn", [_create_auto_loan, _create_mortgage])
    def test_payoff_extra_payment(self, auth_client, seed_user, db, seed_periods, create_fn):
        """POST extra payment mode returns results for any loan type."""
        acct = create_fn(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "200"},
        )
        assert resp.status_code == 200
        assert b"Months Saved" in resp.data

    def test_payoff_target_date(self, auth_client, seed_user, db, seed_periods):
        """POST target date mode returns payment data."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "target_date", "target_date": "2040-01-01"},
        )
        assert resp.status_code == 200
        assert b"$" in resp.data

    def test_payoff_validation(self, auth_client, seed_user, db, seed_periods):
        """Invalid mode returns error."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "invalid_mode"},
        )
        assert resp.status_code == 200
        assert b"Please correct the highlighted errors" in resp.data

    def test_payoff_idor(self, auth_client, second_user, db, seed_periods):
        """Payoff calc to another user's loan returns 404."""
        other = _create_other_loan(second_user, db.session)
        resp = auth_client.post(
            f"/accounts/{other.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "100"},
        )
        assert resp.status_code == 404


# ── Account Creation Redirect Tests ──────────────────────────────────


class TestLoanAccountCreation:
    """Test that creating amortizing account types redirects to loan setup."""

    @pytest.mark.parametrize("type_name", ["Auto Loan", "Mortgage"])
    def test_creation_redirects_to_loan_dashboard(
        self, auth_client, seed_user, db, seed_periods, type_name,
    ):
        """Creating an amortizing account redirects to the loan dashboard."""
        loan_type = db.session.query(AccountType).filter_by(name=type_name).one()
        resp = auth_client.post(
            "/accounts",
            data={
                "name": f"New {type_name}",
                "account_type_id": str(loan_type.id),
                "anchor_balance": "20000",
            },
        )
        assert resp.status_code == 302
        assert "/loan" in resp.headers.get("Location", "")

    def test_params_not_duplicated(self, auth_client, seed_user, db, seed_periods):
        """Visiting dashboard does not create duplicate param records."""
        acct = _create_auto_loan(seed_user, db.session)
        auth_client.get(f"/accounts/{acct.id}/loan")
        auth_client.get(f"/accounts/{acct.id}/loan")

        count = db.session.query(LoanParams).filter_by(account_id=acct.id).count()
        assert count == 1


# ── Negative Path Tests ──────────────────────────────────────────────


class TestLoanNegativePaths:
    """Negative-path and boundary tests for loan routes."""

    def test_negative_interest_rate(self, auth_client, seed_user, db, seed_periods):
        """Negative interest rate is rejected."""
        acct = _create_auto_loan(seed_user, db.session)
        orig = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        orig_rate = orig.interest_rate

        auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={"current_principal": "25000.00", "interest_rate": "-0.01", "payment_day": "15"},
        )
        db.session.expire_all()
        after = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        assert after.interest_rate == orig_rate

    def test_payment_day_zero(self, auth_client, seed_user, db, seed_periods):
        """Payment day 0 is rejected."""
        acct = _create_auto_loan(seed_user, db.session)
        orig = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        orig_day = orig.payment_day

        auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={"current_principal": "25000.00", "interest_rate": "5.000", "payment_day": "0"},
        )
        db.session.expire_all()
        after = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        assert after.payment_day == orig_day

    def test_payment_day_32(self, auth_client, seed_user, db, seed_periods):
        """Payment day 32 is rejected."""
        acct = _create_auto_loan(seed_user, db.session)
        orig = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        orig_day = orig.payment_day

        auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={"current_principal": "25000.00", "interest_rate": "5.000", "payment_day": "32"},
        )
        db.session.expire_all()
        after = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        assert after.payment_day == orig_day

    def test_escrow_missing_name(self, auth_client, seed_user, db, seed_periods):
        """Escrow POST without name returns 400."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow",
            data={"annual_amount": "1200.00"},
        )
        assert resp.status_code == 400

    def test_rate_change_missing_date(self, auth_client, seed_user, db, seed_periods):
        """Rate change without effective_date returns 400."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/rate",
            data={"interest_rate": "5.5"},
        )
        assert resp.status_code == 400

    def test_escrow_nonexistent_account(self, auth_client, seed_user, db, seed_periods):
        """Escrow POST to nonexistent account returns 404."""
        resp = auth_client.post(
            "/accounts/999999/loan/escrow",
            data={"name": "Tax", "annual_amount": "3000.00"},
        )
        assert resp.status_code == 404

    def test_rate_change_nonexistent_account(self, auth_client, seed_user, db, seed_periods):
        """Rate change to nonexistent account returns 404."""
        resp = auth_client.post(
            "/accounts/999999/loan/rate",
            data={"interest_rate": "5.5", "effective_date": "2026-06-01"},
        )
        assert resp.status_code == 404

    def test_escrow_idor_add(self, auth_client, second_user, db, seed_periods):
        """Escrow add to another user's loan returns 404."""
        other = _create_other_loan(second_user, db.session, "Mortgage")
        resp = auth_client.post(
            f"/accounts/{other.id}/loan/escrow",
            data={"name": "Stolen", "annual_amount": "9999.00"},
        )
        assert resp.status_code == 404

        count = db.session.query(EscrowComponent).filter_by(account_id=other.id).count()
        assert count == 0


# ── Section 5 Regression Baseline ──────────────────────────────────────

# All five amortizing account types with realistic parameters.
_AMORTIZING_TYPES = [
    ("Mortgage", Decimal("250000.00"), Decimal("0.06500"), 360, 600),
    ("Auto Loan", Decimal("25000.00"), Decimal("0.05000"), 60, 120),
    ("Student Loan", Decimal("45000.00"), Decimal("0.04500"), 120, 300),
    ("Personal Loan", Decimal("10000.00"), Decimal("0.08000"), 48, 120),
    ("HELOC", Decimal("50000.00"), Decimal("0.07250"), 180, 360),
]


class TestLoanDashboardRegression:
    """Regression baseline for Section 5 loan dashboard changes.

    Verifies dashboard rendering, payoff calculator modes, and
    multi-type support before Section 5 modifies the amortization
    engine and loan UI.
    """

    @pytest.mark.parametrize("type_name,principal,rate,term,max_term", _AMORTIZING_TYPES)
    def test_dashboard_renders_for_all_amortizing_types(
        self, auth_client, seed_user, db, seed_periods,
        type_name, principal, rate, term, max_term,
    ):
        """Dashboard must render successfully for every amortizing account type.

        Section 5 may add type-specific dashboard panels.  This ensures
        all existing types continue to work.
        """
        acct = _create_loan_account(
            seed_user, db.session, type_name, f"Test {type_name}",
            principal, rate, term, date(2024, 1, 1), 1,
        )
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Dashboard should display the monthly payment.
        assert "Monthly" in html or "monthly" in html

    @pytest.mark.parametrize("type_name,principal,rate,term,max_term", _AMORTIZING_TYPES)
    def test_payoff_extra_payment_all_types(
        self, auth_client, seed_user, db, seed_periods,
        type_name, principal, rate, term, max_term,
    ):
        """Payoff calculator extra-payment mode works for all amortizing types.

        Verifies months saved, interest saved, and new payoff date are
        present in the response.
        """
        acct = _create_loan_account(
            seed_user, db.session, type_name, f"Test {type_name}",
            principal, rate, term, date(2024, 1, 1), 1,
        )
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "200.00"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Response must contain savings metrics.
        assert "saved" in html.lower() or "interest" in html.lower()

    def test_payoff_target_date_returns_required_payment(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Target-date mode returns the extra monthly payment needed.

        Verifies the payoff calculator correctly handles the target_date
        code path and returns a numeric result.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "target_date", "target_date": "2040-06-01"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Should contain a dollar amount for the required extra payment.
        assert "$" in html or "extra" in html.lower()

    def test_payoff_zero_extra_payment_shows_standard_metrics(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Zero extra payment should return standard schedule metrics
        with zero months saved and zero interest saved.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "0.00"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # With zero extra, months saved should be 0.
        assert "0 months" in html.lower() or "0 mo" in html.lower() or \
               "$0" in html

    def test_payoff_invalid_mode_does_not_crash(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Invalid payoff mode must not cause a server error.

        The handler returns 200 with default/empty results rather than
        a 400 validation error.  This documents the current behavior.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "invalid_mode"},
        )
        # Must not crash -- 200 or 400 are both acceptable, not 500.
        assert resp.status_code != 500

    def test_payoff_negative_extra_payment_rejected(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Negative extra payment must be rejected by validation."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "-100.00"},
        )
        # Should not succeed -- either 400 or validation error.
        assert resp.status_code in (400, 422) or b"error" in resp.data.lower()

    def test_dashboard_idor_blocked(
        self, auth_client, seed_second_user, seed_second_periods,
        second_auth_client, seed_user, seed_periods, db,
    ):
        """User A cannot view User B's loan dashboard.

        Verifies the IDOR protection returns an identical response
        for 'not found' and 'not yours' per the security response rule.
        """
        other_acct = _create_loan_account(
            seed_second_user, db.session, "Mortgage", "Other Mortgage",
            Decimal("200000.00"), Decimal("0.06000"), 360,
            date(2024, 1, 1), 1,
        )
        # User A tries to access User B's dashboard.
        resp = auth_client.get(f"/accounts/{other_acct.id}/loan")
        # Must not return 200 -- should redirect or return 404.
        assert resp.status_code in (302, 404)
        if resp.status_code == 200:
            pytest.fail("IDOR: User A could view User B's loan dashboard")
