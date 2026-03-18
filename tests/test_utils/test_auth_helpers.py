"""
Shekel Budget App - Auth Helpers Tests

Tests for the reusable ownership verification helpers in
app/utils/auth_helpers.py.  Uses test_request_context + login_user
to set up a real Flask-Login request context for each test.
"""

from datetime import date
from decimal import Decimal

import pytest
from flask_login import login_user

from app.extensions import db
from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.models.ref import AccountType, Status, TransactionType
from app.models.transaction import Transaction
from app.utils.auth_helpers import get_or_404, get_owned_via_parent


class TestGetOr404:
    """Tests for the get_or_404 ownership helper (Pattern A)."""

    def test_returns_owned_record(self, app, db, seed_user):
        """Happy path: returns the record when it belongs to the current user."""
        with app.test_request_context():
            login_user(seed_user["user"])
            result = get_or_404(Account, seed_user["account"].id)
            assert result is not None
            assert result.id == seed_user["account"].id

    def test_returns_none_for_nonexistent_pk(self, app, db, seed_user):
        """Returns None when no record exists at the given PK."""
        with app.test_request_context():
            login_user(seed_user["user"])
            result = get_or_404(Account, 999999)
            assert result is None

    def test_returns_none_for_other_users_record(self, app, db, seed_user, second_user):
        """Core security test: user A cannot load user B's record."""
        with app.test_request_context():
            login_user(seed_user["user"])
            # second_user's account belongs to a different user.
            result = get_or_404(Account, second_user["account"].id)
            assert result is None

    def test_returns_none_for_pk_zero(self, app, db, seed_user):
        """PK=0 does not exist in PostgreSQL autoincrement; must not crash."""
        with app.test_request_context():
            login_user(seed_user["user"])
            result = get_or_404(Account, 0)
            assert result is None

    def test_custom_user_id_field_nonexistent(self, app, db, seed_user):
        """Passing a nonexistent field name returns None (safe fallback)."""
        with app.test_request_context():
            login_user(seed_user["user"])
            # Account has user_id, but "nonexistent" does not exist --
            # getattr returns None which != current_user.id.
            result = get_or_404(Account, seed_user["account"].id,
                                user_id_field="nonexistent")
            assert result is None


class TestGetOwnedViaParent:
    """Tests for the get_owned_via_parent ownership helper (Pattern B)."""

    def _create_transaction(self, seed_user, period):
        """Helper: create a projected expense in the given period."""
        projected = db.session.query(Status).filter_by(name="projected").one()
        expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

        txn = Transaction(
            pay_period_id=period.id,
            scenario_id=seed_user["scenario"].id,
            status_id=projected.id,
            name="Test Expense",
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=expense_type.id,
            estimated_amount=Decimal("50.00"),
        )
        db.session.add(txn)
        db.session.flush()
        return txn

    def test_returns_owned_child_record(self, app, db, seed_user, seed_periods):
        """Happy path: returns the child when its parent belongs to the current user."""
        with app.test_request_context():
            login_user(seed_user["user"])
            txn = self._create_transaction(seed_user, seed_periods[0])
            result = get_owned_via_parent(Transaction, txn.id, "pay_period")
            assert result is not None
            assert result.id == txn.id

    def test_returns_none_for_nonexistent_pk(self, app, db, seed_user, seed_periods):
        """Returns None when no child record exists at the given PK."""
        with app.test_request_context():
            login_user(seed_user["user"])
            result = get_owned_via_parent(Transaction, 999999, "pay_period")
            assert result is None

    def test_returns_none_for_other_users_child(self, app, db, seed_user, second_user, seed_periods):
        """Core security test: user A cannot load user B's child record."""
        with app.test_request_context():
            login_user(seed_user["user"])

            # Create a pay period for the second user.
            from app.services import pay_period_service
            periods2 = pay_period_service.generate_pay_periods(
                user_id=second_user["user"].id,
                start_date=date(2026, 3, 1),
                num_periods=2,
                cadence_days=14,
            )
            db.session.flush()

            # Create a transaction owned by the second user.
            projected = db.session.query(Status).filter_by(name="projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()
            txn2 = Transaction(
                pay_period_id=periods2[0].id,
                scenario_id=second_user["scenario"].id,
                status_id=projected.id,
                name="Other User Expense",
                category_id=second_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("99.00"),
            )
            db.session.add(txn2)
            db.session.flush()

            result = get_owned_via_parent(Transaction, txn2.id, "pay_period")
            assert result is None

    def test_returns_none_when_parent_attr_missing(self, app, db, seed_user, seed_periods):
        """Bad parent_attr name returns None instead of crashing."""
        with app.test_request_context():
            login_user(seed_user["user"])
            txn = self._create_transaction(seed_user, seed_periods[0])
            result = get_owned_via_parent(
                Transaction, txn.id, "nonexistent_relationship",
            )
            assert result is None

    def test_returns_none_when_parent_user_id_attr_missing(self, app, db, seed_user, seed_periods):
        """Bad parent_user_id_attr name returns None instead of crashing."""
        with app.test_request_context():
            login_user(seed_user["user"])
            txn = self._create_transaction(seed_user, seed_periods[0])
            result = get_owned_via_parent(
                Transaction, txn.id, "pay_period",
                parent_user_id_attr="nonexistent",
            )
            assert result is None
