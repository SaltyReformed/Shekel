"""The ``plan file`` command: a step, finding, ruling or question filed after
:func:`check.violations` passes, a filing a failure cut short finished by the same
command (R-BAL186), and nothing written over one that finished -- ruling
``balance:R-BAL202``'s mark, its leaf rule (R-BAL204) and ruling closes (R-BAL206).
``plan.py``'s module docstring is the command's usage.

Split out of ``plan.py`` (X-cx L2, leaf C), which leaf C's change would otherwise take
past the 1,000 lines ``too-many-lines`` allows.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from _command import (
    ON_BOARD,
    Refused,
    _label,
    _one,
    _outside_parent,
    _shipped,
    _unfinished,
    _with_closure,
)
from _state import filing_unfinished, is_live, leaf_placement
from _tracker import Card, Tracker, TrackerError
from check import (
    Draft,
    Owner,
    in_ruling_shape,
    normalized,
    ruling_body,
    ruling_question,
    violations,
)
from setup_tracker import FILING


def _read(path: str) -> str:
    """A file's text (a spec, a question, an answer)."""
    return Path(path).read_text(encoding="utf-8")


@dataclass(frozen=True)
class _Asked:
    """The question card a ruling is converted from, its body as read, and whether an EDIT
    of the plan tool's (not its filing) ever saved it in a ruling's shape -- an earlier
    conversion's work, whoever saved the card since."""

    card: Card
    body: str
    converted_before: bool


def _converted_body(asked: _Asked, answer: str) -> str:
    """The body an answered question takes as its ruling: its question, then ``answer``.

    A conversion cut short may already have written it (R-BAL186), so a body
    that is exactly what this conversion writes gives back its question, never
    wrapped twice.  A body in a ruling's shape with ANOTHER answer, on a card an
    earlier conversion once rewrote (an edit of the tool's saved a ruling's
    shape), is that conversion's work -- touched up since or not -- not the
    developer's question, and is refused, as is a card already typed a ruling
    with another answer: an answer is the developer's record, and another answer
    is another ruling.  Any other body is the developer's question, word for
    word, whatever marks it holds -- so a question quoting a ruling's shape on a
    card the tool once rewrote is refused too, and is put back on the web
    instead.
    """
    question = ruling_question(asked.body, answer)
    if question is None and asked.card.kind == "ruling":
        raise Refused(f"{_label(asked.card)} is already a ruling, and not with this answer: "
                      "its answer is the developer's record, so another answer is another ruling")
    if question is None and asked.converted_before and in_ruling_shape(asked.body):
        number = asked.card.number
        raise Refused(f"{_label(asked.card)}'s text is an earlier conversion's, with another "
                      f"answer: put the developer's question back first (`plan spec-history "
                      f"plan#{number}` lists its saved versions, `plan spec-revert plan#{number} "
                      "--to EDIT_ID` restores one), then run this again")
    return ruling_body(asked.body if question is None else question, answer)


def _draft_for(args, tracker: Tracker, root: Path) -> tuple[Draft, Card | None, _Asked | None]:
    """The card ``file`` would write; its parent (its owner, or the step it splits); and,
    for a ruling converted from a question, that question as read.

    Whether the parent is still LIVE is git's answer and the cards', read the
    way ``next`` reads a step (:func:`_state.is_live`), never the card's open
    state alone, which is display.
    """
    parent_number = getattr(args, "owner", None) or getattr(args, "parent", None)
    parent = _one(tracker, parent_number) if parent_number else None
    if args.kind == "step" and parent is not None and filing_unfinished(parent):
        raise Refused(f"{_label(parent)}'s own filing has not finished (R-BAL202): finish it "
                      "with the same `plan file` command first, then split it -- a leaf under "
                      "it would make it a split step its own filing puts on the board")
    owner = None
    if parent is not None:
        _, shipped, _ = _shipped(root)
        cards = _with_closure(tracker, {parent.number: parent})
        owner = Owner(parent.kind, is_live(parent.number, cards, shipped))
    asked = None
    if args.kind in ("step", "question"):
        body = _read(args.body_file)
    elif args.kind == "finding":
        body = args.text
    elif args.from_question:
        card = _one(tracker, args.from_question)
        text, versions = tracker.edits(card.number)
        asked = _Asked(card, text, any(version.editor == tracker.app_login
                                       and in_ruling_shape(version.body)
                                       for version in versions[1:]))
        body = _converted_body(asked, _read(args.answer_file))
    else:
        body = ruling_body(_read(args.question_file), _read(args.answer_file))
    labels = tuple(dict.fromkeys((args.arc, *(getattr(args, "label", None) or ()))))
    return Draft(args.kind, args.title, body, labels, owner), parent, asked


