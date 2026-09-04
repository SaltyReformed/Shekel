"""What the OPENED card discloses, and the rows its MATCH tab offers.

Plan step ``bank_import:X-gj-1b``; rulings **bank_import:R-HP**, **R-HR**,
**R-HS**, **R-HW** and **R-HX**.

**The subject is the boundary ruling R-HR draws.**  A card shows the decision;
the panel behind it holds every reason, every verb and every control -- so
these cases are about which act a verb performs for a line, which tab opens,
and which of the account's rows a line may be paired against.
"""

from decimal import Decimal

from datetime import timedelta

import pytest

# Pylint: ``shekel-private-module-import`` -- a test of a service's
# INTERNALS reaches for them by name, which is the convention this package's
# own test modules already keep (``test_bars``, ``test_candidates``,
# ``test_reconcile``).  The alternative is widening the package's public
# surface for the tests alone.
# pylint: disable=shekel-private-module-import
from app.services.statement_match._cards import to_explain_sections
from app.services.statement_match._panel import (
    AddAct,
    MatchCandidates,
)
from app.services.statement_match._placement import placements_for
from app.services.statement_match._reads import review_set
from app.services.statement_match._rules import RuleAnswer, RuleView
from app.services.statement_match._stating import (
    rule_creating,
    rule_naming,
    state_rules,
)
from app.services.statement_match._verbs import Verb

from ._builders import (
    a_bank_line,
    a_later_period,
    a_rule,
    a_merchant,
    a_scope,
    a_transaction,
    an_envelope,
    an_import,
    an_unexplained_outflow,
    the_merchant_id,
)

#: What SECU files a card payment under, which ruling **R-GJ** reads.
_CARD_PAYMENT = "Financial Services/Credit Card Payment"


def _cards(seed_user):
    """Return the inbox's cards for the seeded account, with their candidates.

    Args:
        seed_user: The seeded user bundle.

    Returns:
        A ``(cards, candidates)`` pair -- the cards in section order, and the
        index they were built from, so a case can assert the two agree.
    """
    scope = a_scope(seed_user)
    review = review_set(scope)
    candidates = MatchCandidates.of(scope, review)
    sections = to_explain_sections(review)
    return (
        [card for section in sections for card in section.cards],
        candidates,
    )


class TestASectionRendersNewestFirst:
    """The locked direction's own rule, and it was not kept.

    ``docs/design/bank_import_audit.md``: *Within a section, newest first*.
    The pass hands its lines over ASCENDING by day, so every section rendered
    oldest first -- the owner's most recent swipes, the ones they can still
    remember, at the bottom of a 27-card list on their own data.
    """

    def test_the_most_recent_bank_day_is_the_first_card(
        self, app, db, seed_user,
    ):
        """Three swipes on three days, newest at the top."""
        an_envelope(seed_user)
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        for offset, merchant in ((0, "Oldest Co"), (2, "Middle Co"),
                                 (5, "Newest Co")):
            a_bank_line(
                seed_user, statement, amount="-10.00",
                posted_on=day + timedelta(days=offset), merchant=merchant,
            )
        db.session.commit()

        cards, _ = _cards(seed_user)
        merchants = [card.line.merchant_label for card in cards]

        assert merchants[:3] == ["Newest Co", "Middle Co", "Oldest Co"], (
            f"the section is not newest first: {merchants[:3]}"
        )


class TestTheADDVerbSaysWHICHActItPerforms:
    """Ruling **R-HP** names ADD once and the app has TWO doors under it.

    A purchase is filed into a container the owner picks between; an income
    row is filed against nothing (ruling **bank_import:R-GW**).  They take
    different submissions and offer different controls, so the card states
    which -- and no template asks a bank line's SIGN, which would be a second
    spelling of ``_recordable_inflows``' own partition.
    """

    def test_an_outflow_the_create_door_would_take_records_a_PURCHASE(
        self, app, db, seed_user,
    ):
        """The ordinary swipe."""
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user, merchant="Lowe's", amount="-35.72")
        db.session.commit()

        card = _cards(seed_user)[0][0]

        assert card.panel.add.act is AddAct.PURCHASE
        assert card.panel.add.records_a_purchase
        assert not card.panel.add.records_income

    def test_an_unmatched_deposit_records_INCOME(self, app, db, seed_user):
        """Ruling **bank_import:R-GW**'s other direction."""
        statement = an_import(seed_user)
        a_bank_line(
            seed_user, statement, amount="2573.42",
            posted_on=seed_user["bootstrap_period"].start_date,
            description="ACH DEPOSIT TOWN OF CLAYTON PAYROLL",
        )
        db.session.commit()

        card = _cards(seed_user)[0][0]

        assert card.panel.add.act is AddAct.INCOME
        assert card.panel.add.records_income
        assert not card.panel.add.records_a_purchase
        # **Filed against NO container**, which is the whole difference.
        assert card.panel.add.destinations == ()
        assert card.panel.add.placement is None

    def test_the_two_predicates_PARTITION_the_enum(self):
        """Neither arm may be silently added to.

        The template renders one arm each with no ``else``, so a third act
        added without a pane would render NOTHING -- which is why the
        partition is graded here rather than trusted.
        """
        assert set(AddAct) == {AddAct.PURCHASE, AddAct.INCOME}

    def test_a_line_ruling_R_GJ_bars_has_no_ADD_ACT_AT_ALL(
        self, app, db, seed_user,
    ):
        """A bar is not a shortage of destinations.

        No answer lifts it (**R-GJ**), so there is nothing for the tab to
        offer beyond the refusal its own offer already carries -- and a value
        holding an empty ``AddTab`` would be a control one Jinja condition
        away from rendering.
        """
        an_envelope(seed_user)
        an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()

        scope = a_scope(seed_user)
        review = review_set(scope)
        candidates = MatchCandidates.of(scope, review)
        # pylint: disable-next=shekel-private-module-import
        from app.services.statement_match._cards import parked_card
        card = parked_card(review, review.parked[0])

        assert card.panel.add is None
        assert not card.panel.offer_for(Verb.ADD).is_open


