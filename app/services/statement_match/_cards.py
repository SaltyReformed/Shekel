"""One CARD per bank line, and the sections the inbox groups them into.

Plan step ``bank_import:X-gj-1a``; rulings **bank_import:R-HP**, **R-HQ**,
**R-HR**, **R-HS**, **R-HW** and **R-HX**.  :mod:`._reconcile` assembles the
PAGE; this is the card it is made of, and the two are separate modules because
they are separate subjects -- and because one file holding both passed the
1,000-line ceiling that already split :mod:`._accepted_view` out of
:mod:`._reads`.

**The same card renders on all five tabs**, which is the whole design: the
inbox, the two holding tabs and the two settled tabs are one list seen five
ways, not five screens.  Two kinds exist because a line the books have not
settled and an act already applied carry disjoint facts -- a
:class:`LineCard` has a bank line and four verb offers, an :class:`ActCard`
has an Undo and what it would destroy -- and one value holding empty versions
of the other's fields is a control one Jinja condition away from rendering,
which is what :class:`~._bars.ParkedLine` exists to refuse.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
frozen dataclasses out, no Flask import, no clock read, no query.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ._sentence import Span, choose, for_accepted, for_parked_never
from ._sentence import for_parked_transfer, for_placement, for_proposal
from ._verbs import ADD_SHUT_BY_A_PROPOSAL, Verb, VerbOffer, offers_for

if TYPE_CHECKING:  # pragma: no cover -- annotations only
    from ._accepted_view import AcceptedGroup
    from ._bars import ParkedLine
    from ._creations import PurchaseDestination
    from ._leftovers import CreatableLine, RecordableInflow
    from ._offers import BankLine, MatchProposal
    from ._placement import Placement
    from ._reads import IncomeAlreadyRecorded, ReviewSet


class Section(enum.Enum):
    """What suggested a To-explain card's verb, which is how the tab groups.

    Ruling **bank_import:R-HP** replaced ruling **R-HB**'s visible EVIDENCE
    grouping with this one and returned the evidence partition to the service.
    The reader's question on every line is the same -- *what is this?* -- and
    what they need to know first is how much the app is claiming: a standing
    rule the owner stated, a tier's guess, or nothing at all.

    **The order of the members is the order the sections render**, most
    confident first, because that is the order the work is quickest in.
    """

    BY_RULE = "by_rule"
    PROPOSED = "proposed"
    NOTHING = "nothing"

    @property
    def heading(self) -> str:
        """Return the thin rule this section is printed under.

        Returns:
            The heading.  It is a NAME and a count and nothing else: ruling
            **R-HR** moved the paragraph that used to sit under each heading
            into the opened card, because the same two sentences were printed
            sixteen times on one screen.
        """
        return _SECTION_HEADINGS[self]


#: Each section's heading.  Three words or so each, deliberately: **R-HR**
#: retired the explanatory paragraph these used to carry.
_SECTION_HEADINGS: "dict[Section, str]" = {
    Section.BY_RULE: "Suggested by your rules",
    Section.PROPOSED: "Proposed",
    Section.NOTHING: "Nothing suggested",
}

@dataclass(frozen=True)
class LineCard:  # pylint: disable=too-many-instance-attributes
    """One bank line the books have not settled, as one card.

    Pylint: ``too-many-instance-attributes`` (12/7) -- **twelve because the
    card and the panel behind it read twelve disjoint facts about one line**,
    and the alternative is what :class:`~._bars.ParkedLine` and
    :class:`~._leftovers.RecordableInflow` both exist to refuse: one value
    carrying empty versions of another mechanism's fields, one Jinja condition
    away from rendering a control that cannot succeed.  Splitting it by tab
    would be worse still, because the five tabs render the SAME card and that
    sameness is the whole design (**R-HR**).

    Attributes:
        line: The bank's own record of the movement, which is the top of every
            card: the cleaned merchant, the posted day, the raw description.
        section: Which of the three To-explain sections this sits in
            (:class:`Section`), or ``None`` on a holding tab, which has one
            unnamed section.
        suggested: The verb the sentence proposes (:class:`~._verbs.Verb`), or
            ``None`` where nothing justified one -- in which case the card
            reads *Choose what this is* and opens its panel instead of
            offering a one-click OK (**R-HS**, **R-HX**).
        sentence: The ONE sentence the card carries, as spans
            (:mod:`._sentence`).  **The whole of what the card says**: ruling
            **R-HR** put every reason one click away.
        offers: All four verbs and whether each has a door
            (:func:`~._verbs.offers_for`, ruling **R-HW**).  A shut verb
            renders its explanation and what it waits for, never a control.
        notes: Every sentence this pass owes the reader about this line -- a
            rule it withheld, a tier's refusal, a bar -- in reading order.
            **Rendered in the opened panel and NOT on the card** (**R-HR**):
            printing them beside the line is the grain that put two sentences
            on the review screen sixteen times.
        income_already_held: What the books already record as unexplained
            income for this line's own pay period
            (:class:`~._reads.IncomeAlreadyRecorded`), or ``None``.  **The one
            money-at-risk signal this build has**, and the only thing on a card
            that may be drawn in amber: recording a deposit whose period
            already holds income is how a paycheck gets counted twice.
        sweep_class: Which of :data:`SWEEP_LABELS` a one-click sweep would
            reach this card under, or ``None`` where no sweep may.
        destinations: The budget lines this could become a purchase against,
            for the panel's ADD tab.  Empty is a real answer -- a period whose
            every envelope has closed at a fixed figure offers none, and a NEW
            envelope is then the only arm.
        placement: What a standing rule comes to for this line
            (:class:`~._placement.Placement`), or ``None``.
        proposal: The match a tier offers (:class:`~._offers.MatchProposal`),
            or ``None``.  **At most one of this and** :attr:`placement` **is
            set**, by construction rather than by care: ``creatable`` is a
            subset of ``unmatched``, which :func:`~._reads._unexplained` has
            already taken every proposal's line out of.
        answer_door: The sentence naming where a standing answer that parks
            this line is changed, or ``None`` where changing it would change
            nothing (:attr:`~._bars.ParkedLine.answer_door`).
    """

    line: "BankLine"
    section: "Section | None"
    suggested: "Verb | None"
    sentence: "tuple[Span, ...]"
    offers: "tuple[VerbOffer, ...]"
    notes: "tuple[str, ...]"
    income_already_held: "IncomeAlreadyRecorded | None"
    risk_class: "str | None"
    destinations: "tuple[PurchaseDestination, ...]"
    placement: "Placement | None"
    proposal: "MatchProposal | None"
    answer_door: "str | None"

    def offer_for(self, verb: Verb) -> VerbOffer:
        """Return this line's offer for one verb.

        Args:
            verb: Which of the four.

        Returns:
            Its :class:`~._verbs.VerbOffer`.  Total, because
            :func:`~._verbs.offers_for` emits all four for every line
            (**R-HW**) -- so this cannot answer ``None`` and no caller needs
            an absence arm.

        Raises:
            KeyError: Never in practice, and deliberately not defended
                against: a card missing a verb would mean
                :func:`~._verbs.offers_for` had stopped being total, which is
                a defect to see rather than to absorb.
        """
        return next(offer for offer in self.offers if offer.verb is verb)

    @property
    def offers_ok(self) -> bool:
        """Return whether this card offers the one-click OK.

        **It asks the DOOR and not the sentence**, which is the difference
        between a working button and the shape ruling **R-HW** forbids: a
        Transfers card's sentence opens on TRANSFER, a verb with no door, so
        a test on :attr:`suggested` alone would render OK on every parked card
        payment and every line a standing *never a purchase* answer disposes
        of -- 9 of the developer's 27 unexplained lines, measured 2026-08-29.

        Returns:
            ``True`` exactly when a verb was justified AND that verb has a
            door.  A card with neither shows *Choose*, which opens the panel,
            so the two controls are exclusive and a template asks one
            question.
        """
        if self.suggested is None:
            return False
        return self.offer_for(self.suggested).is_open

    @property
    def sweep_class(self) -> "str | None":
        """Return which sweep may reach this card, or ``None`` where none may.

        **ONE statement of the guard, on the value.**  Ruling **R-FZ(c)**
        sweeps per risk class, and the developer's ruling of 2026-08-28 added
        the second half: a bulk click may not reach a line whose own card says
        the books may already hold it, or that the pass could not finish
        checking.  :func:`~._queue._sweeps_for` kept that by giving sweeps to
        one evidence group only -- a coupling that broke the moment the
        grouping changed -- so it is stated here as the predicate it always
        was.

        Returns:
            :attr:`risk_class` when this card carries a working verb, no
            withheld sentence and no money at risk; ``None`` otherwise.
        """
        if not self.offers_ok:
            return None
        if self.notes or self.income_already_held is not None:
            return None
        return self.risk_class


@dataclass(frozen=True)
class ActCard:
    """One act already applied, as the same card one tense over.

    Attributes:
        act: The :class:`~._accepted_view.AcceptedGroup` -- what the bank
            showed, what it named, whether it still holds, and what an Undo
            would destroy.
        sentence: The past-tense sentence (:func:`~._sentence.for_accepted`).

    **Two fields and not a flattened copy**, because everything else the
    settled card renders is already on the act and a second spelling of it is
    how the Undo control comes to promise something the door will not do --
    which is the argument :attr:`~._accepted_view.AcceptedGroup.removes`
    already makes for reading the release door's own derivation.
    """

    act: "AcceptedGroup"
    sentence: "tuple[Span, ...]"


@dataclass(frozen=True)
class CardSection:
    """One thin rule, and the cards under it.

    Attributes:
        section: Which of the three To-explain sections
            (:class:`Section`), or ``None`` for a tab that has one unnamed
            section.
        cards: The cards, all of one kind: :class:`LineCard` on To explain,
            Transfers and Skipped; :class:`ActCard` on Explained and Filed by
            rules.  Which kind is the TAB's fact, so the two are rendered by
            two partials and neither ever meets the other's type.
        withheld: How many cards a BOUND left out -- ``0`` where none did.
            **No default**, which is the discipline
            :attr:`~._accepted_view.AcceptedGroup.applied_by_rule` keeps for
            its own reason: ``0`` is the value that reads as safe, and it
            claims *the whole record is here*.  **A truncated list that does not say it
            is truncated is a page claiming to be the whole record**, which is
            :class:`~._accepted_view.AcceptedRegister`'s own argument for
            travelling with the same count; dropping it on the way to the
            screen would have reinstated exactly what that value exists to
            prevent.

    **An empty section is ABSENT rather than rendered empty**, which is the
    rule :class:`~._queue.StatementQueue` already keeps: a heading over no
    rows reads as work the owner has somewhere to do.
    """

    section: "Section | None"
    cards: "tuple[LineCard, ...] | tuple[ActCard, ...]"
    withheld: int

    @property
    def count(self) -> int:
        """Return how many cards this section holds.

        Returns:
            The count, which the heading prints beside its name.
        """
        return len(self.cards)

def _offers(
    review: "ReviewSet", add_waits: "str | None",
    proposal: "MatchProposal | None",
) -> "tuple[VerbOffer, ...]":
    """Return all four verb offers for one line.

    **The one place the pass-level MATCH fact is applied.**  A proposal names
    its own rows, and those rows are exactly the ones ``unmatched_rows``
    withholds (:func:`~._reads._rows_the_bank_never_showed`) -- so a pass whose
    every row a proposal claims would report an empty pool and shut MATCH on
    the very lines it had just matched.

    Args:
        review: The pass, which owns the pool of unexplained app rows.
        add_waits: Why ADD is shut for this line, or ``None``.
        proposal: The match a tier offers for this line, or ``None``.

    Returns:
        The four :class:`~._verbs.VerbOffer` values.
    """
    return offers_for(
        add_waits=add_waits,
        has_rows_to_match=proposal is not None or bool(review.unmatched_rows),
    )


def _proposal_card(review: "ReviewSet", proposal: "MatchProposal") -> LineCard:
    """Return the card for a match a TIER proposes.

    Args:
        review: The pass.
        proposal: The proposal, which names exactly one line -- every tier
            constructs ``lines=(line,)``.

    Returns:
        The :class:`LineCard`.
    """
    return LineCard(
        line=proposal.lines[0],
        section=Section.PROPOSED,
        suggested=Verb.MATCH,
        sentence=for_proposal(proposal),
        offers=_offers(review, ADD_SHUT_BY_A_PROPOSAL, proposal),
        # **A proposal is a CONCLUSION**, so this pass has no unfinished
        # search to report about its line: the gap sentence answers *why can
        # the app not say nothing explains this*, which a proposal answers.
        notes=(),
        income_already_held=None,
        risk_class=proposal.review_class,
        destinations=(),
        placement=None,
        proposal=proposal,
        answer_door=None,
    )


def _creatable_card(
    review: "ReviewSet", creatable: "CreatableLine",
) -> LineCard:
    """Return the card for an outflow the create door would accept.

    **A rule that does not REACH this line suggests nothing**, so an
    UNRESOLVED placement lands in *Nothing suggested* with its reason in the
    panel rather than in *Suggested by your rules* under a sentence naming a
    destination the rule never gave.  Substituting one is how a suggestion
    becomes a guess (:mod:`._placement`).

    Args:
        review: The pass.
        creatable: The :class:`~._leftovers.CreatableLine`.

    Returns:
        The :class:`LineCard`.
    """
    placement = creatable.placement
    names_a_home = placement is not None and (
        placement.records_in or placement.creates
    )
    return LineCard(
        line=creatable.line,
        section=Section.BY_RULE if names_a_home else Section.NOTHING,
        suggested=Verb.ADD if names_a_home else None,
        sentence=for_placement(placement) if names_a_home else choose(),
        offers=_offers(review, creatable.withheld, None),
        # **The gap is not asked for**: :func:`~._verdict.ruled` has already
        # folded it into ``warning``, which is the wider sentence -- a rule
        # this pass withheld, OR a search it did not finish -- so asking again
        # would print the same words twice.
        notes=() if creatable.warning is None else (creatable.warning,),
        income_already_held=None,
        risk_class=placement.sweep_class if names_a_home else None,
        destinations=creatable.destinations,
        placement=placement,
        proposal=None,
        answer_door=None,
    )


def _inflow_card(review: "ReviewSet", inflow: "RecordableInflow") -> LineCard:
    """Return the card for money coming IN that no row explains.

    Ruling **bank_import:R-HX**: nothing is pre-filled.  The only inflow door
    that exists records uncategorized INCOME, and being the only act is not a
    justification -- a merchant credit is a refund, and calling it income is
    the wrong act ``X-gj-2`` exists to correct.

    Args:
        review: The pass, which owns both the search gap and the
            books-already-hold-income signal.
        inflow: The :class:`~._leftovers.RecordableInflow`.

    Returns:
        The :class:`LineCard`.
    """
    gap = review.search_gap_for(inflow.line)
    return LineCard(
        line=inflow.line,
        section=Section.NOTHING,
        suggested=None,
        sentence=choose(),
        offers=_offers(review, inflow.withheld, None),
        notes=tuple(
            said for said in (inflow.withheld, gap) if said is not None
        ),
        income_already_held=review.income_already_recorded_in(inflow.line),
        risk_class=None,
        destinations=(),
        placement=None,
        proposal=None,
        answer_door=None,
    )


def parked_card(review: "ReviewSet", parked: "ParkedLine") -> LineCard:
    """Return the holding card for an outflow that may not become spending.

    Ruling **bank_import:R-HQ**.  Which TAB it lands on is
    :mod:`._reconcile`'s; this builds the card either tab renders, and the
    sentence differs because the two states are different facts: money the
    bank says went to another account the owner holds, against spending the
    owner has said is never spending.

    Args:
        review: The pass.
        parked: The :class:`~._bars.ParkedLine`.

    Returns:
        The :class:`LineCard`.  Its ADD is shut by the bar, its TRANSFER and
        SKIP by having no door at all, so :attr:`LineCard.offers_ok` is False
        and no tab renders it a button.
    """
    gap = review.search_gap_for(parked.line)
    pays_an_account = parked.also_pays_an_account
    return LineCard(
        line=parked.line,
        section=None,
        suggested=Verb.TRANSFER if pays_an_account else Verb.SKIP,
        sentence=(
            for_parked_transfer(parked) if pays_an_account
            else for_parked_never(parked)
        ),
        offers=_offers(review, parked.reason, None),
        notes=tuple(
            said for said in (parked.reason, gap) if said is not None
        ),
        income_already_held=None,
        risk_class=None,
        destinations=(),
        placement=None,
        proposal=None,
        answer_door=parked.answer_door,
    )


def to_explain_sections(review: "ReviewSet") -> "tuple[CardSection, ...]":
    """Return the inbox, grouped by what suggested each card's verb.

    Ruling **bank_import:R-HP**.  **The three source lists are DISJOINT and
    that is what lets them be concatenated**: ``creatable`` and
    ``recordable_inflows`` are both subsets of ``unmatched``, which
    :func:`~._reads._unexplained` has already taken every proposal's line out
    of, and ``parked`` is held out of both by ruling **R-GJ**'s bar -- so no
    line can appear on two cards.

    **``parked`` is absent** (**R-HQ**): a line with no available act is a
    holding state on its own tab, never inbox work.

    Args:
        review: The pass.

    Returns:
        One :class:`CardSection` per :class:`Section` that has a card, in the
        enum's order.  An empty section is ABSENT rather than rendered empty.
    """
    cards = (
        [_proposal_card(review, one) for one in review.proposals]
        + [_creatable_card(review, one) for one in review.creatable]
        + [_inflow_card(review, one) for one in review.recordable_inflows]
    )
    sections = []
    for section in Section:
        mine = tuple(card for card in cards if card.section is section)
        if mine:
            sections.append(
                CardSection(section=section, cards=mine, withheld=0),
            )
    return tuple(sections)


def act_sections(register) -> "tuple[CardSection, ...]":
    """Return acts already applied, as one unnamed section of cards.

    Args:
        register: The :class:`~._accepted_view.AcceptedRegister` for ONE half
            of the accepted set -- narrowed before its bound, so its
            ``withheld_count`` is this tab's own truncation and not the whole
            account's.

    Returns:
        One :class:`CardSection`, or nothing at all when there are no acts.
        **The bound travels with the cards**, so the tab can say how many it
        did not render.
    """
    if not register.shown:
        return ()
    return (
        CardSection(
            section=None,
            cards=tuple(
                ActCard(act=act, sentence=for_accepted(act))
                for act in register.shown
            ),
            withheld=register.withheld_count,
        ),
    )
