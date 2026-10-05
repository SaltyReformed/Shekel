"""An in-memory tracker for the ``plan`` command's tests, and the helpers they share.

``_tracker`` is graded against RECORDED GitHub answers (``test__tracker.py``);
the command's tests grade what each command decides to read and write, so they
drive the commands against :class:`FakeTracker`, which keeps cards in memory.
It keeps what the commands depend on the way the recordings show GitHub keeping
it -- a body edit saves a full version, and the first edit also saves the body
as filed; a parent's read lists each sub-issue's kind and state as they stand
now, not as they were when it was linked -- and FAILS LOUDLY on a write the
tool must never send (re-parenting a card, under a parent inside the tracker
or out; adding a card already on the board; removing a mark the card does not
carry), rather than guessing GitHub's answer to it.  Its board shows a
placement at once unless told to lag (:attr:`FakeBoard.lagging`); the lag
itself is graded against a recording.
Nothing here calls GitHub.
"""
from __future__ import annotations

import dataclasses

import plan
from _github import GitHubError
from _scratch import run as _run
from _tracker import Card, Child, Claim, ClaimTaken, Edit
from setup_tracker import FILING


class FakeBoard:
    """The board in memory: card numbers in drag order; its writes go to the tracker's log."""

    def __init__(self, writes):
        """An empty board logging into ``writes``; it shows each placement at once."""
        self.items: list[int] = []
        self.writes = writes
        self.lagging = False

    def order(self):
        """The board's order: ``(card number, item id)``."""
        return [(n, f"PVTI_{n}") for n in self.items]

    def add(self, card):
        """Add a card at the bottom; the tool never adds one already on the board."""
        assert card.number not in self.items, f"plan#{card.number} is already on the board"
        self.writes.append(("board_add", card.number))
        self.items.append(card.number)
        return f"PVTI_{card.number}"

    def remove(self, item):
        """Take a card off the board."""
        number = int(item.removeprefix("PVTI_"))
        self.writes.append(("board_remove", number))
        self.items.remove(number)

    def place(self, item, after):
        """Move an item to just after ``after`` (the top when None); whether the board
        shows it there (not while :attr:`lagging`)."""
        number = int(item.removeprefix("PVTI_"))
        self.writes.append(("board_place", number, after))
        self.items.remove(number)
        index = 0 if after is None else self.items.index(int(after.removeprefix("PVTI_"))) + 1
        self.items.insert(index, number)
        return not self.lagging



