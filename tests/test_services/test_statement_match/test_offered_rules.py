"""What standing answer a pass EARNS the right to ask about.

Plan step ``bank_import:X-gj-1b``, ruling **bank_import:R-IB** (developer,
2026-08-30).  The Reconcile card carried an *always, for this merchant*
checkbox until that ruling; the offer is on the RECEIPT now, once per merchant,
about what the door actually APPLIED.

**The narrowing is graded HERE and not through the route**, and the reason is
worth writing down: a first version tested it by posting a creation the money
door would refuse and asserting no offer came back -- and it passed with the
narrowing DELETED, because the stale destination it used was dropped by a
different arm of the same function (*a destination the scope does not offer is
skipped*).  Two arms, one observable outcome, and the case could not tell which
had fired.  ``applied`` is a parameter, so a test can state it -- and since
plan step ``bank_import:X-gx`` it is stated by running the REAL door
(:func:`_filed`), because the items carry the merchant the door filed for and
an item a test wrote for itself would agree with the offer by construction.

**Since that step the offer reads the MERCHANT off the receipt too** (finding
**BI-495**): it read each line's merchant off the page's pre-lock derivation
until then, so a re-import naming a merchant between the derivation and the
press -- plan step ``bank_import:X-gv``'s window -- was filed under by the door
and asked about by nobody.  :class:`TestTheOfferNamesTheMerchantTheDoorFiledFor`
reproduces that on the tree before the step.
"""

from datetime import timedelta

from sqlalchemy import text

from app.models.category import Category
from app.services import statement_match
from app.services.statement_match import (
    Consent,
    NewEnvelope,
    PurchaseCreation,
    ReviewedBatch,
    RuleAnswer,
    RuleDoorAccepts,
    review_set,
    rules_worth_offering,
)
from app.services.statement_match._outcome import FiledMerchant
from app.services.statement_match._rules import offerable_templates
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_merchant,
    a_scope,
    an_envelope,
    an_import,
    an_unexplained_outflow,
    filed_by,
    the_merchant_id,
)


def _two_swipes(seed_user, db, merchant="Lowe's"):
    """Stage two outflows of one merchant, and an envelope to file them in.

    Returns:
        ``(envelope, first, second)``.
    """
    envelope = an_envelope(seed_user, name="Home Improvement")
    first = an_unexplained_outflow(
        seed_user, merchant=merchant, amount="-35.72",
    )
    second = an_unexplained_outflow(
        seed_user, merchant=merchant, amount="-12.10",
    )
    db.session.commit()
    return envelope, first, second


def _filed(scope, *creations):
    """Run the REAL create door over *creations* and return the receipt's items.

    **Through ``apply_reviewed`` and never by constructing an ``AppliedItem``**,
    for the reason :func:`_accepts` states about the door's set: an item a test
    wrote for itself carries the merchant the test chose, and the offer would
    agree with it by construction.  The door reads the merchant off the line
    it holds locked, which is the fact these cases grade.

    Args:
        scope: The pass to apply against.
        *creations: :class:`PurchaseCreation` values -- the same acts
            :func:`_creations` states to the offer in the schema's shape, since
            the route hands the offer both.

    Returns:
        :attr:`~app.services.statement_match._outcome.BatchOutcome.applied`.
    """
    return statement_match.apply_reviewed(
        ReviewedBatch(
            consent=Consent.TICKED, matches=(), incomes=(), skips=(),
            creations=creations,
        ),
        scope,
    ).applied


def _into(envelope, *lines):
    """Return the :class:`PurchaseCreation` filing each of *lines* into *envelope*."""
    return tuple(
        PurchaseCreation(line_id=line.id, transaction_id=envelope.id)
        for line in lines
    )


def _minting(name, category_id, *lines):
    """Return the :class:`PurchaseCreation` filing each line into a NEW envelope."""
    return tuple(
        PurchaseCreation(
            line_id=line.id,
            new_envelope=NewEnvelope(name=name, category_id=category_id),
        )
        for line in lines
    )


def _accepts(db, seed_user):
    """Return what the rule door would take, from its own producers.

    **Built from `offerable_templates` and the active-category query the door
    itself validates against**, never hand-listed: a fixture that stated the
    accepted set for itself would agree with the offer by construction, which
    is the whole defect these cases exist for -- the OFFER set and the DOOR's
    set are different sets, and an adversarial review reproduced both ways
    they part.
    """
    return RuleDoorAccepts(
        template_ids=frozenset(offerable_templates(seed_user["account"].id)),
        category_ids=frozenset(
            row.id for row in db.session.query(Category).filter(
                Category.user_id == seed_user["user"].id,
                Category.is_active.is_(True),
            )
        ),
    )


