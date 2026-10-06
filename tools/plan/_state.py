"""What the plan says, decided from the cards, the board, the claims and git.

Nothing here reads GitHub or git: the caller hands over what it read, so every
decision the ``plan`` command makes is a pure function a test can drive.

**Work** is a step or finding that is not a container (:func:`is_work`): the
only cards a branch builds and a commit ships.  ``claim``, ``next`` and
``sync`` all ask that one predicate.

**A leaf** of a step is a step linked under it -- unless it was dropped before
its filing finished: closed while still marked, and not shipped by git's
answer (:func:`leaves`, the one spelling; a container is a step with a leaf).
Such a card was never part of the split, so a split step left with no other
leaf is the plain step it was, and no read that lags an unlink, and no close
of a person's on the web, makes it a split step whose leaves were all dropped
(``R-BAL187``).

**Resolved** -- the one question ``next`` asks of a blocker and ``sync`` asks
of a container's leaves -- is git's answer for work, and the tracker's for the
rest (ruling ``balance:R-BAL170``):

- a card closed by a PERSON, or by ``plan drop``, is resolved: it was dropped
  (the build plan: "Dropped ... ``plan drop`` ..., or the developer closing a
  card by hand");
- a piece of work git says SHIPPED is resolved, whatever its open or closed
  state; one the TOOL closed as completed is only the tool's display of git,
  so it is resolved only while git says it shipped -- a ``Reopens:`` commit
  makes it unresolved at once, before ``sync`` reopens it;
- a container -- a step with a leaf (``R-BAL177``, :func:`leaves`) -- holds no decision
  of the tool's (``R-BAL190``): it is resolved by its leaves, when every
  leaf is, and dropped when every leaf was (``R-BAL187``: what waits on
  it is released, and no leaf can be filed under it), or when a PERSON closed
  it, a drop its leaves inherit (``R-BAL185``).  Its open or closed state is
  the tool's display of its leaves, which ``sync`` keeps in step both ways --
  reopened when a leaf is revived, closed as completed or as not planned as
  they end.  No commit ships a container, so a ``Ships:`` naming one is a
  mistyped or stale number, reported and never acted on;
- a ruling or a question is never shipped by git (a ruling is a record, a
  question the developer's), so it is resolved once it is closed: a question
  when it is answered (its card becomes the ruling) or withdrawn.

**A leaf inherits every step above it** (``R-BAL182``, ``R-BAL185``): it is
workable only when its own blockers AND every blocker of every step above it
are resolved, and while no step above it was dropped.  A wait is recorded
once, on the step it was set on, and so is a PERSON's close of a split step;
nothing is copied.  ``plan drop`` of a split step writes its drop on each leaf
below it that is still work instead (``R-BAL190``: the tool records a decision
only where the work is).

**A card whose filing has not finished is never offered** (``R-BAL202``):
every card ``plan file`` creates carries the filing mark from its first write
until its last removes it (:func:`filing_unfinished`), so a card some of whose
writes have not landed -- a leaf not yet linked under its split step, or not
yet in its place -- is never handed out or claimed, and ``next`` and ``sync``
name it until the same command finishes it, or ``plan drop`` drops it (a leaf
dropped while marked is first unlinked from its split step: it was never part
of the split, ``R-BAL205``).  While a filing is still running its card is named
too: no read can tell a filing running from one a failure cut short.  A leaf
dropped while marked some other way -- closed by a person on the web, or with
its split step -- is no leaf from its close, and ``sync`` unlinks it, putting a
split step it leaves with no leaf back on the board (:func:`unsplit`).

**A card linked to an issue outside the tracker is never offered**
(``R-BAL188``): the plan reads only its own cards, so the link is reported
until someone removes it, and a step blocked by an outside issue waits, as
does every leaf under it.

**``sync`` never overrides a person** (the build plan: a check "never acts on"
the developer's own edit): a card whose last close or reopen was a person's is
reported, never changed.  **Permanent git history never fails it**
(``R-BAL184``): what a commit already on ``dev`` says wrongly (a stray
``Reopens:``, a ``Ships:`` naming no card or a card no commit ships, a
malformed trailer) is printed apart as history, since no tracker write can
change it.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from tools.plan._tracker import Card, Claim
from tools.plan.setup_tracker import FILING

#: The build plan: "``plan next`` reports a claim older than 3 days with no pushed branch".
STALE_CLAIM = timedelta(days=3)
#: The kinds of card a commit ships; a ruling is a record and a question the
#: developer's, so ``sync`` never opens or closes either.
SHIPPABLE = ("step", "finding")


def leaves(card: Card, cards: Mapping[int, Card], shipped: Iterable[int]) -> tuple[int, ...]:
    """The steps ``card`` splits into, the one spelling: each step linked under it
    (:attr:`_tracker.Card.step_children`; the findings and rulings it owns decide
    nothing, R-BAL177) but one dropped before its filing finished -- CLOSED while still
    marked, and :func:`dropped` (not shipped by git's answer).

    Such a card was never part of the split: R-BAL205's reason ("It was never part of
    the split"), which R-BAL205 ruled for ``plan drop`` (which also unlinks it), carried
    by the L2 lane under R-BAL207 to a person's close of a still-marked leaf on the web,
    and to a read that lags ``plan drop``'s own unlink.  An OPEN marked leaf counts: its
    filing is under way, and its split step is not offered meanwhile.  A leaf git says
    shipped counts however it was closed: its work is done.  ``sync`` unlinks a card this
    rule leaves out (:func:`never_split`), so its mark stops deciding.  ``cards`` holds
    every card linked under ``card`` (:func:`missing`)."""
    shipped = set(shipped)
    return tuple(number for number in card.step_children
                 if not (FILING in cards[number].labels and not cards[number].is_open
                         and dropped(number, cards, shipped)))


def never_split(card: Card, cards: Mapping[int, Card], shipped: Iterable[int]) -> tuple[int, ...]:
    """The steps linked under ``card`` that are no leaf of it (:func:`leaves`): each dropped
    before its filing finished, so never part of the split (R-BAL205), and a link ``sync``
    removes (:func:`unsplit`)."""
    split = set(leaves(card, cards, shipped))
    return tuple(number for number in card.step_children if number not in split)


def is_container(card: Card, cards: Mapping[int, Card], shipped: Iterable[int]) -> bool:
    """A step split into steps: one with a leaf (:func:`leaves`).  A card that is not a
    step is no container, whatever hangs under it."""
    return card.kind == "step" and bool(leaves(card, cards, shipped))


def is_work(card: Card, cards: Mapping[int, Card], shipped: Iterable[int]) -> bool:
    """Whether a card is something a branch builds and a commit ships: a step or a
    finding, and not a container."""
    return card.kind in SHIPPABLE and not is_container(card, cards, shipped)


def withdrawn(card: Card) -> bool:
    """Whether ``card`` is a ruling withdrawn: closed as anything but completed -- not
    planned or a duplicate, by ``plan drop`` or a person (R-BAL206).  A ruling closed as
    completed, by anyone, is a record."""
    return card.kind == "ruling" and not card.is_open and card.state_reason != "COMPLETED"


def filing_ended(card: Card) -> bool:
    """Whether ``card``'s filing ended without standing: a ruling :func:`withdrawn`, marked
    or not, or any other card CLOSED while still marked.  A ruling closed as completed
    while marked is no such card: its filing is unfinished.  Of two cards a filing may
    already have made, one that ended is passed over for one that stands."""
    return withdrawn(card) or (FILING in card.labels and not card.is_open
                               and card.kind != "ruling")


def withdrawn_filing(card: Card, shipped: Iterable[int]) -> bool:
    """Whether a DECISION ended ``card``'s filing, so nothing is ever filed over it: it
    :func:`filing_ended`, and it is not WORK git says shipped -- a ruling withdrawn, or a
    card dropped while still marked, by ``plan drop`` or a person.  Work that shipped and
    was closed while still marked (its filing stopped at its last write, and a person
    closed it as ``sync`` asks) was not dropped (:func:`dropped`): its filing is moot, not
    refused.  A ruling or a question is never shipped, so a ``Ships:`` naming one decides
    nothing here, as it decides nothing anywhere (R-BAL184)."""
    return filing_ended(card) and not (card.kind in SHIPPABLE and card.number in set(shipped))


def filing_unfinished(card: Card) -> bool:
    """Whether ``card``'s filing has not finished (R-BAL202) -- one still running, or one a
    failure cut short: it still carries the filing mark, and is open, or is a ruling
    closed as completed, by its own filing (which closes it before the last write removes
    the mark) or by anyone (R-BAL206: a ruling is a closed record, so that close
    withdraws nothing).  A ruling :func:`withdrawn` is not; any other card closed while
    marked was dropped before its filing finished; what either's filing left undone is
    moot."""
    return FILING in card.labels and (card.is_open or (
        card.kind == "ruling" and not withdrawn(card)))


def _shown_shipped(card: Card) -> bool:
    """Closed by the tool as completed: the tool's display that git says it shipped,
    which follows git and decides nothing."""
    return not card.is_open and card.closed_by_tool and card.state_reason == "COMPLETED"


def unshipped_shown_done(card: Card, shipped: Iterable[int]) -> bool:
    """Whether ``card``, as a piece of work, is shown shipped -- closed by the tool as
    completed -- though git does not say it shipped (a ``Reopens:`` undid it, or it was a
    split step left plain): :func:`shown_write` reopens it."""
    return card.number not in set(shipped) and _shown_shipped(card)


def _closed_dropped(card: Card) -> bool:
    """A card that is no container closed by a person (for any reason), or by the tool as
    not planned (``plan drop``)."""
    return not card.is_open and not _shown_shipped(card)


def dropped(number: int, cards: Mapping[int, Card], shipped: set[int]) -> bool:
    """Whether a card was dropped.

    A piece of work: closed as dropped, and not shipped (git's answer, whoever
    closed it).  A container: closed by a PERSON (R-BAL185), or every leaf
    dropped (R-BAL187) -- never its own close by the tool, which only shows its
    leaves (R-BAL190).  A container has a leaf (:func:`is_container`), so a step
    left with none is never one whose leaves were all dropped.
    """
    card = cards[number]
    if is_container(card, cards, shipped):
        return (not card.is_open and card.touched_by_hand) or all(
            dropped(leaf, cards, shipped) for leaf in leaves(card, cards, shipped))
    return _closed_dropped(card) and not (is_work(card, cards, shipped) and number in shipped)


def _above(card: Card, cards: Mapping[int, Card]) -> Iterator[Card]:
    """The steps above ``card``, nearest first: the step it splits, that step's, ..."""
    while card.parent is not None:
        card = cards[card.parent]
        yield card


def missing(cards: Mapping[int, Card]) -> set[int]:
    """Cards the decisions below would read that ``cards`` does not hold yet:
    blockers, parents, and every step linked under a card, each read to decide whether
    it is a leaf (:func:`leaves`; the findings and rulings a card owns decide
    nothing)."""
    wanted = set()
    for card in cards.values():
        wanted.update(card.blocked_by)
        wanted.update(card.step_children)
        if card.parent is not None:
            wanted.add(card.parent)
    return wanted - set(cards)


def resolved(number: int, cards: Mapping[int, Card], shipped: Iterable[int]) -> bool:
    """Whether a card is done with: dropped, shipped work, a container whose leaves are,
    or a closed ruling or question."""
    shipped = set(shipped)
    card = cards[number]
    if card.kind not in SHIPPABLE:
        return not card.is_open
    if dropped(number, cards, shipped):
        return True
    if is_container(card, cards, shipped):
        return all(resolved(leaf, cards, shipped) for leaf in leaves(card, cards, shipped))
    return number in shipped


def dropped_above(card: Card, cards: Mapping[int, Card], shipped: Iterable[int]) -> Card | None:
    """The nearest step above ``card`` that was dropped (R-BAL185); None when none was."""
    shipped = set(shipped)
    return next((step for step in _above(card, cards) if dropped(step.number, cards, shipped)),
                None)


def is_live(number: int, cards: Mapping[int, Card], shipped: Iterable[int]) -> bool:
    """Whether a card is still work to do: not resolved, and no step above it dropped.

    The owner a finding needs (``conventions.md`` rule 1, R-BAL177), and the
    step a new leaf may split.
    """
    return (not resolved(number, cards, shipped)
            and dropped_above(cards[number], cards, shipped) is None)


def leaves_below(card: Card, cards: Mapping[int, Card], shipped: Iterable[int]) -> list[Card]:
    """Every leaf below ``card`` that is work, in any state -- under the steps it splits,
    under theirs, ... (a step split again is not a leaf; its own leaves are), each level in
    card order.  ``cards`` holds them all (:func:`missing`)."""
    below, wanted = [], sorted(leaves(card, cards, shipped))
    while wanted:
        below += [cards[number] for number in wanted if is_work(cards[number], cards, shipped)]
        wanted = sorted(number for step in wanted for number in leaves(cards[step], cards,
                                                                          shipped))
    return below


def left_bare(card: Card, cards: Mapping[int, Card], shipped: Iterable[int],
              ending: set[int]) -> list[Card]:
    """The steps at or below ``card`` whose every leaf is in ``ending`` -- leaves
    whose filing has not finished, dropped now.  Closed while marked, those were never
    leaves (:func:`leaves`), so each such step is a plain step again: no leaf carries its
    drop (R-BAL190), so it carries its own.  One a PERSON closed keeps their close; one
    the tool closed (its display of git, or of its leaves) is closed again, as dropped.
    No two are nested: a step split again is a leaf of the
    one above it, and ``ending`` holds work only (:func:`leaves_below`), never a step
    split again.  ``cards`` holds them all (:func:`missing`)."""
    bare, wanted = [], [card]
    while wanted:
        bare += [step for step in wanted if (step.is_open or not step.touched_by_hand)
                 and (split := leaves(step, cards, shipped)) and set(split) <= ending]
        wanted = [cards[number] for step in wanted for number in leaves(step, cards, shipped)
                  if is_container(cards[number], cards, shipped)]
    return bare


def never_offered(card: Card, cards: Mapping[int, Card], shipped: Iterable[int]) -> str | None:
    """Why a piece of work is never offered, whatever its own blockers: its filing not
    finished (R-BAL202), a link of its own outside the tracker, a blocker outside the tracker on a
    step above it (R-BAL188), or a step above it dropped (R-BAL185); None when none holds
    it back."""
    shipped = set(shipped)
    if filing_unfinished(card):
        return ("its filing has not finished (R-BAL202): unless a `plan file` command is "
                "filing it now, the same command run again finishes it")
    if card.outside:
        links = ", ".join(f"its {link.what} {link.issue}" for link in card.outside)
        return f"it links {links}, outside the tracker (R-BAL188)"
    above = list(_above(card, cards))
    for step in above:
        for link in step.outside:
            if link.what == "blocker":
                return (f"plan#{step.number} above it waits on {link.issue}, outside the "
                        "tracker (R-BAL188)")
    gone = next((step for step in above if dropped(step.number, cards, shipped)), None)
    return f"plan#{gone.number} above it was dropped (R-BAL185)" if gone else None


def workable(card: Card, cards: Mapping[int, Card], shipped: Iterable[int],
             claims: Mapping[int, Claim]) -> bool:
    """Whether a card is a step someone could start now: open unshipped unclaimed work that
    nothing holds back (:func:`never_offered`), and every blocker of it and of each step
    above it resolved (R-BAL182)."""
    shipped = set(shipped)
    return (
        card.is_open
        and card.kind == "step"
        and is_work(card, cards, shipped)
        and never_offered(card, cards, shipped) is None
        and card.number not in shipped
        and card.number not in claims
        and all(resolved(blocker, cards, shipped)
                for step in (card, *_above(card, cards)) for blocker in step.blocked_by)
    )


def outside_reports(cards: Mapping[int, Card]) -> list[str]:
    """Each link an open card has to an issue outside the tracker, as a report line
    (R-BAL188): the card is not offered until someone removes it."""
    return [
        f"plan#{card.number}'s {link.what} is {link.issue}, outside the tracker: "
        f"plan#{card.number} is not offered until that link is removed (R-BAL188)"
        for card in sorted(cards.values(), key=lambda card: card.number) if card.is_open
        for link in card.outside
    ]


def unfinished_reports(cards: Mapping[int, Card], shipped: Iterable[int]) -> list[str]:
    """Each card in ``cards`` whose filing has not finished (:func:`filing_unfinished`), as
    a report line: it is never offered until the same command finishes it, or, while it is
    open and git does not say it shipped, ``plan drop`` drops it -- a leaf unlinked from
    its split step first (R-BAL205), which the line says; a closed ruling is withdrawn by
    reopening it and closing it as not planned (R-BAL206); and ``plan show`` says how to
    finish it by hand."""
    shipped = set(shipped)
    return [
        f"plan#{card.number}'s filing has not finished, so it is never offered (R-BAL202): "
        "unless a `plan file` command is filing it now, run that command again to finish it"
        + ("" if card.number in shipped else ", or `plan drop` it" if card.is_open else
           ", or, to withdraw it, reopen it and close it as not planned on the web")
        + (f" (which unlinks it from plan#{card.parent} first: it was never part of that split, "
           "R-BAL205)" if card.is_open and is_work(card, cards, shipped) and card.kind == "step"
           and card.parent and card.number not in shipped else "")
        + f"; with that command lost, `plan show plan#{card.number}` says how to finish it"
        for card in sorted(cards.values(), key=lambda card: card.number)
        if filing_unfinished(card)
    ]


@dataclass(frozen=True)
class NextAnswer:
    """The first workable step in the board's order, and what the order cannot place."""

    card: Card | None
    unplaced: tuple[Card, ...]


def next_step(order: Iterable[int], cards: Mapping[int, Card], shipped: Iterable[int],
              claims: Mapping[int, Claim], arc: str | None = None) -> NextAnswer:
    """The first workable step in ``order`` (the board's card numbers, top first).

    A workable step that is NOT on the board -- filed by hand on the web, or
    just filed and not yet shown by a lagging board -- has no place in the
    order, so it is never chosen and never skipped silently: it is returned in
    ``unplaced`` for the caller to say so.
    """
    shipped = set(shipped)

    def fits(card: Card) -> bool:
        return workable(card, cards, shipped, claims) and (arc is None or arc in card.labels)

    first = next((cards[n] for n in order if n in cards and fits(cards[n])), None)
    placed = set(order)
    unplaced = tuple(sorted(
        (card for card in cards.values() if card.number not in placed and fits(card)),
        key=lambda card: card.number,
    ))
    return NextAnswer(first, unplaced)


@dataclass(frozen=True)
class Placement:
    """Where a leaf goes on the board (R-BAL179).

    ``move``: whether it is moved; when not, it stays where it is, or where
    GitHub adds it, at the bottom.  ``after``: the board item it goes just after
    when it is moved, None for the top.  ``remove``: the split step's own item,
    which leaves the board as the step becomes a container.
    """

    move: bool
    after: str | None
    remove: str | None
    note: str


def leaf_placement(order: Iterable[tuple[int, str]], parent: Card, leaf: int,
                   cards: Mapping[int, Card], shipped: Iterable[int]) -> Placement:
    """Where leaf ``leaf`` of ``parent`` goes, given the board's ``order``
    (``(card number, item id)``, top first), and ``cards`` and ``shipped`` to read
    ``parent``'s leaves (:func:`leaves`).

    In the split step's place while it is on the board; else just after the
    lowest of the leaves filed before it; else just above the highest of the
    leaves filed after it (a leaf whose link failed was no leaf yet, so a later
    leaf could take the split step's place before its filing finished); else
    nowhere new: where it is, or where GitHub adds it, at the bottom.  Filing
    order is the cards' numbers, never the sub-issue list's, which a person may
    drag.  No leaf it is placed by has an unfinished filing: a leaf is never
    filed while another of its split step's is unfinished (R-BAL204).  One
    dropped before its filing finished is no leaf, so nothing is placed by it,
    wherever a failed move left it.
    """
    order = [(number, item) for number, item in order if number != leaf]
    positions = [item for _, item in order]
    items = dict(order)
    if parent.board_item is not None:
        return Placement(True, parent.board_item, parent.board_item,
                         f"into plan#{parent.number}'s place")
    anchors = [number for number in leaves(parent, cards, shipped) if number in items]
    earlier = [items[number] for number in anchors if number < leaf]
    if earlier:
        return Placement(True, max(earlier, key=positions.index), None,
                         f"to just after the leaves of plan#{parent.number} filed before it")
    later = [items[number] for number in anchors if number > leaf]
    if later:
        first = positions.index(min(later, key=positions.index))
        return Placement(True, positions[first - 1] if first else None, None,
                         f"to just above the leaves of plan#{parent.number} filed after it")
    return Placement(False, None, None,
                     f"no other leaf of plan#{parent.number} is on the board to place it by")


@dataclass(frozen=True)
class Unsplit:
    """R-BAL205's undo of a split that leaves whose filing never finished began.

    ``step``: the card they are linked under.  ``unlink``: the leaves taken out of
    it.  ``add``: whether ``step`` is put back on the board first, where GitHub adds
    it, at the bottom.  ``after``: the leaf whose place on the board ``step`` then
    takes, and that leaf's board item, which ``step`` is moved just after; None when
    it is not moved.
    """

    step: int
    unlink: tuple[int, ...]
    add: bool
    after: tuple[int, str] | None


def unsplit(step: Card, out: Iterable[int], cards: Mapping[int, Card], shipped: Iterable[int],
            board: Iterable[tuple[int, str]] | None) -> Unsplit:
    """Take the leaves ``out`` out of the split of ``step`` (R-BAL205): each was never part of
    it, and is unlinked last, so the same command run again still sees the link and
    finishes every write before it.

    ``board`` is the board's order (``(card number, item id)``, top first) when ``step``
    will be open once the caller's other writes land, and None when it will not -- one a
    person closed, or one the caller closes or drops -- which never goes back on it.
    When no leaf of ``step`` is left without them (:func:`leaves`), a STEP that will be
    open is a plain step again, offered as work only from the board, in the place its
    split began: just after the one of ``out`` highest on the board.  So it is put on the
    board when it is off it, and moved there; and when it is on it already but below that
    leaf and not right after it -- a run whose move failed, or whose add lost its answer,
    left it at the bottom -- it is moved there too.  One above that leaf stays: it never
    left its place (a leaf's own failed move leaves the leaf below it), or a person put it
    there.  With none of ``out`` on the board, an added step stays where GitHub adds it.
    ``cards`` holds ``step`` and every card linked under it (:func:`missing`)."""
    out = tuple(sorted(out))
    order = list(board or ())
    positions = [number for number, _ in order]
    items = dict(order)
    plain = (board is not None and step.kind == "step"
             and set(leaves(step, cards, shipped)) <= set(out))
    anchor = next(((number, items[number]) for number in positions if number in out), None)
    add = plain and step.board_item is None
    below = (step.board_item is not None and step.number in positions and anchor is not None
             and positions.index(step.number) > positions.index(anchor[0]) + 1)
    return Unsplit(step.number, out, add, anchor if plain and (add or below) else None)


def shown_write(card: Card, cards: Mapping[int, Card], shipped: Iterable[int]) -> str | None:
    """The state write that makes the tool's display of ``card`` what git and its leaves
    say -- ``"reopen"``, or a close as ``"completed"`` or as ``"not_planned"`` -- or None when
    it shows that already.  A step split into smaller steps shows its leaves
    (:func:`container_shown`, R-BAL190); a piece of work the tool showed shipped that git
    does not say shipped is reopened (:func:`unshipped_shown_done`).  Open work git says
    shipped is closed only by its claim (``_sync_shipped``), so this never decides it,
    and no caller passes a card a person last closed or reopened, which holds their
    decision: ``sync`` reports what it must (``_sync_container``), and ``plan drop`` writes
    only a card the tool closed (:func:`drop_shows`).  ``sync`` writes it
    (:func:`sync_plan`), and so does ``plan drop`` for the card it takes a leaf out of."""
    shipped = set(shipped)
    if is_container(card, cards, shipped):
        wanted = container_shown(card, cards, shipped)
        if (None if card.is_open else card.state_reason) == wanted:
            return None
        return {None: "reopen", "NOT_PLANNED": "not_planned", "COMPLETED": "completed"}[wanted]
    return "reopen" if unshipped_shown_done(card, shipped) else None


def drop_shows(split: Card, leaf: Card, cards: Mapping[int, Card],
               shipped: Iterable[int]) -> str | None:
    """The write ``plan drop`` makes to ``split``'s state before taking ``leaf`` -- a leaf of
    it whose filing never finished -- out of its split (C2 review LOW 6): the one ``sync``
    would make (:func:`shown_write`) to ``split`` as the drop leaves it, with ``leaf``
    closed as not planned by the tool and still marked, so no leaf of it (:func:`leaves`).

    Only for a card the TOOL closed: its state is the tool's display, and once the leaf
    is unlinked, no card ``sync`` reads may lead to it again (unless one waits on it,
    names it in a trailer or claims it), so the drop writes it.  Left a plain step (or a
    finding a person linked the leaf under), it is reopened when it is shown done though
    git never shipped it, and a plain step the tool closed as not planned (a drop) stays
    dropped.  Still split by other leaves, it shows them: reopened while one is still
    work, or closed again as they say.  An open card is read by every ``sync``, which
    shows it; one a PERSON closed keeps their close (R-BAL185).  None: no write."""
    if not split.closed_by_tool:
        return None
    gone = replace(leaf, is_open=False, state_reason="NOT_PLANNED", closed_by_tool=True,
                   touched_by_hand=False)
    return shown_write(split, {**cards, leaf.number: gone}, shipped)


def drop_unlinks(card: Card, cards: Mapping[int, Card], shipped: Iterable[int],
                 ending: set[int]) -> list[Unsplit]:
    """What a drop of ``card`` unlinks (R-BAL205), so no link it leaves behind could ever
    decide anything: under ``card`` and every step below it, each step linked there that is
    no leaf once the drop has closed the unfinished filings in ``ending`` -- one of those,
    or one already closed while still being filed (:func:`never_split`) -- and below each
    of those too.  Unlinks only: no step they are linked under goes back on the board
    (each is dropped, keeps a leaf, or shipped).  A card that is not a
    step splits nothing: nothing is unlinked from it, nor from what hangs under it, which
    its drop does not close.  ``cards`` holds them all (:func:`missing`)."""
    undos, wanted = [], [card] if card.kind == "step" else []
    while wanted:
        step = wanted.pop(0)
        stale = set(never_split(step, cards, shipped)) | ending
        out = [number for number in step.step_children if number in stale]
        if out:
            undos.append(unsplit(step, out, cards, shipped, None))
        wanted += [cards[number] for number in step.step_children]
    return undos


def release_flag(claim: Claim) -> str:
    """The ``release`` option naming ``claim``'s holder, read or not."""
    return "--unreadable" if claim.branch is None else f"--branch {claim.branch}"


def release_hint(claim: Claim) -> str:
    """The command that releases ``claim``, whether or not its branch could be read."""
    return f"`plan release plan#{claim.card} {release_flag(claim)}`"


def holder(claim: Claim) -> str:
    """Who holds ``claim``: its branch, quoted, or that its branch cannot be read."""
    return repr(claim.branch) if claim.branch is not None else "a branch that cannot be read"


def stale_claims(claims: Mapping[int, Claim], now: datetime,
                 pushed: Callable[[str], bool]) -> list[Claim]:
    """Claims older than :data:`STALE_CLAIM` whose branch ``origin`` does not hold.

    A claim whose commit could not be read has no date and no branch, so it
    counts as old and unpushed: it is reported rather than trusted.
    """
    return [
        claim for claim in claims.values()
        if (not claim.made or now - datetime.fromisoformat(claim.made) > STALE_CLAIM)
        and (claim.branch is None or not pushed(claim.branch))
    ]


@dataclass
class SyncPlan:
    """What ``sync`` would write -- ``close`` as completed, ``drop`` (close as not
    planned), ``reopen``, ``release`` a claim; what it found and must leave to a
    person (``reports``); and what ``dev``'s history says wrongly that no tracker
    write can change (``history``, R-BAL184).  Its undo of a split is
    :func:`sync_unsplits`."""

    close: list[int] = field(default_factory=list)
    drop: list[int] = field(default_factory=list)
    reopen: list[int] = field(default_factory=list)
    release: list[int] = field(default_factory=list)
    reports: list[str] = field(default_factory=list)
    history: list[str] = field(default_factory=list)


def _sync_shipped(card: Card, plan: SyncPlan, claims: Mapping[int, Claim],
                  ship_branches: Mapping[int, set[str]]) -> None:
    """An open card git says shipped: close it if its claim names a branch that shipped it."""
    if card.touched_by_hand:
        plan.reports.append(
            f"plan#{card.number} shipped in git but a person reopened it: close it by hand, "
            f"or ship a commit with 'Reopens: plan#{card.number}'"
        )
        return
    claim = claims.get(card.number)
    branches = ship_branches.get(card.number, set())
    if claim is not None and claim.branch in branches:
        plan.close.append(card.number)
        plan.release.append(card.number)
        return
    named = f"its claim names {holder(claim)}" if claim else "it has no claim"
    plan.reports.append(
        f"plan#{card.number} shipped in git from {sorted(branches) or 'no pull request'}, "
        f"but {named}: not closed (a mistyped number must not close another's card)"
    )


def container_shown(card: Card, cards: Mapping[int, Card], shipped: Iterable[int]) -> str | None:
    """The state the tool shows a container in, its display of its leaves (R-BAL190): open
    (None) while one is still work, closed as not planned (``NOT_PLANNED``) once all were
    dropped (R-BAL187), else closed as completed (``COMPLETED``).  ``sync`` writes it
    (:func:`shown_write`)."""
    shipped = set(shipped)
    if not resolved(card.number, cards, shipped):
        return None
    return "NOT_PLANNED" if dropped(card.number, cards, shipped) else "COMPLETED"


def _sync_container(card: Card, plan: SyncPlan, cards: Mapping[int, Card],
                    shipped: set[int]) -> None:
    """A container's state shows its leaves (R-BAL190): open while one is still
    work, closed as not planned once all were dropped (R-BAL187), else closed as
    completed -- reopened, closed or re-closed as they change.

    A person's close is a drop, so the one state a person leaves that its leaves
    contradict is a container reopened by hand while its leaves are all done.
    """
    wanted = container_shown(card, cards, shipped)
    if card.touched_by_hand:
        if wanted is not None and card.is_open:
            plan.reports.append(
                f"container plan#{card.number} was reopened by a person's hand, while its "
                "leaves are all done: to put work back under it, reopen by hand a leaf that "
                "was dropped or that a person closed, and ship 'Reopens: plan#N' for one that "
                "shipped; to leave it, close it by hand, which records it dropped (R-BAL190)"
            )
        return
    _record(plan, card.number, shown_write(card, cards, shipped))


def _record(plan: SyncPlan, number: int, write: str | None) -> None:
    """Put :func:`shown_write`'s write for card ``number`` in ``plan``."""
    if write is not None:
        {"reopen": plan.reopen, "completed": plan.close, "not_planned": plan.drop}[write].append(
            number)


def _claimed_split(card: Card, claim: Claim, cards: Mapping[int, Card], shipped: set[int]) -> str:
    """The REPORT for ``claim`` on ``card``, a step split into smaller steps: release the
    claim -- or, when every leaf of ``card`` is still being filed and not shipped, drop
    each of them, which keeps it whole (R-BAL205).  (``plan file``'s refusal under a claim
    names that drop for its own leaf only, when no other leaf splits the step.)"""
    split = leaves(card, cards, shipped)
    filing = [number for number in split
              if filing_unfinished(cards[number]) and number not in shipped]
    keep = ("" if len(filing) < len(split) else
            "; or, to keep it whole, " + " and ".join(f"`plan drop plan#{number}`"
                                                      for number in filing)
            + ": still being filed, so never part of the split (R-BAL205)")
    return (f"plan#{card.number} is split into smaller steps, but {holder(claim)} still claims "
            "it: a split step is never shipped itself, its smaller steps are the work "
            f"(R-BAL177), so release the claim with {release_hint(claim)}" + keep)


def sync_plan(cards: Mapping[int, Card], shipped: Iterable[int], claims: Mapping[int, Claim],
              ship_branches: Mapping[int, set[str]]) -> SyncPlan:
    """What ``sync`` writes so each step and finding shows git's answer.

    ``ship_branches``: for each shipped card, the head branches of the pull
    requests into ``dev`` that merged its standing ``Ships:`` commits.

    A claim on a step split into smaller steps is reported, never released: a
    branch building that step whole would ship a ``Ships:`` that names a split
    step, which no commit ships.  ``plan file`` refuses a leaf under a claimed
    step (C2 review M4), but some claims it cannot see (its docstring names them)
    still land on one.  ``cards`` holds every claimed card, so each such claim is
    seen (:func:`_claimed_split`).
    """
    shipped = set(shipped)
    plan = SyncPlan(reports=outside_reports(cards) + unfinished_reports(cards, shipped))
    for card in sorted(cards.values(), key=lambda card: card.number):
        if card.kind == "ruling" and card.is_open and not filing_unfinished(card):
            plan.reports.append(
                f"ruling plan#{card.number} is open, though a ruling is a record its filing "
                "closes: close it by hand"
            )
        container = is_container(card, cards, shipped)
        if card.number in shipped and not is_work(card, cards, shipped):
            plan.history.append(
                f"a Ships trailer names plan#{card.number}, a "
                f"{'container' if container else card.kind or 'card with no type'}, "
                "which no commit ships: a mistyped or stale number? It is not acted on"
            )
        if container and (claim := claims.get(card.number)):
            plan.reports.append(_claimed_split(card, claim, cards, shipped))
        if card.kind not in SHIPPABLE:
            continue
        if container:
            _sync_container(card, plan, cards, shipped)
        elif card.number in shipped and card.is_open:
            _sync_shipped(card, plan, claims, ship_branches)
        else:
            _record(plan, card.number, shown_write(card, cards, shipped))
        if card.kind == "step" and card.is_open and (gone := dropped_above(card, cards,
                                                                            shipped)):
            plan.reports.append(
                f"plan#{card.number} is open under plan#{gone.number}, which was dropped, "
                "so it is never offered (R-BAL185): drop it, or give it a live parent"
            )
        if card.kind == "finding" and card.is_open and card.number not in shipped and (
                lost := _owner_lost(card, cards, shipped)):
            plan.reports.append(f"finding plan#{card.number}'s owner {lost}: give it an open "
                                "step as its owner (R-BAL177)")
    return plan


def sync_unsplits(cards: Mapping[int, Card], shipped: Iterable[int], plan: SyncPlan,
                  order: Iterable[tuple[int, str]]) -> list[Unsplit]:
    """R-BAL205's undo of every split in ``cards`` that a step closed while still being
    filed is linked under (:func:`never_split`), each by :func:`unsplit`: what ``sync``
    writes after ``plan``'s state writes, so no such link stays.

    The link decides nothing while that step keeps its filing mark (:func:`leaves`),
    but the mark is all that makes it no leaf: a person who removed the stale label
    would make it a dropped leaf, and R-BAL187 would then drop a split step that was
    never split, built or shipped as a plain step or not (review M4).  Whether the
    split step will be open is read through ``plan``: open and not closed by it (as
    completed, or as not planned), or reopened by it.  Only a step's links are undone:
    a card that is not a step splits nothing, so its links decide nothing.

    Such a link is left by a person's close on the web, or by a command a failure cut
    short: the tool's own drops unlink what they close (:func:`drop_unlinks`).  ``sync``
    reads every card still marked, closed or open, and so every such leaf and the step
    above it (:func:`missing`): each link is undone at the next run, wherever its split
    step stands.  A person who removes the label, or reopens the leaf, before that run
    acts on the link as it stands.
    """
    shipped = set(shipped)
    order = list(order)
    undos = []
    for card in sorted(cards.values(), key=lambda card: card.number):
        if card.kind == "step" and (out := never_split(card, cards, shipped)):
            opened = ((card.is_open and card.number not in plan.close + plan.drop)
                      or card.number in plan.reopen)
            undos.append(unsplit(card, out, cards, shipped, order if opened else None))
    return undos


def _owner_lost(card: Card, cards: Mapping[int, Card], shipped: set[int]) -> str | None:
    """How an open finding lost its live owner; None while it has one -- or an owner
    outside the tracker, which the outside link's own report names (R-BAL188)."""
    if card.parent is not None:
        return (None if is_live(card.parent, cards, shipped) else
                f"plan#{card.parent} is no longer live (done, or under a dropped step)")
    return None if any(link.what == "parent" for link in card.outside) else "is missing"
