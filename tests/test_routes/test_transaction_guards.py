"""
Shekel Budget App -- Transaction Route Guard Tests

Tests for transfer detection guards on every transaction mutation route.
Verifies shadow transactions route through the transfer service, blocked
operations return 400, and regular transactions are unaffected.
"""

from decimal import Decimal

import pytest

from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.models.ref import AccountType, Status, TransactionType
from app.services import transfer_service


def _create_savings(seed_user):
    """Create a savings account for the test user."""
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    acct = Account(
        user_id=seed_user["user"].id,
        account_type_id=savings_type.id,
        name="Savings",
        current_anchor_balance=Decimal("0"),
    )
    db.session.add(acct)
    db.session.flush()
    return acct


def _create_test_transfer(seed_user, seed_periods_today):
    """Create a transfer with shadows via the service.  Returns (transfer, expense_shadow, income_shadow)."""
    savings = _create_savings(seed_user)
    projected = db.session.query(Status).filter_by(name="Projected").one()
    xfer = transfer_service.create_transfer(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=savings.id,
        pay_period_id=seed_periods_today[0].id,
        scenario_id=seed_user["scenario"].id,
        amount=Decimal("300.00"),
        status_id=projected.id,
        category_id=seed_user["categories"]["Rent"].id,
        name="Test Transfer",
    )
    db.session.commit()

    expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
    income_type = db.session.query(TransactionType).filter_by(name="Income").one()
    shadows = db.session.query(Transaction).filter_by(transfer_id=xfer.id).all()
    expense = [s for s in shadows if s.transaction_type_id == expense_type.id][0]
    income = [s for s in shadows if s.transaction_type_id == income_type.id][0]
    return xfer, expense, income


def _create_regular_txn(seed_user, seed_periods_today):
    """Create a regular transaction (no transfer_id)."""
    projected = db.session.query(Status).filter_by(name="Projected").one()
    expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
    txn = Transaction(
        account_id=seed_user["account"].id,
        pay_period_id=seed_periods_today[0].id,
        scenario_id=seed_user["scenario"].id,
        status_id=projected.id,
        name="Regular Expense",
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=expense_type.id,
        estimated_amount=Decimal("50.00"),
    )
    db.session.add(txn)
    db.session.commit()
    return txn


# ── Update Guards ──────────────────────────────────────────────────


class TestUpdateShadowGuard:
    """Tests for PATCH /transactions/<id> on shadow transactions."""

    def test_update_shadow_routes_through_service(
        self, app, db, auth_client, seed_user, seed_periods_today
    ):
        """Amount change on shadow updates transfer and both shadows."""
        with app.app_context():
            xfer, expense, income = _create_test_transfer(seed_user, seed_periods_today)

            resp = auth_client.patch(
                f"/transactions/{expense.id}",
                data={"estimated_amount": "500.00"},
            )

            assert resp.status_code == 200
            assert resp.headers.get("HX-Trigger") == "balanceChanged"

            db.session.expire_all()
            xfer = db.session.get(Transfer, xfer.id)
            expense = db.session.get(Transaction, expense.id)
            income = db.session.get(Transaction, income.id)
            assert xfer.amount == Decimal("500.00")
            assert expense.estimated_amount == Decimal("500.00")
            assert income.estimated_amount == Decimal("500.00")

    def test_update_shadow_actual_amount(
        self, app, db, auth_client, seed_user, seed_periods_today
    ):
        """actual_amount change on shadow propagates to both shadows."""
        with app.app_context():
            xfer, expense, income = _create_test_transfer(seed_user, seed_periods_today)

            resp = auth_client.patch(
                f"/transactions/{expense.id}",
                data={"actual_amount": "290.00"},
            )

            assert resp.status_code == 200
            db.session.expire_all()
            expense = db.session.get(Transaction, expense.id)
            income = db.session.get(Transaction, income.id)
            assert expense.actual_amount == Decimal("290.00")
            assert income.actual_amount == Decimal("290.00")

    def test_update_shadow_status(
        self, app, db, auth_client, seed_user, seed_periods_today
    ):
        """Status change on shadow propagates to transfer and both shadows."""
        with app.app_context():
            xfer, expense, income = _create_test_transfer(seed_user, seed_periods_today)
            done = db.session.query(Status).filter_by(name="Paid").one()

            resp = auth_client.patch(
                f"/transactions/{expense.id}",
                data={"status_id": str(done.id)},
            )

            assert resp.status_code == 200
            db.session.expire_all()
            xfer = db.session.get(Transfer, xfer.id)
            expense = db.session.get(Transaction, expense.id)
            income = db.session.get(Transaction, income.id)
            assert xfer.status_id == done.id
            assert expense.status_id == done.id
            assert income.status_id == done.id


