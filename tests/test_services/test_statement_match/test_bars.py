"""The CREATION BARS: the bank lines that may never become purchases.

Ruling **R-GJ**, plan step ``bank_import:X-ga``.  **The subject is a door that
does not exist**, so almost every case here is a firing control: delete the bar
and the create arm comes back, silently, on exactly the lines whose money the
app already holds.

Measured on the developer's own dev database 2026-08-24, which is what this
step exists for: nine Capital One ACH payments became purchases in eight
`$0.00`-budget
envelopes holding **`$7,412.94`** -- eight and not nine, because two of the nine fell in
one pay period -- in one YTD pass, while the app was already
holding 22 ``CC Payback`` rows RECORDING **`$6,286.46`** of that same card's
spending.  The
saved answer was *a new envelope called Capital One Credit Card*; the screen
printed "a card payment your app records as payback rows would be counted
twice" one card above the select that did it; and
``create_purchase_from_line`` read no rule at all.  A tenth line
(`-$466.47`, 2026-06-17) was group-matched to four of those payback rows
instead, which is the arm this ruling leaves open.

**Two bars, and the second is the developer's ruling about who may decide**
(2026-08-24): the owner's own *never a purchase* answer refuses outright, and a
source's own card-payment category refuses only until they answer.  The bank's
label may REQUIRE an answer and may never supply one, which is measured rather
than stylistic -- SECU files 22 of the developer's 378 recorded lines under
``Financial Services/Credit Card Payment`` and **7 of those 22 are the Van Loan
car payment**, four already matched to ``Transfer to Van Loan`` shadows.
``TestTheSourcesLabelOnlyASKS`` is where that boundary is pinned.
"""

from decimal import Decimal

import pytest

from app.enums import StatementSourceEnum
from app.exceptions import ValidationError
from app.models.merchant_rule import MerchantRule
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.statement_match import StatementMatch
from app.services import statement_match
from app.services.statement_match import (
    CreationBar,
    CreationBars,
    NewEnvelope,
    RuleAnswer,
    RuleSubmission,
    PurchaseCreation,
    ReviewedBatch,
    review_set,
    state_rules,
)
from app.services.statement_match import _create  # pylint: disable=protected-access
from app.services.statement_match._vocabulary import (  # pylint: disable=protected-access
    ACCOUNT_PAYMENT_CATEGORIES,
)

from ._builders import (
    a_bank_line,
    a_bars,
    a_merchant,
    a_rule,
    a_scope,
    a_submission,
    a_transaction,
    the_merchant_id,
    an_import,
)

#: SECU's own category string for a payment to a credit card, verbatim.  22 of
#: the developer's 378 recorded lines carry it.  Written out here rather than
#: imported from the module under test, because a fixture that took the value
#: from the code it grades would still pass if that value were changed to
#: something no statement contains.
CARD_PAYMENT = "Financial Services/Credit Card Payment"

#: What SECU calls the developer's Capital One ACH payments -- and the second
#: spelling it used for the same payee between 2026-01-22 and 2026-02-17, which
#: is why a rule keyed on the merchant string is not by itself a guard.
CARD_MERCHANT = "Capital One Credit Card"
OLD_CARD_MERCHANT = "Capital One Mobile Pmt"


def _a_card_payment(seed_user, statement, *, amount="-793.23", sequence=0,
                    merchant=CARD_MERCHANT):
    """Stage one bank line shaped like a Capital One ACH payment."""
    return a_bank_line(
        seed_user, statement, amount=amount, sequence_in_group=sequence,
        description=f"ACH DEBIT CAPITAL ONE      MOBILE PMT ({merchant})",
        merchant=merchant, source_category=CARD_PAYMENT,
    )


def _a_swipe(seed_user, statement, *, amount="-25.00", sequence=0,
             merchant="Food Lion"):
    """Stage one bank line shaped like an ordinary card swipe."""
    return a_bank_line(
        seed_user, statement, amount=amount, sequence_in_group=sequence,
        description=f"POINT OF SALE DEBIT {merchant}", merchant=merchant,
        source_category="Food & Drink/Groceries",
    )


