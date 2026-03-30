"""
Shekel Budget App -- Transaction Ownership Authorization Tests

Verifies that transaction routes enforce user ownership:
an authenticated user cannot read, modify, or delete another
user's transactions.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError as SAIntegrityError

from app.extensions import db
from app.models.user import User, UserSettings
from app.models.account import Account
from app.models.scenario import Scenario
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.ref import AccountType, Status, TransactionType
from app.services.auth_service import hash_password
from app.services import pay_period_service


def _create_other_user_with_txn(seed_user, seed_periods):
    """Create a second user with their own period and transaction.

    Returns:
        dict with keys: user, period, transaction.
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

    scenario = Scenario(
        user_id=other_user.id,
        name="Baseline",
        is_baseline=True,
    )
    db.session.add(scenario)
    db.session.flush()

    category = Category(
        user_id=other_user.id,
        group_name="Home",
        item_name="Rent",
    )
    db.session.add(category)
    db.session.flush()

    # Create a pay period for the other user.
    other_periods = pay_period_service.generate_pay_periods(
        user_id=other_user.id,
        start_date=date(2026, 1, 2),
        num_periods=3,
        cadence_days=14,
    )
    db.session.flush()

    projected = db.session.query(Status).filter_by(name="Projected").one()
    expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

    txn = Transaction(
        pay_period_id=other_periods[0].id,
        scenario_id=scenario.id,
        account_id=account.id,
        status_id=projected.id,
        name="Other User Rent",
        category_id=category.id,
        transaction_type_id=expense_type.id,
        estimated_amount=Decimal("1500.00"),
    )
    db.session.add(txn)
    db.session.commit()

    return {
        "user": other_user,
        "period": other_periods[0],
        "transaction": txn,
        "scenario": scenario,
        "category": category,
    }


