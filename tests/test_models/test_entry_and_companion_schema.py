"""
Shekel Budget App -- Commit 1 Schema Tests

Tests for the data model additions in Section 9 Commit 1:
  - TransactionEntry model (table, constraints, relationships, cascades)
  - TransactionTemplate flag columns (track_individual_purchases, companion_visible)
  - User role columns (role_id, linked_owner_id)
  - UserRole ref table and ref_cache integration
"""

from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy.exc

from app.extensions import db
from app.models.ref import Status, TransactionType, UserRole
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.models.user import User, UserSettings
from app.services.auth_service import hash_password


# ── Helpers ────────────────────────────────────────────────────────────


def _make_entry(txn, user, amount, description, **kwargs):
    """Create and flush a TransactionEntry with the given fields."""
    entry = TransactionEntry(
        transaction_id=txn.id,
        user_id=user.id,
        amount=amount,
        description=description,
        **kwargs,
    )
    db.session.add(entry)
    db.session.flush()
    return entry


def _make_txn(seed_user, seed_periods, estimated_amount=Decimal("500.00")):
    """Create a projected expense transaction in the first period."""
    expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
    projected_status = db.session.query(Status).filter_by(name="Projected").one()
    txn = Transaction(
        pay_period_id=seed_periods[0].id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=projected_status.id,
        name="Test Expense",
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=expense_type.id,
        estimated_amount=estimated_amount,
    )
    db.session.add(txn)
    db.session.flush()
    return txn


# ── TransactionEntry Tests ─────────────────────────────────────────────


class TestTransactionEntryCascadeDelete:
    """Verify CASCADE behavior on the transaction_id FK."""

    def test_transaction_entry_cascade_delete(self, app, db, seed_user, seed_periods):
        """Deleting a transaction cascades to delete its entries.

        The transaction_entries.transaction_id FK is ON DELETE CASCADE, so
        removing the parent transaction must remove all child entries without
        raising an IntegrityError.
        """
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods)
            user = seed_user["user"]
            _make_entry(txn, user, Decimal("25.00"), "Store A")
            _make_entry(txn, user, Decimal("30.00"), "Store B")
            db.session.commit()

            txn_id = txn.id
            # Verify entries exist before delete.
            assert db.session.query(TransactionEntry).filter_by(
                transaction_id=txn_id
            ).count() == 2

            db.session.delete(txn)
            db.session.commit()

            # Entries must be gone after CASCADE delete.
            assert db.session.query(TransactionEntry).filter_by(
                transaction_id=txn_id
            ).count() == 0


class TestTransactionEntryAmountCheck:
    """Verify the CHECK(amount > 0) constraint on transaction_entries."""

    def test_transaction_entry_amount_zero_rejected(self, app, db, seed_user, seed_periods):
        """Creating an entry with amount=0 raises IntegrityError.

        The ck_transaction_entries_positive_amount constraint requires
        amount > 0, so zero is invalid.
        """
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods)
            entry = TransactionEntry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                amount=Decimal("0.00"),
                description="Zero amount",
            )
            db.session.add(entry)
            with pytest.raises(sqlalchemy.exc.IntegrityError):
                db.session.flush()
            db.session.rollback()

    def test_transaction_entry_amount_negative_rejected(self, app, db, seed_user, seed_periods):
        """Creating an entry with a negative amount raises IntegrityError.

        The ck_transaction_entries_positive_amount constraint requires
        amount > 0, so negative values are invalid.
        """
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods)
            entry = TransactionEntry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                amount=Decimal("-5.00"),
                description="Negative amount",
            )
            db.session.add(entry)
            with pytest.raises(sqlalchemy.exc.IntegrityError):
                db.session.flush()
            db.session.rollback()

    def test_transaction_entry_amount_positive_accepted(self, app, db, seed_user, seed_periods):
        """Creating an entry with a small positive amount succeeds.

        amount=0.01 is the smallest valid Numeric(12,2) value above zero.
        """
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods)
            entry = _make_entry(
                txn, seed_user["user"], Decimal("0.01"), "Penny purchase",
            )
            db.session.commit()
            assert entry.id is not None
            assert entry.amount == Decimal("0.01")


