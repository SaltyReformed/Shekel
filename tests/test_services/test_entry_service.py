"""
Shekel Budget App -- Transaction Entry Service Tests

Comprehensive tests for the entry service CRUD operations,
ownership validation, computation functions, and edge cases.
Each test verifies exact Decimal values for financial correctness.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.ref import Status, TransactionType
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.models.user import User, UserSettings
from app.services import entry_service, pay_period_write
from app.services.auth_service import hash_password
from app.schemas.validation import EntryCreateSchema, EntryUpdateSchema
from app.exceptions import NotFoundError, ValidationError
from app import ref_cache
from app.enums import RoleEnum, StatusEnum
from app.services import account_service, pay_period_service, status_seam
from app.utils.dates import display_today
from app.models.account import AccountAnchorHistory
from tests._test_helpers import mark_purchase_settled


# ── Helper ────────────────────────────────────────────────────────


def _make_entry(transaction, user, amount="50.00", description="Kroger",
                purchased_on=None, is_credit=False):
    """Create an entry directly via ORM (bypasses service validation).

    Used by tests that need pre-existing entries without re-testing
    the full create_entry validation chain.
    """
    entry = TransactionEntry(
        transaction_id=transaction.id, account_id=transaction.account_id,
        user_id=user.id,
        amount=Decimal(amount),
        description=description,
        purchased_on=purchased_on or date(2026, 1, 5),
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
                details=entry_service.EntryDetails(
                    amount=Decimal("50.00"),
                    description="Kroger",
                    purchased_on=date(2026, 1, 5),
                ),
            )

            assert entry.id is not None
            assert entry.transaction_id == txn.id
            assert entry.user_id == user.id
            assert entry.amount == Decimal("50.00")
            assert entry.description == "Kroger"
            assert entry.purchased_on == date(2026, 1, 5)
            assert entry.is_credit is False

    def test_create_entry_credit(self, app, db, seed_user, seed_entry_template):
        """Create a credit entry. is_credit flag is persisted."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=user.id,
                details=entry_service.EntryDetails(
                    amount=Decimal("75.00"),
                    description="Amazon order",
                    purchased_on=date(2026, 1, 6),
                    is_credit=True,
                ),
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
                details=entry_service.EntryDetails(
                    amount=Decimal("10.00"),
                    description="Test",
                    purchased_on=date(2026, 1, 5),
                ),
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
                is_envelope=False,
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
                    details=entry_service.EntryDetails(
                        amount=Decimal("50.00"),
                        description="Test",
                        purchased_on=date(2026, 1, 5),
                    ),
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
                    details=entry_service.EntryDetails(
                        amount=Decimal("50.00"),
                        description="Test",
                        purchased_on=date(2026, 1, 5),
                    ),
                )

    def test_create_entry_rejects_transfer(
        self, app, db, seed_user, seed_periods,
    ):
        """Reject entry on a transaction that is a transfer shadow.

        **The row under test is an AD-HOC envelope row carrying a
        ``transfer_id``, and it used to be a TEMPLATE-linked row given one.**
        That earlier shape set ``template_id`` and ``transfer_id`` on one row,
        which ``ck_transactions_one_pricing_link`` forbids as of plan step
        X-au-c1 -- the balance README documented that exclusivity as a convention
        and the amount model makes it structural (0 of 997 production rows held
        two links).  The assertion is unchanged; only the shape reaching it is,
        and it is now a shape the schema admits.

        **What this test can and cannot claim, stated because the guard's
        reachability is narrower than it looks.**  ``create_entry`` asks
        ``tracks_purchases`` BEFORE it asks about ``transfer_id``, and a real
        transfer shadow carries no template and its own ``is_envelope`` default
        of False -- so a genuine shadow is refused by the ENVELOPE guard and
        never reaches the transfer one.  This row is envelope-flagged so the
        transfer guard is the one that fires, which is what the test is for; that
        the guard is otherwise unreachable is reported rather than papered over.
        """
        with app.app_context():
            from app.models.transfer import Transfer
            from app.models.ref import AccountType

            user_id = seed_user["user"].id
            account_id = seed_user["account"].id
            scenario_id = seed_user["scenario"].id
            period_id = seed_periods[0].id

            # Create a second account for the transfer (different accounts required).
            checking_type = (
                db.session.query(AccountType).filter_by(name="Checking").one()
            )
            second_account = account_service.create_account(
                account_service.AccountSpec(
                    user_id=user_id,
                    account_type_id=checking_type.id,
                    name="Savings",
                    anchor_balance=Decimal("500.00"),
                ),
            )
            db.session.add(second_account)
            db.session.flush()

            projected = (
                db.session.query(Status).filter_by(name="Projected").one()
            )
            expense_type = (
                db.session.query(TransactionType).filter_by(name="Expense").one()
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

            # Ad-hoc (no template), so ``tracks_purchases`` reads the row's own
            # ``is_envelope`` and the transfer guard is what refuses it.
            txn = Transaction(
                pay_period_id=period_id,
                scenario_id=scenario_id,
                account_id=account_id,
                status_id=projected.id,
                name="Shadow with tracking on",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("100.00"),
                is_envelope=True,
                transfer_id=transfer.id,
            )
            db.session.add(txn)
            db.session.flush()

            with pytest.raises(ValidationError, match="transfer"):
                entry_service.create_entry(
                    transaction_id=txn.id,
                    user_id=user_id,
                    details=entry_service.EntryDetails(
                        amount=Decimal("50.00"),
                        description="Test",
                        purchased_on=date(2026, 1, 5),
                    ),
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
                    details=entry_service.EntryDetails(
                        amount=Decimal("50.00"),
                        description="Test",
                        purchased_on=date(2026, 1, 5),
                    ),
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
                    details=entry_service.EntryDetails(
                        amount=Decimal("50.00"),
                        description="Test",
                        purchased_on=date(2026, 1, 5),
                    ),
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
                    details=entry_service.EntryDetails(
                        amount=Decimal("50.00"),
                        description="Test",
                        purchased_on=date(2026, 1, 5),
                    ),
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
                    details=entry_service.EntryDetails(
                        amount=Decimal("50.00"),
                        description="Test",
                        purchased_on=date(2026, 1, 5),
                    ),
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
                    details=entry_service.EntryDetails(
                        amount=Decimal("50.00"),
                        description="Test",
                        purchased_on=date(2026, 1, 5),
                    ),
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
            # Through the seam, which writes the settle day in the same call --
            # a bare status assign leaves the row settled-but-undated, which
            # every reader now refuses (plan step X-f1).
            status_seam.apply_status_change(txn, done.id)
            db.session.flush()

            entry = entry_service.create_entry(
                transaction_id=txn_id,
                user_id=user_id,
                details=entry_service.EntryDetails(
                    amount=Decimal("42.50"),
                    description="Late posting purchase",
                    purchased_on=date(2026, 1, 10),
                ),
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
                details=entry_service.EntryDetails(
                    amount=Decimal("0.01"),
                    description="Penny item",
                    purchased_on=date(2026, 1, 5),
                ),
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
                details=entry_service.EntryDetails(
                    amount=Decimal("9999999999.99"),
                    description="Expensive item",
                    purchased_on=date(2026, 1, 5),
                ),
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
                details=entry_service.EntryDetails(
                    amount=Decimal("10.00"),
                    description=desc,
                    purchased_on=date(2026, 1, 5),
                ),
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
                details=entry_service.EntryDetails(
                    amount=Decimal("35.00"),
                    description="Companion purchase",
                    purchased_on=date(2026, 1, 5),
                ),
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

            periods = pay_period_write.record_paydays(
                user_id=seed_second_user["user"].id,
                first_payday=date(2026, 1, 2),
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
                is_envelope=True,
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
                    details=entry_service.EntryDetails(
                        amount=Decimal("50.00"),
                        description="Unauthorized",
                        purchased_on=date(2026, 1, 5),
                    ),
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
        """Update purchased_on field."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"])

            new_date = date(2026, 1, 10)
            updated = entry_service.update_entry(
                entry.id, seed_user["user"].id, purchased_on=new_date,
            )

            assert updated.purchased_on == new_date

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


# ── Future-date refusal (plan step X-c0, ruling R-M) ──────────────


class TestAFutureEntryDateIsRefused:
    """Both write doors refuse an entry dated after the user's today.

    Plan step **X-c0** (``docs/audits/balance_architecture/README.md``), ruling
    R-M.  An entry records a purchase that HAPPENED; a purchase not yet made is
    what the row's remaining budget already models.  The guard is what lets the
    reservation's ``as_of`` window be DELETED at plan step X-c2 instead of
    ruled, so these tests are the control that window's deletion rests on: if
    the refusal stops firing, an entry can be dated past a reader's now again
    and the two shipping surfaces (the calendar windows, the grid does not)
    have something to disagree about once more.

    The boundary is :func:`~app.utils.dates.display_today` -- the user's civil
    date -- so these tests derive their dates from it rather than from
    ``date.today()``, which is the server's UTC day and differs from it for
    part of every evening (CI runs UTC, the dev host runs Eastern).
    """

    def test_the_create_door_refuses_tomorrow(
        self, app, db, seed_user, seed_entry_template,
    ):
        """create_entry raises on a date one day past the user's today."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            tomorrow = display_today() + timedelta(days=1)

            with pytest.raises(ValidationError) as exc_info:
                entry_service.create_entry(
                    transaction_id=txn.id,
                    user_id=seed_user["user"].id,
                    details=entry_service.EntryDetails(
                        amount=Decimal("150.00"),
                        description="Costco run I have not made",
                        purchased_on=tomorrow,
                    ),
                )

            # The message names BOTH dates: what was rejected and what the
            # boundary was, so the surface can show the user why.
            message = str(exc_info.value)
            assert tomorrow.isoformat() in message
            assert display_today().isoformat() in message

            # Nothing was written -- the refusal precedes the INSERT.
            assert db.session.query(TransactionEntry).filter_by(
                transaction_id=txn.id,
            ).count() == 0

    def test_the_create_door_accepts_today(
        self, app, db, seed_user, seed_entry_template,
    ):
        """The boundary is exclusive: the user's own today is allowed.

        The add form posts exactly this value, so a refusal here would make the
        app reject its own form.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            today = display_today()

            entry = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                details=entry_service.EntryDetails(
                    amount=Decimal("42.87"),
                    description="Walmart",
                    purchased_on=today,
                ),
            )

            assert entry.purchased_on == today

    def test_the_create_door_accepts_a_backdated_purchase(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Backdating stays allowed -- the real data uses it.

        A purchase logged days after it happened, or dated into the previous
        pay period, is ordinary; only the FUTURE is refused.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            long_ago = display_today() - timedelta(days=45)

            entry = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                details=entry_service.EntryDetails(
                    amount=Decimal("101.06"),
                    description="Walmart, logged late",
                    purchased_on=long_ago,
                ),
            )

            assert entry.purchased_on == long_ago

    def test_the_update_door_refuses_moving_a_date_forward(
        self, app, db, seed_user, seed_entry_template,
    ):
        """update_entry raises when the new date is past the user's today.

        This is the door that could actually produce one: the add form posts a
        hidden date fixed to today, while the edit form offers a free picker.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(
                txn, seed_user["user"],
                purchased_on=display_today() - timedelta(days=2),
            )
            original = entry.purchased_on
            tomorrow = display_today() + timedelta(days=1)

            with pytest.raises(ValidationError):
                entry_service.update_entry(
                    entry.id, seed_user["user"].id, purchased_on=tomorrow,
                )

            # The refusal precedes the setattr loop, so the in-session object
            # is untouched -- asserted unconditionally rather than behind a
            # reload that could vacuously skip.
            assert entry.purchased_on == original

    def test_the_update_door_accepts_a_backdated_move(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Correcting a date backwards -- the intended use of the picker."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"])
            corrected = display_today() - timedelta(days=3)

            updated = entry_service.update_entry(
                entry.id, seed_user["user"].id, purchased_on=corrected,
            )

            assert updated.purchased_on == corrected

    def test_a_partial_update_that_omits_the_date_is_unaffected(
        self, app, db, seed_user, seed_entry_template,
    ):
        """The guard fires on the value being SET, never on the stored one.

        An amount-only edit must not be refused for a date it is not touching.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(
                txn, seed_user["user"], amount="50.00",
                purchased_on=display_today(),
            )

            updated = entry_service.update_entry(
                entry.id, seed_user["user"].id, amount=Decimal("75.00"),
            )

            assert updated.amount == Decimal("75.00")
            assert updated.purchased_on == display_today()


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
        """Entries returned in purchased_on ASC order."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            # Create entries out of chronological order.
            _make_entry(txn, user, description="Third",
                        purchased_on=date(2026, 1, 10))
            _make_entry(txn, user, description="First",
                        purchased_on=date(2026, 1, 3))
            _make_entry(txn, user, description="Second",
                        purchased_on=date(2026, 1, 7))

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

    def test_compute_remaining_anchors_on_estimated_only(
        self, app, db, seed_user, seed_entry_template,
    ):
        """C30-3 (E-21 / MED-03 / F-028 / F-056): compute_remaining
        takes only ``estimated_amount`` and therefore cannot anchor on
        ``actual_amount`` or switch on status -- the structural
        guarantee that the entry-tracked bill row's remaining always
        shares the declared E-21 budget base with the row's amount
        cell.

        Worked example: estimated=$120 (the E-21 base), entries
        summing $80; ``actual_amount`` could be anything (e.g. $100)
        and the result MUST stay anchored on $120.
            remaining = 120.00 - 80.00 = 40.00
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]
            entries = [
                _make_entry(txn, user, amount="50.00"),
                _make_entry(txn, user, amount="30.00"),
            ]

            # Even if a hypothetical caller passed actual_amount, the
            # function would compute against that value -- the
            # signature accepts no actual or status, so anchoring on
            # estimated is structural.  Pass estimated and assert the
            # E-21 result.
            remaining = entry_service.compute_remaining(
                Decimal("120.00"), entries,
            )

            assert remaining == Decimal("40.00")

    def test_compute_remaining_signature_excludes_actual_and_status(self):
        """C30-3 partner: the function signature cannot consult
        ``actual_amount`` or ``status`` -- it accepts only the declared
        ``budget`` and ``entries`` -- so a future change
        cannot silently shift the entry-tracked row's base away from
        the E-21 / MED-03 base without an explicit signature
        change (which would surface in review).

        **The first parameter is named ``budget`` since plan step X-au-c2b**,
        where it was ``estimated_amount``.  That rename is the assertion's
        subject rather than incidental: a parameter named after a COLUMN
        invites the next caller to pass that column, and under the amount model
        a derived row's is ``NULL``.  The base is what the row's amount
        RESOLVES to, and the name says so.
        """
        # pylint: disable=import-outside-toplevel
        import inspect
        sig = inspect.signature(entry_service.compute_remaining)
        # E-21 base contract: the only inputs are the declared base and the
        # entries to subtract.  No txn, no status, no actual.
        assert list(sig.parameters) == ["budget", "entries"]


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


class TestCheckPurchaseDateInPeriod:
    """Tests for entry_service.check_purchase_date_in_period().

    It reads ``purchased_on`` and not ``settled_on``, and that distinction is
    what the plan step S1-c column split is for: this warning asks "is this
    purchase budgeted to the right pay period", which is a BUDGET-clock
    question.  When the money reached the bank is a cash-clock fact and
    belongs to the balance fold, not to a budgeting warning.
    """

    def test_date_within_period(
        self, app, db, seed_user, seed_entry_template, seed_periods,
    ):
        """Date inside the pay period returns True.

        Period 0: 2026-01-02 to 2026-01-15.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            result = entry_service.check_purchase_date_in_period(
                date(2026, 1, 5), txn,
            )
            assert result is True

    def test_date_before_period(
        self, app, db, seed_user, seed_entry_template, seed_periods,
    ):
        """Date before the pay period start returns False."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            result = entry_service.check_purchase_date_in_period(
                date(2025, 12, 31), txn,
            )
            assert result is False

    def test_date_after_period(
        self, app, db, seed_user, seed_entry_template, seed_periods,
    ):
        """Date after the pay period end returns False."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            result = entry_service.check_purchase_date_in_period(
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
            result = entry_service.check_purchase_date_in_period(
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
            result = entry_service.check_purchase_date_in_period(
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
                "purchased_on": "2026-01-05",
            })
            assert data["amount"] == Decimal("50.00")
            assert data["description"] == "Kroger"
            assert data["purchased_on"] == date(2026, 1, 5)
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
                    "purchased_on": "2026-01-05",
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
                    "purchased_on": "2026-01-05",
                })
            assert "amount" in exc_info.value.messages

    def test_schema_accepts_minimum_amount(self, app):
        """Amount of exactly 0.01 is accepted."""
        with app.app_context():
            schema = EntryCreateSchema()
            data = schema.load({
                "amount": "0.01",
                "description": "Penny",
                "purchased_on": "2026-01-05",
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
                    "purchased_on": "2026-01-05",
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
                    "purchased_on": "2026-01-05",
                })
            assert "description" in exc_info.value.messages

    def test_schema_accepts_single_char_description(self, app):
        """Description of exactly 1 character is valid."""
        with app.app_context():
            schema = EntryCreateSchema()
            data = schema.load({
                "amount": "10.00",
                "description": "X",
                "purchased_on": "2026-01-05",
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
                "purchased_on": "2026-01-05",
            })
            assert data["description"] == desc

    def test_schema_is_credit_default_false(self, app):
        """is_credit defaults to False when omitted."""
        with app.app_context():
            schema = EntryCreateSchema()
            data = schema.load({
                "amount": "50.00",
                "description": "Test",
                "purchased_on": "2026-01-05",
            })
            assert data["is_credit"] is False

    def test_schema_is_credit_true(self, app):
        """is_credit can be set to True."""
        with app.app_context():
            schema = EntryCreateSchema()
            data = schema.load({
                "amount": "50.00",
                "description": "Test",
                "purchased_on": "2026-01-05",
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
                "purchased_on": "2026-01-05",
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
                "purchased_on": "2026-01-05",
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


# ── pct_complete (Commit 9 / F-23) ────────────────────────────────


class TestPctComplete:
    """Tests for entry_service.pct_complete().

    Locks the MED-04 / E-16 standard: money math is service-layer
    Decimal, not route-layer float.  The companion route used to cast
    the entry-percentage to float before handing it to the template;
    the helper now keeps the result as a Decimal end-to-end, mirroring
    the shape of dashboard_service._safe_pct_complete.
    """

    def test_pct_complete_normal_case(self):
        """C9-1: total $50, target $100 -> Decimal("50.00").

        Hand arithmetic: 50 / 100 * 100 = 50, quantised to 2dp = 50.00.
        """
        assert entry_service.pct_complete(
            Decimal("50"), Decimal("100"),
        ) == Decimal("50.00")

    def test_pct_complete_clamps_at_100(self):
        """C9-2: over-budget total $150 against target $100 -> Decimal("100.00").

        Hand arithmetic: 150 / 100 * 100 = 150, clamped at 100 because
        the progress-bar width has no defined meaning past 100%.
        """
        assert entry_service.pct_complete(
            Decimal("150"), Decimal("100"),
        ) == Decimal("100.00")

    def test_pct_complete_target_zero_guard(self):
        """C9-3: target $0 -> Decimal("0") (no divide-by-zero).

        A zero target is a degenerate budget line; returning 0 (rather
        than raising) lets the companion view render the row without
        special-casing in the template.
        """
        assert entry_service.pct_complete(
            Decimal("50"), Decimal("0"),
        ) == Decimal("0")

    def test_pct_complete_target_negative_guard(self):
        """Negative target -> Decimal("0") (same guard path as zero).

        Defensive: ``estimated_amount`` is CHECK-constrained to >= 0
        at the storage tier, but the helper should not produce a
        misleading negative percentage if a future caller violates
        that invariant in an in-memory object.
        """
        assert entry_service.pct_complete(
            Decimal("50"), Decimal("-10"),
        ) == Decimal("0")

    def test_pct_complete_zero_total(self):
        """Zero spending against a positive target -> Decimal("0.00").

        Hand arithmetic: 0 / 100 * 100 = 0.00.
        """
        assert entry_service.pct_complete(
            Decimal("0"), Decimal("100"),
        ) == Decimal("0.00")

    def test_pct_complete_returns_decimal_not_float(self):
        """MED-04 / E-16: the return type is Decimal, not float.

        Locks the post-F-23 contract that no caller has to do a
        ``float(Decimal_expression)`` cast at the route layer.
        """
        result = entry_service.pct_complete(
            Decimal("55.50"), Decimal("100.00"),
        )
        assert isinstance(result, Decimal)
        # Hand arithmetic: 55.50 / 100 * 100 = 55.50.
        assert result == Decimal("55.50")


def _outstanding_debit(txn, seed_user, amount="50.00",
                       purchased_on=date(2026, 1, 5)):
    """Attach one debit purchase with NO recorded posting day.

    Kept here for the helper-guard class below when the outstanding-set tests
    moved to ``test_reconcile_service`` at plan step X-f2-c1: that class grades
    ``tests._test_helpers.mark_purchase_settled``, which is about a purchase
    with no settle day, and building one is all it needs from the reconcile
    subject.
    """
    return _make_entry(
        txn, seed_user["user"], amount=amount,
        description="Kroger", purchased_on=purchased_on,
    )


class TestTheMarkPurchaseSettledHelperGuardsItsPrecondition:
    """``tests._test_helpers.mark_purchase_settled`` -- the helper's own guard.

    A TEST helper, graded here because its failure mode is what finding
    N-132 / R8 is about and an untested failure mode is one a future edit
    weakens for free.  It is the successor to ``add_entry(..., is_cleared=True)``,
    and the flag it replaced was an UNCONDITIONAL claim that a purchase was
    inside the anchor -- so fixtures set it on accounts whose only assertion
    PREDATED the purchase by months, a state production cannot reach, and those
    fixtures passed for years while silently no longer discriminating the case
    they named.

    The helper does what the flag did AND asserts the precondition the flag let
    fixtures skip.  These are its two refusals, each with the failure message
    checked, because a helper that failed with a bare ``AssertionError`` would
    send the next reader hunting through the balance fold instead of at the
    fixture.
    """

    def test_it_refuses_when_the_assertion_predates_the_purchase(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The N-132 shape itself: settled AFTER the account's latest assertion.

        The seeded account asserts its opening balance for the first period's
        start day; a purchase settled a week later is not inside it, however
        the fixture is spelled, so a suite expecting the settled bucket is
        asking for a state its own account cannot be in.  The message names
        BOTH days so the fix -- move the assertion, or accept the outstanding
        bucket -- is visible without reading the helper.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _outstanding_debit(txn, seed_user)
            db.session.commit()
            too_late = seed_periods[0].start_date + timedelta(days=7)

            with pytest.raises(AssertionError) as excinfo:
                mark_purchase_settled(
                    db.session, seed_user["account"], entry,
                    settled_on=too_late,
                )

            message = str(excinfo.value)
            assert too_late.isoformat() in message
            assert seed_periods[0].start_date.isoformat() in message
            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry.id,
            ).settled_on is None

    def test_it_refuses_when_the_account_has_asserted_nothing(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """No assertion at all means there is nothing to be inside of.

        ``ReconciledThrough.covers`` is total in both the argument and the
        boundary and answers False for a missing assertion, so a fixture that skipped this check would
        build a purchase the projection reads as outstanding while the test
        asserted the settled figure -- failing later, in the balance, with no
        indication that the ACCOUNT was the problem.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _outstanding_debit(txn, seed_user)
            account = seed_user["account"]
            db.session.query(AccountAnchorHistory).filter_by(
                account_id=account.id,
            ).delete()
            db.session.commit()

            with pytest.raises(AssertionError) as excinfo:
                mark_purchase_settled(db.session, account, entry)

            assert "has asserted no balance" in str(excinfo.value)

    def test_it_records_the_day_when_the_assertion_covers_it(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The positive control: it still does what the flag did.

        Without this the two refusals above would be satisfied by a helper that
        refused everything.  A purchase settled on the account's own asserted
        day passes, and ``settled_on`` defaults to the purchase's own
        ``purchased_on`` -- "it posted the day I bought it", the shape a
        same-day debit has.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _outstanding_debit(
                txn, seed_user, purchased_on=seed_periods[0].start_date,
            )
            db.session.commit()

            mark_purchase_settled(db.session, seed_user["account"], entry)
            db.session.commit()

            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry.id,
            ).settled_on == seed_periods[0].start_date


class TestAnArchivedRowsPurchasesAreHistory:
    """Finding **N-229**: the terminal ``Settled`` status refuses entry edits.

    An archived row's cost is already in the books and the state machine gives
    ``Settled`` no outgoing edge but identity, so it can never be reopened and
    re-derived.  Before plan step X-ap a new purchase against one was ACCEPTED,
    persisted, and half-processed: ``actual_amount`` was not recomputed (that
    half graded ``is_done`` -- exactly Paid) while the postings WERE reconciled
    (that half graded the settled BAND), so the ledger moved and the figure it
    was derived from did not.

    Production carries zero rows in this status, which is why the ledger row
    values the defect at ``$0.00`` and why it had zero coverage --
    ``StatusEnum.SETTLED`` appeared in neither this module nor
    ``test_mark_paid_entries``.  It is REACHABLE: the full-edit Status dropdown
    offers Settled from Paid.
    """

    @staticmethod
    def _archive(txn):
        """Move *txn* Projected -> Paid -> Settled through the real seam."""
        status_seam.apply_status_change(
            txn, ref_cache.status_id(StatusEnum.DONE),
        )
        status_seam.apply_status_change(
            txn, ref_cache.status_id(StatusEnum.SETTLED),
        )
        db.session.flush()

    def test_create_is_refused(self, app, db, seed_user, seed_entry_template):
        """A purchase cannot be recorded against an archived row.

        Shown to FIRE: without the guard the entry is created and persists.
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            self._archive(txn)

            with pytest.raises(ValidationError, match="archived"):
                entry_service.create_entry(
                    transaction_id=txn.id,
                    user_id=seed_user["user"].id,
                    details=entry_service.EntryDetails(
                        amount=Decimal("50.00"),
                        description="Late purchase",
                        purchased_on=display_today(),
                    ),
                )

            assert db.session.query(TransactionEntry).filter_by(
                transaction_id=txn.id,
            ).count() == 0

    def test_update_is_refused(self, app, db, seed_user, seed_entry_template):
        """An existing purchase on an archived row cannot be re-priced.

        The row was archived carrying a $50.00 purchase; re-pricing it to
        $500.00 would rewrite what the books already say the row cost.
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            entry = _make_entry(txn, seed_user["user"], amount="50.00")
            self._archive(txn)

            with pytest.raises(ValidationError, match="archived"):
                entry_service.update_entry(
                    entry.id, seed_user["user"].id, amount=Decimal("500.00"),
                )

            # No rollback: the guard runs BEFORE the setattr loop, so a refused
            # call stages nothing -- which is the property being asserted.
            assert entry.amount == Decimal("50.00")

    def test_delete_is_refused(self, app, db, seed_user, seed_entry_template):
        """A purchase cannot be removed from an archived row."""
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            entry = _make_entry(txn, seed_user["user"], amount="50.00")
            self._archive(txn)
            entry_id = entry.id

            with pytest.raises(ValidationError, match="archived"):
                entry_service.delete_entry(entry_id, seed_user["user"].id)

            # No rollback: the guard runs BEFORE ``db.session.delete``, so a
            # refused call stages nothing.
            assert db.session.get(TransactionEntry, entry_id) is not None

    def test_a_PAID_row_still_takes_a_late_purchase(
        self, app, db, seed_user, seed_entry_template,
    ):
        """The refusal is the ARCHIVE status, never the settled band.

        A Paid envelope must keep accepting late-posting purchases and
        re-deriving its actual from them -- that is what the hook exists for,
        and narrowing the refusal to the whole settled band would break it.
        $50.00 recorded after the close settles the row at $50.00.
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.flush()

            entry_service.create_entry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                details=entry_service.EntryDetails(
                    amount=Decimal("50.00"),
                    description="Late purchase",
                    purchased_on=display_today(),
                ),
            )

            assert txn.actual_amount == Decimal("50.00")
