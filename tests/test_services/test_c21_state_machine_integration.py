"""
Shekel Budget App -- C-21 State Machine Integration Tests

Verifies that the state-machine helper (``app.services.state_machine``)
is correctly wired into the transfer service, and that the partial
unique index ``uq_transactions_transfer_type_active`` enforces the
single-active-shadow-of-each-type invariant at the database tier.

Audit reference: F-046 / F-047 / F-161 / commit C-21 of the
2026-04-15 security remediation plan.
"""

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.category import Category
from app.models.ref import Status
from app.models.transaction import Transaction
from app.services import transfer_service


UNIQUE_INDEX_NAME = "uq_transactions_transfer_type_active"


# ── Fixture ─────────────────────────────────────────────────────────


@pytest.fixture()
def transfer_data(app, seed_full_user_data):
    """Provide everything the transfer service needs for these tests.

    Adds the default Transfers: Incoming and Transfers: Outgoing
    categories that the conftest seed_full_user_data fixture does not
    include.  Mirrors the fixture in ``test_transfer_service.py`` so
    these tests can use the same ``_create_basic_transfer`` helper.
    """
    data = seed_full_user_data
    user = data["user"]

    projected = db.session.query(Status).filter_by(name="Projected").one()

    incoming_cat = Category(
        user_id=user.id, group_name="Transfers", item_name="Incoming",
        sort_order=90,
    )
    outgoing_cat = Category(
        user_id=user.id, group_name="Transfers", item_name="Outgoing",
        sort_order=91,
    )
    db.session.add_all([incoming_cat, outgoing_cat])
    db.session.commit()

    return {
        **data,
        "projected_status": projected,
        "incoming_cat": incoming_cat,
        "outgoing_cat": outgoing_cat,
    }


def _create_basic_transfer(td):
    """Helper: create a transfer using the test data dict."""
    return transfer_service.create_transfer(
        user_id=td["user"].id,
        from_account_id=td["account"].id,
        to_account_id=td["savings_account"].id,
        pay_period_id=td["periods"][0].id,
        scenario_id=td["scenario"].id,
        amount=Decimal("250.00"),
        status_id=td["projected_status"].id,
        category_id=td["categories"]["Rent"].id,
    )


# ── Transfer service: legal transitions propagate to shadows ────────


class TestTransferServiceLegalTransitions:
    """update_transfer accepts every legal transition listed in
    state_machine and propagates the new status to both shadow
    transactions atomically."""

    def test_projected_to_done_propagates(self, app, db, transfer_data):
        """projected -> done is legal and reaches both shadows."""
        td = transfer_data
        with app.app_context():
            xfer = _create_basic_transfer(td)
            db.session.commit()
            done_id = ref_cache.status_id(StatusEnum.DONE)

            transfer_service.update_transfer(
                xfer.id, td["user"].id, status_id=done_id,
            )
            db.session.commit()

            db.session.refresh(xfer)
            assert xfer.status_id == done_id
            shadows = db.session.query(Transaction).filter_by(
                transfer_id=xfer.id,
            ).all()
            assert len(shadows) == 2
            for s in shadows:
                assert s.status_id == done_id

    def test_done_to_settled_propagates(self, app, db, transfer_data):
        """done -> settled is legal."""
        td = transfer_data
        with app.app_context():
            xfer = _create_basic_transfer(td)
            db.session.commit()
            done_id = ref_cache.status_id(StatusEnum.DONE)
            settled_id = ref_cache.status_id(StatusEnum.SETTLED)

            transfer_service.update_transfer(
                xfer.id, td["user"].id, status_id=done_id,
            )
            db.session.commit()
            transfer_service.update_transfer(
                xfer.id, td["user"].id, status_id=settled_id,
            )
            db.session.commit()

            db.session.refresh(xfer)
            assert xfer.status_id == settled_id
            shadows = db.session.query(Transaction).filter_by(
                transfer_id=xfer.id,
            ).all()
            for s in shadows:
                assert s.status_id == settled_id

    def test_done_to_projected_revert_propagates(self, app, db, transfer_data):
        """done -> projected (revert) is legal."""
        td = transfer_data
        with app.app_context():
            xfer = _create_basic_transfer(td)
            db.session.commit()
            done_id = ref_cache.status_id(StatusEnum.DONE)
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)

            transfer_service.update_transfer(
                xfer.id, td["user"].id, status_id=done_id,
            )
            db.session.commit()
            transfer_service.update_transfer(
                xfer.id, td["user"].id, status_id=projected_id,
            )
            db.session.commit()

            db.session.refresh(xfer)
            assert xfer.status_id == projected_id

    def test_identity_transition_succeeds(self, app, db, transfer_data):
        """projected -> projected on a still-projected transfer is a no-op."""
        td = transfer_data
        with app.app_context():
            xfer = _create_basic_transfer(td)
            db.session.commit()
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)

            transfer_service.update_transfer(
                xfer.id, td["user"].id, status_id=projected_id,
            )
            db.session.commit()

            db.session.refresh(xfer)
            assert xfer.status_id == projected_id


