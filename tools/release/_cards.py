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

**A group is a card labelled deploy-together**, and its MEMBERS are the steps under it
that production must hold together: each leaf below it that can still ship, by quill's
one leaf rule (``leaves_below``: a leaf split again counting as its own leaves), and
each step below it that will never ship while production holds it before or after the
release judged.  A step that will never ship is dropped (quill's ``dropped``: a leaf
closed as dropped; a step re-planned into leaves that were all dropped; a split step a
person closed; a step left out of quill's leaves, closed while still being filed) or
under a step that was (``dropped_above``; R-BAL185: a person's close of a split step is a
drop its leaves inherit, never copied onto them).  **A step production holds is a member
until a release leaves production holding it on neither side**: its code is in
production, or the release takes it out, whatever the plan now says of it, and its
siblings must not stay there without it (the coordinator, under R-BAL207, 2026-10-14).
A step re-planned into leaves that can still ship is no member, nor is a finding: its
leaves are, and a fix the group cannot ship without is a leaf.  "Under" is quill's one
walk up the steps a card splits (:func:`tools.quill.door.above`; quill's closure,
:func:`tools.quill.door.with_closure`, holds every step below a card it holds), and the
same walk says which groups a release TOUCHES: each group a card it names is under, the
card itself or a step above it.  That card is a leaf, a step re-planned into leaves
since its trailer landed (quill splits no step ``dev`` says shipped, so it was undone
first, or split before its ship merged; a person can split one on the web), or a finding
(the coordinator, under R-BAL207, 2026-10-14).  A card the release names that will never
ship touches nothing unless production holds it, by the same rule.  A release touching a
group must leave production holding every member or none (R-BAL288): a release taking a
whole group out is whole too.

**No refusal of a group is permanent**, by the group rule alone.  A member a refusal
names as missing is held on neither side, so it is a leaf that can still ship: it stops
refusing once it ships, or once it will never ship (then no member).  A member it names
as undone, or one it names as kept, stops refusing once the same release takes the rest
of the group out too: a ``Reopens:`` can name any card production holds (CI's trailer
check reads its shape alone; L9d's check reading the tracker must let one name a step
production holds, a split one included, or that way out cannot land), and a release
leaving production holding no member is whole.  A member that can still ship can also
ship back.  One that will never ship is shipped back by no plan, so it stays a member
until such a release; after it production holds it on neither side, so no later release
counts it unless a trailer ships it again, and then the same way out stands.  That holds
for each kind above alike: what makes a step one that will never ship is the tracker's,
what keeps it a member is production's, and the way out reads production alone.  No
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
not hold.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
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
from tools.release._model import Carried, Group, Step, first_parent_line, steps_blob


@dataclass(frozen=True)
class Cards:
    """What the release's cut knows of the cards its window names, read from the tracker
    once: whether each one that is work moves money; each one's line up (itself, the step
    it splits, that step's, ...); each deploy-together group the read holds, with every
    step under it that can be a member, in card order (each leaf in any state, and each
    step that will never ship); which of those steps and named cards will never ship
    (dropped, or under a step that was); and what each named card that is no work is."""

    money: Mapping[int, bool]
    lines: Mapping[int, tuple[int, ...]]
    groups: Mapping[int, tuple[int, ...]]
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

    def members(group: int) -> tuple[int, ...]:
        # Its leaves by quill's rule, and each step under it that will never ship.
        leaves = {leaf.number for leaf in leaves_below(closure[group], closure, shipped)}
        return tuple(sorted(leaves | {number for number, steps in up.items()
                                      if group in steps and closure[number].kind == "step"
                                      and never_ships(number)}))
    groups = {group: members(group) for group, card in closure.items()
              if DEPLOY_TOGETHER_LABEL in card.labels}
    groups = {group: steps for group, steps in groups.items() if steps}
    return Cards({number: MOVES_MONEY_LABEL in held[number].labels for number in work},
                 {number: (number, *up[number]) for number in work}, groups,
                 frozenset(number for number in {*work, *(step for steps in groups.values()
                                                          for step in steps)}
                           if never_ships(number)),
                 {number: _what(card.kind) for number, card in held.items()
                  if number not in work})


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
    """Whether a card -- a step that can be a member of a group, or a card the window
    names -- counts in a release whose production ``held`` on one side or the other: one
    that will never ship (``known``'s dropped) counts only while production holds it,
    kept by the release or taken out by it (judged per cut, so an offered cut that still
    ships it is held to its siblings)."""
    return number not in known.dropped or number in held


def _group(group: int, known: Cards, touching: Mapping[int, tuple[int, ...]],
           before: frozenset[int], after: frozenset[int]) -> Group:
    """One group a release touches: each of its members (each step under it that counts,
    :func:`_counts`), sorted by whether the window names it and whether production holds
    it ``before`` the release (at its base) and ``after`` it (at the judged cut) --
    carried in by it, released before it, taken out by it (undone), or held on neither
    side (missing) -- and apart, each member that will never ship; and each card the
    window names under the group that is no member of it, with the steps between (the
    group's own card names itself).  ``touching`` maps each card the window names to the
    groups it touches."""
    members = tuple(step for step in known.groups[group]
                    if _counts(step, known, before | after))

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
