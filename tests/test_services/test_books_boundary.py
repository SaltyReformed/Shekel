"""A movement cannot predate the books it is in (plan step X-f3c-2b, **R-HG**).

**The rule, in one sentence.** An account's opening equity is the balance at the
CLOSE of ``budget.account_openings.opened_on`` -- the same rule
``account_anchor_history.observed_on`` states for an assertion (ruling R-DH (a))
-- so no cash movement may be dated ON OR BEFORE that day.

**Three tiers grade it here, and each covers what the one below cannot.**

* The PREDICATE (:func:`app.services.cash_ledger.reject_movement_before_books_open`),
  where the boundary itself is pinned from both sides so a ``<`` written where
  the code has ``<=`` fails.
* The ORM chokepoint (:func:`app.services.settle_day.record_settle_day`) and the
  doors above it, which is what makes the refusal a sentence a date box can
  render rather than a database exception at COMMIT.
* The DATABASE, which is the only tier that sees the writers the other two
  cannot: a bulk ``query.update()`` with no ORM instance, an opening restated
  FORWARD past a movement that already exists, and anything a future writer
  does that nobody enumerated.

**Why the third tier is graded here and not taken on trust.** The suite's own
history is the argument: ``reconcile_service.record_settled_days`` writes
``settled_on`` by bulk ``UPDATE`` precisely because it has no instance to hand
``record_settle_day``, and the ``@validates`` guard one column over documents
its own blindness to exactly that shape. A guard whose scope nobody measured is
a guard whose scope is wrong.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.enums import SettledDayBasisEnum, StatusEnum, TxnTypeEnum
from app.exceptions import ValidationError
from app.extensions import db as _db
from app.models.account_opening import AccountOpening
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app import ref_cache
from app.services import status_seam
from app.services.cash_ledger import (
    AnchorPoint,
    account_opening_fact,
    cash_anchor_facts,
    reject_movement_before_books_open,
)
from app.services.pay_calendar import calendar_for
from app.services.reconcile_service import Statement, record_settled_days
from app.services.settle_day import SettleDay, record_settle_day
from tests._test_helpers import (
    create_account_of_type,
    create_settled_cash_transaction,
    restate_account_opening,
    settle_day_columns,
)

_ONE_DAY = timedelta(days=1)


def _books_open_on(account):
    """Return the civil day *account*'s books open, through the app's loader."""
    return account_opening_fact(account.id).opened_on


def _entered(day):
    """Return the ``entered``-basis :class:`SettleDay` for *day*."""
    return SettleDay(day=day, basis=SettledDayBasisEnum.ENTERED)


#: Removing an opening row is not an ORM act -- ``AccountOpening._block_delete``
#: refuses one -- so the cases that need it go around the ORM, which is the same
#: reason the arm being graded lives in the database rather than in a service.
_REMOVE_ONE_OPENING = "DELETE FROM budget.account_openings WHERE id = :i"

#: Counts an account's opening rows, for the cascade case below: the point
#: there is that SEVERAL are disposed of together, so the number is read
#: rather than assumed.
_COUNT_OPENINGS = (
    "SELECT count(*) FROM budget.account_openings WHERE account_id = :a"
)

#: The SQL half of "which restatement governs", asked directly.  Going through
#: the function rather than re-writing its ``ORDER BY`` here is the point: a
#: hand-written query in the test would agree with the Python loader while the
#: function the CONSTRAINT calls disagreed with both.
_OPENED_ON_IN_SQL = "SELECT budget.account_books_opened_on(:a)"

#: Two restatements in one statement, with ``created_at`` supplied so the
#: RECORDING order is the test's to choose rather than the wall clock's.
_RECORD_TWO_RESTATEMENTS = """
    INSERT INTO budget.account_openings
           (account_id, opened_on, opening_equity, source_id, created_at)
    VALUES (:a, :first, 1000.00, :s, now() + :first_offset),
           (:a, :second, 1000.00, :s, now() + :second_offset)
