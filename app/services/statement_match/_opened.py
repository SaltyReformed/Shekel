"""The MATCH pane of ONE card: the rows it offers, and what the ticked ones come to.

Plan step ``bank_import:X-gi-1``, ruling **bank_import:R-KA**.  **One producer
for a pane two surfaces render**: the live fragment
(``accounts.statement_reconcile_match``) re-derives it on every tick and every
keystroke of the search, and the Reconcile PAGE derives it for the one card a
scriptless request named with ``?open=<line_id>``.  A second assembly would be
two places for a ticked row to be dropped on the way to a door that MOVES
MONEY.

**HOW FAR IT REACHES IS THE CALLER'S FACT** (:class:`MatchReach`), and ruling
**R-BI1** is what decides it (developer, 2026-09-05).  It SUPERSEDES **R-KA**'s
row-set half rather than filling a gap R-KA left: that ruling DID settle the
reach, at the line's own pay period -- *one card is at most 15 rows* -- and
this is the override rather than an answer to an open question.

The scripted pane opens on the line's own pay period and the
SEARCH reaches the rest, which is what keeps a 27-card page off finding
**N-374**; the scriptless render has no search at all, so a period-bounded list
is a bound the owner cannot widen -- which is the cap **N-374** refused.  The
measurement that decides it is :mod:`._panel`'s own (2026-08-30): all 9 of the
9 card payments on the developer's Checking have payback rows their own period
does NOT hold, so the class **R-KA** exists to give an act back is exactly the
class a period-bounded list cannot serve.  The cost is bounded, and it is a
RANGE rather than a figure: rendering this pane's own candidate-row loop body
2026-09-05 gives **570 bytes** for a row with a 30-character label and no
``not_shown_alone`` badge, and about **760** for one carrying that badge, whose
sentence varies.  So 67 unexplained rows on ONE card is roughly **40 KB**,
against the 143,298 bytes **R-KA** rejected for every card's period rows -- and
paid only on a request that exists because the owner asked for that card.

*A first draft of this paragraph quoted one figure and then "reconciled" it
against R-KA's by dividing 143,298 by that render's 311 row renders.  That
arithmetic is wrong and the agreement it manufactured was worth nothing: the
143,298 is the byte total of the PANES, each of which also carries a trigger
wrapper, a search box, a sums block and a consent block, so the quotient is not
a per-row rate at all.  Adversarial review 2026-09-05.*

**EVERY TICKED ROW THE PASS STILL OFFERS IS RENDERED, whatever list this
render is otherwise showing**, and that is not a courtesy: a checkbox that is
not in the document is not submitted.  Without it, ticking a search result and
then searching again takes the first pick silently out of the act -- so a card
payment could not be grouped against two paybacks found by two searches, which
is the very act this module exists to make reachable.  The same hole is what
would make :attr:`MatchReach.EVERY_ROW` unsafe on a browser that DOES have
scripting: it would render all 67, and the first re-price would swap in the
period's 15 and drop whatever the owner had ticked outside them.

**The qualifier is exact and the residue is answered rather than hidden.**  A
submitted row the pass no longer offers -- one another act claimed between two
renders, or a crafted id -- is in no list this walks, so it renders nowhere;
:func:`~._resolve.resolve_rows` refuses that body by name and
:attr:`OpenedMatch.totals` carries the sentence, which is the door's own answer
rather than a silent drop.

**TWO CLAIMS ELSEWHERE REST ON A MECHANISM THIS MODULE REMOVES**, and both are
narrower than they read (adversarial design review 2026-09-05).
:func:`~._variance._reject_unaccepted_difference` argues that a consent ticked
against one remedy and submitted under another is unconstructible BECAUSE the
attribution select's change re-renders the box unticked -- and nothing swaps
anything on the ``?open=`` render.  The body stays unconstructible on a
narrower ground: the select needs :attr:`~._preview.HandTotals.choices`, which
is empty unless a match names more than one row AND its sides disagree, and
every proposal a tier offers is exact or one-to-one.  **``bank_import:X-gn``
re-arms it** -- a match naming a second bank line is exactly that shape -- so
that step owes the guard a re-reading.  The same docstring says a one-row NEAR
MISS can no longer be corrected with scripting off; this pane gives that back,
minus the watching-it-re-price, which is why its copy says the two sides are
compared at the press.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in, a
frozen dataclass out, no Flask import.  It READS and never writes --
:func:`~._preview.preview_hand_build` is the accept door's own reads and
refusals without the writes.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from ._panel import MatchCandidates
from ._preview import preview_hand_build
from ._submission import MatchSubmission, as_reviewed

if TYPE_CHECKING:  # pragma: no cover -- annotations only
    from ._offers import BankLine, CandidateRow, MatchProposal, RowKind
    from ._preview import HandTotals
    from ._reads import CardSubject, ReviewSet
    from ._scope import ReviewScope


class MatchReach(enum.Enum):
    """How far one render of the MATCH pane may offer rows.

    **A property of the RENDER and not of the line**, which is why it is a
    parameter rather than a fact on the pass: the same card reaches one list
    through the live fragment and another through ``?open=<line_id>``, and the
    difference is whether the owner can ask a second question.
    """

    #: The line's own pay period, which the SEARCH widens.  What the live
    #: fragment offers, because it is re-derived on every tick and every
    #: keystroke and 67 rows a keystroke is the payload **N-374** is about.
    THE_PERIOD = "period"

    #: Every unexplained row on the account.  What a render with no search
    #: offers, because a bound the owner cannot widen is the cap **N-374**
    #: refused -- and with scripting off there is no keystroke to widen it
    #: with.
    EVERY_ROW = "every"


@dataclass(frozen=True)
class OpenedMatch:
    """What one card's MATCH pane renders.

    Attributes:
        line: The :class:`~._offers.BankLine` the card is about.
        proposal: The :class:`~._offers.MatchProposal` a tier offers for it, or
            ``None``.  Its rows render TICKED (**R-HS**) and are disjoint from
            :attr:`rows` by construction -- the pass withholds every row a
            proposal claims from ``unmatched_rows`` -- so no row renders twice.
        rows: The candidate rows this render offers, in the pass's own order.
            Every list this module can choose is a subsequence of
            :attr:`~._panel.MatchCandidates.every` in that one order, so the
            union with the ticked rows has one order and not two.
        ticked: The ``(kind, row_id)`` of every row the request named, which
            is what renders a box checked.
        query: What the owner typed into the search, or ``""``.  Echoed so the
            box survives its own re-render.
        re_fetches: Whether this pane will ask the server again.  **ONE field
            and not two, because the search and the running difference are one
            fact wearing two hats** (``CLAUDE.md`` rule 14): both work by the
            same round trip, so a pane that re-fetches has both and a pane
            that does not has neither.  Two booleans that are always equal are
            one value with a maintenance contract.

            ``False`` is the scriptless page, and both consequences are
            user-facing.  The SEARCH box would be inert, its label would offer
            a widening that cannot happen, and -- because the pane sits inside
            the Apply form whose only submit is the money button -- pressing
            Enter in it would PRESS THAT DOOR, which is what ruling **R-BI1**
            rejected a scriptless search submit to avoid.  The FIGURES would
            be computed once and stale from the first tick, so the consent
            copy has to say what the door will do at the press rather than
            promise a difference that "appears here".  Adversarial review
            2026-09-05 found the template asserting both.
        totals: What the ticked rows come to and what Apply would do
            (:class:`~._preview.HandTotals`), or ``None`` on a render the
            schema refused before anything could be priced.
    """

    line: "BankLine"
    proposal: "MatchProposal | None"
    rows: "tuple[CandidateRow, ...]"
    ticked: "frozenset[tuple[RowKind, int]]"
    query: str
    re_fetches: bool
    totals: "HandTotals | None"


def proposed_submission(subject: "CardSubject") -> MatchSubmission:
    """Return the submission a card carries before the owner touches it.

    **The value twin of the hidden fields an unopened card renders**: a tier's
    own rows arrive ticked (**R-HS**), so the pane a scriptless request opens
    has to be priced against exactly them or it would report `$0.00` for a
    proposal the card is already offering to apply.

    Args:
        subject: The :class:`~._reads.CardSubject` the page drew this card
            from.

    Returns:
        The :class:`~._submission.MatchSubmission`, naming this line and the
        proposal's rows -- or the line alone for a card no tier paired.

        Its ``accepted_difference`` is ``None`` because nothing has been
        consented to on a fresh render, and
        :func:`~._preview.preview_hand_build` ignores the field in any case:
        it computes the figure the owner is about to be shown, so reading one
        back would be the screen agreeing with itself.
    """
    proposal = subject.proposal
    return MatchSubmission(
        line_ids=frozenset({subject.line.line_id}),
        rows=frozenset(
            () if proposal is None
            else (as_reviewed(row) for row in proposal.rows)
        ),
        accepted_difference=None,
        attributed_to=None,
    )


@dataclass(frozen=True)
class MatchAsk:
    """What ONE render asks of one card's MATCH pane.

    A parameter object rather than four more arguments, which is this
    project's remedy where a PUBLIC function goes over the argument ceiling --
    and these four are one cohesive entity: *which card, holding what, and how
    far it may look*.  The pass it is answered against
    (:class:`~._scope.ReviewScope` and :class:`~._reads.ReviewSet`) is not
    here, because that is a fact about the REQUEST rather than about the ask.

    Attributes:
        subject: Which card, and what claims it
            (:class:`~._reads.CardSubject`).  The caller resolves it, because
            the caller is what answers a line this pass renders no card for --
            a 404 on the fragment, and NO pane on the page, which are
            different answers to the same absence: an ``?open=`` naming a line
            the owner has just applied is stale rather than forged.
        submitted: What the form currently holds, as the door would be given
            it (:class:`~._submission.MatchSubmission`).
        query: What the owner typed into the search.  Blank is not a search,
            and :attr:`reach` is what the list falls back to.
        reach: How far this render may offer rows (:class:`MatchReach`).
            Ignored while :attr:`query` holds a search, which is over every
            unexplained row on the account whoever asked.
    """

    subject: "CardSubject"
    submitted: MatchSubmission
    query: str
    reach: MatchReach


def _still_ticked(submission: MatchSubmission) -> MatchSubmission:
    """Return *submission* with an attribution the owner has just unticked gone.

    Plan step ``bank_import:X-gj-3a``, second pass.  **This is a TRANSIENT
    BROWSER STATE and not a body the door will ever be asked to honour**, so
    it is normalised here rather than refused.

    The sequence is ordinary.  The owner names a member for the difference,
    then unticks that member.  The change bubbles to ``.rec-match-picks`` and
    fires the live fragment -- and the select, which has not been re-rendered
    yet, posts its now-stale value alongside a ``rows-<line>`` list that no
    longer holds it.  :func:`~._resolve.resolve_rows` refuses exactly that
    shape, correctly and by design, so without this the panel would answer
    *"This match says its difference belongs to a row it does not include.
    Reload the page and try again"* -- a sentence written for a crafted body,
    shown for a legal click, on the one screen whose whole job is to say what
    the press would do.

    **The swap that follows drops the option**, so the next body carries no
    attribution and the state is self-correcting; what this removes is the one
    render in between.

    **It is NOT in the schema reader, and that is the whole of its placement**
    (:func:`~app.schemas.validation.statement_reconcile
    .reconcile_match_payload`): that reader is shared with APPLY, where
    dropping a submitted attribution would silently change which of two money
    acts the press performs.  It ran in the ROUTE until plan step
    ``bank_import:X-gi-1`` gave the pane a second surface, and is here now so
    that the pane's producer owns it rather than one of its callers.

    **Only the FRAGMENT can make it fire**, and saying so is the correction:
    the page builds its submission through :func:`proposed_submission`, which
    sets ``attributed_to=None`` unconditionally, so on that path this returns
    at its first guard every time.  A first draft justified the move as
    "a normalisation one of two callers has to remember is one the other can
    forget", which describes a state that cannot be constructed today.  What
    the placement actually buys is that a LATER caller passing a real
    submission inherits it (adversarial review 2026-09-05).

    Args:
        submission: What the render's body said.

    Returns:
        It unchanged, or without its ``attributed_to`` where that row is not
        among the rows the same body ticked.
    """
    if submission.attributed_to is None:
        return submission
    if submission.attributed_to in submission.rows:
        return submission
    return replace(submission, attributed_to=None)


def opened_match(
    scope: "ReviewScope", review: "ReviewSet", ask: MatchAsk,
) -> OpenedMatch:
    """Return everything one card's MATCH pane renders.

    Args:
        scope: The pass's derived offer set
            (:class:`~._scope.ReviewScope`).  **The route builds it**, which is
            the rule every read pass in this project is held to.
        review: The pass (:class:`~._reads.ReviewSet`).  Taken rather than
            derived, so a page and the pane inside it are one walk -- deriving
            a second would be the redundant producer call in one request this
            package treats as a DRY violation rather than a cost.
        ask: What this render asks (:class:`MatchAsk`).

    Returns:
        The :class:`OpenedMatch`.
    """
    candidates = MatchCandidates.of(scope, review)
    # **The stale attribution goes before anything is priced**, so a legal
    # untick is not answered with a refusal written for a crafted body.
    submitted = _still_ticked(ask.submitted)
    ticked = frozenset(
        (row.kind, row.row_id) for row in submitted.rows
    )
    if ask.query.strip():
        offered = candidates.matching(ask.query)
    elif ask.reach is MatchReach.EVERY_ROW:
        offered = candidates.every
    else:
        offered = candidates.for_line(ask.subject.line)
    shown = {(row.kind, row.row_id) for row in offered}
    return OpenedMatch(
        line=ask.subject.line,
        proposal=ask.subject.proposal,
        # **The offer UNION the owner's own picks**, walked over ``every`` so
        # one order serves both halves: ``for_line`` and ``matching`` are each
        # a subsequence of it, so nothing is reordered by being widened.
        rows=tuple(
            row for row in candidates.every
            if (row.kind, row.row_id) in shown
            or (row.kind, row.row_id) in ticked
        ),
        ticked=ticked,
        query=ask.query,
        # **THE REACH READ FOR WHAT IT IMPLIES**, and not a second fact: a
        # render that may be NARROWED by a later request is one that HAS later
        # requests.  Asked here rather than in the template, which computes
        # nothing.
        re_fetches=ask.reach is MatchReach.THE_PERIOD,
        totals=preview_hand_build(submitted, scope),
    )


def refused_match(subject: "CardSubject") -> OpenedMatch:
    """Return the pane a render whose submission was REFUSED shows.

    **Nothing is priced and nothing is offered**, because the body never
    reached the door: the schema graded it malformed, so there is no row set
    to price and no figure to show.  The caller renders the sentence beside
    it.

    **IT ECHOES NO QUERY**, and that is a correction rather than a detail
    (adversarial review 2026-09-05).  It carried the submitted one for a
    render, and the template's empty-list arm reads a query as *a search that
    matched nothing* -- so a refused body reported "no unexplained row on this
    account matches that" about a search that was never performed, on a screen
    whose whole job is to be trusted about what it is showing.

    Args:
        subject: Which card (:class:`~._reads.CardSubject`).

    Returns:
        The :class:`OpenedMatch`, carrying the line and its proposal and
        nothing else.  It is the SCRIPTED surface's arm -- the fragment is the
        only caller -- so it says the search works.
    """
    return OpenedMatch(
        line=subject.line,
        proposal=subject.proposal,
        rows=(),
        ticked=frozenset(),
        query="",
        re_fetches=True,
        totals=None,
    )