class TestWhichMerchantsAreBarred:
    """The partition, read from the database the screen and the door share."""

    def test_a_NEVER_answer_bars_the_merchant(self, app, db, seed_user):
        """The owner's own decision, which until X-ga was a caption.

        A row with all three columns NULL is the *never a purchase* answer
        (``ck_merchant_rules_one_answer``'s third shape).  Before this
        step it withheld a sweep value and nothing else.
        """
        statement = an_import(seed_user)
        _a_card_payment(seed_user, statement)
        a_rule(seed_user, CARD_MERCHANT)
        db.session.flush()

        bars = a_bars(seed_user)

        assert bars.bar_for(
            the_merchant_id(seed_user, CARD_MERCHANT),
        ) is CreationBar.NEVER_A_PURCHASE

    def test_a_line_that_PAYS_AN_ACCOUNT_is_barred_with_no_answer_at_all(
        self, app, db, seed_user,
    ):
        """The bar that asks nothing, because there is nothing to ask.

        This is the half that reaches a card the owner has never seen before --
        the case a merchant-keyed answer cannot cover, because there is no
        answer yet to key.  Paying an account you hold is not spending whoever
        is asked, so the app states it rather than putting it to them.
        """
        statement = an_import(seed_user)
        _a_card_payment(seed_user, statement)
        db.session.flush()

        bars = a_bars(seed_user)

        assert bars.bar_for(the_merchant_id(seed_user, CARD_MERCHANT)) is (
            CreationBar.PAYS_AN_ACCOUNT_YOU_HOLD
        )

    def test_NO_answer_lifts_it_not_even_the_one_that_double_booked(
        self, app, db, seed_user,
    ):
        """THE FIRING CONTROL for the hole two adversarial reviews measured.

        A first version made this bar an *unanswered* state that any answer
        lifted.  The answer that lifts it is ``a new envelope`` -- which is the
        answer the developer had actually saved for ``Capital One Credit
        Card``, and the one that booked `$7,412.94` through a single sweep
        click.  Restore ``if merchant in self.answered: return None`` between
        the two arms of :meth:`~._bars.CreationBars.bar_for` and this case
        fails.

        The reason it may not lift is not caution: there is no answer that
        would make a payment to an account you hold into spending.  The money
        was spent somewhere else and the budget already holds it there.
        """
        envelope = a_transaction(seed_user, name="Groceries", is_envelope=True)
        statement = an_import(seed_user)
        _a_card_payment(seed_user, statement)
        a_rule(seed_user, CARD_MERCHANT, template_id=envelope.template_id)
        db.session.flush()

        assert a_bars(seed_user).bar_for(the_merchant_id(seed_user, CARD_MERCHANT)) is (
            CreationBar.PAYS_AN_ACCOUNT_YOU_HOLD
        )

    def test_a_NEVER_answer_still_bars_a_merchant_the_bank_never_flagged(
        self, app, db, seed_user,
    ):
        """The two bars are independent, and the first is the owner's alone.

        An insurance premium the owner records as a bill is not a card payment
        and no source says it is; saying *never a purchase* about it must still
        close the door, because the double count it prevents is the same one.
        """
        statement = an_import(seed_user)
        _a_swipe(seed_user, statement, merchant="Geico")
        a_rule(seed_user, "Geico")
        db.session.flush()

        assert a_bars(seed_user).bar_for(the_merchant_id(seed_user, "Geico")) is (
            CreationBar.NEVER_A_PURCHASE
        )

    def test_an_ordinary_unanswered_merchant_is_NOT_barred(
        self, app, db, seed_user,
    ):
        """The control that keeps the whole step from being a blanket refusal.

        74 of the developer's 91 unexplained outflows are ordinary card swipes
        worth `$3,383.49`, and every one of them must keep its create arm --
        that arm is what ruling **R-FS** exists for.
        """
        statement = an_import(seed_user)
        _a_swipe(seed_user, statement)
        db.session.flush()

        assert a_bars(seed_user).bar_for(the_merchant_id(seed_user, "Food Lion")) is None

    def test_a_line_naming_NO_merchant_puts_NOTHING_in_the_bars(
        self, app, db, seed_user,
    ):
        """THE FIRING CONTROL for the ``merchant.isnot(None)`` filter.

        A source naming no merchant keys no rule, so there is nothing the
        owner could ever have said about it -- and a bar that fired on ``None``
        would refuse every such line on every source that truncates its
        descriptions.  SECU's own OFX cuts 326 of 361 to the same 32
        characters.

        **The line here carries the card-payment category AND no merchant**,
        which is what makes this a control rather than an assertion: delete the
        ``isnot(None)`` filter at :func:`~._bars._card_payment_merchants` and
        ``None`` enters ``account_payments``, at which point every merchant-less
        line on the account is barred.  Two adversarial reviews 2026-08-24
        measured the case this replaces -- it staged nothing, so every set was
        empty and it asserted ``None is None`` while a redundant ``merchant is
        None`` branch in :meth:`bar_for` made the filter ungradeable from the
        other side too.  That branch is gone; this is the one guard and this is
        its control.
        """
        statement = an_import(seed_user)
        a_bank_line(
            seed_user, statement, amount="-793.23",
            description="ACH DEBIT CAPITAL ONE      MOBILE PMT",
            merchant=None, source_category=CARD_PAYMENT,
        )
        db.session.flush()

        bars = a_bars(seed_user)

        assert bars.account_payments == frozenset()
        assert bars.bar_for(None) is None
        assert bars.pays_an_account(None) is False


