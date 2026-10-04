"""The ``plan`` command: how every session reads and writes Shekel's plan in its private tracker.

The plan lives in the private repository ``saltyreformed-labs/shekel-plan``
(ruling ``balance:R-BAL170``): each step, finding, ruling and question is one
issue, a "card", and one board holds the steps and questions in the order the
developer drags them (``R-BAL177``).  Every write goes through the plan tool's
GitHub App, and every card a session files passes :mod:`check` first.

Whether a card SHIPPED is git's answer, never the card's state: a commit on
``dev`` carrying ``Ships: plan#N``, with no later ``Reopens: plan#N``
(:mod:`_git`).  The card's open or closed state is display, which ``sync``
writes from git.

Usage, from the repository root (``plan#N`` or ``N`` names a card)::

    python tools/plan/plan.py next [--arc ARC]
    python tools/plan/plan.py claim plan#N [--branch BRANCH]
    python tools/plan/plan.py release plan#N [--branch BRANCH | --unreadable]
    python tools/plan/plan.py show plan#N | OLD-ID
    python tools/plan/plan.py file step --arc ARC --title NAME --body-file SPEC [--parent plan#P]
    python tools/plan/plan.py file finding --arc ARC --title NAME --owner plan#S --text SENTENCE
    python tools/plan/plan.py file ruling --arc ARC --title NAME --owner plan#S
                                (--question-file Q | --from-question plan#Q) --answer-file A
    python tools/plan/plan.py file question --arc ARC --title NAME --body-file QUESTION
    python tools/plan/plan.py block plan#N --by plan#M [--remove]
    python tools/plan/plan.py move plan#N (--top | --bottom | --after plan#M)
    python tools/plan/plan.py drop plan#N --why REASON
    python tools/plan/plan.py sync [--dry-run]
    python tools/plan/plan.py spec-history plan#N [--since REF_OR_DATE]
    python tools/plan/plan.py spec-revert plan#N --to EDIT_ID

Exit status: 0 done; 1 refused, with nothing written (or, for ``sync``, a
REPORT a person must act on); 2 a call failed -- GitHub, git, the network, or
a file the command reads (the App's credentials, a ``--body-file``).  Every
write is printed as it lands, so after a failure the output says what was
written; ``file`` run again finishes a filing a failure cut short (R-BAL186).
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

import _git
from _github import GitHubError
from _state import (
    is_live,
    is_work,
    leaf_placement,
    missing,
    next_step,
    stale_claims,
    sync_plan,
)
from _tracker import Card, Claim, ClaimTaken, Tracker, TrackerError
from check import Draft, normalized, ruling_body, ruling_question, violations
from setup_tracker import ARCS

#: Branches a claim may never name: nothing is built on them directly.
_SHARED_BRANCHES = ("dev", "main")
#: The kinds of card the board holds, in the developer's order (R-BAL177).
ON_BOARD = ("step", "question")
_CARD_REF = re.compile(r"(?:plan#|#)?([1-9][0-9]*)")


class Refused(Exception):
    """A command that will not run as asked; its message says why."""


def card_number(text: str) -> int:
    """``plan#7``, ``#7`` or ``7`` -> 7."""
    found = _CARD_REF.fullmatch(text.strip())
    if not found:
        raise argparse.ArgumentTypeError(f"{text!r} is not a card: write plan#<number>")
    return int(found.group(1))


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


def _shipped(root: Path) -> tuple[_git.History, dict, tuple[_git.Trailer, ...]]:
    """``dev``'s card trailers, fetched fresh; the cards they say shipped; the stray
    ``Reopens:`` trailers (R-BAL181)."""
    _git.fetch(root)
    found = _git.history(root)
    return (found, *_git.shipped(root, found))


def _stray(trailer: _git.Trailer) -> str:
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


