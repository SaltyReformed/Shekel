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

Review 3 found H1's second door: the popover's Actual correction wrote a $125.00
payment record under a deleted Paid Hotel from a stale second tab, and a stale
Mark Credit on a deleted occurrence created a live card payback.  Ruling
**R-CC89** (developer 2026-09-23, "All three layers"): the database refuses a
payment or purchase written under a deleted row
(``tests/test_models/test_cc5_4a4_deleted_row_takes_no_money.py``); every page
and button treats a deleted row as not found; and the two code paths that write
money under a row refuse first with a sentence.  The last two are pinned here,
each against its live-row control.
"""

from __future__ import annotations

import re
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import SettledDayBasisEnum, StatusEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.statement_match import StatementMatch, StatementMatchCreation
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import (
    entry_service,
    pay_period_gates,
    statement_match,
    transaction_service,
)
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
    typed,
)
from tests.test_routes._statement_forms import ReconcileFormReader
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


def _popover_form(auth_client, row_id):
    """The fields the full-edit popover's PATCH form submits for *row_id*."""
    html = auth_client.get(f"/transactions/{row_id}/full-edit").data.decode()
    start = html.index("<form hx-patch")
    reader = ReconcileFormReader()
    reader.feed(html[start:html.index("</form>", start)])
    return dict(reader.fields)


def _holds_nothing_and_locks_nothing(row_id, period, user_id):
    """Assert R-CC75's state: *row_id* hidden, empty, its period free, Reset open."""
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


