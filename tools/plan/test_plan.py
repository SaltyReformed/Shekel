"""The ``plan`` command's decisions, over an in-memory tracker and a throwaway git repository.

``_tracker`` is graded against RECORDED GitHub answers (``test__tracker.py``);
this file grades what each command decides to read and write, so it drives
the commands against :class:`FakeTracker`, which keeps cards in memory.  It
keeps what the commands depend on the way the recordings show GitHub keeping
it -- a body edit saves a full version, and the first edit also saves the body
as filed -- and FAILS LOUDLY on a write the tool must never send (re-parenting
a card, adding a card already on the board), rather than guessing GitHub's
answer to it.  Its board shows a placement at once unless told to lag
(:attr:`FakeBoard.lagging`); the lag itself is graded against a recording.
Nothing here calls GitHub.
"""
from __future__ import annotations

import dataclasses

import pytest
import requests

import _git
import plan
from _github import GitHubError
from _scratch import run as _run
from _tracker import Card, Child, Claim, ClaimTaken, Edit
from check import ruling_body


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


class FakeTracker:
    """The tracker in memory: cards, bodies, claims, its board, and every write."""

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
                  "touched_by_hand": False, **fields}
        self.cards_by_number[number] = Card(**values)
        self.bodies[number] = body
        if on_board and values["is_open"] and kind in plan.ON_BOARD:
            self.board.items.append(number)
        return self.cards_by_number[number]

    def _set(self, number, **fields):
        """Change a held card."""
        self.cards_by_number[number] = dataclasses.replace(self.cards_by_number[number], **fields)

    def _view(self, number):
        """A card as a read returns it, its board item filled in."""
        item = f"PVTI_{number}" if number in self.board.items else None
        return dataclasses.replace(self.cards_by_number[number], board_item=item)

    # -- reads
    def open_cards(self):
        """Every open card."""
        return {n: self._view(n) for n, c in self.cards_by_number.items() if c.is_open}

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

    def add_child(self, parent, child):
        """Make ``child`` a sub-issue of ``parent``; the tool never re-parents a card."""
        held = self.cards_by_number[child.number].parent
        assert held is None, f"plan#{child.number} already has parent plan#{held}"
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


@pytest.fixture(name="code")
def _code(tmp_path, monkeypatch):
    """A code repository under tmp_path with an ``origin/dev``; fetching is a no-op."""
    root = tmp_path / "code"
    root.mkdir()
    _run(root, "init", "--quiet", "--initial-branch=feat/work")
    tree = _run(root, "hash-object", "-t", "tree", "/dev/null")
    base = _run(root, "commit-tree", tree, "-m", "base")
    _run(root, "update-ref", "refs/heads/feat/work", base)
    _run(root, "update-ref", "refs/remotes/origin/dev", base)
    monkeypatch.setattr(_git, "fetch", lambda _root: None)
    monkeypatch.setattr(_git, "pushed", lambda _root, branch: branch == "feat/pushed")
    monkeypatch.setattr(_git, "origin_repository", lambda _root: "o/code")
    return root


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


# -- next ----------------------------------------------------------------------------

def test_next_names_the_first_workable_step_and_what_the_order_cannot_place(code, capsys):
    """Board order; a step not on the board and a stale claim are named, never skipped."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2)
    tracker.add(3, on_board=False)
    tracker.add(4)
    tracker.held[1] = Claim(1, "feat/gone", "2026-09-01T00:00:00Z", "s1")
    ship(code, "Ships: plan#2")
    assert run(tracker, code, "next") == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "next: plan#4 [step, balance] card 4"
    assert "NOT ON THE BOARD, so in no order: plan#3" in out
    assert "STALE CLAIM: plan#1 by 'feat/gone'" in out
    assert not tracker.writes


def test_next_reads_a_closed_blocker_and_a_containers_leaves(code, capsys):
    """A blocker a person closed is dropped; one the tool closed counts only if git shipped it."""
    tracker = FakeTracker()
    tracker.add(5, is_open=False, state_reason="COMPLETED", closed_by_tool=True)
    tracker.add(6, is_open=False, state_reason="COMPLETED", touched_by_hand=True)
    tracker.add(1, blocked_by=(5,))
    tracker.add(2, blocked_by=(6,))
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out.startswith("next: plan#2 ")
    ship(code, "Ships: plan#5")
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out.startswith("next: plan#1 ")


# -- claim, release ------------------------------------------------------------------

def test_claim_names_the_branch_checked_out_and_refuses_a_held_card(code, capsys):
    """Exactly one session wins: the second claim is refused with the holder named."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, "claim", "plan#1") == 0
    assert tracker.held[1].branch == "feat/work"
    assert run(tracker, code, "claim", "1", "--branch", "feat/other") == 1
    assert "already claimed by 'feat/work'" in capsys.readouterr().err


