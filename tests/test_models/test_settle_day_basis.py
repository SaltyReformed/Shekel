"""A settle DAY says how it is known: the pair, its writers, and its readers.

Plan step **X-az**, closing finding **N-332**.  ``settled_on`` carried three
different kinds of fact on both tables that have it -- a day a bank statement
showed money POST, the day a BALANCE was asserted for (an UPPER BOUND on the true
posting day), and a day the owner typed -- and nothing on the row said which.
The statement matcher told the first apart by testing whether
``reconciled_by_id`` was populated, which is a different question: that column
answers WHICH statement was seen to show the money.  The two agreed by
coincidence of the writers that existed, and the inference was BLIND to the
third case, so a day the owner typed read as a day the bank had shown.

``settled_day_basis_id`` is the answer, and it is ``settled_basis_id``'s shape
one column over: finding **N-241** established for the FIGURE that *"which one a
figure is stands in ``settled_basis_id`` rather than being inferred from a column
being populated"*, and this is the same sentence about the DAY.

**Every test here is a FIRING CONTROL** (``docs/plans/verification.md`` standard
4).  A test that asserted the constraint EXISTS would pass against a constraint
admitting everything, so each one writes the state the rule is supposed to refuse
and asserts the refusal -- by CONSTRAINT NAME at the database tier, which is the
only tier that sees a writer bypassing the ORM, and by exception at the value
type and the write door for the rules a CHECK cannot state.

The shapes under test, and the real writer each stands for:

* **the pairing, in BOTH directions and on BOTH tables.**  It is a BICONDITIONAL
  where the FIGURE's pairing is a bare implication, and the asymmetry is the
  design: ``settled_amount`` OUTLIVES the assertion that recorded it (a revert
  releases the day and keeps what moved), so a figure with no day is the legal
  RETAINED state -- while the basis DESCRIBES the day, so the two are born and
  released together and a basis left behind is residue nothing means;
* **the value type's two refusals** -- an instant, and a ``None`` day.  Both are
  its own documented invariant, and a value type that states a rule it does not
  enforce is the shape this project deletes;
* **the ECHO rule**, which is the defect two independent adversarial reviews
  found in this step's first build: every full-edit form prefills the settle-day
  control, so an untouched Save re-submits the day the row already carries, and
  wrapping that as ``entered`` rewrote a reconcile-panel BOUND as the owner's own
  typing.  Measured on production: **59 of 66 linked purchases, `$4,173.07`**;
* **the matcher's window**, which is the one reader whose ANSWER the basis
  changes -- a span for an ``asserted`` purchase, a point for every other row;
* **the confirmation**, where a bank line agrees with a day the panel had only
  bounded.  No settle door fires when the day does not move, so before this step
  such a row reported itself a bound forever;
* **the backfill's three arms**, each a PREDICATE rather than a measurement.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
import sqlalchemy.exc

from app import ref_cache
from app.enums import SettledDayBasisEnum, SettlementBasisEnum, StatusEnum
from app.extensions import db
from app.models.ref import TransactionType
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services.settle_day import (
    SettleDay,
    record_settle_day,
    recorded_settle_day,
    submitted_settle_day,
)
from tests._test_helpers import (
    an_asserted_day,
    an_entered_day,
    an_observed_day,
    settle_day_columns,
    settled_day_basis_id,
    settlement_columns,
)


def _make_transaction(seed_user, seed_periods, **overrides):
    """Return an UNFLUSHED Projected expense row, with *overrides* applied.

    Deliberately bare, for the reason ``test_settlement_record``'s twin is: the
    door helpers exist precisely to make the refused states unreachable, so a
    control routed through one would grade the helper instead of the constraint.

    Args:
        seed_user: The ``seed_user`` fixture payload.
        seed_periods: The ``seed_periods`` fixture list.
        **overrides: Column values to set or replace.

    Returns:
        The unflushed :class:`~app.models.transaction.Transaction`.
    """
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    fields = {
        "pay_period_id": seed_periods[0].id,
        "scenario_id": seed_user["scenario"].id,
        "account_id": seed_user["account"].id,
        "status_id": ref_cache.status_id(StatusEnum.PROJECTED),
        "name": "Day basis control",
        "category_id": seed_user["categories"]["Rent"].id,
        "transaction_type_id": expense_type.id,
        "estimated_amount": Decimal("300.00"),
    }
    fields.update(overrides)
    return Transaction(**fields)


def _make_envelope_with_purchase(seed_user, seed_periods, **entry_overrides):
    """Return a flushed envelope and one UNFLUSHED purchase against it.

    The entry side needs a real parent because
    ``fk_transaction_entries_parent_account`` makes a purchase's ``account_id``
    its parent's, so a bare purchase cannot be written at all.

    Args:
        seed_user: The ``seed_user`` fixture payload.
        seed_periods: The ``seed_periods`` fixture list.
        **entry_overrides: Column values for the purchase.

    Returns:
        ``(parent, entry)`` -- the flushed parent and the unflushed purchase.
    """
    parent = _make_transaction(
        seed_user, seed_periods, is_envelope=True, name="Groceries",
    )
    db.session.add(parent)
    db.session.flush()
    fields = {
        "transaction_id": parent.id,
        "account_id": parent.account_id,
        "user_id": seed_user["user"].id,
        "amount": Decimal("18.64"),
        "description": "Food Lion",
        "purchased_on": seed_periods[0].start_date,
    }
    fields.update(entry_overrides)
    return parent, TransactionEntry(**fields)


class TestTheDayAndItsBasisArePairedBothWays:
    """The BICONDITIONAL, on both tables, in both directions.

    **This is the one place the pairing differs from the FIGURE's**, and the
    difference is a design decision rather than an oversight (developer,
    2026-08-22).  ``ck_transactions_settled_amount_needs_basis`` is a bare
    implication because a revert RELEASES the day and KEEPS what moved -- so a
    figure with no day is the legal RETAINED state, and a draft that forbade it
    destroyed a figure the user had read off a bank statement.  The day's basis
    has no such split lifetime: it describes the day, so the two are written and
    cleared in one statement and a basis with no day is residue nothing means.
    """

    def test_a_transaction_day_with_no_basis_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The ``->`` direction: a day nobody can classify is unstorable."""
        with app.app_context():
            db.session.add(_make_transaction(
                seed_user, seed_periods,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                settled_on=seed_periods[0].start_date,
                settled_day_basis_id=None,
                **settlement_columns(
                    seed_periods[0].start_date, Decimal("300.00"),
                ),
            ))
            with pytest.raises(sqlalchemy.exc.IntegrityError) as exc:
                db.session.flush()
            assert "ck_transactions_settle_day_basis_pairing" in str(exc.value)
            db.session.rollback()

    def test_a_transaction_basis_with_no_day_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The ``<-`` direction, which the FIGURE's pairing deliberately admits.

        A revert clears both columns in one statement
        (``settle_day.record_settle_day``), so no door can leave this behind.
        The constraint is what makes that a property of the table rather than a
        discipline every future writer has to keep -- and it costs nothing,
        because there is no state this forbids that anything means.
        """
        with app.app_context():
            db.session.add(_make_transaction(
                seed_user, seed_periods,
                settled_on=None,
                settled_day_basis_id=settled_day_basis_id(
                    SettledDayBasisEnum.ENTERED,
                ),
            ))
            with pytest.raises(sqlalchemy.exc.IntegrityError) as exc:
                db.session.flush()
            assert "ck_transactions_settle_day_basis_pairing" in str(exc.value)
            db.session.rollback()

    def test_a_purchase_day_with_no_basis_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The same rule on ``budget.transaction_entries``.

        **Both tables, where the FIGURE's basis needed only one** -- a purchase
        stores no figure of its own, and it carries its own day.
        """
        with app.app_context():
            _, entry = _make_envelope_with_purchase(
                seed_user, seed_periods,
                settled_on=seed_periods[0].start_date,
                settled_day_basis_id=None,
            )
            db.session.add(entry)
            with pytest.raises(sqlalchemy.exc.IntegrityError) as exc:
                db.session.flush()
            assert (
                "ck_transaction_entries_settle_day_basis_pairing"
                in str(exc.value)
            )
            db.session.rollback()

    def test_a_purchase_basis_with_no_day_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """And its ``<-`` direction, so neither table is the one with a hole."""
        with app.app_context():
            _, entry = _make_envelope_with_purchase(
                seed_user, seed_periods,
                settled_on=None,
                settled_day_basis_id=settled_day_basis_id(
                    SettledDayBasisEnum.OBSERVED,
                ),
            )
            db.session.add(entry)
            with pytest.raises(sqlalchemy.exc.IntegrityError) as exc:
                db.session.flush()
            assert (
                "ck_transaction_entries_settle_day_basis_pairing"
                in str(exc.value)
            )
            db.session.rollback()

    def test_the_whole_pair_together_is_accepted(
        self, app, db, seed_user, seed_periods,
    ):
        """The legitimate act, so the refusals above are not a blanket ban.

        Without this the four cases are satisfied by a constraint that refuses
        every row, which is the failure mode a refusal-only suite cannot see.
        """
        with app.app_context():
            day = seed_periods[0].start_date
            txn = _make_transaction(
                seed_user, seed_periods,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                **settle_day_columns(day, SettledDayBasisEnum.ASSERTED),
                **settlement_columns(day, Decimal("300.00")),
            )
            db.session.add(txn)
            db.session.flush()

            assert txn.settled_on == day
            assert recorded_settle_day(txn) == an_asserted_day(day)
            db.session.rollback()

    def test_both_columns_empty_is_accepted(
        self, app, db, seed_user, seed_periods,
    ):
        """A Projected row carries neither, which is every unsettled row."""
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods)
            db.session.add(txn)
            db.session.flush()

            assert txn.settled_on is None
            assert txn.settled_day_basis_id is None
            assert recorded_settle_day(txn) is None
            db.session.rollback()


