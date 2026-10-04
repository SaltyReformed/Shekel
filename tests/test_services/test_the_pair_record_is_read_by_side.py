"""What a transfer pair already RECORDS is read by SIDE, not by account (leaf ``X-bi-6-4d-1``).

The settle and the update read the pair's record -- the retained correction a
re-settle honours, the figure an echoed Actual box is compared against -- off
its expense side, through ``transfer_legs`` (``TransferRows.expense_leg``).
An update may move an endpoint AND settle in one call (``update_transfer``'s
contract; ``_endpoints._apply_endpoint_move`` names the case), and the move
assigns the transfer's account RELATIONSHIP, whose ``from_account_id`` column
reads the OLD account until a flush.  A read keyed by that column found the
record only when some lazy load happened to autoflush first; with the shadows'
``entries`` already loaded, nothing did, and the settle raised.  Keyed by the
side -- the shadow's type through the interval, the side link from
``X-bi-6-4d`` -- the read names no account at all.  The leaf's adversarial
review measured the regression (1 failed at its cp1, 3 passed on the base);
this is that case.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import transfer_service
from tests._test_helpers import (
    create_account_of_type,
    create_settled_transfer,
    typed,
)


class TestAMoveAndSettleHonoursTheRetainedCorrection:
    """One call re-points the source and settles; the typed $214.37 still books."""

    def test_the_move_and_settle_with_the_entries_loaded(
        self, app, seed_user, seed_periods,
    ):
        """$250.00 checking -> Rainy Day, corrected to $214.37, reverted, then moved and settled.

        Both shadows' ``entries`` are loaded before the update, so no lazy
        load in the move autoflushes the re-pointed account.  The settle must
        still find the expense side's kept record and honour its typed
        $214.37 on both legs, the source side now booked on the new account.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Rainy Day",
            )
            other = create_account_of_type(
                seed_user, db.session, "Checking", "Second Checking",
            )
            db.session.commit()
            transfer = create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("250.00"),
            )
            db.session.commit()
            transfer_service.update_transfer(
                transfer.id, user_id, figure=typed(Decimal("214.37")),
            )
            db.session.commit()
            transfer_service.update_transfer(
                transfer.id, user_id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()
            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=transfer.id)
                .all()
            )
            for shadow in shadows:
                assert len(list(shadow.entries)) == 1

            transfer_service.update_transfer(
                transfer.id, user_id,
                from_account_id=other.id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.commit()

            db.session.expire_all()
            moved = db.session.get(Transfer, transfer.id)
            assert moved.from_account_id == other.id
            records = {
                shadow.account_id: shadow.covering_movements
                for shadow in db.session.query(Transaction)
                .filter_by(transfer_id=transfer.id)
            }
            assert set(records) == {other.id, savings.id}
            for account_id, (record,) in records.items():
                assert record.account_id == account_id
                assert record.amount == Decimal("214.37")
                assert record.settled_on is not None