@pytest.mark.parametrize("fields", [
    {"kind": "ruling"}, {"kind": "question"}, {"is_open": False},
    {"children": (Child(9, "step", True),)},
])
def test_claim_refuses_what_no_branch_ships(code, fields):
    """A ruling, a question, a closed card and a container are not built by a branch."""
    tracker = FakeTracker()
    tracker.add(1, **fields)
    assert run(tracker, code, "claim", "plan#1") == 1
    assert not tracker.writes


def test_claim_refuses_a_shared_branch(code, capsys):
    """A claim names the branch that will ship the card, never dev or main."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, "claim", "plan#1", "--branch", "dev") == 1
    assert "not 'dev'" in capsys.readouterr().err


def test_release_deletes_only_the_claim_of_the_branch_named(code, capsys):
    """Releasing another branch's claim takes naming that branch."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.held[1] = Claim(1, "feat/other", "2026-10-04T00:00:00Z", "s")
    assert run(tracker, code, "release", "plan#1") == 1
    assert "pass --branch feat/other" in capsys.readouterr().err
    assert run(tracker, code, "release", "plan#1", "--branch", "feat/other") == 0
    assert tracker.writes == [("release", 1)]


# -- file ------------------------------------------------------------------------------

def test_a_finding_that_fails_the_check_writes_nothing(code, capsys):
    """The check runs before anything is sent."""
    tracker = FakeTracker()
    tracker.add(1)
    status = run(tracker, code, "file", "finding", "--arc", "balance", "--title", "t",
                 "--owner", "plan#1", "--text", "Two sentences. Not one.")
    assert status == 1
    assert "ONE sentence" in capsys.readouterr().err
    assert not tracker.writes


