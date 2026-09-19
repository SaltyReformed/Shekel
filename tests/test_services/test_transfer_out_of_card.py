"""
Shekel Budget App -- a transfer OUT of a credit card is refused at both doors

Plan step ``credit_card:CC-10``, design ``docs/design/credit_card_from_scratch.md``
3.8: no cash advances, no balance transfers.
``_validation._reject_transfer_out_of_revolving`` is the loan refusal's
sibling inside ``_validation._reject_unmodeled_source``, the ONE set of source
refusals both doors call -- ``create_transfer`` and the endpoint move in
``_endpoints._resolve_endpoints`` -- and nowhere else.  The allow pins are the
controls: the SAME requests with the card as the DESTINATION succeed, so the
refusal is attributable to the card being the SOURCE and to nothing else.
"""

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.amount_ownership import AmountOwnership
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import transfer_service
from tests._test_helpers import create_account_of_type


def _card(td, name="Visa"):
    """Build a Credit Card account owned by the seeded user, books open."""
    return create_account_of_type(
        td, db.session, "Credit Card", name,
        anchor_balance=Decimal("-500.00"),
    )


def _spec(td, *, from_account_id, to_account_id):
    """The one create spec these tests vary only by its endpoints."""
    return transfer_service.TransferSpec(
        user_id=td["user"].id,
        from_account_id=from_account_id,
        to_account_id=to_account_id,
        pay_period_id=td["periods"][0].id,
        scenario_id=td["scenario"].id,
        amount_ownership=AmountOwnership.own(Decimal("100.00")),
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
        category_id=td["categories"]["Rent"].id,
    )


def _legs(xfer):
    """Return this transfer's ``(expense, income)`` shadows."""
    expense_type = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    shadows = db.session.query(Transaction).filter_by(
        transfer_id=xfer.id, is_deleted=False,
    ).all()
    assert len(shadows) == 2
    expense = [s for s in shadows if s.transaction_type_id == expense_type]
    income = [s for s in shadows if s.transaction_type_id != expense_type]
    return expense[0], income[0]


class TestTheCreateDoor:
    """``create_transfer``: the card may be the destination, never the source."""

    def test_a_transfer_out_of_a_card_is_refused_and_writes_nothing(
        self, app, db, seed_full_user_data,
    ):
        """A card as ``from_account`` raises ValidationError before any write.

        A transfer OUT of a card is a cash advance or a balance transfer,
        which the card refuses rather than models (design 3.8).  The guard
        fires with the loan's, ahead of the first ``db.session.add``, so no
        transfer and no shadow exists afterwards.
        """
        with app.app_context():
            td = seed_full_user_data
            card = _card(td)
            db.session.commit()
            transfers_before = db.session.query(Transfer).count()

            with pytest.raises(ValidationError, match="out of a credit card"):
                transfer_service.create_transfer(_spec(
                    td, from_account_id=card.id, to_account_id=td["account"].id,
                ))

            assert db.session.query(Transfer).count() == transfers_before
            assert (
                db.session.query(Transaction)
                .filter_by(account_id=card.id).count() == 0
            )

    def test_a_transfer_into_a_card_is_its_payment_and_is_allowed(
        self, app, db, seed_full_user_data,
    ):
        """The control: the same spec with the endpoints swapped succeeds.

        A transfer INTO the card is how the card is paid (design 3.5), so it
        must pass the guard that refuses the reverse.  Both shadows exist and
        the income leg sits on the card, which attributes the refusal above to
        the card being the SOURCE and to nothing else about a card.
        """
        with app.app_context():
            td = seed_full_user_data
            card = _card(td)
            db.session.commit()

            xfer = transfer_service.create_transfer(_spec(
                td, from_account_id=td["account"].id, to_account_id=card.id,
            ))
            db.session.flush()

            assert xfer.to_account_id == card.id
            expense, income = _legs(xfer)
            assert expense.account_id == td["account"].id
            assert income.account_id == card.id
            assert income.name == f"Transfer from {td['account'].name}"


