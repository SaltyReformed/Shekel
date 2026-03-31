"""
Shekel Budget App -- Adversarial QA Test Suite

Hostile QA tests that probe for bugs, validation gaps, and edge cases
the happy-path suite doesn't cover.  Each test documents the vulnerability
it would catch.  Assertions match CURRENT behavior (even if broken) with
comments explaining ideal behavior.
"""

from collections import OrderedDict
from datetime import date
from decimal import Decimal, InvalidOperation

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.ref import AccountType, FilingStatus, Status, TransactionType
from app.models.salary_profile import SalaryProfile
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import balance_calculator, carry_forward_service, credit_workflow


# ── Helpers ──────────────────────────────────────────────────────────


def _make_transaction(seed_user, seed_periods, *, period_index=0, status_name="Projected",
                      txn_type_name="Expense", amount="100.00", name="Test Item",
                      category_key="Rent"):
    """Create and flush a transaction with sensible defaults."""
    status = db.session.query(Status).filter_by(name=status_name).one()
    txn_type = db.session.query(TransactionType).filter_by(name=txn_type_name).one()
    txn = Transaction(
        pay_period_id=seed_periods[period_index].id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=status.id,
        name=name,
        category_id=seed_user["categories"][category_key].id,
        transaction_type_id=txn_type.id,
        estimated_amount=Decimal(amount),
    )
    db.session.add(txn)
    db.session.flush()
    return txn


def _make_savings_account(seed_user):
    """Create a second (savings) account for transfer tests."""
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    acct = Account(
        user_id=seed_user["user"].id,
        account_type_id=savings_type.id,
        name="Savings",
        current_anchor_balance=Decimal("500.00"),
    )
    db.session.add(acct)
    db.session.flush()
    return acct


# ══════════════════════════════════════════════════════════════════════
# 1. State Machine Violations
# ══════════════════════════════════════════════════════════════════════