def test_a_finding_is_filed_under_its_owner_and_kept_off_the_board(code):
    """R-BAL177: findings are listed on their owner's card, not in the order."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, "file", "finding", "--arc", "balance", "--title", "Twice",
                "--owner", "plan#1", "--text", "The report counts a refund twice.") == 0
    assert tracker.writes == [("create", 2, "finding", "Twice", ("balance",)),
                              ("add_child", 1, 2)]
    assert tracker.board.items == [1]


def test_a_leaf_takes_the_split_steps_place_and_the_next_leaf_follows_it(code, tmp_path):
    """R-BAL179: the split step leaves the board as a container; its leaves hold its place.
    (Each leaf has its own name: an identical filing finishes the first, R-BAL186.)"""
    spec = tmp_path / "spec.md"
    spec.write_text("Build the half.")
    tracker = FakeTracker()
    for number in (1, 2, 3):
        tracker.add(number)
    args = ("file", "step", "--arc", "balance", "--parent", "plan#2", "--body-file", str(spec))
    assert run(tracker, code, *args, "--title", "first half") == 0
    assert tracker.board.items == [1, 4, 3]
    assert run(tracker, code, *args, "--title", "second half") == 0
    assert tracker.board.items == [1, 4, 5, 3]
    assert tracker.cards_by_number[2].is_container


def test_a_new_step_and_a_question_go_to_the_bottom(code, tmp_path):
    """Nothing places them yet; the developer drags them."""
    text = tmp_path / "q.md"
    text.write_text("Which day?")
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, "file", "question", "--arc", "recurrence", "--title", "Day",
                "--body-file", str(text)) == 0
    assert run(tracker, code, "file", "step", "--arc", "salary", "--title", "New",
                "--body-file", str(text), "--label", "moves-money") == 0
    assert tracker.board.items == [1, 2, 3]
    assert tracker.cards_by_number[3].labels == ("moves-money", "salary")


def test_a_ruling_is_filed_closed_under_its_owner(code, tmp_path):
    """A ruling is a record: question and answer word for word, closed at birth."""
    question, answer = tmp_path / "q", tmp_path / "a"
    question.write_text("Where should it live?")
    answer.write_text('Picked "Here".')
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, "file", "ruling", "--arc", "balance", "--title", "Home",
                "--owner", "plan#1", "--question-file", str(question),
                "--answer-file", str(answer)) == 0
    assert tracker.bodies[2] == ruling_body("Where should it live?", 'Picked "Here".')
    assert ("close", 2, "completed") in tracker.writes
    assert tracker.board.items == [1]


def test_an_answered_question_becomes_its_ruling(code, tmp_path):
    """One card, so the question's text is never copied (rule 14)."""
    answer = tmp_path / "a"
    answer.write_text("Yes.")
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, "question", body="Ship it tonight?", title="Tonight")
    assert run(tracker, code, "file", "ruling", "--arc", "balance", "--title", "Tonight",
                "--owner", "plan#1", "--from-question", "plan#2",
                "--answer-file", str(answer)) == 0
    card = tracker.cards_by_number[2]
    assert (card.kind, card.parent, card.is_open) == ("ruling", 1, False)
    assert tracker.bodies[2] == ruling_body("Ship it tonight?", "Yes.")
    assert tracker.board.items == [1]
    assert not [w for w in tracker.writes if w[0] == "create"]


# -- block, move, drop ------------------------------------------------------------------

def test_block_and_unblock(code):
    """One edge each way; removing an edge that is not there is refused."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2)
    assert run(tracker, code, "block", "plan#1", "--by", "plan#2") == 0
    assert tracker.cards_by_number[1].blocked_by == (2,)
    assert run(tracker, code, "block", "plan#1", "--by", "plan#2", "--remove") == 0
    assert run(tracker, code, "block", "plan#1", "--by", "plan#2", "--remove") == 1


def test_move_places_steps_and_questions_only(code):
    """R-BAL177: a finding is not in the order."""
    tracker = FakeTracker()
    for number in (1, 2, 3):
        tracker.add(number)
    tracker.add(4, "finding", parent=1)
    assert run(tracker, code, "move", "plan#3", "--top") == 0
    assert tracker.board.items == [3, 1, 2]
    assert run(tracker, code, "move", "plan#3", "--after", "plan#1") == 0
    assert tracker.board.items == [1, 3, 2]
    assert run(tracker, code, "move", "plan#1", "--bottom") == 0
    assert tracker.board.items == [3, 2, 1]
    assert run(tracker, code, "move", "plan#4", "--top") == 1


def test_drop_says_why_and_closes_as_not_planned(code):
    """No trailer, no code commit: a comment and a close."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, "drop", "plan#1", "--why", "superseded by plan#2") == 0
    assert tracker.writes == [("comment", 1, "Dropped: superseded by plan#2"),
                              ("close", 1, "not_planned")]


# -- sync ----------------------------------------------------------------------------------

