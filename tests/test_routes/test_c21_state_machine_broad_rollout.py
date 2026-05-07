"""
Shekel Budget App -- C-21 Follow-up: Broad State Machine Rollout

Verifies that ``verify_transition`` is wired into every state-changing
endpoint that previously bypassed it: mark_done, cancel_transaction,
dashboard.mark_paid, and unmark_credit.  Settled and Cancelled rows
can no longer slip into Paid/Received via these endpoints; identity
transitions still succeed so HTMX double-clicks remain idempotent.

Audit reference: F-046 / F-047 / F-161 -- broad rollout following the
2026-04-15 commit C-21.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.ref import Status, TransactionType
from app.models.transaction import Transaction


# ── Helpers ─────────────────────────────────────────────────────────


def _create_projected_expense(seed_user, seed_periods_today, period_index=0):
    """Insert a projected expense in the requested period."""
    projected = db.session.query(Status).filter_by(name="Projected").one()
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    txn = Transaction(
        pay_period_id=seed_periods_today[period_index].id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=projected.id,
        name="Test Expense",
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=expense_type.id,
        estimated_amount=Decimal("100.00"),
        due_date=seed_periods_today[period_index].start_date,
    )
    db.session.add(txn)
    db.session.commit()
    return txn


def _walk_to(txn, status_name):
    """Drive a freshly-projected row to *status_name* via direct writes.

    Bypasses the route layer so the test bodies stay short.  The
    route-layer transitions used here are themselves covered by the
    legal-transition tests in ``test_c21_state_machine_routes.py`` and
    the existing grid-test suite.
    """
    target = db.session.query(Status).filter_by(name=status_name).one()
    txn.status_id = target.id
    db.session.commit()


def _walk_to_settled(txn):
    """Drive a freshly-projected row through Projected -> Paid -> Settled."""
    _walk_to(txn, "Paid")
    _walk_to(txn, "Settled")


# ══════════════════════════════════════════════════════════════════════
# /transactions/<id>/mark-done -- direct (non-envelope, non-transfer)
# ══════════════════════════════════════════════════════════════════════


class TestMarkDoneDirectStateMachine:
    """The grid's mark_done endpoint now refuses transitions that the
    state machine forbids.  Previously the direct branch wrote
    ``status_id`` unconditionally, so a Settled or Cancelled row
    could be silently re-marked Paid."""

    def test_settled_to_paid_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A Settled row cannot be re-marked Paid via mark_done."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            _walk_to_settled(txn)
            settled_id = ref_cache.status_id(StatusEnum.SETTLED)
            done_id = ref_cache.status_id(StatusEnum.DONE)

            resp = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert resp.status_code == 400
            body = resp.data.decode()
            assert "transaction" in body
            assert str(settled_id) in body
            assert str(done_id) in body

            db.session.refresh(txn)
            assert txn.status.name == "Settled"

    def test_cancelled_to_paid_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A Cancelled row cannot jump straight to Paid."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            _walk_to(txn, "Cancelled")

            resp = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert resp.status_code == 400
            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"

    def test_credit_to_paid_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A Credit row cannot jump straight to Paid."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            _walk_to(txn, "Credit")

            resp = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert resp.status_code == 400
            db.session.refresh(txn)
            assert txn.status.name == "Credit"

    def test_projected_to_paid_accepted(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The legal projected -> paid path still returns 200."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)

            resp = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert resp.status_code == 200
            db.session.refresh(txn)
            assert txn.status.name == "Paid"

    def test_paid_to_paid_identity_accepted(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Re-marking a Paid row is idempotent -- HTMX double-clicks
        and dashboard re-fires must not produce 400s."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            _walk_to(txn, "Paid")

            resp = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert resp.status_code == 200
            db.session.refresh(txn)
            assert txn.status.name == "Paid"


# ══════════════════════════════════════════════════════════════════════
# /transactions/<id>/cancel
# ══════════════════════════════════════════════════════════════════════


class TestCancelTransactionStateMachine:
    """Cancel is reachable only from Projected (or the Cancelled
    identity edge).  Done / Received / Settled rows must be reverted
    to Projected first so the audit log records both legs."""

    def test_paid_to_cancelled_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Cancelling a Paid row is now refused."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            _walk_to(txn, "Paid")

            resp = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert resp.status_code == 400
            db.session.refresh(txn)
            assert txn.status.name == "Paid"

    def test_settled_to_cancelled_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Settled is terminal; cancel cannot resurrect it."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            _walk_to_settled(txn)

            resp = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert resp.status_code == 400
            db.session.refresh(txn)
            assert txn.status.name == "Settled"

    def test_credit_to_cancelled_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Credit can only revert to Projected, not Cancelled."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            _walk_to(txn, "Credit")

            resp = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert resp.status_code == 400
            db.session.refresh(txn)
            assert txn.status.name == "Credit"

    def test_projected_to_cancelled_accepted(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The legal projected -> cancelled path still returns 200."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)

            resp = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert resp.status_code == 200
            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"

    def test_cancelled_to_cancelled_identity_accepted(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Re-cancelling a Cancelled row is idempotent."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            _walk_to(txn, "Cancelled")

            resp = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert resp.status_code == 200
            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"


# ══════════════════════════════════════════════════════════════════════
# /dashboard/mark-paid/<id>
# ══════════════════════════════════════════════════════════════════════


class TestDashboardMarkPaidStateMachine:
    """Dashboard mark-paid mirrors the grid's mark_done.  The state
    machine guard sits on the direct branch; the transfer-shadow
    branch already inherits enforcement through transfer_service."""

    def test_settled_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A Settled row cannot be re-marked Paid via the dashboard."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            _walk_to_settled(txn)

            resp = auth_client.post(f"/dashboard/mark-paid/{txn.id}")
            assert resp.status_code == 400
            db.session.refresh(txn)
            assert txn.status.name == "Settled"

    def test_cancelled_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A Cancelled row cannot be re-marked Paid via the dashboard."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            _walk_to(txn, "Cancelled")

            resp = auth_client.post(f"/dashboard/mark-paid/{txn.id}")
            assert resp.status_code == 400
            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"

    def test_projected_accepted(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The legal projected -> paid path still returns 200."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)

            resp = auth_client.post(f"/dashboard/mark-paid/{txn.id}")
            assert resp.status_code == 200
            db.session.refresh(txn)
            assert txn.status.name == "Paid"


# ══════════════════════════════════════════════════════════════════════
# /transactions/<id>/unmark-credit
# ══════════════════════════════════════════════════════════════════════


class TestUnmarkCreditStateMachine:
    """``unmark_credit`` previously rewrote ``status_id`` to Projected
    on any caller-supplied row -- including a Paid row that had no
    payback to clean up.  The follow-up adds (a) a bespoke
    "must be in Credit status" guard and (b) ``verify_transition`` as
    a defense-in-depth layer."""

    def test_paid_row_rejected_with_friendly_message(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Calling unmark-credit on a Paid row is refused with a
        message that names the offending status."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            _walk_to(txn, "Paid")

            resp = auth_client.delete(f"/transactions/{txn.id}/unmark-credit")
            assert resp.status_code == 400
            body = resp.data.decode()
            assert "Paid" in body
            assert "Only Credit" in body
            db.session.refresh(txn)
            # Row stays Paid -- the bespoke guard fires before the
            # service writes anything.
            assert txn.status.name == "Paid"

    def test_projected_row_rejected_with_friendly_message(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A Projected row also fails the bespoke guard -- there is
        no Credit state to revert from."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)

            resp = auth_client.delete(f"/transactions/{txn.id}/unmark-credit")
            assert resp.status_code == 400
            body = resp.data.decode()
            assert "Projected" in body
            db.session.refresh(txn)
            assert txn.status.name == "Projected"

    def test_credit_row_accepted(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The legal Credit -> Projected path still returns 200 and
        deletes the auto-generated payback."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            # Use the real mark-credit workflow to seed a payback row;
            # _walk_to writes status only and would not produce one.
            mark_resp = auth_client.post(f"/transactions/{txn.id}/mark-credit")
            assert mark_resp.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Credit"
            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .one()
            )
            payback_id = payback.id

            resp = auth_client.delete(f"/transactions/{txn.id}/unmark-credit")
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Projected"
            # Payback is deleted as part of the unmark-credit workflow.
            assert db.session.get(Transaction, payback_id) is None


# ══════════════════════════════════════════════════════════════════════
# Transfer-shadow propagation: route exception handling
# ══════════════════════════════════════════════════════════════════════


class TestTransferShadowMarkDoneStateMachine:
    """``transfer_service.update_transfer`` already enforces the state
    machine (commit C-21).  These tests confirm the route layer's
    exception handling translates the resulting ValidationError into
    a clean 400 instead of a 500 when mark_done is invoked on a
    transfer shadow whose parent transfer is in a non-mutable state."""

    def _create_transfer_with_shadows(
        self, app, db_session, seed_user, seed_periods_today,
    ):
        """Helper -- builds a savings account, the Transfers categories
        the service requires, and a Projected transfer with two
        shadows.  Returns the parent transfer so tests can drive
        status changes through it.
        """
        from app.models.account import Account
        from app.models.category import Category
        from app.models.ref import AccountType
        from app.services import transfer_service

        savings_type = (
            db_session.query(AccountType).filter_by(name="Savings").one()
        )
        savings = Account(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="Savings",
            current_anchor_balance=Decimal("500.00"),
            current_anchor_period_id=seed_periods_today[0].id,
        )
        db_session.add(savings)
        db_session.flush()

        for group, item in (("Transfers", "Outgoing"), ("Transfers", "Incoming")):
            db_session.add(
                Category(
                    user_id=seed_user["user"].id,
                    group_name=group,
                    item_name=item,
                )
            )
        db_session.commit()

        projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
        xfer = transfer_service.create_transfer(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            pay_period_id=seed_periods_today[0].id,
            scenario_id=seed_user["scenario"].id,
            amount=Decimal("100.00"),
            status_id=projected_id,
            category_id=seed_user["categories"]["Rent"].id,
        )
        db_session.commit()
        return xfer

    def test_mark_done_on_settled_transfer_shadow_returns_400(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """mark_done on a settled transfer shadow returns 400."""
        with app.app_context():
            xfer = self._create_transfer_with_shadows(
                app, db.session, seed_user, seed_periods_today,
            )
            # Walk parent + shadows through Projected -> Paid -> Settled.
            from app.services import transfer_service

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.commit()
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.SETTLED),
            )
            db.session.commit()

            shadow = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id, is_deleted=False)
                .first()
            )
            resp = auth_client.post(f"/transactions/{shadow.id}/mark-done")
            assert resp.status_code == 400

    def test_cancel_on_paid_transfer_shadow_returns_400(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Cancel on a Paid transfer shadow returns 400 (Done -> Cancelled
        is illegal under the state machine)."""
        with app.app_context():
            xfer = self._create_transfer_with_shadows(
                app, db.session, seed_user, seed_periods_today,
            )
            from app.services import transfer_service

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.commit()

            shadow = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id, is_deleted=False)
                .first()
            )
            resp = auth_client.post(f"/transactions/{shadow.id}/cancel")
            assert resp.status_code == 400


