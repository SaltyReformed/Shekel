"""Plan step ``balance:X-bi-6-4d`` leaf 1: the hard delete asks ``transfer_legs``.

The transfer's hard delete hands the one removal act every entry its transfer
holds, and since leaf ``X-bi-6-4d-1`` it asks
``transfer_legs.held_transfer_entries`` for them rather than walking each
shadow's ``entries`` -- so ``X-bi-6-4d``'s re-parent moves the collection with
the join.  The scope is ANY entry under ANY shadow, live or dead, because that
is what ``fk_transaction_entries_transaction_id`` (NO ACTION) refuses the
cascade for: a kept, un-dated record a revert left behind counts, and so does
one under a shadow an occurrence delete hid (finding **BAL-532**'s state).
A collection narrowed to dated movements, or to live shadows, leaves such a
record behind and the delete fails at the flush -- the two cases below are
that scope's grade.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services import transfer_service
from tests._test_helpers import create_account_of_type, create_settled_transfer


def _a_reverted_transfer(seed_user, seed_periods):
    """A $250.00 checking -> Rainy Day transfer, settled then set back to Projected.

    The revert keeps each side's covering movement, un-dated (ruling
    **R-BAL61**), so the transfer still holds two entries and no dated one.

    Returns:
        ``(transfer_id, the kept movements' ids)``.
    """
    savings = create_account_of_type(
        seed_user, db.session, "Savings", "Rainy Day",
    )
    db.session.commit()
    transfer = create_settled_transfer(
        seed_user, db.session, seed_user["account"], savings,
        seed_periods[0], amount=Decimal("250.00"),
    )
    db.session.commit()
    transfer_service.update_transfer(
        transfer.id, seed_user["user"].id,
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
    )
    db.session.commit()
    kept = (
        db.session.query(TransactionEntry)
        .join(Transaction, TransactionEntry.transaction_id == Transaction.id)
        .filter(Transaction.transfer_id == transfer.id)
        .all()
    )
    assert len(kept) == 2
    assert all(m.covers_settlement and m.settled_on is None for m in kept)
    return transfer.id, {m.id for m in kept}


def _gone(transfer_id, movement_ids):
    """Assert the transfer and every one of *movement_ids* no longer exist."""
    db.session.expire_all()
    assert db.session.get(Transfer, transfer_id) is None
    assert (
        db.session.query(TransactionEntry)
        .filter(TransactionEntry.id.in_(movement_ids))
        .count()
    ) == 0


class TestTheHardDeleteTakesEveryEntryTheTransferHolds:
    """Kept un-dated records, under live shadows and under hidden ones."""

    def test_a_reverted_transfers_kept_records_leave_with_it(
        self, app, seed_user, seed_periods,
    ):
        """Hard-deleting the reverted $250.00 transfer takes both kept records."""
        with app.app_context():
            transfer_id, movement_ids = _a_reverted_transfer(
                seed_user, seed_periods,
            )

            transfer_service.delete_transfer(
                transfer_id, seed_user["user"].id, soft=False,
            )
            db.session.commit()

            _gone(transfer_id, movement_ids)

    def test_records_under_hidden_shadows_leave_with_it(
        self, app, seed_user, seed_periods,
    ):
        """Soft-deleted first (both shadows hidden), then hard-deleted.

        The soft delete withdraws nothing (``_delete``'s own rule), so the
        two kept records sit under DEAD shadows -- the state finding
        **BAL-532** names -- and the hard delete must still find them.
        """
        with app.app_context():
            transfer_id, movement_ids = _a_reverted_transfer(
                seed_user, seed_periods,
            )
            transfer_service.delete_transfer(
                transfer_id, seed_user["user"].id, soft=True,
            )
            db.session.commit()
            assert (
                db.session.query(Transaction)
                .filter_by(transfer_id=transfer_id, is_deleted=False)
                .count()
            ) == 0

            transfer_service.delete_transfer(
                transfer_id, seed_user["user"].id, soft=False,
            )
            db.session.commit()

            _gone(transfer_id, movement_ids)
