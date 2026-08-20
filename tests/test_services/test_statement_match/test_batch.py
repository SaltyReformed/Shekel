"""Applying a whole reviewed pass in one request -- plan step X-f6a-3c-2.

Finding **N-306**.  The review screen offers two acts and, until this step,
each was its own request through its own money door: 215 round trips on the
developer's own statement, each paying 3.593 s of ``candidates_for`` before it
wrote a row.  This module is about what changed and what must not have.

**The three properties that carry the money risk:**

1. **Isolation.**  A refused item leaves NOTHING behind and the rest still
   land.  Measured on the developer's own data, 5 of 124 proposals refuse
   today and will keep refusing (a settled credit-card payback whose derived
   figure has drifted), so a pass that failed whole would lose 119 good
   corrections to one divergence.
2. **Freshness.**  One derivation serves the whole pass, so an item must not be
   handed a row or an envelope an EARLIER item in the same pass has claimed.
   That is what :func:`~app.services.statement_match.matched_subjects` being
   re-read per act buys, and it is the arm a snapshot would silently break.
3. **Failing loud.**  A ``PostingError`` is a broken ledger invariant rather
   than one item's refusal, so it takes the whole request down.

**What the collision cases here fix in place is a REAL number**: 4 envelopes on
the developer's own statement are both named by a proposal and offered as a
destination, so 15 of the 91 creatable lines aim at one.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    AmountSourceEnum,
    SettlementBasisEnum,
    StatusEnum,
    TxnTypeEnum,
)
from app.exceptions import ValidationError
from app.extensions import db as _db
from app.models.statement_match import StatementMatch
from app.models.transaction import Transaction
from app.services import balance_at, cash_ledger, statement_match
from app.services.posting_reads import PostingError
from app.services.transaction_service import _settle as transaction_settle
from app.services.transfer_service import _settle as transfer_settle
from app.services.statement_match import (
    MatchSubmission,
    NewEnvelope,
    PurchaseCreation,
    ReviewedBatch,
)

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


def _batch(seed_user, matches=(), creations=()):
    """Apply one reviewed pass over the seeded account.

    Args:
        seed_user: The seeded user bundle.
        matches: :class:`MatchSubmission` values.
        creations: :class:`PurchaseCreation` values.

    Returns:
        The :class:`~app.services.statement_match.BatchOutcome`.
    """
    return statement_match.apply_reviewed(
        ReviewedBatch(
            matches=tuple(matches),
            creations=tuple(creations),
        ),
        # DERIVED HERE, so every pass sees the rows this test has staged.  The
        # ROUTE is what builds one in the app; a door that built its own would
        # force its caller to derive a second time for the refusal render.
        a_scope(seed_user),
    )


def _match(seed_user, lines=(), transactions=(), entries=()):
    """Return one match item naming exactly these subjects."""
    return MatchSubmission(
        line_ids=frozenset(line.id for line in lines),
        transaction_ids=frozenset(txn.id for txn in transactions),
        entry_ids=frozenset(entry.id for entry in entries),
    )


def _balance(seed_user, day):
    """Return the checking account's balance as of *day*.

    Args:
        seed_user: The seeded user bundle.
        day: The day to value the account on.

    Returns:
        The balance.
    """
    return balance_at.balance_at(
        seed_user["account"],
        # Built with the day it is asked for, exactly as ``test_create``'s own
        # money cases do.  ``BalanceContext.build`` resolves an ``as_of`` from
        # the CLOCK, so two reads a few statements apart share one -- which
        # answers the same figure whatever the postings did.
        balance_at.BalanceContext(
            user_id=seed_user["user"].id,
            scenario=seed_user["scenario"],
            as_of=day,
        ),
        day,
    )


def _creation(seed_user, line, *, transaction_id=None, new_envelope=None):
    """Return one creation item for *line*."""
    return PurchaseCreation(
        line_id=line.id,
        transaction_id=transaction_id,
        new_envelope=new_envelope,
    )


class TestARefusedItemDoesNotCostTheOthers:
    """The developer's ruling of 2026-08-19, which is the whole failure policy.

    Not a hypothetical: 5 of the developer's own 124 proposals refuse today
    with ``Payback 2457 has settled at 50.80, so it cannot be re-derived to
    49.52``, and every one of the other 119 is a correction worth making.
    """

    @staticmethod
    def _good_and_bad(seed_user):
        """Return (a match that lands, a match that cannot).

        The bad one is UNBALANCED -- the shape finding **N-239** takes on the
        developer's own payroll, where the bank shows `$0.05` more than the
        app's rows sum to.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        good_line = a_bank_line(
            seed_user, statement, amount="-180.00", posted_on=day,
        )
        good_row = a_transaction(
            seed_user, name="Electricity", amount="180.00",
            status=StatusEnum.DONE, settled_on=day + timedelta(days=3),
        )
        bad_line = a_bank_line(
            seed_user, statement, amount="2573.43", posted_on=day,
            sequence_in_group=1,
        )
        bad_row = a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        return (good_line, good_row, bad_line, bad_row)

    def test_the_good_one_lands_and_the_bad_one_is_quoted(
        self, app, db, seed_user,
    ):
        """One pass, two outcomes, and the refusal keeps its own sentence."""
        with app.app_context():
            good_line, good_row, bad_line, bad_row = self._good_and_bad(
                seed_user,
            )

            outcome = _batch(seed_user, matches=[
                _match(seed_user, lines=[bad_line], transactions=[bad_row]),
                _match(seed_user, lines=[good_line], transactions=[good_row]),
            ])

            assert outcome.applied_count == 1
            assert outcome.refused_count == 1
            assert "do not add up" in outcome.refused[0].reason
            assert outcome.refused[0].line_ids == (bad_line.id,)
            assert outcome.applied[0].line_ids == (good_line.id,)

    def test_the_refused_item_wrote_nothing(self, app, db, seed_user):
        """The item that could not be applied left no trace of trying.

        **This refusal fires BEFORE the item writes anything** -- an unbalanced
        match is rejected before any settle verb runs -- so it grades the
        loop's ``continue`` rather than the savepoint.  The savepoint's own
        property, an item that has ALREADY written when it is refused, is
        :meth:`test_a_HALF_APPLIED_group_is_undone` and
        :meth:`test_a_refused_CREATION_takes_its_new_envelope_with_it`.  Stated
        because a test whose name promises the stronger thing is worse than no
        test: both survive removing the savepoint entirely, and only the two
        named here fail.
        """
        with app.app_context():
            _, good_row, bad_line, bad_row = self._good_and_bad(seed_user)

            _batch(seed_user, matches=[
                _match(seed_user, lines=[bad_line], transactions=[bad_row]),
                _match(
                    seed_user,
                    lines=[db.session.query(
                        type(bad_line),
                    ).filter_by(amount=Decimal("-180.00")).one()],
                    transactions=[good_row],
                ),
            ])
            db.session.flush()

            assert bad_row.settled_on is None
            assert good_row.settled_on is not None
            assert db.session.query(StatementMatch).count() == 1

    def test_a_HALF_APPLIED_group_is_undone(self, app, db, seed_user):
        """A match can refuse AFTER it has already moved one of its members.

        The accept door applies each member in turn, and a settle verb can
        refuse on the second -- which is exactly the developer's own 5 refusing
        proposals, where a settled credit-card payback cannot be re-derived.
        Here the shape is narrower and buildable: a purchase whose recorded day
        the bank's stated day would move PAST the day the bank posted, which
        ``entry_service.update_entry`` refuses because money cannot clear
        before it is spent.

        Without a savepoint the first purchase keeps the settle day the refused
        match wrote, so the app records money as having moved on the strength
        of a match it also says was never accepted.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            envelope = a_transaction(
                seed_user, name="Groceries", amount="100.00", is_envelope=True,
            )
            first = a_purchase(
                seed_user, envelope, amount="10.00", description="Aldi",
                purchased_on=day,
            )
            second = a_purchase(
                seed_user, envelope, amount="15.00", description="Kroger",
                purchased_on=day + timedelta(days=3),
            )
            line = a_bank_line(
                seed_user, statement, amount="-25.00", posted_on=day,
                transaction_on=day + timedelta(days=2),
            )

            outcome = _batch(seed_user, matches=[
                _match(seed_user, lines=[line], entries=[first, second]),
            ])
            db.session.flush()

            assert outcome.applied_count == 0
            assert outcome.refused_count == 1
            assert first.settled_on is None, (
                "the first member kept the day a REFUSED match wrote"
            )
            assert second.settled_on is None
            assert db.session.query(StatementMatch).count() == 0

    def test_a_refused_CREATION_takes_its_new_envelope_with_it(
        self, app, db, seed_user,
    ):
        """The arm that stages a budget row BEFORE the refusal can fire.

        A creation stages an envelope, then a purchase, and only then records
        the match -- so an item refused at the last step has a budget line to
        undo.  Without the savepoint that row survives into the commit, and the
        owner gets an envelope for a purchase that was never recorded.

        **The refusal has to fire AFTER the envelope is staged**, or this
        asserts nothing about a savepoint.  The one that does is a line the
        bank says was MADE after it POSTED -- 2 of 361 of the developer's own
        OFX lines are that shape -- because ``entry_service.create_entry``
        refuses a posting day before its purchase day, and by then
        ``_create_envelope`` has already flushed a budget row.  A first draft
        used an INFLOW, which ``_load_line`` refuses before anything at all is
        staged; it passed against a door with no savepoint.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            made_later = a_bank_line(
                seed_user, statement, amount="-12.00", posted_on=day,
                transaction_on=day + timedelta(days=1),
            )
            ordinary = a_bank_line(
                seed_user, statement, amount="-12.00", posted_on=day,
                sequence_in_group=1,
            )
            category = seed_user["categories"]["Groceries"]

            outcome = _batch(seed_user, creations=[
                _creation(
                    seed_user, made_later,
                    new_envelope=NewEnvelope(
                        name="Refused", category_id=category.id,
                    ),
                ),
                _creation(
                    seed_user, ordinary,
                    new_envelope=NewEnvelope(
                        name="Landed", category_id=category.id,
                    ),
                ),
            ])
            db.session.flush()

            assert outcome.applied_count == 1
            assert outcome.refused_count == 1
            assert outcome.refused[0].line_ids == (made_later.id,)
            names = {
                row.name for row in db.session.query(Transaction).all()
            }
            assert "Landed" in names
            assert "Refused" not in names, (
                "the refused item's envelope survived, so the savepoint did "
                "not roll its staged rows back"
            )