# ── Transfer service: illegal transitions are rejected ──────────────


class TestTransferServiceIllegalTransitions:
    """update_transfer rejects illegal transitions by raising
    ValidationError.  No partial mutation occurs -- the parent
    transfer's status_id and both shadow rows remain at their
    pre-call values."""

    def test_projected_to_settled_rejected(self, app, db, transfer_data):
        """settled is unreachable from projected -- carry-forward
        contract requires a Done/Received audit row in between."""
        td = transfer_data
        with app.app_context():
            xfer = _create_basic_transfer(td)
            db.session.commit()
            settled_id = ref_cache.status_id(StatusEnum.SETTLED)
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            original_status_id = xfer.status_id
            assert original_status_id == projected_id

            with pytest.raises(ValidationError) as excinfo:
                transfer_service.update_transfer(
                    xfer.id, td["user"].id, status_id=settled_id,
                )
            # Service raises before any shadow mutation -- all three
            # rows should still hold the pre-call status.
            assert "transfer" in str(excinfo.value)

            db.session.rollback()
            db.session.refresh(xfer)
            assert xfer.status_id == projected_id
            shadows = db.session.query(Transaction).filter_by(
                transfer_id=xfer.id,
            ).all()
            for s in shadows:
                assert s.status_id == projected_id

    def test_settled_to_projected_rejected(self, app, db, transfer_data):
        """settled is terminal -- revert is forbidden."""
        td = transfer_data
        with app.app_context():
            xfer = _create_basic_transfer(td)
            db.session.commit()
            done_id = ref_cache.status_id(StatusEnum.DONE)
            settled_id = ref_cache.status_id(StatusEnum.SETTLED)
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)

            # Walk to settled legally first.
            transfer_service.update_transfer(
                xfer.id, td["user"].id, status_id=done_id,
            )
            db.session.commit()
            transfer_service.update_transfer(
                xfer.id, td["user"].id, status_id=settled_id,
            )
            db.session.commit()

            with pytest.raises(ValidationError):
                transfer_service.update_transfer(
                    xfer.id, td["user"].id, status_id=projected_id,
                )

            # Roll back the failed transition; the row stays settled.
            db.session.rollback()
            db.session.refresh(xfer)
            assert xfer.status_id == settled_id

    def test_done_to_cancelled_rejected(self, app, db, transfer_data):
        """done -> cancelled would erase the Paid audit trail."""
        td = transfer_data
        with app.app_context():
            xfer = _create_basic_transfer(td)
            db.session.commit()
            done_id = ref_cache.status_id(StatusEnum.DONE)
            cancelled_id = ref_cache.status_id(StatusEnum.CANCELLED)

            transfer_service.update_transfer(
                xfer.id, td["user"].id, status_id=done_id,
            )
            db.session.commit()

            with pytest.raises(ValidationError):
                transfer_service.update_transfer(
                    xfer.id, td["user"].id, status_id=cancelled_id,
                )

            db.session.rollback()
            db.session.refresh(xfer)
            assert xfer.status_id == done_id


# ── Partial unique index shape and predicate ────────────────────────


class TestPartialUniqueIndexShape:
    """The partial unique index is present in the live schema with
    exactly the predicate the application code relies on."""

    def test_index_exists_in_pg_catalog(self, app):
        """``pg_indexes`` carries the index with the expected definition."""
        with app.app_context():
            row = db.session.execute(text(
                "SELECT indexname, indexdef "
                "FROM pg_indexes "
                "WHERE schemaname = 'budget' "
                "  AND indexname = :name"
            ), {"name": UNIQUE_INDEX_NAME}).fetchone()
            assert row is not None, (
                f"Partial unique index {UNIQUE_INDEX_NAME!r} not found in "
                "pg_indexes -- migration c21a1f0b8e74 may not have been "
                "applied."
            )
            indexdef = row[1]
            # Composite columns and uniqueness must both be present.
            assert "UNIQUE INDEX" in indexdef.upper()
            assert "transfer_id" in indexdef
            assert "transaction_type_id" in indexdef
            # Predicate covers exactly the active-shadow lifecycle.
            assert "transfer_id IS NOT NULL" in indexdef
            assert "is_deleted = false" in indexdef.lower()


