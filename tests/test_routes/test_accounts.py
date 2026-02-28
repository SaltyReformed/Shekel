"""
Shekel Budget App — Account Route Tests

Tests for account CRUD, anchor balance true-up, and account type
management endpoints (§2.1 of the test plan).
"""

from decimal import Decimal

from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.models.user import User, UserSettings
from app.models.ref import AccountType
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

    checking_type = db.session.query(AccountType).filter_by(name="checking").one()
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
            assert b"form" in response.data


class TestAccountCreate:
    """Tests for POST /accounts."""

    def test_create_account(self, app, auth_client, seed_user):
        """POST /accounts creates a new account and redirects to the list."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(name="savings").one()

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
            assert b"Validation error" in response.data

    def test_create_account_duplicate_name(self, app, auth_client, seed_user):
        """POST /accounts with a duplicate name shows a warning flash."""
        with app.app_context():
            # "Checking" already exists from seed_user.
            checking_type = db.session.query(AccountType).filter_by(name="checking").one()

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
            checking_type = db.session.query(AccountType).filter_by(name="checking").one()

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
            savings_type = db.session.query(AccountType).filter_by(name="savings").one()
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
            savings_type = db.session.query(AccountType).filter_by(name="savings").one()
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
            savings_type = db.session.query(AccountType).filter_by(name="savings").one()
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

    def test_inline_anchor_invalid_amount(self, app, auth_client, seed_user):
        """PATCH /accounts/<id>/inline-anchor with invalid amount returns 400."""
        with app.app_context():
            account_id = seed_user["account"].id

            response = auth_client.patch(
                f"/accounts/{account_id}/inline-anchor",
                data={"anchor_balance": "not-a-number"},
            )

            assert response.status_code == 400

    def test_inline_anchor_other_users_account(
        self, app, auth_client, seed_user
    ):
        """PATCH /accounts/<id>/inline-anchor for another user's account returns 404."""
        with app.app_context():
            other = _create_other_user_account()

            response = auth_client.patch(
                f"/accounts/{other['account'].id}/inline-anchor",
                data={"anchor_balance": "9999.00"},
            )

            assert response.status_code == 404


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
        """PATCH /accounts/<id>/true-up with invalid amount returns 400."""
        with app.app_context():
            account_id = seed_user["account"].id

            response = auth_client.patch(
                f"/accounts/{account_id}/true-up",
                data={"anchor_balance": "abc"},
            )

            assert response.status_code == 400

    def test_true_up_other_users_account(
        self, app, auth_client, seed_user, seed_periods
    ):
        """PATCH /accounts/<id>/true-up for another user's account returns 404."""
        with app.app_context():
            other = _create_other_user_account()

            response = auth_client.patch(
                f"/accounts/{other['account'].id}/true-up",
                data={"anchor_balance": "9999.00"},
            )

            assert response.status_code == 404


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
            assert acct_type is not None

    def test_rename_account_type(self, app, auth_client, seed_user):
        """POST /accounts/types/<id> renames an account type."""
        with app.app_context():
            # Create a type to rename (unique name to avoid ref table collisions).
            new_type = AccountType(name="rename_source")
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
            new_type = AccountType(name="crypto")
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
            # "checking" already exists from ref seed.
            response = auth_client.post(
                "/accounts/types",
                data={"name": "checking"},
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"An account type with that name already exists." in response.data

    def test_delete_account_type_in_use(self, app, auth_client, seed_user):
        """POST /accounts/types/<id>/delete for an in-use type shows a warning."""
        with app.app_context():
            # "checking" is used by seed_user's account.
            checking_type = (
                db.session.query(AccountType).filter_by(name="checking").one()
            )

            response = auth_client.post(
                f"/accounts/types/{checking_type.id}/delete",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Cannot delete this account type" in response.data

            # Type should still exist.
            assert db.session.get(AccountType, checking_type.id) is not None
