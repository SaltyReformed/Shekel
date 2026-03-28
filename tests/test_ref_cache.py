"""
Shekel Budget App -- Reference Cache and Status Boolean Column Tests

Tests for the ref_cache module (Commit #1) and the boolean columns added
to the Status model.  Verifies that:

  - The cache loads all StatusEnum and TxnTypeEnum members at startup.
  - The cache raises RuntimeError when a database row is missing.
  - The Status boolean columns (is_settled, is_immutable, excludes_from_balance)
    are correct for every status.
  - Transaction.effective_amount respects the boolean columns.
  - The grid shows "Paid" instead of "Done" for the mark-done button.
"""

from decimal import Decimal

import pytest

from app.extensions import db
from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.models.ref import Status, TransactionType
from app.models.transaction import Transaction


class TestRefCacheStatuses:
    """Tests for ref_cache status ID resolution."""

    def test_ref_cache_loads_all_statuses(self, app, db):
        """ref_cache.status_id() returns an integer for every StatusEnum member."""
        with app.app_context():
            for member in StatusEnum:
                result = ref_cache.status_id(member)
                assert isinstance(result, int), (
                    f"status_id({member.name}) returned {type(result)}, expected int"
                )

    def test_ref_cache_loads_all_txn_types(self, app, db):
        """ref_cache.txn_type_id() returns an integer for every TxnTypeEnum member."""
        with app.app_context():
            for member in TxnTypeEnum:
                result = ref_cache.txn_type_id(member)
                assert isinstance(result, int), (
                    f"txn_type_id({member.name}) returned {type(result)}, expected int"
                )

    def test_ref_cache_fails_on_missing_status(self, app, db):
        """ref_cache.init() raises RuntimeError when a status row is missing."""
        with app.app_context():
            # Delete one status row to trigger the failure.
            projected = (
                db.session.query(Status)
                .filter_by(name="Projected")
                .one()
            )
            db.session.delete(projected)
            db.session.flush()

            with pytest.raises(RuntimeError, match="Projected"):
                ref_cache.init(db.session)

            # Roll back so other tests aren't affected.
            db.session.rollback()

            # Re-init cache with all rows present.
            ref_cache.init(db.session)


class TestStatusBooleanColumns:
    """Tests for the boolean columns on the Status model."""

    def test_status_boolean_columns_correct(self, app, db):
        """All 6 statuses have the correct boolean column values.

        Expected:
          Projected:  settled=F, immutable=F, excludes=F
          Paid:       settled=T, immutable=T, excludes=F
          Received:   settled=T, immutable=T, excludes=F
          Credit:     settled=F, immutable=T, excludes=T
          Cancelled:  settled=F, immutable=T, excludes=T
          Settled:    settled=T, immutable=T, excludes=F
        """
        with app.app_context():
            expected = {
                "Projected": (False, False, False),
                "Paid": (True, True, False),
                "Received": (True, True, False),
                "Credit": (False, True, True),
                "Cancelled": (False, True, True),
                "Settled": (True, True, False),
            }
            for name, (settled, immutable, excludes) in expected.items():
                status = (
                    db.session.query(Status).filter_by(name=name).one()
                )
                assert status.is_settled == settled, (
                    f"{name}: is_settled={status.is_settled}, expected {settled}"
                )
                assert status.is_immutable == immutable, (
                    f"{name}: is_immutable={status.is_immutable}, expected {immutable}"
                )
                assert status.excludes_from_balance == excludes, (
                    f"{name}: excludes_from_balance={status.excludes_from_balance}, "
                    f"expected {excludes}"
                )


class TestEffectiveAmount:
    """Tests for Transaction.effective_amount with boolean status columns."""

    def test_effective_amount_returns_zero_for_excluded_status(
        self, app, db, seed_user, seed_periods
    ):
        """effective_amount returns Decimal('0') for Credit status
        (excludes_from_balance=True).
        """
        with app.app_context():
            credit_id = ref_cache.status_id(StatusEnum.CREDIT)
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="expense").one()
            )

            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=credit_id,
                name="Credited Expense",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("250.00"),
            )
            db.session.add(txn)
            db.session.flush()

            assert txn.effective_amount == Decimal("0")

    def test_effective_amount_uses_actual_for_settled_status(
        self, app, db, seed_user, seed_periods
    ):
        """effective_amount returns actual_amount for Paid status
        (is_settled=True) when actual_amount is set.
        """
        with app.app_context():
            done_id = ref_cache.status_id(StatusEnum.DONE)
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="expense").one()
            )

            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=done_id,
                name="Paid Expense",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
                actual_amount=Decimal("487.00"),
            )
            db.session.add(txn)
            db.session.flush()

            assert txn.effective_amount == Decimal("487.00")

    def test_effective_amount_uses_estimated_for_projected(
        self, app, db, seed_user, seed_periods
    ):
        """effective_amount returns estimated_amount for Projected status."""
        with app.app_context():
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="expense").one()
            )

            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected_id,
                name="Projected Expense",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
            )
            db.session.add(txn)
            db.session.flush()

            assert txn.effective_amount == Decimal("500.00")


class TestGridShowsPaidNotDone:
    """Tests that the grid UI shows 'Paid' instead of 'Done'."""

    def test_grid_shows_paid_not_done(self, app, auth_client, seed_user,
                                      seed_periods):
        """The full-edit form for an expense shows 'Paid' button, not 'Done'.

        Verifies the template rename from Commit #1.
        """
        with app.app_context():
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="expense").one()
            )

            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected_id,
                name="Test Expense",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("100.00"),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            assert resp.status_code == 200

            html = resp.data.decode()
            # The mark-done button should say "Paid", not "Done".
            assert "Paid" in html
            # "Done" should not appear as a button label (it may appear
            # in other contexts like status dropdown options).
            assert "> Done<" not in html
