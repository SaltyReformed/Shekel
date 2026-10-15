"""Which cards a release carries after the cutover, whether each moves money, and which
deploy-together groups each is under: the release tool's card reader (step X-cx's
L9a-1b, T-a a1; rulings ``balance:R-BAL287`` - ``R-BAL289``).

**What a release carries is git's answer**: every card a ``Ships: plan#N`` or a
``Reopens: plan#N`` trailer on a commit in ``<base>..<cut>`` names, read by the one
trailer walk (:func:`tools.ci.trailers.history`).  An undo is carried as a ship is and
judged by the same rules, since it changes what production runs as much (R-BAL288).
Whether a card is in production after a release is git's too: shipped at the cut it is
judged at, by the one rule the tracker tool and CI ask (:func:`tools.ci.trailers.shipped`,
ruling ``R-BAL181``); one the window names that is not is UNDONE.

**A card moves money when it carries the moves-money label**: its OWN label, as the
tracker holds it when the release is judged, split or not (the coordinator, under
R-BAL207, 2026-10-14).  A label is no sentence a tick rewrites, so a card keeps its flag
after it ships (the registries' ``recurrence:R20`` shape, which the pre-cutover reader
reads around, has no counterpart here).  A step split into leaves since its trailer
landed is judged as the work it was, by its own label: a split writes the step only its
sub-issue link, so its labels stay (``tools/quill/test_quill_file.py``), and each leaf is
judged by its own when a trailer names it.  No card inherits a leaf's label: a label a
person moves by hand is an edit any card can take, split or not, and a step undone and
re-planned into a money leaf would count as a second money change beside that leaf,
refusing every release from then on (the full-leaf review of 2026-10-14, its E2).

**A group is a card labelled deploy-together**, and **after a release that touches a
group, production holds it wholly or not at all** (R-BAL288; the coordinator's C', under
R-BAL207, 2026-10-14; a group no release touches is not judged, so a leaf filed under a
group after it was released whole holds back only a release that touches it).  Its
MEMBERS, judged per cut (:func:`_member`), are the steps below it that production must
hold together: each leaf below it that can still ship, by quill's one leaf rule
(``leaves_below``: a leaf split again counting as its own leaves); each step below it
that production holds at the cut, whatever the plan now says of it, since its code is
in production and its siblings must not be there without it; and each step below
it that will never ship that production holds at the base, which the release keeps or
takes out.  A step that will never ship is dropped (quill's ``dropped``: a leaf closed as
dropped; a step re-planned into leaves that were all dropped; a split step a person
closed; a step left out of quill's leaves, closed while still being filed) or under a
step that was (``dropped_above``; R-BAL185: a person's close of a split step is a drop
its leaves inherit, never copied onto them).  So a step re-planned into leaves that can
still ship is a member only while production holds it at the cut (its leaves are members
throughout), and a finding is never one: a fix the group cannot ship without is a leaf.

"Below" is the group's links read DOWN, each step linked under it, under those, ...
(quill's closure, :func:`tools.quill.door.with_closure`, holds every one): the walk
quill's leaf rule reads, so a group's leaves are always among its steps.  Which groups a
release TOUCHES reads the same links UP (:func:`tools.quill.door.above`, quill's one
walk up the steps a card splits): each group a card it names is under, the card itself
or a step above it.  **Under every read this reader judges, the two walks give a group
the same steps, and no step hangs below it unread**: the read takes every card below
each group, of any kind, and judges nothing when a step hangs under a card that is no
step (:func:`_none_hung`: only a person's link or retype puts one there, since quill
links a step only under the step it splits), and then compares the two walks for each
group and judges nothing when they part (:func:`_one_walk`: only a link or a card's
type changed between the tracker's rounds of the read parts them), so no member is
dropped because a walk missed it (the coordinator, under R-BAL207, 2026-10-14).  A card
the release names under a group is a leaf, a step re-planned into leaves since its
trailer landed (quill splits no step ``dev`` says shipped, so it was undone first, or
split before its ship merged; a person can split one on the web), a step that will never
ship, a finding, or the group's own card (the coordinator, under R-BAL207, 2026-10-14).
A card the release names that will never ship touches nothing unless production holds
it, at the base or at the cut.  A release touching a group must leave production holding
every member or none: a release taking a whole group out is whole too.

**A step closed while still being filed is no member once quill's ``sync`` runs**:
``sync`` unlinks it (R-BAL205: it was never part of the split), so it is below no group,
and a release keeping its siblings without it would pass.  No card production holds
carries that mark by any tool's write: quill marks a card only when it creates it, and
X-cx's piece L9d refuses a ``Ships:`` naming a card that still carries the mark when it
lands ("still being filed: finish it with quill file, then land"); the cutover's freeze
waits on L9d, and ``dev`` carried no ``plan#`` trailer on 2026-10-14 (the coordinator's
fork 3 (a), under R-BAL207).  So this reader reads the links as the tracker holds them,
and no history of them.

**No refusal of a group is permanent.**  A refusal names each member the release lacks
(missing, or taken out by it) and, when a member will never ship, every member it would
keep; the report's line for the group names every member, carried, released, missing
and undone.  Two ways out stand:

- **Take the group out**: the same release adds a ``Reopens:`` for each member it would
  keep (the report's carried and released).  That leaves production holding none of the
  group, in ONE
  round by construction: every step below the group that production holds at a cut is a
  member, so after those ``Reopens:`` production holds no step below it at the new cut.
  quill's ``dropped`` and ``leaves`` read git's shipped set, so a ``Reopens:`` can turn
  a live step dead or a container back into a leaf; the new cut is judged with its own
  read, and no such change makes production hold a step below the group.  Where groups
  nest, the outer group's way out takes the inner one out too.  A ``Reopens:`` can name
  any card production holds: CI's trailer check reads its shape alone, and L9d's check
  reading the tracker must let one name a step production holds, a split one included,
  or this way out cannot land.
- **Ship it whole**: a member named as missing or undone that can still ship can ship,
  or ship back.

A member that will never ship leaves by the first way alone, whichever kind it is: no
plan ships it.  After that release production holds it on neither side, so no later
release counts it unless a trailer ships it again, and then the same way out stands.  No
card stands in for the leaves it was re-planned into: the delta review of 2026-10-14
built three histories in which such a stand-in, a member never back in production,
refused every later release.  The way out can meet the money rule's cap (a group holding
two money movers comes out in no one release): that is the developer's question, asked
at X-cx's piece L9d.

**What it cannot read refuses the release**, and nothing is declared after the cutover
(R-BAL287, R-BAL289):

- an old-form ``Ships: <arc>:<id>``: the cutover's freeze ends with a release, so
  production held every one before the registries went (R-BAL287), and one here was
  merged after the freeze or missed by it.  Only a cut holding ``steps.md`` can judge
  it, so the refusal names the newest one in the window whose history holds the claim,
  to release first with the release tool as that cut holds it (none, for a claim merged
  after the cutover);
- an old-form ``Reopens: <arc>:<id>``: it undoes a step that has no card, so no reader
  judges what it takes out of production;
- a trailer naming a card the tracker does not hold: R-BAL289 refuses one before it
  lands, by a trailer check reading the tracker (X-cx's piece L9d, built after this
  reader and before the cutover), so what reaches here is a card deleted after its
  trailer landed (until that check exists, a mistyped number too);
- a trailer naming a card of a kind no commit ships (a ruling, a question, a card of no
  type; quill's ``SHIPPABLE``): read as work it would pass as moving no money.  L9d's
  landing check refuses a trailer naming a card that is no work when it lands, so
  a mistyped one never reaches here;
- a value of neither form, which CI's trailer check refuses before it lands.

**The tracker is read once, at the release's cut** (:func:`read`), and every earlier cut
offered in its place is judged with that read (:func:`carried`): each lies on the cut's
first-parent line, so its window is part of the cut's and names no card the read does
not hold.  The plan's shapes (which steps are leaves, which will never ship) are read
with the cut's shipped set too, so an earlier cut is judged by the shapes the release's
cut gives its steps.
"""
from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from tools.ci import trailers
from tools.quill.door import (
    SHIPPABLE,
    Tracker,
    above,
    dropped,
    dropped_above,
    leaves_below,
    with_closure,
)
from tools.quill.setup_tracker import DEPLOY_TOGETHER_LABEL, MOVES_MONEY_LABEL
from tools.release._model import (
    Carried,
    Group,
    ReleaseError,
    Step,
    first_parent_line,
    steps_blob,
)