# ══════════════════════════════════════════════════════════════════════
# Service-level unmark_credit guard tests
# ══════════════════════════════════════════════════════════════════════


class TestUnmarkCreditServiceGuard:
    """Direct service-level tests for the new guards in
    ``credit_workflow.unmark_credit``.  Route-level coverage above
    confirms the 400 translation; these tests pin the exception type
    and message produced by the service so future refactors do not
    silently regress the friendly-message contract."""

    def test_raises_on_paid_status(
        self, app, seed_user, seed_periods_today,
    ):
        """unmark_credit on a Paid txn raises ValidationError naming
        the status."""
        import pytest

        from app.exceptions import ValidationError
        from app.services import credit_workflow

        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            _walk_to(txn, "Paid")

            with pytest.raises(ValidationError) as excinfo:
                credit_workflow.unmark_credit(txn.id, seed_user["user"].id)
            msg = str(excinfo.value)
            assert "Paid" in msg
            assert "Only Credit" in msg

    def test_raises_on_settled_status(
        self, app, seed_user, seed_periods_today,
    ):
        """unmark_credit on a Settled txn raises ValidationError too --
        the bespoke guard fires before the state machine layer."""
        import pytest

        from app.exceptions import ValidationError
        from app.services import credit_workflow

        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            _walk_to_settled(txn)

            with pytest.raises(ValidationError) as excinfo:
                credit_workflow.unmark_credit(txn.id, seed_user["user"].id)
            assert "Settled" in str(excinfo.value)