class TestTheEndpointMoveDoor:
    """``update_transfer``'s endpoint move: the same rule, asked on a SOURCE move."""

    def test_a_move_making_a_card_the_source_is_refused_before_any_write(
        self, app, db, seed_full_user_data,
    ):
        """Re-pointing a transfer's source onto a card raises, rows untouched.

        The create door refuses a card source; the edit path can move a
        source (plan step R10-b), so it inherits the rule -- otherwise the one
        guard would have a second door straight past it.  The refusal runs in
        ``_resolve_endpoints``, which writes nothing, so the parent and both
        legs still name their pre-edit accounts when it raises.
        """
        with app.app_context():
            td = seed_full_user_data
            card = _card(td)
            xfer = transfer_service.create_transfer(_spec(
                td,
                from_account_id=td["account"].id,
                to_account_id=td["savings_account"].id,
            ))
            db.session.commit()

            with pytest.raises(ValidationError, match="out of a credit card"):
                transfer_service.update_transfer(
                    xfer.id, td["user"].id, from_account_id=card.id,
                )

            # The move assigns RELATIONSHIPS (``_apply_endpoint_move``), whose
            # FK columns sync only at a flush -- so a staged move would still
            # read the old ``from_account_id`` here.  Flush first, so the
            # column assertions grade what the session would have written,
            # then roll the refused edit back.
            db.session.flush()
            assert xfer.from_account_id == td["account"].id
            assert xfer.from_account.id == td["account"].id
            expense, income = _legs(xfer)
            assert expense.account_id == td["account"].id
            assert income.account_id == td["savings_account"].id
            db.session.rollback()

    def test_a_move_of_the_destination_onto_a_card_is_allowed(
        self, app, db, seed_full_user_data,
    ):
        """The control: the same edit naming the card as DESTINATION lands.

        Moving where a transfer arrives onto a card turns it into that card's
        payment, which is allowed; the income leg follows and its derived name
        re-states the unchanged source.
        """
        with app.app_context():
            td = seed_full_user_data
            card = _card(td)
            xfer = transfer_service.create_transfer(_spec(
                td,
                from_account_id=td["account"].id,
                to_account_id=td["savings_account"].id,
            ))
            db.session.commit()

            transfer_service.update_transfer(
                xfer.id, td["user"].id, to_account_id=card.id,
            )
            db.session.flush()

            assert xfer.from_account_id == td["account"].id
            assert xfer.to_account_id == card.id
            expense, income = _legs(xfer)
            assert expense.account_id == td["account"].id
            assert expense.name == f"Transfer to {card.name}"
            assert income.account_id == card.id

    def test_a_legacy_card_source_row_can_still_move_its_destination(
        self, app, db, seed_full_user_data,
    ):
        """The refusal is asked about a source MOVE, not about every edit.

        The loan's twin (``test_a_legacy_loan_SOURCE_row_can_still_move_its
        _destination``) pins the R10-b narrowing for the loan arm; this pins
        it for the card arm, so lifting the composed call out of the
        ``vacated_source`` gate is caught whichever arm a test reaches for.
        The legacy state is PLANTED, because no door produces it: a transfer
        whose source is already a card must move its DESTINATION without
        being re-graded for a shape the edit does not touch.
        """
        with app.app_context():
            td = seed_full_user_data
            card = _card(td)
            xfer = transfer_service.create_transfer(_spec(
                td,
                from_account_id=td["account"].id,
                to_account_id=td["savings_account"].id,
            ))
            db.session.flush()
            expense, _ = _legs(xfer)
            # The pre-refusal shape: money leaving a card.
            xfer.from_account = card
            expense.account = card
            db.session.commit()
            elsewhere = create_account_of_type(
                td, db.session, "Savings", "New Destination",
                anchor_balance=Decimal("0.00"),
            )
            db.session.flush()

            transfer_service.update_transfer(
                xfer.id, td["user"].id, to_account_id=elsewhere.id,
            )
            db.session.flush()

            assert xfer.from_account_id == card.id, (
                "the source it did not touch stayed where it was"
            )
            assert xfer.to_account_id == elsewhere.id
            _, income = _legs(xfer)
            assert income.account_id == elsewhere.id
