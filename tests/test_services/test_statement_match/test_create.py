"""The CREATE door: a bank line the app has no row for BECOMES a purchase.

Plan step **bank_import:X-f6a-3b**, ruling **R-FS**'s third shape.  **It MOVES
MONEY, and differently from its sibling**: accepting a match re-dates a movement
the app already held, and recording a line here ADDS one it did not have at all.

Measured on the developer's own 2026-08-16 statement against a 2026-08-18
production clone: after every proposal the matcher offers, **91 unmatched
outflows** remain that no match can ever explain, 74 of them card swipes worth
`$3,383.49` -- the app records a
period's groceries as one envelope and the bank records every swipe.

**What the door promises, and what each class below pins:**

* the account's recorded outflow GROWS by exactly what the bank took;
* the purchase is born carrying BOTH of the bank's days (**R-FW**);
* the line stops being unexplained, through the SAME match door every other
  correspondence goes through (**R-FT**, **R-FV**);
* the destination set the screen offers is the set the door will accept -- an
  envelope it does not offer is refused, not silently accepted;
* a new envelope is CLOSED from its own purchases, because a projected row in a
  past period is carry-forward bait.

Every refusal below is a FIRING CONTROL, written to fail if the refusal were
deleted.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import SettlementBasisEnum, StatusEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.category import Category
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import (
    balance_at,
    pay_calendar,
    statement_match,
    status_seam,
    transaction_service,
)
from app.services.balance_at import BalanceContext
from app.services.cash_ledger import amount_basis
from app.services.scenario_resolver import require_baseline_scenario
from app.services.statement_match import NewEnvelope, PurchaseCreation
from tests._test_helpers import settlement_if_settling

# Pylint: protected-access -- MintedEnvelopes is an internal collaboration
# between two PRIVATE modules of this package and has no importer outside
# it, so exporting it would be the surface rule 13 forbids; a test for a
# module reaches into it, which is the allowance every sibling here takes.
from app.services.statement_match import _create  # pylint: disable=protected-access

from ._builders import (
    a_bank_line,
    a_later_period,
    a_purchase,
    a_scope,
    a_transaction,
    an_import,
)


def _closed_from_purchases(seed_user, *, name="Groceries", amount="500.00",
                           purchase="120.00", period=None):
    """Return an envelope CLOSED at the sum of its own purchases.

    The state 29 of the developer's 37 closed envelopes are in, and the only
    settled state a new purchase may be added to: a ``purchases`` settlement
    stores no figure, so the row's cost IS its entries and a new one raises it
    by exactly its own amount.

    Args:
        seed_user: The seeded user bundle.
        name: The envelope's name.
        amount: Its budget.
        purchase: The one purchase it closes at.
        period: The pay period to file it under.

    Returns:
        The settled :class:`~app.models.transaction.Transaction`.
    """
    envelope = a_transaction(
        seed_user, name=name, amount=amount, is_envelope=True, period=period,
    )
    a_purchase(seed_user, envelope, amount=purchase, description="Walmart")
    transaction_service.settle_from_entries(envelope)
    db.session.flush()
    return envelope



def _offerable(seed_user):
    """Return the destinations the screen may offer RIGHT NOW.

    Two producers rather than one since plan step **X-f6a-3c-2**, and the split
    is the point:
    :func:`~app.services.statement_match.destinations_for` answers what the
    account COULD offer, which does not change while a review pass runs, and
    :func:`~app.services.statement_match.matched_subjects` answers what a match
    has already claimed, which is exactly what the pass changes.  A screen and
    a write door both narrow the first by the second, each against the claims
    it read for itself -- which is what stops a shared, once-derived offer set
    handing a pass's fourth item an envelope its third has just matched.

    Args:
        seed_user: The seeded user bundle.

    Returns:
        The offerable :class:`~app.services.statement_match.PurchaseDestination`
        values.
    """
    return statement_match.unmatched_destinations(
        statement_match.destinations_for(
            seed_user["user"].id, seed_user["account"].id,
        ),
        statement_match.matched_subjects(seed_user["account"].id),
    )



def _balance_on(seed_user, day):
    """Return the checking account's balance as of *day*."""
    ctx = BalanceContext(
        user_id=seed_user["user"].id,
        scenario=seed_user["scenario"], as_of=day,
    )
    return balance_at.balance_at(seed_user["account"], ctx, day)


