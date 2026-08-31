"""The four verbs, and the ONE sentence a Reconcile card carries.

Plan step ``bank_import:X-gj-1a``; rulings **bank_import:R-HP**, **R-HR**,
**R-HW** and **R-HX**.

**No database.**  Both modules under test are pure -- plain values in, frozen
values out -- so every case here builds its own inputs and the whole module
runs without a clone.  That is deliberate rather than convenient: a case that
staged a world to assert on a string would be grading the fixture as much as
the function, and this project pays about 0.076 s per item for a clone it does
not need.
"""

from datetime import date
from decimal import Decimal

import pytest

# Pylint: ``shekel-private-module-import`` -- a test of a service's
# INTERNALS reaches for them by name, which is the convention this
# package's own test modules already keep (``test_bars``,
# ``test_candidates``, ``test_residual``).  The alternative is widening
# the package's public surface for the tests alone, which is the
# "surface nobody asked for" its ``__init__`` refuses in as many words.
# pylint: disable=shekel-private-module-import
from app.services.statement_match._cards import (
    LineCard,
    Section,
)
from app.services.statement_match._panel import (
    AddAct,
    AddTab,
    VerbPanel,
)
from app.services.statement_match._creations import (
    NewEnvelope,
    PurchaseDestination,
)
from app.services.statement_match._offers import (
    BankLine,
    CandidateRow,
    MatchProposal,
    RowKind,
)
from app.services.statement_match._placement import (
    Placement,
    PlacementKind,
)
from app.services.statement_match._sentence import (
    NAMED_ROW_LIMIT,
    Ink,
    Span,
    choose,
    for_placement,
    for_proposal,
)
from app.services.statement_match._verbs import (
    MATCH_SHUT_NO_ROWS,
    Verb,
    VerbOffer,
    offers_for,
)

#: The marks a span may not OPEN with, because the template joins spans with a
#: single space and these must hug the word before them.  An opening bracket is
#: absent on purpose: ``Lowe's (2026-08-13 - 2026-08-26)`` is correct.
_HUGGING_MARKS = ",:;."


def _a_row(label="Data Manager", amount="-100.00", row_id=1):
    """Return one candidate row for a proposal to name.

    Args:
        label: What the screen calls it.
        amount: Its signed cash effect.
        row_id: Its id.

    Returns:
        The :class:`~app.services.statement_match.CandidateRow`.
    """
    return CandidateRow(
        kind=RowKind.TRANSACTION, row_id=row_id, label=label,
        cash_amount=Decimal(amount), settled_on=date(2026, 8, 21),
        is_settled=True, states_own_figure=False, version_id=1,
    )


def _a_line(amount="-100.00"):
    """Return one bank line.

    Args:
        amount: Its signed figure.

    Returns:
        The :class:`~app.services.statement_match.BankLine`.
    """
    return BankLine(
        line_id=7, posted_on=date(2026, 8, 21), amount=Decimal(amount),
        description="POINT OF SALE DEBIT", merchant="Lowe's",
    )


def _a_destination(name="Lowe's", is_settled=False):
    """Return one budget line a purchase could be filed into.

    Args:
        name: The row's own name, which is what a rule matches on.
        is_settled: Whether it has already closed.

    Returns:
        The :class:`~app.services.statement_match.PurchaseDestination`.
    """
    return PurchaseDestination(
        transaction_id=11, name=name, category_id=3,
        period_start=date(2026, 8, 13), period_end=date(2026, 8, 26),
        pay_period_id=5, is_settled=is_settled,
    )


