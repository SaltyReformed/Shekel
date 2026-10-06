"""The ``quill`` command: how every session reads and writes Shekel's plan in its private tracker.

The plan lives in the private repository ``saltyreformed-labs/shekel-plan``
(ruling ``balance:R-BAL170``): each step, finding, ruling and question is one
issue, a "card", and one board holds the steps and questions in the order the
developer drags them (``R-BAL177``).  Every write goes through quill's
GitHub App, and every card a session files passes :mod:`check` first.

Whether a card SHIPPED is git's answer, never the card's state: a commit on
``dev`` carrying ``Ships: plan#N``, with no later ``Reopens: plan#N``
(:mod:`_git`).  The card's open or closed state is display, which ``sync``
writes from git.  So every command that asks git -- ``next``, ``show``,
``claim``, ``move``, ``drop``, ``sync``, and ``file`` given an owner or a
parent or finding a card of its own closed while still marked -- first runs
``git fetch origin dev``, which moves this checkout's ``origin/dev``.  (``move``
and ``drop`` ask it whether a step is split: a leaf dropped before its filing
finished is no leaf unless git says it shipped, ``_state.leaves``.)

Usage, from the repository root (``plan#N`` or ``N`` names a card)::

    python -m tools.quill.quill next [--arc ARC]
    python -m tools.quill.quill claim plan#N [--branch BRANCH]
    python -m tools.quill.quill release plan#N [--branch BRANCH | --unreadable]
    python -m tools.quill.quill show plan#N | OLD-ID
    python -m tools.quill.quill file step --arc ARC --title NAME --body-file SPEC [--parent plan#P]
    python -m tools.quill.quill file finding --arc ARC --title NAME --owner plan#S --text SENTENCE
    python -m tools.quill.quill file ruling --arc ARC --title NAME --owner plan#S
                                (--question-file Q | --from-question plan#Q) --answer-file A
    python -m tools.quill.quill file question --arc ARC --title NAME --body-file QUESTION
    python -m tools.quill.quill block plan#N --by plan#M [--remove]
    python -m tools.quill.quill move plan#N (--top | --bottom | --after plan#M)
    python -m tools.quill.quill drop plan#N --why REASON
    python -m tools.quill.quill sync [--dry-run]
    python -m tools.quill.quill spec-history plan#N [--since BRANCH_OR_DATE]
    python -m tools.quill.quill spec-revert plan#N --to EDIT_ID

Exit status: 0 done; 1 refused, with nothing written (or, for ``sync``, a
REPORT a person must act on); 2 a call failed -- GitHub, git, the network, or
a file the command reads (the App's credentials, a ``--body-file``) -- or the
command line itself is not one of the forms above (argparse's usage error),
before anything is read.  Every write is printed as it lands, so after a
failure the output says what was written; ``file`` run again finishes a filing
a failure cut short (R-BAL186): every card it creates is marked ``filing``
until its last write, and a marked card is never offered (R-BAL202).  Run
again, it writes nothing over a card the earlier run filed that is still open,
still marked (refused when a person's or `quill drop`'s close ended it, unless it
is work git says shipped), or -- for a ruling, which its own filing closes -- linked under
its owner; a card closed after its filing finished is not looked for, so the
command files another (unless a listing that lags its close still shows it open).
"""
from __future__ import annotations

import argparse
import difflib
import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import requests

from tools.ci import trailers
from tools.ci.arcs import ARCS, REPO
from tools.ci.gitcmd import GitError
from tools.quill import _git
from tools.quill._command import (
    ON_BOARD,
    Refused,
    _label,
    _one,
    _outside_parent,
    _shipped,
    _unfinished,
    _with_closure,
)
from tools.quill._filing import cmd_file
from tools.quill._github import GitHubError
from tools.quill._state import (
    SyncPlan,
    Unsplit,
    drop_shows,
    drop_unlinks,
    filing_unfinished,
    holder,
    is_container,
    is_work,
    leaves_below,
    left_bare,
    never_offered,
    never_split,
    next_step,
    outside_reports,
    release_flag,
    release_hint,
    resolved,
    stale_claims,
    sync_plan,
    sync_unsplits,
    unfinished_reports,
    unsplit,
)
from tools.quill._tracker import Card, Claim, ClaimTaken, Tracker, TrackerError
from tools.quill.setup_tracker import FILING

