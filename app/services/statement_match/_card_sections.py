"""A TAB's list of cards, assembled from one pass.

Plan step ``bank_import:X-gj-4b``, split out of :mod:`._cards` under ruling
**balance:R-IR**, which puts a module split on the session that BREAKS the
1,000-line bound rather than on a later tidy-up.  That module stood at 992
lines with eight to spare, and lighting the SKIP verb spent them.

**The seam is the GRAIN and not the line count.**  Everything left in
:mod:`._cards` builds ONE card -- a bank line, an applied act, a recorded skip
-- and everything here builds a TAB's list of them: which of the pass's lists
feed which section, what order a section renders in, and the bound each list
carries.  A reader asking *what does one card show* and a reader asking *what
is on this tab* were reading one file.

**Nothing changed on the way across** except three builders' names.
``_proposal_card``, ``_creatable_card`` and ``_inflow_card`` are
``proposal_card``, ``creatable_card`` and ``inflow_card`` now, because a
builder a sibling module calls is public -- which is the rule
:func:`~._cards.parked_card` has already followed since plan step
``bank_import:X-gj-1b``, when :mod:`._reconcile` began calling it for the
Transfers tab.  The three were private only because their one caller happened
to sit in the same file.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
frozen dataclasses out, no Flask import, no clock read, no query -- the pass
and the registers arrive already derived.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._cards import (
    ActCard,
    CardSection,
    LineCard,
    Section,
    SkipCard,
    answered_never_card,
    creatable_card,
    inflow_card,
    proposal_card,
)
from ._sentence import for_accepted, for_skip

if TYPE_CHECKING:  # pragma: no cover -- annotations only
    from ._reads import ReviewSet


def to_explain_sections(
    review: "ReviewSet",
) -> "tuple[CardSection, ...]":
    """Return the inbox, grouped by what suggested each card's verb.

    Ruling **bank_import:R-HP**.  **The four source lists are DISJOINT and
    that is what lets them be concatenated**: ``creatable``,
    ``recordable_inflows`` and ``answered_never`` are all subsets of
    ``unmatched``, which :func:`~._reads._unexplained` has already taken every
    proposal's line out of, and :func:`~._leftovers._creatable_lines` puts each
    barred line in exactly one of its two lists -- so no line can appear on two
    cards.  *It said THREE until plan step ``bank_import:X-gj-4c``.*

    **``parked`` is absent** (**R-HQ**): a line a source files as paying an
    account the owner holds is a holding state on its own tab, never inbox
    work.  **``answered_never`` is PRESENT** (**R-JH**), and the two used to be
    one list: a standing *never a purchase* answer shuts the ADD door and
    claims nothing about what the line is, so such a line is still work and
    still has MATCH.

    Args:
        review: The pass.

    Returns:
        One :class:`CardSection` per :class:`Section` that has a card, in the
        enum's order.  An empty section is ABSENT rather than rendered empty.
    """
    cards = (
        [proposal_card(review, one) for one in review.proposals]
        + [creatable_card(review, one) for one in review.creatable]
        + [
            inflow_card(review, one)
            for one in review.recordable_inflows
        ]
        + [
            answered_never_card(review, one)
            for one in review.answered_never
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


def skip_sections(register) -> "tuple[CardSection, ...]":
    """Return the recorded skips, as one unnamed section of cards.

    Plan step ``bank_import:X-gj-4c-2``.  :func:`act_sections`' twin one act
    over, and a separate builder rather than a parameter on that one, because
    the two build DIFFERENT card types from different values -- which is the
    whole reason :class:`SkipCard` is not an :class:`ActCard`.

    Args:
        register: The :class:`~._skipping.SkippedRegister` -- the acts to
            render, in the reader's own order (newest bank day first), and how
            many the bound left out.

    Returns:
        One :class:`CardSection`, or ``()`` where the account has no skip --
        because an empty section is ABSENT rather than rendered empty, which
        is this module's rule for every other list.  **The bound travels with
        the cards**, exactly as :func:`act_sections` carries the settled one,
        so the tab can say how many it did not render (ruling
        **bank_import:R-GX**).
    """
    if not register.shown:
        return ()
    return (
        CardSection(
            section=None,
            cards=tuple(
                SkipCard(skip=act, sentence=for_skip())
                for act in register.shown
            ),
            withheld=register.withheld_count,
        ),
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
