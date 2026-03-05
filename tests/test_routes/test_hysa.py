"""
Tests for HYSA routes (detail view and params update).
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.hysa_params import HysaParams
from app.models.ref import AccountType
from app.services import pay_period_service


def _create_hysa_account(seed_user, db_session, name="My HYSA"):
    """Helper to create a HYSA account with params."""
    hysa_type = db_session.query(AccountType).filter_by(name="hysa").one()
    account = Account(
        user_id=seed_user["user"].id,
        account_type_id=hysa_type.id,
        name=name,
        current_anchor_balance=Decimal("10000.00"),
    )
    db_session.add(account)
    db_session.flush()

    params = HysaParams(account_id=account.id)
    db_session.add(params)
    db_session.commit()
    return account, params


class TestHysaDetailView:
    """GET /accounts/<id>/hysa."""

    def test_hysa_detail_view(self, auth_client, seed_user, db, seed_periods):
        """Returns 200 with interest data."""
        account, _ = _create_hysa_account(seed_user, db.session)
        account.current_anchor_period_id = seed_periods[0].id
        db.session.commit()

        resp = auth_client.get(f"/accounts/{account.id}/hysa")
        assert resp.status_code == 200
        assert b"HYSA" in resp.data
        assert b"APY" in resp.data

    def test_hysa_detail_idor(self, app, auth_client, seed_user, db):
        """Other user's HYSA account → redirect."""
        from app.models.user import User
        from app.services.auth_service import hash_password

        other = User(email="other@test.com", password_hash=hash_password("pass"))
        db.session.add(other)
        db.session.flush()

        hysa_type = db.session.query(AccountType).filter_by(name="hysa").one()
        other_acct = Account(
            user_id=other.id,
            account_type_id=hysa_type.id,
            name="Other HYSA",
            current_anchor_balance=Decimal("5000.00"),
        )
        db.session.add(other_acct)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{other_acct.id}/hysa")
        assert resp.status_code == 302

    def test_hysa_detail_nonexistent(self, auth_client, seed_user, db):
        """Bad account ID → redirect."""
        resp = auth_client.get("/accounts/99999/hysa")
        assert resp.status_code == 302

    def test_hysa_detail_wrong_type(self, auth_client, seed_user, db):
        """Non-HYSA account → redirect with warning."""
        # seed_user already has a checking account.
        account = seed_user["account"]
        resp = auth_client.get(f"/accounts/{account.id}/hysa")
        assert resp.status_code == 302

    def test_hysa_detail_login_required(self, client, db):
        """Unauthenticated → redirect to login."""
        resp = client.get("/accounts/1/hysa")
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")


class TestHysaParamsUpdate:
    """POST /accounts/<id>/hysa/params."""

    def test_hysa_params_update(self, auth_client, seed_user, db, seed_periods):
        """Valid params → updates APY and compounding."""
        account, _ = _create_hysa_account(seed_user, db.session)

        resp = auth_client.post(
            f"/accounts/{account.id}/hysa/params",
            data={
                "apy": "0.05000",
                "compounding_frequency": "monthly",
            },
        )
        assert resp.status_code == 302

        params = db.session.query(HysaParams).filter_by(account_id=account.id).one()
        assert params.apy == Decimal("0.05000")
        assert params.compounding_frequency == "monthly"

    def test_hysa_params_update_validation(self, auth_client, seed_user, db):
        """Invalid params → validation error."""
        account, _ = _create_hysa_account(seed_user, db.session)

        resp = auth_client.post(
            f"/accounts/{account.id}/hysa/params",
            data={
                "apy": "2.00000",  # > 1 is invalid
                "compounding_frequency": "daily",
            },
        )
        assert resp.status_code == 302

        # APY should remain at default.
        params = db.session.query(HysaParams).filter_by(account_id=account.id).one()
        assert params.apy == Decimal("0.04500")

    def test_hysa_params_update_idor(self, app, auth_client, seed_user, db):
        """POST to other user's account → redirect."""
        from app.models.user import User
        from app.services.auth_service import hash_password

        other = User(email="other2@test.com", password_hash=hash_password("pass"))
        db.session.add(other)
        db.session.flush()

        hysa_type = db.session.query(AccountType).filter_by(name="hysa").one()
        other_acct = Account(
            user_id=other.id,
            account_type_id=hysa_type.id,
            name="Other HYSA 2",
            current_anchor_balance=Decimal("5000.00"),
        )
        db.session.add(other_acct)
        db.session.flush()
        other_params = HysaParams(account_id=other_acct.id)
        db.session.add(other_params)
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{other_acct.id}/hysa/params",
            data={"apy": "0.05000", "compounding_frequency": "daily"},
        )
        assert resp.status_code == 302


class TestCreateHysaAccount:
    """Creating a HYSA account auto-creates HysaParams."""

    def test_create_hysa_account_auto_params(self, auth_client, seed_user, db, seed_periods):
        """POST to create account with HYSA type → auto-creates HysaParams."""
        hysa_type = db.session.query(AccountType).filter_by(name="hysa").one()

        resp = auth_client.post(
            "/accounts",
            data={
                "name": "New HYSA",
                "account_type_id": str(hysa_type.id),
                "anchor_balance": "5000.00",
            },
        )
        assert resp.status_code == 302

        account = db.session.query(Account).filter_by(name="New HYSA").one()
        params = db.session.query(HysaParams).filter_by(account_id=account.id).first()
        assert params is not None
        assert params.apy == Decimal("0.04500")
        assert params.compounding_frequency == "daily"