class TestTheValueTypeRefusesWhatItDocuments:
    """:class:`SettleDay` enforces its own two invariants.

    A value type that states a rule and does not enforce it is prose, and the
    whole reason the pair travels as one value is that a door cannot then build
    a malformed one to hand over.
    """

    def test_an_instant_is_refused_at_construction(self):
        """The ``datetime`` refusal, one layer earlier than the seam's was.

        ``datetime`` subclasses ``date``, so the annotation catches nothing and
        PostgreSQL truncates the instant on the UTC session clock -- filing an
        8pm-Eastern settle under tomorrow (finding **N-179**).  Refusing at
        construction means a wrong-typed day cannot even be PACKAGED for a door,
        which is what makes "a refused call leaves the row untouched" free.
        """
        with pytest.raises(TypeError, match="must be a date"):
            SettleDay(
                day=datetime(2026, 3, 4, 4, 30, tzinfo=timezone.utc),
                basis=SettledDayBasisEnum.ENTERED,
            )

    def test_a_none_day_is_refused(self):
        """A row with no settle day carries no :class:`SettleDay` at all.

        Left unrefused it constructs a pair with a NULL day and a non-NULL
        basis, which every ``ck_*_settle_day_basis_pairing`` makes unstorable --
        so the value would travel through a whole call chain and fail at flush,
        naming a constraint instead of the caller that meant to pass nothing.
        """
        with pytest.raises(ValueError, match="cannot wrap"):
            SettleDay(day=None, basis=SettledDayBasisEnum.ENTERED)

    def test_a_civil_day_is_accepted_on_every_basis(self):
        """The pass-through arm, so the refusals are not a blanket ban."""
        day = date(2026, 3, 4)
        for basis in SettledDayBasisEnum:
            assert SettleDay(day=day, basis=basis).day == day