class TestOneDerivationStAYSCorrectAcrossThePass:
    """Freshness: what an EARLIER item claims, a later item cannot.

    The scope is derived once for a whole pass -- 3.593 s against 215 acts --
    and holds every row the account could offer, priced.  What it deliberately
    does NOT hold is which of them a match has claimed, because that is exactly
    what the pass changes.  These are the cases that fail against a scope with
    the claims baked in.
    """

    def test_a_second_item_cannot_name_a_row_the_first_matched(
        self, app, db, seed_user,
    ):
        """The same row, twice in one pass.

        ``propose`` partitions its rows, so the screen cannot produce this --
        but a crafted submission can, and ``uq_statement_match_members_
        transaction`` would answer it with an ``IntegrityError`` after the
        first item had already moved a day.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            first = a_bank_line(
                seed_user, statement, amount="-180.00", posted_on=day,
            )
            second = a_bank_line(
                seed_user, statement, amount="-180.00", posted_on=day,
                sequence_in_group=1,
            )
            row = a_transaction(
                seed_user, name="Electricity", amount="180.00",
                status=StatusEnum.DONE, settled_on=day,
            )

            outcome = _batch(seed_user, matches=[
                _match(seed_user, lines=[first], transactions=[row]),
                _match(seed_user, lines=[second], transactions=[row]),
            ])

            assert outcome.applied_count == 1
            assert outcome.refused_count == 1
            assert "no longer available" in outcome.refused[0].reason
            assert db.session.query(StatementMatch).count() == 1

    def test_a_creation_cannot_target_an_envelope_a_match_claimed(
        self, app, db, seed_user,
    ):
        """The collision the developer ruled on, in the order they ruled.

        4 envelopes on the developer's own statement are both named by a
        proposal and offered as a destination for a creatable line, so 15 of
        the 91 lines land here.  Matches run first and the CREATION is refused:
        the proposal explains money the records already hold against a line the
        bank showed, where the line can be re-aimed next pass.

        It also has to be refused for a money reason.  An envelope's cash leg
        already covers its own outstanding purchases, so a match on the
        envelope AND a new purchase inside it would count that purchase twice.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            envelope = a_transaction(
                seed_user, name="Groceries", amount="500.00", is_envelope=True,
            )
            matched_line = a_bank_line(
                seed_user, statement, amount="-500.00", posted_on=day,
            )
            swipe = a_bank_line(
                seed_user, statement, amount="-57.96", posted_on=day,
                sequence_in_group=1,
            )

            outcome = _batch(
                seed_user,
                matches=[_match(
                    seed_user, lines=[matched_line], transactions=[envelope],
                )],
                creations=[_creation(
                    seed_user, swipe, transaction_id=envelope.id,
                )],
            )
            db.session.flush()

            assert outcome.applied_count == 1
            assert outcome.refused_count == 1
            assert "not one this purchase can go into" in (
                outcome.refused[0].reason
            )
            assert outcome.refused[0].line_ids == (swipe.id,)
            assert envelope.entries == []

    def test_a_matched_purchase_blocks_a_later_match_on_its_PARENT(
        self, app, db, seed_user,
    ):
        """The interaction that makes a shared PRICE safe at all.

        A candidate's figure is ``gross - card entries - posted purchases``, so
        the ONLY way one item can move a figure another item names is by
        posting or adding a purchase under it -- which makes the two a parent
        and its own child.  That pairing is refused across matches, and the
        guard reads the database, so each item flushing before the next is
        validated is what keeps the once-derived price true.

        Without it: matching the purchase first drops the envelope's leg by the
        purchase's amount, and the second item would then be accepted against a
        price the app no longer holds.

        **The sentence is the RE-PRICING one, not the double-count one, and
        that is the correct order of refusals.**  Posting the envelope's only
        purchase leaves the envelope worth `$0.00`, and a row worth nothing can
        match no bank line -- so ``resolve_rows`` refuses it before
        ``record_match``'s guard is ever asked.  The guard still owns the OTHER
        direction, where the envelope is matched first and keeps a figure;
        :meth:`test_the_double_count_guard_still_names_itself` is that case.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            envelope = a_transaction(
                seed_user, name="Groceries", amount="100.00", is_envelope=True,
            )
            purchase = a_purchase(
                seed_user, envelope, amount="25.00", purchased_on=day,
            )
            child_line = a_bank_line(
                seed_user, statement, amount="-25.00", posted_on=day,
            )
            parent_line = a_bank_line(
                seed_user, statement, amount="-100.00", posted_on=day,
                sequence_in_group=1,
            )

            outcome = _batch(seed_user, matches=[
                _match(seed_user, lines=[child_line], entries=[purchase]),
                _match(
                    seed_user, lines=[parent_line], transactions=[envelope],
                ),
            ])

            assert outcome.applied_count == 1
            assert outcome.refused_count == 1
            assert "no longer available" in outcome.refused[0].reason
            assert envelope.settled_on is None, (
                "the envelope was matched against a figure the pass had "
                "already moved"
            )


    def test_the_double_count_guard_still_names_itself(
        self, app, db, seed_user,
    ):
        """The other direction, where the specific sentence is the useful one.

        An envelope matched FIRST keeps its figure, so re-pricing has nothing
        to say about the purchase inside it -- and what must refuse the second
        item is the guard that knows WHY: the envelope's cash leg already
        covers its own outstanding purchases, so naming both counts that
        purchase in two terms.  Measured on a production clone at plan step
        X-f6a-2: two matched line-sets worth `-284.33` backed by `-265.69` of
        ledger, the projected balance `$18.64` high.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            envelope = a_transaction(
                seed_user, name="Groceries", amount="300.00", is_envelope=True,
            )
            purchase = a_purchase(
                seed_user, envelope, amount="25.00", purchased_on=day,
            )
            parent_line = a_bank_line(
                seed_user, statement, amount="-25.00", posted_on=day,
            )
            child_line = a_bank_line(
                seed_user, statement, amount="-25.00", posted_on=day,
                sequence_in_group=1,
            )

            outcome = _batch(seed_user, matches=[
                _match(
                    seed_user, lines=[parent_line], transactions=[envelope],
                ),
                _match(seed_user, lines=[child_line], entries=[purchase]),
            ])

            assert outcome.applied_count == 1
            assert outcome.refused_count == 1
            assert "count the same money twice" in outcome.refused[0].reason


