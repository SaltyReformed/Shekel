"""Plan step ``credit_card:CC-5-4a-4``: the deleted-row rule holds when two clicks race.

Ruling **R-CC96** (developer 2026-09-23, "Lock in both"): *"the database check
locks the row, so no deleted row can hold money from any writer, listed or not.
The app's two money writers (add purchase, Mark Paid) and two hiders (Delete,
Archive) also take that lock first, so whichever click lands second gets a
sentence.  Delete first: the companion sees 'Groceries was deleted: a purchase
cannot be recorded under it' and nothing is written.  Purchase first: your
delete waits a moment, then removes the occurrence and its $12.34 purchase, as
if it was there when you pressed Delete."*  Built "with a two-connection race
test for each order", which is this module -- for these pairs: purchase x
Delete, Mark Paid x Delete, purchase x Archive, purchase x Mark Paid and Mark
Credit x Delete in both orders; the popover's Actual correction x Delete in
the delete-first order only.  NOT raced: Mark Paid x Archive, Delete x
Archive, and any pair on a ONE-OFF row, whose delete removes it from the
table (the step's fifth review, L5 and M3).  Ruling **R-CC100**'s order --
the owner's write lock before the row's -- is
:class:`TestTheOwnersLockComesFirst`.

The step's fourth review measured the race the ruling closes: a recurring $300
Groceries occurrence deleted in one session while another added a $12.34 KROGER
purchase ended ``(hidden, held) = (True, 1)`` -- the money out of every balance,
the period locked, no screen able to reach the row -- and the other order
answered the owner's Delete with a 500.

**How a race is staged** (:func:`_race`).  The FIRST click runs in the test's
own session and stops short of its commit, holding whatever it locked.  The
SECOND runs in a session of its own on another thread, and the test waits until
PostgreSQL shows it waiting on a lock (``pg_locks``, not a sleep), or until it
has finished without waiting.  Then the first commits, and only then may the
second commit.  So a second click that did NOT wait -- a door whose lock was
taken away -- still commits after the first, which is the interleaving that
produced the review's state; each door's lock is graded by that, and the
module's mutation record lives with the step's handoff.

**Two layers, graded apart.**  :class:`TestTheDatabaseLocksTheRow` races raw
statements, the writer nobody enumerated, where only
:mod:`app.deleted_row_infrastructure`'s arrival arm stands; the door classes
race the app's own doors, where the door's lock is what turns the loser's raw
database error into its sentence.  :class:`TestNoDoorPairDeadlocks` is the
lock-order measurement: the same doors racing each other end with both
committed, never ``DeadlockDetected``.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Callable

from sqlalchemy import event, text
from sqlalchemy.orm.exc import StaleDataError

from app.enums import SettledDayBasisEnum
from app.exceptions import NotFoundError, ValidationError
from app.extensions import db as _db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import credit_workflow, entry_service, transaction_service
from app.services.settle_day import SettleDay
from tests._test_helpers import generate_row_of, make_expense_template, typed

#: How long a race waits for the second click to block, or to finish, before
#: calling the harness broken.  The cluster's own ``lock_timeout`` is 10 s.
_WAIT = 8.0

#: The purchase door's refusal of a deleted row, naming it (ruling R-CC98).
_PURCHASE_REFUSED = "Groceries was deleted: a purchase cannot be recorded under it"

#: The settle's refusal of a payment on a deleted row, naming it.
_PAYMENT_REFUSED = "Hotel was deleted: a payment cannot be recorded under it"

#: The database arrival arm's refusal, as it words it.
_ARRIVAL_REFUSED = "was deleted: a payment or purchase cannot be recorded under it"


@dataclass
class _Outcome:
    """What one click came to: ``"committed"``, or the exception it raised."""

    result: object = None
    waited: bool = False

    @property
    def committed(self) -> bool:
        """Whether the click's transaction committed."""
        return self.result == "committed"


