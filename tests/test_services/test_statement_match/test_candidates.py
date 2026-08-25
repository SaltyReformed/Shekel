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

from app.enums import SettledDayBasisEnum, StatusEnum
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.services import pay_calendar
from app.services.statement_match import (
    ReviewedRow,
    RowKind,
    as_reviewed,
    candidates_for,
)
from app.services.statement_match._pairing import DAY_WINDOW

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


class TestEachRowSaysWhetherItsFigureIsItsOwn:
    """``states_own_figure``, read off the row that produced it.

    Plan step **bank_import:X-f6d-1**.  The accept door has always refused a
    correction to a figure that is a fact about ANOTHER row (finding
    **N-252**), and it re-derived the census per act; the near-miss proposer
    needs the same answer and is pure, with no session to ask.  So the
    constructor states it and both read it -- which means the census is graded
    HERE, once, rather than only through the door's four sentences.

    **The two members are load-bearing and one of them was missed once**:
    ``transaction_service`` publishes ``settles_from_entries`` AND
    ``repays_card_spend``, and the transaction door's own backstop refuses only
    the first.
    """

    def test_an_ORDINARY_bill_states_its_own_figure(self, app, db, seed_user):
        """The common shape, and the one a near miss may correct."""
        txn = a_transaction(seed_user, name="Electricity", amount="180.00")
        db.session.flush()

        row = _candidate(seed_user, txn.id, RowKind.TRANSACTION)

        assert row.states_own_figure is True
        assert row.figure_is_correctable is True

    def test_an_ENVELOPE_HOLDING_PURCHASES_does_not(self, app, db, seed_user):
        """Its figure IS its purchases, so a correction is reverted.

        **Both halves of ``settles_from_entries`` matter**: an envelope with
        NO entries derives nothing and keeps its own figure, which the case
        below is the control for.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        a_purchase(seed_user, envelope, amount="25.00")
        db.session.flush()

        row = _candidate(seed_user, envelope.id, RowKind.TRANSACTION)

        assert row.states_own_figure is False
        assert row.figure_is_correctable is False

    def test_an_EMPTY_envelope_still_states_its_own(self, app, db, seed_user):
        """Production's ``Kayla's Spending Money``: envelope-tracked, 0 entries.

        ``tracks_purchases`` alone would claim it and settle it at `$0.00`;
        the predicate asks for entries too, and this is what says so.
        """
        envelope = a_transaction(
            seed_user, name="Fuel", amount="100.00", is_envelope=True,
        )
        db.session.flush()

        row = _candidate(seed_user, envelope.id, RowKind.TRANSACTION)

        assert row.states_own_figure is True

    def test_a_CC_PAYBACK_does_not(self, app, db, seed_user):
        """The member a first draft of the census MISSED.

        A payback's figure is a fact about the row it repays, and
        ``entry_credit_workflow.sync_entry_payback`` re-states it on every
        entry mutation.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        db.session.flush()
        # No template: ``ck_transactions_one_pricing_link`` admits a recurring
        # definition OR a payback link, never both -- a payback is priced by
        # the row it repays.
        payback = a_transaction(
            seed_user, name="CC Payback: Groceries", amount="60.00",
            template=False,
        )
        payback.credit_payback_for_id = envelope.id
        db.session.flush()

        row = _candidate(seed_user, payback.id, RowKind.TRANSACTION)

        assert row.states_own_figure is False
        assert row.figure_is_correctable is False

    def test_a_PURCHASE_states_its_own_figure(self, app, db, seed_user):
        """A purchase is what the derived rows are made OF."""
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(seed_user, envelope, amount="25.00")
        db.session.flush()

        row = _candidate(seed_user, purchase.id, RowKind.PURCHASE)

        assert row.states_own_figure is True
        assert row.figure_is_correctable is True