@dataclass(frozen=True)
class Cards:
    """What the release's cut knows of the cards its window names, read from the tracker
    once: whether each one that is work moves money; each one's line up (itself, the step
    it splits, that step's, ...); each deploy-together group the read holds that has a
    step below it, with every such step (those that can be its members, :func:`_member`),
    in card order; each such group's leaves by quill's rule, in any state; which of those
    steps and named cards will never ship (dropped, or under a step that was); and what
    each named card that is no work is."""

    money: Mapping[int, bool]
    lines: Mapping[int, tuple[int, ...]]
    groups: Mapping[int, tuple[int, ...]]
    leaves: Mapping[int, frozenset[int]]
    dropped: frozenset[int]
    no_work: Mapping[int, str]


def _what(kind: str | None) -> str:
    """What a card of ``kind``, a kind no commit ships, is, for a refusal."""
    return f"a {kind}" if kind else "a card of no type"


@cache
def _window(root: Path, base: str, cut: str) -> trailers.History:
    """The trailers on ``<base>..<cut>``, read once per range: the judge passes resolved
    shas, which name commits that never change."""
    return trailers.history(root, f"{base}..{cut}")


@cache
def _standing(root: Path, cut: str) -> frozenset[int]:
    """Every card shipped at ``cut``: what production runs once ``cut`` is released; read
    once per cut, as :func:`_window` is."""
    return frozenset(trailers.shipped(root, trailers.history(root, cut))[0])