def _place_leaf(tracker: Tracker, leaf: Card, parent: Card) -> None:
    """Put a leaf where the step it splits sat (R-BAL179), each board write printed as it
    lands: onto the board if it is not on it, moved to its place, and the split step off
    the board if it is still on it.  A leaf is placed only while its filing is unfinished
    (R-BAL202), so its move is made again on every run until its last write lands."""
    where = leaf_placement(tracker.board.order(), parent, leaf.number)
    item = leaf.board_item
    if item is None:
        item = tracker.board.add(leaf)
        print("  board: added at the bottom")
    if where.move:
        shown = tracker.board.place(item, where.after)
        print(f"  board: moved {where.note}" + ("" if shown else
                                                " (the board has not shown it yet)"))
    else:
        print(f"  board: not moved: {where.note}")
    if where.remove is not None:
        tracker.board.remove(where.remove)
        print(f"  board: plan#{parent.number} left it, as a container")


def _half_filed(tracker: Tracker, draft: Draft, unfinished: dict[int, Card]) -> Card | None:
    """The card with ``draft``'s kind, title and text, if one exists: an OPEN card, or one
    in ``unfinished`` (a ruling closed as completed, by its filing before its last write
    or by anyone, R-BAL206).  The caller
    finishes a filing cut short and files nothing over a finished one (R-BAL186,
    R-BAL202).  Refused when its labels differ, the filing mark aside, or when two such
    cards exist."""
    candidates = {**tracker.open_cards(), **unfinished}
    same = [
        card for _, card in sorted(candidates.items())
        if card.kind == draft.kind and card.title.strip() == draft.title.strip()
        and normalized(tracker.body(card.number)) == normalized(draft.body)
    ]
    if len(same) > 1:
        raise Refused(f"{len(same)} cards, open or unfinished, have this kind, title and text "
                      f"({', '.join(f'plan#{card.number}' for card in same)}): drop the extras")
    labels = sorted(label for label in same[0].labels if label != FILING) if same else None
    if same and labels != sorted(draft.labels):
        raise Refused(f"{_label(same[0])} has this kind, title and text but the labels "
                      f"{labels}, not {sorted(draft.labels)}: relabel it by hand")
    return same[0] if same else None


def _refuse_rehoming(card: Card, parent: Card | None) -> None:
    """Refuse to file ``card`` under ``parent`` (None: at the top level) when it is a
    sub-issue of another card, in the tracker or outside it: re-homing a card is a
    person's call."""
    if outside := _outside_parent(card):
        raise Refused(f"{_label(card)} is a sub-issue of {outside}, outside the tracker: "
                      "re-homing a card is done by hand")
    if card.parent is not None and card.parent != (parent.number if parent else None):
        where = f"plan#{parent.number}" if parent else "a top-level card, as this filing names"
        raise Refused(f"{_label(card)} is a sub-issue of plan#{card.parent}, not {where}: "
                      "re-homing a card is done by hand")


def _attach(tracker: Tracker, card: Card, parent: Card | None) -> None:
    """Make ``card`` a sub-issue of ``parent`` (None: none), unless it already is one;
    the caller has already refused a card under another parent."""
    if parent is None:
        return
    if card.parent == parent.number:
        print(f"  already a sub-issue of plan#{parent.number}")
        return
    tracker.add_child(parent.number, card)
    print(f"  a sub-issue of plan#{parent.number}")


def _refuse_beside_unfinished(tracker: Tracker, parent: Card, leaf: Card | None) -> None:
    """Refuse to file a leaf of ``parent`` -- a new one, or ``leaf``'s filing again -- while
    another leaf of ``parent`` has an unfinished filing (R-BAL204): its place is not
    known to be right, so no leaf is placed by it.  Each leaf's mark is read by its card
    number, not from the listing of marked cards, which may lag its removal."""
    others = [card for number, card in sorted(tracker.cards(parent.leaves).items())
              if filing_unfinished(card) and (leaf is None or number != leaf.number)]
    if others:
        names = ", ".join(f"plan#{card.number}" for card in others)
        raise Refused(f"the filing of {names}, of plan#{parent.number}'s leaves, has not "
                      "finished (R-BAL204): finish it first, then run this again.  Unless a "
                      "`plan file` command is filing it now, run that command again; with that "
                      "command lost, or the card's title or text changed since, make its "
                      f"missing writes on the web and remove its {FILING!r} label last "
                      "(`plan show` shows its parent and board place)")


def _filed_already(card: Card, parent: Card | None) -> int:
    """Nothing is written over a filing that finished: ``card`` carries no filing mark, so
    each of its filing's writes landed (R-BAL202), and a person may have moved or edited it
    since.  Refused when it sits at the top level and this filing names ``parent``:
    re-homing a card is a person's call (one under another parent the caller has already
    refused, :func:`_refuse_rehoming`)."""
    if card.parent is None and parent is not None:
        raise Refused(f"{_label(card)} has this kind, title and text, and its filing finished "
                      f"at the top level, not under plan#{parent.number}: re-homing a card is "
                      "done by hand")
    print(f"{_label(card)} is filed already: a card with this kind, title and text exists "
          "and its filing finished (it carries no filing mark), so nothing is written "
          "(R-BAL186, R-BAL202)")
    return 0


