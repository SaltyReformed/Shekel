"""
Shekel Budget App -- Account Route Tests

Tests for account CRUD, anchor balance true-up, and account type
management endpoints (§2.1 of the test plan).
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app import ref_cache
from app.enums import AcctCategoryEnum
from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.models.interest_params import InterestParams
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


class TestAccountArchive:
    """Tests for POST /accounts/<id>/archive and /unarchive."""

    def test_archive_account(self, app, auth_client, seed_user):
        """POST /accounts/<id>/archive archives the account."""
        with app.app_context():
            account_id = seed_user["account"].id

            response = auth_client.post(
                f"/accounts/{account_id}/archive",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"archived" in response.data

            acct = db.session.get(Account, account_id)
            assert acct.is_active is False

    def test_unarchive_account(self, app, auth_client, seed_user):
        """POST /accounts/<id>/unarchive restores an archived account."""
        with app.app_context():
            account_id = seed_user["account"].id

            # Archive first.
            seed_user["account"].is_active = False
            db.session.commit()

            response = auth_client.post(
                f"/accounts/{account_id}/unarchive",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"unarchived" in response.data

            acct = db.session.get(Account, account_id)
            assert acct.is_active is True

    def test_archive_account_with_active_transfers(
        self, app, auth_client, seed_user
    ):
        """POST /accounts/<id>/archive is blocked when active transfer templates reference it."""
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
                f"/accounts/{seed_user['account'].id}/archive",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Cannot archive this account" in response.data

            # Account should still be active.
            acct = db.session.get(Account, seed_user["account"].id)
            assert acct.is_active is True

    def test_archive_other_users_account_redirects(
        self, app, auth_client, seed_user
    ):
        """POST /accounts/<id>/archive for another user's account redirects."""
        with app.app_context():
            other = _create_other_user_account()

            response = auth_client.post(
                f"/accounts/{other['account'].id}/archive",
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

    def test_inline_anchor_update(self, app, auth_client, seed_user, seed_periods_today):
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

    def test_true_up_updates_balance(self, app, auth_client, seed_user, seed_periods_today):
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

    def test_true_up_invalid_amount(self, app, auth_client, seed_user, seed_periods_today):
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
        self, app, auth_client, seed_user, seed_periods_today
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


class TestTrueUpSameDayDuplicate:
    """F-103 / C-22: same-day same-balance double-submit dedupe."""

    def test_double_submit_creates_one_history_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Two identical true-ups same day produce exactly one history row.

        F-103 / C-22: the partial unique expression index
        ``uq_anchor_history_account_period_balance_day`` rejects the
        second INSERT when the user clicks Save twice in a row.
        The route catches the IntegrityError and returns the
        already-current balance so the user sees idempotent success
        instead of a 500.
        """
        with app.app_context():
            account_id = seed_user["account"].id

            r1 = auth_client.patch(
                f"/accounts/{account_id}/true-up",
                data={"anchor_balance": "1234.56"},
            )
            assert r1.status_code == 200

            r2 = auth_client.patch(
                f"/accounts/{account_id}/true-up",
                data={"anchor_balance": "1234.56"},
            )
            # Idempotent success: both requests return 200.
            assert r2.status_code == 200

            # Exactly one audit row was added.
            db.session.expire_all()
            history = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=account_id)
                .all()
            )
            assert len(history) == 1, (
                f"Expected 1 anchor history row after double-submit, "
                f"found {len(history)}; F-103 dedupe failed."
            )
            assert history[0].anchor_balance == Decimal("1234.56")

    def test_same_day_different_balance_creates_two_rows(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Same-day true-ups with different balances both succeed.

        F-103 / C-22: the unique constraint includes
        ``anchor_balance``, so a legitimate same-day correction (the
        user noticed an error and re-trued at a different amount)
        must NOT be blocked.
        """
        with app.app_context():
            account_id = seed_user["account"].id

            r1 = auth_client.patch(
                f"/accounts/{account_id}/true-up",
                data={"anchor_balance": "1000.00"},
            )
            r2 = auth_client.patch(
                f"/accounts/{account_id}/true-up",
                data={"anchor_balance": "1100.00"},
            )
            assert r1.status_code == 200
            assert r2.status_code == 200

            db.session.expire_all()
            history = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=account_id)
                .order_by(AccountAnchorHistory.id)
                .all()
            )
            assert len(history) == 2, (
                f"Expected 2 anchor history rows after distinct "
                f"same-day true-ups, found {len(history)}"
            )
            assert {h.anchor_balance for h in history} == {
                Decimal("1000.00"), Decimal("1100.00"),
            }