class TestTheSourcesLabelOnlyASKS:
    """Where the bank's own words are read, and how far they reach.

    **ONE SURFACE HERE HAS NO FIRING CONTROL, and it is named rather than
    left to be discovered.**  ``_CARD_PAYMENT_CATEGORIES`` is keyed by
    :class:`~app.enums.StatementSourceEnum`, so a category string means what it
    means only for the adapter that wrote it -- and that keying cannot be
    graded today, because the enum has exactly one member and
    ``ref.statement_sources`` exactly one row, so an import from a source
    OUTSIDE the map is unrepresentable.  Measured by mutation 2026-08-24:
    deleting the ``source_id`` clause from the query leaves all 411 tests in
    this package green.  The first case that can grade it is the second
    adapter, ``bank_import:X-f6b``'s SimpleFIN feed.  What IS graded below is
    the category comparison itself, which is the half a live statement can be
    wrong about.
    """

    def test_EVERY_source_declares_its_own_card_payment_vocabulary(self):
        """THE FIRING CONTROL for the registry's fail-OPEN.

        A source absent from ``ACCOUNT_PAYMENT_CATEGORIES`` contributes no clause
        to the query, so no line it recorded is ever barred -- silently, with
        every test green.  ``statement_import._adapters``, which that map
        mirrors, fails LOUD in the same position by design; an adversarial
        review 2026-08-24 measured this one failing open.

        This is what makes adding an adapter a DECISION about the second bar
        rather than an omission: ``bank_import:X-f6b``'s SimpleFIN member
        cannot land without an entry here, empty or not.  An empty set is a
        legitimate answer -- a source that files nothing as a card payment --
        and stating it is the point.
        """
        assert set(ACCOUNT_PAYMENT_CATEGORIES) == set(StatementSourceEnum)

    def test_the_category_is_read_from_the_SOURCE_that_recorded_it(
        self, app, db, seed_user,
    ):
        """``source_category`` is one source's private vocabulary.

        The model rules it "kept as provenance and never read as logic", and
        the reading here is the narrow exception the developer ruled: it may
        require an answer and may never supply one.  Keyed by the ADAPTER, so a
        second source's identical spelling of a different meaning cannot fire
        this one's rule -- which is why the join to ``statement_imports`` is
        part of the derivation rather than an assumption.
        """
        statement = an_import(seed_user)
        _a_card_payment(seed_user, statement)
        db.session.flush()

        assert a_bars(seed_user).account_payments == frozenset(
            {a_merchant(seed_user, CARD_MERCHANT).id},
        )

    def test_another_category_from_the_same_source_is_not_one(
        self, app, db, seed_user,
    ):
        """The firing control for the category comparison itself.

        Widen it to "any Financial Services category" and this fails: the
        developer's own statement carries `$13,376.65` of funds transfers and
        `$2,250.00` of Fidelity investing under that prefix, none of them a
        card payment.
        """
        statement = an_import(seed_user)
        a_bank_line(
            seed_user, statement, amount="-1910.95", merchant="Funds Transfer",
            source_category="Financial Services/Transfers",
        )
        db.session.flush()

        assert a_bars(seed_user).account_payments == frozenset()

    def test_a_second_spelling_of_one_payee_is_a_second_merchant(
        self, app, db, seed_user,
    ):
        """Measured: SECU renamed this payee mid-year, and both are flagged.

        Three of the developer's 15 Capital One lines read
        ``Capital One Mobile Pmt`` and twelve read ``Capital One Credit
        Card``; the string changed between 2026-02-17 and 2026-02-27.  A guard
        that keyed only on what the owner had answered would have missed the
        three, and this is the half of the design that does not: the source
        files BOTH under its card-payment category, so both are asked about.
        """
        statement = an_import(seed_user)
        _a_card_payment(seed_user, statement)
        _a_card_payment(
            seed_user, statement, amount="-1149.53", sequence=1,
            merchant=OLD_CARD_MERCHANT,
        )
        db.session.flush()

        bars = a_bars(seed_user)

        assert bars.account_payments == frozenset({
            a_merchant(seed_user, CARD_MERCHANT).id,
            a_merchant(seed_user, OLD_CARD_MERCHANT).id,
        })
        assert bars.bar_for(the_merchant_id(seed_user, OLD_CARD_MERCHANT)) is (
            CreationBar.PAYS_AN_ACCOUNT_YOU_HOLD
        )

    def test_ANOTHER_accounts_lines_do_not_reach_this_accounts_bars(
        self, app, db, seed_user, seed_second_user,
    ):
        """The scope, which the composite join is what makes structural.

        A rule is stated per account
        (``uq_merchant_rules_account_merchant``) and so is the
        evidence behind it: a card payment recorded on another account says
        nothing about what this account's lines are.  The join carries the
        account on BOTH sides (``fk_bank_statement_lines_import_account``), so
        the narrowing is the key rather than a filter a reader could forget.
        """
        other = an_import(seed_second_user)
        _a_card_payment(seed_second_user, other)
        db.session.flush()

        assert a_bars(seed_user).account_payments == frozenset()
        assert a_bars(seed_second_user).account_payments == frozenset({
            a_merchant(
                seed_second_user, CARD_MERCHANT,
                account=seed_second_user["account"],
            ).id,
        })