class TestTransactionOwnership:
    """Verify that all transaction routes reject access to other users' data."""

    def test_get_cell_blocked(self, app, auth_client, seed_user, seed_periods):
        """GET /transactions/<id>/cell returns 404 for another user's txn
        and does not leak the victim's transaction data."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods)
            resp = auth_client.get(f"/transactions/{other['transaction'].id}/cell")
            assert resp.status_code == 404
            assert b"Other User Rent" not in resp.data
            assert b"1500.00" not in resp.data

    def test_quick_edit_blocked(self, app, auth_client, seed_user, seed_periods):
        """GET /transactions/<id>/quick-edit returns 404 for another user's txn
        and does not leak the victim's transaction data."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods)
            resp = auth_client.get(f"/transactions/{other['transaction'].id}/quick-edit")
            assert resp.status_code == 404
            assert b"Other User Rent" not in resp.data
            assert b"1500.00" not in resp.data

    def test_full_edit_blocked(self, app, auth_client, seed_user, seed_periods):
        """GET /transactions/<id>/full-edit returns 404 for another user's txn
        and does not leak the victim's transaction data."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods)
            resp = auth_client.get(f"/transactions/{other['transaction'].id}/full-edit")
            assert resp.status_code == 404
            assert b"Other User Rent" not in resp.data
            assert b"1500.00" not in resp.data

    def test_update_blocked(self, app, auth_client, seed_user, seed_periods):
        """PATCH /transactions/<id> returns 404 for another user's txn."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods)
            resp = auth_client.patch(
                f"/transactions/{other['transaction'].id}",
                data={"estimated_amount": "0.01"},
            )
            assert resp.status_code == 404

            # Verify the transaction was NOT modified.
            db.session.refresh(other["transaction"])
            assert other["transaction"].estimated_amount == Decimal("1500.00")

    def test_mark_done_blocked(self, app, auth_client, seed_user, seed_periods):
        """POST /transactions/<id>/mark-done returns 404 for another user's txn."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods)
            resp = auth_client.post(f"/transactions/{other['transaction'].id}/mark-done")
            assert resp.status_code == 404

            # Verify status unchanged.
            db.session.refresh(other["transaction"])
            assert other["transaction"].status.name == "Projected"

    def test_mark_credit_blocked(self, app, auth_client, seed_user, seed_periods):
        """POST /transactions/<id>/mark-credit returns 404 for another
        user's txn and leaves the transaction status unchanged."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods)
            txn_id = other["transaction"].id
            resp = auth_client.post(f"/transactions/{txn_id}/mark-credit")
            assert resp.status_code == 404

            # Verify status unchanged and no payback transaction created.
            db.session.expire_all()
            txn_after = db.session.get(Transaction, txn_id)
            assert txn_after.status.name == "Projected", (
                "IDOR attack changed transaction status!"
            )
            # Credit workflow creates a payback txn; verify none exist.
            payback = (
                db.session.query(Transaction)
                .filter_by(
                    pay_period_id=txn_after.pay_period_id,
                    name="Other User Rent (payback)",
                )
                .first()
            )
            assert payback is None, (
                "IDOR attack created a payback transaction!"
            )

    def test_cancel_blocked(self, app, auth_client, seed_user, seed_periods):
        """POST /transactions/<id>/cancel returns 404 for another user's txn."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods)
            resp = auth_client.post(f"/transactions/{other['transaction'].id}/cancel")
            assert resp.status_code == 404

            db.session.refresh(other["transaction"])
            assert other["transaction"].status.name == "Projected"

    def test_delete_blocked(self, app, auth_client, seed_user, seed_periods):
        """DELETE /transactions/<id> returns 404 for another user's txn."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods)
            txn_id = other["transaction"].id
            resp = auth_client.delete(f"/transactions/{txn_id}")
            assert resp.status_code == 404

            # Verify the transaction still exists and is not deleted.
            txn = db.session.get(Transaction, txn_id)
            assert txn is not None
            assert txn.is_deleted is False

    def test_unmark_credit_blocked(self, app, auth_client, seed_user, seed_periods):
        """DELETE /transactions/<id>/unmark-credit returns 404 for another
        user's txn and leaves the transaction status unchanged."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods)
            txn_id = other["transaction"].id
            orig_status = other["transaction"].status.name

            resp = auth_client.delete(
                f"/transactions/{txn_id}/unmark-credit"
            )
            assert resp.status_code == 404

            # Verify status unchanged.
            db.session.expire_all()
            txn_after = db.session.get(Transaction, txn_id)
            assert txn_after.status.name == orig_status, (
                "IDOR attack changed transaction status!"
            )


class TestCreateOwnership:
    """Verify that transaction creation rejects foreign pay_period_id / category_id."""

    def test_inline_create_with_other_users_period(self, app, auth_client, seed_user, seed_periods):
        """POST /transactions/inline rejects another user's pay_period_id."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods)
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            resp = auth_client.post("/transactions/inline", data={
                "estimated_amount": "50.00",
                "category_id": seed_user["categories"]["Groceries"].id,
                "pay_period_id": other["period"].id,  # Other user's period
                "transaction_type_id": expense_type.id,
                "scenario_id": seed_user["scenario"].id,
                "account_id": str(seed_user["account"].id),
            })
            assert resp.status_code == 404

    def test_inline_create_with_other_users_category(self, app, auth_client, seed_user, seed_periods):
        """POST /transactions/inline rejects another user's category_id."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods)
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            resp = auth_client.post("/transactions/inline", data={
                "estimated_amount": "50.00",
                "category_id": other["category"].id,  # Other user's category
                "pay_period_id": seed_periods[0].id,
                "transaction_type_id": expense_type.id,
                "scenario_id": seed_user["scenario"].id,
                "account_id": str(seed_user["account"].id),
            })
            assert resp.status_code == 404

    def test_create_with_other_users_period(self, app, auth_client, seed_user, seed_periods):
        """POST /transactions rejects another user's pay_period_id."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods)
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            resp = auth_client.post("/transactions", data={
                "name": "Sneaky Expense",
                "estimated_amount": "50.00",
                "category_id": seed_user["categories"]["Groceries"].id,
                "pay_period_id": other["period"].id,  # Other user's period
                "transaction_type_id": expense_type.id,
                "scenario_id": seed_user["scenario"].id,
                "account_id": str(seed_user["account"].id),
            })
            assert resp.status_code == 404

    def test_create_with_other_users_scenario_id(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST /transactions with another user's scenario_id returns 404."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods)
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions", data={
                "name": "Sneaky Scenario",
                "estimated_amount": "100.00",
                "pay_period_id": seed_periods[0].id,
                "scenario_id": other["scenario"].id,  # Other user's scenario
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
                "account_id": str(seed_user["account"].id),
            })
            assert resp.status_code == 404

            # No transaction should have been created.
            txn = db.session.query(Transaction).filter_by(
                name="Sneaky Scenario"
            ).first()
            assert txn is None

    def test_inline_create_with_other_users_scenario_id(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST /transactions/inline with another user's scenario_id returns 404."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods)
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions/inline", data={
                "estimated_amount": "75.00",
                "category_id": seed_user["categories"]["Groceries"].id,
                "pay_period_id": seed_periods[0].id,
                "transaction_type_id": expense_type.id,
                "scenario_id": other["scenario"].id,  # Other user's scenario
                "account_id": str(seed_user["account"].id),
            })
            assert resp.status_code == 404

            # No transaction should have been created under the other
            # user's scenario.
            txn = db.session.query(Transaction).filter_by(
                scenario_id=other["scenario"].id,
                estimated_amount=Decimal("75.00"),
            ).first()
            assert txn is None

    def test_create_with_nonexistent_scenario_id(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST /transactions with nonexistent scenario_id returns 404."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions", data={
                "name": "Ghost Scenario",
                "estimated_amount": "50.00",
                "pay_period_id": seed_periods[0].id,
                "scenario_id": 999999,  # Nonexistent
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
                "account_id": str(seed_user["account"].id),
            })
            assert resp.status_code == 404

    def test_create_with_nonexistent_pay_period_id(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST /transactions with nonexistent pay_period_id returns 404."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions", data={
                "name": "Ghost Period",
                "estimated_amount": "100.00",
                "pay_period_id": 999999,
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
                "account_id": str(seed_user["account"].id),
            })
            assert resp.status_code == 404
            assert b"Pay period not found" in resp.data

            # Verify no transaction was created.
            count = db.session.query(Transaction).filter_by(
                name="Ghost Period"
            ).count()
            assert count == 0

    def test_create_with_nonexistent_category_id(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST /transactions with nonexistent category_id returns 400."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions", data={
                "name": "Ghost Category",
                "estimated_amount": "100.00",
                "pay_period_id": seed_periods[0].id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": 999999,
                "transaction_type_id": expense_type.id,
                "account_id": str(seed_user["account"].id),
            })
            assert resp.status_code == 400

            count = db.session.query(Transaction).filter_by(
                name="Ghost Category"
            ).count()
            assert count == 0

    def test_inline_create_with_nonexistent_period(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST /transactions/inline with nonexistent pay_period_id returns 404."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions/inline", data={
                "estimated_amount": "50.00",
                "category_id": seed_user["categories"]["Groceries"].id,
                "pay_period_id": 999999,
                "transaction_type_id": expense_type.id,
                "scenario_id": seed_user["scenario"].id,
                "account_id": str(seed_user["account"].id),
            })
            # Inline route checks category first (passes), then period (404).
            assert resp.status_code == 404

            count = db.session.query(Transaction).filter_by(
                pay_period_id=999999
            ).count()
            assert count == 0