# ── Template Flag Tests ────────────────────────────────────────────────


class TestTemplateFlagsDefault:
    """Verify default values for the new template boolean columns."""

    def test_template_flags_default_false(self, app, db, seed_user):
        """New templates default to track_individual_purchases=False and companion_visible=False.

        Both columns have server_default='false' and Python default=False,
        so templates created without explicit values must have both False.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                name="Default Template",
                default_amount=Decimal("100.00"),
            )
            db.session.add(template)
            db.session.commit()

            # Re-read from database to verify server_default took effect.
            db.session.expire(template)
            assert template.track_individual_purchases is False
            assert template.companion_visible is False

    def test_template_flags_explicit_true(self, app, db, seed_user):
        """Templates can be created with tracking and visibility flags set to True.

        Verifies the columns accept True values and persist them correctly.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                name="Tracking Template",
                default_amount=Decimal("200.00"),
                track_individual_purchases=True,
                companion_visible=True,
            )
            db.session.add(template)
            db.session.commit()

            db.session.expire(template)
            assert template.track_individual_purchases is True
            assert template.companion_visible is True


# ── User Role Tests ────────────────────────────────────────────────────


class TestUserRoleDefault:
    """Verify user role_id defaults and linked_owner_id behavior."""

    def test_user_role_default_owner(self, app, db, seed_user):
        """Users created without explicit role_id default to the owner role.

        The server_default='1' maps to the 'owner' row in ref.user_roles.
        The seed_user fixture creates a User without specifying role_id.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import RoleEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            # Re-query from database to pick up server_default.
            user = db.session.get(User, seed_user["user"].id)
            assert user.role_id == ref_cache.role_id(RoleEnum.OWNER)

    def test_user_linked_owner_nullable(self, app, db, seed_user):
        """Owner users have linked_owner_id=None by default.

        The linked_owner_id column is nullable and has no default, so
        regular owner users should have None.
        """
        with app.app_context():
            # Re-query from database to verify persisted value.
            user = db.session.get(User, seed_user["user"].id)
            assert user.linked_owner_id is None

    def test_companion_linked_to_owner(self, app, db, seed_companion, seed_user):
        """Companion users have linked_owner_id pointing to the owner.

        The seed_companion fixture creates a companion with role_id=2
        and linked_owner_id=seed_user.id.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import RoleEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            companion = seed_companion["user"]
            assert companion.role_id == ref_cache.role_id(RoleEnum.COMPANION)
            assert companion.linked_owner_id == seed_user["user"].id


class TestUserRoleForeignKey:
    """Verify the role_id FK constraint enforcement."""

    def test_user_role_fk_constraint(self, app, db):
        """Setting role_id to a nonexistent value raises IntegrityError.

        The FK from auth.users.role_id to ref.user_roles.id must reject
        invalid references.
        """
        with app.app_context():
            user = User(
                email="badrole@shekel.local",
                password_hash=hash_password("testpass"),
                display_name="Bad Role User",
                role_id=999,
            )
            db.session.add(user)
            with pytest.raises(sqlalchemy.exc.IntegrityError):
                db.session.flush()
            db.session.rollback()


# ── Ref Table and Cache Tests ──────────────────────────────────────────


