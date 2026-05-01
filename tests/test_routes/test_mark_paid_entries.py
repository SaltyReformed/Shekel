"""
Shekel Budget App -- Mark-Paid Entry Integration and Status Guard Tests

Tests for Commit 5: auto-populating actual_amount from entries on
mark-paid, Credit status guard on entry-capable transactions, and
post-paid entry mutation actual_amount updates.

Each test verifies exact Decimal values.  Arithmetic is documented
inline so a reviewer can verify by hand.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.ref import Status, TransactionType
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import RecurrencePattern
from app import ref_cache
from app.enums import StatusEnum
from app.services import entry_service


# ── Helpers ──────────────────────────────────────────────────────


def _make_entry(txn_id, user_id, amount="50.00", description="Kroger",
                entry_date=None, is_credit=False):
    """Create an entry directly via ORM (bypasses service validation).

    Uses IDs rather than ORM objects to avoid session detachment
    issues when combined with auth_client HTTP requests.
    """
    entry = TransactionEntry(
        transaction_id=txn_id,
        user_id=user_id,
        amount=Decimal(amount),
        description=description,
        entry_date=entry_date or date(2026, 1, 5),
        is_credit=is_credit,
    )
    db.session.add(entry)
    db.session.flush()
    return entry


def _create_tracked_txn(seed_user, seed_periods):
    """Create a tracked expense transaction with template.

    Creates a minimal template with is_envelope=True
    and a projected expense transaction linked to it.
    """
    every_period = (
        db.session.query(RecurrencePattern)
        .filter_by(name="Every Period").one()
    )
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    projected = db.session.query(Status).filter_by(name="Projected").one()

    rule = RecurrenceRule(
        user_id=seed_user["user"].id,
        pattern_id=every_period.id,
    )
    db.session.add(rule)
    db.session.flush()

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Groceries"].id,
        recurrence_rule_id=rule.id,
        transaction_type_id=expense_type.id,
        name="Tracked Groceries",
        default_amount=Decimal("500.00"),
        is_envelope=True,
    )
    db.session.add(template)
    db.session.flush()

    txn = Transaction(
        template_id=template.id,
        pay_period_id=seed_periods[0].id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=projected.id,
        name="Tracked Groceries",
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=expense_type.id,
        estimated_amount=Decimal("500.00"),
    )
    db.session.add(txn)
    db.session.commit()
    return txn


def _create_non_tracked_txn(seed_user, seed_periods):
    """Create a regular expense transaction without entry tracking."""
    projected = db.session.query(Status).filter_by(name="Projected").one()
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )

    txn = Transaction(
        pay_period_id=seed_periods[0].id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=projected.id,
        name="Non-Tracked Expense",
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=expense_type.id,
        estimated_amount=Decimal("200.00"),
    )
    db.session.add(txn)
    db.session.commit()
    return txn


# ── Mark-Paid with Entries ───────────────────────────────────────


class TestMarkPaidAutoActual:
    """Tests for auto-populating actual_amount from entries on mark-paid."""

    def test_mark_done_auto_populates_actual(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Mark-paid on tracked transaction with entries sets actual to entry sum.

        Setup: Two debit entries of $150 and $250.
        Expected actual: 150 + 250 = $400.
        """
        with app.app_context():
            txn = _create_tracked_txn(seed_user, seed_periods)
            txn_id = txn.id
            user_id = seed_user["user"].id

            _make_entry(txn_id, user_id, amount="150.00", description="Kroger")
            _make_entry(txn_id, user_id, amount="250.00", description="Target")
            db.session.commit()

            resp = auth_client.post(f"/transactions/{txn_id}/mark-done")
            assert resp.status_code == 200

            txn = db.session.get(Transaction, txn_id)
            assert txn.actual_amount == Decimal("400.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)

    def test_mark_done_actual_includes_credit_entries(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Actual includes both debit and credit entries.

        Setup: $300 debit + $100 credit.
        Expected actual: 300 + 100 = $400 (credit is included for analytics).
        """
        with app.app_context():
            txn = _create_tracked_txn(seed_user, seed_periods)
            txn_id = txn.id
            user_id = seed_user["user"].id

            _make_entry(txn_id, user_id, amount="300.00", description="Kroger")
            _make_entry(txn_id, user_id, amount="100.00",
                        description="Amazon", is_credit=True)
            db.session.commit()

            resp = auth_client.post(f"/transactions/{txn_id}/mark-done")
            assert resp.status_code == 200

            txn = db.session.get(Transaction, txn_id)
            # 300 + 100 = 400 total spending.
            assert txn.actual_amount == Decimal("400.00")

    def test_mark_done_no_entries_manual_actual(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Tracked transaction with no entries accepts manual actual from form.

        Setup: Entry-capable template, no entries created.
        Expected: manual actual_amount=350 is accepted (fall-through).
        """
        with app.app_context():
            txn = _create_tracked_txn(seed_user, seed_periods)
            txn_id = txn.id

            resp = auth_client.post(
                f"/transactions/{txn_id}/mark-done",
                data={"actual_amount": "350.00"},
            )
            assert resp.status_code == 200

            txn = db.session.get(Transaction, txn_id)
            assert txn.actual_amount == Decimal("350.00")

    def test_mark_done_no_entries_no_actual(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Tracked transaction with no entries and no form actual keeps None.

        Setup: Entry-capable template, no entries, no form data.
        Expected: actual_amount remains None.
        """
        with app.app_context():
            txn = _create_tracked_txn(seed_user, seed_periods)
            txn_id = txn.id

            resp = auth_client.post(f"/transactions/{txn_id}/mark-done")
            assert resp.status_code == 200

            txn = db.session.get(Transaction, txn_id)
            assert txn.actual_amount is None

    def test_mark_done_entries_override_form_actual(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Entry sum overrides manual actual_amount from the form.

        Setup: Entries sum to $400, form submits actual_amount=999.
        Expected: actual_amount = $400 (entries win).
        """
        with app.app_context():
            txn = _create_tracked_txn(seed_user, seed_periods)
            txn_id = txn.id
            user_id = seed_user["user"].id

            _make_entry(txn_id, user_id, amount="200.00", description="Kroger")
            _make_entry(txn_id, user_id, amount="200.00", description="Target")
            db.session.commit()

            resp = auth_client.post(
                f"/transactions/{txn_id}/mark-done",
                data={"actual_amount": "999.00"},
            )
            assert resp.status_code == 200

            txn = db.session.get(Transaction, txn_id)
            # Entry sum (200 + 200 = 400) overrides form value (999).
            assert txn.actual_amount == Decimal("400.00")

    def test_non_tracked_mark_done_unchanged(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Non-tracked transaction mark-done uses manual actual.

        Setup: Non-tracked expense, no template tracking flag.
        Expected: manual actual_amount from form is accepted.
        """
        with app.app_context():
            txn = _create_non_tracked_txn(seed_user, seed_periods)
            txn_id = txn.id

            resp = auth_client.post(
                f"/transactions/{txn_id}/mark-done",
                data={"actual_amount": "175.00"},
            )
            assert resp.status_code == 200

            txn = db.session.get(Transaction, txn_id)
            assert txn.actual_amount == Decimal("175.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)


# ── Credit Status Guard ──────────────────────────────────────────


class TestCreditStatusGuard:
    """Tests for blocking Credit status on entry-capable transactions."""

    def test_update_rejects_credit_status_tracked(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """PATCH with status_id=CREDIT on tracked transaction returns 400.

        The Credit status conflicts with entry-level credit handling.
        """
        with app.app_context():
            txn = _create_tracked_txn(seed_user, seed_periods)
            txn_id = txn.id
            credit_id = ref_cache.status_id(StatusEnum.CREDIT)

            resp = auth_client.patch(
                f"/transactions/{txn_id}",
                data={"status_id": str(credit_id)},
            )
            assert resp.status_code == 400
            assert b"Credit status" in resp.data
            assert b"entry-level credit" in resp.data

            # Transaction status must be unchanged.
            txn = db.session.get(Transaction, txn_id)
            assert txn.status_id != credit_id

    def test_update_allows_credit_status_non_tracked(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """PATCH with status_id=CREDIT on non-tracked transaction succeeds.

        Legacy credit workflow is unaffected for non-entry-capable
        transactions.
        """
        with app.app_context():
            txn = _create_non_tracked_txn(seed_user, seed_periods)
            txn_id = txn.id
            credit_id = ref_cache.status_id(StatusEnum.CREDIT)

            resp = auth_client.patch(
                f"/transactions/{txn_id}",
                data={"status_id": str(credit_id)},
            )
            assert resp.status_code == 200

            txn = db.session.get(Transaction, txn_id)
            assert txn.status_id == credit_id


# ── Post-Paid Entry Mutations ────────────────────────────────────


class TestPostPaidEntryMutation:
    """Tests for actual_amount updates when entries change on Paid txns."""

    def test_entry_added_after_paid_updates_actual(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Adding an entry to a Paid transaction recalculates actual_amount.

        Setup: Tracked transaction marked Paid with entries summing to $300.
               Then a $50 entry is added.
        Expected: actual_amount updates to 300 + 50 = $350.
        """
        with app.app_context():
            txn = _create_tracked_txn(seed_user, seed_periods)
            txn_id = txn.id
            user_id = seed_user["user"].id

            _make_entry(txn_id, user_id, amount="150.00", description="Kroger")
            _make_entry(txn_id, user_id, amount="150.00", description="Target")
            db.session.commit()

            auth_client.post(f"/transactions/{txn_id}/mark-done")
            txn = db.session.get(Transaction, txn_id)
            assert txn.actual_amount == Decimal("300.00")

            # Add a late entry via the service.
            entry_service.create_entry(
                transaction_id=txn_id,
                user_id=user_id,
                amount=Decimal("50.00"),
                description="Late purchase",
                entry_date=date(2026, 1, 10),
            )
            db.session.commit()

            txn = db.session.get(Transaction, txn_id)
            # 150 + 150 + 50 = 350.
            assert txn.actual_amount == Decimal("350.00")

    def test_entry_deleted_after_paid_updates_actual(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Deleting an entry from a Paid transaction recalculates actual.

        Setup: Tracked transaction with entries summing to $400, marked Paid.
               Then delete a $100 entry.
        Expected: actual_amount updates to 400 - 100 = $300.
        """
        with app.app_context():
            txn = _create_tracked_txn(seed_user, seed_periods)
            txn_id = txn.id
            user_id = seed_user["user"].id

            _make_entry(txn_id, user_id, amount="200.00", description="Kroger")
            del_entry = _make_entry(
                txn_id, user_id, amount="100.00",
                description="Gas station",
            )
            del_entry_id = del_entry.id
            _make_entry(txn_id, user_id, amount="100.00", description="Target")
            db.session.commit()

            auth_client.post(f"/transactions/{txn_id}/mark-done")
            txn = db.session.get(Transaction, txn_id)
            # 200 + 100 + 100 = 400.
            assert txn.actual_amount == Decimal("400.00")

            # Delete the $100 "Gas station" entry.
            entry_service.delete_entry(
                entry_id=del_entry_id,
                user_id=user_id,
            )
            db.session.commit()

            txn = db.session.get(Transaction, txn_id)
            # 200 + 100 = 300 (deleted $100 removed).
            assert txn.actual_amount == Decimal("300.00")

    def test_entry_updated_after_paid_updates_actual(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Updating an entry amount on a Paid transaction recalculates actual.

        Setup: Tracked transaction with entries summing to $400, marked Paid.
               Then change one $200 entry to $250.
        Expected: actual_amount updates to 250 + 200 = $450.
        """
        with app.app_context():
            txn = _create_tracked_txn(seed_user, seed_periods)
            txn_id = txn.id
            user_id = seed_user["user"].id

            edit_entry = _make_entry(
                txn_id, user_id, amount="200.00", description="Kroger",
            )
            edit_entry_id = edit_entry.id
            _make_entry(txn_id, user_id, amount="200.00", description="Target")
            db.session.commit()

            auth_client.post(f"/transactions/{txn_id}/mark-done")
            txn = db.session.get(Transaction, txn_id)
            assert txn.actual_amount == Decimal("400.00")

            # Update the Kroger entry from $200 to $250.
            entry_service.update_entry(
                entry_id=edit_entry_id,
                user_id=user_id,
                amount=Decimal("250.00"),
            )
            db.session.commit()

            txn = db.session.get(Transaction, txn_id)
            # 250 + 200 = 450.
            assert txn.actual_amount == Decimal("450.00")

    def test_projected_entry_mutation_does_not_set_actual(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Entry mutations on projected transactions do not touch actual_amount.

        _update_actual_if_paid only fires for DONE status.
        """
        with app.app_context():
            txn = _create_tracked_txn(seed_user, seed_periods)
            txn_id = txn.id
            user_id = seed_user["user"].id

            # Transaction is in PROJECTED status.
            assert txn.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert txn.actual_amount is None

            entry_service.create_entry(
                transaction_id=txn_id,
                user_id=user_id,
                amount=Decimal("100.00"),
                description="Projected period purchase",
                entry_date=date(2026, 1, 5),
            )
            db.session.commit()

            txn = db.session.get(Transaction, txn_id)
            # actual_amount must remain None for projected transactions.
            assert txn.actual_amount is None
