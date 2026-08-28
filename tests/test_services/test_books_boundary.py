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
from app.services.reconcile_service import record_settled_days
from app.services.settle_day import SettleDay, record_settle_day
from tests._test_helpers import (
    create_account_of_type,
    create_settled_cash_transaction,
    restate_account_opening,
)

_ONE_DAY = timedelta(days=1)


def _books_open_on(account):
    """Return the civil day *account*'s books open, through the app's loader."""
    return account_opening_fact(account.id).opened_on


def _entered(day):
    """Return the ``entered``-basis :class:`SettleDay` for *day*."""
    return SettleDay(day=day, basis=SettledDayBasisEnum.ENTERED)


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
            statement = AnchorPoint(
                anchor_id=governing.anchor_id,
                balance=governing.anchor_balance,
                observed_on=opened_on,
                created_at=governing.asserted_at,
            )
            with pytest.raises(ValidationError):
                record_settled_days(
                    seed_user["user"].id, account.id, {entry.id}, statement,
                )
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