class TestEveryLineIsOfferedAllFourVerbs:
    """Ruling **R-HW**: the panel teaches the vocabulary, so none is absent."""

    def test_all_four_appear_in_the_enum_s_own_order(self):
        """The tab order is the enum's, whatever this build can act on."""
        offers = offers_for(add_waits=None, has_rows_to_match=True)
        assert tuple(offer.verb for offer in offers) == tuple(Verb)

    def test_transfer_and_skip_are_shut_and_say_what_they_wait_for(self):
        """Neither has a door in the app, and neither pretends otherwise."""
        offers = {
            offer.verb: offer
            for offer in offers_for(add_waits=None, has_rows_to_match=True)
        }
        for verb in (Verb.TRANSFER, Verb.SKIP):
            assert not offers[verb].is_open
            assert offers[verb].waiting_for

    def test_add_carries_exactly_the_refusal_the_caller_stated(self):
        """The mechanism's own value is what says whether ADD is open."""
        barred = "You have said Capital One is never a purchase."
        shut = {
            offer.verb: offer
            for offer in offers_for(add_waits=barred, has_rows_to_match=True)
        }[Verb.ADD]
        assert shut.waiting_for == barred
        assert not shut.is_open

    def test_match_shuts_when_the_pass_offers_no_row_to_match_against(self):
        """An account whose every row is explained has nothing left to pair."""
        offers = {
            offer.verb: offer
            for offer in offers_for(add_waits=None, has_rows_to_match=False)
        }
        assert offers[Verb.MATCH].waiting_for == MATCH_SHUT_NO_ROWS

    def test_openness_and_its_reason_are_one_field(self):
        """A verb cannot be open and carry a refusal at the same time."""
        assert VerbOffer(verb=Verb.ADD, waiting_for=None).is_open
        assert not VerbOffer(verb=Verb.ADD, waiting_for="no").is_open


class TestTheSentenceOpensOnTheVerb:
    """Ruling **R-HR**: one sentence, and its first word is the decision."""

    def test_a_proposal_opens_on_match(self):
        """A tier's suggestion states the verb before anything else."""
        spans = for_proposal(MatchProposal(
            lines=(_a_line(),), rows=(_a_row(),), day_gap=0,
        ))
        assert spans[0].text == Verb.MATCH.word
        assert spans[0].ink is Ink.VERB

    def test_a_placed_line_opens_on_add(self):
        """A rule's destination is an ADD, stated as such."""
        spans = for_placement(Placement(
            merchant="Lowe's", kind=PlacementKind.RECORD_IN,
            destination=_a_destination(),
        ))
        assert spans[0].text == Verb.ADD.word

    def test_a_line_with_no_suggestion_opens_on_choose_in_the_accent(self):
        """Ruling **R-HX**: the app asks rather than proposing a guess."""
        spans = choose()
        assert spans[0].text == "Choose"
        assert spans[0].ink is Ink.CHOOSE
        assert not any(span.text == Verb.ADD.word for span in spans)


class TestASpanCarriesWordsOrAFigureAndNeverBoth:
    """The invariant that lets a template ask one question per span."""

    def test_words_and_figure_are_exclusive_by_construction(self):
        """The two constructors are the only way one is made."""
        words = Span.words("off by", Ink.MUTED)
        figure = Span.figure(Decimal("0.05"), Ink.STRONG)
        assert words.money is None and words.text == "off by"
        assert figure.text is None and figure.money == Decimal("0.05")

    def test_money_is_never_formatted_into_the_sentence(self):
        """A service that formatted `$0.05` would be a second formatter."""
        spans = for_proposal(MatchProposal(
            lines=(_a_line("-100.05"),), rows=(_a_row(),), day_gap=0,
        ))
        assert any(span.money == Decimal("-0.05") for span in spans)
        assert not any("$" in (span.text or "") for span in spans)

    @pytest.mark.parametrize("spans", [
        choose(),
        for_proposal(MatchProposal(
            lines=(BankLine(
                line_id=7, posted_on=date(2026, 8, 21),
                amount=Decimal("-100.05"), description="D",
            ),),
            rows=(CandidateRow(
                kind=RowKind.TRANSACTION, row_id=1, label="Data Manager",
                cash_amount=Decimal("-100.00"),
                settled_on=date(2026, 8, 21), is_settled=True,
                states_own_figure=False, version_id=1,
            ),),
            day_gap=0,
        )),
        for_placement(Placement(
            merchant="Lowe's", kind=PlacementKind.RECORD_IN,
            destination=PurchaseDestination(
                transaction_id=11, name="Lowe's", category_id=3,
                period_start=date(2026, 8, 13), period_end=date(2026, 8, 26),
                pay_period_id=5, is_settled=False,
            ),
        )),
    ])
    def test_no_span_opens_with_a_mark_that_must_hug_the_word_before(
        self, spans,
    ):
        """The template joins with one space, so ``, 2026-08-13`` renders wrong.

        Measured on the developer's own data before this contract existed: the
        card read ``Add to Lowe's , 2026-08-13 - 2026-08-26``.
        """
        for span in spans:
            if span.text:
                assert span.text[0] not in _HUGGING_MARKS, span.text