def _race(app, first: Callable[[], None], second: Callable[[], None]):
    """Run *first* uncommitted here, *second* in another session, then commit in that order.

    Returns ``(first_outcome, second_outcome)``.  The second click's outcome
    records whether PostgreSQL showed it WAITING on a lock before the first
    committed (``waited``), so a test can assert the doors serialised rather
    than merely that they ended well.
    """
    first_outcome, second_outcome = _Outcome(), _Outcome()
    acted, go, done = threading.Event(), threading.Event(), threading.Event()
    pid_box = {}

    def second_click():
        with app.app_context():
            try:
                pid_box["pid"] = _db.session.execute(
                    text("SELECT pg_backend_pid()"),
                ).scalar_one()
                acted_ok = False
                try:
                    second()
                    acted_ok = True
                finally:
                    acted.set()
                if acted_ok:
                    go.wait(_WAIT)
                    _db.session.commit()
                    second_outcome.result = "committed"
            except Exception as exc:  # pylint: disable=broad-exception-caught
                # A race test records whatever the loser raised -- a door's
                # sentence, the trigger's raw error, a deadlock -- so the
                # assertion can say which; narrowing it would turn the very
                # failure the test grades into an unreported thread crash.
                _db.session.rollback()
                second_outcome.result = exc
            finally:
                _db.session.remove()
                done.set()

    first()
    worker = threading.Thread(target=second_click)
    worker.start()
    deadline = time.monotonic() + _WAIT
    while time.monotonic() < deadline and not acted.is_set():
        pid = pid_box.get("pid")
        if pid is not None and _db.session.execute(
            text("SELECT EXISTS (SELECT 1 FROM pg_locks "
                 "WHERE pid = :pid AND NOT granted)"),
            {"pid": pid},
        ).scalar_one():
            second_outcome.waited = True
            break
        time.sleep(0.02)
    assert second_outcome.waited or acted.is_set(), (
        "the second click neither waited on a lock nor finished -- the "
        "harness is not measuring the race"
    )
    try:
        _db.session.commit()
        first_outcome.result = "committed"
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # The first click's commit can itself be the loser (the hiding arm
        # refusing at COMMIT); recorded for the assertion, as above.
        _db.session.rollback()
        first_outcome.result = exc
    go.set()
    assert done.wait(_WAIT * 2), "the second click never finished"
    worker.join(_WAIT)
    _db.session.expire_all()
    return first_outcome, second_outcome


def _state(row_id):
    """Return ``(is_deleted, movements held)`` for *row_id*, read fresh."""
    return (
        _db.session.execute(
            text("SELECT is_deleted FROM budget.transactions WHERE id = :i"),
            {"i": row_id},
        ).scalar_one(),
        _db.session.execute(
            text("SELECT count(*) FROM budget.transaction_entries "
                 "WHERE transaction_id = :i"),
            {"i": row_id},
        ).scalar_one(),
    )


def _groceries(seed_user):
    """A recurring $300.00 Groceries envelope occurrence, committed; returns ``(template, row_id)``."""
    template = make_expense_template(
        _db.session, seed_user, amount="300.00", name="Groceries",
        category_key="Groceries", is_envelope=True,
    )
    row = generate_row_of(template, seed_user["bootstrap_period"])
    _db.session.commit()
    return template, row.id


def _hotel(seed_user):
    """A recurring $120.00 Hotel occurrence (no purchases), committed; returns its id."""
    template = make_expense_template(
        _db.session, seed_user, amount="120.00", name="Hotel",
        category_key="Rent",
    )
    row = generate_row_of(template, seed_user["bootstrap_period"])
    _db.session.commit()
    return row.id


def _add_kroger(seed_user, row_id):
    """Return the companion's click: a $12.34 KROGER purchase on *row_id*."""
    def click():
        entry_service.create_entry(
            row_id, seed_user["user"].id,
            entry_service.EntryDetails(
                figure=typed(Decimal("12.34")), description="KROGER",
                purchased_on=(
                    seed_user["bootstrap_period"].start_date + timedelta(days=1)
                ),
            ),
        )
    return click


def _delete(seed_user, row_id, *, movements_read_first=False):
    """Return the owner's Delete on *row_id*, through the one delete door.

    *movements_read_first* stands for a caller that read the row's movements
    earlier in its request, before the delete took the lock -- the case the
    door's ``entries`` re-read exists for, and the only one that can grade it.
    """
    def click():
        row = _db.session.get(Transaction, row_id)
        if movements_read_first:
            assert row.entries is not None  # loads the collection pre-lock
        transaction_service.delete_transaction(row, seed_user["user"].id)
        _db.session.flush()
    return click


def _mark_paid(row_id, *, movements_read_first=False):
    """Return Mark Paid on *row_id*, through the settle verb.

    *movements_read_first* stands for a caller that read the row's movements
    earlier in its request, before the settle took the lock -- the case the
    verb's ``entries`` re-read exists for, and the only one that can grade it.
    """
    def click():
        row = _db.session.get(Transaction, row_id)
        if movements_read_first:
            assert row.entries is not None  # loads the collection pre-lock
        transaction_service.settle_transaction(row)
        _db.session.flush()
    return click


