"""
Shekel Budget App -- Account Route Tests

Tests for account CRUD, anchor balance true-up, and account type
management endpoints (§2.1 of the test plan).
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import AcctCategoryEnum
from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.models.hysa_params import HysaParams
from app.models.investment_params import InvestmentParams
from app.models.user import User, UserSettings
from app.models.ref import AccountType, Status, TransactionType
from app.models.transaction import Transaction
from app.services import balance_calculator, pay_period_service
from app.services.auth_service import hash_password


def _create_other_user_account():
    """Create a second user with their own account.

    Returns:
        dict with keys: user, account.
    """
    other_user = User(
        email="other@shekel.local",
        password_hash=hash_password("otherpass"),
        display_name="Other User",
    )
    db.session.add(other_user)
    db.session.flush()

    settings = UserSettings(user_id=other_user.id)
    db.session.add(settings)

    checking_type = db.session.query(AccountType).filter_by(name="Checking").one()
    account = Account(
        user_id=other_user.id,
        account_type_id=checking_type.id,
        name="Other Checking",
        current_anchor_balance=Decimal("500.00"),
    )
    db.session.add(account)
    db.session.commit()

    return {"user": other_user, "account": account}


# ── Account CRUD ───────────────────────────────────────────────────


class TestAccountList:
    """Tests for GET /accounts."""

    def test_list_accounts_renders(self, app, auth_client, seed_user):
        """GET /accounts renders the accounts page with the user's accounts."""
        with app.app_context():
            response = auth_client.get("/accounts")

            assert response.status_code == 200
            assert b"Checking" in response.data

    def test_new_account_form_renders(self, app, auth_client, seed_user):
        """GET /accounts/new renders the account creation form."""
        with app.app_context():
            response = auth_client.get("/accounts/new")

            assert response.status_code == 200
            assert b'name="name"' in response.data
            assert b'name="anchor_balance"' in response.data
            assert b"New Account" in response.data