def test_sync_closes_and_releases_what_its_claimed_branch_shipped(code, capsys):
    """And reports, without closing, a card shipped from a branch its claim does not name."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2)
    tracker.held[1] = Claim(1, "feat/a", "2026-10-04T00:00:00Z", "s1")
    tracker.held[2] = Claim(2, "feat/b", "2026-10-04T00:00:00Z", "s2")
    sha = ship(code, "Ships: plan#1", "Ships: plan#2")
    tracker.pulls[sha] = {"feat/a"}
    assert run(tracker, code, "sync") == 1
    assert ("close", 1, "completed") in tracker.writes and ("release", 1) in tracker.writes
    assert not [w for w in tracker.writes if w[1] == 2]
    assert "plan#2 shipped in git from ['feat/a'], but its claim names 'feat/b'" in (
        capsys.readouterr().out)


def test_sync_dry_run_writes_nothing(code, capsys):
    """``--dry-run`` says what it would do."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.held[1] = Claim(1, "feat/a", "2026-10-04T00:00:00Z", "s1")
    tracker.pulls[ship(code, "Ships: plan#1")] = {"feat/a"}
    assert run(tracker, code, "sync", "--dry-run") == 0
    assert not tracker.writes
    assert "would close plan#1" in capsys.readouterr().out


def test_sync_reopens_what_a_reopens_commit_takes_back(code):
    """The tool's own COMPLETED close follows git."""
    tracker = FakeTracker()
    tracker.add(1, is_open=False, state_reason="COMPLETED", closed_by_tool=True)
    ship(code, "Ships: plan#1")
    ship(code, "Reopens: plan#1")
    assert run(tracker, code, "sync") == 0
    assert tracker.writes == [("reopen", 1)]


def test_sync_prints_a_trailer_naming_no_card_and_a_malformed_one_as_history(code, capsys):
    """BAL-544's class: a number nobody filed is reported, never silently passed -- and, as
    history no tracker write can change, it never fails sync (R-BAL184)."""
    tracker = FakeTracker()
    ship(code, "Ships: plan#77")
    assert run(tracker, code, "sync") == 0
    assert ("HISTORY: a trailer on origin/dev names plan#77, which does not exist"
            in capsys.readouterr().out)
    ship(code, "Ships: plan #8")
    assert run(tracker, code, "sync") == 0
    out = capsys.readouterr().out
    assert "HISTORY: " in out and "'plan #8' is not plan#<number>" in out
    assert "REPORT" not in out


# -- spec-history, spec-revert ----------------------------------------------------------------

def _versions(tracker):
    """Card 1 filed 10-01 by the tool, edited 10-02 by the tool and 10-03 by the developer."""
    tracker.add(1, body="v3")
    tracker.versions[1] = [
        Edit("E1", "2026-10-01T00:00:00Z", "shekel-plan-tool", "v1\n"),
        Edit("E2", "2026-10-02T00:00:00Z", "shekel-plan-tool", "v2\n"),
        Edit("E3", "2026-10-03T00:00:00Z", "SaltyReformed", "v3"),
    ]


def test_spec_history_shows_each_change_in_the_window_against_the_version_before(code, capsys):
    """R-BAL174: a review grades every spec change since its branch started."""
    tracker = FakeTracker()
    _versions(tracker)
    assert run(tracker, code, "spec-history", "plan#1", "--since", "2026-10-02") == 0
    out = capsys.readouterr().out
    assert "edit E2, 2026-10-02T00:00:00Z by shekel-plan-tool" in out
    assert "-v1" in out and "+v2" in out
    assert "edit E3" in out and "by SaltyReformed" in out
    assert "filed" not in out


def test_spec_history_since_a_git_ref_reads_its_commit_date(code, capsys):
    """``--since <ref>``: the branch's start, as a date."""
    tracker = FakeTracker()
    _versions(tracker)
    assert run(tracker, code, "spec-history", "plan#1", "--since", "feat/work") == 0
    assert "has not changed since feat/work" in capsys.readouterr().out


def test_spec_revert_restores_a_version_and_refuses_a_change_since_its_read(code, capsys,
                                                                            monkeypatch):
    """By hand: the version named, and only if nobody saved the card between the read of
    its history and the write (the body is read again just before the write: review M3)."""
    tracker = FakeTracker()
    _versions(tracker)
    assert run(tracker, code, "spec-revert", "plan#1", "--to", "E2") == 0
    assert tracker.bodies[1] == "v2\n"
    read = tracker.edits

    def saved_meanwhile(number):
        """The history as read; then someone saves the card."""
        answer = read(number)
        tracker.bodies[number] = "changed meanwhile"
        return answer

    monkeypatch.setattr(tracker, "edits", saved_meanwhile)
    assert run(tracker, code, "spec-revert", "plan#1", "--to", "E1") == 1
    assert "changed while its history was read" in capsys.readouterr().err
    assert tracker.bodies[1] == "changed meanwhile"