class TestASIBLINGWriteCannotBookAgainstAStalePrice:
    """The counterexample that refuted this step's first safety argument.

    That argument was: one act can only move a figure another act names by
    adding a purchase to that row or posting one under it, which makes the two
    an envelope and its own child -- and
    ``_reject_parent_and_its_own_purchase`` refuses exactly that.  **Measured
    false by adversarial financial review 2026-08-19**, with a booked figure.

    ``entry_service.update_entry`` -- which every matched PURCHASE goes through
    -- calls ``entry_credit_workflow.sync_entry_payback``, and that WRITES the
    envelope's CC Payback ``estimated_amount`` down to the sum of its card
    entries.  A payback is a transaction on the SAME account, so it is a
    candidate priced off that column; and it is the purchase's SIBLING under
    one envelope, not its parent, so no guard here can see the relation.

    Against a once-derived price the second act was accepted at the stale
    figure: the ledger booked `$50.00` for a `-$60.00` bank line and the
    account read **`$10.00` high**.  The answer is that every act re-prices the
    rows it names (:func:`~app.services.statement_match.repriced`), which is
    total where an enumeration of sibling writers is one writer from being
    wrong again.

    **Both acts are ordinary screen proposals in the same sweep class**, so one
    click of "tick all that mark a row as having happened" plus Apply submits
    them together.

    **Plan step X-au-i closed the MECHANISM and this case now grades the
    OUTCOME.**  A payback no longer stores ``estimated_amount`` at all -- it
    declares the ``credit_source`` relation and is worth the card spend it
    repays -- so ``sync_entry_payback`` has no column left to write down, and a
    payback can no longer DRIFT from its card entries in the first place.  The
    hand-built drifted fixture this case used is therefore a row the app can no
    longer produce, and staging one would grade an unreachable state -- which is
    the objection its own builder raised about the sibling template.

    Censused before the rewrite, so the narrowing is measured rather than
    assumed: **no act this batch can perform moves a payback's derived price.**
    A matched PURCHASE stamps ``settled_on`` / ``purchased_on`` only
    (``_accept._apply_row``), never ``amount`` or ``is_credit``; a RECORDED
    purchase is created as a debit (``_create`` passes no ``is_credit``).  Both
    are the inputs a payback's derivation reads, and neither door touches them.

    What is unchanged, and what this case still asserts, is the MONEY: a
    ``-$60.00`` bank line may not be explained by a row worth ``$50.00``.
    ``repriced`` is what refuses it, and it is asked here through a payback
    whose price the app derives rather than one a fixture typed.
    """

    @staticmethod
    def _derived_payback(db, seed_user):
        """Stage an envelope, a card purchase, its payback, and a debit purchase.

        The payback is built by the APP's own door, so its figure is the one
        the app derives -- ``$50.00``, the single card purchase -- rather than
        one this fixture typed.  Since plan step X-au-i a payback that owns a
        figure is a row no door produces, and staging one would grade a state
        the app cannot reach.
        """
        del db  # the session is reached through ``_db``, as the siblings do
        envelope = a_transaction(
            seed_user, name="Groceries", amount="300.00", is_envelope=True,
        )
        a_purchase(
            seed_user, envelope, amount="50.00", description="Card",
            purchased_on=seed_user["bootstrap_period"].start_date,
            is_credit=True,
        )
        debit = a_purchase(
            seed_user, envelope, amount="25.00", description="Aldi",
            purchased_on=seed_user["bootstrap_period"].start_date,
        )
        # **Built the way ``credit_workflow.create_cc_payback_transaction``
        # builds one**, and it has to be: ``ck_transactions_one_pricing_link``
        # admits at most one of ``template_id`` / ``transfer_id`` /
        # ``credit_payback_for_id``, so the sibling builder's template makes a
        # payback the database refuses.  A fixture the app could not produce
        # would grade an unreachable case.
        #
        # It is seated in the BOOTSTRAP period rather than the next one -- which
        # is where ``sync_entry_payback`` would put it -- because the bank lines
        # below are dated there and a candidate carries the window its money
        # moved in (plan step X-f6a-3c-1).  A payback a period away is not
        # offered at all, and this case would grade nothing.
        payback = Transaction(
            account_id=seed_user["account"].id,
            template_id=None,
            pay_period_id=seed_user["bootstrap_period"].id,
            scenario_id=seed_user["scenario"].id,
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            name="CC Payback: Groceries",
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            # **The post-X-au-i shape**: the relation, and no figure.  The two
            # are paired by ``ck_transactions_amount_ownership``, so this is the
            # only amount state a payback can now be written in -- and it is
            # what makes the row worth the ``$50.00`` card purchase above
            # instead of whatever a fixture felt like typing.
            estimated_amount=None,
            amount_source_id=ref_cache.amount_source_id(
                AmountSourceEnum.CREDIT_SOURCE,
            ),
            credit_payback_for_id=envelope.id,
        )
        _db.session.add(payback)
        _db.session.flush()
        return envelope, debit, payback

    def test_the_stale_sibling_is_REFUSED_rather_than_booked(
        self, app, db, seed_user,
    ):
        """The whole finding, as one pass."""
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            _, debit, payback = self._derived_payback(db, seed_user)
            debit_line = a_bank_line(
                seed_user, statement, amount="-25.00", posted_on=day,
            )
            payback_line = a_bank_line(
                seed_user, statement, amount="-60.00", posted_on=day,
                sequence_in_group=1,
            )

            outcome = _batch(seed_user, matches=[
                _match(seed_user, lines=[debit_line], entries=[debit]),
                _match(
                    seed_user, lines=[payback_line], transactions=[payback],
                ),
            ])
            _db.session.flush()

            assert outcome.applied_count == 1, (
                "the purchase match must land -- otherwise the second act was "
                "never offered the stale price and this grades nothing"
            )
            assert outcome.refused_count == 1
            # The FIGURES, because that is what went wrong: the pass moved the
            # payback to 50.00 and the bank line says 60.00.
            assert "do not add up" in outcome.refused[0].reason
            assert "-60.00" in outcome.refused[0].reason
            assert "-50.00" in outcome.refused[0].reason
            assert payback.settled_on is None, (
                "a -60.00 bank line was explained by a row worth 50.00"
            )


