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
Delete, purchase x Archive and purchase x Mark Paid in both orders, Mark Paid x
Delete and Mark Credit x Delete in both orders; the popover's Actual
correction x Delete in the delete-first order only; Mark Paid x Archive in the
archive-first order only, for the words ruling **R-CC107** gives it.  NOT
raced: Delete x Archive, and any pair on a ONE-OFF row, whose delete removes
it from the table (the step's fifth review, L5 and M3).

The step's fourth review measured the race the ruling closes: a recurring $300
Groceries occurrence deleted in one session while another added a $12.34 KROGER
purchase ended ``(hidden, held) = (True, 1)`` -- the money out of every balance,
the period locked, no screen able to reach the row -- and the other order
answered the owner's Delete with a 500.

**Two layers, graded apart.**  :class:`TestTheDatabaseLocksTheRow` races raw
statements, the writer nobody enumerated, where only
:mod:`app.deleted_row_infrastructure`'s arrival arm stands.  Every other class
races the app's own doors as two REAL REQUESTS, because since plan step
``balance:X-bn`` (rulings **R-CC106**, **R-CC114**) the serialisation the doors
rely on is the REQUEST's: every command transaction a signed-in request opens
takes its owner's write lock before it reads any of the owner's data
(:mod:`app.db_transaction`), so the second click waits there, before its door
has read the row, and then reads the first click's committed work.
:class:`TestTheOwnersLockComesFirst` grades that order door by door.

**How a request race is staged** (:func:`_race`).  Each click is a request on
a thread of its own, from a client signed in under an app context of its own,
so each has its own session exactly as production gives each request.  The
FIRST request runs until its COMMIT and is held there -- a ``before_commit``
hook on that thread alone -- still holding everything it locked.  The SECOND
starts, and the test waits until PostgreSQL shows it waiting for the owner's
write lock, keyed on that owner and in this test's own database (``pg_locks``,
never a sleep and never a lock anywhere on the cluster), or until the harness
gives up.  Then the first commits and the second runs to its answer.  **Every
race asserts the second click waited on the owner's key**, and that is each
race's control: with the owner's lock removed the second request does not wait
there, and every race below fails.

