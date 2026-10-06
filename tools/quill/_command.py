"""What every ``quill`` command shares: its refusal, its reads of the tracker and of git, and
how it names a card.

Split out of the command module at X-cx L2's leaf C, whose change would otherwise take
that module past the 1,000 lines ``too-many-lines`` allows (it has been ``quill.py``
since the tool's rename), so that ``_filing`` (the ``file`` command) and ``quill``
(every other command, and the command line) read these from one place.
"""
from __future__ import annotations

from pathlib import Path

from tools.ci import trailers
from tools.ci.arcs import ARCS
from tools.quill import _git
from tools.quill._state import filing_unfinished, missing
from tools.quill._tracker import Card, Tracker, TrackerError


#: The kinds of card the board holds, in the developer's order (R-BAL177).
ON_BOARD = ("step", "question")


class Refused(Exception):
    """A command that will not run as asked; its message says why."""


def _with_closure(tracker: Tracker, cards: dict[int, Card]) -> dict[int, Card]:
    """``cards`` plus every card a decision over them reads (blockers, parents, children)."""
    while wanted := missing(cards):
        found = tracker.cards(wanted)
        absent = wanted - set(found)
        if absent:
            raise TrackerError(f"cards {sorted(absent)} are named by others but do not exist")
        cards.update(found)
    return cards


def _one(tracker: Tracker, number: int) -> Card:
    """One card; :class:`Refused` when it does not exist."""
    found = tracker.cards([number])
    if number not in found:
        raise Refused(f"plan#{number} does not exist")
    return found[number]


def _shipped(root: Path) -> tuple[trailers.History, dict, tuple[trailers.Trailer, ...]]:
    """``dev``'s card trailers, fetched fresh; the cards they say shipped; the stray
    ``Reopens:`` trailers (R-BAL181)."""
    _git.fetch(root)
    found = trailers.history(root)
    return (found, *trailers.shipped(root, found))


def _label(card: Card) -> str:
    """``plan#7 [step, balance] title``."""
    arcs = ", ".join(label for label in card.labels if label in ARCS) or "no arc"
    return f"plan#{card.number} [{card.kind or 'no type'}, {arcs}] {card.title}"


def _outside_parent(card: Card) -> str | None:
    """The issue outside the tracker that ``card`` is a sub-issue of, if any."""
    return next((link.issue for link in card.outside if link.what == "parent"), None)


def _unfinished(tracker: Tracker) -> dict[int, Card]:
    """Every card whose filing has not finished (:func:`_state.filing_unfinished`), open or
    a closed ruling, by number."""
    return {number: card for number, card in tracker.marked().items()
            if filing_unfinished(card)}