def _archive(template_id):
    """Return the archive of *template_id*: the definition stops and its empty rows hide."""
    # Pylint: ``protected-access``-shaped -- the archive's write is the route
    # module's helper, shared by its two archive doors; a race cannot hold a
    # ROUTE's transaction open, so the test calls the write the route makes.
    from app.routes.templates.crud import (  # pylint: disable=import-outside-toplevel
        _soft_delete_projected_rows,
    )
    from app.models.transaction_template import (  # pylint: disable=import-outside-toplevel
        TransactionTemplate,
    )

    def click():
        template = _db.session.get(TransactionTemplate, template_id)
        template.is_active = False
        _soft_delete_projected_rows(template)
        _db.session.flush()
    return click


class TestTheDatabaseLocksTheRow:
    """The arrival arm's lock, raced with raw statements: the writer nobody enumerated."""

    @staticmethod
    def _raw_insert(row_id, source_row_id):
        """Return a click copying *source_row_id*'s movement under *row_id*, raw."""
        copied = ", ".join(
            column.name for column in TransactionEntry.__table__.columns
            if column.name not in ("id", "transaction_id")
        )

        def click():
            _db.session.execute(
                text(
                    f"INSERT INTO budget.transaction_entries "
                    f"(transaction_id, {copied}) "
                    f"SELECT :row, {copied} FROM budget.transaction_entries "
                    f"WHERE transaction_id = :src"
                ),
                {"row": row_id, "src": source_row_id},
            )
        return click

    @staticmethod
    def _raw_hide(row_id):
        """Return a click hiding *row_id* with a raw ``UPDATE``."""
        def click():
            _db.session.execute(
                text("UPDATE budget.transactions SET is_deleted = TRUE "
                     "WHERE id = :i"),
                {"i": row_id},
            )
        return click

    @staticmethod
    def _two_rows(seed_user):
        """An empty Groceries row, and a Pantry envelope row holding the purchase to copy."""
        _template, row_id = _groceries(seed_user)
        pantry = make_expense_template(
            _db.session, seed_user, amount="50.00", name="Pantry",
            category_key="Groceries", is_envelope=True,
        )
        source_id = generate_row_of(pantry, seed_user["bootstrap_period"]).id
        _db.session.commit()
        _add_kroger(seed_user, source_id)()
        _db.session.commit()
        return row_id, source_id

    def test_hide_first_the_insert_waits_and_is_refused(self, app, db, seed_user):
        """Hide open, insert arrives: it waits for the hide, then the arm refuses it."""
        with app.app_context():
            row_id, source_id = self._two_rows(seed_user)
            hide, insert = _race(
                app, self._raw_hide(row_id), self._raw_insert(row_id, source_id),
            )
            assert hide.committed
            assert insert.waited, "the arrival did not wait for the open hide"
            assert _ARRIVAL_REFUSED in str(insert.result)
            assert _state(row_id) == (True, 0)

    def test_insert_first_the_hide_waits_and_is_refused(self, app, db, seed_user):
        """Insert open, hide arrives: it waits for the insert, then the hiding arm refuses it."""
        with app.app_context():
            row_id, source_id = self._two_rows(seed_user)
            insert, hide = _race(
                app, self._raw_insert(row_id, source_id), self._raw_hide(row_id),
            )
            assert insert.committed
            assert hide.waited
            assert "while it still holds a recorded payment or purchase" in (
                str(hide.result)
            )
            assert _state(row_id) == (False, 1)