def _creations(envelope, *lines):
    """Return the creation items a pass filing *lines* into *envelope* holds."""
    return [
        {
            "line_id": line.id,
            "destination": envelope.id,
            "envelope_name": "",
            "category_id": None,
        }
        for line in lines
    ]


class TestTheOfferIsWhatTheDoorAPPLIEDAndNotWhatWasOKd:
    """The HIGH this ruling exists for.

    ``apply_reviewed`` runs each item in its own SAVEPOINT (ruling
    **R-FZ(a)**), so a refused creation rolls back while the pass commits.  The
    rule used to be derived from what was OK'd and computed BEFORE that door
    ran, so a merchant was filed standing for a purchase that never happened --
    and the NEXT import would auto-file it with no press at all.
    """

    def test_a_line_the_door_REFUSED_earns_no_offer(
        self, app, db, seed_user,
    ):
        """One OK'd, one refused: only the one that landed is offered.

        The second line is already explained by a match, so the create door
        refuses it by name (``load_lines``) inside its own savepoint -- a
        REAL refusal, with a destination the offer set holds, so the only
        thing keeping it out of the offer is that the door did not apply it.
        """
        envelope, first, second = _two_swipes(seed_user, db)
        garden = an_envelope(seed_user, name="Garden")
        db.session.commit()
        filed_by(seed_user, second, garden, by_rule=False)
        db.session.commit()
        scope = a_scope(seed_user)

        applied = _filed(scope, *_into(envelope, first, second))
        assert [item.line_ids for item in applied] == [(first.id,)]
        offers = rules_worth_offering(
            _creations(envelope, first, second), applied, scope,
            _accepts(db, seed_user),
        )

        assert len(offers) == 1
        assert offers[0].filed_count == 1, (
            "the offer counted a purchase the door refused"
        )

    def test_a_pass_that_applied_NOTHING_offers_NOTHING(
        self, app, db, seed_user,
    ):
        """The whole-pass shape of the same rule: both refused, no offer."""
        envelope, first, second = _two_swipes(seed_user, db)
        garden = an_envelope(seed_user, name="Garden")
        db.session.commit()
        filed_by(seed_user, first, garden, by_rule=False)
        filed_by(seed_user, second, garden, by_rule=False)
        db.session.commit()
        scope = a_scope(seed_user)

        applied = _filed(scope, *_into(envelope, first, second))
        assert applied == ()
        offers = rules_worth_offering(
            _creations(envelope, first, second), applied, scope,
            _accepts(db, seed_user),
        )

        assert offers == ()


class TestOneMerchantIsAskedAboutOnce:
    """The grain the ruling corrects.

    A rule is ONE fact per merchant and the card was one LINE, so the page
    asked one question 86 times on the developer's own pass -- Amazon 26,
    Walmart 13, Food Lion 12.
    """

    def test_two_lines_into_one_envelope_are_ONE_answer(
        self, app, db, seed_user,
    ):
        """A rule keys on the TEMPLATE, so two periods' rows are one answer.

        This is why the measured contradiction rate on the developer's own
        data is zero across all ten of his repeated merchants: every pay
        period's copy of an envelope resolves to the same rule.
        """
        envelope, first, second = _two_swipes(seed_user, db)
        scope = a_scope(seed_user)

        offers = rules_worth_offering(
            _creations(envelope, first, second),
            _filed(scope, *_into(envelope, first, second)),
            scope,
            _accepts(db, seed_user),
        )

        assert len(offers) == 1
        offer = offers[0]
        assert offer.merchant_id == the_merchant_id(seed_user, "Lowe's")
        assert offer.filed_count == 2
        assert offer.is_split is False, (
            "two lines into one envelope were read as a split merchant"
        )
        assert len(offer.answers) == 1
        assert offer.answers[0].count == 2
        assert offer.answers[0].statement.answer is RuleAnswer.TEMPLATE
        assert offer.answers[0].statement.template_id == envelope.template_id

    def test_two_envelopes_are_a_SPLIT_the_owner_must_choose_between(
        self, app, db, seed_user,
    ):
        """Splitting a merchant is a real thing to do, not an error.

        What it means for the OFFER is that the app cannot know which should
        stand, so the receipt asks -- and because the question is asked once,
        there is no form in which both are submitted.
        """
        first_envelope, first, second = _two_swipes(seed_user, db)
        other = an_envelope(seed_user, name="Garden")
        db.session.commit()
        scope = a_scope(seed_user)

        offers = rules_worth_offering(
            _creations(first_envelope, first) + _creations(other, second),
            _filed(scope, *_into(first_envelope, first), *_into(other, second)),
            scope,
            _accepts(db, seed_user),
        )

        assert len(offers) == 1, "one merchant must still be asked about once"
        offer = offers[0]
        assert offer.is_split is True
        assert offer.filed_count == 2
        assert {answer.count for answer in offer.answers} == {1}
        assert len(offer.offerable) == 2, (
            "both template answers can travel, so both must be offered"
        )