def cmd_file(args, tracker: Tracker, root: Path) -> int:
    """File a step, finding, ruling or question, after :func:`check.violations` passes;
    finish one a failure cut short (R-BAL186).

    Every card is created marked, :data:`setup_tracker.FILING` sent in the call
    that creates it (by :meth:`_tracker.Tracker.create`, never through ``--label``,
    so :func:`check.violations` never sees it), and the mark is removed last
    (R-BAL202).  So a card still marked is
    a filing some of whose writes may not have landed: run again, the same command
    makes every write of the filing that the card does not show done -- the link,
    the close, the board place, and for a leaf the move whenever it has a place to
    go, since no read can tell the tool's move from a person's drag -- then
    removes the mark.
    """
    draft, parent, asked = _draft_for(args, tracker, root)
    problems = violations(draft)
    if problems:
        raise Refused("not filed:\n  " + "\n  ".join(problems))
    if asked is not None:
        return _convert_question(args, tracker, draft, parent, asked)
    card = _half_filed(tracker, draft, _unfinished(tracker))
    if card is not None:
        fresh = tracker.cards([card.number]).get(card.number)
        if fresh is None:
            raise TrackerError(f"plan#{card.number} is listed, but a read by its number does not "
                               "hold it (that read has not caught up yet, or the card was deleted "
                               "or moved since): run the same command again")
        card = fresh
        _refuse_rehoming(card, parent)
        if not filing_unfinished(card):
            return _filed_already(card, parent)
    if args.kind == "step" and parent is not None:
        _refuse_beside_unfinished(tracker, parent, card)
    if card is None:
        card = _created(args.kind, tracker, draft)
    else:
        print(f"finishing {_label(card)}: its filing has not finished (it is still marked "
              f"{FILING!r}), so this command finishes it rather than filing another (R-BAL186, "
              "R-BAL202)")
    _finish(args.kind, tracker, card, parent)
    return 0


def _created(kind: str, tracker: Tracker, draft: Draft) -> Card:
    """A new card filed from ``draft``, marked in the call that creates it (R-BAL202), as
    read back; a card that cannot be read back yet is a failed call, not a refusal: it
    was filed."""
    number = tracker.create(kind, draft.title, draft.body, draft.labels)
    print(f"filed plan#{number}")
    card = tracker.cards([number]).get(number)
    if card is None:
        raise TrackerError(f"plan#{number} was filed but cannot be read back yet: once "
                           f"`plan show plan#{number}` reads it, run the same command again "
                           "to finish it (R-BAL186, R-BAL202); run while GitHub's lists of "
                           "open and marked cards do not hold it yet, it files a second card")
    print(f"  {_label(card)}")
    return card


def _finish(kind: str, tracker: Tracker, card: Card, parent: Card | None) -> None:
    """Every write of a filing after the create that ``card`` does not show done, each
    printed as it lands, then its mark removed, last (R-BAL202): the link, a ruling's
    close, a leaf's place, a top-level step's or question's board place."""
    _attach(tracker, card, parent)
    if kind == "ruling" and card.is_open:
        tracker.close(card.number, "completed")
        print("  closed: a ruling is a record")
    elif kind == "ruling":
        print("  closed already")
    elif kind == "step" and parent is not None:
        _place_leaf(tracker, card, parent)
    elif kind in ON_BOARD and card.board_item is None:
        tracker.board.add(card)
        print("  board: added at the bottom")
    elif kind in ON_BOARD:
        print("  board: already on it")
    tracker.unmark(card.number)
    print(f"  unmarked: plan#{card.number}'s filing is finished")


def _convert_question(args, tracker: Tracker, draft: Draft, owner: Card, asked: _Asked) -> int:
    """An answered question becomes its ruling: one card, so the question is never copied.

    Each write is skipped when it already landed, so the same command finishes a
    conversion a failure cut short (R-BAL186): a card already typed a ruling but
    still open, with this answer, is one.  Its parent is checked before anything
    is written.
    """
    question = asked.card
    if not question.is_open or question.kind not in ("question", "ruling"):
        raise Refused(f"{_label(question)} is not an open question")
    if filing_unfinished(question):
        raise Refused(f"{_label(question)}'s filing has not finished (R-BAL202): finish it with "
                      "the `plan file` command that filed it first, then convert it")
    if args.arc not in question.labels:
        raise Refused(f"{_label(question)} is not in the {args.arc} arc; pass its own --arc")
    _refuse_rehoming(question, owner)
    if question.kind == "ruling":
        print(f"finishing {_label(question)}: its conversion into a ruling was cut short "
              "(R-BAL186)")
    if normalized(asked.body) != normalized(draft.body):
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