class TestPurchaseAgainstDelete:
    """The review's own race: a companion's purchase and the owner's Delete on one occurrence."""

    def test_delete_first_the_purchase_is_refused_in_words(self, app, db, seed_user):
        """Delete lands first: the purchase waits, then meets the door's sentence; nothing is written."""
        with app.app_context():
            _template, row_id = _groceries(seed_user)
            delete, purchase = _race(
                app, _delete(seed_user, row_id), _add_kroger(seed_user, row_id),
            )
            assert delete.committed
            assert purchase.waited
            assert isinstance(purchase.result, ValidationError), purchase.result
            assert _PURCHASE_REFUSED in str(purchase.result)
            assert _state(row_id) == (True, 0)

    def test_purchase_first_the_delete_takes_it_off_too(self, app, db, seed_user):
        """Purchase lands first: the delete waits, then removes the row AND the $12.34 purchase."""
        with app.app_context():
            _template, row_id = _groceries(seed_user)
            purchase, delete = _race(
                app, _add_kroger(seed_user, row_id), _delete(seed_user, row_id),
            )
            assert purchase.committed
            assert delete.waited
            assert delete.committed, delete.result
            assert _state(row_id) == (True, 0)

    def test_purchase_first_a_delete_that_read_the_movements_takes_it_off_too(
        self, app, db, seed_user,
    ):
        """The same, when the delete's caller had loaded Groceries' movements before the lock.

        The door re-reads them under the lock, so the $12.34 that committed
        while it waited goes with the row; read once, before, the delete saw
        none, hid the row over the purchase, and the hiding arm refused it.
        """
        with app.app_context():
            _template, row_id = _groceries(seed_user)
            purchase, delete = _race(
                app, _add_kroger(seed_user, row_id),
                _delete(seed_user, row_id, movements_read_first=True),
            )
            assert purchase.committed
            assert delete.waited
            assert delete.committed, delete.result
            assert _state(row_id) == (True, 0)


class TestMarkPaidAgainstDelete:
    """Mark Paid on a Hotel occurrence against its Delete."""

    def test_delete_first_mark_paid_is_refused_in_words(self, app, db, seed_user):
        """Delete lands first: Mark Paid waits at the seam, then meets its sentence; no payment."""
        with app.app_context():
            row_id = _hotel(seed_user)
            delete, paid = _race(
                app, _delete(seed_user, row_id), _mark_paid(row_id),
            )
            assert delete.committed
            assert paid.waited
            assert isinstance(paid.result, ValidationError), paid.result
            assert _PAYMENT_REFUSED in str(paid.result)
            assert _state(row_id) == (True, 0)

    def test_mark_paid_first_the_delete_answers_the_version_pin(
        self, app, db, seed_user,
    ):
        """Mark Paid lands first: the delete waits, then its version-pinned write reports the change.

        Mark Paid moved the row's version, so the delete meets the error the
        route answers with its 409 and re-fetch -- the owner sees the row Paid
        and decides again; nothing the delete staged is kept.
        """
        with app.app_context():
            row_id = _hotel(seed_user)
            paid, delete = _race(
                app, _mark_paid(row_id), _delete(seed_user, row_id),
            )
            assert paid.committed
            assert delete.waited
            assert isinstance(delete.result, StaleDataError), delete.result
            assert _state(row_id) == (False, 1)


class TestPurchaseAgainstArchive:
    """A purchase on one of a definition's rows against the definition's archive."""

    def test_archive_first_the_purchase_is_refused_in_words(self, app, db, seed_user):
        """Archive lands first: the row is hidden empty, and the purchase meets the sentence."""
        with app.app_context():
            template, row_id = _groceries(seed_user)
            archive, purchase = _race(
                app, _archive(template.id), _add_kroger(seed_user, row_id),
            )
            assert archive.committed
            assert purchase.waited
            assert isinstance(purchase.result, ValidationError), purchase.result
            assert _PURCHASE_REFUSED in str(purchase.result)
            assert _state(row_id) == (True, 0)

    def test_purchase_first_the_archive_keeps_the_row(self, app, db, seed_user):
        """Purchase lands first: the archive waits, then keeps the row that now holds money."""
        with app.app_context():
            template, row_id = _groceries(seed_user)
            purchase, archive = _race(
                app, _add_kroger(seed_user, row_id), _archive(template.id),
            )
            assert purchase.committed
            assert archive.waited
            assert archive.committed, archive.result
            assert _state(row_id) == (False, 1)


class TestNoDoorPairDeadlocks:
    """Two writers on one row queue on its lock and both land -- never ``DeadlockDetected``.

    The measurement behind the strength (:mod:`app.services.row_write_lock`):
    a door taking a SHARED lock first and the write lock later deadlocks
    against its twin, so this pair is the one a weaker door lock would break.
    """

    def test_two_purchases_on_one_row_both_land(self, app, db, seed_user):
        """The owner and a companion each add a purchase to Groceries at once."""
        with app.app_context():
            _template, row_id = _groceries(seed_user)
            one, two = _race(
                app, _add_kroger(seed_user, row_id), _add_kroger(seed_user, row_id),
            )
            assert one.committed
            assert two.waited
            assert two.committed, two.result
            assert _state(row_id) == (False, 2)

    def test_two_deletes_of_one_row_end_deleted_once(self, app, db, seed_user):
        """Two tabs press Delete on one Groceries occurrence: one wins, the other ends cleanly."""
        with app.app_context():
            _template, row_id = _groceries(seed_user)
            one, two = _race(
                app, _delete(seed_user, row_id), _delete(seed_user, row_id),
            )
            assert one.committed
            assert two.waited
            assert two.committed or isinstance(two.result, StaleDataError), (
                two.result
            )
            assert _state(row_id) == (True, 0)


