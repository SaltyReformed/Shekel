"""
Tests for mortgage routes.
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.mortgage_params import MortgageParams, MortgageRateHistory, EscrowComponent
from app.models.ref import AccountType


def _create_mortgage_account(seed_user, db_session, name="My Mortgage"):
    """Helper to create a mortgage account with params."""
    mortgage_type = db_session.query(AccountType).filter_by(name="mortgage").one()
    account = Account(
        user_id=seed_user["user"].id,
        account_type_id=mortgage_type.id,
        name=name,
        current_anchor_balance=Decimal("250000.00"),
    )
    db_session.add(account)
    db_session.flush()

    params = MortgageParams(
        account_id=account.id,
        original_principal=Decimal("300000.00"),
        current_principal=Decimal("250000.00"),
        interest_rate=Decimal("0.06500"),
        term_months=360,
        origination_date=date(2023, 6, 1),
        payment_day=1,
    )
    db_session.add(params)
    db_session.commit()
    return account


def _create_other_user(db_session):
    """Create a second user for IDOR tests."""
    from app.services.auth_service import hash_password
    from app.models.user import User, UserSettings

    user = User(
        email="other@shekel.local",
        password_hash=hash_password("otherpass"),
        display_name="Other User",
    )
    db_session.add(user)
    db_session.flush()

    settings = UserSettings(user_id=user.id)
    db_session.add(settings)
    db_session.commit()
    return user


class TestMortgageDashboard:
    """Tests for the mortgage dashboard page."""

    def test_dashboard_view(self, auth_client, seed_user, db, seed_periods):
        """GET returns 200 with summary data."""
        acct = _create_mortgage_account(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/mortgage")
        assert resp.status_code == 200
        assert b"Loan Summary" in resp.data
        assert b"250,000.00" in resp.data

    def test_dashboard_idor(self, auth_client, seed_user, db, seed_periods):
        """Other user's mortgage → redirect."""
        other_user = _create_other_user(db.session)
        mortgage_type = db.session.query(AccountType).filter_by(name="mortgage").one()
        other_acct = Account(
            user_id=other_user.id,
            account_type_id=mortgage_type.id,
            name="Other Mortgage",
            current_anchor_balance=Decimal("100000.00"),
        )
        db.session.add(other_acct)
        db.session.flush()
        params = MortgageParams(
            account_id=other_acct.id,
            original_principal=Decimal("100000.00"),
            current_principal=Decimal("100000.00"),
            interest_rate=Decimal("0.05000"),
            term_months=360,
            origination_date=date(2024, 1, 1),
            payment_day=1,
        )
        db.session.add(params)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{other_acct.id}/mortgage")
        assert resp.status_code == 302

    def test_dashboard_wrong_type(self, auth_client, seed_user, db, seed_periods):
        """Non-mortgage account → redirect."""
        # seed_user has a checking account
        acct = seed_user["account"]
        resp = auth_client.get(f"/accounts/{acct.id}/mortgage")
        assert resp.status_code == 302

    def test_dashboard_nonexistent(self, auth_client, seed_user, db, seed_periods):
        """Bad ID → redirect."""
        resp = auth_client.get("/accounts/99999/mortgage")
        assert resp.status_code == 302

    def test_dashboard_login_required(self, client, seed_user, db, seed_periods):
        """Unauthenticated → redirect to login."""
        acct = _create_mortgage_account(seed_user, db.session)
        resp = client.get(f"/accounts/{acct.id}/mortgage")
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")


class TestMortgageParamsUpdate:
    """Tests for updating mortgage parameters."""

    def test_params_update(self, auth_client, seed_user, db, seed_periods):
        """POST valid params → updates."""
        acct = _create_mortgage_account(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/mortgage/params",
            data={
                "current_principal": "240000.00",
                "interest_rate": "0.06000",
                "payment_day": "15",
            },
        )
        assert resp.status_code == 302

        params = db.session.query(MortgageParams).filter_by(account_id=acct.id).one()
        assert params.current_principal == Decimal("240000.00")
        assert params.interest_rate == Decimal("0.06000")
        assert params.payment_day == 15

    def test_params_update_validation(self, auth_client, seed_user, db, seed_periods):
        """POST invalid → error."""
        acct = _create_mortgage_account(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/mortgage/params",
            data={"payment_day": "32"},  # Invalid
        )
        assert resp.status_code == 302

    def test_params_update_idor(self, auth_client, seed_user, db, seed_periods):
        """Other user → redirect."""
        other_user = _create_other_user(db.session)
        mortgage_type = db.session.query(AccountType).filter_by(name="mortgage").one()
        other_acct = Account(
            user_id=other_user.id,
            account_type_id=mortgage_type.id,
            name="Other Mortgage",
        )
        db.session.add(other_acct)
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{other_acct.id}/mortgage/params",
            data={"current_principal": "100"},
        )
        assert resp.status_code == 302


class TestRateChange:
    """Tests for ARM rate change recording."""

    def test_rate_change_create(self, auth_client, seed_user, db, seed_periods):
        """POST rate change → creates history row."""
        acct = _create_mortgage_account(seed_user, db.session)
        params = db.session.query(MortgageParams).filter_by(account_id=acct.id).one()
        params.is_arm = True
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/mortgage/rate",
            data={
                "effective_date": "2026-04-01",
                "interest_rate": "0.07000",
                "notes": "Rate adjustment",
            },
        )
        assert resp.status_code == 200

        entry = db.session.query(MortgageRateHistory).filter_by(account_id=acct.id).first()
        assert entry is not None
        assert entry.interest_rate == Decimal("0.07000")

    def test_rate_change_validation(self, auth_client, seed_user, db, seed_periods):
        """Invalid rate → error."""
        acct = _create_mortgage_account(seed_user, db.session)
        params = db.session.query(MortgageParams).filter_by(account_id=acct.id).one()
        params.is_arm = True
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/mortgage/rate",
            data={"interest_rate": "2.0"},  # > 1, invalid
        )
        assert resp.status_code == 400