#: Branches a claim may never name: nothing is built on them directly.
_SHARED_BRANCHES = ("dev", "main")
_CARD_REF = re.compile(r"(?:plan#|#)?([1-9][0-9]*)")


def card_number(text: str) -> int:
    """``plan#7``, ``#7`` or ``7`` -> 7."""
    found = _CARD_REF.fullmatch(text.strip())
    if not found:
        raise argparse.ArgumentTypeError(f"{text!r} is not a card: write plan#<number>")
    return int(found.group(1))


def _stray(trailer: trailers.Trailer) -> str:
    """A stray Reopens, as a report line."""
    return (f"{trailer.sha[:12]} 'Reopens: plan#{trailer.card}' cancels no Ships in its own "
            "history (R-BAL181): a mistyped number? It is not acted on")


def _branch(root: Path, given: str | None) -> str:
    """The branch a claim names: ``given``, or the one checked out; never a shared one."""
    branch = given or _git.current_branch(root)
    if branch is None:
        raise Refused("HEAD is detached: check out the branch that will ship the card, or "
                      "pass --branch")
    if branch in _SHARED_BRANCHES:
        raise Refused(f"a claim names the branch that will ship the card, not {branch!r}")
    return branch


# -- next, claim, release -----------------------------------------------------

def cmd_next(args, tracker: Tracker, root: Path) -> int:
    """The first open step in the board's order that is unclaimed, unshipped and unblocked;
    then what it cannot offer that a person must act on."""
    _, shipped, _ = _shipped(root)
    cards = _with_closure(tracker, {**tracker.open_cards(), **_unfinished(tracker)})
    claims = tracker.claims()
    order = [number for number, _ in tracker.board.order()]
    answer = next_step(order, cards, shipped, claims, args.arc)
    scope = f" in {args.arc}" if args.arc else ""
    print(f"next{scope}: {_label(answer.card)}" if answer.card else f"next{scope}: nothing")
    for card in answer.unplaced:
        print(f"NOT ON THE BOARD, so in no order: {_label(card)} -- "
              + ("`quill sync` puts it back on the board, unlinking the steps closed while "
                 "still being filed under it (R-BAL205)"
                 if never_split(card, cards, shipped) else
                 f"place it with `quill move plan#{card.number} --after plan#M` (or the board "
                 "lags a fresh write)"))
    for claim in stale_claims(claims, datetime.now(UTC), lambda b: _git.pushed(root, b)):
        print(f"STALE CLAIM: plan#{claim.card} by {holder(claim)} since {claim.made or '?'}, "
              f"no pushed branch -- {release_hint(claim)}")
    for line in outside_reports(cards):
        print(f"OUTSIDE LINK: {line}")
    for line in unfinished_reports(cards, shipped):
        print(f"UNFINISHED FILING: {line}")
    return 0


def cmd_claim(args, tracker: Tracker, root: Path) -> int:
    """Claim a card for the branch that will ship it."""
    card = _one(tracker, args.card)
    _, shipped, _ = _shipped(root)
    cards = _with_closure(tracker, {card.number: card})
    if not card.is_open or not is_work(card, cards, shipped):
        raise Refused(f"{_label(card)} is not work a branch ships (open step or finding, "
                      "not a container)")
    if why := never_offered(card, cards, shipped):
        raise Refused(f"{_label(card)} is never offered as work: {why}")
    if card.number in shipped:
        raise Refused(f"{_label(card)} shipped in git: no work is left on it to claim")
    branch = _branch(root, args.branch)
    try:
        claim = tracker.claim(card.number, branch)
    except ClaimTaken:
        held = tracker.claims().get(card.number)
        taken = f"{holder(held)} since {held.made or '?'}" if held else "someone (just released?)"
        raise Refused(f"plan#{card.number} is already claimed by {taken}") from None
    print(f"claimed {_label(card)} for {claim.branch!r}")
    return 0