class TestAGroupNamesItsRowsAndNeverHidesTheRest:
    """A cap that does not say it is a cap reads as the whole list."""

    def test_one_row_is_named_outright(self):
        """No count is printed where there is nothing to count."""
        spans = for_proposal(MatchProposal(
            lines=(_a_line(),), rows=(_a_row("Duke Energy"),), day_gap=0,
        ))
        assert any(span.text == "Duke Energy" for span in spans)
        assert not any("rows" in (span.text or "") for span in spans)

    def test_a_group_states_how_many_and_names_them(self):
        """Ruling **R-GD**'s group, as the reader needs to check it."""
        rows = tuple(
            _a_row(label=name, row_id=index)
            for index, name in enumerate(
                ("Data Manager", "Health Insurance Allowance"),
            )
        )
        spans = for_proposal(MatchProposal(
            lines=(_a_line("-200.00"),), rows=rows, day_gap=0,
        ))
        said = " ".join(span.text or "" for span in spans)
        assert "2 rows" in said
        assert "Data Manager + Health Insurance Allowance" in said

    def test_the_rows_it_could_not_name_are_COUNTED_and_not_dropped(self):
        """Past the limit the sentence says how many it did not name."""
        rows = tuple(
            _a_row(label=f"Row {index}", row_id=index)
            for index in range(NAMED_ROW_LIMIT + 2)
        )
        spans = for_proposal(MatchProposal(
            lines=(_a_line(),), rows=rows, day_gap=0,
        ))
        said = " ".join(span.text or "" for span in spans)
        assert f"{NAMED_ROW_LIMIT + 2} rows" in said
        assert "and 2 more" in said


class TestTheDifferenceIsNamedOnlyWhereMoneyMoves:
    """Ruling **R-GD(a)**: a near miss re-prices, and the card says so."""

    def test_a_balanced_proposal_states_no_difference(self):
        """Nothing is printed where there is nothing to accept."""
        spans = for_proposal(MatchProposal(
            lines=(_a_line("-100.00"),), rows=(_a_row("R", "-100.00"),),
            day_gap=0,
        ))
        assert all(span.money is None for span in spans)

    def test_a_repricing_proposal_names_what_it_would_change(self):
        """The figure the owner is accepting, as a figure and not a string."""
        spans = for_proposal(MatchProposal(
            lines=(_a_line("-100.05"),), rows=(_a_row("R", "-100.00"),),
            day_gap=0,
        ))
        figures = [span.money for span in spans if span.money is not None]
        assert figures == [Decimal("-0.05")]


class TestARuleThatNamesNoHomeGetsNoSentence:
    """Substituting a destination is how a suggestion becomes a guess."""

    def test_an_unresolved_placement_is_REFUSED_rather_than_guessed_at(self):
        """:mod:`._placement` reports every way a rule fails to reach a line."""
        with pytest.raises(ValueError) as refused:
            for_placement(Placement(
                merchant="Lowe's", kind=PlacementKind.UNRESOLVED,
                unresolved_reason="That template has no row in this period.",
            ))
        assert "no row in this period" in str(refused.value)

    def test_a_new_envelope_says_it_is_new_and_says_when_it_JOINS_one(self):
        """Finding **N-327**: the card says so BEFORE the press, not after."""
        placement = Placement(
            merchant="Lowe's", kind=PlacementKind.CREATE_NEW,
            new_envelope=NewEnvelope(name="Home Improvement", category_id=3),
            joins_new=True,
        )
        said = " ".join(span.text or "" for span in for_placement(placement))
        assert "a new envelope" in said
        assert "Home Improvement" in said
        assert "joining the one this pass creates" in said


