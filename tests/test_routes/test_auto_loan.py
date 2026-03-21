"""
Tests for auto loan routes.
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.auto_loan_params import AutoLoanParams
from app.models.ref import AccountType


def _create_auto_loan_account(seed_user, db_session, name="My Auto Loan"):
    """Helper to create an auto loan account with params."""
    auto_loan_type = db_session.query(AccountType).filter_by(name="auto_loan").one()
    account = Account(
        user_id=seed_user["user"].id,
        account_type_id=auto_loan_type.id,
        name=name,
        current_anchor_balance=Decimal("25000.00"),
    )
    db_session.add(account)
    db_session.flush()

    params = AutoLoanParams(
        account_id=account.id,
        original_principal=Decimal("30000.00"),
        current_principal=Decimal("25000.00"),
        interest_rate=Decimal("0.05000"),
        term_months=60,
        origination_date=date(2025, 1, 1),
        payment_day=15,
    )
    db_session.add(params)
    db_session.commit()
    return account


def _create_other_auto_loan(second_user, db_session):
    """Create an auto loan account owned by the second user.

    Builds on the shared second_user fixture. Returns the Account
    with AutoLoanParams already attached.
    """
    auto_type = db_session.query(AccountType).filter_by(
        name="auto_loan"
    ).one()
    account = Account(
        user_id=second_user["user"].id,
        account_type_id=auto_type.id,
        name="Other Auto Loan",
        current_anchor_balance=Decimal("15000.00"),
    )
    db_session.add(account)
    db_session.flush()

    params = AutoLoanParams(
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


class TestAutoLoanDashboard:
    """Tests for the auto loan dashboard page."""

    def test_dashboard_view(self, auth_client, seed_user, db, seed_periods):
        """GET returns 200 with summary."""
        acct = _create_auto_loan_account(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/auto-loan")
        assert resp.status_code == 200
        assert b"Loan Summary" in resp.data
        assert b"25,000.00" in resp.data

    def test_dashboard_idor(
        self, auth_client, second_user, db, seed_periods,
    ):
        """GET another user's auto loan dashboard is rejected
        and does not leak victim data."""
        other_acct = _create_other_auto_loan(second_user, db.session)

        resp = auth_client.get(f"/accounts/{other_acct.id}/auto-loan")
        assert resp.status_code == 302
        location = resp.headers.get("Location", "")
        assert "/savings" in location, (
            f"IDOR redirect went to {location}, expected /savings"
        )
        assert b"Other Auto Loan" not in resp.data, (
            "IDOR response leaked victim's account name"
        )

    def test_dashboard_wrong_type(self, auth_client, seed_user, db, seed_periods):
        """Non-auto-loan → redirect to savings dashboard."""
        acct = seed_user["account"]
        resp = auth_client.get(f"/accounts/{acct.id}/auto-loan")
        assert resp.status_code == 302
        assert "/savings" in resp.headers.get("Location", "")

    def test_dashboard_login_required(self, client, seed_user, db, seed_periods):
        """Unauthenticated → redirect."""
        acct = _create_auto_loan_account(seed_user, db.session)
        resp = client.get(f"/accounts/{acct.id}/auto-loan")
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")


class TestAutoLoanParamsUpdate:
    """Tests for updating auto loan parameters."""

    def test_params_update(self, auth_client, seed_user, db, seed_periods):
        """POST valid → updates."""
        acct = _create_auto_loan_account(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/auto-loan/params",
            data={
                "current_principal": "22000.00",
                "interest_rate": "4.500",
                "payment_day": "1",
            },
        )
        assert resp.status_code == 302

        params = db.session.query(AutoLoanParams).filter_by(account_id=acct.id).one()
        assert params.current_principal == Decimal("22000.00")
        assert params.interest_rate == Decimal("0.04500")

    def test_params_update_validation(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST invalid data is rejected and leaves DB unchanged."""
        acct = _create_auto_loan_account(seed_user, db.session)

        # Snapshot mutable fields before the request.
        original = db.session.query(AutoLoanParams).filter_by(
            account_id=acct.id
        ).one()
        orig_principal = original.current_principal
        orig_rate = original.interest_rate
        orig_day = original.payment_day

        resp = auth_client.post(
            f"/accounts/{acct.id}/auto-loan/params",
            data={"payment_day": "32"},  # Invalid
        )
        assert resp.status_code == 302

        # Verify DB unchanged after invalid submission.
        db.session.expire_all()
        after = db.session.query(AutoLoanParams).filter_by(
            account_id=acct.id
        ).one()
        assert after.current_principal == orig_principal, (
            "Validation failure modified current_principal!"
        )
        assert after.interest_rate == orig_rate, (
            "Validation failure modified interest_rate!"
        )
        assert after.payment_day == orig_day, (
            "Validation failure modified payment_day!"
        )

    def test_params_update_idor(
        self, auth_client, second_user, db, seed_periods,
    ):
        """POST to another user's auto loan params is rejected
        and leaves the victim's data completely unchanged."""
        # Phase A: Setup victim's data with known values.
        other_acct = _create_other_auto_loan(second_user, db.session)
        original = db.session.query(AutoLoanParams).filter_by(
            account_id=other_acct.id
        ).one()
        orig_principal = original.current_principal
        orig_rate = original.interest_rate
        orig_day = original.payment_day

        # Phase B: Attack -- auth_client is User 1, other_acct is User 2's.
        resp = auth_client.post(
            f"/accounts/{other_acct.id}/auto-loan/params",
            data={
                "current_principal": "1.00",
                "interest_rate": "0.99000",
                "payment_day": "28",
            },
        )

        # Phase C: Verify no state change.
        assert resp.status_code == 302
        location = resp.headers.get("Location", "")
        assert "/savings" in location, (
            f"IDOR redirect went to {location}, expected /savings"
        )

        db.session.expire_all()
        after = db.session.query(AutoLoanParams).filter_by(
            account_id=other_acct.id
        ).one()
        assert after.current_principal == orig_principal, (
            "IDOR attack modified current_principal!"
        )
        assert after.interest_rate == orig_rate, (
            "IDOR attack modified interest_rate!"
        )
        assert after.payment_day == orig_day, (
            "IDOR attack modified payment_day!"
        )


