"""What the code repository's git history says about the plan's cards: the commit trailers.

**Whether a card shipped is git's answer, never the card's open or closed
state** (ruling ``balance:R-BAL170``; the card's state is display, which the
tracker tool's ``plan sync`` writes from this module's answer).  A card is
shipped when a commit on ``dev`` carries the trailer ``Ships: plan#N`` and no
LATER commit carries ``Reopens: plan#N``.

**"Later" is git's ancestry, not a date or the merge order** (ruling
``balance:R-BAL181``).  A ``Reopens:`` commit cancels every ``Ships:`` commit
for the same card in its own history -- the commits it was built on, itself
included -- and no other: a card re-shipped after it was reopened is shipped
again, and a ``Ships:`` commit on a branch the reopening never saw still
stands.  An undo (a revert) is always built on what it undoes, so a
``Reopens:`` that cancels nothing is a STRAY -- most likely a mistyped number
-- and is reported, never acted on.

**Only the trailer block is read, and git parses it** (``%(trailers)``, the
way ``tools/plan_gate/_shipped.py`` reads ``Ships: <arc>:<id>`` until step
X-cx's cutover, L8, deletes it): a sentence that mentions ``plan#7`` claims
nothing.  A value is ``plan#<number>``, or the old ``<arc>:<id>`` form the
plan gate reads, or it is reported as malformed.

**A shallow clone is refused**: its history stops short, and every card
shipped before the cut would read as unshipped.

The rules live here, below both of their readers: the tracker tool asks what
shipped, and CI's ``commit_trailers`` check refuses a commit that breaks them.
Moved here from ``tools/plan/_git.py`` by step X-cx's L4.  Nothing here writes
the repository.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from tools.ci.arcs import ARCS
from tools.ci.gitcmd import GitError, git, run

SHIPS, REOPENS = "Ships", "Reopens"
#: The branch every shipped card's commit is on.
DEV = "origin/dev"
#: A trailer's value naming a card: ``plan#`` and the card's number.
_CARD = re.compile(r"plan#([1-9][0-9]*)")
#: The old form, ``<arc>:<id>``, which the plan gate reads until the cutover.
_OLD_FORM = re.compile(rf"(?:{'|'.join(ARCS)}):\S+")
_FIELD, _VALUE, _RECORD = "\x01", "\x02", "\x03"


@dataclass(frozen=True)
class Trailer:
    """One ``Ships:`` or ``Reopens:`` trailer naming a card."""

    key: str
    card: int
    sha: str
    subject: str


@dataclass(frozen=True)
class History:
    """Every card trailer on a branch, and every value that is neither ``plan#N`` nor the
    old ``<arc>:<id>`` form."""

    trailers: tuple[Trailer, ...]
    malformed: tuple[str, ...]


def history(root: Path, ref: str = DEV) -> History:
    """Every ``Ships:`` and ``Reopens:`` trailer naming a card, on ``ref``.

    One ``git log`` over everything ``ref`` names -- a branch, or a range such
    as ``base..head`` -- reading each message's trailer block as git parses it.

    Raises:
        GitError: when ``root`` is a shallow clone, whose history stops short.
    """
    if git(root, "rev-parse", "--is-shallow-repository").strip() == "true":
        raise GitError(
            f"{root} is a shallow clone, so it cannot say what shipped: run "
            "`git fetch --unshallow origin` (in CI, check out with fetch-depth: 0)"
        )
    out = git(
        root, "log", ref,
        f"--format=%H{_FIELD}%s{_FIELD}%(trailers:key={SHIPS},valueonly,separator=%x02)"
        f"{_FIELD}%(trailers:key={REOPENS},valueonly,separator=%x02){_RECORD}",
    )
    trailers, malformed = [], []
    for record in filter(str.strip, out.split(_RECORD)):
        sha, subject, ships, reopens = record.strip("\n").split(_FIELD)
        for key, values in ((SHIPS, ships), (REOPENS, reopens)):
            for value in filter(None, (v.strip() for v in values.split(_VALUE))):
                card = _CARD.fullmatch(value)
                if card:
                    trailers.append(Trailer(key, int(card.group(1)), sha, subject))
                elif not _OLD_FORM.fullmatch(value):
                    malformed.append(f"{sha[:12]} {key}: {value!r} is not plan#<number>")
    return History(tuple(trailers), tuple(malformed))


def is_ancestor(root: Path, older: str, newer: str) -> bool:
    """Whether ``older`` is in ``newer``'s history (a commit is in its own)."""
    done = run(root, "merge-base", "--is-ancestor", older, newer)
    if done.returncode not in (0, 1):
        raise GitError(f"git merge-base --is-ancestor {older} {newer}: {done.stderr.strip()}")
    return done.returncode == 0


def shipped(root: Path, found: History) -> tuple[dict[int, tuple[Trailer, ...]],
                                                 tuple[Trailer, ...]]:
    """``{card: the Ships trailers still standing}`` for every card git says shipped,
    and the STRAY ``Reopens:`` trailers, which cancel no ``Ships:``.

    A ``Ships:`` trailer stands unless a ``Reopens:`` trailer for the same card
    sits on a commit whose history holds it (R-BAL181).
    """
    ships: dict[int, list[Trailer]] = {}
    reopens: dict[int, list[Trailer]] = {}
    for trailer in found.trailers:
        (ships if trailer.key == SHIPS else reopens).setdefault(trailer.card, []).append(trailer)
    standing, cancelling = {}, set()
    for card, claims in ships.items():
        live = []
        for claim in claims:
            undone = [undo for undo in reopens.get(card, ())
                      if is_ancestor(root, claim.sha, undo.sha)]
            cancelling.update(undo.sha for undo in undone)
            if not undone:
                live.append(claim)
        if live:
            standing[card] = tuple(live)
    strays = tuple(undo for undos in reopens.values() for undo in undos
                   if undo.sha not in cancelling)
    return standing, strays