# ── Partial unique index behaviour ──────────────────────────────────


class TestPartialUniqueIndexBehaviour:
    """The partial unique index rejects a third active shadow row of
    the same transaction_type, but allows a re-insert after a
    soft-delete clears the predicate."""

    def _insert_shadow_directly(self, td, xfer, txn_type_id):
        """Insert a shadow row that bypasses the transfer service.

        Used for tests that exercise the database-level partial unique
        index in isolation.  The service layer's ``_get_shadow_transactions``
        check is intentionally skipped so the index becomes the only
        safeguard being asserted.
        """
        projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
        shadow = Transaction(
            account_id=td["account"].id,
            pay_period_id=td["periods"][0].id,
            scenario_id=td["scenario"].id,
            status_id=projected_id,
            name="Rogue extra shadow",
            category_id=td["categories"]["Rent"].id,
            transaction_type_id=txn_type_id,
            estimated_amount=Decimal("250.00"),
            transfer_id=xfer.id,
            is_deleted=False,
        )
        db.session.add(shadow)
        db.session.flush()
        return shadow

    def test_third_active_shadow_blocked(self, app, db, transfer_data):
        """A 3rd active shadow row of an existing transaction_type
        triggers the partial unique index."""
        td = transfer_data
        with app.app_context():
            xfer = _create_basic_transfer(td)
            db.session.commit()
            expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)

            with pytest.raises(IntegrityError):
                # Bypass the service layer entirely -- attempt a raw
                # INSERT of a third active shadow with the same
                # transaction_type as one of the existing two.
                self._insert_shadow_directly(td, xfer, expense_type_id)

            db.session.rollback()
            shadows = db.session.query(Transaction).filter_by(
                transfer_id=xfer.id, is_deleted=False,
            ).all()
            assert len(shadows) == 2

    def test_insert_allowed_after_soft_delete(self, app, db, transfer_data):
        """Soft-deleting a shadow removes it from the partial index, so
        a fresh active shadow of the same transaction_type for the same
        transfer can then be inserted.  This is the predicate guarantee
        that makes restore-after-create flows work -- mirrors the
        credit-payback partial-index lifecycle in commit C-19."""
        td = transfer_data
        with app.app_context():
            xfer = _create_basic_transfer(td)
            db.session.commit()
            expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)

            # Soft-delete the existing expense shadow -- it leaves the
            # index because the predicate excludes ``is_deleted = TRUE``.
            existing_expense = (
                db.session.query(Transaction)
                .filter_by(
                    transfer_id=xfer.id,
                    transaction_type_id=expense_type_id,
                    is_deleted=False,
                )
                .one()
            )
            existing_expense.is_deleted = True
            db.session.commit()

            # Insert a fresh active expense shadow for the same
            # transfer.  Without the partial predicate this would
            # fail; with it, the index permits the insert because
            # the soft-deleted row is excluded.
            self._insert_shadow_directly(td, xfer, expense_type_id)
            db.session.commit()

            active = db.session.query(Transaction).filter_by(
                transfer_id=xfer.id,
                transaction_type_id=expense_type_id,
                is_deleted=False,
            ).all()
            assert len(active) == 1

    def test_index_does_not_apply_to_non_transfer_rows(self, app, db, transfer_data):
        """Regular transactions (transfer_id IS NULL) are excluded from
        the index -- two projected expense rows in the same period and
        type must continue to coexist as they always have."""
        td = transfer_data
        with app.app_context():
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)

            txn_a = Transaction(
                account_id=td["account"].id,
                pay_period_id=td["periods"][0].id,
                scenario_id=td["scenario"].id,
                status_id=projected_id,
                name="Regular A",
                category_id=td["categories"]["Rent"].id,
                transaction_type_id=expense_type_id,
                estimated_amount=Decimal("100.00"),
                transfer_id=None,
            )
            txn_b = Transaction(
                account_id=td["account"].id,
                pay_period_id=td["periods"][0].id,
                scenario_id=td["scenario"].id,
                status_id=projected_id,
                name="Regular B",
                category_id=td["categories"]["Rent"].id,
                transaction_type_id=expense_type_id,
                estimated_amount=Decimal("200.00"),
                transfer_id=None,
            )
            db.session.add_all([txn_a, txn_b])
            # No IntegrityError expected; the predicate excludes NULL
            # transfer_id rows from the index.
            db.session.commit()
            assert txn_a.id is not None
            assert txn_b.id is not None
