"""
Shekel Budget App -- "Paid from this account" through the reconcile ROUTE (CC-5-4b)

The list's rules are graded at the service
(``tests/test_services/test_reconcile_settlements.py``).  What is graded here
is the part only the route and the template can get wrong: that the card's
panel renders the section, its note and each row's label, that an envelope's
row keeps its "Close" verb (ruling **R-CC119**), and that a tick posted as
the browser posts it -- every amount box the form renders plus the ticked
checkbox, under the ROW fields a bill's tick uses (ruling **R-CC116**) --
settles the row with its payment on the card.
"""

import re
from decimal import Decimal

from werkzeug.datastructures import MultiDict

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.services import cash_ledger, transaction_service
from app.services.transaction_service import settle_transaction
from tests._test_helpers import (
    create_account_of_type,
    generate_row_of,
    make_expense_template,
    typed,
)

_GROCERIES = Decimal("120.00")

#: One ``<input ...>`` tag, to read the controls the panel's form renders.
_INPUT = re.compile(r"<input\b[^>]*>", re.S)


def _attr(tag, name):
    """Return attribute *name* of an ``<input>`` *tag*, or ``None``."""
    match = re.search(rf'\b{name}="([^"]*)"', tag)
    return None if match is None else match.group(1)


def _browser_payload(body, ticked_row_id):
    """Return what a browser submits for the panel with ONE row ticked.

    Every amount box the form renders is submitted whatever is ticked (a
    number input always is), and a checkbox only when checked -- so the
    payload is all the ``type="number"`` inputs plus the ticked row's
    checkbox, as the template emitted them.
    """
    payload = []
    for tag in _INPUT.findall(body):
        name, value = _attr(tag, "name"), _attr(tag, "value")
        if _attr(tag, "type") == "number":
            payload.append((name, value))
        elif _attr(tag, "type") == "checkbox" and value == str(ticked_row_id):
            payload.append((name, value))
    return payload


def _card(seed_user):
    """Create an active Credit Card account whose books open before any day used here."""
    return create_account_of_type(
        seed_user, db.session, "Credit Card", "Rewards Card",
        anchor_balance=Decimal("-500.00"),
    )


def _reopened_card_payment(seed_user, period, card, *, is_envelope=False):
    """Return a Checking row marked paid from *card* and reopened (its payment kept, un-dated)."""
    template = make_expense_template(
        db.session, seed_user,
        amount="300.00" if is_envelope else str(_GROCERIES),
        name="Groceries", category_key="Groceries", is_envelope=is_envelope,
    )
    txn = generate_row_of(template, period)
    db.session.commit()
    settle_transaction(
        txn, submitted=typed(_GROCERIES) if is_envelope else None,
        tender_account_id=card.id,
    )
    db.session.commit()
    transaction_service.apply_requested_status(
        txn, ref_cache.status_id(StatusEnum.PROJECTED),
    )
    db.session.commit()
    return txn


class TestPaidFromThisAccountThroughTheRoute:
    """The card's panel renders the list, and a browser's POST settles from it."""

    def test_the_card_panel_prints_the_section_note_and_label_under_the_row_fields(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """R-CC111's heading, note and row, and R-CC116's fields, as rendered."""
        with app.app_context():
            card = _card(seed_user)
            txn = _reopened_card_payment(seed_user, seed_periods_today[0], card)

            body = auth_client.get(f"/accounts/{card.id}/reconcile").data.decode()

            assert "Paid from this account" in body
            assert (
                "Recorded on this account, then reopened. Ticking one "
                "records it here on your statement date."
            ) in body
            assert "Groceries (Checking&#39;s plan, paid from this card)" in body
            assert f'name="transaction_ids" value="{txn.id}"' in body
            assert f'name="settled_amount-{txn.id}"' in body

    def test_an_empty_envelope_row_reads_CLOSE(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """R-CC119 ("Add 'Close'"): the verb the row's own list prints for the same row."""
        with app.app_context():
            card = _card(seed_user)
            _reopened_card_payment(
                seed_user, seed_periods_today[0], card, is_envelope=True,
            )

            body = auth_client.get(f"/accounts/{card.id}/reconcile").data.decode()

            assert (
                "Close Groceries (Checking&#39;s plan, paid from this card)"
            ) in body

    def test_a_browser_post_of_the_rendered_form_settles_the_row_on_the_card(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """End to end: posted as rendered, the row settles and its card payment is linked."""
        with app.app_context():
            card = _card(seed_user)
            txn = _reopened_card_payment(seed_user, seed_periods_today[0], card)
            txn_id = txn.id
            body = auth_client.get(f"/accounts/{card.id}/reconcile").data.decode()

            response = auth_client.post(
                f"/accounts/{card.id}/reconcile",
                data=MultiDict(_browser_payload(body, txn_id)),
            )

            assert response.status_code == 200
            assert response.headers.get("HX-Trigger") == "balanceChanged"
            db.session.expire_all()
            row = db.session.get(Transaction, txn_id)
            assert row.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert row.reconciled_by_id is None
            (payment,) = row.covering_movements
            assert payment.account_id == card.id
            assert payment.amount == _GROCERIES
            assert payment.settled_on == cash_ledger.governing_anchor(
                card.id,
            ).observed_on
            assert payment.reconciled_by_id == cash_ledger.governing_anchor(
                card.id,
            ).anchor_id