class TestADeletedRowTakesNoPurchase:
    """H1: the add-purchase door refuses a deleted row, as the settle doors do."""

    def test_a_stale_page_cannot_put_a_purchase_under_a_deleted_occurrence(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Delete the occurrence, then post a purchase to it as a stale grid would.

        Before review 2's guard: 200, the hidden row held the $12.34, its
        period locked ``HOLDS_MOVEMENT`` and Reset was refused, with no screen
        able to reach the row.  Review 2's guard answered 400 with a sentence;
        since ruling **R-CC89** the ownership door answers first, "not found",
        exactly as for another user's row.
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

            assert response.status_code == 404
            _holds_nothing_and_locks_nothing(row_id, period, user_id)

    def test_the_door_itself_refuses_with_its_sentence(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """R-CC89 layer 3: ``create_entry`` refuses a deleted row in words.

        A route no longer reaches it with one (the test above); a service
        caller that skipped the ownership door does, and meets the sentence
        before the database's refusal.
        """
        with app.app_context():
            period = seed_periods_today[3]
            _template, row = _occurrence(
                seed_user, period, name="Groceries", amount="300.00",
                is_envelope=True,
            )
            row_id, user_id = row.id, seed_user["user"].id
            assert auth_client.delete(f"/transactions/{row_id}").status_code == 200

            with pytest.raises(ValidationError, match=(
                f"Transaction {row_id} was deleted; a purchase cannot be "
                "added to it"
            )):
                entry_service.create_entry(
                    row_id, user_id,
                    entry_service.EntryDetails(
                        figure=typed(Decimal("12.34")),
                        description="KROGER",
                        purchased_on=display_today(),
                    ),
                )
            db.session.rollback()
            _holds_nothing_and_locks_nothing(row_id, period, user_id)

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


class TestADeletedRowIsNotFound:
    """R-CC89 layer 2: every page and button treats a deleted row as not found."""

    def test_a_stale_popovers_actual_correction_is_not_found(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Review 3's P1: the Paid Hotel is deleted, then a second tab saves Actual $125.00.

        Before: the second tab's ``GET .../full-edit`` answered 200 with a
        fresh form, and its PATCH answered 200 and wrote a dated $125.00
        payment record under the hidden row -- its period locked
        ``HOLDS_MOVEMENT`` and Reset was refused for good.  The form is the
        popover's own (read while the row was live), carrying the version the
        delete left, so only the ownership door stands between it and the
        seam.
        """
        with app.app_context():
            period = seed_periods_today[3]
            _template, row = _occurrence(
                seed_user, period, name="Hotel", amount="120.00",
                is_envelope=False,
            )
            _settle(row, period.start_date)
            row_id, user_id = row.id, seed_user["user"].id
            payload = _popover_form(auth_client, row_id)
            assert auth_client.delete(f"/transactions/{row_id}").status_code == 200
            db.session.expire_all()
            payload["version_id"] = str(
                db.session.get(Transaction, row_id).version_id,
            )
            payload["settled_amount"] = "125.00"

            opened = auth_client.get(f"/transactions/{row_id}/full-edit")
            saved = auth_client.patch(f"/transactions/{row_id}", data=payload)

            assert (opened.status_code, saved.status_code) == (404, 404)
            _holds_nothing_and_locks_nothing(row_id, period, user_id)

    def test_the_live_rows_correction_still_saves(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """The control: the same form on the live Paid Hotel records $125.00."""
        with app.app_context():
            period = seed_periods_today[3]
            _template, row = _occurrence(
                seed_user, period, name="Hotel", amount="120.00",
                is_envelope=False,
            )
            _settle(row, period.start_date)
            payload = _popover_form(auth_client, row.id)
            payload["settled_amount"] = "125.00"

            saved = auth_client.patch(f"/transactions/{row.id}", data=payload)

            assert saved.status_code == 200
            db.session.expire_all()
            assert [
                entry.amount for entry in db.session.query(TransactionEntry)
                .filter_by(transaction_id=row.id)
            ] == [Decimal("125.00")]

    def test_a_stale_mark_credit_is_not_found(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Review 3's P4: a stale Mark Credit on a deleted $80.00 occurrence.

        Before: 200, the hidden row turned Credit and a LIVE $80.00 card
        payback appeared in the next period, an expense the owner would see
        and could never trace to its source.
        """
        with app.app_context():
            period = seed_periods_today[3]
            _template, row = _occurrence(
                seed_user, period, name="Phone", amount="80.00",
                is_envelope=False,
            )
            row_id = row.id
            projected = ref_cache.status_id(StatusEnum.PROJECTED)
            assert auth_client.delete(f"/transactions/{row_id}").status_code == 200

            response = auth_client.post(f"/transactions/{row_id}/mark-credit")

            assert response.status_code == 404
            db.session.expire_all()
            assert db.session.get(Transaction, row_id).status_id == projected
            assert db.session.query(Transaction).filter_by(
                credit_payback_for_id=row_id,
            ).count() == 0

    def test_the_live_rows_mark_credit_still_creates_its_payback(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """The control: the same press on the live $80.00 row creates one payback."""
        with app.app_context():
            _template, row = _occurrence(
                seed_user, seed_periods_today[3], name="Phone",
                amount="80.00", is_envelope=False,
            )

            response = auth_client.post(f"/transactions/{row.id}/mark-credit")

            assert response.status_code == 200
            assert db.session.query(Transaction).filter_by(
                credit_payback_for_id=row.id,
            ).count() == 1


class TestTheSeamRefusesADeletedRow:
    """R-CC89 layer 3: the status seam refuses a payment record on a deleted row, in words."""

    def test_the_correction_arm_refuses_before_writing(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """The door review 3 walked: the correction arm reached the covering writer.

        A route no longer reaches it with a deleted row (the class above); a
        service caller does, and the seam refuses ahead of any mutation --
        the row keeps its Paid status and holds nothing.
        """
        with app.app_context():
            period = seed_periods_today[3]
            _template, row = _occurrence(
                seed_user, period, name="Hotel", amount="120.00",
                is_envelope=False,
            )
            _settle(row, period.start_date)
            row_id, user_id = row.id, seed_user["user"].id
            paid = row.status_id
            assert auth_client.delete(f"/transactions/{row_id}").status_code == 200
            db.session.expire_all()
            deleted = db.session.get(Transaction, row_id)

            with pytest.raises(ValidationError, match=(
                f"Transaction {row_id} was deleted; a payment cannot be "
                "recorded on it"
            )):
                transaction_service.apply_requested_status(
                    deleted, paid, submitted=typed(Decimal("125.00")),
                )
            db.session.rollback()
            assert db.session.get(Transaction, row_id).status_id == paid
            _holds_nothing_and_locks_nothing(row_id, period, user_id)

    def test_the_live_rows_correction_is_recorded(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The control: the same call on the live Paid row records $125.00."""
        with app.app_context():
            period = seed_periods_today[3]
            _template, row = _occurrence(
                seed_user, period, name="Hotel", amount="120.00",
                is_envelope=False,
            )
            _settle(row, period.start_date)

            transaction_service.apply_requested_status(
                row, row.status_id, submitted=typed(Decimal("125.00")),
            )
            db.session.commit()

            db.session.expire_all()
            assert [
                entry.amount for entry in db.session.query(TransactionEntry)
                .filter_by(transaction_id=row.id)
            ] == [Decimal("125.00")]


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
            archived = auth_client.post(f"/templates/{template.id}/archive")
            restored = auth_client.post(f"/templates/{template.id}/unarchive")
            # Both doors ran (review 3's L4): a refused archive or un-archive
            # would leave the row hidden for the wrong reason.
            assert (archived.status_code, restored.status_code) == (302, 302)
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
            archived = auth_client.post(f"/templates/{template.id}/archive")
            restored = auth_client.post(f"/templates/{template.id}/unarchive")
            assert (archived.status_code, restored.status_code) == (302, 302)
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
