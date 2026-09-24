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

Review 5 found the sentence never reached the screen: Mark Paid and the
popover's Save answered a deleted row with a bare "Not found" that htmx drops,
and a purchase on a one-off row deleted while it waited was a 500.  Rulings
**R-CC101** ("Show the sentence") and, from Round 14, **R-CC102** (the cell's
red "Deleted"), **R-CC103** (the purchase list's banner alone), **R-CC104**
(a stale tab is told too, and a row it may not name reads the same words
whoever's it was) and **R-CC105** (one sentence for every Save) are pinned in
:class:`TestAStaleTabIsToldTheRowWasDeleted` and
:class:`TestADeleteThatWinsTheRaceIsNamed`; the doors those rulings do not
name keep R-CC89's bare "not found" (:class:`TestTheOtherDoorsStillSayNotFound`).
Finding **CC-376** -- a refused purchase removal was a 500 -- is
:class:`TestARefusedRemovalIsTheListsBanner`.

Review 6 found a row its recurring item's ARCHIVE hid told "was deleted"
(L1), and a deleted transfer shadow named where a live one is not (L2).
Rulings **R-CC107** ("Say archived") and **R-CC108** (the desktop cell's word
"Archived") are :class:`TestAnArchivedItemsRowSaysArchived`; L2 is the deleted
leg in :class:`TestNothingLeaksThroughTheName`.

Review 7 found the archive's read flushing a caller's staged state from the
settle verbs' first refusal (M1, :class:`TestTheHiddenRowsWordsFlushNothing`)
and two of review 6's corrected payback sentences graded by no test (L4,
:class:`TestThePaybackSentencesSayDollars`); review 8 found the same flush
for an EXPIRED row, the third payback sentence's format and the salary case
ungraded (L1, L5, L4).  Every figure is made up.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event

from app import ref_cache
from app.enums import SettledDayBasisEnum, StatusEnum, TxnTypeEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.ref import FilingStatus
from app.models.salary_profile import SalaryProfile
from app.models.statement_match import StatementMatch, StatementMatchCreation
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
# The archive's write is the route module's helper, shared by its two archive
# doors; a thread cannot hold a ROUTE's transaction open, so the other tab
# calls the write the route makes (the race module's ``_archive`` does too).
from app.routes.templates.crud import _soft_delete_projected_rows
from app.services import (
    entry_service,
    pay_period_gates,
    row_write_lock,
    transaction_service,
    transfer_service,
)
from app.services.one_off import OneOffToPlace, place_one_off
from app.services.pay_calendar import calendar_for
from app.services.pay_period_locks import PeriodLockReason, classify_schedule_locks
from app.services.settle_day import SettleDay
from app.services.transaction_service import (
    settle_amount,
    settle_from_entries,
    settle_transaction,
)
from app.utils.dates import display_today
from app.utils.error_fragments import ROW_NO_LONGER_EXISTS_MSG
from app.utils.hidden_row import HiddenRow
from tests._test_helpers import (
    amount_basis_for,
    derived_span,
    generate_row_of,
    make_every_period_rule,
    make_expense_template,
    typed,
)
from tests.test_routes._statement_forms import ReconcileFormReader
from tests.test_routes.test_transfer_leg_cells import (
    _create_savings,
    _create_transfer,
)
# Pylint: ``shekel-private-module-import`` -- the statement-match builders are
# the one way a test stages an act that MINTS an envelope as the app does (the
# convention ``test_cc5_4a3_captions`` keeps).
# pylint: disable=shekel-private-module-import
from tests.test_services.test_statement_match._builders import (
    a_purchase,
    a_purchase_in_a_minted_envelope,
    a_transaction,
)

#: Mark Paid's refusal of a deleted Hotel (the status seam's sentence).
_PAYMENT_REFUSED = (
    "Hotel was deleted: a payment cannot be recorded under it.  "
    "Reload the page."
)

#: The Save door's refusal of a deleted Hotel (ruling R-CC105).
_SAVE_REFUSED = (
    "Hotel was deleted: this change cannot be saved.  Reload the page."
)

#: The purchase door's refusal of a deleted Groceries envelope.
_PURCHASE_REFUSED = (
    "Groceries was deleted: a purchase cannot be recorded under it.  "
    "Reload the page."
)

#: What each door says about the made-up $120.00 Gym envelope's occurrence
#: once Gym is ARCHIVED (ruling R-CC107), keyed by the surface pressed.
_GYM_ARCHIVED = {
    "cell": "Gym was archived: a payment cannot be recorded under it.  "
            "Reload the page.",
    "card": "Gym was archived: a payment cannot be recorded under it.  "
            "Reload the page.",
    "save": "Gym was archived: this change cannot be saved.  Reload the page.",
    "list": "Gym was archived: a purchase cannot be recorded under it.  "
            "Reload the page.",
    "list_tp": "Gym was archived: a purchase cannot be recorded under it.  "
               "Reload the page.",
}


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
        since ruling **R-CC89** the ownership door answers first, a 404 --
        whose body names the row since ruling **R-CC104**, graded in
        :class:`TestAStaleTabIsToldTheRowWasDeleted`.  This test grades the
        status and that nothing was written.
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

            # Named, never numbered (ruling R-CC98, developer 2026-09-23).
            with pytest.raises(ValidationError, match=(
                "Groceries was deleted: a purchase cannot be recorded under it"
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
    """R-CC89 layer 2: every page and button treats a deleted row as not found.

    Each answers 404 and writes nothing.  What the body SAYS at the three
    doors ruling R-CC104 amended -- Mark Paid, the popover's Save, add
    purchase, which name the row -- is :class:`TestAStaleTabIsToldTheRowWasDeleted`'s;
    these grade the status and the state.
    """

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
        seam.  Both answer 404; the PATCH's body names the row (ruling
        R-CC105's Save sentence, graded in
        :class:`TestAStaleTabIsToldTheRowWasDeleted`).
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

            # Named, never numbered (ruling R-CC98, developer 2026-09-23).
            with pytest.raises(ValidationError, match=(
                "Hotel was deleted: a payment cannot be recorded under it"
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
            _line, created = a_purchase_in_a_minted_envelope(seed_user)
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


def _placed_one_off(seed_user, period, *, name, amount, is_envelope):
    """A one-off -- a rule-less definition and its placed row -- committed.

    Its delete removes the row from the table (and the definition with its
    last row, ruling R-BAL27), which is the HARD arm a recurring occurrence's
    soft delete never reaches.
    """
    row = place_one_off(
        OneOffToPlace(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            name=name,
            amount=Decimal(amount),
            category_id=seed_user["categories"]["Groceries"].id,
            is_envelope=is_envelope,
        ),
        derived_span(period),
        scenario_id=seed_user["scenario"].id,
    )
    db.session.commit()
    assert row.recurs is False
    return row


def _in_another_tab(app, act):
    """Run *act* and commit it from a session of its own, now.

    The other tab is another thread with its own app context, so its session
    is its own; ``result`` re-raises anything it raised here, in the test.
    """
    def other_tab():
        with app.app_context():
            try:
                act()
                db.session.commit()
            finally:
                db.session.remove()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(other_tab).result(timeout=8.0)


def _deleted_in_another_tab(app, row_id, owner_id):
    """Commit the app's own Delete of *row_id* from a session of its own, now."""
    _in_another_tab(app, lambda: transaction_service.delete_transaction(
        db.session.get(Transaction, row_id), owner_id,
    ))


def _archived_in_another_tab(app, template_id):
    """Commit the archive of *template_id* from a session of its own, now.

    The archive route's own write -- the definition off, then its empty
    Projected rows hidden (``_soft_delete_projected_rows``, which both archive
    doors share) -- because a thread cannot hold a ROUTE's transaction open.
    """
    def archive():
        template = db.session.get(TransactionTemplate, template_id)
        template.is_active = False
        _soft_delete_projected_rows(template)

    _in_another_tab(app, archive)


def _lands_before_the_lock(monkeypatch, lock_name, row_id, other_tab):
    """Make *other_tab* commit just before the door locks row *row_id*.

    Ruling R-CC96's race, the technique review 5 used: the route's door has
    read the row live, then the other tab's act commits, then the door's row
    lock (``row_write_lock.<lock_name>``) sees the winner.  The act commits
    BEFORE the lock is requested, so the door never waits: under READ
    COMMITTED the outcome is the waiting interleaving's, which the race
    module grades at service level.  Returns a list that holds the row id
    once the act has fired, so a test can assert its race was staged rather
    than skipped.
    """
    real = getattr(row_write_lock, lock_name)
    fired = []

    def act_first(target, *args, **kwargs):
        target_id = target if isinstance(target, int) else target.id
        if not fired and target_id == row_id:
            fired.append(target_id)
            other_tab()
        return real(target, *args, **kwargs)

    monkeypatch.setattr(row_write_lock, lock_name, act_first)
    return fired


def _delete_lands_before_the_lock(monkeypatch, app, lock_name, row_id, owner_id):
    """Make another tab's Delete of *row_id* commit just before the door locks it."""
    return _lands_before_the_lock(
        monkeypatch, lock_name, row_id,
        lambda: _deleted_in_another_tab(app, row_id, owner_id),
    )


def _is_the_deleted_cell(response, sentence, word="Deleted"):
    """Assert *response* is R-CC102's red cell, showing *word* and saying *sentence*.

    *word* is "Deleted", or "Archived" for a row whose recurring item is
    archived (ruling R-CC108).
    """
    body = response.get_data(as_text=True)
    assert response.status_code == 404
    assert response.headers.get("Shekel-Designed-Fragment") == "1"
    assert f'<span aria-hidden="true">{word}</span>' in body
    assert f'title="{sentence}"' in body
    assert f'<span class="visually-hidden">{sentence}</span>' in body
    # Nothing left to press: no opener, no checkmark.
    for control in ("txn-open", "data-txn-id", "paybtn", "hx-post"):
        assert control not in body


def _is_the_banner_card(response, row_id, sentence):
    """Assert *response* is the phone's banner-only card for *row_id* saying *sentence*."""
    body = response.get_data(as_text=True)
    assert response.status_code == 404
    assert response.headers.get("Shekel-Designed-Fragment") == "1"
    assert f'id="card-tp-{row_id}"' in body
    assert f"<span>{sentence}</span>" in body
    assert "mobile-txn-card" not in body


def _is_the_banner_list(response, root_id, sentence):
    """Assert *response* is R-CC103's purchase list: the banner alone, under *root_id*."""
    body = response.get_data(as_text=True)
    assert response.status_code == 404
    assert response.headers.get("Shekel-Designed-Fragment") == "1"
    assert f'id="{root_id}"' in body
    assert f"<span>{sentence}</span>" in body
    # No list and no Add form: nothing under a row that takes no purchase.
    for control in ("/entries", "Remaining", "<form"):
        assert control not in body


def _card_mark_paid(auth_client, row_id):
    """The phone card's Mark Paid on *row_id* (This Period's ``tp`` tab)."""
    return auth_client.post(
        f"/transactions/{row_id}/mark-done",
        data={"render": "mobile_card", "card_prefix": "tp", "can_edit": "1"},
    )


def _a_purchase_form():
    """What the add-purchase form posts: a made-up $12.34 charge today."""
    return {"amount": "12.34", "direction": "charge",
            "description": "Made-up store",
            "purchased_on": display_today().isoformat()}


class TestAStaleTabIsToldTheRowWasDeleted:
    """R-CC104: a click on a row deleted minutes ago shows the sentence, not nothing."""

    def test_mark_paid_on_the_cell_shows_deleted(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """The $120.00 Hotel is deleted, then a stale grid presses its checkmark.

        Before: ``404 Not found`` with no designed header, which htmx drops, so
        the click did nothing visible.
        """
        with app.app_context():
            period = seed_periods_today[3]
            _template, row = _occurrence(
                seed_user, period, name="Hotel", amount="120.00",
                is_envelope=False,
            )
            row_id, user_id = row.id, seed_user["user"].id
            assert auth_client.delete(f"/transactions/{row_id}").status_code == 200

            response = auth_client.post(f"/transactions/{row_id}/mark-done")

            _is_the_deleted_cell(response, _PAYMENT_REFUSED)
            _holds_nothing_and_locks_nothing(row_id, period, user_id)

    def test_mark_paid_on_the_card_shows_the_banner(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """The same press from the phone card: the banner-only card, keyed to the card."""
        with app.app_context():
            period = seed_periods_today[3]
            _template, row = _occurrence(
                seed_user, period, name="Hotel", amount="120.00",
                is_envelope=False,
            )
            row_id, user_id = row.id, seed_user["user"].id
            assert auth_client.delete(f"/transactions/{row_id}").status_code == 200

            response = _card_mark_paid(auth_client, row_id)

            _is_the_banner_card(response, row_id, _PAYMENT_REFUSED)
            _holds_nothing_and_locks_nothing(row_id, period, user_id)

    def test_a_save_with_an_actual_says_the_change_cannot_be_saved(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Review 3's P1 case, the popover's own form, now answered in words (R-CC105)."""
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
            payload["settled_amount"] = "125.00"

            response = auth_client.patch(f"/transactions/{row_id}", data=payload)

            _is_the_deleted_cell(response, _SAVE_REFUSED)
            _holds_nothing_and_locks_nothing(row_id, period, user_id)

    def test_a_save_on_an_unpaid_row_says_the_same(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """An unpaid Hotel's estimate typed from $120.00 to $130.00: one Save sentence."""
        with app.app_context():
            period = seed_periods_today[3]
            _template, row = _occurrence(
                seed_user, period, name="Hotel", amount="120.00",
                is_envelope=False,
            )
            row_id = row.id
            payload = _popover_form(auth_client, row_id)
            assert auth_client.delete(f"/transactions/{row_id}").status_code == 200
            db.session.expire_all()
            version_after_delete = db.session.get(Transaction, row_id).version_id
            payload["estimated_amount"] = "130.00"

            response = auth_client.patch(f"/transactions/{row_id}", data=payload)

            _is_the_deleted_cell(response, _SAVE_REFUSED)
            db.session.expire_all()
            assert db.session.get(Transaction, row_id).version_id == (
                version_after_delete
            )

    @pytest.mark.parametrize("host", ["", "tp"])
    def test_a_purchase_shows_the_banner_alone(
        self, app, db, auth_client, seed_user, seed_periods_today, host,
    ):
        """A $12.34 purchase on the deleted Groceries envelope, popover and phone list."""
        with app.app_context():
            period = seed_periods_today[3]
            _template, row = _occurrence(
                seed_user, period, name="Groceries", amount="300.00",
                is_envelope=True,
            )
            row_id, user_id = row.id, seed_user["user"].id
            assert auth_client.delete(f"/transactions/{row_id}").status_code == 200

            response = auth_client.post(
                f"/transactions/{row_id}/entries?host={host}",
                data=_a_purchase_form(),
            )

            root = f"entry-list-{host}-{row_id}" if host else f"entry-list-{row_id}"
            _is_the_banner_list(response, root, _PURCHASE_REFUSED)
            _holds_nothing_and_locks_nothing(row_id, period, user_id)

    def test_an_erased_one_off_is_not_named(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """A one-off's delete takes its name with it: the nameless sentence, at both doors."""
        with app.app_context():
            period = seed_periods_today[3]
            bill = _placed_one_off(
                seed_user, period, name="Hotel", amount="45.00",
                is_envelope=False,
            )
            envelope = _placed_one_off(
                seed_user, period, name="Groceries", amount="300.00",
                is_envelope=True,
            )
            bill_id, envelope_id = bill.id, envelope.id
            for row_id in (bill_id, envelope_id):
                assert auth_client.delete(
                    f"/transactions/{row_id}",
                ).status_code == 200
            db.session.expire_all()
            assert db.session.get(Transaction, bill_id) is None

            paid = auth_client.post(f"/transactions/{bill_id}/mark-done")
            bought = auth_client.post(
                f"/transactions/{envelope_id}/entries", data=_a_purchase_form(),
            )

            _is_the_deleted_cell(paid, ROW_NO_LONGER_EXISTS_MSG)
            _is_the_banner_list(
                bought, f"entry-list-{envelope_id}", ROW_NO_LONGER_EXISTS_MSG,
            )

    def test_another_users_row_reads_the_same_words(
        self, app, db, auth_client, seed_user, seed_second_user,
        seed_periods_today,
    ):
        """Another user's row, an id that never existed, an erased one-off: one body.

        R-CC104's "Another user's row gets the same words, so nothing leaks":
        the three answers are byte-identical, so the body says nothing about
        which one the id was.
        """
        with app.app_context():
            theirs_template = make_expense_template(
                db.session, seed_second_user, amount="80.00", name="Phone",
                category_key="Groceries",
            )
            theirs = generate_row_of(
                theirs_template, seed_second_user["bootstrap_period"],
            )
            db.session.commit()
            erased = _placed_one_off(
                seed_user, seed_periods_today[3], name="Hotel",
                amount="45.00", is_envelope=False,
            )
            theirs_id, erased_id = theirs.id, erased.id
            assert auth_client.delete(
                f"/transactions/{erased_id}",
            ).status_code == 200
            never = max(theirs_id, erased_id) + 1000

            bodies = {
                name: auth_client.post(f"/transactions/{row_id}/mark-done")
                for name, row_id in (
                    ("theirs", theirs_id), ("never", never),
                    ("erased", erased_id),
                )
            }

            for response in bodies.values():
                _is_the_deleted_cell(response, ROW_NO_LONGER_EXISTS_MSG)
            assert len({r.get_data() for r in bodies.values()}) == 1
            assert "Phone" not in bodies["theirs"].get_data(as_text=True)
            db.session.expire_all()
            assert db.session.get(Transaction, theirs_id).status_id == (
                ref_cache.status_id(StatusEnum.PROJECTED)
            )


class TestADeleteThatWinsTheRaceIsNamed:
    """R-CC101: the delete commits after the door read the row live, before its lock.

    Staged by :func:`_delete_lands_before_the_lock`, which commits the delete
    before the door requests the row's lock, so the door does not wait; the
    waiting interleaving is graded at service level, in
    ``tests/test_services/test_cc5_4a4_row_lock_races.py``.
    """

    def test_mark_paid_on_the_cell(
        self, app, db, auth_client, seed_user, seed_periods_today, monkeypatch,
    ):
        """Review 5's M2 case on the recurring $120.00 Hotel: the cell's red "Deleted"."""
        with app.app_context():
            period = seed_periods_today[3]
            _template, row = _occurrence(
                seed_user, period, name="Hotel", amount="120.00",
                is_envelope=False,
            )
            row_id, user_id = row.id, seed_user["user"].id
            fired = _delete_lands_before_the_lock(
                monkeypatch, app, "lock_row", row_id, user_id,
            )

            response = auth_client.post(f"/transactions/{row_id}/mark-done")

            assert fired == [row_id]
            _is_the_deleted_cell(response, _PAYMENT_REFUSED)
            _holds_nothing_and_locks_nothing(row_id, period, user_id)

    def test_mark_paid_on_the_card(
        self, app, db, auth_client, seed_user, seed_periods_today, monkeypatch,
    ):
        """The same race from the phone card: the banner-only card."""
        with app.app_context():
            period = seed_periods_today[3]
            _template, row = _occurrence(
                seed_user, period, name="Hotel", amount="120.00",
                is_envelope=False,
            )
            row_id, user_id = row.id, seed_user["user"].id
            fired = _delete_lands_before_the_lock(
                monkeypatch, app, "lock_row", row_id, user_id,
            )

            response = _card_mark_paid(auth_client, row_id)

            assert fired == [row_id]
            _is_the_banner_card(response, row_id, _PAYMENT_REFUSED)

    @pytest.mark.parametrize("surface", ("cell", "card"))
    def test_a_replayed_mark_paid_on_a_paid_row(
        self, app, db, auth_client, seed_user, seed_periods_today, monkeypatch,
        surface,
    ):
        """Review 9's L1: the $120.00 Hotel is already Paid, and its Delete wins the replay's race.

        Before: 200 and a live "Hotel ... Paid" chip, because the settle
        verb's identity no-op answered for the Paid row before anything asked
        whether the lock had found it deleted.
        """
        with app.app_context():
            period = seed_periods_today[3]
            _template, row = _occurrence(
                seed_user, period, name="Hotel", amount="120.00",
                is_envelope=False,
            )
            _settle(row, period.start_date)
            row_id, user_id = row.id, seed_user["user"].id
            fired = _delete_lands_before_the_lock(
                monkeypatch, app, "lock_row", row_id, user_id,
            )

            response = _press(auth_client, surface, row_id)

            assert fired == [row_id]
            _is_the_gone_answer(response, surface, row_id, _PAYMENT_REFUSED)
            _holds_nothing_and_locks_nothing(row_id, period, user_id)

    def test_mark_paid_on_an_erased_one_off_names_it(
        self, app, db, auth_client, seed_user, seed_periods_today, monkeypatch,
    ):
        """A made-up $45.00 one-off Hotel bill erased mid-click is still named.

        The row is gone from the table by the time the refusal re-reads it,
        so the name is the one the door read while it was live.
        """
        with app.app_context():
            bill = _placed_one_off(
                seed_user, seed_periods_today[3], name="Hotel",
                amount="45.00", is_envelope=False,
            )
            row_id, user_id = bill.id, seed_user["user"].id
            fired = _delete_lands_before_the_lock(
                monkeypatch, app, "lock_row", row_id, user_id,
            )

            response = auth_client.post(f"/transactions/{row_id}/mark-done")

            assert fired == [row_id]
            _is_the_deleted_cell(response, _PAYMENT_REFUSED)
            db.session.expire_all()
            assert db.session.get(Transaction, row_id) is None

    def test_mark_paid_on_the_card_of_an_erased_one_off_names_it(
        self, app, db, auth_client, seed_user, seed_periods_today, monkeypatch,
    ):
        """The erased one-off's race from the phone card: the banner card, named."""
        with app.app_context():
            bill = _placed_one_off(
                seed_user, seed_periods_today[3], name="Hotel",
                amount="45.00", is_envelope=False,
            )
            row_id, user_id = bill.id, seed_user["user"].id
            fired = _delete_lands_before_the_lock(
                monkeypatch, app, "lock_row", row_id, user_id,
            )

            response = _card_mark_paid(auth_client, row_id)

            assert fired == [row_id]
            _is_the_banner_card(response, row_id, _PAYMENT_REFUSED)

    def test_a_save_with_an_actual(
        self, app, db, auth_client, seed_user, seed_periods_today, monkeypatch,
    ):
        """Review 5's M2 correction case: the Paid Hotel deleted as its Actual is saved."""
        with app.app_context():
            period = seed_periods_today[3]
            _template, row = _occurrence(
                seed_user, period, name="Hotel", amount="120.00",
                is_envelope=False,
            )
            _settle(row, period.start_date)
            row_id, user_id = row.id, seed_user["user"].id
            payload = _popover_form(auth_client, row_id)
            payload["settled_amount"] = "125.00"
            fired = _delete_lands_before_the_lock(
                monkeypatch, app, "lock_row", row_id, user_id,
            )

            response = auth_client.patch(f"/transactions/{row_id}", data=payload)

            assert fired == [row_id]
            _is_the_deleted_cell(response, _SAVE_REFUSED)
            _holds_nothing_and_locks_nothing(row_id, period, user_id)

    def test_a_purchase_on_a_recurring_row(
        self, app, db, auth_client, seed_user, seed_periods_today, monkeypatch,
    ):
        """The purchase door's race, soft: the banner alone where the list stood (R-CC103)."""
        with app.app_context():
            period = seed_periods_today[3]
            _template, row = _occurrence(
                seed_user, period, name="Groceries", amount="300.00",
                is_envelope=True,
            )
            row_id, user_id = row.id, seed_user["user"].id
            fired = _delete_lands_before_the_lock(
                monkeypatch, app, "lock_and_read", row_id, user_id,
            )

            response = auth_client.post(
                f"/transactions/{row_id}/entries", data=_a_purchase_form(),
            )

            assert fired == [row_id]
            _is_the_banner_list(
                response, f"entry-list-{row_id}", _PURCHASE_REFUSED,
            )
            _holds_nothing_and_locks_nothing(row_id, period, user_id)

    def test_a_purchase_on_an_erased_one_off_names_it(
        self, app, db, auth_client, seed_user, seed_periods_today, monkeypatch,
    ):
        """Review 5's M3: a one-off envelope erased mid-purchase was a 500."""
        with app.app_context():
            envelope = _placed_one_off(
                seed_user, seed_periods_today[3], name="Groceries",
                amount="300.00", is_envelope=True,
            )
            row_id, user_id = envelope.id, seed_user["user"].id
            fired = _delete_lands_before_the_lock(
                monkeypatch, app, "lock_and_read", row_id, user_id,
            )

            response = auth_client.post(
                f"/transactions/{row_id}/entries?host=tp",
                data=_a_purchase_form(),
            )

            assert fired == [row_id]
            _is_the_banner_list(
                response, f"entry-list-tp-{row_id}", _PURCHASE_REFUSED,
            )
            db.session.expire_all()
            assert db.session.get(Transaction, row_id) is None
            assert db.session.query(TransactionEntry).filter_by(
                transaction_id=row_id,
            ).count() == 0


#: The five places a gone row is answered (review 6, M2): Mark Paid on the
#: desktop cell and on the phone card, the popover's Save, and add purchase on
#: the popover's list and on the phone's (``?host=tp``).
_SURFACES = ("cell", "card", "save", "list", "list_tp")


def _press(client, surface, row_id):
    """Press *surface*'s door on *row_id* the way its control posts."""
    if surface == "cell":
        return client.post(f"/transactions/{row_id}/mark-done")
    if surface == "card":
        return _card_mark_paid(client, row_id)
    if surface == "save":
        return client.patch(
            f"/transactions/{row_id}", data={"estimated_amount": "1.00"},
        )
    host = "tp" if surface == "list_tp" else ""
    return client.post(
        f"/transactions/{row_id}/entries?host={host}", data=_a_purchase_form(),
    )


def _is_the_gone_answer(response, surface, row_id, sentence, word="Deleted"):
    """Assert *surface*'s gone-row answer saying *sentence*; the desktop cells show *word*."""
    if surface in ("cell", "save"):
        _is_the_deleted_cell(response, sentence, word)
    elif surface == "card":
        _is_the_banner_card(response, row_id, sentence)
    else:
        root = (
            f"entry-list-tp-{row_id}" if surface == "list_tp"
            else f"entry-list-{row_id}"
        )
        _is_the_banner_list(response, root, sentence)


def _is_the_nameless_answer(response, surface, row_id, name):
    """Assert *surface*'s gone-row answer, in the words that name no row, never *name*."""
    assert name not in response.get_data(as_text=True)
    _is_the_gone_answer(response, surface, row_id, ROW_NO_LONGER_EXISTS_MSG)


class TestNothingLeaksThroughTheName:
    """R-CC104: only a row the requester could reach is ever named.

    Review 6's M2: the uniform answer was graded for another user's LIVE row
    alone, so three reorderings of
    ``auth_helpers.get_accessible_transaction_or_deleted`` passed every test
    -- naming a deleted row before the access check, naming a companion's
    hidden deleted row, and calling a live transfer shadow "deleted".  Each
    case below is the one row a reordering would name, at every surface.
    """

    @pytest.mark.parametrize("surface", _SURFACES)
    def test_another_users_deleted_row_is_not_named(
        self, app, db, auth_client, seed_user, seed_second_user, surface,
    ):
        """Another user's made-up $81.00 Gym, deleted by them: its name never reaches you."""
        with app.app_context():
            template = make_expense_template(
                db.session, seed_second_user, amount="81.00", name="Gym",
                category_key="Groceries",
            )
            theirs = generate_row_of(template, seed_second_user["bootstrap_period"])
            db.session.commit()
            transaction_service.delete_transaction(
                theirs, seed_second_user["user"].id,
            )
            db.session.commit()
            theirs_id = theirs.id
            assert db.session.get(Transaction, theirs_id).is_deleted is True

            response = _press(auth_client, surface, theirs_id)

            _is_the_nameless_answer(response, surface, theirs_id, "Gym")

    @pytest.mark.parametrize("surface", ("card", "list", "list_tp"))
    def test_a_companions_hidden_deleted_row_is_not_named(
        self, app, db, companion_client, seed_user, seed_periods_today, surface,
    ):
        """The owner's deleted Spa, never shown to the companion: the companion's controls.

        The phone card and both purchase lists are what a companion's page
        posts; the desktop cell and the Save are the owner's alone.
        """
        with app.app_context():
            template = make_expense_template(
                db.session, seed_user, amount="60.00", name="Spa",
                category_key="Groceries", companion_visible=False,
            )
            row = generate_row_of(template, seed_periods_today[3])
            db.session.commit()
            transaction_service.delete_transaction(row, seed_user["user"].id)
            db.session.commit()
            row_id = row.id

            response = _press(companion_client, surface, row_id)

            _is_the_nameless_answer(response, surface, row_id, "Spa")

    @pytest.mark.parametrize("surface", _SURFACES)
    def test_a_live_transfer_leg_is_never_called_deleted(
        self, app, db, auth_client, seed_user, seed_periods_today, surface,
    ):
        """A made-up transfer's live shadow, asked for by a crafted id: gone, but not "deleted"."""
        with app.app_context():
            savings = _create_savings(seed_user)
            transfer = _create_transfer(seed_user, seed_periods_today[4], savings)
            shadow = db.session.query(Transaction).filter_by(
                transfer_id=transfer.id,
            ).first()
            shadow_id, shadow_name = shadow.id, shadow.name
            assert shadow.is_deleted is False

            response = _press(auth_client, surface, shadow_id)

            assert "was deleted" not in response.get_data(as_text=True)
            _is_the_nameless_answer(response, surface, shadow_id, shadow_name)

    @pytest.mark.parametrize("surface", _SURFACES)
    def test_a_deleted_transfer_leg_is_not_named(
        self, app, db, auth_client, seed_user, seed_periods_today, surface,
    ):
        """Review 6's L2: a made-up transfer soft-deleted, then its shadow's id pressed.

        Before: "Transfer to Savings was deleted: a payment cannot be recorded
        under it." -- the wrong door's sentence for a transfer's leg, and a
        name the live leg above is never told.  The door met the shadow fence
        first, so the answer is the shadow's: the words that name no row.
        """
        with app.app_context():
            savings = _create_savings(seed_user)
            transfer = _create_transfer(seed_user, seed_periods_today[4], savings)
            shadow = db.session.query(Transaction).filter_by(
                transfer_id=transfer.id,
            ).first()
            shadow_id, shadow_name = shadow.id, shadow.name
            transfer_service.delete_transfer(
                transfer.id, seed_user["user"].id, soft=True,
            )
            db.session.commit()
            db.session.expire_all()
            assert db.session.get(Transaction, shadow_id).is_deleted is True

            response = _press(auth_client, surface, shadow_id)

            assert "was deleted" not in response.get_data(as_text=True)
            _is_the_nameless_answer(response, surface, shadow_id, shadow_name)


class TestAnArchivedItemsRowSaysArchived:
    """R-CC107 and R-CC108: a hidden row whose recurring item is archived is told "was archived".

    Review 6's L1, measured: archiving the made-up $120.00 Gym and then
    pressing Mark Paid on its hidden occurrence said "Gym was deleted: a
    payment cannot be recorded under it." -- but the owner archived Gym, and
    Unarchive can bring the row back (ruling R-CC86).  A row deleted while
    its item stays active keeps "was deleted"
    (:class:`TestAStaleTabIsToldTheRowWasDeleted`).  The answer reads the
    item's state, not which act hid the row: a row deleted before or after
    its item's archive says archived too.
    """

    @staticmethod
    def _gym(seed_user, period):
        """The made-up $120.00 Gym envelope and its occurrence in *period*."""
        return _occurrence(
            seed_user, period, name="Gym", amount="120.00", is_envelope=True,
        )

    @pytest.mark.parametrize("surface", _SURFACES)
    def test_a_stale_tab_is_told_the_item_was_archived(
        self, app, db, auth_client, seed_user, seed_periods_today, surface,
    ):
        """Gym is archived, then a stale page presses each door on its hidden occurrence."""
        with app.app_context():
            period = seed_periods_today[3]
            template, row = self._gym(seed_user, period)
            row_id, user_id = row.id, seed_user["user"].id
            assert auth_client.post(
                f"/templates/{template.id}/archive",
            ).status_code == 302
            db.session.expire_all()
            assert db.session.get(Transaction, row_id).is_deleted is True

            response = _press(auth_client, surface, row_id)

            _is_the_gone_answer(
                response, surface, row_id, _GYM_ARCHIVED[surface],
                word="Archived",
            )
            _holds_nothing_and_locks_nothing(row_id, period, user_id)

    def test_a_row_deleted_and_then_archived_says_archived(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """R-CC107's last sentence: one Gym row deleted, and later the whole item archived.

        *"If you deleted one row and later archived the whole item, it says
        'archived', which is also true."*
        """
        with app.app_context():
            period = seed_periods_today[3]
            template, row = self._gym(seed_user, period)
            row_id, user_id = row.id, seed_user["user"].id
            assert auth_client.delete(f"/transactions/{row_id}").status_code == 200
            assert auth_client.post(
                f"/templates/{template.id}/archive",
            ).status_code == 302

            response = auth_client.post(f"/transactions/{row_id}/mark-done")

            _is_the_deleted_cell(response, _GYM_ARCHIVED["cell"], "Archived")
            _holds_nothing_and_locks_nothing(row_id, period, user_id)

    def test_a_row_deleted_after_the_archive_says_archived(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Gym archived first, and its row deleted by the owner afterwards.

        The archive keeps a row holding a purchase (ruling R-CC63), so the
        made-up $12.34 purchase keeps Gym's occurrence visible through it;
        the owner's own delete is what hides the row.  Gym is archived, so
        the sentence says archived (review 7, L2: the rule is the item's
        state; :meth:`test_a_deactivated_salary_profiles_row_says_archived`
        is the same rule reached through the salary door).  A PIN: the
        delete empties the row, so the end state is the deleted-then-archived
        test's, and only an implementation that read history would separate
        the two.
        """
        with app.app_context():
            period = seed_periods_today[3]
            template, row = self._gym(seed_user, period)
            row_id, user_id = row.id, seed_user["user"].id
            assert auth_client.post(
                f"/transactions/{row_id}/entries", data=_a_purchase_form(),
            ).status_code == 200
            assert auth_client.post(
                f"/templates/{template.id}/archive",
            ).status_code == 302
            db.session.expire_all()
            assert db.session.get(Transaction, row_id).is_deleted is False
            assert auth_client.delete(f"/transactions/{row_id}").status_code == 200

            response = auth_client.post(f"/transactions/{row_id}/mark-done")

            _is_the_deleted_cell(response, _GYM_ARCHIVED["cell"], "Archived")
            _holds_nothing_and_locks_nothing(row_id, period, user_id)

    def test_a_deactivated_salary_profiles_row_says_archived(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """A made-up salary profile deactivated, then its Day Job row deleted.

        Deactivating a profile archives its item
        (``routes/salary/profiles.delete_profile``) and hides no row, so the
        owner's delete is what hides this one; the item is archived, so the
        sentence says archived (review 8, L4: measured by probe, now read by
        a test).
        """
        with app.app_context():
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=next(iter(seed_user["categories"].values())).id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
                name="Day Job",
                default_amount=Decimal("11.11"),
            )
            db.session.add(template)
            db.session.flush()
            make_every_period_rule(db.session, template)
            profile = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=db.session.query(FilingStatus).first().id,
                template_id=template.id,
                name="Made-up salary",
                annual_salary=Decimal("52000.00"),
                state_code="NC",
                is_active=True,
            )
            db.session.add(profile)
            db.session.flush()
            period = seed_periods_today[3]
            row = generate_row_of(template, period)
            db.session.commit()
            row_id, template_id = row.id, template.id
            user_id = seed_user["user"].id
            assert auth_client.post(
                f"/salary/{profile.id}/delete",
            ).status_code == 302
            db.session.expire_all()
            assert db.session.get(TransactionTemplate, template_id).is_active is False
            assert db.session.get(Transaction, row_id).is_deleted is False
            assert auth_client.delete(f"/transactions/{row_id}").status_code == 200

            response = auth_client.post(f"/transactions/{row_id}/mark-done")

            _is_the_deleted_cell(
                response,
                "Day Job was archived: a payment cannot be recorded under "
                "it.  Reload the page.",
                "Archived",
            )
            _holds_nothing_and_locks_nothing(row_id, period, user_id)

    def test_mark_paid_losing_to_the_archive_says_archived(
        self, app, db, auth_client, seed_user, seed_periods_today, monkeypatch,
    ):
        """The archive commits after the door read Gym's row live, before its lock.

        The refusal's re-read after its rollback is what names the row here
        (``HiddenRow.of_reread``), so this grades that arm's archive reading.
        """
        with app.app_context():
            period = seed_periods_today[3]
            template, row = self._gym(seed_user, period)
            row_id, user_id = row.id, seed_user["user"].id
            fired = _lands_before_the_lock(
                monkeypatch, "lock_row", row_id,
                lambda: _archived_in_another_tab(app, template.id),
            )

            response = auth_client.post(f"/transactions/{row_id}/mark-done")

            assert fired == [row_id]
            _is_the_deleted_cell(response, _GYM_ARCHIVED["cell"], "Archived")
            _holds_nothing_and_locks_nothing(row_id, period, user_id)

    def test_a_purchase_losing_to_the_archive_says_archived(
        self, app, db, auth_client, seed_user, seed_periods_today, monkeypatch,
    ):
        """The same race at add purchase: the banner alone, saying archived."""
        with app.app_context():
            period = seed_periods_today[3]
            template, row = self._gym(seed_user, period)
            row_id, user_id = row.id, seed_user["user"].id
            fired = _lands_before_the_lock(
                monkeypatch, "lock_and_read", row_id,
                lambda: _archived_in_another_tab(app, template.id),
            )

            response = auth_client.post(
                f"/transactions/{row_id}/entries", data=_a_purchase_form(),
            )

            assert fired == [row_id]
            _is_the_banner_list(
                response, f"entry-list-{row_id}", _GYM_ARCHIVED["list"],
            )
            _holds_nothing_and_locks_nothing(row_id, period, user_id)

    def test_the_settle_verb_says_archived(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """A service caller that skipped the door settles Gym's hidden occurrence.

        The words a SERVICE caller reads (``reject_unsettleable``); the
        routes' own answer is the door's, graded above.
        """
        with app.app_context():
            period = seed_periods_today[3]
            template, row = self._gym(seed_user, period)
            row_id, user_id = row.id, seed_user["user"].id
            assert auth_client.post(
                f"/templates/{template.id}/archive",
            ).status_code == 302
            db.session.expire_all()

            with pytest.raises(ValidationError, match=(
                "Gym was archived: a payment cannot be recorded under it"
            )):
                settle_transaction(db.session.get(Transaction, row_id))
            db.session.rollback()
            _holds_nothing_and_locks_nothing(row_id, period, user_id)

    def test_the_seam_says_archived(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """The correction arm on a Paid Hotel deleted, then its item archived.

        :class:`TestTheSeamRefusesADeletedRow`'s case with the archive after
        the delete: the status seam's own refusal says "was archived".
        """
        with app.app_context():
            period = seed_periods_today[3]
            template, row = _occurrence(
                seed_user, period, name="Hotel", amount="120.00",
                is_envelope=False,
            )
            _settle(row, period.start_date)
            row_id, user_id = row.id, seed_user["user"].id
            paid = row.status_id
            assert auth_client.delete(f"/transactions/{row_id}").status_code == 200
            assert auth_client.post(
                f"/templates/{template.id}/archive",
            ).status_code == 302
            db.session.expire_all()

            with pytest.raises(ValidationError, match=(
                "Hotel was archived: a payment cannot be recorded under it"
            )):
                transaction_service.apply_requested_status(
                    db.session.get(Transaction, row_id), paid,
                    submitted=typed(Decimal("125.00")),
                )
            db.session.rollback()
            _holds_nothing_and_locks_nothing(row_id, period, user_id)


def _settle_by(verb, row, basis):
    """Call the settle verb named *verb* on *row*, the way a service caller does."""
    if verb == "settle_transaction":
        return settle_transaction(row)
    if verb == "settle_from_entries":
        return settle_from_entries(row)
    return settle_amount(row, basis)


class _FlushRecorder:
    """Record what each flush of the test's session would write, while open."""

    def __init__(self):
        self.flushed = []
        self._session = None

    def _record(self, flushing, _context, _instances):
        self.flushed.append(sorted(type(o).__name__ for o in flushing.dirty))

    def __enter__(self):
        self._session = db.session()
        event.listen(self._session, "before_flush", self._record)
        return self

    def __exit__(self, *_exc):
        event.remove(self._session, "before_flush", self._record)


class TestTheHiddenRowsWordsFlushNothing:
    """Reviews 7 and 8: a settle verb refusing a deleted row writes no staged state.

    The three settle verbs ask ``reject_unsettleable`` at their first check,
    before any lock, so a call refused there leaves a caller's staged state
    unwritten.  Its deleted-row sentence reads whether the row's item is
    archived (``HiddenRow.of``, ruling R-CC107).  Review 7 measured all three
    verbs flushing a staged change to another row before raising, through
    that read's autoflush (checkpoint 11); review 8 measured the same for a
    row the caller's commit had EXPIRED, whose columns refresh by a statement
    of their own (checkpoint 12).
    """

    @staticmethod
    def _a_deleted_hotel_and_a_staged_note(auth_client, seed_user, period):
        """The made-up $120.00 Hotel loaded deleted; a note staged on the $80.00 Phone.

        Returns ``(hotel, phone, basis)``: ``settle_amount``'s basis is built
        before anything is staged, because building it reads the database
        and that read may flush.
        """
        _template, hotel = _occurrence(
            seed_user, period, name="Hotel", amount="120.00",
            is_envelope=False,
        )
        _template, phone = _occurrence(
            seed_user, period, name="Phone", amount="80.00",
            is_envelope=False,
        )
        hotel_id, phone_id = hotel.id, phone.id
        assert auth_client.delete(f"/transactions/{hotel_id}").status_code == 200
        db.session.expire_all()
        hotel = db.session.get(Transaction, hotel_id)
        assert hotel.is_deleted is True
        basis = amount_basis_for(hotel)
        phone = db.session.get(Transaction, phone_id)
        phone.notes = "Made-up note, not saved yet"
        return hotel, phone, basis

    @pytest.mark.parametrize("expired", (False, True))
    @pytest.mark.parametrize(
        "verb", ("settle_transaction", "settle_from_entries", "settle_amount"),
    )
    def test_the_refusal_flushes_no_staged_change(
        self, app, db, auth_client, seed_user, seed_periods_today, verb,
        expired,
    ):
        """Each verb on the deleted Hotel, loaded or expired, beside the staged note."""
        with app.app_context():
            hotel, phone, basis = self._a_deleted_hotel_and_a_staged_note(
                auth_client, seed_user, seed_periods_today[3],
            )
            if expired:
                db.session.expire(hotel)

            with _FlushRecorder() as recorder:
                with pytest.raises(ValidationError, match=(
                    "Hotel was deleted: a payment cannot be recorded under it"
                )):
                    _settle_by(verb, hotel, basis)

            assert recorder.flushed == []
            assert phone in db.session.dirty
            db.session.rollback()

    def test_the_hidden_rows_read_of_an_expired_row_flushes_nothing(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """``HiddenRow.of`` asked directly of the expired Hotel (review 8's L1).

        Its own guard covers the row's columns too, which the verbs' first
        check cannot grade: that check has already refreshed the row by the
        time it asks.
        """
        with app.app_context():
            hotel, phone, _basis = self._a_deleted_hotel_and_a_staged_note(
                auth_client, seed_user, seed_periods_today[3],
            )
            db.session.expire(hotel)

            with _FlushRecorder() as recorder:
                answer = HiddenRow.of(hotel)

            assert answer == HiddenRow("Hotel")
            assert recorder.flushed == []
            assert phone in db.session.dirty
            db.session.rollback()


class TestTheOtherDoorsStillSayNotFound:
    """R-CC104 names three doors; every other door keeps R-CC89's bare answer."""

    def test_mark_credit_racing_a_delete_is_not_found(
        self, app, db, auth_client, seed_user, seed_periods_today, monkeypatch,
    ):
        """Mark Credit on a $80.00 Phone deleted while it waited: bare "Not found".

        Pins the blueprint's default answer for a gone row, which the helpers
        that used to return it now hand up to.
        """
        with app.app_context():
            _template, row = _occurrence(
                seed_user, seed_periods_today[3], name="Phone",
                amount="80.00", is_envelope=False,
            )
            row_id, user_id = row.id, seed_user["user"].id
            fired = _delete_lands_before_the_lock(
                monkeypatch, app, "lock_and_read", row_id, user_id,
            )

            response = auth_client.post(f"/transactions/{row_id}/mark-credit")

            assert fired == [row_id]
            assert (response.status_code, response.get_data()) == (
                404, b"Not found",
            )
            assert "Shekel-Designed-Fragment" not in response.headers
            assert db.session.query(Transaction).filter_by(
                credit_payback_for_id=row_id,
            ).count() == 0

    def test_a_live_row_the_door_refuses_is_never_called_deleted(
        self, app, db, companion_client, seed_user, seed_periods_today,
    ):
        """A companion's malformed Mark Paid asking for the owner-only desktop cell.

        The refusal's re-fetch goes through the owner-only door, which answers
        a companion ``None`` for a row that is live and undeleted.  That is
        the door refusing the SURFACE, not the row going: the answer stays the
        bare "Not found" it was, and never "Hotel was deleted".
        """
        with app.app_context():
            template = make_expense_template(
                db.session, seed_user, amount="120.00", name="Hotel",
                category_key="Groceries", companion_visible=True,
            )
            row = generate_row_of(template, seed_periods_today[3])
            db.session.commit()

            response = companion_client.post(
                f"/transactions/{row.id}/mark-done",
                data={"settled_amount": "not-a-number"},
            )

            assert (response.status_code, response.get_data()) == (
                404, b"Not found",
            )

    def test_a_stale_cancel_is_not_found(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Cancel on a deleted row: the ownership door's bare "Not found", unchanged.

        A PIN, not a grader (review 6, L6): Cancel's own door
        (``_get_owned_transaction``) answers the deleted row before any code
        this step's gone-row answers touched runs, so it passed before them
        too.  It holds Cancel to R-CC89's bare answer against a later change
        that would name the row at a door ruling R-CC104 does not.
        """
        with app.app_context():
            _template, row = _occurrence(
                seed_user, seed_periods_today[3], name="Phone",
                amount="80.00", is_envelope=False,
            )
            row_id = row.id
            assert auth_client.delete(f"/transactions/{row_id}").status_code == 200

            response = auth_client.post(f"/transactions/{row_id}/cancel")

            assert (response.status_code, response.get_data()) == (
                404, b"Not found",
            )


class TestARefusedRemovalIsTheListsBanner:
    """CC-376: the purchase delete's refusal is the list's banner, not a 500."""

    def test_a_purchase_under_a_fixed_figure_close_stays_with_a_sentence(
        self, app, db, auth_client, seed_user,
    ):
        """A made-up Done $300.00 Groceries close holding one $12.34 purchase.

        The close records a fixed figure, so removing the purchase is refused
        (``entry_service.removal_refusal``).  Before: that ``ValidationError``
        had no arm in the route, a 500.
        """
        with app.app_context():
            start = seed_user["bootstrap_period"].start_date
            envelope = a_transaction(
                seed_user, name="Groceries", amount="300.00",
                is_envelope=True, status=StatusEnum.DONE,
                settled_on=start + timedelta(days=1),
            )
            purchase = a_purchase(
                seed_user, envelope, amount="12.34",
                description="Made-up store", purchased_on=start,
                settled_on=start + timedelta(days=1),
            )
            db.session.commit()
            envelope_id, purchase_id = envelope.id, purchase.id

            response = auth_client.delete(
                f"/transactions/{envelope_id}/entries/{purchase_id}",
            )

            body = response.get_data(as_text=True)
            assert response.status_code == 400
            assert response.headers.get("Shekel-Designed-Fragment") == "1"
            assert (
                "Groceries has settled and records a fixed figure, so a "
                "purchase cannot be removed from it"
            ) in body
            assert f'id="entry-list-{envelope_id}"' in body
            db.session.expire_all()
            assert db.session.get(TransactionEntry, purchase_id) is not None

    def test_a_paid_payback_is_named_with_its_dollars(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Review 6's M3, an ordinary flow: pay the card, then remove the card purchase.

        A made-up $50.00 card purchase on a $120.00 Groceries envelope creates
        its payback, the payback is marked Paid, and the purchase's delete is
        refused.  Before: "Payback 2 has settled at 50.00 ..." -- an id, and a
        figure with no dollar sign (ruling R-CC98).
        """
        with app.app_context():
            _template, envelope = _occurrence(
                seed_user, seed_periods_today[3], name="Groceries",
                amount="120.00", is_envelope=True,
            )
            envelope_id = envelope.id
            purchase = entry_service.create_entry(
                envelope_id, seed_user["user"].id, entry_service.EntryDetails(
                    figure=typed(Decimal("50.00")), description="Made-up store",
                    purchased_on=display_today(), is_credit=True,
                ),
            )
            db.session.commit()
            purchase_id = purchase.id
            payback = db.session.query(Transaction).filter_by(
                credit_payback_for_id=envelope_id,
            ).one()
            payback_id = payback.id
            settle_transaction(payback)
            db.session.commit()

            response = auth_client.delete(
                f"/transactions/{envelope_id}/entries/{purchase_id}",
            )

            body = response.get_data(as_text=True)
            assert response.status_code == 400
            assert response.headers.get("Shekel-Designed-Fragment") == "1"
            assert "CC Payback: Groceries" in body
            assert "has settled at $50.00, so it cannot be removed" in body
            assert f"Payback {payback_id}" not in body
            db.session.expire_all()
            assert db.session.get(TransactionEntry, purchase_id) is not None

    def test_a_payment_record_is_named_by_its_row(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """A Paid $120.00 Hotel's own payment record, asked to go by a crafted id.

        The list never draws the record, so only a crafted request reaches
        this.  Before: "Entry 1 is the payment record of transaction 1 ...".
        """
        with app.app_context():
            period = seed_periods_today[3]
            _template, bill = _occurrence(
                seed_user, period, name="Hotel", amount="120.00",
                is_envelope=False,
            )
            _settle(bill, period.start_date)
            bill_id = bill.id
            record_id = db.session.query(TransactionEntry).filter_by(
                transaction_id=bill_id,
            ).one().id

            response = auth_client.delete(
                f"/transactions/{bill_id}/entries/{record_id}",
            )

            body = response.get_data(as_text=True)
            assert response.status_code == 400
            assert response.headers.get("Shekel-Designed-Fragment") == "1"
            assert "This is the payment record of Hotel" in body
            assert f"Entry {record_id} is" not in body
            assert "record of transaction" not in body
            db.session.expire_all()
            assert db.session.get(TransactionEntry, record_id) is not None


def _card_purchase(envelope_id, user_id, amount, description):
    """Record a made-up card purchase (a negative *amount* is a refund) on *envelope_id*."""
    return entry_service.create_entry(
        envelope_id, user_id, entry_service.EntryDetails(
            figure=typed(Decimal(amount)), description=description,
            purchased_on=display_today(), is_credit=True,
        ),
    )


class TestThePaybackSentencesSayDollars:
    """Review 7's L4: review 6's M3 sentences name the payback and its dollars.

    Review 6 made two more of the card payback's refusals name the row and
    print its figures in dollars (ruling R-CC98); no test read either
    sentence past "has settled at", so reverting one to "Payback 2 has
    settled at 1200.00" or dropping a "$" passed the suite (review 7's R1
    and R2).  The figures carry a thousands separator, so the format is
    graded too.
    """

    def test_a_later_card_purchase_names_the_payback_in_dollars(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The $1,200.00 card purchase's payback is Paid, then a $50.00 one is added."""
        with app.app_context():
            _template, envelope = _occurrence(
                seed_user, seed_periods_today[3], name="Groceries",
                amount="1500.00", is_envelope=True,
            )
            envelope_id, user_id = envelope.id, seed_user["user"].id
            _card_purchase(envelope_id, user_id, "1200.00", "Made-up store")
            db.session.commit()
            payback = db.session.query(Transaction).filter_by(
                credit_payback_for_id=envelope_id,
            ).one()
            payback_id = payback.id
            settle_transaction(payback)
            db.session.commit()

            with pytest.raises(ValidationError) as refused:
                _card_purchase(envelope_id, user_id, "50.00", "Made-up store")
            db.session.rollback()

            assert str(refused.value) == (
                "The payback 'CC Payback: Groceries' has settled at "
                "$1,200.00, so it cannot be re-derived to $1,250.00: a "
                "settled row records what MOVED. Set the payback back to "
                "Projected, then record this purchase -- the figure it "
                "recorded is kept, and marking it paid again books the new "
                "total."
            )
            assert f"Payback {payback_id}" not in str(refused.value)

    def test_a_refund_past_the_card_purchases_says_its_dollars(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A $100.00 card purchase, then a $1,334.56 card refund: $1,234.56 over."""
        with app.app_context():
            _template, envelope = _occurrence(
                seed_user, seed_periods_today[3], name="Groceries",
                amount="300.00", is_envelope=True,
            )
            envelope_id, user_id = envelope.id, seed_user["user"].id
            _card_purchase(envelope_id, user_id, "100.00", "Made-up store")
            db.session.commit()

            with pytest.raises(ValidationError) as refused:
                _card_purchase(
                    envelope_id, user_id, "-1334.56", "Made-up refund",
                )
            db.session.rollback()

            assert str(refused.value).startswith(
                "The card refunds on 'Groceries' now exceed its card "
                "purchases by $1,234.56, which would mean the card owes YOU "
                "rather than the other way round."
            )

    def test_a_removed_card_purchase_names_the_payback_in_dollars(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """The $1,200.00 card purchase's payback is Paid, then the purchase is removed.

        Review 8's L5: the third sentence, graded only at $50.00 (by
        :meth:`TestARefusedRemovalIsTheListsBanner.test_a_paid_payback_is_named_with_its_dollars`),
        so a lost thousands separator passed.
        """
        with app.app_context():
            _template, envelope = _occurrence(
                seed_user, seed_periods_today[3], name="Groceries",
                amount="1500.00", is_envelope=True,
            )
            envelope_id, user_id = envelope.id, seed_user["user"].id
            purchase_id = _card_purchase(
                envelope_id, user_id, "1200.00", "Made-up store",
            ).id
            db.session.commit()
            settle_transaction(db.session.query(Transaction).filter_by(
                credit_payback_for_id=envelope_id,
            ).one())
            db.session.commit()

            response = auth_client.delete(
                f"/transactions/{envelope_id}/entries/{purchase_id}",
            )

            assert response.status_code == 400
            assert "has settled at $1,200.00, so it cannot be removed" in (
                response.get_data(as_text=True)
            )
            db.session.expire_all()
            assert db.session.get(TransactionEntry, purchase_id) is not None
