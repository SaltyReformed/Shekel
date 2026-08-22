"""What the matcher OFFERS, read against a real database.

:mod:`app.services.statement_match._candidates` is the read half: it turns the
account's rows into :class:`~app.services.statement_match.CandidateRow` values
priced and DATED as the app holds them.  Its sibling ``test_propose`` grades
the rules over those values and builds them by hand; nothing graded the values
themselves until plan step ``bank_import:X-f6a-3c-1`` made the WINDOW they
carry the proposer's only bound, and an adversarial review measured that a
test suite building rows by hand cannot see a producer that fills them wrongly.

**The scope arm is here for the same reason.**  That step re-keyed ownership
from a correlated subquery on ``pay_periods.user_id`` to the ids of the
owner's own derived calendar, which is what makes the window lookup total --
and a scope is exactly the kind of clause a hand-built value cannot exercise.
"""

from datetime import date, timedelta

import pytest

from app.enums import StatusEnum
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.services import pay_calendar
from app.services.statement_match import RowKind, candidates_for

from ._builders import (
    a_basis,
    a_later_period,
    a_purchase,
    a_transaction,
    an_assertion,
)


def _candidate(seed_user, row_id, kind):
    """Return the candidate the matcher offers for one row, or ``None``."""
    rows = candidates_for(
        seed_user["account"].id,
        pay_calendar.calendar_for(seed_user["user"].id),
        a_basis(seed_user),
    ).rows
    return next(
        (r for r in rows if r.row_id == row_id and r.kind is kind), None,
    )


class TestTheWindowEachRowCarries:
    """Every candidate says which days the app believes its money moved."""

    def test_an_unsettled_transaction_carries_its_WHOLE_pay_period(
        self, app, seed_user,
    ):
        """Both ends, from the DERIVED calendar rather than the stored columns.

        ``pay_periods.end_date`` is a stored copy of a derivable fact that plan
        step ``pay_calendar:C4`` drops, so a bound reading it would have to be
        rewritten by that step; this reads the same span off the calendar.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = a_transaction(seed_user, name="Electricity")
            db.session.commit()

            row = _candidate(seed_user, txn.id, RowKind.TRANSACTION)

            assert row is not None
            assert row.expected_window == (period.start_date, period.end_date)

    def test_a_settled_transaction_carries_the_day_it_SETTLED(
        self, app, seed_user,
    ):
        """An observation beats a belief, and the row still has both."""
        with app.app_context():
            settled_on = seed_user["bootstrap_period"].start_date
            txn = a_transaction(
                seed_user, name="Electricity", settled_on=settled_on,
                status=StatusEnum.DONE,
            )
            db.session.commit()

            row = _candidate(seed_user, txn.id, RowKind.TRANSACTION)

            assert row is not None
            assert row.expected_window == (settled_on, settled_on)

    def test_an_unsettled_purchase_carries_the_day_it_was_MADE(
        self, app, seed_user,
    ):
        """A purchase's budget clock is ONE day, so both ends are that day."""
        with app.app_context():
            made_on = seed_user["bootstrap_period"].start_date
            parent = a_transaction(
                seed_user, name="Groceries", is_envelope=True,
            )
            purchase = a_purchase(
                seed_user, parent, amount="30.00", purchased_on=made_on,
            )
            db.session.commit()

            row = _candidate(seed_user, purchase.id, RowKind.PURCHASE)

            assert row is not None
            assert row.expected_window == (made_on, made_on)