class TestEveryOpenVerbHasSomethingToOfferAndEveryCardOpensOnOne:
    """Ruling **R-HW**, read as an invariant rather than as a screen.

    The template renders a pane per verb and dispatches on what that verb's
    tab offers, so an OPEN ADD with no :class:`AddTab` would render a heading
    and no control -- the silent gap this pins.
    """

    def _a_mixed_inbox(self, seed_user, db):
        """Stage one outflow, one deposit and one barred payment."""
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user, merchant="Lowe's", amount="-35.72")
        an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            sequence=1, source_category=_CARD_PAYMENT,
        )
        statement = an_import(seed_user)
        a_bank_line(
            seed_user, statement, amount="2573.42", sequence_in_group=2,
            posted_on=seed_user["bootstrap_period"].start_date,
            description="ACH DEPOSIT TOWN OF CLAYTON PAYROLL",
        )
        db.session.commit()

    def test_an_OPEN_add_always_carries_the_tab_that_serves_it(
        self, app, db, seed_user,
    ):
        """The invariant the ADD pane's two arms rest on."""
        self._a_mixed_inbox(seed_user, db)

        for card in _cards(seed_user)[0]:
            if card.panel.offer_for(Verb.ADD).is_open:
                assert card.panel.add is not None, (
                    f"line {card.line.line_id} offers ADD and says nothing "
                    f"about what ADD would do"
                )

    def test_a_card_opens_on_a_verb_the_panel_actually_renders(
        self, app, db, seed_user,
    ):
        """``opens_on`` is total over the four, whatever the pass found."""
        self._a_mixed_inbox(seed_user, db)

        for card in _cards(seed_user)[0]:
            assert card.opens_on in {
                offer.verb for offer in card.panel.offers
            }

    def test_a_suggestion_opens_on_ITS_OWN_verb(self, app, db, seed_user):
        """Ruling **R-HS**'s pre-fill, read at the grain of the tab."""
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user, merchant="Lowe's", amount="-35.72")
        db.session.commit()
        # A rule the owner stated, so the card suggests ADD.
        a_rule(
            seed_user, "Lowe's", envelope_name="Home Improvement",
            category_id=seed_user["categories"]["Groceries"].id,
        )
        db.session.commit()

        card = _cards(seed_user)[0][0]

        assert card.suggested is Verb.ADD
        assert card.opens_on is Verb.ADD

    def test_a_card_with_NO_suggestion_opens_on_its_own_ADD_act(
        self, app, db, seed_user,
    ):
        """Ruling **R-HX** leaves most inbox cards with no suggestion.

        Measured 2026-08-29 on the developer's own account: 16 of his 18
        inbox cards read *Choose what this is*, and opening every one of them
        on MATCH would put the only act they have behind a tab.
        """
        statement = an_import(seed_user)
        a_bank_line(
            seed_user, statement, amount="2573.42",
            posted_on=seed_user["bootstrap_period"].start_date,
            description="ACH DEPOSIT TOWN OF CLAYTON PAYROLL",
        )
        db.session.commit()

        card = _cards(seed_user)[0][0]

        assert card.suggested is None
        assert card.opens_on is Verb.ADD

    def test_a_parked_line_opens_on_the_verb_that_EXPLAINS_it(
        self, app, db, seed_user,
    ):
        """A shut tab carrying its reason is a disclosure, not a control."""
        an_envelope(seed_user)
        an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()

        scope = a_scope(seed_user)
        review = review_set(scope)
        # pylint: disable-next=shekel-private-module-import
        from app.services.statement_match._cards import parked_card
        card = parked_card(review, review.parked[0])

        assert card.opens_on is Verb.TRANSFER
        assert not card.panel.offer_for(Verb.TRANSFER).is_open
        assert card.panel.offer_for(Verb.TRANSFER).waiting_for


