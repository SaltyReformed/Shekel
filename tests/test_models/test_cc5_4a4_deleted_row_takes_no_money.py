"""Plan step ``credit_card:CC-5-4a-4``: the database keeps money out of a deleted row.

Ruling **R-CC89** (developer 2026-09-23), its first layer: *"The database refuses
any payment or purchase written under a deleted row, so no door, now or later,
can do it."*, and **R-CC92** extending it the same day ("Refuse both ways"): *"The
database also refuses hiding a row that still holds a payment or purchase,
except a transfer's half (BAL-532's, until X-bi-6-4). The check runs when the
change is saved, so a door that takes the money off and hides the row in the
same save still works."*  Review 3 measured the door that made the rule
necessary: a stale popover's Actual correction wrote a $125.00 payment record
under a deleted Paid Hotel, which locked its pay period and refused Reset for
good.  The pages' and writers' layers are
``tests/test_routes/test_cc5_4a4_hidden_row_doors.py``; this grades
:mod:`app.deleted_row_infrastructure`'s two triggers against the database the
suite runs on, with raw SQL where a writer nobody enumerated would use it.

Four things are graded, each against its control:

* **a movement cannot ARRIVE under a deleted row** -- an ``INSERT`` (raw, and
  through the ORM) or an ``UPDATE`` re-pointing one there is refused, and the
  same statement against a live row lands;
* **a row cannot be HIDDEN holding one** -- refused at COMMIT, so the delete
  door's own shape (the money off and the row hidden in one save) commits, an
  empty row hides, and a transfer's leg is excepted;
* **what is neither passes** -- a movement a hidden leg already holds (finding
  **BAL-532**'s state) can still be written in place and taken off, so the
  step that ends BAL-532 is not refused by this rule;
* **both are installed at head**, where the suite's template carries them.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import InternalError

from app.deleted_row_infrastructure import DELETED_ROW_TRIGGERS
from app.extensions import db as _db
from app.models.transaction_entry import TransactionEntry
from app.services import transaction_service
from tests._test_helpers import (
    add_entry,
    create_account_of_type,
    create_settled_transfer,
    generate_row_of,
    make_expense_template,
)
# Pylint: ``shekel-private-module-import`` -- the statement-match builders are
# the one way a test stages a one-off envelope as the grid's create door
# writes it (the convention ``test_cc5_4a4_row_keeps_its_movements`` keeps).
# pylint: disable=shekel-private-module-import
from tests.test_services.test_statement_match._builders import (
    a_one_off_envelope,
)

#: The arrival arm's refusal, as the database words it.
_ARRIVAL_REFUSED = "was deleted: a payment or purchase cannot be recorded under it"

#: The hiding arm's refusal, as the database words it.
_HIDING_REFUSED = "was deleted while it still holds a recorded payment or purchase"

#: Every column of a movement but its key and its row, so a raw statement can
#: copy one under another row the way a bulk writer would.
_COPIED = ", ".join(
    column.name for column in TransactionEntry.__table__.columns
    if column.name not in ("id", "transaction_id")
)


def _day(seed_user):
    """A purchase day inside the bootstrap period, after the books opened."""
    return seed_user["bootstrap_period"].start_date + timedelta(days=1)


def _holding(seed_user, name):
    """A one-off envelope holding a $25.00 purchase; returns ``(row, entry)``."""
    row = a_one_off_envelope(seed_user, name=name)
    add_entry(
        _db.session, seed_user, row, Decimal("25.00"), _day(seed_user),
    )
    _db.session.commit()
    entry = _db.session.query(TransactionEntry).filter_by(
        transaction_id=row.id,
    ).one()
    return row, entry


def _hide(row_id):
    """Stage ``UPDATE ... SET is_deleted`` on *row_id*; the caller commits."""
    _db.session.execute(
        text("UPDATE budget.transactions SET is_deleted = TRUE WHERE id = :id"),
        {"id": row_id},
    )


def _hidden_leg(seed_user):
    """BAL-532's state: a settled $100.00 transfer soft-deleted, its legs holding their payments.

    Hidden the way ``transfer_service.delete_transfer(..., soft=True)`` leaves
    it -- the transfer and both shadows flagged, the payments in place.
    Returns ``(leg_id, its payment's id)`` for the expense leg.
    """
    savings = create_account_of_type(
        seed_user, _db.session, "Savings", "R-CC89 Savings",
    )
    _db.session.commit()
    transfer = create_settled_transfer(
        seed_user, _db.session, seed_user["account"], savings,
        seed_user["bootstrap_period"], amount=Decimal("100.00"),
    )
    _db.session.commit()
    _db.session.execute(
        text("UPDATE budget.transactions SET is_deleted = TRUE "
             "WHERE transfer_id = :t"),
        {"t": transfer.id},
    )
    _db.session.execute(
        text("UPDATE budget.transfers SET is_deleted = TRUE WHERE id = :t"),
        {"t": transfer.id},
    )
    _db.session.commit()
    leg_id, entry_id = _db.session.execute(
        text("SELECT t.id, e.id FROM budget.transactions t "
             "JOIN budget.transaction_entries e ON e.transaction_id = t.id "
             "WHERE t.transfer_id = :t AND t.account_id = :a"),
        {"t": transfer.id, "a": seed_user["account"].id},
    ).one()
    return leg_id, entry_id


def _copy_under(entry_id, row_id):
    """``INSERT`` a copy of movement *entry_id* under row *row_id*, in raw SQL."""
    _db.session.execute(
        text(
            f"INSERT INTO budget.transaction_entries (transaction_id, {_COPIED}) "
            f"SELECT :row, {_COPIED} FROM budget.transaction_entries "
            "WHERE id = :entry"
        ),
        {"row": row_id, "entry": entry_id},
    )


def _re_point(entry_id, row_id):
    """``UPDATE`` movement *entry_id* to sit under row *row_id*, in raw SQL."""
    _db.session.execute(
        text("UPDATE budget.transaction_entries SET transaction_id = :row "
             "WHERE id = :entry"),
        {"row": row_id, "entry": entry_id},
    )


def _held_by(row_id):
    """How many movements row *row_id* holds, read from the database."""
    return _db.session.execute(
        text("SELECT count(*) FROM budget.transaction_entries "
             "WHERE transaction_id = :row"),
        {"row": row_id},
    ).scalar()


def _is_hidden(row_id):
    """Whether row *row_id* is soft-deleted, read from the database."""
    return _db.session.execute(
        text("SELECT is_deleted FROM budget.transactions WHERE id = :row"),
        {"row": row_id},
    ).scalar()


class TestAMovementCannotArriveUnderADeletedRow:
    """INSERT and a re-pointing UPDATE are refused under a deleted row, not a live one."""

    def test_an_insert_under_a_deleted_row_is_refused(self, app, db, seed_user):
        """The writer nobody enumerated: a raw INSERT under a hidden envelope."""
        with app.app_context():
            _row, entry = _holding(seed_user, "Home Improvement")
            hidden = a_one_off_envelope(seed_user, name="Garage Sale")
            _hide(hidden.id)
            db.session.commit()
            with pytest.raises(InternalError, match=_ARRIVAL_REFUSED) as caught:
                _copy_under(entry.id, hidden.id)
            assert f"transaction {hidden.id} " in str(caught.value)
            db.session.rollback()
            assert _held_by(hidden.id) == 0

    def test_the_same_insert_under_a_live_row_lands(self, app, db, seed_user):
        """The control: the same statement under a visible envelope."""
        with app.app_context():
            _row, entry = _holding(seed_user, "Home Improvement")
            live = a_one_off_envelope(seed_user, name="Garage Sale")
            _copy_under(entry.id, live.id)
            db.session.commit()
            assert _held_by(live.id) == 1

    def test_the_orm_write_is_refused_too(self, app, db, seed_user):
        """A door writing through the ORM meets the same rule at its flush."""
        with app.app_context():
            hidden = a_one_off_envelope(seed_user, name="Garage Sale")
            _hide(hidden.id)
            db.session.commit()
            db.session.expire_all()
            with pytest.raises(InternalError, match=_ARRIVAL_REFUSED):
                add_entry(
                    db.session, seed_user, hidden, Decimal("12.34"),
                    _day(seed_user),
                )
            db.session.rollback()
            assert _held_by(hidden.id) == 0

    def test_re_pointing_a_movement_onto_a_deleted_row_is_refused(
        self, app, db, seed_user,
    ):
        """An UPDATE that moves a movement under a hidden row is an arrival."""
        with app.app_context():
            row, entry = _holding(seed_user, "Home Improvement")
            hidden = a_one_off_envelope(seed_user, name="Garage Sale")
            _hide(hidden.id)
            db.session.commit()
            with pytest.raises(InternalError, match=_ARRIVAL_REFUSED):
                _re_point(entry.id, hidden.id)
            db.session.rollback()
            assert (_held_by(row.id), _held_by(hidden.id)) == (1, 0)

    def test_re_pointing_onto_a_live_row_lands(self, app, db, seed_user):
        """The control: the same UPDATE onto a visible envelope."""
        with app.app_context():
            row, entry = _holding(seed_user, "Home Improvement")
            live = a_one_off_envelope(seed_user, name="Garage Sale")
            _re_point(entry.id, live.id)
            db.session.commit()
            assert (_held_by(row.id), _held_by(live.id)) == (0, 1)


class TestARowCannotBeHiddenHoldingMoney:
    """Hiding a non-transfer row that holds a movement is refused when the change is saved."""

    def test_hiding_a_row_holding_a_purchase_is_refused_at_commit(
        self, app, db, seed_user,
    ):
        """The statement passes; the COMMIT refuses, and nothing is hidden."""
        with app.app_context():
            row, _entry = _holding(seed_user, "Home Improvement")
            _hide(row.id)
            with pytest.raises(InternalError, match=_HIDING_REFUSED) as caught:
                db.session.commit()
            assert f"transaction {row.id} " in str(caught.value)
            db.session.rollback()
            assert (_is_hidden(row.id), _held_by(row.id)) == (False, 1)

    def test_an_empty_row_hides(self, app, db, seed_user):
        """The control: the same UPDATE on a row holding nothing commits."""
        with app.app_context():
            empty = a_one_off_envelope(seed_user, name="Garage Sale")
            _hide(empty.id)
            db.session.commit()
            assert _is_hidden(empty.id) is True

    def test_the_money_off_and_the_row_hidden_in_one_save_commits(
        self, app, db, seed_user,
    ):
        """The money off and the row hidden in one save, written row FIRST, commits.

        The order an immediate check would refuse (measured: made immediate,
        this test fails while the delete door below still passes, because a
        read inside that door autoflushes its ``DELETE`` first).  Checked at
        COMMIT, the row's ``UPDATE`` meeting the movement still there is not a
        refusal: by the commit it holds nothing.
        """
        with app.app_context():
            row, entry = _holding(seed_user, "Home Improvement")
            _hide(row.id)
            db.session.execute(
                text("DELETE FROM budget.transaction_entries WHERE id = :e"),
                {"e": entry.id},
            )
            db.session.commit()
            assert (_is_hidden(row.id), _held_by(row.id)) == (True, 0)

    def test_the_delete_door_still_hides_a_holding_occurrence(
        self, app, db, seed_user,
    ):
        """End to end: a recurring $300 Groceries occurrence holding a $40 purchase, deleted.

        ``transaction_service.delete_transaction`` takes the purchase off
        through the one removal act and hides the row in one save (ruling
        **R-CC75**); the hiding arm lets it commit.
        """
        with app.app_context():
            template = make_expense_template(
                db.session, seed_user, amount="300.00", name="Groceries",
                category_key="Groceries", is_envelope=True,
            )
            row = generate_row_of(template, seed_user["bootstrap_period"])
            add_entry(
                db.session, seed_user, row, Decimal("40.00"), _day(seed_user),
            )
            db.session.commit()
            assert row.recurs is True

            outcome = transaction_service.delete_transaction(
                row, seed_user["user"].id,
            )
            db.session.commit()

            assert outcome.soft is True
            assert (_is_hidden(row.id), _held_by(row.id)) == (True, 0)

    def test_a_transfer_leg_is_excepted(self, app, db, seed_user):
        """BAL-532's state commits: the transfer's soft delete still makes it until X-bi-6-4."""
        with app.app_context():
            leg_id, _entry_id = _hidden_leg(seed_user)
            assert (_is_hidden(leg_id), _held_by(leg_id)) == (True, 1)

    def test_stripping_a_hidden_leg_of_its_transfer_is_refused(
        self, app, db, seed_user,
    ):
        """``transfer_id`` is watched too, so the exception cannot be walked out of.

        ``ck_transactions_one_pricing_link`` already refuses a bare ``NULL``,
        so the leg is re-pointed at a definition on its own account in the
        same statement, with the due day a definition's row carries -- a row
        that is no transfer's half, hidden, holding the payment.
        """
        with app.app_context():
            template = make_expense_template(
                db.session, seed_user, amount="100.00", name="Stray",
                category_key="Groceries", is_envelope=False,
            )
            db.session.commit()
            leg_id, _entry_id = _hidden_leg(seed_user)
            db.session.execute(
                text("UPDATE budget.transactions "
                     "SET transfer_id = NULL, template_id = :template, "
                     "due_date = :day WHERE id = :id"),
                {"id": leg_id, "template": template.id,
                 "day": _day(seed_user)},
            )
            with pytest.raises(InternalError, match=_HIDING_REFUSED):
                db.session.commit()
            db.session.rollback()


class TestWhatIsNeitherPasses:
    """A movement a hidden leg already holds can be written in place and taken off.

    BAL-532's state, staged the way the transfer's soft delete leaves it.  The
    step that ends BAL-532 must be able to take that payment off, and neither
    arm may stand in its way.
    """

    def test_an_in_place_write_and_a_same_row_save_pass(
        self, app, db, seed_user,
    ):
        """Neither moves a movement anywhere, so neither is refused."""
        with app.app_context():
            _leg_id, entry_id = _hidden_leg(seed_user)
            db.session.execute(
                text("UPDATE budget.transaction_entries "
                     "SET description = 'renamed', "
                     "transaction_id = transaction_id WHERE id = :entry"),
                {"entry": entry_id},
            )
            db.session.commit()
            assert db.session.execute(
                text("SELECT description FROM budget.transaction_entries "
                     "WHERE id = :entry"),
                {"entry": entry_id},
            ).scalar() == "renamed"

    def test_taking_it_off_passes(self, app, db, seed_user):
        """A DELETE is a departure, never an arrival."""
        with app.app_context():
            leg_id, entry_id = _hidden_leg(seed_user)
            db.session.execute(
                text("DELETE FROM budget.transaction_entries WHERE id = :entry"),
                {"entry": entry_id},
            )
            db.session.commit()
            assert _held_by(leg_id) == 0


class TestTheRuleIsInstalledAtHead:
    """The suite's template is built at head, so both attachments are there."""

    def test_each_trigger_is_attached_once(self, app, db):
        """``DELETED_ROW_TRIGGERS`` names every attachment the database carries."""
        with app.app_context():
            found = db.session.execute(
                text("SELECT tgname, tgrelid::regclass::text FROM pg_trigger "
                     "WHERE tgname = ANY(:names) AND NOT tgisinternal "
                     "ORDER BY tgname"),
                {"names": [name for name, _table in DELETED_ROW_TRIGGERS]},
            ).all()
            assert [tuple(row) for row in found] == sorted(DELETED_ROW_TRIGGERS)