# -- show, and the exit statuses ----------------------------------------------------------------

def test_show_resolves_an_old_id_by_its_title_alias(code, capsys):
    """``R-BAL80`` finds the card titled ``[R-BAL80] ...``; two arcs' same id need the arc."""
    tracker = FakeTracker()
    tracker.add(1, "ruling", title="[R-GU] one", labels=("balance",), is_open=False)
    tracker.add(2, "ruling", title="[R-GU] two", labels=("bank_import",), is_open=False)
    tracker.add(3, "ruling", title="[R-GUX] other", labels=("balance",), is_open=False)
    assert run(tracker, code, "show", "R-GU") == 1
    assert "names 2 cards" in capsys.readouterr().err
    assert run(tracker, code, "show", "bank_import:R-GU") == 0
    assert capsys.readouterr().out.startswith("plan#2 [ruling, bank_import] [R-GU] two")


def test_a_github_failure_exits_2(code, capsys, monkeypatch):
    """Refused is 1; a failed call is 2, with its message."""
    tracker = FakeTracker()

    def broken(_numbers):
        raise _git.GitError("boom")

    monkeypatch.setattr(tracker, "cards", broken)
    assert run(tracker, code, "drop", "plan#1", "--why", "x") == 2
    assert "failed: boom" in capsys.readouterr().err


# -- the review of checkpoint 2 ------------------------------------------------------------------

class _FailOnce:
    """One tracker write that fails the first time, as a timeout or a 502 would."""

    def __init__(self, tracker, name):
        """Stand in for ``tracker.<name>``."""
        self.real = getattr(tracker, name)
        self.failed = False
        setattr(tracker, name, self)

    def __call__(self, *args):
        """Fail once, then write."""
        if not self.failed:
            self.failed = True
            raise GitHubError(502, "bad gateway")
        return self.real(*args)


def test_a_step_waiting_on_a_question_is_released_once_it_is_answered(code, tmp_path, capsys):
    """Review H1: the tool closes a ruling at birth and git never ships one, so a closed
    ruling is resolved; the step waiting on the question stayed blocked forever."""
    answer = tmp_path / "a"
    answer.write_text("Tuesday.")
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, "question", body="Which day?", title="Day")
    tracker.add(3, blocked_by=(2,))
    tracker.held[1] = Claim(1, "feat/one", "2026-10-04T12:00:00Z", "s")
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out.startswith("next: nothing")
    assert run(tracker, code, "file", "ruling", "--arc", "balance", "--title", "Day",
               "--owner", "plan#1", "--from-question", "plan#2",
               "--answer-file", str(answer)) == 0
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out.startswith("next: plan#3 ")