class TestTheOKControlFollowsTheDOORAndNotTheSentence:
    """Ruling **R-HW**, and the defect it would otherwise ship."""

    def _card(self, suggested, offers):
        """Return a card carrying *suggested* and *offers* and nothing else.

        Args:
            suggested: The verb the sentence opens on, or ``None``.
            offers: The four verb offers.

        Returns:
            The :class:`~app.services.statement_match._cards.LineCard`.
        """
        return LineCard(
            line=_a_line(), section=Section.NOTHING, suggested=suggested,
            sentence=choose(), income_already_held=None, risk_class=None,
            panel=VerbPanel(
                offers=offers, notes=(), answer_door=None, add=None,
                proposal=None,
            ),
        )

    def test_a_card_whose_verb_has_NO_door_offers_no_OK(self):
        """A Transfers card's sentence opens on a verb nothing can perform.

        Measured 2026-08-29: 9 of the developer's 27 unexplained lines are
        parked card payments, so a test on ``suggested`` alone would have put
        a working-looking OK on every one of them.
        """
        card = self._card(
            Verb.TRANSFER, offers_for(add_waits="barred", has_rows_to_match=True),
        )
        assert not card.offers_ok

    def test_a_card_whose_verb_HAS_a_door_offers_it(self):
        """The ordinary case still works."""
        card = self._card(
            Verb.ADD, offers_for(add_waits=None, has_rows_to_match=True),
        )
        assert card.offers_ok

    def test_a_card_with_no_suggestion_offers_none(self):
        """*Choose* opens the panel; the two controls are exclusive."""
        card = self._card(
            None, offers_for(add_waits=None, has_rows_to_match=True),
        )
        assert not card.offers_ok


class TestNoSweptCardCarriesASentenceOrMoneyAtRisk:
    """The developer's ruling of 2026-08-28, restated as a predicate.

    :func:`~._queue._sweeps_for` kept it by giving sweeps to one evidence
    group only -- a coupling to a grouping ruling **R-HP** has since replaced.
    Stated on the value, it survives the regrouping.
    """

    def _card(self, *, notes=(), income_already_held=None, risk="into_open"):
        """Return a card that would otherwise be sweepable.

        Args:
            notes: What the pass owes the reader about this line.
            income_already_held: The money-at-risk signal, or ``None``.
            risk: The raw sweep partition value.

        Returns:
            The :class:`~app.services.statement_match._cards.LineCard`.
        """
        return LineCard(
            line=_a_line(), section=Section.BY_RULE, suggested=Verb.ADD,
            sentence=choose(), income_already_held=income_already_held,
            risk_class=risk,
            panel=VerbPanel(
                offers=offers_for(add_waits=None, has_rows_to_match=True),
                notes=notes, answer_door=None,
                add=AddTab(
                    act=AddAct.PURCHASE,
                    destinations=(_a_destination(),), placement=None,
                ),
                proposal=None,
            ),
        )

    def test_a_clean_card_is_swept_by_its_own_risk_class(self):
        """Ruling **R-FZ(c)**: one control per class, never a tick-all."""
        assert self._card().sweep_class == "into_open"

    def test_a_card_carrying_a_WITHHELD_sentence_is_not_swept(self):
        """A bulk click may not reach a line the pass would not file."""
        card = self._card(notes=("A rule named a row this statement explains.",))
        assert card.sweep_class is None

    def test_a_card_whose_books_ALREADY_hold_income_is_not_swept(self):
        """The one money-at-risk signal this build has."""
        held = object()
        assert self._card(income_already_held=held).sweep_class is None

    def test_a_card_whose_verb_has_no_door_is_not_swept(self):
        """Nothing can be swept into a door that does not exist."""
        card = LineCard(
            line=_a_line(), section=None, suggested=Verb.TRANSFER,
            sentence=choose(), income_already_held=None,
            risk_class="into_open",
            panel=VerbPanel(
                offers=offers_for(
                    add_waits="barred", has_rows_to_match=True,
                ),
                notes=(), answer_door=None, add=None,
                proposal=None,
            ),
        )
        assert card.sweep_class is None
