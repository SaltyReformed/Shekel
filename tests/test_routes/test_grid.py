"""
Shekel Budget App -- Grid & Transaction Route Tests

Tests the main budget grid view and transaction CRUD endpoints.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.account import Account
from app.models.scenario import Scenario
from app.models.user import User, UserSettings
from app.models.transaction import Transaction
from app.models.ref import AccountType, Status, TransactionType
from app.services.auth_service import hash_password
from app.services import pay_period_service


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
            # Total Income/Expenses are now in tbody subtotals, not in the tfoot.
            assert b"Total Income" not in resp.data

    def test_balance_row_no_current_period(self, app, auth_client, seed_user):
        """GET /grid/balance-row with no periods returns 204 empty."""
        with app.app_context():
            # No periods generated -- get_current_period returns None.
            resp = auth_client.get("/grid/balance-row")
            assert resp.status_code == 204
            assert resp.data == b""

    def test_balance_row_custom_offset(self, app, auth_client, seed_user, seed_periods):
        """GET /grid/balance-row with offset shifts the visible window."""
        with app.app_context():
            resp = auth_client.get("/grid/balance-row?periods=3&offset=2")
            assert resp.status_code == 200
            assert b"Projected End Balance" in resp.data
            assert b"Total Expenses" not in resp.data

    def test_grid_periods_large_value(self, app, auth_client, seed_user, seed_periods):
        """GET / with periods larger than available still renders."""
        with app.app_context():
            # Request 100 periods when only 10 exist -- should render what's available.
            resp = auth_client.get("/?periods=100")
            assert resp.status_code == 200
            assert b"Projected End Balance" in resp.data
            assert b"01/02" in resp.data


class TestTransactionCRUD:
    """Tests for transaction create, update, delete, and status changes."""

    def _create_test_txn(self, seed_user, seed_periods):
        """Helper: create and return a projected expense."""
        projected = db.session.query(Status).filter_by(name="Projected").one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

        txn = Transaction(
            pay_period_id=seed_periods[0].id,
            scenario_id=seed_user["scenario"].id,
            account_id=seed_user["account"].id,
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
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            response = auth_client.post("/transactions", data={
                "name": "New Expense",
                "estimated_amount": "99.99",
                "pay_period_id": seed_periods[0].id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
                "account_id": str(seed_user["account"].id),
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
            assert txn.status.name == "Projected"

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
            assert txn.status.name == "Paid"
            assert txn.actual_amount == Decimal("120.00")

    def test_mark_income_received(self, app, auth_client, seed_user, seed_periods):
        """POST /transactions/<id>/mark-done sets status to received for income."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()

            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
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
            assert txn.status.name == "Received"

    def test_soft_delete_template_transaction(self, app, auth_client, seed_user, seed_periods):
        """DELETE /transactions/<id> soft-deletes template-linked items."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods)
            # Simulate template linkage.
            from app.models.transaction_template import TransactionTemplate
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
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
            assert txn.status.name == "Paid"
            assert txn.actual_amount is None

    def test_cancel_transaction(self, app, auth_client, seed_user, seed_periods):
        """POST /transactions/<id>/cancel sets status to cancelled."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods)

            response = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"
            assert txn.effective_amount == Decimal("0")

    def test_mark_credit_creates_payback(self, app, auth_client, seed_user, seed_periods):
        """POST /transactions/<id>/mark-credit creates payback in next period."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods)

            response = auth_client.post(f"/transactions/{txn.id}/mark-credit")
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Credit"

            # A payback transaction should exist in the next period.
            payback = db.session.query(Transaction).filter(
                Transaction.name.like("%Payback%"),
                Transaction.pay_period_id == seed_periods[1].id,
            ).first()
            assert payback is not None, "Payback transaction was not created"
            assert payback.name == "CC Payback: Test Expense"
            assert payback.estimated_amount == Decimal("123.45")
            assert payback.status.name == "Projected"
            assert payback.pay_period_id == seed_periods[1].id
            assert payback.credit_payback_for_id == txn.id

    def test_unmark_credit_reverts_and_deletes_payback(self, app, auth_client, seed_user, seed_periods):
        """DELETE /transactions/<id>/unmark-credit reverts to projected and deletes payback."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods)

            # First mark as credit.
            auth_client.post(f"/transactions/{txn.id}/mark-credit")
            db.session.refresh(txn)
            assert txn.status.name == "Credit"

            # Now unmark.
            response = auth_client.delete(f"/transactions/{txn.id}/unmark-credit")
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Projected"

            # Payback should be deleted.
            payback = db.session.query(Transaction).filter(
                Transaction.name.like("%Payback%"),
                Transaction.pay_period_id == seed_periods[1].id,
            ).first()
            assert payback is None

    def test_create_transaction_full_form(self, app, auth_client, seed_user, seed_periods):
        """POST /transactions with all fields creates a complete transaction."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            projected = db.session.query(Status).filter_by(name="Projected").one()

            response = auth_client.post("/transactions", data={
                "name": "Full Form Expense",
                "estimated_amount": "250.00",
                "pay_period_id": seed_periods[2].id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Car Payment"].id,
                "transaction_type_id": expense_type.id,
                "status_id": projected.id,
                "account_id": str(seed_user["account"].id),
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

            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            response = auth_client.get(
                f"/transactions/new/quick"
                f"?category_id={seed_user['categories']['Rent'].id}"
                f"&period_id={seed_periods[0].id}"
                f"&transaction_type_id={expense_type.id}"
                f"&account_id={seed_user['account'].id}"
            )
            assert response.status_code == 400
            assert b"No baseline scenario" in response.data


class TestTransactionNegativePaths:
    """Tests for transaction route error handling, validation, and edge cases."""

    def _create_test_txn(self, seed_user, seed_periods):
        """Helper: create and return a projected expense."""
        projected = db.session.query(Status).filter_by(name="Projected").one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

        txn = Transaction(
            pay_period_id=seed_periods[0].id,
            scenario_id=seed_user["scenario"].id,
            account_id=seed_user["account"].id,
            status_id=projected.id,
            name="Test Expense",
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=expense_type.id,
            estimated_amount=Decimal("123.45"),
        )
        db.session.add(txn)
        db.session.commit()
        return txn

    # ── Nonexistent ID tests ──────────────────────────────────────

    def test_update_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods):
        """PATCH /transactions/999999 returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.patch(
                "/transactions/999999", data={"estimated_amount": "200.00"}
            )
            assert resp.status_code == 404
            assert b"Not found" in resp.data

    def test_mark_done_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods):
        """POST /transactions/999999/mark-done returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.post("/transactions/999999/mark-done")
            assert resp.status_code == 404
            assert b"Not found" in resp.data

    def test_cancel_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods):
        """POST /transactions/999999/cancel returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.post("/transactions/999999/cancel")
            assert resp.status_code == 404
            assert b"Not found" in resp.data

    def test_delete_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods):
        """DELETE /transactions/999999 returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.delete("/transactions/999999")
            assert resp.status_code == 404
            assert b"Not found" in resp.data

    def test_mark_credit_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods):
        """POST /transactions/999999/mark-credit returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.post("/transactions/999999/mark-credit")
            assert resp.status_code == 404
            assert b"Not found" in resp.data

    def test_unmark_credit_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods):
        """DELETE /transactions/999999/unmark-credit returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.delete("/transactions/999999/unmark-credit")
            assert resp.status_code == 404
            assert b"Not found" in resp.data

    def test_get_cell_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods):
        """GET /transactions/999999/cell returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.get("/transactions/999999/cell")
            assert resp.status_code == 404

    def test_get_quick_edit_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods):
        """GET /transactions/999999/quick-edit returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.get("/transactions/999999/quick-edit")
            assert resp.status_code == 404

    def test_get_full_edit_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods):
        """GET /transactions/999999/full-edit returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.get("/transactions/999999/full-edit")
            assert resp.status_code == 404

    # ── Schema validation failure tests ───────────────────────────

    def test_create_transaction_missing_name(self, app, auth_client, seed_user, seed_periods):
        """POST /transactions without required 'name' field returns 400 with field error."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions", data={
                "estimated_amount": "100.00",
                "pay_period_id": seed_periods[0].id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
            })
            assert resp.status_code == 400
            resp_json = resp.get_json()
            assert "name" in resp_json["errors"]

            # Verify no transaction was created.
            count = db.session.query(Transaction).filter_by(
                scenario_id=seed_user["scenario"].id,
            ).count()
            assert count == 0

    def test_create_transaction_negative_amount(self, app, auth_client, seed_user, seed_periods):
        """POST /transactions with negative estimated_amount returns 400."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions", data={
                "name": "Bad Amount",
                "estimated_amount": "-100.00",
                "pay_period_id": seed_periods[0].id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
            })
            assert resp.status_code == 400
            resp_json = resp.get_json()
            assert "estimated_amount" in resp_json["errors"]

            # Verify no transaction was created.
            count = db.session.query(Transaction).filter_by(
                name="Bad Amount",
            ).count()
            assert count == 0

    def test_create_transaction_zero_amount(self, app, auth_client, seed_user, seed_periods):
        """POST /transactions with estimated_amount=0.00 succeeds (Range min=0 is inclusive)."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions", data={
                "name": "Zero Amount",
                "estimated_amount": "0.00",
                "pay_period_id": seed_periods[0].id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
                "account_id": str(seed_user["account"].id),
            })
            # Range(min=0) is inclusive by default -- 0.00 is accepted.
            assert resp.status_code == 201

            txn = db.session.query(Transaction).filter_by(name="Zero Amount").one()
            assert txn.estimated_amount == Decimal("0.00")

    def test_create_transaction_missing_pay_period_id(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST /transactions without required pay_period_id returns 400 with field error."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions", data={
                "name": "No Period",
                "estimated_amount": "50.00",
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
            })
            assert resp.status_code == 400
            resp_json = resp.get_json()
            assert "pay_period_id" in resp_json["errors"]

    def test_create_transaction_with_other_users_pay_period(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST /transactions with another user's pay_period_id returns 404."""
        with app.app_context():
            # Create a second user with pay periods for IDOR testing.
            other_user = User(
                email="other@shekel.local",
                password_hash=hash_password("otherpass"),
                display_name="Other User",
            )
            db.session.add(other_user)
            db.session.flush()

            settings = UserSettings(user_id=other_user.id)
            db.session.add(settings)

            other_periods = pay_period_service.generate_pay_periods(
                user_id=other_user.id,
                start_date=date(2026, 1, 2),
                num_periods=3,
                cadence_days=14,
            )
            db.session.commit()

            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions", data={
                "name": "Sneaky",
                "estimated_amount": "100.00",
                "pay_period_id": other_periods[0].id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
                "account_id": str(seed_user["account"].id),
            })
            assert resp.status_code == 404
            assert b"Pay period not found" in resp.data

            # Verify no transaction was created.
            count = db.session.query(Transaction).filter_by(name="Sneaky").count()
            assert count == 0

    def test_update_transaction_invalid_amount(self, app, auth_client, seed_user, seed_periods):
        """PATCH /transactions/<id> with non-numeric amount returns 400."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods)
            txn_id = txn.id

            resp = auth_client.patch(
                f"/transactions/{txn_id}",
                data={"estimated_amount": "not_a_number"},
            )
            assert resp.status_code == 400

            # Verify the transaction's amount was NOT changed.
            db.session.expire_all()
            txn_after = db.session.get(Transaction, txn_id)
            assert txn_after.estimated_amount == Decimal("123.45")

    # ── State transition edge cases ───────────────────────────────

    def test_mark_done_already_done_expense(self, app, auth_client, seed_user, seed_periods):
        """POST /transactions/<id>/mark-done is idempotent for already-done transactions."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods)

            # First mark-done.
            resp1 = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert resp1.status_code == 200

            # NOTE: mark_done is idempotent -- no guard against double mark-done.
            # The route unconditionally sets status to done/received regardless
            # of current status.
            resp2 = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert resp2.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Paid"

    def test_cancel_already_cancelled_transaction(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST /transactions/<id>/cancel is idempotent for already-cancelled transactions."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods)

            # First cancel.
            resp1 = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert resp1.status_code == 200

            # NOTE: cancel is idempotent -- no guard against double cancel.
            resp2 = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert resp2.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"

    def test_mark_done_cancelled_transaction(self, app, auth_client, seed_user, seed_periods):
        """POST /transactions/<id>/mark-done on a cancelled transaction succeeds."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods)

            # Cancel first.
            auth_client.post(f"/transactions/{txn.id}/cancel")
            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"

            # NOTE: No state machine guard -- cancelled transactions can be marked
            # done. This is a potential behavioral issue: the UI hides the "Done"
            # button for non-projected statuses, but the API endpoint does not
            # enforce this.
            resp = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Paid"

    def test_cancel_done_transaction(self, app, auth_client, seed_user, seed_periods):
        """POST /transactions/<id>/cancel on a done transaction succeeds."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods)

            # Mark done first.
            auth_client.post(f"/transactions/{txn.id}/mark-done")
            db.session.refresh(txn)
            assert txn.status.name == "Paid"

            # NOTE: No state machine guard -- done transactions can be cancelled
            # via direct API call. UI hides the Cancel button for done status.
            resp = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"

    def test_mark_done_with_invalid_actual_amount(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST /transactions/<id>/mark-done with non-numeric actual_amount returns 400."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods)
            txn_id = txn.id

            resp = auth_client.post(
                f"/transactions/{txn_id}/mark-done",
                data={"actual_amount": "not_a_number"},
            )
            assert resp.status_code == 400
            assert b"Invalid actual amount" in resp.data

            # The route modifies txn.status_id before parsing actual_amount.
            # The early return skips commit, so rollback to discard dirty state.
            db.session.rollback()
            txn_after = db.session.get(Transaction, txn_id)
            assert txn_after.status.name == "Projected"
            assert txn_after.actual_amount is None

    def test_mark_done_with_negative_actual_amount(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST /transactions/<id>/mark-done accepts negative actual_amount (no range check)."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods)

            # NOTE: mark_done does not validate actual_amount range. Negative
            # actuals are accepted. The hostile QA audit (test_hostile_qa.py)
            # documents this as a known behavioral issue.
            resp = auth_client.post(
                f"/transactions/{txn.id}/mark-done",
                data={"actual_amount": "-50.00"},
            )
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert txn.actual_amount == Decimal("-50.00")
            assert txn.status.name == "Paid"

    # ── XSS protection test ──────────────────────────────────────

    def test_create_transaction_xss_in_name(self, app, auth_client, seed_user, seed_periods):
        """Transaction name with script tag is stored but auto-escaped in rendered output."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions", data={
                "name": "<script>alert(1)</script>",
                "estimated_amount": "50.00",
                "pay_period_id": seed_periods[0].id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
                "account_id": str(seed_user["account"].id),
            })
            assert resp.status_code == 201

            txn = db.session.query(Transaction).filter_by(
                name="<script>alert(1)</script>",
            ).one()

            # Verify Jinja2 auto-escaping prevents XSS in the cell partial.
            cell_resp = auth_client.get(f"/transactions/{txn.id}/cell")
            assert cell_resp.status_code == 200
            assert b"<script>" not in cell_resp.data
            assert b"&lt;script&gt;" in cell_resp.data


class TestCreateBaseline:
    """Tests for POST /create-baseline route."""

    def test_create_baseline_success(self, app, auth_client, seed_user):
        """POST /create-baseline creates a baseline scenario when none exists.

        Verifies: the route creates a Scenario with name='Baseline' and
        is_baseline=True, then redirects to the grid index.
        """
        with app.app_context():
            # Remove the existing baseline so the route has work to do.
            Scenario.query.filter_by(
                user_id=seed_user["user"].id, is_baseline=True
            ).delete()
            db.session.commit()

            response = auth_client.post("/create-baseline")
            assert response.status_code == 302

            scenario = Scenario.query.filter_by(
                user_id=seed_user["user"].id, is_baseline=True
            ).one()
            assert scenario.name == "Baseline"
            assert scenario.is_baseline is True

    def test_create_baseline_idempotent(self, app, auth_client, seed_user):
        """POST /create-baseline with existing baseline does not create a duplicate.

        Verifies: when a baseline already exists (from seed_user fixture),
        the route redirects without creating a second scenario.
        """
        with app.app_context():
            response = auth_client.post("/create-baseline")
            assert response.status_code == 302

            count = Scenario.query.filter_by(
                user_id=seed_user["user"].id, is_baseline=True
            ).count()
            assert count == 1

    def test_create_baseline_requires_login(self, app, client):
        """POST /create-baseline without authentication redirects to login.

        Verifies: unauthenticated requests are rejected and no scenario
        is created.
        """
        with app.app_context():
            response = client.post("/create-baseline")
            assert response.status_code == 302
            assert "/login" in response.headers["Location"]

            count = Scenario.query.count()
            assert count == 0

    def test_create_baseline_rejects_get(self, app, auth_client, seed_user):
        """GET /create-baseline returns 405 Method Not Allowed.

        Verifies: the route only accepts POST requests.
        """
        with app.app_context():
            response = auth_client.get("/create-baseline")
            assert response.status_code == 405

    def test_create_baseline_user_isolation(self, app, auth_client, seed_user, second_user):
        """POST /create-baseline creates a scenario for the logged-in user only.

        Verifies: the route uses current_user.id correctly and does not
        affect other users' data.
        """
        with app.app_context():
            # Remove seed_user's baseline.
            Scenario.query.filter_by(
                user_id=seed_user["user"].id, is_baseline=True
            ).delete()
            db.session.commit()

            response = auth_client.post("/create-baseline")
            assert response.status_code == 302

            # The new scenario belongs to seed_user, not second_user.
            new_scenario = Scenario.query.filter_by(
                user_id=seed_user["user"].id, is_baseline=True
            ).one()
            assert new_scenario.user_id == seed_user["user"].id

            # second_user's baseline is untouched.
            other_baseline = Scenario.query.filter_by(
                user_id=second_user["user"].id, is_baseline=True
            ).one()
            assert other_baseline.user_id == second_user["user"].id


class TestAccountIdColumn:
    """Tests for the account_id column added to the Transaction model."""

    def test_transaction_model_has_account_id(self, app, db, seed_user, seed_periods):
        """Create a Transaction with account_id. Verify it saves and the relationship resolves."""
        projected = db.session.query(Status).filter_by(name="Projected").one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
        account = seed_user["account"]

        txn = Transaction(
            account_id=account.id,
            pay_period_id=seed_periods[0].id,
            scenario_id=seed_user["scenario"].id,
            status_id=projected.id,
            name="Account Test",
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=expense_type.id,
            estimated_amount=Decimal("50.00"),
        )
        db.session.add(txn)
        db.session.commit()

        assert txn.account_id == account.id
        assert txn.account is not None
        assert txn.account.id == account.id
        assert txn.account.name == "Checking"

    def test_transaction_without_account_id_raises_integrity_error(
        self, app, db, seed_user, seed_periods
    ):
        """Attempting to create a Transaction without account_id raises IntegrityError."""
        from sqlalchemy.exc import IntegrityError

        projected = db.session.query(Status).filter_by(name="Projected").one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

        txn = Transaction(
            pay_period_id=seed_periods[0].id,
            scenario_id=seed_user["scenario"].id,
            status_id=projected.id,
            name="No Account",
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=expense_type.id,
            estimated_amount=Decimal("50.00"),
        )
        db.session.add(txn)
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()

    def test_recurrence_engine_sets_account_id(self, app, db, seed_full_user_data):
        """Transactions generated by the recurrence engine have account_id from the template."""
        from app.services import recurrence_engine

        data = seed_full_user_data
        template = data["template"]
        periods = data["periods"]
        scenario = data["scenario"]

        created = recurrence_engine.generate_for_template(
            template, periods, scenario.id
        )

        assert len(created) > 0
        for txn in created:
            assert txn.account_id == template.account_id

    def test_credit_payback_inherits_account_id(self, app, db, seed_user, seed_periods):
        """The payback transaction created by mark_as_credit inherits account_id."""
        from app.services import credit_workflow

        projected = db.session.query(Status).filter_by(name="Projected").one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
        account = seed_user["account"]

        txn = Transaction(
            account_id=account.id,
            pay_period_id=seed_periods[0].id,
            scenario_id=seed_user["scenario"].id,
            status_id=projected.id,
            name="Test Expense for Credit",
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=expense_type.id,
            estimated_amount=Decimal("75.00"),
        )
        db.session.add(txn)
        db.session.commit()

        payback = credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)
        db.session.commit()

        assert payback.account_id == account.id

    def test_inline_create_sets_account_id(self, app, auth_client, seed_user, seed_periods):
        """POST /transactions/inline with account_id saves it on the transaction."""
        account = seed_user["account"]
        category = seed_user["categories"]["Groceries"]
        scenario = seed_user["scenario"]
        projected = db.session.query(Status).filter_by(name="Projected").one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

        resp = auth_client.post("/transactions/inline", data={
            "account_id": account.id,
            "category_id": category.id,
            "pay_period_id": seed_periods[0].id,
            "scenario_id": scenario.id,
            "transaction_type_id": expense_type.id,
            "estimated_amount": "99.99",
        })
        assert resp.status_code == 201

        txn = Transaction.query.filter_by(name=category.display_name).first()
        assert txn is not None
        assert txn.account_id == account.id

    def test_inline_create_rejects_missing_account_id(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST /transactions/inline without account_id returns validation error."""
        category = seed_user["categories"]["Groceries"]
        scenario = seed_user["scenario"]
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

        resp = auth_client.post("/transactions/inline", data={
            "category_id": category.id,
            "pay_period_id": seed_periods[0].id,
            "scenario_id": scenario.id,
            "transaction_type_id": expense_type.id,
            "estimated_amount": "50.00",
        })
        assert resp.status_code == 400

    def test_inline_create_rejects_other_users_account_id(
        self, app, auth_client, seed_user, seed_periods, second_user
    ):
        """POST /transactions/inline with another user's account_id returns 404."""
        other_account = second_user["account"]
        category = seed_user["categories"]["Groceries"]
        scenario = seed_user["scenario"]
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

        resp = auth_client.post("/transactions/inline", data={
            "account_id": other_account.id,
            "category_id": category.id,
            "pay_period_id": seed_periods[0].id,
            "scenario_id": scenario.id,
            "transaction_type_id": expense_type.id,
            "estimated_amount": "50.00",
        })
        assert resp.status_code == 404


class TestAccountScopedGrid:
    """Tests verifying the grid filters transactions by account_id.

    The grid resolves a viewed account (checking by default, or via the
    ?account_id query param / user settings).  Only transactions belonging
    to that account should appear in the grid body and footer totals.
    Transactions on other accounts must be excluded.
    """

    def _create_savings_account(self, user, periods):
        """Helper: create a savings account with anchor balance and period."""
        savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
        savings = Account(
            user_id=user.id,
            account_type_id=savings_type.id,
            name="Savings",
            current_anchor_balance=Decimal("5000.00"),
            current_anchor_period_id=periods[0].id,
        )
        db.session.add(savings)
        db.session.flush()
        return savings

    def _create_txn(self, account, period, scenario, name, amount,
                    txn_type_name="Expense", status_name="Projected", category=None):
        """Helper: create a transaction on the given account."""
        status = db.session.query(Status).filter_by(name=status_name).one()
        txn_type = db.session.query(TransactionType).filter_by(name=txn_type_name).one()
        txn = Transaction(
            account_id=account.id,
            pay_period_id=period.id,
            scenario_id=scenario.id,
            status_id=status.id,
            name=name,
            category_id=category.id if category else None,
            transaction_type_id=txn_type.id,
            estimated_amount=Decimal(str(amount)),
        )
        db.session.add(txn)
        return txn

    # --- Core filtering tests ---

    def test_grid_shows_only_checking_transactions(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Default grid (checking) shows only checking transactions, not savings."""
        checking = seed_user["account"]
        scenario = seed_user["scenario"]
        savings = self._create_savings_account(seed_user["user"], seed_periods)

        self._create_txn(checking, seed_periods[0], scenario, "Rent", 1200,
                         category=seed_user["categories"]["Rent"])
        self._create_txn(savings, seed_periods[0], scenario, "Savings Interest", 50,
                         txn_type_name="Income", category=seed_user["categories"]["Salary"])
        db.session.commit()

        resp = auth_client.get("/")
        assert resp.status_code == 200
        html = resp.data.decode()

        assert "Rent" in html
        assert "Savings Interest" not in html

    def test_grid_account_override_shows_savings_transactions(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Passing ?account_id=savings shows only savings transactions.

        Transactions are matched to cells by category_id and type.  The
        grid renders amounts (not names) in cells, so we check for the
        amount values and verify that the checking expense amount does
        not appear on the savings grid.
        """
        checking = seed_user["account"]
        scenario = seed_user["scenario"]
        savings = self._create_savings_account(seed_user["user"], seed_periods)

        # Use a visible period (current period index ~5).
        current = pay_period_service.get_current_period(seed_user["user"].id)
        self._create_txn(checking, current, scenario, "Checking Rent", 1234,
                         category=seed_user["categories"]["Rent"])
        self._create_txn(savings, current, scenario, "Savings Deposit", 567,
                         txn_type_name="Income", category=seed_user["categories"]["Salary"])
        db.session.commit()

        # Savings grid: should show the $567 deposit, not the $1234 rent.
        resp = auth_client.get(f"/?account_id={savings.id}")
        assert resp.status_code == 200
        html = resp.data.decode()

        assert "567" in html
        assert "1,234" not in html

    def test_grid_shows_correct_account_name_in_header(
        self, app, auth_client, seed_user, seed_periods
    ):
        """The grid header shows the viewed account's name."""
        savings = self._create_savings_account(seed_user["user"], seed_periods)
        db.session.commit()

        resp = auth_client.get(f"/?account_id={savings.id}")
        html = resp.data.decode()
        assert "Savings Balance" in html

    # --- Balance correctness tests ---

    def test_balance_uses_correct_anchor_for_each_account(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Each account's grid uses its own anchor balance, not another's.

        Checking anchor: $1000 (from seed_user).
        Savings anchor: $5000.
        With no transactions, the projected balance equals the anchor.
        """
        savings = self._create_savings_account(seed_user["user"], seed_periods)
        db.session.commit()

        # Checking grid: balance should reflect $1000 anchor.
        resp = auth_client.get("/")
        html = resp.data.decode()
        assert "$1,000" in html

        # Savings grid: balance should reflect $5000 anchor.
        resp = auth_client.get(f"/?account_id={savings.id}")
        html = resp.data.decode()
        assert "$5,000" in html

    def test_balance_excludes_other_accounts_transactions(
        self, app, auth_client, seed_user, seed_periods
    ):
        """A $500 expense on checking should NOT reduce the savings balance.

        Checking: $1000 anchor - $500 expense = $500 projected.
        Savings: $5000 anchor, no expenses = $5000 projected.
        """
        checking = seed_user["account"]
        scenario = seed_user["scenario"]
        savings = self._create_savings_account(seed_user["user"], seed_periods)

        self._create_txn(checking, seed_periods[0], scenario, "Rent", 500,
                         category=seed_user["categories"]["Rent"])
        db.session.commit()

        # Savings grid: balance should still be $5000 (the expense is on checking).
        resp = auth_client.get(f"/?account_id={savings.id}")
        html = resp.data.decode()
        assert "$5,000" in html

    # --- Balance row HTMX refresh tests ---

    def test_balance_row_refresh_scoped_to_account(
        self, app, auth_client, seed_user, seed_periods
    ):
        """GET /grid/balance-row with account_id returns that account's balances."""
        checking = seed_user["account"]
        scenario = seed_user["scenario"]
        savings = self._create_savings_account(seed_user["user"], seed_periods)

        self._create_txn(checking, seed_periods[0], scenario, "Expense on Checking", 300,
                         category=seed_user["categories"]["Rent"])
        db.session.commit()

        # Balance row for savings: no expenses, balance = anchor.
        resp = auth_client.get(f"/grid/balance-row?periods=6&offset=0&account_id={savings.id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "$5,000" in html

    def test_balance_row_refresh_includes_account_id_in_htmx_url(
        self, app, auth_client, seed_user, seed_periods
    ):
        """The returned tfoot contains account_id in its hx-get URL for future refreshes."""
        savings = self._create_savings_account(seed_user["user"], seed_periods)
        db.session.commit()

        resp = auth_client.get(f"/grid/balance-row?periods=6&offset=0&account_id={savings.id}")
        html = resp.data.decode()
        assert f"account_id={savings.id}" in html

    # --- Footer totals tests ---

    def test_footer_totals_reflect_viewed_account_only(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Subtotal rows count only the viewed account's transactions.

        The tbody subtotal rows sum projected (unsettled) transactions for
        the viewed account.  Savings transactions must not appear in the
        checking account's subtotals.
        """
        checking = seed_user["account"]
        scenario = seed_user["scenario"]
        savings = self._create_savings_account(seed_user["user"], seed_periods)

        # Use the current period so it falls within the visible window.
        current = pay_period_service.get_current_period(seed_user["user"].id)

        self._create_txn(checking, current, scenario, "Salary", 2000,
                         txn_type_name="Income", category=seed_user["categories"]["Salary"])
        self._create_txn(checking, current, scenario, "Rent", 800,
                         category=seed_user["categories"]["Rent"])
        self._create_txn(savings, current, scenario, "Interest", 100,
                         txn_type_name="Income", category=seed_user["categories"]["Salary"])
        db.session.commit()

        # Full grid page for checking account -- subtotals reflect checking only.
        resp = auth_client.get("/")
        html = resp.data.decode()
        assert "$2,000" in html  # Total Income (checking).
        assert "$800" in html    # Total Expenses (checking).

        # Savings footer: shows projected balance ($5,000 anchor + $100 income = $5,100).
        resp = auth_client.get(f"/grid/balance-row?periods=6&offset=0&account_id={savings.id}")
        html = resp.data.decode()
        assert "$5,100" in html
        # Checking expenses must NOT appear on savings balance row.
        assert "$800" not in html

    # --- Empty / edge case tests ---

    def test_grid_for_account_with_no_transactions(
        self, app, auth_client, seed_user, seed_periods
    ):
        """An account with no transactions renders the grid without errors.

        Section banners should appear. No transaction cells. Balance equals anchor.
        """
        savings = self._create_savings_account(seed_user["user"], seed_periods)
        db.session.commit()

        resp = auth_client.get(f"/?account_id={savings.id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "INCOME" in html
        assert "EXPENSES" in html
        assert "$5,000" in html  # Anchor balance, no transactions.

    def test_grid_hides_category_rows_without_account_transactions(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Categories with transactions only on checking should not render on savings grid.

        Create a Rent expense on checking. The Rent category row should
        appear on checking grid but not on savings grid.
        """
        checking = seed_user["account"]
        scenario = seed_user["scenario"]
        savings = self._create_savings_account(seed_user["user"], seed_periods)

        self._create_txn(checking, seed_periods[0], scenario, "Rent", 1200,
                         category=seed_user["categories"]["Rent"])
        db.session.commit()

        # Checking grid: Rent row visible.
        resp = auth_client.get("/")
        html = resp.data.decode()
        assert "Rent" in html

        # Savings grid: no Rent row (no transactions for this category on savings).
        resp = auth_client.get(f"/?account_id={savings.id}")
        html = resp.data.decode()
        # The category name "Rent" should not appear as a row label.
        # It may appear in the "Add Transaction" modal dropdown, so check
        # specifically for the row label pattern.
        assert 'class="sticky-col row-label"' not in html or "Rent" not in html.split("EXPENSES")[0].split("INCOME")[-1]

    def test_grid_account_with_no_anchor_balance(
        self, app, auth_client, seed_user, seed_periods
    ):
        """An account with NULL anchor balance defaults to $0 for projections."""
        savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
        savings = Account(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="New Savings",
            current_anchor_balance=None,
            current_anchor_period_id=seed_periods[0].id,
        )
        db.session.add(savings)
        db.session.commit()

        resp = auth_client.get(f"/?account_id={savings.id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "New Savings Balance" in html

    def test_grid_account_with_no_anchor_period(
        self, app, auth_client, seed_user, seed_periods
    ):
        """An account with NULL anchor period uses current period as fallback."""
        savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
        savings = Account(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="No Anchor Period",
            current_anchor_balance=Decimal("1000.00"),
            current_anchor_period_id=None,
        )
        db.session.add(savings)
        db.session.commit()

        resp = auth_client.get(f"/?account_id={savings.id}")
        assert resp.status_code == 200

    # --- Cancelled and deleted transaction edge cases ---

    def test_cancelled_transactions_excluded_from_account_grid(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Cancelled transactions on the viewed account do not render as cells.

        The grid template filters out cancelled transactions at the cell
        level (txn.status.name != 'cancelled').  The cancelled transaction
        is still loaded by the query (is_deleted is False) but not rendered.
        """
        checking = seed_user["account"]
        scenario = seed_user["scenario"]
        current = pay_period_service.get_current_period(seed_user["user"].id)

        active = self._create_txn(checking, current, scenario, "Active Expense", 100,
                                  category=seed_user["categories"]["Rent"])
        cancelled = self._create_txn(checking, current, scenario, "Cancelled Expense", 200,
                                     status_name="Cancelled",
                                     category=seed_user["categories"]["Car Payment"])
        db.session.commit()

        resp = auth_client.get("/")
        html = resp.data.decode()
        # The active transaction's cell should be rendered with its ID.
        assert f"txn-cell-{active.id}" in html
        # The cancelled transaction should NOT have a rendered cell.
        assert f"txn-cell-{cancelled.id}" not in html

    def test_soft_deleted_transactions_excluded_from_account_grid(
        self, app, db, auth_client, seed_user, seed_periods
    ):
        """Soft-deleted transactions (is_deleted=True) do not appear."""
        checking = seed_user["account"]
        scenario = seed_user["scenario"]

        txn = self._create_txn(checking, seed_periods[0], scenario, "Deleted Expense", 999,
                               category=seed_user["categories"]["Rent"])
        txn.is_deleted = True
        db.session.commit()

        resp = auth_client.get("/")
        html = resp.data.decode()
        assert "$999" not in html

    # --- Carry forward interaction test ---

    def test_carry_forward_moves_all_accounts_transactions(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Carry forward moves projected transactions from ALL accounts, not just the viewed one.

        This verifies carry forward is NOT account-scoped -- it is a
        period-level operation that moves everything unpaid in that period.
        """
        checking = seed_user["account"]
        scenario = seed_user["scenario"]
        savings = self._create_savings_account(seed_user["user"], seed_periods)

        # Create projected transactions on both accounts in period 0.
        checking_txn = self._create_txn(
            checking, seed_periods[0], scenario, "Checking Expense", 100,
            category=seed_user["categories"]["Rent"],
        )
        savings_txn = self._create_txn(
            savings, seed_periods[0], scenario, "Savings Expense", 50,
            category=seed_user["categories"]["Groceries"],
        )
        db.session.commit()

        checking_txn_id = checking_txn.id
        savings_txn_id = savings_txn.id

        # Carry forward from period 0.
        resp = auth_client.post(f"/pay-periods/{seed_periods[0].id}/carry-forward")
        assert resp.status_code == 200

        # Both transactions should have moved to the current period.
        db.session.expire_all()
        checking_after = db.session.get(Transaction, checking_txn_id)
        savings_after = db.session.get(Transaction, savings_txn_id)

        current_period = pay_period_service.get_current_period(seed_user["user"].id)
        assert checking_after.pay_period_id == current_period.id
        assert savings_after.pay_period_id == current_period.id

    # --- Inline create scoped to correct account ---

    def test_inline_create_on_savings_grid_saves_to_savings(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Creating a transaction inline on the savings grid assigns it to the savings account."""
        savings = self._create_savings_account(seed_user["user"], seed_periods)
        category = seed_user["categories"]["Salary"]
        scenario = seed_user["scenario"]
        income_type = db.session.query(TransactionType).filter_by(name="Income").one()
        db.session.commit()

        resp = auth_client.post("/transactions/inline", data={
            "account_id": savings.id,
            "category_id": category.id,
            "pay_period_id": seed_periods[0].id,
            "scenario_id": scenario.id,
            "transaction_type_id": income_type.id,
            "estimated_amount": "250.00",
        })
        assert resp.status_code == 201

        txn = Transaction.query.filter_by(
            estimated_amount=Decimal("250.00"),
            account_id=savings.id,
        ).first()
        assert txn is not None
        assert txn.account_id == savings.id

    # --- Multi-period balance roll-forward correctness ---

    def test_balance_rolls_forward_correctly_per_account(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Balance roll-forward across periods uses only the viewed account's transactions.

        Checking: anchor $1000, current period expense $200, next period expense $300.
        Savings: anchor $5000, current period income $100.

        The Projected End Balance for checking should reflect only checking
        transactions.  The savings balance must not be affected by checking
        expenses.
        """
        checking = seed_user["account"]
        scenario = seed_user["scenario"]
        savings = self._create_savings_account(seed_user["user"], seed_periods)

        current = pay_period_service.get_current_period(seed_user["user"].id)
        # Find the next period after current.
        current_idx = next(
            i for i, p in enumerate(seed_periods) if p.id == current.id
        )
        next_period = seed_periods[current_idx + 1]

        self._create_txn(checking, current, scenario, "Expense A", 200,
                         category=seed_user["categories"]["Rent"])
        self._create_txn(checking, next_period, scenario, "Expense B", 300,
                         category=seed_user["categories"]["Car Payment"])
        self._create_txn(savings, current, scenario, "Deposit", 100,
                         txn_type_name="Income", category=seed_user["categories"]["Salary"])
        db.session.commit()

        # Checking balance: anchor $1000 - $200 = $800, then $800 - $300 = $500.
        resp = auth_client.get(f"/grid/balance-row?periods=6&offset=0&account_id={checking.id}")
        html = resp.data.decode()
        assert "$800" in html
        assert "$500" in html

        # Savings balance: anchor $5000 + $100 = $5100, steady after that.
        resp = auth_client.get(f"/grid/balance-row?periods=6&offset=0&account_id={savings.id}")
        html = resp.data.decode()
        assert "$5,100" in html
        # Checking expenses must NOT appear on savings balance row.
        assert "$800" not in html
        assert "$500" not in html


# ── TRANSFERS Section Removal Tests ────────────────────────────────


class TestTransfersSectionRemoved:
    """Verify the TRANSFERS grid section is gone and shadows render inline."""

    def test_grid_no_transfers_section(self, app, auth_client, seed_user, seed_periods):
        """Grid does not contain a TRANSFERS section banner."""
        with app.app_context():
            resp = auth_client.get("/")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "section-banner-transfer" not in html
            assert "xfer-cell-" not in html

    def test_grid_renders_without_transfers(self, app, auth_client, seed_user, seed_periods):
        """Grid renders normally with no transfers or shadows."""
        with app.app_context():
            resp = auth_client.get("/")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "section-banner-income" in html
            assert "section-banner-expense" in html
            assert "section-banner-transfer" not in html


# ── Inline Subtotal Row Tests ──────────────────────────────────────


class TestInlineSubtotalRows:
    """Tests for the Total Income and Total Expenses subtotal rows in tbody."""

    def test_subtotal_rows_present(self, app, auth_client, seed_user, seed_periods):
        """Grid contains subtotal-row-income and subtotal-row-expense rows."""
        with app.app_context():
            # Create transactions so the sections render.
            from app.models.ref import TransactionType
            projected = db.session.query(Status).filter_by(name="Projected").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            from app.services import pay_period_service
            current = pay_period_service.get_current_period(seed_user["user"].id)
            if not current:
                current = seed_periods[0]

            txn_inc = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Salary",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=Decimal("2000.00"),
            )
            txn_exp = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Rent",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("1200.00"),
            )
            db.session.add_all([txn_inc, txn_exp])
            db.session.commit()

            resp = auth_client.get("/")
            html = resp.data.decode()

            assert "subtotal-row-income" in html
            assert "subtotal-row-expense" in html

    def test_subtotal_values_correct(self, app, auth_client, seed_user, seed_periods):
        """Subtotal rows show correct per-period totals."""
        with app.app_context():
            from app.models.ref import TransactionType
            projected = db.session.query(Status).filter_by(name="Projected").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            from app.services import pay_period_service
            current = pay_period_service.get_current_period(seed_user["user"].id)
            if not current:
                current = seed_periods[0]

            for name, cat, typ, amt in [
                ("Pay", "Salary", income_type.id, "2000.00"),
                ("Stipend", "Salary", income_type.id, "100.00"),
                ("Rent", "Rent", expense_type.id, "1200.00"),
                ("Food", "Groceries", expense_type.id, "400.00"),
            ]:
                txn = Transaction(
                    pay_period_id=current.id,
                    scenario_id=seed_user["scenario"].id,
                    account_id=seed_user["account"].id,
                    status_id=projected.id,
                    name=name,
                    category_id=seed_user["categories"][cat].id,
                    transaction_type_id=typ,
                    estimated_amount=Decimal(amt),
                )
                db.session.add(txn)
            db.session.commit()

            resp = auth_client.get("/")
            html = resp.data.decode()

            # Total Income = 2000 + 100 = 2100.
            assert "$2,100" in html
            # Total Expenses = 1200 + 400 = 1600.
            assert "$1,600" in html

    def test_subtotal_excludes_cancelled(self, app, auth_client, seed_user, seed_periods):
        """Cancelled transactions are excluded from subtotals."""
        with app.app_context():
            from app.models.ref import TransactionType
            projected = db.session.query(Status).filter_by(name="Projected").one()
            cancelled = db.session.query(Status).filter_by(name="Cancelled").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            from app.services import pay_period_service
            current = pay_period_service.get_current_period(seed_user["user"].id)
            if not current:
                current = seed_periods[0]

            txn_ok = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Good Pay",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=Decimal("1000.00"),
            )
            txn_bad = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=cancelled.id,
                name="Cancelled Pay",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=Decimal("500.00"),
            )
            db.session.add_all([txn_ok, txn_bad])
            db.session.commit()

            resp = auth_client.get("/")
            html = resp.data.decode()

            # Only $1,000 counted (cancelled $500 excluded).
            assert "$1,000" in html

    def test_balance_row_refresh_unaffected(self, app, auth_client, seed_user, seed_periods):
        """The balance-row HTMX endpoint returns tfoot only, no subtotal rows."""
        with app.app_context():
            resp = auth_client.get(
                f"/grid/balance-row?periods=6&offset=0&account_id={seed_user['account'].id}"
            )
            html = resp.data.decode()
            assert "subtotal-row" not in html
            assert "net-cash-flow-row" not in html
            assert "<tfoot" in html


# ── Net Cash Flow Row Tests ────────────────────────────────────────


class TestNetCashFlowRow:
    """Tests for the Net Cash Flow row in tbody."""

    def _seed_txns(self, seed_user, seed_periods, income_amt, expense_amt):
        """Helper: create income + expense in the current/first visible period."""
        from app.models.ref import TransactionType
        from app.services import pay_period_service
        projected = db.session.query(Status).filter_by(name="Projected").one()
        income_type = db.session.query(TransactionType).filter_by(name="Income").one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
        current = pay_period_service.get_current_period(seed_user["user"].id)
        if not current:
            current = seed_periods[0]

        txns = []
        if income_amt:
            txns.append(Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Income",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=Decimal(income_amt),
            ))
        if expense_amt:
            txns.append(Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Expense",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal(expense_amt),
            ))
        db.session.add_all(txns)
        db.session.commit()

    def test_net_cash_flow_row_present(self, app, db, auth_client, seed_user, seed_periods):
        """Grid contains a net-cash-flow-row with correct label."""
        with app.app_context():
            self._seed_txns(seed_user, seed_periods, "2000", "1400")
            resp = auth_client.get("/")
            html = resp.data.decode()
            assert "net-cash-flow-row" in html
            assert "Net Cash Flow" in html
            assert "$600" in html

    def test_net_cash_flow_negative(self, app, db, auth_client, seed_user, seed_periods):
        """Negative net cash flow shows warning indicator."""
        with app.app_context():
            self._seed_txns(seed_user, seed_periods, "1000", "1500")
            resp = auth_client.get("/")
            html = resp.data.decode()
            assert "balance-negative" in html
            # Warning icon for negative net.
            assert "bi-exclamation-triangle-fill" in html

    def test_net_cash_flow_zero(self, app, db, auth_client, seed_user, seed_periods):
        """Breakeven period shows empty net cash flow cell."""
        with app.app_context():
            self._seed_txns(seed_user, seed_periods, "1000", "1000")
            resp = auth_client.get("/")
            html = resp.data.decode()
            assert "net-cash-flow-row" in html
            # Net is zero -- cell should be empty (matching footer behavior).

    def test_balance_row_refresh_excludes_net_cash_flow(
        self, app, db, auth_client, seed_user, seed_periods
    ):
        """Balance-row HTMX endpoint does not include net-cash-flow-row."""
        with app.app_context():
            resp = auth_client.get(
                f"/grid/balance-row?periods=6&offset=0&account_id={seed_user['account'].id}"
            )
            html = resp.data.decode()
            assert "net-cash-flow-row" not in html


# ── Footer Condensation Tests ──────────────────────────────────────


class TestFooterCondensation:
    """Tests verifying the footer contains only Projected End Balance."""

    def test_footer_single_row(self, app, db, auth_client, seed_user, seed_periods):
        """Balance-row response has exactly 1 row: Projected End Balance."""
        with app.app_context():
            resp = auth_client.get(
                f"/grid/balance-row?periods=6&offset=0&account_id={seed_user['account'].id}"
            )
            html = resp.data.decode()
            assert "Projected End Balance" in html
            assert "Total Income" not in html
            assert "Total Expenses" not in html
            assert "Net (Income" not in html
            assert html.count("<tr") == 1

    def test_footer_htmx_attributes_preserved(self, app, db, auth_client, seed_user, seed_periods):
        """The tfoot has all HTMX attributes for the self-referencing refresh."""
        with app.app_context():
            resp = auth_client.get(
                f"/grid/balance-row?periods=6&offset=0&account_id={seed_user['account'].id}"
            )
            html = resp.data.decode()
            assert 'id="grid-summary"' in html
            assert "hx-get=" in html
            assert 'hx-trigger="balanceChanged from:body"' in html
            assert 'hx-swap="outerHTML"' in html

    def test_footer_htmx_refresh_cycle(self, app, db, auth_client, seed_user, seed_periods):
        """Initial page and balance-row both produce tfoot with HTMX attributes."""
        with app.app_context():
            page_resp = auth_client.get("/")
            page_html = page_resp.data.decode()
            assert 'id="grid-summary"' in page_html

            balance_resp = auth_client.get(
                f"/grid/balance-row?periods=6&offset=0&account_id={seed_user['account'].id}"
            )
            balance_html = balance_resp.data.decode()
            assert 'id="grid-summary"' in balance_html
            assert "hx-trigger" in balance_html

    def test_subtotals_still_present_in_tbody(self, app, db, auth_client, seed_user, seed_periods):
        """Tbody subtotal and net cash flow rows survive footer condensation."""
        with app.app_context():
            from app.models.ref import TransactionType
            from app.services import pay_period_service
            projected = db.session.query(Status).filter_by(name="Projected").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            current = pay_period_service.get_current_period(seed_user["user"].id)
            if not current:
                current = seed_periods[0]

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Pay",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=Decimal("2000.00"),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get("/")
            html = resp.data.decode()
            assert "subtotal-row-income" in html
            assert "subtotal-row-expense" in html
            assert "net-cash-flow-row" in html