class TestWhichRowsTheBankNeverShowsByThemselves:
    """``not_shown_alone``, the caveat the review panel prints per row.

    Plan step **bank_import:X-gc**, finding **N-345**'s design half.  The panel
    headed *Rows you recorded and the bank never showed* asserts that each of
    its rows is *a payment your records claim happened and your bank did not
    make*.  That inference needs the row's money to have been a bank line of
    its OWN, and for two shapes it never is: a CC payback leaves inside one
    lump payment to the card, and an envelope holding purchases is what the
    bank showed the purchases of.

    **The rows stay in the panel and only the CLAIM is withdrawn**, which the
    render tests in ``tests/test_routes/test_statement_matches.py`` pin: that
    panel is also the hand-build form's row-picker, and ruling **R-GJ** leaves
    the group match as a parked card payment's only arm.

    The class above grades ``states_own_figure`` itself; this grades that the
    caveat is derived from it and from nothing else, so the two cannot drift.
    """

    def test_a_CC_PAYBACK_carries_the_caveat(self, app, db, seed_user):
        """18 of the developer's own 67 panel rows, measured 2026-08-25."""
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        db.session.flush()
        payback = a_transaction(
            seed_user, name="CC Payback: Groceries", amount="60.00",
            template=False,
        )
        payback.credit_payback_for_id = envelope.id
        db.session.flush()

        row = _candidate(seed_user, payback.id, RowKind.TRANSACTION)

        assert row.not_shown_alone is not None
        assert row.not_shown_alone.label == "not a line of its own"
        assert "never shows it as a line by itself" in (
            row.not_shown_alone.sentence
        )
        # It ends in the ACT, not the diagnosis: this row is what a parked
        # Capital One line is grouped against, and the panel is where that
        # group is built.
        assert "Tick it here together with the line" in (
            row.not_shown_alone.sentence
        )

    def test_an_ENVELOPE_HOLDING_PURCHASES_carries_it_too(
        self, app, db, seed_user,
    ):
        """The other member, which has zero live instances and one rule.

        The bank showed the purchases inside this row, not the row.  It is
        graded here rather than left to the payback case because the two reach
        the caveat through DIFFERENT published predicates
        (``settles_from_entries`` and ``repays_card_spend``), and a caveat that
        happened to cover only one of them would read as covering both.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        a_purchase(seed_user, envelope, amount="25.00")
        db.session.flush()

        row = _candidate(seed_user, envelope.id, RowKind.TRANSACTION)

        assert row.not_shown_alone is not None

    def test_an_ORDINARY_bill_carries_NONE(self, app, db, seed_user):
        """The discriminating half: a bill IS a line of its own.

        Without this the caveat could be returned unconditionally and every
        assertion above would still pass -- which would withdraw the panel's
        alarm from every row on it, including the payments the bank really did
        fail to make.
        """
        txn = a_transaction(seed_user, name="Electricity", amount="180.00")
        db.session.flush()

        row = _candidate(seed_user, txn.id, RowKind.TRANSACTION)

        assert row.not_shown_alone is None

    def test_a_PURCHASE_carries_NONE(self, app, db, seed_user):
        """A debit swipe is exactly what the statement shows line by line."""
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(seed_user, envelope, amount="25.00")
        db.session.flush()

        row = _candidate(seed_user, purchase.id, RowKind.PURCHASE)

        assert row.not_shown_alone is None

    def test_an_INCOME_ALLOWANCE_carries_NONE_and_that_is_deliberate(
        self, app, db, seed_user,
    ):
        """The KNOWN GAP, pinned so it is a decision rather than a surprise.

        The developer's ``Phone Allowance`` (`$39.54`) and ``Health Insurance
        Allowance`` (`$100.00`) arrive INSIDE one payroll deposit, so the bank
        never shows them separately either -- and no fact on the row says so.
        The app therefore cannot prove it, and the caveat is not claimed: the
        alternative was a list of row names, which is the allowlist this
        project removes rather than writes.  They stay under the panel's alarm
        caption, and the exception queue (``bank_import:X-gf``) is where being
        buried among 67 rows is answered.
        """
        allowance = a_transaction(
            seed_user, name="Phone Allowance", amount="39.54", income=True,
        )
        db.session.flush()

        row = _candidate(seed_user, allowance.id, RowKind.TRANSACTION)

        assert row.states_own_figure is True
        assert row.not_shown_alone is None


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
    :data:`~app.services.statement_match._pairing.DAY_WINDOW` days past their
    purchase day, so a point at the bound put every one out of reach of its own
    bank line -- and the import recorded **50 duplicate purchases worth
    `$3,590.00`** rather than matching what the app already held.

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
                settle_day_basis=SettledDayBasisEnum.ASSERTED,
            )
            db.session.commit()

            row = _candidate(seed_user, purchase.id, RowKind.PURCHASE)

            assert row is not None
            assert row.settle_day_basis is SettledDayBasisEnum.ASSERTED
            # Made on the 1st, asserted for the 31st: the money moved somewhere
            # in those 30 days and the app cannot say where.
            assert row.expected_window == (made_on, asserted_for)

    def test_a_reconciled_TRANSACTION_still_carries_only_its_settle_day(
        self, app, seed_user,
    ):
        """A BILL ticked on the panel keeps its point -- developer decision.

        The panel stamps a bill exactly as it stamps a purchase, so the
        argument for widening both is the same.  **The evidence is not, and
        neither is the risk** (developer decision 2026-08-22, after two
        independent adversarial reviews): ``budget.transactions`` carries zero
        reconciled rows, so that arm would ship on argument alone -- and a
        purchase has a database floor where a bill has only its pay-period
        start, which ``expected_window`` refuses to read as a point precisely
        because it is a budgeting fact rather than an observation.

        This asserts the SPAN a widened bill would open, so the case that made
        the decision cannot come back silently.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            asserted_for = period.end_date + timedelta(days=30)
            assertion = an_assertion(seed_user, observed_on=asserted_for)
            txn = a_transaction(
                seed_user, name="Electricity", settled_on=asserted_for,
                status=StatusEnum.DONE, reconciled_by=assertion,
                settle_day_basis=SettledDayBasisEnum.ASSERTED,
            )
            db.session.commit()

            row = _candidate(seed_user, txn.id, RowKind.TRANSACTION)

            assert row is not None
            # The FACT still travels -- it is the window rule that declines to
            # act on it -- so turning the arm on later is one predicate, not a
            # re-derivation.
            assert row.settle_day_basis is SettledDayBasisEnum.ASSERTED
            assert row.expected_window == (asserted_for, asserted_for)
            # What widening would have opened: a span in which
            # ``_days_outside`` scores every day zero, so same-amount lines
            # months apart all become legal top-ranked pairings.
            would_have_spanned = (asserted_for - period.start_date).days
            assert would_have_spanned > 2 * DAY_WINDOW

    def test_a_row_settled_WITHOUT_a_tick_still_carries_the_point(
        self, app, seed_user,
    ):
        """The other half of the partition, so the span is not the default.

        A statement match stamps the bank's own posting day and releases the
        link (ruling **R-FL**), which is precisely the row whose day IS an
        observation -- widening that one would re-admit the loose matching
        ``expected_window`` exists to refuse.

        **The row SAYS ``observed`` rather than being read as one because it
        carries no link** (plan step **X-az**).  That inference is finding
        **N-332**, and it could not see the third case at all: a day the owner
        typed carries no link either and is not an observation.
        """
        with app.app_context():
            settled_on = seed_user["bootstrap_period"].start_date
            txn = a_transaction(
                seed_user, name="Electricity", settled_on=settled_on,
                status=StatusEnum.DONE,
                settle_day_basis=SettledDayBasisEnum.OBSERVED,
            )
            db.session.commit()

            row = _candidate(seed_user, txn.id, RowKind.TRANSACTION)

            assert row is not None
            assert row.settle_day_basis is SettledDayBasisEnum.OBSERVED
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
                settle_day_basis=SettledDayBasisEnum.ASSERTED,
            )
            db.session.commit()

            row = _candidate(seed_user, txn.id, RowKind.TRANSACTION)

            assert row is not None
            assert row.settle_day_basis is SettledDayBasisEnum.ASSERTED
            # expected_on (the later period's start) is AFTER settled_on, so
            # the span would be inverted; the point stands instead.
            assert later.start_date > asserted_for
            assert row.expected_window == (asserted_for, asserted_for)


