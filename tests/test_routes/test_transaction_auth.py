"""
Shekel Budget App — Transaction Ownership Authorization Tests

Verifies that transaction routes enforce user ownership:
an authenticated user cannot read, modify, or delete another
user's transactions.
"""

from datetime import date
from decimal import Decimal

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

    checking_type = db.session.query(AccountType).filter_by(name="checking").one()
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

    projected = db.session.query(Status).filter_by(name="projected").one()
    expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

    txn = Transaction(
        pay_period_id=other_periods[0].id,
        scenario_id=scenario.id,
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
        """GET /transactions/<id>/cell returns 404 for another user's txn."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods)
            resp = auth_client.get(f"/transactions/{other['transaction'].id}/cell")
            assert resp.status_code == 404

    def test_quick_edit_blocked(self, app, auth_client, seed_user, seed_periods):
        """GET /transactions/<id>/quick-edit returns 404 for another user's txn."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods)
            resp = auth_client.get(f"/transactions/{other['transaction'].id}/quick-edit")
            assert resp.status_code == 404

    def test_full_edit_blocked(self, app, auth_client, seed_user, seed_periods):
        """GET /transactions/<id>/full-edit returns 404 for another user's txn."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods)
            resp = auth_client.get(f"/transactions/{other['transaction'].id}/full-edit")
            assert resp.status_code == 404

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
            assert other["transaction"].status.name == "projected"

    def test_mark_credit_blocked(self, app, auth_client, seed_user, seed_periods):
        """POST /transactions/<id>/mark-credit returns 404 for another user's txn."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods)
            resp = auth_client.post(f"/transactions/{other['transaction'].id}/mark-credit")
            assert resp.status_code == 404

    def test_cancel_blocked(self, app, auth_client, seed_user, seed_periods):
        """POST /transactions/<id>/cancel returns 404 for another user's txn."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods)
            resp = auth_client.post(f"/transactions/{other['transaction'].id}/cancel")
            assert resp.status_code == 404

            db.session.refresh(other["transaction"])
            assert other["transaction"].status.name == "projected"

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
        """DELETE /transactions/<id>/unmark-credit returns 404 for another user's txn."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods)
            resp = auth_client.delete(
                f"/transactions/{other['transaction'].id}/unmark-credit"
            )
            assert resp.status_code == 404


class TestCreateOwnership:
    """Verify that transaction creation rejects foreign pay_period_id / category_id."""

    def test_inline_create_with_other_users_period(self, app, auth_client, seed_user, seed_periods):
        """POST /transactions/inline rejects another user's pay_period_id."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods)
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

            resp = auth_client.post("/transactions/inline", data={
                "estimated_amount": "50.00",
                "category_id": seed_user["categories"]["Groceries"].id,
                "pay_period_id": other["period"].id,  # Other user's period
                "transaction_type_id": expense_type.id,
                "scenario_id": seed_user["scenario"].id,
            })
            assert resp.status_code == 404

    def test_inline_create_with_other_users_category(self, app, auth_client, seed_user, seed_periods):
        """POST /transactions/inline rejects another user's category_id."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods)
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

            resp = auth_client.post("/transactions/inline", data={
                "estimated_amount": "50.00",
                "category_id": other["category"].id,  # Other user's category
                "pay_period_id": seed_periods[0].id,
                "transaction_type_id": expense_type.id,
                "scenario_id": seed_user["scenario"].id,
            })
            assert resp.status_code == 404

    def test_create_with_other_users_period(self, app, auth_client, seed_user, seed_periods):
        """POST /transactions rejects another user's pay_period_id."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods)
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

            resp = auth_client.post("/transactions", data={
                "name": "Sneaky Expense",
                "estimated_amount": "50.00",
                "category_id": seed_user["categories"]["Groceries"].id,
                "pay_period_id": other["period"].id,  # Other user's period
                "transaction_type_id": expense_type.id,
                "scenario_id": seed_user["scenario"].id,
            })
            assert resp.status_code == 404


class TestCarryForwardOwnership:
    """Verify carry-forward rejects another user's period."""

    def test_carry_forward_other_users_period(self, app, auth_client, seed_user, seed_periods):
        """POST /pay-periods/<id>/carry-forward returns 404 for another user's period."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods)
            resp = auth_client.post(f"/pay-periods/{other['period'].id}/carry-forward")
            assert resp.status_code == 404
