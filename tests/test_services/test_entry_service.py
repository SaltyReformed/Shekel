"""
Shekel Budget App -- Transaction Entry Service Tests

Comprehensive tests for the entry service CRUD operations,
ownership validation, computation functions, and edge cases.
Each test verifies exact Decimal values for financial correctness.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.ref import Status, TransactionType
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.models.user import User, UserSettings
from app.services import entry_service
from app.services.auth_service import hash_password
from app.schemas.validation import EntryCreateSchema, EntryUpdateSchema
from app.exceptions import NotFoundError, ValidationError
from app import ref_cache
from app.enums import RoleEnum


# ── Helper ────────────────────────────────────────────────────────


def _make_entry(transaction, user, amount="50.00", description="Kroger",
                entry_date=None, is_credit=False):
    """Create an entry directly via ORM (bypasses service validation).

    Used by tests that need pre-existing entries without re-testing
    the full create_entry validation chain.
    """
    entry = TransactionEntry(
        transaction_id=transaction.id,
        user_id=user.id,
        amount=Decimal(amount),
        description=description,
        entry_date=entry_date or date(2026, 1, 5),
        is_credit=is_credit,
    )
    db.session.add(entry)
    db.session.flush()
    return entry


# ── CRUD Tests ────────────────────────────────────────────────────


class TestCreateEntry:
    """Tests for entry_service.create_entry()."""

    def test_create_entry_basic(self, app, db, seed_user, seed_entry_template):
        """Create a debit entry with valid inputs. Verifies all fields persisted."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=user.id,
                amount=Decimal("50.00"),
                description="Kroger",
                entry_date=date(2026, 1, 5),
            )

            assert entry.id is not None
            assert entry.transaction_id == txn.id
            assert entry.user_id == user.id
            assert entry.amount == Decimal("50.00")
            assert entry.description == "Kroger"
            assert entry.entry_date == date(2026, 1, 5)
            assert entry.is_credit is False

    def test_create_entry_credit(self, app, db, seed_user, seed_entry_template):
        """Create a credit entry. is_credit flag is persisted."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=user.id,
                amount=Decimal("75.00"),
                description="Amazon order",
                entry_date=date(2026, 1, 6),
                is_credit=True,
            )

            assert entry.is_credit is True
            assert entry.amount == Decimal("75.00")

    def test_create_entry_returns_flushed_id(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Created entry has a database-assigned id immediately (flushed)."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                amount=Decimal("10.00"),
                description="Test",
                entry_date=date(2026, 1, 5),
            )
            assert isinstance(entry.id, int)
            assert entry.id > 0

    def test_create_entry_rejects_non_tracking_template(
        self, app, db, seed_user, seed_periods,
    ):
        """Reject entry on a transaction whose template has track=False."""
        with app.app_context():
            # Create a template with tracking disabled.
            expense_type = (
                db.session.query(TransactionType).filter_by(name="Expense").one()
            )
            projected = (
                db.session.query(Status).filter_by(name="Projected").one()
            )
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                name="Rent",
                default_amount=Decimal("1500.00"),
                track_individual_purchases=False,
            )
            db.session.add(template)
            db.session.flush()

            txn = Transaction(
                template_id=template.id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Rent",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("1500.00"),
            )
            db.session.add(txn)
            db.session.flush()

            with pytest.raises(ValidationError, match="does not support"):
                entry_service.create_entry(
                    transaction_id=txn.id,
                    user_id=seed_user["user"].id,
                    amount=Decimal("50.00"),
                    description="Test",
                    entry_date=date(2026, 1, 5),
                )

    def test_create_entry_rejects_no_template(
        self, app, db, seed_user, seed_periods,
    ):
        """Reject entry on an ad-hoc transaction (template_id=None)."""
        with app.app_context():
            expense_type = (
                db.session.query(TransactionType).filter_by(name="Expense").one()
            )
            projected = (
                db.session.query(Status).filter_by(name="Projected").one()
            )
            txn = Transaction(
                template_id=None,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Ad-hoc expense",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("100.00"),
            )
            db.session.add(txn)
            db.session.flush()

            with pytest.raises(ValidationError, match="does not support"):
                entry_service.create_entry(
                    transaction_id=txn.id,
                    user_id=seed_user["user"].id,
                    amount=Decimal("50.00"),
                    description="Test",
                    entry_date=date(2026, 1, 5),
                )

    def test_create_entry_rejects_transfer(
        self, app, db, seed_user, seed_entry_template, seed_periods,
    ):
        """Reject entry on a transaction that is a transfer shadow."""
        with app.app_context():
            from app.models.transfer import Transfer
            from app.models.account import Account
            from app.models.ref import AccountType

            txn_id = seed_entry_template["transaction"].id
            user_id = seed_user["user"].id
            account_id = seed_user["account"].id
            scenario_id = seed_user["scenario"].id
            period_id = seed_periods[0].id

            # Reload transaction in this session context.
            txn = db.session.get(Transaction, txn_id)

            # Create a second account for the transfer (different accounts required).
            checking_type = (
                db.session.query(AccountType).filter_by(name="Checking").one()
            )
            second_account = Account(
                user_id=user_id,
                account_type_id=checking_type.id,
                name="Savings",
                current_anchor_balance=Decimal("500.00"),
            )
            db.session.add(second_account)
            db.session.flush()

            projected = (
                db.session.query(Status).filter_by(name="Projected").one()
            )
            transfer = Transfer(
                user_id=user_id,
                from_account_id=account_id,
                to_account_id=second_account.id,
                amount=Decimal("100.00"),
                pay_period_id=period_id,
                scenario_id=scenario_id,
                status_id=projected.id,
                name="Test Transfer",
            )
            db.session.add(transfer)
            db.session.flush()

            txn.transfer_id = transfer.id
            db.session.flush()

            with pytest.raises(ValidationError, match="transfer"):
                entry_service.create_entry(
                    transaction_id=txn_id,
                    user_id=user_id,
                    amount=Decimal("50.00"),
                    description="Test",
                    entry_date=date(2026, 1, 5),
                )

    def test_create_entry_rejects_income(
        self, app, db, seed_user, seed_periods,
    ):
        """Reject entry on an income transaction (even with tracking enabled)."""
        with app.app_context():
            income_type = (
                db.session.query(TransactionType).filter_by(name="Income").one()
            )
            projected = (
                db.session.query(Status).filter_by(name="Projected").one()
            )
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                name="Salary",
                default_amount=Decimal("3000.00"),
                track_individual_purchases=True,
            )
            db.session.add(template)
            db.session.flush()

            txn = Transaction(
                template_id=template.id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Salary",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=Decimal("3000.00"),
            )
            db.session.add(txn)
            db.session.flush()

            with pytest.raises(ValidationError, match="income"):
                entry_service.create_entry(
                    transaction_id=txn.id,
                    user_id=seed_user["user"].id,
                    amount=Decimal("50.00"),
                    description="Test",
                    entry_date=date(2026, 1, 5),
                )

    def test_create_entry_rejects_other_user(
        self, app, db, seed_user, seed_second_user,
        seed_entry_template,
    ):
        """Reject entry when user does not own the transaction (NotFoundError, not 403)."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            other_user = seed_second_user["user"]

            with pytest.raises(NotFoundError):
                entry_service.create_entry(
                    transaction_id=txn.id,
                    user_id=other_user.id,
                    amount=Decimal("50.00"),
                    description="Test",
                    entry_date=date(2026, 1, 5),
                )

    def test_create_entry_rejects_nonexistent_transaction(
        self, app, db, seed_user,
    ):
        """NotFoundError when transaction_id does not exist."""
        with app.app_context():
            with pytest.raises(NotFoundError):
                entry_service.create_entry(
                    transaction_id=999999,
                    user_id=seed_user["user"].id,
                    amount=Decimal("50.00"),
                    description="Test",
                    entry_date=date(2026, 1, 5),
                )

    def test_create_entry_rejects_cancelled_transaction(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Cannot add entries to a cancelled transaction."""
        with app.app_context():
            txn_id = seed_entry_template["transaction"].id
            user_id = seed_user["user"].id

            # Reload transaction in this session context.
            txn = db.session.get(Transaction, txn_id)
            cancelled = (
                db.session.query(Status).filter_by(name="Cancelled").one()
            )
            txn.status_id = cancelled.id
            db.session.flush()

            with pytest.raises(ValidationError, match="cancelled"):
                entry_service.create_entry(
                    transaction_id=txn_id,
                    user_id=user_id,
                    amount=Decimal("50.00"),
                    description="Test",
                    entry_date=date(2026, 1, 5),
                )

    def test_create_entry_rejects_credit_status_transaction(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Cannot add entries to a transaction with legacy Credit status.

        Entry-capable transactions handle credit at the entry level (OQ-10).
        If Credit status is somehow set, entries should be blocked.
        """
        with app.app_context():
            txn_id = seed_entry_template["transaction"].id
            user_id = seed_user["user"].id

            # Reload transaction in this session context.
            txn = db.session.get(Transaction, txn_id)
            credit = db.session.query(Status).filter_by(name="Credit").one()
            txn.status_id = credit.id
            db.session.flush()

            with pytest.raises(ValidationError, match="Credit status"):
                entry_service.create_entry(
                    transaction_id=txn_id,
                    user_id=user_id,
                    amount=Decimal("50.00"),
                    description="Test",
                    entry_date=date(2026, 1, 5),
                )

    def test_create_entry_on_done_transaction(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Entries allowed on Paid (DONE) transactions for late-posting purchases.

        Per scope doc section 4.2: "If entries are added to a transaction
        already in Paid status, the actual amount should update to reflect
        the new sum."
        """
        with app.app_context():
            txn_id = seed_entry_template["transaction"].id
            user_id = seed_user["user"].id

            # Reload transaction in this session context.
            txn = db.session.get(Transaction, txn_id)
            done = db.session.query(Status).filter_by(name="Paid").one()
            txn.status_id = done.id
            db.session.flush()

            entry = entry_service.create_entry(
                transaction_id=txn_id,
                user_id=user_id,
                amount=Decimal("42.50"),
                description="Late posting purchase",
                entry_date=date(2026, 1, 10),
            )

            assert entry.id is not None
            assert entry.amount == Decimal("42.50")

    def test_create_entry_boundary_minimum_amount(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Minimum valid amount (Decimal('0.01')) succeeds."""
        with app.app_context():
            entry = entry_service.create_entry(
                transaction_id=seed_entry_template["transaction"].id,
                user_id=seed_user["user"].id,
                amount=Decimal("0.01"),
                description="Penny item",
                entry_date=date(2026, 1, 5),
            )
            assert entry.amount == Decimal("0.01")

    def test_create_entry_boundary_large_amount(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Large amount within Numeric(12,2) precision succeeds."""
        with app.app_context():
            entry = entry_service.create_entry(
                transaction_id=seed_entry_template["transaction"].id,
                user_id=seed_user["user"].id,
                amount=Decimal("9999999999.99"),
                description="Expensive item",
                entry_date=date(2026, 1, 5),
            )
            assert entry.amount == Decimal("9999999999.99")

    def test_create_entry_description_at_max_length(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Description at exactly 200 characters succeeds."""
        with app.app_context():
            desc = "A" * 200
            entry = entry_service.create_entry(
                transaction_id=seed_entry_template["transaction"].id,
                user_id=seed_user["user"].id,
                amount=Decimal("10.00"),
                description=desc,
                entry_date=date(2026, 1, 5),
            )
            assert len(entry.description) == 200


# ── Companion Ownership Tests ─────────────────────────────────────


class TestCompanionAccess:
    """Tests for companion user access to entry operations."""

    def test_create_entry_as_companion_on_linked_owner(
        self, app, db, seed_user, seed_entry_template, seed_companion,
    ):
        """Companion can create entries on their linked owner's transactions."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            companion = seed_companion["user"]

            entry = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=companion.id,
                amount=Decimal("35.00"),
                description="Companion purchase",
                entry_date=date(2026, 1, 5),
            )

            assert entry.id is not None
            assert entry.user_id == companion.id

    def test_create_entry_as_companion_on_different_owner(
        self, app, db, seed_second_user, seed_entry_template, seed_companion,
    ):
        """Companion cannot create entries on transactions owned by a different user."""
        with app.app_context():
            # seed_entry_template belongs to seed_user.
            # seed_companion is linked to seed_user.
            # seed_second_user owns different data.
            # Create a transaction owned by seed_second_user.
            expense_type = (
                db.session.query(TransactionType).filter_by(name="Expense").one()
            )
            projected = (
                db.session.query(Status).filter_by(name="Projected").one()
            )
            from app.models.pay_period import PayPeriod
            from app.services import pay_period_service

            periods = pay_period_service.generate_pay_periods(
                user_id=seed_second_user["user"].id,
                start_date=date(2026, 1, 2),
                num_periods=3,
                cadence_days=14,
            )
            db.session.flush()

            template = TransactionTemplate(
                user_id=seed_second_user["user"].id,
                account_id=seed_second_user["account"].id,
                category_id=seed_second_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                name="Other Groceries",
                default_amount=Decimal("400.00"),
                track_individual_purchases=True,
            )
            db.session.add(template)
            db.session.flush()

            txn = Transaction(
                template_id=template.id,
                pay_period_id=periods[0].id,
                scenario_id=seed_second_user["scenario"].id,
                account_id=seed_second_user["account"].id,
                status_id=projected.id,
                name="Other Groceries",
                category_id=seed_second_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("400.00"),
            )
            db.session.add(txn)
            db.session.flush()

            companion = seed_companion["user"]
            with pytest.raises(NotFoundError):
                entry_service.create_entry(
                    transaction_id=txn.id,
                    user_id=companion.id,
                    amount=Decimal("50.00"),
                    description="Unauthorized",
                    entry_date=date(2026, 1, 5),
                )

    def test_resolve_owner_id_for_owner(self, app, db, seed_user):
        """Owner user resolves to their own id."""
        with app.app_context():
            owner = seed_user["user"]
            result = entry_service._resolve_owner_id(owner.id)
            assert result == owner.id

    def test_resolve_owner_id_for_companion(
        self, app, db, seed_user, seed_companion,
    ):
        """Companion user resolves to linked_owner_id."""
        with app.app_context():
            companion = seed_companion["user"]
            owner = seed_user["user"]
            result = entry_service._resolve_owner_id(companion.id)
            assert result == owner.id

    def test_resolve_owner_id_nonexistent_user(self, app, db):
        """NotFoundError for a user_id that does not exist."""
        with app.app_context():
            with pytest.raises(NotFoundError, match="User not found"):
                entry_service._resolve_owner_id(999999)

    def test_resolve_owner_id_companion_no_linked_owner(
        self, app, db, seed_user,
    ):
        """ValidationError when companion has linked_owner_id=None.

        This is a data integrity issue -- companion accounts must always
        have a linked owner.
        """
        with app.app_context():
            companion_role_id = ref_cache.role_id(RoleEnum.COMPANION)
            broken_companion = User(
                email="broken@shekel.local",
                password_hash=hash_password("test"),
                display_name="Broken Companion",
                role_id=companion_role_id,
                linked_owner_id=None,
            )
            db.session.add(broken_companion)
            db.session.flush()
            settings = UserSettings(user_id=broken_companion.id)
            db.session.add(settings)
            db.session.flush()

            with pytest.raises(ValidationError, match="no linked owner"):
                entry_service._resolve_owner_id(broken_companion.id)


# ── Update Tests ──────────────────────────────────────────────────


class TestUpdateEntry:
    """Tests for entry_service.update_entry()."""

    def test_update_entry_amount(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Update amount on an existing entry."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"], amount="50.00")

            updated = entry_service.update_entry(
                entry.id, seed_user["user"].id, amount=Decimal("75.00"),
            )

            assert updated.amount == Decimal("75.00")
            assert updated.id == entry.id

    def test_update_entry_credit_toggle(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Toggle is_credit from False to True."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"], is_credit=False)

            updated = entry_service.update_entry(
                entry.id, seed_user["user"].id, is_credit=True,
            )

            assert updated.is_credit is True

    def test_update_entry_description(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Update description field."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"], description="Old")

            updated = entry_service.update_entry(
                entry.id, seed_user["user"].id, description="New store",
            )

            assert updated.description == "New store"

    def test_update_entry_date(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Update entry_date field."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"])

            new_date = date(2026, 1, 10)
            updated = entry_service.update_entry(
                entry.id, seed_user["user"].id, entry_date=new_date,
            )

            assert updated.entry_date == new_date

    def test_update_entry_multiple_fields(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Update multiple fields in one call."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"])

            updated = entry_service.update_entry(
                entry.id, seed_user["user"].id,
                amount=Decimal("99.99"),
                description="Updated",
                is_credit=True,
            )

            assert updated.amount == Decimal("99.99")
            assert updated.description == "Updated"
            assert updated.is_credit is True

    def test_update_entry_empty_kwargs(
        self, app, db, seed_user, seed_entry_template,
    ):
        """ValidationError when no fields to update are provided."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"])

            with pytest.raises(ValidationError, match="No fields to update"):
                entry_service.update_entry(entry.id, seed_user["user"].id)

    def test_update_entry_unknown_kwargs(
        self, app, db, seed_user, seed_entry_template,
    ):
        """ValidationError when unknown field names are passed."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"])

            with pytest.raises(ValidationError, match="Cannot update fields"):
                entry_service.update_entry(
                    entry.id, seed_user["user"].id,
                    transaction_id=999,
                )

    def test_update_entry_rejects_other_user(
        self, app, db, seed_user, seed_second_user, seed_entry_template,
    ):
        """NotFoundError when updating an entry the user does not own."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"])

            with pytest.raises(NotFoundError):
                entry_service.update_entry(
                    entry.id, seed_second_user["user"].id,
                    amount=Decimal("99.00"),
                )

    def test_update_entry_nonexistent(self, app, db, seed_user):
        """NotFoundError when entry_id does not exist."""
        with app.app_context():
            with pytest.raises(NotFoundError):
                entry_service.update_entry(
                    999999, seed_user["user"].id, amount=Decimal("10.00"),
                )