# ── Mark Done Guard ────────────────────────────────────────────────


class TestMarkDoneShadowGuard:
    """Tests for POST /transactions/<id>/mark-done on shadow transactions."""

    def test_mark_done_shadow_routes_through_service(
        self, app, db, auth_client, seed_user, seed_periods_today
    ):
        """Mark-done on shadow updates transfer and both shadows."""
        with app.app_context():
            xfer, expense, income = _create_test_transfer(seed_user, seed_periods_today)

            resp = auth_client.post(f"/transactions/{expense.id}/mark-done")

            assert resp.status_code == 200
            assert "gridRefresh" in resp.headers.get("HX-Trigger", "")

            db.session.expire_all()
            done = db.session.query(Status).filter_by(name="Paid").one()
            xfer = db.session.get(Transfer, xfer.id)
            expense = db.session.get(Transaction, expense.id)
            income = db.session.get(Transaction, income.id)
            assert xfer.status_id == done.id
            assert expense.status_id == done.id
            assert income.status_id == done.id


# ── Mark Credit Guard (BLOCK) ─────────────────────────────────────


class TestMarkCreditShadowGuard:
    """Tests for POST /transactions/<id>/mark-credit on shadow transactions."""

    def test_mark_credit_blocked_for_shadow(
        self, app, db, auth_client, seed_user, seed_periods_today
    ):
        """Mark-credit returns 400 for shadow transactions."""
        with app.app_context():
            xfer, expense, _ = _create_test_transfer(seed_user, seed_periods_today)
            original_status = expense.status_id

            resp = auth_client.post(f"/transactions/{expense.id}/mark-credit")

            assert resp.status_code == 400
            db.session.expire_all()
            expense = db.session.get(Transaction, expense.id)
            assert expense.status_id == original_status

            # No payback transaction was created.
            paybacks = db.session.query(Transaction).filter_by(
                credit_payback_for_id=expense.id
            ).count()
            assert paybacks == 0


# ── Unmark Credit Guard (BLOCK) ───────────────────────────────────


class TestUnmarkCreditShadowGuard:
    """Tests for DELETE /transactions/<id>/unmark-credit on shadow transactions."""

    def test_unmark_credit_blocked_for_shadow(
        self, app, db, auth_client, seed_user, seed_periods_today
    ):
        """Unmark-credit returns 400 for shadow transactions."""
        with app.app_context():
            _, expense, _ = _create_test_transfer(seed_user, seed_periods_today)

            resp = auth_client.delete(f"/transactions/{expense.id}/unmark-credit")

            assert resp.status_code == 400


# ── Cancel Guard ───────────────────────────────────────────────────


class TestCancelShadowGuard:
    """Tests for POST /transactions/<id>/cancel on shadow transactions."""

    def test_cancel_shadow_routes_through_service(
        self, app, db, auth_client, seed_user, seed_periods_today
    ):
        """Cancel on shadow updates transfer and both shadows to cancelled."""
        with app.app_context():
            xfer, expense, income = _create_test_transfer(seed_user, seed_periods_today)

            resp = auth_client.post(f"/transactions/{expense.id}/cancel")

            assert resp.status_code == 200
            assert "gridRefresh" in resp.headers.get("HX-Trigger", "")

            db.session.expire_all()
            cancelled = db.session.query(Status).filter_by(name="Cancelled").one()
            xfer = db.session.get(Transfer, xfer.id)
            expense = db.session.get(Transaction, expense.id)
            income = db.session.get(Transaction, income.id)
            assert xfer.status_id == cancelled.id
            assert expense.status_id == cancelled.id
            assert income.status_id == cancelled.id