def _record(seed_user, line, minted=None, **destination):
    """Record *line* as a purchase, into whichever destination is named.

    Args:
        seed_user: The seeded user bundle.
        line: The recorded bank line to record.
        minted: What this REQUEST has already created.  ``None`` gives each
            call its OWN empty registry, which is the HAND path -- one line,
            one request -- and is what every case here means unless it says
            otherwise.  A case about a SWEEP passes one registry to several
            calls, because that is what one press is.
        **destination: The ``PurchaseCreation`` destination fields.

    Returns:
        The :class:`~app.services.statement_match.CreatedPurchase`.
    """
    return statement_match.create_purchase_from_line(
        PurchaseCreation(
            line_id=line.id,
            **destination,
        ),
        # DERIVED HERE, so every call sees the rows this test has staged.
        a_scope(seed_user),
        minted if minted is not None else _create.MintedEnvelopes.none_yet(),
    )


class TestRecordingALineAddsTheMovement:
    """The money property: the app now holds what the bank showed."""

    def test_the_balance_falls_by_what_the_bank_took(
        self, app, db, seed_user,
    ):
        """A movement the app did not have is a movement the app now has.

        This is the whole point of the step and the reason it is marked MOVES
        MONEY.  Accepting a match cannot do this -- it only re-dates a row the
        app already held -- which is why 74 of the developer's own lines
        survive every proposal.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(
                seed_user, statement, amount="-57.96", posted_on=day,
                description="POINT OF SALE DEBIT L340 WAL-MART (Walmart)",
            )
            before = _balance_on(seed_user, day + timedelta(days=2))

            _record(seed_user, line, transaction_id=envelope.id)
            db.session.flush()

            after = _balance_on(seed_user, day + timedelta(days=2))
            assert after == before - Decimal("57.96")

    def test_the_envelope_s_recorded_cost_grows_by_the_purchase(
        self, app, db, seed_user,
    ):
        """A ``purchases`` close IS its entries, so the row cost more.

        Not a side effect to tolerate: the bank is the evidence that this
        envelope paid for something the records did not name.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user, purchase="120.00")
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(seed_user, statement, amount="-57.96",
                               posted_on=day)

            _record(seed_user, line, transaction_id=envelope.id)
            db.session.flush()
            db.session.expire(envelope)

            assert envelope.settled_amount is None, (
                "a purchases settlement stores no figure"
            )
            assert sum(
                (entry.amount for entry in envelope.entries), Decimal("0"),
            ) == Decimal("177.96")

    def test_the_purchase_carries_BOTH_of_the_banks_days(
        self, app, db, seed_user,
    ):
        """Ruling **R-FW**: a purchase has a budget clock and a cash clock.

        The day it was MADE is what the bank states inside a card line's
        description (182 of the developer's 361 lines); the day it POSTED is
        when the money left.  Both are the bank's, and both are written in one
        ``create_entry`` call -- so a purchase the bank has already taken never
        exists, even briefly, as an outstanding one.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            statement = an_import(seed_user)
            posted = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(
                seed_user, statement, amount="-57.96", posted_on=posted,
                transaction_on=posted - timedelta(days=3),
            )

            recorded = _record(seed_user, line, transaction_id=envelope.id)
            db.session.flush()

            entry = db.session.get(TransactionEntry, recorded.entry_id)
            assert entry.settled_on == posted
            assert entry.purchased_on == posted - timedelta(days=3)
            assert recorded.made_on == posted - timedelta(days=3)

    def test_a_line_stating_no_made_day_takes_the_posting_day(
        self, app, db, seed_user,
    ):
        """179 of 361 lines state none, so this is the majority arm.

        The posting day is the tightest bound a source stating nothing
        supports: money cannot clear before it moves.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            statement = an_import(seed_user)
            posted = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(seed_user, statement, amount="-57.96",
                               posted_on=posted, transaction_on=None)

            recorded = _record(seed_user, line, transaction_id=envelope.id)
            db.session.flush()

            entry = db.session.get(TransactionEntry, recorded.entry_id)
            assert entry.purchased_on == posted
            assert entry.settled_on == posted

    def test_the_line_stops_being_unexplained(self, app, db, seed_user):
        """Recorded through the SAME match door as every correspondence.

        Ruling **R-FT**'s table and ruling **R-FV**'s identity-only rule are
        not re-implemented here: the door builds a
        :class:`~app.services.statement_match.MatchSubmission` and hands it to
        :func:`~app.services.statement_match.accept_match`, so a re-import
        cannot re-offer the line and an undo has something to delete.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(seed_user, statement, amount="-57.96",
                               posted_on=day)
            before = statement_match.review_set(a_scope(seed_user))
            assert line.id in {ln.line_id for ln in before.unmatched}

            recorded = _record(seed_user, line, transaction_id=envelope.id)
            db.session.flush()

            after = statement_match.review_set(a_scope(seed_user))
            assert line.id not in {ln.line_id for ln in after.unmatched}
            assert recorded.match_id in {
                group.match_id for group in after.accepted
            }


class TestTheNewEnvelopeArm:
    """A line that fits no envelope gets one, rather than going unrecorded.

    Measured on the developer's own data: the 2026-03-26 period holds three
    envelopes and all three closed at a fixed figure, so 8 lines worth
    `$662.13` have no existing destination at all -- and several merchants
    (Lowe's, a parks fee, two subscription services) have no envelope in any
    period.
    """

    def test_it_creates_an_ENVELOPE_and_puts_the_purchase_in_it(
        self, app, db, seed_user,
    ):
        """An envelope, never a bare row.

        A plain transaction would be a budget line that is also its own single
        payment, so the next statement line for the same merchant would have
        nowhere to go.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(
                seed_user, statement, amount="-31.41", posted_on=day,
                description="POINT OF SALE DEBIT L340 LOWES #00907 (Lowe's)",
            )

            recorded = _record(seed_user, line, new_envelope=NewEnvelope(
                name="Lowe's",
                category_id=seed_user["categories"]["Groceries"].id,
            ))
            db.session.flush()

            envelope = db.session.get(Transaction, recorded.transaction_id)
            assert envelope.tracks_purchases
            assert envelope.name == "Lowe's"
            assert envelope.estimated_amount == Decimal("0.00")
            assert recorded.envelope_created is True
            assert [entry.id for entry in envelope.entries] == [
                recorded.entry_id,
            ]

    def test_the_new_envelope_is_CLOSED_from_its_purchases(
        self, app, db, seed_user,
    ):
        """Left Projected it would be carry-forward BAIT.

        ``carry_forward_unpaid`` moves every projected row out of a source
        period and settles it, so an envelope budgeting `$0.00` against a
        purchase already spent would roll a NEGATIVE leftover into a later
        period.  Closing it from its own entries records the figure the bank
        stated and reserves nothing.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(seed_user, statement, amount="-31.41",
                               posted_on=day)

            recorded = _record(seed_user, line, new_envelope=NewEnvelope(
                name="Lowe's",
                category_id=seed_user["categories"]["Groceries"].id,
            ))
            db.session.flush()

            envelope = db.session.get(Transaction, recorded.transaction_id)
            assert envelope.status.is_settled
            assert envelope.settled_on == day
            assert envelope.settled_basis_id == ref_cache.settlement_basis_id(
                SettlementBasisEnum.PURCHASES,
            )
            assert envelope.settled_amount is None

    def test_it_lands_in_the_period_of_the_day_it_was_MADE(
        self, app, db, seed_user,
    ):
        """The budget clock places the row, not the clearing day.

        A swipe made on one period's last day and posted on the next period's
        first belongs to the budget it was made under -- and filing it by the
        posting day would raise ``check_purchase_date_in_period``'s
        out-of-period warning on a row this door had just built.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="-31.41",
                posted_on=period.end_date + timedelta(days=1),
                transaction_on=period.end_date,
            )

            recorded = _record(seed_user, line, new_envelope=NewEnvelope(
                name="Lowe's",
                category_id=seed_user["categories"]["Groceries"].id,
            ))
            db.session.flush()

            envelope = db.session.get(Transaction, recorded.transaction_id)
            assert envelope.pay_period_id == period.id

    def test_the_purchase_is_named_for_the_MERCHANT(
        self, app, db, seed_user,
    ):
        """The merchant the bank NAMED, not its whole line.

        The app's own purchases are called "Walmart" and "Food Lion"; a row
        called ``POINT OF SALE DEBIT L340 DATE 08-13 Amazon.com*5H2RA5V...``
        would be the only one in the entries list nobody can read.  The bank's
        full wording is not lost -- it stays on the statement line, which the
        match ties to this purchase.

        The merchant is the RECORDED column since plan step X-f6a-3d, not a
        parse of the description: what reads it here is
        :func:`~app.services.statement_match.merchant_label`, and what fills it
        is the adapter (graded in ``test_secu_csv``).
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(
                seed_user, statement, amount="-57.96", posted_on=day,
                description=(
                    "POINT OF SALE DEBIT L340 DATE 08-13 "
                    "Amazon.com*5H2RA5VAmzn.com/bil (Amazon)"
                ),
                merchant="Amazon",
            )

            recorded = _record(seed_user, line, transaction_id=envelope.id)
            db.session.flush()

            entry = db.session.get(TransactionEntry, recorded.entry_id)
            assert entry.description == "Amazon"

    def test_a_line_naming_no_merchant_is_named_by_its_DESCRIPTION(
        self, app, db, seed_user,
    ):
        """The LABEL is total even though the key is not.

        A source that names no merchant records ``NULL``, which keys no
        destination policy -- but ``transaction_entries.description`` is NOT
        NULL and this door calls ``create_entry`` directly, so the name has to
        come from somewhere.  Falling back to the description is the honest
        answer, and it is why the label and the key are different readers:
        SECU's OFX truncates 326 of 361 descriptions to the same 32
        characters, which is a usable name and a ruinous key.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(seed_user, statement, amount="-57.96",
                               posted_on=day, description="ACH DEBIT GEICO",
                               merchant=None)

            recorded = _record(seed_user, line, transaction_id=envelope.id)
            db.session.flush()

            entry = db.session.get(TransactionEntry, recorded.entry_id)
            assert entry.description == "ACH DEBIT GEICO"