class TestTheDoorRefusesABarredLine:
    """The money half: what a crafted body or a stale page reaches."""

    def _creation(self, seed_user, line):
        """Return the submission a stale page would send for *line*."""
        return PurchaseCreation(
            line_id=line.id,
            new_envelope=NewEnvelope(
                name=CARD_MERCHANT,
                category_id=seed_user["categories"]["Groceries"].id,
            ),
        )

    def _record(self, seed_user, line):
        """Call the create door for *line*, with this pass's real bars."""
        return statement_match.create_purchase_from_line(
            self._creation(seed_user, line), a_scope(seed_user),
            _create.MintedEnvelopes.none_yet(), a_bars(seed_user),
        )

    def test_a_NEVER_line_is_refused_and_NOTHING_is_written(
        self, app, db, seed_user,
    ):
        """The refusal that would have stopped `$7,412.94`.

        The screen renders no control for this line at all, so the only way
        here is a stale page or a crafted body -- and that is precisely the
        path the last version of this rule left open, because the last version
        was a paragraph.
        """
        statement = an_import(seed_user)
        line = _a_card_payment(seed_user, statement)
        a_rule(seed_user, CARD_MERCHANT)
        db.session.flush()
        before = db.session.query(Transaction).count()

        with pytest.raises(ValidationError) as refusal:
            self._record(seed_user, line)

        assert "never a purchase" in str(refusal.value)
        assert "Nothing was changed" in str(refusal.value)
        assert db.session.query(TransactionEntry).count() == 0
        assert db.session.query(Transaction).count() == before
        assert db.session.query(StatementMatch).count() == 0

    def test_a_line_that_PAYS_AN_ACCOUNT_is_refused_at_the_door_too(
        self, app, db, seed_user,
    ):
        """The second bar reaches the door, not only the screen.

        A guard that lived only in the reader would be a rendered control
        removed and a route left open, which is the asymmetry every refusal in
        this package is written twice to avoid.
        """
        statement = an_import(seed_user)
        line = _a_card_payment(seed_user, statement)
        db.session.flush()

        with pytest.raises(ValidationError) as refusal:
            self._record(seed_user, line)

        assert "payment to an account you hold" in str(refusal.value)
        assert db.session.query(TransactionEntry).count() == 0

    def test_an_ORDINARY_swipe_still_records_through_the_same_door(
        self, app, db, seed_user,
    ):
        """THE FIRING CONTROL for both refusals above.

        Same door, same submission shape, and it succeeds -- because an
        ordinary swipe IS spending and ruling **R-FS**'s create arm is what
        this whole package exists to offer.  Without this case the two refusals
        above would pass just as well if the door refused everything.

        It is deliberately NOT *the same line once answered*: no answer lifts
        the second bar, which is the correction two adversarial reviews forced
        2026-08-24.
        """
        statement = an_import(seed_user)
        line = _a_swipe(seed_user, statement, amount="-25.00")
        db.session.flush()

        recorded = self._record(seed_user, line)

        assert recorded.amount == Decimal("25.00")
        assert recorded.envelope_created is True

    def test_the_refusal_fires_BEFORE_the_destination_is_resolved(
        self, app, db, seed_user,
    ):
        """The order of refusals, which is what the sentence depends on.

        A barred line has no legal destination, so answering it with "that
        envelope is not one this purchase can go into" would be a true sentence
        about the wrong problem -- the ordering ``_create`` already states for
        its new-envelope pair.  Submitted here with a destination that does not
        exist, so a door resolving first would say so.
        """
        statement = an_import(seed_user)
        line = _a_card_payment(seed_user, statement)
        a_rule(seed_user, CARD_MERCHANT)
        db.session.flush()

        with pytest.raises(ValidationError) as refusal:
            statement_match.create_purchase_from_line(
                PurchaseCreation(line_id=line.id, transaction_id=999_999),
                a_scope(seed_user),
                _create.MintedEnvelopes.none_yet(), a_bars(seed_user),
            )

        assert "never a purchase" in str(refusal.value)