# ── Delete Guard (BLOCK) ──────────────────────────────────────────


class TestDeleteShadowGuard:
    """Tests for DELETE /transactions/<id> on shadow transactions."""

    def test_delete_shadow_blocked(
        self, app, db, auth_client, seed_user, seed_periods_today
    ):
        """Direct deletion of shadow transaction returns 400."""
        with app.app_context():
            xfer, expense, income = _create_test_transfer(seed_user, seed_periods_today)
            expense_id = expense.id
            income_id = income.id
            xfer_id = xfer.id

            resp = auth_client.delete(f"/transactions/{expense_id}")

            assert resp.status_code == 400

            # All records still exist.
            db.session.expire_all()
            assert db.session.get(Transfer, xfer_id) is not None
            assert db.session.get(Transaction, expense_id) is not None
            assert db.session.get(Transaction, income_id) is not None


# ── Full Edit Guard ────────────────────────────────────────────────


class TestFullEditShadowGuard:
    """Tests for GET /transactions/<id>/full-edit on shadow transactions."""

    def test_full_edit_shadow_returns_transfer_form(
        self, app, db, auth_client, seed_user, seed_periods_today
    ):
        """Full edit for shadow returns transfer edit form, not transaction form."""
        with app.app_context():
            _, expense, _ = _create_test_transfer(seed_user, seed_periods_today)

            resp = auth_client.get(f"/transactions/{expense.id}/full-edit")

            assert resp.status_code == 200
            html = resp.data.decode()
            # Transfer form has the transfer PATCH endpoint.
            assert "/transfers/instance/" in html
            # Has category dropdown (transfer-specific).
            assert "category_id" in html

    def test_full_edit_shadow_targets_transaction_cell(
        self, app, db, auth_client, seed_user, seed_periods_today
    ):
        """Transfer form opened from shadow targets #txn-cell-<shadow_id>."""
        with app.app_context():
            _, expense, _ = _create_test_transfer(seed_user, seed_periods_today)

            resp = auth_client.get(f"/transactions/{expense.id}/full-edit")

            html = resp.data.decode()
            assert f"txn-cell-{expense.id}" in html


# ── Quick Edit Guard ───────────────────────────────────────────────


class TestQuickEditShadowGuard:
    """Tests for GET /transactions/<id>/quick-edit on shadow transactions."""

    def test_quick_edit_shadow_returns_normal_form(
        self, app, db, auth_client, seed_user, seed_periods_today
    ):
        """Quick edit for shadow returns the standard amount input form."""
        with app.app_context():
            _, expense, _ = _create_test_transfer(seed_user, seed_periods_today)

            resp = auth_client.get(f"/transactions/{expense.id}/quick-edit")

            assert resp.status_code == 200
            html = resp.data.decode()
            assert "estimated_amount" in html


# ── Regular Transaction Regression ─────────────────────────────────


class TestRegularTransactionUnaffected:
    """Verify guards do not interfere with regular (non-shadow) transactions."""

    def test_update_regular_transaction(
        self, app, db, auth_client, seed_user, seed_periods_today
    ):
        """PATCH on regular transaction works normally."""
        with app.app_context():
            txn = _create_regular_txn(seed_user, seed_periods_today)

            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"estimated_amount": "75.00"},
            )

            assert resp.status_code == 200
            db.session.refresh(txn)
            assert txn.estimated_amount == Decimal("75.00")

    def test_mark_done_regular_transaction(
        self, app, db, auth_client, seed_user, seed_periods_today
    ):
        """Mark-done on regular transaction works normally."""
        with app.app_context():
            txn = _create_regular_txn(seed_user, seed_periods_today)

            resp = auth_client.post(f"/transactions/{txn.id}/mark-done")

            assert resp.status_code == 200
            db.session.refresh(txn)
            assert txn.status.name == "Paid"

    def test_mark_credit_regular_transaction(
        self, app, db, auth_client, seed_user, seed_periods_today
    ):
        """Mark-credit on regular expense transaction works normally."""
        with app.app_context():
            txn = _create_regular_txn(seed_user, seed_periods_today)

            resp = auth_client.post(f"/transactions/{txn.id}/mark-credit")

            assert resp.status_code == 200
            db.session.refresh(txn)
            assert txn.status.name == "Credit"

    def test_cancel_regular_transaction(
        self, app, db, auth_client, seed_user, seed_periods_today
    ):
        """Cancel on regular transaction works normally."""
        with app.app_context():
            txn = _create_regular_txn(seed_user, seed_periods_today)

            resp = auth_client.post(f"/transactions/{txn.id}/cancel")

            assert resp.status_code == 200
            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"

    def test_delete_regular_ad_hoc_transaction(
        self, app, db, auth_client, seed_user, seed_periods_today
    ):
        """Delete on regular ad-hoc transaction works normally."""
        with app.app_context():
            txn = _create_regular_txn(seed_user, seed_periods_today)
            txn_id = txn.id

            resp = auth_client.delete(f"/transactions/{txn_id}")

            assert resp.status_code == 200
            assert db.session.get(Transaction, txn_id) is None

    def test_full_edit_regular_returns_transaction_form(
        self, app, db, auth_client, seed_user, seed_periods_today
    ):
        """Full edit for regular transaction returns transaction form."""
        with app.app_context():
            txn = _create_regular_txn(seed_user, seed_periods_today)

            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")

            assert resp.status_code == 200
            html = resp.data.decode()
            # Transaction form has estimated_amount field.
            assert "estimated_amount" in html
            # Does not contain transfer-specific endpoint.
            assert "/transfers/instance/" not in html