def _movements(row_id):
    """Return ``[(amount, is the row's own payment record)]`` under *row_id*, oldest first."""
    return [
        (str(amount), covers)
        for amount, covers in _db.session.execute(
            text("SELECT amount, covers_settlement "
                 "FROM budget.transaction_entries "
                 "WHERE transaction_id = :i ORDER BY id"),
            {"i": row_id},
        ).all()
    ]


def _status_name(row_id):
    """Return *row_id*'s status name, read fresh (display only, never logic)."""
    return _db.session.execute(
        text("SELECT s.name FROM budget.transactions t "
             "JOIN ref.statuses s ON s.id = t.status_id WHERE t.id = :i"),
        {"i": row_id},
    ).scalar_one()


class TestPurchaseAgainstMarkPaid:
    """Ruling **R-CC99** (a): a purchase and Mark Paid racing on one empty envelope.

    Measured before the fix, in BOTH orders: Groceries ($300.00, nothing spent)
    ended Paid holding a $300.00 payment record AND the companion's $12.34
    purchase -- $312.34 recorded against one $300.00 envelope, the purchase
    counted twice (finding N-318's state, which it called unreachable).  Mark
    Paid decided its figure from a read taken before the purchase committed;
    the purchase door's settled-row refusal read a ``status`` the locking
    statement handed back as ``None`` after its wait.
    """

    def test_purchase_first_mark_paid_settles_at_the_purchases(
        self, app, db, seed_user,
    ):
        """Purchase lands first: Mark Paid waits, then settles Groceries at its purchases, $12.34."""
        with app.app_context():
            _template, row_id = _groceries(seed_user)
            purchase, paid = _race(
                app, _add_kroger(seed_user, row_id),
                _mark_paid(row_id, movements_read_first=True),
            )
            assert purchase.committed
            assert paid.waited
            assert paid.committed, paid.result
            assert _status_name(row_id) == "Paid"
            assert _movements(row_id) == [("12.34", False)]

    def test_mark_paid_first_the_purchase_is_refused_in_words(
        self, app, db, seed_user,
    ):
        """Mark Paid lands first: the purchase waits, then meets the settled-row refusal, naming the row."""
        with app.app_context():
            _template, row_id = _groceries(seed_user)
            paid, purchase = _race(
                app, _mark_paid(row_id), _add_kroger(seed_user, row_id),
            )
            assert paid.committed
            assert purchase.waited
            assert isinstance(purchase.result, ValidationError), purchase.result
            assert str(purchase.result).startswith(
                "Groceries has settled and records a fixed figure",
            )
            assert _status_name(row_id) == "Paid"
            assert _movements(row_id) == [("300.00", True)]