class TestTheWidenedInstantRefusalCoversPurchasesToo:
    """``TransactionEntry.settled_on`` refuses an instant, which it did not.

    The ``@validates`` hook lived on ``Transaction`` alone until this step, and
    the purchase column had the IDENTICAL exposure with no guard: a rule stated
    for one table and enforced on one table is a rule the second table does not
    have.  :class:`app.models.mixins.SettleDatedMixin` is where both get it.
    """

    def test_a_purchase_refuses_an_instant_on_assignment(
        self, app, db, seed_user, seed_periods,
    ):
        """``entry.settled_on = <datetime>`` raises before anything is stored."""
        with app.app_context():
            _, entry = _make_envelope_with_purchase(
                seed_user, seed_periods,
                **settle_day_columns(seed_periods[0].start_date),
            )
            db.session.add(entry)
            db.session.flush()

            with pytest.raises(TypeError, match="must be a date"):
                entry.settled_on = datetime(
                    2026, 3, 4, 4, 30, tzinfo=timezone.utc,
                )
            assert entry.settled_on == seed_periods[0].start_date
            db.session.rollback()

    def test_a_purchase_refuses_an_instant_in_its_constructor(
        self, app, db, seed_user, seed_periods,
    ):
        """The declarative constructor assigns through ``setattr`` too."""
        with app.app_context():
            with pytest.raises(TypeError, match="must be a date"):
                _make_envelope_with_purchase(
                    seed_user, seed_periods,
                    settled_on=datetime(2026, 3, 4, 4, 30, tzinfo=timezone.utc),
                )
            db.session.rollback()