class TestUserRoleRefTable:
    """Verify the user_roles ref table is properly seeded."""

    def test_user_role_ref_table_seeded(self, app, db):
        """The ref.user_roles table contains exactly 2 rows: owner and companion.

        These are seeded at app startup and must be present for
        ref_cache.role_id() to function.
        """
        with app.app_context():
            roles = db.session.query(UserRole).order_by(UserRole.id).all()
            assert len(roles) == 2
            assert roles[0].name == "owner"
            assert roles[0].id == 1
            assert roles[1].name == "companion"
            assert roles[1].id == 2

    def test_role_enum_cache_loaded(self, app, db):
        """ref_cache.role_id() returns integer IDs for both RoleEnum members.

        The cache is initialized during app setup.  Both OWNER and
        COMPANION must resolve to valid integer PKs from ref.user_roles.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import RoleEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            owner_id = ref_cache.role_id(RoleEnum.OWNER)
            companion_id = ref_cache.role_id(RoleEnum.COMPANION)
            assert isinstance(owner_id, int)
            assert isinstance(companion_id, int)
            assert owner_id != companion_id


# ── Entry-User Cascade Tests ──────────────────────────────────────────


class TestEntryUserCascadeDelete:
    """Verify CASCADE behavior on the user_id FK."""

    def test_entry_user_cascade_delete(self, app, db, seed_user, seed_periods):
        """Deleting the user who created an entry cascades to delete the entry.

        The transaction_entries.user_id FK is ON DELETE CASCADE.  When
        a user is deleted, all their entries must be removed.

        We create a second user to own the entry so that deleting them
        does not also cascade-delete the parent transaction (which belongs
        to seed_user).
        """
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods)

            # Create a separate user who will own the entry.
            entry_user = User(
                email="entryuser@shekel.local",
                password_hash=hash_password("entrypass"),
                display_name="Entry User",
            )
            db.session.add(entry_user)
            db.session.flush()

            entry = _make_entry(
                txn, entry_user, Decimal("15.00"), "Entry by doomed user",
            )
            db.session.commit()

            entry_id = entry.id
            assert db.session.get(TransactionEntry, entry_id) is not None

            db.session.delete(entry_user)
            db.session.commit()

            # Entry must be gone after user deletion.
            assert db.session.get(TransactionEntry, entry_id) is None


# ── Transaction-Entries Relationship Tests ─────────────────────────────


class TestTransactionEntriesRelationship:
    """Verify the Transaction.entries relationship behavior."""

    def test_transaction_entries_relationship(self, app, db, seed_user, seed_periods):
        """Accessing txn.entries returns entries ordered by entry_date.

        Creates 3 entries with different dates on the same transaction.
        The relationship's order_by=TransactionEntry.entry_date must
        return them in chronological order.
        """
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods)
            user = seed_user["user"]

            # Create entries out of chronological order.
            _make_entry(
                txn, user, Decimal("30.00"), "Middle",
                entry_date=date(2026, 1, 5),
            )
            _make_entry(
                txn, user, Decimal("10.00"), "First",
                entry_date=date(2026, 1, 3),
            )
            _make_entry(
                txn, user, Decimal("20.00"), "Last",
                entry_date=date(2026, 1, 7),
            )
            db.session.commit()

            # Expire to force reload from database.
            db.session.expire(txn)
            entries = txn.entries

            assert len(entries) == 3
            assert entries[0].description == "First"
            assert entries[1].description == "Middle"
            assert entries[2].description == "Last"
            assert entries[0].entry_date < entries[1].entry_date < entries[2].entry_date

    def test_transaction_entries_empty_by_default(self, app, db, seed_user, seed_periods):
        """A transaction with no entries returns an empty list via the relationship.

        Verifies that accessing txn.entries does not error when no
        child entries exist.
        """
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods)
            db.session.commit()
            db.session.expire(txn)
            assert txn.entries == []


# ── Edge Case Tests ────────────────────────────────────────────────────


class TestTransactionEntryEdgeCases:
    """Edge case tests beyond the plan baseline."""

    def test_entry_large_amount_accepted(self, app, db, seed_user, seed_periods):
        """Numeric(12,2) supports amounts up to 9999999999.99.

        The maximum value for Numeric(12,2) is 10 digits before the
        decimal + 2 after.  Verify the upper bound is accepted.
        """
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods)
            entry = _make_entry(
                txn, seed_user["user"],
                Decimal("9999999999.99"), "Big purchase",
            )
            db.session.commit()
            db.session.expire(entry)
            assert entry.amount == Decimal("9999999999.99")

    def test_entry_description_max_length(self, app, db, seed_user, seed_periods):
        """A 200-character description is accepted (VARCHAR(200) limit).

        Verifies the column stores the full 200-character string.
        """
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods)
            desc = "A" * 200
            entry = _make_entry(
                txn, seed_user["user"], Decimal("5.00"), desc,
            )
            db.session.commit()
            db.session.expire(entry)
            assert len(entry.description) == 200

    def test_entry_description_over_max_rejected(self, app, db, seed_user, seed_periods):
        """A 201-character description is rejected by the database.

        VARCHAR(200) enforces a maximum length at the database level.
        """
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods)
            entry = TransactionEntry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                amount=Decimal("5.00"),
                description="A" * 201,
            )
            db.session.add(entry)
            with pytest.raises(sqlalchemy.exc.DataError):
                db.session.flush()
            db.session.rollback()

    def test_entry_is_credit_default_false(self, app, db, seed_user, seed_periods):
        """Entries default to is_credit=False when not specified.

        Both the Python default and server_default are False.
        """
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods)
            entry = _make_entry(
                txn, seed_user["user"], Decimal("10.00"), "Cash purchase",
            )
            db.session.commit()
            db.session.expire(entry)
            assert entry.is_credit is False

    def test_entry_credit_payback_set_null_on_delete(self, app, db, seed_user, seed_periods):
        """Deleting the payback transaction sets credit_payback_id to NULL.

        The FK on credit_payback_id is ON DELETE SET NULL.  When the
        referenced payback transaction is deleted, the entry's
        credit_payback_id must become NULL rather than cascading.
        """
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods)
            payback_txn = _make_txn(seed_user, seed_periods, Decimal("10.00"))

            entry = _make_entry(
                txn, seed_user["user"], Decimal("10.00"), "CC purchase",
                is_credit=True, credit_payback_id=payback_txn.id,
            )
            db.session.commit()

            entry_id = entry.id
            assert entry.credit_payback_id == payback_txn.id

            db.session.delete(payback_txn)
            db.session.commit()

            refreshed = db.session.get(TransactionEntry, entry_id)
            assert refreshed is not None
            assert refreshed.credit_payback_id is None

    def test_entry_date_server_default(self, app, db, seed_user, seed_periods):
        """Entry date defaults to CURRENT_DATE when not explicitly set.

        The server_default=CURRENT_DATE uses the database server's
        timezone, so we verify the date is within 1 day of Python's
        date.today() to account for UTC/local timezone differences.
        """
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods)
            entry = TransactionEntry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                amount=Decimal("12.50"),
                description="No date specified",
            )
            db.session.add(entry)
            db.session.commit()

            # Re-query to pick up server_default.
            refreshed = db.session.get(TransactionEntry, entry.id)
            assert isinstance(refreshed.entry_date, date)
            delta = abs((refreshed.entry_date - date.today()).days)
            assert delta <= 1, (
                f"entry_date {refreshed.entry_date} is more than 1 day "
                f"from today {date.today()}"
            )

    def test_multiple_entries_on_same_transaction(self, app, db, seed_user, seed_periods):
        """Multiple entries can be created on the same transaction.

        Verifies no unique constraint prevents multiple entries per
        transaction, and the relationship correctly returns all of them.
        """
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods)
            user = seed_user["user"]

            for i in range(5):
                _make_entry(
                    txn, user, Decimal("10.00"), f"Entry {i}",
                    entry_date=date(2026, 1, 3 + i),
                )
            db.session.commit()

            db.session.expire(txn)
            assert len(txn.entries) == 5

    def test_user_role_relationship_joined(self, app, db, seed_user):
        """User.role relationship loads the UserRole via lazy='joined'.

        Verifies that user.role returns the correct UserRole object
        with the expected name attribute.
        """
        with app.app_context():
            # Re-query to get a fresh instance attached to the session.
            user = db.session.get(User, seed_user["user"].id)
            assert user.role is not None
            assert user.role.name == "owner"

    def test_entry_repr(self, app, db, seed_user, seed_periods):
        """TransactionEntry.__repr__ includes description, amount, and id."""
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods)
            entry = _make_entry(
                txn, seed_user["user"], Decimal("42.50"), "Kroger",
            )
            db.session.commit()
            result = repr(entry)
            assert "Kroger" in result
            assert "42.50" in result