def _label(card: Card) -> str:
    """``plan#7 [step, balance] title``."""
    arcs = ", ".join(label for label in card.labels if label in ARCS) or "no arc"
    return f"plan#{card.number} [{card.kind or 'no type'}, {arcs}] {card.title}"


def _release_flag(claim: Claim) -> str:
    """The ``release`` option naming ``claim``'s holder, read or not."""
    return "--unreadable" if claim.branch is None else f"--branch {claim.branch}"


def _release_hint(claim: Claim) -> str:
    """The command that releases ``claim``, whether or not its branch could be read."""
    return f"`plan release plan#{claim.card} {_release_flag(claim)}`"


def _holder(claim: Claim) -> str:
    """Who holds ``claim``: its branch, quoted, or that its branch cannot be read."""
    return repr(claim.branch) if claim.branch is not None else "a branch that cannot be read"


# -- next, claim, release -----------------------------------------------------

def cmd_next(args, tracker: Tracker, root: Path) -> int:
    """The first open step in the board's order that is unclaimed, unshipped and unblocked."""
    _, shipped, _ = _shipped(root)
    cards = _with_closure(tracker, tracker.open_cards())
    claims = tracker.claims()
    order = [number for number, _ in tracker.board.order()]
    answer = next_step(order, cards, shipped, claims, args.arc)
    scope = f" in {args.arc}" if args.arc else ""
    print(f"next{scope}: {_label(answer.card)}" if answer.card else f"next{scope}: nothing")
    for card in answer.unplaced:
        print(f"NOT ON THE BOARD, so in no order: {_label(card)} -- place it with "
              f"`plan move plan#{card.number} --after plan#M` (or the board lags a fresh write)")
    for claim in stale_claims(claims, datetime.now(UTC), lambda b: _git.pushed(root, b)):
        print(f"STALE CLAIM: plan#{claim.card} by {_holder(claim)} since {claim.made or '?'}, "
              f"no pushed branch -- {_release_hint(claim)}")
    return 0


def cmd_claim(args, tracker: Tracker, root: Path) -> int:
    """Claim a card for the branch that will ship it."""
    card = _one(tracker, args.card)
    if not card.is_open or not is_work(card):
        raise Refused(f"{_label(card)} is not work a branch ships (open step or finding, "
                      "not a container)")
    branch = _branch(root, args.branch)
    try:
        claim = tracker.claim(card.number, branch)
    except ClaimTaken:
        held = tracker.claims().get(card.number)
        holder = f"{held.branch!r} since {held.made}" if held else "someone (just released?)"
        raise Refused(f"plan#{card.number} is already claimed by {holder}") from None
    print(f"claimed {_label(card)} for {claim.branch!r}")
    return 0


def cmd_release(args, tracker: Tracker, root: Path) -> int:
    """Delete a card's claim, only when it names the branch the caller names."""
    claim = tracker.claims().get(args.card)
    if claim is None:
        raise Refused(f"plan#{args.card} is not claimed")
    if args.unreadable:
        if claim.branch is not None:
            raise Refused(f"plan#{args.card}'s claim names {claim.branch!r}; --unreadable "
                          "releases only a claim whose branch cannot be read")
    elif claim.branch != (branch := args.branch or _git.current_branch(root)):
        raise Refused(f"plan#{args.card}'s claim names {_holder(claim)}, not {branch!r}; to "
                      f"release another branch's claim, pass {_release_flag(claim)}")
    tracker.release(args.card)
    print(f"released plan#{args.card} (claimed by {_holder(claim)} since {claim.made or '?'})")
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