class TestEveryOFFEREDRowCanCarryItsOwnTokenBack:
    """The screen must not render a tick the schema will refuse.

    Plan step ``bank_import:X-f6d-3``.  A candidate's reviewed state travels
    to the browser as one string and comes back through
    :meth:`~app.services.statement_match.ReviewedRow.from_token`, whose figure
    pattern bounds what it will read: twelve integer digits and six decimal
    places.  Every priced candidate descends from ``Numeric(12, 2)`` or from
    ``round_money`` today, so there is real headroom -- **but nothing in the
    tree fails if that stops being true**, and the failure would be silent at
    render time and total at Apply: the token renders, the schema refuses it,
    and that proposal can never be accepted from a browser at all.

    Named by adversarial security review 2026-08-23, which measured the
    round trip over 100 emitted tokens and asked for a standing control rather
    than a one-off measurement.
    """

    def test_every_priced_candidate_round_trips_through_its_token(
        self, app, db, seed_user,
    ):
        """Over the real offer set, across both row kinds and both signs."""
        # The WIDEST and NARROWEST figures the schema can hold, plus both
        # signs: ``Numeric(12, 2)`` tops out at ten integer digits, and a
        # `$0.01` purchase is the other end.  A fixture of ordinary `$180.00`
        # rows would round-trip whatever the pattern said.
        envelope = a_transaction(
            seed_user, name="Groceries", amount="1234567890.12",
            is_envelope=True,
        )
        a_purchase(seed_user, envelope, amount="0.01")
        a_transaction(seed_user, name="Paycheck", amount="2473.38",
                      income=True)
        db.session.flush()
        calendar = pay_calendar.calendar_for(seed_user["user"].id)

        offered = candidates_for(
            seed_user["account"].id, calendar, a_basis(seed_user),
        ).rows

        assert offered, "the fixture offered nothing, so this graded nothing"
        kinds = {row.kind for row in offered}
        assert kinds == set(RowKind), (
            f"only {kinds} were exercised; a kind whose builder forgot the "
            "revision would not be graded here"
        )
        for row in offered:
            reviewed = as_reviewed(row)
            assert ReviewedRow.from_token(reviewed.token) == reviewed, (
                f"{row.label!r} at {row.cash_amount} emits a token its own "
                "reader refuses, so its tick can never be accepted"
            )


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