class TestABarredItemCostsOnlyItself:
    """The ruled per-item isolation, applied to this refusal."""

    def test_the_rest_of_the_pass_still_lands(self, app, db, seed_user):
        """A refused item leaves nothing behind and the others still apply.

        The developer's own statement offers 215 acts in one press, so a
        refusal that took the pass down with it would cost every other decision
        made in it -- which is the failure ruling **R-FZ**'s savepoints exist
        for, measured here against the bar this step adds.
        """
        envelope = a_transaction(seed_user, name="Groceries", is_envelope=True)
        statement = an_import(seed_user)
        barred = _a_card_payment(seed_user, statement)
        ordinary = _a_swipe(seed_user, statement, sequence=1)
        a_rule(seed_user, CARD_MERCHANT)
        db.session.commit()

        outcome = statement_match.apply_reviewed(
            ReviewedBatch(matches=(), creations=(
                PurchaseCreation(line_id=barred.id, new_envelope=NewEnvelope(
                    name=CARD_MERCHANT,
                    category_id=seed_user["categories"]["Groceries"].id,
                )),
                PurchaseCreation(
                    line_id=ordinary.id, transaction_id=envelope.id,
                ),
            )),
            a_scope(seed_user),
        )
        db.session.flush()

        assert len(outcome.refused) == 1
        assert outcome.refused[0].line_ids == (barred.id,)
        assert "never a purchase" in outcome.refused[0].reason
        assert outcome.recorded_count == 1
        assert [
            entry.description
            for entry in db.session.query(TransactionEntry).all()
        ] == ["Food Lion"]