def read(root: Path, base: str, cut: str, tracker: Tracker) -> Cards:
    """What ``cut`` knows of the release ``<base>..<cut>``: every card its trailers name,
    read by number, and every card a decision over them reads, read once.  ``base`` and
    ``cut`` are resolved shas: the window and the shipped sets are cached by them.

    Raises:
        ReleaseError: when a step hangs below a deploy-together group under a card that
            is no step (:func:`_none_hung`), or the group's links read down and its cards'
            lines read up give it different steps (:func:`_one_walk`).
        tools.quill.door.TrackerError: when a card one of them links to is not there.
        tools.quill.door.GitHubError: when GitHub answers a read with an error.
        requests.RequestException, OSError: when the tracker cannot be reached.
        tools.ci.gitcmd.GitError: when git cannot read the range.
    """
    named = {trailer.card for trailer in _window(root, base, cut).trailers}
    held = tracker.cards(named)
    closure = with_closure(tracker, dict(held))
    shipped = set(_standing(root, cut))
    work = {number for number, card in held.items() if card.kind in SHIPPABLE}
    up = {number: tuple(step.number for step in above(card, closure))
          for number, card in closure.items()}

    def never_ships(number: int) -> bool:
        return (dropped(number, closure, shipped)
                or dropped_above(closure[number], closure, shipped) is not None)

    def below(group: int) -> tuple[int, ...]:
        # Every step linked below the group, read down its links as quill's leaf rule is.
        found, wanted = [], list(closure[group].step_children)
        while wanted:
            found += wanted
            wanted = [number for step in wanted for number in closure[step].step_children]
        return tuple(sorted(found))
    groups = {group: below(group) for group, card in closure.items()
              if DEPLOY_TOGETHER_LABEL in card.labels}
    kinds = {number: card.kind for number, card in closure.items()}
    _none_hung(tracker, groups, {number: tuple((child.number, child.kind)
                                               for child in card.children)
                                 for number, card in closure.items()}, kinds)
    _one_walk(groups, up, kinds)
    groups = {group: steps for group, steps in groups.items() if steps}
    return Cards(
        {number: MOVES_MONEY_LABEL in held[number].labels for number in work},
        {number: (number, *up[number]) for number in work},
        groups,
        {group: frozenset(leaf.number for leaf in leaves_below(closure[group], closure,
                                                               shipped))
         for group in groups},
        frozenset(number for number in {*work, *(step for steps in groups.values()
                                                 for step in steps)}
                  if never_ships(number)),
        {number: _what(card.kind) for number, card in held.items() if number not in work})


def _one_walk(groups: Mapping[int, Collection[int]], lines: Mapping[int, tuple[int, ...]],
              kinds: Mapping[int, str | None]) -> None:
    """Refuse to read the release unless, for each group, the steps below it read DOWN its
    links (``groups``) are the steps whose line UP reaches it (``lines``, each card's steps
    above it, nearest first; ``kinds``, each card's kind): two reads of the same links,
    which agree for every link quill writes.  Once no step hangs under a card that is no
    step (:func:`_none_hung`, run first), they part only when a link or a card's type
    changed between the read's rounds; a member either walk misses would be dropped
    unseen, so nothing is judged.

    Raises:
        ReleaseError: naming each group whose walks part and each step one walk holds and
            the other does not.
    """
    parted = [said for group, down in sorted(groups.items())
              if (said := _parted(group, down, lines, kinds))]
    if parted:
        raise ReleaseError("; and ".join(parted))