class TestMarkCreditAgainstDelete:
    """Ruling **R-CC99** (b): a stale Mark Credit against the row's Delete.

    Measured before the fix: Delete first, then the stale tab's Mark Credit,
    turned the hidden $80.00 Dinner Credit and created a live $80.00 payback in
    the next period with no visible source.  Ruling **R-CC89**: "the stale Mark
    Credit gets 'not found', exactly as for another user's row".
    """

    @staticmethod
    def _dinner(seed_user, seed_periods):
        """A recurring $80.00 Dinner occurrence with a next period to repay in; returns its id."""
        template = make_expense_template(
            _db.session, seed_user, amount="80.00", name="Dinner",
            category_key="Rent",
        )
        row = generate_row_of(template, seed_periods[0])
        _db.session.commit()
        return row.id

    @staticmethod
    def _mark_credit(seed_user, row_id):
        """Return the stale tab's Mark Credit on *row_id*."""
        def click():
            credit_workflow.mark_as_credit(row_id, seed_user["user"].id)
            _db.session.flush()
        return click

    @staticmethod
    def _paybacks(row_id):
        """Return the ids of the LIVE paybacks naming *row_id* as their source."""
        return _db.session.execute(
            text("SELECT id FROM budget.transactions "
                 "WHERE credit_payback_for_id = :i AND NOT is_deleted"),
            {"i": row_id},
        ).scalars().all()

    def test_delete_first_mark_credit_is_not_found(
        self, app, db, seed_user, seed_periods,
    ):
        """Delete lands first: Mark Credit waits, then answers "not found"; no payback is born."""
        with app.app_context():
            row_id = self._dinner(seed_user, seed_periods)
            delete, credit = _race(
                app, _delete(seed_user, row_id),
                self._mark_credit(seed_user, row_id),
            )
            assert delete.committed
            assert credit.waited
            assert isinstance(credit.result, NotFoundError), credit.result
            assert _state(row_id) == (True, 0)
            assert _status_name(row_id) == "Projected"
            assert not self._paybacks(row_id)

    def test_mark_credit_first_the_delete_answers_the_version_pin(
        self, app, db, seed_user, seed_periods,
    ):
        """Mark Credit lands first: the delete waits, then reports the changed row; the payback stays."""
        with app.app_context():
            row_id = self._dinner(seed_user, seed_periods)
            credit, delete = _race(
                app, self._mark_credit(seed_user, row_id),
                _delete(seed_user, row_id),
            )
            assert credit.committed
            assert delete.waited
            assert isinstance(delete.result, StaleDataError), delete.result
            assert _state(row_id) == (False, 0)
            assert _status_name(row_id) == "Credit"
            assert len(self._paybacks(row_id)) == 1


class TestActualCorrectionAgainstDelete:
    """The seam's own lock: the popover's Actual correction on a Paid row against its Delete.

    The correction reaches the covering writer through
    ``transaction_service.apply_requested_status``'s correction arm, straight
    into the status seam -- the door review 3 measured writing a $125.00
    payment under a deleted Paid Hotel -- so the settle verb's lock does not
    cover it, and the seam's does.
    """

    def test_delete_first_the_correction_is_refused_in_words(
        self, app, db, seed_user,
    ):
        """Delete lands first: the $125.00 correction waits, then meets the seam's sentence."""
        with app.app_context():
            row_id = _hotel(seed_user)
            transaction_service.settle_transaction(
                _db.session.get(Transaction, row_id),
            )
            _db.session.commit()
            paid_status = _db.session.get(Transaction, row_id).status_id

            def correct_actual():
                transaction_service.apply_requested_status(
                    _db.session.get(Transaction, row_id), paid_status,
                    submitted=typed(Decimal("125.00")),
                )
                _db.session.flush()

            delete, correction = _race(
                app, _delete(seed_user, row_id), correct_actual,
            )
            assert delete.committed
            assert correction.waited
            assert isinstance(correction.result, ValidationError), (
                correction.result
            )
            assert _PAYMENT_REFUSED in str(correction.result)
            assert _state(row_id) == (True, 0)


def _posted_groceries(seed_user):
    """Groceries ($300.00) holding a DATED $40.00 KROGER purchase, so its postings exist; returns its id."""
    _template, row_id = _groceries(seed_user)
    day = seed_user["bootstrap_period"].start_date + timedelta(days=1)
    entry_service.create_entry(
        row_id, seed_user["user"].id,
        entry_service.EntryDetails(
            figure=typed(Decimal("40.00")), description="KROGER",
            purchased_on=day,
            settle_day=SettleDay(day=day, basis=SettledDayBasisEnum.OBSERVED),
        ),
    )
    _db.session.commit()
    return row_id


def _settle_both(first_id, then_id):
    """Return the reconcile tick's shape: ONE transaction settling *first_id*, then *then_id*."""
    def click():
        transaction_service.settle_transaction(_db.session.get(Transaction, first_id))
        transaction_service.settle_transaction(_db.session.get(Transaction, then_id))
        _db.session.flush()
    return click