def test_a_ships_naming_a_container_closes_nothing_and_unblocks_nothing(code, capsys):
    """Review H2: a mistyped number closed the container with no claim and unblocked the
    step waiting on it while its leaf was open."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", True),), on_board=False)
    tracker.add(2, parent=1)
    tracker.add(3, blocked_by=(1,))
    tracker.held[2] = Claim(2, "feat/two", "2026-10-04T12:00:00Z", "s")
    ship(code, "Ships: plan#1")
    assert run(tracker, code, "sync") == 0
    assert not tracker.writes
    assert "HISTORY: a Ships trailer names plan#1, a container" in capsys.readouterr().out
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out.startswith("next: nothing")


def test_a_leaf_is_never_filed_under_a_step_already_done_with(code, tmp_path, capsys):
    """Review H2: every decision would ignore a leaf under a shipped step."""
    spec = tmp_path / "spec.md"
    spec.write_text("Late work.")
    tracker = FakeTracker()
    tracker.add(1)
    ship(code, "Ships: plan#1")
    assert run(tracker, code, "file", "step", "--arc", "balance", "--title", "late",
               "--parent", "plan#1", "--body-file", str(spec)) == 1
    assert "the LIVE step it splits" in capsys.readouterr().err
    assert not tracker.writes


def test_splitting_a_blocked_step_offers_no_leaf_until_its_wait_ends(code, tmp_path, capsys):
    """R-BAL182 (review H3): the wait stays recorded on the split step and holds its leaves."""
    spec = tmp_path / "spec.md"
    spec.write_text("The first half.")
    tracker = FakeTracker()
    tracker.add(2)
    tracker.held[2] = Claim(2, "feat/l2", "2026-10-04T12:00:00Z", "s")
    tracker.add(7, blocked_by=(2,))
    assert run(tracker, code, "file", "step", "--arc", "balance", "--title", "L7a",
               "--parent", "plan#7", "--body-file", str(spec)) == 0
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out.startswith("next: nothing")
    ship(code, "Ships: plan#2")
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out.startswith("next: plan#8 ")


def test_dropping_a_split_step_takes_its_leaves_out_of_the_order(code, capsys):
    """R-BAL185 (review L2): the drop is recorded once, on the split step; sync lists the
    leaves left open under it."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", True),), on_board=False)
    tracker.add(2, parent=1)
    assert run(tracker, code, "drop", "plan#1", "--why", "superseded") == 0
    assert "its open leaves plan#2 are no longer offered" in capsys.readouterr().out
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out.startswith("next: nothing")
    assert run(tracker, code, "sync") == 1
    assert "REPORT: plan#2 is open under plan#1, which was dropped" in capsys.readouterr().out


@pytest.mark.parametrize("failing", ["retype", "add_child", "close"])
def test_a_conversion_cut_short_is_finished_by_the_same_command(code, tmp_path, capsys,
                                                                failing):
    """R-BAL186 (review M1): each write is printed as it lands, and the retry finishes the
    card; it wrapped the question twice."""
    answer = tmp_path / "a"
    answer.write_text("Yes.")
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, "question", body="Ship it tonight?", title="Tonight")
    _FailOnce(tracker, failing)
    args = ("file", "ruling", "--arc", "balance", "--title", "Tonight", "--owner", "plan#1",
            "--from-question", "plan#2", "--answer-file", str(answer))
    assert run(tracker, code, *args) == 2
    assert "plan#2's body: the question, then the answer" in capsys.readouterr().out
    assert run(tracker, code, *args) == 0
    card = tracker.cards_by_number[2]
    assert (card.kind, card.parent, card.is_open) == ("ruling", 1, False)
    assert tracker.bodies[2] == ruling_body("Ship it tonight?", "Yes.")
    assert len(tracker.versions[2]) == 2, "the body was written once"
    assert tracker.board.items == [1]


@pytest.mark.parametrize("failing", ["add_child", "close"])
def test_a_ruling_filing_cut_short_is_finished_not_filed_twice(code, tmp_path, failing):
    """R-BAL186 (review M1): the retry filed a second ruling."""
    question, answer = tmp_path / "q", tmp_path / "a"
    question.write_text("Where?")
    answer.write_text("Here.")
    tracker = FakeTracker()
    tracker.add(1)
    _FailOnce(tracker, failing)
    args = ("file", "ruling", "--arc", "balance", "--title", "Home", "--owner", "plan#1",
            "--question-file", str(question), "--answer-file", str(answer))
    assert run(tracker, code, *args) == 2
    assert run(tracker, code, *args) == 0
    rulings = [card for card in tracker.cards_by_number.values() if card.kind == "ruling"]
    assert [(card.number, card.parent, card.is_open) for card in rulings] == [(2, 1, False)]