class TestThePairHasOneWriterAndOneReader:
    """:func:`record_settle_day` and :func:`recorded_settle_day` round-trip.

    The pair's single writer is what keeps the two columns from being written
    apart by any door that goes through it; the constraint is the storage-tier
    backstop for the writer nobody routed.
    """

    def test_writing_and_reading_are_inverses_on_every_basis(
        self, app, db, seed_user, seed_periods,
    ):
        """Every member survives the round trip, so none is silently dropped."""
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods)
            db.session.add(txn)
            db.session.flush()
            day = seed_periods[0].start_date

            for basis in SettledDayBasisEnum:
                record_settle_day(txn, SettleDay(day=day, basis=basis))
                assert recorded_settle_day(txn) == SettleDay(
                    day=day, basis=basis,
                )
            db.session.rollback()

    def test_clearing_writes_both_columns(
        self, app, db, seed_user, seed_periods,
    ):
        """A revert releases the day AND the basis that described it.

        Asserted on the COLUMNS rather than through the reader, because the
        reader would answer ``None`` for a half-cleared row only by raising --
        and what this grades is that the writer left no residue at all.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods)
            db.session.add(txn)
            db.session.flush()
            record_settle_day(txn, an_entered_day(seed_periods[0].start_date))

            record_settle_day(txn, None)

            assert txn.settled_on is None
            assert txn.settled_day_basis_id is None
            db.session.rollback()

    def test_the_reader_refuses_a_half_pair(
        self, app, db, seed_user, seed_periods,
    ):
        """A pair written around every door FAILS LOUD rather than reading as none.

        Unreachable through the constraint, which is the point: this is what
        happens when something writes around it, and answering ``None`` would
        hand a caller a row it could not classify while claiming it had no day.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods)
            txn.settled_on = seed_periods[0].start_date
            txn.settled_day_basis_id = None

            with pytest.raises(ValueError, match="one fact"):
                recorded_settle_day(txn)
            db.session.rollback()