class TestEscrow:
    """Tests for escrow component management."""

    def test_escrow_add(self, auth_client, seed_user, db, seed_periods):
        """POST escrow component → creates."""
        acct = _create_mortgage_account(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/mortgage/escrow",
            data={
                "name": "Property Tax",
                "annual_amount": "4800.00",
                "inflation_rate": "0.03",
            },
        )
        assert resp.status_code == 200
        assert b"Property Tax" in resp.data

        comp = db.session.query(EscrowComponent).filter_by(account_id=acct.id).first()
        assert comp is not None
        assert comp.name == "Property Tax"
        assert comp.annual_amount == Decimal("4800.00")

    def test_escrow_add_duplicate_name(self, auth_client, seed_user, db, seed_periods):
        """Duplicate name → error."""
        acct = _create_mortgage_account(seed_user, db.session)
        # Add first component.
        comp = EscrowComponent(
            account_id=acct.id,
            name="Insurance",
            annual_amount=Decimal("2400.00"),
        )
        db.session.add(comp)
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/mortgage/escrow",
            data={"name": "Insurance", "annual_amount": "3000.00"},
        )
        assert resp.status_code == 400

    def test_escrow_delete(self, auth_client, seed_user, db, seed_periods):
        """POST delete → deactivates component."""
        acct = _create_mortgage_account(seed_user, db.session)
        comp = EscrowComponent(
            account_id=acct.id,
            name="Old Insurance",
            annual_amount=Decimal("1200.00"),
        )
        db.session.add(comp)
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/mortgage/escrow/{comp.id}/delete",
        )
        assert resp.status_code == 200

        db.session.refresh(comp)
        assert comp.is_active is False

    def test_escrow_delete_idor(self, auth_client, seed_user, db, seed_periods):
        """Other user's escrow → error."""
        other_user = _create_other_user(db.session)
        mortgage_type = db.session.query(AccountType).filter_by(name="mortgage").one()
        other_acct = Account(
            user_id=other_user.id,
            account_type_id=mortgage_type.id,
            name="Other Mortgage",
        )
        db.session.add(other_acct)
        db.session.flush()
        params = MortgageParams(
            account_id=other_acct.id,
            original_principal=Decimal("100000.00"),
            current_principal=Decimal("100000.00"),
            interest_rate=Decimal("0.05000"),
            term_months=360,
            origination_date=date(2024, 1, 1),
            payment_day=1,
        )
        db.session.add(params)
        comp = EscrowComponent(
            account_id=other_acct.id,
            name="Tax",
            annual_amount=Decimal("3000.00"),
        )
        db.session.add(comp)
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{other_acct.id}/mortgage/escrow/{comp.id}/delete",
        )
        assert resp.status_code == 404


class TestPayoffCalculator:
    """Tests for the payoff calculator."""

    def test_payoff_extra_payment(self, auth_client, seed_user, db, seed_periods):
        """POST payoff with extra → results fragment."""
        acct = _create_mortgage_account(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/mortgage/payoff",
            data={"mode": "extra_payment", "extra_monthly": "200"},
        )
        assert resp.status_code == 200
        assert b"Months Saved" in resp.data

    def test_payoff_target_date(self, auth_client, seed_user, db, seed_periods):
        """POST payoff with target date → results fragment."""
        acct = _create_mortgage_account(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/mortgage/payoff",
            data={"mode": "target_date", "target_date": "2040-01-01"},
        )
        assert resp.status_code == 200

    def test_payoff_validation(self, auth_client, seed_user, db, seed_periods):
        """Invalid input → error."""
        acct = _create_mortgage_account(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/mortgage/payoff",
            data={"mode": "invalid_mode"},
        )
        assert resp.status_code == 200
        assert b"Validation error" in resp.data


class TestPayoffSlider:
    """Tests for the payoff calculator slider (U1)."""

    def test_dashboard_has_extra_payment_slider(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Mortgage dashboard renders a range slider for extra payment."""
        from app.models.mortgage_params import MortgageParams

        mortgage_type = db.session.query(AccountType).filter_by(name="mortgage").one()
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

        resp = auth_client.get(f"/accounts/{account.id}/mortgage")
        assert resp.status_code == 200
        assert b'data-slider-group="payoff"' in resp.data
        assert b'type="range"' in resp.data
        assert b'chart_slider.js' in resp.data


class TestCreateMortgageAccount:
    """Test creating a mortgage account redirects correctly."""

    def test_create_mortgage_account(self, auth_client, seed_user, db, seed_periods):
        """Creating mortgage account type redirects to mortgage dashboard."""
        mortgage_type = db.session.query(AccountType).filter_by(name="mortgage").one()
        resp = auth_client.post(
            "/accounts",
            data={
                "name": "New Mortgage",
                "account_type_id": str(mortgage_type.id),
                "anchor_balance": "200000",
            },
        )
        assert resp.status_code == 302
        assert "/mortgage" in resp.headers.get("Location", "")