class TestTheReceiptSaysWhatHappened:
    """A pass that reports only "done" is a pass nobody can check."""

    def test_it_counts_each_EFFECT_separately(self, app, db, seed_user):
        """Settling, re-dating and recording are three different acts.

        A single "3 items applied" would hide which: settling books money the
        projection was holding forward, moving a day moves money already
        booked, and recording adds a movement the app did not have.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            settle_line = a_bank_line(
                seed_user, statement, amount="-180.00", posted_on=day,
            )
            projected = a_transaction(
                seed_user, name="Electricity", amount="180.00",
            )
            correct_line = a_bank_line(
                seed_user, statement, amount="-42.00", posted_on=day,
                sequence_in_group=1,
            )
            settled = a_transaction(
                seed_user, name="Water", amount="42.00",
                status=StatusEnum.DONE, settled_on=day + timedelta(days=2),
            )
            swipe = a_bank_line(
                seed_user, statement, amount="-9.99", posted_on=day,
                sequence_in_group=2,
            )

            outcome = _batch(
                seed_user,
                matches=[
                    _match(
                        seed_user, lines=[settle_line],
                        transactions=[projected],
                    ),
                    _match(
                        seed_user, lines=[correct_line], transactions=[settled],
                    ),
                ],
                creations=[_creation(
                    seed_user, swipe,
                    new_envelope=NewEnvelope(
                        name="Amazon",
                        category_id=seed_user["categories"]["Groceries"].id,
                    ),
                )],
            )

            assert outcome.applied_count == 3
            assert outcome.settled_count == 1
            assert outcome.corrected_count == 1
            assert outcome.recorded_count == 1
            assert outcome.envelopes_created == 1
            assert outcome.moved_nothing is False

    def test_a_pass_that_only_CONFIRMS_says_it_moved_nothing(
        self, app, db, seed_user,
    ):
        """An applied item is not the same as a changed record.

        A match on a row already carrying the bank's own day writes no column,
        so a receipt counting applied items would claim work that did not
        happen -- the distinction ``AcceptedMatch`` already draws for one act.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            line = a_bank_line(
                seed_user, statement, amount="-180.00", posted_on=day,
            )
            row = a_transaction(
                seed_user, name="Electricity", amount="180.00",
                status=StatusEnum.DONE, settled_on=day,
            )

            outcome = _batch(seed_user, matches=[
                _match(seed_user, lines=[line], transactions=[row]),
            ])

            assert outcome.applied_count == 1
            assert outcome.moved_nothing is True

    def test_an_EMPTY_pass_is_not_an_error(self, app, db, seed_user):
        """Ticking nothing and pressing Apply is an ordinary thing to do."""
        with app.app_context():
            outcome = _batch(seed_user)

            assert outcome.applied_count == 0
            assert outcome.refused_count == 0
            assert outcome.moved_nothing is True


class TestWhatIsNOTCaughtPerItem:
    """The other half of the isolation rule, and it has to be asserted.

    A savepoint that swallowed everything would turn a broken ledger invariant
    into one line of a receipt, on a screen whose whole job is to move money
    correctly.  ``ValidationError`` is this project's DESIGNED refusal -- a
    sentence written for the person who submitted the form -- and it is the
    only thing the loop catches.
    """

    def test_a_PostingError_fails_the_whole_pass(
        self, app, db, seed_user, monkeypatch,
    ):
        """A broken invariant is a fact about the account, not about an item."""
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            line = a_bank_line(
                seed_user, statement, amount="-180.00", posted_on=day,
            )
            row = a_transaction(
                seed_user, name="Electricity", amount="180.00",
            )

            def _boom(*_args, **_kwargs):
                raise PostingError("the ledger does not balance")

            monkeypatch.setattr(
                statement_match._batch, "accept_match", _boom,
            )

            with pytest.raises(PostingError):
                _batch(seed_user, matches=[
                    _match(seed_user, lines=[line], transactions=[row]),
                ])

    def test_a_ValidationError_is_the_ONLY_thing_reported_per_item(
        self, app, db, seed_user, monkeypatch,
    ):
        """The control for the arm above: the designed refusal IS caught."""
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            line = a_bank_line(
                seed_user, statement, amount="-180.00", posted_on=day,
            )
            row = a_transaction(
                seed_user, name="Electricity", amount="180.00",
            )

            def _refuse(*_args, **_kwargs):
                raise ValidationError("a designed refusal")

            monkeypatch.setattr(
                statement_match._batch, "accept_match", _refuse,
            )

            outcome = _batch(seed_user, matches=[
                _match(seed_user, lines=[line], transactions=[row]),
            ])

            assert outcome.refused_count == 1
            assert outcome.refused[0].reason == "a designed refusal"


class TestThePassIsOneUnitOfWorkForTheCALLER:
    """The savepoints bound a REFUSAL; they do not commit anything.

    ``apply_reviewed`` does not commit, so a caller that abandons the request
    leaves no trace of a pass that had already "applied" 195 items -- which is
    what makes the route's own "nothing was changed" true on a database error.
    """

    def test_nothing_survives_a_rollback_by_the_caller(
        self, app, db, seed_user,
    ):
        """A released savepoint is still inside the outer transaction."""
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            line = a_bank_line(
                seed_user, statement, amount="-180.00", posted_on=day,
            )
            row = a_transaction(
                seed_user, name="Electricity", amount="180.00",
            )

            outcome = _batch(seed_user, matches=[
                _match(seed_user, lines=[line], transactions=[row]),
            ])
            assert outcome.applied_count == 1

            _db.session.rollback()

            assert db.session.query(StatementMatch).count() == 0