def _race_paused_at_the_owners_lock(app, first, second):
    """Run *first*, stopped at its FIRST request for the owner's write lock; run *second*; release.

    The interleaving review 5 measured and :func:`_race` cannot stage: the
    first click has done everything it does before it asks for the owner's
    lock -- under ruling R-CC100 nothing, so it holds no lock at all; before
    it, the Delete held its row -- and the second click runs to its end (or
    waits) meanwhile.  Both clicks commit on their own threads.

    Returns ``(first_outcome, second_outcome)``.
    """
    first_outcome, second_outcome = _Outcome(), _Outcome()
    paused, release = threading.Event(), threading.Event()
    box = {"thread": None, "paused": False}
    pid_box = {}

    def stop_at_the_owners_lock(_conn, _cursor, statement, _params, _ctx, _many):
        if (threading.get_ident() == box["thread"] and not box["paused"]
                and "pg_advisory_xact_lock" in statement):
            box["paused"] = True
            paused.set()
            release.wait(_WAIT)

    def run(click, outcome, is_first):
        if is_first:
            box["thread"] = threading.get_ident()
        with app.app_context():
            try:
                if not is_first:
                    pid_box["pid"] = _db.session.execute(
                        text("SELECT pg_backend_pid()"),
                    ).scalar_one()
                click()
                _db.session.commit()
                outcome.result = "committed"
            except Exception as exc:  # pylint: disable=broad-exception-caught
                # Recorded for the assertion, as in :func:`_race`: the loser's
                # sentence, a version pin, or the deadlock this class exists
                # to rule out.
                _db.session.rollback()
                outcome.result = exc
            finally:
                _db.session.remove()
                if is_first:
                    paused.set()

    event.listen(_db.engine, "before_cursor_execute", stop_at_the_owners_lock)
    try:
        one = threading.Thread(target=run, args=(first, first_outcome, True))
        one.start()
        assert paused.wait(_WAIT) and box["paused"], (
            "the first click never asked for the owner's lock -- the harness "
            "is not measuring the order"
        )
        two = threading.Thread(target=run, args=(second, second_outcome, False))
        two.start()
        deadline = time.monotonic() + _WAIT
        while time.monotonic() < deadline and two.is_alive():
            pid = pid_box.get("pid")
            if pid is not None and _db.session.execute(
                text("SELECT EXISTS (SELECT 1 FROM pg_locks "
                     "WHERE pid = :pid AND NOT granted)"),
                {"pid": pid},
            ).scalar_one():
                second_outcome.waited = True
                break
            time.sleep(0.02)
        _db.session.rollback()
        release.set()
        one.join(_WAIT * 3)
        two.join(_WAIT * 3)
    finally:
        event.remove(_db.engine, "before_cursor_execute", stop_at_the_owners_lock)
    _db.session.expire_all()
    return first_outcome, second_outcome


def _no_deadlock(*outcomes):
    """Assert no outcome is PostgreSQL's ``DeadlockDetected``."""
    for outcome in outcomes:
        assert "DeadlockDetected" not in repr(outcome.result), outcome.result


