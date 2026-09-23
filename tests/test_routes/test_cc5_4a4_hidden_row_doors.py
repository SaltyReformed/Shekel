"""Plan step ``credit_card:CC-5-4a-4``, its second review: what a DELETED row still offers.

Ruling **R-CC75** (developer 2026-09-23): deleting a recurring occurrence takes
its payments and purchases off the books, and "A hidden row then never holds
money".  Review 2 found three places that promise or permit more than that:

* **H1** -- the add-purchase door wrote a movement under a hidden row
  (``entry_service.create_entry`` never asked ``is_deleted``), so a stale page
  -- a companion's open grid -- put money back under a row no screen shows,
  which locked its pay period with no row to delete it from;
* **M1** -- ruling **R-CC83**'s "Archiving and then un-archiving that item would
  bring it back, empty." showed on every recurring occurrence, but un-archive
  restores only Projected rows (ruling **R-CC86**: "The un-archive sentence
  appears only on a not-yet-paid occurrence");
* the dialog called a Paid bill's own payment record "the 1 purchase filed
  under it".

And ruling **R-CC84**'s reachable case (a statement-minted envelope whose item
later gained a cadence, then deleted) is pinned here: the tombstone counts as
leaving, so the dialog and the receipt report no created row as kept.
"""

from __future__ import annotations

import re

from app.enums import SettledDayBasisEnum
from app.extensions import db
from app.models.statement_match import StatementMatch, StatementMatchCreation
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import pay_period_gates, statement_match, transaction_service
from app.services.pay_calendar import calendar_for
from app.services.pay_period_locks import PeriodLockReason, classify_schedule_locks
from app.services.settle_day import SettleDay
from app.services.statement_match import NewEnvelope, PurchaseCreation
from app.services.transaction_service import settle_transaction
from app.utils.dates import display_today
# Pylint: ``shekel-private-module-import`` -- the statement-match builders and
# the create door's per-request minted-envelope register are the one way a
# test stages an act that MINTS an envelope as the app does (the convention
# ``test_cc5_4a3_captions`` keeps).
# pylint: disable=shekel-private-module-import
from app.services.statement_match import _create
from tests._test_helpers import (
    generate_row_of,
    make_every_period_rule,
    make_expense_template,
)
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_scope,
    an_answers,
    an_import,
)


def _occurrence(seed_user, period, *, name, amount, is_envelope):
    """A recurring definition's row in *period*, committed."""
    template = make_expense_template(
        db.session, seed_user, amount=amount, name=name,
        category_key="Groceries", is_envelope=is_envelope,
    )
    row = generate_row_of(template, period)
    db.session.commit()
    assert row.recurs is True
    return template, row


def _delete_question(auth_client, row_id):
    """The delete button's ``hx-confirm`` on *row_id*'s full-edit card."""
    html = auth_client.get(f"/transactions/{row_id}/full-edit").data.decode()
    found = re.search(
        r'hx-delete="/transactions/' + str(row_id)
        + r'"[^>]*?hx-confirm="([^"]*)"',
        html, flags=re.S,
    )
    assert found is not None
    return found.group(1)


def _settle(row, day):
    """Mark *row* Paid on *day* through the settle verb (its payment record)."""
    settle_transaction(row, settle_day=SettleDay(
        day=day, basis=SettledDayBasisEnum.ENTERED,
    ))
    db.session.commit()


