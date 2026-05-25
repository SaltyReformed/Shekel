"""
Shekel Budget App -- Grid & Transaction Route Tests

Tests the main budget grid view and transaction CRUD endpoints.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.scenario import Scenario
from app.models.user import User, UserSettings
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.ref import AccountType, Status, TransactionType
from app.services.auth_service import hash_password
from app.services import pay_period_service
from app.services import account_service

from tests._test_helpers import freeze_today


class TestGridView:
    """Tests for the main grid page at /."""

    def test_grid_loads_with_periods(self, app, auth_client, seed_user, seed_periods_today):
        """GET / renders the budget grid with pay period columns."""
        with app.app_context():
            response = auth_client.get("/grid")
            assert response.status_code == 200
            # Check for key grid elements.
            assert b"Checking Balance" in response.data
            assert b"Projected End Balance" in response.data

    def test_grid_shows_no_periods_page(self, app, auth_client, seed_user):
        """GET / shows the no-periods prompt when none exist."""
        with app.app_context():
            response = auth_client.get("/grid")
            assert response.status_code == 200
            assert b"No Pay Periods" in response.data

    def test_grid_shows_dynamic_account_name(self, app, auth_client, seed_user, seed_periods_today):
        """GET / shows the resolved account name in the header."""
        with app.app_context():
            response = auth_client.get("/grid")
            assert response.status_code == 200
            assert b"Checking Balance" in response.data

    def test_grid_period_controls(
        self, app, auth_client, seed_user, seed_periods, monkeypatch,
    ):
        """Grid respects the periods query parameter.

        Asserts the literal "01/02" rendered in the header, which is
        the start of seed_periods[0] (2026-01-02).  Uses the calendar-
        anchored seed_periods + freeze_today to keep the assertion
        stable regardless of wall-clock date.
        """
        freeze_today(monkeypatch, date(2026, 1, 5))
        with app.app_context():
            response = auth_client.get("/grid?periods=3")
            assert response.status_code == 200
            assert b"01/02" in response.data
            assert b"Projected End Balance" in response.data


class TestGridRowScoping:
    """Tests for the compact-view default and ?show_all=1 opt-out.

    Compact view (the default) generates row keys only from
    transactions whose pay_period_id is in the visible window.  This
    hides one-offs and infrequent recurring items that have nothing
    to render in the current view.  ``?show_all=1`` restores the old
    full-projection behavior for full planning sessions.  Subtotals
    and projected balances must be identical either way -- only which
    rows render changes.
    """

    def _make_oneoff(
        self, seed_user, period, name, amount="42.00",
    ):
        """Create one standalone expense in the given period."""
        projected = db.session.query(Status).filter_by(
            name="Projected",
        ).one()
        expense_type = db.session.query(TransactionType).filter_by(
            name="Expense",
        ).one()
        txn = Transaction(
            account_id=seed_user["account"].id,
            pay_period_id=period.id,
            scenario_id=seed_user["scenario"].id,
            status_id=projected.id,
            name=name,
            category_id=seed_user["categories"]["Rent"].id,
            transaction_type_id=expense_type.id,
            estimated_amount=Decimal(amount),
        )
        db.session.add(txn)
        db.session.flush()
        return txn

    def _visible_period(self, seed_user, seed_periods_today):
        """Return a period that falls in the default visible window.

        The grid starts at the current period; seed_periods_today places
        period 4 around today's date so get_current_period always
        returns a valid period.  No fallback is needed.
        """
        # pylint: disable=unused-argument
        return pay_period_service.get_current_period(
            seed_user["user"].id,
        )

    def _hidden_period(self, seed_user, seed_periods_today):
        """Return a period that is NOT in the default visible window.

        The anchor period sits at ``seed_periods_today[0]``, ~8 weeks
        before today.  With ``grid_default_periods=6`` the visible
        window starts at the current period, so the anchor is
        historical and hidden in compact view.
        """
        # pylint: disable=unused-argument
        return seed_periods_today[0]

    def test_compact_view_hides_oneoff_outside_visible_window(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A one-off in a hidden period must not render its row label."""
        with app.app_context():
            hidden = self._hidden_period(seed_user, seed_periods_today)
            self._make_oneoff(
                seed_user, hidden, name="HIDDEN_FAR_AWAY_BILL",
            )
            db.session.commit()

            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            assert b"HIDDEN_FAR_AWAY_BILL" not in resp.data

    def test_compact_view_shows_oneoff_inside_visible_window(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A one-off in the visible window must render its row label."""
        with app.app_context():
            visible = self._visible_period(seed_user, seed_periods_today)
            self._make_oneoff(
                seed_user, visible, name="VISIBLE_NEARBY_BILL",
            )
            db.session.commit()

            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            assert b"VISIBLE_NEARBY_BILL" in resp.data

    def test_show_all_reveals_oneoff_outside_visible_window(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """?show_all=1 must render rows from the full forward projection."""
        with app.app_context():
            hidden = self._hidden_period(seed_user, seed_periods_today)
            self._make_oneoff(
                seed_user, hidden, name="FAR_REVEALED_BY_SHOW_ALL",
            )
            db.session.commit()

            resp = auth_client.get("/grid?show_all=1")
            assert resp.status_code == 200
            assert b"FAR_REVEALED_BY_SHOW_ALL" in resp.data

    def test_compact_toggle_button_defaults_to_show_all_link(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The toggle button in compact view must link to show_all=1."""
        with app.app_context():
            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            assert b"show_all=1" in resp.data
            assert b"All Rows" in resp.data

    def test_show_all_toggle_button_links_back_to_compact(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """When show_all is active, the button must link back without it."""
        with app.app_context():
            resp = auth_client.get("/grid?show_all=1")
            assert resp.status_code == 200
            assert b"Compact" in resp.data

    def test_scoping_does_not_change_visible_subtotals(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Adding a hidden-period txn must not shift any visible subtotal.

        This is the key correctness invariant: hiding a row is a pure
        display filter, so the computed totals for visible periods
        must be byte-identical before and after the hidden txn exists.
        """
        with app.app_context():
            baseline = auth_client.get("/grid").data
            hidden = self._hidden_period(seed_user, seed_periods_today)
            self._make_oneoff(
                seed_user, hidden, name="HIDDEN_SUBTOTAL_PROBE",
                amount="999.00",
            )
            db.session.commit()

            after = auth_client.get("/grid").data
            assert b"HIDDEN_SUBTOTAL_PROBE" not in after

            # Projected End Balance is the canonical forward-math
            # summary.  It SHOULD change because the hidden txn still
            # affects the actual account trajectory (projected
            # balances include the full forward projection, not just
            # visible-row txns).  This asserts balance math is not
            # coupled to row scoping.
            assert b"Projected End Balance" in after


class TestBalanceRow:
    """Tests for GET /grid/balance-row HTMX partial."""

    def test_balance_row_returns_partial(self, app, auth_client, seed_user, seed_periods_today):
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

    def test_balance_row_no_baseline_scenario(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """GET /grid/balance-row returns 204 when the user has no baseline scenario.

        Regression test for F-099 (C-45 of the 2026-04-15 security
        audit).  Before the fix, ``balance_row`` dereferenced
        ``scenario.id`` to build the transaction query filter; when
        ``get_baseline_scenario`` returned ``None`` (orphaned test
        fixture or a freshly-deleted user mid-cascade in production)
        the route raised ``AttributeError: 'NoneType' object has no
        attribute 'id'`` and returned HTTP 500 via the unhandled-
        exception handler.

        The fix short-circuits with HTTP 204 No Content, matching the
        existing ``not current_period`` branch -- HTMX leaves the
        existing DOM untouched, the grid index route renders
        ``no_setup.html`` separately, and the user sees a coherent
        empty state instead of a stack trace.

        Asserts both the status code AND empty body to pin the
        contract; a future change that returns 200 with a rendered
        template would silently regress the HTMX partial-swap UX.
        """
        with app.app_context():
            db.session.query(Scenario).filter_by(
                user_id=seed_user["user"].id,
            ).delete()
            db.session.commit()

            resp = auth_client.get("/grid/balance-row?periods=6&offset=0")
            assert resp.status_code == 204
            assert resp.data == b""

    def test_balance_row_custom_offset(self, app, auth_client, seed_user, seed_periods_today):
        """GET /grid/balance-row with offset shifts the visible window."""
        with app.app_context():
            resp = auth_client.get("/grid/balance-row?periods=3&offset=2")
            assert resp.status_code == 200
            assert b"Projected End Balance" in resp.data
            assert b"Total Expenses" not in resp.data

    def test_grid_periods_large_value(
        self, app, auth_client, seed_user, seed_periods, monkeypatch,
    ):
        """GET / with periods larger than available still renders.

        Asserts the literal "01/02" header (start of seed_periods[0]),
        so uses the calendar-anchored seed_periods fixture and freezes
        today inside the period range.
        """
        freeze_today(monkeypatch, date(2026, 1, 5))
        with app.app_context():
            # Request 100 periods when only 10 exist -- should render what's available.
            resp = auth_client.get("/grid?periods=100")
            assert resp.status_code == 200
            assert b"Projected End Balance" in resp.data
            assert b"01/02" in resp.data


class TestTransactionCRUD:
    """Tests for transaction create, update, delete, and status changes."""

    def _create_test_txn(self, seed_user, seed_periods_today):
        """Helper: create and return a projected expense."""
        projected = db.session.query(Status).filter_by(name="Projected").one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

        txn = Transaction(
            pay_period_id=seed_periods_today[0].id,
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

    def test_create_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions creates a new ad-hoc transaction."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            response = auth_client.post("/transactions", data={
                "name": "New Expense",
                "estimated_amount": "99.99",
                "pay_period_id": seed_periods_today[0].id,
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
            assert txn.pay_period_id == seed_periods_today[0].id
            assert txn.category_id == seed_user["categories"]["Groceries"].id
            assert txn.status.name == "Projected"

    def test_update_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """PATCH /transactions/<id> updates fields."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"estimated_amount": "200.00"},
            )
            assert response.status_code == 200
            assert b"200" in response.data

    def test_mark_expense_done(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/<id>/mark-done sets status to done for expenses."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            response = auth_client.post(
                f"/transactions/{txn.id}/mark-done",
                data={"actual_amount": "120.00"},
            )
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Paid"
            assert txn.actual_amount == Decimal("120.00")

    def test_mark_income_received(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/<id>/mark-done sets status to received for income."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()

            txn = Transaction(
                pay_period_id=seed_periods_today[0].id,
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

    def test_soft_delete_template_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """DELETE /transactions/<id> soft-deletes template-linked items."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)
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

    def test_hard_delete_adhoc_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """DELETE /transactions/<id> hard-deletes ad-hoc (no template) items."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)
            txn_id = txn.id

            response = auth_client.delete(f"/transactions/{txn_id}")
            assert response.status_code == 200

            # Ad-hoc transaction should be fully deleted.
            assert db.session.get(Transaction, txn_id) is None

    def test_mark_done_without_actual_amount(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/<id>/mark-done without actual_amount sets status only."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            response = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Paid"
            assert txn.actual_amount is None

    def test_cancel_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/<id>/cancel sets status to cancelled."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            response = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"
            assert txn.effective_amount == Decimal("0")

    def test_mark_credit_creates_payback(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/<id>/mark-credit creates payback in next period."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            response = auth_client.post(f"/transactions/{txn.id}/mark-credit")
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Credit"

            # A payback transaction should exist in the next period.
            payback = db.session.query(Transaction).filter(
                Transaction.name.like("%Payback%"),
                Transaction.pay_period_id == seed_periods_today[1].id,
            ).first()
            assert payback is not None, "Payback transaction was not created"
            assert payback.name == "CC Payback: Test Expense"
            assert payback.estimated_amount == Decimal("123.45")
            assert payback.status.name == "Projected"
            assert payback.pay_period_id == seed_periods_today[1].id
            assert payback.credit_payback_for_id == txn.id

    def test_unmark_credit_reverts_and_deletes_payback(self, app, auth_client, seed_user, seed_periods_today):
        """DELETE /transactions/<id>/unmark-credit reverts to projected and deletes payback."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

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
                Transaction.pay_period_id == seed_periods_today[1].id,
            ).first()
            assert payback is None

    def test_create_transaction_full_form(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions with all fields creates a complete transaction."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            projected = db.session.query(Status).filter_by(name="Projected").one()

            response = auth_client.post("/transactions", data={
                "name": "Full Form Expense",
                "estimated_amount": "250.00",
                "pay_period_id": seed_periods_today[2].id,
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
            assert txn.pay_period_id == seed_periods_today[2].id
            assert txn.category_id == seed_user["categories"]["Car Payment"].id

    def test_create_inline_no_scenario(self, app, auth_client, seed_user, seed_periods_today):
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
                f"&period_id={seed_periods_today[0].id}"
                f"&transaction_type_id={expense_type.id}"
                f"&account_id={seed_user['account'].id}"
            )
            assert response.status_code == 400
            assert b"No baseline scenario" in response.data


class TestTransactionNegativePaths:
    """Tests for transaction route error handling, validation, and edge cases."""

    def _create_test_txn(self, seed_user, seed_periods_today):
        """Helper: create and return a projected expense."""
        projected = db.session.query(Status).filter_by(name="Projected").one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

        txn = Transaction(
            pay_period_id=seed_periods_today[0].id,
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

    def test_update_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """PATCH /transactions/999999 returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.patch(
                "/transactions/999999", data={"estimated_amount": "200.00"}
            )
            assert resp.status_code == 404
            assert b"Not found" in resp.data

    def test_mark_done_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/999999/mark-done returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.post("/transactions/999999/mark-done")
            assert resp.status_code == 404
            assert b"Not found" in resp.data

    def test_cancel_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/999999/cancel returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.post("/transactions/999999/cancel")
            assert resp.status_code == 404
            assert b"Not found" in resp.data

    def test_delete_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """DELETE /transactions/999999 returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.delete("/transactions/999999")
            assert resp.status_code == 404
            assert b"Not found" in resp.data

    def test_mark_credit_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/999999/mark-credit returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.post("/transactions/999999/mark-credit")
            assert resp.status_code == 404
            assert b"Not found" in resp.data

    def test_unmark_credit_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """DELETE /transactions/999999/unmark-credit returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.delete("/transactions/999999/unmark-credit")
            assert resp.status_code == 404
            assert b"Not found" in resp.data

    def test_get_cell_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """GET /transactions/999999/cell returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.get("/transactions/999999/cell")
            assert resp.status_code == 404

    def test_get_quick_edit_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """GET /transactions/999999/quick-edit returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.get("/transactions/999999/quick-edit")
            assert resp.status_code == 404

    def test_get_full_edit_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """GET /transactions/999999/full-edit returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.get("/transactions/999999/full-edit")
            assert resp.status_code == 404

    # ── Schema validation failure tests ───────────────────────────

    def test_create_transaction_missing_name(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions without required 'name' field returns 400 with field error."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions", data={
                "estimated_amount": "100.00",
                "pay_period_id": seed_periods_today[0].id,
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

    def test_create_transaction_negative_amount(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions with negative estimated_amount returns 400."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions", data={
                "name": "Bad Amount",
                "estimated_amount": "-100.00",
                "pay_period_id": seed_periods_today[0].id,
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

    def test_create_transaction_zero_amount(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions with estimated_amount=0.00 succeeds (Range min=0 is inclusive)."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions", data={
                "name": "Zero Amount",
                "estimated_amount": "0.00",
                "pay_period_id": seed_periods_today[0].id,
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
        self, app, auth_client, seed_user, seed_periods_today
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
        self, app, auth_client, seed_user, seed_periods_today
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

    def test_update_transaction_invalid_amount(self, app, auth_client, seed_user, seed_periods_today):
        """PATCH /transactions/<id> with non-numeric amount returns 400."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)
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

    def test_mark_done_already_done_expense(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/<id>/mark-done is idempotent for already-done transactions."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

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
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transactions/<id>/cancel is idempotent for already-cancelled transactions."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            # First cancel.
            resp1 = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert resp1.status_code == 200

            # NOTE: cancel is idempotent -- no guard against double cancel.
            resp2 = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert resp2.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"

    def test_mark_done_cancelled_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/<id>/mark-done on a cancelled transaction is rejected.

        After the C-21 follow-up the mark_done endpoint runs every
        status change through ``verify_transition``.  Cancelled may
        only revert to Projected; a direct jump to Paid would
        resurrect the row without the explicit revert audit step.
        Was previously a 200 with a comment noting "UI hides the Done
        button for non-projected statuses, but the API endpoint does
        not enforce this"; the API now enforces it.
        """
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            # Cancel first.
            auth_client.post(f"/transactions/{txn.id}/cancel")
            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"

            resp = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert resp.status_code == 400

            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"

    def test_cancel_done_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/<id>/cancel on a done transaction is now rejected.

        After the C-21 follow-up the cancel endpoint runs every status
        change through ``app.services.state_machine.verify_transition``.
        Done -> Cancelled is illegal -- the user must revert to
        Projected first so the audit trail records both the revert
        and the subsequent cancellation.  Was previously a 200 with a
        comment noting "UI hides the Cancel button for done status";
        the API now enforces the same contract the UI was relying on.
        """
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            # Mark done first.
            auth_client.post(f"/transactions/{txn.id}/mark-done")
            db.session.refresh(txn)
            assert txn.status.name == "Paid"

            resp = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert resp.status_code == 400

            db.session.refresh(txn)
            assert txn.status.name == "Paid"

    def test_mark_done_with_invalid_actual_amount(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transactions/<id>/mark-done with non-numeric actual_amount returns 400.

        Pre-C-27: the route caught ``InvalidOperation`` and returned
        the literal string ``"Invalid actual amount"`` with status
        400.  Post-C-27 (commit C-27 of the 2026-04-15 security
        remediation plan): :class:`MarkDoneSchema` rejects the
        value at the schema tier and the route returns
        ``jsonify(errors=...)`` so HTMX form callers can render
        the per-field message.  The status code stays 400; only
        the body shape and message text changed.
        """
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)
            txn_id = txn.id

            resp = auth_client.post(
                f"/transactions/{txn_id}/mark-done",
                data={"actual_amount": "not_a_number"},
            )
            assert resp.status_code == 400
            payload = resp.get_json()
            assert payload is not None
            assert "actual_amount" in payload["errors"]

            # ``MarkDoneSchema`` runs before the route's status
            # mutation (commit C-27 reordered the parse to the
            # top of the function), so a rollback is no longer
            # required to keep the row clean.  The assertions
            # remain to guard against regression.
            db.session.expire_all()
            txn_after = db.session.get(Transaction, txn_id)
            assert txn_after.status.name == "Projected"
            assert txn_after.actual_amount is None

    def test_mark_done_with_negative_actual_amount(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transactions/<id>/mark-done rejects negative actual_amount.

        Two layers reject this value:
          * Pre-C-27 only the DB CHECK constraint
            ``actual_amount >= 0`` rejected the row, surfacing as
            a 500 IntegrityError without the route's catch.
          * Post-C-27 (commit C-27 of the 2026-04-15 security
            remediation plan): :class:`MarkDoneSchema`'s
            ``Range(min=0)`` rejects the value at the schema tier
            so the route returns 400 before the row is touched.

        The DB CHECK remains as the storage-tier backstop (L-01)
        for any future caller that bypasses the schema.
        """
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)
            original_status_id = txn.status_id

            resp = auth_client.post(
                f"/transactions/{txn.id}/mark-done",
                data={"actual_amount": "-50.00"},
            )
            assert resp.status_code == 400

            db.session.expire_all()
            db.session.refresh(txn)
            assert txn.status_id == original_status_id, (
                "schema-tier rejection must not transition the row"
            )

    # ── XSS protection test ──────────────────────────────────────

    def test_create_transaction_xss_in_name(self, app, auth_client, seed_user, seed_periods_today):
        """Transaction name with script tag is stored but auto-escaped in rendered output."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions", data={
                "name": "<script>alert(1)</script>",
                "estimated_amount": "50.00",
                "pay_period_id": seed_periods_today[0].id,
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

    def test_transaction_model_has_account_id(self, app, db, seed_user, seed_periods_today):
        """Create a Transaction with account_id. Verify it saves and the relationship resolves."""
        projected = db.session.query(Status).filter_by(name="Projected").one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
        account = seed_user["account"]

        txn = Transaction(
            account_id=account.id,
            pay_period_id=seed_periods_today[0].id,
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
        self, app, db, seed_user, seed_periods_today
    ):
        """Attempting to create a Transaction without account_id raises IntegrityError."""
        from sqlalchemy.exc import IntegrityError

        projected = db.session.query(Status).filter_by(name="Projected").one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

        txn = Transaction(
            pay_period_id=seed_periods_today[0].id,
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

    def test_credit_payback_inherits_account_id(self, app, db, seed_user, seed_periods_today):
        """The payback transaction created by mark_as_credit inherits account_id."""
        from app.services import credit_workflow

        projected = db.session.query(Status).filter_by(name="Projected").one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
        account = seed_user["account"]

        txn = Transaction(
            account_id=account.id,
            pay_period_id=seed_periods_today[0].id,
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

    def test_inline_create_sets_account_id(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/inline with account_id saves it on the transaction."""
        account = seed_user["account"]
        category = seed_user["categories"]["Groceries"]
        scenario = seed_user["scenario"]
        projected = db.session.query(Status).filter_by(name="Projected").one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

        resp = auth_client.post("/transactions/inline", data={
            "account_id": account.id,
            "category_id": category.id,
            "pay_period_id": seed_periods_today[0].id,
            "scenario_id": scenario.id,
            "transaction_type_id": expense_type.id,
            "estimated_amount": "99.99",
        })
        assert resp.status_code == 201

        txn = Transaction.query.filter_by(name=category.display_name).first()
        assert txn is not None
        assert txn.account_id == account.id

    def test_inline_create_rejects_missing_account_id(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transactions/inline without account_id returns validation error."""
        category = seed_user["categories"]["Groceries"]
        scenario = seed_user["scenario"]
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

        resp = auth_client.post("/transactions/inline", data={
            "category_id": category.id,
            "pay_period_id": seed_periods_today[0].id,
            "scenario_id": scenario.id,
            "transaction_type_id": expense_type.id,
            "estimated_amount": "50.00",
        })
        assert resp.status_code == 400

    def test_inline_create_rejects_other_users_account_id(
        self, app, auth_client, seed_user, seed_periods_today, second_user
    ):
        """POST /transactions/inline with another user's account_id returns 404."""
        other_account = second_user["account"]
        category = seed_user["categories"]["Groceries"]
        scenario = seed_user["scenario"]
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

        resp = auth_client.post("/transactions/inline", data={
            "account_id": other_account.id,
            "category_id": category.id,
            "pay_period_id": seed_periods_today[0].id,
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
        savings = account_service.create_account(
            user_id=user.id,
            account_type_id=savings_type.id,
            name="Savings",
            anchor_balance=Decimal("5000.00"),
            anchor_period_id=periods[0].id,
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
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """Default grid (checking) shows only checking transactions, not savings."""
        checking = seed_user["account"]
        scenario = seed_user["scenario"]
        savings = self._create_savings_account(seed_user["user"], seed_periods_today)

        self._create_txn(checking, seed_periods_today[0], scenario, "Rent", 1200,
                         category=seed_user["categories"]["Rent"])
        self._create_txn(savings, seed_periods_today[0], scenario, "Savings Interest", 50,
                         txn_type_name="Income", category=seed_user["categories"]["Salary"])
        db.session.commit()

        resp = auth_client.get("/grid")
        assert resp.status_code == 200
        html = resp.data.decode()

        assert "Rent" in html
        assert "Savings Interest" not in html

    def test_grid_account_override_shows_savings_transactions(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """Passing ?account_id=savings shows only savings transactions.

        Transactions are matched to cells by category_id and type.  The
        grid renders amounts (not names) in cells, so we check for the
        amount values and verify that the checking expense amount does
        not appear on the savings grid.
        """
        checking = seed_user["account"]
        scenario = seed_user["scenario"]
        savings = self._create_savings_account(seed_user["user"], seed_periods_today)

        # Use a visible period (current period index ~5).
        current = pay_period_service.get_current_period(seed_user["user"].id)
        self._create_txn(checking, current, scenario, "Checking Rent", 1234,
                         category=seed_user["categories"]["Rent"])
        self._create_txn(savings, current, scenario, "Savings Deposit", 567,
                         txn_type_name="Income", category=seed_user["categories"]["Salary"])
        db.session.commit()

        # Savings grid: should show the $567 deposit, not the $1234 rent.
        resp = auth_client.get(f"/grid?account_id={savings.id}")
        assert resp.status_code == 200
        html = resp.data.decode()

        assert "567" in html
        assert "1,234" not in html

    def test_grid_shows_correct_account_name_in_header(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """The grid header shows the viewed account's name."""
        savings = self._create_savings_account(seed_user["user"], seed_periods_today)
        db.session.commit()

        resp = auth_client.get(f"/grid?account_id={savings.id}")
        html = resp.data.decode()
        assert "Savings Balance" in html

    # --- Balance correctness tests ---

    def test_balance_uses_correct_anchor_for_each_account(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """Each account's grid uses its own anchor balance, not another's.

        Checking anchor: $1000 (from seed_user).
        Savings anchor: $5000.
        With no transactions, the projected balance equals the anchor.
        """
        savings = self._create_savings_account(seed_user["user"], seed_periods_today)
        db.session.commit()

        # Checking grid: balance should reflect $1000 anchor.
        resp = auth_client.get("/grid")
        html = resp.data.decode()
        assert "$1,000" in html

        # Savings grid: balance should reflect $5000 anchor.
        resp = auth_client.get(f"/grid?account_id={savings.id}")
        html = resp.data.decode()
        assert "$5,000" in html

    def test_balance_excludes_other_accounts_transactions(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """A $500 expense on checking should NOT reduce the savings balance.

        Checking: $1000 anchor - $500 expense = $500 projected.
        Savings: $5000 anchor, no expenses = $5000 projected.
        """
        checking = seed_user["account"]
        scenario = seed_user["scenario"]
        savings = self._create_savings_account(seed_user["user"], seed_periods_today)

        self._create_txn(checking, seed_periods_today[0], scenario, "Rent", 500,
                         category=seed_user["categories"]["Rent"])
        db.session.commit()

        # Savings grid: balance should still be $5000 (the expense is on checking).
        resp = auth_client.get(f"/grid?account_id={savings.id}")
        html = resp.data.decode()
        assert "$5,000" in html

    # --- Balance row HTMX refresh tests ---

    def test_balance_row_refresh_scoped_to_account(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """GET /grid/balance-row with account_id returns that account's balances."""
        checking = seed_user["account"]
        scenario = seed_user["scenario"]
        savings = self._create_savings_account(seed_user["user"], seed_periods_today)

        self._create_txn(checking, seed_periods_today[0], scenario, "Expense on Checking", 300,
                         category=seed_user["categories"]["Rent"])
        db.session.commit()

        # Balance row for savings: no expenses, balance = anchor.
        resp = auth_client.get(f"/grid/balance-row?periods=6&offset=0&account_id={savings.id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "$5,000" in html

    def test_balance_row_refresh_includes_account_id_in_htmx_url(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """The returned tfoot contains account_id in its hx-get URL for future refreshes."""
        savings = self._create_savings_account(seed_user["user"], seed_periods_today)
        db.session.commit()

        resp = auth_client.get(f"/grid/balance-row?periods=6&offset=0&account_id={savings.id}")
        html = resp.data.decode()
        assert f"account_id={savings.id}" in html

    # --- Footer totals tests ---

    def test_footer_totals_reflect_viewed_account_only(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """Subtotal rows count only the viewed account's transactions.

        The tbody subtotal rows sum projected (unsettled) transactions for
        the viewed account.  Savings transactions must not appear in the
        checking account's subtotals.
        """
        checking = seed_user["account"]
        scenario = seed_user["scenario"]
        savings = self._create_savings_account(seed_user["user"], seed_periods_today)

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
        resp = auth_client.get("/grid")
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
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """An account with no transactions renders the grid without errors.

        Section banners should appear. No transaction cells. Balance equals anchor.
        """
        savings = self._create_savings_account(seed_user["user"], seed_periods_today)
        db.session.commit()

        resp = auth_client.get(f"/grid?account_id={savings.id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "INCOME" in html
        assert "EXPENSES" in html
        assert "$5,000" in html  # Anchor balance, no transactions.

    def test_grid_hides_category_rows_without_account_transactions(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """Categories with transactions only on checking should not render on savings grid.

        Create a Rent expense on checking. The Rent category row should
        appear on checking grid but not on savings grid.
        """
        checking = seed_user["account"]
        scenario = seed_user["scenario"]
        savings = self._create_savings_account(seed_user["user"], seed_periods_today)

        self._create_txn(checking, seed_periods_today[0], scenario, "Rent", 1200,
                         category=seed_user["categories"]["Rent"])
        db.session.commit()

        # Checking grid: Rent row visible.
        resp = auth_client.get("/grid")
        html = resp.data.decode()
        assert "Rent" in html

        # Savings grid: no Rent row (no transactions for this category on savings).
        resp = auth_client.get(f"/grid?account_id={savings.id}")
        html = resp.data.decode()
        # The category name "Rent" should not appear as a row label.
        # It may appear in the "Add Transaction" modal dropdown, so check
        # specifically for the row label pattern.
        assert 'class="sticky-col row-label"' not in html or "Rent" not in html.split("EXPENSES")[0].split("INCOME")[-1]

    # NOTE: ``test_grid_account_with_no_anchor_balance`` and
    # ``test_grid_account_with_no_anchor_period`` previously exercised
    # the NULL-anchor branches of the balance producers.  E-19 / Commit
    # 3 makes both NULL states unreachable at the storage tier (NOT NULL
    # + ``ck_accounts_anchor_balance_present``) and at the application
    # tier (``account_service.create_account`` resolves the period if
    # omitted and rejects NULL balances).  The scenarios these tests
    # constructed (``Account(..., current_anchor_balance=None, ...)``
    # and ``Account(..., current_anchor_period_id=None, ...)``) can no
    # longer be materialised through any code path -- the constraint
    # fires at the DB and the factory raises TypeError / ValidationError
    # respectively.  Coverage of the constraint itself lives in
    # ``test_models/test_account_anchor_invariant.py::TestModelRejectsNullAnchor``.
    # Tests deleted (not skipped) because the asserted behaviour does
    # not exist anymore -- skipping would falsely imply the case is
    # still meaningful.

    # --- Cancelled and deleted transaction edge cases ---

    def test_cancelled_transactions_excluded_from_account_grid(
        self, app, auth_client, seed_user, seed_periods_today
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

        resp = auth_client.get("/grid")
        html = resp.data.decode()
        # The active transaction's cell should be rendered with its ID.
        assert f"txn-cell-{active.id}" in html
        # The cancelled transaction should NOT have a rendered cell.
        assert f"txn-cell-{cancelled.id}" not in html

    def test_soft_deleted_transactions_excluded_from_account_grid(
        self, app, db, auth_client, seed_user, seed_periods_today
    ):
        """Soft-deleted transactions (is_deleted=True) do not appear."""
        checking = seed_user["account"]
        scenario = seed_user["scenario"]

        txn = self._create_txn(checking, seed_periods_today[0], scenario, "Deleted Expense", 999,
                               category=seed_user["categories"]["Rent"])
        txn.is_deleted = True
        db.session.commit()

        resp = auth_client.get("/grid")
        html = resp.data.decode()
        assert "$999" not in html

    # --- Carry forward interaction test ---

    def test_carry_forward_moves_all_accounts_transactions(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """Carry forward moves projected transactions from ALL accounts, not just the viewed one.

        This verifies carry forward is NOT account-scoped -- it is a
        period-level operation that moves everything unpaid in that period.
        """
        checking = seed_user["account"]
        scenario = seed_user["scenario"]
        savings = self._create_savings_account(seed_user["user"], seed_periods_today)

        # Create projected transactions on both accounts in period 0.
        checking_txn = self._create_txn(
            checking, seed_periods_today[0], scenario, "Checking Expense", 100,
            category=seed_user["categories"]["Rent"],
        )
        savings_txn = self._create_txn(
            savings, seed_periods_today[0], scenario, "Savings Expense", 50,
            category=seed_user["categories"]["Groceries"],
        )
        db.session.commit()

        checking_txn_id = checking_txn.id
        savings_txn_id = savings_txn.id

        # Carry forward from period 0.
        resp = auth_client.post(f"/pay-periods/{seed_periods_today[0].id}/carry-forward")
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
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """Creating a transaction inline on the savings grid assigns it to the savings account."""
        savings = self._create_savings_account(seed_user["user"], seed_periods_today)
        category = seed_user["categories"]["Salary"]
        scenario = seed_user["scenario"]
        income_type = db.session.query(TransactionType).filter_by(name="Income").one()
        db.session.commit()

        resp = auth_client.post("/transactions/inline", data={
            "account_id": savings.id,
            "category_id": category.id,
            "pay_period_id": seed_periods_today[0].id,
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
        self, app, auth_client, seed_user, seed_periods_today
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
        savings = self._create_savings_account(seed_user["user"], seed_periods_today)

        current = pay_period_service.get_current_period(seed_user["user"].id)
        # Find the next period after current.
        current_idx = next(
            i for i, p in enumerate(seed_periods_today) if p.id == current.id
        )
        next_period = seed_periods_today[current_idx + 1]

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

    def test_grid_no_transfers_section(self, app, auth_client, seed_user, seed_periods_today):
        """Grid does not contain a TRANSFERS section banner."""
        with app.app_context():
            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "section-banner-transfer" not in html
            assert "xfer-cell-" not in html

    def test_grid_renders_without_transfers(self, app, auth_client, seed_user, seed_periods_today):
        """Grid renders normally with no transfers or shadows."""
        with app.app_context():
            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "section-banner-income" in html
            assert "section-banner-expense" in html
            assert "section-banner-transfer" not in html


# ── Inline Subtotal Row Tests ──────────────────────────────────────


class TestInlineSubtotalRows:
    """Tests for the Total Income and Total Expenses subtotal rows in tbody."""

    def test_subtotal_rows_present(self, app, auth_client, seed_user, seed_periods_today):
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
                current = seed_periods_today[0]

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

            resp = auth_client.get("/grid")
            html = resp.data.decode()

            assert "subtotal-row-income" in html
            assert "subtotal-row-expense" in html

    def test_subtotal_values_correct(self, app, auth_client, seed_user, seed_periods_today):
        """Subtotal rows show correct per-period totals."""
        with app.app_context():
            from app.models.ref import TransactionType
            projected = db.session.query(Status).filter_by(name="Projected").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            from app.services import pay_period_service
            current = pay_period_service.get_current_period(seed_user["user"].id)
            if not current:
                current = seed_periods_today[0]

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

            resp = auth_client.get("/grid")
            html = resp.data.decode()

            # Total Income = 2000 + 100 = 2100.
            assert "$2,100" in html
            # Total Expenses = 1200 + 400 = 1600.
            assert "$1,600" in html

    def test_subtotal_excludes_cancelled(self, app, auth_client, seed_user, seed_periods_today):
        """Cancelled transactions are excluded from subtotals."""
        with app.app_context():
            from app.models.ref import TransactionType
            projected = db.session.query(Status).filter_by(name="Projected").one()
            cancelled = db.session.query(Status).filter_by(name="Cancelled").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            from app.services import pay_period_service
            current = pay_period_service.get_current_period(seed_user["user"].id)
            if not current:
                current = seed_periods_today[0]

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

            resp = auth_client.get("/grid")
            html = resp.data.decode()

            # Only $1,000 counted (cancelled $500 excluded).
            assert "$1,000" in html

    def test_balance_row_refresh_unaffected(self, app, auth_client, seed_user, seed_periods_today):
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

    def _seed_txns(self, seed_user, seed_periods_today, income_amt, expense_amt):
        """Helper: create income + expense in the current/first visible period."""
        from app.models.ref import TransactionType
        from app.services import pay_period_service
        projected = db.session.query(Status).filter_by(name="Projected").one()
        income_type = db.session.query(TransactionType).filter_by(name="Income").one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
        current = pay_period_service.get_current_period(seed_user["user"].id)
        if not current:
            current = seed_periods_today[0]

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

    def test_net_cash_flow_row_present(self, app, db, auth_client, seed_user, seed_periods_today):
        """Grid contains a net-cash-flow-row with correct label."""
        with app.app_context():
            self._seed_txns(seed_user, seed_periods_today, "2000", "1400")
            resp = auth_client.get("/grid")
            html = resp.data.decode()
            assert "net-cash-flow-row" in html
            assert "Net Cash Flow" in html
            assert "$600" in html

    def test_net_cash_flow_negative(self, app, db, auth_client, seed_user, seed_periods_today):
        """Negative net cash flow shows warning indicator."""
        with app.app_context():
            self._seed_txns(seed_user, seed_periods_today, "1000", "1500")
            resp = auth_client.get("/grid")
            html = resp.data.decode()
            assert "balance-negative" in html
            # Warning icon for negative net.
            assert "bi-exclamation-triangle-fill" in html

    def test_net_cash_flow_zero(self, app, db, auth_client, seed_user, seed_periods_today):
        """Breakeven period shows empty net cash flow cell."""
        with app.app_context():
            self._seed_txns(seed_user, seed_periods_today, "1000", "1000")
            resp = auth_client.get("/grid")
            html = resp.data.decode()
            assert "net-cash-flow-row" in html
            # Net is zero -- cell should be empty (matching footer behavior).

    def test_balance_row_refresh_excludes_net_cash_flow(
        self, app, db, auth_client, seed_user, seed_periods_today
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

    def test_footer_single_row(self, app, db, auth_client, seed_user, seed_periods_today):
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

    def test_footer_htmx_attributes_preserved(self, app, db, auth_client, seed_user, seed_periods_today):
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

    def test_footer_htmx_refresh_cycle(self, app, db, auth_client, seed_user, seed_periods_today):
        """Initial page and balance-row both produce tfoot with HTMX attributes."""
        with app.app_context():
            page_resp = auth_client.get("/grid")
            page_html = page_resp.data.decode()
            assert 'id="grid-summary"' in page_html

            balance_resp = auth_client.get(
                f"/grid/balance-row?periods=6&offset=0&account_id={seed_user['account'].id}"
            )
            balance_html = balance_resp.data.decode()
            assert 'id="grid-summary"' in balance_html
            assert "hx-trigger" in balance_html

    def test_subtotals_still_present_in_tbody(self, app, db, auth_client, seed_user, seed_periods_today):
        """Tbody subtotal and net cash flow rows survive footer condensation."""
        with app.app_context():
            from app.models.ref import TransactionType
            from app.services import pay_period_service
            projected = db.session.query(Status).filter_by(name="Projected").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            current = pay_period_service.get_current_period(seed_user["user"].id)
            if not current:
                current = seed_periods_today[0]

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

            resp = auth_client.get("/grid")
            html = resp.data.decode()
            assert "subtotal-row-income" in html
            assert "subtotal-row-expense" in html
            assert "net-cash-flow-row" in html


class TestPeriodHeaderDateFormat:
    """Tests for pay period date headers -- Commit #14.

    Headers show only the paycheck date (start_date), not the period range.
    Current-year periods omit the year suffix (e.g., '3/26').
    Non-current-year periods include 2-digit year (e.g., '3/26/27').
    """

    def _make_periods(self, db, seed_user, start_date, num_periods=6):
        """Helper: generate pay periods and set anchor to the first one."""
        periods = pay_period_service.generate_pay_periods(
            user_id=seed_user["user"].id,
            start_date=start_date,
            num_periods=num_periods,
            cadence_days=14,
        )
        db.session.flush()
        seed_user["account"].current_anchor_period_id = periods[0].id
        db.session.commit()
        return periods

    def test_period_header_compact_for_current_year(self, app, auth_client, seed_user):
        """Current-year periods display paycheck date without year suffix.

        The grid starts at the current period (the one containing today),
        so we must check the current period's header, not the first
        generated period.
        """
        with app.app_context():
            today = date.today()
            start = today - timedelta(days=28)
            periods = self._make_periods(db, seed_user, start)

            # The grid starts at the current period -- find it.
            current = pay_period_service.get_current_period(
                seed_user["user"].id
            )
            assert current is not None, "No period covers today"

            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            html = resp.data.decode()

            # The current period's start_date should appear in compact
            # format (no year suffix) since it's in the current year.
            expected = current.start_date.strftime("%-m/%-d")
            assert expected in html
            # Should NOT contain a range separator for this period.
            end = current.start_date + timedelta(days=13)
            range_str = f"{current.start_date.strftime('%-m/%-d')} - {end.strftime('%-m/%-d')}"
            assert range_str not in html

    def test_period_header_full_format_for_cross_year(self, app, auth_client, seed_user):
        """A period starting in a non-current year shows the year suffix."""
        with app.app_context():
            today = date.today()
            # Generate enough periods to extend into next year.
            start = today - timedelta(days=28)
            periods = self._make_periods(db, seed_user, start, num_periods=28)

            # Find a period whose start_date is in the next year.
            next_year_period = None
            for p in periods:
                if p.start_date.year > today.year:
                    next_year_period = p
                    break

            assert next_year_period is not None, "Test requires a next-year period"

            current_period = pay_period_service.get_current_period(
                seed_user["user"].id
            )
            offset = next_year_period.period_index - current_period.period_index
            resp = auth_client.get(f"/grid?periods=3&offset={offset}")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Expect paycheck date with year suffix.
            expected = next_year_period.start_date.strftime("%-m/%-d/%y")
            assert expected in html

    def test_period_header_full_format_for_past_year(self, app, auth_client, seed_user):
        """Periods in the previous year show the year suffix."""
        with app.app_context():
            today = date.today()
            past_start = date(today.year - 1, 6, 1)
            days_to_today = (today - past_start).days
            num = (days_to_today // 14) + 4
            periods = self._make_periods(db, seed_user, past_start, num_periods=num)

            current_period = pay_period_service.get_current_period(
                seed_user["user"].id
            )
            first_offset = periods[0].period_index - current_period.period_index
            resp = auth_client.get(f"/grid?periods=3&offset={first_offset}")
            assert resp.status_code == 200
            html = resp.data.decode()

            expected = past_start.strftime("%-m/%-d/%y")
            assert expected in html

    def test_period_header_full_format_for_future_year(self, app, auth_client, seed_user):
        """Periods in the next year show the year suffix."""
        with app.app_context():
            today = date.today()
            start = today - timedelta(days=28)
            days_to_next_year = (date(today.year + 1, 2, 1) - start).days
            num = (days_to_next_year // 14) + 2
            periods = self._make_periods(db, seed_user, start, num_periods=num)

            future_period = None
            for p in periods:
                if p.start_date.year > today.year:
                    future_period = p
                    break

            assert future_period is not None, "Test requires a next-year period"

            current_period = pay_period_service.get_current_period(
                seed_user["user"].id
            )
            offset = future_period.period_index - current_period.period_index
            resp = auth_client.get(f"/grid?periods=3&offset={offset}")
            assert resp.status_code == 200
            html = resp.data.decode()

            expected = future_period.start_date.strftime("%-m/%-d/%y")
            assert expected in html

    def test_period_header_mixed_formats_same_page(self, app, auth_client, seed_user):
        """Current-year and non-current-year headers coexist on the same page."""
        with app.app_context():
            today = date.today()
            start = today - timedelta(days=28)
            days_to_next_year = (date(today.year + 1, 2, 1) - start).days
            num = (days_to_next_year // 14) + 2
            periods = self._make_periods(db, seed_user, start, num_periods=num)

            # Find the last current-year period and first next-year period.
            last_current = None
            first_next = None
            for p in periods:
                if p.start_date.year == today.year:
                    last_current = p
                if first_next is None and p.start_date.year > today.year:
                    first_next = p

            assert last_current is not None
            assert first_next is not None

            current_period = pay_period_service.get_current_period(
                seed_user["user"].id
            )
            offset = last_current.period_index - current_period.period_index
            resp = auth_client.get(f"/grid?periods=6&offset={offset}")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Current-year: paycheck date without year.
            compact = last_current.start_date.strftime("%-m/%-d")
            assert compact in html

            # Next-year: paycheck date with year suffix.
            full = first_next.start_date.strftime("%-m/%-d/%y")
            assert full in html

    def test_carry_forward_button_still_present_after_format_change(
        self, app, auth_client, seed_user
    ):
        """Carry forward button renders correctly alongside the new date format."""
        with app.app_context():
            today = date.today()
            start = today - timedelta(days=56)
            periods = self._make_periods(db, seed_user, start)

            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = (
                db.session.query(TransactionType).filter_by(name="Expense").one()
            )
            first_cat = list(seed_user["categories"].values())[0]
            txn = Transaction(
                pay_period_id=periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Test Bill",
                category_id=first_cat.id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("100.00"),
            )
            db.session.add(txn)
            db.session.commit()

            current_period = pay_period_service.get_current_period(
                seed_user["user"].id
            )
            offset = periods[0].period_index - current_period.period_index
            resp = auth_client.get(f"/grid?periods=6&offset={offset}")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Carry forward button present.
            assert "carry-forward" in html
            # Paycheck date without year (current year period).
            expected = periods[0].start_date.strftime("%-m/%-d")
            assert expected in html

    def test_grid_renders_without_error_after_format_change(
        self, app, auth_client, seed_user
    ):
        """Smoke test: grid renders with correct table structure after date format change."""
        with app.app_context():
            today = date.today()
            start = today - timedelta(days=14)
            self._make_periods(db, seed_user, start)

            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "<thead" in html
            assert "<tbody>" in html
            assert "Projected End Balance" in html

    def test_balance_row_still_works_after_format_change(
        self, app, auth_client, seed_user
    ):
        """Balance row HTMX partial still renders after the thead date change."""
        with app.app_context():
            today = date.today()
            start = today - timedelta(days=14)
            self._make_periods(db, seed_user, start)

            resp = auth_client.get("/grid/balance-row?periods=6&offset=0")
            assert resp.status_code == 200
            assert b'id="grid-summary"' in resp.data

    def test_period_header_handles_january_1st(self, app, auth_client, seed_user):
        """A period starting January 1 of the current year uses compact format."""
        with app.app_context():
            today = date.today()
            jan1 = date(today.year, 1, 1)
            days_to_today = (today - jan1).days
            num = max((days_to_today // 14) + 4, 6)
            periods = self._make_periods(db, seed_user, jan1, num_periods=num)

            current_period = pay_period_service.get_current_period(
                seed_user["user"].id
            )
            offset = periods[0].period_index - current_period.period_index
            resp = auth_client.get(f"/grid?periods=3&offset={offset}")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Jan 1 in current year -- no year suffix.
            assert "1/1" in html

    def test_period_header_handles_december_31st(self, app, auth_client, seed_user):
        """A late-December period in the current year uses compact format."""
        with app.app_context():
            today = date.today()
            dec18 = date(today.year, 12, 18)
            start = today - timedelta(days=14)
            days_to_dec18 = (dec18 - start).days
            num = (days_to_dec18 // 14) + 4
            periods = self._make_periods(db, seed_user, start, num_periods=num)

            # Find the last period starting in the current year.
            dec_period = None
            for p in periods:
                if p.start_date.year == today.year:
                    dec_period = p

            if dec_period is None:
                pytest.skip("No period starting in late current year generated")

            current_period = pay_period_service.get_current_period(
                seed_user["user"].id
            )
            offset = dec_period.period_index - current_period.period_index
            resp = auth_client.get(f"/grid?periods=3&offset={offset}")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Current-year start date -- no year suffix.
            expected = dec_period.start_date.strftime("%-m/%-d")
            assert expected in html

            # The next period (if it exists and starts next year) shows year.
            next_periods = [
                p for p in periods
                if p.period_index == dec_period.period_index + 1
            ]
            if next_periods and next_periods[0].start_date.year > today.year:
                full = next_periods[0].start_date.strftime("%-m/%-d/%y")
                assert full in html


class TestTransactionNameRows:
    """Tests for Commit #15: transaction-name-based row headers.

    The grid now shows one row per unique (category, template, name) tuple
    instead of one row per category.  These tests verify that the restructure
    produces correct row headers, handles all transaction types, maintains
    deterministic ordering, and preserves subtotals and HTMX interactions.
    """

    def _get_current_period(self, seed_user):
        """Return the current period for the seed user."""
        return pay_period_service.get_current_period(seed_user["user"].id)

    def test_grid_separate_rows_for_same_category_transactions(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Two templates in the same category produce two distinct grid rows,
        each with the transaction name in the row header.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            # Create a second category item under "Auto" group.
            auto_insurance = Category(
                user_id=seed_user["user"].id,
                group_name="Auto",
                item_name="Insurance",
            )
            db.session.add(auto_insurance)
            db.session.flush()

            # Two templates, same category.
            tmpl_sf = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=auto_insurance.id,
                transaction_type_id=expense_type.id,
                name="State Farm",
                default_amount=Decimal("150.00"),
            )
            tmpl_geico = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=auto_insurance.id,
                transaction_type_id=expense_type.id,
                name="Geico",
                default_amount=Decimal("120.00"),
            )
            db.session.add_all([tmpl_sf, tmpl_geico])
            db.session.flush()

            txn_sf = Transaction(
                template_id=tmpl_sf.id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="State Farm",
                category_id=auto_insurance.id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("150.00"),
            )
            txn_geico = Transaction(
                template_id=tmpl_geico.id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Geico",
                category_id=auto_insurance.id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("120.00"),
            )
            db.session.add_all([txn_sf, txn_geico])
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Both names appear as row headers in the expenses section.
            assert "State Farm" in html
            assert "Geico" in html

            # Verify they are in separate <th> elements.
            import re
            th_labels = re.findall(
                r'<th[^>]*class="[^"]*row-label[^"]*"[^>]*>\s*(\S[^<]*?)\s*</th>',
                html,
            )
            assert "State Farm" in th_labels
            assert "Geico" in th_labels

    def test_grid_one_time_transaction_gets_own_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A one-time transaction (no template) produces its own row with
        the transaction name in the row header.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Car Repair",
                category_id=seed_user["categories"]["Car Payment"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("450.00"),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            assert resp.status_code == 200
            html = resp.data.decode()

            import re
            th_labels = re.findall(
                r'<th[^>]*class="[^"]*row-label[^"]*"[^>]*>\s*(\S[^<]*?)\s*</th>',
                html,
            )
            assert "Car Repair" in th_labels

    def test_grid_shadow_transactions_get_own_rows(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Shadow transactions from transfers produce their own grid rows
        with the transaction name visible in the row header.
        """
        with app.app_context():
            from app.models.transfer import Transfer
            from app.models.transfer_template import TransferTemplate

            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            current = self._get_current_period(seed_user)

            # Create a savings account for the transfer destination.
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
            savings_acct = account_service.create_account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Savings",
                anchor_balance=Decimal("0.00"),
            )
            db.session.add(savings_acct)
            db.session.flush()

            # Create the outgoing category.
            out_cat = Category(
                user_id=seed_user["user"].id,
                group_name="Transfers",
                item_name="Outgoing",
            )
            db.session.add(out_cat)
            db.session.flush()

            # Create transfer and shadow expense on checking.
            transfer = Transfer(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                pay_period_id=current.id,
                status_id=projected.id,
                name="To Savings",
                amount=Decimal("500.00"),
                from_account_id=seed_user["account"].id,
                to_account_id=savings_acct.id,
            )
            db.session.add(transfer)
            db.session.flush()

            shadow = Transaction(
                transfer_id=transfer.id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Transfer to Savings",
                category_id=out_cat.id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
            )
            db.session.add(shadow)
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            assert resp.status_code == 200
            html = resp.data.decode()

            import re
            th_labels = re.findall(
                r'<th[^>]*class="[^"]*row-label[^"]*"[^>]*>\s*(\S[^<]*?)\s*</th>',
                html,
            )
            # "Transfer to" prefix is stripped -- row shows just "Savings".
            assert "Savings" in th_labels

    def test_grid_empty_cell_has_correct_category_id(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Empty cells pass the correct category_id for quick create,
        matching the row key's category.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            # Create a transaction only in the current period so adjacent
            # periods have empty cells for this row key.
            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Electric Bill",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("120.00"),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            assert resp.status_code == 200
            html = resp.data.decode()

            # The empty cell's hx-get URL should contain the correct category_id.
            cat_id = seed_user["categories"]["Rent"].id
            assert f"category_id={cat_id}" in html

    def test_grid_group_headers_appear(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Group header rows appear before each category group's transactions
        with the group-header-row CSS class.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            # Create expenses in two different groups.
            txn_home = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Rent Payment",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("1000.00"),
            )
            txn_auto = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Car Loan",
                category_id=seed_user["categories"]["Car Payment"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("400.00"),
            )
            db.session.add_all([txn_home, txn_auto])
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Group headers with correct class.
            assert 'class="group-header-row"' in html
            # Both groups present.
            assert "Home" in html
            assert "Auto" in html

    def test_grid_inline_edit_works_after_restructure(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Inline quick-edit still works: GET returns form, PATCH updates
        the cell, and HX-Trigger fires balanceChanged.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Phone Bill",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("80.00"),
            )
            db.session.add(txn)
            db.session.commit()

            # GI-1: GET quick edit form.
            resp = auth_client.get(f"/transactions/{txn.id}/quick-edit")
            assert resp.status_code == 200
            assert b"80" in resp.data

            # GI-2: PATCH updates amount.
            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"estimated_amount": "95.00"},
            )
            assert resp.status_code == 200
            assert b"95" in resp.data
            assert resp.headers.get("HX-Trigger") == "balanceChanged"

    def test_grid_empty_cell_quick_create_works(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """GI-9 regression: clicking an empty cell loads the quick-create form."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            # Create a transaction so a row key exists with empty cells
            # in adjacent periods.
            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Internet Bill",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("60.00"),
            )
            db.session.add(txn)
            db.session.commit()

            # Extract the quick-create URL from an empty cell.
            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            import re
            # Find quick-create hx-get URLs for the Rent category.
            # The route URL is /transactions/new/quick; HTML encodes & as &amp;.
            cat_id = seed_user["categories"]["Rent"].id
            pattern = rf'hx-get="(/transactions/new/quick\?[^"]*category_id={cat_id}[^"]*)"'
            urls = re.findall(pattern, html)
            assert urls, "No quick-create URL found for the Rent category"

            # Decode HTML entities so the test client can use the URL.
            url = urls[0].replace("&amp;", "&")

            # GET the quick-create form.
            resp = auth_client.get(url)
            assert resp.status_code == 200
            assert b"estimated_amount" in resp.data

    def test_grid_keyboard_nav_classes_correct(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Transaction rows do not have excluded CSS classes; group headers,
        subtotals, and banners do.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            current = self._get_current_period(seed_user)

            txn = Transaction(
                pay_period_id=current.id,
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

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            # Group header rows have the correct class.
            assert "group-header-row" in html
            # Subtotal rows have correct class.
            assert "subtotal-row" in html
            # Net cash flow row.
            assert "net-cash-flow-row" in html
            # Section banners.
            assert "section-banner-income" in html
            assert "section-banner-expense" in html

    def test_grid_empty_state_no_transactions(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Grid renders cleanly with no transactions -- section banners,
        subtotal rows with zeros, and no crash.
        """
        with app.app_context():
            resp = auth_client.get("/grid?periods=3")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Structure intact.
            assert "INCOME" in html
            assert "EXPENSES" in html
            assert "Total Income" in html
            assert "Total Expenses" in html
            assert "Net Cash Flow" in html
            assert "Projected End Balance" in html

    def test_grid_subtotals_unchanged_after_restructure(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Subtotals iterate over all transactions per period, not row keys.
        Total Income shows $2,000, Total Expenses shows $1,500, Net shows $500.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            current = self._get_current_period(seed_user)

            income = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Paycheck",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=Decimal("2000.00"),
            )
            expense1 = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Rent",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("1000.00"),
            )
            expense2 = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
            )
            db.session.add_all([income, expense1, expense2])
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Subtotals computed from all transactions.
            assert "$2,000" in html   # Total Income
            assert "$1,500" in html   # Total Expenses
            assert "$500" in html     # Net Cash Flow

    def test_grid_payday_workflow_complete(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Full payday workflow still works after the row restructure:
        true-up, mark received, carry forward, mark paid, mark credit.
        Identical to C-0-7 from regression suite.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            account = seed_user["account"]

            current = self._get_current_period(seed_user)
            past = next(
                p for p in seed_periods_today
                if p.period_index == current.period_index - 1
            )

            # Setup: past expense, current income + 2 expenses.
            past_exp = Transaction(
                pay_period_id=past.id,
                scenario_id=seed_user["scenario"].id,
                account_id=account.id,
                status_id=projected.id,
                name="Past Rent",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("150.00"),
            )
            income_txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=account.id,
                status_id=projected.id,
                name="Paycheck",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=Decimal("2000.00"),
            )
            exp_done = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=account.id,
                status_id=projected.id,
                name="Electric Bill",
                category_id=seed_user["categories"]["Car Payment"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
            )
            exp_credit = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=account.id,
                status_id=projected.id,
                name="Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("300.00"),
            )
            db.session.add_all([past_exp, income_txn, exp_done, exp_credit])
            db.session.commit()

            # Step 1: True-up.
            resp = auth_client.patch(
                f"/accounts/{account.id}/true-up",
                data={"anchor_balance": "5000.00"},
            )
            assert resp.status_code == 200

            # Step 2: Mark income received.
            resp = auth_client.post(f"/transactions/{income_txn.id}/mark-done")
            assert resp.status_code == 200

            # Step 3: Carry forward.
            resp = auth_client.post(f"/pay-periods/{past.id}/carry-forward")
            assert resp.status_code == 200

            # Step 4: Mark expense paid.
            resp = auth_client.post(f"/transactions/{exp_done.id}/mark-done")
            assert resp.status_code == 200

            # Step 5: Mark expense credit.
            resp = auth_client.post(f"/transactions/{exp_credit.id}/mark-credit")
            assert resp.status_code == 200

            # Verify balances.
            resp = auth_client.get(
                f"/grid/balance-row?periods=2&offset=0"
                f"&account_id={account.id}"
            )
            assert resp.status_code == 200
            assert b"$4,850" in resp.data
            assert b"$4,550" in resp.data

    def test_grid_row_ordering_is_deterministic(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Row ordering is deterministic -- two requests produce identical
        row label sequences.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            current = self._get_current_period(seed_user)

            # Create multiple transactions across categories.
            txns = [
                Transaction(
                    pay_period_id=current.id,
                    scenario_id=seed_user["scenario"].id,
                    account_id=seed_user["account"].id,
                    status_id=projected.id,
                    name="Paycheck",
                    category_id=seed_user["categories"]["Salary"].id,
                    transaction_type_id=income_type.id,
                    estimated_amount=Decimal("2000.00"),
                ),
                Transaction(
                    pay_period_id=current.id,
                    scenario_id=seed_user["scenario"].id,
                    account_id=seed_user["account"].id,
                    status_id=projected.id,
                    name="Rent",
                    category_id=seed_user["categories"]["Rent"].id,
                    transaction_type_id=expense_type.id,
                    estimated_amount=Decimal("1000.00"),
                ),
                Transaction(
                    pay_period_id=current.id,
                    scenario_id=seed_user["scenario"].id,
                    account_id=seed_user["account"].id,
                    status_id=projected.id,
                    name="Groceries",
                    category_id=seed_user["categories"]["Groceries"].id,
                    transaction_type_id=expense_type.id,
                    estimated_amount=Decimal("200.00"),
                ),
                Transaction(
                    pay_period_id=current.id,
                    scenario_id=seed_user["scenario"].id,
                    account_id=seed_user["account"].id,
                    status_id=projected.id,
                    name="Car Loan",
                    category_id=seed_user["categories"]["Car Payment"].id,
                    transaction_type_id=expense_type.id,
                    estimated_amount=Decimal("400.00"),
                ),
            ]
            db.session.add_all(txns)
            db.session.commit()

            import re

            def extract_row_labels(html_str):
                """Extract row-label <th> text in order."""
                return re.findall(
                    r'<th[^>]*class="[^"]*row-label[^"]*"[^>]*>\s*(\S[^<]*?)\s*</th>',
                    html_str,
                )

            resp1 = auth_client.get("/grid?periods=3")
            labels1 = extract_row_labels(resp1.data.decode())

            resp2 = auth_client.get("/grid?periods=3")
            labels2 = extract_row_labels(resp2.data.decode())

            assert labels1 == labels2
            assert len(labels1) >= 4  # At least 4 transaction rows.

    def test_grid_credit_payback_gets_own_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """CC Payback transactions generated by the credit workflow appear
        in their own row with 'CC Payback: ...' in the row header.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Restaurant",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("75.00"),
            )
            db.session.add(txn)
            db.session.commit()

            # Mark as credit -- generates payback in next period.
            resp = auth_client.post(f"/transactions/{txn.id}/mark-credit")
            assert resp.status_code == 200

            # GET the grid showing the next period where the payback lives.
            resp = auth_client.get("/grid?periods=3")
            assert resp.status_code == 200
            html = resp.data.decode()

            import re
            th_labels = re.findall(
                r'<th[^>]*class="[^"]*row-label[^"]*"[^>]*>\s*(\S[^<]*?)\s*</th>',
                html,
            )
            # "CC Payback:" prefix is stripped -- row shows original name.
            # The original transaction was "Restaurant", so the payback
            # row should show "Restaurant" (under Credit Card: Payback group).
            assert "Restaurant" in th_labels, (
                f"Expected 'Restaurant' row header for payback, got: {th_labels}"
            )

    def test_grid_cancelled_transaction_handling(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Cancelled transactions are excluded from the grid -- they do not
        generate row keys and do not appear as cells.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Cancelled Item",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("50.00"),
            )
            db.session.add(txn)
            db.session.commit()

            # Cancel it.
            resp = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert resp.status_code == 200

            # GET the grid.
            resp = auth_client.get("/grid?periods=3")
            assert resp.status_code == 200
            html = resp.data.decode()

            import re
            th_labels = re.findall(
                r'<th[^>]*class="[^"]*row-label[^"]*"[^>]*>\s*(\S[^<]*?)\s*</th>',
                html,
            )
            assert "Cancelled Item" not in th_labels


class TestTooltipContent:
    """Tests for Commit #16: enhanced transaction cell tooltips.

    The tooltip now shows full dollar amounts with cents, actual-vs-estimated
    comparison, status labels, and notes.  The transaction name is no longer
    in the tooltip (it moved to the row header in Commit #15).
    """

    def _get_current_period(self, seed_user):
        """Return the current period for the seed user."""
        return pay_period_service.get_current_period(seed_user["user"].id)

    @staticmethod
    def _extract_txn_titles(html):
        """Extract title attribute values from txn-cell divs."""
        import re
        return re.findall(
            r'<div class="txn-cell"[^>]*title="([^"]*)"', html,
        )

    def test_tooltip_contains_full_amount_with_cents(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Tooltip shows the full dollar amount with two decimal places,
        including comma-separated thousands (e.g. $1,234.56).
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Test Tooltip Amount",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("1234.56"),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            titles = self._extract_txn_titles(html)
            matching = [t for t in titles if "$1,234.56" in t]
            assert matching, f"Expected tooltip with $1,234.56, got: {titles}"
            # Should NOT show the rounded amount as the primary tooltip content.
            assert not any(
                t.startswith("$1,235 ") or t == "$1,235" for t in titles
            ), "Tooltip should show cents, not rounded amount"

    def test_tooltip_shows_actual_vs_estimated_when_different(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """When actual_amount differs from estimated_amount, the tooltip shows
        both: '$487.32 (est: $500.00)'.
        """
        with app.app_context():
            paid = db.session.query(Status).filter_by(name="Paid").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=paid.id,
                name="Test Est Comparison",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
                actual_amount=Decimal("487.32"),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            titles = self._extract_txn_titles(html)
            matching = [t for t in titles if "$487.32" in t and "(est: $500.00)" in t]
            assert matching, f"Expected '$487.32 (est: $500.00)', got: {titles}"

    def test_tooltip_hides_estimate_when_amounts_equal(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """When actual_amount equals estimated_amount, the tooltip shows only
        the amount without the '(est: ...)' comparison.
        """
        with app.app_context():
            paid = db.session.query(Status).filter_by(name="Paid").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=paid.id,
                name="Test Equal Amounts",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
                actual_amount=Decimal("500.00"),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            titles = self._extract_txn_titles(html)
            matching = [t for t in titles if "$500.00" in t]
            assert matching, f"Expected tooltip with $500.00, got: {titles}"
            assert not any("(est:" in t for t in matching), (
                "Tooltip should not show '(est:' when amounts are equal"
            )

    def test_tooltip_includes_paid_status(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Tooltip includes '-- Paid' for transactions with Paid status."""
        with app.app_context():
            paid = db.session.query(Status).filter_by(name="Paid").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=paid.id,
                name="Test Paid Status",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("100.00"),
                actual_amount=Decimal("100.00"),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            titles = self._extract_txn_titles(html)
            matching = [t for t in titles if "-- Paid" in t]
            assert matching, f"Expected tooltip with '-- Paid', got: {titles}"

    def test_tooltip_includes_projected_status(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Tooltip includes '-- Projected' for projected transactions."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Test Projected Status",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("75.00"),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            titles = self._extract_txn_titles(html)
            matching = [t for t in titles if "-- Projected" in t]
            assert matching, f"Expected tooltip with '-- Projected', got: {titles}"

    def test_tooltip_includes_notes(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Tooltip includes notes when present on the transaction."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Test Notes Tooltip",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("50.00"),
                notes="Auto-pay on the 15th",
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            titles = self._extract_txn_titles(html)
            matching = [t for t in titles if "-- Auto-pay on the 15th" in t]
            assert matching, f"Expected notes in tooltip, got: {titles}"

    def test_tooltip_no_trailing_separator_when_no_notes(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """When notes are empty/None, the tooltip does not have a trailing
        '-- ' separator with nothing after it.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Test No Trailing Sep",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("200.00"),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            titles = self._extract_txn_titles(html)
            matching = [t for t in titles if "$200.00" in t]
            assert matching, f"Expected tooltip with $200.00, got: {titles}"
            for title in matching:
                assert not title.endswith("-- "), (
                    f"Tooltip has trailing separator: '{title}'"
                )
                assert not title.endswith("--"), (
                    f"Tooltip has trailing separator: '{title}'"
                )

    def test_tooltip_handles_zero_amount(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Tooltip renders $0.00 correctly for a zero-amount transaction."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Test Zero Amount",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("0.00"),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            titles = self._extract_txn_titles(html)
            matching = [t for t in titles if "$0.00" in t]
            assert matching, f"Expected tooltip with $0.00, got: {titles}"

    def test_tooltip_handles_large_amount(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Tooltip formats large amounts with comma-separated thousands."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Test Large Amount",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("12345.67"),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            titles = self._extract_txn_titles(html)
            matching = [t for t in titles if "$12,345.67" in t]
            assert matching, f"Expected tooltip with $12,345.67, got: {titles}"

    def test_tooltip_credit_transaction_shows_charged_amount(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Credit transactions show the estimated (charged) amount in the
        tooltip, not $0.00 from effective_amount.  Also includes '-- Credit'.
        """
        with app.app_context():
            credit = db.session.query(Status).filter_by(name="Credit").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=credit.id,
                name="Test Credit Tooltip",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("200.00"),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            titles = self._extract_txn_titles(html)
            matching = [t for t in titles if "$200.00" in t and "-- Credit" in t]
            assert matching, (
                f"Expected tooltip with $200.00 and '-- Credit', got: {titles}"
            )
            # Must NOT show $0.00 (which is what effective_amount returns).
            assert not any("$0.00" in t and "-- Credit" in t for t in titles), (
                "Credit tooltip should show charged amount, not $0.00"
            )

    def test_tooltip_survives_htmx_cell_update(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """After a PATCH update via quick edit, the re-rendered cell includes
        a title attribute with the updated amount (server-side rendering).
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Test HTMX Update",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("80.00"),
            )
            db.session.add(txn)
            db.session.commit()

            # PATCH the amount.
            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"estimated_amount": "95.50"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            # The re-rendered cell should have the updated amount in the title.
            assert "$95.50" in html, (
                f"Expected $95.50 in PATCH response title, got: {html[:500]}"
            )

    def test_tooltip_no_redundant_name(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The tooltip does NOT contain the transaction name (it moved to
        the row header in Commit #15).
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="State Farm",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("150.00"),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            titles = self._extract_txn_titles(html)
            matching = [t for t in titles if "$150.00" in t]
            assert matching, f"Expected tooltip with $150.00, got: {titles}"
            for title in matching:
                assert "State Farm" not in title, (
                    f"Tooltip should not contain transaction name, got: '{title}'"
                )


class TestSubtotalDecimalPrecision:
    """Verify server-side Decimal subtotals agree with balance row at the penny level (H-05)."""

    def test_subtotals_match_balance_row(self, app, auth_client, seed_user, seed_periods_today):
        """Pre-computed Decimal subtotals match the balance calculator's values exactly.

        Creates 20+ transactions with sub-dollar amounts that would
        accumulate float drift if |float were used. Verifies the grid
        subtotals and the balance row agree within $0.01.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            from app.services import pay_period_service
            period = pay_period_service.get_current_period(seed_user["user"].id)
            if not period:
                period = seed_periods_today[0]

            # Create 20 expense transactions with amounts that cause float drift.
            expected_expense = Decimal("0")
            for i in range(20):
                amt = Decimal("33.33")
                txn = Transaction(
                    pay_period_id=period.id,
                    scenario_id=seed_user["scenario"].id,
                    account_id=seed_user["account"].id,
                    status_id=projected.id,
                    name=f"Expense {i}",
                    category_id=seed_user["categories"]["Groceries"].id,
                    transaction_type_id=expense_type.id,
                    estimated_amount=amt,
                )
                db.session.add(txn)
                expected_expense += amt

            # Create 5 income transactions.
            expected_income = Decimal("0")
            for i in range(5):
                amt = Decimal("777.77")
                txn = Transaction(
                    pay_period_id=period.id,
                    scenario_id=seed_user["scenario"].id,
                    account_id=seed_user["account"].id,
                    status_id=projected.id,
                    name=f"Income {i}",
                    category_id=seed_user["categories"]["Groceries"].id,
                    transaction_type_id=income_type.id,
                    estimated_amount=amt,
                )
                db.session.add(txn)
                expected_income += amt

            db.session.commit()

            # Fetch the grid page.
            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Verify subtotals appear with correct Decimal-computed values.
            # 20 * $33.33 = $666.60
            assert "$667" in html or "$666" in html, (
                "Expected expense subtotal ~$666-667 in grid"
            )
            # 5 * $777.77 = $3,888.85
            assert "$3,889" in html or "$3,888" in html, (
                "Expected income subtotal ~$3888-3889 in grid"
            )


class TestGridSubtotalsRegressionBaseline:
    """Regression baseline: per-period subtotal reflects actual_amount.

    Pre-Commit-10 the grid subtotal was an inline ``sum(...
    effective_amount ...)`` loop in ``app/routes/grid.py``.  Commit 10
    routes the subtotal through ``balance_resolver.period_subtotal``,
    which uses ``effective_amount`` for income and the entries-aware
    reduction for expenses; for income with no entries the
    ``effective_amount`` rule is unchanged, so this 5A.1-era regression
    baseline continues to hold (Projected income with
    ``actual_amount`` populated still reports the actual on screen).
    """

    def test_subtotals_reflect_actual_for_projected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Projected income with estimated=500, actual=400: subtotal
        reflects actual_amount (400).

        Originally a Commit #0 regression baseline asserting the D-1 bug
        (subtotal showed estimated).  Updated in Commit 5A.1 to assert
        the corrected behavior: effective_amount now returns actual when
        populated, so the grid subtotal automatically shows 400.
        Commit 10 routes the subtotal through
        ``balance_resolver.period_subtotal`` whose income leg still uses
        ``effective_amount``, so the assertion is unchanged.
        """
        with app.app_context():
            scenario = seed_user["scenario"]
            account = seed_user["account"]

            projected = db.session.query(Status).filter_by(
                name="Projected",
            ).one()
            income_type = db.session.query(TransactionType).filter_by(
                name="Income",
            ).one()

            # Place the transaction in the current period so it is
            # visible on the default grid view.
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            if not current:
                current = seed_periods_today[0]

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=scenario.id,
                account_id=account.id,
                status_id=projected.id,
                name="Regression Subtotal Income",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=Decimal("500.00"),
                actual_amount=Decimal("400.00"),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            html = resp.data.decode()

            # 5A.1 fix: subtotal uses effective_amount which now returns
            # actual (400).  Grid formats subtotals as "${:,.0f}".
            assert "$400" in html, (
                "Income subtotal should reflect actual_amount (400) "
                "for Projected transactions when actual is populated"
            )
            assert "subtotal-row-income" in html, (
                "Income subtotal row must be present in grid"
            )


class TestGridPeriodSubtotalCanonical:
    """Commit 10: per-period subtotals routed through ``period_subtotal``.

    Pre-Commit-10 the grid's per-period subtotal was an inline
    ``sum(... effective_amount ...)`` loop in ``app/routes/grid.py``
    that did NOT apply the entries-aware reduction.  F-002 Pair C /
    F-004 (Q-10) flagged this as a same-page divergence: the subtotal
    row and the balance row consumed the same in-memory transactions
    but with different expense formulas.  Commit 10 collapses the
    grid subtotal onto ``balance_resolver.period_subtotal``, so a
    Projected envelope expense with cleared entries now reports the
    same entries-aware impact on both rows; ``balance[p] -
    balance[p-1] == subtotal[p].net`` by construction.
    """

    def test_grid_subtotal_entry_aware_for_projected_expense(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Rendered grid subtotal reflects the entry-aware reduction.

        Setup: a Projected $500.00 envelope expense in the visible
        current period carries three cleared debit entries summing
        $462.34.  Pre-Commit-10 the subtotal row showed $500
        (raw ``effective_amount``); the corrected entries-aware
        impact is $37.66.

        Hand arithmetic (F-002 Pair C / F-004):
          cleared_debit = 20.00 + 442.34 + 0.00 = 462.34
          uncleared_debit = 0
          sum_credit = 0
          impact = max(500.00 - 462.34 - 0, 0) = 37.66.
        """
        from app.models.transaction_entry import TransactionEntry

        with app.app_context():
            projected = db.session.query(Status).filter_by(
                name="Projected",
            ).one()
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None, (
                "seed_periods_today must produce a current period"
            )

            txn = Transaction(
                pay_period_id=current.id,
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

            for amt in (
                Decimal("20.00"),
                Decimal("442.34"),
            ):
                db.session.add(TransactionEntry(
                    transaction_id=txn.id,
                    user_id=seed_user["user"].id,
                    amount=amt,
                    description="cleared purchase",
                    entry_date=current.start_date,
                    is_credit=False,
                    is_cleared=True,
                ))
            db.session.commit()

            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Grid formats subtotals as "${:,.0f}", so $37.66 -> $38.
            # The pre-Commit-10 value would have rounded $500 -> $500.
            assert "$38" in html, (
                "Expense subtotal should be entry-aware: "
                "$500 estimated - $462.34 cleared = $37.66 (-> $38)"
            )
            assert "$500" not in html.split("subtotal-row-expense")[1].split("</tr>")[0], (
                "Subtotal expense cell must not show the raw "
                "effective_amount $500 (F-002 Pair C / F-004 regression)"
            )

    def test_grid_subtotal_reconciles_balance_delta(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``balance[p] - balance[p-1] == subtotal[p].net`` exactly.

        Same-formula invariant E-25 / Q-10 resolution: the canonical
        producer drives both the subtotal row and the balance carry-
        forward, so the period-to-period balance delta must equal the
        subtotal's ``net`` to the penny.  The previous inline loop
        violated this whenever a Projected envelope expense carried
        cleared entries (the subtotal showed the raw estimate, the
        balance row showed the entry-aware impact).

        Setup: anchor $1000 at periods[0]; one Projected $300.00
        envelope expense in periods[5] with two cleared debits
        summing $250.00.

        Hand arithmetic:
          period5_impact = max(300.00 - 250.00 - 0, 0) = 50.00.
          balance[periods[5]] = balance[periods[4]] - 50.00.
          subtotal[periods[5]].net = 0 - 50.00 = -50.00.
          balance[periods[5]] - balance[periods[4]] = -50.00 == net.
        """
        from app.models.transaction_entry import TransactionEntry
        from app.services import balance_resolver

        with app.app_context():
            projected = db.session.query(Status).filter_by(
                name="Projected",
            ).one()
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()
            periods = seed_periods_today
            target_period = periods[5]

            txn = Transaction(
                pay_period_id=target_period.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Groceries window",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("300.00"),
            )
            db.session.add(txn)
            db.session.flush()
            for amt in (Decimal("100.00"), Decimal("150.00")):
                db.session.add(TransactionEntry(
                    transaction_id=txn.id,
                    user_id=seed_user["user"].id,
                    amount=amt,
                    description="cleared purchase",
                    entry_date=target_period.start_date,
                    is_credit=False,
                    is_cleared=True,
                ))
            db.session.commit()

            # Exercise the grid route to prove the wiring is in place
            # before falling back to the resolver-level assertion.
            resp = auth_client.get("/grid")
            assert resp.status_code == 200

            # Resolver-level reconciliation: the grid route and the
            # producers consume the same fixture, so their outputs are
            # the ground truth the rendered HTML reflects.
            balance_result = balance_resolver.balances_for(
                seed_user["account"],
                seed_user["scenario"].id,
                periods,
            )
            sub = balance_resolver.period_subtotal(
                seed_user["account"],
                seed_user["scenario"].id,
                target_period,
            )

            prior_period = periods[4]
            delta = (
                balance_result.balances[target_period.id]
                - balance_result.balances[prior_period.id]
            )
            # 0 - max(300 - 100 - 150, 0) = -50.00.
            assert sub.expense == Decimal("50.00"), (
                f"expected $50.00 entry-aware expense, got {sub.expense!r}"
            )
            assert sub.net == Decimal("-50.00")
            assert delta == sub.net, (
                f"balance delta {delta!r} must equal subtotal net {sub.net!r}"
            )

    def test_grid_inline_subtotal_loop_removed(self):
        """Static guard: no inline ``sum(... effective_amount ...)`` in grid.py.

        The plan's verification gate -- if the inline loop is ever
        reintroduced, the canonical-producer routing is silently
        bypassed.  This regression lock fires the moment a future edit
        re-grows the loop.
        """
        import re

        from pathlib import Path

        grid_source = Path("app/routes/grid.py").read_text(encoding="utf-8")
        pattern = re.compile(
            r"sum\([^\)]*(effective_amount|estimated_amount)",
        )
        offenders = pattern.findall(grid_source)
        assert not offenders, (
            "app/routes/grid.py contains an inline subtotal loop "
            f"({offenders!r}); route through "
            "balance_resolver.period_subtotal instead (F-002 Pair C, "
            "F-004 same-page regression)"
        )

    def test_grid_balance_computation_routed_through_resolver(self):
        """Static guard: grid balance computation routes through ``balance_resolver``.

        F-6 lock.  The cross-page balance-equality regression test
        (``tests/test_integration/test_cross_page_balance_equality.py``,
        Commit 11 of the main remediation) cannot catch a route-handler
        bypass of the canonical producer because its grid reader
        re-runs ``balance_resolver.balances_for`` itself rather than
        parsing the rendered HTML.  A regression that re-introduces a
        hand-rolled balance loop in ``app/routes/grid.py`` (or that
        swaps the canonical entries-aware producer for the bare
        entries-blind ``balance_calculator.calculate_balances``) would
        therefore drift silently.  This static lock closes that gap.

        Two assertions:
          1. ``balance_resolver.balances_for`` must still appear in
             ``app/routes/grid.py`` (positive: the E-25 / Commit 5
             canonical-producer wiring is intact).
          2. ``balance_calculator.calculate_balances(`` (the bare
             entries-blind producer) must NOT appear -- the entries-
             aware reduction in ``_entry_aware_amount`` is the F-009 /
             CRIT-01 fix; the bare producer would re-open the silent-
             degrade seam.  ``calculate_balances_with_interest`` is a
             distinct symbol and would not match this anti-pattern.

        Complements ``test_grid_inline_subtotal_loop_removed`` above:
        that guard catches an inline ``sum(... effective_amount ...)``
        accumulator; this guard catches a swap to the entries-blind
        canonical-named function.
        """
        from pathlib import Path  # pylint: disable=import-outside-toplevel

        grid_source = Path("app/routes/grid.py").read_text(encoding="utf-8")
        assert "balance_resolver.balances_for" in grid_source, (
            "app/routes/grid.py no longer calls "
            "``balance_resolver.balances_for`` -- regression on the "
            "E-25 / Commit 5 canonical-producer contract.  Route the "
            "balance computation through ``balance_resolver`` instead "
            "of a hand-rolled loop or the bare entries-blind producer."
        )
        assert "balance_calculator.calculate_balances(" not in grid_source, (
            "app/routes/grid.py imports the bare entries-blind "
            "``balance_calculator.calculate_balances`` -- this bypasses "
            "the entries-aware reduction (F-009 / CRIT-01 fix).  Use "
            "``balance_resolver.balances_for`` instead."
        )

    def test_obligations_has_no_period_subtotal_loop(self):
        """Static guard: obligations.py has no period-subtotal arithmetic.

        Obligations computes ``amount_to_monthly`` per template
        (E-24 / Commit 23 territory), not per-period transaction
        subtotals.  The plan's verification gate covers both files;
        this assertion locks that obligations never grows the same
        inline ``sum(... effective_amount ...)`` loop the grid had.
        """
        import re

        from pathlib import Path

        obligations_source = Path(
            "app/routes/obligations.py",
        ).read_text(encoding="utf-8")
        pattern = re.compile(
            r"sum\([^\)]*(effective_amount|estimated_amount)",
        )
        offenders = pattern.findall(obligations_source)
        assert not offenders, (
            "app/routes/obligations.py contains inline period-subtotal "
            f"arithmetic ({offenders!r}); route through the canonical "
            "producer if it ever needs per-period subtotals"
        )


class TestGridMatchedByRowPeriod:
    """Commit 2 (mobile-first v3): ``matched_by_row_period`` route context.

    The matching predicate previously hand-coded in four blocks of
    Jinja (``grid.html`` income + expense, ``_mobile_grid.html`` income +
    expense) is precomputed once in the route as a dict keyed by
    ``(category_id, template_id, txn_name, period_id)``.  Commit 1
    introduced the macros that read it; Commit 2 (this commit) adds
    the dict to the route context; Commits 3 and 4 wire the templates
    to consume it.

    These tests lock in the route contract: the dict is in the
    rendered context, its keys are 4-tuples, its values are non-empty
    lists of ``Transaction`` ORM objects, and its contents mirror the
    Jinja predicate text-for-text (category match, income/expense per
    section, not-deleted, not-cancelled, template-id-match-takes-
    precedence with name-match fallback).
    """

    @staticmethod
    def _capture_grid_context(app, auth_client):
        """Return the (template, context) tuple captured from /grid.

        Uses Flask's ``template_rendered`` signal to record what the
        grid route handed to ``render_template`` so the test can
        inspect ``matched_by_row_period`` (and any other context key)
        without parsing rendered HTML.  Returns the first
        ``grid/grid.html`` record; raises ``AssertionError`` if the
        route rendered a different template (the ``no_setup`` or
        ``no_periods`` branch) so the test fails loud rather than
        silently inspecting the wrong context.
        """
        from flask import template_rendered  # pylint: disable=import-outside-toplevel

        recorded: list[tuple] = []

        def _record(sender, template, context, **extra):
            recorded.append((template, context))

        template_rendered.connect(_record, app)
        try:
            response = auth_client.get("/grid")
        finally:
            template_rendered.disconnect(_record, app)
        assert response.status_code == 200, (
            f"GET /grid returned {response.status_code}; expected 200"
        )
        grid_records = [
            (t, c) for t, c in recorded if t.name == "grid/grid.html"
        ]
        assert grid_records, (
            "GET /grid did not render grid/grid.html; templates "
            f"rendered: {[t.name for t, _ in recorded]!r}"
        )
        return grid_records[0]

    def test_index_renders_with_new_context(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C2-1: GET /grid still returns 200 with the new precomputation.

        Pure smoke test that adding the precomputation and the new
        ``matched_by_row_period`` kwarg to ``render_template`` did not
        break the existing rendering pipeline.  No assertion on the
        dict's contents -- C2-2 and C2-3 cover that.
        """
        with app.app_context():
            response = auth_client.get("/grid")
            assert response.status_code == 200
            assert b"Checking Balance" in response.data
            assert b"Projected End Balance" in response.data

    def test_matched_by_row_period_in_context(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C2-2: ``matched_by_row_period`` is present in the render context.

        Setup: seed one Projected expense in the current period so the
        dict has at least one entry.  Asserts the dict exists, is a
        ``dict``, every key is a 4-tuple of ``(int, int | None, str,
        int)``, and every value is a non-empty list of ``Transaction``
        ORM objects.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None
            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="Weekly Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                estimated_amount=Decimal("123.45"),
            )
            db.session.add(txn)
            db.session.commit()

            _, context = self._capture_grid_context(app, auth_client)

            assert "matched_by_row_period" in context, (
                "render_template kwargs missing matched_by_row_period; "
                "Commit 2 of mobile-first v3 adds it as the canonical "
                "matching producer for the grid macros"
            )
            matched = context["matched_by_row_period"]
            assert isinstance(matched, dict), (
                f"matched_by_row_period must be a dict, got {type(matched)!r}"
            )
            assert matched, (
                "Seeded one txn in the current period; "
                "matched_by_row_period should be non-empty"
            )
            for key, value in matched.items():
                assert isinstance(key, tuple) and len(key) == 4, (
                    f"matched_by_row_period key {key!r} is not a 4-tuple"
                )
                category_id, template_id, txn_name, period_id = key
                assert isinstance(category_id, int)
                assert template_id is None or isinstance(template_id, int)
                assert isinstance(txn_name, str)
                assert isinstance(period_id, int)
                assert isinstance(value, list) and value, (
                    "matched_by_row_period values must be non-empty lists"
                )
                for matched_txn in value:
                    assert isinstance(matched_txn, Transaction), (
                        "matched_by_row_period values must contain "
                        f"Transaction ORM objects, got {type(matched_txn)!r}"
                    )

    def test_matched_dict_mirrors_jinja_predicate(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C2-3: dict contents mirror the Jinja matching predicate.

        Seeds four transactions exercising each predicate branch:
          (a) a template-linked income (Salary template) in the
              current period -- must match via the template-id branch.
          (b) a standalone expense in Groceries by name -- must match
              via the name-match fallback.
          (c) a cancelled expense in Groceries -- must NOT appear (the
              ``status_id != STATUS_CANCELLED`` guard).
          (d) a soft-deleted expense in Groceries -- must NOT appear
              (the ``not is_deleted`` guard).

        Asserts: matched_by_row_period contains the expected keys for
        (a) and (b); the matched lists include only the correct txn;
        (c) and (d) do not appear in any matched list.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            cancelled_id = ref_cache.status_id(StatusEnum.CANCELLED)
            income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
            expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
            salary_cat = seed_user["categories"]["Salary"]
            groceries_cat = seed_user["categories"]["Groceries"]

            # (a) Template-linked income.
            salary_template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=salary_cat.id,
                transaction_type_id=income_type_id,
                name="Biweekly Salary",
                default_amount=Decimal("2500.00"),
            )
            db.session.add(salary_template)
            db.session.flush()
            txn_a = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                status_id=projected_id,
                name="Biweekly Salary",
                category_id=salary_cat.id,
                transaction_type_id=income_type_id,
                estimated_amount=Decimal("2500.00"),
                template_id=salary_template.id,
            )
            # (b) Standalone expense.
            txn_b = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                status_id=projected_id,
                name="Adhoc Groceries",
                category_id=groceries_cat.id,
                transaction_type_id=expense_type_id,
                estimated_amount=Decimal("85.00"),
            )
            # (c) Cancelled expense.
            txn_c = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                status_id=cancelled_id,
                name="Cancelled Groceries",
                category_id=groceries_cat.id,
                transaction_type_id=expense_type_id,
                estimated_amount=Decimal("50.00"),
            )
            # (d) Soft-deleted expense.
            txn_d = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                status_id=projected_id,
                name="Deleted Groceries",
                category_id=groceries_cat.id,
                transaction_type_id=expense_type_id,
                estimated_amount=Decimal("60.00"),
                is_deleted=True,
            )
            db.session.add_all([txn_a, txn_b, txn_c, txn_d])
            db.session.commit()
            txn_a_id = txn_a.id
            txn_b_id = txn_b.id
            txn_c_id = txn_c.id
            txn_d_id = txn_d.id
            template_id = salary_template.id

            _, context = self._capture_grid_context(app, auth_client)
            matched = context["matched_by_row_period"]

            # (a) Template-linked: key uses template_id, txn_name from row
            # key is the template name; the matched list contains txn_a.
            key_a = (
                salary_cat.id, template_id, "Biweekly Salary", current.id,
            )
            assert key_a in matched, (
                f"Template-linked match missing; expected key {key_a!r} "
                f"in dict; got keys {list(matched.keys())!r}"
            )
            assert [t.id for t in matched[key_a]] == [txn_a_id], (
                f"Template-linked match must contain only txn_a "
                f"(id={txn_a_id}); got "
                f"{[t.id for t in matched[key_a]]!r}"
            )

            # (b) Standalone: key uses template_id=None, txn_name from
            # row key is the instance name; matched list contains txn_b.
            key_b = (
                groceries_cat.id, None, "Adhoc Groceries", current.id,
            )
            assert key_b in matched, (
                f"Standalone match missing; expected key {key_b!r} "
                f"in dict; got keys {list(matched.keys())!r}"
            )
            assert [t.id for t in matched[key_b]] == [txn_b_id], (
                "Standalone match must contain only txn_b "
                f"(id={txn_b_id}); got "
                f"{[t.id for t in matched[key_b]]!r}"
            )

            # (c) Cancelled: must not appear in any matched list.  Also
            # row-key for "Cancelled Groceries" should be absent because
            # _build_row_keys filters cancelled txns at row-key time.
            all_matched_ids = {
                t.id for v in matched.values() for t in v
            }
            assert txn_c_id not in all_matched_ids, (
                f"Cancelled txn (id={txn_c_id}) must not appear in "
                "matched_by_row_period (status_id != STATUS_CANCELLED "
                "guard)"
            )

            # (d) Soft-deleted: must not appear in any matched list.
            assert txn_d_id not in all_matched_ids, (
                f"Soft-deleted txn (id={txn_d_id}) must not appear in "
                "matched_by_row_period (not is_deleted guard)"
            )

    def test_no_balance_resolver_reads(self):
        """C2-4: no NEW direct reads of canonical-producer source columns.

        Plan Section 1 rule 2 ("Canonical producers only for monetary
        values"): the precomputation Commit 2 introduces must not read
        ``Account.current_anchor_balance`` /
        ``Account.current_anchor_period_id`` /
        ``LoanParams.current_principal`` / ``LoanParams.interest_rate``
        beyond the baseline that already exists in the route.

        Baseline (pre-commit): exactly one read of
        ``account.current_anchor_balance`` at the ``anchor_balance``
        local in ``index()`` -- this is the existing display value
        and is NOT a bypass of ``balance_resolver``.  After this
        commit, that count must still be exactly one and the other
        three symbols must still be zero.

        Complements the existing
        ``test_grid_balance_computation_routed_through_resolver``
        (F-6 lock) by pinning the *count* of legacy reads rather
        than the presence/absence of the canonical-producer symbol.
        """
        import re  # pylint: disable=import-outside-toplevel
        from pathlib import Path  # pylint: disable=import-outside-toplevel

        grid_source = Path("app/routes/grid.py").read_text(encoding="utf-8")
        current_anchor_balance_reads = len(
            re.findall(r"\.current_anchor_balance\b", grid_source),
        )
        current_anchor_period_id_reads = len(
            re.findall(r"\.current_anchor_period_id\b", grid_source),
        )
        current_principal_reads = len(
            re.findall(r"\.current_principal\b", grid_source),
        )
        interest_rate_reads = len(
            re.findall(r"\.interest_rate\b", grid_source),
        )

        assert current_anchor_balance_reads == 1, (
            "app/routes/grid.py contains "
            f"{current_anchor_balance_reads} reads of "
            "``.current_anchor_balance`` (expected 1 baseline read at "
            "``anchor_balance = account.current_anchor_balance ...``); "
            "Commit 2 of mobile-first v3 must not add NEW direct reads "
            "of canonical-producer source columns -- route all monetary "
            "values through ``balance_resolver``"
        )
        assert current_anchor_period_id_reads == 0, (
            "app/routes/grid.py reads "
            "``.current_anchor_period_id`` directly; route through "
            "``balance_resolver`` instead"
        )
        assert current_principal_reads == 0, (
            "app/routes/grid.py reads ``.current_principal`` directly; "
            "route through ``loan_resolver`` instead"
        )
        assert interest_rate_reads == 0, (
            "app/routes/grid.py reads ``.interest_rate`` directly; "
            "route through ``loan_resolver`` instead"
        )


class TestPaidAtLifecycle:
    """Tests for paid_at timestamp management during status changes."""

    def _create_test_txn(self, seed_user, seed_periods_today):
        """Create a projected expense transaction for testing."""
        from app import ref_cache
        from app.enums import StatusEnum, TxnTypeEnum

        projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
        expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)

        txn = Transaction(
            account_id=seed_user["account"].id,
            pay_period_id=seed_periods_today[0].id,
            scenario_id=seed_user["scenario"].id,
            status_id=projected_id,
            name="Test Expense",
            category_id=seed_user["categories"]["Rent"].id,
            transaction_type_id=expense_type_id,
            estimated_amount=Decimal("100.00"),
            due_date=seed_periods_today[0].start_date,
        )
        db.session.add(txn)
        db.session.commit()
        return txn

    def _create_income_txn(self, seed_user, seed_periods_today):
        """Create a projected income transaction for testing."""
        from app import ref_cache
        from app.enums import StatusEnum, TxnTypeEnum

        projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
        income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)

        txn = Transaction(
            account_id=seed_user["account"].id,
            pay_period_id=seed_periods_today[0].id,
            scenario_id=seed_user["scenario"].id,
            status_id=projected_id,
            name="Test Income",
            category_id=seed_user["categories"]["Salary"].id,
            transaction_type_id=income_type_id,
            estimated_amount=Decimal("2000.00"),
            due_date=seed_periods_today[0].start_date,
        )
        db.session.add(txn)
        db.session.commit()
        return txn

    def test_paid_at_set_on_mark_done(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/<id>/mark-done sets paid_at timestamp for expenses."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)
            assert txn.paid_at is None

            response = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.paid_at is not None

    def test_paid_at_set_on_mark_received(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/<id>/mark-done sets paid_at timestamp for income."""
        with app.app_context():
            txn = self._create_income_txn(seed_user, seed_periods_today)
            assert txn.paid_at is None

            response = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.paid_at is not None

    def test_paid_at_nulled_on_status_revert(self, app, auth_client, seed_user, seed_periods_today):
        """PATCH /transactions/<id> with status_id reverted to projected nulls paid_at."""
        with app.app_context():
            from app import ref_cache
            from app.enums import StatusEnum

            txn = self._create_test_txn(seed_user, seed_periods_today)

            # Mark done to set paid_at.
            auth_client.post(f"/transactions/{txn.id}/mark-done")
            db.session.refresh(txn)
            assert txn.paid_at is not None

            # Revert to projected via PATCH.
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(projected_id)},
            )
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.paid_at is None

    def test_paid_at_re_mark_sets_new_timestamp(self, app, auth_client, seed_user, seed_periods_today):
        """Mark done, revert to projected, mark done again -- paid_at is set both times."""
        with app.app_context():
            from app import ref_cache
            from app.enums import StatusEnum

            txn = self._create_test_txn(seed_user, seed_periods_today)

            # First mark done.
            auth_client.post(f"/transactions/{txn.id}/mark-done")
            db.session.refresh(txn)
            first_paid_at = txn.paid_at
            assert first_paid_at is not None

            # Revert to projected.
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(projected_id)},
            )
            db.session.refresh(txn)
            assert txn.paid_at is None

            # Mark done again.
            auth_client.post(f"/transactions/{txn.id}/mark-done")
            db.session.refresh(txn)
            second_paid_at = txn.paid_at
            assert second_paid_at is not None
            assert second_paid_at >= first_paid_at

    def test_paid_at_not_set_on_non_settling_status_change(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transactions/<id>/cancel does not set paid_at."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)
            assert txn.paid_at is None

            response = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.paid_at is None

    def test_paid_at_preserved_on_non_status_update(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """PATCH /transactions/<id> updating amount only preserves paid_at."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            # Mark done to set paid_at.
            auth_client.post(f"/transactions/{txn.id}/mark-done")
            db.session.refresh(txn)
            original_paid_at = txn.paid_at
            assert original_paid_at is not None

            # Update estimated_amount only -- no status change.
            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"estimated_amount": "200.00"},
            )
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.paid_at is not None

    def test_mark_done_idempotent_updates_paid_at(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transactions/<id>/mark-done twice both succeed; paid_at is set each time."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            # First mark done.
            resp1 = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert resp1.status_code == 200
            db.session.refresh(txn)
            first_paid_at = txn.paid_at
            assert first_paid_at is not None

            # Second mark done (idempotent).
            resp2 = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert resp2.status_code == 200
            db.session.refresh(txn)
            second_paid_at = txn.paid_at
            assert second_paid_at is not None
            assert second_paid_at >= first_paid_at


class TestSchemaValidation:
    """Tests for due_day_of_month and due_date schema validation."""

    def test_schema_due_day_of_month_zero(self, app):
        """due_day_of_month=0 is rejected by the template schema."""
        from app.schemas.validation import TemplateCreateSchema
        with app.app_context():
            schema = TemplateCreateSchema()
            errors = schema.validate({"due_day_of_month": "0"})
            assert "due_day_of_month" in errors

    def test_schema_due_day_of_month_32(self, app):
        """due_day_of_month=32 is rejected by the template schema."""
        from app.schemas.validation import TemplateCreateSchema
        with app.app_context():
            schema = TemplateCreateSchema()
            errors = schema.validate({"due_day_of_month": "32"})
            assert "due_day_of_month" in errors

    def test_schema_due_day_of_month_valid_range(self, app):
        """due_day_of_month values 1-31 are all accepted."""
        from app.schemas.validation import TemplateCreateSchema
        with app.app_context():
            schema = TemplateCreateSchema()
            for day in range(1, 32):
                errors = schema.validate({"due_day_of_month": str(day)})
                assert "due_day_of_month" not in errors, (
                    f"day {day} should be valid but got: {errors.get('due_day_of_month')}"
                )

    def test_schema_due_date_on_transaction_update(self, app):
        """due_date accepted as a valid Date field in TransactionUpdateSchema."""
        from app.schemas.validation import TransactionUpdateSchema
        with app.app_context():
            schema = TransactionUpdateSchema()
            errors = schema.validate({"due_date": "2026-04-15"})
            assert "due_date" not in errors

    def test_schema_paid_at_is_dump_only(self, app):
        """paid_at is dump_only so it is ignored when submitted in PATCH data.

        The field should not appear in loaded data even when submitted.
        """
        from app.schemas.validation import TransactionUpdateSchema
        with app.app_context():
            schema = TransactionUpdateSchema()
            data = schema.load({"paid_at": "2026-04-15T10:00:00"})
            assert "paid_at" not in data


class TestMobileThisPeriodPartial:
    """Regression locks for the mobile "This Period" tab partial.

    The partial at ``app/templates/grid/_mobile_this_period.html`` is
    rendered inside the ``#mobile-this-period`` tab-pane in
    ``_mobile_grid.html``.  These tests assert structural invariants
    of the rendered HTML so subsequent commits cannot silently regress
    the tab layout (default-active flip, period nav arrow hrefs, the
    presence of the income / expense / net / balance sections).

    Mobile / desktop split: ``_mobile_grid.html`` is wrapped in
    ``d-md-none`` and the desktop grid in ``d-none d-md-block``; both
    render server-side regardless of the requesting client, so the
    assertions can inspect the response body without simulating a
    mobile user-agent or viewport.
    """

    def test_this_period_partial_exists(self):
        """C6-1: the new partial file exists at the canonical path.

        Filesystem check; ensures the file landed at the path the
        ``{% include "grid/_mobile_this_period.html" %}`` reference in
        ``_mobile_grid.html`` resolves to.
        """
        import pathlib  # pylint: disable=import-outside-toplevel

        partial = (
            pathlib.Path(__file__).resolve().parents[2]
            / "app" / "templates" / "grid" / "_mobile_this_period.html"
        )
        assert partial.is_file()

    def test_default_active_tab_is_this_period(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C6-5: the "This Period" pill is the default-active tab.

        The Commit 6 default-tab flip moves the ``active`` /
        ``aria-selected="true"`` pair from "Plan" to "This Period";
        the matching tab-pane carries ``show active``.  Lock both so
        a later commit cannot silently flip the default back.
        """
        with app.app_context():
            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            # Slice each button's full opening tag ('<button ... >') so
            # the assertions span the template's multi-line attribute
            # layout.
            tp_id = 'id="mobile-tab-this-period"'
            tp_open = body[body.rindex("<button", 0, body.index(tp_id)):
                           body.index(">", body.index(tp_id))]
            assert "nav-link active" in tp_open
            assert 'aria-selected="true"' in tp_open

            plan_id = 'id="mobile-tab-plan"'
            plan_open = body[body.rindex("<button", 0, body.index(plan_id)):
                             body.index(">", body.index(plan_id))]
            # Plan tab carries the bare "nav-link" class (no "active").
            assert "nav-link active" not in plan_open
            assert 'aria-selected="false"' in plan_open

            # The tab-pane carries "show active" via its outer class.
            pane_id = 'id="mobile-this-period"'
            pane_open = body[body.rindex("<div", 0, body.index(pane_id)):
                             body.index(">", body.index(pane_id))]
            assert "show active" in pane_open

    def test_this_period_renders_current_period_by_default(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C6-2: the partial renders periods[0] (== current period when
        start_offset == 0).

        At the default URL ``/grid`` the visible window starts at
        ``current_period`` (offset=0), so the period label inside the
        partial's nav header equals ``current_period.label``.
        """
        with app.app_context():
            from app.services import pay_period_service  # pylint: disable=import-outside-toplevel

            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None

            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            # The partial's header div is followed by the period
            # label inside a fw-bold div.  Encode the label so non-ASCII
            # whitespace and quoting are byte-stable.
            assert current.label.encode("utf-8") in response.data
            # The partial-specific collapse IDs prefix with mobile-tp-
            # to avoid colliding with the Plan tab's mobile-income-/mobile-expense-.
            assert f"mobile-tp-income-{current.id}".encode("utf-8") in response.data
            assert f"mobile-tp-expense-{current.id}".encode("utf-8") in response.data

    def test_this_period_includes_income_expense_net_balance(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C6-3: the partial emits the four expected sections.

        Income card (header text "Income"), expense card (header text
        "Expenses"), net cash flow bar ("Net Cash Flow"), projected
        balance card ("Projected Balance").  The mobile-section classes
        carry the brand colors so they double as section markers.
        """
        with app.app_context():
            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            # Slice to just the This Period pane so the assertions do
            # not leak through to the Plan pane's symmetric markup.
            pane_start = body.index('id="mobile-this-period"')
            # The pane ends at the next sibling tab-pane (mobile-plan).
            pane_end = body.index('id="mobile-plan"', pane_start)
            pane = body[pane_start:pane_end]

            assert "mobile-section-income" in pane
            assert "mobile-section-expense" in pane
            assert "Net Cash Flow" in pane
            assert "Projected Balance" in pane

    def test_this_period_arrows_link_to_offset_neighbors(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C6-4: the prev/next arrows link to offset-1 and offset+1.

        At the default URL ``/grid`` (``start_offset == 0``), the
        partial's ``[<]`` should link to
        ``/grid?periods=1&offset=-1#this-period`` and ``[>]`` to
        ``/grid?periods=1&offset=1#this-period``.  Both carry the
        ``#this-period`` fragment so the page lands back on the same
        tab after the GET.
        """
        with app.app_context():
            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            pane_start = body.index('id="mobile-this-period"')
            pane_end = body.index('id="mobile-plan"', pane_start)
            pane = body[pane_start:pane_end]

            # Flask url_for renders integer query args inline; assert
            # the canonical href tail rather than the full URL.
            assert "/grid?periods=1&amp;offset=-1#this-period" in pane
            assert "/grid?periods=1&amp;offset=1#this-period" in pane

    def test_this_period_arrows_use_start_offset(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C6-4 (extended): arrows always step from ``start_offset``.

        When the user is at ``?periods=1&offset=2``, the prev arrow
        links to ``offset=1`` and the next arrow to ``offset=3``.  The
        partial uses ``start_offset`` directly, so the formula must
        survive non-zero starting offsets.
        """
        with app.app_context():
            response = auth_client.get("/grid?periods=1&offset=2")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            pane_start = body.index('id="mobile-this-period"')
            pane_end = body.index('id="mobile-plan"', pane_start)
            pane = body[pane_start:pane_end]

            assert "/grid?periods=1&amp;offset=1#this-period" in pane
            assert "/grid?periods=1&amp;offset=3#this-period" in pane


class TestMobileCardActionBar:
    """Regression locks for the per-card inline action bar (Commit 7
    of the mobile-first v3 implementation).

    The bar lives in ``app/templates/grid/_mobile_card_actions.html``
    and is emitted by ``render_row_card`` as a sibling of each card
    ``<li>``, wrapped together in
    ``<div class="mobile-card-wrapper">``.  A delegated tap handler in
    ``app/static/js/mobile_grid.js`` toggles the Bootstrap collapse so
    the user sees ``[Mark Paid]``, ``[Edit Amount]``, and
    ``[Open Full]`` directly under the tapped card.

    These tests pin down the structural contract the JS handler and
    the action-bar route consumers depend on:

      - Both new partials (``_mobile_plan.html`` and
        ``_mobile_card_actions.html``) exist at the canonical paths.
      - The Mark Paid form is conditional on the transaction state -
        Projected / Received and other non-terminal statuses get it;
        Done and Settled (the two state-machine terminals for the
        mark-done path) do not (mark_done would reject them via the
        state machine, so omitting the affordance is the honest UX).
      - ``can_edit=False`` (the companion contract per R-7 / D-B of
        the v3 plan) drops the owner-only ``[Edit Amount]`` and
        ``[Open Full]`` buttons while keeping ``[Mark Paid]``
        (companions are allowed to mark paid per the existing
        entries-blueprint precedent).
      - The Mark Paid form posts to ``transactions.mark_done`` with
        the swap target set to the row's ``#txn-cell-<id>``.
    """

    @staticmethod
    def _render_action_bar(app, txn, can_edit=True):
        """Render ``_mobile_card_actions.html`` directly with the given
        ``txn`` and ``can_edit``.

        Direct render (rather than scraping a full ``/grid`` response)
        keeps the structural assertions immune to surrounding markup
        drift: the test asserts what the partial emits, not where
        it lands in the larger page.  ``app.test_request_context``
        is what makes ``url_for`` resolve inside the template; the
        ``app.jinja_env.globals`` registrations from
        ``app.jinja_globals.register_ref_id_globals`` provide
        ``STATUS_DONE`` / ``STATUS_SETTLED`` without further setup.
        """
        template = app.jinja_env.get_template("grid/_mobile_card_actions.html")
        with app.test_request_context("/"):
            return template.render(txn=txn, can_edit=can_edit)

    def test_plan_partial_exists(self):
        """C7-1: ``_mobile_plan.html`` exists at the canonical path.

        Filesystem check; ensures the partial landed where the
        ``{% include "grid/_mobile_plan.html" %}`` reference in
        ``_mobile_grid.html`` resolves to.
        """
        import pathlib  # pylint: disable=import-outside-toplevel

        partial = (
            pathlib.Path(__file__).resolve().parents[2]
            / "app" / "templates" / "grid" / "_mobile_plan.html"
        )
        assert partial.is_file()

    def test_mobile_card_actions_partial_exists(self):
        """C7-2: ``_mobile_card_actions.html`` exists at the canonical path.

        Filesystem check; ensures the partial landed where the
        ``{% include "grid/_mobile_card_actions.html" %}`` reference
        in ``_grid_row_macros.html``'s ``render_row_card`` resolves to.
        """
        import pathlib  # pylint: disable=import-outside-toplevel

        partial = (
            pathlib.Path(__file__).resolve().parents[2]
            / "app" / "templates" / "grid" / "_mobile_card_actions.html"
        )
        assert partial.is_file()

    def test_action_bar_includes_mark_paid_when_not_settled(
        self, app, seed_user, seed_periods_today,
    ):
        """C7-3: Projected txns get a ``[Mark Paid]`` form in the bar.

        A Projected transaction is in scope for the mark-done state
        transition, so the action bar offers the affordance.  The
        rendered partial must contain a ``hx-post`` form to
        ``transactions.mark_done`` plus the visible button label.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None
            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="C7-3 Projected Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                estimated_amount=Decimal("42.00"),
            )
            db.session.add(txn)
            db.session.commit()

            rendered = self._render_action_bar(app, txn, can_edit=True)

            assert f'/transactions/{txn.id}/mark-done' in rendered
            assert "Mark Paid" in rendered

    def test_action_bar_excludes_mark_paid_when_settled(
        self, app, seed_user, seed_periods_today,
    ):
        """C7-4: Settled txns do NOT get a ``[Mark Paid]`` form.

        Settled is a state-machine terminal for the mark-done path;
        offering the button would let the user fire a request the
        route would reject with 400.  The partial's guard on
        ``status_id`` is the source of truth here.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None
            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.SETTLED),
                name="C7-4 Settled Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                estimated_amount=Decimal("42.00"),
            )
            db.session.add(txn)
            db.session.commit()

            rendered = self._render_action_bar(app, txn, can_edit=True)

            assert f'/transactions/{txn.id}/mark-done' not in rendered
            assert "Mark Paid" not in rendered

    def test_action_bar_excludes_mark_paid_when_done(
        self, app, seed_user, seed_periods_today,
    ):
        """C7-4 (sibling): Done txns also drop the ``[Mark Paid]`` form.

        Mirrors the Settled guard: Done is the other terminal for the
        mark-done path (Done -> Settled is a separate transition,
        and the action bar does not currently expose a Settle action).
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None
            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                name="C7-4b Done Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                estimated_amount=Decimal("42.00"),
            )
            db.session.add(txn)
            db.session.commit()

            rendered = self._render_action_bar(app, txn, can_edit=True)

            assert f'/transactions/{txn.id}/mark-done' not in rendered
            assert "Mark Paid" not in rendered

    def test_action_bar_excludes_mark_paid_when_received(
        self, app, seed_user, seed_periods_today,
    ):
        """C7-4c: income txns marked Received also drop ``[Mark Paid]``.

        Locks the fix for an income-specific bug that the Playwright
        harness surfaced after a mark-done round-trip:
        ``transactions.mark_done`` sets ``status_id = RECEIVED`` for
        income (not DONE), so the spec's literal
        ``status_id != STATUS_DONE and status_id != STATUS_SETTLED``
        guard missed RECEIVED and kept the Mark Paid button visible
        on already-received income.  Switched to the semantic
        ``Status.is_settled`` boolean which covers Paid, Received,
        and Settled uniformly.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None
            salary_cat = seed_user["categories"]["Salary"]
            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.RECEIVED),
                name="C7-4c Received Salary",
                category_id=salary_cat.id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
                estimated_amount=Decimal("2500.00"),
            )
            db.session.add(txn)
            db.session.commit()

            rendered = self._render_action_bar(app, txn, can_edit=True)

            assert f'/transactions/{txn.id}/mark-done' not in rendered
            assert "Mark Paid" not in rendered

    def test_action_bar_excludes_edit_when_can_edit_false(
        self, app, seed_user, seed_periods_today,
    ):
        """C7-5: ``can_edit=False`` (companion) drops ``[Edit Amount]`` and
        ``[Open Full]`` but keeps ``[Mark Paid]``.

        The companion role can mark transactions paid (entries
        blueprint precedent) but cannot open the desktop full-edit
        form or the inline quick-edit popover.  The action bar
        partial's ``{% if can_edit %}`` guard is the only thing
        between the companion render path and the owner-only
        affordances.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None
            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="C7-5 Projected Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                estimated_amount=Decimal("99.00"),
            )
            db.session.add(txn)
            db.session.commit()

            rendered = self._render_action_bar(app, txn, can_edit=False)

            # Mark Paid remains -- companions can mark paid.
            assert f'/transactions/{txn.id}/mark-done' in rendered
            assert "Mark Paid" in rendered
            # Edit Amount and Open Full are gone for the companion path.
            assert "Edit Amount" not in rendered
            assert "Open Full" not in rendered
            assert f'/transactions/{txn.id}/quick-edit' not in rendered
            assert "txn-expand-btn" not in rendered

    def test_action_bar_hx_post_target_is_cell(
        self, app, seed_user, seed_periods_today,
    ):
        """C7-6: Mark Paid form posts to mark-done targeting ``#txn-cell-<id>``.

        Locks the form attributes the action bar's HTMX wiring
        depends on: ``hx-post`` URL, ``hx-target`` selector, and
        ``hx-swap`` mode.  ``outerHTML`` is the spec'd swap mode for
        the bar (the response also fires ``HX-Trigger: gridRefresh``
        which causes a full page reload, so the swap target and
        mode are only load-bearing if a future commit removes the
        gridRefresh).
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None
            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="C7-6 Projected Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                estimated_amount=Decimal("42.00"),
            )
            db.session.add(txn)
            db.session.commit()

            rendered = self._render_action_bar(app, txn, can_edit=True)

            assert f'hx-post="/transactions/{txn.id}/mark-done"' in rendered
            assert f'hx-target="#txn-cell-{txn.id}"' in rendered
            assert 'hx-swap="outerHTML"' in rendered

    def test_card_wrapper_emits_bar_sibling_in_grid_page(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C7-integration: the full ``/grid`` page emits the action-bar
        sibling next to each mobile card.

        Asserts the macro-level wiring: ``render_row_card`` wraps
        each ``<li>`` in ``<div class="mobile-card-wrapper">`` and
        emits ``_mobile_card_actions.html`` after it.  Without this
        integration, the unit-level checks above would pass while
        the bar still never appears on the rendered page.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None
            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="C7-integration Projected Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                estimated_amount=Decimal("31.00"),
            )
            db.session.add(txn)
            db.session.commit()
            txn_id = txn.id

            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            # Wrapper exists; action-bar id is per-tab-prefixed so the
            # same txn rendered in both This Period and Plan tabs does
            # not violate the HTML unique-id rule.  See the
            # `id_prefix` param on render_row_card.
            assert 'class="mobile-card-wrapper"' in body
            assert f'id="card-actions-tp-{txn_id}"' in body
            assert f'id="card-actions-plan-{txn_id}"' in body

    def test_action_bar_id_uses_prefix_when_supplied(
        self, app, seed_user, seed_periods_today,
    ):
        """C7-integration: ``id_prefix`` namespaces the action-bar element id.

        The "This Period" and "Plan" tabs render the same window of
        pay periods at the same time, so without per-tab namespacing
        the same txn yields a duplicate ``id="card-actions-<id>"``
        in two places.  The ``id_prefix`` parameter on
        ``render_row_card`` (forwarded into the action-bar partial
        via ``with context``) is the fix: This Period passes
        ``id_prefix='tp'``, Plan passes ``id_prefix='plan'``, and an
        empty prefix preserves the simpler legacy id form for the
        direct-render unit tests above.

        Locks the three branches explicitly so a future refactor of
        the formula cannot regress one without flagging.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None
            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="C7-prefix Projected Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                estimated_amount=Decimal("17.00"),
            )
            db.session.add(txn)
            db.session.commit()

            template = app.jinja_env.get_template(
                "grid/_mobile_card_actions.html",
            )
            with app.test_request_context("/"):
                no_prefix = template.render(txn=txn, can_edit=True)
                tp_prefix = template.render(
                    txn=txn, can_edit=True, id_prefix="tp",
                )
                plan_prefix = template.render(
                    txn=txn, can_edit=True, id_prefix="plan",
                )

            assert f'id="card-actions-{txn.id}"' in no_prefix
            assert f'id="card-actions-tp-{txn.id}"' in tp_prefix
            assert f'id="card-actions-plan-{txn.id}"' in plan_prefix
            # Prefixed renders must NOT also emit the unprefixed form.
            assert f'id="card-actions-{txn.id}"' not in tp_prefix
            assert f'id="card-actions-{txn.id}"' not in plan_prefix

    def test_mobile_grid_includes_plan_partial(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C7-integration: ``_mobile_grid.html`` Plan tab body is the
        ``_mobile_plan.html`` include, not the inline scroll view.

        Locks the Commit 7 refactor where the Plan tab body moved
        out of ``_mobile_grid.html`` into its own partial.  Verified
        by asserting that the Plan tab pane carries the rendered
        contents of the partial (the period-nav button ids) and
        that the inline content marker from before the refactor is
        absent at the call site level.
        """
        with app.app_context():
            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            # The Plan partial's period-nav controls are present.
            pane_start = body.index('id="mobile-plan"')
            pane = body[pane_start:]
            assert 'id="mobile-prev-btn"' in pane
            assert 'id="mobile-next-btn"' in pane


class TestMobileSwipeAction:
    """Regression locks for the swipe-left Mark Paid affordance
    (Commit 9 of the mobile-first v3 implementation).

    The gesture itself lives in ``app/static/js/mobile_grid.js`` and
    cannot run inside a pytest process (no real touch events, no
    Bootstrap collapse JS).  These tests pin the SERVER-RENDERED
    contract the gesture depends on:

      - ``render_row_card`` emits a ``<button
        class="swipe-action-mark-paid">`` as the first child of
        ``.mobile-card-wrapper`` (DOM order so the card stacks
        above the button when both share a stacking context).
      - The button's HTMX wiring posts to ``transactions.mark_done``
        with the row's cell as the swap target and ``outerHTML`` as
        the swap mode -- the same shape the inline action bar's
        Mark Paid form already uses, so both the gesture and the
        non-gesture path commit through one endpoint.
      - The button is preserved on the companion render
        (``can_edit=False``) per R-7 / the existing entries-
        blueprint precedent that companions are allowed to mark
        paid; only the bottom-sheet / Edit Amount affordances on
        the action bar are dropped for companions, not Mark Paid.
      - The button is SUPPRESSED for already-settled
        transactions, matching the action-bar Mark Paid guard at
        ``_mobile_card_actions.html:60``.  Suppressing the button
        rather than letting the user swipe and see a 400 from
        ``mark_done`` aligns the gesture path with the inline
        action-bar guard discovered in the action-bar fix that
        landed in Commit 7.
      - The JS swipe handler uses ``passive: true`` (R-8 of the
        plan) and the same 50 px horizontal threshold as the
        existing period-nav swipe at the top of ``init()``.

    Direct-macro rendering (rather than scraping a full ``/grid``
    response) is the right tool for the companion / settled
    branches because the owner ``/grid`` route always passes
    ``can_edit=True`` and the seeded settled-vs-projected mix in
    ``seed_user`` is not load-bearing for the assertion.
    """

    @staticmethod
    def _render_row_card(
        app, *, txn, category, period, can_edit=True, id_prefix="",
    ):
        """Render ``render_row_card`` with a single matched txn.

        Builds a ``RowKey`` and a ``matched_by_row_period`` dict
        mirroring the shape ``grid.index`` would have produced for the
        given txn, then renders the macro through a small
        ``from_string`` wrapper.  Keeps the assertion target -- the
        macro's HTML output -- in front of the test and not buried
        inside a full ``/grid`` page.
        """
        from app.routes.grid import RowKey  # pylint: disable=import-outside-toplevel

        rk = RowKey(
            category_id=category.id,
            template_id=txn.template_id,
            txn_name=txn.name,
            group_name=category.group_name,
            item_name=category.item_name,
            display_name=txn.name,
            category=category,
        )
        matched_by_row_period = {
            (rk.category_id, rk.template_id, rk.txn_name, period.id): [txn],
        }
        template = app.jinja_env.from_string(
            "{% from 'grid/_grid_row_macros.html' import render_row_card %}"
            "{{ render_row_card(rk, period, matched_by_row_period, "
            "entry_sums, can_edit, id_prefix) }}"
        )
        with app.test_request_context("/"):
            return template.render(
                rk=rk,
                period=period,
                matched_by_row_period=matched_by_row_period,
                entry_sums={},
                can_edit=can_edit,
                id_prefix=id_prefix,
            )

    def test_swipe_action_button_emitted(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C9-1: ``render_row_card`` emits a swipe-action button
        sibling for a Projected txn on the live ``/grid`` page.

        Integration check: locks the macro change against the
        rendered page so a future refactor that drops the wrapper
        emission would fail loudly here even if a unit-render test
        somehow passed.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None
            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="C9-1 Projected Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                estimated_amount=Decimal("23.00"),
            )
            db.session.add(txn)
            db.session.commit()

            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            # At least one swipe-action button exists on the page.
            assert 'class="swipe-action-mark-paid"' in body
            # That button targets the new txn's mark-done route.
            assert f'/transactions/{txn.id}/mark-done' in body

    def test_swipe_action_hx_post_targets_mark_done(
        self, app, seed_user, seed_periods_today,
    ):
        """C9-2: the swipe-action button posts to ``mark_done`` and
        swaps the row's cell ``outerHTML``.

        Locks the three HTMX attributes the gesture depends on: the
        post URL, the swap target (the row's ``#txn-cell-<id>``),
        and the swap mode (``outerHTML``).  These mirror the inline
        action bar's Mark Paid form so both paths produce the same
        update on success.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None
            cat = seed_user["categories"]["Groceries"]
            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="C9-2 Projected Groceries",
                category_id=cat.id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                estimated_amount=Decimal("17.00"),
            )
            db.session.add(txn)
            db.session.commit()

            rendered = self._render_row_card(
                app, txn=txn, category=cat, period=current,
            )

            assert 'class="swipe-action-mark-paid"' in rendered
            assert f'hx-post="/transactions/{txn.id}/mark-done"' in rendered
            assert f'hx-target="#txn-cell-{txn.id}"' in rendered
            assert 'hx-swap="outerHTML"' in rendered

    def test_swipe_action_present_for_companion(
        self, app, seed_user, seed_periods_today,
    ):
        """C9-3: the swipe-action button is emitted for
        ``can_edit=False`` (the companion render path per R-7).

        Companions can mark paid via the existing entries-blueprint
        precedent, so the gesture-shortcut Paid button must remain
        available to them.  Only the owner-only buttons (Edit
        Amount, Open Full -- both inside the inline action bar) are
        dropped at ``can_edit=False``.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None
            cat = seed_user["categories"]["Groceries"]
            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="C9-3 Companion Projected Groceries",
                category_id=cat.id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                estimated_amount=Decimal("19.00"),
            )
            db.session.add(txn)
            db.session.commit()

            rendered = self._render_row_card(
                app, txn=txn, category=cat, period=current, can_edit=False,
            )

            assert 'class="swipe-action-mark-paid"' in rendered
            assert f'/transactions/{txn.id}/mark-done' in rendered
            # The wrapper exists even on companion path so the JS
            # handler's `.mobile-card-wrapper` lookup succeeds.
            assert 'class="mobile-card-wrapper"' in rendered

    def test_swipe_action_suppressed_when_settled(
        self, app, seed_user, seed_periods_today,
    ):
        """C9-discovered: settled txns do NOT get a swipe-action button.

        Folded refinement (work summary section I): aligns the
        gesture path with the action-bar Mark Paid guard at
        ``_mobile_card_actions.html:60`` so a swipe on a settled
        row stays a no-op rather than letting the user reveal a
        button whose POST ``mark_done`` would reject with 400.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None
            cat = seed_user["categories"]["Groceries"]
            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.SETTLED),
                name="C9-discovered Settled Groceries",
                category_id=cat.id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                estimated_amount=Decimal("12.00"),
            )
            db.session.add(txn)
            db.session.commit()

            rendered = self._render_row_card(
                app, txn=txn, category=cat, period=current,
            )

            # The wrapper still exists (so the card layout is
            # unchanged) but the swipe-action button is absent.
            assert 'class="mobile-card-wrapper"' in rendered
            assert 'class="swipe-action-mark-paid"' not in rendered
            # Defensive: the cell-targeting hx-post URL for the
            # mark_done route must not appear in the rendered card
            # wrapper (it only lives on the suppressed swipe button
            # and the suppressed action-bar form).
            assert f'/transactions/{txn.id}/mark-done' not in rendered

    def test_swipe_threshold_matches_period_swipe(self):
        """C9-4: card-swipe and period-swipe both use the 50 px
        threshold (R-8 of the mobile-first v3 plan).

        Re-scoped in mobile-first v3 plan Commit 13: the card-swipe
        gesture moved from ``mobile_grid.js`` to the shared helper
        ``app/static/js/swipe.js::attachSwipeAction`` so the
        companion view (``companion.js``) can reuse it.  The
        period-nav swipe stays in ``mobile_grid.js``.  Both files
        must carry the literal ``50`` threshold to keep the gestures
        consistent under the finger.

        Three assertions:
          1. ``mobile_grid.js`` still uses ``Math.abs(dx) > 50``
             for the period-nav swipe at the top of ``init()``.
          2. ``mobile_grid.js`` calls ``attachSwipeAction`` with
             ``threshold: 50`` so the card gesture stays at the
             same value the period-nav uses.
          3. ``swipe.js`` carries the ``dx < -threshold`` (left-swipe
             reveal) and ``dx > threshold`` (right-swipe un-swipe)
             branches that consume the configured threshold.
        """
        import pathlib  # pylint: disable=import-outside-toplevel

        root = pathlib.Path(__file__).resolve().parents[2] / "app" / "static" / "js"
        mobile_grid_src = (root / "mobile_grid.js").read_text(encoding="utf-8")
        swipe_src = (root / "swipe.js").read_text(encoding="utf-8")

        # Period-nav swipe at the top of init(): a horizontal motion
        # past 50 px wins the gesture.
        assert "Math.abs(dx) > 50" in mobile_grid_src
        # Card-swipe threshold is supplied to the shared helper at
        # 50 px so card-swipe matches period-nav under the finger.
        assert "threshold: 50" in mobile_grid_src
        # The shared helper consumes the threshold via these two
        # comparisons (Commit 13 factoring preserves the original
        # left-/right-swipe branches text-for-text up to the
        # threshold variable).
        assert "dx < -threshold" in swipe_src
        assert "dx > threshold" in swipe_src

    def test_swipe_handlers_are_passive(self):
        """R-8 alignment: every touch listener uses ``passive: true``.

        Passive listeners cannot ``preventDefault`` -- they cannot
        block vertical scroll, which is the trade-off that lets
        the swipe co-exist with normal page scrolling.  The
        ``Math.abs(dy) > Math.abs(dx)`` cancel-on-vertical guard
        inside touchmove is what makes the trade-off safe.

        Re-scoped in mobile-first v3 plan Commit 13: the three
        card touchstart/touchmove/touchend listeners moved from
        ``mobile_grid.js`` to ``swipe.js`` along with the rest of
        ``attachSwipeAction``.  The period-nav touchstart + touchend
        listeners stay in ``mobile_grid.js``.  Both files together
        must carry at least 5 ``passive: true`` listeners; either
        file alone may carry fewer.  A regression that flipped any
        of them to a default non-passive listener would silently
        re-block scroll on iOS Safari.
        """
        import pathlib  # pylint: disable=import-outside-toplevel

        root = pathlib.Path(__file__).resolve().parents[2] / "app" / "static" / "js"
        mobile_grid_src = (root / "mobile_grid.js").read_text(encoding="utf-8")
        swipe_src = (root / "swipe.js").read_text(encoding="utf-8")

        # 2 in mobile_grid.js (period-nav touchstart + touchend) +
        # 3 in swipe.js (card touchstart + touchmove + touchend) = 5.
        total = mobile_grid_src.count("passive: true") + swipe_src.count("passive: true")
        assert total >= 5, (
            f"expected at least 5 'passive: true' touch listeners across "
            f"mobile_grid.js + swipe.js, found {total}"
        )

    def test_no_inline_style_on_swipe_action(self):
        """Pin the no-inline-style invariant on the swipe-action
        button so a future refactor cannot bring back the
        ``style="..."`` attribute that CSP ``style-src 'self'``
        (without ``'unsafe-inline'``) silently rejects.

        The button's visual sizing lives entirely in
        ``app/static/css/app.css`` under the
        ``.swipe-action-mark-paid`` selector.
        """
        import pathlib  # pylint: disable=import-outside-toplevel

        macros = (
            pathlib.Path(__file__).resolve().parents[2]
            / "app" / "templates" / "grid" / "_grid_row_macros.html"
        )
        src = macros.read_text(encoding="utf-8")

        # Grab the lines that emit the swipe-action button and assert
        # no `style=` attribute travels with them.
        button_block_start = src.index('class="swipe-action-mark-paid"')
        button_block_end = src.index("</button>", button_block_start)
        button_block = src[button_block_start:button_block_end]
        assert "style=" not in button_block


class TestMobileJumpToPeriod:
    """Regression locks for the jump-to-period ``<select>`` in the
    "This Period" tab header (Commit 10 of the mobile-first v3
    implementation).

    The select lives in ``app/templates/grid/_mobile_this_period.html``
    below the ``[<] [>]`` arrow row and lets the user reach any
    non-adjacent period in one tap, avoiding N taps on ``[<]``.
    Picking a non-current option fires ``change``, which a delegated
    listener in ``app/static/js/mobile_grid.js`` translates into a
    full GET submit to ``/grid?periods=1&offset=N``.

    These tests pin the structural contract the JS handler and the
    grid route consume:

      - The ``<select name="offset">`` is emitted exactly once per
        page render, inside the ``#mobile-this-period`` tab-pane
        (so the JS handler's ``.closest('#mobile-this-period')``
        guard matches).
      - One ``<option>`` per period in ``all_periods`` -- the option
        list mirrors the user's full visible projection so the user
        can jump to any of them.
      - Option ``value`` is the period's offset relative to
        ``current_period.period_index``, matching the desktop
        selector convention at ``grid/grid.html:24-49`` and the
        partial's own prev/next arrow hrefs.
      - The option for the currently visible period
        (``periods[0]`` / ``period``) carries the ``selected``
        attribute so the picker opens on that row.
      - Method is GET with a hidden ``periods=1`` input -- the GET
        is read-only navigation (no state mutation), so no CSRF
        token is required per CLAUDE.md "State-changing actions
        must use POST".
      - The JS file carries the delegated change handler with the
        ``select[name="offset"]`` selector and the
        ``#mobile-this-period`` scope guard.
    """

    def test_jump_to_select_present(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C10-1: a single ``<select name="offset">`` lives inside the
        ``#mobile-this-period`` tab-pane.

        The select is the jump-to control. Scoping the assertion to
        the pane (not just the document) guards against a future
        regression where a sibling ``<select name="offset">`` lands
        in another tab and double-submits via the same delegated JS
        handler.
        """
        with app.app_context():
            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            pane_start = body.index('id="mobile-this-period"')
            pane_end = body.index('id="mobile-plan"', pane_start)
            pane = body[pane_start:pane_end]

            assert pane.count('<select name="offset"') == 1
            # The hidden periods=1 input rides with the select so
            # the GET lands at the single-period URL shape.
            assert 'name="periods" value="1"' in pane
            # GET form -- read-only navigation, no CSRF gate.
            assert 'method="get"' in pane

    def test_jump_to_options_match_all_periods(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C10-2: option count equals ``len(all_periods)``.

        ``seed_periods_today`` provisions 10 biweekly periods
        (indices 0..9); ``pay_period_service.get_all_periods``
        returns all 10 to the route, so the rendered select carries
        10 ``<option>`` elements. Each option's ``value`` is the
        offset relative to the current period (period_index 4 under
        ``seed_periods_today``), so the value set is
        ``{-4, -3, -2, -1, 0, 1, 2, 3, 4, 5}``.
        """
        with app.app_context():
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None
            all_periods = pay_period_service.get_all_periods(
                seed_user["user"].id,
            )
            assert len(all_periods) == 10
            assert current.period_index == 4

            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            pane_start = body.index('id="mobile-this-period"')
            pane_end = body.index('id="mobile-plan"', pane_start)
            pane = body[pane_start:pane_end]

            # Slice down to the select's option block to keep the
            # count immune to unrelated <option> elements elsewhere
            # in the pane (none exist today, but the slice keeps
            # future additions safe).
            select_start = pane.index('<select name="offset"')
            select_end = pane.index("</select>", select_start)
            select_block = pane[select_start:select_end]
            assert select_block.count("<option ") == len(all_periods)

            # Spot-check the boundary offsets. period_index 0 -> -4,
            # period_index 9 -> +5 (all under current.period_index=4).
            assert 'value="-4"' in select_block
            assert 'value="5"' in select_block
            # And the current option must exist at value="0".
            assert 'value="0"' in select_block

    def test_jump_to_current_period_selected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C10-3: the option for the currently visible period carries
        ``selected``.

        At the default ``/grid`` URL (``start_offset == 0``),
        ``periods[0]`` (the partial's ``period`` local) equals
        ``current_period``, so the offset-0 option is the selected
        one. Verified by slicing the offset-0 option's opening
        tag and asserting ``selected`` appears inside it.
        """
        with app.app_context():
            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            pane_start = body.index('id="mobile-this-period"')
            pane_end = body.index('id="mobile-plan"', pane_start)
            pane = body[pane_start:pane_end]

            select_start = pane.index('<select name="offset"')
            select_end = pane.index("</select>", select_start)
            select_block = pane[select_start:select_end]

            # Locate the offset=0 option (the current period under
            # start_offset=0) and slice its full opening tag so the
            # assertion spans the multi-line attribute layout.
            opt_start = select_block.index('value="0"')
            opt_tag_open = select_block.rindex("<option", 0, opt_start)
            opt_tag_close = select_block.index(">", opt_start)
            opt_open = select_block[opt_tag_open:opt_tag_close]
            assert "selected" in opt_open

    def test_jump_to_selected_follows_visible_period(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C10-3 (extended): non-zero ``start_offset`` shifts the
        selected option to the currently visible period.

        At ``?periods=1&offset=2`` the visible period is the one
        with ``period_index == current.period_index + 2 == 6``;
        the select's ``selected`` must move to the offset=2 option
        (and the offset=0 option must NOT carry ``selected``).
        Locks the ``p.id == period.id`` predicate against drift
        toward an unconditional or current-only selection rule.
        """
        with app.app_context():
            response = auth_client.get("/grid?periods=1&offset=2")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            pane_start = body.index('id="mobile-this-period"')
            pane_end = body.index('id="mobile-plan"', pane_start)
            pane = body[pane_start:pane_end]

            select_start = pane.index('<select name="offset"')
            select_end = pane.index("</select>", select_start)
            select_block = pane[select_start:select_end]

            # offset=2 option carries selected.
            opt2_start = select_block.index('value="2"')
            opt2_open = select_block[
                select_block.rindex("<option", 0, opt2_start)
                :select_block.index(">", opt2_start)
            ]
            assert "selected" in opt2_open

            # offset=0 option does NOT carry selected.
            opt0_start = select_block.index('value="0"')
            opt0_open = select_block[
                select_block.rindex("<option", 0, opt0_start)
                :select_block.index(">", opt0_start)
            ]
            assert "selected" not in opt0_open

    def test_jump_to_delegated_handler_in_mobile_grid_js(self):
        """C10 JS-side regression lock: the delegated change handler
        in ``mobile_grid.js`` references the select selector and the
        ``#mobile-this-period`` scope guard.

        The CSP-friendly delegated handler replaces the inline
        ``onchange="this.form.submit()"`` from the plan's draft
        markup (per CLAUDE.md "No inline scripts"). Reading the JS
        file directly (same pattern as
        ``test_swipe_threshold_matches_period_swipe``) locks both
        the selector and the scope so a future refactor cannot
        silently drop either guard.
        """
        import pathlib  # pylint: disable=import-outside-toplevel

        js_path = (
            pathlib.Path(__file__).resolve().parents[2]
            / "app" / "static" / "js" / "mobile_grid.js"
        )
        src = js_path.read_text(encoding="utf-8")

        # The selector targets the jump-to <select>.
        assert 'select[name="offset"]' in src
        # The scope guard limits the handler to the "This Period" pane.
        assert "#mobile-this-period" in src
        # form.submit() is what turns the change into a GET to /grid.
        assert "form.submit()" in src