class TestWhatTheCreateDoorRefuses:
    """Every refusal, each written to fail if the refusal were deleted."""

    def test_an_INFLOW_line_is_refused(self, app, db, seed_user):
        """A purchase is an expense.

        ``ck_transaction_entries_positive_amount`` is the backstop; 16 of the
        developer's own unexplained lines are inflows, three of them card
        refunds, so this is the ordinary shape rather than a crafted request.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="2573.42",
                posted_on=seed_user["bootstrap_period"].start_date,
            )

            with pytest.raises(ValidationError, match="money LEAVING"):
                _record(seed_user, line, transaction_id=envelope.id)
            assert db.session.query(TransactionEntry).count() == 1

    def test_an_ALREADY_MATCHED_line_is_refused(self, app, db, seed_user):
        """Refused HERE rather than by the unique index after a write.

        ``uq_statement_match_members_line`` refuses the second act either way,
        and so does ``accept_match``'s own ``_load_lines`` -- but BOTH arrive
        after ``create_entry`` has already staged a purchase.  So the assertion
        that matters is the COUNT, not the raise: deleting this refusal leaves
        an orphan purchase behind on a page the owner merely double-submitted.
        Measured -- without the count assertion this test passed with the
        refusal deleted.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(seed_user, statement, amount="-57.96",
                               posted_on=day)
            _record(seed_user, line, transaction_id=envelope.id)
            db.session.flush()
            after_first = db.session.query(TransactionEntry).count()

            with pytest.raises(ValidationError, match="already matched"):
                _record(seed_user, line, transaction_id=envelope.id)

            assert db.session.query(TransactionEntry).count() == after_first, (
                "a refused second submission staged a purchase anyway"
            )

    def test_a_line_on_ANOTHER_account_is_refused(
        self, app, db, seed_user, seed_second_user,
    ):
        """The scope is the account, and a statement is one bank's record of one.

        Reaching another account's line by id would book money against a
        statement that never showed it.

        **The COUNT is the control and the raise is not**, for the reason the
        already-matched test states: ``accept_match`` scopes its own lines too,
        so deleting this clause still raises -- after ``create_entry`` has put
        a purchase from a foreign statement onto this owner's envelope.
        Measured: without the count assertion this test passed with the clause
        deleted.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            statement = an_import(seed_second_user)
            line = a_bank_line(
                seed_second_user, statement, amount="-57.96",
                posted_on=seed_second_user["bootstrap_period"].start_date,
            )
            before = db.session.query(TransactionEntry).count()

            with pytest.raises(ValidationError, match="no longer on this"):
                _record(seed_user, line, transaction_id=envelope.id)

            assert db.session.query(TransactionEntry).count() == before, (
                "a foreign statement line staged a purchase on this account"
            )

    def test_an_envelope_the_screen_could_not_OFFER_is_refused(
        self, app, db, seed_user,
    ):
        """One scope for the reader and the writer.

        A row closed at a FIXED figure cannot record a new purchase: its gross
        cannot rise, so ``settled_cash_leg`` would subtract money the gross
        never held.  Measured on a production clone, `-163.95` became
        `+203.67` -- an expense row publishing an inflow -- while the anchor
        true-up moved `$0.00`, so the spending was never recorded at all.
        """
        with app.app_context():
            fixed = a_transaction(
                seed_user, name="Mint Mobile", amount="132.69",
                is_envelope=True, status=StatusEnum.DONE,
                settled_on=seed_user["bootstrap_period"].start_date,
            )
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="-57.96",
                posted_on=seed_user["bootstrap_period"].start_date,
            )
            offered = _offerable(seed_user)
            assert fixed.id not in {d.transaction_id for d in offered}

            with pytest.raises(ValidationError, match="not one this purchase"):
                _record(seed_user, line, transaction_id=fixed.id)
            assert db.session.query(TransactionEntry).count() == 0

    def test_ANOTHER_USERS_category_is_refused(
        self, app, db, seed_user, seed_second_user,
    ):
        """The IDOR probe every create door in this project performs.

        A foreign ``category_id`` satisfies the foreign key -- the row exists
        -- and would link another user's category onto this owner's budget.
        """
        with app.app_context():
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="-57.96",
                posted_on=seed_user["bootstrap_period"].start_date,
            )
            foreign = (
                db.session.query(Category)
                .filter(Category.user_id == seed_second_user["user"].id)
                .first()
            )

            with pytest.raises(ValidationError, match="not one of yours"):
                _record(seed_user, line, new_envelope=NewEnvelope(
                    name="Lowe's", category_id=foreign.id,
                ))
            assert db.session.query(Transaction).filter(
                Transaction.name == "Lowe's",
            ).count() == 0

    def test_naming_BOTH_destinations_is_refused(self, app, db, seed_user):
        """A purchase has exactly one parent.

        Stated as a refusal rather than a precedence rule: a door that silently
        preferred an arm would record something the owner did not ask for.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="-57.96",
                posted_on=seed_user["bootstrap_period"].start_date,
            )

            with pytest.raises(ValidationError, match="exactly one place"):
                _record(
                    seed_user, line, transaction_id=envelope.id,
                    new_envelope=NewEnvelope(
                        name="Lowe's",
                        category_id=seed_user["categories"]["Groceries"].id,
                    ),
                )

    def test_naming_NEITHER_destination_is_refused(self, app, db, seed_user):
        """The same rule's other half."""
        with app.app_context():
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="-57.96",
                posted_on=seed_user["bootstrap_period"].start_date,
            )

            with pytest.raises(ValidationError, match="exactly one place"):
                _record(seed_user, line)

    def test_a_line_MADE_after_it_POSTED_is_refused(self, app, db, seed_user):
        """Money does not leave an account before it is spent.

        **The rule is ``entry_service``'s and this door does not restate it.**
        A first version added its own ``_reject_impossible_days`` so the
        message could name the line rather than the purchase, and so the
        refusal preceded the new-envelope write.  Both reasons failed: the SECU
        CSV reader provably cannot produce the pair (it resolves the stated day
        to the most recent one at or before the posting day), so the guard was
        rule 13's speculative shape; and the door already depends on the
        request-level rollback for every other ``entry_service`` refusal, so
        ordering bought nothing it did not already owe.  Removed after
        adversarial design review 2026-08-19.

        What is graded here is that such a line becomes no purchase and leaves
        no budget line behind -- which is the property, whichever rule states
        it.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(
                seed_user, statement, amount="-57.96", posted_on=day,
                transaction_on=day + timedelta(days=1),
            )

            with pytest.raises(
                ValidationError, match="cannot reach your bank before",
            ):
                _record(seed_user, line, new_envelope=NewEnvelope(
                    name="Lowe's",
                    category_id=seed_user["categories"]["Groceries"].id,
                ))
            db.session.rollback()
            assert db.session.query(Transaction).filter(
                Transaction.name == "Lowe's",
            ).count() == 0

    def test_a_day_NO_PAY_PERIOD_covers_is_refused(self, app, db, seed_user):
        """130 of the developer's own 361 lines predate their first payday.

        There is no budget for such a purchase to belong to, and
        ``transactions.pay_period_id`` is NOT NULL -- so the honest answer is a
        sentence naming the day rather than a row filed under the nearest
        period, which would misplace real money.
        """
        with app.app_context():
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="-57.96",
                posted_on=(
                    seed_user["bootstrap_period"].start_date
                    - timedelta(days=400)
                ),
            )

            with pytest.raises(ValidationError, match="No pay period covers"):
                _record(seed_user, line, new_envelope=NewEnvelope(
                    name="Lowe's",
                    category_id=seed_user["categories"]["Groceries"].id,
                ))


class TestTheDestinationMustBeInTheLINEsOwnPeriod:
    """The security guard X-f6a-3c-2 added, which had NO test at all.

    Measured by adversarial test-quality review 2026-08-19: deleting
    ``if destination.pay_period_id == pay_period_id`` from
    ``_create._existing_envelope`` left the whole suite green.  A security fix
    with no test is a security fix that gets refactored away.

    What the clause stops, in its own docstring's words: a crafted request
    filing a swipe into a Groceries envelope eighteen months forward, or
    raising a closed past envelope's recorded cost in a period the line has
    nothing to do with.  ``destinations_for`` deliberately returns budget lines
    across EVERY period on the account -- the screen renders only the line's
    own -- so this clause is the whole of what keeps the door's set the
    screen's set.
    """

    def test_an_envelope_in_ANOTHER_period_is_refused(
        self, app, db, seed_user,
    ):
        """The crafted request, refused, with nothing written."""
        with app.app_context():
            other = a_later_period(seed_user)
            elsewhere = a_transaction(
                seed_user, name="Groceries", amount="500.00",
                is_envelope=True, period=other,
            )
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="-57.96",
                posted_on=seed_user["bootstrap_period"].start_date,
            )

            # It IS offerable -- just not for THIS line.
            assert elsewhere.id in {
                d.transaction_id for d in _offerable(seed_user)
            }

            with pytest.raises(ValidationError, match="not one this purchase"):
                _record(seed_user, line, transaction_id=elsewhere.id)

            db.session.flush()
            assert elsewhere.entries == []

    def test_the_SAME_envelope_in_the_line_s_own_period_is_accepted(
        self, app, db, seed_user,
    ):
        """The control, without which the case above could pass vacuously.

        Same shape, same door, same figures -- only the period differs.  A
        refusal that fired for any other reason would fail here too.
        """
        with app.app_context():
            here = a_transaction(
                seed_user, name="Groceries", amount="500.00", is_envelope=True,
            )
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="-57.96",
                posted_on=seed_user["bootstrap_period"].start_date,
            )

            recorded = _record(seed_user, line, transaction_id=here.id)

            db.session.flush()
            assert recorded.transaction_id == here.id
            assert [e.amount for e in here.entries] == [Decimal("57.96")]


class TestWhatTheScreenMayOFFER:
    """``destinations_for`` -- the set the door will accept, and nothing more.

    Every clause is one of ``create_entry``'s or ``accept_match``'s, so the
    screen cannot render a chooser whose submission is refused.  That failure
    -- an Accept button that can never succeed -- is one this arc has now
    found three times.
    """

    @staticmethod
    def _offered(seed_user):
        """Return the ids the screen may offer for the seeded account."""
        return {
            destination.transaction_id
            for destination in _offerable(seed_user)
        }

    def test_a_PROJECTED_envelope_is_offered(self, app, db, seed_user):
        """The open case: a purchase joins a budget still being spent."""
        with app.app_context():
            envelope = a_transaction(
                seed_user, name="Groceries", amount="500.00", is_envelope=True,
            )
            assert envelope.id in self._offered(seed_user)

    def test_an_envelope_CLOSED_AT_ITS_PURCHASES_is_offered(
        self, app, db, seed_user,
    ):
        """29 of the developer's 37 closed envelopes are in this state.

        Its figure IS its entries, so a new purchase raises what the row cost
        by exactly the figure the bank showed and the row's own cash leg does
        not move.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            assert envelope.id in self._offered(seed_user)

    def test_an_envelope_closed_at_a_FIXED_FIGURE_is_not(
        self, app, db, seed_user,
    ):
        """THE money clause.  See the refusal test for the measurement."""
        with app.app_context():
            envelope = a_transaction(
                seed_user, name="Mint Mobile", amount="132.69",
                is_envelope=True, status=StatusEnum.DONE,
                settled_on=seed_user["bootstrap_period"].start_date,
            )
            assert envelope.settled_basis_id == ref_cache.settlement_basis_id(
                SettlementBasisEnum.DERIVED,
            )
            assert envelope.id not in self._offered(seed_user)

    def test_an_ARCHIVED_envelope_is_not(self, app, db, seed_user):
        """Finding **N-229**: an archived row's purchases are history."""
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            status_seam.apply_status_change(
                envelope, ref_cache.status_id(StatusEnum.SETTLED),
                settlement=settlement_if_settling(
                    envelope, ref_cache.status_id(StatusEnum.SETTLED),
                ),
            )
            db.session.flush()
            assert envelope.id not in self._offered(seed_user)

    def test_a_CANCELLED_envelope_is_not(self, app, db, seed_user):
        """A cancelled row records no cash, so a purchase under it posts none."""
        with app.app_context():
            envelope = a_transaction(
                seed_user, name="Strawberry Picking", amount="60.00",
                is_envelope=True, status=StatusEnum.CANCELLED,
            )
            assert envelope.id not in self._offered(seed_user)

    def test_a_NON_ENVELOPE_row_is_not(self, app, db, seed_user):
        """``create_entry`` refuses a parent that does not track purchases."""
        with app.app_context():
            bill = a_transaction(seed_user, name="Electricity", amount="180.00")
            assert bill.id not in self._offered(seed_user)

    def test_an_INCOME_row_is_not(self, app, db, seed_user):
        """Money coming in is not a purchase."""
        with app.app_context():
            deposit = a_transaction(
                seed_user, name="Paycheck", amount="2473.38", income=True,
                is_envelope=True,
            )
            assert deposit.id not in self._offered(seed_user)

    def test_an_envelope_ALREADY_MATCHED_to_a_line_is_not(
        self, app, db, seed_user,
    ):
        """``accept_match`` refuses a purchase whose parent another match names.

        **The refusal is WIDER than this door needs** (finding **N-317**), and
        X-f6a-3c-1 re-measured how much: every destination this clause uniquely
        removes is one a new purchase moves by `$0.00`, because a match settles
        the envelope it names and only an envelope that settles FROM ITS
        ENTRIES lands on the purchases basis the money clause admits.  It is
        left whole anyway on the developer's ruling of 2026-08-19 -- a money
        guard is not narrowed for a `$0.00` benefit -- so the screen stops
        offering it and the width stays a finding rather than a fix.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            statement = an_import(seed_user)
            owner_id = seed_user["user"].id
            leg = statement_match.candidates_for(
                seed_user["account"].id,
                pay_calendar.calendar_for(owner_id),
                amount_basis(owner_id, require_baseline_scenario(owner_id).id),
            )
            worth = next(
                row.cash_amount for row in leg.rows
                if row.row_id == envelope.id
                and row.kind is statement_match.RowKind.TRANSACTION
            )
            line = a_bank_line(
                seed_user, statement, amount=str(worth),
                posted_on=seed_user["bootstrap_period"].start_date,
            )
            statement_match.accept_match(
                statement_match.MatchSubmission(
                    line_ids=frozenset({line.id}),
                    transaction_ids=frozenset({envelope.id}),
                    entry_ids=frozenset(),
                ),
                a_scope(seed_user),
            )
            db.session.flush()

            assert envelope.id not in self._offered(seed_user)