class TestTheOwnersLockComesFirst:
    """Ruling **R-CC100**: every door this step locks takes the owner's write lock FIRST.

    *"Every door this step locks (add purchase, Mark Paid, the popover's
    Actual, Delete, Archive, Mark Credit) takes the owner's write lock before
    the row's lock, from the one lock module."*  Review 5 (M1) measured the
    cycle the order closes: a Delete holding its row and then asking for the
    owner's lock (its ledger reversal) against the reconcile tick holding the
    owner's lock and then asking for the row ended ``DeadlockDetected``.

    Graded two ways.  Each door's statements, in order: the owner's lock comes
    before any statement that locks or writes a row.  And the two pairs, raced
    with the first click stopped where it first asks for the owner's lock --
    the one moment a door that locked its row first would hold it while
    waiting.
    """

    @staticmethod
    def _statements(click):
        """Run *click* in this session, uncommitted; return every statement it issued, in order."""
        seen = []
        here = threading.get_ident()

        def record(_conn, _cursor, statement, _params, _ctx, _many):
            if threading.get_ident() == here:
                seen.append(statement)

        event.listen(_db.engine, "before_cursor_execute", record)
        try:
            click()
        finally:
            event.remove(_db.engine, "before_cursor_execute", record)
            _db.session.rollback()
        return seen

    @classmethod
    def _assert_the_owners_lock_first(cls, click):
        """Assert *click*'s first row lock or row write comes after its first owner's lock."""
        statements = cls._statements(click)
        owners = [i for i, sql in enumerate(statements) if "pg_advisory_xact_lock" in sql]
        rows = [
            i for i, sql in enumerate(statements)
            if sql.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE"))
            or " FOR " in sql.upper()
        ]
        assert owners, "the door never took the owner's write lock"
        assert rows, "the door locked and wrote no row -- nothing was measured"
        assert owners[0] < rows[0], statements[rows[0]]

    def test_the_purchase_door(self, app, db, seed_user):
        """A $12.34 purchase on Groceries asks for the owner's lock before it locks the row."""
        with app.app_context():
            _template, row_id = _groceries(seed_user)
            self._assert_the_owners_lock_first(_add_kroger(seed_user, row_id))

    def test_mark_paid(self, app, db, seed_user):
        """Mark Paid on the $120.00 Hotel asks for the owner's lock before it locks the row."""
        with app.app_context():
            self._assert_the_owners_lock_first(_mark_paid(_hotel(seed_user)))

    def test_the_popover_actual(self, app, db, seed_user):
        """A $125.00 Actual correction on a Paid Hotel asks for the owner's lock first.

        Through the SERVICE, so it grades the status arm's order alone: the
        route's own field write -- a typed note -- flushes before this arm
        runs and precedes the owner's lock (review 6, M1), which is plan step
        ``balance:X-bn``'s to end (ruling R-CC106).
        """
        with app.app_context():
            row_id = _hotel(seed_user)
            transaction_service.settle_transaction(_db.session.get(Transaction, row_id))
            _db.session.commit()
            paid_status = _db.session.get(Transaction, row_id).status_id

            def correct_actual():
                transaction_service.apply_requested_status(
                    _db.session.get(Transaction, row_id), paid_status,
                    submitted=typed(Decimal("125.00")),
                )
                _db.session.flush()

            self._assert_the_owners_lock_first(correct_actual)

    def test_delete(self, app, db, seed_user):
        """Delete of a posted Groceries asks for the owner's lock before it locks the row."""
        with app.app_context():
            self._assert_the_owners_lock_first(
                _delete(seed_user, _posted_groceries(seed_user)),
            )

    def test_archive(self, app, db, seed_user):
        """Archive asks for the owner's lock before its rows' locks AND before its own UPDATE."""
        with app.app_context():
            template, _row_id = _groceries(seed_user)
            self._assert_the_owners_lock_first(_archive(template.id))

    def test_mark_credit(self, app, db, seed_user, seed_periods):
        """Mark Credit on an $80.00 Dinner asks for the owner's lock before it locks the row."""
        with app.app_context():
            row_id = TestMarkCreditAgainstDelete._dinner(  # pylint: disable=protected-access
                seed_user, seed_periods,
            )
            self._assert_the_owners_lock_first(
                TestMarkCreditAgainstDelete._mark_credit(  # pylint: disable=protected-access
                    seed_user, row_id,
                ),
            )

    def test_reconcile_against_the_delete_of_a_posted_envelope(
        self, app, db, seed_user,
    ):
        """Review 5's M1 pair: the Delete stopped at its owner's lock, the two-row settle runs.

        Before ruling R-CC100 the Delete held Groceries' row here and the
        settle, holding the owner's lock from Hotel, waited on it: the release
        closed the cycle and the settle ended ``DeadlockDetected``.  Now the
        Delete holds nothing, the settle lands, and the Delete meets the row
        Paid under a moved version: the route's 409.
        """
        with app.app_context():
            row_id = _posted_groceries(seed_user)
            hotel_id = _hotel(seed_user)
            delete, settle = _race_paused_at_the_owners_lock(
                app, _delete(seed_user, row_id), _settle_both(hotel_id, row_id),
            )
            _no_deadlock(delete, settle)
            assert settle.committed, settle.result
            assert isinstance(delete.result, StaleDataError), delete.result
            assert _status_name(row_id) == "Paid"
            assert _state(row_id) == (False, 1)

    def test_mark_paid_against_the_delete(self, app, db, seed_user):
        """Mark Paid stopped at its owner's lock, the Delete runs: Mark Paid then meets the sentence.

        The pair a Delete ALONE taking the owner's lock first would have
        broken (measured: ``DeadlockDetected``), because Mark Paid then held
        Hotel's row while it asked for the owner's lock.
        """
        with app.app_context():
            row_id = _hotel(seed_user)
            paid, delete = _race_paused_at_the_owners_lock(
                app, _mark_paid(row_id), _delete(seed_user, row_id),
            )
            _no_deadlock(paid, delete)
            assert delete.committed, delete.result
            assert isinstance(paid.result, ValidationError), paid.result
            assert _PAYMENT_REFUSED in str(paid.result)
            assert _state(row_id) == (True, 0)
