"""Database CHECK constraint regression tests for budget.transactions.

Locks the storage-tier guarantee that estimated_amount and
actual_amount cannot hold negative values.  The constraints are
declared on the model (`app/models/transaction.py`
`ck_transactions_estimated_amount`, `ck_transactions_actual_amount`)
and materialised by migration
`dc46e02d15b4_add_check_constraints_to_loan_params_.py`.

The original H-1 drift fix
(`migrations/versions/724d21236759_drop_redundant_transaction_check_.py`)
removed an older duplicate pair (`ck_transactions_positive_amount` /
`ck_transactions_positive_actual`) that the model never declared but
that the migration chain materialised under different names.  These
tests are the contract that the surviving constraints continue to
catch negative amounts -- if a future migration accidentally drops
both pairs, the test suite turns red here instead of letting a
negative-amount Transaction slip past the storage tier and into
balance projections.

Audit reference: H-1 of
docs/audits/security-2026-04-15/model-migration-drift.md.
"""
# pylint: disable=redefined-outer-name  -- pytest fixture pattern
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.ref import Status, TransactionType
from app.models.transaction import Transaction


def _make_txn_kwargs(seed_user, seed_periods_today):
    """Return the minimum kwargs needed to construct a valid Transaction.

    The caller overrides estimated_amount and/or actual_amount to
    exercise the CHECK constraint under test.
    """
    projected = db.session.query(Status).filter_by(name="Projected").one()
    expense = db.session.query(TransactionType).filter_by(name="Expense").one()
    return {
        "account_id": seed_user["account"].id,
        "pay_period_id": seed_periods_today[0].id,
        "scenario_id": seed_user["scenario"].id,
        "status_id": projected.id,
        "name": "Constraint Test",
        "category_id": seed_user["categories"]["Groceries"].id,
        "transaction_type_id": expense.id,
    }


class TestTransactionAmountCheckConstraints:
    """Negative estimated_amount / actual_amount rejected at flush time."""

    def test_negative_estimated_amount_rejected(
        self, app, db, seed_user, seed_periods_today
    ):
        """Inserting a Transaction with estimated_amount < 0 raises IntegrityError.

        The ck_transactions_estimated_amount CHECK constraint pins
        storage to non-negative values.  Without the constraint, a
        negative-amount transaction would corrupt every balance
        projection that touched the period.
        """
        with app.app_context():
            kwargs = _make_txn_kwargs(seed_user, seed_periods_today)
            txn = Transaction(**kwargs, estimated_amount=Decimal("-1.00"))
            db.session.add(txn)
            with pytest.raises(IntegrityError) as exc_info:
                db.session.flush()
            assert "ck_transactions_estimated_amount" in str(exc_info.value)
            db.session.rollback()

    def test_negative_actual_amount_rejected(
        self, app, db, seed_user, seed_periods_today
    ):
        """Inserting a Transaction with actual_amount < 0 raises IntegrityError.

        The ck_transactions_actual_amount CHECK constraint admits NULL
        (the projected-but-not-yet-paid case) and otherwise pins
        storage to non-negative values.  Mirrors the
        estimated_amount guarantee -- negative actuals would corrupt
        the balance calculator the same way negative estimates would.
        """
        with app.app_context():
            kwargs = _make_txn_kwargs(seed_user, seed_periods_today)
            txn = Transaction(
                **kwargs,
                estimated_amount=Decimal("100.00"),
                actual_amount=Decimal("-1.00"),
            )
            db.session.add(txn)
            with pytest.raises(IntegrityError) as exc_info:
                db.session.flush()
            assert "ck_transactions_actual_amount" in str(exc_info.value)
            db.session.rollback()

    def test_null_actual_amount_allowed(
        self, app, db, seed_user, seed_periods_today
    ):
        """A Transaction with actual_amount IS NULL is the projected default.

        Asserts the CHECK predicate's NULL branch
        (`actual_amount IS NULL OR actual_amount >= 0`) admits the
        common case.  A regression that tightened the constraint to
        `actual_amount >= 0` (no NULL branch) would block every
        projected transaction -- a routine application path -- and
        this test would catch it before the migration hit production.
        """
        with app.app_context():
            kwargs = _make_txn_kwargs(seed_user, seed_periods_today)
            txn = Transaction(
                **kwargs,
                estimated_amount=Decimal("100.00"),
                actual_amount=None,
            )
            db.session.add(txn)
            db.session.flush()
            assert txn.id is not None
            db.session.rollback()