class TestAccountCreate:
    """Tests for POST /accounts."""

    def test_create_account(self, app, auth_client, seed_user):
        """POST /accounts creates a new account and redirects to the list."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()

            response = auth_client.post("/accounts", data={
                "name": "Savings",
                "account_type_id": savings_type.id,
                "anchor_balance": "500.00",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Account &#39;Savings&#39; created." in response.data

            # Verify in the database.
            acct = (
                db.session.query(Account)
                .filter_by(user_id=seed_user["user"].id, name="Savings")
                .one()
            )
            assert acct.current_anchor_balance == Decimal("500.00")

    def test_create_account_validation_error(self, app, auth_client, seed_user):
        """POST /accounts with missing name shows a validation error."""
        with app.app_context():
            response = auth_client.post("/accounts", data={
                "name": "",
                "account_type_id": "",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Please correct the highlighted errors" in response.data

    def test_create_account_duplicate_name(self, app, auth_client, seed_user):
        """POST /accounts with a duplicate name shows a warning flash."""
        with app.app_context():
            # "Checking" already exists from seed_user.
            checking_type = db.session.query(AccountType).filter_by(name="Checking").one()

            response = auth_client.post("/accounts", data={
                "name": "Checking",
                "account_type_id": checking_type.id,
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"An account with that name already exists." in response.data


class TestAccountUpdate:
    """Tests for GET/POST /accounts/<id>/edit."""

    def test_edit_account_form_renders(self, app, auth_client, seed_user):
        """GET /accounts/<id>/edit renders the edit form."""
        with app.app_context():
            account_id = seed_user["account"].id

            response = auth_client.get(f"/accounts/{account_id}/edit")

            assert response.status_code == 200
            assert b"Checking" in response.data

    def test_update_account(self, app, auth_client, seed_user):
        """POST /accounts/<id> updates the account and redirects."""
        with app.app_context():
            account_id = seed_user["account"].id
            checking_type = db.session.query(AccountType).filter_by(name="Checking").one()

            response = auth_client.post(f"/accounts/{account_id}", data={
                "name": "Primary Checking",
                "account_type_id": checking_type.id,
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Account &#39;Primary Checking&#39; updated." in response.data

            # Verify in the database.
            acct = db.session.get(Account, account_id)
            assert acct.name == "Primary Checking"

    def test_update_account_duplicate_name(self, app, auth_client, seed_user):
        """POST /accounts/<id> with a duplicate name shows a warning."""
        with app.app_context():
            # Create a second account first.
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
            second = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Savings",
                current_anchor_balance=Decimal("0"),
            )
            db.session.add(second)
            db.session.commit()

            # Try to rename it to "Checking" (already exists).
            response = auth_client.post(f"/accounts/{second.id}", data={
                "name": "Checking",
                "account_type_id": savings_type.id,
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"An account with that name already exists." in response.data

    def test_edit_other_users_account_redirects(self, app, auth_client, seed_user):
        """GET /accounts/<id>/edit for another user's account redirects with flash."""
        with app.app_context():
            other = _create_other_user_account()

            response = auth_client.get(
                f"/accounts/{other['account'].id}/edit",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Account not found." in response.data

    def test_update_other_users_account_redirects(self, app, auth_client, seed_user):
        """POST /accounts/<id> for another user's account redirects with flash."""
        with app.app_context():
            other = _create_other_user_account()

            response = auth_client.post(
                f"/accounts/{other['account'].id}",
                data={"name": "Hacked"},
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Account not found." in response.data

            # Verify name was not changed.
            acct = db.session.get(Account, other["account"].id)
            assert acct.name == "Other Checking"


class TestAccountDeactivate:
    """Tests for POST /accounts/<id>/delete and /reactivate."""

    def test_deactivate_account(self, app, auth_client, seed_user):
        """POST /accounts/<id>/delete soft-deactivates the account."""
        with app.app_context():
            account_id = seed_user["account"].id

            response = auth_client.post(
                f"/accounts/{account_id}/delete",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"deactivated" in response.data

            acct = db.session.get(Account, account_id)
            assert acct.is_active is False

    def test_reactivate_account(self, app, auth_client, seed_user):
        """POST /accounts/<id>/reactivate restores a deactivated account."""
        with app.app_context():
            account_id = seed_user["account"].id

            # Deactivate first.
            seed_user["account"].is_active = False
            db.session.commit()

            response = auth_client.post(
                f"/accounts/{account_id}/reactivate",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"reactivated" in response.data

            acct = db.session.get(Account, account_id)
            assert acct.is_active is True

    def test_deactivate_account_with_active_transfers(
        self, app, auth_client, seed_user
    ):
        """POST /accounts/<id>/delete is blocked when active transfer templates reference it."""
        with app.app_context():
            from app.models.transfer_template import TransferTemplate

            # Create a second account and an active transfer template.
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
            savings = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Savings",
                current_anchor_balance=Decimal("0"),
            )
            db.session.add(savings)
            db.session.flush()

            template = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                name="Monthly Savings",
                default_amount=Decimal("200.00"),
                is_active=True,
            )
            db.session.add(template)
            db.session.commit()

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/delete",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Cannot deactivate this account" in response.data

            # Account should still be active.
            acct = db.session.get(Account, seed_user["account"].id)
            assert acct.is_active is True

    def test_deactivate_other_users_account_redirects(
        self, app, auth_client, seed_user
    ):
        """POST /accounts/<id>/delete for another user's account redirects."""
        with app.app_context():
            other = _create_other_user_account()

            response = auth_client.post(
                f"/accounts/{other['account'].id}/delete",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Account not found." in response.data

            # Other user's account should still be active.
            acct = db.session.get(Account, other["account"].id)
            assert acct.is_active is True

    def test_create_account_double_submit(self, app, auth_client, seed_user):
        """POST /accounts twice with the same name flashes duplicate on 2nd."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
            data = {
                "name": "Emergency Fund",
                "account_type_id": savings_type.id,
                "anchor_balance": "0",
            }

            # First submit succeeds.
            response1 = auth_client.post("/accounts", data=data, follow_redirects=True)
            assert b"created" in response1.data

            # Second submit hits duplicate guard.
            response2 = auth_client.post("/accounts", data=data, follow_redirects=True)
            assert b"An account with that name already exists." in response2.data


# ── Anchor Balance (Inline + True-up) ─────────────────────────────


class TestInlineAnchor:
    """Tests for HTMX inline anchor balance endpoints on accounts list."""

    def test_inline_anchor_update(self, app, auth_client, seed_user, seed_periods):
        """PATCH /accounts/<id>/inline-anchor updates the balance."""
        with app.app_context():
            account_id = seed_user["account"].id

            response = auth_client.patch(
                f"/accounts/{account_id}/inline-anchor",
                data={"anchor_balance": "2500.00"},
            )

            assert response.status_code == 200

            acct = db.session.get(Account, account_id)
            assert acct.current_anchor_balance == Decimal("2500.00")

    def test_inline_anchor_form_returns_partial(
        self, app, auth_client, seed_user
    ):
        """GET /accounts/<id>/inline-anchor-form returns the edit partial."""
        with app.app_context():
            account_id = seed_user["account"].id

            response = auth_client.get(
                f"/accounts/{account_id}/inline-anchor-form"
            )

            assert response.status_code == 200
            assert b'name="anchor_balance"' in response.data
            assert b"1000.00" in response.data

    def test_inline_anchor_display_returns_partial(
        self, app, auth_client, seed_user
    ):
        """GET /accounts/<id>/inline-anchor-display returns the display partial."""
        with app.app_context():
            account_id = seed_user["account"].id

            response = auth_client.get(
                f"/accounts/{account_id}/inline-anchor-display"
            )

            assert response.status_code == 200
            assert b"$1000.00" in response.data
            assert b"font-mono" in response.data

    def test_inline_anchor_invalid_amount(self, app, auth_client, seed_user):
        """PATCH /accounts/<id>/inline-anchor with invalid amount returns 400 with errors JSON."""
        with app.app_context():
            account_id = seed_user["account"].id

            response = auth_client.patch(
                f"/accounts/{account_id}/inline-anchor",
                data={"anchor_balance": "not-a-number"},
            )

            assert response.status_code == 400
            body = response.get_json()
            assert "errors" in body, "400 response must contain validation errors"

    def test_inline_anchor_other_users_account(
        self, app, auth_client, seed_user
    ):
        """PATCH /accounts/<id>/inline-anchor for another user's account returns 404.

        IDOR write-path: must verify the anchor balance was not changed.
        """
        with app.app_context():
            other = _create_other_user_account()
            orig_balance = other["account"].current_anchor_balance

            response = auth_client.patch(
                f"/accounts/{other['account'].id}/inline-anchor",
                data={"anchor_balance": "9999.00"},
            )

            assert response.status_code == 404

            # Prove no state change occurred.
            db.session.expire_all()
            db.session.refresh(other["account"])
            assert other["account"].current_anchor_balance == orig_balance


class TestTrueUp:
    """Tests for the grid anchor balance true-up endpoints."""

    def test_true_up_updates_balance(self, app, auth_client, seed_user, seed_periods):
        """PATCH /accounts/<id>/true-up updates the balance and creates history."""
        with app.app_context():
            account_id = seed_user["account"].id

            response = auth_client.patch(
                f"/accounts/{account_id}/true-up",
                data={"anchor_balance": "3000.00"},
            )

            assert response.status_code == 200
            assert response.headers.get("HX-Trigger") == "balanceChanged"

            acct = db.session.get(Account, account_id)
            assert acct.current_anchor_balance == Decimal("3000.00")

            # Verify audit record was created.
            history = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=account_id)
                .one()
            )
            assert history.anchor_balance == Decimal("3000.00")

    def test_true_up_no_current_period(self, app, auth_client, seed_user):
        """PATCH /accounts/<id>/true-up returns 400 when no pay periods exist."""
        with app.app_context():
            account_id = seed_user["account"].id

            response = auth_client.patch(
                f"/accounts/{account_id}/true-up",
                data={"anchor_balance": "1000.00"},
            )

            assert response.status_code == 400
            assert b"No current pay period found" in response.data

    def test_true_up_invalid_amount(self, app, auth_client, seed_user, seed_periods):
        """PATCH /accounts/<id>/true-up with invalid amount returns 400 with errors JSON."""
        with app.app_context():
            account_id = seed_user["account"].id

            response = auth_client.patch(
                f"/accounts/{account_id}/true-up",
                data={"anchor_balance": "abc"},
            )

            assert response.status_code == 400
            body = response.get_json()
            assert "errors" in body, "400 response must contain validation errors"

    def test_true_up_other_users_account(
        self, app, auth_client, seed_user, seed_periods
    ):
        """PATCH /accounts/<id>/true-up for another user's account returns 404.

        IDOR write-path: must verify the anchor balance was not changed.
        """
        with app.app_context():
            other = _create_other_user_account()
            orig_balance = other["account"].current_anchor_balance

            response = auth_client.patch(
                f"/accounts/{other['account'].id}/true-up",
                data={"anchor_balance": "9999.00"},
            )

            assert response.status_code == 404

            # Prove no state change occurred.
            db.session.expire_all()
            db.session.refresh(other["account"])
            assert other["account"].current_anchor_balance == orig_balance


# ── Account Type CRUD ─────────────────────────────────────────────


class TestAccountTypes:
    """Tests for account type create, rename, and delete."""

    def test_create_account_type(self, app, auth_client, seed_user):
        """POST /accounts/types creates a new account type."""
        with app.app_context():
            response = auth_client.post(
                "/accounts/types",
                data={"name": "investment"},
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Account type &#39;investment&#39; created." in response.data

            acct_type = (
                db.session.query(AccountType).filter_by(name="investment").one()
            )
            # .one() already raises NoResultFound if missing; verify the created name
            assert acct_type.name == "investment"

    def test_rename_account_type(self, app, auth_client, seed_user):
        """POST /accounts/types/<id> renames an account type."""
        with app.app_context():
            # Create a type to rename (unique name to avoid ref table collisions).
            new_type = AccountType(
                name="rename_source",
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.ASSET),
            )
            db.session.add(new_type)
            db.session.commit()

            response = auth_client.post(
                f"/accounts/types/{new_type.id}",
                data={"name": "rename_target"},
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Account type renamed" in response.data

            db.session.refresh(new_type)
            assert new_type.name == "rename_target"

    def test_delete_unused_account_type(self, app, auth_client, seed_user):
        """POST /accounts/types/<id>/delete deletes an unused type."""
        with app.app_context():
            new_type = AccountType(
                name="crypto",
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.ASSET),
            )
            db.session.add(new_type)
            db.session.commit()
            type_id = new_type.id

            response = auth_client.post(
                f"/accounts/types/{type_id}/delete",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"deleted" in response.data

            assert db.session.get(AccountType, type_id) is None

    def test_create_duplicate_account_type(self, app, auth_client, seed_user):
        """POST /accounts/types with a duplicate name shows a warning."""
        with app.app_context():
            # "Checking" already exists from ref seed (exact case match required).
            response = auth_client.post(
                "/accounts/types",
                data={"name": "Checking"},
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"An account type with that name already exists." in response.data

    def test_delete_account_type_in_use(self, app, auth_client, seed_user):
        """POST /accounts/types/<id>/delete for an in-use type shows a warning."""
        with app.app_context():
            # "checking" is used by seed_user's account.
            checking_type = (
                db.session.query(AccountType).filter_by(name="Checking").one()
            )

            response = auth_client.post(
                f"/accounts/types/{checking_type.id}/delete",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Cannot delete this account type" in response.data

            # Type should still exist.
            assert db.session.get(AccountType, checking_type.id) is not None


# ── Account Type Metadata Columns ────────────────────────────────


class TestAccountTypeMetadataColumns:
    """Verify the has_interest, is_pretax, and is_liquid metadata columns
    are seeded correctly on ref.account_types."""

    def test_account_type_has_interest_column(self, app, seed_user):
        """HYSA and HSA have has_interest=True; other types do not."""
        with app.app_context():
            hysa = db.session.query(AccountType).filter_by(name="HYSA").one()
            hsa = db.session.query(AccountType).filter_by(name="HSA").one()
            checking = db.session.query(AccountType).filter_by(name="Checking").one()
            mortgage = db.session.query(AccountType).filter_by(name="Mortgage").one()

            assert hysa.has_interest is True
            assert hsa.has_interest is True
            assert checking.has_interest is False
            assert mortgage.has_interest is False

            # Column is non-nullable.
            col = AccountType.__table__.columns["has_interest"]
            assert col.nullable is False

    def test_account_type_is_pretax_column(self, app, seed_user):
        """401(k) and Traditional IRA have is_pretax=True; Roth types do not."""
        with app.app_context():
            k401 = db.session.query(AccountType).filter_by(name="401(k)").one()
            trad_ira = db.session.query(AccountType).filter_by(
                name="Traditional IRA",
            ).one()
            roth_401k = db.session.query(AccountType).filter_by(
                name="Roth 401(k)",
            ).one()
            roth_ira = db.session.query(AccountType).filter_by(name="Roth IRA").one()
            brokerage = db.session.query(AccountType).filter_by(
                name="Brokerage",
            ).one()

            assert k401.is_pretax is True
            assert trad_ira.is_pretax is True
            assert roth_401k.is_pretax is False
            assert roth_ira.is_pretax is False
            assert brokerage.is_pretax is False

            # Column is non-nullable.
            col = AccountType.__table__.columns["is_pretax"]
            assert col.nullable is False

    def test_account_type_is_liquid_column(self, app, seed_user):
        """Checking, Savings, HYSA, Money Market have is_liquid=True."""
        with app.app_context():
            checking = db.session.query(AccountType).filter_by(
                name="Checking",
            ).one()
            savings = db.session.query(AccountType).filter_by(name="Savings").one()
            hysa = db.session.query(AccountType).filter_by(name="HYSA").one()
            money_market = db.session.query(AccountType).filter_by(
                name="Money Market",
            ).one()
            cd = db.session.query(AccountType).filter_by(name="CD").one()
            hsa = db.session.query(AccountType).filter_by(name="HSA").one()
            credit_card = db.session.query(AccountType).filter_by(
                name="Credit Card",
            ).one()
            k401 = db.session.query(AccountType).filter_by(name="401(k)").one()

            assert checking.is_liquid is True
            assert savings.is_liquid is True
            assert hysa.is_liquid is True
            assert money_market.is_liquid is True
            assert cd.is_liquid is False
            assert hsa.is_liquid is False
            assert credit_card.is_liquid is False
            assert k401.is_liquid is False

            # Column is non-nullable.
            col = AccountType.__table__.columns["is_liquid"]
            assert col.nullable is False

    def test_hsa_has_parameters_true(self, app, seed_user):
        """HSA now has has_parameters=True (changed from False)."""
        with app.app_context():
            hsa = db.session.query(AccountType).filter_by(name="HSA").one()
            assert hsa.has_parameters is True
            assert hsa.has_interest is True


# ── Negative Paths ────────────────────────────────────────────────


class TestAccountNegativePaths:
    """Negative-path tests: nonexistent IDs, IDOR, idempotent ops, validation, XSS."""

    def test_edit_nonexistent_account(self, app, auth_client, seed_user):
        """GET /accounts/999999/edit for a nonexistent account redirects with flash."""
        with app.app_context():
            resp = auth_client.get("/accounts/999999/edit", follow_redirects=True)

            assert resp.status_code == 200
            assert b"Account not found." in resp.data

    def test_update_nonexistent_account(self, app, auth_client, seed_user):
        """POST /accounts/999999 for a nonexistent account redirects with flash."""
        with app.app_context():
            checking_type = db.session.query(AccountType).filter_by(name="Checking").one()

            resp = auth_client.post("/accounts/999999", data={
                "name": "Ghost",
                "account_type_id": checking_type.id,
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Account not found." in resp.data

    def test_deactivate_nonexistent_account(self, app, auth_client, seed_user):
        """POST /accounts/999999/delete for a nonexistent account redirects with flash."""
        with app.app_context():
            resp = auth_client.post(
                "/accounts/999999/delete", follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"Account not found." in resp.data

    def test_reactivate_other_users_account_idor(
        self, app, auth_client, seed_user, second_user
    ):
        """POST /accounts/<id>/reactivate for another user's deactivated account is blocked."""
        with app.app_context():
            # Re-query to ensure the object is in the current session.
            acct_id = second_user["account"].id
            other_acct = db.session.get(Account, acct_id)
            other_acct.is_active = False
            db.session.commit()

            resp = auth_client.post(
                f"/accounts/{acct_id}/reactivate",
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"Account not found." in resp.data

            # Verify DB state unchanged: account still inactive.
            db.session.expire_all()
            refreshed = db.session.get(Account, acct_id)
            assert refreshed.is_active is False

    def test_deactivate_already_inactive_account(self, app, auth_client, seed_user):
        """POST /accounts/<id>/delete on an already-inactive account is idempotent."""
        with app.app_context():
            account_id = seed_user["account"].id

            # First deactivation via the route.
            resp1 = auth_client.post(
                f"/accounts/{account_id}/delete",
                follow_redirects=True,
            )
            assert resp1.status_code == 200
            assert b"deactivated" in resp1.data

            # Second deactivation -- account is already inactive.
            resp2 = auth_client.post(
                f"/accounts/{account_id}/delete",
                follow_redirects=True,
            )

            # Route does not guard against double-deactivate; it sets
            # is_active=False and commits. This is idempotent behavior.
            assert resp2.status_code == 200
            assert b"deactivated" in resp2.data

            db.session.expire_all()
            refreshed = db.session.get(Account, account_id)
            assert refreshed.is_active is False

    def test_reactivate_already_active_account(self, app, auth_client, seed_user):
        """POST /accounts/<id>/reactivate on an already-active account is idempotent."""
        with app.app_context():
            account_id = seed_user["account"].id

            # Account starts active (default from seed). Reactivate anyway.
            resp = auth_client.post(
                f"/accounts/{account_id}/reactivate",
                follow_redirects=True,
            )

            # Route does not guard against reactivating an already-active
            # account; it sets is_active=True and commits.
            assert resp.status_code == 200
            assert b"reactivated" in resp.data

            db.session.expire_all()
            refreshed = db.session.get(Account, account_id)
            assert refreshed.is_active is True

    def test_create_account_missing_name(self, app, auth_client, seed_user):
        """POST /accounts with missing name field fails schema validation and creates no record."""
        with app.app_context():
            checking_type = db.session.query(AccountType).filter_by(name="Checking").one()

            resp = auth_client.post("/accounts", data={
                "account_type_id": checking_type.id,
                "anchor_balance": "500.00",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors" in resp.data

            # Verify no extra account was created (seed_user has exactly 1).
            count = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert count == 1

    def test_create_account_xss_in_name(self, app, auth_client, seed_user):
        """POST /accounts with script tag in name is stored but Jinja2 auto-escapes on render."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()

            # Schema accepts the name (no character restrictions, 32 chars < 100 max).
            resp = auth_client.post("/accounts", data={
                "name": "<script>alert(1)</script>",
                "account_type_id": savings_type.id,
                "anchor_balance": "0",
            }, follow_redirects=True)

            assert resp.status_code == 200

            # Verify account was created in DB.
            acct = (
                db.session.query(Account)
                .filter_by(
                    user_id=seed_user["user"].id,
                    name="<script>alert(1)</script>",
                )
                .one()
            )
            assert acct is not None

            # Verify the XSS payload does not appear unescaped in the response.
            assert b"<script>alert(1)</script>" not in resp.data
            # Verify the escaped form is present (Jinja2 auto-escaping).
            assert b"&lt;script&gt;" in resp.data


# ── Account Creation Redirect Tests ──────────────────────────────


class TestAccountCreationRedirects:
    """Tests for post-creation redirect routing.

    Parameterized account types redirect to their configuration pages
    with setup=1.  Non-parameterized types redirect to the accounts list.
    """

    def test_hysa_creation_redirects_to_detail(
        self, app, auth_client, seed_user,
    ):
        """HYSA creation redirects to HYSA detail with setup=1 and auto-creates HysaParams."""
        with app.app_context():
            hysa_type = db.session.query(AccountType).filter_by(name="HYSA").one()

            resp = auth_client.post("/accounts", data={
                "name": "My HYSA",
                "account_type_id": hysa_type.id,
                "anchor_balance": "5000.00",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert "/hysa" in location
            assert "setup=1" in location

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="My HYSA",
            ).one()
            assert db.session.query(HysaParams).filter_by(
                account_id=acct.id
            ).first() is not None

    def test_mortgage_creation_redirects_to_dashboard(
        self, app, auth_client, seed_user,
    ):
        """Mortgage creation redirects to loan dashboard with setup=1."""
        with app.app_context():
            mortgage_type = db.session.query(AccountType).filter_by(
                name="Mortgage"
            ).one()

            resp = auth_client.post("/accounts", data={
                "name": "Home Mortgage",
                "account_type_id": mortgage_type.id,
                "anchor_balance": "250000.00",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert "/loan" in location
            assert "setup=1" in location

    def test_auto_loan_creation_redirects_to_dashboard(
        self, app, auth_client, seed_user,
    ):
        """Auto loan creation redirects to loan dashboard with setup=1."""
        with app.app_context():
            auto_loan_type = db.session.query(AccountType).filter_by(
                name="Auto Loan"
            ).one()

            resp = auth_client.post("/accounts", data={
                "name": "Car Loan",
                "account_type_id": auto_loan_type.id,
                "anchor_balance": "20000.00",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert "/loan" in location
            assert "setup=1" in location

    def test_401k_creation_redirects_to_investment_dashboard(
        self, app, auth_client, seed_user,
    ):
        """401(k) creation redirects to investment dashboard and auto-creates InvestmentParams."""
        with app.app_context():
            k401_type = db.session.query(AccountType).filter_by(
                name="401(k)"
            ).one()

            resp = auth_client.post("/accounts", data={
                "name": "Work 401k",
                "account_type_id": k401_type.id,
                "anchor_balance": "10000.00",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert "/investment" in location
            assert "setup=1" in location

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="Work 401k",
            ).one()
            assert db.session.query(InvestmentParams).filter_by(
                account_id=acct.id
            ).first() is not None

    def test_roth_ira_creation_redirects_to_investment_dashboard(
        self, app, auth_client, seed_user,
    ):
        """Roth IRA creation routes to investment dashboard with InvestmentParams."""
        with app.app_context():
            roth_ira_type = db.session.query(AccountType).filter_by(
                name="Roth IRA"
            ).one()

            resp = auth_client.post("/accounts", data={
                "name": "My Roth IRA",
                "account_type_id": roth_ira_type.id,
                "anchor_balance": "5000.00",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert "/investment" in location
            assert "setup=1" in location

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="My Roth IRA",
            ).one()
            assert db.session.query(InvestmentParams).filter_by(
                account_id=acct.id
            ).first() is not None

    def test_brokerage_creation_redirects_to_investment_dashboard(
        self, app, auth_client, seed_user,
    ):
        """Brokerage creation routes to investment dashboard with InvestmentParams."""
        with app.app_context():
            brokerage_type = db.session.query(AccountType).filter_by(
                name="Brokerage"
            ).one()

            resp = auth_client.post("/accounts", data={
                "name": "My Brokerage",
                "account_type_id": brokerage_type.id,
                "anchor_balance": "1000.00",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert "/investment" in location
            assert "setup=1" in location

    def test_checking_creation_redirects_to_accounts_list(
        self, app, auth_client, seed_user,
    ):
        """Checking account creation redirects to accounts list without setup param."""
        with app.app_context():
            checking_type = db.session.query(AccountType).filter_by(
                name="Checking"
            ).one()

            resp = auth_client.post("/accounts", data={
                "name": "Secondary Checking",
                "account_type_id": checking_type.id,
                "anchor_balance": "0",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert location.endswith("/accounts")
            assert "setup" not in location

    def test_savings_creation_redirects_to_accounts_list(
        self, app, auth_client, seed_user,
    ):
        """Plain savings account creation redirects to accounts list."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(
                name="Savings"
            ).one()

            resp = auth_client.post("/accounts", data={
                "name": "Emergency Fund",
                "account_type_id": savings_type.id,
                "anchor_balance": "0",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert location.endswith("/accounts")
            assert "setup" not in location

    def test_student_loan_creation_redirects_to_loan(
        self, app, auth_client, seed_user,
    ):
        """Student loan creation redirects to loan dashboard for setup.

        Student loans have has_amortization=True and are now served by
        the unified loan routes.  They must not be routed to the
        investment dashboard.
        """
        with app.app_context():
            sl_type = db.session.query(AccountType).filter_by(
                name="Student Loan"
            ).one()

            resp = auth_client.post("/accounts", data={
                "name": "Student Loan",
                "account_type_id": sl_type.id,
                "anchor_balance": "30000.00",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert "investment" not in location
            assert "/loan" in location

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="Student Loan",
            ).one()
            assert db.session.query(InvestmentParams).filter_by(
                account_id=acct.id
            ).first() is None

    def test_personal_loan_creation_no_investment_params(
        self, app, auth_client, seed_user,
    ):
        """Personal loan creation does NOT create InvestmentParams."""
        with app.app_context():
            pl_type = db.session.query(AccountType).filter_by(
                name="Personal Loan"
            ).one()

            resp = auth_client.post("/accounts", data={
                "name": "Personal Loan",
                "account_type_id": pl_type.id,
                "anchor_balance": "5000.00",
            })

            assert resp.status_code == 302
            assert "investment" not in resp.headers["Location"]

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="Personal Loan",
            ).one()
            assert db.session.query(InvestmentParams).filter_by(
                account_id=acct.id
            ).first() is None

    def test_investment_params_not_duplicated(
        self, app, auth_client, seed_user,
    ):
        """Auto-creation of InvestmentParams produces exactly one record."""
        with app.app_context():
            k401_type = db.session.query(AccountType).filter_by(
                name="401(k)"
            ).one()

            auth_client.post("/accounts", data={
                "name": "Dupe Test 401k",
                "account_type_id": k401_type.id,
                "anchor_balance": "10000.00",
            })

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="Dupe Test 401k",
            ).one()

            count = db.session.query(InvestmentParams).filter_by(
                account_id=acct.id
            ).count()
            assert count == 1

    def test_investment_params_defaults_are_reasonable(
        self, app, auth_client, seed_user,
    ):
        """Auto-created InvestmentParams have sensible default values."""
        with app.app_context():
            k401_type = db.session.query(AccountType).filter_by(
                name="401(k)"
            ).one()

            auth_client.post("/accounts", data={
                "name": "Default 401k",
                "account_type_id": k401_type.id,
                "anchor_balance": "0",
            })

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="Default 401k",
            ).one()
            params = db.session.query(InvestmentParams).filter_by(
                account_id=acct.id,
            ).one()

            assert params.assumed_annual_return == Decimal("0.07000")
            assert params.employer_contribution_type == "none"
            assert params.assumed_annual_return >= 0


# ── Wizard Banner Tests ──────────────────────────────────────────


class TestWizardBanner:
    """Tests for the setup wizard banner on parameter pages.

    The banner appears when ?setup=1 is in the query string, indicating
    the user just created the account and should review configuration.
    """

    def test_wizard_banner_shown_on_hysa_with_setup_param(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """HYSA detail page shows wizard banner when ?setup=1 is present."""
        with app.app_context():
            hysa_type = db.session.query(AccountType).filter_by(
                name="HYSA"
            ).one()
            acct = Account(
                user_id=seed_user["user"].id,
                account_type_id=hysa_type.id,
                name="Banner HYSA",
                current_anchor_balance=Decimal("5000.00"),
                current_anchor_period_id=seed_periods[0].id,
            )
            db.session.add(acct)
            db.session.flush()
            db.session.add(HysaParams(account_id=acct.id))
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/hysa?setup=1")
            assert resp.status_code == 200
            assert b"Configure the settings below" in resp.data
            assert b"alert-dismissible" in resp.data

    def test_wizard_banner_hidden_on_hysa_without_setup_param(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """HYSA detail page does NOT show wizard banner without ?setup=1."""
        with app.app_context():
            hysa_type = db.session.query(AccountType).filter_by(
                name="HYSA"
            ).one()
            acct = Account(
                user_id=seed_user["user"].id,
                account_type_id=hysa_type.id,
                name="No Banner HYSA",
                current_anchor_balance=Decimal("5000.00"),
                current_anchor_period_id=seed_periods[0].id,
            )
            db.session.add(acct)
            db.session.flush()
            db.session.add(HysaParams(account_id=acct.id))
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/hysa")
            assert resp.status_code == 200
            assert b"Configure the settings below" not in resp.data

    def test_wizard_banner_shown_on_investment_with_setup_param(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Investment dashboard shows wizard banner when ?setup=1 is present."""
        with app.app_context():
            k401_type = db.session.query(AccountType).filter_by(
                name="401(k)"
            ).one()
            acct = Account(
                user_id=seed_user["user"].id,
                account_type_id=k401_type.id,
                name="Banner 401k",
                current_anchor_balance=Decimal("10000.00"),
                current_anchor_period_id=seed_periods[0].id,
            )
            db.session.add(acct)
            db.session.flush()
            db.session.add(InvestmentParams(account_id=acct.id))
            db.session.commit()

            resp = auth_client.get(
                f"/accounts/{acct.id}/investment?setup=1"
            )
            assert resp.status_code == 200
            assert b"Configure the settings below" in resp.data
            assert b"alert-dismissible" in resp.data

    def test_wizard_banner_hidden_on_investment_without_setup_param(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Investment dashboard does NOT show wizard banner without ?setup=1."""
        with app.app_context():
            k401_type = db.session.query(AccountType).filter_by(
                name="401(k)"
            ).one()
            acct = Account(
                user_id=seed_user["user"].id,
                account_type_id=k401_type.id,
                name="No Banner 401k",
                current_anchor_balance=Decimal("10000.00"),
                current_anchor_period_id=seed_periods[0].id,
            )
            db.session.add(acct)
            db.session.flush()
            db.session.add(InvestmentParams(account_id=acct.id))
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/investment")
            assert resp.status_code == 200
            assert b"Configure the settings below" not in resp.data


# ── Checking Detail ──────────────────────────────────────────────


class TestCheckingDetail:
    """Tests for the checking account detail page with balance projections."""

    def _create_checking_account(self, seed_user, periods, balance="5000.00"):
        """Create a new checking account with anchor set to period 0.

        Creates a fresh account (avoiding session identity map caching
        from seed_user's account) with the specified anchor balance.
        """
        checking_type = db.session.query(AccountType).filter_by(name="Checking").one()
        acct = Account(
            user_id=seed_user["user"].id,
            account_type_id=checking_type.id,
            name="Detail Checking",
            current_anchor_balance=Decimal(balance),
            current_anchor_period_id=periods[0].id,
        )
        db.session.add(acct)
        return acct

    def test_checking_detail_page_renders(self, app, auth_client, seed_user):
        """GET /accounts/<id>/checking renders the detail page with account name and balance."""
        with app.app_context():
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date.today(),
                num_periods=10,
            )
            acct = self._create_checking_account(seed_user, periods)
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/checking")

            assert resp.status_code == 200
            assert b"Detail Checking" in resp.data
            assert b"$5,000.00" in resp.data

    def test_checking_detail_projection_values_are_correct(
        self, app, auth_client, seed_user,
    ):
        """Checking detail projections match expected balance calculations.

        With anchor $5,000 and net +$500 per period, projections are:
        3 months (6 periods) = $8,000, 6 months (13) = $11,500, 1 year (26) = $18,000.
        """
        with app.app_context():
            scenario = seed_user["scenario"]
            category = seed_user["categories"]["Salary"]

            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date.today(),
                num_periods=27,
            )
            acct = self._create_checking_account(seed_user, periods)
            db.session.flush()

            projected_status = db.session.query(Status).filter_by(name="Projected").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            # Create income and expense in all post-anchor periods.
            for p in periods[1:]:
                db.session.add(Transaction(
                    pay_period_id=p.id,
                    scenario_id=scenario.id,
                    account_id=acct.id,
                    status_id=projected_status.id,
                    name="Paycheck",
                    category_id=category.id,
                    transaction_type_id=income_type.id,
                    estimated_amount=Decimal("2000.00"),
                ))
                db.session.add(Transaction(
                    pay_period_id=p.id,
                    scenario_id=scenario.id,
                    account_id=acct.id,
                    status_id=projected_status.id,
                    name="Expenses",
                    category_id=category.id,
                    transaction_type_id=expense_type.id,
                    estimated_amount=Decimal("1500.00"),
                ))
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/checking")
            assert resp.status_code == 200

            # 3 months (6 periods): 5000 + 6*500 = 8000
            assert b"$8,000" in resp.data
            # 6 months (13 periods): 5000 + 13*500 = 11500
            assert b"$11,500" in resp.data
            # 1 year (26 periods): 5000 + 26*500 = 18000
            assert b"$18,000" in resp.data

    def test_checking_detail_matches_grid_balance(
        self, app, auth_client, seed_user,
    ):
        """Checking detail projections use the same balance calculator as the grid.

        Calls calculate_balances() directly and verifies the detail page
        displays the same value for the 3-month projection.
        """
        with app.app_context():
            scenario = seed_user["scenario"]
            category = seed_user["categories"]["Salary"]

            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date.today(),
                num_periods=27,
            )
            acct = self._create_checking_account(seed_user, periods)
            db.session.flush()

            projected_status = db.session.query(Status).filter_by(name="Projected").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            for p in periods[1:]:
                db.session.add(Transaction(
                    pay_period_id=p.id,
                    scenario_id=scenario.id,
                    account_id=acct.id,
                    status_id=projected_status.id,
                    name="Paycheck",
                    category_id=category.id,
                    transaction_type_id=income_type.id,
                    estimated_amount=Decimal("2000.00"),
                ))
                db.session.add(Transaction(
                    pay_period_id=p.id,
                    scenario_id=scenario.id,
                    account_id=acct.id,
                    status_id=projected_status.id,
                    name="Bills",
                    category_id=category.id,
                    transaction_type_id=expense_type.id,
                    estimated_amount=Decimal("1500.00"),
                ))
            db.session.commit()

            # Call balance calculator directly (same function grid.py uses).
            acct_transactions = (
                db.session.query(Transaction)
                .filter(
                    Transaction.account_id == acct.id,
                    Transaction.pay_period_id.in_([p.id for p in periods]),
                    Transaction.scenario_id == scenario.id,
                    Transaction.is_deleted.is_(False),
                )
                .all()
            )

            balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("5000.00"),
                anchor_period_id=periods[0].id,
                periods=periods,
                transactions=acct_transactions,
            )

            # Get the 3-month balance from the calculator.
            target_period = periods[6]
            calc_balance = balances[target_period.id]

            # Verify the detail page shows this exact value.
            resp = auth_client.get(f"/accounts/{acct.id}/checking")
            assert resp.status_code == 200

            # The projection summary uses {:,.0f} format.
            expected_str = "${:,.0f}".format(float(calc_balance))
            assert expected_str.encode() in resp.data

    def test_checking_detail_rejects_non_checking_account(
        self, app, auth_client, seed_user,
    ):
        """GET /accounts/<id>/checking returns 404 for non-checking account types."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
            savings = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="My Savings",
                current_anchor_balance=Decimal("1000.00"),
            )
            db.session.add(savings)
            db.session.commit()

            resp = auth_client.get(f"/accounts/{savings.id}/checking")
            assert resp.status_code == 404

    def test_checking_detail_rejects_other_users_account(
        self, app, auth_client, seed_user, second_user,
    ):
        """GET /accounts/<id>/checking returns 404 for another user's account (IDOR)."""
        with app.app_context():
            resp = auth_client.get(
                f"/accounts/{second_user['account'].id}/checking"
            )
            assert resp.status_code == 404

    def test_checking_detail_handles_no_transactions(
        self, app, auth_client, seed_user,
    ):
        """Checking detail with no transactions shows flat balance at anchor amount."""
        with app.app_context():
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date.today(),
                num_periods=27,
            )
            acct = self._create_checking_account(seed_user, periods)
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/checking")
            assert resp.status_code == 200

            # With no transactions, projections should show the anchor balance.
            assert b"$5,000" in resp.data

    def test_checking_detail_handles_short_horizon(
        self, app, auth_client, seed_user,
    ):
        """Short horizon: 3-month projection available, 12-month projection missing."""
        with app.app_context():
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date.today(),
                num_periods=10,
            )
            acct = self._create_checking_account(seed_user, periods)
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/checking")
            assert resp.status_code == 200

            # 3-month target (period index 6) is within range (10 periods).
            assert b"3 months" in resp.data
            # 6-month (index 13) and 12-month (index 26) are beyond our horizon.
            assert b"6 months" not in resp.data
            assert b"1 year" not in resp.data

    def test_checking_detail_excludes_credit_transactions(
        self, app, auth_client, seed_user,
    ):
        """Credit-status transactions are excluded from the projected balance.

        A credit expense should not reduce the checking balance because
        credit transactions are not paid from checking.
        """
        with app.app_context():
            scenario = seed_user["scenario"]
            category = seed_user["categories"]["Rent"]

            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date.today(),
                num_periods=10,
            )
            acct = self._create_checking_account(seed_user, periods)
            db.session.flush()

            credit_status = db.session.query(Status).filter_by(name="Credit").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            # Create a credit expense in the first post-anchor period.
            db.session.add(Transaction(
                pay_period_id=periods[1].id,
                scenario_id=scenario.id,
                account_id=acct.id,
                status_id=credit_status.id,
                name="Credit Card Groceries",
                category_id=category.id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("1000.00"),
            ))
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/checking")
            assert resp.status_code == 200

            # The credit expense should NOT reduce the balance.
            # Projections should still show $5,000 (flat from anchor).
            assert b"$5,000" in resp.data
            # Verify the balance was NOT reduced by the credit expense.
            assert b"$4,000" not in resp.data

    def test_checking_detail_shows_anchor_date(self, app, auth_client, seed_user):
        """Anchor period start date is displayed on the checking detail page."""
        with app.app_context():
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date.today(),
                num_periods=10,
            )
            acct = self._create_checking_account(seed_user, periods)
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/checking")
            assert resp.status_code == 200

            # The anchor period's start date should be displayed.
            anchor_date_str = periods[0].start_date.strftime("%b %-d, %Y")
            assert anchor_date_str.encode() in resp.data