def cmd_release(args, tracker: Tracker, root: Path) -> int:
    """Delete a card's claim: one naming the branch passed with ``--branch`` (else the one
    checked out), or with ``--unreadable`` one whose branch cannot be read."""
    claim = tracker.claims().get(args.card)
    if claim is None:
        raise Refused(f"plan#{args.card} is not claimed")
    if args.unreadable:
        if claim.branch is not None:
            raise Refused(f"plan#{args.card}'s claim names {claim.branch!r}; --unreadable "
                          "releases only a claim whose branch cannot be read")
    else:
        branch = args.branch or _git.current_branch(root)
        if branch is None:
            raise Refused(f"HEAD is detached, so no branch names the claim to release; "
                          f"pass {release_flag(claim)}")
        if claim.branch != branch:
            raise Refused(f"plan#{args.card}'s claim names {holder(claim)}, not {branch!r}; "
                          f"to release another branch's claim, pass {release_flag(claim)}")
    tracker.release(args.card)
    print(f"released plan#{args.card} (claimed by {holder(claim)} since {claim.made or '?'})")
    return 0


# -- show ---------------------------------------------------------------------

def _resolve_reference(tracker: Tracker, text: str) -> int:
    """``plan#N`` -> N; an old id (``R-BAL80``, ``balance:X-bi-6-4d``) -> its titled card."""
    try:
        return card_number(text)
    except argparse.ArgumentTypeError:
        pass
    arc, _, ident = text.rpartition(":")
    alias = f"[{ident}]"
    found = [
        number for number, title in tracker.find_titles(ident)
        if title.startswith(alias)
    ]
    if arc:
        found = [n for n, card in tracker.cards(found).items() if arc in card.labels]
    if len(found) != 1:
        raise Refused(f"{text!r} names {len(found)} cards ({found}); name one as plan#N"
                      + ("" if arc else " or give its arc, e.g. balance:" + ident))
    return found[0]


def _card_lines(card: Card, tracker: Tracker, cards: dict[int, Card],
                shipped: dict) -> list[str]:
    """What the tracker and git say about a card, one fact a line.  ``cards`` holds every
    step linked below it (:func:`_steps_below`), so a step linked under it that is no leaf
    of it says so (:func:`_state.never_split`), as ``next`` reads it."""
    claim = tracker.claims().get(card.number)
    stale = set(never_split(card, cards, shipped))
    order = [n for n, _ in tracker.board.order()]
    state = "open" if card.is_open else f"closed ({card.state_reason})"
    ship = f"yes, {shipped[card.number][0].sha[:12]}" if card.number in shipped else "no"
    place = f"#{order.index(card.number) + 1}" if card.number in order else "not on it"
    lines = [
        _label(card),
        f"  state: {state}",
        f"  shipped (git): {ship}",
        f"  board: {place}",
        f"  claim: {holder(claim)} since {claim.made or '?'}" if claim else "  claim: none",
        (f"  parent: plan#{card.parent}" if card.parent else
         f"  parent: {outside} (outside the tracker)" if (outside := _outside_parent(card))
         else "  parent: none"),
    ]
    lines += [f"  child: plan#{child.number} ({child.kind}, "
              f"{'open' if child.is_open else 'closed'}"
              + ("; no leaf: closed while still being filed, so never part of the split, "
                 "R-BAL205" if child.number in stale else "") + ")"
              for child in card.children]
    lines += [f"  blocked by: plan#{blocker}" for blocker in card.blocked_by]
    if filing_unfinished(card):
        lines.append("  filing: not finished (R-BAL202) -- never offered until its `quill file` "
                     "command runs again; with that command lost, make its missing writes on "
                     f"the web and remove its {FILING!r} label last")
    lines += [f"  {link.what} outside the tracker: {link.issue} -- not offered until it is "
              "removed (R-BAL188)" for link in card.outside if link.what != "parent"]
    return lines


def _steps_below(tracker: Tracker, card: Card) -> dict[int, Card]:
    """``card`` and every step linked below it, read by number: all that the leaf rule
    reads (:func:`_state.leaves` reads a linked step's own leaves, never a blocker or a
    parent), so ``show`` reads no more than it says."""
    cards, wanted = {card.number: card}, set(card.step_children)
    while wanted:
        found = tracker.cards(wanted)
        if absent := sorted(wanted - set(found)):
            raise TrackerError(f"plan#{absent[0]} is linked below plan#{card.number}, but a "
                               "read by its number does not hold it: run show again")
        cards.update(found)
        wanted = {number for step in found.values() for number in step.step_children} - set(cards)
    return cards