class TestFormRenderingOwnership:
    """Verify form-rendering GET endpoints reject other users' resources.

    These endpoints load Category and PayPeriod by ID from query params.
    Without ownership checks, an attacker could enumerate IDs to discover
    another user's category names and pay period dates.
    """

    def test_quick_create_rejects_other_users_category(
        self, app, auth_client, seed_user, seed_periods
    ):
        """GET /transactions/new/quick returns 404 for another user's category."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods)
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            resp = auth_client.get("/transactions/new/quick", query_string={
                "category_id": other["category"].id,
                "period_id": seed_periods[0].id,
                "transaction_type_id": expense_type.id,
                "account_id": seed_user["account"].id,
            })
            assert resp.status_code == 404

    def test_quick_create_rejects_other_users_period(
        self, app, auth_client, seed_user, seed_periods
    ):
        """GET /transactions/new/quick returns 404 for another user's period."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods)
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            resp = auth_client.get("/transactions/new/quick", query_string={
                "category_id": seed_user["categories"]["Groceries"].id,
                "period_id": other["period"].id,
                "transaction_type_id": expense_type.id,
                "account_id": seed_user["account"].id,
            })
            assert resp.status_code == 404

    def test_quick_create_rejects_mixed_ownership(
        self, app, auth_client, seed_user, seed_periods
    ):
        """GET /transactions/new/quick returns 404 when both resources are foreign."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods)
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            resp = auth_client.get("/transactions/new/quick", query_string={
                "category_id": other["category"].id,
                "period_id": other["period"].id,
                "transaction_type_id": expense_type.id,
                "account_id": seed_user["account"].id,
            })
            assert resp.status_code == 404

    def test_full_create_rejects_other_users_category(
        self, app, auth_client, seed_user, seed_periods
    ):
        """GET /transactions/new/full returns 404 for another user's category."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods)
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            resp = auth_client.get("/transactions/new/full", query_string={
                "category_id": other["category"].id,
                "period_id": seed_periods[0].id,
                "transaction_type_id": expense_type.id,
                "account_id": seed_user["account"].id,
            })
            assert resp.status_code == 404

    def test_full_create_rejects_other_users_period(
        self, app, auth_client, seed_user, seed_periods
    ):
        """GET /transactions/new/full returns 404 for another user's period."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods)
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            resp = auth_client.get("/transactions/new/full", query_string={
                "category_id": seed_user["categories"]["Groceries"].id,
                "period_id": other["period"].id,
                "transaction_type_id": expense_type.id,
                "account_id": seed_user["account"].id,
            })
            assert resp.status_code == 404

    def test_empty_cell_rejects_other_users_category(
        self, app, auth_client, seed_user, seed_periods
    ):
        """GET /transactions/empty-cell returns 404 for another user's category."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods)
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            resp = auth_client.get("/transactions/empty-cell", query_string={
                "category_id": other["category"].id,
                "period_id": seed_periods[0].id,
                "transaction_type_id": expense_type.id,
                "account_id": seed_user["account"].id,
            })
            assert resp.status_code == 404

    def test_empty_cell_rejects_other_users_period(
        self, app, auth_client, seed_user, seed_periods
    ):
        """GET /transactions/empty-cell returns 404 for another user's period."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods)
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            resp = auth_client.get("/transactions/empty-cell", query_string={
                "category_id": seed_user["categories"]["Groceries"].id,
                "period_id": other["period"].id,
                "transaction_type_id": expense_type.id,
                "account_id": seed_user["account"].id,
            })
            assert resp.status_code == 404

    def test_quick_create_allows_own_resources(
        self, app, auth_client, seed_user, seed_periods
    ):
        """GET /transactions/new/quick returns 200 for the user's own resources."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            resp = auth_client.get("/transactions/new/quick", query_string={
                "category_id": seed_user["categories"]["Groceries"].id,
                "period_id": seed_periods[0].id,
                "transaction_type_id": expense_type.id,
                "account_id": seed_user["account"].id,
            })
            assert resp.status_code == 200

    def test_full_create_allows_own_resources(
        self, app, auth_client, seed_user, seed_periods
    ):
        """GET /transactions/new/full returns 200 for the user's own resources."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            resp = auth_client.get("/transactions/new/full", query_string={
                "category_id": seed_user["categories"]["Groceries"].id,
                "period_id": seed_periods[0].id,
                "transaction_type_id": expense_type.id,
                "account_id": seed_user["account"].id,
            })
            assert resp.status_code == 200

    def test_empty_cell_allows_own_resources(
        self, app, auth_client, seed_user, seed_periods
    ):
        """GET /transactions/empty-cell returns 200 for the user's own resources."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            resp = auth_client.get("/transactions/empty-cell", query_string={
                "category_id": seed_user["categories"]["Groceries"].id,
                "period_id": seed_periods[0].id,
                "transaction_type_id": expense_type.id,
                "account_id": seed_user["account"].id,
            })
            assert resp.status_code == 200


class TestCarryForwardOwnership:
    """Verify carry-forward rejects another user's period."""

    def test_carry_forward_other_users_period(self, app, auth_client, seed_user, seed_periods):
        """POST /pay-periods/<id>/carry-forward returns 404 for another user's period."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods)
            resp = auth_client.post(f"/pay-periods/{other['period'].id}/carry-forward")
            assert resp.status_code == 404