class TestAReSubmittedDayDoesNotRestateItsBasis:
    """The ECHO rule -- the defect two independent adversarial reviews found.

    Every full-edit form and the purchase popover PREFILL the settle-day control
    and submit it on Save, so an untouched Save re-posts the day the row already
    carries.  Wrapping that as ``entered`` rewrote a reconcile-panel BOUND as the
    owner's own typing, with the day unchanged -- so nothing released the
    clearing link and nothing signalled the change -- and
    ``CandidateRow.expected_window`` then collapsed the purchase to a POINT at
    the assertion day, out of reach of its own bank line.  Measured on production
    2026-08-22: **59 of 66 linked purchases, `$4,173.07`**, one innocuous save
    each.

    It is :func:`app.services.status_seam.figure_for_status`' own rule one column
    over, and it is stated ONCE for all three form doors.
    """

    def test_an_echoed_day_keeps_the_basis_the_row_records(self):
        """The defect itself: the same day does not become ``entered``."""
        day = date(2026, 8, 18)

        answer = submitted_settle_day(day, an_asserted_day(day))

        assert answer == an_asserted_day(day)

    def test_an_echo_of_an_OBSERVED_day_keeps_observed_too(self):
        """The other stored basis, so the rule is not asserted-only."""
        day = date(2026, 8, 18)

        answer = submitted_settle_day(day, an_observed_day(day))

        assert answer == an_observed_day(day)

    def test_a_day_that_MOVED_is_the_owners_own(self):
        """The firing half: a real correction is ``entered`` and must be.

        Without this the rule is satisfied by one that keeps the old basis
        always, which would report a day the owner typed as one the bank showed
        -- the same laundering in the other direction.
        """
        stored, typed = date(2026, 8, 18), date(2026, 8, 15)

        answer = submitted_settle_day(typed, an_asserted_day(stored))

        assert answer == an_entered_day(typed)

    def test_a_row_recording_no_day_takes_the_owners_own(self):
        """A row settling for the first time has nothing to echo."""
        typed = date(2026, 8, 15)

        assert submitted_settle_day(typed, None) == an_entered_day(typed)


class TestTheBackfillArmsAreExactOverTheirOwnPredicates:
    """The migration classifies by EVIDENCE, and each arm is a predicate.

    The step specification claimed no row could be shown ``observed``
    retrospectively; ``budget.statement_match_members`` is that evidence and it
    already ran, 235 of 235 rows on the developer's dev database carrying exactly
    their match's own posting day.  What is graded here is the SQL's shape rather
    than a row count, because a count goes stale and a predicate does not.
    """

    def test_every_arm_requires_the_day_to_EQUAL_its_evidence(self):
        """Neither arm classifies on a column merely being POPULATED.

        That shape -- inferring a fact from another column being non-NULL -- is
        verbatim what finding **N-332** is about, so a backfill that used it
        would install the defect as the classifier of record.  The ``asserted``
        arm tested ``reconciled_by_id IS NOT NULL`` alone until an adversarial
        review said so.
        """
        # pylint: disable-next=import-outside-toplevel
        from tests._test_helpers import load_migration_module

        module = load_migration_module(
            "c7d31f9a45e8_a_settle_day_says_how_it_is_known.py"
        )
        source = module.upgrade.__doc__ or ""
        assert "observed" in source and "asserted" in source

        # The two evidence joins, read off the module's own SQL fragments.
        assert "max(l.posted_on)" in module._MATCH_POSTS_ON
        assert "budget.statement_match_members" in module._MATCH_POSTS_ON

    def test_the_three_members_are_seeded_and_resolvable(self, app):
        """``ref_cache`` answers for every member, on a migration-built DB.

        The dual-seed guard (``test_posting_ref_seed_parity``) grades the
        migration and the reseed list; this grades the third leg -- that the
        rows a live app resolves are actually there.
        """
        with app.app_context():
            ids = {
                basis: ref_cache.settled_day_basis_id(basis)
                for basis in SettledDayBasisEnum
            }
            assert len(set(ids.values())) == len(SettledDayBasisEnum)
            assert all(isinstance(v, int) for v in ids.values())