def cmd_show(args, tracker: Tracker, root: Path) -> int:
    """A card, then the code repository's history for it."""
    number = _resolve_reference(tracker, args.card)
    card = _one(tracker, number)
    found, shipped, strays = _shipped(root)
    print("\n".join(_card_lines(card, tracker, _steps_below(tracker, card), shipped)))
    print()
    print(tracker.body(number))
    print()
    history = [t for t in found.trailers if t.card == number]
    for trailer in history:
        print(f"{trailer.key}: {trailer.sha[:12]} {trailer.subject}")
    if not history:
        print(f"no commit on {trailers.DEV} carries Ships: or Reopens: plan#{number}")
    for trailer in strays:
        if trailer.card == number:
            print(f"STRAY: {_stray(trailer)}")
    return 0


# -- block, move, drop ----------------------------------------------------------

def cmd_block(args, tracker: Tracker, _root: Path) -> int:
    """Record (or with ``--remove``, delete) that one card is blocked by another."""
    if args.card == args.by:
        raise Refused("a card cannot block itself")
    blocker = _one(tracker, args.by)
    card = _one(tracker, args.card)
    if args.remove:
        if blocker.number not in card.blocked_by:
            raise Refused(f"plan#{card.number} is not blocked by plan#{blocker.number}")
        tracker.unblock(card.number, blocker)
        print(f"plan#{card.number} is no longer blocked by plan#{blocker.number}")
    else:
        tracker.block(card.number, blocker)
        print(f"plan#{card.number} is blocked by plan#{blocker.number}")
    return 0


def cmd_move(args, tracker: Tracker, root: Path) -> int:
    """Put a step or question at a place in the board's order."""
    card = _one(tracker, args.card)
    _, shipped, _ = _shipped(root)
    cards = _with_closure(tracker, {card.number: card})
    if card.kind not in ON_BOARD or not card.is_open or is_container(card, cards, shipped):
        raise Refused(f"the board holds open steps and questions only, and no step split into "
                      f"leaves (R-BAL177, R-BAL179): {_label(card)}")
    if filing_unfinished(card) and card.kind == "step":
        raise Refused(f"{_label(card)}'s filing has not finished (R-BAL202): finish it first.  "
                      "If it is a leaf -- one whose link has not landed reads as a top-level "
                      "step -- its `quill file` command moves it again after this move")
    order = tracker.board.order()
    items = dict(order)
    if args.after is not None:
        if args.after not in items:
            raise Refused(f"plan#{args.after} is not on the board")
        after = items[args.after]
    elif args.bottom:
        rest = [item for number, item in order if number != card.number]
        after = rest[-1] if rest else None
    else:
        after = None
    item = items.get(card.number)
    if item is None:
        item = tracker.board.add(card)
        print(f"  board: added {_label(card)} at the bottom")
    shown = tracker.board.place(item, after)
    print(f"moved {_label(card)}" + ("" if shown else
                                     " -- the board has not shown it yet; re-read it later"))
    return 0


def _drop(tracker: Tracker, card: Card, why: str) -> None:
    """Drop one piece of work: the reason as a comment, then closed as not planned; each
    write printed as it lands, and a claim on it named."""
    tracker.comment(card.number, f"Dropped: {why}")
    print(f"  commented on plan#{card.number}: Dropped: {why}")
    _close_dropped(tracker, card)


def _close_dropped(tracker: Tracker, card: Card) -> None:
    """Close a card whose reason is already on it as not planned, printed as it lands,
    and name a claim on it."""
    tracker.close(card.number, "not_planned")
    print(f"dropped {_label(card)}")
    claim = tracker.claims().get(card.number)
    if claim is not None:
        print(f"  its claim by {holder(claim)} stays: {release_hint(claim)}")


def _unsplit(tracker: Tracker, undo: Unsplit, cards: dict[int, Card],
             dry_run: bool = False) -> None:
    """Take leaves whose filing never finished out of the split they began (R-BAL205,
    :func:`_state.unsplit` decides), each write printed as it lands, or under ``dry_run``
    printed as what would be written and not made: the split step put back on the board,
    and moved into a leaf's place, when it is left a plain step; then each leaf unlinked.
    ``cards`` holds the split step and every card linked under it
    (:func:`_with_closure`); ``quill drop`` and ``quill sync`` both take a split apart
    here."""
    split, would = cards[undo.step], "would be " if dry_run else ""
    item = split.board_item
    if undo.add:
        item = None if dry_run else tracker.board.add(split)
        print(f"  board: plan#{split.number} {would}added back at the bottom, a plain step again")
    if undo.after is not None:
        leaf, after = undo.after
        shown = dry_run or tracker.board.place(item, after)
        print(f"  board: plan#{split.number} {would}moved into plan#{leaf}'s place"
              + ("" if shown else " (the board has not shown it yet)"))
    for number in undo.unlink:
        if not dry_run:
            tracker.remove_child(split.number, cards[number])
        print(f"  plan#{number} {would}unlinked from plan#{split.number}: its filing never "
              "finished, so it was never part of that split (R-BAL205)")