class TestCheckingDashboardLink:
    """Tests for the checking detail link on the savings/accounts dashboard."""

    def test_dashboard_has_checking_detail_link(self, app, auth_client, seed_user):
        """GET /savings dashboard includes a link to the checking detail page."""
        with app.app_context():
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date.today(),
                num_periods=10,
            )
            # Set anchor on the seed_user account so the dashboard
            # can compute balances for it.
            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            # The dashboard should include a link to the checking detail page.
            expected_url = f"/accounts/{seed_user['account'].id}/checking"
            assert expected_url.encode() in resp.data

    def test_dashboard_checking_link_not_shown_for_other_types(
        self, app, auth_client, seed_user,
    ):
        """Dashboard does not show checking detail link for non-checking accounts."""
        with app.app_context():
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date.today(),
                num_periods=10,
            )

            # Create a savings account.
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
            savings = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="My Savings",
                current_anchor_balance=Decimal("0"),
                current_anchor_period_id=periods[0].id,
            )
            db.session.add(savings)

            seed_user["account"].current_anchor_period_id = periods[0].id
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            # The savings account should NOT have a checking detail link.
            savings_checking_url = f"/accounts/{savings.id}/checking"
            assert savings_checking_url.encode() not in resp.data

            # But the checking account should have one.
            checking_url = f"/accounts/{seed_user['account'].id}/checking"
            assert checking_url.encode() in resp.data