"""


def _record_two_restatements(account, first, second, *, same_instant):
    """Append two opening restatements to *account*, oldest recorded first.

    Args:
        account: The :class:`~app.models.account.Account` to restate.
        first: The civil day the first-RECORDED restatement names.
        second: The civil day the second-RECORDED one names.
        same_instant: When true both rows share one ``created_at``, so only
            ``id`` can break the tie; when false the second is recorded a
            second later.
    """
    _db.session.execute(sa.text(_RECORD_TWO_RESTATEMENTS), {
        "a": account.id,
        "s": account_opening_fact(account.id).source_id,
        "first": first,
        "second": second,
        "first_offset": timedelta(seconds=1),
        "second_offset": timedelta(seconds=1 if same_instant else 2),
    })
    _db.session.commit()


class TestTheBoundaryItself:
    """The predicate, pinned from BOTH sides of the day it turns on.

    Asserted at the boundary rather than at an arbitrary depth so the comparison
    direction is what the tests measure: a ``<`` written where the code has
    ``<=`` moves exactly the opening day across the line and fails
    :meth:`test_the_opening_day_itself_is_refused` while every other case here
    still passes.
    """

    def test_a_day_before_the_books_open_is_refused(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The day before the opening raises, naming both dates."""
        with app.app_context():
            account = create_account_of_type(
                seed_user, db.session, "Checking", "Bounded",
                anchor_balance=Decimal("1000.00"),
            )
            opened_on = _books_open_on(account)
            with pytest.raises(ValidationError) as exc:
                reject_movement_before_books_open(
                    account.id, opened_on - _ONE_DAY,
                )
            message = str(exc.value)
            assert (opened_on - _ONE_DAY).isoformat() in message
            assert opened_on.isoformat() in message

    def test_the_opening_day_itself_is_refused(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """**The `<=` half of the rule, and the whole of R-HG.**

        The opening equity is the CLOSING balance for its own day, so money that
        moved that day is inside it.  Paired with the case below so neither can
        pass while the comparison is off by one in either direction.
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, db.session, "Checking", "Bounded",
                anchor_balance=Decimal("1000.00"),
            )
            opened_on = _books_open_on(account)
            with pytest.raises(ValidationError):
                reject_movement_before_books_open(account.id, opened_on)

    def test_the_day_after_the_books_open_is_accepted(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """One day past the opening is the first recordable day."""
        with app.app_context():
            account = create_account_of_type(
                seed_user, db.session, "Checking", "Bounded",
                anchor_balance=Decimal("1000.00"),
            )
            reject_movement_before_books_open(
                account.id, _books_open_on(account) + _ONE_DAY,
            )

    def test_an_account_with_no_opening_record_RAISES_rather_than_permitting(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A broken invariant fails loud instead of reading as "anything goes".

        The alternative -- treating a missing record as an unbounded account --
        is the fabrication ``account_opening_fact`` exists to refuse on the read
        side, arriving through the write side instead.
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, db.session, "Checking", "Bounded",
                anchor_balance=Decimal("1000.00"),
            )
            db.session.query(AccountOpening).filter_by(
                account_id=account.id,
            ).delete(synchronize_session=False)
            db.session.flush()
            with pytest.raises(RuntimeError, match="zero"):
                reject_movement_before_books_open(account.id, date(2030, 1, 1))


class TestTheOneOrmWriter:
    """``record_settle_day`` asks it, so every ORM door inherits the refusal.

    ``status_seam.apply_status_change``, both ``entry_service`` doors and the
    statement matcher all reach ``settled_on`` through this one function, which
    is why the rule is asked there rather than at each of them.
    """

    def test_a_transaction_cannot_be_settled_on_the_opening_day(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """And the row is left UNTOUCHED, both columns."""
        with app.app_context():
            account = seed_user["account"]
            opened_on = _books_open_on(account)
            row = create_settled_cash_transaction(
                seed_user, db.session, seed_periods[0], Decimal("25.00"),
                settled_on=opened_on + _ONE_DAY, name="dated-probe",
            )
            before = (row.settled_on, row.settled_day_basis_id)
            with pytest.raises(ValidationError):
                record_settle_day(row, _entered(opened_on))
            assert (row.settled_on, row.settled_day_basis_id) == before

    def test_a_purchase_cannot_be_settled_on_the_opening_day(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The SECOND table carries the same rule, from the same call.

        ``transaction_entries`` is the other :class:`SettleDatedMixin` table and
        the fold reads a posted purchase as a movement exactly as it reads a
        settled transaction (ruling **R-FM**).  A rule stated for one table and
        enforced on one table is a rule the second table does not have.
        """
        with app.app_context():
            account = seed_user["account"]
            parent = create_settled_cash_transaction(
                seed_user, db.session, seed_periods[0], Decimal("25.00"),
                settled_on=_books_open_on(account) + _ONE_DAY, name="parent",
            )
            entry = TransactionEntry(
                transaction_id=parent.id,
                account_id=account.id,
                user_id=seed_user["user"].id,
                amount=Decimal("10.00"),
                description="probe",
                purchased_on=_books_open_on(account) - _ONE_DAY,
                is_credit=False,
            )
            with pytest.raises(ValidationError):
                record_settle_day(entry, _entered(_books_open_on(account)))
            assert entry.settled_on is None

    def test_CLEARING_a_settle_day_is_never_bounded(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A revert withdraws an assertion and states nothing about when.

        The same carve-out ``settle_day_for_status`` makes for the schedule
        floor (ruling **R-EG**'s unlock path): bounding the clear arm would
        strand a legacy row whose stored day precedes its own books, which is
        the one row that most needs to be revertible.
        """
        with app.app_context():
            account = seed_user["account"]
            row = create_settled_cash_transaction(
                seed_user, db.session, seed_periods[0], Decimal("25.00"),
                settled_on=_books_open_on(account) + _ONE_DAY, name="revert-probe",
            )
            record_settle_day(row, None)
            assert row.settled_on is None
            assert row.settled_day_basis_id is None

    def test_the_seam_refuses_it_for_every_caller(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """``apply_status_change`` inherits it, so a programmatic caller does too.

        The bound is a SEAM invariant and not a door policy, which is the
        difference from the schedule floor (ruling **R-EL**): a pre-schedule
        settle is absorbed by an assertion and is correct, where a pre-opening
        one is counted twice and never is.
        """
        with app.app_context():
            account = seed_user["account"]
            row = Transaction(
                account_id=account.id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="seam-probe",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                estimated_amount=Decimal("25.00"),
            )
            db.session.add(row)
            db.session.flush()
            done = ref_cache.status_id(StatusEnum.DONE)
            with pytest.raises(ValidationError):
                status_seam.apply_status_change(
                    row, done,
                    settle_day=_entered(_books_open_on(account)),
                    settlement=status_seam.Settlement.from_settle(
                        row, Decimal("10.00"),
                    ),
                )


class TestTheBulkWriterAsksForItself:
    """``reconcile_service.record_settled_days`` has no instance to be guarded.

    It stamps a statement's day onto every ticked purchase with one
    ``query.update()``, by design (its own comment: "there is no ORM instance
    to hand that function"), so the refusal
    :func:`app.services.settle_day.record_settle_day` inherits cannot reach it.
    It asks the same predicate itself, and this is the control that says so.

    **Reachable rather than theoretical.**  An assertion's ``observed_on`` is
    bounded below only by ``pay_period_service.earliest_recordable_day``, which
    on the developer's own data is 2026-03-26 -- the very day Checking's books
    open after this step's migration -- so a statement recorded for that day
    would stamp every ticked purchase inside the opening equity.
    """

    def test_a_statement_dated_inside_the_opening_stamps_NOTHING(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The whole act is refused, and no entry is written.

        Refused per ACT rather than per entry, because the day being stamped is
        the STATEMENT's: either every tick lands inside the opening equity or
        none does.  Asserted on the entry's columns after the refusal, so a
        version that raised AFTER its ``UPDATE`` would fail here.
        """
        with app.app_context():
            account = seed_user["account"]
            opened_on = _books_open_on(account)
            parent = create_settled_cash_transaction(
                seed_user, db.session, seed_periods[0], Decimal("40.00"),
                settled_on=opened_on + timedelta(days=5), name="envelope",
            )
            entry = TransactionEntry(
                transaction_id=parent.id,
                account_id=account.id,
                user_id=seed_user["user"].id,
                amount=Decimal("10.00"),
                description="ticked purchase",
                purchased_on=opened_on - _ONE_DAY,
                is_credit=False,
            )
            db.session.add(entry)
            db.session.flush()

            governing = cash_anchor_facts(account.id)[0]
            statement = Statement(
                calendar_for(seed_user["user"].id),
                account.id,
                AnchorPoint(
                    anchor_id=governing.anchor_id,
                    balance=governing.anchor_balance,
                    observed_on=opened_on,
                    created_at=governing.asserted_at,
                ),
            )
            with pytest.raises(ValidationError):
                record_settled_days(statement, {entry.id})
            db.session.refresh(entry)
            assert entry.settled_on is None
            assert entry.reconciled_by_id is None


class TestTheDatabaseSeesWhatTheOrmCannot:
    """The constraint trigger, graded on the writers no ORM guard reaches.

    Each case here goes through raw SQL or a bulk ``UPDATE`` on purpose: an ORM
    call would prove only that the service refusal fires, which the class above
    already grades.
    """

    def test_a_bulk_update_onto_the_opening_day_aborts_at_COMMIT(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """``query.update()`` bypasses every Python guard and is still refused.

        This is the shape ``reconcile_service.record_settled_days`` writes in,
        and the shape the ``settled_on`` ORM validator documents itself blind
        to.
        """
        with app.app_context():
            account = seed_user["account"]
            row = create_settled_cash_transaction(
                seed_user, db.session, seed_periods[0], Decimal("25.00"),
                settled_on=_books_open_on(account) + _ONE_DAY,
                name="bulk-probe",
            )
            db.session.commit()
            db.session.query(Transaction).filter_by(id=row.id).update(
                {Transaction.settled_on: _books_open_on(account)},
                synchronize_session=False,
            )
            with pytest.raises(sa.exc.InternalError, match="books open"):
                db.session.commit()
            db.session.rollback()

    def test_an_opening_cannot_be_restated_FORWARD_past_a_movement(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The OTHER direction, which no write-door refusal can see.

        Moving the books forward is how an account acquires a pre-opening
        movement without anybody writing one, so the invariant needs both arms
        or it holds only against the writer that happens to be guarded.
        """
        with app.app_context():
            account = seed_user["account"]
            moved_on = _books_open_on(account) + _ONE_DAY
            create_settled_cash_transaction(
                seed_user, db.session, seed_periods[0], Decimal("25.00"),
                settled_on=moved_on, name="in-the-way",
            )
            db.session.commit()
            db.session.add(AccountOpening(
                account_id=account.id,
                opened_on=moved_on,
                opening_equity=Decimal("1000.00"),
                source_id=account_opening_fact(account.id).source_id,
            ))
            with pytest.raises(sa.exc.InternalError, match="cannot open its books"):
                db.session.commit()
            db.session.rollback()

    def test_an_opening_restated_BACKWARD_is_accepted(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Which is the repair the refusal's own message tells an owner to make."""
        with app.app_context():
            account = seed_user["account"]
            earlier = _books_open_on(account) - timedelta(days=30)
            restate_account_opening(db.session, account, earlier)
            db.session.commit()
            assert _books_open_on(account) == earlier

    def test_the_check_is_DEFERRED_so_one_transaction_may_do_both(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Re-date the movements and move the books forward in ONE commit.

        **This is why the trigger is ``DEFERRABLE INITIALLY DEFERRED`` and not a
        statement-level check.**  The account-10 repair (**N-379**) does exactly
        this: the opening moves forward to the bank's own day while the rows in
        its way move out of it, and an immediate check would refuse the pair by
        statement order alone.
        """
        with app.app_context():
            account = seed_user["account"]
            opened_on = _books_open_on(account)
            row = create_settled_cash_transaction(
                seed_user, db.session, seed_periods[0], Decimal("25.00"),
                settled_on=opened_on + _ONE_DAY, name="moves-with-the-books",
            )
            db.session.commit()

            later = opened_on + timedelta(days=10)
            db.session.query(Transaction).filter_by(id=row.id).update(
                {Transaction.settled_on: later + _ONE_DAY},
                synchronize_session=False,
            )
            db.session.add(AccountOpening(
                account_id=account.id,
                opened_on=later,
                opening_equity=Decimal("1000.00"),
                source_id=account_opening_fact(account.id).source_id,
            ))
            db.session.commit()
            assert _books_open_on(account) == later

    def test_a_SUPERSEDED_restatement_is_history_and_not_a_violation(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Only the GOVERNING row constrains anything.

        The table is append-only precisely so what the opening USED to be
        survives; a superseded row that the live data now contradicts is a
        record, not a defect.  Both rows land in ONE transaction, so the check
        is made against the one that wins.
        """
        with app.app_context():
            account = seed_user["account"]
            opened_on = _books_open_on(account)
            create_settled_cash_transaction(
                seed_user, db.session, seed_periods[0], Decimal("25.00"),
                settled_on=opened_on + _ONE_DAY, name="after-the-books",
            )
            db.session.commit()

            source_id = account_opening_fact(account.id).source_id
            db.session.execute(sa.text("""
                INSERT INTO budget.account_openings
                       (account_id, opened_on, opening_equity, source_id,
                        created_at)
                VALUES (:a, :bad, 1000.00, :s, now()),
                       (:a, :good, 1000.00, :s, now() + interval '1 second')
            """), {
                "a": account.id,
                "bad": opened_on + timedelta(days=90),
                "good": opened_on,
                "s": source_id,
            })
            db.session.commit()
            assert _books_open_on(account) == opened_on


class TestTheGoverningRowIsWhatIsGraded:
    """The openings side grades the SURVIVING books, not the row written.

    ``budget.assert_books_open_before_books_movements`` dispatches and
    ``budget.assert_account_books_hold_its_movements`` decides, and the
    predicate takes an ACCOUNT rather than a row.  That is what lets one
    statement of the rule cover an INSERT, a raw ``UPDATE`` and a raw
    ``DELETE`` -- and the delete is the case a row-oriented check cannot see at
    all, because the row that breaks the invariant is the one that SURVIVED.
    """

    def test_deleting_the_governing_restatement_cannot_strand_a_movement(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Removing a restatement PROMOTES an older one, which may not reach.

        Hand-built so the promotion is the whole of what changes: the books are
        restated BACKWARD (legal, and the repair the refusal's own message
        recommends), a movement is then recorded in the span that restatement
        opened up, and the restatement is removed by raw SQL -- which the ORM
        refuses (``AccountOpening._block_delete``) and which is exactly why the
        arm lives in the database.  The older row's day is on or after the
        movement, so the account's books no longer hold what it records.
        """
        with app.app_context():
            account = seed_user["account"]
            original = _books_open_on(account)
            restate_account_opening(
                db.session, account, original - timedelta(days=10),
            )
            db.session.commit()
            restatement_id = account_opening_fact(account.id).opening_id

            create_settled_cash_transaction(
                seed_user, db.session, seed_periods[0], Decimal("25.00"),
                settled_on=original - timedelta(days=5),
                name="inside-the-older-books",
            )
            db.session.commit()

            db.session.execute(
                sa.text(_REMOVE_ONE_OPENING), {"i": restatement_id},
            )
            with pytest.raises(
                sa.exc.InternalError, match="cannot open its books",
            ):
                db.session.commit()
            db.session.rollback()

    def test_removing_one_that_does_NOT_govern_is_allowed(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The negative half, so the arm above is not simply "no deletes".

        A superseded row decides nothing, so removing it changes no answer and
        the constraint has nothing to say.  Without this case the arm above
        would pass just as well against a trigger that refused every delete,
        which is a different and wrong rule.
        """
        with app.app_context():
            account = seed_user["account"]
            superseded_id = account_opening_fact(account.id).opening_id
            restate_account_opening(
                db.session, account, _books_open_on(account) - _ONE_DAY,
            )
            db.session.commit()
            governing = account_opening_fact(account.id)
            assert governing.opening_id != superseded_id

            db.session.execute(
                sa.text(_REMOVE_ONE_OPENING), {"i": superseded_id},
            )
            db.session.commit()
            assert account_opening_fact(account.id).opening_id == (
                governing.opening_id
            )


    def test_disposing_of_the_ACCOUNT_cascades_its_books_away(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The DELETE arm cannot make an account undeletable.

        The case a new DELETE arm on a constraint trigger owes: a CASCADE
        removes several opening rows at once, each firing this trigger, and if
        the predicate graded the SURVIVING books against movements that were
        also being disposed of it would abort an ordinary disposal at COMMIT.

        **It cannot, and the reason is an FK asymmetry that this case PINS
        rather than assumes.**  ``budget.account_openings.account_id`` is ON
        DELETE CASCADE and ``budget.transactions.account_id`` is ON DELETE
        RESTRICT, so the two halves are asserted in the order they constrain:
        while a movement is on file the delete is refused by the FOREIGN KEY
        (an ``IntegrityError``, never this trigger's message -- if the books
        constraint were what refused it, that would be the bug this case is
        for); once the movement is gone the account and its several openings
        go together.  A first draft asserted only the second half against an
        account that had never recorded anything, which could not fail.
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, db.session, "Savings", "Disposable",
                anchor_balance=Decimal("500.00"),
            )
            restate_account_opening(
                db.session, account, _books_open_on(account) - timedelta(days=5),
            )
            movement = create_settled_cash_transaction(
                seed_user, db.session, seed_periods[0], Decimal("25.00"),
                settled_on=_books_open_on(account) + _ONE_DAY,
                account=account, name="on-the-books",
            )
            db.session.commit()
            account_id, movement_id = account.id, movement.id
            assert db.session.execute(
                sa.text(_COUNT_OPENINGS), {"a": account_id},
            ).scalar() >= 3, "the cascade must take several rows, not one"

            # While the movement is on file, the FOREIGN KEY refuses -- and it
            # is the FK rather than the books trigger, which is the half that
            # makes the arm above safe.
            with pytest.raises(sa.exc.IntegrityError) as refusal:
                db.session.execute(
                    sa.text("DELETE FROM budget.accounts WHERE id = :i"),
                    {"i": account_id},
                )
                db.session.commit()
            assert "cannot open its books" not in str(refusal.value)
            db.session.rollback()

            # With it gone, the account and its openings go together.
            db.session.execute(
                sa.text("DELETE FROM budget.transactions WHERE id = :i"),
                {"i": movement_id},
            )
            db.session.execute(
                sa.text("DELETE FROM budget.accounts WHERE id = :i"),
                {"i": account_id},
            )
            db.session.commit()
            assert db.session.execute(
                sa.text(_COUNT_OPENINGS), {"a": account_id},
            ).scalar() == 0


    def test_moving_an_opening_BETWEEN_accounts_grades_the_one_it_left(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Both accounts an ``UPDATE`` touches are graded, not just the new one.

        The arm this pins is the whole reason
        ``budget.assert_account_books_hold_its_movements`` takes an ACCOUNT id
        instead of reading ``NEW``: one raw ``UPDATE`` can change which books
        govern TWO accounts, and the account it left is the one a
        ``NEW``-reading predicate never looks at.  The donor is left with an
        older restatement its own movements no longer fit, so a trigger that
        graded only ``NEW.account_id`` would commit this happily.

        No door does this -- the table is append-only and nothing reassigns an
        opening -- which is exactly why it belongs at the database tier, whose
        stated job is the writer nobody enumerated.
        """
        with app.app_context():
            donor = seed_user["account"]
            recipient = create_account_of_type(
                seed_user, db.session, "Savings", "Recipient",
                anchor_balance=Decimal("500.00"),
            )
            original = _books_open_on(donor)
            # The donor's books move BACK, a movement lands in the span that
            # opened up, and only the restatement keeps the two legal.
            restate_account_opening(
                db.session, donor, original - timedelta(days=10),
            )
            db.session.commit()
            moved_id = account_opening_fact(donor.id).opening_id
            create_settled_cash_transaction(
                seed_user, db.session, seed_periods[0], Decimal("25.00"),
                settled_on=original - timedelta(days=5),
                name="held-by-the-restatement",
            )
            db.session.commit()

            # Hand that restatement to the OTHER account.  The recipient is
            # unaffected -- it records nothing that early -- so any refusal
            # must come from grading the account the row LEFT.
            db.session.execute(
                sa.text(
                    "UPDATE budget.account_openings SET account_id = :b "
                    "WHERE id = :i"
                ),
                {"b": recipient.id, "i": moved_id},
            )
            with pytest.raises(
                sa.exc.InternalError, match="cannot open its books",
            ) as refusal:
                db.session.commit()
            assert f"account {donor.id} " in str(refusal.value), (
                "the refusal must name the DONOR, which is the account a "
                "NEW-reading predicate would never have graded"
            )
            db.session.rollback()

    def test_the_PURCHASE_table_carries_the_constraint_too(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The second movement table is attached, graded at the DATABASE tier.

        ``apply_opening_infrastructure`` attaches the movement trigger to
        ``budget.transaction_entries`` as well as ``budget.transactions``, and
        every other case for the entries table goes through the Python
        refusal -- which would still pass if the attachment were dropped from
        ``_MOVEMENT_TABLES`` or silently failed to re-pin.  Purchases are
        where the bulk ``query.update()`` writer lives, so the tier that
        covers it is the one that has to be shown attached.

        Raw SQL on purpose: an ORM write would grade the service rule instead.
        """
        with app.app_context():
            account = seed_user["account"]
            opened_on = _books_open_on(account)
            parent = create_settled_cash_transaction(
                seed_user, db.session, seed_periods[0], Decimal("40.00"),
                settled_on=opened_on + timedelta(days=5), name="envelope",
            )
            entry = TransactionEntry(
                transaction_id=parent.id,
                account_id=account.id,
                user_id=seed_user["user"].id,
                amount=Decimal("10.00"),
                description="purchase",
                # BEFORE the books, which is legal: a purchase DATE says
                # when the card was swiped and only ``settled_on`` says when
                # cash moved.  It also has to precede the settle day the
                # UPDATE below writes, or
                # ``ck_transaction_entries_settled_not_before_purchase``
                # refuses first and this case would grade that instead.
                purchased_on=opened_on - _ONE_DAY,
                is_credit=False,
                **settle_day_columns(opened_on + timedelta(days=5)),
            )
            db.session.add(entry)
            db.session.commit()

            db.session.execute(
                sa.text(
                    "UPDATE budget.transaction_entries SET settled_on = :d "
                    "WHERE id = :i"
                ),
                {"d": opened_on, "i": entry.id},
            )
            with pytest.raises(sa.exc.InternalError, match="books open"):
                db.session.commit()
            db.session.rollback()

class TestTheTwoGoverningLookupsElectTheSameRow:
    """SQL and Python must not disagree about which restatement is in force.

    ``budget.account_books_opened_on`` decides what the constraint ENFORCES and
    :func:`app.services.cash_ledger.account_opening_fact` decides what the fold
    SEEDS AT.  They are two implementations of one rule (ruling **R-HE**: the
    latest RECORDING instant governs, ``id`` breaking a same-instant tie), and
    nothing but this class holds them to the same answer -- a disagreement
    would let the app render a balance from a level the database is
    simultaneously refusing to let it record against.
    """

    def test_they_agree_when_the_recording_order_contradicts_the_days(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Restatements recorded in the REVERSE order of the days they name.

        A lookup ordered by ``opened_on`` -- which is what the positional read
        plan step X-f3c-2a deleted did -- elects the 1-day-back row here, and
        one ordered by the recording instant elects the 90-days-back row.  The
        days are chosen so those are different rows, so the case fails rather
        than passing vacuously if either side drifts.
        """
        with app.app_context():
            account = seed_user["account"]
            base = _books_open_on(account)
            _record_two_restatements(
                account,
                base - timedelta(days=1),
                base - timedelta(days=90),
                same_instant=False,
            )

            in_sql = db.session.execute(
                sa.text(_OPENED_ON_IN_SQL), {"a": account.id},
            ).scalar()
            assert in_sql == base - timedelta(days=90)
            assert account_opening_fact(account.id).opened_on == in_sql

    def test_they_break_a_SAME_INSTANT_tie_the_same_way(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Two rows sharing ``created_at`` to the microsecond, ``id`` deciding.

        Reachable rather than contrived: ``created_at`` carries a ``now()``
        default and PostgreSQL's ``now()`` is the TRANSACTION timestamp, so two
        restatements written in one transaction share it exactly.  Both sides
        break the tie on ``id`` DESC; without that arm each lookup would take
        whichever row its own plan happened to hand it first.
        """
        with app.app_context():
            account = seed_user["account"]
            base = _books_open_on(account)
            _record_two_restatements(
                account,
                base - timedelta(days=30),
                base - timedelta(days=60),
                same_instant=True,
            )

            rows = db.session.query(AccountOpening).filter_by(
                account_id=account.id,
            ).order_by(AccountOpening.id).all()
            assert rows[-1].opened_on == base - timedelta(days=60)

            in_sql = db.session.execute(
                sa.text(_OPENED_ON_IN_SQL), {"a": account.id},
            ).scalar()
            assert in_sql == rows[-1].opened_on
            assert account_opening_fact(account.id).opened_on == in_sql