class TestAReconciledDayIsABoundAndNotAnObservation:
    """A row TICKED on the reconcile panel spans; one settled otherwise points.

    **The two settle days this package can meet are different facts, and
    reading both as observations cost real money.**  The panel stamps the day
    the owner asserted the BALANCE for, which
    ``reconcile_service._purchases.record_settled_days`` documents as *"an
    UPPER BOUND on the true posting day"*; a statement match stamps the day the
    bank actually posted.  Measured on the developer's dev database
    2026-08-21: 59 of 61 reconciled purchases sat more than
    :data:`~app.services.statement_match._propose.DAY_WINDOW` days past their
    purchase day, so a point at the bound put every one out of reach of its own
    bank line -- and the import recorded **24 duplicate purchases worth
    `$1,720.61`** rather than matching what the app already held.

    ``TestTheWindowEachRowCarries`` above grades the days a row carries; this
    grades which KIND of fact the settle day is, because that is what decides
    between a span and a point.
    """

    def test_a_reconciled_PURCHASE_opens_its_window_at_the_day_it_was_made(
        self, app, seed_user,
    ):
        """The defect itself: 30 days of bound, not a point 30 days out.

        The gap is deliberately wider than ``DAY_WINDOW`` (14), because inside
        that span the old rule and the new one agree and the case would pass
        against the bug.
        """
        with app.app_context():
            made_on = seed_user["bootstrap_period"].start_date
            asserted_for = made_on + timedelta(days=30)
            assertion = an_assertion(seed_user, observed_on=asserted_for)
            parent = a_transaction(
                seed_user, name="Groceries", is_envelope=True,
            )
            purchase = a_purchase(
                seed_user, parent, amount="18.64", purchased_on=made_on,
                settled_on=asserted_for, reconciled_by=assertion,
            )
            db.session.commit()

            row = _candidate(seed_user, purchase.id, RowKind.PURCHASE)

            assert row is not None
            assert row.settle_day_is_upper_bound is True
            # Made on the 1st, asserted for the 31st: the money moved somewhere
            # in those 30 days and the app cannot say where.
            assert row.expected_window == (made_on, asserted_for)

    def test_a_reconciled_TRANSACTION_opens_its_window_at_its_pay_period(
        self, app, seed_user,
    ):
        """The twin, from the same column, written by the panel's other arm.

        ``reconcile_service._transactions`` stamps the assertion onto a bill
        exactly as ``_purchases`` does onto a purchase, so a rule that fixed
        only purchases would leave the identical defect one table over.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            asserted_for = period.end_date + timedelta(days=30)
            assertion = an_assertion(seed_user, observed_on=asserted_for)
            txn = a_transaction(
                seed_user, name="Electricity", settled_on=asserted_for,
                status=StatusEnum.DONE, reconciled_by=assertion,
            )
            db.session.commit()

            row = _candidate(seed_user, txn.id, RowKind.TRANSACTION)

            assert row is not None
            assert row.settle_day_is_upper_bound is True
            # The period is what the app asserts about WHEN; the assertion day
            # is the ceiling it was ticked under.
            assert row.expected_window == (period.start_date, asserted_for)

    def test_a_row_settled_WITHOUT_a_tick_still_carries_the_point(
        self, app, seed_user,
    ):
        """The other half of the partition, so the span is not the default.

        A statement match stamps the bank's own posting day and releases the
        link (ruling **R-FL**), which is precisely the row whose day IS an
        observation -- widening that one would re-admit the loose matching
        ``expected_window`` exists to refuse.
        """
        with app.app_context():
            settled_on = seed_user["bootstrap_period"].start_date
            txn = a_transaction(
                seed_user, name="Electricity", settled_on=settled_on,
                status=StatusEnum.DONE,
            )
            db.session.commit()

            row = _candidate(seed_user, txn.id, RowKind.TRANSACTION)

            assert row is not None
            assert row.settle_day_is_upper_bound is False
            assert row.expected_window == (settled_on, settled_on)

    def test_a_tick_EARLIER_than_the_row_s_own_period_keeps_the_point(
        self, app, seed_user,
    ):
        """A bound below the floor bounds nothing, so the tighter answer wins.

        Ticking a row against an assertion made BEFORE its pay period opens
        says the money moved by that day and says nothing about a floor.
        Opening the window at the period would then run it BACKWARDS; the
        point is the honest reading, and it is the direction a half-stated
        fact has to fail in on a money path.
        """
        with app.app_context():
            later = a_later_period(seed_user)
            asserted_for = seed_user["bootstrap_period"].start_date
            assertion = an_assertion(seed_user, observed_on=asserted_for)
            txn = a_transaction(
                seed_user, name="Electricity", period=later,
                settled_on=asserted_for, status=StatusEnum.DONE,
                reconciled_by=assertion,
            )
            db.session.commit()

            row = _candidate(seed_user, txn.id, RowKind.TRANSACTION)

            assert row is not None
            assert row.settle_day_is_upper_bound is True
            # expected_on (the later period's start) is AFTER settled_on, so
            # the span would be inverted; the point stands instead.
            assert later.start_date > asserted_for
            assert row.expected_window == (asserted_for, asserted_for)


class TestTheCalendarIsTheOwnershipSCOPE:
    """A row this reader returns names a period the calendar was built from.

    That property is what makes ``calendar.period_by_id`` total in
    ``_transaction_candidates``: it dereferences the answer without a guard,
    so a row whose period the calendar does not carry would raise inside a
    money read rather than being declined.
    """

    def test_every_offered_row_is_datable_by_the_calendar_it_was_scoped_to(
        self, app, seed_user,
    ):
        """The totality argument, asserted rather than reasoned about."""
        with app.app_context():
            a_transaction(seed_user, name="Electricity")
            parent = a_transaction(
                seed_user, name="Groceries", is_envelope=True,
            )
            a_purchase(seed_user, parent, amount="30.00")
            db.session.commit()

            calendar = pay_calendar.calendar_for(seed_user["user"].id)
            rows = candidates_for(
                seed_user["account"].id, calendar, a_basis(seed_user),
            ).rows

            assert rows
            assert all(row.expected_window is not None for row in rows)

    def test_a_row_in_ANOTHER_owner_s_period_is_not_offered(
        self, app, seed_user, second_user,
    ):
        """The scope did not loosen when it stopped being a subquery.

        A period belonging to someone else is not in this owner's calendar, so
        its ids are not in the scope and a row filed under it cannot be
        reached -- the same answer the ``pay_periods.user_id`` subquery gave,
        reached from the value the window is read off instead.
        """
        with app.app_context():
            theirs = PayPeriod(
                user_id=second_user["user"].id,
                start_date=date(2024, 1, 5) + timedelta(days=14),
                end_date=date(2024, 1, 18) + timedelta(days=14),
                period_index=1,
            )
            db.session.add(theirs)
            db.session.flush()
            intruder = a_transaction(
                seed_user, name="Not yours", period=theirs,
            )
            db.session.commit()

            assert _candidate(
                seed_user, intruder.id, RowKind.TRANSACTION,
            ) is None