class TestTheMatchTabOffersThePeriodAndTheSearchReachesFurther:
    """The developer's ruling of 2026-08-30, and the measurement behind it.

    The PERIOD is what a card renders with the page, so the payroll group a
    card is usually about needs no round trip.  The SEARCH is over every
    unexplained row on the account, because a bound that cannot be widened is
    the cap finding **N-374** refused -- and all 9 of the developer's 9 card
    payments have payback rows their own period does NOT hold.
    """

    def _a_deposit_and_two_rows(self, seed_user, db):
        """Stage a payroll deposit beside the two rows that explain it."""
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="2573.42",
            posted_on=seed_user["bootstrap_period"].start_date,
            description="ACH DEPOSIT TOWN OF CLAYTON PAYROLL",
        )
        a_transaction(
            seed_user, name="Data Manager", amount="2473.38", income=True,
        )
        a_transaction(
            seed_user, name="Health Insurance Allowance", amount="100.00",
            income=True,
        )
        db.session.commit()
        return line

    def test_the_lines_own_period_holds_the_rows_that_explain_it(
        self, app, db, seed_user,
    ):
        """Finding **salary:N-391**'s own case, reachable in one click.

        Measured 2026-08-30 on the developer's own account: the 2026-03-26
        deposit of `$2,573.42` finds ``Health Insurance Allowance`` `$100.00`
        and ``Data Manager`` `$2,473.38` in its own period, a difference of
        `$0.04`.  *(Cited **N-239** until `balance:X-aw` retired that row and
        split its bank half off as **N-391**.)*

        **Asserted against the INDEX rather than against a card.**  The card
        carried its line's rows until plan step ``bank_import:X-gj-1b`` and
        rendered none of them -- the panel's list is lazy-loaded, so the
        fragment asks :class:`MatchCandidates` for itself -- so the field went
        and this asks the producer that still answers the question.
        """
        line = self._a_deposit_and_two_rows(seed_user, db)

        cards, candidates = _cards(seed_user)
        labels = {
            row.label for row in candidates.for_line(cards[0].line)
        }
        assert cards[0].line.line_id == line.id, (
            "the deposit is not the first card, so this asks about the wrong "
            "line"
        )

        assert "Data Manager" in labels
        assert "Health Insurance Allowance" in labels

    def test_the_search_reaches_a_row_the_period_does_not_hold(
        self, app, db, seed_user,
    ):
        """The half that keeps a card payment groupable.

        A row budgeted in a LATER period is outside the line's own window and
        is not on its card; the search finds it, because the alternative is
        the cap finding **N-374** refused -- *the row that explains a line may
        be number 51*.  Measured 2026-08-30 on the developer's own account:
        all 9 of his 9 card payments have payback rows their own period does
        not hold.

        **The second bank line is what makes the case real rather than
        arranged**: the pass offers only rows inside the span its statements
        cover (``_rows_the_bank_never_showed``), so a row in a period no
        statement reaches is a candidate NOWHERE -- and a case that skipped
        the line would have been asserting against that bound instead of
        against this one.
        """
        self._a_deposit_and_two_rows(seed_user, db)
        later = a_later_period(seed_user)
        # **A figure NO tier can pair with the row**, deliberately: an
        # exact-amount line would be PROPOSED against it, and a proposal
        # withholds both its line and its row from the pass's unexplained
        # sets -- so the case would have graded an empty pool rather than
        # this bound.  Found by running it.
        a_bank_line(
            seed_user, an_import(seed_user), amount="-2490.65",
            posted_on=later.start_date, sequence_in_group=1,
            description="ACH DEBIT CAPITAL ONE MOBILE PMT",
        )
        a_transaction(
            seed_user, name="Rogue Equipment Payback", amount="1958.87",
            period=later,
        )
        db.session.commit()

        cards, candidates = _cards(seed_user)
        deposit = next(
            card for card in cards if card.line.amount > 0
        )
        on_the_card = {
            row.label for row in candidates.for_line(deposit.line)
        }
        found = {row.label for row in candidates.matching("Rogue")}

        assert "Rogue Equipment Payback" not in on_the_card
        assert "Rogue Equipment Payback" in found

    def test_the_search_matches_a_FIGURE_as_readily_as_a_name(
        self, app, db, seed_user,
    ):
        """An owner knows a row by its money as often as by its name."""
        self._a_deposit_and_two_rows(seed_user, db)

        _, candidates = _cards(seed_user)

        assert {row.label for row in candidates.matching("2473.38")} == {
            "Data Manager",
        }

    def test_an_EMPTY_search_matches_nothing(self, app, db, seed_user):
        """An empty search is not a search; the caller renders the period."""
        self._a_deposit_and_two_rows(seed_user, db)

        _, candidates = _cards(seed_user)

        assert candidates.matching("") == ()
        assert candidates.matching("   ") == ()

    def test_the_index_answers_for_EVERY_line_the_page_cards(
        self, app, db, seed_user,
    ):
        """No card may be one the pane cannot price.

        **This asked a different question until plan step
        ``bank_import:X-gj-1b``**: whether each card's own copy of the row
        list matched the index it came from. That copy is gone -- the builders
        stated it and the templates rendered none of it -- and comparing a
        value to the producer it was assigned from could only ever fail if a
        BUILDER forgot to pass it, which the type system now settles because
        there is nothing to pass.

        What is worth pinning is the fact the pane depends on: every line the
        page renders a card for resolves in the index, so no card can open on
        a tab that cannot answer. That is the same property
        ``ReviewSet.card_subject`` keeps on the route side.
        """
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user, merchant="Lowe's", amount="-35.72")
        self._a_deposit_and_two_rows(seed_user, db)

        cards, candidates = _cards(seed_user)

        assert cards
        for card in cards:
            assert candidates.for_line(card.line) is not None