def _leave_split(tracker: Tracker, leaf: Card, cards: dict[int, Card], shipped: dict) -> None:
    """Take ``leaf``, dropped while its filing has not finished, out of the split it began,
    which it was never part of (R-BAL205), before its close: a split step the TOOL closed
    first shown as ``sync`` would show it once the drop is done
    (:func:`_state.drop_shows`), each write printed as it lands; then :func:`_unsplit`,
    the step put back on the board only when it will be open."""
    split = cards[leaf.parent]
    write = drop_shows(split, leaf, cards, shipped)
    if write == "reopen":
        tracker.reopen(split.number)
        print(f"  reopened plan#{split.number}: quill had closed it, and without "
              f"plan#{leaf.number} it still has work to do (R-BAL190)")
    elif write is not None:
        tracker.close(split.number, write)
        print(f"  closed plan#{split.number} again as {write.replace('_', ' ')}: what its "
              f"smaller steps say once plan#{leaf.number} is out of them (R-BAL190)")
    opened = split.is_open or write == "reopen"
    _unsplit(tracker, unsplit(split, (leaf.number,), cards, shipped,
                              tracker.board.order() if opened else None), cards)


def cmd_drop(args, tracker: Tracker, root: Path) -> int:
    """Retire work with no code: each card's reason as a comment, then closed as not planned.

    Work git says shipped is never dropped: undoing it is a ``Reopens:`` commit.
    A leaf dropped while its filing has not finished is first taken out of the
    split it began, which it was never part of (R-BAL205, :func:`_leave_split`):
    unlinked, and its split step, if it was the only leaf and will be open, a
    plain step again, on the board (in the leaf's place when the leaf is on
    it).  A split step the TOOL closed is first shown as ``sync`` would show it
    once the drop is done (:func:`_state.drop_shows`, review C2 LOW 6): reopened
    while it still has work to do, closed again as its other leaves say, or, a
    plain step a drop closed, left dropped.  One a PERSON closed keeps their
    close, and stays off the board.
    No drop leaves a link that is no leaf behind it (:func:`_state.drop_unlinks`):
    a step closed while still being filed and linked under the card dropped is
    unlinked before the card is closed, and so is every leaf still being filed
    that a split step's drop closes, after its close and before any step left
    bare is closed.  Left linked until the next ``sync`` (which reads every
    marked card), such a leaf would act on the plan as it stands meanwhile: a
    person reopening it would make it a leaf again and revive the step that was
    dropped (review C2 M1), and a person removing its stale mark would make it a
    dropped leaf, so R-BAL187 could drop a step nobody built (review C3 M1).
    A split step holds no decision of the tool's (R-BAL190):
    dropping one notes it on the split step first, then drops every leaf below
    it that is still work by git's answer -- not shipped and not dropped,
    whatever its card shows -- each with the reason, and names the shipped ones
    it leaves; its own state shows its leaves at the next ``sync``.  A step
    every leaf of which was still being filed is closed too, after them: closed
    while marked, they were never its leaves (:func:`_state.left_bare`).  Run again
    after a failure, it notes the split step again and drops the leaves still
    work (a leaf whose comment landed but not its close gets the reason twice).
    """
    card = _one(tracker, args.card)
    _, shipped, _ = _shipped(root)
    cards = _with_closure(tracker, {card.number: card})
    if not is_container(card, cards, shipped):
        if not card.is_open:
            raise Refused(f"{_label(card)} is already closed")
        if is_work(card, cards, shipped) and card.number in shipped:
            raise Refused(f"{_label(card)} shipped in git: undo it with a commit carrying "
                          f"'Reopens: plan#{card.number}', not a drop")
        if card.kind == "step" and card.parent is not None and filing_unfinished(card):
            _leave_split(tracker, card, cards, shipped)
        for undo in drop_unlinks(card, cards, shipped, set()):
            _unsplit(tracker, undo, cards)
        _drop(tracker, card, args.why)
        return 0
    below = leaves_below(card, cards, shipped)
    done = [leaf for leaf in below if leaf.number in shipped]
    below = [leaf for leaf in below if not resolved(leaf.number, cards, shipped)]
    for leaf in done:
        print(f"  plan#{leaf.number} shipped in git, so it is not dropped")
    if not below:
        raise Refused(f"{_label(card)} has no leaf below it that is still work; its own state "
                      "shows its leaves, which `quill sync` writes")
    names = ", ".join(f"plan#{leaf.number}" for leaf in below)
    ending = {leaf.number for leaf in below if filing_unfinished(leaf)}
    bare = left_bare(card, cards, shipped, ending)
    tracker.comment(card.number, f"Dropped: {args.why} (its leaves still work: {names})")
    print(f"  commented on plan#{card.number}: dropping its leaves still work, {names}"
          + ("" if card in bare else "; it shows them at the next `quill sync`"))
    for leaf in below:
        _drop(tracker, leaf, args.why)
    for undo in drop_unlinks(card, cards, shipped, ending):
        _unsplit(tracker, undo, cards)
    for step in bare:
        print(f"  plan#{step.number}: every leaf of it was still being filed, so no leaf "
              "carries its drop (R-BAL190): it is closed itself")
        if step.number == card.number:
            _close_dropped(tracker, step)
        else:
            _drop(tracker, step, args.why)
    return 0