class TestStateMachineViolations:
    """Probe the PATCH /transactions/<id> endpoint for unconstrained
    status_id and the mark_done endpoint for missing guards."""

    def test_update_status_to_nonexistent_id(self, app, auth_client, seed_user, seed_periods):
        """PATCH with status_id=9999 returns 400 (FK constraint caught).

        The DB FK to ref.statuses catches invalid references and the
        IntegrityError handler returns a clean 400 response.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods)
            db.session.commit()

            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": "9999"},
            )
            assert resp.status_code == 400

    def test_update_done_back_to_projected(self, app, auth_client, seed_user, seed_periods):
        """Mark done then PATCH back to projected -- no transition guard.

        Bug: There is no domain logic preventing backward status transitions.
        A 'done' transaction can be freely reverted to 'projected'.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods, status_name="Paid")
            db.session.commit()

            projected = db.session.query(Status).filter_by(name="Projected").one()
            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(projected.id)},
            )
            # Current behavior: allowed -- no transition guard.
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Projected"

    def test_mark_done_negative_actual_amount(self, app, auth_client, seed_user, seed_periods):
        """POST mark_done with actual_amount=-500 is rejected by DB CHECK constraint.

        The CHECK constraint on budget.transactions.actual_amount
        prevents negative values at the database level (L-01).
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods)
            db.session.commit()

            resp = auth_client.post(
                f"/transactions/{txn.id}/mark-done",
                data={"actual_amount": "-500.00"},
            )
            assert resp.status_code == 400

    def test_mark_done_already_done_transaction(self, app, auth_client, seed_user, seed_periods):
        """POST mark_done on an already-done transaction.

        Bug: No idempotency check -- re-setting status is harmless but
        the actual_amount could be overwritten if a new value is posted.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods, status_name="Paid")
            txn.actual_amount = Decimal("75.00")
            db.session.commit()

            # Call mark_done again without an actual_amount -- should keep the old one.
            resp = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert resp.status_code == 200

            db.session.refresh(txn)
            # Current behavior: status is re-set, actual_amount preserved
            # because the form didn't send one.
            assert txn.actual_amount == Decimal("75.00")

    def test_projected_to_cancelled_to_projected_double_reversal(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Projected → cancelled → projected double reversal.

        Cancel a projected transaction, then PATCH it back to projected.
        The audit flagged this round-trip as missing test coverage.
        Verifies effective_amount correctness at each stage.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods)
            db.session.commit()
            original_amount = txn.estimated_amount  # Decimal("100.00")

            # Verify initial effective_amount.
            assert txn.effective_amount == original_amount

            # Step 1: Cancel the transaction.
            resp = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"
            assert txn.effective_amount == Decimal("0")

            # Step 2: PATCH back to projected.
            projected = db.session.query(Status).filter_by(name="Projected").one()
            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(projected.id)},
            )
            # Current behavior: no transition guard, reversion is allowed.
            # Ideal: cancelled → projected should require explicit confirmation
            # to prevent accidental re-projection of cancelled items.
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Projected"
            assert txn.effective_amount == original_amount

    def test_done_to_cancelled_transition(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Done → cancelled transition.

        A done transaction (with actual_amount set) is cancelled.
        Verifies that actual_amount is preserved in the DB but
        effective_amount drops to zero (excluded from balance calcs).
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods, status_name="Paid")
            txn.actual_amount = Decimal("85.00")
            db.session.commit()

            # Verify initial effective_amount uses actual_amount.
            assert txn.effective_amount == Decimal("85.00")

            # Cancel the transaction.
            resp = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"
            assert txn.effective_amount == Decimal("0")

            # Current behavior: actual_amount is preserved in the DB.
            # The cancel endpoint only changes status_id, does not clear actual_amount.
            assert txn.actual_amount == Decimal("85.00")

    def test_received_to_projected_reversion(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Received → projected reversion on an income transaction.

        Income marked received (with actual_amount) is reverted to
        projected via PATCH. Financially dangerous: income that was
        counted as received re-appears at estimated_amount, which may
        differ from the actual_amount that was settled.
        """
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods, txn_type_name="Income",
                name="Paycheck", category_key="Salary", amount="3000.00",
                status_name="Received",
            )
            txn.actual_amount = Decimal("2800.00")
            db.session.commit()

            # Verify initial effective_amount uses actual_amount.
            assert txn.effective_amount == Decimal("2800.00")

            # PATCH back to projected.
            projected = db.session.query(Status).filter_by(name="Projected").one()
            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(projected.id)},
            )
            # Current behavior: no transition guard, reversion is allowed.
            # Ideal: received → projected should be rejected to prevent
            # balance corruption from re-projecting settled income.
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Projected"
            # After 5A.1: effective_amount prefers actual_amount for all
            # active statuses, so even as Projected it returns 2800.
            assert txn.effective_amount == Decimal("2800.00")
            # actual_amount is NOT cleared by the PATCH.
            assert txn.actual_amount == Decimal("2800.00")

    def test_credit_to_projected_reversion(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Credit → projected reversion leaves orphaned payback.

        A transaction marked as credit (auto-generates payback) is
        reverted to projected via PATCH. The payback transaction is
        NOT deleted, creating an orphaned payback that inflates
        projected expenses.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods, period_index=0)
            db.session.commit()

            # Mark as credit -- creates payback in next period.
            resp = auth_client.post(f"/transactions/{txn.id}/mark-credit")
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Credit"
            assert txn.effective_amount == Decimal("0")

            # Find the payback transaction.
            payback = db.session.query(Transaction).filter_by(
                credit_payback_for_id=txn.id,
            ).one()
            payback_id = payback.id

            # PATCH the original back to projected.
            projected = db.session.query(Status).filter_by(name="Projected").one()
            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(projected.id)},
            )
            # Current behavior: no transition guard, reversion is allowed.
            # Ideal: credit → projected should also delete the payback
            # (like unmark_credit does) to prevent orphaned paybacks.
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Projected"
            assert txn.effective_amount == Decimal("100.00")

            # BUG: the payback transaction is now orphaned.
            orphan = db.session.get(Transaction, payback_id)
            assert orphan is not None, \
                "Payback still exists as orphan after credit→projected reversion"

    def test_cancelled_to_done_direct(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Cancelled → done via direct PATCH (bypassing mark_done).

        PATCHing status_id to done bypasses the mark_done endpoint,
        resulting in a 'done' transaction without actual_amount.
        effective_amount falls through to estimated_amount.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods, status_name="Cancelled")
            db.session.commit()

            done_status = db.session.query(Status).filter_by(name="Paid").one()
            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(done_status.id)},
            )
            # Current behavior: no transition guard, direct status change is allowed.
            # Ideal: cancelled → done should be rejected; use mark_done instead.
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Paid"
            # effective_amount for done without actual_amount uses estimated_amount.
            assert txn.effective_amount == txn.estimated_amount

    def test_cancel_already_cancelled_transaction(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Cancel → cancel idempotency check.

        Cancelling an already-cancelled transaction should be harmless.
        Verifies the status remains cancelled with no side effects.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods, status_name="Cancelled")
            db.session.commit()

            resp = auth_client.post(f"/transactions/{txn.id}/cancel")
            # Current behavior: cancel sets status unconditionally. Idempotent.
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"
            assert txn.effective_amount == Decimal("0")

    def test_mark_done_on_cancelled_transaction(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """POST mark_done on a cancelled transaction.

        A cancelled transaction is excluded from balance calculations.
        mark_done re-includes it as 'done', which could corrupt balance
        projections if the cancellation was intentional.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods, status_name="Cancelled")
            db.session.commit()

            resp = auth_client.post(
                f"/transactions/{txn.id}/mark-done",
                data={"actual_amount": "95.00"},
            )
            # Current behavior: mark_done has no guard against cancelled transactions.
            # Ideal: should reject with "cannot mark cancelled as done".
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Paid"
            assert txn.actual_amount == Decimal("95.00")
            assert txn.effective_amount == Decimal("95.00")

    def test_mark_credit_on_done_transaction(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """POST mark-credit on a done transaction -- rejected by service.

        credit_workflow.mark_as_credit only allows projected transactions.
        Attempting to mark a done transaction as credit returns 400.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods, status_name="Paid")
            txn.actual_amount = Decimal("100.00")
            db.session.commit()

            resp = auth_client.post(f"/transactions/{txn.id}/mark-credit")
            # credit_workflow raises ValidationError for non-projected transactions.
            # Route catches it and returns 400.
            assert resp.status_code == 400
            assert b"Only projected" in resp.data

            db.session.refresh(txn)
            assert txn.status.name == "Paid"
            assert txn.effective_amount == Decimal("100.00")


# ══════════════════════════════════════════════════════════════════════
# 2. Referential Integrity
# ══════════════════════════════════════════════════════════════════════


class TestReferentialIntegrity:
    """Probe FK cascade/restrict behavior on deletion."""

    def test_delete_account_with_transfers_blocked(self, app, auth_client, seed_user, seed_periods):
        """Deactivating an account with active transfer templates is blocked.

        The route checks for active transfer templates referencing the account
        and returns a flash warning instead of proceeding.
        """
        with app.app_context():
            savings = _make_savings_account(seed_user)
            checking = seed_user["account"]
            checking_id = checking.id

            # Create a transfer template referencing both accounts.
            template = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=checking_id,
                to_account_id=savings.id,
                name="Monthly Savings",
                default_amount=Decimal("200.00"),
                is_active=True,
            )
            db.session.add(template)
            db.session.commit()

            resp = auth_client.post(f"/accounts/{checking_id}/delete")
            # Current behavior: route blocks deactivation with a flash warning.
            assert resp.status_code == 302  # Redirect back to list

            # Follow redirect to verify the flash message.
            follow = auth_client.get(resp.headers["Location"])
            assert b"Cannot deactivate" in follow.data

            # Re-query to verify account is still active.
            acct = db.session.get(Account, checking_id)
            assert acct.is_active is True

    def test_delete_category_with_transactions_blocked(self, app, auth_client, seed_user, seed_periods):
        """DELETE category when transactions reference it -- blocked by route guard.

        The route checks for templates and transactions using the category
        before allowing deletion.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods, category_key="Rent")
            db.session.commit()

            rent_cat = seed_user["categories"]["Rent"]
            resp = auth_client.post(f"/categories/{rent_cat.id}/delete")
            # Current behavior: route blocks with flash warning.
            assert resp.status_code == 302

            # Follow redirect to verify the flash message.
            follow = auth_client.get(resp.headers["Location"])
            assert b"in use" in follow.data

            # Category still exists.
            cat = db.session.get(Category, rent_cat.id)
            assert cat is not None

    def test_delete_pay_period_cascades_transactions(self, app, auth_client, seed_user, seed_periods):
        """DELETE period → all transactions silently destroyed (CASCADE).

        Bug: Transaction.pay_period_id has ondelete='CASCADE'.  Deleting a
        period destroys all its transactions without any application-level
        warning.  This documents the cascading data loss.

        Note: Must use raw SQL to bypass ORM relationship handling, and
        must use a non-anchor period (period 2) since the anchor period
        is FK-referenced by the account.
        """
        with app.app_context():
            # Use period index 2 (not the anchor period at index 0).
            txn = _make_transaction(seed_user, seed_periods, period_index=2)
            db.session.commit()
            txn_id = txn.id
            period_id = seed_periods[2].id

            # Use raw SQL to trigger DB-level CASCADE (ORM intercepts otherwise).
            db.session.execute(
                db.text("DELETE FROM budget.pay_periods WHERE id = :pid"),
                {"pid": period_id},
            )
            db.session.commit()

            # Expire cached objects so we get fresh DB state.
            db.session.expire_all()

            # Transaction cascaded away.
            assert db.session.get(Transaction, txn_id) is None

    def test_template_id_set_null_on_delete(self, app, auth_client, seed_user, seed_periods):
        """DELETE template → linked transactions get template_id=NULL.

        Transaction.template_id has ondelete='SET NULL'.  Documents that
        transactions survive template deletion but lose their link.
        """
        with app.app_context():
            # Create a template.
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                name="Rent Template",
                default_amount=Decimal("1500.00"),
            )
            db.session.add(template)
            db.session.flush()

            # Create a transaction linked to the template.
            txn = _make_transaction(seed_user, seed_periods, name="Rent", amount="1500.00")
            txn.template_id = template.id
            db.session.commit()
            txn_id = txn.id

            # Delete the template directly.
            db.session.delete(template)
            db.session.commit()

            # Transaction survives with NULL template_id.
            txn = db.session.get(Transaction, txn_id)
            assert txn is not None
            assert txn.template_id is None