class TestAutoLoanNegativePaths:
    """Negative-path and boundary tests for auto loan routes."""

    def test_params_update_negative_interest_rate(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Negative interest rate is rejected by Range(min=0) validator."""
        acct = _create_auto_loan_account(seed_user, db.session)
        orig = db.session.query(AutoLoanParams).filter_by(account_id=acct.id).one()
        orig_rate = orig.interest_rate

        resp = auth_client.post(
            f"/accounts/{acct.id}/auto-loan/params",
            data={
                "current_principal": "25000.00",
                "interest_rate": "-0.01",
                "payment_day": "15",
            },
        )
        assert resp.status_code == 302

        db.session.expire_all()
        after = db.session.query(AutoLoanParams).filter_by(account_id=acct.id).one()
        assert after.interest_rate == orig_rate

    def test_params_update_payment_day_zero(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Payment day 0 is rejected by Range(min=1) validator."""
        acct = _create_auto_loan_account(seed_user, db.session)
        orig = db.session.query(AutoLoanParams).filter_by(account_id=acct.id).one()
        orig_day = orig.payment_day

        resp = auth_client.post(
            f"/accounts/{acct.id}/auto-loan/params",
            data={
                "current_principal": "25000.00",
                "interest_rate": "0.05000",
                "payment_day": "0",
            },
        )
        assert resp.status_code == 302

        db.session.expire_all()
        after = db.session.query(AutoLoanParams).filter_by(account_id=acct.id).one()
        assert after.payment_day == orig_day

    def test_params_update_payment_day_32(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Payment day 32 is rejected by Range(max=31) validator."""
        acct = _create_auto_loan_account(seed_user, db.session)
        orig = db.session.query(AutoLoanParams).filter_by(account_id=acct.id).one()
        orig_day = orig.payment_day

        resp = auth_client.post(
            f"/accounts/{acct.id}/auto-loan/params",
            data={
                "current_principal": "25000.00",
                "interest_rate": "0.05000",
                "payment_day": "32",
            },
        )
        assert resp.status_code == 302

        db.session.expire_all()
        after = db.session.query(AutoLoanParams).filter_by(account_id=acct.id).one()
        assert after.payment_day == orig_day

    def test_params_update_non_numeric_principal(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Non-numeric principal is rejected and DB unchanged."""
        acct = _create_auto_loan_account(seed_user, db.session)
        orig = db.session.query(AutoLoanParams).filter_by(account_id=acct.id).one()
        orig_principal = orig.current_principal

        resp = auth_client.post(
            f"/accounts/{acct.id}/auto-loan/params",
            data={
                "current_principal": "abc",
                "interest_rate": "0.05000",
                "payment_day": "15",
            },
        )
        assert resp.status_code == 302

        db.session.expire_all()
        after = db.session.query(AutoLoanParams).filter_by(account_id=acct.id).one()
        assert after.current_principal == orig_principal

    def test_params_update_nonexistent_account(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST to nonexistent account redirects with flash."""
        resp = auth_client.post(
            "/accounts/999999/auto-loan/params",
            data={
                "current_principal": "20000.00",
                "interest_rate": "0.04000",
                "payment_day": "1",
            },
        )
        assert resp.status_code == 302
        assert "/savings" in resp.headers.get("Location", "")
        resp2 = auth_client.get(resp.headers["Location"])
        assert b"Auto loan account not found." in resp2.data

    def test_params_update_wrong_account_type(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST auto loan params to checking account is rejected."""
        checking_acct = seed_user["account"]
        resp = auth_client.post(
            f"/accounts/{checking_acct.id}/auto-loan/params",
            data={
                "current_principal": "20000.00",
                "interest_rate": "0.04000",
                "payment_day": "1",
            },
        )
        assert resp.status_code == 302
        assert "/savings" in resp.headers.get("Location", "")
        # _load_auto_loan_account returns (None, None) for wrong type.
        resp2 = auth_client.get(resp.headers["Location"])
        assert b"Auto loan account not found." in resp2.data

    def test_create_params_already_configured(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST setup when params already exist redirects with info flash."""
        acct = _create_auto_loan_account(seed_user, db.session)
        orig = db.session.query(AutoLoanParams).filter_by(account_id=acct.id).one()
        orig_principal = orig.current_principal
        orig_rate = orig.interest_rate

        resp = auth_client.post(
            f"/accounts/{acct.id}/auto-loan/setup",
            data={
                "original_principal": "99999.00",
                "current_principal": "99999.00",
                "interest_rate": "0.99000",
                "term_months": "12",
                "origination_date": "2025-01-01",
                "payment_day": "1",
            },
        )
        assert resp.status_code == 302
        assert "/auto-loan" in resp.headers.get("Location", "")
        resp2 = auth_client.get(resp.headers["Location"])
        assert b"Auto loan parameters already configured." in resp2.data

        # Verify original params are unchanged.
        db.session.expire_all()
        after = db.session.query(AutoLoanParams).filter_by(account_id=acct.id).one()
        assert after.current_principal == orig_principal
        assert after.interest_rate == orig_rate


class TestCreateAutoLoanAccount:
    """Test creating an auto loan account redirects correctly."""

    def test_create_auto_loan_account(self, auth_client, seed_user, db, seed_periods):
        """Creating auto loan account type redirects to auto loan dashboard."""
        auto_type = db.session.query(AccountType).filter_by(name="auto_loan").one()
        resp = auth_client.post(
            "/accounts",
            data={
                "name": "New Auto Loan",
                "account_type_id": str(auto_type.id),
                "anchor_balance": "20000",
            },
        )
        assert resp.status_code == 302
        assert "/auto-loan" in resp.headers.get("Location", "")

        # Verify DB record was created.
        acct = db.session.query(Account).filter_by(
            user_id=seed_user["user"].id, name="New Auto Loan",
        ).one()
        assert acct.account_type_id == auto_type.id
        assert acct.current_anchor_balance == Decimal("20000")