def _card_lines(card: Card, tracker: Tracker, shipped: dict) -> list[str]:
    """What the tracker and git say about a card, one fact a line."""
    claim = tracker.claims().get(card.number)
    order = [n for n, _ in tracker.board.order()]
    state = "open" if card.is_open else f"closed ({card.state_reason})"
    ship = f"yes, {shipped[card.number][0].sha[:12]}" if card.number in shipped else "no"
    place = f"#{order.index(card.number) + 1}" if card.number in order else "not on it"
    lines = [
        _label(card),
        f"  state: {state}",
        f"  shipped (git): {ship}",
        f"  board: {place}",
        f"  claim: {claim.branch!r} since {claim.made}" if claim else "  claim: none",
        f"  parent: plan#{card.parent}" if card.parent else "  parent: none",
    ]
    lines += [f"  child: plan#{child.number} ({child.kind}, "
              f"{'open' if child.is_open else 'closed'})" for child in card.children]
    lines += [f"  blocked by: plan#{blocker}" for blocker in card.blocked_by]
    return lines


def cmd_show(args, tracker: Tracker, root: Path) -> int:
    """A card, then the code repository's history for it."""
    number = _resolve_reference(tracker, args.card)
    card = _one(tracker, number)
    found, shipped, strays = _shipped(root)
    print("\n".join(_card_lines(card, tracker, shipped)))
    print()
    print(tracker.body(number))
    print()
    history = [t for t in found.trailers if t.card == number]
    for trailer in history:
        print(f"{trailer.key}: {trailer.sha[:12]} {trailer.subject}")
    if not history:
        print(f"no commit on {_git.DEV} carries Ships: or Reopens: plan#{number}")
    for trailer in strays:
        if trailer.card == number:
            print(f"STRAY: {_stray(trailer)}")
    return 0


# -- file -----------------------------------------------------------------------

def _read(path: str) -> str:
    """A file's text (a spec, a question, an answer)."""
    return Path(path).read_text(encoding="utf-8")


def _draft_for(args, tracker: Tracker, root: Path) -> tuple[Draft, Card | None]:
    """The card ``file`` would write, and its parent (its owner, or the step it splits).

    Whether the parent is still LIVE is git's answer and the cards', read the
    way ``next`` reads a step (:func:`_state.is_live`), never the card's open
    state alone, which is display.  An answered question already rewritten into
    a ruling's shape by a conversion cut short gives back its question, so the
    retry never wraps it twice (R-BAL186).
    """
    parent_number = getattr(args, "owner", None) or getattr(args, "parent", None)
    parent = _one(tracker, parent_number) if parent_number else None
    live = False
    if parent is not None:
        _, shipped, _ = _shipped(root)
        cards = _with_closure(tracker, {parent.number: parent})
        live = is_live(parent.number, cards, shipped)
    if args.kind in ("step", "question"):
        body = _read(args.body_file)
    elif args.kind == "finding":
        body = args.text
    elif args.from_question:
        asked = tracker.body(_one(tracker, args.from_question).number)
        question = ruling_question(asked)
        body = ruling_body(asked if question is None else question, _read(args.answer_file))
    else:
        body = ruling_body(_read(args.question_file), _read(args.answer_file))
    labels = tuple(dict.fromkeys((args.arc, *(getattr(args, "label", None) or ()))))
    draft = Draft(args.kind, args.title, body, labels,
                  owner_kind=parent.kind if parent else None, owner_live=live)
    return draft, parent


def _place_leaf(tracker: Tracker, leaf: Card, parent: Card) -> None:
    """Put a new leaf where the step it splits sat (R-BAL179), each board write printed as
    it lands; a leaf already placed is left where it is (R-BAL186)."""
    if leaf.board_item is not None and parent.board_item is None:
        print("  board: already on it")
        return
    where = leaf_placement(tracker.board.order(), parent, leaf.number)
    item = leaf.board_item
    if item is None:
        item = tracker.board.add(leaf)
        print("  board: added at the bottom")
    if where.after is not None:
        shown = tracker.board.place(item, where.after)
        print(f"  board: moved {where.note}" + ("" if shown else
                                                " (the board has not shown it yet)"))
    if where.remove is not None:
        tracker.board.remove(where.remove)
        print(f"  board: plan#{parent.number} left it, as a container")