# -- sync -------------------------------------------------------------------------

def _ship_branches(tracker: Tracker, root: Path, cards: dict[int, Card],
                   shipped: dict) -> dict[int, set[str]]:
    """For each open card git says shipped, the branches whose merged pull requests
    into ``dev`` carried its standing ``Ships:`` commits."""
    repository = _git.origin_repository(root)
    return {
        number: {branch for trailer in trailers
                 for branch in tracker.merged_into_dev(repository, trailer.sha)}
        for number, trailers in shipped.items() if number in cards and cards[number].is_open
    }


def _undo_splits(tracker: Tracker, cards: dict[int, Card], shipped: dict, changes: SyncPlan,
                 dry_run: bool) -> None:
    """``sync``'s undo of each split a leaf closed while still being filed is linked under
    (:func:`_state.sync_unsplits`, R-BAL205, which reads the board's order and ``changes``,
    the state writes ``sync`` makes): each named, then made by :func:`_unsplit` -- under
    ``dry_run``, printed as what would be written and not made."""
    for undo in sync_unsplits(cards, shipped, changes, tracker.board.order()):
        names = ", ".join(f"plan#{number}" for number in undo.unlink)
        print(f"{'would ' if dry_run else ''}undo the split of {_label(cards[undo.step])} by "
              f"{names}, closed while still being filed (R-BAL205)")
        _unsplit(tracker, undo, cards, dry_run)


def _sync_reads(tracker: Tracker, found: trailers.History) -> tuple[dict[int, Card],
                                                                 dict[int, Claim], list[str]]:
    """The cards ``sync`` decides over, every claim, and a HISTORY line for each card a
    trailer in ``found`` names that does not exist.

    The listings of open cards and of EVERY card still marked (a filing unfinished, or
    one closed while still being filed, whose link under its split step ``sync``
    undoes wherever that step is) only FIND the cards it decides over; each
    is then read by its number, which decides, since a listing may still show a card
    as it was before a write: a leaf's close the listing lagged made its split step,
    which ``quill drop`` had just closed, read as split again, and sync reopened it.
    Every card a trailer names, and every claimed card, is read by its number too --
    the claimed ones so that a claim on a split step is reported whatever that step's
    state (:func:`_state.sync_plan`) -- and then every card a decision over them reads
    (:func:`_with_closure`).
    """
    named = {trailer.card for trailer in found.trailers}
    listed = {**tracker.open_cards(), **tracker.marked()}
    claims = tracker.claims()
    cards = tracker.cards(named | set(listed) | set(claims))
    if unread := sorted(set(listed) - set(cards)):
        raise TrackerError(f"plan#{unread[0]} is listed, but a read by its number does not hold "
                           "it (that read has not caught up yet, or the card was deleted or "
                           "moved since): run sync again")
    history = [f"a trailer on {trailers.DEV} names plan#{number}, which does not exist"
               for number in sorted(named - set(cards))]
    return _with_closure(tracker, cards), claims, history