class TestTheRuleDoorRefusesTheAnswerThatContradictsTheBar:
    """No answer opens the create arm, so no answer may claim to."""

    def _state(self, seed_user, answer, **fields):
        """Submit one rule statement for the card merchant."""
        return state_rules(
            (RuleSubmission(
                merchant_id=a_merchant(seed_user, CARD_MERCHANT).id,
                answer=answer, **fields,
            ),),
            owner_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
        )

    def test_a_TEMPLATE_answer_is_refused(self, app, db, seed_user):
        """The answer that would name a recurring envelope.

        A merchant whose money paid an account the owner holds cannot be filed
        in a budget line at all, so a stored answer saying it goes in one would
        be an answer nothing could ever apply -- and it would say, in the
        owner's own words on their own screen, that Capital One goes in
        Groceries.
        """
        envelope = a_transaction(seed_user, name="Groceries", is_envelope=True)
        statement = an_import(seed_user)
        _a_card_payment(seed_user, statement)
        db.session.flush()

        outcome = self._state(
            seed_user, RuleAnswer.TEMPLATE, template_id=envelope.template_id,
        )

        assert outcome.stated == ()
        assert len(outcome.refused) == 1
        assert "payment to an account you hold" in outcome.refused[0]
        assert db.session.query(MerchantRule).count() == 0

    def test_a_NEW_ENVELOPE_answer_is_refused(self, app, db, seed_user):
        """The exact answer the developer had saved, and what it cost.

        ``Capital One Credit Card -> a new envelope`` is what booked
        `$7,412.94` into eight `$0.00`-budget envelopes.  While any answer
        lifted the bar, re-picking it restored the create arm AND the one-click
        sweep; two adversarial reviews measured that on 2026-08-24.
        """
        statement = an_import(seed_user)
        _a_card_payment(seed_user, statement)
        db.session.flush()

        outcome = self._state(
            seed_user, RuleAnswer.NEW_ENVELOPE,
            envelope_name=CARD_MERCHANT,
            category_id=seed_user["categories"]["Groceries"].id,
        )

        assert outcome.stated == ()
        assert len(outcome.refused) == 1
        assert db.session.query(MerchantRule).count() == 0

    def test_NEVER_and_a_WITHDRAWAL_are_both_still_taken(
        self, app, db, seed_user,
    ):
        """THE FIRING CONTROL: the door refuses two answers, not four.

        Both of these are TRUE of such a merchant, so both stay legal --
        without this case a door that refused every answer would satisfy the
        two above.
        """
        statement = an_import(seed_user)
        _a_card_payment(seed_user, statement)
        db.session.flush()

        stated = self._state(seed_user, RuleAnswer.NEVER)
        assert stated.refused == ()
        assert db.session.query(MerchantRule).count() == 1

        withdrawn = self._state(seed_user, None)
        assert withdrawn.refused == ()
        assert db.session.query(MerchantRule).count() == 0

    def test_an_ORDINARY_merchant_may_still_be_given_an_envelope(
        self, app, db, seed_user,
    ):
        """The other firing control: the refusal is scoped to the flagged set.

        74 of the developer's 91 unexplained outflows are ordinary swipes, and
        answering for them is the whole of what the rule control is for.
        """
        envelope = a_transaction(seed_user, name="Groceries", is_envelope=True)
        statement = an_import(seed_user)
        _a_swipe(seed_user, statement)
        db.session.flush()

        outcome = state_rules(
            (RuleSubmission(
                merchant_id=a_merchant(seed_user, "Food Lion").id,
                answer=RuleAnswer.TEMPLATE,
                template_id=envelope.template_id,
            ),),
            owner_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
        )

        assert outcome.refused == ()
        assert db.session.query(MerchantRule).count() == 1

    def test_a_STORED_answer_that_became_illegal_is_refused_not_ignored(
        self, app, db, seed_user,
    ):
        """A bank may start filing a merchant as an account payment LATER.

        The answer was legal when it was given.  Refusing a restatement is what
        makes the owner correct it; treating it as unchanged -- which is what
        ``_apply_one``'s own short-circuit would do if this fired after it --
        would leave an answer stored forever that nothing could ever apply.
        """
        envelope = a_transaction(seed_user, name="Groceries", is_envelope=True)
        statement = an_import(seed_user)
        a_rule(seed_user, CARD_MERCHANT, template_id=envelope.template_id)
        _a_card_payment(seed_user, statement)
        db.session.flush()

        outcome = self._state(
            seed_user, RuleAnswer.TEMPLATE, template_id=envelope.template_id,
        )

        assert outcome.unchanged_count == 0
        assert len(outcome.refused) == 1
        assert "payment to an account you hold" in outcome.refused[0]