*Until plan step ``balance:X-bn`` the door races called the SERVICES directly
on two sessions, and each door took the row's own write lock
(``app.services.row_write_lock``, deleted at that step).  Three cases went with
it, each grading a moment the request's owner lock makes impossible: a Delete
whose caller had read the row's movements before its lock (the door's
movements re-read), and review 5's two deadlock pairs, which needed a door
holding a row lock while it asked for the owner's lock -- the owner's lock is
now a request's first lock, graded per door below and suite-wide by the lock
census (no endpoint takes a row lock and then the owner's lock).*
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Callable
from urllib.parse import urlsplit

from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.enums import SettledDayBasisEnum
from app.extensions import db as _db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import entry_service, transaction_service
from app.services.settle_day import SettleDay
from app.utils.dates import display_today
from tests._test_helpers import (
    advisory_lock_keys,
    generate_row_of,
    make_expense_template,
    owner_lock_key,
    typed,
)
from tests.conftest import SEED_USER_EMAIL, SEED_USER_PASSWORD
from tests.test_routes._statement_forms import ReconcileFormReader
from tests.test_routes.test_cc5_4a5_popover_presses import dialog_delete_values

#: How long a race waits for a click to reach its commit, or the second click
#: to block, before calling the harness broken.  The cluster's own
#: ``lock_timeout`` is 10 s.
_WAIT = 8.0

#: The purchase door's refusal of a deleted row, naming it (ruling R-CC98),
#: as the add-purchase route shows it (ruling R-CC103).
_PURCHASE_REFUSED = (
    "Groceries was deleted: a purchase cannot be recorded under it.  "
    "Reload the page."
)

#: The same refusal for a row its recurring item's ARCHIVE hid (ruling R-CC107).
_PURCHASE_REFUSED_ARCHIVED = (
    "Groceries was archived: a purchase cannot be recorded under it.  "
    "Reload the page."
)

#: Mark Paid's refusal of a deleted Hotel, as the cell shows it (R-CC102).
_PAYMENT_REFUSED = (
    "Hotel was deleted: a payment cannot be recorded under it.  "
    "Reload the page."
)

#: The same refusal for a row its recurring item's ARCHIVE hid (R-CC107).
_PAYMENT_REFUSED_ARCHIVED = (
    "Hotel was archived: a payment cannot be recorded under it.  "
    "Reload the page."
)

#: The popover Save's refusal of a deleted Hotel (ruling R-CC105).
_SAVE_REFUSED = (
    "Hotel was deleted: this change cannot be saved.  Reload the page."
)

#: The database arrival arm's refusal, as it words it.
_ARRIVAL_REFUSED = "was deleted: a payment or purchase cannot be recorded under it"

#: Whether a backend in THIS database waits, ungranted, for one owner's write
#: lock.  Scoped to the owner's key and to ``current_database()``: a poll of
#: every ungranted lock on the cluster reads a wait on any other lock -- a row
#: lock, another test's key -- as this one, which is how a broken build once
#: passed a race (the step's third checkpoint).
_WAITING_ON_THE_OWNER = text(
    "SELECT EXISTS (SELECT 1 FROM pg_locks "
    "WHERE locktype = 'advisory' AND NOT granted "
    "AND database = (SELECT oid FROM pg_database "
    "WHERE datname = current_database()) "
    "AND classid = CAST(:namespace AS oid) AND objid = CAST(:owner AS oid) "
    "AND objsubid = 2)"
)


@dataclass
class _Outcome:
    """What one raw click came to: ``"committed"``, or the exception it raised."""

    result: object = None
    waited: bool = False

    @property
    def committed(self) -> bool:
        """Whether the click's transaction committed."""
        return self.result == "committed"


def _race_statements(app, first: Callable[[], None], second: Callable[[], None]):
    """Run raw *first* uncommitted here, raw *second* in another session, then commit in that order.

    The harness for :class:`TestTheDatabaseLocksTheRow` alone, whose clicks
    are raw statements rather than requests.  Returns ``(first_outcome,
    second_outcome)``; the second records whether PostgreSQL showed it WAITING
    on a lock before the first committed (``waited``).
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
                # A race test records whatever the loser raised -- the
                # trigger's raw error above all -- so the assertion can say
                # which; narrowing it would turn the very failure the test
                # grades into an unreported thread crash.
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


def _signed_in_clients(app, count=2):
    """*count* clients, each signed in as the seed user under an app context of its own.

    Signed in one context at a time because Flask-Login keeps a signed-in
    user on ``g``, which the test's shared app context would hand the second
    sign-in, and it would then redirect without signing in (finding
    **BAL-521**, owner plan step ``balance:X-cr``).  Each sign-in's redirect
    is checked not to be the sign-in page, so a race cannot quietly run one
    client signed out.

    Returns:
        The signed-in clients, in order.
    """
    clients = []
    for _ in range(count):
        with app.app_context():
            client = app.test_client()
            response = client.post("/login", data={
                "email": SEED_USER_EMAIL, "password": SEED_USER_PASSWORD,
            })
            assert response.status_code == 302, response.status_code
            assert urlsplit(response.headers["Location"]).path != "/login"
            clients.append(client)
    return clients


@dataclass
class _Answer:
    """What one request came to: its response, and whether it waited on the owner's key."""

    response: object = None
    waited: bool = False


def _race(app, owner_id, first, second):
    """Race two REQUESTS: *first* held at its commit, *second* started, then both released.

    *first* and *second* are no-argument callables that each issue one
    request and return its response.  Each runs on a thread of its own with
    no app context pushed, so the request pushes its own, with its own
    session, as production gives it.  The first is held at its FIRST commit
    by a ``before_commit`` hook on its thread alone; the second starts once
    it is held, and the test polls for the second waiting on *owner_id*'s
    write lock (:data:`_WAITING_ON_THE_OWNER`).  Then the first commits and
    the second runs to its answer.

    Args:
        app: The application.
        owner_id: The owner whose write lock both requests take.
        first: The request that commits first.
        second: The request that arrives while the first is held.

    Returns:
        ``(first_answer, second_answer)``; ``second_answer.waited`` is whether
        the second was seen waiting on the owner's key.

    Raises:
        AssertionError: When the first request never reached a commit, or a
            thread did not finish -- the harness measured nothing.
    """
    namespace, owner_key = owner_lock_key(owner_id)
    answers = (_Answer(), _Answer())
    errors = []
    held, release = threading.Event(), threading.Event()
    box = {"first": None, "held": False}

    def hold_the_first_commit(_session):
        if threading.get_ident() == box["first"] and not box["held"]:
            box["held"] = True
            held.set()
            release.wait(_WAIT)

    def run(click, answer, is_first):
        if is_first:
            box["first"] = threading.get_ident()
        try:
            answer.response = click()
        except BaseException as exc:  # pylint: disable=broad-exception-caught
            # Re-raised on the test's thread below: an exception a request
            # raised is the failure the test must report, with its traceback,
            # rather than a thread dying unseen.
            errors.append(exc)
        finally:
            if is_first:
                held.set()

    event.listen(Session, "before_commit", hold_the_first_commit)
    try:
        one = threading.Thread(target=run, args=(first, answers[0], True))
        one.start()
        assert held.wait(_WAIT) and box["held"], (
            "the first request never reached its commit -- nothing was held, "
            f"so nothing raced ({errors or answers[0].response})"
        )
        two = threading.Thread(target=run, args=(second, answers[1], False))
        two.start()
        deadline = time.monotonic() + _WAIT
        while time.monotonic() < deadline and two.is_alive():
            if _db.session.execute(
                _WAITING_ON_THE_OWNER,
                {"namespace": namespace, "owner": owner_key},
            ).scalar_one():
                answers[1].waited = True
                break
            time.sleep(0.02)
        _db.session.rollback()
        release.set()
        one.join(_WAIT * 3)
        two.join(_WAIT * 3)
        assert not one.is_alive() and not two.is_alive(), (
            "a request never finished"
        )
    finally:
        release.set()
        event.remove(Session, "before_commit", hold_the_first_commit)
    if errors:
        raise errors[0]
    _db.session.expire_all()
    return answers


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


def _paybacks(row_id):
    """Return the ids of the LIVE paybacks naming *row_id* as their source."""
    return _db.session.execute(
        text("SELECT id FROM budget.transactions "
             "WHERE credit_payback_for_id = :i AND NOT is_deleted"),
        {"i": row_id},
    ).scalars().all()


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
    """A recurring $120.00 Hotel occurrence (no purchases), committed; returns ``(template, row_id)``."""
    template = make_expense_template(
        _db.session, seed_user, amount="120.00", name="Hotel",
        category_key="Rent",
    )
    row = generate_row_of(template, seed_user["bootstrap_period"])
    _db.session.commit()
    return template, row.id


def _dinner(seed_user, seed_periods):
    """A recurring $80.00 Dinner occurrence with a next period to repay in; returns its id."""
    template = make_expense_template(
        _db.session, seed_user, amount="80.00", name="Dinner",
        category_key="Rent",
    )
    row = generate_row_of(template, seed_periods[0])
    _db.session.commit()
    return row.id


def _paid_hotel(seed_user):
    """The $120.00 Hotel, settled and committed; returns its id."""
    _template, row_id = _hotel(seed_user)
    transaction_service.settle_transaction(_db.session.get(Transaction, row_id))
    _db.session.commit()
    return row_id


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


def _purchase(client, row_id):
    """Return the click: a $12.34 KROGER charge added to *row_id* through the add-purchase form."""
    return lambda: client.post(f"/transactions/{row_id}/entries", data={
        "amount": "12.34", "direction": "charge", "description": "KROGER",
        "purchased_on": display_today().isoformat(),
    })


def _delete(client, row_id):
    """Return the click: Delete on *row_id*, posting what its dialog names NOW.

    The popover is drawn as the click is made ready -- before the race -- so
    the dialog sends the bank lines and purchases the row held then (rulings
    R-CC127 / R-CC131), as a card opened before the other click does.
    """
    query = dialog_delete_values(client, row_id)
    return lambda: client.delete(f"/transactions/{row_id}", query_string=query)


def _mark_paid(client, row_id):
    """Return the click: Mark Paid on *row_id*'s desktop cell."""
    return lambda: client.post(f"/transactions/{row_id}/mark-done")


def _archive(client, template_id):
    """Return the click: Archive on the recurring item *template_id*."""
    return lambda: client.post(f"/templates/{template_id}/archive")


def _mark_credit(client, row_id):
    """Return the click: Mark Credit on *row_id*."""
    return lambda: client.post(f"/transactions/{row_id}/mark-credit")


def _save_an_actual(client, row_id, figure):
    """Return the click: the popover's Save on Paid *row_id* with its Actual typed as *figure*.

    The form is read off the popover as the page renders it, before the click
    (a browser submits every control it renders), so the Save carries the
    version the page was drawn at.
    """
    html = client.get(f"/transactions/{row_id}/full-edit").data.decode()
    start = html.index("<form hx-patch")
    reader = ReconcileFormReader()
    reader.feed(html[start:html.index("</form>", start)])
    payload = dict(reader.fields)
    payload["settled_amount"] = figure
    return lambda: client.patch(f"/transactions/{row_id}", data=payload)


def _body(answer):
    """Return *answer*'s response body as text."""
    return answer.response.get_data(as_text=True)


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
        entry_service.create_entry(
            source_id, seed_user["user"].id,
            entry_service.EntryDetails(
                figure=typed(Decimal("12.34")), description="KROGER",
                purchased_on=(
                    seed_user["bootstrap_period"].start_date + timedelta(days=1)
                ),
            ),
        )
        _db.session.commit()
        return row_id, source_id

    def test_hide_first_the_insert_waits_and_is_refused(self, app, db, seed_user):
        """Hide open, insert arrives: it waits for the hide, then the arm refuses it."""
        with app.app_context():
            row_id, source_id = self._two_rows(seed_user)
            hide, insert = _race_statements(
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
            insert, hide = _race_statements(
                app, self._raw_insert(row_id, source_id), self._raw_hide(row_id),
            )
            assert insert.committed
            assert hide.waited
            assert "while it still holds a recorded payment or purchase" in (
                str(hide.result)
            )
            assert _state(row_id) == (False, 1)


class TestPurchaseAgainstDelete:
    """The review's own race: a purchase and the owner's Delete on one occurrence."""

    def test_delete_first_the_purchase_is_refused_in_words(self, app, db, seed_user):
        """Delete lands first: the purchase waits, then meets the door's sentence; nothing is written."""
        with app.app_context():
            _template, row_id = _groceries(seed_user)
            one, two = _signed_in_clients(app)
            delete, purchase = _race(
                app, seed_user["user"].id,
                _delete(one, row_id), _purchase(two, row_id),
            )
            assert delete.response.status_code == 200
            assert purchase.waited
            assert purchase.response.status_code == 404
            assert _PURCHASE_REFUSED in _body(purchase)
            assert _state(row_id) == (True, 0)

    def test_purchase_first_the_delete_is_refused_naming_it(
        self, app, db, seed_user,
    ):
        """Purchase lands first: the delete waits, then is refused -- its dialog named no purchase.

        Ruling R-CC131 (developer 2026-10-04, "Refuse and redraw"), the
        clause ruling R-CC96 promised: until it, this delete removed the row
        AND the $12.34 purchase its dialog never named (rule-5 re-expression,
        developer-ruled: this read ``delete.response.status_code == 200`` and
        ``_state(row_id) == (True, 0)``).
        """
        with app.app_context():
            _template, row_id = _groceries(seed_user)
            one, two = _signed_in_clients(app)
            purchase, delete = _race(
                app, seed_user["user"].id,
                _purchase(one, row_id), _delete(two, row_id),
            )
            assert purchase.response.status_code == 200
            assert delete.waited
            assert delete.response.status_code == 400
            assert "Groceries holds 1 purchase now" in _body(delete)
            assert _state(row_id) == (False, 1)


class TestMarkPaidAgainstDelete:
    """Mark Paid on a Hotel occurrence against its Delete."""

    def test_delete_first_mark_paid_is_refused_in_words(self, app, db, seed_user):
        """Delete lands first: Mark Paid waits, then its cell says the row was deleted; no payment."""
        with app.app_context():
            _template, row_id = _hotel(seed_user)
            one, two = _signed_in_clients(app)
            delete, paid = _race(
                app, seed_user["user"].id,
                _delete(one, row_id), _mark_paid(two, row_id),
            )
            assert delete.response.status_code == 200
            assert paid.waited
            assert paid.response.status_code == 404
            assert _PAYMENT_REFUSED in _body(paid)
            assert _state(row_id) == (True, 0)

    def test_mark_paid_first_the_delete_removes_the_paid_row(
        self, app, db, seed_user,
    ):
        """Mark Paid lands first: the delete waits, then reads Hotel Paid and deletes it, payment too.

        The delete reads the row after Mark Paid committed, so it deletes what
        the row now is -- the $120.00 payment goes off the books with it.
        Until plan step ``balance:X-bn`` the delete had read the row before
        its row lock, and its version-pinned write answered the change with
        the route's 409; whether a Delete pressed on a row another tab has
        since paid should be told so is a stale page's question, plan step
        ``balance:X-da``'s.
        """
        with app.app_context():
            _template, row_id = _hotel(seed_user)
            one, two = _signed_in_clients(app)
            paid, delete = _race(
                app, seed_user["user"].id,
                _mark_paid(one, row_id), _delete(two, row_id),
            )
            assert paid.response.status_code == 200
            assert delete.waited
            assert delete.response.status_code == 200
            assert _state(row_id) == (True, 0)


class TestPurchaseAgainstArchive:
    """A purchase on one of a definition's rows against the definition's archive."""

    def test_archive_first_the_purchase_is_refused_in_words(self, app, db, seed_user):
        """Archive lands first: the row is hidden empty, and the purchase meets the sentence.

        The sentence says "was archived" since ruling **R-CC107** (developer
        2026-09-24, "Say archived": *"(and the same for a Save or a
        purchase)"*).
        """
        with app.app_context():
            template, row_id = _groceries(seed_user)
            one, two = _signed_in_clients(app)
            archive, purchase = _race(
                app, seed_user["user"].id,
                _archive(one, template.id), _purchase(two, row_id),
            )
            assert archive.response.status_code == 302
            assert purchase.waited
            assert purchase.response.status_code == 404
            assert _PURCHASE_REFUSED_ARCHIVED in _body(purchase)
            assert _state(row_id) == (True, 0)

    def test_purchase_first_the_archive_keeps_the_row(self, app, db, seed_user):
        """Purchase lands first: the archive waits, then keeps the row that now holds money."""
        with app.app_context():
            template, row_id = _groceries(seed_user)
            one, two = _signed_in_clients(app)
            purchase, archive = _race(
                app, seed_user["user"].id,
                _purchase(one, row_id), _archive(two, template.id),
            )
            assert purchase.response.status_code == 200
            assert archive.waited
            assert archive.response.status_code == 302
            assert _state(row_id) == (False, 1)


class TestMarkPaidAgainstArchive:
    """Mark Paid on a Hotel occurrence against its definition's archive (ruling R-CC107)."""

    def test_archive_first_mark_paid_says_archived(self, app, db, seed_user):
        """Archive lands first: Mark Paid waits, then its cell says Hotel was archived; no payment."""
        with app.app_context():
            template, row_id = _hotel(seed_user)
            one, two = _signed_in_clients(app)
            archive, paid = _race(
                app, seed_user["user"].id,
                _archive(one, template.id), _mark_paid(two, row_id),
            )
            assert archive.response.status_code == 302
            assert paid.waited
            assert paid.response.status_code == 404
            assert _PAYMENT_REFUSED_ARCHIVED in _body(paid)
            assert _state(row_id) == (True, 0)


class TestNoDoorPairDeadlocks:
    """Two of the same door on one row queue and both end cleanly -- never ``DeadlockDetected``."""

    def test_two_purchases_on_one_row_both_land(self, app, db, seed_user):
        """Two tabs each add a $12.34 purchase to Groceries at once: both land."""
        with app.app_context():
            _template, row_id = _groceries(seed_user)
            one, two = _signed_in_clients(app)
            first, second = _race(
                app, seed_user["user"].id,
                _purchase(one, row_id), _purchase(two, row_id),
            )
            assert first.response.status_code == 200
            assert second.waited
            assert second.response.status_code == 200
            assert _state(row_id) == (False, 2)

    def test_two_deletes_of_one_row_end_deleted_once(self, app, db, seed_user):
        """Two tabs press Delete on one Groceries occurrence: one deletes it, the other is told it is gone."""
        with app.app_context():
            _template, row_id = _groceries(seed_user)
            one, two = _signed_in_clients(app)
            first, second = _race(
                app, seed_user["user"].id,
                _delete(one, row_id), _delete(two, row_id),
            )
            assert first.response.status_code == 200
            assert second.waited
            assert second.response.status_code == 404
            assert _state(row_id) == (True, 0)


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
            one, two = _signed_in_clients(app)
            purchase, paid = _race(
                app, seed_user["user"].id,
                _purchase(one, row_id), _mark_paid(two, row_id),
            )
            assert purchase.response.status_code == 200
            assert paid.waited
            assert paid.response.status_code == 200
            assert _status_name(row_id) == "Paid"
            assert _movements(row_id) == [("12.34", False)]

    def test_mark_paid_first_the_purchase_is_refused_in_words(
        self, app, db, seed_user,
    ):
        """Mark Paid lands first: the purchase waits, then meets the settled-row refusal, naming the row."""
        with app.app_context():
            _template, row_id = _groceries(seed_user)
            one, two = _signed_in_clients(app)
            paid, purchase = _race(
                app, seed_user["user"].id,
                _mark_paid(one, row_id), _purchase(two, row_id),
            )
            assert paid.response.status_code == 200
            assert purchase.waited
            assert purchase.response.status_code == 400
            assert "Groceries has settled and records a fixed figure" in (
                _body(purchase)
            )
            assert _status_name(row_id) == "Paid"
            assert _movements(row_id) == [("300.00", True)]


class TestMarkCreditAgainstDelete:
    """Ruling **R-CC99** (b): Mark Credit against the row's Delete.

    Measured before the fix: Delete first, then the stale tab's Mark Credit,
    turned the hidden $80.00 Dinner Credit and created a live $80.00 payback in
    the next period with no visible source.  Ruling **R-CC89**: "the stale Mark
    Credit gets 'not found', exactly as for another user's row".
    """

    def test_delete_first_mark_credit_is_not_found(
        self, app, db, seed_user, seed_periods,
    ):
        """Delete lands first: Mark Credit waits, then answers "not found"; no payback is born."""
        with app.app_context():
            row_id = _dinner(seed_user, seed_periods)
            one, two = _signed_in_clients(app)
            delete, credit = _race(
                app, seed_user["user"].id,
                _delete(one, row_id), _mark_credit(two, row_id),
            )
            assert delete.response.status_code == 200
            assert credit.waited
            assert credit.response.status_code == 404
            assert _state(row_id) == (True, 0)
            assert _status_name(row_id) == "Projected"
            assert not _paybacks(row_id)

    def test_mark_credit_first_the_delete_takes_the_payback_too(
        self, app, db, seed_user, seed_periods,
    ):
        """Mark Credit lands first: the delete waits, then deletes the Credit row and its payback.

        The delete reads Dinner after Mark Credit committed, so it deletes the
        Credit row and takes its $80.00 payback down with it, as a Delete of a
        Credit row always does.  Until plan step ``balance:X-bn`` the delete
        had read the row before its row lock and answered the change with the
        route's 409, keeping the payback; whether a Delete pressed on a row
        another tab has since changed should be told so is plan step
        ``balance:X-da``'s question.
        """
        with app.app_context():
            row_id = _dinner(seed_user, seed_periods)
            one, two = _signed_in_clients(app)
            credit, delete = _race(
                app, seed_user["user"].id,
                _mark_credit(one, row_id), _delete(two, row_id),
            )
            assert credit.response.status_code == 200
            assert delete.waited
            assert delete.response.status_code == 200
            assert _state(row_id) == (True, 0)
            assert not _paybacks(row_id)


class TestActualCorrectionAgainstDelete:
    """The popover's Actual correction on a Paid row against its Delete.

    The correction reaches the covering writer through
    ``transaction_service.apply_requested_status``'s correction arm, straight
    into the status seam -- the door review 3 measured writing a $125.00
    payment under a deleted Paid Hotel.
    """

    def test_delete_first_the_correction_is_refused_in_words(
        self, app, db, seed_user,
    ):
        """Delete lands first: the $125.00 Save waits, then its cell says the change cannot be saved."""
        with app.app_context():
            row_id = _paid_hotel(seed_user)
            one, two = _signed_in_clients(app)
            correction = _save_an_actual(two, row_id, "125.00")
            delete, save = _race(
                app, seed_user["user"].id, _delete(one, row_id), correction,
            )
            assert delete.response.status_code == 200
            assert save.waited
            assert save.response.status_code == 404
            assert _SAVE_REFUSED in _body(save)
            assert _state(row_id) == (True, 0)


class TestTheOwnersLockComesFirst:
    """Every door this module races takes the owner's write lock before it reads the owner's data.

    Ruling **R-CC100** (developer 2026-09-23, "Write lock first here"):
    *"Every door this step locks (add purchase, Mark Paid, the popover's
    Actual, Delete, Archive, Mark Credit) takes the owner's write lock before
    the row's lock"* -- and since plan step ``balance:X-bn`` (ruling
    **R-CC106**) the door takes no row lock at all: the REQUEST takes the
    owner's lock where its transaction begins (:mod:`app.db_transaction`).
    Review 5 (M1) measured the cycle the order closes: a Delete holding its
    row and then asking for the owner's lock against the reconcile tick
    holding the owner's lock and then asking for the row ended
    ``DeadlockDetected``.

    Graded on each door's REAL request, run on a thread of its own so it has
    its own session: the owner's key is locked, it is the first advisory lock
    the request takes, and no statement before it reads or writes a budget or
    salary table -- the only rows read before it are the signed-in user's own.
    """

    @staticmethod
    def _statements(click):
        """Run *click* on a thread of its own; return its response and every ``(statement, params)`` it issued."""
        seen, errors = [], []
        box = {}

        def record(_conn, _cursor, statement, params, _ctx, _many):
            if threading.get_ident() == box.get("thread"):
                seen.append((statement, params))

        def run():
            box["thread"] = threading.get_ident()
            try:
                box["response"] = click()
            except BaseException as exc:  # pylint: disable=broad-exception-caught
                # Re-raised below, on the test's thread, with its traceback.
                errors.append(exc)

        event.listen(_db.engine, "before_cursor_execute", record)
        try:
            worker = threading.Thread(target=run)
            worker.start()
            worker.join(_WAIT * 3)
            assert not worker.is_alive(), "the request never finished"
        finally:
            event.remove(_db.engine, "before_cursor_execute", record)
        if errors:
            raise errors[0]
        return box["response"], seen

    @classmethod
    def _assert_the_owners_lock_first(cls, owner_id, click, status):
        """Assert *click*'s request answers *status*, locking the owner before any owner's data."""
        response, statements = cls._statements(click)
        assert response.status_code == status, response.status_code
        keys = advisory_lock_keys(statements)
        assert keys, "the request took no advisory lock"
        assert keys[0] == owner_lock_key(owner_id), keys
        first_lock = next(
            i for i, (sql, _params) in enumerate(statements)
            if "pg_advisory_xact_lock" in sql
        )
        touched = [
            sql for sql, _params in statements[:first_lock]
            if "budget." in sql or "salary." in sql
            or sql.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE"))
            or " FOR " in sql.upper()
        ]
        assert not touched, touched
        assert any(
            "budget." in sql for sql, _params in statements[first_lock:]
        ), "the door read no budget row -- nothing was measured"

    def test_the_purchase_door(self, app, db, seed_user):
        """A $12.34 purchase on Groceries locks the owner before it reads the row."""
        with app.app_context():
            _template, row_id = _groceries(seed_user)
            (client,) = _signed_in_clients(app, 1)
            self._assert_the_owners_lock_first(
                seed_user["user"].id, _purchase(client, row_id), 200,
            )

    def test_mark_paid(self, app, db, seed_user):
        """Mark Paid on the $120.00 Hotel locks the owner before it reads the row."""
        with app.app_context():
            _template, row_id = _hotel(seed_user)
            (client,) = _signed_in_clients(app, 1)
            self._assert_the_owners_lock_first(
                seed_user["user"].id, _mark_paid(client, row_id), 200,
            )

    def test_the_popover_actual(self, app, db, seed_user):
        """A $125.00 Actual saved on a Paid Hotel locks the owner before it reads the row.

        Through the popover's own Save, so the route's field writes -- a typed
        note flushed before the status arm (review 6, M1) -- are inside the
        measurement: none precedes the owner's lock.
        """
        with app.app_context():
            row_id = _paid_hotel(seed_user)
            (client,) = _signed_in_clients(app, 1)
            self._assert_the_owners_lock_first(
                seed_user["user"].id,
                _save_an_actual(client, row_id, "125.00"), 200,
            )

    def test_delete(self, app, db, seed_user):
        """Delete of a posted Groceries locks the owner before it reads the row."""
        with app.app_context():
            row_id = _posted_groceries(seed_user)
            (client,) = _signed_in_clients(app, 1)
            self._assert_the_owners_lock_first(
                seed_user["user"].id, _delete(client, row_id), 200,
            )

    def test_archive(self, app, db, seed_user):
        """Archive locks the owner before it reads the item or its rows."""
        with app.app_context():
            template, _row_id = _groceries(seed_user)
            (client,) = _signed_in_clients(app, 1)
            self._assert_the_owners_lock_first(
                seed_user["user"].id, _archive(client, template.id), 302,
            )

    def test_mark_credit(self, app, db, seed_user, seed_periods):
        """Mark Credit on an $80.00 Dinner locks the owner before it reads the row."""
        with app.app_context():
            row_id = _dinner(seed_user, seed_periods)
            (client,) = _signed_in_clients(app, 1)
            self._assert_the_owners_lock_first(
                seed_user["user"].id, _mark_credit(client, row_id), 200,
            )
