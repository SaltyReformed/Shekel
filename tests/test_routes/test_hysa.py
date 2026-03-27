"""
Tests for HYSA routes (detail view and params update).
"""

from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.hysa_params import HysaParams
from app.models.ref import AccountType


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


def _create_other_hysa(second_user, db_session):
    """Create a HYSA account owned by the second user.

    Builds on the shared second_user fixture. Returns (Account, HysaParams).
    """
    hysa_type = db_session.query(AccountType).filter_by(name="hysa").one()
    account = Account(
        user_id=second_user["user"].id,
        account_type_id=hysa_type.id,
        name="Other HYSA",
        current_anchor_balance=Decimal("5000.00"),
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

    def test_hysa_detail_idor(self, auth_client, second_user, db):
        """GET another user's HYSA account is rejected
        and does not leak victim data."""
        other_acct, _ = _create_other_hysa(second_user, db.session)

        resp = auth_client.get(f"/accounts/{other_acct.id}/hysa")
        assert resp.status_code == 302
        location = resp.headers.get("Location", "")
        assert "/accounts" in location, (
            f"IDOR redirect went to {location}, expected /accounts"
        )
        assert b"Other HYSA" not in resp.data, (
            "IDOR response leaked victim's account name"
        )

    def test_hysa_detail_nonexistent(self, auth_client, seed_user, db):
        """Bad account ID → redirect to accounts list."""
        resp = auth_client.get("/accounts/99999/hysa")
        assert resp.status_code == 302
        assert "/accounts" in resp.headers.get("Location", "")

    def test_hysa_detail_wrong_type(self, auth_client, seed_user, db):
        """Non-HYSA account → redirect to accounts list with warning."""
        # seed_user already has a checking account.
        account = seed_user["account"]
        resp = auth_client.get(f"/accounts/{account.id}/hysa")
        assert resp.status_code == 302
        assert "/accounts" in resp.headers.get("Location", "")

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
                "apy": "5.000",
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
                "apy": "200",  # > 100 is invalid
                "compounding_frequency": "daily",
            },
        )
        assert resp.status_code == 302

        # APY should remain at default.
        params = db.session.query(HysaParams).filter_by(account_id=account.id).one()
        assert params.apy == Decimal("0.04500")

    def test_hysa_params_update_idor(self, auth_client, second_user, db):
        """POST to another user's HYSA params is rejected
        and leaves the victim's data completely unchanged."""
        # Phase A: Setup victim's data with known values.
        other_acct, _ = _create_other_hysa(second_user, db.session)
        original = db.session.query(HysaParams).filter_by(
            account_id=other_acct.id
        ).one()
        orig_apy = original.apy
        orig_freq = original.compounding_frequency

        # Phase B: Attack.
        resp = auth_client.post(
            f"/accounts/{other_acct.id}/hysa/params",
            data={"apy": "0.09000", "compounding_frequency": "monthly"},
        )

        # Phase C: Verify no state change.
        assert resp.status_code == 302
        location = resp.headers.get("Location", "")
        assert "/accounts" in location, (
            f"IDOR redirect went to {location}, expected /accounts"
        )

        db.session.expire_all()
        after = db.session.query(HysaParams).filter_by(
            account_id=other_acct.id
        ).one()
        assert after.apy == orig_apy, (
            "IDOR attack modified apy!"
        )
        assert after.compounding_frequency == orig_freq, (
            "IDOR attack modified compounding_frequency!"
        )


