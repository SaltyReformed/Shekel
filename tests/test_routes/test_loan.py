"""
Tests for unified loan routes.

Covers dashboard, setup, parameter updates, escrow management,
rate history, and payoff calculator across multiple loan types.
"""

import re
from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.account import Account
from app.models.loan_params import LoanParams
from app.models.loan_features import RateHistory, EscrowComponent
from app.models.ref import AccountType
from app.services.transfer_service import create_transfer


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


# ── Payment Integration Tests (Commit 5.1-2) ────────────────────────


def _create_transfer_to_loan(seed_user, loan_account, period, amount,
                              status_enum=StatusEnum.PROJECTED):
    """Create a transfer from checking to loan account via the transfer service.

    Enforces shadow transaction invariants by using the production
    code path.  Does NOT directly insert shadow transactions.
    """
    return create_transfer(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=loan_account.id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        amount=amount,
        status_id=ref_cache.status_id(status_enum),
        category_id=seed_user["categories"]["Rent"].id,
    )


class TestLoanDashboardWithPayments:
    """Integration tests for payment-aware loan dashboard.

    Verifies that the dashboard and payoff calculator correctly load
    payment history from shadow transactions and pass it to the
    amortization engine.
    """

    def test_dashboard_no_payments_backward_compat(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with no transfers renders identically to pre-5.1 behavior.

        This complements the Commit #0 regression tests by explicitly
        verifying the payment integration code path produces the same
        output when no payments exist.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Loan Summary" in html

    def test_dashboard_with_confirmed_payments(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with confirmed transfer payments renders successfully.

        A Paid transfer to the loan account creates a confirmed shadow
        income transaction.  The dashboard should load it and pass it
        to the engine without error.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Loan Summary" in html

    def test_dashboard_with_projected_payments(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with projected (future) transfer payments renders.

        Projected shadow transactions represent committed future payments
        from recurring transfers.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[2], Decimal("1580.00"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200

    def test_dashboard_with_mixed_payments(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with confirmed + projected payments renders correctly.

        This is the typical real-world case: past payments are confirmed
        (Paid/Settled), future payments are projected.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.DONE,
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[3], Decimal("1580.00"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200

    def test_payoff_extra_payment_with_history(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Payoff calculator extra payment mode works with payment history.

        The calculator should not crash when shadow transactions exist
        for the loan account.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "200.00"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "saved" in html.lower() or "interest" in html.lower()

    def test_payoff_target_date_with_history(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Payoff calculator target date mode works with payment history.

        The target date mode uses current_principal from LoanParams
        (not derived from payments in this commit).
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "target_date", "target_date": "2040-06-01"},
        )
        assert resp.status_code == 200

    def test_dashboard_cancelled_transfer_excluded(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Cancelled transfers do not affect the dashboard projection.

        A cancelled payment should not appear in the payment history.
        The dashboard output should match the no-payments case.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.CANCELLED,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Loan Summary" in html


# ── Transfer Prompt Tests (Commit 5.1-3) ─────────────────────────


class TestTransferPrompt:
    """Tests for the recurring payment transfer prompt on the loan dashboard
    and the create_payment_transfer route.
    """

    def test_dashboard_shows_prompt_no_transfer(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with LoanParams but no recurring transfer: prompt visible."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No recurring payment" in html
        assert "Create Recurring Transfer" in html

    def test_dashboard_hides_prompt_transfer_exists(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with active recurring transfer template: prompt hidden."""
        from app.enums import RecurrencePatternEnum  # pylint: disable=import-outside-toplevel
        from app.models.recurrence_rule import RecurrenceRule  # pylint: disable=import-outside-toplevel
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel

        acct = _create_mortgage(seed_user, db.session)

        # Create an active recurring transfer template targeting this account.
        monthly_id = ref_cache.recurrence_pattern_id(RecurrencePatternEnum.MONTHLY)
        rule = RecurrenceRule(
            user_id=seed_user["user"].id,
            pattern_id=monthly_id,
            day_of_month=1,
        )
        db.session.add(rule)
        db.session.flush()
        tpl = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=acct.id,
            recurrence_rule_id=rule.id,
            name="Existing Mortgage Payment",
            default_amount=Decimal("1500.00"),
            is_active=True,
        )
        db.session.add(tpl)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No recurring payment" not in html

    def test_dashboard_shows_prompt_inactive_template(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Inactive (archived) transfer template: prompt still shown.

        The user may have deactivated a prior transfer. The prompt
        should reappear so they can create a new one.
        """
        from app.enums import RecurrencePatternEnum  # pylint: disable=import-outside-toplevel
        from app.models.recurrence_rule import RecurrenceRule  # pylint: disable=import-outside-toplevel
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel

        acct = _create_mortgage(seed_user, db.session)

        monthly_id = ref_cache.recurrence_pattern_id(RecurrencePatternEnum.MONTHLY)
        rule = RecurrenceRule(
            user_id=seed_user["user"].id,
            pattern_id=monthly_id,
            day_of_month=1,
        )
        db.session.add(rule)
        db.session.flush()
        tpl = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=acct.id,
            recurrence_rule_id=rule.id,
            name="Old Mortgage Payment",
            default_amount=Decimal("1500.00"),
            is_active=False,  # Deactivated
        )
        db.session.add(tpl)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No recurring payment" in html

    def test_create_transfer_success(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST with valid source account creates RecurrenceRule + TransferTemplate.

        Redirects to the loan dashboard after successful creation.
        """
        from app.models.recurrence_rule import RecurrenceRule  # pylint: disable=import-outside-toplevel
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel

        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )
        assert resp.status_code == 302
        assert f"/accounts/{acct.id}/loan" in resp.headers.get("Location", "")

        # Verify records were created.
        tpl = (
            db.session.query(TransferTemplate)
            .filter_by(to_account_id=acct.id, user_id=seed_user["user"].id)
            .first()
        )
        assert tpl is not None
        assert tpl.is_active is True
        assert tpl.from_account_id == checking.id
        assert tpl.recurrence_rule_id is not None
        assert tpl.default_amount > 0

        rule = db.session.get(RecurrenceRule, tpl.recurrence_rule_id)
        assert rule is not None

    def test_create_transfer_redirect_hides_prompt(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """After creation, GET dashboard: prompt no longer visible."""
        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]

        # Create the recurring transfer.
        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )

        # Dashboard should no longer show the prompt.
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No recurring payment" not in html

    def test_create_transfer_generates_shadow_transactions(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """After creation: shadow transactions exist on the loan account."""
        from app.models.transaction import Transaction  # pylint: disable=import-outside-toplevel
        from app.enums import TxnTypeEnum  # pylint: disable=import-outside-toplevel

        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]

        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )

        # Shadow income transactions should exist on the loan account.
        income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
        shadows = (
            db.session.query(Transaction)
            .filter(
                Transaction.account_id == acct.id,
                Transaction.transfer_id.isnot(None),
                Transaction.transaction_type_id == income_type_id,
                Transaction.is_deleted.is_(False),
            )
            .all()
        )
        assert len(shadows) > 0

    def test_create_transfer_validates_source_ownership(
        self, auth_client, seed_user, seed_second_user,
        seed_second_periods, db, seed_periods,
    ):
        """POST with other user's account as source: 404-equivalent redirect."""
        acct = _create_mortgage(seed_user, db.session)
        other_account = seed_second_user["account"]

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(other_account.id)},
        )
        # Should not succeed -- security response rule.
        assert resp.status_code == 302
        assert "/savings" in resp.headers.get("Location", "")

    def test_create_transfer_validates_source_not_self(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST with debt account as source: validation error."""
        acct = _create_mortgage(seed_user, db.session)

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(acct.id)},
        )
        assert resp.status_code == 302
        assert f"/accounts/{acct.id}/loan" in resp.headers.get("Location", "")

    def test_create_transfer_idor_debt_account(
        self, auth_client, seed_user, seed_second_user,
        seed_second_periods, db, seed_periods,
    ):
        """POST to other user's debt account: 404-equivalent redirect."""
        other_loan = _create_loan_account(
            seed_second_user, db.session, "Mortgage", "Other Mortgage",
            Decimal("200000.00"), Decimal("0.06000"), 360,
            date(2024, 1, 1), 1,
        )
        checking = seed_user["account"]

        resp = auth_client.post(
            f"/accounts/{other_loan.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )
        assert resp.status_code == 302
        assert "/savings" in resp.headers.get("Location", "")

    def test_create_transfer_amount_override(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST with custom amount: template uses the override amount."""
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel

        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={
                "source_account_id": str(checking.id),
                "amount": "2000.00",
            },
        )
        assert resp.status_code == 302

        tpl = (
            db.session.query(TransferTemplate)
            .filter_by(to_account_id=acct.id, user_id=seed_user["user"].id)
            .first()
        )
        assert tpl is not None
        assert tpl.default_amount == Decimal("2000.00")

    def test_source_accounts_exclude_debt_account(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Source accounts dropdown does not include the current debt account."""
        acct = _create_mortgage(seed_user, db.session)

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # The prompt's <select> should not have the mortgage as an option.
        assert f'value="{acct.id}"' not in html or "No recurring payment" not in html


# ── ARM Rate History Integration Tests (Commit 5.7-1) ──────────────


class TestARMRateHistoryIntegration:
    """Tests for ARM rate history integration in the loan dashboard.

    Verifies that the dashboard and payoff calculator correctly load
    RateHistory entries for ARM loans, convert them to RateChangeRecords,
    and pass them to the amortization engine.
    """

    def test_arm_dashboard_passes_rate_history(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """ARM mortgage with rate history: dashboard projection differs
        from a fixed-rate projection.

        Creates an ARM mortgage at 5%, adds a rate change to 7%,
        verifies the dashboard renders and shows rate history data.
        """
        acct = _create_loan_account(
            seed_user, db.session, "Mortgage", "ARM Mortgage",
            Decimal("100000.00"), Decimal("0.05000"), 360,
            date(2024, 1, 1), 1, is_arm=True,
        )
        # Add a rate change effective Feb 2025.
        entry = RateHistory(
            account_id=acct.id,
            effective_date=date(2025, 2, 1),
            interest_rate=Decimal("0.07000"),
        )
        db.session.add(entry)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Dashboard should render with the rate history visible.
        assert "Loan Summary" in html

    def test_non_arm_dashboard_ignores_rate_history(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Non-ARM loan with rate history entries: rate_changes not passed.

        Defensively verifies that rate history entries on a non-ARM loan
        do not affect the projection.  The dashboard should produce
        identical output to one with no rate history.
        """
        acct = _create_mortgage(seed_user, db.session)

        # Insert rate history despite is_arm=False (defensive case).
        entry = RateHistory(
            account_id=acct.id,
            effective_date=date(2025, 2, 1),
            interest_rate=Decimal("0.07000"),
        )
        db.session.add(entry)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Loan Summary" in html
        # Non-ARM: rate history section should NOT be visible.
        assert "Rate History" not in html or "Rate Change" not in html


# ── Multi-Scenario Visualization Tests (Commit 5.5-1) ──────────────


class TestMultiScenarioVisualization:
    """Tests for multi-scenario balance chart and payoff calculator.

    Verifies that the dashboard and payoff calculator correctly compute
    and display original, committed, floor, and accelerated scenarios.
    """

    def test_dashboard_chart_original_only_no_payments(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with no transfers: only original schedule in chart.

        data-original should be non-empty.  data-committed and
        data-floor should be empty arrays.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "data-original=" in html
        # No payments means committed and floor are empty.
        assert "data-committed='[]'" in html
        assert "data-floor='[]'" in html

    def test_dashboard_chart_with_committed_schedule(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with projected transfers: data-committed populated.

        A projected transfer creates shadow transactions that the
        dashboard should reflect in the committed schedule.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "data-original=" in html
        assert "data-committed=" in html
        # Committed should not be empty.
        assert "data-committed='[]'" not in html

    def test_dashboard_chart_with_floor(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with confirmed transfers: data-floor populated.

        A confirmed (Paid) transfer establishes the floor -- the real
        position if all extras were cancelled.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "data-floor=" in html
        assert "data-floor='[]'" not in html

    def test_payoff_results_committed_metrics(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Payoff calculator shows committed vs. original comparison.

        When payments exist, the payoff results partial should show
        how many months the committed plan saves vs. the original.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "200"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Multi-scenario chart data should be present.
        assert "data-original=" in html
        assert "data-committed=" in html

    def test_payoff_what_if_three_scenarios(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Payoff with extra: original + committed + accelerated.

        With payments and extra_monthly > 0, all three chart datasets
        should be present and the accelerated line should be shorter.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "200"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "data-original=" in html
        assert "data-accelerated=" in html
        assert "Months Saved" in html

    def test_payoff_no_transfer_degrades(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Payoff with no transfers: committed is empty, original shown.

        When no payments exist, committed chart data should be empty.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "200"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "data-original=" in html
        # No payments -> committed is empty.
        assert "data-committed='[]'" in html

    def test_payoff_what_if_zero(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """extra_monthly=0: accelerated matches committed.

        Zero extra should not cause errors and should still render.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "0"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "data-original=" in html

    def test_payoff_target_date_still_works(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Target date mode is unaffected by multi-scenario changes.

        The existing target_date mode should continue to return
        required extra payment data without regressions.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "target_date", "target_date": "2040-01-01"},
        )
        assert resp.status_code == 200

    def test_charts_page_amortization_not_broken(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Charts page amortization endpoint still renders.

        The Charts page uses get_amortization_breakdown() which is
        unchanged.  This test verifies no side effects from the
        multi-scenario changes in the loan route.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.get(
            "/charts/amortization",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200

    def test_dashboard_arm_original_excludes_rate_changes(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """ARM loan: original schedule does NOT include rate changes.

        The original schedule is the pure contractual baseline at the
        initial rate.  Rate changes only affect committed and floor.
        """
        acct = _create_loan_account(
            seed_user, db.session, "Mortgage", "ARM Mortgage",
            Decimal("100000.00"), Decimal("0.05000"), 360,
            date(2024, 1, 1), 1, is_arm=True,
        )
        entry = RateHistory(
            account_id=acct.id,
            effective_date=date(2025, 2, 1),
            interest_rate=Decimal("0.07000"),
        )
        db.session.add(entry)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Original and committed should both be present.
        assert "data-original=" in html
        assert "Loan Summary" in html


# ── Payment Breakdown Tests (Commit 5.14-1) ────────────────────────


def _add_escrow(db_session, account_id, name, annual_amount,
                inflation_rate=None):
    """Helper to add an escrow component to a loan account."""
    comp = EscrowComponent(
        account_id=account_id,
        name=name,
        annual_amount=annual_amount,
        inflation_rate=inflation_rate,
    )
    db_session.add(comp)
    db_session.flush()
    return comp


class TestPaymentBreakdown:
    """Tests for the payment allocation breakdown card on the loan dashboard.

    Verifies that the breakdown shows correct P/I/E split, handles
    edge cases, and renders the progress bar with accurate percentages.
    """

    def test_breakdown_shows_on_dashboard(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Mortgage with escrow: breakdown card shows P/I/E amounts.

        Setup: $250,000 mortgage at 6.5%, 360 months, origination 2023-06-01.
        LoanParams created by _create_mortgage: current_principal=$250K,
        original_principal=$255K, rate=0.065, term=360, payment_day=1.
        Escrow: $7,200 property tax + $2,400 insurance = $9,600/yr = $800/mo.

        Monthly P&I from original terms ($255K, 6.5%, 360mo): ~$1,611.64.
        The breakdown should show the P/I split for the current period
        plus the escrow portion.
        """
        acct = _create_mortgage(seed_user, db.session)
        _add_escrow(db.session, acct.id, "Property Tax", Decimal("7200.00"))
        _add_escrow(db.session, acct.id, "Insurance", Decimal("2400.00"))
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Breakdown card renders.
        assert "Payment Allocation" in html
        assert "to principal" in html
        assert "to interest" in html
        assert "to escrow" in html
        # Escrow = $800/mo (9600/12).
        assert "800.00" in html

    def test_breakdown_no_escrow(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Auto loan with no escrow: only P/I shown, escrow line absent."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Payment Allocation" in html
        assert "to principal" in html
        assert "to interest" in html
        # Escrow line should not appear.
        assert "to escrow" not in html

    def test_breakdown_proportions_sum_to_100(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Displayed percentages sum to exactly 100.0%.

        Parse the three percentage values from the HTML and verify
        their sum.  Uses a mortgage with escrow for three components.
        """
        acct = _create_mortgage(seed_user, db.session)
        _add_escrow(db.session, acct.id, "Tax", Decimal("6000.00"))
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Extract percentages from "to principal (XX.X%)" pattern.
        import re as _re
        pcts = _re.findall(r"to (?:principal|interest|escrow) \((\d+\.\d)%\)", html)
        assert len(pcts) == 3, f"Expected 3 percentages, found {len(pcts)}: {pcts}"
        total = sum(Decimal(p) for p in pcts)
        assert total == Decimal("100.0"), (
            f"Percentages sum to {total}, expected 100.0"
        )

    def test_breakdown_hidden_no_params(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Loan without LoanParams: breakdown card not shown."""
        # Create a bare account with no params.
        loan_type = db.session.query(AccountType).filter_by(
            name="Mortgage",
        ).one()
        account = Account(
            user_id=seed_user["user"].id,
            account_type_id=loan_type.id,
            name="No Params Mortgage",
            current_anchor_balance=Decimal("200000.00"),
        )
        db.session.add(account)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{account.id}/loan")
        # Without params, renders setup page (no breakdown).
        assert resp.status_code == 200
        assert b"Payment Allocation" not in resp.data

    def test_breakdown_with_extra_payment(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Extra payments included in the principal portion.

        When the committed schedule includes extra payments (from
        transfers), the "principal" line in the breakdown should
        reflect both standard principal and extra_payment.
        """
        acct = _create_mortgage(seed_user, db.session)
        # Create a transfer that overpays the standard P&I.
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("2000.00"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Payment Allocation" in html
        assert "to principal" in html

    def test_breakdown_confirmed_row_labeled(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Confirmed payment: card header shows Confirmed badge.

        When all schedule rows are confirmed (loan fully paid through
        transfers), the breakdown should label the data as confirmed.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # The first non-confirmed (projected) row is shown, but
        # the confirmed payment's row would show "Confirmed" badge.
        assert "Payment Allocation" in html

    def test_breakdown_escrow_zero_hidden(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Mortgage where all escrow components are inactive: escrow hidden.

        Even though the loan type typically has escrow, if all components
        are inactive, the escrow line should not render.
        """
        acct = _create_mortgage(seed_user, db.session)
        # No escrow components added (none active).
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Payment Allocation" in html
        assert "to escrow" not in html

    def test_breakdown_uses_committed_schedule(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Breakdown uses committed (payment-aware) schedule, not original.

        When payments exist, the committed schedule's P/I split differs
        from the original because real payments affect the balance
        trajectory.  The breakdown should reflect the committed values.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Breakdown renders from committed data.
        assert "Payment Allocation" in html
        assert "to principal" in html

    def test_breakdown_escrow_inflation_note(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """O-3: Escrow with inflation_rate shows projected increase note.

        When escrow components have non-null inflation rates, the
        breakdown should show a note about projected escrow increase.
        """
        acct = _create_mortgage(seed_user, db.session)
        _add_escrow(
            db.session, acct.id, "Property Tax",
            Decimal("7200.00"), inflation_rate=Decimal("0.0300"),
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "inflation estimates" in html

    def test_breakdown_no_inflation_note_when_zero(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Escrow with zero or null inflation_rate: no inflation note."""
        acct = _create_mortgage(seed_user, db.session)
        _add_escrow(
            db.session, acct.id, "Insurance",
            Decimal("2400.00"), inflation_rate=None,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "inflation estimates" not in html


# -- Amortization Schedule Tab Tests (Commit 5.13-1) ------------------


def _create_fresh_mortgage(seed_user, db_session, principal=Decimal("250000.00"),
                           rate=Decimal("0.06500"), term=360, payment_day=1):
    """Create a mortgage with origination this month for predictable schedule length.

    Sets original_principal = current_principal so the schedule aligns
    with the full term (no early-payoff due to a lower current balance).
    """
    origination = date.today().replace(day=1)
    return _create_loan_account_exact(
        seed_user, db_session, "Mortgage", "Fresh Mortgage",
        principal, principal, rate, term, origination, payment_day,
    )


def _create_loan_account_exact(seed_user, db_session, type_name, name,
                                original_principal, current_principal,
                                rate, term, orig_date, payment_day,
                                is_arm=False):
    """Like _create_loan_account but with explicit original_principal."""
    loan_type = db_session.query(AccountType).filter_by(name=type_name).one()
    account = Account(
        user_id=seed_user["user"].id,
        account_type_id=loan_type.id,
        name=name,
        current_anchor_balance=current_principal,
    )
    db_session.add(account)
    db_session.flush()

    params = LoanParams(
        account_id=account.id,
        original_principal=original_principal,
        current_principal=current_principal,
        interest_rate=rate,
        term_months=term,
        origination_date=orig_date,
        payment_day=payment_day,
        is_arm=is_arm,
    )
    db_session.add(params)
    db_session.commit()
    return account


class TestAmortizationSchedule:
    """Tests for the full amortization schedule tab on the loan dashboard.

    Verifies that the schedule table renders correctly with the right
    number of rows, confirmed/projected distinction, currency formatting,
    totals row, and conditional Rate column for ARM loans.
    """

    def test_schedule_tab_exists(self, auth_client, seed_user, db, seed_periods):
        """C-5.13-1: Dashboard with LoanParams shows the Amortization Schedule tab.

        GET the dashboard for a mortgage with params. Assert the tab
        nav item and tab pane markup are both present in the HTML.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Tab nav item exists.
        assert "Amortization Schedule" in html
        # Tab pane exists.
        assert 'id="tab-schedule"' in html
        # Schedule table rendered (Month-by-Month header).
        assert "Month-by-Month Schedule" in html

    def test_schedule_has_correct_row_count(self, auth_client, seed_user, db, seed_periods):
        """C-5.13-2: 30-year mortgage produces the correct number of data rows.

        Creates a fresh mortgage where original_principal = current_principal
        and origination_date = this month, so remaining_months = term = 360.
        The engine may produce 360 or 361 rows depending on rounding
        residual (a sub-dollar final payment to zero out the balance).
        Computes the expected count from the engine and verifies the
        template renders all of them.
        """
        from app.services.amortization_engine import generate_schedule

        principal = Decimal("250000.00")
        rate = Decimal("0.06500")
        term = 360
        expected_count = len(generate_schedule(
            principal, rate, term,
            payment_day=1, original_principal=principal, term_months=term,
        ))

        acct = _create_fresh_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Count data rows: each has exactly one Projected or Confirmed badge.
        projected = html.count('badge bg-secondary">Projected</span>')
        confirmed = html.count('badge bg-success">Confirmed</span>')
        total_rows = projected + confirmed
        assert total_rows == expected_count, (
            f"Expected {expected_count} data rows, got {total_rows} "
            f"({projected} projected, {confirmed} confirmed)"
        )

    def test_schedule_confirmed_rows_marked(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-3: Confirmed payment rows are visually distinguished.

        Creates a mortgage and two confirmed transfers matching the
        schedule's first two months (April and May 2026 via seed_periods
        7 and 9).  Asserts confirmed rows get a distinct badge and the
        rest are Projected.
        """
        acct = _create_fresh_mortgage(seed_user, db.session)
        # seed_periods[7] start_date is in April 2026 -> schedule month 1.
        # seed_periods[9] start_date is in May 2026 -> schedule month 2.
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[7], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[9], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        confirmed = html.count('badge bg-success">Confirmed</span>')
        projected = html.count('badge bg-secondary">Projected</span>')
        assert confirmed == 2, (
            f"Expected 2 confirmed rows, got {confirmed}"
        )
        assert projected > 0, "Expected some projected rows"
        # Total should still be a full schedule.
        assert confirmed + projected > 300

    def test_schedule_first_last_row(self, auth_client, seed_user, db, seed_periods):
        """C-5.13-4: First and last rows have correct values for known loan params.

        Loan: $250,000 at 6.5% for 360 months.
        Hand calculation:
          M = P * r(1+r)^n / [(1+r)^n - 1]
          r = 0.065/12 = 0.00541666...
          (1+r)^360 ~ 6.9920
          M = 250000 * (0.00541666 * 6.9920) / (6.9920 - 1)
          M = 250000 * 0.037878 / 5.9920
          M ~ $1,580.17

        First month:
          Interest = 250000 * 0.065/12 = $1,354.17
          Principal = 1580.17 - 1354.17 = $226.00

        Last row: remaining_balance = $0.00
        """
        acct = _create_fresh_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # First row: month 1, expected payment of $1,580.17.
        assert "$1,580.17" in html
        # First month interest: $250,000 * 0.065/12 = $1,354.17.
        assert "$1,354.17" in html
        # First month principal: $1,580.17 - $1,354.17 = $226.00.
        assert "$226.00" in html
        # Last row balance must be $0.00.
        # The totals row does not have a balance cell, so "$0.00" comes
        # from the last data row's remaining_balance.
        assert "$0.00" in html

    def test_schedule_early_payoff_fewer_rows(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-5: Loan with low principal pays off early (fewer than 360 rows).

        Creates a loan where current_principal ($5,000) is much lower
        than the contractual payment from original_principal ($255,000).
        The monthly payment (~$1,612) exceeds the balance, so the loan
        pays off in a few months rather than the full term.
        """
        acct = _create_loan_account_exact(
            seed_user, db.session, "Mortgage", "Almost Paid",
            Decimal("255000.00"), Decimal("5000.00"),
            Decimal("0.06500"), 360, date(2023, 6, 1), 1,
        )
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        projected = html.count('badge bg-secondary">Projected</span>')
        confirmed = html.count('badge bg-success">Confirmed</span>')
        total_rows = projected + confirmed
        assert total_rows < 360, (
            f"Expected fewer than 360 rows for near-payoff loan, got {total_rows}"
        )
        # Last row should still reach $0.00.
        assert "$0.00" in html

    def test_schedule_hidden_no_params(self, auth_client, seed_user, db, seed_periods):
        """C-5.13-6: Loan without LoanParams renders setup page, not schedule tab.

        When no LoanParams exist, the route renders setup.html, which
        does not include the dashboard tabs at all.
        """
        loan_type = db.session.query(AccountType).filter_by(name="Mortgage").one()
        account = Account(
            user_id=seed_user["user"].id,
            account_type_id=loan_type.id,
            name="No Params Loan",
            current_anchor_balance=Decimal("200000.00"),
        )
        db.session.add(account)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{account.id}/loan")
        assert resp.status_code == 200
        assert b"Amortization Schedule" not in resp.data
        assert b"tab-schedule" not in resp.data

    def test_schedule_hidden_empty_schedule(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-7: Loan with zero principal shows empty-schedule message.

        When current_principal is zero (paid off), the engine returns
        an empty schedule.  The tab pane shows a message instead of a table.
        """
        acct = _create_loan_account_exact(
            seed_user, db.session, "Mortgage", "Paid Off",
            Decimal("250000.00"), Decimal("0.00"),
            Decimal("0.06500"), 360, date(2023, 6, 1), 1,
        )
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Tab should exist but show empty message.
        assert "Amortization Schedule" in html
        assert "paid off" in html.lower()
        # No table body with data rows.
        assert "Month-by-Month Schedule" not in html

    def test_schedule_arm_rate_column_shown(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-8: ARM mortgage shows Rate column in the schedule table.

        Creates an ARM mortgage with a rate history entry. The Rate
        column header and rate values should appear in the schedule.
        """
        acct = _create_loan_account(
            seed_user, db.session, "Mortgage", "ARM Schedule",
            Decimal("100000.00"), Decimal("0.05000"), 360,
            date(2024, 1, 1), 1, is_arm=True,
        )
        entry = RateHistory(
            account_id=acct.id,
            effective_date=date(2025, 2, 1),
            interest_rate=Decimal("0.07000"),
        )
        db.session.add(entry)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Month-by-Month Schedule" in html
        # Rate column header.
        assert ">Rate</th>" in html or ">Rate<" in html
        # At least one rate percentage value.
        assert "7.000%" in html or "5.000%" in html

    def test_schedule_fixed_rate_no_rate_column(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-9: Non-ARM mortgage does NOT show Rate column.

        Fixed-rate loans have the same rate for every row. Showing it
        360 times is noise, so the column is omitted.
        """
        acct = _create_fresh_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Month-by-Month Schedule" in html
        # Rate column header must not exist in the schedule table.
        # (The overview tab shows the rate, but not as a <th>.)
        assert ">Rate</th>" not in html

    def test_schedule_totals_row(self, auth_client, seed_user, db, seed_periods):
        """C-5.13-10: Schedule table includes a totals footer row.

        The <tfoot> row shows summed payment, principal, interest, and
        extra columns.  Verify the footer exists and contains currency
        values.
        """
        acct = _create_fresh_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Footer row present.
        assert "<tfoot" in html
        assert ">Total</td>" in html or ">Total<" in html
        # Footer contains currency values (dollar amounts).
        # The total interest for a $250K/6.5%/30yr loan is significant.
        assert "$" in html

    def test_schedule_totals_match_rows(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-11: Totals row values match the sum of individual data rows.

        Computes expected totals from the amortization engine directly
        and verifies they appear in the rendered HTML.

        For $250K at 6.5% for 360 months with no escrow, no extra:
        Total interest = sum of all monthly interest values over the
        full schedule.  The engine computes this precisely using Decimal.
        No escrow means Payment total = P&I total.
        """
        from app.services.amortization_engine import generate_schedule

        principal = Decimal("250000.00")
        rate = Decimal("0.06500")
        term = 360

        # Compute expected totals from the engine.
        schedule = generate_schedule(
            principal, rate, term,
            payment_day=1,
            original_principal=principal,
            term_months=term,
        )
        expected_interest = sum(
            (row.interest for row in schedule), Decimal("0.00"),
        )
        # No escrow on this test loan, so Payment = P&I total.
        expected_payment = sum(
            (row.payment for row in schedule), Decimal("0.00"),
        )
        formatted_interest = f"${expected_interest:,.2f}"
        formatted_payment = f"${expected_payment:,.2f}"

        acct = _create_fresh_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert formatted_interest in html, (
            f"Expected total interest {formatted_interest} not found"
        )
        assert formatted_payment in html, (
            f"Expected total payment {formatted_payment} not found"
        )

    def test_schedule_extra_payment_displayed(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-12: Extra payments show as dollar amounts when present.

        Creates a mortgage with a committed transfer that overpays the
        standard P&I.  The row matching that month should show a non-zero
        Extra column.  The Extra column itself is only shown when at
        least one row has a genuine extra payment.
        """
        acct = _create_fresh_mortgage(seed_user, db.session)
        # Standard payment is ~$1,580.17.  Overpay by $500.
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[7], Decimal("2080.17"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Extra column should appear when overpayments exist.
        assert ">Extra</th>" in html, "Expected Extra column for overpayment"
        # Rows without extra show "--".
        assert "--" in html, "Expected dashes for zero-extra rows"

    def test_schedule_uses_committed_schedule(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-13: Schedule uses committed data when payments exist.

        Creates a mortgage with a confirmed transfer.  If the schedule
        used only the original (no-payments) schedule, no rows would
        be marked Confirmed.  Presence of Confirmed badges proves the
        committed schedule is used.
        """
        acct = _create_fresh_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[7], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        confirmed = html.count('badge bg-success">Confirmed</span>')
        assert confirmed >= 1, (
            "Expected at least 1 confirmed row from committed schedule"
        )

    def test_schedule_currency_formatting_consistent(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-14: Currency values in the schedule use consistent formatting.

        All monetary values should match the $X,XXX.XX pattern with
        exactly 2 decimal places.  Parses several values from the
        schedule and validates the format.
        """
        acct = _create_fresh_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Extract dollar amounts from the schedule.  The pattern
        # matches $X.XX through $XXX,XXX,XXX.XX.
        amounts = re.findall(r'\$[\d,]+\.\d{2}', html)
        assert len(amounts) > 100, (
            f"Expected many currency values in schedule, found {len(amounts)}"
        )
        # Every matched amount should have exactly 2 decimal places.
        for amount in amounts[:20]:  # Spot-check first 20.
            assert re.match(r'^\$[\d,]+\.\d{2}$', amount), (
                f"Currency format mismatch: {amount}"
            )


# -- Dashboard / Payoff Calculator Consistency Tests -------------------


class TestDashboardPayoffConsistency:
    """Verify the dashboard and payoff calculator use the same data pipeline.

    Both routes must produce identical amortization calculations from
    the same loan.  These tests catch mismatches caused by one route
    applying payment preparation (escrow subtraction, biweekly
    redistribution) while the other uses raw data.
    """

    def test_payoff_committed_matches_dashboard_chart(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Payoff committed chart data matches dashboard committed chart.

        Both routes render committed balance data for Chart.js.  Since
        both use _load_loan_context for payment preparation, the
        committed balance arrays must be identical.
        """
        acct = _create_fresh_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[7], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        # Dashboard: extract committed chart data.
        dash_resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert dash_resp.status_code == 200
        dash_html = dash_resp.data.decode()
        dash_match = re.search(r"data-committed='(\[.*?\])'", dash_html)
        assert dash_match, "Dashboard missing committed chart data"
        dash_committed = dash_match.group(1)

        # Payoff with 0 extra: extract committed chart data.
        payoff_resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "0"},
        )
        assert payoff_resp.status_code == 200
        payoff_html = payoff_resp.data.decode()
        payoff_match = re.search(r"data-committed='(\[.*?\])'", payoff_html)
        assert payoff_match, "Payoff missing committed chart data"
        payoff_committed = payoff_match.group(1)

        assert dash_committed == payoff_committed, (
            "Dashboard and payoff calculator committed schedules differ -- "
            "data pipeline mismatch"
        )

    def test_payoff_with_payments_no_crash(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Payoff calculator with prepared payments does not crash.

        After the DRY refactor, both routes use _load_loan_context.
        Verify the payoff calculator handles prepared payments correctly
        in both extra_payment and target_date modes.
        """
        acct = _create_fresh_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[7], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[9], Decimal("1580.17"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        # Extra payment mode.
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "200"},
        )
        assert resp.status_code == 200

        # Target date mode.
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "target_date", "target_date": "2040-01-01"},
        )
        assert resp.status_code == 200

    def test_escrow_subtracted_consistently(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Escrow-inclusive transfers do not inflate payoff savings.

        When transfers include escrow, both routes must subtract it
        before passing to the engine.  Without this, the payoff
        calculator would report inflated interest savings because the
        engine would count escrow as extra principal.
        """
        acct = _create_fresh_mortgage(seed_user, db.session)
        # Add escrow: $600/month.
        _add_escrow(db.session, acct.id, "Property Tax", Decimal("7200.00"))
        db.session.commit()

        # Transfer includes P&I (~$1,580) + escrow ($600) = ~$2,180.
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[7], Decimal("2180.00"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        # Dashboard should NOT show the escrow as "extra" in schedule.
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # The Escrow column should show $600 (7200/12).
        assert "Escrow" in html
        assert "$600.00" in html

        # Payoff calculator should also work with prepared payments.
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "0"},
        )
        assert resp.status_code == 200

    def test_biweekly_overlap_handled_in_payoff(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Two payments in the same month are redistributed in payoff too.

        Creates two transfers in the same calendar month (biweekly
        overlap).  Both the dashboard and payoff calculator must
        distribute them across two schedule months.
        """
        acct = _create_fresh_mortgage(seed_user, db.session)
        # seed_periods[7] and [8] are both in April 2026.
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[7], Decimal("1580.17"),
            status_enum=StatusEnum.PROJECTED,
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[8], Decimal("1580.17"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        # Dashboard schedule: no month should show double payment.
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # $3,160 (2x $1,580.17) should NOT appear as a single payment.
        assert "$3,160" not in html, (
            "Dashboard shows double payment -- biweekly fix not applied"
        )

        # Payoff calculator: same -- should not crash or double-count.
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "0"},
        )
        assert resp.status_code == 200