def _parted(group: int, down: Collection[int], lines: Mapping[int, tuple[int, ...]],
            kinds: Mapping[int, str | None]) -> str | None:
    """How ``group``'s two walks part (:func:`_one_walk`); None when they agree."""
    up = {number for number, line in lines.items()
          if group in line and kinds[number] == "step"}
    down_only, up_only = sorted(set(down) - up), sorted(up - set(down))
    seen = ([f"{_keys(down_only)} below it by its links, which its own read does not place "
             "under it"] if down_only else [])
    seen += ([f"{_keys(up_only)} under it by its own read, which its links do not reach"]
             if up_only else [])
    return (f"the links under the deploy-together group plan#{group} disagree, so its "
            f"members cannot be read: {'; '.join(seen)} (the tracker changed while it was "
            "read: judge again)") if seen else None


def _none_hung(tracker: Tracker, groups: Mapping[int, Collection[int]],
               listed: Mapping[int, tuple[tuple[int, str | None], ...]],
               kinds: Mapping[int, str | None]) -> None:
    """Refuse to read the release when a step hangs below a deploy-together group under a
    card that is no step (a finding, a ruling, a question, a card of no type): quill links
    a step only under the step it splits, so only a person's link puts one there, or a
    person's change of a split step's type.  Neither walk of the read sees such a step
    (quill's closure holds a step's step sub-issues, never a finding's), so a member
    production holds could be dropped unseen.  Every card below each group, of any kind,
    is read for it: ``listed`` holds each card the read holds's sub-issues, as ``(number,
    kind)``, and ``kinds`` each one's kind; the rest are read from ``tracker`` here, in
    rounds, each card once.

    Raises:
        ReleaseError: naming each card a step hangs under, the steps and the groups above
            it; or a card listed below a group that the tracker does not hold.
    """
    listed, kinds = dict(listed), dict(kinds)
    hung: dict[int, tuple[set[int], set[int]]] = {}
    seen = frontier = {(group, card) for group, steps in groups.items()
                       for card in (group, *steps)}
    while frontier:
        for number, card in tracker.cards({card for _, card in frontier} - set(listed)).items():
            listed[number] = tuple((child.number, child.kind) for child in card.children)
            kinds[number] = card.kind
        later = set()
        for group, card in frontier:
            if card not in listed:
                raise ReleaseError(f"plan#{card} is listed below the deploy-together group "
                                   f"plan#{group} and the tracker does not hold it (the "
                                   "tracker changed while it was read: judge again)")
            for child, kind in listed[card]:
                if kind != "step":
                    later.add((group, child))
                elif card != group and kinds[card] != "step":
                    steps, above_it = hung.setdefault(card, (set(), set()))
                    steps.add(child)
                    above_it.add(group)
        frontier = later - seen
        seen = seen | later
    if hung:
        raise ReleaseError("a step hangs under a card that is no step, so the members of a "
                           "group above it cannot be read: " + "; ".join(
                               f"{_keys(sorted(steps))} under plan#{card}, "
                               f"{_what(kinds[card])}, below the deploy-together group "
                               f"{_keys(sorted(groups))} (move each under the step it "
                               f"splits, or give plan#{card} the step type if a person "
                               "changed it)"
                               for card, (steps, groups) in sorted(hung.items())))


def _keys(numbers: Collection[int]) -> str:
    """``numbers`` as the cards they are, for a message."""
    return ", ".join(f"plan#{number}" for number in numbers)


def _judgeable(root: Path, base: str, cut: str, claim: str) -> str | None:
    """The newest cut on ``dev``'s first-parent line in ``<base>..<cut>`` whose tree holds
    ``steps.md`` and whose history holds the commit ``claim``, so that releasing it
    carries the claim; None when none does (the claim was merged after the cutover)."""
    return next((sha for sha, _ in first_parent_line(root, base, cut)
                 if steps_blob(root, sha) is not None
                 and trailers.is_ancestor(root, claim, sha)), None)


def _unreadable(root: Path, base: str, cut: str, found: trailers.History,
                unread: Mapping[int, tuple[list[str], str | None]]) -> list[str]:
    """Each thing this reader cannot read: an old-form claim; an old-form ``Reopens:``; a
    card the tracker does not hold, or holds as no work; and a value of neither form."""
    lines = []
    for claim in found.old_form:
        judgeable = _judgeable(root, base, cut, claim.sha)
        first = (f"release {judgeable[:12]} first, with the release tool as that cut holds it"
                 if judgeable else "no cut on dev's line in this window holds steps.md and "
                 "this commit, so no release can judge it: it was merged after the cutover")
        lines.append(f"{claim.sha[:12]} claims `{claim.key}` in the old form, which only a cut "
                     "holding steps.md can judge (R-BAL287: the cutover's freeze ends with a "
                     f"release of every one): {first}")
    lines += [f"{undo.sha[:12]} carries `Reopens: {undo.key}` in the old form: it undoes a "
              "step that has no card, so no reader judges what it takes out of production "
              "(R-BAL287, R-BAL288)" for undo in found.old_form_reopens]
    for number, (shas, what) in sorted(unread.items()):
        named = f"plan#{number} (named at {', '.join(sha[:12] for sha in shas)})"
        lines.append(f"{named} is a card the tracker does not hold, so whether it moves "
                     "money cannot be read (R-BAL289)" if what is None else
                     f"{named} is {what}, which no commit ships, so it names no work whose "
                     "flag can be read")
    return lines + list(found.malformed)