class TestTheOfferNamesTheDestinationTheWayARuleMEANSIt:
    """A standing answer applies to EVERY pay period, so its name must too."""

    def test_a_template_answer_is_named_without_its_pay_period(
        self, app, db, seed_user,
    ):
        """The card's label and the offer's label are different questions.

        ``PurchaseDestination.label`` carries the period -- *Groceries
        (2026-08-13 - 2026-08-26)* -- because an owner CHOOSING a destination
        has to tell two copies of one envelope apart. Borrowing it here would
        read *always file Walmart in Groceries for that fortnight*, which is
        not what pressing it does: the rule names the TEMPLATE. Caught by
        rendering the offer against a production clone, where every one of the
        developer's template answers was labelled with one period's span.
        """
        envelope, first, second = _two_swipes(seed_user, db)
        scope = a_scope(seed_user)

        offers = rules_worth_offering(
            _creations(envelope, first, second),
            _filed(scope, *_into(envelope, first, second)),
            scope,
            _accepts(db, seed_user),
        )

        label = offers[0].answers[0].label
        assert label == "Home Improvement", label
        assert "(" not in label, (
            "the offer named a pay period, so it promises a rule that expires"
        )


class TestAnAnswerTheRuleDoorWouldREFUSEIsNotOffered:
    """The offer set and the DOOR's set are different sets.

    ``destinations_for`` never asks whether a template is still active, and
    ``archive_template`` soft-deletes only PROJECTED rows -- so a settled
    envelope from an archived template survives as a real destination this
    pass can file into, and ``state_rules`` refuses it. An adversarial review
    reproduced both this and the archived-category arm on 2026-08-30, each
    rendering as a radio the owner could press that could never succeed --
    the *chooser whose submission can never succeed* shape this package's own
    docstrings say it has closed four times.
    """

    def test_a_template_the_rule_door_does_not_offer_is_BLOCKED(
        self, app, db, seed_user,
    ):
        """Offered as a destination, refused as a standing answer."""
        envelope, first, second = _two_swipes(seed_user, db)
        scope = a_scope(seed_user)
        accepts = _accepts(db, seed_user)

        offers = rules_worth_offering(
            _creations(envelope, first, second),
            _filed(scope, *_into(envelope, first, second)),
            scope,
            # The door's set, minus the very template this pass filed into.
            RuleDoorAccepts(
                template_ids=accepts.template_ids - {envelope.template_id},
                category_ids=accepts.category_ids,
            ),
        )

        offer = offers[0]
        assert offer.offerable == (), (
            "an answer the rule door would refuse was rendered as pressable"
        )
        assert len(offer.unofferable) == 1
        assert "no longer offered" in offer.unofferable[0].blocked
        assert offer.filed_count == 2, "the receipt lost what it filed"

    def test_a_new_envelope_under_an_ARCHIVED_category_is_BLOCKED(
        self, app, db, seed_user,
    ):
        """The second arm, which the register's own control does not cover."""
        _, first, second = _two_swipes(seed_user, db)
        scope = a_scope(seed_user)
        accepts = _accepts(db, seed_user)
        category_id = next(iter(accepts.category_ids))

        offers = rules_worth_offering(
            [{
                "line_id": first.id, "destination": "new",
                "envelope_name": "Decking", "category_id": category_id,
            }],
            _filed(scope, *_minting("Decking", category_id, first)),
            scope,
            RuleDoorAccepts(
                template_ids=accepts.template_ids,
                category_ids=accepts.category_ids - {category_id},
            ),
        )

        offer = offers[0]
        assert offer.offerable == ()
        assert "archived" in offer.unofferable[0].blocked

    def test_an_ACCEPTED_answer_carries_no_block(
        self, app, db, seed_user,
    ):
        """The control's other side: the ordinary case is still offered.

        Without this, a version that blocked EVERYTHING would pass both cases
        above -- the failure mode a refusal-only pair cannot see.
        """
        envelope, first, _ = _two_swipes(seed_user, db)
        scope = a_scope(seed_user)

        offers = rules_worth_offering(
            _creations(envelope, first),
            _filed(scope, *_into(envelope, first)),
            scope,
            _accepts(db, seed_user),
        )

        assert offers[0].answers[0].blocked is None
        assert len(offers[0].offerable) == 1


