"""Plan step ``credit_card:CC-5-4a-5`` leaf 5c-2a: the statement screen says what it withholds.

Ruling **R-CC137** (developer 2026-10-04, "Only Paid from, say why"), graded
through the statement screen's own GET route: *"Checking's statement screen
does not offer Hotel while its payment is recorded on the Visa (the reconcile
panel's test, R-CC126), and says so on that screen: 'Hotel $120.00 is planned
on Checking but its payment is recorded on the Visa, so it is not offered
here. If Checking paid it, press Paid in Hotel's popover with Paid from set to
Checking.' Doing that marks Hotel Paid on Checking dated today; Checking's
screen then offers its payment"*.  The service-level half (the offer set, the
refused accept on Checking, the accept on the card) is
``test_cc5_3_settle_tender::TestTheRowsClearingLinkStaysOnTheRowsAccount
::test_the_matcher_never_moves_a_card_payment``.

Every figure here is a made-up example.
"""

from __future__ import annotations

from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.services import entry_service, transaction_service
from app.services.statement_match import RowKind
from app.services.transaction_service import settle_transaction
from tests._test_helpers import (
    create_account_of_type,
    generate_row_of,
    make_expense_template,
    typed,
)
# Pylint: ``shekel-private-module-import`` -- the statement-match builders are
# the one way a test stages a scope as the app does; the convention
# ``test_statement_reconcile`` keeps.
# pylint: disable=shekel-private-module-import
from tests.test_services.test_statement_match._builders import a_scope


def _screen(auth_client, account_id):
    """Return the account's statement screen body."""
    response = auth_client.get(f"/accounts/{account_id}/statements/reconcile")
    assert response.status_code == 200
    return response.data.decode()


def _reverted_card_paid_hotel(seed_user, period):
    """Hotel $120.00 planned on checking, marked paid from the card, set back to Projected."""
    card = create_account_of_type(
        seed_user, db.session, "Credit Card", "Rewards Card",
        anchor_balance=Decimal("-500.00"),
    )
    hotel = generate_row_of(
        make_expense_template(
            db.session, seed_user, amount="120.00", name="Hotel",
            category_key="Groceries",
        ),
        period,
    )
    db.session.commit()
    settle_transaction(hotel, tender_account_id=card.id)
    db.session.commit()
    transaction_service.apply_requested_status(
        hotel, ref_cache.status_id(StatusEnum.PROJECTED),
    )
    db.session.commit()
    return hotel, card


class TestTheScreenSaysWhatItWithholds:
    """R-CC137: the withheld row is named on its own account's screen, with the remedy."""

    def test_the_checking_screen_names_the_card_paid_bill(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The ruled sentence, account names as the owner named them; the row is not offered."""
        with app.app_context():
            checking = seed_user["account"]
            hotel, card = _reverted_card_paid_hotel(
                seed_user, seed_periods_today[0],
            )
            body = _screen(auth_client, checking.id)

            assert (
                f"Hotel $120.00 is planned on {checking.name} but its payment "
                f"is recorded on {card.name}, so it is not offered here. If "
                f"{checking.name} paid it, press Paid in Hotel&#39;s popover "
                f"with Paid from set to {checking.name}."
            ) in body
            assert [
                row for row in a_scope(seed_user).candidates.rows
                if row.transaction_id == hotel.id
            ] == []

    def test_paid_from_checking_ends_the_sentence_and_offers_the_payment(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The remedy the sentence names: Paid with Paid from checking, then checking offers it."""
        with app.app_context():
            checking = seed_user["account"]
            hotel, _card = _reverted_card_paid_hotel(
                seed_user, seed_periods_today[0],
            )
            settle_transaction(hotel, tender_account_id=checking.id)
            db.session.commit()

            body = _screen(auth_client, checking.id)

            assert "is planned on" not in body
            (offered,) = [
                row for row in a_scope(seed_user).candidates.rows
                if row.transaction_id == hotel.id
            ]
            assert offered.kind is RowKind.SETTLEMENT
            (payment,) = hotel.covering_movements
            assert payment.account_id == checking.id
            assert offered.row_id == payment.id

    def test_a_bill_holding_purchases_stays_offered_on_its_own_screen(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """'A bill paid by its own purchases stays on its own list' (R-CC126, the panel's test)."""
        with app.app_context():
            checking = seed_user["account"]
            envelope = generate_row_of(
                make_expense_template(
                    db.session, seed_user, amount="300.00", name="Groceries",
                    category_key="Groceries", is_envelope=True,
                ),
                seed_periods_today[0],
            )
            db.session.commit()
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            settle_transaction(
                envelope, submitted=typed(Decimal("120.00")),
                tender_account_id=card.id,
            )
            db.session.commit()
            transaction_service.apply_requested_status(
                envelope, ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()
            entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                entry_service.EntryDetails(
                    figure=typed(Decimal("30.00")), description="Kroger",
                    purchased_on=seed_periods_today[0].start_date,
                ),
            )
            db.session.commit()

            body = _screen(auth_client, checking.id)

            assert "is planned on" not in body
            assert a_scope(seed_user).candidates.held_elsewhere == ()