class TestADeletedRowTakesNoPurchase:
    """H1: the add-purchase door refuses a deleted row, as the settle doors do."""

    def test_a_stale_page_cannot_put_a_purchase_under_a_deleted_occurrence(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Delete the occurrence, then post a purchase to it as a stale grid would.

        Before the guard: 200, the hidden row held the $12.34, its period
        locked ``HOLDS_MOVEMENT`` and Reset was refused, with no screen able
        to reach the row (measured by review 2).
        """
        with app.app_context():
            period = seed_periods_today[3]
            _template, row = _occurrence(
                seed_user, period, name="Groceries", amount="300.00",
                is_envelope=True,
            )
            row_id, user_id = row.id, seed_user["user"].id
            assert auth_client.delete(f"/transactions/{row_id}").status_code == 200

            response = auth_client.post(
                f"/transactions/{row_id}/entries",
                data={"amount": "12.34", "description": "KROGER",
                      "purchased_on": display_today().isoformat()},
            )

            assert response.status_code == 400
            assert "was deleted; a purchase cannot be added" in (
                response.data.decode()
            )
            db.session.expire_all()
            assert db.session.get(Transaction, row_id).is_deleted is True
            assert db.session.query(TransactionEntry).filter_by(
                transaction_id=row_id,
            ).count() == 0
            locks = classify_schedule_locks(
                calendar_for(user_id), as_of=period.start_date,
            )
            assert locks.get(period.id) is not PeriodLockReason.HOLDS_MOVEMENT
            assert pay_period_gates.can_reset_pay_periods(user_id) is True

    def test_the_live_occurrence_still_takes_one(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """The control: the same post on the row before its delete lands."""
        with app.app_context():
            _template, row = _occurrence(
                seed_user, seed_periods_today[3], name="Groceries",
                amount="300.00", is_envelope=True,
            )
            response = auth_client.post(
                f"/transactions/{row.id}/entries",
                data={"amount": "12.34", "description": "KROGER",
                      "purchased_on": display_today().isoformat()},
            )
            assert response.status_code == 200
            assert db.session.query(TransactionEntry).filter_by(
                transaction_id=row.id,
            ).count() == 1


class TestTheDialogSaysWhatUnarchiveDoes:
    """R-CC86: the un-archive sentence only where un-archive would bring the row back."""

    def test_a_paid_occurrence_does_not_promise_to_come_back(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """A Paid Hotel: no un-archive sentence, and un-archive indeed leaves it hidden."""
        with app.app_context():
            period = seed_periods_today[3]
            template, row = _occurrence(
                seed_user, period, name="Hotel", amount="120.00",
                is_envelope=False,
            )
            _settle(row, period.start_date)
            row_id = row.id

            question = _delete_question(auth_client, row_id)

            assert "This occurrence stays deleted" in question
            assert "un-archiving" not in question
            assert auth_client.delete(f"/transactions/{row_id}").status_code == 200
            auth_client.post(f"/templates/{template.id}/archive")
            auth_client.post(f"/templates/{template.id}/unarchive")
            db.session.expire_all()
            assert db.session.get(Transaction, row_id).is_deleted is True

    def test_a_projected_occurrence_promises_it_and_comes_back(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """The control: a not-yet-paid Hotel says it, and un-archive restores it."""
        with app.app_context():
            template, row = _occurrence(
                seed_user, seed_periods_today[3], name="Hotel",
                amount="120.00", is_envelope=False,
            )
            row_id = row.id

            question = _delete_question(auth_client, row_id)

            assert (
                "Archiving and then un-archiving that item would bring it "
                "back, empty." in question
            )
            assert auth_client.delete(f"/transactions/{row_id}").status_code == 200
            auth_client.post(f"/templates/{template.id}/archive")
            auth_client.post(f"/templates/{template.id}/unarchive")
            db.session.expire_all()
            assert db.session.get(Transaction, row_id).is_deleted is False


class TestTheDialogCountsPurchasesOnly:
    """A Paid bill's own payment record is not 'a purchase filed under it'."""

    def test_a_paid_bill_names_no_purchase(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Its one entry is its payment: the dialog names the money, not a purchase."""
        with app.app_context():
            period = seed_periods_today[3]
            _template, row = _occurrence(
                seed_user, period, name="Hotel", amount="120.00",
                is_envelope=False,
            )
            _settle(row, period.start_date)
            assert len(row.entries) == 1 and row.entries[0].covers_settlement

            question = _delete_question(auth_client, row.id)

            assert "purchase" not in question
            assert "Your books record $120.00 as having left your account" in (
                question
            )


class TestATombstoneCountsAsLeaving:
    """R-CC84's reachable case: a statement-minted envelope given a cadence, then deleted."""

    def test_no_created_row_is_reported_kept(self, app, db, seed_user):
        """The act minted the envelope; the owner made it recur; the delete keeps a tombstone.

        Until R-CC84 the delete verb told the act only the rows leaving the
        TABLE, so the tombstone the creation names read as a created row that
        stays (``kept_rows == 1``, measured by review 2).  Now the dialog and
        the receipt agree on 0, and the act and both creation records go.
        """
        with app.app_context():
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="-25.00",
                posted_on=seed_user["bootstrap_period"].start_date,
            )
            created = statement_match.create_purchase_from_line(
                PurchaseCreation(
                    line_id=line.id,
                    new_envelope=NewEnvelope(
                        name="Public Library",
                        category_id=seed_user["categories"]["Groceries"].id,
                    ),
                ),
                a_scope(seed_user),
                _create.MintedEnvelopes.none_yet(),
                an_answers(seed_user),
                applied_by_rule=False,
            )
            db.session.commit()
            envelope = db.session.get(Transaction, created.transaction_id)
            assert db.session.query(StatementMatchCreation).filter_by(
                transaction_id=envelope.id,
            ).count() == 1
            make_every_period_rule(db.session, envelope.template)
            db.session.commit()
            db.session.expire_all()
            envelope = db.session.get(Transaction, created.transaction_id)
            assert envelope.recurs is True

            preview = transaction_service.preview_deletion(envelope)
            outcome = transaction_service.delete_transaction(
                envelope, seed_user["user"].id,
            )
            db.session.commit()
            db.session.expire_all()

            assert preview.soft is True
            assert preview.withdrawn == outcome.withdrawn
            assert outcome.withdrawn.kept_rows == 0
            assert db.session.get(Transaction, created.transaction_id).is_deleted
            assert db.session.get(StatementMatch, created.match_id) is None
            assert db.session.query(StatementMatchCreation).count() == 0