class TestTrueUpClearsEntries:
    """Tests for the auto-clear behavior on checking anchor true-up.

    When the user trues up a checking anchor balance, the service
    flips is_cleared=TRUE on every past-dated entry whose parent
    transaction is Projected.  See the rationale in
    app/services/entry_service.py::clear_entries_for_anchor_true_up.

    These tests verify the scope of the update: past-dated entries on
    projected parents are cleared, while future-dated entries, entries
    on non-projected parents, and entries on non-checking-account
    true-ups are left alone.
    """

    def _make_grocery_txn_with_entries(self, seed_user, seed_periods_today, entries):
        """Create a tracked grocery transaction with the given entries.

        Args:
            seed_user: seed_user fixture dict.
            seed_periods_today: list of PayPeriods.
            entries: list of (amount, entry_date, is_credit, is_cleared)
                tuples.

        Returns:
            The Transaction object.
        """
        from app.models.transaction_entry import TransactionEntry
        from app.models.transaction_template import TransactionTemplate

        projected = db.session.query(Status).filter_by(name="Projected").one()
        expense_type = db.session.query(TransactionType).filter_by(
            name="Expense",
        ).one()

        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=expense_type.id,
            name="Groceries",
            default_amount=Decimal("500.00"),
            is_envelope=True,
        )
        db.session.add(template)
        db.session.flush()

        txn = Transaction(
            template_id=template.id,
            pay_period_id=seed_periods_today[0].id,
            scenario_id=seed_user["scenario"].id,
            account_id=seed_user["account"].id,
            status_id=projected.id,
            name="Groceries",
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=expense_type.id,
            estimated_amount=Decimal("500.00"),
        )
        db.session.add(txn)
        db.session.flush()

        for amount, entry_date, is_credit, is_cleared in entries:
            db.session.add(TransactionEntry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                amount=Decimal(amount),
                description="Test purchase",
                entry_date=entry_date,
                is_credit=is_credit,
                is_cleared=is_cleared,
            ))
        db.session.commit()
        return txn

    def test_past_dated_projected_entries_get_cleared(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """True-up flips past-dated uncleared debits on projected parents."""
        from app.models.transaction_entry import TransactionEntry

        with app.app_context():
            past = date.today() - __import__("datetime").timedelta(days=1)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [
                    ("106.86", past, False, False),
                    ("249.71", past, False, False),
                    ("105.77", past, False, False),
                ],
            )

            response = auth_client.patch(
                f"/accounts/{seed_user['account'].id}/true-up",
                data={"anchor_balance": "4537.66"},
            )
            assert response.status_code == 200

            db.session.expire_all()
            entries = (
                db.session.query(TransactionEntry)
                .filter_by(transaction_id=txn.id)
                .all()
            )
            assert len(entries) == 3
            assert all(e.is_cleared for e in entries)

    def test_future_dated_entries_not_cleared(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Entries with entry_date > today must NOT be flipped by true-up."""
        from app.models.transaction_entry import TransactionEntry

        with app.app_context():
            future = date.today() + __import__("datetime").timedelta(days=7)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [
                    ("50.00", future, False, False),
                ],
            )

            response = auth_client.patch(
                f"/accounts/{seed_user['account'].id}/true-up",
                data={"anchor_balance": "5000.00"},
            )
            assert response.status_code == 200

            db.session.expire_all()
            entry = (
                db.session.query(TransactionEntry)
                .filter_by(transaction_id=txn.id)
                .one()
            )
            assert entry.is_cleared is False

    def test_entries_on_non_projected_parent_not_cleared(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Entries on settled (Paid) parents are not touched by true-up.

        They're already excluded from the balance formula, but leaving
        their is_cleared alone is the correct behavior -- we only flip
        what the anchor change actually reconciles.
        """
        from app.models.transaction_entry import TransactionEntry

        with app.app_context():
            past = date.today() - __import__("datetime").timedelta(days=1)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [
                    ("100.00", past, False, False),
                ],
            )
            # Flip the parent to Paid after entry creation.
            paid = db.session.query(Status).filter_by(name="Paid").one()
            txn.status_id = paid.id
            db.session.commit()

            response = auth_client.patch(
                f"/accounts/{seed_user['account'].id}/true-up",
                data={"anchor_balance": "5000.00"},
            )
            assert response.status_code == 200

            db.session.expire_all()
            entry = (
                db.session.query(TransactionEntry)
                .filter_by(transaction_id=txn.id)
                .one()
            )
            assert entry.is_cleared is False

    def test_non_checking_true_up_does_not_clear(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A true-up on a non-checking account does not affect entries.

        Debit entries only hit checking, so anchor updates on savings
        or loan accounts must not touch is_cleared.
        """
        from app.models.transaction_entry import TransactionEntry

        with app.app_context():
            past = date.today() - __import__("datetime").timedelta(days=1)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [
                    ("100.00", past, False, False),
                ],
            )

            # Create a non-checking account for the user.
            savings_type = db.session.query(AccountType).filter_by(
                name="Savings",
            ).one()
            savings = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Savings",
                current_anchor_balance=Decimal("1000.00"),
                current_anchor_period_id=seed_periods_today[0].id,
            )
            db.session.add(savings)
            db.session.commit()

            response = auth_client.patch(
                f"/accounts/{savings.id}/true-up",
                data={"anchor_balance": "1500.00"},
            )
            assert response.status_code == 200

            db.session.expire_all()
            entry = (
                db.session.query(TransactionEntry)
                .filter_by(transaction_id=txn.id)
                .one()
            )
            # Debit entries untouched because savings != checking.
            assert entry.is_cleared is False

    def test_already_cleared_entries_unchanged(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Entries that are already cleared remain cleared -- true-up is idempotent."""
        from app.models.transaction_entry import TransactionEntry

        with app.app_context():
            past = date.today() - __import__("datetime").timedelta(days=1)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [
                    ("100.00", past, False, True),
                ],
            )

            response = auth_client.patch(
                f"/accounts/{seed_user['account'].id}/true-up",
                data={"anchor_balance": "5000.00"},
            )
            assert response.status_code == 200

            db.session.expire_all()
            entry = (
                db.session.query(TransactionEntry)
                .filter_by(transaction_id=txn.id)
                .one()
            )
            assert entry.is_cleared is True


# ── Account Type CRUD ─────────────────────────────────────────────