class TestHysaNegativePaths:
    """Negative-path and boundary tests for HYSA routes."""

    def test_params_update_invalid_apy(self, auth_client, seed_user, db):
        """Non-numeric APY is rejected and DB is unchanged."""
        account, params = _create_hysa_account(seed_user, db.session)
        orig_apy = params.apy

        resp = auth_client.post(
            f"/accounts/{account.id}/hysa/params",
            data={"apy": "abc", "compounding_frequency": "daily"},
        )
        assert resp.status_code == 302

        db.session.expire_all()
        after = db.session.query(HysaParams).filter_by(account_id=account.id).one()
        assert after.apy == orig_apy, "Invalid APY modified the DB!"

    def test_params_update_negative_apy(self, auth_client, seed_user, db):
        """Negative APY is rejected by Range(min=0) validator."""
        account, params = _create_hysa_account(seed_user, db.session)
        orig_apy = params.apy

        resp = auth_client.post(
            f"/accounts/{account.id}/hysa/params",
            data={"apy": "-0.5", "compounding_frequency": "daily"},
        )
        assert resp.status_code == 302

        db.session.expire_all()
        after = db.session.query(HysaParams).filter_by(account_id=account.id).one()
        assert after.apy == orig_apy, "Negative APY modified the DB!"

    def test_params_update_invalid_compounding_frequency(
        self, auth_client, seed_user, db,
    ):
        """Invalid compounding frequency is rejected by OneOf validator."""
        account, params = _create_hysa_account(seed_user, db.session)
        orig_freq = params.compounding_frequency

        resp = auth_client.post(
            f"/accounts/{account.id}/hysa/params",
            data={"apy": "0.04500", "compounding_frequency": "bogus_value"},
        )
        assert resp.status_code == 302

        db.session.expire_all()
        after = db.session.query(HysaParams).filter_by(account_id=account.id).one()
        assert after.compounding_frequency == orig_freq

    def test_params_update_nonexistent_account(self, auth_client, seed_user, db):
        """POST to nonexistent account redirects with flash."""
        resp = auth_client.post(
            "/accounts/999999/hysa/params",
            data={"apy": "0.04500", "compounding_frequency": "daily"},
        )
        assert resp.status_code == 302
        assert "/accounts" in resp.headers.get("Location", "")
        resp2 = auth_client.get(resp.headers["Location"])
        assert b"Account not found." in resp2.data

    def test_params_update_wrong_account_type(self, auth_client, seed_user, db):
        """POST HYSA params to a checking account is rejected."""
        checking_acct = seed_user["account"]
        resp = auth_client.post(
            f"/accounts/{checking_acct.id}/hysa/params",
            data={"apy": "0.04500", "compounding_frequency": "daily"},
        )
        assert resp.status_code == 302
        assert "/accounts" in resp.headers.get("Location", "")
        resp2 = auth_client.get(resp.headers["Location"])
        assert b"This account is not a HYSA." in resp2.data

    def test_params_update_extremely_high_apy(self, auth_client, seed_user, db):
        """APY > 1 (100%) is rejected by Range(max=1) validator."""
        account, params = _create_hysa_account(seed_user, db.session)
        orig_apy = params.apy

        resp = auth_client.post(
            f"/accounts/{account.id}/hysa/params",
            data={"apy": "500", "compounding_frequency": "daily"},
        )
        assert resp.status_code == 302

        db.session.expire_all()
        after = db.session.query(HysaParams).filter_by(account_id=account.id).one()
        assert after.apy == orig_apy, "APY > 1 should be rejected by schema"


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
        assert params is not None, "HysaParams were not auto-created"
        assert params.account_id == account.id
        assert params.apy == Decimal("0.04500")
        assert params.compounding_frequency == "daily"


class TestHysaDetailShadowTransactions:
    """Verify that the HYSA detail page includes shadow transactions
    from transfers in its balance calculation and projection.
    """

    def test_hysa_detail_includes_transfer_deposit(
        self, auth_client, seed_user, db, seed_periods
    ):
        """Verify that the HYSA detail page includes shadow income
        transactions from transfers in the balance projection.  Without
        this, the HYSA projected balance underestimates by the total of
        all missed transfer deposits, and interest compounds on the
        wrong base amount.
        """
        from app.models.category import Category  # pylint: disable=import-outside-toplevel
        from app.models.ref import Status  # pylint: disable=import-outside-toplevel
        from app.services import transfer_service  # pylint: disable=import-outside-toplevel

        account, _ = _create_hysa_account(seed_user, db.session)
        account.current_anchor_period_id = seed_periods[0].id
        db.session.commit()

        # Add transfer categories required by the service.
        incoming = Category(
            user_id=seed_user["user"].id,
            group_name="Transfers", item_name="Incoming",
        )
        outgoing = Category(
            user_id=seed_user["user"].id,
            group_name="Transfers", item_name="Outgoing",
        )
        db.session.add_all([incoming, outgoing])
        db.session.flush()

        # Create a $500 transfer from checking to HYSA.
        projected = db.session.query(Status).filter_by(name="projected").one()
        transfer_service.create_transfer(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=account.id,
            pay_period_id=seed_periods[0].id,
            scenario_id=seed_user["scenario"].id,
            amount=Decimal("500.00"),
            status_id=projected.id,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{account.id}/hysa")
        assert resp.status_code == 200

        html = resp.data.decode()
        # Anchor $10,000 + $500 deposit + interest at 4.5% APY daily
        # compounding = ~$10,601.  Without the fix, the balance would
        # be ~$10,096 (interest on anchor only, deposit missing).
        # Check for "10,6" to confirm the deposit is reflected.
        assert "10,6" in html

    def test_hysa_detail_no_transfers_regression(
        self, auth_client, seed_user, db, seed_periods
    ):
        """Verify that the HYSA detail page still works correctly when
        there are no transfers.  The account_id query must return an
        empty set without errors, and the balance equals anchor + interest.
        """
        account, _ = _create_hysa_account(seed_user, db.session)
        account.current_anchor_period_id = seed_periods[0].id
        db.session.commit()

        resp = auth_client.get(f"/accounts/{account.id}/hysa")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Anchor is $10,000.  With only interest, balance should be
        # just above $10,000.  Check for "10,0" to confirm.
        assert "10,0" in html
