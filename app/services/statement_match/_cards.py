"""One CARD per bank line, and the sections the inbox groups them into.

Plan steps ``bank_import:X-gj-1a`` and ``X-gj-1b``; rulings
**bank_import:R-HP**, **R-HQ**, **R-HR**, **R-HS**, **R-HW** and **R-HX**.
:mod:`._reconcile` assembles the PAGE; this is the card it is made of, and the
two are separate modules because they are separate subjects -- and because one
file holding both passed the 1,000-line ceiling that already split
:mod:`._accepted_view` out of :mod:`._reads`.

**The same card renders on all five tabs**, which is the whole design: the
inbox, the two holding tabs and the two settled tabs are one list seen five
ways, not five screens.  Two kinds exist because a line the books have not
settled and an act already applied carry disjoint facts -- a
:class:`LineCard` has a bank line and a :class:`~._panel.VerbPanel`, an
:class:`ActCard` has an Undo and what it would destroy -- and one value
holding empty versions of the other's fields is a control one Jinja condition
away from rendering, which is what :class:`~._bars.ParkedLine` exists to
refuse.

**What the card SHOWS and what the panel DISCLOSES are two values** (ruling
**R-HR**, plan step ``X-gj-1b``).  :class:`LineCard` carried twelve attributes
under a ``too-many-instance-attributes`` disable while it held both, and the
page needed two more -- which act ADD would perform, and the rows MATCH
offers.  :mod:`._panel` holds the disclosure now, so the disable is DELETED
rather than raised: the boundary ruling R-HR draws is stated in the type
system, and a template cannot print a panel fact beside the sentence because
it is not there to print.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
frozen dataclasses out, no Flask import, no clock read, no query.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ._panel import AddAct, AddTab, VerbPanel
from ._rules import is_inflow
from ._sentence import NAMED_ROW_LIMIT, Span, choose, for_accepted
from ._sentence import for_income_placement
from ._sentence import for_parked_never
from ._sentence import for_parked_transfer, for_placement, for_proposal
from ._verbs import ADD_SHUT_BY_A_PROPOSAL, Verb, VerbOffer, offers_for

if TYPE_CHECKING:  # pragma: no cover -- annotations only
    from ._accepted_view import AcceptedGroup
    from ._bars import ParkedLine
    from ._leftovers import CreatableLine, RecordableInflow
    from ._offers import BankLine, MatchProposal
    from ._reads import ArrivalsAlreadyHeld, ReviewSet


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
class LineCard:
    """One bank line the books have not settled, as one card.

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
        arrivals_already_held: Every ARRIVING row this line's own pay period
            already holds that no bank line explains
            (:class:`~._reads.ArrivalsAlreadyHeld`), or ``None``.  **The one
            money-at-risk signal this build has**, and the only thing on a card
            that may be drawn in amber: recording a deposit whose period
            already holds the same money arriving is how a paycheck gets
            counted twice.  **ARRIVING and not income** -- a stored refund is
            one of them since ruling **bank_import:R-II** -- and the field said
            ``income`` until plan step ``bank_import:X-gj-2b-3``.
        risk_class: Which of :data:`~._reconcile.SWEEP_LABELS` this card's act
            falls under, or ``None`` where it falls under none.  It is what a
            sweep would reach IF the card were clean; :attr:`sweep_class` is
            the guarded answer and is what a control may read.
        panel: What the opened card discloses (:class:`~._panel.VerbPanel`) --
            all four verbs, every sentence this pass owes the reader, and what
            each verb's tab offers.
    """

    line: "BankLine"
    section: "Section | None"
    suggested: "Verb | None"
    sentence: "tuple[Span, ...]"
    arrivals_already_held: "ArrivalsAlreadyHeld | None"
    risk_class: "str | None"
    panel: VerbPanel

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
        return self.panel.offer_for(self.suggested).is_open

    @property
    def takes_ok(self) -> bool:
        """Return whether this card can be consented to at all.

        Plan step ``bank_import:X-gj-1b``.  **The question the CHECKBOX asks,
        and it is not the one :attr:`offers_ok` asks.**  ``offers_ok`` says
        whether the SUMMARY shows a one-click OK button -- a verb was
        justified and its door exists.  This says whether the card has any act
        to consent to at all, which is what decides whether the ``ok``
        checkbox is in the document.

        **They were one question until an adversarial review measured what
        that cost.**  The panel renders its primary button as a
        ``<label for="ok-N">`` inside every open verb pane, and the checkbox
        it points at was rendered only where ``offers_ok`` -- so a card with no
        suggestion rendered a button pointing at an element that was not in the
        document.  Measured 2026-08-30 on a restored production clone: **31 of
        248 cards**, and on those the primary act was dead in a browser -- an
        inflow could never be recorded, an unruled swipe could never become a
        purchase, a parked payment could never be group-matched -- with the
        whole suite green, because every acting test hand-appended an ``ok``
        value a browser could not have produced.

        Returns:
            Whether any verb's door would accept this line
            (:attr:`~._panel.VerbPanel.open_verbs`).  **That is exactly the
            set whose panes render a submitting control**: TRANSFER and SKIP
            have no door in this build so they are never open, an open MATCH
            always offers the row list, and an open ADD always carries an
            :class:`~._panel.AddTab` -- a card whose ADD has no act states its
            refusal through ``add_waits`` instead, which shuts the verb.
            Measured on the same 248 cards: this predicate and *a pane renders
            a control* agree on every one, with no disagreement.

            ``False`` is reachable and is the holding state ruling **R-HQ**
            names: a parked card payment on an account whose every row is
            already explained has no act, so it takes no OK and its panel is a
            disclosure.
        """
        return bool(self.panel.open_verbs)

    @property
    def opens_on(self) -> Verb:
        """Return which of the four tabs the opened panel opens on.

        **Total, and stated here rather than in Jinja**, because it decides
        which control a reader sees first on a screen that writes money.  Two
        arms, each with a reason:

        * the verb the sentence proposes, where one was justified.  The panel
          opens on the destination a standing rule names, which is ruling
          **R-HS**'s pre-fill read at the grain of the tab;
        * otherwise the verb this line's own mechanism can act on -- ADD where
          it has an act (a purchase for an outflow, an income row for a
          deposit) -- and failing that the first verb with a door at all.
          **The mechanism rather than the enum's order**, because ruling
          **R-HX** leaves 16 of the developer's own 18 inbox cards with no
          suggestion, and opening every one of them on MATCH would put the
          only act they have behind a tab.

        Returns:
            The :class:`~._verbs.Verb` whose tab opens.  A card with NO open
            verb -- a parked payment on an account with nothing left to pair
            -- opens on :attr:`suggested` or, failing that, the first verb,
            whose tab is then its explanation.  That is the holding state
            ruling **R-HQ** names, and it is a disclosure rather than a
            control (**R-HW**).
        """
        if self.suggested is not None:
            return self.suggested
        open_verbs = self.panel.open_verbs
        if self.panel.add is not None and Verb.ADD in open_verbs:
            return Verb.ADD
        return open_verbs[0] if open_verbs else Verb.MATCH

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
        if self.panel.notes or self.arrivals_already_held is not None:
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

    @property
    def rows_not_named(self) -> "tuple[str, ...]":
        """Return the member rows the SENTENCE could not fit.

        Plan step ``bank_import:X-gj-1c``.  **The settled tabs retire the
        register (R-HU), so they may not drop what it printed.**  That page
        listed every member row's label; this card carries a sentence, and
        :func:`~._sentence.for_accepted` names at most
        :data:`~._sentence.NAMED_ROW_LIMIT` of them before it says *and N
        more*.  The count was never silent, but the NAMES were -- so an owner
        deciding whether to undo could not see which rows the act claims.

        Measured on a restored production clone 2026-08-31, 220 acts on the
        developer's own account: **217 name one row, 2 name two, and 1 names
        four** -- so this is non-empty for exactly ONE of them, and the card
        stays one line for the other 219.  An earlier draft of this module's
        card claimed *0 of 221 acts have either to say*; that figure is
        :attr:`~._release.PlannedRemovals.rows`' (removals), and applying it
        to member rows was measured false by adversarial review.

        Returns:
            The labels beyond what the sentence names, in the act's own order;
            ``()`` when it named them all.  **It reads the same constant the
            sentence does**, so the two cannot disagree about where the cut
            falls -- which is the whole reason it is here and not a length
            comparison in Jinja.
        """
        labels = tuple(row.label for row in self.act.rows)
        if len(labels) <= 1:
            return ()
        return labels[NAMED_ROW_LIMIT:]


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


def _proposal_card(
    review: "ReviewSet", proposal: "MatchProposal",
) -> LineCard:
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
        arrivals_already_held=None,
        risk_class=proposal.review_class,
        panel=VerbPanel(
            offers=_offers(review, ADD_SHUT_BY_A_PROPOSAL, proposal),
            # **A proposal is a CONCLUSION**, so this pass has no unfinished
            # search to report about its line: the gap sentence answers *why
            # can the app not say nothing explains this*, which a proposal
            # answers.
            notes=(),
            answer_door=None,
            # **No ADD act at all.**  The pass derives destinations from
            # ``unmatched``, which a proposal's line is not in, so no envelope
            # was worked out for it -- which is exactly what
            # :data:`~._verbs.ADD_SHUT_BY_A_PROPOSAL` says on the tab.
            add=None,
            proposal=proposal,
        ),
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
        arrivals_already_held=None,
        risk_class=placement.sweep_class if names_a_home else None,
        panel=VerbPanel(
            offers=_offers(review, creatable.withheld, None),
            # **The gap is not asked for**: :func:`~._verdict.ruled` has
            # already folded it into ``warning``, which is the wider sentence
            # -- a rule this pass withheld, OR a search it did not finish --
            # so asking again would print the same words twice.
            notes=() if creatable.warning is None else (creatable.warning,),
            answer_door=None,
            add=AddTab(
                act=AddAct.PURCHASE,
                destinations=creatable.destinations,
                placement=placement,
                # Money ARRIVING that a rule files as a purchase is a REFUND
                # (ruling **R-II**).  Stated here, where the line is in hand,
                # so no template restates the partition -- and asked through
                # :func:`~._rules.is_inflow`, which is the ONE statement of the
                # bank's sign convention this package has.
                records_a_refund=is_inflow(creatable.line.amount),
            ),
            proposal=None,
        ),
    )


def _inflow_card(
    review: "ReviewSet", inflow: "RecordableInflow",
) -> LineCard:
    """Return the card for money coming IN that no row explains.

    **Ruling R-HX, and the condition it set is now MET for one class of
    deposit** (plan step ``bank_import:X-gj-2a``).  That ruling said every
    unmatched inflow reads *Choose what this is* "until ``X-gj-2`` ships
    **R-HT(a)**'s inflow rule and the destination becomes one the app can
    defend" -- because recording a deposit as uncategorized income was the ONLY
    inflow act, and being the only act is not what **R-HS** means by justified.

    So the split is by whether a STANDING RULE answers this signature, and by
    nothing else:

    * a rule that names an income category is a destination the owner stated,
      so the card states it and offers OK (:func:`~._sentence
      .for_income_placement`);
    * everything else still reads :func:`choose` and opens the panel -- a
      merchant credit included, which is a REFUND whose act
      ``bank_import:X-gj-2b`` builds, and calling it income remains the wrong
      act this arc's audit measured.

    **The reason a rule withheld is a NOTE and never a suppressed sentence.**
    An unresolved placement carries one -- an archived category, or a spending
    answer whose refund arm does not exist yet -- and it joins the panel's
    notes beside the search gap, so the owner reads why their own rule did
    nothing rather than seeing a card that looks unanswered.

    Args:
        review: The pass, which owns the search gap, the
            books-already-hold-income signal and the category names a stated
            rule resolves against.
        inflow: The :class:`~._leftovers.RecordableInflow`.

    Returns:
        The :class:`LineCard`.
    """
    gap = review.search_gap_for(inflow.line)
    placement = inflow.placement
    files_here = placement is not None and placement.records
    withheld_by_rule = (
        placement.unresolved_reason if placement is not None else None
    )
    return LineCard(
        line=inflow.line,
        section=Section.BY_RULE if files_here else Section.NOTHING,
        suggested=Verb.ADD if files_here else None,
        sentence=(
            for_income_placement(placement)
            if files_here else choose()
        ),
        arrivals_already_held=review.arrivals_already_held_in(inflow.line),
        risk_class=None,
        panel=VerbPanel(
            offers=_offers(review, inflow.withheld, None),
            notes=tuple(
                said for said in (inflow.withheld, withheld_by_rule, gap)
                if said is not None
            ),
            answer_door=None,
            # **An income row is filed against NO container**, so the tab
            # offers no destinations and carries no placement: ruling
            # **R-GW** states that emptiness as the whole difference between
            # the two ADD doors, and :class:`~._panel.AddAct` is what says
            # which one this is instead of a template reading the sign.
            # ``records_a_refund=False`` is STATED rather than defaulted
            # (plan step ``bank_import:X-gj-2b-3``): an income row is filed
            # against no container, so it is not a refund -- and a default
            # would have meant that silently for every future builder too.
            add=AddTab(
                act=AddAct.INCOME, destinations=(), placement=None,
                records_a_refund=False,
            ),
            proposal=None,
        ),
    )


def parked_card(
    review: "ReviewSet", parked: "ParkedLine",
) -> LineCard:
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
        arrivals_already_held=None,
        risk_class=None,
        panel=VerbPanel(
            offers=_offers(review, parked.reason, None),
            notes=tuple(
                said for said in (parked.reason, gap) if said is not None
            ),
            answer_door=parked.answer_door,
            # **NO ADD act, and that is the BAR rather than a shortage of
            # destinations** (ruling **R-GJ**): no answer lifts it, so there
            # is nothing for the tab to offer beyond the refusal its own
            # offer carries.
            add=None,
            proposal=None,
        ),
    )


def to_explain_sections(
    review: "ReviewSet",
) -> "tuple[CardSection, ...]":
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
        + [
            _inflow_card(review, one)
            for one in review.recordable_inflows
        ]
    )
    sections = []
    for section in Section:
        mine = _newest_first(
            card for card in cards if card.section is section
        )
        if mine:
            sections.append(
                CardSection(section=section, cards=mine, withheld=0),
            )
    return tuple(sections)


def _newest_first(cards) -> "tuple[LineCard, ...]":
    """Return *cards* with the most recent bank day first.

    The locked direction's own rule for a section (``docs/design
    /bank_import_audit.md``, *Within a section, newest first*), and it was not
    kept until plan step ``bank_import:X-gj-1b``: the pass hands its lines
    over ASCENDING by day (:attr:`~._reads.ReviewSet.unmatched`), so every
    section rendered oldest first -- the owner's most recent swipes, which are
    the ones they can still remember, at the bottom of a 27-card list.

    **Sorted HERE rather than in Jinja**, because the order a screen presents
    work in is a decision and a template restating it is a second place for it
    to be wrong -- the rule this package keeps for every count and every
    partition.

    Args:
        cards: The section's cards, in the pass's own order.

    Returns:
        Them, descending by the bank's POSTED day.  **A STABLE sort**, so two
        lines the bank posted on one day keep the pass's own order rather than
        an arbitrary one that could differ between two renders of the same
        page -- which is what a reader comparing a screenshot would see.
    """
    return tuple(
        sorted(cards, key=lambda card: card.line.posted_on, reverse=True)
    )


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
