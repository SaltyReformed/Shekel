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
from tests._test_helpers import (
    generate_row_of,
    make_expense_template,
    one_off_row_of,
    repriced_by_the_owner,
    settle_day_columns,
    settlement_basis_id,
)


def _placed_row(seed_user, seed_periods_today, status_name="Projected"):
    """Place an ordinary one-off in the first paycheck, *status_name* laid on BARE.

    The producer's row (plan step balance:X-bi-7c).  The settlement record
    is the ROW's own -- ``settled_amount`` / ``settled_basis_id`` -- so the
    two settled-figure cases lay theirs on this row and flush; the row's
    plan figure is its definition's and never on the row, which is why the
    negative-estimate case takes the OWN arm instead (see it).

    Args:
        seed_user: The seeded owner fixture.
        seed_periods_today: The seeded pay periods; the row lands in the first.
        status_name: The ``ref.statuses`` name to lay on the placed row.  A
            test that writes a settlement record must pass a SETTLED one,
            because a record on a row whose money has not moved is the state
            the write door refuses.

    Returns:
        The placed, flushed :class:`~app.models.transaction.Transaction`.
    """
    status = db.session.query(Status).filter_by(name=status_name).one()
    expense = db.session.query(TransactionType).filter_by(name="Expense").one()
    txn = one_off_row_of(
        seed_periods_today[0],
        name="Constraint Test",
        amount=Decimal("100.00"),
        user_id=seed_periods_today[0].user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=expense.id,
        category_id=seed_user["categories"]["Groceries"].id,
    )
    txn.status_id = status.id
    return txn


class TestTransactionAmountCheckConstraints:
    """Negative estimated_amount / settled_amount rejected at flush time."""

    def test_negative_estimated_amount_rejected(
        self, app, db, seed_user, seed_periods_today
    ):
        """Writing a row that OWNS estimated_amount < 0 raises IntegrityError.

        The ck_transactions_estimated_amount CHECK constraint pins
        storage to non-negative values.  Without the constraint, a
        negative-amount transaction would corrupt every balance
        projection that touched the period.

        The row that STORES a figure is the OWNER's: a recurring
        definition's row the owner re-priced (X-cf's idiom, ruling R-BAL17;
        ``repriced_by_the_owner`` flushes the two acts).  A one-off's figure
        is its definition's and never on the row (R-BAL29), so this CHECK
        binds on the OWN arm alone (plan step balance:X-bi-7c).
        """
        with app.app_context():
            template = make_expense_template(
                db.session, seed_user, amount="100.00", name="Constraint Test",
                category_key="Groceries",
            )
            row = generate_row_of(template, seed_periods_today[0])
            with pytest.raises(IntegrityError) as exc_info:
                repriced_by_the_owner(row, "-1.00")
            assert "ck_transactions_estimated_amount" in str(exc_info.value)
            db.session.rollback()

    def test_negative_settled_amount_rejected(
        self, app, db, seed_user, seed_periods_today
    ):
        """Writing a Transaction with settled_amount < 0 raises IntegrityError.

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
            txn = _placed_row(seed_user, seed_periods_today, status_name="Paid")
            for column, value in settle_day_columns(
                seed_periods_today[0].start_date,
            ).items():
                setattr(txn, column, value)
            txn.settled_amount = Decimal("-1.00")
            txn.settled_basis_id = settlement_basis_id(SettlementBasisEnum.CORRECTED)
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
            txn = _placed_row(seed_user, seed_periods_today, status_name="Paid")
            for column, value in settle_day_columns(
                seed_periods_today[0].start_date,
            ).items():
                setattr(txn, column, value)
            purchases_basis = ref_cache.settlement_basis_id(
                SettlementBasisEnum.PURCHASES,
            )
            txn.settled_amount = None
            txn.settled_basis_id = purchases_basis
            db.session.flush()
            # The row was flushed at placement, so its id proves nothing about
            # this UPDATE: read the stored record back.
            db.session.refresh(txn)
            assert txn.settled_amount is None
            assert txn.settled_basis_id == purchases_basis
            db.session.rollback()
