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
- a container -- a step with step children (``R-BAL177``) -- is resolved only
  by its leaves, when every step child is: no commit ships a container, so a
  ``Ships:`` naming one is a mistyped or stale number, reported and never
  acted on.  One whose leaves were ALL dropped counts as dropped itself
  (``R-BAL187``): what waits on it is released, no leaf can be filed under it,
  and ``sync`` closes it as not planned, never as completed;
- a ruling or a question is never shipped by git (a ruling is a record, a
  question the developer's), so it is resolved once it is closed: a question
  when it is answered (its card becomes the ruling) or withdrawn.

**A leaf inherits every step above it** (``R-BAL182``, ``R-BAL185``): it is
workable only when its own blockers AND every blocker of every step above it
are resolved, and while no step above it was dropped.  The waits and the drop
are recorded once, on the step they were set on; nothing is copied.

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

#: The build plan: "``plan next`` reports a claim older than 3 days with no pushed branch".
STALE_CLAIM = timedelta(days=3)
#: The kinds of card a commit ships; a ruling is a record and a question the
#: developer's, so ``sync`` never opens or closes either.
SHIPPABLE = ("step", "finding")


def is_work(card: Card) -> bool:
    """Whether a card is something a branch builds and a commit ships: a step or a
    finding, and not a container."""
    return card.kind in SHIPPABLE and not card.is_container


def _shown_shipped(card: Card) -> bool:
    """Closed by the tool as completed: the tool's display that git says it shipped,
    which follows git and decides nothing."""
    return not card.is_open and card.closed_by_tool and card.state_reason == "COMPLETED"


def _closed_dropped(card: Card) -> bool:
    """Closed by a person (for any reason), or by the tool as not planned (``plan drop``,
    or ``sync`` showing a container whose leaves were all dropped)."""
    return not card.is_open and not _shown_shipped(card)


def _leaves(card: Card) -> list[int]:
    """The steps a container splits into (its findings and rulings decide nothing)."""
    return [child.number for child in card.children if child.kind == "step"]


def dropped(number: int, cards: Mapping[int, Card], shipped: set[int]) -> bool:
    """Whether a card was dropped: closed as dropped, or a container whose leaves were
    all dropped (R-BAL187) -- never a piece of work git says shipped, whoever closed it."""
    card = cards[number]
    if is_work(card) and number in shipped:
        return False
    return _closed_dropped(card) or (
        card.is_container and all(dropped(leaf, cards, shipped) for leaf in _leaves(card)))


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
        wanted.update(_leaves(card))
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
        return all(resolved(leaf, cards, shipped) for leaf in _leaves(card))
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


def workable(card: Card, cards: Mapping[int, Card], shipped: Iterable[int],
             claims: Mapping[int, Claim]) -> bool:
    """Whether a card is a step someone could start now: open unshipped unclaimed work
    with no link outside the tracker (R-BAL188), no step above it dropped, and every
    blocker of it and of each step above it resolved and inside the tracker
    (R-BAL182, R-BAL185)."""
    shipped = set(shipped)
    above = list(_above(card, cards))
    return (
        card.is_open
        and card.kind == "step"
        and is_work(card)
        and not card.outside
        and card.number not in shipped
        and card.number not in claims
        and not any(dropped(step.number, cards, shipped) for step in above)
        and not any(link.what == "blocker" for step in above for link in step.outside)
        and all(resolved(blocker, cards, shipped)
                for step in (card, *above) for blocker in step.blocked_by)
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
    """Where a new leaf goes on the board (R-BAL179).

    ``after``: the board item it goes just after; None leaves it where GitHub
    adds it, at the bottom.  ``remove``: the split step's own item, which leaves
    the board as the step becomes a container.
    """

    after: str | None
    remove: str | None
    note: str


def leaf_placement(order: Iterable[tuple[int, str]], parent: Card, leaf: int) -> Placement:
    """Where leaf ``leaf`` of ``parent`` goes, given the board's ``order``
    (``(card number, item id)``, top first): in the split step's place while it is
    on the board, else just after its other leaves, else at the bottom."""
    order = list(order)
    items = dict(order)
    if parent.board_item is not None:
        return Placement(parent.board_item, parent.board_item,
                         f"into plan#{parent.number}'s place")
    siblings = [items[child.number] for child in parent.children
                if child.kind == "step" and child.number in items and child.number != leaf]
    if siblings:
        positions = [item for _, item in order]
        return Placement(max(siblings, key=positions.index), None,
                         f"to just after plan#{parent.number}'s other leaves")
    return Placement(None, None, "at the bottom")


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
    """A container's state follows its leaves: open while one is still work, closed as
    not planned once all were dropped (R-BAL187), else closed as completed.

    A person's close is a drop, so the one state a person leaves that its leaves
    contradict is a container reopened by hand while its leaves are all done.
    """
    done = resolved(card.number, cards, shipped)
    if card.touched_by_hand:
        if done and card.is_open:
            plan.reports.append(
                f"container plan#{card.number} was reopened by a person's hand, while its "
                "leaves are all done: close it by hand, or file a new leaf under it"
            )
    elif not done:
        if _shown_shipped(card):
            plan.reopen.append(card.number)
    elif dropped(card.number, cards, shipped):
        if card.is_open or _shown_shipped(card):
            plan.drop.append(card.number)
    elif card.is_open:
        plan.close.append(card.number)


def sync_plan(cards: Mapping[int, Card], shipped: Iterable[int], claims: Mapping[int, Claim],
              ship_branches: Mapping[int, set[str]]) -> SyncPlan:
    """What ``sync`` writes so each step and finding shows git's answer.

    ``ship_branches``: for each shipped card, the head branches of the pull
    requests into ``dev`` that merged its standing ``Ships:`` commits.
    """
    shipped = set(shipped)
    plan = SyncPlan(reports=outside_reports(cards))
    for card in sorted(cards.values(), key=lambda card: card.number):
        if card.kind == "ruling" and card.is_open:
            plan.reports.append(
                f"ruling plan#{card.number} is open, though a ruling is a record closed when "
                "it is filed: finish it with the `plan file ruling` command that filed it, or "
                "close it by hand"
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
            card.parent is None or not is_live(card.parent, cards, shipped)
        ):
            owner = ("is missing" if card.parent is None else
                     f"plan#{card.parent} is no longer live (done, or under a dropped step)")
            plan.reports.append(f"finding plan#{card.number}'s owner {owner}: give it an open "
                                "step as its owner (R-BAL177)")
    return plan