# ══════════════════════════════════════════════════════════════════════
# 3. Credit Workflow Edge Cases
# ══════════════════════════════════════════════════════════════════════


class TestCreditWorkflowEdgeCases:
    """Probe credit_workflow.py for data loss and nonsensical states."""

    def test_unmark_credit_deletes_done_payback(self, app, auth_client, seed_user, seed_periods):
        """Unmark credit on a transaction whose payback is already marked done.

        Bug: unmark_credit deletes the payback regardless of its status.
        If the payback was already paid (done), this represents real money
        lost from the tracking -- data loss.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods, period_index=0)
            db.session.commit()

            # Mark as credit -- creates payback in next period.
            payback = credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)
            db.session.commit()
            payback_id = payback.id

            # Simulate: payback was paid.
            done_status = db.session.query(Status).filter_by(name="Paid").one()
            payback.status_id = done_status.id
            payback.actual_amount = Decimal("100.00")
            db.session.commit()

            # Unmark credit -- payback is deleted even though it was done.
            credit_workflow.unmark_credit(txn.id, seed_user["user"].id)
            db.session.commit()

            # Current behavior: done payback is deleted. Data loss.
            assert db.session.get(Transaction, payback_id) is None

    def test_mark_credit_on_income_transaction(self, app, auth_client, seed_user, seed_periods):
        """Mark credit on an income transaction -- rejected by service.

        The service correctly raises ValidationError for income.
        """
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods, txn_type_name="Income",
                name="Paycheck", category_key="Salary", amount="3000.00",
            )
            db.session.commit()

            from app.exceptions import ValidationError
            with pytest.raises(ValidationError, match="Cannot mark income as credit"):
                credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)

    def test_mark_credit_on_last_period(self, app, auth_client, seed_user, seed_periods):
        """Mark credit on a transaction in the last period -- no next period.

        Bug: get_next_period() returns None when the transaction is in the
        last pay period.  mark_as_credit raises ValidationError.
        """
        with app.app_context():
            last_period_idx = len(seed_periods) - 1
            txn = _make_transaction(
                seed_user, seed_periods, period_index=last_period_idx,
            )
            db.session.commit()

            from app.exceptions import ValidationError
            with pytest.raises(ValidationError, match="No next pay period"):
                credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)


# ══════════════════════════════════════════════════════════════════════
# 4. Carry Forward Edge Cases
# ══════════════════════════════════════════════════════════════════════


class TestCarryForwardEdgeCases:
    """Probe carry_forward_service for source==target and credit handling."""

    def test_carry_forward_source_equals_target(self, app, auth_client, seed_user, seed_periods):
        """Carry forward with source == target period returns 0 without modification.

        The source==target guard returns early, preventing the previous bug
        of setting is_override=True on template-linked transactions.
        """
        with app.app_context():
            # Create a template-linked transaction in period 0.
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                name="Rent Template",
                default_amount=Decimal("1500.00"),
            )
            db.session.add(template)
            db.session.flush()

            txn = _make_transaction(seed_user, seed_periods, name="Rent", amount="1500.00")
            txn.template_id = template.id
            db.session.commit()

            # Carry forward from period 0 to period 0.
            period_id = seed_periods[0].id
            count = carry_forward_service.carry_forward_unpaid(
                period_id, period_id, seed_user["user"].id,
                seed_user["scenario"].id,
            )
            db.session.commit()

            # Guard returns 0 -- no items processed, no side effects.
            assert count == 0
            db.session.refresh(txn)
            assert txn.pay_period_id == period_id
            assert txn.is_override is False  # Not modified by the guard

    def test_carry_forward_preserves_credit_transactions(self, app, auth_client, seed_user, seed_periods):
        """Carry forward should NOT move credit-status transactions.

        Credit transactions are settled via their payback mechanism.
        carry_forward_unpaid only moves 'projected' status items, so
        credit-status items are correctly excluded.
        """
        with app.app_context():
            # Create a transaction and mark it as credit.
            txn = _make_transaction(seed_user, seed_periods, period_index=0)
            db.session.commit()
            credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)
            db.session.commit()

            # Also create a projected transaction in the same period.
            projected_txn = _make_transaction(
                seed_user, seed_periods, period_index=0, name="Groceries",
                category_key="Groceries",
            )
            db.session.commit()

            # Carry forward from period 0 to period 2.
            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[2].id, seed_user["user"].id,
                seed_user["scenario"].id,
            )
            db.session.commit()

            # Only the projected transaction should be moved.
            assert count == 1
            db.session.refresh(projected_txn)
            assert projected_txn.pay_period_id == seed_periods[2].id

            # Credit transaction stays in its original period.
            db.session.refresh(txn)
            assert txn.pay_period_id == seed_periods[0].id


# ══════════════════════════════════════════════════════════════════════
# 5. Input Validation Bypass
# ══════════════════════════════════════════════════════════════════════


class TestInputValidationBypass:
    """Probe for unvalidated query params and schema gaps."""

    def test_grid_non_numeric_periods_param(self, app, auth_client, seed_user, seed_periods):
        """?periods=abc gracefully falls back to default periods.

        Flask's request.args.get(..., type=int) returns the default
        when the value cannot be converted, instead of raising ValueError.
        """
        with app.app_context():
            resp = auth_client.get("/?periods=abc")
            assert resp.status_code == 200

    def test_grid_negative_periods_param(self, app, auth_client, seed_user, seed_periods):
        """?periods=-1 → negative period count.

        Bug: No Range validation on the periods query param.
        With -1, get_periods_in_range returns empty and grid renders with
        no columns (but doesn't crash).
        """
        with app.app_context():
            resp = auth_client.get("/?periods=-1")
            # Current behavior: renders a valid page (grid or pay period setup).
            assert resp.status_code == 200
            assert b"Shekel" in resp.data
            assert b"Traceback" not in resp.data
            assert b"Internal Server Error" not in resp.data

    def test_grid_extreme_periods_param(self, app, auth_client, seed_user, seed_periods):
        """?periods=10000 → resource exhaustion risk.

        Bug: No upper bound on the periods count.  With 10000, the query
        is bounded by actual data (only 10 periods exist), so it doesn't
        crash, but in production with more data this could be a problem.
        """
        with app.app_context():
            resp = auth_client.get("/?periods=10000")
            # Current behavior: works because only 10 periods exist in test data.
            assert resp.status_code == 200
            assert b"grid-table" in resp.data
            assert b"Traceback" not in resp.data
            assert b"Internal Server Error" not in resp.data

    def test_transfer_zero_amount_via_schema(self, app, auth_client, seed_user, seed_periods):
        """TransferCreateSchema should reject amount=0.

        The schema uses validate.Range(min=0, min_inclusive=False) which
        correctly rejects zero.  The DB CHECK constraint also requires > 0.
        """
        with app.app_context():
            from app.schemas.validation import TransferCreateSchema
            schema = TransferCreateSchema()

            savings = _make_savings_account(seed_user)
            db.session.commit()

            errors = schema.validate({
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                "amount": "0.00",
                "pay_period_id": str(seed_periods[0].id),
                "scenario_id": str(seed_user["scenario"].id),
            })
            # Schema correctly rejects amount=0.
            assert "amount" in errors


# ══════════════════════════════════════════════════════════════════════
# 6. Balance Calculator Boundary Cases
# ══════════════════════════════════════════════════════════════════════


class TestBalanceCalculatorBoundary:
    """Probe balance_calculator for anchor mismatches and None handling."""

    def test_balance_calc_anchor_not_in_periods(self, app, seed_user, seed_periods):
        """Anchor period_id doesn't match any period in the list.

        Bug: calculate_balances silently returns an empty OrderedDict
        when the anchor period is not found in the periods list.
        No error raised -- caller gets silent wrong results.
        """
        with app.app_context():
            result, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("1000.00"),
                anchor_period_id=999999,  # Doesn't match any period
                periods=seed_periods,
                transactions=[],
            )
            # Current behavior: returns empty dict -- all periods are "pre-anchor".
            assert result == OrderedDict()

    def test_balance_calc_empty_periods_list(self, app, seed_user, seed_periods):
        """Empty periods list → returns empty dict.

        Documents behavior: not a bug, but worth testing.
        """
        with app.app_context():
            result, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("1000.00"),
                anchor_period_id=seed_periods[0].id,
                periods=[],
                transactions=[],
            )
            assert result == OrderedDict()

    def test_balance_calc_none_anchor_balance(self, app, seed_user, seed_periods):
        """anchor_balance=None → defaults to Decimal("0.00").

        The service handles None by defaulting to 0.00.
        """
        with app.app_context():
            result, _ = balance_calculator.calculate_balances(
                anchor_balance=None,
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods,
                transactions=[],
            )
            # Current behavior: None is treated as 0.00.
            assert result[seed_periods[0].id] == Decimal("0.00")

    def test_balance_calc_negative_anchor_balance(self, app, seed_user, seed_periods):
        """Negative anchor balance → propagates correctly.

        Valid for overdrawn accounts.  Income increases the balance,
        expenses decrease it further.
        """
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods, period_index=1,
                txn_type_name="Income", name="Paycheck",
                category_key="Salary", amount="2000.00",
            )
            db.session.commit()

            result, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("-500.00"),
                anchor_period_id=seed_periods[0].id,
                periods=seed_periods[:2],
                transactions=[txn],
            )
            # Period 0: anchor = -500.00, no transactions → -500.00
            assert result[seed_periods[0].id] == Decimal("-500.00")
            # Period 1: -500.00 + 2000.00 income = 1500.00
            assert result[seed_periods[1].id] == Decimal("1500.00")


# ══════════════════════════════════════════════════════════════════════
# 7. Numeric Edge Cases
# ══════════════════════════════════════════════════════════════════════


class TestNumericEdgeCases:
    """Probe Numeric(12,2) field boundaries."""

    def test_transaction_amount_at_db_max(self, app, auth_client, seed_user, seed_periods):
        """Decimal("9999999999.99") -- at the Numeric(12,2) max.

        Should store OK: 10 digits before decimal + 2 after = 12 total.
        """
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods, amount="9999999999.99",
            )
            db.session.commit()
            db.session.refresh(txn)
            assert txn.estimated_amount == Decimal("9999999999.99")

    def test_transaction_amount_exceeds_db_max(self, app, auth_client, seed_user, seed_periods):
        """Decimal("99999999999.99") -- exceeds Numeric(12,2).

        Bug: No application-level guard.  PostgreSQL raises a
        NumericValueOutOfRange (DataError) on flush.
        """
        with app.app_context():
            from sqlalchemy.exc import DataError

            status = db.session.query(Status).filter_by(name="Projected").one()
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=status.id,
                name="Overflow Test",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=txn_type.id,
                estimated_amount=Decimal("99999999999.99"),
            )
            db.session.add(txn)

            # Current behavior: DB raises DataError on flush/commit.
            # Ideal: schema-level max value check would catch this first.
            with pytest.raises(DataError):
                db.session.flush()
            db.session.rollback()

    def test_schema_rejects_negative_amount(self, app, seed_user, seed_periods):
        """TransactionCreateSchema with negative estimated_amount.

        The schema has validate.Range(min=0) which should reject negatives.
        """
        with app.app_context():
            from app.schemas.validation import TransactionCreateSchema
            schema = TransactionCreateSchema()

            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            projected = db.session.query(Status).filter_by(name="Projected").one()

            errors = schema.validate({
                "name": "Bad Item",
                "estimated_amount": "-50.00",
                "pay_period_id": str(seed_periods[0].id),
                "scenario_id": str(seed_user["scenario"].id),
                "category_id": str(seed_user["categories"]["Rent"].id),
                "transaction_type_id": str(expense_type.id),
            })
            assert "estimated_amount" in errors


# ══════════════════════════════════════════════════════════════════════
# 8. Auth Edge Cases
# ══════════════════════════════════════════════════════════════════════


class TestAuthEdgeCases:
    """Cross-user resource access -- confirm defense in depth."""

    def test_access_other_users_transaction(
        self, app, auth_client, seed_user, seed_periods, second_user,
    ):
        """User A tries to view User B's transaction → must get 404.

        Routes filter by current_user.id via _get_owned_transaction().
        Cross-user access should return 404, not 403 (no info leakage).
        Uses the shared second_user fixture from conftest.py.
        """
        with app.app_context():
            # Create a pay period for user 2.
            from app.services import pay_period_service
            periods2 = pay_period_service.generate_pay_periods(
                user_id=second_user["user"].id,
                start_date=date(2026, 1, 2),
                num_periods=2,
                cadence_days=14,
            )
            db.session.flush()

            # Create a transaction for user 2.
            status = db.session.query(Status).filter_by(name="Projected").one()
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            txn2 = Transaction(
                pay_period_id=periods2[0].id,
                scenario_id=second_user["scenario"].id,
                account_id=second_user["account"].id,
                status_id=status.id,
                name="Other's Expense",
                category_id=second_user["categories"]["Rent"].id,
                transaction_type_id=txn_type.id,
                estimated_amount=Decimal("99.99"),
            )
            db.session.add(txn2)
            db.session.commit()

            # Auth client is logged in as user 1 -- try to access user 2's txn.
            resp = auth_client.get(f"/transactions/{txn2.id}/cell")
            assert resp.status_code == 404

            resp = auth_client.patch(
                f"/transactions/{txn2.id}",
                data={"estimated_amount": "1.00"},
            )
            assert resp.status_code == 404

            # Verify DB state unchanged after IDOR PATCH attempt.
            db.session.refresh(txn2)
            assert txn2.estimated_amount == Decimal("99.99"), \
                "IDOR PATCH should not have modified the victim's amount"
            assert txn2.status.name == "Projected", \
                "IDOR PATCH should not have modified the victim's status"

    def test_access_other_users_account(
        self, app, auth_client, seed_user, seed_periods, second_user,
    ):
        """User A tries to edit User B's account → must get redirect/flash.

        Account routes check user_id == current_user.id and redirect with
        a flash warning when the account doesn't belong to the current user.
        Uses the shared second_user fixture from conftest.py.
        """
        with app.app_context():
            # Auth client is logged in as user 1 -- try to edit user 2's account.
            resp = auth_client.get(f"/accounts/{second_user['account'].id}/edit")
            # Current behavior: redirect to accounts list with flash.
            assert resp.status_code == 302
            assert "/accounts" in resp.headers.get("Location", "")
            assert b"Other Checking" not in resp.data

            # Follow redirect to verify the flash warning message.
            follow = auth_client.get(resp.headers["Location"])
            assert b"not found" in follow.data.lower()

            # Verify DB state unchanged after IDOR attempt.
            acct2 = db.session.get(Account, second_user["account"].id)
            assert acct2.name == "Other Checking", \
                "IDOR attempt should not have modified the victim's account"


# ══════════════════════════════════════════════════════════════════════
# 9. SQL Injection Prevention
# ══════════════════════════════════════════════════════════════════════


class TestSQLInjectionPrevention:
    """Verify SQL injection payloads are rejected or stored literally.

    SQLAlchemy uses parameterized queries, so data fields are safe by
    construction.  Query parameters parsed via int() or schema validation
    also reject injection strings.  These tests verify that explicitly.
    """

    def test_grid_period_param_sql_injection(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """SQL injection in ?periods param is safely ignored.

        Flask's request.args.get(..., type=int) returns the default
        when the value cannot be converted, rejecting the injection.
        """
        with app.app_context():
            resp = auth_client.get(
                "/?periods=1;DROP%20TABLE%20budget.transactions--"
            )
            assert resp.status_code == 200

            # Verify the transactions table still exists.
            count = db.session.execute(
                db.text("SELECT count(*) FROM budget.transactions")
            ).scalar()
            assert count is not None

    def test_transaction_patch_sql_in_name(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """SQL injection payload in transaction name is stored literally.

        SQLAlchemy parameterized queries prevent execution.  The payload
        is stored as a plain string in the name column.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods)
            db.session.commit()

            payload = "'; DROP TABLE budget.transactions; --"
            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"name": payload},
            )
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert txn.name == payload

            # Verify the table still exists and the transaction is intact.
            count = db.session.execute(
                db.text("SELECT count(*) FROM budget.transactions")
            ).scalar()
            assert count >= 1

    def test_transaction_patch_sql_in_amount(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """SQL injection in estimated_amount is rejected by schema validation.

        The Marshmallow schema validates estimated_amount as a Decimal.
        A SQL injection string is not a valid decimal and returns 400.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods)
            db.session.commit()
            original_amount = txn.estimated_amount

            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"estimated_amount": "0; DROP TABLE budget.transactions"},
            )
            assert resp.status_code == 400

            db.session.refresh(txn)
            assert txn.estimated_amount == original_amount

            # Verify the table still exists.
            count = db.session.execute(
                db.text("SELECT count(*) FROM budget.transactions")
            ).scalar()
            assert count >= 1


# ══════════════════════════════════════════════════════════════════════
# 10. Cross-Resource IDOR
# ══════════════════════════════════════════════════════════════════════


class TestCrossResourceIDOR:
    """IDOR tests for salary profiles, categories, and transfer templates.

    Authenticated as user 1, attempt to access/modify resources owned
    by user 2.  Each test verifies both the HTTP response and DB state.
    """

    def test_edit_other_users_salary_profile(
        self, app, auth_client, seed_user, seed_periods, second_user,
    ):
        """User A tries to GET User B's salary profile edit page → redirect.

        The salary route checks user_id ownership and redirects with
        a flash warning when the profile doesn't belong to the current user.
        """
        with app.app_context():
            filing_single = db.session.query(FilingStatus).filter_by(
                name="single",
            ).one()

            profile = SalaryProfile(
                user_id=second_user["user"].id,
                scenario_id=second_user["scenario"].id,
                filing_status_id=filing_single.id,
                name="Other User Job",
                annual_salary=Decimal("60000.00"),
                state_code="NC",
            )
            db.session.add(profile)
            db.session.commit()
            profile_id = profile.id
            original_salary = profile.annual_salary

            # Auth client (user 1) tries to access user 2's salary profile.
            resp = auth_client.get(f"/salary/{profile_id}/edit")
            assert resp.status_code == 302

            # Follow redirect to verify flash message.
            follow = auth_client.get(resp.headers["Location"])
            assert b"Salary profile not found" in follow.data

            # Verify profile is unchanged in DB.
            db.session.refresh(profile)
            assert profile.annual_salary == original_salary
            assert profile.name == "Other User Job"

    def test_delete_other_users_category(
        self, app, auth_client, seed_user, seed_periods, second_user,
    ):
        """User A tries to DELETE User B's category → rejected.

        The categories route checks user_id ownership and redirects
        with a flash warning.
        """
        with app.app_context():
            cat = second_user["categories"]["Rent"]
            cat_id = cat.id

            resp = auth_client.post(f"/categories/{cat_id}/delete")
            assert resp.status_code == 302

            # Follow redirect to verify flash message.
            follow = auth_client.get(resp.headers["Location"])
            assert b"Category not found" in follow.data

            # Verify category still exists.
            cat_after = db.session.get(Category, cat_id)
            assert cat_after is not None

    def test_delete_other_users_transfer_template(
        self, app, auth_client, seed_user, seed_periods, second_user,
    ):
        """User A tries to DELETE User B's transfer template → rejected.

        The transfers route checks user_id ownership and redirects
        with a flash warning.
        """
        with app.app_context():
            # Create a savings account and transfer template for second user.
            savings_type = db.session.query(AccountType).filter_by(
                name="Savings",
            ).one()
            savings_acct2 = Account(
                user_id=second_user["user"].id,
                account_type_id=savings_type.id,
                name="Other Savings",
                current_anchor_balance=Decimal("0.00"),
            )
            db.session.add(savings_acct2)
            db.session.flush()

            template2 = TransferTemplate(
                user_id=second_user["user"].id,
                from_account_id=second_user["account"].id,
                to_account_id=savings_acct2.id,
                name="Other Savings Transfer",
                default_amount=Decimal("100.00"),
            )
            db.session.add(template2)
            db.session.commit()
            template2_id = template2.id

            # Auth client (user 1) tries to delete user 2's template.
            resp = auth_client.post(f"/transfers/{template2_id}/delete")
            assert resp.status_code == 302

            # Follow redirect to verify flash message.
            follow = auth_client.get(resp.headers["Location"])
            assert b"Recurring transfer not found" in follow.data

            # Verify template still exists and is active.
            db.session.refresh(template2)
            assert template2.is_active is True