def cmd_sync(args, tracker: Tracker, root: Path) -> int:
    """Write each step's and finding's open or closed state from git (display only), then
    undo each split a leaf closed while still being filed is linked under (R-BAL205,
    :func:`_undo_splits`): last, after every state write and every REPORT and HISTORY
    line, so a failed undo costs none of them; run again, it finishes the undo.

    Prints a REPORT for each thing a person must fix in the tracker, which makes
    it exit 1, and a HISTORY line for each thing a commit already on ``dev`` says
    wrongly, which no tracker write can change and so never fails it (R-BAL184).

    What it reads: :func:`_sync_reads`.
    """
    found, shipped, strays = _shipped(root)
    cards, claims, history = _sync_reads(tracker, found)
    changes = sync_plan(cards, shipped, claims, _ship_branches(tracker, root, cards, shipped))
    verb = "would " if args.dry_run else ""
    for number in changes.close:
        if not args.dry_run:
            tracker.close(number, "completed")
        print(f"{verb}close {_label(cards[number])}")
    for number in changes.drop:
        if not args.dry_run:
            tracker.close(number, "not_planned")
        print(f"{verb}close as not planned, every leaf dropped (R-BAL187): "
              f"{_label(cards[number])}")
    for number in changes.reopen:
        if not args.dry_run:
            tracker.reopen(number)
        print(f"{verb}reopen {_label(cards[number])}")
    for number in changes.release:
        if not args.dry_run:
            tracker.release(number)
        print(f"{verb}release plan#{number}'s claim")
    for line in changes.reports:
        print(f"REPORT: {line}")
    history += changes.history + list(found.malformed) + [_stray(t) for t in strays]
    for line in history:
        print(f"HISTORY: {line}")
    _undo_splits(tracker, cards, shipped, changes, args.dry_run)
    return 1 if changes.reports else 0


# -- spec-history, spec-revert ------------------------------------------------------

def _since(root: Path, text: str) -> datetime:
    """``--since``: an ISO date or time, or a branch (when its work started,
    :func:`_git.started`)."""
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        when = datetime.fromisoformat(_git.started(root, text))
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _diff(before: str, after: str, label: str) -> str:
    """A unified diff of two bodies."""
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        "before", label, lineterm="\n",
    ))


def cmd_spec_history(args, tracker: Tracker, root: Path) -> int:
    """Every saved change to a card's body in a window, for the review R-BAL174 requires."""
    _, versions = tracker.edits(args.card)
    since = _since(root, args.since) if args.since else None
    shown = 0
    for index, version in enumerate(versions):
        if since and datetime.fromisoformat(version.edited_at) < since:
            continue
        before = versions[index - 1].body if index else ""
        what = "filed" if index == 0 else f"edit {version.edit_id}"
        print(f"== {what}, {version.edited_at} by {version.editor}")
        print(_diff(before, version.body, version.edited_at) or "(no change to the text)")
        shown += 1
    if not shown:
        last = versions[-1]
        print(f"plan#{args.card}'s body has not changed since {args.since} (last saved "
              f"{last.edited_at} by {last.editor})")
    return 0


def cmd_spec_revert(args, tracker: Tracker, _root: Path) -> int:
    """Restore a card's body to an earlier saved version, by hand.

    The body is read again just before the write, and the write is refused if
    it changed since the history was read: the diff printed is then still the
    change made.  (GitHub offers no conditional issue write, so a change in the
    instant between that read and the write is not seen.)
    """
    body, versions = tracker.edits(args.card)
    chosen = [version for version in versions if version.edit_id == args.to]
    if not chosen:
        raise Refused(f"plan#{args.card} has no saved version {args.to!r}; "
                      f"`quill spec-history plan#{args.card}` lists them")
    if chosen[0].body == body:
        raise Refused(f"plan#{args.card}'s body already is that version")
    print(_diff(body, chosen[0].body, f"version {args.to}"))
    if tracker.body(args.card) != body:
        raise Refused(f"plan#{args.card} changed while its history was read; nothing was "
                      "written: run it again")
    tracker.set_body(args.card, chosen[0].body)
    print(f"restored plan#{args.card}'s body to the version saved {chosen[0].edited_at} "
          f"by {chosen[0].editor}")
    return 0


# -- the command line -----------------------------------------------------------------