# ── Delete Tests ──────────────────────────────────────────────────


class TestDeleteEntry:
    """Tests for entry_service.delete_entry()."""

    def test_delete_entry(self, app, db, seed_user, seed_entry_template):
        """Hard-delete an entry. Entry no longer exists in the database."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"])
            entry_id = entry.id

            result = entry_service.delete_entry(entry_id, seed_user["user"].id)

            assert result == txn.id
            assert db.session.get(TransactionEntry, entry_id) is None

    def test_delete_entry_returns_transaction_id(
        self, app, db, seed_user, seed_entry_template,
    ):
        """delete_entry returns the parent transaction_id for CC Payback sync."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"])

            result = entry_service.delete_entry(entry.id, seed_user["user"].id)
            assert result == txn.id

    def test_delete_entry_rejects_other_user(
        self, app, db, seed_user, seed_second_user, seed_entry_template,
    ):
        """NotFoundError when deleting an entry the user does not own."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"])

            with pytest.raises(NotFoundError):
                entry_service.delete_entry(
                    entry.id, seed_second_user["user"].id,
                )

    def test_delete_entry_nonexistent(self, app, db, seed_user):
        """NotFoundError when entry_id does not exist."""
        with app.app_context():
            with pytest.raises(NotFoundError):
                entry_service.delete_entry(999999, seed_user["user"].id)


# ── Get Entries Tests ─────────────────────────────────────────────


class TestGetEntries:
    """Tests for entry_service.get_entries_for_transaction()."""

    def test_get_entries_ordered_by_date(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Entries returned in entry_date ASC order."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            # Create entries out of chronological order.
            _make_entry(txn, user, description="Third",
                        entry_date=date(2026, 1, 10))
            _make_entry(txn, user, description="First",
                        entry_date=date(2026, 1, 3))
            _make_entry(txn, user, description="Second",
                        entry_date=date(2026, 1, 7))

            entries = entry_service.get_entries_for_transaction(
                txn.id, user.id,
            )

            assert len(entries) == 3
            assert entries[0].description == "First"
            assert entries[1].description == "Second"
            assert entries[2].description == "Third"

    def test_get_entries_empty(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Empty list when transaction has no entries."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entries = entry_service.get_entries_for_transaction(
                txn.id, seed_user["user"].id,
            )
            assert entries == []

    def test_get_entries_rejects_other_user(
        self, app, db, seed_user, seed_second_user, seed_entry_template,
    ):
        """NotFoundError when requesting entries for another user's transaction."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            with pytest.raises(NotFoundError):
                entry_service.get_entries_for_transaction(
                    txn.id, seed_second_user["user"].id,
                )

    def test_get_entries_nonexistent_transaction(self, app, db, seed_user):
        """NotFoundError when transaction does not exist."""
        with app.app_context():
            with pytest.raises(NotFoundError):
                entry_service.get_entries_for_transaction(
                    999999, seed_user["user"].id,
                )


