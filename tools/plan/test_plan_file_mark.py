"""``plan file`` under ruling ``balance:R-BAL202``: every card it creates is born marked
``filing``, the mark comes off with its filing's last write, and a card still marked is
never offered, is named by ``next`` and ``sync``, and is finished by the same command --
for a leaf, its move made again -- over :class:`_fake.FakeTracker` and a throwaway git
repository (the ``code`` fixture, ``conftest.py``).  Nothing here calls GitHub.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from _fake import FailOnce, FakeTracker, ReadBackFailsOnce, run
from _tracker import Child
from setup_tracker import FILING


def _leaf(tmp_path, title, parent="plan#1"):
    """``plan file step`` filing a leaf named ``title`` under ``parent``."""
    spec = tmp_path / f"{title}.md"
    spec.write_text(f"Build {title}.")
    return ("file", "step", "--parent", parent, "--arc", "balance", "--title", title,
            "--body-file", str(spec))


def _ruling(tmp_path, owner):
    """``plan file ruling`` filing the ruling "Home" under ``owner``, its question and answer
    written to files."""
    question, answer = tmp_path / "q", tmp_path / "a"
    question.write_text("Where?")
    answer.write_text("Here.")
    return ("file", "ruling", "--arc", "balance", "--title", "Home", "--owner", owner,
            "--question-file", str(question), "--answer-file", str(answer))


def test_a_leaf_and_a_ruling_are_created_marked_and_unmarked_by_their_last_write(code,
                                                                                tmp_path):
    """R-BAL202: a leaf is created marked, linked, added, moved, its split step taken off the
    board, then unmarked; a ruling is created marked, linked, closed, then unmarked.  (A
    finding's, a question's and a top-level step's writes are pinned in
    ``test_plan_file.py``.)"""
    tracker = FakeTracker()
    for number in (1, 2, 3):
        tracker.add(number)
    assert run(tracker, code, *_leaf(tmp_path, "half", "plan#2")) == 0
    assert tracker.writes == [("create", 4, "step", "half", ("balance", FILING)),
                              ("add_child", 2, 4), ("board_add", 4), ("board_place", 4, "PVTI_2"),
                              ("board_remove", 2), ("unmark", 4)]
    tracker.writes.clear()
    assert run(tracker, code, *_ruling(tmp_path, "plan#4")) == 0
    assert tracker.writes == [("create", 5, "ruling", "Home", ("balance", FILING)),
                              ("add_child", 4, 5), ("close", 5, "completed"), ("unmark", 5)]


@pytest.mark.parametrize("failure", ["link", "add", "move", "read-back", "remove", "unmark"])
def test_a_leaf_cut_short_after_its_create_is_never_offered_and_the_same_command_finishes_it(
        code, tmp_path, capsys, failure):
    """Review cp5 MEDIUM-1, R-BAL202: plan#1 waits on question plan#9.  Its leaf's filing
    stops at each write after the create in turn (the read-back after the create is
    ``test_plan_file.py``'s); until the same command runs again, ``plan next`` names the
    leaf as an unfinished filing and never offers it, and ``plan claim`` refuses it.  Under
    R-BAL201's order (link last), a stop at the move, its read-back or the link left the
    leaf a top-level step, offered and claimed past plan#1's wait."""
    tracker = FakeTracker()
    tracker.add(9, "question")
    tracker.add(1, blocked_by=(9,))
    if failure == "link":
        FailOnce(tracker, "add_child")
    elif failure == "move":
        FailOnce(tracker.board, "place")
    elif failure == "read-back":
        ReadBackFailsOnce(tracker.board)
    elif failure == "unmark":
        FailOnce(tracker, "unmark")
    else:
        FailOnce(tracker.board, failure)
    args = _leaf(tmp_path, "Trailer check")
    assert run(tracker, code, *args) == 2
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    out = capsys.readouterr().out
    assert out.startswith("next: nothing\n")
    assert "UNFINISHED FILING: plan#10's filing has not finished" in out
    assert run(tracker, code, "claim", "plan#10", "--branch", "feat/x") == 1
    assert "its filing has not finished (R-BAL202)" in capsys.readouterr().err
    assert run(tracker, code, *args) == 0
    card = tracker.cards_by_number[10]
    assert (card.parent, card.labels, tracker.board.items) == (1, ("balance",), [9, 10])
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out == "next: nothing\n"


def test_dropping_the_split_step_reaches_a_marked_leaf_linked_under_it(code, tmp_path, capsys):
    """Review cp5 MEDIUM-1, R-BAL202: the leaf is linked by its second write, so a drop of the
    split step drops it too, and a dropped card's unfinished filing is moot."""
    tracker = FakeTracker()
    tracker.add(1)
    FailOnce(tracker.board, "place")
    assert run(tracker, code, *_leaf(tmp_path, "Trailer check")) == 2
    assert run(tracker, code, "drop", "plan#1", "--why", "replanned") == 0
    assert not tracker.cards_by_number[2].is_open
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out == "next: nothing\n"


def test_a_leaf_cut_short_before_its_link_is_reported_after_its_split_step_is_dropped(
        code, tmp_path, capsys):
    """Review cp5 MEDIUM-1, R-BAL202: the link failed, so the drop of the split step cannot
    reach the leaf, and the same command is refused (its parent is not live); the leaf is
    never offered, and ``next`` and ``sync`` name it until ``plan drop`` drops it -- under
    R-BAL201's order it was offered as work, and sync said nothing."""
    tracker = FakeTracker()
    tracker.add(1)
    FailOnce(tracker, "add_child")
    args = _leaf(tmp_path, "Trailer check")
    assert run(tracker, code, *args) == 2
    assert run(tracker, code, "drop", "plan#1", "--why", "replanned") == 0
    capsys.readouterr()
    assert run(tracker, code, *args) == 1
    assert "a step's parent is the LIVE step it splits" in capsys.readouterr().err
    assert run(tracker, code, "next") == 0
    assert ("UNFINISHED FILING: plan#2's filing has not finished, so it is never offered "
            "(R-BAL202): unless a `plan file` command is filing it now, run that command again "
            "to finish it, or `plan drop` it\n") in capsys.readouterr().out
    assert run(tracker, code, "sync", "--dry-run") == 1
    assert "REPORT: plan#2's filing has not finished" in capsys.readouterr().out
    assert run(tracker, code, "drop", "plan#2", "--why", "replanned") == 0
    assert run(tracker, code, "sync", "--dry-run") == 0


def _split(tracker):
    """plan#1, split into plan#2 and plan#3 and off the board; plan#4 below its leaves."""
    tracker.add(1, children=(Child(2, "step", True), Child(3, "step", True)), on_board=False)
    tracker.add(2, parent=1)
    tracker.add(3, parent=1)
    tracker.add(4)


def test_a_card_added_below_a_leaf_cut_short_leaves_its_move_to_the_same_command(code,
                                                                                tmp_path):
    """Review cp4e M1 (a), R-BAL202: whatever joined the board below the leaf, the re-run
    moves it after the leaves filed before it."""
    tracker = FakeTracker()
    _split(tracker)
    FailOnce(tracker.board, "place")
    args = _leaf(tmp_path, "Trailer check")
    assert run(tracker, code, *args) == 2
    tracker.add(6)
    assert tracker.board.items == [2, 3, 4, 5, 6]
    assert run(tracker, code, *args) == 0
    assert tracker.board.items == [2, 3, 5, 4, 6]


def test_no_leaf_is_filed_while_another_leaf_of_its_split_step_is_unfinished(code, tmp_path,
                                                                            capsys):
    """Review cp4e M1 (b), R-BAL204: plan#5's move failed, so it may sit anywhere; plan#6 is
    refused, naming it, with nothing written, until plan#5's own command finishes it --
    then the board holds the leaves in their filing order."""
    tracker = FakeTracker()
    _split(tracker)
    FailOnce(tracker.board, "place")
    first = _leaf(tmp_path, "First")
    assert run(tracker, code, *first) == 2
    writes = list(tracker.writes)
    capsys.readouterr()
    second = _leaf(tmp_path, "Second")
    assert run(tracker, code, *second) == 1
    assert ("refused: the filing of plan#5, of plan#1's leaves, has not finished (R-BAL204): "
            "finish it first") in capsys.readouterr().err
    assert tracker.writes == writes
    assert run(tracker, code, *first) == 0
    assert run(tracker, code, *second) == 0
    assert tracker.board.items == [2, 3, 5, 6, 4]


def test_a_first_leaf_whose_link_failed_goes_above_the_leaf_that_took_its_place(code,
                                                                                tmp_path):
    """Review cp4e M1 (c), R-BAL204 (its second answer: the rule placing a leaf just above
    the leaves filed after it is kept): plan#5's link failed, so it was no leaf of plan#1
    and plan#6 was not refused; plan#6 took plan#1's place and took plan#1 off the board;
    plan#5's re-run links it, adds it, and puts it just above plan#6."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(4)
    FailOnce(tracker, "add_child")
    first = _leaf(tmp_path, "First")
    assert run(tracker, code, *first) == 2
    assert run(tracker, code, *_leaf(tmp_path, "Second")) == 0
    assert tracker.board.items == [6, 4]
    assert run(tracker, code, *first) == 0
    assert tracker.board.items == [5, 6, 4]
    assert [child.number for child in tracker.cards_by_number[1].children] == [6, 5]


def test_a_leaf_whose_move_into_its_split_steps_place_failed_is_finished_first(code,
                                                                              tmp_path):
    """Review cp4e M1 (c), R-BAL204: plan#5's move into plan#1's place failed, so plan#1 is
    still on the board and plan#6 is refused until plan#5's re-run puts plan#5 in plan#1's
    place; plan#6 then follows it."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(4)
    FailOnce(tracker.board, "place")
    first = _leaf(tmp_path, "First")
    second = _leaf(tmp_path, "Second")
    assert run(tracker, code, *first) == 2
    assert tracker.board.items == [1, 4, 5]
    assert run(tracker, code, *second) == 1
    assert run(tracker, code, *first) == 0
    assert run(tracker, code, *second) == 0
    assert tracker.board.items == [5, 6, 4]


def test_a_placed_leaf_whose_unmark_failed_holds_the_next_leaf_until_it_is_finished(
        code, tmp_path, capsys, monkeypatch):
    """Review rbal202a M3, R-BAL204: plan#9 is in its right place with only its unmark
    missing; plan#10 is refused -- the mark read by plan#9's card number, so a listing of
    marked cards that has not caught up changes nothing -- and once plan#9 is finished
    plan#10 follows it: [7, 9, 10, 8], where the leaf-A build gave [7, 8, 9, 10]."""
    tracker = FakeTracker()
    for number in (7, 1, 8):
        tracker.add(number)
    FailOnce(tracker, "unmark")
    first = _leaf(tmp_path, "First")
    second = _leaf(tmp_path, "Second")
    assert run(tracker, code, *first) == 2
    assert tracker.board.items == [7, 9, 8]
    real_marked = tracker.marked
    monkeypatch.setattr(tracker, "marked", dict)
    capsys.readouterr()
    assert run(tracker, code, *second) == 1
    assert "the filing of plan#9, of plan#1's leaves, has not finished" in capsys.readouterr().err
    monkeypatch.setattr(tracker, "marked", real_marked)
    assert run(tracker, code, *first) == 0
    assert run(tracker, code, *second) == 0
    assert tracker.board.items == [7, 9, 10, 8]


def test_a_step_filed_at_the_top_level_is_never_refiled_as_a_leaf(code, tmp_path, capsys):
    """Review cp4e M1 (d), cp5 LOW 6, R-BAL202: its filing finished at the top level, so the
    same title and text filed with a parent is a re-homing, a person's call: refused with
    nothing written, where b5213af4d linked it and left it below the leaves."""
    tracker = FakeTracker()
    _split(tracker)
    tracker.add(5, body="Build Trailer check.", title="Trailer check")
    assert run(tracker, code, *_leaf(tmp_path, "Trailer check")) == 1
    assert ("its filing finished at the top level, not under plan#1: re-homing a card is done "
            "by hand") in capsys.readouterr().err
    assert not tracker.writes


def test_a_split_step_a_leaf_filing_left_on_the_board_takes_no_leaf_above_it(code, tmp_path):
    """Review cp5 LOW 4, R-BAL204: plan#4's filing stopped before taking plan#2 off the board;
    plan#5 is refused until plan#4's re-run takes plan#2 off, so it never lands above
    plan#4 in plan#2's place."""
    tracker = FakeTracker()
    for number in (1, 2, 3):
        tracker.add(number)
    FailOnce(tracker.board, "remove")
    first = _leaf(tmp_path, "First", "plan#2")
    second = _leaf(tmp_path, "Second", "plan#2")
    assert run(tracker, code, *first) == 2
    assert tracker.board.items == [1, 2, 4, 3]
    assert run(tracker, code, *second) == 1
    assert run(tracker, code, *first) == 0
    assert run(tracker, code, *second) == 0
    assert tracker.board.items == [1, 4, 5, 3]


def test_a_ruling_closed_but_still_marked_is_finished_by_its_unmark_alone(code, tmp_path,
                                                                          capsys):
    """R-BAL202: a ruling's filing closes it before the mark comes off, so the closed, marked
    ruling is the unfinished filing -- named by ``next`` and ``sync``, which read it from
    the marked cards, since the open ones no longer hold it -- and the re-run closes nothing
    twice."""
    tracker = FakeTracker()
    tracker.add(1)
    FailOnce(tracker, "unmark")
    args = _ruling(tmp_path, "plan#1")
    assert run(tracker, code, *args) == 2
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    assert "UNFINISHED FILING: plan#2's filing has not finished" in capsys.readouterr().out
    assert run(tracker, code, "sync", "--dry-run") == 1
    assert "REPORT: plan#2's filing has not finished" in capsys.readouterr().out
    assert run(tracker, code, *args) == 0
    assert tracker.writes == [*writes, ("unmark", 2)]
    assert "  closed already\n" in capsys.readouterr().out


def test_no_leaf_conversion_or_leaf_move_rides_on_a_filing_that_has_not_finished(code,
                                                                                tmp_path,
                                                                                capsys):
    """R-BAL202: a leaf under a step still marked would make it a split step its own
    filing's re-run puts on the board (plan#1 is marked and off the board, as after a failed
    board add); a question still marked is converted only once its filing finishes; and a
    marked leaf is not moved by hand, since its re-run moves it again.  A finding under a
    marked step, and a move of a marked top-level card, which its re-run never moves, are
    not refused."""
    tracker = FakeTracker()
    tracker.add(1, labels=("balance", FILING), on_board=False)
    tracker.add(2, "question", body="Ship it?", title="Ship", labels=("balance", FILING))
    tracker.add(3)
    tracker.add(4, children=(Child(5, "step", True),))
    tracker.add(5, parent=4, labels=("balance", FILING))
    assert run(tracker, code, *_leaf(tmp_path, "half")) == 1
    assert "plan#1 [step, balance] card 1's own filing has not finished" in (
        capsys.readouterr().err)
    answer = tmp_path / "a"
    answer.write_text("Yes.")
    assert run(tracker, code, "file", "ruling", "--arc", "balance", "--title", "Ship",
               "--owner", "plan#3", "--from-question", "plan#2",
               "--answer-file", str(answer)) == 1
    assert "Ship's filing has not finished (R-BAL202): finish it with the `plan file` " in (
        capsys.readouterr().err)
    assert run(tracker, code, "move", "plan#5", "--top") == 1
    assert "its `plan file` command places this leaf" in capsys.readouterr().err
    assert not tracker.writes
    assert run(tracker, code, "move", "plan#2", "--top") == 0
    assert run(tracker, code, "file", "finding", "--arc", "balance", "--title", "Twice",
               "--owner", "plan#1", "--text", "The report counts a refund twice.") == 0


def test_a_card_dropped_before_its_filing_finished_is_never_finished(code, tmp_path, capsys):
    """R-BAL202: a card closed while marked was dropped, and what its filing left undone is
    moot -- it is neither named as unfinished nor finished by the same command, which files
    a new card as it would after any drop."""
    text = tmp_path / "q.md"
    text.write_text("Which day?")
    tracker = FakeTracker()
    FailOnce(tracker.board, "add")
    args = ("file", "question", "--arc", "recurrence", "--title", "Day", "--body-file",
            str(text))
    assert run(tracker, code, *args) == 2
    assert run(tracker, code, "drop", "plan#1", "--why", "asked elsewhere") == 0
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out == "next: nothing\n"
    assert run(tracker, code, *args) == 0
    assert capsys.readouterr().out.startswith("filed plan#2\n")
    assert tracker.cards_by_number[1].labels == ("filing", "recurrence")


@pytest.mark.parametrize("closer", ["drop", "person"])
def test_a_ruling_withdrawn_before_its_filing_finished_is_no_unfinished_filing(code, tmp_path,
                                                                               capsys, closer):
    """Review rbal202a M2: only the tool's own close as completed is a ruling filing's close;
    a ruling dropped, or closed by a person, while marked was withdrawn, so ``sync`` stops
    naming it -- it named it on every run, and the one exit it offered re-linked the
    withdrawn ruling under its owner."""
    tracker = FakeTracker()
    tracker.add(1)
    FailOnce(tracker, "add_child")
    assert run(tracker, code, *_ruling(tmp_path, "plan#1")) == 2
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    assert "plan#2's filing has not finished" in capsys.readouterr().out
    assert run(tracker, code, "sync", "--dry-run") == 1
    reports = [line for line in capsys.readouterr().out.splitlines() if "plan#2" in line]
    assert len(reports) == 1 and "filing has not finished" in reports[0]
    if closer == "drop":
        assert run(tracker, code, "drop", "plan#2", "--why", "asked again") == 0
    else:
        tracker.cards_by_number[2] = replace(tracker.cards_by_number[2], is_open=False,
                                             state_reason="NOT_PLANNED", closed_by_tool=False,
                                             touched_by_hand=True)
    capsys.readouterr()
    assert run(tracker, code, "sync", "--dry-run") == 0
    assert "plan#2" not in capsys.readouterr().out


def test_a_leaf_still_marked_says_its_drop_counts_toward_its_split_steps(code, tmp_path,
                                                                         capsys):
    """Review rbal202a M1, R-BAL187: plan#1's first leaf was linked and its board add failed;
    the advice to drop it says that dropping a split step's last leaf still work drops the
    split step too, as any leaf's drop does."""
    tracker = FakeTracker()
    tracker.add(1)
    FailOnce(tracker.board, "add")
    args = _leaf(tmp_path, "Trailer check")
    assert run(tracker, code, *args) == 2
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    assert (", or `plan drop` it (if it is plan#1's last leaf still work, that drops plan#1 "
            "too, R-BAL187)\n") in capsys.readouterr().out
    assert run(tracker, code, "show", "plan#2") == 0
    assert "\n  filing: not finished (R-BAL202) -- never offered until its `plan file` " in (
        capsys.readouterr().out)
    assert run(tracker, code, *args) == 0
    capsys.readouterr()
    assert run(tracker, code, "show", "plan#2") == 0
    assert "filing:" not in capsys.readouterr().out
