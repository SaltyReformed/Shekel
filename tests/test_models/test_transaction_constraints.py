"""Database CHECK constraint regression tests for budget.transactions.

Locks the storage-tier guarantee that estimated_amount and the settled
figure cannot hold negative values.  The constraints are declared on the
model (`app/models/transaction.py` `ck_transactions_estimated_amount`,
`ck_transactions_settled_amount`) and materialised by migration
`dc46e02d15b4_add_check_constraints_to_loan_params_.py`, the second of
them renamed with its column by `e4b8a71c0f36_settlement_record.py`.

**The NULL branch changed meaning at plan step X-au-c3** and the third
test says so.  `actual_amount IS NULL` used to be the ordinary
projected-but-not-yet-paid row; a projected row now carries no settlement
record at all, and the branch is exercised by the one SETTLED shape that
stores no figure -- a `purchases` record, where the row's own entries
state the amount.

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

from app import ref_cache
from app.enums import SettlementBasisEnum
from app.extensions import db
from app.models.ref import Status, TransactionType
from app.models.transaction import Transaction
from tests._test_helpers import (
    settle_day_columns,
    settlement_basis_id,
)
from app.models.amount_ownership import AmountOwnership


def _make_txn_kwargs(seed_user, seed_periods_today, status_name="Projected"):
    """Return the minimum kwargs needed to construct a valid Transaction.

    The caller overrides estimated_amount and/or the settlement record to
    exercise the CHECK constraint under test.

    Args:
        seed_user: The seeded owner fixture.
        seed_periods_today: The seeded pay periods; the row lands in the first.
        status_name: The ``ref.statuses`` name to build the row in.  A test
            that writes a settlement record must pass a SETTLED one, because a
            record on a row whose money has not moved is the state the write
            door refuses.
    """
    status = db.session.query(Status).filter_by(name=status_name).one()
    expense = db.session.query(TransactionType).filter_by(name="Expense").one()
    return {
        "account_id": seed_user["account"].id,
        "user_id": seed_periods_today[0].user_id,
        "pay_period_id": seed_periods_today[0].id,
        "scenario_id": seed_user["scenario"].id,
        "status_id": status.id,
        "name": "Constraint Test",
        "category_id": seed_user["categories"]["Groceries"].id,
        "transaction_type_id": expense.id,
    }


class TestTransactionAmountCheckConstraints:
    """Negative estimated_amount / settled_amount rejected at flush time."""

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
            txn = Transaction(**kwargs, amount_ownership=AmountOwnership.own(Decimal("-1.00")))
            db.session.add(txn)
            with pytest.raises(IntegrityError) as exc_info:
                db.session.flush()
            assert "ck_transactions_estimated_amount" in str(exc_info.value)
            db.session.rollback()

    def test_negative_settled_amount_rejected(
        self, app, db, seed_user, seed_periods_today
    ):
        """Inserting a Transaction with settled_amount < 0 raises IntegrityError.

        The ck_transactions_settled_amount CHECK constraint admits NULL
        (the `purchases` record, whose figure its entries state) and
        otherwise pins storage to non-negative values.  Mirrors the
        estimated_amount guarantee -- a negative recorded figure would
        corrupt the balance calculator the same way a negative estimate
        would.

        The rest of the record is COHERENT (a settled status, a settle day,
        and a basis that stores its figure) so the flush fails on the
        constraint under test and not on the record's own pairing.
        """
        with app.app_context():
            kwargs = _make_txn_kwargs(
                seed_user, seed_periods_today, status_name="Paid",
            )
            txn = Transaction(
                **kwargs,
                amount_ownership=AmountOwnership.own(Decimal("100.00")),
                **settle_day_columns(seed_periods_today[0].start_date),
                settled_amount=Decimal("-1.00"),
                settled_basis_id=settlement_basis_id(SettlementBasisEnum.CORRECTED),
            )
            db.session.add(txn)
            with pytest.raises(IntegrityError) as exc_info:
                db.session.flush()
            assert "ck_transactions_settled_amount" in str(exc_info.value)
            db.session.rollback()

    def test_null_settled_amount_allowed(
        self, app, db, seed_user, seed_periods_today
    ):
        """A `purchases` record stores no figure, and the CHECK admits it.

        Asserts the CHECK predicate's NULL branch
        (`settled_amount IS NULL OR settled_amount >= 0`).  A regression
        that tightened the constraint to `settled_amount >= 0` (no NULL
        branch) would block every envelope close -- a routine application
        path -- and this test would catch it before the migration hit
        production.

        **The shape that exercises it changed at plan step X-au-c3.**  It used
        to be the ordinary projected row, whose `actual_amount` was NULL until
        somebody typed one; a projected row now carries no settlement record at
        all, so the branch belongs to the one SETTLED basis that stores nothing.
        """
        with app.app_context():
            kwargs = _make_txn_kwargs(
                seed_user, seed_periods_today, status_name="Paid",
            )
            txn = Transaction(
                **kwargs,
                amount_ownership=AmountOwnership.own(Decimal("100.00")),
                **settle_day_columns(seed_periods_today[0].start_date),
                settled_amount=None,
                settled_basis_id=ref_cache.settlement_basis_id(
                    SettlementBasisEnum.PURCHASES,
                ),
            )
            db.session.add(txn)
            db.session.flush()
            assert txn.id is not None
            db.session.rollback()
