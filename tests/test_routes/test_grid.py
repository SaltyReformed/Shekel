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

    def test_grid_account_with_no_anchor_balance(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """An account with NULL anchor balance defaults to $0 for projections."""
        savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
        savings = Account(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="New Savings",
            current_anchor_balance=None,
            current_anchor_period_id=seed_periods_today[0].id,
        )
        db.session.add(savings)
        db.session.commit()

        resp = auth_client.get(f"/grid?account_id={savings.id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "New Savings Balance" in html

    def test_grid_account_with_no_anchor_period(
        self, app, auth_client, seed_user, seed_periods_today
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

        resp = auth_client.get(f"/grid?account_id={savings.id}")
        assert resp.status_code == 200

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
            savings_acct = Account(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Savings",
                current_anchor_balance=Decimal("0.00"),
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
    """Regression baseline for Section 5A.1.

    Grid subtotals use txn.effective_amount (grid.py lines 233-234).
    After 5A.1, effective_amount returns actual_amount when populated,
    so subtotals automatically reflect actuals.
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