class TestTheScopeIsDerivedONCE:
    """The step's own measurement, asserted rather than described.

    12.88 minutes of derivation became 5.80 s because the account is derived
    once per PASS rather than once per ACT.  A later change that gave a door
    its own scope again would restore the cost silently -- every test above
    would still pass, and the only thing that would move is the wall clock.
    """

    def test_the_offer_set_is_built_once_however_many_items_run(
        self, app, db, seed_user, monkeypatch,
    ):
        """The firing control for the whole step."""
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            calls = []
            real = statement_match._scope.candidates_for

            def _counted(account_id, calendar, basis):
                calls.append(account_id)
                return real(account_id, calendar, basis)

            monkeypatch.setattr(
                statement_match._scope, "candidates_for", _counted,
            )

            items = []
            for index in range(4):
                line = a_bank_line(
                    seed_user, statement, amount=f"-{100 + index}.00",
                    posted_on=day, sequence_in_group=index,
                )
                row = a_transaction(
                    seed_user, name=f"Bill {index}", amount=f"{100 + index}.00",
                    status=StatusEnum.DONE, settled_on=day,
                )
                items.append(_match(
                    seed_user, lines=[line], transactions=[row],
                ))

            outcome = _batch(seed_user, matches=items)

            assert outcome.applied_count == 4
            assert calls == [seed_user["account"].id], (
                "the account was derived once per ACT again, which is the "
                "12.88 minutes finding N-306 measures"
            )


def test_a_scope_serves_the_screen_and_the_doors_alike(app, db, seed_user):
    """One derivation, two consumers, and they must agree about the account.

    The route builds a scope, renders the screen from it and applies the pass
    against it.  A reader and a writer disagreeing about what an account holds
    is the class of defect this package's one-scope rule exists to remove.
    """
    with app.app_context():
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="-180.00", posted_on=day,
        )
        a_transaction(
            seed_user, name="Electricity", amount="180.00",
            status=StatusEnum.DONE, settled_on=day + timedelta(days=3),
        )
        scope = a_scope(seed_user)

        review = statement_match.review_set(scope)

        assert len(review.proposals) == 1
        proposal = review.proposals[0]
        outcome = statement_match.apply_reviewed(ReviewedBatch(
            matches=(MatchSubmission(
                line_ids=frozenset(
                    bank.line_id for bank in proposal.lines
                ),
                transaction_ids=frozenset(
                    row.row_id for row in proposal.rows
                ),
                entry_ids=frozenset(),
            ),),
            creations=(),
        ), scope)

        assert outcome.applied_count == 1
        assert outcome.corrected_count == 1
        assert line.id in {
            item.line_ids[0] for item in outcome.applied
        }