class TestWhatTheScreenShowsInstead:
    """The parked card, and the arm ruling R-GJ leaves open."""

    def test_a_barred_line_is_PARKED_with_the_reason(
        self, app, db, seed_user,
    ):
        """Not hidden, and not a create row with its control removed.

        A line that vanished from the screen would read as disposed of while
        still counting toward the work outstanding, which is the clean-sweep
        shape ``ReviewBounds`` exists to prevent.
        """
        statement = an_import(seed_user)
        _a_card_payment(seed_user, statement)
        a_rule(seed_user, CARD_MERCHANT)
        db.session.commit()

        review = review_set(a_scope(seed_user))

        assert review.creatable == ()
        assert len(review.parked) == 1
        parked = review.parked[0]
        assert parked.barred_by is CreationBar.NEVER_A_PURCHASE
        assert CARD_MERCHANT in parked.reason
        assert "never a purchase" in parked.reason

    def test_the_two_bars_say_DIFFERENT_things_about_who_decided(
        self, app, db, seed_user,
    ):
        """Why the sentence is derived per bar rather than written once.

        One is a decision the owner made and the other is an observation about
        what the money did.  Telling someone who answered *never* that their
        bank decided for them, or someone whose bank filed a transfer that they
        had once said something they never said, is the collapse the enum
        exists to prevent.
        """
        statement = an_import(seed_user)
        _a_card_payment(seed_user, statement)
        db.session.commit()

        parked = review_set(a_scope(seed_user)).parked[0]

        assert parked.barred_by is CreationBar.PAYS_AN_ACCOUNT_YOU_HOLD
        assert "payment to an account you hold" in parked.reason
        assert "You have said" not in parked.reason

    def test_a_barred_line_is_still_offered_to_the_HAND_MATCH_form(
        self, app, db, seed_user,
    ):
        """The arm ruling R-GJ leaves open, which is the whole remedy.

        A card payment meets the payback rows it repays by being ticked beside
        them and matched, with any difference NAMED (**R-FN**).  The one
        Capital One line handled that way on the developer's own data --
        `-$466.47` on 2026-06-17 -- is grouped with four ``CC Payback`` rows
        whose recorded figures sum to exactly `$466.47`, so that one needed no
        difference at all.  If the bar removed the line from ``unmatched`` as
        well, the whole arm would be unreachable.
        """
        statement = an_import(seed_user)
        line = _a_card_payment(seed_user, statement)
        a_rule(seed_user, CARD_MERCHANT)
        db.session.commit()

        review = review_set(a_scope(seed_user))

        assert [row.line_id for row in review.unmatched] == [line.id]

    def test_the_group_match_arm_ACTUALLY_WORKS_on_a_barred_line(
        self, app, db, seed_user,
    ):
        """The remedy, PERFORMED rather than advertised.

        Every other case here stops at *the checkbox is rendered*.  Ruling
        **R-GJ** leaves exactly one arm open -- match the payment to the rows
        it repaid -- and the parked card, the refusal sentence and this
        module's own docstring all send the owner to it, so an arm graded only
        by its own advertisement is an arm nobody has shown works.  Named by an
        adversarial review 2026-08-24.

        The shape is the one measured on the developer's own data: one card
        payment against several of the owner's rows whose figures sum to it
        exactly, so no difference is left to name.
        """
        first = a_transaction(
            seed_user, name="CC Payback: Groceries", amount="500.00",
        )
        second = a_transaction(
            seed_user, name="CC Payback: Gas", amount="293.23",
        )
        statement = an_import(seed_user)
        line = _a_card_payment(seed_user, statement)
        a_rule(seed_user, CARD_MERCHANT)
        db.session.commit()
        scope = a_scope(seed_user)

        outcome = statement_match.apply_reviewed(
            ReviewedBatch(
                matches=(a_submission(
                    scope, lines=[line], transactions=[first, second],
                ),),
                creations=(),
            ),
            scope,
        )
        db.session.commit()

        assert not outcome.refused, [item.reason for item in outcome.refused]
        assert outcome.applied_count == 1
        # ...and the line is no longer anybody's question: not parked, not
        # creatable, not unmatched.
        after = review_set(a_scope(seed_user))
        assert after.parked == ()
        assert after.creatable == ()
        assert [row.line_id for row in after.unmatched] == []
        # The bar refused a PURCHASE and never touched the match: both rows
        # took the bank's day.
        assert {first.settled_on, second.settled_on} == {line.posted_on}

    def test_the_rule_control_says_WHY_it_is_asking(
        self, app, db, seed_user,
    ):
        """The row where the bar is lifted has to be findable.

        An unanswered card-payment merchant has no create arm, and this control
        is the only door that gives it one back -- so a row that did not say
        which merchant the bank had flagged would leave the parked card naming
        an act with no target.
        """
        statement = an_import(seed_user)
        _a_card_payment(seed_user, statement)
        _a_swipe(seed_user, statement, sequence=1)
        db.session.commit()

        rows = {
            row.merchant: row
            for row in review_set(a_scope(seed_user)).merchants.merchants
        }

        assert rows[CARD_MERCHANT].pays_an_account is True
        assert rows[CARD_MERCHANT].line_count == 1
        assert rows[CARD_MERCHANT].total == Decimal("-793.23")
        assert rows["Food Lion"].pays_an_account is False

    def test_the_flag_is_carried_WHATEVER_the_owner_has_said(
        self, app, db, seed_user,
    ):
        """THE FIRING CONTROL for *carried whatever they have already said*.

        Narrow the flag to ``merchant not in answered`` and the whole suite
        stayed green until this case: measured by two adversarial reviews
        2026-08-24.  It matters because the flag is what the row says INSTEAD
        of offering the two answers the door refuses -- an owner who stated
        *never a purchase* is entitled to see why that was the only answer that
        fit, and one whose stored answer predates the bank filing this merchant
        as an account payment is entitled to see why their Save is now being
        refused.
        """
        statement = an_import(seed_user)
        _a_card_payment(seed_user, statement)
        a_rule(seed_user, CARD_MERCHANT)
        db.session.commit()

        row = {
            item.merchant: item
            for item in review_set(a_scope(seed_user)).merchants.merchants
        }[CARD_MERCHANT]

        assert row.rule.answer is RuleAnswer.NEVER
        assert row.pays_an_account is True
