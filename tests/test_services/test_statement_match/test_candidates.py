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
from app.services.cash_ledger import amount_basis
from app.services.scenario_resolver import require_baseline_scenario
from app.services.statement_match import RowKind, candidates_for


def _basis(seed_user):
    """Return the pass's amount basis, the way ``ReviewScope.build`` does.

    Built here rather than defaulted inside ``candidates_for`` because plan
    step X-au-j made it the PASS's, and a producer that rebuilt its caller's
    basis is exactly the copy the parameter exists to remove.
    """
    owner_id = seed_user["user"].id
    return amount_basis(owner_id, require_baseline_scenario(owner_id).id)

from ._builders import a_purchase, a_transaction


def _candidate(seed_user, row_id, kind):
    """Return the candidate the matcher offers for one row, or ``None``."""
    rows = candidates_for(
        seed_user["account"].id,
        pay_calendar.calendar_for(seed_user["user"].id),
        _basis(seed_user),
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
                seed_user["account"].id, calendar, _basis(seed_user),
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