class TestAccountTypes:
    """Tests for account type create, rename, and delete."""

    def test_create_account_type(self, app, auth_client, seed_user):
        """POST /accounts/types creates a new account type with category."""
        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            response = auth_client.post(
                "/accounts/types",
                data={"name": "investment", "category_id": asset_id},
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Account type &#39;investment&#39; created." in response.data

            acct_type = (
                db.session.query(AccountType).filter_by(name="investment").one()
            )
            assert acct_type.name == "investment"
            assert acct_type.category_id == asset_id

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
            assert b"updated" in response.data

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
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            # "Checking" already exists from ref seed (exact case match required).
            response = auth_client.post(
                "/accounts/types",
                data={"name": "Checking", "category_id": asset_id},
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


# ── Account Type Metadata Validation ─────────────────────────────


class TestAccountTypeMetadataValidation:
    """Tests for cross-field validation on account type create/update schemas."""

    def test_create_account_type_with_category(self, app, auth_client, seed_user):
        """POST with category and flags creates a type with correct metadata."""
        with app.app_context():
            liability_id = ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY)
            resp = auth_client.post(
                "/accounts/types",
                data={
                    "name": "Test Debt",
                    "category_id": liability_id,
                    "has_parameters": "true",
                    "has_amortization": "true",
                    "max_term_months": "240",
                    "icon_class": "bi-cash-coin",
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"created" in resp.data

            acct_type = db.session.query(AccountType).filter_by(
                name="Test Debt",
            ).one()
            assert acct_type.category_id == liability_id
            assert acct_type.has_parameters is True
            assert acct_type.has_amortization is True
            assert acct_type.max_term_months == 240
            assert acct_type.icon_class == "bi-cash-coin"

    def test_create_account_type_invalid_flag_combo(
        self, app, auth_client, seed_user,
    ):
        """has_amortization=True with Asset category is rejected."""
        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            resp = auth_client.post(
                "/accounts/types",
                data={
                    "name": "Bad Combo",
                    "category_id": asset_id,
                    "has_amortization": "true",
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200
            # Validation error redirects with flash.
            assert b"correct the highlighted errors" in resp.data
            # Type should NOT have been created.
            assert db.session.query(AccountType).filter_by(
                name="Bad Combo",
            ).first() is None

    def test_create_account_type_mutual_exclusion(
        self, app, auth_client, seed_user,
    ):
        """has_amortization and has_interest together is rejected."""
        with app.app_context():
            liability_id = ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY)
            resp = auth_client.post(
                "/accounts/types",
                data={
                    "name": "Bad Exclusive",
                    "category_id": liability_id,
                    "has_amortization": "true",
                    "has_interest": "true",
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"correct the highlighted errors" in resp.data

    def test_max_term_without_amortization(self, app, auth_client, seed_user):
        """max_term_months without has_amortization is rejected."""
        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            resp = auth_client.post(
                "/accounts/types",
                data={
                    "name": "Bad Term",
                    "category_id": asset_id,
                    "max_term_months": "120",
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"correct the highlighted errors" in resp.data

    def test_update_account_type_metadata(self, app, auth_client, seed_user):
        """POST update changes metadata fields."""
        with app.app_context():
            new_type = AccountType(
                name="update_meta_test",
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.ASSET),
            )
            db.session.add(new_type)
            db.session.commit()

            resp = auth_client.post(
                f"/accounts/types/{new_type.id}",
                data={
                    "name": "update_meta_test",
                    "has_parameters": "true",
                    "has_interest": "true",
                    "is_liquid": "true",
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"updated" in resp.data

            db.session.refresh(new_type)
            assert new_type.has_parameters is True
            assert new_type.has_interest is True
            assert new_type.is_liquid is True

    def test_update_account_type_multidict_checkboxes(self, app, auth_client, seed_user):
        """Boolean flags resolve correctly from MultiDict form data.

        Browsers submit hidden-input + checkbox pairs as duplicate keys
        in a MultiDict.  Checked checkboxes send ('field', 'false') and
        ('field', 'true'); unchecked send only ('field', 'false').  The
        schema must take the last value so checked boxes resolve to True.
        Regression test for a bug where Flask's MultiDict.items() returned
        only the first value, making all booleans always False.
        """
        from werkzeug.datastructures import MultiDict

        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            new_type = AccountType(
                name="multidict_test",
                category_id=asset_id,
            )
            db.session.add(new_type)
            db.session.commit()

            # Simulate browser form with checked checkboxes (hidden + checkbox).
            resp = auth_client.post(
                f"/accounts/types/{new_type.id}",
                data=MultiDict([
                    ("name", "multidict_test"),
                    ("category_id", str(asset_id)),
                    ("has_parameters", "false"),
                    ("has_parameters", "true"),
                    ("has_amortization", "false"),
                    ("has_interest", "false"),
                    ("has_interest", "true"),
                    ("is_pretax", "false"),
                    ("is_liquid", "false"),
                    ("is_liquid", "true"),
                    ("icon_class", "bi-cash-stack"),
                    ("max_term_months", ""),
                ]),
                follow_redirects=True,
            )
            assert resp.status_code == 200

            db.session.refresh(new_type)
            # Checked checkboxes must resolve to True.
            assert new_type.has_parameters is True
            assert new_type.has_interest is True
            assert new_type.is_liquid is True
            # Unchecked checkboxes must resolve to False.
            assert new_type.has_amortization is False
            assert new_type.is_pretax is False

    def test_update_account_type_multidict_all_unchecked(self, app, auth_client, seed_user):
        """Unchecking all boolean flags via MultiDict sets them to False.

        Starts with all flags True, then submits a form where every
        checkbox is unchecked (only hidden 'false' values sent).
        """
        from werkzeug.datastructures import MultiDict

        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            new_type = AccountType(
                name="multidict_uncheck_test",
                category_id=asset_id,
                has_parameters=True,
                has_interest=True,
                is_liquid=True,
            )
            db.session.add(new_type)
            db.session.commit()

            # Simulate browser form with all checkboxes unchecked.
            resp = auth_client.post(
                f"/accounts/types/{new_type.id}",
                data=MultiDict([
                    ("name", "multidict_uncheck_test"),
                    ("category_id", str(asset_id)),
                    ("has_parameters", "false"),
                    ("has_amortization", "false"),
                    ("has_interest", "false"),
                    ("is_pretax", "false"),
                    ("is_liquid", "false"),
                    ("icon_class", "bi-cash-stack"),
                    ("max_term_months", ""),
                ]),
                follow_redirects=True,
            )
            assert resp.status_code == 200

            db.session.refresh(new_type)
            assert new_type.has_parameters is False
            assert new_type.has_interest is False
            assert new_type.is_liquid is False


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

    def test_archive_nonexistent_account(self, app, auth_client, seed_user):
        """POST /accounts/999999/archive for a nonexistent account redirects with flash."""
        with app.app_context():
            resp = auth_client.post(
                "/accounts/999999/archive", follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"Account not found." in resp.data

    def test_unarchive_other_users_account_idor(
        self, app, auth_client, seed_user, second_user
    ):
        """POST /accounts/<id>/unarchive for another user's archived account is blocked."""
        with app.app_context():
            # Re-query to ensure the object is in the current session.
            acct_id = second_user["account"].id
            other_acct = db.session.get(Account, acct_id)
            other_acct.is_active = False
            db.session.commit()

            resp = auth_client.post(
                f"/accounts/{acct_id}/unarchive",
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"Account not found." in resp.data

            # Verify DB state unchanged: account still inactive.
            db.session.expire_all()
            refreshed = db.session.get(Account, acct_id)
            assert refreshed.is_active is False

    def test_archive_already_inactive_account(self, app, auth_client, seed_user):
        """POST /accounts/<id>/archive on an already-inactive account is idempotent."""
        with app.app_context():
            account_id = seed_user["account"].id

            # First archive via the route.
            resp1 = auth_client.post(
                f"/accounts/{account_id}/archive",
                follow_redirects=True,
            )
            assert resp1.status_code == 200
            assert b"archived" in resp1.data

            # Second archive -- account is already inactive.
            resp2 = auth_client.post(
                f"/accounts/{account_id}/archive",
                follow_redirects=True,
            )

            # Route does not guard against double-archive; it sets
            # is_active=False and commits. This is idempotent behavior.
            assert resp2.status_code == 200
            assert b"archived" in resp2.data

            db.session.expire_all()
            refreshed = db.session.get(Account, account_id)
            assert refreshed.is_active is False

    def test_unarchive_already_active_account(self, app, auth_client, seed_user):
        """POST /accounts/<id>/unarchive on an already-active account is idempotent."""
        with app.app_context():
            account_id = seed_user["account"].id

            # Account starts active (default from seed). Unarchive anyway.
            resp = auth_client.post(
                f"/accounts/{account_id}/unarchive",
                follow_redirects=True,
            )

            # Route does not guard against unarchiving an already-active
            # account; it sets is_active=True and commits.
            assert resp.status_code == 200
            assert b"unarchived" in resp.data

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
        """HYSA creation redirects to HYSA detail with setup=1 and auto-creates InterestParams."""
        with app.app_context():
            hysa_type = db.session.query(AccountType).filter_by(name="HYSA").one()

            resp = auth_client.post("/accounts", data={
                "name": "My HYSA",
                "account_type_id": hysa_type.id,
                "anchor_balance": "5000.00",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert "/interest" in location
            assert "setup=1" in location

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="My HYSA",
            ).one()
            assert db.session.query(InterestParams).filter_by(
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


# ── Metadata-Driven Interest Dispatch ────────────────────────────


class TestInterestDispatch:
    """Verify that has_interest metadata flag drives auto-creation,
    redirect, and detail page access instead of hardcoded HYSA type ID."""

    def test_create_account_hsa_auto_creates_interest_params(
        self, app, auth_client, seed_user,
    ):
        """HSA has has_interest=True; creating one auto-creates InterestParams."""
        with app.app_context():
            hsa_type = db.session.query(AccountType).filter_by(name="HSA").one()
            assert hsa_type.has_interest is True

            resp = auth_client.post("/accounts", data={
                "name": "My HSA",
                "account_type_id": hsa_type.id,
                "anchor_balance": "1200.00",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert "/interest" in location
            assert "setup=1" in location

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="My HSA",
            ).one()
            params = db.session.query(InterestParams).filter_by(
                account_id=acct.id,
            ).first()
            assert params is not None, "InterestParams not auto-created for HSA"

    def test_create_account_money_market_with_interest(
        self, app, auth_client, seed_user, db,
    ):
        """Money Market with has_interest=True auto-creates InterestParams."""
        with app.app_context():
            mm_type = db.session.query(AccountType).filter_by(
                name="Money Market",
            ).one()
            mm_type.has_interest = True
            mm_type.has_parameters = True
            db.session.commit()

            resp = auth_client.post("/accounts", data={
                "name": "My MM",
                "account_type_id": mm_type.id,
                "anchor_balance": "3000.00",
            })

            assert resp.status_code == 302
            assert "/interest" in resp.headers["Location"]

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="My MM",
            ).one()
            params = db.session.query(InterestParams).filter_by(
                account_id=acct.id,
            ).first()
            assert params is not None

    def test_interest_detail_accepts_any_interest_type(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """Interest detail page renders for any has_interest=True type."""
        with app.app_context():
            hsa_type = db.session.query(AccountType).filter_by(name="HSA").one()
            acct = Account(
                user_id=seed_user["user"].id,
                name="HSA Detail Test",
                account_type_id=hsa_type.id,
                current_anchor_balance=500,
                current_anchor_period_id=seed_periods_today[0].id,
            )
            db.session.add(acct)
            db.session.flush()
            db.session.add(InterestParams(account_id=acct.id))
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/interest")
            assert resp.status_code == 200

    def test_interest_detail_rejects_non_interest_type(
        self, app, auth_client, seed_user,
    ):
        """Checking (has_interest=False) is rejected by interest detail."""
        with app.app_context():
            acct = seed_user["account"]
            resp = auth_client.get(
                f"/accounts/{acct.id}/interest", follow_redirects=True,
            )
            assert b"does not support interest parameters" in resp.data

    def test_has_interest_true_but_no_params_row(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """Interest detail auto-creates params if row missing."""
        with app.app_context():
            hsa_type = db.session.query(AccountType).filter_by(name="HSA").one()
            acct = Account(
                user_id=seed_user["user"].id,
                name="HSA No Params",
                account_type_id=hsa_type.id,
                current_anchor_balance=100,
                current_anchor_period_id=seed_periods_today[0].id,
            )
            db.session.add(acct)
            db.session.commit()

            # No InterestParams row exists yet.
            assert db.session.query(InterestParams).filter_by(
                account_id=acct.id,
            ).first() is None

            resp = auth_client.get(f"/accounts/{acct.id}/interest")
            assert resp.status_code == 200

            # Auto-created by the detail route's safety fallback.
            assert db.session.query(InterestParams).filter_by(
                account_id=acct.id,
            ).first() is not None


# ── Investment Dispatch (Metadata-Driven) ────────────────────────


class TestInvestmentDispatch:
    """Verify that investment/retirement auto-creation and redirect use
    metadata flags instead of hardcoded type ID frozensets."""

    def test_create_account_user_type_retirement_auto_creates_params(
        self, app, auth_client, seed_user, db,
    ):
        """A user-created Retirement type with has_parameters=True auto-creates
        InvestmentParams and redirects to the investment dashboard."""
        from app import ref_cache
        from app.enums import AcctCategoryEnum

        with app.app_context():
            custom_type = AccountType(
                name="TestSEPIRA",
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.RETIREMENT),
                has_parameters=True,
            )
            db.session.add(custom_type)
            db.session.commit()

            resp = auth_client.post("/accounts", data={
                "name": "My SEP IRA",
                "account_type_id": custom_type.id,
                "anchor_balance": "10000.00",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert "/investment" in location
            assert "setup=1" in location

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="My SEP IRA",
            ).one()
            params = db.session.query(InvestmentParams).filter_by(
                account_id=acct.id,
            ).first()
            assert params is not None, "InvestmentParams not auto-created"

    def test_has_parameters_false_no_auto_create(
        self, app, auth_client, seed_user,
    ):
        """An account type with has_parameters=False gets no params and
        redirects to the accounts list."""
        with app.app_context():
            # Savings has has_parameters=False.
            savings_type = db.session.query(AccountType).filter_by(
                name="Savings",
            ).one()
            assert savings_type.has_parameters is False

            resp = auth_client.post("/accounts", data={
                "name": "Plain Savings",
                "account_type_id": savings_type.id,
                "anchor_balance": "500.00",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert "/accounts" in location
            assert "setup" not in location

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="Plain Savings",
            ).one()
            assert db.session.query(InterestParams).filter_by(
                account_id=acct.id,
            ).first() is None
            assert db.session.query(InvestmentParams).filter_by(
                account_id=acct.id,
            ).first() is None

    def test_529_plan_has_parameters_true_in_seed(self, app, seed_user):
        """529 Plan has has_parameters=True in seed data."""
        with app.app_context():
            plan_type = db.session.query(AccountType).filter_by(
                name="529 Plan",
            ).one()
            assert plan_type.has_parameters is True

    def test_create_account_529_auto_creates_investment_params(
        self, app, auth_client, seed_user,
    ):
        """529 Plan auto-creates InvestmentParams and redirects to investment dashboard."""
        with app.app_context():
            plan_type = db.session.query(AccountType).filter_by(
                name="529 Plan",
            ).one()

            resp = auth_client.post("/accounts", data={
                "name": "College Fund",
                "account_type_id": plan_type.id,
                "anchor_balance": "2000.00",
            })

            assert resp.status_code == 302
            assert "/investment" in resp.headers["Location"]

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="College Fund",
            ).one()
            assert db.session.query(InvestmentParams).filter_by(
                account_id=acct.id,
            ).first() is not None


# ── Wizard Banner Tests ──────────────────────────────────────────


class TestWizardBanner:
    """Tests for the setup wizard banner on parameter pages.

    The banner appears when ?setup=1 is in the query string, indicating
    the user just created the account and should review configuration.
    """

    def test_wizard_banner_shown_on_hysa_with_setup_param(
        self, app, auth_client, seed_user, seed_periods_today,
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
                current_anchor_period_id=seed_periods_today[0].id,
            )
            db.session.add(acct)
            db.session.flush()
            db.session.add(InterestParams(account_id=acct.id))
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/interest?setup=1")
            assert resp.status_code == 200
            assert b"Configure the settings below" in resp.data
            assert b"alert-dismissible" in resp.data

    def test_wizard_banner_hidden_on_hysa_without_setup_param(
        self, app, auth_client, seed_user, seed_periods_today,
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
                current_anchor_period_id=seed_periods_today[0].id,
            )
            db.session.add(acct)
            db.session.flush()
            db.session.add(InterestParams(account_id=acct.id))
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/interest")
            assert resp.status_code == 200
            assert b"Configure the settings below" not in resp.data

    def test_wizard_banner_shown_on_investment_with_setup_param(
        self, app, auth_client, seed_user, seed_periods_today,
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
                current_anchor_period_id=seed_periods_today[0].id,
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
        self, app, auth_client, seed_user, seed_periods_today,
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
                current_anchor_period_id=seed_periods_today[0].id,
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


# ── Optimistic Locking (commit C-17 / F-009) ────────────────────────


def _bump_account_version_outside_session(account_id):
    """Simulate a concurrent commit by bumping ``version_id`` directly.

    Uses a fresh DB connection (NOT the test session) so the in-memory
    identity map of the calling session is unaffected.  After this
    helper returns, any object the caller previously loaded for
    ``account_id`` retains its old in-memory ``version_id`` while the
    database row carries the bumped value -- exactly the state a
    concurrent request from another browser tab would produce.

    The connection commit is essential: without it the UPDATE would
    sit in an open transaction and ``READ COMMITTED`` MVCC would
    hide the bump from the test session.
    """
    with db.engine.connect() as conn:
        conn.execute(
            text(
                "UPDATE budget.accounts "
                "SET version_id = version_id + 1 "
                "WHERE id = :id"
            ),
            {"id": account_id},
        )
        conn.commit()


class TestAccountVersionIdColumn:
    """Schema-level invariants for the optimistic-lock counter."""

    def test_version_id_column_present_and_not_null(self, app):
        """The live ``budget.accounts`` table carries a NOT NULL ``version_id``."""
        with app.app_context():
            insp = inspect(db.engine)
            cols = {
                c["name"]: c
                for c in insp.get_columns("accounts", schema="budget")
            }

            assert "version_id" in cols, (
                "Account.version_id column is missing from the live "
                "schema -- migration 861a48e11960 may not have run."
            )
            assert cols["version_id"]["nullable"] is False, (
                "Account.version_id must be NOT NULL or the optimistic "
                "lock silently fails on rows that have a NULL counter."
            )

    def test_version_id_check_constraint_present(self, app):
        """The CHECK constraint that pins ``version_id > 0`` is on the live table."""
        with app.app_context():
            insp = inspect(db.engine)
            checks = {
                c["name"]: c["sqltext"]
                for c in insp.get_check_constraints(
                    "accounts", schema="budget",
                )
            }

            assert "ck_accounts_version_id_positive" in checks, (
                "ck_accounts_version_id_positive missing -- the schema "
                "no longer matches the model declaration."
            )
            # PostgreSQL normalises the predicate; either form is valid.
            normalised = checks["ck_accounts_version_id_positive"].lower().replace(" ", "")
            assert "version_id>0" in normalised, (
                "CHECK constraint expression has changed; rerun the "
                "migration or update the model in lockstep."
            )

    def test_version_id_check_rejects_zero(self, app, db, seed_user):
        """Inserting a row with ``version_id = 0`` raises IntegrityError.

        The application never sets ``version_id`` directly; this test
        exercises the database-tier guard against a future raw-SQL
        path or a buggy migration that writes 0.
        """
        with app.app_context():
            checking_type = (
                db.session.query(AccountType).filter_by(name="Checking").one()
            )
            with pytest.raises(IntegrityError):
                db.session.execute(
                    text(
                        "INSERT INTO budget.accounts "
                        "(user_id, account_type_id, name, version_id) "
                        "VALUES (:u, :t, :n, 0)"
                    ),
                    {
                        "u": seed_user["user"].id,
                        "t": checking_type.id,
                        "n": "Bad Version",
                    },
                )
                db.session.flush()
            db.session.rollback()

    def test_mapper_declares_version_id_col(self, app):
        """``Account.__mapper_args__`` exposes the version counter to SQLAlchemy.

        Without this declaration SQLAlchemy emits ``UPDATE`` without
        the ``WHERE version_id = ?`` narrowing and the optimistic-lock
        contract collapses; the rest of the test class would still pass
        but production would silently regress.
        """
        with app.app_context():
            mapper = inspect(Account)
            assert mapper.version_id_col is not None, (
                "Account mapper has no version_id_col -- "
                "__mapper_args__ regression."
            )
            assert mapper.version_id_col.name == "version_id"


class TestAccountVersionIdLifecycle:
    """End-to-end behaviour of the ``version_id`` counter through ORM operations."""

    def test_new_account_starts_at_version_one(self, app, auth_client, seed_user):
        """Newly created accounts have ``version_id == 1``.

        ``server_default='1'`` on the column guarantees this for rows
        inserted via SQLAlchemy with no explicit ``version_id``.  The
        seed_user fixture path exercises this exact code path.
        """
        with app.app_context():
            acct = db.session.get(Account, seed_user["account"].id)
            assert acct.version_id == 1

    def test_seed_user_account_starts_at_version_one(self, app, seed_user):
        """The seed fixture's account has ``version_id == 1`` after creation."""
        with app.app_context():
            acct = db.session.get(Account, seed_user["account"].id)
            assert acct.version_id == 1, (
                f"seed_user fixture account should start at version 1, "
                f"got {acct.version_id}"
            )

    def test_version_does_not_increment_on_read(self, app, db, seed_user):
        """Pure SELECT operations leave ``version_id`` unchanged.

        The optimistic-lock contract increments only on UPDATE/DELETE;
        a regression here would inflate the counter on every page
        view and turn every form submit into a stale-form 409.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            initial_version = db.session.get(Account, acct_id).version_id

            for _ in range(5):
                _ = db.session.get(Account, acct_id).current_anchor_balance
                db.session.expire_all()

            final_version = db.session.get(Account, acct_id).version_id
            assert final_version == initial_version

    def test_version_increments_on_update(self, app, db, seed_user):
        """Each ORM-emitted UPDATE bumps ``version_id`` by exactly one."""
        with app.app_context():
            acct_id = seed_user["account"].id
            v0 = db.session.get(Account, acct_id).version_id

            acct = db.session.get(Account, acct_id)
            acct.name = "Renamed Once"
            db.session.commit()
            v1 = db.session.get(Account, acct_id).version_id

            acct.name = "Renamed Twice"
            db.session.commit()
            v2 = db.session.get(Account, acct_id).version_id

            assert v1 == v0 + 1
            assert v2 == v1 + 1


class TestAccountConcurrentMutationStaleData:
    """SQLAlchemy ``StaleDataError`` is raised on truly concurrent races."""

    def test_concurrent_update_raises_stale_data_error(
        self, app, db, seed_user,
    ):
        """A race that bumps the version between load and commit raises StaleDataError.

        The simulated concurrent commit advances the row to version 2;
        the test session, still holding an in-memory copy at version
        1, attempts an UPDATE -- the version-pinned WHERE matches no
        rows and SQLAlchemy raises ``StaleDataError``.  This is the
        load-bearing invariant that makes the SQLAlchemy tier of the
        optimistic lock work.
        """
        with app.app_context():
            acct_id = seed_user["account"].id

            acct = db.session.get(Account, acct_id)
            assert acct.version_id == 1

            _bump_account_version_outside_session(acct_id)

            acct.current_anchor_balance = Decimal("9999.00")

            with pytest.raises(StaleDataError):
                db.session.commit()

            db.session.rollback()

            db.session.expire_all()
            persisted = db.session.get(Account, acct_id)
            assert persisted.current_anchor_balance != Decimal("9999.00")
            assert persisted.version_id == 2

    def test_concurrent_delete_raises_stale_data_error(
        self, app, db, seed_user,
    ):
        """DELETE also enforces the version pin; concurrent bump blocks the delete."""
        with app.app_context():
            checking_type = (
                db.session.query(AccountType)
                .filter_by(name="Checking").one()
            )
            spare = Account(
                user_id=seed_user["user"].id,
                account_type_id=checking_type.id,
                name="Spare",
                current_anchor_balance=Decimal("0.00"),
            )
            db.session.add(spare)
            db.session.commit()
            spare_id = spare.id

            _bump_account_version_outside_session(spare_id)

            db.session.delete(spare)
            with pytest.raises(StaleDataError):
                db.session.commit()
            db.session.rollback()

            persisted = db.session.get(Account, spare_id)
            assert persisted is not None, (
                "Stale-data DELETE must leave the row intact for the "
                "winner of the race to handle."
            )


class TestTrueUpStaleForm:
    """``true_up`` (PATCH /accounts/<id>/true-up) optimistic-locking behaviour."""

    def test_true_up_succeeds_with_matching_version(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A submitted ``version_id`` that matches the row succeeds and bumps the counter."""
        with app.app_context():
            acct_id = seed_user["account"].id
            initial_version = db.session.get(Account, acct_id).version_id

            response = auth_client.patch(
                f"/accounts/{acct_id}/true-up",
                data={
                    "anchor_balance": "1100.00",
                    "version_id": str(initial_version),
                },
            )

            assert response.status_code == 200, response.data

            db.session.expire_all()
            acct = db.session.get(Account, acct_id)
            assert acct.current_anchor_balance == Decimal("1100.00")
            assert acct.version_id == initial_version + 1

    def test_true_up_returns_409_on_stale_version(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A submitted ``version_id`` older than the current row returns 409.

        The route MUST short-circuit before touching the database; the
        anchor balance and ``AccountAnchorHistory`` table both stay
        unchanged.  This is the manual-verification scenario from the
        C-17 plan: Tab 1 holds an old version, Tab 2 commits to bump
        the row, Tab 1 resubmits with the stale version -- the
        server must refuse.

        ``stale_version`` is captured from the row state before the
        bump rather than hard-coded to 1; ``seed_periods_today`` also
        commits an UPDATE to set the anchor period and would otherwise
        leave the row at version 2 before this test even runs.
        """
        with app.app_context():
            acct_id = seed_user["account"].id

            # Capture the version Tab 1 would have loaded.
            stale_version = db.session.get(Account, acct_id).version_id

            # Simulate Tab 2 having already advanced the row.
            _bump_account_version_outside_session(acct_id)
            db.session.expire_all()
            current_version = db.session.get(Account, acct_id).version_id
            assert current_version == stale_version + 1, (
                "fixture invariant: bump must advance the version by "
                "exactly one"
            )
            balance_before = db.session.get(Account, acct_id).current_anchor_balance
            history_count_before = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=acct_id).count()
            )

            response = auth_client.patch(
                f"/accounts/{acct_id}/true-up",
                data={
                    "anchor_balance": "1200.00",
                    "version_id": str(stale_version),
                },
            )

            assert response.status_code == 409, (
                f"stale version_id must produce 409 Conflict, got "
                f"{response.status_code}: {response.data!r}"
            )
            # The conflict UI must include the "changed by another action"
            # affordance and the latest balance.
            body = response.data.decode()
            assert "changed by another action" in body.lower()
            # The display partial uses the warning class plus icon.
            assert "text-warning" in body
            assert "exclamation-triangle" in body

            db.session.expire_all()
            acct = db.session.get(Account, acct_id)
            assert acct.current_anchor_balance == balance_before, (
                "Stale-form 409 must NOT mutate the anchor balance."
            )
            assert acct.version_id == current_version, (
                "Stale-form 409 must NOT bump the version counter."
            )
            assert (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=acct_id).count()
            ) == history_count_before, (
                "Stale-form 409 must NOT write a history row -- the "
                "audit trail records only the winner."
            )

    def test_true_up_route_catches_stale_data_error_as_409(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """A StaleDataError raised at flush time is converted to a 409 response.

        Engineers a true race using a SQLAlchemy mapper event: the
        route loads the row at version N, mutates it, then begins
        the flush; the event listener fires during the UPDATE and
        bumps the row from a separate connection, defeating the
        version-pinned WHERE clause.  SQLAlchemy raises
        ``StaleDataError`` and the route's ``except`` clause
        converts it into the same 409 + conflict partial the form-
        side check produces.  This exercises the SQLAlchemy-tier
        of the optimistic lock end-to-end through the HTTP layer.
        """
        from sqlalchemy import event  # pylint: disable=import-outside-toplevel

        with app.app_context():
            acct_id = seed_user["account"].id
            balance_before = db.session.get(Account, acct_id).current_anchor_balance

            fired = {"flag": False}

            def make_stale(mapper, connection, target):
                """Bump version_id from a separate connection mid-flush."""
                if fired["flag"] or target.id != acct_id:
                    return
                fired["flag"] = True
                _bump_account_version_outside_session(acct_id)

            event.listen(Account, "before_update", make_stale)
            try:
                response = auth_client.patch(
                    f"/accounts/{acct_id}/true-up",
                    data={"anchor_balance": "5555.00"},
                )
            finally:
                event.remove(Account, "before_update", make_stale)

            assert response.status_code == 409, (
                f"StaleDataError must convert to 409, got "
                f"{response.status_code}"
            )
            body = response.data.decode()
            assert "changed by another action" in body.lower()
            assert "exclamation-triangle" in body

            db.session.expire_all()
            persisted = db.session.get(Account, acct_id)
            assert persisted.current_anchor_balance == balance_before, (
                "StaleDataError-on-commit must roll back the pending "
                "balance change."
            )

    def test_true_up_omitted_version_falls_through_to_db_check(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Omitting ``version_id`` skips the form-side check.

        Backwards-compat: a future programmatic client that has no
        way to plumb the version through still validates and reaches
        the SQLAlchemy-tier check.  In the no-conflict case the
        update succeeds; in the conflict case StaleDataError fires
        on flush -- both are tested elsewhere.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            v0 = db.session.get(Account, acct_id).version_id

            response = auth_client.patch(
                f"/accounts/{acct_id}/true-up",
                data={"anchor_balance": "1400.00"},
            )

            assert response.status_code == 200
            db.session.expire_all()
            acct = db.session.get(Account, acct_id)
            assert acct.current_anchor_balance == Decimal("1400.00")
            assert acct.version_id == v0 + 1


class TestInlineAnchorStaleForm:
    """``inline_anchor_update`` (PATCH /accounts/<id>/inline-anchor) optimistic locking."""

    def test_inline_anchor_succeeds_with_matching_version(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A matching ``version_id`` updates the balance and bumps the counter."""
        with app.app_context():
            acct_id = seed_user["account"].id
            v0 = db.session.get(Account, acct_id).version_id

            response = auth_client.patch(
                f"/accounts/{acct_id}/inline-anchor",
                data={
                    "anchor_balance": "2500.00",
                    "version_id": str(v0),
                },
            )

            assert response.status_code == 200
            db.session.expire_all()
            acct = db.session.get(Account, acct_id)
            assert acct.current_anchor_balance == Decimal("2500.00")
            assert acct.version_id == v0 + 1

    def test_inline_anchor_returns_409_on_stale_version(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A stale ``version_id`` returns 409 with the conflict partial."""
        with app.app_context():
            acct_id = seed_user["account"].id
            stale_version = db.session.get(Account, acct_id).version_id

            _bump_account_version_outside_session(acct_id)
            db.session.expire_all()
            balance_before = db.session.get(Account, acct_id).current_anchor_balance
            history_count_before = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=acct_id).count()
            )

            response = auth_client.patch(
                f"/accounts/{acct_id}/inline-anchor",
                data={
                    "anchor_balance": "9999.99",
                    "version_id": str(stale_version),
                },
            )

            assert response.status_code == 409
            body = response.data.decode()
            assert "changed by another action" in body.lower()
            assert "text-warning" in body

            db.session.expire_all()
            acct = db.session.get(Account, acct_id)
            assert acct.current_anchor_balance == balance_before
            assert (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=acct_id).count()
            ) == history_count_before


class TestUpdateAccountStaleForm:
    """``update_account`` (POST /accounts/<id>) optimistic locking on the full edit form."""

    def test_update_account_succeeds_with_matching_version(
        self, app, auth_client, seed_user,
    ):
        """A matching ``version_id`` on the edit form updates and bumps the counter."""
        with app.app_context():
            acct_id = seed_user["account"].id
            checking_type = (
                db.session.query(AccountType).filter_by(name="Checking").one()
            )
            v0 = db.session.get(Account, acct_id).version_id

            response = auth_client.post(
                f"/accounts/{acct_id}",
                data={
                    "name": "Primary Checking",
                    "account_type_id": str(checking_type.id),
                    "version_id": str(v0),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Account &#39;Primary Checking&#39; updated." in response.data

            db.session.expire_all()
            acct = db.session.get(Account, acct_id)
            assert acct.name == "Primary Checking"
            assert acct.version_id == v0 + 1

    def test_update_account_redirects_with_warning_on_stale_version(
        self, app, auth_client, seed_user,
    ):
        """A stale ``version_id`` redirects back to the edit form with a warning flash.

        The non-HTMX update_account path uses flash + redirect rather
        than a 409 partial because the surrounding UX is a full-page
        form, not a swap.  The behaviour invariant is the same: NO
        write occurs and the user is told the row changed.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            checking_type = (
                db.session.query(AccountType).filter_by(name="Checking").one()
            )
            stale_version = db.session.get(Account, acct_id).version_id

            _bump_account_version_outside_session(acct_id)
            db.session.expire_all()
            name_before = db.session.get(Account, acct_id).name

            response = auth_client.post(
                f"/accounts/{acct_id}",
                data={
                    "name": "Should Not Apply",
                    "account_type_id": str(checking_type.id),
                    "version_id": str(stale_version),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"changed by another action" in response.data.lower()

            db.session.expire_all()
            acct = db.session.get(Account, acct_id)
            assert acct.name == name_before, (
                "Stale-form on update_account must NOT mutate any field."
            )


class TestArchiveAndDeleteStaleData:
    """``archive_account`` / ``unarchive_account`` / ``hard_delete_account`` StaleDataError handling."""

    def test_archive_account_stale_data_redirects_with_warning(
        self, app, db, auth_client, seed_user,
    ):
        """A StaleDataError during archive surfaces as a flash + redirect.

        The contract: the user always receives a useful response,
        never a 500.  The account stays unchanged; the user reloads
        and retries.
        """
        from sqlalchemy import event  # pylint: disable=import-outside-toplevel

        with app.app_context():
            checking_type = (
                db.session.query(AccountType).filter_by(name="Checking").one()
            )
            spare = Account(
                user_id=seed_user["user"].id,
                account_type_id=checking_type.id,
                name="Archive Target",
                current_anchor_balance=Decimal("0.00"),
                is_active=True,
            )
            db.session.add(spare)
            db.session.commit()
            spare_id = spare.id

            fired = {"flag": False}

            def make_stale(mapper, connection, target):
                if fired["flag"] or target.id != spare_id:
                    return
                fired["flag"] = True
                _bump_account_version_outside_session(spare_id)

            event.listen(Account, "before_update", make_stale)
            try:
                response = auth_client.post(
                    f"/accounts/{spare_id}/archive",
                    follow_redirects=True,
                )
            finally:
                event.remove(Account, "before_update", make_stale)

            assert response.status_code == 200
            assert b"changed by another action" in response.data.lower()

            db.session.expire_all()
            persisted = db.session.get(Account, spare_id)
            assert persisted.is_active is True, (
                "StaleDataError on archive must NOT flip is_active."
            )

    def test_hard_delete_account_stale_data_redirects_with_warning(
        self, app, db, auth_client, seed_user,
    ):
        """A StaleDataError during hard-delete leaves the row intact.

        Unlike a normal delete, the row does NOT get removed when the
        version race goes against this request.  The user receives a
        warning flash and the row remains for the winner of the race.
        """
        from sqlalchemy import event  # pylint: disable=import-outside-toplevel

        with app.app_context():
            checking_type = (
                db.session.query(AccountType).filter_by(name="Checking").one()
            )
            spare = Account(
                user_id=seed_user["user"].id,
                account_type_id=checking_type.id,
                name="Delete Target",
                current_anchor_balance=Decimal("0.00"),
            )
            db.session.add(spare)
            db.session.commit()
            spare_id = spare.id

            fired = {"flag": False}

            def make_stale(mapper, connection, target):
                if fired["flag"] or target.id != spare_id:
                    return
                fired["flag"] = True
                _bump_account_version_outside_session(spare_id)

            event.listen(Account, "before_delete", make_stale)
            try:
                response = auth_client.post(
                    f"/accounts/{spare_id}/hard-delete",
                    follow_redirects=True,
                )
            finally:
                event.remove(Account, "before_delete", make_stale)

            assert response.status_code == 200
            assert b"changed by another action" in response.data.lower()

            db.session.expire_all()
            persisted = db.session.get(Account, spare_id)
            assert persisted is not None, (
                "StaleDataError on hard-delete must leave the row in "
                "place for the winner of the race to handle."
            )


class TestAnchorTemplatesEmitVersionPin:
    """Templates that render anchor edit forms must include a hidden ``version_id`` input."""

    def test_grid_anchor_form_includes_version_pin(
        self, app, auth_client, seed_user,
    ):
        """GET /accounts/<id>/anchor-form returns a form with the version_id pin."""
        with app.app_context():
            acct_id = seed_user["account"].id
            current_version = db.session.get(Account, acct_id).version_id

            response = auth_client.get(f"/accounts/{acct_id}/anchor-form")

            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' in body, (
                "grid anchor form must ship version_id as a hidden "
                "input for the optimistic-lock contract."
            )
            assert f'value="{current_version}"' in body, (
                "version_id hidden input must carry the current row's "
                "version, not a placeholder."
            )

    def test_inline_anchor_form_includes_version_pin(
        self, app, auth_client, seed_user,
    ):
        """GET /accounts/<id>/inline-anchor-form ships ``version_id`` to the client."""
        with app.app_context():
            acct_id = seed_user["account"].id
            current_version = db.session.get(Account, acct_id).version_id

            response = auth_client.get(
                f"/accounts/{acct_id}/inline-anchor-form"
            )

            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' in body
            assert f'value="{current_version}"' in body

    def test_account_edit_form_includes_version_pin(
        self, app, auth_client, seed_user,
    ):
        """GET /accounts/<id>/edit ships ``version_id`` so the POST round-trips."""
        with app.app_context():
            acct_id = seed_user["account"].id
            current_version = db.session.get(Account, acct_id).version_id

            response = auth_client.get(f"/accounts/{acct_id}/edit")

            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' in body
            assert f'value="{current_version}"' in body

    def test_account_create_form_omits_version_pin(
        self, app, auth_client, seed_user,
    ):
        """The create form has no ``version_id`` -- there is no row to pin yet.

        Catching the regression of a copy-paste that puts an
        ``account.version_id`` reference into the create form would
        produce a Jinja UndefinedError because ``account`` is None
        on that path.
        """
        with app.app_context():
            response = auth_client.get("/accounts/new")
            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' not in body