def test_the_same_finding_filed_twice_is_one_card(code, capsys):
    """R-BAL186: an open card of the same kind, title and text is that card; the second run
    writes nothing and says so."""
    tracker = FakeTracker()
    tracker.add(1)
    args = ("file", "finding", "--arc", "balance", "--title", "Twice", "--owner", "plan#1",
            "--text", "The report counts a refund twice.")
    assert run(tracker, code, *args) == 0
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, *args) == 0
    assert tracker.writes == writes
    out = capsys.readouterr().out
    assert out.startswith("finishing plan#2 ") and "already a sub-issue of plan#1" in out


def test_a_matching_card_under_another_owner_or_with_other_labels_is_refused_unwritten(
        code, tmp_path, capsys):
    """R-BAL186 finishes only the filing it repeats: a card re-homed or relabelled since is
    a person's to fix, and nothing is written."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(3)
    tracker.add(2, "finding", body="The report counts a refund twice.", title="Twice", parent=3)
    assert run(tracker, code, "file", "finding", "--arc", "balance", "--title", "Twice",
               "--owner", "plan#1", "--text", "The report counts a refund twice.") == 1
    assert "a sub-issue of plan#3, not plan#1" in capsys.readouterr().err
    spec = tmp_path / "spec.md"
    spec.write_text("New work.")
    tracker.add(4, body="New work.", title="New", labels=("salary",))
    assert run(tracker, code, "file", "step", "--arc", "salary", "--title", "New",
               "--body-file", str(spec), "--label", "moves-money") == 1
    assert "relabel it by hand" in capsys.readouterr().err
    assert not tracker.writes


def test_a_top_level_card_is_added_at_the_bottom_with_one_write(code, tmp_path):
    """Review L7: GitHub adds an item at the bottom; placing it after a lagging read's last
    card could put it above the cards that read missed."""
    text = tmp_path / "q.md"
    text.write_text("Which day?")
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, "file", "question", "--arc", "recurrence", "--title", "Day",
               "--body-file", str(text)) == 0
    assert tracker.writes == [("create", 2, "question", "Day", ("recurrence",)),
                              ("board_add", 2)]


def test_a_label_named_twice_files_the_card_once(code, tmp_path):
    """Review L8: GitHub keeps one, and the read-back refused the card it had just filed."""
    text = tmp_path / "spec.md"
    text.write_text("New work.")
    tracker = FakeTracker()
    assert run(tracker, code, "file", "step", "--arc", "salary", "--title", "New",
               "--body-file", str(text), "--label", "moves-money", "--label", "moves-money") == 0
    assert tracker.writes[0] == ("create", 1, "step", "New", ("moves-money", "salary"))


def test_a_network_error_or_missing_credentials_exit_2_not_a_traceback(code, capsys,
                                                                        monkeypatch):
    """Review M5: both escaped ``main`` as a traceback with exit 1, which means refused."""
    tracker = FakeTracker()
    tracker.add(1)

    def unreachable(_numbers):
        raise requests.ConnectionError("connection reset")

    monkeypatch.setattr(tracker, "cards", unreachable)
    assert run(tracker, code, "drop", "plan#1", "--why", "x") == 2
    assert "failed: connection reset" in capsys.readouterr().err

    def no_credentials():
        raise FileNotFoundError(2, "No such file or directory",
                                "/home/x/.config/shekel-plan/app.json")

    assert plan.main(["next"], connect=no_credentials, root=code) == 2
    assert "app.json" in capsys.readouterr().err


def test_a_claim_whose_branch_cannot_be_read_is_released_by_saying_so(code, capsys):
    """Review L1: next printed ``--branch None``, and no release could match it."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2)
    tracker.held[1] = Claim(1, None, "", "s1")
    tracker.held[2] = Claim(2, "feat/x", "2026-10-04T11:00:00Z", "s2")
    assert run(tracker, code, "next") == 0
    out = capsys.readouterr().out
    assert "`plan release plan#1 --unreadable`" in out and "--branch None" not in out
    assert run(tracker, code, "release", "plan#1", "--branch", "feat/work") == 1
    assert "pass --unreadable" in capsys.readouterr().err
    assert run(tracker, code, "release", "plan#2", "--unreadable") == 1
    assert run(tracker, code, "release", "plan#1", "--unreadable") == 0
    assert tracker.writes == [("release", 1)]