def _half_filed(tracker: Tracker, draft: Draft) -> Card | None:
    """The OPEN card with ``draft``'s kind, title and text, if one exists: a filing a
    failure cut short, which the same command finishes instead of filing another
    (R-BAL186).  Refused when its labels differ, or when two such cards exist."""
    same = [
        card for card in tracker.open_cards().values()
        if card.kind == draft.kind and card.title.strip() == draft.title.strip()
        and normalized(tracker.body(card.number)) == normalized(draft.body)
    ]
    if len(same) > 1:
        raise Refused(f"{len(same)} open cards have this kind, title and text "
                      f"({', '.join(f'plan#{card.number}' for card in same)}): drop the extras")
    if same and sorted(same[0].labels) != sorted(draft.labels):
        raise Refused(f"{_label(same[0])} has this kind, title and text but the labels "
                      f"{sorted(same[0].labels)}, not {sorted(draft.labels)}: relabel it by hand")
    return same[0] if same else None


def _attach(tracker: Tracker, card: Card, parent: Card) -> None:
    """Make ``card`` a sub-issue of ``parent``, unless it already is one."""
    if card.parent == parent.number:
        print(f"  already a sub-issue of plan#{parent.number}")
        return
    if card.parent is not None:
        raise Refused(f"{_label(card)} is a sub-issue of plan#{card.parent}, not "
                      f"plan#{parent.number}: re-homing a card is done by hand")
    tracker.add_child(parent.number, card)
    print(f"  a sub-issue of plan#{parent.number}")


def cmd_file(args, tracker: Tracker, root: Path) -> int:
    """File a step, finding, ruling or question, after :func:`check.violations` passes;
    finish one a failure cut short (R-BAL186)."""
    draft, parent = _draft_for(args, tracker, root)
    problems = violations(draft)
    if problems:
        raise Refused("not filed:\n  " + "\n  ".join(problems))
    if args.kind == "ruling" and args.from_question:
        return _convert_question(args, tracker, draft, parent)
    card = _half_filed(tracker, draft)
    if card is None:
        card = _one(tracker, tracker.create(args.kind, draft.title, draft.body, draft.labels))
        print(f"filed {_label(card)}")
    else:
        print(f"finishing {_label(card)}: an open card with this kind, title and text exists, "
              "so this filing finishes it rather than filing another (R-BAL186)")
    if parent is not None:
        _attach(tracker, card, parent)
    if args.kind == "ruling":
        tracker.close(card.number, "completed")
        print("  closed: a ruling is a record")
    elif args.kind == "step" and parent is not None:
        _place_leaf(tracker, card, parent)
    elif args.kind in ON_BOARD and card.board_item is None:
        tracker.board.add(card)
        print("  board: added at the bottom")
    elif args.kind in ON_BOARD:
        print("  board: already on it")
    return 0


def _convert_question(args, tracker: Tracker, draft: Draft, owner: Card) -> int:
    """An answered question becomes its ruling: one card, so the question is never copied.

    Each write is skipped when it already landed, so the same command finishes a
    conversion a failure cut short (R-BAL186): a card already typed a ruling but
    still open is one.  Its parent is checked before anything is written.
    """
    question = _one(tracker, args.from_question)
    if not question.is_open or question.kind not in ("question", "ruling"):
        raise Refused(f"{_label(question)} is not an open question")
    if args.arc not in question.labels:
        raise Refused(f"{_label(question)} is not in the {args.arc} arc; pass its own --arc")
    if question.parent not in (None, owner.number):
        raise Refused(f"{_label(question)} is a sub-issue of plan#{question.parent}, not "
                      f"plan#{owner.number}: re-homing a card is done by hand")
    if question.kind == "ruling":
        print(f"finishing {_label(question)}: its conversion into a ruling was cut short "
              "(R-BAL186)")
    if normalized(tracker.body(question.number)) != normalized(draft.body):
        tracker.set_body(question.number, draft.body)
        print(f"  plan#{question.number}'s body: the question, then the answer")
    if question.kind != "ruling":
        tracker.retype(question.number, "ruling")
        print(f"  plan#{question.number} is now a ruling")
    if draft.title.strip() != question.title.strip():
        tracker.retitle(question.number, draft.title)
        print(f"  retitled {draft.title!r}")
    _attach(tracker, question, owner)
    if question.board_item is not None:
        tracker.board.remove(question.board_item)
        print("  board: taken off it (a ruling is not in the order)")
    tracker.close(question.number, "completed")
    print("  closed: a ruling is a record")
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


