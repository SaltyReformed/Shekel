"""
Shekel Budget App — Grid & Transaction Route Tests

Tests the main budget grid view and transaction CRUD endpoints.
"""

from decimal import Decimal

from app.extensions import db
from app.models.transaction import Transaction
from app.models.ref import Status, TransactionType


class TestGridView:
    """Tests for the main grid page at /."""

    def test_grid_loads_with_periods(self, app, auth_client, seed_user, seed_periods):
        """GET / renders the budget grid with pay period columns."""
        with app.app_context():
            response = auth_client.get("/")
            assert response.status_code == 200
            # Check for key grid elements.
            assert b"Checking Balance" in response.data
            assert b"Projected End Balance" in response.data

    def test_grid_shows_no_periods_page(self, app, auth_client, seed_user):
        """GET / shows the no-periods prompt when none exist."""
        with app.app_context():
            response = auth_client.get("/")
            assert response.status_code == 200
            assert b"No Pay Periods" in response.data

    def test_grid_period_controls(self, app, auth_client, seed_user, seed_periods):
        """Grid respects the periods query parameter."""
        with app.app_context():
            response = auth_client.get("/?periods=3")
            assert response.status_code == 200


class TestTransactionCRUD:
    """Tests for transaction create, update, delete, and status changes."""

    def _create_test_txn(self, seed_user, seed_periods):
        """Helper: create and return a projected expense."""
        projected = db.session.query(Status).filter_by(name="projected").one()
        expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

        txn = Transaction(
            pay_period_id=seed_periods[0].id,
            scenario_id=seed_user["scenario"].id,
            status_id=projected.id,
            name="Test Expense",
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=expense_type.id,
            estimated_amount=Decimal("123.45"),
        )
        db.session.add(txn)
        db.session.commit()
        return txn

    def test_create_transaction(self, app, auth_client, seed_user, seed_periods):
        """POST /transactions creates a new ad-hoc transaction."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

            response = auth_client.post("/transactions", data={
                "name": "New Expense",
                "estimated_amount": "99.99",
                "pay_period_id": seed_periods[0].id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
            })
            assert response.status_code == 201

    def test_update_transaction(self, app, auth_client, seed_user, seed_periods):
        """PATCH /transactions/<id> updates fields."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods)

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"estimated_amount": "200.00"},
            )
            assert response.status_code == 200
            assert b"200" in response.data

    def test_mark_expense_done(self, app, auth_client, seed_user, seed_periods):
        """POST /transactions/<id>/mark-done sets status to done for expenses."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods)

            response = auth_client.post(
                f"/transactions/{txn.id}/mark-done",
                data={"actual_amount": "120.00"},
            )
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "done"
            assert txn.actual_amount == Decimal("120.00")

    def test_mark_income_received(self, app, auth_client, seed_user, seed_periods):
        """POST /transactions/<id>/mark-done sets status to received for income."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="projected").one()
            income_type = db.session.query(TransactionType).filter_by(name="income").one()

            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=projected.id,
                name="Paycheck",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=Decimal("2000.00"),
            )
            db.session.add(txn)
            db.session.commit()

            response = auth_client.post(
                f"/transactions/{txn.id}/mark-done",
                data={"actual_amount": "2050.00"},
            )
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "received"

    def test_soft_delete_template_transaction(self, app, auth_client, seed_user, seed_periods):
        """DELETE /transactions/<id> soft-deletes template-linked items."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods)
            # Simulate template linkage.
            from app.models.transaction_template import TransactionTemplate
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                name="Template",
                default_amount=Decimal("100.00"),
            )
            db.session.add(template)
            db.session.flush()
            txn.template_id = template.id
            db.session.commit()

            response = auth_client.delete(f"/transactions/{txn.id}")
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.is_deleted is True