class TestABatchBooksWhatTheSameActsBookOneAtATime:
    """**The firing control for the whole step**, and it was missing.

    Everything else in this module grades COUNTS, refusal sentences and row
    existence.  None of that can see the thing the step actually risks: one
    derivation is shared across every act, and a candidate's price from that
    derivation feeds exactly one consumer -- ``_accept._reject_unbalanced``,
    which is a money gate.  A stale price admits a match a fresh derivation
    would refuse, or refuses one it would admit, and the difference shows up in
    a FIGURE rather than in a count.

    So this applies the same acts twice, once as a batch against one shared
    scope and once one at a time against a fresh scope each, and compares the
    money to the cent.  Each run is bracketed by its own SAVEPOINT so the
    second starts from the state the first did.

    Found missing by adversarial test-quality review 2026-08-19, which put it
    exactly right: *"That premise is graded by nothing."*
    """

    @staticmethod
    def _money(app_db, seed_user, account):
        """Return every figure the two runs must agree on.

        Not just a balance: a balance can agree while the rows underneath it
        disagree in offsetting ways, so the per-row settle facts travel with
        it.

        Args:
            app_db: The test's ``db`` fixture.
            seed_user: The seeded user bundle.
            account: The account being reviewed.

        Returns:
            A comparable dict.
        """
        ctx = balance_at.BalanceContext.build(seed_user["user"].id)
        period = seed_user["bootstrap_period"]
        days = [
            period.start_date,
            period.start_date + timedelta(days=7),
            period.end_date,
            period.end_date + timedelta(days=30),
        ]
        # **No surrogate keys.**  A row this pass CREATES gets a sequence
        # value, and a rolled-back sequence does not rewind -- so the two runs
        # differ on ``id`` and on nothing else that matters.  An id is not
        # money; what the two runs must agree on is what each row RECORDS.
        rows = app_db.session.execute(_db.text(
            "SELECT name, settled_on, settled_amount, settled_basis_id,"
            "       status_id, estimated_amount"
            "  FROM budget.transactions ORDER BY name, id"
        )).all()
        entries = app_db.session.execute(_db.text(
            "SELECT t.name, e.description, e.amount, e.purchased_on,"
            "       e.settled_on, e.is_credit"
            "  FROM budget.transaction_entries e"
            "  JOIN budget.transactions t ON t.id = e.transaction_id"
            " ORDER BY t.name, e.description, e.amount"
        )).all()
        # The LEDGER, not just the projection.  A balance can agree while the
        # postings underneath it differ, and the posting writer is what the
        # settle verbs reach through -- so the ledger is where a re-dated
        # settle leaving its old posting behind would show up.
        #
        # **Keyed by the chart row's NAME, never its id**, which is the lesson
        # ``tests/manual/verify_statement_baseline.py`` states for itself: a
        # chart row minted by a run has an id the other run never had, and it
        # would diff as a moved line where the money did not move.
        postings = app_db.session.execute(_db.text(
            "SELECT l.name, e.entry_date, COALESCE(SUM(p.amount), 0)"
            "  FROM budget.account_postings p"
            "  JOIN budget.journal_entries e ON e.id = p.journal_entry_id"
            "  JOIN budget.ledger_accounts l ON l.id = p.ledger_account_id"
            " GROUP BY l.name, e.entry_date"
            " ORDER BY l.name, e.entry_date"
        )).all()
        return {
            "balances": {
                day.isoformat(): str(
                    balance_at.cash_balance_at(account, ctx, day),
                )
                for day in days
            },
            "transactions": [tuple(str(v) for v in row) for row in rows],
            "entries": [tuple(str(v) for v in row) for row in entries],
            "postings": [tuple(str(v) for v in row) for row in postings],
        }

    @staticmethod
    def _acts(db, seed_user):
        """Stage a pass that exercises all three effects, and return its items.

        One proposal that SETTLES a projected row, one that CORRECTS a settled
        row's day, one that re-dates a PURCHASE, and one line RECORDED into an
        existing envelope -- so the comparison covers every write the door
        performs rather than the cheapest one.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        matches, creations = [], []

        projected = a_transaction(
            seed_user, name="Electricity", amount="180.00",
        )
        settle_line = a_bank_line(
            seed_user, statement, amount="-180.00", posted_on=day,
        )
        matches.append(_match(
            seed_user, lines=[settle_line], transactions=[projected],
        ))

        settled = a_transaction(
            seed_user, name="Water", amount="42.00", status=StatusEnum.DONE,
            settled_on=day + timedelta(days=4),
        )
        correct_line = a_bank_line(
            seed_user, statement, amount="-42.00", posted_on=day,
            sequence_in_group=1,
        )
        matches.append(_match(
            seed_user, lines=[correct_line], transactions=[settled],
        ))

        envelope = a_transaction(
            seed_user, name="Groceries", amount="300.00", is_envelope=True,
        )
        # **Recorded LATER than the bank posted it**, which is the only shape
        # ruling R-FW re-dates: the owner's own assertion that this line IS
        # this purchase refutes a purchase day after the day it cleared.
        late = a_purchase(
            seed_user, envelope, amount="31.00", description="Aldi",
            purchased_on=day + timedelta(days=5),
        )
        redate_line = a_bank_line(
            seed_user, statement, amount="-31.00",
            posted_on=day + timedelta(days=3),
            transaction_on=day + timedelta(days=2), sequence_in_group=2,
        )
        matches.append(_match(
            seed_user, lines=[redate_line], entries=[late],
        ))

        swipe = a_bank_line(
            seed_user, statement, amount="-57.96", posted_on=day,
            sequence_in_group=3,
        )
        creations.append(_creation(
            seed_user, swipe, transaction_id=envelope.id,
        ))
        db.session.flush()
        return tuple(matches), tuple(creations)

    def test_the_two_agree_to_the_cent(self, app, db, seed_user):
        """One shared derivation books what four fresh ones book."""
        with app.app_context():
            from app.models.account import Account  # pylint: disable=import-outside-toplevel

            matches, creations = self._acts(db, seed_user)
            account = db.session.get(Account, seed_user["account"].id)

            batched = _db.session.begin_nested()
            outcome = statement_match.apply_reviewed(
                ReviewedBatch(matches=matches, creations=creations),
                a_scope(seed_user),
            )
            _db.session.flush()
            as_a_batch = self._money(db, seed_user, account)
            batched.rollback()
            # **EXPIRE, or the second run reads the first run's writes.**  A
            # savepoint rollback restores the DATABASE, and SQLAlchemy returns
            # the instance already in its identity map for a known primary key
            # -- so without this the second run saw the purchase the first run
            # had settled and reported "unchanged", booking nothing.  Two
            # REQUESTS have two sessions; this is what models that, and a
            # harness that does not is comparing a fresh run against a stale
            # one.  Measured while writing this test: the two runs differed by
            # exactly the `$31.00` purchase.
            _db.session.expire_all()

            one_at_a_time = _db.session.begin_nested()
            singly_did = []
            for submission in matches:
                singly_did.append(statement_match.accept_match(
                    submission, a_scope(seed_user),
                ))
                _db.session.flush()
            for creation in creations:
                # Its OWN registry per call, because acting one at a time is
                # several REQUESTS and a request is the registry's scope.
                # **This control does NOT measure convergence** and a comment
                # here once claimed it did: its fixture holds a single creation
                # naming an EXISTING envelope, so neither path consults the
                # registry and both create zero envelopes.  Two adversarial
                # reviews measured that independently 2026-08-20.  What does
                # measure it is
                # ``TestConvergingMovesTheSameMoneyAsNotConverging`` below,
                # which needs its own fixture and its own comparison because
                # the row-count comparison here is exactly what convergence is
                # SUPPOSED to change.
                singly_did.append(statement_match.create_purchase_from_line(
                    creation, a_scope(seed_user),
                    _create.MintedEnvelopes.none_yet(),
                ))
                _db.session.flush()
            singly = self._money(db, seed_user, account)
            one_at_a_time.rollback()
            _db.session.expire_all()

            assert all(
                getattr(did, "match_id", None) is not None
                for did in singly_did
            ), "an act in the one-at-a-time run did not record a match"

            assert outcome.applied_count == 4, (
                "the pass must APPLY its items, or the comparison is between "
                "two states in which nothing happened"
            )
            # **TWO settles, and the second one is the point.**  The late
            # purchase was never marked as having happened, so the match
            # SETTLES it -- and it is re-dated in the same act, which is
            # exactly why ``redated_count`` cuts across the settled/corrected
            # partition rather than joining it.  A first draft of this fixture
            # expected one settle and one correction and was wrong about the
            # purchase; the counts are the code's, checked against the rows.
            assert outcome.settled_count == 2
            assert outcome.corrected_count == 1
            assert outcome.redated_count == 1
            assert outcome.recorded_count == 1

            assert as_a_batch["balances"] == singly["balances"]
            assert as_a_batch["transactions"] == singly["transactions"]
            assert as_a_batch["entries"] == singly["entries"]
            assert as_a_batch["postings"] == singly["postings"]


class TestOnePressMintsOneEnvelopePerAnswerPerPeriod:
    """Finding **N-327**, developer ruling 2026-08-20 (plan step X-f6a-4).

    A ``new envelope called X`` answer minted one PER LINE, so a sweep
    fragmented the budget line the policy names.  Measured on the developer's
    own statement: a ``Lowe's`` answer places 4 lines over 3 pay periods, so
    ONE press made 4 envelopes -- two of them in the SAME period -- and the
    next statement made more beside them.

    **The convergence is scoped to one REQUEST**, which is what
    :class:`~app.services.statement_match.MintedEnvelopes` is: the
    cross-statement half is answered by the SUGGESTION, which degrades to a
    ``RECORD_IN`` against an envelope of that name already in the period and is
    printed where the owner can override it.

    **It moves NO money**, which the batch-vs-one-at-a-time control above now
    measures: each purchase carries its own posting day and is its own cash
    movement, so an envelope's own leg is `0.00` whether one envelope holds
    four purchases or four hold one each.
    """

    def test_two_lines_in_ONE_period_share_one_envelope(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: this is the same-press duplication itself."""
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        first = a_bank_line(
            seed_user, statement, amount="-30.00", posted_on=day,
            description="LOWES ONE",
        )
        second = a_bank_line(
            seed_user, statement, amount="-45.00", posted_on=day,
            description="LOWES TWO", sequence_in_group=0,
        )
        category = seed_user["categories"]["Groceries"]
        answer = NewEnvelope(name="Home Improvement", category_id=category.id)

        outcome = _batch(seed_user, creations=[
            _creation(seed_user, first, new_envelope=answer),
            _creation(seed_user, second, new_envelope=answer),
        ])
        db.session.flush()

        assert outcome.applied_count == 2
        assert outcome.recorded_count == 2
        assert outcome.envelopes_created == 1
        envelopes = db.session.query(Transaction).filter_by(
            name="Home Improvement",
        ).all()
        assert len(envelopes) == 1
        assert sorted(entry.amount for entry in envelopes[0].entries) == [
            Decimal("30.00"), Decimal("45.00"),
        ]

    def test_the_shared_envelope_moves_NO_cash_leg_of_its_own(
        self, app, db, seed_user,
    ):
        """MONEY: the second purchase carries its own posting day.

        An envelope's own cash leg is what its purchases have NOT already
        taken (ruling **R-FM**), and every purchase this door writes carries
        the day the bank took it -- so the envelope contributes nothing itself
        whether it holds one purchase or four.  Asserted directly, because
        "the budget stopped fragmenting" would be worth nothing if the money
        moved with it.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
        lines = [
            a_bank_line(
                seed_user, statement, amount=amount, posted_on=day,
                description=f"LOWES {amount}",
            )
            for amount in ("-30.00", "-45.00")
        ]
        category = seed_user["categories"]["Groceries"]
        answer = NewEnvelope(name="Home Improvement", category_id=category.id)
        # Valued AFTER the posting day, never on the anchor's own: an anchor
        # RESETS the ledger for its day, so a balance read there answers the
        # assertion whatever the postings say.
        measured_on = day + timedelta(days=2)
        before = _balance(seed_user, measured_on)

        _batch(seed_user, creations=[
            _creation(seed_user, line, new_envelope=answer) for line in lines
        ])
        db.session.flush()

        assert _balance(seed_user, measured_on) == before - Decimal("75.00")

    def test_lines_in_TWO_periods_get_one_envelope_EACH(
        self, app, db, seed_user,
    ):
        """The key carries the period, because an envelope belongs to one.

        FIRING CONTROL against the over-correction: converging on the NAME
        alone would file a March swipe into an April budget line, which is the
        one thing a pay-period app may never do.
        """
        statement = an_import(seed_user)
        first_day = seed_user["bootstrap_period"].start_date
        later = a_later_period(seed_user).start_date
        one = a_bank_line(
            seed_user, statement, amount="-30.00", posted_on=first_day,
            description="LOWES EARLY",
        )
        two = a_bank_line(
            seed_user, statement, amount="-45.00", posted_on=later,
            description="LOWES LATE",
        )
        category = seed_user["categories"]["Groceries"]
        answer = NewEnvelope(name="Home Improvement", category_id=category.id)

        outcome = _batch(seed_user, creations=[
            _creation(seed_user, one, new_envelope=answer),
            _creation(seed_user, two, new_envelope=answer),
        ])
        db.session.flush()

        assert outcome.applied_count == 2
        assert outcome.envelopes_created == 2
        envelopes = db.session.query(Transaction).filter_by(
            name="Home Improvement",
        ).all()
        assert len({row.pay_period_id for row in envelopes}) == 2

    def test_one_NAME_under_TWO_categories_stays_two_envelopes(
        self, app, db, seed_user,
    ):
        """The key carries the category, and that is not decoration.

        Two answers naming one word under two categories are two budget lines,
        and merging them would file spending under a category the owner did not
        pick -- which is what every spending report groups by.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        one = a_bank_line(
            seed_user, statement, amount="-30.00", posted_on=day,
            description="ONE",
        )
        two = a_bank_line(
            seed_user, statement, amount="-45.00", posted_on=day,
            description="TWO",
        )
        groceries = seed_user["categories"]["Groceries"]
        other = next(
            category for name, category in seed_user["categories"].items()
            if name != "Groceries"
        )

        outcome = _batch(seed_user, creations=[
            _creation(seed_user, one, new_envelope=NewEnvelope(
                name="Shared Name", category_id=groceries.id,
            )),
            _creation(seed_user, two, new_envelope=NewEnvelope(
                name="Shared Name", category_id=other.id,
            )),
        ])
        db.session.flush()

        assert outcome.envelopes_created == 2
        assert db.session.query(Transaction).filter_by(
            name="Shared Name",
        ).count() == 2

    def test_a_REFUSED_creation_leaves_no_envelope_for_the_next_line(
        self, app, db, seed_user,
    ):
        """Ruling **R-FZ**: a refused item leaves nothing behind.

        A creation rolled back inside its own SAVEPOINT must not leave a later
        line pointing at a row that no longer exists -- so the registry is
        written only once the act has returned.  The second line therefore
        mints the envelope the first one failed to.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        made_later = a_bank_line(
            seed_user, statement, amount="-30.00", posted_on=day,
            transaction_on=day + timedelta(days=3), description="REFUSED",
        )
        ordinary = a_bank_line(
            seed_user, statement, amount="-45.00", posted_on=day,
            description="LANDS",
        )
        category = seed_user["categories"]["Groceries"]
        answer = NewEnvelope(name="Home Improvement", category_id=category.id)

        outcome = _batch(seed_user, creations=[
            _creation(seed_user, made_later, new_envelope=answer),
            _creation(seed_user, ordinary, new_envelope=answer),
        ])
        db.session.flush()

        assert outcome.refused_count == 1
        assert outcome.applied_count == 1
        assert outcome.envelopes_created == 1
        envelope = db.session.query(Transaction).filter_by(
            name="Home Improvement",
        ).one()
        assert [entry.amount for entry in envelope.entries] == [
            Decimal("45.00"),
        ]


class TestAConvergedEnvelopeClosesOnTheLatestDayItHolds:
    """Finding from adversarial financial review 2026-08-20.

    A press mints ONE envelope per answer per pay period, and the second line
    to reach it used to leave the container recording the FIRST line's posting
    day.  Measured before the fix: two lines in one period submitted 01-05 then
    01-09 closed the envelope on **2024-01-05 while it held `$45.00` the bank
    did not take until 01-09**, and submitting them the other way round
    recorded 01-09 -- so the close day was whichever line happened to be filed
    first.

    **No figure moved either way** (each purchase carries its own posting day,
    so the envelope's own cash leg is `0.00`), which is why no balance
    assertion could see it.  What was wrong is that a row recorded closing
    before money it holds.  The rule applied is this arc's own for a group:
    a match's day is ``max(posted_on)`` over its lines.
    """

    def _sweep(self, seed_user, days, name="Home Improvement"):
        """Record one line per day in *days*, in that order, under one answer."""
        statement = an_import(seed_user)
        category = seed_user["categories"]["Groceries"]
        answer = NewEnvelope(name=name, category_id=category.id)
        lines = [
            a_bank_line(
                seed_user, statement, amount=amount, posted_on=day,
                description=f"LOWES {amount}",
            )
            for amount, day in days
        ]
        return _batch(seed_user, creations=[
            _creation(seed_user, line, new_envelope=answer) for line in lines
        ])

    def test_it_closes_on_the_LATEST_day_whichever_order_they_arrive(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL, and the order-independence is the point.

        The two submissions differ only in which line is filed first, and the
        recorded close day must not.
        """
        first = seed_user["bootstrap_period"].start_date + timedelta(days=1)
        later = first + timedelta(days=4)

        self._sweep(seed_user, [("-30.00", first), ("-45.00", later)])
        db.session.flush()
        ascending = db.session.query(Transaction).filter_by(
            name="Home Improvement",
        ).one()

        assert ascending.settled_on == later

    def test_the_order_does_not_decide_the_close_day(
        self, app, db, seed_user,
    ):
        """The same two lines, filed newest first."""
        first = seed_user["bootstrap_period"].start_date + timedelta(days=1)
        later = first + timedelta(days=4)

        self._sweep(seed_user, [("-45.00", later), ("-30.00", first)])
        db.session.flush()
        descending = db.session.query(Transaction).filter_by(
            name="Home Improvement",
        ).one()

        assert descending.settled_on == later

    def test_re_closing_it_moves_NO_cash_leg_of_its_own(
        self, app, db, seed_user,
    ):
        """MONEY: re-stamping the close day must not move a figure.

        The envelope settles from its own entries and every purchase carries
        the day the bank took it, so the container contributes nothing itself
        -- before the re-close and after it.
        """
        first = seed_user["bootstrap_period"].start_date + timedelta(days=1)
        later = first + timedelta(days=4)
        before = _balance(seed_user, later + timedelta(days=2))

        self._sweep(seed_user, [("-30.00", first), ("-45.00", later)])
        db.session.flush()

        envelope = db.session.query(Transaction).filter_by(
            name="Home Improvement",
        ).one()
        assert cash_ledger.settled_cash_leg(envelope) == Decimal("0.00")
        assert _balance(seed_user, later + timedelta(days=2)) == (
            before - Decimal("75.00")
        )

    def test_an_EXISTING_envelope_keeps_its_own_close_day(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL against over-correcting.

        A row the owner already closed is THEIR record.  Recording a purchase
        into it is the shipped destination arm and it leaves that day alone; a
        rule that re-dated it here would edit the owner's record rather than
        complete this press's own.
        """
        closed_on = seed_user["bootstrap_period"].start_date + timedelta(days=1)
        envelope = a_transaction(
            seed_user, name="Groceries", amount="500.00", is_envelope=True,
            settled_on=closed_on,
        )
        envelope.settled_basis_id = ref_cache.settlement_basis_id(
            SettlementBasisEnum.PURCHASES,
        )
        envelope.settled_amount = None
        envelope.status_id = ref_cache.status_id(StatusEnum.DONE)
        db.session.flush()
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-20.00",
            posted_on=closed_on + timedelta(days=5), description="LATER",
        )

        _batch(seed_user, creations=[
            _creation(seed_user, line, transaction_id=envelope.id),
        ])
        db.session.flush()

        db.session.refresh(envelope)
        assert envelope.settled_on == closed_on


class TestConvergingMovesTheSameMoneyAsNotConverging:
    """The money half of finding **N-327**, measured rather than asserted.

    A press mints ONE envelope per answer per pay period.  The claim that the
    convergence is money-neutral was stated in three docstrings and graded by
    nothing until two adversarial reviews said so 2026-08-20 -- the
    batch-versus-one-at-a-time control could not see it (its fixture creates no
    envelope at all), and the direct balance case passes with convergence
    disabled, because two envelopes holding one purchase each and one envelope
    holding two both take the same cash.

    **So this compares the two paths on what convergence must PRESERVE and
    deliberately not on what it must CHANGE.**  Balances, ledger postings and
    the purchases themselves must be identical; the number of budget rows is
    the thing that differs, and comparing it would grade the fixture rather
    than the money.
    """

    def _facts(self, db, seed_user, days):
        """Return the money facts both paths must agree on."""
        account = seed_user["account"]
        ctx = balance_at.BalanceContext.build(seed_user["user"].id)
        entries = _db.session.execute(_db.text(
            "SELECT e.description, e.amount, e.purchased_on, e.settled_on"
            "  FROM budget.transaction_entries e"
            " ORDER BY e.amount, e.settled_on"
        )).all()
        postings = _db.session.execute(_db.text(
            "SELECT l.name, e.entry_date, SUM(p.amount)"
            "  FROM budget.account_postings p"
            "  JOIN budget.journal_entries e ON e.id = p.journal_entry_id"
            "  JOIN budget.ledger_accounts l ON l.id = p.ledger_account_id"
            " GROUP BY l.name, e.entry_date"
            " ORDER BY l.name, e.entry_date"
        )).all()
        return {
            "balances": {
                day.isoformat(): str(
                    balance_at.cash_balance_at(account, ctx, day),
                )
                for day in days
            },
            "entries": [tuple(str(v) for v in row) for row in entries],
            "postings": [tuple(str(v) for v in row) for row in postings],
        }

    def test_the_two_paths_book_the_same_money(self, app, db, seed_user):
        """FIRING CONTROL for the money claim, on a fixture that converges."""
        statement = an_import(seed_user)
        category = seed_user["categories"]["Groceries"]
        answer = NewEnvelope(name="Home Improvement", category_id=category.id)
        day = seed_user["bootstrap_period"].start_date + timedelta(days=2)
        later = day + timedelta(days=3)
        lines = [
            a_bank_line(
                seed_user, statement, amount="-30.00", posted_on=day,
                description="LOWES ONE",
            ),
            a_bank_line(
                seed_user, statement, amount="-45.00", posted_on=later,
                description="LOWES TWO",
            ),
        ]
        creations = [
            _creation(seed_user, line, new_envelope=answer) for line in lines
        ]
        days = [day, later, later + timedelta(days=7)]
        _db.session.flush()

        converged_run = _db.session.begin_nested()
        outcome = _batch(seed_user, creations=creations)
        _db.session.flush()
        assert outcome.envelopes_created == 1, "the fixture did not converge"
        converged = self._facts(db, seed_user, days)
        converged_run.rollback()
        _db.session.expire_all()

        separate_run = _db.session.begin_nested()
        for creation in creations:
            statement_match.create_purchase_from_line(
                creation, a_scope(seed_user),
                _create.MintedEnvelopes.none_yet(),
            )
            _db.session.flush()
        assert _db.session.execute(_db.text(
            "SELECT count(*) FROM budget.transactions"
            " WHERE name = 'Home Improvement'"
        )).scalar() == 2, "the un-converged path did not make two envelopes"
        separate = self._facts(db, seed_user, days)
        separate_run.rollback()
        _db.session.expire_all()

        assert converged["balances"] == separate["balances"]
        assert converged["entries"] == separate["entries"]
        assert converged["postings"] == separate["postings"]


class TestThePassHoldsONEAmountBasis:
    """Plan step **X-au-j**: the derivations are the PASS's, not the row's.

    An :class:`~app.services.cash_ledger.AmountBasis` holds the owner's live
    derivations -- the paycheck engine run over the whole pay-period set, and
    each loan's P&I, payment day and escrow history.  ``amount_basis``'s own
    docstring says calling those per row is finding **N-228**, and finding
    **N-309** measured this pass doing exactly that: **609 salary-pricing and
    609 loan-pricing constructions** over 825 candidates, ``4.7 s`` to render,
    with the accept door paying it all again.

    It is ``TestTheScopeIsDerivedONCE`` one column over and it fails the same
    way: a later change that let a producer build its own would restore the cost
    in silence, because every figure would still be right.  Only the wall clock
    moves, which is why the count is asserted rather than the timing.
    """

    def test_one_basis_serves_every_candidate_the_pass_prices(
        self, app, db, seed_user, monkeypatch,
    ):
        """The firing control: ONE construction, however many rows are priced."""
        with app.app_context():
            # **PROJECTED rows, and that is what makes this control sharp.**  A
            # SETTLED row is valued from its record (``settled_cash_leg``) and
            # never reaches the resolver, so a pass of settled rows builds one
            # basis whether or not this step shipped.  Each of these prices.
            for index in range(8):
                a_transaction(
                    seed_user, name=f"Bill {index}",
                    amount=f"{100 + index}.00",
                )
            _db.session.commit()

            built = []
            real = cash_ledger.amount_basis

            def _counted(user_id, scenario_id):
                built.append((user_id, scenario_id))
                return real(user_id, scenario_id)

            # Patched where the SEAM publishes it and where each priced row
            # would reach it, so a row that built its own is visible whichever
            # door it went through.
            monkeypatch.setattr(cash_ledger, "amount_basis", _counted)
            monkeypatch.setattr(
                transaction_settle, "amount_basis", _counted,
            )
            monkeypatch.setattr(transfer_settle, "amount_basis", _counted)
            monkeypatch.setattr(
                statement_match._scope, "amount_basis", _counted,
            )

            scope = statement_match.ReviewScope.build(
                seed_user["user"].id, seed_user["account"].id,
            )

            assert len(scope.candidates.rows) >= 8, (
                "the pass must price several rows -- otherwise one basis and "
                "one per row are the same number and this grades nothing"
            )
            assert len(built) == 1, (
                f"the pass built {len(built)} amount bases for "
                f"{len(scope.candidates.rows)} candidates; X-au-j makes it one"
            )
