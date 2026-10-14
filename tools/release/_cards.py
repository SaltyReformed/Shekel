"""Which cards a release carries after the cutover, whether each moves money, and which
deploy-together group each is a leaf of: the release tool's card reader (step X-cx's
L9a-1b, T-a a1; rulings ``balance:R-BAL287`` - ``R-BAL289``).

**What a release carries is git's answer**: every card a ``Ships: plan#N`` or a
``Reopens: plan#N`` trailer on a commit in ``<base>..<cut>`` names, read by the one
trailer walk (:func:`tools.ci.trailers.history`).  An undo is carried as a ship is and
judged by the same rules, since it changes what production runs as much (R-BAL288).
Whether a card is in production after a release is git's too: shipped at the cut it is
judged at, by the one rule the tracker tool and CI ask (:func:`tools.ci.trailers.shipped`,
ruling ``R-BAL181``); one the window names that is not is UNDONE.

**A card moves money when it carries the moves-money label**, as the tracker holds it
when the release is judged.  A label is no sentence a tick rewrites, so a card keeps its
flag after it ships (the registries' ``recurrence:R20`` shape, which the pre-cutover
reader reads around, has no counterpart here).

**A group is a card labelled deploy-together** among the cards a decision over the named
ones reads (quill's closure, :func:`tools.quill.door.with_closure`, which holds every step
above each of them); its leaves are quill's own (``leaves_below``: work in any state, a
leaf split again counting as its own leaves), less each one dropped, which never ships.
A release naming one of its leaves must leave production holding every leaf or none
(R-BAL288): a release taking a whole group out is whole too.

**What it cannot read refuses the release**, and nothing is declared after the cutover
(R-BAL287, R-BAL289):

- an old-form ``Ships: <arc>:<id>``: the cutover's freeze ends with a release, so
  production held every one before the registries went (R-BAL287), and one here was
  merged after the freeze or missed by it.  Only a cut holding ``steps.md`` can judge
  it, so the refusal names the newest one in the window, to release first with the
  release tool as that cut holds it;
- a trailer naming a card the tracker does not hold: R-BAL289 refuses one before it
  lands, by a trailer check reading the tracker that X-cx builds after this reader and
  before the cutover, so what reaches here is a card deleted after its trailer landed
  (until that check exists, a mistyped number too);
- a value of neither form, which CI's trailer check refuses before it lands.

**The tracker is read once, at the release's cut** (:func:`read`), and every earlier cut
offered in its place is judged with that read (:func:`carried`): each lies on the cut's
first-parent line, so its window is part of the cut's and names no card the read does
not hold.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from tools.ci import trailers
from tools.ci.gitcmd import git
from tools.quill.door import Tracker, dropped, leaves_below, with_closure
from tools.quill.setup_tracker import DEPLOY_TOGETHER_LABEL, MOVES_MONEY_LABEL
from tools.release._model import Carried, Group, Step, steps_blob


@dataclass(frozen=True)
class Cards:
    """What the release's cut knows of the cards its window names, read from the tracker
    once: whether each card the tracker holds moves money, and each deploy-together
    group one of them is a leaf of, with its leaves."""

    money: Mapping[int, bool]
    groups: Mapping[int, tuple[int, ...]]


def _standing(root: Path, cut: str) -> set[int]:
    """Every card shipped at ``cut``: what production runs once ``cut`` is released."""
    return set(trailers.shipped(root, trailers.history(root, cut))[0])


def read(root: Path, base: str, cut: str, tracker: Tracker) -> Cards:
    """What ``cut`` knows of the release ``<base>..<cut>``: every card its trailers name,
    read by number, and every card a decision over them reads, read once.

    Raises:
        tools.quill.door.TrackerError: when a card one of them links to is not there.
        tools.quill.door.GitHubError: when GitHub answers a read with an error.
        tools.ci.gitcmd.GitError: when git cannot read the range.
    """
    named = {trailer.card for trailer in trailers.history(root, f"{base}..{cut}").trailers}
    held = tracker.cards(named)
    closure = with_closure(tracker, dict(held))
    shipped = _standing(root, cut)
    groups = {}
    for number, card in closure.items():
        if DEPLOY_TOGETHER_LABEL in card.labels:
            leaves = tuple(leaf.number for leaf in leaves_below(card, closure, shipped)
                           if not dropped(leaf.number, closure, shipped))
            if named.intersection(leaves):
                groups[number] = leaves
    return Cards({number: MOVES_MONEY_LABEL in card.labels for number, card in held.items()},
                 groups)


def _judgeable(root: Path, base: str, cut: str) -> str | None:
    """The newest cut on ``dev``'s first-parent line in ``<base>..<cut>`` whose tree holds
    ``steps.md``; None when none does."""
    line = git(root, "rev-list", "--first-parent", f"{base}..{cut}").split()
    return next((sha for sha in line if steps_blob(root, sha) is not None), None)


def _unreadable(root: Path, base: str, cut: str, found: trailers.History,
                absent: Mapping[int, list[str]]) -> list[str]:
    """Each thing this reader cannot read: an old-form claim, a card the tracker does not
    hold, and a value of neither form."""
    lines = []
    if found.old_form:
        judgeable = _judgeable(root, base, cut)
        first = (f"release {judgeable[:12]} first, with the release tool as that cut holds it"
                 if judgeable else "no cut on dev's line in this window holds steps.md")
        lines += [f"{claim.sha[:12]} claims `{claim.key}` in the old form, which only a cut "
                  "holding steps.md can judge (R-BAL287: the cutover's freeze ends with a "
                  f"release of every one): {first}" for claim in found.old_form]
    lines += [f"plan#{number} (named at {', '.join(sha[:12] for sha in shas)}) is a card the "
              "tracker does not hold, so whether it moves money cannot be read (R-BAL289)"
              for number, shas in sorted(absent.items())]
    return lines + list(found.malformed)


def _group(group: int, leaves: tuple[int, ...], named: set[int], standing: set[int]) -> Group:
    """One group a release touches, each leaf sorted by whether the window names it and
    whether production holds it after the release."""
    def which(in_window: bool, in_production: bool) -> tuple[str, ...]:
        return tuple(f"plan#{leaf}" for leaf in leaves
                     if (leaf in named) == in_window and (leaf in standing) == in_production)
    return Group(f"plan#{group}", carried=which(True, True), released=which(False, True),
                 missing=which(False, False), undone=which(True, False))


def carried(root: Path, base: str, cut: str, known: Cards) -> Carried:
    """What ``<base>..<cut>`` carries, judged with ``known``, the release's cut's
    :class:`Cards` (``cut`` is that cut or an earlier one on its first-parent line).

    Raises:
        tools.ci.gitcmd.GitError: when git cannot read the range.
    """
    found = trailers.history(root, f"{base}..{cut}")
    named: dict[int, list[str]] = {}
    for trailer in found.trailers:
        shas = named.setdefault(trailer.card, [])
        if trailer.sha not in shas:
            shas.append(trailer.sha)
    standing = _standing(root, cut)
    steps = tuple(
        Step(f"plan#{number}", tuple(shas), known.money[number],
             tuple(f"plan#{group}" for group, leaves in known.groups.items()
                   if number in leaves),
             undone=number not in standing)
        for number, shas in sorted(named.items()) if number in known.money)
    groups = tuple(_group(group, leaves, set(named), standing)
                   for group, leaves in known.groups.items() if named.keys() & set(leaves))
    absent = {number: shas for number, shas in named.items() if number not in known.money}
    return Carried(steps, groups, tuple(_unreadable(root, base, cut, found, absent)))
