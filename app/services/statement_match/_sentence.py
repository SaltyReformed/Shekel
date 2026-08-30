"""The ONE sentence a Reconcile card carries, composed as SPANS.

Ruling **bank_import:R-HR**, plan step ``bank_import:X-gj-1a``.  A card shows
the decision and nothing else: the bank's facts, the amount, one sentence
whose FIRST WORD is the verb, and OK.  Every reason -- why a rule withheld,
what a tier declined, what accepting writes -- is the opened panel's and the
receipt's.  This composes the sentence.

**It is a list of SPANS rather than a formatted string, and that is what keeps
money out of the service.**  The sentence mixes plain words, emphasised names
and FIGURES, and a service that formatted ``$0.05`` into a string would be the
second money formatter beside ``_money_macros.money`` -- which is the rule
:class:`~._reads.IncomeAlreadyRecorded` already states by carrying rows and a
Decimal instead of a sentence.  A span carries either words or a figure, never
both, and the template renders each by its :class:`Ink`.

**It is spans rather than named fields for a second reason**: a MATCH sentence
and an ADD sentence do not have the same parts, so a fixed-field value would
force the template to branch on the verb to lay them out -- a partition
restated in Jinja, which is the shape this package refuses in
:attr:`~._placement.Placement.sweep_class`,
:attr:`~._bars.ParkedLine.reason` and
:attr:`~._offers.MatchProposal.review_class`.  A template that loops spans
branches on nothing.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
frozen dataclasses out, no Flask import, no clock read, no query.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from ._placement import Placement
from ._verbs import Verb

if TYPE_CHECKING:  # pragma: no cover -- annotations only
    from ._accepted_view import AcceptedGroup
    from ._bars import ParkedLine
    from ._offers import MatchProposal


#: How many member rows a group's sentence NAMES before it says how many more
#: there are.  Three, because the developer's own payroll deposits are two and
#: three rows (Data Manager, Health Insurance Allowance, sometimes Phone
#: Allowance) and naming them is the whole point of the sentence; past that a
#: card would wrap to three lines.  **The remainder is COUNTED and not
#: dropped**: a silent cap reads as a complete list.
NAMED_ROW_LIMIT = 3


class Ink(enum.Enum):
    """How one span of the sentence is drawn.

    Not colours: the five are ROLES, and the stylesheet decides what each
    looks like in either theme.  A service naming ``--shekel-accent`` would be
    a presentation decision taken where the presentation cannot be seen.

    * ``VERB`` -- the first word, which is the decision the card states.
    * ``CHOOSE`` -- the first word when there is no suggestion at all, drawn
      in the accent because it is the one thing on the card asking to be
      pressed.
    * ``STRONG`` -- what the verb acts on: the row, the envelope, the account.
    * ``PLAIN`` -- the words that join them.
    * ``MUTED`` -- a trailing phrase that qualifies the sentence without being
      part of the decision.  **The separator before one is the template's**,
      not a character in the span: a service that wrote ``"* off by"`` would
      be choosing a glyph it cannot see rendered, in either theme.

    **NO SPAN OPENS WITH A MARK THAT MUST HUG THE WORD BEFORE IT** -- no
    comma, colon, semicolon or full stop -- which is the contract that lets the
    template join spans with a single space and nothing else.  A span reading
    ``", 2026-08-13"`` rendered as ``Lowe's , 2026-08-13`` on the developer's
    own data; the alternative, a template that suppresses the space before
    certain characters, is presentation logic deciding where a sentence breaks,
    which is exactly what spans exist to take out of Jinja.  An opening bracket
    is fine and is how a period span reads: ``Lowe's (2026-08-13 - 2026-08-26)``.
    """

    VERB = "verb"
    CHOOSE = "choose"
    STRONG = "strong"
    PLAIN = "plain"
    MUTED = "muted"


@dataclass(frozen=True)
class Span:
    """One piece of a card's sentence: words OR a figure, and how to draw it.

    Attributes:
        text: The words, or ``None`` for a figure span.
        money: The figure, or ``None`` for a words span.  Rendered by the
            ``money`` macro, which is the app's ONE money formatter.
        ink: Which role this span plays (:class:`Ink`).

    **Exactly one of** :attr:`text` **and** :attr:`money` **is set**, and it is
    guaranteed by the two constructors rather than by a check every reader
    remembers: :meth:`words` and :meth:`figure` are the only ways one is made
    here, and a template asking ``span.money is not None`` gets a total answer.
    """

    text: "str | None"
    money: "Decimal | None"
    ink: Ink

    @classmethod
    def words(cls, text: str, ink: Ink) -> "Span":
        """Return a span of plain words.

        Args:
            text: The words.
            ink: How to draw them.

        Returns:
            The :class:`Span`.
        """
        return cls(text=text, money=None, ink=ink)

    @classmethod
    def figure(cls, amount: Decimal, ink: Ink) -> "Span":
        """Return a span carrying a figure for the ``money`` macro to format.

        Args:
            amount: The figure, as a ``Decimal`` -- never a pre-formatted
                string, because formatting money is the macro's job and a
                second formatter is how two surfaces come to disagree.
            ink: How to draw it.

        Returns:
            The :class:`Span`.
        """
        return cls(text=None, money=amount, ink=ink)


def choose() -> "tuple[Span, ...]":
    """Return the sentence for a line nothing has suggested a verb for.

    Ruling **bank_import:R-HS** bans the ARBITRARY default and ruling
    **R-HX** bounds what counts as justified: where the app cannot defend a
    destination it asks rather than proposing one, and the card opens its
    panel instead of offering a one-click OK.

    Returns:
        The spans.
    """
    return (
        Span.words("Choose", Ink.CHOOSE),
        Span.words("what this is", Ink.PLAIN),
    )


def _named_rows(labels: "tuple[str, ...]") -> "tuple[Span, ...]":
    """Return the spans naming a group's member rows.

    Args:
        labels: Every member's label, in the order the pass holds them.

    Returns:
        The spans: the count, then the first :data:`NAMED_ROW_LIMIT` labels,
        then how many were not named.  **The remainder is stated**, so a
        reader is never shown a partial list that reads as a whole one.
    """
    named = labels[:NAMED_ROW_LIMIT]
    spans = [
        Span.words(f"{len(labels)} rows", Ink.STRONG),
        Span.words(" + ".join(named), Ink.PLAIN),
    ]
    remainder = len(labels) - len(named)
    if remainder:
        spans.append(Span.words(f"and {remainder} more", Ink.PLAIN))
    return tuple(spans)


def for_proposal(proposal: "MatchProposal") -> "tuple[Span, ...]":
    """Return the sentence for a match a TIER has proposed.

    Args:
        proposal: The :class:`~._offers.MatchProposal`.

    Returns:
        The spans.  A one-row proposal names the row; a group names how many
        and which.  **The difference appears only where accepting would move
        an AMOUNT** (:attr:`~._offers.MatchProposal.reprices`) -- asked of the
        proposal rather than tested here as ``difference != 0``, which is the
        rule that property exists to state once.
    """
    labels = tuple(row.label for row in proposal.rows)
    spans: "list[Span]" = [Span.words(Verb.MATCH.word, Ink.VERB)]
    if len(labels) == 1:
        spans.append(Span.words(labels[0], Ink.STRONG))
    else:
        spans.extend(_named_rows(labels))
    if proposal.reprices:
        spans.append(Span.words("off by", Ink.MUTED))
        spans.append(Span.figure(proposal.difference, Ink.STRONG))
    return tuple(spans)


def for_placement(placement: Placement) -> "tuple[Span, ...]":
    """Return the sentence for spending a standing RULE names a home for.

    Ruling **bank_import:R-HS**: the destination a rule names is a suggestion
    the app can justify, so the card states it and offers OK.

    **The envelope's own NAME is emitted, not**
    :attr:`~._creations.PurchaseDestination.label`, and the period follows it
    as a separate span, because the name has to be emphasised and the period
    must not be.  **The reason is LAYOUT and nothing else**: an earlier draft
    argued the name is what a rule matches on, and that is false for the arm
    it was written on -- ``PurchaseDestination.template_id`` records that a
    rule cannot be keyed on the name, since template 22 generated a row called
    ``Kayla`` in one period and ``Kayla's Spending Money`` in the other 60.
    Named by adversarial review 2026-08-29.  Both spans are read off the same
    destination in one place, so the two cannot describe different rows; if a
    third reader ever needs the pair, it belongs beside ``label`` rather than
    spelled again here.

    Args:
        placement: The :class:`~._placement.Placement`, which must name a
            destination -- ``records_in`` or ``creates``.  An UNRESOLVED
            placement names none and its card takes :func:`choose` instead.

    Returns:
        The spans.

    Raises:
        ValueError: When *placement* names no destination.  **A refusal rather
            than a fallback sentence**: substituting is how a suggestion
            becomes a guess, which is the rule :mod:`._placement` opens with,
            and a card cannot state a home the rule never named.
    """
    if placement.records_in:
        destination = placement.destination
        return (
            Span.words(Verb.ADD.word, Ink.VERB),
            Span.words("to", Ink.PLAIN),
            Span.words(destination.name, Ink.STRONG),
            Span.words(
                f"({destination.period_start} - {destination.period_end})",
                Ink.PLAIN,
            ),
        )
    if placement.creates:
        spans = [
            Span.words(Verb.ADD.word, Ink.VERB),
            Span.words("to a new envelope", Ink.PLAIN),
            Span.words(placement.new_envelope.name, Ink.STRONG),
        ]
        if placement.joins_new:
            # **Said on the card rather than discovered after the press**
            # (finding **N-327**): an earlier line in this same pass already
            # creates that envelope, so this one joins it.
            spans.append(
                Span.words("joining the one this pass creates", Ink.MUTED),
            )
        return tuple(spans)
    raise ValueError(
        f"A placement of kind {placement.kind.value!r} names no destination, "
        f"so no sentence can state where it files. Its card states "
        f"{placement.unresolved_reason!r} instead."
    )


def for_parked_transfer(parked: "ParkedLine") -> "tuple[Span, ...]":
    """Return the sentence for a payment to an account the owner holds.

    Ruling **bank_import:R-GJ** parks such a line and **R-HQ** makes it a
    HOLDING state rather than inbox work.  The card says what the money did
    and what the app is waiting for; it carries no control, because there is
    no door (see :data:`~._verbs.TRANSFER_WAITS`).

    Args:
        parked: The :class:`~._bars.ParkedLine`, which must be one a source
            files as paying an account the owner holds.

    Returns:
        The spans.
    """
    return (
        Span.words(Verb.TRANSFER.word, Ink.VERB),
        Span.words("to", Ink.PLAIN),
        Span.words(parked.line.merchant_label, Ink.STRONG),
        Span.words("waiting for that account", Ink.MUTED),
    )


def for_parked_never(parked: "ParkedLine") -> "tuple[Span, ...]":
    """Return the sentence for a merchant the owner said is never a purchase.

    **This line is already SKIPPED, and by a standing decision rather than a
    stored disposition** -- which is why it needs none of plan step
    ``bank_import:X-gj-4``'s store to leave the inbox: the owner has said, once
    and for this merchant, that its money is not spending, and ruling
    **R-HP** calls a line deliberately explained by nothing a SKIP.  Ruling
    **R-HQ** then puts it on the tab that owns it rather than in a queue that
    offers it no act.

    **It is NOT a transfer and must not sit with them.**  The two bars are
    different kinds of fact (:class:`~._bars.CreationBar`): one is a decision
    the owner made, the other an observation about where the money went, and a
    screen that filed the first under Transfers would tell someone their bank
    had decided for them.  Measured 2026-08-29 on the developer's own account:
    0 of his 9 parked lines are this shape -- all nine carry BOTH bars, so all
    nine are transfers -- which makes this the arm that has never rendered,
    built because the predicate is real and the data is one account's.

    Args:
        parked: The :class:`~._bars.ParkedLine`, barred by the owner's own
            answer and NOT also filed as paying an account they hold.

    Returns:
        The spans, in the past tense: the decision has already been made.
    """
    return (
        Span.words(Verb.SKIP.past, Ink.VERB),
        Span.words("because", Ink.PLAIN),
        Span.words(parked.line.merchant_label, Ink.STRONG),
        Span.words("is never a purchase", Ink.PLAIN),
    )


def for_accepted(group: "AcceptedGroup") -> "tuple[Span, ...]":
    """Return the past-tense sentence for an act that has already landed.

    The Explained and Filed-by-rules tabs render the SAME card as the inbox
    with the sentence one tense over, which is what makes the five tabs one
    list rather than five screens.

    **ADD and MATCH are told apart by**
    :attr:`~._accepted_view.AcceptedGroup.created_every_row`.  A MATCH says
    *this line IS a row the books already hold*, so an act naming even one row
    it did not create is one -- and an act that merely CREATED something is
    not enough, because an unbalanced group mints **R-FN**'s residual and
    records it as a creation.

    Args:
        group: The :class:`~._accepted_view.AcceptedGroup`.

    Returns:
        The spans.  A group that no longer HOLDS says so in the trailing
        phrase, because that is the one thing about a settled act a reader
        must act on (:attr:`~._accepted_view.AcceptedGroup.agrees`).
    """
    labels = tuple(row.label for row in group.rows)
    verb = Verb.ADD if group.created_every_row else Verb.MATCH
    spans: "list[Span]" = [Span.words(verb.past, Ink.VERB)]
    if len(labels) == 1:
        spans.append(Span.words(labels[0], Ink.STRONG))
    elif labels:
        spans.extend(_named_rows(labels))
    else:
        # **A match can outlive every row it named** -- a cascade takes the
        # members with the purchase or the pay period -- and that is exactly
        # the state ``agrees`` reports.  The sentence says so rather than
        # ending on the verb alone.
        spans.append(Span.words("rows that no longer exist", Ink.STRONG))
    if not group.agrees:
        spans.append(Span.words("this no longer holds", Ink.MUTED))
    return tuple(spans)