class FakeTracker:  # pylint: disable=too-many-public-methods
    """The tracker in memory: cards, bodies, claims, its board, and every write.

    Pylint: ``too-many-public-methods`` (22/20) -- it stands in for
    :class:`_tracker.Tracker`, so it has each of that class's 21 reads and
    writes (its own disable says why there are so many), and ``add``, which
    puts a card in.
    """

    app_login = "shekel-plan-tool"

    def __init__(self):
        """An empty tracker."""
        self.cards_by_number: dict[int, Card] = {}
        self.bodies: dict[int, str] = {}
        self.versions: dict[int, list[Edit]] = {}
        self.held: dict[int, Claim] = {}
        self.pulls: dict[str, set[str]] = {}
        self.writes: list[tuple] = []
        self.board = FakeBoard(self.writes)

    def add(self, number, kind="step", body="", on_board=True, **fields):
        """Put a card in the tracker (open, unclaimed, by default on the board's bottom)."""
        values = {"number": number, "id": 1000 + number, "node_id": f"I_{number}",
                  "title": f"card {number}", "kind": kind, "labels": ("balance",),
                  "is_open": True, "state_reason": None, "parent": None, "children": (),
                  "blocked_by": (), "board_item": None, "closed_by_tool": False,
                  "touched_by_hand": False, "outside": (), **fields}
        self.cards_by_number[number] = Card(**values)
        self.bodies[number] = body
        if on_board and values["is_open"] and kind in plan.ON_BOARD:
            self.board.items.append(number)
        return self.cards_by_number[number]

    def _set(self, number, **fields):
        """Change a held card."""
        self.cards_by_number[number] = dataclasses.replace(self.cards_by_number[number], **fields)

    def _view(self, number):
        """A card as a read returns it: its board item filled in, and each sub-issue it
        holds listed with that card's kind and state as they stand."""
        card = self.cards_by_number[number]
        item = f"PVTI_{number}" if number in self.board.items else None
        children = tuple(
            Child(child.number, held.kind, held.is_open)
            if (held := self.cards_by_number.get(child.number)) else child
            for child in card.children
        )
        return dataclasses.replace(card, board_item=item, children=children)

    # -- reads
    def open_cards(self):
        """Every open card."""
        return {n: self._view(n) for n, c in self.cards_by_number.items() if c.is_open}

    def marked(self):
        """Every card carrying the filing mark, open or closed."""
        return {n: self._view(n) for n, c in self.cards_by_number.items() if FILING in c.labels}

    def cards(self, numbers):
        """The cards that exist among ``numbers``."""
        return {n: self._view(n) for n in numbers if n in self.cards_by_number}

    def body(self, number):
        """A card's body."""
        return self.bodies[number]

    def claims(self):
        """Every claim."""
        return dict(self.held)

    def edits(self, number):
        """A card's body and its versions (the body as filed when it was never edited)."""
        versions = self.versions.get(number) or [
            Edit(None, "2026-10-01T00:00:00Z", self.app_login, self.bodies[number])]
        return self.bodies[number], list(versions)

    def find_titles(self, text):
        """Cards whose title holds ``text``."""
        return [(n, c.title) for n, c in self.cards_by_number.items() if text in c.title]

    def merged_into_dev(self, _repository, sha):
        """The branches recorded as having merged ``sha`` into dev."""
        return self.pulls.get(sha, set())

    # -- writes
    def create(self, kind, title, body, labels):
        """File a card at the next number."""
        number = max(self.cards_by_number, default=0) + 1
        self.writes.append(("create", number, kind, title, tuple(sorted(labels))))
        self.add(number, kind, body, on_board=False, title=title, labels=tuple(sorted(labels)))
        return number

    def retype(self, number, kind):
        """Change a card's kind."""
        self.writes.append(("retype", number, kind))
        self._set(number, kind=kind)

    def retitle(self, number, title):
        """Rename a card."""
        self.writes.append(("retitle", number, title))
        self._set(number, title=title)

    def set_body(self, number, body):
        """Replace a card's body, saving a version as GitHub does: the full body, and on
        the first edit the body as filed before it (measured 2026-10-04)."""
        self.writes.append(("set_body", number, body))
        versions = self.versions.setdefault(number, [])
        if not versions:
            versions.append(Edit(f"E{number}.0", "2026-10-04T00:00:00Z", self.app_login,
                                 self.bodies[number]))
        versions.append(Edit(f"E{number}.{len(versions)}", "2026-10-04T00:00:01Z",
                             self.app_login, body))
        self.bodies[number] = body

    def comment(self, number, text):
        """Comment on a card."""
        self.writes.append(("comment", number, text))

    def close(self, number, reason):
        """Close a card as the tool."""
        self.writes.append(("close", number, reason))
        self._set(number, is_open=False, state_reason=reason.upper(), closed_by_tool=True,
                  touched_by_hand=False)

    def reopen(self, number):
        """Reopen a card as the tool."""
        self.writes.append(("reopen", number))
        self._set(number, is_open=True, state_reason="REOPENED", closed_by_tool=False,
                  touched_by_hand=False)

    def unmark(self, number):
        """Remove a card's filing mark; the tool never removes one the card does not carry
        (what GitHub answers to that is not measured)."""
        labels = self.cards_by_number[number].labels
        assert FILING in labels, f"plan#{number} carries no {FILING!r} mark"
        self.writes.append(("unmark", number))
        self._set(number, labels=tuple(label for label in labels if label != FILING))

    def add_child(self, parent, child):
        """Make ``child`` a sub-issue of ``parent``; the tool never re-parents a card, in the
        tracker or outside it."""
        held = self.cards_by_number[child.number]
        assert held.parent is None, f"plan#{child.number} already has parent plan#{held.parent}"
        assert not [link for link in held.outside if link.what == "parent"], (
            f"plan#{child.number} already has a parent outside the tracker")
        self.writes.append(("add_child", parent, child.number))
        held = self.cards_by_number[parent]
        self._set(parent, children=(*held.children, Child(child.number, child.kind, True)))
        self._set(child.number, parent=parent)

    def block(self, number, blocker):
        """Record a blocked-by edge."""
        self.writes.append(("block", number, blocker.number))
        held = self.cards_by_number[number]
        self._set(number, blocked_by=(*held.blocked_by, blocker.number))

    def unblock(self, number, blocker):
        """Remove a blocked-by edge."""
        self.writes.append(("unblock", number, blocker.number))
        held = self.cards_by_number[number]
        self._set(number, blocked_by=tuple(b for b in held.blocked_by if b != blocker.number))

    def claim(self, number, branch):
        """Claim a card; refused when held."""
        if number in self.held:
            raise ClaimTaken(f"plan#{number} is already claimed")
        self.writes.append(("claim", number, branch))
        self.held[number] = Claim(number, branch, "2026-10-04T12:00:00Z", f"sha{number}")
        return self.held[number]

    def release(self, number):
        """Delete a claim."""
        self.writes.append(("release", number))
        del self.held[number]


class FailOnce:
    """One write of ``owner`` (the tracker or its board) failing the first time, as a timeout
    or a 502 would; it writes every time after."""

    def __init__(self, owner, name):
        """Stand in for ``owner.<name>``."""
        self.real, self.failed = getattr(owner, name), False
        setattr(owner, name, self)

    def __call__(self, *args):
        """Fail once, then write."""
        if not self.failed:
            self.failed = True
            raise GitHubError(502, "bad gateway")
        return self.real(*args)


class ReadBackFailsOnce:
    """``board.place`` whose move lands and whose read of the board back then fails once, as
    one of :meth:`_tracker.Board.shows`'s reads can."""

    def __init__(self, board):
        """Stand in for ``board.place``."""
        self.real, self.failed = board.place, False
        board.place = self

    def __call__(self, item, after):
        """Move, then fail once."""
        shown = self.real(item, after)
        if not self.failed:
            self.failed = True
            raise GitHubError(502, "bad gateway")
        return shown


def ship(root, *trailers):
    """Put a commit carrying ``trailers`` on origin/dev; its sha."""
    tree = _run(root, "hash-object", "-t", "tree", "/dev/null")
    parent = _run(root, "rev-parse", "refs/remotes/origin/dev")
    sha = _run(root, "commit-tree", tree, "-p", parent, "-m", "leaf", "-m", "\n".join(trailers))
    _run(root, "update-ref", "refs/remotes/origin/dev", sha)
    return sha



def run(tracker, root, *argv):
    """Run one command against ``tracker`` and ``root``; its exit status."""
    return plan.main(list(argv), connect=lambda: tracker, root=root)
