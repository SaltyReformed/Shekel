"""
Tests for investment/retirement account routes.
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.models.ref import AccountType


def _create_investment_account(seed_user, db_session, type_name="401k",
                                name="My 401k", balance="50000.00"):
    """Helper to create an investment/retirement account."""
    acct_type = db_session.query(AccountType).filter_by(name=type_name).one()
    account = Account(
        user_id=seed_user["user"].id,
        account_type_id=acct_type.id,
        name=name,
        current_anchor_balance=Decimal(balance),
    )
    db_session.add(account)
    db_session.flush()
    return account


def _create_investment_params(db_session, account_id, **overrides):
    """Helper to create investment params for an account."""
    defaults = {
        "account_id": account_id,
        "assumed_annual_return": Decimal("0.07000"),
        "annual_contribution_limit": Decimal("23500.00"),
        "contribution_limit_year": 2026,
        "employer_contribution_type": "none",
    }
    defaults.update(overrides)
    params = InvestmentParams(**defaults)
    db_session.add(params)
    db_session.commit()
    return params


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


class TestInvestmentDashboard:
    """Tests for the investment dashboard page."""

    def test_dashboard_no_params(self, auth_client, seed_user, db, seed_periods):
        """GET returns 200 even without investment params."""
        acct = _create_investment_account(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        assert b"50,000.00" in resp.data

    def test_dashboard_with_params(self, auth_client, seed_user, db, seed_periods):
        """GET returns 200 with params and projection data."""
        acct = _create_investment_account(seed_user, db.session)
        acct.current_anchor_period_id = seed_periods[0].id
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        assert b"50,000.00" in resp.data

    def test_dashboard_idor(self, auth_client, seed_user, db, seed_periods):
        """Other user's account → redirect."""
        other_user = _create_other_user(db.session)
        acct_type = db.session.query(AccountType).filter_by(name="401k").one()
        other_acct = Account(
            user_id=other_user.id,
            account_type_id=acct_type.id,
            name="Other 401k",
            current_anchor_balance=Decimal("10000.00"),
        )
        db.session.add(other_acct)
        db.session.commit()
        resp = auth_client.get(f"/accounts/{other_acct.id}/investment")
        assert resp.status_code == 302

    def test_dashboard_nonexistent(self, auth_client, seed_user, db, seed_periods):
        """Nonexistent account → redirect."""
        resp = auth_client.get("/accounts/99999/investment")
        assert resp.status_code == 302

    def test_dashboard_brokerage(self, auth_client, seed_user, db, seed_periods):
        """Brokerage account (no contribution limit) works."""
        acct = _create_investment_account(
            seed_user, db.session, type_name="brokerage",
            name="Brokerage", balance="25000.00",
        )
        _create_investment_params(
            db.session, acct.id,
            annual_contribution_limit=None,
            contribution_limit_year=None,
        )
        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200


class TestInvestmentParams:
    """Tests for creating/updating investment params."""

    def test_create_params(self, auth_client, seed_user, db, seed_periods):
        """POST creates new investment params."""
        acct = _create_investment_account(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/params",
            data={
                "assumed_annual_return": "7",
                "annual_contribution_limit": "23500",
                "contribution_limit_year": "2026",
                "employer_contribution_type": "none",
            },
        )
        assert resp.status_code == 302
        params = db.session.query(InvestmentParams).filter_by(
            account_id=acct.id
        ).first()
        assert params is not None
        assert params.assumed_annual_return == Decimal("0.07000")

    def test_update_params(self, auth_client, seed_user, db, seed_periods):
        """POST updates existing investment params."""
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)
        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/params",
            data={
                "assumed_annual_return": "8",
                "annual_contribution_limit": "23500",
                "contribution_limit_year": "2026",
                "employer_contribution_type": "none",
            },
        )
        assert resp.status_code == 302
        params = db.session.query(InvestmentParams).filter_by(
            account_id=acct.id
        ).first()
        assert params.assumed_annual_return == Decimal("0.08000")

    def test_create_params_with_employer_match(self, auth_client, seed_user, db, seed_periods):
        """POST with employer match config."""
        acct = _create_investment_account(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/params",
            data={
                "assumed_annual_return": "7",
                "annual_contribution_limit": "23500",
                "contribution_limit_year": "2026",
                "employer_contribution_type": "match",
                "employer_match_percentage": "100",
                "employer_match_cap_percentage": "6",
            },
        )
        assert resp.status_code == 302
        params = db.session.query(InvestmentParams).filter_by(
            account_id=acct.id
        ).first()
        assert params is not None
        assert params.employer_contribution_type == "match"
        assert params.employer_match_percentage == Decimal("1.0000")
        assert params.employer_match_cap_percentage == Decimal("0.0600")

    def test_params_idor(self, auth_client, seed_user, db, seed_periods):
        """Cannot update params on another user's account."""
        other_user = _create_other_user(db.session)
        acct_type = db.session.query(AccountType).filter_by(name="401k").one()
        other_acct = Account(
            user_id=other_user.id,
            account_type_id=acct_type.id,
            name="Other 401k",
            current_anchor_balance=Decimal("10000.00"),
        )
        db.session.add(other_acct)
        db.session.commit()
        resp = auth_client.post(
            f"/accounts/{other_acct.id}/investment/params",
            data={
                "assumed_annual_return": "7",
                "employer_contribution_type": "none",
            },
        )
        assert resp.status_code == 302

    def test_validation_error(self, auth_client, seed_user, db, seed_periods):
        """Invalid data flashes error and redirects."""
        acct = _create_investment_account(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/params",
            data={
                "assumed_annual_return": "not_a_number",
                "employer_contribution_type": "none",
            },
        )
        assert resp.status_code == 302


class TestGrowthChartFragment:
    """Tests for the investment growth chart HTMX fragment (U2)."""

    def test_growth_chart_redirects_without_htmx(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """GET without HX-Request header redirects to dashboard."""
        acct = _create_investment_account(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/investment/growth-chart")
        assert resp.status_code == 302

    def test_growth_chart_empty_without_params(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Returns empty state when no investment params exist."""
        acct = _create_investment_account(seed_user, db.session)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert b"No projection data" in resp.data

    def test_growth_chart_with_data(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Returns canvas element when projection data exists."""
        acct = _create_investment_account(seed_user, db.session)
        acct.current_anchor_period_id = seed_periods[0].id
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart?horizon_years=2",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert b"growthChart" in resp.data

    def test_growth_chart_idor(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Other user's account returns 404."""
        other_user = _create_other_user(db.session)
        acct_type = db.session.query(AccountType).filter_by(name="401k").one()
        other_acct = Account(
            user_id=other_user.id,
            account_type_id=acct_type.id,
            name="Other 401k",
            current_anchor_balance=Decimal("10000.00"),
        )
        db.session.add(other_acct)
        db.session.commit()
        resp = auth_client.get(
            f"/accounts/{other_acct.id}/investment/growth-chart",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 404