class TestWhatTheWireCannotCarryIsNAMEDAndNotDropped:
    """``rule_name-<key>`` is paired with the MERCHANT, not with the option.

    That pairing is what makes one answer per merchant structural, and its
    price is that a merchant filed into two DIFFERENT new envelopes has two
    answers and one pair of name fields.  Only one can travel; the other is
    reported, because the receipt says what it filed and where.
    """

    def test_a_second_new_envelope_is_reported_but_not_offered(
        self, app, db, seed_user,
    ):
        """The narrow case, stated so the counts still add up."""
        _, first, second = _two_swipes(seed_user, db)
        scope = a_scope(seed_user)
        category_id = seed_user["categories"]["Groceries"].id
        creations = [
            {
                "line_id": first.id, "destination": "new",
                "envelope_name": "Decking", "category_id": category_id,
            },
            {
                "line_id": second.id, "destination": "new",
                "envelope_name": "Fencing", "category_id": category_id,
            },
        ]

        offers = rules_worth_offering(
            creations,
            _filed(
                scope,
                *_minting("Decking", category_id, first),
                *_minting("Fencing", category_id, second),
            ),
            scope,
            _accepts(db, seed_user),
        )

        offer = offers[0]
        assert offer.filed_count == 2, "the receipt lost a purchase it filed"
        assert len(offer.offerable) == 1
        assert offer.offerable[0].statement.envelope_name == "Decking"
        assert len(offer.unofferable) == 1
        assert offer.unofferable[0].statement.envelope_name == "Fencing"


class TestTheOfferNamesTheMerchantTheDoorFiledFor:
    """Finding **BI-495**, plan step ``bank_import:X-gx``.

    The press runs the page's derivation (``review_set``) and then the door
    in one transaction, and the door reads its line under a row lock that
    hands back the row as it stands (plan step ``bank_import:X-gv``).  The
    offer read each line's merchant off that DERIVATION until this step, so a
    re-import naming the merchant between the two -- the 10-column export
    first, the one with the merchant column second -- was the merchant the
    door filed under and not the one the offer asked about: the door applied
    the creation and the receipt offered nothing.  **Reproduced on the tree
    before this step**: ``applied_count == 1`` and ``offers == ()``.
    """

    def test_a_merchant_filled_after_derivation_is_the_one_offered(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: derive the merchant from ``review`` and this reads ``()``."""
        with app.app_context():
            envelope = an_envelope(seed_user)
            line = a_bank_line(
                seed_user, an_import(seed_user), amount="-57.96",
                posted_on=seed_user["bootstrap_period"].start_date + timedelta(days=5),
                description="POINT OF SALE DEBIT L340 THING",
            )
            amazon = a_merchant(seed_user, "Amazon")
            db.session.commit()
            scope = a_scope(seed_user)
            review = review_set(scope)
            # What the screen showed: the line, with no merchant to answer for.
            assert {each.line_id: each.merchant_id for each in review.unmatched} == {
                line.id: None,
            }
            # The re-import's NULL-fill, landing from a second connection
            # between the derivation and the press.
            with db.engine.connect() as connection:
                connection.execute(
                    text(
                        "UPDATE budget.bank_statement_lines "
                        "SET merchant_id = :merchant WHERE id = :id"
                    ),
                    {"merchant": amazon.id, "id": line.id},
                )
                connection.commit()

            applied = _filed(scope, *_into(envelope, line))
            offers = rules_worth_offering(
                _creations(envelope, line), applied, scope,
                _accepts(db, seed_user),
            )

            assert [item.merchant for item in applied] == [
                FiledMerchant(merchant_id=amazon.id, name="Amazon"),
            ]
            assert [(offer.merchant_id, offer.merchant) for offer in offers] == [
                (amazon.id, "Amazon"),
            ]
            assert offers[0].filed_count == 1
            db.session.rollback()

    def test_a_line_naming_no_merchant_earns_no_offer_and_its_item_says_so(
        self, app, db, seed_user,
    ):
        """There is nobody to answer for, and the receipt item carries ``None``.

        The control for the filter: an offer built over every applied item
        would ask about a merchant that does not exist.
        """
        with app.app_context():
            envelope = an_envelope(seed_user)
            line = a_bank_line(
                seed_user, an_import(seed_user), amount="-57.96",
                posted_on=seed_user["bootstrap_period"].start_date + timedelta(days=5),
                description="POINT OF SALE DEBIT L340 THING",
            )
            db.session.commit()
            scope = a_scope(seed_user)

            applied = _filed(scope, *_into(envelope, line))
            offers = rules_worth_offering(
                _creations(envelope, line), applied, scope,
                _accepts(db, seed_user),
            )

            assert [item.line_ids for item in applied] == [(line.id,)]
            assert applied[0].merchant is None
            assert offers == ()
            db.session.rollback()
