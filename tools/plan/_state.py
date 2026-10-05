"""What the plan says, decided from the cards, the board, the claims and git.

Nothing here reads GitHub or git: the caller hands over what it read, so every
decision the ``plan`` command makes is a pure function a test can drive.

**Work** is a step or finding that is not a container (:func:`is_work`): the
only cards a branch builds and a commit ships.  ``claim``, ``next`` and
``sync`` all ask that one predicate.

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
- a container -- a step with step children (``R-BAL177``) -- holds no decision
  of the tool's (``R-BAL190``): it is resolved by its leaves, when every
  step child is, and dropped when every leaf was (``R-BAL187``: what waits on
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
name it until the same command finishes it, or ``plan drop`` drops it (a
dropped leaf counts toward its split step's drop, ``R-BAL187``, as any leaf
does).  While a filing is still running its card is named too: no read can
tell a filing running from one a failure cut short.

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
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from _tracker import Card, Claim
from setup_tracker import FILING

#: The build plan: "``plan next`` reports a claim older than 3 days with no pushed branch".
STALE_CLAIM = timedelta(days=3)
#: The kinds of card a commit ships; a ruling is a record and a question the
#: developer's, so ``sync`` never opens or closes either.
SHIPPABLE = ("step", "finding")


def is_work(card: Card) -> bool:
    """Whether a card is something a branch builds and a commit ships: a step or a
    finding, and not a container."""
    return card.kind in SHIPPABLE and not card.is_container


def filing_unfinished(card: Card) -> bool:
    """Whether ``card``'s filing has not finished (R-BAL202) -- one still running, or one a
    failure cut short: it still carries the filing mark, and is open, or is a ruling its
    own filing closed (the tool's close as completed, which comes before the last write
    removes the mark).  Any other card closed while marked -- a ruling included -- was
    dropped before its filing finished, by ``plan drop`` (closed as not planned) or by a
    person, so what its filing left undone is moot."""
    return FILING in card.labels and (card.is_open or (
        card.kind == "ruling" and card.closed_by_tool and card.state_reason == "COMPLETED"))


def _shown_shipped(card: Card) -> bool:
    """Closed by the tool as completed: the tool's display that git says it shipped,
    which follows git and decides nothing."""
    return not card.is_open and card.closed_by_tool and card.state_reason == "COMPLETED"


def _closed_dropped(card: Card) -> bool:
    """A card that is no container closed by a person (for any reason), or by the tool as
    not planned (``plan drop``)."""
    return not card.is_open and not _shown_shipped(card)


def dropped(number: int, cards: Mapping[int, Card], shipped: set[int]) -> bool:
    """Whether a card was dropped.

    A piece of work: closed as dropped, and not shipped (git's answer, whoever
    closed it).  A container: closed by a PERSON (R-BAL185), or every leaf
    dropped (R-BAL187) -- never its own close by the tool, which only shows its
    leaves (R-BAL190).
    """
    card = cards[number]
    if card.is_container:
        return (not card.is_open and card.touched_by_hand) or all(
            dropped(leaf, cards, shipped) for leaf in card.leaves)
    return _closed_dropped(card) and not (is_work(card) and number in shipped)


def _above(card: Card, cards: Mapping[int, Card]) -> Iterator[Card]:
    """The steps above ``card``, nearest first: the step it splits, that step's, ..."""
    while card.parent is not None:
        card = cards[card.parent]
        yield card


def missing(cards: Mapping[int, Card]) -> set[int]:
    """Cards the decisions below would read that ``cards`` does not hold yet:
    blockers, parents, and a container's leaves (its STEP children; the findings
    and rulings it owns decide nothing)."""
    wanted = set()
    for card in cards.values():
        wanted.update(card.blocked_by)
        wanted.update(card.leaves)
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
    if card.is_container:
        return all(resolved(leaf, cards, shipped) for leaf in card.leaves)
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
        and is_work(card)
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