def _counts(number: int, known: Cards, held: frozenset[int]) -> bool:
    """Whether a card the window names touches the groups above it, in a release whose
    production ``held`` on one side or the other: one that will never ship (``known``'s
    dropped) touches them only while production holds it, at the base or at the cut judged
    (kept by the release, carried in by it or taken out by it; judged per cut, so an
    offered cut that still ships it is held to its siblings)."""
    return number not in known.dropped or number in held


def _member(step: int, group: int, known: Cards, before: frozenset[int],
            after: frozenset[int]) -> bool:
    """Whether ``step``, a step below ``group``, is a member of it in a release whose
    production holds ``before`` at its base and ``after`` at the cut judged: a leaf of it
    that can still ship, any step production holds at the cut, or a step that will never
    ship that production holds at the base (the coordinator's C', under R-BAL207,
    2026-10-14)."""
    return (step in after or (step in known.dropped and step in before)
            or (step in known.leaves[group] and step not in known.dropped))


def _group(group: int, known: Cards, touching: Mapping[int, tuple[int, ...]],
           before: frozenset[int], after: frozenset[int]) -> Group:
    """One group a release touches: each of its members (:func:`_member`), sorted by
    whether the window names it and whether production holds it ``before`` the release
    (at its base) and ``after`` it (at the judged cut) -- carried in by it, released
    before it, taken out by it (undone), or held on neither side (missing) -- and apart,
    each member that will never ship; and each card the window names that touches the
    group and is no member of it, with the cards between (steps, or a finding it hangs
    under; the group's own card names itself).  ``touching`` maps each card the window
    names to the groups it touches (:func:`_counts`)."""
    members = tuple(step for step in known.groups[group]
                    if _member(step, group, known, before, after))

    def keys(test: Callable[[int], bool]) -> tuple[str, ...]:
        return tuple(f"plan#{member}" for member in members if test(member))
    return Group(f"plan#{group}",
                 carried=keys(lambda member: member in touching and member in after),
                 released=keys(lambda member: member not in touching and member in after),
                 missing=keys(lambda member: member not in before and member not in after),
                 undone=keys(lambda member: member in before and member not in after),
                 through=tuple(" under ".join(f"plan#{step}"
                                              for step in walk[:walk.index(group) or 1])
                               for number, walk in sorted(known.lines.items())
                               if group in touching.get(number, ())
                               and number not in members),
                 dead=keys(lambda member: member in known.dropped))


def carried(root: Path, base: str, cut: str, known: Cards) -> Carried:
    """What ``<base>..<cut>`` carries, judged with ``known``, the release's cut's
    :class:`Cards` (``cut`` is that cut or an earlier one on its first-parent line; both
    are resolved shas, as :func:`read` takes them).

    Raises:
        tools.ci.gitcmd.GitError: when git cannot read the range.
    """
    found = _window(root, base, cut)
    named: dict[int, list[str]] = {}
    for trailer in found.trailers:
        shas = named.setdefault(trailer.card, [])
        if trailer.sha not in shas:
            shas.append(trailer.sha)
    standing, before = _standing(root, cut), _standing(root, base)
    held = standing | before
    # The groups each named card touches, nearest first: none for one that does not count.
    touching = {number: tuple(step for step in known.lines.get(number, ())
                              if step in known.groups) if _counts(number, known, held) else ()
                for number in named}
    steps = tuple(
        Step(f"plan#{number}", tuple(shas), known.money[number],
             tuple(f"plan#{group}" for group in touching[number]),
             undone=number in before and number not in standing)
        for number, shas in sorted(named.items()) if number in known.money)
    groups = tuple(_group(group, known, touching, before, standing)
                   for group in sorted({group for each in touching.values() for group in each}))
    unread = {number: (shas, known.no_work.get(number)) for number, shas in named.items()
              if number not in known.money}
    return Carried(steps, groups, tuple(_unreadable(root, base, cut, found, unread)))
