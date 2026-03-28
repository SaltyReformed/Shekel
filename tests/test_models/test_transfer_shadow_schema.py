"""
Shekel Budget App -- Transfer Shadow Transaction Schema Tests

Tests for the schema additions in Task 2 of the Transfer Architecture Rework:
  - Transaction.transfer_id (nullable FK to Transfer with ON DELETE CASCADE)
  - Transfer.category_id (nullable FK to Category)
  - TransferTemplate.category_id (nullable FK to Category)
  - Transfer.shadow_transactions backref
  - transfer_id exclusion from transaction Marshmallow schemas
"""

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.category import Category
from app.models.ref import Status, TransactionType
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.schemas.validation import (
    InlineTransactionCreateSchema,
    TransactionCreateSchema,
)


class TestTransactionTransferId:
    """Tests for the transfer_id column on Transaction."""

    def _make_transfer(self, seed_full_user_data):
        """Helper: create a Transfer linked to the seeded user's accounts."""
        projected = db.session.query(Status).filter_by(name="Projected").one()
        data = seed_full_user_data
        xfer = Transfer(
            user_id=data["user"].id,
            from_account_id=data["account"].id,
            to_account_id=data["savings_account"].id,
            pay_period_id=data["periods"][0].id,
            scenario_id=data["scenario"].id,
            status_id=projected.id,
            name="Test Transfer",
            amount=Decimal("100.00"),
        )
        db.session.add(xfer)
        db.session.flush()
        return xfer

    def _make_shadow(self, seed_full_user_data, transfer, txn_type_name):
        """Helper: create a shadow transaction linked to a transfer."""
        projected = db.session.query(Status).filter_by(name="Projected").one()
        txn_type = db.session.query(TransactionType).filter_by(name=txn_type_name).one()
        data = seed_full_user_data
        account_id = (
            data["account"].id if txn_type_name == "expense"
            else data["savings_account"].id
        )
        txn = Transaction(
            pay_period_id=data["periods"][0].id,
            scenario_id=data["scenario"].id,
            account_id=account_id,
            status_id=projected.id,
            name=f"Shadow: {transfer.name}",
            category_id=data["categories"]["Rent"].id,
            transaction_type_id=txn_type.id,
            estimated_amount=transfer.amount,
            transfer_id=transfer.id,
        )
        db.session.add(txn)
        db.session.flush()
        return txn

    def test_transaction_model_has_transfer_id(self, app, db, seed_full_user_data):
        """Transaction with transfer_id saves and resolves the relationship."""
        with app.app_context():
            xfer = self._make_transfer(seed_full_user_data)
            txn = self._make_shadow(seed_full_user_data, xfer, "expense")

            assert txn.transfer_id == xfer.id
            assert txn.transfer is not None
            assert txn.transfer.id == xfer.id

    def test_transaction_transfer_id_nullable(self, app, db, seed_full_user_data):
        """Regular transaction with transfer_id=None saves without error."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="expense").one()
            data = seed_full_user_data
            txn = Transaction(
                pay_period_id=data["periods"][0].id,
                scenario_id=data["scenario"].id,
                account_id=data["account"].id,
                status_id=projected.id,
                name="Regular Txn",
                category_id=data["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("50.00"),
                transfer_id=None,
            )
            db.session.add(txn)
            db.session.flush()

            assert txn.transfer_id is None
            assert txn.transfer is None

    def test_transfer_cascade_deletes_shadow_transactions(
        self, app, db, seed_full_user_data
    ):
        """ON DELETE CASCADE removes both shadow transactions when transfer is deleted."""
        with app.app_context():
            xfer = self._make_transfer(seed_full_user_data)
            shadow_expense = self._make_shadow(seed_full_user_data, xfer, "expense")
            shadow_income = self._make_shadow(seed_full_user_data, xfer, "income")
            expense_id = shadow_expense.id
            income_id = shadow_income.id

            # Delete the transfer and commit so the CASCADE executes.
            db.session.delete(xfer)
            db.session.commit()

            # Expire all cached objects so get() hits the database.
            db.session.expire_all()

            # Both shadow transactions should be gone.
            assert db.session.get(Transaction, expense_id) is None
            assert db.session.get(Transaction, income_id) is None

    def test_shadow_transactions_backref(self, app, db, seed_full_user_data):
        """Transfer.shadow_transactions backref returns both linked transactions."""
        with app.app_context():
            xfer = self._make_transfer(seed_full_user_data)
            shadow_expense = self._make_shadow(seed_full_user_data, xfer, "expense")
            shadow_income = self._make_shadow(seed_full_user_data, xfer, "income")

            shadows = xfer.shadow_transactions
            assert len(shadows) == 2
            shadow_ids = {s.id for s in shadows}
            assert shadow_expense.id in shadow_ids
            assert shadow_income.id in shadow_ids


class TestTransferCategoryId:
    """Tests for the category_id column on Transfer."""

    def test_transfer_model_has_category_id(self, app, db, seed_full_user_data):
        """Transfer with category_id saves and resolves the relationship."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            data = seed_full_user_data
            category = data["categories"]["Rent"]

            xfer = Transfer(
                user_id=data["user"].id,
                from_account_id=data["account"].id,
                to_account_id=data["savings_account"].id,
                pay_period_id=data["periods"][0].id,
                scenario_id=data["scenario"].id,
                status_id=projected.id,
                name="Categorized Transfer",
                amount=Decimal("500.00"),
                category_id=category.id,
            )
            db.session.add(xfer)
            db.session.flush()

            assert xfer.category_id == category.id
            assert xfer.category is not None
            assert xfer.category.item_name == "Rent"

    def test_transfer_category_id_nullable(self, app, db, seed_full_user_data):
        """Transfer with category_id=None saves without error."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            data = seed_full_user_data

            xfer = Transfer(
                user_id=data["user"].id,
                from_account_id=data["account"].id,
                to_account_id=data["savings_account"].id,
                pay_period_id=data["periods"][0].id,
                scenario_id=data["scenario"].id,
                status_id=projected.id,
                name="No Category Transfer",
                amount=Decimal("300.00"),
                category_id=None,
            )
            db.session.add(xfer)
            db.session.flush()

            assert xfer.category_id is None
            assert xfer.category is None


class TestTransferTemplateCategoryId:
    """Tests for the category_id column on TransferTemplate."""

    def test_transfer_template_model_has_category_id(
        self, app, db, seed_full_user_data
    ):
        """TransferTemplate with category_id saves and resolves the relationship."""
        with app.app_context():
            data = seed_full_user_data
            category = data["categories"]["Rent"]

            tpl = TransferTemplate(
                user_id=data["user"].id,
                from_account_id=data["account"].id,
                to_account_id=data["savings_account"].id,
                name="Categorized Template",
                default_amount=Decimal("200.00"),
                category_id=category.id,
            )
            db.session.add(tpl)
            db.session.flush()

            assert tpl.category_id == category.id
            assert tpl.category is not None
            assert tpl.category.item_name == "Rent"

    def test_transfer_template_category_id_nullable(
        self, app, db, seed_full_user_data
    ):
        """TransferTemplate with category_id=None saves without error."""
        with app.app_context():
            data = seed_full_user_data

            tpl = TransferTemplate(
                user_id=data["user"].id,
                from_account_id=data["account"].id,
                to_account_id=data["savings_account"].id,
                name="No Category Template",
                default_amount=Decimal("150.00"),
                category_id=None,
            )
            db.session.add(tpl)
            db.session.flush()

            assert tpl.category_id is None
            assert tpl.category is None


class TestTransferIdNotInTransactionSchemas:
    """Verify transfer_id cannot be set via transaction Marshmallow schemas.

    The transfer_id field is set exclusively by the transfer service.
    User input must not be able to forge a shadow transaction link.
    """

    def test_transfer_id_not_in_transaction_create_schema(self):
        """TransactionCreateSchema strips transfer_id (EXCLUDE mode)."""
        data = TransactionCreateSchema().load({
            "name": "Groceries",
            "estimated_amount": "85.50",
            "pay_period_id": "1",
            "scenario_id": "1",
            "category_id": "1",
            "transaction_type_id": "1",
            "account_id": "1",
            "transfer_id": "999",
        })
        assert "transfer_id" not in data

    def test_transfer_id_not_in_inline_create_schema(self):
        """InlineTransactionCreateSchema strips transfer_id (EXCLUDE mode)."""
        data = InlineTransactionCreateSchema().load({
            "estimated_amount": "50.00",
            "account_id": "1",
            "category_id": "1",
            "pay_period_id": "1",
            "transaction_type_id": "1",
            "scenario_id": "1",
            "transfer_id": "999",
        })
        assert "transfer_id" not in data