class TestARuleNamesTheDestinationTheCardChose:
    """Plan step ``bank_import:X-gj-1b``: the ADD tab's *always* control.

    The rule is read back off the DESTINATION the same card submits, so the
    two can never name different budget lines -- and the mapping is TOTAL,
    which is what lets the control be offered on every purchase card rather
    than withheld on some.
    """

    def test_a_TEMPLATE_generated_row_is_named_by_its_template(
        self, app, db, seed_user,
    ):
        """The identity across pay periods ruling **R-GA** says a rule needs."""
        envelope = an_envelope(seed_user)
        db.session.commit()
        scope = a_scope(seed_user)
        destination = next(
            one for one in scope.destinations
            if one.transaction_id == envelope.id
        )
        assert destination.template_id is not None, (
            "this case needs a templated destination or it grades nothing"
        )

        rule = rule_naming(7, destination)

        assert rule.answer is RuleAnswer.TEMPLATE
        assert rule.template_id == destination.template_id

    def test_a_row_no_template_generated_is_named_by_its_own_NAME(
        self, app, db, seed_user,
    ):
        """Those rows are exactly what a new-envelope answer creates.

        Measured 2026-08-30 on the developer's own account: 223 of his 256
        offerable destinations carry a template and 33 do not, and every one
        of the 33 was minted by a new-envelope answer.
        """
        envelope = a_transaction(
            seed_user, name="Amazon", amount="0.00", is_envelope=True,
            template=False,
        )
        db.session.commit()
        scope = a_scope(seed_user)
        destination = next(
            one for one in scope.destinations
            if one.transaction_id == envelope.id
        )
        assert destination.template_id is None

        rule = rule_naming(7, destination)

        assert rule.answer is RuleAnswer.NEW_ENVELOPE
        assert rule.envelope_name == destination.name
        assert rule.category_id == destination.category_id

    def test_the_rule_RESOLVES_BACK_to_the_row_it_was_read_from(
        self, app, db, seed_user,
    ):
        """The round trip, which is what makes the mapping exact.

        A rule that named a DIFFERENT row than the purchase it was stated
        beside would file the next statement's spending somewhere the owner
        never chose.  This states the rule from a row and resolves it again
        through the very producer the screen reads.
        """
        envelope = a_transaction(
            seed_user, name="Amazon", amount="0.00", is_envelope=True,
            template=False,
        )
        a_merchant(seed_user, "Amazon")
        db.session.commit()
        merchant_id = the_merchant_id(seed_user, "Amazon")
        scope = a_scope(seed_user)
        destination = next(
            one for one in scope.destinations
            if one.transaction_id == envelope.id
        )

        state_rules(
            (rule_naming(merchant_id, destination),),
            seed_user["user"].id, seed_user["account"].id,
        )
        db.session.commit()

        view = RuleView.build(
            seed_user["user"].id, seed_user["account"].id,
        )
        placement = placements_for(
            merchant_id, view, list(scope.destinations),
        )

        assert placement.records_in
        assert placement.destination.transaction_id == envelope.id

    def test_the_new_envelope_arm_states_what_the_owner_TYPED(self):
        """The other half, for the arm where no row exists yet."""
        # pylint: disable-next=shekel-private-module-import
        from app.services.statement_match._creations import NewEnvelope

        rule = rule_creating(7, NewEnvelope(name="Lowe's", category_id=3))

        assert rule.answer is RuleAnswer.NEW_ENVELOPE
        assert rule.envelope_name == "Lowe's"
        assert rule.category_id == 3