def test_sync_and_show_print_a_stray_reopens_and_act_on_neither(code, capsys):
    """R-BAL181 and R-BAL184 (review M2: both prints survived their deletion)."""
    tracker = FakeTracker()
    tracker.add(1)
    ship(code, "Reopens: plan#1")
    assert run(tracker, code, "sync") == 0
    out = capsys.readouterr().out
    assert "HISTORY: " in out and "'Reopens: plan#1' cancels no Ships" in out
    assert run(tracker, code, "show", "plan#1") == 0
    out = capsys.readouterr().out
    assert "STRAY: " in out and "'Reopens: plan#1' cancels no Ships" in out
    assert not tracker.writes


def test_a_finding_owner_open_on_the_tracker_but_shipped_in_git_is_refused(code, capsys):
    """Review M2: a live owner is git's answer, never the card's open state (display)."""
    tracker = FakeTracker()
    tracker.add(1)
    ship(code, "Ships: plan#1")
    assert run(tracker, code, "file", "finding", "--arc", "balance", "--title", "Late",
               "--owner", "plan#1", "--text", "The report counts a refund twice.") == 1
    assert "a step that is done" in capsys.readouterr().err
    assert not tracker.writes


def test_a_question_becomes_a_ruling_only_in_its_own_arc(code, tmp_path, capsys):
    """Review M2: the arc refusal survived its deletion."""
    answer = tmp_path / "a"
    answer.write_text("Yes.")
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, "question", body="Ship it?", title="Ship", labels=("salary",))
    assert run(tracker, code, "file", "ruling", "--arc", "balance", "--title", "Ship",
               "--owner", "plan#1", "--from-question", "plan#2",
               "--answer-file", str(answer)) == 1
    assert "not in the balance arc" in capsys.readouterr().err
    assert not tracker.writes


def test_a_placement_the_board_has_not_shown_yet_is_said(code, tmp_path, capsys):
    """Review M3: the fake showed every placement at once, so this was never run."""
    spec = tmp_path / "spec.md"
    spec.write_text("Half.")
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2)
    tracker.board.lagging = True
    assert run(tracker, code, "move", "plan#2", "--top") == 0
    assert "the board has not shown it yet" in capsys.readouterr().out
    assert run(tracker, code, "file", "step", "--arc", "balance", "--title", "half",
               "--parent", "plan#1", "--body-file", str(spec)) == 0
    assert "(the board has not shown it yet)" in capsys.readouterr().out


def test_two_open_cards_with_the_same_kind_title_and_text_are_refused(code, capsys):
    """R-BAL186 treats them as one card, so it cannot pick between two: a person drops one."""
    tracker = FakeTracker()
    tracker.add(1)
    for number in (2, 3):
        tracker.add(number, "finding", body="The report counts a refund twice.", title="Twice",
                    on_board=False)
    assert run(tracker, code, "file", "finding", "--arc", "balance", "--title", "Twice",
               "--owner", "plan#1", "--text", "The report counts a refund twice.") == 1
    assert "2 open cards have this kind, title and text (plan#2, plan#3)" in (
        capsys.readouterr().err)
    assert not tracker.writes


def test_a_question_already_under_another_step_is_refused_before_any_write(code, tmp_path,
                                                                             capsys):
    """Review M1: the conversion never read the question's parent, so its first writes
    landed before the sub-issue call could fail."""
    answer = tmp_path / "a"
    answer.write_text("Yes.")
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(3)
    tracker.add(2, "question", body="Ship it?", title="Ship", parent=3)
    assert run(tracker, code, "file", "ruling", "--arc", "balance", "--title", "Ship",
               "--owner", "plan#1", "--from-question", "plan#2",
               "--answer-file", str(answer)) == 1
    assert "a sub-issue of plan#3, not plan#1" in capsys.readouterr().err
    assert not tracker.writes