def cmd_move(args, tracker: Tracker, _root: Path) -> int:
    """Put a step or question at a place in the board's order."""
    card = _one(tracker, args.card)
    if card.kind not in ON_BOARD or not card.is_open:
        raise Refused(f"the board holds open steps and questions only (R-BAL177): {_label(card)}")
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
    item = items.get(card.number) or tracker.board.add(card)
    shown = tracker.board.place(item, after)
    print(f"moved {_label(card)}" + ("" if shown else
                                     " -- the board has not shown it yet; re-read it later"))
    return 0


def cmd_drop(args, tracker: Tracker, _root: Path) -> int:
    """Retire a card with no code: the reason as a comment, then closed as not planned."""
    card = _one(tracker, args.card)
    if not card.is_open:
        raise Refused(f"{_label(card)} is already closed")
    tracker.comment(card.number, f"Dropped: {args.why}")
    print(f"  commented: Dropped: {args.why}")
    tracker.close(card.number, "not_planned")
    print(f"dropped {_label(card)}")
    leaves = [f"plan#{child.number}" for child in card.children
              if child.kind == "step" and child.is_open]
    if leaves:
        print(f"  its open leaves {', '.join(leaves)} are no longer offered: a leaf inherits "
              "the drop of a step above it (R-BAL185)")
    claim = tracker.claims().get(card.number)
    if claim is not None:
        print(f"  its claim by {_holder(claim)} stays: {_release_hint(claim)}")
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


def cmd_sync(args, tracker: Tracker, root: Path) -> int:
    """Write each step's and finding's open or closed state from git (display only).

    Prints a REPORT for each thing a person must fix in the tracker, which makes
    it exit 1, and a HISTORY line for each thing a commit already on ``dev`` says
    wrongly, which no tracker write can change and so never fails it (R-BAL184).
    """
    found, shipped, strays = _shipped(root)
    named = {trailer.card for trailer in found.trailers}
    cards = tracker.open_cards()
    cards.update(tracker.cards(named - set(cards)))
    history = [f"a trailer on {_git.DEV} names plan#{number}, which does not exist"
               for number in sorted(named - set(cards))]
    cards = _with_closure(tracker, cards)
    changes = sync_plan(cards, shipped, tracker.claims(),
                        _ship_branches(tracker, root, cards, shipped))
    verb = "would " if args.dry_run else ""
    for number in changes.close:
        if not args.dry_run:
            tracker.close(number, "completed")
        print(f"{verb}close {_label(cards[number])}")
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
    return 1 if changes.reports else 0


# -- spec-history, spec-revert ------------------------------------------------------

def _since(root: Path, text: str) -> datetime:
    """``--since``: an ISO date or time, or a git ref (when its commit was first written)."""
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        when = datetime.fromisoformat(_git.author_date(root, text))
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
                      f"`plan spec-history plan#{args.card}` lists them")
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
    top = argparse.ArgumentParser(prog="plan", description=__doc__.splitlines()[0])
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
    history.add_argument("--since", help="an ISO date or time, or a git ref")
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
        return COMMANDS[args.command](args, connect(), root or _git.repository_root())
    except Refused as refused:
        print(f"refused: {refused}", file=sys.stderr)
        return 1
    except (GitHubError, TrackerError, _git.GitError, requests.RequestException,
            OSError) as error:
        print(f"failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