class TestTheDayBasisMovesNoMoney:
    """The step's central claim, asserted rather than argued.

    The basis is metadata ABOUT a day; every balance, fold and posting reads the
    DAY, which this step changes on no row.  A settled row valued through the
    project's own reader must answer the same figure whatever its basis says.
    """

    def test_every_basis_values_the_row_identically(
        self, app, db, seed_user, seed_periods,
    ):
        """One row, three bases, one figure."""
        # pylint: disable-next=import-outside-toplevel
        from app.services.row_valuation import settled_figure

        with app.app_context():
            day = seed_periods[0].start_date
            txn = _make_transaction(
                seed_user, seed_periods,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                **settle_day_columns(day),
                **settlement_columns(day, Decimal("300.00")),
            )
            db.session.add(txn)
            db.session.flush()

            figures = set()
            for basis in SettledDayBasisEnum:
                record_settle_day(txn, SettleDay(day=day, basis=basis))
                db.session.flush()
                db.session.expire(txn, ["status"])
                figures.add(settled_figure(txn))

            assert len(figures) == 1
            assert figures == {Decimal("300.00")}
            db.session.rollback()

    def test_the_day_itself_survives_a_basis_change(
        self, app, db, seed_user, seed_periods,
    ):
        """Raising a basis does not move the day the ledger files money under.

        This is what makes the confirmation arm safe: the matcher writes
        ``observed`` over an ``asserted`` bound the bank agrees with, and the
        posting day is unchanged, so no journal entry re-dates.
        """
        with app.app_context():
            day = seed_periods[0].start_date + timedelta(days=3)
            txn = _make_transaction(
                seed_user, seed_periods,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                **settle_day_columns(day, SettledDayBasisEnum.ASSERTED),
                **settlement_columns(day, Decimal("300.00")),
            )
            db.session.add(txn)
            db.session.flush()

            record_settle_day(txn, an_observed_day(day))
            db.session.flush()

            assert txn.settled_on == day
            assert recorded_settle_day(txn).basis is (
                SettledDayBasisEnum.OBSERVED
            )
            db.session.rollback()


class TestTheFigureBasisAndTheDayBasisAreDifferentColumns:
    """The two provenances do not share a column, a lifetime, or a constraint.

    Believing they did is what a draft of **X-au-c3** got wrong for the FIGURE,
    and repeating it for the DAY would be the same defect one column over.
    """

    def test_a_revert_releases_the_day_pair_and_keeps_what_moved(
        self, app, db, seed_user, seed_periods,
    ):
        """The asymmetry, end to end through the seam.

        A revert withdraws the ASSERTION -- the day, its basis and the clearing
        link -- and KEEPS what moved, because the popover TELLS the user to
        revert in order to edit and destroying their statement reading there is
        the data loss **N-241**'s fix exists to prevent.
        """
        # pylint: disable-next=import-outside-toplevel
        from app.services.status_seam import Settlement, apply_status_change

        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods)
            db.session.add(txn)
            db.session.flush()

            apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE),
                settle_day=an_asserted_day(seed_periods[0].start_date),
                settlement=Settlement(
                    amount=Decimal("287.31"),
                    basis=SettlementBasisEnum.CORRECTED,
                ),
            )
            db.session.flush()
            assert recorded_settle_day(txn).basis is (
                SettledDayBasisEnum.ASSERTED
            )

            apply_status_change(txn, ref_cache.status_id(StatusEnum.PROJECTED))
            db.session.flush()

            # The ASSERTION is gone, all three columns of it.
            assert txn.settled_on is None
            assert txn.settled_day_basis_id is None
            assert txn.reconciled_by_id is None
            # WHAT MOVED is kept, which is the whole point of the split.
            assert txn.settled_amount == Decimal("287.31")
            assert txn.settled_basis_id == ref_cache.settlement_basis_id(
                SettlementBasisEnum.CORRECTED,
            )
            db.session.rollback()