def unfinished_reports(cards: Mapping[int, Card]) -> list[str]:
    """Each card in ``cards`` whose filing has not finished (:func:`filing_unfinished`), as
    a report line: it is never offered until the same command finishes it, or, while it is
    open, ``plan drop`` drops it -- which, for a leaf, counts toward its split step's drop
    like any leaf's (R-BAL187), so the line says so."""
    return [
        f"plan#{card.number}'s filing has not finished, so it is never offered (R-BAL202): "
        "unless a `plan file` command is filing it now, run that command again to finish it"
        + (", or `plan drop` it" if card.is_open else "")
        + (f" (if it is plan#{card.parent}'s last leaf still work, that drops plan#{card.parent} "
           "too, R-BAL187)" if card.is_open and card.kind == "step" and card.parent else "")
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


def leaf_placement(order: Iterable[tuple[int, str]], parent: Card, leaf: int) -> Placement:
    """Where leaf ``leaf`` of ``parent`` goes, given the board's ``order``
    (``(card number, item id)``, top first).

    In the split step's place while it is on the board; else just after the
    lowest of the leaves filed before it; else just above the highest of the
    leaves filed after it (a leaf whose link failed was no leaf yet, so a later
    leaf could take the split step's place before its filing finished); else
    nowhere new: where it is, or where GitHub adds it, at the bottom.  Filing
    order is the cards' numbers, never the sub-issue list's, which a person may
    drag.  Every other leaf it is placed by is one whose filing finished: a leaf
    is never filed while another of its split step's is unfinished (R-BAL204).
    """
    order = [(number, item) for number, item in order if number != leaf]
    positions = [item for _, item in order]
    items = dict(order)
    if parent.board_item is not None:
        return Placement(True, parent.board_item, parent.board_item,
                         f"into plan#{parent.number}'s place")
    anchors = [number for number in parent.leaves if number in items]
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
    write can change (``history``, R-BAL184)."""

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
    named = f"its claim names {claim.branch!r}" if claim else "it has no claim"
    plan.reports.append(
        f"plan#{card.number} shipped in git from {sorted(branches) or 'no pull request'}, "
        f"but {named}: not closed (a mistyped number must not close another's card)"
    )


def _sync_container(card: Card, plan: SyncPlan, cards: Mapping[int, Card],
                    shipped: set[int]) -> None:
    """A container's state shows its leaves (R-BAL190): open while one is still
    work, closed as not planned once all were dropped (R-BAL187), else closed as
    completed -- reopened, closed or re-closed as they change.

    A person's close is a drop, so the one state a person leaves that its leaves
    contradict is a container reopened by hand while its leaves are all done.
    """
    done = resolved(card.number, cards, shipped)
    if card.touched_by_hand:
        if done and card.is_open:
            plan.reports.append(
                f"container plan#{card.number} was reopened by a person's hand, while its "
                "leaves are all done: to put work back under it, reopen by hand a leaf that "
                "was dropped or that a person closed, and ship 'Reopens: plan#N' for one that "
                "shipped; to leave it, close it by hand, which records it dropped (R-BAL190)"
            )
        return
    shown = None if card.is_open else card.state_reason
    if not done:
        wanted, writes = None, plan.reopen
    elif dropped(card.number, cards, shipped):
        wanted, writes = "NOT_PLANNED", plan.drop
    else:
        wanted, writes = "COMPLETED", plan.close
    if shown != wanted:
        writes.append(card.number)


def sync_plan(cards: Mapping[int, Card], shipped: Iterable[int], claims: Mapping[int, Claim],
              ship_branches: Mapping[int, set[str]]) -> SyncPlan:
    """What ``sync`` writes so each step and finding shows git's answer.

    ``ship_branches``: for each shipped card, the head branches of the pull
    requests into ``dev`` that merged its standing ``Ships:`` commits.
    """
    shipped = set(shipped)
    plan = SyncPlan(reports=outside_reports(cards) + unfinished_reports(cards))
    for card in sorted(cards.values(), key=lambda card: card.number):
        if card.kind == "ruling" and card.is_open and not filing_unfinished(card):
            plan.reports.append(
                f"ruling plan#{card.number} is open, though a ruling is a record its filing "
                "closes: close it by hand"
            )
        if card.number in shipped and not is_work(card):
            plan.history.append(
                f"a Ships trailer names plan#{card.number}, a "
                f"{'container' if card.is_container else card.kind or 'card with no type'}, "
                "which no commit ships: a mistyped or stale number? It is not acted on"
            )
        if card.kind not in SHIPPABLE:
            continue
        if card.is_container:
            _sync_container(card, plan, cards, shipped)
        elif card.number in shipped and card.is_open:
            _sync_shipped(card, plan, claims, ship_branches)
        elif card.number not in shipped and _shown_shipped(card):
            plan.reopen.append(card.number)
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


def _owner_lost(card: Card, cards: Mapping[int, Card], shipped: set[int]) -> str | None:
    """How an open finding lost its live owner; None while it has one -- or an owner
    outside the tracker, which the outside link's own report names (R-BAL188)."""
    if card.parent is not None:
        return (None if is_live(card.parent, cards, shipped) else
                f"plan#{card.parent} is no longer live (done, or under a dropped step)")
    return None if any(link.what == "parent" for link in card.outside) else "is missing"
