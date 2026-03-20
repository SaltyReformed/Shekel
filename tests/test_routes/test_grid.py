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

    def test_grid_shows_dynamic_account_name(self, app, auth_client, seed_user, seed_periods):
        """GET / shows the resolved account name in the header."""
        with app.app_context():
            response = auth_client.get("/")
            assert response.status_code == 200
            assert b"Checking Balance" in response.data

    def test_grid_period_controls(self, app, auth_client, seed_user, seed_periods):
        """Grid respects the periods query parameter."""
        with app.app_context():
            response = auth_client.get("/?periods=3")
            assert response.status_code == 200
            assert b"01/02" in response.data
            assert b"Projected End Balance" in response.data


class TestBalanceRow:
    """Tests for GET /grid/balance-row HTMX partial."""

    def test_balance_row_returns_partial(self, app, auth_client, seed_user, seed_periods):
        """GET /grid/balance-row returns recalculated balance HTML partial."""
        with app.app_context():
            resp = auth_client.get("/grid/balance-row?periods=6&offset=0")
            assert resp.status_code == 200
            assert b"Projected End Balance" in resp.data
            assert b"Total Income" in resp.data

    def test_balance_row_no_current_period(self, app, auth_client, seed_user):
        """GET /grid/balance-row with no periods returns 204 empty."""
        with app.app_context():
            # No periods generated — get_current_period returns None.
            resp = auth_client.get("/grid/balance-row")
            assert resp.status_code == 204
            assert resp.data == b""

    def test_balance_row_custom_offset(self, app, auth_client, seed_user, seed_periods):
        """GET /grid/balance-row with offset shifts the visible window."""
        with app.app_context():
            resp = auth_client.get("/grid/balance-row?periods=3&offset=2")
            assert resp.status_code == 200
            assert b"Projected End Balance" in resp.data
            assert b"Total Expenses" in resp.data

    def test_grid_periods_large_value(self, app, auth_client, seed_user, seed_periods):
        """GET / with periods larger than available still renders."""
        with app.app_context():
            # Request 100 periods when only 10 exist — should render what's available.
            resp = auth_client.get("/?periods=100")
            assert resp.status_code == 200
            assert b"Projected End Balance" in resp.data
            assert b"01/02" in resp.data


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

            # Verify the transaction was persisted correctly.
            txn = db.session.query(Transaction).filter_by(
                name="New Expense",
                scenario_id=seed_user["scenario"].id,
            ).one()
            assert txn.estimated_amount == Decimal("99.99")
            assert txn.pay_period_id == seed_periods[0].id
            assert txn.category_id == seed_user["categories"]["Groceries"].id
            assert txn.status.name == "projected"

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

    def test_hard_delete_adhoc_transaction(self, app, auth_client, seed_user, seed_periods):
        """DELETE /transactions/<id> hard-deletes ad-hoc (no template) items."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods)
            txn_id = txn.id

            response = auth_client.delete(f"/transactions/{txn_id}")
            assert response.status_code == 200

            # Ad-hoc transaction should be fully deleted.
            assert db.session.get(Transaction, txn_id) is None

    def test_mark_done_without_actual_amount(self, app, auth_client, seed_user, seed_periods):
        """POST /transactions/<id>/mark-done without actual_amount sets status only."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods)

            response = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "done"
            assert txn.actual_amount is None

    def test_cancel_transaction(self, app, auth_client, seed_user, seed_periods):
        """POST /transactions/<id>/cancel sets status to cancelled."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods)

            response = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "cancelled"
            assert txn.effective_amount == Decimal("0")

    def test_mark_credit_creates_payback(self, app, auth_client, seed_user, seed_periods):
        """POST /transactions/<id>/mark-credit creates payback in next period."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods)

            response = auth_client.post(f"/transactions/{txn.id}/mark-credit")
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "credit"

            # A payback transaction should exist in the next period.
            payback = db.session.query(Transaction).filter(
                Transaction.name.like("%Payback%"),
                Transaction.pay_period_id == seed_periods[1].id,
            ).first()
            assert payback is not None, "Payback transaction was not created"
            assert payback.name == "CC Payback: Test Expense"
            assert payback.estimated_amount == Decimal("123.45")
            assert payback.status.name == "projected"
            assert payback.pay_period_id == seed_periods[1].id
            assert payback.credit_payback_for_id == txn.id

    def test_unmark_credit_reverts_and_deletes_payback(self, app, auth_client, seed_user, seed_periods):
        """DELETE /transactions/<id>/unmark-credit reverts to projected and deletes payback."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods)

            # First mark as credit.
            auth_client.post(f"/transactions/{txn.id}/mark-credit")
            db.session.refresh(txn)
            assert txn.status.name == "credit"

            # Now unmark.
            response = auth_client.delete(f"/transactions/{txn.id}/unmark-credit")
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "projected"

            # Payback should be deleted.
            payback = db.session.query(Transaction).filter(
                Transaction.name.like("%Payback%"),
                Transaction.pay_period_id == seed_periods[1].id,
            ).first()
            assert payback is None

    def test_create_transaction_full_form(self, app, auth_client, seed_user, seed_periods):
        """POST /transactions with all fields creates a complete transaction."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()
            projected = db.session.query(Status).filter_by(name="projected").one()

            response = auth_client.post("/transactions", data={
                "name": "Full Form Expense",
                "estimated_amount": "250.00",
                "pay_period_id": seed_periods[2].id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Car Payment"].id,
                "transaction_type_id": expense_type.id,
                "status_id": projected.id,
            })
            assert response.status_code == 201

            txn = db.session.query(Transaction).filter_by(
                name="Full Form Expense"
            ).one()
            assert txn.estimated_amount == Decimal("250.00")
            assert txn.pay_period_id == seed_periods[2].id
            assert txn.category_id == seed_user["categories"]["Car Payment"].id

    def test_create_inline_no_scenario(self, app, auth_client, seed_user, seed_periods):
        """GET /transactions/new/quick with no baseline scenario returns 400.

        The route returns the plain text error 'No baseline scenario' when
        no baseline scenario exists for the user.
        """
        with app.app_context():
            from app.models.scenario import Scenario

            # Delete the baseline scenario.
            db.session.query(Scenario).filter_by(
                user_id=seed_user["user"].id,
            ).delete()
            db.session.commit()

            response = auth_client.get(
                f"/transactions/new/quick"
                f"?category_id={seed_user['categories']['Rent'].id}"
                f"&period_id={seed_periods[0].id}"
                f"&txn_type_name=expense"
            )
            assert response.status_code == 400
            assert b"No baseline scenario" in response.data