# ── Due Date PATCH Tests ────────────────────────────────────────────


class TestDueDatePatch:
    """Tests for PATCH due_date on transactions."""

    def test_patch_due_date_override(self, app, auth_client, seed_user, seed_periods_today):
        """PATCH due_date updates the transaction's due_date."""
        with app.app_context():
            from datetime import date
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense = db.session.query(TransactionType).filter_by(name="Expense").one()

            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=seed_periods_today[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=projected.id,
                name="Test Bill",
                transaction_type_id=expense.id,
                estimated_amount=Decimal("500.00"),
                due_date=date(2026, 1, 15),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"due_date": "2026-01-20"},
            )
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert txn.due_date == date(2026, 1, 20)

    def test_patch_due_date_shadow_propagates(self, app, auth_client, seed_user, seed_periods_today):
        """PATCH due_date on transfer shadow updates both shadows."""
        with app.app_context():
            from datetime import date

            transfer, exp_shadow, inc_shadow = _create_test_transfer(
                seed_user, seed_periods_today,
            )

            resp = auth_client.patch(
                f"/transactions/{exp_shadow.id}",
                data={"due_date": "2026-01-20"},
            )
            assert resp.status_code == 200

            db.session.refresh(exp_shadow)
            db.session.refresh(inc_shadow)
            assert exp_shadow.due_date == date(2026, 1, 20)
            assert inc_shadow.due_date == date(2026, 1, 20)

    def test_patch_due_date_does_not_affect_other_fields(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """PATCH only due_date leaves amount, status, notes unchanged."""
        with app.app_context():
            from datetime import date
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense = db.session.query(TransactionType).filter_by(name="Expense").one()

            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=seed_periods_today[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=projected.id,
                name="Stable",
                transaction_type_id=expense.id,
                estimated_amount=Decimal("750.00"),
                notes="original note",
                due_date=date(2026, 1, 5),
            )
            db.session.add(txn)
            db.session.commit()

            auth_client.patch(
                f"/transactions/{txn.id}",
                data={"due_date": "2026-01-25"},
            )
            db.session.refresh(txn)
            assert txn.estimated_amount == Decimal("750.00")
            assert txn.notes == "original note"
            assert txn.status_id == projected.id

    def test_full_edit_shows_due_date(self, app, auth_client, seed_user, seed_periods_today):
        """GET full-edit popover contains due_date input for txn with due_date."""
        with app.app_context():
            from datetime import date
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense = db.session.query(TransactionType).filter_by(name="Expense").one()

            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=seed_periods_today[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=projected.id,
                name="With Due",
                transaction_type_id=expense.id,
                estimated_amount=Decimal("100.00"),
                due_date=date(2026, 1, 10),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            assert resp.status_code == 200
            assert b"due_date" in resp.data
            assert b"2026-01-10" in resp.data