# ── Computation Tests ──────────────────────────────���──────────────


class TestComputeEntrySums:
    """Tests for entry_service.compute_entry_sums()."""

    def test_compute_entry_sums_all_debit(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Sum debit entries: (180, 0).

        $50 + $100 + $30 = $180 debit, $0 credit.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entries = [
                _make_entry(txn, user, amount="50.00"),
                _make_entry(txn, user, amount="100.00"),
                _make_entry(txn, user, amount="30.00"),
            ]

            sum_debit, sum_credit = entry_service.compute_entry_sums(entries)

            assert sum_debit == Decimal("180.00")
            assert sum_credit == Decimal("0")

    def test_compute_entry_sums_mixed(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Mixed entries: (150, 80).

        Debit: $100 + $50 = $150.  Credit: $80.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entries = [
                _make_entry(txn, user, amount="100.00"),
                _make_entry(txn, user, amount="50.00"),
                _make_entry(txn, user, amount="80.00", is_credit=True),
            ]

            sum_debit, sum_credit = entry_service.compute_entry_sums(entries)

            assert sum_debit == Decimal("150.00")
            assert sum_credit == Decimal("80.00")

    def test_compute_entry_sums_empty(self, app):
        """Empty list returns (0, 0)."""
        with app.app_context():
            sum_debit, sum_credit = entry_service.compute_entry_sums([])

            assert sum_debit == Decimal("0")
            assert sum_credit == Decimal("0")

    def test_compute_entry_sums_single_debit(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Single debit entry: (50, 0)."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"], amount="50.00")

            sum_debit, sum_credit = entry_service.compute_entry_sums([entry])

            assert sum_debit == Decimal("50.00")
            assert sum_credit == Decimal("0")

    def test_compute_entry_sums_single_credit(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Single credit entry: (0, 75)."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(
                txn, seed_user["user"], amount="75.00", is_credit=True,
            )

            sum_debit, sum_credit = entry_service.compute_entry_sums([entry])

            assert sum_debit == Decimal("0")
            assert sum_credit == Decimal("75.00")

    def test_compute_entry_sums_all_credit(
        self, app, db, seed_user, seed_entry_template,
    ):
        """All credit entries: (0, 250).

        $100 + $150 = $250 credit, $0 debit.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entries = [
                _make_entry(txn, user, amount="100.00", is_credit=True),
                _make_entry(txn, user, amount="150.00", is_credit=True),
            ]

            sum_debit, sum_credit = entry_service.compute_entry_sums(entries)

            assert sum_debit == Decimal("0")
            assert sum_credit == Decimal("250.00")


class TestComputeRemaining:
    """Tests for entry_service.compute_remaining()."""

    def test_compute_remaining_under_budget(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Under budget: remaining = 500 - 330 = 170.

        Entries: $200 debit + $130 debit = $330 total.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entries = [
                _make_entry(txn, user, amount="200.00"),
                _make_entry(txn, user, amount="130.00"),
            ]

            remaining = entry_service.compute_remaining(
                Decimal("500.00"), entries,
            )

            assert remaining == Decimal("170.00")

    def test_compute_remaining_over_budget(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Over budget: remaining = 500 - 530 = -30.

        Negative remaining means overspent.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entries = [
                _make_entry(txn, user, amount="300.00"),
                _make_entry(txn, user, amount="230.00"),
            ]

            remaining = entry_service.compute_remaining(
                Decimal("500.00"), entries,
            )

            assert remaining == Decimal("-30.00")

    def test_compute_remaining_zero(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Exactly on budget: remaining = 500 - 500 = 0."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entries = [
                _make_entry(txn, user, amount="300.00"),
                _make_entry(txn, user, amount="200.00"),
            ]

            remaining = entry_service.compute_remaining(
                Decimal("500.00"), entries,
            )

            assert remaining == Decimal("0")

    def test_compute_remaining_with_credit_entries(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Credit entries count toward budget consumption.

        remaining = 500 - (200 debit + 100 credit) = 200.
        Payment method doesn't affect remaining budget.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entries = [
                _make_entry(txn, user, amount="200.00"),
                _make_entry(txn, user, amount="100.00", is_credit=True),
            ]

            remaining = entry_service.compute_remaining(
                Decimal("500.00"), entries,
            )

            assert remaining == Decimal("200.00")

    def test_compute_remaining_zero_estimated(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Estimated amount of 0 with entries: remaining goes negative.

        remaining = 0 - 50 = -50.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"], amount="50.00")

            remaining = entry_service.compute_remaining(
                Decimal("0"), [entry],
            )

            assert remaining == Decimal("-50.00")

    def test_compute_remaining_empty_entries(self, app):
        """No entries: remaining equals the estimated amount."""
        with app.app_context():
            remaining = entry_service.compute_remaining(
                Decimal("500.00"), [],
            )

            assert remaining == Decimal("500.00")

    def test_compute_remaining_large_amounts(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Large amounts within Numeric(12,2) precision.

        remaining = 9999999999.99 - 9999999999.98 = 0.01.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(
                txn, seed_user["user"], amount="9999999999.98",
            )

            remaining = entry_service.compute_remaining(
                Decimal("9999999999.99"), [entry],
            )

            assert remaining == Decimal("0.01")


class TestComputeActualFromEntries:
    """Tests for entry_service.compute_actual_from_entries()."""

    def test_compute_actual_includes_credit(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Actual = sum of all entries (debit + credit).

        $200 debit + $100 debit + $100 credit = $400.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entries = [
                _make_entry(txn, user, amount="200.00"),
                _make_entry(txn, user, amount="100.00"),
                _make_entry(txn, user, amount="100.00", is_credit=True),
            ]

            actual = entry_service.compute_actual_from_entries(entries)
            assert actual == Decimal("400.00")

    def test_compute_actual_empty(self, app):
        """Empty entries: actual = 0."""
        with app.app_context():
            actual = entry_service.compute_actual_from_entries([])
            assert actual == Decimal("0")

    def test_compute_actual_single_entry(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Single entry: actual equals that entry's amount."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"], amount="42.50")

            actual = entry_service.compute_actual_from_entries([entry])
            assert actual == Decimal("42.50")

    def test_compute_actual_all_credit(
        self, app, db, seed_user, seed_entry_template,
    ):
        """All credit entries: actual still equals their sum.

        $80 + $120 = $200 (total spending regardless of payment method).
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entries = [
                _make_entry(txn, user, amount="80.00", is_credit=True),
                _make_entry(txn, user, amount="120.00", is_credit=True),
            ]

            actual = entry_service.compute_actual_from_entries(entries)
            assert actual == Decimal("200.00")


# ── Date Validation Tests (OP-4) ─────────────────────────────────


class TestCheckEntryDateInPeriod:
    """Tests for entry_service.check_entry_date_in_period()."""

    def test_date_within_period(
        self, app, db, seed_user, seed_entry_template, seed_periods,
    ):
        """Date inside the pay period returns True.

        Period 0: 2026-01-02 to 2026-01-15.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            result = entry_service.check_entry_date_in_period(
                date(2026, 1, 5), txn,
            )
            assert result is True

    def test_date_before_period(
        self, app, db, seed_user, seed_entry_template, seed_periods,
    ):
        """Date before the pay period start returns False."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            result = entry_service.check_entry_date_in_period(
                date(2025, 12, 31), txn,
            )
            assert result is False

    def test_date_after_period(
        self, app, db, seed_user, seed_entry_template, seed_periods,
    ):
        """Date after the pay period end returns False."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            result = entry_service.check_entry_date_in_period(
                date(2026, 1, 20), txn,
            )
            assert result is False

    def test_date_on_period_start(
        self, app, db, seed_user, seed_entry_template, seed_periods,
    ):
        """Date exactly on period start_date returns True."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            period = txn.pay_period
            result = entry_service.check_entry_date_in_period(
                period.start_date, txn,
            )
            assert result is True

    def test_date_on_period_end(
        self, app, db, seed_user, seed_entry_template, seed_periods,
    ):
        """Date exactly on period end_date returns True."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            period = txn.pay_period
            result = entry_service.check_entry_date_in_period(
                period.end_date, txn,
            )
            assert result is True


# ── Schema Tests ──────────────────────────────────────────────────


class TestEntryCreateSchema:
    """Tests for EntryCreateSchema validation."""

    def test_valid_create_data(self, app):
        """All required fields present and valid."""
        with app.app_context():
            schema = EntryCreateSchema()
            data = schema.load({
                "amount": "50.00",
                "description": "Kroger",
                "entry_date": "2026-01-05",
            })
            assert data["amount"] == Decimal("50.00")
            assert data["description"] == "Kroger"
            assert data["entry_date"] == date(2026, 1, 5)
            assert data["is_credit"] is False

    def test_schema_rejects_zero_amount(self, app):
        """Amount of 0 is rejected (min is 0.01)."""
        with app.app_context():
            schema = EntryCreateSchema()
            from marshmallow import ValidationError as MarshmallowError
            with pytest.raises(MarshmallowError) as exc_info:
                schema.load({
                    "amount": "0",
                    "description": "Test",
                    "entry_date": "2026-01-05",
                })
            assert "amount" in exc_info.value.messages

    def test_schema_rejects_negative_amount(self, app):
        """Negative amount is rejected."""
        with app.app_context():
            schema = EntryCreateSchema()
            from marshmallow import ValidationError as MarshmallowError
            with pytest.raises(MarshmallowError) as exc_info:
                schema.load({
                    "amount": "-10.00",
                    "description": "Test",
                    "entry_date": "2026-01-05",
                })
            assert "amount" in exc_info.value.messages

    def test_schema_accepts_minimum_amount(self, app):
        """Amount of exactly 0.01 is accepted."""
        with app.app_context():
            schema = EntryCreateSchema()
            data = schema.load({
                "amount": "0.01",
                "description": "Penny",
                "entry_date": "2026-01-05",
            })
            assert data["amount"] == Decimal("0.01")

    def test_schema_rejects_empty_description(self, app):
        """Empty description is rejected (min length 1)."""
        with app.app_context():
            schema = EntryCreateSchema()
            from marshmallow import ValidationError as MarshmallowError
            with pytest.raises(MarshmallowError) as exc_info:
                schema.load({
                    "amount": "50.00",
                    "description": "",
                    "entry_date": "2026-01-05",
                })
            # strip_empty_strings removes "" -> missing required field
            assert "description" in exc_info.value.messages

    def test_schema_rejects_description_too_long(self, app):
        """Description over 200 characters is rejected."""
        with app.app_context():
            schema = EntryCreateSchema()
            from marshmallow import ValidationError as MarshmallowError
            with pytest.raises(MarshmallowError) as exc_info:
                schema.load({
                    "amount": "50.00",
                    "description": "A" * 201,
                    "entry_date": "2026-01-05",
                })
            assert "description" in exc_info.value.messages

    def test_schema_accepts_single_char_description(self, app):
        """Description of exactly 1 character is valid."""
        with app.app_context():
            schema = EntryCreateSchema()
            data = schema.load({
                "amount": "10.00",
                "description": "X",
                "entry_date": "2026-01-05",
            })
            assert data["description"] == "X"

    def test_schema_accepts_max_length_description(self, app):
        """Description of exactly 200 characters is valid."""
        with app.app_context():
            schema = EntryCreateSchema()
            desc = "B" * 200
            data = schema.load({
                "amount": "10.00",
                "description": desc,
                "entry_date": "2026-01-05",
            })
            assert data["description"] == desc

    def test_schema_is_credit_default_false(self, app):
        """is_credit defaults to False when omitted."""
        with app.app_context():
            schema = EntryCreateSchema()
            data = schema.load({
                "amount": "50.00",
                "description": "Test",
                "entry_date": "2026-01-05",
            })
            assert data["is_credit"] is False

    def test_schema_is_credit_true(self, app):
        """is_credit can be set to True."""
        with app.app_context():
            schema = EntryCreateSchema()
            data = schema.load({
                "amount": "50.00",
                "description": "Test",
                "entry_date": "2026-01-05",
                "is_credit": True,
            })
            assert data["is_credit"] is True

    def test_schema_strips_empty_strings(self, app):
        """Empty string fields are stripped (HTML form pattern)."""
        with app.app_context():
            schema = EntryCreateSchema()
            data = schema.load({
                "amount": "50.00",
                "description": "Kroger",
                "entry_date": "2026-01-05",
                "is_credit": "",  # Empty string from unchecked checkbox
            })
            # is_credit reverts to default (False) after strip
            assert data["is_credit"] is False

    def test_schema_amount_quantized(self, app):
        """Amount with extra decimal places is quantized to 2 places.

        Marshmallow's Decimal(places=2) handles this by rounding.
        """
        with app.app_context():
            schema = EntryCreateSchema()
            data = schema.load({
                "amount": "50.999",
                "description": "Test",
                "entry_date": "2026-01-05",
            })
            # Marshmallow with places=2 quantizes to 2 decimal places.
            assert data["amount"] == Decimal("51.00")


class TestEntryUpdateSchema:
    """Tests for EntryUpdateSchema validation."""

    def test_update_partial_amount(self, app):
        """Only amount provided -- valid partial update."""
        with app.app_context():
            schema = EntryUpdateSchema()
            data = schema.load({"amount": "75.00"})
            assert data["amount"] == Decimal("75.00")
            assert "description" not in data

    def test_update_partial_is_credit_only(self, app):
        """Only is_credit provided -- valid partial update."""
        with app.app_context():
            schema = EntryUpdateSchema()
            data = schema.load({"is_credit": True})
            assert data["is_credit"] is True
            assert "amount" not in data

    def test_update_no_fields(self, app):
        """No fields provided -- empty dict (service handles this error)."""
        with app.app_context():
            schema = EntryUpdateSchema()
            data = schema.load({})
            assert data == {}

    def test_update_rejects_zero_amount(self, app):
        """Amount of 0 is rejected in update schema too."""
        with app.app_context():
            schema = EntryUpdateSchema()
            from marshmallow import ValidationError as MarshmallowError
            with pytest.raises(MarshmallowError) as exc_info:
                schema.load({"amount": "0"})
            assert "amount" in exc_info.value.messages

    def test_update_rejects_negative_amount(self, app):
        """Negative amount is rejected in update schema."""
        with app.app_context():
            schema = EntryUpdateSchema()
            from marshmallow import ValidationError as MarshmallowError
            with pytest.raises(MarshmallowError) as exc_info:
                schema.load({"amount": "-5.00"})
            assert "amount" in exc_info.value.messages

    def test_update_unknown_fields_excluded(self, app):
        """Unknown fields are excluded by BaseSchema (Meta.unknown = EXCLUDE)."""
        with app.app_context():
            schema = EntryUpdateSchema()
            data = schema.load({
                "amount": "50.00",
                "bogus_field": "ignored",
            })
            assert "bogus_field" not in data
            assert data["amount"] == Decimal("50.00")