def _file_parser(commands) -> None:
    """``file step|finding|ruling|question`` and each kind's arguments."""
    kinds = commands.add_parser("file", help="file a step, finding, ruling or question")
    sub = kinds.add_subparsers(dest="kind", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--arc", required=True, choices=ARCS)
    common.add_argument("--title", required=True, help="a short name (R-BAL178)")
    step = sub.add_parser("step", parents=[common], help="a step, or a leaf of one")
    step.add_argument("--body-file", required=True, help="its spec")
    step.add_argument("--parent", type=card_number, help="the step this leaf splits")
    step.add_argument("--label", action="append", choices=("moves-money", "deploy-together"))
    finding = sub.add_parser("finding", parents=[common], help="a defect in the code")
    finding.add_argument("--owner", type=card_number, required=True, help="its owner step")
    finding.add_argument("--text", required=True, help="one sentence, at most 400 characters")
    ruling = sub.add_parser("ruling", parents=[common], help="the developer's decision")
    ruling.add_argument("--owner", type=card_number, required=True, help="its owner step")
    asked = ruling.add_mutually_exclusive_group(required=True)
    asked.add_argument("--question-file", help="his question, word for word")
    asked.add_argument("--from-question", type=card_number,
                       help="the question card he answered; it becomes the ruling")
    ruling.add_argument("--answer-file", required=True, help="his answer, word for word")
    question = sub.add_parser("question", parents=[common], help="for the developer to answer")
    question.add_argument("--body-file", required=True, help="the question")


def parser() -> argparse.ArgumentParser:
    """Every command and its arguments."""
    top = argparse.ArgumentParser(prog="quill", description=__doc__.splitlines()[0])
    commands = top.add_subparsers(dest="command", required=True)
    nxt = commands.add_parser("next", help="the next step to build")
    nxt.add_argument("--arc", choices=ARCS)
    claim = commands.add_parser("claim", help="claim a card for this branch")
    claim.add_argument("card", type=card_number)
    claim.add_argument("--branch", help="default: the branch checked out")
    release = commands.add_parser("release", help="delete a card's claim")
    release.add_argument("card", type=card_number)
    whose = release.add_mutually_exclusive_group()
    whose.add_argument("--branch", help="the branch the claim names; default: the one checked out")
    whose.add_argument("--unreadable", action="store_true",
                       help="release a claim whose branch cannot be read, and no other")
    commands.add_parser("show", help="a card and its git history").add_argument("card")
    _file_parser(commands)
    block = commands.add_parser("block", help="record a blocked-by edge")
    block.add_argument("card", type=card_number)
    block.add_argument("--by", type=card_number, required=True)
    block.add_argument("--remove", action="store_true")
    move = commands.add_parser("move", help="place a step or question on the board")
    move.add_argument("card", type=card_number)
    where = move.add_mutually_exclusive_group(required=True)
    where.add_argument("--top", action="store_true")
    where.add_argument("--bottom", action="store_true")
    where.add_argument("--after", type=card_number)
    drop = commands.add_parser("drop", help="retire a card with no code")
    drop.add_argument("card", type=card_number)
    drop.add_argument("--why", required=True)
    commands.add_parser("sync", help="write cards' state from git").add_argument(
        "--dry-run", action="store_true")
    history = commands.add_parser("spec-history", help="a card's body edits")
    history.add_argument("card", type=card_number)
    history.add_argument("--since", help="an ISO date or time, or a branch (from its start)")
    revert = commands.add_parser("spec-revert", help="restore an earlier body")
    revert.add_argument("card", type=card_number)
    revert.add_argument("--to", required=True, help="an edit id spec-history printed")
    return top


COMMANDS: dict[str, Callable[..., int]] = {
    "next": cmd_next, "claim": cmd_claim, "release": cmd_release, "show": cmd_show,
    "file": cmd_file, "block": cmd_block, "move": cmd_move, "drop": cmd_drop,
    "sync": cmd_sync, "spec-history": cmd_spec_history, "spec-revert": cmd_spec_revert,
}


def main(argv: list[str] | None = None, connect: Callable[[], Tracker] = Tracker.connect,
         root: Path | None = None) -> int:
    """Run one command; its exit status."""
    args = parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args, connect(), root or REPO)
    except Refused as refused:
        print(f"refused: {refused}", file=sys.stderr)
        return 1
    except (GitHubError, TrackerError, GitError, requests.RequestException,
            OSError) as error:
        print(f"failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
